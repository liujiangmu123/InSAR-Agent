"""论文图注(report/captions.py + /api/report/caption)的事实纪律测试。

覆盖(任务验收清单):
  - facts 抽取:速度图 sidecar(全字段)/ 直方图 sidecar(缺色标/值域/参考点,
    时间窗回落账本 dates 参数)/ 无 sidecar / 空账本 → 缺项显式「未记录」;
    sidecar step 优先于产物行 step;资源参数与内部键剔除;
  - 骨架:确定性(同 facts 必得同文本)+ 双语一致(中英数值 token 集逐字相同)
    + 数字闭包(骨架数值均可在 facts JSON 里反查)+ 模拟警示双语强制;
  - 润色校验:mock LLM 丢数值 / 编数值 / 改方法名 / 丢警示句 / 缺语种 /
    非字符串 / 抛 BrainUnavailable → 一律双语回退骨架(llm_polish=False);
    合法改写 → 采纳(llm_polish=True);
  - locate_figure:/api/figures 同口径(目录成员 / 单文件产物 / 三档归并 /
    路径穿越与越界拒绝);
  - API:归属校验(跨会话 404 不泄露)/ 未知图件与三档 404 / 正常生成 +
    落盘 <name>.caption.json / GET 读取已落盘图注 / provider 注入时润色生效。

路由挂接:测试直接 include create_report_router(store, home)
(test_report_draft.py 同款隔离模式,不拉起整个 create_app)。
"""

from __future__ import annotations

import copy
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from insar_agent.api.report_router import create_report_router
from insar_agent.brain.provider import BrainUnavailable, LLMProvider, LLMRoute
from insar_agent.core.db import Database
from insar_agent.core.store import Store
from insar_agent.report.captions import (
    CAPTION_SUFFIX,
    SIMULATED_EN,
    SIMULATED_ZH,
    build_caption_facts,
    caption_skeleton,
    generate_caption,
    load_caption,
    locate_figure,
)
from insar_agent.report.draft import MISSING, numeric_tokens

# ---------------------------------------------------------------------------
# 样本(sidecar 与 engines/figures.py 落盘同形;账本与 export_provenance 同形)
# ---------------------------------------------------------------------------

def velocity_meta() -> dict:
    return {
        "title": "InSAR LOS velocity",
        "units": "mm/yr",
        "cmap": "cmc.vik",
        "vlim": [-23.4, 23.4],
        "date_range": ["20190704", "20190716"],
        "ref_point": [35.77, -117.61],
        "step": 10,
        "params": {"dpi": 600, "cmap": "vik", "format": "png+pdf"},
    }


def hist_meta() -> dict:
    """直方图 sidecar:只写确有依据的字段(无色标/值域/参考点/时间窗)。"""
    return {"title": "LOS velocity histogram", "units": "mm/yr", "step": 10,
            "params": {"dpi": 600, "cmap": "vik", "format": "png+pdf",
                       "threads": 8, "_probe": "内部键不入闭集"}}


def sample_provenance() -> dict:
    return {
        "schema_version": "1.0",
        "run_id": "20260813T090000-c0001",
        "session_id": "sess-a",
        "generated_at_utc": "2026-08-13T09:12:00Z",
        "simulated": True,
        "environment": {"python": "3.11.9", "platform": "win32",
                        "tools": {"isce2": "2.6.3", "mintpy": "1.5.1"}},
        "intent": {"goal": "Ridgecrest 2019 同震形变分析"},
        "scenario": "coseismic_interferogram",
        "steps": {
            "10": {"name": "出图导出", "capability": "figures",
                   "method": "figure_journal", "state": "done",
                   "params": {"dpi": 600, "cmap": "vik", "threads": 8}},
            "1": {"name": "数据获取", "capability": "acquire",
                  "method": "local_import", "state": "done",
                  "params": {"platform": "Sentinel-1", "scenes": 12,
                             "dates": "20190704-20190716", "_probe": "内部键"}},
            "2": {"name": "辅助数据", "capability": "aux",
                  "method": "hyp3_submit", "state": "skipped", "params": {}},
            "5": {"name": "滤波", "capability": "filter", "method": "goldstein",
                  "state": "done", "params": {"alpha": 0.6}},
            "6": {"name": "解缠", "capability": "unwrap", "method": "snaphu_mcf",
                  "state": "failed", "failure_class": "engine_missing",
                  "params": {"cost_mode": "SMOOTH"}},
        },
        "artifacts": {"vel_png": {"path": "products/figures/velocity.png",
                                  "kind": "FIGURE"}},
        "metrics": {
            "unwrap_coverage": {"value": 0.87, "unit": ""},
            "crossval_r": {"value": 0.93, "unit": ""},
        },
        "qa": {"status": "fail"},
        "evidence_level": "runnable",
        "interventions": [],
    }


# ---------------------------------------------------------------------------
# ① facts 抽取(各产物类型)
# ---------------------------------------------------------------------------

def test_facts_velocity_full_extraction():
    f = build_caption_facts(velocity_meta(), sample_provenance(), step=10)
    assert f["title"] == "InSAR LOS velocity"
    assert f["units"] == "mm/yr" and f["cmap"] == "cmc.vik"
    assert f["vlim"] == [-23.4, 23.4]
    assert f["time_window"] == ["20190704", "20190716"]   # sidecar 优先
    assert f["ref_point"] == [35.77, -117.61]
    assert f["step_id"] == 10 and f["step_method"] == "figure_journal"
    assert f["figure_params"] == {"dpi": 600, "cmap": "vik", "format": "png+pdf"}
    assert f["platform"] == "Sentinel-1"
    assert f["subject"] == "Ridgecrest 2019 同震形变分析"
    assert f["qa_status"] == "fail" and f["simulated"] is True
    # 处理链:done 步按序,出图步骤(10)不重复进链;skipped/failed 不进
    assert [m["step_id"] for m in f["methods"]] == [1, 5]
    assert f["methods"][1]["method"] == "goldstein"
    assert f["methods"][1]["params"] == {"alpha": 0.6}


def test_facts_histogram_missing_fields_and_window_fallback():
    """直方图 sidecar:缺色标/值域/参考点 → 未记录/None;时间窗回落账本 dates。"""
    f = build_caption_facts(hist_meta(), sample_provenance(), step=10)
    assert f["cmap"] == MISSING
    assert f["vlim"] is None and f["ref_point"] is None
    assert f["time_window"] == ["20190704-20190716"]      # 账本 dates 参数兜底
    # sidecar params 的资源参数与内部键剔除
    assert "threads" not in f["figure_params"] and "_probe" not in f["figure_params"]


def test_facts_no_sidecar_and_empty_ledger():
    f = build_caption_facts(None, {}, step=3)
    for key in ("title", "units", "cmap", "subject", "platform",
                "qa_status", "step_method", "scenario", "run_id"):
        assert f[key] == MISSING, key
    assert f["vlim"] is None and f["ref_point"] is None
    assert f["time_window"] == MISSING
    assert f["step_id"] == 3                                # 产物行 step 兜底
    assert f["methods"] == [] and f["metrics"] == []
    assert f["simulated"] is False


def test_facts_sidecar_step_wins_over_artifact_step():
    f = build_caption_facts(velocity_meta(), sample_provenance(), step=99)
    assert f["step_id"] == 10                               # sidecar 与图同源,更可信
    assert f["step_method"] == "figure_journal"


def test_facts_subject_falls_back_to_session():
    doc = sample_provenance()
    doc["intent"] = {}
    assert build_caption_facts(None, doc)["subject"] == "sess-a"


def test_facts_ledger_resource_params_filtered():
    f = build_caption_facts(velocity_meta(), sample_provenance(), step=10)
    p1 = {m["step_id"]: m for m in f["methods"]}[1]["params"]
    assert "_probe" not in p1 and p1["scenes"] == 12


# ---------------------------------------------------------------------------
# ② 骨架:确定性 + 双语一致 + 数字闭包 + 模拟警示
# ---------------------------------------------------------------------------

@pytest.fixture()
def facts():
    return build_caption_facts(velocity_meta(), sample_provenance(), step=10)


def test_skeleton_deterministic(facts):
    assert caption_skeleton(facts, "zh") == caption_skeleton(copy.deepcopy(facts), "zh")
    assert caption_skeleton(facts, "en") == caption_skeleton(copy.deepcopy(facts), "en")
    changed = copy.deepcopy(facts)
    changed["methods"][1]["params"]["alpha"] = 0.8
    assert caption_skeleton(changed, "zh") != caption_skeleton(facts, "zh")


def test_skeleton_bilingual_same_numeric_tokens(facts):
    """双语一致:同一 facts 的中英骨架数值 token 集逐字相同(模块不变量)。"""
    assert numeric_tokens(caption_skeleton(facts, "zh")) \
        == numeric_tokens(caption_skeleton(facts, "en"))


def test_skeleton_numbers_all_traceable_to_facts(facts):
    """数字闭包:骨架里每个数值 token 必须能在事实闭集 JSON 里逐字反查。"""
    allowed = numeric_tokens(json.dumps(facts, ensure_ascii=False))
    assert numeric_tokens(caption_skeleton(facts, "zh")) <= allowed
    assert numeric_tokens(caption_skeleton(facts, "en")) <= allowed


def test_skeleton_contains_key_facts(facts):
    zh = caption_skeleton(facts, "zh")
    en = caption_skeleton(facts, "en")
    assert zh.startswith("图 X:") and en.startswith("Figure X:")
    for text in (zh, en):
        assert "goldstein" in text and "alpha=0.6" in text   # 方法 + 关键参数
        assert "Sentinel-1" in text                          # 卫星平台
        assert "±23.4" in text and "mm/yr" in text           # 值域(对称)+ 单位
        assert "35.77" in text and "117.61" in text          # 参考点
        assert "figure_journal" in text and "dpi=600" in text
        assert "unwrap_coverage = 0.87" in text              # QA 相关值
        assert "20190704" in text and "20190716" in text     # 时间窗
    assert "参考点(图中黑色方块)" in zh
    assert "reference point (black square)" in en
    assert "snaphu_mcf" not in zh                            # failed 步不进图注


def test_skeleton_missing_fields_rendered_honestly():
    f = build_caption_facts(hist_meta(), {}, step=10)
    zh = caption_skeleton(f, "zh")
    en = caption_skeleton(f, "en")
    assert "色标:未记录" in zh and "Colour scale: not recorded" in en
    assert "参考点未记录" in zh and "Reference point not recorded" in en
    assert "时间窗未记录" in zh and "time window not recorded" in en
    assert numeric_tokens(zh) == numeric_tokens(en)


def test_skeleton_simulated_sentence_forced_bilingual(facts):
    assert SIMULATED_ZH in caption_skeleton(facts, "zh")
    assert SIMULATED_EN in caption_skeleton(facts, "en")
    plain = copy.deepcopy(facts)
    plain["simulated"] = False
    assert "不构成科学证据" not in caption_skeleton(plain, "zh")
    assert "not scientific evidence" not in caption_skeleton(plain, "en")


def test_skeleton_asymmetric_vlim_and_bad_lang(facts):
    asym = copy.deepcopy(facts)
    asym["vlim"] = [-5.0, 40.0]
    assert "-5.0 至 40.0" in caption_skeleton(asym, "zh")
    assert "-5.0 to 40.0" in caption_skeleton(asym, "en")
    with pytest.raises(ValueError):
        caption_skeleton(facts, "fr")


# ---------------------------------------------------------------------------
# ③ 润色校验(mock LLM;任一语种任一项不过 → 双语回退)
# ---------------------------------------------------------------------------

class FakeProvider(LLMProvider):
    """按脚本返回响应的假 LLM(不走网络;test_report_draft.py 同款)。"""

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


def _skeletons(facts) -> dict:
    return {"zh": caption_skeleton(facts, "zh"), "en": caption_skeleton(facts, "en")}


def _echo_polish(mutate_zh=None, mutate_en=None):
    """构造响应函数:回读送审骨架,按需改写其中一种语言。"""
    def responder(system, user):
        d = json.loads(user)
        zh = ("(润色)" + d["zh"].replace("。", ",", 1)) if mutate_zh is None \
            else mutate_zh(d["zh"])
        en = ("Polished: " + d["en"]) if mutate_en is None else mutate_en(d["en"])
        return {"zh": zh, "en": en}
    return responder


def test_caption_without_provider_returns_skeleton(facts):
    out = generate_caption(None, facts)
    assert out == {**_skeletons(facts), "llm_polish": False}
    out = generate_caption(LLMProvider(routes=[]), facts)
    assert out["llm_polish"] is False


def test_caption_polish_accepted_when_facts_preserved(facts):
    provider = FakeProvider([_echo_polish()])
    out = generate_caption(provider, facts)
    assert out["llm_polish"] is True and provider.calls == 1
    assert out["zh"].startswith("(润色)") and out["en"].startswith("Polished: ")
    # 采纳稿与骨架数值双向一致
    sk = _skeletons(facts)
    assert numeric_tokens(out["zh"]) == numeric_tokens(sk["zh"])
    assert numeric_tokens(out["en"]) == numeric_tokens(sk["en"])


def test_caption_polish_dropping_number_rejected(facts):
    provider = FakeProvider([_echo_polish(
        mutate_zh=lambda t: t.replace("alpha=0.6", "alpha=适中"))])
    out = generate_caption(provider, facts)
    assert out["llm_polish"] is False and out == {**_skeletons(facts), "llm_polish": False}


def test_caption_polish_adding_unknown_number_rejected(facts):
    provider = FakeProvider([_echo_polish(
        mutate_en=lambda t: t + " Cross-correlation reaches 0.99.")])
    out = generate_caption(provider, facts)
    assert out["llm_polish"] is False and "0.99" not in out["en"]


def test_caption_polish_changing_method_name_rejected(facts):
    """改方法名(goldstein → 自适应滤波,数值不变)→ 任一语种不过即双语回退。"""
    provider = FakeProvider([_echo_polish(
        mutate_en=lambda t: t.replace("goldstein", "adaptive"))])
    out = generate_caption(provider, facts)
    assert out["llm_polish"] is False and out["en"] == _skeletons(facts)["en"]


def test_caption_polish_dropping_simulated_sentence_rejected(facts):
    """警示句无数字,数值校验拦不住 → 专项校验拒绝(双语各自把关)。"""
    provider = FakeProvider([_echo_polish(
        mutate_zh=lambda t: t.replace(SIMULATED_ZH, ""))])
    out = generate_caption(provider, facts)
    assert out["llm_polish"] is False and SIMULATED_ZH in out["zh"]


def test_caption_polish_missing_language_or_non_string_rejected(facts):
    assert generate_caption(FakeProvider(
        [lambda system, user: {"zh": json.loads(user)["zh"]}]), facts)["llm_polish"] is False
    assert generate_caption(FakeProvider([{"zh": 42, "en": 42}]), facts)["llm_polish"] is False


def test_caption_polish_brain_unavailable_falls_back(facts):
    out = generate_caption(FakeProvider([BrainUnavailable("网络失败")]), facts)
    assert out == {**_skeletons(facts), "llm_polish": False}


# ---------------------------------------------------------------------------
# ④ locate_figure(/api/figures 同口径的定位规则)
# ---------------------------------------------------------------------------

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def _run_of(ws) -> dict:
    return {"run_id": "r1", "workspace": str(ws)}


def test_locate_figure_directory_member_and_tier_merge(tmp_path):
    fig = tmp_path / "products" / "figures"
    fig.mkdir(parents=True)
    for name in ("velocity.png", "velocity_browse.png", "velocity_thumb.png",
                 "orphan_browse.png"):
        (fig / name).write_bytes(PNG)
    arts = [{"path": "products/figures", "kind": "FIGURE", "step_id": 10, "art_id": "figs"}]
    run = _run_of(tmp_path)

    found = locate_figure(run, arts, "velocity.png")
    assert found is not None and found[0].name == "velocity.png"
    assert found[1]["art_id"] == "figs"
    # 三档归并:基图在场的 _browse/_thumb 不是独立条目
    assert locate_figure(run, arts, "velocity_browse.png") is None
    assert locate_figure(run, arts, "velocity_thumb.png") is None
    # 孤档(基图缺失)仍按普通图件对待(与 /api/figures 列表口径一致)
    assert locate_figure(run, arts, "orphan_browse.png")[0].name == "orphan_browse.png"


def test_locate_figure_single_file_artifact(tmp_path):
    (tmp_path / "products").mkdir()
    (tmp_path / "products" / "plot.png").write_bytes(PNG)
    arts = [{"path": "products/plot.png", "kind": "FIGURE", "step_id": 9, "art_id": "p"}]
    assert locate_figure(_run_of(tmp_path), arts, "plot.png")[0].name == "plot.png"
    assert locate_figure(_run_of(tmp_path), arts, "other.png") is None


def test_locate_figure_rejects_traversal_and_non_image(tmp_path):
    fig = tmp_path / "products" / "figures"
    fig.mkdir(parents=True)
    (fig / "velocity.png").write_bytes(PNG)
    (tmp_path / "evil.png").write_bytes(PNG)
    arts = [{"path": "products/figures", "kind": "FIGURE", "step_id": 10, "art_id": "figs"}]
    run = _run_of(tmp_path)
    assert locate_figure(run, arts, "../evil.png") is None          # 路径成分拒绝
    assert locate_figure(run, arts, "..\\evil.png") is None
    assert locate_figure(run, arts, "velocity.txt") is None         # 扩展名闭集
    assert locate_figure(run, arts, "") is None
    # 产物行本身越界(绝对路径 / ../)一律跳过
    bad = [{"path": str(tmp_path / "evil.png"), "step_id": 1, "art_id": "a"},
           {"path": "../evil.png", "step_id": 1, "art_id": "b"}]
    assert locate_figure(run, bad, "evil.png") is None


# ---------------------------------------------------------------------------
# ⑤ API:归属 / 404 / 生成 + 落盘 / GET 读取 / provider 注入
# ---------------------------------------------------------------------------

RUN_ID = "20260813T100000-cap01"
_HASHES = {"task_hash": "t1", "args_hash": "a1", "local_hash": "l1", "eval_hash": "e1"}


@pytest.fixture()
def env(tmp_path):
    """两个会话 + sess-a 名下一个手搭的模拟 run(数据获取/滤波/出图三个 done 步 +
    指标 + products/figures 目录产物:velocity.png 三档 + sidecar)。"""
    home = tmp_path / "home"
    home.mkdir()
    store = Store(Database(home / "insar.db"))
    store.create_session("sess-a", "sess-a")
    store.create_session("sess-b", "sess-b")
    ws = tmp_path / "sessions" / "sess-a"
    fig_dir = ws / "products" / "figures"
    fig_dir.mkdir(parents=True)
    store.create_run(RUN_ID, "sess-a", workspace=str(ws), simulated=True,
                     intent={"goal": "Ridgecrest 2019 同震形变分析"},
                     scenario="coseismic", tool_versions={"mintpy": "1.5.1"})
    for sid, cap, name, method, params in (
            (1, "acquire", "数据获取", "local_import",
             {"platform": "Sentinel-1", "scenes": 12, "dates": "20190704-20190716"}),
            (5, "filter", "滤波", "goldstein", {"alpha": 0.6}),
            (10, "figures", "出图导出", "figure_journal", {"dpi": 600, "cmap": "vik"}),
    ):
        store.upsert_step(RUN_ID, sid, capability=cap, name=name, method=method,
                          params=params, hashes=_HASHES)
        store.advance(RUN_ID, sid, "VERIFIED", state="done", run_ok=1,
                      qa=[], exit_code=0)
    store.record_metric(RUN_ID, "unwrap_coverage", value=0.87,
                        source_artifact="qa.json", source_field="coverage",
                        reparsed_ok=True)
    for name in ("velocity.png", "velocity_browse.png", "velocity_thumb.png"):
        (fig_dir / name).write_bytes(PNG)
    (fig_dir / "velocity.json").write_text(
        json.dumps(velocity_meta(), ensure_ascii=False), encoding="utf-8")
    store.record_artifact(RUN_ID, 10, "figs", path="products/figures", kind="FIGURE",
                          layout="", policy="stat", fp="stat:sha256:deadbeef")

    def make_client(provider_factory=None):
        app = FastAPI()
        app.include_router(create_report_router(store, home,
                                                provider_factory=provider_factory))
        return TestClient(app)

    with make_client() as client:
        yield {"client": client, "store": store, "ws": ws, "fig_dir": fig_dir,
               "tmp": tmp_path, "make_client": make_client}
    store.close()


def test_api_caption_ok_and_saved(env):
    r = env["client"].post("/api/report/caption",
                           json={"session": "sess-a", "figure": "velocity.png"})
    assert r.status_code == 200
    data = r.json()
    assert set(data) == {"run_id", "figure", "zh", "en", "llm_polish", "saved"}
    assert data["run_id"] == RUN_ID and data["figure"] == "velocity.png"
    assert data["llm_polish"] is False          # home 无 llm.json → 骨架模式
    assert data["saved"] is True
    for text in (data["zh"], data["en"]):
        assert "goldstein" in text and "alpha=0.6" in text
        assert "Sentinel-1" in text and "±23.4" in text
    assert data["zh"].startswith("图 X:") and data["en"].startswith("Figure X:")
    assert "不构成科学证据" in data["zh"]
    assert "not scientific evidence" in data["en"]
    assert numeric_tokens(data["zh"]) == numeric_tokens(data["en"])
    # 落盘图件旁 <name>.caption.json,内容与响应一致(saved 字段不落盘)
    saved = json.loads((env["fig_dir"] / f"velocity{CAPTION_SUFFIX}")
                       .read_text(encoding="utf-8"))
    assert saved == {k: v for k, v in data.items() if k != "saved"}
    assert load_caption(env["fig_dir"] / "velocity.png") == saved


def test_api_get_saved_caption_roundtrip(env):
    c = env["client"]
    params = {"session": "sess-a", "figure": "velocity.png"}
    assert c.get("/api/report/caption", params=params).status_code == 404  # 尚未生成
    posted = c.post("/api/report/caption", json=params).json()
    got = c.get("/api/report/caption", params=params)
    assert got.status_code == 200
    data = got.json()
    assert data["zh"] == posted["zh"] and data["en"] == posted["en"]
    assert data["llm_polish"] is False and data["run_id"] == RUN_ID


def test_api_cross_session_404_no_leak(env):
    r = env["client"].post("/api/report/caption", json={
        "session": "sess-b", "run_id": RUN_ID, "figure": "velocity.png"})
    assert r.status_code == 404
    leak = str(env["tmp"]).replace("\\", "/")
    assert leak not in r.text.replace("\\\\", "/").replace("\\", "/")


def test_api_unknown_figure_tier_and_traversal_404(env):
    c = env["client"]
    for figure in ("nope.png", "velocity_browse.png", "../velocity.png",
                   "velocity.txt"):
        r = c.post("/api/report/caption",
                   json={"session": "sess-a", "figure": figure})
        assert r.status_code == 404, figure
        assert str(env["tmp"]).replace("\\", "/") \
            not in r.text.replace("\\\\", "/").replace("\\", "/")
    assert c.get("/api/report/caption",
                 params={"session": "sess-a", "figure": "nope.png"}).status_code == 404


def test_api_polish_via_injected_provider(env):
    fake = FakeProvider([_echo_polish()])
    with env["make_client"](provider_factory=lambda: fake) as client:
        data = client.post("/api/report/caption",
                           json={"session": "sess-a", "figure": "velocity.png"}).json()
    assert data["llm_polish"] is True and fake.calls == 1
    assert data["zh"].startswith("(润色)")
    # 落盘的就是润色稿
    saved = load_caption(env["fig_dir"] / "velocity.png")
    assert saved["zh"] == data["zh"] and saved["llm_polish"] is True


def test_api_bad_polish_falls_back_end_to_end(env):
    fake = FakeProvider([_echo_polish(mutate_zh=lambda t: t + " 精度达 0.001 mm。")])
    with env["make_client"](provider_factory=lambda: fake) as client:
        data = client.post("/api/report/caption",
                           json={"session": "sess-a", "figure": "velocity.png"}).json()
    assert data["llm_polish"] is False and "0.001" not in data["zh"]


def test_api_response_json_serializable(env):
    data = env["client"].post("/api/report/caption",
                              json={"session": "sess-a", "figure": "velocity.png"}).json()
    json.dumps(data, ensure_ascii=False)  # 不抛
    assert isinstance(data["zh"], str) and isinstance(data["en"], str)
