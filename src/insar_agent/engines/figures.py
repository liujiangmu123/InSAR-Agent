"""真实出图(step 10 · figure_journal):velocity.h5 → PNG(matplotlib Agg)。

脚本在引擎环境的 Python 里跑(h5py/matplotlib 由 mintpy 依赖带入);
引擎环境未配置时回退宿主 Python(宿主须有 h5py+matplotlib,否则显式失败)。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from insar_agent.engines.mintpy import engine_python
from insar_agent.registry.model import Capability
from insar_agent.runtime.jobs import CommandPlan

_FIGURE_PY = '''\
# insar-agent 真实出图脚本(数据不造假:直接读 velocity.h5)
import sys
from pathlib import Path

import h5py
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

WS = Path(".").resolve()
DPI = {dpi}
CMAP = {cmap!r}

vel_path = next((p for p in [WS / "mintpy/velocity.h5", WS / "velocity.h5"] if p.exists()), None)
if vel_path is None:
    print("ERROR: velocity.h5 不存在", flush=True)
    sys.exit(2)

with h5py.File(vel_path, "r") as f:
    vel = f["velocity"][:] * 1000.0  # m/yr -> mm/yr
    print(f"velocity 栅格: {{vel.shape}}, 有效像元 {{np.isfinite(vel).sum()}}", flush=True)

out_dir = WS / "products" / "figures"
out_dir.mkdir(parents=True, exist_ok=True)

finite = vel[np.isfinite(vel)]
vmin, vmax = np.percentile(finite, [2, 98])
lim = max(abs(vmin), abs(vmax))
fig, ax = plt.subplots(figsize=(10, 8))
im = ax.imshow(vel, cmap=CMAP, vmin=-lim, vmax=lim, interpolation="nearest")
fig.colorbar(im, ax=ax, label="LOS velocity (mm/yr)", shrink=0.8)
ax.set_title("LOS velocity (insar-agent)")
fig.savefig(out_dir / "velocity.png", dpi=DPI, bbox_inches="tight")
print(f"✓ {{out_dir / 'velocity.png'}} ({{DPI}} dpi)", flush=True)

# 速度直方图(质检辅助)
fig2, ax2 = plt.subplots(figsize=(6, 4))
ax2.hist(finite, bins=100)
ax2.set_xlabel("LOS velocity (mm/yr)")
ax2.set_ylabel("count")
fig2.savefig(out_dir / "velocity_hist.png", dpi=150, bbox_inches="tight")
print("✓ velocity_hist.png", flush=True)
print("出图完成", flush=True)
'''


def build(*, cap: Capability, method: str, params: dict[str, Any], run: dict,
          workspace: Path) -> CommandPlan:
    script_rel = ".report/make_figures.py"
    content = _FIGURE_PY.format(
        dpi=int(params.get("dpi", 600)),
        cmap={"roma": "RdBu_r"}.get(str(params.get("cmap", "roma")), str(params.get("cmap", "RdBu_r"))),
    )
    return CommandPlan(
        argv=[engine_python(), "-u", script_rel],
        cwd=str(workspace),
        env={"PYTHONIOENCODING": "utf-8", "HDF5_USE_FILE_LOCKING": "FALSE"},
        files={script_rel: content},
        shell_line=f"python {script_rel}",
    )
