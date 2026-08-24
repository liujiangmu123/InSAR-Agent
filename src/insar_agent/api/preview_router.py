"""产物预览端点:表格/JSON/HDF5 结构/栅格 PNG 等,只读工作区相对路径。

GET /api/preview?session=&run_id=&path=     JSON 元数据(+表格/文本)
GET /api/preview/image?session=&run_id=&path=  PNG(无图则 404)

可选窗口参数(与 JSON / image 同一套,翻页不丢上下文):
  sheet / offset / col_offset / limit / col_limit /
  dataset / slice / member / page / row / col / lat / lon

纪律对齐 export_router:自实现 resolve_run,不 import app 闭包;
错误不携带磁盘绝对路径;穿越/绝对路径 404。
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from insar_agent.core.store import Store
from insar_agent.preview.dispatch import PreviewOpts, preview_file
from insar_agent.preview.workspace import resolve_workspace_file


def _opts_of(
    sheet: str | None,
    offset: int,
    col_offset: int,
    limit: int,
    col_limit: int,
    dataset: str | None,
    slice: int,
    member: str | None,
    page: int,
    row: int | None,
    col: int | None,
    lat: float | None,
    lon: float | None,
) -> PreviewOpts:
    return PreviewOpts(
        sheet=sheet,
        offset=offset,
        col_offset=col_offset,
        limit=limit,
        col_limit=col_limit,
        dataset=dataset,
        slice=slice,
        member=member,
        page=page,
        row=row,
        col=col,
        lat=lat,
        lon=lon,
    ).clamp()


def create_preview_router(store: Store) -> APIRouter:
    router = APIRouter(tags=["preview"])

    def resolve_run(session: str, run_id: str | None) -> dict:
        run = store.get_run(run_id) if run_id else store.latest_run(session)
        if run is None:
            raise HTTPException(404, "no run")
        if run["session_id"] != session:
            raise HTTPException(404, "run 不存在或不属于该会话")
        return run

    def target_of(session: str, run_id: str | None, rel: str) -> tuple[dict, Path]:
        if not rel or not rel.strip():
            raise HTTPException(400, "path 不能为空")
        run = resolve_run(session, run_id)
        ws = Path(run["workspace"])
        target = resolve_workspace_file(ws, rel.strip())
        if target is None:
            raise HTTPException(404, "file 不存在")
        return run, target

    def image_query(session: str, run_id: str, path: str, opts: PreviewOpts) -> str:
        # row/col/lat/lon 只影响取样曲线,不进 PNG URL,避免点图时切片图闪烁重载
        skip = {"row", "col", "lat", "lon"}
        pairs = [("session", session), ("run_id", run_id), ("path", path)]
        pairs.extend((k, v) for k, v in opts.query_pairs() if k not in skip)
        return urlencode(pairs)

    @router.get("/api/preview")
    def preview(
        session: str,
        path: str,
        run_id: str | None = None,
        sheet: str | None = None,
        offset: int = Query(0, ge=0),
        col_offset: int = Query(0, ge=0),
        limit: int = Query(200, ge=1, le=2000),
        col_limit: int = Query(40, ge=1, le=200),
        dataset: str | None = None,
        slice: int = Query(0, ge=0),
        member: str | None = None,
        page: int = Query(1, ge=1),
        row: int | None = Query(None, ge=0),
        col: int | None = Query(None, ge=0),
        lat: float | None = Query(None),
        lon: float | None = Query(None),
    ):
        run, target = target_of(session, run_id, path)
        opts = _opts_of(sheet, offset, col_offset, limit, col_limit,
                        dataset, slice, member, page, row, col, lat, lon)
        result = preview_file(target, opts)
        q = image_query(session, run["run_id"], path, opts)
        image_url = f"/api/preview/image?{q}" if result.png is not None else None
        body = result.as_json(image_url=image_url)
        body["run"] = run["run_id"]
        body["name"] = target.name
        body["size"] = target.stat().st_size
        return body

    @router.get("/api/preview/image")
    def preview_image(
        session: str,
        path: str,
        run_id: str | None = None,
        sheet: str | None = None,
        offset: int = Query(0, ge=0),
        col_offset: int = Query(0, ge=0),
        limit: int = Query(200, ge=1, le=2000),
        col_limit: int = Query(40, ge=1, le=200),
        dataset: str | None = None,
        slice: int = Query(0, ge=0),
        member: str | None = None,
        page: int = Query(1, ge=1),
        row: int | None = Query(None, ge=0),
        col: int | None = Query(None, ge=0),
        lat: float | None = Query(None),
        lon: float | None = Query(None),
    ):
        _run, target = target_of(session, run_id, path)
        opts = _opts_of(sheet, offset, col_offset, limit, col_limit,
                        dataset, slice, member, page, row, col, lat, lon)
        result = preview_file(target, opts)
        if result.png is None:
            raise HTTPException(404, "该文件没有图像预览")
        return Response(
            content=result.png,
            media_type=result.media_type,
            headers={"Cache-Control": "no-store"},
        )

    return router
