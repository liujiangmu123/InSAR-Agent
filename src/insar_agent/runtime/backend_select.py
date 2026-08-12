"""作业后端工厂:按方法引擎 + WSL 可达性路由(AGENT-DESIGN §4.7-§4.9 接线)。

规则(优先级从高到低):
  1. INSAR_JOB_BACKEND=local|wsl 显式强制(运维逃逸阀)。强制 wsl 时不做探测,
     发行版不可达就让作业显式失败 —— 绝不静默回退(§1.4 显式失败)。
  2. 引擎 ∈ WSL_ENGINES(isce2/snaphu,只有 Linux 原生发行版本)且发行版可达
     → WslJobBackend。发行版名取 INSAR_WSL_DISTRO(默认 insar,
     2026-08-12 实测发行版,见 docs/VALIDATION-isce2-wsl.md)。
  3. 其余(mintpy/qa/figures/localdata 及模拟运行等宿主可跑的链)
     → LocalJobBackend。

无 WSL 的环境所有分支都落到 LocalJobBackend,与接线前行为完全一致。
可达性探测只发生在 WSL_ENGINES 分支(每步 launch 前一次,相对分钟级步骤
可忽略),其余引擎绝不起 wsl.exe。
"""

from __future__ import annotations

import os
from typing import Mapping

from insar_agent.runtime.jobs import JobBackend, LocalJobBackend
from insar_agent.runtime.wsl import Runner, WslJobBackend, WslPaths, wsl_status

#: 必须在 WSL 内执行的引擎(宿主 Windows 没有可用的发行版本)
WSL_ENGINES = frozenset({"isce2", "snaphu"})

DEFAULT_DISTRO = "insar"


def wsl_distro(env: Mapping[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    return (env.get("INSAR_WSL_DISTRO") or DEFAULT_DISTRO).strip()


def wsl_reachable(distro: str, runner: Runner | None = None) -> bool:
    """发行版是否已注册(wsl.exe -l -q)。比较大小写不敏感:wsl.exe 按注册时
    原样输出,用户环境变量里的大小写经常与之不一致。"""
    status = wsl_status(runner)
    if not status.get("installed"):
        return False
    want = distro.casefold()
    return any(d.casefold() == want for d in status.get("distros", []))


def make_wsl_backend(distro: str) -> WslJobBackend:
    return WslJobBackend(paths=WslPaths(distro=distro))


def select_backend(engine: str, *, env: Mapping[str, str] | None = None,
                   runner: Runner | None = None) -> JobBackend:
    """按引擎选后端。env/runner 可注入(测试);默认读 os.environ、起真 wsl.exe。"""
    env = os.environ if env is None else env
    forced = (env.get("INSAR_JOB_BACKEND") or "").strip().lower()
    if forced == "local":
        return LocalJobBackend()
    if forced == "wsl":
        return make_wsl_backend(wsl_distro(env))
    if forced:
        raise ValueError(f"INSAR_JOB_BACKEND 只接受 local|wsl,得到 {forced!r}")
    if engine in WSL_ENGINES and wsl_reachable(wsl_distro(env), runner):
        return make_wsl_backend(wsl_distro(env))
    return LocalJobBackend()


def backend_for_step(*, engine: str, simulated: bool = False,
                     override: JobBackend | None = None,
                     env: Mapping[str, str] | None = None,
                     runner: Runner | None = None) -> JobBackend:
    """driver 每步 launch 前的选择入口。

    override 是构造 Driver 时显式注入的后端(测试/运维),永远最优先;
    模拟运行产出合成产物(纯 Python),即使被 INSAR_JOB_BACKEND=wsl 强制
    也留在本地 —— 演示模式不该因为一个运维变量而依赖 WSL。
    """
    if override is not None:
        return override
    if simulated:
        return LocalJobBackend()
    return select_backend(engine, env=env, runner=runner)
