"""LLM 配置面(/api/llm/*):界面「填密钥 → 获取模型 → 选模型 → 测试」闭环。

安全纪律:
  - api_key 只写 workspace/llm.json(gitignore 排除),响应永远只回掩码;
  - /models 与 /test 都在服务端持钥出网,密钥不下发浏览器;
  - 测试调用消耗极小(chat 单次 JSON、vision 内置 8×8 纯色图 + max_tokens=64)。
"""

from __future__ import annotations

import base64
import struct
import time
import zlib
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from insar_agent.brain.llm_config import (
    DEFAULT_BASE_URL,
    agent_loop_settings,
    load_llm_config,
    mask_key,
    routes_from_config,
    save_llm_config,
    vision_route_from_config,
)
from insar_agent.brain.provider import (
    BrainUnavailable,
    LLMProvider,
    LLMRoute,
    describe_image_stream,
    list_models,
)
from insar_agent.brain.usage import UsageLedger


class LLMConfigBody(BaseModel):
    base_url: str | None = None
    api_key: str | None = None
    chat_model: str | None = None
    vision_model: str | None = None
    # 自主循环(LOOP-CONTRACT §8)。None = 不改;越界由 pydantic 挡成 422,
    # 与本路由 /usage 的 Query(ge/le) 及 POST /config 既有的 pydantic 类型校验同风格。
    agent_loop: bool | None = None
    agent_max_cycles: int | None = Field(default=None, ge=1, le=12)


class ModelsBody(BaseModel):
    base_url: str | None = None
    api_key: str | None = None


class TestBody(BaseModel):
    kind: str = "chat"  # chat | vision
    model: str | None = None  # 未保存前试选:临时指定模型


def _tiny_png_data_url() -> str:
    """内置识图测试图:8×8 纯红 PNG(零依赖构造,数据 URL 形态)。"""
    w = h = 8
    raw = b"".join(b"\x00" + bytes([220, 30, 30]) * w for _ in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
           + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    return "data:image/png;base64," + base64.b64encode(png).decode()


def _config_view(home: Path) -> dict:
    cfg = load_llm_config(home)
    file_ready = bool(cfg.get("base_url") and cfg.get("api_key")
                      and cfg.get("chat_model"))
    env_routes = [r for r in routes_from_config(home)
                  if not file_ready or r.model != cfg.get("chat_model")]
    source = "file" if file_ready else ("env" if env_routes else "none")
    loop = agent_loop_settings(home)  # 缺失/损坏回默认,回显永远是合法值
    return {
        "configured": bool(routes_from_config(home)),
        "source": source,
        "base_url": cfg.get("base_url") or DEFAULT_BASE_URL,
        "chat_model": cfg.get("chat_model", ""),
        "vision_model": cfg.get("vision_model", ""),
        "api_key_masked": mask_key(cfg.get("api_key", "")),
        "agent_loop": loop["enabled"],
        "agent_max_cycles": loop["max_cycles"],
    }


#: /usage 未接账本(独立挂载形态)时的空响应,形状与 UsageLedger.summary 一致
_EMPTY_TOTAL = {"calls": 0, "prompt_tokens": None,
                "completion_tokens": None, "cost_est_cny": None}

#: 出网地址 scheme 白名单:base_url 会被 /models、/test 持钥出网,provider 走 urllib
#: (实测 file:// 可读本地文件),不校验即 SSRF/LFI 面。仅放行 http/https(AUDIT-api-r3)。
_ALLOWED_URL_SCHEMES = ("http", "https")


def _require_safe_base_url(base_url: str | None) -> None:
    """出网地址边界校验:非 http/https 一律 400(空值=不改/用缺省,放行)。"""
    s = (base_url or "").strip()
    if s and urlsplit(s).scheme.lower() not in _ALLOWED_URL_SCHEMES:
        raise HTTPException(400, "base_url 仅支持 http/https(拒绝 file:// 等非 HTTP 协议)")


def create_llm_router(home: Path, usage_ledger: UsageLedger | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/llm", tags=["llm"])

    @router.get("/config")
    def get_config() -> dict:
        return _config_view(home)

    @router.get("/usage")
    def get_usage(days: int = Query(7, ge=1, le=90)) -> dict:
        """LLM 用量账本汇总(顶栏用量芯片数据源):
        总量 / 按模型 / 按日 / 按会话 / 最近 20 条流水;成本估算 CNY,未知为 null。"""
        if usage_ledger is None:
            return {"days": days, "total": dict(_EMPTY_TOTAL), "by_model": [],
                    "by_day": [], "by_session": [], "recent": []}
        return usage_ledger.summary(days=days)

    @router.post("/config")
    def post_config(body: LLMConfigBody) -> dict:
        _require_safe_base_url(body.base_url)  # 落盘前挡下 file:// 等非 HTTP 地址
        save_llm_config(home, body.model_dump())
        return _config_view(home)

    @router.post("/models")
    def post_models(body: ModelsBody) -> dict:
        """获取模型列表:优先用请求体里的(未保存先试),缺省用已存配置。"""
        _require_safe_base_url(body.base_url)  # 直传 base_url 是 SSRF 主入口:出网前先校验
        cfg = load_llm_config(home)
        base = (body.base_url or "").strip() or cfg.get("base_url") or DEFAULT_BASE_URL
        key = (body.api_key or "").strip() or cfg.get("api_key", "")
        if not key:
            return {"ok": False, "error": "未提供密钥:先在密钥框粘贴,再获取模型"}
        try:
            models = list_models(base, key)
        except BrainUnavailable as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "models": [{
            "id": m["id"],
            "vision": bool(m.get("supports_vision")),
            "tools": bool(m.get("supports_tools")),
            "reasoning": bool(m.get("supports_reasoning")),
            "context_length": m.get("context_length"),
            "price_in": m.get("effective_input_price_per_million"),
            "price_out": m.get("effective_output_price_per_million"),
            "currency": m.get("currency", ""),
        } for m in models]}

    @router.post("/test")
    def post_test(body: TestBody) -> dict:
        """连通性测试。chat:单次 JSON 补全;vision:内置小图流式识别
        (识图必须流式 —— 接入方约束,provider.describe_image_stream 强制)。"""
        cfg = load_llm_config(home)
        t0 = time.monotonic()
        if body.kind == "vision":
            route = vision_route_from_config(home)
            if body.model and cfg.get("base_url") and cfg.get("api_key"):
                route = LLMRoute(cfg["base_url"].rstrip("/"), cfg["api_key"],
                                 body.model)
            if route is None:
                return {"ok": False, "error": "未配置识图模型:先保存 vision_model"}
            try:
                reply = describe_image_stream(
                    route, prompt="这张图片主要是什么颜色?只答颜色名。",
                    image_data_url=_tiny_png_data_url(), max_tokens=64)
            except BrainUnavailable as exc:
                return {"ok": False, "model": route.model, "error": str(exc)}
            return {"ok": True, "model": route.model, "kind": "vision",
                    "latency_ms": int((time.monotonic() - t0) * 1000),
                    "reply": reply[:120]}
        routes = routes_from_config(home)
        if body.model and cfg.get("base_url") and cfg.get("api_key"):
            routes = [LLMRoute(cfg["base_url"].rstrip("/"), cfg["api_key"],
                               body.model)]
        if not routes:
            return {"ok": False, "error": "未配置对话模型:先保存 base_url/密钥/chat_model"}
        provider = LLMProvider(routes)
        try:
            # max_tokens 给足:推理型模型(deepseek/glm 等)先产思维链再产正文,
            # 32 会在正文前就撞上限触发 BrainTruncated(2026-08-13 实测)
            data = provider.complete_json(
                system="你是连通性探针。只输出 JSON。",
                user='原样返回 {"ok": true}', max_tokens=2048)
        except BrainUnavailable as exc:
            return {"ok": False, "model": routes[0].model, "error": str(exc)}
        return {"ok": bool(data.get("ok") is True), "model": routes[0].model,
                "kind": "chat",
                "latency_ms": int((time.monotonic() - t0) * 1000),
                "reply": str(data)[:120]}

    return router
