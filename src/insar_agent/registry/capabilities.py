"""十一步流水线能力声明(纯数据)。

与 prototype/js/state.js STEP_DEFS 逐项对齐;超时/资源/磁盘按 AGENT-DESIGN §1.1/§4.10/§4.11;
阈值一律引用 audit/contract.yaml 台账(threshold_key),PENDING 的只出 warning(§4.13)。

注意:这里的数字凡无实测依据者,都只作为「声明结构」示例存在,不参与硬 gate ——
硬 gate 必须走 contract.yaml 的 source 纪律。
"""

from __future__ import annotations

from insar_agent.registry.model import (
    ArtifactSpec,
    Capability,
    DiskEstimate,
    Method,
    Param,
    RunOkCheck,
    Timeouts,
)

PIPELINE: tuple[Capability, ...] = (
    Capability(
        id=1,
        name="数据获取",
        phase="数据获取",
        deps=(),
        methods=(
            Method("asf_search_slc", "asf_search_slc", "asf_api", why="下载原始 SLC,可控性最强"),
            Method("hyp3_submit", "hyp3_submit", "hyp3", why="云端处理,跳过 2-6 步,失去中间产物控制权",
                   requires_credentials=("earthdata",), extra="需 ASF 配额"),
            Method("local_import", "local_import", "-", why="已有本地数据", recommend=True),
        ),
        default_method="local_import",
        params={
            "scenes": Param(7, kind="science", type="int", min=2, max=200, hint="景数 2-200"),
            "platform": Param("sentinel-1", kind="science", type="str"),
            "dates": Param("2019-06-10..2019-08-15", kind="science", type="str"),
            "source": Param("", kind="science", type="str",
                            hint="local_import 的数据源目录(HyP3 产品目录或 SLC 目录)"),
        },
        artifacts=(
            ArtifactSpec("slc", ("data/slc", "hyp3"), kind="SLC", layout="isce2", policy="path"),
            # HyP3 路线:云端已完成解缠,导入的产品目录同时充当 unw 输入(供第 7 步)
            ArtifactSpec("unw", ("hyp3",), kind="IFG_UNWRAPPED", layout="hyp3",
                         policy="path", required=False),
            ArtifactSpec("era5", ("mintpy/inputs/ERA5.h5",), kind="CONFIG",
                         policy="stat", required=False),
        ),
        run_ok=(RunOkCheck("exit_code", equals=0), RunOkCheck("artifact_exists", id="slc")),
        timeouts=Timeouts(idle=1800, total=6 * 3600),
        disk=DiskEstimate("scenes * 2.4"),
        io="heavy",
        replay="safe",  # 数据发现/校验幂等;真实下载有断点续传,重跑安全
    ),
    Capability(
        id=2,
        name="辅助数据",
        phase="辅助数据",
        deps=(1,),
        methods=(
            Method("dem_copernicus", "dem_copernicus", "dem_service", why="Copernicus 30 m,覆盖全球且质量稳定", recommend=True),
            Method("dem_srtm", "dem_srtm", "dem_service", why="SRTM 30 m,高纬度覆盖缺口"),
            Method("dem_local", "dem_local", "-", why="使用本地 DEM 瓦片"),
        ),
        default_method="dem_copernicus",
        params={
            "dem": Param("copernicus-30m", kind="science", type="str"),
            "orbit": Param("poeorb", kind="science", type="str", enum=("poeorb", "resorb")),
        },
        artifacts=(ArtifactSpec("dem", ("data/dem",), kind="DEM", layout="isce2", policy="path"),),
        inputs=("slc",),
        run_ok=(RunOkCheck("exit_code", equals=0), RunOkCheck("artifact_exists", id="dem")),
        timeouts=Timeouts(idle=600, total=3600),
        disk=DiskEstimate("1.5"),
        replay="safe",
    ),
    Capability(
        id=3,
        name="配准",
        phase="配准",
        deps=(1, 2),
        methods=(
            Method("isce2_tops_geom_esd", "isce2_tops_geom_esd", "isce2",
                   why="S1 IW 标准路径:几何配准 + ESD 精化", recommend=True, requires_engines=("isce2",)),
            Method("isce2_stripmap_xcorr", "isce2_stripmap_xcorr", "isce2",
                   why="条带模式(ALOS raw,2026-08 WSL 实测全链通过)", requires_engines=("isce2",),
                   scenario_only=("stripmap_coseismic",)),
            Method("snap_backgeocoding", "snap_backgeocoding", "snap",
                   why="走 SNAP 链,需换 layout", requires_engines=("snap",)),
        ),
        default_method="isce2_tops_geom_esd",
        params={
            "esd_coherence_threshold": Param(0.85, kind="science", min=0, max=1,
                                             hint="ESD 相干阈值 0-1"),
            # 以下为 isce2_stripmap_xcorr(ALOS raw 条带链)专用:路径是 science
            # 输入(决定处理的是哪份数据,进指纹);形态见 docs/VALIDATION-isce2-wsl.md
            "reference_image": Param("data/raw/reference/IMG-HH", kind="science", type="str",
                                     hint="stripmap:参考 raw 影像 IMG 相对路径"),
            "reference_leader": Param("data/raw/reference/LED", kind="science", type="str",
                                      hint="stripmap:参考影像 LED 头文件相对路径"),
            "secondary_image": Param("data/raw/secondary/IMG-HH", kind="science", type="str",
                                     hint="stripmap:从 raw 影像 IMG 相对路径"),
            "secondary_leader": Param("data/raw/secondary/LED", kind="science", type="str",
                                      hint="stripmap:从影像 LED 头文件相对路径"),
            "resample_flag": Param("", kind="science", type="str",
                                   enum=("", "dual2single"),
                                   hint="stripmap:FBD 从影像配 FBS 主影像时用 dual2single,空=不重采样"),
            "dem_path": Param("data/dem/dem.wgs84", kind="science", type="str",
                              hint="stripmap:ISCE 格式 DEM 相对路径"),
            "threads": Param(8, kind="resource", type="int", min=1, max=32,
                             hint="线程数 1-32(本机 24 核,留 4 核给系统)"),
        },
        artifacts=(
            # 首候选 data/coreg 不变(tops/simulate 兼容);条带链配准段(startup→
            # fine_resample)产物是 coregisteredSlc/(run 脚本 cd isce2 后执行,
            # 2026-08 Baja 手工链工作区实测布局 —— 预检发现原声明缺此候选,
            # 真实 stripmap run 会在产物发现阶段 contract_broken)
            ArtifactSpec("coreg", ("data/coreg", "isce2/coregisteredSlc"),
                         kind="RSLC", layout="isce2"),
        ),
        inputs=("slc", "dem"),
        run_ok=(RunOkCheck("exit_code", equals=0), RunOkCheck("artifact_exists", id="coreg"),
                RunOkCheck("log_absent", pattern=r"ERROR|Segmentation fault")),
        timeouts=Timeouts(idle=1800, total=4 * 3600),
        disk=DiskEstimate("scenes * 1.2", peak_multiplier=1.6),
        cpu=8,
        mem_gb=12,
        io="heavy",
    ),
    Capability(
        id=4,
        name="干涉",
        phase="干涉",
        deps=(3,),
        methods=(
            Method("isce2_ifg_multilook", "isce2_ifg_multilook", "isce2",
                   why="可调多视比。小基线网络按时空基线剪枝,非全组合", recommend=True,
                   requires_engines=("isce2",)),
            Method("snap_interferogram", "snap_interferogram", "snap", why="SNAP 链对应步骤",
                   requires_engines=("snap",)),
            # 条带链(ALOS raw):stripmapApp 分段 split_range_spectrum→filter,
            # 分频谱占位步不可跳过(pickle 链约束,engines/isce2.py _STRIPMAP_RANGES)。
            # 耗时/磁盘按 docs/VALIDATION-isce2-wsl.md 实测:全链 33 min、峰值 32 GB,
            # 本段落在阶段 1(startup→filter 约 20 min,两景聚焦占大头)内,
            # 均远低于本 capability 的 total 超时与磁盘预算上限
            Method("isce2_stripmap_ifg", "isce2_stripmap_ifg", "isce2",
                   why="条带链干涉:stripmapApp 分频谱→干涉→滤波段(ALOS raw)",
                   requires_engines=("isce2",), scenario_only=("stripmap_coseismic",),
                   extra="实测(ALOS Baja):阶段1 startup→filter 约 20 min;全链 33 min/32 GB"),
        ),
        default_method="isce2_ifg_multilook",
        params={
            "range_looks": Param(10, kind="science", type="int", min=1, max=40, hint="距离向多视 1-40"),
            "azimuth_looks": Param(2, kind="science", type="int", min=1, max=40, hint="方位向多视 1-40"),
            "pairs": Param(11, kind="science", type="int", min=1, max=5000),
            "threads": Param(8, kind="resource", type="int", min=1, max=32),
        },
        artifacts=(
            # 候选按声明序匹配,tops 布局在前(首候选不变,simulate 仍写 data/ifg);
            # 条带链产物在 isce2/interferogram/ 下(run 脚本 cd isce2 后执行 stripmapApp,
            # 路径按 VALIDATION 报告实测:干涉步产 topophase.flat,段尾 filter 产 filt_ 版本)
            ArtifactSpec("ifg", ("data/ifg", "isce2/interferogram/topophase.flat",
                                 "isce2/interferogram/filt_topophase.flat"),
                         kind="IFG_WRAPPED", layout="isce2"),
        ),
        inputs=("coreg",),
        run_ok=(RunOkCheck("exit_code", equals=0), RunOkCheck("artifact_exists", id="ifg")),
        timeouts=Timeouts(idle=3600, total=8 * 3600),
        disk=DiskEstimate("pairs * 0.34", peak_multiplier=1.4),
        cpu=8,
        mem_gb=12,
        io="heavy",
    ),
    Capability(
        id=5,
        name="滤波",
        phase="滤波",
        deps=(4,),
        methods=(
            Method("goldstein", "goldstein", "isce2", why="低相干区推荐", recommend=True,
                   requires_engines=("isce2",)),
            Method("boxcar", "boxcar", "isce2", why="简单快速,但边缘模糊", requires_engines=("isce2",)),
            Method("none", "none", "-", why="不滤波,保留全部细节"),
            # 条带链滤波:stripmapApp filter 单步重跑(前驱 sub_band_interferogram 的
            # pickle 在干涉段已生成);filter_strength 沿实测形态用 stripmapApp 默认,
            # XML 不渲染该属性(docs/VALIDATION-isce2-wsl.md)
            Method("isce2_stripmap_filter", "isce2_stripmap_filter", "isce2",
                   why="条带链滤波:stripmapApp filter 单步(ALOS raw)",
                   requires_engines=("isce2",), scenario_only=("stripmap_coseismic",),
                   extra="单步重跑,分钟级(实测全链 33 min 内占比很小)"),
        ),
        default_method="goldstein",
        params={
            "alpha": Param(0.4, kind="science", min=0, max=1, hint="Goldstein alpha 0-1"),
            "filter_strength": Param(0.5, kind="science", min=0, max=1),
        },
        artifacts=(
            # 条带链滤波产物:isce2/interferogram/filt_topophase.flat(VALIDATION 报告实测路径)
            ArtifactSpec("ifg_filt", ("data/ifg_filt", "isce2/interferogram/filt_topophase.flat"),
                         kind="IFG_WRAPPED", layout="isce2"),
        ),
        inputs=("ifg",),
        run_ok=(RunOkCheck("exit_code", equals=0), RunOkCheck("artifact_exists", id="ifg_filt")),
        timeouts=Timeouts(idle=1800, total=4 * 3600),
        disk=DiskEstimate("pairs * 0.17"),
        io="medium",
    ),
    Capability(
        id=6,
        name="解缠",
        phase="解缠",
        deps=(5,),
        methods=(
            Method("snaphu_mcf", "snaphu_mcf", "snaphu",
                   why="Minimum Cost Flow。低相干区稳健,MintPy 原生兼容", recommend=True,
                   requires_engines=("snaphu",), extra="耗时/内存待实测"),
            Method("snaphu_smooth", "snaphu_smooth", "snaphu",
                   why="精度更高但需人工调 cost function", requires_engines=("snaphu",),
                   extra="需交互配置"),
            Method("icu", "icu", "isce2", why="区域增长法,大范围低相干区易产生解缠孤岛",
                   requires_engines=("isce2",)),
            Method("3D_FULL", "3D_FULL", "unw3d", why="需 3D 相位解缠工具链,输出格式与下游不兼容",
                   requires_engines=("unw3d",)),
            # 条带链解缠:snaphu 由 stripmapApp 内置驱动(XML 里 do unwrap=True/
            # unwrapper name=snaphu),不需要独立 snaphu 可执行,故只依赖 isce2;
            # 分段必须从 filter_low_band 续起补齐 pickle 链(实测教训 2:直接从
            # unwrap 起,前驱 filter_high_band 占位步 pickle 缺失,恢复空状态必崩),
            # 段尾含 geocode 地理编码 —— docs/VALIDATION-isce2-wsl.md
            Method("isce2_stripmap_unwrap_snaphu", "isce2_stripmap_unwrap_snaphu", "isce2",
                   why="条带链解缠:stripmapApp 内置 snaphu + 地理编码(ALOS raw)",
                   requires_engines=("isce2",), scenario_only=("stripmap_coseismic",),
                   extra="实测 filter_low_band→geocode 约 13 min(snaphu 约 10 min)"),
        ),
        default_method="snaphu_mcf",
        params={
            "min_coherence": Param(0.25, kind="science", min=0, max=1, hint="相干性阈值 0-1"),
            "cost_mode": Param("SMOOTH", kind="science", type="str", enum=("SMOOTH", "DEFO", "TOPO")),
            "threads": Param(8, kind="resource", type="int", min=1, max=32),
        },
        artifacts=(
            # 条带链解缠产物按 VALIDATION 报告实测:filt_topophase.unw(+.conncomp)
            # 与 .geo 地理编码版本,均在 isce2/interferogram/ 下
            ArtifactSpec("unw", ("data/unw", "data/unw/geo",
                                 "isce2/interferogram/filt_topophase.unw",
                                 "isce2/interferogram/filt_topophase.unw.geo"),
                         kind="IFG_UNWRAPPED", layout="isce2"),
            ArtifactSpec("unwrap_cfg", ("params/unwrap.yaml",), kind="CONFIG", policy="content",
                         required=False),
        ),
        inputs=("ifg_filt",),
        run_ok=(
            RunOkCheck("exit_code", equals=0),
            RunOkCheck("artifact_exists", id="unw"),
            RunOkCheck("log_absent", pattern=r"ERROR|Segmentation fault"),
        ),
        quality_gate=(
            RunOkCheck("metric_min", metric="unwrap_coverage", threshold_key="unwrap_coverage",
                       on_fail="stop"),
        ),
        timeouts=Timeouts(idle=1800, total=7200),
        disk=DiskEstimate("pairs * 0.2"),
        cpu=8,
        mem_gb=8,
        io="medium",
    ),
    Capability(
        id=7,
        name="时序反演",
        phase="时序反演",
        deps=(6,),
        methods=(
            Method("mintpy_sbas", "mintpy_sbas", "mintpy",
                   why="小基线集,适合低相干面状形变区", recommend=True, requires_engines=("mintpy",)),
            Method("pystamps_ps", "pystamps_ps", "pystamps",
                   why="永久散射体,适合高相干点状目标;需 ISCE2→PyStamps 桥",
                   requires_engines=("pystamps",)),
        ),
        default_method="mintpy_sbas",
        params={
            "network": Param("small_baseline", kind="science", type="str",
                             enum=("small_baseline", "star", "sequential")),
            "max_temporal_baseline": Param(120, kind="science", type="int", min=6, max=730,
                                           hint="时间基线 6-730 天"),
            "parallel_workers": Param(4, kind="resource", type="int", min=1, max=16),
        },
        artifacts=(
            ArtifactSpec("timeseries",
                         ("mintpy/timeseries.h5", "products/timeseries.h5", "timeseries.h5"),
                         kind="TIMESERIES", layout="mintpy_h5", policy="content"),
        ),
        inputs=("unw",),
        run_ok=(
            RunOkCheck("exit_code", equals=0),
            RunOkCheck("artifact_exists", id="timeseries"),
            RunOkCheck("not_all_nan", id="timeseries"),
        ),
        timeouts=Timeouts(idle=1800, total=24 * 3600),
        disk=DiskEstimate("pairs * 0.12 + 2"),
        cpu=8,
        mem_gb=16,
        io="heavy",
    ),
    Capability(
        id=8,
        name="误差校正",
        phase="误差校正",
        deps=(7,),
        methods=(
            # CDS 凭据是「无 ERA5 缓存时」才需要的条件依赖,静态声明表达不了,
            # 不做硬拦:缓存/凭据都缺时由运行期失败 + triage 诚实呈现
            Method("tropo_era5_pyaps", "tropo_era5_pyaps", "pyaps",
                   why="ERA5 大气校正(需 CDS 凭据或已缓存的 ERA5.h5)", recommend=True,
                   requires_engines=("pyaps",)),
            Method("tropo_gacos", "tropo_gacos", "gacos", why="GACOS 产品,需在线申请",
                   requires_credentials=("gacos",)),
            Method("tropo_height_corr", "tropo_height_corr", "mintpy",
                   why="无气象数据时的降级方案(证据级别下降)", requires_engines=("mintpy",)),
        ),
        default_method="tropo_era5_pyaps",
        params={
            # 默认 no 对齐 MintPy 上游(deramp=no):linear 会把 co-/post-/inter-seismic
            # 的长波长形变梯度当轨道误差扣除(smallbaselineApp.cfg §9 官方注释;
            # RESEARCH-insar-step-knowledge C1/P0)。局地形变(沉降/矿区/滑坡)再覆写 linear。
            "ramp": Param("no", kind="science", type="str", enum=("no", "linear", "quadratic")),
            "dem_error": Param(True, kind="science", type="bool"),
            # 默认对齐实测基准配置 RidgecrestSenDT71.txt(未启用 SET);
            # 另:conda-forge pysolid 的 Fortran DLL 在 Windows 上加载失败,开启前须验证
            "solid_earth_tides": Param(False, kind="science", type="bool"),
        },
        artifacts=(
            # 输出文件名随配置组合变化(InSAR_Agent mintpy.py:343-344 的教训):候选全列
            ArtifactSpec("timeseries_corrected",
                         ("mintpy/timeseries_ERA5_ramp_demErr.h5",
                          "mintpy/timeseries_ERA5_demErr.h5",
                          "mintpy/timeseries_ramp_demErr.h5",
                          "mintpy/timeseries_demErr.h5",
                          "products/timeseries_corrected.h5",
                          "timeseries_ERA5_ramp_demErr.h5",
                          "timeseries_ramp_demErr.h5"),
                         kind="TIMESERIES", layout="mintpy_h5", policy="content"),
        ),
        inputs=("timeseries",),
        run_ok=(RunOkCheck("exit_code", equals=0),
                RunOkCheck("artifact_exists", id="timeseries_corrected")),
        timeouts=Timeouts(idle=1800, total=6 * 3600),
        disk=DiskEstimate("3"),
        io="medium",
    ),
    Capability(
        id=9,
        name="形变模型",
        phase="形变模型",
        deps=(8,),
        methods=(
            Method("poly_periodic", "poly_periodic(1,[1,0.5])", "mintpy",
                   why="线性 + 年周期 + 半年周期,匹配季节冻融机理", requires_engines=("mintpy",),
                   scenario_only=("permafrost",)),
            Method("linear", "linear", "mintpy", why="仅线性趋势", recommend=True,
                   requires_engines=("mintpy",)),
            Method("step", "step(date)", "mintpy", why="同震阶跃", requires_engines=("mintpy",),
                   scenario_only=("quake",)),
            Method("exponential", "exponential", "mintpy", why="震后/矿区衰减形变",
                   requires_engines=("mintpy",)),
        ),
        default_method="linear",
        params={
            "periods": Param([1, 0.5], kind="science", type="list"),
            "poly_order": Param(1, kind="science", type="int", min=0, max=3),
            "step_date": Param("", kind="science", type="str"),
        },
        artifacts=(
            ArtifactSpec("velocity", ("mintpy/velocity.h5", "products/velocity.h5", "velocity.h5"),
                         kind="VELOCITY", layout="mintpy_h5", policy="content"),
        ),
        inputs=("timeseries_corrected",),
        run_ok=(RunOkCheck("exit_code", equals=0), RunOkCheck("artifact_exists", id="velocity"),
                RunOkCheck("not_all_nan", id="velocity")),
        timeouts=Timeouts(idle=600, total=3600),
        disk=DiskEstimate("0.5"),
        io="light",
    ),
    Capability(
        id=10,
        name="出图导出",
        phase="成图",
        deps=(9,),
        methods=(
            Method("figure_journal", "figure_journal", "-",
                   why="期刊级排版:600 dpi、色盲安全色带、比例尺", recommend=True),
            Method("mintpy_geocode", "mintpy_geocode", "mintpy", why="仅地理编码,不做排版",
                   requires_engines=("mintpy",)),
            Method("gdal_warp", "gdal_warp", "gdal", why="导出 GeoTIFF 供 GIS 使用",
                   requires_engines=("gdal",)),
        ),
        default_method="figure_journal",
        params={
            "dpi": Param(600, kind="presentation", type="int", min=72, max=1200, hint="出图 DPI 72-1200"),
            "cmap": Param("roma", kind="presentation", type="str"),
            "format": Param("png+pdf", kind="presentation", type="str"),
        },
        artifacts=(
            ArtifactSpec("figures", ("products/figures", "products/velocities"),
                         kind="FIGURE", policy="stat"),
        ),
        inputs=("velocity",),
        run_ok=(RunOkCheck("exit_code", equals=0), RunOkCheck("artifact_exists", id="figures")),
        timeouts=Timeouts(idle=600, total=1800),
        disk=DiskEstimate("0.2"),
        io="light",
        replay="safe",  # 出图无副作用,重跑安全
    ),
    Capability(
        id=11,
        name="质检",
        phase="质检",
        deps=(10, 7),
        methods=(
            Method("crossval_ps_sbas", "crossval_ps_sbas", "-",
                   why="PS/SBAS 双链交叉验证 —— 本项目独有质量门", recommend=True),
            Method("loop_closure", "loop_closure", "mintpy",
                   why="闭合回路残差检查,只验解缠不验反演", requires_engines=("mintpy",)),
            Method("coherence_mask", "coherence_mask", "mintpy", why="相干性掩膜,最弱的质检",
                   requires_engines=("mintpy",)),
        ),
        default_method="crossval_ps_sbas",
        params={
            "corr_threshold": Param(0.85, kind="science", min=0, max=1,
                                    hint="交叉验证相关阈值 0-1"),
        },
        artifacts=(
            ArtifactSpec("qa_report", ("products/report/qa.json", "qa.json"), kind="REPORT",
                         policy="content"),
        ),
        inputs=("velocity", "timeseries"),
        run_ok=(RunOkCheck("exit_code", equals=0), RunOkCheck("artifact_exists", id="qa_report")),
        quality_gate=(
            RunOkCheck("metric_min", metric="crossval_r", threshold_key="corr_threshold",
                       on_fail="stop"),
        ),
        timeouts=Timeouts(idle=900, total=7200),
        disk=DiskEstimate("0.1"),
        io="light",
        replay="safe",
    ),
)

REGISTRY: dict[int, Capability] = {c.id: c for c in PIPELINE}


def capability_of(step_id: int) -> Capability:
    return REGISTRY[step_id]


def step_def(step_id: int) -> Capability:
    return REGISTRY[step_id]


def downstream_of(step_id: int) -> list[int]:
    """反向依赖 BFS,不含自身(与 prototype state.js downstreamOf 对齐)。"""
    rev: dict[int, list[int]] = {}
    for cap in PIPELINE:
        for dep in cap.deps:
            rev.setdefault(dep, []).append(cap.id)
    seen: set[int] = set()
    queue = list(rev.get(step_id, []))
    while queue:
        cur = queue.pop(0)
        if cur in seen:
            continue
        seen.add(cur)
        queue.extend(rev.get(cur, []))
    return sorted(seen)


def topo_order(step_ids: list[int]) -> list[int]:
    """按依赖拓扑排序(步骤 id 已保证单调,直接排序即拓扑序;保留显式实现以防未来改号)。"""
    remaining = set(step_ids)
    ordered: list[int] = []
    while remaining:
        progressed = False
        for sid in sorted(remaining):
            deps = set(REGISTRY[sid].deps) & remaining
            if not deps:
                ordered.append(sid)
                remaining.discard(sid)
                progressed = True
        if not progressed:  # 环 —— registry 声明错误
            raise ValueError(f"dependency cycle among steps: {sorted(remaining)}")
    return ordered
