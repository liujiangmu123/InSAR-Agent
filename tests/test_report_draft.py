"""方法章节草稿(report/draft.py + /api/report/draft)的事实纪律测试。

覆盖(任务验收清单):
  - facts 抽取:完整账本 / 缺字段显式「未记录」/ 模拟 run / 干预次数 /
    资源参数与内部键剔除 / 数据规模只认账本既有键;
  - 骨架生成:确定性(同 facts 必得同文本)+ 数字闭包(骨架里每个数值
    token 都能在 facts_used 清单里逐字反查)+ 模拟警示句强制;
  - 润色校验:mock LLM 丢数值 / 多出未知数字 / 改方法名 / 润掉警示句 /
    返回非字符串 / 抛 BrainUnavailable → 一律回退骨架(llm_polish=False);
    合法改写(数值双向一致)→ 采纳(llm_polish=True);
  - API:归属校验(跨会话 404 不泄露)/ 无 run 404 / 正常生成 + 落盘
    report_draft.md / provider 注入时润色生效。

路由挂接:测试直接 include create_report_router(store, home)(artifacts
路由测试同款隔离模式,不拉起整个 create_app)。
"""

from __future__ import annotations

import copy
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from insar_agent.api.report_router import DRAFT_FILENAME, create_report_router
from insar_agent.brain.provider import BrainUnavailable, LLMProvider, LLMRoute
from insar_agent.core.db import Database
from insar_agent.core.store import Store
from insar_agent.report.draft import (
    MISSING,
    SIMULATED_SENTENCE,
    build_facts,
    draft_methods,
    facts_used_list,
    numeric_tokens,
    skeleton_text,
)

# ---------------------------------------------------------------------------
# 样本账本(与 core/ledger.export_provenance 的字段同形,手工可控)
# ---------------------------------------------------------------------------

def sample_provenance() -> dict:
    return {
        "schema_version": "1.0",
        "run_id": "20260813T090000-t0001",
        "session_id": "sess-a",
        "generated_at_utc": "2026-08-13T09:12:00Z",
        "simulated": True,
        "environment": {"python": "3.11.9", "platform": "win32",
                        "tools": {"isce2": "2.6.3", "mintpy": "1.5.1"}},
        "intent": {"goal": "Ridgecrest 2019 同震形变分析"},
        "scenario": "coseismic_interferogram",
        "steps": {
            # 键刻意乱序 + 含两位数,验证按数值排序(10 不排在 2 前)
            "10": {"name": "出图导出", "capability": "figures",
                   "method": "figure_journal", "state": "done",
                   "params": {"dpi": 600, "threads": 8}},
            "1": {"name": "数据获取", "capability": "acquire",
                  "method": "local_import", "state": "done",
                  "params": {"platform": "Sentinel-1", "scenes": 12,
                             "dates": "20190704-20190716",
                             "threads": 8, "_probe": "内部键不入闭集"}},
            "2": {"name": "辅助数据", "capability": "aux",
                  "method": "hyp3_submit", "state": "skipped", "params": {}},
            "4": {"name": "干涉", "capability": "interferogram",
                  "method": "isce2_ifg_multilook", "state": "done",
                  "params": {"pairs": 11}},
            "5": {"name": "滤波", "capability": "filter", "method": "goldstein",
                  "state": "done", "params": {"alpha": 0.6}},
            "6": {"name": "解缠", "capability": "unwrap", "method": "snaphu_mcf",
                  "state": "failed", "failure_class": "engine_missing",
                  "params": {"cost_mode": "SMOOTH"}},
            "7": {"name": "时序反演", "capability": "invert",
                  "method": "mintpy_sbas", "state": "pending", "params": {}},
        },
        "artifacts": {"vel_png": {"path": "products/figures/velocity.png",
                                  "kind": "FIGURE"}},
        "metrics": {
            "crossval_r": {"value": 0.93, "unit": "",
                           "source_artifact": "qa.json", "source_field": "r"},
            "unwrap_coverage": {"value": 0.87, "unit": "",
                                "source_artifact": "qa.json", "source_field": "cov"},
        },
        "thresholds": {},
        "qa": {"status": "fail"},
        "evidence": {"level": "runnable"},
        "evidence_level": "runnable",
        "warnings": [],
        "interventions": [
            {"action": "SET_PARAMS", "target": "5",
             "payload": {"params": {"alpha": 0.6}}, "deliver_as": "steer",
             "consumed_at": 1786957320.0},
            {"action": "PAUSE", "target": None, "payload": {},
             "deliver_as": "steer", "consumed_at": 1786957440.0},
        ],
    }


# ---------------------------------------------------------------------------
# ① facts 抽取
# ---------------------------------------------------------------------------

def test_build_facts_full_extraction():
    f = build_facts(sample_provenance())
    assert f["run_id"] == "20260813T090000-t0001"
    assert f["scenario"] == "coseismic_interferogram"
    assert f["intent"] == "Ridgecrest 2019 同震形变分析"
    assert f["simulated"] is True
    assert f["python"] == "3.11.9" and f["platform"] == "win32"
    assert f["tools"] == {"isce2": "2.6.3", "mintpy": "1.5.1"}
    assert f["qa_status"] == "fail"
    assert f["evidence_level"] == "runnable"
    assert f["interventions"] == 2
    # 步骤按数值序(10 在末尾,不排在 2 前)
    assert [s["step_id"] for s in f["steps"]] == [1, 2, 4, 5, 6, 7, 10]
    by_id = {s["step_id"]: s for s in f["steps"]}
    assert by_id[5]["method"] == "goldstein" and by_id[5]["params"] == {"alpha": 0.6}
    assert by_id[6]["state"] == "failed"
    assert by_id[6]["failure_class"] == "engine_missing"


def test_build_facts_filters_resource_and_private_params():
    """threads(资源参数)与 _ 前缀(内部键)不入事实闭集 —— 与科学复现无关。"""
    f = build_facts(sample_provenance())
    p1 = {s["step_id"]: s for s in f["steps"]}[1]["params"]
    assert "threads" not in p1 and "_probe" not in p1
    assert p1["scenes"] == 12  # 科学参数原值保留


def test_build_facts_data_scale_from_ledger_only():
    f = build_facts(sample_provenance())
    assert f["data_scale"] == {"platform": "Sentinel-1", "scenes": 12,
                               "dates": "20190704-20190716", "pairs": 11,
                               "artifact_count": 1}


def test_build_facts_missing_fields_marked_not_invented():
    """空账本:一切缺字段显式 MISSING,绝不编数。"""
    f = build_facts({})
    for key in ("run_id", "scenario", "intent", "generated_at_utc",
                "python", "platform", "qa_status", "evidence_level"):
        assert f[key] == MISSING, key
    assert f["steps"] == [] and f["tools"] == {} and f["metrics"] == []
    assert f["simulated"] is False and f["interventions"] == 0
    assert f["data_scale"] == {"platform": MISSING, "scenes": MISSING,
                               "dates": MISSING, "pairs": MISSING,
                               "artifact_count": 0}


def test_build_facts_intent_variants():
    assert build_facts({"intent": {"goal": "g"}})["intent"] == "g"
    assert build_facts({"intent": {"text": "t"}})["intent"] == "t"
    assert build_facts({"intent": "纯字符串意图"})["intent"] == "纯字符串意图"
    assert build_facts({"intent": {}})["intent"] == MISSING
    assert build_facts({"intent": {"goal": "  "}})["intent"] == MISSING


def test_build_facts_evidence_level_fallback_to_evidence_dict():
    f = build_facts({"evidence": {"level": "audited"}})
    assert f["evidence_level"] == "audited"


# ---------------------------------------------------------------------------
# ② 骨架生成:确定性 + 数字闭包 + 强制警示
# ---------------------------------------------------------------------------

def test_skeleton_deterministic():
    facts = build_facts(sample_provenance())
    assert skeleton_text(facts) == skeleton_text(copy.deepcopy(facts))
    # 改一个事实值必然改变文本(不是常量模板)
    changed = copy.deepcopy(facts)
    changed["steps"][3]["params"]["alpha"] = 0.8
    assert skeleton_text(changed) != skeleton_text(facts)


def test_skeleton_numbers_all_traceable_to_facts():
    """数字闭包:骨架里每个数值 token 必须能在 facts_used 清单里逐字反查
    —— 「每个数字只允许来自账本」的机器判定。"""
    facts = build_facts(sample_provenance())
    allowed = numeric_tokens("\n".join(facts_used_list(facts)))
    assert numeric_tokens(skeleton_text(facts)) <= allowed


def test_skeleton_numbers_traceable_for_empty_ledger():
    facts = build_facts({})
    allowed = numeric_tokens("\n".join(facts_used_list(facts)))
    assert numeric_tokens(skeleton_text(facts)) <= allowed
    assert MISSING in skeleton_text(facts)


def test_skeleton_contains_key_facts_and_states():
    text = skeleton_text(build_facts(sample_provenance()))
    assert "goldstein" in text and "alpha=0.6" in text          # 方法 + 关键参数
    assert "snaphu_mcf" in text and "engine_missing" in text    # 失败步如实止述
    assert "云端/缓存完成" in text                               # skipped 步
    assert "mintpy_sbas" not in text                             # pending 步不进方法章节
    assert "crossval_r = 0.93" in text and "unwrap_coverage = 0.87" in text
    assert "isce2 2.6.3" in text and "mintpy 1.5.1" in text      # 工具版本
    assert "runnable" in text and "2 次人工干预" in text
    assert "影像 12 景" in text and "干涉对 11 对" in text        # 数据规模


def test_skeleton_simulated_sentence_forced():
    doc = sample_provenance()
    assert SIMULATED_SENTENCE in skeleton_text(build_facts(doc))
    doc["simulated"] = False
    assert "不构成科学证据" not in skeleton_text(build_facts(doc))


def test_skeleton_no_intervention_wording():
    doc = sample_provenance()
    doc["interventions"] = []
    assert "无人工干预" in skeleton_text(build_facts(doc))


# ---------------------------------------------------------------------------
# ③ 润色校验(mock LLM;不过即回退骨架)
# ---------------------------------------------------------------------------

class FakeProvider(LLMProvider):
    """按脚本返回响应的假 LLM(不走网络;test_brain.py 同款)。"""

    def __init__(self, responses):
        super().__init__(routes=[LLMRoute("http://fake", "", "fake-model")])
        self._responses = list(responses)
        self.calls = 0

    def complete_json(self, *, system, user, max_tokens=512):
        self.calls += 1
        if not self._responses:
            raise BrainUnavailable("no more scripted responses")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        if callable(item):
            return item(system=system, user=user)
        return item


@pytest.fixture()
def facts():
    return build_facts(sample_provenance())


def _valid_polish(skeleton: str) -> str:
    """构造一份合法改写:只动措辞,数值/方法名/警示句全部保留。"""
    return ("(润色稿)" + skeleton
            .replace("本研究的 InSAR 数据处理", "我们采用 InSAR 技术,其数据处理")
            .replace(";", ","))


def test_draft_without_provider_returns_skeleton(facts):
    out = draft_methods(None, facts)
    assert out["llm_polish"] is False
    assert out["draft"] == skeleton_text(facts)
    assert out["facts_used"] == facts_used_list(facts)


def test_draft_with_disabled_provider_returns_skeleton(facts):
    out = draft_methods(LLMProvider(routes=[]), facts)
    assert out["llm_polish"] is False and out["draft"] == skeleton_text(facts)


def test_polish_accepted_when_facts_preserved(facts):
    provider = FakeProvider([lambda system, user: {"draft": _valid_polish(user)}])
    out = draft_methods(provider, facts)
    assert out["llm_polish"] is True
    assert out["draft"].startswith("(润色稿)")
    assert provider.calls == 1
    # 采纳的润色稿数值与骨架双向一致
    assert numeric_tokens(out["draft"]) == numeric_tokens(skeleton_text(facts))


def test_polish_dropping_number_rejected(facts):
    """润色稿丢数值(alpha=0.6 被删)→ 拒绝,回退骨架。"""
    provider = FakeProvider([lambda system, user:
                             {"draft": user.replace("alpha=0.6", "alpha=适中")}])
    out = draft_methods(provider, facts)
    assert out["llm_polish"] is False and out["draft"] == skeleton_text(facts)


def test_polish_adding_unknown_number_rejected(facts):
    """润色稿多出未知数字(编造相关系数 0.99)→ 拒绝,回退骨架。"""
    provider = FakeProvider([lambda system, user:
                             {"draft": user + " 两链速度场相关系数高达 0.99。"}])
    out = draft_methods(provider, facts)
    assert out["llm_polish"] is False and out["draft"] == skeleton_text(facts)


def test_polish_changing_method_name_rejected(facts):
    """改方法名(goldstein → 自适应滤波,数值不变)→ 同样拒绝。"""
    provider = FakeProvider([lambda system, user:
                             {"draft": user.replace("goldstein", "自适应滤波")}])
    out = draft_methods(provider, facts)
    assert out["llm_polish"] is False and out["draft"] == skeleton_text(facts)


def test_polish_dropping_simulated_sentence_rejected(facts):
    """模拟 run 的「不构成科学证据」句被润掉(该句无数字,数值校验拦不住)
    → 专项校验拒绝。"""
    provider = FakeProvider([lambda system, user:
                             {"draft": user.replace(SIMULATED_SENTENCE, "")}])
    out = draft_methods(provider, facts)
    assert out["llm_polish"] is False
    assert SIMULATED_SENTENCE in out["draft"]


def test_polish_non_string_rejected(facts):
    out = draft_methods(FakeProvider([{"draft": 12345}]), facts)
    assert out["llm_polish"] is False and out["draft"] == skeleton_text(facts)
    out = draft_methods(FakeProvider([{"markdown": "答非所问"}]), facts)
    assert out["llm_polish"] is False


def test_polish_brain_unavailable_falls_back(facts):
    out = draft_methods(FakeProvider([BrainUnavailable("网络失败")]), facts)
    assert out["llm_polish"] is False and out["draft"] == skeleton_text(facts)


# ---------------------------------------------------------------------------
# ④ API:归属校验 / 生成 + 落盘 / provider 注入
# ---------------------------------------------------------------------------

RUN_ID = "20260813T100000-draft01"
_HASHES = {"task_hash": "t1", "args_hash": "a1", "local_hash": "l1", "eval_hash": "e1"}


@pytest.fixture()
def env(tmp_path):
    """两个会话 + sess-a 名下一个手搭的模拟 run(2 done 步 + 指标 + 已消费干预),
    app 只挂 report 路由(create_report_router(store, home) 隔离模式)。"""
    home = tmp_path / "home"
    home.mkdir()
    store = Store(Database(home / "insar.db"))
    store.create_session("sess-a", "sess-a")
    store.create_session("sess-b", "sess-b")
    ws = tmp_path / "sessions" / "sess-a"
    ws.mkdir(parents=True)
    store.create_run(RUN_ID, "sess-a", workspace=str(ws), simulated=True,
                     intent={"goal": "API 契约验证"}, scenario="coseismic",
                     tool_versions={"isce2": "2.6.3"})
    for sid, cap, name, method, params in (
            (5, "filter", "滤波", "goldstein", {"alpha": 0.6}),
            (6, "unwrap", "解缠", "snaphu_mcf", {"min_coherence": 0.3}),
    ):
        store.upsert_step(RUN_ID, sid, capability=cap, name=name, method=method,
                          params=params, hashes=_HASHES)
        store.advance(RUN_ID, sid, "VERIFIED", state="done", run_ok=1,
                      qa=[], exit_code=0)
    store.record_metric(RUN_ID, "unwrap_coverage", value=0.87,
                        source_artifact="qa.json", source_field="coverage",
                        reparsed_ok=True)
    aid = store.push_action(scope="step", target="5", action="SET_PARAMS",
                            payload={"params": {"alpha": 0.6}}, run_id=RUN_ID)
    store.consume_action(aid)

    def make_client(provider_factory=None):
        app = FastAPI()
        app.include_router(create_report_router(store, home,
                                                provider_factory=provider_factory))
        return TestClient(app)

    with make_client() as client:
        yield {"client": client, "store": store, "ws": ws, "home": home,
               "tmp": tmp_path, "make_client": make_client}
    store.close()


def test_api_draft_ok_and_saved(env):
    r = env["client"].post("/api/report/draft", json={"session": "sess-a"})
    assert r.status_code == 200
    data = r.json()
    assert set(data) == {"run_id", "draft", "llm_polish", "facts_used", "saved"}
    assert data["run_id"] == RUN_ID
    assert data["llm_polish"] is False           # home 无 llm.json → 骨架模式
    assert data["saved"] is True
    # 草稿含账本事实:方法名/参数/指标/模拟警示/干预次数
    assert "goldstein" in data["draft"] and "alpha=0.6" in data["draft"]
    assert "unwrap_coverage = 0.87" in data["draft"]
    assert "不构成科学证据" in data["draft"]
    assert "1 次人工干预" in data["draft"]
    assert any(x == "step5.param.alpha=0.6" for x in data["facts_used"])
    # 落盘 run 工作目录(文件面板可见),内容与响应一致
    saved = (env["ws"] / DRAFT_FILENAME).read_text(encoding="utf-8")
    assert saved == data["draft"]


def test_api_explicit_run_id_equals_default(env):
    c = env["client"]
    by_default = c.post("/api/report/draft", json={"session": "sess-a"}).json()
    by_run_id = c.post("/api/report/draft",
                       json={"session": "sess-a", "run_id": RUN_ID}).json()
    assert by_default == by_run_id


def test_api_cross_session_404_no_leak(env):
    """sess-b 借 run_id 生成 sess-a 的草稿:按「不存在」处理,不泄露路径。"""
    r = env["client"].post("/api/report/draft",
                           json={"session": "sess-b", "run_id": RUN_ID})
    assert r.status_code == 404
    leak = str(env["tmp"]).replace("\\", "/")
    assert leak not in r.text.replace("\\\\", "/").replace("\\", "/")


def test_api_no_run_404(env):
    r = env["client"].post("/api/report/draft", json={"session": "sess-b"})
    assert r.status_code == 404


def test_api_polish_via_injected_provider(env):
    """provider 注入合法润色 → llm_polish=true 且落盘的是润色稿。"""
    fake = FakeProvider([lambda system, user: {"draft": "(润色稿)" + user}])
    with env["make_client"](provider_factory=lambda: fake) as client:
        data = client.post("/api/report/draft", json={"session": "sess-a"}).json()
    assert data["llm_polish"] is True
    assert data["draft"].startswith("(润色稿)")
    assert fake.calls == 1
    saved = (env["ws"] / DRAFT_FILENAME).read_text(encoding="utf-8")
    assert saved == data["draft"]


def test_api_bad_polish_falls_back_to_skeleton(env):
    """provider 注入编数润色 → 回退骨架,llm_polish=false(端到端护栏)。"""
    fake = FakeProvider([lambda system, user: {"draft": user + " 精度达 0.001 mm。"}])
    with env["make_client"](provider_factory=lambda: fake) as client:
        data = client.post("/api/report/draft", json={"session": "sess-a"}).json()
    assert data["llm_polish"] is False
    assert "0.001" not in data["draft"]


def test_api_response_is_json_serializable_roundtrip(env):
    """facts_used 全部为字符串(前端逐条展示的契约)。"""
    data = env["client"].post("/api/report/draft", json={"session": "sess-a"}).json()
    assert all(isinstance(x, str) for x in data["facts_used"])
    json.dumps(data, ensure_ascii=False)  # 不抛
