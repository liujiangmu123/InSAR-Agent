"""数据下载凭证面(/api/credentials*):Earthdata 凭证「填 → 保存 → 验证」闭环。

安全纪律(与 api/llm_router 同级):
  - 凭证只写 workspace/credentials.json(gitignore 排除),响应永远只回掩码
    (密码/token 全掩;用户名非密钥,明文回显便于核对账号);
  - /verify 由服务端持凭出网,凭证不下发浏览器;
  - 保存/启动时热更新进程环境变量(EARTHDATA_*):runtime/probe.py 的凭据判定
    与 planner 可行性收窄认 EARTHDATA_TOKEN,不热更新则「已配置」不会点亮
    环境面板凭据区。启动走 setdefault(显式环境变量优先,setup_router 同款
    语义),POST 保存是用户刚做的选择,直接覆盖。

验证端点(测试里全部打桩,零真实网络):
  - token 方式:GET CMR search(带 Bearer)—— asf_search ASFSession.auth_with_token
    的同款判据,CMR 对无效 token 回 401,单页 page_size=1 消耗极小;
  - 账号密码方式:GET URS /api/users/tokens(HTTP Basic)—— EDL 官方轻量只读
    API,凭证错误回 401,不产生任何副作用。
"""

from __future__ import annotations

import base64
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from insar_agent.runtime.credentials import (
    configured_mode,
    load_credentials,
    mask_secret,
    materialize_env,
    save_credentials,
)

#: token 校验端点(CMR 对无效 Bearer token 回 401;page_size=1 最小消耗)
VERIFY_TOKEN_URL = ("https://cmr.earthdata.nasa.gov/search/collections"
                    "?provider=ASF&page_size=1")
#: 账号密码校验端点(EDL 官方「列出我的 token」只读 API,Basic 认证)
VERIFY_LOGIN_URL = "https://urs.earthdata.nasa.gov/api/users/tokens"

_TIMEOUT = 15.0


class CredentialsBody(BaseModel):
    earthdata_username: str | None = None
    earthdata_password: str | None = None
    edl_token: str | None = None


class VerifyBody(BaseModel):
    mode: str | None = None  # token | password;缺省按已存配置(token 优先)


def _view(home: Path) -> dict:
    cfg = load_credentials(home)
    mode = configured_mode(cfg)
    return {
        "configured": mode != "none",
        "mode": mode,
        "earthdata_username": cfg.get("earthdata_username", ""),
        "earthdata_password_masked": mask_secret(cfg.get("earthdata_password", "")),
        "edl_token_masked": mask_secret(cfg.get("edl_token", "")),
    }


def _hot_env_update(home: Path, *, overwrite: bool) -> None:
    """凭证 → 进程环境变量(probe/可行性判定的口径;子作业也随 os.environ 继承)。"""
    for key, value in materialize_env(home).items():
        if overwrite or not os.environ.get(key):
            os.environ[key] = value


def _http_status(url: str, headers: dict[str, str]) -> int:
    """GET 一次只取状态码;4xx/5xx 不抛(HTTPError 本身携带状态码)。"""
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return int(resp.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)


def create_credentials_router(home: Path) -> APIRouter:
    router = APIRouter(prefix="/api/credentials", tags=["credentials"])
    _hot_env_update(home, overwrite=False)  # 启动装载:显式环境变量优先

    @router.get("")
    def get_credentials() -> dict:
        return _view(home)

    @router.post("")
    def post_credentials(body: CredentialsBody) -> dict:
        save_credentials(home, body.model_dump())
        _hot_env_update(home, overwrite=True)  # 用户刚做的选择,本进程立即生效
        return _view(home)

    @router.post("/verify")
    def post_verify(body: VerifyBody) -> dict:
        cfg = load_credentials(home)
        mode = body.mode or configured_mode(cfg)
        t0 = time.monotonic()
        if mode == "token" and cfg.get("edl_token"):
            headers = {"Authorization": f"Bearer {cfg['edl_token']}"}
            url = VERIFY_TOKEN_URL
            label = "EDL token"
        elif mode == "password" and cfg.get("earthdata_username") \
                and cfg.get("earthdata_password"):
            basic = base64.b64encode(
                f"{cfg['earthdata_username']}:{cfg['earthdata_password']}"
                .encode("utf-8")).decode("ascii")
            headers = {"Authorization": f"Basic {basic}"}
            url = VERIFY_LOGIN_URL
            label = f"账号 {cfg['earthdata_username']}"
        else:
            return {"ok": False, "mode": mode,
                    "error": "未配置凭证:先填 EDL token 或 Earthdata 账号密码并保存"}
        try:
            status = _http_status(url, headers)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return {"ok": False, "mode": mode,
                    "error": f"网络不可达,无法完成验证:{exc}"}
        latency_ms = int((time.monotonic() - t0) * 1000)
        if 200 <= status < 300:
            return {"ok": True, "mode": mode, "status": status,
                    "latency_ms": latency_ms, "detail": f"{label} 验证通过"}
        if status in (401, 403):
            return {"ok": False, "mode": mode, "status": status,
                    "error": f"凭证无效(HTTP {status}):账号/密码错误,"
                             "或 token 已过期/被吊销 —— 到 urs.earthdata.nasa.gov 复核"}
        return {"ok": False, "mode": mode, "status": status,
                "error": f"验证端点返回 HTTP {status}(服务端异常或被限流,稍后重试)"}

    return router
