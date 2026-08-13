r"""冻结产物(desktop/backend-bundle)的环境探测行为断言。

对象是 PyInstaller 冻结出的 insar-backend.exe 本体 —— 不是源码逻辑的单测
(那些在 test_setup_router.py / test_setup_optional_semantics.py),而是
「冻结形态下探测行为与源码一致」的集成验证:

  1. 隐式 conda 回退(runtime/probe._KNOWN_ENV_ROOTS)在 sys.frozen 下生效:
     PATH 无 conda 工具的机器上 mintpy/gdal 仍报 present(<env>);
  2. wsl.exe 子进程能从冻结 exe 拉起(sys.frozen 下 PATH/编码无回归),
     WSL 引擎以 " (wsl)" 后缀并入向导;
  3. WSL 探测 TTL 缓存(wsl_probe._PROBE_CACHE)生效:二次 status 秒回;
  4. 生产包纯净:hypothesis/ruff/pytest 等 dev 依赖不得混入 dist。

产物不存在(CI/未构建)整文件跳过;个别断言依赖本机布局(E:\miniforge3、
WSL 发行版 insar),条件不满足时单独跳过 —— 换机器不误报红。
构建:powershell -File desktop/backend-bundle/build_backend.ps1
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_DIST = _REPO / "desktop" / "backend-bundle" / "dist" / "insar-backend"
_EXE = _DIST / "insar-backend.exe"

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32", reason="冻结产物仅 Windows"),
    pytest.mark.skipif(not _EXE.exists(),
                       reason="冻结产物不存在(先跑 build_backend.ps1);CI 无产物按设计跳过"),
]

_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
# 本机已验证的隐式回退安装位(probe._KNOWN_ENV_ROOTS 首项);不存在则跳相关断言
_IMPLICIT_ENV = Path(r"E:\miniforge3\envs\insar")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _get(url: str, timeout: float) -> tuple[int, dict]:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def _wait_health(port: int, proc: subprocess.Popen, deadline_s: float = 40.0) -> None:
    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise AssertionError(f"冻结后端提前退出 exit={proc.returncode}")
        try:
            status, _ = _get(f"http://127.0.0.1:{port}/api/health", timeout=3)
            if status == 200:
                return
        except (urllib.error.URLError, OSError):
            time.sleep(0.4)
    raise AssertionError(f"{deadline_s}s 内 /api/health 未就绪(port={port})")


def _spawn_frozen(tmp: Path, *, drop_env: tuple[str, ...] = ()) -> tuple[subprocess.Popen, int]:
    """密封启动冻结 exe:一次性 INSAR_HOME、清掉显式引擎配置(逼出隐式回退)。"""
    port = _free_port()
    env = {k: v for k, v in os.environ.items() if k not in drop_env}
    env.pop("INSAR_ENGINE_PREFIX", None)  # 隐式回退路径必须在"未配置"下验证
    env.pop("INSAR_HYP3_SOURCE", None)
    env["INSAR_PORT"] = str(port)
    env["INSAR_HOME"] = str(tmp / "home")
    out = open(tmp / "backend.out.log", "wb")
    err = open(tmp / "backend.err.log", "wb")
    try:
        proc = subprocess.Popen([str(_EXE)], env=env, stdout=out, stderr=err,
                                stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
    finally:
        out.close()
        err.close()
    return proc, port


def _kill(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.kill()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:  # pragma: no cover - 极端兜底
        pass


def _wsl_distro_reachable(distro: str = "insar") -> bool:
    """wsl.exe 存在且发行版能起 bash(与 wsl_probe 同判据,轻量版)。"""
    try:
        cp = subprocess.run(
            ["wsl.exe", "-d", distro, "--exec", "echo", "ok"],
            capture_output=True, timeout=30, creationflags=_NO_WINDOW,
            env={**os.environ, "WSL_UTF8": "1"})
        return cp.returncode == 0 and b"ok" in cp.stdout
    except (OSError, subprocess.SubprocessError):
        return False


# ---------------- 探测行为(隐式 conda 回退 / WSL / TTL) ----------------

@pytest.fixture(scope="module")
def frozen_status(tmp_path_factory):
    """冻结 exe 起一次,连续取两次 /api/setup/status(计时),全模块共享。

    两次调用在同一 fixture 里完成:TTL 缓存是进程内状态,分测试各自调用
    会受用例执行顺序影响,计时对比必须同源。
    """
    tmp = tmp_path_factory.mktemp("frozen-probe")
    proc, port = _spawn_frozen(tmp)
    try:
        _wait_health(port, proc)
        url = f"http://127.0.0.1:{port}/api/setup/status"
        t0 = time.monotonic()
        s1, body1 = _get(url, timeout=120)  # 首次:WSL 冷探测约 20s,放宽
        t1 = time.monotonic() - t0
        t0 = time.monotonic()
        s2, body2 = _get(url, timeout=120)
        t2 = time.monotonic() - t0
        assert s1 == 200 and s2 == 200
        yield {"first": body1, "second": body2, "t1": t1, "t2": t2}
    finally:
        _kill(proc)


def test_implicit_conda_fallback_effective_when_frozen(frozen_status):
    """sys.frozen 下 _KNOWN_ENV_ROOTS 扫描生效:mintpy/gdal 由隐式前缀报出。

    值形如 present(<env名>) 只可能来自 prefix 分支(PATH 命中是裸 'present'),
    据此区分回退与 PATH;PATH 本就有 conda 工具的机器无法区分,跳过。
    """
    if not (_IMPLICIT_ENV / "Lib" / "site-packages" / "mintpy").is_dir():
        pytest.skip(f"本机无已验证安装位 {_IMPLICIT_ENV},隐式回退断言不适用")
    if shutil.which("smallbaselineApp.py") or shutil.which("gdalinfo"):
        pytest.skip("PATH 已有 conda 工具,无法归因隐式回退")

    engines = frozen_status["first"]["engine"]["engines"]
    assert engines["mintpy"] == f"present({_IMPLICIT_ENV.name})", engines
    assert engines["gdal"] == f"present({_IMPLICIT_ENV.name})", engines
    # 向导判定同步生效:engine_prefix 未配置时 mintpy/gdal 探测到即视为通过
    by_key = {c["key"]: c for c in frozen_status["first"]["checks"]}
    assert by_key["engine_mintpy"]["ok"] is True
    assert by_key["engine_gdal"]["ok"] is True


def test_wsl_probe_spawnable_from_frozen_exe(frozen_status):
    """wsl.exe 子进程从冻结 exe 能起:WSL 引擎以 " (wsl)" 后缀并入向导。

    以 snaphu 为信号:本机 snaphu 不在本地前缀/PATH,只可能由 WSL 兜底报出。
    """
    if not _wsl_distro_reachable():
        pytest.skip("本机无可达的 WSL 发行版 insar,WSL 兜底断言不适用")
    if shutil.which("snaphu") or (_IMPLICIT_ENV / "Library" / "bin" / "snaphu.exe").exists():
        pytest.skip("本地已有 snaphu,WSL 兜底信号被本地探测遮蔽")

    engines = frozen_status["first"]["engine"]["engines"]
    assert engines["snaphu"], f"WSL 可达但 snaphu 未报出(WSL 兜底失效):{engines}"
    assert engines["snaphu"].endswith(" (wsl)"), engines["snaphu"]


def test_wsl_probe_ttl_cache_effective_when_frozen(frozen_status):
    """TTL 缓存(wsl_probe._PROBE_CACHE)在冻结进程内生效:二次调用秒回。

    首次付了 WSL 冷探测全价(>8s)时,二次必须显著更快;WSL 不可达时
    探测本身就快(不入缓存),只保留绝对上界。
    """
    t1, t2 = frozen_status["t1"], frozen_status["t2"]
    assert t2 < 10.0, f"二次 status 应秒回,实测 {t2:.1f}s(首次 {t1:.1f}s)"
    if t1 > 8.0:
        assert t2 < t1 / 2, f"TTL 缓存未生效:首次 {t1:.1f}s,二次 {t2:.1f}s"
    # 缓存命中返回的是同一份结果:引擎结论不得漂移
    assert frozen_status["first"]["engine"]["engines"] == \
        frozen_status["second"]["engine"]["engines"]


# ---------------- 已知风险点(Path.home() / 盘符缺失) ----------------

@pytest.mark.xfail(
    strict=True,
    reason="已知缺陷(2026-08-13 冻结复验实测):probe.py 模块级 _KNOWN_ENV_ROOTS "
           "在 import 期求值 Path.home(),USERPROFILE/HOMEPATH 全缺时抛 "
           "RuntimeError('Could not determine home directory') → 整个后端启动失败 "
           "rc=1(而非仅隐式回退降级)。期望行为:该扫描根惰性求值/异常兜底。"
           "probe.py 归属探测分支,修复后本测试会 XPASS 提醒改回普通断言。")
def test_frozen_survives_without_home_env(tmp_path):
    """Path.home() 风险点:家目录环境变量全缺时,冻结后端应能起、探测应降级而非全灭。"""
    proc, port = _spawn_frozen(
        tmp_path, drop_env=("USERPROFILE", "HOMEDRIVE", "HOMEPATH", "HOME"))
    try:
        _wait_health(port, proc)
        status, body = _get(f"http://127.0.0.1:{port}/api/setup/status", timeout=120)
        assert status == 200
        assert isinstance(body["engine"]["engines"], dict)
    finally:
        _kill(proc)


def test_absent_drive_root_scan_is_safe():
    """盘符不存在时 _KNOWN_ENV_ROOTS 扫描的判据行为:is_dir() 安静返回 False,
    不抛不挂 —— 无 E: 盘的机器隐式回退退化为"扫其余根",与旧版行为一致。
    (pathlib 纯 Python,源码与冻结走同一实现,此断言两形态等价。)"""
    ghost = Path(r"\\?\Z:\__insar_no_such_root__\envs")
    assert ghost.is_dir() is False
    t0 = time.monotonic()
    for _ in range(10):
        Path(r"Z:\__insar_no_such_root__\envs").is_dir()
    assert time.monotonic() - t0 < 5.0, "不存在盘符的 is_dir 不应有秒级卡顿"


# ---------------- 生产包纯净度(spec 核对) ----------------

def test_dist_contains_no_dev_dependencies():
    """dev 工具链(pyproject [dev]/打包器自身)不得混入生产包:
    hypothesis 曾经由 pydantic 可选 import 被拖进包(spec excludes 的由来),
    这里对 _internal 顶层做黑名单断言,防回潮。"""
    internal = _DIST / "_internal"
    assert internal.is_dir(), "onedir 布局应有 _internal/"
    entries = {p.name.lower() for p in internal.iterdir()}
    # httpx 不在名单:requirements.txt 把它装进构建 venv,version_router 又按
    # 「优先 httpx」显式消费 —— 属有意的运行时可选依赖,不算混入(PYZ 内,列目录也看不见)。
    # PIL 在名单:仅为图标脚本声明,经 pygments 可选 import 拖入过,spec 已排除,此处防回潮。
    banned = {"hypothesis", "ruff", "pytest", "_pytest", "pluggy", "iniconfig",
              "psutil", "pip_audit", "pip-audit", "pyinstaller", "pil"}
    hit = {e for e in entries
           if any(e == b or e.startswith(b + "-") or e.startswith(b + ".") for b in banned)}
    assert not hit, f"生产包混入 dev 依赖:{sorted(hit)}"
    # 正向对照:运行时依赖应在(证明看的是正确目录,而非黑名单空转)
    assert any(e.startswith("numpy") for e in entries), entries
    assert any(e.startswith("h5py") for e in entries), entries


def test_dist_probe_data_files_in_place():
    """探测链依赖的包内数据文件仍按原包相对路径随包(spec datas 回归锁):
    wsl_wrapper.sh(WSL 执行链)/ local_wrapper.py(本地作业)。"""
    internal = _DIST / "_internal" / "insar_agent"
    assert (internal / "runtime" / "wsl_wrapper.sh").is_file()
    assert (internal / "runtime" / "local_wrapper.py").is_file()
