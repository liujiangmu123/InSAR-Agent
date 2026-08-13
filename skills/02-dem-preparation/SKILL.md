---
name: 02-dem-preparation
description: "当需要为流水线第 2 步(辅助数据)选择 DEM 来源(dem_copernicus / dem_srtm / dem_local)、决定轨道产品档次(orbit=poeorb/resorb)、或诊断 DEM 下载失败、覆盖空洞、高程基准错误、干涉图地形相关残余条纹、轨道长波长斜坡等问题时使用。"
capability: 2
version: "1.0.0"
applies_to: all
---

# 辅助数据:DEM 与轨道准备(DEM & Orbit Preparation)

能力速览(以 `registry/capabilities.py` id=2 为准):方法 `dem_copernicus`(默认、推荐)/ `dem_srtm` / `dem_local`;参数 `dem`(默认 "copernicus-30m")、`orbit`(枚举 `poeorb` / `resorb`,默认 `poeorb`);输入 `slc`;产物 `dem`(候选 `data/dem`,ISCE2 layout);磁盘预算 1.5 GB;`replay="safe"`。HyP3 路线本步整体 skipped(`cloud_completed` 含 2)。

## 适用判据

- **`dem_copernicus`(默认、推荐)**:Copernicus GLO-30(30 m),全球覆盖、基于 TanDEM-X 2011–2015 采集,现势性与绝对精度均优于 SRTM(Copernicus DEM Handbook)。无特殊理由不换。
- **`dem_srtm`**:SRTM 1 弧秒(30 m),覆盖仅 56°S–60°N,且是 2000 年 2 月的单时相快照——冰川、矿区、大型工程区高程已过时,过时高程经垂直基线放大成地形残差相位。定位:Copernicus 服务不可用时的备胎,高纬 AOI 不可用。
- **`dem_local`**:已有本地 DEM 瓦片,或非标准格式转换成品。stripmap_coseismic 场景固定走此路:GMT netCDF 的 `dem.grd` 经 `gdal_translate -of ISCE` 转 `dem.wgs84`、再 `fixImageXml.py -f` 修绝对路径(成品在 WSL 工作区,第 3 步 `dem_path` 参数已随场景包固化)。
- **`orbit` 的档次选择**:`poeorb`(Precise Orbit Ephemerides,精密轨道,采集后约 20 天发布,厘米级)是科研成品的唯一合法档;`resorb`(restituted orbit,快速轨道,约 3 h 延迟,分米级)只用于事件应急响应。轨道误差在干涉图上表现为长波长斜坡(orbital ramp),与同震形变的长波长成分混叠——quake 类场景对轨道档次最敏感。
- 场景差异:permafrost(青藏高原 ~33°N)与 landslide(雅鲁藏布)均在 SRTM 覆盖内但地形陡峭,SRTM 空洞(void)风险高,仍首选 Copernicus;stripmap_coseismic 用场景包固化的 `dem_local`,不走在线服务。

## 参数启发式

- **`dem`(默认 "copernicus-30m")**:30 m 档对本流水线是否够用,判据是 DEM 误差的相位放大公式:φ_topo_err = (4π/λ)·(B⊥/(R·sinθ))·δh。S1 的垂直基线(perpendicular baseline)B⊥ 通常 <200 m,GLO-30 平地高程误差 δh 约 2–4 m,残差远小于一个条纹周期,足够;ALOS L 波段(λ=23.6 cm)容差再宽 4 倍。何时需要更好的 DEM:大 B⊥ 干涉对(>300 m)+ 陡峭地形 + 城区精细形变——此时优先做法是第 4 步剔除大 B⊥ 对,而不是找更高分辨率 DEM。方向感:换 `dem_srtm` 只解决"服务可用性",不解决精度;精度问题回到网络设计。
- **`orbit`(默认 `poeorb`)**:何时降为 `resorb`——事件发生后 20 天内要出快速结果。降档后必须在结果里预期:干涉图可能出现近似平行等间距的贯穿性条纹;`poeorb` 发布后应改回本参数重跑(science 参数变更自动触发 3–6 步失效重算),或临时依赖第 8 步 `ramp` 参数兜底——但注意 quake 场景对 deramp 的告诫:同震长波长形变会被当轨道误差扣掉(MintPy smallbaselineApp.cfg §9 注释),兜底仅限时序中性场景。
- 隐性检查点(不是参数但决定成败):高程基准。SRTM/Copernicus 原始产品是大地水准面(geoid,EGM96/EGM2008)高程,ISCE2 需要 WGS84 椭球高——标准下载器(dem stitcher)自动做转换,`dem_local` 路线必须自己保证。基准错 ≈ 全区系统性 δh 数十米,症状见下节第 3 条。

## 常见失败与处置

1. **症状**:DEM 下载超时或 404。**根因**:DEM 服务临时故障,或 AOI 对应瓦片缺失。**处置**:直接重跑(`replay="safe"`);连续失败换 `dem_srtm`(纬度允许时)或 `dem_local`;不要在服务故障时反复重试撞 idle 超时(600 s)。
2. **症状**:高纬 AOI 返回空数据或全 NaN。**根因**:SRTM 覆盖界外(>60°N / <56°S)。**处置**:换 `dem_copernicus`(全球覆盖);这是方法选择错误,不是数据故障。
3. **症状**:第 4 步干涉图出现与地形起伏严格相关的残余条纹(条纹沿山脊山谷走)。**根因**:DEM 高程基准错(geoid 高被当椭球高)、DEM 高程过时(冰川/矿区)、或空洞插值伪影。**处置**:核验本步日志中的基准转换记录;`dem_local` 路线重做 geoid→椭球转换;换 `dem_copernicus`;若仅大 B⊥ 对受影响,回第 4 步剔除这些对。
4. **症状**:stripmap 链第 3 步报找不到 `dem.wgs84` 或读 DEM 即崩。**根因**:ISCE 的 `.xml` 元数据里写的是绝对路径,目录迁移后失效;或漏跑 `fixImageXml.py`。**处置**:在 DEM 所在目录重跑 `fixImageXml.py -f`;核对第 3 步 `dem_path` 参数与实际文件一致(场景包固化值勿手改)。
5. **症状**:WSL 内 gdal 转换报 PROJ 相关错误。**根因**:未激活 conda 环境时 `PROJ_DATA` / `PROJ_LIB` / `GDAL_DATA` 缺失(本仓库 VALIDATION 实测教训 1)。**处置**:确认 WSL 侧 `/etc/profile.d/insar.sh` 已固化这三个环境变量;不要在缺变量的裸 shell 里手工补跑转换。
6. **症状**:第 3 步几何配准初值差、burst 对不齐。**根因**:`orbit=resorb` 精度不足,或轨道文件时间窗不覆盖采集时刻。**处置**:改 `orbit=poeorb` 重跑本步;核对轨道文件的起止时间包住每景采集时刻;这是"上游输入病下游发作"的典型,别在第 3 步调 ESD 参数硬扛。

## QA 依据

- run_ok:`exit_code == 0` 且 `dem` 产物在 `data/dem` 命中。
- 覆盖完整性:DEM 范围完整包含全部 SLC footprint 并留外扩余量——边缘缺一角,第 3 步几何配准在缺角处产 NaN 或直接崩;合格线是"覆盖 + 余量",不是"恰好覆盖"。
- 数值合理性:高程极值对照已知地形常识(玉树高原约 3500–6000 m;Ridgecrest 约 600–2500 m;Baja 谷地近 0 m);出现 -32768 之类填充值条带即 void 未处理。
- 空洞与伪影:无 NaN 条带;山影图(hillshade)目视无格网接缝、无插值平台。
- 元数据一致性:ISCE `.xml` 声明的尺寸/采样间隔/左上角坐标与数据体一致(`dem_local` 路线重点查);体积在 1.5 GB 预算量级。
- 本步无 quality_gate;地形残差的最终裁决在第 4 步条纹形态检查,本步把"覆盖、基准、空洞"三件事拦住即可。

## 参考文献

- Airbus / ESA (2020–2022). *Copernicus DEM Product Handbook*(GLO-30/GLO-90 产品定义与精度指标)。
- Farr, T. G., et al. (2007). The Shuttle Radar Topography Mission. *Reviews of Geophysics*, 45, RG2004. doi:10.1029/2005RG000183 —— SRTM 覆盖界与 void 特性。
- Rizzoli, P., et al. (2017). Generation and performance assessment of the global TanDEM-X digital elevation model. *ISPRS Journal of Photogrammetry and Remote Sensing*, 132, 119–139. doi:10.1016/j.isprsjprs.2017.08.008 —— Copernicus DEM 的数据基础。
- Hanssen, R. F. (2001). *Radar Interferometry: Data Interpretation and Error Analysis*. Kluwer. doi:10.1007/0-306-47633-9 —— 地形相位、轨道误差与基线几何(δh 放大公式出处)。
- Sentinel-1 Precise Orbit Determination (POD) 产品规范:Copernicus POD Service,sentinels.copernicus.eu —— poeorb/resorb 延迟与精度档。
- ISCE2 源码与文档:github.com/isce-framework/isce2(`contrib/demUtils` dem stitcher 的 geoid 校正、`applications/fixImageXml.py`)。
- 仓库内:`docs/VALIDATION-isce2-wsl.md`(dem.grd→dem.wgs84 转换与 PROJ 环境教训)、`src/insar_agent/registry/scenario_packs/stripmap_coseismic/SKILL.md`(DEM 转换节)。
