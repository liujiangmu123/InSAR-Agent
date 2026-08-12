"""干预动作应用(AGENT-DESIGN §4.5/§1.7)。

队列本体在 store(pending_actions 表);这里是动作语义。
动作集对齐 aiida-workgraph task_actions.py:36-50,加 InSAR 特有的两个:
    RESET | PAUSE | PLAY | SKIP | KILL | SET_METHOD | SET_PARAMS

与 aiida-workgraph 的差异(§4.5):
  1. 算影响范围并展示,不静默重置;
  2. 资源参数与科学参数区分对待(SET_PARAMS 走 stale.apply_change 的三分类)。

投递语义(pending_actions.deliver_as,absorb-J 双投递 + absorb-E4 pi 三队列):
    steer      当前步骤结束后、下一步启动前生效
    follow_up  当前 run 结束后生效
    next_run   下一次 run 规划时生效
未消费(consumed_at IS NULL)的动作可编辑/撤回(store.edit_action/withdraw_action)。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from insar_agent.core.stale import StaleImpact, apply_change
from insar_agent.core.store import Store
from insar_agent.registry.capabilities import downstream_of
from insar_agent.registry.model import Capability

ACTIONS = ("RESET", "PAUSE", "PLAY", "SKIP", "KILL", "SET_METHOD", "SET_PARAMS")


@dataclass
class ActionOutcome:
    action: str
    ok: bool
    message: str
    impact: StaleImpact | None = None
    events: list[dict] = field(default_factory=list)  # 供轨迹流的 intervention 条目(§7.4)


def apply_action(store: Store, run_id: str, action: dict, *,
                 registry: dict[int, Capability], tool_versions: dict) -> ActionOutcome:
    """应用一条已出队的动作。KILL/PAUSE/PLAY 由 driver 层处理(需要令牌/调度权),
    这里处理纯状态类动作;返回 intervention 事件供轨迹留痕。"""
    kind = action["action"]
    payload = action.get("payload") or {}

    if kind == "SET_METHOD":
        step_id = int(action["target"])
        impact = apply_change(store, run_id, step_id, registry=registry,
                              tool_versions=tool_versions, method=payload["method"])
        msg = (f"第 {step_id} 步方法改为 {payload['method']},"
               f"影响 {len(impact.affected)} 步({impact.reason})")
        return ActionOutcome(kind, True, msg, impact,
                             [{"t": "intervention", "text": msg,
                               "affected": impact.affected_ids()}])

    if kind == "SET_PARAMS":
        step_id = int(action["target"])
        impact = apply_change(store, run_id, step_id, registry=registry,
                              tool_versions=tool_versions, params_patch=payload["params"])
        if impact.reason == "no_change":
            msg = f"第 {step_id} 步参数变更不影响结果(资源参数),无需重跑"
        else:
            msg = (f"第 {step_id} 步参数变更({impact.reason}),"
                   f"影响 {len(impact.affected)} 步")
        return ActionOutcome(kind, True, msg, impact,
                             [{"t": "intervention", "text": msg,
                               "affected": impact.affected_ids()}])

    if kind == "RESET":
        step_id = int(action["target"])
        store.reset_step_for_rerun(run_id, step_id)
        downstream = [d for d in downstream_of(step_id)
                      if (s := store.load_step(run_id, d)) and s.state in ("done", "stale")]
        for d in downstream:
            store.set_stale(run_id, d, True, "upstream_changed")
        msg = f"第 {step_id} 步已复位;下游 {downstream} 已标脏"
        return ActionOutcome(kind, True, msg, None,
                             [{"t": "intervention", "text": msg,
                               "affected": [step_id, *downstream]}])

    if kind == "SKIP":
        step_id = int(action["target"])
        step = store.load_step(run_id, step_id)
        if step is None:
            return ActionOutcome(kind, False, f"步骤 {step_id} 不存在")
        store.mark_step(run_id, step_id, state="skipped")
        msg = f"第 {step_id} 步已标记跳过(产物沿用现状,责任在操作者)"
        return ActionOutcome(kind, True, msg, None, [{"t": "intervention", "text": msg}])

    if kind == "PAUSE":
        store.set_run_status(run_id, "paused")
        return ActionOutcome(kind, True, "运行已暂停(当前步骤完成后不再启动新步骤)",
                             None, [{"t": "intervention", "text": "已暂停"}])

    if kind == "PLAY":
        store.set_run_status(run_id, "running")
        return ActionOutcome(kind, True, "运行已恢复", None,
                             [{"t": "intervention", "text": "已恢复"}])

    if kind == "KILL":
        # 实际取消由 driver 持有的 CancelToken 完成;这里只留痕
        return ActionOutcome(kind, True, "取消请求已受理", None,
                             [{"t": "intervention", "text": "取消当前步骤"}])

    return ActionOutcome(kind, False, f"未知动作 {kind}")
