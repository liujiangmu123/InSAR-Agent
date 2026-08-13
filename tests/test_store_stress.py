"""并发压力:乐观并发 advance、命令预留幂等、动作队列原子性、租约互斥、fork 谱系。

方法:threading.Barrier 制造真并发;库用文件库(:memory: 不走 WAL,形状失真)。
单进程内所有线程共享一个连接、靠 RLock 串行 —— 这些测试断言的是「串行化之后
语义仍正确」:胜负恰好一次、幂等不重复、动作恰好消费一次、租约至多一个持有者;
并用 join 超时兜死锁(busy_timeout / RLock 失效会在这里露头)。
"""

import sqlite3
import threading
import time

import pytest

from conftest import TIME_FACTOR
from insar_agent.core.db import Database
from insar_agent.core.store import STAGES, StageConflict, Store

HASHES = {"task_hash": "t", "args_hash": "a", "local_hash": "l", "eval_hash": "e"}


@pytest.fixture()
def fstore(tmp_path):
    """文件库 Store:真 WAL + busy_timeout(与部署形态一致)。"""
    s = Store(Database(tmp_path / "stress.db"))
    yield s
    s.close()


def _mk_run(store: Store, run_id: str = "r1", steps: tuple[int, ...] = (6,)) -> str:
    store.create_session("s1", "stress")
    store.create_run(run_id, "s1", workspace="/w")
    for sid in steps:
        store.upsert_step(run_id, sid, capability="unwrap", name=f"步骤{sid}",
                          method="snaphu_mcf", params={"k": sid}, hashes=HASHES)
    return run_id


def _race(n: int, fn, timeout: float = 30.0):
    """n 线程 barrier 同起跑;返回 (results, errors),join 超时视为死锁。"""
    timeout *= TIME_FACTOR  # 死锁判定上限随负载放宽;无死锁时不多等
    barrier = threading.Barrier(n)
    results: list = [None] * n
    errors: list = [None] * n

    def work(i: int) -> None:
        try:
            barrier.wait(timeout)
            results[i] = fn(i)
        except Exception as exc:  # noqa: BLE001 —— 收集到主线程统一断言
            errors[i] = exc

    threads = [threading.Thread(target=work, args=(i,), daemon=True) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout)
    assert not any(t.is_alive() for t in threads), "线程未归 —— 疑似死锁"
    return results, errors


# ---------------- advance 乐观并发 ----------------


def test_advance_race_exactly_one_winner(fstore):
    """两线程同 step 同 stage 竞争推进:恰一胜(True)一败(False 幂等跳过),
    败者字段绝不落盘(现有 CAS 语义:输者读到已达阶段,直接跳过)。"""
    rid = _mk_run(fstore)
    results, errors = _race(
        2, lambda i: fstore.advance(rid, 6, "PREPARED", config_hash=f"c{i}"))
    assert errors == [None, None]
    assert sorted(results) == [False, True]
    winner = results.index(True)
    step = fstore.load_step(rid, 6)
    assert step.stage == "PREPARED"
    assert step.config_hash == f"c{winner}"  # 只有胜者的字段在


def test_advance_race_expect_stage_loser_self_stops(fstore):
    """expect_stage 提供时(外部终结者场景,absorb-E6):败者收 StageConflict 自停,
    而不是静默 False —— 两种失败形态由是否给 expect_stage 区分。"""
    rid = _mk_run(fstore)
    results, errors = _race(
        2, lambda i: fstore.advance(rid, 6, "PREPARED",
                                    expect_stage="PENDING", job_dir=f"/j{i}"))
    wins = [r for r in results if r is True]
    conflicts = [e for e in errors if isinstance(e, StageConflict)]
    assert len(wins) == 1 and len(conflicts) == 1
    assert fstore.load_step(rid, 6).job_dir == f"/j{results.index(True)}"


def test_advance_multi_step_mixed_no_cross_state(fstore):
    """6 线程混战 8 个 step × 5 级阶梯:每个 (step, stage) 档位恰好一人推进成功,
    步与步状态互不串;全程无 StageConflict(不给 expect_stage 时败者只收 False)。"""
    step_ids = tuple(range(8))
    rid = _mk_run(fstore, steps=step_ids)
    ladder = STAGES[1:]  # PREPARED..VERIFIED

    def climb(i: int):
        wins = []
        for sid in step_ids:
            for stage in ladder:
                if fstore.advance(rid, sid, stage):
                    wins.append((sid, stage))
        return wins

    results, errors = _race(6, climb)
    assert errors == [None] * 6
    all_wins = [w for r in results for w in r]
    # 40 个档位,每个恰好一次胜出;无重复、无遗漏
    assert len(all_wins) == len(step_ids) * len(ladder)
    assert len(set(all_wins)) == len(all_wins)
    for sid in step_ids:
        st = fstore.load_step(rid, sid)
        assert st.stage == "VERIFIED"
        assert st.params == {"k": sid}  # 步骤自身声明未被串改


# ---------------- reserve/settle 幂等 ----------------


def test_reserve_reentry_reuses_unsettled_intent(fstore):
    """崩溃重入:同 argv 重复 reserve 复用同一预留 id 并刷新落点,
    不产生第二条未结算命令;argv 变了(重规划)才是新意图;结算关帐后再
    reserve 同 argv 是新一次执行,照常新插。"""
    rid = _mk_run(fstore)
    argv = ["snaphu", "-f", "cfg"]
    cid = fstore.reserve_command(rid, 6, argv, stdout_path="/jobs/a1/job.log", attempt=1)
    # 执行器崩溃后恢复:attempt 按 COUNT+1 重算(executor.py),落点换 a2
    cid2 = fstore.reserve_command(rid, 6, argv, stdout_path="/jobs/a2/job.log", attempt=2)
    assert cid2 == cid
    unsettled = [c for c in fstore.commands_of(rid, 6) if c["exit_code"] is None]
    assert len(unsettled) == 1
    assert unsettled[0]["stdout_path"] == "/jobs/a2/job.log"  # 预留行已刷新
    assert unsettled[0]["attempt"] == 2
    assert fstore.latest_unsettled_command(rid, 6)["id"] == cid

    other = fstore.reserve_command(rid, 6, ["snaphu", "-s", "cfg2"])  # 意图变了
    assert other != cid

    fstore.settle_command(cid, exit_code=0, duration=1.0)
    fstore.settle_command(other, exit_code=1, duration=1.0)
    cid3 = fstore.reserve_command(rid, 6, argv, attempt=3)  # 已关帐 → 新意图
    assert cid3 not in (cid, other)


def test_reserve_race_converges_to_single_intent(fstore):
    """并发重复 reserve 同一意图:所有线程拿到同一个预留 id,未结算行恰一条。"""
    rid = _mk_run(fstore)
    results, errors = _race(
        8, lambda i: fstore.reserve_command(rid, 6, ["snaphu"], attempt=i + 1))
    assert errors == [None] * 8
    assert len(set(results)) == 1
    unsettled = [c for c in fstore.commands_of(rid, 6) if c["exit_code"] is None]
    assert len(unsettled) == 1


def test_settle_race_first_writer_wins_then_immutable(fstore):
    """并发结算同一命令:恰一人写入,之后重结算全部无声拒绝(现有语义:
    WHERE exit_code IS NULL,不抛错、不覆写)。"""
    rid = _mk_run(fstore)
    cid = fstore.reserve_command(rid, 6, ["snaphu"])
    _, errors = _race(
        8, lambda i: fstore.settle_command(cid, exit_code=i, duration=float(i)))
    assert errors == [None] * 8
    row = fstore.commands_of(rid, 6)[0]
    first = row["exit_code"]
    assert first in range(8) and row["duration"] == float(first)  # 同一人的成对写入
    fstore.settle_command(cid, exit_code=99, duration=99.0)  # 事后重结算
    assert fstore.commands_of(rid, 6)[0]["exit_code"] == first


# ---------------- pending_actions:按 run 隔离 + 恰好一次消费 ----------------


def test_actions_two_runs_concurrent_no_cross_talk(fstore):
    """两 run 并发入队 + 各自消费:互不串扰,每条动作恰好被消费一次。
    (store 侧隔离依据是 target 字段;消费方按 target 过滤 + consume_action CAS。)"""
    fstore.create_session("s1", "stress")
    for rid in ("rA", "rB"):
        fstore.create_run(rid, "s1", workspace="/w")
    per_run = 30
    consumed: dict[str, list[int]] = {"rA": [], "rB": []}

    def producer(rid: str) -> None:
        for k in range(per_run):
            fstore.push_action(scope="run", target=rid, action="PAUSE",
                               payload={"n": k}, deliver_as="steer")

    def consumer(rid: str) -> None:
        deadline = time.monotonic() + 20
        while len(consumed[rid]) < per_run and time.monotonic() < deadline:
            for a in fstore.due_actions("steer"):
                if a["target"] == rid and fstore.consume_action(a["id"]):
                    consumed[rid].append(a["id"])

    jobs = [lambda _i: producer("rA"), lambda _i: producer("rB"),
            lambda _i: consumer("rA"), lambda _i: consumer("rB")]
    _, errors = _race(4, lambda i: jobs[i](i), timeout=60.0)
    assert errors == [None] * 4
    assert len(consumed["rA"]) == per_run and len(consumed["rB"]) == per_run
    assert not set(consumed["rA"]) & set(consumed["rB"])  # 恰好一次:无双重消费
    assert fstore.due_actions("steer") == []  # 无遗漏
    for rid in ("rA", "rB"):  # 消费到的每一条确实属于自己
        marks = ",".join(str(i) for i in consumed[rid])
        rows = fstore.db.query(
            f"SELECT DISTINCT target FROM pending_actions WHERE id IN ({marks})")
        assert [r["target"] for r in rows] == [rid]


def test_consume_action_atomic_single_consumer(fstore):
    """due_actions 消费原子性:8 个消费者抢同一条动作,CAS 保证恰一人得手。"""
    _mk_run(fstore)
    aid = fstore.push_action(scope="run", target="r1", action="PAUSE")
    results, errors = _race(8, lambda i: fstore.consume_action(aid))
    assert errors == [None] * 8
    assert results.count(True) == 1 and results.count(False) == 7


def test_consume_actions_batch_exactly_once(fstore):
    """批量出队并发竞争:每条动作恰好出现在一个批次里(单事务原子)。
    注意:consume_actions 不按 run 过滤 —— 两个并发 run 同收 follow_up 会互吞,
    这是接口的已知边界(见测试报告 P1),多 run 并发场景应走逐条 CAS 消费。"""
    fstore.create_session("s1", "stress")
    for rid in ("rA", "rB"):
        fstore.create_run(rid, "s1", workspace="/w")
        for k in range(20):
            fstore.push_action(scope="run", target=rid, action="PAUSE",
                               payload={"n": k}, deliver_as="follow_up")
    results, errors = _race(2, lambda i: fstore.consume_actions("follow_up"))
    assert errors == [None, None]
    ids0 = [a["id"] for a in results[0]]
    ids1 = [a["id"] for a in results[1]]
    assert len(ids0) + len(ids1) == 40
    assert not set(ids0) & set(ids1)
    assert fstore.due_actions("follow_up") == []


# ---------------- leases:到期争抢互斥 ----------------


@pytest.mark.timing
def test_lease_expiry_single_takeover(fstore):
    """租约:活租不可抢;心跳超时后 8 人争抢恰一人接管;续租/释放语义完整。"""
    assert fstore.acquire_lease("pool:cpu", "h0", ttl=0.2)

    # 活租约:无论多少人、多大耐心(stale_after 大)都抢不走
    # (耐心阈值乘 TF:负载下 acquire→争抢之间的间隙不许老化越线)
    results, _ = _race(8, lambda i: fstore.acquire_lease("pool:cpu", f"c{i}",
                                                         stale_after=30.0 * TIME_FACTOR))
    assert results == [False] * 8

    time.sleep(0.3)  # 心跳老化超过 stale_after 阈值(老化型等待,方向安全不乘)
    results, errors = _race(8, lambda i: fstore.acquire_lease("pool:cpu", f"c{i}",
                                                              stale_after=0.15))
    assert errors == [None] * 8
    assert results.count(True) == 1  # 同一时刻至多一个持有者
    winner = f"c{results.index(True)}"
    row = fstore.db.query_one("SELECT holder FROM leases WHERE resource=?", ("pool:cpu",))
    assert row["holder"] == winner

    assert fstore.heartbeat_lease("pool:cpu", winner) is True   # 持有者续租
    assert fstore.heartbeat_lease("pool:cpu", "someone") is False  # 非持有者不能续
    fstore.release_lease("pool:cpu", "someone")  # 非持有者释放是无操作
    assert fstore.db.query_one("SELECT 1 FROM leases WHERE resource=?", ("pool:cpu",))
    fstore.release_lease("pool:cpu", winner)
    assert fstore.acquire_lease("pool:cpu", "h9", stale_after=30.0)  # 已释放可立即取


def test_lease_free_resource_single_winner(fstore):
    """空租约 8 人同抢:恰一人插入成功,其余读到活租返回 False。"""
    results, errors = _race(8, lambda i: fstore.acquire_lease("res:x", f"h{i}",
                                                              stale_after=30.0))
    assert errors == [None] * 8
    assert results.count(True) == 1


@pytest.mark.timing
def test_lease_reacquire_by_holder_renews(fstore):
    """同 holder 重复 acquire 是续租(刷新心跳),不是失败也不是双持有。

    判定窗:续租与 h1 来抢之间的间隙必须小于 stale_after —— 负载下两条语句
    之间可能被调度拖开,阈值与老化等待同乘系数保持比例。"""
    assert fstore.acquire_lease("res:y", "h0", stale_after=0.2 * TIME_FACTOR)
    time.sleep(0.25 * TIME_FACTOR)
    # 心跳已老化,但持有者自己 re-acquire 走续租分支,不受 stale_after 影响
    assert fstore.acquire_lease("res:y", "h0", stale_after=0.2 * TIME_FACTOR)
    # 续租刷新了心跳 → 别人立刻来抢(以同样阈值)抢不走
    assert fstore.acquire_lease("res:y", "h1", stale_after=0.2 * TIME_FACTOR) is False


# ---------------- fork 谱系 ----------------


def test_fork_lineage_isolation_and_artifact_chain(fstore):
    """run fork:parent_run_id 链完整;产物沿祖先链就近解析、子 run 产物遮蔽
    而不覆写父 run;子 run 推进自己的 step 绝不污染父 run 的同名 step。"""
    fstore.create_session("s1", "stress")
    fstore.create_run("A", "s1", workspace="/w")
    fstore.create_run("B", "s1", workspace="/w", parent_run_id="A")
    fstore.create_run("C", "s1", workspace="/w", parent_run_id="B")
    assert fstore.get_run("C")["parent_run_id"] == "B"
    assert fstore.get_run("B")["parent_run_id"] == "A"
    assert fstore.get_run("A")["parent_run_id"] is None

    fstore.record_artifact("A", 5, "ifg", path="/w/A/ifg", kind="IFG", layout="isce2",
                           policy="stat", fp="stat:x:fpA")
    assert fstore.find_artifact("C", "ifg")["run_id"] == "A"  # 隔两代仍可解析

    # 子 run 记录同名产物:就近遮蔽,父 run 记录原样保留
    fstore.record_artifact("B", 5, "ifg", path="/w/B/ifg", kind="IFG", layout="isce2",
                           policy="stat", fp="stat:x:fpB")
    assert fstore.find_artifact("C", "ifg")["fp"] == "stat:x:fpB"
    assert fstore.find_artifact("A", "ifg")["fp"] == "stat:x:fpA"

    # 同 step_id 在父子 run 各自声明:子 run 推进,父 run 纹丝不动
    for rid in ("A", "C"):
        fstore.upsert_step(rid, 6, capability="unwrap", name="解缠",
                           method="snaphu_mcf", params={}, hashes=HASHES)
    fstore.advance("C", 6, "VERIFIED", state="done", run_ok=1, job_dir="/w/C/j6")
    parent_step = fstore.load_step("A", 6)
    assert parent_step.stage == "PENDING" and parent_step.state == "pending"
    assert parent_step.job_dir is None
    assert fstore.load_step("C", 6).stage == "VERIFIED"


def test_fork_cycle_guarded(fstore):
    """谱系成环(异常数据)时 find_artifact 有上限护栏,返回 None 而非死循环。"""
    fstore.create_session("s1", "stress")
    fstore.create_run("X", "s1", workspace="/w", parent_run_id="Y")
    fstore.create_run("Y", "s1", workspace="/w", parent_run_id="X")
    assert fstore.find_artifact("X", "nope") is None


# ---------------- 第二连接:busy_timeout 兜底,不 "database is locked" ----------------


def test_second_connection_writes_with_busy_timeout(tmp_path):
    """同库第二连接(备份/巡检工具形态)与主连接并发写:busy_timeout 生效,
    全程无 sqlite3.OperationalError,行数分毫不差。"""
    path = tmp_path / "dual.db"
    a, b = Store(Database(path)), Store(Database(path))
    try:
        for s in (a, b):
            assert s.db.query_one("PRAGMA busy_timeout")[0] == 5000
        a.create_session("s1", "dual")
        n = 150

        def write(i: int) -> None:
            s = a if i == 0 else b
            for k in range(n):
                s.append_trace(session_id="s1", phase=f"conn{i}", thought=f"t{k}")

        _, errors = _race(2, write, timeout=60.0)
        locked = [e for e in errors if isinstance(e, sqlite3.OperationalError)]
        assert locked == [], f"busy_timeout 未兜住写锁竞争: {locked}"
        assert errors == [None, None]
        assert a.db.query_one("SELECT COUNT(*) AS n FROM trace")["n"] == 2 * n
    finally:
        a.close()
        b.close()


# ---------------- pragma 与索引迁移 ----------------


def test_file_db_pragmas(tmp_path):
    """文件库四项 pragma:WAL(崩溃安全+读写并行)、synchronous=NORMAL(WAL 推荐档,
    每提交不再 fsync)、busy_timeout=5000(显式固定)、foreign_keys=ON。"""
    db = Database(tmp_path / "p.db")
    try:
        assert db.query_one("PRAGMA journal_mode")[0] == "wal"
        assert db.query_one("PRAGMA synchronous")[0] == 1  # NORMAL
        assert db.query_one("PRAGMA busy_timeout")[0] == 5000
        assert db.query_one("PRAGMA foreign_keys")[0] == 1
    finally:
        db.close()


def test_index_migration_on_reopen(tmp_path):
    """旧库重开自动收敛到目标索引集:缺的补(schema.sql 幂等重放),
    冗余的删(_STATEMENT_MIGRATIONS:idx_steps_run 与主键 autoindex 重复)。"""
    path = tmp_path / "m.db"
    db = Database(path)
    with db.tx() as cur:  # 手工做旧:删新索引、造回冗余索引
        cur.execute("DROP INDEX IF EXISTS idx_runs_session")
        cur.execute("DROP INDEX IF EXISTS idx_steps_cap_method")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_steps_run ON steps(run_id)")
    db.close()

    db = Database(path)
    try:
        names = {r["name"] for r in db.query(
            "SELECT name FROM sqlite_master WHERE type='index'"
            " AND name NOT LIKE 'sqlite_%'")}
        assert {"idx_runs_session", "idx_steps_cap_method"} <= names
        assert "idx_steps_run" not in names
    finally:
        db.close()
