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
_COLUMN_MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    # absorb-E3:取消是控制位不是状态 —— 持久化取消意图,服务重启后不丢
    ("runs", "control",
     "ALTER TABLE runs ADD COLUMN control TEXT NOT NULL DEFAULT 'running'"),
)


def _migrate(conn: sqlite3.Connection) -> None:
    for table, column, ddl in _COLUMN_MIGRATIONS:
        cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if cols and column not in cols:
            conn.execute(ddl)


class Database:
    """单进程访问(设计约束:只有宿主进程访问 .db)。线程安全靠 RLock 串行化。"""

    def __init__(self, path: str | Path = ":memory:"):
        self.path = str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.executescript(_load_schema())
        _migrate(self._conn)
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
