---
name: 04-interferogram
description: "当需要为流水线第 4 步干涉图生成选择方法(isce2_ifg_multilook / snap_interferogram / isce2_stripmap_ifg)、设定 range_looks/azimuth_looks 多视比、规划 pairs 干涉网络规模、或诊断全噪干涉图、轨道斜坡条纹、地形相关条纹、近场条纹混叠、磁盘超预算等问题时使用。"
capability: 4
version: "1.0.0"
applies_to: all
---

# 干涉图生成(Interferogram Generation)

能力速览(以 `registry/capabilities.py` id=4 为准):方法 `isce2_ifg_multilook`(默认、推荐,小基线网络按时空基线剪枝而非全组合)/ `snap_interferogram` / `isce2_stripmap_ifg`(仅 stripmap_coseismic,stripmapApp 的 split_range_spectrum→filter 段);science 参数 `range_looks`(默认 10,1–40)、`azimuth_looks`(默认 2,1–40)、`pairs`(默认 11,1–5000);resource 参数 `threads`(默认 8);输入 `coreg`;产物 `ifg`(IFG_WRAPPED,候选 `data/ifg`、`isce2/interferogram/topophase.flat`、`isce2/interferogram/filt_topophase.flat`);磁盘 `pairs * 0.34` GB(峰值 ×1.4);total 超时 8 h;io=heavy。

## 适用判据

- **`isce2_ifg_multilook`(默认、推荐)**:标准路径,多视比可调;小基线(small baseline)网络按时间基线(temporal baseline)与垂直基线(perpendicular baseline)双阈值剪枝,不做全组合——对数受台账 `max_temporal_baseline = 120` 天(literature 级,Yunjun et al. 2019 宽松阈值+冗余网络主张)约束。
- **`snap_interferogram`**:SNAP 链对应步骤,仅当整链走 SNAP 布局时用,不与 ISCE2 布局混搭。
- **`isce2_stripmap_ifg`(scenario_only=stripmap_coseismic)**:stripmapApp 的 split_range_spectrum→filter 区间;分频谱(split range spectrum)占位步不可跳过——pickle 链约束(`engines/isce2.py` `_STRIPMAP_RANGES`),跳步续跑必在首个产品引用处以 NoneType 崩溃。实测(ALOS Baja):本段含在阶段 1(startup→filter 约 20 min)内,全链 33 min / 峰值 32 GB。
- 场景差异:quake(HyP3)本步云端已完成;stripmap_coseismic 单干涉对,`pairs` 实际不起网络作用;SBAS 时序(permafrost 等)网络设计是本步的核心科学决策。

## 参数启发式

- **`range_looks` × `azimuth_looks`(默认 10×2)**:
  - 几何直觉:S1 IW 单视像元约 2.3 m(斜距向)× 14.1 m(方位向),10×2 得约 23 m × 28 m 的近方形地面像元,与 30 m DEM 匹配;保持"距离:方位 ≈ 5:1"的比例才能得到方形地面像元(HyP3 的 20×4 → 80 m 产品是同一比例的更粗档)。
  - stripmap(ALOS)比例倒置(2026-08-13 C3 修正):ALOS FBS 方位像元 ~3.2 m < 地面距离像元 ~7–8 m,与 S1 相反 —— az>rg 约 2:1 才得近方形像元,10×2 会产出强矩形像元;stripmap_coseismic 场景包已覆写 2×4(规划层声明进新计划指纹,分段 XML 维持实测形态不渲染 looks)。
  - 物理作用:相位噪声标准差随视数(number of looks)增加而下降(给定相干性,σ_φ ∝ 1/√N_L,Rodriguez & Martin 1992),残差点(residues)随之减少、解缠变稳;代价是分辨率线性变粗。
  - 往上调(20×4 甚至 30×6):低相干弱信号场景——冻土季节形变空间尺度大(km 级),牺牲分辨率换信噪比是纯赚;大范围时序也同理。
  - 往下调(5×1)或维持 10×2:同震近场高梯度——条纹率接近每像元 π 弧度时,多视平均会把条纹混叠(aliasing)成噪声,一旦发生不可恢复;近断层要保细节。L 波段(stripmap 链,λ=23.6 cm)条纹率天然比 C 波段低约 4 倍,同样形变更宽容。
  - 顺序纪律:多视是无偏降噪,滤波(第 5 步)是有偏降噪——信噪比不够先加视数,再考虑加滤波强度。
- **`pairs`(默认 11,1–5000)**:SBAS 网络对数。
  - 冗余下限:每个日期至少 2–3 个连接;闭合修正能力随冗余上升——序贯 3/5/10 连接可完全修正的错误干涉图占比上限分别为 5/20/35%(Yunjun et al. 2019 结论 2)。基准场景 Ridgecrest:7 景 11 对。
  - 冻土场景收紧:跨冻结/融化转换期的干涉对相干性系统性下降,时间基线按 S1 惯例收到 24–90 天并优先序贯短基线对(permafrost 场景包);台账的 120 天是宽松侧全局门,不是推荐值。
  - 同震场景:跨事件对是信号载体,震前-震前对用于估背景;stripmap 单对时本参数不起作用。
  - 预算联动:磁盘 `pairs * 0.34` GB × 峰值 1.4,5000 对 ≈ 2.4 TB——先算盘再设网。
- **`threads`(resource,默认 8)**:io=heavy 步骤,磁盘吞吐常先于 CPU 饱和;加线程前先确认不是 IO 瓶颈。

## 常见失败与处置

1. **症状**:某干涉对整幅椒盐噪声、完全无条纹。**根因**:该对失相干(时间基线过长、跨冻融/雪盖季、地表剧变),或第 3 步配准失败。**处置**:先查第 3 步 QA(配准失败是全对皆坏,失相干是个别对坏);确属失相干则从网络剔除该对(收紧时间基线、减 `pairs`),不要指望第 5 步强滤波起死回生。
2. **症状**:规律的平行等间距条纹贯穿全幅,与地形、形变均无关。**根因**:轨道误差(第 2 步用了 `resorb`)或基线计算错误。**处置**:回第 2 步改 `orbit=poeorb` 重跑;这不是本步参数能修的,加大多视只会把斜坡藏起来而不是去掉。
3. **症状**:条纹沿山脊山谷走、与地形起伏强相关。**根因**:DEM 残差(基准错、空洞、过时高程),经垂直基线放大进相位。**处置**:回第 2 步核 DEM QA(基准转换、覆盖、void);受影响最重的是大 B⊥ 对——网络里优先剔除大垂直基线对是立竿见影的止血。
4. **症状**:近断层条纹密不可分辨,多视后糊成噪声带。**根因**:形变梯度超过采样极限(每像元相位增量 >π,混叠)。**处置**:降 `range_looks`/`azimuth_looks`(10×2→5×1)保近场;或接受近场掩膜、把重点放在中远场,第 6 步配 `cost_mode="DEFO"`;根本性解法是换 L 波段数据(stripmap 链)。
5. **症状**:磁盘预检拒绝执行,或跑到中途磁盘满。**根因**:`pairs * 0.34` GB × 1.4 峰值超出可用空间。**处置**:减 `pairs`;加大多视(产物体积随视数积近似反比缩小);对旧 run 产物 GC;stripmap 链注意全链峰值 32 GB 的实测参照。
6. **症状**:stripmap 链恢复续跑时 NoneType 崩溃。**根因**:split_range_spectrum→filter 区间没有与上一段首尾相接(pickle 链断)。**处置**:按 `_STRIPMAP_RANGES` 固化分段从段首重跑;绝不允许"从中间某步起跑"的手工恢复。

## QA 依据

- run_ok:`exit_code == 0` 且 `ifg` 产物命中(`data/ifg` 或 `isce2/interferogram/topophase.flat`)。
- 条纹形态的物理合理性(最重要的定性判据):同震应呈围绕破裂带的蝶形瓣状条纹(Massonnet et al. 1993 的 Landers 范式);冻土应是大尺度平缓条纹;滑坡是坡体局部的紧凑条纹团。与先验形态相悖(如全幅均匀密条纹)优先怀疑轨道/DEM,而不是形变。
- 条纹连续性:TOPS 干涉图 burst 边界无锯齿/条带(配准质量的回归探针);无处理分块接缝。
- 相干性直方图形态:健康对呈双峰(相干地物峰 + 水体/植被低相干峰);整体塌缩在 <0.2 的对判失相干候选剔除;时序场景逐对排查,跨季节对重点看。
- 网络健康(SBAS):全部日期连通、无孤立子网(孤岛会让第 7 步反演退化);时间-垂直基线散点图覆盖均衡,无"单线串联"脆弱结构。
- 量纲自检:缠绕相位取值 [-π, π);topophase.flat 已去平地相位(flattened)与地形相位——若可视化出整幅线性趋势,回失败处置第 2/3 条。

## 参考文献

- Hanssen, R. F. (2001). *Radar Interferometry: Data Interpretation and Error Analysis*. Kluwer. doi:10.1007/0-306-47633-9 —— 干涉相位组成与误差源的系统论述。
- Zebker, H. A., & Villasenor, J. (1992). Decorrelation in interferometric radar echoes. *IEEE TGRS*, 30(5), 950–959. doi:10.1109/36.175330 —— 时间/基线失相干机理。
- Rodriguez, E., & Martin, J. M. (1992). Theory and design of interferometric synthetic aperture radars. *IEE Proceedings-F*, 139(2), 147–159. —— 相位噪声-相干性-视数关系。
- Just, D., & Bamler, R. (1994). Phase statistics of interferograms with applications to synthetic aperture radar. *Applied Optics*, 33(20), 4361–4368. doi:10.1364/AO.33.004361 —— 多视相位统计。
- Berardino, P., Fornaro, G., Lanari, R., & Sansosti, E. (2002). A new algorithm for surface deformation monitoring based on small baseline differential SAR interferograms. *IEEE TGRS*, 40(11), 2375–2383. doi:10.1109/TGRS.2002.803792 —— SBAS 网络思想本源。
- Yunjun, Z., Fattahi, H., & Amelung, F. (2019). Small baseline InSAR time series analysis. *Computers & Geosciences*, 133, 104331. doi:10.1016/j.cageo.2019.104331 —— 网络冗余与闭合修正定量结论;台账 max_temporal_baseline 的 ref。
- Massonnet, D., et al. (1993). The displacement field of the Landers earthquake mapped by radar interferometry. *Nature*, 364, 138–142. doi:10.1038/364138a0 与 Massonnet, D., & Feigl, K. L. (1998). *Reviews of Geophysics*, 36(4), 441–500. doi:10.1029/97RG03139 —— 同震条纹形态判读范式。
- ASF HyP3 InSAR Product Guide(hyp3-docs.asf.alaska.edu)—— 20×4 多视 80 m 产品的同比例参照。
- 仓库内:`src/insar_agent/audit/contract.yaml`(max_temporal_baseline 条目)、`docs/VALIDATION-isce2-wsl.md`(stripmap 段实测耗时/磁盘)。
