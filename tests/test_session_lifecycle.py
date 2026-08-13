"""会话生命周期:重命名 / 归档(软删除)/ 还原 / 硬删除(purge)。

语义锁定(api/app.py 会话端点组 + core/store.py 会话方法):
  - PATCH /api/sessions/{id}:name 重命名(check_session_name 宽松校验:
    非空、≤80 字、无控制字符);archived=true/false 归档/还原。
  - DELETE /api/sessions/{id}:默认软删除(archived 列落时间戳,列表默认
    不显示;run 与工作区一律保留);?purge=1 仅当会话无任何 run 时允许,
    也只删 DB 行,工作区目录改名 <id>.deleted-<时间戳> 留人工回收。
  - 有 running run 的会话拒绝归档(409)。
  - 越权语义:会话本身无归属概念(单用户桌面应用),任何合法存在的
    session_id 都可被重命名/归档 —— 本文件按现状记录该语义。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from insar_agent.api.app import create_app
from insar_agent.core.db import Database
from insar_agent.core.store import Store

# ============================================================
# store 层(conftest 的 :memory: store fixture)
# ============================================================


def test_store_rename_session(store):
    store.create_session("s1", "旧名")
    assert store.rename_session("s1", "新名") is True
    assert store.get_session("s1")["name"] == "新名"
    assert store.rename_session("ghost", "x") is False  # 不存在:False,不落行


def test_store_archive_restore_and_list_filter(store):
    store.create_session("keep", "留着")
    store.create_session("gone", "归档我")
    assert store.archive_session("gone") is True

    ids = {s["session_id"] for s in store.list_sessions()}
    assert ids == {"keep"}  # 默认列表过滤已归档
    all_rows = {s["session_id"]: s for s in store.list_sessions(include_archived=True)}
    assert set(all_rows) == {"keep", "gone"}
    assert all_rows["gone"]["archived"] is not None  # 归档时刻已落
    assert all_rows["keep"]["archived"] is None

    assert store.restore_session("gone") is True
    assert {s["session_id"] for s in store.list_sessions()} == {"keep", "gone"}
    assert store.get_session("gone")["archived"] is None

    assert store.archive_session("ghost") is False
    assert store.restore_session("ghost") is False


def test_store_purge_refuses_with_runs(store):
    """极度保守:有 run 的会话绝不硬删(run/steps/artifacts 是溯源台账)。"""
    store.create_session("s1", "有 run")
    store.create_run("r1", "s1", workspace="/tmp/ws")
    with pytest.raises(ValueError, match=r"1 个 run"):
        store.purge_session("s1")
    assert store.get_session("s1") is not None  # 事务回滚,行还在
    assert store.get_run("r1") is not None


def test_store_purge_deletes_row_and_chat(store):
    store.create_session("s1", "无 run")
    store.append_chat("s1", "user", "你好")
    assert store.purge_session("s1") is True
    assert store.get_session("s1") is None
    assert store.chat_history("s1") == []  # 子行一并删(外键要求)
    assert store.purge_session("s1") is False  # 幂等:再删返回 False


def test_store_has_running_run_checks_all_not_latest(store):
    """归档守卫看全量 run:老 run 仍在跑而新 run 已终态时,latest 口径会漏。"""
    store.create_session("s1", "双 run")
    store.create_run("r-old", "s1", workspace="/tmp/a")
    store.create_run("r-new", "s1", workspace="/tmp/b")
    store.set_run_status("r-old", "running")
    store.set_run_status("r-new", "done")
    assert store.latest_run("s1")["run_id"] == "r-new"  # 前提:running 的不是最新
    assert store.has_running_run("s1") is True
    store.set_run_status("r-old", "done")
    assert store.has_running_run("s1") is False


# ============================================================
# API 层(密封 probe,同 tests/test_api.py)
# ============================================================


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from insar_agent.runtime.probe import ProbeResult

    def empty_probe(*args, **kwargs):
        return ProbeResult(
            engines={k: None for k in ("isce2", "mintpy", "snaphu", "gdal", "snap",
                                       "pystamps", "pyaps")},
            credentials={"earthdata": False, "cds": False, "gacos": False},
            disk_free_gb=100.0, cpu_count=8)

    monkeypatch.setattr("insar_agent.loop.driver.probe_environment", empty_probe)
    app = create_app(home=tmp_path / "home")
    with TestClient(app) as c:
        yield c


def _mk_session(client, sid="demo", name=None):
    r = client.post("/api/sessions", json={"id": sid, "name": name or sid})
    assert r.status_code == 200
    return r.json()


def _mk_run(client, sid="demo"):
    """跑一个规划回合给会话留下 run 行(模拟执行,秒级)。返回 run_id。"""
    with client.stream("POST", "/api/turn",
                       json={"session": sid, "text": "Ridgecrest 地震同震"}) as resp:
        assert resp.status_code == 200
        for line in resp.iter_lines():
            if line.strip():
                json.loads(line)  # 消费整条流,回合才算结束
    run = client.get("/api/state", params={"session": sid}).json()["run"]
    assert run is not None
    return run["run_id"]


def _listed_ids(client, include_archived=False):
    params = {"include_archived": "1"} if include_archived else {}
    return {s["session_id"] for s in client.get("/api/sessions", params=params).json()}


def test_patch_rename_ok(client):
    _mk_session(client, "demo", "原名")
    r = client.patch("/api/sessions/demo", json={"name": "Ridgecrest 重分析"})
    assert r.status_code == 200
    assert r.json()["name"] == "Ridgecrest 重分析"
    names = {s["session_id"]: s["name"] for s in client.get("/api/sessions").json()}
    assert names["demo"] == "Ridgecrest 重分析"


def test_patch_rename_boundaries(client):
    """check_session_name 宽松版边界:非空、≤80 字、无控制字符、首尾空白剔除。"""
    _mk_session(client)
    assert client.patch("/api/sessions/demo", json={"name": ""}).status_code == 400
    assert client.patch("/api/sessions/demo", json={"name": "   "}).status_code == 400
    assert client.patch("/api/sessions/demo", json={"name": "a" * 81}).status_code == 400
    assert client.patch("/api/sessions/demo", json={"name": "换\n行"}).status_code == 400
    assert client.patch("/api/sessions/demo", json={"name": "制\t表"}).status_code == 400

    r80 = client.patch("/api/sessions/demo", json={"name": "名" * 80})
    assert r80.status_code == 200 and r80.json()["name"] == "名" * 80
    # 宽松点:路径分隔符等文件系统敏感字符在 name 里合法(name 不落文件系统)
    r = client.patch("/api/sessions/demo", json={"name": "  A/B:C*D?  "})
    assert r.status_code == 200 and r.json()["name"] == "A/B:C*D?"  # 且首尾空白剔除


def test_patch_requires_some_field(client):
    _mk_session(client)
    r = client.patch("/api/sessions/demo", json={})
    assert r.status_code == 400
    assert "name" in r.json()["detail"] and "archived" in r.json()["detail"]


def test_patch_and_delete_unknown_session_404(client):
    assert client.patch("/api/sessions/ghost", json={"name": "x"}).status_code == 404
    assert client.delete("/api/sessions/ghost").status_code == 404
    assert client.delete("/api/sessions/ghost", params={"purge": "1"}).status_code == 404
    # id 非法(目录名级校验,如 Windows 保留设备名)走 400,不进 DB 查询
    assert client.patch("/api/sessions/CON", json={"name": "x"}).status_code == 400
    assert client.delete("/api/sessions/demo.").status_code == 400  # 结尾 '.' 非法


def test_delete_archives_by_default_and_preserves_data(client, tmp_path):
    """软删除:列表不显示;run 行、对话、工作区目录一律保留。"""
    _mk_session(client)
    run_id = _mk_run(client)
    ws = tmp_path / "home" / "sessions" / "demo"
    assert ws.is_dir()

    r = client.delete("/api/sessions/demo")
    assert r.status_code == 200
    body = r.json()
    assert body["archived"] is True and body["session"]["archived"] is not None

    assert "demo" not in _listed_ids(client)                       # 默认列表不显示
    assert "demo" in _listed_ids(client, include_archived=True)    # 全量列表可见
    assert ws.is_dir()                                             # 工作区原样保留
    state = client.get("/api/state", params={"session": "demo"}).json()
    assert state["run"]["run_id"] == run_id                        # run 数据原样保留


def test_archive_idempotent_and_archived_still_patchable(client):
    _mk_session(client)
    assert client.delete("/api/sessions/demo").status_code == 200
    assert client.delete("/api/sessions/demo").status_code == 200  # 重复归档幂等
    # 归档中的会话仍可重命名(还原后名字生效)
    assert client.patch("/api/sessions/demo",
                        json={"name": "改名不解归档"}).status_code == 200
    assert "demo" not in _listed_ids(client)


def test_patch_restore_archived(client):
    _mk_session(client)
    client.delete("/api/sessions/demo")
    assert "demo" not in _listed_ids(client)

    r = client.patch("/api/sessions/demo", json={"archived": False})
    assert r.status_code == 200 and r.json()["archived"] is None
    assert "demo" in _listed_ids(client)  # 还原后回到默认列表


def test_delete_running_run_409(client, tmp_path):
    """运行中会话拒绝归档:DELETE 与 PATCH archived=true 都是 409。"""
    _mk_session(client)
    run_id = _mk_run(client)
    db = Database(tmp_path / "home" / "insar.db")
    Store(db).set_run_status(run_id, "running")
    db.close()

    r = client.delete("/api/sessions/demo")
    assert r.status_code == 409 and "运行" in r.json()["detail"]
    r2 = client.patch("/api/sessions/demo", json={"archived": True})
    assert r2.status_code == 409
    assert "demo" in _listed_ids(client)  # 拒绝后仍在活跃列表

    db = Database(tmp_path / "home" / "insar.db")
    Store(db).set_run_status(run_id, "done")
    db.close()
    assert client.delete("/api/sessions/demo").status_code == 200  # 收尾后可归档


def test_purge_with_runs_409(client, tmp_path):
    """硬删除极度保守:有 run 一律 409,提示只能归档;数据分毫不动。"""
    _mk_session(client)
    _mk_run(client)
    r = client.delete("/api/sessions/demo", params={"purge": "1"})
    assert r.status_code == 409
    assert "1 个 run" in r.json()["detail"] and "归档" in r.json()["detail"]
    assert "demo" in _listed_ids(client)                                 # 行还在
    assert (tmp_path / "home" / "sessions" / "demo").is_dir()            # 目录还在


def test_purge_no_runs_renames_workspace(client, tmp_path):
    """无 run 的硬删除:只删 DB 行;工作区目录改名 <id>.deleted-<时间戳> 留人工回收。"""
    _mk_session(client)
    sessions_dir = tmp_path / "home" / "sessions"
    assert (sessions_dir / "demo").is_dir()  # driver_of 建过工作区

    r = client.delete("/api/sessions/demo", params={"purge": "1"})
    assert r.status_code == 200
    body = r.json()
    assert body["purged"] is True
    assert body["workspace_moved_to"].startswith("demo.deleted-")

    assert "demo" not in _listed_ids(client, include_archived=True)  # DB 行已删
    assert not (sessions_dir / "demo").exists()                      # 原目录名已让位
    moved = sessions_dir / body["workspace_moved_to"]
    assert moved.is_dir()                                            # 数据还在,改名保留
    assert client.delete("/api/sessions/demo").status_code == 404    # 再删:已不存在


def test_purge_archived_session_ok(client):
    """归档态不阻碍 purge(仍受「无 run」守卫)。"""
    _mk_session(client, "bare")
    client.delete("/api/sessions/bare")  # 先归档
    r = client.delete("/api/sessions/bare", params={"purge": "1"})
    assert r.status_code == 200
    assert r.json()["purged"] is True


def test_purge_without_workspace_dir_reports_none(client, tmp_path):
    """工作区目录从未建立(直接落 DB 的旧数据):purge 照常,moved 为 None。"""
    db = Database(tmp_path / "home" / "insar.db")
    Store(db).create_session("raw", "裸行")
    db.close()
    r = client.delete("/api/sessions/raw", params={"purge": "1"})
    assert r.status_code == 200
    assert r.json()["purged"] is True and r.json()["workspace_moved_to"] is None


def test_cross_session_single_user_semantics(client):
    """越权语义按现状记录:会话无归属概念(单用户桌面应用),任何存在的
    session_id 都可被重命名/归档;不存在的一律 404,不泄露更多信息。"""
    _mk_session(client, "alice")
    _mk_session(client, "bob")
    # 「以 alice 的视角」改 bob:没有鉴权维度,合法 id 即可操作(现状语义)
    assert client.patch("/api/sessions/bob", json={"name": "被改"}).status_code == 200
    assert client.delete("/api/sessions/bob").status_code == 200
    # bob 归档不影响 alice 的可见性
    assert _listed_ids(client) == {"alice"}
    # 不存在的 id:404(与「存在但无权」不做区分 —— 单用户语义下无此维度)
    assert client.patch("/api/sessions/carol", json={"name": "x"}).status_code == 404


def test_list_sessions_include_archived_param_shapes(client):
    """GET /api/sessions 的 include_archived 参数形状:缺省过滤、=1 全量。"""
    _mk_session(client, "a")
    _mk_session(client, "b")
    client.delete("/api/sessions/b")
    default_rows = client.get("/api/sessions").json()
    assert all(s["archived"] is None for s in default_rows)
    assert {s["session_id"] for s in default_rows} == {"a"}
    full = client.get("/api/sessions", params={"include_archived": "true"}).json()
    assert {s["session_id"] for s in full} == {"a", "b"}
