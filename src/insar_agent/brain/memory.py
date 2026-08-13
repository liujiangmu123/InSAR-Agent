"""跨会话记忆:研究偏好 / 重要事实 / 历史结论的存取与规则萃取。

用户目标:「有记忆」的助手 —— 记住常用区域/场景/参数口味(preference)、
数据位置/机器环境特点(fact)、某区域某方法的历史效果(outcome),
后续对话自动带上。

设计:
  - 存储:memories 表(core/db.py 的 _STATEMENT_MIGRATIONS 建表,新旧库同
    路径补齐)。kind 闭集 preference|fact|outcome;source 闭集 user(手动)|
    auto(规则萃取);session_id NULL=全局记忆;archived 非 NULL=软删时刻
    (同 sessions.archived 语义:列表默认不含,数据保留可查)。
  - 注入契约(与对话二期代理约定,签名一字不差,tests/test_memory.py 用
    inspect.signature 锁死):get_context_snippets(store, session_id, limit=5)
    → 权重降序的活跃记忆内容串(全局 + 该会话),空表返回 []。
  - 萃取是纯规则,不用 LLM:extract_from_run 只对 done run 产出 outcome 记忆
    「<日期> <场景>@<区域 or 会话名> 用 <关键方法链> 完成,证据级 <level>」;
    record_preference 供对话代理在 converse plan 动作后记场景/区域偏好。
  - 去重加权:同 kind+content 的活跃记忆不重插,weight+0.5(反复提到的事
    更重要,注入排序随之上浮);萃取与偏好钩子因此天然幂等。

接线点(本模块零侵入,不碰 loop/driver.py 与 brain/facade.py):
  - api/memory_router.py:REST 面(列表/搜索/手动添加/归档软删/萃取);
  - POST /api/memory/extract {run_id}:run 到达 done 后由前端建议卡或用户
    手动触发(不挂 driver 钩子,萃取时机归 UI/队列层);
  - 对话二期代理:converse 组 prompt 时调 get_context_snippets 注入;
    plan 动作落地后调 record_preference(store, scenario, region)。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from insar_agent.audit.contract import load_contract
from insar_agent.audit.ladder import compute_evidence
from insar_agent.core.db import Database
from insar_agent.core.store import Store, _escape_like

#: 记忆分类闭集(schema 注释同步):偏好 / 事实 / 历史结论
MEMORY_KINDS = ("preference", "fact", "outcome")
#: 来源闭集:user=手动添加,auto=规则萃取
MEMORY_SOURCES = ("user", "auto")

#: 内容长度上限:记忆是注入 prompt 的短句不是文档(API 边界同限,防撑爆库)
CONTENT_MAX = 500

#: 方法链展示上限:超出取前 N-1 个 + 「等 n 步」(注入片段必须短)
_CHAIN_MAX = 6


class MemoryStore:
    """memories 表的最小存取封装(与 core/store.Store 共库共锁,单进程访问)。"""

    def __init__(self, db: Database | Store):
        # 接 Store 或 Database 皆可:api 层手里是 Store,测试常直接给 Database
        self.db = db.db if isinstance(db, Store) else db

    # ---------------- 写入 ----------------

    def add(self, kind: str, content: str, *, source: str = "user",
            session_id: str | None = None, weight: float = 1.0) -> int:
        """新增记忆;同 kind+content 的活跃记忆已存在时不重插,weight+0.5。

        kind/source 闭集校验、内容非空且 ≤CONTENT_MAX,违反抛 ValueError
        (API 层转 400)。返回记忆 id(命中去重时返回既有行 id)。
        """
        if kind not in MEMORY_KINDS:
            raise ValueError(f"未知记忆类型 {kind!r}(可用:{'|'.join(MEMORY_KINDS)})")
        if source not in MEMORY_SOURCES:
            raise ValueError(f"未知来源 {source!r}(可用:{'|'.join(MEMORY_SOURCES)})")
        content = (content or "").strip()
        if not content or len(content) > CONTENT_MAX:
            raise ValueError(f"content 不合法:去首尾空白后长度须为 1-{CONTENT_MAX} 字符")
        with self.db.tx() as cur:
            row = cur.execute(
                "SELECT id FROM memories WHERE kind=? AND content=? AND archived IS NULL"
                " ORDER BY id LIMIT 1", (kind, content)).fetchone()
            if row is not None:
                cur.execute("UPDATE memories SET weight=weight+0.5 WHERE id=?",
                            (row["id"],))
                return int(row["id"])
            cur.execute(
                "INSERT INTO memories(ts,kind,content,source,session_id,weight)"
                " VALUES (?,?,?,?,?,?)",
                (time.time(), kind, content, source, session_id, float(weight)))
            return int(cur.lastrowid)

    def archive(self, memory_id: int) -> bool:
        """软删除:落归档时刻,列表/注入不再出现,数据保留。不存在返回 False。"""
        with self.db.tx() as cur:
            cur.execute("UPDATE memories SET archived=? WHERE id=?",
                        (time.time(), memory_id))
            return cur.rowcount > 0

    # ---------------- 读取 ----------------

    def get(self, memory_id: int) -> dict | None:
        r = self.db.query_one("SELECT * FROM memories WHERE id=?", (memory_id,))
        return dict(r) if r else None

    def find_active(self, kind: str, content: str) -> dict | None:
        """按 kind+content 精确查活跃记忆(add 的去重判据;萃取用它报告 created)。"""
        r = self.db.query_one(
            "SELECT * FROM memories WHERE kind=? AND content=? AND archived IS NULL"
            " ORDER BY id LIMIT 1", (kind, content))
        return dict(r) if r else None

    def list(self, *, kind: str | None = None, include_archived: bool = False,
             limit: int = 200) -> list[dict]:
        """记忆清单。默认不含已归档;排序与注入一致(weight 降序,新者优先),
        面板看到的顺序即对话带上的顺序。"""
        where, params = [], []
        if not include_archived:
            where.append("archived IS NULL")
        if kind:
            where.append("kind=?")
            params.append(kind)
        sql = "SELECT * FROM memories"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY weight DESC, ts DESC, id DESC LIMIT ?"
        return [dict(r) for r in self.db.query(sql, tuple(params) + (int(limit),))]

    def search(self, keyword: str, *, include_archived: bool = False,
               limit: int = 200) -> list[dict]:
        """内容子串搜索(LIKE;%/_/\\ 转义按字面匹配,与 store 会话搜索同口径)。"""
        where = ["content LIKE ? ESCAPE '\\'"]
        params: list = ["%" + _escape_like(keyword) + "%"]
        if not include_archived:
            where.append("archived IS NULL")
        return [dict(r) for r in self.db.query(
            "SELECT * FROM memories WHERE " + " AND ".join(where)
            + " ORDER BY weight DESC, ts DESC, id DESC LIMIT ?",
            tuple(params) + (int(limit),))]


# ---------------------------------------------------------------------------
# 消费契约(对话二期代理依赖 —— 签名一字不得改,守护测试 inspect.signature 锁死)
# ---------------------------------------------------------------------------

def get_context_snippets(store, session_id, limit=5) -> list[str]:
    """回合注入用的记忆片段:全局记忆(session_id IS NULL)+ 该会话记忆,
    只取活跃行,weight 降序(同权新者优先),返回内容串列表;空表 []。

    store 是 MemoryStore;session_id 传 None 时只取全局记忆。
    """
    rows = store.db.query(
        "SELECT content FROM memories WHERE archived IS NULL"
        " AND (session_id IS NULL OR session_id=?)"
        " ORDER BY weight DESC, ts DESC, id DESC LIMIT ?",
        (session_id, int(limit)))
    return [r["content"] for r in rows]


# ---------------------------------------------------------------------------
# 规则萃取(不用 LLM;调用点见模块头「接线点」)
# ---------------------------------------------------------------------------

def record_preference(store: MemoryStore, scenario, region) -> list[int]:
    """【预留钩子 —— 对话代理 converse plan 动作后调用】记场景/区域偏好。

    空值跳过;重复调用命中 add 的去重加权(weight+0.5),天然幂等。
    返回写入(或加权)的记忆 id 列表。
    """
    ids = []
    for label, value in (("偏好场景", scenario), ("常用区域", region)):
        value = (value or "").strip() if isinstance(value, str) else ""
        if value:
            ids.append(store.add("preference", f"{label}:{value}", source="auto"))
    return ids


def _run_place(core: Store, run: dict) -> str:
    """outcome 记忆的地点段:intent.region 优先,回退会话显示名,再退 session_id。"""
    try:
        intent = json.loads(run.get("intent") or "{}")
    except ValueError:
        intent = {}
    region = intent.get("region") if isinstance(intent, dict) else None
    if isinstance(region, str) and region.strip():
        return region.strip()
    sess = core.get_session(run["session_id"])
    return (sess or {}).get("name") or run["session_id"]


def _method_chain(core: Store, run_id: str) -> str:
    """done 步骤按 step_id 序的方法链(连续去重);超长截断保持片段可注入。"""
    methods: list[str] = []
    for s in core.load_steps(run_id):
        if s.state == "done" and (not methods or methods[-1] != s.method):
            methods.append(s.method)
    if not methods:
        return "云端/缓存链"  # 全部步骤 skipped 的 done run:无本地执行方法
    if len(methods) > _CHAIN_MAX:
        methods = methods[:_CHAIN_MAX - 1] + [f"等{len(methods)}步"]
    return "→".join(methods)


def extract_from_run(store: MemoryStore, run_id: str) -> dict:
    """done run → 一条全局 outcome 记忆(纯规则;幂等:同内容命中去重加权)。

    内容模板:「<日期> <场景>@<区域 or 会话名> 用 <关键方法链> 完成,证据级 <level>」
      - 日期:run 创建时刻的本地日期(YYYY-MM-DD);
      - 证据级:audit.ladder 六级阶梯的机器判定(与 provenance 同口径,
        simulated run 封顶 runnable —— 演示结论不冒充证据)。

    run 不存在抛 KeyError,非 done 抛 ValueError(API 层分别转 404/409)。
    返回 {"memory_id", "content", "created"}(created=False 表示命中去重)。
    """
    core = Store(store.db)  # 共用同一连接/锁,只读 runs/steps/sessions
    run = core.get_run(run_id)
    if run is None:
        raise KeyError(run_id)
    if run["status"] != "done":
        raise ValueError(f"run {run_id} 状态为 {run['status']},只对 done run 萃取结论")
    date = time.strftime("%Y-%m-%d", time.localtime(run["created_at"]))
    scenario = run.get("scenario") or "未知场景"
    ws = run.get("workspace") or ""
    level = compute_evidence(core, run_id, load_contract(),
                             workspace=Path(ws) if ws else None).level
    content = (f"{date} {scenario}@{_run_place(core, run)}"
               f" 用 {_method_chain(core, run_id)} 完成,证据级 {level}")
    created = store.find_active("outcome", content) is None
    memory_id = store.add("outcome", content, source="auto")
    return {"memory_id": memory_id, "content": content, "created": created}
