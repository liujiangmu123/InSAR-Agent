---
name: 24-stats-profile
description: 当规划或诊断分析链第 24 步统计剖面(剖面、区域平均、时段平均、残差 RMS)时使用:默认方法是 transection 而非 passthrough,必须给起点终点;本步只做描述统计,不是反演、不是变化检测。
capability: 24
version: "1.0.0"
applies_to: all
---

# 统计剖面(剖面 / 空间平均 / 时间平均 / RMS)

第 24 步从分解(或透传)场提取可引用的数字。方法 id:`transection`(默认、推荐,
引擎 mintpy,`plot_transection.py`)、`spatial_average`、`temporal_average`、
`timeseries_rms`(均引擎 mintpy)。**本步没有 passthrough** —— 规划了分析链
就会真的做统计,不像 21/22/23/25/26/27/28 那样默认跳过。产物 `measure`
(候选 `analysis/measure.json`、`analysis/transect.txt`,policy=content)。
输入 `decomposed`。依赖第 23 步。`replay = safe`。

## 适用判据

- **`transection`(默认)**:跨断层、跨沉降漏斗、跨边坡的一维梯度。必须提供
  `start_lalo` 与 `end_lalo`。适合论文剖面图的数据底稿(出图在第 25 步)。
- **`spatial_average`**:漏斗强度、参考区是否稳定、AOI 平均速率。实现绕开
  MintPy CLI 对 `FILE_TYPE=velocity` 写 SpatialAvg.txt 的上游缺陷,在引擎
  Python 里算 `nanmean`,写入 `analysis/measure.json`(真实像元,不编数)。
- **`temporal_average`**:输入须是时序类;对速度场跑时段平均没有意义。stdout
  原样落成 `measure.json`,不改写。
- **`timeseries_rms`**:残差 RMS,用于噪声水平与参考日期选择;同样要求时序
  类输入。速度场请用 spatial_average 或 transection。
- 变化检测是第 26 步,外推是第 27 步,GBIS 导出是第 28 步。本步只描述「现在
  这场数据的平均/剖面/RMS」。

## 参数启发式

- **`start_lalo` / `end_lalo`**(science,默认 `""`):`transection` 必填,
  `lat,lon`(实现也接受冒号)。剖面应穿过信号最大梯度,两端落在相对稳定区,
  以便读出相对幅度;不要把两端都放在漏斗里。
- **`aoi_lalo`**(science,默认 `""`):声明统计盒 `lat0:lat1,lon0:lon1`。
  **当前 `spatial_average` 实现未把该盒传给计算**(全图 `nanmean`);需要 AOI
  时先在第 21 步 `subset_lalo`,再平均。不要假装本参数已裁剪。
- **`dataset`**(science,默认 `""`):h5 内数据集。分解产物用 `vertical` /
  `east`;空=MintPy/读取默认(通常 `velocity`)。透传 LOS 场不要填 vertical。
- 单位:MintPy 速度常为 m/yr,报告换算 mm/yr 时写明;剖面 txt 的列含义以
  MintPy 输出头为准,不要改列。

## 常见失败与处置

1. **`start_lalo 须为两个值`** → 默认方法就是 transection,空参数会失败 →
   填起点终点,或改 `spatial_average`(不需要剖面坐标)。
2. **transection 输入不存在:analysis/decomposed.h5** → 第 23 步没跑或
   passthrough 源不是 h5 → 回查 20–23。
3. **spatial_average 结果非有限值** → 全 NaN 场 → 病根在掩膜/分解重叠,本步
   不填 0 充数。
4. **temporal_average / timeseries_rms 在速度场上崩溃** → 换方法或换时序
   h5 到规范路径(先确认 FILE_TYPE)。
5. **把剖面峰值当成反演滑动量** → 本步无弹性模型。要源反演走第 28 步导出
   GBIS/Kite,**在那些软件里反演**,不要在 measure.json 里编 Okada 解。

## QA 依据

- run_ok:`exit_code == 0` + `artifact_exists(measure)`。
- `spatial_average`:`measure.json` 含 `method`/`values`/`mean`(单值时)与可选
  `unit`;`allow_nan=False` 写入,缺有限值即失败。
- `transection`:`analysis/transect.txt` 由 MintPy `--nodisplay` 写出;点数过少
  (剖面几乎不压到有效像元)时结论降级。
- 无合同阈值;数字必须能从产物重算(policy=content)。

## 参考文献

- MintPy CLI:`plot_transection` / `spatial_average` / `temporal_average` /
  `timeseries_rms`(https://github.com/insarlab/MintPy)
- Yunjun et al. (2019). *C&G* 133:104331(残差 RMS 与参考日期)
- 本仓库:engines/mintpy_post.py(`_spatial_average` 绕开上游 IndexError/
  FileNotFoundError;transection 把剖面写到 analysis/transect.txt)
