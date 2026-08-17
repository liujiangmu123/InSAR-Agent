"""第三轮 API 面模糊测试与安全复查(今日新增面:/api/llm/* · /api/queue/* ·
/api/doctor · /api/diagnostics*)。

纪律(硬约束):
  - 全部 TestClient / 进程内;LLM 相关一律 monkeypatch,绝不真实出网、绝不用真实密钥;
  - 网络硬断(autouse 拦 urllib.urlopen);引擎/WSL 探测密封;绝不跑真实计算;
  - 端口检查一律指向随机高位端口,绝不触碰 8873。

重点面:
  1. 密钥不泄漏(最高优先):config/models/test 任何路径(含错误分支)不回显全 key;
     provider 错误串不含 key;诊断包/静态路由都取不到 workspace/llm.json 与全 key。
  2. base_url SSRF/LFI:provider 走 urllib(实测 file:// 可读本地文件)—— 本轮加固
     scheme 白名单 http/https(见 llm_router._require_safe_base_url),内网 IP 属残留。
  3. 队列:存在性不泄漏、幂等去重、running 的 409、step_ids 校验、并发入队去重不变量。
  4. 诊断:run_id 注入、下载名白名单绕过、并发生成、超额裁剪。
  5. doctor:检查器崩溃隔离、并发单飞、超时与恢复。
  6. llm.json 不经任意静态路径可达。
"""

from __future__ import annotations

import json
import socket
import threading
import time
import zipfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from conftest import TIME_FACTOR
from insar_agent import doctor as doc
from insar_agent.api.app import create_app
from insar_agent.api.doctor_router import create_doctor_router
from insar_agent.brain.provider import BrainUnavailable
from insar_agent.core.db import Database
from insar_agent.core.store import Store
from insar_agent.report.diagbundle import build_diag_bundle

#: 测试专用假密钥(绝不是真实密钥):任何响应/日志/诊断包出现它即判泄漏
LLM_KEY = "sk-r3-SECRET-DO-NOT-LEAK-abcdef0123456789"


def _raise(exc):
    raise exc


def _free_port() -> int:
    """取一个空闲高位端口(绑定 0 让内核分配后立即释放),供 doctor 端口检查指向。"""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    """密封 + 断网:所有用例默认不碰真实引擎/WSL/网络。"""
    from insar_agent.runtime.probe import ProbeResult

    def empty_probe(*args, **kwargs):
        return ProbeResult(
            engines={k: None for k in ("isce2", "mintpy", "snaphu", "gdal", "snap",
                                       "pystamps", "pyaps")},
            credentials={"earthdata": False, "cds": False, "gacos": False},
            disk_free_gb=100.0, cpu_count=8)

    monkeypatch.setattr("insar_agent.loop.driver.probe_environment", empty_probe)
    monkeypatch.setattr("insar_agent.runtime.wsl_probe.probe_wsl_engines",
                        lambda **kw: {"ok": False})
    # 诊断包内 env.json 的探测同样密封(与 driver 侧不同引用,需单独打桩)
    monkeypatch.setattr("insar_agent.report.diagbundle.probe_environment", empty_probe)
    monkeypatch.setattr("insar_agent.report.diagbundle._implicit_engine_prefix",
                        lambda: None)

    def _no_net(*args, **kwargs):
        raise AssertionError("测试禁止真实出网:urllib.urlopen 被 autouse 拦截")

    # LLM/端口相关的出网一律走高层 monkeypatch;真到这一层即测试构造有误
    monkeypatch.setattr("urllib.request.urlopen", _no_net)


@pytest.fixture()
def home(tmp_path) -> Path:
    return tmp_path / "home"


@pytest.fixture()
def client(home, _hermetic):
    app = create_app(home=home)
    with TestClient(app) as c:
        yield c


def _mk_owned_run(home: Path, session: str, run_id: str) -> None:
    """在 home/insar.db 直接落一个归属 session 的 run(不跑计算,仅供队列/诊断解析)。

    与既有测试同法:TestClient 存活期间另开一把 Database 连同一库(WAL,幂等迁移)。
    """
    db = Database(home / "insar.db")
    st = Store(db)
    try:
        st.create_session(session, session)
        st.create_run(run_id, session, workspace=str(home / "sessions" / session))
    finally:
        db.close()


# ==================== 1. LLM 密钥不泄漏(最高优先) ====================


def test_llm_config_get_masks_and_survives_corrupt(client, home):
    """GET /config:空配置回空掩码;存 key 后只回掩码;llm.json 损坏不 500 且不回显残留。"""
    b = client.get("/api/llm/config").json()
    assert b["api_key_masked"] == "" and b["source"] == "none"

    r = client.post("/api/llm/config", json={
        "base_url": "https://api.example/v1", "api_key": LLM_KEY, "chat_model": "m"})
    assert r.status_code == 200
    assert LLM_KEY not in r.text                 # POST 回执也不得回显全 key
    view = client.get("/api/llm/config")
    assert LLM_KEY not in view.text
    assert "…" in view.json()["api_key_masked"]  # 掩码形态(前 8 + … + 末 4)

    (home / "llm.json").write_text("{not valid json", encoding="utf-8")
    r3 = client.get("/api/llm/config")
    assert r3.status_code == 200 and LLM_KEY not in r3.text


def test_llm_config_post_type_fuzz_never_500(client):
    """POST /config 非字符串/嵌套对象/None:一律 422(或 None 放行),绝不 500。"""
    for body in ({"base_url": 123}, {"api_key": ["x"]}, {"chat_model": {"a": 1}},
                 {"vision_model": 3.14}, {"base_url": [1, 2, 3]}):
        r = client.post("/api/llm/config", json=body)
        assert r.status_code == 422, (body, r.status_code)
        assert "detail" in r.json()
    assert client.post("/api/llm/config",
                       json={"base_url": None, "api_key": None}).status_code == 200
    # 超长(但合法 scheme)base_url 可保存,不 500
    long_url = "https://" + ("a" * 5000) + ".example/v1"
    assert client.post("/api/llm/config", json={"base_url": long_url}).status_code == 200


def test_llm_models_and_test_errors_have_no_key(client, monkeypatch):
    """/models 与 /test 的错误分支绝不回显密钥(供应商错误信息也不例外)。"""
    client.post("/api/llm/config", json={
        "base_url": "https://api.example/v1", "api_key": LLM_KEY,
        "chat_model": "m", "vision_model": "v"})

    monkeypatch.setattr(
        "insar_agent.api.llm_router.list_models",
        lambda *a, **k: _raise(BrainUnavailable("模型列表请求被拒(HTTP 401):检查地址与密钥")))
    rm = client.post("/api/llm/models", json={})
    assert rm.status_code == 200 and rm.json()["ok"] is False
    assert LLM_KEY not in rm.text

    monkeypatch.setattr(
        "insar_agent.brain.provider.LLMProvider.complete_json",
        lambda self, **kw: _raise(BrainUnavailable("全部路由失败:<urlopen error timed out>")))
    rt = client.post("/api/llm/test", json={"kind": "chat"})
    assert rt.status_code == 200 and rt.json()["ok"] is False
    assert LLM_KEY not in rt.text

    monkeypatch.setattr(
        "insar_agent.api.llm_router.describe_image_stream",
        lambda *a, **k: _raise(BrainUnavailable("识图请求被拒(HTTP 400):检查模型是否支持图片输入")))
    rv = client.post("/api/llm/test", json={"kind": "vision"})
    assert rv.status_code == 200 and rv.json()["ok"] is False
    assert LLM_KEY not in rv.text


def test_llm_test_success_path_has_no_key(client, monkeypatch):
    """/test 成功分支只回 model/reply,绝不含密钥。"""
    client.post("/api/llm/config", json={
        "base_url": "https://api.example/v1", "api_key": LLM_KEY, "chat_model": "m"})
    monkeypatch.setattr("insar_agent.brain.provider.LLMProvider.complete_json",
                        lambda self, **kw: {"ok": True})
    r = client.post("/api/llm/test", json={"kind": "chat"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert LLM_KEY not in r.text


def test_provider_errors_never_contain_key(monkeypatch):
    """provider 层(密钥只在 Authorization 头)错误串绝不回显 key —— 进程内直证,不出网。"""
    import urllib.error

    from insar_agent.brain import provider

    monkeypatch.setattr(provider.urllib.request, "urlopen",
                        lambda *a, **k: _raise(urllib.error.URLError("Connection refused")))
    with pytest.raises(provider.BrainUnavailable) as e1:
        provider.list_models("https://api.example/v1", LLM_KEY)
    assert LLM_KEY not in str(e1.value)

    prov = provider.LLMProvider([provider.LLMRoute("https://api.example/v1", LLM_KEY, "m")])
    with pytest.raises(provider.BrainUnavailable) as e2:
        prov.complete_json(system="s", user="u")
    assert LLM_KEY not in str(e2.value)


# ==================== 2. base_url SSRF/LFI 与畸形输入 ====================


def test_llm_base_url_ssrf_schemes_rejected_before_egress(client, monkeypatch):
    """危险 scheme(file/javascript/ftp/gopher)在 /config 与 /models 都 400,且绝不出网。"""
    called: list[str] = []
    monkeypatch.setattr("insar_agent.api.llm_router.list_models",
                        lambda base, key, **k: called.append(base) or [])

    for bad in ("file:///C:/Windows/win.ini", "file:///etc/passwd",
                "javascript:alert(1)", "ftp://x/y", "gopher://x:70/_probe"):
        rc = client.post("/api/llm/config",
                         json={"base_url": bad, "api_key": LLM_KEY, "chat_model": "m"})
        assert rc.status_code == 400, (bad, rc.status_code)
        rm = client.post("/api/llm/models", json={"base_url": bad, "api_key": LLM_KEY})
        assert rm.status_code == 400, (bad, rm.status_code)
    assert called == []  # 全程未触发任何出网调用

    # scheme 白名单只挡协议:内网/元数据 IP 仍走 http(残留,已在审计声明并说明取舍)
    ok = client.post("/api/llm/models",
                     json={"base_url": "http://169.254.169.254/v1", "api_key": LLM_KEY})
    assert ok.status_code == 200 and ok.json()["ok"] is True
    assert called and called[0].startswith("http://169.254.169.254")


def test_llm_config_concurrent_writes_atomic_no_tmp_residue(client, home):
    """并发写 /config 的安全/一致性不变量:llm.json 始终是合法 JSON、api_key 不丢、
    无 *.tmp 残留。

    可用性备注(P2,见审计):Windows 上并发 os.replace(atomic_write_text)争用
    同一目标会个别抛 WinError 5 → 该次请求 500;安全属性(密钥不泄漏、原子落位、
    不残留半截)不受影响,故这里记录 500 但不判失败。
    """
    client.post("/api/llm/config", json={
        "base_url": "https://api.example/v1", "api_key": LLM_KEY, "chat_model": "m0"})

    barrier = threading.Barrier(8)
    outcomes: list = []
    lock = threading.Lock()

    def hit(i: int):
        barrier.wait()
        try:
            code = client.post("/api/llm/config", json={"chat_model": f"m{i}"}).status_code
        except Exception as exc:  # noqa: BLE001 —— WinError5 争用会经 TestClient 重抛,记录不外泄
            code = type(exc).__name__
        with lock:
            outcomes.append(code)

    threads = [threading.Thread(target=hit, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not any(t.is_alive() for t in threads)

    cfg = json.loads((home / "llm.json").read_text(encoding="utf-8"))
    assert cfg["api_key"] == LLM_KEY          # 合并写:并发下 api_key 不被抹掉
    assert not list(home.glob("llm.json*.tmp"))  # 原子写:无临时文件残留(失败分支也清理)
    assert LLM_KEY not in client.get("/api/llm/config").text
    assert outcomes and all(o in (200, "PermissionError", "OSError") for o in outcomes)


# ==================== 3. 队列面 ====================


def test_queue_unknown_and_cross_session_indistinguishable_404(client, home):
    """不存在的 run 与「别人会话的 run」入队/取消一律 404,状态码不可区分(不泄露存在性)。"""
    client.post("/api/sessions", json={"id": "alice"})
    client.post("/api/sessions", json={"id": "bob"})
    _mk_owned_run(home, "alice", "r-alice")

    cross = client.post("/api/queue", json={"session": "bob", "run_id": "r-alice"})
    ghost = client.post("/api/queue", json={"session": "bob", "run_id": "ghost-run"})
    assert cross.status_code == 404 and ghost.status_code == 404
    assert cross.status_code == ghost.status_code  # 存在与否同码,防枚举
    assert client.delete("/api/queue/r-alice", params={"session": "bob"}).status_code == 404
    assert client.delete("/api/queue/ghost-run", params={"session": "bob"}).status_code == 404


def test_queue_enqueue_idempotent(client, home):
    """同 run 重复入队:第二次 created=False 且位置不变(去重)。"""
    client.post("/api/sessions", json={"id": "demo"})
    _mk_owned_run(home, "demo", "r-dedup")
    r1 = client.post("/api/queue", json={"session": "demo", "run_id": "r-dedup"})
    r2 = client.post("/api/queue", json={"session": "demo", "run_id": "r-dedup"})
    assert r1.status_code == 202 and r1.json()["created"] is True
    assert r2.status_code == 202 and r2.json()["created"] is False
    assert r2.json()["position"] == r1.json()["position"]


def test_queue_delete_state_matrix(client, home):
    """DELETE:pending→200 取消;running→409;已不在活跃队列→404。"""
    from insar_agent.loop.queue import RunQueue

    client.post("/api/sessions", json={"id": "demo"})
    _mk_owned_run(home, "demo", "r-run")
    _mk_owned_run(home, "demo", "r-pend")
    client.post("/api/queue", json={"session": "demo", "run_id": "r-run"})
    client.post("/api/queue", json={"session": "demo", "run_id": "r-pend"})

    db = Database(home / "insar.db")
    st = Store(db)
    try:  # 把 r-run 推进到 running(取消要走 KILL,不能撤排队)
        rid = st.db.query_one("SELECT id FROM run_queue WHERE run_id=?", ("r-run",))["id"]
        assert RunQueue(st).mark_running(int(rid))
    finally:
        db.close()

    assert client.delete("/api/queue/r-run", params={"session": "demo"}).status_code == 409
    ok = client.delete("/api/queue/r-pend", params={"session": "demo"})
    assert ok.status_code == 200 and ok.json()["cancelled"] is True
    # 再删已取消的(不在活跃队列)→ 404
    assert client.delete("/api/queue/r-pend", params={"session": "demo"}).status_code == 404


def test_queue_step_ids_validation(client, home):
    """step_ids:越界值 400(挡在入口);类型不对 422(pydantic)。"""
    client.post("/api/sessions", json={"id": "demo"})
    _mk_owned_run(home, "demo", "r-steps")  # 该 run 无任何步骤 → 任意 step_id 皆越界
    assert client.post("/api/queue", json={
        "session": "demo", "run_id": "r-steps", "step_ids": [999]}).status_code == 400
    assert client.post("/api/queue", json={
        "session": "demo", "run_id": "r-steps", "step_ids": [-1]}).status_code == 400
    assert client.post("/api/queue", json={
        "session": "demo", "run_id": "r-steps", "step_ids": "7"}).status_code == 422
    assert client.post("/api/queue", json={
        "session": "demo", "run_id": "r-steps", "step_ids": [{"x": 1}]}).status_code == 422


def test_queue_concurrent_enqueue_dedup_invariant(client, home):
    """并发入队同一 run:恰好一次真正入队(created=True),库内活跃条目恒为 1。"""
    client.post("/api/sessions", json={"id": "demo"})
    _mk_owned_run(home, "demo", "r-conc")

    n = 8
    barrier = threading.Barrier(n)
    results: list = []
    lock = threading.Lock()

    def hit():
        barrier.wait()
        r = client.post("/api/queue", json={"session": "demo", "run_id": "r-conc"})
        with lock:
            results.append(r)

    threads = [threading.Thread(target=hit) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not any(t.is_alive() for t in threads)
    assert all(r.status_code == 202 for r in results)
    assert sum(r.json()["created"] is True for r in results) == 1  # 去重不变量

    db = Database(home / "insar.db")
    st = Store(db)
    try:
        row = st.db.query_one(
            "SELECT COUNT(*) AS n FROM run_queue WHERE run_id=?"
            " AND state IN ('pending','running')", ("r-conc",))
    finally:
        db.close()
    assert row["n"] == 1


def test_queue_get_snapshot_and_session_filter(client, home):
    """GET /queue:含 1-based position 升序;会话过滤命中/落空正确。"""
    client.post("/api/sessions", json={"id": "demo"})
    _mk_owned_run(home, "demo", "r-a")
    _mk_owned_run(home, "demo", "r-b")
    client.post("/api/queue", json={"session": "demo", "run_id": "r-a"})
    client.post("/api/queue", json={"session": "demo", "run_id": "r-b"})

    items = client.get("/api/queue").json()["items"]
    ids = {i["run_id"] for i in items}
    assert {"r-a", "r-b"} <= ids
    positions = [i["position"] for i in items]
    assert positions == sorted(positions)
    assert client.get("/api/queue", params={"session": "no-such"}).json()["items"] == []


# ==================== 4. 诊断包面 ====================


def test_diagnostics_run_id_injection_404(client, home):
    """run_id 是 DB 主键不是路径:穿越/绝对路径一律 404,不落越权文件。"""
    for evil in ("../../etc/passwd", "..\\..\\secret", "C:\\Windows\\win.ini",
                 "a/../../b", "....//....//x"):
        r = client.post("/api/diagnostics", json={"run_id": evil})
        assert r.status_code == 404, (evil, r.status_code)
    assert not (home.parent / "passwd").exists()
    assert not (home.parent / "secret").exists()


def test_diagnostics_download_name_whitelist(client, home):
    """下载端点只认 ^diag-[0-9TZ-]+\\.zip$:各类绕过一律 400/404,合法名 200。"""
    gen = client.post("/api/diagnostics", json={})
    assert gen.status_code == 200
    real = Path(gen.json()["path"]).name
    ok = client.get("/api/diagnostics/file", params={"name": real})
    assert ok.status_code == 200 and ok.headers["content-type"].startswith("application/zip")

    for name in ("../../etc/passwd", "..\\..\\win.ini", "C:\\Windows\\win.ini",
                 "diag-2026.zip/../../x", "..%2f..%2fetc",
                 "DIAG-20260101T000000Z.zip",       # 大小写
                 "diag-20260101T000000Z.zip.exe",   # 尾缀伪造
                 "diag-<script>.zip", "diag- .zip", "diag-;rm.zip",
                 "diag-....zip", "evil.zip", ""):
        r = client.get("/api/diagnostics/file", params={"name": name})
        assert r.status_code in (400, 404), (name, r.status_code)
    # 白名单形态但不存在 → 404(不泄露磁盘路径)
    miss = client.get("/api/diagnostics/file",
                      params={"name": "diag-20990101T000000Z.zip"})
    assert miss.status_code == 404


def test_diagnostics_bundle_excludes_llm_key(client, home):
    """诊断包绝不打包 workspace/llm.json,也绝不出现完整密钥(端到端护栏)。"""
    client.post("/api/llm/config", json={
        "base_url": "https://api.example/v1", "api_key": LLM_KEY, "chat_model": "m"})
    assert (home / "llm.json").is_file()

    gen = client.post("/api/diagnostics", json={})
    assert gen.status_code == 200
    with zipfile.ZipFile(gen.json()["path"]) as zf:
        names = zf.namelist()
        blob = b"".join(zf.read(n) for n in names)
    assert not any("llm.json" in n for n in names)
    assert LLM_KEY.encode() not in blob


def test_diagbundle_trim_keeps_core_and_no_key(tmp_path, monkeypatch):
    """超额裁剪(小额度模拟 50MB 触发):核心三份永在,裁剪如实记账,密钥不入包。"""
    from insar_agent.runtime.probe import ProbeResult
    monkeypatch.setattr(
        "insar_agent.report.diagbundle.probe_environment",
        lambda *a, **k: ProbeResult(engines={}, credentials={}, disk_free_gb=1.0, cpu_count=1))
    monkeypatch.setattr("insar_agent.report.diagbundle._implicit_engine_prefix", lambda: None)

    home = tmp_path / "home"
    home.mkdir()
    (home / "llm.json").write_text(
        '{"base_url":"https://x/v1","api_key":"%s","chat_model":"m"}' % LLM_KEY,
        encoding="utf-8")
    db = Database(home / "insar.db")
    st = Store(db)
    st.create_session("demo", "demo")
    ws = home / "sessions" / "demo"
    ws.mkdir(parents=True)
    st.create_run("r-big", "demo", workspace=str(ws))
    db.close()
    logdir = ws / ".jobs" / "r-big" / "s06" / "a1"
    logdir.mkdir(parents=True)
    (logdir / "job.log").write_text("Z" * (400 * 1024), encoding="utf-8")

    z = build_diag_bundle(home, "r-big", out_dir=home / "diagnostics",
                          max_total_bytes=100 * 1024)
    with zipfile.ZipFile(z) as zf:
        names = set(zf.namelist())
        manifest = json.loads(zf.read("manifest.json"))
        blob = b"".join(zf.read(n) for n in zf.namelist())
    assert {"manifest.json", "db_summary.json", "env.json"} <= names  # 核心三份永不丢
    assert manifest["size_budget"]["trim_steps"]                      # 触发了裁剪
    assert LLM_KEY.encode() not in blob


def test_diagbundle_masks_secret_env_vars(tmp_path, monkeypatch):
    """诊断包 env.json:形似密钥的 INSAR_* 环境变量只报长度,绝不落原值。"""
    from insar_agent.runtime.probe import ProbeResult
    monkeypatch.setattr(
        "insar_agent.report.diagbundle.probe_environment",
        lambda *a, **k: ProbeResult(engines={}, credentials={}, disk_free_gb=1.0, cpu_count=1))
    monkeypatch.setattr("insar_agent.report.diagbundle._implicit_engine_prefix", lambda: None)
    monkeypatch.setenv("INSAR_LLM_API_KEY", LLM_KEY)

    home = tmp_path / "home"
    home.mkdir()
    z = build_diag_bundle(home, None, out_dir=home / "diagnostics")
    with zipfile.ZipFile(z) as zf:
        env = json.loads(zf.read("env.json"))
        blob = b"".join(zf.read(n) for n in zf.namelist())
    assert LLM_KEY.encode() not in blob
    assert env["env_vars"]["INSAR_LLM_API_KEY"].startswith("<已隐藏")


def test_diagnostics_concurrent_generation_safe(client, home):
    """并发生成:安全边界恒成立(zip 恒落在 diagnostics/ 内、合法);
    可用性上并发命名竞态可能致 500(P2,见审计),此处容忍不判失败。"""
    n = 6
    barrier = threading.Barrier(n)
    res: list = []
    lock = threading.Lock()

    def hit():
        barrier.wait()
        r = client.post("/api/diagnostics", json={})
        with lock:
            res.append(r)

    threads = [threading.Thread(target=hit) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not any(t.is_alive() for t in threads)

    out_dir = (home / "diagnostics").resolve()
    for r in res:
        assert r.status_code in (200, 500), r.text
        if r.status_code == 200:
            p = Path(r.json()["path"]).resolve()
            assert p.is_file() and zipfile.is_zipfile(p)
            assert p.parent == out_dir          # 绝不落到诊断目录之外
    assert sum(r.status_code == 200 for r in res) >= 1


# ==================== 5. doctor(一键体检) ====================


def test_doctor_checker_crash_isolated(tmp_path):
    """单个检查器崩溃被隔离为该项 fail(带异常信息),其余检查器照常出结果。"""
    def raiser(_home):
        raise RuntimeError("boom-xyz-42")

    def oker(_home):
        return [doc.CheckResult("正常项", "测试", "ok", "fine")]

    app = FastAPI()
    with _patched_checkers((("测试", raiser), ("测试", oker))):
        app.include_router(create_doctor_router(tmp_path))
        with TestClient(app) as c:
            r = c.get("/api/doctor")
    assert r.status_code == 200
    body = r.json()
    assert any(x["status"] == "fail" and "boom-xyz-42" in x["detail"]
               for x in body["results"])
    assert any(x["name"] == "正常项" and x["status"] == "ok" for x in body["results"])
    assert body["status"] == "fail"


def test_doctor_endpoint_smoke(client, monkeypatch):
    """真实 /api/doctor 冒烟:返回体形状正确;端口检查指向随机高位端口(绝不碰 8873)。"""
    monkeypatch.setenv("INSAR_PORT", str(_free_port()))
    r = client.get("/api/doctor")
    assert r.status_code == 200
    body = r.json()
    assert {"status", "exit_code", "counts", "results"} <= set(body)
    assert "took_ms" in body


@pytest.mark.timing
def test_doctor_single_flight_shares_one_run(tmp_path):
    """并发单飞:N 个并发请求只跑一份体检,且都拿到同一份结果。"""
    calls: list = []
    started = threading.Event()
    release = threading.Event()

    def slow(_home):
        calls.append(1)
        started.set()
        release.wait(10 * TIME_FACTOR)
        return [doc.CheckResult("慢检", "测试", "ok", "done")]

    app = FastAPI()
    codes: list = []
    bodies: list = []
    lock = threading.Lock()
    with _patched_checkers((("测试", slow),)):
        app.include_router(create_doctor_router(tmp_path, timeout=30 * TIME_FACTOR))
        with TestClient(app) as c:
            def hit():
                r = c.get("/api/doctor")
                with lock:
                    codes.append(r.status_code)
                    bodies.append(r.json())

            threads = [threading.Thread(target=hit) for _ in range(6)]
            for t in threads:
                t.start()
            assert started.wait(10 * TIME_FACTOR), "首个体检应立即开跑"
            time.sleep(0.4 * TIME_FACTOR)  # 判定窗:让其余请求挂上同一 future
            release.set()
            for t in threads:
                t.join(timeout=30 * TIME_FACTOR)
    assert codes and all(x == 200 for x in codes)
    assert len(calls) == 1                       # 单飞:只跑一份
    assert all(b == bodies[0] for b in bodies)   # 全部复用同一结果


@pytest.mark.timing
def test_doctor_timeout_then_recovers(tmp_path):
    """体检卡死 → 504 不占死连接;后台收尾后,后续请求恢复正常 200。"""
    gate = threading.Event()

    def blocker(_home):
        gate.wait(30 * TIME_FACTOR)
        return [doc.CheckResult("闸门", "测试", "ok", "released")]

    app = FastAPI()
    with _patched_checkers((("测试", blocker),)):
        app.include_router(create_doctor_router(tmp_path, timeout=0.3 * TIME_FACTOR))
        with TestClient(app) as c:
            assert c.get("/api/doctor").status_code == 504
            gate.set()  # 后台线程收尾
            deadline = time.time() + 10 * TIME_FACTOR
            last = None
            while time.time() < deadline:
                last = c.get("/api/doctor")
                if last.status_code == 200:
                    break
                time.sleep(0.1)
    assert last is not None and last.status_code == 200


class _patched_checkers:
    """上下文管理器:临时替换 doctor._CHECKERS(不依赖 monkeypatch 的作用域)。"""

    def __init__(self, checkers):
        self.checkers = tuple(checkers)
        self._saved = None

    def __enter__(self):
        self._saved = doc._CHECKERS
        doc._CHECKERS = self.checkers
        return self

    def __exit__(self, *exc):
        doc._CHECKERS = self._saved
        return False


# ==================== 6. 密钥文件不可经静态路径取出 ====================


def test_llm_json_not_reachable_via_static_route(client, home):
    """workspace/llm.json 绝不经任意路径可达。"""
    client.post("/api/llm/config", json={
        "base_url": "https://api.example/v1", "api_key": LLM_KEY, "chat_model": "m"})
    assert (home / "llm.json").is_file()
    for path in ("/llm.json", "/../llm.json", "/%2e%2e/llm.json", "/sessions/../llm.json"):
        r = client.get(path)
        # 任何静态路径都取不到它:要么 404,要么至少不含完整密钥
        assert r.status_code != 200 or LLM_KEY not in r.text
    assert client.get("/llm.json").status_code == 404
