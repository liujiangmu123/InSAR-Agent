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


# ================= fork:证据继承语义现状(P1 守护) =================

def test_fork_reuse_inherits_no_evidence_p1(store):
    """fork 复用步骤的证据继承现状(P1 缺口的守护测试,行为变更须显式过此测试):

    - 复用步骤在 fork run 里 VERIFIED/run_ok=1(qa=reused_from_parent)→ L0/L1 通过;
    - 但产物/指标不随 fork 复制:L2「审计完整」对零产物零指标空洞通过(过宽),
      父 run 的 gnss/crossval 指标不继承 → 永远停在 audited(过严)。
    两个方向都不是设计后的语义 —— 修复须显式设计继承规则(见报告 P1)。
    """
    from insar_agent.core.stale import compute_step_hashes
    from insar_agent.planner.plan import fork_run
    from tests.test_loop import empty_probe

    cap = _cap()
    probe = empty_probe()
    hashes = compute_step_hashes(cap, "m1", {}, [], probe.tool_versions())
    store.create_session("s1", "test")
    store.create_run("parent", "s1", workspace="ws")
    store.create_step("parent", cap.id, capability=str(cap.id), name=cap.name,
                      method="m1", params={}, hashes=hashes)
    store.advance("parent", cap.id, "VERIFIED", state="done", run_ok=1)
    store.record_artifact("parent", cap.id, "qa_report", path="qa.json", kind="REPORT",
                          layout="", policy="content", fp="content:v1:" + "f" * 64)
    for name, val in (("gnss_rmse_mm", 1.0), ("crossval_r", 0.92)):
        store.record_metric("parent", name, value=val, source_artifact="qa.json",
                            source_field=name, reparsed_ok=True)
    assert compute_evidence(store, "parent", OK_CONTRACT).level == "validated"

    plan = fork_run(store, "parent", registry={cap.id: cap}, changes={}, probe=probe)
    fork_id = plan.run_id
    step = store.load_steps(fork_id)[0]
    assert step.state == "done" and step.run_ok == 1  # eval_hash 未变 → 复用生效
    assert step.qa and step.qa[0]["check"] == "reused_from_parent"
    assert store.find_artifact(fork_id, "qa_report") is not None  # 产物沿祖先链可解析

    ev = compute_evidence(store, fork_id, OK_CONTRACT)
    # 现状:fork run 自身零产物零指标,L2 空洞通过、父指标不继承 → audited
    assert store.artifacts_of(fork_id) == [] and store.metrics_of(fork_id) == []
    assert ev.level == "audited"


def test_fork_keeps_skipped_steps_skipped(store):
    """云端已完成(skipped)的步骤 fork 后仍是 skipped;全跳过 run 的证据级现状。"""
    from insar_agent.core.stale import compute_step_hashes
    from insar_agent.planner.plan import fork_run
    from tests.test_loop import empty_probe

    cap = _cap()
    probe = empty_probe()
    hashes = compute_step_hashes(cap, "m1", {}, [], probe.tool_versions())
    store.create_session("s1", "test")
    store.create_run("parent", "s1", workspace="ws")
    store.create_step("parent", cap.id, capability=str(cap.id), name=cap.name,
                      method="m1", params={}, hashes=hashes, state="skipped")

    plan = fork_run(store, "parent", registry={cap.id: cap}, changes={}, probe=probe)
    step = store.load_steps(plan.run_id)[0]
    assert step.state == "skipped" and step.run_ok is None and step.qa is None

    # 现状:全跳过 run 无任何本地证据,也能空洞爬到 audited(cloud 证据不进阶梯,见报告)
    ev = compute_evidence(store, plan.run_id, OK_CONTRACT)
    assert ev.level == "audited"
