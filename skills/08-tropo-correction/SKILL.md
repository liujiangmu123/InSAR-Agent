---
name: 08-tropo-correction
description: 当规划或诊断第 8 步误差校正(对流层延迟 tropospheric delay、去 ramp、DEM 误差、固体潮)时使用:在 tropo_era5_pyaps / tropo_gacos / tropo_height_corr 之间选方法、定 ramp 阶数与 dem_error / solid_earth_tides 开关、处置 ERA5 下载失败与 PySolid/MKL 等 Windows 环境崩溃。
capability: 8
version: "1.0.0"
applies_to: all
---

# 误差校正(对流层 / ramp / DEM 误差 / 固体潮)

第 8 步从原始时序中扣除非形变项。方法 id:`tropo_era5_pyaps`(默认、推荐)、
`tropo_gacos`、`tropo_height_corr`;产物 artifact id 为 `timeseries_corrected`。
真实运行口径:`smallbaselineApp insar_agent.cfg --start correct_LOD --end
reference_date`(engines/mintpy.py `_RANGES[8]`),覆盖 correct_LOD、correct_SET、
correct_ionosphere、correct_troposphere、deramp、correct_topography、
residual_RMS、reference_date 八个 MintPy 内部步骤。方法 id 到 MintPy 配置的映射
(engines/mintpy.py `render_cfg`):`tropo_era5_pyaps → pyaps`、`tropo_gacos →
gacos`、`tropo_height_corr → height_correlation`,写入
`mintpy.troposphericDelay.method`。

## 适用判据

- **为什么必须校正**:单景对流层扰动可达 cm 级(相对湿度 20% 的时空变化 ≈ 10 cm
  形变误差,Zebker et al. 1997),远大于 mm/yr 级信号。信号量级 ≤ 大气噪声的
  场景 —— 震间、冻土(`permafrost`)、滑坡蠕滑(`landslide`)—— 校正不可省;
  同震(`quake`/`stripmap_coseismic`)形变数十 cm,大气不改变一阶结论,但精化
  滑动分布仍需校正。
- **`tropo_era5_pyaps`(默认)**:PyAPS 用 ERA5 再分析(reanalysis)算分层延迟。
  适用面:分层延迟(与高程相关)主导的山区/高原 —— ERA-I 在青藏昆仑平均削减
  APS 73%(Jolivet et al. 2011)。局限:湍流(turbulence)主导时改善差甚至恶化
  (Jolivet et al. 2014:效果"primarily controlled by the level of turbulence";
  天山案例校正后 STD 反升)。凭据是条件依赖:工作区已有 `mintpy/inputs/ERA5.h5`
  缓存(quake 包实测约 47.9 MB,`local_import` 会复制进工作区)则**免 CDS
  (Copernicus Climate Data Store)凭据**;缓存与凭据都缺时由运行期失败 + triage
  诚实呈现,不做静态硬拦(capabilities.py 注释)。
- **`tropo_gacos`**:GACOS(Generic Atmospheric Correction Online Service)ITD
  模型融合 ECMWF HRES(0.125°,137 层)与 GNSS ZTD。需在线申请
  (`requires_credentials=("gacos",)`)。官方立场是"先看产品自带可行性指标再决定
  采用"(Yu et al. 2018)。实测收益参照:校正后精度 ≈ 1 cm;LiCSBAS 日本案例
  InSAR−GNSS 速度差 STD 2.4 → 1.9 mm/yr(Morishita et al. 2020 §3.4)。
- **`tropo_height_corr`(降级方案)**:高程相关经验校正(Doin et al. 2009)。
  无气象数据时的兜底,**证据级别下降,报告必须声明**;致命局限:无法区分与地形
  相关的真实形变(冻土坡面、火山)—— MintPy cfg §8 注释与 Yunjun et al. 2019
  §4.6 均明示。
- L 波段(`stripmap_coseismic`)注意:电离层(ionosphere)误差常大于对流层,
  标准处置是 split-spectrum(Fattahi et al. 2017);本步骤未声明电离层参数,
  MintPy `correct_ionosphere` 默认关,报告里如实声明该残差项。

## 参数启发式

本步骤在 registry(capabilities.py id=8)声明的参数只有三个:

- **`ramp`**(science,默认 `"linear"`,枚举 `no | linear | quadratic`,渲染为
  `mintpy.deramp`):阶数选择原则 ——
  - `linear`:局部形变(城市沉降、矿区)+ 明显轨道残差趋势面时的常规选择;
  - `no`:**长波长形变场景必选** —— MintPy 官方注释明确 co-/post-/inter-seismic
    等长波长信号不推荐 deramp(smallbaselineApp.cfg §9),同震阶跃属长波长信号,
    `quake` 场景建议覆写为 `no`,避免把形变梯度当轨道误差扣除;
  - `quadratic`:仅当残差趋势面明显二次且形变确属局地时,阶数越高吃掉真实信号
    的风险越大 —— 宁低勿高。
- **`dem_error`**(science,默认 `True`,渲染为 `mintpy.topographicResidual`):
  Fattahi & Amelung (2013) 的 DEM 误差(topographic residual)校正,MintPy 默认
  开启(`auto (yes)`),保持 True。垂直基线越大 DEM 误差相位越大 —— L 波段长基线
  网络尤其不可关。配套细节:`mintpy.topographicResidual.stepFuncDate` 由
  render_cfg 自动跟随第 9 步 `step` 方法的 `step_date`(同震拐点共用),本步骤
  自身不声明该参数。
- **`solid_earth_tides`**(science,默认 `False`,渲染为
  `mintpy.solidEarthTides`):**默认关,且在 Windows 上开启前必须先验证** ——
  conda-forge pysolid 的 Fortran DLL 在 Windows 加载失败是本仓库已实测的崩溃坑
  (README「环境坑位记录」;capabilities.py 注释)。科学面:固体潮
  (solid Earth tides)对 S1 大区域长时序才显著(Yunjun et al. 2022),
  默认 False 同时对齐实测基准配置 RidgecrestSenDT71.txt。
- **输出文件名随配置组合变化**:`timeseries_ERA5_ramp_demErr.h5` /
  `timeseries_ERA5_demErr.h5` / `timeseries_ramp_demErr.h5` /
  `timeseries_demErr.h5` 等 —— registry 的 `timeseries_corrected` 候选列表已
  全列(InSAR_Agent mintpy.py:343-344 的教训),排障时按此顺序找文件而不是
  假定单一名字。

## 常见失败与处置

1. **correct_troposphere 阶段 ERA5 下载失败 / 长时间无输出** → CDS 凭据缺失
   (`~/.cdsapirc`)、CDS 队列拥堵或网络不通 → 先查工作区 `mintpy/inputs/ERA5.h5`
   缓存是否在位(在位则不应触发下载,查 cfg 的 weather 目录指向);无缓存则补
   凭据重试;都不可行时降级 `tropo_height_corr` 并在报告声明证据级别下降。
2. **固体潮校正段崩溃(ImportError / DLL load failed)** → conda-forge pysolid
   的 Fortran DLL 在 Windows 加载失败(本仓库实测)→ 保持 `solid_earth_tides =
   False`;确需开启时先在引擎环境验证 `python -c "import pysolid"` 通过。
3. **deramp 第 2 景硬崩、无栈、0xC06D007F(Windows)** → conda-forge 默认
   BLAS=MKL,MKL 2024 多线程延迟加载 bug(i9-13900K 实测,崩点正是本步 deramp)
   → `conda install "libblas=*=*openblas"` 切 OpenBLAS(README「环境坑位记录」,
   setup 向导必做步骤)。
4. **校正后速度场仍现(或新增)与地形相关的条带/坡面信号** → 分层延迟校正不
   完全,或 `tropo_height_corr` 把地形相关真实形变(冻土坡面/火山)一并扣除 →
   对比校正前后时序与地形的相关性;高原场景优先 ERA5/GACOS 而非经验法;GACOS
   先查可行性指标再采用。
5. **校正后残差 RMS 不降反升** → 湍流主导区,再分析模型分辨不出局地湍流
   (Jolivet et al. 2014)→ 保留未校正版本对比,报告写明方法选择依据;可改试
   `tropo_gacos`(GNSS ZTD 融合对湍流略好)。
6. **run_ok 报 `timeseries_corrected` 不存在但日志显示步骤完成** → 输出文件名
   组合与候选不匹配(如自定义 tropo 模型名)→ 查 `mintpy/` 下 `timeseries_*.h5`
   实际名;本仓库 registry 候选已覆盖 ERA5/ramp/demErr 的常规组合,新组合须
   同步扩充 ArtifactSpec 候选而不是改判定。

## QA 依据

- **run_ok 双判定**(registry 声明):`exit_code == 0` + `artifact_exists
  (timeseries_corrected)`。
- **逐历元残差 RMS**(本步 residual_RMS 内部步骤产出
  `rms_timeseriesResidual*.txt`,第 11 步 qa.json 的 `residual_rms_mm` 重解析):
  去二次趋势面后计算,> 3×MAD(中位数绝对偏差)判噪声历元并剔除,参考日取最小
  RMS 历元(Yunjun et al. 2019 §4.9;MintPy `residualRMS.cutoff = auto (3)`)。
  量级参照:LiCSBAS 像元级掩膜默认 `resid_rms ≤ 2 mm`(震间/缓变场景);
  Ridgecrest 同震实测 21.7 mm —— 大形变场景残差大属预期,跨场景不要硬套 2 mm。
- **校正收益参照系**(判断"校正是否起效"):ERA-I 昆仑 APS −73%
  (Jolivet et al. 2011);ERA-I 洛杉矶单景方差 −70%(Jolivet et al. 2014);
  GACOS 相位 StdDev −47~54%、校正后 ≈ 1 cm(Yu et al. 2018);GACOS 后
  InSAR−GNSS 速度差 STD 2.4 → 1.9 mm/yr(Morishita et al. 2020)。
- **降级的证据纪律**:`tropo_height_corr` 属降级路径(registry `why` 字段明示
  "证据级别下降"),triage/报告必须声明;这与阈值台账"PENDING 只警告"同源 ——
  诚实呈现而非假装等效。

## 参考文献

- Zebker H.A., Rosen P.A., Hensley S. (1997). Atmospheric effects in
  interferometric synthetic aperture radar surface deformation and topographic
  maps. *JGR* 102(B4):7547-7563. doi:10.1029/96JB03804(湿度 20% → 10 cm 误差)
- Jolivet R., Grandin R., Lasserre C., Doin M.-P., Peltzer G. (2011). Systematic
  InSAR tropospheric phase delay corrections from global meteorological
  reanalysis data. *GRL* 38:L17311. doi:10.1029/2011GL048757(PyAPS 方法文献;
  昆仑 APS −73%)
- Jolivet R., Agram P.S., Lin N.Y., Simons M., Doin M.-P., Peltzer G., Li Z.
  (2014). Improving InSAR geodesy using Global Atmospheric Models. *JGR*
  119:2324-2341. doi:10.1002/2013JB010588(湍流决定成败;LA 方差 −70%)
- Yu C., Li Z., Penna N.T., Crippa P. (2018). Generic atmospheric correction
  model for Interferometric Synthetic Aperture Radar observations. *JGR Solid
  Earth* 123:9202-9222. doi:10.1029/2017JB015305(GACOS;StdDev −47~54%)
- Yu C., Li Z., Penna N.T. (2018). Interferometric synthetic aperture radar
  atmospheric correction using a GPS-based iterative tropospheric decomposition
  model. *RSE* 204:109-121. doi:10.1016/j.rse.2017.10.038(ITD 模型)
- Doin M.-P., Lasserre C., Peltzer G., Cavalié O., Doubre C. (2009). Corrections
  of stratified tropospheric delays in SAR interferometry: validation with
  global atmospheric models. *J. Applied Geophysics* 69:35-50(高程相关经验校正)
- Fattahi H., Amelung F. (2013). DEM error correction in InSAR time series.
  *IEEE TGRS* 51(7):4249-4259. doi:10.1109/TGRS.2012.2227761(dem_error 参数依据)
- Fattahi H., Simons M., Agram P. (2017). InSAR time-series estimation of the
  ionospheric phase delay: an extension of the split range-spectrum technique.
  *IEEE TGRS* 55(10). doi:10.1109/TGRS.2017.2718566(L 波段电离层)
- Yunjun Z., Fattahi H., Pi X., Rosen P., Simons M., Agram P., Aoki Y. (2022).
  Range geolocation accuracy of C-/L-band SAR and its implications for
  operational stack coregistration. *IEEE TGRS* 60. (固体潮对大区域长时序的
  影响;MintPy solidEarthTides 功能背景)
- Morishita Y. et al. (2020). LiCSBAS: An open-source InSAR time series analysis
  package. *Remote Sensing* 12(3):424. doi:10.3390/rs12030424(GACOS 收益实测、
  resid_rms 掩膜默认)
- MintPy smallbaselineApp.cfg §8/§9:
  https://github.com/insarlab/MintPy/blob/main/src/mintpy/defaults/smallbaselineApp.cfg
  (pyaps 默认开启、长波长形变不推荐 deramp 的官方注释)
- PyAPS 软件仓库:https://github.com/insarlab/PyAPS ;GACOS 官网:
  http://www.gacos.net/
