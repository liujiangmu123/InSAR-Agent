# -*- coding: utf-8 -*-
"""LLM 用量账本:provider 计量埋点 → UsageLedger 记账/成本估算 → /api/llm/usage。

纪律:mock 一切 LLM(urlopen 打桩),零真实出网;价格表用假 loader,绝不联网。
流式 SSE 帧形状按 2026-08-13 tokenrhythm 实测复刻:内容帧 usage 为 null,
[DONE] 前一帧 choices 为空数组且带完整 usage(见 provider.describe_image_stream 注释)。
"""
from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

import insar_agent.brain.provider as provider_mod
from insar_agent.api.app import create_app
from insar_agent.brain.provider import LLMProvider, LLMRoute, describe_image_stream
from insar_agent.brain.usage import UsageLedger, usage_context
from insar_agent.core.db import Database

# ---------------- 公共桩件 ----------------


@pytest.fixture(autouse=True)
def _reset_sink():
    """sink 是模块级状态:每个用例退出后清空,防跨用例(含跨文件)泄漏。"""
    yield
    provider_mod.set_usage_sink(None)


@pytest.fixture()
def home(tmp_path, monkeypatch):
    for var in ("INSAR_LLM_BASE_URL", "INSAR_LLM_API_KEY", "INSAR_LLM_MODEL",
                "INSAR_LLM_VISION_MODEL",
                "INSAR_LLM_FALLBACK_BASE_URL", "INSAR_LLM_FALLBACK_MODEL"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path / "home"


class FakeJSONResp:
    """非流式 /chat/completions 响应桩(urlopen 返回值形态)。"""

    def __init__(self, body: dict):
        self._raw = json.dumps(body, ensure_ascii=False).encode()

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeStreamResp:
    """流式 SSE 响应桩:按行迭代(与 urlopen 的按行读取一致)。"""

    def __init__(self, lines: list[bytes]):
        self._lines = lines

    def __iter__(self):
        return iter(self._lines)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def chat_body(content: dict | str, usage=None, finish="stop") -> dict:
    if isinstance(content, dict):
        content = json.dumps(content, ensure_ascii=False)
    body = {"choices": [{"message": {"role": "assistant", "content": content},
                         "finish_reason": finish}]}
    if usage is not None:
        body["usage"] = usage
    return body


#: tokenrhythm 实测形态的流式帧:内容帧带 "usage": null,尾帧 choices=[] + usage
TOKENRHYTHM_FRAMES = [
    b'data: {"choices":[{"delta":{"content":"\xe7\xba\xa2"}}],"usage":null}\n',
    b"\n",
    b'data: {"choices":[{"delta":{"content":"\xe8\x89\xb2"},"finish_reason":"stop"}],'
    b'"usage":null}\n',
    b'data: {"choices":[],"usage":{"prompt_tokens":25,"completion_tokens":80,'
    b'"total_tokens":105},"cost_cny":0.0021,"billing_pending":true}\n',
    b"data: [DONE]\n",
]


def capture_sink() -> tuple[list[dict], object]:
    records: list[dict] = []
    provider_mod.set_usage_sink(records.append)
    return records, None


# ---------------- provider 埋点:chat(非流式) ----------------


def test_chat_sink_captures_usage_and_latency(monkeypatch):
    records, _ = capture_sink()
    monkeypatch.setattr(
        provider_mod.urllib.request, "urlopen",
        lambda req, timeout: FakeJSONResp(chat_body(
            {"ok": True}, usage={"prompt_tokens": 120, "completion_tokens": 34})))
    out = LLMProvider(routes=[LLMRoute("https://x/v1", "k", "m1")]).complete_json(
        system="s", user="u")
    assert out == {"ok": True}
    assert len(records) == 1
    rec = records[0]
    assert rec["model"] == "m1" and rec["kind"] == "chat"
    assert rec["prompt_tokens"] == 120 and rec["completion_tokens"] == 34
    assert isinstance(rec["latency_ms"], int) and rec["latency_ms"] >= 0


def test_chat_usage_missing_records_null(monkeypatch):
    """响应体没有 usage(非标准中转站)→ token 记 None,绝不编数。"""
    records, _ = capture_sink()
    monkeypatch.setattr(provider_mod.urllib.request, "urlopen",
                        lambda req, timeout: FakeJSONResp(chat_body({"ok": True})))
    LLMProvider(routes=[LLMRoute("https://x/v1", "k", "m1")]).complete_json(
        system="s", user="u")
    assert records[0]["prompt_tokens"] is None
    assert records[0]["completion_tokens"] is None


def test_chat_usage_garbage_values_recorded_as_null(monkeypatch):
    """usage 字段值是字符串/负数/bool 等垃圾形态 → 一律 None(值域校验)。"""
    records, _ = capture_sink()
    monkeypatch.setattr(
        provider_mod.urllib.request, "urlopen",
        lambda req, timeout: FakeJSONResp(chat_body(
            {"ok": True}, usage={"prompt_tokens": "120", "completion_tokens": -3})))
    LLMProvider(routes=[LLMRoute("https://x/v1", "k", "m1")]).complete_json(
        system="s", user="u")
    assert records[0]["prompt_tokens"] is None
    assert records[0]["completion_tokens"] is None


def test_chat_truncated_still_recorded(monkeypatch):
    """截断整体拒绝(BrainTruncated),但 token 已被中转站计费 → 照样入账。"""
    records, _ = capture_sink()
    monkeypatch.setattr(
        provider_mod.urllib.request, "urlopen",
        lambda req, timeout: FakeJSONResp(chat_body(
            {"x": 1}, usage={"prompt_tokens": 9, "completion_tokens": 512},
            finish="length")))
    with pytest.raises(provider_mod.BrainTruncated):
        LLMProvider(routes=[LLMRoute("https://x/v1", "k", "m1")]).complete_json(
            system="s", user="u")
    assert len(records) == 1 and records[0]["completion_tokens"] == 512


def test_chat_fallback_records_each_billed_call(monkeypatch):
    """主路由响应不可解析(已计费)→ 切备路由:两次调用各记一行。"""
    records, _ = capture_sink()
    resps = [FakeJSONResp(chat_body("不是 JSON", usage={"prompt_tokens": 5,
                                                        "completion_tokens": 7})),
             FakeJSONResp(chat_body({"ok": 1}, usage={"prompt_tokens": 5,
                                                      "completion_tokens": 3}))]
    monkeypatch.setattr(provider_mod.urllib.request, "urlopen",
                        lambda req, timeout: resps.pop(0))
    out = LLMProvider(routes=[LLMRoute("https://a/v1", "k", "ma"),
                              LLMRoute("https://b/v1", "k", "mb")]).complete_json(
        system="s", user="u")
    assert out == {"ok": 1}
    assert [r["model"] for r in records] == ["ma", "mb"]


# ---------------- provider 埋点:vision(流式) ----------------


def test_stream_requests_include_usage_and_captures_tail_frame(monkeypatch):
    """发起流式请求必须带 stream_options.include_usage;尾帧 usage 被采集。"""
    records, _ = capture_sink()
    seen = {}

    def fake_urlopen(req, timeout):
        seen["payload"] = json.loads(req.data.decode("utf-8"))
        return FakeStreamResp(TOKENRHYTHM_FRAMES)

    monkeypatch.setattr(provider_mod.urllib.request, "urlopen", fake_urlopen)
    out = describe_image_stream(LLMRoute("https://x/v1", "k", "vm"),
                                prompt="颜色?", image_data_url="data:image/png;base64,AA")
    assert out == "红色"
    assert seen["payload"]["stream_options"] == {"include_usage": True}
    assert len(records) == 1
    rec = records[0]
    assert rec["model"] == "vm" and rec["kind"] == "vision"
    assert rec["prompt_tokens"] == 25 and rec["completion_tokens"] == 80


def test_stream_without_usage_tail_records_null(monkeypatch):
    """中转站不认 include_usage(尾帧无 usage)→ token 记 None,内容不受影响。"""
    records, _ = capture_sink()
    frames = [
        b'data: {"choices":[{"delta":{"content":"red"},"finish_reason":"stop"}]}\n',
        b"data: [DONE]\n",
    ]
    monkeypatch.setattr(provider_mod.urllib.request, "urlopen",
                        lambda req, timeout: FakeStreamResp(frames))
    out = describe_image_stream(LLMRoute("https://x/v1", "k", "vm"),
                                prompt="p", image_data_url="data:image/png;base64,AA")
    assert out == "red"
    assert records[0]["prompt_tokens"] is None
    assert records[0]["completion_tokens"] is None


def test_stream_truncated_still_recorded(monkeypatch):
    records, _ = capture_sink()
    frames = [
        b'data: {"choices":[{"delta":{"content":"x"},"finish_reason":"length"}]}\n',
        b'data: {"choices":[],"usage":{"prompt_tokens":25,"completion_tokens":64}}\n',
        b"data: [DONE]\n",
    ]
    monkeypatch.setattr(provider_mod.urllib.request, "urlopen",
                        lambda req, timeout: FakeStreamResp(frames))
    with pytest.raises(provider_mod.BrainTruncated):
        describe_image_stream(LLMRoute("https://x/v1", "k", "vm"),
                              prompt="p", image_data_url="data:image/png;base64,AA")
    assert records[0]["completion_tokens"] == 64


# ---------------- 无 sink / 坏 sink:主链路零影响 ----------------


def test_no_sink_zero_overhead_and_unchanged_behavior(monkeypatch):
    """默认无 sink:不记录、不报错,complete_json 行为与埋点前逐字节一致。"""
    assert provider_mod._usage_sink is None
    monkeypatch.setattr(
        provider_mod.urllib.request, "urlopen",
        lambda req, timeout: FakeJSONResp(chat_body(
            {"ok": True}, usage={"prompt_tokens": 1, "completion_tokens": 2})))
    out = LLMProvider(routes=[LLMRoute("https://x/v1", "k", "m")]).complete_json(
        system="s", user="u")
    assert out == {"ok": True}


def test_broken_sink_never_breaks_llm_call(monkeypatch):
    """计量是旁路:sink 抛错必须被吞掉,LLM 调用照常返回。"""

    def bomb(_rec):
        raise RuntimeError("账本炸了")

    provider_mod.set_usage_sink(bomb)
    monkeypatch.setattr(provider_mod.urllib.request, "urlopen",
                        lambda req, timeout: FakeJSONResp(chat_body({"ok": True})))
    out = LLMProvider(routes=[LLMRoute("https://x/v1", "k", "m")]).complete_json(
        system="s", user="u")
    assert out == {"ok": True}


# ---------------- 表迁移 ----------------

_EXPECTED_COLUMNS = ["id", "ts", "model", "kind", "prompt_tokens",
                     "completion_tokens", "latency_ms", "session_id", "run_id",
                     "cost_est"]


def test_migration_creates_llm_calls_table(db):
    cols = [r["name"] for r in db.query("PRAGMA table_info(llm_calls)")]
    assert cols == _EXPECTED_COLUMNS


def test_migration_idempotent_on_existing_db(tmp_path):
    """同一库文件开两次(模拟旧库升级/服务重启):迁移幂等,数据保留。"""
    path = tmp_path / "insar.db"
    d1 = Database(path)
    UsageLedger(d1, price_loader=lambda: {}).record(
        {"model": "m", "kind": "chat", "prompt_tokens": 1, "completion_tokens": 2,
         "latency_ms": 3})
    d1.close()
    d2 = Database(path)
    try:
        assert d2.query_one("SELECT COUNT(*) AS n FROM llm_calls")["n"] == 1
    finally:
        d2.close()


# ---------------- UsageLedger:记账 / 成本 / 上下文 ----------------


def test_ledger_cost_math_with_mock_prices(db):
    """成本 = prompt/1e6*输入价 + completion/1e6*输出价(CNY,价格表 mock)。"""
    ledger = UsageLedger(db, price_loader=lambda: {"m1": (2.0, 8.0)})
    ledger.record({"model": "m1", "kind": "chat",
                   "prompt_tokens": 1000, "completion_tokens": 500, "latency_ms": 42})
    row = db.query_one("SELECT * FROM llm_calls")
    assert row["cost_est"] == pytest.approx(1000 / 1e6 * 2.0 + 500 / 1e6 * 8.0)  # 0.006
    assert row["model"] == "m1" and row["kind"] == "chat" and row["latency_ms"] == 42
    assert row["session_id"] is None and row["run_id"] is None


def test_ledger_unknown_model_or_missing_tokens_cost_null(db):
    ledger = UsageLedger(db, price_loader=lambda: {"m1": (2.0, 8.0)})
    ledger.record({"model": "m2", "kind": "chat",          # 价格表没有 m2
                   "prompt_tokens": 1000, "completion_tokens": 500, "latency_ms": 1})
    ledger.record({"model": "m1", "kind": "vision",        # 缺 completion 计数
                   "prompt_tokens": 1000, "completion_tokens": None, "latency_ms": 1})
    costs = [r["cost_est"] for r in db.query("SELECT cost_est FROM llm_calls ORDER BY id")]
    assert costs == [None, None]


def test_ledger_price_loader_failure_records_null_and_throttles(db):
    """价格拉取失败:记账照常(cost NULL),且失败后节流不再每次重拉。"""
    calls = {"n": 0}

    def bad_loader():
        calls["n"] += 1
        raise ConnectionError("中转站不可达")

    ledger = UsageLedger(db, price_loader=bad_loader)
    for _ in range(3):
        ledger.record({"model": "m", "kind": "chat",
                       "prompt_tokens": 10, "completion_tokens": 10, "latency_ms": 1})
    assert db.query_one("SELECT COUNT(*) AS n FROM llm_calls")["n"] == 3
    assert calls["n"] == 1  # 首次失败后进入节流窗,不是每条流水都拉一次


def test_ledger_price_cache_fetches_once(db):
    calls = {"n": 0}

    def loader():
        calls["n"] += 1
        return {"m": (1.0, 1.0)}

    ledger = UsageLedger(db, price_loader=loader)
    for _ in range(3):
        ledger.record({"model": "m", "kind": "chat",
                       "prompt_tokens": 10, "completion_tokens": 10, "latency_ms": 1})
    assert calls["n"] == 1


def test_usage_context_binds_session_and_run(db):
    ledger = UsageLedger(db, price_loader=lambda: {})
    with usage_context("sess-1", "run-9"):
        ledger.record({"model": "m", "kind": "chat",
                       "prompt_tokens": 1, "completion_tokens": 1, "latency_ms": 1})
    ledger.record({"model": "m", "kind": "chat",
                   "prompt_tokens": 1, "completion_tokens": 1, "latency_ms": 1})
    rows = db.query("SELECT session_id, run_id FROM llm_calls ORDER BY id")
    assert (rows[0]["session_id"], rows[0]["run_id"]) == ("sess-1", "run-9")
    assert (rows[1]["session_id"], rows[1]["run_id"]) == (None, None)


# ---------------- UsageLedger:汇总 ----------------


def test_summary_totals_by_model_by_day_recent(db):
    ledger = UsageLedger(db, price_loader=lambda: {"a": (2.0, 8.0)})
    for i in range(3):
        ledger.record({"model": "a", "kind": "chat",
                       "prompt_tokens": 1000, "completion_tokens": 500,
                       "latency_ms": 10 + i})
    ledger.record({"model": "b", "kind": "vision",     # 无价格 → cost NULL
                   "prompt_tokens": 25, "completion_tokens": None, "latency_ms": 7})
    s = ledger.summary(days=7)

    assert s["total"]["calls"] == 4
    assert s["total"]["prompt_tokens"] == 3025
    assert s["total"]["completion_tokens"] == 1500      # NULL 不参与求和
    assert s["total"]["cost_est_cny"] == pytest.approx(0.018)  # 3 × 0.006,未知不编数

    models = {r["model"]: r for r in s["by_model"]}
    assert models["a"]["calls"] == 3 and models["a"]["cost_est_cny"] == pytest.approx(0.018)
    assert models["b"]["cost_est_cny"] is None

    assert len(s["by_day"]) == 1 and s["by_day"][0]["calls"] == 4
    assert time.strftime("%Y-%m-%d") == s["by_day"][0]["day"]  # 本地时区口径

    assert [r["model"] for r in s["recent"]] == ["b", "a", "a", "a"]  # 新在前
    assert set(s["recent"][0]) == set(_EXPECTED_COLUMNS)


def test_summary_days_window_filters_old_rows(db):
    ledger = UsageLedger(db, price_loader=lambda: {})
    ledger.record({"model": "new", "kind": "chat",
                   "prompt_tokens": 1, "completion_tokens": 1, "latency_ms": 1})
    with db.tx() as cur:  # 直接补一条 10 天前的旧流水
        cur.execute("INSERT INTO llm_calls (ts, model, kind) VALUES (?,?,?)",
                    (time.time() - 10 * 86400, "old", "chat"))
    s = ledger.summary(days=7)
    assert s["total"]["calls"] == 1
    assert [r["model"] for r in s["by_model"]] == ["new"]
    assert len(ledger.summary(days=30)["by_model"]) == 2
    # recent 是流水视角(不受 days 窗过滤),两条都在
    assert {r["model"] for r in s["recent"]} == {"new", "old"}


def test_summary_empty_ledger_shape(db):
    s = UsageLedger(db, price_loader=lambda: {}).summary()
    assert s["total"] == {"calls": 0, "prompt_tokens": None,
                          "completion_tokens": None, "cost_est_cny": None}
    assert s["by_model"] == [] and s["by_day"] == [] and s["recent"] == []


def test_summary_recent_capped_at_20(db):
    ledger = UsageLedger(db, price_loader=lambda: {})
    for i in range(25):
        ledger.record({"model": f"m{i}", "kind": "chat", "prompt_tokens": 1,
                       "completion_tokens": 1, "latency_ms": 1})
    s = ledger.summary()
    assert len(s["recent"]) == 20
    assert s["recent"][0]["model"] == "m24"  # 最新在前


# ---------------- API:/api/llm/usage 全链路 ----------------


def _fake_relay(req, timeout):
    """按 URL 分发的假中转站:/models 给价格表,/chat/completions 给带 usage 的应答。"""
    url = req.full_url
    if url.endswith("/models"):
        return FakeJSONResp({"data": [{
            "id": "m1", "supports_vision": False,
            "effective_input_price_per_million": 2.0,
            "effective_output_price_per_million": 8.0, "currency": "CNY"}]})
    return FakeJSONResp(chat_body(
        {"ok": True}, usage={"prompt_tokens": 1000, "completion_tokens": 500}))


def test_api_usage_end_to_end_shape(home, monkeypatch):
    """配置 → /api/llm/test(urlopen 打桩)→ /api/llm/usage 反映流水与成本。"""
    monkeypatch.setattr(provider_mod.urllib.request, "urlopen", _fake_relay)
    with TestClient(create_app(home=home)) as client:
        client.post("/api/llm/config", json={
            "base_url": "https://fake-relay/v1", "api_key": "sk_test",
            "chat_model": "m1"})
        assert client.post("/api/llm/test", json={"kind": "chat"}).json()["ok"] is True

        out = client.get("/api/llm/usage?days=7").json()
        assert set(out) == {"days", "total", "by_model", "by_day", "by_session", "recent"}
        assert out["total"]["calls"] == 1
        assert out["total"]["prompt_tokens"] == 1000
        assert out["total"]["completion_tokens"] == 500
        assert out["total"]["cost_est_cny"] == pytest.approx(0.006)  # 价格表来自 /models
        assert out["by_model"][0]["model"] == "m1"
        assert out["recent"][0]["kind"] == "chat"
        assert out["recent"][0]["cost_est"] == pytest.approx(0.006)


def test_api_usage_days_validation(home, monkeypatch):
    monkeypatch.setattr(provider_mod.urllib.request, "urlopen", _fake_relay)
    with TestClient(create_app(home=home)) as client:
        assert client.get("/api/llm/usage?days=0").status_code == 422
        assert client.get("/api/llm/usage?days=91").status_code == 422
        empty = client.get("/api/llm/usage").json()
        assert empty["days"] == 7 and empty["total"]["calls"] == 0


def test_router_without_ledger_returns_empty_shape(home):
    """create_llm_router 不接账本(独立挂载形态)→ /usage 给同形空响应。"""
    from fastapi import FastAPI

    from insar_agent.api.llm_router import create_llm_router

    app = FastAPI()
    app.include_router(create_llm_router(home))
    with TestClient(app) as client:
        out = client.get("/api/llm/usage").json()
        assert out["total"]["calls"] == 0 and out["recent"] == []
