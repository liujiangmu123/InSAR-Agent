---
name: 22-velocity-correct
description: 当规划或诊断分析链第 22 步速度场校正(ITRF 刚性板块运动改正或默认透传)时使用:默认 passthrough;选 plate_motion_itrf 时必填板块名并备齐几何文件。本步不是 GNSS 平差,不要编造未实现的参考框架约束。
capability: 22
version: "1.0.0"
applies_to: all
---

# 速度场校正(板块运动 / 透传)

第 22 步在掩膜后的速度场上做**可选**的参考框架改正。方法 id:`passthrough`
(默认、推荐,引擎 `-`)、`plate_motion_itrf`(引擎 mintpy,label
`plate_motion.py`)。产物 `corrected`(`analysis/corrected.h5`)与可选
`corrected_2`。输入 `masked`。依赖第 21 步。

**本步不做 GNSS 平差、不估欧拉极、不把 InSAR 约束到 IGS 站网。** GNSS 对比
在核心第 11 步 `gnss_compare`(CSV 与 LOS 速度最近像元 RMSE),同样不自造平差。
未实现的「GNSS 约束速度场」不要写进计划。

## 适用判据

- **`passthrough`(默认且推荐)**:局地相对形变(沉降漏斗、滑坡、矿区、相对参考点
  的同震位移)不需要全球框架。分析链多数场景应保持透传:`masked.h5` →
  `corrected.h5`(有次源则双侧)。
- **`plate_motion_itrf`**:要把 LOS 速度放到 ITRF 类全球框架(与 GNSS ITRF 速度
  对比、跨轨道拼接长波长)。实现是 MintPy `plate_motion.py`(ITRF2014-PMM
  刚性板块),**不是**弹性块模型,也不是 GNSS 网平差。双源时对
  primary/secondary 各自按其几何改正。
- **不要选的**:没有 `geometryRadar.h5` / `geometryGeo.h5` 却想「扣掉板块」——
  实现拒绝跳过改正装作做了。也不存在 ISCE3 轨道/框架工具作为本步方法。

## 参数启发式

- **`plate`**(science,默认 `""`,enum 空串 + ITRF2014-PMM 闭集):
  Antartica / Arabia / Australia / Eurasia / India / Nazca / NorthAmerica /
  Nubia / Pacific / SouthAmerica / Somalia。**拼写 `Antartica` 对齐 MintPy
  上游,不要改成 Antarctica。** `passthrough` 忽略本参数;选
  `plate_motion_itrf` 时必填,否则 build 期 ValueError。
- 板块怎么选:研究区所在刚性板块(中国大陆常用 Eurasia;加州常用 NorthAmerica;
  日本常用 Pacific 或 Eurasia 视边界而定)。选错板块会留下长波长斜坡,看起来
  像「没做大气/轨道」,其实是框架。
- 几何文件:优先 `mintpy/inputs/geometryRadar.h5`,否则 `geometryGeo.h5`。
  分析 run 工作区经常没有它们 → 从源核心 run 拷入 `mintpy/inputs/` 再跑。

## 常见失败与处置

1. **`plate_motion_itrf 要求 params.plate 必填`** → 改了方法忘填板块 → 从闭集
   选一个;不确定就改回 passthrough,不要猜。
2. **未知板块** → 拼写不在闭集(含把 Antartica 改成 Antarctica)→ 按上游拼写。
3. **板块运动改正需要几何文件…未找到** → 分析工作区无 geometry*.h5 → 拷入后
   重跑;没有几何就不要选本方法。
4. **期望 GNSS 约束 / Helmert 七参数 / 区域滤波去板块** → 未实现。第 11 步
   `gnss_compare` 只算 RMSE,不回写速度场。
5. **误读默认** → 默认透传,速度场参考框架与第 20 步源相同;报告不要写
   「已扣除 ITRF 板块运动」除非方法真是 `plate_motion_itrf` 且 exit 0。

## QA 依据

- run_ok:`exit_code == 0` + `artifact_exists(corrected)`。`corrected_2` 非必填。
- passthrough:与 `masked.h5` 字节一致。
- plate_motion:MintPy 在速度场上扣刚性板块 LOS 投影;改正后相对 GNSS ITRF
  的长波长偏差应下降,局地梯度应基本不变(刚性旋转不是漏斗形状的来源)。
- 没有质量门阈值;框架对错靠板块名与几何,不靠 corr_threshold。

## 参考文献

- Altamimi Z., Métivier L., Collilieux X. (2017). ITRF2014 plate motion model.
  *Geophys. J. Int.* 209(3):1906-1912. doi:10.1093/gji/ggx136(PMM;MintPy
  plate_motion 的上游模型)
- MintPy CLI:`python -m mintpy.cli.plate_motion --help`
- 本仓库:engines/mintpy_post.py(`_ITRF_PLATES`,`_find_geometry` 缺文件即失败);
  第 11 步 GNSS 对比见 engines/gnss.py(不自造平差)
- capabilities.py 注释:用全球参考框架时必须做;局地相对形变可不做
