"""失败分类闭集(AGENT-DESIGN §4.12)。LLM 只能从这个枚举里选,不能发明新类别。

分类靠确定性规则优先(正则,顺序敏感),LLM 兜底;全部未命中 → UNKNOWN(停链问人)。
UNKNOWN 占比是可观测指标:升高说明有新失败模式需要加规则。
"""

from __future__ import annotations

import re
from enum import StrEnum


class FailureClass(StrEnum):
    # 环境类 —— 可自愈
    NETWORK_TRANSIENT = "network_transient"   # → 指数退避重试 ≤3
    AUTH_EXPIRED = "auth_expired"             # → 换账号
    QUOTA_EXHAUSTED = "quota_exhausted"       # → 换账号或排队
    SERVICE_DOWN = "service_down"             # → 降级(带证据级别代价)
    # 资源类 —— 可自愈但需调整
    DISK_FULL = "disk_full"                   # → GC 建议 + 暂停
    OOM = "oom"                               # → 降并行度重试 ≤1
    TIMEOUT = "timeout"                       # → 重试或调 capability 超时声明
    # 环境损坏 —— 停链
    TOOL_MISSING = "tool_missing"             # → 停,报环境问题
    WSL_ORPHANED = "wsl_orphaned"             # → 停,可续跑(非计算失败)
    # 参数/数据类 —— 停链,回到决策点
    PARAM_INVALID = "param_invalid"           # → 回候选集
    DATA_QUALITY = "data_quality"             # → 质量门拦截,停链
    CONTRACT_BROKEN = "contract_broken"       # → 停,报 bug
    # 兜底
    UNKNOWN = "unknown"                       # → 停链问人,绝不猜


# 顺序敏感:先匹配先生效(§4.12)
RULES: list[tuple[str, FailureClass]] = [
    (r"No space left on device|ENOSPC", FailureClass.DISK_FULL),
    (r"\bKilled\b|Out of memory|MemoryError|OOM", FailureClass.OOM),
    (r"command not found|No such file.*\.py|ModuleNotFoundError", FailureClass.TOOL_MISSING),
    (r"HTTP 40[13]|Unauthorized|Forbidden|401|403", FailureClass.AUTH_EXPIRED),
    (r"quota|credits exhausted", FailureClass.QUOTA_EXHAUSTED),
    (r"HTTP 5\d\d|Service Unavailable|Bad Gateway", FailureClass.SERVICE_DOWN),
    (r"timed out|Connection reset|ConnectionError", FailureClass.NETWORK_TRANSIENT),
    (r"invalid (parameter|argument|value)|ValueError", FailureClass.PARAM_INVALID),
]

# 每类的预定义处置(闭集;LLM 只分类不发明,§1.5)
DISPOSITIONS: dict[FailureClass, dict] = {
    FailureClass.NETWORK_TRANSIENT: {"auto": "retry", "max_retries": 3, "note": "指数退避重试"},
    FailureClass.AUTH_EXPIRED: {"auto": "stop", "note": "换账号/更新凭据后重试"},
    FailureClass.QUOTA_EXHAUSTED: {"auto": "stop", "note": "换账号或等待配额"},
    FailureClass.SERVICE_DOWN: {"auto": "degrade", "note": "按降级矩阵降级(证据级别下降,须显式告知)"},  # noqa: E501
    FailureClass.DISK_FULL: {"auto": "pause", "note": "清理已归档中间产物后续跑"},
    FailureClass.OOM: {"auto": "retry_reduced", "max_retries": 1, "note": "降并行度重试一次"},
    FailureClass.TIMEOUT: {"auto": "stop", "note": "确认任务规模或调大 capability 超时声明"},
    FailureClass.TOOL_MISSING: {"auto": "stop", "note": "环境问题:安装/修复引擎"},
    FailureClass.WSL_ORPHANED: {"auto": "stop", "note": "重启 WSL 后续跑(不是重跑)"},
    FailureClass.PARAM_INVALID: {"auto": "stop", "note": "回候选集重选参数"},
    FailureClass.DATA_QUALITY: {"auto": "stop", "note": "质量门拦截:换方法或放宽阈值(需依据)"},
    FailureClass.CONTRACT_BROKEN: {"auto": "stop", "note": "产物契约破坏:报 issue"},
    FailureClass.UNKNOWN: {"auto": "stop", "note": "未知失败:人工介入,绝不猜"},
}


def classify_log(text: str) -> FailureClass | None:
    """确定性规则分类。None = 规则未命中(交给 LLM 或标 UNKNOWN)。"""
    for pattern, cls in RULES:
        if re.search(pattern, text, re.IGNORECASE):
            return cls
    return None


def error_window(log_text: str, *, context: int = 5, max_chars: int = 2000) -> str:
    """错误行 ±context 行的窗口 —— 给 LLM 的唯一日志视图(§3.3 约束四:日志绝不全量进上下文)。"""
    lines = log_text.splitlines()
    hit = None
    for i, line in enumerate(lines):
        if re.search(r"error|fail|traceback|exception", line, re.IGNORECASE):
            hit = i
            break
    if hit is None:
        window = lines[-(2 * context + 1):]
    else:
        window = lines[max(0, hit - context): hit + context + 1]
    return "\n".join(window)[:max_chars]
