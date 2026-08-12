"""FastAPI 层:人与 LLM 共用同一套接口(AGENT-DESIGN §2)。

协议纪律(吸收 pi RPC,absorb-E4/E8):
  - 回合是流式 NDJSON:POST 响应只代表「已接受」,过程与结果走事件流,
    绝不给同一请求发第二个终态响应。
  - 运行中投递消息必须显式声明 deliver_as=steer|follow_up|next_run,否则 400。
  - 全局事件走 SSE(/api/events);单回合事件走该回合自己的 NDJSON 流。

启动:python -m insar_agent.api.app(默认 127.0.0.1:8873,INSAR_HOME 可配)。
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import (FileResponse, JSONResponse, PlainTextResponse,
                               StreamingResponse)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from insar_agent.audit.contract import load_contract
from insar_agent.brain.facade import Brain
from insar_agent.brain.provider import LLMProvider
from insar_agent.core.actions import ACTIONS
from insar_agent.core.db import Database
from insar_agent.core.ledger import export_provenance
from insar_agent.core.stale import preview_change
from insar_agent.core.store import DELIVER_AS, Store
from insar_agent.loop.driver import Driver
from insar_agent.planner.feasibility import narrow_methods
from insar_agent.planner.plan import fork_run
from insar_agent.api.admin_router import create_admin_router
from insar_agent.api.artifacts_router import create_artifacts_router
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

log = logging.getLogger(__name__)

# session_id 会成为 workspace 子目录名(home/sessions/<id>):必须在 API 边界
# 挡掉路径穿越与文件系统非法名,否则 GET /api/registry?session=../../x 之类的
# 请求会在 home 之外创建目录(driver 构造时 mkdir)。
_SESSION_ID_MAX = 64
_SESSION_FORBIDDEN_CHARS = set('/\\:*?"<>|')
_WINDOWS_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL",
                           *(f"COM{i}" for i in range(1, 10)),
                           *(f"LPT{i}" for i in range(1, 10))}

#: /api/actions 可入队的动作闭集(core/actions.ACTIONS + 掉线消息 USER_MESSAGE,
#: 见 schema.sql pending_actions.action 注释);其中步骤级动作需要整数 target。
_KNOWN_ACTIONS = frozenset(ACTIONS) | {"USER_MESSAGE"}
_STEP_ACTIONS = frozenset({"RESET", "SKIP", "SET_METHOD", "SET_PARAMS"})

#: SQLite INTEGER 上限之内的宽松步骤号边界(流水线实际只有 1-11)
_STEP_ID_MAX = 1_000_000

#: 影像面板可直读的图像扩展名闭集(→ Content-Type)。svg 显式排除:
#: 它可携带脚本,作为同源文档打开会成为 XSS 面;位图格式无此风险。
_IMAGE_MEDIA_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp",
}

#: 三档尺寸契约(HyP3 式,engines/figures.py 的产物目录约定):
#: <name>_browse.* / <name>_thumb.* 是 <name>.* 的浏览/缩略档,
#: 列表时归并进基图条目(url/thumbUrl),不单独成条目。
_TIER_SUFFIXES = ("_browse", "_thumb")

#: 元数据 sidecar(<name>.json)的读取上限:防坏文件/误命名的大 JSON 拖垮列表
_SIDECAR_MAX_BYTES = 64 * 1024


def read_sidecar_meta(image: Path) -> dict | None:
    """读图件同名 .json sidecar(engines/figures.py 随图落盘的元数据)。

    容忍一切失败:缺文件/超限/坏 JSON/顶层非对象都返回 None,
    列表端点绝不因一个坏 sidecar 而 500。
    """
    sidecar = image.with_suffix(".json")
    try:
        if not sidecar.is_file() or sidecar.stat().st_size > _SIDECAR_MAX_BYTES:
            return None
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # ValueError 覆盖 JSONDecodeError/UnicodeDecodeError
        return None
    return data if isinstance(data, dict) else None


def check_session_id(session_id: str) -> str:
    """校验 session_id 可安全用作目录名;不合法直接 400(结构化 detail)。"""
    sid = session_id
    if not sid or len(sid) > _SESSION_ID_MAX:
        raise HTTPException(400, f"session 不合法:长度须为 1-{_SESSION_ID_MAX} 字符")
    if any(c in _SESSION_FORBIDDEN_CHARS or ord(c) < 0x20 or ord(c) == 0x7F
           for c in sid):
        raise HTTPException(400, "session 不合法:不允许路径分隔符、控制字符或 "
                                 '\'/\\:*?"<>|\' 字符')
    if sid[0] == " " or sid[-1] in " .":
        raise HTTPException(400, "session 不合法:首尾不允许空格,结尾不允许 '.'")
    if sid.split(".")[0].upper() in _WINDOWS_RESERVED_NAMES:
        raise HTTPException(400, f"session 不合法:{sid} 是 Windows 保留设备名")
    try:
        # 孤代理(U+D800-DFFF)可经原始字节体注入:无法编码 UTF-8,落到 SQLite
        # 绑定或 mkdir 时抛 UnicodeEncodeError 逃逸为 500(fuzz 波次发现 1,P1)
        sid.encode("utf-8")
    except UnicodeEncodeError:
        raise HTTPException(400, "session 不合法:含无法编码为 UTF-8 的码位(孤代理)")
    return sid


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

    @app.exception_handler(RequestValidationError)
    def _on_validation_error(request: Request, exc: RequestValidationError):
        # 422 错误体会回显出错输入;体内 NaN/Infinity(json.loads 的非标准扩展,
        # requests 类客户端可发出)经默认渲染 allow_nan=False 二次抛错逃逸为
        # 500(fuzz 波次发现 2,P2)。非有限浮点替换为 None 后安全渲染。
        safe = jsonable_encoder(
            {"detail": exc.errors()},
            custom_encoder={float: lambda v: v if math.isfinite(v) else None})
        return JSONResponse(status_code=422, content=safe)

    app.include_router(create_setup_router(home))  # 环境向导(/api/setup/*,settings.json 与 DB 同目录)
    app.include_router(version_router)             # 版本信息与更新检查(/api/version*)
    app.include_router(create_admin_router(store))  # 外部终结与运维视图(/api/admin/*,absorb-E6)
    app.include_router(create_artifacts_router(store))  # 产物清单(/api/artifacts,文件面板数据源)
    from insar_agent.api.data_router import create_data_router; app.include_router(create_data_router(store))  # 点位时序数据

    def driver_of(session_id: str) -> Driver:
        check_session_id(session_id)  # 边界校验:id 将成为目录名(见模块头注释)
        if session_id not in drivers:
            ws = home / "sessions" / session_id
            drivers[session_id] = Driver(
                store, workspace=ws, brain=Brain(LLMProvider()),
                allow_simulated=os.environ.get("INSAR_ALLOW_SIMULATED", "1") == "1")
            store.create_session(session_id, session_id)
        return drivers[session_id]

    # 回合泵任务的强引用(asyncio 只弱引用 task,不留强引用会被 GC 掐断)
    turn_tasks: set[asyncio.Task] = set()

    def ndjson(agen):
        """回合流:响应通道与回合执行解耦(协议纪律:POST 只代表「已接受」)。

        - 客户端中途断开只关闭响应,不取消回合:run 归服务端所有,继续推进到
          终态(事件仍发布到 SSE 总线),不留 status=running 却无人跟随的孤儿;
          用户主动取消走 /api/abort(control 位)。
        - 回合内部异常转成一条 note 事件(流协议里错误是事件,不是裸断连),
          细节进服务端日志。
        """
        async def gen():
            queue: asyncio.Queue = asyncio.Queue()

            async def pump():
                try:
                    async for event in agen:
                        queue.put_nowait(json.dumps(event, ensure_ascii=False) + "\n")
                except Exception:  # noqa: BLE001 —— 转结构化事件,绝不裸断连
                    log.exception("回合流内部异常")
                    queue.put_nowait(json.dumps(
                        {"t": "note", "tone": "bad",
                         "text": "服务内部错误,回合中止(详见服务端日志)"},
                        ensure_ascii=False) + "\n")
                finally:
                    queue.put_nowait(None)

            task = asyncio.create_task(pump())
            turn_tasks.add(task)
            task.add_done_callback(turn_tasks.discard)
            while True:
                line = await queue.get()
                if line is None:
                    break
                yield line

        return StreamingResponse(gen(), media_type="application/x-ndjson")

    def resolve_run(session_id: str, run_id: str | None, *,
                    required: bool = True) -> dict | None:
        """按会话解析 run 并做归属校验。

        run_id 缺省 → 该会话最近一个 run;给了 run_id 但不属于该会话 → 404
        (统一按「不存在」处理,不泄露其他会话的 run 是否存在)。
        """
        run = store.get_run(run_id) if run_id else store.latest_run(session_id)
        if run is None:
            if required:
                raise HTTPException(404, "no run")
            return None
        if run["session_id"] != session_id:
            raise HTTPException(404, f"run {run['run_id']} 不存在或不属于会话 {session_id}")
        return run

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
        check_session_id(body.id)  # 先校验再落库:非法 id 不留半截会话行
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
        # WSL 引擎环境探测:发行版可达时并入(面板显示带 (wsl) 后缀的引擎)。
        # 纯查询、失败静默 —— 没装 WSL 的机器该端点行为不变。
        try:
            from insar_agent.runtime.wsl_probe import merge_wsl_probe, probe_wsl_engines

            wsl_result = probe_wsl_engines(timeout=30.0)
            if wsl_result.get("ok"):
                merge_wsl_probe(probe, wsl_result)
        except Exception:
            pass
        return {
            "probe": probe.to_dict(),
            "thresholds": [{"key": k, "value": t.value, "source": t.source,
                            "ref": t.ref, "status": t.status}
                           for k, t in contract.items()],
        }

    @app.get("/api/state")
    def state(session: str, run_id: str | None = None):
        run = resolve_run(session, run_id, required=False)
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
        d = driver_of(body.session)  # 先做 session 校验,再解析 run
        run = resolve_run(body.session, body.run_id, required=False)
        if body.run_id and run is None:
            raise HTTPException(404, f"run {body.run_id} 不存在")
        if body.step_ids:
            # 流式响应一旦开始就无法改状态码:step_ids 必须在开流前校验,
            # 否则非法 id 会在 driver 循环里 KeyError 炸断连接
            if run is None:
                raise HTTPException(404, "no run")
            planned = {s.step_id for s in store.load_steps(run["run_id"])}
            bad = [sid for sid in body.step_ids if sid not in planned]
            if bad:
                raise HTTPException(400, f"步骤 id 不在该 run 的计划内:{bad}")
        return ndjson(d.execute(body.session, body.run_id, body.step_ids))

    @app.post("/api/resume")
    def resume(body: PipelineBody):
        return ndjson(driver_of(body.session).resume(body.session))

    @app.post("/api/abort")
    def abort(body: PipelineBody):
        run = resolve_run(body.session, body.run_id)  # 404 覆盖不存在/不属于该会话
        driver_of(body.session).request_cancel(run["run_id"])
        return JSONResponse({"accepted": True}, status_code=202)

    # ---------------- 消息与干预 ----------------

    @app.post("/api/message")
    def message(body: MessageBody):
        if store.get_session(body.session) is None:
            # chat_messages 有外键约束:不先查会话,未知 session 会 IntegrityError → 500
            raise HTTPException(404, f"会话 {body.session} 不存在")
        if body.deliver_as is not None and body.deliver_as not in DELIVER_AS:
            raise HTTPException(
                400, f"未知投递语义 {body.deliver_as}(可用:{'|'.join(DELIVER_AS)})")
        active = running_run(body.session)
        if active and body.deliver_as not in DELIVER_AS:
            # absorb-E4(pi rpc.md:56-65):运行中投递必须显式声明语义
            raise HTTPException(
                400, "run 正在执行:必须指定 deliver_as=steer|follow_up|next_run")
        store.append_chat(body.session, "user", body.text,
                          meta={"deliver_as": body.deliver_as} if body.deliver_as else None)
        return JSONResponse({"accepted": True, "deliver_as": body.deliver_as},
                            status_code=202)

    @app.post("/api/actions")
    def actions(body: ActionBody):
        # 入队即校验(闭集 + payload 形状):干预队列是跨回合消费的,坏动作
        # 入队后会在之后某次 turn/execute 的流中间引爆(int(target)/payload KeyError),
        # 那时已无法给客户端返回错误 —— 必须挡在入口。
        if body.action not in _KNOWN_ACTIONS:
            raise HTTPException(
                400, f"未知动作 {body.action}(可用:{sorted(_KNOWN_ACTIONS)})")
        if body.deliver_as not in DELIVER_AS:
            raise HTTPException(
                400, f"未知投递语义 {body.deliver_as}(可用:{'|'.join(DELIVER_AS)})")
        if body.scope not in ("step", "run"):
            raise HTTPException(400, f"未知 scope {body.scope}(可用:step|run)")
        if body.action in _STEP_ACTIONS:
            try:
                sid = int(body.target)
            except (TypeError, ValueError):
                raise HTTPException(
                    400, f"{body.action} 的 target 必须是步骤号,收到 {body.target!r}")
            cap = REGISTRY.get(sid)
            if cap is None:
                raise HTTPException(400, f"未知步骤 {sid}")
            if body.action == "SET_METHOD":
                method = body.payload.get("method")
                if not isinstance(method, str) or cap.method(method) is None:
                    raise HTTPException(
                        400, f"第 {sid} 步没有方法 {method!r}"
                             f"(候选:{[m.id for m in cap.methods]})")
            if body.action == "SET_PARAMS":
                params = body.payload.get("params")
                if not isinstance(params, dict):
                    raise HTTPException(400, "SET_PARAMS 需要 payload.params 为 JSON 对象")
                errors = cap.validate_params(params)
                if errors:
                    raise HTTPException(400, f"参数校验失败:{errors}")
        # 动作归属:入队即绑定 run(缺省取该会话最近 run)。没有归属的动作会被
        # 任何 run 的 driver 消费(跨 run/会话互吞,REVIEW P1),溯源也无法入账
        run = resolve_run(body.session, body.run_id, required=False)
        if body.action == "KILL" and run:
            driver_of(body.session).request_cancel(run["run_id"])
        action_id = store.push_action(scope=body.scope, target=body.target,
                                      action=body.action, payload=body.payload,
                                      deliver_as=body.deliver_as,
                                      run_id=run["run_id"] if run else None)
        return JSONResponse({"accepted": True, "id": action_id}, status_code=202)

    @app.get("/api/impact")
    def impact(session: str, step: int = Query(ge=0, le=_STEP_ID_MAX),
               method: str | None = None, params: str | None = None,
               run_id: str | None = None):
        run = resolve_run(session, run_id)
        d = driver_of(session)
        if store.load_step(run["run_id"], step) is None:
            raise HTTPException(404, f"步骤 {step} 不在该 run 中")
        if params is not None:
            try:
                params_patch = json.loads(params)
            except json.JSONDecodeError as exc:
                raise HTTPException(400, f"params 不是合法 JSON:{exc}")
            if not isinstance(params_patch, dict):
                raise HTTPException(400, "params 必须是 JSON 对象(参数名 → 值)")
        else:
            params_patch = None
        if method is not None:
            cap = d.registry.get(step)
            if cap is not None and cap.method(method) is None:
                raise HTTPException(
                    400, f"第 {step} 步没有方法 {method!r}"
                         f"(候选:{[m.id for m in cap.methods]})")
        imp = preview_change(
            store, run["run_id"], step, registry=d.registry,
            tool_versions=d.probe().tool_versions(), method=method,
            params_patch=params_patch)
        return {
            "changedStep": imp.changed_step, "reason": imp.reason,
            "affected": imp.affected, "rerunMinutes": imp.rerun_minutes,
            "rerunBasis": imp.rerun_basis,
        }

    @app.post("/api/fork")
    def fork(body: ForkBody):
        d = driver_of(body.session)
        run = resolve_run(body.session, body.run_id)  # fork_run 对未知 run 抛 KeyError
        changes = {int(k): v for k, v in body.changes.items()}
        planned = {s.step_id for s in store.load_steps(run["run_id"])}
        for sid, change in changes.items():
            if sid not in planned:
                raise HTTPException(400, f"步骤 id 不在该 run 的计划内:{sid}")
            cap = d.registry.get(sid)
            m = change.get("method")
            if m is not None and (not isinstance(m, str)
                                  or (cap is not None and cap.method(m) is None)):
                raise HTTPException(
                    400, f"第 {sid} 步没有方法 {m!r}"
                         f"(候选:{[x.id for x in cap.methods] if cap else []})")
            if "params" in change and not isinstance(change["params"], dict):
                raise HTTPException(400, f"changes[{sid}].params 必须是 JSON 对象")
        try:
            plan = fork_run(store, run["run_id"], registry=d.registry,
                            changes=changes, probe=d.probe())
        except ValueError as exc:  # cap.validate_params 拒绝(未声明参数/越界)
            raise HTTPException(400, str(exc))
        return {"runId": plan.run_id,
                "steps": [{"id": p.step_id, "state": p.state, "method": p.method}
                          for p in plan.steps]}

    # ---------------- 导出 ----------------

    @app.get("/api/provenance")
    def provenance(session: str, run_id: str | None = None):
        run = resolve_run(session, run_id)
        return export_provenance(store, run["run_id"], contract=contract,
                                 workspace=driver_of(session).workspace)

    @app.get("/api/run.sh")
    def run_script(session: str, run_id: str | None = None):
        run = resolve_run(session, run_id)
        return PlainTextResponse(
            export_run_script(store, run["run_id"], driver_of(session).workspace))

    @app.get("/api/methods.md")
    def methods(session: str, run_id: str | None = None):
        run = resolve_run(session, run_id)
        doc = export_provenance(store, run["run_id"], contract=contract,
                                workspace=driver_of(session).workspace)
        md, source = driver_of(session).brain.narrate(doc)
        return PlainTextResponse(md, headers={"X-Narrate-Source": source})

    @app.get("/api/trace")
    def trace(session: str, run_id: str | None = None):
        run = resolve_run(session, run_id, required=False)
        if run is None:
            return []
        return store.trace_of(run["run_id"])

    @app.get("/api/logs")
    def logs(session: str, step: int = Query(ge=0, le=_STEP_ID_MAX),
             run_id: str | None = None, tail_kb: int = Query(64, ge=1, le=1024)):
        """步骤日志尾部(面板 8「终端」):读 log_path 末尾 N KB;无 run/步骤/日志文件 → 404。"""
        run = resolve_run(session, run_id)
        step_row = store.load_step(run["run_id"], step)
        if step_row is None:
            raise HTTPException(404, "no step")
        path = Path(step_row.log_path) if step_row.log_path else None
        if path is None or not path.exists():
            raise HTTPException(404, "no log file")
        size = path.stat().st_size
        limit = tail_kb * 1024
        with path.open("rb") as f:
            if size > limit:
                f.seek(size - limit)
            data = f.read()
        text = data.decode("utf-8", errors="replace")
        if size > limit and "\n" in text:
            text = text.split("\n", 1)[1]  # 掐掉截断处的半行
        return PlainTextResponse(text, headers={
            "X-Log-Size": str(size),
            "X-Log-Truncated": "1" if size > limit else "0",
        })

    # ---------------- 图像产物(面板 2「影像」) ----------------

    def resolve_artifact_file(run: dict, rel_path: str) -> Path | None:
        """产物相对路径 → run 工作区内的绝对路径;越界一律返回 None。

        artifacts.path 由执行器落库为相对工作区的路径,但该值可能来自
        坏数据/被篡改的 DB 行 —— 与 check_session_id 同理,读文件前必须
        在边界处规范化复核:绝对路径、盘符、../ 穿越都不放行。
        调用方对 None 统一按 404 处理,错误信息不携带磁盘路径。
        """
        base = Path(run["workspace"]).resolve()
        rel = Path(rel_path)
        if rel.is_absolute() or rel.drive:
            return None
        target = (base / rel).resolve()
        if target == base or not target.is_relative_to(base):
            return None
        return target

    @app.get("/api/figures")
    def figures(session: str, run_id: str | None = None):
        """该 run 全部图像类产物的清单(按扩展名闭集过滤,文件已缺失的行不列)。

        条目含 step/artId/文件名/尺寸/mtime 与三档 url(三档尺寸契约,
        engines/figures.py 的产物目录约定):
          - url      浏览档(_browse 存在时优先,灯箱用)
          - fullUrl  原图(「查看原图」/下载)
          - thumbUrl 缩略档(_thumb 存在时优先,网格用)
        三档缺档一律回退原图;_browse/_thumb 文件不单独成条目。
        同名 .json sidecar 存在且可解析时并入 meta 字段(坏文件容忍不并入)。
        尺寸与 mtime 取落盘原图实测值,不信 DB 记录(可能已被覆写)。
        """
        run = resolve_run(session, run_id, required=False)
        if run is None:
            return {"run": None, "figures": []}

        def file_url(art: dict, member: str | None = None) -> str:
            q = {"session": session, "run_id": run["run_id"],
                 "step": art["step_id"], "art_id": art["art_id"]}
            if member:
                q["file"] = member
            return "/api/artifact-file?" + urlencode(q)

        def entry(art: dict, target: Path, member: str | None = None,
                  browse: str | None = None, thumb: str | None = None) -> dict:
            st = target.stat()
            full = file_url(art, member)
            item = {
                "step": art["step_id"], "artId": art["art_id"],
                "name": target.name, "path": art["path"], "kind": art["kind"],
                "size": st.st_size, "mtime": st.st_mtime,
                "url": file_url(art, browse) if browse else full,
                "fullUrl": full,
                "thumbUrl": file_url(art, thumb) if thumb else full,
            }
            meta = read_sidecar_meta(target)
            if meta is not None:
                item["meta"] = meta
            return item

        items = []
        for art in store.artifacts_of(run["run_id"]):
            target = resolve_artifact_file(run, art["path"])
            if target is None:
                continue
            if Path(art["path"]).suffix.lower() in _IMAGE_MEDIA_TYPES:
                if target.is_file():
                    items.append(entry(art, target))
            elif target.is_dir():
                # 目录型产物(注册表里 figures 声明的是 products/figures 目录):
                # 枚举目录内图像文件 —— 真实链的图件都长在这里,只按产物路径
                # 后缀过滤会让画廊对标准管线永远空转(2026-08-12 终验发现)
                children = sorted(p for p in target.iterdir()
                                  if p.is_file()
                                  and p.suffix.lower() in _IMAGE_MEDIA_TYPES)
                by_stem = {p.stem: p.name for p in children}
                listed = 0
                for child in children:
                    stem = child.stem
                    # 基图存在的 _browse/_thumb 档并入基图条目,不单独列;
                    # 孤档(基图缺失)仍按普通图件列出,列表不吞真实文件
                    if any(stem.endswith(sfx) and stem[:-len(sfx)] in by_stem
                           for sfx in _TIER_SUFFIXES):
                        continue
                    items.append(entry(art, child, member=child.name,
                                       browse=by_stem.get(stem + "_browse"),
                                       thumb=by_stem.get(stem + "_thumb")))
                    listed += 1
                    if listed >= 100:
                        break
        items.sort(key=lambda x: (x["step"], x["artId"], x["name"]))
        return {"run": run["run_id"], "figures": items}

    @app.get("/api/artifact-file")
    def artifact_file(session: str, art_id: str,
                      step: int = Query(ge=0, le=_STEP_ID_MAX),
                      run_id: str | None = None, file: str | None = None):
        """产物图像文件本体(只读 FileResponse)。

        安全边界:非图像扩展名 400;产物不存在/路径越界/跨会话一律 404,
        且不泄露磁盘路径(与 resolve_run 的「不存在」口径一致)。
        file 参数用于目录型产物(如 products/figures)取内部成员:成员解析后
        必须仍落在产物目录内(防 ../ 越界),且同受图像扩展名闭集约束。
        """
        run = resolve_run(session, run_id)
        art = next((a for a in store.artifacts_of(run["run_id"], step)
                    if a["art_id"] == art_id), None)
        if art is None:
            raise HTTPException(404, "no artifact")
        target = resolve_artifact_file(run, art["path"])
        if target is None:
            raise HTTPException(404, "no artifact file")
        if file is not None:
            if not target.is_dir():
                raise HTTPException(404, "no artifact file")
            try:
                member = (target / file).resolve()
                member.relative_to(target.resolve())
            except (ValueError, OSError):
                raise HTTPException(404, "no artifact file")
            target = member
            media = _IMAGE_MEDIA_TYPES.get(target.suffix.lower())
        else:
            media = _IMAGE_MEDIA_TYPES.get(Path(art["path"]).suffix.lower())
        if media is None:
            raise HTTPException(
                400, f"仅支持图像类产物({'/'.join(sorted(e.lstrip('.') for e in _IMAGE_MEDIA_TYPES))})")
        if not target.is_file():
            raise HTTPException(404, "no artifact file")
        return FileResponse(target, media_type=media)

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
