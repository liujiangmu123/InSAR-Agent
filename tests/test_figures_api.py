"""影像面板 API 契约与安全边界(/api/figures + /api/artifact-file)。

覆盖:
  - /api/figures 只列「图像扩展名 且 落盘文件存在 且 未越界」的产物;
  - /api/artifact-file 返回 200 与正确 Content-Type,字节原样透传;
  - 路径穿越(../../、绝对路径)→ 404 且不泄露磁盘路径;
  - 非图像扩展名 → 400;
  - 跨会话取他人 run → 404(与 resolve_run 口径一致);
  - 无 run / 未知产物 → 空列表 / 404。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from insar_agent.api.app import create_app
from insar_agent.core.db import Database
from insar_agent.core.store import Store

# 内容不要求可解码:端点按扩展名闭集定 Content-Type,这里只验证字节透传
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 16

RUN_ID = "20260812T000000-figtest"


def _add_artifact(store, ws, run_id, step, art_id, rel_path, *,
                  data=PNG_BYTES, kind="FIGURE", write=True):
    """写一行 artifacts + (可选)落盘文件。rel_path 允许包含 ../ 以构造穿越样例。"""
    if write:
        f = ws / rel_path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)
    store.record_artifact(run_id, step, art_id, path=rel_path, kind=kind,
                          layout="", policy="stat", fp="stat:sha256:deadbeef")


@pytest.fixture()
def env(tmp_path):
    """两个会话 + sess-a 名下一个带 PNG 产物的模拟 run(直接写 store 行 + tmp 文件)。"""
    home = tmp_path / "home"
    app = create_app(home=home)
    with TestClient(app) as client:
        client.post("/api/sessions", json={"id": "sess-a"})
        client.post("/api/sessions", json={"id": "sess-b"})
        store = Store(Database(home / "insar.db"))
        ws = home / "sessions" / "sess-a"
        store.create_run(RUN_ID, "sess-a", workspace=str(ws))

        _add_artifact(store, ws, RUN_ID, 10, "vel_png", "products/figures/velocity.png")
        _add_artifact(store, ws, RUN_ID, 10, "hist_jpg", "products/figures/velocity_hist.jpg",
                      data=JPG_BYTES)
        # 非图像扩展名:列表须排除,直读须 400
        _add_artifact(store, ws, RUN_ID, 9, "vel_h5", "mintpy/velocity.h5",
                      data=b"\x89HDF", kind="DATA")
        # 穿越样例:文件真实存在于工作区之外(home/evil.png),仍必须拒绝
        _add_artifact(store, ws, RUN_ID, 10, "evil", "../../evil.png")
        # 绝对路径样例(坏 DB 行)
        outside = tmp_path / "outside.png"
        outside.write_bytes(PNG_BYTES)
        _add_artifact(store, ws, RUN_ID, 10, "abs_evil", str(outside), write=False)
        # 记录存在但文件已被删:列表须排除,直读须 404
        _add_artifact(store, ws, RUN_ID, 10, "ghost_png", "products/figures/ghost.png",
                      write=False)

        yield {"client": client, "store": store, "ws": ws, "home": home}
        store.close()


# ---------------- /api/figures ----------------

def test_figures_lists_only_valid_images(env):
    c = env["client"]
    data = c.get("/api/figures", params={"session": "sess-a"}).json()
    assert data["run"] == RUN_ID
    by_id = {f["artId"]: f for f in data["figures"]}
    # 只剩两张真实存在的图像:h5(扩展名)/evil(越界)/abs_evil(绝对)/ghost(缺文件)都不列
    assert set(by_id) == {"vel_png", "hist_jpg"}

    vel = by_id["vel_png"]
    assert vel["step"] == 10
    assert vel["name"] == "velocity.png"
    assert vel["path"] == "products/figures/velocity.png"
    assert vel["size"] == len(PNG_BYTES)
    assert vel["mtime"] > 0
    assert "/api/artifact-file?" in vel["url"] and "art_id=vel_png" in vel["url"]


def test_figures_explicit_run_id_and_no_run(env):
    c = env["client"]
    data = c.get("/api/figures", params={"session": "sess-a", "run_id": RUN_ID}).json()
    assert data["run"] == RUN_ID and len(data["figures"]) == 2
    # sess-b 没有任何 run:返回空表而非报错(前端以此回落演示图件)
    empty = c.get("/api/figures", params={"session": "sess-b"}).json()
    assert empty == {"run": None, "figures": []}


def test_figures_cross_session_404(env):
    c = env["client"]
    r = c.get("/api/figures", params={"session": "sess-b", "run_id": RUN_ID})
    assert r.status_code == 404


# ---------------- /api/artifact-file ----------------

def test_artifact_file_png_and_jpg_roundtrip(env):
    c = env["client"]
    r = c.get("/api/artifact-file", params={
        "session": "sess-a", "run_id": RUN_ID, "step": 10, "art_id": "vel_png"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/png")
    assert r.content == PNG_BYTES

    r2 = c.get("/api/artifact-file", params={
        "session": "sess-a", "run_id": RUN_ID, "step": 10, "art_id": "hist_jpg"})
    assert r2.status_code == 200
    assert r2.headers["content-type"].startswith("image/jpeg")
    assert r2.content == JPG_BYTES


def test_artifact_file_url_from_figures_works(env):
    """列表返回的 url 可直接用作 <img src>(契约:两个端点自洽)。"""
    c = env["client"]
    for fig in c.get("/api/figures", params={"session": "sess-a"}).json()["figures"]:
        r = c.get(fig["url"])
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("image/")


def test_artifact_file_traversal_404_no_path_leak(env):
    c = env["client"]
    # ../../ 穿越:目标文件真实存在于工作区外,仍必须 404
    r = c.get("/api/artifact-file", params={
        "session": "sess-a", "run_id": RUN_ID, "step": 10, "art_id": "evil"})
    assert r.status_code == 404
    # 绝对路径坏行同样 404
    r2 = c.get("/api/artifact-file", params={
        "session": "sess-a", "run_id": RUN_ID, "step": 10, "art_id": "abs_evil"})
    assert r2.status_code == 404
    # 错误信息不泄露磁盘布局
    leak = str(env["home"]).replace("\\", "/")
    for resp in (r, r2):
        text = resp.text.replace("\\\\", "/").replace("\\", "/")
        assert "evil" not in text and leak not in text


def test_artifact_file_non_image_extension_400(env):
    c = env["client"]
    r = c.get("/api/artifact-file", params={
        "session": "sess-a", "run_id": RUN_ID, "step": 9, "art_id": "vel_h5"})
    assert r.status_code == 400


def test_artifact_file_missing_file_404(env):
    c = env["client"]
    r = c.get("/api/artifact-file", params={
        "session": "sess-a", "run_id": RUN_ID, "step": 10, "art_id": "ghost_png"})
    assert r.status_code == 404


def test_artifact_file_unknown_artifact_404(env):
    c = env["client"]
    r = c.get("/api/artifact-file", params={
        "session": "sess-a", "run_id": RUN_ID, "step": 10, "art_id": "nope"})
    assert r.status_code == 404


def test_artifact_file_cross_session_404(env):
    """sess-b 借 run_id 取 sess-a 的产物:按「不存在」处理,不泄露归属。"""
    c = env["client"]
    r = c.get("/api/artifact-file", params={
        "session": "sess-b", "run_id": RUN_ID, "step": 10, "art_id": "vel_png"})
    assert r.status_code == 404


def test_artifact_file_no_run_404(env):
    c = env["client"]
    r = c.get("/api/artifact-file", params={
        "session": "sess-b", "step": 10, "art_id": "vel_png"})
    assert r.status_code == 404


# ---------------- 目录型产物(注册表真实形态:figures 声明的是目录) ----------------
# 2026-08-12 终验发现:标准管线第 10 步的产物行是 products/figures 目录,
# 只按产物路径后缀过滤会让画廊对真实链永远空转 —— 目录型必须枚举内部图像。

def _add_dir_artifact(env):
    ws = env["ws"]
    gal = ws / "products" / "gallery"
    gal.mkdir(parents=True, exist_ok=True)
    (gal / "a_velocity.png").write_bytes(PNG_BYTES)
    (gal / "b_hist.jpg").write_bytes(JPG_BYTES)
    (gal / "notes.txt").write_bytes(b"not an image")
    (gal / "sub").mkdir(exist_ok=True)  # 子目录不下钻
    (gal / "sub" / "deep.png").write_bytes(PNG_BYTES)
    env["store"].record_artifact(RUN_ID, 10, "gallery_dir", path="products/gallery",
                                 kind="FIGURE", layout="", policy="stat",
                                 fp="stat:sha256:cafebabe")


def test_figures_lists_directory_artifact_members(env):
    _add_dir_artifact(env)
    c = env["client"]
    figs = c.get("/api/figures", params={"session": "sess-a"}).json()["figures"]
    members = [f for f in figs if f["artId"] == "gallery_dir"]
    # 只列顶层图像成员:txt 与子目录内文件都不列
    assert sorted(m["name"] for m in members) == ["a_velocity.png", "b_hist.jpg"]
    for m in members:
        assert "file=" in m["url"]
        r = c.get(m["url"])  # 列表 url 直接可用作 <img src>
        assert r.status_code == 200 and r.headers["content-type"].startswith("image/")


def test_artifact_file_dir_member_traversal_404(env):
    _add_dir_artifact(env)
    c = env["client"]
    # 成员穿越:解析后跳出产物目录必须 404(工作区内的其他文件也不许经此读取)
    r = c.get("/api/artifact-file", params={
        "session": "sess-a", "run_id": RUN_ID, "step": 10, "art_id": "gallery_dir",
        "file": "../figures/velocity.png"})
    assert r.status_code == 404
    r2 = c.get("/api/artifact-file", params={
        "session": "sess-a", "run_id": RUN_ID, "step": 10, "art_id": "gallery_dir",
        "file": "../../../evil.png"})
    assert r2.status_code == 404


def test_artifact_file_dir_member_non_image_400_and_file_on_file_404(env):
    _add_dir_artifact(env)
    c = env["client"]
    r = c.get("/api/artifact-file", params={
        "session": "sess-a", "run_id": RUN_ID, "step": 10, "art_id": "gallery_dir",
        "file": "notes.txt"})
    assert r.status_code == 400
    # file 参数只对目录型产物有意义:对文件型产物给 file → 404
    r2 = c.get("/api/artifact-file", params={
        "session": "sess-a", "run_id": RUN_ID, "step": 10, "art_id": "vel_png",
        "file": "velocity.png"})
    assert r2.status_code == 404
