---
name: 09-deformation-model
description: 当规划或诊断第 9 步形变模型(time function 拟合、velocity 估计)时使用:在 linear / step / poly_periodic / exponential 之间按机理选模型、设定 poly_order / periods / step_date、诊断 velocity.h5 全 NaN、阶跃残余与周期项不可分离等问题。
capability: 9
version: "1.0.0"
applies_to: all
---

# 形变模型(时间函数拟合与速度场)

第 9 步把校正后时序拟合为参数化时间函数(time function),产出速度场。方法 id:
`linear`(默认、推荐)、`step`(仅 `quake` 场景,label `step(date)`)、
`poly_periodic`(仅 `permafrost` 场景,label `poly_periodic(1,[1,0.5])`)、
`exponential`;产物 artifact id 为 `velocity`(首候选 `mintpy/velocity.h5`)。
真实运行口径:`smallbaselineApp insar_agent.cfg --start velocity --end velocity`
(engines/mintpy.py `_RANGES[9]`,即 MintPy timeseries2velocity);cfg 渲染
(render_cfg):`mintpy.timeFunc.polynomial = poly_order`、`mintpy.timeFunc.periodic
= periods`(**仅当方法为 `poly_periodic` 时渲染,否则 auto**)、
`mintpy.timeFunc.stepDate = step_date`(**仅当方法为 `step` 时渲染**)。
理论框架:Hetland et al. (2012) MInTS 时间函数族。

## 适用判据

模型形态必须匹配形变机理,按场景对照:

- **`linear`(仅线性趋势,默认)**:蠕滑滑坡(`landslide` 场景的 model ——
  蠕滑期准匀速,线性速率是标准产品)、震间应变积累、一般性沉降;也是
  `stripmap_coseismic` 的 model —— 单干涉对仅两景,时序上阶跃与线性不可辨识,
  linear 是唯一可辨识参数化(场景包 reason 原文)。
- **`step`(同震阶跃,scenario_only=quake)**:同震位错是以发震时刻为界的
  Heaviside 阶跃。`quake` 场景 overrides 固化 `step_date: '20190706T0320'`
  (Ridgecrest 主震 2019-07-06 03:19:53 UTC,取整到分钟)。用 `linear` 拟合
  阶跃会摊成假趋势 —— 这正是 MintPy timeseries2velocity 支持 stepDate 的动机
  (smallbaselineApp.cfg §12 注释)。
- **`poly_periodic`(线性 + 年周期 + 半年周期,scenario_only=permafrost)**:
  季节冻融机理 —— 冻胀/融沉年循环叠加多年冻土退化的长期趋势。半年项吸收冻融
  循环的非正弦不对称(Daout et al. 2017 用 8 年数据证明冬季冻结期无形变、循环
  不对称);纯 `linear` 把季节项当噪声抹掉,纯周期无趋势项丢失退化信号。
- **`exponential`(衰减形变)**:震后黏弹性松弛(postseismic viscoelastic
  relaxation)、矿区采空衰减。注意余滑(afterslip)机理对应对数形
  d(t)=A·ln(1+t/τ)(Marone et al. 1991;Ingleby & Wright 2017 全球 151 例证实
  Omori 型衰减);Mw≥7 大震常用对数+指数组合式(Tobita 2016)。本步骤未声明
  log 参数 —— 需要对数/组合模型时走 fork_run 分支试探(MintPy 模板键语法
  `mintpy.timeFunc.log = 20190706,60`),不覆写本 run。

## 参数启发式

本步骤在 registry(capabilities.py id=9)声明的参数只有三个:

- **`poly_order`**(science,默认 `1`,范围 0–3,渲染为
  `mintpy.timeFunc.polynomial`):`1` 是标准速度产品;`0` 仅截距(配合周期/阶跃
  项做无趋势拟合);`2-3` 慎用 —— 高阶多项式会把未建模信号(震后衰减、加速
  蠕滑)拟合掉。滑坡加速识别纪律(landslide 场景包):临滑前兆表现为**线性拟合
  残差系统性增大,应报警而非升阶拟合掉**。
- **`periods`**(science,默认 `[1, 0.5]`,单位年,仅 `poly_periodic` 方法消费):
  年 + 半年是社区一阶默认(d(t)=a·t+b·sin(2πt/T+φ₀)+c,T=1 年,Li et al. 2019;
  半年项为吸收非对称的傅里叶二阶项,Daout et al. 2017)。可靠拟合的数据跨度
  门槛:**≥ 2 个完整年循环;观测 < 14 个月时线性趋势与季节项不可分离**
  (Li et al. 2019)—— 跨度不足时不要报告季节振幅,只报趋势并声明耦合。
- **`step_date`**(science,默认 `""`,仅 `step` 方法消费):格式 `YYYYMMDD` 或
  `YYYYMMDDTHHMM`(UTC),取权威发震时刻(USGS)。空值时 cfg 渲染为 auto,
  阶跃不生效 —— `quake` 场景由 overrides.yaml 固化,手工规划时必须显式传参。
  该值同时被第 8 步复用为 `mintpy.topographicResidual.stepFuncDate`(DEM 误差
  校正共用同震拐点,render_cfg 自动带过去,无需重复声明)。
- **模型选择的代价意识**:选错形态不是"拟合差一点"而是**系统性偏差** ——
  linear 拟合阶跃产生假趋势;poly_periodic 用于同震机理不符;对数与指数在短
  观测窗内近似不可辨识,τ 与振幅强相关 —— 固定 τ 网格搜索、报告拟合残差而非
  断言机理(Sobrero et al. 2020;Tobita 2016)。
- 不确定度口径:MintPy 默认 residue 法(Fattahi & Amelung 2015 不确定度传播),
  bootstrap 备选(默认 400 次)—— 报告须写明所用口径。

## 常见失败与处置

1. **velocity.h5 全 NaN,run_ok 的 `not_all_nan(velocity)` 失败** → 上游
   `timeseries_corrected` 掩膜后有效像元不足(时间相干掩膜过严/解缠覆盖差)→
   回查第 7 步网络与掩膜、第 6/8 步覆盖率指标;不要在本步"放宽"任何东西 ——
   本步只是拟合,病根在上游。
2. **step 模型拟合后,残差(或速度场)仍呈断层同形态图案** → `step_date` 错误
   (时刻/时区错)或形变含未建模震后项 → 核对 USGS 发震时刻(UTC);残差随时间
   衰减 → fork 一条 `exponential`(或 log 组合)分支对比,不覆写本 run。
3. **周期振幅异常大或与线性趋势强耦合** → 数据跨度 < 2 个完整年循环(< 14 个月
   必然不可分离)→ 报告只给趋势 + 声明不可分离;补数据后重跑,而非调参硬拟合。
4. **`exponential` 拟合不收敛或 τ 发散** → 对数/指数在观测窗内不可辨识
   (Sobrero et al. 2020)→ 固定 τ 做网格搜索比较残差;报告写"残差择优",
   不写机理断言。
5. **速度量级看起来大得离谱** → 单位混淆:`velocity.h5` 数据集单位 m/yr,
   出图与 qa 均 ×1000 转 mm/yr(engines/figures.py / engines/qa.py);或参考点
   落形变区造成整体偏移(回第 7 步重选参考点)。同震参照:Ridgecrest 实测
   vel p2–p98 为 −309~+828 mm/yr —— 阶跃被年化后本来就大,先查机理再怀疑数据。
6. **cfg 相关崩溃(UnicodeDecodeError / MKL 0xC06D007F)** → 与第 7/8 步同源
   (MintPy 共用同一份 `mintpy/insar_agent.cfg`;BLAS 拟合亦走 MKL)→ 处置同
   07/08 技能文档:cfg 纯 ASCII + `PYTHONUTF8=1`;切 OpenBLAS。

## QA 依据

- **run_ok 三判定**(registry 声明):`exit_code == 0`、`artifact_exists
  (velocity)`、`not_all_nan(velocity)`。
- **速度精度参照系**(判断速度场可信度的文献量级):SBAS ≈ 1 mm/yr、时序单历元
  ≈ 5 mm(Casu et al. 2006);PS < 1 mm/yr、最优 0.1–0.5 mm/yr(Ferretti et al.
  2001);EGMS 产品规格速度 STD 0.7 mm/yr;S1 速度 std 收敛到 2 mm/yr 需约
  2.2 年(24 天采样,Morishita et al. 2020 §3.5)—— 短时序不要宣称 mm/yr 精度。
- **与 GNSS 对比量级预期**:强形变场景 InSAR vs GNSS 时序 RMSE 0.5–1.8 cm 是
  "结果可信"的文献参照(Yunjun et al. 2019 §5.1 Fig. 8);震间/平稳区速度差
  STD ≈ 1–2 mm/yr(Morishita 2020;EGMS 验证绝大多数 < 2 mm/yr)。
- **模型残差空间检查**:阶跃拟合残差的空间分布应无断层同形态残余(有则提示
  step_date 或模型形态错误,quake 场景包纪律);滑坡场景线性残差系统性增大是
  临滑报警信号,进 triage 而非改模型。
- **qa.json 联动**(第 11 步重解析本步产物):`vel_p2` / `vel_p98`(速度 2/98
  分位,mm/yr)做量级合理性检查;`velocity_coverage` / `nan_fraction` 对照台账
  `nan_fraction_below = 0.50`(local_calibration,PENDING → 只警告)。

## 参考文献

- Hetland E.A., Musé P., Simons M., Lin Y.N., Agram P.S., DiCaprio C.J. (2012).
  Multiscale InSAR time series (MInTS) analysis of surface deformation. *JGR*
  117:B02404. doi:10.1029/2011JB008731(MintPy timeFunc 时间函数族的理论出处)
- Fattahi H., Amelung F. (2015). InSAR bias and uncertainty due to the
  systematic and stochastic tropospheric delay. *JGR* 120. (速度不确定度传播;
  MintPy residue 法背景)
- Marone C., Scholz C.H., Bilham R. (1991). On the mechanics of earthquake
  afterslip. *JGR* 96(B5):8441-8452. doi:10.1029/91JB00275(余滑对数形)
- Ingleby T., Wright T.J. (2017). Omori-like decay of postseismic velocities
  following continental earthquakes. *GRL* 44. doi:10.1002/2017GL072865
- Tobita M. (2016). Combined logarithmic and exponential function model for
  fitting postseismic GNSS time series after 2011 Tohoku-Oki earthquake.
  *Earth Planets Space* 68:41. doi:10.1186/s40623-016-0422-4(组合式标准形)
- Sobrero F.S., Bevis M., Gómez D.D., Wang F. (2020). Logarithmic and
  exponential transients in GNSS trajectory models as indicators of dominant
  processes in postseismic deformation. *J. Geodesy* 94:84.
  doi:10.1007/s00190-020-01413-4(τ 不可辨识性告诫)
- Daout S., Doin M.-P., Peltzer G., Socquet A., Lasserre C. (2017). Large-scale
  InSAR monitoring of permafrost freeze-thaw cycles on the Tibetan Plateau.
  *GRL* 44. doi:10.1002/2016GL070781(季节循环非正弦不对称;半年项依据)
- Li et al. (2019). Time-series InSAR monitoring of permafrost freeze-thaw
  seasonal displacement over Qinghai-Tibetan Plateau using Sentinel-1 data.
  *Remote Sensing* 11(9):1000. doi:10.3390/rs11091000(峰-峰 40–80 mm;
  < 14 个月不可分离)
- Casu F., Manzo M., Lanari R. (2006). A quantitative assessment of the SBAS
  algorithm performance. *RSE* 102:195-210(SBAS 速度精度 ~1 mm/yr)
- Ferretti A., Prati C., Rocca F. (2001). Permanent scatterers in SAR
  interferometry. *IEEE TGRS* 39(1):8-20(PS 速度精度)
- Yunjun Z., Fattahi H., Amelung F. (2019). *Computers & Geosciences*
  133:104331(§5.1 GNSS RMSE 0.5–1.8 cm;残差历元 3×MAD)
- Morishita Y. et al. (2020). *Remote Sensing* 12(3):424(速度 std 收敛年限;
  InSAR−GNSS 速度差 STD)
- Colesanti C., Wasowski J. (2006). Investigating landslides with space-borne
  SAR interferometry. *Engineering Geology* 88:173-199(蠕滑线性速率与
  加速报警框架)
- MintPy smallbaselineApp.cfg §12(timeFunc 语法与 stepDate 注释):
  https://github.com/insarlab/MintPy/blob/main/src/mintpy/defaults/smallbaselineApp.cfg
