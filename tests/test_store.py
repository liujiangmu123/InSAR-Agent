"""状态机 + 幂等守卫 + 乐观并发(AGENT-DESIGN §4.3/§6.1;absorb-E2/E6)。"""

import pytest

from insar_agent.core.store import StageConflict, Store

HASHES = {"task_hash": "t", "args_hash": "a", "local_hash": "l", "eval_hash": "e"}


def _mk_run(store: Store, run_id="r1"):
    store.create_session("s1", "test")
    store.create_run(run_id, "s1", workspace="/tmp/ws")
    store.create_step(run_id, 6, capability="unwrap", name="解缠", method="snaphu_mcf",
                      params={"min_coherence": 0.25}, hashes=HASHES)
    return run_id


def test_stage_progression_and_guards(store):
    rid = _mk_run(store)
    step = store.load_step(rid, 6)
    assert step.stage == "PENDING" and step.stage_lt("PREPARED")

    assert store.advance(rid, 6, "PREPARED", config_hash="c1") is True
    assert store.advance(rid, 6, "LAUNCHED", job_dir="/jobs/x") is True
    step = store.load_step(rid, 6)
    assert step.stage == "LAUNCHED" and step.job_dir == "/jobs/x"
    assert not step.stage_lt("PREPARED")  # 守卫:PREPARED 已过,恢复时跳过渲染

    # 幂等守卫:重复推进到已达阶段直接跳过并返回 False,字段不覆写
    assert store.advance(rid, 6, "PREPARED", config_hash="c2") is False
    assert store.advance(rid, 6, "LAUNCHED", job_dir="/jobs/other") is False
    step = store.load_step(rid, 6)
    assert step.stage == "LAUNCHED" and step.job_dir == "/jobs/x" \
        and step.config_hash == "c1"


def test_cross_stage_recovery_uses_stage_lt(store):
    """跨阶段恢复(§4.3):stage 落后于实际进度时,用 < 比较逐段独立判断。

    场景:job.rc 已写但产物未发现 —— stage 停在 LAUNCHED,恢复应跳过
    PREPARED/LAUNCHED,直接从 RUNNING 之后继续(aiida 六处守卫的写法)。
    """
    rid = _mk_run(store)
    store.advance(rid, 6, "LAUNCHED")  # 允许跨越 PREPARED 单调向前
    step = store.load_step(rid, 6)
    assert not step.stage_lt("PREPARED")   # 已过:跳过渲染
    assert not step.stage_lt("LAUNCHED")   # 已过:跳过启动
    assert step.stage_lt("RUNNING")        # 未过:从这继续
    assert step.stage_lt("COLLECTED") and step.stage_lt("VERIFIED")


def test_optimistic_concurrency(store):
    rid = _mk_run(store)
    store.advance(rid, 6, "PREPARED")
    # 期望 stage 不匹配 → 冲突(外部终结者场景,absorb-E6)
    with pytest.raises(StageConflict):
        store.advance(rid, 6, "LAUNCHED", expect_stage="PENDING")
    store.advance(rid, 6, "LAUNCHED", expect_stage="PREPARED")


def test_idempotent_replay_skips_completed(store):
    """Phase 0 验收:同一步执行两次,第二次全部守卫跳过。"""
    rid = _mk_run(store)
    executed = []

    def run_once():
        # 步级整体跳过判定先行(§5.1 短路序:内存哈希比对最便宜)
        skip, reason = store.should_skip(rid, 6, HASHES["eval_hash"])
        if skip:
            return
        step = store.load_step(rid, 6)
        if step.stage_lt("PREPARED"):
            executed.append("render")
            store.advance(rid, 6, "PREPARED")
        step = store.load_step(rid, 6)
        if step.stage_lt("LAUNCHED"):
            executed.append("launch")
            store.advance(rid, 6, "LAUNCHED")
        step = store.load_step(rid, 6)
        if step.stage_lt("VERIFIED"):
            executed.append("verify")
            store.advance(rid, 6, "VERIFIED", state="done", run_ok=1)

    run_once()
    assert executed == ["render", "launch", "verify"]
    run_once()
    assert executed == ["render", "launch", "verify"]  # 第二次零执行


def test_should_skip_conditions(store):
    """should_skip 五条件:存在 × VERIFIED × run_ok × eval_hash × record_version。"""
    rid = _mk_run(store)

    # 无记录
    assert store.should_skip(rid, 99, "e") == (False, "no_metadata")
    # 未到 VERIFIED
    assert store.should_skip(rid, 6, HASHES["eval_hash"]) == (False, "not_verified")

    store.advance(rid, 6, "VERIFIED", state="done", run_ok=1)
    assert store.should_skip(rid, 6, HASHES["eval_hash"]) == (True, None)
    # eval_hash 变化(改参数/上游重跑)→ 不可跳过
    assert store.should_skip(rid, 6, "changed") == (False, "eval_hash_changed")
    # 标脏后不可跳过
    store.set_stale(rid, 6, True, "upstream_changed")
    assert store.should_skip(rid, 6, HASHES["eval_hash"]) == (False, "upstream_changed")
    store.set_stale(rid, 6, False, None)

    # run_ok 双判定未过 → 不可跳过
    store.mark_step(rid, 6, state="done", run_ok=0)
    assert store.should_skip(rid, 6, HASHES["eval_hash"]) == (False, "run_ok_failed")


def test_should_skip_gates_on_record_version(store):
    """record_version 门控(absorb-M):指纹算法升级后旧记录判「不可比」,
    绝不误判可复用 —— 哪怕 stage/run_ok/eval_hash 全部匹配。"""
    rid = _mk_run(store)
    store.advance(rid, 6, "VERIFIED", state="done", run_ok=1)
    assert store.should_skip(rid, 6, HASHES["eval_hash"]) == (True, None)

    # 模拟旧版本记录(算法已升级,库里还是老格式)
    with store.db.tx() as cur:
        cur.execute("UPDATE steps SET record_version=0 WHERE run_id=? AND step_id=?",
                    (rid, 6))
    assert store.should_skip(rid, 6, HASHES["eval_hash"]) == (False, "outdated_metadata")


def test_upsert_step_updates_declaration_keeps_execution(store):
    """upsert 幂等:重复声明只更新配置/指纹,不动执行状态。"""
    rid = _mk_run(store)
    store.advance(rid, 6, "LAUNCHED", job_dir="/jobs/x")

    new_hashes = dict(HASHES, args_hash="a2", eval_hash="e2")
    store.upsert_step(rid, 6, capability="unwrap", name="解缠", method="snaphu_smooth",
                      params={"min_coherence": 0.3}, hashes=new_hashes)
    step = store.load_step(rid, 6)
    assert step.method == "snaphu_smooth" and step.eval_hash == "e2"
    assert step.stage == "LAUNCHED" and step.job_dir == "/jobs/x"  # 执行状态保留


def test_command_intent_settlement(store):
    """命令两段式:意图落盘(exit NULL)→ 结算;绝不重结算。"""
    rid = _mk_run(store)
    cid = store.reserve_command(rid, 6, ["snaphu", "-f", "cfg"], cwd="/w")
    cmds = store.commands_of(rid, 6)
    assert len(cmds) == 1 and cmds[0]["exit_code"] is None  # 意图可见

    store.settle_command(cid, exit_code=0, duration=12.5)
    assert store.commands_of(rid, 6)[0]["exit_code"] == 0
    store.settle_command(cid, exit_code=1, duration=99)  # 重结算被拒
    assert store.commands_of(rid, 6)[0]["exit_code"] == 0


def test_recovery_is_point_lookup(store):
    """absorb-E2 负面测试:恢复所需的一切在行内,单条点查可得。"""
    rid = _mk_run(store)
    store.advance(rid, 6, "LAUNCHED", job_dir="/jobs/x", log_path="/jobs/x/job.log")
    store.update_log_offset(rid, 6, 4096)
    step = store.load_step(rid, 6)  # 一次主键点查
    assert step.job_dir and step.log_path and step.log_offset == 4096
    assert step.replay in ("never", "safe")  # 恢复策略也在行内


def test_actions_dual_delivery(store):
    """双投递语义(§4.5/absorb-J):不同 deliver_as 各入各队,互不串扰。"""
    _mk_run(store)
    store.push_action(scope="step", target="6", action="SET_METHOD",
                      payload={"method": "snaphu_smooth"}, deliver_as="steer")
    store.push_action(scope="run", target="r1", action="PAUSE", deliver_as="follow_up")
    assert len(store.due_actions("steer")) == 1
    assert len(store.due_actions("follow_up")) == 1
    assert len(store.due_actions("next_run")) == 0

    a = store.due_actions("steer")[0]
    assert store.consume_action(a["id"]) is True
    assert store.consume_action(a["id"]) is False  # 消费幂等
    assert store.due_actions("steer") == []

    # 批量出队:单事务取出并标记消费
    drained = store.consume_actions("follow_up")
    assert [d["action"] for d in drained] == ["PAUSE"]
    assert store.due_actions("follow_up") == []

    with pytest.raises(ValueError):
        store.push_action(scope="run", target="r1", action="PAUSE", deliver_as="bogus")


def test_actions_edit_and_withdraw_before_consume(store):
    """未消费(consumed_at IS NULL)可编辑/撤回;已消费不可(§4.5)。"""
    _mk_run(store)
    aid = store.push_action(scope="step", target="6", action="SET_PARAMS",
                            payload={"params": {"min_coherence": 0.3}}, deliver_as="steer")

    # 排队中:可编辑 payload 与投递语义
    assert store.edit_action(aid, payload={"params": {"min_coherence": 0.35}},
                             deliver_as="follow_up") is True
    assert store.due_actions("steer") == []
    a = store.due_actions("follow_up")[0]
    assert a["payload"]["params"]["min_coherence"] == 0.35

    # 排队中:可撤回
    assert store.withdraw_action(aid) is True
    assert store.due_actions("follow_up") == []

    # 已消费:编辑与撤回都被拒
    aid2 = store.push_action(scope="run", target="r1", action="PAUSE", deliver_as="steer")
    store.consume_action(aid2)
    assert store.edit_action(aid2, payload={}) is False
    assert store.withdraw_action(aid2) is False


def test_lease_heartbeat_and_steal(store):
    assert store.acquire_lease("pool:cpu", "runA", stale_after=0.05)
    assert not store.acquire_lease("pool:cpu", "runB", stale_after=999)  # 活租约不可抢
    import time
    time.sleep(0.06)
    assert store.acquire_lease("pool:cpu", "runB", stale_after=0.05)  # 心跳超时可抢占


def test_artifact_lookup_walks_parent_chain(store):
    """run fork(absorb-E5):产物先查本 run,再沿祖先链。"""
    store.create_session("s1", "t")
    store.create_run("parent", "s1", workspace="/w")
    store.create_run("child", "s1", workspace="/w", parent_run_id="parent")
    store.record_artifact("parent", 5, "ifg_filt", path="data/ifg_filt", kind="IFG_WRAPPED",
                          layout="isce2", policy="stat", fp="fp1")
    art = store.find_artifact("child", "ifg_filt")
    assert art is not None and art["run_id"] == "parent"


def test_trace_truncation(store):
    tid = store.append_trace(session_id="s1", thought="x" * 99999, raw_response="y" * 99999)
    assert tid > 0
    row = store.db.query_one("SELECT thought, raw_response FROM trace WHERE id=?", (tid,))
    assert len(row["thought"]) == 2000 and len(row["raw_response"]) == 3000
