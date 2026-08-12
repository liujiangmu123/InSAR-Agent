"""WSL 内引擎环境探测(scripts/wsl_setup.ps1 验证段;probe_environment 的后续接线点)。

契约(与 runtime/wsl.py 的 Runner 一致):
  - runner 可注入:``Callable[[argv: list[str], timeout: float], subprocess.CompletedProcess]``;
    允许抛 FileNotFoundError(无 wsl.exe)/ subprocess.TimeoutExpired / OSError,
    probe_wsl_engines 内部归一化,绝不外抛。默认 runner 后台执行(CREATE_NO_WINDOW,
    不弹控制台)、WSL_UTF8=1、输出按 UTF-8 解码并剔除 NUL。
  - 探测命令经 ``wsl.exe -d <distro> -u root --exec bash -lc <script>`` 执行:
    -l 登录 shell 会加载 /etc/profile.d/insar.sh(scripts/wsl_setup.sh 步骤 4 写入),
    PATH 才带引擎与 ISCE2 applications 目录。
  - 纯查询,replay=safe;不改 probe.py 本体,merge_wsl_probe() 由后续在
    probe_environment 处接线。

probe_wsl_engines 返回的结果字典::

    {
      "ok": bool,                 # wsl.exe 与发行版可达
      "distro": str,
      "error": str | None,        # 不可达原因(仅 ok=False 时非空)
      "engine_prefix": str|None,  # INSAR_ENGINE_PREFIX 或默认 /opt/miniforge3/envs/insar
      "engines": {                # 键与 probe.py 的 _ENGINE_EXES 引擎名一致
        "isce2":  {"present": bool, "path": str|None, "version": str|None, "error": str|None},
        "mintpy": {...},
        "snaphu": {...},
      },
    }
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:  # 仅类型标注,避免运行期耦合 probe.py
    from insar_agent.runtime.probe import ProbeResult

Runner = Callable[[list[str], float], subprocess.CompletedProcess]

_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0  # 后台探测不弹控制台

_REACH_MARKER = "__insar_wsl_ok__"
_DEFAULT_PREFIX = "/opt/miniforge3/envs/insar"

# 引擎名与 probe.py 的 _ENGINE_EXES 对齐;值 =(可执行名, 版本命令)
_ENGINES: dict[str, tuple[str, str]] = {
    "isce2": (
        "topsApp.py",
        "python3 -c 'import isce; print(getattr(isce, \"release_version\", \"\")"
        " or getattr(isce, \"__version__\", \"\"))'",
    ),
    "mintpy": (
        "smallbaselineApp.py",
        "python3 -c 'import mintpy; print(mintpy.__version__)'",
    ),
    # snaphu 无 --version:空跑的用法输出首行形如 `snaphu v2.0.7`(rc 非零,不影响取版本)
    "snaphu": ("snaphu", "snaphu 2>&1 | head -n 2"),
}
_VERSION_RE = re.compile(r"(\d+(?:\.\d+)+[\w.\-+]*)")


def _decode(data: bytes | str | None) -> str:
    if data is None:
        return ""
    text = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else str(data)
    return text.replace("\x00", "")  # 老版 wsl.exe 消息为 UTF-16,剔除残留 NUL


def _default_runner(argv: list[str], timeout: float) -> subprocess.CompletedProcess:
    env = {**os.environ, "WSL_UTF8": "1"}
    cp = subprocess.run(argv, capture_output=True, timeout=timeout,
                        creationflags=_NO_WINDOW, env=env)
    return subprocess.CompletedProcess(argv, cp.returncode,
                                       stdout=_decode(cp.stdout), stderr=_decode(cp.stderr))


def _argv(distro: str, script: str) -> list[str]:
    return ["wsl.exe", "-d", distro, "-u", "root", "--exec", "bash", "-lc", script]


def _run(runner: Runner, argv: list[str], timeout: float) -> tuple[int | None, str, str, str | None]:
    """归一化 runner 异常:返回 (rc, stdout, stderr, error)。rc=None 表示命令没跑起来。"""
    try:
        cp = runner(argv, timeout)
    except FileNotFoundError:
        return None, "", "", "wsl.exe 不存在(未安装 WSL)"
    except subprocess.TimeoutExpired:
        return None, "", "", f"timeout({timeout:g}s)"
    except OSError as exc:
        return None, "", "", f"wsl.exe 启动失败:{exc}"
    return cp.returncode, _decode(cp.stdout).strip(), _decode(cp.stderr).strip(), None


#: probe_wsl_engines_cached 的模块级缓存:{distro: (时间戳, 结果)}。
#: WSL 引擎探测实测约 20s(VM 启动 + conda python 冷启动 + 逐引擎查版本),
#: 向导/环境面板每次都付一遍不可接受(2026-08-12 实测:15s 超时下向导必失败)
_PROBE_CACHE: dict[str, tuple[float, dict]] = {}
_PROBE_CACHE_TTL = 300.0


def probe_wsl_engines_cached(distro: str = "insar", runner: Runner | None = None,
                             timeout: float = 60.0, ttl: float = _PROBE_CACHE_TTL,
                             force: bool = False) -> dict:
    """带 TTL 缓存的 probe_wsl_engines:首次付全价(约 20s),窗口内秒回。

    只缓存 ok=True 的结果:失败(未装 WSL/超时)不缓存,下次调用重试——
    否则一次冷启动超时会让 5 分钟内的所有探测都错报"不可达"。
    """
    import time as _time

    hit = _PROBE_CACHE.get(distro)
    if not force and hit and _time.monotonic() - hit[0] < ttl:
        return hit[1]
    result = probe_wsl_engines(distro=distro, runner=runner, timeout=timeout)
    if result.get("ok"):
        _PROBE_CACHE[distro] = (_time.monotonic(), result)
    return result


def probe_wsl_engines(distro: str = "insar", runner: Runner | None = None,
                      timeout: float = 60.0) -> dict:
    """探测 WSL 内引擎(topsApp.py/smallbaselineApp.py/snaphu 的存在性与版本、conda env 路径)。

    单个引擎探测失败(缺失/超时)不影响其余引擎;只有 WSL/发行版不可达才整体 ok=False。
    """
    run = runner or _default_runner
    result: dict = {
        "ok": False,
        "distro": distro,
        "error": None,
        "engine_prefix": None,
        "engines": {name: {"present": False, "path": None, "version": None, "error": None}
                    for name in _ENGINES},
    }

    # 1) 可达性:wsl.exe 存在 + 发行版能起 bash
    rc, out, err, error = _run(run, _argv(distro, f"echo {_REACH_MARKER}"), timeout)
    if error:
        result["error"] = error
        return result
    if rc != 0 or _REACH_MARKER not in out:
        detail = (err or out or f"rc={rc}").splitlines()[0] if (err or out) else f"rc={rc}"
        result["error"] = f"发行版 {distro!r} 不可达:{detail[:200]}"
        return result
    result["ok"] = True

    # 2) conda env 路径(INSAR_ENGINE_PREFIX 优先,回落默认前缀;目录存在才算)
    rc, out, _e, error = _run(
        run,
        _argv(distro, 'p="${INSAR_ENGINE_PREFIX:-' + _DEFAULT_PREFIX + '}"; [ -d "$p" ] && echo "$p"'),
        timeout)
    if error is None and rc == 0 and out:
        result["engine_prefix"] = out.splitlines()[0].strip()

    # 3) 各引擎:存在性(command -v)→ 版本(尽力而为,取不到不影响 present)
    for name, (exe, version_cmd) in _ENGINES.items():
        eng = result["engines"][name]
        rc, out, err, error = _run(run, _argv(distro, f"command -v {exe}"), timeout)
        if error:
            eng["error"] = error
            continue
        if rc != 0 or not out:
            continue  # 未安装:present 保持 False
        eng["present"] = True
        eng["path"] = out.splitlines()[0].strip()
        rc, out, err, error = _run(run, _argv(distro, version_cmd), timeout)
        if error:
            eng["error"] = error
            continue
        m = _VERSION_RE.search(out + "\n" + err)
        if m:
            eng["version"] = m.group(1)
    return result


def merge_wsl_probe(probe: "ProbeResult", wsl_result: dict) -> "ProbeResult":
    """把 probe_wsl_engines 的结果并入 ProbeResult(原地合并并返回同一对象)。

    - 引擎键带 " (wsl)" 后缀标注来源,值语义与 ProbeResult.engines 一致:
      版本字符串 / "present"(在但取不到版本)/ None(WSL 已探测但该引擎缺失)。
      engine_ok("isce2 (wsl)")、tool_versions() 因此天然适用。
    - 探测元数据(ok/error/engine_prefix)挂到 probe.wsl["engine_probe"],
      供面板9/feasibility 解释"WSL 引擎为何不可用"。
    - WSL 不可达时不添加任何引擎键:与"根本没探测"可由 engine_probe 区分。
    """
    probe.wsl["engine_probe"] = {
        "ok": bool(wsl_result.get("ok")),
        "distro": wsl_result.get("distro"),
        "error": wsl_result.get("error"),
        "engine_prefix": wsl_result.get("engine_prefix"),
    }
    if not wsl_result.get("ok"):
        return probe
    for name, eng in (wsl_result.get("engines") or {}).items():
        key = f"{name} (wsl)"
        probe.engines[key] = (eng.get("version") or "present") if eng.get("present") else None
    return probe


def _main(argv: list[str] | None = None) -> int:
    """CLI:人类可读引擎清单(默认)或 JSON。退出码:0 全部就绪;1 不可达;2 引擎不全。"""
    import argparse
    import json

    ap = argparse.ArgumentParser(
        description="探测 WSL 发行版内的 InSAR 引擎(ISCE2/MintPy/SNAPHU)")
    ap.add_argument("--distro", default="insar", help="WSL 发行版名(默认 insar)")
    ap.add_argument("--timeout", type=float, default=60.0, help="单条探测命令超时秒数")
    ap.add_argument("--json", action="store_true", help="输出 JSON(默认人类可读清单)")
    args = ap.parse_args(argv)

    result = probe_wsl_engines(distro=args.distro, timeout=args.timeout)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        state = "可达" if result["ok"] else f"不可达({result['error']})"
        print(f"WSL 引擎探测  distro={result['distro']}  {state}")
        if result["engine_prefix"]:
            print(f"  conda env: {result['engine_prefix']}")
        for name, eng in result["engines"].items():
            if eng["present"]:
                print(f"  [ok] {name:<7} {eng['version'] or 'present':<14} {eng['path']}")
            else:
                print(f"  [--] {name:<7} {eng['error'] or '未安装'}")
    if not result["ok"]:
        return 1
    if not all(e["present"] for e in result["engines"].values()):
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI 入口(wsl_setup.ps1 验证段调用)
    raise SystemExit(_main())
