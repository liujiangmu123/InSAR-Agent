# ISCE3 迁移路线图

- **调研日期**:2026-08-12(网络调研 + 本仓库 `src/insar_agent/engines/isce2.py`、`src/insar_agent/registry/capabilities.py` 声明形态分析)
- **背景**:竞品调研(`reference/RESEARCH-competitors-2026-08-12.md` §1.8/§3.4)确认 **ISCE2 已被 JPL 官方停维**([官方讨论区 2026-07 原话](https://github.com/isce-framework/isce2/discussions/28133):"ISCE2 is no longer systematically updated by JPL… their focus now is NISAR and ISCE3"),行业主力迁向 ISCE3/OPERA 生态(COMPASS / dolphin / sweets)。
- **一句话结论**:**双引擎共存、按 capability 渐进迁移**——S1 TOPS 链(cap3-6)在 6-12 个月内新增 `isce3/dolphin 方法族`并逐步默认化;**条带链(ALOS,cap3-6 的 stripmap 场景包)留在 ISCE2**(ISCE3 无对应物);MintPy(cap7-9)保持不动,桥是最大不确定项;ISCE2 作为回退与条带专用引擎保守可再用 2-3 年。

---

## 0. TL;DR

| 问题 | 答案 |
|---|---|
| ISCE3 能装吗 | conda-forge `isce3` 0.25.12(2026-05 更新),**仅 linux-64/macOS,无 Windows**([issue #258](https://github.com/isce-framework/isce3/issues/258))——恰好匹配我们的 WSL 后端 |
| ISCE3 是 ISCE2 升级版吗 | **不是**。C++/pybind11 从零重写的**库**;没有 topsApp/stripmapApp,没有 XML,没有 pickle 断点。工作流层 = NISAR 产品导向的 `nisar.workflows` + **YAML runconfig**;S1 支持完全外包给 OPERA 系(COMPASS 等) |
| 我们的 cap3-6 对应什么 | cap3=COMPASS(CSLC 几何配准),cap4+5+6=dolphin(相位链接→Goldstein/插值→可插拔解缠),snaphu 以 snaphu-py 形式仍是默认解缠器 |
| 条带链(刚做的 stripmapApp)怎么办 | **留在 ISCE2**。ISCE3 只有示例级 ALOS→NISAR 格式转换脚本,无 CEOS 条带工作流;这是 ISCE2 长期保留的核心理由 |
| MintPy 还能接上吗 | 能但有坑:官方 `prep_dolphin` 仍在[讨论中](https://github.com/insarlab/MintPy/discussions/1016)未落地;现有路径是社区工具/自写 prep,雷达/地理坐标元数据判定是主要坑 |
| ISCE2 还能用多久 | 研究用途保守 **2-3 年**(社区/ASF 仍在发版:[v2.6.5,2026-06-30](https://github.com/isce-framework/isce2/releases/tag/v2.6.5) 支持 S1C/S1D);但 numpy<2 钉死、无 NISAR 支持,长期生态漂移不可逆 |

---

## 1. 现状综述

### 1.1 ISCE3 本体(2025-2026)

- **定位**:NISAR 任务的官方处理框架([文档](https://isce-framework.github.io/isce3/)、[GitHub](https://github.com/isce-framework/isce3));C++/CUDA 核心 + pybind11 Python 绑定,自我定义是**通用 SAR 处理库**而非应用程序集([AGU 2023 综述](https://ui.adsabs.harvard.edu/abs/2023AGUFM.G23C0488F/abstract))。
- **版本与节奏**:conda-forge 当前 **0.25.12**(linux-64 构建 2026-07,包页 2026-05 更新);无传统 GitHub release 节奏,版本跟随 NISAR SAS 交付,develop 分支 2026 年持续高频提交。README 仍挂着 "**early development – features and interface are subject to change**" 警告,但它已实际支撑 NISAR 正式产品生产(见 §1.6)。
- **可装性**:
  - conda-forge 三个包:`isce3`(元包)、`isce3-cpu`、`isce3-cuda`(CUDA 12.9 构建,py3.12/3.13);
  - 平台:**linux-64、osx-64、osx-arm64;无 win-64**([issue #258](https://github.com/isce-framework/isce3/issues/258),2026-04,ASF 工程师提出,尚未解决)——对本项目无碍,我们的引擎全在 WSL(linux-64)里;
  - GPU:`isce3-cuda` 在 WSL2 + NVIDIA 驱动下理论可用,属可选优化,不进主线。
- **文档成熟度**:安装/构建文档完善;API 文档(C++ doxygen + Python sphinx)齐;**面向最终用户的教程几乎没有**——S1 用户文档全部在下游仓库(COMPASS/dolphin/sweets 的 README 与 readthedocs);NISAR 工作流靠仓库内 runconfig schema(`share/nisar/schemas/*.yaml`)与 defaults 自解释。总体判断:**库级文档成熟,应用级文档外包给 OPERA 生态**。
- **与 ISCE2 的 API 关系**:**不是升级,是范式更换**。
  - ISCE2 是"应用程序集"(topsApp/stripmapApp/topsStack…,XML 配置,`--start/--end` 步进,pickle 链断点——我们 `engines/isce2.py` 封装的正是这套);
  - ISCE3 是"库 + NISAR 工作流":`python -m nisar.workflows.focus|gslc|gcov|insar <runconfig.yaml>`,输入输出是 NISAR 规范 HDF5 产品(RSLC/GSLC/GCOV/RIFG/RUNW/GUNW/ROFF/GOFF);
  - **ISCE3 本体不读 Sentinel-1 SAFE、不读 CEOS**。S1 的 SAFE→burst 解析在 [s1-reader](https://github.com/opera-adt/s1-reader)(conda-forge `s1reader`,README 自标 "pre-alpha"),S1 工作流在 COMPASS/RTC/dolphin 等 opera-adt/isce-framework 仓库;
  - 对我们意义:**"迁移到 ISCE3"实际是"迁移到 OPERA 工具链"**,isce3 本体只是它们的底层依赖。

### 1.2 OPERA 工具链分工(与我们 cap3-6 的对应)

新链条(S1 IW):`SAFE + 轨道 + DEM →(COMPASS)→ 地理编码 burst CSLC 栈 →(dolphin)→ 相位链接 + 干涉网络 + 滤波 + 解缠 + 时序`。

| 工具 | 职责 | 形态 | 对应我们的 cap |
|---|---|---|---|
| [COMPASS](https://github.com/opera-adt/COMPASS) | SAFE→**地理编码 CSLC**(OPERA CSLC-S1 算法,5×10 m UTM,burst 级) | `s1_cslc.py --grid geo <runconfig.yaml>`,每日期一个 YAML;conda-forge `compass` 0.5.6 | **cap3 配准**(+吃掉 cap2 的部分几何工作) |
| [s1-reader](https://github.com/opera-adt/s1-reader) | SAFE→isce3 burst 对象(COMPASS 依赖) | Python 库,conda-forge `s1reader` | (cap3 内部) |
| [burst_db](https://github.com/opera-adt/burst_db) | S1 burst/frame 数据库(burst 边界、UTM EPSG) | `opera-db` CLI + SQLite | cap1/cap3 辅助 |
| [dolphin](https://github.com/isce-framework/dolphin) | CSLC 栈→PS/DS 相位链接→干涉网络→(Goldstein/插值)→解缠→L1 时序反演+速度([JOSS 2024](https://doi.org/10.21105/joss.06997)) | `dolphin config` + `dolphin run <yaml>`;另有独立子命令 `dolphin unwrap`、`dolphin timeseries`;conda-forge 0.42.5(2026-03) | **cap4 干涉 + cap5 滤波 + cap6 解缠**(可选覆盖 cap7/9) |
| [snaphu-py](https://github.com/isce-framework/snaphu-py) | SNAPHU 的 Python 封装(内置分块/并行) | conda-forge/PyPI `snaphu`;dolphin 默认解缠器 | **cap6**(snaphu 在新链里的位置:不再独立驱动,而是被 dolphin 以库形式调度) |
| [spurt](https://github.com/isce-framework/spurt) / [tophu](https://github.com/isce-framework/tophu) / whirlwind + isce3 内置 PHASS/ICU | 3D 时空解缠 / 多尺度分块解缠 / 新网络流解缠 | dolphin `unwrap_method: snaphu\|icu\|phass\|spurt\|whirlwind`(+tophu 配置段) | **cap6 可插拔方法池**(正是竞品报告借鉴项 #4) |
| [sweets](https://github.com/isce-framework/sweets) | AOI+日期+轨道 → 端到端(下载 burst→COMPASS→dolphin→速度图);v0.3.1(2026-04),2026-06 新增独立干涉图工作流 + React Web UI([PR #152](https://github.com/isce-framework/sweets/pull/152)) | `sweets config` + `sweets run`;**conda-forge 包滞后(还在 pre-0.2 API),0.3.x 依赖多个 fork 未合上游** | 对标我们 cap1-9 的**演示级整合**——是参照物/轻竞品,不建议当引擎封装 |
| [disp-s1](https://github.com/opera-adt/disp-s1) | OPERA DISP-S1 生产 SAS(dolphin+tophu 之上加产品打包) | 生产系统,研究者一般不直接用 | (cap4-7 的云端产品化对照,类比 HyP3) |
| [opera-utils](https://github.com/opera-adt/opera-utils) / [burst2safe](https://github.com/ASFHyP3/burst2safe) | OPERA CSLC/DISP 产品发现下载、burst→SAFE 重组 | conda-forge 均有 | **cap1 数据获取** |

关键架构差异(影响 registry 声明):

1. **配准方式变了**:COMPASS 是纯几何配准 + 模型化校正 LUT(双站方位延迟、方位 FM-rate 失配、固体潮、电离层 TEC、静态对流层——[CSLC-S1 ATBD §2.2.4](https://cumulus.asf.earthdatacloud.nasa.gov/PUBLIC/DATA/OPERA/OPERA_CSLC-S1_ATBD_D-108752_Initial_2024-06-24_signed.pdf)),**没有数据驱动的 ESD**;我们 cap3 的 `esd_coherence_threshold` 参数在新方法族里不存在,取而代之的是 `x/y_posting`、校正开关等。
2. **产物坐标系变了**:CSLC 与 dolphin 全链产物是**地理编码 GeoTIFF/HDF5(UTM)**,不再是 ISCE2 的雷达坐标 + 最后 geocode;我们 `ArtifactSpec.layout` 需要新增 `cslc_geo` 类布局,cap10 出图反而更省(GeoTIFF 原生)。
3. **部分大气校正前移**:CSLC 已内嵌固体潮/静态对流层/TEC 校正 LUT——与 cap8(MintPy ERA5/SET)存在**重复校正风险**,迁移时必须在 registry 参数上显式声明哪层做了什么(诚实接口纪律)。
4. **步骤边界重划**:dolphin 一个 YAML 覆盖我们 cap4-6(甚至 7/9),但它的 CLI 有独立子命令(`dolphin run --no-unwrap` 语义的配置开关 `run_unwrap: false`、独立 `dolphin unwrap`、`dolphin timeseries`),**可以拆回我们的三步分段模型**(见 §3 阶段 2)。

### 1.3 Sentinel-1 TOPS 在 ISCE3 生态的完整路径(SLC→解缠干涉图)

三条路径,按"自己算多少"排序:

**路径 A(北美/OPERA 覆盖区最短路径)**:直接下载现成 OPERA CSLC-S1(ASF,[产品页](https://www.earthdata.nasa.gov/data/catalog/asf-opera-l2-cslc-s1-v1-1)),跳过 COMPASS:

```bash
# 数据发现/下载:asf_search 或 opera-utils(cap1 语义)
dolphin config --slc-files 'cslcs/*.h5' --subdataset /data/VV --work-dir dolphin_work
dolphin run dolphin_work/dolphin_config.yaml
```

**路径 B(全球通用,自算 CSLC)**——最小示例(两景一对,WSL 内,以 2026-08 各仓库 README/defaults 为准):

```bash
# ① 辅助数据(cap2 语义)
eof --search-path ./slcs --save-dir ./orbits     # sentineleof:S1 精轨 EOF
sardem --bbox <W> <S> <E> <N> -o dem.tif         # Copernicus 30m DEM(GeoTIFF 即可)
# 可选:opera-db create  → burst_db.sqlite3(限定 burst 边界/EPSG 用)

# ② COMPASS:每个日期渲染一份 runconfig,逐日期出地理编码 CSLC(cap3 语义)
s1_cslc.py --grid geo runconfig_20230101.yaml
s1_cslc.py --grid geo runconfig_20230113.yaml
#   栈处理:compass.s1_geocode_stack 可为整个 SLC 目录批量生成 runconfigs
#   (sweets 内部即调用它,见 sweets/_geocode_slcs.py 的 create_config_files)

# ③ dolphin:干涉 + 滤波 + 解缠(+ 时序)(cap4-6 语义)
dolphin config --slc-files 'gslcs/*/*.h5' --subdataset /data/VV --work-dir dolphin_work
dolphin run dolphin_work/dolphin_config.yaml
```

COMPASS runconfig 最小形态(结构照抄 [s1_cslc_geo.yaml 默认文件](https://github.com/opera-adt/COMPASS/blob/main/src/compass/defaults/s1_cslc_geo.yaml),本地输出的 HDF5 数据集路径以 `h5ls` 实测为准):

```yaml
runconfig:
  name: cslc_s1_workflow_default
  groups:
    input_file_group:
      safe_file_path: [./slcs/S1A_IW_SLC__1SDV_20230101T....zip]
      orbit_file_path: [./orbits/S1A_OPER_AUX_POEORB_....EOF]
      burst_id:                      # 留空 = 处理 SAFE 内全部 burst;可填 [t078_206576_iw2]
    dynamic_ancillary_file_group:
      dem_file: ./dem.tif
      tec_file:                      # 可选:IONEX TEC(电离层校正)
    static_ancillary_file_group:
      burst_database_file:           # 可选:burst_db.sqlite3
    product_path_group:
      product_path: ./gslcs
      scratch_path: ./scratch
      sas_output_file: ./gslcs
    processing:
      polarization: co-pol
      geocoding: {flatten: True, x_posting: 5, y_posting: 10}
```

dolphin 配置关键段(`dolphin config --print-empty` 全量样例见[官方 sample](https://github.com/isce-framework/dolphin/blob/main/docs/sample_dolphin_config.yaml);与我们 registry 参数的对应):

```yaml
cslc_file_list: [...]                 # 唯一必填
output_options: {strides: {x: 6, y: 3}}     # ≈ 我们的 range/azimuth_looks(多视)
phase_linking: {ministack_size: 15, half_window: {x: 14, y: 7}}
interferogram_network:
  max_bandwidth: 3                    # ≈ 我们的 pairs/network(近邻-N 网络)
  max_temporal_baseline:              # ≈ cap7 的 max_temporal_baseline
unwrap_options:
  run_unwrap: true                    # false = 只出缠绕干涉图(cap4/5 与 cap6 的分界开关)
  run_goldstein: false                # ≈ cap5 goldstein(alpha 在 preprocess_options.alpha)
  run_interpolation: false            # 低相干掩膜+插值(EZ 竞品报告未覆盖的新前处理)
  unwrap_method: snaphu               # snaphu | icu | phass | spurt | whirlwind(cap6 方法池)
  snaphu_options: {init_method: mcf, cost: smooth}   # ≈ 我们 cap6 的 cost_mode(SMOOTH/DEFO)
timeseries_options: {run_inversion: true, method: L1, run_velocity: true}  # 与 cap7/9 重叠区
worker_settings: {threads_per_worker: 1, n_parallel_bursts: 1, block_shape: [512, 512]}  # resource 参数
```

**路径 C(NISAR 数据)**:不用自己算干涉——NISAR L2 **GUNW(地理编码解缠干涉图)是官方标准产品**,ASF 直接下载([NISAR 数据指南](https://nisar-docs.asf.alaska.edu/availability-overview/)),MintPy `prep_nisar` 直接进 cap7(类比现在的 HyP3 路线,2-6 步云端完成)。自算路径(L0B→`nisar.workflows.focus`→RSLC→`nisar.workflows.insar`→RIFG/RUNW/GUNW,YAML runconfig)仅在需要非标参数时使用。

### 1.4 ALOS / 条带模式在 ISCE3 的支持现状

**结论:没有 stripmapApp 对应物,条带链必须留在 ISCE2。**

- ISCE3 的条带处理只存在于 NISAR 工作流:`focus.py`(L0B raw→RSLC 聚焦)+ `insar.py`(RSLC 对→干涉),输入输出都是 **NISAR 规范 HDF5**,不读 CEOS;
- ALOS-1 raw 只有**示例级**转换脚本 [`share/nisar/examples/alos_to_nisar_l0b.py`](https://github.com/isce-framework/isce3/tree/develop/share/nisar/examples)(把 ALOS L0 CEOS 打包成 NISAR L0B;2025-01 修过 numpy 2.x 兼容、2026 仍有小修——说明有人用,但它在 `examples/` 目录、无产品保证、无教程、无社区实践沉淀);ALOS-2 有同级的 `alos2_to_nisar_l1.py`;
- 对比我们刚完成的 stripmapApp 链(2026-08 WSL 实测,`docs/VALIDATION-isce2-wsl.md`):ISCE2 的 ALOS raw→SLC→干涉→解缠→地理编码全链成熟且我们已踩平 pickle 分段的坑;ISCE3 侧重走"转换器+NISAR 工作流"路线的迁移成本高、风险大、收益不明;
- 中长期看,条带需求的真正接替者更可能是 **NISAR L 波段官方产品**(RSLC/GUNW 直接下载,2026-07 起 PROVISIONAL 已可用)而不是"ISCE3 版 ALOS 处理"。
- **决策**:`isce2_stripmap_*` 方法族(cap3-6 的 `stripmap_coseismic` 场景包)长期保留在 ISCE2;这也决定了迁移策略必须是**双引擎共存**而非替换。

### 1.5 dolphin 输出 → MintPy 的桥:现状(迁移的最大不确定项)

我们 cap7-9(时序/校正/形变模型)押在 MintPy 上,dolphin 输出能否顺畅进 MintPy 直接决定迁移路径的完整性:

- **官方 `prep_dolphin` 尚未落地**:MintPy 维护者与 dolphin 作者在 [MintPy discussion #1016](https://github.com/insarlab/MintPy/discussions/1016) 中持续推进("working on getting a `prep_` script for dolphin and sweets"),卡在元数据语义对齐(dolphin 的 `X/Y_PIXEL_SIZE`、`X/YLOOKS` vs MintPy 必需的 `RANGE/AZIMUTH_PIXEL_SIZE`、`A/RLOOKS`);
- **真实用户在踩坑**:[dolphin discussion #677](https://github.com/isce-framework/dolphin/discussions/677)、[MintPy discussion #1460](https://github.com/insarlab/MintPy/discussions/1460) 记录了典型失败:MintPy 依据 `X/Y_FIRST/STEP` 元数据自动判定雷达/地理坐标,dolphin GeoTIFF 的元数据会让它误判,报出难懂的 `geometryGeo.h5 / geometryRadar.h5 not found`;
- **社区补丁工具已出现**:[Dolphin2MintPy](https://github.com/bcankara/Dolphin2MintPy)(第三方,写 ROI_PAC `.rsc` sidecar + 生成 mintpy 配置,提供 radar/geo 模式显式切换)——典型的"跨引擎桥各自为战"(竞品报告 §3.3-5 预言的空白,风险与机会并存);
- **专用旁路已成熟**:
  - OPERA 官方 [calval-DISP](https://github.com/OPERA-Cal-Val/calval-DISP) 的 `run2_prep_mintpy_opera.py` 把 **DISP-S1 产品**(.nc)转成 MintPy 兼容的 `timeseries.h5`/`velocity.h5`(拿现成产品时可用);
  - sweets 自带 `prep_mintpy.py`(研究级);
  - **NISAR GUNW→MintPy 是一等公民**:`prep_nisar` 自 v1.5.3 存在,2026-04 大幅强化([PR #1487](https://github.com/insarlab/MintPy/pull/1487),+1258 行,电离层/对流层/固体潮辅助栈全支持);
- **旁路 2:不走 MintPy**——dolphin 内置 `timeseries_options`(L1 网络反演 + 自动参考点 + 速度估计),可作为 cap7 的替代方法;但 cap8 的 ERA5 校正、cap9 的模型族(周期/阶跃/指数)仍是 MintPy 独有,完整链还是需要桥。

### 1.6 ISCE2 还能安全用多久(时间窗判断)

**维护现状**:

- JPL 主导的最后版本 2.6.3(2023);此后均为社区 bugfix:[v2.6.4](https://github.com/isce-framework/isce2/releases/tag/v2.6.4)(2025-05,S1C 支持)、[v2.6.5](https://github.com/isce-framework/isce2/releases/tag/v2.6.5)(**2026-06-30**,S1C 轨道重构修复 + **Sentinel-1D 支持**);
- 值得注意:2.6.4/2.6.5 的提交者都是 **ASF 工程师**——HyP3 的 burst InSAR 与 ARIA GUNW 产线跑在 ISCE2 上,ASF 的商业依赖是 ISCE2 近期最强的"续命"力量;
- conda-forge `isce2` 2.6.5 可装(linux-64/osx-64),我们 WSL 里是 2.6.3——**短期动作:升级到 2.6.5**(获得 S1C/S1D 支持,2025 年后新采集的 S1 数据必需)。

**已知不修的问题**(社区安装指南 [lijun99/isce2-install](https://github.com/lijun99/isce2-install),2026-04 更新):

- **NumPy ≥2.0 不兼容**(运行期问题,官方明确不系统性修,[讨论 #28133](https://github.com/isce-framework/isce2/discussions/28133));必须钉 `numpy<=1.26.4`;
- Python 建议 3.9-3.12(3.13 需补丁,3.14+ 未测);gcc ≤14;CUDA 11-12(13 不行);
- 无 NISAR 支持(永远不会有)。

**行业迁移信号**(判断窗口关闭速度):

- ASF 已开始动手:[hyp3-autorift v0.22.0](https://doi.org/10.5281/zenodo.15611848)(2025-06)"**All ISCE2 based workflows … converted to ISCE3 based workflows**"(用 compass/s1reader/burst2safe);[hyp3-isce3](https://github.com/ASFHyP3/hyp3-isce3) 插件已建(NISAR GUNW 工作流);
- NISAR 数据已成现实:2025-07-30 发射,2026-02 BETA、**2026-07-20 PROVISIONAL 产品公开**(2026-06-17 起采集),全记录 2026 底补齐([ASF 公告](https://asf.alaska.edu/notices/nisar-l-band-data-now-publicly-available/));NISAR 用户从第一天起就在 ISCE3 生态里;
- OPERA DISP-S1 validated 产品 + [DISP-NI](https://www.earthdata.nasa.gov/data/catalog/asf-opera-l3-disp-s1-v1-1)(NISAR 版)代表"标准需求产品化"持续推进。

**判断**:

| 时间 | ISCE2 状态预期 |
|---|---|
| 现在 ~ 2027 | 安全。conda-forge 可装、ASF 商业依赖保底、S1A/C/D 数据可处理;topsStack 仍是学界主力 |
| 2027 ~ 2029 | 可用但环境成本上升:numpy<2 钉死使它与新版 MintPy/GDAL/Python 同环境共存越来越难(**必须独立 conda env**,我们已是);HyP3 若完成 burst InSAR 迁移,ASF 投入减弱 |
| 2029+ | 仅存量维护;新传感器(含 S1 后继)支持无保障;条带链继续能跑(冻结软件处理冻结格式的老数据,风险低) |

**给我们的时间窗**:在 **12 个月内完成 TOPS 链的 isce3 方法族共存**即可从容;条带链无迁移压力。

---

## 2. 工具对照表(cap × ISCE2 现用 × ISCE3 对应物 × 迁移成本)

方法 id 以 `registry/capabilities.py` 现声明为基准;成本为**人日估算**(含 registry 声明 + engines 构建器 + 测试 + WSL 实测,不含大规模真实数据验证)。

| cap | 现用(ISCE2 链) | ISCE3 生态对应物 | 配置形态 | 迁移成本(人日) | 优先级 |
|---|---|---|---|---|---|
| 1 数据获取 | `asf_search_slc` / `hyp3_submit` / `local_import` | + `opera_cslc_import`(现成 CSLC,opera-utils/asf_search);burst2safe(burst 级下载) | CLI | 2-3(可选) | P2 |
| 2 辅助数据 | `dem_copernicus`(ISCE 格式 DEM)+ poeorb | sentineleof(轨道)、sardem/dem-stitcher(**GeoTIFF DEM,格式不同**)、burst_db、IONEX TEC(可选) | CLI | 2-3 | P1 |
| 3 配准 | `isce2_tops_geom_esd`(topsApp startup→fineresamp,ESD) | **COMPASS** `s1_cslc.py --grid geo`(几何配准+校正 LUT,无 ESD;每日期一 runconfig,`s1_geocode_stack` 批量生成) | YAML runconfig × N 日期 | 4-6 | **P1** |
| 4 干涉 | `isce2_ifg_multilook`(topsApp ion→filter) | **dolphin** phase linking(EMI/EVD/CPL)+ `interferogram_network`(近邻-N/时间基线);多视=`strides` | dolphin_config.yaml(`run_unwrap: false` 出缠绕干涉图) | 3-5 | **P1** |
| 5 滤波 | `goldstein` / `boxcar`(topsApp filter) | dolphin `run_goldstein`(alpha)+ `run_interpolation`(低相干插值)——**变成解缠前处理配置段,不再是独立执行步** | 同一 YAML 配置段 | 1-2(并入 cap6 声明) | P1 |
| 6 解缠 | `snaphu_mcf` / `snaphu_smooth` / `icu` | dolphin `unwrap_method: snaphu(snaphu-py)/icu/phass/spurt/whirlwind` + tophu;`init_method: mcf/mst`、`cost: smooth/defo` 与现参数一一对应;独立子命令 `dolphin unwrap` 支撑分步 | 同一 YAML / CLI 子命令 | 2-3 | **P0**(可先行:对现有 ISCE2 产物也能用) |
| 7 时序反演 | `mintpy_sbas` / `pystamps_ps` | 甲:**桥→MintPy**(官方 prep 未落地,§1.5);乙:`dolphin timeseries`(L1 反演+速度)作为新方法 | CLI | 桥 5-8 / 内置 2-3 | **P0(桥)** |
| 8 误差校正 | `tropo_era5_pyaps` 等(MintPy) | 不变;注意 CSLC 已内嵌 SET/静态对流层/TEC 校正,**需在 registry 声明防重复校正** | — | 1-2(声明梳理) | P1 |
| 9 形变模型 | `linear`/`step`/… (MintPy) | 不变(dolphin velocity 仅线性,不能替代模型族) | — | 0 | — |
| 10 出图导出 | `figure_journal` / `gdal_warp` | 不变;dolphin 产物为带 overview 的 GeoTIFF,反而更顺 | — | 0-1 | — |
| 11 质检 | `crossval_ps_sbas` / `loop_closure` | 不变;**新增素材**:dolphin 附带 temporal_coherence、similarity、closure_phase(`write_closure_phase`)栅格可进阈值台账;ISCE2 链 vs isce3 链双链交叉验证是 crossval 的天然扩展 | — | 1-2 | P2 |
| 3-6 条带 | `isce2_stripmap_*`(stripmapApp,场景包覆写) | **无对应物**(仅示例级 ALOS→NISAR 转换,§1.4)→ 不迁 | — | 0 | 长期留 ISCE2 |

合计(TOPS 链共存,P0+P1):约 **22-33 人日**,拆成两个里程碑落地(§3 阶段 1/2)。

**registry 设计要点**(声明形态,不改代码,落地时执行):

- **新增方法族而非替换**:`Method(engine="compass"/"dolphin")` + `requires_engines=("isce3",...)`,ISCE2 方法原样保留——`registry/model.py` 的 Method/requires_engines/scenario_only 机制现成支持,stripmap 场景包已验证过"同 cap 双链共存"的全部路径(方法路由、产物候选列表、分段区间表);
- **新引擎探测**:`runtime/wsl_probe.py` 的 `_ENGINES` 加 `isce3`(`python -c "import isce3"`)、`compass`(`command -v s1_cslc.py`)、`dolphin`(`command -v dolphin`),env 前缀走新变量(如 `INSAR_ISCE3_PREFIX`);
- **产物布局**:`ArtifactSpec` 新增 `layout="cslc_geo"`(UTM GeoTIFF/HDF5),cap4-6 的候选路径列表加 dolphin 工作目录形态(`interferograms/`、`unwrapped/`、`timeseries/`);
- **断点语义**:dolphin 无 pickle 链,分步靠"配置开关 + 独立子命令 + 产物存在性"——比 ISCE2 的 pickle 连续性约束(`engines/isce2.py` 区间表的硬约束)干净得多,executor 无需新机制;
- **参数三分类**:`strides/half_window/ministack_size/max_bandwidth/alpha/unwrap_method/init_method/cost` 归 science(进指纹);`threads_per_worker/n_parallel_bursts/block_shape` 归 resource(进 env 不进指纹,与现 `threads` 同法)。

---

## 3. 分阶段路线

### 阶段 0:决策与跟踪(现在,≈0.5 人日)

- 本文档评审定案;不改代码;
- 建立跟踪清单(每季度看一眼):MintPy 官方 `prep_dolphin` 是否落地([#1016](https://github.com/insarlab/MintPy/discussions/1016))、dolphin 是否 1.0、HyP3 burst InSAR 是否迁 ISCE3、conda-forge isce2 是否 build 失败、sweets fork 依赖是否合上游。

### 阶段 1:短期共存——环境先行 + 解缠器先行(1-2 个月,≈10-14 人日)

1. WSL 增装独立 `insar3` env(§4 命令序列;**不动现有 insar env**——isce2 的 numpy<2 与 isce3 生态的 numpy≥2 硬冲突,分环境是唯一解);
2. `wsl_probe`/probe 扩展三个引擎键,面板可见;
3. **cap6 先行**:`dolphin_unwrap_snaphu` / `dolphin_unwrap_spurt` 作为解缠新方法(dolphin 可直接消费 ISCE2 topsStack 形态的输入,[官方文档明示](https://dolphin-insar.readthedocs.io/en/latest/getting-started/) `--slc-files merged/SLC/*/*.slc`)——不动上游即可演示"解缠失败→换方法→失效传播",兑现竞品报告借鉴项 #4;
4. 顺手:现有 insar env 的 isce2 从 2.6.3 升 2.6.5(S1C/S1D 支持;独立动作,验证后更新 `scripts/wsl_setup.sh` 声明)。

### 阶段 2:中期新增方法族——TOPS 全链(3-6 个月,≈15-20 人日)

1. `engines/compass.py`:cap3 新方法 `compass_cslc_geo`(runconfig YAML 渲染 + 每日期 run 脚本,形态复用 `engines/isce2.py` 的 CommandPlan 模式);
2. `engines/dolphin.py`:cap4-6 三段式——cap4+5 = `dolphin run`(`run_unwrap: false`,Goldstein/插值作为 cap5 参数注入),cap6 = `dolphin unwrap`(或 run_unwrap: true 的续跑);cap7 新增可选方法 `dolphin_timeseries`;
3. **桥**:dolphin 输出→MintPy(优先等官方 prep;未落地则自写最小 prep,借鉴 Dolphin2MintPy 的 .rsc sidecar 方案,radar/geo 判定显式化——把"桥的正确性边界"写进声明,竞品报告 §3.3-5 的空白正是我们的主张);
4. 新场景包 `tops_isce3`(step_overrides 覆写 3-6 步方法,复制 stripmap_coseismic 的成熟模式);
5. 验证:同一 S1 数据对,ISCE2 链 vs isce3 链**双链跑通并交叉对比**(解缠相位差异、速度场相关性)——本身就是 cap11 crossval 的绝佳素材与论文证据。

### 阶段 3:长期默认切换(6-12 个月后,按触发指标)

- 触发条件(满足任意两条):官方 prep_dolphin 落地;dolphin ≥1.0 或 DISP-S1 算法冻结(ATBD Rev B);我们双链交叉验证通过阈值台账;ISCE2 在环境刷新中 build/依赖失败一次;
- 动作:cap3-6 的 S1 IW 场景 `default_method` 切到 isce3 族;ISCE2 降级为"条带专用 + 回退引擎";`recommend` 标志与 planner 收窄理由同步更新;
- 同期评估 NISAR 路线:GUNW→`prep_nisar`→cap7 直通(类 HyP3 的"云端完成 2-6 步"声明,`cloud_completed` 机制现成),让 insar-agent 成为少数原生支持 NISAR 的编排层。

---

## 4. WSL 安装验证命令序列(**只记录,不执行**)

前提:现有发行版 `insar`(Ubuntu 24.04,Miniforge 在 `/opt/miniforge3`,`docs/WSL-SETUP.md`);全程不触碰现有 `insar` env。镜像沿用清华 conda-forge(与 `scripts/wsl_setup.sh` 一致)。

```powershell
# 0) 可达性与磁盘预检(CSLC 栈:单 burst 单日期约百 MB 量级,
#    30 景 × 10 burst 的栈预算数十 GB;E: 盘 vhdx 余量先看)
wsl -d insar -u root -- bash -lc 'echo ok && df -h / && /opt/miniforge3/bin/mamba --version'

# 1) 建独立 env(isce3 生态 numpy>=2,与 isce2 env 的 numpy<2 硬冲突,必须分环境)
wsl -d insar -u root -- bash -lc '/opt/miniforge3/bin/mamba create -n insar3 -y -c https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/conda-forge --override-channels python=3.12 isce3-cpu compass s1reader dolphin snaphu tophu opera-utils burst2safe sentineleof sardem gdal'
#    备注:
#    - isce3-cpu 即可(GPU 需求再评估 isce3-cuda,WSL2+NVIDIA 驱动理论可用)
#    - spurt 若 conda-forge 解析失败则 pip 补装(见步骤 2b)
#    - conda-forge compass(0.5.6)可能滞后于 GitHub main;若与 dolphin 新版接口冲突,
#      改用 pip 装 GitHub 版:python -m pip install git+https://github.com/opera-adt/COMPASS.git

# 2) 验证:import 层
wsl -d insar -u root -- bash -lc '/opt/miniforge3/envs/insar3/bin/python -c "import isce3; print(\"isce3\", isce3.__version__)"'
wsl -d insar -u root -- bash -lc '/opt/miniforge3/envs/insar3/bin/python -c "import compass, s1reader, snaphu, tophu, opera_utils; print(\"imports ok\")"'
# 2b) spurt(conda 无则 pip)
wsl -d insar -u root -- bash -lc '/opt/miniforge3/envs/insar3/bin/python -c "import spurt" || /opt/miniforge3/envs/insar3/bin/python -m pip install spurt'

# 3) 验证:CLI 层(与未来 wsl_probe 探测命令一致)
wsl -d insar -u root -- bash -lc 'export PATH=/opt/miniforge3/envs/insar3/bin:$PATH; command -v s1_cslc.py && s1_cslc.py --help >/dev/null && echo compass_cli=ok'
wsl -d insar -u root -- bash -lc 'export PATH=/opt/miniforge3/envs/insar3/bin:$PATH; dolphin --help >/dev/null && echo dolphin_cli=ok'
wsl -d insar -u root -- bash -lc 'export PATH=/opt/miniforge3/envs/insar3/bin:$PATH; dolphin config --print-empty | head -n 20'
wsl -d insar -u root -- bash -lc 'export PATH=/opt/miniforge3/envs/insar3/bin:$PATH; command -v eof && command -v sardem && command -v opera-db && echo aux_cli=ok'

# 4) 环境变量(落地时写入 /etc/profile.d/insar.sh,供 wsl_probe 扩展)
#    export INSAR_ISCE3_PREFIX=/opt/miniforge3/envs/insar3

# 5) 冒烟(拿到测试数据后;遵守重型计算规矩,须先获批准):
#    单 burst 单日期 COMPASS + 两日期 dolphin 全默认,线程限 8,BelowNormal 优先级
```

回滚:`/opt/miniforge3/bin/mamba env remove -n insar3` 即可,零残留(不碰 insar env 与 profile)。

---

## 5. 风险与决策点

| # | 风险 | 等级 | 缓解 |
|---|---|---|---|
| 1 | **dolphin→MintPy 桥无官方支持**(§1.5):元数据语义错配,radar/geo 误判 | 高 | 阶段 2 把桥当独立交付物(5-8 人日预算);优先等官方 prep(#1016);自写 prep 时把坐标判定显式声明进 registry;备选:`dolphin timeseries` 直出 + 仅 cap8/9 转 MintPy 格式 |
| 2 | **步骤边界错位**:dolphin 一个 YAML ≈ 我们 cap4-6,滤波退化为配置段 | 中 | 三段式拆分(run_unwrap 开关 + `dolphin unwrap` 子命令);cap5 在 isce3 族里声明为"参数注入型方法"(无独立执行),failure 传播语义在 registry 层保持不变 |
| 3 | **重复校正**:CSLC 内嵌 SET/静态对流层/TEC,与 cap8 的 MintPy 校正叠加 | 中 | registry 参数显式声明 CSLC 校正开关;cap8 方法 `why` 注明与 CSLC LUT 的互斥关系;交叉验证时监控 |
| 4 | **版本节奏快**:dolphin 月度级发版(0.42.x),COMPASS conda 包滞后,sweets 0.3.x 依赖 fork | 中 | env 里 pin 版本并记录进指纹(tool_upgraded 失效传播正好覆盖);不封装 sweets,只封装 COMPASS/dolphin 两个稳定层 |
| 5 | **s1-reader 自标 pre-alpha**、isce3 README 自标 early development | 中 | 以 OPERA 生产背书(CSLC-S1/DISP-S1 validated 产品在产)对冲;关键在 pin 版本 + 双链交叉验证,不裸信 |
| 6 | **磁盘**:CSLC 栈(5×10 m)显著大于多视后的 ISCE2 中间产物;WSL vhdx 落 E: 的余量 | 中 | `DiskEstimate` 公式按实测标定后再放开大 AOI;strides 多视尽早降采样 |
| 7 | **ESD 参数消失**引起的用户心智/文档迁移(esd_coherence_threshold → 校正 LUT 开关) | 低 | 方法 `why` 与场景包知识正文写清两种配准的差异与适用性 |
| 8 | **条带链双引擎长期并存**的维护成本(两套 env、两套探测、两套形态) | 低 | 本来就是既定架构(WSL env 隔离 + registry 方法族);stripmap 已验证该模式可持续 |
| 9 | **OPERA CSLC 现成产品仅覆盖北美**,全球区域必须自跑 COMPASS | 低 | 路径 A/B 都封装,planner 按 AOI 收窄(数据发现自动化正好是竞品报告借鉴项 #5) |

**决策点清单**(需要主线拍板的,按时间序):

1. 【现在】是否批准阶段 1(建 insar3 env + cap6 解缠器先行)——建议:是,成本低、独立可回滚、演示价值高;
2. 【阶段 2 前】桥的路线:等官方 prep vs 自写 prep vs dolphin timeseries 直出——建议:先自写最小 prep(把桥的正确性边界做成声明,与项目主张一致),官方落地后切换;
3. 【阶段 2 中】cap5 的声明形态:独立"参数注入型方法" vs 并入 cap6 参数——影响失效传播粒度,建议保留独立 cap 以维持 11 步模型稳定;
4. 【阶段 3 前】S1 IW 默认方法切换时机——按 §3 触发条件,勿早于双链交叉验证通过;
5. 【并行】NISAR 路线(GUNW→prep_nisar→cap7)是否提为独立里程碑——取决于用户侧是否出现 NISAR 数据需求,机会窗口:MintPy 侧支持已成熟(2026-04 大修),竞品中仅 LiCSBAS2 跟进。

---

## 6. 来源

**ISCE3 本体**:[文档](https://isce-framework.github.io/isce3/) · [GitHub](https://github.com/isce-framework/isce3) · [conda-forge isce3](https://anaconda.org/conda-forge/isce3)(0.25.12)/ [isce3-cpu](https://anaconda.org/conda-forge/isce3-cpu) / [isce3-cuda](https://anaconda.org/conda-forge/isce3-cuda) · [Windows 平台缺席 issue #258](https://github.com/isce-framework/isce3/issues/258) · [AGU 2023 综述](https://ui.adsabs.harvard.edu/abs/2023AGUFM.G23C0488F/abstract) · ALOS 转换脚本:[share/nisar/examples](https://github.com/isce-framework/isce3/tree/develop/share/nisar/examples)(alos_to_nisar_l0b.py / alos2_to_nisar_l1.py)

**OPERA 工具链**:[COMPASS](https://github.com/opera-adt/COMPASS)([conda-forge](https://anaconda.org/conda-forge/compass)、[s1_cslc_geo.yaml 默认配置](https://github.com/opera-adt/COMPASS/blob/main/src/compass/defaults/s1_cslc_geo.yaml)) · [CSLC-S1 ATBD](https://cumulus.asf.earthdatacloud.nasa.gov/PUBLIC/DATA/OPERA/OPERA_CSLC-S1_ATBD_D-108752_Initial_2024-06-24_signed.pdf) · [CSLC-S1 产品页](https://www.earthdata.nasa.gov/data/catalog/asf-opera-l2-cslc-s1-v1-1) · [s1-reader](https://github.com/opera-adt/s1-reader) · [burst_db](https://github.com/opera-adt/burst_db) · [dolphin](https://github.com/isce-framework/dolphin)([JOSS 2024](https://doi.org/10.21105/joss.06997)、[docs](https://dolphin-insar.readthedocs.io/)、[sample config](https://github.com/isce-framework/dolphin/blob/main/docs/sample_dolphin_config.yaml)、[changelog](https://dolphin-insar.readthedocs.io/en/latest/changelog/)、[conda-forge](https://anaconda.org/conda-forge/dolphin) 0.42.5) · [sweets](https://github.com/isce-framework/sweets)(v0.3.1、[IfgWorkflow PR #152](https://github.com/isce-framework/sweets/pull/152)) · [disp-s1](https://github.com/opera-adt/disp-s1) · [DISP-S1 产品页](https://www.earthdata.nasa.gov/data/catalog/asf-opera-l3-disp-s1-v1-1) · [opera-utils](https://github.com/opera-adt/opera-utils) · [burst2safe](https://github.com/ASFHyP3/burst2safe) · [bowser](https://github.com/opera-adt/bowser)

**解缠器**:[snaphu-py](https://github.com/isce-framework/snaphu-py) · [spurt](https://github.com/isce-framework/spurt) · [tophu](https://github.com/isce-framework/tophu) · whirlwind(dolphin `unwrap_method` 官方选项)

**MintPy 衔接**:[prep_dolphin 讨论 #1016](https://github.com/insarlab/MintPy/discussions/1016) · [NISAR 加载大修 PR #1487](https://github.com/insarlab/MintPy/pull/1487)(2026-04) · [dolphin→MintPy 用户踩坑 #677](https://github.com/isce-framework/dolphin/discussions/677) / [MintPy #1460](https://github.com/insarlab/MintPy/discussions/1460) · [Dolphin2MintPy(第三方桥)](https://github.com/bcankara/Dolphin2MintPy) · [calval-DISP(DISP-S1→MintPy)](https://github.com/OPERA-Cal-Val/calval-DISP)

**ISCE2 维护状态**:[停维讨论 #28133](https://github.com/isce-framework/isce2/discussions/28133)(2026-07) · [v2.6.4](https://github.com/isce-framework/isce2/releases/tag/v2.6.4)(2025-05) · [v2.6.5](https://github.com/isce-framework/isce2/releases/tag/v2.6.5)(2026-06-30,S1C/S1D) · [conda-forge isce2](https://anaconda.org/channels/conda-forge/packages/isce2/overview) · [社区安装指南(numpy/python 钉死)](https://github.com/lijun99/isce2-install)

**行业迁移信号**:[hyp3-autorift v0.22.0(ISCE2→ISCE3 全面转换)](https://doi.org/10.5281/zenodo.15611848) · [hyp3-isce3](https://github.com/ASFHyP3/hyp3-isce3) · [NISAR 数据可用性](https://nisar-docs.asf.alaska.edu/availability-overview/) · [NISAR PROVISIONAL 发布公告(2026-07-20)](https://asf.alaska.edu/notices/nisar-l-band-data-now-publicly-available/)

**本仓库**:`src/insar_agent/engines/isce2.py`(XML 渲染 + pickle 分段区间表) · `src/insar_agent/registry/capabilities.py`(11 步声明) · `src/insar_agent/registry/model.py`(Method/requires_engines/scenario_only) · `src/insar_agent/runtime/wsl_probe.py`(引擎探测契约) · `docs/WSL-SETUP.md` · `docs/VALIDATION-isce2-wsl.md` · `reference/RESEARCH-competitors-2026-08-12.md`

> 可信度说明:版本号/日期均来自官方 release 页、conda-forge 包页或 GitHub 讨论区原文(检索日 2026-08-12);人日估算为本项目自估;COMPASS 本地输出 HDF5 的数据集内部路径、`opera-utils`/`sardem` 的具体子命令形态未逐一实测,落地阶段 1 时以 `--help`/`h5ls` 实测为准。
