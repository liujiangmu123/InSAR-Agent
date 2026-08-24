---
name: teaching
description: 教学演示场景:小数据快节奏、图件全开,适合课堂讲清 11 步与可复现纪律(数据待获取)。SBAS 链 + linear;第 11 步诚实降级 coherence_mask(不假装有 PS 双链)。适配「教学/课堂/演示/homework/teaching/tutorial」类请求。priority=90,避免抢走地震/冻土/滑坡等业务命中。
metadata:
  version: 1.0.0
  priority: 90
  label: 教学演示
  match: '教学|课堂|演示|homework|teaching|tutorial'
  chain: SBAS
  model: linear
  pick_7: mintpy_sbas
  diag: 课堂要的是可讲完、可复现、图件齐全,不是研究级覆盖或双链交叉验证
  reason: 小数据快节奏 → SBAS+linear;图件全开便于板书;无 PS 对照链 → coherence_mask
  data_ready: false
  region: 课堂演示(小数据,待指定)
  dates: 待定
  scenes: 小数据快节奏(数据待获取)
---

# 教学演示

## 场景概述

本包给课堂/作业用,不是业务监测包。目标是 **小数据、快节奏、图件全开**:
在一节课里把「意图 → 计划 → 11 步 → 图件/报告 → 复现包」走通,让学生看见
每一步的产物与证据,而不是跑完才丢一张速度图。

priority=90(很低):「冻土教学」「滑坡课堂作业」这类混合句仍归冻土/滑坡等
业务包(它们的 priority 20–30 先试)。只有明确的教学/课堂/homework/tutorial
用语才落到本包。match **不含**单独的 `demo`,避免英文 demo 误伤其它请求。

本包不含现成数据(data_ready=false)。

## 课堂节奏与选参

| 选择 | 值 | 课堂理由 |
|---|---|---|
| 链 | SBAS(`mintpy_sbas`) | 本仓已实测的主链,依赖面比 PS 小,适合当堂演示 |
| 第 9 步 | linear | 无事件先验时不发明阶跃/周期;线性速率最好讲 |
| 第 10 步 figure_set | velocity, coherence, mask, network, points_timeseries | 板书需要速度/相干/掩膜/网络/点时序五张,缺一就只能口头补 |
| 第 11 步 | coherence_mask | 教学默认不假装有 PS 对照链;交叉验证要等双链都建成 |

不要为了「看起来完整」去开 `crossval_ps_sbas`。没有第二条链就没有交叉验证。

## 模式 C:官方位移产品直接分析(零算力)

领域依据:`pi-insar/docs/plan/52-research-insar-domain.md` §1.2 / §4.3。

三种接入里,课堂应优先讲 **模式 C**:OPERA DISP / EGMS / LT-1 全国形变场等
**L3 位移产品**直接进分析链(20–28),不跑 SLC→解缠。价值是 **零重型计算、
分钟级出分析结论**,符合本机算力纪律,也适合「InSAR 能看见什么」的第一课。

诚实边界:模式 C 的 GeoTIFF/CSV 读入若尚未在第 20 步落地,课堂改用已有
h5 样例或 HyP3 小栈(模式 B 的缩小版),**禁止编造位移场或数值**来「把课讲圆」。
模式 A(SLC 全链)只作对照板书,不当堂做重型计算。

## strict / free 双模式(可讲可复现)

- **strict**:科学步骤只走 `insar_*` 闭集,自由 bash 被拦截 —— 用来讲
  「可复现科学计算」:同一计划、同一指纹、同一报告数字。
- **free**:允许排障与探路,但报告数字仍必须来自工具实测。
- 复现包 zip 可直接当作业材料。学生交回来的数字必须对得回 provenance,
  对不上就是编造,判不合格。

## 禁止编造数值

- 速度、时序、相干、RMSE、条纹周数 —— 全部来自产物/qa.json,没有产物就写
  「未计算」,不要用文献典型值冒充本 run 结果。
- 文献量级(例如沉降工程化 ±5 mm)只可出现在「对照/期望」段落,并标明来源。
- 点时序必须用 `points_lalo` 显式给坐标,不要凭记忆挑像素。

## 质量门与出图

- 第 11 步 `coherence_mask`:从产物重算真实指标,最弱但诚实。
- 第 10 步图件全开,缺输入的图种按引擎既有行为跳过并声明,不补假图。
- 无 `cloud_completed`:教学包不假装 HyP3/官方产品已经跑完 2–6 步。

## 与相邻包的边界

- 地震/同震/Ridgecrest → `quake`(10);冻土/青藏 → `permafrost`(20);
  沉降漏斗 → `subsidence`(25);滑坡 → `landslide`(30)。本包不抢。
- 国产 LT-1 / GAMMA 布局导入 → `lt1_gamma`(35),即使出现在作业题里。
