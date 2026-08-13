---
name: 01-data-acquisition
description: "当需要为流水线第 1 步选择数据获取路线(asf_search_slc / hyp3_submit / local_import)、设定 scenes/dates/platform/source 参数、或诊断数据检索为空、下载超时、HyP3 配额与凭据失败、local_import 目录核验不过等问题时使用。"
capability: 1
version: "1.0.0"
applies_to: all
---

# 数据获取(Data Acquisition)

能力速览(以 `registry/capabilities.py` id=1 为准):方法 `asf_search_slc` / `hyp3_submit` / `local_import`(默认 `local_import`);参数 `scenes`(int,2–200,默认 7)、`platform`(默认 "sentinel-1")、`dates`(默认 "2019-06-10..2019-08-15")、`source`(local_import 数据源目录);产物 `slc`(候选 `data/slc`、`hyp3`),可选 `unw`(HyP3 路线)与 `era5`(`mintpy/inputs/ERA5.h5`);磁盘预算 `scenes * 8` GB(2026-08-13 C4 修正:双极化 IW SLC 未压缩 ≈8 GB/景,下载+解压峰值另计、由 peak_multiplier 1.6 覆盖);`replay="safe"`(检索/校验幂等,下载有断点续传,重跑安全)。

## 适用判据

三条路线的选择是"控制权 vs 成本"的权衡,不是简单的优先级:

- **`local_import`(默认、推荐)**:数据已在本地或 WSL 工作区。两类合法输入:HyP3 产品目录(含成对 `*_unw_phase.tif` / `*_corr.tif`)或 SLC(Single Look Complex,单视复数影像)目录。`source` 为 POSIX 绝对路径时走 WSL 工作区模式:只读核验、登记 `data/slc/manifest.json`,不搬运字节。`data_ready: true` 的场景(quake 的 Ridgecrest HyP3 产品、stripmap_coseismic 的 ALOS Baja raw 对)一律走此路。
- **`asf_search_slc`**:需要原始 SLC 且要对第 2–6 步保留完整控制权时选它——自定多视比、滤波强度、解缠代价函数,或后续要走 PS(Persistent Scatterer,永久散射体)链(PS 必须从 SLC 起算,HyP3 成品进不了 PS 链)。代价:磁盘 `scenes * 8` GB、下载耗时(total 超时 6 h)、本机需具备 2–6 步引擎。
- **`hyp3_submit`**:ASF HyP3 云端 INSAR_GAMMA 处理。第 2–6 步由云端完成(场景包声明 `cloud_completed: [2,3,4,5,6]`,计划时标 skipped),**失去全部中间产物控制权**——不能重滤波、不能换解缠参数。适用:快速同震响应、本机无 ISCE2/SNAPHU 引擎、教学演示。需 `earthdata` 凭据且受 ASF 配额限制。
- 场景差异:quake → HyP3 产品导入(解缠相位现成);stripmap_coseismic → 必须 `local_import`(ALOS raw 已装配在 WSL 工作区);permafrost(数据待获取)→ `asf_search_slc` 或 HyP3 二选一,取决于是否需要控制 4–6 步参数(低相干区通常需要,倾向 SLC 路线);landslide → PS 链,只能 SLC 路线。

## 参数启发式

- **`scenes`(2–200,默认 7)**:
  - 注册表 hint 已按场景分层(2026-08-13 C5 修正):同震单对=2;SBAS ≥15–20;
    PS ≥20–25(Crosetto 2016 综述;Berardino 2002 经典案例 44 景)。
  - 同震(quake / stripmap_coseismic):最少 2 景(震前/震后各一),震后景越早越好——推迟一个重访周期就多混入一段震后余滑并加剧时间失相干(temporal decorrelation)。
  - SBAS(Small Baseline Subset,小基线集)时序:≥15–20 景才谈得上网络冗余;闭合修正能力随冗余上升(序贯 3/5/10 连接可完全修正的错误干涉图占比上限 5/20/35%,Yunjun et al. 2019 结论 2)。基准场景 Ridgecrest 为 7 景/11 对,属"能跑通"下限而非理想配置。
  - PS 链(landslide):≥20–25 景,短于此相位稳定性估计不可靠(Ferretti et al. 2001;PSI 综述惯例)。
  - 冻土(permafrost):景数由跨度决定——周期项可靠拟合需 ≥2 个完整年循环(观测 <14 个月时周期与趋势不可分,Li et al. 2019),S1 12 天重访 2 年约 60 景,预算紧张可稀释采样但每个冻结/融化季都要有覆盖。
  - 上限约束:磁盘预检按 `scenes * 8` GB 估算(2026-08-13 C4 修正,未压缩;
    下载 zip ≈4–4.5 GB/景与解压并存的峰值另计),200 景 ≈ 1.6 TB,先看盘再填数。
- **`dates`(格式 "YYYY-MM-DD..YYYY-MM-DD")**:同震取跨事件日的最短包络;冻土取 ≥2 整年且首尾对齐同一季相(便于周期项相位对齐);时序滑坡越长越好。改 `dates` 是 science 参数变更,必然触发下游全链失效重算——补数据宁可一次补足。
- **`platform`(默认 "sentinel-1")**:C 波段(5.55 cm)对植被和冻融循环敏感;植被区/大梯度同震优先考虑 L 波段 ALOS——但注意本参数不切换处理链,ALOS raw 走的是 stripmap_coseismic 场景对 3–6 步的方法覆写,数据源由 `source` 指定。
- **`source`(仅 `local_import` 消费)**:HyP3 产品目录或 SLC 目录;stripmap 场景指向含 `IMG-HH-*` / `LED-*` 平铺文件与 `dem/dem.wgs84` 的 WSL 工作区(如 `/home/insar/work/baja`)。science 输入进指纹:换数据源 = 换实验,不要复用同一 run 改 source。

## 常见失败与处置

1. **症状**:检索返回 0 景或远少于预期。**根因**:`dates` 区间写错(格式必须带 `..` 分隔)、`platform` 拼写不对、或该轨道该时段确无采集。**处置**:核对 `dates` 与 `platform` 参数;放宽日期区间重试;用 ASF Vertex 网页端同条件人工复核,确认是参数问题还是数据本身不存在。
2. **症状**:下载进行中被 idle 超时(1800 s 无输出)掐断。**根因**:网络限速或 ASF 端节流。**处置**:直接重跑本步——`replay="safe"`,断点续传不浪费已下载字节;仍频繁超时则错峰执行,不要并发多路下载(io=heavy,系统限单并发)。
3. **症状**:`hyp3_submit` 提交被拒或云端作业 FAILED。**根因**:`earthdata` 凭据缺失/过期(方法声明 `requires_credentials=("earthdata",)`),或月度配额耗尽。**处置**:先补凭据再重提;配额耗尽等重置,或改走 `asf_search_slc` + 本地 2–6 步(注意先做引擎可用性预检)。
4. **症状**:`local_import` 退出码为 0 之外,或 `artifact_exists(slc)` 不过。**根因**:`source` 目录布局不符合两类合法输入之一、路径拼写错、WSL 模式没用 POSIX 绝对路径。**处置**:核对 `source` 指向;HyP3 目录应含成对 `*_unw_phase.tif`(每对 10 文件);SLC 目录应含 SAFE/zip 或已解压景;stripmap 场景按包内固化路径,不要手改。
5. **症状**:磁盘预算预检直接拒绝执行。**根因**:`scenes * 8` GB 超出可用空间(峰值另乘 1.6)。**处置**:减 `scenes`、对旧 run 产物做 GC、或改 HyP3 路线(Ridgecrest 实测 11 对产品仅 402 MB)。
6. **症状**:个别景解压失败或文件尺寸异常偏小。**根因**:传输截断产生损坏文件。**处置**:删除该景后重跑本步(断点续传会只补缺);对照 ASF 公布的字节数核验;损坏景不剔除会在第 3 步配准处以更隐晦的方式崩。

## QA 依据

- run_ok 双判定(与 registry 声明一致):`exit_code == 0` 且 `slc` 产物在候选路径(`data/slc` 或 `hyp3`)命中。
- 清单完备性:`manifest.json` 登记景数 == 请求的 `scenes`;日期集覆盖 `dates` 区间无空窗;全部景同轨道同 frame——混轨数据到第 3 步才会暴露,代价高得多。
- HyP3 路线附加检查:可选产物 `unw` 在位(它同时是第 7 步时序反演的输入);`era5`(`mintpy/inputs/ERA5.h5`,Ridgecrest 实测约 47.9 MB)命中则第 8 步 `tropo_era5_pyaps` 免 CDS 凭据——导入时丢了它,第 8 步会平白多一个凭据依赖。
- 尺寸合理性:S1 IW SLC 双极化单景未压缩 ≈7–8 GB、zip ≈4–4.5 GB(预算按 8 GB/景,2026-08-13 C4 修正);离群小文件按损坏处理,不要带病进下游。
- 本步无 quality_gate 硬门;把关靠上述产物核验,坏数据在这里拦住最便宜(重跑代价不对称:第 1 步分钟级,第 3–4 步小时级)。

## 参考文献

- Torres, R., et al. (2012). GMES Sentinel-1 mission. *Remote Sensing of Environment*, 120, 9–24. doi:10.1016/j.rse.2012.05.028
- Rosenqvist, A., Shimada, M., Ito, N., & Watanabe, M. (2007). ALOS PALSAR: A pathfinder mission for global-scale monitoring of the environment. *IEEE TGRS*, 45(11), 3307–3316. doi:10.1109/TGRS.2007.901027
- Ferretti, A., Prati, C., & Rocca, F. (2001). Permanent scatterers in SAR interferometry. *IEEE TGRS*, 39(1), 8–20. doi:10.1109/36.898661 —— PS 链景数门槛依据。
- Yunjun, Z., Fattahi, H., & Amelung, F. (2019). Small baseline InSAR time series analysis: Unwrapping error correction and noise reduction. *Computers & Geosciences*, 133, 104331. doi:10.1016/j.cageo.2019.104331 —— 网络冗余与景数关系。
- Li, Z., et al. (2019). InSAR analysis of surface deformation over permafrost on the Tibetan Plateau. *Remote Sensing*, 11(9), 1000 附近文献族 —— 冻土观测跨度 <14 个月时周期/趋势不可分(转引自本仓库 permafrost 场景包调研)。
- ASF HyP3 文档与 InSAR Product Guide:hyp3-docs.asf.alaska.edu —— 云端产品内容、配额与凭据要求。
- asf_search 官方文档:docs.asf.alaska.edu/asf_search/ —— 检索 API 行为。
- 仓库内:`src/insar_agent/registry/scenario_packs/{quake,permafrost,landslide,stripmap_coseismic}/SKILL.md`(各场景数据要求)、`docs/VALIDATION-isce2-wsl.md`(ALOS Baja 数据装配实测)。
