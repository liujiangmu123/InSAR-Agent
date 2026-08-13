"""SQLite 连接封装:WAL、外键、线程锁、事务上下文。"""

from __future__ import annotations

import sqlite3
import threading
from importlib import resources
from pathlib import Path


def _load_schema() -> str:
    return (resources.files("insar_agent.core") / "schema.sql").read_text(encoding="utf-8")


# 列迁移清单:schema.sql 的 CREATE TABLE IF NOT EXISTS 不会改动既有表,
# 旧库缺列时按此清单 ALTER 补齐(PRAGMA table_info 检查,幂等)。
# 新增索引不需要进清单:schema.sql 的 CREATE INDEX IF NOT EXISTS 每次连接都会
# 重放,旧库自动补齐;清单只兜 CREATE 覆盖不到的变更(加列、删冗余索引)。
_COLUMN_MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    # absorb-E3:取消是控制位不是状态 —— 持久化取消意图,服务重启后不丢
    ("runs", "control",
     "ALTER TABLE runs ADD COLUMN control TEXT NOT NULL DEFAULT 'running'"),
    # 干预动作按 run 隔离(REVIEW-2026-08-12 P1:跨 run 互吞/溯源污染)
    ("pending_actions", "run_id",
     "ALTER TABLE pending_actions ADD COLUMN run_id TEXT"),
    # 会话软删除:归档时刻(NULL=活跃);列表默认过滤,run/工作区数据一律保留
    ("sessions", "archived",
     "ALTER TABLE sessions ADD COLUMN archived REAL"),
    # 指纹记录格式版本门控(absorb-M):早期库建于该列入 schema 之前,缺列会让
    # load_steps 在 r["record_version"] 上 IndexError 炸穿 —— 2026-08-13 真实
    # workspace 实测(converse 上线首日聊天即触发,老库一直没走过这条路径)
    ("steps", "record_version",
     "ALTER TABLE steps ADD COLUMN record_version INTEGER NOT NULL DEFAULT 1"),
    ("artifacts", "record_version",
     "ALTER TABLE artifacts ADD COLUMN record_version INTEGER NOT NULL DEFAULT 1"),
    # 投递语义列同期缺失(absorb-E4 之前的库)
    ("pending_actions", "deliver_as",
     "ALTER TABLE pending_actions ADD COLUMN deliver_as TEXT NOT NULL DEFAULT 'steer'"),
)

_STATEMENT_MIGRATIONS: tuple[str, ...] = (
    # idx_steps_run 与主键 autoindex (run_id, step_id) 前缀完全重复,计划器
    # 本就选 autoindex(scripts/bench_store.py 的 EXPLAIN 佐证),徒增每次
    # INSERT 的维护成本 → 旧库删掉;schema.sql 已不再创建。
    "DROP INDEX IF EXISTS idx_steps_run",
    # 分页索引(与 schema.sql 末尾索引区双处一致):schema 重放本可自动补齐,
    # 迁移清单再列一份是显式台账 —— 未来 schema 重放策略若收紧,旧库不掉索引。
    "CREATE INDEX IF NOT EXISTS idx_sessions_created ON sessions(created_at, session_id)",
    "CREATE INDEX IF NOT EXISTS idx_trace_run_ts ON trace(run_id, ts)",
    # 运行队列(loop/queue.py:全局串行调度的持久 FIFO)。建表走迁移而不动
    # schema.sql 主文件:CREATE IF NOT EXISTS 幂等重放,新旧库同路径补齐。
    "CREATE TABLE IF NOT EXISTS run_queue ("
    " id INTEGER PRIMARY KEY, run_id TEXT NOT NULL, session_id TEXT NOT NULL,"
    " step_ids_json TEXT, enqueued_at REAL NOT NULL, started_at REAL,"
    " state TEXT NOT NULL DEFAULT 'pending')",  # state: pending|running|done|cancelled
    # LLM 用量账本(brain/usage.py):每次调用一行流水;token/成本拿不到记 NULL
    "CREATE TABLE IF NOT EXISTS llm_calls ("
    " id INTEGER PRIMARY KEY, ts REAL NOT NULL, model TEXT NOT NULL,"
    " kind TEXT NOT NULL, prompt_tokens INTEGER, completion_tokens INTEGER,"
    " latency_ms INTEGER, session_id TEXT, run_id TEXT, cost_est REAL)",  # kind: chat|vision
    "CREATE INDEX IF NOT EXISTS idx_llm_calls_ts ON llm_calls(ts)",
    # 跨会话记忆(brain/memory.py):kind=preference|fact|outcome,source=user|auto;
    # session_id NULL=全局记忆;archived 非 NULL=软删时刻(同 sessions.archived 语义)
    "CREATE TABLE IF NOT EXISTS memories ("
    " id INTEGER PRIMARY KEY, ts REAL NOT NULL, kind TEXT NOT NULL,"
    " content TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'user',"
    " session_id TEXT, weight REAL NOT NULL DEFAULT 1.0, archived REAL)",
)


def _migrate_columns(conn: sqlite3.Connection) -> None:
    """列补齐必须先于 schema 重放:schema.sql 的 CREATE INDEX IF NOT EXISTS
    可能引用后来才加的列(如 idx_actions_due → deliver_as),旧库缺列时
    executescript 会在建索引处直接炸(2026-08-13 真实 workspace 实测)。
    全新库(无表)时 PRAGMA 返回空,整个循环自然跳过。"""
    for table, column, ddl in _COLUMN_MIGRATIONS:
        cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if cols and column not in cols:
            conn.execute(ddl)


def _migrate_statements(conn: sqlite3.Connection) -> None:
    for ddl in _STATEMENT_MIGRATIONS:
        conn.execute(ddl)


class Database:
    """单进程访问(设计约束:只有宿主进程访问 .db)。线程安全靠 RLock 串行化。"""

    def __init__(self, path: str | Path = ":memory:"):
        self.path = str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        # busy_timeout 显式固定:此前依赖 Python connect(timeout=5.0) 的隐式默认。
        # 单进程设计下仍会出现第二连接(备份/巡检工具、bench、测试),写锁竞争时
        # 等待而不是立刻 "database is locked"。
        self._conn.execute("PRAGMA busy_timeout = 5000")
        if self.path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
            # WAL 推荐搭配 NORMAL:fsync 从每次提交降到 checkpoint 时;掉电最多
            # 丢最近提交、库不损坏。「每阶段一事务」的写路径对每提交 fsync 最敏感
            # (FULL→NORMAL 实测见 scripts/bench_store.py 写路径样本)。
            self._conn.execute("PRAGMA synchronous = NORMAL")
        _migrate_columns(self._conn)  # 先补列(见函数注释:索引可能引用新列)
        self._conn.executescript(_load_schema())
        _migrate_statements(self._conn)
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def tx(self):
        """事务上下文:with db.tx() as cur: ...(提交或回滚整个批次)。"""
        return _Tx(self)

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def query_one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()


class _Tx:
    def __init__(self, db: Database):
        self._db = db

    def __enter__(self) -> sqlite3.Cursor:
        self._db._lock.acquire()
        self._cur = self._db._conn.cursor()
        return self._cur

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if exc_type is None:
                self._db._conn.commit()
            else:
                self._db._conn.rollback()
        finally:
            self._cur.close()
            self._db._lock.release()
