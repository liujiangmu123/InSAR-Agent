"""声明式插件清单端点(独立 APIRouter,由 api/app.py include_router 挂载)。

GET /api/plugins → {plugins: [...], roots: [...]}

纪律:
- 只读 YAML,绝不 import/exec 插件目录里的 Python(RCE 面);
- 坏文件已在 loader 层警告跳过,端点绝不因单个坏清单 500;
- 响应不携带 home 绝对路径(内置 roots 报 catalog,home 只报 plugins/<dir>)。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from insar_agent.plugins.loader import load_plugins, plugin_roots


def create_plugin_router(home: Path | str) -> APIRouter:
    """home 由 create_app 注入,用于扫描 <home>/plugins/*/plugin.yaml。"""
    router = APIRouter(prefix="/api/plugins", tags=["plugins"])
    home = Path(home)

    @router.get("")
    def list_plugins() -> dict:
        plugins = load_plugins(home)
        return {
            "plugins": [p.as_public() for p in plugins],
            "roots": plugin_roots(plugins),
        }

    return router
