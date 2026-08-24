"""ISCE2→PyStamps 桥:合成布局上验证 par 自洽 / TCN / big-endian。

不跑真实 ISCE2/PyStamps 全链。夹具是最小合成树,不是验收用的科学数据。
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from insar_agent.engines.bridges import isce2_to_pystamps as bridge
from insar_agent.engines.bridges.baseline_tcn import (
    parse_base_tcn,
    perp_par_from_tcn,
    tcn_from_perp_par,
)
from insar_agent.engines.bridges.gamma_geom import (
    RANGE_REL_TOL,
    incidence_angle_rad,
    look_angle_rad,
    parse_par,
    rel_err,
    slant_ranges,
)
from insar_agent.engines.bridges.prep_isce import build_prep_command
from insar_agent.engines.pystamps import build as pystamps_build
from insar_agent.registry.capabilities import REGISTRY

NEAR = 800_000.0
DR = 2.33
DA = 14.0
SE = 7_000_000.0
RE = 6_371_000.0
PAIR = "20190704_20190716"


def _phase(length: int = 2, width: int = 4) -> np.ndarray:
    n = length * width
    return (np.arange(n, dtype=np.float32).reshape(length, width) * 0.15 + 0.05)


def write_synthetic_layout(
    ws: Path, *,
    pair: str = PAIR,
    phase: np.ndarray | None = None,
    tcn: tuple[float, float, float] = (0.0, 85.3, -12.1),
    bperp_bpar: tuple[float, float] | None = None,
    skip_baseline: bool = False,
    use_interferograms: bool = False,
    complex_int: bool = False,
    extra_pair: str | None = None,
    extra_baseline: bool = True,
    incidence: float | None = None,
    rdr_xml_wh: tuple[int, int] | None = None,
) -> np.ndarray:
    """最小合成 ISCE2 树。返回写出的相位(float32, 用于 BE 对拍)。"""
    arr = _phase() if phase is None else np.asarray(phase, dtype=np.float32)
    length, width = arr.shape
    slc = ws / "isce2/merged/SLC"
    ifg = ws / "isce2/merged/interferograms"
    geom = ws / "isce2/merged/geom_reference"
    base = ws / "isce2/baselines"
    for d in (slc, ifg, geom, base):
        d.mkdir(parents=True, exist_ok=True)
    parent = ifg if use_interferograms else slc
    if use_interferograms:
        pair_dir = parent / pair
        pair_dir.mkdir(parents=True, exist_ok=True)
        target = pair_dir / "filt_fine.int"
    else:
        target = slc / f"{pair}.slc"
    if complex_int:
        cpx = np.exp(1j * arr).astype(np.complex64)
        np.asarray(cpx, dtype="<c8").tofile(target)
    else:
        arr.astype("<f4").tofile(target)
    payload = {
        "range_samples": width,
        "azimuth_lines": length,
        "range_pixel_spacing": DR,
        "azimuth_pixel_spacing": DA,
        "near_range_slc": NEAR,
        "sar_to_earth_center": SE,
        "earth_radius_below_sensor": RE,
        "heading": -12.0,
        "radar_frequency": 5.405e9,
        "prf": 1717.0,
        "sensor": "Sentinel-1",
    }
    if incidence is not None:
        payload["incidence_angle"] = incidence
    (geom / "geom.json").write_text(json.dumps(payload), encoding="utf-8")
    if rdr_xml_wh is not None:
        w, nrows = rdr_xml_wh
        xml = (f'<imageFile><property name="width"><value>{w}</value></property>'
               f'<property name="length"><value>{nrows}</value></property></imageFile>')
        (geom / "lat.rdr").write_bytes(b"x")
        (geom / "lat.rdr.xml").write_text(xml, encoding="utf-8")
    if not skip_baseline:
        if bperp_bpar is not None:
            rec = {"bperp": bperp_bpar[0], "bpar": bperp_bpar[1]}
        else:
            rec = {"t": tcn[0], "c": tcn[1], "n": tcn[2]}
        (base / f"{pair}.json").write_text(json.dumps(rec), encoding="utf-8")
    if extra_pair:
        extra_phase = arr.copy()
        extra_phase.astype("<f4").tofile(slc / f"{extra_pair}.slc")
        if extra_baseline:
            (base / f"{extra_pair}.json").write_text(
                json.dumps({"t": 1.0, "c": 10.0, "n": -2.0}), encoding="utf-8")
    return arr


def test_prep_isce_argv_hyp3_vs_isce(tmp_path):
    hyp3 = build_prep_command("hyp3", tmp_path)
    assert hyp3[0] == "bash" and "prep_hyp3.py" in hyp3[-1]
    assert "../hyp3/*/*.tif" in hyp3[-1]
    assert "prep_isce.py" not in hyp3[-1]
    isce = build_prep_command("isce2", tmp_path)
    assert "prep_isce.py" in isce[-1]
    assert "../isce2/merged/interferograms" in isce[-1]
    assert "../isce2/reference" in isce[-1]
    assert "prep_hyp3.py" not in isce[-1]
    other = build_prep_command("anything-else", tmp_path)
    assert other == isce


def test_slant_ranges_and_look_formulas():
    width = 11
    near, center, far = slant_ranges(near_range=NEAR, range_pixel_spacing=DR, width=width)
    assert rel_err(far, NEAR + (width - 1) * DR) < RANGE_REL_TOL
    assert rel_err(center, NEAR + ((width - 1) / 2) * DR) < RANGE_REL_TOL
    look = look_angle_rad(SE, center, RE)
    inc = incidence_angle_rad(SE, center, RE)
    assert 0 < look < inc < math.pi / 2


def test_tcn_roundtrip_matches_perp_par():
    look = look_angle_rad(SE, NEAR + 5 * DR, RE)
    t, c, n = 1.5, 80.0, -20.0
    bperp, bpar = perp_par_from_tcn(c=c, n=n, look_rad=look)
    t2, c2, n2 = tcn_from_perp_par(bperp=bperp, bpar=bpar, look_rad=look, along_track=t)
    assert t2 == pytest.approx(t)
    assert c2 == pytest.approx(c)
    assert n2 == pytest.approx(n)


def test_missing_dirs_environment_not_ready(tmp_path):
    assert bridge.check_ready(tmp_path) == list(bridge.REQUIRED_INPUTS)
    with pytest.raises(bridge.EnvironmentNotReady, match="桥前置输入缺失"):
        bridge.convert(tmp_path)


def test_empty_dirs_environment_not_ready(tmp_path):
    for rel in bridge.REQUIRED_INPUTS:
        (tmp_path / rel).mkdir(parents=True)
    assert bridge.check_ready(tmp_path) == []
    with pytest.raises(bridge.EnvironmentNotReady, match="内容不完整"):
        bridge.convert(tmp_path)
    assert not list(tmp_path.rglob("*.diff"))


def test_convert_writes_be_diff_par_base(tmp_path):
    tcn = (0.25, 85.3, -12.1)
    phase = write_synthetic_layout(tmp_path, tcn=tcn)
    written = bridge.convert(tmp_path)
    work = tmp_path / "pystamps/work"
    diff = work / f"{PAIR}.diff"
    par = work / f"{PAIR}.par"
    base = work / f"{PAIR}.base"
    assert {p.resolve() for p in written} == {diff.resolve(), par.resolve(), base.resolve()}

    be = np.fromfile(diff, dtype=">f4").reshape(phase.shape)
    assert be.dtype == np.dtype(">f4")
    np.testing.assert_allclose(be, phase, rtol=0, atol=1e-6)
    assert diff.read_bytes() == np.asarray(phase, np.float32).astype(">f4").tobytes()
    le_bytes = np.asarray(phase, np.float32).astype("<f4").tobytes()
    assert diff.read_bytes() != le_bytes

    fields = parse_par(par.read_text(encoding="utf-8"))
    width = phase.shape[1]
    near, center, far = slant_ranges(
        near_range=NEAR, range_pixel_spacing=DR, width=width)
    assert rel_err(float(fields["near_range_slc"]), near) < RANGE_REL_TOL
    assert rel_err(float(fields["center_range_slc"]), center) < RANGE_REL_TOL
    assert rel_err(float(fields["far_range_slc"]), far) < RANGE_REL_TOL
    span = (width - 1) * float(fields["range_pixel_spacing"])
    assert rel_err(float(fields["far_range_slc"]) - float(fields["near_range_slc"]),
                   span) < RANGE_REL_TOL

    got = parse_base_tcn(base.read_text(encoding="utf-8"))
    assert got[0] == pytest.approx(tcn[0])
    assert got[1] == pytest.approx(tcn[1])
    assert got[2] == pytest.approx(tcn[2])


def test_convert_bperp_bpar_to_tcn(tmp_path):
    phase = _phase()
    _, center, _ = slant_ranges(near_range=NEAR, range_pixel_spacing=DR,
                                width=phase.shape[1])
    look = look_angle_rad(SE, center, RE)
    want = (0.0, 80.0, -20.0)
    bperp, bpar = perp_par_from_tcn(c=want[1], n=want[2], look_rad=look)
    write_synthetic_layout(tmp_path, phase=phase, bperp_bpar=(bperp, bpar))
    bridge.convert(tmp_path)
    got = parse_base_tcn(
        (tmp_path / "pystamps/work" / f"{PAIR}.base").read_text(encoding="utf-8"))
    assert got[0] == pytest.approx(0.0)
    assert got[1] == pytest.approx(want[1])
    assert got[2] == pytest.approx(want[2])


def test_missing_baseline_refuses_pair_without_writing(tmp_path):
    write_synthetic_layout(
        tmp_path, extra_pair="20190716_20190728", extra_baseline=False)
    with pytest.raises(bridge.EnvironmentNotReady, match="20190716_20190728"):
        bridge.convert(tmp_path)
    assert not list(tmp_path.rglob("*.diff"))
    assert not list((tmp_path / "pystamps").glob("**/*.base"))


def test_complex_interferogram_angle_and_ifg_dir(tmp_path):
    phase = _phase()
    write_synthetic_layout(tmp_path, phase=phase, use_interferograms=True,
                           complex_int=True)
    bridge.convert(tmp_path)
    be = np.fromfile(tmp_path / "pystamps/work" / f"{PAIR}.diff", dtype=">f4")
    np.testing.assert_allclose(be.reshape(phase.shape), phase, atol=1e-6)


def test_inconsistent_incidence_rejected(tmp_path):
    write_synthetic_layout(tmp_path, incidence=1.0)
    with pytest.raises(ValueError, match="incidence_angle"):
        bridge.convert(tmp_path)


def test_rdr_xml_width_mismatch(tmp_path):
    write_synthetic_layout(tmp_path, rdr_xml_wh=(99, 99))
    with pytest.raises(ValueError, match="rdr xml"):
        bridge.convert(tmp_path)


def test_pystamps_script_calls_bridge(workspace):
    plan = pystamps_build(
        cap=REGISTRY[7], method="pystamps_ps", params={},
        run={"simulated": 0}, workspace=workspace)
    bridge_py = plan.files["pystamps/run_bridge.py"]
    sh = plan.files["pystamps/run_ps.sh"]
    assert "convert(" in bridge_py
    assert "EnvironmentNotReady" in bridge_py
    assert "isce2_to_pystamps" in bridge_py
    assert "run_bridge.py" in sh
    assert "python -m pystamps" in sh
