"""键集分页(store 分页方法 + API 可选参数)与索引迁移的契约。

覆盖:
  - keyset 正确性:limit 逐页翻完与全量不重不漏,含同 ts 多行的 tie-break;
    翻页间隙插入新行不使已发出的游标窗口滑移(OFFSET 做不到的性质);
  - 过滤组合:include_archived / q(LIKE 特殊字符按字面匹配)/ status 与分页交叉;
  - 游标健壮性:坏 base64/坏 JSON/键型不符(跨端点串用)/limit<1 → ValueError,
    API 层统一 400;
  - 默认行为回归(硬约束):不带新参数时 /api/sessions、/api/runs、/api/trace
    的响应与老口径逐字段一致,且绝不出现 next_cursor 字段;
  - 索引存在性:PRAGMA index_list 断言新索引(idx_sessions_created /
    idx_trace_run_ts)与既有索引齐备,旧库缺索引时重开连接自动补齐;
    分页 SQL 的 EXPLAIN 不再出现全量临时排序。
"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from insar_agent.api.app import create_app
from insar_agent.core.db import Database
from insar_agent.core.store import Store

# ---------------- store 层:造数工具 ----------------


def seed_sessions(store: Store, n: int = 25) -> None:
    """n 个会话,created_at 每 3 个共享同一时刻(tie-break 覆盖);i%5==0 归档。"""
    with store.db.tx() as cur:
        for i in range(n):
            cur.execute(
                "INSERT INTO sessions(session_id,name,created_at,mode,archived)"
                " VALUES (?,?,?,?,?)",
                (f"s{i:02d}", f"会话{i:02d}", 1000.0 + i // 3, "expert",
                 999.0 if i % 5 == 0 else None))


def seed_runs(store: Store, session_id: str, n: int = 20) -> None:
    """n 个 run,created_at 每 4 个共享同一时刻;status 轮流 done/failed。"""
    for i in range(n):
        store.create_run(f"r{i:02d}", session_id, workspace="w",
                         intent={"i": i})
        store.set_run_status(f"r{i:02d}", "done" if i % 2 else "failed")
    with store.db.tx() as cur:
        for i in range(n):
            cur.execute("UPDATE runs SET created_at=? WHERE run_id=?",
                        (2000.0 + i // 4, f"r{i:02d}"))


def seed_trace(store: Store, run_id: str, n: int = 17) -> None:
    """n 条事件,ts 每 5 条共享同一时刻(同 ts 多行按 id 升序 tie-break)。"""
    for i in range(n):
        store.append_trace(run_id=run_id, phase=f"p{i:02d}")
    with store.db.tx() as cur:
        cur.execute("UPDATE trace SET ts=3000.0+((id-1)/5) WHERE run_id=?", (run_id,))


def drain(fetch, limit: int) -> list[dict]:
    """逐页翻到 next_cursor 为 None,断言每页不超 limit,返回拼接结果。"""
    items, cursor, pages = [], None, 0
    while True:
        page = fetch(limit, cursor)
        assert len(page["items"]) <= limit
        items += page["items"]
        pages += 1
        assert pages < 100, "翻页不收敛"
        cursor = page["next_cursor"]
        if cursor is None:
            return items


# ---------------- keyset 正确性:不重不漏(含同 ts 多行) ----------------


def test_sessions_page_drain_matches_full(store):
    seed_sessions(store)
    full = store.list_sessions_page(None, include_archived=True)["items"]
    assert len(full) == 25
    # 全量序:created_at 倒序、同 ts 内 session_id 倒序(键集分页的全序)
    keys = [(-r["created_at"], tuple(-ord(c) for c in r["session_id"])) for r in full]
    assert keys == sorted(keys)
    paged = drain(lambda lim, cur: store.list_sessions_page(
        lim, cur, include_archived=True), limit=4)
    assert [r["session_id"] for r in paged] == [r["session_id"] for r in full]
    assert len({r["session_id"] for r in paged}) == 25  # 不重
    # 与老全量方法集合一致(老方法 tie 序未定义,只比集合)
    assert ({r["session_id"] for r in paged}
            == {r["session_id"] for r in store.list_sessions(include_archived=True)})


def test_sessions_page_stable_under_insert(store):
    """翻页间隙插入新会话:已发出的游标只往更旧的方向走,旧行不重不漏,
    新行不会被吸进后续页(键集分页对 OFFSET 滑窗问题免疫的核心性质)。"""
    seed_sessions(store)
    before = [r["session_id"] for r in
              store.list_sessions_page(None, include_archived=True)["items"]]
    page1 = store.list_sessions_page(3, include_archived=True)
    got = list(page1["items"])
    store.create_session("s-new", "新会话")  # created_at=now,排序在最前
    cursor = page1["next_cursor"]
    while cursor is not None:
        page = store.list_sessions_page(3, cursor, include_archived=True)
        got += page["items"]
        cursor = page["next_cursor"]
    assert [r["session_id"] for r in got] == before  # 旧行原样,新行不串页


def test_runs_page_drain_and_tiebreak(store):
    store.create_session("sa", "sa")
    store.create_session("sb", "sb")
    seed_runs(store, "sa")
    store.create_run("rb", "sb", workspace="w")  # 会话隔离样本
    full = store.list_runs_page("sa", None)["items"]
    assert len(full) == 20 and all(r["session_id"] == "sa" for r in full)
    paged = drain(lambda lim, cur: store.list_runs_page("sa", lim, cur), limit=3)
    assert [r["run_id"] for r in paged] == [r["run_id"] for r in full]
    assert len({r["run_id"] for r in paged}) == 20
    assert {r["run_id"] for r in paged} == {r["run_id"] for r in store.list_runs("sa")}
    # 同 created_at 组内 run_id 倒序(tie-break 生效:r03>r02>r01>r00)
    assert [r["run_id"] for r in paged][-4:] == ["r03", "r02", "r01", "r00"]


def test_events_page_drain_matches_trace_of(store):
    store.create_session("sa", "sa")
    store.create_run("ra", "sa", workspace="w")
    seed_trace(store, "ra")
    store.append_trace(run_id="other", phase="x")  # 其他 run 的事件不串
    full = store.trace_of("ra")
    assert len(full) == 17
    paged = drain(lambda lim, cur: store.events_page("ra", lim, cur), limit=5)
    # ts 单调不回拨时 (ts,id) 序与 trace_of 的 id 序逐行一致
    assert [r["id"] for r in paged] == [r["id"] for r in full]
    assert all(r["run_id"] == "ra" for r in paged)


# ---------------- 过滤组合 ----------------


def test_sessions_filters_and_q_escaping(store):
    seed_sessions(store)  # 25 个里 5 个归档(i%5==0)
    assert len(store.list_sessions_page(None)["items"]) == 20
    assert len(store.list_sessions_page(None, include_archived=True)["items"]) == 25
    # q 子串过滤 + 分页交叉:名字含「1」的会话(会话10..19 + 会话01/21)
    expect = {r["session_id"] for r in store.list_sessions(include_archived=True)
              if "1" in r["name"]}
    got = drain(lambda lim, cur: store.list_sessions_page(
        lim, cur, include_archived=True, q="1"), limit=4)
    assert {r["session_id"] for r in got} == expect
    # LIKE 特殊字符按字面匹配:% _ \ 不当通配符
    store.create_session("lit-pct", "百分a%b号")
    store.create_session("lit-under", "下划a_b线")
    store.create_session("lit-plain", "普通aXb名")
    hit = store.list_sessions_page(None, q="a%b")["items"]
    assert [r["session_id"] for r in hit] == ["lit-pct"]
    hit = store.list_sessions_page(None, q="a_b")["items"]
    assert [r["session_id"] for r in hit] == ["lit-under"]


def test_runs_page_status_filter(store):
    store.create_session("sa", "sa")
    seed_runs(store, "sa")  # done/failed 各 10
    done = drain(lambda lim, cur: store.list_runs_page(
        "sa", lim, cur, status="done"), limit=3)
    assert len(done) == 10 and all(r["status"] == "done" for r in done)
    assert store.list_runs_page("sa", 5, status="planning")["items"] == []


# ---------------- 游标健壮性 ----------------


def test_bad_cursor_raises_value_error(store):
    store.create_session("sa", "sa")
    for bad in ("!!!", "not-base64===", "eyJ4Ijox",  # 坏 base64 / 非 [ts,key] JSON
                Store._encode_cursor(1.0, True)):    # 布尔键伪装
        with pytest.raises(ValueError):
            store.list_sessions_page(5, cursor=bad)
    # 跨端点串用:sessions 游标键型是 str,events 期待 int → 拒绝而非静默漏页
    ses_cursor = Store._encode_cursor(1000.0, "s01")
    with pytest.raises(ValueError):
        store.events_page("ra", 5, cursor=ses_cursor)
    with pytest.raises(ValueError):
        store.list_runs_page("sa", 5, cursor=Store._encode_cursor(1.0, 7))
    with pytest.raises(ValueError):
        store.list_sessions_page(0)  # limit<1(API 有 ge=1,store 自身也守)


# ---------------- API 层:默认回归 + 分页 + 400 ----------------


@pytest.fixture()
def api(tmp_path):
    home = tmp_path / "home"
    app = create_app(home=home)
    with TestClient(app) as client:
        client.post("/api/sessions", json={"id": "sess-a"})
        st = Store(Database(home / "insar.db"))
        seed_sessions(st, 10)
        seed_runs(st, "sess-a", 10)
        with st.db.tx() as cur:  # r09 显式定为最新 run(不依赖同 ts 平局的扫描方向)
            cur.execute("UPDATE runs SET created_at=2099.0 WHERE run_id='r09'")
        seed_trace(st, "r09", 12)
        yield {"client": client, "store": st}
        st.close()


def test_api_default_shapes_unchanged(api):
    """硬性回归:不带新参数时,响应形状与老口径一致且无 next_cursor。"""
    c, st = api["client"], api["store"]
    ses = c.get("/api/sessions").json()
    assert isinstance(ses, list)
    def _undecorate(row: dict) -> dict:
        return {k: v for k, v in row.items() if k not in ("project_name", "project_root")}
    assert [_undecorate(r) for r in ses] == st.list_sessions()  # 装饰字段外逐字段同 store
    runs = c.get("/api/runs", params={"session": "sess-a"}).json()
    assert set(runs) == {"session", "runs"} and len(runs["runs"]) == 10
    trace = c.get("/api/trace", params={"session": "sess-a"}).json()
    assert isinstance(trace, list) and len(trace) == 12
    assert [_undecorate(r) for r in
            c.get("/api/sessions", params={"include_archived": "1"}).json()] \
        == st.list_sessions(include_archived=True)


def test_api_sessions_pagination_and_q(api):
    c = api["client"]
    page1 = c.get("/api/sessions", params={"limit": 4}).json()
    assert set(page1) == {"items", "next_cursor"} and len(page1["items"]) == 4
    page2 = c.get("/api/sessions",
                  params={"limit": 100, "cursor": page1["next_cursor"]}).json()
    assert page2["next_cursor"] is None
    joined = [r["session_id"] for r in page1["items"] + page2["items"]]
    assert joined == [r["session_id"] for r in c.get("/api/sessions").json()]
    # q 不带 limit:老形状(裸数组),过滤生效,无 next_cursor 包装
    only = c.get("/api/sessions", params={"q": "会话03"}).json()
    assert isinstance(only, list) and [r["session_id"] for r in only] == ["s03"]


def test_api_runs_pagination_status_and_counts(api):
    c = api["client"]
    full = c.get("/api/runs", params={"session": "sess-a"}).json()["runs"]
    page1 = c.get("/api/runs", params={"session": "sess-a", "limit": 3}).json()
    assert set(page1) == {"session", "runs", "next_cursor"}
    assert set(page1["runs"][0]) == {"run_id", "parent_run_id", "created_at",
                                     "status", "scenario", "simulated", "steps"}
    got, cursor = list(page1["runs"]), page1["next_cursor"]
    while cursor is not None:
        page = c.get("/api/runs", params={"session": "sess-a", "limit": 3,
                                          "cursor": cursor}).json()
        got += page["runs"]
        cursor = page["next_cursor"]
    assert [r["run_id"] for r in got] == [r["run_id"] for r in full]
    done = c.get("/api/runs",
                 params={"session": "sess-a", "status": "done"}).json()
    assert "next_cursor" not in done  # 只过滤不分页:不出现 next_cursor 字段
    assert {r["status"] for r in done["runs"]} == {"done"}


def test_api_trace_pagination(api):
    c = api["client"]
    full = c.get("/api/trace", params={"session": "sess-a"}).json()
    page1 = c.get("/api/trace", params={"session": "sess-a", "limit": 5}).json()
    assert set(page1) == {"items", "next_cursor"} and len(page1["items"]) == 5
    got, cursor = list(page1["items"]), page1["next_cursor"]
    while cursor is not None:
        page = c.get("/api/trace", params={"session": "sess-a", "limit": 5,
                                           "cursor": cursor}).json()
        got += page["items"]
        cursor = page["next_cursor"]
    assert [r["id"] for r in got] == [r["id"] for r in full]
    # 无 run 的会话 + limit:分页空壳而非裸空数组
    c.post("/api/sessions", json={"id": "sess-empty"})
    empty = c.get("/api/trace", params={"session": "sess-empty", "limit": 5}).json()
    assert empty == {"items": [], "next_cursor": None}


def test_api_bad_cursor_400(api):
    c = api["client"]
    for path, params in (
            ("/api/sessions", {"limit": 5, "cursor": "@@bad@@"}),
            ("/api/runs", {"session": "sess-a", "limit": 5, "cursor": "@@bad@@"}),
            ("/api/trace", {"session": "sess-a", "limit": 5, "cursor": "@@bad@@"})):
        r = c.get(path, params=params)
        assert r.status_code == 400, f"{path} 坏 cursor 应 400,实得 {r.status_code}"
    # 跨端点串用游标(sessions 的 str 键喂给 trace 的 int 键)→ 400
    ses = c.get("/api/sessions", params={"limit": 2}).json()
    r = c.get("/api/trace", params={"session": "sess-a", "limit": 5,
                                    "cursor": ses["next_cursor"]})
    assert r.status_code == 400


# ---------------- 索引存在性与查询计划 ----------------


def _index_names(db: Database, table: str) -> set[str]:
    return {r["name"] for r in db.query(f"PRAGMA index_list('{table}')")}


def test_indexes_present(db):
    assert "idx_sessions_created" in _index_names(db, "sessions")
    assert {"idx_trace_run", "idx_trace_run_ts"} <= _index_names(db, "trace")
    # 既有索引不回归
    assert "idx_runs_session" in _index_names(db, "runs")
    assert "idx_actions_due" in _index_names(db, "pending_actions")
    assert "idx_chat_session" in _index_names(db, "chat_messages")
    assert "idx_commands_step" in _index_names(db, "commands")


def test_index_migration_backfills_old_db(tmp_path):
    """旧库缺新索引:重开连接(schema 重放 + db.py 迁移清单)自动补齐。"""
    p = tmp_path / "old.db"
    Database(p).close()
    conn = sqlite3.connect(p)
    conn.execute("DROP INDEX idx_sessions_created")
    conn.execute("DROP INDEX idx_trace_run_ts")
    conn.commit()
    conn.close()
    d = Database(p)
    assert "idx_sessions_created" in _index_names(d, "sessions")
    assert "idx_trace_run_ts" in _index_names(d, "trace")
    d.close()


def test_pagination_plans_have_no_full_sort(db):
    """分页 SQL 不再全量临时排序:sessions/trace 计划完全无 TEMP B-TREE;
    runs 允许同 ts 组内的 LAST TERM 小排序,但不允许全量 ORDER BY 物化。"""
    plans = {
        "sessions": ("SELECT * FROM sessions WHERE archived IS NULL"
                     " ORDER BY created_at DESC, session_id DESC LIMIT 5", ()),
        "trace": ("SELECT * FROM trace WHERE run_id=? ORDER BY ts, id LIMIT 5",
                  ("r0",)),
        "runs": ("SELECT * FROM runs WHERE session_id=?"
                 " ORDER BY created_at DESC, run_id DESC LIMIT 5", ("s0",)),
    }
    for name, (sql, params) in plans.items():
        detail = " | ".join(r["detail"] for r in
                            db.query(f"EXPLAIN QUERY PLAN {sql}", params))
        assert "USE TEMP B-TREE FOR ORDER BY" not in detail, f"{name}: {detail}"
        if name in ("sessions", "trace"):
            assert "USE TEMP B-TREE" not in detail, f"{name}: {detail}"
