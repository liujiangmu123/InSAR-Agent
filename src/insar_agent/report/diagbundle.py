"""诊断包导出:用户遇到问题时一键打包(日志 + 环境 + DB 摘要 + 最近事件)。

产出一个 zip(交给支持渠道 / 贴进 issue),内容:

  manifest.json     生成时间 UTC / app 版本(version_router 口径)/ 平台信息
                    (OS / Python / CPU 逻辑核 / 内存,只用 platform/os 标准库)
                    / 大小裁剪记录 / 包内文件清单
  env.json          环境探测(引擎/前缀/WSL;WSL 引擎探测只复用既有缓存,
                    绝不现场触发 20 秒探测)+ 白名单环境变量(INSAR_* 全部,
                    形似密钥的值打码;PATH 只记条数不含内容 —— 防泄漏)
  db_summary.json   各表行数 / 最近 10 个 run 的 id·状态·时间 / 最近 50 条
                    note·error 事件摘要(store 公开方法优先;缺口用本模块
                    内部的只读 SELECT,绝不改 store.py)
  logs/             指定 run(缺省取最近一个失败 run)的全部 job.log 尾部
                    (默认 256KB/文件,截断处显式标注)+ 该 run 的 provenance json

隐私与体积纪律:
  - 默认不含科学数据产物(大栅格/HDF5 走产物面板,不进诊断包);
  - 全部文本脱敏:当前用户主目录 → ~(含正/反斜杠与任意用户名形态),
    其余带盘符的绝对路径与 UNC 路径默认打码(mask_abs_paths=False 可关);
  - 总大小上限 50MB,超限逐级裁剪并在 manifest 如实记录:
      L1 日志尾部砍到 64KB/文件 → L2 事件砍到 10 条 → L3 逐个丢弃最大的
      日志/provenance 条目(核心三份 manifest/env/db_summary 永不丢)。

只读纪律:除 out_dir 下落一个 zip 外,不向工作区写任何文件;DB 只经
Database.query 只读访问(不建表、不写行 —— schema.sql 全部 IF NOT EXISTS,
对既有库是无害重放)。
"""

from __future__ import annotations

import json
import os
import platform
import re
import sys
import time
import zipfile
from pathlib import Path

from insar_agent.api.version_router import get_version
from insar_agent.core.db import Database
from insar_agent.core.store import Store
from insar_agent.runtime.probe import _implicit_engine_prefix, probe_environment

#: 总大小上限(未压缩内容合计;zip 压缩后只会更小)
MAX_TOTAL_BYTES = 50 * 1024 * 1024
#: manifest 自身的预留额度(裁剪判定时从总额度里扣除)
_MANIFEST_RESERVE = 16 * 1024

#: 日志尾部默认/降级额度(字节)
TAIL_BYTES = 256 * 1024
TAIL_BYTES_DEGRADED = 64 * 1024

#: 事件摘要默认/降级条数
EVENTS_LIMIT = 50
EVENTS_LIMIT_DEGRADED = 10

#: 事件摘要单条文本上限(字符)
_EVENT_TEXT_MAX = 300

#: schema.sql 的表闭集(行数统计只报这些;与 sqlite_master 求交,防坏库炸端点)
_KNOWN_TABLES = ("sessions", "chat_messages", "runs", "steps", "edges", "artifacts",
                 "commands", "metrics", "pending_actions", "leases", "trace")

#: 形似密钥的环境变量名片段:值不进包,只报长度
_SECRET_HINT = re.compile(r"TOKEN|KEY|SECRET|PASSWORD|PASSWD|CREDENTIAL", re.IGNORECASE)

#: 任意用户主目录形态(盘符可变、用户名可变、正反斜杠均可,兼容 JSON 转义的
#: 双反斜杠):C:\Users\<user>
_ANY_USER_HOME = re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+[^\\/\s\"'<>|:*?]+", re.IGNORECASE)
#: 带盘符的绝对路径(主目录替换后剩下的);盘符保留,余下打码
_DRIVE_PATH = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]):[\\/][^\s\"'<>|:*?]+")
#: UNC 路径(\\host\share\...)整体打码
_UNC_PATH = re.compile(r"(?<![\\\w])\\\\[^\s\"'<>|:*?\\]+(?:\\[^\s\"'<>|:*?\\]+)+")

#: zip 文件名白名单(与 api/diag_router.py 的下载校验同一口径)
DIAG_NAME_RE = re.compile(r"^diag-[0-9TZ-]+\.zip$")


# ---------------- 脱敏 ----------------


def _home_patterns() -> list[re.Pattern]:
    """当前用户主目录的正则形态(正/反斜杠混写、重复分隔符、大小写不敏感)。"""
    try:
        home = str(Path.home())
    except (RuntimeError, OSError):  # 无主目录环境(服务账户):只走通用形态
        return []
    # "C:\\Users\\x" → 每个分隔符处允许正反斜杠互换与重复(JSON 转义形态)
    parts = [re.escape(p) for p in re.split(r"[\\/]+", home) if p]
    if not parts:
        return []
    pattern = r"[\\/]+".join(parts)
    if home[:1] in ("/", "\\"):  # POSIX 主目录:根分隔符也吃进匹配
        pattern = r"[\\/]+" + pattern
    return [re.compile(pattern, re.IGNORECASE)]


def redact_text(text: str, *, mask_abs_paths: bool = True) -> str:
    """文本脱敏:主目录 → ~;(默认)其余带盘符绝对路径/UNC 打码。

    顺序敏感:先替换主目录(替换后以 ~ 开头,天然躲开绝对路径打码),
    再打码剩余的盘符路径与 UNC —— 打码保留盘符便于诊断,余下一律 ***。
    """
    for pat in _home_patterns():
        text = pat.sub("~", text)
    text = _ANY_USER_HOME.sub("~", text)  # 其他用户的主目录同样不外泄
    if mask_abs_paths:
        text = _UNC_PATH.sub(r"\\\\***", text)
        text = _DRIVE_PATH.sub(r"\1:\\***", text)
    return text


def _redact_obj(obj, *, mask_abs_paths: bool = True):
    """递归脱敏 JSON 树里的每个字符串(先脱敏后序列化,JSON 永远合法)。"""
    if isinstance(obj, str):
        return redact_text(obj, mask_abs_paths=mask_abs_paths)
    if isinstance(obj, dict):
        return {k: _redact_obj(v, mask_abs_paths=mask_abs_paths) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_redact_obj(v, mask_abs_paths=mask_abs_paths) for v in obj]
    return obj


# ---------------- 平台信息(只 platform/os 标准库,不加 psutil) ----------------


def _mem_gb() -> float | None:
    if sys.platform == "win32":
        # 复用 probe.py 的 ctypes 实现(纯标准库),失败让步为 None
        try:
            from insar_agent.runtime.probe import _windows_mem_gb
            return _windows_mem_gb()
        except Exception:  # noqa: BLE001 —— 展示项,绝不拖垮打包
            return None
    try:
        return round(os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / (1 << 30), 1)
    except (ValueError, OSError, AttributeError):
        return None


def _platform_info() -> dict:
    return {
        "os": platform.platform(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "cpu_logical": os.cpu_count(),
        "mem_gb": _mem_gb(),
    }


# ---------------- env.json ----------------


def _cached_wsl_probe() -> tuple[dict | None, bool]:
    """只读 wsl_probe 的模块级缓存:命中返回 (结果, True);绝不现场探测
    (完整探测约 20s,诊断收集必须秒级返回)。"""
    try:
        from insar_agent.runtime import wsl_probe
        for _ts, result in wsl_probe._PROBE_CACHE.values():
            return result, True
    except Exception:  # noqa: BLE001 —— 缓存形态变化不拖垮打包
        pass
    return None, False


def _collect_env(home: Path) -> dict:
    """环境探测 + 白名单环境变量。探测失败如实记 error,不抛。"""
    try:
        # check_wsl=False:wsl.exe 调用交给缓存复用,收集路径零子进程等待
        probe = probe_environment(home, with_versions=False, check_wsl=False).to_dict()
        probe_error = None
    except Exception as exc:  # noqa: BLE001 —— 探测失败也要出包(它正是要诊断的对象)
        probe, probe_error = None, f"{exc.__class__.__name__}: {exc}"

    prefix = os.environ.get("INSAR_ENGINE_PREFIX")
    if prefix:
        prefix_source = "env"
    else:
        try:
            prefix = _implicit_engine_prefix()
        except Exception:  # noqa: BLE001
            prefix = None
        prefix_source = "implicit" if prefix else "none"

    wsl_probe_result, from_cache = _cached_wsl_probe()

    env_vars: dict[str, str] = {}
    for key in sorted(os.environ):
        if not key.upper().startswith("INSAR_"):
            continue
        value = os.environ[key]
        if _SECRET_HINT.search(key):
            env_vars[key] = f"<已隐藏 {len(value)} 字符>"  # 形似密钥:只报长度
        else:
            env_vars[key] = value

    path_value = os.environ.get("PATH", "")
    return {
        "probe": probe,
        "probe_error": probe_error,
        "engine_prefix": {"value": prefix, "source": prefix_source},
        "wsl_engine_probe": wsl_probe_result,
        "wsl_probe_from_cache": from_cache,
        "env_vars": env_vars,
        # PATH 只统计条数,绝不含内容(条目常携带用户名/公司内部路径)
        "path_entry_count": len([p for p in path_value.split(os.pathsep) if p]),
    }


# ---------------- db_summary.json ----------------


def _collect_events(store: Store, limit: int) -> list[dict]:
    """最近 note/error 事件摘要(倒序合并两个持久化来源,截断长文本)。

    error 取 trace 表 error_occurred=1 的行;note 取 chat_messages 表
    role='note' 的行。store 无现成方法 → 本模块内部只读 SELECT(纪律见模块头)。
    """
    events: list[dict] = []
    for r in store.db.query(
            "SELECT ts, run_id, error_type, error_message FROM trace"
            " WHERE error_occurred=1 ORDER BY id DESC LIMIT ?", (limit,)):
        events.append({
            "kind": "error", "ts": r["ts"], "run_id": r["run_id"],
            "tag": r["error_type"] or "",
            "text": (r["error_message"] or "")[:_EVENT_TEXT_MAX],
        })
    for r in store.db.query(
            "SELECT created_at, session_id, content FROM chat_messages"
            " WHERE role='note' ORDER BY id DESC LIMIT ?", (limit,)):
        events.append({
            "kind": "note", "ts": r["created_at"], "session_id": r["session_id"],
            "tag": "chat", "text": (r["content"] or "")[:_EVENT_TEXT_MAX],
        })
    events.sort(key=lambda e: e["ts"] or 0, reverse=True)
    return events[:limit]


def _collect_db_summary(store: Store, events_limit: int) -> dict:
    counts: dict[str, int] = {}
    present = {r["name"] for r in store.db.query(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    for table in _KNOWN_TABLES:  # 表名闭集,不拼接外部输入
        if table in present:
            counts[table] = int(store.db.query_one(f"SELECT COUNT(*) AS n FROM {table}")["n"])
    recent_runs = [{
        "run_id": r["run_id"], "session_id": r["session_id"],
        "status": r["status"], "created_at": r["created_at"],
    } for r in store.list_runs()[:10]]
    return {
        "table_counts": counts,
        "recent_runs": recent_runs,
        "events": _collect_events(store, events_limit),
    }


# ---------------- run 选择与日志收集 ----------------


def _select_run(store: Store, run_id: str | None) -> tuple[dict | None, str]:
    """返回 (run, 选择依据)。显式 run_id 不存在 → KeyError(API 层转 404)。"""
    if run_id:
        run = store.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        return run, "指定 run_id"
    runs = store.list_runs()  # created_at 倒序
    for r in runs:
        if r["status"] in ("failed", "interrupted"):
            return r, "最近一个失败/中断 run"
    if runs:
        return runs[0], "无失败 run,取最近一个 run"
    return None, "库中没有任何 run"


def _tail_file(path: Path, limit: int) -> tuple[str, bool, int]:
    """读文件末尾 limit 字节 → (文本, 是否截断, 原始大小)。掐掉截断处半行。"""
    size = path.stat().st_size
    with path.open("rb") as f:
        if size > limit:
            f.seek(size - limit)
        data = f.read()
    text = data.decode("utf-8", errors="replace")
    truncated = size > limit
    if truncated and "\n" in text:
        text = text.split("\n", 1)[1]
    return text, truncated, size


def _collect_run_logs(store: Store, run: dict) -> list[tuple[str, Path]]:
    """该 run 全部 job.log 的 (zip 内相对名, 磁盘路径) 清单。

    双来源合并:工作区 .jobs/<run>/ 递归(覆盖全部 attempt)+ 各步骤落库的
    log_path(WSL 后端的作业目录不在工作区下,仅靠 rglob 会漏)。
    """
    seen: dict[Path, str] = {}
    workspace = Path(run["workspace"])
    jobs_root = workspace / ".jobs" / run["run_id"]
    if jobs_root.is_dir():
        try:
            for log in sorted(jobs_root.rglob("job.log")):
                seen[log] = log.relative_to(jobs_root).as_posix()
        except OSError:
            pass  # 枚举窗口内目录被清理:落库 log_path 仍能兜住
    for step in store.load_steps(run["run_id"]):
        if not step.log_path:
            continue
        p = Path(step.log_path)
        if p in seen or not p.is_file():
            continue
        seen[p] = f"s{step.step_id:02d}-extern/{p.name}"
    return sorted(((arc, p) for p, arc in seen.items()), key=lambda x: x[0])


def _load_provenance(store: Store, run: dict) -> tuple[dict | None, str]:
    """该 run 的 provenance:落盘文件 run_id 吻合就用它(现场证据优先),
    否则现场导出;都不行如实记原因。返回 (doc, 来源说明)。"""
    workspace = Path(run["workspace"])
    disk = workspace / "provenance.json"
    try:
        doc = json.loads(disk.read_text(encoding="utf-8"))
        if isinstance(doc, dict) and doc.get("run_id") == run["run_id"]:
            return doc, "工作区落盘文件"
    except (OSError, ValueError):
        pass
    try:
        from insar_agent.audit.contract import load_contract
        from insar_agent.core.ledger import export_provenance
        return (export_provenance(store, run["run_id"], contract=load_contract(),
                                  workspace=workspace), "现场导出")
    except Exception as exc:  # noqa: BLE001 —— 溯源缺失本身就是诊断信息
        return None, f"不可用({exc.__class__.__name__})"


# ---------------- 打包主流程 ----------------


def _zip_name(out_dir: Path) -> str:
    """diag-<UTC 时间戳>.zip,撞名补 -N;字符集严格落在下载白名单内。"""
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    name = f"diag-{stamp}.zip"
    n = 1
    while (out_dir / name).exists():
        n += 1
        name = f"diag-{stamp}-{n}.zip"
    return name


def build_diag_bundle(home: Path | str, run_id: str | None = None, *,
                      out_dir: Path | str | None = None,
                      mask_abs_paths: bool = True,
                      max_total_bytes: int = MAX_TOTAL_BYTES,
                      tail_bytes: int = TAIL_BYTES,
                      tail_bytes_degraded: int = TAIL_BYTES_DEGRADED,
                      events_limit: int = EVENTS_LIMIT,
                      events_limit_degraded: int = EVENTS_LIMIT_DEGRADED) -> Path:
    """一键打诊断包 → 返回 zip 路径(落在 out_dir,缺省 home/diagnostics)。

    run_id 显式给定但不存在 → KeyError(API 层转 404);其余一切收集失败
    (无 DB / 探测失败 / 日志缺失)都降级记录,保证「越是坏环境越出得了包」。
    """
    home = Path(home).resolve()
    out_dir = Path(out_dir) if out_dir is not None else home / "diagnostics"
    notes: list[str] = []

    def _dump(obj) -> bytes:
        return json.dumps(_redact_obj(obj, mask_abs_paths=mask_abs_paths),
                          ensure_ascii=False, indent=1).encode("utf-8")

    def _log_entry(arc: str, path: Path, limit: int) -> bytes | None:
        try:
            text, truncated, size = _tail_file(path, limit)
        except OSError as exc:
            notes.append(f"logs/{arc} 读取失败({exc.__class__.__name__}),未入包")
            return None
        if truncated:
            text = (f"[诊断包截断] 原文件 {size} 字节,仅保留末尾约 {limit} 字节\n"
                    + text)
        return redact_text(text, mask_abs_paths=mask_abs_paths).encode("utf-8")

    # ---- 收集(与 DB 的交互集中在这一段,结束即关连接) ----
    run: dict | None = None
    run_selected_by = "无数据库(insar.db 不存在)"
    db_summary: dict = {}
    log_sources: list[tuple[str, Path]] = []
    provenance_doc: dict | None = None
    provenance_source = "无 run"

    db_path = home / "insar.db"
    if db_path.is_file():
        store = Store(Database(db_path))
        try:
            run, run_selected_by = _select_run(store, run_id)
            db_summary = _collect_db_summary(store, events_limit)
            if run is not None:
                log_sources = _collect_run_logs(store, run)
                provenance_doc, provenance_source = _load_provenance(store, run)
        finally:
            store.close()
    else:
        if run_id:  # 显式点名的 run 无从核实 → 与「不存在」同口径
            raise KeyError(run_id)
        db_summary = {"error": "insar.db 不存在"}

    env_doc = _collect_env(home)

    # ---- 组装条目(可裁剪项与核心三份分开记账) ----
    core: list[tuple[str, bytes]] = [
        ("env.json", _dump(env_doc)),
    ]
    droppable: list[tuple[str, bytes]] = []
    if provenance_doc is not None:
        droppable.append(("logs/provenance.json", _dump(provenance_doc)))
    log_arcs = [(f"logs/{arc}", path) for arc, path in log_sources]
    logs: list[tuple[str, bytes]] = []
    for arc, path in log_arcs:
        data = _log_entry(arc[len("logs/"):], path, tail_bytes)
        if data is not None:
            logs.append((arc, data))

    trim_steps: list[str] = []
    budget = max_total_bytes - _MANIFEST_RESERVE

    def _total() -> int:
        return (sum(len(d) for _, d in core) + sum(len(d) for _, d in logs)
                + sum(len(d) for _, d in droppable) + len(_dump(db_summary)))

    # L1:日志尾部降到 64KB/文件
    if _total() > budget and logs:
        logs = []
        for arc, path in log_arcs:
            data = _log_entry(arc[len("logs/"):], path, tail_bytes_degraded)
            if data is not None:
                logs.append((arc, data))
        trim_steps.append(f"L1 日志尾部 {tail_bytes} → {tail_bytes_degraded} 字节/文件")

    # L2:事件条数降级
    if _total() > budget and db_summary.get("events"):
        db_summary["events"] = db_summary["events"][:events_limit_degraded]
        trim_steps.append(f"L2 事件摘要 {events_limit} → {events_limit_degraded} 条")

    # L3:逐个丢弃最大的日志/provenance 条目(核心三份永不丢)
    dropped: list[str] = []
    while _total() > budget:
        pool = logs if logs else droppable
        if not pool:
            break  # 只剩核心三份:如实带出,不再硬砍
        pool.sort(key=lambda e: len(e[1]), reverse=True)
        arc, data = pool.pop(0)
        dropped.append(f"{arc}({len(data)} 字节)")
    if dropped:
        trim_steps.append(f"L3 丢弃超额条目:{'; '.join(dropped)}")

    entries: list[tuple[str, bytes]] = (
        [("db_summary.json", _dump(db_summary))] + core + droppable + logs)

    manifest = {
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "app": get_version(),  # version_router 同一口径:version/git_head/python/platform/build
        "platform": _platform_info(),
        "run": ({"run_id": run["run_id"], "session_id": run["session_id"],
                 "status": run["status"], "created_at": run["created_at"]}
                if run else None),
        "run_selected_by": run_selected_by,
        "provenance_source": provenance_source,
        "redaction": {"home_replaced": True, "abs_paths_masked": mask_abs_paths},
        "size_budget": {
            "max_total_bytes": max_total_bytes,
            "trim_steps": trim_steps,   # 空列表 = 未触发任何裁剪
        },
        "notes": notes,
        "files": [{"name": arc, "bytes": len(data)} for arc, data in entries],
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / _zip_name(out_dir)
    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", _dump(manifest))
            for arc, data in entries:
                zf.writestr(arc, data)
    except BaseException:
        zip_path.unlink(missing_ok=True)  # 半截 zip 不留盘
        raise
    return zip_path
