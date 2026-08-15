# 44 · 附录 D — InSAR 产物与术语速查(执行 AI 的领域最小词典)

> 目的:执行 AI 改代码/写测试时不需要猜术语。**科学决策依据仍以 `skills/`(11 步技能)与场景包正文为准**,本表只是索引。

## 1. 核心产物字典(路径为 run 工作区相对路径)

| 产物 | 典型路径 | 是什么 | 单位/约定 |
|---|---|---|---|
| SLC | `data/slc/` | 单视复数影像(原始观测) | 复数;每景 ≈8 GB(C4) |
| DEM | `data/dem/` | 数字高程模型(地形相位去除) | m |
| 干涉图(缠绕) | `data/ifg/`、`isce2/interferogram/topophase.flat` | 两景相位差,含 2π 模糊 | rad,(-π, π] |
| 相干性 | HyP3 `*corr*.tif`、`mintpy/temporalCoherence.h5` | 相位质量 0-1 | 无量纲;质量门键 `min_coherence`=0.25 |
| 解缠相位 | `data/unw/`、`*unw_phase*.tif` | 去 2π 模糊的连续相位 | rad;质量门键 `unwrap_coverage` |
| ifgramStack | `mintpy/inputs/ifgramStack.h5` | 全部干涉对堆栈(时序反演输入) | 含 `date12List`、`bperp` |
| 时序 | `mintpy/timeseries*.h5` | 逐历元累计 LOS 位移 | **m**(工具层换算 mm 时 ×1000) |
| 校正时序 | `timeseries_ERA5_ramp_demErr.h5` 等 | 文件名随校正组合变化(候选全列在注册表) | m |
| 速度场 | `mintpy/velocity.h5` | 时间函数拟合的速率(+`velocityStd` 不确定度) | m/yr(展示 mm/yr ×1000) |
| 掩膜 | `mintpy/maskTempCoh.h5` | 可信像元(时相相干阈值) | 0/1 |
| 几何 | `mintpy/inputs/geometryRadar.h5` / `geometryGeo.h5` | 入射角/方位角/高程(分解与桥的输入) | 度/m |
| 图件 | `products/figures/` + `.json` sidecar | 600 dpi 三档尺寸 + 元数据 | sidecar 是图注事实源 |
| QA 报告 | `products/report/qa.json` | 质检指标(crossval_r 等) | 阈值见 `audit/contract.yaml` |
| 分析产物 | `analysis/`(masked/corrected/decomposed/measure/prediction…) | 分析 run 的规范路径链 | 见 Phase 10 §设计 |

## 2. 术语(一行版)

- **LOS**:雷达视线方向;InSAR 原生观测是 LOS 一维投影,升+降轨才能分解垂直/东西向(南北向对极轨 SAR 几乎盲)。
- **升轨/降轨(asc/desc)**:卫星向北/向南飞的成像几何;两者对同一形变的 LOS 投影不同。
- **多视(looks)**:距离×方位向平均降噪;S1 IW 惯例 rg:az≈5:1(像元 2.3×14.1 m)。
- **时空基线**:两景的垂直距离(m)/时间间隔(天);SBAS 靠短基线网络抑制失相干。
- **SBAS / PS**:小基线集(面状形变、低相干区)/永久散射体(点目标、高相干);本项目第 11 步用双链交叉验证。
- **解缠(unwrapping)**:恢复 2π 模糊;SNAPHU MCF 是默认;解缠误差改正(bridging/phase_closure)在第 7 步区间。
- **大气改正**:对流层(ERA5/GACOS/高程相关/OPERA)+ 电离层(split-spectrum,L 波段重要)。
- **deramp**:去平面/二次趋势;**同震/震后/火山场景 deramp=no 是红线**(长波长形变会被当轨道误差扣掉)。
- **DEM 误差校正**:地形残差与垂直基线相关项(Fattahi & Amelung);`topographicResidual`。
- **板块运动改正**:ITRF 刚性板块速度从速度场中扣除(用全球参考框架对比 GNSS 时必须)。
- **参考点/参考日期**:InSAR 是相对测量,一切数值相对参考点与参考历元;报告必须交代。
- **证据阶梯**:本项目的结论可信度分级(…runnable < done < audited);模拟 run 封顶 runnable。
- **五阶段执行**:每步 恢复→预检→执行→发现产物→验证 的固定生命周期(`runtime/executor.py`)。

## 3. 单位换算约定(工具层统一口径)

| 量 | 存储 | 展示 |
|---|---|---|
| 位移 | m(MintPy `UNIT=m`) | mm(×1000,`/api/timeseries-point` 已换算) |
| 速率 | m/yr | mm/yr(×1000,`report/results.py` 同口径) |
| 相位 | rad | rad(或换算 LOS mm:×λ/4π,S1 C 波段 λ=55.5 mm) |

> 任何新代码涉及单位,先查这里 + `engines/qa.py` 的既有口径,**不要自行发明换算**。
