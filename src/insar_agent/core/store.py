"""状态机 + 幂等守卫(AGENT-DESIGN §4.3/§6.1)。

纪律(吸收 pi harness,PI_FRAMEWORK_ANALYSIS absorb-E2/E6):
  - stage 是单调高水位:advance 用乐观并发(UPDATE ... WHERE stage=当前),
    外部终结者与执行器竞争时,输者发现行已变,自停(§4.9 外部终结)。
  - 恢复 = 主键点查,绝不扫描表推断状态。
  - 命令两段式:reserve_command 落意图(预留 id),settle_command 落结算。
  - 每个事务提交后才算数;部分成果(日志偏移/产物)随时落盘(§1.6)。
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from insar_agent.core.db import Database
from insar_agent.core.fingerprint import RECORD_VERSION

# 阶段序(§4.3):PREPARED<LAUNCHED<RUNNING<COLLECTED<VERIFIED;
# FAILED/INTERRUPTED 终态不占 stage 位 —— stage 是单调高水位,终态走 state 列
# (+failure_class),这样恢复时已达阶段的守卫判断不被失败态污染。
STAGES = ("PENDING", "PREPARED", "LAUNCHED", "RUNNING", "COLLECTED", "VERIFIED")
_STAGE_IDX = {s: i for i, s in enumerate(STAGES)}

TERMINAL_BAD = ("failed", "interrupted", "orphaned")

DELIVER_AS = ("steer", "follow_up", "next_run")  # 投递语义(§4.5/absorb-E4)


def new_run_id(prefix: str = "") -> str:
    """UUID + 单调时间戳(DESIGN.md:639:不能靠目录名)。"""
    ts = time.strftime("%Y%m%dT%H%M%S")
    return f"{ts}-{uuid.uuid4().hex[:8]}" + (f"-{prefix}" if prefix else "")


class StageConflict(RuntimeError):
    """乐观并发失败:行已被别人推进/终结(absorb-E6)。"""


@dataclass
class StepRow:
    run_id: str
    step_id: int
    capability: str
    name: str
    method: str
    params: dict
    task_hash: str
    args_hash: str
    local_hash: str
    eval_hash: str
    record_version: int
    stage: str
    state: str
    stale: bool
    stale_reason: str | None
    failure_class: str | None
    replay: str
    job_dir: str | None
    command_id: int | None
    log_path: str | None
    log_offset: int
    exit_code: int | None
    run_ok: int | None
    qa: list | None
    config_hash: str | None
    started_at: float | None
    ended_at: float | None

    def stage_lt(self, stage: str) -> bool:
        return _STAGE_IDX[self.stage] < _STAGE_IDX[stage]

    def stage_ge(self, stage: str) -> bool:
        return _STAGE_IDX[self.stage] >= _STAGE_IDX[stage]


def _row_to_step(r) -> StepRow:
    return StepRow(
        run_id=r["run_id"], step_id=r["step_id"], capability=r["capability"], name=r["name"],
        method=r["method"], params=json.loads(r["params"]),
        task_hash=r["task_hash"], args_hash=r["args_hash"], local_hash=r["local_hash"],
        eval_hash=r["eval_hash"], record_version=r["record_version"],
        stage=r["stage"], state=r["state"], stale=bool(r["stale"]),
        stale_reason=r["stale_reason"], failure_class=r["failure_class"], replay=r["replay"],
        job_dir=r["job_dir"], command_id=r["command_id"], log_path=r["log_path"],
        log_offset=r["log_offset"], exit_code=r["exit_code"], run_ok=r["run_ok"],
        qa=json.loads(r["qa"]) if r["qa"] else None, config_hash=r["config_hash"],
        started_at=r["started_at"], ended_at=r["ended_at"],
    )


class Store:
    def __init__(self, db: Database | str | Path):
        # 直接给路径时自建连接:初始化即执行 schema.sql,WAL 模式(db.py)
        self.db = db if isinstance(db, Database) else Database(db)

    def close(self) -> None:
        self.db.close()

    # ---------------- sessions / chat ----------------

    def create_session(self, session_id: str, name: str, *, mode: str = "expert",
                       scenario: str | None = None, meta: dict | None = None) -> None:
        with self.db.tx() as cur:
            cur.execute(
                "INSERT OR IGNORE INTO sessions(session_id,name,created_at,mode,scenario,meta)"
                " VALUES (?,?,?,?,?,?)",
                (session_id, name, time.time(), mode, scenario, json.dumps(meta or {})),
            )

    def list_sessions(self) -> list[dict]:
        return [dict(r) for r in self.db.query(
            "SELECT * FROM sessions ORDER BY created_at DESC")]

    def get_session(self, session_id: str) -> dict | None:
        r = self.db.query_one("SELECT * FROM sessions WHERE session_id=?", (session_id,))
        return dict(r) if r else None

    def set_session_mode(self, session_id: str, mode: str) -> None:
        with self.db.tx() as cur:
            cur.execute("UPDATE sessions SET mode=? WHERE session_id=?", (mode, session_id))

    def append_chat(self, session_id: str, role: str, content: str,
                    meta: dict | None = None) -> int:
        with self.db.tx() as cur:
            cur.execute(
                "INSERT INTO chat_messages(session_id,created_at,role,content,meta)"
                " VALUES (?,?,?,?,?)",
                (session_id, time.time(), role, content, json.dumps(meta or {})),
            )
            return int(cur.lastrowid)

    def chat_history(self, session_id: str, limit: int = 200) -> list[dict]:
        rows = self.db.query(
            "SELECT * FROM chat_messages WHERE session_id=? ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        )
        return [dict(r) for r in reversed(rows)]

    # ---------------- runs ----------------

    def create_run(self, run_id: str, session_id: str, *, workspace: str,
                   intent: dict | None = None, scenario: str | None = None,
                   parent_run_id: str | None = None, simulated: bool = False,
                   tool_versions: dict | None = None, env_hash: str | None = None,
                   git_head: str | None = None, git_dirty: bool | None = None,
                   agent_hash: str | None = None) -> None:
        with self.db.tx() as cur:
            cur.execute(
                "INSERT INTO runs(run_id,session_id,parent_run_id,created_at,status,intent,"
                "scenario,workspace,simulated,env_hash,tool_versions,git_head,git_dirty,agent_hash)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, session_id, parent_run_id, time.time(), "planning",
                 json.dumps(intent or {}), scenario, workspace, int(simulated), env_hash,
                 json.dumps(tool_versions or {}), git_head,
                 None if git_dirty is None else int(git_dirty), agent_hash),
            )

    def get_run(self, run_id: str) -> dict | None:
        r = self.db.query_one("SELECT * FROM runs WHERE run_id=?", (run_id,))
        return dict(r) if r else None

    def list_runs(self, session_id: str | None = None) -> list[dict]:
        if session_id:
            rows = self.db.query(
                "SELECT * FROM runs WHERE session_id=? ORDER BY created_at DESC", (session_id,))
        else:
            rows = self.db.query("SELECT * FROM runs ORDER BY created_at DESC")
        return [dict(r) for r in rows]

    def set_run_status(self, run_id: str, status: str) -> None:
        with self.db.tx() as cur:
            cur.execute("UPDATE runs SET status=? WHERE run_id=?", (status, run_id))

    # ---- 取消 control 位(absorb-E3,pi harness「abort 是 control 位不是状态」) ----

    def request_cancel(self, run_id: str) -> None:
        """落盘取消意图(幂等)。control 位与 status 分离:已在途的作业照常结算,
        只禁止发起新效果;服务重启后意图不丢 —— resume/execute 读到
        cancel_requested 就不再启动新步骤,直接把 run 收尾为 interrupted。"""
        with self.db.tx() as cur:
            cur.execute("UPDATE runs SET control='cancel_requested' WHERE run_id=?",
                        (run_id,))

    def clear_cancel(self, run_id: str) -> None:
        """取消意图已兑现(run 已收尾 interrupted):control 复位 running,
        之后用户显式重跑不再被旧意图拦截。"""
        with self.db.tx() as cur:
            cur.execute("UPDATE runs SET control='running' WHERE run_id=?", (run_id,))

    def cancel_requested(self, run_id: str) -> bool:
        r = self.db.query_one("SELECT control FROM runs WHERE run_id=?", (run_id,))
        return bool(r) and r["control"] == "cancel_requested"

    def latest_run(self, session_id: str) -> dict | None:
        r = self.db.query_one(
            "SELECT * FROM runs WHERE session_id=? ORDER BY created_at DESC LIMIT 1",
            (session_id,))
        return dict(r) if r else None

    # ---------------- steps ----------------

    def upsert_step(self, run_id: str, step_id: int, *, capability: str, name: str,
                    method: str, params: dict, hashes: dict, replay: str = "never",
                    state: str = "pending") -> None:
        """声明步骤(幂等):重复 upsert 只更新声明(方法/参数/指纹),
        保留执行状态(stage/state/日志偏移)—— 计划可重构,执行历史不丢。
        params 存 canonical JSON(键排序,absorb-M)。
        """
        with self.db.tx() as cur:
            cur.execute(
                "INSERT INTO steps(run_id,step_id,capability,name,method,params,"
                "task_hash,args_hash,local_hash,eval_hash,record_version,stage,state,replay)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(run_id,step_id) DO UPDATE SET"
                " capability=excluded.capability, name=excluded.name, method=excluded.method,"
                " params=excluded.params, task_hash=excluded.task_hash,"
                " args_hash=excluded.args_hash, local_hash=excluded.local_hash,"
                " eval_hash=excluded.eval_hash, record_version=excluded.record_version,"
                " replay=excluded.replay",
                (run_id, step_id, capability, name, method,
                 json.dumps(params, sort_keys=True, ensure_ascii=False),
                 hashes["task_hash"], hashes["args_hash"], hashes["local_hash"],
                 hashes["eval_hash"], RECORD_VERSION, "PENDING", state, replay),
            )

    # 兼容旧名(planner 等调用点);语义同 upsert_step
    create_step = upsert_step

    def add_edge(self, run_id: str, parent: int, child: int) -> None:
        with self.db.tx() as cur:
            cur.execute("INSERT OR IGNORE INTO edges(run_id,parent,child) VALUES (?,?,?)",
                        (run_id, parent, child))

    def edges(self, run_id: str) -> list[tuple[int, int]]:
        return [(r["parent"], r["child"]) for r in
                self.db.query("SELECT parent,child FROM edges WHERE run_id=?", (run_id,))]

    def load_step(self, run_id: str, step_id: int) -> StepRow | None:
        r = self.db.query_one(
            "SELECT * FROM steps WHERE run_id=? AND step_id=?", (run_id, step_id))
        return _row_to_step(r) if r else None

    def load_steps(self, run_id: str) -> list[StepRow]:
        rows = self.db.query(
            "SELECT * FROM steps WHERE run_id=? ORDER BY step_id", (run_id,))
        return [_row_to_step(r) for r in rows]

    def advance(self, run_id: str, step_id: int, to_stage: str, *,
                expect_stage: str | None = None, state: str | None = None,
                **fields: Any) -> bool:
        """阶段推进(单调 + 幂等守卫 + 乐观并发)。返回是否真的推进了。

        幂等守卫(aiida tasks.py:137-140 的守卫模式):重复推进到已达(或已越过)
        的阶段直接跳过并返回 False,字段不写 —— 恢复重放同一序列时天然无害。
        expect_stage 提供时,行必须仍处于该 stage,否则 StageConflict(absorb-E6:
        外部终结者可能已经改写;竞争的输者必须自停,不能覆写)。
        """
        if to_stage not in _STAGE_IDX:
            raise ValueError(f"unknown stage {to_stage}")
        sets = ["stage=?"]
        vals: list[Any] = [to_stage]
        if state is not None:
            sets.append("state=?")
            vals.append(state)
        for key, value in fields.items():
            if key in ("params", "qa"):
                value = json.dumps(value)
            sets.append(f"{key}=?")
            vals.append(value)
        with self.db.tx() as cur:
            row = cur.execute(
                "SELECT stage FROM steps WHERE run_id=? AND step_id=?",
                (run_id, step_id)).fetchone()
            if row is None:
                raise StageConflict(f"step {run_id}/{step_id}: not found")
            current = row["stage"]
            if expect_stage is not None and current != expect_stage:
                raise StageConflict(
                    f"step {run_id}/{step_id}: expected stage {expect_stage}, found {current}")
            if _STAGE_IDX[to_stage] <= _STAGE_IDX[current]:
                return False  # 已达阶段:幂等跳过,不覆写
            # CAS:以读到的 stage 为条件更新;外部终结者若已改行,这里 rowcount=0 → 自停
            cur.execute(
                f"UPDATE steps SET {', '.join(sets)} WHERE run_id=? AND step_id=? AND stage=?",
                tuple(vals) + (run_id, step_id, current))
            if cur.rowcount == 0:
                raise StageConflict(
                    f"step {run_id}/{step_id}: row changed concurrently")
            return True

    def should_skip(self, run_id: str, step_id: int,
                    eval_hash: str) -> tuple[bool, str | None]:
        """幂等重放判定:此步能否整体跳过(Phase 0 验收「第二次全跳过」的判据)。

        跳过条件(全部满足):记录存在 且 stage==VERIFIED 且 run_ok==1
        且 eval_hash 未变 且 record_version==当前值 且未被标脏。
        判定序对齐 absorb-N 的短路序:先版本门控(snakemake:低于阈值判
        「不可比」而非「可复用」),再内存哈希比对;IO 指纹复核在
        stale.verify_artifacts,不在此处。
        返回 (skip, reason):skip=False 时 reason 给出不可跳过的原因。
        """
        step = self.load_step(run_id, step_id)
        if step is None:
            return False, "no_metadata"
        if step.record_version != RECORD_VERSION:
            return False, "outdated_metadata"
        if step.stage != "VERIFIED":
            return False, "not_verified"
        if step.run_ok != 1:
            return False, "run_ok_failed"
        if step.eval_hash != eval_hash:
            return False, "eval_hash_changed"
        if step.stale:
            return False, step.stale_reason or "stale"
        return True, None

    def mark_step(self, run_id: str, step_id: int, *, state: str,
                  failure_class: str | None = None, expect_stage: str | None = None,
                  **fields: Any) -> None:
        """终态/中间态标记(不推进 stage)。

        expect_stage 提供时做 CAS(absorb-E6 外部终结):行必须仍处于该 stage,
        否则 StageConflict —— 活跃执行器若已推进,终结者是竞争的输者,必须自停,
        绝不覆写别人的推进。
        """
        sets = ["state=?"]
        vals: list[Any] = [state]
        if failure_class is not None:
            sets.append("failure_class=?")
            vals.append(failure_class)
        for key, value in fields.items():
            if key in ("params", "qa"):
                value = json.dumps(value)
            sets.append(f"{key}=?")
            vals.append(value)
        with self.db.tx() as cur:
            if expect_stage is None:
                cur.execute(
                    f"UPDATE steps SET {', '.join(sets)} WHERE run_id=? AND step_id=?",
                    tuple(vals) + (run_id, step_id))
            else:
                cur.execute(
                    f"UPDATE steps SET {', '.join(sets)}"
                    " WHERE run_id=? AND step_id=? AND stage=?",
                    tuple(vals) + (run_id, step_id, expect_stage))
                if cur.rowcount == 0:
                    raise StageConflict(
                        f"step {run_id}/{step_id}: stage moved past {expect_stage},"
                        " refusing to overwrite")

    def set_step_config(self, run_id: str, step_id: int, *, method: str, params: dict,
                        hashes: dict) -> None:
        """改方法/参数后写回配置与指纹(stale 标记由 core/stale.py 负责)。"""
        with self.db.tx() as cur:
            cur.execute(
                "UPDATE steps SET method=?, params=?, task_hash=?, args_hash=?, local_hash=?,"
                " eval_hash=? WHERE run_id=? AND step_id=?",
                (method, json.dumps(params), hashes["task_hash"], hashes["args_hash"],
                 hashes["local_hash"], hashes["eval_hash"], run_id, step_id))

    def set_stale(self, run_id: str, step_id: int, stale: bool, reason: str | None) -> None:
        with self.db.tx() as cur:
            cur.execute(
                "UPDATE steps SET stale=?, stale_reason=?,"
                " state=CASE WHEN ? AND state='done' THEN 'stale' ELSE state END"
                " WHERE run_id=? AND step_id=?",
                (int(stale), reason, int(stale), run_id, step_id))

    def reset_step_for_rerun(self, run_id: str, step_id: int) -> None:
        """重跑前复位:stage 回 PENDING,保留旧产物记录(将被覆写)。"""
        with self.db.tx() as cur:
            cur.execute(
                "UPDATE steps SET stage='PENDING', state='pending', stale=0, stale_reason=NULL,"
                " failure_class=NULL, exit_code=NULL, run_ok=NULL, qa=NULL, job_dir=NULL,"
                " command_id=NULL, log_offset=0, started_at=NULL, ended_at=NULL"
                " WHERE run_id=? AND step_id=?",
                (run_id, step_id))

    def update_log_offset(self, run_id: str, step_id: int, offset: int) -> None:
        with self.db.tx() as cur:
            cur.execute("UPDATE steps SET log_offset=? WHERE run_id=? AND step_id=?",
                        (offset, run_id, step_id))

    # ---------------- commands(意图/结算两段式) ----------------

    def reserve_command(self, run_id: str, step_id: int, argv: list[str], *,
                        cwd: str | None = None, cmd_path: str | None = None,
                        stdout_path: str | None = None, attempt: int = 1,
                        env_delta: dict | None = None) -> int:
        """意图落盘:命令即将执行,exit_code 为 NULL(absorb-E2 预留 id)。

        崩溃重入守卫:同 (run,step) 已有「同 argv、未结算」的意图行时,复用其
        预留 id 并刷新落点(cwd/路径/attempt/created_at),不再新插。
        触发场景:执行器在 reserve 之后、launch 前崩溃 —— 恢复路径查
        latest_unsettled_command 发现作业从未启动、不认领,重走 reserve;
        旧行若不复用会永远悬挂为未结算(假的"在途命令"),且 attempt 取
        COUNT(commands)+1(executor.py),每次崩溃循环都再涨一格。
        刷新(而非只返回 id)是必要的:恢复时的认领判定读该行 stdout_path
        找作业目录,落点必须指向本次真实要启动的目录。
        argv 变了(重规划)则照常新插 —— 那是另一条意图。
        """
        argv_json = json.dumps(argv)
        with self.db.tx() as cur:
            row = cur.execute(
                "SELECT id FROM commands WHERE run_id=? AND step_id=? AND argv=?"
                " AND exit_code IS NULL ORDER BY id DESC LIMIT 1",
                (run_id, step_id, argv_json)).fetchone()
            if row is not None:
                cur.execute(
                    "UPDATE commands SET cwd=?, env_delta=?, cmd_path=?, stdout_path=?,"
                    " attempt=?, created_at=? WHERE id=?",
                    (cwd, json.dumps(env_delta or {}), cmd_path, stdout_path,
                     attempt, time.time(), row["id"]))
                return int(row["id"])
            cur.execute(
                "INSERT INTO commands(run_id,step_id,argv,cwd,env_delta,cmd_path,exit_code,"
                "duration,stdout_path,attempt,created_at) VALUES (?,?,?,?,?,?,NULL,NULL,?,?,?)",
                (run_id, step_id, argv_json, cwd,
                 json.dumps(env_delta or {}), cmd_path, stdout_path, attempt, time.time()))
            return int(cur.lastrowid)

    def settle_command(self, command_id: int, *, exit_code: int, duration: float) -> None:
        """结算:只在真正结束时写入。绝不重结算(WHERE exit_code IS NULL)。"""
        with self.db.tx() as cur:
            cur.execute(
                "UPDATE commands SET exit_code=?, duration=? WHERE id=? AND exit_code IS NULL",
                (exit_code, duration, command_id))

    def record_command(self, run_id: str, step_id: int, argv: list[str], *,
                       exit_code: int, duration: float | None = None,
                       cwd: str | None = None, cmd_path: str | None = None,
                       stdout_path: str | None = None, attempt: int = 1,
                       env_delta: dict | None = None) -> int:
        """一步式落盘已结束的命令(意图+结算同事务;执行器走两段式,这是简用入口)。"""
        with self.db.tx() as cur:
            cur.execute(
                "INSERT INTO commands(run_id,step_id,argv,cwd,env_delta,cmd_path,exit_code,"
                "duration,stdout_path,attempt,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, step_id, json.dumps(argv), cwd, json.dumps(env_delta or {}),
                 cmd_path, exit_code, duration, stdout_path, attempt, time.time()))
            return int(cur.lastrowid)

    def latest_unsettled_command(self, run_id: str, step_id: int) -> dict | None:
        """最近一条「意图已落盘、尚未结算」的命令(恢复时认领预留 id,absorb-E2)。"""
        r = self.db.query_one(
            "SELECT * FROM commands WHERE run_id=? AND step_id=? AND exit_code IS NULL"
            " ORDER BY id DESC LIMIT 1", (run_id, step_id))
        return dict(r) if r else None

    def command_attempts(self, run_id: str, step_id: int) -> int:
        r = self.db.query_one(
            "SELECT COUNT(*) AS n FROM commands WHERE run_id=? AND step_id=?",
            (run_id, step_id))
        return int(r["n"]) if r else 0

    def commands_of(self, run_id: str, step_id: int | None = None) -> list[dict]:
        if step_id is None:
            rows = self.db.query(
                "SELECT * FROM commands WHERE run_id=? ORDER BY id", (run_id,))
        else:
            rows = self.db.query(
                "SELECT * FROM commands WHERE run_id=? AND step_id=? ORDER BY id",
                (run_id, step_id))
        return [dict(r) for r in rows]

    def duration_history(self, capability: str, method: str, limit: int = 10) -> list[float]:
        """跨 run 的历史耗时(供预估;样本 <3 一律「未知」,§7.5)。"""
        rows = self.db.query(
            "SELECT c.duration FROM commands c JOIN steps s"
            " ON c.run_id=s.run_id AND c.step_id=s.step_id"
            " WHERE s.capability=? AND s.method=? AND c.exit_code=0 AND c.duration IS NOT NULL"
            " ORDER BY c.id DESC LIMIT ?",
            (capability, method, limit))
        return [float(r["duration"]) for r in rows]

    # ---------------- artifacts ----------------

    def record_artifact(self, run_id: str, step_id: int, art_id: str, *, path: str,
                        kind: str, layout: str, policy: str, fp: str,
                        size: int | None = None, mtime_ns: int | None = None) -> None:
        """fp 是三段编码 "<policy>:<algo>:<digest>"(filehash.fingerprint 的输出)。"""
        with self.db.tx() as cur:
            cur.execute(
                "INSERT INTO artifacts(run_id,step_id,art_id,path,kind,layout,policy,fp,"
                "record_version,size,mtime_ns) VALUES (?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(run_id,step_id,art_id) DO UPDATE SET"
                " path=excluded.path, fp=excluded.fp, record_version=excluded.record_version,"
                " size=excluded.size, mtime_ns=excluded.mtime_ns",
                (run_id, step_id, art_id, path, kind, layout, policy, fp,
                 RECORD_VERSION, size, mtime_ns))

    def artifacts_of(self, run_id: str, step_id: int | None = None) -> list[dict]:
        if step_id is None:
            rows = self.db.query("SELECT * FROM artifacts WHERE run_id=?", (run_id,))
        else:
            rows = self.db.query(
                "SELECT * FROM artifacts WHERE run_id=? AND step_id=?", (run_id, step_id))
        return [dict(r) for r in rows]

    def find_artifact(self, run_id: str, art_id: str) -> dict | None:
        """先查本 run,再沿 parent_run_id 祖先链查(run fork 产物复用,absorb-E5)。"""
        current: str | None = run_id
        for _ in range(32):  # 防环
            if current is None:
                return None
            r = self.db.query_one(
                "SELECT * FROM artifacts WHERE run_id=? AND art_id=?", (current, art_id))
            if r:
                return dict(r)
            run = self.get_run(current)
            current = run.get("parent_run_id") if run else None
        return None

    # ---------------- metrics ----------------

    def record_metric(self, run_id: str, name: str, *, value: float | None, unit: str = "",
                      source_artifact: str = "", source_field: str = "",
                      reparsed_ok: bool | None = None) -> None:
        with self.db.tx() as cur:
            cur.execute(
                "INSERT INTO metrics(run_id,name,value,unit,source_artifact,source_field,reparsed_ok)"
                " VALUES (?,?,?,?,?,?,?)"
                " ON CONFLICT(run_id,name) DO UPDATE SET value=excluded.value,"
                " reparsed_ok=excluded.reparsed_ok",
                (run_id, name, value, unit, source_artifact, source_field,
                 None if reparsed_ok is None else int(reparsed_ok)))

    def metrics_of(self, run_id: str) -> list[dict]:
        return [dict(r) for r in self.db.query(
            "SELECT * FROM metrics WHERE run_id=?", (run_id,))]

    # ---------------- pending_actions(干预队列) ----------------

    def push_action(self, *, scope: str, target: str, action: str,
                    payload: dict | None = None, deliver_as: str = "steer",
                    run_id: str | None = None) -> int:
        if deliver_as not in DELIVER_AS:
            raise ValueError(f"unknown deliver_as {deliver_as}")
        with self.db.tx() as cur:
            cur.execute(
                "INSERT INTO pending_actions(created_at,run_id,scope,target,action,payload,deliver_as)"
                " VALUES (?,?,?,?,?,?,?)",
                (time.time(), run_id, scope, target, action,
                 json.dumps(payload or {}), deliver_as))
            return int(cur.lastrowid)

    def due_actions(self, deliver_as: str, run_id: str | None = None,
                    include_unattributed: bool = True) -> list[dict]:
        """未消费动作;run_id 给定时只取"该 run 的(+ 可选未定向 NULL)"的动作。

        include_unattributed=True 兼容旧数据与无 run 语义的投递(next_run 面向
        未来 run,天然 NULL);steer/follow_up 的执行期消费应传 False —— NULL 行
        会被任意 run 的 driver 吞掉(REVIEW-r2 P1-3:A 会话首个 run 规划前排队
        的动作被 B 消费;API 入队已绑定 run,严格匹配不再丢合法投递)。
        """
        if run_id is None:
            rows = self.db.query(
                "SELECT * FROM pending_actions WHERE consumed_at IS NULL AND deliver_as=?"
                " ORDER BY id", (deliver_as,))
        elif include_unattributed:
            rows = self.db.query(
                "SELECT * FROM pending_actions WHERE consumed_at IS NULL AND deliver_as=?"
                " AND (run_id=? OR run_id IS NULL) ORDER BY id", (deliver_as, run_id))
        else:
            rows = self.db.query(
                "SELECT * FROM pending_actions WHERE consumed_at IS NULL AND deliver_as=?"
                " AND run_id=? ORDER BY id", (deliver_as, run_id))
        out = []
        for r in rows:
            d = dict(r)
            d["payload"] = json.loads(d["payload"] or "{}")
            out.append(d)
        return out

    def consume_action(self, action_id: int) -> bool:
        with self.db.tx() as cur:
            cur.execute(
                "UPDATE pending_actions SET consumed_at=? WHERE id=? AND consumed_at IS NULL",
                (time.time(), action_id))
            return cur.rowcount > 0

    def consume_actions(self, deliver_as: str, run_id: str | None = None) -> list[dict]:
        """整批出队:取出该投递语义下全部未消费动作并标记消费(单事务原子)。

        driver 主循环用「due_actions → 应用 → consume_action」逐条消费
        (应用失败不吞动作);这里是批量场景(如 run 结束统一收 follow_up)。
        run_id 过滤语义同 due_actions(含 NULL 兼容行)。
        """
        with self.db.tx() as cur:
            if run_id is None:
                rows = cur.execute(
                    "SELECT * FROM pending_actions WHERE consumed_at IS NULL AND deliver_as=?"
                    " ORDER BY id", (deliver_as,)).fetchall()
            else:
                rows = cur.execute(
                    "SELECT * FROM pending_actions WHERE consumed_at IS NULL AND deliver_as=?"
                    " AND (run_id=? OR run_id IS NULL) ORDER BY id",
                    (deliver_as, run_id)).fetchall()
            now = time.time()
            out = []
            for r in rows:
                d = dict(r)
                d["payload"] = json.loads(d["payload"] or "{}")
                cur.execute("UPDATE pending_actions SET consumed_at=? WHERE id=?",
                            (now, d["id"]))
                out.append(d)
            return out

    def edit_action(self, action_id: int, *, payload: dict | None = None,
                    deliver_as: str | None = None) -> bool:
        """编辑排队中的动作(§4.5:consumed_at IS NULL 才可改;已消费返回 False)。"""
        if deliver_as is not None and deliver_as not in DELIVER_AS:
            raise ValueError(f"unknown deliver_as {deliver_as}")
        sets, vals = [], []
        if payload is not None:
            sets.append("payload=?")
            vals.append(json.dumps(payload))
        if deliver_as is not None:
            sets.append("deliver_as=?")
            vals.append(deliver_as)
        if not sets:
            return False
        with self.db.tx() as cur:
            cur.execute(
                f"UPDATE pending_actions SET {', '.join(sets)}"
                " WHERE id=? AND consumed_at IS NULL",
                tuple(vals) + (action_id,))
            return cur.rowcount > 0

    def withdraw_action(self, action_id: int) -> bool:
        """撤回排队中的动作(§4.5:未消费才可撤;已消费返回 False,不可逆)。"""
        with self.db.tx() as cur:
            cur.execute("DELETE FROM pending_actions WHERE id=? AND consumed_at IS NULL",
                        (action_id,))
            return cur.rowcount > 0

    # ---------------- leases(§4.11) ----------------

    def acquire_lease(self, resource: str, holder: str, *, ttl: float = 90.0,
                      stale_after: float = 90.0) -> bool:
        now = time.time()
        with self.db.tx() as cur:
            row = cur.execute("SELECT holder,heartbeat FROM leases WHERE resource=?",
                              (resource,)).fetchone()
            if row is None:
                cur.execute(
                    "INSERT INTO leases(resource,holder,acquired,heartbeat,ttl)"
                    " VALUES (?,?,?,?,?)", (resource, holder, now, now, ttl))
                return True
            if row["holder"] == holder:
                cur.execute("UPDATE leases SET heartbeat=? WHERE resource=?", (now, resource))
                return True
            if now - row["heartbeat"] > stale_after:
                # 持有者心跳超时,抢占(抢占前调用方需先确认作业已死,§4.11)
                cur.execute(
                    "UPDATE leases SET holder=?, acquired=?, heartbeat=? WHERE resource=?",
                    (holder, now, now, resource))
                return True
            return False

    def heartbeat_lease(self, resource: str, holder: str) -> bool:
        with self.db.tx() as cur:
            cur.execute(
                "UPDATE leases SET heartbeat=? WHERE resource=? AND holder=?",
                (time.time(), resource, holder))
            return cur.rowcount > 0

    def release_lease(self, resource: str, holder: str) -> None:
        with self.db.tx() as cur:
            cur.execute("DELETE FROM leases WHERE resource=? AND holder=?", (resource, holder))

    # ---------------- trace(轨迹,OpenDiscoveryTrace 对齐) ----------------

    def append_trace(self, *, run_id: str | None = None, session_id: str | None = None,
                     step_no: int | None = None, phase: str = "", thought: str = "",
                     action: dict | None = None, observation: str = "",
                     error_occurred: bool = False, error_type: str = "",
                     error_message: str = "", revision_trigger: str = "",
                     confidence: float | None = None, raw_response: str = "") -> int:
        # 长字段硬截断(OpenDiscoveryTrace agent_harness.py:377 —— 否则日志撑爆 SQLite)
        thought = thought[:2000]
        observation = observation[:2000]
        raw_response = raw_response[:3000]
        action_json = json.dumps(action or {}, ensure_ascii=False)[:1000]
        with self.db.tx() as cur:
            cur.execute(
                "INSERT INTO trace(run_id,session_id,step_no,ts,phase,thought,action,observation,"
                "error_occurred,error_type,error_message,revision_trigger,confidence,raw_response)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, session_id, step_no, time.time(), phase, thought, action_json,
                 observation, int(error_occurred), error_type, error_message,
                 revision_trigger, confidence, raw_response))
            return int(cur.lastrowid)

    def trace_of(self, run_id: str) -> list[dict]:
        return [dict(r) for r in self.db.query(
            "SELECT * FROM trace WHERE run_id=? ORDER BY id", (run_id,))]
