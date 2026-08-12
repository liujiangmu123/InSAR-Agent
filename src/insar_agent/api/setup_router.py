"""桌面首启环境向导后端(/api/setup):检测 → 修复建议 → 配置落盘。

三个端点(挂载方式见 docs/INTEGRATION-setup.md,本模块不改 app.py):
  - GET  /api/setup/status      一次性返回首启所需的全部检测 + 中文修复建议;
  - POST /api/setup/engine-env  只生成「创建 conda 引擎环境」的命令清单给前端展示/复制,
                                绝不在后端执行任何安装(重型计算管控);
  - POST /api/setup/save        engine_prefix / hyp3_source 写入 INSAR_HOME/settings.json
                                (同目录 tmp + os.replace 原子写)并热更新 os.environ。

配置优先级:显式环境变量 > settings.json(GET 加载用 setdefault,不覆盖已设的 env);
POST /save 是用户刚做的选择,直接覆盖进程内 env,本进程立即生效。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from insar_agent.runtime.probe import probe_environment

# settings.json 键 → 进程环境变量
_ENV_OF = {
    "engine_prefix": "INSAR_ENGINE_PREFIX",
    "hyp3_source": "INSAR_HYP3_SOURCE",
}

# 首启就绪的最低磁盘余量(HyP3 路线:2-6 步云端完成,本地只做导入 + MintPy 链)
_MIN_DISK_GB = 10.0

# 命令清单的镜像与下载源(与 README「环境坑位记录」的处置保持一致)
_TUNA_CONDA_FORGE = "https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/conda-forge/"
_MINIFORGE_TUNA = ("https://mirrors.tuna.tsinghua.edu.cn/github-release/conda-forge/"
                   "miniforge/LatestRelease/Miniforge3-Windows-x86_64.exe")
_MINIFORGE_OFFICIAL = ("https://github.com/conda-forge/miniforge/releases/latest/"
                       "download/Miniforge3-Windows-x86_64.exe")
# conda 检测顺序:E:\miniforge3(本机验证过的安装位)→ PATH
_CONDA_CANDIDATES = (
    Path(r"E:\miniforge3\condabin\conda.bat"),
    Path(r"E:\miniforge3\Scripts\conda.exe"),
)


class SaveBody(BaseModel):
    engine_prefix: str | None = None  # conda 引擎环境目录;传 "" 表示清除该项
    hyp3_source: str | None = None    # HyP3 产品数据源目录;传 "" 表示清除该项


# ---------------- settings.json ----------------

def _resolve_home(override: Path | str | None) -> Path:
    # 与 app.py create_app 的解析规则一致:参数 > INSAR_HOME > ./workspace
    return Path(override or os.environ.get("INSAR_HOME", "workspace")).resolve()


def settings_path(home: Path) -> Path:
    return home / "settings.json"


def _read_settings_file(home: Path) -> dict:
    """只读 settings.json,不碰 os.environ;缺失/损坏一律回空 dict。"""
    try:
        data = json.loads(settings_path(home).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def load_settings(home: Path) -> dict:
    """加载 settings.json 并热更新进程环境(setdefault:显式环境变量优先)。"""
    settings = _read_settings_file(home)
    for key, env in _ENV_OF.items():
        value = settings.get(key)
        if isinstance(value, str) and value:
            os.environ.setdefault(env, value)
    return settings


def _atomic_write_json(path: Path, data: dict) -> None:
    """同目录 tmp + os.replace:进程崩溃也不会留下半截 settings.json。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp",
                               dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------- 检测辅助 ----------------

def _count_unw_rasters(source: Path) -> int:
    """干涉对计数,与 engines/localdata.py 同一判据:*/*unw_phase_clipped.tif。"""
    root = source / "hyp3" if (source / "hyp3").is_dir() else source
    try:
        return sum(1 for _ in root.glob("*/*unw_phase_clipped.tif"))
    except OSError:
        return 0


def _detect_conda() -> str | None:
    for cand in _CONDA_CANDIDATES:
        if cand.exists():
            return str(cand)
    return shutil.which("conda") or shutil.which("mamba")


def _check(key: str, ok: bool, message: str, fix_hint: str = "", *,
           required: bool = True) -> dict:
    return {"key": key, "ok": ok, "message": message,
            "fix_hint": "" if ok else fix_hint, "required": required}


# ---------------- 路由 ----------------

def create_setup_router(home: Path | str | None = None) -> APIRouter:
    """home 不传则每次请求按 INSAR_HOME 动态解析(与 create_app 默认一致);
    create_app(home=...) 显式传了别的目录时,应把同一 home 传进来,见集成文档。"""
    router = APIRouter(prefix="/api/setup", tags=["setup"])

    def _home() -> Path:
        return _resolve_home(home)

    @router.get("/status")
    def status() -> dict:
        home_dir = _home()
        settings = load_settings(home_dir)

        # 磁盘探测点:INSAR_HOME 或其最近存在的祖先(首启时 home 可能还没建出来)
        probe_root = next((p for p in (home_dir, *home_dir.parents) if p.exists()),
                          Path("."))
        probe = probe_environment(probe_root, with_versions=False, check_wsl=False)

        prefix = os.environ.get("INSAR_ENGINE_PREFIX") or None
        prefix_exists = bool(prefix) and Path(prefix).is_dir()
        engines = {k: probe.engines.get(k) for k in ("mintpy", "gdal", "snaphu", "pyaps")}

        source = os.environ.get("INSAR_HYP3_SOURCE") or None
        source_exists = bool(source) and Path(source).is_dir()
        pair_count = _count_unw_rasters(Path(source)) if source_exists else 0

        py_ver = sys.version.split()[0]
        in_venv = sys.prefix != sys.base_prefix

        engines_ok = bool(engines["mintpy"]) and bool(engines["gdal"])
        if prefix and prefix_exists:
            prefix_check = _check("engine_prefix", True, f"引擎环境:{prefix}")
        elif prefix:
            prefix_check = _check(
                "engine_prefix", False,
                f"INSAR_ENGINE_PREFIX 指向的目录不存在:{prefix}",
                "确认 conda 引擎环境路径(如 E:\\miniforge3\\envs\\insar)后重新保存;"
                "或按 POST /api/setup/engine-env 返回的命令先创建环境")
        elif engines_ok:
            prefix_check = _check(
                "engine_prefix", True,
                "未配置 INSAR_ENGINE_PREFIX,但 PATH 中已能探测到 mintpy/gdal")
        else:
            prefix_check = _check(
                "engine_prefix", False, "未配置引擎环境(INSAR_ENGINE_PREFIX)",
                "调用 POST /api/setup/engine-env 获取创建命令;环境建好后把 conda 环境路径"
                "(如 E:\\miniforge3\\envs\\insar)通过 POST /api/setup/save 保存为 engine_prefix")

        if source and source_exists and pair_count > 0:
            data_check = _check(
                "data_source", True, f"数据源:{source}(解缠相位栅格 {pair_count} 个)")
        elif source and source_exists:
            data_check = _check(
                "data_source", False,
                f"数据源目录里没有 */*unw_phase_clipped.tif:{source}",
                "确认选择的是 HyP3 产品根目录:每个干涉对一个子目录,内含 *unw_phase_clipped.tif"
                ";根目录下带 hyp3/ 子目录的布局也支持")
        elif source:
            data_check = _check(
                "data_source", False, f"数据源目录不存在:{source}",
                "确认路径后通过 POST /api/setup/save 重新保存 hyp3_source")
        else:
            data_check = _check(
                "data_source", False, "未配置数据源(INSAR_HYP3_SOURCE)",
                "在向导中选择 HyP3 产品目录,通过 POST /api/setup/save 保存为 hyp3_source")

        checks = [
            _check("agent_python", sys.version_info >= (3, 11),
                   f"Agent 运行时 Python {py_ver}" + ("(venv)" if in_venv else "(非 venv)"),
                   "用项目内虚拟环境运行 agent(需 Python ≥ 3.11):py -m venv .venv,"
                   "然后 .venv\\Scripts\\python.exe -m pip install -r requirements.txt"),
            prefix_check,
            _check("engine_mintpy", engines["mintpy"] is not None,
                   f"MintPy:{engines['mintpy']}" if engines["mintpy"]
                   else "未探测到 MintPy(7-9 步 SBAS 反演的引擎)",
                   "按 POST /api/setup/engine-env 返回的命令创建 conda 引擎环境"
                   "(python=3.11 + mintpy)"),
            _check("engine_gdal", engines["gdal"] is not None,
                   f"GDAL:{engines['gdal']}" if engines["gdal"]
                   else "未探测到 GDAL(栅格读写)",
                   "GDAL 随 mintpy 的 conda 包自动安装;创建引擎环境并保存 engine_prefix 后复检"),
            _check("engine_snaphu", engines["snaphu"] is not None,
                   f"snaphu:{engines['snaphu']}" if engines["snaphu"]
                   else "(可选)未探测到 snaphu:HyP3 云端路线不需要本地解缠",
                   "如需本地解缠链路:conda install -n insar snaphu", required=False),
            _check("engine_pyaps", engines["pyaps"] is not None,
                   f"pyaps3:{engines['pyaps']}" if engines["pyaps"]
                   else "(可选)未探测到 pyaps3:对流层校正用;数据源含缓存 ERA5.h5 时可免",
                   "conda install -n insar pyaps3(通常随 mintpy 依赖已装)", required=False),
            data_check,
            _check("disk_space", probe.disk_free_gb >= _MIN_DISK_GB,
                   f"磁盘可用 {probe.disk_free_gb:.1f} GB / 共 {probe.disk_total_gb:.1f} GB",
                   f"至少预留 {_MIN_DISK_GB:.0f} GB:清理磁盘或把 INSAR_HOME 指向更大的盘"),
        ]
        ready = all(c["ok"] for c in checks if c["required"])

        return {
            "ready": ready,
            "agent": {"python": py_ver, "venv": in_venv, "executable": sys.executable},
            "engine": {"prefix": prefix, "prefix_configured": bool(prefix),
                       "prefix_exists": prefix_exists, "engines": engines},
            "data": {"source": source, "configured": bool(source),
                     "exists": source_exists, "pair_count": pair_count},
            "disk": {"free_gb": round(probe.disk_free_gb, 1),
                     "total_gb": round(probe.disk_total_gb, 1)},
            "checks": checks,
            "settings_file": str(settings_path(home_dir)),
            "settings": settings,
        }

    @router.post("/engine-env")
    def engine_env() -> dict:
        """只产出命令清单(展示/复制),后端绝不执行安装 —— 重型计算管控。"""
        conda = _detect_conda()
        exe = conda or "conda"
        if " " in exe:
            exe = f'"{exe}"'
        commands = [
            {"title": "安装 Miniforge(已检测到 conda 时可跳过)",
             "command": _MINIFORGE_TUNA,
             "note": "清华镜像下载,建议安装到 E:\\miniforge3;官方源:" + _MINIFORGE_OFFICIAL},
            {"title": "创建引擎环境(python=3.11 + MintPy,清华 conda-forge 镜像)",
             "command": f"{exe} create -n insar -c {_TUNA_CONDA_FORGE} "
                        "--override-channels python=3.11 mintpy -y",
             "note": "对应 README 已验证环境(MintPy 1.6.4 + GDAL 3.13.2,conda-forge);"
                     "gdal/pyaps3 作为 mintpy 依赖自动安装"},
            {"title": "BLAS 切 OpenBLAS(必做)",
             "command": f'{exe} install -n insar -c {_TUNA_CONDA_FORGE} '
                        '--override-channels "libblas=*=*openblas" -y',
             "note": "README「环境坑位记录」:conda-forge Windows 默认 BLAS=MKL,"
                     "MKL 2024 在 i9-13900K 上多线程延迟加载硬崩(0xC06D007F);切 OpenBLAS 后稳定"},
            {"title": "(可选)本地解缠 snaphu",
             "command": f"{exe} install -n insar -c {_TUNA_CONDA_FORGE} "
                        "--override-channels snaphu -y",
             "note": "HyP3 云端路线(2-6 步在云端完成)不需要;走本地解缠链路时再装"},
            {"title": "完成后保存引擎环境路径",
             "command": r"E:\miniforge3\envs\insar",
             "note": "把该路径作为 engine_prefix 通过 POST /api/setup/save 保存,"
                     "再 GET /api/setup/status 复检"},
        ]
        return {"detected_conda": conda, "commands": commands}

    @router.post("/save")
    def save(body: SaveBody) -> dict:
        home_dir = _home()
        patch = {k: v for k, v in
                 (("engine_prefix", body.engine_prefix), ("hyp3_source", body.hyp3_source))
                 if v is not None}
        if not patch:
            raise HTTPException(400, "没有要保存的字段(engine_prefix / hyp3_source 至少一项)")
        settings = _read_settings_file(home_dir)
        for key, raw in patch.items():
            value = raw.strip()
            env = _ENV_OF[key]
            if value:
                settings[key] = value
                os.environ[env] = value  # 热更新:本进程立即生效
            else:
                settings.pop(key, None)  # 空串 = 清除该项
                os.environ.pop(env, None)
        _atomic_write_json(settings_path(home_dir), settings)
        return {
            "ok": True,
            "settings_file": str(settings_path(home_dir)),
            "settings": settings,
            "exists": {k: Path(v).exists() for k, v in settings.items() if k in _ENV_OF},
        }

    return router


# 默认实例:INSAR_HOME 动态解析,app.py 一行 include_router 即可挂载
setup_router = create_setup_router()
