"""产物清单端点契约与安全边界(/api/artifacts,files 面板真实化)。

覆盖:
  - 按步骤分组的结构:steps 升序、组内产物按 artId 升序、步骤名/方法来自
    steps 表、无产物的步骤不出组;
  - 字段清单钉死(防漂移):顶层/步骤/产物三层的键集合与
    prototype/js/fileslive.js 逐字段读取一一对应;
  - fp 回三段完整编码(policy:algo:digest),不缩写;
  - size/mtime 落盘实测优先,文件缺失回落 DB 记录并标 exists=False;
    目录型产物沿用执行器口径(size/mtime 记 None);
  - 路径只回工作区相对路径:绝对路径/../ 越界的坏 DB 行降级为仅文件名,
    响应全文不泄露盘符与磁盘布局;
  - 会话归属与 app.py resolve_run 同口径:跨会话取他人 run → 404(不泄露
    存在性),无 run 会话 → {"run": None, "steps": []}(前端回落演示的判据)。

路由挂接:本分支不改 api/app.py,测试直接 include create_artifacts_router;
主线合并时在 create_app 里加一行 include_router 即接通。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from insar_agent.api.artifacts_router import create_artifacts_router
from insar_agent.core.db import Database
from insar_agent.core.store import Store

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32

RUN_ID = "20260812T000000-arttest"

#: fileslive.js 逐字段读取的键集合 —— 增删任何键先红在这里,不等面板空白
TOP_FIELDS = {"run", "steps"}
STEP_FIELDS = {"stepId", "name", "method", "artifacts"}
ARTIFACT_FIELDS = {"artId", "path", "kind", "policy", "fp", "size", "mtime", "exists"}

_HASHES = {"task_hash": "t", "args_hash": "a", "local_hash": "l", "eval_hash": "e"}


def _add_artifact(store, ws, run_id, step, art_id, rel_path, *, data=PNG_BYTES,
                  kind="FIGURE", policy="content", fp=None, write=True,
                  size=None, mtime_ns=None):
    """写一行 artifacts + (可选)落盘文件。rel_path 允许 ../ 与绝对路径构造坏行。"""
    if write:
        f = ws / rel_path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)
    store.record_artifact(run_id, step, art_id, path=rel_path, kind=kind,
                          layout="", policy=policy,
                          fp=fp or f"{policy}:sha256:{'ab12' * 16}",
                          size=size, mtime_ns=mtime_ns)


@pytest.fixture()
def env(tmp_path):
    """两个会话 + sess-a 名下一个带产物的模拟 run(直接写 store 行 + tmp 文件),
    app 只挂 artifacts 路由(create_artifacts_router(store) 模式)。"""
    store = Store(Database(tmp_path / "insar.db"))
    store.create_session("sess-a", "sess-a")
    store.create_session("sess-b", "sess-b")
    ws = tmp_path / "sessions" / "sess-a"
    ws.mkdir(parents=True)
    store.create_run(RUN_ID, "sess-a", workspace=str(ws))

    # 步骤声明:名称/方法是分组头的数据源;第 5 步无产物,须不出组
    store.upsert_step(RUN_ID, 3, capability="coregister", name="配准",
                      method="isce2_tops_geom_esd", params={}, hashes=_HASHES)
    store.upsert_step(RUN_ID, 5, capability="filter", name="滤波",
                      method="goldstein", params={}, hashes=_HASHES)
    store.upsert_step(RUN_ID, 10, capability="figures", name="出图导出",
                      method="figure_journal", params={}, hashes=_HASHES)

    # 目录型产物(执行器口径:size/mtime 记 None,目录在盘)
    (ws / "data" / "coreg").mkdir(parents=True)
    _add_artifact(store, ws, RUN_ID, 3, "coreg", "data/coreg", write=False,
                  kind="RSLC", policy="stat", fp="stat:v1:" + "cd34" * 16)
    # 常规文件产物(在盘:size/mtime 取实测)
    _add_artifact(store, ws, RUN_ID, 10, "vel_png", "products/figures/velocity.png")
    # 记录在、文件已删(DB 有 size/mtime 记录):列出但 exists=False,回落 DB 值
    _add_artifact(store, ws, RUN_ID, 10, "ghost", "products/figures/ghost.png",
                  write=False, size=1234, mtime_ns=1_700_000_000_000_000_000)
    # 越界坏行:../../ 穿越(文件真实存在于工作区之外)
    _add_artifact(store, ws, RUN_ID, 10, "trav", "../../evil.png")
    # 绝对路径坏行(不落盘)
    outside = tmp_path / "outside.png"
    outside.write_bytes(PNG_BYTES)
    _add_artifact(store, ws, RUN_ID, 10, "abs_evil", str(outside), write=False)

    app = FastAPI()
    app.include_router(create_artifacts_router(store))
    with TestClient(app) as client:
        yield {"client": client, "store": store, "ws": ws, "tmp": tmp_path}
    store.close()


def _get(env, **params):
    return env["client"].get("/api/artifacts", params=params)


def _arts_of(data, step_id):
    step = next(s for s in data["steps"] if s["stepId"] == step_id)
    return {a["artId"]: a for a in step["artifacts"]}


# ---------------- 分组结构与字段清单 ----------------

def test_grouped_by_step_with_names_and_order(env):
    data = _get(env, session="sess-a").json()
    assert data["run"] == RUN_ID
    # 步骤升序;无产物的第 5 步不出组
    assert [s["stepId"] for s in data["steps"]] == [3, 10]
    s3, s10 = data["steps"]
    assert (s3["name"], s3["method"]) == ("配准", "isce2_tops_geom_esd")
    assert (s10["name"], s10["method"]) == ("出图导出", "figure_journal")
    # 组内产物按 artId 升序
    assert [a["artId"] for a in s10["artifacts"]] == ["abs_evil", "ghost", "trav", "vel_png"]


def test_field_sets_pinned_against_drift(env):
    """三层键集合精确锁定:fileslive.js 逐字段消费,后端删改任一键这里先红。"""
    data = _get(env, session="sess-a").json()
    assert set(data) == TOP_FIELDS
    for s in data["steps"]:
        assert set(s) == STEP_FIELDS
        assert isinstance(s["stepId"], int) and isinstance(s["name"], str)
        for a in s["artifacts"]:
            assert set(a) == ARTIFACT_FIELDS


def test_full_three_segment_fingerprint(env):
    """fp 回三段完整编码,不缩写(短哈希是前端展示层的事)。"""
    arts = _arts_of(_get(env, session="sess-a").json(), 10)
    assert arts["vel_png"]["fp"] == "content:sha256:" + "ab12" * 16
    assert arts["vel_png"]["policy"] == "content"
    coreg = _arts_of(_get(env, session="sess-a").json(), 3)["coreg"]
    assert coreg["fp"] == "stat:v1:" + "cd34" * 16


# ---------------- size / mtime / exists 语义 ----------------

def test_size_mtime_live_stat_for_existing_file(env):
    a = _arts_of(_get(env, session="sess-a").json(), 10)["vel_png"]
    assert a["exists"] is True
    assert a["size"] == len(PNG_BYTES)      # 实测值,不信 DB(记录时传的 None)
    assert a["mtime"] and a["mtime"] > 0
    assert a["kind"] == "FIGURE"


def test_missing_file_kept_with_db_fallback(env):
    """文件已删的记录照常列出(记录本位),exists=False,size/mtime 回落 DB。"""
    a = _arts_of(_get(env, session="sess-a").json(), 10)["ghost"]
    assert a["exists"] is False
    assert a["size"] == 1234
    assert a["mtime"] == pytest.approx(1_700_000_000.0)


def test_directory_artifact_follows_executor_convention(env):
    """目录型产物:在盘 exists=True,size/mtime 沿执行器口径为 None。"""
    a = _arts_of(_get(env, session="sess-a").json(), 3)["coreg"]
    assert a["exists"] is True
    assert a["size"] is None and a["mtime"] is None
    assert a["path"] == "data/coreg"


# ---------------- 路径安全:相对路径,不泄露盘符 ----------------

def test_paths_relative_no_disk_layout_leak(env):
    resp = _get(env, session="sess-a")
    arts = _arts_of(resp.json(), 10)
    # 正常行:工作区相对路径,正斜杠
    assert arts["vel_png"]["path"] == "products/figures/velocity.png"
    # 坏行(绝对路径 / ../ 越界):降级为仅文件名
    assert arts["abs_evil"]["path"] == "outside.png"
    assert arts["trav"]["path"] == "evil.png"
    # 响应全文不出现盘上布局(统一斜杠后比对,覆盖 JSON 转义)
    leak = str(env["tmp"]).replace("\\", "/")
    text = resp.text.replace("\\\\", "/").replace("\\", "/")
    assert leak not in text
    drive = env["tmp"].drive
    if drive:  # Windows 上盘符(如 "C:")绝不可出现在任何 path 字段
        for step in resp.json()["steps"]:
            for a in step["artifacts"]:
                assert drive not in a["path"]


# ---------------- 会话归属(resolve_run 同口径) ----------------

def test_cross_session_404_no_leak(env):
    """sess-b 借 run_id 取 sess-a 的清单:按「不存在」处理,不泄露归属与路径。"""
    r = _get(env, session="sess-b", run_id=RUN_ID)
    assert r.status_code == 404
    leak = str(env["tmp"]).replace("\\", "/")
    assert leak not in r.text.replace("\\\\", "/").replace("\\", "/")


def test_no_run_session_returns_null_run(env):
    """无 run 会话 → {"run": None, "steps": []}(fileslive.js 回落演示的判据)。"""
    assert _get(env, session="sess-b").json() == {"run": None, "steps": []}


def test_unknown_run_id_matches_resolve_run_semantics(env):
    """未知 run_id 与 app.py resolve_run(required=False) 同口径:按无 run 处理。"""
    r = _get(env, session="sess-a", run_id="nope-123")
    assert r.status_code == 200
    assert r.json() == {"run": None, "steps": []}


def test_explicit_run_id_equals_default(env):
    c = env["client"]
    by_default = c.get("/api/artifacts", params={"session": "sess-a"}).json()
    by_run_id = c.get("/api/artifacts",
                      params={"session": "sess-a", "run_id": RUN_ID}).json()
    assert by_default == by_run_id
