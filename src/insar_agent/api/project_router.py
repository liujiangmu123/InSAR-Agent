"""项目文件夹 API:新建/打开一个目录,会话钉在上面,数据从该目录读。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from insar_agent.core.store import Store
from insar_agent.project.paths import (
    init_project_dir,
    list_tree,
    new_project_id,
    read_marker,
    read_text,
    slugify,
    write_output,
)
from insar_agent.runtime.folder_pick import pick_folder


class ProjectCreateBody(BaseModel):
    name: str
    root: str | None = None  # 缺省则在 INSAR_HOME/projects/<slug> 下建


class ProjectFileBody(BaseModel):
    rel: str
    content: str


def create_project_router(store: Store, home: Path) -> APIRouter:
    router = APIRouter(prefix="/api/projects", tags=["projects"])

    def _row(proj: dict) -> dict:
        root = Path(proj["root"])
        marker = read_marker(root) or {}
        return {
            "project_id": proj["project_id"],
            "name": proj["name"],
            "root": proj["root"],
            "created_at": proj["created_at"],
            "archived": proj.get("archived"),
            "exists": root.is_dir(),
            "marker": marker.get("project_id"),
        }

    def require(project_id: str) -> dict:
        proj = store.get_project(project_id)
        if proj is None or proj.get("archived"):
            raise HTTPException(404, f"项目 {project_id} 不存在")
        return proj

    @router.get("")
    def list_projects() -> list[dict]:
        return [_row(p) for p in store.list_projects()]

    @router.post("/pick-folder")
    def pick_folder_endpoint() -> dict:
        """弹出系统选文件夹对话框(Tauri 浏览失败时的备用通路)。

        取消 → {ok:false, cancelled:true};选定 → {ok:true, path}。
        """
        path = pick_folder("选择项目文件夹")
        if not path:
            return {"ok": False, "cancelled": True, "path": None}
        return {"ok": True, "cancelled": False, "path": path}

    @router.post("")
    def create_project(body: ProjectCreateBody) -> dict:
        name = (body.name or "").strip()
        if not name or len(name) > 80:
            raise HTTPException(400, "name 不合法:长度须为 1-80")
        if body.root and body.root.strip():
            root = Path(body.root.strip())
            if not root.is_absolute():
                raise HTTPException(400, "root 必须是绝对路径")
        else:
            root = (home / "projects" / slugify(name)).resolve()
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise HTTPException(400, f"无法创建目录:{exc}") from exc
        if not root.is_dir():
            raise HTTPException(400, f"root 不是目录:{root}")
        existing = store.get_project_by_root(str(root.resolve()))
        if existing:
            return _row(existing)
        pid = new_project_id()
        try:
            init_project_dir(root, project_id=pid, name=name)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        store.create_project(pid, name, str(root.resolve()))
        row = _row(store.get_project(pid) or {"project_id": pid, "name": name,
                                              "root": str(root.resolve()),
                                              "created_at": 0})
        items = list_tree(root)
        row["file_count"] = sum(1 for i in items if i.get("kind") == "file")
        row["item_count"] = len(items)
        return row

    @router.get("/{project_id}")
    def get_project(project_id: str) -> dict:
        return _row(require(project_id))

    @router.get("/{project_id}/files")
    def project_files(project_id: str, rel: str = "") -> dict:
        proj = require(project_id)
        root = Path(proj["root"])
        if not root.is_dir():
            raise HTTPException(400, f"项目目录不存在:{root}")
        try:
            items = list_tree(root, rel=rel) if rel else list_tree(root)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"project_id": project_id, "root": str(root), "items": items}

    @router.get("/{project_id}/file")
    def project_file(project_id: str, rel: str) -> dict:
        proj = require(project_id)
        try:
            text = read_text(Path(proj["root"]), rel)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except FileNotFoundError:
            raise HTTPException(404, f"文件不存在:{rel}")
        return {"project_id": project_id, "rel": rel, "text": text}

    @router.post("/{project_id}/file")
    def write_project_file(project_id: str, body: ProjectFileBody) -> dict:
        proj = require(project_id)
        try:
            path = write_output(Path(proj["root"]), body.rel, body.content)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(400, f"写入失败:{exc}") from exc
        return {"ok": True, "rel": body.rel, "path": str(path)}

    return router
