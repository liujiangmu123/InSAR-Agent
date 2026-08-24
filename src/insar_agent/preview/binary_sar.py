"""雷达坐标二进制(.unw/.flat/.int/.slc/.mli/.diff)诚实预览。

宽度只来自文件旁 sidecar,绝不从文件体积反推行列(禁止 1000×1000 一类猜测)。

检测顺序(命中即停,不混用两份 sidecar):
  1. {stem}.rsc     ROI_PAC/ISCE: 键 WIDTH= 或 WIDTH<空白>
  2. {name}.xml     ISCE: <property name="width"><value>N</value>  (stem+suffix.xml)
  3. {stem}.par     GAMMA: range_samples:
  4. {stem}.xml     ISCE 偶发只用 stem.xml
  5. ENVI {name}.hdr  samples= / lines=
  6. {stem}.hdr     同上

有宽度且 numpy 可读、dtype 有把握时才 fromfile 下采样 PNG(长边≤1024)。
.unw 按 ISCE float32 两波段(BIL, amp+phase)理解;对不齐或其它后缀只给元数据。
无宽度: png=None, reason=missing_geometry。
"""

from __future__ import annotations

import re
import struct
import zlib
from pathlib import Path

from insar_agent.preview.dispatch import Preview, register

_KIND = "binary_sar"
_MISSING_NOTE = "无宽度元数据，拒绝猜测行列，不渲染假图"
_MAX_SIDE = 1024
# fromfile 整文件读入;超此体积不下图,宽度仍回传(避免侧栏把 GB 级栅格拉进内存)
_MAX_FROMFILE = 64 * 1024 * 1024

_RSC_WIDTH = re.compile(r"^WIDTH(?:\s*[=:]\s*|\s+)(\d+)\b", re.I | re.M)
_RSC_LENGTH = re.compile(
    r"^(?:LENGTH|FILE_LENGTH)(?:\s*[=:]\s*|\s+)(\d+)\b", re.I | re.M)
_PAR_WIDTH = re.compile(r"^\s*range_samples\s*:\s*(\d+)\b", re.I | re.M)
_PAR_LENGTH = re.compile(r"^\s*azimuth_lines\s*:\s*(\d+)\b", re.I | re.M)
_HDR_WIDTH = re.compile(r"^\s*samples\s*=\s*(\d+)\b", re.I | re.M)
_HDR_LENGTH = re.compile(r"^\s*lines\s*=\s*(\d+)\b", re.I | re.M)
_XML_PROP = re.compile(
    r'<property\s+name=["\'](width|length)["\']\s*>(.*?)</property>',
    re.I | re.S,
)
_XML_VALUE = re.compile(r"<value>\s*(\d+)\s*</value>", re.I)


@register(".unw", ".flat", ".int", ".slc", ".mli", ".diff")
def preview_binary_sar(path: Path) -> Preview:
    suffix = path.suffix.lower()
    size_bytes = path.stat().st_size
    payload: dict[str, int | str] = {"size_bytes": size_bytes, "suffix": suffix}

    geom = _geometry_from_sidecar(path)
    if geom is None:
        payload["reason"] = "missing_geometry"
        return Preview(kind=_KIND, payload=payload, png=None, note=_MISSING_NOTE)

    width, source, length = geom
    payload["width"] = width
    payload["geometry_source"] = source
    if length is not None:
        payload["length"] = length

    png = _maybe_png(path, suffix, width, size_bytes)
    return Preview(kind=_KIND, payload=payload, png=png)


def _geometry_from_sidecar(path: Path) -> tuple[int, str, int | None] | None:
    """只读约定 sidecar。返回 (width, source, length|None);无宽度则 None。"""
    rsc = path.with_suffix(".rsc")
    if rsc.is_file():
        parsed = _parse_rsc(rsc)
        if parsed is not None:
            return parsed[0], "rsc", parsed[1]

    xml = Path(str(path) + ".xml")
    if xml.is_file():
        parsed = _parse_isce_xml(xml)
        if parsed is not None:
            return parsed[0], "isce_xml", parsed[1]

    par = path.with_suffix(".par")
    if par.is_file():
        parsed = _parse_gamma_par(par)
        if parsed is not None:
            return parsed[0], "gamma_par", parsed[1]

    stem_xml = path.with_suffix(".xml")
    if stem_xml.is_file():
        parsed = _parse_isce_xml(stem_xml)
        if parsed is not None:
            return parsed[0], "isce_xml", parsed[1]

    name_hdr = Path(str(path) + ".hdr")
    if name_hdr.is_file():
        parsed = _parse_envi_hdr(name_hdr)
        if parsed is not None:
            return parsed[0], "envi_hdr", parsed[1]

    stem_hdr = path.with_suffix(".hdr")
    if stem_hdr.is_file():
        parsed = _parse_envi_hdr(stem_hdr)
        if parsed is not None:
            return parsed[0], "envi_hdr", parsed[1]

    return None


def _read_text(path: Path) -> str:
    return path.read_bytes().decode("utf-8", errors="replace")


def _positive_int(token: str) -> int | None:
    try:
        n = int(token, 10)
    except ValueError:
        return None
    return n if n > 0 else None


def _parse_rsc(path: Path) -> tuple[int, int | None] | None:
    text = _read_text(path)
    wm = _RSC_WIDTH.search(text)
    if wm is None:
        return None
    width = _positive_int(wm.group(1))
    if width is None:
        return None
    lm = _RSC_LENGTH.search(text)
    length = _positive_int(lm.group(1)) if lm else None
    return width, length


def _parse_isce_xml(path: Path) -> tuple[int, int | None] | None:
    text = _read_text(path)
    found: dict[str, int] = {}
    for key, body in _XML_PROP.findall(text):
        vm = _XML_VALUE.search(body)
        if vm is None:
            continue
        n = _positive_int(vm.group(1))
        if n is None:
            continue
        found[key.lower()] = n
    width = found.get("width")
    if width is None:
        return None
    return width, found.get("length")


def _parse_gamma_par(path: Path) -> tuple[int, int | None] | None:
    text = _read_text(path)
    wm = _PAR_WIDTH.search(text)
    if wm is None:
        return None
    width = _positive_int(wm.group(1))
    if width is None:
        return None
    lm = _PAR_LENGTH.search(text)
    length = _positive_int(lm.group(1)) if lm else None
    return width, length


def _parse_envi_hdr(path: Path) -> tuple[int, int | None] | None:
    text = _read_text(path)
    wm = _HDR_WIDTH.search(text)
    if wm is None:
        return None
    width = _positive_int(wm.group(1))
    if width is None:
        return None
    lm = _HDR_LENGTH.search(text)
    length = _positive_int(lm.group(1)) if lm else None
    return width, length


def _maybe_png(path: Path, suffix: str, width: int, size_bytes: int) -> bytes | None:
    """仅 .unw 且样本能被 width(×2 波段)整除时渲染;否则 None。"""
    if suffix != ".unw" or width < 1:
        return None
    if size_bytes <= 0 or size_bytes > _MAX_FROMFILE or size_bytes % 4 != 0:
        return None
    try:
        import numpy as np
    except ImportError:
        return None

    samples = np.fromfile(path, dtype=np.float32)
    n = int(samples.size)
    if n == 0:
        return None

    # ISCE .unw: float32 BIL, 每行 amp[width] + phase[width]
    two = width * 2
    if n % two == 0:
        height = n // two
        phase = samples.reshape(height, 2, width)[:, 1, :]
        vis = phase
    elif n % width == 0:
        height = n // width
        vis = samples.reshape(height, width)
    else:
        return None

    vis = _decimate(vis)
    return _png_gray(_stretch_u8(vis))


def _decimate(arr):
    h, w = arr.shape
    m = max(h, w)
    if m <= _MAX_SIDE:
        return arr
    step = (m + _MAX_SIDE - 1) // _MAX_SIDE
    return arr[::step, ::step]


def _stretch_u8(arr):
    import numpy as np

    a = np.asarray(arr, dtype=np.float64)
    finite = np.isfinite(a)
    out = np.zeros(a.shape, dtype=np.uint8)
    if not finite.any():
        return out
    vals = a[finite]
    lo = float(np.percentile(vals, 2))
    hi = float(np.percentile(vals, 98))
    if hi <= lo:
        out[finite] = 128
        return out
    scaled = np.clip((a - lo) / (hi - lo), 0.0, 1.0)
    out[finite] = (scaled[finite] * 255.0).astype(np.uint8)
    return out


def _png_gray(u8) -> bytes:
    """8-bit 灰度 PNG,不依赖 Pillow。"""
    import numpy as np

    u8 = np.ascontiguousarray(u8, dtype=np.uint8)
    h, w = (int(u8.shape[0]), int(u8.shape[1]))
    raw = b"".join(b"\x00" + u8[i].tobytes() for i in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )
