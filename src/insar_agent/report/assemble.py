"""一键完整报告:把散落的报告素材拼成一份完整 Markdown(report_full.md)。

素材全部现成,本模块只拼不算(0814B 契约 §3):

  标题+元信息 → 摘要 → 数据 → 方法(复用 draft)→ 结果(复用 results)
  → 图件(figures+captions)→ 质检与证据级别(qa 指标表+阶梯结论)
  → 复现附录(run.sh 引用+provenance 引用块)→ 参考文献(技能《参考文献》章)

纪律(硬约束):
  1. 全链确定性:本模块不触 LLM、不触网 —— LLM 不可用时照常可拼。
     已落盘的章节稿(report_draft.md / report_results.md,可能含 LLM 润色、
     数值已经双向校验)属「现成素材」:正文含本 run 标识才复用并如实标注来源,
     否则回退确定性骨架现拼(工作区按会话共享,fork run 的旧稿不得错认);
  2. 缺素材如实占位绝不编:占位句是模块常量(PLACEHOLDER_*,测试锁死),
     绝不用推算内容充数;
  3. 模拟 run 全文首部强制显著警示(draft.SIMULATED_SENTENCE 同律);
  4. 图件枚举与 GET /api/figures 同口径(产物图像 + 目录成员 + _browse/_thumb
     三档归并 + 越界防御);图注优先复用已落盘 <name>.caption.json,缺失时按
     captions 双语骨架现拼;
  5. 参考文献 = run 涉及步骤(账本 steps)的技能文档《参考文献》章按步序合并,
     条目级精确去重(空白归一后逐字比对;措辞不同的同一文献不强行合并 ——
     拼装器不替内容代理做学术判断)。

模块边界:只读 store / run 工作区 / 技能目录,绝不写文件(落盘归 API 层);
不 import api 层(sidecar 读取按 api.app.read_sidecar_meta 同口径复刻,
captions.py 同律)。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from insar_agent.audit.contract import Threshold, load_contract
from insar_agent.core.ledger import export_provenance
from insar_agent.core.store import Store
from insar_agent.report.captions import (
    _IMAGE_EXTS,
    _TIER_SUFFIXES,
    build_caption_facts,
    caption_skeleton,
    load_caption,
)
from insar_agent.report.draft import MISSING, _fmt, build_facts, skeleton_text
from insar_agent.report.results import (
    RESULTS_FILENAME,
    build_result_facts,
    results_skeleton,
)
from insar_agent.skills.loader import load_skills

#: 完整报告落盘文件名(run 工作目录下;与 report_draft.md / report_results.md 并列)
FULL_REPORT_FILENAME = "report_full.md"

#: 方法章节草稿的落盘文件名 —— 与 api/report_router.DRAFT_FILENAME 同口径
#: (报告层不反向 import API 层,captions._IMAGE_EXTS 同律;守护测试锁两处一致)
METHODS_DRAFT_FILENAME = "report_draft.md"

#: 章节标题闭集(拼装顺序即列表顺序;测试按此锁定「全部章节在场且有序」)
SECTION_TITLES = ("元信息", "摘要", "数据", "方法", "结果", "图件",
                  "质检与证据级别", "复现附录", "参考文献")

#: 模拟 run 的全文首部强制警示(显著:引用块 + 加粗;含 draft 同款关键短语)
SIMULATED_BANNER = ("> ⚠ **模拟执行警示**:本次运行为模拟执行(引擎缺失环境下的演示),"
                    "全文数值仅验证处理链结构与账本贯通性,不构成科学证据。")

# ---- 缺素材占位句(如实说明,绝不编内容;测试逐字锁定) ----
PLACEHOLDER_RESULTS = "结果章节:run 未完成,不可用"
PLACEHOLDER_FIGURES = "图件章节:该 run 无可引用的图件产物,不可用"
PLACEHOLDER_METRICS = "质量指标表:run 未入账任何质量指标,不可用"
PLACEHOLDER_REFERENCES = "参考文献:当前场景涉及步骤的技能文档均无《参考文献》章可引"

#: 已落盘章节稿的复用上限(64KB:正常章节稿千字级,超限视为坏文件不复用)
_CHAPTER_MAX_BYTES = 64 * 1024

#: 图件 sidecar 读取上限(api.app._SIDECAR_MAX_BYTES 同口径)
_SIDECAR_MAX_BYTES = 64 * 1024

#: 目录型产物的图件成员上限(GET /api/figures 的 listed 上限同款)
_FIGURES_PER_DIR = 100


def _int_or_zero(k) -> int:
    try:
        return int(k)
    except (TypeError, ValueError):
        return 0


def _utc(epoch) -> str:
    """epoch 秒 → UTC ISO 串;不可解析如实「未记录」。"""
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(epoch)))
    except (TypeError, ValueError, OSError, OverflowError):
        return MISSING


# ---------------------------------------------------------------------------
# 已落盘章节稿的复用(含本 run 标识才认;fork 共享工作区的旧稿不得错认)
# ---------------------------------------------------------------------------

def load_owned_chapter(workspace: Path | None, filename: str, run_id: str) -> str | None:
    """读工作区已落盘章节稿;仅当正文含本 run 标识时返回(否则 None → 骨架现拼)。

    容忍一切失败(缺文件/超限/不可读)—— 复用是增强,骨架是保底。
    run_id 缺失(MISSING)时不做归属判定,直接不复用。
    """
    if workspace is None or not run_id or run_id == MISSING:
        return None
    path = workspace / filename
    try:
        if not path.is_file() or path.stat().st_size > _CHAPTER_MAX_BYTES:
            return None
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return text if run_id in text else None


# ---------------------------------------------------------------------------
# 图件枚举(GET /api/figures 同口径)与图注取用
# ---------------------------------------------------------------------------

def _read_sidecar(image: Path) -> dict | None:
    """图件同名 .json sidecar —— api.app.read_sidecar_meta 同口径复刻
    (报告层不反向 import API 层):缺文件/超限/坏 JSON/顶层非对象一律 None。"""
    sidecar = image.with_suffix(".json")
    try:
        if not sidecar.is_file() or sidecar.stat().st_size > _SIDECAR_MAX_BYTES:
            return None
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def list_figures(run: dict, artifacts: list[dict]) -> list[dict]:
    """该 run 全部图像类产物 → [{path(绝对), rel(工作区相对 posix), art}]。

    枚举口径与 GET /api/figures 逐条对齐(captions.locate_figure 的枚举版):
    产物相对路径必须落在 run 工作区内;单文件图像产物直取;目录型产物枚举
    图像成员(resolve 后仍须落在产物目录内,防外指 symlink);基图在场的
    _browse/_thumb 三档归并不单独成条;窗口内消失的文件逐行跳过。
    """
    if not run.get("workspace"):
        return []
    base = Path(run["workspace"]).resolve()
    out: list[dict] = []
    for art in sorted(artifacts, key=lambda a: (a["step_id"], a["art_id"])):
        rel = Path(art["path"])
        if rel.is_absolute() or rel.drive:
            continue
        try:
            target = (base / rel).resolve()
            if target == base or not target.is_relative_to(base):
                continue
            if rel.suffix.lower() in _IMAGE_EXTS:
                if target.is_file():
                    out.append({"path": target, "rel": rel.as_posix(), "art": art})
                continue
            if not target.is_dir():
                continue
            children = sorted(p for p in target.iterdir()
                              if p.is_file() and p.suffix.lower() in _IMAGE_EXTS
                              and p.resolve().is_relative_to(target))
        except OSError:
            continue  # 枚举窗口内文件/目录被清理:该产物行跳过,拼装不炸
        stems = {p.stem for p in children}
        listed = 0
        for child in children:
            stem = child.stem
            if any(stem.endswith(sfx) and stem[:-len(sfx)] in stems
                   for sfx in _TIER_SUFFIXES):
                continue  # 三档归并:基图在场,浏览/缩略档不单独立目
            out.append({"path": child, "rel": f"{rel.as_posix()}/{child.name}",
                        "art": art})
            listed += 1
            if listed >= _FIGURES_PER_DIR:
                break
    return out


def _caption_of(figure: Path, art: dict, doc: dict) -> tuple[str, str, str]:
    """单图图注 → (中文, 英文, 来源标注)。

    已落盘 <name>.caption.json 优先(可能经 LLM 润色,当时已过三重校验);
    缺失时按 captions 双语骨架现拼(确定性,不触 LLM)。
    """
    saved = load_caption(figure)
    if saved is not None:
        src = ("已落盘图注(caption.json,LLM 已润色)" if saved.get("llm_polish")
               else "已落盘图注(caption.json,确定性骨架)")
        return saved["zh"], saved["en"], src
    facts = build_caption_facts(_read_sidecar(figure), doc, step=art["step_id"])
    return (caption_skeleton(facts, "zh"), caption_skeleton(facts, "en"),
            "确定性骨架现拼(尚未生成图注文件)")


# ---------------------------------------------------------------------------
# 参考文献:技能《参考文献》章按步序合并 + 条目级去重
# ---------------------------------------------------------------------------

def reference_items(section: str) -> list[str]:
    """《参考文献》章正文 → 条目列表(每条归一为单行,空白折叠)。

    条目 = 「- 」起头的列表项;缩进续行并入当前条目;空行结束当前条目。
    列表项之外的散文本(理论上不存在)不算参考文献条目 —— 如实丢弃。
    """
    items: list[str] = []
    cur: list[str] | None = None

    def flush() -> None:
        nonlocal cur
        if cur:
            joined = " ".join(" ".join(cur).split())
            if joined:
                items.append(joined)
        cur = None

    for line in (section or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            flush()
            cur = [stripped[2:]]
        elif stripped and cur is not None:
            cur.append(stripped)
        elif not stripped:
            flush()
    flush()
    return items


def merged_references(doc: dict) -> list[str]:
    """run 涉及步骤(账本 steps)的技能《参考文献》章 → 按步序合并去重的条目表。

    技能须对当前场景适用(applies_to 命中,loader.applies 口径);无技能/
    无该章/场景不适用的步骤自然跳过。去重口径:空白归一后逐字相同才算重复
    —— 措辞不同的同一文献不强行合并(拼装器不做学术判断)。
    """
    skills = load_skills()
    scenario = doc.get("scenario") or None
    seen: set[str] = set()
    out: list[str] = []
    for sid in sorted(doc.get("steps") or {}, key=_int_or_zero):
        skill = skills.get(_int_or_zero(sid))
        if skill is None or not skill.applies(scenario):
            continue
        for item in reference_items(skill.sections.get("参考文献", "")):
            if item not in seen:
                seen.add(item)
                out.append(item)
    return out


# ---------------------------------------------------------------------------
# 各章节拼装(全部确定性;缺素材走 PLACEHOLDER_* 占位)
# ---------------------------------------------------------------------------

def _meta_lines(run: dict, doc: dict, session_id: str) -> list[str]:
    env = doc.get("environment") or {}
    tools = env.get("tools") or {}
    engine = ("、".join(f"{k} {v}" for k, v in sorted(tools.items()))
              if tools else MISSING)
    simulated = bool(doc.get("simulated"))
    return [
        "## 元信息", "",
        f"- run:`{doc.get('run_id') or MISSING}`",
        f"- 会话:{session_id}",
        f"- run 状态:{run.get('status') or MISSING}",
        f"- 创建时间(UTC):{_utc(run.get('created_at'))}",
        f"- 账本导出时间(UTC):{doc.get('generated_at_utc') or MISSING}",
        f"- 场景:{doc.get('scenario') or MISSING}",
        f"- 引擎/工具链:{engine}",
        "- 模拟执行:" + ("**是(演示,不构成科学证据)**" if simulated else "否"),
    ]


def _abstract_lines(doc: dict, run: dict, result_facts: dict) -> list[str]:
    """确定性摘要:从结果章节事实与账本提炼,无 LLM 也能拼(数字全部可反查)。"""
    steps = doc.get("steps") or {}
    by_state: dict[str, int] = {}
    for s in steps.values():
        st = (s or {}).get("state") or "unknown"
        by_state[st] = by_state.get(st, 0) + 1
    state_txt = ("、".join(f"{k} {v} 步" for k, v in sorted(by_state.items()))
                 if by_state else "无步骤记录")
    lines = ["## 摘要", "",
             f"本报告汇总 InSAR-Agent run `{doc.get('run_id') or MISSING}`"
             f"(场景 {doc.get('scenario') or MISSING},状态 {run.get('status') or MISSING})"
             f"的处理与质检记录:处理链共 {len(steps)} 步({state_txt});"
             f"QA 总判定 {result_facts['qa_status']},证据级别 "
             f"{result_facts['evidence_level']}(六级证据阶梯)。"]
    v = result_facts.get("velocity") or {}
    if v.get("available") and v.get("min_mmyr") is not None:
        lines.append(f"速度场({v['path']})LOS 形变速率介于 {_fmt(v['min_mmyr'])} 至 "
                     f"{_fmt(v['max_mmyr'])} mm/yr,均值 {_fmt(v['mean_mmyr'])} mm/yr,"
                     f"有效像元占比 {_fmt(v['valid_pixel_pct'])}%。")
    else:
        lines.append(f"速度场统计:{MISSING}"
                     f"({v.get('reason') or 'run 未产出速度场'})。")
    if doc.get("simulated"):
        lines.append("本次为模拟执行,以上数值不构成科学证据。")
    return lines


def _data_lines(doc: dict) -> list[str]:
    """数据章:数据集/时间范围/轨道等,只认账本步骤参数里的既有键,缺就是缺。"""
    facts = build_facts(doc)
    scale = facts["data_scale"]
    source = MISSING
    orbit = MISSING
    for s in facts["steps"]:
        p = s["params"]
        if source == MISSING and p.get("source"):
            source = _fmt(p["source"])
        if orbit == MISSING and p.get("orbit") is not None:
            orbit = _fmt(p["orbit"])
    return [
        "## 数据", "",
        f"- 研究意图:{facts['intent']}",
        f"- 数据来源(source):{source}",
        f"- 影像平台:{_fmt(scale['platform'])}",
        f"- 影像景数:{_fmt(scale['scenes'])}",
        f"- 时间范围:{_fmt(scale['dates'])}",
        f"- 轨道星历:{orbit}",
        f"- 干涉对数:{_fmt(scale['pairs'])}",
        f"- 入账产物:{scale['artifact_count']} 项",
        "",
        "> 以上均取自 provenance 账本步骤参数,缺项如实标「未记录」,不作推算。",
    ]


def _methods_lines(doc: dict, workspace: Path | None) -> list[str]:
    reused = load_owned_chapter(workspace, METHODS_DRAFT_FILENAME,
                                doc.get("run_id") or "")
    if reused is not None:
        note = ("> 本章复用已落盘方法章节草稿(report_draft.md,归属本 run;"
                "若曾经 LLM 润色,数值已过双向校验)。")
        return ["## 方法", "", note, "", reused.strip()]
    return ["## 方法", "",
            "> 本章为确定性骨架现拼(report/draft.py,无 LLM 参与,"
            "数字均可在账本反查)。", "",
            skeleton_text(build_facts(doc))]


#: run 终态闭集:结果章节只对终态 run 拼装(账本已定格);其余状态如实占位
_TERMINAL_STATUSES = frozenset({"done", "failed"})


def _results_lines(run: dict, doc: dict, result_facts: dict) -> list[str]:
    status = run.get("status") or MISSING
    if status not in _TERMINAL_STATUSES:
        return ["## 结果", "",
                f"{PLACEHOLDER_RESULTS}(当前状态 {status},账本仍在演化,"
                "待 run 终态后重新生成)。"]
    workspace = Path(run["workspace"]) if run.get("workspace") else None
    reused = load_owned_chapter(workspace, RESULTS_FILENAME, doc.get("run_id") or "")
    if reused is not None:
        note = ("> 本章复用已落盘结果章节草稿(report_results.md,归属本 run;"
                "若曾经 LLM 润色,数值已过双向校验)。")
        return ["## 结果", "", note, "", reused.strip()]
    return ["## 结果", "",
            "> 本章为确定性骨架现拼(report/results.py,无 LLM 参与)。", "",
            results_skeleton(result_facts)]


def _figure_lines(run: dict, artifacts: list[dict], doc: dict) -> list[str]:
    figures = list_figures(run, artifacts)
    if not figures:
        return ["## 图件", "",
                f"{PLACEHOLDER_FIGURES}(产物账本无图像条目或文件已缺失)。"]
    lines = ["## 图件", ""]
    for i, fig in enumerate(figures, 1):
        zh, en, src = _caption_of(fig["path"], fig["art"], doc)
        lines += [
            f"### 图 {i}:{fig['path'].name}(第 {fig['art']['step_id']} 步产物 "
            f"`{fig['art']['art_id']}`)", "",
            f"![{fig['path'].name}]({fig['rel']})", "",
            f"- 图注(中):{zh}",
            f"- Caption(EN):{en}",
            f"- 图注来源:{src}",
            "",
        ]
    lines.append("> 图片以工作区相对路径引用:报告与图件同处 run 工作区时可直接渲染;"
                 "_browse/_thumb 浏览档已归并进基图,不重复列出。")
    return lines


def _qa_lines(doc: dict) -> list[str]:
    lines = ["## 质检与证据级别", "",
             f"- QA 总判定:**{(doc.get('qa') or {}).get('status') or MISSING}**"
             "(账本口径:全部步骤 run_ok 通过或云端跳过为 pass,否则 fail)", ""]
    metrics = doc.get("metrics") or {}
    if metrics:
        lines += ["| 指标 | 值 | 单位 | 来源 | 重解析 |", "|---|---|---|---|---|"]
        for name in sorted(metrics):
            m = metrics[name] or {}
            value = _fmt(m.get("value")) if m.get("value") is not None else MISSING
            unit = m.get("unit") or "—"
            src = f"`{m.get('source_artifact')}#{m.get('source_field')}`"
            rp = {True: "✓", False: "✗", None: "—"}.get(m.get("reparsed_ok"), "—")
            lines.append(f"| {name} | {value} | {unit} | {src} | {rp} |")
        lines += ["", "> 重解析 ✓ = 数字已从产物文件二次解析核对,不是日志转述。"]
    else:
        lines.append(f"{PLACEHOLDER_METRICS}(账本 metrics 为空,如实说明)。")
    ev = doc.get("evidence") or {}
    level = ev.get("level") or doc.get("evidence_level") or MISSING
    ladder = [str(x) for x in (ev.get("ladder") or [])]
    lines += ["", f"- 证据级别:**{level}**"
              + (f"(阶梯:{' → '.join(ladder)})" if ladder else "")]
    lines.extend(f"- 停级原因:{r}" for r in (ev.get("reasons") or []))
    if ev.get("ceiling"):
        lines.append(f"- 封顶:{ev['ceiling']}({ev.get('ceiling_reason') or MISSING})")
    return lines


def _repro_lines(run: dict, doc: dict) -> list[str]:
    workspace = Path(run["workspace"]) if run.get("workspace") else None
    has_script = bool(workspace and (workspace / "run.sh").is_file())
    script_note = ("工作区已落盘 `run.sh`" if has_script
                   else "`run.sh` 尚未落盘,可经 `GET /api/run.sh` 一键导出")
    arts = doc.get("artifacts") or {}
    with_fp = sum(1 for a in arts.values() if (a or {}).get("fp"))
    interventions = doc.get("interventions")
    n_iv = len(interventions) if isinstance(interventions, list) else 0
    repo = doc.get("repo") or {}
    agent = doc.get("agent") or {}
    return [
        "## 复现附录", "",
        f"- 一键复现:{script_note} —— 按序拼接真实执行过的每步 cmd.sh"
        "(是执行过的那一份,不是重新生成的近似品);",
        "- 完整账本:`provenance.json`(`GET /api/provenance`),或经 "
        "`GET /api/repro-bundle` 打包 provenance / run.sh / methods / qa / 图件;",
        "",
        "> provenance 引用块(账本身份指纹):",
        f"> run_id:`{doc.get('run_id') or MISSING}`;"
        f"账本导出时间 {doc.get('generated_at_utc') or MISSING} UTC",
        f"> git_head:`{repo.get('git_head') or MISSING}`;"
        f"agent_hash:`{agent.get('agent_hash') or MISSING}`",
        f"> 步骤 {len(doc.get('steps') or {})} 项;产物 {len(arts)} 项"
        f"(含指纹 {with_fp} 项);指标 {len(doc.get('metrics') or {})} 项;"
        f"人工干预 {n_iv} 次",
    ]


def _reference_lines(doc: dict) -> list[str]:
    refs = merged_references(doc)
    if not refs:
        return ["## 参考文献", "",
                f"{PLACEHOLDER_REFERENCES}(技能目录无对应步骤条目,如实说明)。"]
    lines = ["## 参考文献", ""]
    lines.extend(f"- {r}" for r in refs)
    lines += ["", "> 条目来自当前 run 涉及步骤的技能文档《参考文献》章,"
              "按步骤序合并、逐字去重;措辞不同的同一文献不强行合并。"]
    return lines


# ---------------------------------------------------------------------------
# 总装
# ---------------------------------------------------------------------------

def assemble_full_report(store: Store, home: Path, session_id: str, run_id: str,
                         *, contract: dict[str, Threshold] | None = None) -> str:
    """run 的完整报告 → Markdown 全文(确定性;缺素材如实占位绝不编)。

    run 不存在抛 KeyError(export_provenance 同口径;归属校验归 API 层)。
    home 为波次契约固定签名的注入位(与 router 工厂同形),当前拼装不消费
    —— 素材全部来自 store / run 工作区 / 技能目录。
    """
    _ = home  # 契约签名保留位(见 docstring)
    run = store.get_run(run_id)
    if run is None:
        raise KeyError(run_id)
    contract = contract if contract is not None else load_contract()
    workspace = Path(run["workspace"]) if run.get("workspace") else None
    doc = export_provenance(store, run_id, contract=contract, workspace=workspace)
    artifacts = store.artifacts_of(run_id)

    blocks: list[list[str]] = []
    title = [f"# InSAR 处理完整报告:{doc.get('scenario') or MISSING}"
             f"(run `{run_id}`)"]
    if doc.get("simulated"):
        title += ["", SIMULATED_BANNER]
    blocks.append(title)
    blocks.append(_meta_lines(run, doc, session_id))
    result_facts = build_result_facts(store, run_id, contract=contract)
    blocks.append(_abstract_lines(doc, run, result_facts))
    blocks.append(_data_lines(doc))
    blocks.append(_methods_lines(doc, workspace))
    blocks.append(_results_lines(run, doc, result_facts))
    blocks.append(_figure_lines(run, artifacts, doc))
    blocks.append(_qa_lines(doc))
    blocks.append(_repro_lines(run, doc))
    blocks.append(_reference_lines(doc))
    return "\n\n".join("\n".join(b) for b in blocks) + "\n"
