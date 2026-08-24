"""GAMMA 风格 SLC .par 几何自洽(DESIGN.md §4.2 难点 1)。

range 轴是斜距。near / center / far 必须与
``far = near + (width - 1) * range_pixel_spacing`` 互洽,否则后续 bperp
与高程误差估计会崩。look / incidence 由卫星—地心—目标三角形导出,
不能直接喂 ISCE2 的 los.rdr 充当 par 字段。
"""

from __future__ import annotations

import math
import re
from pathlib import Path

# 用户验收:near/center/far 与 spacing*width 相对误差上限
RANGE_REL_TOL = 1e-3


def slant_ranges(*, near_range: float, range_pixel_spacing: float,
                 width: int) -> tuple[float, float, float]:
    """返回 (near, center, far) 斜距,单位与输入相同(米)。

    GAMMA/ISCE 惯例:第 0 个 range 像元在 near_range,相邻像元间隔
    range_pixel_spacing,故
      far    = near + (width - 1) * dr
      center = near + ((width - 1) / 2) * dr
    """
    if width < 1:
        raise ValueError(f"range_samples/width 必须 ≥ 1,得到 {width}")
    if range_pixel_spacing <= 0:
        raise ValueError(f"range_pixel_spacing 必须 > 0,得到 {range_pixel_spacing}")
    if near_range <= 0:
        raise ValueError(f"near_range_slc 必须 > 0,得到 {near_range}")
    span = (width - 1) * range_pixel_spacing
    far = near_range + span
    center = near_range + 0.5 * span
    return near_range, center, far


def look_angle_rad(se: float, rg: float, re: float) -> float:
    """卫星处 look 角(弧度)。

    DESIGN.md / GAMMA ISP::

        look = arccos((se² + rg² - re²) / (2 · se · rg))

    三角形:卫星 S — 地心 O — 目标 T; OS=se (sar_to_earth_center),
    OT=re (earth_radius_below_sensor), ST=rg (slant range)。
    星下点方向 SO 与视线 ST 的夹角即 look。
    """
    _require_triangle(se, rg, re)
    denom = 2.0 * se * rg
    cos_look = (se * se + rg * rg - re * re) / denom
    cos_look = max(-1.0, min(1.0, cos_look))
    return math.acos(cos_look)


def incidence_angle_rad(se: float, rg: float, re: float) -> float:
    """地面入射角(弧度)。球面地球正弦定理::

        sin(incidence) / se = sin(look) / re

    入射角大于 look(地球曲率)。asin 值域对侧视 SAR 落在 (0, π/2)。
    """
    look = look_angle_rad(se, rg, re)
    s = (se / re) * math.sin(look)
    s = max(-1.0, min(1.0, s))
    return math.asin(s)


def rel_err(actual: float, expected: float) -> float:
    return abs(actual - expected) / max(abs(expected), 1e-15)


def assert_ranges_consistent(*, near: float, center: float, far: float,
                             range_pixel_spacing: float, width: int,
                             tol: float = RANGE_REL_TOL) -> None:
    n0, c0, f0 = slant_ranges(near_range=near, range_pixel_spacing=range_pixel_spacing,
                              width=width)
    for name, got, exp in (("near_range_slc", near, n0),
                           ("center_range_slc", center, c0),
                           ("far_range_slc", far, f0)):
        if rel_err(got, exp) > tol:
            raise ValueError(
                f"{name}={got} 与 spacing*width 推导值 {exp} 相对误差 "
                f"{rel_err(got, exp):.3e} > {tol}")


def assert_incidence_consistent(provided_deg: float, *, se: float, rg: float,
                                re: float, tol: float = RANGE_REL_TOL) -> None:
    derived = math.degrees(incidence_angle_rad(se, rg, re))
    if rel_err(provided_deg, derived) > tol:
        raise ValueError(
            f"incidence_angle={provided_deg}° 与 se/re/rg 推导值 {derived:.6f}° "
            f"相对误差 {rel_err(provided_deg, derived):.3e} > {tol}")


def format_par(*, title: str, width: int, length: int,
               range_pixel_spacing: float, azimuth_pixel_spacing: float,
               near: float, center: float, far: float,
               look_deg: float, incidence_deg: float,
               se: float, re: float, heading: float,
               radar_frequency: float, prf: float,
               sensor: str, date_str: str) -> str:
    """GAMMA ISP 风格文本。字段顺序稳定,便于 diff / 测试解析。"""
    assert_ranges_consistent(near=near, center=center, far=far,
                             range_pixel_spacing=range_pixel_spacing, width=width)
    return (
        "Gamma ISP Image Parameter File (ISCE2→PyStamps)\n"
        f"title: {title}\n"
        f"sensor: {sensor}\n"
        f"date: {date_str}\n"
        "image_geometry:                 SLANT_RANGE\n"
        f"range_samples:                 {width}\n"
        f"azimuth_lines:                 {length}\n"
        f"range_pixel_spacing:           {range_pixel_spacing:.7f}   m\n"
        f"azimuth_pixel_spacing:         {azimuth_pixel_spacing:.7f}   m\n"
        f"near_range_slc:                {near:.6f}   m\n"
        f"center_range_slc:              {center:.6f}   m\n"
        f"far_range_slc:                 {far:.6f}   m\n"
        f"look_angle:                    {look_deg:.6f}   degrees\n"
        f"incidence_angle:               {incidence_deg:.6f}   degrees\n"
        f"sar_to_earth_center:           {se:.6f}   m\n"
        f"earth_radius_below_sensor:     {re:.6f}   m\n"
        f"heading:                       {heading:.6f}   degrees\n"
        f"radar_frequency:               {radar_frequency:.7e}   Hz\n"
        f"prf:                           {prf:.6f}   Hz\n"
    )


def write_par(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def parse_par(text: str) -> dict[str, str]:
    """解析 `key: value [unit]` 行;value 取第一个空白分隔字段。"""
    out: dict[str, str] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, rest = line.split(":", 1)
        key = key.strip()
        if not key:
            continue
        parts = rest.split()
        if not parts:
            out[key] = ""
            continue
        out[key] = rest.strip() if key in ("title", "sensor", "date") else parts[0]
    return out


def _require_triangle(se: float, rg: float, re: float) -> None:
    if min(se, rg, re) <= 0:
        raise ValueError(f"se/rg/re 必须 > 0,得到 se={se} rg={rg} re={re}")
    if se + re <= rg or se + rg <= re or re + rg <= se:
        raise ValueError(
            f"se/rg/re 不构成三角形(se={se}, rg={rg}, re={re})")


_XML_WH = re.compile(
    r'<property name="(width|length)">\s*<value>\s*(\d+)\s*</value>',
    re.IGNORECASE,
)


def parse_isce_rdr_xml_wh(xml_text: str) -> tuple[int, int] | None:
    """从 ISCE2 *.rdr.xml 抽 (width, length);缺一则 None。"""
    found: dict[str, int] = {}
    for m in _XML_WH.finditer(xml_text):
        found[m.group(1).lower()] = int(m.group(2))
    if "width" in found and "length" in found:
        return found["width"], found["length"]
    return None
