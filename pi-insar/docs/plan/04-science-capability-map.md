# 04 · InSAR 全流程科学能力地图(上游 → 处理 → 下游 → 交付)

> 目的:把"一个完整的 InSAR 数据处理 Agent 到底要会做什么"摊开成清单,逐项标注**本机是否真实可用**、**注册表是否已声明**、**LLM 是否够得着**、**在哪个 Phase 补齐**。
> 全部"本机可用"结论均来自对 `E:\miniforge3\envs\insar` 的实测清点(2026-08-15),不是文献推测。

## 0. 结论先行

本机 MintPy 环境**已经具备**做完整 InSAR 科学工作流所需的绝大部分下游能力(升降轨分解、剖面、电离层校正、板块运动、解缠误差改正、KMZ/GeoTIFF/QGIS/GBIS/Kite 导出、网络图、相干矩阵、时序图……共 70+ 个 CLI)。

真正的缺口不在"引擎没有",而在两处接线:
1. **注册表只声明了其中一小撮**(11 步 × 少量方法),大量真实可用的引擎能力没有进入可复现执行体系;
2. **扩展只暴露了 16 个工具**,后端已有的分析/出图/导出/报告端点对 LLM 完全不可见。

## 1. 上游:数据与辅助资料

| 环节 | 内容 | 本机/后端现状 | 补齐 Phase |
|---|---|---|---|
| SAR 数据源 | Sentinel-1 SLC(ASF/CDSE)、ALOS-1/2 PALSAR、TerraSAR-X、COSMO-SkyMed | 注册表方法 `asf_search_slc`(引擎 `asf_api`)、`local_import`;`local_import` 有真实实现(`engines/localdata.py`) | 现状可用 |
| 云端已处理产品 | ASF HyP3 InSAR、ARIA GUNW、LiCSAR | `hyp3_submit` 方法 + `prep_hyp3.py`;场景包 `cloud_completed: [2,3,4,5,6]` 跳过本地 2-6 步 | 现状可用(**realtest 走的就是这条**) |
| 多处理器接入 | ISCE / ARIA / GAMMA / GMTSAR / SNAP / ROI_PAC / FRINGE / NISAR / COSI-Corr | 本机具备 `prep_isce.py`、`prep_aria.py`、`prep_gamma.py`、`prep_gmtsar.py`、`prep_snap.py`、`prep_roipac.py`、`prep_fringe.py`、`prep_nisar.py`、`prep_cosicorr.py` —— **注册表一个都没声明** | Phase 15(场景包)按需声明 |
| DEM | Copernicus GLO-30 / SRTM / 本地 | 注册表第 2 步三方法齐 | 现状可用 |
| 精密轨道 | POEORB / RESORB | 第 2 步 `orbit` 参数 | 现状可用 |
| 气象再分析(对流层) | ERA5(CDS)、GACOS、高程相关经验模型 | 第 8 步三方法齐;本机 `tropo_pyaps3.py`、`tropo_gacos.py`、`tropo_phase_elevation.py`、`tropo_opera.py` | 现状可用 |
| 电离层 | split-spectrum、TEC(IGS GIM) | 本机 `iono_split_spectrum.py`、`iono_tec.py` —— **注册表未声明** | **Phase 11** |
| 外部对照数据 | GNSS、水准、地震目录 | 无 | 不做(超出范围,见 §6) |

## 2. 处理链:已有的 11 步

| 步 | 名称 | 已声明方法 | 本机可扩充但未声明 |
|---|---|---|---|
| 1 | 数据获取 | asf_search_slc / hyp3_submit / local_import | 9 个 `prep_*.py` 多处理器接入 |
| 2 | 辅助数据 | dem_copernicus / dem_srtm / dem_local | — |
| 3 | 配准 | isce2_tops_geom_esd / isce2_stripmap_xcorr / snap_backgeocoding | — |
| 4 | 干涉 | isce2_ifg_multilook / snap_interferogram / isce2_stripmap_ifg | — |
| 5 | 滤波 | goldstein / boxcar / none / isce2_stripmap_filter | `spatial_filter.py` |
| 6 | 解缠 | snaphu_mcf / snaphu_smooth / icu / 3D_FULL / isce2_stripmap_unwrap_snaphu | **解缠误差改正** `unwrap_error_bridging.py`、`unwrap_error_phase_closure.py` |
| 7 | 时序反演 | mintpy_sbas / pystamps_ps | `ifgram_inversion.py` 细粒度、`modify_network.py`、`closure_phase_bias.py`(fading 偏差) |
| 8 | 误差校正 | tropo_era5_pyaps / tropo_gacos / tropo_height_corr | **电离层**、**板块运动** `plate_motion.py`、`s1ab_range_bias.py`、`local_oscilator_drift.py`、`solid_earth_tides.py`(已有参数开关) |
| 9 | 形变模型 | linear / step / exponential / poly_periodic | `timeseries2velocity.py` 的更多时间函数、`temporal_derivative.py` |
| 10 | 出图导出 | figure_journal / mintpy_geocode / gdal_warp | **`view.py`、`plot_network.py`、`plot_coherence_matrix.py`、`plot_transection.py`、`tsview.py`、`save_kmz*.py`、`save_gdal.py`、`save_qgis.py`、`save_gmt.py`、`save_hdfeos5.py`** |
| 11 | 质检 | crossval_ps_sbas / loop_closure / coherence_mask | `timeseries_rms.py`、`temporal_average.py` |

## 3. 下游:分析、解译、预测(**当前最大缺口区**)

| 类别 | 具体能力 | 本机工具(实测存在) | 现状 | 补齐 Phase |
|---|---|---|---|---|
| **几何后处理** | 参考点重置 | `reference_point.py` | 未声明 | Phase 10 |
| | 参考日期重置 | `reference_date.py` | 未声明 | Phase 10 |
| | 掩膜生成/应用 | `generate_mask.py`、`mask.py` | 未声明 | Phase 10 |
| | 空间子集 | `subset.py` | 仅 cfg 里 `subset.lalo` | Phase 10 |
| | 地理编码 | `geocode.py` | 第 10 步方法 | 现状可用 |
| | 影像拼接(多轨/多帧) | `image_stitch.py` | 未声明 | Phase 10 |
| | 栅格代数 | `add.py`、`diff.py`、`image_math.py` | 未声明 | Phase 10 |
| **多几何融合** | 升降轨分解 → 垂直 + 东西向 | `asc_desc2horz_vert.py` | **未声明(跨 run 分析)** | **Phase 10(分析 run)** |
| **定量分析** | 点位时间序列 | 后端 `/api/timeseries-point` 已实现 | **LLM 够不着** | **Phase 06** |
| | 剖面(单条/多条) | `plot_transection.py`、`multi_transect.py` | 未声明 | Phase 10 / 12 |
| | 空间统计(区域均值) | `spatial_average.py` | 未声明 | Phase 10 |
| | 时间统计(时段平均) | `temporal_average.py` | 未声明 | Phase 10 |
| | 残差 RMS / 噪声评估 | `timeseries_rms.py` | 未声明 | Phase 11 |
| **变化与异常** | 加速/突变识别 | 无现成 CLI(可由时间函数残差 + 分段拟合导出) | 缺 | **Phase 13** |
| | 相干性变化检测 | 可由 `diff.py` + 相干产物组合 | 缺 | Phase 13(可选) |
| **预测** | 形变趋势外推 + 不确定度 | 无现成 CLI(基于第 9 步拟合模型外推) | 缺 | **Phase 13** |
| **源反演** | 断层滑动/形变源反演 | **不自造**:本机有 `save_gbis.py`(→ GBIS 贝叶斯反演)、`save_kite.py`(→ Kite/pyrocko 降采样)、`save_gmt.py` | 缺桥 | **Phase 14(只做导出桥)** |

## 4. 出图体系(交付的脸面)

| 图种 | 用途 | 本机工具 | 现状 | 补齐 Phase |
|---|---|---|---|---|
| 速度场图 | 主结论图 | `engines/figures.py`(自建,600 dpi + 色盲安全 + 三档尺寸 + sidecar) | ✅ 已有且质量高 | — |
| 通用栅格图 | 任意 h5 数据集出图 | `view.py` | 未声明 | Phase 12 |
| 时间序列曲线 | 点位形变过程 | `tsview.py` | 未声明 | Phase 12 |
| 剖面图 | 跨断层/跨沉降漏斗 | `plot_transection.py` | 未声明 | Phase 12 |
| 基线网络图 | 数据质量与网络拓扑 | `plot_network.py` | 未声明 | Phase 12 |
| 相干矩阵图 | 相干性结构诊断 | `plot_coherence_matrix.py` | 未声明 | Phase 12 |
| 干涉图/相干图 | 中间过程质检 | `view.py` 对 ifgramStack | 未声明(出图只在第 10 步) | Phase 12 |
| KMZ / Google Earth | 汇报与共享 | `save_kmz.py`、`save_kmz_timeseries.py` | 未声明 | Phase 12 |
| 多面板成图 | 论文 figure | 自建组合 | 缺 | Phase 12 |
| **AI 识图质检** | 让模型看图判断质量 | 后端 `/api/vision-qa` 已实现 | **LLM 够不着** | **Phase 07** |

## 5. 数据与文档交付

| 交付物 | 后端现状 | LLM 可及? | 补齐 Phase |
|---|---|---|---|
| HDF5 / CSV / GeoTIFF / KMZ / Shapefile 导出 | `/api/export` + `report/export.py` 已实现 | ❌ | **Phase 07** |
| QGIS / GMT / HDF-EOS5 / GBIS / Kite 导出 | 本机 CLI 有,后端未接 | ❌ | Phase 12 / 14 |
| 方法章节草稿(双向数值校验、防幻觉) | `/api/report/draft` + `report/draft.py` | ❌ | **Phase 08** |
| 结果章节草稿 | `/api/report/results` | ❌ | **Phase 08** |
| 双语论文图注 | `/api/report/caption` | ❌ | **Phase 08** |
| 完整报告(report_full.md) | `/api/report/full` | ❌ | **Phase 08** |
| provenance 账本导出 | `/api/provenance` | ✅ `insar_export_provenance` | — |
| 等价裸命令脚本 run.sh | `/api/run.sh` | ❌ | Phase 05 |
| 复现包 zip(账本+脚本+方法+图件+MANIFEST) | `/api/repro-bundle` | ❌ | **Phase 08** |
| 诊断包 zip(排障用) | `/api/diagnostics` | ❌ | Phase 06 |
| 下一步建议卡 | `/api/advise` | ❌ | Phase 08 |

## 6. 明确不做(边界声明,防止无限扩张)

- **不自造反演算法**(Okada/贝叶斯滑动分布):这是独立学科软件的领域,本项目只做**标准导出桥**(GBIS/Kite/GMT),把降采样与格式转换做对,反演交给专业软件。理由:自造反演既无法验证也无法与文献对齐,违背"证据可溯源"的立身之本。
- **不接 GNSS/水准等外部观测融合**:需要外部数据契约与坐标框架处理,超出"InSAR 数据处理 Agent"的边界;需要时以导出桥形式对接。
- **不做黑箱机器学习预测**:预测必须是**第 9 步已拟合的时间函数的外推 + 不确定度**,并强制标注外推有效期与置信区间(纪律见 Phase 13);不允许出现无法溯源、无法证伪的预测数字。
- **不复活 prototype Web UI**:pi TUI 是唯一主界面。
- **不做远程会话 / pi-chat / MCP**:见 `03-design-analysis.md` §1.4。

## 7. 能力补齐总路线

```
已有(不动)        11 步主链 + provenance + 证据阶梯 + 质量门 + 报告体系
        │
Phase 05-09  ── 工具面补全:把后端已有的分析/出图/导出/报告端点全部暴露给 LLM(便宜、高收益)
        │
Phase 10     ── 后处理能力:掩膜/子集/统计/栅格代数/参考点 + 升降轨分解(分析 run 机制)
Phase 11     ── 校正链补全:电离层、解缠误差、板块运动、闭合相位偏差(扩 cfg 模板 + 加方法)
Phase 12     ── 出图体系:网络图/相干矩阵/时序图/剖面/KMZ/中间过程图/多面板
Phase 13     ── 分析与预测:变化检测、加速识别、趋势外推 + 不确定度纪律
Phase 14     ── 反演桥:GBIS / Kite / GMT / QGIS 标准导出
Phase 15     ── 场景包:沉降/火山/滑坡/冻土/矿区 + 端到端剧本
        │
Phase 16     ── 文档收口 + 全量最终验收
```
