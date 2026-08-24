"""按后缀把预览分发给各模块。模块通过 @register 自注册;import 失败则该格式暂缺。"""

from __future__ import annotations

import contextvars
import importlib
import math
import warnings
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable

_REGISTRY: list[tuple[frozenset[str], str, Callable[[Path], "Preview"]]] = []

_HANDLER_MODULES = (
    "insar_agent.preview.table",
    "insar_agent.preview.excel",
    "insar_agent.preview.json_view",
    "insar_agent.preview.timeseries_view",
    "insar_agent.preview.hdf5_view",
    "insar_agent.preview.mat_view",
    "insar_agent.preview.raster_view",
    "insar_agent.preview.binary_sar",
    "insar_agent.preview.text_view",
    "insar_agent.preview.zip_view",
    "insar_agent.preview.pdf_view",
    "insar_agent.preview.shp_view",
)

_loaded = False


@dataclass(frozen=True)
class PreviewOpts:
    """一次预览的窗口参数。默认对齐旧行为(首屏 200×40),翻页由 API 传入。"""

    sheet: str | None = None
    offset: int = 0
    col_offset: int = 0
    limit: int = 200
    col_limit: int = 40
    dataset: str | None = None
    slice: int = 0
    member: str | None = None
    page: int = 1
    row: int | None = None
    col: int | None = None
    lat: float | None = None
    lon: float | None = None

    def clamp(self) -> PreviewOpts:
        def i(v: int | None, lo: int, hi: int, default: int) -> int:
            if v is None:
                return default
            try:
                n = int(v)
            except (TypeError, ValueError):
                return default
            return max(lo, min(hi, n))

        def finite(v: float | None) -> float | None:
            if v is None:
                return None
            try:
                f = float(v)
            except (TypeError, ValueError):
                return None
            return f if math.isfinite(f) else None

        sheet = (self.sheet or "").strip() or None
        member = (self.member or "").replace("\\", "/").strip() or None
        if member and (".." in member.split("/") or member.startswith("/")):
            member = None
        dataset = (self.dataset or "").strip() or None
        row = None if self.row is None else i(self.row, 0, 10_000_000, 0)
        col = None if self.col is None else i(self.col, 0, 10_000_000, 0)
        return replace(
            self,
            sheet=sheet,
            member=member,
            dataset=dataset,
            offset=i(self.offset, 0, 50_000_000, 0),
            col_offset=i(self.col_offset, 0, 1_000_000, 0),
            limit=i(self.limit, 1, 2000, 200),
            col_limit=i(self.col_limit, 1, 200, 40),
            slice=i(self.slice, 0, 1_000_000, 0),
            page=i(self.page, 1, 50_000, 1),
            row=row,
            col=col,
            lat=finite(self.lat),
            lon=finite(self.lon),
        )

    def query_pairs(self) -> list[tuple[str, str]]:
        """非默认窗口参数,接到 /api/preview 与 /api/preview/image 同一套 query。"""
        d = self.clamp()
        out: list[tuple[str, str]] = []
        if d.sheet:
            out.append(("sheet", d.sheet))
        if d.offset:
            out.append(("offset", str(d.offset)))
        if d.col_offset:
            out.append(("col_offset", str(d.col_offset)))
        if d.limit != 200:
            out.append(("limit", str(d.limit)))
        if d.col_limit != 40:
            out.append(("col_limit", str(d.col_limit)))
        if d.dataset:
            out.append(("dataset", d.dataset))
        if d.slice:
            out.append(("slice", str(d.slice)))
        if d.member:
            out.append(("member", d.member))
        if d.page != 1:
            out.append(("page", str(d.page)))
        if d.row is not None:
            out.append(("row", str(d.row)))
        if d.col is not None:
            out.append(("col", str(d.col)))
        if d.lat is not None:
            out.append(("lat", str(d.lat)))
        if d.lon is not None:
            out.append(("lon", str(d.lon)))
        return out


_OPTS: contextvars.ContextVar[PreviewOpts] = contextvars.ContextVar(
    "preview_opts", default=PreviewOpts(),
)


def current_opts() -> PreviewOpts:
    return _OPTS.get()


@dataclass
class Preview:
    """一种格式的预览结果。payload 必须 JSON 可序列化;png 仅给 /api/preview/image。"""

    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    png: bytes | None = None
    truncated: bool = False
    note: str | None = None
    media_type: str = "image/png"

    def as_json(self, *, image_url: str | None = None) -> dict[str, Any]:
        body = {
            "ok": True,
            "kind": self.kind,
            "truncated": self.truncated,
            "note": self.note,
            **self.payload,
        }
        if self.png is not None:
            body["has_image"] = True
            if image_url:
                body["image_url"] = image_url
        else:
            body["has_image"] = False
        return body


def register(*suffixes: str):
    """装饰器:把处理函数挂到后缀闭集上(小写,须带点)。"""

    norm = frozenset(s.lower() if s.startswith(".") else f".{s.lower()}"
                     for s in suffixes)

    def deco(fn: Callable[[Path], Preview]):
        _REGISTRY.append((norm, fn.__name__, fn))
        return fn

    return deco


def _ensure_loaded() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    for name in _HANDLER_MODULES:
        try:
            importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001 — 单格式缺失不得拖垮预览入口
            warnings.warn(f"preview handler {name} 未加载: {exc}", stacklevel=2)


def preview_file(path: Path, opts: PreviewOpts | None = None) -> Preview:
    """按后缀分发。无处理器 → kind=unsupported。"""
    _ensure_loaded()
    token = _OPTS.set((opts or PreviewOpts()).clamp())
    try:
        suffix = path.suffix.lower()
        for suffixes, _name, fn in _REGISTRY:
            if suffix in suffixes:
                try:
                    return fn(path)
                except Exception as exc:  # noqa: BLE001 — 单文件损坏不得 500
                    return Preview(
                        kind="unsupported",
                        note=f"预览失败:{type(exc).__name__}",
                        payload={"reason": "handler_error"},
                    )
        return Preview(
            kind="unsupported",
            note=f"侧栏尚无 {suffix or '(无扩展名)'} 的预览器",
            payload={"reason": "no_handler", "suffix": suffix},
        )
    finally:
        _OPTS.reset(token)
