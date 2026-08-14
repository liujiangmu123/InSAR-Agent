"""一键完整报告(report/assemble.py + POST /api/report/full)的拼装纪律测试。

覆盖(任务验收清单):
  ① 完整拼装:标题 + 九章标题全部在场且按契约序出现;数据章取账本步骤参数;
  ② 模拟 run 首部强制显著警示(非模拟 run 全文无警示语);
  ③ 缺素材占位:run 未终态 → 结果占位;无图件 → 图件占位;无指标 → 指标表
     占位;空技能目录 → 参考文献占位(占位句为模块常量,逐字锁定);
  ④ 参考文献:run 涉及步骤的技能《参考文献》章按步序合并、逐字去重;
     场景不适用的技能不纳入;跨行条目归一为单行;
  ⑤ 落盘与幂等:report_full.md 原子落盘、响应与文件一致、重复调用覆写同一文件;
  ⑥ 无 LLM 确定性:home 无 llm.json 全链可拼;两次拼装除账本导出时间戳外
     逐字节一致;
  ⑦ 端点错误口径与既有 report 端点一致:无 run 404 / 跨会话 404 不泄露路径;
     run 非终态不 409(占位纪律,与 /api/repro-bundle 的 409 交付纪律有意不同);
  另:已落盘章节稿(report_draft.md/report_results.md)含本 run 标识才复用;
     已落盘图注复用、缺图注走确定性骨架;_browse 三档归并不重复列图。

密封纪律:INSAR_SKILLS_DIR 强制指向 tmp(autouse)—— 参考文献与账本里的
技能字段都不依赖仓库 skills/ 的真实内容;全部 run 数据手搭最小夹具。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from insar_agent.api.report_router import create_report_router
from insar_agent.core.db import Database
from insar_agent.core.store import Store
from insar_agent.report.assemble import (
    FULL_REPORT_FILENAME,
    PLACEHOLDER_FIGURES,
    PLACEHOLDER_METRICS,
    PLACEHOLDER_REFERENCES,
    PLACEHOLDER_RESULTS,
    SECTION_TITLES,
    SIMULATED_BANNER,
    assemble_full_report,
    reference_items,
)

RUN_ID = "20260814T000000-full01"
_HASHES = {"task_hash": "t1", "args_hash": "a1", "local_hash": "l1", "eval_hash": "e1"}

#: 拼装用的最小 PNG 字节(内容不被读取,存在性即可;magic 头保持体面)
_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


@pytest.fixture(autouse=True)
def skills_root(tmp_path, monkeypatch) -> Path:
    """密封技能目录:缺省为空(参考文献如实占位);测试按需写伪造 SKILL.md。

    autouse:assemble(merged_references)与 export_provenance(步骤技能字段)
    都会扫技能目录,不密封就会把仓库 skills/ 的真实文献混进断言。
    """
    root = tmp_path / "skills"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("INSAR_SKILLS_DIR", str(root))
    return root


def _write_skill(root: Path, dirname: str, capability: int, refs: list[str], *,
                 applies_to: str = "all") -> None:
    """写一份契约合法的最小 SKILL.md(五章齐全,只有《参考文献》有实义)。"""
    body = "\n".join(refs)
    (root / dirname).mkdir(parents=True, exist_ok=True)
    (root / dirname / "SKILL.md").write_text(
        f"""---
name: {dirname}
description: "测试技能(何时读:测试)"
capability: {capability}
version: "1.0.0"
applies_to: {applies_to}
---

# 测试技能

## 适用判据

占位。

## 参数启发式

占位。

## 常见失败与处置

占位。

## QA 依据

占位。

## 参考文献

{body}
""", encoding="utf-8")


def _build_env(base: Path, *, simulated: bool = True, status: str = "done",
               with_figure: bool = True, with_browse_tier: bool = False,
               with_caption: bool = False,
               with_metrics: bool = True) -> tuple[Store, Path, Path]:
    """手搭最小 run:5 个 done 步 + 可选图件(PNG+sidecar)+ 可选指标。"""
    home = base / "home"
    home.mkdir(parents=True, exist_ok=True)
    store = Store(Database(home / "insar.db"))
    store.create_session("sess-a", "sess-a")
    store.create_session("sess-b", "sess-b")
    ws = base / "sessions" / "sess-a"
    ws.mkdir(parents=True, exist_ok=True)
    store.create_run(RUN_ID, "sess-a", workspace=str(ws), simulated=simulated,
                     intent={"goal": "完整报告拼装验证"}, scenario="coseismic",
                     tool_versions={"mintpy": "1.5.1"})
    for sid, cap, name, method, params in (
            (1, "acquire", "数据获取", "local_import",
             {"platform": "Sentinel-1", "scenes": 12,
              "dates": "20190704-20190716", "source": "data/hyp3_products",
              "threads": 8}),
            (2, "aux", "辅助数据", "dem_copernicus",
             {"dem": "copernicus", "orbit": "poeorb"}),
            (5, "filter", "滤波", "goldstein", {"alpha": 0.6}),
            (9, "model", "形变模型", "linear", {"poly_order": 1}),
            (10, "figures", "出图导出", "figure_journal", {"dpi": 600, "cmap": "vik"}),
    ):
        store.upsert_step(RUN_ID, sid, capability=cap, name=name, method=method,
                          params=params, hashes=_HASHES)
        store.advance(RUN_ID, sid, "VERIFIED", state="done", run_ok=1,
                      qa=[], exit_code=0)
    if with_metrics:
        store.record_metric(RUN_ID, "crossval_r", value=0.92,
                            source_artifact="qa.json", source_field="r",
                            reparsed_ok=True)
        store.record_metric(RUN_ID, "unwrap_coverage", value=0.87,
                            source_artifact="qa.json", source_field="coverage",
                            reparsed_ok=True)
    if with_figure:
        figdir = ws / "products" / "figures"
        figdir.mkdir(parents=True, exist_ok=True)
        (figdir / "velocity.png").write_bytes(_PNG_BYTES)
        (figdir / "velocity.json").write_text(
            json.dumps({"title": "平均速度场", "units": "mm/yr", "step": 10}),
            encoding="utf-8")
        if with_browse_tier:
            (figdir / "velocity_browse.png").write_bytes(_PNG_BYTES)
        if with_caption:
            (figdir / "velocity.caption.json").write_text(
                json.dumps({"zh": "已存中文图注 CAPZH", "en": "saved caption CAPEN",
                            "llm_polish": True}, ensure_ascii=False),
                encoding="utf-8")
        store.record_artifact(RUN_ID, 10, "figs", path="products/figures",
                              kind="FIGURE", layout="dir", policy="content",
                              fp="content:sha256:cafe")
    store.set_run_status(RUN_ID, status)
    return store, home, ws


@pytest.fixture()
def env_factory(tmp_path):
    """按需建独立变体 run(一个测试可建多个);统一收尾 close,
    防 Windows 下 SQLite 句柄拖住 tmp 清理(test_results_draft 同款)。"""
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


def _client(store: Store, home: Path) -> TestClient:
    app = FastAPI()
    app.include_router(create_report_router(store, home))
    return TestClient(app)


# ---------------------------------------------------------------------------
# ① 完整拼装:章节齐全且有序;数据章取账本
# ---------------------------------------------------------------------------

def test_all_sections_present_and_ordered(env_factory):
    store, home, _ = env_factory()
    md = assemble_full_report(store, home, "sess-a", RUN_ID)
    assert md.startswith("# InSAR 处理完整报告:coseismic")
    indices = [md.index(f"## {title}") for title in SECTION_TITLES]
    assert indices == sorted(indices)  # 契约章节序:一个不缺、先后不乱
    # 元信息:run 标识 / 引擎 / 模拟标记
    assert RUN_ID in md and "mintpy 1.5.1" in md
    assert "模拟执行:**是(演示,不构成科学证据)**" in md


def test_data_section_from_ledger_params(env_factory):
    store, home, _ = env_factory()
    md = assemble_full_report(store, home, "sess-a", RUN_ID)
    assert "数据来源(source):data/hyp3_products" in md
    assert "影像平台:Sentinel-1" in md
    assert "影像景数:12" in md
    assert "时间范围:20190704-20190716" in md
    assert "轨道星历:poeorb" in md
    assert "研究意图:完整报告拼装验证" in md


def test_figures_inline_reference_and_skeleton_caption(env_factory):
    """图件章:相对路径引用 + 双语图注(未落盘图注时走确定性骨架)。"""
    store, home, _ = env_factory()
    md = assemble_full_report(store, home, "sess-a", RUN_ID)
    assert "### 图 1:velocity.png" in md
    assert "![velocity.png](products/figures/velocity.png)" in md
    assert "平均速度场" in md and "mm/yr" in md   # sidecar 事实进图注
    assert "图注来源:确定性骨架现拼" in md
    assert "Figure X:" in md                      # 英文骨架同批事实


# ---------------------------------------------------------------------------
# ② 模拟 run 首部警示
# ---------------------------------------------------------------------------

def test_simulated_banner_forced_at_top(env_factory):
    store, home, _ = env_factory(simulated=True)
    md = assemble_full_report(store, home, "sess-a", RUN_ID)
    assert SIMULATED_BANNER in md
    assert md.index(SIMULATED_BANNER) < md.index("## 元信息")  # 首部,先于一切章节


def test_no_simulated_wording_for_real_run(env_factory):
    store, home, _ = env_factory(simulated=False)
    md = assemble_full_report(store, home, "sess-a", RUN_ID)
    assert "模拟执行警示" not in md
    assert "不构成科学证据" not in md
    assert "模拟执行:否" in md


# ---------------------------------------------------------------------------
# ③ 缺素材占位(如实占位,绝不编)
# ---------------------------------------------------------------------------

def test_placeholder_results_when_run_not_terminal(env_factory):
    store, home, _ = env_factory(status="running")
    md = assemble_full_report(store, home, "sess-a", RUN_ID)
    assert PLACEHOLDER_RESULTS in md
    assert "当前状态 running" in md


def test_results_present_for_failed_run(env_factory):
    """failed 是终态:结果章如实拼(QA fail/缺数据由骨架诚实陈述),不占位。"""
    store, home, _ = env_factory(status="failed")
    md = assemble_full_report(store, home, "sess-a", RUN_ID)
    assert PLACEHOLDER_RESULTS not in md
    assert "确定性骨架现拼(report/results.py" in md


def test_placeholder_figures_and_metrics(env_factory):
    store, home, _ = env_factory(with_figure=False, with_metrics=False)
    md = assemble_full_report(store, home, "sess-a", RUN_ID)
    assert PLACEHOLDER_FIGURES in md
    assert PLACEHOLDER_METRICS in md


def test_placeholder_references_when_no_skills(env_factory):
    """autouse 密封技能目录为空 → 参考文献如实占位。"""
    store, home, _ = env_factory()
    md = assemble_full_report(store, home, "sess-a", RUN_ID)
    assert PLACEHOLDER_REFERENCES in md


# ---------------------------------------------------------------------------
# ④ 参考文献:按步序合并 + 逐字去重 + 场景过滤 + 跨行归一
# ---------------------------------------------------------------------------

_DUP_REF = ("- Berardino, P., et al. (2002). *IEEE TGRS*, 40(11), 2375-2383. "
            "doi:10.1109/TGRS.2002.803792")


def test_references_merged_and_deduped(env_factory, skills_root):
    _write_skill(skills_root, "05-filter-test", 5,
                 [_DUP_REF, "- Goldstein, R. M., & Werner, C. L. (1998). GRL 25."])
    _write_skill(skills_root, "09-model-test", 9,
                 [_DUP_REF,
                  "- Hetland, E. A., et al. (2012). MInTS analysis. *JGR*",
                  "  117:B02404. doi:10.1029/2011JB008731"])  # 跨行条目
    store, home, _ = env_factory()
    md = assemble_full_report(store, home, "sess-a", RUN_ID)
    dup_text = _DUP_REF[2:]
    assert md.count(dup_text) == 1                    # 两步同条:去重后只出现一次
    assert "Goldstein, R. M., & Werner, C. L. (1998)" in md
    # 跨行条目归一为单行(续行并入,空白折叠)
    assert ("Hetland, E. A., et al. (2012). MInTS analysis. *JGR* "
            "117:B02404. doi:10.1029/2011JB008731") in md
    # 步序:第 5 步的独有条目先于第 9 步的
    assert md.index("Goldstein, R. M.") < md.index("Hetland, E. A.")
    assert PLACEHOLDER_REFERENCES not in md


def test_references_scenario_filter(env_factory, skills_root):
    """applies_to 未命中当前场景(coseismic)的技能不纳入参考文献。"""
    _write_skill(skills_root, "05-filter-test", 5,
                 ['- 不该出现的文献(场景不适用)'], applies_to='["permafrost"]')
    store, home, _ = env_factory()
    md = assemble_full_report(store, home, "sess-a", RUN_ID)
    assert "不该出现的文献" not in md
    assert PLACEHOLDER_REFERENCES in md


def test_reference_items_pure_function():
    section = ("- 条目一,单行。\n"
               "- 条目二\n"
               "  续行 A\n"
               "  续行 B\n"
               "\n"
               "- 条目三\n")
    assert reference_items(section) == [
        "条目一,单行。", "条目二 续行 A 续行 B", "条目三"]
    assert reference_items("") == []
    assert reference_items("散文本不算条目") == []


# ---------------------------------------------------------------------------
# ⑤ 落盘与幂等 + 响应契约
# ---------------------------------------------------------------------------

def test_api_full_saves_and_is_idempotent(env_factory):
    store, home, ws = env_factory()
    with _client(store, home) as client:
        r1 = client.post("/api/report/full", json={"session": "sess-a"})
        assert r1.status_code == 200
        data = r1.json()
        assert set(data) == {"ok", "run_id", "path", "markdown", "saved"}
        assert data["ok"] is True and data["run_id"] == RUN_ID
        assert data["path"] == FULL_REPORT_FILENAME and data["saved"] is True
        on_disk = (ws / FULL_REPORT_FILENAME).read_text(encoding="utf-8")
        assert on_disk == data["markdown"]            # 响应与落盘一字不差

        r2 = client.post("/api/report/full",
                         json={"session": "sess-a", "run_id": RUN_ID})
        assert r2.status_code == 200
        data2 = r2.json()
        assert data2["path"] == FULL_REPORT_FILENAME  # 幂等:同名覆写,不长副本
        on_disk2 = (ws / FULL_REPORT_FILENAME).read_text(encoding="utf-8")
        assert on_disk2 == data2["markdown"]
        # 章节结构逐次一致(时间戳之外整体等价,见确定性测试)
        assert [f"## {t}" in data2["markdown"] for t in SECTION_TITLES] \
            == [True] * len(SECTION_TITLES)


# ---------------------------------------------------------------------------
# ⑥ 无 LLM 确定性路径
# ---------------------------------------------------------------------------

_TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


def test_deterministic_without_llm(env_factory):
    """home 无 llm.json(assemble 本就不触 LLM):两次拼装除账本导出时间戳外
    逐字节一致 —— 全链确定性的机器判定。"""
    store, home, _ = env_factory()
    a = assemble_full_report(store, home, "sess-a", RUN_ID)
    b = assemble_full_report(store, home, "sess-a", RUN_ID)
    assert _TS_RE.sub("<TS>", a) == _TS_RE.sub("<TS>", b)


# ---------------------------------------------------------------------------
# ⑦ 端点错误口径(与既有 report 端点一致)
# ---------------------------------------------------------------------------

def test_api_no_run_404(env_factory):
    store, home, _ = env_factory()
    with _client(store, home) as client:
        r = client.post("/api/report/full", json={"session": "sess-b"})
    assert r.status_code == 404


def test_api_cross_session_404_no_leak(env_factory, tmp_path):
    """sess-b 借 run_id 拼 sess-a 的报告:按「不存在」处理,不泄露磁盘路径。"""
    store, home, _ = env_factory()
    with _client(store, home) as client:
        r = client.post("/api/report/full",
                        json={"session": "sess-b", "run_id": RUN_ID})
    assert r.status_code == 404
    leak = str(tmp_path).replace("\\", "/")
    assert leak not in r.text.replace("\\\\", "/").replace("\\", "/")


def test_api_non_terminal_run_is_200_not_409(env_factory):
    """中途态 run 走占位纪律(200 + 结果占位),不学 /api/repro-bundle 的 409
    —— 完整报告的价值恰是「现在就能看到已有素材 + 缺口如实标注」。"""
    store, home, _ = env_factory(status="running")
    with _client(store, home) as client:
        r = client.post("/api/report/full", json={"session": "sess-a"})
    assert r.status_code == 200
    assert PLACEHOLDER_RESULTS in r.json()["markdown"]


# ---------------------------------------------------------------------------
# 已落盘素材的复用纪律(含本 run 标识才认)
# ---------------------------------------------------------------------------

def test_reuses_saved_methods_draft_owned_by_run(env_factory):
    store, home, ws = env_factory()
    (ws / "report_draft.md").write_text(
        f"润色过的方法章节 METHODS-MARKER(运行标识 {RUN_ID})", encoding="utf-8")
    md = assemble_full_report(store, home, "sess-a", RUN_ID)
    assert "METHODS-MARKER" in md
    assert "复用已落盘方法章节草稿" in md


def test_ignores_saved_draft_without_run_id(env_factory):
    """工作区按会话共享:不含本 run 标识的旧稿(fork/前 run 遗留)不复用。"""
    store, home, ws = env_factory()
    (ws / "report_draft.md").write_text("旧 run 的稿子 STALE-MARKER", encoding="utf-8")
    (ws / "report_results.md").write_text("旧 run 结果 STALE-R-MARKER", encoding="utf-8")
    md = assemble_full_report(store, home, "sess-a", RUN_ID)
    assert "STALE-MARKER" not in md and "STALE-R-MARKER" not in md
    assert "确定性骨架现拼(report/draft.py" in md
    assert "确定性骨架现拼(report/results.py" in md


def test_reuses_saved_caption_and_merges_tiers(env_factory):
    store, home, _ = env_factory(with_caption=True, with_browse_tier=True)
    md = assemble_full_report(store, home, "sess-a", RUN_ID)
    assert "CAPZH" in md and "CAPEN" in md
    assert "已落盘图注(caption.json,LLM 已润色)" in md
    assert "velocity_browse.png" not in md   # 三档归并:浏览档不单独成图
    assert md.count("### 图 ") == 1
