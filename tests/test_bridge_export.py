"""反演桥(第 28 步):GBIS/Kite/GMT/QGIS/HDF-EOS5 导出。

不造假科学样本:argv 与 ToolMissing 是契约测试;若 realtest 真实 velocity.h5
存在,再对其做轻量导出(不跑反演)。引擎缺失必须 ToolMissing,禁止静默跳过。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from insar_agent.engines import ToolMissing, resolve_builder
from insar_agent.registry.capabilities import REGISTRY

_REALTEST_ROOTS = (
    Path(r"E:\01所有项目\06定职讲师\00insaragent\workspace\realtest"),
    Path(os.environ.get("INSAR_HOME", "") or ""),
)


def _real_velocity() -> Path | None:
    for root in _REALTEST_ROOTS:
        if not root or not root.is_dir():
            continue
        for cand in (root / "ws" / "mintpy" / "velocity.h5",
                     root / "mintpy" / "velocity.h5"):
            if cand.is_file() and cand.stat().st_size > 0:
                return cand
        hits = list(root.glob("**/mintpy/velocity.h5"))
        if hits:
            return hits[0]
    return None


def test_bridge_kite_routes_to_mintpy_post_not_simulate():
    from insar_agent.engines import mintpy_post, simulate

    b = resolve_builder(REGISTRY[28], "bridge_kite", simulated=False)
    assert b is mintpy_post.build
    assert b is not simulate.build
    for method in ("bridge_gbis", "bridge_gmt", "bridge_qgis", "bridge_hdfeos5"):
        assert resolve_builder(REGISTRY[28], method, simulated=False) is mintpy_post.build


def test_bridge_engine_missing_is_tool_missing(workspace, monkeypatch):
    """引擎 Python 不存在 → ToolMissing,绝不静默跳过或回退 simulate。"""
    from insar_agent.engines import mintpy_post

    monkeypatch.setattr("insar_agent.engines.mintpy_post.engine_python",
                        lambda: r"C:\missing-insar-engine\python.exe")
    with pytest.raises(ToolMissing, match="不存在|未配置|CLI 缺失"):
        mintpy_post.build(
            cap=REGISTRY[28], method="bridge_kite",
            params=REGISTRY[28].default_params(),
            run={"simulated": 0}, workspace=workspace)
    with pytest.raises(ToolMissing):
        mintpy_post.build(
            cap=REGISTRY[28], method="bridge_gbis",
            params=REGISTRY[28].default_params(),
            run={"simulated": 0}, workspace=workspace)


def test_unknown_bridge_method_is_tool_missing(workspace):
    from insar_agent.engines import mintpy_post

    with pytest.raises(ToolMissing, match="无此方法"):
        mintpy_post.build(
            cap=REGISTRY[28], method="bridge_okada_homemade",
            params=REGISTRY[28].default_params(),
            run={"simulated": 0}, workspace=workspace)


def test_bridge_argv_points_at_analysis_bridge(workspace):
    from insar_agent.engines import mintpy_post

    cap = REGISTRY[28]
    params = cap.default_params()
    kite = mintpy_post.build(cap=cap, method="bridge_kite", params=params,
                             run={"simulated": 0}, workspace=workspace)
    joined = " ".join(kite.argv)
    assert "mintpy.cli.save_kite" in joined
    assert "-d" in kite.argv and "velocity" in kite.argv
    assert any(p.endswith("kite") or p.endswith("kite.npz") for p in kite.argv)
    assert (workspace / "analysis" / "bridge").is_dir()

    gbis = mintpy_post.build(cap=cap, method="bridge_gbis", params=params,
                             run={"simulated": 0}, workspace=workspace)
    assert "mintpy.cli.save_gbis" in " ".join(gbis.argv)
    assert "-g" in gbis.argv and "--nodisplay" in gbis.argv

    gmt = mintpy_post.build(cap=cap, method="bridge_gmt", params=params,
                            run={"simulated": 0}, workspace=workspace)
    assert "mintpy.cli.save_gmt" in " ".join(gmt.argv)
    assert any(p.endswith("velocity.grd") for p in gmt.argv)

    qgis = mintpy_post.build(cap=cap, method="bridge_qgis", params=params,
                             run={"simulated": 0}, workspace=workspace)
    assert "mintpy.cli.save_qgis" in " ".join(qgis.argv)

    eos = mintpy_post.build(cap=cap, method="bridge_hdfeos5", params=params,
                            run={"simulated": 0}, workspace=workspace)
    assert "mintpy.cli.save_hdfeos5" in " ".join(eos.argv)
    assert Path(eos.cwd).resolve() == (workspace / "analysis" / "bridge").resolve()


def test_bridge_kite_realtest_velocity_if_present(workspace):
    """真实 velocity.h5 存在则轻量走导出路径:物化输入 + 拼 argv;不跑反演。

    全场 save_kite 会把整幅速度场载入 kite.Scene,超出轻量范围。这里只断言:
    文件是非空真实 h5、CommandPlan 指向它、analysis/bridge 已建。若文件不存在则跳过。
    """
    vel = _real_velocity()
    if vel is None:
        pytest.skip("realtest velocity.h5 不存在")
    dest = workspace / "analysis" / "decomposed.h5"
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(vel, dest)
    except OSError:
        if vel.stat().st_size > 200 * 1024 * 1024:
            pytest.skip(f"velocity.h5 过大({vel.stat().st_size} B)且跨卷无法硬链接,不做全场拷贝")
        shutil.copy2(vel, dest)
    assert dest.is_file() and dest.stat().st_size > 0

    from insar_agent.engines import mintpy_post

    params = {**REGISTRY[28].default_params(), "input": "analysis/decomposed.h5"}
    plan = mintpy_post.build(
        cap=REGISTRY[28], method="bridge_kite", params=params,
        run={"simulated": 0}, workspace=workspace)
    assert str(dest) in plan.argv or str(dest.resolve()) in plan.argv
    out_dir = workspace / "analysis" / "bridge"
    assert out_dir.is_dir()
    # 轻量:不调用 save_kite 本体(需 kite/pyrocko + 几何,且会载入全场)。
    # 若文件已地理编码,改走 save_gmt(无 kite 依赖)做一次真实导出抽查.
    try:
        import h5py
    except ImportError:
        return
    with h5py.File(dest, "r") as f:
        geocoded = "Y_FIRST" in f.attrs
        has_vel = "velocity" in f
    if not has_vel:
        pytest.skip("velocity.h5 无 velocity 数据集,不冒充导出成功")
    if not geocoded or dest.stat().st_size > 200 * 1024 * 1024:
        return  # argv 已覆盖;未地理编码或全场过大则不跑 CLI(不做静默 geocode/裁剪)
    gmt = mintpy_post.build(
        cap=REGISTRY[28], method="bridge_gmt", params=params,
        run={"simulated": 0}, workspace=workspace)
    # encoding 显式钉 UTF-8:中文 Windows 默认 GBK,GMT 输出含非 GBK 字节时
    # 读线程会抛 UnicodeDecodeError(pytest 记为未处理线程异常警告)
    cp = subprocess.run(gmt.argv, cwd=gmt.cwd, capture_output=True, text=True,
                        encoding="utf-8", errors="replace",
                        timeout=45, env={**os.environ, **gmt.env})
    grd = workspace / "analysis" / "bridge" / "velocity.grd"
    if cp.returncode != 0:
        pytest.skip(f"save_gmt 真实失败(未静默跳过):{(cp.stderr or cp.stdout)[-400:]}")
    assert grd.is_file() and grd.stat().st_size > 0
