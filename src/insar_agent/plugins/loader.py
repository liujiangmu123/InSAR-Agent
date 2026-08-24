"""声明式插件清单加载器(只读 YAML,绝不 import/exec 用户代码)。

路径:
  1. 内置 src/insar_agent/plugins/catalog/*.yaml
  2. <home>/plugins/*/plugin.yaml(必须是 home/plugins 的直接子目录)

损坏 YAML / 缺必填字段 → 警告跳过,绝不 500。未知键警告不拒载。
匹配只看文件名,不读文件内容(与 data/catalog.py 纪律一致)。
"""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass
from pathlib import Path

import yaml

#: 内置清单目录(与本文件同包;不经 importlib 加载任何用户代码)
CATALOG_DIR = Path(__file__).resolve().parent / "catalog"

_KINDS = frozenset({"viewer", "skill", "engine"})
_STATUSES = frozenset({"ready", "reserved"})
_RENDERS = frozenset({
    "png_gallery", "timeseries_point", "external", "none",
    "table", "json_tree", "hdf5_meta", "raster_png",
    "zip_list", "pdf_meta", "binary_meta", "text_plain",
})
_SIDEBARS = frozenset({"figures", "files", "none"})
_SKILL_TYPES = frozenset({"step", "scenario"})
_TOP_FIELDS = frozenset({
    "id", "kind", "status", "title", "version", "match",
    "render", "sidebar", "note", "skill_type",
})
_MATCH_FIELDS = frozenset({"suffixes", "name_contains"})
_REQUIRED = ("id", "kind", "status", "title")

_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SAFE_DIR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class PluginWarning(UserWarning):
    """插件清单校验警告(未知字段 / 跳过原因)。"""


@dataclass(frozen=True)
class Plugin:
    id: str
    kind: str
    status: str
    title: str
    version: str
    render: str
    sidebar: str
    note: str
    skill_type: str | None
    match_suffixes: tuple[str, ...]
    match_name_contains: tuple[str, ...]
    source: str  # builtin | home
    sort_key: str  # catalog 文件名(home 为 <dir>/plugin.yaml)
    origin: str  # 相对定位: catalog/<file> 或 plugins/<dir>

    def as_public(self) -> dict:
        """API 条目:无磁盘绝对路径。"""
        item = {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "title": self.title,
            "version": self.version,
            "render": self.render,
            "sidebar": self.sidebar,
            "note": self.note,
            "source": self.source,
        }
        if self.kind == "skill" and self.skill_type:
            item["skill_type"] = self.skill_type
        match: dict[str, list[str]] = {}
        if self.match_suffixes:
            match["suffixes"] = list(self.match_suffixes)
        if self.match_name_contains:
            match["name_contains"] = list(self.match_name_contains)
        if match:
            item["match"] = match
        return item

    def as_viewer_hit(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "title": self.title,
            "render": self.render,
        }


def _warn(msg: str) -> None:
    warnings.warn(f"plugins: {msg}", PluginWarning, stacklevel=2)


def _within(path: Path, root: Path) -> bool:
    """resolve 后仍落在 root 内;原路径含 .. 段一律拒绝。"""
    if ".." in path.parts:
        return False
    try:
        resolved = path.resolve()
        base = root.resolve()
    except OSError:
        return False
    try:
        return resolved.is_relative_to(base)
    except (OSError, ValueError):
        return False


def _norm_suffix(value: str) -> str:
    text = value.strip().lower()
    if not text:
        return ""
    return text if text.startswith(".") else f".{text}"


def _str_list(value, *, field: str, label: str) -> tuple[str, ...] | None:
    """解析字符串列表。非法 → None(交上层拒载)。"""
    if not isinstance(value, list):
        _warn(f"{label}: {field} 必须是列表,跳过")
        return None
    out: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            _warn(f"{label}: {field} 项必须是非空字符串,跳过")
            return None
        out.append(item.strip())
    return tuple(out)


def _read_mapping(path: Path, label: str) -> dict | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        _warn(f"{label}: 不可读({exc.__class__.__name__}),跳过")
        return None
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        _warn(f"{label}: YAML 解析失败,跳过")
        return None
    if not isinstance(data, dict):
        _warn(f"{label}: 顶层必须是映射,跳过")
        return None
    return data


def _parse_plugin(data: dict, *, source: str, label: str,
                  sort_key: str, origin: str) -> Plugin | None:
    unknown = set(data) - _TOP_FIELDS
    if unknown:
        _warn(f"{label}: 未知字段 {sorted(map(str, unknown))}(闭集外,已忽略)")

    for key in _REQUIRED:
        if key not in data or data[key] in (None, ""):
            _warn(f"{label}: 缺 {key},跳过")
            return None

    ident = data["id"]
    if not isinstance(ident, str) or not _ID_RE.match(ident):
        _warn(f"{label}: id 必须是 kebab-case,跳过")
        return None
    kind = data["kind"]
    if kind not in _KINDS:
        _warn(f"{label}: kind 必须是 viewer|skill|engine,跳过")
        return None
    status = data["status"]
    if status not in _STATUSES:
        _warn(f"{label}: status 必须是 ready|reserved,跳过")
        return None
    title = data["title"]
    if not isinstance(title, str) or not title.strip():
        _warn(f"{label}: title 必须是非空字符串,跳过")
        return None

    version = data.get("version", "0.1.0")
    if version in (None, ""):
        version = "0.1.0"
    elif not isinstance(version, str):
        _warn(f"{label}: version 必须是字符串,跳过")
        return None

    render = data.get("render", "none")
    if render in (None, ""):
        render = "none"
    if render not in _RENDERS:
        _warn(f"{label}: render 必须是闭集值,跳过")
        return None
    sidebar = data.get("sidebar", "none")
    if sidebar in (None, ""):
        sidebar = "none"
    if sidebar not in _SIDEBARS:
        _warn(f"{label}: sidebar 必须是闭集值,跳过")
        return None

    note = data.get("note", "")
    if note is None:
        note = ""
    elif not isinstance(note, str):
        _warn(f"{label}: note 必须是字符串,跳过")
        return None

    skill_type = data.get("skill_type")
    if kind == "skill":
        if skill_type not in _SKILL_TYPES:
            _warn(f"{label}: skill 必须声明 skill_type=step|scenario,跳过")
            return None
    elif skill_type not in (None, ""):
        _warn(f"{label}: skill_type 仅 kind=skill,已忽略")
        skill_type = None
    else:
        skill_type = None

    suffixes: tuple[str, ...] = ()
    contains: tuple[str, ...] = ()
    raw_match = data.get("match")
    if raw_match is None:
        pass
    elif not isinstance(raw_match, dict):
        _warn(f"{label}: match 必须是映射,跳过")
        return None
    else:
        unknown_m = set(raw_match) - _MATCH_FIELDS
        if unknown_m:
            keys = sorted(map(str, unknown_m))
            _warn(f"{label}: match 未知字段 {keys}(闭集外,已忽略)")
        if "suffixes" in raw_match and raw_match["suffixes"] is not None:
            raw = _str_list(raw_match["suffixes"], field="suffixes", label=label)
            if raw is None:
                return None
            normalized = tuple(s for s in (_norm_suffix(x) for x in raw) if s)
            suffixes = tuple(dict.fromkeys(normalized))
        if "name_contains" in raw_match and raw_match["name_contains"] is not None:
            raw = _str_list(
                raw_match["name_contains"], field="name_contains", label=label)
            if raw is None:
                return None
            contains = raw

    return Plugin(
        id=ident, kind=kind, status=status, title=title.strip(),
        version=version, render=render, sidebar=sidebar, note=note,
        skill_type=skill_type, match_suffixes=suffixes,
        match_name_contains=contains, source=source,
        sort_key=sort_key, origin=origin,
    )


def _load_one(path: Path, *, source: str, label: str,
              sort_key: str, origin: str) -> Plugin | None:
    data = _read_mapping(path, label)
    if data is None:
        return None
    return _parse_plugin(
        data, source=source, label=label, sort_key=sort_key, origin=origin)


def _load_builtin() -> list[Plugin]:
    try:
        catalog = CATALOG_DIR.resolve()
    except OSError:
        return []
    try:
        if not catalog.is_dir():
            return []
        files = sorted(catalog.glob("*.yaml"), key=lambda p: p.name.lower())
    except OSError:
        return []
    out: list[Plugin] = []
    for path in files:
        if not _within(path, catalog):
            _warn(f"catalog/{path.name}: 越出 catalog,跳过")
            continue
        plugin = _load_one(
            path, source="builtin", label=f"catalog/{path.name}",
            sort_key=path.name, origin=f"catalog/{path.name}")
        if plugin is not None:
            out.append(plugin)
    return out


def _load_home(home: Path) -> list[Plugin]:
    try:
        home_res = Path(home).resolve()
        root = (home_res / "plugins").resolve()
    except OSError:
        return []
    try:
        if not root.is_dir():
            return []
    except OSError:
        return []
    if not root.is_relative_to(home_res):
        _warn("plugins: 目录越出工作区,跳过全部 home 插件")
        return []
    try:
        children = sorted(root.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return []
    out: list[Plugin] = []
    for child in children:
        try:
            if not child.is_dir():
                continue
        except OSError:
            continue
        if child.name in {".", ".."} or ".." in child.name:
            _warn("plugins: 拒绝穿越目录,跳过")
            continue
        if not _SAFE_DIR_RE.match(child.name):
            _warn("plugins: 非法目录名,跳过")
            continue
        label = f"plugins/{child.name}"
        yaml_path = child / "plugin.yaml"
        if not _within(yaml_path, root):
            _warn(f"{label}: plugin.yaml 越出 plugins 目录,跳过")
            continue
        try:
            if not yaml_path.is_file():
                continue
        except OSError:
            continue
        plugin = _load_one(
            yaml_path, source="home", label=label,
            sort_key=f"{child.name}/plugin.yaml", origin=label)
        if plugin is not None:
            out.append(plugin)
    return out


def load_plugins(home: Path | str | None = None) -> list[Plugin]:
    """加载内置 + 可选 home 插件。同 id 先到者生效(内置优先)。"""
    seen: set[str] = set()
    plugins: list[Plugin] = []
    for plugin in (*_load_builtin(), *(_load_home(Path(home)) if home else ())):
        if plugin.id in seen:
            _warn(f"{plugin.origin}: id {plugin.id} 与已加载插件重复,跳过")
            continue
        seen.add(plugin.id)
        plugins.append(plugin)
    return plugins


def plugin_roots(plugins: list[Plugin]) -> list[str]:
    """API roots:内置不报磁盘绝对路径;home 只报 plugins/<dir>。"""
    roots: list[str] = []
    if any(p.source == "builtin" for p in plugins):
        roots.append("catalog")
    seen: set[str] = set()
    for plugin in plugins:
        if plugin.source != "home":
            continue
        rel = plugin.origin  # plugins/<dir>
        if rel.startswith("plugins/") and rel not in seen:
            seen.add(rel)
            roots.append(rel)
    return roots


def _viewer_matches(plugin: Plugin, filename: str) -> bool:
    lower = filename.lower()
    suffix = Path(filename).suffix.lower()
    has_suf = bool(plugin.match_suffixes)
    has_con = bool(plugin.match_name_contains)
    if not has_suf and not has_con:
        return False
    ok = True
    if has_suf:
        ok = ok and suffix in plugin.match_suffixes
    if has_con:
        ok = ok and any(n.lower() in lower for n in plugin.match_name_contains)
    return ok


def match_viewers(filename: str,
                  plugins: list[Plugin] | None = None) -> list[dict]:
    """按文件名匹配 kind=viewer 的插件(含 reserved)。不读文件内容。

    suffixes 与 name_contains 同时声明则 AND;只声明一项则该项即可。
    多命中按 catalog 文件名排序。
    """
    items = load_plugins() if plugins is None else plugins
    name = Path(str(filename)).name
    hits = [p for p in items if p.kind == "viewer" and _viewer_matches(p, name)]
    hits.sort(key=lambda p: p.sort_key.lower())
    return [p.as_viewer_hit() for p in hits]
