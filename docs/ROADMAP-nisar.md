# NISAR 接入路线图

- **调研日期**:2026-08-13(网络调研 + 本仓库 `registry/capabilities.py`、`registry/kinds.py`、`engines/localdata.py`、`engines/mintpy.py` 声明形态分析)
- **背景**:NISAR 2025-07-30 发射,**2026-07-20 起 PROVISIONAL(已定标)L 波段产品全球免费公开**([ASF 公告](https://asf.alaska.edu/notices/nisar-l-band-data-now-publicly-available/));`docs/ROADMAP-isce3.md` §3 阶段 3 已把「NISAR 路线(GUNW→prep_nisar→cap7)是否提为独立里程碑」列为并行决策点 5,本文档就是该决策点的预研答卷。
- **一句话结论**:**GUNW 直通 MintPy 是低成本高确定性的路线**——任务方已在云端替我们完成 3-6 步(配准/干涉/滤波/解缠全部内置于 L2 GUNW 标准产品),MintPy v1.6.4(2026-07-25)官方 `prep_nisar` 已成熟,我们只需「cap1 新增导入方法 + kinds/bridges 声明 + mintpy 引擎加 `processor=nisar` 渲染分支」,**阶段 N1 约 10-16 人日**;GSLC→ISCE3/dolphin 自算链(阶段 N2)强依赖 ROADMAP-isce3 的 insar3 环境,且单景 8-23 GB 的体量决定它必须等 AOI 裁剪工具链成熟后再做。

---

## 0. TL;DR

| 问题 | 答案 |
|---|---|
| NISAR 数据现在能用吗 | 能。PROVISIONAL(CRID P05023,已定标、部分验证)覆盖 2026-06-17 起全部采集,36-72 h 延迟持续发布;2026 年底前完成全记录(2025-10 起)validated 重处理([可用性页](https://nisar-docs.asf.alaska.edu/availability-overview/)) |
| GUNW 是什么形态 | L2 标准产品:**HDF5,UTM/极地立体网格,解缠相位 80 m + 缠绕干涉图 20 m + 相干(20/80 m)+ 连通分量 + 电离层屏 + 对流层/固体潮 LUT 层**,仅同极化(HH 或 VV)、仅 frequency A、仅最近邻配对([GUNW 产品页](https://nisar-docs.asf.alaska.edu/gunw/)) |
| 覆盖节奏 | 12 天精确重访(173 轨/周期),幅宽 ~240 km,全部陆地+冰盖每周期升降轨各至少一次;GUNW 只做最近邻对(缺周期时实测出现 24/36 天对) |
| 单产品多大 | CMR 实测:GUNW 单对 **1.1-2.4 GB**,GSLC 单景 **2.5-23 GB**,RSLC 更大(ISRO 官方均值 43 GB);任务级 ~85 TB/天、3 年 140 PB |
| 怎么拿 | ASF DAAC(Earthdata Cloud/AWS),**免费但必须 Earthdata Login**;Earthdata Search / Vertex / `asf_search`(`dataset='NISAR'`)/ `earthaccess` / S3 直读;S 波段走 ISRO Bhoonidhi |
| MintPy 支持到什么程度 | 一等公民:`prep_nisar` 自 v1.5.3;**v1.6.4(2026-07-25)大修落地**(#1487:电离层/对流层/固体潮辅助栈、原生掩膜、bperp、频率选择、DEM 强校验);`mintpy.load.processor = nisar` 全模板化 |
| LiCSBAS2 呢 | v2.0.0(2026-03-12)经 ARIA-tools 路径支持 NISAR GUNW(`LiCSBAS_aria2geoc.py`),推荐 NISAR 无滤波相干阈值 0.2,2026-05-30 修了相位符号翻转 |
| 对我们最大的架构收益 | 复刻 HyP3 模式:**「云端完成 3-6 步」的 `cloud_completed` 机制现成**,cap1 导入 + cap7 直通,是三条数据获取路线(HyP3 产品/本地导入/ALOS 条带)之后的第四条,且是唯一免配额、全球覆盖、持续更新的一条 |
| 最大的坑 | ① GUNW 仅最近邻链 → 无冗余网络、无闭合环(cap11 `loop_closure` 不可用);② prep_nisar 需要用户自备 GeoTIFF DEM(cap2 不能跳);③ AOI 服务器端裁剪尚无(Harmony 只到 GCOV),整帧下载不可避免;④ 高纬电离层残差(provisional 已知问题) |

---

## 1. 产品线事实(2026-08)

### 1.1 产品层级总览

L 波段产品由 JPL 生产、ASF DAAC 归档分发;S 波段由 ISRO 生产、走 [Bhoonidhi](https://bhoonidhi.nrsc.gov.in/NISAR/)(2026 年起提供 1-2 月采集的 S 波段 RSLC/GSLC/GCOV 样品)。L 波段全套([产品总览](https://nisar-docs.asf.alaska.edu/products-overview/)、[产品规范文档列表](https://nisar-docs.asf.alaska.edu/product-specification/)):

| 级别 | 产品 | 内容 | 坐标 | 与我们 11 步的对应 |
|---|---|---|---|---|
| L0B | RRSD | 原始回波 | — | (不用) |
| L1 | RSLC | 聚焦 SLC(Range-Doppler) | 雷达 | cap3 之前的原料(自算链才需要) |
| L1 | RIFG / RUNW / ROFF | 缠绕/解缠干涉图、偏移量(雷达坐标) | 雷达 | cap4-6 的雷达坐标版本 |
| L2 | **GSLC** | 地理编码 SLC,**已做轨道相位展平**(两景共轭相乘直接得去平地相位的干涉图,[GSLC 页](https://nisar-docs.asf.alaska.edu/gslc/)) | UTM/极地立体 | 阶段 N2 自算链输入(≈OPERA CSLC 的 NISAR 版) |
| L2 | GCOV | 辐射地形校正多极化协方差(≈RTC) | UTM/PS | (振幅应用,非 InSAR) |
| L2 | **GUNW** | **地理编码解缠干涉图**(详见 §1.2) | UTM/PS | **cap3-6 的云端成品** |
| L2 | GOFF | 地理编码像素偏移 | UTM/PS | (同震大形变备用) |
| L3 | SME2 | 土壤湿度 | 地理 | (不用) |
| L3(OPERA) | **DISP-NI** | NISAR 版位移时序产品,**2026-09 计划发布,仅北美**,30 m([OPERA 产品页](https://www.jpl.nasa.gov/go/opera/products/)、[DISP 简报 PDF](https://www.earthdata.nasa.gov/s3fs-public/2025-01/OPERA-DISP_Nov2024.pdf)) | UTM | cap7-9 的云端成品(北美 AOI 的对照/替代) |

仪器要点([About NISAR](https://nisar-docs.asf.alaska.edu/nisar-intro/)):L-SAR 波长 24 cm,幅宽 ~240 km,方位分辨率 ~7 m、距离向 2-8 m(模式依赖);S-SAR 9.3 cm 主要覆盖印度。干涉产品只用 frequency A;模式带宽 5/20/40/77 MHz(决定 GSLC 采样,见 §1.3)。

### 1.2 GUNW:格式细节(接入的核心对象)

来源:[GUNW 产品页](https://nisar-docs.asf.alaska.edu/gunw/)、[产品规范 JPL D-102272 Rev F(PDF)](https://nisar.asf.earthdatacloud.nasa.gov/NISAR-SAMPLE-DATA/DOCS/NISAR_D-102272_RevE_NASA_SDS_Product_Specification_L2_GUNW_Nov8_2024_w-sigs.pdf)、[ASF 样例数据教程](https://www.earthdata.nasa.gov/learn/tutorials/work-nisar-sample-data)、[数据格式页](https://nisar-docs.asf.alaska.edu/data-format/)。

- **配对策略**:任务只生产**同极化(HH 或 VV)最近邻对**,名义 12 天;参考影像取**较早**日期(与 ARIA-S1-GUNW 的 reference=较晚约定相反,[HyP3 GUNW 指南](https://hyp3-docs.asf.alaska.edu/guides/gunw_product_guide/);形变符号约定随之不同,LiCSBAS2 对 NISAR 专门做了符号翻转,§2.2)。缺周期时取最近可用采集:我们从 CMR 实测到 24 天、36 天的对。仅 frequency A。
- **网格**:UTM 或极地立体(按帧选定 EPSG);解缠相位 **80 m** posting;缠绕干涉图雷达坐标 30 m 多视后以 **20 m** posting 地理编码;相干幅值 20 m 与 80 m 双版本;浮点层双线性插值、复数层 Sinc、整型(连通分量)最近邻。
- **HDF5 结构**(数据层,以 HH 为例):

```text
/science/LSAR/GUNW/grids/frequencyA/
  unwrappedInterferogram/HH/{unwrappedPhase, coherenceMagnitude,
                             connectedComponents, ionospherePhaseScreen,
                             ionospherePhaseScreenUncertainty}
  unwrappedInterferogram/mask
  wrappedInterferogram/HH/{wrappedInterferogram(complex64), coherenceMagnitude}
  pixelOffsets/HH/{alongTrackOffset, slantRangeOffset, correlationSurfacePeak}
/science/LSAR/GUNW/metadata/radarGrid/   # 粗网格元数据立方,插值到全网格用
  {incidenceAngle, perpendicularBaseline, parallelBaseline,
   hydrostaticTroposphericPhaseScreen, wetTroposphericPhaseScreen,
   slantRangeSolidEarthTidesPhase, referenceSlantRange, losUnitVectorX/Y,
   alongTrackUnitVectorX/Y, elevationAngle, groundTrackVelocity,
   xCoordinates, yCoordinates, projection, ...}
```

- **校正层语义(诚实接口关键)**:电离层相位屏**随产品发布但默认不应用**;固体潮/干湿对流层以**地理编码 LUT 层**形式附带、同样不从干涉相位中扣除——校正决策权留给用户(MintPy 的 ion/tropo/SET 辅助栈正是消费这些层,§2.1)。
- **软件兼容性**:HDF5 按 NetCDF CF 约定编码空间参考,GDAL/QGIS/ArcGIS(<3.4)需按 NetCDF 打开(`NETCDF:"file.h5":/science/...` 或改 `.nc` 后缀);GDAL 可直接 `gdalwarp` 子数据集裁剪([GDAL 教程页](https://nisar-docs.asf.alaska.edu/gdal/))。
- **元数据仍在演化**:MintPy [issue #1485](https://github.com/insarlab/MintPy/issues/1485) 记录了 `radarGrid/slantRange` → `referenceSlantRange` 的产品规范变更导致 prep_nisar 崩溃([PR #1478](https://github.com/insarlab/MintPy/pull/1478) 修复)——接 PROVISIONAL 数据必须 pin 工具版本并准备跟产品 CRID 演化。

### 1.3 GSLC:阶段 N2 的输入

- 相位**已按 RSLC 轨道展平**(去了地形相位):两景 GSLC 共轭相乘直接得到平了地的干涉图,这是它作为「NISAR 版 CSLC」进 dolphin 相位链接的基础([GSLC 页](https://nisar-docs.asf.alaska.edu/gslc/))。
- 采样随带宽:5 MHz→5×40 m,20 MHz→5×10 m,40 MHz→5×5 m,77 MHz→5×2.5 m(北向×东向);多数陆地帧为 5×5 或 5×10 m。
- 数据层 `/science/LSAR/GSLC/grids/frequencyA/{HH,HV,...}`(complex64 DN;`|DN|²`=beta0);gamma0/sigma0 LUT 在 `metadata/calibrationInformation/geometry`。
- **中心频率不是常数**:BETA GSLC 集合中实测 6 个不同 frequency A 中心频率(1221.5-1293.5 MHz,波长差 ~5.9%)——下游做相位→位移换算**必须从产品元数据读波长**,不能用常数(dolphin [bug #704](https://github.com/isce-framework/dolphin/issues/704)、opera-utils [PR #225](https://github.com/opera-adt/opera-utils/pull/225) `get_nisar_wavelength`,§2.3)。

### 1.4 发布状态、延迟与质量(2026-08)

时间线([可用性页](https://nisar-docs.asf.alaska.edu/availability-overview/)):

| 时间 | 事件 | 规模/性质 |
|---|---|---|
| 2025-07-30 | 发射 | — |
| 2026-01-23 | 25 个预定标样例产品 | 9 种 L1-L3 产品各若干 |
| 2026-02-27 | **BETA** 全球预定标发布 | >100,000 个 L1-L3 产品、>500 TB(2025-10-17..2026-01-20 采集,每轨最多 8 次) |
| 2026-07-20 | **PROVISIONAL**(CRID **P05023**) | 已定标+部分验证;覆盖 2026-06-17 起全部采集,持续前向生产;**首次含 L0B RRSD**;另有「补充 provisional」对选定帧回溯生产更长时序(我们在 CMR 里看到的 2025-10/11 对即来源于此) |
| 2026 Q4 | **validated 重处理** | 新处理器版本重做 2025-10 起全记录,预计 2026 年底完成,取代旧版本 |

- **延迟**:L0B 采集后 2-10 h 上架;**L1-L3(含 GUNW)名义 36-72 h**。
- **PROVISIONAL 已知问题**([Known Issues 页](https://nisar-docs.asf.alaska.edu/provisional-known-issues/)),与我们相关的:①**高纬电离层**缓解不充分——地理定位误差、条带状去相干、应用电离层屏后仍有相位残差(对 permafrost 场景是真实风险);② RFI 滤波偶有欠/过校正;③ 极化通道间相位失衡(相干双/四极化分析需手工加 59° 相位,GUNW 同极化不受影响);④ 部分诊断模式帧(Track 161/169 的 4 个帧)不可用至 2026-08;⑤ BETA 与 PROVISIONAL(CRID < / ≥ 05023)**不要混入同一分析**。
- **观测计划**([Observation Plan 页](https://nisar-docs.asf.alaska.edu/observation-plan/)、[eoportal 任务页](https://www.eoportal.org/ftp/satellite-missions/n/NISAR-25032021/NISAR.html)):12 天精确重访、每周期 173 轨;参考观测计划(ROP)地理上基本静态以保证时序一致;全部陆地/冰盖每周期升+降轨各至少覆盖一次(>60°N 隔次剔除冗余采集);升/降单向覆盖各留有约 10% 的固定缝隙,合并后绝大多数陆地具备升降双向覆盖。track-frame 网格:frame 沿轨约 250 km([CMR STAC 约定](https://github.com/nasa/cmr-stac/issues/413)),一个 GUNW granule = 一个 frame(≈240 km × 250 km)。

### 1.5 获取渠道与认证

([Access 总览](https://nisar-docs.asf.alaska.edu/access-overview/)、[Earthdata Search 指南](https://nisar-docs.asf.alaska.edu/earthdata-search/)、[Vertex 指南](https://nisar-docs.asf.alaska.edu/vertex/)、[asf_search 指南](https://nisar-docs.asf.alaska.edu/asf-search/))

- 数据存于 NASA Earthdata Cloud(AWS us-west-2),**免费,需 [Earthdata Login](https://urs.earthdata.nasa.gov/)(EDL)**——与我们 cap1 `hyp3_submit` 已声明的 `requires_credentials=("earthdata",)` 同一凭据,无新增凭据负担、无配额限制(HyP3 有配额,NISAR 标准产品没有)。
- 集合短名:`NISAR_L2_GUNW_PROVISIONAL_V1` / `NISAR_L2_GSLC_PROVISIONAL_V1`(BETA 版把 PROVISIONAL 换成 BETA;validated 版将是 [`NISAR_L2_GUNW_V1`](https://www.earthdata.nasa.gov/data/catalog/asf-nisar-l2-gunw-v1-1))。
- 编程接口:

```python
import asf_search as asf
# 按数据集+产品级检索;NISAR 专属过滤:frameCoverage(整帧/部分帧)、
# mainBandPolarization、rangeBandwidth、jointObservation 等
results = asf.search(dataset='NISAR', processingLevel='GUNW',
                     intersectsWith='POINT(101.5 35.6)', maxResults=250)
# 或按短名:asf.search(shortName=['NISAR_L2_GUNW_PROVISIONAL_V1'], ...)
```

([asf_search Best Practices](https://docs.asf.alaska.edu/asf_search/BestPractices/);`earthaccess` 与 S3 直读/`fsspec`+`xarray` 流式读取亦官方支持)。

---

## 2. GUNW → 时序分析:接入路径对比

四条路径,按「自己算多少」排序;我们 cap7 = `mintpy_sbas`(`engines/mintpy.py` 以 `smallbaselineApp --start/--end` 分段驱动),接入点即「把 GUNW 栈喂进 `load_data`」。

### 2.1 路径 A(推荐主线):MintPy `prep_nisar` 直通

**现状**:`prep_nisar` 自 [v1.5.3(#1035,2023)](https://github.com/insarlab/MintPy/releases) 存在;**[PR #1487](https://github.com/insarlab/MintPy/pull/1487)(2026-04-22 合并,+1258 行)大修**,并已进入 **[v1.6.4 正式发行版(2026-07-25)](https://github.com/insarlab/MintPy/releases)**:

- 从 GUNW 直接产 `ifgramStack.h5` + 几何(`load_data` 阶段完成写栈,跳过通用写数据步——与 prep_aria 同模式);
- **辅助栈全支持**:`ionStack.h5`(电离层屏)、`tropoStack.h5`(干/湿对流层屏)、`setStack.h5`(固体潮)——消费的正是 GUNW 内嵌的校正层(§1.2),cap8 因此有了「产品自带校正」与「MintPy ERA5/pyaps」两条可选路线;
- bperp 从 `radarGrid/perpendicularBaseline` 读取;原生 GUNW mask 作为主掩膜;frequency A/B 可选;**DEM 必填强校验**(auto/none 直接报错);
- [PR #1494](https://github.com/insarlab/MintPy/pull/1494) 提出 `--band LSAR/SSAR` 与 `mintpy.load.band`(S 波段 GUNW 同一工作流)——**截至检索日仍 open 未合并**,列入 §5.3 跟踪。

**模板键**(`load_data.py` 的 nisar 分支实测确认,main 分支源码):

```text
mintpy.load.processor  = nisar
mintpy.load.unwFile    = ../products/NISAR_L2_*GUNW*.h5   # GUNW 文件 glob(作 prep_nisar -i)
mintpy.load.demFile    = ../dem/dem.tif                   # 必填,GeoTIFF;GDAL warp 对齐 GUNW 网格
mintpy.load.waterMaskFile = <可选>
mintpy.load.frequency  = auto        # auto→A
mintpy.subset.lalo     = S:N,W:E     # → prep_nisar --sub-lat/--sub-lon(YX 子集不支持)
```

CLI 等价形态:`prep_nisar.py -i 'interferograms/*.h5' -d dem.tif [--sub-lat .. --sub-lon ..]`([cli/prep_nisar.py 源码](https://github.com/insarlab/MintPy/blob/main/src/mintpy/cli/prep_nisar.py))。

**与我们 cap7 的接入点**(对照 `engines/mintpy.py` 现状):`_CFG_TEMPLATE` 目前硬编码 `processor=hyp3` + HyP3 clipped GeoTIFF glob;NISAR 只需增加一个渲染分支(processor=nisar + 上述键),分支选择依据 run["chain"] 里 cap1 的方法 id——**cap7-9 的 `--start/--end` 分段区间、断点、run_ok 判定全部复用,零改动**。注意两点:① `mintpy.subset.lalo` 已有 `INSAR_SUBSET_LALO` 环境变量缝,NISAR 直接复用;② prep_nisar 失败时 load_data 只 warn 不 raise(源码 `except: warnings.warn`),我们的 run_ok 必须靠 `artifact_exists(ifgramStack.h5)` 兜底,不能只看 exit code——现声明形态恰好就是这么做的。

**适用边界**:prep_nisar 按帧栈工作(官方示例 glob 是 `interferograms/stitched/*.h5`)——**AOI 落单帧内时最顺**;跨帧 AOI 需要先拼接,这正是路径 B 的强项。

### 2.2 路径 B:ARIA-tools 预处理(跨帧/裁剪)→ `prep_aria` 或 LiCSBAS2

**ARIA-tools 自 2026-02 起全面支持 NISAR GUNW**([README](https://github.com/aria-tools/ARIA-tools)、[#480 下载支持](https://github.com/aria-tools/ARIA-tools/commit/72d545905b75e5a3978342a30209f72d04cc6b6d)、[releases](https://github.com/aria-tools/ARIA-tools/releases)、[团队公告](https://www.linkedin.com/posts/david-bekaert-49652717_available-nisar-data-nisar-data-user-guide-activity-7433640242907152384-GIBx)):

- `ariaDownload.py --mission NISAR -t <track> -b "<S N W E>"` 下载或 `-o url` 生成 URL 清单做 **vsicurl/S3 虚拟访问(不落盘)**;
- `ariaExtract.py`/`ariaTSsetup.py`:按 bbox **裁剪 + 跨帧拼接**(处理跨帧 EPSG 变化、用产品内嵌 mask 去边缘伪影)、提取 unw/coh/连通分量/几何/校正层,产出 MintPy `prep_aria` 可直接吃的 ARIA 布局;NISAR 电离层默认**不做**短波长滤波(除非 `--iono_filter`);
- 接口与 S1 GUNW 完全一致——「会用 ARIA-tools+MintPy 处理 S1 的人直接会用 NISAR」。

**LiCSBAS2 的 NISAR 支持就架在这条路径上**(竞品中最快跟进者):[v2.0.0(2026-03-12)commit「Support ARIA NISAR GUNW products」](https://github.com/yumorishita/LiCSBAS2/commit/98bbfffc71b48806bf92073d41d8fae1c7489e2e) 给 `LiCSBAS_aria2geoc.py`(ARIA 目录→GEOC 布局转换器)加了 NISAR 分支;要点:相干阈值参数默认改为 None 并注明「**S1 滤波相干用 0.5,NISAR 无滤波相干用 0.2**」;[PR #152(2026-05-30)对 NISAR 相位做符号翻转](https://github.com/yumorishita/LiCSBAS2/pull/152)(以对齐 NASA 官方形变符号约定;参考影像次序差异见 §1.2)。官方工作流([wiki 2.7](https://github.com/yumorishita/LiCSBAS2/wiki/2_7_ARIA)):

```bash
ariaDownload.py -t 38 -b "37.2 38.1 138.7 139.4" --mission NISAR
ariaTSsetup.py -f 'products/*' -b "37.2 38.1 138.7 139.4" \
               -l 'unwrappedPhase,coherence,amplitude' -of GTiff
LiCSBAS_aria2geoc.py    # → GEOC/,之后 batch_LiCSBAS.sh 从 step02 起跑
```

### 2.3 路径 C:自算干涉(GSLC/RSLC + ISCE3 生态)——阶段 N2

- **GSLC→dolphin**:dolphin 接受 NISAR GSLC 作 CSLC 输入;sweets 已有 `--source nisar-gslc`(经 opera-utils 的 `NisarGslcSearch` 按 AOI+track/frame 检索并**按 AOI 子集读取**,[sweets README](https://github.com/isce-framework/sweets));已知坑:**波长不自动识别 → 时序输出停留在弧度**([dolphin #704](https://github.com/isce-framework/dolphin/issues/704)),须经 [opera-utils `get_nisar_wavelength`](https://github.com/opera-adt/opera-utils/pull/225) 从产品元数据显式取波长(§1.3 的 6 种中心频率问题)。收益:任意配对网络(摆脱最近邻链)、5-10 m 分辨率、相位链接(PS/DS)。
- **RSLC→isce3 `nisar.workflows.insar`**:任意自定义对;ASF 的 [hyp3-isce3 插件](https://github.com/ASFHyP3/hyp3-isce3) 提供 CLI(`python -m hyp3_isce3 --reference <RSLC_ID> --secondary <RSLC_ID>`)产 GUNW 同规格产品;社区亦有 [isce3-builder](https://github.com/CBurton90/isce3-builder)(Snakemake 批量 12/24/36 天对)。代价:RSLC 单景数十 GB,重型下载+重型计算。
- **ASF 云端定制服务尚不存在**:官方在 [Earthdata Forum](https://forum.earthdata.nasa.gov/viewtopic.php?t=7601) 明确「自定义配对目前需用户自算,on-demand 视资源而定」;[ASF 开发路线图](https://nisar-docs.asf.alaska.edu/roadmap/) 把「HyP3 定制 GUNW(自定义日期对)」列为未来项——落地后即是 `hyp3_submit` 的 NISAR 版,值得跟踪。

### 2.4 路径 D:等 OPERA DISP-NI(北美)

DISP-NI(NISAR 版 DISP,dolphin 产线)计划 **2026-09** 发布、仅北美、30 m([OPERA 产品页](https://www.jpl.nasa.gov/go/opera/products/);PGE 工程版 [ER2.0 已于 2026-03-24 发布](https://github.com/nasa/opera-sds-pge/releases/tag/6.0.0-er.2.0-disp-ni))。对北美 AOI 是「cap7-9 也云端完成」的终极形态,类比 DISP-S1 + calval-DISP 转 MintPy 的现成路径(ROADMAP-isce3 §1.5)。非北美不可用,只作对照与跟踪项。

### 2.5 对比表与网络结构约束

| 路径 | 覆盖 | 我们要做的步 | 控制权 | 成熟度(2026-08) | 对应改动 |
|---|---|---|---|---|---|
| A:GUNW→prep_nisar | 全球 | 1,2(DEM),7-11 | 网络=最近邻链(只能剪不能加) | 官方发行版,高 | **阶段 N1 主线** |
| B:GUNW→ARIA-tools→prep_aria | 全球 | 同 A + 跨帧拼接/虚拟访问 | 同 A + bbox 裁剪 | 官方,高(LiCSBAS2 同路径背书) | N1 可选增强 |
| C:GSLC→dolphin | 全球 | 1,2,4-11(cap3 由 GSLC 吃掉) | 任意网络/高分辨率 | 生态可用但有波长坑,中 | 阶段 N2 |
| D:DISP-NI | 仅北美 | 1,8-11 | 最低 | 未发布 | 跟踪 |

**GUNW 最近邻链的科学约束(必须写进声明的诚实接口)**:标准产品只有 sequential 网络——① cap7 的 `network` 参数在 NISAR 场景只有 `sequential` 语义(`small_baseline` 声明保留但等价于链);② **无干涉三元环 → cap11 `loop_closure` 对 GUNW 栈原理上不可用**,质检要靠相干掩膜 + 与 S1 链的双链交叉验证(cap11 `crossval` 的天然新素材);③ 单链累积误差风险高于冗余网络,缺周期造成的 24/36 天对在低相干区风险放大——这是路径 C 存在的根本理由。

---

## 3. 体量与本地化预算

### 3.1 单产品体量(实测 + 官方)

CMR 实测(PROVISIONAL,2026-08-13 各取 8 个 granule):

| 产品 | 实测范围 | 备注 |
|---|---|---|
| GUNW 单对 | **1.15-2.36 GB**(均值约 1.6 GB) | 整帧;含 20 m 缠绕层与偏移层 |
| GSLC 单景 | **2.5-22.8 GB** | 40 MHz DHDH 整帧 21-23 GB;77 MHz SH 约 8 GB;部分帧更小 |

ISRO ARSET 官方均值表([2026-07 培训 PDF](https://www.earthdata.nasa.gov/s3fs-public/2026-07/arset-2026-nisar-part2-isro.pdf)):LSAR RSLC 43 GB、GSLC 61 GB、GCOV 12.5 GB、GUNW 3.5 GB、GOFF 1.3 GB(均值口径偏保守,可作上限参考)。任务级:**~85 TB/天、3 年 ~140 PB**([Earthdata 专文](https://www.earthdata.nasa.gov/news/feature-articles/getting-ready-nisar-managing-big-data-using-commercial-cloud)、[发射后数据说明](https://www.earthdata.nasa.gov/news/now-that-nisar-launched-heres-what-you-can-expect-from-the-data));BETA 一次发布即 >500 TB(§1.4)。

### 3.2 AOI 裁剪:服务器端还没有,客户端三条路

- **Harmony 现状**([ASF 开发路线图](https://nisar-docs.asf.alaska.edu/roadmap/)):已上线的只有 **GCOV 图层提取→COG**(2026-06);GCOV 空间裁剪排在 2026 Q4-2027;**GUNW/其他 L2 的裁剪只在「未来计划」列表**。即:**近 1-2 年内 GUNW 没有服务器端按 AOI 出切片的服务**。
- 客户端现实路径:① **prep_nisar `--sub-lat/--sub-lon`**——整帧下载、装载时裁剪(下载量不减,MintPy 栈体量减);② **ARIA-tools 虚拟访问**——`ariaDownload -o url` + vsicurl/VSIS3 流式,`ariaTSsetup -b` 只物化 AOI 范围(**下载量近似按 AOI 面积比例缩减**,跨帧自动拼接);③ **GDAL 直连**——`gdalwarp NETCDF:"/vsicurl/...h5":/science/... -te ...`(EDL netrc 认证,[GDAL 页](https://nisar-docs.asf.alaska.edu/gdal/))。
- 阶段 N1 取①为默认(实现最薄、与 MintPy 官方路径一致),②作为「大 AOI/跨帧」的声明式升级项。

### 3.3 对我们磁盘预算声明的影响

以「1 帧 × 1 年 PROVISIONAL(12 天最近邻,~30 对)」估算:

| 项 | 体量 | 说明 |
|---|---|---|
| GUNW 原始 h5 | 30 × 1.6-2.4 GB ≈ **50-72 GB** | cap1 落盘主项;`DiskEstimate("pairs * 2.4")` 恰与现 cap1 公式 `scenes * 2.4` 同形(最近邻链 pairs≈scenes-1) |
| MintPy 栈(全帧) | ifgramStack(80 m,unw+coh+connComp)~2-3 GB;ion/tropo/SET 辅助栈各同量级;geometry <1 GB → **合计 ~5-10 GB** | 80 m posting 决定了 MintPy 侧极轻;AOI 子集再按面积比缩 |
| **对比:GSLC 栈(阶段 N2)** | 30 × 8-23 GB ≈ **240-690 GB/帧** | **不做 AOI 子集不可行**——这就是 N2 必须后置并依赖 opera-utils 子集读取的量化理由 |

结论:**GUNW 路线对磁盘预算友好**(一个完整分析 ≲80 GB,低于我们 ALOS 条带链实测的 32 GB 峰值的 3 倍内,E 盘可承受);声明层面新增 cap1 `nisar_gunw` 方法时按上表给公式,数字按首次真实运行标定后进 `contract.yaml` 台账(遵循「无实测不硬 gate」纪律)。

---

## 4. 与 Sentinel-1 的互补:场景包价值

**物理底牌**:L 波段 24 cm vs C 波段 5.6 cm——穿透植被冠层、时间去相干慢、单缠绕周期容纳的形变梯度大 4 倍;代价是电离层敏感度高(§1.4 已知问题)与 80 m 解缠 posting 偏粗。文献定量证据:

- **冻土(permafrost 包)**:青藏高原北部 ALOS/ALOS-2 vs S1 对比研究显示 L 波段能探测到 C 波段**几乎探测不到的热融塌陷**,且与光学核验一致([Remote Sens. 14(8):1870, 2022](https://www.mdpi.com/2072-4292/14/8/1870));NISAR 补上了「L 波段免费+12 天规律重访+升降轨」这一 ALOS-2 从未提供过的组合。GUNW 内嵌 SET/对流层 LUT + MintPy setStack/tropoStack 对冻土周期信号分离直接有用。**注意**:高纬电离层残差是当前 provisional 的最大质量风险(§1.4),cap8 的电离层校正(ionStack)在此场景应默认开启。
- **滑坡(landslide 包)**:白鹤滩库区 C/L 对比:随植被厚度增加,**L/C 平均相干比升至 ~3 倍**,L 波段在高植被覆盖(>0.6 覆盖度占 95% 区域)的高山峡谷明显占优([Remote Sens. 16(9):1591, 2024](https://doi.org/10.3390/rs16091591));茂县研究:大梯度形变下 S1 时序失相干而 ALOS-2 保持相干,但微小形变 C 波段更灵敏([Remote Sens. 15(18):4538, 2023](https://www.mdpi.com/2072-4292/15/18/4538));阿尔卑斯岩坡研究:L 波段 PS 密度约为 C 波段 **5 倍**、森林区 C 波段完全无解而 L 波段可测,并直接为 NISAR 给出应用建议([NHESS 26:2579, 2026](https://nhess.copernicus.org/articles/26/2579/2026/))。**注意**:80 m GUNW posting 对小型滑坡偏粗——小体量滑坡要么继续用 S1(10-40 m 级),要么等阶段 N2 的 GSLC 5-10 m 自算链。
- **地震(quake/stripmap_coseismic 包)**:GUNW 最近邻对天然覆盖同震(36-72 h 延迟);大梯度近场 L 波段不易失相干,GOFF/pixelOffsets 层还给了偏移量备份。长期看,**NISAR 标准产品正是我们 ALOS 条带链所服务的「L 波段同震」需求的接替者**(ROADMAP-isce3 §1.4 已预判)。
- **互补策略(而非替代)**:S1 有 2014 年起 11 年档案、6-12 天重访、C 波段对小形变敏感;NISAR 记录从 2025-10(validated 回溯后)起。推荐姿态:**S1 链为长档案主链,NISAR GUNW 为植被区/大梯度增强链,双链跑同一 AOI 做 cap11 交叉验证**——这同时是论文级质检素材(与 ROADMAP-isce3 阶段 2 的 ISCE2/ISCE3 双链交叉验证同构,且成本低得多)。

| 场景包 | NISAR 价值 | 声明层注意事项 |
|---|---|---|
| permafrost | 高(植被冻土相干 + SET/tropo 层 + 双轨向) | 默认启用 ionStack;高纬 provisional 质量告警进 SKILL 正文 |
| landslide | 高(植被坡体相干 ×3-5)但受 80 m 限制 | SKILL 正文写明「大中型滑坡适用;小型滑坡→S1 或 N2 GSLC 链」 |
| quake | 中高(同震近场保相干、偏移层) | 与 stripmap 包互为备份;36-72 h 延迟可接受 |
| 通用 | 免配额云端 3-6 步 + 全球覆盖 | `loop_closure` 不可用的诚实降级(§2.5) |

---

## 5. 落地设计草案(分阶段改动面)

设计基线:复刻 **HyP3 模式**——cap1 导入产品目录、`cloud_completed` 标记云端已完成步骤、cap7 换 processor。全部机制(`Scenario.cloud_completed`、`ArtifactSpec` 候选列表、`Method.requires_credentials`、场景包 overrides)现成,无需 executor/planner 新机制。

### 5.1 阶段 N1:GUNW 导入 → MintPy 链(建议 1-2 个月内,核心 10-16 人日)

改动面按文件:

| 文件 | 改动 | 人日 |
|---|---|---|
| `registry/kinds.py` | layout 词表加 `nisar_gunw`(HDF5/UTM);新增 `KINDS["GSLC"] = DataKind("GSLC", "nisar_h5", "utm")` 预留 N2 | 0.5 |
| `registry/bridges.py` | `Bridge("nisar_to_mintpy", from_layout="nisar_gunw", to_layout="mintpy_h5", impl=prep_nisar, official=True)`——桥即 MintPy 官方 prep,无自建桥风险(对比 pystamps 桥的三大难点,这里是零) | 含上 |
| `registry/capabilities.py` | cap1 加 `Method("nisar_gunw_import", engine="-", recommend 场景内)` 与可选 `Method("nisar_gunw_download", engine="asf_api", requires_credentials=("earthdata",))`;params 增 `track/frame`(science,进指纹);artifacts 增 `ArtifactSpec("nisar_gunw", ("nisar_gunw",), kind="IFG_UNWRAPPED", layout="nisar_gunw", required=False)`(对齐 hyp3 的 unw 声明形态);cap1 disk 公式分支 `pairs * 2.4`;cap2 说明 NISAR 链 DEM 为 GeoTIFF(见下行);cap11 `loop_closure.why` 注明 GUNW 链不可用 | 1 |
| `engines/localdata.py` | 新脚本模板 `_NISAR_IMPORT_PY`:识别目录内 `NISAR_L2_*GUNW*.h5`,目录联接/复制(复用现有 link_or_copy),**登记清单**(按 track/frame 分组、按日期对排序、检查链断点/CRID 混用并显式告警)——形态完全对齐现有 HyP3 导入脚本 | 2-3 |
| `engines/mintpy.py` | `_CFG_TEMPLATE` 增 nisar 渲染分支(§2.1 模板键;分支选择看 run["chain"] cap1 方法);cap8 params 透传辅助栈开关(ion/tropo/SET 三选,与 `tropo_era5_pyaps` 声明互斥关系,防重复校正——同 ROADMAP-isce3 风险 3 的处理纪律) | 2-3 |
| cap2(`localdata.py` dem_local 分支) | dem_local 放宽:GeoTIFF 模式不再强制 `.xml` sidecar(按 params 或文件后缀判定 isce2/gtiff 形态);或加 `dem_geotiff_local` 新方法保持 dem_local 语义不变(倾向后者,不动已验证路径) | 1-2 |
| `registry/scenario_packs/nisar_gunw/` | 新场景包:`cloud_completed: [3,4,5,6]`(**cap2 不在内**——prep_nisar 需要 DEM);SKILL.md 正文写 §1.4 已知问题、§2.5 网络约束、§4 场景边界;pick_7=mintpy_sbas | 1 |
| 下载器(可选,N1b) | `nisar_gunw_download`:asf_search 按 AOI/track/frame/日期检索 GUNW、幂等下载(断点续传;`replay="safe"` 语义对齐现 cap1) | 2-3 |
| 测试 + 真实验证 | 单测:h5py 造最小 GUNW 结构 fixture(复用 `INSAR_WSL_HOSTROOT` 测试缝思路);端到端:选 1 个帧 8-10 对(下载 15-25 GB),MintPy Windows 侧直跑(**无需 WSL——MintPy 引擎本来就双端**,NISAR 链不碰 isce2) | 3-4 |

**合计:核心 10-13,含下载器 12-16 人日**。关键省钱点:不新增引擎、不新增凭据、不动 executor;MintPy v1.6.4 一个版本号升级动作(现网 MintPy 版本需确认 ≥1.6.4,升级验证 0.5 人日计入测试)。

### 5.2 阶段 N2:GSLC → ISCE3/dolphin 自算链(6 个月+,前置条件驱动)

**前置**:ROADMAP-isce3 阶段 1/2 完成(insar3 env + dolphin 方法族封装)——N2 本质是「dolphin 方法族换输入源」,不是独立工程。

| 项 | 改动 | 人日 |
|---|---|---|
| cap1 | `Method("nisar_gslc_import"/"nisar_gslc_download")`;**AOI 子集读取强制**(opera-utils `NisarGslcSearch`;参照 sweets `--source nisar-gslc` 形态);disk 声明按 §3.3 GSLC 行给出并硬提示 | 2-3 |
| cap4-6 | dolphin 配置注入 NISAR 波长(opera-utils `get_nisar_wavelength`,规避 [#704](https://github.com/isce-framework/dolphin/issues/704) 的「弧度陷阱」);其余复用 isce3 迁移的 dolphin 三段式声明 | 2-3 |
| 验证 | 同 AOI 三链对比:GUNW→MintPy vs GSLC→dolphin vs S1 链(cap11 素材) | 3-4 |
| RSLC 自算对(hyp3-isce3 形态) | 仅在「必须自定义对且 dolphin 不适用」时立项,默认不做(RSLC 43 GB/景的下载与算力代价) | 0(挂起) |

合计 **8-12 人日**(不含 isce3 迁移本体)。

### 5.3 跟踪清单(每季度看一眼)

1. **validated 重处理进度**(2026 Q4,全记录回溯到 2025-10;短名切换 `*_PROVISIONAL_V1`→`NISAR_L2_GUNW_V1`)——触发我们把场景包默认集合切到 validated;
2. **DISP-NI 发布**(2026-09,北美)——北美 AOI 的 cap7 云端化选项;
3. **Harmony GUNW 空间裁剪** 与 **HyP3 定制 GUNW**([ASF 路线图](https://nisar-docs.asf.alaska.edu/roadmap/))——前者改写 §3.2 结论,后者即 `hyp3_submit` 的 NISAR 版;
4. MintPy `mintpy.load.band`(SSAR)进正式发行版;LiCSBAS2 NISAR 路线演化(作竞品参照);
5. dolphin #704 波长自动识别是否官方修复(N2 前提之一);
6. CRID 升版(>P05023)时 prep_nisar 元数据兼容性(§1.2 的 #1485 教训)。

### 5.4 风险表

| # | 风险 | 等级 | 缓解 |
|---|---|---|---|
| 1 | GUNW 最近邻链无冗余/无闭合环,解缠错误无法环路自检 | 高(科学) | 声明层诚实降级(cap11 用 coherence_mask + 双链 crossval);低相干区提示 N2 自算网络 |
| 2 | 高纬电离层残差(provisional 已知问题) | 中高 | permafrost 包默认启用 ionStack 校正;SKILL 正文写明质量边界;等 validated 版 |
| 3 | 产品规范/CRID 演化打破 prep_nisar(#1485 先例) | 中 | pin MintPy ≥1.6.4;CRID 记入指纹(`tool_upgraded` 失效传播现成);导入脚本显式记录 CRID 并拒绝混用 |
| 4 | prep_nisar 失败被 load_data 吞掉(只 warn) | 中 | run_ok 以 `artifact_exists(ifgramStack.h5)` + `not_all_nan` 兜底(现声明形态已具备) |
| 5 | 跨帧 AOI 单帧假设破裂 | 中 | N1 声明「AOI 须落单帧」(planner 收窄理由);跨帧走 ARIA-tools 路径(N1b 增强项) |
| 6 | 整帧下载不可避免(无服务器端裁剪) | 低中 | 磁盘公式如实声明(§3.3);ARIA-tools 虚拟访问作大 AOI 逃生门 |
| 7 | GSLC 波长常数陷阱(N2) | 中 | 强制 opera-utils 元数据读取,禁用硬编码波长;进方法 `why` 说明 |
| 8 | BETA/PROVISIONAL 混用产生系统性偏差 | 低 | 导入清单按 CRID 分组校验,混用显式失败(§1.4 官方警告) |

---

## 6. 来源

**NISAR 官方(任务/产品/获取)**:[NISAR Data User Guide(ASF)](https://nisar-docs.asf.alaska.edu/) · [产品总览](https://nisar-docs.asf.alaska.edu/products-overview/) · [产品规范列表](https://nisar-docs.asf.alaska.edu/product-specification/)(GUNW 规范 [JPL D-102272 Rev F](https://nisar.asf.earthdatacloud.nasa.gov/NISAR-SAMPLE-DATA/DOCS/NISAR_D-102272_RevE_NASA_SDS_Product_Specification_L2_GUNW_Nov8_2024_w-sigs.pdf)、GSLC 规范 [JPL D-102269](https://nisar.asf.earthdatacloud.nasa.gov/NISAR-SAMPLE-DATA/DOCS/NISAR_D-102269_RevE_NASA_SDS_Product_Specification_L2_GSLC_Nov8_2024_w-sigs.pdf)) · [GUNW 页](https://nisar-docs.asf.alaska.edu/gunw/) · [GSLC 页](https://nisar-docs.asf.alaska.edu/gslc/) · [数据格式(HDF5)](https://nisar-docs.asf.alaska.edu/data-format/) · [可用数据与时间线](https://nisar-docs.asf.alaska.edu/availability-overview/) · [PROVISIONAL 已知问题](https://nisar-docs.asf.alaska.edu/provisional-known-issues/) · [观测计划](https://nisar-docs.asf.alaska.edu/observation-plan/) · [数据获取总览](https://nisar-docs.asf.alaska.edu/access-overview/) · [Earthdata Search 指南](https://nisar-docs.asf.alaska.edu/earthdata-search/) · [Vertex 指南](https://nisar-docs.asf.alaska.edu/vertex/) · [asf_search 指南](https://nisar-docs.asf.alaska.edu/asf-search/) · [GDAL 流式教程](https://nisar-docs.asf.alaska.edu/gdal/) · [ASF 工具路线图(Harmony/HyP3 计划)](https://nisar-docs.asf.alaska.edu/roadmap/) · [About NISAR(仪器)](https://nisar-docs.asf.alaska.edu/nisar-intro/) · [L 波段公开发布公告(2026-07-20)](https://asf.alaska.edu/notices/nisar-l-band-data-now-publicly-available/) · [GUNW validated 集合目录页](https://www.earthdata.nasa.gov/data/catalog/asf-nisar-l2-gunw-v1-1) · [样例数据教程(HDF5 结构表)](https://www.earthdata.nasa.gov/learn/tutorials/work-nisar-sample-data) · [eoportal 任务页(轨道/覆盖)](https://www.eoportal.org/ftp/satellite-missions/n/NISAR-25032021/NISAR.html) · [CMR STAC track/frame 约定](https://github.com/nasa/cmr-stac/issues/413) · [Bhoonidhi NISAR(S 波段)](https://bhoonidhi.nrsc.gov.in/NISAR/) · [ISRO ARSET 培训 PDF(产品均值体量表)](https://www.earthdata.nasa.gov/s3fs-public/2026-07/arset-2026-nisar-part2-isro.pdf) · granule 实测体量:CMR API(`cmr.earthdata.nasa.gov/search/granules.json?short_name=NISAR_L2_GUNW_PROVISIONAL_V1`,检索日 2026-08-13)

**数据体量(任务级)**:[Getting Ready for NISAR(85 TB/天、140 PB)](https://www.earthdata.nasa.gov/news/feature-articles/getting-ready-nisar-managing-big-data-using-commercial-cloud) · [发射后数据预期](https://www.earthdata.nasa.gov/news/now-that-nisar-launched-heres-what-you-can-expect-from-the-data) · [AWS/JPL SDS 案例(70 TB/天)](https://aws.amazon.com/solutions/case-studies/nasa-jpl-spot-case-study/)

**MintPy 衔接**:[prep_nisar 初版 #1035(v1.5.3)](https://github.com/insarlab/MintPy/releases) · [NISAR 装载大修 PR #1487(2026-04-22)](https://github.com/insarlab/MintPy/pull/1487) · [v1.6.4 发行说明(2026-07-25,含 #1487 与 OPERA L4 TROPO)](https://github.com/insarlab/MintPy/releases) · [SSAR 波段支持 PR #1494](https://github.com/insarlab/MintPy/pull/1494) · [元数据演化 issue #1485](https://github.com/insarlab/MintPy/issues/1485) / [修复 PR #1478](https://github.com/insarlab/MintPy/pull/1478) · [cli/prep_nisar.py 源码](https://github.com/insarlab/MintPy/blob/main/src/mintpy/cli/prep_nisar.py) · [load_data.py 源码(nisar 分支模板键)](https://github.com/insarlab/MintPy/blob/main/src/mintpy/load_data.py) · 孵化 fork:[nisar-solid/MintPy](https://github.com/nisar-solid/MintPy)

**ARIA-tools / LiCSBAS2**:[ARIA-tools(NISAR GUNW 支持声明)](https://github.com/aria-tools/ARIA-tools) · [ariaDownload NISAR 支持 #480(2026-02-26)](https://github.com/aria-tools/ARIA-tools/commit/72d545905b75e5a3978342a30209f72d04cc6b6d) · [releases(虚拟访问/S3/拼接/iono)](https://github.com/aria-tools/ARIA-tools/releases) · [团队公告(2026-02-28)](https://www.linkedin.com/posts/david-bekaert-49652717_available-nisar-data-nisar-data-user-guide-activity-7433640242907152384-GIBx) · [LiCSBAS2 v2.0.0 NISAR 支持 commit(2026-03-12)](https://github.com/yumorishita/LiCSBAS2/commit/98bbfffc71b48806bf92073d41d8fae1c7489e2e) · [NISAR 符号翻转 PR #152(2026-05-30)](https://github.com/yumorishita/LiCSBAS2/pull/152) · [LiCSBAS2 wiki 2.7(ARIA/NISAR 工作流)](https://github.com/yumorishita/LiCSBAS2/wiki/2_7_ARIA)

**ISCE3/dolphin 生态(阶段 N2)**:[dolphin NISAR GSLC 波长 bug #704](https://github.com/isce-framework/dolphin/issues/704) · [opera-utils 波长读取 PR #225](https://github.com/opera-adt/opera-utils/pull/225) · [sweets(--source nisar-gslc)](https://github.com/isce-framework/sweets) · [hyp3-isce3(RSLC 对→GUNW 插件)](https://github.com/ASFHyP3/hyp3-isce3) · [isce3-builder(社区批量对)](https://github.com/CBurton90/isce3-builder) · [ASF on-demand 表态(Earthdata Forum)](https://forum.earthdata.nasa.gov/viewtopic.php?t=7601) · [OPERA 产品页(DISP-NI 2026-09)](https://www.jpl.nasa.gov/go/opera/products/) · [OPERA DISP 简报](https://www.earthdata.nasa.gov/s3fs-public/2025-01/OPERA-DISP_Nov2024.pdf) · [DISP-NI PGE ER2.0(2026-03-24)](https://github.com/nasa/opera-sds-pge/releases/tag/6.0.0-er.2.0-disp-ni)

**asf_search**:[Best Practices(NISAR 检索/stack)](https://docs.asf.alaska.edu/asf_search/BestPractices/) · [Searching(NISAR 专属关键字)](https://docs.asf.alaska.edu/asf_search/searching/)

**L vs C 波段文献**:[青藏高原冻土 ALOS/ALOS-2/S1 对比(RS 2022)](https://www.mdpi.com/2072-4292/14/8/1870) · [白鹤滩库区滑坡 C/L 适用性(RS 2024)](https://doi.org/10.3390/rs16091591) · [茂县滑坡 C/L 对比(RS 2023)](https://www.mdpi.com/2072-4292/15/18/4538) · [阿尔卑斯岩坡 L 波段监测(NHESS 2026)](https://nhess.copernicus.org/articles/26/2579/2026/) · [多波段植被区相干性评估(ISPRS 2018)](https://doi.org/10.5194/isprs-archives-xlii-3-2277-2018)

**本仓库**:`src/insar_agent/registry/capabilities.py`(cap1 三方法/HyP3 unw 声明形态) · `src/insar_agent/registry/kinds.py` · `src/insar_agent/registry/bridges.py` · `src/insar_agent/engines/localdata.py`(双模导入) · `src/insar_agent/engines/mintpy.py`(processor=hyp3 渲染) · `src/insar_agent/registry/scenarios.py`(cloud_completed 机制) · `docs/ROADMAP-isce3.md`(§1.3 路径 C、§3 阶段 3 决策点 5)

> 可信度说明:版本号/日期/体量均来自官方文档、GitHub 原文或 CMR API 实测(检索日 2026-08-13);GUNW/GSLC 单品体量为 8 个 granule 的抽样范围而非全集统计;MintPy 栈体量为按 posting 推算的量级估计,未实跑;人日为本项目自估。prep_nisar 对 PROVISIONAL(P05023)数据的端到端兼容性、`nisar_gunw_import` 清单脚本的配对完整性规则,须在阶段 N1 首次真实运行时以实测为准。
