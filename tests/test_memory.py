"""跨会话记忆(brain/memory.py + api/memory_router.py)的行为与契约测试。

覆盖(任务验收清单):
  - CRUD:add/list(默认不含 archived)/archive(软删)/search(LIKE 转义);
  - 闭集校验:kind/source 闭集、内容非空与长度上限;
  - 注入契约:get_context_snippets 空表 [] / 权重排序 / limit /
    全局+会话隔离;inspect.signature 签名守护(对话二期代理按
    get_context_snippets(store, session_id, limit=5) 消费,一字不得改);
  - 规则萃取:done run → outcome 记忆内容模板「<日期> <场景>@<区域 or 会话名>
    用 <关键方法链> 完成,证据级 <level>」;区域回退会话名;intent.region
    优先;方法链连续去重与超长截断;全跳过 run 的方法链兜底;
    非 done ValueError(API 409);未知 run KeyError(API 404);重复萃取幂等;
  - 去重加权:同 kind+content 不重插 weight+0.5(record_preference 幂等);
  - API:列表/搜索/类型过滤/手动添加校验/归档软删/萃取三态。

路由挂接:测试直接 include create_memory_router(store)(report 路由测试
同款隔离模式,不拉起整个 create_app)。
"""

from __future__ import annotations

import inspect
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from insar_agent.api.memory_router import create_memory_router
from insar_agent.audit.ladder import LADDER
from insar_agent.brain.memory import (
    CONTENT_MAX,
    MemoryStore,
    extract_from_run,
    get_context_snippets,
    record_preference,
)

_HASHES = {"task_hash": "t1", "args_hash": "a1", "local_hash": "l1", "eval_hash": "e1"}


@pytest.fixture()
def memories(db):
    return MemoryStore(db)


def make_done_run(store, *, run_id="20260810T090000-mem001", session="sess-mem",
                  name="玉树冻土排查", scenario="permafrost_sbas",
                  methods=(("acquire", "local_import"),
                           ("interferogram", "isce2_ifg"),
                           ("unwrap", "snaphu_mcf")),
                  simulated=True, intent=None, status="done",
                  step_state="done") -> str:
    """手搭一个(默认 simulated 的)run:steps 全 done/skipped,状态可控。"""
    store.create_session(session, name)
    store.create_run(run_id, session, workspace="", intent=intent or {},
                     scenario=scenario, simulated=simulated)
    for i, (cap, method) in enumerate(methods, start=1):
        if step_state == "skipped":
            store.upsert_step(run_id, i, capability=cap, name=cap, method=method,
                              params={}, hashes=_HASHES, state="skipped")
        else:
            store.upsert_step(run_id, i, capability=cap, name=cap, method=method,
                              params={}, hashes=_HASHES)
            store.advance(run_id, i, "VERIFIED", state="done", run_ok=1, exit_code=0)
    store.set_run_status(run_id, status)
    return run_id


# ---------------------------------------------------------------------------
# ① MemoryStore CRUD
# ---------------------------------------------------------------------------

def test_add_and_get_roundtrip(memories):
    mid = memories.add("fact", "  SAR 数据在 D:/SAR/yushu  ", session_id=None)
    m = memories.get(mid)
    assert m["kind"] == "fact"
    assert m["content"] == "SAR 数据在 D:/SAR/yushu"  # 首尾空白剔除
    assert m["source"] == "user" and m["session_id"] is None
    assert m["weight"] == 1.0 and m["archived"] is None
    assert abs(m["ts"] - time.time()) < 5


def test_add_validates_closed_sets(memories):
    with pytest.raises(ValueError):
        memories.add("vibe", "未知类型")
    with pytest.raises(ValueError):
        memories.add("fact", "未知来源", source="llm")
    with pytest.raises(ValueError):
        memories.add("fact", "   ")  # 空内容
    with pytest.raises(ValueError):
        memories.add("fact", "长" * (CONTENT_MAX + 1))  # 超长
    memories.add("fact", "长" * CONTENT_MAX)  # 恰好达上限:合法


def test_list_default_excludes_archived(memories):
    a = memories.add("preference", "常用区域:玉树")
    b = memories.add("fact", "机器 16GB 内存")
    assert memories.archive(a) is True
    active = memories.list()
    assert [m["id"] for m in active] == [b]
    everything = memories.list(include_archived=True)
    assert {m["id"] for m in everything} == {a, b}
    archived = next(m for m in everything if m["id"] == a)
    assert archived["archived"] is not None  # 软删:行保留,落归档时刻


def test_archive_missing_returns_false(memories):
    assert memories.archive(9999) is False


def test_list_kind_filter_and_limit(memories):
    memories.add("preference", "偏好场景:permafrost_sbas")
    memories.add("fact", "事实一")
    memories.add("fact", "事实二")
    assert {m["kind"] for m in memories.list(kind="fact")} == {"fact"}
    assert len(memories.list(kind="fact")) == 2
    assert len(memories.list(limit=2)) == 2


def test_list_orders_by_weight_then_recency(memories):
    old = memories.add("fact", "普通记忆")
    heavy = memories.add("preference", "常用区域:玉树")
    memories.add("preference", "常用区域:玉树")  # 去重加权 → 1.5
    new = memories.add("fact", "较新的记忆")
    ids = [m["id"] for m in memories.list()]
    assert ids[0] == heavy            # weight 1.5 优先
    assert ids[1:] == [new, old]      # 同权按 ts 新者优先(同秒退 id 降序)


def test_search_like_with_escaping(memories):
    hit = memories.add("fact", "解缠成功率 100% 达标")
    memories.add("fact", "a_b 形态的路径")
    memories.add("fact", "普通内容")
    assert [m["id"] for m in memories.search("100%")] == [hit]
    assert [m["content"] for m in memories.search("_")] == ["a_b 形态的路径"]
    assert memories.search("不存在") == []
    archived_id = memories.search("100%")[0]["id"]
    memories.archive(archived_id)
    assert memories.search("100%") == []  # 默认不含已归档
    assert [m["id"] for m in memories.search("100%", include_archived=True)] == [hit]


def test_dedup_same_content_bumps_weight(memories):
    a = memories.add("preference", "常用区域:玉树")
    b = memories.add("preference", "常用区域:玉树")
    assert a == b
    assert memories.get(a)["weight"] == 1.5
    memories.add("preference", "常用区域:玉树")
    assert memories.get(a)["weight"] == 2.0
    # 归档后重加是新行(用户显式删过,不给旧行还魂)
    memories.archive(a)
    c = memories.add("preference", "常用区域:玉树")
    assert c != a and memories.get(c)["weight"] == 1.0
    # 同内容不同 kind 不去重(分类不同就是两条记忆)
    d = memories.add("fact", "常用区域:玉树")
    assert d != c


# ---------------------------------------------------------------------------
# ② 注入契约:get_context_snippets(签名 + 行为)
# ---------------------------------------------------------------------------

def test_contract_signature_locked():
    """【契约守护】对话二期代理按此签名消费:改动即红,先对齐再动手。"""
    sig = inspect.signature(get_context_snippets)
    params = list(sig.parameters.values())
    assert [p.name for p in params] == ["store", "session_id", "limit"]
    assert params[0].default is inspect.Parameter.empty
    assert params[1].default is inspect.Parameter.empty
    assert params[2].default == 5
    assert all(p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD for p in params)
    assert str(sig.return_annotation).replace(" ", "") == "list[str]"


def test_snippets_empty_table(memories):
    assert get_context_snippets(memories, "sess-a") == []


def test_snippets_global_plus_session_scoped(memories):
    memories.add("preference", "全局:常用区域玉树")
    memories.add("fact", "会话A:本次用 2020-2023 数据", session_id="sess-a")
    memories.add("fact", "会话B:私有记忆", session_id="sess-b")
    got = get_context_snippets(memories, "sess-a")
    assert "全局:常用区域玉树" in got
    assert "会话A:本次用 2020-2023 数据" in got
    assert all("会话B" not in s for s in got)  # 他会话记忆不越界
    assert get_context_snippets(memories, None) == ["全局:常用区域玉树"]


def test_snippets_weight_sorted_and_limited(memories):
    memories.add("fact", "普通一")
    memories.add("preference", "常用区域:玉树")
    memories.add("preference", "常用区域:玉树")   # weight 1.5
    memories.add("fact", "普通二")
    got = get_context_snippets(memories, "sess-a")
    assert got[0] == "常用区域:玉树"               # 权重优先
    assert got[1:] == ["普通二", "普通一"]          # 同权新者优先
    assert get_context_snippets(memories, "sess-a", limit=2) == ["常用区域:玉树", "普通二"]


def test_snippets_exclude_archived(memories):
    mid = memories.add("fact", "将被删除的记忆")
    memories.archive(mid)
    assert get_context_snippets(memories, "sess-a") == []


# ---------------------------------------------------------------------------
# ③ 规则萃取:extract_from_run / record_preference
# ---------------------------------------------------------------------------

def test_extract_outcome_content_template(store, memories):
    run_id = make_done_run(store)
    out = extract_from_run(memories, run_id)
    date = time.strftime("%Y-%m-%d", time.localtime(store.get_run(run_id)["created_at"]))
    # simulated run 证据级恒 runnable(阶梯封顶:演示不冒充证据),模板全段可预期
    assert out["content"] == (f"{date} permafrost_sbas@玉树冻土排查"
                              f" 用 local_import→isce2_ifg→snaphu_mcf 完成,证据级 runnable")
    assert out["created"] is True
    m = memories.get(out["memory_id"])
    assert m["kind"] == "outcome" and m["source"] == "auto"
    assert m["session_id"] is None  # 结论是全局记忆:新会话也能带上


def test_extract_prefers_intent_region_over_session_name(store, memories):
    run_id = make_done_run(store, intent={"text": "任务", "region": "拉萨"})
    out = extract_from_run(memories, run_id)
    assert "@拉萨 用" in out["content"]
    assert "玉树冻土排查" not in out["content"]


def test_extract_real_run_level_from_ladder(store, memories):
    """非模拟 run:证据级由阶梯机器判定,断言落在六级闭集(不锁具体级,
    contract.yaml 的阈值标定状态变化不应使本测试碎裂)。"""
    run_id = make_done_run(store, simulated=False)
    out = extract_from_run(memories, run_id)
    level = out["content"].rsplit("证据级 ", 1)[1]
    assert level in LADDER


def test_extract_chain_dedups_and_truncates(store, memories):
    # 相邻同方法去重
    run_a = make_done_run(store, run_id="20260810T090000-mem00a", session="sess-a",
                          methods=(("acquire", "local_import"),
                                   ("aux", "local_import"),
                                   ("unwrap", "snaphu_mcf")))
    assert " 用 local_import→snaphu_mcf 完成" in extract_from_run(memories, run_a)["content"]
    # 超过 6 个:前 5 + 「等 n 步」
    many = tuple((f"cap{i}", f"m{i}") for i in range(1, 9))
    run_b = make_done_run(store, run_id="20260810T090000-mem00b", session="sess-b",
                          methods=many)
    assert " 用 m1→m2→m3→m4→m5→等8步 完成" in extract_from_run(memories, run_b)["content"]


def test_extract_all_skipped_run_falls_back_chain(store, memories):
    run_id = make_done_run(store, step_state="skipped")
    assert " 用 云端/缓存链 完成" in extract_from_run(memories, run_id)["content"]


def test_extract_idempotent_bumps_weight(store, memories):
    run_id = make_done_run(store)
    first = extract_from_run(memories, run_id)
    second = extract_from_run(memories, run_id)
    assert second["memory_id"] == first["memory_id"]
    assert second["created"] is False
    assert memories.get(first["memory_id"])["weight"] == 1.5


def test_extract_rejects_non_done_and_unknown(store, memories):
    run_id = make_done_run(store, status="running")
    with pytest.raises(ValueError):
        extract_from_run(memories, run_id)
    with pytest.raises(KeyError):
        extract_from_run(memories, "no-such-run")
    assert memories.list() == []  # 拒绝路径零写入


def test_record_preference_hook(memories):
    ids = record_preference(memories, "permafrost_sbas", "玉树")
    assert len(ids) == 2
    contents = {memories.get(i)["content"] for i in ids}
    assert contents == {"偏好场景:permafrost_sbas", "常用区域:玉树"}
    assert all(memories.get(i)["kind"] == "preference" for i in ids)
    assert all(memories.get(i)["source"] == "auto" for i in ids)
    # 重复调用幂等加权;空值跳过
    again = record_preference(memories, "permafrost_sbas", "玉树")
    assert again == ids
    assert all(memories.get(i)["weight"] == 1.5 for i in ids)
    assert record_preference(memories, "", None) == []
    only_region = record_preference(memories, None, "拉萨")
    assert [memories.get(i)["content"] for i in only_region] == ["常用区域:拉萨"]


# ---------------------------------------------------------------------------
# ④ API(隔离挂接 create_memory_router)
# ---------------------------------------------------------------------------

@pytest.fixture()
def client(store):
    app = FastAPI()
    app.include_router(create_memory_router(store))
    with TestClient(app) as c:
        yield c


def test_api_add_and_list(client):
    r = client.post("/api/memory", json={"kind": "preference", "content": "常用区域:玉树"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["memory"]["kind"] == "preference"
    assert body["memory"]["content"] == "常用区域:玉树"
    assert body["memory"]["source"] == "user"
    items = client.get("/api/memory").json()["items"]
    assert [m["content"] for m in items] == ["常用区域:玉树"]


def test_api_add_validation(client):
    assert client.post("/api/memory",
                       json={"kind": "vibe", "content": "x"}).status_code == 400
    assert client.post("/api/memory",
                       json={"kind": "fact", "content": "  "}).status_code == 400
    assert client.post("/api/memory",
                       json={"kind": "fact", "content": "长" * 501}).status_code == 400
    assert client.get("/api/memory").json()["items"] == []  # 校验失败零写入


def test_api_list_search_and_kind_filter(client):
    client.post("/api/memory", json={"kind": "preference", "content": "常用区域:玉树"})
    client.post("/api/memory", json={"kind": "fact", "content": "数据在 D:/SAR/yushu"})
    client.post("/api/memory", json={"kind": "outcome", "content": "玉树 SBAS 效果好"})
    assert len(client.get("/api/memory").json()["items"]) == 3
    hits = client.get("/api/memory", params={"q": "玉树"}).json()["items"]
    assert {m["kind"] for m in hits} == {"preference", "outcome"}
    both = client.get("/api/memory", params={"q": "玉树", "kind": "outcome"}).json()["items"]
    assert [m["kind"] for m in both] == ["outcome"]
    assert client.get("/api/memory", params={"kind": "bogus"}).status_code == 400


def test_api_delete_is_soft_archive(client):
    mid = client.post("/api/memory",
                      json={"kind": "fact", "content": "要删的记忆"}).json()["memory"]["id"]
    r = client.delete(f"/api/memory/{mid}")
    assert r.status_code == 200 and r.json()["archived"] is True
    assert client.get("/api/memory").json()["items"] == []
    kept = client.get("/api/memory", params={"include_archived": 1}).json()["items"]
    assert [m["id"] for m in kept] == [mid]  # 软删:数据保留
    assert client.delete("/api/memory/9999").status_code == 404


def test_api_extract_states(client, store):
    # run 不存在 → 404
    assert client.post("/api/memory/extract",
                       json={"run_id": "no-such"}).status_code == 404
    # 非 done → 409(状态原样进错误信息,前端可直显)
    running = make_done_run(store, run_id="20260810T090000-api-run",
                            session="sess-api", status="running")
    r = client.post("/api/memory/extract", json={"run_id": running})
    assert r.status_code == 409 and "running" in r.json()["detail"]
    # done → 200,记忆落库且模板成立;重复萃取幂等加权
    store.set_run_status(running, "done")
    first = client.post("/api/memory/extract", json={"run_id": running})
    assert first.status_code == 200
    body = first.json()
    assert body["ok"] is True and body["created"] is True
    assert "证据级" in body["memory"]["content"]
    assert body["memory"]["kind"] == "outcome" and body["memory"]["source"] == "auto"
    second = client.post("/api/memory/extract", json={"run_id": running}).json()
    assert second["created"] is False
    assert second["memory"]["weight"] == 1.5
    assert len(client.get("/api/memory").json()["items"]) == 1
