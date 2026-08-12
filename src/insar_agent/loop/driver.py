"""事件驱动主循环(AGENT-DESIGN §3.2)。

与竞品 for 循环的根本区别:LLM 调用只是事件处理的一种,不是循环的骨架。
长任务期间循环继续转:干预队列每秒消费(KILL 即时,其余当前步结束后生效,§4.5),
用户可随时继续对话(API 层并发调用 turn)。

驱动两类回合:
    turn(text)      规划回合:意图 → 探测 → 计划 → 候选决策点(不执行)
    execute(run)    执行回合:逐步跑五阶段执行器,消费干预,失败分诊,收尾审计

所有事件同时:yield 给调用方(HTTP 流式响应)+ 发布到 EventBus(全局 SSE)
+ 关键事件落 trace(轨迹面板)。监听器错误被隔离(absorb-E8)。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from pathlib import Path
from typing import AsyncIterator

from insar_agent.audit.contract import load_contract
from insar_agent.audit.verify import verify_metrics
from insar_agent.brain.facade import Brain
from insar_agent.core.actions import apply_action
from insar_agent.core.failures import DISPOSITIONS, FailureClass
from insar_agent.core.ledger import write_provenance
from insar_agent.core.store import Store
from insar_agent.engines import default_builder
from insar_agent.loop import events as ev
from insar_agent.loop.events import EventBus
from insar_agent.planner.plan import PlanResult, make_plan
from insar_agent.registry.capabilities import REGISTRY, topo_order
from insar_agent.registry.model import Capability
from insar_agent.report.script import write_run_script
from insar_agent.runtime.backend_select import backend_for_step
from insar_agent.runtime.executor import ExecContext, execute_step
from insar_agent.runtime.jobs import JobBackend, LocalJobBackend
from insar_agent.runtime.probe import ProbeResult, probe_environment
from insar_agent.runtime.stream import CancelToken


def compute_agent_hash(registry: dict[int, Capability]) -> str:
    """prompt + capabilities schema 哈希(DESIGN.md:644:agent 快照进 provenance)。"""
    payload = {
        str(sid): {
            "version": cap.version, "methods": [m.id for m in cap.methods],
            "params": {k: p.kind for k, p in cap.params.items()},
            "replay": cap.replay,
        } for sid, cap in registry.items()
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


class Driver:
    def __init__(self, store: Store, *, workspace: Path,
                 registry: dict[int, Capability] | None = None,
                 brain: Brain | None = None, backend=None,
                 allow_simulated: bool = True,
                 probe: ProbeResult | None = None,
                 poll: float = 0.5, startup_grace: float = 30.0,
                 idle_timeout_override: float | None = None,
                 total_timeout_override: float | None = None):
        self.store = store
        self.workspace = Path(workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.registry = registry or REGISTRY
        self.contract = load_contract()
        self.brain = brain or Brain(None)
        # 显式注入(测试/运维)则整个 run 固定用它;None = 每步按方法引擎选择
        # (isce2/snaphu 且 WSL 可达 → WslJobBackend,见 runtime/backend_select)
        self.backend = backend
        self.allow_simulated = allow_simulated
        self.bus = EventBus()
        self._probe = probe
        self._tokens: dict[str, CancelToken] = {}
        # 本 run 用过的自带保活的后端实例(WslJobBackend),run 收尾统一释放
        # keepalive(WSL P2:此前 sleep infinity 随 run 数量堆积泄漏)
        self._run_backends: dict[str, list[JobBackend]] = {}
        self._poll = poll
        self._startup_grace = startup_grace
        self._idle_override = idle_timeout_override
        self._total_override = total_timeout_override
        self.agent_hash = compute_agent_hash(self.registry)

    # ---------------- 基础设施 ----------------

    def _emit(self, event: dict) -> dict:
        self.bus.publish(event)
        return event

    def probe(self, *, refresh: bool = False) -> ProbeResult:
        if self._probe is None or refresh:
            self._probe = probe_environment(self.workspace, check_wsl=False)
        return self._probe

    def token_for(self, run_id: str) -> CancelToken:
        if run_id not in self._tokens:
            self._tokens[run_id] = CancelToken()
        return self._tokens[run_id]

    def request_cancel(self, run_id: str) -> None:
        """取消 = control 位不是状态(absorb-E3):意图先落盘(服务重启不丢),
        token 是同进程快路径。已在途的作业照常结算,只禁止新效果。"""
        self.store.request_cancel(run_id)
        self.token_for(run_id).cancel()

    def _backend_for(self, run: dict, cap: Capability, method_id: str) -> JobBackend:
        m = cap.method(method_id)
        backend = backend_for_step(engine=m.engine if m else "-",
                                   simulated=bool(run.get("simulated")),
                                   override=self.backend)
        # keepalive 释放钩子:登记本 run 用过的可释放后端(WslJobBackend 每步
        # 新建、不跨 run 共享 → 无需引用计数,run 收尾各释放各的;取舍:并发
        # run 各持一个 sleep infinity,多一两个空转进程可忽略,换来零共享状态。
        # 注入 override(backend is self.backend)可能跨 run 共享,生命周期归
        # 注入方,绝不代为释放。
        rid = str(run.get("run_id") or "")
        if (rid and backend is not self.backend
                and callable(getattr(backend, "release_keepalive", None))):
            bucket = self._run_backends.setdefault(rid, [])
            if all(b is not backend for b in bucket):
                bucket.append(backend)
        return backend

    def _exec_ctx(self, backend: JobBackend | None = None, emit=None) -> ExecContext:
        return ExecContext(
            store=self.store, backend=backend or self.backend or LocalJobBackend(),
            workspace=self.workspace,
            registry=self.registry, builder=default_builder, contract=self.contract,
            emit=emit or self._emit, poll=self._poll, startup_grace=self._startup_grace,
            idle_timeout_override=self._idle_override,
            total_timeout_override=self._total_override)

    # ---------------- 规划回合 ----------------

    async def turn(self, session_id: str, text: str) -> AsyncIterator[dict]:
        store = self.store
        session = store.get_session(session_id)
        if session is None:
            store.create_session(session_id, session_id)
            session = store.get_session(session_id)
        store.append_chat(session_id, "user", text)

        intent = self.brain.intent(text)
        if intent.need_form:
            yield self._emit(ev.ask(
                "无法从描述中识别场景,请补充:",
                [{"key": "scenario", "label": "场景", "options": ["quake", "permafrost", "landslide"]},
                 {"key": "region", "label": "区域"}, {"key": "dates", "label": "时间范围"}]))
            return
        sc = intent.scenario
        assert sc is not None
        mode = session["mode"]
        yield self._emit(ev.thinking(
            "解析意图与约束",
            f"区域:{sc.region or '待定'}\n目标:{sc.chain} 时序形变\n时间范围:{sc.dates or '待定'}\n"
            f"场景:{sc.label}(来源:{intent.source})\n"
            f"模式:{'专家(每步人工确认方法)' if mode == 'expert' else '向导(自动决策,关键节点征询)'}"))

        # ---- 环境探测(真实,不 mock) ----
        probe = self.probe()
        yield self._emit(ev.tool_start("probe", "probe", "runtime/probe.py --engines", "探测可用引擎"))
        for engine, version in sorted(probe.engines.items()):
            tone = "ok" if version else "warn"
            mark = f"{version}" if version else "✗ 缺失"
            yield self._emit(ev.tool_log("probe", f"{engine:<10} {mark}", tone))
        yield self._emit(ev.tool_log(
            "probe", f"磁盘 {probe.disk_free_gb:.0f} GB 可用 / CPU {probe.cpu_count} 核", "dim"))
        available = sum(1 for v in probe.engines.values() if v)
        yield self._emit(ev.tool_end(
            "probe", 0, f"{available}/{len(probe.engines)} 个引擎可用"))

        # ---- next_run 干预消费(absorb-E4) ----
        # next_run 面向"下次规划",无未来 run 可绑定;入队时记录的是当时会话
        # 最近 run 的 id,消费时据此排除其他会话的预约(无归属的旧行照旧消费)
        overrides: dict[int, dict] = {}
        for action in store.due_actions("next_run"):
            owner = store.get_run(action["run_id"]) if action.get("run_id") else None
            if owner and owner["session_id"] != session_id:
                continue
            if action["action"] in ("SET_METHOD", "SET_PARAMS"):
                sid = int(action["target"])
                overrides.setdefault(sid, {})
                if action["action"] == "SET_METHOD":
                    overrides[sid]["method"] = action["payload"]["method"]
                else:
                    overrides[sid].setdefault("params", {}).update(action["payload"]["params"])
                store.consume_action(action["id"])
                yield self._emit(ev.intervention(
                    f"应用上次预约的变更:第 {sid} 步 {action['payload']}"))

        # ---- 计划 ----
        # next_run 预约的变更(overrides)只能经 make_plan 进入新计划;复用既有
        # run 的分支不会应用它们 —— 有 overrides 时必须重新规划,否则动作已被
        # 消费却静默丢失(REVIEW P1:next_run 干预失效)。
        latest = store.latest_run(session_id)
        plan: PlanResult | None = None
        if latest and not overrides and latest["scenario"] == sc.key and latest["status"] in (
                "ready", "planning", "paused", "interrupted", "running", "done", "failed"):
            run_id = latest["run_id"]
        else:
            plan = make_plan(store, session_id, registry=self.registry, probe=probe,
                             scenario=sc, workspace=str(self.workspace),
                             intent={"text": text, "source": intent.source},
                             overrides=overrides or None,
                             allow_simulated=self.allow_simulated,
                             agent_hash=self.agent_hash)
            run_id = plan.run_id
            if plan.problems:
                for p in plan.problems:
                    yield self._emit(ev.note("bad", p))
                yield self._emit(ev.say([
                    "环境不满足执行条件(见上)。可安装引擎后重试,或使用模拟模式演示流程。"]))
                return
            if plan.simulated:
                yield self._emit(ev.note(
                    "warn", "引擎缺失:本次为模拟执行(演示流程用),证据级别封顶 runnable。"))

        # ---- 计划事件(形状对齐 mock) ----
        steps = store.load_steps(run_id)
        done_ids = [s.step_id for s in steps if s.state == "done"]
        todo = [s for s in steps if s.state in ("pending", "stale", "interrupted", "orphaned")]
        decision_step = todo[0].step_id if todo else None
        items = [
            {"n": 1, "text": "探测引擎与环境,收窄候选集", "st": "d"},
            {"n": 2, "text": f"已完成/可复用步骤:{done_ids or '无'}", "st": "d"},
            {"n": 3, "text": f"第 {decision_step} 步方法决策" if decision_step else "无待决策步骤",
             "st": "r" if decision_step else "d"},
            {"n": 4, "text": f"执行待跑步骤 {[s.step_id for s in todo]}", "st": "p"},
            {"n": 5, "text": "质量门 + provenance 导出", "st": "p"},
        ]
        yield self._emit(ev.plan(items))

        # ---- 决策点叙述 ----
        if decision_step is not None:
            cap = self.registry[decision_step]
            from insar_agent.planner.feasibility import narrow_methods

            feas = narrow_methods(cap, probe, scenario=sc.key,
                                  allow_simulated=self.allow_simulated)
            env_facts = f"可用引擎:{[e for e, v in probe.engines.items() if v] or '无(模拟)'}"
            pick = self.brain.select(cap, feas, env_facts=env_facts, prefer=sc.step_overrides
                                     .get(decision_step, {}).get("method"))
            reply = (f"计划就绪。第 {decision_step} 步「{cap.name}」建议 {pick.method_id}"
                     f"({pick.reason};来源:{pick.source})。"
                     f"待跑 {len(todo)} 步,确认后开始执行。")
            yield self._emit(ev.say([reply]))
            store.append_chat(session_id, "agent", reply)
            yield self._emit(ev.candidates(decision_step))
        else:
            reply = "全部步骤已完成。可改参数试探(fork)或导出报告。"
            yield self._emit(ev.say([reply]))
            store.append_chat(session_id, "agent", reply)

    # ---------------- 执行回合 ----------------

    async def execute(self, session_id: str, run_id: str | None = None,
                      step_ids: list[int] | None = None) -> AsyncIterator[dict]:
        store = self.store
        run = store.get_run(run_id) if run_id else store.latest_run(session_id)
        if run is None:
            yield self._emit(ev.note("bad", "没有可执行的 run,先发起规划"))
            return
        if run["status"] == "planning":
            # 计划存在未解决的可行性问题(make_plan problems)—— 拒绝执行,不带病上路
            yield self._emit(ev.note("bad", "计划存在可行性问题(缺引擎/方法不可行),"
                                            "解决后重新规划再执行"))
            return
        run_id = run["run_id"]
        token = self.token_for(run_id)
        if token.cancelled:
            self._tokens.pop(run_id, None)
            token = self.token_for(run_id)
        # E3:持久化取消意图 —— 重启后 token 丢了,control 位还在。读到
        # cancel_requested 就不启动任何新步骤,直接把 run 收尾为 interrupted
        # (在途作业由 wrapper 按文件契约自行结算,不抢杀)。
        if run.get("control") == "cancel_requested":
            store.set_run_status(run_id, "interrupted")
            store.clear_cancel(run_id)  # 意图已兑现,复位后允许显式重跑
            yield self._emit(ev.note(
                "warn", "检测到持久化的取消请求:不启动新步骤,run 收尾为 interrupted"))
            return
        # §4.11 运行锁:同一 run 只允许一个执行回合(并发 execute 会在执行器
        # 五阶段推进上撞 StageConflict 并可能双重启动作业 —— REVIEW P1)。
        # 租约带心跳:活跃回合每 ~5s 续租;崩溃回合约 60s 后可被接管(resume)。
        holder = uuid.uuid4().hex
        lease = f"run:{run_id}"
        if not store.acquire_lease(lease, holder, ttl=60.0, stale_after=60.0):
            yield self._emit(ev.note(
                "bad", "该 run 已有执行回合在进行(运行锁被占用);并发执行被拒绝。"
                       "若上一回合已崩溃,约 60 秒后重试即可接管。"))
            return
        try:
            async for event in self._execute_run(run_id, step_ids, token,
                                                 lease=lease, holder=holder):
                yield event
        finally:
            store.release_lease(lease, holder)
            # run 收尾(含 done/failed/interrupted/paused 与异常/断流)统一释放
            # 本 run 的 WSL keepalive:暂停/中断后 VM 允许空闲回收,续跑的
            # launch 会重新 ensure_keepalive(§4.8)
            self._release_run_backends(run_id)

    def _release_run_backends(self, run_id: str) -> None:
        for backend in self._run_backends.pop(run_id, []):
            try:
                backend.release_keepalive()
            except Exception:
                pass  # 释放失败不影响 run 收尾;残留保活进程随宿主进程退出而消亡

    async def _execute_run(self, run_id: str, step_ids: list[int] | None,
                           token: CancelToken, *, lease: str, holder: str
                           ) -> AsyncIterator[dict]:
        store = self.store
        run = store.get_run(run_id) or {}

        def _waiting_steps() -> list[int]:
            return [s.step_id for s in store.load_steps(run_id)
                    if s.state in ("pending", "stale", "interrupted",
                                   "orphaned", "running")]

        # ---- done run 重入防空转(2026-08-12 干预矩阵决策二) ----
        # 此前对 status=done 且无待跑步骤的 run 再次 execute 会空转收尾:重写
        # provenance/run.sh、重发 result/report(前端重复渲染终态卡)。现在:
        # 无待跑步骤时先消费排队干预(不吞:steer 与 follow_up 此刻都已到期),
        # 消费后仍无待跑步骤 → 一条 note 说明后直接返回;干预造出了待跑步骤
        # (SET_PARAMS 标脏/RESET 复位)→ 照常进入执行,本回合直接重跑。
        # 已有待跑步骤(如上一回合遗留的 stale)不走入口消费,由步间检查点按
        # 原语义消费(KILL 在那里仍是取消)。显式 step_ids 不走此检查(重验证)。
        if step_ids is None and run.get("status") == "done" and not _waiting_steps():
            for deliver_as in ("steer", "follow_up"):
                async for event in self._consume_actions_idle(run_id, deliver_as):
                    yield event
            if not _waiting_steps():
                yield self._emit(ev.note(
                    "ok", "run 已完成,无待跑步骤;改参数或 RESET 后可重跑"
                          "(显式指定 step_ids 可重验证)"))
                return
        store.set_run_status(run_id, "running")

        # 待跑集合:显式指定,或 pending+stale+interrupted+orphaned+running
        # (failed 需人工 RESET;running 是服务重启后的接回目标 —— REVIEW P1:
        # 漏掉它会让活作业永远接不回,下游步骤反因缺输入 contract_broken,
        # 执行器的 claim/reattach 语义本就支持接回,不会重跑已完成阶段)
        steps = {s.step_id: s for s in store.load_steps(run_id)}
        if step_ids is None:
            step_ids = [sid for sid, s in steps.items()
                        if s.state in ("pending", "stale", "interrupted",
                                       "orphaned", "running")]
        else:
            # 显式列表防御:skipped(云端已完成)步骤没有本地作业可执行,
            # 硬跑必然 contract_broken(2026-08-12 浏览器实测:前端用本地种子
            # 状态发来 [6..11],skipped 的第 6 步被强行执行后把 run 打成 failed)
            dropped = [sid for sid in step_ids
                       if sid in steps and steps[sid].state == "skipped"]
            if dropped:
                step_ids = [sid for sid in step_ids if sid not in dropped]
                yield self._emit(ev.note(
                    "warn", f"忽略云端已完成步骤 {dropped}:产物由云端交付,"
                            f"无本地作业可执行(显式重跑需先 RESET)"))
        step_ids = topo_order(step_ids)
        for sid in step_ids:  # 失效/中断步骤先复位(新 attempt);running 不复位,交执行器接回
            if steps[sid].state in ("stale", "interrupted", "orphaned"):
                store.reset_step_for_rerun(run_id, sid)

        total = len(step_ids)
        done_count = 0
        consecutive_failures: dict[str, int] = {}
        last_renew = time.monotonic()

        for sid in step_ids:
            # 干预消费:steer 在步骤之间生效(§4.5 / absorb-E4)
            async for event in self._consume_steer(run_id, sid):
                yield event
            run_row = store.get_run(run_id) or {}
            if run_row.get("status") == "paused":
                yield self._emit(ev.note("warn", "已暂停:当前进度已保留,PLAY 后继续"))
                return
            # E3:每步之间检查 control 位(token 是同进程快路径,control 位管
            # 跨进程/重启;外部终结者置位后活着的 driver 在此自停)
            if token.cancelled or run_row.get("control") == "cancel_requested":
                store.set_run_status(run_id, "interrupted")
                store.clear_cancel(run_id)
                yield self._emit(ev.note("warn", "运行已取消;已完成步骤保留"))
                return

            step = store.load_step(run_id, sid)
            if step.state == "skipped":
                # SKIP 干预在步间被消费后,该步已标 skipped;step_ids 是回合入口
                # 的快照,不复查状态会照跑并把 skipped 覆写成 done,静默吞掉用户
                # 的跳过决定(触发场景:执行中对未跑步骤排队 SKIP,矩阵测试发现)
                done_count += 1
                yield self._emit(ev.note(
                    "warn", f"第 {sid} 步已被标记跳过,不执行(产物沿用现状)"))
                yield self._emit(ev.overall(round(done_count / total * 100)))
                continue
            cap = self.registry[sid]
            yield self._emit(ev.step_start(sid))
            yield self._emit(ev.tool_start(
                f"s{sid}", f"[{sid:02d}/{max(step_ids)}]",
                f"{cap.name} --method {step.method}", cap.name, open_=True))

            # 执行 + 并行动作轮询(KILL 即时响应,§1.7)。执行器细节事件
            # (step.stage / 执行期 tool.log)经队列泵入回合流:双通道承诺对
            # 执行期同样成立 —— 此前只进 SSE 总线,前端 SSE 侧 busy 时全部
            # 跳过,UI 两条通道都收不到执行日志(契约对账 P1)。
            detail: asyncio.Queue = asyncio.Queue()

            def pump(event: dict, _q: asyncio.Queue = detail) -> dict:
                self._emit(event)
                _q.put_nowait(event)
                return event

            backend = self._backend_for(run, cap, step.method)
            exec_task = asyncio.create_task(
                execute_step(self._exec_ctx(backend, emit=pump), run_id, sid, token))
            while not exec_task.done():
                await asyncio.sleep(min(self._poll, 0.2))
                while not detail.empty():
                    yield detail.get_nowait()
                if time.monotonic() - last_renew > 5.0:  # 运行锁心跳续租
                    store.acquire_lease(lease, holder, ttl=60.0, stale_after=60.0)
                    last_renew = time.monotonic()
                for action in store.due_actions("steer", run_id=run_id):
                    if action["action"] == "KILL":
                        store.consume_action(action["id"])
                        self.request_cancel(run_id)  # E3:control 位同步落盘
                        yield self._emit(ev.intervention("取消当前步骤(KILL)", mode="steer"))
            while not detail.empty():  # 执行结束后冲刷余量,不丢尾部日志
                yield detail.get_nowait()
            result = exec_task.result()
            step = result.step

            if result.outcome == "done":
                done_count += 1
                consecutive_failures.clear()
                arts = [{"path": a["path"], "hash": a["fp"][:12]}
                        for a in store.artifacts_of(run_id, sid)]
                yield self._emit(ev.tool_end(f"s{sid}", 0, f"{cap.name}完成", arts))
                yield self._emit(ev.step_end(sid, 0))
                yield self._emit(ev.overall(round(done_count / total * 100)))
                continue

            # ---- 非正常结束 ----
            yield self._emit(ev.tool_end(f"s{sid}", step.exit_code if step.exit_code is not None else -1,
                                         result.detail or result.outcome))
            yield self._emit(ev.step_end(sid, step.exit_code if step.exit_code is not None else -1))

            if result.outcome == "interrupted":
                store.set_run_status(run_id, "interrupted")
                store.clear_cancel(run_id)  # E3:取消意图已兑现,消费 control 位
                yield self._emit(ev.note("warn", f"第 {sid} 步已取消 · 可续跑(已完成阶段保留)"))
                return
            if result.outcome == "orphaned":
                store.set_run_status(run_id, "interrupted")
                yield self._emit(ev.note(
                    "warn", f"第 {sid} 步:执行环境停止(非计算失败)。重启环境后续跑,不是重跑。"))
                store.append_trace(run_id=run_id, step_no=sid, phase=cap.phase,
                                   revision_trigger="orphaned", error_occurred=True,
                                   error_type="wsl_orphaned")
                return
            if result.outcome == "gate_stop":
                store.set_run_status(run_id, "failed")
                gate = ev.gate_stop(
                    f"第 {sid} 步被质量门拦停:{result.detail}",
                    suggestions=[f"换用其他方法({[m.id for m in cap.methods if m.id != step.method]})",
                                 "放宽阈值(需在 contract.yaml 里给出依据)"])
                store.append_trace(run_id=run_id, step_no=sid, phase=cap.phase,
                                   revision_trigger="gate_stop", error_occurred=True,
                                   error_type="data_quality")
                yield self._emit(gate)
                return

            # failed:分诊 → 处置建议(闭集,§4.12)
            log_text = ""
            if step.log_path and Path(step.log_path).exists():
                log_text = Path(step.log_path).read_text(encoding="utf-8", errors="replace")
            triage = self.brain.triage(log_text or (result.detail or ""))
            fc = step.failure_class or triage.failure_class.value
            disposition = DISPOSITIONS.get(FailureClass(fc) if fc in FailureClass._value2member_map_
                                           else FailureClass.UNKNOWN, {})
            consecutive_failures[fc] = consecutive_failures.get(fc, 0) + 1
            store.mark_step(run_id, sid, state="failed", failure_class=fc)
            store.set_run_status(run_id, "failed")
            yield self._emit(ev.note(
                "bad", f"第 {sid} 步失败 · {fc}(分诊来源:{triage.source})。"
                       f"处置:{disposition.get('note', '人工介入')}"))
            store.append_trace(run_id=run_id, step_no=sid, phase=cap.phase,
                               revision_trigger="failure", error_occurred=True,
                               error_type=fc, error_message=result.detail[:500])
            if consecutive_failures[fc] >= 3:
                yield self._emit(ev.note("bad", f"同类失败已连续 {consecutive_failures[fc]} 次,停链问人(§3.3)"))
            return

        # ---- 收尾前的最后一次 steer 消费点(2026-08-12 干预矩阵决策一) ----
        # 最后一步执行期间排队的 steer(KILL 除外 —— 泵内每 poll 即时消费)
        # 此前没有检查点可消费,会静默遗留到该 run 的下一次 execute 才生效,
        # 用户体感是「干预被吞了」。现在收尾前统一消费:能应用的应用(标脏/
        # 复位,由下方「待重跑」note 说明将在续跑时生效);KILL/PAUSE/PLAY
        # 已无作用对象,消费并 note 说明,不许静默遗留。
        async for event in self._consume_actions_idle(run_id, "steer"):
            yield event

        # ---- 收尾:重解析 → 账本 → 报告(try/finally 语义由 executor 层保证过程留痕) ----
        verify_results = verify_metrics(store, run_id, self.workspace)
        bad_metrics = [r for r in verify_results if not r["ok"]]
        if bad_metrics:
            yield self._emit(ev.note("warn", f"指标重解析未通过:{bad_metrics}"))
        prov_path = write_provenance(store, run_id, contract=self.contract,
                                     workspace=self.workspace)
        script_path = write_run_script(store, run_id, self.workspace)
        store.set_run_status(run_id, "done")

        # follow_up 干预此刻消费(absorb-E4;按 run 过滤,防跨 run 互吞)
        for action in store.due_actions("follow_up", run_id=run_id):
            outcome = apply_action(store, run_id, action, registry=self.registry,
                                   tool_versions=(self.probe().tool_versions()))
            store.consume_action(action["id"])
            for event in outcome.events:
                yield self._emit(event)

        # ---- 「done 但有待重跑」的明示(2026-08-12 干预矩阵决策三) ----
        # 执行中(或收尾前消费点)对已完成步骤 SET_PARAMS/RESET 后,本回合仍以
        # done 收尾、脏步骤留待续跑 —— 不引入新 run 状态(schema 不动),用
        # note 消除「done = 全部最新」的误读:result 前列出待重跑步骤,report
        # 后再补一条提示;provenance 照常导出(它反映本次执行的事实)。
        waiting = sorted(s.step_id for s in store.load_steps(run_id)
                         if s.state in ("pending", "stale"))
        waiting_text = "、".join(str(sid) for sid in waiting)
        if waiting:
            yield self._emit(ev.note(
                "warn", f"run 已完成,但第 {waiting_text} 步因干预待重跑:当前产物"
                        f"仍对应干预前的配置,再次执行将只重跑这些步骤"))
        yield self._emit(ev.result())
        yield self._emit(ev.note(
            "ok", f"provenance: {prov_path.name} · 等价命令: {script_path.name}"))
        yield self._emit(ev.report())
        if waiting:
            yield self._emit(ev.note(
                "warn", f"提示:第 {waiting_text} 步待重跑,续跑后结果才反映最新配置"))

    # ---------------- 干预消费 / 恢复 ----------------

    async def _consume_steer(self, run_id: str, current_step: int) -> AsyncIterator[dict]:
        for action in self.store.due_actions("steer", run_id=run_id):
            kind = action["action"]
            if kind == "KILL":
                self.store.consume_action(action["id"])
                self.request_cancel(run_id)
                yield self._emit(ev.intervention("取消运行(KILL)", mode="steer"))
                continue
            outcome = apply_action(self.store, run_id, action,
                                   registry=self.registry,
                                   tool_versions=self.probe().tool_versions())
            self.store.consume_action(action["id"])
            for event in outcome.events:
                self.store.append_trace(run_id=run_id, step_no=current_step,
                                        revision_trigger="intervention",
                                        observation=outcome.message)
                yield self._emit(event)

    async def _consume_actions_idle(self, run_id: str,
                                    deliver_as: str) -> AsyncIterator[dict]:
        """「已无在跑/待跑步骤」语境下的干预消费(2026-08-12 干预矩阵决策一/二)。

        两处调用:回合收尾前的最后一次 steer 消费点(决策一:末步执行期间排队
        的 steer 此前没有检查点可消费,会静默遗留到下一次 execute);done run
        重入的入口消费(决策二:不吞排队干预)。

        与 _consume_steer 的差别 —— 此刻没有「当前/下一步」可言:
        - KILL/PAUSE/PLAY 已无作用对象:消费 + note 说明,不落 control 位、
          不改 run 状态(否则遗留的 KILL 会误拦下一次 execute,PAUSE 会把
          已完成的 run 翻回 paused);
        - 状态类动作(SET_PARAMS/SET_METHOD/RESET/SKIP)照常应用:标脏/复位
          即刻入库,由收尾处的「待重跑」note 说明将在续跑时生效(决策三)。
        纪律:凡到期动作,本回合必消费、必留痕,不许静默遗留。
        """
        store = self.store
        for action in store.due_actions(deliver_as, run_id=run_id):
            kind = action["action"]
            if kind in ("KILL", "PAUSE", "PLAY"):
                store.consume_action(action["id"])
                yield self._emit(ev.note(
                    "warn", f"{kind} 干预到达时已无在跑/待跑步骤,无可作用对象,"
                            f"已消费(不遗留到下一回合)"))
                continue
            outcome = apply_action(store, run_id, action, registry=self.registry,
                                   tool_versions=self.probe().tool_versions())
            store.consume_action(action["id"])
            for event in outcome.events:
                store.append_trace(run_id=run_id, step_no=None,
                                   revision_trigger="intervention",
                                   observation=outcome.message)
                yield self._emit(event)

    async def resume(self, session_id: str) -> AsyncIterator[dict]:
        """服务重启后的恢复入口(§7.4 reattach 条目):接回 running 状态的 run。

        E3:control 位是持久化的取消意图 —— 重启前请求过取消的 run 不再
        reattach/启动新步骤,由 execute 的入口检查直接收尾为 interrupted。
        """
        for run in self.store.list_runs(session_id):
            if run["status"] != "running":
                continue
            run_id = run["run_id"]
            if run.get("control") != "cancel_requested":
                live = [s for s in self.store.load_steps(run_id)
                        if s.state == "running" and s.stage in ("PREPARED", "LAUNCHED", "RUNNING")]
                if live:
                    s = live[0]
                    yield self._emit(ev.reattach(
                        f"检测到第 {s.step_id} 步仍在运行(job: {s.job_dir},"
                        f"日志偏移 {s.log_offset}),已接回 —— 不重跑已完成阶段"))
            async for event in self.execute(session_id, run_id):
                yield event
