"""LLM 用量账本:每次调用一行流水,供 /api/llm/usage 汇总(计费中转站 ¥/百万 token)。

数据流(依赖方向只进不出,provider 保持纯传输层):
  provider._report_usage → set_usage_sink 注入的 UsageLedger.record → llm_calls 表。

成本估算纪律(绝不编数):
  - 价格表复用 provider.list_models 的字段(effective_*_price_per_million,CNY),
    进程内缓存一次;拉取失败按 _PRICE_RETRY_INTERVAL 节流重试,不让坏配置把
    每次 LLM 调用都拖一个模型列表请求;
  - 缺任一 token 计数或查不到该模型价格 → cost_est=NULL(界面显示为未知)。

会话上下文:usage_context(contextvars)由调用方(llm_router/facade)在 LLM
调用外围绑定;拿不到就记 NULL —— 账本对调用点零打扰。
"""

from __future__ import annotations

import contextlib
import contextvars
import time
from pathlib import Path
from typing import Callable

from insar_agent.core.db import Database

#: (session_id, run_id) 上下文:provider 上报的 record 不含会话信息,记账时从这取
_context: contextvars.ContextVar[tuple[str | None, str | None]] = contextvars.ContextVar(
    "llm_usage_context", default=(None, None))

#: 价格表拉取失败后的最小重试间隔(秒)
_PRICE_RETRY_INTERVAL = 300.0


@contextlib.contextmanager
def usage_context(session_id: str | None, run_id: str | None = None):
    """在 with 块内发生的 LLM 调用记账到指定会话/run(可嵌套,退出即还原)。"""
    token = _context.set((session_id, run_id))
    try:
        yield
    finally:
        _context.reset(token)


def _int_or_none(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _price_or_none(value) -> float | None:
    """价格字段 → 非负数值才可信(中转站可能给字符串形态的数字)。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if value >= 0 else None
    if isinstance(value, str):
        try:
            v = float(value)
        except ValueError:
            return None
        return v if v >= 0 else None
    return None


class UsageLedger:
    """记账 + 汇总。写入走 db 事务锁,FastAPI 线程池并发安全。"""

    def __init__(self, db: Database, home: Path | None = None,
                 price_loader: Callable[[], dict[str, tuple[float, float]]] | None = None):
        self._db = db
        self._home = home
        self._price_loader = price_loader or self._load_prices_from_relay
        self._prices: dict[str, tuple[float, float]] | None = None
        self._price_fail_at = float("-inf")

    # ---------------- 记账 ----------------

    def record(self, rec: dict) -> None:
        """落一行流水。rec 来自 provider._report_usage(或测试直构):
        {model, kind, prompt_tokens, completion_tokens, latency_ms
         [, session_id, run_id]};缺失字段一律记 NULL。"""
        session_id, run_id = _context.get()
        model = str(rec.get("model") or "")
        prompt = _int_or_none(rec.get("prompt_tokens"))
        completion = _int_or_none(rec.get("completion_tokens"))
        with self._db.tx() as cur:
            cur.execute(
                "INSERT INTO llm_calls (ts, model, kind, prompt_tokens,"
                " completion_tokens, latency_ms, session_id, run_id, cost_est)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (time.time(), model, str(rec.get("kind") or ""), prompt, completion,
                 _int_or_none(rec.get("latency_ms")),
                 rec.get("session_id") or session_id,
                 rec.get("run_id") or run_id,
                 self._estimate_cost(model, prompt, completion)))

    def _estimate_cost(self, model: str, prompt: int | None,
                       completion: int | None) -> float | None:
        """成本(CNY)。缺任一计数或无该模型价格 → None,绝不编数。"""
        if prompt is None or completion is None:
            return None
        prices = self._get_prices()
        pair = prices.get(model) if prices else None
        if pair is None:
            return None
        return round(prompt / 1e6 * pair[0] + completion / 1e6 * pair[1], 6)

    def _get_prices(self) -> dict[str, tuple[float, float]] | None:
        if self._prices is not None:
            return self._prices
        if time.monotonic() - self._price_fail_at < _PRICE_RETRY_INTERVAL:
            return None  # 上次拉取刚失败:节流,期间 cost_est 一律 NULL
        try:
            self._prices = self._price_loader()
        except Exception:  # noqa: BLE001 —— 价格拿不到只影响估算,不影响记账
            self._price_fail_at = time.monotonic()
            return None
        return self._prices

    def _load_prices_from_relay(self) -> dict[str, tuple[float, float]]:
        """服务端持钥拉一次 /models,取每模型 (输入价, 输出价)(¥/百万 token)。"""
        from insar_agent.brain.llm_config import load_llm_config
        from insar_agent.brain.provider import list_models

        cfg = load_llm_config(self._home) if self._home else {}
        if not (cfg.get("base_url") and cfg.get("api_key")):
            raise LookupError("未配置中转站,无价格表")
        out: dict[str, tuple[float, float]] = {}
        for m in list_models(cfg["base_url"], cfg["api_key"]):
            pin = _price_or_none(m.get("effective_input_price_per_million"))
            pout = _price_or_none(m.get("effective_output_price_per_million"))
            if pin is not None and pout is not None:
                out[str(m["id"])] = (pin, pout)
        return out

    # ---------------- 汇总查询 ----------------

    def summary(self, days: int = 7) -> dict:
        """近 N 天汇总:总量 / 按模型 / 按日(本地时区)/ 按会话 / 最近 20 条流水。

        SUM 语义即「绝不编数」语义:token/cost 的 NULL 不参与求和;整列全 NULL
        (或零行)时和为 NULL —— 已知部分的和是下界,未知就是未知。
        """
        since = time.time() - days * 86400.0
        agg = ("COUNT(*) AS calls, SUM(prompt_tokens) AS pt,"
               " SUM(completion_tokens) AS ct, SUM(cost_est) AS cost")

        def shape(row, extra: dict | None = None) -> dict:
            return {**(extra or {}),
                    "calls": row["calls"], "prompt_tokens": row["pt"],
                    "completion_tokens": row["ct"], "cost_est_cny": row["cost"]}

        total = self._db.query_one(
            f"SELECT {agg} FROM llm_calls WHERE ts >= ?", (since,))
        by_model = self._db.query(
            f"SELECT model, {agg} FROM llm_calls WHERE ts >= ?"
            " GROUP BY model ORDER BY calls DESC, model", (since,))
        by_day = self._db.query(
            f"SELECT date(ts, 'unixepoch', 'localtime') AS day, {agg}"
            " FROM llm_calls WHERE ts >= ? GROUP BY day ORDER BY day DESC", (since,))
        by_session = self._db.query(
            f"SELECT session_id, {agg} FROM llm_calls WHERE ts >= ?"
            " GROUP BY session_id ORDER BY calls DESC", (since,))
        recent = self._db.query(
            "SELECT id, ts, model, kind, prompt_tokens, completion_tokens,"
            " latency_ms, session_id, run_id, cost_est FROM llm_calls"
            " ORDER BY id DESC LIMIT 20")
        return {
            "days": days,
            "total": shape(total),
            "by_model": [shape(r, {"model": r["model"]}) for r in by_model],
            "by_day": [shape(r, {"day": r["day"]}) for r in by_day],
            "by_session": [shape(r, {"session_id": r["session_id"]}) for r in by_session],
            "recent": [dict(r) for r in recent],
        }
