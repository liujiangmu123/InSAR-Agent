# -*- coding: utf-8 -*-
"""方法章节质量门(UI-DETAILS-AUDIT 第 3 条配套,report/methods.py 升级验收)。

用旅程夹具(与 test_e2e_contract 同款密封环境)造一个 done run,锁定:
  1. 生成文本覆盖全部执行步骤的方法名;云端跳过步骤如实声明来源;
  2. 数字可溯:正文(剔除〔ref:…〕文献锚点后)的每一个数字都能在
     provenance JSON 里逐字找到;〔prov-…〕引用逐个反查到对应节点,
     引用前的数字必须出现在被引节点里;
  3. 证据边界声明与 provenance.evidence.level 完全一致(级别/阶梯/原因);
  4. 无 run 的空态:HTTP 404(前端回落演示)+ 空 provenance 的诚实文案;
  5. /api/methods.md 响应头契约:X-Narrate-Source ∈ {template, llm},
     Content-Type 显式 charset=utf-8(前端 reportlive.js 读取该头标注来源)。
"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from insar_agent.api.app import create_app
from insar_agent.report.methods import methods_markdown

# 〔ref:…〕是文献/依据锚点:年份、DOI、惯例区间等数字属于引用注释,
# 不是"本次运行的数据",数字可溯审计前先剔除
_REF_RE = re.compile(r"〔ref:[^〕]*〕")
_PROV_RE = re.compile(r"〔prov-([^〕]+)〕")
_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    """密封环境(module 级复用:旅程只跑一次,全模块断言共享)。"""
    from insar_agent.runtime.probe import ProbeResult

    def empty_probe(*args, **kwargs):
        return ProbeResult(
            engines={k: None for k in ("isce2", "mintpy", "snaphu", "gdal", "snap",
                                       "pystamps", "pyaps")},
            credentials={"earthdata": False, "cds": False, "gacos": False},
            disk_free_gb=100.0, cpu_count=8)

    mp = pytest.MonkeyPatch()
    mp.setattr("insar_agent.loop.driver.probe_environment", empty_probe)
    for var in ("INSAR_LLM_BASE_URL", "INSAR_LLM_MODEL",
                "INSAR_LLM_FALLBACK_BASE_URL", "INSAR_LLM_FALLBACK_MODEL"):
        mp.delenv(var, raising=False)
    mp.setenv("INSAR_ALLOW_SIMULATED", "1")
    app = create_app(home=tmp_path_factory.mktemp("home"))
    with TestClient(app) as c:
        yield c
    mp.undo()


def _drain(client: TestClient, url: str, body: dict) -> None:
    with client.stream("POST", url, json=body) as resp:
        assert resp.status_code == 200
        for _ in resp.iter_lines():
            pass


@pytest.fixture(scope="module")
def journey(client):
    """旅程夹具:规划 + 执行(simulated,秒级)→ done run 的账本与方法草稿。"""
    client.post("/api/sessions", json={"id": "mq"})
    _drain(client, "/api/turn", {"session": "mq", "text": "分析 Ridgecrest 2019 地震同震形变"})
    _drain(client, "/api/pipeline", {"session": "mq"})
    state = client.get("/api/state", params={"session": "mq"}).json()
    assert state["run"]["status"] == "done", "旅程夹具必须产出 done run"
    prov = client.get("/api/provenance", params={"session": "mq"}).json()
    resp = client.get("/api/methods.md", params={"session": "mq"})
    assert resp.status_code == 200
    return SimpleNamespace(prov=prov, md=resp.text, resp=resp)


# ---------------------------------------------------------------------------
# 1. 处理链覆盖:全部执行步骤的方法名都在文中;跳过步骤如实声明
# ---------------------------------------------------------------------------

def test_methods_covers_all_executed_steps(journey):
    md, prov = journey.md, journey.prov
    done = {sid: s for sid, s in prov["steps"].items() if s["state"] == "done"}
    skipped = {sid: s for sid, s in prov["steps"].items() if s["state"] == "skipped"}
    assert done, "旅程应有已执行步骤"

    for sid, s in done.items():
        assert s["name"] in md, f"执行步骤 {sid} 的名称 {s['name']} 未出现在草稿"
        assert f"`{s['method']}`" in md, f"执行步骤 {sid} 的方法 {s['method']} 未出现在草稿"
        assert f"〔prov-{sid}〕" in md, f"执行步骤 {sid} 缺可溯引用标记"

    # 云端跳过步骤:名称出现 + 云端来源声明 + 凭据状态如实(本旅程无 manifest)
    if skipped:
        for sid, s in skipped.items():
            assert s["name"] in md, f"跳过步骤 {sid} 的名称应在云端声明句中出现"
        assert "云端" in md and "HyP3" in md
        assert "hyp3_manifest.json" in md, "无 manifest 时必须声明凭据缺失"


# ---------------------------------------------------------------------------
# 2. 数字可溯:全文审计 + 引用逐个反查
# ---------------------------------------------------------------------------

def test_every_number_appears_in_provenance(journey):
    """剔除〔ref:…〕锚点后,正文每个数字都能在 provenance JSON 里逐字找到
    (生成器纪律:模板不携带裸数字,数值一律 str(provenance 原值)直排)。"""
    body = _REF_RE.sub("", journey.md)
    hay = json.dumps(journey.prov, ensure_ascii=False)
    misses = [n for n in _NUM_RE.findall(body) if n not in hay]
    assert not misses, f"以下数字在 provenance 里找不到出处:{sorted(set(misses))}"


def test_prov_citations_resolve_and_cover_preceding_numbers(journey):
    """〔prov-…〕逐个反查:步骤引用必须命中 steps,指标引用必须命中 metrics;
    同一行内引用之前的数字必须出现在被引节点的 JSON 里(强可溯性)。"""
    md, prov = journey.md, journey.prov
    citations = _PROV_RE.findall(md)
    assert citations, "草稿必须携带〔prov-…〕可溯引用"

    for line in _REF_RE.sub("", md).splitlines():
        pos = 0
        for m in _PROV_RE.finditer(line):
            target = m.group(1)
            segment = line[pos:m.start()]
            pos = m.end()
            if target.isdigit():
                assert target in prov["steps"], f"引用了不存在的步骤:{target}"
                node = json.dumps(prov["steps"][target], ensure_ascii=False)
            elif "#" in target:
                art, field = target.split("#", 1)
                hits = [v for v in prov["metrics"].values()
                        if v["source_artifact"] == art and v["source_field"] == field]
                assert hits, f"指标引用未命中 metrics:{target}"
                node = json.dumps(hits, ensure_ascii=False)
            else:
                pytest.fail(f"未知引用形态:〔prov-{target}〕")
            for num in _NUM_RE.findall(segment):
                assert num in node, (
                    f"数字 {num} 出现在〔prov-{target}〕引用之前,"
                    f"但被引节点里没有它:{segment!r}")


# ---------------------------------------------------------------------------
# 3. 证据边界与 evidence.level 一致
# ---------------------------------------------------------------------------

def test_evidence_boundary_matches_provenance(journey):
    md, ev = journey.md, journey.prov["evidence"]
    assert "## 证据边界" in md
    assert f"**{ev['level']}**" in md, "声明级别必须与 evidence.level 一字不差"
    assert " → ".join(ev["ladder"]) in md, "六级阶梯词汇表必须与后端一致"
    for reason in ev["reasons"]:
        assert reason in md, f"级别判定原因缺失:{reason}"
    # 未达级别逐级声明(不越级):当前级之上的每一级都有「尚未达到」行
    idx = ev["ladder"].index(ev["level"])
    for higher in ev["ladder"][idx + 1:]:
        assert f"尚未达到 {higher}" in md
    # simulated run 必须反复声明演示性质
    if journey.prov["simulated"]:
        assert "模拟执行" in md


def test_pending_thresholds_disclosed(journey):
    md = journey.md
    pending = [k for k, t in journey.prov["thresholds"].items()
               if t["status"] == "PENDING"]
    for key in pending:
        assert key in md, f"PENDING 阈值 {key} 未披露"
    assert "PENDING 待标定" in md, "PENDING 阈值的锚点必须标注待标定"


# ---------------------------------------------------------------------------
# 4. 文献锚点与图件引用
# ---------------------------------------------------------------------------

def test_literature_anchors_from_contract(journey):
    md, thresholds = journey.md, journey.prov["thresholds"]
    assert "〔ref:" in md, "关键参数后必须有依据锚点"
    # 台账有 ref 的参数(本旅程第 7 步执行了 max_temporal_baseline)引用台账原文
    assert thresholds["max_temporal_baseline"]["ref"] in md
    # 台账没有的参数诚实写「本项目配置」
    assert "本项目配置" in md


def test_figure_citation_when_figure_artifacts_exist(journey):
    md = journey.md
    figs = {aid: a for aid, a in journey.prov["artifacts"].items()
            if a["kind"] == "FIGURE"}
    assert figs, "旅程夹具应产出 FIGURE 产物(出图导出步)"
    assert "## 图件引用" in md
    assert "Figure 1" in md, "图件段必须给出 Figure X 句式"
    for a in figs.values():
        assert a["path"] in md, f"图件产物路径 {a['path']} 未被引用"


def test_no_figure_section_without_figure_artifacts():
    md = methods_markdown({"steps": {}, "artifacts": {}, "metrics": {}})
    assert "## 图件引用" not in md


# ---------------------------------------------------------------------------
# 5. 空态与响应头契约
# ---------------------------------------------------------------------------

def test_no_run_returns_404(client):
    """无 run 的会话:404 —— 前端 reportlive.js 拿 null 回落演示草稿。"""
    client.post("/api/sessions", json={"id": "mq-empty"})
    resp = client.get("/api/methods.md", params={"session": "mq-empty"})
    assert resp.status_code == 404


def test_empty_provenance_honest_text():
    """空 provenance 直调(防御路径):不崩溃、如实声明无步骤记录。"""
    md = methods_markdown({})
    assert "没有任何步骤执行记录" in md
    assert "证据边界" in md  # 章节骨架完整,不因空数据丢段


def test_narrate_source_header_contract(journey):
    """前端 reportlive.js 读取的 metadata 头:X-Narrate-Source 与 charset。"""
    resp = journey.resp
    assert resp.headers.get("X-Narrate-Source") in ("template", "llm")
    # 本夹具清空了 LLM 路由 → 必然走确定性模板
    assert resp.headers["X-Narrate-Source"] == "template"
    ctype = resp.headers.get("content-type", "")
    assert ctype.startswith("text/plain") and "charset=utf-8" in ctype


def test_methods_markdown_accepts_explicit_contract(journey):
    """纯函数契约:显式传 audit.contract 台账(Threshold 对象)与缺省
    (provenance 内嵌 thresholds)产出一致 —— 两种输入形态都被支持。"""
    from insar_agent.audit.contract import load_contract

    via_default = methods_markdown(journey.prov)
    via_contract = methods_markdown(journey.prov, contract=load_contract())
    assert via_default == via_contract
