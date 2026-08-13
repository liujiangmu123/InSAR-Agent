"""环境探测(AGENT-DESIGN §7.2 面板9 的数据源;planner/feasibility 的输入)。

探测是 replay=safe 的纯查询;结果进 provenance(tool_versions)与候选收窄解释。
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from insar_agent.runtime.wsl import wsl_status

# 引擎 → 探测方式:(PATH 可执行名) 或 (python 模块名)
_ENGINE_EXES = {
    "isce2": "topsApp.py",
    "mintpy": "smallbaselineApp.py",
    "snaphu": "snaphu",
    "gdal": "gdalinfo",
    "snap": "gpt",
}
_ENGINE_MODULES = {
    "pystamps": "pystamps",
    "pyaps": "pyaps3",
}
# 凭据 → 判定文件/环境变量
_CREDENTIALS = {
    "earthdata": ("~/.netrc", "EARTHDATA_TOKEN"),
    "cds": ("~/.cdsapirc", "CDSAPI_KEY"),
    "gacos": ("", "GACOS_TOKEN"),
}


@dataclass
class ProbeResult:
    engines: dict[str, str | None] = field(default_factory=dict)  # engine -> version|None
    credentials: dict[str, bool] = field(default_factory=dict)
    wsl: dict = field(default_factory=dict)
    disk_free_gb: float = 0.0
    disk_total_gb: float = 0.0
    cpu_count: int = 0
    mem_gb: float | None = None
    python: str = ""
    platform: str = ""

    def engine_ok(self, engine: str) -> bool:
        if engine in ("-", ""):
            return True
        return self.engines.get(engine) is not None

    def tool_versions(self) -> dict[str, str]:
        return {k: v for k, v in self.engines.items() if v}

    def to_dict(self) -> dict:
        return {
            "engines": self.engines,
            "credentials": self.credentials,
            "wsl": self.wsl,
            "disk_free_gb": round(self.disk_free_gb, 1),
            "disk_total_gb": round(self.disk_total_gb, 1),
            "cpu_count": self.cpu_count,
            "mem_gb": self.mem_gb,
            "python": self.python,
            "platform": self.platform,
        }


_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0  # 后台探测不弹控制台


def _try_version(exe: str) -> str:
    """探测到可执行文件后尽力取版本号;拿不到就标 'present'。"""
    try:
        cp = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=5,
                            creationflags=_NO_WINDOW)
        out = (cp.stdout or cp.stderr or "").strip().splitlines()
        return out[0][:40] if out else "present"
    except Exception:
        return "present"


def _windows_mem_gb() -> float | None:
    try:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
        return round(stat.ullTotalPhys / (1 << 30), 1)
    except Exception:
        return None


#: 测试覆盖钩子:置为 tuple 时 _known_env_roots() 直接返回它(兼容既有
#: monkeypatch 用法);None = 按下方惰性逻辑计算
_KNOWN_ENV_ROOTS: tuple[Path, ...] | None = None


def _known_env_roots() -> tuple[Path, ...]:
    """隐式引擎环境的扫描根(优先级序):本机 README 验证过的安装位在前。

    惰性求值:Path.home() 在 USERPROFILE/HOMEPATH 全缺的环境抛 RuntimeError,
    模块级求值会炸掉整个后端(冻结版实测,frozen-probe 核查 xfail 记录)。"""
    if _KNOWN_ENV_ROOTS is not None:
        return _KNOWN_ENV_ROOTS
    roots = [Path(r"E:\miniforge3\envs")]
    try:
        home = Path.home()
        roots += [home / "miniforge3" / "envs", home / "mambaforge" / "envs"]
    except (RuntimeError, OSError):
        pass  # 无家目录环境(服务账户/精简容器):跳过用户级安装位
    roots.append(Path(r"C:\miniforge3\envs"))
    return tuple(roots)


def _implicit_engine_prefix() -> str | None:
    """INSAR_ENGINE_PREFIX 未配置时,在已知 conda 根里找含 mintpy 的环境。

    判据与显式 prefix 的检查一致(site-packages/mintpy 目录),名为 insar 的
    环境优先;找不到返回 None(行为与旧版完全一致)。"""
    site_rel = ("Lib/site-packages/mintpy" if sys.platform == "win32"
                else "lib/python3.11/site-packages/mintpy")
    for root in _known_env_roots():
        try:
            if not root.is_dir():
                continue
            envs = sorted(root.iterdir(),
                          key=lambda e: (e.name != "insar", e.name))
            for env in envs:
                if (env / site_rel).is_dir():
                    return str(env)
        except OSError:
            continue
    return None


def probe_environment(workspace: Path | str = ".", *, with_versions: bool = False,
                      check_wsl: bool = True) -> ProbeResult:
    result = ProbeResult()
    result.python = sys.version.split()[0]
    result.platform = sys.platform
    result.cpu_count = os.cpu_count() or 0
    if sys.platform == "win32":
        result.mem_gb = _windows_mem_gb()

    for engine, exe in _ENGINE_EXES.items():
        path = shutil.which(exe)
        if path is None:
            result.engines[engine] = None
        else:
            result.engines[engine] = _try_version(exe) if with_versions else "present"
    for engine, module in _ENGINE_MODULES.items():
        result.engines[engine] = "present" if importlib.util.find_spec(module) else None

    # conda 引擎环境(INSAR_ENGINE_PREFIX):Windows 原生 MintPy 路线(无 WSL 快速验证)。
    # 未配置时按已知安装位隐式回退(2026-08-13 用户实测:从无 conda PATH 的 shell
    # 启动服务,gdal 探测不到 → 必需项失败 → 向导误报"未通过";引擎明明装在
    # E:\miniforge3\envs\insar,探测不该依赖启动 shell 的 PATH)
    prefix = os.environ.get("INSAR_ENGINE_PREFIX") or _implicit_engine_prefix()
    if prefix:
        p = Path(prefix)
        scripts = p / ("Scripts" if sys.platform == "win32" else "bin")
        lib_bin = p / ("Library/bin" if sys.platform == "win32" else "bin")
        site = p / ("Lib/site-packages" if sys.platform == "win32" else
                    "lib/python3.11/site-packages")
        checks = {
            # conda 的 console_scripts 在 Windows 上是 smallbaselineApp.py.exe;
            # site-packages 模块目录是最稳的判据
            "mintpy": [site / "mintpy", scripts / "smallbaselineApp.py.exe",
                       scripts / "smallbaselineApp.exe", scripts / "smallbaselineApp"],
            "gdal": [lib_bin / "gdalinfo.exe", lib_bin / "gdalinfo"],
            "snaphu": [lib_bin / "snaphu.exe", scripts / "snaphu", lib_bin / "snaphu"],
            "pyaps": [site / "pyaps3"],
            "pystamps": [site / "pystamps"],
        }
        for engine, cands in checks.items():
            if not result.engines.get(engine) and any(c.exists() for c in cands):
                result.engines[engine] = f"present({p.name})"
        if result.engines.get("mintpy") and with_versions:
            py = p / ("python.exe" if sys.platform == "win32" else "bin/python")
            try:
                cp = subprocess.run(
                    [str(py), "-c", "import mintpy; print(mintpy.__version__)"],
                    capture_output=True, text=True, timeout=60, creationflags=_NO_WINDOW)
                if cp.returncode == 0 and cp.stdout.strip():
                    result.engines["mintpy"] = cp.stdout.strip()
            except Exception:
                pass

    for cred, (file_hint, env_hint) in _CREDENTIALS.items():
        ok = bool(env_hint and os.environ.get(env_hint))
        if not ok and file_hint:
            ok = Path(file_hint).expanduser().exists()
        result.credentials[cred] = ok

    if check_wsl:
        result.wsl = wsl_status()

    try:
        usage = shutil.disk_usage(str(workspace))
        result.disk_free_gb = usage.free / (1 << 30)
        result.disk_total_gb = usage.total / (1 << 30)
    except OSError:
        pass
    return result
