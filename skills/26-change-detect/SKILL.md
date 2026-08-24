---
name: 26-change-detect
description: 当规划或诊断分析链第 26 步变化检测(历元差分、二次加速度、速度对比,或默认跳过)时使用:默认 passthrough;显著性口径是 |a|/σ_a≥2 的声明与测试辅助,CLI 目前主要写 change.h5。不要把本步当成深度学习解缠或地震预测。
capability: 26
version: "1.0.0"
applies_to: all
---

# 变化检测(历元差 / 加速度 / 速度对比 / 透传)

第 26 步回答「这两个时期/两套解之间发生了什么」。方法 id:`epoch_diff`
(mintpy `diff.py`)、`quadratic_accel`(`timeseries2velocity --poly 2`)、
`velocity_compare`(`diff.py velocity`)、`passthrough`(默认、推荐)。
**`default_method=passthrough`**:写 `analysis/change_summary.json`(方法跳过
标记,`significant_fraction: null`)并透传 `decomposed.h5` → `change.h5`,
**不做检测**。产物 `change`(`analysis/change.h5`,非必填)与必填
`change_summary`。输入 `decomposed`。依赖第 24 步(与第 25/28 并行)。

本步不是解缠、不是深度学习 unwrap、不是地震/滑坡时间预报。

## 适用判据

- **`passthrough`(默认)**:本次分析不做变化检测。第 27 步依赖本步产物,跳过
  时仍要本步成功(标记 JSON),以便外推也可以继续 passthrough。
- **`epoch_diff`**:两历元或两文件相减。参数 `epoch1`/`epoch2` 为工作区相对
  路径;空则回落到 `analysis/corrected.h5` 与 `corrected_2.h5`。
- **`velocity_compare`**:两个速度解相减。`secondary_velocity` 空则用
  `corrected_2.h5`。这是「两套处理差多少」,不是加速度。
- **`quadratic_accel`**:对时序拟合二次多项式,二次项即加速度。实现调用
  `mintpy.cli.timeseries2velocity --poly 2 -o analysis/change.h5`。显著性辅助
  函数声明 `|a|/σ_a ≥ 2`(mintpy_post.ACCEL_SIGMA_RATIO),供测试与摘要使用;
  **当前 CLI 路径不自动写带该口径的 change_summary.json**。
- **诚实缺口**:run_ok 要求 `change_summary.json`。passthrough 会写;上述
  MintPy CLI 方法目前主要写 `change.h5`。选科学方法后若缺 summary,属实现
  缺口而非「检测无变化」—— 不要补编 significant_fraction。

## 参数启发式

- **`epoch1` / `epoch2`**(science,默认 `""`):`epoch_diff` 的两个文件。须都
  在工作区内;单位一致才能减。
- **`secondary_velocity`**(science,默认 `""`):`velocity_compare` 的对比场。
- 加速度:需要时序(`ts_file` 缺省 `mintpy/timeseries.h5`,registry 未列该键)。
  分析工作区无时序则不要选 `quadratic_accel`。二次项会吸收未建模的阶跃/季节
  —— 同震或强季节场景先看第 9 步模型是否匹配,再解释「加速」。
- `|a|/σ_a ≥ 2` 是常用 2σ 启发式,不是合同硬门,也不是滑坡破坏时间模型。

## 常见失败与处置

1. **只有 passthrough 的 change_summary** → 默认如此。要差分就改方法并准备
   双文件/时序。
2. **diff 缺文件** → 单源分析没有 `corrected_2.h5` → 显式给 epoch2/次速度路径,
   或保持 passthrough。
3. **quadratic_accel 找不到 timeseries.h5** → 拷入核心时序,或不要选该方法。
4. **artifact_exists(change_summary) 在科学方法下失败** → CLI 未写该 JSON →
   如实报缺产物,不要手写假摘要;passthrough 才能稳定过门。
5. **把加速像元当「即将滑动」** → 拒绝。无水文/物性/降雨的破坏时间预报不在
   能力闭集(第 27 步同样声明)。

## QA 依据

- run_ok:`exit_code == 0` + `artifact_exists(change_summary)`。`change.h5`
  非必填。
- passthrough 标记:`method=passthrough`,`significant_fraction=null`,
  `note` 说明未做检测。
- 若未来写入加速度摘要,应含 `criterion: |a|/sigma_a >= 2`、`n_valid`、
  `n_significant`(mintpy_post.summarize_accel);在写出来之前不得在报告里引用
  这些数字。

## 参考文献

- Hetland E.A. et al. (2012). Multiscale InSAR Time Series (MInTS) analysis of
  surface deformation. *JGR* 117:B02404. doi:10.1029/2011JB008731(时间函数;
  二次项是多项式族的一员,不是独立物理模型)
- 加速度解释须对照第 9 步已拟合模型:未建模阶跃/季节会被二次项吸收,不能直接
  当成物理加速(见 skills/09-deformation-model)
- MintPy `timeseries2velocity --poly 2`;`diff.py`
- 本仓库:engines/mintpy_post.py(`quadratic_accel` / `epoch_diff` /
  `velocity_compare`);engines/passthrough.py 第 26 步标记 JSON
