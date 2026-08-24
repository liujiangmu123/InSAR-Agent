"""论文结果章节草稿:QA 指标 + 速度场统计 → 事实闭集 → 骨架段落 →(可选)LLM 润色。

与 report/draft.py(方法章节)配套,同一防幻觉纪律(校验函数直接 import 复用,
不复制):

  1. 草稿中的每个数字只允许来自 build_result_facts 抽出的事实闭集:
     账本 QA 指标(判定引用 contract.yaml 台账阈值口径)、速度场统计
     (velocity h5 真实存在且 h5py 可用才产出,否则整组「未记录」+ 如实原因)、
     形变模型参数(第 9 步)。缺字段显式「未记录」,绝不编造;
  2. 两段式防幻觉:骨架由纯代码拼接(确定性,单独可用);LLM 只润色措辞,
     润色稿必须通过 draft._polish_valid 三重校验(数值双向一致 / 方法名保全 /
     模拟警示句保全),任一不过 → 回退骨架(llm_polish=False);
  3. 模拟 run 强制附「不构成科学证据」句;
  4. 速度场单位口径与 engines/qa.py 一致:velocity 数据集为 m/yr,×1000 报 mm/yr。

纯函数边界:results_skeleton / results_facts_used 不碰网络与文件系统;
build_result_facts 只读 store 与 run 工作区产物(秒级统计,不是计算任务);
draft_results 只通过传入的 provider 触网。
"""

from __future__ import annotations

from pathlib import Path

from insar_agent.audit.contract import Threshold, load_contract
from insar_agent.brain.provider import BrainUnavailable, LLMProvider
from insar_agent.core.ledger import export_provenance
from insar_agent.core.store import Store
from insar_agent.report.bundle import _resolve_inside
from insar_agent.report.draft import (
    _POLISH_INPUT_MAX,
    MISSING,
    SIMULATED_SENTENCE,
    _clean_params,
    _fmt,
    _params_phrase,
    _polish_valid,
)

#: 结果章节落盘文件名(run 工作目录下;与方法章节 report_draft.md 并列)
RESULTS_FILENAME = "report_results.md"

#: 指标 → 台账阈值键(判定口径唯一来源:registry/capabilities 的 metric_min 声明
#: —— crossval_r 引 corr_threshold、unwrap_coverage 引同名键;其余指标无台账
#: 判据,只陈述数值不下结论)
_METRIC_THRESHOLD_KEY = {"crossval_r": "corr_threshold",
                         "unwrap_coverage": "unwrap_coverage"}

#: 指标中文标签(纯文案映射,不含数字;未知指标原名直排)
_METRIC_LABEL = {
    "crossval_r": "PS/SBAS 交叉验证相关系数",
    "crossval_rmse_mm": "双链重叠区速度差 RMSE",
    "gnss_rmse_mm": "InSAR−GNSS 时序 RMSE",
    "unwrap_coverage": "解缠有效覆盖率",
    "velocity_coverage": "速度场有效像元占比",
    "nan_fraction": "NaN 占比",
    "mean_coherence": "平均空间相干性",
    "residual_rms_mm": "时序残差 RMS",
    "vel_p2": "速度 2% 分位",
    "vel_p98": "速度 98% 分位",
}

#: velocity h5 的约定候选路径(registry 第 9 步 ArtifactSpec / engines/qa.py 同序)
_VELOCITY_CANDIDATES = ("mintpy/velocity.h5", "products/velocity.h5", "velocity.h5")


# ---------------------------------------------------------------------------
# ① 事实闭集抽取(账本指标 + 产物统计;缺失显式「未记录」)
# ---------------------------------------------------------------------------

def _verdict(value, threshold) -> str | None:
    """台账 metric_min 语义(audit/runok.py):值 ≥ 阈值为达标;不可比较如实 None。"""
    try:
        return "达到" if float(value) >= float(threshold) else "未达到"
    except (TypeError, ValueError):
        return None


def _find_velocity_file(doc: dict, workspace: Path | None) -> tuple[Path, str] | None:
    """定位 velocity h5:账本 VELOCITY 产物优先(路径越界防御同 bundle.py),
    再按注册表候选路径兜底;找不到返回 None(→ 统计组「未记录」)。"""
    if workspace is None:
        return None
    arts = doc.get("artifacts") or {}
    for aid in sorted(arts):
        a = arts[aid] or {}
        if a.get("kind") != "VELOCITY":
            continue
        rel = str(a.get("path") or "")
        target = _resolve_inside(workspace, rel)
        if target is not None and target.is_file():
            return target, rel
    for rel in _VELOCITY_CANDIDATES:
        target = workspace / rel
        if target.is_file():
            return target, rel
    return None


def _velocity_stats(found: tuple[Path, str] | None) -> dict:
    """velocity h5 → 统计组(min/max/mean/p2/p98 mm/yr + 有效像元占比)。

    文件缺失 / h5py 缺失 / 无法解析 → available=False + 如实 reason,绝不编数。
    单位口径:数据集存 m/yr(MintPy 惯例),×1000 报 mm/yr(engines/qa.py 同款)。
    """
    if found is None:
        return {"available": False, "reason": "velocity h5 不存在(或工作区未记录)"}
    try:
        import h5py
        import numpy as np
    except ImportError:
        return {"available": False, "reason": "h5py/numpy 不可用(未安装 [raster] 依赖组)"}
    path, rel = found
    try:
        with h5py.File(path, "r") as f:
            if "velocity" not in f:
                return {"available": False, "reason": "velocity 数据集缺失"}
            vel = np.asarray(f["velocity"][()], dtype="float64") * 1000.0
    except OSError:
        return {"available": False, "reason": "velocity h5 无法解析"}
    total = int(vel.size)
    if total == 0:
        return {"available": False, "reason": "velocity 数据集为空"}
    finite = np.isfinite(vel)
    vals = vel[finite]
    out = {"available": True, "path": rel,
           "valid_pixel_pct": round(float(finite.sum()) / total * 100.0, 2),
           "min_mmyr": None, "max_mmyr": None, "mean_mmyr": None,
           "p2_mmyr": None, "p98_mmyr": None}
    if vals.size:  # 全 NaN 时统计量保持 None(骨架如实写「未记录」)
        out.update(
            min_mmyr=round(float(vals.min()), 2),
            max_mmyr=round(float(vals.max()), 2),
            mean_mmyr=round(float(vals.mean()), 2),
            p2_mmyr=round(float(np.percentile(vals, 2)), 2),
            p98_mmyr=round(float(np.percentile(vals, 98)), 2))
    return out


def build_result_facts(store: Store, run_id: str, *,
                       contract: dict[str, Threshold] | None = None) -> dict:
    """账本 + 产物 → 结果章节事实闭集(草稿允许使用的全部事实,仅此一处产生)。

    覆盖:QA 指标与台账判定 / 速度场统计 / 形变模型参数(第 9 步)/
    QA 总判定 / 证据级 / 模拟标记。run 不存在抛 KeyError(export_provenance 同款)。
    """
    contract = contract or load_contract()
    run = store.get_run(run_id)
    if run is None:
        raise KeyError(run_id)
    workspace = Path(run["workspace"]) if run["workspace"] else None
    doc = export_provenance(store, run_id, contract=contract, workspace=workspace)

    thresholds = doc.get("thresholds") or {}
    metrics: list[dict] = []
    for name in sorted(doc.get("metrics") or {}):
        m = doc["metrics"][name] or {}
        entry = {"name": name, "value": m.get("value"), "unit": m.get("unit") or "",
                 "threshold_key": None, "threshold_value": None,
                 "threshold_status": None, "verdict": None}
        tkey = _METRIC_THRESHOLD_KEY.get(name)
        th = thresholds.get(tkey) if tkey else None
        if th and th.get("value") is not None and entry["value"] is not None:
            entry.update(threshold_key=tkey, threshold_value=th["value"],
                         threshold_status=th.get("status") or MISSING,
                         verdict=_verdict(entry["value"], th["value"]))
        metrics.append(entry)

    step9 = (doc.get("steps") or {}).get("9")
    model = None
    if step9:
        model = {"step_id": 9, "name": step9.get("name") or MISSING,
                 "method": step9.get("method") or MISSING,
                 "state": step9.get("state") or MISSING,
                 "params": _clean_params(step9.get("params"))}

    from insar_agent.report.product_level import annotate
    art_view = dict(doc.get("artifacts") or {})
    if workspace is not None:
        dec = workspace / "analysis" / "decomposed.h5"
        if dec.is_file():
            art_view.setdefault("decomposed", {"path": "analysis/decomposed.h5"})
        vel_found = _find_velocity_file(doc, workspace)
        if vel_found is not None:
            try:
                import h5py
                with h5py.File(vel_found[0], "r") as hf:
                    if "vertical" in hf or "east" in hf:
                        art_view.setdefault("components",
                                            {"path": vel_found[1], "dataset": "vertical"})
            except (OSError, ImportError):
                pass
    metric_for_level = {m["name"]: m["value"] for m in metrics}
    product_level = annotate(metric_for_level, art_view,
                             simulated=bool(doc.get("simulated")))

    return {
        "run_id": doc.get("run_id") or MISSING,
        "scenario": doc.get("scenario") or MISSING,
        "simulated": bool(doc.get("simulated")),
        "generated_at_utc": doc.get("generated_at_utc") or MISSING,
        "qa_status": (doc.get("qa") or {}).get("status") or MISSING,
        "evidence_level": doc.get("evidence_level") or MISSING,
        "metrics": metrics,
        "velocity": _velocity_stats(_find_velocity_file(doc, workspace)),
        "model": model,
        "product_level": product_level,
    }


# ---------------------------------------------------------------------------
# ② 结果骨架(纯代码拼接,确定性;没有 LLM 也是完整可用的草稿)
# ---------------------------------------------------------------------------

def _velocity_paragraph(v: dict) -> str:
    if not v.get("available"):
        return (f"速度场统计:{MISSING}({v.get('reason') or MISSING})"
                "—— 数据缺失如实说明,不作推算。")
    if v.get("min_mmyr") is None:
        return (f"速度场({v['path']})无有效像元"
                f"(有效像元占比 {_fmt(v['valid_pixel_pct'])}%),速率统计{MISSING}。")
    text = (f"速度场({v['path']})显示:LOS 形变速率介于 {_fmt(v['min_mmyr'])} 至 "
            f"{_fmt(v['max_mmyr'])} mm/yr,均值 {_fmt(v['mean_mmyr'])} mm/yr,"
            f"2%/98% 分位为 {_fmt(v['p2_mmyr'])} 与 {_fmt(v['p98_mmyr'])} mm/yr,"
            f"有效像元占比 {_fmt(v['valid_pixel_pct'])}%。")
    if isinstance(v["min_mmyr"], (int, float)) and v["min_mmyr"] < 0:
        # abs 不产生新数值 token(校验正则不含负号,-40.0 与 40.0 同 token)
        text += f"最大沉降速率(LOS 负向)为 {_fmt(abs(v['min_mmyr']))} mm/yr。"
    return text


def _model_paragraph(model: dict | None) -> str:
    if not model:
        return "形变模型拟合参数:未记录(账本中无形变模型步骤)。"
    if model["state"] == "done":
        return (f"形变速率场由第 {model['step_id']} 步「{model['name']}」以 "
                f"{model['method']} 方法拟合{_params_phrase(model['params'])}。")
    return (f"形变模型(第 {model['step_id']} 步,方法 {model['method']})状态为 "
            f"{model['state']},拟合结果不作陈述。")


def _metric_sentence(m: dict) -> str:
    label = _METRIC_LABEL.get(m["name"])
    head = f"{label}({m['name']})" if label else m["name"]
    value = _fmt(m["value"]) if m["value"] is not None else MISSING
    unit = f" {m['unit']}" if m["unit"] else ""
    text = f"{head} = {value}{unit}"
    if m["verdict"]:
        text += (f",{m['verdict']}台账阈值 {_fmt(m['threshold_value'])}"
                 f"({m['threshold_key']},状态 {m['threshold_status']})")
    return text


def results_skeleton(facts: dict) -> str:
    """事实闭集 → 中文结果章节骨架(确定性:同 facts 必得同文本)。"""
    f = facts
    paras: list[str] = [_velocity_paragraph(f["velocity"]), _model_paragraph(f["model"])]

    # 质量指标与判定(引用台账阈值口径;通过/未达标如实写)
    if f["metrics"]:
        line = ";".join(_metric_sentence(m) for m in f["metrics"])
        pending_note = ("台账状态 PENDING 的阈值仅产生警告,不构成硬性达标判定;"
                        if any(m["threshold_status"] == "PENDING" and m["verdict"]
                               for m in f["metrics"]) else "")
        paras.append(f"质量控制指标:{line}。{pending_note}"
                     f"QA 总体判定为 {f['qa_status']}"
                     "(账本口径:全部步骤 run_ok 通过或云端跳过为 pass,否则 fail)。")
    else:
        paras.append(f"本次运行未入账任何质量指标(质量指标:{MISSING});"
                     f"QA 总体判定为 {f['qa_status']}。")

    # 可回溯性与证据级
    paras.append(f"以上结果数值均来自 run {f['run_id']} 的 provenance 账本与产物重解析"
                 f"(账本导出时间 {f['generated_at_utc']} UTC),"
                 f"证据级别为 {f['evidence_level']}(六级证据阶梯),"
                 "每个数字可回溯至对应指标记录或产物文件。")

    pl = f.get("product_level") or {}
    level = pl.get("level") or "basic"
    pl_line = (f"产品级别:{level}"
               "(EGMS 对齐:basic 相对 LOS,calibrated GNSS 锚定,ortho 垂直与东西向)。")
    if pl.get("simulated") or f.get("simulated"):
        pl_line += "本次为模拟产物,级别标注不掩盖模拟属性。"
    paras.append(pl_line)

    # 模拟 run 强制警示(任务硬约束;与方法章节同句)
    if f["simulated"]:
        paras.append(SIMULATED_SENTENCE)
    return "\n\n".join(paras)


def results_facts_used(facts: dict) -> list[str]:
    """事实闭集 → 扁平「键=值」清单(API 回给前端/测试,便于逐条反查)。

    键名 velocity.p2_mmyr / p98_mmyr 同时为骨架里的分位标号 2/98 提供数字闭包。
    """
    f = facts
    out = [
        f"run_id={f['run_id']}",
        f"scenario={f['scenario']}",
        f"simulated={'true' if f['simulated'] else 'false'}",
        f"generated_at_utc={f['generated_at_utc']}",
        f"qa_status={f['qa_status']}",
        f"evidence_level={f['evidence_level']}",
    ]
    pl = f.get("product_level") or {}
    out.append(f"product_level={pl.get('level') or 'basic'}")
    out.append(f"product_level.simulated="
               f"{'true' if pl.get('simulated') or f.get('simulated') else 'false'}")
    v = f["velocity"]
    out.append(f"velocity.available={'true' if v.get('available') else 'false'}")
    if v.get("available"):
        out.append(f"velocity.path={v['path']}")
        for key in ("min_mmyr", "max_mmyr", "mean_mmyr", "p2_mmyr", "p98_mmyr",
                    "valid_pixel_pct"):
            out.append(f"velocity.{key}="
                       f"{_fmt(v[key]) if v.get(key) is not None else MISSING}")
    else:
        out.append(f"velocity.reason={v.get('reason') or MISSING}")
    model = f["model"]
    if model:
        out.append(f"model.step_id={model['step_id']}")
        out.append(f"model.method={model['method']}")
        out.append(f"model.state={model['state']}")
        out.extend(f"model.param.{k}={_fmt(val)}"
                   for k, val in sorted(model["params"].items()))
    else:
        out.append(f"model={MISSING}")
    for m in f["metrics"]:
        value = _fmt(m["value"]) if m["value"] is not None else MISSING
        unit = f" {m['unit']}" if m["unit"] else ""
        out.append(f"metric.{m['name']}={value}{unit}")
        if m["verdict"]:
            out.append(f"metric.{m['name']}.threshold={_fmt(m['threshold_value'])}"
                       f"({m['threshold_key']},{m['threshold_status']},{m['verdict']})")
    return out


# ---------------------------------------------------------------------------
# ③ LLM 润色(校验复用 draft._polish_valid,不过即回退骨架)
# ---------------------------------------------------------------------------

_RESULTS_POLISH_SYSTEM = (
    "你是论文结果章节的措辞润色器。输入是一份由账本 QA 指标与产物统计拼接的"
    "中文结果章节骨架,其中每个数字都经过溯源审计。你只允许改写措辞使行文更"
    "符合期刊习惯,严禁增加、删除或改动任何数值、指标名、方法名与「未记录」占位,"
    "严禁补充骨架之外的任何事实或数字;骨架若含「不构成科学证据」警示句,"
    '必须原样保留。只输出 JSON:{"draft": "<润色后的全文>"},不要输出其他字段。'
)


def _validation_facts(facts: dict) -> dict:
    """适配 draft._polish_valid 的入参形状:方法名保全清单 = 形变模型方法。"""
    model = facts.get("model") or {}
    steps = [{"method": model.get("method") or ""}] if model else []
    return {"steps": steps, "simulated": facts["simulated"]}


def draft_results(provider: LLMProvider | None, facts: dict) -> dict:
    """facts → {"draft": str, "llm_polish": bool, "facts_used": [...]}。

    与 draft_methods 同契约:骨架是保底且单独可用;LLM 润色是增强,
    未配置/失败/校验不过一律回退骨架(llm_polish=False)。
    """
    skeleton = results_skeleton(facts)
    result = {"draft": skeleton, "llm_polish": False,
              "facts_used": results_facts_used(facts)}
    if provider is None or not provider.enabled or len(skeleton) > _POLISH_INPUT_MAX:
        return result
    try:
        data = provider.complete_json(system=_RESULTS_POLISH_SYSTEM, user=skeleton,
                                      max_tokens=2048)
    except BrainUnavailable:
        return result
    polished = data.get("draft")
    if _polish_valid(skeleton, polished, _validation_facts(facts)):
        result["draft"] = polished
        result["llm_polish"] = True
    return result
