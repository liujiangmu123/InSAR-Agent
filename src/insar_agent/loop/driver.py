"""事件驱动主循环(AGENT-DESIGN §3.2)。

与竞品 for 循环的根本区别:LLM 调用只是事件处理的一种,不是循环的骨架。
长任务期间循环继续转:干预队列每秒消费(KILL 即时,其余当前步结束后生效,§4.5),
用户可随时继续对话(API 层并发调用 turn)。

驱动两类回合:
    turn(text)      对话回合:LLM 可用时先走 converse(自然语言聊天 + 闭集动作
                    映射到既有路径);无 LLM / LLM 失败走规则路径
                    (意图 → 探测 → 计划 → 候选决策点,行为与手动流水线一致)
    execute(run)    执行回合:逐步跑五阶段执行器,消费干预,失败分诊,收尾审计

所有事件同时:yield 给调用方(HTTP 流式响应)+ 发布到 EventBus(全局 SSE)
+ 关键事件落 trace(轨迹面板)。监听器错误被隔离(absorb-E8)。
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import AsyncIterator

from insar_agent.audit.contract import load_contract
from insar_agent.audit.verify import verify_metrics
from insar_agent.brain.facade import Brain, ConverseResult
from insar_agent.brain.provider import BrainUnavailable
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
from insar_agent.registry.scenarios import Scenario, scenario_of
from insar_agent.report.script import write_run_script
from insar_agent.runtime.backend_select import backend_for_job_dir, backend_for_step
from insar_agent.runtime.executor import ExecContext, execute_step
from insar_agent.runtime.jobs import JobBackend, LocalJobBackend
from insar_agent.runtime.probe import ProbeResult, probe_environment
from insar_agent.runtime.stream import CancelToken

log = logging.getLogger(__name__)


def _tail_text(path: Path, limit_kb: int = 64) -> str:
    """读日志尾部 N KB(口径与 /api/logs 一致:掐掉截断处的半行)。

    分诊只需要错误窗口(设计 §3.3 的 error_window 本就是 ±5 行),真实 ISCE2
    全链日志数百 MB,整读会瞬时吃满内存(REVIEW-r2 P2-12)。文件消失/不可读
    (作业目录被清理的竞态)按空日志处置,交由 triage 用 result.detail 兜底。
    """
    try:
        size = path.stat().st_size
        limit = limit_kb * 1024
        with path.open("rb") as f:
            if size > limit:
                f.seek(size - limit)
            data = f.read()
    except OSError:
        return ""
    text = data.decode("utf-8", errors="replace")
    if size > limit and "\n" in text:
        text = text.split("\n", 1)[1]
    return text


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
        # 在途执行任务的强引用(asyncio 只弱引用 task):回合生成器被提前关闭
        # (断连/关停)后 exec_task 仍要继续跑完当前步,不能被 GC 掐断(P2-2r2)
        self._exec_tasks: set[asyncio.Task] = set()
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
        token 是同进程快路径。已在途的作业照常结算,只禁止新效果。

        资源生命周期:只取消「已存在」的 token,不为不在执行中的 run 凭空创建
        —— 无执行回合时取消语义完全由持久化 control 位承载(execute 入口消费),
        凭空创建的 token 没有消费方,只会滞留 _tokens 字典。"""
        self.store.request_cancel(run_id)
        token = self._tokens.get(run_id)
        if token is not None:
            token.cancel()

    def _backend_for(self, run: dict, cap: Capability, method_id: str,
                     job_dir: str | None = None) -> JobBackend:
        if self.backend is not None:
            backend = self.backend  # 显式注入(测试/运维)永远最优先
        elif job_dir:
            # 接回已有作业目录的步骤按目录归属路由(REVIEW-r2 P2-5,admin 侧同款
            # backend_for_job_dir):引擎+探测路由在探测瞬断(wsl 服务重启)、
            # INSAR_WSL_DISTRO 改名、强制 INSAR_JOB_BACKEND=local 时会把 WSL 活
            # 作业交给 LocalJobBackend 判活 —— 无宿主可见 job.hb,两击即被判
            # orphaned,run 被标 interrupted 而 Linux 侧进程照跑。归属由路径形态
            # 决定(POSIX/UNC 前缀 → WSL),不受探测波动影响。
            backend = backend_for_job_dir(job_dir)
        else:
            m = cap.method(method_id)
            backend = backend_for_step(engine=m.engine if m else "-",
                                       simulated=bool(run.get("simulated")),
                                       override=None)
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

    # ---------------- 对话回合 ----------------

    async def turn(self, session_id: str, text: str) -> AsyncIterator[dict]:
        store = self.store
        session = store.get_session(session_id)
        if session is None:
            store.create_session(session_id, session_id)
            session = store.get_session(session_id)
        # converse 的滚动历史 = 本条消息之前的最近 8 条:必须在落库当前消息
        # 之前取,否则当前消息在 prompt 里出现两次(历史一次 + 用户消息一次)
        history = store.chat_history(session_id, limit=8) if self.brain.enabled else []
        store.append_chat(session_id, "user", text)

        # ---- 对话入口(LLM 可用时):converse 先行,动作映射到既有代码路径 ----
        # Brain(provider=None)/未配置不进此块,直接走下方规则路径 —— 「无 LLM
        # 时系统退化为手动流水线、行为逐字节不变」的铁律(守护测试锁死)不动。
        if self.brain.enabled:
            outcome: ConverseResult | None = None
            try:
                outcome = self.brain.converse(
                    text, history=history,
                    state_summary=self._converse_state(session_id),
                    registry=self.registry)
            except BrainUnavailable:
                # 诚实降级:LLM 失败不装哑,说明一句后走规则路径(与无 LLM 同轨)
                yield self._emit(ev.note("warn", "LLM 暂不可用,已退化为关键词模式"))
            if outcome is not None:
                if (outcome.action or {}).get("type") == "plan":
                    # plan 动作:reply 是规划前的过渡语;场景已过闭集校验,
                    # 之后与规则路径共用同一套规划流程(绝不绕过校验/状态机)
                    yield self._emit(ev.say([outcome.reply]))
                    store.append_chat(session_id, "agent", outcome.reply)
                    sc = scenario_of(outcome.action["scenario"])
                    assert sc is not None  # facade 闭集校验保证 key 在场景包内
                    if outcome.action.get("region") or outcome.action.get("timerange"):
                        # LLM 抽取的区域/时间只覆盖场景包的展示元数据,
                        # 不进指纹(Scenario.region/dates 本就是展示字段)
                        sc = dataclasses.replace(
                            sc, region=outcome.action.get("region", sc.region),
                            dates=outcome.action.get("timerange", sc.dates))
                    async for event in self._plan_turn(session_id, session, text, sc,
                                                       intent_source="converse"):
                        yield event
                else:
                    async for event in self._apply_converse(session_id, outcome):
                        yield event
                return

        # ---- 规则路径(无 LLM / LLM 失败):行为与历史版本逐字节一致 ----
        intent = self.brain.intent(text)
        if intent.need_form:
            # 场景选项 = 技能包闭集(动态生成):此前硬编码三场景,第四包
            # stripmap_coseismic 加入后表单脱节,用户无法从表单选到条带链
            # (触发场景:tests/test_journey_permafrost.py 边界段 ask 断言)
            from insar_agent.registry.scenarios import SCENARIOS

            yield self._emit(ev.ask(
                "无法从描述中识别场景,请补充:",
                [{"key": "scenario", "label": "场景", "options": [s.key for s in SCENARIOS]},
                 {"key": "region", "label": "区域"}, {"key": "dates", "label": "时间范围"}]))
            return
        sc = intent.scenario
        assert sc is not None
        async for event in self._plan_turn(session_id, session, text, sc,
                                           intent_source=intent.source):
            yield event

    # ---------------- 规划回合(turn 的规划主体,converse plan 动作与规则路径共用) ----------------

    async def _plan_turn(self, session_id: str, session: dict, text: str,
                         sc: Scenario, *, intent_source: str) -> AsyncIterator[dict]:
        store = self.store
        mode = session["mode"]
        yield self._emit(ev.thinking(
            "解析意图与约束",
            f"区域:{sc.region or '待定'}\n目标:{sc.chain} 时序形变\n时间范围:{sc.dates or '待定'}\n"
            f"场景:{sc.label}(来源:{intent_source})\n"
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

        # ---- next_run 干预收集(absorb-E4) ----
        # next_run 面向"下次规划",无未来 run 可绑定;入队时记录的是当时会话
        # 最近 run 的 id,据此排除其他会话的预约(无归属的旧行照旧可见)。
        # 此处只收集不消费:消费点后移到 make_plan 校验通过之后(REVIEW-r2
        # P2-8 —— 先消费后校验会让过时预约「被消费即蒸发」,既没生效也无法
        # 重排;计划失败时动作留在队列,下次规划自动重试)。
        overrides: dict[int, dict] = {}
        pending_next_run: list[tuple[dict, int]] = []
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
                pending_next_run.append((action, sid))

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
                             intent={"text": text, "source": intent_source},
                             overrides=overrides or None,
                             allow_simulated=self.allow_simulated,
                             agent_hash=self.agent_hash)
            run_id = plan.run_id
            if plan.problems:
                for p in plan.problems:
                    yield self._emit(ev.note("bad", p))
                if pending_next_run:
                    yield self._emit(ev.note(
                        "warn", f"预约的 {len(pending_next_run)} 项变更未消费,保留在"
                                f"队列:计划存在问题(见上),解决后重新规划时自动生效"))
                yield self._emit(ev.say([
                    "环境不满足执行条件(见上)。可安装引擎后重试,或使用模拟模式演示流程。"]))
                return
            # 校验通过、计划已落库 —— 此刻才消费预约(消费不可逆,见上方收集处注释)
            for action, sid in pending_next_run:
                store.consume_action(action["id"])
                yield self._emit(ev.intervention(
                    f"应用上次预约的变更:第 {sid} 步 {action['payload']}"))
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

    # ---------------- converse 动作映射(turn 的对话入口辅助) ----------------

    async def _apply_converse(self, session_id: str,
                              outcome: ConverseResult) -> AsyncIterator[dict]:
        """converse 动作 → 既有代码路径的映射(plan 除外,turn 直接接规划流程)。

        纪律:绝不绕过既有校验/状态机 —— execute 走 self.execute(运行锁/
        control 位/租约照常),set_* 走 pending_actions 队列(消费点的
        apply_change 再校验一次,双保险)。reply 一律走既有 say 事件形状
        (parts 列表),前端零改动即可渲染。
        """
        store = self.store
        kind = (outcome.action or {}).get("type")

        if kind is None:
            # 纯聊天(含动作越界被拦截):只落一条 agent 消息,不碰 run/plan 状态
            yield self._emit(ev.say([outcome.reply]))
            store.append_chat(session_id, "agent", outcome.reply)
            return

        action = outcome.action
        assert action is not None
        if kind == "execute":
            # 等价用户点「运行流水线」:无 run / 计划有问题 / 锁被占等由
            # execute 入口的既有检查发 note,这里不重复造判断
            yield self._emit(ev.say([outcome.reply]))
            store.append_chat(session_id, "agent", outcome.reply)
            async for event in self.execute(session_id):
                yield event
            return

        if kind == "status":
            status_text = self._run_status_text(session_id)
            yield self._emit(ev.say([outcome.reply, status_text]))
            store.append_chat(session_id, "agent", f"{outcome.reply}\n{status_text}")
            return

        if kind == "check_env":
            env_text = self._env_summary_text()
            yield self._emit(ev.say([outcome.reply, env_text]))
            store.append_chat(session_id, "agent", f"{outcome.reply}\n{env_text}")
            return

        # set_params / set_method:闭集校验已在 facade 完成,这里入队。
        # deliver_as=steer:在跑 run 步间生效;空闲 run 由下次 execute 的
        # 检查点/入口消费 —— 与前端「改参数」按钮同一条队列语义。
        run = store.latest_run(session_id)
        if run is None:
            yield self._emit(ev.say([outcome.reply]))
            store.append_chat(session_id, "agent", outcome.reply)
            yield self._emit(ev.note(
                "warn", "当前会话还没有 run,参数/方法修改无处归属;"
                        "先说一句任务需求(场景/区域/时间)完成规划"))
            return
        if kind == "set_params":
            act_name, payload = "SET_PARAMS", {"params": action["params"]}
        else:
            act_name, payload = "SET_METHOD", {"method": action["method"]}
        store.push_action(scope="step", target=str(action["step"]), action=act_name,
                          payload=payload, deliver_as="steer", run_id=run["run_id"])
        yield self._emit(ev.say([outcome.reply]))
        store.append_chat(session_id, "agent", outcome.reply)
        yield self._emit(ev.intervention(
            f"已排队:第 {action['step']} 步 {act_name} {payload}"
            f"(steer,下一检查点生效)", affected=[action["step"]]))

    def _env_summary_text(self) -> str:
        """环境探测一行摘要(check_env 动作回复与 converse 状态注入共用)。"""
        probe = self.probe()
        ok = [f"{e} {v}" for e, v in sorted(probe.engines.items()) if v]
        missing = [e for e, v in sorted(probe.engines.items()) if not v]
        return (f"引擎可用:{'、'.join(ok) if ok else '无(将以模拟模式演示)'};"
                f"缺失:{'、'.join(missing) if missing else '无'};"
                f"磁盘 {probe.disk_free_gb:.0f} GB 可用,CPU {probe.cpu_count} 核")

    def _run_status_text(self, session_id: str) -> str:
        """最近 run 状态 + 步骤矩阵一行摘要(status 动作回复与状态注入共用)。"""
        run = self.store.latest_run(session_id)
        if run is None:
            return "当前会话还没有 run;说一句任务需求(场景/区域/时间)即可开始规划。"
        groups: dict[str, list[int]] = {}
        for s in self.store.load_steps(run["run_id"]):
            groups.setdefault(s.state, []).append(s.step_id)
        matrix = ";".join(f"{state} {ids}" for state, ids in sorted(groups.items()))
        sim = "(模拟)" if run.get("simulated") else ""
        return (f"最近 run {run['run_id']}{sim}:场景 {run.get('scenario') or '-'},"
                f"状态 {run['status']};步骤:{matrix or '无'}")

    def _converse_state(self, session_id: str) -> str:
        """converse 的系统状态摘要(注入 user 消息;system prompt 保持静态)。

        四行:环境探测、最近 run 状态与步骤矩阵、可用场景闭集、数据源配置。
        全部只读、一行一项 —— 上下文预算纪律(§3.3 约束四)对会话职责同样成立。
        """
        from insar_agent.registry.scenarios import SCENARIOS

        src = os.environ.get("INSAR_HYP3_SOURCE", "")
        if not src:
            data_line = "未配置(INSAR_HYP3_SOURCE)"
        else:
            data_line = f"{src}({'在位' if Path(src).is_dir() else '路径不存在'})"
        scenarios = "、".join(f"{s.key}({s.label})" for s in SCENARIOS)
        return "\n".join([
            f"环境:{self._env_summary_text()}",
            f"运行:{self._run_status_text(session_id)}",
            f"可用场景:{scenarios}",
            f"数据源:{data_line}",
        ])

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
            self._tokens.pop(run_id, None)  # run 已终态:入口刚建的 token 一并释放
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
                       "可用 POST /api/queue 排队,空闲后自动执行;"
                       "若上一回合已崩溃,约 60 秒后重试即可接管。"))
            return
        try:
            async for event in self._execute_run(run_id, step_ids, token,
                                                 lease=lease, holder=holder):
                yield event
        except Exception as exc:
            # 意外异常的 run 状态 reconcile(REVIEW-r2 P2-11):未捕获异常打断
            # 回合时 run 若停在 running 会成僵尸 —— admin 视图误报活 run、turn
            # 复用分支照常复用、60s 内重试还被运行锁拒绝。收尸为 failed 并留痕
            # (异常原样上抛,api 层 ndjson 转结构化 note,细节进服务端日志)。
            # GeneratorExit/CancelledError 是 BaseException,不在此拦截:断连/
            # 关停不是失败,run 状态交给后台任务与下次 resume。
            if (store.get_run(run_id) or {}).get("status") == "running":
                store.set_run_status(run_id, "failed")
                store.append_trace(
                    run_id=run_id, phase="driver",
                    revision_trigger="unexpected_exception", error_occurred=True,
                    error_type=type(exc).__name__, error_message=str(exc)[:500])
            raise
        finally:
            # 异常序加固(REVIEW-r2 P2-1):release_lease 是一次 DB 写,busy/锁
            # 超时可抛 OperationalError —— 不隔离的话下面的 keepalive 释放被跳过
            # (wsl.exe sleep infinity 保活进程泄漏),且原始业务异常被顶替。
            # 释放失败只意味着租约行残留,超过 stale_after(60s)后自然可被接管。
            try:
                store.release_lease(lease, holder)
            except Exception:
                log.exception("release_lease 失败(租约行将随 stale 窗口过期,可被接管)")
            # run 收尾(含 done/failed/interrupted/paused 与异常/断流)统一释放
            # 本 run 的 WSL keepalive:暂停/中断后 VM 允许空闲回收,续跑的
            # launch 会重新 ensure_keepalive(§4.8)
            self._release_run_backends(run_id)
            # 终态后释放取消令牌(资源生命周期:_tokens 此前只增不减,长期服务
            # 随 run 数量无界增长)。放在拿到运行锁的回合收尾处:锁被并发回合
            # 占用的早退路径不释放 —— 那个 token 归执行中的回合所有。
            self._release_token_if_terminal(run_id)

    def _release_token_if_terminal(self, run_id: str) -> None:
        """run 到达终态(done/failed/interrupted)后释放同进程取消令牌。

        paused/running 不释放:并发 abort 仍需经 token 立即触达在跑回合。
        释放不丢取消语义 —— 跨回合/重启的取消意图由持久化 control 位兜底
        (request_cancel 落盘,execute 入口消费)。"""
        status = (self.store.get_run(run_id) or {}).get("status")
        if status in ("done", "failed", "interrupted"):
            self._tokens.pop(run_id, None)

    def _release_run_backends(self, run_id: str) -> None:
        for backend in self._run_backends.pop(run_id, []):
            try:
                backend.release_keepalive()
            except Exception:
                pass  # 释放失败不影响 run 收尾;残留保活进程随宿主进程退出而消亡

    def _reap_exec_task(self, task: asyncio.Task) -> None:
        """exec_task 收尾回调:释放强引用 + 取回异常。

        回合生成器在 yield 点被提前关闭(断连/关停)后无人再 await 该任务,
        不取回异常会积累「Task exception was never retrieved」告警噪音
        (REVIEW-r2 P2-2)。正常路径异常仍由回合泵的 exec_task.result() 消费,
        这里只兜底、不处置 —— 步骤终态已由执行器自身落库。"""
        self._exec_tasks.discard(task)
        if not task.cancelled():
            task.exception()

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
        # 待跑集合:显式指定,或 pending+stale+interrupted+orphaned+running
        # (failed 默认需人工 RESET;running 是服务重启后的接回目标 —— REVIEW P1:
        # 漏掉它会让活作业永远接不回,下游步骤反因缺输入 contract_broken,
        # 执行器的 claim/reattach 语义本就支持接回,不会重跑已完成阶段)
        explicit = step_ids is not None
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
        # 空集合防御(REVIEW-r2 P1-2):质量门拦停/全失败的 run,默认选集为空,
        # 若照走收尾会被标 done —— 拦停 run 重跑一次就"翻绿",伤质量门可信度。
        if not step_ids:
            failed = sorted(sid for sid, s in steps.items() if s.state == "failed")
            if failed:
                yield self._emit(ev.note(
                    "bad", f"没有可执行步骤:失败步骤 {failed} 需处置后重跑"
                           f"(失败卡「从断点继续」或 RESET);run 状态保持不变"))
                return
        store.set_run_status(run_id, "running")
        for sid in step_ids:  # 失效/中断步骤先复位(新 attempt);running 不复位,交执行器接回
            st = steps[sid].state
            # failed 只在显式列表中复位:失败卡「从断点继续」发的就是显式列表,
            # 不复位会让执行器在 try 块外裸抛(REVIEW-r2 P1-1);默认选集仍要求
            # 人工处置,不自动重试失败步骤
            if st in ("stale", "interrupted", "orphaned") or (explicit and st == "failed"):
                store.reset_step_for_rerun(run_id, sid)

        total = len(step_ids)
        done_count = 0
        consecutive_failures: dict[str, int] = {}
        last_renew = time.monotonic()
        lease_lost = False

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
            if step.state == "stale":
                # 步间干预复位(2026-08-13 quake 旅程深验发现,P1):UI 的改参重跑
                # 是「排队 SET_PARAMS(steer)+ 显式 step_ids」两连发,动作在本步
                # 检查点(上方 _consume_steer)才被消费 —— 入口复位 pass 用的是
                # 消费前的快照,此刻本步已 stale 但 stage 仍停在 VERIFIED,五阶段
                # 幂等守卫会把重跑空转成 no-op:步骤卡照发 tool.end/step.end,
                # 命令数却为 0,结果仍是旧配置。与入口 pass 同语义,补一次复位。
                store.reset_step_for_rerun(run_id, sid)
                step = store.load_step(run_id, sid)
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

            backend = self._backend_for(run, cap, step.method, job_dir=step.job_dir)
            exec_task = asyncio.create_task(
                execute_step(self._exec_ctx(backend, emit=pump), run_id, sid, token))
            # GeneratorExit 接缝(REVIEW-r2 P2-2):本生成器在下方 yield 点被提前
            # 关闭(服务关停取消泵任务/内层生成器 aclose/GC 兜底)后不再恢复;
            # exec_task 交由事件循环继续跑完当前步 —— 「run 归服务端所有,断连
            # 不取消」是 api 层 ndjson 的既有决策(app.py),这里与之对齐:
            # 不 cancel、不等待;强引用集合防任务被 GC 掐断,done-callback
            # 收割异常防 asyncio 告警。租约随 execute() 的 finally 释放,残余
            # 双驱窗口仅限当前步,由执行器阶段 CAS(StageConflict → 让位)仲裁。
            self._exec_tasks.add(exec_task)
            exec_task.add_done_callback(self._reap_exec_task)
            while not exec_task.done():
                await asyncio.sleep(min(self._poll, 0.2))
                while not detail.empty():
                    yield detail.get_nowait()
                if time.monotonic() - last_renew > 5.0:  # 运行锁心跳续租
                    # 续租失败 = 锁已被接管(本回合曾挂起超过 stale 窗)。输者自停
                    # (REVIEW-r2 P1-5):当前步走完就退出,不再启动新步骤;不碰
                    # token/control 位——在途作业的跟随与结算交给接管方,同一步骤
                    # 上的竞争由执行器阶段 CAS 仲裁(残余双驱窗口仅限当前步)。
                    if not store.acquire_lease(lease, holder, ttl=60.0, stale_after=60.0):
                        lease_lost = True
                    last_renew = time.monotonic()
                for action in store.due_actions("steer", run_id=run_id, include_unattributed=False):
                    if action["action"] == "KILL":
                        store.consume_action(action["id"])
                        self.request_cancel(run_id)  # E3:control 位同步落盘
                        yield self._emit(ev.intervention("取消当前步骤(KILL)", mode="steer"))
            while not detail.empty():  # 执行结束后冲刷余量,不丢尾部日志
                yield detail.get_nowait()
            result = exec_task.result()
            step = result.step
            if lease_lost:
                yield self._emit(ev.note(
                    "warn", "运行锁已被其他执行回合接管,本回合自停;"
                            "已完成步骤保留,后续步骤由接管方推进"))
                return

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

            # failed:分诊 → 处置建议(闭集,§4.12)。只读日志尾部:错误几乎
            # 总在末尾,triage 的 error_window 也只要 ±5 行(REVIEW-r2 P2-12)
            log_text = _tail_text(Path(step.log_path)) if step.log_path else ""
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
        for action in store.due_actions("follow_up", run_id=run_id, include_unattributed=False):
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
        for action in self.store.due_actions("steer", run_id=run_id, include_unattributed=False):
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
        for action in store.due_actions(deliver_as, run_id=run_id, include_unattributed=False):
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
