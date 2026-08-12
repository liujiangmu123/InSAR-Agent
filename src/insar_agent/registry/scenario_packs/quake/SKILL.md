---
name: quake
description: 同震形变场景:2019 Ridgecrest Mw 7.1 地震(实测数据就绪,全链路基准场景)。SBAS 链 + step 阶跃形变模型(stepFuncDate=20190706T0320);HyP3 云端产品已含解缠相位,第 2-6 步跳过。适配「地震/同震/quake/Ridgecrest/玛多」类请求。
metadata:
  version: 1.0.0
  priority: 10
  label: 同震形变
  match: '地震|同震|quake|Ridgecrest|玛多'
  chain: SBAS
  model: step
  pick_7: mintpy_sbas
  diag: 同震形变量级大(数十 cm),HyP3 已提供解缠相位与相干性
  reason: 阶跃形变 → step(20190706) 模型;日期取自实测配置
  data_ready: true
  region: Ridgecrest(加州)
  dates: '2019-06-10 — 2019-08-15'
  scenes: 7 个获取日期 / 11 个干涉对
---

# 同震形变(Ridgecrest 2019)

## 场景概述

2019-07-06 加州 Ridgecrest Mw 7.1 主震(07-04 前震 Mw 6.4)。同震形变在时序上表现为
以发震时刻为界的阶跃(step),量级数十 cm,远超大气噪声与季节性信号,是少数
「模型形态先验明确」的场景。本场景数据已就绪(data_ready=true),作为全链路验收的基准场景。

## 数据要求与云端已完成事实(Ridgecrest 实测)

- 数据源:ASF HyP3 INSAR_GAMMA 产品,Sentinel-1 降轨 DT71,
  实测 **7 个获取日期 / 11 个干涉对**(110 文件 / 402 MB,每对 10 文件)。
- HyP3 产品已含解缠相位(`*_unw_phase.tif`)与相干性(`*_corr.tif`)——
  即第 2-6 步(辅助数据 DEM、配准、干涉、滤波、解缠)**已由 ASF 云端完成**:
  `cloud_completed: [2, 3, 4, 5, 6]`,计划时这些步骤标 `skipped`,
  不做本机可行性检查(本机缺 ISCE2/SNAPHU 不阻塞 HyP3 路线),不执行。
- 第 1 步走 `local_import`:HyP3 产品目录同时充当第 7 步时序反演的 unw 输入。

## ERA5 缓存路径约定

- 数据源目录下 `mintpy/inputs/ERA5.h5`(实测约 47.9 MB)是 PyAPS 大气校正的本地缓存;
  `local_import` 会把它复制进工作区同名路径 `<workspace>/mintpy/inputs/ERA5.h5`(小文件,进指纹)。
- 缓存命中时第 8 步 `tropo_era5_pyaps` **免 CDS 凭据**;CDS 凭据是「无缓存时」才需要的
  条件依赖,不做静态硬拦 —— 缓存与凭据都缺时由运行期失败 + triage 诚实呈现,
  可降级 `tropo_height_corr`(证据级别下降,报告中须声明)。

## 选参依据(来源等级对齐 AGENT-DESIGN 阈值台账纪律)

| 参数 | 值 | 来源 |
|---|---|---|
| stepFuncDate(第 9 步 step_date) | 20190706T0320 | A·实测配置 `mintpy/RidgecrestSenDT71.txt`(Ridgecrest 同震时刻,UTC) |
| weightFunc | no | A·实测配置(MintPy 默认为 var,此处是有意覆盖) |
| solid_earth_tides | false | A·对齐实测基准配置未启用 SET;且 conda-forge pysolid 的 Fortran DLL 在 Windows 加载失败,开启前须验证 |

无实测或文献依据的阈值一律不进硬门(status: PENDING 只出 warning)。

## 模型设定说明

第 9 步形变模型取 `step(step_date=20190706T0320)`:同震位错是瞬时阶跃,
`linear` 会把阶跃摊成假趋势,`poly_periodic` 与机理不符。
若需研究震后余滑,应 fork 一条 `exponential` 模型的分支对比(参数试探走 fork_run,
未受影响步骤零重算),而不是覆写本 run。

## 质量门侧重

- 第 11 步覆写为 `coherence_mask`:PS 链未建成前,PS/SBAS 交叉验证(crossval_ps_sbas)
  不可行 —— 诚实降级为真实统计质检,不假装有双链验证。
- 解缠已在云端完成,unwrap_coverage 门不在本机触发;反演后重点看
  timeseries/velocity 的 not_all_nan 检查与阶跃拟合残差的空间分布(断层两侧应反号)。

## 领域知识 · 参数依据(2026-08 调研)

来源:reference/RESEARCH-insar-params-2026-08-12.md(引用均已按一手来源核对)。

- **时间基线**:ASF 官方 HyP3+MintPy Ridgecrest 教程(hyp3-docs,
  hyp3_insar_stack_for_ts_analysis.ipynb)与本场景同数据源同链路,取
  max_temporal_baseline = 24 天;contract 台账的 120 天门(Yunjun et al. 2019 §6.3
  宽松阈值+冗余网络主张)在 HyP3 路线不构成约束 —— 实际选对已由 11 个干涉对产品固化。
- **解缠(云端已完成,复核用)**:HyP3 用 MCF 算法,相干 < 0.1 的像元不参与解缠
  (HyP3 InSAR Product Guide);若本地重做,ISCE2 `snaphu_mcf` = SMOOTH 代价 + MCF 初始化
  + initOnly(runUnwrapSnaphu.py 源码);近场跨断层出现解缠跳变时应改 DEFO 模式
  (snaphu DEFOMAX_CYCLE 默认 1.2 周,官方 man page),而不是换初始化算法。
- **去 ramp 警告**:MintPy 官方注释明确 co-/post-/inter-seismic 等长波长形变不推荐 deramp
  (smallbaselineApp.cfg §9)。同震阶跃属长波长信号,第 8 步 ramp 参数在本场景建议覆写为
  no,避免把形变梯度当轨道误差扣除。
- **震后模型(fork 分支用)**:余滑 → 对数 d(t)=A·ln(1+t/τ)(Marone et al. 1991, JGR
  96(B5):8441-8452;Ingleby & Wright 2017, GRL 全球 151 例证实 Omori 型衰减);黏弹性松弛
  → 指数;Mw≥7 大震常用组合式 d(t)=a·ln(1+t/b)+c(1−e^(−t/τ))+Vt(Tobita 2016, EPS 68:41)。
  MintPy 语法:`mintpy.timeFunc.log = 20190706,60`。对数/指数在短观测窗内近似不可辨识,
  报告拟合残差而非断言机理(Sobrero et al. 2020, J Geodesy 94:84)。
- **质量预期**:强形变场景 InSAR vs GNSS 时序 RMSE 0.5–1.8 cm 是「结果可信」的文献参照
  (Yunjun et al. 2019 §5.1 Fig. 8);阶跃拟合残差的空间分布应无断层同形态残余
  (有则提示阶跃日期/模型形态错误)。
- **闭合环质检**:图级闭合环相位 RMS > 1.5 rad 判问题干涉图(LiCSBAS p12_loop_thre 默认;
  Morishita et al. 2020 §2.4.2);HyP3 产品自带连通分量,可跑 MintPy phase_closure 的
  T_int 指示图(Yunjun et al. 2019 §3.2 式(8)-(9))定位残余解缠误差。
