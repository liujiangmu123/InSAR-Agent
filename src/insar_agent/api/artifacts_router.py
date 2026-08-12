"""产物清单端点(files 面板真实化,UI-DETAILS-AUDIT-2026-08-12 第 2 条)。

GET /api/artifacts?session=&run_id= → 按步骤分组的真实产物清单
(step_id / 步骤名 / 方法 + 每个产物的 art_id / 相对路径 / kind / policy /
三段完整指纹 fp / size / mtime / exists)。

纪律:
  - 会话归属校验与 api/app.py 的 resolve_run(required=False) 同口径,在本
    路由内自行实现(app.py 属并行分支所有权,不 import 其内部闭包):
    run_id 缺省 → 该会话最近 run;给了 run_id 但属于其他会话 → 404,错误
    文案统一「不存在或不属于」,不泄露其他会话 run 的存在性;会话没有任何
    run → {"run": None, "steps": []}(前端以此回落演示数据,/api/figures 同款)。
  - 路径只回工作区相对路径:绝对路径/带盘符/../ 越界的坏 DB 行降级为仅
    文件名,绝不回显磁盘布局(app.py resolve_artifact_file 同款边界)。
  - size/mtime 优先取落盘文件实测值(/api/figures 同纪律:DB 记录可能已被
    覆写);文件缺失回落 DB 记录值并标 exists=False —— 「产物被删」是要
    如实展示的正常状态,不是要隐藏的异常(§1.3 的 -1 编码哲学)。

接线说明:app.py 需加一行 app.include_router(create_artifacts_router(store)),
由主线合并时接(本分支不改 api/app.py)。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

from insar_agent.core.store import Store


def _resolve_run(store: Store, session: str, run_id: str | None) -> dict | None:
    """按会话解析 run 并做归属校验(app.py resolve_run 的 required=False 口径)。

    返回 None = 「本会话还没有 run」;归属不符直接 404(按「不存在」处理)。
    """
    run = store.get_run(run_id) if run_id else store.latest_run(session)
    if run is None:
        return None
    if run["session_id"] != session:
        raise HTTPException(404, f"run {run['run_id']} 不存在或不属于会话 {session}")
    return run


def _safe_rel_path(base: Path, rel_path: str) -> Path | None:
    """产物相对路径 → 工作区内绝对路径;绝对路径/盘符/越界一律 None。

    与 app.py 的 resolve_artifact_file 同款复核:artifacts.path 可能来自
    坏数据/被篡改的 DB 行,清单端点虽不读文件本体,但 stat 前仍须确认
    目标落在工作区内,否则回显 size/mtime 也是对区外文件的探测面。
    """
    rel = Path(rel_path)
    if rel.is_absolute() or rel.drive:
        return None
    target = (base / rel).resolve()
    if target == base or not target.is_relative_to(base):
        return None
    return target


def _artifact_entry(base: Path, art: dict) -> dict:
    """单行产物 → 响应条目(字段清单由 tests/test_artifacts_api.py 钉死)。"""
    target = _safe_rel_path(base, art["path"])
    if target is None:
        # 坏 DB 行(绝对路径/越界):只回文件名,不回显任何磁盘布局
        path_out = Path(art["path"]).name
    else:
        path_out = Path(art["path"]).as_posix()
    exists = target is not None and target.exists()
    if target is not None and target.is_file():
        # 实测值优先(记录可能已被覆写);目录型产物沿用 DB 记录(执行器对
        # 目录不记 size/mtime,与其口径一致)
        st = target.stat()
        size: int | None = st.st_size
        mtime: float | None = st.st_mtime
    else:
        size = art["size"]
        mtime = art["mtime_ns"] / 1e9 if art["mtime_ns"] is not None else None
    return {
        "artId": art["art_id"], "path": path_out, "kind": art["kind"],
        "policy": art["policy"], "fp": art["fp"],
        "size": size, "mtime": mtime, "exists": exists,
    }


def create_artifacts_router(store: Store) -> APIRouter:
    router = APIRouter(tags=["artifacts"])

    @router.get("/api/artifacts")
    def artifacts(session: str, run_id: str | None = None):
        """该 run 全部产物记录,按步骤分组(记录本位:文件已缺失的行也列,
        标 exists=False —— 与 /api/figures 的「可直读文件清单」定位互补)。"""
        run = _resolve_run(store, session, run_id)
        if run is None:
            return {"run": None, "steps": []}
        base = Path(run["workspace"]).resolve()
        meta = {s.step_id: s for s in store.load_steps(run["run_id"])}
        grouped: dict[int, list[dict]] = {}
        for art in store.artifacts_of(run["run_id"]):
            grouped.setdefault(art["step_id"], []).append(_artifact_entry(base, art))
        steps = []
        for step_id in sorted(grouped):
            row = meta.get(step_id)
            steps.append({
                "stepId": step_id,
                # 产物行有外键约束,正常不会缺步骤声明;缺了也不 500,给占位名
                "name": row.name if row else f"步骤 {step_id}",
                "method": row.method if row else "",
                "artifacts": sorted(grouped[step_id], key=lambda a: a["artId"]),
            })
        return {"run": run["run_id"], "steps": steps}

    return router
