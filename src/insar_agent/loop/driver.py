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
import inspect
import json
import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import AsyncIterator

from insar_agent.audit.contract import load_contract
from insar_agent.audit.verify import verify_metrics
from insar_agent.brain.facade import Brain, ConverseResult
from insar_agent.brain.provider import BrainTruncated, BrainUnavailable
from insar_agent.brain.usage import usage_context
from insar_agent.core.actions import apply_action
from insar_agent.core.failures import DISPOSITIONS, FailureClass
from insar_agent.core.ledger import write_provenance
from insar_agent.core.store import Store
from insar_agent.engines import default_builder
from insar_agent.loop import events as ev
from insar_agent.loop.budget import clip_summary
from insar_agent.loop.events import EventBus
from insar_agent.loop.goal import (
    ADAPTIVE_CHUNK,
    cycle_kinds as _cycle_kinds,
    goal_is_env_only as _goal_is_env_only,
    goal_is_work as _goal_is_work,
    should_extend_budget,
)
from insar_agent.brain.llm_config import AGENT_MAX_CYCLES_MAX
from insar_agent.planner.plan import PlanResult, make_plan, pipeline_groups
from insar_agent.registry.capabilities import REGISTRY, topo_order
from insar_agent.registry.model import Capability
from insar_agent.registry.scenarios import Scenario, scenario_of
from insar_agent.report.script import write_run_script
from insar_agent.runtime.backend_select import backend_for_job_dir, backend_for_step
from insar_agent.runtime.executor import ExecContext, execute_step
from insar_agent.runtime.jobs import JobBackend, LocalJobBackend
from insar_agent.runtime.probe import ProbeResult, probe_environment
from insar_agent.runtime.stream import CancelToken
from insar_agent.skills.loader import SECTION_FAILURES, SECTION_PARAMS, skill_section_text

log = logging.getLogger(__name__)

#: 数据集类型的中文标签(键与 data/catalog.KINDS 闭集对齐)
_DATASET_KIND_LABELS = {"hyp3": "HyP3 产品", "alos_raw": "ALOS 原始条带",
                        "slc_stack": "SLC 栈", "dem": "DEM", "unknown": "未识别"}

#: 数据集清单缓存 TTL(秒):与 /api/datasets 的清单缓存同一口径
_DATASETS_TTL_S = 60.0

#: 自主循环动作闭集(LOOP-CONTRACT §1,与 prototype/js/agentloop.js ACTION_META 对齐)
LOOP_ACTIONS = ("search_data", "inspect_file", "check_env", "list_data", "status",
                "plan", "execute", "set_params", "set_method", "thinking",
                "install_engine", "list_files",
                "learn_tool", "search_docs", "probe_scratch")

#: 循环动作的中文标签(工具卡 label,文案与 agentloop.js ACTION_META.zh 一致)
_LOOP_ACTION_LABELS = {
    "search_data": "搜索数据", "inspect_file": "查看文件", "check_env": "检查环境",
    "list_data": "列出数据", "status": "查询状态", "plan": "制定计划",
    "execute": "执行步骤", "set_params": "调整参数", "set_method": "切换方法",
    "thinking": "思考中", "install_engine": "安装引擎", "list_files": "列举项目文件",
    "learn_tool": "学习工具", "search_docs": "检索文档", "probe_scratch": "受控探针",
}

#: 重复提案熔断阈值:同一动作签名连续第 3 次提案 → 记账后收束,不执行第 3 次
_LOOP_REPEAT_BREAKER = 3

#: 失败未换策略熔断阈值:同签名动作连续失败 2 次 → unresolved_failure 收束
_LOOP_FAILURE_BREAKER = 2

#: WKT 几何前缀:ASF 的 intersectsWith 只认 WKT,region 非此形状就不传空间参数
_WKT_RE = re.compile(
    r"^\s*(?:POINT|LINESTRING|POLYGON|MULTIPOINT|MULTILINESTRING|MULTIPOLYGON"
    r"|GEOMETRYCOLLECTION)\s*\(", re.IGNORECASE)

#: ISO 日期前缀(YYYY[-MM[-DD]]):timerange 两端只认它,认不出就不传时间参数
_DATE_RE = re.compile(r"^\d{4}(?:-\d{1,2}){0,2}$")

#: _converse_stream 的队列哨兵:LLM 线程任务终态后经 add_done_callback 投递,
#: 消费侧读到即知任务已结束(成功/失败都必达,消费循环绝不悬停在 q.get)
_CONVERSE_DONE: object = object()


def _supports_on_delta(converse) -> bool:
    """facade.converse 是否声明了 on_delta 形参(0814B W1 并行开发的防御闸门)。

    只认显式命名形参:**kwargs 形态的替身(测试桩/旧实现)即使收下 on_delta
    也不会外发,按不支持处置最诚实;签名探不出(C 实现/Mock)同样回 False ——
    误传关键字给旧签名会直接 TypeError 炸回合,退回无流式调用永远安全。
    W1 落地带 on_delta 的签名后此闸门自动放行,driver 无需再改。
    """
    try:
        return "on_delta" in inspect.signature(converse).parameters
    except (TypeError, ValueError):
        return False


def _asf_filters(region: str | None, timerange: str | None
                 ) -> tuple[str | None, str | None, str | None, list[str]]:
    """把 facade 归一化的 search_data 过滤字段翻译为 asf_search 参数。

    facade(_validate_cycle_action)只透传非空字符串:region 是 WKT 或地名,
    timerange 形如 "2019-06-01/2019-08-31"(容忍 ~ 分隔,单端也认)。
    返回 (intersects_wkt, start, end, notes):翻译不出的条件不上送(ASF 收到
    坏参数会整次报错),但必须进 notes 如实注记 —— 过滤条件绝不静默蒸发。
    """
    notes: list[str] = []
    wkt = region if region and _WKT_RE.match(region) else None
    if region and wkt is None:
        notes.append(f"区域「{clip_summary(region, max_chars=40)}」非 WKT,"
                     f"ASF 未按空间过滤")
    start = end = None
    if timerange:
        halves = [p.strip() for p in re.split(r"\s*[/~]\s*", timerange, maxsplit=1)]
        halves += [""] * (2 - len(halves))  # 单端形态("2019-06")按只给 start 处理
        start = halves[0] if _DATE_RE.match(halves[0]) else None
        end = halves[1] if _DATE_RE.match(halves[1]) else None
        if start is None and end is None:
            notes.append(f"时间范围「{clip_summary(timerange, max_chars=40)}」"
                         f"无法解析,ASF 未按时间过滤")
    return wkt, start, end, notes


def _llm_error_text(exc: BaseException) -> str:
    """LLM 调用失败原文。不包「暂不可用/已收束/关键词模式」。"""
    msg = str(exc).strip()
    return msg or type(exc).__name__


def _is_resume_text(text: str) -> bool:
    t = (text or "").strip()
    return t in {"继续", "接着", "接着做", "往下", "继续做", "resume", "continue"} or t.startswith("继续")


def _human_size(num_bytes: float) -> str:
    """字节数 → 人类可读(数据集摘要用,GB/MB/KB 粗粒度足够)。"""
    for unit, factor in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if num_bytes >= factor:
            value = num_bytes / factor
            return f"{value:.1f} {unit}" if unit == "GB" else f"{value:.0f} {unit}"
    return f"{num_bytes:.0f} B"


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


def _run_intent(run: dict) -> dict:
    raw = run.get("intent") or "{}"
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}


def _run_groups(run: dict) -> tuple[str, ...]:
    groups = _run_intent(run).get("groups") or ["core"]
    return tuple(groups)


def _overrides_from_params(params: dict | None) -> dict[int, dict]:
    """turn 请求的 params → make_plan overrides。

    数字键视为逐步覆写;否则整份当作第 20 步(分析输入)参数。
    """
    if not params:
        return {}
    if all(str(k).isdigit() for k in params):
        out: dict[int, dict] = {}
        for k, v in params.items():
            if isinstance(v, dict) and ("params" in v or "method" in v):
                out[int(k)] = v
            elif isinstance(v, dict):
                out[int(k)] = {"params": v}
        return out
    return {20: {"params": params}}


class Driver:
    def __init__(self, store: Store, *, workspace: Path,
                 registry: dict[int, Capability] | None = None,
                 brain: Brain | None = None, backend=None,
                 allow_simulated: bool = True,
                 probe: ProbeResult | None = None,
                 poll: float = 0.5, startup_grace: float = 30.0,
                 idle_timeout_override: float | None = None,
                 total_timeout_override: float | None = None,
                 project_root: Path | None = None,
                 preflight_env: bool = True,
                 allow_auto_install: bool = True):
        self.store = store
        self.workspace = Path(workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.project_root = Path(project_root).resolve() if project_root else None
        self.preflight_env = preflight_env
        self.allow_auto_install = allow_auto_install
        self._last_slots: list = []
        self._forced_continues = 0
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
        # 无 run 会话的会话级取消意图(/api/abort 无 run 情形):converse 回合
        # 不产生 run,没有持久化 control 位可落 —— 同进程一次性 token,
        # converse_loop 在周期边界消费(_loop_cancel_requested)
        self._session_cancels: set[str] = set()
        # 在途执行任务的强引用(asyncio 只弱引用 task):回合生成器被提前关闭
        # (断连/关停)后 exec_task 仍要继续跑完当前步,不能被 GC 掐断(P2-2r2)
        self._exec_tasks: set[asyncio.Task] = set()
        # 在途 LLM 线程任务的强引用(_exec_tasks 同款,0814B §1.3):流式回合
        # 生成器在 delta 中途被关闭(客户端断连)后,brain.converse 线程仍要
        # 跑完收尾(同步 HTTP 调用本就无法中断),不能被 GC 掐断
        self._llm_tasks: set[asyncio.Task] = set()
        # 本 run 用过的自带保活的后端实例(WslJobBackend),run 收尾统一释放
        # keepalive(WSL P2:此前 sleep infinity 随 run 数量堆积泄漏)
        self._run_backends: dict[str, list[JobBackend]] = {}
        self._poll = poll
        self._startup_grace = startup_grace
        self._idle_override = idle_timeout_override
        self._total_override = total_timeout_override
        # 数据集清单缓存 (扫描时刻, 条目列表):converse 每回合都注入一行摘要,
        # TTL 与 /api/datasets 同款(60s),挡住高频对话的重复扫描
        self._datasets_cache: tuple[float, list[dict]] | None = None
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

    def request_session_cancel(self, session_id: str) -> None:
        """无 run 会话的取消入口(/api/abort 在 run_id 缺省且会话无 run 时调用)。

        converse 回合不产生 run,取消意图没有 control 位可落盘 —— 置同进程
        会话级一次性 token,converse_loop 在周期边界消费(消费即清除,不让
        遗留意图误拦下一个回合)。有 run 的会话仍走 request_cancel(run_id),
        语义不变(control 位落盘 + run 级 token)。"""
        self._session_cancels.add(session_id)

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

    async def turn(self, session_id: str, text: str, *,
                   pipeline: str = "core",
                   step_params: dict | None = None) -> AsyncIterator[dict]:
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
            # 会话自动命名触发条件(二期):会话名还是默认值(= 会话 id)且本条
            # 恰是首条用户消息。短路序:名字判断零成本,先挡掉绝大多数回合。
            # 无 LLM 时天然不改名(本块整体不进)。
            want_title = (session["name"] == session_id and sum(
                1 for m in store.chat_history(session_id)
                if m.get("role") == "user") == 1)
            state_summary = self._converse_state(session_id)
            if want_title:
                # 与 system prompt 的 session_title 契约呼应:提示只在首条消息出现
                state_summary += ("\n会话命名:本条是会话首条消息,请在输出 JSON 里"
                                  "附 session_title(不超过 12 字的中文标题,"
                                  "概括用户想做的事)")
            outcome: ConverseResult | None = None
            stream_out: dict = {}
            try:
                # converse 经线程桥调用(0814B §1.3):say.delta / say.abort 帧
                # 裸 yield 只进回合 NDJSON,不经 _emit(不上总线/trace,例外
                # 条款见 docs/AGENT-LOOP.md §4.1);结果写 stream_out,失败
                # 语义与旧同步调用一致,之后的分支零改动
                async for frame in self._converse_stream(
                        text, history=history,
                        state_summary=state_summary, out=stream_out):
                    yield frame
                outcome = stream_out.get("outcome")
            except BrainUnavailable as exc:
                # 已配置模型但调用失败:原样展示错误,不退关键词/规则路径。
                # 无 LLM(brain.enabled=False)才走下方规则路径。
                err = _llm_error_text(exc)
                yield self._emit(ev.note("bad", err))
                store.append_chat(session_id, "agent", err)
                return
            if outcome is not None:
                if want_title and outcome.session_title:
                    # 只信触发条件不信 LLM 时机:非首条消息带回的标题一律忽略
                    # (want_title=False 不进此支);标题已在 facade 消毒截断
                    if store.rename_session(session_id, outcome.session_title):
                        yield self._emit(ev.note(
                            "ok", f"已把本会话命名为「{outcome.session_title}」"
                                  f"(可在会话列表改名)"))
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
                                                       intent_source="converse",
                                                       pipeline=pipeline,
                                                       step_params=step_params):
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
                                           intent_source=intent.source,
                                           pipeline=pipeline, step_params=step_params):
            yield event

    # ---------------- converse 线程桥(turn 的流式辅助,0814B §1.3) ----------------

    def _reap_llm_task(self, task: asyncio.Task) -> None:
        """LLM 线程任务收尾回调:释放强引用 + 兜底取回异常(_reap_exec_task 同款)。

        回合生成器在 delta 中途被提前关闭(断连/关停)后无人再消费
        task.result(),不取回异常会积累「Task exception was never retrieved」
        告警噪音。正常路径的异常仍由 _converse_stream 的 task.result() 上抛,
        这里只兜底、不处置。"""
        self._llm_tasks.discard(task)
        if not task.cancelled():
            task.exception()

    async def _converse_stream(self, text: str, *, history: list[dict],
                               state_summary: str, out: dict) -> AsyncIterator[dict]:
        """converse 的线程桥:同步 LLM 调用进线程,delta 增量经队列回事件循环
        (异步化顺带解决旧隐患 —— 此前同步调用会把事件循环阻塞至多 60s)。

        产出帧闭集:say.delta × N;失败且已外发过时补一帧 say.abort 收尾
        (reason:truncated=token 上限截断 / unavailable=供应商失败)。这些帧
        由调用方(turn)裸 yield 只进回合 NDJSON,不经 _emit(例外条款见
        docs/AGENT-LOOP.md §4.1)。成功结果写 out["outcome"](异步生成器无
        返回值,_loop_action_events 的 out 写回同款);失败原样上抛 ——
        turn() 的 BrainUnavailable 分支语义与旧同步调用逐字节一致。

        facade.converse 的 on_delta 形参由 W1 并行开发:签名未声明时退回
        无流式调用(仍进线程,零 delta),集成后自动点亮流式。
        """
        converse = self.brain.converse
        kwargs = dict(history=history, state_summary=state_summary,
                      registry=self.registry)
        if not _supports_on_delta(converse):
            out["outcome"] = await asyncio.to_thread(converse, text, **kwargs)
            return

        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()

        def push(chunk: str) -> None:
            # provider 线程 → 事件循环的唯一通道;循环已关闭(服务关停竞态)
            # 时静默丢弃 —— 已无消费者,增量无处可去(终稿语义不受影响)
            try:
                loop.call_soon_threadsafe(q.put_nowait, chunk)
            except RuntimeError:
                pass

        task = asyncio.create_task(
            asyncio.to_thread(converse, text, on_delta=push, **kwargs))
        # 强引用防 GC(asyncio 只弱引用 task):回合生成器在 yield 点被提前
        # 关闭(客户端断连)后线程任务仍要跑完收尾。哨兵经 add_done_callback
        # 投递(成功/失败都必达,消费循环绝不悬停);回调按登记序执行:
        # 先收割(释放引用 + 兜底取回异常),再投哨兵唤醒消费侧。
        self._llm_tasks.add(task)
        task.add_done_callback(self._reap_llm_task)
        task.add_done_callback(lambda _t: q.put_nowait(_CONVERSE_DONE))

        emitted = False   # 是否外发过 delta:决定失败时要不要补 say.abort
        finished = False
        while not finished:
            chunk = await q.get()
            if chunk is _CONVERSE_DONE:
                break
            parts = [chunk]
            while not q.empty():  # 排空合帧:消费慢时积压的增量合成一帧外发
                nxt = q.get_nowait()
                if nxt is _CONVERSE_DONE:
                    finished = True
                    break
                parts.append(nxt)
            merged = "".join(parts)
            if merged:
                emitted = True
                yield ev.say_delta(merged)
        try:
            out["outcome"] = task.result()
        except BrainTruncated:
            if emitted:  # 半截回复标废;零外发则无废可标,与改造前序列一致
                yield ev.say_abort("truncated")
            raise
        except BrainUnavailable:
            if emitted:
                yield ev.say_abort("unavailable")
            raise

    async def _cycle_stream(self, *, goal: str, cycles_summary: list[str],
                            state_summary: str, route_pin: int | None,
                            out: dict) -> AsyncIterator[dict]:
        """cycle 的线程桥:say 字段增量 → think.delta(等待可见思考)。

        假 Brain/无 on_delta 形参时整段调用、零增量,既有循环测试零感知。
        失败语义与 _converse_stream 同款:已外发则补 think.end,再上抛。
        """
        cycle = self.brain.cycle
        kwargs = dict(goal=goal, cycles_summary=cycles_summary,
                      state_summary=state_summary, registry=self.registry,
                      route_pin=route_pin)
        if not _supports_on_delta(cycle):
            out["result"] = await asyncio.to_thread(cycle, **kwargs)
            return

        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()

        def push(chunk: str) -> None:
            try:
                loop.call_soon_threadsafe(q.put_nowait, chunk)
            except RuntimeError:
                pass

        task = asyncio.create_task(asyncio.to_thread(cycle, on_delta=push, **kwargs))
        self._llm_tasks.add(task)
        task.add_done_callback(self._reap_llm_task)
        task.add_done_callback(lambda _t: q.put_nowait(_CONVERSE_DONE))

        emitted = False
        finished = False
        while not finished:
            chunk = await q.get()
            if chunk is _CONVERSE_DONE:
                break
            parts = [chunk]
            while not q.empty():
                nxt = q.get_nowait()
                if nxt is _CONVERSE_DONE:
                    finished = True
                    break
                parts.append(nxt)
            merged = "".join(parts)
            if merged:
                emitted = True
                yield ev.think_delta(merged)
        try:
            out["result"] = task.result()
            out["streamed"] = emitted
        except BrainTruncated:
            if emitted:
                yield ev.think_end()
            raise
        except BrainUnavailable:
            if emitted:
                yield ev.think_end()
            raise

    # ---------------- 规划回合(turn 的规划主体,converse plan 动作与规则路径共用) ----------------

    async def _plan_turn(self, session_id: str, session: dict, text: str,
                         sc: Scenario, *, intent_source: str,
                         pipeline: str = "core",
                         step_params: dict | None = None) -> AsyncIterator[dict]:
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

        # pipeline → groups 只在这一处转换(API 只转发 pipeline 字符串)
        groups = pipeline_groups(pipeline)
        for sid, ov in _overrides_from_params(step_params).items():
            overrides.setdefault(sid, {})
            if "method" in ov:
                overrides[sid]["method"] = ov["method"]
            if "params" in ov:
                overrides[sid].setdefault("params", {}).update(ov["params"])

        # ---- 计划 ----
        # next_run 预约的变更(overrides)只能经 make_plan 进入新计划;复用既有
        # run 的分支不会应用它们 —— 有 overrides 时必须重新规划,否则动作已被
        # 消费却静默丢失(REVIEW P1:next_run 干预失效)。
        latest = store.latest_run(session_id)
        plan: PlanResult | None = None
        if (latest and not overrides and latest["scenario"] == sc.key
                and _run_groups(latest) == groups
                and latest["status"] in (
                "ready", "planning", "paused", "interrupted", "running", "done", "failed")):
            run_id = latest["run_id"]
        else:
            plan = make_plan(store, session_id, registry=self.registry, probe=probe,
                             scenario=sc, workspace=str(self.workspace),
                             intent={"text": text, "source": intent_source,
                                     "pipeline": pipeline},
                             overrides=overrides or None,
                             allow_simulated=self.allow_simulated,
                             agent_hash=self.agent_hash,
                             groups=groups)
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
            pick = self.brain.select(
                cap, feas, env_facts=env_facts,
                # 步骤技能《参数启发式》按场景匹配注入(无技能=空串,select 行为不变)
                skill_hints=skill_section_text(decision_step, SECTION_PARAMS, scene=sc.key),
                prefer=sc.step_overrides.get(decision_step, {}).get("method"))
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

        if kind == "list_data":
            # 与 status/check_env 同款:reply 是过渡语,真实清单由系统扫描附上
            # (LLM 绝不编造数据集;数据口径 = data/catalog 只读元数据识别)
            data_text = self._datasets_text()
            yield self._emit(ev.say([outcome.reply, data_text]))
            store.append_chat(session_id, "agent", f"{outcome.reply}\n{data_text}")
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

    def _wsl_for_inventory(self, force: bool = False) -> dict | None:
        """生产路径走 WSL 缓存探测(冷缓存会真探一次);测试关预检时只窥视。"""
        if not (self.preflight_env or self.allow_auto_install):
            return self._peek_wsl_probe()
        try:
            from insar_agent.runtime.wsl_probe import probe_wsl_engines_cached
            hit = probe_wsl_engines_cached(timeout=25.0, force=force)
            if isinstance(hit, dict) and hit.get("ok"):
                return hit
        except Exception:  # noqa: BLE001 —— 探测失败按未预热,不炸回合
            log.debug("WSL 引擎探测失败,回退缓存窥视", exc_info=True)
        return self._peek_wsl_probe()

    @staticmethod
    def _peek_wsl_probe() -> dict | None:
        """只窥视 WSL 引擎探测的模块级 TTL 缓存,绝不触发探测(同 doctor._peek_wsl_cache)。

        真探测约 20s(VM 启动 + conda 冷启动),绝不落在对话回合;缓存由
        /api/env、/api/setup/status(setup_router 的 probe_wsl_engines_cached)
        预热,TTL 语义与其完全一致。冷缓存返回 None,调用方标注「未预热」。
        """
        from insar_agent.runtime import wsl_probe
        from insar_agent.runtime.backend_select import wsl_distro

        hit = wsl_probe._PROBE_CACHE.get(wsl_distro())
        if hit and time.monotonic() - hit[0] < wsl_probe._PROBE_CACHE_TTL:
            return hit[1]
        return None

    def _env_summary_text(self) -> str:
        """环境探测一行摘要(check_env 动作回复与 converse 状态注入共用)。

        口径对齐 setup_router(2026-08-13 浏览器实测缺口):本机探测(check_wsl=False)
        会把 WSL 里实际可用的 isce2/snaphu 报成缺失 —— 合并 WSL 引擎缓存
        (merge_wsl_probe 同款,本机优先、WSL 兜底),并标注每个引擎的来源。
        合并落在探测副本上:共享的 self._probe 不动,规则路径(_plan_turn 的
        引擎清单、feasibility 收窄)行为逐字节不变。
        """
        from insar_agent.runtime.wsl_probe import merge_wsl_probe

        probe = self.probe()
        merged = dataclasses.replace(probe, engines=dict(probe.engines),
                                     wsl=dict(probe.wsl))
        cached = self._peek_wsl_probe()
        if cached is not None:
            merge_wsl_probe(merged, cached)
        from insar_agent.runtime.env_inventory import AVAILABLE, classify

        self._last_slots = classify(merged)
        ok, missing = [], []
        for slot in self._last_slots:
            if slot.status == AVAILABLE:
                loc = "WSL" if slot.where == "wsl" else "本机"
                ver = (slot.version or "").replace(" (wsl)", "").strip()
                ok.append(f"{slot.name} {ver}({loc})")
            else:
                missing.append(slot.name)
        note = ("" if cached is not None else
                ";注:WSL 引擎探测未预热,缺失清单未含 WSL 侧"
                "(打开环境面板或稍后再问可获得完整口径)")
        return (f"引擎可用:{'、'.join(ok) if ok else '无(将以模拟模式演示)'};"
                f"缺失:{'、'.join(missing) if missing else '无'};"
                f"磁盘 {probe.disk_free_gb:.0f} GB 可用,CPU {probe.cpu_count} 核{note}")

    def _scan_datasets(self) -> list[dict]:
        """本地数据集清单(60s TTL 缓存),扫描根口径与 /api/datasets 一致:
        INSAR_DATA_DIR 环境变量 + <home>/datasets + <home>/datasets_roots.json
        自定义根。home 按 create_app 同一规则解析(INSAR_HOME,缺省 ./workspace)
        —— driver.workspace 是会话工作区(home/sessions/<id>),不是 home 本身。
        识别器是 data/catalog 的只读元数据扫描(有界遍历,绝不读文件内容),秒级。
        """
        now = time.monotonic()
        if self._datasets_cache and now - self._datasets_cache[0] < _DATASETS_TTL_S:
            return self._datasets_cache[1]
        from insar_agent.data.catalog import dataset_id, scan_roots

        home = Path(os.environ.get("INSAR_HOME", "workspace")).resolve()
        candidates: list[Path] = []
        env_dir = (os.environ.get("INSAR_DATA_DIR") or "").strip()
        if env_dir:
            candidates.append(Path(env_dir))
        candidates.append(home / "datasets")
        if self.project_root is not None:
            candidates.append(self.project_root)
            candidates.append(self.project_root / "data")
        try:
            raw = json.loads((home / "datasets_roots.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = []
        if isinstance(raw, list):
            candidates.extend(Path(x) for x in raw if isinstance(x, str) and x)
        roots: list[Path] = []
        seen: set[str] = set()
        for p in candidates:
            try:
                if not p.is_dir():
                    continue
            except OSError:
                continue
            key = dataset_id(p)  # 与 /api/datasets 同一套路径归一去重
            if key not in seen:
                seen.add(key)
                roots.append(p)
        datasets = scan_roots(roots)
        self._datasets_cache = (now, datasets)
        return datasets

    def _datasets_summary_line(self) -> str:
        """数据集一行摘要(converse 系统状态注入用):几个、什么类型。"""
        datasets = self._scan_datasets()
        if not datasets:
            return "未发现本地数据集"
        counts: dict[str, int] = {}
        for d in datasets:
            counts[d["kind"]] = counts.get(d["kind"], 0) + 1
        kinds = "、".join(
            f"{_DATASET_KIND_LABELS.get(k, k)} {n} 个"
            for k, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
        return f"共 {len(datasets)} 个({kinds})"

    def _datasets_text(self) -> str:
        """数据集清单详情(list_data 动作回复用):类型/大小/日期范围,最多 8 条。"""
        datasets = self._scan_datasets()
        if not datasets:
            return ("本地未发现数据集:可设置 INSAR_DATA_DIR 指向数据目录,"
                    "或在文件面板「数据集」区添加扫描根后再问一次。")
        lines = []
        for d in datasets[:8]:
            rng = d.get("date_range") or {}
            span = f",{rng['start']}~{rng['end']}" if rng else ""
            lines.append(f"- {d['name']}:{_DATASET_KIND_LABELS.get(d['kind'], d['kind'])},"
                         f"{_human_size(d['size_bytes'])}{span}")
        head = (f"本地数据集共 {len(datasets)} 个"
                + ("(仅列前 8 个)" if len(datasets) > 8 else "") + ":")
        return head + "\n" + "\n".join(lines)

    def _project_files_text(self) -> tuple[str, bool]:
        """列举项目文件夹(有界)。未绑项目则诚实说明。"""
        if self.project_root is None or not self.project_root.is_dir():
            return ("未绑定项目文件夹:请先「新建项目」选一个目录,把数据放进去", False)
        from insar_agent.project.paths import list_tree

        items = list_tree(self.project_root)
        if not items:
            return (f"项目目录 {self.project_root} 是空的:把数据放到该文件夹或 data/ 子目录",
                    True)
        lines = []
        for it in items[:16]:
            mark = "📁" if it["kind"] == "dir" else "📄"
            extra = f" {_human_size(it['size'])}" if it.get("size") else ""
            lines.append(f"- {mark} {it['rel']}{extra}")
        more = f"(共 {len(items)} 项,仅列前 16)" if len(items) > 16 else f"共 {len(items)} 项"
        return f"项目 {self.project_root.name} {more}:\n" + "\n".join(lines), True

    async def _loop_install_engine(self, action: dict) -> tuple[str, bool]:
        from insar_agent.runtime.install_runner import run_install

        engine = str(action.get("engine") or "")
        result = await asyncio.to_thread(run_install, engine)
        return str(result.get("summary") or ""), bool(result.get("ok"))

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

    def _memory_snippets(self, session_id: str) -> list[str]:
        """用户记忆片段(brain/memory.py 由并行代理实现,经契约解耦):

        契约:get_context_snippets(store, session_id, limit=5) -> list[str],
        返回该用户的记忆条目(如「常用区域:玉树」「偏好 SBAS」)。
        契约防御:模块不存在 / 接口缺失 / 调用抛错 / 返回形状不对 —— 一律回
        空列表,零影响对话(import 失败绝不炸 converse)。
        """
        import importlib

        try:
            memory = importlib.import_module("insar_agent.brain.memory")
            snippets = memory.get_context_snippets(self.store, session_id, limit=5)
        except Exception:  # noqa: BLE001 —— 可选依赖,任何失败都按「无记忆」处置
            return []
        if not isinstance(snippets, list):
            return []
        return [s.strip()[:200] for s in snippets[:5]
                if isinstance(s, str) and s.strip()]

    def _converse_state(self, session_id: str) -> str:
        """converse 的系统状态摘要(注入 user 消息;system prompt 保持静态)。

        五行:环境探测(本机+WSL 合并口径)、最近 run 状态与步骤矩阵、可用场景
        闭集、数据源配置、数据集一行摘要;有用户记忆时追加一行。全部只读、
        一行一项 —— 上下文预算纪律(§3.3 约束四)对会话职责同样成立。
        """
        from insar_agent.registry.scenarios import SCENARIOS

        src = os.environ.get("INSAR_HYP3_SOURCE", "")
        if not src:
            data_line = "未配置(INSAR_HYP3_SOURCE)"
        else:
            data_line = f"{src}({'在位' if Path(src).is_dir() else '路径不存在'})"
        scenarios = "、".join(f"{s.key}({s.label})" for s in SCENARIOS)
        proj = (f"项目目录:{self.project_root}" if self.project_root
                else "项目目录:未绑定(请先新建项目文件夹)")
        lines = [
            f"环境:{self._env_summary_text()}",
            f"运行:{self._run_status_text(session_id)}",
            f"可用场景:{scenarios}",
            f"数据源:{data_line}",
            f"数据集:{self._datasets_summary_line()}",
            proj,
        ]
        snippets = self._memory_snippets(session_id)
        if snippets:
            lines.append("用户记忆:" + ";".join(snippets))
        return "\n".join(lines)

    # ---------------- 自主循环回合(LOOP-CONTRACT §4) ----------------

    async def converse_loop(self, session_id: str, text: str, *,
                            max_cycles: int = 6) -> AsyncIterator[dict]:
        """自主循环回合:一个回合内自主跑多个「决策 → 动作」周期。

        「单周期单决策」纪律不变:LLM 每周期仍只输出一个闭集动作或 say 收束,
        周期间由本方法(确定性驱动器)衔接 —— 动作结果压缩为 ≤300 字摘要回灌
        下一周期(原始日志绝不进 LLM)。终止条件闭集:say(正常终止)|
        max_cycles 耗尽 | 取消 | BrainUnavailable | 同签名连续 3 次提案熔断 |
        同签名连续 2 次失败(unresolved_failure)。除 say 外一律 note 收尾。

        降级闸门(零回归铁律):brain 不可用或缺 cycle 职责(B3 未就绪/被拔除)
        时逐事件转发既有 turn(),该路径行为与单步版本逐字节一致。

        签名注记:契约 §4 的 converse_loop(text) 是省写 —— 与 turn 同形,
        首参是 session_id(Driver 不持有会话标识,由调用方注入,turn 同款)。
        """
        brain = self.brain
        if not getattr(brain, "enabled", False) or not callable(getattr(brain, "cycle", None)):
            async for event in self.turn(session_id, text):
                yield event
            return

        store = self.store
        if store.get_session(session_id) is None:
            store.create_session(session_id, session_id)
        store.append_chat(session_id, "user", text)
        max_cycles = max(1, int(max_cycles))
        budget = max_cycles
        hard_cap = max(budget, AGENT_MAX_CYCLES_MAX)
        # 陈旧取消意图的入口清零(P2-8):会话级 token 只服务「取消在途回合」,
        # 上一回合收尾后才置位的 /api/abort 若滞留到现在,会在第 1 周期边界
        # 误杀本回合 —— 新回合入口即作废。run 级 control 位不动:它有排队
        # 语义(归 run_queue 的未来 execute 消费者,见 _loop_cancel_requested)。
        self._session_cancels.discard(session_id)

        goal = text
        cycles_summary: list[str] = []  # 逐周期结构化摘要(唯一回灌 LLM 的过程记忆)
        route_pin: int | None = None    # 首周期后钉死路由(周期间不换供应商)
        prev_sig: str | None = None     # 上一周期动作签名(重复提案熔断)
        repeat = 0
        failed_sig: str | None = None   # 最近一次失败的动作签名(未换策略熔断)
        failed_streak = 0
        closed = False                  # 已显式收束(区分 max_cycles 自然耗尽)
        clean_stop = False
        self._forced_continues = 0
        op_id = uuid.uuid4().hex[:12]
        prev = store.load_loop_op(session_id)
        if prev and prev.get("phase") == "running" and _is_resume_text(text):
            goal = (prev.get("goal") or text) + f"\n[续跑] {text}"
            prev_sum = prev.get("cycles_summary") or []
            if isinstance(prev_sum, list):
                cycles_summary = [str(s) for s in prev_sum]

        with usage_context(session_id):  # 用量账本:本回合 LLM 调用记到该会话
            if self.preflight_env:
                async for event in self._preflight_env(session_id, cycles_summary):
                    yield event
            n = 0
            while n < hard_cap:
                if n >= budget:
                    why = should_extend_budget(
                        goal, cycles_summary, budget=budget, hard_cap=hard_cap)
                    if not why:
                        break
                    old = budget
                    budget = min(hard_cap, budget + ADAPTIVE_CHUNK)
                    cycles_summary.append(f"[sys] 预算延长 {old}→{budget}:{why}")
                    yield self._emit(ev.note(
                        "info", f"目标未完成,周期预算 {old}→{budget}({why})"))
                    if n >= budget:
                        break
                n += 1
                max_cycles = budget  # 周期账/工具卡上的分母跟着加
                # 1. 取消检查(周期边界):E3 控制位 + 同进程 token 快路径
                if self._loop_cancel_requested(session_id):
                    yield self._emit(ev.note(
                        "warn", f"自主循环已取消(第 {n} 周期边界);已完成周期的结果保留"))
                    closed = True
                    break

                # 2. steering:捎话(USER_MESSAGE,steer)并入回合目标
                for msg in self._consume_loop_steering(session_id):
                    goal += f"\n[用户第 {n} 周期补充] {msg}"
                    yield self._emit(ev.intervention(
                        f"已并入用户补充:{clip_summary(msg, max_chars=80)}", mode="steer"))

                # 3. LLM 决策:同步 provider 进线程池,不阻塞事件循环
                #    (store/SQLite 操作全部留在事件循环线程,包括状态摘要)
                #    有 on_delta 时把 say 字段增量裸 yield 为 think.delta
                #    (Cursor 式等待可见思考;通道与 say.delta 同款,不经 _emit)
                state_summary = self._converse_state(session_id)
                cyc_out: dict = {}
                try:
                    async for event in self._cycle_stream(
                            goal=goal, cycles_summary=list(cycles_summary),
                            state_summary=state_summary, route_pin=route_pin,
                            out=cyc_out):
                        yield event
                    result = cyc_out["result"]
                    if cyc_out.get("streamed"):
                        yield ev.think_end()
                except BrainUnavailable as exc:
                    err = _llm_error_text(exc)
                    yield self._emit(ev.note("bad", err))
                    store.append_chat(session_id, "agent", err)
                    closed = True
                    break
                if route_pin is None:
                    pin = getattr(result, "route_index", None)  # B3 可选字段,缺失不钉
                    if isinstance(pin, int) and not isinstance(pin, bool):
                        route_pin = pin

                action = getattr(result, "action", None)
                say_text = getattr(result, "say", None)
                say_text = say_text.strip() if isinstance(say_text, str) else ""
                if getattr(result, "done", False) or not isinstance(action, dict):
                    # say 默认想收束;若还有未完成工作则拒绝停机(pi:说话不等于停)
                    why = self._should_continue(goal, cycles_summary)
                    if why:
                        self._forced_continues += 1
                        cycles_summary.append(f"[sys] 收束被拒:{why}")
                        yield self._emit(ev.note("info", f"继续工作:{why}"))
                        continue
                    reply = say_text or "本回合没有更多可做的了。"
                    yield self._emit(ev.say([reply]))
                    store.append_chat(session_id, "agent", reply)
                    closed = True
                    clean_stop = True
                    break

                # 4. 周期账:每周期恰好一条,先于动作执行(前端进度条契约)
                kind = str(action.get("type") or "")
                yield self._emit(ev.agent_cycle(n, max_cycles, kind))
                sig = json.dumps(action, sort_keys=True, ensure_ascii=False)
                repeat = repeat + 1 if sig == prev_sig else 1
                prev_sig = sig
                if repeat >= _LOOP_REPEAT_BREAKER:
                    yield self._emit(ev.note(
                        "warn", f"熔断:同一动作已连续提出 {repeat} 次({kind}),"
                                f"不再执行,回合收束"))
                    closed = True
                    break

                # 5. 动作执行(闭集分发;单周期失败不炸回合,如实记入摘要)
                out: dict = {}
                async for event in self._loop_action_events(
                        session_id, n, max_cycles, goal, action, say_text, out):
                    yield event
                summary = clip_summary(out.get("summary") or f"{kind} 无输出")
                cycles_summary.append(f"[{n}] {kind}:{summary}")
                store.save_loop_op(
                    session_id, op_id=op_id, phase="running", goal=goal, n=n,
                    max_cycles=max_cycles, cycles_summary=cycles_summary,
                    last_action=kind, last_summary=summary, route_pin=route_pin)
                if out.get("terminal"):
                    closed = True
                    break

                # 6. 失败未换策略熔断:同签名连续失败 2 次即停,如实声明未解决
                if out.get("ok", True):
                    failed_sig, failed_streak = None, 0
                else:
                    failed_streak = failed_streak + 1 if sig == failed_sig else 1
                    failed_sig = sig
                    if failed_streak >= _LOOP_FAILURE_BREAKER:
                        yield self._emit(ev.note(
                            "warn", f"unresolved_failure:同一动作连续失败 "
                                    f"{failed_streak} 次未换策略({kind}:{summary}),"
                                    f"回合收束,问题未解决"))
                        closed = True
                        break

        if not closed:
            digest = clip_summary(";".join(cycles_summary), max_chars=200) or "无"
            yield self._emit(ev.note(
                "warn", f"已达周期上限({max_cycles}),回合收束;进展:{digest}"))
            store.save_loop_op(
                session_id, op_id=op_id, phase="running", goal=goal,
                n=max_cycles, max_cycles=max_cycles, cycles_summary=cycles_summary,
                last_summary=digest, route_pin=route_pin)
        elif clean_stop:
            store.clear_loop_op(session_id)

    async def _preflight_env(self, session_id: str,
                             cycles_summary: list[str]) -> AsyncIterator[dict]:
        """回合开工先检环境;可自动装的缺失引擎当场代装,再复检。"""
        from insar_agent.runtime.env_inventory import open_installables
        from insar_agent.runtime.install_runner import run_install

        # 预检才允许冷启动 WSL 探测;摘要路径仍只窥视缓存(对话回合不堵 20s)
        self._wsl_for_inventory()
        env_text = self._env_summary_text()
        yield self._emit(ev.tool_start("preflight-env", "[预检]", "check_env", "检查环境"))
        yield self._emit(ev.tool_end("preflight-env", 0, clip_summary(env_text)))
        cycles_summary.append(f"[pre] check_env:{clip_summary(env_text)}")
        if not self.allow_auto_install:
            return
        for engine in open_installables(self._last_slots):
            if self._loop_cancel_requested(session_id):
                return
            tool_id = f"preflight-install-{engine}"
            yield self._emit(ev.tool_start(tool_id, "[预检]", "install_engine",
                                           f"安装 {engine}"))
            result = await asyncio.to_thread(run_install, engine)
            ok = bool(result.get("ok"))
            summary = str(result.get("summary") or "")
            yield self._emit(ev.tool_end(tool_id, 0 if ok else 1, clip_summary(summary)))
            cycles_summary.append(f"[pre] install_engine {engine}:{clip_summary(summary)}")
        if any(s.startswith("[pre] install_engine") for s in cycles_summary):
            env_text = self._env_summary_text()
            cycles_summary.append(f"[pre] check_env:{clip_summary(env_text)}")

    def _should_continue(self, goal: str, cycles_summary: list[str]) -> str | None:
        """有未完成工作时拒绝 say 停机。闲聊/纯状态查询不强迫续跑。"""
        from insar_agent.runtime.env_inventory import open_installables

        refuse_limit = 6 if _goal_is_work(goal) else 2
        if self._forced_continues >= refuse_limit:
            return None
        kinds = _cycle_kinds(cycles_summary)
        last = kinds[-1] if kinds else ""
        missing = open_installables(self._last_slots)
        if missing and "install_engine" not in kinds:
            if _goal_is_env_only(goal) or _goal_is_work(goal):
                return f"还有可自动安装的引擎:{'、'.join(missing)}"
        if last == "install_engine":
            return "安装后必须复检环境"
        if last == "check_env" and _goal_is_work(goal):
            if "list_data" not in kinds and "list_files" not in kinds and "plan" not in kinds:
                return "环境已盘点,继续盘点项目数据,不要收束"
        return None

    def _loop_cancel_requested(self, session_id: str) -> bool:
        """自主循环的周期边界取消检查(E3:control 位是持久化意图,token 是快路径)。

        消费语义:run 未在执行(status != running)且不在运行队列时,本循环
        就是取消意图的唯一在场消费者 —— 兑现(收束回合)后复位 control 位并
        释放 token,不让遗留意图误拦用户下一次显式执行;执行回合在场
        (running)时只读不碰,位的归属在执行回合的入口/步间检查点。

        排队例外(P1-4):run 在 run_queue 有活跃条目(pending/running)时,
        control 位属于未来的 execute 消费者(调度器出队 → execute 入口读位
        收尾为 interrupted)—— 这里只读不清,否则用户取消过的重计算照样开跑
        (重型计算管控红线)。
        """
        # 会话级取消(/api/abort 无 run 情形置位):一次性消费,先于 run 检查
        if session_id in self._session_cancels:
            self._session_cancels.discard(session_id)
            return True
        run = self.store.latest_run(session_id)
        if run is None:
            return False  # 无 run 且无会话级取消:无取消载体
        run_id = run["run_id"]
        token = self._tokens.get(run_id)
        hit = bool(token is not None and token.cancelled) \
            or run.get("control") == "cancel_requested"
        if hit and run.get("status") != "running":
            from insar_agent.loop.queue import RunQueue  # 局部导入:仅此处用到队列视图

            if RunQueue(self.store).position(run_id) is None:
                if run.get("control") == "cancel_requested":
                    self.store.clear_cancel(run_id)
                self._tokens.pop(run_id, None)
        return hit

    def _consume_loop_steering(self, session_id: str) -> list[str]:
        """消费捎话(USER_MESSAGE,deliver_as=steer)→ 返回文本列表,并入 goal。

        归属纪律参照 _consume_steer:会话有 run 时只取绑定该 run 的行
        (include_unattributed=False,防跨会话互吞 —— REVIEW-r2 P1-3 同款);
        无 run 时只取未定向(run_id IS NULL)的行,无 run 会话的捎话本就无从
        绑定。其余动作(KILL/SET_* 等)一律不动:它们的消费点在执行回合。
        """
        store = self.store
        run = store.latest_run(session_id)
        if run is not None:
            pool = store.due_actions("steer", run_id=run["run_id"],
                                     include_unattributed=False)
        else:
            pool = [a for a in store.due_actions("steer") if a["run_id"] is None]
        out: list[str] = []
        for action in pool:
            if action["action"] != "USER_MESSAGE":
                continue
            store.consume_action(action["id"])
            msg = str((action.get("payload") or {}).get("text") or "").strip()
            if msg:
                out.append(msg[:500])  # 捎话与 converse 历史条目同款硬预算
        return out

    async def _loop_action_events(self, session_id: str, n: int, max_cycles: int,
                                  goal: str, action: dict, say_text: str,
                                  out: dict) -> AsyncIterator[dict]:
        """执行一个循环动作。事件直接产出;结果写回 out(异步生成器无返回值):
        summary=结果摘要 / ok=是否成功(喂给未换策略熔断)/ terminal=是否收束回合。

        纪律:动作语义全部复用既有代码路径(_apply_converse/_plan_turn 同源),
        绝不绕过校验/状态机;单个动作抛错不炸回合 —— 隔离为失败摘要留给 LLM
        换策略(EventBus 监听器隔离的同款哲学,absorb-E8)。
        """
        kind = str(action.get("type") or "")
        store = self.store

        if kind == "thinking":
            body = say_text or "(整理思路)"
            yield self._emit(ev.thinking("思考", body))
            out.update(summary=f"思考:{body}", ok=True)
            return

        if kind == "execute":
            # 红线 §0.3:execute 只产生确认卡(ask)+ say 收束,绝不自启流水线;
            # 「计划不会自动开始」的用户承诺不因自主循环而变。
            run = store.latest_run(session_id)
            if run is None:
                yield self._emit(ev.note(
                    "warn", "还没有可执行的 run:先完成规划,再请求执行"))
                out.update(summary="execute 被拒:当前会话还没有 run,应先 plan", ok=False)
                return
            reply = say_text or "计划已就绪,等你确认后开始执行(计划不会自动开始)。"
            yield self._emit(ev.ask(
                f"Agent 请求执行 run {run['run_id']} 的待跑步骤,是否批准?",
                [{"key": "confirm", "label": "确认执行(等价点击「运行流水线」)",
                  "options": ["run_pipeline"]}]))
            yield self._emit(ev.say([reply]))
            store.append_chat(session_id, "agent", reply)
            out.update(summary=f"已发执行确认卡等待用户批准(run {run['run_id']})",
                       ok=True, terminal=True)
            return

        if kind == "plan":
            # 与下方工具类动作同款单动作异常隔离(P2-11):规划流程(探测/建库/
            # 决策点)任一环节抛错只记失败摘要留给 LLM 换策略,不炸回合
            try:
                async for event in self._loop_plan_action(session_id, goal, action, out):
                    yield event
            except Exception as exc:  # noqa: BLE001 —— 单周期失败隔离,如实进摘要
                log.exception("自主循环动作 plan 执行异常")
                out.update(summary=f"plan 执行异常:{type(exc).__name__}: {exc}",
                           ok=False)
            return

        if kind not in LOOP_ACTIONS:
            yield self._emit(ev.note(
                "warn", f"动作越界已忽略:{kind!r}(闭集:{LOOP_ACTIONS})"))
            out.update(summary=f"动作 {kind!r} 越界被拦截,请改用闭集动作或 say 收束",
                       ok=False)
            return

        # ---- 其余动作:tool.start/tool.end 包裹的只读查询或入队 ----
        tool_id = f"loop{n}"
        label = _LOOP_ACTION_LABELS.get(kind, kind)
        yield self._emit(ev.tool_start(tool_id, f"[{n:02d}/{max_cycles:02d}]", kind, label))
        extra_events: list[dict] = []
        try:
            if kind == "search_data":
                summary, ok = await self._loop_search_data(action)
            elif kind == "inspect_file":
                summary, ok = self._loop_inspect_file(session_id, action)
            elif kind == "check_env":
                summary, ok = self._env_summary_text(), True
            elif kind == "list_data":
                summary, ok = self._datasets_text(), True
            elif kind == "list_files":
                summary, ok = self._project_files_text()
            elif kind == "install_engine":
                summary, ok = await self._loop_install_engine(action)
            elif kind == "learn_tool":
                from insar_agent.runtime.explore import learn_tool
                summary, ok = learn_tool(str(action.get("tool") or ""))
            elif kind == "search_docs":
                from insar_agent.runtime.explore import search_docs
                summary, ok = await asyncio.to_thread(
                    search_docs, str(action.get("query") or ""))
            elif kind == "probe_scratch":
                from insar_agent.runtime.explore import probe_scratch
                summary, ok = await asyncio.to_thread(
                    probe_scratch,
                    str(action.get("kind") or ""),
                    str(action.get("name") or ""),
                    project_root=self.project_root)
            elif kind == "status":
                summary, ok = self._run_status_text(session_id), True
            else:  # set_params / set_method
                summary, ok, extra_events = self._loop_queue_change(session_id, kind, action)
        except Exception as exc:  # noqa: BLE001 —— 单周期失败隔离,如实进摘要
            log.exception("自主循环动作 %s 执行异常", kind)
            summary, ok = f"{kind} 执行异常:{type(exc).__name__}: {exc}", False
        yield self._emit(ev.tool_end(tool_id, 0 if ok else 1, clip_summary(summary)))
        for event in extra_events:
            yield self._emit(event)
        out.update(summary=summary, ok=ok)

    async def _loop_plan_action(self, session_id: str, goal: str, action: dict,
                                out: dict) -> AsyncIterator[dict]:
        """plan 动作:与 turn 的 converse plan 分支同源 —— 场景闭集校验后进入
        既有规划流程(_plan_turn:探测、next_run 消费、make_plan、决策点叙述)。
        规划有问题(缺引擎等)时 run 停在 planning,如实记为失败摘要。
        """
        sc = scenario_of(str(action.get("scenario") or ""))
        if sc is None:
            yield self._emit(ev.note(
                "warn", f"计划动作的场景越界,已忽略:{action.get('scenario')!r}"))
            out.update(summary=f"plan 被拒:场景 {action.get('scenario')!r} 越界", ok=False)
            return
        region = action.get("region")
        timerange = action.get("timerange")
        region = region.strip() if isinstance(region, str) and region.strip() else None
        timerange = (timerange.strip()
                     if isinstance(timerange, str) and timerange.strip() else None)
        if region or timerange:
            # 与 turn 的 converse plan 分支同款:只覆盖展示元数据,不进指纹
            sc = dataclasses.replace(sc, region=region or sc.region,
                                     dates=timerange or sc.dates)
        session = self.store.get_session(session_id)
        async for event in self._plan_turn(session_id, session, goal, sc,
                                           intent_source="agent_loop"):
            yield event
        run = self.store.latest_run(session_id)
        if run is None or run["status"] == "planning":
            out.update(summary=f"规划未就绪:{self._run_status_text(session_id)}", ok=False)
        else:
            out.update(summary=f"规划完成:{self._run_status_text(session_id)}", ok=True)

    def _loop_queue_change(self, session_id: str, kind: str,
                           action: dict) -> tuple[str, bool, list[dict]]:
        """set_params/set_method:与 _apply_converse 同语义 —— 入 pending_actions
        队列(steer),消费点的 apply_change 会再校验一次。B3 已做闭集校验,
        这里用 registry 复核一遍(双保险,与消费点纪律同源),越界只拒不炸。
        返回 (摘要, 是否成功, 追发事件)。
        """
        store = self.store
        run = store.latest_run(session_id)
        if run is None:
            return ("参数/方法修改无处归属:当前会话还没有 run,应先 plan", False, [])
        step = action.get("step")
        if isinstance(step, bool) or not isinstance(step, int) or step not in self.registry:
            return (f"步骤号越界:{step!r}(闭集:{sorted(self.registry)})", False, [])
        cap = self.registry[step]
        if kind == "set_method":
            method = action.get("method")
            if not isinstance(method, str) or cap.method(method) is None:
                return (f"方法越界:{method!r}(候选:{[m.id for m in cap.methods]})",
                        False, [])
            act_name, payload = "SET_METHOD", {"method": method}
        else:
            params = action.get("params")
            if not isinstance(params, dict) or not params:
                return ("params 缺失或为空", False, [])
            errors = cap.validate_params(params)
            if errors:
                return (f"参数校验失败:{errors}", False, [])
            act_name, payload = "SET_PARAMS", {"params": params}
        store.push_action(scope="step", target=str(step), action=act_name,
                          payload=payload, deliver_as="steer", run_id=run["run_id"])
        msg = f"已排队:第 {step} 步 {act_name} {payload}(steer,下一检查点生效)"
        return (msg, True, [ev.intervention(msg, affected=[step])])

    def _loop_inspect_file(self, session_id: str, action: dict) -> tuple[str, bool]:
        """inspect_file:闭集内文件检查(红线 §0.5「LLM 零命令零路径」)。

        可检查对象只有两类:当前 run 某步的日志尾部与产物清单(step 字段)、
        按名称匹配的本地数据集(name 字段)。LLM 给的值只做闭集匹配/查表,
        绝不当文件系统路径解引用;日志读取走 _tail_text 的有界口径。
        """
        step = action.get("step")
        if isinstance(step, int) and not isinstance(step, bool):
            run = self.store.latest_run(session_id)
            if run is None:
                return "没有 run,无步骤日志/产物可查", False
            row = self.store.load_step(run["run_id"], step)
            if row is None:
                return f"第 {step} 步不在当前 run 中", False
            parts = [f"第 {step} 步「{row.name}」:状态 {row.state},方法 {row.method}"]
            arts = self.store.artifacts_of(run["run_id"], step)
            if arts:
                names = "、".join(Path(a["path"]).name for a in arts[:5])
                parts.append(f"产物 {len(arts)} 个:{names}")
            # 日志尾行只在失败时回灌且 ≤120 字(红线 §0.5:原始日志绝不进 LLM,
            # 只允许失败步骤的错误窗口)—— 成功/在跑步骤的日志与 LLM 无关
            if row.state == "failed" and row.log_path:
                tail = _tail_text(Path(row.log_path), limit_kb=4)
                lines = [ln.strip() for ln in tail.splitlines() if ln.strip()]
                if lines:
                    parts.append(f"日志尾行:{lines[-1][:120]}")
            return ";".join(parts), True
        name = str(action.get("name") or "").strip()
        if name:
            for d in self._scan_datasets():
                if name.lower() in str(d.get("name", "")).lower():
                    rng = d.get("date_range") or {}
                    span = f",{rng['start']}~{rng['end']}" if rng else ""
                    kind_label = _DATASET_KIND_LABELS.get(d["kind"], d["kind"])
                    return (f"数据集 {d['name']}:{kind_label},"
                            f"{_human_size(d['size_bytes'])}{span}", True)
            if self.project_root and self.project_root.is_dir():
                from insar_agent.project.paths import find_in_project, read_text

                hits = find_in_project(self.project_root, name)
                if hits:
                    it = hits[0]
                    extra = f"(另有 {len(hits) - 1} 个同名)" if len(hits) > 1 else ""
                    try:
                        body = read_text(self.project_root, it["rel"])
                    except (OSError, ValueError):
                        return f"项目文件 {it['rel']} 无法读取", False
                    return (f"项目文件 {it['rel']}{extra}"
                            f"({_human_size(len(body.encode()))}):\n{body[:400]}", True)
            return f"没有名称匹配「{name}」的本地数据集或项目文件", False
        return "inspect_file 需要 step(步骤号)或 name(数据集名),都未提供", False

    async def _loop_search_data(self, action: dict) -> tuple[str, bool]:
        """search_data:本地盘点 + ASF 检索 + 可选 web,经 gather_limited 并行扇出。

        insar_agent.net(B4)与 loop.subtasks(B8)是并行在建单元,经契约解耦:
        模块缺失/接口不符/扇出抛错 → 优雅降级为仅本地盘点(_memory_snippets
        同款防御纪律),检索能力缺位绝不炸循环。部分失败不整体失败(net 契约
        同语义):任一来源成功即算本周期成功。联网纪律:只发查询词,不发数据。

        字段契约(P1-1):facade 校验器透传的是 region/timerange(循环提示词
        教的也是这两个),由 _asf_filters 翻译为 asf_search 的
        intersects_wkt/start/end;翻译不出的条件不上送、进摘要注记。
        """
        import importlib

        def field(key: str) -> str | None:
            v = action.get(key)
            return v.strip() if isinstance(v, str) and v.strip() else None

        try:
            net = importlib.import_module("insar_agent.net")
            subtasks = importlib.import_module("insar_agent.loop.subtasks")
            asf_search, web_search = net.asf_search, net.web_search
            gather_limited = subtasks.gather_limited
        except Exception:  # noqa: BLE001 —— 可选依赖,任何失败都按「模块未就绪」处置
            local = await asyncio.to_thread(self._datasets_summary_line)
            return f"本地数据集:{local}(联网检索模块未就绪,已降级为仅本地盘点)", True

        wkt, start, end, filter_notes = _asf_filters(field("region"), field("timerange"))
        named = {
            "local": asyncio.to_thread(self._datasets_summary_line),
            "asf": asyncio.to_thread(asf_search, intersects_wkt=wkt,
                                     start=start, end=end,
                                     max_results=20),
        }
        query = field("query")
        if query:
            named["web"] = asyncio.to_thread(web_search, query, k=5)
        try:
            results = await gather_limited(named, limit=3, timeout=25.0)
        except Exception:  # noqa: BLE001 —— B8 契约防御:扇出器坏了退回仅本地盘点
            log.debug("search_data 扇出失败,降级为仅本地盘点", exc_info=True)
            for coro in named.values():
                try:
                    coro.close()  # 未被消费的协程显式关闭,不留 never-awaited 告警
                except Exception:  # noqa: BLE001
                    pass
            local = await asyncio.to_thread(self._datasets_summary_line)
            return f"本地数据集:{local}(检索扇出失败,已降级为仅本地盘点)", True

        parts: list[str] = []
        any_ok = False
        for key, title in (("local", "本地数据集"), ("asf", "ASF 检索"), ("web", "网页检索")):
            r = results.get(key)
            if r is None:
                continue
            if getattr(r, "ok", False):
                any_ok = True
                value = getattr(r, "value", None)
                if key == "local":
                    parts.append(f"{title}:{value}")
                elif isinstance(value, list):
                    parts.append(f"{title}:命中 {len(value)} 条")
                else:
                    parts.append(f"{title}:完成")
            else:
                err = clip_summary(str(getattr(r, "error", "") or "未知错误"), max_chars=60)
                parts.append(f"{title}:失败({err})")
        parts.extend(f"注:{n}" for n in filter_notes)  # 被丢弃的过滤条件如实可见
        return ";".join(parts) or "检索无结果", any_ok

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
            triage = self.brain.triage(
                log_text or (result.detail or ""),
                # 步骤技能《常见失败与处置》附给分诊上下文(无技能=空串,行为不变)
                skill_notes=skill_section_text(sid, SECTION_FAILURES))
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
