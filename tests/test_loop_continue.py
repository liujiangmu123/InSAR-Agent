"""自主循环:观察后不停、开工预检、install_engine 走执行器。"""

from __future__ import annotations

import subprocess

from test_agent_loop import collect, cy, empty_probe, make_driver, FakeCycleBrain
from insar_agent.runtime.install_guide import ENGINE_ORDER
from insar_agent.runtime.probe import ProbeResult


def _ready_probe():
    engines = {e: None for e in ENGINE_ORDER}
    engines.update({"mintpy": "1.6.4", "gdal": "3.13", "pyaps": "0.3.7",
                    "isce2 (wsl)": "2.6.5", "snaphu (wsl)": "2.0.6"})
    return ProbeResult(engines=engines, disk_free_gb=100.0, cpu_count=8,
                       wsl={"installed": True, "distros": ["insar"]})


def test_work_goal_refuses_stop_after_check_env(store, workspace):
    """分析任务在 check_env 之后 say 不得停机,必须继续动作。"""
    brain = FakeCycleBrain([
        cy(action={"type": "check_env"}, say="先看环境"),
        cy(say="环境看完了,收工。", done=True),
        cy(action={"type": "list_data"}, say="接着盘点"),
        cy(say="数据也看过了,下一步请你决定。", done=True),
    ])
    driver = make_driver(store, workspace, brain=brain, probe=_ready_probe())
    events = collect(driver.converse_loop("s1", "帮我分析 Ridgecrest 地震形变"))
    notes = [e["text"] for e in events if e["t"] == "note"]
    assert any("继续" in t for t in notes)
    kinds = [e["action"] for e in events if e["t"] == "agent.cycle"]
    assert "check_env" in kinds
    assert "list_data" in kinds
    assert events[-1]["t"] == "say"


def test_env_only_question_may_stop_after_check(store, workspace):
    brain = FakeCycleBrain([
        cy(action={"type": "check_env"}, say=""),
        cy(say="环境就是这样。", done=True),
    ])
    driver = make_driver(store, workspace, brain=brain, probe=_ready_probe())
    events = collect(driver.converse_loop("s1", "环境如何"))
    assert events[-1]["t"] == "say"
    assert not any(e["t"] == "note" and "继续工作" in e.get("text", "") for e in events)


def test_preflight_runs_check_env_before_llm(store, workspace):
    brain = FakeCycleBrain([cy(say="就绪。", done=True)])
    driver = make_driver(store, workspace, brain=brain, probe=_ready_probe(),
                         preflight_env=True, allow_auto_install=False)
    events = collect(driver.converse_loop("s1", "环境如何"))
    tools = [e for e in events if e["t"] == "tool.start"]
    assert tools and tools[0]["cmd"] == "check_env"
    assert brain.calls  # 预检之后仍问 LLM


def test_install_engine_action_calls_runner(store, workspace, monkeypatch):
    from insar_agent.runtime import install_runner

    def fake_run(engine, **kwargs):
        return {"ok": True, "engine": engine, "kind": "conda",
                "summary": f"{engine} 安装成功(rc=0):ok", "rc": 0}

    monkeypatch.setattr(install_runner, "run_install", fake_run)
    brain = FakeCycleBrain([
        cy(action={"type": "install_engine", "engine": "gdal"}, say="开始装"),
        cy(say="装完了。", done=True),
    ])
    driver = make_driver(store, workspace, brain=brain, probe=empty_probe())
    events = collect(driver.converse_loop("s1", "把缺的装上"))
    ends = [e for e in events if e["t"] == "tool.end"]
    assert any("安装成功" in (e.get("summary") or "") for e in ends)


def test_work_goal_extends_budget_instead_of_hard_stop(store, workspace):
    """分析任务在软上限用尽后继续探索,不会「刚热身就收工」。"""
    script = [
        cy(action={"type": "check_env"}, say="先看环境"),
        cy(action={"type": "list_data"}, say="再看数据"),
        cy(action={"type": "learn_tool", "tool": "gdal"}, say="补知识"),
        cy(action={"type": "plan", "scenario": "quake"}, say="开始规划"),
        cy(say="计划已出,等你决定是否执行。", done=True),
    ]
    brain = FakeCycleBrain(script)
    driver = make_driver(store, workspace, brain=brain, probe=_ready_probe())
    events = collect(driver.converse_loop(
        "s1", "帮我分析 Ridgecrest 地震形变", max_cycles=3))
    notes = [e.get("text", "") for e in events if e["t"] == "note"]
    assert any("预算" in t and "3→" in t for t in notes)
    kinds = [e["action"] for e in events if e["t"] == "agent.cycle"]
    assert "learn_tool" in kinds
    assert "plan" in kinds
    assert not any("周期上限" in t for t in notes)


def test_idle_lookup_still_stops_at_soft_cap(store, workspace):
    brain = FakeCycleBrain([
        cy(action={"type": "status"}, say=""),
        cy(action={"type": "check_env"}, say=""),
        cy(action={"type": "list_data"}, say=""),
    ])
    driver = make_driver(store, workspace, brain=brain)
    events = collect(driver.converse_loop("s1", "把能查的都查一遍", max_cycles=3))
    assert events[-1]["t"] == "note" and "周期上限" in events[-1]["text"]


def test_list_files_without_project(store, workspace):
    brain = FakeCycleBrain([
        cy(action={"type": "list_files"}, say=""),
        cy(say="没项目。", done=True),
    ])
    driver = make_driver(store, workspace, brain=brain)
    events = collect(driver.converse_loop("s1", "看看项目里有什么文件"))
    ends = [e for e in events if e["t"] == "tool.end"]
    assert ends and "未绑定项目" in ends[0]["summary"]
