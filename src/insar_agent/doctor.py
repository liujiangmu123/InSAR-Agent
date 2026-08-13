"""一键体检(doctor):面向排障的深检 —— 环境向导(api/setup_router)的超集。

向导回答「首次配置是否就绪」;doctor 回答「环境曾经好过、现在为什么不对」:
数据库完整性/孤儿记录、磁盘水位、端口占用、WSL 状态、依赖版本漂移。
给用户与支持排障一个权威入口(CLI 与 GET /api/doctor 共用本模块)。

纪律(与仓库重型计算管控一致):
  - 全部检查项秒级、只读:只报告不修,绝不运行真实 InSAR 计算;
  - WSL 引擎探测只窥视 runtime/wsl_probe 的既有 TTL 缓存,绝不冷启动(约 20s);
  - 复用 runtime/probe.py / runtime/wsl_probe.py 的既有探测函数,不另造判据;
  - 每个检查器独立 try/except:单项崩溃记为 fail(检查器自身异常),绝不拖垮整体。

用法:
  python -m insar_agent.doctor [--home DIR] [--json]
退出码:0 全 ok;1 有 warn 无 fail;2 有 fail。彩色输出遵守 NO_COLOR 环境变量。
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import os
import re
import shutil
import socket
import sqlite3
import sys
import tempfile
import time
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

# ---------------- 结果模型 ----------------

STATUS_OK = "ok"
STATUS_WARN = "warn"
STATUS_FAIL = "fail"

#: 状态 → 严重度(同时也是 CLI 退出码):全 ok=0,有 warn=1,有 fail=2
_SEVERITY = {STATUS_OK: 0, STATUS_WARN: 1, STATUS_FAIL: 2}


@dataclass
class CheckResult:
    name: str          # 检查项名(人类可读,中文)
    category: str      # 类别:环境|引擎|数据库|文件系统|网络/端口|WSL
    status: str        # ok | warn | fail
    detail: str = ""   # 实测细节(数值/路径/错误摘要)
    fix_hint: str = "" # 处置建议(仅 warn/fail 时有意义)

    def to_dict(self) -> dict:
        return asdict(self)


def overall_status(results: list[CheckResult]) -> str:
    worst = max((_SEVERITY.get(r.status, 2) for r in results), default=0)
    return (STATUS_OK, STATUS_WARN, STATUS_FAIL)[worst]


def summarize(results: list[CheckResult]) -> dict:
    """CLI --json 与 GET /api/doctor 共用的机器可读汇总(单一真相源,防两端漂移)。"""
    counts = {STATUS_OK: 0, STATUS_WARN: 0, STATUS_FAIL: 0}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    status = overall_status(results)
    return {
        "status": status,
        "exit_code": _SEVERITY[status],
        "counts": counts,
        "results": [r.to_dict() for r in results],
    }


# ---------------- 公共辅助 ----------------

def _resolve_home(home: Path | str | None) -> Path:
    # 与 api/app.py create_app / api/setup_router 的解析规则一致:参数 > INSAR_HOME > ./workspace
    return Path(home or os.environ.get("INSAR_HOME", "workspace")).resolve()


def _existing_root(home: Path) -> Path:
    """home 或其最近存在的祖先(首启时 workspace 可能还没建出来;探测点必须存在)。"""
    return next((p for p in (home, *home.parents) if p.exists()), Path("."))


def _peek_wsl_cache(distro: str) -> dict | None:
    """只窥视 wsl_probe 的模块级 TTL 缓存,绝不触发探测。

    缓存未命中时 probe_wsl_engines_cached 会付约 20s 冷启动(VM 启动 + conda
    python 冷启动),违背「体检全部秒级」—— 体检宁可报告「缓存为空」。
    缓存语义(键/TTL)与 probe_wsl_engines_cached 完全一致。
    """
    from insar_agent.runtime import wsl_probe

    hit = wsl_probe._PROBE_CACHE.get(distro)
    if hit and time.monotonic() - hit[0] < wsl_probe._PROBE_CACHE_TTL:
        return hit[1]
    return None


# ---------------- 环境:Python / venv / 依赖 ----------------

def _check_python(home: Path) -> list[CheckResult]:
    ver = sys.version.split()[0]
    ok = sys.version_info >= (3, 11)
    return [CheckResult(
        "Python 版本", "环境", STATUS_OK if ok else STATUS_FAIL,
        f"{ver} @ {sys.executable}",
        "" if ok else "本项目要求 Python ≥ 3.11:用 py -3.11 -m venv .venv 重建虚拟环境")]


def _check_venv(home: Path) -> list[CheckResult]:
    in_venv = sys.prefix != sys.base_prefix
    return [CheckResult(
        "虚拟环境隔离", "环境", STATUS_OK if in_venv else STATUS_WARN,
        f"venv 内运行:{sys.prefix}" if in_venv
        else f"未用虚拟环境(直接跑在 {sys.prefix}),依赖易与全局站点包互相污染",
        "" if in_venv
        else r"用项目内虚拟环境运行:.venv\Scripts\python.exe -m insar_agent.doctor")]


#: 发行名 → import 名(仅列不同名者;其余按小写连字符转下划线)
_IMPORT_NAME = {"pyyaml": "yaml"}

#: importlib.metadata 拿不到自身元数据时(未 pip install -e .)的回退清单,
#: 与 pyproject.toml [project].dependencies 保持一致
_FALLBACK_MAIN_DEPS: tuple[tuple[str, str], ...] = (
    ("fastapi", "0.110"), ("uvicorn", "0.29"), ("pyyaml", "6.0"))

#: 需求串解析:发行名 + 可选 extras 方括号 + 可选 >= 下限(足够覆盖本仓 pyproject 写法)
_REQ_RE = re.compile(
    r"^\s*([A-Za-z0-9][A-Za-z0-9._\-]*)\s*(?:\[[^\]]*\])?\s*(?:>=\s*([0-9][^,;\s]*))?")


def _declared_requirements() -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """从已安装发行版元数据读 pyproject 依赖:(主依赖, raster 可选依赖)。

    pyproject.toml 源文件不随包分发,运行期以 importlib.metadata 为准
    (pip install -e . 时由 pyproject 生成);读不到时回退硬编码清单。
    """
    main: list[tuple[str, str]] = []
    optional: list[tuple[str, str]] = []
    try:
        reqs = importlib.metadata.requires("insar-agent") or []
    except importlib.metadata.PackageNotFoundError:
        reqs = []
    for raw in reqs:
        m = _REQ_RE.match(raw)
        if not m:
            continue
        pair = (m.group(1), m.group(2) or "")
        if "extra ==" in raw:
            # 只体检 raster 可选组(run_ok 栅格校验用);dev 工具链不属运行期健康
            if 'extra == "raster"' in raw:
                optional.append(pair)
        else:
            main.append(pair)
    return (main or list(_FALLBACK_MAIN_DEPS)), optional


def _ver_tuple(s: str) -> tuple[int, ...]:
    """版本串 → 数值元组(遇到非数字段截断;够比较 pyproject 的 >= 下限)。"""
    parts: list[int] = []
    for seg in s.split("."):
        m = re.match(r"\d+", seg)
        if not m:
            break
        parts.append(int(m.group()))
    return tuple(parts)


def _dep_row(dist: str, floor: str, *, required: bool) -> CheckResult:
    label = f"{'依赖' if required else '可选依赖'} {dist}"
    mod = _IMPORT_NAME.get(dist.lower(), dist.lower().replace("-", "_"))
    try:
        version = importlib.metadata.version(dist)
    except importlib.metadata.PackageNotFoundError:
        version = None
    try:
        importable = importlib.util.find_spec(mod) is not None
    except Exception as exc:  # noqa: BLE001 —— 损坏的包在 find_spec 就可能炸,如实报告
        return CheckResult(label, "环境", STATUS_FAIL, f"import {mod} 探测异常:{exc!r}",
                           '安装疑似损坏:.venv\\Scripts\\pip install --force-reinstall '
                           f"{dist} 后复检")
    if version is None and not importable:
        return CheckResult(
            label, "环境", STATUS_FAIL if required else STATUS_WARN, "未安装",
            '重装依赖:.venv\\Scripts\\pip install -e ".[dev]"' if required
            else '栅格校验/QA 需要时安装:.venv\\Scripts\\pip install -e ".[raster]"')
    if not importable:
        return CheckResult(label, "环境", STATUS_FAIL,
                           f"元数据在({version})但 import {mod} 找不到模块 —— 安装残缺",
                           f".venv\\Scripts\\pip install --force-reinstall {dist}")
    if floor and _ver_tuple(version or "0") < _ver_tuple(floor):
        return CheckResult(label, "环境", STATUS_WARN,
                           f"{version} 低于 pyproject 下限 >={floor}(版本漂移)",
                           '对齐声明版本:.venv\\Scripts\\pip install -e ".[dev]" --upgrade')
    return CheckResult(label, "环境", STATUS_OK, f"{version or 'present'}(import {mod} 可用)")


def _check_dependencies(home: Path) -> list[CheckResult]:
    main, optional = _declared_requirements()
    out: list[CheckResult] = []
    for dist, floor in main:
        out.append(_dep_row(dist, floor, required=True))
    for dist, floor in optional:
        out.append(_dep_row(dist, floor, required=False))
    return out


def _check_download_credentials(home: Path) -> list[CheckResult]:
    """数据下载凭证(workspace/credentials.json):只看配置存在性,绝不出网验证。"""
    from insar_agent.runtime.credentials import configured_mode, load_credentials

    label = {"token": "EDL token", "password": "Earthdata 账号密码"}.get(
        configured_mode(load_credentials(home)))
    return [CheckResult(
        "数据下载凭证", "环境", STATUS_OK if label else STATUS_WARN,
        f"已配置({label} 方式,credentials.json)" if label else
        "未配置(第 1 步 asf_search/HyP3 真实下载需要 NASA Earthdata 凭证;模拟演示不需要)",
        "" if label else "前端「环境」面板 →「数据下载凭证」填 EDL token 或账号密码;"
                         "无账号先注册 https://urs.earthdata.nasa.gov")]


# ---------------- 引擎(复用 probe_environment + WSL 缓存) ----------------

def _check_engines(home: Path) -> list[CheckResult]:
    # _implicit_engine_prefix:与向导同一套发现例程(见 setup_router 的复用注释),
    # 体检报告的必须是探测真用的 prefix,不能抄一份判据
    from insar_agent.runtime.backend_select import wsl_distro
    from insar_agent.runtime.probe import _implicit_engine_prefix, probe_environment
    from insar_agent.runtime.wsl_probe import merge_wsl_probe

    probe = probe_environment(_existing_root(home), with_versions=False, check_wsl=False)
    cached = _peek_wsl_cache(wsl_distro())
    if cached:
        merge_wsl_probe(probe, cached)  # 只并入缓存命中的结果,绝不冷启动

    out: list[CheckResult] = []

    # 1) 引擎环境前缀:在哪个前缀、来源是显式配置还是隐式发现
    prefix = os.environ.get("INSAR_ENGINE_PREFIX") or None
    if prefix and Path(prefix).is_dir():
        out.append(CheckResult("引擎环境前缀", "引擎", STATUS_OK,
                               f"{prefix}(显式配置 INSAR_ENGINE_PREFIX)"))
    elif prefix:
        out.append(CheckResult(
            "引擎环境前缀", "引擎", STATUS_FAIL,
            f"INSAR_ENGINE_PREFIX 指向不存在的目录:{prefix}",
            "确认 conda 引擎环境路径(如 E:\\miniforge3\\envs\\insar)后重新保存;"
            "或走向导 POST /api/setup/engine-env 获取创建命令"))
    else:
        implicit = _implicit_engine_prefix()
        if implicit:
            out.append(CheckResult(
                "引擎环境前缀", "引擎", STATUS_OK,
                f"{implicit}(隐式发现,未固化)",
                "建议在向导第 3 步保存为显式 engine_prefix,避免换终端后靠再次扫描碰运气"))
        else:
            out.append(CheckResult(
                "引擎环境前缀", "引擎", STATUS_WARN,
                "未配置且未发现 conda 引擎环境(模拟演示不需要;真实计算前需配置)",
                "POST /api/setup/engine-env 获取创建命令,环境建好后在向导保存 engine_prefix"))

    # 2) 核心引擎(mintpy/gdal):本地优先,WSL 缓存兜底 —— 与向导同一解析次序
    def _have(name: str) -> str | None:
        local = probe.engines.get(name)
        if local:
            return local
        wsl = probe.engines.get(f"{name} (wsl)")
        return f"{wsl} (wsl)" if wsl else None

    core = {k: _have(k) for k in ("mintpy", "gdal")}
    missing = [k for k, v in core.items() if not v]
    out.append(CheckResult(
        "核心引擎 mintpy/gdal", "引擎",
        STATUS_OK if not missing else STATUS_WARN,
        " · ".join(f"{k}={v or '缺失'}" for k, v in core.items()),
        "" if not missing else
        "7-9 步 SBAS 反演需要 MintPy/GDAL:按向导创建 conda 引擎环境(模拟演示可继续用)"))

    # 3) 可选引擎:存在性清单(信息项,不影响结论)
    opt = {k: _have(k) for k in ("snaphu", "pyaps", "isce2", "snap", "pystamps")}
    present = [f"{k}={v}" for k, v in opt.items() if v]
    absent = [k for k, v in opt.items() if not v]
    out.append(CheckResult(
        "可选引擎", "引擎", STATUS_OK,
        (("可用:" + " · ".join(present)) if present else "全部未探测到")
        + (f";缺失:{'/'.join(absent)}" if absent else ""),
        ""))
    return out


# ---------------- 数据库(只读只报告,绝不建库/迁移/修复) ----------------

#: running 状态的 run,其租约过期超过该秒数才算可疑(刚崩溃的 60s 接管窗不报)
_LEASE_ORPHAN_AGE = 3600.0
#: 命令意图落盘但始终未结算(exit_code IS NULL)超过该秒数记可疑
_COMMAND_STUCK_AGE = 86400.0


def _check_database(home: Path) -> list[CheckResult]:
    db = home / "insar.db"
    if not db.exists():
        return [CheckResult(
            "数据库文件", "数据库", STATUS_WARN,
            f"不存在:{db}(服务首次启动前属正常)",
            "启动过后端仍缺文件时,核对 INSAR_HOME 是否指向了预期目录")]
    out = [CheckResult("数据库文件", "数据库", STATUS_OK,
                       f"{db}({db.stat().st_size / 1024:.0f} KB)")]

    # 只读打开(URI mode=ro):体检绝不建库/跑迁移 —— core.db.Database 会执行
    # schema 重放与列迁移,不能用
    try:
        conn = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True, timeout=2.0)
    except sqlite3.Error as exc:
        out.append(CheckResult("数据库可打开性", "数据库", STATUS_FAIL,
                               f"只读打开失败:{exc}",
                               "确认文件未被其他程序独占;损坏时停服后用备份恢复"))
        return out
    try:
        for pragma in ("integrity_check", "quick_check"):
            try:
                msgs = [str(r[0]) for r in conn.execute(f"PRAGMA {pragma}").fetchall()]
                if msgs == ["ok"]:
                    out.append(CheckResult(f"PRAGMA {pragma}", "数据库", STATUS_OK, "ok"))
                else:
                    out.append(CheckResult(
                        f"PRAGMA {pragma}", "数据库", STATUS_FAIL,
                        ";".join(msgs[:3])[:300],
                        "数据库已损坏:停服后备份 insar.db,用最近备份恢复(体检只报告不修)"))
            except sqlite3.Error as exc:
                out.append(CheckResult(
                    f"PRAGMA {pragma}", "数据库", STATUS_FAIL, f"执行失败:{exc}",
                    "文件可能不是 SQLite 库或已严重损坏:停服后用备份恢复"))

        # 孤儿 run:status=running 但租约(resource='run:<id>',loop/driver 写入)
        # 过期超过 1h,或压根没有租约且创建已超过 1h —— 大概率是进程死亡后的残留
        now = time.time()
        try:
            rows = conn.execute(
                "SELECT r.run_id, r.created_at, l.heartbeat, l.ttl "
                "FROM runs r LEFT JOIN leases l ON l.resource = 'run:' || r.run_id "
                "WHERE r.status = 'running'").fetchall()
            suspects = []
            for run_id, created, hb, ttl in rows:
                stale_for = (now - (hb + ttl)) if hb is not None else (now - (created or now))
                if stale_for > _LEASE_ORPHAN_AGE:
                    kind = "租约过期" if hb is not None else "无租约"
                    suspects.append(f"{run_id}({kind} {stale_for / 3600:.1f}h)")
            out.append(CheckResult(
                "孤儿 run(running 但租约过期>1h)", "数据库",
                STATUS_OK if not suspects else STATUS_WARN,
                "无" if not suspects else f"{len(suspects)} 个:" + "、".join(suspects[:3]),
                "" if not suspects else
                "只报告不修:重启后端后由租约接管/管理端(/api/admin)处置,或人工核对后终结"))
        except sqlite3.Error as exc:
            out.append(CheckResult("孤儿 run(running 但租约过期>1h)", "数据库",
                                   STATUS_FAIL, f"查询失败:{exc}",
                                   "表结构异常或文件损坏:结合上方完整性检查判断"))

        # 未结算命令:意图已 INSERT 但 exit_code 始终为 NULL 且创建超过 24h
        try:
            n = conn.execute(
                "SELECT COUNT(*) FROM commands WHERE exit_code IS NULL AND created_at < ?",
                (now - _COMMAND_STUCK_AGE,)).fetchone()[0]
            out.append(CheckResult(
                "未结算命令(exit_code 为空>24h)", "数据库",
                STATUS_OK if n == 0 else STATUS_WARN,
                "无" if n == 0 else f"{n} 条(意图落盘但从未结算,多为进程中途死亡)",
                "" if n == 0 else "只报告不修:不影响新任务;确认无在途作业后可忽略"))
        except sqlite3.Error as exc:
            out.append(CheckResult("未结算命令(exit_code 为空>24h)", "数据库",
                                   STATUS_FAIL, f"查询失败:{exc}",
                                   "表结构异常或文件损坏:结合上方完整性检查判断"))
    finally:
        conn.close()
    return out


# ---------------- 文件系统:可写性 / 磁盘水位 / 残留作业目录 ----------------

_DISK_WARN_GB = 10.0
_DISK_FAIL_GB = 2.0


def _check_filesystem(home: Path) -> list[CheckResult]:
    out: list[CheckResult] = []

    # 工作区可写(写探针文件再删,是体检里唯一的写动作,不留痕)
    if not home.exists():
        out.append(CheckResult("工作区可写", "文件系统", STATUS_WARN,
                               f"目录不存在:{home}(首次启动时由后端自动创建;体检不代建)",
                               "如已运行过后端仍缺目录,核对 INSAR_HOME"))
    elif not home.is_dir():
        out.append(CheckResult("工作区可写", "文件系统", STATUS_FAIL,
                               f"{home} 不是目录",
                               "INSAR_HOME 必须指向目录;移走同名文件或改配置"))
    else:
        try:
            fd, tmp = tempfile.mkstemp(prefix=".doctor-probe-", dir=str(home))
            os.close(fd)
            os.unlink(tmp)
            out.append(CheckResult("工作区可写", "文件系统", STATUS_OK, str(home)))
        except OSError as exc:
            out.append(CheckResult("工作区可写", "文件系统", STATUS_FAIL,
                                   f"写入探针失败:{exc}",
                                   "检查目录 ACL/只读属性/磁盘状态;运行数据库与作业都要写这里"))

    # 磁盘水位(<10GB warn,<2GB fail)
    try:
        usage = shutil.disk_usage(str(_existing_root(home)))
        free_gb = usage.free / (1 << 30)
        total_gb = usage.total / (1 << 30)
        status = (STATUS_FAIL if free_gb < _DISK_FAIL_GB
                  else STATUS_WARN if free_gb < _DISK_WARN_GB else STATUS_OK)
        out.append(CheckResult(
            "磁盘剩余", "文件系统", status,
            f"{free_gb:.1f} GB 可用 / 共 {total_gb:.1f} GB",
            "" if status == STATUS_OK else
            f"低于 {'2' if status == STATUS_FAIL else '10'} GB 水位:清理磁盘或把 "
            "INSAR_HOME 指向更大的盘(中间产物动辄数十 GB)"))
    except OSError as exc:
        out.append(CheckResult("磁盘剩余", "文件系统", STATUS_FAIL, f"探测失败:{exc}",
                               "磁盘/挂载点异常:确认 INSAR_HOME 所在卷可访问"))

    # .jobs 残留作业目录统计(runtime/executor 的本地作业契约目录;只统计不清理)
    try:
        count = 0
        sessions = home / "sessions"
        if sessions.is_dir():
            for jobs_dir in sessions.glob("*/.jobs"):
                count += sum(1 for c in jobs_dir.iterdir() if c.is_dir())
        out.append(CheckResult(
            ".jobs 残留作业目录", "文件系统", STATUS_OK,
            f"{count} 个(sessions/*/.jobs/*)" + ("" if count == 0 else
            ";属正常执行痕迹,确认无运行中作业后可手动清理释放磁盘")))
    except OSError as exc:
        out.append(CheckResult(".jobs 残留作业目录", "文件系统", STATUS_WARN,
                               f"统计失败:{exc}", "目录可读性异常,不影响运行;可人工核查"))
    return out


# ---------------- 网络/端口(仅检测报告,绝不杀进程/绝不绑定) ----------------

#: 判定占用者是否本项目后端的健康探测超时(秒);测试可调小
_HEALTH_TIMEOUT = 2.0


def _check_port(home: Path) -> list[CheckResult]:
    raw = os.environ.get("INSAR_PORT", "8873")
    try:
        port = int(raw)
        if not 0 < port < 65536:
            raise ValueError(raw)
    except ValueError:
        return [CheckResult("配置端口", "网络/端口", STATUS_WARN,
                            f"INSAR_PORT={raw!r} 不是合法端口(桌面壳会回退 8873)",
                            "改成 1-65535 的整数,或删除该环境变量用默认 8873")]

    # 只做 TCP connect 探测(读侧动作):连不上 = 空闲;连上了再用 /api/health
    # 分辨是否本项目后端 —— 被别的程序占着才是要报告的故障
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.0):
            pass
    except OSError:
        return [CheckResult("配置端口", "网络/端口", STATUS_OK,
                            f"127.0.0.1:{port} 空闲(后端未运行,或运行在其他端口)")]

    import urllib.request
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/health")
        with urllib.request.urlopen(req, timeout=_HEALTH_TIMEOUT) as resp:
            body = json.loads(resp.read().decode("utf-8", "replace"))
        if body.get("ok"):
            return [CheckResult("配置端口", "网络/端口", STATUS_OK,
                                f"127.0.0.1:{port} 由 InSAR 后端监听(/api/health 正常)")]
        raise ValueError("health 响应形状不对")
    except Exception:  # noqa: BLE001 —— 非本后端的占用者行为不可预期,一律归为「被占用」
        return [CheckResult(
            "配置端口", "网络/端口", STATUS_WARN,
            f"127.0.0.1:{port} 已被其他进程占用(/api/health 无有效响应)",
            f"Get-NetTCPConnection -LocalPort {port} 找占用进程;或换 INSAR_PORT。"
            "体检仅检测报告,不杀进程")]


# ---------------- WSL:发行版状态 + 引擎缓存(不冷启动) ----------------

def _check_wsl(home: Path) -> list[CheckResult]:
    from insar_agent.runtime.backend_select import wsl_distro
    from insar_agent.runtime.wsl import wsl_status

    distro = wsl_distro()
    st = wsl_status()  # wsl.exe -l -q:秒级,只列注册发行版,不启动 VM
    out: list[CheckResult] = []
    if not st.get("installed"):
        out.append(CheckResult(
            "WSL 发行版", "WSL", STATUS_WARN,
            "未检测到 WSL(HyP3 云端/模拟路线不需要;isce2/snaphu 本地链需要)",
            "需要本地 ISCE2/snaphu 链路时:wsl --install 后按 scripts/wsl_setup.ps1 配置"))
    elif any(d.casefold() == distro.casefold() for d in st.get("distros", [])):
        out.append(CheckResult(
            "WSL 发行版", "WSL", STATUS_OK,
            f"发行版 {distro} 已注册(共 {len(st['distros'])} 个:"
            f"{'、'.join(st['distros'][:4])})"))
    else:
        out.append(CheckResult(
            "WSL 发行版", "WSL", STATUS_WARN,
            f"发行版 {distro} 未注册(已注册:{'、'.join(st['distros'][:4]) or '无'})",
            "按 scripts/wsl_setup.ps1 导入发行版,或用 INSAR_WSL_DISTRO 指向已有发行版"))

    cached = _peek_wsl_cache(distro)
    if cached:
        engines = cached.get("engines") or {}
        present = [k for k, e in engines.items() if e.get("present")]
        out.append(CheckResult(
            "WSL 引擎(TTL 缓存)", "WSL", STATUS_OK,
            f"engine prefix={cached.get('engine_prefix') or '未发现'};"
            f"引擎 {len(present)}/{len(engines)} 在位"
            + (f"({'、'.join(present)})" if present else "")))
    else:
        # 缓存只存 ok=True 的结果,为空 = 近 5 分钟没有成功探测过。不冷启动
        # (约 20s),报告事实并给预热入口
        out.append(CheckResult(
            "WSL 引擎(TTL 缓存)", "WSL", STATUS_OK,
            "缓存为空:本次跳过引擎探测(避免约 20s 冷启动)",
            "打开前端「环境」面板或 GET /api/setup/status 可预热缓存后重跑体检"))
    return out


# ---------------- 汇总入口 ----------------

#: 检查器注册表:(类别, 检查器函数)。测试可向此注入故障检查器验证隔离性。
_CHECKERS: tuple[tuple[str, Callable[[Path], list[CheckResult]]], ...] = (
    ("环境", _check_python),
    ("环境", _check_venv),
    ("环境", _check_dependencies),
    ("环境", _check_download_credentials),
    ("引擎", _check_engines),
    ("数据库", _check_database),
    ("文件系统", _check_filesystem),
    ("网络/端口", _check_port),
    ("WSL", _check_wsl),
)


def check_all(home: Path | str | None = None) -> list[CheckResult]:
    """执行全部体检项。单个检查器崩溃记为该项 fail,绝不拖垮整体。"""
    home_dir = _resolve_home(home)
    results: list[CheckResult] = []
    for category, fn in _CHECKERS:
        try:
            results.extend(fn(home_dir))
        except Exception as exc:  # noqa: BLE001 —— 隔离墙:检查器自身缺陷也要成为体检结果
            results.append(CheckResult(
                f"{fn.__name__}(检查器异常)", category, STATUS_FAIL,
                f"{type(exc).__name__}: {exc}",
                "这是体检器自身缺陷,请把本行信息附进 issue 反馈"))
    return results


# ---------------- CLI(纯 ANSI 彩色表格,遵守 NO_COLOR) ----------------

def _enable_vt() -> None:
    """Windows 控制台启用 ANSI 转义(VT)。失败无害:老控制台可用 NO_COLOR 关色。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        k32 = ctypes.windll.kernel32
        handle = k32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_ulong()
        if k32.GetConsoleMode(handle, ctypes.byref(mode)):
            k32.SetConsoleMode(handle, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except (OSError, AttributeError):
        pass


def _use_color() -> bool:
    # NO_COLOR 约定(no-color.org):设了(任意值)就降级纯文本;重定向输出同样降级
    return "NO_COLOR" not in os.environ and sys.stdout.isatty()


_ANSI = {"ok": "32", "warn": "33", "fail": "31", "bold": "1", "dim": "2"}


def _paint(text: str, code: str, use: bool) -> str:
    return f"\x1b[{_ANSI[code]}m{text}\x1b[0m" if use else text


def _disp_width(s: str) -> int:
    """终端显示宽度:东亚全宽字符按 2 列(中文表头/类别不对齐会歪成锯齿)。"""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in s)


def _pad(s: str, width: int) -> str:
    return s + " " * max(0, width - _disp_width(s))


_STATUS_LABEL = {STATUS_OK: " OK ", STATUS_WARN: "WARN", STATUS_FAIL: "FAIL"}


def _print_table(results: list[CheckResult], home: Path) -> None:
    use = _use_color()
    if use:
        _enable_vt()
    cat_w = max((_disp_width(r.category) for r in results), default=4)
    name_w = max((_disp_width(r.name) for r in results), default=6)

    print(_paint(f"InSAR-Agent 一键体检 · workspace: {home}", "bold", use))
    rule = "─" * min(100, 8 + cat_w + name_w + 40)
    print(rule)
    last_cat = None
    for r in results:
        if r.category != last_cat and last_cat is not None:
            print()
        last_cat = r.category
        tag = _paint(f"[{_STATUS_LABEL[r.status]}]", r.status, use)
        print(f"{tag} {_pad(r.category, cat_w)}  {_pad(r.name, name_w)}  {r.detail}")
        if r.status != STATUS_OK and r.fix_hint:
            # └ 在 GB2312/GBK 里有码位;↳(U+21B3)会让 GBK 控制台直接 UnicodeEncodeError
            indent = " " * 7
            print(indent + _paint(f"└ 处置:{r.fix_hint}", "dim", use))
    print(rule)
    summary = summarize(results)
    counts = summary["counts"]
    verdict = _paint(summary["status"].upper(), summary["status"], use)
    print(f"结论:{verdict}(ok {counts['ok']} · warn {counts['warn']} · "
          f"fail {counts['fail']})· 退出码 {summary['exit_code']}")


def _main(argv: list[str] | None = None) -> int:
    import argparse

    # Windows 控制台常见 GBK 编码:个别字符编不进去时降级为替换符,绝不让体检
    # 工具自己崩在输出上(2026-08-13 实测:PowerShell 5.1 下 U+21B3 直接炸)
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, OSError):
        pass  # 非 TextIOWrapper(如测试替身)/流已关闭:维持原样

    ap = argparse.ArgumentParser(
        prog="python -m insar_agent.doctor",
        description="InSAR-Agent 一键体检:秒级只读深检(环境/引擎/数据库/文件系统/端口/WSL),"
                    "只报告不修")
    ap.add_argument("--home", default=None,
                    help="工作区目录(默认 INSAR_HOME 或 ./workspace)")
    ap.add_argument("--json", action="store_true",
                    help="输出机器可读 JSON(支持排障/脚本消费)")
    args = ap.parse_args(argv)

    home = _resolve_home(args.home)
    results = check_all(home)
    summary = summarize(results)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        _print_table(results, home)
    return summary["exit_code"]


if __name__ == "__main__":  # pragma: no cover - CLI 入口
    raise SystemExit(_main())
