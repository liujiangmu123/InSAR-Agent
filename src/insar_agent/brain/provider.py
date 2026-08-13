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
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable


class BrainUnavailable(RuntimeError):
    """LLM 不可用/失败 —— 调用方必须走降级路径。"""


class BrainTruncated(BrainUnavailable):
    """输出被 token 上限截断 —— 整体拒绝(absorb-E9)。"""


@dataclass(frozen=True)
class LLMRoute:
    base_url: str
    api_key: str
    model: str


# ---------------- 用量计量(回调注入,provider 保持纯传输层) ----------------

#: 模块级用量回调:app 启动时经 set_usage_sink 注入(brain/usage.UsageLedger.record)。
#: 默认 None = 不计量、零开销;本模块绝不 import 存储层,依赖方向只进不出。
_usage_sink: Callable[[dict], None] | None = None


def set_usage_sink(fn: Callable[[dict], None] | None) -> None:
    """注册用量回调。record 形如 {model, kind, prompt_tokens, completion_tokens,
    latency_ms};token 计数拿不到时为 None(绝不编数),会话上下文由账本侧补。"""
    global _usage_sink
    _usage_sink = fn


def _tokens_or_none(value) -> int | None:
    """usage 字段值 → 非负整数才可信,其余(缺失/负数/字符串/bool)记 None。"""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _report_usage(model: str, kind: str, usage, latency_ms: int) -> None:
    """上报一次调用的用量。计量是旁路:回调抛错一律吞掉,绝不拖垮主链路。"""
    if _usage_sink is None:
        return
    u = usage if isinstance(usage, dict) else {}
    try:
        _usage_sink({"model": model, "kind": kind,
                     "prompt_tokens": _tokens_or_none(u.get("prompt_tokens")),
                     "completion_tokens": _tokens_or_none(u.get("completion_tokens")),
                     "latency_ms": latency_ms})
    except Exception:  # noqa: BLE001 —— 记账失败不能影响 LLM 调用本身
        pass


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
        t0 = time.monotonic()
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        # 拿到响应体即记账:token 已在中转站计费,截断/解析失败同样要入账
        _report_usage(route.model, "chat",
                      body.get("usage") if isinstance(body, dict) else None,
                      int((time.monotonic() - t0) * 1000))
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


def list_models(base_url: str, api_key: str, timeout: float = 30.0) -> list[dict]:
    """GET {base}/models,返回 data 数组原样(调用方裁剪字段)。

    OpenAI 兼容中转站普遍在模型对象上带能力元数据(supports_vision 等,
    tokenrhythm 实测有);解析失败/形状不对 → BrainUnavailable。
    """
    req = urllib.request.Request(
        base_url.rstrip("/") + "/models",
        headers={"Authorization": f"Bearer {api_key}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise BrainUnavailable(f"模型列表请求被拒(HTTP {exc.code}):检查地址与密钥") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise BrainUnavailable(f"模型列表获取失败:{exc}") from exc
    data = body.get("data")
    if not isinstance(data, list):
        raise BrainUnavailable("模型列表响应缺 data 数组(非 OpenAI 兼容形态)")
    return [m for m in data if isinstance(m, dict) and m.get("id")]


def describe_image_stream(route: LLMRoute, *, prompt: str, image_data_url: str,
                          max_tokens: int = 512, timeout: float = 120.0) -> str:
    """识图调用:OpenAI 兼容 image_url 形态,**强制流式**(接入方约束:
    识图模型必须 stream=true;2026-08-13 tokenrhythm kimi-k2.5/2.6 实测通过)。

    聚合 SSE 增量(choices[0].delta.content)返回全文;HTTP 错误/断流/零内容
    → BrainUnavailable,由调用方走降级路径。
    """
    payload = {
        "model": route.model,
        "stream": True,
        # 流式用量(OpenAI 兼容):尾帧(choices 为空)带 usage。2026-08-13
        # tokenrhythm 实测:认该字段,内容帧 usage 为 null,[DONE] 前一帧带完整
        # usage;不认的中转站会忽略之,彼时尾帧无 usage → 计量记 null。
        "stream_options": {"include_usage": True},
        "max_tokens": max_tokens,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": image_data_url}},
            ],
        }],
    }
    req = urllib.request.Request(
        route.base_url + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {route.api_key}"})
    parts: list[str] = []
    finish: str | None = None
    usage: dict | None = None
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            try:
                for raw_line in resp:
                    line = raw_line.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        continue  # 心跳/坏帧跳过,以 [DONE] 或断流为终止
                    if not isinstance(obj, dict):
                        continue
                    if isinstance(obj.get("usage"), dict):
                        usage = obj["usage"]  # 内容帧多为 null,取最后一次非空
                    choice = (obj.get("choices") or [{}])[0]
                    delta = choice.get("delta") or {}
                    if delta.get("content"):
                        parts.append(delta["content"])
                    finish = choice.get("finish_reason") or finish
            finally:
                # 流一旦建立即记账(断流前的 token 也已计费);HTTP 层被拒
                # (urlopen 抛错)不进此块 —— 那种调用没有计费事实
                _report_usage(route.model, "vision", usage,
                              int((time.monotonic() - t0) * 1000))
    except urllib.error.HTTPError as exc:
        raise BrainUnavailable(f"识图请求被拒(HTTP {exc.code}):"
                               f"检查模型是否支持图片输入") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise BrainUnavailable(f"识图流中断:{exc}") from exc
    if finish == "length":
        raise BrainTruncated("识图输出被 token 上限截断,整体拒绝")
    text = "".join(parts).strip()
    if not text:
        raise BrainUnavailable("识图响应为空(模型无内容输出)")
    return text
