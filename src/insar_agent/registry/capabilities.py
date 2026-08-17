"""十一步流水线能力声明(纯数据)。

超时/资源/磁盘按 AGENT-DESIGN §1.1/§4.10/§4.11;
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
            # hint 按场景分层(C5):同震单对 2 景即可;SBAS ≥15-20 景起步(Crosetto 2016
            # 综述,C 波段;Berardino 2002 经典案例 44 景);PS ≥20-25 景,短于此相位
            # 稳定性估计不可靠
            "scenes": Param(7, kind="science", type="int", min=2, max=200,
                            hint="景数 2-200:同震单对=2;SBAS ≥15-20;PS ≥20-25"
                                 "(Crosetto 2016;Berardino 2002)"),
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
        # ≈8 GB/景(C4):双极化 IW SLC 解压后 ≈7-8 GB(SentiWiki:25 s 切片单极化
        # ~3.1 GB×2;Sci Data 2022 "typical unzipped IW SLC ≈7 GB"),旧值 2.4 低估 2-3×。
        # 下载+解压峰值另计:zip(~4-4.5 GB/景)与解压产物并存 ≈12.5 GB/景,
        # 由 peak_multiplier 1.6(8×1.6=12.8)覆盖
        disk=DiskEstimate("scenes * 8", peak_multiplier=1.6),
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
            # 默认 10×2 是 S1 IW(tops)比例(C3):像元 2.3×14.1 m(SentiWiki),
            # rg:az≈5:1 才得近方形地面像元;stripmap(ALOS)像元几何倒置(FBS 方位
            # ~3.2 m < 地面距离 ~7-8 m,JAXA 规格),比例须 az>rg 约 2:1 ——
            # 由 stripmap_coseismic 场景包覆写 2×4(只影响新计划,不动 S1 默认)
            "range_looks": Param(10, kind="science", type="int", min=1, max=40,
                                 hint="距离向多视 1-40;默认按 S1 IW rg:az≈5:1,"
                                      "stripmap(ALOS)比例须倒置(C3)"),
            "azimuth_looks": Param(2, kind="science", type="int", min=1, max=40,
                                   hint="方位向多视 1-40;stripmap(ALOS)az>rg 约 2:1(C3)"),
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
            # star/sequential 护栏(C7):star=单参考星形(PS 式拓扑),SBAS 下退化为
            # 无冗余无闭合环;sequential 纯短基线网络有 fading 系统偏差
            # (Ansari 2021:seq-5 达 -6.5 mm/yr,混入长基线对后收敛到 -0.24)
            "network": Param("small_baseline", kind="science", type="str",
                             enum=("small_baseline", "star", "sequential"),
                             hint="star=单参考仅 PS/试验(SBAS 下无冗余无闭合);"
                                  "sequential 有 fading 偏差,须混长基线对(Ansari 2021)"),
            "max_temporal_baseline": Param(120, kind="science", type="int", min=6, max=730,
                                           hint="时间基线 6-730 天"),
            # 垂直基线阈值(C6):默认 0=不限,对齐 MintPy 上游 perpBaseMax=auto(no)
            # (S1 轨道管 <200 m 天然非约束);stripmap/L 波段场景才需覆写。
            # engines/mintpy.py 渲染 mintpy.network.perpBaseMax(0 → no)
            "max_perp_baseline": Param(0, kind="science", type="int", min=0, max=10000,
                                       hint="垂直基线阈值(米)0-10000;0=不限(MintPy 上游 "
                                            "auto=no);场景参考:ERS 级 130 m(Berardino 2002)"
                                            "、L 波段 ALOS ≤1800 m(Yunjun 2019 §5.1)"),
            "parallel_workers": Param(4, kind="resource", type="int", min=1, max=16),
            # 数据面(MintPy load.processor 语义):默认 HyP3 布局与硬编码逐字相同。
            # ISCE/ARIA/GAMMA 等经场景包或 apply_change 覆写,不改默认值。
            "processor": Param("hyp3", kind="science", type="str",
                               enum=("hyp3", "isce", "aria", "gamma", "gmtsar", "snap",
                                     "roipac", "nisar"),
                               hint="干涉产品的处理器布局(MintPy load.processor 语义)"),
            "unw_pattern": Param("../hyp3/*/*unw_phase_clipped.tif", kind="science", type="str",
                                 hint="解缠相位文件 glob,相对 mintpy/ 工作目录"),
            "cor_pattern": Param("../hyp3/*/*corr_clipped.tif", kind="science", type="str",
                                 hint="相干性文件 glob,相对 mintpy/ 工作目录"),
            "dem_pattern": Param("../hyp3/*/*dem_clipped.tif", kind="science", type="str",
                                 hint="DEM 文件 glob,相对 mintpy/ 工作目录"),
            "inc_pattern": Param("../hyp3/*/*lv_theta_clipped.tif", kind="science", type="str",
                                 hint="入射角文件 glob,相对 mintpy/ 工作目录"),
            "water_pattern": Param("../hyp3/*/*water_mask_clipped.tif", kind="science", type="str",
                                   hint="水掩膜文件 glob,相对 mintpy/ 工作目录"),
            # 解缠误差改正(C 新增):发生在 invert_network 之前,MintPy 上游默认 no。
            # bridging 适合被水体/低相干带分隔的连通域;phase_closure 用闭合环冗余,
            # 需要网络冗余度(pairs 数)支撑;两者可叠加(Yunjun et al. 2019 §4.2)
            "unwrap_error_method": Param(
                "no", kind="science", type="str",
                enum=("no", "bridging", "phase_closure", "bridging+phase_closure"),
                hint="解缠误差改正:no=不改正(上游默认);bridging=连通域桥接;"
                     "phase_closure=闭合环法(需网络冗余);可叠加"),
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
            Method("tropo_opera", "tropo_opera", "mintpy",
                   why="OPERA 对流层产品(需产品已在位)", requires_engines=("mintpy",),
                   extra="cfg: troposphericDelay.method=opera;无产品时运行期诚实失败"),
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
            # 电离层改正(L 波段/长波长场景重要;C 波段短时序通常可忽略)。
            # 诚实声明:split_spectrum 需要 ISCE-2 stack 处理器产出的分频谱干涉对,
            # HyP3 云端路线没有这些输入 —— 选了它,MintPy 会在运行期以真实报错失败,
            # 由 triage 如实呈现;不做静默降级
            "iono_method": Param(
                "no", kind="science", type="str", enum=("no", "split_spectrum"),
                hint="电离层改正:split_spectrum 需 ISCE2 stack 分频谱产物"
                     "(HyP3 路线无此输入,勿选);L 波段(ALOS)建议开启"),
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
                   why="线性 + 年周期 + 半年周期,匹配季节冻融或抽水型沉降",
                   requires_engines=("mintpy",),
                   scenario_only=("permafrost", "subsidence")),
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
            # 速度不确定度(Phase 13 预测的输入):residue=残差传播(上游默认,最快);
            # covariance=时序协方差传播;bootstrap=自助抽样(最稳健,最慢,默认 400 次)
            "uncertainty": Param("residue", kind="science", type="str",
                                 enum=("residue", "covariance", "bootstrap"),
                                 hint="velocityStd 的估计方式;bootstrap 最稳健但慢"),
            "bootstrap_count": Param(400, kind="science", type="int", min=50, max=5000,
                                     hint="仅 uncertainty=bootstrap 时生效"),
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
            # 分色带建议(C8,Crameri 2020 三分类):循环量配非循环色带会在 ±π 处
            # 产生假边界;默认 roma 视作「未显式指定」,由 engines/figures.py 按
            # 产物类型(h5 FILE_TYPE)路由默认色带,显式指定其他值时直通
            "cmap": Param("roma", kind="presentation", type="str",
                          hint="默认按产物类型路由:速度=vik/roma(diverging)、相干=batlow"
                               "(sequential)、缠绕相位=romaO(cyclic);显式指定时直通"),
            "format": Param("png+pdf", kind="presentation", type="str"),
            "figure_set": Param(["velocity"], kind="presentation", type="list",
                                hint="本步要出的图种;场景包可覆写"
                                     "(velocity/coherence/mask/network/points_timeseries 等)"),
            "points_lalo": Param([], kind="science", type="list",
                                 hint="points_timeseries 的采样点 [[lat,lon], ...]"),
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

# ITRF2014-PMM 板块名闭集(拼写 Antartica 对齐 MintPy 上游,不要改成 Antarctica)
_ITRF_PLATES = (
    "Antartica", "Arabia", "Australia", "Eurasia", "India", "Nazca",
    "NorthAmerica", "Nubia", "Pacific", "SouthAmerica", "Somalia",
)

ANALYSIS: tuple[Capability, ...] = (
    Capability(
        id=20, name="分析输入", phase="分析", group="analysis", deps=(),
        methods=(Method("register_sources", "register_sources", "-",
                        why="登记并校验待分析的源产物(不复制、不改写)", recommend=True),),
        default_method="register_sources",
        params={
            # 相对 run 工作区的路径(与第 3 步 stripmap 的路径参数同形态,进指纹)
            "primary": Param("mintpy/velocity.h5", kind="science", type="str",
                             hint="主源产物路径(升轨速度场 / 待分析时序)"),
            "secondary": Param("", kind="science", type="str",
                               hint="次源产物路径(降轨速度场);单源分析留空"),
            "primary_run": Param("", kind="science", type="str",
                                 hint="主源 run_id,仅作溯源记录"),
            "secondary_run": Param("", kind="science", type="str", hint="次源 run_id"),
        },
        artifacts=(
            # register_sources 把 params 路径物化为规范链首(NTFS 硬链接优先、拷贝兜底,
            # 数据是真实的,只是换了规范位置)—— 后续步骤全部读固定的规范路径,零决策
            ArtifactSpec("src_primary", ("analysis/source.h5",), kind="DATA", policy="stat"),
            ArtifactSpec("src_secondary", ("analysis/source_2.h5",), kind="DATA",
                         policy="stat", required=False),
        ),
        run_ok=(RunOkCheck("exit_code", equals=0),
                RunOkCheck("artifact_exists", id="src_primary")),
        timeouts=Timeouts(idle=120, total=600), disk=DiskEstimate("0.01"),
        io="light", replay="safe",
    ),
    Capability(
        id=21, name="掩膜子集", phase="分析", group="analysis", deps=(20,),
        methods=(
            Method("mask_by_coherence", "mask.py --mask maskTempCoh.h5", "mintpy",
                   why="按时相相干掩膜剔除不可信像元", recommend=True,
                   requires_engines=("mintpy",)),
            Method("subset_lalo", "subset.py --lat/--lon", "mintpy",
                   why="裁到研究区,后续统计与出图都更快", requires_engines=("mintpy",)),
            Method("passthrough", "passthrough", "-", why="不做掩膜/裁剪,直接透传上游产物"),
        ),
        default_method="passthrough",
        params={
            "mask_file": Param("mintpy/maskTempCoh.h5", kind="science", type="str"),
            "subset_lat": Param("", kind="science", type="str", hint="如 35.6:36.0,留空=不裁"),
            "subset_lon": Param("", kind="science", type="str", hint="如 -117.9:-117.2"),
        },
        artifacts=(ArtifactSpec("masked", ("analysis/masked.h5",), kind="VELOCITY",
                                layout="mintpy_h5", policy="content"),),
        inputs=("src_primary",),
        run_ok=(RunOkCheck("exit_code", equals=0), RunOkCheck("artifact_exists", id="masked")),
        timeouts=Timeouts(idle=600, total=1800), disk=DiskEstimate("1"), io="medium",
    ),
    Capability(
        id=22, name="速度场校正", phase="分析", group="analysis", deps=(21,),
        methods=(
            Method("passthrough", "passthrough", "-",
                   why="不做速度场级校正,规范链透传", recommend=True),
            # 本机实测存在:mintpy.cli.plate_motion(ITRF2014-PMM 刚性板块运动改正)。
            # 用全球参考框架(如与 GNSS 对比)时必须做;局地相对形变可不做。
            # 双源(升降轨)时对 primary/secondary 各自按其几何改正
            Method("plate_motion_itrf", "plate_motion.py", "mintpy",
                   why="扣除 ITRF 刚性板块运动:长波长速度偏差的主要来源之一",
                   requires_engines=("mintpy",),
                   extra="需几何文件(geometryRadar.h5/geometryGeo.h5)在位"),
        ),
        default_method="passthrough",
        params={
            "plate": Param("", kind="science", type="str",
                           enum=("",) + _ITRF_PLATES,
                           hint="板块名(ITRF2014-PMM;选 plate_motion_itrf 时必填;"
                                "取值 " + "/".join(_ITRF_PLATES) + ";"
                                "拼写 Antartica 对齐 MintPy 上游)"),
        },
        artifacts=(
            ArtifactSpec("corrected", ("analysis/corrected.h5",), kind="DATA", policy="stat"),
            ArtifactSpec("corrected_2", ("analysis/corrected_2.h5",), kind="DATA",
                         policy="stat", required=False),
        ),
        inputs=("masked",),
        run_ok=(RunOkCheck("exit_code", equals=0), RunOkCheck("artifact_exists", id="corrected")),
        timeouts=Timeouts(idle=600, total=1800), disk=DiskEstimate("1"), io="light",
    ),
    Capability(
        id=23, name="几何分解", phase="分析", group="analysis", deps=(22,),
        methods=(
            Method("asc_desc_horz_vert", "asc_desc2horz_vert.py", "mintpy",
                   why="升降轨 LOS 分解为垂直 + 水平(默认东西向)—— InSAR 解译标准动作",
                   recommend=True, requires_engines=("mintpy",),
                   extra="需双源(analysis/corrected.h5 + corrected_2.h5),"
                        "且两源已地理编码到同一分辨率与范围"),
            Method("raster_diff", "diff.py", "mintpy",
                   why="两期/两源相减(变化量、与参考解的差异)", requires_engines=("mintpy",)),
            Method("passthrough", "passthrough", "-", why="单源分析,不做分解"),
        ),
        default_method="passthrough",
        params={
            "horz_az_angle": Param(-90.0, kind="science", min=-180, max=180,
                                   hint="关心的水平方向方位角(度),自北起逆时针为正;"
                                        "-90=东西向(MintPy 上游默认);跨断层可设为断层走向"),
            "use_geometry_files": Param(False, kind="science", type="bool",
                                        hint="用逐像元入射/方位角替代常量元数据"),
        },
        artifacts=(ArtifactSpec("decomposed", ("analysis/decomposed.h5",),
                                kind="VELOCITY", layout="mintpy_h5", policy="content"),),
        inputs=("corrected",),
        run_ok=(RunOkCheck("exit_code", equals=0),
                RunOkCheck("artifact_exists", id="decomposed"),
                RunOkCheck("not_all_nan", id="decomposed")),
        timeouts=Timeouts(idle=900, total=3600), disk=DiskEstimate("2"),
        cpu=4, mem_gb=8, io="medium",
    ),
    Capability(
        id=24, name="统计剖面", phase="分析", group="analysis", deps=(23,),
        methods=(
            Method("spatial_average", "spatial_average.py", "mintpy",
                   why="区域平均(沉降漏斗强度、参考区稳定性)", requires_engines=("mintpy",)),
            Method("temporal_average", "temporal_average.py", "mintpy",
                   why="时段平均(季节项、年际对比)", requires_engines=("mintpy",)),
            Method("transection", "plot_transection.py", "mintpy",
                   why="剖面:跨断层/跨漏斗的形变梯度", recommend=True,
                   requires_engines=("mintpy",)),
            Method("timeseries_rms", "timeseries_rms.py", "mintpy",
                   why="残差 RMS:噪声水平与参考日期选择依据", requires_engines=("mintpy",)),
        ),
        default_method="transection",
        params={
            "start_lalo": Param("", kind="science", type="str", hint="剖面起点 lat,lon"),
            "end_lalo": Param("", kind="science", type="str", hint="剖面终点 lat,lon"),
            "aoi_lalo": Param("", kind="science", type="str",
                              hint="统计区 lat0:lat1,lon0:lon1"),
            "dataset": Param("", kind="science", type="str",
                             hint="h5 内数据集名(分解产物用 vertical/east);空=默认"),
        },
        artifacts=(ArtifactSpec("measure", ("analysis/measure.json", "analysis/transect.txt"),
                                kind="REPORT", policy="content"),),
        inputs=("decomposed",),
        run_ok=(RunOkCheck("exit_code", equals=0), RunOkCheck("artifact_exists", id="measure")),
        timeouts=Timeouts(idle=600, total=1800), disk=DiskEstimate("0.2"),
        io="light", replay="safe",
    ),
    Capability(
        id=25, name="分析出图", phase="分析", group="analysis", deps=(24,),
        methods=(
            Method("view_snapshot", "view.py", "mintpy",
                   why="任意 h5 数据集的标准浏览图(MintPy 官方渲染)",
                   requires_engines=("mintpy",)),
            Method("transection_figure", "plot_transection.py", "mintpy",
                   why="剖面图:形变梯度可视化", requires_engines=("mintpy",)),
            Method("kmz", "save_kmz.py", "mintpy",
                   why="Google Earth 交付(汇报/共享)", requires_engines=("mintpy",)),
            Method("kmz_timeseries", "save_kmz_timeseries.py", "mintpy",
                   why="交互式时序 KMZ(点开看曲线)", requires_engines=("mintpy",),
                   extra="需地理编码时序;文件较大"),
            Method("passthrough", "passthrough", "-",
                   why="本次分析不出图", recommend=True),
        ),
        default_method="passthrough",
        params={
            "input": Param("analysis/decomposed.h5", kind="science", type="str"),
            "dataset": Param("", kind="science", type="str",
                             hint="h5 内数据集名,如 velocity/vertical/east;空=默认"),
            "start_lalo": Param("", kind="science", type="str", hint="transection 起点"),
            "end_lalo": Param("", kind="science", type="str", hint="transection 终点"),
            "dpi": Param(300, kind="presentation", type="int", min=72, max=1200),
            "cmap": Param("", kind="presentation", type="str",
                          hint="空=按产物类型路由(与第 10 步同一色带纪律)"),
        },
        artifacts=(ArtifactSpec("analysis_figures", ("analysis/figures",),
                                kind="FIGURE", policy="stat"),),
        inputs=("measure",),
        run_ok=(RunOkCheck("exit_code", equals=0),
                RunOkCheck("artifact_exists", id="analysis_figures")),
        timeouts=Timeouts(idle=600, total=1800), disk=DiskEstimate("0.3"),
        io="light", replay="safe",
    ),
    Capability(
        id=26, name="变化检测", phase="分析", group="analysis", deps=(24,),
        methods=(
            Method("epoch_diff", "diff.py", "mintpy",
                   why="两历元/两源相减:这两个时期之间发生了什么变化",
                   requires_engines=("mintpy",)),
            Method("quadratic_accel", "timeseries2velocity --poly 2", "mintpy",
                   why="二次项加速度:哪里在加速/减速(|a|/σ_a≥2)",
                   requires_engines=("mintpy",)),
            Method("velocity_compare", "diff.py velocity", "mintpy",
                   why="两个解/两个时段的速度差异", requires_engines=("mintpy",)),
            Method("passthrough", "passthrough", "-",
                   why="本次分析不做变化检测", recommend=True),
        ),
        default_method="passthrough",
        params={
            "epoch1": Param("", kind="science", type="str", hint="epoch_diff 第一历元/文件"),
            "epoch2": Param("", kind="science", type="str", hint="epoch_diff 第二历元/文件"),
            "secondary_velocity": Param("", kind="science", type="str",
                                        hint="velocity_compare 的对比速度场;空=次源 corrected_2"),
        },
        artifacts=(
            ArtifactSpec("change", ("analysis/change.h5",), kind="DATA",
                         policy="stat", required=False),
            ArtifactSpec("change_summary", ("analysis/change_summary.json",),
                         kind="REPORT", policy="content"),
        ),
        inputs=("decomposed",),
        run_ok=(RunOkCheck("exit_code", equals=0),
                RunOkCheck("artifact_exists", id="change_summary")),
        timeouts=Timeouts(idle=900, total=3600), disk=DiskEstimate("1"), io="medium",
    ),
    Capability(
        id=27, name="外推预测", phase="分析", group="analysis", deps=(26,),
        methods=(
            Method("extrapolate_fitted", "extrapolate_fitted", "-",
                   why="只外推第 9 步已落账的时间函数,强制不确定度与有效期"),
            Method("passthrough", "passthrough", "-",
                   why="本次分析不做外推", recommend=True),
        ),
        default_method="passthrough",
        params={
            "horizon_years": Param(1.0, kind="science", min=0.05, max=10,
                                   hint="外推时长(年);默认上限 min(0.5×观测时长, 2 年),"
                                        "超限须填 horizon_override_reason"),
            "horizon_override_reason": Param("", kind="science", type="str",
                                             hint="超上限外推的理由,进账本与报告;空=不允许超限"),
            "points_lalo": Param([], kind="science", type="list",
                                 hint="重点预测点位;空=仅整场统计"),
            "confidence": Param(0.95, kind="science", min=0.5, max=0.999, hint="置信水平"),
        },
        artifacts=(
            ArtifactSpec("prediction", ("analysis/prediction.json",),
                         kind="REPORT", policy="content"),
            ArtifactSpec("prediction_fig", ("analysis/prediction.png",),
                         kind="FIGURE", policy="stat", required=False),
        ),
        inputs=("change_summary",),
        run_ok=(RunOkCheck("exit_code", equals=0),
                RunOkCheck("artifact_exists", id="prediction")),
        timeouts=Timeouts(idle=600, total=1800), disk=DiskEstimate("0.1"),
        io="light", replay="safe",
    ),
    Capability(
        id=28, name="反演桥导出", phase="分析", group="analysis", deps=(24,),
        methods=(
            Method("bridge_gbis", "save_gbis.py", "mintpy",
                   why="导出 GBIS .mat,供贝叶斯形变源反演(Okada/Mogi/Yang)",
                   requires_engines=("mintpy",),
                   extra="需速度/位移场 + 几何文件;不在 Agent 内反演"),
            Method("bridge_kite", "save_kite.py", "mintpy",
                   why="导出 Kite npz/yaml,供 quadtree 降采样与 Grond 反演",
                   requires_engines=("mintpy",)),
            Method("bridge_gmt", "save_gmt.py", "mintpy",
                   why="导出 GMT grd 制图栅格",
                   requires_engines=("mintpy",),
                   extra="输入必须已地理编码(Y_FIRST);不做静默 geocode"),
            Method("bridge_qgis", "save_qgis.py", "mintpy",
                   why="导出 QGIS 时序矢量点",
                   requires_engines=("mintpy",)),
            Method("bridge_hdfeos5", "save_hdfeos5.py", "mintpy",
                   why="导出 HDF-EOS5 存档(UNAVCO 惯例)",
                   requires_engines=("mintpy",)),
            Method("passthrough", "passthrough", "-",
                   why="本次分析不导出", recommend=True),
        ),
        default_method="passthrough",
        params={
            "input": Param("analysis/decomposed.h5", kind="science", type="str",
                           hint="速度/位移场(GBIS/Kite/GMT)"),
            "ts_file": Param("mintpy/timeseries.h5", kind="science", type="str",
                             hint="时序文件(QGIS/HDF-EOS5)"),
            "geom_file": Param("mintpy/inputs/geometryGeo.h5", kind="science", type="str",
                               hint="几何文件;GBIS 必填,其余按 CLI 要求,缺则真实报错"),
            "mask_file": Param("", kind="science", type="str", hint="可选掩膜;空=不掩"),
            "dataset": Param("velocity", kind="science", type="str",
                             hint="h5 内数据集名,Kite 必填(默认 velocity)"),
        },
        artifacts=(ArtifactSpec("bridge_out", ("analysis/bridge",), kind="DATA", policy="stat"),),
        inputs=("measure",),
        run_ok=(RunOkCheck("exit_code", equals=0),
                RunOkCheck("artifact_exists", id="bridge_out")),
        timeouts=Timeouts(idle=600, total=1800), disk=DiskEstimate("0.5"),
        io="light", replay="safe",
    ),
)

REGISTRY: dict[int, Capability] = {c.id: c for c in (*PIPELINE, *ANALYSIS)}


def capability_of(step_id: int) -> Capability:
    return REGISTRY[step_id]


def step_def(step_id: int) -> Capability:
    return REGISTRY[step_id]


def downstream_of(step_id: int) -> list[int]:
    """反向依赖 BFS,不含自身。"""
    rev: dict[int, list[int]] = {}
    for cap in REGISTRY.values():
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
