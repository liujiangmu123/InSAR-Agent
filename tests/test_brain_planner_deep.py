"""LLM 降级路径与规划正确性深测(brain/provider/facade × planner)。

盯防的核心设计约束(AGENT-DESIGN):
  - 拔掉 LLM(清空环境变量 / provider=None)系统仍可用:intent/select/triage/narrate
    四门面全部走规则路径,结构完整、reason 可读,且绝不发起任何网络请求;
  - LLM 只做候选集内选择(只选不造):候选之外的方法名、幻觉字段、类型越界
    (含 bool——Python 里 bool 是 int 子类)一律拒绝并降级到规则选择;
  - planner:收窄理由可解释、pick 决定性、fork 对 science/presentation/resource
    三类参数的失效传播边界精确(以 eval_hash/local_hash 级联断言)、
    cloud_completed 步骤标 skipped 且永不进执行队列。

与现有用例的分工:test_brain.py 用 FakeProvider 覆盖 facade 主逻辑;
test_provider_http.py / test_brain_with_http.py 用本地 HTTP 回环覆盖真实网络栈。
本文件专注:环境变量级降级完备性、假 urlopen 传输层语义、幻觉输入拒绝、
narrow/pick/fork/cloud 的规划正确性。全程无真实网络、无重型计算。
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

import pytest

from insar_agent.brain.facade import Brain
from insar_agent.brain.provider import (
    BrainTruncated,
    BrainUnavailable,
    LLMProvider,
    LLMRoute,
    routes_from_env,
)
from insar_agent.core.failures import FailureClass
from insar_agent.planner.feasibility import MethodFeasibility, narrow_methods
from insar_agent.planner.plan import fork_run, make_plan
from insar_agent.planner.score import pick_method
from insar_agent.registry.capabilities import REGISTRY
from insar_agent.registry.model import Method
from insar_agent.registry.scenarios import SCENARIOS, scenario_of
from insar_agent.runtime.probe import ProbeResult

LLM_ENV = (
    "INSAR_LLM_BASE_URL", "INSAR_LLM_API_KEY", "INSAR_LLM_MODEL",
    "INSAR_LLM_FALLBACK_BASE_URL", "INSAR_LLM_FALLBACK_API_KEY",
    "INSAR_LLM_FALLBACK_MODEL",
)

FULL_TOOLS = {"isce2": "2.6.5", "mintpy": "1.6.4", "snaphu": "2.0.7", "gdal": "3.8",
              "snap": "9", "pystamps": "0.3.4", "pyaps": "0.3.6"}


def probe_of(engines=None, credentials=None, disk_free_gb=500.0) -> ProbeResult:
    return ProbeResult(engines=dict(engines or {}), credentials=dict(credentials or {}),
                       disk_free_gb=disk_free_gb)


def full_probe() -> ProbeResult:
    return probe_of(FULL_TOOLS, {"earthdata": True, "cds": True, "gacos": False})


def empty_probe() -> ProbeResult:
    return probe_of({k: None for k in FULL_TOOLS},
                    {"earthdata": False, "cds": False, "gacos": False})


def _feas6():
    return narrow_methods(REGISTRY[6], full_probe())


def _ok_ids(feas) -> list[str]:
    return [f.method.id for f in feas if f.ok]


PROV = {"run_id": "r", "scenario": "quake", "simulated": True,
        "environment": {"python": "3.11", "platform": "win32", "tools": {}},
        # state=done:方法章节只为已执行步骤展开参数细节(2026-08 质量升级),
        # 本夹具的意图是「模板里确有数字」→ 必须是已执行步骤
        "steps": {"6": {"name": "解缠", "method": "snaphu_mcf", "state": "done",
                        "params": {"min_coherence": 0.25}}},
        "metrics": {}, "evidence": {"level": "runnable", "ladder": [], "reasons": []},
        "thresholds": {}}


class ScriptedProvider(LLMProvider):
    """按脚本返回 complete_json 结果的假 provider(绝不碰网络)。"""

    def __init__(self, responses):
        super().__init__(routes=[LLMRoute("http://scripted.invalid", "", "fake-model")])
        self._responses = list(responses)
        self.calls = 0

    def complete_json(self, *, system, user, max_tokens=512):
        self.calls += 1
        if not self._responses:
            raise BrainUnavailable("脚本用尽")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _FakeResponse:
    def __init__(self, doc):
        self._raw = json.dumps(doc, ensure_ascii=False).encode("utf-8")

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeTransport:
    """urllib.request.urlopen 的脚本化替身:每项对应一次请求。

    项为 dict → 作为 HTTP JSON 体应答;为 Exception → 抛出(模拟网络故障/超时)。
    脚本用尽还来请求 → 直接失败(暴露多余的网络调用)。
    """

    def __init__(self, script):
        self.script = list(script)
        self.requests: list[tuple[str, dict]] = []  # (url, 请求 payload)

    def __call__(self, req, timeout=None):
        self.requests.append((req.full_url, json.loads(req.data.decode("utf-8"))))
        if not self.script:
            raise AssertionError("多余的网络请求(脚本已用尽)")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return _FakeResponse(item)


def openai_shell(content, finish_reason: str = "stop") -> dict:
    """OpenAI /chat/completions 响应壳。content 传 dict/list 时序列化为消息文本。"""
    if isinstance(content, (dict, list)):
        content = json.dumps(content, ensure_ascii=False)
    return {"choices": [{"message": {"role": "assistant", "content": content},
                         "finish_reason": finish_reason}]}


@pytest.fixture()
def transport(monkeypatch):
    """安装脚本化假传输;返回工厂以便单测内多次换脚本。"""

    def _install(script) -> FakeTransport:
        t = FakeTransport(script)
        monkeypatch.setattr(urllib.request, "urlopen", t)
        return t

    return _install


@pytest.fixture()
def no_llm_env(monkeypatch):
    """拔掉全部 LLM 环境变量,并用哨兵替换 urlopen:任何网络企图都被记录并炸掉。"""
    for var in LLM_ENV:
        monkeypatch.delenv(var, raising=False)
    attempts: list = []

    def _sentinel(*args, **kwargs):
        attempts.append(args)
        raise AssertionError("LLM 未配置:降级路径不得发起网络请求")

    monkeypatch.setattr(urllib.request, "urlopen", _sentinel)
    yield attempts
    assert not attempts, f"降级路径发起了 {len(attempts)} 次网络请求"


# =====================================================================
# 一、Brain 降级完备性:清空环境变量后四门面全走规则路径,结构完整、零网络
# =====================================================================

def test_no_env_provider_disabled_and_never_dials(no_llm_env):
    assert routes_from_env() == []
    provider = LLMProvider()  # 从(已清空的)环境构造
    assert not provider.enabled
    with pytest.raises(BrainUnavailable):
        provider.complete_json(system="s", user="u")
    assert not Brain(provider).enabled
    assert not Brain(None).enabled  # provider 整个拔掉同样成立


def test_intent_rules_cover_every_scenario_keyword(no_llm_env):
    """registry/scenarios.py 每个场景的每个 match 关键词都必须被规则层接住。"""
    brain = Brain(LLMProvider())
    assert len(SCENARIOS) >= 3  # quake / permafrost / landslide 起步
    for sc in SCENARIOS:
        keywords = sc.match.split("|")
        assert keywords, f"{sc.key} 的 match 为空"
        for kw in keywords:
            # 按 | 拆词的前提是 match 为纯字面量交替;若未来引入元字符,此处提醒更新拆分
            assert re.escape(kw) == kw, f"{sc.key} 的 match 含正则元字符:{kw!r}"
            r = brain.intent(f"请帮我处理{kw}区域的一批 Sentinel-1 数据")
            assert r.ok and r.scenario is not None, f"关键词 {kw!r} 未被规则层识别"
            assert r.scenario.key == sc.key, f"关键词 {kw!r} 命中了 {r.scenario.key}"
            assert r.source == "rules" and not r.need_form and r.fields == {}


def test_intent_unrecognized_text_degrades_to_form(no_llm_env):
    r = Brain(LLMProvider()).intent("帮我把这批影像跑一遍全流程")
    assert not r.ok and r.need_form and r.source == "form" and r.scenario is None


def test_select_degrades_to_recommend_with_readable_reason(no_llm_env):
    brain = Brain(LLMProvider())
    feas = _feas6()
    ok_ids = _ok_ids(feas)
    assert len(ok_ids) >= 2  # 前提:确实存在需要决策的多候选局面
    pick = brain.select(REGISTRY[6], feas)
    assert pick.source == "recommend" and pick.method_id == "snaphu_mcf"
    assert pick.method_id in ok_ids
    assert isinstance(pick.reason, str) and pick.reason.strip()  # reason 可读
    # 降级路径同样尊重 prefer(场景 override 在无 LLM 时不失效)
    assert brain.select(REGISTRY[6], feas, prefer="icu").method_id == "icu"


def test_select_single_candidate_short_circuits(no_llm_env):
    feas = [f for f in _feas6() if f.method.id == "icu"]
    pick = Brain(LLMProvider()).select(REGISTRY[6], feas)
    assert pick.source == "only_choice" and pick.method_id == "icu"
    assert isinstance(pick.reason, str) and pick.reason.strip()


def test_triage_rules_cover_full_rule_table(no_llm_env):
    """core/failures.RULES 全表逐条命中:规则层不依赖 LLM 即可分类。"""
    brain = Brain(LLMProvider())
    samples = {
        "写盘失败:No space left on device": FailureClass.DISK_FULL,
        "worker process Killed after 120s": FailureClass.OOM,
        "bash: topsApp.py: command not found": FailureClass.TOOL_MISSING,
        "HTTP 401 Unauthorized": FailureClass.AUTH_EXPIRED,
        "ASF quota exceeded for this account": FailureClass.QUOTA_EXHAUSTED,
        "HTTP 502 Bad Gateway": FailureClass.SERVICE_DOWN,
        "requests.exceptions.ConnectionError: peer reset": FailureClass.NETWORK_TRANSIENT,
        "invalid parameter: azimuth_looks": FailureClass.PARAM_INVALID,
    }
    for text, want in samples.items():
        r = brain.triage(text)
        assert r.failure_class is want, f"{text!r} 应分类为 {want}"
        assert r.source == "rules"


def test_triage_unmatched_degrades_to_unknown_with_detail(no_llm_env):
    r = Brain(LLMProvider()).triage("步骤静默退出,日志里再无别的线索")
    assert r.failure_class is FailureClass.UNKNOWN and r.source == "fallback"
    assert isinstance(r.detail, str) and r.detail.strip()  # 降级原因可读


def test_narrate_degrades_to_template(no_llm_env):
    md, source = Brain(LLMProvider()).narrate(PROV)
    assert source == "template"
    assert isinstance(md, str) and "snaphu_mcf" in md and "0.25" in md


# =====================================================================
# 二、provider 传输层(假 urlopen):截断/坏 JSON/网络故障的处理语义
# =====================================================================

def _clear_llm_env(monkeypatch):
    for var in LLM_ENV:
        monkeypatch.delenv(var, raising=False)


def test_provider_normal_json_and_request_shape(transport):
    t = transport([openai_shell({"choice": 2, "reason": "精度优先"})])
    provider = LLMProvider(routes=[LLMRoute("http://primary.invalid/v1", "k-1", "m-1")])

    data = provider.complete_json(system="系统", user="用户", max_tokens=99)

    assert data == {"choice": 2, "reason": "精度优先"}
    url, payload = t.requests[0]
    assert url == "http://primary.invalid/v1/chat/completions"
    assert payload["model"] == "m-1" and payload["max_tokens"] == 99
    assert payload["temperature"] == 0
    assert payload["response_format"] == {"type": "json_object"}
    assert [m["role"] for m in payload["messages"]] == ["system", "user"]  # 不带历史


def test_provider_truncation_rejected_no_retry_no_fallback(transport):
    """finish_reason=length 的语义(absorb-E9):整体拒绝——不重试、不换路由、直接抛。"""
    t = transport([openai_shell({"choice": 0}, finish_reason="length")])
    provider = LLMProvider(routes=[LLMRoute("http://a.invalid", "", "m1"),
                                   LLMRoute("http://b.invalid", "", "m2")])
    with pytest.raises(BrainTruncated):
        provider.complete_json(system="s", user="u")
    assert len(t.requests) == 1  # 备路由一次都没被触达
    assert issubclass(BrainTruncated, BrainUnavailable)  # 调用方可统一按降级捕获


def test_provider_non_json_content_takes_single_hop_fallback(transport):
    t = transport([openai_shell("好的!我推荐使用 snaphu(这不是 JSON)"),
                   openai_shell({"src": "backup"})])
    provider = LLMProvider(routes=[LLMRoute("http://a.invalid", "", "m1"),
                                   LLMRoute("http://b.invalid", "", "m2")])
    assert provider.complete_json(system="s", user="u") == {"src": "backup"}
    assert len(t.requests) == 2
    assert t.requests[1][0].startswith("http://b.invalid")  # 单跳换到备路由


def test_provider_non_json_on_both_routes_gives_unavailable(transport):
    t = transport([openai_shell("自然语言 A"), openai_shell("自然语言 B")])
    provider = LLMProvider(routes=[LLMRoute("http://a.invalid", "", "m1"),
                                   LLMRoute("http://b.invalid", "", "m2")])
    with pytest.raises(BrainUnavailable):
        provider.complete_json(system="s", user="u")
    assert len(t.requests) == 2  # 主+备各一次,绝不无限重试


def test_provider_network_errors_degrade_gracefully(transport):
    # 拒连 + 读超时:provider 层收敛为 BrainUnavailable,不向上泄漏原始异常
    transport([urllib.error.URLError("connection refused"), TimeoutError("read timed out")])
    provider = LLMProvider(routes=[LLMRoute("http://a.invalid", "", "m1"),
                                   LLMRoute("http://b.invalid", "", "m2")])
    with pytest.raises(BrainUnavailable):
        provider.complete_json(system="s", user="u")

    # facade 层:同样的故障下 select 优雅降级到 recommend,不崩
    transport([urllib.error.URLError("boom"), TimeoutError("boom")])
    brain = Brain(LLMProvider(routes=[LLMRoute("http://a.invalid", "", "m1"),
                                      LLMRoute("http://b.invalid", "", "m2")]))
    pick = brain.select(REGISTRY[6], _feas6())
    assert pick.source == "recommend" and pick.method_id == "snaphu_mcf"

    # triage 同理:规则未命中 + LLM 网络故障 → UNKNOWN 兜底
    transport([urllib.error.URLError("boom")])
    brain2 = Brain(LLMProvider(routes=[LLMRoute("http://a.invalid", "", "m1")]))
    r = brain2.triage("完全陌生的失败输出")
    assert r.failure_class is FailureClass.UNKNOWN and r.source == "fallback"


def test_routes_from_env_parses_primary_and_fallback(monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("INSAR_LLM_BASE_URL", "http://main.invalid/v1/")
    monkeypatch.setenv("INSAR_LLM_API_KEY", "key-main")
    monkeypatch.setenv("INSAR_LLM_MODEL", "model-main")
    monkeypatch.setenv("INSAR_LLM_FALLBACK_BASE_URL", "http://backup.invalid/")
    monkeypatch.setenv("INSAR_LLM_FALLBACK_MODEL", "model-backup")  # 备路由允许无 key

    routes = routes_from_env()
    assert routes == [LLMRoute("http://main.invalid/v1", "key-main", "model-main"),
                      LLMRoute("http://backup.invalid", "", "model-backup")]
    assert LLMProvider().enabled


def test_routes_from_env_requires_both_url_and_model(monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("INSAR_LLM_BASE_URL", "http://main.invalid")  # 缺 MODEL → 主路由无效
    monkeypatch.setenv("INSAR_LLM_FALLBACK_BASE_URL", "http://backup.invalid")
    monkeypatch.setenv("INSAR_LLM_FALLBACK_MODEL", "model-backup")

    routes = routes_from_env()
    assert [r.model for r in routes] == ["model-backup"]  # 只剩备路由


def test_no_env_zero_network_at_provider_level(monkeypatch, transport):
    _clear_llm_env(monkeypatch)
    t = transport([])  # 任何请求都会炸
    with pytest.raises(BrainUnavailable):
        LLMProvider().complete_json(system="s", user="u")
    assert t.requests == []  # 未配置 = 一次网络都不发


# =====================================================================
# 三、select 只选不造:候选之外的方法名/幻觉字段/类型越界一律拒绝
# =====================================================================

def test_select_rejects_method_name_string_even_if_valid_id():
    """契约是「候选序号」;直接回方法名字符串(哪怕是真实 id)也必须拒绝。"""
    provider = ScriptedProvider([{"choice": "icu"}, {"choice": "icu"}])
    pick = Brain(provider).select(REGISTRY[6], _feas6())
    assert pick.source == "recommend" and pick.method_id == "snaphu_mcf"
    assert provider.calls == 2  # 重问一次,仍错才降级


def test_select_rejects_invented_method_and_params():
    all_ids = {m.id for m in REGISTRY[6].methods}
    assert "quantum_unwrap" not in all_ids  # 前提:确为幻觉方法名
    provider = ScriptedProvider([{"method": "quantum_unwrap", "params": {"magic": 1}},
                                 {"method": "quantum_unwrap"}])
    pick = Brain(provider).select(REGISTRY[6], _feas6())
    assert pick.source == "recommend" and pick.method_id in _ok_ids(_feas6())


def test_select_rejects_bool_choice():
    """触发场景:LLM 返回 {"choice": true}。bool 是 int 子类,修复前会被当索引 1
    静默接受(source 标成 llm)—— 那不是候选序号,是幻觉输出,必须按越界拒绝。"""
    provider = ScriptedProvider([{"choice": True}, {"choice": False}])
    pick = Brain(provider).select(REGISTRY[6], _feas6())
    assert pick.source == "recommend"  # 修复前:source == "llm" 且选中 ok[1]
    assert provider.calls == 2


def test_select_rejects_float_choice():
    provider = ScriptedProvider([{"choice": 1.0}, {"choice": 0.0}])
    pick = Brain(provider).select(REGISTRY[6], _feas6())
    assert pick.source == "recommend"


def test_select_never_returns_outside_candidates():
    """任意畸形应答下,select 的输出必须始终落在可行候选集内(核心不变量)。"""
    ok_ids = _ok_ids(_feas6())
    weird = [{"choice": 99}, {"choice": -1}, {"choice": None},
             {"choice": "snaphu_mcf"}, {"reason": "只给理由不选"}, {},
             {"choice": [0]}, {"choice": {"index": 0}}]
    for resp in weird:
        pick = Brain(ScriptedProvider([resp, resp])).select(REGISTRY[6], _feas6())
        assert pick.method_id in ok_ids, f"应答 {resp!r} 让 select 越出了候选集"
        assert pick.source == "recommend"


def test_select_valid_boundary_choices_accepted():
    """防过度拒绝:0 与 len-1 两个合法边界索引仍然接受(带可读 reason)。"""
    feas = _feas6()
    ok = [f for f in feas if f.ok]
    first = Brain(ScriptedProvider([{"choice": 0, "reason": "首选"}])).select(REGISTRY[6], feas)
    assert first.source == "llm" and first.method_id == ok[0].method.id
    last_idx = len(ok) - 1
    last = Brain(ScriptedProvider([{"choice": last_idx, "reason": "末选"}])).select(
        REGISTRY[6], feas)
    assert last.source == "llm" and last.method_id == ok[last_idx].method.id


def test_select_with_no_feasible_method_raises():
    feas = narrow_methods(REGISTRY[6], empty_probe())
    assert not _ok_ids(feas)
    with pytest.raises(ValueError):
        Brain(None).select(REGISTRY[6], feas)


# =====================================================================
# 四、planner:narrow 理由 / pick 决定性 / fork 三类参数传播 / cloud 跳步
# =====================================================================

def test_narrow_missing_engine_reason_names_the_engine():
    feas = narrow_methods(REGISTRY[3], empty_probe())
    assert all(not f.ok for f in feas)
    by_id = {f.method.id: f for f in feas}
    assert "工具链缺失" in by_id["isce2_tops_geom_esd"].blocked_reason
    assert "isce2" in by_id["isce2_tops_geom_esd"].blocked_reason
    assert "snap" in by_id["snap_backgeocoding"].blocked_reason
    for f in feas:  # 每个被排除方法都有明确 reason(面板9 可解释性)
        assert f.blocked_reason.strip(), f"{f.method.id} 被排除但没有理由"


def test_narrow_missing_credential_reason():
    probe = probe_of({"hyp3": "present", "asf_api": None}, {"earthdata": False})
    by_id = {f.method.id: f for f in narrow_methods(REGISTRY[1], probe)}
    assert not by_id["hyp3_submit"].ok
    assert "缺凭据" in by_id["hyp3_submit"].blocked_reason
    assert "earthdata" in by_id["hyp3_submit"].blocked_reason
    assert by_id["local_import"].ok  # 无外部依赖的方法不受影响


def test_narrow_engine_and_credential_reasons_stack():
    probe = probe_of({"hyp3": None, "asf_api": None}, {"earthdata": False})
    by_id = {f.method.id: f for f in narrow_methods(REGISTRY[1], probe)}
    reason = by_id["hyp3_submit"].blocked_reason
    assert "工具链缺失" in reason and "hyp3" in reason
    assert "缺凭据" in reason and "earthdata" in reason  # 多重阻塞理由全部呈现


def test_narrow_disk_shortage_is_not_a_narrowing_dimension():
    """记录现状:收窄只看引擎/凭据/场景,磁盘预算由 setup 预检与执行层把关
    (api/setup_router 的 disk_space 检查)。probe 磁盘归零不应排除任何方法。"""
    starving = probe_of(FULL_TOOLS, {"earthdata": True, "cds": True}, disk_free_gb=0.01)
    normal = probe_of(FULL_TOOLS, {"earthdata": True, "cds": True}, disk_free_gb=500.0)
    for cap_id in REGISTRY:
        lean = narrow_methods(REGISTRY[cap_id], starving)
        assert lean == narrow_methods(REGISTRY[cap_id], normal)


def test_narrow_scenario_mismatch_reason_lists_allowed_scenarios():
    by_id = {f.method.id: f for f in
             narrow_methods(REGISTRY[9], full_probe(), scenario="landslide")}
    assert not by_id["step"].ok and "场景不匹配" in by_id["step"].blocked_reason
    assert "quake" in by_id["step"].blocked_reason  # 理由里给出允许的场景
    assert not by_id["poly_periodic"].ok
    assert "permafrost" in by_id["poly_periodic"].blocked_reason
    assert by_id["linear"].ok and by_id["exponential"].ok


def test_narrow_every_blocked_method_has_reason_globally():
    """全 registry 不变量:任何 probe/场景组合下,被排除的方法必有非空理由。"""
    for probe in (empty_probe(), probe_of({"mintpy": "1.6"}, {})):
        for scenario in (None, "quake", "permafrost", "landslide"):
            for cap in REGISTRY.values():
                for f in narrow_methods(cap, probe, scenario=scenario):
                    assert f.ok or f.blocked_reason.strip(), \
                        f"{cap.id}/{f.method.id} 被排除但没有理由"


def test_make_plan_all_blocked_steps_become_problems(store):
    store.create_session("s1", "t")
    plan = make_plan(store, "s1", registry=REGISTRY, probe=empty_probe(),
                     scenario=None, workspace="ws")
    assert not plan.runnable()
    prob_ids = {int(m) for p in plan.problems for m in re.findall(r"第 (\d+) 步", p)}
    assert prob_ids == {3, 4, 6, 7, 8, 9}  # 无引擎方法的步骤全部如实上报
    for p in plan.problems:  # 每条 problem 附带该步全部候选的排除理由
        sid = int(re.search(r"第 (\d+) 步", p).group(1))
        for m in REGISTRY[sid].methods:
            assert m.id in p, f"步骤 {sid} 的 problem 缺少候选 {m.id} 的解释"
    # 无方法的步骤不落库;run 停在 planning,driver 拒绝执行
    planned_ids = {s.step_id for s in store.load_steps(plan.run_id)}
    assert prob_ids.isdisjoint(planned_ids)
    assert store.get_run(plan.run_id)["status"] == "planning"


def test_pick_method_deterministic():
    feas = _feas6()
    assert len({pick_method(feas).method.id for _ in range(20)}) == 1
    # 收窄本身也是纯函数:同 probe 同场景 → 逐项相等
    assert narrow_methods(REGISTRY[6], full_probe()) == narrow_methods(REGISTRY[6], full_probe())


def test_pick_method_priority_chain():
    a = Method("alpha", "alpha", "-")
    b = Method("beta", "beta", "-", recommend=True)
    c = Method("gamma", "gamma", "-")
    feas = [MethodFeasibility(a, True), MethodFeasibility(b, True), MethodFeasibility(c, True)]
    assert pick_method(feas).method.id == "beta"                      # recommend 优先
    assert pick_method(feas, prefer="gamma").method.id == "gamma"     # prefer 压过 recommend
    assert pick_method(feas, prefer="不存在的").method.id == "beta"    # 未知 prefer 回落
    b_blocked = [MethodFeasibility(a, True),
                 MethodFeasibility(b, False, blocked_reason="工具链缺失:x"),
                 MethodFeasibility(c, True)]
    assert pick_method(b_blocked).method.id == "alpha"                # 无可行 recommend → 首个可行
    assert pick_method(b_blocked, prefer="beta").method.id == "alpha"  # prefer 不可行 → 跳过
    assert pick_method([MethodFeasibility(a, False, blocked_reason="x")]) is None


# ---------------- fork:三类参数的失效传播边界 ----------------

def _plan_all_done(store, *, scenario=None):
    store.create_session("s1", "t")
    plan = make_plan(store, "s1", registry=REGISTRY, probe=full_probe(),
                     scenario=scenario, workspace="ws")
    assert plan.runnable()
    for p in plan.steps:
        if p.state != "skipped":
            store.advance(plan.run_id, p.step_id, "VERIFIED", state="done", run_ok=1)
    return plan


def _states(store, run_id):
    return {s.step_id: s.state for s in store.load_steps(run_id)}


def _evals(store, run_id):
    return {s.step_id: s.eval_hash for s in store.load_steps(run_id)}


def _locals(store, run_id):
    return {s.step_id: s.local_hash for s in store.load_steps(run_id)}


def _queue(store, run_id):
    """复刻 loop/driver 的待跑集合谓词:skipped/done 永不入队。"""
    return sorted(s.step_id for s in store.load_steps(run_id)
                  if s.state in ("pending", "stale", "interrupted", "orphaned"))


def test_fork_presentation_change_reruns_only_that_step(store):
    """改呈现参数(出图配色):零上游重跑、零下游传播,只重跑本步。"""
    parent = _plan_all_done(store)
    fork = fork_run(store, parent.run_id, registry=REGISTRY,
                    changes={10: {"params": {"cmap": "viridis"}}}, probe=full_probe())
    states = _states(store, fork.run_id)
    assert states[10] == "pending"
    assert all(states[s] == "done" for s in states if s != 10)  # 含下游 11
    # 指纹级断言:presentation 只进 local_hash,不进 eval_hash → 不级联
    assert _evals(store, fork.run_id) == _evals(store, parent.run_id)
    pl, fl = _locals(store, parent.run_id), _locals(store, fork.run_id)
    assert fl[10] != pl[10]
    assert all(fl[s] == pl[s] for s in fl if s != 10)
    assert _queue(store, fork.run_id) == [10]


def test_fork_science_change_invalidates_exactly_downstream(store):
    """改科学参数(滤波强度):恰好从第 5 步起,下游全部失效,上游全部复用。"""
    parent = _plan_all_done(store)
    fork = fork_run(store, parent.run_id, registry=REGISTRY,
                    changes={5: {"params": {"filter_strength": 0.7}}}, probe=full_probe())
    states = _states(store, fork.run_id)
    assert {s for s, st in states.items() if st == "pending"} == {5, 6, 7, 8, 9, 10, 11}
    assert {s for s, st in states.items() if st == "done"} == {1, 2, 3, 4}
    pe, fe = _evals(store, parent.run_id), _evals(store, fork.run_id)
    assert all(fe[s] == pe[s] for s in (1, 2, 3, 4))      # 上游指纹原样
    assert all(fe[s] != pe[s] for s in (5, 6, 7, 8, 9, 10, 11))  # eval_hash 逐级级联
    assert _queue(store, fork.run_id) == [5, 6, 7, 8, 9, 10, 11]


def test_fork_resource_change_zero_invalidation(store):
    """改资源参数(线程数):零失效——全部复用,仅 provenance 记录新值。"""
    parent = _plan_all_done(store)
    fork = fork_run(store, parent.run_id, registry=REGISTRY,
                    changes={3: {"params": {"threads": 16}}}, probe=full_probe())
    assert all(st == "done" for st in _states(store, fork.run_id).values())
    assert _evals(store, fork.run_id) == _evals(store, parent.run_id)
    assert _locals(store, fork.run_id) == _locals(store, parent.run_id)
    assert _queue(store, fork.run_id) == []
    forked = {s.step_id: s for s in store.load_steps(fork.run_id)}
    parent_steps = {s.step_id: s for s in store.load_steps(parent.run_id)}
    assert forked[3].params["threads"] == 16          # 新值进 provenance
    assert parent_steps[3].params["threads"] == 8     # 父 run 不受影响


def test_fork_rejects_hallucinated_method_without_partial_run(store):
    """触发场景:API /fork 透传外部 changes,LLM/用户给出候选之外的方法名。
    必须整体拒绝,且校验先于落库 —— 不留半成品 run。"""
    parent = _plan_all_done(store)
    runs_before = store.db.query("SELECT COUNT(*) AS n FROM runs")[0]["n"]
    with pytest.raises(ValueError, match="未知方法"):
        fork_run(store, parent.run_id, registry=REGISTRY,
                 changes={6: {"method": "quantum_unwrap"}}, probe=full_probe())
    with pytest.raises(ValueError, match="参数校验失败"):
        fork_run(store, parent.run_id, registry=REGISTRY,
                 changes={6: {"params": {"magic_knob": 1}}}, probe=full_probe())
    with pytest.raises(ValueError, match="参数校验失败"):  # 越界值同样拒绝
        fork_run(store, parent.run_id, registry=REGISTRY,
                 changes={6: {"params": {"min_coherence": 5}}}, probe=full_probe())
    runs_after = store.db.query("SELECT COUNT(*) AS n FROM runs")[0]["n"]
    assert runs_after == runs_before  # 三次拒绝,零半成品


def test_fork_rejects_change_to_nonexistent_step(store):
    """变更指向不存在的步骤若被静默忽略,fork 会与父 run 完全相同,
    调用方误以为变更已生效 —— 必须显式报错。"""
    parent = _plan_all_done(store)
    with pytest.raises(ValueError, match="不存在的步骤"):
        fork_run(store, parent.run_id, registry=REGISTRY,
                 changes={99: {"params": {"threads": 2}}}, probe=full_probe())


# ---------------- cloud_completed:跳步且永不进执行队列 ----------------

def test_quake_plan_cloud_steps_skipped_even_with_zero_engines(store):
    """cloud_completed 步骤不做可行性检查:零引擎环境下 2-6 仍是 skipped,
    绝不混入 problems;7-9 缺引擎则如实上报。"""
    store.create_session("s1", "t")
    sc = scenario_of("quake")
    plan = make_plan(store, "s1", registry=REGISTRY, probe=empty_probe(),
                     scenario=sc, workspace="ws")
    cloud = set(sc.cloud_completed)
    assert cloud == {2, 3, 4, 5, 6}
    states = _states(store, plan.run_id)
    assert all(states[s] == "skipped" for s in cloud)
    assert not (cloud & set(_queue(store, plan.run_id)))  # 永不入执行队列
    prob_ids = {int(m) for p in plan.problems for m in re.findall(r"第 (\d+) 步", p)}
    assert prob_ids == {7, 8, 9} and not (prob_ids & cloud)
    planned = {p.step_id: p for p in plan.steps}
    assert planned[2].state == "skipped"
    assert planned[2].narrowed[0]["reason"] == "云端(HyP3)已完成"  # 面板9 可解释


def test_fork_of_quake_run_keeps_cloud_steps_skipped(store):
    parent = _plan_all_done(store, scenario=scenario_of("quake"))
    fork = fork_run(store, parent.run_id, registry=REGISTRY,
                    changes={9: {"params": {"step_date": "20190707T0000"}}},
                    probe=full_probe())
    states = _states(store, fork.run_id)
    assert all(states[s] == "skipped" for s in (2, 3, 4, 5, 6))  # skipped 不被复活
    assert states[1] == states[7] == states[8] == "done"
    assert {s for s, st in states.items() if st == "pending"} == {9, 10, 11}
    assert _queue(store, fork.run_id) == [9, 10, 11]  # 云端步骤仍不入队


# ---------------- make_plan overrides:外部覆写的幻觉拒绝 ----------------

def test_make_plan_override_invalid_param_value_refused(store):
    """触发场景:next_run 干预队列的 SET_PARAMS 不经 apply_change 校验直达 make_plan。
    越界值若静默合并,会进指纹并流向引擎 —— 必须记 problems 拒绝带病上路。"""
    store.create_session("s1", "t")
    plan = make_plan(store, "s1", registry=REGISTRY, probe=full_probe(), scenario=None,
                     workspace="ws", overrides={6: {"params": {"min_coherence": 5}}})
    assert not plan.runnable()
    assert any("min_coherence" in p and "第 6 步" in p for p in plan.problems)
    assert store.get_run(plan.run_id)["status"] == "planning"
    step6 = next(s for s in store.load_steps(plan.run_id) if s.step_id == 6)
    assert step6.params["min_coherence"] == 0.25  # 非法值未合并,保持默认


def test_make_plan_override_hallucinated_param_name_refused(store):
    store.create_session("s1", "t")
    plan = make_plan(store, "s1", registry=REGISTRY, probe=full_probe(), scenario=None,
                     workspace="ws", overrides={6: {"params": {"magic_knob": 1}}})
    assert not plan.runnable()
    assert any("magic_knob" in p for p in plan.problems)
    step6 = next(s for s in store.load_steps(plan.run_id) if s.step_id == 6)
    assert "magic_knob" not in step6.params


def test_make_plan_override_hallucinated_method_refused(store):
    """幻觉方法名若只是静默回退 recommend,用户会误以为覆写已生效 —— 必须上报。"""
    store.create_session("s1", "t")
    plan = make_plan(store, "s1", registry=REGISTRY, probe=full_probe(), scenario=None,
                     workspace="ws", overrides={6: {"method": "quantum_unwrap"}})
    assert not plan.runnable()
    assert any("覆写方法未知" in p and "quantum_unwrap" in p for p in plan.problems)
    step6 = next(p for p in plan.steps if p.step_id == 6)
    assert step6.method in {m.id for m in REGISTRY[6].methods}  # 计划本身仍在候选集内


def test_make_plan_override_valid_applies_cleanly(store):
    """防过度拒绝回归:合法覆写照常生效,计划可执行。"""
    store.create_session("s1", "t")
    plan = make_plan(store, "s1", registry=REGISTRY, probe=full_probe(), scenario=None,
                     workspace="ws",
                     overrides={6: {"method": "icu", "params": {"min_coherence": 0.3}}})
    assert plan.runnable()
    step6 = next(p for p in plan.steps if p.step_id == 6)
    assert step6.method == "icu" and step6.params["min_coherence"] == 0.3
    assert store.get_run(plan.run_id)["status"] == "ready"
