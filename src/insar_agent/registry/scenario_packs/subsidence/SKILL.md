---
name: subsidence
description: 城市地面沉降场景:建成区抽水/荷载引起的局地漏斗形变(数据待获取)。高相干面状目标 → SBAS 链;短时间基线密网(90 天)+ 局地线性去斜 + 年周期形变模型(periods=[1])。适配「沉降/subsidence/抽水沉降/沉降漏斗」类请求。
metadata:
  version: 1.0.0
  priority: 25
  label: 城市地面沉降
  match: '沉降|subsidence|抽水沉降|沉降漏斗'
  chain: SBAS
  model: poly_periodic
  pick_7: mintpy_sbas
  diag: 城市建成区高相干,形变呈局地漏斗,常叠加地下水开采的年周期
  reason: 局地形变 → ramp=linear 合法;抽水年周期 → poly_periodic(periods=[1]);短基线密网保相干
  region: 城市建成区(数据待获取)
  dates: 待定
  scenes: 数据待获取
---

# 城市地面沉降

## 场景概述

建成区地面沉降是 InSAR 最高频的应用之一:地下水开采、软土固结、地下空间施工
在时序上表现为局地漏斗 + 缓慢累积,量级通常 mm—cm/年,远小于同震阶跃。
信号空间波长短(街区到城区尺度),时间上常带与降水/开采同步的年周期。

本包不含现成数据(data_ready=false):规划后需先 `local_import` / HyP3 产品或
SLC 全链接入真实干涉对,再跑第 7-11 步。

## 选参依据

| 参数 | 值 | 理由 |
|---|---|---|
| max_temporal_baseline(第 7 步) | 90 天 | 城市高相干,短基线密网即可;比全局门 120 天更紧,减少季节去相干对进入网络 |
| ramp(第 8 步) | linear | 局地漏斗不是长波长构造信号。MintPy 官方注释把线性去斜留给沉降/矿区/滑坡等局地形变(smallbaselineApp.cfg §9;capabilities C1 的例外) |
| 第 9 步 method | poly_periodic | 抽水型沉降常带年周期(旱季开采、雨季回弹),纯 linear 会把季节项摊进速率 |
| periods | [1] | 只要年周期。冻土包的半年项([1, 0.5])吸收冻融不对称,沉降机理不需要 |

时间基线 90 天仍落在 S1 惯例区间(ASF Ridgecrest 教程 24 天、GMTSAR S1 教程 50 天、
本仓全局门 120 天之间的收紧侧),城市像元相干高,密网闭合修正能力足够。

## 质量门侧重

- **参考点必须落在稳定基岩或已知稳定区**(远离漏斗中心与开采井)。参考点若在漏斗里,
  整场速率会平移,漏斗形态还在但绝对值不可用 —— 报告必须写参考点坐标与选取依据。
- 城市高相干不代表解缠无误:建筑物叠掩、交通干线失相干条带仍可能造成 2π 跳变,
  第 11 步看闭合环与速率场是否沿道路/轨道呈假条纹。
- 年周期项可靠估计需要 ≥2 个完整年的跨度;短于约 14 个月时线性趋势与季节项不可分离,
  应在报告声明,不要把周期振幅写成开采量。

## 交付侧重

漏斗范围(速度场 + 掩膜)+ 代表性点位时序曲线 + GeoTIFF。第 10 步 `figure_set`
声明 velocity / coherence / mask / points_timeseries;曲线点位用 `points_lalo`
显式给出,不要凭记忆挑像素。

## 与相邻包的边界

- 冻土融沉也含「沉降」口语,但冻土包 match 是 `冻土|permafrost|玉树|青海|青藏`,
  且 priority=20 先于本包(25):「冻土融沉」归冻土,本包不抢。
- 滑坡是点状 PS 链,match 不含「沉降」。
- 火山长波长充放归 volcano 包,本包的 linear deramp 在火山场景是红线(见 volcano 包)。
