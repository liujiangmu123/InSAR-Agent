---
name: unwrap-method-selection
description: 规划第 6 步(解缠)需要在候选方法间做选择、或该步失败需要分诊时读取:按相干性水平、形变梯度与场景链路给出方法与参数启发式,以及常见失败的闭集处置路径。
capability: 6
version: 1.0.0
applies_to: all
---

# 解缠方法选择

## 适用判据

- 第 6 步「解缠」有多个可行候选(snaphu_mcf / snaphu_smooth / icu / 3D_FULL /
  isce2_stripmap_unwrap_snaphu),需要按数据形态而非默认推荐做决策时。
- 第 6 步失败进入分诊,日志出现解缠特有的失败形态(见「常见失败与处置」)时。
- 上游输入是第 5 步滤波后的缠绕干涉图(ifg_filt);若走 HyP3 云端路线,
  本步已被标 skipped,本技能不适用。

## 参数启发式

方法选择(按候选收窄后的可行集判断):

- **snaphu_mcf**(默认推荐):统计代价 + 最小费用流,低相干区最稳健,输出
  .unw + .conncomp 与 MintPy 原生兼容。相干性中位数 < 0.5 或空间相干性
  分布未知时,一律首选。
- **snaphu_smooth**:平滑代价函数,高相干、缓变形变场上残差更小,但 cost
  function 需人工调优 —— 只有明确需要更高精度且有人力交互调参时才选。
- **icu**:区域增长法,只适合高相干(中位数 > 0.7)的紧凑小区域;大范围
  低相干场景会产生解缠孤岛(conncomp 碎片化),SBAS 网络里应避免。
- **3D_FULL**:需独立 3D 解缠工具链且输出格式与 MintPy 下游不兼容,常规
  时序链不选;仅在时空一致性解缠是硬需求时进入人工评审。
- **isce2_stripmap_unwrap_snaphu**(仅 stripmap_coseismic 场景):snaphu 由
  stripmapApp 内置驱动,不需要独立 snaphu 可执行;分段必须从
  filter_low_band 续起补齐 pickle 链,直接从 unwrap 起步必崩
  (docs/VALIDATION-isce2-wsl.md 实测教训)。

参数启发式:

- **cost_mode**:同震/快速形变(条纹密、梯度大)用 DEFO;缓慢面状形变
  (冻土季节性、缓慢沉降)用 SMOOTH(默认);地形残差主导(DEM 误差大、
  基线长)用 TOPO。
- **min_coherence**:默认 0.25(literature,见「QA 依据」)。植被/冻土等
  低相干区可降到 0.20,但必须同时确认第 4 步多视数足够(range_looks ≥ 10),
  否则解缠噪声主导;调高到 0.30 以上会牺牲覆盖率,注意 unwrap_coverage
  质量门(0.70)的边际。
- **threads**:snaphu 主要受内存带宽限制,超过 8 线程收益极小;大幅图
  优先考虑上游加多视而不是加线程。

## 常见失败与处置

- **内存耗尽**(日志 `Out of memory` / `Killed`,闭集 oom):优先回第 4 步
  加大多视比(range_looks × azimuth_looks)缩小像元数,其次降 threads;
  处置矩阵允许降并行度自动重试一次。
- **解缠孤岛 / conncomp 碎片化**(覆盖率骤降、conncomp 数量异常多):
  典型于 icu 或低相干区 —— 换 snaphu_mcf;仍碎片化则回第 5 步提高
  Goldstein alpha(0.4 → 0.6)增强滤波后重跑本步。
- **unwrap_coverage 低于门限**(质量门 warning/拦停,闭集 data_quality):
  先看相干性直方图判断是数据问题还是参数问题;参数侧依次尝试:降
  min_coherence(0.25 → 0.20)、cost_mode 换 DEFO(强梯度被 SMOOTH 罚没)、
  回第 4 步剪枝时间基线过长的干涉对。
- **超时**(闭集 timeout):大幅图 + 低多视的组合最常见;确认任务规模后
  回第 4 步加多视,而不是盲目调大 capability 超时声明。
- **条带链从 unwrap 直接起步崩溃**(stripmapApp 恢复空状态,闭集
  contract_broken):前驱 filter_high_band 占位步 pickle 缺失所致 ——
  从 filter_low_band 续起,见方法说明;这不是计算失败,不要换方法。
- **输出 .unw 存在但全 NaN**(闭集 data_quality):多为上游 ifg_filt 已
  损坏或掩膜全遮 —— 回第 5 步核查产物指纹,不要在本步反复重试。

## QA 依据

- **unwrap_coverage ≥ 0.70**(audit/contract.yaml,source=local_calibration,
  status=PENDING):本项目从严的工程门,文献参照系是 LiCSBAS 图级剔除线
  unw_cov_thre=0.3(Morishita et al. 2020 §2.4.1);PENDING 期间只出
  warning 不硬拦停,实测标定一次后转 OK。
- **min_coherence = 0.25**(source=literature,status=OK):Berardino et al.
  (2002) §V 固定 0.25,同值为 GIAnT/MintPy 社区惯例(转引 Yunjun et al.
  2019 §6.5)。
- 解缠正确性的交叉证据在第 11 步:loop_closure 闭合环残差只验解缠不验
  反演,是本步结果最直接的独立复核。

## 参考文献

- Chen, C. W. & Zebker, H. A. (2001). Two-dimensional phase unwrapping with
  use of statistical models for cost functions in nonlinear optimization.
  JOSA A, 18(2), 338-351.(SNAPHU 统计代价框架)
- Chen, C. W. & Zebker, H. A. (2002). Phase unwrapping for large SAR
  interferograms: statistical segmentation and generalized network models.
  IEEE TGRS, 40(8), 1709-1719.(SNAPHU MCF/网络流)
- Goldstein, R. M., Zebker, H. A. & Werner, C. L. (1988). Satellite radar
  interferometry: two-dimensional phase unwrapping. Radio Science, 23(4),
  713-720.(枝切法与解缠问题定义)
- Berardino, P. et al. (2002). A new algorithm for surface deformation
  monitoring based on small baseline differential SAR interferograms.
  IEEE TGRS, 40(11), 2375-2383.(SBAS;相干阈值 0.25 出处)
- Morishita, Y. et al. (2020). LiCSBAS: An open-source InSAR time series
  analysis package. Remote Sensing, 12(3), 424.(unw_cov_thre 社区下限)
- Yunjun, Z., Fattahi, H. & Amelung, F. (2019). Small baseline InSAR time
  series analysis: Unwrapping error correction and noise reduction.
  Computers & Geosciences, 133, 104331.(MintPy;解缠误差校正)
