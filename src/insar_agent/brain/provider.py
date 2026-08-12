"""LLM provider:OpenAI 兼容 chat 接口 + 单跳 fallback(AGENT-DESIGN §3.5)。

约束:
  - 决策请求不带历史(§3.3 约束四):每次调用都是独立请求。
  - finish_reason == "length" → 整体拒绝(absorb-E9,pi agent-loop.ts:207-214:
    截断响应里「能解析的」结构化输出也可能语义不完整,一个都不能用)。
  - 结构保证靠 response_format + 代码层值域校验(§3.4:约束保证结构不保证语义)。

配置(环境变量;未配置 = brain 禁用,系统退化为手动流水线):
  INSAR_LLM_BASE_URL / INSAR_LLM_API_KEY / INSAR_LLM_MODEL
  INSAR_LLM_FALLBACK_BASE_URL / _API_KEY / _MODEL(可选,单跳)
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass


class BrainUnavailable(RuntimeError):
    """LLM 不可用/失败 —— 调用方必须走降级路径。"""


class BrainTruncated(BrainUnavailable):
    """输出被 token 上限截断 —— 整体拒绝(absorb-E9)。"""


@dataclass(frozen=True)
class LLMRoute:
    base_url: str
    api_key: str
    model: str


def routes_from_env() -> list[LLMRoute]:
    out: list[LLMRoute] = []
    if os.environ.get("INSAR_LLM_BASE_URL") and os.environ.get("INSAR_LLM_MODEL"):
        out.append(LLMRoute(os.environ["INSAR_LLM_BASE_URL"].rstrip("/"),
                            os.environ.get("INSAR_LLM_API_KEY", ""),
                            os.environ["INSAR_LLM_MODEL"]))
    if os.environ.get("INSAR_LLM_FALLBACK_BASE_URL") and os.environ.get("INSAR_LLM_FALLBACK_MODEL"):
        out.append(LLMRoute(os.environ["INSAR_LLM_FALLBACK_BASE_URL"].rstrip("/"),
                            os.environ.get("INSAR_LLM_FALLBACK_API_KEY", ""),
                            os.environ["INSAR_LLM_FALLBACK_MODEL"]))
    return out


class LLMProvider:
    def __init__(self, routes: list[LLMRoute] | None = None, timeout: float = 60.0):
        self.routes = routes if routes is not None else routes_from_env()
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self.routes)

    def complete_json(self, *, system: str, user: str, max_tokens: int = 512) -> dict:
        """返回解析后的 JSON 对象。失败/截断 → BrainUnavailable/BrainTruncated。
        单跳 fallback:主路由失败换备路由一次,备也失败即放弃(不无限重试)。"""
        if not self.routes:
            raise BrainUnavailable("未配置 LLM 路由")
        last_error: Exception | None = None
        for route in self.routes[:2]:
            try:
                return self._call(route, system, user, max_tokens)
            except BrainTruncated:
                raise  # 截断不换路由:换供应商也解决不了 prompt 过长
            except Exception as exc:  # noqa: BLE001 —— 网络/解析失败换路由
                last_error = exc
        raise BrainUnavailable(f"全部路由失败:{last_error}")

    def _call(self, route: LLMRoute, system: str, user: str, max_tokens: int) -> dict:
        payload = {
            "model": route.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "max_tokens": max_tokens,
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        req = urllib.request.Request(
            route.base_url + "/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {route.api_key}"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        choice = (body.get("choices") or [{}])[0]
        if choice.get("finish_reason") == "length":
            raise BrainTruncated("输出被 token 上限截断,整体拒绝")
        content = (choice.get("message") or {}).get("content") or ""
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise BrainUnavailable(f"响应不是合法 JSON:{content[:200]}") from exc
        if not isinstance(parsed, dict):
            # 路由无视 response_format 返回数组/标量时,原样透传会让 facade 在
            # data.get(...) 上 AttributeError 炸穿降级路径 —— 契约是 dict,这里拒绝
            raise BrainUnavailable(f"响应 JSON 顶层不是对象:{content[:200]}")
        return parsed
