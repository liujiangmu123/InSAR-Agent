# -*- coding: utf-8 -*-
"""landslide 场景全旅程深验(PS 链场景的诚实边界与全链贯通)。

旅程矩阵(本文件的断言地图):

  J1 意图与计划    turn「雅鲁藏布江滑坡区做 PS 点监测并与 GNSS 对比」
                   → 规则层命中 landslide;第 7 步覆写 pystamps_ps;第 9 步 linear。
  J2 可行性收窄    密封 probe 无 pystamps 时:
                   a) 有 mintpy、禁 simulated → 第 7 步降级 mintpy_sbas
                      (覆写不可兑现,narrowed 留痕,不进 problems —— 现状:静默但留痕);
                   b) 全缺、禁 simulated → 第 7 步无可行方法,problems 呈现,
                      run 停在 planning,turn 给出「模拟模式」降级出口;
                   c) 允许 simulated → pystamps_ps 以 simulated=True 放行,
                      blocked_reason 留痕;scenario_only 约束不因 simulated 放宽。
  J3 诚实边界      ISCE2→PyStamps 桥显式拒绝执行:输入缺失 → EnvironmentNotReady,
                   输入就绪 → NotImplementedError(Phase 6)。绝不产出假桥产物。
  J4 全链模拟执行  11 步全 done;provenance.simulated;证据封顶 runnable
                   (演示不冒充证据);methods.md 如实描述 PS 与模拟性质。
  J5 GNSS 对比语义 crossval 指标(qa.json 演示值)如实呈现 + PENDING 阈值披露;
                   gnss_rmse_mm 诚实缺席(calibrated 级要求在证据边界可见);
                   SKILL 知识段的 Colesanti & Wasowski 锚点进 methods
                   (绑定在实际执行的 pystamps_ps 上,数字只进 ref 锚点)。
  J6 边界          混合关键词「滑坡区的地震形变」按 priority 消解(quake 10 先于
                   landslide 30,当前行为如此);ask 表单 scenario 选项 = 技能包闭集,
                   回填 landslide 后 turn 正常规划。

诚实纪律:PS 链未建成是已知边界(桥 NotImplemented),本文件只按现状断言,
不为过测试伪造桥实现;降级行为的合理性评估写在各测试 docstring 里。
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from insar_agent.api.app import create_app
from insar_agent.brain.facade import Brain
from insar_agent.engines.bridges import isce2_to_pystamps as bridge
from insar_agent.loop.driver import Driver
from insar_agent.planner.feasibility import narrow_methods
from insar_agent.planner.plan import make_plan
from insar_agent.planner.score import pick_method
from insar_agent.registry.capabilities import REGISTRY, capability_of
from insar_agent.registry.scenarios import SCENARIOS, classify_text, scenario_of
from insar_agent.report.methods import methods_markdown
from insar_agent.runtime.probe import ProbeResult

TURN_TEXT = "雅鲁藏布江滑坡区做 PS 点监测并与 GNSS 对比"

_ALL_ENGINES = ("isce2", "mintpy", "snaphu", "gdal", "snap", "pystamps", "pyaps")


def _probe(**present: str) -> ProbeResult:
    """密封探测:全部引擎缺失,present 里的显式给版本号。"""
    engines: dict[str, str | None] = {k: None for k in _ALL_ENGINES}
    engines.update(present)
    return ProbeResult(
        engines=engines,
        credentials={"earthdata": False, "cds": False, "gacos": False},
        disk_free_gb=100.0, cpu_count=8)


def _collect(agen) -> list[dict]:
    async def run():
        return [e async for e in agen]

    return asyncio.run(run())


# ---------------------------------------------------------------------------
# A. registry / 技能包声明一致性(J1 的静态前提)
# ---------------------------------------------------------------------------

def test_cap7_declares_pystamps_ps_and_pack_points_to_it():
    """cap7 声明 PS 方法,landslide 包的覆写指向它;第 9 步覆写 linear 同样在候选内。"""
    cap7 = capability_of(7)
    assert [m.id for m in cap7.methods] == ["mintpy_sbas", "pystamps_ps"]
    ps = cap7.method("pystamps_ps")
    assert ps.engine == "pystamps" and ps.requires_engines == ("pystamps",)
    assert "ISCE2→PyStamps 桥" in ps.why  # 依赖桥的事实写进 registry 声明

    sc = scenario_of("landslide")
    assert sc is not None and sc.chain == "PS" and sc.model == "linear"
    assert sc.pick_7 == "pystamps_ps"
    assert sc.step_overrides == {7: {"method": "pystamps_ps"}, 9: {"method": "linear"}}
    # 覆写的方法都必须在 registry 候选集内(只选不造)
    assert cap7.method(sc.step_overrides[7]["method"]) is not None
    assert capability_of(9).method(sc.step_overrides[9]["method"]) is not None
    # SKILL 知识段携带 methods 锚点的出处(J5 的源头)
    knowledge = sc.knowledge()
    assert "Colesanti & Wasowski" in knowledge
    assert "LOS" in knowledge  # 投影假设纪律的知识依据


# ---------------------------------------------------------------------------
# B. 意图:规则层命中与优先级消解(J1 / J6)
# ---------------------------------------------------------------------------

def test_intent_rules_hit_landslide():
    sc = classify_text(TURN_TEXT)
    assert sc is not None and sc.key == "landslide"
    # 三个关键词各自都能命中(match: 滑坡|landslide|雅鲁藏布)
    assert classify_text("滑坡监测").key == "landslide"
    assert classify_text("landslide monitoring").key == "landslide"
    assert classify_text("雅鲁藏布江下游形变").key == "landslide"


def test_mixed_keywords_resolve_by_scenario_priority():
    """混合文本按技能包 priority 消解(数值小者先试)。

    当前行为:「滑坡区的地震形变」命中 quake —— quake(priority 10)先于
    landslide(priority 30)。这是硬编码时代语义的延续(scenarios.load_scenarios
    docstring),对「滑坡诱发的地震形变」类文本偏向事件驱动场景,行为可解释。
    连带记录:「青藏高原的滑坡」命中 permafrost(20 < 30)—— 区域词先于对象词,
    是规则层的已知边界,无法判定时应交 LLM/表单。
    """
    keys = [s.key for s in SCENARIOS]
    assert keys == ["stripmap_coseismic", "quake", "volcano", "permafrost",
                    "subsidence", "landslide"]
    assert classify_text("滑坡区的地震形变").key == "quake"
    assert classify_text("青藏高原的滑坡").key == "permafrost"
    # 无更高优先级关键词时,滑坡文本回到 landslide
    assert classify_text("川藏交界滑坡群普查").key == "landslide"


# ---------------------------------------------------------------------------
# C. J1:turn 规划回合(driver 级,密封 probe + 允许 simulated)
# ---------------------------------------------------------------------------

def test_turn_hits_landslide_and_applies_overrides(store, workspace):
    driver = Driver(store, workspace=workspace, probe=_probe(),
                    allow_simulated=True, brain=Brain(None))
    events = _collect(driver.turn("j1", TURN_TEXT))
    kinds = [e["t"] for e in events]

    # 意图叙述:场景与来源、PS 链目标进 thinking
    thinking = next(e for e in events if e["t"] == "thinking")
    assert "场景:滑坡点状目标(来源:rules)" in thinking["body"]
    assert "目标:PS 时序形变" in thinking["body"]

    # 计划落库:场景 + 两处覆写兑现
    run = store.latest_run("j1")
    assert run is not None and run["scenario"] == "landslide"
    steps = {s.step_id: s for s in store.load_steps(run["run_id"])}
    assert steps[7].method == "pystamps_ps"
    assert steps[9].method == "linear"

    # 引擎全缺 → 模拟横幅必须出现(诚实性),且回合以决策点收尾
    assert any(e["t"] == "note" and "模拟执行" in e["text"] and "runnable" in e["text"]
               for e in events)
    assert kinds[-1] == "candidates"
    say = next(e for e in events if e["t"] == "say")
    assert "第 1 步" in say["parts"][0]  # 决策点从首个待跑步骤开始


# ---------------------------------------------------------------------------
# D. J2:可行性收窄与降级行为
# ---------------------------------------------------------------------------

def test_narrow_methods_excludes_ps_without_engine():
    """narrow_methods 三种口径:全缺禁 sim / 有 mintpy 禁 sim / 允许 sim。"""
    cap7 = capability_of(7)

    # 全缺 + 禁 simulated:两个候选全被排除,选择器返回 None
    feas = narrow_methods(cap7, _probe(), scenario="landslide", allow_simulated=False)
    by = {f.method.id: f for f in feas}
    assert not by["pystamps_ps"].ok
    assert "工具链缺失:pystamps" in by["pystamps_ps"].blocked_reason
    assert not by["mintpy_sbas"].ok
    assert pick_method(feas, prefer="pystamps_ps") is None

    # 有 mintpy + 禁 simulated:场景覆写不可兑现 → 回落 recommend(SBAS)
    feas2 = narrow_methods(cap7, _probe(mintpy="present"), scenario="landslide",
                           allow_simulated=False)
    picked = pick_method(feas2, prefer="pystamps_ps")
    assert picked is not None and picked.method.id == "mintpy_sbas"
    assert not picked.simulated

    # 允许 simulated:PS 放行但 simulated=True,阻塞原因保留(不洗白)
    feas3 = narrow_methods(cap7, _probe(), scenario="landslide", allow_simulated=True)
    by3 = {f.method.id: f for f in feas3}
    assert by3["pystamps_ps"].ok and by3["pystamps_ps"].simulated
    assert "工具链缺失:pystamps" in by3["pystamps_ps"].blocked_reason
    assert pick_method(feas3, prefer="pystamps_ps").method.id == "pystamps_ps"


def test_plan_degrades_to_sbas_when_only_mintpy_present(store, workspace):
    """J2a:pystamps 缺、mintpy 在、禁 simulated → 第 7 步降级 mintpy_sbas。

    合理性评估(按现状断言):
    - 降级只在 narrowed(面板 9 候选收窄)留痕,不进 problems,turn 层没有
      「场景要求的 PS 链被替换」的显式提示 —— 与 SKILL「当前按可行性收窄诚实
      降级」的自述一致,报告端也只描述实际执行的方法,不会谎称 PS;
    - 但对用户而言覆写落空是静默的,依赖面板 9 才能发现 —— 记为体验缺口
      (不在本任务修复范围,已写进任务报告)。
    """
    store.create_session("j2a", "j2a")
    plan = make_plan(store, "j2a", registry=REGISTRY, probe=_probe(mintpy="present"),
                     scenario=scenario_of("landslide"), workspace=str(workspace),
                     allow_simulated=False)
    s7 = next(s for s in plan.steps if s.step_id == 7)
    assert s7.method == "mintpy_sbas" and not s7.simulated
    narrowed = {row["method"]: row for row in s7.narrowed}
    assert narrowed["pystamps_ps"]["ok"] is False
    assert "工具链缺失:pystamps" in narrowed["pystamps_ps"]["reason"]
    assert narrowed["mintpy_sbas"]["ok"] is True
    # 降级不是 problem:第 7 步有可行方法;问题清单只报真正无解的步骤(3/4/6 等)
    assert not any("第 7 步" in p for p in plan.problems)
    assert any("第 3 步" in p for p in plan.problems)  # isce2 全缺,配准确实无解


def test_turn_blocks_honestly_when_nothing_available(store, workspace):
    """J2b:全缺 + 禁 simulated → problems 呈现第 7 步无解,run 停 planning。"""
    driver = Driver(store, workspace=workspace, probe=_probe(),
                    allow_simulated=False, brain=Brain(None))
    events = _collect(driver.turn("j2b", TURN_TEXT))
    bad = [e["text"] for e in events if e["t"] == "note" and e["tone"] == "bad"]
    hit = [t for t in bad if "第 7 步" in t and "无可行方法" in t]
    assert hit and "pystamps" in hit[0] and "mintpy" in hit[0]  # 两个候选的排除理由都可见
    say = [e for e in events if e["t"] == "say"][-1]
    assert "环境不满足执行条件" in say["parts"][0]
    assert "模拟模式" in say["parts"][0]  # 降级出口如实给出
    assert not any(e["t"] == "candidates" for e in events)  # 不进决策点
    run = store.latest_run("j2b")
    assert run["status"] == "planning"  # 拒绝带病 ready


def test_plan_simulated_keeps_ps_override_with_honest_marks(store, workspace):
    """J2c:允许 simulated 时覆写兑现,但 simulated/blocked_reason 全程留痕;
    scenario_only 的场景约束不因演示模式放宽(第 9 步 quake/permafrost 专属
    方法仍被排除)。"""
    store.create_session("j2c", "j2c")
    plan = make_plan(store, "j2c", registry=REGISTRY, probe=_probe(),
                     scenario=scenario_of("landslide"), workspace=str(workspace),
                     allow_simulated=True)
    assert plan.runnable() and plan.simulated
    s7 = next(s for s in plan.steps if s.step_id == 7)
    assert s7.method == "pystamps_ps" and s7.simulated
    n7 = {row["method"]: row for row in s7.narrowed}
    assert n7["pystamps_ps"]["simulated"] is True
    assert "工具链缺失:pystamps" in n7["pystamps_ps"]["reason"]

    s9 = next(s for s in plan.steps if s.step_id == 9)
    assert s9.method == "linear"
    n9 = {row["method"]: row for row in s9.narrowed}
    assert n9["step"]["ok"] is False and "场景不匹配" in n9["step"]["reason"]
    assert n9["poly_periodic"]["ok"] is False and "场景不匹配" in n9["poly_periodic"]["reason"]


# ---------------------------------------------------------------------------
# E. J3:ISCE2→PyStamps 桥的诚实边界(Phase 6,不伪实现)
# ---------------------------------------------------------------------------

def test_isce2_pystamps_bridge_refuses_honestly(tmp_path):
    """桥的两级显式拒绝:输入缺失 → EnvironmentNotReady;输入就绪 →
    NotImplementedError(Phase 6 需真实 ISCE2 产物验证,不允许无真值猜测实现)。
    本测试按现状锁定边界 —— 桥一旦真正实现,这里应当同步改写。"""
    assert bridge.check_ready(tmp_path) == list(bridge.REQUIRED_INPUTS)
    with pytest.raises(bridge.EnvironmentNotReady) as not_ready:
        bridge.convert(tmp_path)
    assert "桥前置输入缺失" in str(not_ready.value)

    for rel in bridge.REQUIRED_INPUTS:
        (tmp_path / rel).mkdir(parents=True)
    assert bridge.check_ready(tmp_path) == []
    with pytest.raises(NotImplementedError) as todo:
        bridge.convert(tmp_path)
    assert "Phase 6" in str(todo.value)
    # 计划产物形状已声明(par/diff/base 三件套),实现前不落任何一件
    assert any(p.endswith(".par") for p in bridge.PLANNED_OUTPUTS)
    assert not list(tmp_path.rglob("*.diff"))


# ---------------------------------------------------------------------------
# F. J4/J5:API 全旅程(模拟执行 → done → methods.md)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client(tmp_path_factory):
    """密封环境(module 级复用:11 步模拟旅程只跑一次,全部断言共享)。"""
    mp = pytest.MonkeyPatch()
    mp.setattr("insar_agent.loop.driver.probe_environment",
               lambda *a, **k: _probe())
    for var in ("INSAR_LLM_BASE_URL", "INSAR_LLM_MODEL",
                "INSAR_LLM_FALLBACK_BASE_URL", "INSAR_LLM_FALLBACK_MODEL"):
        mp.delenv(var, raising=False)
    mp.setenv("INSAR_ALLOW_SIMULATED", "1")
    app = create_app(home=tmp_path_factory.mktemp("home"))
    with TestClient(app) as c:
        yield c
    mp.undo()


def _stream(client: TestClient, url: str, body: dict) -> list[dict]:
    events: list[dict] = []
    with client.stream("POST", url, json=body) as resp:
        assert resp.status_code == 200
        for line in resp.iter_lines():
            if line.strip():
                events.append(json.loads(line))
    return events


@pytest.fixture(scope="module")
def journey(client):
    client.post("/api/sessions", json={"id": "ls", "name": "滑坡旅程", "mode": "expert"})
    turn_events = _stream(client, "/api/turn", {"session": "ls", "text": TURN_TEXT})
    run_events = _stream(client, "/api/pipeline", {"session": "ls"})
    state = client.get("/api/state", params={"session": "ls"}).json()
    prov = client.get("/api/provenance", params={"session": "ls"}).json()
    md_resp = client.get("/api/methods.md", params={"session": "ls"})
    assert md_resp.status_code == 200
    return SimpleNamespace(turn=turn_events, run=run_events, state=state,
                           prov=prov, md=md_resp.text)


def test_full_simulated_chain_reaches_done(journey):
    """J4:landslide 无云端跳过 → 11 步全部本地(模拟)执行到 done。"""
    started = [e["stepId"] for e in journey.run if e["t"] == "step.start"]
    ended = {e["stepId"]: e["exit"] for e in journey.run if e["t"] == "step.end"}
    assert started == list(range(1, 12))  # 全链 11 步,拓扑序
    assert set(ended) == set(started) and all(x == 0 for x in ended.values())
    pcts = [e["pct"] for e in journey.run if e["t"] == "overall"]
    assert pcts and pcts[-1] == 100
    assert any(e["t"] == "result" for e in journey.run)

    run = journey.state["run"]
    assert run["status"] == "done" and run["scenario"] == "landslide"
    assert run["simulated"] == 1
    by_id = {s["id"]: s for s in journey.state["steps"]}
    assert by_id[7]["method"] == "pystamps_ps" and by_id[7]["state"] == "done"
    assert by_id[9]["method"] == "linear" and by_id[9]["state"] == "done"
    # 指标重解析必须干净(qa.json 演示值与账本一致)
    assert not any(e["t"] == "note" and "指标重解析" in e.get("text", "")
                   for e in journey.run)


def test_evidence_capped_runnable_with_honest_reasons(journey):
    """J4:证据阶梯封顶 runnable(演示不冒充证据),原因如实入账。"""
    prov = journey.prov
    assert prov["simulated"] is True and prov["scenario"] == "landslide"
    ev = prov["evidence"]
    assert ev["level"] == "runnable" and ev["ceiling"] == "runnable"
    assert "模拟执行" in ev["ceiling_reason"]
    assert any("无 GNSS/水准外部比对记录" in r for r in ev["reasons"])
    assert any(r.startswith("封顶 runnable") for r in ev["reasons"])
    # 全部 11 步的证据来源都是本地执行(无云端/无继承)
    assert {v["origin"] for v in ev["step_sources"].values()} == {"local"}
    assert len(ev["step_sources"]) == 11


def test_methods_md_describes_ps_with_knowledge_anchor(journey):
    """J5:methods.md 如实描述 PS 链;SKILL 知识段的 Colesanti & Wasowski
    锚点与 LOS 投影假设纪律进入方法章节(绑定实际执行的 pystamps_ps)。"""
    md = journey.md
    assert "**模拟执行**" in md  # 头部横幅:演示性质置顶声明
    assert "场景:landslide" in md
    assert "永久散射体(PS)方法反演" in md and "`pystamps_ps`" in md
    assert "〔prov-7〕" in md
    assert "Ferretti" in md
    assert "Colesanti & Wasowski" in md  # SKILL 知识段锚点(J5 核心断言)
    assert "投影假设" in md              # LOS → 坡向投影纪律进报告
    # 第 9 步 linear 的期刊化措辞(蠕滑准匀速)
    assert "`linear`" in md and "线性速率拟合" in md
    # 诚实性:没跑 SBAS 反演就绝不描述它(降级选择如实呈现的另一面)
    assert "小基线集(SBAS)方法反演" not in md


def test_methods_md_crossval_and_gnss_semantics(journey):
    """J5:crossval 指标可溯呈现 + PENDING 披露;GNSS 比对诚实缺席。

    「与 GNSS 对比」的用户意图在模拟旅程里无法兑现 —— 正确形态不是编一个
    gnss_rmse_mm,而是:指标缺席 + 证据边界写明 calibrated 级要求什么。
    """
    md, prov = journey.md, journey.prov
    m = prov["metrics"]
    # qa.json 演示指标入账并重解析通过(结构演示值,非科学结论)
    assert m["crossval_r"]["value"] == 0.92 and m["crossval_r"]["reparsed_ok"] is True
    assert m["crossval_r"]["source_artifact"] == "qa.json"
    assert "gnss_rmse_mm" not in m  # 诚实缺席:没做过就不入账
    # 呈现:双链交叉验证句 + 指标级引用 + 阈值锚点(PENDING 待标定)
    assert "PS/SBAS 双链交叉验证相关系数为 0.92〔prov-qa.json#crossval_r〕" in md
    assert "0.85" in md and "PENDING 待标定" in md
    assert "corr_threshold" in md  # 待标定阈值清单披露
    assert "双链重叠区速度差 RMSE 为 3.1 mm" in md and "Terrafirma" in md
    # GNSS 语义:方法章节不得出现伪造的 GNSS 比对句;证据边界写明差什么
    assert "InSAR−GNSS 时序 RMSE" not in md
    assert "尚未达到 calibrated(该级要求:外部比对指标(GNSS/水准,gnss_rmse_mm)入账)" in md
    # 用户的 GNSS 意图进入账本(intent 原文可溯)
    assert "GNSS" in json.dumps(prov["intent"], ensure_ascii=False)


def test_methods_markdown_honest_for_degraded_sbas_choice():
    """J5 补充(纯函数):第 7 步若降级为 mintpy_sbas,方法章节描述 SBAS
    而非 PS —— methods.md 只忠于实际执行的方法,降级选择不会被美化。"""
    base = {"steps": {"7": {"name": "时序反演", "state": "done",
                            "params": {"network": "small_baseline"}}},
            "artifacts": {}, "metrics": {}}
    base["steps"]["7"]["method"] = "mintpy_sbas"
    md_sbas = methods_markdown(base)
    assert "小基线集(SBAS)方法反演" in md_sbas
    assert "永久散射体" not in md_sbas

    base["steps"]["7"]["method"] = "pystamps_ps"
    md_ps = methods_markdown(base)
    assert "永久散射体(PS)方法反演" in md_ps
    assert "Colesanti & Wasowski" in md_ps
    assert "小基线集" not in md_ps


# ---------------------------------------------------------------------------
# G. J6:ask 表单路径(scenario 字段回填后 turn 正常)
# ---------------------------------------------------------------------------

def test_ask_form_scenario_backfill_then_turn_works(client):
    client.post("/api/sessions", json={"id": "ask-ls"})
    # 无场景关键词 + 无 LLM → 规则层无法判定 → ask 表单
    events = _stream(client, "/api/turn", {"session": "ask-ls", "text": "帮我做个形变分析"})
    assert events and events[-1]["t"] == "ask"
    fields = {f["key"]: f for f in events[-1]["fields"]}
    # 场景选项 = 已加载技能包闭集(此前硬编码漏 stripmap_coseismic,已修)
    assert fields["scenario"]["options"] == [s.key for s in SCENARIOS]
    assert "landslide" in fields["scenario"]["options"]
    # ask 不落 run(规划未发生)
    assert client.get("/api/state", params={"session": "ask-ls"}).json()["run"] is None

    # 回填 scenario 字段(前端语义:用户在输入框补充后重发)→ 正常规划
    backfill = _stream(client, "/api/turn",
                       {"session": "ask-ls", "text": "场景:landslide,雅鲁藏布江流域"})
    kinds = [e["t"] for e in backfill]
    assert "ask" not in kinds
    for t in ("thinking", "plan", "say", "candidates"):
        assert t in kinds, f"回填后规划回合缺 {t} 事件"
    state = client.get("/api/state", params={"session": "ask-ls"}).json()
    assert state["run"]["scenario"] == "landslide"
    assert {s["id"]: s["method"] for s in state["steps"]}[7] == "pystamps_ps"
