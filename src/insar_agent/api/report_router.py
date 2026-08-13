"""方法章节草稿端点(报告面板「生成方法章节草稿」按钮的数据源)。

POST /api/report/draft {session, run_id?} →
  {run_id, draft, llm_polish, facts_used, saved}
POST/GET /api/report/caption(body/query:session, figure, run_id?)→ 双语图注的
  生成/读取(report/captions.py);图件定位与 sidecar 读取按 /api/figures 同口径,
  生成结果落盘图件旁 <name>.caption.json。

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

from insar_agent.api.app import read_sidecar_meta
from insar_agent.audit.contract import load_contract
from insar_agent.brain.llm_config import routes_from_config
from insar_agent.brain.provider import LLMProvider
from insar_agent.core.fsio import atomic_write_text
from insar_agent.core.ledger import export_provenance
from insar_agent.core.store import Store
from insar_agent.report import captions
from insar_agent.report.draft import build_facts, draft_methods
from insar_agent.report.results import RESULTS_FILENAME, build_result_facts, draft_results

#: 落盘文件名(run 工作目录下;文件面板按目录可见)
DRAFT_FILENAME = "report_draft.md"


class DraftBody(BaseModel):
    session: str
    run_id: str | None = None


class CaptionBody(BaseModel):
    session: str
    figure: str
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

    # ---- 结果章节(append 块:与方法草稿同纪律;路由 /api/report/results 不重名) ----
    @router.post("/api/report/results")
    def report_results(body: DraftBody):
        """结果章节草稿:QA 指标 + 速度场统计 → 中文结果段,落盘 report_results.md。"""
        run = _resolve_run(store, body.session, body.run_id)
        facts = build_result_facts(store, run["run_id"], contract=contract)
        result = draft_results(factory(), facts)
        saved = False
        if run["workspace"]:  # 落盘失败不阻塞响应(_save_draft 同语义,文件名不同)
            try:
                ws = Path(run["workspace"])
                ws.mkdir(parents=True, exist_ok=True)
                atomic_write_text(ws / RESULTS_FILENAME, result["draft"])
                saved = True
            except OSError:
                saved = False
        return {
            "run_id": run["run_id"],
            "draft": result["draft"],
            "llm_polish": result["llm_polish"],
            "facts_used": result["facts_used"],
            "saved": saved,
        }

    # ---- 图注(append 块:双语骨架 + 三重校验润色;/api/report/caption) ----
    def _figure_of(session: str, run_id: str | None, figure: str):
        # run 归属校验 + 图件定位(/api/figures 同口径);找不到统一 404,不泄露磁盘路径
        run = _resolve_run(store, session, run_id)
        found = captions.locate_figure(run, store.artifacts_of(run["run_id"]), figure)
        if found is None:
            raise HTTPException(404, f"figure {figure} 不存在或不属于该 run")
        return run, found[0], found[1]

    @router.post("/api/report/caption")
    def report_caption(body: CaptionBody):
        run, target, art = _figure_of(body.session, body.run_id, body.figure)
        ws = run["workspace"]
        doc = export_provenance(store, run["run_id"], contract=contract,
                                workspace=Path(ws) if ws else None)
        facts = captions.build_caption_facts(read_sidecar_meta(target), doc,
                                             step=art["step_id"])
        payload = {"run_id": run["run_id"], "figure": target.name,
                   **captions.generate_caption(factory(), facts)}
        return {**payload, "saved": captions.save_caption(target, payload)}

    @router.get("/api/report/caption")
    def report_caption_saved(session: str, figure: str, run_id: str | None = None):
        run, target, _ = _figure_of(session, run_id, figure)
        data = captions.load_caption(target)
        if data is None:
            raise HTTPException(404, "尚未生成图注")
        return {"run_id": run["run_id"], "figure": target.name, "zh": data["zh"],
                "en": data["en"], "llm_polish": bool(data.get("llm_polish"))}

    return router
