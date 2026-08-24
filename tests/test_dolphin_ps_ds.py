"""R4 dolphin_ps_ds:命令快照含 dolphin config/run,不真跑 Dolphin。"""

from __future__ import annotations

from insar_agent.engines import dolphin, resolve_builder
from insar_agent.registry.capabilities import REGISTRY


def test_dolphin_declared_not_default():
    cap = REGISTRY[7]
    m = cap.method("dolphin_ps_ds")
    assert m is not None
    assert m.engine == "dolphin"
    assert "PS/DS" in m.why or "相位连接" in m.why
    assert cap.default_method == "mintpy_sbas"


def test_resolve_builder_returns_build_without_path_check():
    """dolphin 不在 PATH 时 resolve_builder 仍返回 build(运行期再失败)。"""
    assert resolve_builder(REGISTRY[7], "dolphin_ps_ds", simulated=False) is dolphin.build


def test_dolphin_command_snapshot_argv(workspace):
    plan = dolphin.build(
        cap=REGISTRY[7], method="dolphin_ps_ds", params={},
        run={"simulated": 0}, workspace=workspace)
    argv_blob = " ".join(plan.argv)
    script = plan.files["dolphin/run_dolphin.py"]
    assert "dolphin" in argv_blob
    assert "dolphin" in script
    assert "config" in script and "run" in script
    assert "bash" not in plan.argv
    assert "-lc" not in plan.argv
    assert "['dolphin', 'config'" in script or '"dolphin", "config"' in script
    assert "velocity.h5" in script  # 注释:不假装写出
    assert "dolphin_config.yaml" in script
    assert "data/slc" in script


def test_dolphin_slc_param_baked(workspace):
    plan = dolphin.build(
        cap=REGISTRY[7], method="dolphin_ps_ds",
        params={"source": "data/cslc"}, run={"simulated": 0},
        workspace=workspace)
    script = plan.files["dolphin/run_dolphin.py"]
    assert "data/cslc" in script
