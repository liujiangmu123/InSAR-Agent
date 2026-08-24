---
name: 27-extrapolate
description: 当规划或诊断分析链第 27 步外推预测(只外推第 9 步已落账时间函数,强制不确定度与有效期,或默认不做)时使用:默认 passthrough;模拟 run 拒绝外推。不是地震发生预测,不是滑坡破坏时间预报。
capability: 27
version: "1.0.0"
applies_to: all
---

# 外推预测(已拟合时间函数 / 透传)

第 27 步在「若当前趋势延续」前提下外推位移,必须带置信区间与有效期。方法 id:
`extrapolate_fitted`(引擎 `-`,自研 `engines/predict.py`)、`passthrough`
(默认、推荐)。**`default_method=passthrough`**:写
`analysis/prediction.json`,status=passthrough,明确列出不是地震/滑坡破坏/
新阶跃的预报,ci 为 null。产物 `prediction`(必填)与可选 `prediction_fig`。
输入 `change_summary`。依赖第 26 步。`replay=safe`。

**纪律**:只外推第 9 步已落账的时间函数族;不允许训练式黑箱;不允许模拟 run
的占位字节。

## 适用判据

- **`passthrough`(默认)**:不问「未来会怎样」、或证据不足以外推。JSON 仍落地,
  避免下游缺产物;数字全是空/占位说明,不是预测值。
- **`extrapolate_fitted`**:核心链第 9 步已拟合 linear / poly_periodic / step /
  exponential,工作区有真实 `velocity.h5`(含 `velocityStd`)及可确定的观测时长
  (timeseries 的 date,或属性 START_DATE/END_DATE)。从账本 `run.chain[9]`
  或 h5 属性读取模型族,**拒绝自行选模型**。
- **永远不要当成本步能做的事**:地震发生时间、滑坡破坏时间、新同震/突水阶跃、
  深度学习时序预报、ISCE3/物理流变正演。问到时只提供趋势外推 + 第 26 步加速
  识别入口。

## 参数启发式

- **`horizon_years`**(science,默认 `1.0`,0.05–10):外推时长(年)。有效期上限
  `min(0.5 × 观测时长, 2 年)`。超限必须填 `horizon_override_reason`(进指纹/
  账本/报告);空则 `HorizonCapError`,不写出预测。
- **`horizon_override_reason`**(science,默认 `""`):超上限的理由。没有理由就
  把 horizon 降到上限内,不要「先写出再注释不可靠」。
- **`points_lalo`**(science,默认 `[]`):`[[lat,lon], ...]`。空=仅整场中位速度
  × horizon;有点且有时序则对该像元 OLS 外推。点预测需要地理编码属性。
- **`confidence`**(science,默认 `0.95`,0.5–0.999):置信水平。区间来自系数
  协方差或 `velocityStd` 传播;缺 `velocityStd` → `IncompletePrediction`,
  **没有不确定度就没有预测**。
- 阶跃项:拟合期内 Heaviside 保留,外推期贡献为常数,**未来不会再跳一次**。

## 常见失败与处置

1. **模拟 run 不可外推** → `runs.simulated=1` 的产物是演示字节 → 配真引擎重跑
   1–11 再外推;与导出 409 同理。
2. **无法读取时间函数族** → 分析 run 没有第 9 步账本、velocity 属性也没有
   timeFunc → 不要手选 linear 充数;回到有账本的核心 run 工作区。
3. **horizon 超过上限** → 缩短 horizon 或写 override 理由。
4. **velocity 全 NaN / 无 velocityStd** → 拒绝落盘。passthrough 仍可过门,
   但那不是预测。
5. **把 prediction.json 的毫米数说成「将在某日滑动」** → 违反
   `not_a_forecast_of`。回答必须带「若当前趋势延续」、CI、有效期。

## QA 依据

- run_ok:`exit_code == 0` + `artifact_exists(prediction)`。
- `extrapolate_fitted` 落盘三要素缺一不可:`assumptions`、`not_a_forecast_of`、
  `ci.lower/upper`;否则不写文件。
- 图标题固定 `LOS displacement extrapolation, not a forecast`(有 matplotlib
  才写 png;缺库跳过,json 仍算成功)。
- 数学自洽:解析直线+噪声的外推与闭式解一致;含 step 的模型外推段导数不含新
  阶跃(tests/test_predict.py)。

## 参考文献

- Hetland et al. (2012). *JGR* 117:B02404(时间函数族;本步锁定第 9 步已选族)
- Fattahi H., Amelung F. (2015). InSAR uncertainty due to orbital errors.
  *JGR* / MintPy residue 速度不确定度传播(本步整场 CI 用 velocityStd)
- 本仓库:engines/predict.py(`enforce_horizon`、`NOT_A_FORECAST_OF`、
  模拟拒绝);pi-insar/docs/plan/33-phase13-analysis-and-prediction.md
- passthrough 标记字段见 engines/passthrough.py `_PASSTHROUGH_MARKERS[27]`
