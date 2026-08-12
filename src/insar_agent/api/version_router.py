"""版本信息与更新检查端点(独立 APIRouter,由 api/app.py include_router 挂载)。

- GET /api/version        运行时版本指纹:包版本 / git HEAD / Python / 平台 / 是否冻结打包。
- GET /api/version/check  读环境变量 INSAR_UPDATE_MANIFEST 指向的 JSON 清单
                          ({"latest": "x.y.z", "notes": "...", "url": "..."}),
                          与本地版本做语义化比较,判断是否有新版本。

纪律:
- git 子进程必须 CREATE_NO_WINDOW(Windows 下不闪控制台窗)且 5 秒超时,失败一律降级 null;
- 语义化版本比较(compare_versions)为纯标准库实现,不引第三方依赖;
- 拉取清单优先 httpx(项目 dev 依赖),未安装时回落 urllib,同样零新增依赖;
- /api/version/check 任何失败都降级为 {update_available: false, reason: ...},绝不 500。

与桌面壳的关系见 desktop/updater/UPDATER.md:壳(Tauri)的下载安装走 tauri-plugin-updater,
本端点只负责「是否有新版」的轻量提示与运行时诊断。
"""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

from fastapi import APIRouter

from insar_agent import __version__

router = APIRouter(prefix="/api/version", tags=["version"])

#: 更新清单地址(环境变量);未配置时 /api/version/check 返回「未配置更新源」。
MANIFEST_ENV = "INSAR_UPDATE_MANIFEST"

#: Windows 专有:子进程不创建控制台窗口;其余平台无此属性,取 0 等价于不设置。
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_GIT_TIMEOUT_S = 5
_HTTP_TIMEOUT_S = 5.0

_NUMERIC_IDENT = re.compile(r"^\d+$")


# ---------------- 语义化版本比较(纯标准库) ----------------


def _parse_version(version: str) -> tuple[tuple[int, ...], tuple[int | str, ...] | None]:
    """解析版本号 → (主版本段, 预发布段或 None)。

    容忍 v/V 前缀;构建元数据(+build)按 SemVer 规则忽略;无法解析时抛 ValueError。
    """
    text = version.strip()
    if text[:1] in ("v", "V"):
        text = text[1:]
    core_and_pre = text.partition("+")[0]  # 丢弃构建元数据
    core, dash, prerelease = core_and_pre.partition("-")
    try:
        release = tuple(int(part) for part in core.split("."))
    except ValueError:
        raise ValueError(f"无法解析版本号:{version!r}") from None
    if not dash:
        return release, None
    idents: list[int | str] = []
    for ident in prerelease.split("."):
        if not ident:
            raise ValueError(f"预发布段含空标识符:{version!r}")
        idents.append(int(ident) if _NUMERIC_IDENT.match(ident) else ident)
    return release, tuple(idents)


def compare_versions(left: str, right: str) -> int:
    """语义化版本比较:left > right 返回 1,相等返回 0,否则返回 -1。

    规则(SemVer 2.0.0 §11):
    - 主版本段逐段按数值比较(1.0.10 > 1.0.9),段数不足按 0 补齐(1.2 == 1.2.0);
    - 带预发布段的版本低于对应正式版(1.0.0-rc.1 < 1.0.0);
    - 预发布段逐标识符比较:纯数字按数值、字母数字按 ASCII、纯数字低于字母数字,
      公共前缀相同时标识符多者为大(1.0.0-alpha < 1.0.0-alpha.1)。
    """
    left_release, left_pre = _parse_version(left)
    right_release, right_pre = _parse_version(right)

    width = max(len(left_release), len(right_release))
    left_padded = left_release + (0,) * (width - len(left_release))
    right_padded = right_release + (0,) * (width - len(right_release))
    if left_padded != right_padded:
        return 1 if left_padded > right_padded else -1

    if left_pre is None and right_pre is None:
        return 0
    if left_pre is None:
        return 1  # 正式版 > 预发布版
    if right_pre is None:
        return -1

    for left_ident, right_ident in zip(left_pre, right_pre):
        if left_ident == right_ident:
            continue
        left_num = isinstance(left_ident, int)
        right_num = isinstance(right_ident, int)
        if left_num and right_num:
            return 1 if left_ident > right_ident else -1
        if left_num != right_num:
            return -1 if left_num else 1  # 纯数字标识符优先级低于字母数字
        return 1 if str(left_ident) > str(right_ident) else -1
    if len(left_pre) != len(right_pre):
        return 1 if len(left_pre) > len(right_pre) else -1
    return 0


# ---------------- 数据采集 ----------------


def _git_head() -> str | None:
    """源码所在 git 仓库的 HEAD 短哈希;非 git 环境 / git 缺失 / 超时一律返回 None。"""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            creationflags=_CREATE_NO_WINDOW,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    head = result.stdout.strip()
    return head if result.returncode == 0 and head else None


def _fetch_manifest(url: str, timeout: float = _HTTP_TIMEOUT_S) -> dict[str, Any]:
    """拉取更新清单 JSON。优先 httpx,未安装时回落 urllib;失败抛异常由调用方降级。"""
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError(f"更新源必须是 http(s) 地址:{url!r}")
    try:
        import httpx
    except ImportError:
        httpx = None

    if httpx is not None:
        response = httpx.get(url, timeout=timeout, follow_redirects=True)
        response.raise_for_status()
        data = response.json()
    else:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 - 已限定 http(s)
            data = json.loads(resp.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("更新清单必须是 JSON 对象")
    return data


# ---------------- 端点 ----------------


@router.get("")
def get_version() -> dict[str, Any]:
    """运行时版本指纹(桌面壳「关于」页与诊断用),不联网。"""
    return {
        "version": __version__,
        "git_head": _git_head(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "build": {"frozen": bool(getattr(sys, "frozen", False))},
    }


@router.get("/check")
def check_update() -> dict[str, Any]:
    """对照 INSAR_UPDATE_MANIFEST 清单检查新版本;只提示,不下载不安装。"""
    manifest_url = os.environ.get(MANIFEST_ENV, "").strip()
    if not manifest_url:
        return {"update_available": False, "reason": "未配置更新源"}
    try:
        manifest = _fetch_manifest(manifest_url)
    except Exception as exc:  # noqa: BLE001 - 网络/解析错误统一降级,绝不 500
        return {"update_available": False, "reason": f"获取更新清单失败:{exc}"}
    latest = str(manifest.get("latest") or "").strip()
    if not latest:
        return {"update_available": False, "reason": "更新清单缺少 latest 字段"}
    try:
        newer = compare_versions(latest, __version__) > 0
    except ValueError as exc:
        return {"update_available": False, "reason": f"版本号无法比较:{exc}"}
    return {
        "update_available": newer,
        "current": __version__,
        "latest": latest,
        "notes": manifest.get("notes"),
        "url": manifest.get("url"),
    }
