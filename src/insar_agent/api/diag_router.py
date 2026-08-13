"""诊断包端点(report/diagbundle.py 的 HTTP 出口)。

  POST /api/diagnostics        body {run_id?} → 打包到 <home>/diagnostics/,
                               返回 {path, size, download_url}
  GET  /api/diagnostics/file   ?name=<zip 名> → FileResponse 下载

安全边界:
  - 下载文件名走白名单正则 ^diag-[0-9TZ-]+\\.zip$(与 diagbundle 的命名
    口径同源)—— 字符集不含路径分隔符与点段,穿越无从构造;resolve 复核
    双保险,越界一律 404 且不回显磁盘路径;
  - 显式 run_id 不存在 → 404(与 app.py resolve_run 的「不存在」口径一致);
  - 打包本身只读(见 diagbundle 模块头),唯一写落点是 diagnostics/ 目录。

接线:api/app.py 一行 app.include_router(create_diag_router(home))。
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from insar_agent.report.diagbundle import build_diag_bundle

#: 下载文件名白名单(diagbundle._zip_name 生成的一切名字都落在此集合内)
_DIAG_NAME_RE = re.compile(r"^diag-[0-9TZ-]+\.zip$")


class DiagBody(BaseModel):
    run_id: str | None = None


def create_diag_router(home: Path) -> APIRouter:
    router = APIRouter(tags=["diagnostics"])
    out_dir = (Path(home) / "diagnostics").resolve()

    @router.post("/api/diagnostics")
    def make_diagnostics(body: DiagBody | None = None):
        """一键打诊断包(体积上限/脱敏/裁剪纪律全在 diagbundle 内)。
        run_id 缺省 → 最近一个失败 run(再缺省取最近 run)。"""
        run_id = body.run_id if body else None
        try:
            zip_path = build_diag_bundle(home, run_id, out_dir=out_dir)
        except KeyError:
            raise HTTPException(404, f"run {run_id} 不存在")
        return {
            "path": str(zip_path),
            "size": zip_path.stat().st_size,
            "download_url": f"/api/diagnostics/file?name={zip_path.name}",
        }

    @router.get("/api/diagnostics/file")
    def get_diagnostics_file(name: str):
        """按名下载已生成的诊断包(只认白名单形态的文件名,防穿越)。"""
        if not _DIAG_NAME_RE.match(name):
            raise HTTPException(400, "文件名不合法:仅接受 diag-<时间戳>.zip")
        target = out_dir / name
        try:
            inside = target.resolve().is_relative_to(out_dir)
        except OSError:
            inside = False
        if not inside or not target.is_file():
            raise HTTPException(404, "诊断包不存在")
        return FileResponse(target, media_type="application/zip", filename=name)

    return router
