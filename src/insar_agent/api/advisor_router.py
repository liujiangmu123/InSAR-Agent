"""下一步建议端点(独立 APIRouter,由 api/app.py 两行挂载)。

- GET /api/advise?session=...&run=...   run 终态 → 建议卡数据
  (run 缺省取该会话最近 run;run_id 为别名参数,与其他端点的命名习惯兼容)

契约:
  - 归属校验口径同 app.resolve_run:run 不存在或不属于该会话一律 404,
    不泄露其他会话 run 的存在性;
  - run 非终态 → {"suggestions": [], "note": "运行中"}(200,不报错 ——
    前端在 SSE 竞态下提前来拉也不至于弹错误);
  - 路由存在性运行时探测(request.app.routes):/api/report/draft 在则
    建议里出现「生成论文方法草稿」,不在则整条建议消失,不硬依赖;
  - LLM(workspace/llm.json 或环境变量)只润色 why 措辞,未配置照常出建议;
  - 纯读端点:不走 driver_of,不为未知会话创建目录/会话行。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from insar_agent.brain.llm_config import routes_from_config
from insar_agent.brain.provider import LLMProvider
from insar_agent.core.store import Store
from insar_agent.report.advisor import TERMINAL_STATUSES, advise


def route_paths(app) -> set[str]:
    """收集应用全部路由路径(/api/report/draft 存在性探测的数据源)。

    新版 starlette 把 include_router 包成 _IncludedRouter(顶层不暴露 path,
    真实路由藏在 original_router.routes 里,路径已含前缀)—— 只扫 app.routes
    顶层会漏掉所有 include_router 挂载的端点,探测永假。这里三种形态都走:
    直挂路由(.path)、嵌套路由器/Mount(.routes)、包装路由(.original_router)。
    """
    out: set[str] = set()

    def visit(routes) -> None:
        for r in routes:
            p = getattr(r, "path", None)
            if isinstance(p, str):
                out.add(p)
            inner = getattr(r, "original_router", None)
            if inner is not None:
                visit(getattr(inner, "routes", None) or [])
            sub = getattr(r, "routes", None)
            if sub:
                visit(sub)

    visit(app.routes)
    return out


def create_advisor_router(store: Store, home: Path) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["advise"])

    @router.get("/advise")
    def get_advise(request: Request, session: str,
                   run: str | None = None, run_id: str | None = None):
        rid = run or run_id
        row = store.get_run(rid) if rid else store.latest_run(session)
        if row is None:
            raise HTTPException(404, "no run")
        if row["session_id"] != session:
            raise HTTPException(
                404, f"run {row['run_id']} 不存在或不属于会话 {session}")
        if row["status"] not in TERMINAL_STATUSES:
            return {"run_id": row["run_id"], "status": row["status"],
                    "suggestions": [], "note": "运行中"}
        # provider 每请求即时构建:llm.json 界面改完立即生效,未配置 = 纯规则文案
        provider = LLMProvider(routes_from_config(home))
        return advise(store, row["run_id"], provider=provider,
                      available_routes=route_paths(request.app))

    return router
