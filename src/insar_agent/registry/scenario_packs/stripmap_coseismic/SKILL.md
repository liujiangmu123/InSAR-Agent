---
name: stripmap_coseismic
description: ALOS 条带(stripmap)同震干涉场景:2010 El Mayor–Cucapah(Baja)Mw 7.2 地震 ALOS raw 条带对,ISCE2 stripmapApp 全链 WSL 实测通过(docs/VALIDATION-isce2-wsl.md)。3-6 步(配准/干涉/滤波/解缠)覆写为 isce2_stripmap_* 方法,单干涉对、无云端已完成步骤;数据已装配在 WSL 工作区 /home/insar/work/baja,路径参数随包固化。适配「ALOS/条带/stripmap/L波段」类请求。
metadata:
  version: 1.1.0
  priority: 5
  label: 条带同震干涉
  match: 'ALOS|条带|stripmap|L波段'
  chain: stripmap
  model: linear
  pick_7: mintpy_sbas
  diag: L 波段 raw 条带数据,单干涉对;近场同震形变量级大(实测解缠 LOS 约 -4.8 ~ +7.1 m)
  reason: raw 条带数据 → stripmapApp 链(3-6 步全覆写);单对仅两景,时序上无法区分阶跃与线性 → linear
  data_ready: true
  region: Baja California(El Mayor–Cucapah,T212/F0640)
  dates: 震前/震后各一景(事件 2010-04-04)
  scenes: 1 个干涉对(FBS 主影像 + FBD 从影像)
---

# 条带同震干涉(ALOS Baja,El Mayor–Cucapah 2010)

## 场景概述

2010-04-04 El Mayor–Cucapah Mw 7.2 地震(北 Baja California)。数据是 ALOS PALSAR
原始处理级(raw)条带对,走 ISCE2 stripmapApp 链:raw → SLC 聚焦 → 配准 → 干涉 →
滤波 → snaphu 解缠 → 地理编码。2026-08 已在 WSL 引擎环境端到端实测通过
(docs/VALIDATION-isce2-wsl.md),产出物理上合理的同震形变场:断层带条纹瓣、
谷地失相干、山区高相干均符合已发表结果形态,解缠 LOS 范围约 -4.8 ~ +7.1 m。
本场景优先级(priority=5)排在 quake(10)之前:文本同时含「条带/ALOS」与「同震」
关键词时按数据形态归入本链,纯同震请求仍归 quake(S1/HyP3 路线)。

## 数据要求与布局(FBS/FBD 配对,已装配在 WSL 工作区)

- 主影像 FBS(震前,IMG-HH + LED),从影像 FBD(震后):FBD 配 FBS 主影像时
  从影像组件必须声明 `RESAMPLE_FLAG=dual2single`(第 3 步 `resample_flag` 参数);
  FBS/FBS 同模式配对则留空,XML 不渲染该属性 —— 与实测 XML 形态一致。
- 数据已装配(data_ready=true):手工验证链把 GMTSAR 官方示例包 ALOS Baja EQ
  装配在 WSL 发行版 `insar` 的 `/home/insar/work/baja`(raw/ 平铺四个
  `IMG-HH-*`/`LED-*` 文件 + dem/dem.wgs84 已完成 ISCE 转换)。引擎作业本来就在
  WSL 内跑,3 步的 IMG/LED/dem 路径参数直接用 WSL 绝对路径(overrides.yaml 固化,
  与手工验证的 stripmapApp.xml 逐字段一致),零拷贝零装配。
- 第 1 步 `local_import` 走 WSL 工作区模式(source 为 POSIX 绝对路径时):只读
  核验干涉对与 DEM 在位,把清单登记进 `data/slc/manifest.json`,不搬运字节。
- 更换数据时只覆写第 1/2/3 步路径参数(science 输入,进指纹),4-6 步分段续跑
  同一份 stripmapApp 配置。

## DEM 转换(dem.grd → ISCE 格式)

GMT netCDF 的 `topo/dem.grd` 不能直接喂给 ISCE2:先
`gdal_translate -of ISCE dem.grd dem.wgs84`,再 `fixImageXml.py -f` 把 XML 里的
路径改为绝对路径。本包数据的转换成品在 `/home/insar/work/baja/dem/dem.wgs84`
(第 2 步 `dem` 与第 3 步 `dem_path` 参数已固化为该路径)。
第 2 步选 `dem_local`(本地 DEM,核验 dem.wgs84 与 fixImageXml 产物 .xml 在位
后登记清单),不走在线 DEM 服务。
注意 PROJ 数据路径:不激活 conda 环境时 `PROJ_DATA/PROJ_LIB/GDAL_DATA` 必须已在
环境里(WSL 侧已固化进 /etc/profile.d/insar.sh,实测教训 1)。

## pickle 链约束(分段续跑的硬规则)

ISCE2 `--steps` 续跑恢复时只加载「起始步骤直接前驱」的 pickle;分段之间跳过任何
步骤(哪怕是 do_xxx=False 的空转占位步)都会拿到空状态,在首个产品引用处以
NoneType 崩溃(实测教训 2:从 filter 停、从 unwrap 起,秒挂)。因此相邻步骤的
区间必须在 stripmapApp 全序列上首尾相接,3-6 步分段固化为:

| 步骤 | 方法 | stripmapApp 区间 | 实测耗时 |
|---|---|---|---|
| 3 配准 | isce2_stripmap_xcorr | startup → fine_resample | 阶段1(→filter)共约 20 min,两景聚焦占大头 |
| 4 干涉 | isce2_stripmap_ifg | split_range_spectrum → filter | 含在阶段1 内 |
| 5 滤波 | isce2_stripmap_filter | filter → filter(单步重跑) | 分钟级 |
| 6 解缠 | isce2_stripmap_unwrap_snaphu | filter_low_band → geocode | 约 13 min(snaphu 约 10 min) |

全链实测 33 min、工作区峰值 32 GB。解缠段必须从 filter_low_band 续起,让空转
占位步补齐 pickle 链,绝不能直接从 unwrap 起。区间表在 engines/isce2.py
`_STRIPMAP_RANGES`,连续性有测试断言,勿手改分段。

## 产物路径(第 4-6 步 run_ok 判据)

run 脚本 `cd isce2` 后执行 stripmapApp,产物在 `isce2/interferogram/` 下
(VALIDATION 报告实测):干涉 `topophase.flat`,滤波 `filt_topophase.flat` 与
`phsig.cor`,解缠 `filt_topophase.unw`(+`.conncomp`)及 `.geo` 地理编码版本。

## 模型设定与质量门侧重

- 单干涉对仅两个获取日期,7-9 步是形式化通道:时序上无法区分阶跃与线性,
  model 取 `linear`(唯一可辨识参数化);同震信号的核心产物是第 6 步解缠形变场。
- 第 11 步覆写 `coherence_mask`:单对无 PS/SBAS 双链,交叉验证(crossval_ps_sbas)
  不可行 —— 与 quake 包同款诚实降级,不假装有双链验证。
- 与 topsApp 路线共存:链选择由场景决定 —— S1 IW 场景(quake 等)走 topsApp
  缺省方法,本包仅在 stripmap_coseismic 场景下覆写 3-6 步,互不冲突
  (stripmap 方法声明了 scenario_only,其他场景下会被可行性收窄排除)。
