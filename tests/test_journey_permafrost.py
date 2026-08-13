"""permafrost 场景全旅程深验(青海玉树冻土 SBAS 时序,2020-2023)。

与 test_e2e_contract(quake 契约旅程)/ test_driver_matrix(缩减 6 步矩阵)的分工:
本文件把「冻土场景」当作一条完整用户旅程从头走到尾 —— quake 走 HyP3 云端跳步路线
(cloud_completed=2-6),permafrost 是唯一「全 11 步本地执行 + 周期形变模型」的路线,
覆盖了 quake 旅程测不到的分支:场景包 step_overrides 选中非 recommend 方法、
零跳步的证据链(step_sources 全 local)、周期模型进方法章节。

旅程矩阵(任务书 6 段 × 断言点):
  1. turn 规划    意图规则层命中 permafrost;step_overrides 生效(第 9 步 poly_periodic
                  + periods=[1,0.5] + poly_order=1,压过 registry recommend 的 linear);
                  无 cloud_completed → 11 步全 pending、零 skipped
  2. 全链执行    11 步全 done(拓扑序、exit=0、overall 闭合 100);第 9 步 params 带
                  周期项进 provenance;methods.md 含周期模型描述 + Daout 2017 锚点
  3. 干预        执行中(第 4 步窗口内)PAUSE → 当前步跑完即停(4 done / 5-11 pending)
                  → PLAY 续跑到完成,已完成步骤零重复启动
  4. impact      resource(threads)预览 → affected 空、零重跑;science
                  (max_temporal_baseline)预览 → 级联 7→11,且预览不落库
  5. 证据链      step_sources 11 步全 local;阈值台账 permafrost 收紧建议只进 ref
                  文本,不动全局门(max_temporal_baseline 仍 120,场景包也没改它)
  6. 边界        同文本二次 turn 复用既有 run(不新建、不重发 candidates);
                  无场景词的随机中文 → ask 表单(场景选项 = 技能包闭集),run 不受扰

发现并修复的真实 bug(触发场景:本文件边界段 ask 表单断言):
  loop/driver.py 的 ask 表单场景选项硬编码 ["quake","permafrost","landslide"],
  第四个技能包 stripmap_coseismic 加入后未同步 —— 表单闭集与已加载技能包脱节,
  用户无法从表单选到条带链场景。已改为从 SCENARIOS 动态生成。
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from insar_agent.api.app import create_app
from insar_agent.brain.facade import Brain
from insar_agent.loop.driver import Driver
from insar_agent.registry.scenarios import SCENARIOS, scenario_of
from insar_agent.runtime.probe import ProbeResult

TURN_TEXT = "分析青海玉树冻土 2020-2023 的 SBAS 时序形变"
#: 不含任何技能包 match 词(冻土/青海/玉树/青藏|地震/同震|滑坡|ALOS/条带…)的随机中文
UNRECOGNIZED_TEXT = "帮我把这批影像随便处理一下看看效果"
ALL_STEPS = tuple(range(1, 12))
SESSION = "pf-journey"


def empty_probe(*_args, **_kwargs) -> ProbeResult:
    return ProbeResult(
        engines={k: None for k in ("isce2", "mintpy", "snaphu", "gdal", "snap",
                                   "pystamps", "pyaps")},
        credentials={"earthdata": False, "cds": False, "gacos": False},
        disk_free_gb=100.0, cpu_count=8)


# ---------------------------------------------------------------------------
# 夹具:密封环境(空探针 + LLM 路由清空 → 规则意图/模板叙述;允许 simulated)。
# 模块级共享一次「turn → 全链执行」旅程:后续用例都在同一状态上做只读深验,
# 全链只跑一遍(11 步 × 子进程,函数级重复执行会把用时翻十倍)。
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setattr("insar_agent.loop.driver.probe_environment", empty_probe)
    for var in ("INSAR_LLM_BASE_URL", "INSAR_LLM_MODEL",
                "INSAR_LLM_FALLBACK_BASE_URL", "INSAR_LLM_FALLBACK_MODEL"):
        mp.delenv(var, raising=False)
    mp.setenv("INSAR_ALLOW_SIMULATED", "1")
    app = create_app(home=tmp_path_factory.mktemp("pf-home"))
    with TestClient(app) as c:
        yield c
    mp.undo()


def _stream(client: TestClient, url: str, body: dict) -> list[dict]:
    """消费 NDJSON 回合流(纪律与 e2e 契约一致:每行可解析且带类型字段)。"""
    events: list[dict] = []
    with client.stream("POST", url, json=body) as resp:
        assert resp.status_code == 200
        for line in resp.iter_lines():
            if not line.strip():
                continue
            event = json.loads(line)
            assert isinstance(event, dict) and "t" in event, f"事件缺类型字段: {line!r}"
            events.append(event)
    return events


class Journey:
    """一次完整旅程的现场:turn 事件、执行事件、两个时点的状态镜像。"""

    def __init__(self, client: TestClient):
        r = client.post("/api/sessions",
                        json={"id": SESSION, "name": "冻土旅程", "mode": "expert"})
        assert r.status_code == 200
        self.turn_events = _stream(client, "/api/turn",
                                   {"session": SESSION, "text": TURN_TEXT})
        self.state_planned = client.get("/api/state",
                                        params={"session": SESSION}).json()
        self.run_id = self.state_planned["run"]["run_id"]
        self.run_events = _stream(client, "/api/pipeline", {"session": SESSION})
        self.state_done = client.get("/api/state", params={"session": SESSION}).json()
        self.provenance = client.get("/api/provenance",
                                     params={"session": SESSION}).json()

    def planned_steps(self) -> dict[int, dict]:
        return {s["id"]: s for s in self.state_planned["steps"]}

    def done_steps(self) -> dict[int, dict]:
        return {s["id"]: s for s in self.state_done["steps"]}


@pytest.fixture(scope="module")
def journey(client) -> Journey:
    return Journey(client)


# ===========================================================================
# 1. turn 规划:意图命中 + step_overrides 生效 + 无 cloud_completed
# ===========================================================================

def test_intent_rules_hit_permafrost(journey):
    """规则层(零 LLM)从中文请求识别出冻土场景,thinking 叙述带场景来源与区域。"""
    thinking = next(e for e in journey.turn_events if e["t"] == "thinking")
    assert "冻土季节冻融" in thinking["body"]          # 断言点 1:场景标签
    assert "来源:rules" in thinking["body"]           # 断言点 2:规则层命中,非 LLM/表单
    assert "青海玉树" in thinking["body"] and "SBAS" in thinking["body"]
    assert journey.state_planned["run"]["scenario"] == "permafrost"  # 断言点 3:落库场景
    # 规划回合的骨架完整:计划 + 决策点 + 模拟执行诚实横幅
    kinds = [e["t"] for e in journey.turn_events]
    for t in ("thinking", "tool.start", "tool.end", "plan", "say", "candidates"):
        assert t in kinds, f"规划回合缺 {t} 事件: {kinds}"
    assert any(e["t"] == "note" and "模拟" in e["text"] for e in journey.turn_events)


def test_scenario_step_overrides_applied(journey):
    """场景包 overrides.yaml 生效:第 9 步压过 recommend 的 linear 选 poly_periodic,
    周期参数逐值进计划;第 7 步走 SBAS(mintpy_sbas)。"""
    steps = journey.planned_steps()
    assert steps[9]["method"] == "poly_periodic"       # 断言点 4:覆写压过 recommend
    assert steps[9]["params"]["periods"] == [1, 0.5]   # 断言点 5:年 + 半年周期
    assert steps[9]["params"]["poly_order"] == 1       # 断言点 6:线性趋势项
    assert steps[7]["method"] == "mintpy_sbas"         # 低相干面状 → SBAS 链
    # 决策点是第 1 步(全链待跑),say 给出 registry 推荐依据
    assert next(e for e in journey.turn_events if e["t"] == "candidates")["stepId"] == 1
    assert any(e["t"] == "say" and "local_import" in "".join(map(str, e["parts"]))
               for e in journey.turn_events)


def test_no_cloud_completed_full_chain_planned(journey):
    """permafrost 不带 cloud_completed:11 步全部 pending,零 skipped
    (对照:quake 场景 2-6 步由 HyP3 云端交付、计划即 skipped)。"""
    assert scenario_of("permafrost").cloud_completed == ()
    steps = journey.planned_steps()
    assert sorted(steps) == list(ALL_STEPS)            # 断言点 7:计划齐 11 步
    assert all(s["state"] == "pending" for s in steps.values())  # 断言点 8:全部待跑
    assert not any(s["state"] == "skipped" for s in steps.values())


# ===========================================================================
# 2. 全链执行:11 步全 done + 周期参数入账 + methods.md 周期模型叙述
# ===========================================================================

def test_full_chain_executes_all_eleven_steps(journey):
    """全 11 步按拓扑序执行、全部 exit=0,进度闭合 100,终态序 result → report。"""
    started = [e["stepId"] for e in journey.run_events if e["t"] == "step.start"]
    ended = {e["stepId"]: e["exit"] for e in journey.run_events if e["t"] == "step.end"}
    assert started == list(ALL_STEPS)                  # 断言点 9:全 11 步、拓扑序
    assert set(ended) == set(ALL_STEPS) and all(x == 0 for x in ended.values())
    assert {s["state"] for s in journey.state_done["steps"]} == {"done"}  # 断言点 10
    assert journey.state_done["run"]["status"] == "done"
    pcts = [e["pct"] for e in journey.run_events if e["t"] == "overall"]
    assert pcts == sorted(pcts) and pcts[-1] == 100
    kinds = [e["t"] for e in journey.run_events]
    last_end = max(i for i, t in enumerate(kinds) if t == "step.end")
    assert last_end < kinds.index("result") < kinds.index("report")
    # 第 9 步以周期模型方法启动(工具卡命令行可见)
    assert any(e["t"] == "tool.start" and e["id"] == "s9"
               and "poly_periodic" in e["cmd"] for e in journey.run_events)
    # 指标重解析一致(simulated 引擎产出与账本自洽,不出告警)
    assert not any(e["t"] == "note" and "指标重解析" in e.get("text", "")
                   for e in journey.run_events)


def test_step9_periodic_params_recorded_in_provenance(journey):
    """第 9 步的周期项参数全量进账本(方法/参数/状态可溯)。"""
    step9 = journey.provenance["steps"]["9"]
    assert step9["method"] == "poly_periodic"
    assert step9["params"]["periods"] == [1, 0.5]      # 断言点 11:账本周期项
    assert step9["params"]["poly_order"] == 1
    assert step9["state"] == "done" and step9["run_ok"] == 1
    assert journey.provenance["scenario"] == "permafrost"
    assert journey.provenance["simulated"] is True     # 引擎全缺:诚实标注
    assert journey.provenance["qa"]["status"] == "pass"


def test_methods_md_narrates_periodic_model_with_daout_anchor(client, journey):
    """methods.md(模板路径,无 LLM):周期模型描述 + Daout 2017 文献锚点 +
    场景包知识(半年项吸收非正弦不对称)+ 参数原值可反查。"""
    resp = client.get("/api/methods.md", params={"session": SESSION})
    assert resp.status_code == 200
    assert resp.headers["x-narrate-source"] == "template"  # LLM 关闭 → 模板保底
    md = resp.text
    assert "年/半年周期项" in md                        # 断言点 12:周期模型描述
    assert "Daout et al. (2017)" in md                 # 断言点 13:文献锚点
    assert "非正弦不对称" in md                         # 场景包知识段(冻融机理)已入
    assert "periods = [1, 0.5]" in md                  # 参数原值直排可反查
    assert "场景:permafrost" in md
    assert "模拟执行" in md                             # 诚实性:演示值不构成科学结论


# ===========================================================================
# 3. 干预:执行中 PAUSE → 当前步跑完即停 → PLAY 续跑到完成
#    (直连 Driver:事件逐个消费,干预注入时机完全确定,零 sleep 竞态)
# ===========================================================================

async def _drive(agen, on_event=None) -> list[dict]:
    events: list[dict] = []
    async for e in agen:
        events.append(e)
        if on_event is not None:
            on_event(e)
    return events


def test_pause_mid_run_stops_after_current_step_then_play_resumes(store, workspace):
    """PAUSE 在第 4 步启动后注入(steer 步间语义):第 4 步跑完才停,5-11 零启动;
    PLAY 后续跑恰好执行 5-11,全链完成且已完成步骤零重复命令。"""
    driver = Driver(store, workspace=workspace, probe=empty_probe(), poll=0.03,
                    startup_grace=15.0, allow_simulated=True, brain=Brain(None))
    turn_events = asyncio.run(_drive(driver.turn("pf-pause", TURN_TEXT)))
    assert any(e["t"] == "candidates" for e in turn_events)
    run_id = store.latest_run("pf-pause")["run_id"]
    assert store.get_run(run_id)["scenario"] == "permafrost"

    fired: dict = {}

    def inject_pause(e: dict) -> None:
        if e["t"] == "step.start" and e["stepId"] == 4 and "pause" not in fired:
            fired["pause"] = True
            store.push_action(scope="run", target=run_id, action="PAUSE",
                              deliver_as="steer", run_id=run_id)

    events = asyncio.run(_drive(driver.execute("pf-pause"), on_event=inject_pause))
    # 当前步(第 4 步)跑完才暂停:有 step.end(4),且 4 之后零 step.start
    assert any(e["t"] == "step.end" and e["stepId"] == 4 and e["exit"] == 0
               for e in events)                        # 断言点 14:当前步跑完
    started = [e["stepId"] for e in events if e["t"] == "step.start"]
    assert started == [1, 2, 3, 4]                     # 断言点 15:第 5 步未启动
    assert any(e["t"] == "intervention" and "暂停" in e["text"] for e in events)
    assert any(e["t"] == "note" and "已暂停" in e["text"] for e in events)
    assert not any(e["t"] in ("result", "report") for e in events)  # 暂停无终态卡
    assert store.get_run(run_id)["status"] == "paused"
    states = {s.step_id: s.state for s in store.load_steps(run_id)}
    assert all(states[sid] == "done" for sid in (1, 2, 3, 4))
    assert all(states[sid] == "pending" for sid in range(5, 12))
    for sid in range(5, 12):
        assert not store.commands_of(run_id, sid)      # 暂停后零新命令意图

    # PLAY 经队列消费 → 续跑到完成
    store.push_action(scope="run", target=run_id, action="PLAY",
                      deliver_as="steer", run_id=run_id)
    events2 = asyncio.run(_drive(driver.execute("pf-pause")))
    assert any(e["t"] == "intervention" and "恢复" in e["text"] for e in events2)
    resumed = [e["stepId"] for e in events2 if e["t"] == "step.start"]
    assert resumed == list(range(5, 12))               # 断言点 16:恰好续跑 5-11
    assert store.get_run(run_id)["status"] == "done"
    assert all(s.state == "done" for s in store.load_steps(run_id))
    # 全链每步恰一条命令:PAUSE/PLAY 没有造成任何重复启动
    assert {sid: len(store.commands_of(run_id, sid)) for sid in ALL_STEPS} \
        == {sid: 1 for sid in ALL_STEPS}               # 断言点 17:零重复启动


# ===========================================================================
# 4. impact 预览:resource 零重跑 / science 级联;预览不落库
# ===========================================================================

def test_impact_preview_resource_threads_zero_rerun(client, journey):
    """threads 是 resource 参数(不进任何指纹):影响预览为空、零重跑。"""
    imp = client.get("/api/impact", params={
        "session": SESSION, "step": 6,
        "params": json.dumps({"threads": 16})}).json()
    assert imp["changedStep"] == 6
    assert imp["reason"] == "no_change"                # 断言点 18:资源参数无影响
    assert imp["affected"] == []                       # 断言点 19:零重跑集合
    assert imp["rerunMinutes"] is None and imp["rerunBasis"] == "无待跑步骤"


def test_impact_preview_science_baseline_cascades_downstream(client, journey):
    """max_temporal_baseline 是第 7 步 science 参数:预览级联 7(param_changed)
    + 8-11(upstream_changed);纯预览不落库(状态镜像不变脏)。"""
    imp = client.get("/api/impact", params={
        "session": SESSION, "step": 7,
        "params": json.dumps({"max_temporal_baseline": 90})}).json()
    assert imp["changedStep"] == 7 and imp["reason"] == "param_changed"
    by_id = {a["step_id"]: a for a in imp["affected"]}
    assert sorted(by_id) == [7, 8, 9, 10, 11]          # 断言点 20:级联 = 自身 + 全下游
    assert by_id[7]["reason"] == "param_changed"       # 断言点 21:触发点原因
    assert all(by_id[sid]["reason"] == "upstream_changed"
               for sid in (8, 9, 10, 11))              # 断言点 22:下游原因
    assert all(a["state_before"] == "done" for a in imp["affected"])
    # 预览是只读的:run 仍全 done、零标脏
    state = client.get("/api/state", params={"session": SESSION}).json()
    assert all(s["state"] == "done" and not s["stale"] for s in state["steps"])


# ===========================================================================
# 5. 证据链:step_sources 全 local;阈值台账收紧建议不动全局门
# ===========================================================================

def test_evidence_step_sources_all_local(journey):
    """全本地执行(零云端跳步、零 fork 继承):11 步证据来源全部 local。"""
    srcs = journey.provenance["evidence"]["step_sources"]
    assert sorted(srcs, key=int) == [str(s) for s in ALL_STEPS]
    assert {entry["origin"] for entry in srcs.values()} == {"local"}  # 断言点 23
    assert not journey.provenance["evidence"]["parent_validations"]


def test_ledger_permafrost_tightening_stays_out_of_global_gate(journey):
    """台账纪律:permafrost 收紧建议(SKILL 调研知识)只进 ref 说明文字,
    全局门 max_temporal_baseline 值保持 120;场景包 overrides 也没碰第 7 步参数。"""
    th = journey.provenance["thresholds"]["max_temporal_baseline"]
    assert th["value"] == 120                          # 断言点 24:全局门不动
    assert th["status"] == "OK" and th["source"] == "literature"
    assert "收紧" in th["ref"] and "permafrost" in th["ref"]  # 断言点 25:建议在 ref
    # 场景包知识正文确有收紧依据(S1 惯例区间),但 overrides 只改第 9 步
    knowledge = scenario_of("permafrost").knowledge()
    assert "24–90" in knowledge and "Daout" in knowledge
    assert set(scenario_of("permafrost").step_overrides) == {9}
    # 本 run 第 7 步实际参数仍取台账全局门的宽松值
    assert journey.done_steps()[7]["params"]["max_temporal_baseline"] == 120


# ===========================================================================
# 6. 边界:同文本二次 turn 复用 run;无场景词中文 → ask 表单
# ===========================================================================

def test_same_text_second_turn_reuses_existing_run(client, journey):
    """同文本再次 turn:场景相同且 run 已 done → 复用,不新建 run、
    不再发 candidates(无待决策步骤),明确告知可 fork/导出。"""
    runs_before = [r for r in client.get("/api/admin/runs").json()
                   if r["session_id"] == SESSION]
    events = _stream(client, "/api/turn", {"session": SESSION, "text": TURN_TEXT})
    runs_after = [r for r in client.get("/api/admin/runs").json()
                  if r["session_id"] == SESSION]
    assert len(runs_after) == len(runs_before) == 1    # 断言点 26:不重复建 run
    state = client.get("/api/state", params={"session": SESSION}).json()
    assert state["run"]["run_id"] == journey.run_id    # 断言点 27:latest 仍是原 run
    assert not any(e["t"] == "candidates" for e in events)
    assert any(e["t"] == "say" and any("全部步骤已完成" in str(p) for p in e["parts"])
               for e in events)


def test_unrecognized_chinese_text_yields_ask_form(client, journey):
    """无场景词的中文:规则层不猜、LLM 关闭 → ask 表单;场景选项与技能包闭集
    一致(修复点:此前硬编码三场景,第四包 stripmap_coseismic 缺席);
    已有 run 不受扰动。"""
    events = _stream(client, "/api/turn",
                     {"session": SESSION, "text": UNRECOGNIZED_TEXT})
    assert events and events[-1]["t"] == "ask"          # 断言点 28:转表单
    ask = events[-1]
    assert ask["prompt"] and isinstance(ask["fields"], list)
    scenario_field = next(f for f in ask["fields"] if f.get("key") == "scenario")
    assert scenario_field["options"] == [s.key for s in SCENARIOS]  # 断言点 29:闭集同步
    assert "permafrost" in scenario_field["options"]
    assert {f.get("key") for f in ask["fields"]} >= {"scenario", "region", "dates"}
    # 表单回合不产生副作用:run 与步骤状态原样
    state = client.get("/api/state", params={"session": SESSION}).json()
    assert state["run"]["run_id"] == journey.run_id
    assert all(s["state"] == "done" for s in state["steps"])
