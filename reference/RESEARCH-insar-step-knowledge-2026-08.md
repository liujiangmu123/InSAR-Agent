# RESEARCH:InSAR 11 步流水线逐步知识底座(权威源 / 决策点 / 失败模式 / 参数核对)

- 日期:2026-08-13
- 目的:为每个流水线步骤即将挂载的「技能文档」(参数启发式、失败→处置、QA 依据)提供可核查的权威知识源。
  对象是 `src/insar_agent/registry/capabilities.py`(本文核对版本:commit `9cbd3ea`)的 11 个 Capability。
  **本文只做知识沉淀,不改产品代码。**
- 与前篇的关系:`reference/RESEARCH-insar-params-2026-08-12.md`(下称 **[前篇]**)纵向钻透了
  contract.yaml 阈值与三个场景包;本篇横向铺开 11 步,每步四节(权威源/决策点/失败模式/参数核对),
  参数映射覆盖 capabilities.py 的全部 science 参数。前篇已核实的数值直接引用不重复展开。
- 方法与来源分级(沿用前篇):
  - **[U] upstream_default** — 软件源码/官方默认配置的确切值(本次全部重新核对原文);
  - **[L] literature** — 期刊论文明确数值/公式;
  - **[C] community practice** — 官方教程/产品指南/论坛汇聚的惯例;
  - 失败模式表另标来源类型:文档 / 论坛 / 论文 / 本仓库实测。
- 调研纪律:所有引用均在 2026-08-13 由网络检索核对原文(topsApp.py、smallbaselineApp.cfg、HyP3
  Product Guide、SentiWiki、Copernicus POD/DEM 手册等一手来源逐字确认);无先例的阈值如实标注。

---

## 0. 总览:11 步 × 推荐方法 × 关键 science 参数 × 本文结论

| # | 能力名 | 默认方法 | 关键参数(默认) | 参数核对结论 |
|---|---|---|---|---|
| 1 | 数据获取 | local_import(推荐)/ asf_search_slc / hyp3_submit | scenes=7, platform=sentinel-1, dates, source | scenes 下限与磁盘估算 → **修正 C4/C5** |
| 2 | 辅助数据 | dem_copernicus(推荐)/ dem_srtm / dem_local | dem=copernicus-30m, orbit=poeorb | 合理(poeorb→resorb 降级规则见 §2.2) |
| 3 | 配准 | isce2_tops_geom_esd(推荐)/ stripmap_xcorr / snap | esd_coherence_threshold=0.85 | 0.85 = ISCE2 上游默认,OK |
| 4 | 干涉 | isce2_ifg_multilook(推荐) | range_looks=10, azimuth_looks=2, pairs=11 | S1 合理(=HyP3 精档);stripmap 须倒置 → **修正 C3** |
| 5 | 滤波 | goldstein(推荐)/ boxcar / none | alpha=0.4, filter_strength=0.5 | 双参数重复且值冲突 → **修正 C2** |
| 6 | 解缠 | snaphu_mcf(推荐)/ snaphu_smooth / icu / 3D_FULL | min_coherence=0.25, cost_mode=SMOOTH | 0.25 有文献锚点(前篇);SMOOTH 与 snaphu_mcf 语义一致 |
| 7 | 时序反演 | mintpy_sbas(推荐)/ pystamps_ps | network=small_baseline, max_temporal_baseline=120 | 120 d 有台账;网络 enum 需护栏 → **修正 C6/C7** |
| 8 | 误差校正 | tropo_era5_pyaps(推荐)/ tropo_gacos / height_corr | ramp=linear, dem_error=True, solid_earth_tides=False | **ramp 默认与上游相悖 → 修正 C1(P0)** |
| 9 | 形变模型 | linear(推荐)/ poly_periodic / step / exponential | poly_order=1, periods=[1,0.5], step_date | 合理;场景覆写机制已具备 |
| 10 | 出图导出 | figure_journal(推荐)/ mintpy_geocode / gdal_warp | dpi=600, cmap=roma, format=png+pdf | dpi/roma 有据;缠绕相位需循环色带 → **修正 C8** |
| 11 | 质检 | crossval_ps_sbas(推荐)/ loop_closure / coherence_mask | corr_threshold=0.85 | 维持 PENDING(前篇结论);loop 阈值可引 1.5 rad |

**修正建议共 8 条(C1–C8),汇总见 §12。**

---

## 1. 第 1 步 数据获取(asf_search_slc / hyp3_submit / local_import)

### 1.1 权威知识源

- **asf_search 官方手册**:Basics / Searching / Downloading / Best Practices,
  <https://docs.asf.alaska.edu/asf_search/basics/>(PyPI: asf-search)。搜索函数
  `geo_search`(WKT)、`granule_search`、`stack_from_id`(基线栈);下载须 Earthdata 认证
  (`ASFSession.auth_with_creds/token`,推荐 .netrc),`download()` 支持 `processes` 并行。[U]
- **HyP3 官方文档与 InSAR Product Guide**:<https://hyp3-docs.asf.alaska.edu/guides/insar_product_guide/>。
  GAMMA 链;多视档 20×4(80 m 像元/160 m 分辨率,**默认**)与 10×2(40 m 像元/80 m 分辨率);
  相干 <0.1 不参与解缠;水掩膜(OSM+ESA WorldCover)默认关;全幅产品用 MCF+三角网、
  **不输出连通分量文件**(Burst InSAR 产品才有 conncomp)。[U]
- **Sentinel-1 产品规格**:SentiWiki "S1 Products",<https://sentiwiki.copernicus.eu/web/s1-products>:
  IW SLC 像元间距 2.3 m(斜距)× 14.1 m(方位),25 s 切片单极化约 3.1 GB,双极化 ×2
  (未压缩全产品 ≈7 GB,见 Sci Data 2022, PMC9279408 "typical unzipped IW SLC ≈7 GB")。[U]
- **数据量下限文献**:Crosetto M., Monserrat O., Cuevas-González M., Devanthéry N., Crippa B. (2016).
  Persistent Scatterer Interferometry: A review. *ISPRS J. Photogramm. Remote Sens.* 115:78-89,
  doi:10.1016/j.isprsjprs.2015.10.011 —— **C 波段 PSI 至少 15–20 景**;另一综述
  (doi:10.1007/s40534-016-0108-4)给 **25 景**保底。SBAS 经典案例 44 景(Berardino et al. 2002,[前篇])。[L]

### 1.2 关键决策点与经验法则

1. **路线选择**:local_import(已有数据,可控性/复现性最好)> asf_search_slc(自控全链)>
   hyp3_submit(免 2–6 步算力,但失去中间产物控制权、限 VV、有配额)。HyP3 产品自带解缠结果,
   直通第 7 步 —— 与 capabilities 第 1 步 unw artifact 的设计一致。
2. **景数规划**:同震单对 2 景即可;SBAS 时序 ≥15–20 景起步;PS ≥20–25 景(Crosetto 2016;
   Ferretti 2001 [前篇])。速度精度随时长收敛:24 天采样约需 **2.2 年**才到 2 mm/yr
   (Morishita et al. 2020 式(4),[前篇])。
3. **搜索防混入**:ASF 目录里每个 SLC 伴随约 27 个衍生产品(OPERA-S1、SLC-BURST),必须限定
   `processingLevel=SLC`(asf_search Best Practices)。[U]
4. **磁盘预算**:IW SLC zip 约 4–4.5 GB,解压后 ≈7–8 GB(双极化);"下载+解压并存"的峰值按 2× 计。

### 1.3 常见失败模式

| 症状 | 根因 | 处置 | 来源 |
|---|---|---|---|
| 下载 401/循环重定向 | Earthdata 未认证或未授权 ASF 应用 | .netrc / auth_with_token;EDL 里授权 "Alaska Satellite Facility" | 文档(asf_search Downloading) |
| zip 校验失败、解包崩 | 下载中断截断 | 重下;核对文件大小/MD5;用 `processes` 并行但控制并发 | 文档(asf_search) |
| 景数虚高、下载错产品 | 搜索未过滤衍生产品(OPERA/BURST) | `processingLevel=SLC`;或 `groupID` 关联搜索 | 文档(Best Practices) |
| HyP3 提交后拿不到产品 | 月度 credit 配额耗尽;产品过保留期被清理 | 查配额;产品生成后及时下载归档(保留期以 HyP3 文档为准) | 文档(HyP3 docs) |
| 时序网络断链(缺景) | AOI 跨 slice/frame,部分日期缺采集 | `stack_from_id` 按基线栈补全;检查 frame 对齐 | 文档(ASF baseline stack) |

### 1.4 参数映射核对

| 参数 | 默认(范围) | 知识源依据 | 判定 |
|---|---|---|---|
| scenes | 7(2–200) | 单对=2(同震);SBAS ≥15–20、PS ≥20–25(Crosetto 2016;Ferretti 2001) | hint 需按场景分层 → **C5** |
| platform | "sentinel-1" | ASF PLATFORM 常量;本项目另支持 ALOS stripmap 场景 | OK |
| dates | "2019-06-10..2019-08-15" | Ridgecrest 实测配置(quake 场景固化值);通用默认无文献语义 | OK(声明示例) |
| source | ""(local_import 目录) | 工程输入,无文献语义 | OK |
| (disk) | scenes×2.4 GB | IW SLC 未压缩 ≈7 GB/景(SentiWiki;Sci Data 2022) | 偏低 → **C4** |

---

## 2. 第 2 步 辅助数据(dem_copernicus / dem_srtm / dem_local + 轨道)

### 2.1 权威知识源

- **Copernicus DEM 产品手册**(GEO1988-CopernicusDEM-SPE-002, i5.0, 2024):GLO-30 绝对垂直精度
  **<4 m(LE90)**,相对精度 <2 m(坡度≤20%)/<4 m(>20%),水平 <6 m(CE90);TanDEM-X 数据
  (2011–2015)+ ICESat 区块平差。<https://dataspace.copernicus.eu/>。[U]
- **全球对比评估**:Del Rosario González-Moradas & Viveen (2023)-类全球评估(JGR Biogeosciences
  128, doi:10.1029/2023JG007672):GLO-30 优于 SRTM/NASADEM;SRTM LE90 ≈8.5 m vs GLO-30 ≈7.7 m
  (LiDAR 对照,洪泛区研究,EGNOS RD4 转引)。[L]
- **SRTM 原始文献**:Farr T.G. et al. (2007). The Shuttle Radar Topography Mission.
  *Rev. Geophys.* 45, RG2004, doi:10.1029/2005RG000183(覆盖 60°N–56°S,高纬缺口)。[L]
- **Sentinel-1 轨道产品**:Copernicus POD Product Handbook + Sentinels 官网 + AWS s1-orbits registry:
  **AUX_POEORB** 时延 ~20 天,精度要求 5 cm 3D RMS(实际常 <1 cm);**AUX_RESORB** 时延 ~180 min,
  要求 10 cm 2D RMS(实际常 <5 cm);POEORB 可用后取代 RESORB。镜像:ASF
  <https://s1qc.asf.alaska.edu/aux_poeorb/> 与 AWS `s1-orbits`。[U]
- **DEM 误差→相位的量级**(判断 4 m DEM 误差是否要紧):地形相位 ∝ B⊥,
  Hanssen R.F. (2001). *Radar Interferometry: Data Interpretation and Error Analysis*. Kluwer(Ch.2/4);
  时序中的残余 DEM 误差校正:Fattahi & Amelung (2013, IEEE TGRS 51(7), [前篇])。
  S1 轨道管 B⊥ 典型 <150 m([前篇] ESA 技术说明),高程模糊度 ~数十至数百 m/条纹,
  4 m 级 DEM 误差对 S1 形变干涉可忽略,对大基线 L 波段则须在第 8 步做 DEM 误差估计。[L]

### 2.2 关键决策点与经验法则

1. **DEM 选型**:默认 Copernicus GLO-30(全球覆盖含 >60°N、精度最好、HyP3 同款);SRTM 仅作
   兼容/复现旧研究用;dem_local 用于无网环境。注意 GLO-30 是 **DSM**(含植被/建筑),城市/森林
   区与地面之间存在系统偏置 —— 对差分干涉影响一阶抵消,对绝对定位/地理编码有残余。[U]
2. **大地水准面改正**:公开 DEM 高程基准是 EGM96/EGM2008 大地水准面,ISCE2/InSAR 需要 **WGS84
   椭球高** —— ISCE2 `dem.py` 与 HyP3 都显式做 geoid correction;漏做会引入平滑的假形变坡面。[U]
3. **轨道策略**:`poeorb` 默认正确;仅当数据 <20 天新时降级 `resorb`,且事后应回补 POEORB 重跑
   配准(两者精度差一个量级,虽同属 cm 级、对 ESD 后相位影响小,但基线/几何配准更稳)。[U]
4. **轨道下载现状**:Copernicus SciHub 已于 2023-10-31 退役,匿名 gnssuser 通道失效;
   用 ASF s1qc 镜像(Earthdata 凭据)或 CDSE(ISCE2 discussion #822)。[论坛/文档]

### 2.3 常见失败模式

| 症状 | 根因 | 处置 | 来源 |
|---|---|---|---|
| fetchOrbit `IndexError: list index out of range` | SciHub 退役后旧脚本拿不到轨道 | 指向 ASF s1qc / CDSE;或 sentineleof 工具 | 论坛(isce2 #406/#822) |
| 新数据只有 RESORB | POEORB 20 天时延未到 | 先 resorb 出初步结果,20 天后回补重跑 | 文档(POD Handbook) |
| 干涉图现平滑假坡面/残余地形纹 | DEM 未做 geoid→椭球改正 | 用 dem.py 流程或核对 HyP3 产品(已改正) | 文档(ISCE2/HyP3) |
| dem_srtm 高纬报错/空洞 | SRTM 覆盖止于 60°N | 换 dem_copernicus | 文档(Farr 2007) |
| DEM 局部 NaN/水体 void | 瓦片缺失或编辑空洞 | 填充(void-fill)或换 GLO-30(已做水体平整) | 文档(Copernicus DEM 手册) |

### 2.4 参数映射核对

| 参数 | 默认(范围) | 知识源依据 | 判定 |
|---|---|---|---|
| dem | "copernicus-30m" | GLO-30 <4 m LE90(产品手册);全球评估最优;HyP3 同款 | OK(推荐正确) |
| orbit | "poeorb" ∈ {poeorb, resorb} | POEORB 5 cm 3D RMS/20 天;RESORB 10 cm 2D RMS/3 h(POD Handbook) | OK;建议在技能文档写明 <20 天降级与回补规则 |

---

## 3. 第 3 步 配准(isce2_tops_geom_esd / isce2_stripmap_xcorr / snap_backgeocoding)

### 3.1 权威知识源

- **TOPS 配准精度要求**:Yagüe-Martínez N. et al. (2016). Interferometric Processing of Sentinel-1
  TOPS Data. *IEEE TGRS* 54(4):2220-2234, doi:10.1109/TGRS.2015.2497902 —— 方位配准精度须
  **≈0.0009 像元**(≈1.9 µs / 1.3 cm)才能把 burst 边界相位误差压到 1/100 周(3.6°);
  常规互相关只能到 0.1 像元,故 **ESD 必需**。同文:常规(条带)干涉 0.1 像元精度即足够。[L]
- **ESD 原始方法**:Prats-Iraola P., Scheiber R., Marotti L., Wollstadt S., Reigber A. (2012).
  TOPS Interferometry with TerraSAR-X. *IEEE TGRS* 50(8):3179-3188。[L]
- **NESD(时序化 ESD)**:Fattahi H., Agram P., Simons M. (2017). A Network-Based Enhanced Spectral
  Diversity Approach for TOPS Time-Series Analysis. *IEEE TGRS* 55(2):777-786,
  doi:10.1109/TGRS.2016.2614925 —— 要求 **<0.001 方位像元**;实测各栈失配 std 1.1–2.0×10⁻³
  像元(≈1.6–2.8 cm),主要来自轨道不确定度;几何配准(精轨+DEM)+ ESD 精化是标准路径。[L]
- **ISCE2 topsApp 上游默认**(applications/topsApp.py,main 分支逐行核对):
  `ESD_COHERENCE_THRESHOLD default=0.85`;`DO_ESD default=True`;ESD 重叠区多视 15(rg)×5(az);
  `NUMBER_RANGE_LOOKS default=19`、`NUMBER_AZIMUTH_LOOKS default=7`;解缠器默认 `icu`、
  `DO_UNWRAP default=False`。[U]
- **SNAP 对应实现**:S1 Back-Geocoding + Enhanced-Spectral-Diversity 算子(SNAP 帮助文档
  SpectralDiversityOp,实现的即 NESD,引 Fattahi 2017)。[U]
- **条带互相关**:ISCE2 stripmapApp(ampcor 密集偏移);精度要求 0.1 像元(Yagüe-Martínez 2016);
  相干损失与失配的经典关系见 Just & Bamler (1994, *Appl. Opt.* 33(20):4361-4368) 与 Hanssen (2001) §4。[L]

### 3.2 关键决策点与经验法则

1. **S1 IW 一律走几何配准+ESD**(默认方法正确):精轨+DEM 几何配准到 ~0.01 像元,ESD 收尾到
   0.001 像元。ESD 相干阈值 0.85 是 ISCE2 默认;**低相干区(植被/农田)ESD 可用样本不足**时,
   优先降阈值(0.75–0.8)或用 NESD 网络化,而不是关 ESD。[U]/[L]
2. **判据**:burst 边界残余相位跳变是配准质量的直读指标 —— 0.001 像元失配 ↔ 相邻 burst 相位差
   <3°(PS-ESD, IEEE GRSL 2022, doi:10.1109/LGRS.2022.3201356 引述)。成品干涉图上肉眼可见的
   burst 条带 = 配准不合格,必须回退本步。[L]
3. **条带链**:互相关窗口要覆盖足够纹理;FBD/FBS 混采集须 `dual2single` 重采样(本仓库
   resample_flag 参数,与 docs/VALIDATION-isce2-wsl.md 实测一致)。[本仓库]
4. **aux_cal 只在老数据需要**:IPF <002.36(约 2015-03 前)的 EAP 天线相位校正;新数据不需要
   (ISCE2 topsStack README)。[U]

### 3.3 常见失败模式

| 症状 | 根因 | 处置 | 来源 |
|---|---|---|---|
| 成品干涉图 burst 边界相位跳变 | ESD 失败/被跳过;方位失配 >0.001 像元 | 降 esd_coherence_threshold、增 ESD looks、查轨道文件;时序栈用 NESD | 论文(Fattahi 2017;Yagüe-Martínez 2016) |
| `No annotation xml file found in ...SAFE` | 极化不匹配(HH 数据按默认 VV 找不到) | 显式设置 polarization(reference/secondary 两节都要) | 论坛(isce2 #495) |
| 老数据相位系统偏移 | IPF<002.36 缺 AUX_CAL(EAP 校正) | 从 sar-mpc.eu 下载 AUX_CAL 并配置目录 | 文档(topsStack README) |
| stripmap 偏移场稀疏/拟合发散 | 失相干强、初始偏移超搜索窗 | 加大 ampcor 搜索窗;先几何初值;挑高相干对 | 文档(stripmapApp)+本仓库实测 |
| 产物为空/崩:bbox 与 swath 不交 | ROI 设置错、swath 选择错 | 核对 bbox(S,N,W,E 顺序)与 swaths | 论坛(isce2 常见问答) |

### 3.4 参数映射核对

| 参数 | 默认(范围) | 知识源依据 | 判定 |
|---|---|---|---|
| esd_coherence_threshold | 0.85(0–1) | ISCE2 topsApp `ESD_COHERENCE_THRESHOLD default=0.85`(源码核对);contract.yaml 已 OK | OK;技能文档补"低相干降到 0.75–0.8"启发式 |
| reference/secondary_image, *_leader, dem_path | 路径 | 工程输入;形态=docs/VALIDATION-isce2-wsl.md 实测 | OK |
| resample_flag | "" ∈ {"", dual2single} | ALOS FBD(14 MHz)从影像配 FBS(28 MHz)主影像须重采样(ISCE2 stripmapApp 惯例) | OK |
| threads | 8(1–32,resource) | 工程资源参数 | OK |

---

## 4. 第 4 步 干涉(isce2_ifg_multilook / snap_interferogram / isce2_stripmap_ifg)

### 4.1 权威知识源

- **多视与相位噪声**:Rodriguez E. & Martin J.M. (1992). Theory and design of interferometric
  synthetic aperture radars. *IEE Proc.-F* 139(2):147-159(相位方差 CRB:σφ ∝ (1/√(2N_L))·√(1-γ²)/γ);
  Just D. & Bamler R. (1994). Phase statistics of interferograms with applications to synthetic
  aperture radar. *Appl. Opt.* 33(20):4361-4368;Hanssen (2001) §4.2。多视数 N_L 每 ×4,
  相位 std 约减半 —— 分辨率换信噪比的一阶规则。[L]
- **S1 像元几何**:IW SLC 2.3 m(斜距)×14.1 m(方位)(SentiWiki S1 Products)→ 近似方形
  地面像元需 **rg:az ≈ 5:1**;HyP3 两档 20×4(80 m)/10×2(40 m)(HyP3 Product Guide);
  LiCSAR 全球产品 20×4([前篇] Morishita 2020);ISCE2 topsApp 源码默认 19×7(偏保守的方位平滑)。[U]
- **相干估计偏差**:Touzi R., Lopes A., Bruniquel J., Vachon P.W. (1999). Coherence estimation
  for SAR imagery. *IEEE TGRS* 37(1):135-149 —— 小窗口/低相干时相干估计正偏,阈值判断要在
  多视后的一致窗口下解释。[L]
- **短基线网络的系统偏差(fading signal)**:Ansari H., De Zan F., Parizzi A. (2021). Study of
  Systematic Bias in Measuring Surface Deformation With SAR Interferometry. *IEEE TGRS*
  59(2):1285-1301, doi:10.1109/TGRS.2020.3003421 —— 纯短时基线网络(如 sequential-5)在 4 年
  S1 栈上引入 **−6.5 mm/yr** 速度偏差;混入长基线对/相位链接后降到 **−0.24 mm/yr**。
  理论根源:De Zan F., Zonno M., López-Dekker P. (2015). Phase Inconsistencies and Multiple
  Scattering in SAR Interferometry. *IEEE TGRS* 53(12):6608-6616。[L]
- **网络构建惯例**:[前篇] §5(Berardino 2002 bperp<130 m;S1 轨道管使 bperp 非约束;
  ASF Ridgecrest 教程 24 天/11 对;MintPy 默认不限+数据驱动修剪)。

### 4.2 关键决策点与经验法则

1. **多视比按传感器几何定,不是自由旋钮**:S1 IW 取 rg:az≈5:1(10×2、20×4 合规);
   ALOS 条带 FBS 方位像元(~3.2 m)远小于地面距离像元(~7 m 级),**比例须倒置(az>rg)**,
   直接沿用 10×2 会得到严重矩形化像元与方向性噪声 → 修正 **C3**。[U]/[L]
2. **多视强度与解缠联动**:强多视(80 m 级)利于低相干区解缠与 LiCSAR 式全球处理;
   精细多视(40 m 级)保形变梯度细节(同震近场)。先定应用需求再定档位,勿中途换档
   (换档 = 全下游重跑 + 阈值语义漂移)。[C]
3. **网络设计防两头**:对数太少 → 断链(n_gap)与反演欠定;纯短基线 → fading 偏差
   (Ansari 2021)。健康网络 = 序贯短基线为骨架 + 适量长基线对 + 保留 MST 连通([前篇] §5.2)。[L]
4. **pairs=11 是 Ridgecrest 教程固化值**(ASF HyP3 教程 24 天阈值下的产物数,[前篇]),
   通用场景应由第 7 步网络参数派生而非手工指定。[C]

### 4.3 常见失败模式

| 症状 | 根因 | 处置 | 来源 |
|---|---|---|---|
| 干涉图有方向性拖尾/像元矩形化 | 多视比与传感器像元几何不匹配 | 按 rg:az 像元间距重定(S1 5:1;ALOS 倒置) | 文档(SentiWiki)+论文(Hanssen §4) |
| 同震近场条纹糊成一片 | 条纹率超奈奎斯特 + 强多视平均 | 降多视/近场单独低多视处理;或用偏移量场补充 | 教科书(Hanssen)+社区惯例 |
| 时序速度系统偏移(无明显误差源) | 纯短基线网络 fading 偏差 | 网络加长基线对;或换相位链接类方法 | 论文(Ansari 2021) |
| 反演期发现网络断链 | 阈值剪枝过狠/缺景 | 保留 MST;检查 n_gap;补长基线桥接对 | 文档(MintPy cfg §2;LiCSBAS) |
| 磁盘峰值超预算 | 全分辨率中间产物(merged/)未清理 | 预算按 peak_multiplier;分段清理 | 本仓库(VALIDATION 实测 32 GB 峰值) |

### 4.4 参数映射核对

| 参数 | 默认(范围) | 知识源依据 | 判定 |
|---|---|---|---|
| range_looks | 10(1–40) | =HyP3 10×2 精档(40 m 像元);S1 rg:az≈5:1 合规(SentiWiki 像元间距) | S1 OK;stripmap 须倒置 → **C3** |
| azimuth_looks | 2(1–40) | 同上 | 同上 |
| pairs | 11(1–5000) | ASF Ridgecrest HyP3 教程固化(24 天阈值,[前篇]) | OK(quake 场景);通用场景应由网络参数派生 |
| threads | 8(resource) | 工程参数 | OK |

---

## 5. 第 5 步 滤波(goldstein / boxcar / none / isce2_stripmap_filter)

### 5.1 权威知识源

- **Goldstein 滤波原文**:Goldstein R.M. & Werner C.L. (1998). Radar interferogram filtering for
  geophysical applications. *GRL* 25(21):4035-4038 —— 频域谱加权指数 α∈[0,1];原文以解缠
  残差点(residues)计数作为滤波收益的定量指标。[L]
- **自适应改进**:Baran I. et al. (2003). A modification to the Goldstein radar interferogram
  filter. *IEEE TGRS* 41(9):2114-2118(α = 1 − 平均相干,防高相干区过滤)。[L]
- **各软件默认值**([前篇] §2.1 已全部核对源码):ISCE2 topsApp `FILTER_STRENGTH=0.5`(本次
  再次源码确认);SNAP 1.0(IGARSS 2023, doi:10.1109/IGARSS52108.2023.10282000 批评过强,
  建议 0.5–0.6);HyP3 adf **0.6**(本次产品指南原文再确认:"Phase filter parameter |
  Dampening factor | 0.6");MintPy Galápagos 案例 0.2。[U]
- **滤波的告诫**:滤波是不可逆的相位改写;MintPy 载入注释警告平滑会破坏 2π 整数关系([前篇] §2.2)。
  PS 链不滤波(点目标相位会被邻域平均破坏,StaMPS 工作流,Hooper et al. 2004/2007)。[U]/[L]

### 5.2 关键决策点与经验法则

([前篇] §2.2 场景表直接适用:城市/裸岩 0.2–0.5;植被低相干 0.6–1.0;大梯度宁多视勿强滤;
自适应 α=1−γ̄。)本篇补充:

1. **调参判据用残差点计数**(Goldstein & Werner 1998 原始做法):滤波前后 residue 数量下降
   比例是客观指标,比目视条纹清晰度可靠,可进 QA 日志。[L]
2. **boxcar 的定位**:纯移动平均,快但边缘糊、破坏梯度;仅在粗看/低要求出图用;正式链
   goldstein 或 none(PS)。[C]
3. **α 与 FFT 窗口联动**(IGARSS 2023:两者是最敏感参数对);跨软件比较 α 时先对齐前置多视。[L]

### 5.3 常见失败模式

| 症状 | 根因 | 处置 | 来源 |
|---|---|---|---|
| 十字状伪影、假条纹 | α 过大(尤其 SNAP 默认 1.0)+ 大 FFT 窗 | α 降到 0.5–0.6;缩窗 | 论文(IGARSS 2023) |
| 解缠残差点仍密集 | 欠滤波(α 过小)或噪声本底太高 | 提 α;或回第 4 步加多视 | 论文(Goldstein & Werner 1998) |
| 高相干区细节被抹平 | 固定强 α 作用于高相干区 | Baran 自适应 α=1−γ̄;或分区 α | 论文(Baran 2003) |
| PS 点相位失稳 | 对点目标做了空间滤波 | PS 链选 none;滤波只用于 SBAS 面状链 | 文档(StaMPS 工作流) |

### 5.4 参数映射核对

| 参数 | 默认(范围) | 知识源依据 | 判定 |
|---|---|---|---|
| alpha | 0.4(0–1) | ISCE2 0.5 / HyP3 0.6 / MintPy 示例 0.2 之间([前篇] §2.1) | 值合理;但与 filter_strength 冲突 → **C2** |
| filter_strength | 0.5(0–1) | ISCE2 `FILTER_STRENGTH default=0.5` **就是** Goldstein α(源码同一参数) | 与 alpha 重复且默认不同 → **C2** |

---

## 6. 第 6 步 解缠(snaphu_mcf / snaphu_smooth / icu / 3D_FULL / stripmap 内置)

### 6.1 权威知识源

- **SNAPHU 三部曲**:Chen C.W. & Zebker H.A. (2000). Network approaches to two-dimensional phase
  unwrapping. *JOSA A* 17(3):401-414;(2001). Two-dimensional phase unwrapping with use of
  statistical models for cost functions. *JOSA A* 18(2):338-351;(2002). Phase unwrapping for
  large SAR interferograms: statistical segmentation and generalized network models. *IEEE TGRS*
  40(8):1709-1719。官方 man page 与 snaphu.conf.full([前篇] §3 已核对:TOPO/DEFO/SMOOTH 语义、
  DEFOMAX_CYCLE=1.2、tile 参数、内存 ~100 MB/百万像素)。[L]/[U]
- **snaphu_mcf 的真实语义**([前篇]源码核对):ISCE2 `runUnwrapMcf = SMOOTH 代价 + MCF 初始化 +
  initOnly=True`;MCF 是初始化算法不是 cost mode。[U]
- **分支切割原文**:Goldstein R.M., Zebker H.A., Werner C.L. (1988). Satellite radar
  interferometry: Two-dimensional phase unwrapping. *Radio Science* 23(4):713-720。[L]
- **ICU 无正式论文**:ISCE2 官方讨论区(isce-framework/isce2 Discussions #447、#700)确认:
  ICU = Goldstein 1988 分支切割的工程强化(neutrons 引导切割 + 低相干掩膜 + 分块自举),
  "fast, works well if coherence is high and the data does not have a lot of fringes";
  snaphu_mcf "slow but can unwrap low coherence... always manually check quality"。[C(官方论坛)]
- **3D 解缠**:Hooper A. & Zebker H.A. (2007). Phase unwrapping in three dimensions with
  application to InSAR time series. *JOSA A* 24(9):2737-2747(StaMPS 时序解缠的理论基础)。[L]
- **HyP3 产品对照**:GAMMA MCF+三角网,无 conncomp 文件(全幅产品);相干 <0.1 掩膜出解缠;
  水掩膜显著改善跨水体解缠(Product Guide 原文核对)。[U]
- **解缠误差检测/修正**:Yunjun et al. (2019) §3(闭合相位整数模糊 T_int;bridging/phase_closure
  修正);LiCSBAS loop RMS 1.5 rad 图级剔除([前篇] §3.3)。[L]/[U]

### 6.2 关键决策点与经验法则

1. **方法选型三分法**(ISCE2 #700 + snaphu man):高相干少条纹 → icu(快);低相干/碎块 →
   snaphu_mcf(稳,默认正确);对代价函数有精调需求(同震近场跨断层)→ snaphu 完整优化
   (DEFO + DEFOMAX>0),而不是换初始化。[C]/[U]
2. **cost_mode 场景规则**([前篇] §3.1):形变应用永远不用 TOPO;缓变形变(震间/冻土/蠕滑)
   SMOOTH;同震近场允许不连续 → DEFO(DEFOMAX_CYCLE 默认 1.2 周)。[U]
3. **掩膜层级**:解缠有效掩膜宜宽(0.1–0.3,HyP3 用 0.1;本项目 0.25 = Berardino/GIAnT 惯例点,
   [前篇] §1.2);水体必须掩(HyP3:水面偶发假相干会传播解缠错误)。[U]
4. **大幅面**:tile 模式 + 数十像元 overlap + `-S` 单幅重优化([前篇] §3.2);内存预算
   ~100 MB/Mpix(snaphu 主页)。[U]
5. **质检**:conncomp 数量与最大连通域占比、unwrap_coverage(本项目 gate,PENDING 0.70,
   社区下限 0.3,[前篇])、闭合环 T_int 图(Yunjun 2019)。快速人工判据:解缠相位对 2π 取模
   应还原干涉条纹,断层/水体外不应有阶梯状台地。[L]/[U]

### 6.3 常见失败模式

| 症状 | 根因 | 处置 | 来源 |
|---|---|---|---|
| 解缠孤岛/大片 2π 台地 | ICU 在低相干区断链;或掩膜过严切碎区域 | 换 snaphu_mcf;放宽掩膜到 0.1–0.25;水体掩膜 | 论坛(isce2 #700)+文档(HyP3) |
| 跨断层相位跳变整周错 | SMOOTH 禁止不连续,强行平滑 | DEFO 模式 + DEFOMAX≈1.2;或沿断层分区解缠 | 文档(snaphu man page) |
| 大幅面内存耗尽/极慢 | 单 tile 全图优化 | `--tile r c 30 30 --nproc N`;`-S` 重优化 | 文档(snaphu man/conf.full) |
| 零相干区也被"解"出平滑场 | snaphu 无条件外推 | 事后按相干/conncomp 掩膜;闭合环检查 | 论坛(isce2 #700)+论文(Yunjun 2019 §3) |
| 跨水体相位桥接错误 | 水面偶发假相干 | 水掩膜进解缠(HyP3 做法);或 conncomp 分量分治 | 文档(HyP3 Product Guide) |

### 6.4 参数映射核对

| 参数 | 默认(范围) | 知识源依据 | 判定 |
|---|---|---|---|
| min_coherence | 0.25(0–1) | Berardino 2002 §V 明文 + GIAnT 惯例;HyP3 解缠掩膜下界 0.1(contract 已 literature/OK) | OK |
| cost_mode | "SMOOTH" ∈ {SMOOTH, DEFO, TOPO} | snaphu man page 语义;与 snaphu_mcf(SMOOTH+MCF+initOnly)一致;quake 近场覆写 DEFO | OK;技能文档写明 DEFO 触发条件 |
| threads | 8(resource) | snaphu tile `--nproc` 对应 | OK |
| (gate) unwrap_coverage | 0.70(PENDING) | LiCSBAS 下限 0.3;本项目从严([前篇]) | 维持 PENDING,不新增修正 |

---

## 7. 第 7 步 时序反演(mintpy_sbas / pystamps_ps)

### 7.1 权威知识源

- **MintPy 主文献**:Yunjun Z., Fattahi H., Amelung F. (2019). Small baseline InSAR time series
  analysis: Unwrapping error correction and noise reduction. *Computers & Geosciences* 133:104331,
  doi:10.1016/j.cageo.2019.104331。**smallbaselineApp.cfg 上游默认**(本次原文核对):
  `weightFunc=var`(官方注释:经典 SBAS = minNormVelocity yes + weightFunc no);
  `minRedundancy=1.0`;`minTempCoh=0.7`;`minNumPixel=100`;`maskThreshold=0.4`(默认不启用
  空间相干掩膜);网络默认不限 temp/perp 基线。[U]/[L]
- **SBAS 原文**:Berardino P., Fornaro G., Lanari R., Sansosti E. (2002). *IEEE TGRS*
  40(11):2375-2383, doi:10.1109/TGRS.2002.803792。[L]
- **时间相干**:Pepe A. & Lanari R. (2006). *IEEE TGRS* 44(9):2374-2383(0.7 惯例源头)。[L]
- **PS 路线**:Ferretti A., Prati C., Rocca F. (2001). Permanent scatterers in SAR interferometry.
  *IEEE TGRS* 39(1):8-20(D_A≤0.25 选点;<1 mm/yr 精度);Hooper A., Zebker H., Segall P.,
  Kampes B. (2004). A new method for measuring deformation on volcanoes and other natural terrains
  using InSAR persistent scatterers. *GRL* 31:L23611, doi:10.1029/2004GL021737 —— **StaMPS 候选
  D_A 阈值 0.4**(相位稳定性迭代精选;StaMPS `mt_prep` 帮助原文:"typical values: 0.4 for PS,
  0.6 for SB");Hooper et al. (2012). Recent advances in SAR interferometry time series analysis.
  *Tectonophysics* 514-517:1-13(综述)。[L]/[U]
- **参考点准则**(MintPy reference_point.py 官方文档原文):1) 不在形变区;2) 不受强大气湍流/
  电离层条纹影响;3) 贴近 AOI 且高程相近(压制空间相关大气);4) 高相干区。自动法:相干 ≥0.85
  随机选(`mintpy.reference.minCoherence=0.85`)。[U]
- **网络类型语义**:MintPy `select_network.py` 支持 sequential/star(=单参考,PS 式)/delaunay/
  mst/hierarchical;fading 偏差约束见 Ansari 2021(§4.1 已引:sequential-5 → −6.5 mm/yr)。[U]/[L]

### 7.2 关键决策点与经验法则

1. **SBAS vs PS**:面状缓变低相干 → SBAS;高相干点状目标(城市/岩体/基础设施)→ PS;
   两者互检是本项目第 11 步的立身之本。PS 最少景数 15–25(Crosetto 2016),D_A 候选 0.4
   (StaMPS)/0.25(Ferretti 严选)。[L]
2. **网络反演配置**:MintPy 默认 `var` 加权即可;要复现经典 SBAS 才用 `no` 均权(本项目
   contract weightFunc="no" 有意覆写,台账已注明语义)。反演后以 `minTempCoh=0.7` 掩膜
   (冗余弱时升 0.8,Yunjun §6.5.5,[前篇])。[U]/[L]
3. **网络健康三查**:每景冗余 ≥1(minRedundancy 下限);n_gap=0(断链数);
   纯短基线偏差风险(Ansari)→ 混长基线对。[U]/[L]
4. **max_temporal_baseline=120 天**:contract 已 literature/OK(宽松侧 + 数据驱动修剪兜底,
   [前篇]);冻土场景收紧(跨冻融季失相干)。[L]
5. **参考点是全链条基准**:选错参考点 = 全场速度平移+大气泄漏;按 §7.1 四准则,且必须落在
   maskConnComp 有效区内。[U]

### 7.3 常见失败模式

| 症状 | 根因 | 处置 | 来源 |
|---|---|---|---|
| `ValueError: input reference point is in masked OUT area defined by maskConnComp.h5` | 参考点落在低相干/连通分量外 | `reference_point.py --reset` 后按 avgSpatialCoh/maskConnComp 手选;或剔除烂图扩大有效区 | 论坛(MintPy #1188、#216) |
| `RuntimeError: Not enough reliable pixels (minimum of 100)` | 网络质量差 → 时间相干普遍 <0.7 | 修网络(剔烂图/加对)优先;实在不行降 minTempCoh(如 0.6)并在报告声明 | 论坛(MintPy Google Groups) |
| 内存溢出(大栈反演) | 全图一次性载入 | `mintpy.compute.memorySize`/分块;或裁剪 AOI | 论坛(MintPy #216) |
| 速度场整体系统偏差 | 纯短基线网络 fading 偏差 | 加长基线对重反演;对比两版速度差 | 论文(Ansari 2021) |
| star 网络整链崩坏 | 单参考景失相干/含大气异常 | star 仅 PS 链用;SBAS 保持冗余网络 | 文档(select_network 语义)+论文(Berardino 2002) |

### 7.4 参数映射核对

| 参数 | 默认(范围) | 知识源依据 | 判定 |
|---|---|---|---|
| network | "small_baseline" ∈ {small_baseline, star, sequential} | Berardino 2002(SB);MintPy select_network(star=单参考=PS 式;sequential=近邻连接);Ansari 2021(纯短基线偏差) | 默认 OK;enum 缺护栏 → **C7** |
| max_temporal_baseline | 120 天(6–730) | contract literature/OK([前篇] 映射表 a) | OK |
| (缺) max_perp_baseline | — | MintPy `perpBaseMax`;Berardino 130 m(ERS);ALOS <1800 m(Yunjun §5.1);S1 轨道管使其非约束 | 缺参数 → **C6** |
| parallel_workers | 4(1–16,resource) | MintPy compute 并行 | OK |

---

## 8. 第 8 步 误差校正(tropo_era5_pyaps / tropo_gacos / tropo_height_corr + dem_error/SET/ramp)

### 8.1 权威知识源

- **误差量级与必要性**:Zebker H.A., Rosen P.A., Hensley S. (1997). Atmospheric effects in
  interferometric synthetic aperture radar surface deformation and topographic maps. *JGR*
  102(B4):7547-7563(湿度 20% 变化 → ~10 cm 形变误差)。[L]
- **ERA5 本体**:Hersbach H., Bell B., Berrisford P., et al. (2020). The ERA5 global reanalysis.
  *QJRMS* 146(730):1999-2049, doi:10.1002/qj.3803(~31 km 格网、137 层、逐小时)。[L]
- **PyAPS**:Jolivet R. et al. (2011). *GRL* 38:L17311(GAM 校正框架);Jolivet R. et al. (2014).
  Improving InSAR geodesy using Global Atmospheric Models. *JGR* 119:2324-2341(效果由湍流水平
  决定;LA 方差 −70%)。MintPy 默认 `pyaps+ERA5`("recommended and turn ON by default",
  cfg §8 原文;GAM 延迟日期自动跳过校正 —— cfg 注释原文)。[L]/[U]
- **GACOS**:Yu C., Li Z., Penna N.T., Crippa P. (2018). *JGR* 123:9202-9222,
  doi:10.1029/2017JB015305;官网 <http://www.gacos.net>(ITD 模型;ECMWF HRES 0.1°/6 h;
  输出 90 m ZTD 格网;**单次请求限 10°×10°、20 个日期**;产品自带可行性指标,官方立场
  "先看指标再决定是否用")。[L]/[U]
- **高程相关经验校正**:Doin M.-P. et al. (2009). *J. Applied Geophysics* 69:35-50;MintPy
  `height_correlation`(polyOrder=1,额外多视 8 —— cfg §8 原文);**无法区分地形相关真形变**
  (cfg 注释 + Yunjun 2019 §4.6,[前篇])。[L]/[U]
- **DEM 误差(地形残差)**:Fattahi H. & Amelung F. (2013). DEM error correction in InSAR time
  series. *IEEE TGRS* 51(7):4249-4259;MintPy `topographicResidual=yes` 默认开(cfg §10)。[L]/[U]
- **固体潮**:MintPy `solidEarthTides=no` 默认关;参考 Milbert (2018, SOLID 程序) 与
  Yunjun Z. et al. (2022, IEEE TGRS,pysolid;S1 大区域长时序才显著)。[U]/[L]
- **去 ramp**:MintPy cfg §9 原文:"Recommended for localized deformation signals...
  **NOT recommended for long spatial wavelength deformation signals, i.e. co-, post- and
  inter-seismic deformation**";默认 `no`。[U]
- **电离层(L 波段场景)**:Gomba G. et al. (2016). Toward Operational Compensation of Ionospheric
  Effects in SAR Interferograms: The Split-Spectrum Method. *IEEE TGRS* 54(3):1446-1461(米级
  电离层误差可校到 cm–mm);MintPy cfg §7 注明 topsApp 用 Liang et al. 2019、stripmapApp 用
  Fattahi et al. 2017 的实现,默认关。[L]/[U]

### 8.2 关键决策点与经验法则

1. **方法优先级**(证据等级递减):GACOS/ERA5(有外部物理模型)→ height_correlation(经验,
   降级须报告声明)。GACOS 与 ERA5 谁更好因区域而异 —— GACOS 融合 GNSS 且分辨率高
   (0.1°+90 m 输出),但依赖服务可用性;ERA5 全自动可缓存。官方判据:GACOS 产品自带指标
   (GNSS/ECMWF 交叉 RMS、相位-延迟相关)决定是否采用(Yu 2018 §5)。[L]
2. **湍流主导时 GAM 校正可能恶化**(Jolivet 2014;[前篇] §4.2 天山案例):校正后 std 反升
   即回退不用 —— 这是可执行的 QA 规则(比较校正前后各干涉图空间 std)。[L]
3. **ramp 与形变信号的张力**:同震/震间等长波长信号 **禁 deramp**(MintPy cfg §9);
   局地形变(滑坡/沉降/火山)可 linear。本仓库默认 linear 与旗舰 quake 场景冲突 → **C1**。[U]
4. **dem_error=True 正确**(Fattahi & Amelung 2013;MintPy 默认 yes);大 bperp(L 波段)时
   尤其必要([前篇] §5.1)。solid_earth_tides=False 与 MintPy 默认一致;开启前须先解决
   conda-forge pysolid 在 Windows 的 DLL 问题(capabilities 注释,本仓库)。[U]/[本仓库]
5. **顺序不可乱**:tropo → deramp → topographic residual 是 MintPy smallbaselineApp 的固定步序
   (correct_troposphere §8 → deramp §9 → correct_topography §10),自定义链路时保持一致,
   否则 ramp 会吸收部分对流层分层项使归因混乱。[U]

### 8.3 常见失败模式

| 症状 | 根因 | 处置 | 来源 |
|---|---|---|---|
| pyaps 下载报错/卡死 | CDS API key 未配置或 license 未接受;CDS 队列拥堵 | 配置 ~/.cdsapirc 并在网页接受 ERA5 license;预下载缓存 ERA5.h5(本仓库第 1 步已设 era5 缓存 artifact) | 文档(PyAPS/CDS)+本仓库 |
| 最近几景没有校正量 | ERA5 数据延迟(初步版 ERA5T 约滞后 5 天,最终版数月) | MintPy 行为:有 GAM 的日期校正、无的跳过(cfg §8 注释);报告标注未校正日期 | 文档(ECMWF CDS;MintPy cfg) |
| GACOS 请求被拒/久无邮件 | 超出 10°×10°/20 日期限制;服务波动 | 分批请求;或改走 ERA5;检查官网服务公告 | 文档(gacos.net) |
| 校正后噪声反增 | 湍流主导区,GAM 分层模型不适用 | 校正前后空间 std 对比,恶化则回退;考虑 GACOS 指标判据 | 论文(Jolivet 2014;Yu 2018) |
| 火山/冻土坡形变被"校正"掉 | height_correlation 把地形相关真形变当大气扣除 | 降级链使用时在报告显式声明;优先物理模型法 | 文档(MintPy cfg §8)+论文(Yunjun 2019 §4.6) |
| 输出文件名与预期不符 | MintPy 按开启的校正组合动态命名(ERA5/ramp/demErr) | capabilities 已列全候选(本仓库注:InSAR_Agent mintpy.py 教训) | 本仓库 |

### 8.4 参数映射核对

| 参数 | 默认(范围) | 知识源依据 | 判定 |
|---|---|---|---|
| ramp | "linear" ∈ {no, linear, quadratic} | MintPy 默认 **no**;官方注释:同震/震后/震间长波长信号不推荐 deramp(cfg §9 原文) | **默认不合理 → C1(P0)** |
| dem_error | True | MintPy `topographicResidual=yes` 默认;Fattahi & Amelung 2013 | OK |
| solid_earth_tides | False | MintPy `solidEarthTides=no` 默认;Milbert 2018 / Yunjun 2022;Windows DLL 待验证(本仓库注) | OK |

---

## 9. 第 9 步 形变模型(linear / poly_periodic / step / exponential)

### 9.1 权威知识源

- **时间函数框架**:Hetland E.A. et al. (2012). Multiscale InSAR time series (MInTS) analysis of
  surface deformation. *JGR* 117:B02404(MintPy timeFunc 的式(2)-(9) 出处,cfg §12 引用);
  MintPy `timeseries2velocity`:polynomial + periodic(1,0.5)+ stepDate + exp/log。[L]/[U]
- **不确定度**:residue 法(Fattahi H. & Amelung F. 2015. InSAR bias and uncertainty due to the
  systematic and stochastic tropospheric delay. *JGR* 120:8758-8773 —— MintPy cfg §12 引用);
  bootstrap(Efron & Tibshirani 1986,默认 400 次)。[L]/[U]
- **同震阶跃 / 震后对数-指数 / 冻土周期与 Stefan 模型 / 滑坡蠕滑线性+残差报警**:
  [前篇] §6.1–6.4 已完整核对(Marone 1991;Ingleby & Wright 2017;Tobita 2016;Sobrero 2020;
  Daout 2017;Li et al. 2019;Liu et al. 2012;Colesanti & Wasowski 2006),本篇不重复。

### 9.2 关键决策点与经验法则

1. **模型形态由机理定,参数由数据定**:linear 是默认底座;quake 场景必须加 step(否则阶跃
   摊成假趋势,MintPy cfg §12 注释);震后加 log/exp(τ 网格搜索,报告残差不断言机理);
   冻土 poly_periodic(periods=[1,0.5],半年项吸收非正弦不对称)。[L]/[U]
2. **周期项需 ≥2 个完整年循环**;<14 个月线性与季节不可分离(Li 2019,[前篇] §6.3)。[L]
3. **poly_order>1 慎用**:高阶多项式会拟合掉加速/异常信号 —— 加速形变应表现为线性模型残差
   系统增大并触发报警(滑坡纪律,[前篇] §6.4),而不是提阶吸收。[L]
4. **残差历元剔除**:去二次趋势后 RMS >3×MAD 判噪声历元(Yunjun 2019 §4.9;MintPy
   residualRMS cutoff=3 默认,本次原文核对)。[L]/[U]

### 9.3 常见失败模式

| 症状 | 根因 | 处置 | 来源 |
|---|---|---|---|
| 速度图上断层两侧对称假趋势 | quake 数据只拟 linear,阶跃摊平 | 加 step(date)=主震时刻(contract stepFuncDate 已 OK) | 文档(MintPy cfg §12) |
| log 与 exp 拟合优度几乎相同 | 短观测窗内二者不可辨识 | 固定 τ 网格搜索;报告残差与参数相关性,不断言机理 | 论文(Sobrero 2020;Tobita 2016) |
| 季节振幅/相位不稳定 | 数据 <2 个年循环 | 延长时段或不报周期项 | 论文(Li 2019;Daout 2017) |
| 残差 RMS 个别历元爆高 | 大气异常/解缠错误历元 | 3×MAD 剔除并回查第 6/8 步 | 论文(Yunjun 2019 §4.9) |

### 9.4 参数映射核对

| 参数 | 默认(范围) | 知识源依据 | 判定 |
|---|---|---|---|
| poly_order | 1(0–3) | MintPy polynomial 默认 1;高阶风险见 §9.2.3 | OK;技能文档加"提阶须给理由"护栏 |
| periods | [1, 0.5] 年 | MintPy periodic 1,0.5;半年项依据 Daout 2017([前篇] §6.3) | OK |
| step_date | ""(YYYYMMDD(THHMM)) | MintPy stepDate 语法;Ridgecrest 20190706T0320 已入 contract | OK |

---

## 10. 第 10 步 出图导出(figure_journal / mintpy_geocode / gdal_warp)

### 10.1 权威知识源

- **色带科学**:Crameri F., Shephard G.E., Heron P.J. (2020). The misuse of colour in science
  communication. *Nature Communications* 11:5444, doi:10.1038/s41467-020-19160-7 ——
  感知均匀、色盲安全、黑白打印可读;rainbow/jet 类失真。**Scientific colour maps**
  (Crameri,Zenodo 存档)含 sequential/diverging/**cyclic** 三类:roma(diverging)适合
  速度场(有物理零点),**缠绕相位是循环量应配 cyclic 的 romaO**。[L]
- **分辨率规范**(出版社原文核对):Elsevier —— 半色调 ≥300 dpi、线图+半色调组合 ≥500 dpi、
  纯线图 ≥1000 dpi;Wiley —— 线图 600 dpi/照片 300 dpi;PLOS 300–600 dpi;分辨率按**最终版面
  尺寸**衡量。InSAR 速度图属"组合图",600 dpi 落在 500–1200 的合规带内。[U]
- **地理编码/导出**:MintPy `geocode.py` + `save_gdal.py`(GeoTIFF);HyP3 产品原生 UTM
  GeoTIFF(Product Guide);GDAL warp 重投影。[U]

### 10.2 关键决策点与经验法则

1. **按数据类型选色带**:速度/形变(有零点)→ diverging(roma/vik);相干/振幅(单调)→
   sequential(batlow/lajolla);**缠绕相位 → cyclic(romaO)**,否则 ±π 处出现假边界。[L]
2. **速度图三要素**:参考点标注、色标单位(mm/yr,LOS 方向定义)、比例尺/指北 ——
   LOS 语义(正=朝卫星)必须写在图注,升降轨混用时尤其易错。[C]
3. **导出双轨**:出版用 png+pdf(矢量文本);GIS 用 GeoTIFF(带 CRS 元数据,UTM 或 EPSG:4326),
   两者并出与 capabilities 的 figure_journal/gdal_warp 分工一致。[U]
4. **dpi=600 默认合理**;若图内含大量线划注记,按 Elsevier 组合图标准≥500、纯线图导出可升
   1000–1200。[U]

### 10.3 常见失败模式

| 症状 | 根因 | 处置 | 来源 |
|---|---|---|---|
| 审稿人质疑色带/色盲不可读 | jet/rainbow 类感知失真色带 | 换 Scientific colour maps(roma/vik/batlow) | 论文(Crameri 2020) |
| 缠绕相位图 ±π 处假条带 | 非循环色带用于循环量 | romaO/cyclic 色带 | 文档(Scientific colour maps 分类) |
| 投稿被打回重做图 | dpi 低于出版社下限(或超大文件被压缩) | 按 300/500-600/1000 三档核对;文本用矢量 | 文档(Elsevier/Wiley 规范) |
| GeoTIFF 在 GIS 中错位 | 缺 CRS/geotransform 或 geo/radar 坐标混淆 | save_gdal 前先 geocode;核对 EPSG | 文档(MintPy save_gdal) |

### 10.4 参数映射核对

| 参数 | 默认(范围) | 知识源依据 | 判定 |
|---|---|---|---|
| dpi | 600(72–1200) | Elsevier 组合图 ≥500/线图 ≥1000;Wiley 线图 600;PLOS ≤600 | OK |
| cmap | "roma" | Crameri Scientific colour maps(diverging,适合速度场) | OK;缠绕相位产物应 romaO → **C8** |
| format | "png+pdf" | 出版惯例(位图预览+矢量正稿) | OK |

---

## 11. 第 11 步 质检(crossval_ps_sbas / loop_closure / coherence_mask)

### 11.1 权威知识源

([前篇] §7 已完整核对,此处列结论性锚点。)

- **速度精度参照系**:SBAS ≈1 mm/yr(Casu et al. 2006);PS <1 mm/yr(Ferretti 2001);
  S1 数年数据 InSAR−GNSS 速度差 STD 收敛至 ≈2 mm/yr(Morishita et al. 2020 式(4));
  EGMS 产品规格速度 STD 0.7 mm/yr、GNSS 验证绝大多数 <2 mm/yr。[L]/[U]
- **时序 RMSE 参照系**:强形变场景 InSAR vs GNSS RMSE 0.5–1.8 cm、坏点由时间相干 <0.7 预先
  识别(Yunjun 2019 §5.1 Fig.8)。[L]
- **闭合环**:图级环 RMS >1.5 rad 判问题环(LiCSBAS p12 默认;Morishita 2020 §2.4.2,
  含"环相位非严格为零(多视/滤波/土壤水分)"告诫);像元级 T_int 指示图(Yunjun 2019 §3.2)。[U]/[L]
- **互检惯例**:多处理器速度差 std 0.5–0.7 mm/yr(Terrafirma)、四方法 1.1 mm/yr
  (RSE 256:112306, 2021);PS/SBAS 系统差是常态(Stigliano 案例)。**相关系数阈值无文献先例**
  —— corr_threshold=0.85 维持 local_calibration/PENDING 是诚实做法([前篇] §7.4)。[L]

### 11.2 关键决策点与经验法则

1. **三种方法的证据强度**:crossval_ps_sbas(双独立链互检,最强)> loop_closure(只验解缠
   一致性,不验反演)> coherence_mask(最弱,只是可用像元声明)。降级使用时报告必须写明。[C]
2. **互检指标双报**:相关系数 r + 一对一速度差 std(mm/yr),后者才有文献参照系
   (≤1–2 mm/yr 平稳区);标定实验(experiments/PENDING-crossval-calibration.md)按此设计。[L]
3. **loop_closure 阈值**:图级 1.5 rad(LiCSBAS 惯例)可直接作 literature 锚点;
   非零闭合不全然=解缠错(多视/滤波/土壤湿度亦贡献),阈值判断要配 T_int 空间分布交叉验证。[U]/[L]
4. **速度 std 与观测时长联动**:不满 ~2 年的 S1 序列不要用 2 mm/yr 门槛苛责(Morishita
   收敛曲线);QA 报告应同时给出时长与理论可达精度。[L]

### 11.3 常见失败模式

| 症状 | 根因 | 处置 | 来源 |
|---|---|---|---|
| crossval r 低但两链各自自洽 | PS/SBAS 散射体类型/密度/滤波差异的系统差 | 用速度差 std 与空间分布复核;阈值不宜一刀切 | 论文(RSE 2021;Stigliano 2019) |
| loop 残差超标但形变场正常 | 多视/滤波/土壤湿度引入的非解缠闭合残差 | 配 T_int 图定位;仅当空间聚集时判解缠错 | 论文(Morishita 2020 §2.4.2) |
| 时间相干 0.7 掩膜后像元过少 | 网络冗余不足致相干虚高后又被削 | 修网络;冗余弱时改 0.8 并报告(Yunjun §6.5.5) | 论文(Yunjun 2019) |
| QA 用相干掩膜"全绿"但结果错 | coherence_mask 是最弱质检,不验反演 | 升级到 loop_closure/crossval;报告写明证据等级 | 本仓库设计+社区惯例 |

### 11.4 参数映射核对

| 参数 | 默认(范围) | 知识源依据 | 判定 |
|---|---|---|---|
| corr_threshold | 0.85(0–1,PENDING) | 无文献先例;参照系=速度差 std 0.5–1.1 mm/yr([前篇] 映射表 a) | 维持 PENDING;标定时双报 r 与 std(不新增修正) |

---

## 12. 修正建议(Corrections)

> 判定基准:与上游默认/文献惯例明显相悖,或会在已声明的场景中产生系统性错误。
> P0=会直接吃掉信号/给错结果;P1=预检与规划失真;P2=能力缺口/护栏缺失;P3=呈现层增强。

| # | 优先级 | 位置 | 现状 | 建议 | 依据 |
|---|---|---|---|---|---|
| **C1** | **P0** | 第 8 步 `ramp` | 默认 `"linear"` | 改默认 `"no"`;landslide/subsidence 等局地场景再覆写 linear | MintPy `deramp=no` 上游默认 + cfg §9 原文"NOT recommended for ... co-, post- and inter-seismic deformation";旗舰 quake 场景下 linear 会把同震长波长信号当轨道误差扣除([前篇]已提示,本篇升格为修正) |
| **C2** | **P0** | 第 5 步 `alpha` / `filter_strength` | 同一物理旋钮两个参数、默认还不同(0.4 vs 0.5) | 合并为单参数(或 filter_strength 别名指向 alpha),默认统一(建议 0.5=ISCE2 上游,或 0.4 注明折中) | ISCE2 topsApp `FILTER_STRENGTH default=0.5` 即 Goldstein α(源码);两值并存会因方法路由不同而行为漂移 |
| **C3** | P1 | 第 4 步 `range_looks=10, azimuth_looks=2` | 默认按 S1 IW(rg:az≈5:1)设 | stripmap(ALOS)方法生效时比例须倒置(az>rg,约 2:1);建议 per-method 默认或 stripmap_coseismic 场景包覆写 | S1 像元 2.3×14.1 m(SentiWiki);ALOS FBS 方位像元 ~3.2 m < 地面距离像元 ~7 m 级(JAXA PALSAR 规格,量级判断)→ 10×2 在 ALOS 上产出强矩形像元 |
| **C4** | P1 | 第 1 步 disk `scenes*2.4` GB | 低估 2–3× | 双极化 IW SLC 未压缩 ≈7–8 GB/景、zip ≈4–4.5 GB;建议 ≈8 GB/景(或按极化/是否保留 zip 细分),下载+解压峰值另计 | SentiWiki S1 Products(25 s 切片单极化 ~3.1 GB×2);Sci Data 2022"typical unzipped IW SLC ≈7 GB" |
| **C5** | P1 | 第 1 步 `scenes` min=2 / hint | "景数 2-200" 无场景语义 | hint 分层:同震单对=2;SBAS ≥15–20;PS ≥20–25;并提示速度精度随时长收敛(2 mm/yr 需 ~2.2 年 @24 天采样) | Crosetto 2016(15–20);综述 25;Berardino 2002(44 景);Morishita 2020 式(4) |
| **C6** | P2 | 第 7 步缺 `max_perp_baseline` | 只有时间基线参数 | 增加垂直基线阈值参数(默认不限=MintPy 上游;stripmap/L 波段场景覆写:ERS 级 130 m、ALOS ≤1800 m) | MintPy `perpBaseMax=auto(no)`;Berardino 2002 §V;Yunjun 2019 §5.1;S1 轨道管非约束但已声明支持 ALOS stripmap 场景 |
| **C7** | P2 | 第 7 步 `network` enum | star/sequential 无护栏 | hint 标注:star=单参考(仅 PS/试验,SBAS 下退化无冗余无闭合);sequential 纯短基线有 fading 偏差,须混长基线对 | Ansari 2021(seq-5 → −6.5 mm/yr,混长基线后 −0.24);Berardino 2002(冗余网络);MintPy select_network 语义 |
| **C8** | P3 | 第 10 步 `cmap` | 单一 roma 作用于所有产物 | 按产物类型分色带:速度=roma/vik(diverging)、相干=batlow(sequential)、缠绕相位=romaO(cyclic) | Crameri 2020 + Scientific colour maps 三分类(循环量配非循环色带会产生 ±π 假边界) |

**不列为修正、但技能文档应写明的护栏**(核对后维持现状):
`esd_coherence_threshold=0.85`(上游默认,低相干降 0.75–0.8);`min_coherence=0.25`(literature/OK);
`cost_mode=SMOOTH`(与 snaphu_mcf 一致,quake 近场覆写 DEFO);`max_temporal_baseline=120`
(literature/OK,冻土收紧);`orbit=poeorb`(<20 天数据降 resorb、事后回补);
`unwrap_coverage=0.70` 与 `corr_threshold=0.85`(维持 PENDING,依 contract 台账纪律标定);
`pairs=11`(quake 教程固化值,通用场景应由网络参数派生);`dpi=600`(合规带内)。

---

## 13. 参考文献与一手来源(本篇新增;前篇清单继续有效)

**软件源码 / 官方文档([U] 可引)**

- ISCE2 topsApp 源码(本次逐行核对默认值):<https://github.com/isce-framework/isce2/blob/main/applications/topsApp.py>(ESD_COHERENCE_THRESHOLD=0.85、FILTER_STRENGTH=0.5、rg/az looks=19/7、ESD looks=15/5、unwrapper 默认 icu、2-stage REDARC0)
- ISCE2 topsStack README(orbit/aux_cal 规程):<https://github.com/isce-framework/isce2/blob/main/contrib/stack/topsStack/README.md>
- ISCE2 官方讨论区:ICU 算法说明 #447、icu/snaphu/snaphu2stage 对比 #700、轨道下载 #406/#822、polarization 报错 #495(github.com/isce-framework/isce2/discussions)
- MintPy smallbaselineApp.cfg(本次原文核对 §2/§3/§5/§6/§7/§8/§9/§10/§11/§12 全部默认值):<https://github.com/insarlab/MintPy/blob/main/src/mintpy/defaults/smallbaselineApp.cfg>
- MintPy reference_point 文档(参考点四准则):manpages `mintpy-reference_point(1)`;MintPy Discussions #1188、Issue #216、Google Groups "Not enough reliable pixels"
- asf_search 手册:<https://docs.asf.alaska.edu/asf_search/basics/>(Searching/Downloading/BestPractices)
- HyP3 InSAR Product Guide(GAMMA MCF+三角网、相干 0.1 掩膜、adf 0.6、水掩膜、20×4/10×2):<https://hyp3-docs.asf.alaska.edu/guides/insar_product_guide/>
- SentiWiki S1 Products(IW SLC 像元 2.3×14.1 m、切片体积):<https://sentiwiki.copernicus.eu/web/s1-products>
- Copernicus POD Product Handbook + Sentinels 官网轨道公告(POEORB 20 d/5 cm 3D;RESORB 180 min/10 cm 2D);AWS Registry "s1-orbits"
- Copernicus DEM Product Handbook i5.0(GLO-30 <4 m LE90):<https://dataspace.copernicus.eu/>(GEO1988-CopernicusDEM-SPE-002)
- GACOS 官网与 ReadMe(0.1° HRES/6 h、90 m ZTD、10°×10°/20 dates 限制):<http://www.gacos.net/>
- StaMPS `mt_prep_snap`(D_A:0.4 PS / 0.6 SB):<https://github.com/dbekaert/StaMPS/blob/master/bin/mt_prep_snap>
- SNAP SpectralDiversityOp 帮助(NESD 实现,引 Fattahi 2017):step.esa.int
- 出版社图件规范:Elsevier artwork instructions(300/500/1000 dpi);Wiley(600 dpi 线图);PLOS(300–600 dpi)

**期刊文献([L] 可引;前篇已列的不重复)**

- Rosen P.A., Gurrola E., Sacco G.F., Zebker H. (2012). The InSAR scientific computing environment. *EUSAR 2012*, pp.730-733.(ISCE2 正式引文)
- Yagüe-Martínez N., Prats-Iraola P., Rodríguez González F., Brcic R., Shau R., Geudtner D., Eineder M., Bamler R. (2016). Interferometric Processing of Sentinel-1 TOPS Data. *IEEE TGRS* 54(4):2220-2234. doi:10.1109/TGRS.2015.2497902(方位配准 0.0009 像元 ↔ 1/100 周;互相关 0.1 像元)
- Prats-Iraola P., Scheiber R., Marotti L., Wollstadt S., Reigber A. (2012). TOPS Interferometry with TerraSAR-X. *IEEE TGRS* 50(8):3179-3188.(ESD 原文)
- Fattahi H., Agram P., Simons M. (2017). A Network-Based Enhanced Spectral Diversity Approach for TOPS Time-Series Analysis. *IEEE TGRS* 55(2):777-786. doi:10.1109/TGRS.2016.2614925(<0.001 像元;NESD)
- Hooper A., Zebker H., Segall P., Kampes B. (2004). A new method for measuring deformation on volcanoes and other natural terrains using InSAR persistent scatterers. *GRL* 31:L23611. doi:10.1029/2004GL021737(StaMPS;D_A 候选阈值 0.4)
- Hooper A., Zebker H.A. (2007). Phase unwrapping in three dimensions with application to InSAR time series. *JOSA A* 24(9):2737-2747.(3D 解缠)
- Hooper A., Bekaert D., Spaans K., Arıkan M. (2012). Recent advances in SAR interferometry time series analysis for measuring crustal deformation. *Tectonophysics* 514-517:1-13.(PS/SBAS 综述)
- Goldstein R.M., Zebker H.A., Werner C.L. (1988). Satellite radar interferometry: Two-dimensional phase unwrapping. *Radio Science* 23(4):713-720.(分支切割;ICU 的血统)
- Crosetto M., Monserrat O., Cuevas-González M., Devanthéry N., Crippa B. (2016). Persistent Scatterer Interferometry: A review. *ISPRS J. Photogramm. Remote Sens.* 115:78-89. doi:10.1016/j.isprsjprs.2015.10.011(C 波段 ≥15–20 景)
- Ansari H., De Zan F., Parizzi A. (2021). Study of Systematic Bias in Measuring Surface Deformation With SAR Interferometry. *IEEE TGRS* 59(2):1285-1301. doi:10.1109/TGRS.2020.3003421(fading 偏差 −6.5→−0.24 mm/yr)
- De Zan F., Zonno M., López-Dekker P. (2015). Phase Inconsistencies and Multiple Scattering in SAR Interferometry. *IEEE TGRS* 53(12):6608-6616.(闭合不一致的物理根源)
- Gomba G., Parizzi A., De Zan F., Eineder M., Bamler R. (2016). Toward Operational Compensation of Ionospheric Effects in SAR Interferograms: The Split-Spectrum Method. *IEEE TGRS* 54(3):1446-1461. doi:10.1109/TGRS.2015.2481079
- Hersbach H., Bell B., Berrisford P., et al. (2020). The ERA5 global reanalysis. *QJRMS* 146(730):1999-2049. doi:10.1002/qj.3803
- Rodriguez E., Martin J.M. (1992). Theory and design of interferometric synthetic aperture radars. *IEE Proc.-F* 139(2):147-159.(多视-相位方差)
- Just D., Bamler R. (1994). Phase statistics of interferograms with applications to synthetic aperture radar. *Appl. Opt.* 33(20):4361-4368.
- Touzi R., Lopes A., Bruniquel J., Vachon P.W. (1999). Coherence estimation for SAR imagery. *IEEE TGRS* 37(1):135-149.(相干估计偏差)
- Farr T.G. et al. (2007). The Shuttle Radar Topography Mission. *Rev. Geophys.* 45:RG2004. doi:10.1029/2005RG000183
- Crameri F., Shephard G.E., Heron P.J. (2020). The misuse of colour in science communication. *Nature Communications* 11:5444. doi:10.1038/s41467-020-19160-7
- Hanssen R.F. (2001). *Radar Interferometry: Data Interpretation and Error Analysis*. Kluwer Academic Publishers.(教科书基准:相干/多视/基线几何/大气统计)
- (JGR Biogeosciences 128, 2023, doi:10.1029/2023JG007672)A Global Evaluation of Radar-Derived Digital Elevation Models: SRTM, NASADEM, and GLO-30.

**前篇(reference/RESEARCH-insar-params-2026-08-12.md)已核对、本篇直接引用的**:
Berardino 2002;Ferretti 2001;Yunjun 2019;Pepe & Lanari 2006;Morishita 2020;Goldstein & Werner 1998;
Baran 2003;Chen & Zebker 2000/2001/2002;Zebker, Rosen & Hensley 1997;Jolivet 2011/2014;Yu 2018×2;
Doin 2009;Manunta 2019;Casu 2006;Hetland 2012;Fattahi & Amelung 2013/2015;Marone 1991;
Ingleby & Wright 2017;Tobita 2016;Sobrero 2020;Daout 2017;Li 2019;Liu 2012;
Colesanti & Wasowski 2006;Stigliano 2019;RSE 256:112306 (2021);IGARSS 2023(SNAP Goldstein);
IGARSS 2024(EGMS 验证);Kang 2021;Chaussard 2015;Fattahi 2017(电离层)/Liang 2019。

**调研纪律声明**:本篇所有新增数值(0.0009/0.001 像元、POEORB/RESORB 时延与精度、GLO-30 精度、
SLC 体积、HyP3 各默认、topsApp/smallbaselineApp 默认、GACOS 限制、fading 偏差量级、D_A 0.4、
PSI 15–25 景、dpi 三档)均于 2026-08-13 由一手来源原文核对;ICU 无正式出版物一事按官方讨论区
如实标注;未验证的传闻值(如 HyP3 产品保留期具体天数)已注明"以官方文档为准"。
