"""GET /api/runs 契约(run 历史切换器数据源)+ run_id 透传跨端点验证。

覆盖:
  - 清单契约:字段窄集(run_id/parent_run_id/created_at/status/scenario/
    steps 统计 total|done|skipped|failed),不泄漏 intent/tool_versions 大字段;
  - 排序:created_at 倒序(最新在前);
  - 谱系:fork 子 run 的 parent_run_id 指向父 run,根 run 为 None;
  - 会话归属:A 的 run 不出现在 B 的清单;未知会话 → 空清单且零副作用
    (纯读端点不走 driver_of,不为未知会话创建目录);
  - run_id 透传:/api/state、/api/figures、/api/artifacts、/api/provenance
    带 run_id 时返回对应 run 的数据(两个 run 的步骤/产物可区分),
    缺省 run_id 仍取最新 run;
  - 跨会话 404:B 会话带 A 的 run_id 查任一端点,一律按「不存在」处理
    (resolve_run 口径,不泄露其他会话 run 的存在性)。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from insar_agent.api.app import create_app
from insar_agent.core.db import Database
from insar_agent.core.store import Store

_HASHES = {"task_hash": "t0", "args_hash": "a0",
           "local_hash": "l0", "eval_hash": "e0" * 6}
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16

RUN_OLD = "20260810T000000-aaaaaaaa"        # 根 run,含 done/skipped/failed/pending 各态
RUN_NEW = "20260812T000000-bbbbbbbb"        # 根 run,2 步全 done
RUN_FORK = "20260813T000000-cccccccc-fork"  # RUN_NEW 的 fork 子 run(最新)


@pytest.fixture()
def env(tmp_path):
    """sess-a 名下三个 run(两根 + 一 fork,created_at 显式定序)+ 空会话 sess-b。"""
    home = tmp_path / "home"
    app = create_app(home=home)
    with TestClient(app) as client:
        client.post("/api/sessions", json={"id": "sess-a"})
        client.post("/api/sessions", json={"id": "sess-b"})
        store = Store(Database(home / "insar.db"))
        ws = home / "sessions" / "sess-a"

        store.create_run(RUN_OLD, "sess-a", workspace=str(ws), scenario="quake")
        store.create_run(RUN_NEW, "sess-a", workspace=str(ws), scenario="quake")
        store.create_run(RUN_FORK, "sess-a", workspace=str(ws), scenario="quake",
                         parent_run_id=RUN_NEW)
        # created_at 显式定序:排序断言不依赖三次 create_run 的挂钟间隔
        with store.db.tx() as cur:
            for rid, ts in ((RUN_OLD, 1000.0), (RUN_NEW, 2000.0), (RUN_FORK, 3000.0)):
                cur.execute("UPDATE runs SET created_at=? WHERE run_id=?", (ts, rid))
        store.set_run_status(RUN_OLD, "failed")
        store.set_run_status(RUN_NEW, "done")

        # RUN_OLD:done/skipped/failed/pending 各一 —— 步骤统计契约的样本
        for sid, st in ((1, "done"), (2, "skipped"), (3, "failed"), (4, "pending")):
            store.upsert_step(RUN_OLD, sid, capability=f"cap{sid}", name=f"旧步骤{sid}",
                              method="m_old", params={}, hashes=_HASHES)
            if st != "pending":
                store.mark_step(RUN_OLD, sid, state=st)
        # RUN_NEW:2 步全 done,方法/步骤名与 RUN_OLD 可区分(透传断言用)
        for sid in (1, 2):
            store.upsert_step(RUN_NEW, sid, capability=f"cap{sid}", name=f"新步骤{sid}",
                              method="m_new", params={}, hashes=_HASHES)
            store.mark_step(RUN_NEW, sid, state="done")

        # 两个 run 各一张真实落盘 PNG,文件名不同(figures/artifacts 透传断言用)
        for rid, png in ((RUN_OLD, "old_vel.png"), (RUN_NEW, "new_vel.png")):
            f = ws / "products" / png
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(PNG_BYTES)
            store.record_artifact(rid, 1, f"fig_{png}", path=f"products/{png}",
                                  kind="FIGURE", layout="", policy="stat",
                                  fp="stat:v1:deadbeef")

        yield {"client": client, "store": store, "home": home}
        store.close()


# ---------------- /api/runs 清单契约 ----------------

def test_runs_contract_fields(env):
    """字段窄集 + 步骤终态统计:切换器一行展示所需,不多不少。"""
    data = env["client"].get("/api/runs", params={"session": "sess-a"}).json()
    assert data["session"] == "sess-a"
    assert len(data["runs"]) == 3
    by_id = {r["run_id"]: r for r in data["runs"]}

    old = by_id[RUN_OLD]
    assert set(old) == {"run_id", "parent_run_id", "created_at",
                        "status", "scenario", "simulated", "steps"}
    assert old["status"] == "failed"
    assert old["scenario"] == "quake"
    assert old["created_at"] == 1000.0
    assert old["steps"] == {"total": 4, "done": 1, "skipped": 1, "failed": 1}
    # 无步骤的 run 统计全零(fork 后还没铺步骤的临界态,不 500)
    assert by_id[RUN_FORK]["steps"] == {"total": 0, "done": 0, "skipped": 0, "failed": 0}


def test_runs_sorted_newest_first(env):
    """created_at 倒序:最新在前 —— 切换器首项直接当「最新」用。"""
    data = env["client"].get("/api/runs", params={"session": "sess-a"}).json()
    assert [r["run_id"] for r in data["runs"]] == [RUN_FORK, RUN_NEW, RUN_OLD]


def test_runs_lineage_parent_chain(env):
    """谱系:fork 子 run 带 parent_run_id,根 run 为 None(前端据此画缩进树)。"""
    data = env["client"].get("/api/runs", params={"session": "sess-a"}).json()
    by_id = {r["run_id"]: r for r in data["runs"]}
    assert by_id[RUN_FORK]["parent_run_id"] == RUN_NEW
    assert by_id[RUN_NEW]["parent_run_id"] is None
    assert by_id[RUN_OLD]["parent_run_id"] is None


def test_runs_session_isolation(env):
    """会话归属:B 的清单看不到 A 的 run;有 run 的会话互不串。"""
    data = env["client"].get("/api/runs", params={"session": "sess-b"}).json()
    assert data == {"session": "sess-b", "runs": []}


def test_runs_unknown_session_empty_and_no_side_effect(env):
    """未知会话 → 空清单(不 404/500);纯读端点不为它创建目录或会话行。"""
    r = env["client"].get("/api/runs", params={"session": "ghost-session"})
    assert r.status_code == 200
    assert r.json() == {"session": "ghost-session", "runs": []}
    assert not (env["home"] / "sessions" / "ghost-session").exists()
    assert env["store"].get_session("ghost-session") is None


# ---------------- run_id 透传:各端点返回对应 run 的数据 ----------------

def test_state_run_id_passthrough(env):
    """/api/state:缺省取最新 run;带 run_id 返回该 run 的步骤(两 run 可区分)。"""
    c = env["client"]
    latest = c.get("/api/state", params={"session": "sess-a"}).json()
    assert latest["run"]["run_id"] == RUN_FORK

    old = c.get("/api/state", params={"session": "sess-a", "run_id": RUN_OLD}).json()
    assert old["run"]["run_id"] == RUN_OLD
    assert {s["method"] for s in old["steps"]} == {"m_old"}
    assert {s["state"] for s in old["steps"]} == {"done", "skipped", "failed", "pending"}

    new = c.get("/api/state", params={"session": "sess-a", "run_id": RUN_NEW}).json()
    assert new["run"]["run_id"] == RUN_NEW
    assert [s["name"] for s in new["steps"]] == ["新步骤1", "新步骤2"]
    assert {s["method"] for s in new["steps"]} == {"m_new"}


def test_figures_run_id_passthrough(env):
    """/api/figures:带 run_id 只列该 run 的图件,互不串。"""
    c = env["client"]
    old = c.get("/api/figures", params={"session": "sess-a", "run_id": RUN_OLD}).json()
    assert old["run"] == RUN_OLD
    assert [f["name"] for f in old["figures"]] == ["old_vel.png"]
    new = c.get("/api/figures", params={"session": "sess-a", "run_id": RUN_NEW}).json()
    assert new["run"] == RUN_NEW
    assert [f["name"] for f in new["figures"]] == ["new_vel.png"]
    # 缺省 = 最新(RUN_FORK,无产物):空表,不回落到别的 run
    latest = c.get("/api/figures", params={"session": "sess-a"}).json()
    assert latest["run"] == RUN_FORK and latest["figures"] == []


def test_artifacts_run_id_passthrough(env):
    """/api/artifacts(文件面板数据源):带 run_id 返回该 run 的产物清单。"""
    c = env["client"]
    old = c.get("/api/artifacts", params={"session": "sess-a", "run_id": RUN_OLD}).json()
    assert old["run"] == RUN_OLD
    paths = [a["path"] for s in old["steps"] for a in s["artifacts"]]
    assert paths == ["products/old_vel.png"]
    new = c.get("/api/artifacts", params={"session": "sess-a", "run_id": RUN_NEW}).json()
    assert new["run"] == RUN_NEW
    paths = [a["path"] for s in new["steps"] for a in s["artifacts"]]
    assert paths == ["products/new_vel.png"]


def test_provenance_run_id_passthrough(env):
    """/api/provenance(审计面板数据源):带 run_id 导出该 run 的证据链。"""
    c = env["client"]
    old = c.get("/api/provenance", params={"session": "sess-a", "run_id": RUN_OLD}).json()
    assert old["run_id"] == RUN_OLD
    latest = c.get("/api/provenance", params={"session": "sess-a"}).json()
    assert latest["run_id"] == RUN_FORK


def test_cross_session_run_id_404(env):
    """跨会话 404:B 会话带 A 的 run_id,各端点一律按「不存在」处理。"""
    c = env["client"]
    for path in ("/api/state", "/api/figures", "/api/artifacts", "/api/provenance"):
        r = c.get(path, params={"session": "sess-b", "run_id": RUN_OLD})
        assert r.status_code == 404, f"{path} 应对跨会话 run_id 返回 404,实得 {r.status_code}"
        assert "sess-a" not in r.text  # 错误文案不泄露 run 真正归属的会话
