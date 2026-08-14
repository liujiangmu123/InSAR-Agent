"""JIT 学习与受控探针:读本地技能/安装知识、有界网页检索、闭集探针。

LLM 不给命令、不给路径、不给任意 Python。探针 kind 与可导入模块都是写死闭集。
"""

from __future__ import annotations

import importlib
import shutil
import subprocess
import sys
from pathlib import Path

from insar_agent.loop.budget import clip_summary
from insar_agent.runtime.install_guide import ENGINE_ORDER, install_hint_for
from insar_agent.skills.loader import SECTION_FAILURES, SECTION_PARAMS, load_skills

#: probe_scratch.kind 闭集
PROBE_KINDS = ("import", "file_meta", "cli_help")

#: import 探针:对外短名 → 真实模块(只允许这些)
_IMPORT_ALLOW: dict[str, str] = {
    "gdal": "osgeo.gdal",
    "osgeo": "osgeo.gdal",
    "h5py": "h5py",
    "numpy": "numpy",
    "mintpy": "mintpy",
    "rasterio": "rasterio",
}

#: cli_help:引擎短名 → 写死 argv(绝不用模型给的参数)
_CLI_HELP: dict[str, tuple[str, ...]] = {
    "gdal": ("gdalinfo", "--version"),
    "snaphu": ("snaphu", "-h"),
}

_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def _skill_alias_map() -> dict[str, int]:
    out: dict[str, int] = {}
    for sid, skill in load_skills().items():
        out[skill.name.lower()] = sid
        out[str(sid)] = sid
        # 目录短名:06-unwrap → unwrap
        if "-" in skill.name:
            out[skill.name.split("-", 1)[-1].lower()] = sid
    return out


def learn_tool(tool: str) -> tuple[str, bool]:
    """读本地安装知识 + 步骤技能。不联网、不执行安装。"""
    name = (tool or "").strip().lower()
    if not name:
        return "learn_tool 缺 tool", False
    parts: list[str] = []
    if name in ENGINE_ORDER:
        parts.append(install_hint_for(name))
    aliases = _skill_alias_map()
    sid = aliases.get(name)
    if sid is not None:
        params = clip_summary(skill_text(sid, SECTION_PARAMS), max_chars=400)
        fails = clip_summary(skill_text(sid, SECTION_FAILURES), max_chars=280)
        if params:
            parts.append(f"步骤 {sid} 参数启发式:{params}")
        if fails:
            parts.append(f"步骤 {sid} 失败处置:{fails}")
    if not parts:
        known = "/".join(ENGINE_ORDER) + " 或步骤技能名"
        return f"未收录 {name!r} 的本地知识(可学:{known})", False
    return clip_summary("\n".join(parts), max_chars=900), True


def skill_text(step_id: int, section: str) -> str:
    from insar_agent.skills.loader import skill_section_text
    return skill_section_text(step_id, section)


def search_docs(query: str) -> tuple[str, bool]:
    """本地技能关键词 + 可选网页检索(只发查询词)。"""
    q = (query or "").strip()
    if not q:
        return "search_docs 缺 query", False
    hits: list[str] = []
    q_l = q.lower()
    for sid, skill in load_skills().items():
        blob = f"{skill.name} {skill.description} {' '.join(skill.sections)}"
        if q_l in blob.lower() or any(tok in blob.lower() for tok in q_l.split() if len(tok) > 1):
            excerpt = clip_summary(skill.sections.get(SECTION_PARAMS, ""), max_chars=180)
            hits.append(f"技能 {skill.name}(步骤 {sid}):{excerpt or skill.description}")
        if len(hits) >= 3:
            break
    web_lines: list[str] = []
    try:
        from insar_agent.net.websearch import web_search
        rows = web_search(q, k=3, timeout=8.0)
        for row in rows:
            title = str(row.get("title") or "").strip()
            snippet = clip_summary(str(row.get("snippet") or ""), max_chars=80)
            if title:
                web_lines.append(f"- {title}:{snippet}" if snippet else f"- {title}")
    except Exception as exc:  # noqa: BLE001 —— 检索失败不炸周期
        web_lines.append(f"(网页检索不可用:{type(exc).__name__})")
    chunks = []
    if hits:
        chunks.append("本地技能:\n" + "\n".join(hits))
    if web_lines:
        chunks.append("网页(只发了查询词):\n" + "\n".join(web_lines))
    if not chunks:
        return f"没有检索到与「{clip_summary(q, max_chars=40)}」相关的文档", False
    return clip_summary("\n".join(chunks), max_chars=900), True


def probe_scratch(kind: str, name: str, *,
                  project_root: Path | None = None) -> tuple[str, bool]:
    """受控探针。kind/name 必须已过 facade 闭集校验。"""
    if kind == "import":
        return _probe_import(name)
    if kind == "cli_help":
        return _probe_cli(name)
    if kind == "file_meta":
        return _probe_file_meta(name, project_root)
    return f"probe kind 越界:{kind!r}", False


def _probe_import(name: str) -> tuple[str, bool]:
    mod_name = _IMPORT_ALLOW.get((name or "").strip().lower())
    if not mod_name:
        return (f"不可探测模块 {name!r}(闭集:{'/'.join(sorted(_IMPORT_ALLOW))})",
                False)
    try:
        mod = importlib.import_module(mod_name)
    except Exception as exc:  # noqa: BLE001
        return f"{mod_name} 不可导入:{type(exc).__name__}: {exc}", False
    ver = getattr(mod, "__version__", None) or getattr(mod, "Version", None)
    extra = f" version={ver}" if ver else ""
    return f"{mod_name} 可导入{extra}", True


def _probe_cli(name: str) -> tuple[str, bool]:
    argv = _CLI_HELP.get((name or "").strip().lower())
    if not argv:
        return (f"无写死的 --help 探针:{name!r}(闭集:{'/'.join(_CLI_HELP)})",
                False)
    exe = shutil.which(argv[0])
    if exe is None:
        return f"本机 PATH 找不到 {argv[0]}", False
    try:
        proc = subprocess.run(
            [exe, *argv[1:]],
            capture_output=True, text=True, timeout=8,
            creationflags=_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except Exception as exc:  # noqa: BLE001
        return f"{argv[0]} 探针失败:{type(exc).__name__}: {exc}", False
    text = (proc.stdout or proc.stderr or "").strip()
    head = clip_summary(text.replace("\r\n", "\n"), max_chars=400)
    return f"{argv[0]} rc={proc.returncode}:{head or '(无输出)'}", proc.returncode in (0, 1, 2)


def _probe_file_meta(name: str, project_root: Path | None) -> tuple[str, bool]:
    if project_root is None or not project_root.is_dir():
        return "未绑定项目,无法做文件探针", False
    from insar_agent.project.paths import find_in_project

    hits = find_in_project(project_root, name)
    if not hits:
        return f"项目里没有名为 {name!r} 的文件", False
    rel = hits[0]["rel"]
    path = project_root / rel
    try:
        size = path.stat().st_size
        magic = path.read_bytes()[:16]
    except OSError as exc:
        return f"读 {rel} 失败:{exc}", False
    hex_m = magic.hex()
    extra = ""
    if path.suffix.lower() in {".tif", ".tiff", ".grd"}:
        extra = _gdal_size(path)
    elif path.suffix.lower() in {".h5", ".he5", ".hdf5"}:
        extra = _h5_keys(path)
    more = f"(另有 {len(hits) - 1} 个同名)" if len(hits) > 1 else ""
    return (f"{rel}{more}: {size} bytes, magic={hex_m}{extra}", True)


def _gdal_size(path: Path) -> str:
    try:
        from osgeo import gdal
        ds = gdal.Open(str(path))
        if ds is None:
            return ", GDAL 打不开"
        return f", GDAL {ds.RasterXSize}x{ds.RasterYSize}x{ds.RasterCount}"
    except Exception:  # noqa: BLE001
        return ", 无 GDAL"


def _h5_keys(path: Path) -> str:
    try:
        import h5py
        with h5py.File(path, "r") as f:
            keys = list(f.keys())[:8]
        return f", HDF5 keys={keys}"
    except Exception:  # noqa: BLE001
        return ", 无 h5py"
