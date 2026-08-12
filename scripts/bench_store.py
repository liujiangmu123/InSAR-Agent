"""SQLite 性能体检:造数 → 热查询计时 → EXPLAIN QUERY PLAN → PRAGMA 报告。

用法(项目根):
    .venv\\Scripts\\python.exe scripts\\bench_store.py [--runs 200] [--db PATH] [--keep]

规模:默认 200 run × 11 step + 每 step 3 命令 3 指标(≈2 万行),
插入 + 查询全程秒级 —— 轻量脚本,不属重型计算。
临时库默认建在项目内 .bench_tmp/(本机 %TEMP% 有 ACL 问题,见 pyproject basetemp 注)。
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from insar_agent.core.db import Database  # noqa: E402
from insar_agent.core.store import Store  # noqa: E402

# 11 步链(对齐典型 isce2 流水的 capability/method 形状)
CHAIN = [
    ("fetch", "asf_download"), ("orbit", "poeorb"), ("dem", "srtm"),
    ("coreg", "ampcor"), ("ifg", "crossmul"), ("filter", "goldstein"),
    ("coherence", "cchz_wave"), ("unwrap", "snaphu_mcf"), ("geocode", "geo2rdr"),
    ("timeseries", "sbas"), ("report", "html"),
]
N_SESSIONS = 10


def seed(db: Database, n_runs: int, n_cmds: int, n_metrics: int) -> dict:
    """批量造数(raw SQL,几个大事务;INSERT 形状与 store 一致)。"""
    rng = random.Random(42)
    t0 = time.perf_counter()
    now = time.time()
    with db.tx() as cur:
        cur.executemany(
            "INSERT INTO sessions(session_id,name,created_at,mode) VALUES (?,?,?,?)",
            [(f"sess{i}", f"会话{i}", now + i, "expert") for i in range(N_SESSIONS)])
        runs = []
        for i in range(n_runs):
            # 尾部 8 个 run 组成 parent 链(fork 谱系查询的走链场景)
            parent = f"run{i - 1:04d}" if i >= n_runs - 8 and i > 0 else None
            runs.append((f"run{i:04d}", f"sess{i % N_SESSIONS}", parent, now + i,
                         "done", "{}", None, "/ws", 0))
        cur.executemany(
            "INSERT INTO runs(run_id,session_id,parent_run_id,created_at,status,intent,"
            "scenario,workspace,simulated) VALUES (?,?,?,?,?,?,?,?,?)", runs)

        steps, edges, cmds, arts, mets = [], [], [], [], []
        for i in range(n_runs):
            rid = f"run{i:04d}"
            for sid, (cap, method) in enumerate(CHAIN):
                steps.append((rid, sid, cap, f"步骤{sid}", method, "{}",
                              f"t{sid}", f"a{sid}", f"l{sid}", f"e{sid}", 1,
                              "VERIFIED", "done", 0, "never", 1))
                if sid:
                    edges.append((rid, sid - 1, sid))
                for k in range(n_cmds):
                    # 每 step 最后一条命令在 1/3 的 step 上保持未结算(恢复扫描的真实形状)
                    unsettled = (k == n_cmds - 1) and (sid % 3 == 0)
                    cmds.append((rid, sid, json.dumps([method, f"--arg{k}"]), "/w", "{}",
                                 None, None if unsettled else 0,
                                 None if unsettled else rng.uniform(1, 300),
                                 None, k + 1, now + i))
                for k in range(n_metrics):
                    mets.append((rid, f"s{sid:02d}:metric{k}", rng.random(), "", "", "", 1))
                for k in range(2):
                    arts.append((rid, sid, f"art{sid}_{k}", f"/w/{rid}/{sid}/{k}",
                                 "RASTER", "isce2", "stat", f"stat:x:{i}{sid}{k}", 1))
        cur.executemany(
            "INSERT INTO steps(run_id,step_id,capability,name,method,params,task_hash,"
            "args_hash,local_hash,eval_hash,record_version,stage,state,stale,replay,run_ok)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", steps)
        cur.executemany("INSERT INTO edges(run_id,parent,child) VALUES (?,?,?)", edges)
        cur.executemany(
            "INSERT INTO commands(run_id,step_id,argv,cwd,env_delta,cmd_path,exit_code,"
            "duration,stdout_path,attempt,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)", cmds)
        cur.executemany(
            "INSERT INTO metrics(run_id,name,value,unit,source_artifact,source_field,"
            "reparsed_ok) VALUES (?,?,?,?,?,?,?)", mets)
        cur.executemany(
            "INSERT INTO artifacts(run_id,step_id,art_id,path,kind,layout,policy,fp,"
            "record_version) VALUES (?,?,?,?,?,?,?,?,?)", arts)
        # 干预队列:300 待消费 + 3000 已消费(索引需在大量已消费行里挑出待消费)
        cur.executemany(
            "INSERT INTO pending_actions(created_at,scope,target,action,payload,deliver_as,"
            "consumed_at) VALUES (?,?,?,?,?,?,?)",
            [(now, "run", f"run{i % n_runs:04d}", "PAUSE", "{}",
              ("steer", "follow_up", "next_run")[i % 3],
              None if i < 300 else now) for i in range(3300)])
    return {"seed_s": time.perf_counter() - t0,
            "rows": {"runs": len(runs), "steps": len(steps), "commands": len(cmds),
                     "metrics": len(mets), "artifacts": len(arts), "actions": 3300}}


def bench(name: str, fn, n: int, results: list) -> None:
    fn()  # 预热(page cache / 语句编译)
    t0 = time.perf_counter()
    for _ in range(n):
        out = fn()
    total = time.perf_counter() - t0
    size = len(out) if isinstance(out, (list, dict)) else out
    results.append((name, n, total * 1000, total * 1000 / n, size))


# 热查询的原始 SQL(与 store.py 逐字一致,用于 EXPLAIN QUERY PLAN)
HOT_SQL = {
    "load_steps": (
        "SELECT * FROM steps WHERE run_id=? ORDER BY step_id", ("run0100",)),
    "latest_unsettled_command": (
        "SELECT * FROM commands WHERE run_id=? AND step_id=? AND exit_code IS NULL"
        " ORDER BY id DESC LIMIT 1", ("run0100", 6)),
    "duration_history": (
        "SELECT c.duration FROM commands c JOIN steps s"
        " ON c.run_id=s.run_id AND c.step_id=s.step_id"
        " WHERE s.capability=? AND s.method=? AND c.exit_code=0 AND c.duration IS NOT NULL"
        " ORDER BY c.id DESC LIMIT ?", ("unwrap", "snaphu_mcf", 10)),
    "due_actions": (
        "SELECT * FROM pending_actions WHERE consumed_at IS NULL AND deliver_as=?"
        " ORDER BY id", ("steer",)),
    "list_runs(session)": (
        "SELECT * FROM runs WHERE session_id=? ORDER BY created_at DESC", ("sess3",)),
    "latest_run(session)": (
        "SELECT * FROM runs WHERE session_id=? ORDER BY created_at DESC LIMIT 1",
        ("sess3",)),
    "find_artifact(单跳)": (
        "SELECT * FROM artifacts WHERE run_id=? AND art_id=?", ("run0100", "art5_0")),
    "commands_of": (
        "SELECT * FROM commands WHERE run_id=? ORDER BY id", ("run0100",)),
    "metrics_of": (
        "SELECT * FROM metrics WHERE run_id=?", ("run0100",)),
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runs", type=int, default=200)
    ap.add_argument("--cmds", type=int, default=3)
    ap.add_argument("--metrics", type=int, default=3)
    ap.add_argument("--db", type=Path, default=None, help="库文件路径(默认 .bench_tmp/)")
    ap.add_argument("--keep", action="store_true", help="跑完保留库文件")
    args = ap.parse_args()

    tmp_dir = Path(".bench_tmp")
    db_path = args.db or tmp_dir / f"bench_{int(time.time())}.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    db = Database(db_path)
    store = Store(db)
    rng = random.Random(7)

    info = seed(db, args.runs, args.cmds, args.metrics)
    print(f"== 造数 == {info['rows']}({info['seed_s'] * 1000:.0f} ms)")
    print(f"库文件: {db_path}")

    # ---- PRAGMA 现状 ----
    print("\n== PRAGMA ==")
    for p in ("journal_mode", "synchronous", "busy_timeout", "foreign_keys",
              "wal_autocheckpoint"):
        print(f"  {p:18s} = {db.query_one(f'PRAGMA {p}')[0]}")

    # ---- 热查询计时 ----
    results: list = []
    run_ids = [f"run{i:04d}" for i in range(args.runs)]

    def panorama():
        rid = rng.choice(run_ids)
        store.get_run(rid)
        steps = store.load_steps(rid)
        store.edges(rid)
        store.commands_of(rid)
        store.artifacts_of(rid)
        store.metrics_of(rid)
        return steps

    bench("load run 全景(6 查询)", panorama, 50, results)
    bench("latest_unsettled_command",
          lambda: store.latest_unsettled_command(rng.choice(run_ids),
                                                 rng.randrange(len(CHAIN))) or {},
          500, results)
    bench("duration_history(命中)",
          lambda: store.duration_history("unwrap", "snaphu_mcf"), 200, results)
    bench("duration_history(落空=最坏)",
          lambda: store.duration_history("unwrap", "no_such_method"), 200, results)
    bench("due_actions(steer)", lambda: store.due_actions("steer"), 200, results)
    bench("list_runs(session)", lambda: store.list_runs("sess3"), 200, results)
    bench("latest_run(session)", lambda: store.latest_run("sess3") or {}, 500, results)
    bench("find_artifact(走 8 级祖先链)",
          lambda: store.find_artifact(f"run{args.runs - 1:04d}", "art_missing") or {},
          200, results)

    # 写路径样本:典型「每阶段一事务」的推进(synchronous 对此最敏感)
    def advance_sample():
        rid = f"w{rng.randrange(10 ** 9):09d}"
        store.create_run(rid, "sess0", workspace="/w")
        store.upsert_step(rid, 0, capability="unwrap", name="x", method="m", params={},
                          hashes={"task_hash": "t", "args_hash": "a",
                                  "local_hash": "l", "eval_hash": "e"})
        for st in ("PREPARED", "LAUNCHED", "RUNNING", "COLLECTED", "VERIFIED"):
            store.advance(rid, 0, st)
        return 7  # 7 个事务

    bench("写路径:1 run 7 事务推进", advance_sample, 30, results)

    print("\n== 热查询计时 ==")
    print(f"  {'查询':38s} {'次数':>5s} {'总耗时ms':>9s} {'均值ms':>8s} {'行数':>5s}")
    for name, n, total, per, size in results:
        print(f"  {name:38s} {n:5d} {total:9.1f} {per:8.3f} {size:5d}")

    # ---- EXPLAIN QUERY PLAN ----
    print("\n== EXPLAIN QUERY PLAN ==")
    flagged = []
    for name, (sql, params) in HOT_SQL.items():
        plan = db.query(f"EXPLAIN QUERY PLAN {sql}", params)
        lines = [row["detail"] for row in plan]
        print(f"  [{name}]")
        for ln in lines:
            mark = ""
            if ln.startswith("SCAN") and "USING" not in ln:
                mark = "   ← 全表扫描"
                flagged.append((name, ln))
            print(f"    {ln}{mark}")
    if flagged:
        print("\n!! 存在全表扫描的热查询:")
        for name, ln in flagged:
            print(f"   - {name}: {ln}")

    print("\n== 现有索引 ==")
    for r in db.query("SELECT name, tbl_name FROM sqlite_master WHERE type='index'"
                      " AND name NOT LIKE 'sqlite_%' ORDER BY tbl_name, name"):
        print(f"  {r['tbl_name']:16s} {r['name']}")

    store.close()
    if not args.keep and args.db is None:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    return 1 if flagged else 0


if __name__ == "__main__":
    sys.exit(main())
