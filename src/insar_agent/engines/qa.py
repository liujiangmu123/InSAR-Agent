"""真实质检(step 11 · coherence_mask 路线):从产物算真指标,绝不编造。

指标(全部可由 verify.py 重解析):
    velocity_coverage    速度场有效像元占比
    nan_fraction         NaN 占比
    mean_coherence       平均空间相干性(avgSpatialCoh.h5,invert_network 副产物)
    vel_p2 / vel_p98     速度 2/98 分位(mm/yr)
    residual_rms_mm      时序残差 RMS(若 rms_timeseriesResidual 存在)

crossval_r(PS/SBAS 交叉验证)在 PS 链建成前**不写入** ——
质量门引用的 corr_threshold 是 PENDING,缺指标只产生 warning,证据阶梯如实反映。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from insar_agent.engines.mintpy import engine_python
from insar_agent.registry.model import Capability
from insar_agent.runtime.jobs import CommandPlan

_QA_PY = '''\
# insar-agent 真实质检脚本:指标全部来自产物重解析,绝不编造
import json, sys
from pathlib import Path

import h5py
import numpy as np

WS = Path(".").resolve()
qa: dict = {"simulated": False, "method": "coherence_mask"}

vel_path = next((p for p in [WS / "mintpy/velocity.h5", WS / "velocity.h5"] if p.exists()), None)
if vel_path is None:
    print("ERROR: velocity.h5 不存在", flush=True)
    sys.exit(2)
with h5py.File(vel_path, "r") as f:
    vel = f["velocity"][:] * 1000.0
total = vel.size
finite = np.isfinite(vel)
qa["velocity_coverage"] = round(float(finite.sum()) / total, 4)
qa["nan_fraction"] = round(1.0 - qa["velocity_coverage"], 4)
vals = vel[finite]
qa["vel_p2"] = round(float(np.percentile(vals, 2)), 2)
qa["vel_p98"] = round(float(np.percentile(vals, 98)), 2)
print(f"velocity: 覆盖 {qa['velocity_coverage']:.1%} · p2={qa['vel_p2']} p98={qa['vel_p98']} mm/yr",
      flush=True)

coh_path = WS / "mintpy/avgSpatialCoh.h5"
if coh_path.exists():
    with h5py.File(coh_path, "r") as f:
        coh = f["coherence"][:]
    qa["mean_coherence"] = round(float(np.nanmean(coh)), 4)
    print(f"平均空间相干性: {qa['mean_coherence']}", flush=True)
else:
    print("avgSpatialCoh.h5 不存在,跳过相干性指标", flush=True)

rms_files = sorted((WS / "mintpy").glob("rms_timeseriesResidual*.txt"))
if rms_files:
    try:
        rows = [l.split() for l in rms_files[0].read_text().splitlines()
                if l.strip() and not l.startswith(("#", "date"))]
        rms = [float(r[1]) for r in rows if len(r) >= 2]
        if rms:
            qa["residual_rms_mm"] = round(sum(rms) / len(rms) * 1000.0, 2)
            print(f"时序残差 RMS: {qa['residual_rms_mm']} mm", flush=True)
    except (ValueError, IndexError):
        print("residual RMS 解析失败,跳过", flush=True)

print("注:crossval_r 缺失 —— PS 链未建成,交叉验证不可用(诚实缺席,阈值 PENDING 仅警告)",
      flush=True)

out = WS / "products" / "report" / "qa.json"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(qa, indent=1, ensure_ascii=False), encoding="utf-8")
print(f"✓ {out}", flush=True)
'''


def build(*, cap: Capability, method: str, params: dict[str, Any], run: dict,
          workspace: Path) -> CommandPlan:
    script_rel = ".report/run_qa.py"
    return CommandPlan(
        argv=[engine_python(), "-u", script_rel],
        cwd=str(workspace),
        env={"PYTHONIOENCODING": "utf-8", "HDF5_USE_FILE_LOCKING": "FALSE"},
        files={script_rel: _QA_PY},
        shell_line=f"python {script_rel}",
    )
