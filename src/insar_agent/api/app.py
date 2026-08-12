"""FastAPI 层:人与 LLM 共用同一套接口(AGENT-DESIGN §2)。

协议纪律(吸收 pi RPC,absorb-E4/E8):
  - 回合是流式 NDJSON:POST 响应只代表「已接受」,过程与结果走事件流,
    绝不给同一请求发第二个终态响应。
  - 运行中投递消息必须显式声明 deliver_as=steer|follow_up|next_run,否则 400。
  - 全局事件走 SSE(/api/events);单回合事件走该回合自己的 NDJSON 流。

启动:python -m insar_agent.api.app(默认 127.0.0.1:8873,INSAR_HOME 可配)。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from insar_agent.audit.contract import load_contract
from insar_agent.brain.facade import Brain
from insar_agent.brain.provider import LLMProvider
from insar_agent.core.db import Database
from insar_agent.core.ledger import export_provenance
from insar_agent.core.stale import preview_change
from insar_agent.core.store import Store
from insar_agent.loop.driver import Driver
from insar_agent.planner.feasibility import narrow_methods
from insar_agent.planner.plan import fork_run
from insar_agent.api.setup_router import create_setup_router
from insar_agent.api.version_router import router as version_router
from insar_agent.registry.capabilities import PIPELINE, REGISTRY
from insar_agent.report.methods import methods_markdown
from insar_agent.report.script import export_run_script

# INSAR_UI_DIR:静态 UI 目录的唯一环境变量覆盖点(桌面冻结版由
# desktop/backend-bundle/entry.py 在 import 本模块前设置;源码运行不受影响,
# 缺省仍按 __file__ 回溯源码树 src/../prototype)。
_UI_DIR_OVERRIDE = os.environ.get("INSAR_UI_DIR", "")
PROTOTYPE_DIR = (Path(_UI_DIR_OVERRIDE) if _UI_DIR_OVERRIDE
                 else Path(__file__).resolve().parents[3] / "prototype")


class TurnBody(BaseModel):
    session: str
    text: str


class PipelineBody(BaseModel):
    session: str
    run_id: str | None = None
    step_ids: list[int] | None = None


class MessageBody(BaseModel):
    session: str
    text: str
    deliver_as: str | None = None  # steer | follow_up | next_run(运行中必填)


class ActionBody(BaseModel):
    session: str
    run_id: str | None = None
    scope: str = "step"
    target: str
    action: str
    payload: dict = {}
    deliver_as: str = "steer"


class ForkBody(BaseModel):
    session: str
    run_id: str
    changes: dict[int, dict]


class SessionBody(BaseModel):
    id: str
    name: str | None = None
    mode: str = "expert"


def create_app(home: Path | None = None) -> FastAPI:
    home = Path(home or os.environ.get("INSAR_HOME", "workspace")).resolve()
    home.mkdir(parents=True, exist_ok=True)
    db = Database(home / "insar.db")
    store = Store(db)
    contract = load_contract()
    drivers: dict[str, Driver] = {}

    app = FastAPI(title="insar-agent", version="0.1.0")
    app.include_router(create_setup_router(home))  # 环境向导(/api/setup/*,settings.json 与 DB 同目录)
    app.include_router(version_router)             # 版本信息与更新检查(/api/version*)

    def driver_of(session_id: str) -> Driver:
        if session_id not in drivers:
            ws = home / "sessions" / session_id
            drivers[session_id] = Driver(
                store, workspace=ws, brain=Brain(LLMProvider()),
                allow_simulated=os.environ.get("INSAR_ALLOW_SIMULATED", "1") == "1")
            store.create_session(session_id, session_id)
        return drivers[session_id]

    def ndjson(agen):
        async def gen():
            async for event in agen:
                yield json.dumps(event, ensure_ascii=False) + "\n"
        return StreamingResponse(gen(), media_type="application/x-ndjson")

    def running_run(session_id: str) -> dict | None:
        latest = store.latest_run(session_id)
        return latest if latest and latest["status"] == "running" else None

    # ---------------- 会话 ----------------

    @app.get("/api/health")
    def health():
        return {"ok": True, "version": app.version}

    @app.get("/api/sessions")
    def sessions():
        return store.list_sessions()

    @app.post("/api/sessions")
    def create_session(body: SessionBody):
        store.create_session(body.id, body.name or body.id, mode=body.mode)
        driver_of(body.id)
        return store.get_session(body.id)

    @app.get("/api/chat")
    def chat(session: str):
        return store.chat_history(session)

    # ---------------- registry / 环境 / 状态 ----------------

    @app.get("/api/registry")
    def registry(session: str | None = None):
        probe = driver_of(session).probe() if session else None
        out = []
        for cap in PIPELINE:
            methods = []
            feas = narrow_methods(cap, probe, allow_simulated=True) if probe else None
            feas_by_id = {f.method.id: f for f in feas} if feas else {}
            for m in cap.methods:
                f = feas_by_id.get(m.id)
                methods.append({
                    "id": m.id, "label": m.label, "engine": m.engine, "why": m.why,
                    "recommend": m.recommend, "extra": m.extra,
                    "ok": f.ok if f else True,
                    "simulated": f.simulated if f else False,
                    "blocked": f.blocked_reason if f else "",
                })
            out.append({
                "id": cap.id, "name": cap.name, "deps": list(cap.deps),
                "method": cap.default_method, "methods": methods,
                "params": {k: {"default": p.default, "kind": p.kind, "type": p.type,
                               "min": p.min, "max": p.max, "hint": p.hint}
                           for k, p in cap.params.items()},
                "outputs": [{"path": a.candidates[0], "kind": a.kind, "layout": a.layout}
                            for a in cap.artifacts],
                "replay": cap.replay,
                "timeouts": {"idle": cap.timeouts.idle, "total": cap.timeouts.total},
            })
        return out

    @app.get("/api/env")
    def env(session: str):
        d = driver_of(session)
        probe = d.probe(refresh=True)
        return {
            "probe": probe.to_dict(),
            "thresholds": [{"key": k, "value": t.value, "source": t.source,
                            "ref": t.ref, "status": t.status}
                           for k, t in contract.items()],
        }

    @app.get("/api/state")
    def state(session: str, run_id: str | None = None):
        run = store.get_run(run_id) if run_id else store.latest_run(session)
        if run is None:
            return {"run": None, "steps": []}
        steps = [{
            "id": s.step_id, "name": s.name, "method": s.method, "params": s.params,
            "stage": s.stage, "state": s.state, "stale": s.stale,
            "staleReason": s.stale_reason, "failureClass": s.failure_class,
            "fingerprint": s.eval_hash[:12], "runOk": s.run_ok,
            "logOffset": s.log_offset,
        } for s in store.load_steps(run["run_id"])]
        return {"run": dict(run), "steps": steps}

    # ---------------- 回合(流式) ----------------

    @app.post("/api/turn")
    def turn(body: TurnBody):
        return ndjson(driver_of(body.session).turn(body.session, body.text))

    @app.post("/api/pipeline")
    def pipeline(body: PipelineBody):
        return ndjson(driver_of(body.session).execute(
            body.session, body.run_id, body.step_ids))

    @app.post("/api/resume")
    def resume(body: PipelineBody):
        return ndjson(driver_of(body.session).resume(body.session))

    @app.post("/api/abort")
    def abort(body: PipelineBody):
        run = store.get_run(body.run_id) if body.run_id else store.latest_run(body.session)
        if run is None:
            raise HTTPException(404, "no run")
        driver_of(body.session).request_cancel(run["run_id"])
        return JSONResponse({"accepted": True}, status_code=202)

    # ---------------- 消息与干预 ----------------

    @app.post("/api/message")
    def message(body: MessageBody):
        active = running_run(body.session)
        if active and body.deliver_as not in ("steer", "follow_up", "next_run"):
            # absorb-E4(pi rpc.md:56-65):运行中投递必须显式声明语义
            raise HTTPException(
                400, "run 正在执行:必须指定 deliver_as=steer|follow_up|next_run")
        store.append_chat(body.session, "user", body.text,
                          meta={"deliver_as": body.deliver_as} if body.deliver_as else None)
        return JSONResponse({"accepted": True, "deliver_as": body.deliver_as},
                            status_code=202)

    @app.post("/api/actions")
    def actions(body: ActionBody):
        if body.action == "KILL":
            run = store.get_run(body.run_id) if body.run_id else store.latest_run(body.session)
            if run:
                driver_of(body.session).request_cancel(run["run_id"])
        action_id = store.push_action(scope=body.scope, target=body.target,
                                      action=body.action, payload=body.payload,
                                      deliver_as=body.deliver_as)
        return JSONResponse({"accepted": True, "id": action_id}, status_code=202)

    @app.get("/api/impact")
    def impact(session: str, step: int, method: str | None = None,
               params: str | None = None, run_id: str | None = None):
        run = store.get_run(run_id) if run_id else store.latest_run(session)
        if run is None:
            raise HTTPException(404, "no run")
        d = driver_of(session)
        imp = preview_change(
            store, run["run_id"], step, registry=d.registry,
            tool_versions=d.probe().tool_versions(), method=method,
            params_patch=json.loads(params) if params else None)
        return {
            "changedStep": imp.changed_step, "reason": imp.reason,
            "affected": imp.affected, "rerunMinutes": imp.rerun_minutes,
            "rerunBasis": imp.rerun_basis,
        }

    @app.post("/api/fork")
    def fork(body: ForkBody):
        d = driver_of(body.session)
        plan = fork_run(store, body.run_id, registry=d.registry,
                        changes={int(k): v for k, v in body.changes.items()},
                        probe=d.probe())
        return {"runId": plan.run_id,
                "steps": [{"id": p.step_id, "state": p.state, "method": p.method}
                          for p in plan.steps]}

    # ---------------- 导出 ----------------

    @app.get("/api/provenance")
    def provenance(session: str, run_id: str | None = None):
        run = store.get_run(run_id) if run_id else store.latest_run(session)
        if run is None:
            raise HTTPException(404, "no run")
        return export_provenance(store, run["run_id"], contract=contract,
                                 workspace=driver_of(session).workspace)

    @app.get("/api/run.sh")
    def run_script(session: str, run_id: str | None = None):
        run = store.get_run(run_id) if run_id else store.latest_run(session)
        if run is None:
            raise HTTPException(404, "no run")
        return PlainTextResponse(
            export_run_script(store, run["run_id"], driver_of(session).workspace))

    @app.get("/api/methods.md")
    def methods(session: str, run_id: str | None = None):
        run = store.get_run(run_id) if run_id else store.latest_run(session)
        if run is None:
            raise HTTPException(404, "no run")
        doc = export_provenance(store, run["run_id"], contract=contract,
                                workspace=driver_of(session).workspace)
        md, source = driver_of(session).brain.narrate(doc)
        return PlainTextResponse(md, headers={"X-Narrate-Source": source})

    @app.get("/api/trace")
    def trace(session: str, run_id: str | None = None):
        run = store.get_run(run_id) if run_id else store.latest_run(session)
        if run is None:
            return []
        return store.trace_of(run["run_id"])

    # ---------------- 全局 SSE ----------------

    @app.get("/api/events")
    async def events(session: str, request: Request):
        d = driver_of(session)

        async def gen():
            q = d.bus.subscribe()
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        import asyncio

                        event = await asyncio.wait_for(q.get(), timeout=15.0)
                        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    except TimeoutError:
                        yield ": keepalive\n\n"
            finally:
                d.bus.unsubscribe(q)

        return StreamingResponse(gen(), media_type="text/event-stream")

    # ---------------- 静态 UI ----------------

    if PROTOTYPE_DIR.exists():
        app.mount("/", StaticFiles(directory=str(PROTOTYPE_DIR), html=True), name="ui")

    return app


def main() -> None:
    import uvicorn

    app = create_app()
    uvicorn.run(app, host=os.environ.get("INSAR_HOST", "127.0.0.1"),
                port=int(os.environ.get("INSAR_PORT", "8873")))


if __name__ == "__main__":
    main()
