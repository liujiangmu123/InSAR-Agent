"""结果章节草稿(report/results.py + /api/report/results)的事实纪律测试。

覆盖(任务验收清单):
  - facts 抽取:有 h5(tmp 造的小 velocity h5,统计值逐项对已知数值)/
    无 h5(整组「未记录」+ 如实原因)/ 无 h5py(依赖缺失同样「未记录」)/
    全 NaN / 指标缺失 / 模拟 run / 台账阈值判定(crossval_r↔corr_threshold、
    unwrap_coverage↔同名键,PENDING 状态如实入闭集);
  - 骨架生成:确定性(同 facts 必得同文本)+ 数字闭包(骨架里每个数值
    token 都能在 results_facts_used 清单里逐字反查)+ 模拟警示句强制 +
    QA fail 如实写;
  - 润色校验(复用 draft._polish_valid):丢数值 / 多出未知数字 / 改形变
    模型方法名 / 润掉警示句 / 抛 BrainUnavailable → 一律回退骨架;
    合法改写 → 采纳;
  - API:归属校验(跨会话 404 不泄露)/ 无 run 404 / 正常生成 + 落盘
    report_results.md / provider 注入时润色生效。

统计单位口径:h5 的 velocity 数据集存 m/yr(MintPy 惯例),事实闭集
×1000 报 mm/yr(engines/qa.py 同款)。样本 [[-0.04, 0.01], [0.02, NaN]] →
有效 [-40, 10, 20] mm/yr:min=-40.0 max=20.0 mean=-3.33 p2=-38.0 p98=19.6,
有效像元占比 75.0%。
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from insar_agent.api.report_router import create_report_router
from insar_agent.brain.provider import BrainUnavailable
from insar_agent.core.db import Database
from insar_agent.core.store import Store
from insar_agent.report.draft import MISSING, SIMULATED_SENTENCE, numeric_tokens
from insar_agent.report.results import (
    RESULTS_FILENAME,
    build_result_facts,
    draft_results,
    results_facts_used,
    results_skeleton,
)
from test_report_draft import FakeProvider

RUN_ID = "20260813T110000-res01"
_HASHES = {"task_hash": "t1", "args_hash": "a1", "local_hash": "l1", "eval_hash": "e1"}


def _write_velocity_h5(ws: Path, values) -> Path:
    """已知数值的小 velocity h5(m/yr;float32 对齐 MintPy 实际存储)。"""
    h5py = pytest.importorskip("h5py")
    np = pytest.importorskip("numpy")
    path = ws / "mintpy" / "velocity.h5"
    path.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(path, "w") as f:
        f.create_dataset("velocity", data=np.asarray(values, dtype="float32"))
    return path


def _build_env(base: Path, *, simulated: bool = True, with_h5: bool = True,
               with_metrics: bool = True, with_step9: bool = True,
               step9_state: str = "done",
               record_velocity_artifact: bool = True) -> tuple[Store, Path, Path]:
    """手搭样本 run:第 9 步形变模型 + QA 指标 + 可选 velocity h5(已知数值)。"""
    home = base / "home"
    home.mkdir(parents=True, exist_ok=True)
    store = Store(Database(home / "insar.db"))
    store.create_session("sess-a", "sess-a")
    store.create_session("sess-b", "sess-b")
    ws = base / "sessions" / "sess-a"
    ws.mkdir(parents=True, exist_ok=True)
    store.create_run(RUN_ID, "sess-a", workspace=str(ws), simulated=simulated,
                     intent={"goal": "结果章节验证"}, scenario="coseismic",
                     tool_versions={"mintpy": "1.5.1"})
    if with_step9:
        # threads 是资源参数,须被闭集剔除(与方法章节同纪律)
        store.upsert_step(RUN_ID, 9, capability="model", name="形变模型",
                          method="linear", params={"poly_order": 1, "threads": 8},
                          hashes=_HASHES)
        if step9_state == "done":
            store.advance(RUN_ID, 9, "VERIFIED", state="done", run_ok=1,
                          qa=[], exit_code=0)
        else:
            store.advance(RUN_ID, 9, "VERIFIED", state="failed", run_ok=0,
                          failure_class="engine_error", qa=[], exit_code=1)
    if with_metrics:
        store.record_metric(RUN_ID, "crossval_r", value=0.92,
                            source_artifact="qa.json", source_field="r",
                            reparsed_ok=True)
        store.record_metric(RUN_ID, "unwrap_coverage", value=0.87,
                            source_artifact="qa.json", source_field="coverage",
                            reparsed_ok=True)
        store.record_metric(RUN_ID, "crossval_rmse_mm", value=3.1, unit="mm",
                            source_artifact="qa.json", source_field="rmse",
                            reparsed_ok=True)
    if with_h5:
        _write_velocity_h5(ws, [[-0.04, 0.01], [0.02, float("nan")]])
        if with_step9 and record_velocity_artifact:
            store.record_artifact(RUN_ID, 9, "velocity", path="mintpy/velocity.h5",
                                  kind="VELOCITY", layout="mintpy_h5",
                                  policy="content", fp="content:sha256:deadbeef")
    return store, home, ws


@pytest.fixture()
def env_factory(tmp_path):
    """每次调用建一个独立子目录里的样本 run(一个测试可建多个变体);
    统一收尾 close,防 Windows 下 SQLite 句柄拖住 tmp 清理。"""
    stores: list[Store] = []
    counter = [0]

    def make(**kw) -> tuple[Store, Path, Path]:
        counter[0] += 1
        store, home, ws = _build_env(tmp_path / f"case{counter[0]}", **kw)
        stores.append(store)
        return store, home, ws

    yield make
    for s in stores:
        s.close()


# ---------------------------------------------------------------------------
# ① facts 抽取:统计正确性 / 缺失如实 / 台账判定
# ---------------------------------------------------------------------------

def test_facts_velocity_stats_known_values(env_factory):
    """tmp 小 h5 已知数值:统计逐项精确(m/yr ×1000 → mm/yr,round 2)。"""
    store, _home, _ws = env_factory()
    v = build_result_facts(store, RUN_ID)["velocity"]
    assert v["available"] is True
    assert v["path"] == "mintpy/velocity.h5"
    assert v["min_mmyr"] == -40.0
    assert v["max_mmyr"] == 20.0
    assert v["mean_mmyr"] == -3.33
    assert v["p2_mmyr"] == -38.0
    assert v["p98_mmyr"] == 19.6
    assert v["valid_pixel_pct"] == 75.0


def test_facts_h5_found_by_convention_path(env_factory):
    """账本无 VELOCITY 产物行时按注册表候选路径兜底(engines/mintpy 口径)。"""
    store, *_ = env_factory(record_velocity_artifact=False)
    v = build_result_facts(store, RUN_ID)["velocity"]
    assert v["available"] is True and v["path"] == "mintpy/velocity.h5"


def test_facts_without_h5_marked_unavailable(env_factory):
    """velocity h5 缺失 → 整组「未记录」+ 如实原因,绝不产出统计字段。"""
    store, *_ = env_factory(with_h5=False)
    v = build_result_facts(store, RUN_ID)["velocity"]
    assert v["available"] is False
    assert "不存在" in v["reason"]
    assert "min_mmyr" not in v


def test_facts_without_h5py_marked_unavailable(env_factory, monkeypatch):
    """h5py 依赖缺失(sys.modules 置 None 模拟)→ 同样「未记录」,不炸不编。"""
    pytest.importorskip("h5py")  # 造文件需要真 h5py
    store, *_ = env_factory()
    monkeypatch.setitem(sys.modules, "h5py", None)
    v = build_result_facts(store, RUN_ID)["velocity"]
    assert v["available"] is False and "h5py" in v["reason"]


def test_facts_all_nan_h5_stats_honest(env_factory):
    """全 NaN 速度场:占比 0.0 如实入闭集,统计量保持 None(骨架写无有效像元)。"""
    store, _home, ws = env_factory(with_h5=False)
    _write_velocity_h5(ws, [[float("nan"), float("nan")]])
    facts = build_result_facts(store, RUN_ID)
    v = facts["velocity"]
    assert v["available"] is True and v["valid_pixel_pct"] == 0.0
    assert v["min_mmyr"] is None
    assert "无有效像元" in results_skeleton(facts)


def test_facts_metrics_with_ledger_verdicts(env_factory):
    """判定口径 = contract.yaml 台账:crossval_r↔corr_threshold、
    unwrap_coverage↔同名键(值/状态入闭集);无台账判据的指标不下结论。"""
    store, *_ = env_factory()
    facts = build_result_facts(store, RUN_ID)
    by = {m["name"]: m for m in facts["metrics"]}
    assert [m["name"] for m in facts["metrics"]] == sorted(by)  # 排序确定性
    cr = by["crossval_r"]
    assert (cr["value"], cr["threshold_key"], cr["threshold_value"]) == \
        (0.92, "corr_threshold", 0.85)
    assert cr["threshold_status"] == "PENDING" and cr["verdict"] == "达到"
    uc = by["unwrap_coverage"]
    assert uc["threshold_value"] == 0.7 and uc["verdict"] == "达到"
    rmse = by["crossval_rmse_mm"]
    assert rmse["verdict"] is None and rmse["threshold_key"] is None
    assert facts["qa_status"] == "pass"


def test_facts_model_params_cleaned(env_factory):
    """形变模型(第 9 步)方法与拟合参数入闭集;资源参数 threads 剔除。"""
    store, *_ = env_factory()
    model = build_result_facts(store, RUN_ID)["model"]
    assert model == {"step_id": 9, "name": "形变模型", "method": "linear",
                     "state": "done", "params": {"poly_order": 1}}


def test_facts_metrics_missing_skeleton_honest(env_factory):
    """指标缺失:facts 空表,骨架显式「未记录」,绝不编指标。"""
    store, *_ = env_factory(with_metrics=False)
    facts = build_result_facts(store, RUN_ID)
    assert facts["metrics"] == []
    text = results_skeleton(facts)
    assert "未入账任何质量指标" in text and MISSING in text


def test_facts_unknown_run_raises(env_factory):
    store, *_ = env_factory()
    with pytest.raises(KeyError):
        build_result_facts(store, "no-such-run")


# ---------------------------------------------------------------------------
# ② 骨架:确定性 + 数字闭包 + 强制警示 + 判定如实
# ---------------------------------------------------------------------------

def test_skeleton_deterministic(env_factory):
    store, *_ = env_factory()
    facts = build_result_facts(store, RUN_ID)
    assert results_skeleton(facts) == results_skeleton(copy.deepcopy(facts))
    changed = copy.deepcopy(facts)
    changed["velocity"]["max_mmyr"] = 21.5
    assert results_skeleton(changed) != results_skeleton(facts)


def test_skeleton_numbers_all_traceable(env_factory):
    """数字闭包:骨架每个数值 token 必须能在 results_facts_used 里逐字反查
    ——「每个数字只允许来自账本与产物统计」的机器判定(四种事实形态全覆盖)。"""
    for kw in ({}, {"with_h5": False}, {"with_metrics": False},
               {"with_step9": False}):
        store, *_ = env_factory(**kw)
        facts = build_result_facts(store, RUN_ID)
        allowed = numeric_tokens("\n".join(results_facts_used(facts)))
        assert numeric_tokens(results_skeleton(facts)) <= allowed, kw


def test_skeleton_contains_key_results(env_factory):
    store, *_ = env_factory()
    text = results_skeleton(build_result_facts(store, RUN_ID))
    assert "速度场(mintpy/velocity.h5)显示" in text
    assert "-40.0 至 20.0 mm/yr" in text and "均值 -3.33 mm/yr" in text
    assert "-38.0 与 19.6 mm/yr" in text
    assert "最大沉降速率(LOS 负向)为 40.0 mm/yr" in text
    assert "有效像元占比 75.0%" in text
    assert "crossval_r" in text and "0.92" in text
    assert "达到台账阈值 0.85(corr_threshold,状态 PENDING)" in text
    assert "解缠有效覆盖率" in text and "0.87" in text
    assert "PENDING 的阈值仅产生警告" in text
    assert "linear" in text and "poly_order=1" in text
    assert "QA 总体判定为 pass" in text
    assert SIMULATED_SENTENCE in text


def test_skeleton_qa_fail_and_failed_model_honest(env_factory):
    """失败 run 如实写:QA 判定 fail;形变模型失败 → 拟合结果不作陈述。"""
    store, *_ = env_factory(step9_state="failed")
    facts = build_result_facts(store, RUN_ID)
    assert facts["qa_status"] == "fail"
    text = results_skeleton(facts)
    assert "QA 总体判定为 fail" in text
    assert "状态为 failed,拟合结果不作陈述" in text


def test_skeleton_simulated_sentence_forced(env_factory):
    store, *_ = env_factory(simulated=False)
    facts = build_result_facts(store, RUN_ID)
    assert facts["simulated"] is False
    assert "不构成科学证据" not in results_skeleton(facts)
    store2, *_ = env_factory(simulated=True)
    assert SIMULATED_SENTENCE in results_skeleton(build_result_facts(store2, RUN_ID))


# ---------------------------------------------------------------------------
# ③ 润色校验(mock LLM;校验复用 draft._polish_valid,不过即回退)
# ---------------------------------------------------------------------------

@pytest.fixture()
def facts(env_factory):
    store, *_ = env_factory()
    return build_result_facts(store, RUN_ID)


def _valid_polish(skeleton: str) -> str:
    """构造一份合法改写:只动措辞,数值/方法名/警示句全部保留。"""
    return ("(润色稿)" + skeleton
            .replace("显示:", "分析结果表明:")
            .replace(";", ","))


def test_draft_without_provider_returns_skeleton(facts):
    out = draft_results(None, facts)
    assert out["llm_polish"] is False
    assert out["draft"] == results_skeleton(facts)
    assert out["facts_used"] == results_facts_used(facts)


def test_polish_accepted_when_facts_preserved(facts):
    provider = FakeProvider([lambda system, user: {"draft": _valid_polish(user)}])
    out = draft_results(provider, facts)
    assert out["llm_polish"] is True
    assert out["draft"].startswith("(润色稿)")
    assert provider.calls == 1
    assert numeric_tokens(out["draft"]) == numeric_tokens(results_skeleton(facts))


def test_polish_dropping_number_rejected(facts):
    """润色稿丢数值(交叉验证 0.92 被改成定性词)→ 拒绝,回退骨架。"""
    provider = FakeProvider([lambda system, user:
                             {"draft": user.replace("0.92", "极高")}])
    out = draft_results(provider, facts)
    assert out["llm_polish"] is False and out["draft"] == results_skeleton(facts)


def test_polish_adding_unknown_number_rejected(facts):
    """润色稿多出未知数字(编造 GNSS RMSE 1.2 mm)→ 拒绝,回退骨架。"""
    provider = FakeProvider([lambda system, user:
                             {"draft": user + " 与 GNSS 对比 RMSE 仅 1.2 mm。"}])
    out = draft_results(provider, facts)
    assert out["llm_polish"] is False and out["draft"] == results_skeleton(facts)


def test_polish_changing_model_method_rejected(facts):
    """改形变模型方法名(linear → 线性模型,数值不变)→ 方法名保全校验拒绝。"""
    provider = FakeProvider([lambda system, user:
                             {"draft": user.replace("linear", "线性模型")}])
    out = draft_results(provider, facts)
    assert out["llm_polish"] is False and out["draft"] == results_skeleton(facts)


def test_polish_dropping_simulated_sentence_rejected(facts):
    """模拟 run 的警示句被润掉(该句无数字,数值校验拦不住)→ 专项校验拒绝。"""
    provider = FakeProvider([lambda system, user:
                             {"draft": user.replace(SIMULATED_SENTENCE, "")}])
    out = draft_results(provider, facts)
    assert out["llm_polish"] is False
    assert SIMULATED_SENTENCE in out["draft"]


def test_polish_non_string_and_unavailable_fall_back(facts):
    out = draft_results(FakeProvider([{"draft": 12345}]), facts)
    assert out["llm_polish"] is False and out["draft"] == results_skeleton(facts)
    out = draft_results(FakeProvider([BrainUnavailable("网络失败")]), facts)
    assert out["llm_polish"] is False and out["draft"] == results_skeleton(facts)


# ---------------------------------------------------------------------------
# ④ API:归属校验 / 生成 + 落盘 / provider 注入
# ---------------------------------------------------------------------------

@pytest.fixture()
def api_env(tmp_path):
    """app 只挂 report 路由(test_report_draft 同款隔离模式)。"""
    store, home, ws = _build_env(tmp_path / "api")

    def make_client(provider_factory=None):
        app = FastAPI()
        app.include_router(create_report_router(store, home,
                                                provider_factory=provider_factory))
        return TestClient(app)

    with make_client() as client:
        yield {"client": client, "store": store, "ws": ws, "tmp": tmp_path,
               "make_client": make_client}
    store.close()


def test_api_results_ok_and_saved(api_env):
    r = api_env["client"].post("/api/report/results", json={"session": "sess-a"})
    assert r.status_code == 200
    data = r.json()
    assert set(data) == {"run_id", "draft", "llm_polish", "facts_used", "saved"}
    assert data["run_id"] == RUN_ID
    assert data["llm_polish"] is False           # home 无 llm.json → 骨架模式
    assert data["saved"] is True
    assert "-40.0 至 20.0 mm/yr" in data["draft"]
    assert "crossval_r" in data["draft"] and "0.92" in data["draft"]
    assert "不构成科学证据" in data["draft"]     # 模拟 run 强制警示
    assert any(x == "velocity.min_mmyr=-40.0" for x in data["facts_used"])
    saved = (api_env["ws"] / RESULTS_FILENAME).read_text(encoding="utf-8")
    assert saved == data["draft"]


def test_api_explicit_run_id_same_run(api_env):
    r = api_env["client"].post("/api/report/results",
                               json={"session": "sess-a", "run_id": RUN_ID})
    assert r.status_code == 200 and r.json()["run_id"] == RUN_ID


def test_api_cross_session_404_no_leak(api_env):
    """sess-b 借 run_id 生成 sess-a 的结果章节:按「不存在」处理,不泄露路径。"""
    r = api_env["client"].post("/api/report/results",
                               json={"session": "sess-b", "run_id": RUN_ID})
    assert r.status_code == 404
    leak = str(api_env["tmp"]).replace("\\", "/")
    assert leak not in r.text.replace("\\\\", "/").replace("\\", "/")


def test_api_no_run_404(api_env):
    r = api_env["client"].post("/api/report/results", json={"session": "sess-b"})
    assert r.status_code == 404


def test_api_polish_via_injected_provider(api_env):
    """provider 注入合法润色 → llm_polish=true 且落盘的是润色稿。"""
    fake = FakeProvider([lambda system, user: {"draft": "(润色稿)" + user}])
    with api_env["make_client"](provider_factory=lambda: fake) as client:
        data = client.post("/api/report/results", json={"session": "sess-a"}).json()
    assert data["llm_polish"] is True
    assert data["draft"].startswith("(润色稿)")
    assert fake.calls == 1
    saved = (api_env["ws"] / RESULTS_FILENAME).read_text(encoding="utf-8")
    assert saved == data["draft"]


def test_api_bad_polish_falls_back_to_skeleton(api_env):
    """provider 注入编数润色 → 回退骨架,llm_polish=false(端到端护栏)。"""
    fake = FakeProvider([lambda system, user: {"draft": user + " 精度达 0.001 mm。"}])
    with api_env["make_client"](provider_factory=lambda: fake) as client:
        data = client.post("/api/report/results", json={"session": "sess-a"}).json()
    assert data["llm_polish"] is False
    assert "0.001" not in data["draft"]


def test_api_response_is_json_serializable_roundtrip(api_env):
    """facts_used 全部为字符串(前端逐条展示的契约)。"""
    data = api_env["client"].post("/api/report/results",
                                  json={"session": "sess-a"}).json()
    assert all(isinstance(x, str) for x in data["facts_used"])
    json.dumps(data, ensure_ascii=False)  # 不抛
