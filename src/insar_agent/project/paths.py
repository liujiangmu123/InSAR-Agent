"""项目文件夹路径纪律:解析、标记、有界列举/读写。LLM 不给路径,只给文件名。"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from pathlib import Path

MARKER_DIR = ".insar"
MARKER_NAME = "project.json"
DATA_DIR = "data"
OUTPUT_DIR = "output"
_MAX_TREE = 200
_MAX_DEPTH = 6
_MAX_READ = 64 * 1024
_MAX_FIND = 8000
_SLUG_RE = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)


def slugify(name: str) -> str:
    s = _SLUG_RE.sub("-", (name or "").strip()).strip("-")
    return (s[:40] or "project").lower()


def new_project_id() -> str:
    return "p-" + uuid.uuid4().hex[:10]


def safe_join(root: Path, rel: str) -> Path:
    """把相对路径钉在 root 内;含盘符/绝对路径/.. 一律拒绝。"""
    raw = (rel or "").strip().replace("\\", "/")
    if not raw or raw.startswith("/") or ":" in raw or any(
            p in ("..", "") for p in raw.split("/")):
        raise ValueError(f"非法相对路径:{rel!r}")
    root = root.resolve()
    target = (root / raw).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"路径逃出项目目录:{rel!r}") from exc
    return target


def init_project_dir(root: Path, *, project_id: str, name: str) -> dict:
    """创建 data/output/.insar 并写入 project.json。已存在标记则复用 id。"""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    if not root.is_dir():
        raise ValueError(f"项目根不是目录:{root}")
    marker_dir = root / MARKER_DIR
    marker_dir.mkdir(parents=True, exist_ok=True)
    (root / DATA_DIR).mkdir(exist_ok=True)
    (root / OUTPUT_DIR).mkdir(exist_ok=True)
    (marker_dir / "sessions").mkdir(exist_ok=True)
    existing = read_marker(root)
    if existing:
        existing["name"] = name or existing.get("name") or name
        (marker_dir / MARKER_NAME).write_text(
            json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
        return existing
    payload = {
        "project_id": project_id,
        "name": name,
        "created_at": time.time(),
        "root": str(root.resolve()),
    }
    tmp = marker_dir / (MARKER_NAME + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(marker_dir / MARKER_NAME)
    return payload


def read_marker(root: Path) -> dict | None:
    path = Path(root) / MARKER_DIR / MARKER_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("project_id") else None


def list_tree(root: Path, *, rel: str = "", max_entries: int = _MAX_TREE,
              max_depth: int = _MAX_DEPTH) -> list[dict]:
    """有界列举。跳过 .insar 内部会话目录的大产物,仍列出 .insar/project.json。"""
    base = Path(root).resolve() if not rel else safe_join(Path(root), rel)
    if not base.is_dir():
        return []
    out: list[dict] = []
    root_res = Path(root).resolve()

    def walk(cur: Path, depth: int) -> None:
        if len(out) >= max_entries or depth > max_depth:
            return
        try:
            kids = sorted(cur.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError:
            return
        for child in kids:
            if len(out) >= max_entries:
                return
            if child.name.startswith(".") and child.name != MARKER_DIR:
                continue
            try:
                rel_s = child.resolve().relative_to(root_res).as_posix()
            except (ValueError, OSError):
                continue
            if child.is_dir():
                out.append({"name": child.name, "rel": rel_s, "kind": "dir"})
                if child.name != MARKER_DIR or depth == 0:
                    walk(child, depth + 1)
            else:
                try:
                    size = child.stat().st_size
                except OSError:
                    size = 0
                out.append({"name": child.name, "rel": rel_s, "kind": "file",
                            "size": size})

    walk(base, 0)
    return out


def find_in_project(root: Path, name: str, *, max_files: int = _MAX_FIND) -> list[dict]:
    """在项目树内按文件名(不含路径)查找。不跟随符号链接,跳过 .insar/sessions。"""
    needle = (name or "").strip()
    if not needle or any(tok in needle for tok in ("/", "\\", "..", ":")):
        return []
    root_res = Path(root).resolve()
    if not root_res.is_dir():
        return []
    hits: list[dict] = []
    seen = 0
    skip = {MARKER_DIR + "/sessions"}
    for dirpath, dirnames, filenames in os.walk(root_res, followlinks=False):
        rel_dir = Path(dirpath).resolve().relative_to(root_res).as_posix()
        if rel_dir == ".":
            rel_dir = ""
        if rel_dir.startswith(".insar/sessions"):
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if not d.startswith(".") or d == MARKER_DIR]
        for fname in filenames:
            seen += 1
            if seen > max_files:
                return hits
            if needle.lower() not in fname.lower():
                continue
            rel = f"{rel_dir}/{fname}" if rel_dir else fname
            path = Path(dirpath) / fname
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            hits.append({"name": fname, "rel": rel, "kind": "file", "size": size})
    return hits


def read_text(root: Path, rel: str, *, max_bytes: int = _MAX_READ) -> str:
    path = safe_join(Path(root), rel)
    if not path.is_file():
        raise FileNotFoundError(rel)
    data = path.read_bytes()[: max_bytes + 1]
    clipped = data[:max_bytes]
    text = clipped.decode("utf-8", errors="replace")
    if len(data) > max_bytes:
        text += f"\n…(截断,仅读前 {max_bytes} 字节)"
    return text


def write_output(root: Path, rel: str, content: str) -> Path:
    """只允许写到 output/ 或 .insar/ 下,原子替换。"""
    norm = (rel or "").replace("\\", "/").lstrip("/")
    if not (norm.startswith(OUTPUT_DIR + "/") or norm.startswith(MARKER_DIR + "/")):
        raise ValueError("只能写入项目的 output/ 或 .insar/ 目录")
    path = safe_join(Path(root), norm)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)
    return path
