# -*- coding: utf-8 -*-
"""处理路线推荐路由(GET /api/recommend?dataset_id=...)。

数据集卡片「处理建议」按钮的数据源:经数据集目录取条目,组合环境探测缓存,
交给 planner/recommend.recommend_routes(纯规则引擎)出路线优劣对比。

响应:{dataset: 数据集条目, routes: [RouteOption.to_dict()...]};
dataset_id 不存在 → 强制重扫一次仍未命中才 404(口径同 data_catalog_router
的详情端点:缓存窗口内刚落盘的数据不误报)。

扫描根解析与清单缓存刻意镜像 data_catalog_router(INSAR_DATA_DIR →
<home>/datasets → datasets_roots.json 三源合一、60s TTL + 锁):推荐端点
必须与数据集清单看到同一批数据集、同一套 id,两处口径变更须同步。
不直接 import 那边的闭包 —— 路由工厂各自持有缓存,互不共享状态。

环境探测缓存:probe_environment(纯查询,毫秒级)+ WSL 引擎合并(口径同
app.py /api/env:probe_wsl_engines_cached 自带 TTL,失败静默不拖垮推荐),
结果按 probe_ttl_seconds(默认 300s)缓存 —— 引擎装卸是低频事件,推荐点击
是高频交互,不值得每次点击都重探。probe 参数是测试缝:注入固定 ProbeResult
后走纯确定性路径,绝不触发真实探测。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException

# dataset_id 起别名:端点查询参数按 API 契约就叫 dataset_id,避免名字遮蔽
from insar_agent.data.catalog import dataset_id as path_key
from insar_agent.data.catalog import scan_roots
from insar_agent.planner.recommend import recommend_routes
from insar_agent.runtime.probe import ProbeResult, probe_environment

log = logging.getLogger(__name__)

#: 清单缓存 TTL(秒):与 data_catalog_router.CACHE_TTL_S 同值同语义
CACHE_TTL_S = 60.0

#: 环境探测缓存 TTL(秒):引擎装卸低频,5 分钟内复用同一份探测
PROBE_TTL_S = 300.0


def _live_probe(home: Path) -> ProbeResult:
    """真实探测:原生引擎(PATH/conda 前缀)+ WSL 引擎合并(失败静默)。

    check_wsl=False:wsl_status 的发行版枚举交给 probe_wsl_engines_cached
    一并覆盖(带 TTL 缓存),不在这里重复起 wsl.exe 子进程。
    """
    probe = probe_environment(home, check_wsl=False)
    try:
        from insar_agent.runtime.wsl_probe import merge_wsl_probe, probe_wsl_engines_cached

        wsl_result = probe_wsl_engines_cached(timeout=30.0)  # TTL 缓存,与向导/env 共享
        if wsl_result.get("ok"):
            merge_wsl_probe(probe, wsl_result)
    except Exception:  # noqa: BLE001 —— 可选探测绝不拖垮推荐端点(同 /api/env 口径)
        log.debug("WSL 引擎探测合并失败,按未探测处置", exc_info=True)
    return probe


def create_recommend_router(home: Path | str, *, ttl_seconds: float = CACHE_TTL_S,
                            probe_ttl_seconds: float = PROBE_TTL_S,
                            probe: ProbeResult | None = None) -> APIRouter:
    """构造推荐路由。home 由 create_app 注入(工作区根,INSAR_HOME)。

    ttl_seconds / probe_ttl_seconds 是测试缝(注入 0 验证过期重扫/重探);
    probe 注入固定探测结果后,真实探测路径(_live_probe)完全不触发。
    """
    router = APIRouter()
    home = Path(home)
    roots_file = home / "datasets_roots.json"

    # 两级缓存 + 锁:同步端点在线程池并发,check-then-scan 不互斥会并发重扫
    # (同 data_catalog_router 的 drivers_lock 教训)
    cache: dict = {"at": 0.0, "datasets": None}
    probe_cache: dict = {"at": 0.0, "probe": probe}
    lock = threading.Lock()

    def custom_roots() -> list[str]:
        """读 datasets_roots.json;缺失/损坏/形状不对回空表(同 catalog 路由)。"""
        try:
            data = json.loads(roots_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(data, list):
            return []
        return [x for x in data if isinstance(x, str) and x]

    def resolve_scan_roots() -> list[Path]:
        """三源合一的扫描根(镜像 data_catalog_router.resolve_scan_roots,
        存在的目录才算数、按归一路径去重;口径变更两处须同步)。"""
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
            key = path_key(p)  # 与数据集 id 同一套路径归一
            if key not in seen:
                seen.add(key)
                roots.append(p)
        return roots

    def scan(*, force: bool) -> list[dict]:
        with lock:
            now = time.time()
            if (not force and cache["datasets"] is not None
                    and now - cache["at"] < ttl_seconds):
                return cache["datasets"]
            cache["datasets"] = scan_roots(resolve_scan_roots())
            cache["at"] = now
            return cache["datasets"]

    def current_probe() -> ProbeResult:
        # 注入的固定 probe 永不过期(测试缝);真实探测按 TTL 复用
        if probe is not None:
            return probe
        with lock:
            now = time.time()
            if (probe_cache["probe"] is None
                    or now - probe_cache["at"] >= probe_ttl_seconds):
                probe_cache["probe"] = _live_probe(home)
                probe_cache["at"] = now
            return probe_cache["probe"]

    @router.get("/api/recommend")
    def recommend(dataset_id: str):
        """数据集 → 路线优劣对比:{dataset, routes}。未知 dataset_id → 404。"""
        entry = next((d for d in scan(force=False) if d["id"] == dataset_id), None)
        if entry is None:
            # 缓存窗口内刚落盘的数据:强制重扫一次再判 404(同详情端点口径)
            entry = next((d for d in scan(force=True) if d["id"] == dataset_id), None)
        if entry is None:
            raise HTTPException(404, f"数据集 {dataset_id} 不存在(可先 rescan=1 重扫)")
        routes = recommend_routes(entry, current_probe())
        return {"dataset": entry, "routes": [r.to_dict() for r in routes]}

    return router
