"""run_ok 双判定(AGENT-DESIGN §4.6):进程退出码 + 领域校验。

教训来源:swmm5 退出 0 但 rpt 里有 solver error(agentic-swmm);
MintPy 可以「成功」输出全 NaN 时序。退出码 0 ≠ 成功。

quality_gate 与 run_ok 分开:gate 失败是「质量门拦截」不是「错误」(§7.4 gate_stop),
且引用 PENDING 阈值的 gate 只降级为 warning(§4.13 纪律 2)。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from insar_agent.audit.contract import Threshold
from insar_agent.registry.model import Capability, RunOkCheck


@dataclass
class CheckResult:
    check: str
    ok: bool
    severity: str  # pass | fail | stop | warn
    detail: str = ""


@dataclass
class RunOkResult:
    ok: bool
    gate_stop: bool
    checks: list[CheckResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_qa(self) -> list[dict]:
        return [{"check": c.check, "ok": c.ok, "severity": c.severity, "detail": c.detail}
                for c in self.checks]


def _artifact_path(artifacts: dict[str, Path], art_id: str) -> Path | None:
    return artifacts.get(art_id)


def _read_metric(metrics: dict[str, float], artifacts: dict[str, Path], name: str) -> float | None:
    if name in metrics:
        return metrics[name]
    # 从 qa_report JSON 产物兜底解析(声明式指标来源的最短路径)
    qa = artifacts.get("qa_report")
    if qa and qa.exists() and qa.suffix == ".json":
        try:
            data = json.loads(qa.read_text(encoding="utf-8"))
            value = data.get(name)
            if isinstance(value, (int, float)):
                return float(value)
        except (json.JSONDecodeError, OSError):
            return None
    return None


_H5_SUFFIXES = (".h5", ".he5", ".hdf5")


def _nan_fraction(path: Path) -> float | None:
    """栅格 NaN 占比。支持 .npy(numpy)与 HDF5(h5py,均为可选依赖);
    其余格式或依赖缺失返回 None(无法判定,由调用方降级为 warn)。

    HDF5 多数据集探测:遍历全部浮点数据集,取元素数最大者为主数据
    (同大小按名字典序取首,保证判定确定性)—— MintPy timeseries.h5
    (timeseries/date/bperp)与 velocity.h5(velocity/velocityStd)均由此命中主栅格。
    """
    if path.suffix == ".npy":
        try:
            import numpy as np

            arr = np.load(path, mmap_mode="r")
            total = arr.size
            if total == 0:
                return 1.0
            return float(np.isnan(arr).sum()) / float(total)
        except Exception:
            return None
    if path.suffix in _H5_SUFFIXES:
        try:
            import h5py
            import numpy as np

            with h5py.File(path, "r") as f:
                candidates: list[tuple[int, str]] = []

                def _collect(name: str, obj) -> None:
                    if isinstance(obj, h5py.Dataset) and obj.dtype.kind == "f":
                        candidates.append((int(obj.size), name))

                f.visititems(_collect)
                if not candidates:
                    return None  # 无浮点数据集:无法判定
                candidates.sort(key=lambda t: (-t[0], t[1]))
                total, main = candidates[0]
                if total == 0:
                    return 1.0
                ds = f[main]
                if ds.ndim == 0:
                    return float(np.isnan(ds[()]))
                # 按首轴分片统计,避免整块载入大栅格
                nan_count = 0
                for i in range(ds.shape[0]):
                    nan_count += int(np.isnan(ds[i]).sum())
                return float(nan_count) / float(total)
        except Exception:
            return None
    return None


def _eval_check(
    check: RunOkCheck,
    *,
    exit_code: int | None,
    artifacts: dict[str, Path],
    log_path: Path | None,
    metrics: dict[str, float],
    contract: dict[str, Threshold],
    hard: bool,
) -> CheckResult:
    kind = check.check

    if kind == "exit_code":
        ok = exit_code == check.equals
        return CheckResult(kind, ok, "pass" if ok else "fail",
                           f"exit_code={exit_code},期望 {check.equals}")

    if kind == "artifact_exists":
        p = _artifact_path(artifacts, check.id)
        ok = p is not None and p.exists()
        return CheckResult(kind, ok, "pass" if ok else "fail",
                           f"artifact {check.id}: {p if p else '未发现'}")

    if kind == "artifact_nonempty":
        p = _artifact_path(artifacts, check.id)
        ok = bool(p and p.exists() and (p.stat().st_size > 0 if p.is_file() else any(p.iterdir())))
        return CheckResult(kind, ok, "pass" if ok else "fail", f"artifact {check.id}")

    if kind == "log_absent":
        if log_path is None or not log_path.exists():
            return CheckResult(kind, True, "pass", "无日志文件")
        text = log_path.read_text(encoding="utf-8", errors="replace")
        hit = re.search(check.pattern, text)
        ok = hit is None
        return CheckResult(kind, ok, "pass" if ok else "fail",
                           f"日志命中禁用模式 {check.pattern!r}: {hit.group(0)!r}" if hit else "")

    if kind == "not_all_nan":
        p = _artifact_path(artifacts, check.id)
        frac = _nan_fraction(p) if p else None
        if frac is None:
            return CheckResult(kind, True, "warn", f"artifact {check.id}: 无法解析,跳过 NaN 检查")
        ok = frac < 1.0
        return CheckResult(kind, ok, "pass" if ok else "fail", f"NaN 占比 {frac:.2%}")

    if kind == "nan_fraction_below":
        p = _artifact_path(artifacts, check.id)
        frac = _nan_fraction(p) if p else None
        limit = check.value
        if check.threshold_key and check.threshold_key in contract:
            limit = float(contract[check.threshold_key].value)  # type: ignore[arg-type]
        if frac is None or limit is None:
            return CheckResult(kind, True, "warn", "无法解析,跳过")
        ok = frac < float(limit)
        return CheckResult(kind, ok, "pass" if ok else "fail", f"NaN {frac:.2%} < {limit}")

    if kind == "metric_min":
        value = _read_metric(metrics, artifacts, check.metric)
        threshold = check.value
        pending = False
        if check.threshold_key:
            th = contract.get(check.threshold_key)
            if th is None:
                return CheckResult(kind, False, "stop" if hard else "fail",
                                   f"阈值 {check.threshold_key} 不在台账 —— 契约破坏")
            threshold = th.value
            pending = th.pending
        if value is None:
            # 指标缺失:硬 gate 无法证明质量 → 拦停;PENDING → 警告
            sev = "warn" if pending else ("stop" if hard and check.on_fail == "stop" else "fail")
            return CheckResult(kind, sev == "warn", sev, f"指标 {check.metric} 缺失")
        ok = float(value) >= float(threshold)  # type: ignore[arg-type]
        if not ok and pending:
            # §4.13 纪律 2:PENDING 阈值不参与硬 gate,只 warning
            return CheckResult(kind, True, "warn",
                               f"{check.metric}={value} < {threshold}(阈值 PENDING,仅警告)")
        sev = "pass" if ok else ("stop" if hard and check.on_fail == "stop" else "fail")
        return CheckResult(kind, ok, sev, f"{check.metric}={value},阈值 {threshold}")

    return CheckResult(kind, False, "fail", f"未知检查类型 {kind}")


def evaluate_run_ok(
    cap: Capability,
    *,
    exit_code: int | None,
    artifacts: dict[str, Path],
    log_path: Path | None,
    metrics: dict[str, float] | None = None,
    contract: dict[str, Threshold] | None = None,
) -> RunOkResult:
    contract = contract or {}
    metrics = metrics or {}
    result = RunOkResult(ok=True, gate_stop=False)

    for check in cap.run_ok:
        cr = _eval_check(check, exit_code=exit_code, artifacts=artifacts, log_path=log_path,
                         metrics=metrics, contract=contract, hard=False)
        result.checks.append(cr)
        if cr.severity == "warn":
            result.warnings.append(cr.detail)
        elif not cr.ok:
            result.ok = False

    for check in cap.quality_gate:
        cr = _eval_check(check, exit_code=exit_code, artifacts=artifacts, log_path=log_path,
                         metrics=metrics, contract=contract, hard=True)
        result.checks.append(cr)
        if cr.severity == "warn":
            result.warnings.append(cr.detail)
        elif cr.severity == "stop" and not cr.ok:
            result.gate_stop = True
        elif not cr.ok:
            result.ok = False

    return result
