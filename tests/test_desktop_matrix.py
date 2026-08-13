r"""桌面冻结包功能完整性矩阵(自动化部分;全景与手工清单见 docs/DESKTOP-PARITY.md)。

对象是 PyInstaller 冻结的 insar-backend.exe(desktop/backend-bundle/dist)本体,
目标是给「只用桌面版就能完成全部功能」提供可重复执行的证明:对照源码运行
逐面核验 —— API 面逐 router 探活、静态 UI 资源逐个比对、安全响应头、LLM 配置
读写落盘、模拟 run 全链冒烟、版本与技能数量与源码零差距(OpenAPI 全面对齐)。

与 tests/test_frozen_probe.py 的分工:那边验「冻结形态下环境探测行为与源码
一致」,这里验「冻结形态下全部功能面与源码零差距」。

纪律(硬约束):
  - 产物不存在(CI/未构建)整文件跳过;一键入口:python scripts/check_desktop.py
    (dist 缺失会先跑 desktop/backend-bundle/build_backend.ps1);
  - 冻结 exe 起在随机高位端口 + 一次性临时 INSAR_HOME,模块结束杀净
    (含按可执行路径过滤的残留清扫),绝不碰默认端口 8873 的生产实例;
  - 环境密封,绝不触发真实 InSAR 计算/云端提交:INSAR_ENGINE_PREFIX 指向
    空目录(压住 probe 的已知安装位隐式回退)+ 最小 PATH(压住 PATH 探测)
    + 家目录环境变量全部重定向到临时目录(压住 ~/.netrc 等凭据探测)——
    与 journey 测试的空探测打桩同语义,规划必然落到 simulated 方法;
  - 已确认的冻结缺陷用 strict xfail 记录(修复后 XPASS 提醒改回普通断言),
    修复不在本文件职责内 —— 缺陷细节与分派见 docs/DESKTOP-PARITY.md。

标记:desktop + slow(pyproject 注册)。常规 CI 无 dist 产物,按 skipif 跳过。
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

httpx = pytest.importorskip("httpx", reason="矩阵走真实 HTTP,需要 dev 依赖 httpx")

_REPO = Path(__file__).resolve().parents[1]
_DIST = _REPO / "desktop" / "backend-bundle" / "dist" / "insar-backend"
_EXE = _DIST / "insar-backend.exe"

pytestmark = [
    pytest.mark.desktop,
    pytest.mark.slow,
    pytest.mark.skipif(sys.platform != "win32", reason="冻结产物仅 Windows"),
    pytest.mark.skipif(not _EXE.exists(),
                       reason="冻结产物不存在(python scripts/check_desktop.py 会先构建);"
                              "CI 无产物按设计跳过"),
]

_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

#: 探活用会话 id(不存在的会话;个别 GET 端点经 driver_of 惰性建会话,无碍临时 HOME)
_S = "matrix-probe"
#: 模拟 run 冒烟用会话 id
SID = "matrix-quake"
#: run 的终态闭集(轮询停止条件,与 store 状态机一致)
_TERMINAL = {"done", "failed", "interrupted"}
#: 跨测试传递的旅程状态(module 内定义序执行,同 journey 测试的 ctx 约定)
_CTX: dict = {}


# ---------------------------------------------------------------------------
# 密封环境与进程管理
# ---------------------------------------------------------------------------

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _hermetic_env(tmp: Path, port: int) -> dict[str, str]:
    r"""构造冻结 exe 的密封环境:引擎/凭据/LLM 探测必然全空。

    三道闸(缺一不可,详见模块头「纪律」):
      1. INSAR_ENGINE_PREFIX=空目录 —— probe 对显式前缀只查子路径存在性,
         全空;同时压住未配置时对 E:\miniforge3 等已知安装位的隐式扫描;
      2. 最小 PATH(System32 保底 wsl.exe 等系统件)—— shutil.which 探测全空;
      3. 家目录/凭据环境变量重定向或删除 —— ~/.netrc、EARTHDATA_TOKEN 等全空。
    """
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("INSAR_", "EARTHDATA", "CDSAPI", "GACOS"))}
    profile = tmp / "profile"
    (profile / "AppData" / "Local").mkdir(parents=True, exist_ok=True)
    (profile / "AppData" / "Roaming").mkdir(parents=True, exist_ok=True)
    tmpdir = tmp / "tmpdir"
    tmpdir.mkdir(exist_ok=True)
    no_engines = tmp / "no-engines"
    no_engines.mkdir(exist_ok=True)
    env.update({
        "USERPROFILE": str(profile),
        "HOMEDRIVE": profile.drive,
        "HOMEPATH": str(profile)[len(profile.drive):],
        "LOCALAPPDATA": str(profile / "AppData" / "Local"),
        "APPDATA": str(profile / "AppData" / "Roaming"),
        "TEMP": str(tmpdir),
        "TMP": str(tmpdir),
        "PATH": r"C:\Windows\System32;C:\Windows",
        "INSAR_ENGINE_PREFIX": str(no_engines),
        "INSAR_HOME": str(tmp / "home"),
        "INSAR_PORT": str(port),
        "INSAR_ALLOW_SIMULATED": "1",
    })
    return env


def _sweep_strays() -> list[int]:
    """按可执行路径清扫本 dist 的残留进程(只认本仓 dist 路径,绝不误伤
    其他目录的同名进程,如 8873 生产实例)。返回被清扫的 PID 列表。"""
    script = (
        "Get-CimInstance Win32_Process -Filter \"Name='insar-backend.exe'\" | "
        f"Where-Object {{ $_.ExecutablePath -eq '{_EXE}' }} | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force "
        "-ErrorAction SilentlyContinue; $_.ProcessId }")
    try:
        cp = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                            capture_output=True, text=True, timeout=60,
                            creationflags=_NO_WINDOW)
        return [int(x) for x in cp.stdout.split() if x.strip().isdigit()]
    except (OSError, subprocess.SubprocessError):
        return []  # 清扫尽力而为:主进程已 kill,残留只可能是秒退的误派生实例


def _wait_health(client: "httpx.Client", proc: subprocess.Popen,
                 deadline_s: float = 60.0) -> None:
    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise AssertionError(f"冻结后端提前退出 exit={proc.returncode}")
        try:
            if client.get("/api/health", timeout=3).status_code == 200:
                return
        except httpx.HTTPError:
            time.sleep(0.4)
    raise AssertionError(f"{deadline_s}s 内 /api/health 未就绪")


@pytest.fixture(scope="module")
def backend(tmp_path_factory):
    """冻结 exe 起一次全模块共享:随机高位端口 + 一次性 INSAR_HOME,结束杀净。"""
    tmp = tmp_path_factory.mktemp("desktop-matrix")
    port = _free_port()
    env = _hermetic_env(tmp, port)
    out = open(tmp / "backend.out.log", "wb")
    err = open(tmp / "backend.err.log", "wb")
    try:
        proc = subprocess.Popen([str(_EXE)], env=env, stdout=out, stderr=err,
                                stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
    finally:
        out.close()
        err.close()
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=30.0)
    try:
        _wait_health(client, proc)
        yield {"client": client, "home": tmp / "home", "port": port,
               "proc": proc, "tmp": tmp}
    finally:
        client.close()
        if proc.poll() is None:
            proc.kill()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover - 极端兜底
            pass
        _sweep_strays()


@pytest.fixture(scope="module")
def source_openapi(tmp_path_factory):
    """源码 create_app 的 OpenAPI 描述(同版本源码树):冻结包 API 面的对照基准。"""
    from insar_agent.api.app import create_app

    app = create_app(home=tmp_path_factory.mktemp("desktop-matrix-src") / "home")
    return app.openapi()


def _stream(client: "httpx.Client", url: str, body: dict,
            timeout: float = 300.0) -> list[dict]:
    """消费 NDJSON 回合流(流协议纪律:每行可解析且含类型字段 t)。"""
    events: list[dict] = []
    with client.stream("POST", url, json=body, timeout=timeout) as resp:
        assert resp.status_code == 200, f"{url} -> {resp.status_code}"
        assert resp.headers["content-type"].startswith("application/x-ndjson")
        for line in resp.iter_lines():
            if not line.strip():
                continue
            event = json.loads(line)
            assert isinstance(event, dict) and "t" in event, f"事件缺类型字段: {line!r}"
            events.append(event)
    return events


def _wait_terminal(client: "httpx.Client", session: str,
                   deadline_s: float = 60.0) -> dict:
    """轮询 /api/state 到 run 终态(done/failed/interrupted)。"""
    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        state = client.get("/api/state", params={"session": session}).json()
        run = state.get("run")
        if run and run["status"] in _TERMINAL:
            return state
        time.sleep(0.5)
    raise AssertionError(f"{deadline_s}s 内 run 未到终态")


# ---------------------------------------------------------------------------
# 基础探活与安全响应头
# ---------------------------------------------------------------------------

def test_health_and_api_security_headers(backend):
    """/api/health 可达;API 面安全头(nosniff/no-store/DENY)在冻结包下同样生效。"""
    r = backend["client"].get("/api/health")
    assert r.status_code == 200 and r.json()["ok"] is True
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["x-frame-options"] == "DENY"


def test_static_ui_csp_headers(backend):
    """静态 UI 的按页 CSP 在冻结包下同样生效(app.scan_ui_csp 启动扫描表)。"""
    r = backend["client"].get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    csp = r.headers.get("content-security-policy", "")
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "script-src" in csp and "style-src" in csp
    assert r.headers["x-content-type-options"] == "nosniff"


# ---------------------------------------------------------------------------
# API 面:OpenAPI 全面对齐 + 逐 router 探活(动态完整性守卫)
# ---------------------------------------------------------------------------

def test_openapi_surface_parity_with_source(backend, source_openapi):
    """冻结包挂载的 API 面(路径 × 方法)与源码 create_app 逐项一致 ——
    include_router 清单的机器可验形态,新增/丢失路由都会在此暴露。"""
    frozen = backend["client"].get("/openapi.json").json()
    fset = {(p, m.upper()) for p, ops in frozen["paths"].items() for m in ops}
    sset = {(p, m.upper()) for p, ops in source_openapi["paths"].items() for m in ops}
    assert fset == sset, (f"API 面不一致:冻结缺 {sorted(sset - fset)};"
                          f"冻结多 {sorted(fset - sset)}")
    assert frozen["info"]["version"] == source_openapi["info"]["version"]


#: 逐 router 探活表:(探针 id, 方法, 路径, 请求参数, 允许状态码, 响应断言)。
#: 口径:GET 断言 200 / 合理 4xx;副作用类 POST 只测可达与校验错误形状,
#: 不触业务效果(setup/save 空 body、diagnostics 未知 run 等都停在校验层)。
_PROBES: list[tuple] = [
    ("health", "GET", "/api/health", {}, {200}, lambda b: b.get("ok") is True),
    # setup_router(/api/setup)—— status 首次含 WSL 冷探测,放宽超时
    ("setup-status", "GET", "/api/setup/status", {"timeout": 150.0}, {200},
     lambda b: {"engine", "checks", "settings_file"} <= set(b)),
    ("setup-save-empty", "POST", "/api/setup/save", {"json": {}}, {400},
     lambda b: "engine_prefix" in str(b.get("detail", ""))),
    ("setup-engine-env", "POST", "/api/setup/engine-env", {}, {200},
     lambda b: isinstance(b.get("commands"), list) and b["commands"]),
    # llm_router(/api/llm)—— 读写闭环另有专测,这里只探形状(零网络出行)
    ("llm-config", "GET", "/api/llm/config", {}, {200},
     lambda b: {"configured", "source", "api_key_masked"} <= set(b)),
    ("llm-models-nokey", "POST", "/api/llm/models", {"json": {}}, {200},
     lambda b: b.get("ok") is False and "密钥" in b.get("error", "")),
    ("llm-test-unconfigured", "POST", "/api/llm/test", {"json": {}}, {200},
     lambda b: b.get("ok") is False),
    # version_router(/api/version)
    ("version", "GET", "/api/version", {}, {200},
     lambda b: {"version", "git_head", "build"} <= set(b)),
    ("version-check", "GET", "/api/version/check", {}, {200},
     lambda b: b.get("update_available") is False),  # 密封环境未配置更新源
    # skills_router(/api/skills)—— 数量与源码的对齐另有专测
    ("skills-list", "GET", "/api/skills", {}, {200},
     lambda b: isinstance(b.get("skills"), list)),
    ("skills-step", "GET", "/api/skills/6", {}, {200, 404}, None),
    # admin_router(/api/admin)
    ("admin-runs", "GET", "/api/admin/runs", {}, {200},
     lambda b: isinstance(b, list)),
    ("admin-terminate-unknown", "POST", "/api/admin/terminate",
     {"json": {"run_id": "no-such-run"}}, {404}, None),
    # artifacts_router
    ("artifacts-norun", "GET", "/api/artifacts", {"params": {"session": _S}}, {200},
     lambda b: b.get("run") is None),
    ("artifacts-novalidation", "GET", "/api/artifacts", {}, {422}, None),
    # doctor_router(秒级只读深检)
    ("doctor", "GET", "/api/doctor", {"timeout": 60.0}, {200},
     lambda b: isinstance(b, dict) and b),
    # data_router
    ("timeseries-norun", "GET", "/api/timeseries-point",
     {"params": {"session": _S}}, {404}, None),
    # diag_router
    ("diag-make-unknown", "POST", "/api/diagnostics",
     {"json": {"run_id": "no-such-run"}}, {404}, None),
    ("diag-file-badname", "GET", "/api/diagnostics/file",
     {"params": {"name": "evil.zip"}}, {400}, None),
    # data_catalog_router(/api/datasets)
    ("datasets", "GET", "/api/datasets", {}, {200},
     lambda b: {"roots", "datasets"} <= set(b)),
    ("datasets-root-relative", "POST", "/api/datasets/roots",
     {"json": {"path": "relative/path"}}, {400}, None),
    ("datasets-unknown", "GET", "/api/datasets/no-such-id", {}, {404}, None),
    # queue_router(/api/queue)
    ("queue-list", "GET", "/api/queue", {}, {200},
     lambda b: isinstance(b.get("items"), list)),
    ("queue-post-norun", "POST", "/api/queue", {"json": {"session": _S}}, {404}, None),
    ("queue-del-unknown", "DELETE", "/api/queue/no-such-run",
     {"params": {"session": _S}}, {404}, None),
    # app.py 内联端点(会话/状态/导出/干预面)
    ("sessions-list", "GET", "/api/sessions", {}, {200},
     lambda b: isinstance(b, list)),
    ("sessions-invalid-id", "POST", "/api/sessions",
     {"json": {"id": "bad/id"}}, {400}, None),
    ("sessions-patch-unknown", "PATCH", "/api/sessions/no-such-session",
     {"json": {"name": "x"}}, {404}, None),
    ("sessions-delete-unknown", "DELETE", "/api/sessions/no-such-session",
     {}, {404}, None),
    ("chat-empty", "GET", "/api/chat", {"params": {"session": _S}}, {200},
     lambda b: b == []),
    ("registry", "GET", "/api/registry", {}, {200},
     lambda b: isinstance(b, list) and len(b) == 11),
    ("env", "GET", "/api/env", {"params": {"session": _S}, "timeout": 60.0}, {200},
     lambda b: {"probe", "thresholds"} <= set(b)),
    ("runs-empty", "GET", "/api/runs", {"params": {"session": _S}}, {200},
     lambda b: b.get("runs") == []),
    ("state-norun", "GET", "/api/state", {"params": {"session": _S}}, {200},
     lambda b: b.get("run") is None),
    ("turn-invalid-session", "POST", "/api/turn",
     {"json": {"session": "bad/id", "text": "x"}}, {400}, None),
    ("resume-norun", "POST", "/api/resume", {"json": {"session": _S}}, {200}, None),
    ("abort-norun", "POST", "/api/abort", {"json": {"session": _S}}, {404}, None),
    ("message-unknown-session", "POST", "/api/message",
     {"json": {"session": "no-such-session", "text": "hi"}}, {404}, None),
    ("actions-unknown-action", "POST", "/api/actions",
     {"json": {"session": _S, "target": "1", "action": "NOPE"}}, {400},
     lambda b: "未知动作" in str(b.get("detail", ""))),
    ("impact-norun", "GET", "/api/impact",
     {"params": {"session": _S, "step": 1}}, {404}, None),
    ("fork-unknown-run", "POST", "/api/fork",
     {"json": {"session": _S, "run_id": "no-such-run", "changes": {}}}, {404}, None),
    ("provenance-norun", "GET", "/api/provenance", {"params": {"session": _S}},
     {404}, None),
    ("runsh-norun", "GET", "/api/run.sh", {"params": {"session": _S}}, {404}, None),
    ("methods-norun", "GET", "/api/methods.md", {"params": {"session": _S}},
     {404}, None),
    ("repro-norun", "GET", "/api/repro-bundle", {"params": {"session": _S}},
     {404}, None),
    ("trace-norun", "GET", "/api/trace", {"params": {"session": _S}}, {200},
     lambda b: b == []),
    ("logs-norun", "GET", "/api/logs",
     {"params": {"session": _S, "step": 1}}, {404}, None),
    ("figures-norun", "GET", "/api/figures", {"params": {"session": _S}}, {200},
     lambda b: b.get("run") is None),
    ("artifact-file-norun", "GET", "/api/artifact-file",
     {"params": {"session": _S, "step": 1, "art_id": "x"}}, {404}, None),
]


@pytest.mark.parametrize("probe", _PROBES, ids=[p[0] for p in _PROBES])
def test_api_probe(backend, probe):
    """逐端点探活:状态码落在允许集合,响应体形状符合契约(4xx 必须是
    结构化 detail,而不是 500/裸断连 —— 校验错误形状在冻结包下不回归)。"""
    probe_id, method, path, kwargs, allowed, check = probe
    r = backend["client"].request(method, path, **kwargs)
    assert r.status_code in allowed, (
        f"[{probe_id}] {method} {path} -> {r.status_code}(允许 {sorted(allowed)}):"
        f"{r.text[:300]}")
    if not r.headers.get("content-type", "").startswith("application/json"):
        # 流式端点(如 /api/resume 的空 NDJSON 流)只测可达,不解析 JSON 体
        assert check is None, f"[{probe_id}] 非 JSON 响应的探针不该带形状断言"
        return
    body = r.json()
    if r.status_code >= 400:
        assert "detail" in body, f"[{probe_id}] 4xx 必须携带结构化 detail:{body}"
    if check is not None:
        shown = json.dumps(body, ensure_ascii=False)[:300]
        assert check(body), f"[{probe_id}] 响应形状不符:{shown}"


def test_probe_table_covers_every_mounted_api_segment(backend):
    """动态完整性守卫:冻结包实际挂载的每个 /api/<段> 都必须被矩阵触达
    (探活表 / 全链冒烟 / SSE 专测)。以后新挂 router 而不进矩阵,这里立即红。"""
    frozen = backend["client"].get("/openapi.json").json()
    mounted = {p.split("/")[2] for p in frozen["paths"] if p.startswith("/api/")}
    probed = {p[2].split("/")[2] for p in _PROBES}
    # pipeline/events 不适合表驱动探活(流式语义),由专测覆盖
    covered = probed | {"pipeline", "events"}
    assert mounted <= covered, f"新挂载的 API 段未纳入矩阵:{sorted(mounted - covered)}"


# ---------------------------------------------------------------------------
# 静态 UI:index + 引用资源逐个 200 且与源码逐字节一致
# ---------------------------------------------------------------------------

#: index.html 里的本地 js/css 引用(动态解析,不硬编码清单)
_ASSET_RE = re.compile(r'(?:src|href)="([^":]+?\.(?:js|css))"')


def test_static_ui_index_and_assets_serve_source_bytes(backend):
    """index 200 且与源码逐字节一致;其引用的全部 js/css 逐个 200 且逐字节
    一致 —— 同时是「dist 过旧」的探测器:UI 改了没重建会在这里红。"""
    client = backend["client"]
    r = client.get("/")
    assert r.status_code == 200
    src_index = (_REPO / "prototype" / "index.html").read_bytes()
    assert r.content == src_index, "冻结包 index.html 与源码不一致(dist 过旧?先重建)"

    assets = sorted(set(_ASSET_RE.findall(r.content.decode("utf-8"))))
    js = [a for a in assets if a.endswith(".js")]
    css = [a for a in assets if a.endswith(".css")]
    assert len(js) >= 10 and len(css) >= 10, f"引用清单解析异常:{assets}"

    missing, mismatched = [], []
    for rel in assets:
        resp = client.get("/" + rel)
        if resp.status_code != 200:
            missing.append(f"{rel} -> {resp.status_code}")
            continue
        assert resp.headers["x-content-type-options"] == "nosniff"
        src = _REPO / "prototype" / rel
        if not (src.is_file() and resp.content == src.read_bytes()):
            mismatched.append(rel)
    assert not missing, f"index 引用的资源在冻结包取不到:{missing}"
    assert not mismatched, f"冻结包资源与源码不一致(dist 过旧?先重建):{mismatched}"


# ---------------------------------------------------------------------------
# 模拟 run 全链冒烟(规划 → 执行 → 终态 → 账本导出)
# ---------------------------------------------------------------------------

def test_turn_plans_quake_simulated(backend):
    """quake 关键词规划回合:意图规则命中、云端 2-6 跳过、run ready 且
    simulated(密封环境的直接证明 —— 引擎全空,规划必然落到模拟方法)。"""
    client = backend["client"]
    r = client.post("/api/sessions", json={"id": SID, "name": "矩阵冒烟", "mode": "expert"})
    assert r.status_code == 200 and r.json()["session_id"] == SID

    events = _stream(client, "/api/turn",
                     {"session": SID, "text": "Ridgecrest 地震同震形变"})
    kinds = [e["t"] for e in events]
    for t in ("thinking", "plan", "say", "candidates"):
        assert t in kinds, f"规划回合缺 {t} 事件:{kinds}"
    thinking = next(e for e in events if e["t"] == "thinking")
    assert "(来源:rules)" in thinking["body"], "意图应由规则层命中(密封环境无 LLM)"
    assert any(e["t"] == "note" and "模拟" in e.get("text", "") for e in events), \
        "引擎全空必须给出显式「模拟执行」横幅(诚实性)"

    state = client.get("/api/state", params={"session": SID}).json()
    run = state["run"]
    assert run["status"] == "ready" and run["scenario"] == "quake"
    assert bool(run["simulated"]) is True
    st = {s["id"]: s["state"] for s in state["steps"]}
    assert sorted(st) == list(range(1, 12))
    assert all(st[sid] == "skipped" for sid in (2, 3, 4, 5, 6))
    assert all(st[sid] == "pending" for sid in (1, 7, 8, 9, 10, 11))
    _CTX["run_id"] = run["run_id"]


@pytest.mark.xfail(
    strict=True,
    reason="已知缺陷(2026-08-13 桌面矩阵实测,P1):模拟执行链在冻结包下断裂 ——"
           "runtime/jobs.LocalJobBackend.launch 与 engines/simulate.build 都用"
           " sys.executable 当 Python 解释器,冻结态它是 insar-backend.exe 本身:"
           "wrapper/模拟脚本没有被执行,反而误派生第二个后端实例(继承 INSAR_PORT"
           "→端口占用秒退;若无 INSAR_PORT 会落到 8873)。job.pid 永不出现,步骤在"
           " startup_grace(30s)后按 orphaned 失败,run 终态 failed。期望行为:"
           "冻结态解析真实 Python(引擎前缀/PATH)或显式失败提示。修复归属产品"
           "代码(另行分派,见 docs/DESKTOP-PARITY.md);修好后本测试 XPASS 提醒"
           "改回普通断言。")
def test_pipeline_executes_simulated_run_to_done(backend):
    """执行全链(与源码 journey J2 同口径的冒烟):只跑 [1,7,8,9,10,11],
    逐步 exit 0,run 到 done —— 源码运行成立,冻结包必须同样成立。"""
    client = backend["client"]
    assert "run_id" in _CTX, "前置规划节点未完成"
    events = _stream(client, "/api/pipeline", {"session": SID}, timeout=600.0)

    started = [e["stepId"] for e in events if e["t"] == "step.start"]
    assert started == [1, 7, 8, 9, 10, 11], f"执行集合漂移:{started}"
    ends = {e["stepId"]: e for e in events if e["t"] == "step.end"}
    assert all(ends[sid]["exit"] == 0 for sid in started), \
        {sid: ends.get(sid, {}).get("exit") for sid in started}
    assert any(e["t"] == "result" for e in events)

    state = _wait_terminal(client, SID)
    assert state["run"]["run_id"] == _CTX["run_id"]
    assert state["run"]["status"] == "done", state["run"]["status"]
    st = {s["id"]: s["state"] for s in state["steps"]}
    assert all(st[sid] == "done" for sid in (1, 7, 8, 9, 10, 11))
    _CTX["executed"] = True


def test_run_reaches_terminal_state_and_provenance_exports(backend):
    """无论执行结局如何(done / failed),run 必须收敛到终态且 provenance
    可导出、simulated 如实入账 —— 账本诚实性不随冻结形态回归。"""
    client = backend["client"]
    assert "run_id" in _CTX, "前置规划节点未完成"
    state = _wait_terminal(client, SID)
    assert state["run"]["status"] in _TERMINAL

    r = client.get("/api/provenance", params={"session": SID})
    assert r.status_code == 200
    prov = r.json()
    assert prov["run_id"] == _CTX["run_id"]
    assert prov["schema_version"] == "1.0"
    assert prov["simulated"] is True
    assert set(prov["steps"]) == {str(i) for i in range(1, 12)}
    assert "evidence" in prov and "step_sources" in prov["evidence"]


def test_sse_events_endpoint_reachable(backend):
    """全局 SSE(/api/events)在冻结包可建流(探活:200 + 正确媒体类型)。"""
    with backend["client"].stream("GET", "/api/events", params={"session": SID},
                                  timeout=20.0) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")


# ---------------------------------------------------------------------------
# LLM 配置面(冻结包文件写入路径正确性)—— 必须在 run 冒烟之后:
# 保存的假配置会让新建 driver 启用 brain,规划回合被密封性要求排在前面
# ---------------------------------------------------------------------------

def test_llm_config_roundtrip_persists_under_frozen_home(backend):
    """临时 HOME 下初始未配置;POST 假配置 → 掩码正确回显、完整密钥绝不回显;
    llm.json 落在冻结进程的 INSAR_HOME 下(写入路径正确性)。"""
    client = backend["client"]
    before = client.get("/api/llm/config").json()
    assert before["configured"] is False
    assert before["source"] == "none"
    assert before["api_key_masked"] == ""

    key = "sk-matrix-fake-0123456789abcdef"  # >12 字符:掩码走 前8…后4 分支
    saved = client.post("/api/llm/config", json={
        # base_url 指向本机 discard 端口:即使被误用也秒拒,绝不出网
        "base_url": "http://127.0.0.1:9/v1",
        "api_key": key,
        "chat_model": "fake-chat-model",
    }).json()
    assert saved["configured"] is True and saved["source"] == "file"
    assert saved["api_key_masked"] == f"{key[:8]}…{key[-4:]}"
    assert key not in json.dumps(saved), "完整密钥绝不回显"

    again = client.get("/api/llm/config").json()
    assert again["api_key_masked"] == saved["api_key_masked"]
    assert again["chat_model"] == "fake-chat-model"

    cfg_file = backend["home"] / "llm.json"
    assert cfg_file.is_file(), "llm.json 未落在冻结进程的 INSAR_HOME 下"
    on_disk = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert on_disk["api_key"] == key and on_disk["chat_model"] == "fake-chat-model"


# ---------------------------------------------------------------------------
# 差距断言:版本与技能数量与源码零差距
# ---------------------------------------------------------------------------

def test_version_parity_with_source(backend):
    """冻结包 /api/version 与源码包版本一致,且如实自报 frozen 形态。"""
    from insar_agent import __version__

    body = backend["client"].get("/api/version").json()
    assert body["version"] == __version__
    assert body["build"]["frozen"] is True


@pytest.mark.xfail(
    strict=True,
    reason="已知缺陷(2026-08-13 桌面矩阵实测,P1):步骤技能文档没进冻结包 ——"
           "insar_backend.spec 的 datas 缺仓库根 skills/,entry.py 也未设"
           " INSAR_SKILLS_DIR(skills/loader.py 头注声称会注入,实际没有);"
           "loader 冻结态回退到 <dist>/insar-backend/skills(不存在)→ /api/skills"
           "恒为空,规划/分诊失去技能知识源。期望:11 份与源码一致。修复归属"
           "打包配置(另行分派,见 docs/DESKTOP-PARITY.md);修好后本测试 XPASS。")
def test_skills_count_matches_source_eleven(backend):
    """/api/skills 数量 = 11,与源码技能目录零差距。"""
    from insar_agent.skills.loader import load_skills

    assert len(load_skills(_REPO / "skills")) == 11, "源码基准自证失败(技能目录变动?)"
    body = backend["client"].get("/api/skills").json()
    assert len(body["skills"]) == 11, f"冻结包技能数量 {len(body['skills'])} ≠ 11"
