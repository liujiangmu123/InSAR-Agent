"""安装执行器:只跑写死的 argv,不接受外部命令;手动引擎拒绝代装。"""

from __future__ import annotations

import subprocess

from insar_agent.runtime.install_runner import plan_auto_install, run_install


def test_manual_engines_cannot_auto_install():
    for engine in ("snap", "pystamps"):
        plan = plan_auto_install(engine)
        assert plan["kind"] == "manual"
        assert plan["argv"] is None
        out = run_install(engine)
        assert out["ok"] is False
        assert "不能代装" in out["summary"] or "无官方" in out["summary"]


def test_unknown_engine_rejected():
    out = run_install("quantum-unwrapper")
    assert out["ok"] is False
    assert out["kind"] == "unknown"


def test_conda_plan_uses_argv_not_shell_string(monkeypatch, tmp_path):
    conda = tmp_path / "conda.exe"
    conda.write_text("", encoding="utf-8")
    monkeypatch.setenv("INSAR_CONDA", str(conda))
    plan = plan_auto_install("gdal")
    assert plan["kind"] == "conda"
    assert plan["argv"][0] == str(conda)
    assert "-y" in plan["argv"]
    assert all(isinstance(x, str) for x in plan["argv"])


def test_run_install_uses_injected_runner(monkeypatch, tmp_path):
    conda = tmp_path / "conda.exe"
    conda.write_text("", encoding="utf-8")
    monkeypatch.setenv("INSAR_CONDA", str(conda))
    seen = []

    def runner(argv, timeout):
        seen.append((argv, timeout))
        return subprocess.CompletedProcess(argv, 0, stdout="done\n", stderr="")

    out = run_install("pyaps", runner=runner, timeout=12)
    assert out["ok"] is True
    assert seen and seen[0][0][0] == str(conda)
    assert "pyaps3" in seen[0][0]
    assert "成功" in out["summary"]
