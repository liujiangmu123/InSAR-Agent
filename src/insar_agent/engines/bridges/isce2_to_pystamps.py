"""ISCE2 → PyStamps 桥(主 novelty)。

三个物理难点(DESIGN.md:123-132;AGENT-DESIGN §9 Phase 6):
  1. par 几何字段自洽:range/azimuth pixel spacing、incidence、
     near/center/far range 必须互洽(见 gamma_geom)。
  2. TCN 基线:ISCE2 B⊥/B∥ → GAMMA TCN,写入 .base(见 baseline_tcn)。
  3. 全程 big-endian:PyStamps 无条件 .byteswap(),桥输出 .diff 必须 BE。

输入约定(合成与真实共用;不下载数据,最小可测树)::

    isce2/merged/interferograms/          # 若有配对则优先
        YYYYMMDD_YYYYMMDD/filt_fine.int   # complex64 或 float32,LE
        或 YYYYMMDD_YYYYMMDD.int
    isce2/merged/SLC/                     # interferograms 无配对时
        YYYYMMDD_YYYYMMDD.slc             # float32 LE 相位,或 cfloat32 LE
        或 YYYYMMDD_YYYYMMDD/filt_fine.int
    isce2/merged/geom_reference/
        geom.json                         # 标量几何(合成 sidecar,必填标量)
        lat.rdr[.full] / lon.rdr[.full]   # 可选;标准 ISCE2 布局时读宽高
    isce2/baselines/
        YYYYMMDD_YYYYMMDD.json            # {t,c,n} 或 {bperp,bpar}
        或 .txt / .npy / 子目录同名文件

geom.json 必填键:range_samples, azimuth_lines, range_pixel_spacing,
azimuth_pixel_spacing, near_range_slc, sar_to_earth_center,
earth_radius_below_sensor。可选:incidence_angle(须与 se/re/rg 互洽)、
heading, radar_frequency, prf, sensor。

输出::

    pystamps/work/YYYYMMDD_YYYYMMDD.diff  # float32 big-endian 相位
    pystamps/work/YYYYMMDD_YYYYMMDD.par
    pystamps/work/YYYYMMDD_YYYYMMDD.base

单日期 SLC 目录(20190704/20190704.slc.full)不是最小输入 —— 不在此
组网;需要 YYYYMMDD_YYYYMMDD 配对文件。缺任一对的基线则整次 convert
拒绝(先校验再写,不留下残缺 .diff 冒充成功)。
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

from insar_agent.engines.bridges.baseline_tcn import (
    tcn_from_perp_par,
    write_base,
)
from insar_agent.engines.bridges.gamma_geom import (
    assert_incidence_consistent,
    format_par,
    incidence_angle_rad,
    look_angle_rad,
    parse_isce_rdr_xml_wh,
    slant_ranges,
    write_par,
)

DATE_PAIR_RE = re.compile(r"(?P<m>\d{8})_(?P<s>\d{8})")
_PHASE_SUFFIXES = {".slc", ".int", ".diff"}
_INT_NAMES = ("filt_fine.int", "fine.int", "filt.int")
_RDR_NAMES = (
    "lat.rdr.full", "lon.rdr.full", "hgt.rdr.full", "los.rdr.full",
    "lat.rdr", "lon.rdr", "hgt.rdr", "los.rdr",
)
_GEOM_REQUIRED = (
    "range_samples", "azimuth_lines", "range_pixel_spacing",
    "azimuth_pixel_spacing", "near_range_slc", "sar_to_earth_center",
    "earth_radius_below_sensor",
)


class EnvironmentNotReady(RuntimeError):
    """桥的执行环境未就绪(目录缺失或目录在但配对/几何/基线文件空)。"""


REQUIRED_INPUTS = (
    "isce2/merged/SLC",            # 配准后 SLC 堆栈或配对相位
    "isce2/merged/geom_reference", # lat/lon/hgt/los 几何 + geom.json
    "isce2/baselines",             # 基线(TCN 或 bperp/bpar)
)

PLANNED_OUTPUTS = (
    "pystamps/work/*.diff",   # YYYYMMDD_YYYYMMDD.diff(严格命名,BE)
    "pystamps/work/*.par",    # GAMMA 风格参数文件(几何字段自洽)
    "pystamps/work/*.base",   # 基线文件(缺失会静默丢干涉图 —— 输入校验必须拦)
)


@dataclass(frozen=True)
class Geom:
    width: int
    length: int
    range_pixel_spacing: float
    azimuth_pixel_spacing: float
    near: float
    center: float
    far: float
    se: float
    re: float
    look_rad: float
    look_deg: float
    incidence_deg: float
    heading: float
    radar_frequency: float
    prf: float
    sensor: str


@dataclass(frozen=True)
class PairSpec:
    master: str
    slave: str
    phase_path: Path

    @property
    def key(self) -> str:
        return f"{self.master}_{self.slave}"


def check_ready(workspace: Path) -> list[str]:
    """返回缺失的必选目录;空列表 = 目录层就绪(文件层由 convert 再验)。"""
    return [rel for rel in REQUIRED_INPUTS if not (workspace / rel).exists()]


def convert(workspace: Path) -> list[Path]:
    """把 ISCE2 布局写成 PyStamps work 三件套。返回写出的路径。"""
    workspace = Path(workspace)
    missing = check_ready(workspace)
    if missing:
        raise EnvironmentNotReady(
            f"ISCE2→PyStamps 桥前置输入缺失:{missing}。"
            "需要 isce2/merged/SLC、isce2/merged/geom_reference、isce2/baselines。")

    geom_dir = workspace / "isce2/merged/geom_reference"
    pairs = discover_pairs(workspace)
    file_problems: list[str] = []
    if not _geom_available(geom_dir):
        file_problems.append("geom_reference 无 geom.json 且无标准 rdr")
    if not pairs:
        file_problems.append(
            "未发现 YYYYMMDD_YYYYMMDD 配对文件(空目录不足以执行)")
    if file_problems:
        raise EnvironmentNotReady(
            "ISCE2→PyStamps 桥输入目录存在但内容不完整: "
            + "; ".join(file_problems))

    geom = load_geom(geom_dir)
    base_dir = workspace / "isce2/baselines"
    resolved: list[tuple[PairSpec, Path]] = []
    for pair in pairs:
        bp = find_baseline(base_dir, pair.key)
        if bp is None:
            raise EnvironmentNotReady(
                f"干涉对 {pair.key} 缺少基线文件,拒绝写残缺 .base"
                "(PyStamps 缺 .base 会静默丢干涉图)")
        resolved.append((pair, bp))

    out_dir = workspace / "pystamps" / "work"
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for pair, bp in resolved:
        tcn = load_baseline_tcn(bp, look_rad=geom.look_rad)
        phase = read_phase(pair.phase_path, geom.width, geom.length)
        diff_p = out_dir / f"{pair.key}.diff"
        par_p = out_dir / f"{pair.key}.par"
        base_p = out_dir / f"{pair.key}.base"
        write_diff_be(diff_p, phase)
        write_par(par_p, format_par(
            title=f"ISCE2→PyStamps {pair.key}",
            width=geom.width, length=geom.length,
            range_pixel_spacing=geom.range_pixel_spacing,
            azimuth_pixel_spacing=geom.azimuth_pixel_spacing,
            near=geom.near, center=geom.center, far=geom.far,
            look_deg=geom.look_deg, incidence_deg=geom.incidence_deg,
            se=geom.se, re=geom.re, heading=geom.heading,
            radar_frequency=geom.radar_frequency, prf=geom.prf,
            sensor=geom.sensor, date_str=_date_str(pair.master),
        ))
        write_base(base_p, t=tcn[0], c=tcn[1], n=tcn[2])
        written.extend([diff_p, par_p, base_p])
    return written


def discover_pairs(workspace: Path) -> list[PairSpec]:
    ifg_root = workspace / "isce2/merged/interferograms"
    slc_root = workspace / "isce2/merged/SLC"
    found = _pairs_under(ifg_root) if ifg_root.is_dir() else []
    if not found and slc_root.is_dir():
        found = _pairs_under(slc_root)
    found.sort(key=lambda p: p.key)
    return found


def find_baseline(base_dir: Path, key: str) -> Path | None:
    for name in (f"{key}.json", f"{key}.txt", f"{key}.npy", f"{key}.base"):
        p = base_dir / name
        if p.is_file():
            return p
    sub = base_dir / key
    if sub.is_dir():
        named = sub / f"{key}.txt"
        if named.is_file():
            return named
        for cand in sorted(sub.iterdir()):
            if cand.is_file() and cand.suffix.lower() in {".json", ".txt", ".npy", ".base"}:
                return cand
    return None


def load_geom(geom_dir: Path) -> Geom:
    sidecar = geom_dir / "geom.json"
    rdr_wh = _rdr_wh(geom_dir)
    if not sidecar.is_file():
        raise EnvironmentNotReady(
            "geom_reference 缺少 geom.json(像素间距/近距/se/re 等标量);"
            "标准 rdr 只提供栅格,不能单独生成自洽 par")
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    missing = [k for k in _GEOM_REQUIRED if k not in data]
    if missing:
        raise EnvironmentNotReady(f"geom.json 缺键:{missing}")
    width = int(data["range_samples"])
    length = int(data["azimuth_lines"])
    if rdr_wh is not None and rdr_wh != (width, length):
        raise ValueError(
            f"geom.json 宽高 {width}x{length} 与 rdr xml {rdr_wh[0]}x{rdr_wh[1]} 不一致")
    dr = float(data["range_pixel_spacing"])
    da = float(data["azimuth_pixel_spacing"])
    near = float(data["near_range_slc"])
    se = float(data["sar_to_earth_center"])
    re = float(data["earth_radius_below_sensor"])
    near, center, far = slant_ranges(near_range=near, range_pixel_spacing=dr, width=width)
    look = look_angle_rad(se, center, re)
    inc_deg = math.degrees(incidence_angle_rad(se, center, re))
    if "incidence_angle" in data:
        assert_incidence_consistent(float(data["incidence_angle"]), se=se, rg=center, re=re)
    return Geom(
        width=width, length=length,
        range_pixel_spacing=dr, azimuth_pixel_spacing=da,
        near=near, center=center, far=far, se=se, re=re,
        look_rad=look, look_deg=math.degrees(look), incidence_deg=inc_deg,
        heading=float(data.get("heading", -12.0)),
        radar_frequency=float(data.get("radar_frequency", 5.405e9)),
        prf=float(data.get("prf", 1717.0)),
        sensor=str(data.get("sensor", "Sentinel-1")),
    )


def load_baseline_tcn(path: Path, *, look_rad: float) -> tuple[float, float, float]:
    data = _read_baseline_payload(path)
    return interpret_baseline(data, look_rad=look_rad)


def interpret_baseline(data: dict, *, look_rad: float) -> tuple[float, float, float]:
    lower = {str(k).lower(): v for k, v in data.items()}
    has_cn = "c" in lower and "n" in lower
    if has_cn:
        return (float(lower.get("t", 0.0)), float(lower["c"]), float(lower["n"]))
    if "bperp" in lower or "perpendicular" in lower:
        bperp = float(lower.get("bperp", lower.get("perpendicular")))
        bpar = float(lower.get("bpar", lower.get("bparallel", lower.get("parallel", 0.0))))
        along = float(lower.get("t", lower.get("along_track", 0.0)))
        return tcn_from_perp_par(bperp=bperp, bpar=bpar, look_rad=look_rad,
                                 along_track=along)
    raise EnvironmentNotReady(
        f"基线文件缺少 TCN(t/c/n) 或 ISCE2(bperp/bpar):{sorted(data)}")


def read_phase(path: Path, width: int, length: int):
    import numpy as np

    n = width * length
    raw = path.read_bytes()
    if len(raw) == n * 4:
        return np.frombuffer(raw, dtype="<f4").reshape(length, width).copy()
    if len(raw) == n * 8:
        cpx = np.frombuffer(raw, dtype="<c8").reshape(length, width)
        return np.angle(cpx).astype(np.float32)
    raise ValueError(
        f"{path} 大小 {len(raw)} 字节,与几何 {length}x{width} "
        f"(float32={n * 4} 或 cfloat32={n * 8}) 不符")


def write_diff_be(path: Path, phase) -> None:
    """写出 float32 big-endian 相位(PyStamps 无条件 byteswap)。"""
    import numpy as np

    np.asarray(phase, dtype=np.float32).astype(">f4").tofile(path)


def _pairs_under(root: Path) -> list[PairSpec]:
    found: dict[str, PairSpec] = {}
    if not root.is_dir():
        return []
    for child in sorted(root.iterdir()):
        key = _pair_key(child.name)
        if key is None:
            continue
        master, slave = key
        if child.is_file() and child.suffix.lower() in _PHASE_SUFFIXES:
            found[f"{master}_{slave}"] = PairSpec(master, slave, child)
            continue
        if child.is_dir():
            phase = _phase_in_dir(child)
            if phase is not None:
                found[f"{master}_{slave}"] = PairSpec(master, slave, phase)
    return list(found.values())


def _phase_in_dir(folder: Path) -> Path | None:
    for name in _INT_NAMES:
        p = folder / name
        if p.is_file():
            return p
    for child in sorted(folder.iterdir()):
        if child.is_file() and child.suffix.lower() in _PHASE_SUFFIXES:
            return child
    return None


def _pair_key(name: str) -> tuple[str, str] | None:
    m = DATE_PAIR_RE.search(name)
    if not m:
        return None
    return m.group("m"), m.group("s")


def _geom_available(geom_dir: Path) -> bool:
    if (geom_dir / "geom.json").is_file():
        return True
    return any((geom_dir / name).is_file() for name in _RDR_NAMES)


def _rdr_wh(geom_dir: Path) -> tuple[int, int] | None:
    for name in _RDR_NAMES:
        xml = geom_dir / f"{name}.xml"
        if xml.is_file():
            parsed = parse_isce_rdr_xml_wh(xml.read_text(encoding="utf-8", errors="ignore"))
            if parsed:
                return parsed
    return None


def _read_baseline_payload(path: Path) -> dict:
    suffix = path.suffix.lower()
    if suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise EnvironmentNotReady(f"{path} JSON 基线必须是对象")
        return data
    if suffix == ".npy":
        import numpy as np

        arr = np.load(path)
        flat = [float(x) for x in np.asarray(arr).ravel()]
        if len(flat) >= 3:
            return {"t": flat[0], "c": flat[1], "n": flat[2]}
        if len(flat) == 2:
            return {"bperp": flat[0], "bpar": flat[1]}
        raise EnvironmentNotReady(f"{path} npy 长度 {len(flat)},需要 2(bperp,bpar) 或 3(TCN)")
    return _parse_baseline_text(path.read_text(encoding="utf-8"))


def _parse_baseline_text(text: str) -> dict:
    stripped = text.strip()
    if stripped.startswith("{"):
        data = json.loads(stripped)
        if isinstance(data, dict):
            return data
    kv: dict[str, float] = {}
    for line in stripped.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if ":" in s:
            k, v = s.split(":", 1)
            key = k.strip().lower().replace(" ", "")
            nums = re.findall(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", v)
            if not nums:
                continue
            if "bperp" in key or key == "perpendicular":
                kv["bperp"] = float(nums[0])
            elif "bpar" in key or "parallel" in key:
                kv["bpar"] = float(nums[0])
            elif key in {"t", "c", "n", "along_track"}:
                kv["t" if key == "along_track" else key] = float(nums[0])
            elif len(nums) >= 3 and "tcn" in key:
                kv["t"], kv["c"], kv["n"] = (float(nums[0]), float(nums[1]), float(nums[2]))
        else:
            nums = [float(x) for x in s.split() if _is_float(x)]
            if len(nums) >= 3:
                return {"t": nums[0], "c": nums[1], "n": nums[2]}
            if len(nums) == 2:
                return {"bperp": nums[0], "bpar": nums[1]}
    if kv:
        return kv
    raise EnvironmentNotReady("无法解析基线文本(需要 t/c/n 或 bperp/bpar)")


def _is_float(token: str) -> bool:
    try:
        float(token)
        return True
    except ValueError:
        return False


def _date_str(yyyymmdd: str) -> str:
    return f"{yyyymmdd[0:4]} {int(yyyymmdd[4:6])} {int(yyyymmdd[6:8])}"
