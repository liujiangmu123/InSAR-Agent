"""把相对路径钉在 run 工作区内(与 /api/artifact-file 同一穿越口径)。"""

from __future__ import annotations

from pathlib import Path


def resolve_workspace_file(workspace: Path, rel: str) -> Path | None:
    """相对路径 → 工作区内已存在的文件;越界/绝对/缺文件 → None。"""
    if not rel or not isinstance(rel, str):
        return None
    raw = rel.replace("\\", "/").lstrip("/")
    if not raw or raw.startswith("..") or "/../" in f"/{raw}/":
        return None
    base = workspace.resolve()
    try:
        target = (base / raw).resolve()
    except OSError:
        return None
    try:
        if not target.is_relative_to(base):
            return None
    except (ValueError, OSError):
        return None
    if target == base or not target.is_file():
        return None
    return target
