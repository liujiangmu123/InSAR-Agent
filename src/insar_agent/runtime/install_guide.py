"""引擎安装指引知识表(纯数据 + 纯函数;本模块与其上的 API 绝不执行任何安装)。

用户目标「可以进行环境配置,看缺少什么环境」的补全件:probe.py 只回答
「缺什么」,本模块回答「怎么装」—— 每个引擎给出可复制执行的安装途径。
命令口径与仓库实测一致,不编造包名:
  - WSL 途径对照 scripts/wsl_setup.sh 与 docs/WSL-SETUP.md(2026-08-13 实测:
    isce2=2.6.5 钉定、snaphu 走 apt、libblas=openblas 校验、哨兵幂等);
  - Windows 本机 conda 途径对照 api/setup_router.py 的 miniforge 口径与
    README「环境坑位记录」(MKL 2024 硬崩 → 必切 OpenBLAS);
  - 无官方分发渠道的引擎(pystamps/snap)如实标注 manual 途径,占位符
    显式写成 <...>,不假装存在 conda/PyPI 包。

安全边界:安装是用户主权操作(重型计算管控)—— 这里只产出命令文本供
用户复制到自己的终端执行;api/install_router.py 同样只读,无代跑端点。

对话联动:install_hint_for(engine) 是无状态纯函数,输入引擎名返回一句话
途径摘要 —— 对话大脑(brain/facade)后续可把它注入为提示词素材或工具,
本模块不做接线,不 import brain/driver。
"""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Mapping

if TYPE_CHECKING:  # 仅类型标注:知识表不依赖探测实现,保持纯数据可独立测试
    from insar_agent.runtime.probe import ProbeResult

# ---------------- 常量(镜像与下载源,与 setup_router / wsl_setup.sh 同源) ----------------

_TUNA_CONDA_FORGE = "https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/conda-forge/"
_MINIFORGE_TUNA_WIN = ("https://mirrors.tuna.tsinghua.edu.cn/github-release/conda-forge/"
                       "miniforge/LatestRelease/Miniforge3-Windows-x86_64.exe")
_ROOTFS_URL = ("https://cloud-images.ubuntu.com/wsl/noble/current/"
               "ubuntu-noble-wsl-amd64-wsl.rootfs.tar.gz")

#: 七引擎的呈现顺序(与 probe.py 的探测集一致;mintpy/gdal 是 7-9 步必需项,排前)
ENGINE_ORDER: tuple[str, ...] = (
    "mintpy", "gdal", "snaphu", "isce2", "pyaps", "pystamps", "snap")

#: 必需引擎(与 api/setup_router.py 的 engines_ok 判定同口径:缺失会拦 ready)
REQUIRED_ENGINES: frozenset[str] = frozenset({"mintpy", "gdal"})

#: 前置条件闭集:route["requires"] 只允许引用这里的键。
#: met 判定见 _requirement_state:wsl2 可机器判定,conda 两项探测不到,
#: 前端按「请自行确认」呈现(诚实缺席,不假装知道)。
REQUIREMENTS: dict[str, str] = {
    "wsl2": "WSL2 可用(wsl --version;缺失先装 wsl --install,装完需重启)",
    "conda_windows": "Windows 本机 Miniforge/conda(建议装到 E:\\miniforge3)",
    "conda_env_insar": "conda 引擎环境 insar(如 E:\\miniforge3\\envs\\insar;"
                       "没有就先走 mintpy 的 conda 途径建环境)",
}

# ---------------- 共用途径模板 ----------------

#: WSL 一键途径:isce2/mintpy/snaphu/gdal/pyaps 共享同一套实测步骤
#: (scripts/wsl_setup.ps1 → wsl_setup.sh 幂等四步,验证走 wsl_probe)。
_WSL_STEPS: tuple[str, ...] = (
    "wsl --version",
    f"下载 Ubuntu 24.04 WSL rootfs:{_ROOTFS_URL}",
    r"wsl --import insar E:\wsl\insar <rootfs.tar.gz 下载路径> --version 2",
    r"powershell -NoProfile -ExecutionPolicy Bypass -File scripts\wsl_setup.ps1",
    r".venv\Scripts\python.exe -m insar_agent.runtime.wsl_probe --distro insar",
)
_WSL_COMMON_NOTES: tuple[str, ...] = (
    "发行版 vhdx 必须落 E: 盘(C: 空间不足,docs/WSL-SETUP.md §目标环境)",
    "脚本幂等可重跑:断网/中断后直接重跑续装(哨兵 /opt/.insar_setup_done_N)",
    "一次装齐 isce2=2.6.5 + mintpy + snaphu + gdal + pyaps3(清华 conda-forge 镜像)",
    "详见 docs/WSL-SETUP.md(含验证步骤与故障排查表)",
)


def _wsl_route(*extra_notes: str, est_minutes: int = 40, disk_gb: float = 12.0) -> dict:
    return {
        "route": "wsl",
        "title": "WSL 一键脚本(scripts/wsl_setup.ps1,已实测)",
        "steps": list(_WSL_STEPS),
        "est_minutes": est_minutes,
        "disk_gb": disk_gb,
        "notes": [*extra_notes, *_WSL_COMMON_NOTES],
        "requires": ["wsl2"],
    }


#: Windows 本机 conda 途径的公共尾步:MKL→OpenBLAS(README 坑位)+ 固化配置
_CONDA_OPENBLAS_CMD = (f"conda install -n insar -c {_TUNA_CONDA_FORGE} "
                       '--override-channels "libblas=*=*openblas" -y')
_CONDA_MKL_NOTE = ("必做:BLAS 切 OpenBLAS —— conda-forge Windows 默认 MKL,"
                   "MKL 2024 在 i9-13900K 上多线程延迟加载硬崩(0xC06D007F,"
                   "README「环境坑位记录」)")

# ---------------- 知识表本体 ----------------

#: 引擎 → {label, hint, rationale: {route_id: 推荐理由}, routes: [途径...]}。
#: 途径字段:route("conda"|"wsl"|"manual") / title / steps(一行一条,命令保持
#: 纯命令可直接复制,操作类步骤用中文短句) / est_minutes / disk_gb / notes(坑位)
#: / requires(前置,引用 REQUIREMENTS 闭集)。routes 的声明顺序 = 推荐优先序。
INSTALL_GUIDE: dict[str, dict] = {
    "mintpy": {
        "label": "MintPy(SBAS 时序反演,7-9 步引擎)",
        "hint": ("Windows 本机 conda 最快:miniforge 装好后 conda create -n insar "
                 "python=3.11 mintpy(清华镜像)+ BLAS 切 OpenBLAS;"
                 "或随 WSL 一键脚本与 isce2 一起装齐"),
        "rationale": {
            "conda": "Windows 本机 conda 环境最快可验证(无需 WSL),gdal/pyaps3 随依赖一并到位",
            "wsl": "要跑 ISCE2 本地全链时,WSL 一键脚本把 mintpy 与 isce2/snaphu/gdal 一次装齐",
        },
        "routes": [
            {
                "route": "conda",
                "title": "Windows 本机 conda(miniforge,与向导同口径)",
                "steps": [
                    ("下载并安装 Miniforge(建议装到 E:\\miniforge3;已有 conda 可跳过):"
                     f"{_MINIFORGE_TUNA_WIN}"),
                    (f"conda create -n insar -c {_TUNA_CONDA_FORGE} "
                     "--override-channels python=3.11 mintpy -y"),
                    _CONDA_OPENBLAS_CMD,
                    ("把引擎环境路径 E:\\miniforge3\\envs\\insar 在环境向导第 3 步"
                     "保存为 engine_prefix(或 POST /api/setup/save)"),
                ],
                "est_minutes": 20,
                "disk_gb": 6.0,
                "notes": [
                    _CONDA_MKL_NOTE,
                    "gdal/pyaps3 作为 mintpy 依赖自动安装",
                    "对应 README 已验证环境(MintPy 1.6.4 + GDAL 3.13.2,conda-forge)",
                ],
                "requires": [],
            },
            _wsl_route("MintPy 在 WSL 里随 conda env insar 一并安装(实测 1.6.4)"),
        ],
    },
    "gdal": {
        "label": "GDAL(栅格读写,导入/出图依赖)",
        "hint": ("GDAL 随 mintpy 的 conda 包自动安装;单独补装:conda install -n insar "
                 "gdal(清华 conda-forge 镜像);WSL 一键脚本也会装"),
        "rationale": {
            "conda": "GDAL 随 mintpy 的 conda 包自动安装;已有环境时单独补装一条命令即可",
            "wsl": "WSL 一键脚本包含 gdal(实测 3.10.3,PROJ_DATA 已由 profile 配好)",
        },
        "routes": [
            {
                "route": "conda",
                "title": "Windows 本机 conda(随 MintPy 环境,或单独补装)",
                "steps": [
                    ("下载并安装 Miniforge(建议装到 E:\\miniforge3;已有 conda 可跳过):"
                     f"{_MINIFORGE_TUNA_WIN}"),
                    (f"conda create -n insar -c {_TUNA_CONDA_FORGE} "
                     "--override-channels python=3.11 mintpy -y"),
                    _CONDA_OPENBLAS_CMD,
                    ("把引擎环境路径 E:\\miniforge3\\envs\\insar 在环境向导第 3 步"
                     "保存为 engine_prefix(或 POST /api/setup/save)"),
                ],
                "est_minutes": 20,
                "disk_gb": 6.0,
                "notes": [
                    ("已有 insar 环境时只需单独补装:"
                     f"conda install -n insar -c {_TUNA_CONDA_FORGE} --override-channels gdal -y"),
                    _CONDA_MKL_NOTE,
                ],
                "requires": [],
            },
            _wsl_route("GDAL 在 WSL 里随 conda env insar 一并安装(实测 3.10.3)"),
        ],
    },
    "snaphu": {
        "label": "SNAPHU(相位解缠,第 6 步引擎)",
        "hint": ("推荐 WSL 途径:apt 版 snaphu 2.0.6 提供 PATH 可执行(scripts/wsl_setup.ps1 "
                 "一键装);conda-forge 的 snaphu 是 snaphu-py 包装器,可能不含命令行可执行"),
        "rationale": {
            "wsl": ("WSL 途径已实测:apt 版 snaphu 2.0.6 提供 /usr/bin/snaphu;"
                    "conda-forge 的 snaphu 包是 snaphu-py 包装器,可能不往 PATH 装可执行"),
            "conda": ("本机 conda 一条命令,但 conda-forge 的 snaphu 可能是 snaphu-py 包装器"
                      "(装完务必复检);复检仍缺失就改走 WSL 途径"),
        },
        "routes": [
            _wsl_route("snaphu 走 apt(universe 2.0.6):wsl_setup.sh 步骤 1 安装,"
                       "实测 /usr/bin/snaphu;isce2/snaphu 作业本就路由到 WSL 执行"),
            {
                "route": "conda",
                "title": "Windows 本机 conda(装进已有 insar 环境)",
                "steps": [
                    (f"conda install -n insar -c {_TUNA_CONDA_FORGE} "
                     "--override-channels snaphu -y"),
                    "复检:环境面板「刷新」或本抽屉「我装好了,重新检测」",
                ],
                "est_minutes": 5,
                "disk_gb": 0.5,
                "notes": [
                    ("坑位:Linux 侧实测(2026-08-13)conda-forge 的 snaphu 已是 snaphu-py "
                     "包装器(0.4.x),不往 PATH 装可执行 —— 装完复检仍缺失就改走 WSL 途径"),
                    "HyP3 云端路线(2-6 步云端完成)不需要本地解缠,可不装",
                ],
                "requires": ["conda_windows", "conda_env_insar"],
            },
        ],
    },
    "isce2": {
        "label": "ISCE2(干涉处理 2-6 步引擎,topsApp/stripmapApp)",
        "hint": (r"仅支持 WSL/Linux(conda-forge 无 Windows 包):scripts\wsl_setup.ps1 "
                 "一键安装到发行版 insar(isce2=2.6.5 钉定,约 40 分钟)"),
        "rationale": {
            "wsl": ("唯一途径:isce2 无 Windows 原生包(conda-forge 仅 Linux/macOS),"
                    "必须装在 WSL 发行版内;isce2 作业本就由后端路由到 WSL 执行"),
        },
        "routes": [
            _wsl_route(
                "isce2 只支持 WSL/Linux:宿主探测不到是正常的,装好后引擎键显示为 isce2 (wsl)",
                "版本钉定 isce2=2.6.5(S1C/S1D 支持;升级须先过 "
                "docs/VALIDATION-isce2-wsl.md 验证矩阵)",
                "isce2 钉死 numpy<2(上游约束):不要在该 env 里手动升 numpy",
            ),
        ],
    },
    "pyaps": {
        "label": "pyAPS3(ERA5 对流层校正,第 8 步依赖)",
        "hint": ("conda install -n insar pyaps3(清华镜像;通常随 mintpy 依赖已装);"
                 "WSL 一键脚本也会装;数据源含缓存 ERA5.h5 时可免装"),
        "rationale": {
            "conda": "一条 conda 命令补装(通常随 mintpy 依赖已装,缺失多半是环境不完整)",
            "wsl": "WSL 一键脚本包含 pyaps3(实测 0.3.7)",
        },
        "routes": [
            {
                "route": "conda",
                "title": "Windows 本机 conda(装进已有 insar 环境)",
                "steps": [
                    (f"conda install -n insar -c {_TUNA_CONDA_FORGE} "
                     "--override-channels pyaps3 -y"),
                    "复检:环境面板「刷新」或本抽屉「我装好了,重新检测」",
                ],
                "est_minutes": 5,
                "disk_gb": 0.5,
                "notes": [
                    "通常随 mintpy 依赖已装;数据源含缓存 ERA5.h5 时对流层校正可免装",
                    "在线拉取 ERA5 需要 CDS 凭据(~/.cdsapirc),与安装无关、单独配置",
                ],
                "requires": ["conda_windows", "conda_env_insar"],
            },
            _wsl_route("pyaps3 在 WSL 里随 conda env insar 一并安装(实测 0.3.7)"),
        ],
    },
    "pystamps": {
        "label": "PyStamps(PS 链第 7 步备选,滑坡场景)",
        "hint": ("无官方 conda/PyPI 包:取得 PyStamps 源码后用引擎环境的 python "
                 "-m pip install <源码目录> 安装,探测判据是 import pystamps 可用"),
        "rationale": {
            "manual": ("唯一途径:PyStamps 无官方 conda/PyPI 分发(本表不编造包名),"
                       "需源码 pip 安装进引擎环境"),
        },
        "routes": [
            {
                "route": "manual",
                "title": "源码 pip 安装(装进引擎环境)",
                "steps": [
                    "获取 PyStamps 源码包(课题组内部分发;conda-forge/PyPI 无官方包)",
                    r"E:\miniforge3\envs\insar\python.exe -m pip install <PyStamps 源码目录>",
                    ("E:\\miniforge3\\envs\\insar\\python.exe -c "
                     '"import pystamps; print(pystamps.__name__)"'),
                ],
                "est_minutes": 15,
                "disk_gb": 1.0,
                "notes": [
                    "探测判据:引擎环境 site-packages 里有 pystamps 模块(probe.py)",
                    "仅 PS 链(第 7 步 pystamps_ps,滑坡场景)需要;SBAS 主链可不装",
                    "配套的 ISCE2→PyStamps 数据桥已内置(engines/bridges),无需单独安装",
                ],
                "requires": ["conda_env_insar"],
            },
        ],
    },
    "snap": {
        "label": "ESA SNAP(gpt,2-4 步备选链)",
        "hint": ("从 ESA STEP 官网下载 SNAP 安装器手动安装,把安装目录 bin(含 gpt)"
                 "加入 PATH;主链走 HyP3/ISCE2 时可不装"),
        "rationale": {
            "manual": ("唯一途径:ESA SNAP 以桌面安装器分发(无 conda 包);"
                       "装完把 bin 目录加入 PATH,探测判据是 PATH 上的 gpt"),
        },
        "routes": [
            {
                "route": "manual",
                "title": "官网安装器(ESA STEP)",
                "steps": [
                    "下载 Windows 安装器:https://step.esa.int/main/download/snap-download/",
                    "运行安装器,选装 Sentinel-1 Toolbox(安装盘自选,建议非 C: 盘)",
                    r"把安装目录的 bin(如 D:\esa-snap\bin,含 gpt)加入用户 PATH",
                    "gpt -h",
                ],
                "est_minutes": 30,
                "disk_gb": 3.0,
                "notes": [
                    "SNAP 链(snap_backgeocoding / snap_interferogram)是 2-4 步的"
                    "备选方法;主链走 HyP3/ISCE2 时可不装",
                    "改 PATH 后需新开终端(以及重启本服务)探测才会生效",
                ],
                "requires": [],
            },
        ],
    },
}

# ---------------- 纯函数 ----------------


def resolve_engines(probe: "ProbeResult | Mapping[str, str | None]") -> dict[str, str | None]:
    """七引擎的探测终值:本地优先、WSL 兜底(与 api/setup_router 的 _engine 同口径)。

    probe 可以是 ProbeResult,也可以是 engines 映射(测试注入用)。
    WSL 兜底值带 " (wsl)" 后缀标注来源,如 "2.6.5 (wsl)"。
    """
    engines = probe.engines if hasattr(probe, "engines") else dict(probe)
    out: dict[str, str | None] = {}
    for name in ENGINE_ORDER:
        local = engines.get(name)
        if local:
            out[name] = local
            continue
        wsl = engines.get(f"{name} (wsl)")
        out[name] = f"{wsl} (wsl)" if wsl else None
    return out


def _requirement_state(req: str, wsl_status: Mapping) -> bool | None:
    """前置条件的机器判定:True 满足 / False 未满足 / None 探测不到(请自行确认)。"""
    if req == "wsl2":
        if "installed" not in wsl_status:
            return None  # 本次没探测 WSL(check_wsl=False):不臆断
        return bool(wsl_status.get("installed"))
    return None  # conda_windows / conda_env_insar:probe 不探测 conda,诚实缺席


def _enrich_route(route: dict, wsl_status: Mapping) -> dict:
    """深拷贝途径并附 requires_detail:[{id,label,met}](met 为 True/False/None)。"""
    out = deepcopy(route)
    out["requires_detail"] = [
        {"id": req, "label": REQUIREMENTS[req], "met": _requirement_state(req, wsl_status)}
        for req in route["requires"]
    ]
    return out


def plan_installs(probe: "ProbeResult | Mapping[str, str | None]",
                  wsl_status: Mapping | None = None) -> list[dict]:
    """按当前探测结果给出「缺什么 → 推荐哪条途径 → 为什么」的有序清单。

    - 只列缺失引擎(本地与 WSL 兜底都探测不到才算缺);
    - 排序:必需引擎(mintpy/gdal)在前,其余按 ENGINE_ORDER;
    - 推荐:取声明顺序里第一条「机器可判定前置没有明确未满足」的途径;
      全部途径前置都未满足时仍推荐首选途径,why 里说明前置未就绪;
    - 每条途径附 requires_detail(前置逐项的满足状态),供前端展示。
    """
    if wsl_status is None:
        wsl_status = getattr(probe, "wsl", None) or {}
    engines = resolve_engines(probe)
    missing = [e for e in ENGINE_ORDER if engines.get(e) is None]
    missing.sort(key=lambda e: (e not in REQUIRED_ENGINES, ENGINE_ORDER.index(e)))

    plans: list[dict] = []
    for engine in missing:
        spec = INSTALL_GUIDE[engine]
        routes = [_enrich_route(r, wsl_status) for r in spec["routes"]]

        def _unmet(route: dict) -> list[str]:
            return [d["id"] for d in route["requires_detail"] if d["met"] is False]

        chosen = next((r for r in routes if not _unmet(r)), routes[0])
        unmet = _unmet(chosen)
        why = spec["rationale"][chosen["route"]]
        if unmet:
            labels = ";".join(REQUIREMENTS[u] for u in unmet)
            why += f"。前置未就绪:{labels}"
        elif chosen is not routes[0]:
            first = routes[0]
            labels = ";".join(REQUIREMENTS[u] for u in _unmet(first))
            why = (f"首选「{first['title']}」前置未满足({labels}),改荐本途径。" + why)

        plans.append({
            "engine": engine,
            "label": spec["label"],
            "required": engine in REQUIRED_ENGINES,
            "recommend": chosen["route"],
            "why": why,
            "unmet": unmet,
            "routes": routes,
        })
    return plans


def install_hint_for(engine: str) -> str:
    """一句话安装途径摘要(对话大脑后续可注入,见模块头注释;无副作用)。"""
    spec = INSTALL_GUIDE.get(engine)
    if spec is None:
        return (f"未收录引擎 {engine} 的安装指引"
                f"(已收录:{'/'.join(ENGINE_ORDER)})")
    return f"{engine}:{spec['hint']}"
