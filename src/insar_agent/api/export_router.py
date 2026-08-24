"""数据产品导出端点(0814B 契约 §2;逻辑在 report/export.py,本模块只做边界)。

GET /api/export/options?session=&run_id=
    → 能力矩阵:产品(velocity/velocity_std/timeseries)× 格式(h5/csv/xlsx/
      gtiff/kmz/shp)× 可用性,不可用一律给诚实原因(引擎缺失附安装指引;
      xlsx 缺 openpyxl → 501 提示改用 csv)。
GET /api/export?session=&run_id=&product=&fmt=
    → FileResponse(下载名 {run_id}_{product}.{ext};转换产物落 run 工作区
      export/ 子目录,同参幂等复用,响应头 X-Export-Reused=0|1)。

纪律:
  - 会话归属校验与 app.py 的 resolve_run 同口径,本路由自实现不 import
    app.py 内部闭包(artifacts_router/data_router 先例):run 不存在或不属于
    该会话一律 404,不泄露其他会话 run 的存在性,错误信息不携带磁盘路径;
  - product/fmt 闭集校验(400)—— 这同时是路径面防御:用户输入永不进入
    文件路径拼接,export/ 内文件名由白名单清洗的 run_id 构成;
  - 模拟 run(runs.simulated=1)导出一律 409:演示占位字节不是数据产品,
    以数据格式交付即造假(options 仍 200,矩阵全不可用并给同一原因);
  - 错误闭集经 report/export.ExportError 翻译:源 h5 缺失 404、h5py/openpyxl/
    引擎缺失 501(带安装指引)、引擎失败 502(stderr 原样透传)、超时 504。

接线说明:app.py 挂载归 W6(app.include_router(create_export_router(store,
home)));本单元不改 api/app.py,测试用独立 FastAPI 挂载。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from insar_agent.core.store import Store
from insar_agent.report import export as export_mod
from insar_agent.report.export import FORMATS, PRODUCTS, ExportError

_SIMULATED_DETAIL = ("模拟 run 的产物是演示占位字节(SIMULATED 标记),"
                     "不可导出为数据产品 —— 请配置真实引擎后重跑,再导出真实产物")


def create_export_router(store: Store, home: Path) -> APIRouter:
    """构造导出路由。home 为契约签名保留(全局配置读取的统一注入点),
    当前导出逻辑全部以 run 工作区(runs.workspace)为根,不落 home。"""
    _ = home
    router = APIRouter(tags=["export"])

    def resolve_run(session: str, run_id: str | None) -> dict:
        """app.py resolve_run 的 required=True 口径(data_router 同款):
        run_id 缺省 → 会话最近 run;不存在/不属于该会话 → 404 不泄存在性。"""
        run = store.get_run(run_id) if run_id else store.latest_run(session)
        if run is None:
            raise HTTPException(404, "no run")
        if run["session_id"] != session:
            raise HTTPException(
                404, f"run {run['run_id']} 不存在或不属于会话 {session}")
        return run

    @router.get("/api/export/options")
    def export_options(session: str, run_id: str | None = None):
        """指定 run 的导出能力矩阵(产品×格式×可用性+不可用原因)。"""
        run = resolve_run(session, run_id)
        simulated = bool(run["simulated"])
        matrix = export_mod.format_matrix(Path(run["workspace"]),
                                          simulated=simulated)
        return {"run": run["run_id"], "simulated": simulated, **matrix}

    @router.get("/api/export")
    def export_file(session: str, product: str, fmt: str,
                    run_id: str | None = None):
        """导出一个数据产品文件(转换产物幂等复用,见模块头)。"""
        if product not in PRODUCTS:
            raise HTTPException(
                400, f"未知产品 {product!r}:可选 {'/'.join(PRODUCTS)}")
        if fmt not in FORMATS:
            raise HTTPException(
                400, f"未知格式 {fmt!r}:可选 {'/'.join(FORMATS)}")
        run = resolve_run(session, run_id)
        if bool(run["simulated"]):
            raise HTTPException(409, _SIMULATED_DETAIL)
        try:
            result = export_mod.perform_export(
                Path(run["workspace"]), run["run_id"], product, fmt)
        except ExportError as exc:
            raise HTTPException(exc.status, exc.message)
        return FileResponse(result.path, media_type=result.media_type,
                            filename=result.filename,
                            headers={"X-Export-Reused": "1" if result.reused else "0"})

    return router
