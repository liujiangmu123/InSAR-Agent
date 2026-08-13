"""方法章节草稿端点(报告面板「生成方法章节草稿」按钮的数据源)。

POST /api/report/draft {session, run_id?} →
  {run_id, draft, llm_polish, facts_used, saved}

纪律:
  - 会话归属校验与 api/app.py 的 resolve_run 同口径,在本路由内自行实现
    (app.py 属并行分支所有权,不 import 其内部闭包):run_id 缺省 → 该会话
    最近 run;run 不存在或属于其他会话 → 404「不存在或不属于」,不泄露
    其他会话 run 的存在性;
  - 草稿两段式防幻觉(report/draft.py):骨架纯代码拼接,LLM 只润色措辞且
    经双向数值校验,不过即回退骨架(llm_polish=false);LLM 未配置时照常
    产出骨架 —— 本端点不依赖 LLM;
  - 生成结果落盘 run 工作目录 report_draft.md(原子写,供文件面板可见);
    落盘失败不阻塞响应,如实回 saved=false。

接线:api/app.py 加一行 include_router(create_report_router(store, home))。
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from insar_agent.audit.contract import load_contract
from insar_agent.brain.llm_config import routes_from_config
from insar_agent.brain.provider import LLMProvider
from insar_agent.core.fsio import atomic_write_text
from insar_agent.core.ledger import export_provenance
from insar_agent.core.store import Store
from insar_agent.report.draft import build_facts, draft_methods

#: 落盘文件名(run 工作目录下;文件面板按目录可见)
DRAFT_FILENAME = "report_draft.md"


class DraftBody(BaseModel):
    session: str
    run_id: str | None = None


def _resolve_run(store: Store, session: str, run_id: str | None) -> dict:
    """按会话解析 run 并做归属校验(app.py resolve_run 的 required=True 口径)。"""
    run = store.get_run(run_id) if run_id else store.latest_run(session)
    if run is None:
        raise HTTPException(404, "no run")
    if run["session_id"] != session:
        raise HTTPException(404, f"run {run['run_id']} 不存在或不属于会话 {session}")
    return run


def _save_draft(workspace: str | None, text: str) -> bool:
    """草稿落盘(原子写);工作区异常不炸端点,如实返回 False。"""
    if not workspace:
        return False
    try:
        ws = Path(workspace)
        ws.mkdir(parents=True, exist_ok=True)
        atomic_write_text(ws / DRAFT_FILENAME, text)
        return True
    except OSError:
        return False


def create_report_router(store: Store, home: Path, *,
                         provider_factory: Callable[[], LLMProvider | None] | None = None,
                         ) -> APIRouter:
    """provider_factory 仅供测试注入 mock LLM;缺省按 workspace/llm.json +
    环境变量组路由(与 driver 的 brain 同一配置源,brain/llm_config)。"""
    router = APIRouter(tags=["report"])
    contract = load_contract()  # 与 create_app 同款:启动读一次,请求期只读

    def default_factory() -> LLMProvider:
        return LLMProvider(routes_from_config(home))

    factory = provider_factory or default_factory

    @router.post("/api/report/draft")
    def report_draft(body: DraftBody):
        run = _resolve_run(store, body.session, body.run_id)
        workspace = run["workspace"]
        doc = export_provenance(store, run["run_id"], contract=contract,
                                workspace=Path(workspace) if workspace else None)
        result = draft_methods(factory(), build_facts(doc))
        return {
            "run_id": run["run_id"],
            "draft": result["draft"],
            "llm_polish": result["llm_polish"],
            "facts_used": result["facts_used"],
            "saved": _save_draft(workspace, result["draft"]),
        }

    return router
