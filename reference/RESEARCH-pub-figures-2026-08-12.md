# 出版级科学图件规范调研 —— 面向 `engines/figures.py`

- 日期:2026-08-12
- 任务:整理期刊图件硬性规格、科学配色、InSAR 领域惯例、matplotlib 落地方法与图件可追溯性做法,产出 `figures.py` 改造清单与论文级速率图参考实现。
- 现状基线:`src/insar_agent/engines/figures.py` 当前为单图 `imshow` + `RdBu_r` 回退(`"roma"→"RdBu_r"` 映射)、像素坐标轴、无参考点/比例尺/LOS 箭头/山影/provenance,输出仅 PNG。已做对的一件事:按 2–98 百分位取对称限幅(发散色标零点居中)。

---

## 一、期刊硬性要求

### 1.1 四刊规格对照

| 项目 | AGU(JGR/GRL) | Nature 系 | Science | RSE(Elsevier) |
|---|---|---|---|---|
| 单栏宽 | ~95 mm(quarter page) | 89 mm | 3.5 in ≈ 90 mm(另一官方页:5.7 cm) | 90 mm(最小 30 mm) |
| 1.5 栏 | — | 120–136 mm | 5.0 in ≈ 127 mm | 140 mm |
| 双栏/全宽 | 190 mm(整页 190×230 mm) | 183 mm(页深 247 mm) | 7.3 in ≈ 184 mm | 190 mm |
| 字号 | 无硬性;实践建议 ≥6 pt | 正文 5–7 pt;面板标签 8 pt 粗体小写 a/b/c | ≥5 pt,缩印后约 7 pt(2.5 mm);同图字号跨度 ≤4 档 | 正文 7 pt,上下标 ≥6 pt |
| 线宽 | 建议 ≥0.5 pt | 0.25–1 pt(<0.25 pt 印刷可能消失) | ≥0.5 pt;符号 ≥6 pt | 建议 ≥0.25 pt(绝对下限 0.1 pt) |
| 分辨率 | 图像 ≥300 dpi,线稿 600 dpi | 彩色 300 / 灰度 600 / 线稿 1200 dpi | 初投 300;修回 300–600,线稿 1200 dpi | 半色调 300 / 图文混合(combination art)500 / 线稿 1000 dpi |
| 字体 | 无硬性(无衬线为惯例) | 无衬线,Helvetica/Arial 优先,全文统一 | 无衬线,Myriad 首选、Helvetica 次之 | 无硬性,7 pt 可读为准 |
| 格式 | JPG/TIFF/EPS/PS/PDF;子图必须合成单文件;图内不得含 caption 或 "Figure 1" 字样;**地图必须标注经纬度** | 矢量优先(AI/EPS/PDF),文本不得转曲;Extended Data 仅收 JPEG/TIFF/EPS 且 ≤10 MB | 矢量 PDF/EPS/AI 优先;修回栅格 TIFF ≥300 dpi;不收 PowerPoint | TIFF(栅格)/EPS(矢量)首选,PDF/JPEG 可;**明确不收 PNG** |

出处:
- AGU 图文要求:https://www.agu.org/publications/authors/journals/text-graphics-requirements ;Wiley 图件准备(300/600 dpi、小写子图标签、地图标经纬度):https://agupubs.onlinelibrary.wiley.com/hub/books-manuscript-preparation ;图像指南 PDF:https://www.agu.org/-/media/files/publications/author_image_guidance_final.pdf ;JGR 190×230 mm 版面(社区 matplotlib 预设):https://gist.github.com/aikubo/e0f49aaf4ff8941c84876f509de56b12
- Nature 终稿要求(89/183 mm、5–7 pt、0.25–1 pt、300/600/1200 dpi):https://www.nature.com/nature/for-authors/final-submission ;Extended Data 图规格 PDF:https://www.nature.com/documents/nature-extended-data.pdf
- Science(3.5/5.0/7.3 in、≥5 pt、≥0.5 pt、Myriad):https://www.science.org/content/page/information-authors-research-articles ;初投页(5.7/12.1/18.4 cm 三栏制,两页数字并存,按目标栏目核对):https://www.science.org/content/page/instructions-preparing-initial-manuscript ;修回页:https://www.science.org/content/page/instructions-preparing-revised-manuscript
- Elsevier 图件尺寸(90/140/190 mm、7 pt、300/500/1000 dpi、像素换算表):https://www.elsevier.com/about/policies-and-standards/author/artwork-and-media-instructions/artwork-sizing ;格式(TIFF/EPS 首选):…/artwork-overview ;FAQ(线宽 0.25/0.1 pt):…/artwork-faq

### 1.2 给 insar-agent 的"安全交集"设计值

一次设计、四刊通吃的保守取值(全部在最终物理尺寸上设计,**不做后期缩放**):

- 图宽:单栏 89–90 mm、1.5 栏 140 mm、双栏 180–183 mm;按目标宽度直接设 `figsize`。
- 字号:正文/轴标签 7–8 pt,刻度 6–7 pt,任何文字 ≥5 pt;面板标签 8 pt 粗体小写 `(a)`。
- 线宽:轴线/刻度 0.5–0.8 pt,数据线 ≥1 pt,任何线 ≥0.25 pt。
- 分辨率:含栅格数据层(速率场即是)按"combination art"对待 → **500–600 dpi** 存栅格版;矢量 PDF 同步输出。
- 格式策略:PNG 只作预览/网页/报告嵌入;**投稿交付 PDF(矢量)+ TIFF(栅格)**,Elsevier 明确不收 PNG。
- 图内不写标题、不写 "Figure 1"(AGU 明令);标题信息移入 caption——`figures.py` 现在的 `ax.set_title(...)` 在投稿模式下应去掉或改为可开关。
- 图内文字一律英文,避免中文字体嵌入/缺字问题。

---

## 二、科学配色

### 2.1 为什么不用 jet(及一切彩虹色标)

Crameri, Shephard & Heron (2020), *The misuse of colour in science communication*, Nature Communications 11:5444(https://doi.org/10.1038/s41467-020-19160-7)给出定量依据:

- 色轴等价于第三根坐标轴,必须"等数据变化 = 等感知变化"(感知均匀)。jet 的亮度非单调、局部梯度剧烈,会在数据平滑处制造假边界、在数据陡变处抹平细节,盲判读误差可超过所显示数据变化幅度的 **7%**;
- jet 对红绿色弱(deuteranopia/protanopia,男性约 8%)不可读,灰度打印后不单调;
- 结论性建议:顺序数据用感知均匀顺序色标(如 batlow),发散数据用感知均匀发散色标,周期数据用循环色标;红-绿组合整体回避。

配套论文:Crameri (2018), Geosci. Model Dev. 11:2541–2562(https://doi.org/10.5194/gmd-11-2541-2018);色标数据集 Zenodo(概念 DOI,含 v8.0.1):https://doi.org/10.5281/zenodo.1243862

### 2.2 形变速率图:发散色标(Crameri 家族选型)

速率场是"零点有物理意义"(0 = 无形变)的发散数据。Crameri 发散色标(https://www.fabiocrameri.ch/colourmaps/):

| 色标 | 端点色 | InSAR 适用场景 |
|---|---|---|
| **vik** | 蓝 ← 白 → 红 | 速率图首选:与"红=沉降/远离卫星、蓝=抬升/朝向卫星"的社区习惯衔接最自然;MintPy 已内置 |
| roma | 蓝绿 ← 白 → 棕红 | 为地震层析设计,亦常用于形变;`figures.py` 参数名 `roma` 的本尊 |
| broc / cork / berlin | 蓝-白-绿 / 蓝-白-棕 / 蓝-黑-红 | 备选;berlin 为暗底版(深色底图叠加时用) |

使用纪律:发散色标 **vmin/vmax 必须关于 0 对称**(现有代码 `lim = max(|p2|, |p98|)` 正确,保留);不对称需求用 `matplotlib.colors.TwoSlopeNorm`,不要直接砍一边。

### 2.3 缠绕相位:循环色标

缠绕干涉相位在 ±π 处回卷,普通发散/顺序色标会在回卷处制造人为断裂。惯例:

- **Crameri romaO**(及 vikO/brocO/corkO/bamO):官方明确以 SAR 干涉为设计场景,示例即 Jónsson (2002) Darwin 火山干涉图,见 https://www.fabiocrameri.ch/cycliccolourmaps/ ;
- **cmocean `phase`**:恒定亮度循环色标(Thyng et al. 2016, Oceanography 29(3), https://doi.org/10.5670/oceanog.2016.66 ;文档 https://matplotlib.org/cmocean/ );
- matplotlib ≥3.0 内置 `twilight` / `twilight_shifted` 是零依赖兜底;
- InSAR 社区旧习惯:MintPy 自带 `cmy`、ISCE 的 `dismph`(https://mintpy.readthedocs.io/en/latest/api/colormaps/ ,该页同时确认 MintPy 默认打包了 Crameri batlow/roma/vik/vikO/oleron)。

### 2.4 相干性 / 幅度 / DEM:顺序色标

- 相干性(0–1)、幅度:感知均匀顺序色标,**batlow**(Crameri 旗舰,"科学的彩虹")或内置 `viridis`;灰度 `grayC`/`gray` 亦可(相干性常用灰图)。
- DEM/地形底图:Crameri **oleron**(专为地形设计的分段色标);山影灰底 + 半透明数据层是更常见的 InSAR 做法(见 4.5)。

### 2.5 色盲安全验证工具

| 工具 | 形态 | 备注 |
|---|---|---|
| DaltonLens simulator | 在线 | https://daltonlens.org/colorblindness-simulator ;作者系统评估过各算法:**Brettel 1997 / Viénot 1999 / Machado 2009 可信,Coblis V1/V2 算法不可靠不推荐** |
| Color Oracle | 桌面(Win/mac/Linux) | https://colororacle.org ,全屏实时滤镜,设计时随手切换 |
| Colorblind Vision Simulator | 在线(2026 新) | https://colors.phage.org ,模拟+自动修色+DPI/TIFF 转换,纯浏览器本地处理 |
| 自动化(可进 CI) | Python | `colorspacious`/`daltonlens` 包可对 PNG 做 CVD 仿真;最廉价的自检:图转灰度看是否仍单调可读(Crameri 色标天然通过) |

### 2.6 小结:insar-agent 的配色决策表

| 产品 | 色标 | 兜底(零依赖) |
|---|---|---|
| LOS 速率图 | `cmc.vik` | `RdBu_r`(ColorBrewer,CVD 安全) |
| 缠绕相位/缠绕形变 | `cmc.romaO` 或 `cmocean.phase` | `twilight` |
| 相干性/温度相干 | `cmc.batlow` 或 `viridis` | `viridis` / `gray` |
| DEM 底图 | 山影灰(`gray`)或 `cmc.oleron` | `gray` |
| 一律禁用 | `jet`、`hsv`、`rainbow`、红绿对 | — |

---

## 三、InSAR 图件领域惯例

### 3.1 速率图必带元素清单

1. **参考点标记**:InSAR 速率是相对量,必须画出空间参考点。惯例为黑色方块(如 Remote Sens. 18(1):142 图 5 "black square",https://doi.org/10.3390/rs18010142 ),也见五角星;caption 中写明参考方式。MintPy 的 `velocity.h5` 属性 `REF_LAT`/`REF_LON`(或 `REF_Y`/`REF_X`)直接可用。
2. **色标单位与正方向约定**:单位 mm/yr(慢速形变、城市沉降首选)或 cm/yr(快速形变);**必须写明 "positive = motion toward satellite"**(或相反)。ESA/CCI InSAR 指南的约定:正值=朝向卫星,负值=远离卫星(https://bigweb.unifr.ch/Science/Geosciences/Geomorphology/Pub/Website/CCI/CurrentVersion/Current_InSAR-based_Guidelines.pdf );挪威 NGU 全国 InSAR 服务同此(https://www.ngu.no/en/geological-mapping/what-insar )。
3. **卫星飞行方向 + 视线方向双箭头**:标注 heading(方位向)与 LOS 地面投影(右视 = heading + 90°),常画成一对正交箭头并注 "Azi"/"LOS";升降轨图并列时尤其必要(几何示意惯例见 https://doi.org/10.3390/rs18010142 图 4)。入射角 θ 常一并写入 caption。
4. **断层线/活动构造叠加**:黑色实线 + 名称标注(同上文献 Haiyuan Fault 画法);数据源常用 GEM active faults 或区域断层库,叠加层保持矢量。
5. **经纬度坐标轴**:AGU 明确要求地图标注 lat/lon(Wiley hub);等经纬度地理编码网格用 `imshow(extent=…)` 即可,无需投影库。
6. **比例尺(+ 指北)**:轴已是经纬度时指北可省,比例尺仍建议保留(度→km 直观化)。
7. **DEM 山影底图**:社区标准做法(MintPy `view.py --dem geometryGeo.h5 --shade-exag`),提供地形语境,低相干掩膜区露出灰底而非空白。
8. **掩膜惯例**:低相干区(如 temporal coherence < 0.7)置 NaN/透明,不要让不可靠像元参与视觉判断。

### 3.2 时序图排版

参照 Xu et al. (2021) JGR Solid Earth(https://doi.org/10.1029/2021JB022579 )图 4 的成熟版式:

- 多站点小图纵向堆叠、**共享时间轴**(`sharex=True`),每格右上角标站名/点位;
- GNSS 日解画灰色小点,InSAR 画彩色实心点/折线;两者并列时 GNSS 需先投影到 LOS;
- InSAR 每期误差棒 = 站点周围空间窗口(该文用 500 m)内像元标准差;
- 拟合的线性速率 ± 1σ 写在小图内(左上角,7 pt);
- 纵轴统一 "LOS displacement (mm)",各小图 y 范围尽量一致以便横向比较。

### 3.3 干涉图(缠绕相位)标注

- 循环色标(romaO/phase),colorbar 刻度 −π…π;
- **波长-形变换算必须标注**:一条条纹(2π)= λ/2 的 LOS 形变;Sentinel-1 C 波段 λ≈5.6 cm(精确 0.055465763 m),**一条条纹 ≈ 2.8 cm**。写进 colorbar 副标签或 caption。出处:ASF HyP3 产品指南 https://hyp3-docs.asf.alaska.edu/guides/insar_product_guide/ ;NASA ARSET 问答 https://earthdata.nasa.gov/s3fs-public/2025-05/ARSET_SAR-intro_part2_qa.pdf ;ESA S1TBX 教程 https://step.esa.int/docs/tutorials/S1TBX%20TOPSAR%20Interferometry%20with%20Sentinel-1%20Tutorial_v2.pdf
- 干涉对日期(YYYYMMDD–YYYYMMDD)与垂直基线写入小图标题或 caption。

### 3.4 生态参照

MintPy `view.py` 已实现上述大半(参考点方块、`--dem` 山影、`--lalo-label`、单位换算、`--cbar-label`),其默认模板色标仍是 `jet`(https://manpages.debian.org/unstable/mintpy/mintpy-view.1 )——insar-agent 自研出图对标它的元素完备性、超越它的默认配色即可称"论文级"。

---

## 四、matplotlib 落地

### 4.1 rcParams 出版预设(含字体嵌入与 mathtext)

```python
MM = 1 / 25.4  # mm → inch
PUB_RC = {
    # 字体:四刊都接受的无衬线族;缺 Arial 时退 DejaVu(matplotlib 自带,必定存在)
    "font.family": "sans-serif",
    "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans"],
    "font.size": 8, "axes.labelsize": 8, "axes.titlesize": 8,
    "xtick.labelsize": 7, "ytick.labelsize": 7, "legend.fontsize": 7,
    # 线宽:轴线 0.6 pt(0.25–1 pt 区间内)
    "axes.linewidth": 0.6,
    "xtick.major.width": 0.6, "ytick.major.width": 0.6,
    "lines.linewidth": 1.0,
    # 字体嵌入:Type 42(TrueType)→ PDF/EPS 文本在 Illustrator 可编辑、过期刊 Type 3 检查
    "pdf.fonttype": 42, "ps.fonttype": 42,
    "svg.fonttype": "none",            # SVG 保留 <text> 而非转曲
    "mathtext.fontset": "dejavusans",  # 数学符号与正文同族无衬线;不开 usetex(零依赖、服务器安全)
    "figure.constrained_layout.use": True,
    "savefig.dpi": 600,                # combination art 500–600 dpi
}
plt.rcParams.update(PUB_RC)
```

依据:matplotlib 字体文档(Type 3 vs Type 42、子集化嵌入)https://matplotlib.org/stable/users/explain/text/fonts.html ;rcParams 全表 https://matplotlib.org/stable/users/explain/customizing.html ;Type 42 对 Illustrator/IEEE 合规的必要性:https://jonathansoma.com/lede/data-studio/matplotlib/exporting-from-matplotlib-to-open-in-adobe-illustrator/

### 4.2 物理尺寸 figsize

```python
fig, ax = plt.subplots(figsize=(90 * MM, 75 * MM))   # 单栏
fig, ax = plt.subplots(figsize=(140 * MM, 105 * MM)) # 1.5 栏
fig, axs = plt.subplots(1, 2, figsize=(183 * MM, 80 * MM))  # 双栏(升/降轨并列)
```

在最终尺寸上设计 → 字号所见即所得,期刊不再缩放(Science/Nature 均如此要求)。现有 `figsize=(10, 8)`(254×203 mm)远超任何版面,缩印后字号失控,是当前离"论文级"最远的一处。

### 4.3 constrained_layout 与多子图共享色标

`layout="constrained"`(或 rcParam `figure.constrained_layout.use`)是官方推荐的色标布局方案,自动为 colorbar 让位、避免 `tight_layout` 与 colorbar 的经典冲突(https://matplotlib.org/stable/users/explain/axes/colorbar_placement.html ):

```python
fig, axs = plt.subplots(1, 2, figsize=(183*MM, 80*MM), layout="constrained",
                        sharex=True, sharey=True)
norm = mpl.colors.Normalize(vmin=-lim, vmax=lim)      # 共享 norm 保证颜色一致
for ax, (data, tag) in zip(axs, [(vel_asc, "Ascending"), (vel_des, "Descending")]):
    im = ax.imshow(data, cmap=cmap, norm=norm, extent=extent, interpolation="nearest")
    ax.set_title(tag)
fig.colorbar(im, ax=axs, shrink=0.8, label="LOS velocity (mm/yr)")  # ax=数组 → 一根共享色标
```

多图共享色标官方示例:https://matplotlib.org/stable/gallery/images_contours_and_fields/multi_image.html

### 4.4 比例尺与指北针(零依赖画法)

等经纬度轴上,经度 1° ≈ 111.32·cos(lat) km,直接画线段即可,不需要任何第三方包:

```python
lat_c = 0.5 * (extent[2] + extent[3])
km_per_deg = 111.32 * np.cos(np.radians(lat_c))
bar_km  = max(round(abs(extent[1] - extent[0]) * km_per_deg / 5), 1)  # 全宽约 1/5,可再吸附到 1-2-5 系列
bar_deg = bar_km / km_per_deg
x0 = extent[0] + 0.06 * (extent[1] - extent[0]); y0 = extent[2] + 0.06 * (extent[3] - extent[2])
ax.plot([x0, x0 + bar_deg], [y0, y0], "k-", lw=1.5, solid_capstyle="butt")
ax.text(x0 + bar_deg / 2, y0, f"{bar_km} km", ha="center", va="bottom", fontsize=6)
# 指北(轴为经纬度时可省):
ax.annotate("N", xy=(0.97, 0.97), xytext=(0.97, 0.90), xycoords="axes fraction",
            ha="center", fontsize=7, arrowprops=dict(arrowstyle="-|>", lw=1.0, color="k"))
```

要更精致再上包:`matplotlib.offsetbox`/`mpl_toolkits.axes_grid1.anchored_artists.AnchoredSizeBar`(matplotlib 自带,仍零新增依赖);第三方 `matplotlib-scalebar`(https://github.com/ppinard/matplotlib-scalebar ,传 `dx`)或 `matplotlib-map-utils`(https://github.com/moss-xyz/matplotlib-map-utils/ ,4326 下自动大圆换算)。对 insar-agent:手画方案够用且无依赖风险,推荐。

### 4.5 GeoTIFF/DEM 背景叠加:最小方案 vs cartopy

**最小方案(推荐,零新增依赖)**:MintPy 工作区自带 `inputs/geometryGeo.h5`(`height` 数据集),配 `matplotlib.colors.LightSource` 即可山影:

```python
from matplotlib.colors import LightSource
with h5py.File(ws / "mintpy/inputs/geometryGeo.h5", "r") as g:
    dem = np.nan_to_num(g["height"][:])
shade = LightSource(azdeg=315, altdeg=45).hillshade(dem, vert_exag=1.0)
ax.imshow(shade, cmap="gray", extent=extent, zorder=0)               # 底:山影
ax.imshow(vel, cmap=cmap, vmin=-lim, vmax=lim, extent=extent, zorder=1)  # 面:速率(NaN 自然透出灰底)
```

外部 GeoTIFF(如 DEM/正射影像)则 `rasterio.open` + `rasterio.plot.plotting_extent(ds)` 得到 `(left, right, bottom, top)` 直接喂 `imshow(extent=…)`(https://rasterio.readthedocs.io/en/stable/api/rasterio.plot.html )——rasterio 在 mintpy 引擎环境通常已有。

**cartopy 的收益与成本**:收益 = 正确的任意投影重投影(`transform=`)、自动经纬网/海岸线/国界矢量(https://cartopy.readthedocs.io/stable/gallery/scalar_data/raster_reprojections.html );成本 = PROJ/GEOS 二进制依赖链、安装脆、Agg 服务器端偶有坑。**结论**:MintPy 地理编码产品是等经纬度网格,`imshow + extent` 在数学上就是 plate carrée 投影,正确无损;区域尺度(<几度)纵横比失真可用 `ax.set_aspect(1/cos(lat))` 修正。cartopy 只在需要大范围小比例尺制图或叠加在线底图时才值得引入,建议作为可选增强而非默认路径。

### 4.6 输出与栅格化细节

- 双输出:`fig.savefig(out/"velocity.png")`(600 dpi,预览)+ `fig.savefig(out/"velocity.pdf")`(矢量,投稿);TIFF 可由 PNG 经 Pillow 转出(带 dpi 元数据)。
- 矢量文件中的大栅格层:`imshow` 在 PDF 中本就以内嵌图像存储,无需处理;**大点云 scatter 必须 `rasterized=True`**,否则 PDF 体积爆炸、审稿人打不开。
- `bbox_inches="tight"` 与 constrained_layout 二选一,不叠用(后者已管好边距,叠用会改变最终物理尺寸)。

---

## 五、图注与可追溯性(provenance)

约束:AGU 等明令图内不得含 caption/图题 → provenance 必须"低调"。三层做法,由隐到显:

1. **文件级元数据(首选,完全不占版面)**:`savefig(..., metadata={...})`。PNG(Agg)支持任意 tEXt 键值(键 <79 字符,常用 `Title/Author/Description/Software/Comment/Creation Time`),PDF 支持 `Title/Author/Subject/Keywords` 等预定义键(https://matplotlib.org/stable/api/_as_gen/matplotlib.pyplot.savefig.html )。写入:数据窗口(START_DATE–END_DATE)、轨道/平台、参考点、处理版本、参数指纹(短 hash)、色标名。可复现性小技巧:回归测试需字节级可复现时,把 PDF 的 `CreationDate` 设为 `None`。
2. **图角小字(可开关)**:`fig.text(0.99, 0.01, stamp, ha="right", va="bottom", fontsize=5, color="0.45")`——5 pt、40% 灰,内容如 `S1 DESC | 20230101–20241230 | insar-agent v0.4 | cfg a1b2c3d`。内部评审/讲义版默认开,投稿版关(正文字号下限 5 pt 的规则以"传达数据的文字"为对象,这类水印惯例上容忍,但审稿保守起见提供开关)。
3. **Sidecar JSON(机器可读)**:`velocity.png.json` 记录完整参数字典 + 输入文件 hash;对齐 insar-agent"数据不造假、处处可审计"的产品主张,也方便 `report/methods.py` 反向引用同一份指纹。

社区参照:`dfm/savefig`(git hash 写入 PNG/PDF 元数据,https://github.com/dfm/savefig );`gitplothash`(图角 commit stamp + dirty 标记,https://github.com/tomsmilton/gitplothash );W3C PROV 级方案 yProv4DV(https://doi.org/10.48550/arXiv.2603.20437 )——insar-agent 用 1+3 组合即可,不必上重型框架。

---

## 六、`figures.py` 改造清单(按 成本×价值 排序)

| # | 改造项 | 成本 | 价值 | 关键片段 |
|---|---|---|---|---|
| 1 | rcParams 出版预设 + 物理尺寸 figsize + Type 42 | 极低(~20 行) | 极高:一次性解决字体/字号/线宽/嵌入四类硬伤 | `plt.rcParams.update(PUB_RC)`;`figsize=(140*MM, 105*MM)`(见 4.1/4.2) |
| 2 | vik 色标接入(cmcrameri 优先,分级回退) | 低 | 高:告别"默认配色"观感,且 CVD 安全 | 见 §7 `pub_cmap()`;取舍见 7.1 |
| 3 | 地理坐标轴(attrs → extent) | 低 | 高:满足 AGU 地图规范,是比例尺/参考点的前置 | `extent=(x0, x0+dx*ncol, y0+dy*nrow, y0)`(MintPy attrs `X_FIRST/Y_FIRST/X_STEP/Y_STEP`) |
| 4 | colorbar 单位 + 正方向约定 | 极低(1 行) | 高:审稿人第一眼检查项 | `cbar.set_label("LOS velocity (mm/yr)\npositive = motion toward satellite")` |
| 5 | 参考点黑方块 + "Ref" | 低 | 高:相对测量的必带元素 | `ax.plot(ref_lon, ref_lat, "ks", ms=4, mec="w", mew=0.5)`(attrs `REF_LAT/REF_LON`) |
| 6 | provenance:savefig metadata + 图角小字开关 + sidecar JSON | 低 | 中高:契合产品主张,几乎零版面代价 | `fig.savefig(p, metadata={"Description": stamp})`(见 §5) |
| 7 | 飞行/LOS 双箭头 | 中(几何要算对) | 中高:升降轨解读必需 | heading θ:飞行向量 `(sinθ, cosθ)`,右视 LOS = `(sin(θ+90°), cos(θ+90°))`(attrs `HEADING`) |
| 8 | DEM 山影底图(geometryGeo.h5 + LightSource) | 中 | 中高:一步接近 MintPy 级观感,NaN 区不再空白 | 见 4.5 |
| 9 | 比例尺(零依赖) | 低 | 中 | 见 4.4 |
| 10 | 双输出 PNG + PDF(矢量) | 低 | 中:打通投稿链路(Elsevier 不收 PNG) | `fig.savefig(out/"velocity.pdf")` |
| 11 | 时序/多子图版式:constrained_layout + 共享色标 + sharex | 中 | 中:覆盖 step 10 之外的时序产品 | 见 4.3 |
| 12 | 断层线叠加(可选数据源) | 中(需断层数据接入) | 中(构造类研究高、城市沉降低) | `ax.plot(flt_lon, flt_lat, "k-", lw=0.8)` |
| 13 | cartopy 投影/底图(可选增强) | 高(依赖链) | 低-中(等经纬度场景无增益) | 仅大范围制图启用,见 4.5 |

注:第 1–6 项合计约 60 行改动,即可覆盖"论文级"检查单的 80%;7–10 项补齐领域惯例;11 之后按产品线扩展。

---

## 七、论文级速率图参考实现(<120 行)

### 7.1 Crameri 色标获取方式的取舍

| 方案 | 优点 | 缺点 | 结论 |
|---|---|---|---|
| **cmcrameri 包**(https://pypi.org/project/cmcrameri/ ) | 真·感知均匀数据(基于 Scientific colour maps v8);纯 Python,仅依赖 numpy+matplotlib;pip/conda 皆有 | 引擎环境需多装一个包 | **首选**:在引擎环境 `pip install cmcrameri`,与 mintpy 依赖无冲突 |
| 内嵌色标数组 | 零依赖 | 完整 vik 为 256×3 浮点数组,内嵌进出图脚本约 260 行,喧宾夺主;若用少量锚点插值近似,则**破坏感知均匀性**,伪 vik 不如不用 | 若必须离线内嵌:把官方 `vik.txt`(MIT 许可,需保留 Crameri 引用)放进包资源 `data/colormaps/`,`np.loadtxt` + `ListedColormap` 加载,**不要**手写锚点近似 |
| 运行时回退 `RdBu_r` | matplotlib 内置,ColorBrewer 出品,CVD 安全的合格发散色标 | 非 Crameri,白区偏宽 | 作为最终兜底,并把实际所用色标写进 provenance |

### 7.2 参考实现(直接可抄进 `_FIGURE_PY` 的形态,113 行)

```python
# make_velocity_figure.py -- 论文级 LOS 速率图(velocity.h5 -> PNG + PDF)
# 依赖:h5py / numpy / matplotlib(mintpy 环境自带);cmcrameri 可选。
import sys
from datetime import datetime, timezone
from pathlib import Path

import h5py
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LightSource

MM = 1 / 25.4  # mm -> inch
PUB_RC = {  # 四刊安全交集:无衬线、正文 7-8 pt、线宽 0.5-1 pt、Type 42 嵌字
    "font.family": "sans-serif",
    "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans"],
    "font.size": 8, "axes.labelsize": 8,
    "xtick.labelsize": 7, "ytick.labelsize": 7,
    "axes.linewidth": 0.6, "xtick.major.width": 0.6, "ytick.major.width": 0.6,
    "pdf.fonttype": 42, "ps.fonttype": 42, "svg.fonttype": "none",
    "mathtext.fontset": "dejavusans",
    "figure.constrained_layout.use": True,
    "savefig.dpi": 600,
}


def pub_cmap(name="vik"):
    """Crameri 发散色标;缺 cmcrameri 时退 RdBu_r(CVD 安全兜底),并回报实际所用名。"""
    try:
        import cmcrameri.cm as cmc
        return getattr(cmc, name), f"cmc.{name}"
    except (ImportError, AttributeError):
        return plt.get_cmap("RdBu_r"), "RdBu_r"


def main(ws=Path(".")):
    plt.rcParams.update(PUB_RC)
    vel_path = next((p for p in (ws / "mintpy/velocity.h5", ws / "velocity.h5") if p.exists()), None)
    if vel_path is None:
        sys.exit("ERROR: velocity.h5 不存在")
    with h5py.File(vel_path, "r") as f:
        vel = f["velocity"][:] * 1000.0  # m/yr -> mm/yr
        atr = {k: str(v) for k, v in f.attrs.items()}

    nrow, ncol = vel.shape
    geo = all(k in atr for k in ("X_FIRST", "Y_FIRST", "X_STEP", "Y_STEP"))
    if geo:  # MintPy 地理编码属性 -> imshow extent(等经纬度网格,无需 cartopy)
        x0, y0, dx, dy = (float(atr[k]) for k in ("X_FIRST", "Y_FIRST", "X_STEP", "Y_STEP"))
        extent = (x0, x0 + dx * ncol, y0 + dy * nrow, y0)  # W, E, S, N(dy < 0)
    else:
        extent = None

    lim = float(np.percentile(np.abs(vel[np.isfinite(vel)]), 98))  # 对称限幅:发散色标零点居中
    cmap, cmap_name = pub_cmap("vik")

    fig, ax = plt.subplots(figsize=(140 * MM, 105 * MM))  # 1.5 栏物理尺寸,所见即所得

    dem_path = vel_path.parent / "inputs/geometryGeo.h5"
    if geo and dem_path.exists():  # DEM 山影底图(可选;NaN 区透出灰底)
        with h5py.File(dem_path, "r") as g:
            dem = np.nan_to_num(g["height"][:])
        shade = LightSource(azdeg=315, altdeg=45).hillshade(dem, vert_exag=1.0)
        ax.imshow(shade, cmap="gray", extent=extent, zorder=0)

    im = ax.imshow(vel, cmap=cmap, vmin=-lim, vmax=lim, extent=extent,
                   interpolation="nearest", zorder=1)
    cbar = fig.colorbar(im, ax=ax, shrink=0.75, pad=0.02, extend="both")
    cbar.set_label("LOS velocity (mm/yr)\npositive = motion toward satellite")
    cbar.outline.set_linewidth(0.6)

    if geo and "REF_LON" in atr and "REF_LAT" in atr:  # 参考点黑方块(领域惯例)
        rlon, rlat = float(atr["REF_LON"]), float(atr["REF_LAT"])
        ax.plot(rlon, rlat, "ks", ms=4, mec="w", mew=0.5, zorder=3)
        ax.annotate("Ref", (rlon, rlat), xytext=(3, 3),
                    textcoords="offset points", fontsize=6)

    if "HEADING" in atr:  # 飞行方向 + LOS 地面投影(右视 = heading + 90°)
        th = np.radians(float(atr["HEADING"]))
        x0a, y0a, L = 0.92, 0.88, 0.07  # 轴分数坐标锚点与箭长
        ax.annotate("", xytext=(x0a, y0a), xycoords="axes fraction",
                    xy=(x0a + L * np.sin(th), y0a + L * np.cos(th)),
                    arrowprops=dict(arrowstyle="-|>", lw=1.0, color="k"))
        ax.annotate("LOS", xytext=(x0a, y0a), xycoords="axes fraction", fontsize=6,
                    xy=(x0a + 0.6 * L * np.cos(th), y0a - 0.6 * L * np.sin(th)),
                    arrowprops=dict(arrowstyle="-|>", lw=0.8, color="k"))

    if geo:  # 零依赖比例尺:1 度经度 = 111.32*cos(lat) km
        km_deg = 111.32 * np.cos(np.radians(0.5 * (extent[2] + extent[3])))
        bar_km = max(round(abs(extent[1] - extent[0]) * km_deg / 5), 1)
        xb = extent[0] + 0.06 * (extent[1] - extent[0])
        yb = extent[2] + 0.06 * (extent[3] - extent[2])
        ax.plot([xb, xb + bar_km / km_deg], [yb, yb], "k-", lw=1.5, solid_capstyle="butt")
        ax.text(xb + 0.5 * bar_km / km_deg, yb, f"{bar_km} km",
                ha="center", va="bottom", fontsize=6)
        ax.set_xlabel("Longitude (\N{DEGREE SIGN}E)")
        ax.set_ylabel("Latitude (\N{DEGREE SIGN}N)")

    # provenance:图角小字(投稿版可整行删除)+ 文件元数据(零版面)
    stamp = (f"{atr.get('PLATFORM', 'SAR')} {atr.get('ORBIT_DIRECTION', '')} | "
             f"{atr.get('START_DATE', '?')}-{atr.get('END_DATE', '?')} | "
             f"cmap:{cmap_name} | {datetime.now(timezone.utc):%Y-%m-%dT%H:%MZ}")
    fig.text(0.99, 0.01, stamp, ha="right", va="bottom", fontsize=5, color="0.45")
    meta = {"Title": "InSAR LOS velocity", "Software": "insar-agent",
            "Description": f"{stamp} | ref=({atr.get('REF_LAT')},{atr.get('REF_LON')})"}

    out = ws / "products" / "figures"
    out.mkdir(parents=True, exist_ok=True)
    fig.savefig(out / "velocity.png", metadata=meta)  # 600 dpi 栅格:预览/报告
    fig.savefig(out / "velocity.pdf", metadata={"Title": meta["Title"]})  # 矢量:投稿
    print(f"OK {out / 'velocity.png'} | cmap={cmap_name} | vlim=+-{lim:.1f} mm/yr")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path("."))
```

接入说明(不在本次改动范围,仅备忘):
- 塞回 `_FIGURE_PY` 模板时,f-string 花括号需按现有 `{{...}}` 转义惯例处理;`dpi`/`cmap` 模板参数与 `PUB_RC["savefig.dpi"]`、`pub_cmap(name)` 对接,`build()` 里 `{"roma": "RdBu_r"}` 的映射表可删(回退逻辑已内聚到 `pub_cmap`)。
- 引擎环境建议补装 `cmcrameri`(纯 Python,数 MB);装不上也不阻塞,自动退 `RdBu_r` 且 provenance 记录在案。
- 速度直方图(质检辅助)保留现状即可,它不是投稿图件。

---

## 附:出处链接汇总

**期刊规范**:AGU 图文要求 https://www.agu.org/publications/authors/journals/text-graphics-requirements | AGU/Wiley 图件准备 https://agupubs.onlinelibrary.wiley.com/hub/books-manuscript-preparation | Nature 终稿 https://www.nature.com/nature/for-authors/final-submission | Nature Extended Data PDF https://www.nature.com/documents/nature-extended-data.pdf | Science 作者须知 https://www.science.org/content/page/information-authors-research-articles | Science 初投 https://www.science.org/content/page/instructions-preparing-initial-manuscript | Elsevier 图件尺寸 https://www.elsevier.com/about/policies-and-standards/author/artwork-and-media-instructions/artwork-sizing

**配色**:Crameri et al. 2020 NC https://doi.org/10.1038/s41467-020-19160-7 | Crameri 2018 GMD https://doi.org/10.5194/gmd-11-2541-2018 | Scientific colour maps(Zenodo)https://doi.org/10.5281/zenodo.1243862 | 循环色标专页 https://www.fabiocrameri.ch/cycliccolourmaps/ | cmocean https://matplotlib.org/cmocean/ | Thyng et al. 2016 https://doi.org/10.5670/oceanog.2016.66 | cmcrameri https://pypi.org/project/cmcrameri/ | DaltonLens https://daltonlens.org/colorblindness-simulator | Color Oracle https://colororacle.org | MintPy colormaps https://mintpy.readthedocs.io/en/latest/api/colormaps/

**InSAR 惯例**:ESA/CCI InSAR 指南 https://bigweb.unifr.ch/Science/Geosciences/Geomorphology/Pub/Website/CCI/CurrentVersion/Current_InSAR-based_Guidelines.pdf | Xu et al. 2021 JGR https://doi.org/10.1029/2021JB022579 | InSAR+GNSS 3D(参考点/箭头画法)https://doi.org/10.3390/rs18010142 | ASF HyP3 产品指南 https://hyp3-docs.asf.alaska.edu/guides/insar_product_guide/ | NASA ARSET https://earthdata.nasa.gov/s3fs-public/2025-05/ARSET_SAR-intro_part2_qa.pdf | NGU https://www.ngu.no/en/geological-mapping/what-insar | MintPy view.py https://manpages.debian.org/unstable/mintpy/mintpy-view.1

**matplotlib**:字体/Type 42 https://matplotlib.org/stable/users/explain/text/fonts.html | rcParams https://matplotlib.org/stable/users/explain/customizing.html | colorbar 布局 https://matplotlib.org/stable/users/explain/axes/colorbar_placement.html | 共享色标 https://matplotlib.org/stable/gallery/images_contours_and_fields/multi_image.html | savefig/metadata https://matplotlib.org/stable/api/_as_gen/matplotlib.pyplot.savefig.html | rasterio.plot https://rasterio.readthedocs.io/en/stable/api/rasterio.plot.html | cartopy 栅格重投影 https://cartopy.readthedocs.io/stable/gallery/scalar_data/raster_reprojections.html | matplotlib-scalebar https://github.com/ppinard/matplotlib-scalebar | matplotlib-map-utils https://github.com/moss-xyz/matplotlib-map-utils/ | AGU 风格 gist https://gist.github.com/aikubo/e0f49aaf4ff8941c84876f509de56b12

**provenance**:dfm/savefig https://github.com/dfm/savefig | gitplothash https://github.com/tomsmilton/gitplothash | yProv4DV https://doi.org/10.48550/arXiv.2603.20437
