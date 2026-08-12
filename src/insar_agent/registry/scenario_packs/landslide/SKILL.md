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

## 领域知识 · 参数依据(2026-08 调研)

来源:reference/RESEARCH-insar-params-2026-08-12.md(引用均已按一手来源核对)。

- **PS 数据量门槛**:PS 候选按振幅离散度 D_A ≤ 0.25 初选(Ferretti et al. 2001,
  IEEE TGRS 39(1):8-20 §III);可靠估计需 ≥20–25 景(PSI 综述,
  doi:10.1007/s40534-016-0108-4)—— 与本包「PS 需要长时序(通常 ≥20 景)」的数据要求一致。
- **精度预期**:PS 速度精度 <1 mm/yr,最优 0.1–0.5 mm/yr(Ferretti et al. 2001;罗马
  70 景案例后验 0.25 mm/yr);蠕滑滑坡 mm–cm/yr 量级在 PSI 适用域内(Colesanti & Wasowski
  2006, Engineering Geology 88:173-199 —— 滑坡 InSAR 适用性的标准引文)。
- **几何可见性**:LOS 对近南北向运动几乎不敏感,坡向/坡度相对 LOS 的几何决定可测性,
  升降轨互补是 PSI 滑坡应用的普遍做法(Colesanti & Wasowski 2006)——
  与本包「LOS → 坡向投影假设必须进报告」的纪律互为表里。
- **滤波**:高相干裸岩点目标区 Goldstein alpha 取低值(0.2–0.5)防过滤
  (Baran et al. 2003, IEEE TGRS 的 α=1−γ̄ 自适应原则;HyP3 官方指导 α>0.2);
  PS 链本身不滤波(滤波破坏点目标相位),此条仅适用于 SBAS 对照链。
- **双链交叉验证预期**:PSI 与 SBAS 在同一滑坡给出系统性不同的速度带是常态(Stigliano
  案例:PS 5–25 vs SBAS 5–15 mm/yr,doi:10.1080/19475705.2018.1549113);互检惯例用
  一对一速度差 std 表述:多处理器互检 0.5–1.1 mm/yr(Terrafirma;RSE 256:112306, 2021)。
  crossval 门的 0.85 相关阈值属本地标定,无文献先例(contract 台账维持 PENDING)。
- **加速识别**:线性拟合残差系统性增大是临滑前兆信号,应报警而非改用高阶模型拟合掉
  (维持本包既有纪律;残差历元剔除用 3×MAD 惯例,Yunjun et al. 2019 §4.9)。
