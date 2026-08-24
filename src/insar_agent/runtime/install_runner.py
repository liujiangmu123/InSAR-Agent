"""受约束的引擎安装执行器:只跑本模块写死的 argv,绝不接受 LLM 给的命令。

循环动作 install_engine 与开工预检共用。SNAP / PyStamps 无官方包 → 诚实
声明不能代装。conda / WSL 命令从知识表口径固化为参数列表,shell=False。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable

from insar_agent.runtime.install_guide import _TUNA_CONDA_FORGE, ENGINE_ORDER

#: 本机 conda 可代装的包规格(与 install_guide 清华 conda-forge 口径一致)
_CONDA_SPECS: dict[str, tuple[str, ...]] = {
    "mintpy": ("python=3.11", "mintpy"),
    "gdal": ("gdal",),
    "pyaps": ("pyaps3",),
    "snaphu": ("snaphu",),
}

_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

Runner = Callable[[list[str], float], subprocess.CompletedProcess]


def find_conda() -> Path | None:
    """发现 conda 可执行:INSAR_CONDA > 引擎前缀推演 > PATH。"""
    explicit = (os.environ.get("INSAR_CONDA") or "").strip()
    if explicit:
        p = Path(explicit)
        if p.is_file():
            return p
    prefix = (os.environ.get("INSAR_ENGINE_PREFIX") or "").strip()
    if prefix:
        root = Path(prefix)
        # .../envs/insar → .../Scripts/conda.exe ; 也容忍 prefix 本身就是 env
        for cand in (
            root.parent.parent / "Scripts" / "conda.exe",
            root.parent.parent / "condabin" / "conda.bat",
            root / "Scripts" / "conda.exe",
            root / "condabin" / "conda.bat",
        ):
            if cand.is_file():
                return cand
    which = shutil.which("conda")
    return Path(which) if which else None


def wsl_distro() -> str:
    return (os.environ.get("INSAR_WSL_DISTRO") or "insar").strip() or "insar"


def plan_auto_install(engine: str) -> dict:
    """返回安装计划(不执行)。kind=conda|wsl|manual|unknown。"""
    if engine not in ENGINE_ORDER:
        return {"engine": engine, "kind": "unknown",
                "reason": f"未知引擎 {engine}(闭集:{'/'.join(ENGINE_ORDER)})"}
    if engine in _CONDA_SPECS:
        conda = find_conda()
        if conda is None:
            return {"engine": engine, "kind": "conda", "argv": None,
                    "reason": "未找到 conda(设 INSAR_CONDA 或 INSAR_ENGINE_PREFIX)"}
        argv = [str(conda), "install", "-n", "insar", "-c", _TUNA_CONDA_FORGE,
                "--override-channels", *_CONDA_SPECS[engine], "-y"]
        return {"engine": engine, "kind": "conda", "argv": argv, "reason": ""}
    if engine == "isce2":
        argv = [
            "wsl.exe", "-d", wsl_distro(), "-u", "root", "--exec", "bash", "-lc",
            "source /etc/profile.d/insar.sh 2>/dev/null; "
            f"conda install -n insar -c {_TUNA_CONDA_FORGE} "
            "--override-channels 'isce2=2.6.5' -y",
        ]
        return {"engine": engine, "kind": "wsl", "argv": argv, "reason": ""}
    if engine == "snaphu":
        # Windows 无可靠命令行可执行时走 WSL apt(与 wsl_setup.sh 同口径)
        argv = [
            "wsl.exe", "-d", wsl_distro(), "-u", "root", "--exec", "bash", "-lc",
            "command -v snaphu >/dev/null || "
            "(apt-get update -qq && apt-get install -y snaphu)",
        ]
        return {"engine": engine, "kind": "wsl", "argv": argv, "reason": ""}
    return {"engine": engine, "kind": "manual", "argv": None,
            "reason": f"{engine} 无官方 conda/包管理器途径,不能代装"}


def _default_runner(argv: list[str], timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv, capture_output=True, timeout=timeout, text=True,
        encoding="utf-8", errors="replace", creationflags=_NO_WINDOW)


def run_install(engine: str, *, runner: Runner | None = None,
                timeout: float = 600.0) -> dict:
    """执行允许清单里的安装。返回 {ok, engine, kind, summary, rc}。"""
    plan = plan_auto_install(engine)
    kind = plan["kind"]
    if kind == "unknown":
        return {"ok": False, "engine": engine, "kind": kind,
                "summary": plan["reason"], "rc": None}
    if kind == "manual" or plan.get("argv") is None:
        return {"ok": False, "engine": engine, "kind": kind,
                "summary": plan["reason"] or "无可执行安装命令", "rc": None}
    argv = list(plan["argv"])
    run = runner or _default_runner
    try:
        cp = run(argv, timeout)
    except FileNotFoundError:
        return {"ok": False, "engine": engine, "kind": kind,
                "summary": f"找不到可执行文件:{argv[0]}", "rc": None}
    except subprocess.TimeoutExpired:
        return {"ok": False, "engine": engine, "kind": kind,
                "summary": f"安装超时({timeout:.0f}s):{engine}", "rc": None}
    except OSError as exc:
        return {"ok": False, "engine": engine, "kind": kind,
                "summary": f"启动安装失败:{exc}", "rc": None}
    tail = ((cp.stdout or "") + "\n" + (cp.stderr or "")).strip().splitlines()
    last = tail[-1][:160] if tail else "(无输出)"
    ok = cp.returncode == 0
    summary = (f"{engine} 安装{'成功' if ok else '失败'}(rc={cp.returncode}):{last}")
    return {"ok": ok, "engine": engine, "kind": kind, "summary": summary,
            "rc": cp.returncode}
