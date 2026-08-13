"""审计层全枝深测:runok 双判定 / contract 来源纪律 / ladder 六级证据阶梯 / fork 语义。

与 test_provenance.py 的分工:那边走执行器端到端验收;这里直接构造 store 状态与
真实微型文件(KB 级 npy/h5),逐分支验证 audit 层的判定语义、边界行为与降级信息
可读性。测试数据一律现造,不依赖任何外部数据。
"""

from __future__ import annotations

import numpy as np
import pytest

from insar_agent.audit.contract import Threshold, load_contract, pending_keys
from insar_agent.audit.ladder import LADDER, compute_evidence
from insar_agent.audit.runok import _nan_fraction, evaluate_run_ok
from insar_agent.registry.model import Capability, Method, RunOkCheck

# ---------------- 工具 ----------------

_H = dict(task_hash="t" * 64, args_hash="a" * 64, local_hash="l" * 64, eval_hash="e" * 64)


def _cap(**kw) -> Capability:
    base = dict(id=6, name="审计深测步", deps=(), methods=(Method("m1", "m1", "-"),),
                default_method="m1")
    base.update(kw)
    return Capability(**base)


def _th(key: str, value, *, status: str = "OK", source: str = "local_calibration",
        ref: str = "tests/test_audit_deep.py 固定测试值") -> Threshold:
    return Threshold(key=key, value=value, source=source, ref=ref, status=status)


OK_CONTRACT = {"corr_threshold": _th("corr_threshold", 0.85)}
PENDING_CONTRACT = {"corr_threshold": _th("corr_threshold", 0.85, status="PENDING")}


# ================= runok:nan_fraction 真实文件(npy / h5) =================

def test_nan_fraction_npy_real_files(tmp_path):
    half = tmp_path / "half.npy"
    np.save(half, np.array([[1.0, np.nan], [np.nan, 4.0]], dtype=np.float32))
    assert _nan_fraction(half) == 0.5

    all_nan = tmp_path / "all.npy"
    np.save(all_nan, np.full((3, 3), np.nan, dtype=np.float64))
    assert _nan_fraction(all_nan) == 1.0

    # 空数组按全 NaN 处置(零数据不能充当证据)
    empty = tmp_path / "empty.npy"
    np.save(empty, np.zeros((0,), dtype=np.float32))
    assert _nan_fraction(empty) == 1.0

    # 不认识的格式 → None(无法判定,不硬猜)
    assert _nan_fraction(tmp_path / "opaque.tif") is None


def test_not_all_nan_and_nan_fraction_boundary_on_npy(tmp_path):
    half = tmp_path / "half.npy"
    np.save(half, np.array([1.0, np.nan], dtype=np.float32))  # frac = 0.5
    cap = _cap(run_ok=(RunOkCheck("not_all_nan", id="ras"),
                       RunOkCheck("nan_fraction_below", id="ras", value=0.5),
                       RunOkCheck("nan_fraction_below", id="ras", value=0.51)))
    res = evaluate_run_ok(cap, exit_code=0, artifacts={"ras": half}, log_path=None)
    not_all, at_limit, below_limit = res.checks
    assert not_all.ok and not_all.severity == "pass"
    # 边界:frac 恰等于阈值 → 不通过(严格小于)
    assert not at_limit.ok and at_limit.severity == "fail"
    assert "50.00%" in at_limit.detail
    assert below_limit.ok
    assert not res.ok  # run_ok 检查失败拖垮整体


def test_nan_fraction_h5_multidataset_probe(tmp_path):
    """h5 多数据集探测:取最大的浮点数据集为主数据;非浮点与小数据集不干扰判定。"""
    h5py = pytest.importorskip("h5py")
    path = tmp_path / "timeseries.h5"
    ts = np.ones((2, 3, 3), dtype=np.float32)  # 18 元素
    ts[0] = np.nan  # 9 个 NaN → 0.5
    with h5py.File(path, "w") as f:
        f["timeseries"] = ts                        # 主数据集(最大浮点)
        f["date"] = np.arange(40, dtype=np.int64)   # 元素更多但非浮点 → 不参与
        f["bperp"] = np.zeros(3, dtype=np.float32)  # 更小的浮点 → 不选
        f["geo/extra"] = np.zeros(2, dtype=np.float32)  # 嵌套组也在探测范围
    assert _nan_fraction(path) == 0.5


def test_nan_fraction_h5_tie_break_no_float_and_empty(tmp_path):
    h5py = pytest.importorskip("h5py")
    tie = tmp_path / "tie.h5"
    with h5py.File(tie, "w") as f:
        f["b_clean"] = np.zeros((2, 2), dtype=np.float32)
        f["a_bad"] = np.full((2, 2), np.nan, dtype=np.float32)
    # 同大小按名字典序取首(a_bad)—— 判定必须确定,不随遍历顺序漂移
    assert _nan_fraction(tie) == 1.0

    no_float = tmp_path / "no_float.h5"
    with h5py.File(no_float, "w") as f:
        f["date"] = np.arange(5, dtype=np.int64)
    assert _nan_fraction(no_float) is None  # 无浮点数据集 → 无法判定

    hollow = tmp_path / "hollow.h5"
    with h5py.File(hollow, "w") as f:
        f["only"] = np.zeros((0,), dtype=np.float32)
    assert _nan_fraction(hollow) == 1.0  # 零元素同空 npy 语义


def test_h5_checks_through_evaluate_run_ok(tmp_path):
    h5py = pytest.importorskip("h5py")
    dead = tmp_path / "velocity.h5"
    with h5py.File(dead, "w") as f:
        f["velocity"] = np.full((3, 3), np.nan, dtype=np.float32)
    cap = _cap(run_ok=(RunOkCheck("not_all_nan", id="vel"),))
    res = evaluate_run_ok(cap, exit_code=0, artifacts={"vel": dead}, log_path=None)
    assert not res.ok
    assert "100.00%" in res.checks[0].detail  # 全 NaN 产物被真实文件检查抓住

    half = tmp_path / "ts.h5"
    with h5py.File(half, "w") as f:
        f["timeseries"] = np.array([1.0, np.nan], dtype=np.float32)
    contract = {"nan_fraction_below": _th("nan_fraction_below", 0.6)}
    cap2 = _cap(run_ok=(RunOkCheck("nan_fraction_below", id="ts",
                                   threshold_key="nan_fraction_below"),))
    res2 = evaluate_run_ok(cap2, exit_code=0, artifacts={"ts": half}, log_path=None,
                           contract=contract)
    assert res2.ok  # 0.5 < 0.6,台账阈值生效

    # 无浮点数据集的 h5 → 无法判定,降级为 warn 且信息可读
    opaque = tmp_path / "opaque.h5"
    with h5py.File(opaque, "w") as f:
        f["date"] = np.arange(3, dtype=np.int64)
    res3 = evaluate_run_ok(cap, exit_code=0, artifacts={"vel": opaque}, log_path=None)
    assert res3.ok and res3.checks[0].severity == "warn"
    assert res3.warnings and "跳过 NaN 检查" in res3.warnings[0]


# ================= runok:log_pattern / metric 边界 / gate_stop =================

def test_log_pattern_hit_and_miss(tmp_path):
    log = tmp_path / "job.log"
    log.write_text("step ok\nSegmentation fault (core dumped)\n", encoding="utf-8")

    hit = evaluate_run_ok(
        _cap(run_ok=(RunOkCheck("log_absent", pattern=r"ERROR|Segmentation fault"),)),
        exit_code=0, artifacts={}, log_path=log)
    assert not hit.ok and hit.checks[0].severity == "fail"
    assert "Segmentation fault" in hit.checks[0].detail  # 命中的片段原样可读

    miss = evaluate_run_ok(
        _cap(run_ok=(RunOkCheck("log_absent", pattern=r"Traceback"),)),
        exit_code=0, artifacts={}, log_path=log)
    assert miss.ok and miss.checks[0].detail == ""

    absent = evaluate_run_ok(
        _cap(run_ok=(RunOkCheck("log_absent", pattern=r"ERROR"),)),
        exit_code=0, artifacts={}, log_path=tmp_path / "nope.log")
    assert absent.ok and absent.checks[0].detail == "无日志文件"


def test_log_absent_hits_pattern_straddling_chunk_boundary(tmp_path):
    """流式分块扫描(REVIEW P2-5):禁用模式恰好跨 1 MiB 块边界仍命中(重叠窗)。"""
    from insar_agent.audit.runok import _LOG_SCAN_CHUNK

    log = tmp_path / "job.log"
    pad = "x" * (_LOG_SCAN_CHUNK - 3)  # 'ERR' 落在第一块末尾,'OR' 在第二块开头
    log.write_text(pad + "ERROR: boom\n" + "tail\n" * 20, encoding="utf-8")
    res = evaluate_run_ok(
        _cap(run_ok=(RunOkCheck("log_absent", pattern=r"ERROR"),)),
        exit_code=0, artifacts={}, log_path=log)
    assert not res.ok
    assert "ERROR" in res.checks[0].detail


def test_log_absent_clean_multi_chunk_log_passes(tmp_path):
    """干净的多块大日志(≈1.5 MiB)pass —— 语义与整读等价:全文无命中才算 absent。"""
    log = tmp_path / "job.log"
    log.write_text("all fine here\n" * 120_000, encoding="utf-8")
    res = evaluate_run_ok(
        _cap(run_ok=(RunOkCheck("log_absent", pattern=r"ERROR|Segmentation fault"),)),
        exit_code=0, artifacts={}, log_path=log)
    assert res.ok and res.checks[0].detail == ""


def test_nan_fraction_corrupt_files_still_degrade_to_none(tmp_path):
    """损坏的 npy/h5(预期内异常闭集)仍返回 None 降级 warn ——
    except 收窄(REVIEW P2-2)不误伤既有降级语义。"""
    bad_npy = tmp_path / "bad.npy"
    bad_npy.write_bytes(b"\x93NUMPY garbage-truncated-header")
    assert _nan_fraction(bad_npy) is None

    pytest.importorskip("h5py")
    bad_h5 = tmp_path / "bad.h5"
    bad_h5.write_bytes(b"definitely not hdf5 bytes")
    assert _nan_fraction(bad_h5) is None


def test_metric_min_exactly_at_threshold_passes():
    """边界语义:值恰等于阈值 → 通过(≥,不是 >)。"""
    cap = _cap(quality_gate=(RunOkCheck("metric_min", metric="crossval_r",
                                        threshold_key="corr_threshold", on_fail="stop"),))
    at = evaluate_run_ok(cap, exit_code=0, artifacts={}, log_path=None,
                         metrics={"crossval_r": 0.85}, contract=OK_CONTRACT)
    assert at.ok and not at.gate_stop
    assert at.checks[0].severity == "pass"

    below = evaluate_run_ok(cap, exit_code=0, artifacts={}, log_path=None,
                            metrics={"crossval_r": 0.84}, contract=OK_CONTRACT)
    assert below.gate_stop and below.ok  # 拦停 ≠ 错误:ok 位不受 gate 污染


def test_gate_stop_and_fail_are_distinct():
    # 硬 gate(on_fail=stop)失败 → gate_stop=True,ok 不动
    stop_cap = _cap(quality_gate=(RunOkCheck("metric_min", metric="m",
                                             threshold_key="corr_threshold",
                                             on_fail="stop"),))
    stopped = evaluate_run_ok(stop_cap, exit_code=0, artifacts={}, log_path=None,
                              metrics={"m": 0.1}, contract=OK_CONTRACT)
    assert stopped.gate_stop and stopped.ok
    assert stopped.checks[0].severity == "stop"

    # gate 声明 on_fail=fail → 一般失败,不拦停
    fail_cap = _cap(quality_gate=(RunOkCheck("metric_min", metric="m", value=0.85,
                                             on_fail="fail"),))
    failed = evaluate_run_ok(fail_cap, exit_code=0, artifacts={}, log_path=None,
                             metrics={"m": 0.1})
    assert not failed.ok and not failed.gate_stop
    assert failed.checks[0].severity == "fail"

    # run_ok 一般检查失败 → ok=False,同样不是 gate_stop
    rc_cap = _cap(run_ok=(RunOkCheck("exit_code", equals=0),))
    rc = evaluate_run_ok(rc_cap, exit_code=1, artifacts={}, log_path=None)
    assert not rc.ok and not rc.gate_stop
    assert "exit_code=1" in rc.checks[0].detail and "期望 0" in rc.checks[0].detail


def test_pending_threshold_warns_and_metric_missing_paths():
    gate = _cap(quality_gate=(RunOkCheck("metric_min", metric="crossval_r",
                                         threshold_key="corr_threshold", on_fail="stop"),))
    # PENDING 阈值 + 未达标 → 只警告(§4.13 纪律 2)
    warned = evaluate_run_ok(gate, exit_code=0, artifacts={}, log_path=None,
                             metrics={"crossval_r": 0.5}, contract=PENDING_CONTRACT)
    assert warned.ok and not warned.gate_stop
    assert warned.warnings and "PENDING" in warned.warnings[0]

    # 指标缺失 + PENDING → 警告
    missing_pending = evaluate_run_ok(gate, exit_code=0, artifacts={}, log_path=None,
                                      metrics={}, contract=PENDING_CONTRACT)
    assert missing_pending.ok
    assert "缺失" in missing_pending.warnings[0]

    # 指标缺失 + OK 硬 gate → 无法证明质量,拦停
    missing_hard = evaluate_run_ok(gate, exit_code=0, artifacts={}, log_path=None,
                                   metrics={}, contract=OK_CONTRACT)
    assert missing_hard.gate_stop
    assert missing_hard.checks[0].severity == "stop"
    assert "缺失" in missing_hard.checks[0].detail

    # 硬 gate 引用台账外阈值 → 契约破坏,拦停且信息点名
    rogue = _cap(quality_gate=(RunOkCheck("metric_min", metric="m",
                                          threshold_key="ghost_key", on_fail="stop"),))
    broken = evaluate_run_ok(rogue, exit_code=0, artifacts={}, log_path=None,
                             metrics={"m": 1.0}, contract=OK_CONTRACT)
    assert broken.gate_stop
    assert "ghost_key" in broken.checks[0].detail and "契约破坏" in broken.checks[0].detail


def test_metric_read_from_qa_report_artifact(tmp_path):
    """metrics 入参缺失时,从 qa_report JSON 产物兜底重读(声明式指标来源)。"""
    import json

    qa = tmp_path / "qa.json"
    qa.write_text(json.dumps({"crossval_r": 0.9}), encoding="utf-8")
    cap = _cap(quality_gate=(RunOkCheck("metric_min", metric="crossval_r",
                                        threshold_key="corr_threshold", on_fail="stop"),))
    res = evaluate_run_ok(cap, exit_code=0, artifacts={"qa_report": qa}, log_path=None,
                          metrics={}, contract=OK_CONTRACT)
    assert res.ok and not res.gate_stop


# ================= runok:缺失产物降级与显式失败 =================

def test_missing_artifact_degradation_messages(tmp_path):
    """产物缺失时:存在性检查显式失败(点名 id),NaN 检查降级 warn 且可读。"""
    cap = _cap(run_ok=(RunOkCheck("artifact_exists", id="unw"),
                       RunOkCheck("not_all_nan", id="unw"),
                       RunOkCheck("nan_fraction_below", id="unw", value=0.5),
                       RunOkCheck("artifact_nonempty", id="unw")))
    res = evaluate_run_ok(cap, exit_code=0, artifacts={}, log_path=None)
    exists, not_all, frac, nonempty = res.checks
    assert not exists.ok and "unw" in exists.detail and "未发现" in exists.detail
    assert not_all.ok and not_all.severity == "warn" and "无法解析" in not_all.detail
    assert frac.ok and frac.severity == "warn" and "跳过" in frac.detail
    assert not nonempty.ok
    assert not res.ok
    assert len(res.warnings) == 2  # 两条降级警告如实上浮

    # 已登记但文件被删 → 报出具体路径,不是空泛失败
    ghost = tmp_path / "ghost.npy"
    res2 = evaluate_run_ok(_cap(run_ok=(RunOkCheck("artifact_exists", id="g"),)),
                           exit_code=0, artifacts={"g": ghost}, log_path=None)
    assert not res2.ok and "ghost.npy" in res2.checks[0].detail


def test_artifact_nonempty_file_and_dir(tmp_path):
    f = tmp_path / "a.bin"
    f.write_bytes(b"x")
    empty_f = tmp_path / "e.bin"
    empty_f.write_bytes(b"")
    d = tmp_path / "d"
    d.mkdir()
    (d / "x.dat").write_text("1", encoding="utf-8")
    empty_d = tmp_path / "ed"
    empty_d.mkdir()
    cap = _cap(run_ok=(RunOkCheck("artifact_nonempty", id="f"),
                       RunOkCheck("artifact_nonempty", id="ef"),
                       RunOkCheck("artifact_nonempty", id="d"),
                       RunOkCheck("artifact_nonempty", id="ed")))
    res = evaluate_run_ok(cap, exit_code=0, log_path=None,
                          artifacts={"f": f, "ef": empty_f, "d": d, "ed": empty_d})
    assert [c.ok for c in res.checks] == [True, False, True, False]


def test_unknown_check_kind_fails_explicitly():
    res = evaluate_run_ok(_cap(run_ok=(RunOkCheck("quantum_check"),)),
                          exit_code=0, artifacts={}, log_path=None)
    assert not res.ok and "未知检查类型" in res.checks[0].detail


# ================= contract:来源纪律 =================

def test_contract_literature_without_ref_rejected():
    text = """
thresholds:
  unwrap_coverage:
    value: 0.7
    source: literature
    status: OK
"""
    with pytest.raises(ValueError, match="必须填 ref"):
        load_contract(text)


def test_contract_local_calibration_without_ref_rejected():
    text = """
thresholds:
  corr_threshold:
    value: 0.85
    source: local_calibration
"""
    with pytest.raises(ValueError, match="必须填 ref"):
        load_contract(text)


def test_contract_invalid_source_rejected():
    text = """
thresholds:
  magic_number:
    value: 42
    source: gut_feeling
    ref: "拍脑袋"
"""
    with pytest.raises(ValueError, match="非法 source"):
        load_contract(text)


def test_contract_upstream_default_needs_no_ref_and_pending_keys():
    text = """
thresholds:
  esd_coherence_threshold:
    value: 0.85
    source: upstream_default
  zz_pending:
    value: 1
    source: local_calibration
    ref: "待标定"
    status: PENDING
  aa_pending:
    value: 2
    source: literature
    ref: "待查证"
    status: pending
"""
    contract = load_contract(text)
    th = contract["esd_coherence_threshold"]
    assert th.ref == "" and th.status == "OK" and not th.pending
    # pending 判定大小写不敏感;pending_keys 输出有序(报告可复现)
    assert pending_keys(contract) == ["aa_pending", "zz_pending"]


# ================= ladder:六级全枝 =================

def _mk_run(store, run_id="r1", *, simulated=False, intent=None):
    store.create_session("s1", "test")
    if store.get_run(run_id) is None:
        store.create_run(run_id, "s1", workspace="ws", simulated=simulated, intent=intent)


def _add_step(store, run_id, sid, *, verified=True, state="done", run_ok=1, skipped=False):
    store.create_step(run_id, sid, capability=str(sid), name=f"步{sid}", method="m",
                      params={}, hashes=_H, state="skipped" if skipped else "pending")
    if skipped or not verified:
        return
    store.advance(run_id, sid, "VERIFIED", state=state, run_ok=run_ok)


def _audited_state(store, run_id="r1", *, simulated=False, intent=None):
    """最小完备的 L2 状态:VERIFIED 步 + 带指纹产物 + 已重解析指标。"""
    _mk_run(store, run_id, simulated=simulated, intent=intent)
    _add_step(store, run_id, 6)
    store.record_artifact(run_id, 6, "qa_report", path="products/qa.json", kind="REPORT",
                          layout="", policy="content", fp="content:v1:" + "f" * 64)
    store.record_metric(run_id, "unwrap_coverage", value=0.94, source_artifact="qa.json",
                        source_field="unwrap_coverage", reparsed_ok=True)


def _validated_state(store, run_id="r1", *, simulated=False, intent=None, crossval=0.92):
    _audited_state(store, run_id, simulated=simulated, intent=intent)
    store.record_metric(run_id, "gnss_rmse_mm", value=1.2, source_artifact="qa.json",
                        source_field="gnss_rmse_mm", reparsed_ok=True)
    store.record_metric(run_id, "crossval_r", value=crossval, source_artifact="qa.json",
                        source_field="crossval_r", reparsed_ok=True)


def test_ladder_levels_pinned():
    assert LADDER == ("runnable", "checked", "audited", "calibrated",
                      "validated", "publishable")


def test_ladder_runnable_incomplete_steps(store):
    _mk_run(store)
    _add_step(store, "r1", 6, verified=False)
    ev = compute_evidence(store, "r1", OK_CONTRACT)
    assert (ev.level, ev.level_index) == ("runnable", 0)
    assert any("未完成步骤" in r for r in ev.reasons)
    assert ev.ceiling is None  # 全 OK 契约:不封顶


def test_ladder_runnable_empty_run(store):
    _mk_run(store)
    ev = compute_evidence(store, "r1", OK_CONTRACT)
    assert ev.level == "runnable" and "无步骤" in ev.reasons[0]


def test_ladder_checked_negative_run_ok_failed(store):
    _mk_run(store)
    _add_step(store, "r1", 6, run_ok=0)
    ev = compute_evidence(store, "r1", OK_CONTRACT)
    assert ev.level == "runnable"
    assert any("run_ok 未通过" in r for r in ev.reasons)


def test_ladder_checked_missing_fingerprint(store):
    _mk_run(store)
    _add_step(store, "r1", 6)
    store.record_artifact("r1", 6, "unw", path="data/unw", kind="DATA",
                          layout="", policy="stat", fp="")
    ev = compute_evidence(store, "r1", OK_CONTRACT)
    assert (ev.level, ev.level_index) == ("checked", 1)
    assert any("缺指纹" in r and "unw" in r for r in ev.reasons)


def test_ladder_checked_unreparsed_metric(store):
    _mk_run(store)
    _add_step(store, "r1", 6)
    # reparsed_ok=None(从未重解析)与 False(重解析不一致)都不算数
    store.record_metric("r1", "never_checked", value=1.0, source_artifact="qa.json",
                        source_field="x", reparsed_ok=None)
    ev = compute_evidence(store, "r1", OK_CONTRACT)
    assert ev.level == "checked"
    assert any("未重解析" in r and "never_checked" in r for r in ev.reasons)


def test_ladder_audited_no_gnss(store):
    _audited_state(store)
    ev = compute_evidence(store, "r1", OK_CONTRACT)
    assert (ev.level, ev.level_index) == ("audited", 2)
    assert any("GNSS" in r for r in ev.reasons)


def test_ladder_calibrated_no_crossval(store):
    _audited_state(store)
    store.record_metric("r1", "gnss_rmse_mm", value=1.2, source_artifact="qa.json",
                        source_field="gnss_rmse_mm", reparsed_ok=True)
    ev = compute_evidence(store, "r1", OK_CONTRACT)
    assert (ev.level, ev.level_index) == ("calibrated", 3)
    assert any("交叉验证" in r for r in ev.reasons)


def test_ladder_validated_below_threshold_stays_calibrated(store):
    _validated_state(store, crossval=0.5)
    ev = compute_evidence(store, "r1", OK_CONTRACT)
    assert ev.level == "calibrated"


def test_ladder_validated_boundary_and_missing_cross_env(store):
    # crossval 恰等于阈值 → 达标(≥ 语义);无跨环境复现记录 → 停在 validated
    _validated_state(store, crossval=0.85)
    ev = compute_evidence(store, "r1", OK_CONTRACT)
    assert (ev.level, ev.level_index) == ("validated", 4)
    assert any("跨环境复现" in r for r in ev.reasons)


def test_ladder_publishable_all_ok_no_ceiling(store):
    _validated_state(store, intent={"cross_env_reproduced": True})
    ev = compute_evidence(store, "r1", OK_CONTRACT)
    assert (ev.level, ev.level_index) == ("publishable", 5)
    assert ev.reasons == [] and ev.ceiling is None and ev.ceiling_reason == ""


def test_ladder_crossval_none_or_missing_threshold_key(store):
    _validated_state(store)
    store.record_metric("r1", "crossval_r", value=None, source_artifact="qa.json",
                        source_field="crossval_r", reparsed_ok=True)
    assert compute_evidence(store, "r1", OK_CONTRACT).level == "calibrated"

    # 台账里没有 corr_threshold → 无法声称 validated
    _validated_state(store, run_id="r2")
    assert compute_evidence(store, "r2", {}).level == "calibrated"


def test_ladder_pending_caps_at_audited(store):
    """PENDING 阈值存在 → 即便满足 publishable 的全部条件也封顶 audited。"""
    _validated_state(store, intent={"cross_env_reproduced": True})
    ev = compute_evidence(store, "r1", PENDING_CONTRACT)
    assert (ev.level, ev.ceiling) == ("audited", "audited")
    assert "待标定" in ev.ceiling_reason and "corr_threshold" in ev.ceiling_reason
    assert any("封顶" in r for r in ev.reasons)


def test_ladder_pending_cap_only_lowers_never_lifts(store):
    _mk_run(store)
    _add_step(store, "r1", 6)
    store.record_artifact("r1", 6, "unw", path="data/unw", kind="DATA",
                          layout="", policy="stat", fp="")
    ev = compute_evidence(store, "r1", PENDING_CONTRACT)
    assert ev.level == "checked"  # 封顶只降不升,低于顶的级别原样保留
    assert ev.ceiling == "audited"


def test_ladder_simulated_caps_at_runnable(store):
    _validated_state(store, simulated=True, intent={"cross_env_reproduced": True})
    ev = compute_evidence(store, "r1", OK_CONTRACT)
    assert (ev.level, ev.ceiling) == ("runnable", "runnable")
    assert "模拟执行" in ev.ceiling_reason


# ================= fork / 云端跳过:证据继承与进阶语义(FOLLOWUPS #11/#12) =================
#
# 本节两个守护测试(test_fork_reuse_audits_along_ancestor_chain /
# test_fork_skipped_stays_skipped_and_capped)由原
# test_fork_reuse_inherits_no_evidence_p1 / test_fork_keeps_skipped_steps_skipped
# 按设计后的新语义改写:原测试固定的是「缺口现状」(fork 零产物空洞过 L2、
# 全跳过 run 空洞爬 audited),语义升级后按新判定断言。

def _fork_scaffold():
    """fork 类测试的公共导入(保持与原守护测试相同的取材方式)。"""
    from tests.test_loop import empty_probe

    from insar_agent.core.stale import compute_step_hashes
    from insar_agent.planner.plan import fork_run
    return compute_step_hashes, fork_run, empty_probe()


def _mk_parent(store, cap, probe, compute_step_hashes, *, run_id="parent",
               fp="content:v1:" + "f" * 64, artifact=True, validations=True):
    """构造一个已执行完成的父 run(可选:带指纹产物 + 外部验证指标)。"""
    hashes = compute_step_hashes(cap, "m1", {}, [], probe.tool_versions())
    store.create_session("s1", "test")
    store.create_run(run_id, "s1", workspace="ws")
    store.create_step(run_id, cap.id, capability=str(cap.id), name=cap.name,
                      method="m1", params={}, hashes=hashes)
    store.advance(run_id, cap.id, "VERIFIED", state="done", run_ok=1)
    if artifact:
        store.record_artifact(run_id, cap.id, "qa_report", path="qa.json", kind="REPORT",
                              layout="", policy="content", fp=fp)
    if validations:
        for name, val in (("gnss_rmse_mm", 1.0), ("crossval_r", 0.92)):
            store.record_metric(run_id, name, value=val, source_artifact="qa.json",
                                source_field=name, reparsed_ok=True)
    return hashes


def test_fork_reuse_audits_along_ancestor_chain(store):
    """守护测试改写(#11 语义升级):fork 复用步骤沿祖先链核对,不再空洞过审。

    - 复用步骤 VERIFIED/run_ok=1(qa=reused_from_parent)照常继承 → L0/L1 通过
      (执行没变,结论可携带);
    - L2 不再对零产物空洞放行:产物记录沿 parent_run_id 可寻得且指纹一致 → 计入完整,
      step_sources 记 inherited(parent=xx);
    - 父 run 的 gnss/crossval 属外部验证,fork 后不自动继承(换参数须重新验证)
      → 停在 audited,但 parent_validations 列出「曾验证过什么 + 当时参数指纹」供人判断。
    """
    compute_step_hashes, fork_run, probe = _fork_scaffold()
    cap = _cap()
    hashes = _mk_parent(store, cap, probe, compute_step_hashes)
    assert compute_evidence(store, "parent", OK_CONTRACT).level == "validated"

    plan = fork_run(store, "parent", registry={cap.id: cap}, changes={}, probe=probe)
    fork_id = plan.run_id
    step = store.load_steps(fork_id)[0]
    assert step.state == "done" and step.run_ok == 1  # eval_hash 未变 → 复用生效
    assert step.qa and step.qa[0]["check"] == "reused_from_parent"
    assert store.find_artifact(fork_id, "qa_report") is not None  # 产物沿祖先链可解析

    ev = compute_evidence(store, fork_id, OK_CONTRACT)
    # fork run 自身仍零产物零指标,但 L2 现在有真实依据(祖先链核对通过)
    assert store.artifacts_of(fork_id) == [] and store.metrics_of(fork_id) == []
    assert ev.level == "audited" and ev.ceiling is None

    src = ev.step_sources[str(cap.id)]
    assert src["origin"] == "inherited" and src["parent_run_id"] == "parent"
    assert src["source"] == "inherited(parent=parent)"
    assert src["artifacts"] == {"qa_report": "content:v1:" + "f" * 64}

    # 父链验证清单:名称有序、指向父 run、附当时的参数指纹,且明确不继承
    assert [v["name"] for v in ev.parent_validations] == ["crossval_r", "gnss_rmse_mm"]
    for v in ev.parent_validations:
        assert v["run_id"] == "parent" and v["inherited"] is False
        assert v["args_hash"] == hashes["args_hash"]  # 验证发生时的参数指纹
    # 降级原因把「父链曾验证过」说给人听
    assert any("不自动继承" in r for r in ev.reasons)


def test_fork_skipped_stays_skipped_and_capped(store):
    """守护测试改写(#12 语义升级):skipped 步骤 fork 后仍 skipped;
    全跳过 + 零本地产物 + 无 manifest 的 run 封顶 checked,不再空洞爬 audited。"""
    compute_step_hashes, fork_run, probe = _fork_scaffold()
    cap = _cap()
    hashes = compute_step_hashes(cap, "m1", {}, [], probe.tool_versions())
    store.create_session("s1", "test")
    store.create_run("parent", "s1", workspace="ws")
    store.create_step("parent", cap.id, capability=str(cap.id), name=cap.name,
                      method="m1", params={}, hashes=hashes, state="skipped")

    plan = fork_run(store, "parent", registry={cap.id: cap}, changes={}, probe=probe)
    step = store.load_steps(plan.run_id)[0]
    assert step.state == "skipped" and step.run_ok is None and step.qa is None

    ev = compute_evidence(store, plan.run_id, OK_CONTRACT)  # 未提供 workspace → 无云端证据
    assert (ev.level, ev.ceiling) == ("checked", "checked")
    assert "hyp3_manifest" in ev.ceiling_reason  # 审计缺口写明
    assert "全部步骤云端跳过" in ev.ceiling_reason  # 零本地产物的run 不高于 checked
    src = ev.step_sources[str(cap.id)]
    assert src["origin"] == "missing" and "缺本地证据" in src["detail"]


def test_fork_two_level_chain_inherits_from_grandparent(store):
    """fork 链两级继承:孙代复用步骤的产物在祖代(中间代无产物记录),就近祖先解析。"""
    compute_step_hashes, fork_run, probe = _fork_scaffold()
    cap = _cap()
    _mk_parent(store, cap, probe, compute_step_hashes, run_id="gen0", validations=False)

    gen1 = fork_run(store, "gen0", registry={cap.id: cap}, changes={}, probe=probe).run_id
    gen2 = fork_run(store, gen1, registry={cap.id: cap}, changes={}, probe=probe).run_id
    assert store.artifacts_of(gen1) == []  # 中间代零产物记录,链核对须穿透它

    ev = compute_evidence(store, gen2, OK_CONTRACT)
    assert ev.level == "audited"
    src = ev.step_sources[str(cap.id)]
    assert src["origin"] == "inherited" and src["parent_run_id"] == "gen0"


def test_fork_ancestor_rerun_breaks_inheritance(store):
    """指纹不一致降级:fork 后祖先被改参(指纹变了),复用步骤的审计依据失效 → checked。"""
    compute_step_hashes, fork_run, probe = _fork_scaffold()
    cap = _cap()
    _mk_parent(store, cap, probe, compute_step_hashes, validations=False)
    fork_id = fork_run(store, "parent", registry={cap.id: cap}, changes={},
                       probe=probe).run_id
    assert compute_evidence(store, fork_id, OK_CONTRACT).level == "audited"

    # 父 run 改参重跑:步骤指纹与 fork 复用时不再一致(产物记录被覆写风险)
    new_hashes = compute_step_hashes(cap, "m1", {"threads": 8}, [], probe.tool_versions())
    store.set_step_config("parent", cap.id, method="m1", params={"threads": 8},
                          hashes=new_hashes)

    ev = compute_evidence(store, fork_id, OK_CONTRACT)
    assert (ev.level, ev.level_index) == ("checked", 1)
    assert any("fork 复用不可核对" in r for r in ev.reasons)
    src = ev.step_sources[str(cap.id)]
    assert src["origin"] == "missing" and "指纹不一致" in src["detail"]


def test_fork_ancestor_artifact_without_fp_blocks_audit(store):
    """祖先产物缺指纹:找得到记录但无 fp 可核 → 复用步骤不计入完整,停在 checked。"""
    compute_step_hashes, fork_run, probe = _fork_scaffold()
    cap = _cap()
    _mk_parent(store, cap, probe, compute_step_hashes, fp="", validations=False)
    fork_id = fork_run(store, "parent", registry={cap.id: cap}, changes={},
                       probe=probe).run_id
    ev = compute_evidence(store, fork_id, OK_CONTRACT)
    assert ev.level == "checked"
    src = ev.step_sources[str(cap.id)]
    assert src["origin"] == "missing" and "缺指纹" in src["detail"]


def test_fork_broken_chain_is_missing(store):
    """链断裂:parent_run_id 指向不存在的 run → 复用依据无从核对,停在 checked。"""
    store.create_session("s1", "test")
    store.create_run("orphan", "s1", workspace="ws", parent_run_id="ghost")
    store.create_step("orphan", 6, capability="6", name="步6", method="m",
                      params={}, hashes=_H)
    store.advance("orphan", 6, "VERIFIED", state="done", run_ok=1,
                  qa=[{"check": "reused_from_parent", "ok": True, "detail": "ghost"}])

    ev = compute_evidence(store, "orphan", OK_CONTRACT)
    assert ev.level == "checked"
    src = ev.step_sources["6"]
    assert src["origin"] == "missing" and "不存在" in src["detail"]


def test_fork_reused_zero_artifact_step_is_legal(store):
    """复用本就不登记产物的步骤:找到执行原点且指纹一致 → 与本地零产物步骤同等对待。"""
    compute_step_hashes, fork_run, probe = _fork_scaffold()
    cap = _cap()
    _mk_parent(store, cap, probe, compute_step_hashes, artifact=False, validations=False)
    fork_id = fork_run(store, "parent", registry={cap.id: cap}, changes={},
                       probe=probe).run_id
    ev = compute_evidence(store, fork_id, OK_CONTRACT)
    assert ev.level == "audited"  # 不因「零产物」误伤
    src = ev.step_sources[str(cap.id)]
    assert src["origin"] == "inherited" and src["artifacts"] == {}


def test_parent_validations_two_generations_nearest_wins(store):
    """父链验证清单跨两代收集:同名指标取最近祖先,来源 run 与参数指纹如实标注。"""
    compute_step_hashes, fork_run, probe = _fork_scaffold()
    cap = _cap()
    _mk_parent(store, cap, probe, compute_step_hashes, run_id="gen0")  # gnss=1.0 + crossval
    gen1 = fork_run(store, "gen0", registry={cap.id: cap}, changes={}, probe=probe).run_id
    # 中间代自己重做了 GNSS 验证(值不同)—— 孙代视角应看到最近的这次
    store.record_metric(gen1, "gnss_rmse_mm", value=2.0, source_artifact="qa.json",
                        source_field="gnss_rmse_mm", reparsed_ok=True)
    gen2 = fork_run(store, gen1, registry={cap.id: cap}, changes={}, probe=probe).run_id

    ev = compute_evidence(store, gen2, OK_CONTRACT)
    vals = {v["name"]: v for v in ev.parent_validations}
    assert set(vals) == {"gnss_rmse_mm", "crossval_r"}
    assert vals["gnss_rmse_mm"]["run_id"] == gen1 and vals["gnss_rmse_mm"]["value"] == 2.0
    assert vals["crossval_r"]["run_id"] == "gen0" and vals["crossval_r"]["value"] == 0.92
    # gen1 是复用 run,无产物记录可定位来源 → 参数指纹如实为空;gen0 可定位
    assert vals["gnss_rmse_mm"]["args_hash"] is None
    assert vals["crossval_r"]["args_hash"]


def test_fork_mixed_reuse_and_cloud(store, workspace):
    """混合 fork:一步复用(祖先链核对)+ 一步云端跳过(manifest 佐证)。"""
    import json as _json

    compute_step_hashes, fork_run, probe = _fork_scaffold()
    cap6, cap7 = _cap(), _cap(id=7, name="审计深测步7")
    _mk_parent(store, cap6, probe, compute_step_hashes, validations=False)
    h7 = compute_step_hashes(cap7, "m1", {}, [], probe.tool_versions())
    store.create_step("parent", 7, capability="7", name=cap7.name, method="m1",
                      params={}, hashes=h7, state="skipped")
    (workspace / "hyp3_manifest.json").write_text(
        _json.dumps([{"job_id": "j1"}]), encoding="utf-8")

    fork_id = fork_run(store, "parent", registry={6: cap6, 7: cap7}, changes={},
                       probe=probe).run_id
    ev = compute_evidence(store, fork_id, OK_CONTRACT, workspace=workspace)
    assert (ev.level, ev.ceiling) == ("audited", "audited")  # 云端封顶 audited
    assert ev.step_sources["6"]["origin"] == "inherited"
    assert ev.step_sources["7"]["origin"] == "cloud"


# ================= 云端跳过进阶梯(#12) =================

def _cloud_run(store, *, run_id="r1", skipped_ids=(5,), local_ids=(6,),
               validations=True):
    """构造混合 run:skipped_ids 云端跳过,local_ids 本地完成(带指纹产物+重解析指标)。"""
    _mk_run(store, run_id)
    for sid in skipped_ids:
        _add_step(store, run_id, sid, skipped=True)
    for sid in local_ids:
        _add_step(store, run_id, sid)
        store.record_artifact(run_id, sid, f"art{sid}", path=f"products/a{sid}.json",
                              kind="REPORT", layout="", policy="content",
                              fp="content:v1:" + "f" * 64)
    if local_ids:
        store.record_metric(run_id, "unwrap_coverage", value=0.94,
                            source_artifact=f"a{local_ids[0]}.json",
                            source_field="unwrap_coverage", reparsed_ok=True)
        if validations:
            for name, val in (("gnss_rmse_mm", 1.0), ("crossval_r", 0.92)):
                store.record_metric(run_id, name, value=val,
                                    source_artifact=f"a{local_ids[0]}.json",
                                    source_field=name, reparsed_ok=True)


def test_cloud_skip_with_manifest_caps_at_audited(store, workspace):
    """混合 run + 可核 manifest:跳过步骤计入完整,但整 run 封顶 audited
    (云端过程不可本地复核,calibrated/validated 的外部验证须本地重新做)。"""
    import hashlib as _hashlib
    import json as _json

    _cloud_run(store)  # 本地部分满足到 validated 的全部条件
    manifest = workspace / "hyp3_manifest.json"
    manifest.write_text(_json.dumps([{"job_id": "j1"}, {"job_id": "j2"}]),
                        encoding="utf-8")

    ev = compute_evidence(store, "r1", OK_CONTRACT, workspace=workspace)
    assert (ev.level, ev.ceiling) == ("audited", "audited")
    assert "不可本地复核" in ev.ceiling_reason
    assert any("封顶 audited" in r for r in ev.reasons)  # 若无云端封顶本可到 validated

    sha = _hashlib.sha256(manifest.read_bytes()).hexdigest()
    src = ev.step_sources["5"]
    assert src["origin"] == "cloud" and src["manifest_sha256"] == sha
    assert src["source"] == f"cloud(manifest sha256:{sha[:12]})"
    assert ev.step_sources["6"] == {"origin": "local", "source": "local"}

    # 账本 evidence 段透出同一份判定依据(#12 连带 #3:ledger 同步)
    from insar_agent.core.ledger import export_provenance

    doc = export_provenance(store, "r1", contract=OK_CONTRACT, workspace=workspace)
    assert doc["evidence"]["step_sources"]["5"]["origin"] == "cloud"
    assert doc["evidence"]["step_sources"]["6"]["origin"] == "local"


def test_cloud_skip_without_manifest_caps_at_checked(store, workspace):
    """混合 run + 无 manifest:云端完成声明无凭据 → 审计缺口,封顶 checked。"""
    _cloud_run(store)
    ev = compute_evidence(store, "r1", OK_CONTRACT, workspace=workspace)
    assert (ev.level, ev.ceiling) == ("checked", "checked")
    assert "审计缺口" in ev.ceiling_reason
    src = ev.step_sources["5"]
    assert src["origin"] == "missing" and "缺本地证据" in src["detail"]


def test_all_skipped_run_never_above_checked_even_with_manifest(store, workspace):
    """全跳过 + 零本地产物:即便 manifest 可核,也不得高于 checked
    (manifest 单独不构成审计完整;per-step 来源仍如实记 cloud)。"""
    import json as _json

    _cloud_run(store, skipped_ids=(5, 6), local_ids=())
    (workspace / "hyp3_manifest.json").write_text(_json.dumps([{"job_id": "j1"}]),
                                                  encoding="utf-8")
    ev = compute_evidence(store, "r1", OK_CONTRACT, workspace=workspace)
    assert (ev.level, ev.ceiling) == ("checked", "checked")
    assert "全部步骤云端跳过" in ev.ceiling_reason
    assert ev.step_sources["5"]["origin"] == "cloud"  # 步级证据与 run 级封顶各说各话
    assert ev.step_sources["6"]["origin"] == "cloud"


def test_cloud_manifest_parse_error_not_credited(store, workspace):
    """manifest 存在但不可解析:sha256 虽可算,不构成云端完成的证据 → 同缺失处理。"""
    _cloud_run(store)
    (workspace / "hyp3_manifest.json").write_text("{不是 json", encoding="utf-8")
    ev = compute_evidence(store, "r1", OK_CONTRACT, workspace=workspace)
    assert (ev.level, ev.ceiling) == ("checked", "checked")
    src = ev.step_sources["5"]
    assert src["origin"] == "missing" and "无法解析" in src["detail"]


def test_step_sources_local_run_shape(store):
    """普通本地 run:step_sources 全为 local,parent_validations 为空,
    且 to_dict() 输出账本 evidence 段的新字段(#3 字段契约)。"""
    _audited_state(store)
    ev = compute_evidence(store, "r1", OK_CONTRACT)
    assert ev.step_sources == {"6": {"origin": "local", "source": "local"}}
    assert ev.parent_validations == []
    d = ev.to_dict()
    assert set(d) == {"level", "level_index", "ladder", "reasons", "ceiling",
                      "ceiling_reason", "step_sources", "parent_validations"}
