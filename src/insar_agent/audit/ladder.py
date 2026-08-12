"""六级证据阶梯的机器可判定实现(AGENT-DESIGN §6.3)。

    runnable → checked → audited → calibrated → validated → publishable

原则:
  - 取最差(InSAR-Pro result_metadata.py:148 的 overall_tier):有一步降级则整体降级。
  - PENDING 阈值封顶 audited(§4.13):还有待标定的阈值就不能声称 validated。
  - simulated run 封顶 runnable:演示不冒充证据。
  - fork 不空洞过审(FOLLOWUPS #11):复用步骤的产物记录沿 parent_run_id 祖先链
    核对 —— 可寻得且指纹一致才计入「审计完整」,找不到或不一致停在 checked;
    父 run 的外部验证指标(gnss/crossval 等)不自动继承,只在 parent_validations
    里列出「曾验证过什么 + 当时的参数指纹」供人判断 —— 换参数后科学上必须重新验证。
  - 云端跳过不免检(FOLLOWUPS #12):skipped(云端已完成)步骤有可核对的
    hyp3_manifest 才计入完整,且整 run 封顶 audited(云端过程不可本地复核,
    calibrated/validated 级的外部验证必须本地重新做);无 manifest 封顶 checked;
    全跳过 + 零本地产物封顶 checked(manifest 单独不构成审计完整)。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from insar_agent.audit.contract import Threshold, pending_keys
from insar_agent.core.store import StepRow, Store

LADDER = ("runnable", "checked", "audited", "calibrated", "validated", "publishable")

# 祖先链遍历步长上限,与 Store.find_artifact 的防环限一致
_ANCESTRY_LIMIT = 32

# 外部验证类指标(calibrated/validated 级证据)的命名前缀:fork 后不自动继承
EXTERNAL_VALIDATION_PREFIXES = ("gnss_", "crossval_", "leveling_")

# 云端(HyP3)完成声明的本地证据候选(工作区相对路径,按序查找)
_CLOUD_MANIFEST_CANDIDATES = ("hyp3/hyp3_manifest.json", "hyp3_manifest.json")


def cloud_evidence(workspace: Path | None) -> dict:
    """跳过步骤(云端已完成)的证据:HyP3 manifest 存在即引用并指纹,缺失如实声明。

    诚实原则:跳过 ≠ 免检 —— provenance 里必须能看到「凭什么说云端做过」,
    而不是一句无凭据的 skipped。ladder 与 ledger 共用此判定(#12 后上移至此)。
    """
    if workspace is None:
        return {"present": False, "detail": "未提供 workspace,无法查找云端证据"}
    for rel in _CLOUD_MANIFEST_CANDIDATES:
        p = workspace / rel
        if not p.is_file():
            continue
        raw = p.read_bytes()
        out: dict = {"present": True, "kind": "hyp3_manifest", "path": rel,
                     "sha256": hashlib.sha256(raw).hexdigest()}
        try:
            data = json.loads(raw.decode("utf-8"))
            out["entries"] = len(data) if isinstance(data, (list, dict)) else 0
        except (json.JSONDecodeError, UnicodeDecodeError):
            out["parse_error"] = True
        return out
    return {"present": False,
            "detail": "未找到 hyp3_manifest.json(云端完成声明缺本地证据)"}


@dataclass
class EvidenceResult:
    level: str
    level_index: int
    reasons: list[str] = field(default_factory=list)  # 为什么停在这一级
    ceiling: str | None = None
    ceiling_reason: str = ""
    # 每步证据来源(#11/#12):local | inherited(parent=xx) | cloud(manifest sha256:xx) | missing
    step_sources: dict[str, dict] = field(default_factory=dict)
    # fork 场景:父链曾有的外部验证指标(不继承,仅列出供人判断是否需要重新验证)
    parent_validations: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"level": self.level, "level_index": self.level_index,
                "ladder": list(LADDER), "reasons": self.reasons,
                "ceiling": self.ceiling, "ceiling_reason": self.ceiling_reason,
                "step_sources": self.step_sources,
                "parent_validations": self.parent_validations}


def _reused_from(step: StepRow) -> str | None:
    """fork 复用步骤的标记(planner.fork_run 写入 qa);返回 fork 时的直接父 run_id。"""
    for check in step.qa or []:
        if check.get("check") == "reused_from_parent":
            return str(check.get("detail") or "")
    return None


def _hashes_match(anc_step: StepRow, step: StepRow) -> bool:
    """复用一致性 = fork_run 当时的复用条件:eval_hash 与 local_hash 都未变。"""
    return (anc_step.eval_hash == step.eval_hash
            and anc_step.local_hash == step.local_hash)


def _resolve_inherited(store: Store, run: dict, step: StepRow) -> dict:
    """沿 parent_run_id 祖先链为 fork 复用步骤定位产物记录并核对指纹一致性(#11)。

    与 Store.find_artifact 同序(就近祖先优先),核对的是账本记录本身:
    产出步骤的指纹须与本步一致(祖先 fork 后被改参/重跑即不一致),产物须有 fp。
    磁盘上的产物是否仍与记录一致归 stale.verify_artifacts 管,阶梯不做文件 IO。
    """
    current = run.get("parent_run_id")
    for _ in range(_ANCESTRY_LIMIT):
        if not current:
            return {"origin": "missing", "source": "missing",
                    "detail": "沿祖先链未找到复用步骤的产物记录"}
        ancestor = store.get_run(current)
        if ancestor is None:
            return {"origin": "missing", "source": "missing",
                    "detail": f"祖先 run {current} 不存在(链断裂)"}
        anc_step = store.load_step(current, step.step_id)
        arts = store.artifacts_of(current, step.step_id)
        if arts:
            if anc_step is None or not _hashes_match(anc_step, step):
                return {"origin": "missing", "source": "missing",
                        "parent_run_id": current,
                        "detail": f"祖先 {current} 的产物与本步指纹不一致"
                                  "(fork 后祖先被改参/重跑)"}
            no_fp = sorted(a["art_id"] for a in arts if not a["fp"])
            if no_fp:
                return {"origin": "missing", "source": "missing",
                        "parent_run_id": current,
                        "detail": f"祖先 {current} 的产物缺指纹:{no_fp}"}
            return {"origin": "inherited", "source": f"inherited(parent={current})",
                    "parent_run_id": current,
                    "artifacts": {a["art_id"]: a["fp"] for a in arts}}
        if anc_step is not None and _reused_from(anc_step) is None:
            # 走到执行原点(非复用步骤)且它没有产物记录
            if not _hashes_match(anc_step, step):
                return {"origin": "missing", "source": "missing",
                        "parent_run_id": current,
                        "detail": f"祖先 {current} 的原点步骤与本步指纹不一致"}
            if anc_step.run_ok == 1:
                # 原点本就不登记产物:与本地零产物步骤同等对待(合法,空产物集)
                return {"origin": "inherited",
                        "source": f"inherited(parent={current})",
                        "parent_run_id": current, "artifacts": {}}
            return {"origin": "missing", "source": "missing",
                    "parent_run_id": current,
                    "detail": f"祖先 {current} 的原点步骤无产物记录且 run_ok≠1,"
                              "复用依据不足"}
        current = ancestor.get("parent_run_id")
    return {"origin": "missing", "source": "missing",
            "detail": f"祖先链超过 {_ANCESTRY_LIMIT} 层(疑似成环)"}


def _source_step_args_hash(store: Store, run_id: str, source_artifact: str) -> str | None:
    """指标来源产物 → 产出步骤的 args_hash(外部验证当时的参数指纹)。

    source_artifact 存的是文件名/相对路径(executor 记 qa.name),
    匹配语义与 audit.verify._find_artifact_path 一致。
    """
    if not source_artifact:
        return None
    for art in store.artifacts_of(run_id):
        p = str(art["path"])
        if p == source_artifact or Path(p).name == source_artifact:
            src_step = store.load_step(run_id, int(art["step_id"]))
            return src_step.args_hash if src_step else None
    return None


def _collect_parent_validations(store: Store, run: dict) -> list[dict]:
    """父链曾有的外部验证指标 + 产出步骤的参数指纹(#11 决策:只列出、不继承)。

    外部验证绑定当时的参数,fork 改参后科学上必须重新验证;账本里给出
    「曾验证过什么、在哪个 run、参数指纹是什么」供人判断。同名指标取最近祖先
    (与产物解析的就近语义一致)。
    """
    out: list[dict] = []
    seen: set[str] = set()
    current = run.get("parent_run_id")
    for _ in range(_ANCESTRY_LIMIT):
        if not current:
            break
        ancestor = store.get_run(current)
        if ancestor is None:
            break
        for m in store.metrics_of(current):
            name = str(m["name"])
            if name in seen or not name.startswith(EXTERNAL_VALIDATION_PREFIXES):
                continue
            seen.add(name)
            out.append({
                "name": name, "value": m["value"], "unit": m["unit"],
                "run_id": current, "source_artifact": m["source_artifact"],
                "args_hash": _source_step_args_hash(
                    store, current, str(m["source_artifact"] or "")),
                "inherited": False,
                "note": "外部验证不随 fork 继承,需以当前参数重新验证",
            })
        current = ancestor.get("parent_run_id")
    return sorted(out, key=lambda d: d["name"])


def compute_evidence(store: Store, run_id: str, contract: dict[str, Threshold],
                     *, workspace: Path | None = None) -> EvidenceResult:
    run = store.get_run(run_id) or {}
    steps = store.load_steps(run_id)
    metrics = {m["name"]: m for m in store.metrics_of(run_id)}
    arts = store.artifacts_of(run_id)
    reasons: list[str] = []

    # ---- 每步证据来源(#11/#12;随 evidence 段进账本) ----
    skipped = [s for s in steps if s.state == "skipped"]
    cloud_ev = cloud_evidence(workspace) if skipped else None
    # 「可核」= manifest 存在且内容可解析(sha256 对不可解析的字节串也能算,
    # 但读不出条目的 manifest 不构成云端完成的证据)
    cloud_ok = bool(cloud_ev and cloud_ev.get("present")
                    and not cloud_ev.get("parse_error"))
    step_sources: dict[str, dict] = {}
    fork_gaps: dict[int, str] = {}  # 复用步骤的审计缺口:{step_id: 缺口说明}
    for s in steps:
        if s.state == "skipped":
            if cloud_ok:
                sha = str(cloud_ev.get("sha256") or "")
                step_sources[str(s.step_id)] = {
                    "origin": "cloud",
                    "source": f"cloud(manifest sha256:{sha[:12]})",
                    "manifest_sha256": sha,
                    "manifest_path": cloud_ev.get("path")}
            else:
                detail = ("manifest 存在但无法解析" if (cloud_ev or {}).get("parse_error")
                          else (cloud_ev or {}).get("detail") or "无云端证据")
                step_sources[str(s.step_id)] = {
                    "origin": "missing", "source": "missing",
                    "detail": f"云端完成声明缺本地证据:{detail}"}
            continue
        if _reused_from(s) is not None:
            entry = _resolve_inherited(store, run, s)
            step_sources[str(s.step_id)] = entry
            if entry["origin"] == "missing":
                fork_gaps[s.step_id] = entry.get("detail", "")
            continue
        step_sources[str(s.step_id)] = {"origin": "local", "source": "local"}

    parent_validations = (_collect_parent_validations(store, run)
                          if run.get("parent_run_id") else [])

    # ---- 封顶判定(多来源候选取最低;同级原因合并) ----
    candidates: list[tuple[str, str]] = []
    pending = pending_keys(contract)
    if run.get("simulated"):
        candidates.append(("runnable", "模拟执行(引擎缺失),演示结果不构成证据"))
    if pending:
        candidates.append(("audited", f"{len(pending)} 项阈值待标定:{pending}"))
    if skipped:
        sids = [s.step_id for s in skipped]
        if cloud_ok:
            candidates.append((
                "audited",
                f"步骤 {sids} 云端(HyP3)完成,仅有 manifest 佐证,过程不可本地复核,"
                "calibrated 及以上的外部验证须本地重新做"))
        else:
            candidates.append((
                "checked", f"云端跳过步骤 {sids} 无可核对的 hyp3_manifest(审计缺口)"))
        if len(skipped) == len(steps) and not arts:
            candidates.append(("checked", "全部步骤云端跳过且无本地产物,不高于 checked"))
    ceiling: str | None = None
    ceiling_reason = ""
    if candidates:
        low = min(LADDER.index(level) for level, _ in candidates)
        ceiling = LADDER[low]
        ceiling_reason = ";".join(r for level, r in candidates
                                  if LADDER.index(level) == low)

    def finish(level: str, index: int) -> EvidenceResult:
        result = EvidenceResult(level, index, reasons,
                                step_sources=step_sources,
                                parent_validations=parent_validations)
        if ceiling is not None:
            cap_idx = LADDER.index(ceiling)
            if result.level_index > cap_idx:
                result.reasons.append(f"封顶 {ceiling}:{ceiling_reason}")
                result.level = ceiling
                result.level_index = cap_idx
        result.ceiling = ceiling
        result.ceiling_reason = ceiling_reason
        return result

    # L0 runnable:全部步骤 VERIFIED(或显式 skipped)
    incomplete = [s.step_id for s in steps
                  if s.stage != "VERIFIED" and s.state != "skipped"]
    if incomplete or not steps:
        reasons.append(f"未完成步骤:{incomplete or '无步骤'}")
        return finish("runnable", 0)

    # L1 checked:全部 run_ok(复用步骤照常继承 run_ok/qa —— 执行没变,结论可携带)
    not_ok = [s.step_id for s in steps if s.state != "skipped" and s.run_ok != 1]
    if not_ok:
        reasons.append(f"run_ok 未通过:{not_ok}")
        return finish("runnable", 0)

    # L2 audited:provenance 完整 —— 本 run 产物有指纹、指标全部重解析通过;
    # fork 复用步骤须沿祖先链可寻得且指纹一致(#11:不再对零产物空洞放行)
    missing_fp = [a["art_id"] for a in arts if not a["fp"]]
    unparsed = [m["name"] for m in metrics.values() if m["reparsed_ok"] != 1]
    if missing_fp or unparsed or fork_gaps:
        parts = []
        if missing_fp or unparsed:
            parts.append(f"缺指纹:{missing_fp};未重解析:{unparsed}")
        if fork_gaps:
            gaps = ";".join(f"步骤 {sid}:{d}" for sid, d in sorted(fork_gaps.items()))
            parts.append(f"fork 复用不可核对 —— {gaps}")
        reasons.append("审计不完整 —— " + ";".join(parts))
        return finish("checked", 1)

    # L3 calibrated:存在 GNSS/水准比对(fork 后不继承 —— 换参数须重新验证)
    if "gnss_rmse_mm" not in metrics:
        prior = [v["name"] for v in parent_validations if v["name"].startswith("gnss_")]
        hint = (f"(父链曾有 {prior},fork 后不自动继承,需重新验证)" if prior else "")
        reasons.append("无 GNSS/水准外部比对记录" + hint)
        return finish("audited", 2)

    # L4 validated:双链交叉验证达标(阈值必须非 PENDING —— 已被封顶逻辑保证)
    cv = metrics.get("crossval_r")
    th = contract.get("corr_threshold")
    if cv is None or th is None or cv["value"] is None or float(cv["value"]) < float(th.value):  # type: ignore[arg-type]
        prior = [v["name"] for v in parent_validations
                 if v["name"].startswith("crossval_")]
        hint = (f"(父链曾有 {prior},fork 后不自动继承,需重新验证)"
                if cv is None and prior else "")
        reasons.append("双链交叉验证缺失或未达契约阈值" + hint)
        return finish("calibrated", 3)

    # L5 publishable:证据边界表 + 跨环境复现记录(运行元数据里显式声明)
    intent = run.get("intent") or "{}"
    if "cross_env_reproduced" not in str(intent):
        reasons.append("无跨环境复现记录/证据边界表")
        return finish("validated", 4)

    return finish("publishable", 5)
