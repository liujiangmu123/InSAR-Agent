"""LLM provider:OpenAI 兼容 chat 接口 + 单跳 fallback(AGENT-DESIGN §3.5)。

约束:
  - 决策请求不带历史(§3.3 约束四):complete_json 每次调用都是独立请求;
    chat 原语(docs/LOOP-CONTRACT.md §2)按 OpenAI messages 透传多轮上下文,
    历史纪律(只带周期摘要、不带全量历史)由调用方把握。
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
from typing import Callable, Iterable, Iterator


class BrainUnavailable(RuntimeError):
    """LLM 调用失败。消息就是给用户看的错误原文,调用方不要再包一层。"""


def describe_call_error(exc: BaseException | None) -> str:
    """把供应商调用失败收成一条可直接展示的错误(HTTP 状态 + 响应体)。"""
    if exc is None:
        return "LLM 调用失败(无错误详情)"
    if isinstance(exc, urllib.error.HTTPError):
        detail = ""
        try:
            raw = exc.read()
            if raw:
                detail = raw.decode("utf-8", "replace").strip()[:500]
        except Exception:  # noqa: BLE001 —— 读响应体失败仍报状态码
            detail = ""
        reason = (getattr(exc, "reason", None) or "").strip()
        head = f"HTTP {exc.code}" + (f" {reason}" if reason else "")
        return f"{head}: {detail}" if detail else head
    text = str(exc).strip()
    return text or type(exc).__name__


class BrainTruncated(BrainUnavailable):
    """输出被 token 上限截断 —— 整体拒绝(absorb-E9)。"""


@dataclass(frozen=True)
class LLMRoute:
    base_url: str
    api_key: str
    model: str


@dataclass
class ChatOutcome:
    """chat 原语的单次结果(docs/LOOP-CONTRACT.md §2)。

    content 与 tool_calls 皆可能为空(模型只回文本或只回工具调用);
    route_index 是实际应答的路由下标,供循环调用方在后续周期钉死(route_pin)。
    """

    content: str            # assistant 文本(无 / null 归一为空串)
    tool_calls: list[dict]  # OpenAI 形状 [{"id","type","function":{...}}];无则 []
    finish_reason: str      # 供应商原样 finish_reason(缺失归一为空串)
    route_index: int        # 实际使用的路由下标


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
        # 「流式不支持」的路由记忆(WAVE-0814B §1.1):建流前 4xx 的路由下标记入,
        # 后续 chat_stream 直接走非流式 _chat_call,不反复探测;实例级,进程内有效
        self._stream_unsupported: set[int] = set()

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
        raise BrainUnavailable(describe_call_error(last_error))

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

    def chat(self, messages: list[dict], *, tools: list[dict] | None = None,
             max_tokens: int = 2048, json_only: bool = True,
             route_pin: int | None = None) -> ChatOutcome:
        """多轮 chat 原语(docs/LOOP-CONTRACT.md §2),服务自主循环的周期决策。

        - messages 按 OpenAI 格式原样透传(role: system/user/assistant/tool),
          content 不做 JSON 解析 —— 语义校验归调用方;
        - json_only=True → payload 带 response_format=json_object + temperature=0(§3.4);
        - tools 给定时原样透传,应答中的 tool_calls 原样带回(无则 []);
        - finish_reason == "length" → BrainTruncated 整体拒绝且绝不换路由
          (absorb-E9:含 tool_calls 分片的截断同样一个都不能用);
        - route_pin=None:沿用单跳 fallback(主失败换备一次,至多两路);
          route_pin=i:钉死路由 i,失败不切换,归一化为 BrainUnavailable 直接抛。
        """
        if not self.routes:
            raise BrainUnavailable("未配置 LLM 路由")
        if route_pin is None:
            candidates = list(enumerate(self.routes[:2]))
        elif 0 <= route_pin < len(self.routes):
            candidates = [(route_pin, self.routes[route_pin])]
        else:
            raise BrainUnavailable(
                f"route_pin 越界:{route_pin}(共 {len(self.routes)} 条路由)")
        last_error: Exception | None = None
        for index, route in candidates:
            try:
                return self._chat_call(route, index, messages, tools, max_tokens, json_only)
            except BrainTruncated:
                raise  # 截断不换路由:换供应商也解决不了 prompt 过长
            except Exception as exc:  # noqa: BLE001 —— 网络/HTTP/超时按路由失败归一化
                last_error = exc
        pin_note = "" if route_pin is None else f" (钉死路由 {route_pin})"
        raise BrainUnavailable(describe_call_error(last_error) + pin_note)

    def _chat_call(self, route: LLMRoute, route_index: int, messages: list[dict],
                   tools: list[dict] | None, max_tokens: int, json_only: bool) -> ChatOutcome:
        payload: dict = {"model": route.model, "messages": messages, "max_tokens": max_tokens}
        if json_only:
            # 结构约束与 complete_json 同款:JSON 模式 + 温度 0(§3.4)
            payload["temperature"] = 0
            payload["response_format"] = {"type": "json_object"}
        if tools is not None:
            payload["tools"] = tools  # OpenAI 工具描述原样透传,不改写不校验
        req = urllib.request.Request(
            route.base_url + "/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {route.api_key}"})
        t0 = time.monotonic()
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        # 拿到响应体即记账(与 complete_json 同纪律):截断/坏形状同样已在中转站计费
        _report_usage(route.model, "agent",
                      body.get("usage") if isinstance(body, dict) else None,
                      int((time.monotonic() - t0) * 1000))
        choice = (body.get("choices") or [{}])[0]
        finish = str(choice.get("finish_reason") or "")
        if finish == "length":
            raise BrainTruncated("chat 输出被 token 上限截断,整体拒绝(含 tool_calls 分片)")
        message = choice.get("message") or {}
        raw_calls = message.get("tool_calls")
        if not isinstance(raw_calls, list):
            raw_calls = []
        return ChatOutcome(content=str(message.get("content") or ""),
                           tool_calls=[t for t in raw_calls if isinstance(t, dict)],
                           finish_reason=finish,
                           route_index=route_index)

    def chat_stream(self, messages: list[dict], *, tools: list[dict] | None = None,
                    max_tokens: int = 2048, json_only: bool = True,
                    route_pin: int | None = None,
                    on_delta: Callable[[str], None] | None = None) -> ChatOutcome:
        """流式 chat 原语(WAVE-0814B §1.1):语义对齐 chat(),content 增量经 on_delta 外发。

        - payload 带 stream=true + stream_options.include_usage,复用模块级 _iter_sse;
        - 只外发 choices[0].delta.content(reasoning_content 等思考增量不外发);
        - tool_calls 按 index 分片拼装(首帧登记 id/type/name,arguments 逐帧拼接,
          坏帧跳过);finish_reason 取最后一个非空;usage 取最后一次非空,流建立即
          记账 kind="agent",无 usage 尾帧时 token 记 None,绝不编数;
        - finish_reason=="length" → 流结束后抛 BrainTruncated,绝不换路由(absorb-E9);
        - [DONE]/断流但 finish_reason 从未出现 → BrainUnavailable(半截绝不当完整结果);
        - 零外发前缀内才允许重试/换路由:建流前 4xx → 同路由降级非流式 _chat_call
          重试一次并记入 self._stream_unsupported(实例级记忆,不反复探测);
          200 但 Content-Type 是 JSON → 就地按非流式解析;建流前网络失败 →
          route_pin=None 换备一次 / route_pin=i 直接抛;流一旦建立,之后的任何失败
          一律 BrainUnavailable 绝不重放(增量可能已外发,重放=用户看到重复文本)。
        """
        if not self.routes:
            raise BrainUnavailable("未配置 LLM 路由")
        if route_pin is None:
            candidates = list(enumerate(self.routes[:2]))
        elif 0 <= route_pin < len(self.routes):
            candidates = [(route_pin, self.routes[route_pin])]
        else:
            raise BrainUnavailable(
                f"route_pin 越界:{route_pin}(共 {len(self.routes)} 条路由)")
        last_error: Exception | None = None
        for index, route in candidates:
            if index in self._stream_unsupported:
                # 该路由已实测拒绝流式:直接非流式,不再反复探测
                try:
                    return self._chat_call(route, index, messages, tools,
                                           max_tokens, json_only)
                except BrainTruncated:
                    raise  # 截断不换路由(chat 同款)
                except Exception as exc:  # noqa: BLE001 —— 按路由失败归一化
                    last_error = exc
                    continue
            try:
                return self._chat_stream_call(route, index, messages, tools,
                                              max_tokens, json_only, on_delta)
            except BrainUnavailable:
                # 含 BrainTruncated:_chat_stream_call 只在「流已建立」后抛这一族,
                # 增量可能已外发 —— 终态失败,绝不重试绝不换路由
                raise
            except urllib.error.HTTPError as exc:
                if 400 <= exc.code < 500:
                    # 建流前 4xx(路由不支持流式/参数被拒)→ 记忆 + 同路由非流式重试一次
                    self._stream_unsupported.add(index)
                    try:
                        return self._chat_call(route, index, messages, tools,
                                               max_tokens, json_only)
                    except BrainTruncated:
                        raise
                    except Exception as retry_exc:  # noqa: BLE001
                        last_error = retry_exc
                        continue
                last_error = exc  # 5xx:建流前路由失败,单跳换备
                continue
            except Exception as exc:  # noqa: BLE001 —— 建流前网络失败换路由
                last_error = exc
                continue
        pin_note = "" if route_pin is None else f" (钉死路由 {route_pin})"
        raise BrainUnavailable(describe_call_error(last_error) + pin_note)

    def _chat_stream_call(self, route: LLMRoute, route_index: int, messages: list[dict],
                          tools: list[dict] | None, max_tokens: int, json_only: bool,
                          on_delta: Callable[[str], None] | None) -> ChatOutcome:
        """单路由的一次流式调用。异常契约(chat_stream 按此分类处置):
        建流前失败(HTTP 状态码/拒连/超时)原样上抛;流建立(200 SSE)之后的
        失败一律归一化为 BrainUnavailable/BrainTruncated(终态,绝不重试)。"""
        payload: dict = {"model": route.model, "messages": messages,
                         "max_tokens": max_tokens, "stream": True,
                         # 流式用量:尾帧(choices 为空)带 usage;不认该字段的中转站
                         # 会忽略之,彼时无尾帧 → 计量记 None(describe_image_stream 同款)
                         "stream_options": {"include_usage": True}}
        if json_only:
            # 结构约束与 _chat_call 同款:JSON 模式 + 温度 0(§3.4)
            payload["temperature"] = 0
            payload["response_format"] = {"type": "json_object"}
        if tools is not None:
            payload["tools"] = tools  # OpenAI 工具描述原样透传,不改写不校验
        req = urllib.request.Request(
            route.base_url + "/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {route.api_key}"})
        t0 = time.monotonic()
        # 建流前失败从 urlopen 原样抛出(HTTPError/URLError/超时),调用方分类处置
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            ctype = str(resp.headers.get("Content-Type") or "")
            if "json" in ctype.lower():
                # 200 但回的是 JSON 体(路由无视 stream=true)→ 就地按非流式解析,
                # 语义与 _chat_call 逐条对齐;解析失败原样上抛(零外发,允许换路由)
                body = json.loads(resp.read().decode("utf-8"))
                _report_usage(route.model, "agent",
                              body.get("usage") if isinstance(body, dict) else None,
                              int((time.monotonic() - t0) * 1000))
                choice = (body.get("choices") or [{}])[0]
                finish = str(choice.get("finish_reason") or "")
                if finish == "length":
                    raise BrainTruncated(
                        "chat 输出被 token 上限截断,整体拒绝(含 tool_calls 分片)")
                message = choice.get("message") or {}
                raw_calls = message.get("tool_calls")
                if not isinstance(raw_calls, list):
                    raw_calls = []
                return ChatOutcome(content=str(message.get("content") or ""),
                                   tool_calls=[t for t in raw_calls if isinstance(t, dict)],
                                   finish_reason=finish,
                                   route_index=route_index)
            # ---- 流已建立:自此一切失败都是终态,绝不重试/换路由 ----
            parts: list[str] = []
            calls: dict[int, dict] = {}
            finish = ""
            usage: dict | None = None
            try:
                for obj in _iter_sse(resp):
                    if isinstance(obj.get("usage"), dict):
                        usage = obj["usage"]  # 内容帧多为 null,取最后一次非空
                    choices = obj.get("choices")
                    choice = choices[0] if isinstance(choices, list) and choices else {}
                    if not isinstance(choice, dict):
                        continue  # 坏帧跳过(choices[0] 不是对象)
                    delta = choice.get("delta")
                    delta = delta if isinstance(delta, dict) else {}
                    content = delta.get("content")
                    if isinstance(content, str) and content:
                        # 只外发 content;reasoning_content 等思考增量不进结果不外发
                        parts.append(content)
                        if on_delta is not None:
                            on_delta(content)
                    _merge_tool_call_fragments(calls, delta.get("tool_calls"))
                    finish = str(choice.get("finish_reason") or "") or finish
            except BrainUnavailable:
                raise
            except Exception as exc:  # noqa: BLE001 —— 断流/回调抛错:终态归一化
                raise BrainUnavailable(f"chat 流中断:{exc}") from exc
            finally:
                # 流一旦建立即记账(断流前的 token 也已在中转站计费);
                # 无 usage 尾帧时 token 记 None,绝不编数
                _report_usage(route.model, "agent", usage,
                              int((time.monotonic() - t0) * 1000))
        if finish == "length":
            raise BrainTruncated("chat 流式输出被 token 上限截断,整体拒绝(含 tool_calls 分片)")
        if not finish:
            raise BrainUnavailable("chat 流在 finish_reason 之前断开,半截不当完整结果")
        return ChatOutcome(content="".join(parts),
                           tool_calls=[calls[i] for i in sorted(calls)],
                           finish_reason=finish,
                           route_index=route_index)


def _merge_tool_call_fragments(calls: dict[int, dict], fragments) -> None:
    """OpenAI 流式 tool_calls 分片拼装(WAVE-0814B §1.1)。

    index 是拼装主键:首帧登记 id/type/name,arguments 逐帧拼接;坏帧
    (分片非对象/缺 index/index 坏类型)整片跳过,字段类型不对的部分忽略,
    绝不让坏分片污染已拼装的条目。bool 拦截同闭集校验:True 不是 index。
    """
    if not isinstance(fragments, list):
        return
    for frag in fragments:
        if not isinstance(frag, dict):
            continue
        index = frag.get("index")
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            continue  # index 缺失/坏类型:无从归属,坏帧跳过
        entry = calls.setdefault(index, {"id": "", "type": "function",
                                         "function": {"name": "", "arguments": ""}})
        frag_id = frag.get("id")
        if isinstance(frag_id, str) and frag_id and not entry["id"]:
            entry["id"] = frag_id  # 首帧登记,后帧不改写
        frag_type = frag.get("type")
        if isinstance(frag_type, str) and frag_type:
            entry["type"] = frag_type
        fn = frag.get("function")
        if isinstance(fn, dict):
            name = fn.get("name")
            if isinstance(name, str) and name and not entry["function"]["name"]:
                entry["function"]["name"] = name
            args = fn.get("arguments")
            if isinstance(args, str) and args:
                entry["function"]["arguments"] += args


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


def _iter_sse(resp: Iterable[bytes]) -> Iterator[dict]:
    """逐行消费 OpenAI 兼容 SSE 流,产出每个 data 帧解析出的 JSON 对象。

    纪律(从识图函数原样抽出,行为不变):非 data: 行忽略;data: [DONE] 即终止;
    心跳/坏帧(非法 JSON 或非对象)跳过;读流中的 IO 异常原样上抛,
    由调用方归一化为 BrainUnavailable。
    """
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
        yield obj


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
                for obj in _iter_sse(resp):
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
