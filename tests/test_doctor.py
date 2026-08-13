"""一键体检(insar_agent/doctor.py + /api/doctor)测试。

覆盖:
  - check_all 形状与六大类别覆盖;非 ok 项必有 fix_hint;
  - 故障注入:损坏 DB(fail)/孤儿 run + 未结算命令(warn)/工作区无写权限(fail)/
    依赖缺失(fail、可选 warn)/磁盘水位(warn/fail)/端口被占(warn);
  - 隔离性:单个检查器崩溃 → 该项 fail(检查器异常),其余检查照常产出;
  - CLI:退出码 0/1/2、--json 形状、NO_COLOR 降级与彩色路径;
  - API:响应形状、单飞防抖(并发只执行一份)、超时 504。

密封纪律:WSL 探测打桩(不起 wsl.exe)、端口用随机空闲高位端口(绝不碰 8873)、
INSAR_HOME 指向临时目录 —— 全部秒级,不运行任何真实计算。
"""

from __future__ import annotations

import json
import socket
import sqlite3
import threading
import time
from collections import namedtuple

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from insar_agent import doctor
from insar_agent.api.doctor_router import create_doctor_router
from insar_agent.core.db import Database

VALID_STATUS = {"ok", "warn", "fail"}
CATEGORIES = {"环境", "引擎", "数据库", "文件系统", "网络/端口", "WSL"}

GB = 1 << 30


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """密封体检环境:临时 INSAR_HOME + 随机空闲端口 + WSL 打桩(不起 wsl.exe)。"""
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("INSAR_HOME", str(h))
    monkeypatch.setenv("INSAR_PORT", str(_free_port()))
    monkeypatch.setenv("INSAR_ENGINE_PREFIX", "_doctor_test_")
    monkeypatch.delenv("INSAR_ENGINE_PREFIX")
    monkeypatch.setattr("insar_agent.runtime.wsl.wsl_status",
                        lambda runner=None: {"installed": False, "distros": []})
    return h


def _rows(results, name_part):
    return [r for r in results if name_part in r.name]


def _fake_results(*statuses):
    return [doctor.CheckResult(f"检查{i}", "环境", s, f"detail-{i}",
                               "" if s == "ok" else f"fix-{i}")
            for i, s in enumerate(statuses)]


# ---------------- check_all:形状与类别 ----------------

def test_check_all_shape_and_categories(home):
    results = doctor.check_all(home)
    assert results, "体检必须产出检查项"
    for r in results:
        assert isinstance(r, doctor.CheckResult)
        assert r.status in VALID_STATUS
        assert r.name and r.category
        if r.status != "ok":
            assert r.fix_hint, f"非 ok 项必须给处置建议:{r.name}"
    assert CATEGORIES <= {r.category for r in results}


def test_db_missing_is_warn_not_fail(home):
    results = doctor.check_all(home)
    (row,) = _rows(results, "数据库文件")
    assert row.status == "warn"  # 首次启动前属正常,不该吓唬用户
    assert "不存在" in row.detail


# ---------------- 故障注入:数据库 ----------------

def test_corrupt_db_marks_fail_without_crash(home):
    (home / "insar.db").write_bytes("这不是 SQLite 数据库".encode("utf-8") * 64)
    results = doctor.check_all(home)
    db_rows = [r for r in results if r.category == "数据库"]
    assert any(r.status == "fail" for r in db_rows), "损坏文件必须体检出 fail"
    # 不拖垮整体:其他类别照常产出
    assert CATEGORIES <= {r.category for r in results}


def test_orphan_run_and_stuck_commands_reported(home):
    db_path = home / "insar.db"
    Database(db_path).close()  # 用正式 schema 建库(体检自身绝不建库)
    now = time.time()
    conn = sqlite3.connect(db_path)  # 裸连接默认不启用外键,插桩数据不需要会话齐全
    conn.execute("INSERT INTO sessions(session_id,name,created_at) VALUES('s1','s1',?)",
                 (now - 7200,))
    # 孤儿:running 且无租约,创建已 2h
    conn.execute("INSERT INTO runs(run_id,session_id,created_at,status,workspace) "
                 "VALUES('r-orphan','s1',?,'running','ws')", (now - 7200,))
    # 活跃:running 且租约新鲜 → 不该被报告
    conn.execute("INSERT INTO runs(run_id,session_id,created_at,status,workspace) "
                 "VALUES('r-live','s1',?,'running','ws')", (now - 7200,))
    conn.execute("INSERT INTO leases(resource,holder,acquired,heartbeat,ttl) "
                 "VALUES('run:r-live','h',?,?,60)", (now, now))
    # 未结算命令:意图落盘 >24h 仍无 exit_code
    conn.execute("INSERT INTO commands(run_id,step_id,argv,exit_code,created_at) "
                 "VALUES('r-orphan',1,'[]',NULL,?)", (now - 90000,))
    conn.commit()
    conn.close()

    results = doctor.check_all(home)
    (orphan,) = _rows(results, "孤儿 run")
    assert orphan.status == "warn"
    assert "r-orphan" in orphan.detail and "r-live" not in orphan.detail
    (stuck,) = _rows(results, "未结算命令")
    assert stuck.status == "warn"
    assert "1 条" in stuck.detail
    # 完整性检查对健康库应为 ok
    (integ,) = _rows(results, "integrity_check")
    assert integ.status == "ok"


# ---------------- 故障注入:文件系统 ----------------

def test_unwritable_workspace_marks_fail(home, monkeypatch):
    def deny(*args, **kwargs):
        raise PermissionError("模拟 ACL 拒绝写入")

    monkeypatch.setattr(doctor.tempfile, "mkstemp", deny)
    results = doctor.check_all(home)
    (row,) = _rows(results, "工作区可写")
    assert row.status == "fail"
    assert "写入探针失败" in row.detail
    assert CATEGORIES <= {r.category for r in results}  # 单项 fail 不拖垮


def test_home_is_file_marks_fail(tmp_path, home):
    bogus = tmp_path / "not-a-dir.txt"
    bogus.write_text("x", encoding="utf-8")
    results = doctor.check_all(bogus)
    (row,) = _rows(results, "工作区可写")
    assert row.status == "fail"
    assert "不是目录" in row.detail


_Usage = namedtuple("usage", "total used free")


@pytest.mark.parametrize("free_gb,expected", [(5, "warn"), (1, "fail"), (50, "ok")])
def test_disk_watermarks(home, monkeypatch, free_gb, expected):
    monkeypatch.setattr(doctor.shutil, "disk_usage",
                        lambda p: _Usage(500 * GB, (500 - free_gb) * GB, free_gb * GB))
    results = doctor._check_filesystem(home)
    (row,) = _rows(results, "磁盘剩余")
    assert row.status == expected


# ---------------- 故障注入:依赖 ----------------

def test_missing_required_dependency_marks_fail(home, monkeypatch):
    monkeypatch.setattr(doctor, "_declared_requirements",
                        lambda: ([("绝不存在的发行包-xyz", "1.0")], []))
    results = doctor.check_all(home)
    (row,) = _rows(results, "绝不存在的发行包-xyz")
    assert row.status == "fail"
    assert "未安装" in row.detail
    assert CATEGORIES <= {r.category for r in results}


def test_missing_optional_dependency_is_warn(home, monkeypatch):
    monkeypatch.setattr(doctor, "_declared_requirements",
                        lambda: ([], [("绝不存在的可选包-xyz", "")]))
    (row,) = _rows(doctor._check_dependencies(home), "绝不存在的可选包-xyz")
    assert row.status == "warn"


def test_version_drift_is_warn(home, monkeypatch):
    # fastapi 真实已装:把下限抬到不可能的高版本,制造「低于 pyproject 下限」
    monkeypatch.setattr(doctor, "_declared_requirements",
                        lambda: ([("fastapi", "999.0")], []))
    (row,) = _rows(doctor._check_dependencies(home), "fastapi")
    assert row.status == "warn"
    assert "低于" in row.detail


# ---------------- 故障注入:端口 ----------------

def test_port_free_is_ok(home):
    (row,) = _rows(doctor._check_port(home), "配置端口")
    assert row.status == "ok"
    assert "空闲" in row.detail


def test_port_occupied_by_stranger_is_warn(home, monkeypatch):
    monkeypatch.setattr(doctor, "_HEALTH_TIMEOUT", 0.3)
    with socket.socket() as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)  # 只握手不回话:模拟「被非本项目进程占用」
        monkeypatch.setenv("INSAR_PORT", str(srv.getsockname()[1]))
        (row,) = _rows(doctor._check_port(home), "配置端口")
    assert row.status == "warn"
    assert "被其他进程占用" in row.detail
    assert "不杀进程" in row.fix_hint


def test_port_invalid_config_is_warn(home, monkeypatch):
    monkeypatch.setenv("INSAR_PORT", "not-a-port")
    (row,) = _rows(doctor._check_port(home), "配置端口")
    assert row.status == "warn"


# ---------------- 隔离性:检查器自身崩溃 ----------------

def test_crashing_checker_is_isolated(home, monkeypatch):
    def boom(_home):
        raise RuntimeError("检查器内部缺陷")

    monkeypatch.setattr(doctor, "_CHECKERS", doctor._CHECKERS + (("环境", boom),))
    results = doctor.check_all(home)
    (row,) = _rows(results, "boom(检查器异常)")
    assert row.status == "fail"
    assert "RuntimeError" in row.detail
    assert CATEGORIES <= {r.category for r in results}  # 其余检查器照常产出


# ---------------- CLI:退出码 / --json / NO_COLOR ----------------

def test_cli_exit_codes(home, monkeypatch, capsys):
    cases = [(("ok", "ok"), 0), (("ok", "warn"), 1), (("warn", "fail"), 2)]
    for statuses, expected in cases:
        monkeypatch.setattr(doctor, "check_all",
                            lambda home=None, s=statuses: _fake_results(*s))
        assert doctor._main([]) == expected, f"{statuses} 应退出 {expected}"
        capsys.readouterr()


def test_cli_json_shape(home, monkeypatch, capsys):
    monkeypatch.setattr(doctor, "check_all",
                        lambda home=None: _fake_results("ok", "warn"))
    rc = doctor._main(["--json"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert data["status"] == "warn"
    assert data["exit_code"] == 1
    assert data["counts"] == {"ok": 1, "warn": 1, "fail": 0}
    assert {"name", "category", "status", "detail", "fix_hint"} == set(data["results"][0])


def test_cli_json_end_to_end(home, capsys):
    """不打桩的全真 CLI:JSON 可解析、退出码与汇总一致、六大类别齐全。"""
    rc = doctor._main(["--json", "--home", str(home)])
    data = json.loads(capsys.readouterr().out)
    assert rc == data["exit_code"] == {"ok": 0, "warn": 1, "fail": 2}[data["status"]]
    assert CATEGORIES <= {r["category"] for r in data["results"]}


def test_cli_no_color_strips_ansi(home, monkeypatch, capsys):
    monkeypatch.setattr(doctor, "check_all",
                        lambda home=None: _fake_results("ok", "warn", "fail"))
    monkeypatch.setenv("NO_COLOR", "1")
    doctor._main([])
    out = capsys.readouterr().out
    assert "\x1b[" not in out
    assert "WARN" in out and "FAIL" in out  # 降级后语义仍靠文字表达
    assert "处置" in out                     # fix_hint 照常展示


def test_cli_color_path_emits_ansi(home, monkeypatch, capsys):
    monkeypatch.setattr(doctor, "check_all", lambda home=None: _fake_results("fail"))
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr(doctor, "_use_color", lambda: True)  # capsys 非 tty,强制彩色路径
    doctor._main([])
    assert "\x1b[31m" in capsys.readouterr().out  # fail 红色


# ---------------- API:形状 / 单飞防抖 / 超时 ----------------

def test_api_shape(home, monkeypatch):
    monkeypatch.setattr("insar_agent.api.doctor_router.check_all",
                        lambda home=None: _fake_results("ok", "warn"))
    app = FastAPI()
    app.include_router(create_doctor_router(home))
    with TestClient(app) as c:
        r = c.get("/api/doctor")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "warn"
    assert body["counts"] == {"ok": 1, "warn": 1, "fail": 0}
    assert isinstance(body["took_ms"], int)
    assert {"name", "category", "status", "detail", "fix_hint"} == set(body["results"][0])


def test_api_single_flight(home, monkeypatch):
    """并发防抖:两请求同时到达只执行一份体检,后到的等同一份结果。"""
    calls: list[int] = []
    started, release = threading.Event(), threading.Event()

    def gated(home=None):
        calls.append(1)
        started.set()
        release.wait(10)
        return _fake_results("ok")

    monkeypatch.setattr("insar_agent.api.doctor_router.check_all", gated)
    app = FastAPI()
    app.include_router(create_doctor_router(home, timeout=15.0))
    responses: dict[str, object] = {}

    def hit(key):
        with TestClient(app) as c:
            responses[key] = c.get("/api/doctor")

    t1 = threading.Thread(target=hit, args=("a",))
    t1.start()
    assert started.wait(5), "第一份体检应已开跑"
    t2 = threading.Thread(target=hit, args=("b",))
    t2.start()
    time.sleep(0.3)  # 宽松窗口:让第二个请求进入等待(它绝不该触发第二次执行)
    release.set()
    t1.join(10)
    t2.join(10)
    assert len(calls) == 1, "同时只允许跑一份体检"
    assert responses["a"].status_code == responses["b"].status_code == 200
    assert responses["a"].json()["results"] == responses["b"].json()["results"]


def test_api_timeout_504_then_recovers(home, monkeypatch):
    release = threading.Event()

    def slow(home=None):
        release.wait(5)
        return _fake_results("ok")

    monkeypatch.setattr("insar_agent.api.doctor_router.check_all", slow)
    app = FastAPI()
    app.include_router(create_doctor_router(home, timeout=0.2))
    with TestClient(app) as c:
        r = c.get("/api/doctor")
        assert r.status_code == 504
        assert "体检超时" in r.json()["detail"]
        release.set()
        time.sleep(0.5)  # 等后台那份体检收尾(超时不取消执行,结果仍可复用)
        r2 = c.get("/api/doctor")
        assert r2.status_code == 200
        assert r2.json()["status"] == "ok"
