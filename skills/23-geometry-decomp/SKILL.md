---
name: 23-geometry-decomp
description: 当规划或诊断分析链第 23 步几何分解(升降轨 LOS 分解为垂直+水平,或两源相减,或默认透传)时使用:默认 passthrough;asc_desc_horz_vert 需要已对齐的双源,不要把单轨 LOS 写成三维位移,也不要调用未实现的 ISCE3 分解。
capability: 23
version: "1.0.0"
applies_to: all
---

# 几何分解(升降轨垂直/东西向 / 差分 / 透传)

第 23 步把校正后的速度/位移场变成更可解译的分量,或单源直接透传。方法 id:
`asc_desc_horz_vert`(推荐,引擎 mintpy,label `asc_desc2horz_vert.py`)、
`raster_diff`(引擎 mintpy,`diff.py`)、`passthrough`(引擎 `-`)。
**`default_method=passthrough`**:单源分析的诚实默认,把 `corrected.h5` 规范
链到 `decomposed.h5`,**并不做分解**。产物 `decomposed`(`analysis/decomposed.h5`,
policy=content)。输入 `corrected`。依赖第 22 步。run_ok 含 `not_all_nan`。

## 适用判据

- **`passthrough`(默认)**:只有一条轨道、或后续统计/出图只关心 LOS。报告必须
  写 LOS(相对参考点),不要把透传产物称作垂直形变。
- **`asc_desc_horz_vert`**:升、降轨各一源,已地理编码到同一分辨率与范围。
  分解为垂直 + 水平(默认东西向)—— InSAR 解译标准动作。需要
  `analysis/corrected.h5` **与** `corrected_2.h5`(第 20 步 `secondary` 一路
  透传下来)。缺次源会在 build 期明确失败,不会用单源假装 2.5D。
- **`raster_diff`**:两期/两源相减(变化量、与参考解差异),输出仍写入
  `decomposed.h5`。同样要双文件;这不是升降轨分解,不要把差分场当垂直分量。
- **不做的**:北向分量在近极轨几何下观测性极差,本方法默认水平方位 `-90°`
  (东西);ISCE3 三维分解、像素偏移三维、GNSS 融合三维均未实现。

## 参数启发式

- **`horz_az_angle`**(science,默认 `-90.0`,范围 -180–180):关心的水平方向,
  自北起逆时针为正。MintPy 上游默认 `-90` = 东西向。跨断层可设为断层走向,
  此时「水平」是沿该方位的投影,不是自动的沿断层滑动。
- **`use_geometry_files`**(science,默认 `False`):True 时用逐像元入射/方位角
  替代常量元数据,并要求几何文件在位(与第 22 步同一查找)。常量元数据适合
  小范围;大范围/陡入射变化再打开。
- 双源对齐:两轨必须同单位(通常 m/yr)、同参考点语义尽量接近,否则分解把
  参考差当成「东西向」。先各自第 21/22,再本步。
- 单干涉对同震:分解的是位移而不是速度 —— 源文件 FILE_TYPE 由 MintPy 解释;
  不要把 mm 位移当 mm/yr。

## 常见失败与处置

1. **升降轨分解需要双源:analysis/corrected_2.h5 不存在** → 第 20 步没给
   `secondary`,或 21/22 只透传了主源 → 补次源并重跑 20–22,或改回 passthrough。
2. **`not_all_nan(decomposed)` 失败** → 两轨重叠区为空、掩膜互补、或几何错
   → 检查 overlap;不要把全 NaN 场画出「垂直形变图」。
3. **范围/分辨率不一致** → MintPy `asc_desc2horz_vert` 真实报错 → 先 subset
   到公共盒,或上游 geocode 到同一网格;Agent 不做静默重采样。
4. **把 LOS 透传场写成垂直位移** → 方法仍是 passthrough → 报告用语保持 LOS;
   要垂直/东西必须方法为 `asc_desc_horz_vert` 且产物含对应 dataset。
5. **索要北向 / ISCE3 / 三维形变** → 未实现。近极轨对北向不敏感是几何事实,
   不是缺参数。

## QA 依据

- run_ok:`exit_code == 0` + `artifact_exists(decomposed)` + `not_all_nan`。
- 分解产物应含垂直/水平数据集(MintPy 默认 `vertical` / `east` 一类名字;
  第 24 步 `dataset` 参数按此填写)。
- 定性检查:沉降漏斗垂直分量应大于东西向;走滑断层东西向应显著;若垂直≈LOS
  且东西向噪声级,可能两轨几何太接近(近同向)—— 分解条件数差,应声明病态而非
  强解译。
- passthrough:FILE_TYPE 与上游相同,不是分解成功的证据。

## 参考文献

- Wright T.J., Parsons B.E., Lu Z. (2004). Toward mapping surface deformation
  in three dimensions using InSAR. *Geophys. Res. Lett.* 31:L01607.
  doi:10.1029/2003GL018827(升降轨分解几何;北向弱可观测)
- Fuhrmann T., Garthwaite M.C. (2019). Resolving three-dimensional surface
  motion with InSAR: Constraints from multi-geometry data fusion.
  *Remote Sensing* 11(3):241. doi:10.3390/rs11030241
  (升降轨求垂直+东西向;近极轨北向弱;本步不声称三维)
- MintPy CLI:`python -m mintpy.cli.asc_desc2horz_vert --help`
- 本仓库:engines/mintpy_post.py(`asc_desc_horz_vert` 缺次源即 ValueError;
  `raster_diff` 走 `mintpy.cli.diff`)
