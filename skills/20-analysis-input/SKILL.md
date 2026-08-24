---
name: 20-analysis-input
description: 当规划或诊断分析链第 20 步分析输入(登记待分析的 h5/GeoTIFF/CSV 源产物)时使用:确认分析链是可选的、用 register_sources 把相对路径物化到 analysis/source.*,诊断扩展名拒绝、路径逃逸与次源缺失,不要把本步当成科学重算。
capability: 20
version: "1.0.0"
applies_to: all
---

# 分析输入(登记源产物)

分析链(capability id 20–28,`group="analysis"`)是**可选**的:核心 run 默认只规划
1–11,本步不会自动出现。需要后处理/分解/统计/出图/变化检测/外推/反演桥时,
另规划 `groups=("analysis",)` 的分析 run。id 12–19 留给核心链未来成长,当前
无步骤、无技能。方法 id 只有 `register_sources`(默认、推荐,引擎标识 `-`);
产物 `src_primary`(候选 `analysis/source.h5` / `source.tif` / `source.csv`,
policy=stat)与可选 `src_secondary`(`analysis/source_2.*`)。真实口径:
`engines/passthrough.py` 硬链接优先、拷贝兜底,把 `params.primary`(及非空
`secondary`)落到规范路径 —— **不改写科学数值**。`replay = safe`。

## 适用判据

- **何时开分析链**:已有速度场/时序/GeoTIFF/点 CSV,要对结果做后处理,而不是
  重跑 1–11。典型入口:模式 C(位移产品直接分析)、升降轨分解、剖面统计、
  把速度场交给 GBIS/Kite(第 28 步只导出,不在 Agent 内反演)。
- **`register_sources`(唯一方法)**:把工作区相对路径登记为链首。后续 21–28
  读固定规范路径,零决策。单源分析只填 `primary`;升降轨分解必须同时给
  `secondary`(第 23 步 `asc_desc_horz_vert` 要求 `analysis/corrected_2.h5`)。
- **不要选本步的理由**:还没有可分析的真实文件(模拟 run 的占位字节不是源);
  仍在跑核心 1–11 —— 分析 run 不重跑主链。
- 允许扩展名(大小写不敏感):`.h5` / `.hdf5` / `.tif` / `.tiff` / `.csv`。
  PNG/KMZ/shp 等在 build 期 `ValueError`,绝不静默当 h5。

## 参数启发式

本步骤在 registry(capabilities.py id=20)声明的参数:

- **`primary`**(science,默认 `mintpy/velocity.h5`):主源相对工作区路径。必须
  是已存在的文件,禁止绝对路径/盘符逃逸(与第 3 步 stripmap 路径参数同形态,
  进指纹)。换源 = 换实验。
- **`secondary`**(science,默认 `""`):次源(降轨速度场等)。单源留空;填了就必须
  存在且扩展名合法,否则作业非 0。
- **`primary_run` / `secondary_run`**(science,默认 `""`):源 run_id,仅溯源
  记录,物化不读它们。
- **格式行为**(engines/passthrough.py,不编造):
  - HDF5:原样物化到 `analysis/source.h5`,写 `source_manifest.json`;
  - GeoTIFF:硬链 `analysis/source.tif`;有 rasterio+h5py 时把**真实像元**写入
    `source.h5` 的 `velocity`,无 rasterio/无 CRS 则不假装地理参考、不填随机数;
  - CSV:按原列写出 `analysis/source.csv`;列名只做 lon/lat/velocity 启发式识别,
    不发明列(未识别也保持原样)。

## 常见失败与处置

1. **`register_sources 源不存在`** → `primary` 相对当前分析 run 工作区,不是
   源 run 的绝对路径 → 把产物拷进本工作区或填相对路径;不要改用绝对路径
   (逃逸检查会拒)。
2. **不支持扩展名** → 只吃 h5/tif/csv;PNG/PDF 出图不是本步输入 → 换源或先用
   第 10/25 步的栅格/h5 产物。
3. **路径逃逸出工作区** → 含 `..` 或盘符 → 改为工作区内相对路径。
4. **次源不存在 / 扩展名非法** → `secondary` 非空就会强制校验 → 单源分析清空
   `secondary`;双源把降轨文件放到工作区再填相对路径。
5. **期望本步做掩膜/分解/反演** → 本步只登记。掩膜是 21,分解是 23,GBIS 是
   28 的导出桥;**Agent 内没有 GBIS/Okada 反演,没有 GNSS 平差,没有 ISCE3**。
6. **CSV 没有 lon/lat 就失败?** → 不会。启发式未识别时列保持原样,只在
   manifest 注明;后续 MintPy CLI 步骤需要 h5,CSV 源走不了 21+ 的 mintpy
   方法,应改用 h5/GeoTIFF 或停在登记。

## QA 依据

- run_ok:`exit_code == 0` + `artifact_exists(src_primary)`。`src_secondary`
  非必填。
- 物化纪律:目标文件 sha256 与源一致(硬链/拷贝 sidecar 记录 `mode` 与哈希);
  数值必须来自源文件,禁止 simulate 注入合成产物(passthrough 红线)。
- 分析链拓扑(规划器 `groups=("analysis",)`):20→21→22→23→24,其后 25 / 26→27 /
  28 并行依赖 24。多数后续步 `default_method=passthrough`,未显式改方法时只透传
  或写跳过标记,不要把「规划了分析 run」理解成「做了分解/预测/反演」。

## 参考文献

- MintPy 产物惯例(velocity.h5 / timeseries.h5 / geometry*.h5):
  Yunjun Z., Fattahi H., Amelung F. (2019). *Computers & Geosciences* 133:104331.
  doi:10.1016/j.cageo.2019.104331
- 分析 run 与 `group="analysis"` 设计:pi-insar/docs/plan/30-phase10-postprocess-capabilities.md
  (id 从 20 起,12–19 留空;相对路径作 science 参数)
- GeoTIFF → 栅格:GDAL/rasterio 读真实像元;无库则只保留 GeoTIFF,不编地理参考
- 本仓库实现:src/insar_agent/engines/passthrough.py(`register_sources`;
  SUPPORTED_SOURCE_EXT);registry `capabilities.py` ANALYSIS id=20
- 模式 C(产品直接分析)与核心 1–11 正交:src/insar_agent/api/access_mode.py
  (`_ANALYSIS_MIN = 20`)
