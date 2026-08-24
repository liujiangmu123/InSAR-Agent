---
name: lt1_gamma
description: 陆探一号(LT-1)国产 L 波段编队、GAMMA/SARscape 导出后走 MintPy prep_gamma 的接入场景(数据待获取)。SBAS 链 + linear;第 7 步 processor=gamma。适配「陆探/LT-1/LT1/国产L波段/gamma布局」类请求。priority=35,排在滑坡之后,避免抢滑坡;含「沉降」时仍归 subsidence(25)。
metadata:
  version: 1.0.0
  priority: 35
  label: 陆探一号(GAMMA 布局)
  match: '陆探|LT-1|LT1|国产L波段|gamma布局'
  chain: SBAS
  model: linear
  pick_7: mintpy_sbas
  diag: 国产 L 波段编队产品经 GAMMA/SARscape 导出后,按 MintPy prep_gamma 布局进入第 7 步
  reason: 数据面是 GAMMA 不是 HyP3 → processor=gamma;无事件先验 → linear,不发明阶跃/周期
  data_ready: false
  region: 陆探一号覆盖区(数据待获取)
  dates: 待定
  scenes: 数据待获取
---

# 陆探一号(GAMMA 布局)

## 场景概述

陆探一号 **LT-1 A/B**:中国 L 波段 SAR 编队,2023-12 业务化。重访 **单星 8 天 /
双星 4 天**,分辨率/幅宽约 3 m / 400 km,定位为形变+测图双能力的国产自主可控
星座(领域依据:`pi-insar/docs/plan/52-research-insar-domain.md` §1.1)。

本包描述的是 **GAMMA 布局接入**(MintPy `prep_gamma`),不是 HyP3 GeoTIFF 路线,
也不是滑坡 PS 链。data_ready=false:没有随包分发的 LT-1 数据集,禁止把文献
样例路径写进 overrides。

priority=35,排在 `landslide`(30)之后:「滑坡」归滑坡包;「城市地面沉降」因
「沉降」命中 `subsidence`(25)而先被吃掉 —— 这是规则层先到先得,不是漏配。
本包吃的是「陆探 / LT-1 / GAMMA 布局」这类数据源语句。

「国产L波段」与条带包的「L波段」(`stripmap_coseismic`, priority=5)有子串重叠:
只说「L波段」会归 ALOS 条带链。LT-1 请求请带上 **陆探 / LT-1 / LT1 / gamma布局**。

## 数据从哪来、怎么处理

- **分发**:自然资源卫星遥感云服务平台
  (https://www.sasclouds.com/satellite/chinese/lsar)。
- **干涉处理**:行业常用 **SARscape ≥ 5.7**(官方支持 LT-1)或 **GAMMA**;
  导出干涉对后再交给本仓第 7 步 MintPy(`prep_gamma` 布局)。
- **不要**把 HyP3 的 `*unw_phase_clipped.tif` glob 套到 LT-1 产品上。

## 第 7 步数据面(MintPy prep_gamma 惯例)

场景覆写:`processor = gamma`。解缠/相干 glob **按 MintPy 官方目录示例**,
不是本仓实测路径(本包没有装配好的 LT-1 工作区,禁止把未提供的数据集绝对路径
写进知识正文或 overrides)。

MintPy *Example directory structure* · Gamma 节(GalapagosEnvA2T061 示例,
https://mintpy.readthedocs.io/en/latest/dir_structure/):

```
mintpy.load.processor = gamma
mintpy.load.unwFile   = <stack>/interferograms/*/diff*rlks.unw
mintpy.load.corFile   = <stack>/interferograms/*/*filt*rlks.cor
```

相对 `mintpy/` 工作目录的惯用写法:`../interferograms/*/diff*rlks.unw` 与
`../interferograms/*/*filt*rlks.cor`。几何(DEM / lookup)同节为
`geometry/sim*rlks.rdc.dem` 与 `geometry/sim*rlks.UTM_TO_RDC` —— 随
GAMMA/SARscape 导出而变,导入时按实际文件覆写,本包 overrides **只钉
processor=gamma**,不把未装配数据集的绝对路径写进机器配置。

`prep_gamma.py` 还要求解缠文件名能解析出日期对(`YYYYMMDD-YYYYMMDD` 或
`YYMMDD-YYMMDD`),并在同目录找到 `.par` / `.off`(见 MintPy `prep_gamma.py`)。

## 官方在轨测试精度(文献数字,不是硬质量门)

来源:Zhao et al., *Int. Arch. Photogramm. Remote Sens. Spatial Inf. Sci.*,
XLVIII-1/W2-2023, 1251–1256, 2023
(https://doi.org/10.5194/isprs-archives-xlviii-1-w2-2023-1251-2023;
`52-research-insar-domain.md` §2.4 转述):

| 产品形态 | 官方在轨测试指标 |
|---|---|
| D-InSAR 形变场 | 优于 2.7 mm |
| stacking 速度场 | 8.6 mm/yr |
| MT-InSAR 时序 | 3.7 mm |

这些是 **文献/官方测试数字**,只进知识正文与报告「对照」段落,并标明
ISPRS 2023。**禁止**写进 `contract.yaml` 硬门,也禁止当作本 run 的 qa
实测值。本 run 没有算出的数就写未计算。

## 模型与校正

- 第 9 步 `linear`:无同震/冻融/抽水先验时不发明 `step` / `poly_periodic`。
  若用户明确要监测抽水沉降且未强调 LT-1 布局,规则层会先命中 `subsidence`。
- L 波段电离层延迟不可忽略(§2.2)。`split_spectrum` 需要 ISCE2 stack 的
  分频谱产物,GAMMA 导出布局通常没有 —— **不要**在本包默认打开它;有分频谱
  产物时再 `apply_change`,没有就在报告声明未做电离层改正。
- 无 `cloud_completed`:LT-1 官方形变场若走模式 C(位移产品直接分析)是另一条
  路;本包是 GAMMA 干涉对 → MintPy,不假装 2–6 步已在云端完成。

## 与相邻包的边界

- 滑坡 / landslide / 雅鲁藏布 → `landslide`(30 先试)。LT-1 的设计主场景之一
  是滑坡普查,但规则层按用户用词消解,不按「卫星适合什么」抢包。
- 沉降 / 抽水沉降 → `subsidence`(25)。要走本包请写「陆探/LT-1/GAMMA 布局」
  且不要只靠「沉降」一词。
- ALOS / 条带 / stripmap / 单独「L波段」 → `stripmap_coseismic`(5)。
