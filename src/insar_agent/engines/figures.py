"""真实出图(step 10 · figure_journal):velocity.h5 → 论文级 PNG + PDF。

脚本在引擎环境的 Python 里跑(h5py/matplotlib 由 mintpy 依赖带入);
引擎环境未配置时回退宿主 Python(宿主须有 h5py+matplotlib,否则显式失败)。

出版规格依据 reference/RESEARCH-pub-figures-2026-08-12.md(四刊安全交集):
物理尺寸 figsize、Type 42 嵌字、Crameri 色标(缺 cmcrameri 退 RdBu_r 并记录
在案)、参考点/LOS 箭头/比例尺等 InSAR 领域惯例、图角 provenance 小字。
模板用 __TOKEN__ 替换而非 str.format:生成脚本里大量 f-string/字典花括号,
format 转义极易出错。

产物目录约定(products/figures/,HyP3 式三档尺寸 + 元数据 sidecar,
依据 RESEARCH-insar-viewer-ux-2026-08-12.md §4.6):
  <name>.png          原图(dpi=params.dpi,默认 600)——「查看原图」/报告用
  <name>.pdf          矢量原稿(投稿用,不进影像面板)
  <name>_browse.png   2048px 定宽浏览档 —— /api/figures 条目 url 优先取它(灯箱)
  <name>_thumb.png    320px 定宽缩略档 —— 画廊网格用(条目 thumbUrl)
  <name>.json         元数据 sidecar(title/units/cmap/vlim/date_range/
                      ref_point/step/params)—— /api/figures 读到即并入 meta 字段
三档由同一 Figure 只改 dpi 连续 savefig 得到:版式/字号与原图严格一致
(RESEARCH-raster-viewer-tech §1.10);_browse/_thumb 不单独入 artifacts 表,
由 /api/figures 枚举目录时按命名约定归并到基图条目。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from insar_agent.engines.mintpy import engine_python
from insar_agent.registry.model import Capability
from insar_agent.runtime.jobs import CommandPlan

_FIGURE_PY = '''\
# insar-agent 论文级出图脚本(数据不造假:直接读 velocity.h5)
import json
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
STEP_ID = 10          # 流水线步骤号(出图导出)
PARAMS = __PARAMS__   # 本次出图参数摘要(build() 注入),原样写进 sidecar


def save_tiers(fig_obj, base_png):
    """三档尺寸契约(HyP3 式):原图旁另存 _browse(2048px 定宽)与 _thumb(320px 定宽)。

    同一 Figure 只改 dpi 二次渲染,版式/字号与原图严格一致;
    不用 bbox_inches="tight",保证像素宽度可预知(卷帘对比也要求像素对齐)。
    """
    w_in = float(fig_obj.get_figwidth())
    fig_obj.savefig(base_png.with_name(base_png.stem + "_browse.png"), dpi=2048 / w_in)
    fig_obj.savefig(base_png.with_name(base_png.stem + "_thumb.png"), dpi=320 / w_in)


def write_sidecar(base_png, fields):
    """图件元数据 sidecar(<name>.json):字段全部取自脚本内已有变量,不造假。"""
    base_png.with_suffix(".json").write_text(
        json.dumps(fields, ensure_ascii=False, indent=1), encoding="utf-8")

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
if finite.size == 0:
    # 全 NaN 自守(REVIEW-r2 P2-14):上游 not_all_nan 校验能拦,但脚本独立跑
    # (复现/调试)时也绝不产出空图假产物 —— 与 velocity.h5 缺失同一失败口径
    print("ERROR: velocity 全 NaN(0 个有效像元),拒绝出图", flush=True)
    sys.exit(2)
lim = float(np.percentile(np.abs(finite), 98)) or 1.0  # 对称限幅零点居中;全零场兜底 1.0
# 默认色带按产物类型路由(C8,Crameri 2020 三分类:循环量配非循环色带会在 ±π 处
# 产生假边界):速度=vik(diverging)、相干=batlow(sequential)、缠绕相位=romaO(cyclic);
# 显式指定 cmap 时(AUTO=False)直通用户值不路由。产物类型从 meta(h5 FILE_TYPE)判断
_CMAP_ROUTE = {"velocity": "vik", "coherence": "batlow", "temporalCoherence": "batlow",
               "wrapPhase": "romaO"}
_pick = _CMAP_ROUTE.get(atr.get("FILE_TYPE", "velocity"), "vik") if __AUTO_CMAP__ else __CMAP__
cmap, cmap_name = pub_cmap(_pick)

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
if geo:
    # 纵横校正(REVIEW-r2 P2-14):1°lat≈111.32 km 恒定,1°lon=111.32·cos(lat) km;
    # imshow 默认 aspect="equal"(1°=1°)会把中纬度南北向视觉拉伸 ~1/cos(lat)。
    # 校正后与下方比例尺的 cos(lat) 经度换算自洽,南北向长度亦真。
    mid_lat = 0.5 * (extent[2] + extent[3])
    ax.set_aspect(1.0 / max(np.cos(np.radians(mid_lat)), 0.05))
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
vel_png = out_dir / "velocity.png"
fig.savefig(vel_png, metadata=meta)                               # 600dpi 栅格:预览/报告
fig.savefig(out_dir / "velocity.pdf", metadata={"Title": meta["Title"]})  # 矢量:投稿
save_tiers(fig, vel_png)                                          # browse/thumb 两档
write_sidecar(vel_png, {                                          # 元数据随图落盘
    "title": meta["Title"],
    "units": "mm/yr",
    "cmap": cmap_name,                                            # 实际所用(含兜底)
    "vlim": [round(-lim, 2), round(lim, 2)],
    "date_range": [atr.get("START_DATE"), atr.get("END_DATE")],
    "ref_point": ([float(atr["REF_LAT"]), float(atr["REF_LON"])]
                  if "REF_LAT" in atr and "REF_LON" in atr else None),
    "step": STEP_ID,
    "params": PARAMS,
})
print(f"OK velocity.png|pdf|_browse|_thumb|json  cmap={cmap_name}  vlim=+-{lim:.1f} mm/yr",
      flush=True)

# 速度直方图(质检辅助,非投稿图件,保持简装)
fig2, ax2 = plt.subplots(figsize=(6, 4))
ax2.hist(finite, bins=100)
ax2.set_xlabel("LOS velocity (mm/yr)")
ax2.set_ylabel("count")
hist_png = out_dir / "velocity_hist.png"
fig2.savefig(hist_png, dpi=150, bbox_inches="tight")
save_tiers(fig2, hist_png)
write_sidecar(hist_png, {  # 直方图无色标/值域语义,只写确有依据的字段
    "title": "LOS velocity histogram",
    "units": "mm/yr",
    "step": STEP_ID,
    "params": PARAMS,
})
print("OK velocity_hist.png|_browse|_thumb|json", flush=True)
print("出图完成", flush=True)
'''


def build(*, cap: Capability, method: str, params: dict[str, Any], run: dict,
          workspace: Path) -> CommandPlan:
    script_rel = ".report/make_figures.py"
    cmap = str(params.get("cmap", "roma"))
    # 注册表默认 roma 视作「未显式指定」哨兵(C8):脚本内按产物类型路由默认色带,
    # velocity 主图仍落 vik(发散、CVD 安全,RESEARCH-pub-figures §二,旧语义不变);
    # 显式指定其他值直通 pub_cmap(cmcrameri 名字空间),缺包时脚本内退 RdBu_r 并记录在案
    cmap_auto = cmap == "roma"
    cmap = "vik" if cmap_auto else cmap
    dpi = int(params.get("dpi", 600))
    # sidecar 的 params 摘要:只放本步骤声明过的展示参数(repr 注入为 Python 字面量)
    params_summary = {"dpi": dpi, "cmap": cmap,
                      "format": str(params.get("format", "png+pdf"))}
    content = (_FIGURE_PY
               .replace("__DPI__", str(dpi))
               .replace("__AUTO_CMAP__", repr(cmap_auto))
               .replace("__CMAP__", repr(cmap))
               .replace("__PARAMS__", repr(params_summary)))
    return CommandPlan(
        argv=[engine_python(), "-u", script_rel],
        cwd=str(workspace),
        env={"PYTHONIOENCODING": "utf-8", "HDF5_USE_FILE_LOCKING": "FALSE"},
        files={script_rel: content},
        shell_line=f"python {script_rel}",
    )
