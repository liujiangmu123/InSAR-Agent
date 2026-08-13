---
name: 07-sbas-inversion
description: 当规划或诊断第 7 步时序反演(time series inversion)时使用:在 mintpy_sbas 与 pystamps_ps 之间选链、设定小基线网络阈值(max_temporal_baseline)、选择参考点(reference point)、诊断 timeseries.h5 的 NaN 区块与 MintPy 在 Windows 上的环境崩溃。
capability: 7
version: "1.0.0"
applies_to: all
---

# 时序反演(SBAS / PS)

第 7 步把解缠干涉图栈反演为形变时序。方法 id:`mintpy_sbas`(默认、推荐)与
`pystamps_ps`;产物 artifact id 为 `timeseries`(首候选 `mintpy/timeseries.h5`)。
`mintpy_sbas` 的真实运行口径是 `smallbaselineApp insar_agent.cfg --start load_data
--end invert_network`(engines/mintpy.py `_RANGES[7]`),即含 load_data、
modify_network、reference_point、quick_overview、correct_unwrap_error、
invert_network 六个 MintPy 内部步骤 —— 参考点选择与解缠误差修正都发生在本步。

## 适用判据

- **`mintpy_sbas`(小基线集 SBAS,Small Baseline Subset)**:低相干面状形变、
  分布式散射体(distributed scatterer)场景 —— 冻土(`permafrost`)、同震
  (`quake`,HyP3 路线)、农田/植被区沉降。对干涉图栈反演,冗余网络对噪声稳健。
  引擎 `mintpy`;输入 `unw`(第 6 步解缠产物,或 HyP3 云端产品直接充当,见
  quake 包 `cloud_completed: [2,3,4,5,6]`)。
- **`pystamps_ps`(永久散射体 PS,Persistent Scatterer)**:高相干点状目标 ——
  裸岩、城市人工地物,`landslide` 场景的 `pick_7`。精度上限更高
  (Ferretti et al. 2001:速度精度 < 1 mm/yr),但要求长时序:PS 候选按振幅
  离散度 D_A ≤ 0.25 初选,可靠估计需 ≥ 20–25 景;短时序结果不可信。
  **当前工程边界**:依赖 ISCE2→PyStamps 桥(`engines/bridges/isce2_to_pystamps`),
  该桥是接口边界未实现,规划器可行性收窄会将其排除 —— 诚实回退 `mintpy_sbas`
  并在报告声明单链证据。
- **单干涉对(`stripmap_coseismic`,仅 2 景)**:时序反演是形式化通道,核心产物
  是第 6 步解缠形变场;本步照走 `mintpy_sbas` 但不要在时序上过度解读。
- 景数经验下界:SBAS 通常 ≥ 10 景才谈得上网络冗余;不足时结论只到"单对形变场"
  证据级别。

## 参数启发式

本步骤在 registry(capabilities.py id=7)声明的参数只有三个,不要引用不存在的参数:

- **`network`**(science,默认 `"small_baseline"`,枚举 `small_baseline | star |
  sequential`):`small_baseline` 时空基线双阈值剪枝(默认);`sequential` 序贯
  连接 —— Yunjun et al. (2019) §6.3 的基准案例用序贯 5 连接,冗余越高闭合修正
  能力越强(序贯 3/5/10 连接可完全修正的错误干涉图占比上限 5/20/35%);`star`
  单主影像星形网络,仅在高相干区可用(PS 式拓扑,失去闭合环检查能力)。
  注意:当前 cfg 模板(engines/mintpy.py `_CFG_TEMPLATE`)只渲染
  `mintpy.network.tempBaseMax`,网络拓扑在 HyP3 路线已由云端产品对固化。
- **`max_temporal_baseline`**(science,默认 `120` 天,范围 6–730,渲染为
  `mintpy.network.tempBaseMax`):台账锚点(contract.yaml,source=literature,
  status=OK)。取值参照系:
  - MintPy 官方默认不限(`tempBaseMax = auto (no)`),主张宽松阈值 + 冗余网络,
    再用数据驱动修剪(Yunjun et al. 2019 §6.3);
  - Sentinel-1 惯例区间 24–90 天:同数据源的 ASF HyP3+MintPy Ridgecrest 教程取
    24 天,GMTSAR S1 教程取 50 天;
  - `permafrost` 场景必须收紧到 24–90 天:跨冻结/融化季的干涉对相干性系统性
    下降,优先序贯短时基线对并保证冗余;
  - L 波段(ALOS)可放宽到千天量级(Yunjun et al. 2019 §5.1 案例 < 1800 天),
    代价是大垂直基线放大 DEM 误差(需第 8 步 `dem_error` 校正兜底)。
- **空间(垂直)基线**:本步骤**未声明**该参数 —— Sentinel-1 轨道管直径
  100–200 m(RMS),12 天对 bperp 通常 < 165 m,天然小基线,不构成选对约束
  (ESA 技术说明 ESA-EOPG-EOPGMQ-TN-2024-12;Manunta et al. 2019:S1 bperp
  标准差 50 m)。经典参照:ERS 时代 SBAS 取 bperp < 130 m(Berardino et al.
  2002 §V)。若换 L 波段/老平台确需垂直基线剪枝,走 MintPy 模板键
  `mintpy.network.perpBaseMax`(当前 cfg 模板未渲染,须改模板而非造参数)。
- **`parallel_workers`**(resource,默认 `4`,范围 1–16,不进指纹):渲染为
  `mintpy.compute.numWorker`(`cluster = local`);子进程线程数被 engines/mintpy.py
  钳制(`OMP_NUM_THREADS`/`MKL_NUM_THREADS` ≤ 8,本机重型计算管控)。内存预算
  声明 16 GB,大幅面先用 `INSAR_SUBSET_LALO`(渲染 `mintpy.subset.lalo`)裁剪,
  而不是加 worker。
- **参考点选择**(`INSAR_REFERENCE_LALO` 环境变量 → `mintpy.reference.lalo`;
  Ridgecrest 实测默认 `391.5e4,45e4`,HyP3/UTM 布局下按产品坐标系填写):
  - 自动选取:MintPy 在相干 ≥ 0.85 的像元中随机选(`mintpy.reference.minCoherence
    = auto (0.85)`);
  - 人工选点四原则(Yunjun et al. 2019 §4.3):高相干、无强大气湍流、贴近感兴趣区
    (AOI)且高程相近(减小分层大气差)、**位于稳定区远离形变区**;
  - 参考点落在形变区的后果是全场速度整体偏移(InSAR 速度是相对量),见
    "常见失败"第 5 条。
- **反演权重**:cfg 固定渲染 `mintpy.networkInversion.weightFunc = no`,即经典
  SBAS 均权配置(MintPy 官方注释:"SBAS (Berardino et al., 2002) =
  minNormVelocity (yes) + weightFunc (no)";契约台账 `weightFunc` 条目)。

## 常见失败与处置

1. **timeseries.h5 出现成片 NaN 区块 / run_ok 的 `not_all_nan` 检查失败** →
   网络断连(某些日期无干涉对连接,LiCSBAS 语义的 n_gap > 0)或 modify_network
   剔除过多导致像元反演欠定 → 补干涉对(HyP3 路线加对重新导入)或放宽
   `max_temporal_baseline`;确认 MintPy 的 MST(最小生成树)保留策略未被关闭;
   低相干区成片 NaN 属掩膜正常行为,与断连区分开。
2. **deramp/反演阶段第 2 景硬崩、无 Python 栈、退出码异常(Windows)** →
   conda-forge Windows 默认 BLAS=MKL,MKL 2024 多线程延迟加载 bug
   (0xC06D007F,本机 i9-13900K 实测) → 引擎环境执行
   `conda install "libblas=*=*openblas"` 切 OpenBLAS(README「环境坑位记录」;
   本仓库已实测,setup 向导把它列为必做步骤)。
3. **smallbaselineApp 启动即 UnicodeDecodeError(中文 Windows)** → MintPy
   `read_template` 不指定编码,按 GBK 读 UTF-8 配置崩 → cfg 必须纯 ASCII
   (engines/mintpy.py 模板已保证,自定义模板勿加中文注释)+ 子进程
   `PYTHONUTF8=1`(执行器已注入);同样约束适用第 8、9 步(共用同一份 cfg)。
4. **load_data 报 0 个干涉对 / 找不到输入** → `mintpy.load.unwFile` 等 glob 与
   数据布局不匹配(cfg 按 HyP3 处理器渲染 `../hyp3/*/*unw_phase_clipped.tif`)→
   核对工作区 `hyp3/` 目录结构与文件名后缀(clipped 与否)、
   `mintpy.load.processor` 是否与数据来源一致。
5. **速度/时序全场整体偏移,稳定区不为零** → 参考点落在形变区或低相干像元 →
   重选参考点(改 `INSAR_REFERENCE_LALO`,按上文四原则),仅重跑第 7 步及下游
   (级联标脏会自动处理)。
6. **`pystamps_ps` 被规划器排除或桥装配失败** → ISCE2→PyStamps 桥是未实现的
   接口边界(par 字段自洽/TCN 基线/big-endian 三难点,无真值环境不猜测实现)→
   回退 `mintpy_sbas`,报告声明"单链证据,无 PS/SBAS 交叉验证",第 11 步随之
   降级(见 11-crossval-qa)。

## QA 依据

- **run_ok 三判定**(registry 声明):`exit_code == 0`、`artifact_exists
  (timeseries)`、`not_all_nan(timeseries)` —— not_all_nan 是本步唯一的栅格级
  硬检查,防"跑完但全空"的假成功。
- **可靠像元掩膜**:时间相干(temporal coherence)≥ 0.7(Pepe & Lanari 2006
  原始定义;MintPy `minTempCoh = auto (0.7)`);网络冗余被削弱时(先做过空间
  相干阈值化)提高到 0.8(Yunjun et al. 2019 §6.5.5);掩膜后可靠像元数
  ≥ 100(MintPy `minNumPixel = auto (100)`)。
- **平均空间相干**:`invert_network` 副产物 `avgSpatialCoh.h5`,第 11 步 qa.json
  的 `mean_coherence` 指标重解析它;Ridgecrest 实测 0.949(高相干基准,植被区
  预期显著更低)。
- **时序闭合环残差**:三元组闭合相位整数模糊 T_int 指示图定位解缠误差
  (Yunjun et al. 2019 §3.2 式(8)-(9),quick_overview 步输出);图级惯例:
  闭合环相位 RMS > 1.5 rad 判问题干涉图(LiCSBAS `p12_loop_thre` 默认;
  Morishita et al. 2020 §2.4.2)。修正手段 bridging/phase_closure(MintPy
  `correct_unwrap_error` 步,默认关)按场景显式开启,高冗余网络才用 phase_closure。
- **阈值台账纪律**:`max_temporal_baseline`(120 天)在 contract.yaml 为
  literature/OK;凡 PENDING 阈值只出 warning 不硬拦(AGENT-DESIGN §4.13)。

## 参考文献

- Berardino P., Fornaro G., Lanari R., Sansosti E. (2002). A new algorithm for
  surface deformation monitoring based on small baseline differential SAR
  interferograms. *IEEE TGRS* 40(11):2375-2383. doi:10.1109/TGRS.2002.803792
  (SBAS 原始文献;bperp < 130 m、相干阈值 0.25)
- Yunjun Z., Fattahi H., Amelung F. (2019). Small baseline InSAR time series
  analysis: Unwrapping error correction and noise reduction. *Computers &
  Geosciences* 133:104331. doi:10.1016/j.cageo.2019.104331(MintPy 方法论文;
  §3.2 闭合相位、§4.3 参考点准则、§6.3 网络冗余、§6.5 时间相干阈值)
- Ferretti A., Prati C., Rocca F. (2001). Permanent scatterers in SAR
  interferometry. *IEEE TGRS* 39(1):8-20. doi:10.1109/36.898661(PS 原始文献;
  D_A ≤ 0.25、速度精度 < 1 mm/yr)
- Hooper A., Zebker H., Segall P., Kampes B. (2004). A new method for measuring
  deformation on volcanoes and other natural terrains using InSAR persistent
  scatterers. *GRL* 31:L23611. doi:10.1029/2004GL021737(StaMPS 系 PS 方法,
  pystamps 路线的方法学源头)
- Pepe A., Lanari R. (2006). On the extension of the minimum cost flow algorithm
  for phase unwrapping of multitemporal differential SAR interferograms.
  *IEEE TGRS* 44(9):2374-2383. doi:10.1109/TGRS.2006.873207(时间相干定义)
- Casu F., Manzo M., Lanari R. (2006). A quantitative assessment of the SBAS
  algorithm performance for surface deformation retrieval from DInSAR data.
  *RSE* 102:195-210. doi:10.1016/j.rse.2006.01.023(SBAS 速度精度 ~1 mm/yr)
- Manunta M. et al. (2019). The parallel SBAS approach for Sentinel-1 IW
  deformation time-series generation. *IEEE TGRS* 57(9).
  doi:10.1109/TGRS.2019.2904912(S1 小基线特性)
- MintPy 默认配置 smallbaselineApp.cfg:
  https://github.com/insarlab/MintPy/blob/main/src/mintpy/defaults/smallbaselineApp.cfg
  (tempBaseMax/minTempCoh/minNumPixel/reference.minCoherence 默认值出处)
- ASF HyP3+MintPy 时序教程(Ridgecrest,max_temporal_baseline=24 天):
  https://github.com/ASFHyP3/hyp3-docs/blob/main/docs/tutorials/hyp3_insar_stack_for_ts_analysis.ipynb
- ESA 技术说明《Increase of Sentinel-1A Orbital Tube: impact on interferometry》
  (ESA-EOPG-EOPGMQ-TN-2024-12, 2024):S1 轨道管 100→200 m RMS
