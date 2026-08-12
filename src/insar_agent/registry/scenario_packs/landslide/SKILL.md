---
name: landslide
description: 滑坡点状目标场景:雅鲁藏布江高山峡谷区缓慢滑坡监测(数据待获取)。陡坡裸岩高相干点密集 → PS 链(pystamps_ps,需 ISCE2→PyStamps 桥);形变模型 linear(蠕滑准匀速)。适配「滑坡/landslide/雅鲁藏布」类请求。
metadata:
  version: 1.0.0
  priority: 30
  label: 滑坡点状目标
  match: '滑坡|landslide|雅鲁藏布'
  chain: PS
  model: linear
  pick_7: pystamps_ps
  diag: 陡坡地形几何畸变明显,裸岩区高相干点密集
  reason: 高相干点状目标 → PS 链;需 ISCE2→PyStamps 桥
  region: 雅鲁藏布江
  dates: 待定
  scenes: 数据待获取
---

# 滑坡点状目标(雅鲁藏布江)

## 场景概述

高山峡谷区缓慢滑坡(蠕滑)监测。观测目标是坡体上离散的高相干散射体
(裸岩、岩屑坡、人工构筑物),形变沿坡向缓慢累积,量级 mm—cm/年。

## 链选择依据(PS 而非 SBAS)

- 裸岩区存在稳定的点状散射体,PS 技术对点目标的形变精度上限更高;
- 陡坡植被区的面状相干性差,SBAS 的分布式散射体假设在坡面上常不成立;
- PS 链路线:第 7 步 `pystamps_ps`,依赖 ISCE2 → PyStamps 数据桥
  (engines/bridges/isce2_to_pystamps,layout 转换在桥内完成)。

## 几何畸变与数据要求

- 陡坡叠掩(layover)/阴影(shadow)/透视收缩明显:升降轨几何可见性差异大,
  选轨道须结合坡向;HyP3/ISCE2 产品的 lv_theta(入射角栅格)可用于几何可见性掩膜;
- 单一几何只能测得 LOS 分量,换算坡向形变须显式声明投影假设并写进报告;
- 数据待定(data_ready=false):PS 需要长时序(通常 ≥20 景)
  才能可靠估计点目标的相位稳定性,短时序结果不可信。

## 模型设定说明

第 9 步取 `linear`:蠕滑期滑坡以准匀速形变为主,线性速率是标准产品;
若出现加速(临滑前兆),线性拟合残差会系统性增大 —— 这正是质检环节应报警的信号,
不应换用高阶模型把它拟合掉。

## 质量门侧重

- PS 点密度与相位稳定性(振幅离散度)是首要质检对象,点数不足时结论不可外推;
- PS/SBAS 双链交叉验证(crossval_ps_sbas)在两条链都建成后才有意义,
  当前按可行性收窄诚实降级;
- LOS → 坡向投影假设必须进报告(narrate 生成方法章节时引用本节)。
