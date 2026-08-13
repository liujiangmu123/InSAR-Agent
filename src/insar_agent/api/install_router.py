"""环境安装助手后端(/api/install):缺失引擎 → 可执行安装方案;确认后重探。

两个端点(知识源:runtime/install_guide.py,探测源:runtime/probe + wsl_probe):
  - GET  /api/install/guide      组合本地探测 + WSL 引擎探测缓存,返回缺失引擎的
                                 安装方案有序清单(途径/逐步命令/耗时/磁盘/坑位/前置);
  - POST /api/install/mark-done  用户装完点确认 {engine}:穿透 WSL 探测缓存强制重探
                                 (probe_wsl_engines_cached force=True)+ 本地全新探测,
                                 返回该引擎的最新在位状态与刷新后的方案清单。

安全边界(与 setup_router 的 engine-env 同纪律):本路由只产出命令文本,
绝不提供「服务端代跑安装命令」的端点 —— 安装是用户主权操作(重型计算管控),
conda install / wsl --install 之类必须由用户在自己的终端里执行。
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from insar_agent.runtime.install_guide import (
    ENGINE_ORDER,
    INSTALL_GUIDE,
    plan_installs,
    resolve_engines,
)
from insar_agent.runtime.probe import ProbeResult, probe_environment

log = logging.getLogger(__name__)


class MarkDoneBody(BaseModel):
    engine: str  # 七引擎之一(ENGINE_ORDER 闭集,未知引擎 400)


def create_install_router() -> APIRouter:
    router = APIRouter(prefix="/api/install", tags=["install"])

    def _probe_with_wsl(force: bool = False) -> ProbeResult:
        """本地探测 + WSL 引擎探测并入(与 /api/env、/api/setup/status 同款)。

        force=True 穿透 wsl_probe 的 TTL 缓存:用户刚在 WSL 里装完引擎,
        5 分钟内的旧缓存会让复检结果纹丝不动,看起来像「装了没用」。
        """
        probe = probe_environment(Path("."), with_versions=False, check_wsl=True)
        try:
            from insar_agent.runtime.wsl_probe import merge_wsl_probe, probe_wsl_engines_cached

            wsl_result = probe_wsl_engines_cached(timeout=30.0, force=force)
            if wsl_result.get("ok"):
                merge_wsl_probe(probe, wsl_result)
        except Exception:  # noqa: BLE001 —— 可选探测绝不拖垮安装指引
            log.debug("WSL 引擎探测合并失败,按未探测处置", exc_info=True)
        return probe

    def _payload(probe: ProbeResult) -> dict:
        plans = plan_installs(probe, probe.wsl)
        return {
            "missing": [p["engine"] for p in plans],
            "plans": plans,
            "engines": resolve_engines(probe),
            "wsl": {
                "installed": bool(probe.wsl.get("installed")),
                "distros": probe.wsl.get("distros") or [],
                "engine_probe": probe.wsl.get("engine_probe"),
            },
        }

    @router.get("/guide")
    def guide() -> dict:
        """缺失引擎的安装方案清单(纯查询;方案数据见 runtime/install_guide.py)。"""
        return _payload(_probe_with_wsl())

    @router.post("/mark-done")
    def mark_done(body: MarkDoneBody) -> dict:
        """用户装完点确认:探测缓存失效重探,返回该引擎最新状态 + 刷新后的方案。"""
        if body.engine not in INSTALL_GUIDE:
            raise HTTPException(
                400, f"未知引擎 {body.engine}(可用:{'/'.join(ENGINE_ORDER)})")
        probe = _probe_with_wsl(force=True)
        out = _payload(probe)
        version = out["engines"].get(body.engine)
        return {"engine": body.engine, "present": version is not None,
                "version": version, **out}

    return router
