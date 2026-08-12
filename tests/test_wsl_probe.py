"""wsl_probe 测试:假 runner 全覆盖(存在/缺失/超时/wsl 不可达)、merge 语义、脚本静态检查。

不触碰真实 WSL:所有探测走注入的 FakeRunner;静态检查刻意排除
System32\\bash.exe(WSL 转发器),避免在主线导入发行版期间拉起 WSL。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from insar_agent.runtime.probe import ProbeResult
from insar_agent.runtime.wsl_probe import merge_wsl_probe, probe_wsl_engines

REPO = Path(__file__).resolve().parents[1]
SETUP_SH = REPO / "scripts" / "wsl_setup.sh"
SETUP_PS1 = REPO / "scripts" / "wsl_setup.ps1"
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

PREFIX = "/opt/miniforge3/envs/insar"
ISCE_PATH = f"{PREFIX}/lib/python3.11/site-packages/isce/applications/topsApp.py"
MINTPY_PATH = f"{PREFIX}/bin/smallbaselineApp.py"
SNAPHU_PATH = f"{PREFIX}/bin/snaphu"


class FakeRunner:
    """按 bash -lc 脚本的子串匹配返回预设结果;记录全部调用供断言。

    rules: list[(脚本子串, (rc, stdout, stderr) | Exception)]
    """

    def __init__(self, rules):
        self.rules = rules
        self.calls: list[tuple[list[str], float]] = []

    def __call__(self, argv: list[str], timeout: float) -> subprocess.CompletedProcess:
        self.calls.append((list(argv), timeout))
        script = argv[-1]
        for sub, res in self.rules:
            if sub in script:
                if isinstance(res, Exception):
                    raise res
                rc, out, err = res
                return subprocess.CompletedProcess(argv, rc, stdout=out, stderr=err)
        return subprocess.CompletedProcess(argv, 127, stdout="", stderr=f"no rule for: {script}")

    def scripts(self) -> list[str]:
        return [argv[-1] for argv, _ in self.calls]


def rules_all_present():
    return [
        ("echo __insar_wsl_ok__", (0, "__insar_wsl_ok__", "")),
        ("INSAR_ENGINE_PREFIX", (0, PREFIX, "")),
        ("command -v topsApp.py", (0, ISCE_PATH, "")),
        ("command -v smallbaselineApp.py", (0, MINTPY_PATH, "")),
        ("command -v snaphu", (0, SNAPHU_PATH, "")),
        ("import isce", (0, "2.6.3", "")),
        ("import mintpy", (0, "1.6.1", "")),
        # snaphu 空跑:版本在用法输出首行,rc 非零是正常现象
        ("head -n 2", (1, "snaphu v2.0.7\nusage: snaphu [options] infile linelength", "")),
    ]


def with_rule(rules, sub, res):
    """替换 rules 中指定子串的规则。"""
    return [(s, res if s == sub else r) for s, r in rules]


# ---------------------------------------------------------------- 存在:全绿

def test_all_engines_present_with_versions():
    runner = FakeRunner(rules_all_present())
    result = probe_wsl_engines(runner=runner)

    assert result["ok"] is True and result["error"] is None
    assert result["distro"] == "insar"
    assert result["engine_prefix"] == PREFIX

    eng = result["engines"]
    assert set(eng) == {"isce2", "mintpy", "snaphu"}
    assert eng["isce2"] == {"present": True, "path": ISCE_PATH, "version": "2.6.3", "error": None}
    assert eng["mintpy"]["present"] and eng["mintpy"]["version"] == "1.6.1"
    assert eng["snaphu"]["present"] and eng["snaphu"]["version"] == "2.0.7"
    # 调用次数:可达性 1 + 前缀 1 + 存在性 3 + 版本 3
    assert len(runner.calls) == 8


def test_argv_contract_login_shell_root():
    """探测命令必须走 wsl.exe -d <distro> -u root --exec bash -lc:
    登录 shell 才会加载 /etc/profile.d/insar.sh,PATH 才带引擎。"""
    runner = FakeRunner(rules_all_present())
    probe_wsl_engines(runner=runner)
    for argv, timeout in runner.calls:
        assert argv[:5] == ["wsl.exe", "-d", "insar", "-u", "root"]
        assert argv[5:8] == ["--exec", "bash", "-lc"]
        assert timeout > 0


def test_custom_distro_and_injected_runner():
    runner = FakeRunner(rules_all_present())
    result = probe_wsl_engines(distro="insar-dev", runner=runner, timeout=7.5)
    assert result["distro"] == "insar-dev"
    assert all(argv[2] == "insar-dev" for argv, _ in runner.calls)
    assert all(t == 7.5 for _, t in runner.calls)


# ---------------------------------------------------------------- 缺失

def test_engine_missing_no_version_call():
    rules = with_rule(rules_all_present(), "command -v snaphu", (1, "", ""))
    runner = FakeRunner(rules)
    result = probe_wsl_engines(runner=runner)

    assert result["ok"] is True
    snaphu = result["engines"]["snaphu"]
    assert snaphu == {"present": False, "path": None, "version": None, "error": None}
    # 缺失的引擎不再发版本命令
    assert not any("head -n 2" in s for s in runner.scripts())
    # 其余引擎不受影响
    assert result["engines"]["isce2"]["present"] and result["engines"]["mintpy"]["present"]


def test_version_failure_keeps_present():
    """版本命令失败(rc 非零且无可解析版本)不影响 present 判定。"""
    rules = with_rule(rules_all_present(), "import isce",
                      (1, "", "Traceback (most recent call last): ImportError"))
    result = probe_wsl_engines(runner=FakeRunner(rules))
    isce2 = result["engines"]["isce2"]
    assert isce2["present"] is True and isce2["path"] == ISCE_PATH
    assert isce2["version"] is None and isce2["error"] is None


# ---------------------------------------------------------------- 超时

def test_engine_check_timeout_marks_error_and_continues():
    rules = with_rule(rules_all_present(), "command -v topsApp.py",
                      subprocess.TimeoutExpired(cmd=["wsl.exe"], timeout=60))
    result = probe_wsl_engines(runner=FakeRunner(rules))

    assert result["ok"] is True  # 单引擎超时不拖垮整体
    isce2 = result["engines"]["isce2"]
    assert isce2["present"] is False and "timeout" in isce2["error"]
    assert result["engines"]["mintpy"]["present"]
    assert result["engines"]["snaphu"]["present"]


def test_version_timeout_keeps_present_with_error():
    rules = with_rule(rules_all_present(), "import mintpy",
                      subprocess.TimeoutExpired(cmd=["wsl.exe"], timeout=60))
    result = probe_wsl_engines(runner=FakeRunner(rules))
    mintpy = result["engines"]["mintpy"]
    assert mintpy["present"] is True and mintpy["version"] is None
    assert "timeout" in mintpy["error"]


def test_reachability_timeout_stops_probing():
    rules = [("echo __insar_wsl_ok__", subprocess.TimeoutExpired(cmd=["wsl.exe"], timeout=60))]
    runner = FakeRunner(rules)
    result = probe_wsl_engines(runner=runner)

    assert result["ok"] is False and "timeout" in result["error"]
    assert len(runner.calls) == 1  # 不可达即止,不再逐引擎探测
    assert all(not e["present"] for e in result["engines"].values())


# ---------------------------------------------------------------- wsl 不可达

def test_wsl_exe_missing():
    runner = FakeRunner([("echo __insar_wsl_ok__", FileNotFoundError("wsl.exe"))])
    result = probe_wsl_engines(runner=runner)
    assert result["ok"] is False and "wsl.exe" in result["error"]
    assert len(runner.calls) == 1


def test_distro_not_imported():
    runner = FakeRunner([
        ("echo __insar_wsl_ok__", (1, "", "没有指定名称的分发版。")),
    ])
    result = probe_wsl_engines(runner=runner)
    assert result["ok"] is False
    assert "insar" in result["error"] and "分发版" in result["error"]
    assert len(runner.calls) == 1


def test_oserror_normalized():
    runner = FakeRunner([("echo __insar_wsl_ok__", OSError("WinError 1450"))])
    result = probe_wsl_engines(runner=runner)
    assert result["ok"] is False and "启动失败" in result["error"]


# ---------------------------------------------------------------- merge 语义

def _wsl_result(ok=True, **overrides):
    base = {
        "ok": ok,
        "distro": "insar",
        "error": None if ok else "发行版 'insar' 不可达:rc=1",
        "engine_prefix": PREFIX if ok else None,
        "engines": {
            "isce2": {"present": True, "path": ISCE_PATH, "version": "2.6.3", "error": None},
            "mintpy": {"present": True, "path": MINTPY_PATH, "version": None, "error": None},
            "snaphu": {"present": False, "path": None, "version": None, "error": None},
        },
    }
    base.update(overrides)
    return base


def test_merge_adds_wsl_suffixed_engines():
    probe = ProbeResult(engines={"mintpy": None, "gdal": "present"})
    merged = merge_wsl_probe(probe, _wsl_result())

    assert merged is probe  # 原地合并,返回同一对象
    # 来源标注:引擎键带 " (wsl)" 后缀
    assert probe.engines["isce2 (wsl)"] == "2.6.3"
    assert probe.engines["mintpy (wsl)"] == "present"  # 在但取不到版本
    assert probe.engines["snaphu (wsl)"] is None       # 探测过但缺失
    # 原生键不受影响
    assert probe.engines["mintpy"] is None and probe.engines["gdal"] == "present"
    # ProbeResult 语义天然适用
    assert probe.engine_ok("isce2 (wsl)") and not probe.engine_ok("snaphu (wsl)")
    versions = probe.tool_versions()
    assert versions["isce2 (wsl)"] == "2.6.3" and "snaphu (wsl)" not in versions
    # 元数据进 probe.wsl,供解释与面板展示
    meta = probe.wsl["engine_probe"]
    assert meta["ok"] is True and meta["distro"] == "insar" and meta["engine_prefix"] == PREFIX


def test_merge_unreachable_adds_no_engines():
    probe = ProbeResult(engines={"gdal": "present"})
    merge_wsl_probe(probe, _wsl_result(ok=False, engines={}))

    assert not any(k.endswith("(wsl)") for k in probe.engines)
    assert probe.engines == {"gdal": "present"}
    meta = probe.wsl["engine_probe"]
    assert meta["ok"] is False and "不可达" in meta["error"]


def test_probe_then_merge_end_to_end():
    rules = with_rule(rules_all_present(), "command -v snaphu", (1, "", ""))
    result = probe_wsl_engines(runner=FakeRunner(rules))
    probe = merge_wsl_probe(ProbeResult(), result)

    assert probe.engines["isce2 (wsl)"] == "2.6.3"
    assert probe.engines["mintpy (wsl)"] == "1.6.1"
    assert probe.engines["snaphu (wsl)"] is None
    assert probe.to_dict()["wsl"]["engine_probe"]["ok"] is True


# ---------------------------------------------------------------- 脚本静态检查

def _host_bash() -> str | None:
    """找非 WSL 的 bash(Git Bash 等)。System32\\bash.exe 是 WSL 转发器,
    会拉起发行版,静态检查绝不能用它。"""
    cands: list[str] = []
    which = shutil.which("bash")
    if which:
        cands.append(which)
    cands += [r"C:\Program Files\Git\usr\bin\bash.exe", r"C:\Program Files\Git\bin\bash.exe"]
    for c in cands:
        p = Path(c)
        if not p.exists():
            continue
        low = str(p).lower()
        if "system32" in low or "windowsapps" in low:
            continue
        return str(p)
    return None


def test_wsl_setup_sh_bash_syntax(tmp_path):
    bash = _host_bash()
    if bash is None:
        pytest.skip("宿主无可用的非 WSL bash(bash -n 跳过)")
    # 与 ps1 的运行方式一致:剥 CRLF 后再做语法检查(检出端 autocrlf 可能改行尾)
    stripped = tmp_path / "wsl_setup.lf.sh"
    stripped.write_bytes(SETUP_SH.read_bytes().replace(b"\r", b""))
    cp = subprocess.run([bash, "-n", str(stripped)], capture_output=True, text=True,
                        timeout=30, creationflags=_NO_WINDOW)
    assert cp.returncode == 0, f"bash -n 语法错误:\n{cp.stderr}"


def test_wsl_setup_ps1_syntax():
    ps = shutil.which("powershell") or shutil.which("pwsh")
    if ps is None:
        pytest.skip("宿主无 PowerShell(语法检查跳过)")
    script = (
        "$t=$null;$e=$null;"
        f"[void][System.Management.Automation.Language.Parser]::ParseFile('{SETUP_PS1}',[ref]$t,[ref]$e);"
        "if($e.Count -gt 0){$e|ForEach-Object{Write-Output $_.Message};exit 1};exit 0"
    )
    cp = subprocess.run([ps, "-NoProfile", "-NonInteractive", "-Command", script],
                        capture_output=True, text=True, timeout=60, creationflags=_NO_WINDOW)
    assert cp.returncode == 0, f"PowerShell 语法错误:\n{cp.stdout}\n{cp.stderr}"


def _strip_ps_comments(text: str) -> str:
    """去掉 PowerShell 块注释与行注释,留下真正会执行的代码。"""
    import re

    text = re.sub(r"<#.*?#>", "", text, flags=re.S)
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def test_scripts_contract_markers():
    """脚本关键契约的存在性检查:镜像源、哨兵、目标环境、无窗纪律。"""
    sh = SETUP_SH.read_text(encoding="utf-8")
    assert "mirrors.tuna.tsinghua.edu.cn" in sh          # 清华镜像(apt/miniforge/conda-forge)
    assert "/opt/.insar_setup_done_" in sh               # 幂等哨兵
    assert "/opt/miniforge3" in sh
    assert "--override-channels" in sh and "python=3.11" in sh
    for pkg in ("isce2", "mintpy", "snaphu", "gdal", "pyaps3"):
        assert pkg in sh
    assert "openblas" in sh                              # libblas 校验
    assert "/home/insar/work" in sh                      # 工作区在 Linux fs(§4.8)
    assert "HDF5_USE_FILE_LOCKING" in sh
    assert "INSAR_ENGINE_PREFIX" in sh                   # probe 契约变量

    ps1 = SETUP_PS1.read_text(encoding="utf-8-sig")
    assert ".wslconfig" in ps1
    for marker in ("memory=40GB", "processors=20", "swap=16GB", "swap.vhdx",
                   "wsl_setup.sh", "wsl_probe"):
        assert marker in ps1
    code = _strip_ps_comments(ps1)
    assert "Start-Process" not in code                   # 无窗纪律:注释可提及,代码不得调用
    # .wslconfig 变更只提示需要 wsl --shutdown,绝不把 --shutdown 当参数执行
    assert "'--shutdown'" not in code and '"--shutdown"' not in code
