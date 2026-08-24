---
name: 21-mask-subset
description: 当规划或诊断分析链第 21 步掩膜子集(时间相干掩膜、研究区裁剪、或默认透传)时使用:在 mask_by_coherence / subset_lalo / passthrough 之间选择,默认是 passthrough,不要把未改方法的分析 run 当成已经掩膜。
capability: 21
version: "1.0.0"
applies_to: all
---

# 掩膜子集(时间相干掩膜 / 裁剪 / 透传)

第 21 步对第 20 步规范源做可信像元筛选或空间裁剪。方法 id:`mask_by_coherence`
(推荐,引擎 mintpy,label `mask.py --mask maskTempCoh.h5`)、`subset_lalo`
(引擎 mintpy)、`passthrough`(引擎 `-`)。**`default_method=passthrough`**:
分析链可选,未显式改方法时硬链 `analysis/source.h5` → `analysis/masked.h5`
(有次源则 `source_2.h5` → `masked_2.h5`),不做科学掩膜。产物 `masked`
(`analysis/masked.h5`)。输入 `src_primary`。依赖第 20 步。

## 适用判据

- **`passthrough`(默认)**:源已经是研究区子集、或后续步骤自己会掩、或本次
  分析只想透传到 22+。多数分析 run 应保持默认,避免无掩膜文件时硬跑 mintpy。
- **`mask_by_coherence`**:速度场/时序仍含低时相相干像元,需要按
  `maskTempCoh.h5`(或指定 `mask_file`)置 NaN。推荐方法,但**不是默认** ——
  规划器不会仅因「推荐」覆盖 default_method。
- **`subset_lalo`**:全幅太大或只需 AOI。必须同时给 `subset_lat` 与
  `subset_lon`(形式 `35.6:36.0`);留空会在 build 期拆参失败。当前实现只裁
  主源 `analysis/source.h5`,不自动裁次源。
- 掩膜与裁剪当前是**单选方法**,不能在同一步既 mask 又 subset;需要两者时拆
  成两次分析 run 或先 subset 再在 MintPy 外掩(Agent 不提供组合方法)。

## 参数启发式

- **`mask_file`**(science,默认 `mintpy/maskTempCoh.h5`):仅 `mask_by_coherence`
  消费。路径相对工作区,必须存在;常用第 7 步 MintPy 的时间相干掩膜。换掩膜
  = 换像元集合,进指纹。
- **`subset_lat` / `subset_lon`**(science,默认 `""`):仅 `subset_lalo` 消费。
  格式 `min:max`(实现也接受逗号)。地理编码产物才能按 lat/lon 裁;雷达坐标
  应先 geocode(第 10 步 `mintpy_geocode`),不要把雷达行列号填进 lat/lon。
- **何时该掩**:报告速度场前,时间相干过低的像元会把漏斗/断层边缘拉出假梯度;
  Yunjun et al. 2019 用时间相干预筛 GNSS 失效站(阈值 0.7 是其个例,不是本步
  硬门)。本步没有 `min_coherence` 参数 —— 阈值已经烧进 `mask_file` 的生成
  (第 7 步),这里只应用现成掩膜。
- 双源:mask 方法对存在的 `analysis/source_2.h5` 套同一 `mask_file`;subset
  目前只处理主源。升降轨分解前若只裁了一侧,第 23 步会因范围不一致失败。

## 常见失败与处置

1. **`passthrough 规范输入不存在:analysis/source.h5`** → 第 20 步没产出 h5
   (CSV 源只写 `source.csv`;无 rasterio 的 GeoTIFF 可能只有 `.tif`)→ 换 h5
   主源,或不要对 CSV 走 21+ 的 mintpy 方法。
2. **mask 输入/掩膜文件不存在** → 工作区没有 `mintpy/maskTempCoh.h5`(分析 run
   往往不含核心链产物)→ 把掩膜拷进工作区并改 `mask_file`,或保持 passthrough。
3. **`subset_lat 须为两个值`** → 选了 `subset_lalo` 却留空 → 填 `lat0:lat1` 与
   `lon0:lon1`,或改回 passthrough。
4. **裁完第 23 步分解失败** → 升降轨未裁到同一范围/分辨率 → 两侧用同一 AOI
   各做一次,或双方都 passthrough 后依赖上游已对齐的地理编码产品。
5. **误以为默认已掩膜** → `default_method=passthrough`,日志会是硬链/拷贝;
   sidecar `*.json` 记 sha256。要掩必须 `SET_METHOD` 到 `mask_by_coherence`。

## QA 依据

- run_ok:`exit_code == 0` + `artifact_exists(masked)`。
- passthrough:目标与源字节一致(硬链/拷贝),不改变 NaN 比例。
- mask:MintPy `mask.py` 把掩膜 0 处置 NaN;覆盖率下降是预期,不要在本步放宽
  上游相干阈值来「找回」像元。
- subset:输出范围应落在请求的 lat/lon 盒内;空盒或反序由 MintPy CLI 真实报错,
  不做静默交换。

## 参考文献

- Pepe A., Lanari R. (2006). On the extension of the minimum cost flow algorithm
  for phase unwrapping of multitemporal differential SAR interferograms.
  *IEEE TGRS* 44(9):2374-2383. doi:10.1109/TGRS.2006.873207(时间相干)
- Yunjun et al. (2019). *C&G* 133:104331(时间相干预筛;MintPy mask 工作流)
- MintPy CLI:`python -m mintpy.cli.mask --help`;`python -m mintpy.cli.subset --help`
- 本仓库:engines/mintpy_post.py(`mask_by_coherence` / `subset_lalo`);
  engines/passthrough.py 第 21 步规范对
