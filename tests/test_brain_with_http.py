"""facade 四职责 × 真实 HTTP:intent / select / triage / narrate 全部走本地 mock 服务器。

test_brain.py 用 FakeProvider 覆盖 facade 逻辑;本文件不再绕过网络,把
「facade → provider → HTTP → mock → 解析 → 值域校验/降级」整条链端到端钉死。
mock 基建复用 test_provider_http(MockLLMServer / openai_body)。
"""

from __future__ import annotations

import pytest
from test_provider_http import MockLLMServer, openai_body

from insar_agent.brain.facade import Brain
from insar_agent.brain.provider import LLMProvider
from insar_agent.core.failures import FailureClass
from insar_agent.planner.feasibility import narrow_methods
from insar_agent.registry.capabilities import REGISTRY
from insar_agent.report.methods import methods_markdown
from insar_agent.runtime.probe import ProbeResult


@pytest.fixture(autouse=True)
def _direct_connection(monkeypatch):
    """强制直连 127.0.0.1(与 test_provider_http 同理)。"""
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("no_proxy", "*")


@pytest.fixture()
def make_brain():
    """按脚本起 mock 服务器,返回 (Brain, server);测试结束统一关闭。"""
    servers: list[MockLLMServer] = []

    def _make(script: list[dict]) -> tuple[Brain, MockLLMServer]:
        srv = MockLLMServer(script)
        srv.start()
        servers.append(srv)
        return Brain(LLMProvider(routes=[srv.route()], timeout=10.0)), srv

    yield _make
    for s in servers:
        s.stop()


def full_probe() -> ProbeResult:
    return ProbeResult(engines={"snaphu": "2.0.7", "isce2": "2.6.5", "unw3d": None,
                                "mintpy": "1.6.4"},
                       credentials={})


def _feas6():
    return narrow_methods(REGISTRY[6], full_probe())


PROV = {"run_id": "r", "scenario": "quake", "simulated": True,
        "environment": {"python": "3.14", "platform": "win32", "tools": {}},
        # state=done:方法章节只为已执行步骤展开参数细节(2026-08 质量升级),
        # 本夹具的意图是「模板里确有数字」→ 必须是已执行步骤
        "steps": {"6": {"name": "解缠", "method": "snaphu_mcf", "state": "done",
                        "params": {"min_coherence": 0.25}}},
        "metrics": {}, "evidence": {"level": "runnable", "ladder": [], "reasons": []},
        "thresholds": {}}


# ---------------- intent ----------------

def test_intent_llm_scenario_end_to_end(make_brain):
    text = "帮我分析一批影像的地表形变"  # 规则层任何场景正则都不命中
    brain, srv = make_brain([{"json": openai_body({"scenario": "permafrost"})}])

    r = brain.intent(text)

    assert r.ok and r.scenario.key == "permafrost" and r.source == "llm"
    assert len(srv.requests) == 1
    body = srv.requests[0]["body"]
    assert body["messages"][1] == {"role": "user", "content": text}
    assert body["max_tokens"] == 64


def test_intent_llm_unknown_scenario_needs_form(make_brain):
    # LLM 返回不在场景闭集里的 key → 拒绝,落表单(不发明场景)
    brain, srv = make_brain([{"json": openai_body({"scenario": "我发明的场景"})}])

    r = brain.intent("帮我分析一批影像的地表形变")

    assert not r.ok and r.need_form and r.source == "form"
    assert len(srv.requests) == 1


# ---------------- select ----------------

def test_select_out_of_range_reasks_once_then_accepts(make_brain):
    ok = [f for f in _feas6() if f.ok]
    brain, srv = make_brain([
        {"json": openai_body({"choice": 99, "reason": "越界的第一答"})},
        {"json": openai_body({"choice": 1, "reason": "重问后命中"})},
    ])

    pick = brain.select(REGISTRY[6], _feas6())

    assert pick.source == "llm" and pick.method_id == ok[1].method.id
    assert len(srv.requests) == 2  # 越界 → 恰好重问一次
    first_user = srv.requests[0]["body"]["messages"][1]["content"]
    second_user = srv.requests[1]["body"]["messages"][1]["content"]
    assert "越界" not in first_user
    assert "choice=99" in second_user and "越界" in second_user  # 重问带纠错提示,不是复读


def test_select_out_of_range_twice_degrades_to_recommend(make_brain):
    brain, srv = make_brain([
        {"json": openai_body({"choice": 99})},
        {"json": openai_body({"choice": -1})},
    ])

    pick = brain.select(REGISTRY[6], _feas6())

    assert pick.source == "recommend" and pick.method_id == "snaphu_mcf"
    assert len(srv.requests) == 2  # 重问一次仍越界 → 降级,绝不第三问


def test_select_non_object_response_degrades_not_crashes(make_brain):
    """路由无视 response_format 返回 JSON 数组 → 必须走降级,不许 AttributeError 炸穿 facade。"""
    brain, srv = make_brain([{"json": openai_body([0, 1])}])

    pick = brain.select(REGISTRY[6], _feas6())

    assert pick.source == "recommend" and pick.method_id == "snaphu_mcf"


# ---------------- triage ----------------

def test_triage_llm_closed_set_end_to_end(make_brain):
    log = "步骤在中途停止,日志没有更多线索"  # 规则层全部不命中 → 进 LLM
    brain, srv = make_brain([{"json": openai_body({"class": "service_down"})}])

    r = brain.triage(log)

    assert r.failure_class is FailureClass.SERVICE_DOWN and r.source == "llm"
    assert len(srv.requests) == 1
    body = srv.requests[0]["body"]
    assert "service_down" in body["messages"][0]["content"]  # 闭集写进 system 提示
    assert log in body["messages"][1]["content"]  # 错误窗口带原文


def test_triage_llm_invented_class_falls_to_unknown(make_brain):
    brain, srv = make_brain([{"json": openai_body({"class": "我发明的新类别"})}])

    r = brain.triage("另一种没见过的失败输出")

    assert r.failure_class is FailureClass.UNKNOWN and r.source == "fallback"
    assert len(srv.requests) == 1


# ---------------- narrate ----------------

def test_narrate_rejects_number_tampering_end_to_end(make_brain):
    template = methods_markdown(PROV)
    assert "0.25" in template  # 前提:模板里确有该数字
    tampered = template.replace("0.25", "0.3") + "\n(润色版)"
    brain, srv = make_brain([{"json": openai_body({"markdown": tampered})}])

    md, source = brain.narrate(PROV)

    assert source == "template" and md == template  # 数字被改 → 拒绝润色,回退模板
    assert len(srv.requests) == 1
    assert srv.requests[0]["body"]["messages"][1]["content"].startswith("# 处理方法")


def test_narrate_accepts_number_preserving_polish(make_brain):
    template = methods_markdown(PROV)
    polished = template + "\n(以上为润色版,所有数字保持原样)"
    brain, srv = make_brain([{"json": openai_body({"markdown": polished})}])

    md, source = brain.narrate(PROV)

    assert source == "llm" and md == polished
