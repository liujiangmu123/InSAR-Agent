---
name: 03-coregistration
description: "当需要为流水线第 3 步配准选择方法(isce2_tops_geom_esd / isce2_stripmap_xcorr / snap_backgeocoding)、调 esd_coherence_threshold、配置 stripmap 的 IMG/LED/DEM 路径与 resample_flag、或诊断 burst 边界相位跳变、ESD 无有效样本、偏移场拟合发散、Segmentation fault 等配准类失败时使用。"
capability: 3
version: "1.0.0"
applies_to: all
---

# 配准(Coregistration)

能力速览(以 `registry/capabilities.py` id=3 为准):方法 `isce2_tops_geom_esd`(默认、推荐)/ `isce2_stripmap_xcorr`(仅 stripmap_coseismic 场景)/ `snap_backgeocoding`;science 参数 `esd_coherence_threshold`(默认 0.85)与 stripmap 专用路径组 `reference_image` / `reference_leader` / `secondary_image` / `secondary_leader` / `resample_flag`(枚举 ""、"dual2single")/ `dem_path`;resource 参数 `threads`(默认 8,1–32);输入 `slc` + `dem`;产物 `coreg`(RSLC,候选 `data/coreg`、`isce2/coregisteredSlc`);run_ok 含 `log_absent(ERROR|Segmentation fault)`;cpu=8 / mem 12 GB / io=heavy;total 超时 4 h。

## 适用判据

- **`isce2_tops_geom_esd`(默认、推荐)**:Sentinel-1 IW(TOPS,Terrain Observation by Progressive Scans)的标准且几乎唯一正确路径。TOPS 方位向天线扫描造成多普勒中心随方位快变,方位配准误差会转化为 burst 边界相位跳变——要求方位配准精度达千分之一像元量级(约 0.001 像元对应 burst 重叠区几度相位,Prats-Iraola et al. 2012),纯振幅互相关达不到,必须"几何配准(轨道+DEM)+ ESD(Enhanced Spectral Diversity,增强谱分集)精化"。
- **`isce2_stripmap_xcorr`(scenario_only=stripmap_coseismic)**:条带(stripmap)模式 ALOS raw 链,stripmapApp 的 startup→fine_resample 区间(含两景聚焦,实测约占阶段 1 的 20 min 大头,2026-08 WSL 全链实测通过)。振幅互相关(amplitude cross-correlation)偏移场 + 多项式拟合。条带模式无 burst 结构,不需要 ESD。
- **`snap_backgeocoding`**:SNAP 链的 back-geocoding,选它意味着换整条 SNAP 布局(layout),仅当 ISCE2 引擎不可用且下游接受 SNAP 布局时考虑,不与 ISCE2 链混搭。
- 场景差异:quake(HyP3)本步 `cloud_completed` 跳过;permafrost / landslide 的时序堆栈是"单参考景 + 全体从景"的堆栈配准,参考景选择(时相居中、相干性好、非融化峰值期)比阈值微调更影响成败;stripmap_coseismic 的 3–6 步作为整链分段续跑,分段区间固化在 `engines/isce2.py` 的 `_STRIPMAP_RANGES`,勿手改。

## 参数启发式

- **`esd_coherence_threshold`(默认 0.85;contract 台账 OK 级,来源=ISCE2 topsApp 上游默认)**:ESD 只用 burst 重叠区中相干性(coherence)高于该阈值的像元估计方位偏移。
  - 缺省 0.85 不动是常态;调它之前先排除轨道档次问题(第 2 步 `orbit` 必须是 `poeorb`)。
  - 往下调(0.70–0.75):整景低相干(冻土融化季、浓密植被、雪盖)导致重叠区有效样本不足、ESD 报无样本或估计方差大时。代价:纳入噪声像元,估计可能有偏,burst 边界残余跳变反而变大——下调后必须复查第 4 步干涉图 burst 边界。
  - 往上调(0.90+):高相干场景(荒漠同震,如 Ridgecrest 型)收紧样本提纯估计,通常收益边际,不是常规动作。
  - 同震特殊性:近断层大形变梯度会污染重叠区双差相位,但 ESD 是全景统计估计,少数受污染 burst 影响有限;若破裂带贯穿多数 burst(特大地震),考虑剔除受污染子带或接受几何配准精度。
- **stripmap 路径组(`reference_image` / `reference_leader` / `secondary_image` / `secondary_leader` / `dem_path`)**:全部是 science 输入、进指纹——它们决定"处理的是哪份数据"。IMG 与 LED 必须两两配套(同景的 CEOS 影像文件与头文件);`dem_path` 指 ISCE 格式 DEM(`dem.wgs84`)。换数据 = 改这些路径,4–6 步在同一 stripmapApp 配置上分段续跑。
- **`resample_flag`(枚举 ""、"dual2single")**:ALOS FBS(28 MHz)/FBD(14 MHz)混模式配对时的距离向重采样开关。FBD 从影像配 FBS 主影像 → 必须 `"dual2single"`;同模式配对(FBS/FBS、FBD/FBD)→ 留空,XML 不渲染该属性(与本仓库实测 XML 形态一致)。设错的症状:干涉图全噪或距离向尺度失配。
- **`threads`(resource,默认 8,不进指纹)**:本机 24 核留 4 核给系统的上限纪律;内存预算 12 GB 吃紧时先降 threads 而不是换方法;io=heavy,吞吐瓶颈常在磁盘而非 CPU,threads 加倍不等于耗时减半。

## 常见失败与处置

1. **症状**:第 4 步干涉图出现沿方位向等间隔(S1 burst 间距,约 20 km/一 burst)的相位条带或锯齿跳变。**根因**:ESD 方位配准残差——重叠区相干不足、阈值不匹配、或轨道档次低。**处置**:先确认第 2 步 `orbit=poeorb`;再把 `esd_coherence_threshold` 降至 0.75 重跑;时序堆栈则换相干性更好的参考景。
2. **症状**:ESD 阶段报无有效样本 / 估计为 NaN。**根因**:全部重叠区像元低于阈值(整景失相干:雪盖、融化季、水面占比高)。**处置**:阈值降至 0.70;冻土场景避免用融化峰值期的景做参考;若仍失败,该对本身不适合进网络,回第 4 步网络设计剔除。
3. **症状**:stripmap 偏移场离散点多、多项式拟合 RMS > 0.1 像元。**根因**:IMG/LED 路径配对错乱、FBD/FBS 混模式未设 `resample_flag=dual2single`、或近场大形变污染互相关。**处置**:逐一核对四个路径参数两两配套;设 `resample_flag`;同震近场污染属预期,靠几何初值与剔野兜住——不要为迁就近场提高拟合阶次(会把形变吸进配准多项式)。
4. **症状**:日志出现 "Segmentation fault"(被 run_ok 的 `log_absent` 判定拦截)。**根因**:SLC 损坏、DEM 未完整覆盖 footprint、或内存不足。**处置**:回查第 1 步(损坏景)与第 2 步(覆盖+余量)QA;降 `threads`;不要简单重跑赌运气——段错误几乎总是输入问题。
5. **症状**:`coreg` 产物两个候选路径(`data/coreg`、`isce2/coregisteredSlc`)均未命中,contract_broken。**根因**:stripmap 分段中途死(pickle 链断)或 run 脚本工作目录不对(stripmap 链要求 cd isce2 后执行)。**处置**:stripmap 从本段段首(startup)整段重跑,让 pickle 链完整;查步骤日志确认实际落盘目录再对照候选列表。
6. **症状**:耗时逼近 total 超时(4 h)但仍有输出。**根因**:大堆栈全从景配准 + io=heavy 排队。**处置**:确认系统单并发纪律未被绕过;内存允许时 `threads` 升到 16;若是"无输出卡死"(idle 1800 s 先触发),按卡死点定位而不是提超时配额。

## QA 依据

- run_ok 三重判定(与 registry 声明一致):`exit_code == 0`、`coreg` 产物命中、日志无 `ERROR|Segmentation fault`。
- 方位配准精度(TOPS):ESD 迭代日志的最终方位偏移修正量应收敛到 0.001 像元量级;不收敛或来回震荡即判不合格,回参数启发式处置。
- 目视核验:重采样从景与参考景振幅叠加无重影(ghosting)、无边缘错位;快视(quick-look)干涉图 burst 边界连续——这是配准质量最灵敏的探针,比任何数值指标先暴露问题。
- stripmap 专项:剔野后偏移场拟合 RMS < 0.1 像元;`coregisteredSlc/` 下从景尺寸与参考景网格一致。
- 失败样本的价值:配准不合格却放行,第 4–6 步全部产物作废且原因难溯——本步 QA 是全链重跑代价最高的一道闸,宁严勿松。

## 参考文献

- Prats-Iraola, P., Scheiber, R., Marotti, L., Wollstadt, S., & Reigber, A. (2012). TOPS interferometry with TerraSAR-X. *IEEE TGRS*, 50(8), 3179–3188. doi:10.1109/TGRS.2011.2178247 —— ESD 原理与方位配准精度要求。
- Yagüe-Martínez, N., Prats-Iraola, P., et al. (2016). Interferometric processing of Sentinel-1 TOPS data. *IEEE TGRS*, 54(4), 2220–2234. doi:10.1109/TGRS.2015.2497902 —— S1 TOPS 干涉标准流程(几何配准+ESD)。
- De Zan, F., & Monti Guarnieri, A. (2006). TOPSAR: Terrain observation by progressive scans. *IEEE TGRS*, 44(9), 2352–2360. doi:10.1109/TGRS.2006.873853 —— TOPS 成像模式本源。
- Sansosti, E., Berardino, P., Manunta, M., Serafino, F., & Fornaro, G. (2006). Geometrical SAR image registration. *IEEE TGRS*, 44(10), 2861–2870. doi:10.1109/TGRS.2006.875787 —— 几何配准方法依据。
- Rosen, P. A., Gurrola, E., Sacco, G. F., & Zebker, H. (2012). The InSAR scientific computing environment. *EUSAR 2012 Proceedings* —— ISCE2 框架。
- ISCE2 源码:github.com/isce-framework/isce2(`applications/topsApp.py` 的 `ESD_COHERENCE_THRESHOLD` 默认 0.85 —— contract 台账 ref;`applications/stripmapApp.py` 分步区间)。
- 仓库内:`docs/VALIDATION-isce2-wsl.md`(ALOS Baja 条带链实测:分段区间、耗时、resample_flag 形态)、`src/insar_agent/audit/contract.yaml`(esd_coherence_threshold 条目)。
