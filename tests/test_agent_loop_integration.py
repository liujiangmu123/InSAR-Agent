"""facade→driver 贯通验收(P1-1 / P2-10):循环动作从 LLM JSON 到外呼参数的全链。

组网(全程离线,零打桩业务代码):
  - LLM:tests/test_provider_chat.py 的 MockLLMServer(本地 HTTP,OpenAI 形状)
    + 真 LLMProvider + 真 Brain.cycle(闭集校验是真实的,不是替身);
  - ASF:tests/test_net_search.py 的 MockNetServer(本地 HTTP,jsonlite 形状)
    + 真 insar_agent.net 层(INSAR_ASF_SEARCH_BASE 指过去);
  - 驱动:真 Driver.converse_loop(空 probe,规则路径种 run)。

盯防的正是「桩绕过校验器」这类错位:search_data 的 region/timerange 必须经
facade 透传、由 driver 翻译为 asf_search 的 intersects_wkt/start/end 并落到
HTTP 查询参数(intersectsWith/start/end);inspect_file 的 step 形态必须过
真实校验器抵达 driver 的步骤分支。
"""

from __future__ import annotations

import json

import pytest

from insar_agent.brain.facade import Brain
from insar_agent.brain.provider import LLMProvider
from test_agent_loop import collect, make_driver, seed_ready_run
from test_net_search import MockNetServer
from test_provider_chat import MockLLMServer, chat_body

AOI_WKT = "POLYGON((100 0,101 0,101 1,100 1,100 0))"


@pytest.fixture(autouse=True)
def _sealed_env(tmp_path, monkeypatch):
    """密封:清代理防回环劫持;HOME/数据源指空;web/Tavily 通道钉死不出网
    (本文件的动作不带 query,不该有 web 外呼 —— 钉死是防御,不是依赖)。"""
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("no_proxy", "*")
    monkeypatch.setenv("INSAR_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("INSAR_DATA_DIR", raising=False)
    monkeypatch.delenv("INSAR_HYP3_SOURCE", raising=False)
    monkeypatch.delenv("INSAR_TAVILY_KEY", raising=False)
    monkeypatch.setenv("INSAR_WEBSEARCH_BASE", "http://127.0.0.1:9")
    monkeypatch.setenv("INSAR_TAVILY_BASE", "http://127.0.0.1:9")


@pytest.fixture()
def make_llm():
    """假 LLM HTTP 端点工厂(MockLLMServer);测试结束统一关闭。"""
    servers: list[MockLLMServer] = []

    def _make(script: list[dict]) -> MockLLMServer:
        s = MockLLMServer(script)
        s.start()
        servers.append(s)
        return s

    yield _make
    for s in servers:
        s.stop()


@pytest.fixture()
def make_net():
    """假 ASF HTTP 端点工厂(MockNetServer);测试结束统一关闭。"""
    servers: list[MockNetServer] = []

    def _make(script: list[dict]) -> MockNetServer:
        s = MockNetServer(script)
        s.start()
        servers.append(s)
        return s

    yield _make
    for s in servers:
        s.stop()


def cycle_reply(obj: dict) -> dict:
    """把期望的 cycle JSON 包成 OpenAI /chat/completions 应答脚本项。"""
    return {"json": chat_body(content=json.dumps(obj, ensure_ascii=False))}


def real_brain(llm: MockLLMServer) -> Brain:
    return Brain(LLMProvider(routes=[llm.route(model="loop-m")]))


# ---------------- search_data:region/timerange 全链翻译(P1-1) ----------------

def test_search_data_filters_reach_asf_http(store, workspace, make_llm, make_net,
                                            monkeypatch):
    asf = make_net([{"json": {"results": [{"granuleName": "S1A_A"},
                                          {"granuleName": "S1A_B"}]}}])
    monkeypatch.setenv("INSAR_ASF_SEARCH_BASE", asf.base_url)
    llm = make_llm([
        cycle_reply({"say": "先检索数据",
                     "action": {"type": "search_data", "region": AOI_WKT,
                                "timerange": "2019-06-01/2019-08-31"}}),
        cycle_reply({"say": "检索完成,收束。"}),
    ])
    driver = make_driver(store, workspace, brain=real_brain(llm))

    events = collect(driver.converse_loop("s1", "找 Ridgecrest 的数据"))

    end = next(e for e in events if e["t"] == "tool.end")
    assert end["exit"] == 0 and "ASF 检索:命中 2 条" in end["summary"]
    assert "注:" not in end["summary"]  # 过滤条件全部翻译成功,无丢弃注记
    assert events[-1]["t"] == "say"
    # ① facade:真实校验器透传 region/timerange;② driver:翻译为
    # intersects_wkt/start/end;③ net 层:落到 SearchAPI 的查询参数
    assert len(asf.requests) == 1
    q = asf.requests[0]["query"]
    assert q["intersectsWith"] == [AOI_WKT]
    assert q["start"] == ["2019-06-01"] and q["end"] == ["2019-08-31"]
    assert len(llm.requests) == 2  # 两个周期各一次真 LLM HTTP 调用
    # 周期摘要回灌:第二周期的 user 消息里有第一周期的 ASF 结果
    user2 = llm.requests[1]["body"]["messages"][1]["content"]
    assert "ASF 检索:命中 2 条" in user2


def test_search_data_non_wkt_region_omits_spatial_param(store, workspace, make_llm,
                                                        make_net, monkeypatch):
    """region 是地名(非 WKT):不上送 intersectsWith,摘要注记回灌 LLM(P1-1)。"""
    asf = make_net([{"json": {"results": []}}])
    monkeypatch.setenv("INSAR_ASF_SEARCH_BASE", asf.base_url)
    llm = make_llm([
        cycle_reply({"say": "查玉树",
                     "action": {"type": "search_data", "region": "玉树地区"}}),
        cycle_reply({"say": "收束。"}),
    ])
    driver = make_driver(store, workspace, brain=real_brain(llm))

    events = collect(driver.converse_loop("s1", "找玉树的数据"))

    q = asf.requests[0]["query"]
    assert "intersectsWith" not in q and "start" not in q and "end" not in q
    end = next(e for e in events if e["t"] == "tool.end")
    assert "非 WKT" in end["summary"] and "玉树地区" in end["summary"]
    user2 = llm.requests[1]["body"]["messages"][1]["content"]
    assert "非 WKT" in user2  # 被丢弃的过滤条件如实回灌,LLM 可换 WKT 重试


# ---------------- inspect_file:step 形态经真实 facade 可达(P2-10) ----------------

def test_inspect_file_step_form_reaches_driver_branch(store, workspace, make_llm):
    run = seed_ready_run(store, workspace)
    llm = make_llm([
        cycle_reply({"say": "看看第 1 步",
                     "action": {"type": "inspect_file", "step": 1}}),
        cycle_reply({"say": "看完了,收束。"}),
    ])
    driver = make_driver(store, workspace, brain=real_brain(llm))

    events = collect(driver.converse_loop("s1", "第 1 步什么情况"))

    end = next(e for e in events if e["t"] == "tool.end")
    assert end["exit"] == 0
    assert "第 1 步" in end["summary"]  # 此前该分支经真实 facade 不可达(死代码)
    assert store.load_step(run["run_id"], 1).state in end["summary"]
    assert "日志尾行" not in end["summary"]  # 未失败步骤不回灌日志(红线 §0.5)
    assert events[-1]["t"] == "say"
    assert len(llm.requests) == 2
