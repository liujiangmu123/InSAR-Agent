---
name: 28-inversion-bridge
description: 当规划或诊断分析链第 28 步反演桥导出(GBIS/Kite/GMT/QGIS/HDF-EOS5,或默认不导出)时使用:默认 passthrough;只导出文件供外部反演,Agent 内没有 GBIS/Okada/Mogi 反演,不要编造滑动分布。
capability: 28
version: "1.0.0"
applies_to: all
---

# 反演桥导出(GBIS / Kite / GMT / QGIS / HDF-EOS5 / 透传)

第 28 步把分析场交给**外部**工具。方法 id:`bridge_gbis`(`save_gbis.py`)、
`bridge_kite`(`save_kite.py`)、`bridge_gmt`(`save_gmt.py`)、`bridge_qgis`
(`save_qgis.py`)、`bridge_hdfeos5`(`save_hdfeos5.py`)、`passthrough`(默认、
推荐)。**`default_method=passthrough`**:写
`analysis/bridge/passthrough.json`(`本次分析不导出反演桥`),不产生 .mat。
产物 `bridge_out`(`analysis/bridge`)。输入 `measure`。依赖第 24 步(与 25/26
并行,不依赖外推)。`replay=safe`。

**红线:不在 Agent 内做 GBIS/Okada/Mogi/Yang/滑动分布反演。** 导出后到 GBIS
或 Grond/Kite 里反演;问「帮我反演断层」时说明边界,不要编滑移。

## 适用判据

- **`passthrough`(默认)**:不需要外部反演/GIS 件。
- **`bridge_gbis`**:导出 `.mat` 给 GBIS 贝叶斯形变源反演。需要速度/位移场 +
  几何文件;缺几何真实报错。Agent 停在 gbis.mat。
- **`bridge_kite`**:导出 Kite npz/yaml,供 quadtree 降采样与 Grond。`dataset`
  必填语义(默认 `velocity`)。
- **`bridge_gmt`**:导出 GMT `grd`。输入必须已地理编码(`Y_FIRST`);**不做静默
  geocode**。
- **`bridge_qgis`**:时序矢量点,需 `ts_file` + 几何。
- **`bridge_hdfeos5`**:UNAVCO 惯例存档;工作目录切到 `analysis/bridge`。
- 引擎缺失 → `ToolMissing`,禁止静默跳过或回退 simulate(tests/test_bridge_export.py)。
  不存在 `bridge_okada_homemade` 一类自制反演方法。

## 参数启发式

- **`input`**(science,默认 `analysis/decomposed.h5`):GBIS/Kite/GMT 的场。
- **`ts_file`**(science,默认 `mintpy/timeseries.h5`):QGIS/HDF-EOS5。分析
  工作区常无此文件 → 先拷入或改 passthrough。
- **`geom_file`**(science,默认 `mintpy/inputs/geometryGeo.h5`):GBIS 必填;
  其余按 CLI,缺则真实报错。
- **`mask_file`**(science,默认 `""`):可选;空=不掩。
- **`dataset`**(science,默认 `velocity`):Kite 用。分解场导出垂直分量时改
  `vertical`,不要把 LOS 当垂直交给弹性模型还自称 3D。

## 常见失败与处置

1. **只有 passthrough.json** → 默认。要导出则改方法并备齐几何/时序。
2. **几何文件不存在** → 从核心 run 拷 `geometryGeo.h5`;没有几何不要选 GBIS。
3. **GMT:未地理编码** → 先第 10 步 geocode;本步不偷偷 geocode。
4. **MintPy 引擎 Python 不存在** → ToolMissing,不是「已导出空文件」。
5. **用户要滑动分布/Okada 解** → 交付 bridge 文件 + 外部软件下一步;报告写
   「未在 Agent 内反演」。禁止用随机数或文献典型值填滑移。

## QA 依据

- run_ok:`exit_code == 0` + `artifact_exists(bridge_out)`。passthrough 靠
  目录内 json。
- 科学导出:目标扩展名应匹配方法(`.mat` / kite 前缀 / `.grd` / `.shp` /
  HDF-EOS5);0 字节或 CLI 非 0 即失败。
- 证据阶梯:导出成功 ≠ 反演成功。provenance 只记录桥接文件,不记录未跑的
  GBIS 后验。

## 参考文献

- Bagnardi M., Hooper A. (2018). Inversion of Surface Deformation Data for
  Rapid Estimates of Source Parameters and Uncertainties: GBIS.
  *G-cubed* 19:2194-2211. doi:10.1029/2018GC007585(外部反演;本步只 save_gbis)
- Isken M. et al., Kite / Pyrocko Grond:quadtree 降采样与贝叶斯源反演(外部)
- Wessel P. et al. (2019). The Generic Mapping Tools version 6. *G-cubed*
  20:5556-5564. doi:10.1029/2019GC008515(GMT grd 制图,不是反演)
- MintPy CLI:`save_gbis` / `save_kite` / `save_gmt` / `save_qgis` / `save_hdfeos5`
- 本仓库:engines/mintpy_post.py 第 28 步;tests/test_bridge_export.py
  (未知方法 ToolMissing;不跑反演)
