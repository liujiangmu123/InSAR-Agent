"""处理路线推荐(纯规则第一期):数据集类型 + 环境探测 → 路线优劣对比。

用户目标:「系统读到我的数据类型后,给我各种处理方式的优劣对比,帮我决策」。
本模块是确定性的规则引擎 —— 不调 LLM,输入相同输出必然相同,可直接单测。

输入:
  - dataset:数据集条目(data/catalog.identify_dataset 的输出:kind/detail/
    date_range/file_count/size_bytes/path/name);
  - probe:环境探测结果(runtime/probe.ProbeResult;引擎键可带 " (wsl)" 后缀,
    见 runtime/wsl_probe.merge_wsl_probe 的合并口径)。

输出:按适配度排序的 RouteOption 列表。适配度 = 缺失必需项少者在前,
同缺失数按知识表声明序(主推路线声明在前);排序键确定,结果可复现。

知识映射表(_ROUTES)取材两处,逐条注明出处小节:
  - skills/01..11 各步 SKILL.md(「适用判据 / 参数启发式 / 常见失败与处置」);
  - reference/RESEARCH-insar-step-knowledge-2026-08.md(下称「调研」,
    §1-§11 各步四节 + §12 修正建议 C1-C8)。
数字与结论一律有据:实测数字只引仓库实测(docs/VALIDATION-isce2-wsl.md、
skills/01 的 Ridgecrest 产品体积),不编造时长 —— est_note 只给数据规模与
诚实参照,规模外推风险如实标注。
"""

from __future__ import annotations

from dataclasses import dataclass

from insar_agent.runtime.probe import ProbeResult

#: 场景闭集(registry/scenario_packs 目录名;suitable_scenarios 只能取其子集,
#: tests/test_recommend.py 对照 registry.scenarios.SCENARIOS 做闭集校验)
SCENARIO_KEYS = ("quake", "permafrost", "landslide", "stripmap_coseismic",
                 "subsidence", "volcano", "lt1_gamma", "teaching")


@dataclass(frozen=True)
class RouteOption:
    """一条处理路线的完整决策卡(API 与前端消费 to_dict 的形状)。"""

    route_id: str
    name: str
    suitable_scenarios: tuple[str, ...]  # 场景闭集子集
    pros: tuple[str, ...]                # 各 2-4 条,取材技能文档与调研报告
    cons: tuple[str, ...]
    requirements: tuple[dict, ...]       # {key,label,type,optional,ok}
    steps_involved: tuple[int, ...]      # 涉及的流水线步骤号
    est_note: str                        # 数据规模的诚实提示(不编时长)
    ready: bool                          # 必需项全部满足
    missing: tuple[str, ...]             # 缺失的必需项 key(前端黄徽章文案)

    def to_dict(self) -> dict:
        return {
            "route_id": self.route_id,
            "name": self.name,
            "suitable_scenarios": list(self.suitable_scenarios),
            "pros": list(self.pros),
            "cons": list(self.cons),
            "requirements": [dict(r) for r in self.requirements],
            "steps_involved": list(self.steps_involved),
            "est_note": self.est_note,
            "ready": self.ready,
            "missing": list(self.missing),
        }


def _eng(key: str, label: str, *, optional: bool = False) -> dict:
    """引擎需求声明(对照 probe.engines;WSL 形态的 'xxx (wsl)' 键同样算满足)。"""
    return {"key": key, "label": label, "type": "engine", "optional": optional}


def _cred(key: str, label: str, *, optional: bool = False) -> dict:
    """凭据需求声明(对照 probe.credentials)。"""
    return {"key": key, "label": label, "type": "credential", "optional": optional}


# ---------------- 知识映射表(kind → 路线,声明序 = 主推序) ----------------
#
# 每条 pros/cons 末尾括注出处:skills/NN = skills/NN-*/SKILL.md 的小节;
# 调研 §N/CN = reference/RESEARCH-insar-step-knowledge-2026-08.md 的小节/修正号。

_ROUTES: dict[str, tuple[dict, ...]] = {
    "hyp3": (
        {
            "route_id": "hyp3_direct",
            "name": "HyP3 产品直通时序",
            "scenarios": ("quake", "permafrost"),
            "steps": (1, 7, 8, 9, 10, 11),
            "pros": (
                "免 2-6 步本机算力:配准/干涉/滤波/解缠已由 ASF 云端 GAMMA 链完成,"
                "产品直通第 7 步时序反演(skills/01 适用判据;quake 包 cloud_completed=[2-6])",
                "磁盘占用小:Ridgecrest 实测 11 对产品仅 402 MB,远小于 SLC 全链"
                "(skills/01 常见失败 5)",
                "产品自带解缠相位与相干图(*_unw_phase/*_corr),MintPy 直接装载为"
                "第 7 步输入(capabilities 第 1 步 unw artifact;skills/07 适用判据)",
            ),
            "cons": (
                "失去 2-6 步中间产物控制权:不能重滤波、不能换解缠参数"
                "(skills/01 hyp3_submit 判据)",
                "限 VV 极化,多视固定 20×4(80 m 默认档)或 10×2,档位不可自定"
                "(HyP3 Product Guide;调研 §1.1)",
                "全幅产品不输出连通分量文件(conncomp),解缠质检手段受限(调研 §6.1)",
                "进不了 PS 链:PS 必须从 SLC 起算(skills/01 适用判据)",
            ),
            "requires": (
                _eng("mintpy", "MintPy(第 7 步时序反演)"),
                _eng("pyaps", "PyAPS(第 8 步 ERA5 大气校正)", optional=True),
                _eng("gdal", "GDAL(第 10 步 GeoTIFF 导出)", optional=True),
            ),
        },
        {
            "route_id": "slc_reacquire",
            "name": "重取 SLC 走全链",
            "scenarios": ("landslide", "permafrost", "quake"),
            "steps": (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11),
            "pros": (
                "恢复 2-6 步全部控制权:多视比/滤波强度/解缠代价函数逐步可调"
                "(skills/01 asf_search_slc 判据)",
                "可走 PS 链或自定 SBAS 网络,摆脱 HyP3 固定产参限制"
                "(skills/01;调研 §4.2 网络设计)",
            ),
            "cons": (
                "需重新下载原始 SLC:磁盘按 2.4 GB/景预算,调研提示未压缩单景实际"
                "7-8 GB,预算偏低须留裕量(调研 C4)",
                "需 Earthdata 凭据与 ISCE2+SNAPHU 引擎,下载+处理以小时计"
                "(第 1 步 total 超时 6 h;调研 §1.3)",
                "已有 HyP3 产品的字节不被复用,前期成本沉没",
            ),
            "requires": (
                _eng("isce2", "ISCE2(3-6 步配准/干涉/滤波)"),
                _eng("snaphu", "SNAPHU(第 6 步解缠)"),
                _eng("mintpy", "MintPy(第 7 步时序反演)"),
                _cred("earthdata", "Earthdata 凭据(ASF 下载认证)"),
            ),
        },
    ),
    "slc_stack": (
        {
            "route_id": "full_chain_s1",
            "name": "Sentinel-1 全链",
            "scenarios": ("quake", "permafrost", "landslide"),
            "steps": (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11),
            "pros": (
                "全程参数控制:多视比/滤波强度/解缠代价函数每步可调,中间产物齐备"
                "可复查(skills/01 asf_search_slc 判据)",
                "SBAS 网络自主设计:时空基线剪枝并混入长基线对,规避纯短基线"
                "fading 系统偏差(调研 §4.1,Ansari 2021)",
                "可复现性最好:每步产物+指纹落盘,支持步骤级重跑与 fork 试参"
                "(本系统 stale/fork 机制)",
            ),
            "cons": (
                "需 ISCE2+SNAPHU+MintPy 全套引擎(3-6 步 isce2/snaphu,第 7 步 mintpy)",
                "算力与磁盘开销大:配准/干涉 io=heavy,磁盘峰值系数 1.4-1.6"
                "(capabilities 第 3/4 步声明)",
                "步骤多耗时长:单步 total 超时上限 4-8 h 量级(registry 声明;"
                "实际耗时未实测,不做承诺)",
            ),
            "requires": (
                _eng("isce2", "ISCE2(3-6 步配准/干涉/滤波)"),
                _eng("snaphu", "SNAPHU(第 6 步解缠)"),
                _eng("mintpy", "MintPy(第 7 步时序反演)"),
                _eng("pyaps", "PyAPS(第 8 步 ERA5 大气校正)", optional=True),
            ),
        },
        {
            "route_id": "ps_chain_s1",
            "name": "PS 永久散射体链",
            "scenarios": ("landslide",),
            "steps": (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11),
            "pros": (
                "高相干点目标精度上限最高:PS 速度精度可达 <1 mm/yr"
                "(Ferretti 2001;skills/07 适用判据)",
                "点目标相位不做空间滤波,保住人工地物/裸岩的真实信号"
                "(skills/05:PS 链不滤波)",
            ),
            "cons": (
                "要求长时序:PS 候选可靠估计需 ≥20-25 景,短时序结果不可信"
                "(skills/07;调研 §1.1 Crosetto 2016)",
                "工程边界:ISCE2→PyStamps 桥已实现(par 几何自洽 / TCN 基线 / "
                "big-endian 三个难点都在 engines/bridges/ 里落地,合成布局有测试),"
                "但尚未在真实 ISCE2 输出上跑通;输入不完整时桥显式 EnvironmentNotReady "
                "而非猜测,规划器则按 pystamps 可用性收窄回退 SBAS 单链"
                "(skills/07 适用判据)",
                "因此双链交叉验证的 crossval_r 仍缺席、阈值台账为 PENDING,"
                "证据阶梯到不了 validated 级(第 11 步质量门只警告不拦停)",
                "只适合高相干点状目标;面状低相干区(农田/植被)应走 SBAS"
                "(skills/07 选链判据)",
            ),
            "requires": (
                _eng("isce2", "ISCE2(3-6 步配准/干涉)"),
                _eng("pystamps", "PyStamps(第 7 步 PS 反演,需 ISCE2 桥)"),
                _eng("mintpy", "MintPy(交叉验证的 SBAS 对照链)"),
            ),
        },
        {
            "route_id": "hyp3_cloud",
            "name": "HyP3 云端重处理",
            "scenarios": ("quake", "permafrost"),
            "steps": (1, 7, 8, 9, 10, 11),
            "pros": (
                "免本机 2-6 步引擎与算力:云端 GAMMA 链代跑,适合快速响应与教学演示"
                "(skills/01 hyp3_submit 判据)",
                "产品自带解缠相位,取回后直通第 7 步(同 HyP3 直通路线)",
            ),
            "cons": (
                "需 Earthdata 凭据且受 ASF 月度配额限制(capabilities 第 1 步 extra;"
                "skills/01 常见失败 3)",
                "失去中间产物控制权、限 VV 极化(skills/01 hyp3_submit 判据)",
                "本地已有 SLC 字节不被复用:云端从 ASF 档案按 granule 重新处理",
            ),
            "requires": (
                _eng("mintpy", "MintPy(第 7 步时序反演)"),
                _cred("earthdata", "Earthdata 凭据(HyP3 提交与取回)"),
            ),
        },
    ),
    "alos_raw": (
        {
            "route_id": "stripmap_alos",
            "name": "ALOS 条带链",
            "scenarios": ("stripmap_coseismic",),
            "steps": (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11),
            "pros": (
                "L 波段(23.6 cm)穿透植被,植被区相干显著好于 C 波段"
                "(skills/01 platform 判据)",
                "大梯度同震形变不易失相干,近场信号保留好"
                "(stripmap_coseismic 场景定位;skills/03 适用判据)",
                "本仓库已实测打通:WSL ISCE2 条带链全链通过"
                "(docs/VALIDATION-isce2-wsl.md,2026-08 Baja 实测)",
            ),
            "cons": (
                "需 WSL ISCE2 环境:条带链仅在 WSL 实测,Windows 原生未验证"
                "(docs/VALIDATION-isce2-wsl.md)",
                "stripmapApp pickle 连续性约束:3-6 步分段续跑、段内不可任意跳步,"
                "断链须整段重跑(skills/03 常见失败 5;capabilities 第 6 步注释)",
                "多视比须倒置(az>rg):默认 10×2 是 S1 口径,直接沿用会矩形化像元"
                "(调研 C3)",
                "L 波段电离层影响更强,严格场景需分频谱校正(调研 §8.1,Gomba 2016)",
            ),
            "requires": (
                # 条带链解缠由 stripmapApp 内置 snaphu 驱动,不需要独立 snaphu
                # 可执行(capabilities 第 6 步 isce2_stripmap_unwrap_snaphu 注释)
                _eng("isce2", "ISCE2(条带链 3-6 步;本机为 WSL 环境实测)"),
                _eng("mintpy", "MintPy(第 7 步时序反演;单对为形式化通道)"),
            ),
        },
    ),
    "nisar": (
        {
            "route_id": "nisar_gunw",
            "name": "NISAR GUNW 直通时序",
            "scenarios": ("quake", "permafrost", "teaching"),
            "steps": (1, 7, 8, 9, 10, 11),
            "pros": (
                "GUNW 已是云端解缠干涉产品,第 1 步 nisar_import 只登记不下载,"
                "2-6 步按云端已完成跳过(capabilities 第 1 步 nisar_import)",
                "L 波段穿透植被,低相干区相对 Sentinel-1 C 波段更稳"
                "(52 号研究 §1.1;skills/01 platform 判据)",
                "免费开放后与 HyP3 直通同构:产品进第 7 步 MintPy 时序"
                "(skills/07 适用判据)",
            ),
            "cons": (
                "失去 2-6 步中间产物控制权,不能在本机重滤波或换解缠参数"
                "(与 HyP3 直通同一代价)",
                "GUNW 样例全链 7-11 尚未在本仓库用真实产品实测,规划可走、"
                "验收未完成(52 R2a)",
                "S 波段/GOFF 偏移产品不在本路线;大梯度近场应另评估模式 C"
                "(52 §2.1 偏移量追踪不做清单)",
            ),
            "requires": (
                _eng("mintpy", "MintPy(第 7 步时序反演)"),
                _eng("pyaps", "PyAPS(第 8 步 ERA5 大气校正)", optional=True),
            ),
        },
    ),
    "gamma": (
        {
            "route_id": "lt1_gamma_layout",
            "name": "GAMMA/LT-1 布局接入",
            "scenarios": ("lt1_gamma", "subsidence", "landslide"),
            "steps": (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11),
            "pros": (
                "吃 SARscape/GAMMA 导出的 .par+.diff/.mli/.rslc 布局,"
                "国产陆探一号不另造引擎(lt1_gamma 场景包;prep_gamma)",
                "L 波段双星重访适合沉降/滑坡;场景包标注 processor=gamma"
                "(52 R2c)",
            ),
            "cons": (
                "本仓不内置 GAMMA 商业许可,只消费其导出布局;缺 .par 会判成"
                "其它 kind 或 unknown(data/catalog.py)",
                "「用陆探一号做沉降」会先命中 subsidence 包,这是歧义消解不是漏检"
                "(52 §8.4)",
                "电离层在 L 波段更强,严格场景需分频谱校正(调研 §8.1)",
            ),
            "requires": (
                _eng("mintpy", "MintPy(第 7 步时序反演)"),
                _eng("gdal", "GDAL(布局核验/导出)", optional=True),
            ),
        },
    ),
    "displacement": (
        {
            "route_id": "mode_c_analysis",
            "name": "位移产品直接分析",
            "scenarios": ("teaching", "subsidence", "quake"),
            "steps": (20, 21, 22, 23, 24, 25),
            "pros": (
                "模式 C:OPERA DISP / EGMS / 官方形变场不走 1-11 主链,"
                "零重型干涉计算(52 §1.2;planner pipeline=analysis)",
                "register_sources 已吃 GeoTIFF/CSV/HDF5,分钟级出剖面/分解/报告"
                "(engines/passthrough.py)",
                "适合课堂与快速可行性评估,不假装 PS 双链(teaching 包)",
            ),
            "cons": (
                "没有 2-6 步中间产物,不能回溯解缠或换大气模型",
                "产品级别默认 Basic(相对 LOS);GNSS 锚定才升 Calibrated"
                "(52 §3.1 / product_level)",
                "源必须是位移场而非任意 PNG;格式不支持会显式失败,不静默当空",
            ),
            "requires": (
                _eng("gdal", "GDAL/rasterio(GeoTIFF→分析输入)", optional=True),
            ),
        },
    ),
    "dem": (
        {
            "route_id": "dem_only",
            "name": "仅地形,需配主数据",
            "scenarios": ("quake", "permafrost", "landslide", "stripmap_coseismic"),
            "steps": (2,),
            "pros": (
                "免在线下载:第 2 步 dem_local 直接使用本地 DEM,无网/内网环境可用"
                "(capabilities 第 2 步 dem_local)",
                "配主数据后即插即用:同一份 DEM 可服务多个数据集的第 2 步",
            ),
            "cons": (
                "单独不构成处理路线:必须搭配主数据(HyP3 产品 / SLC 栈 / ALOS raw)",
                "须核对高程基准:公开 DEM 常为 EGM 大地水准面,InSAR 需 WGS84 椭球高,"
                "漏改正会引入平滑的假形变坡面(调研 §2.2;skills/02)",
            ),
            "requires": (
                _eng("gdal", "GDAL(DEM 核验/重投影,可选)", optional=True),
            ),
        },
    ),
    "unknown": (
        {
            "route_id": "identify_first",
            "name": "先识别数据类型",
            "scenarios": (),
            "steps": (),
            "pros": (
                "重扫描秒级完成:识别只读文件名与 stat 元数据,零数据 IO 压力"
                "(data/catalog.py 判据)",
                "类型一旦识别,即可获得该类数据的完整路线优劣对比",
            ),
            "cons": (
                "当前目录未匹配任何已知数据形态(HyP3 / ALOS raw / SLC / DEM / "
                "NISAR GUNW / GAMMA / 位移产品),无法给出处理路线",
                "按错误类型强行处理,会在装载/配准阶段以更高代价失败"
                "(skills/01 常见失败 4)",
            ),
            "requires": (),
        },
    ),
}


# ---------------- 数据规模的诚实提示(est_note) ----------------

def _fmt_gb(size_bytes: int | None) -> str:
    if not size_bytes:
        return "体积未知"
    gb = size_bytes / (1 << 30)
    return f"{gb:.1f} GB" if gb >= 0.1 else f"{size_bytes / (1 << 20):.0f} MB"


def _est_note(route_id: str, dataset: dict) -> str:
    """按路线与数据集条目生成规模提示。纪律:不编时长 —— 只报数据里数得出来的
    规模、registry 声明的预算公式、以及仓库实测参照(注明规模外推风险)。"""
    detail = dataset.get("detail") or {}
    if route_id == "hyp3_direct":
        pairs = detail.get("pairs", 0)
        return (f"当前 {pairs} 对干涉、共 {_fmt_gb(dataset.get('size_bytes'))};"
                "第 7 步反演规模随对数与像元数增长。参照:Ridgecrest 11 对产品实测 "
                "402 MB(skills/01);本机反演耗时未实测,不估时长")
    if route_id == "slc_reacquire":
        return ("重取 SLC 磁盘按 2.4 GB/景预算(调研 C4:未压缩单景实际 7-8 GB,"
                "预算偏低须留裕量);景数按目标场景另定:同震 2 景起,SBAS ≥15-20,"
                "PS ≥20-25(skills/01 参数启发式);耗时未实测")
    if route_id in ("full_chain_s1", "ps_chain_s1"):
        n = (detail.get("slc") or 0) + (detail.get("safe") or 0)
        base = (f"当前 {n} 景 SLC、共 {_fmt_gb(dataset.get('size_bytes'))};"
                f"磁盘按 scenes×2.4 GB 预算 ≈{n * 2.4:.0f} GB"
                "(调研 C4:实际单景 7-8 GB,须留裕量)")
        if route_id == "ps_chain_s1":
            gate = ("满足 PS 景数下界" if n >= 20
                    else "低于 PS 下界,建议补数据或走 SBAS")
            return f"{base};PS 可靠估计需 ≥20-25 景,当前 {n} 景{gate}(skills/07)"
        return f"{base};SBAS 网络冗余建议 ≥15-20 景(skills/01);全链耗时未实测"
    if route_id == "hyp3_cloud":
        return ("提交粒度按景对组合、受 ASF 月度配额约束;云端排队与处理时长不受"
                "本机控制,未实测不估时长(skills/01 hyp3_submit)")
    if route_id == "stripmap_alos":
        scenes = detail.get("scenes", 0)
        return (f"当前 {scenes} 组 IMG/LED 场景、共 {_fmt_gb(dataset.get('size_bytes'))};"
                "唯一实测参照:2 景 Baja 全链 33 min/峰值磁盘 32 GB"
                "(docs/VALIDATION-isce2-wsl.md),规模不同勿线性外推")
    if route_id == "dem_only":
        n = len(detail.get("dem_files") or [])
        return (f"{n} 个 DEM 文件;识别只看扩展名,分辨率与覆盖范围需对照主数据 "
                "AOI 自行核对(skills/02 适用判据)")
    if route_id == "nisar_gunw":
        gunw = detail.get("gunw", 0)
        h5 = detail.get("h5", 0)
        return (f"当前识别到 GUNW {gunw}、HDF5/NetCDF {h5}、"
                f"共 {_fmt_gb(dataset.get('size_bytes'))};"
                "GUNW 为云端解缠产品,本机只登记。真实 NISAR 全链耗时未实测,不估时长")
    if route_id == "lt1_gamma_layout":
        par = detail.get("par", 0)
        return (f"当前 {par} 个 .par、共 {_fmt_gb(dataset.get('size_bytes'))};"
                "按 GAMMA 导出布局接入,不内置商业许可。全链耗时未实测")
    if route_id == "mode_c_analysis":
        tif = detail.get("tif", 0)
        csv = detail.get("csv", 0)
        return (f"位移产品 {tif} 个 GeoTIFF / {csv} 个 CSV、"
                f"共 {_fmt_gb(dataset.get('size_bytes'))};"
                "分析链 20-25 不跑干涉,耗时随栅格尺寸变化,未编时长")
    # identify_first:给出判型特征,引导用户整理目录后重扫
    return ("整理目录后点「重新扫描」:HyP3 含 *_unw_phase.tif/*_corr.tif;"
            "ALOS raw 含 IMG-*/LED-*;SLC 含 *.slc 或 *.SAFE;"
            "DEM 含 *.dem/*.wgs84/*.grd;NISAR 含 *GUNW*.h5;"
            "GAMMA 含 *.par 与 .diff/.mli/.rslc;"
            "位移产品为 EGMS/DISP 的 tif/csv(data/catalog.py 判据)")


# ---------------- 需求核对与排序 ----------------

def _req_ok(req: dict, probe: ProbeResult) -> bool:
    """单条需求对照 probe:凭据查 credentials;引擎查原生键或 WSL 合并键
    ('xxx (wsl)',runtime/wsl_probe.merge_wsl_probe 的口径)。"""
    if req["type"] == "credential":
        return bool(probe.credentials.get(req["key"]))
    return probe.engine_ok(req["key"]) or probe.engine_ok(f"{req['key']} (wsl)")


def recommend_routes(dataset: dict, probe: ProbeResult) -> list[RouteOption]:
    """数据集条目 + 环境探测 → 按适配度排序的路线选项。

    排序键(确定、可复现):(缺失必需项数, 知识表声明序)。缺失少者在前;
    全满足的主推路线永远排第一,环境残缺时「当下能跑」的路线自然上浮。
    """
    kind = dataset.get("kind") or "unknown"
    templates = _ROUTES.get(kind, _ROUTES["unknown"])
    options: list[tuple[int, int, RouteOption]] = []
    for rank, t in enumerate(templates):
        requirements = []
        missing = []
        for req in t["requires"]:
            ok = _req_ok(req, probe)
            requirements.append({**req, "ok": ok})
            if not ok and not req["optional"]:
                missing.append(req["key"])
        option = RouteOption(
            route_id=t["route_id"],
            name=t["name"],
            suitable_scenarios=tuple(t["scenarios"]),
            pros=tuple(t["pros"]),
            cons=tuple(t["cons"]),
            requirements=tuple(requirements),
            steps_involved=tuple(t["steps"]),
            est_note=_est_note(t["route_id"], dataset),
            ready=not missing,
            missing=tuple(missing),
        )
        options.append((len(missing), rank, option))
    options.sort(key=lambda x: (x[0], x[1]))
    return [o for _miss, _rank, o in options]
