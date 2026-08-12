"""真实出图(step 10 · figure_journal):velocity.h5 → 论文级 PNG + PDF。

脚本在引擎环境的 Python 里跑(h5py/matplotlib 由 mintpy 依赖带入);
引擎环境未配置时回退宿主 Python(宿主须有 h5py+matplotlib,否则显式失败)。

出版规格依据 reference/RESEARCH-pub-figures-2026-08-12.md(四刊安全交集):
物理尺寸 figsize、Type 42 嵌字、Crameri 色标(缺 cmcrameri 退 RdBu_r 并记录
在案)、参考点/LOS 箭头/比例尺等 InSAR 领域惯例、图角 provenance 小字。
模板用 __TOKEN__ 替换而非 str.format:生成脚本里大量 f-string/字典花括号,
format 转义极易出错。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from insar_agent.engines.mintpy import engine_python
from insar_agent.registry.model import Capability
from insar_agent.runtime.jobs import CommandPlan

_FIGURE_PY = '''\
# insar-agent 论文级出图脚本(数据不造假:直接读 velocity.h5)
import sys
from datetime import datetime, timezone
from pathlib import Path

import h5py
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LightSource

WS = Path(".").resolve()
MM = 1 / 25.4  # mm -> inch:按最终物理尺寸设计,所见即所得
PUB_RC = {  # 四刊安全交集:无衬线、正文 7-8pt、线宽 0.6pt、Type42 嵌字
    "font.family": "sans-serif",
    "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans"],
    "font.size": 8, "axes.labelsize": 8,
    "xtick.labelsize": 7, "ytick.labelsize": 7,
    "axes.linewidth": 0.6, "xtick.major.width": 0.6, "ytick.major.width": 0.6,
    "pdf.fonttype": 42, "ps.fonttype": 42, "svg.fonttype": "none",
    "mathtext.fontset": "dejavusans",
    "figure.constrained_layout.use": True,
    "savefig.dpi": __DPI__,
}


def pub_cmap(name):
    """Crameri 科学色标;缺 cmcrameri 时退 RdBu_r(CVD 安全兜底),回报实际所用名。"""
    try:
        import cmcrameri.cm as cmc
        return getattr(cmc, name), "cmc." + name
    except (ImportError, AttributeError):
        return plt.get_cmap("RdBu_r"), "RdBu_r"


plt.rcParams.update(PUB_RC)
vel_path = next((p for p in (WS / "mintpy/velocity.h5", WS / "velocity.h5") if p.exists()), None)
if vel_path is None:
    print("ERROR: velocity.h5 不存在", flush=True)
    sys.exit(2)

with h5py.File(vel_path, "r") as f:
    vel = f["velocity"][:] * 1000.0  # m/yr -> mm/yr
    atr = {k: str(v) for k, v in f.attrs.items()}
print(f"velocity 栅格: {vel.shape}, 有效像元 {int(np.isfinite(vel).sum())}", flush=True)

nrow, ncol = vel.shape
geo = all(k in atr for k in ("X_FIRST", "Y_FIRST", "X_STEP", "Y_STEP"))
if geo:  # MintPy 地理编码属性 → imshow extent(等经纬度网格,无需 cartopy)
    x0, y0, dx, dy = (float(atr[k]) for k in ("X_FIRST", "Y_FIRST", "X_STEP", "Y_STEP"))
    extent = (x0, x0 + dx * ncol, y0 + dy * nrow, y0)  # W, E, S, N(dy < 0)
else:
    extent = None

finite = vel[np.isfinite(vel)]
lim = float(np.percentile(np.abs(finite), 98)) if finite.size else 1.0  # 对称限幅:零点居中
cmap, cmap_name = pub_cmap(__CMAP__)

fig, ax = plt.subplots(figsize=(140 * MM, 105 * MM))  # 1.5 栏物理尺寸

dem_path = vel_path.parent / "inputs/geometryGeo.h5"
if geo and dem_path.exists():  # DEM 山影底图(可选,零新增依赖)
    with h5py.File(dem_path, "r") as g:
        if "height" in g:
            dem = np.nan_to_num(g["height"][:])
            shade = LightSource(azdeg=315, altdeg=45).hillshade(dem, vert_exag=1.0)
            ax.imshow(shade, cmap="gray", extent=extent, zorder=0)

im = ax.imshow(vel, cmap=cmap, vmin=-lim, vmax=lim, extent=extent,
               interpolation="nearest", zorder=1)
cbar = fig.colorbar(im, ax=ax, shrink=0.75, pad=0.02, extend="both")
cbar.set_label("LOS velocity (mm/yr)\\npositive = motion toward satellite")
cbar.outline.set_linewidth(0.6)

if geo and "REF_LON" in atr and "REF_LAT" in atr:  # 参考点黑方块(领域惯例)
    rlon, rlat = float(atr["REF_LON"]), float(atr["REF_LAT"])
    ax.plot(rlon, rlat, "ks", ms=4, mec="w", mew=0.5, zorder=3)
    ax.annotate("Ref", (rlon, rlat), xytext=(3, 3), textcoords="offset points", fontsize=6)

if "HEADING" in atr:  # 飞行方向 + LOS 地面投影箭头(右视 = heading + 90°)
    th = float(np.radians(float(atr["HEADING"])))
    ax0, ay0, L = 0.92, 0.88, 0.07  # 轴分数坐标锚点与箭长
    ax.annotate("", xytext=(ax0, ay0), xycoords="axes fraction",
                xy=(ax0 + L * np.sin(th), ay0 + L * np.cos(th)),
                arrowprops=dict(arrowstyle="-|>", lw=1.0, color="k"))
    ax.annotate("LOS", xytext=(ax0, ay0), xycoords="axes fraction", fontsize=6,
                xy=(ax0 + 0.6 * L * np.cos(th), ay0 - 0.6 * L * np.sin(th)),
                arrowprops=dict(arrowstyle="-|>", lw=0.8, color="k"))

if geo:  # 零依赖比例尺:1 度经度 = 111.32*cos(lat) km
    km_deg = 111.32 * np.cos(np.radians(0.5 * (extent[2] + extent[3])))
    bar_km = max(round(abs(extent[1] - extent[0]) * km_deg / 5), 1)
    xb = extent[0] + 0.06 * (extent[1] - extent[0])
    yb = extent[2] + 0.06 * (extent[3] - extent[2])
    ax.plot([xb, xb + bar_km / km_deg], [yb, yb], "k-", lw=1.5, solid_capstyle="butt")
    ax.text(xb + 0.5 * bar_km / km_deg, yb, f"{bar_km} km",
            ha="center", va="bottom", fontsize=6)
    ax.set_xlabel("Longitude (deg E)")
    ax.set_ylabel("Latitude (deg N)")
# AGU 惯例:图内不放标题(标题属 caption),故不再 set_title

# provenance:图角小字(投稿版可删)+ 文件元数据(零版面成本)
stamp = (f"{atr.get('PLATFORM', 'SAR')} {atr.get('ORBIT_DIRECTION', '')} | "
         f"{atr.get('START_DATE', '?')}-{atr.get('END_DATE', '?')} | "
         f"cmap:{cmap_name} | {datetime.now(timezone.utc):%Y-%m-%dT%H:%MZ}")
fig.text(0.99, 0.01, stamp, ha="right", va="bottom", fontsize=5, color="0.45")
meta = {"Title": "InSAR LOS velocity", "Software": "insar-agent",
        "Description": stamp + f" | ref=({atr.get('REF_LAT')},{atr.get('REF_LON')})"}

out_dir = WS / "products" / "figures"
out_dir.mkdir(parents=True, exist_ok=True)
fig.savefig(out_dir / "velocity.png", metadata=meta)              # 600dpi 栅格:预览/报告
fig.savefig(out_dir / "velocity.pdf", metadata={"Title": meta["Title"]})  # 矢量:投稿
print(f"OK velocity.png|pdf  cmap={cmap_name}  vlim=+-{lim:.1f} mm/yr", flush=True)

# 速度直方图(质检辅助,非投稿图件,保持简装)
fig2, ax2 = plt.subplots(figsize=(6, 4))
ax2.hist(finite, bins=100)
ax2.set_xlabel("LOS velocity (mm/yr)")
ax2.set_ylabel("count")
fig2.savefig(out_dir / "velocity_hist.png", dpi=150, bbox_inches="tight")
print("OK velocity_hist.png", flush=True)
print("出图完成", flush=True)
'''


def build(*, cap: Capability, method: str, params: dict[str, Any], run: dict,
          workspace: Path) -> CommandPlan:
    script_rel = ".report/make_figures.py"
    cmap = str(params.get("cmap", "roma"))
    # 旧默认 roma 升级为速率图推荐色标 vik(发散、CVD 安全,RESEARCH-pub-figures §二);
    # 其余值直通 pub_cmap(cmcrameri 名字空间),缺包时脚本内退 RdBu_r 并记录在案
    cmap = {"roma": "vik"}.get(cmap, cmap)
    content = (_FIGURE_PY
               .replace("__DPI__", str(int(params.get("dpi", 600))))
               .replace("__CMAP__", repr(cmap)))
    return CommandPlan(
        argv=[engine_python(), "-u", script_rel],
        cwd=str(workspace),
        env={"PYTHONIOENCODING": "utf-8", "HDF5_USE_FILE_LOCKING": "FALSE"},
        files={script_rel: content},
        shell_line=f"python {script_rel}",
    )
