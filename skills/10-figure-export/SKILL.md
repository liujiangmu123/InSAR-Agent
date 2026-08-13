---
name: 10-figure-export
description: 当规划或诊断第 10 步出图导出(论文级速度场图件、地理编码、GeoTIFF 导出)时使用:在 figure_journal / mintpy_geocode / gdal_warp 之间选方法、设定 dpi / cmap / format、掌握色标与参考点标注规范、诊断"拒绝出图"与色标兜底等行为。
capability: 10
version: "1.0.0"
applies_to: all
---

# 出图导出(论文级图件 / 地理编码 / GIS 导出)

第 10 步把 `velocity.h5` 变成可交付图件。方法 id:`figure_journal`(默认、推荐,
引擎标识 `-` 无外部引擎依赖)、`mintpy_geocode`(仅地理编码不排版)、`gdal_warp`
(导出 GeoTIFF 供 GIS);产物 artifact id 为 `figures`(候选 `products/figures`、
`products/velocities`,policy=stat)。`figure_journal` 的真实口径是
engines/figures.py 注入的独立脚本(引擎环境 Python 执行,h5py/matplotlib 由
mintpy 依赖带入):直接读 `mintpy/velocity.h5`(或根目录 `velocity.h5`),
输出 `<name>.png`(原图)+ `<name>.pdf`(矢量)+ `_browse.png`(2048 px 定宽)
+ `_thumb.png`(320 px 定宽)+ `.json` 元数据 sidecar,外加质检辅助的
`velocity_hist.png` 直方图。本步 `replay = safe`:出图无副作用,重跑安全;
三个参数全部是 presentation 类(§5.4 参数三分类),**不进指纹,改了不触发
上游重算**。

## 适用判据

- **`figure_journal`**:需要出版级(publication-quality)交付时的默认 ——
  600 dpi、色盲安全(CVD-safe)色带、参考点/LOS 箭头/比例尺齐备(见参数启发式),
  规格按四刊(AGU/Nature/Science/Elsevier)安全交集设计
  (reference/RESEARCH-pub-figures-2026-08-12.md)。
- **`mintpy_geocode`**:上游产物还在雷达坐标(radar coordinates)时的前置步骤
  (HyP3 路线产品已地理编码,通常不需要);只做坐标变换不排版,引擎 `mintpy`。
- **`gdal_warp`**:交付对象是 GIS 用户/需要与矢量数据叠加分析时,导出 GeoTIFF;
  引擎 `gdal`。
- 三方法不互斥:典型组合是 geocode(如需)→ figure_journal 出论文图 +
  gdal_warp 出 GIS 件;当前 registry 单选,组合需拆 run 或后续扩展。

## 参数启发式

本步骤在 registry(capabilities.py id=10)声明的参数只有三个(全 presentation):

- **`dpi`**(默认 `600`,范围 72–1200):四刊安全交集 —— 速度场含栅格数据层,
  按 combination art 对待取 500–600 dpi(Elsevier 500、AGU 图像 ≥300/线稿 600);
  600 为默认即投稿档;预览/报告用 150 足够;1200 只对纯线稿有意义。脚本另按
  固定像素宽出 `_browse`(2048 px)与 `_thumb`(320 px)两档,同一 Figure 只改
  dpi 连续 savefig,版式字号与原图严格一致 —— 不要为缩略图单独改版式。
- **`cmap`**(默认 `"roma"`):默认值视作「未显式指定」哨兵,引擎按产物类型
  (h5 `FILE_TYPE`)路由默认色带 —— 速度=vik(diverging)、相干=batlow
  (sequential)、缠绕相位=romaO(cyclic)(2026-08-13 C8 修正,Crameri 2020
  三分类;velocity 主图仍落 vik,旧语义不变);显式指定其他值直通 cmcrameri
  名字空间;**缺 cmcrameri 包时退 `RdBu_r` 并记录在案**(sidecar 的
  `cmap` 字段写实际所用名,provenance 小字同步)。选型规范:
  - LOS 速率(零点有物理意义的发散数据)→ `vik`(社区习惯:红=远离卫星/沉降,
    蓝=朝向卫星/抬升);
  - 发散色标 vmin/vmax 必须关于 0 对称 —— 脚本按 |velocity| 的 98 分位对称限幅,
    全零场兜底 ±1.0 mm/yr;
  - 相干性(0–1 顺序量)→ `batlow`;缠绕相位(循环量)→ `romaO` —— 循环量配
    非循环色带会在 ±π 处产生假边界(2026-08-13 C8 修正);
  - 一律禁用 jet/hsv/rainbow(Crameri et al. 2020 的定量依据:亮度非单调制造
    假边界,红绿色弱不可读)。
- **`format`**(默认 `"png+pdf"`):PNG 供预览/报告/影像面板;PDF 是矢量原稿
  (Type 42 嵌字,文本不转曲)供投稿 —— Elsevier 明确不收 PNG,投稿交付
  PDF(矢量)+ 高分辨率栅格。
- **参考点与标注规范**(对齐 engines/figures.py 现状,InSAR 领域惯例):
  - **参考点**:读 `velocity.h5` 属性 `REF_LAT`/`REF_LON`,画黑色方块
    (`ks`,白描边)+ "Ref" 注记 —— InSAR 速度是相对量,参考点必须上图;
  - **色标标签**:`LOS velocity (mm/yr)` 且必须写明正方向约定
    `positive = motion toward satellite`(ESA/CCI 指南约定);
  - **LOS/飞行方向箭头**:按 `HEADING` 属性画方位向箭头 + LOS 地面投影箭头
    (右视 = heading + 90°);
  - **比例尺**:零依赖换算 1° 经度 = 111.32·cos(lat) km;纵横比按 cos(lat)
    校正(imshow 默认 1°=1° 会把中纬度南北向拉伸);
  - **图内不放标题**(AGU 惯例,标题属 caption);图角 provenance 小字
    (平台/轨道/日期/cmap/UTC 时间戳,投稿版可删);
  - **DEM 山影底图**:`mintpy/inputs/geometryGeo.h5` 的 height 存在时自动叠加
    (灰度山影,零新增依赖)。
- sidecar(`<name>.json`)字段:title/units/cmap(实际所用)/vlim/date_range/
  ref_point/step/params —— `/api/figures` 读到即并入 meta;三档文件按命名约定
  归并到基图条目,不单独入 artifacts 表。

## 常见失败与处置

1. **`ERROR: velocity.h5 不存在`,exit 2** → 第 9 步产物缺失,或路径不在脚本
   候选(`mintpy/velocity.h5`、`velocity.h5`)→ 回查第 9 步 run_ok;自定义布局
   须落到候选路径之一,不要改判定逻辑。
2. **`ERROR: velocity 全 NaN(0 个有效像元),拒绝出图`,exit 2** → 上游掩膜
   全灭(时间相干/解缠覆盖问题)→ 回查第 7 步;这是有意设计的自守
   (REVIEW-r2 P2-14):脚本独立跑(复现/调试)时也绝不产出空图假产物,与
   velocity.h5 缺失同一失败口径。
3. **图上没有参考点方块 / 比例尺 / 经纬度轴** → `velocity.h5` 缺地理编码属性
   (X_FIRST/Y_FIRST/X_STEP/Y_STEP → 退像素坐标)或缺 REF_LAT/REF_LON(跳过
   参考点标注)→ 先跑 `mintpy_geocode` 再出图;HyP3 路线正常自带,ISCE2 本地链
   须确认 geocode 段完成。
4. **sidecar 里 `cmap` 是 `RdBu_r` 而不是请求的色标** → 引擎环境缺 cmcrameri 包
   (兜底生效,已记录在案,不算失败)→ 投稿前在引擎环境安装 cmcrameri 后重跑
   本步(replay safe,秒级);报告引用色标名时以 sidecar 为准,不想当然。
5. **`ModuleNotFoundError: h5py / matplotlib`** → 引擎环境未配置
   (`INSAR_ENGINE_PREFIX`),回退宿主 Python 且宿主缺包(显式失败设计,不静默
   降级)→ 配置引擎环境,或宿主安装 h5py+matplotlib。
6. **改了 dpi/cmap 却看到"未重算"** → 三参数均为 presentation 类,不进指纹,
   属预期行为 —— 本步 replay safe 直接重跑即可得新图;若期望它触发上游重算,
   说明把展示参数误当科学参数,检查规划而非执行器。

## QA 依据

- **run_ok 双判定**(registry 声明):`exit_code == 0` + `artifact_exists
  (figures)`;产物 policy=stat(图件按存在性+元信息核验,不做内容哈希)。
- **数据不造假纪律**:脚本直接读 velocity.h5 渲染,vlim/有效像元数/日期范围等
  全部来自数据本身并写入 sidecar;直方图(velocity_hist)是质检辅助件,用于
  肉眼核对速度分布形态(对称性、离群值)。
- **视觉自查清单**:色标零点居中(发散数据);参考点方块落在稳定区(与第 7 步
  参考点一致);形变图案形态与已发表结果/机理预期一致(如同震条纹瓣沿断层
  对称);低相干掩膜区显示为山影灰底而非误导性色块。
- **规格合规**(投稿前):600 dpi、正文字号 7–8 pt、线宽 ≥0.5 pt、Type 42
  嵌字、图内无标题、地图带经纬度 —— 依据四刊硬性要求交集
  (RESEARCH-pub-figures-2026-08-12.md §一)。

## 参考文献

- Crameri F., Shephard G.E., Heron P.J. (2020). The misuse of colour in science
  communication. *Nature Communications* 11:5444.
  doi:10.1038/s41467-020-19160-7(为何禁用 jet;感知均匀与 CVD 安全的定量依据)
- Crameri F. (2018). Geodynamic diagnostics, scientific visualisation and
  StagLab 3.0. *Geosci. Model Dev.* 11:2541-2562. doi:10.5194/gmd-11-2541-2018
  (Scientific colour maps 家族;vik/roma 等色标出处,数据集
  doi:10.5281/zenodo.1243862)
- Thyng K.M., Greene C.A., Hetland R.D., Zimmerle H.M., DiMarco S.F. (2016).
  True colors of oceanography: Guidelines for effective and accurate colormap
  selection. *Oceanography* 29(3):9-13. doi:10.5670/oceanog.2016.66
  (cmocean;循环色标 phase,缠绕相位图选型的依据)
- AGU 图件要求:https://www.agu.org/publications/authors/journals/text-graphics-requirements
  (地图必须标注经纬度;图内不含标题/Figure N 字样)
- Nature 终稿图件规格:https://www.nature.com/nature/for-authors/final-submission
  (89/183 mm 栏宽、5–7 pt、300/600/1200 dpi)
- Science 作者须知:https://www.science.org/content/page/information-authors-research-articles
- Elsevier 图件规格(RSE 等):
  https://www.elsevier.com/about/policies-and-standards/author/artwork-and-media-instructions/artwork-sizing
  (combination art 500 dpi;明确不收 PNG)
- MintPy colormaps 文档(内置 Crameri batlow/roma/vik/vikO/oleron):
  https://mintpy.readthedocs.io/en/latest/api/colormaps/
- 本仓库调研沉淀:reference/RESEARCH-pub-figures-2026-08-12.md(四刊规格对照、
  配色决策表、InSAR 图件惯例清单 —— engines/figures.py 的设计依据)
