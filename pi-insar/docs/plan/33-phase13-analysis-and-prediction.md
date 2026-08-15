# Phase 13 · 科学能力扩展 IV:变化检测与外推预测(有纪律的预测)

> **一句话目标**:回答两类高价值问题——"**哪里在变化/加速**"(变化检测)与"**照此趋势未来会怎样**"(外推预测)。
> **立场**:预测是全计划里**最容易造假**的能力。本 Phase 的一切设计都围绕一条纪律:**只外推已拟合、已落账的时间函数,必须带不确定度与有效期,拒绝一切黑箱**。

## 0. 预测纪律(先立法,后写码)

1. **模型来源封闭**:外推只允许使用第 9 步已拟合的时间函数族(线性 / 多项式 / 周期 / 阶跃 / 指数,MintPy `timeFunc` 语义),模型参数从**该 run 的真实产物**重估或读取——不允许引入训练式黑箱模型;
2. **不确定度强制**:预测输出必须包含置信区间,来源是 Phase 11 的 `uncertaintyQuantification`(residue/covariance/bootstrap)传播;没有不确定度就没有预测;
3. **有效期强制**:外推时长默认上限 = `min(0.5 × 观测时长, 2 年)`;超限需显式参数 `horizon_override_reason`(进指纹、进账本、进报告);
4. **阶跃不可外推**:同震阶跃(step)属一次性事件,外推期内**不允许**隐含"再来一次";指数/对数衰减项按其收敛行为外推并如实标注;
5. **措辞纪律**:一切输出(JSON/图/报告)统一使用"若当前趋势延续(if the current trend continues)"句式,预测 JSON 强制携带 `assumptions` 与 `not_a_forecast_of` 字段;模拟 run 的预测直接拒绝(与导出 409 同理);
6. **提前声明不做的**:地震发生预测、滑坡时间预报(需要物性/水文/触发因素,超出 InSAR 单一手段边界)。Agent 被问到时如实说明,只提供"形变趋势外推 + 加速识别"。

## 1. 能力设计(分析链追加两步)

### 第 26 步 · 变化检测(`group="analysis"`,`deps=(24,)`)

| 方法 | 实现 | 回答的问题 |
|---|---|---|
| `epoch_diff` | `mintpy.cli.diff` 两历元/两源相减 | "这两个时期之间发生了什么变化" |
| `quadratic_accel` | `timeseries2velocity --poly 2`,取二次项系数及其 std | "哪里在加速/减速"(加速度显著性 = \|a\|/σ_a ≥ 2) |
| `velocity_compare` | `diff.py` 两个 run 的速度场 | "两个解/两个时段的速度差异" |
| `passthrough` | 透传 | 本次分析不做变化检测 |

产物:`analysis/change.h5` + `analysis/change_summary.json`(显著变化像元占比、最大加速度及其位置——全部由数据算出)。

### 第 27 步 · 外推预测(`group="analysis"`,`deps=(26,)`,默认 `passthrough`)

| 方法 | 实现 |
|---|---|
| `extrapolate_fitted` | **新建 `engines/predict.py`**(纯 Python:h5py + numpy,零新依赖) |
| `passthrough` | 透传 |

`params`(全部 science,全部进指纹):

```python
"horizon_years": Param(1.0, kind="science", min=0.05, max=10,
                       hint="外推时长(年);默认上限 min(0.5×观测时长, 2 年),超限须填 horizon_override_reason"),
"horizon_override_reason": Param("", kind="science", type="str",
                                 hint="超上限外推的理由,进账本与报告;空=不允许超限"),
"points_lalo": Param([], kind="science", type="list", hint="重点预测点位;空=仅整场统计"),
"confidence": Param(0.95, kind="science", min=0.5, max=0.999, hint="置信水平"),
```

## 2. 文件级改动

### 2.1 `src/insar_agent/engines/predict.py`(**新建**,约 200 行)

```python
"""形变趋势外推(有纪律的预测):已拟合时间函数 + 不确定度传播,拒绝黑箱。

流程(全部真实数据,零网络):
  1. 读 timeseries*.h5(dates, ts)与 velocity.h5(velocity, velocityStd);
  2. 按第 9 步落账的时间函数族(从 run["chain"][9] 读 method/params,
     与账本一致 —— 不重新选模型)以最小二乘重估系数与协方差;
  3. 外推 horizon_years,置信带 = 设计矩阵外推行 × 系数协方差 传播
     (uncertainty=bootstrap 时读 bootstrap 分位数);
  4. 上限检查:horizon > min(0.5×span, 2yr) 且无 override_reason → 直接失败
     (真实报错,不悄悄截断);
  5. 输出:
     analysis/prediction.json   —— 逐点/整场的预测值、置信区间、模型、假设、
                                    有效期、"not_a_forecast_of" 免责闭集
     analysis/prediction.png    —— 观测(实线)+ 外推(虚线)+ 置信带(阴影),
                                    标题强制含 "extrapolation, not a forecast"
  6. 阶跃项处理:拟合期内的 step 保留在模型里,外推期贡献为常数(不再跳变),
     并在 assumptions 里写明 "no new step events assumed"。

模拟 run:直接 raise(与导出 409 同理:占位字节不可外推)。
"""
```

`build()` 返回的 `CommandPlan` 调 `python -m insar_agent.engines.predict <args>`(用 venv 的 Python,不是引擎 Python——本模块零 MintPy 依赖,h5py/numpy/matplotlib 在 venv 已有;核实:`.venv\Scripts\python.exe -c "import h5py, numpy, matplotlib"`)。

### 2.2 `src/insar_agent/engines/mintpy_post.py`(修改)

追加 `epoch_diff` / `quadratic_accel` / `velocity_compare` 分支。`quadratic_accel` 用
`python -m mintpy.cli.timeseries2velocity <ts> --poly 2 -o analysis/accel.h5`,
`change_summary.json` 由后置小脚本从 `accel.h5` 读二次项与 std 算显著性(同一 CommandPlan 内用 `&&`?——**不行**,CommandPlan 是单 argv;改为 predict.py 同款自建 CLI:`python -m insar_agent.engines.change_summary <accel.h5>`,单命令包干"跑 CLI + 汇总"两段,或拆成两条内部子命令由一个入口串联)。

### 2.3 `src/insar_agent/registry/capabilities.py`(修改)

按 §1 追加第 26/27 步声明(形态照抄 Phase 10 的分析步;`quality_gate` 给 27 加一条:`prediction.json` 存在且 `assumptions` 非空——通过 `run_ok` 的 `artifact_exists` + 新增 `content_key_present` 检查?若 `audit/runok.py` 无此检查类型,**不要新增检查类型**,把"assumptions 非空"放进 predict.py 自身的失败路径:缺假设直接不产出文件,`artifact_exists` 自然兜住)。

### 2.4 `src/insar_agent/engines/__init__.py`(修改)

```python
    if method_id in ("epoch_diff", "quadratic_accel", "velocity_compare"):
        from insar_agent.engines import mintpy_post
        return mintpy_post.build
    if method_id == "extrapolate_fitted":
        from insar_agent.engines import predict
        return predict.build
```

### 2.5 扩展侧(`APPEND_SYSTEM.md` + `SKILL.md`)

```markdown
## 预测红线
- 用户问"未来会怎样/还会沉多少" → 分析 run 第 27 步 extrapolate_fitted,
  绝不口算外推。回答必须转述:置信区间、有效期、"若当前趋势延续"前提。
- 用户问"会不会地震/滑坡什么时候滑" → 如实说明 InSAR 单一手段不做事件预报,
  可提供的是形变趋势外推与加速识别,并给出第 26 步加速检测入口。
- 模拟 run 的预测请求被拒绝是正确行为,如实转达。
```

## 3. 测试(`tests/test_predict.py` 新建)

预测引擎是纯函数核(拟合/外推/置信带与 I/O 分离),可以用**数学自洽**测试而不造假科学数据:

```python
def test_linear_extrapolation_recovers_known_slope():
    """对解析构造的直线+噪声,外推值与解析解一致(数学正确性,非科学样本)。"""

def test_horizon_cap_enforced():
    """span=1yr, horizon=2yr, 无 override_reason → 必须失败。"""

def test_step_function_not_repeated_in_future():
    """含 step 的模型,外推段导数不含阶跃 —— 断言未来无新跳变。"""

def test_prediction_json_carries_assumptions_and_ci():
    """assumptions / not_a_forecast_of / ci 三字段缺一 → 不产出文件。"""

def test_simulated_run_refused():
```

> 用解析构造的合成序列测**数学**(斜率恢复、置信带覆盖率)不违反"禁止假数据"——它测的是算法正确性,不冒充科学样本、不进任何账本;这与 `01-execution-rules.md` §纪律 2 的"测试基建惯例"同一口径。**科学验收只认 realtest 真实时序**(见 §4)。

## 4. 验收

```powershell
.venv\Scripts\python.exe -m pytest tests/test_predict.py -q
.venv\Scripts\python.exe -m pytest -q; cd pi-insar; npx vitest run
```

真实验收(轻计算,无需批准):对 realtest 真实 run 建分析 run:26 步 `quadratic_accel` + 27 步 `extrapolate_fitted horizon_years=0.02`(约 7 天,观测 66 天,合规)。期望:
- `change_summary.json` 的加速度显著像元占比是真实计算值;
- `prediction.json` 有 CI 与 assumptions;`prediction.png` 虚线段带阴影置信带、标题含 extrapolation 字样;
- 在 pi 里问"若趋势延续,一周后该点还会动多少"——模型走分析 run,回答带区间与前提,数字与 JSON 一致。

## 5. Git

```powershell
git add src/insar_agent/registry/capabilities.py src/insar_agent/engines/predict.py `
        src/insar_agent/engines/mintpy_post.py src/insar_agent/engines/__init__.py `
        pi-insar/APPEND_SYSTEM.md pi-insar/skills/00-insar-agent/SKILL.md `
        tests/test_predict.py tests/test_registry_groups.py
git commit -m "feat(analysis): 变化检测 + 有纪律的外推预测(置信区间/有效期/假设强制)

预测是最易造假的能力:只外推已落账的时间函数,不确定度与有效期强制,
阶跃不外推,黑箱一律拒绝;模拟 run 拒绝预测。数学正确性用解析序列守护。"
```

## 6. 完成标记

- [ ] 外推只用第 9 步已落账的时间函数族;模型参数可回账本溯源
- [ ] 有效期上限强制,超限必须显式 override + 理由进账本
- [ ] prediction.json 三要素(CI/假设/免责闭集)缺一不产出
- [ ] 加速检测显著性判据(|a|/σ_a≥2)写进 change_summary
- [ ] realtest 真实时序上外推 7 天,pi 里问答数字与 JSON 一致
- [ ] 分析规划断言更新为 [20..27]

→ 下一个文件:`34-phase14-inversion-bridge.md`
