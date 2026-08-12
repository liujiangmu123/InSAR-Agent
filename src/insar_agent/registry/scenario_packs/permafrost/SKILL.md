---
name: permafrost
description: 冻土季节冻融场景:青海玉树多年冻土区季节性冻胀/融沉监测(数据待获取)。低相干面状形变 → SBAS 链;形变含年/半年周期 → poly_periodic 形变模型(periods=[1, 0.5], poly_order=1)。适配「冻土/permafrost/玉树/青海/青藏」类请求。
metadata:
  version: 1.0.0
  priority: 20
  label: 冻土季节冻融
  match: '冻土|permafrost|玉树|青海|青藏'
  chain: SBAS
  model: poly_periodic
  pick_7: mintpy_sbas
  diag: 冻土区植被与季节冻融导致时间去相干严重,点状 PS 目标稀疏
  reason: 低相干面状形变 → SBAS 优于 PS;形变含季节冻融 → poly_periodic 模型
  region: 青海玉树
  dates: '2020-01 — 2023-12'
  scenes: 数据待获取
---

# 冻土季节冻融(青海玉树)

## 场景概述

青藏高原多年冻土区的活动层随季节冻结/融化,地表呈现年周期的冻胀(冬)与融沉(夏),
并叠加多年冻土退化带来的长期沉降趋势。信号量级通常在 mm—cm/年,
远小于同震场景,对大气校正与时序建模的要求更高。

## 链选择依据(SBAS 而非 PS)

- 高寒草甸植被覆盖 + 冻融循环 → 时间去相干严重,人工地物稀少,PS 点密度不足;
- 形变呈面状分布(分布式散射体),SBAS 小基线网络在低相干区更稳健;
- 时间基线越长去相干越强:max_temporal_baseline 应保守取值
  (Sentinel-1 重访 12 天,网络优先保留短时间基线对;跨冻结/融化季的干涉对相干性显著下降)。

## 模型设定说明

第 9 步 `poly_periodic(periods=[1, 0.5], poly_order=1)`:

- `periods=[1, 0.5]`(年 + 半年周期):匹配季节冻融机理 —— 冻胀/融沉的年循环
  并非严格正弦,半年项吸收其非对称性;
- `poly_order=1`(线性趋势项):承载多年冻土退化的长期沉降背景;
- 纯 `linear` 会把季节项当噪声抹掉,纯周期模型无趋势项则丢失退化信号,均与机理不符。

## 质量门侧重

- 低相干是本场景的首要风险:解缠覆盖率(unwrap_coverage)与相干性掩膜是重点检查对象;
- 高原大气延迟与地形强相关,第 8 步 ERA5 对流层校正不可省;无 CDS 凭据且无缓存时
  降级 `tropo_height_corr` 须在报告中声明证据级别下降;
- 周期项可靠拟合需要 ≥2 个完整年周期的数据跨度(本场景取 2020-01 — 2023-12 即为此设计)。

## 数据要求

数据待获取(data_ready=false):Sentinel-1,玉树区域,2020-01 — 2023-12;
获取后走 `asf_search_slc` 下载原始 SLC,或 HyP3 云端路线(跳过 2-6 步,失去中间产物控制权)。

## 领域知识 · 参数依据(2026-08 调研)

来源:reference/RESEARCH-insar-params-2026-08-12.md(引用均已按一手来源核对)。

- **形变模型标准式**:一阶社区默认 d(t)=a·t+b·sin(2πt/T+φ₀)+c,T = 1 年(Li et al. 2019,
  Remote Sens. 11(9):1000 青藏高原 S1 案例;Heihe 综述式(3),doi:10.1029/2022JF006782)。
  Daout et al. (2017, GRL) 用 8 年数据证明冬季冻结期无形变、季节循环非正弦不对称 ——
  半年项(periods=[1, 0.5])正是吸收该不对称的傅里叶二阶项;更物理的替代是 Stefan
  度日模型(形变 ∝ √累计融化度日,Liu et al. 2012, JGR)。
- **量级预期**:青藏高原冻融峰-峰季节位移常见 40–80 mm(Li et al. 2019),长期退化沉降
  mm–cm/yr —— 远小于单景大气扰动(Zebker et al. 1997, JGR:相对湿度 20% 变化 ≈10 cm
  形变误差),故大气校正不可省。
- **大气校正依据**:高原分层延迟与地形强相关,会伪装成与地形相关的冻融信号;GACOS 校正后
  InSAR−GNSS 速度差 STD 2.4→1.9 mm/yr(Morishita et al. 2020 §3.4);ERA-I 在青藏昆仑
  平均削减 APS 73%(Jolivet et al. 2011, GRL)。GACOS 产品自带可行性指标,应先查指标再
  决定采用(Yu et al. 2018, JGR 123:9202-9222)。经验高程相关校正(tropo_height_corr)
  无法区分与地形相关的真实冻融形变,降级时必须在报告声明(MintPy cfg §8 注释;
  Yunjun et al. 2019 §4.6)。
- **网络设计(时间基线收紧)**:contract 台账的 max_temporal_baseline = 120 天是宽松侧
  全局门(Yunjun et al. 2019 §6.3 主张宽松阈值+冗余网络);本场景跨冻结/融化季的干涉对
  相干性系统性下降,应收紧至 S1 惯例区间 24–90 天(同数据源对照:ASF Ridgecrest 教程
  24 天、GMTSAR S1 教程 50 天)并优先序贯短时基线对,同时保证冗余 —— 闭合修正能力随
  冗余上升:序贯 3/5/10 连接可完全修正的错误干涉图占比上限 5/20/35%(Yunjun et al. 2019
  结论 2)。
- **数据跨度**:周期项可靠估计需 ≥2 个完整年循环;观测 <14 个月时线性趋势与季节项
  不可分离(Li et al. 2019)—— 本场景 2020-01 — 2023-12 的 4 年设计满足要求。
- **质检侧重**:解缠覆盖率门参照 LiCSBAS unw_cov_thre=0.3 下限惯例(Morishita et al.
  2020 §2.4.1;本项目 contract 取 0.70 从严,待实测标定);低相干区时序反演后按时间相干
  ≥0.7 掩膜(Pepe & Lanari 2006, IEEE TGRS;MintPy minTempCoh 默认),掩膜后可靠像元数
  ≥100(MintPy minNumPixel 默认)。
