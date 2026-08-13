# -*- coding: utf-8 -*-
"""诊断包导出(report/diagbundle.py + /api/diagnostics*)。

覆盖:
  - 包内容清单:manifest.json / env.json / db_summary.json / logs/*;
    manifest 字段(UTC 时间戳、app 版本 version_router 口径、平台信息、
    run 选择依据、包内文件清单与实际成员双向一致);
  - env.json 白名单:INSAR_* 全收、形似密钥的值打码、PATH 只报条数;
  - db_summary:表行数、最近 run 倒序、note/error 事件合并封顶
    (user 消息绝不进摘要);
  - 脱敏:主目录 → ~(默认且不可关)、其余盘符绝对路径打码(默认开可关);
  - 大小裁剪:L1(日志 64KB/文件)→ L2(事件 10 条)→ L3(丢弃条目)
    逐级触发,manifest 如实记录,总量不超上限;
  - API:POST 生成 + 下载链路贯通;显式 run_id 不存在 404;
    下载文件名白名单拒绝一切穿越/变形。

不运行任何真实 InSAR 计算;API 用 TestClient 进程内调用,不监听端口。
"""

from __future__ import annotations

import json
import os
import platform
import re
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from insar_agent import __version__
from insar_agent.core.db import Database
from insar_agent.core.store import Store
from insar_agent.report.diagbundle import DIAG_NAME_RE, build_diag_bundle, redact_text

HOME_STR = str(Path.home())


@pytest.fixture(autouse=True)
def stub_probe(monkeypatch):
    """环境探测打桩:诊断打包不因本机引擎安装状态而漂移,也不扫描磁盘。"""
    from insar_agent.runtime.probe import ProbeResult

    monkeypatch.setattr(
        "insar_agent.report.diagbundle.probe_environment",
        lambda *a, **k: ProbeResult(engines={"mintpy": None, "gdal": "present"},
                                    credentials={"earthdata": False}, cpu_count=8))


def make_home(tmp_path, *, log_texts: dict[str, str] | None = None,
              error_events: int = 3) -> SimpleNamespace:
    """tmp 假 workspace:insar.db(2 个 run,最新的 failed)+ 假日志 + provenance。

    log_texts:{"s01/a1": 内容} 形态的 job.log 清单;缺省造一条含主目录路径
    与另一盘符绝对路径的日志(脱敏用例的靶子)。
    """
    home = tmp_path / "home"
    home.mkdir()
    ws = home / "sessions" / "s1"
    ws.mkdir(parents=True)
    store = Store(Database(home / "insar.db"))
    store.create_session("s1", "s1")
    store.create_run("run-old", "s1", workspace=str(ws))
    store.set_run_status("run-old", "done")
    store.create_run("run-bad", "s1", workspace=str(ws))
    store.set_run_status("run-bad", "failed")
    with store.db.tx() as cur:  # created_at 显式拉开:选 run 排序不受时钟分辨率影响
        cur.execute("UPDATE runs SET created_at=1000.0 WHERE run_id='run-old'")
        cur.execute("UPDATE runs SET created_at=2000.0 WHERE run_id='run-bad'")

    if log_texts is None:
        log_texts = {"s01/a1": (f"start\nreading {HOME_STR}\\input.slc\n"
                                "writing E:\\secret\\out.bin\ndone\n")}
    for rel, text in log_texts.items():
        job_dir = ws / ".jobs" / "run-bad" / Path(rel)
        job_dir.mkdir(parents=True)
        (job_dir / "job.log").write_text(text, encoding="utf-8")

    (ws / "provenance.json").write_text(
        json.dumps({"run_id": "run-bad", "workspace_hint": str(ws)}, ensure_ascii=False),
        encoding="utf-8")

    for i in range(error_events):
        store.append_trace(run_id="run-bad", error_occurred=True,
                           error_type="EngineError", error_message=f"boom {i}")
    store.append_chat("s1", "note", "第 3 步完成,产物已入账")
    store.append_chat("s1", "user", "user 消息绝不进诊断包")
    store.close()  # 打包用自己的只读视角开库,夹具句柄先撤
    return SimpleNamespace(home=home, ws=ws)


# ---------------- 包内容与 manifest ----------------


def test_bundle_contents_and_manifest(tmp_path, monkeypatch):
    monkeypatch.setenv("INSAR_TEST_MARKER", "42")
    monkeypatch.setenv("INSAR_FAKE_TOKEN", "supersecretvalue")
    env_home = make_home(tmp_path, error_events=60)
    out = build_diag_bundle(env_home.home)

    assert out.parent == env_home.home / "diagnostics"
    assert DIAG_NAME_RE.match(out.name), "zip 名必须落在下载白名单字符集内"

    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
        assert {"manifest.json", "env.json", "db_summary.json",
                "logs/s01/a1/job.log", "logs/provenance.json"} <= names

        man = json.loads(zf.read("manifest.json"))
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", man["generated_at_utc"])
        # app 版本与 version_router 同口径(GET /api/version 的字段)
        assert man["app"]["version"] == __version__
        assert set(man["app"]) >= {"version", "git_head", "python", "platform", "build"}
        assert man["platform"]["cpu_logical"] == os.cpu_count()
        assert man["platform"]["python"] == platform.python_version()
        # run 选择:缺省取最近一个失败 run
        assert man["run"]["run_id"] == "run-bad"
        assert man["run_selected_by"] == "最近一个失败/中断 run"
        assert man["size_budget"]["trim_steps"] == [], "未超限不得触发裁剪"
        # 包内清单与实际成员双向一致(manifest 自身除外)
        assert {f["name"] for f in man["files"]} == names - {"manifest.json"}

        env = json.loads(zf.read("env.json"))
        assert env["env_vars"]["INSAR_TEST_MARKER"] == "42"
        assert "supersecretvalue" not in zf.read("env.json").decode("utf-8"), \
            "形似密钥的 INSAR_* 值必须打码"
        assert isinstance(env["path_entry_count"], int)
        assert "PATH" not in env["env_vars"], "PATH 只报条数,内容防泄漏"
        assert env["probe"]["engines"] == {"mintpy": None, "gdal": "present"}
        assert env["wsl_probe_from_cache"] is False  # 冷缓存:绝不现场触发 20s 探测

        db = json.loads(zf.read("db_summary.json"))
        assert db["table_counts"]["runs"] == 2
        assert db["table_counts"]["chat_messages"] == 2
        assert [r["run_id"] for r in db["recent_runs"]] == ["run-bad", "run-old"]
        assert len(db["events"]) == 50, "60 错误 + 1 note 封顶 50 条"
        kinds = {e["kind"] for e in db["events"]}
        assert kinds == {"error", "note"}
        assert all("user 消息绝不进诊断包" not in (e.get("text") or "")
                   for e in db["events"])

        prov = json.loads(zf.read("logs/provenance.json"))
        assert prov["run_id"] == "run-bad"


def test_explicit_run_and_unknown_run(tmp_path):
    env_home = make_home(tmp_path)
    out = build_diag_bundle(env_home.home, "run-old")
    with zipfile.ZipFile(out) as zf:
        man = json.loads(zf.read("manifest.json"))
    assert man["run"]["run_id"] == "run-old"
    assert man["run_selected_by"] == "指定 run_id"
    with pytest.raises(KeyError):
        build_diag_bundle(env_home.home, "run-nope")


# ---------------- 脱敏 ----------------


def test_redaction_home_and_drive_paths(tmp_path):
    env_home = make_home(tmp_path)
    out = build_diag_bundle(env_home.home)
    with zipfile.ZipFile(out) as zf:
        log = zf.read("logs/s01/a1/job.log").decode("utf-8")
        prov = zf.read("logs/provenance.json").decode("utf-8")
    assert HOME_STR not in log, "用户主目录必须替换"
    assert "~" in log
    assert "E:\\secret" not in log, "盘符绝对路径默认打码"
    assert "E:\\***" in log
    assert HOME_STR not in prov and str(env_home.ws) not in prov, \
        "provenance 里的工作区路径同样脱敏"

    # 打码选项可关:盘符路径保留原文;主目录替换是底线,不受选项影响
    out2 = build_diag_bundle(env_home.home, mask_abs_paths=False)
    with zipfile.ZipFile(out2) as zf:
        log2 = zf.read("logs/s01/a1/job.log").decode("utf-8")
        man2 = json.loads(zf.read("manifest.json"))
    assert "E:\\secret\\out.bin" in log2
    assert HOME_STR not in log2
    assert man2["redaction"] == {"home_replaced": True, "abs_paths_masked": False}


def test_redact_text_unit():
    assert redact_text(rf"cwd={HOME_STR}\proj") == r"cwd=~\proj"
    assert redact_text(r"see C:\Users\bob\data.txt") == r"see ~\data.txt"
    assert redact_text(r"raster D:\big\stack.h5") == r"raster D:\***"
    assert redact_text(r"share \\nas01\insar\pairs") == r"share \\***"
    assert redact_text(r"raster D:\big\stack.h5",
                       mask_abs_paths=False) == r"raster D:\big\stack.h5"


# ---------------- 大小裁剪(逐级砍) ----------------


def _big_home(tmp_path):
    """3 个 150KB 日志 + 60 条错误事件:配小上限逐级触发裁剪。"""
    line = ("x" * 49 + "\n") * 3000  # 150_000 字节/文件,纯 ASCII 无路径
    return make_home(tmp_path, error_events=60,
                     log_texts={"s01/a1": line, "s02/a1": line, "s03/a1": line})


def test_trim_l1_tail_to_64kb(tmp_path):
    env_home = _big_home(tmp_path)
    out = build_diag_bundle(env_home.home, max_total_bytes=300 * 1024)
    with zipfile.ZipFile(out) as zf:
        man = json.loads(zf.read("manifest.json"))
        logs = [f for f in man["files"] if f["name"].endswith("job.log")]
        assert len(logs) == 3, "L1 只砍尾部额度,不丢文件"
        for f in logs:
            assert f["bytes"] <= 66 * 1024
        text = zf.read(logs[0]["name"]).decode("utf-8")
        db = json.loads(zf.read("db_summary.json"))
    assert text.startswith("[诊断包截断]"), "截断必须显式标注"
    steps = man["size_budget"]["trim_steps"]
    assert len(steps) == 1 and steps[0].startswith("L1"), f"只应触发 L1:{steps}"
    assert len(db["events"]) == 50, "L1 不触碰事件条数"
    total = sum(f["bytes"] for f in man["files"])
    assert total <= 300 * 1024


def test_trim_l2_and_l3_ladder(tmp_path):
    env_home = _big_home(tmp_path)
    out = build_diag_bundle(env_home.home, max_total_bytes=120 * 1024)
    with zipfile.ZipFile(out) as zf:
        man = json.loads(zf.read("manifest.json"))
        db = json.loads(zf.read("db_summary.json"))
        names = set(zf.namelist())
    steps = man["size_budget"]["trim_steps"]
    assert [s[:2] for s in steps] == ["L1", "L2", "L3"], f"逐级触发:{steps}"
    assert len(db["events"]) == 10, "L2 事件砍到 10 条"
    kept_logs = [n for n in names if n.endswith("job.log")]
    assert len(kept_logs) == 1, "L3 丢弃两个最大日志,保底留一个"
    assert "L3 丢弃超额条目" in steps[2] and steps[2].count("job.log") == 2
    # 核心三份永不丢
    assert {"manifest.json", "env.json", "db_summary.json"} <= names
    total = sum(f["bytes"] for f in man["files"])
    assert total <= 120 * 1024
    # 包内清单与成员仍双向一致(裁剪后 manifest 不列被丢文件)
    assert {f["name"] for f in man["files"]} == names - {"manifest.json"}


# ---------------- API ----------------


@pytest.fixture()
def api(tmp_path):
    env_home = make_home(tmp_path)
    from insar_agent.api.app import create_app

    app = create_app(home=env_home.home)
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, home=env_home.home)


def test_api_generate_and_download(api):
    resp = api.client.post("/api/diagnostics", json={})
    assert resp.status_code == 200
    out = resp.json()
    assert set(out) == {"path", "size", "download_url"}
    assert out["size"] > 0
    assert re.fullmatch(r"/api/diagnostics/file\?name=diag-[0-9TZ-]+\.zip",
                        out["download_url"])
    dl = api.client.get(out["download_url"])
    assert dl.status_code == 200
    assert dl.content[:2] == b"PK", "FileResponse 必须回真 zip"
    assert len(dl.content) == out["size"]
    with zipfile.ZipFile(__import__("io").BytesIO(dl.content)) as zf:
        assert json.loads(zf.read("manifest.json"))["run"]["run_id"] == "run-bad"

    # 空 body 同样可用(run_id 可选)
    assert api.client.post("/api/diagnostics").status_code == 200
    # 显式 run_id 不存在 → 404
    assert api.client.post("/api/diagnostics",
                           json={"run_id": "run-nope"}).status_code == 404


def test_api_filename_whitelist_rejects_traversal(api):
    bad_names = [
        "../../../insar.db",                     # 相对穿越
        "..%2F..%2Finsar.db",                    # 编码穿越(整串对不上白名单)
        "diag-..zip",                            # 点段变形
        "diag-20260101T000000Z.zip.txt",         # 后缀变形
        "diag-abc.zip",                          # 字符集外(字母)
        "evil-20260101T000000Z.zip",             # 前缀不对
        "diag-20260101T000000Z.ZIP",             # 大小写变形
        "",                                      # 空名
    ]
    for name in bad_names:
        resp = api.client.get("/api/diagnostics/file", params={"name": name})
        assert resp.status_code == 400, f"{name!r} 必须被白名单拒绝"
    # 形态合法但不存在 → 404(不泄露目录布局)
    resp = api.client.get("/api/diagnostics/file",
                          params={"name": "diag-19990101T000000Z.zip"})
    assert resp.status_code == 404
