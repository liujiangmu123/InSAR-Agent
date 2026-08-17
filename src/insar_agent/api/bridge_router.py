"""pi 桥接端点(pi-insar 扩展的后端数据面)。

设计见 reference/PLAN-pi-foundation-P0-2026-08-14.md §A/§D:pi 在顶层作聊天 UI,
InSAR 能力作 pi 扩展经 HTTP 调本后端。本 router 提供扩展**新增**需要、而现有端点
未覆盖的最小面:

- GET  /api/monitor  —— 侧边栏"被监控的流程"数据契约:11 步 × 五阶段 + 当前步 +
  进度 + 证据级 + 自由模式 + 脏标(taint)计数。纯读,不走 driver_of(不为未知
  会话建目录);证据用 run 行里的 workspace 路径按需算,失败降级为 None 不阻断。
  session 省略或 @latest 时取本机 store 最近活动会话的最新 run(桌面右栏无
  会话选择器);显式 session= 仍只看该会话,跨会话 run_id 仍 404。
- GET/POST /api/mode —— 渐进式自由的会话级模式(free|strict)。以 home 下 JSON
  原子落盘持久化(tmp+replace),供严格模式闸门与 provenance 盖章读取。

现有端点(/api/turn 规划、/api/pipeline 执行、/api/fork、/api/provenance、
/api/run.sh、/api/state、/api/runs、/api/events SSE、/api/health)由扩展直接复用,
不在此重复。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from insar_agent.audit.contract import load_contract
from insar_agent.audit.ladder import compute_evidence
from insar_agent.core.store import Store

# 五阶段单字母(侧边栏字形闭集,对齐设计 §D.2)
_STAGE_LETTER = {
    "PREPARED": "P", "LAUNCHED": "L", "RUNNING": "R",
    "COLLECTED": "C", "VERIFIED": "V",
}
# 完成态(计入进度分母的"已结算")
_DONE_STATES = ("done", "skipped")
FREEDOM_MODES = ("free", "strict")
DEFAULT_MODE = "free"
# 桌面右栏无会话选择器,用此哨兵(或省略 session)钉本机最近活动会话
_LATEST_SESSION = "@latest"


class ModeBody(BaseModel):
    session: str
    mode: str


class JournalBody(BaseModel):
    """pi 侧一次工具调用的台账外记录(观察日志,非 provenance)。"""
    session: str
    tool: str
    mode: str | None = None
    is_error: bool | None = None
    input_digest: str | None = None   # 截断后的入参摘要,扩展侧负责截断
    ts: float | None = None           # 扩展侧时钟;服务端另记 received_at


def create_bridge_router(store: Store, home: Path | str,
                         contract: dict | None = None) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["bridge"])
    contract = contract if contract is not None else load_contract()
    modes_path = Path(home) / "pi_bridge_modes.json"

    def _load_modes() -> dict:
        try:
            return json.loads(modes_path.read_text("utf-8"))
        except Exception:  # noqa: BLE001 —— 文件缺失/损坏一律回退默认,读端点不因它 500
            return {}

    def _save_modes(data: dict) -> None:
        modes_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = modes_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
        tmp.replace(modes_path)  # 原子发布(同目录 rename,对齐仓库 tmp+replace 决议)

    def _mode_of(session: str) -> str:
        return _load_modes().get(session, DEFAULT_MODE)

    def _empty_monitor(session: str) -> dict:
        return {"session": session, "run": None, "steps": [], "current": None,
                "progress": {"total": 0, "done": 0, "pct": 0},
                "evidence": None, "mode": _mode_of(session), "taints": 0}

    def _latest_active_session() -> str | None:
        # 活动时间=最新 run(流水线在哪就钉哪);归档会话不进右栏;无 run 才回退创建序
        seen: set[str] = set()
        for run in store.list_runs():  # created_at DESC
            sid = run["session_id"]
            if sid in seen:
                continue
            seen.add(sid)
            sess = store.get_session(sid)
            if sess is not None and sess.get("archived") is None:
                return sid
        sessions = store.list_sessions()  # 未归档,created_at DESC
        return sessions[0]["session_id"] if sessions else None

    @router.get("/monitor")
    def monitor(session: str | None = Query(None), run_id: str | None = None) -> dict:
        """侧边栏数据面。缺省取会话最新 run;无 run 返回空壳(mode 仍有效)。

        session 省略或 @latest:本机 store 最近活动会话;无任何会话则空壳。
        """
        if not session or session == _LATEST_SESSION:
            resolved = _latest_active_session()
            if resolved is None:
                return _empty_monitor(_LATEST_SESSION)
            session = resolved
        run = store.get_run(run_id) if run_id else store.latest_run(session)
        if run is None:
            return _empty_monitor(session)
        if run["session_id"] != session:
            # 跨会话不泄露其他 run 的存在(对齐 resolve_run 口径);@latest 解析后同样校验
            raise HTTPException(404, "run 不存在或不属于该会话")

        rid = run["run_id"]
        steps = store.load_steps(rid)
        # 耗时取 commands 台账最后一条已结算行,不用 trace 事件墙钟跨度:
        # 单次尝试往往只有 1 条审计事件;RESET 重跑后旧行保留,max(ts)-min(ts)
        # 会把失败到重跑之间的等待算进「耗时」。
        cmd_meta: dict[int, dict] = {}
        for c in store.commands_of(rid):
            sid = int(c["step_id"])
            slot = cmd_meta.setdefault(sid, {"attempts": 0, "duration": None})
            slot["attempts"] += 1
            if c["exit_code"] is not None:
                dur = c["duration"]
                slot["duration"] = float(dur) if dur is not None else None
        out_steps: list[dict] = []
        taints = 0
        current: dict | None = None
        for s in steps:
            if s.stale:
                taints += 1
            meta = cmd_meta.get(s.step_id, {})
            out_steps.append({
                "step": s.step_id, "name": s.name, "capability": s.capability,
                "method": s.method, "state": s.state, "stage": s.stage,
                "stage_letter": _STAGE_LETTER.get(s.stage, "-"),
                "stale": s.stale, "stale_reason": s.stale_reason,
                "failure_class": s.failure_class, "exit_code": s.exit_code,
                "run_ok": s.run_ok,
                "duration": meta.get("duration"),
                "attempts": int(meta.get("attempts", 0)),
            })
            if s.state == "running" and current is None:
                current = {"step": s.step_id, "name": s.name,
                           "method": s.method, "stage": s.stage}

        total = len(steps)
        done = sum(1 for s in steps if s.state in _DONE_STATES)
        pct = int(round(100 * done / total)) if total else 0

        try:
            ws = run.get("workspace")
            evidence = compute_evidence(
                store, rid, contract,
                workspace=Path(ws) if ws else None).to_dict()
        except Exception:  # noqa: BLE001 —— 证据算不出不该拖垮侧边栏刷新
            evidence = None

        return {
            "session": session,
            "run": {
                "run_id": rid, "scenario": run.get("scenario"),
                "status": run.get("status"),
                "simulated": bool(run.get("simulated")),
                "parent_run_id": run.get("parent_run_id"),
            },
            "steps": out_steps,
            "current": current,
            "progress": {"total": total, "done": done, "pct": pct},
            "evidence": evidence,
            "mode": _mode_of(session),
            "taints": taints,
        }

    @router.get("/mode")
    def get_mode(session: str = Query(...)) -> dict:
        return {"session": session, "mode": _mode_of(session)}

    @router.post("/mode")
    def set_mode(body: ModeBody) -> dict:
        if body.mode not in FREEDOM_MODES:
            raise HTTPException(400, f"mode 须为 {FREEDOM_MODES} 之一,收到 {body.mode!r}")
        data = _load_modes()
        data[body.session] = body.mode
        _save_modes(data)
        return {"session": body.session, "mode": body.mode}

    journal_path = Path(home) / "pi_journal.ndjson"

    @router.post("/pi-journal")
    def append_journal(body: JournalBody) -> dict:
        """台账外(out-of-ledger)追加:回答"自由模式下 agent 做了什么"。

        只追加、不进 run provenance、不参与证据阶梯 —— 与科学账本物理分离,
        绝不为科学结论背书(设计红线,对齐 PLAN-pi-foundation-P0 §3)。
        """
        record = body.model_dump()
        record["received_at"] = time.time()
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        with journal_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return {"accepted": True}

    @router.get("/pi-journal")
    def read_journal(limit: int = Query(200, ge=1, le=2000)) -> dict:
        try:
            lines = journal_path.read_text("utf-8").splitlines()
        except FileNotFoundError:
            return {"entries": [], "total": 0}
        entries = []
        for line in lines[-limit:]:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # 半行写入(进程被杀)容忍:读端点不因坏行 500
        return {"entries": entries, "total": len(lines)}

    return router
