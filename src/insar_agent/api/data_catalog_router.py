# -*- coding: utf-8 -*-
"""数据集清单路由(/api/datasets*):文件面板「数据集」区的数据源。

扫描根每次请求时解析(环境变量与配置文件可热变,不在启动时固化):
  1. INSAR_DATA_DIR 环境变量(设置且是目录时);
  2. <home>/datasets(工作区约定目录,存在时);
  3. <home>/datasets_roots.json 里用户添加的自定义根(POST /api/datasets/roots
     持久化,同目录 tmp + os.replace 原子写,口径同 setup_router 的 settings.json)。

端点:
  - GET  /api/datasets          清单(60s TTL 缓存;?rescan=1 强制重扫)——
                                扫描只读文件系统元数据(catalog.py),秒级;
                                TTL 挡住前端切面板的重复扫描;
  - POST /api/datasets/roots    添加自定义扫描根:只收「存在的绝对路径目录」,
                                拒绝相对路径与 .. 段(路径穿越);
  - GET  /api/datasets/{id}     单个详情 + 文件清单前 200 项。id 是路径哈希,
                                查找只在扫描结果内进行 —— 用户输入不参与
                                文件系统寻址,天然无穿越面。

错误信息可回显用户刚提交的路径(本地单用户形态,提交者即看到者),
但绝不回显其他来源的磁盘路径细节。挂载:app.py 一行 include(数据源注入
home,模式同 create_setup_router)。
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from insar_agent.data.catalog import dataset_id, identify_dataset, list_files, scan_roots

#: 清单缓存 TTL(秒):扫描是纯元数据读,慢盘/网络盘上也应挡住高频重扫
CACHE_TTL_S = 60.0

#: 详情端点的文件清单上限
_DETAIL_FILES_LIMIT = 200


class RootBody(BaseModel):
    path: str


def _atomic_write_json(path: Path, data: object) -> None:
    """同目录 tmp + os.replace:进程崩溃也不留半截 JSON(同 setup_router)。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp",
                               dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def create_data_catalog_router(home: Path | str,
                               *, ttl_seconds: float = CACHE_TTL_S) -> APIRouter:
    """构造数据集路由。home 由 create_app 注入(工作区根,INSAR_HOME)。

    ttl_seconds 是测试缝:默认 60s;测试注入 0 验证「过期即重扫」。
    """
    router = APIRouter()
    home = Path(home)
    roots_file = home / "datasets_roots.json"

    # 清单缓存:{at: 扫描时刻, data: 响应体};FastAPI 同步端点在线程池并发,
    # check-then-scan 不互斥会让并发首请求各扫一遍(同 app.py drivers_lock 教训)
    cache: dict = {"at": 0.0, "data": None}
    lock = threading.Lock()

    def custom_roots() -> list[str]:
        """读 datasets_roots.json;缺失/损坏/形状不对一律回空表(不炸清单)。"""
        try:
            data = json.loads(roots_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(data, list):
            return []
        return [x for x in data if isinstance(x, str) and x]

    def resolve_scan_roots() -> list[Path]:
        """三个来源的扫描根合集(存在的目录才算数,按归一路径去重)。"""
        candidates: list[Path] = []
        env_dir = (os.environ.get("INSAR_DATA_DIR") or "").strip()
        if env_dir:
            candidates.append(Path(env_dir))
        candidates.append(home / "datasets")
        candidates.extend(Path(s) for s in custom_roots())
        roots: list[Path] = []
        seen: set[str] = set()
        for p in candidates:
            try:
                if not p.is_dir():
                    continue
            except OSError:
                continue
            key = dataset_id(p)  # 与数据集 id 同一套路径归一(大小写/分隔符)
            if key not in seen:
                seen.add(key)
                roots.append(p)
        return roots

    def scan(*, force: bool) -> dict:
        with lock:
            now = time.time()
            if (not force and cache["data"] is not None
                    and now - cache["at"] < ttl_seconds):
                return {**cache["data"], "cached": True}
            roots = resolve_scan_roots()
            data = {
                "roots": [str(r) for r in roots],
                "datasets": scan_roots(roots),
                "scanned_at": now,
            }
            cache["at"] = now
            cache["data"] = data
            return {**data, "cached": False}

    def invalidate() -> None:
        with lock:
            cache["at"] = 0.0
            cache["data"] = None

    @router.get("/api/datasets")
    def datasets(rescan: int = 0):
        """数据集清单:{roots, datasets, scanned_at, cached}。?rescan=1 穿透缓存。"""
        return scan(force=bool(rescan))

    @router.post("/api/datasets/roots")
    def add_root(body: RootBody):
        """添加自定义扫描根并持久化;返回 {ok, path, roots(自定义根全表)}。"""
        raw = body.path.strip()
        if not raw:
            raise HTTPException(400, "path 不能为空")
        p = Path(raw)
        # 绝对路径硬要求:相对路径的锚点是服务进程 cwd,用户看到的和服务解析的
        # 会是两个目录;Windows 盘符相对形态(\x 或 C:x)同样拒绝
        if not p.is_absolute():
            raise HTTPException(400, f"必须是绝对路径(收到:{raw})")
        if any(part == ".." for part in p.parts):
            raise HTTPException(400, "路径不允许包含 .. 段(拒绝路径穿越)")
        try:
            if not p.is_dir():
                raise HTTPException(400, f"目录不存在或不是目录:{raw}")
            resolved = str(p.resolve())
        except OSError:
            raise HTTPException(400, f"路径不可访问:{raw}")
        existing = custom_roots()
        if dataset_id(resolved) not in {dataset_id(x) for x in existing}:
            existing.append(resolved)
            try:
                _atomic_write_json(roots_file, existing)
            except OSError:
                raise HTTPException(500, "扫描根配置写入失败(datasets_roots.json)")
        invalidate()  # 新根立即可见,不等 TTL
        return {"ok": True, "path": resolved, "roots": existing}

    @router.get("/api/datasets/{ds_id}")
    def dataset_detail(ds_id: str):
        """单个数据集详情 + 文件清单前 200 项(路径升序,只 stat 不读内容)。"""
        data = scan(force=False)
        entry = next((d for d in data["datasets"] if d["id"] == ds_id), None)
        if entry is None:
            # 缓存窗口内刚落盘的数据:强制重扫一次再判 404
            data = scan(force=True)
            entry = next((d for d in data["datasets"] if d["id"] == ds_id), None)
        if entry is None:
            raise HTTPException(404, f"数据集 {ds_id} 不存在(可先 rescan=1 重扫)")
        # 详情按当前盘面重新识别:清单缓存最多 60s 旧,详情页应看到最新状态
        fresh = identify_dataset(Path(entry["path"]))
        files, more = list_files(Path(entry["path"]), limit=_DETAIL_FILES_LIMIT)
        return {**fresh, "files": files, "files_truncated": more}

    return router
