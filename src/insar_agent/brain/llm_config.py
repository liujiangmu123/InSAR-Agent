"""LLM 供应商配置:文件(workspace/llm.json)+ 环境变量双通道。

安全边界:
  - api_key 只落在 workspace/llm.json(.gitignore 已排除 workspace/,不进版本库);
  - API 层永远只回显掩码(mask_key),完整密钥不出服务端;
  - 环境变量通道(INSAR_LLM_*)保留为部署/CI 形态,文件配置优先(界面可改)。

路由组合语义(provider.LLMRoute):
  文件配置(chat_model)为主路由;环境变量路由追加为 fallback(单跳,§3.5)。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from insar_agent.brain.provider import LLMRoute, routes_from_env
from insar_agent.core.fsio import atomic_write_text

_FILENAME = "llm.json"

#: 界面「获取模型」前的缺省接入点(用户 2026-08-13 提供的中转站;可在界面改)
DEFAULT_BASE_URL = "https://tokenrhythm.studio/v1"

_ALLOWED_KEYS = ("base_url", "api_key", "chat_model", "vision_model")

#: 自主循环缺省值(LOOP-CONTRACT §8):开关默认开,单回合周期上限默认 6(闭区间 1..12)
AGENT_LOOP_DEFAULT = True
AGENT_MAX_CYCLES_DEFAULT = 6
AGENT_MAX_CYCLES_MIN = 1
AGENT_MAX_CYCLES_MAX = 12


def config_path(home: Path) -> Path:
    return Path(home) / _FILENAME


def _agent_fields(raw: dict) -> dict:
    """自主循环两键(agent_loop / agent_max_cycles)的值域过滤。

    类型与区间都合法才收,否则整键丢弃(读取时 = 回默认;保存时 = 保留旧值)。
    注意 bool 是 int 的子类:agent_max_cycles=true 这类形态必须拒绝。
    """
    out: dict = {}
    if isinstance(raw.get("agent_loop"), bool):
        out["agent_loop"] = raw["agent_loop"]
    v = raw.get("agent_max_cycles")
    if (isinstance(v, int) and not isinstance(v, bool)
            and AGENT_MAX_CYCLES_MIN <= v <= AGENT_MAX_CYCLES_MAX):
        out["agent_max_cycles"] = v
    return out


def load_llm_config(home: Path) -> dict:
    """读文件配置;缺失/损坏返回 {}(绝不抛:配置坏了 = 未配置)。"""
    p = config_path(home)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    cfg = {k: raw[k] for k in _ALLOWED_KEYS
           if isinstance(raw.get(k), str) and raw[k].strip()}
    cfg.update(_agent_fields(raw))  # 非法/越界的循环键在此被剔除(= 未配置)
    return cfg


def save_llm_config(home: Path, updates: dict) -> dict:
    """合并写入(None/空串字段 = 保留旧值;api_key 同理,界面留空不覆盖)。"""
    cfg = load_llm_config(home)
    for k in _ALLOWED_KEYS:
        v = updates.get(k)
        if isinstance(v, str) and v.strip():
            cfg[k] = v.strip()
    cfg.update(_agent_fields(updates))  # 循环两键同语义:合法值覆盖,None/非法保留旧值
    Path(home).mkdir(parents=True, exist_ok=True)
    atomic_write_text(config_path(home), json.dumps(cfg, ensure_ascii=False, indent=2))
    return cfg


def agent_loop_settings(home: Path) -> dict:
    """自主循环设置(/api/converse 缺省周期数与循环开关的数据源,LOOP-CONTRACT §8)。

    与 load_llm_config 同哲学:文件缺失/损坏、键缺失、类型或区间非法一律回默认,绝不抛。
    """
    cfg = load_llm_config(home)
    return {"enabled": cfg.get("agent_loop", AGENT_LOOP_DEFAULT),
            "max_cycles": cfg.get("agent_max_cycles", AGENT_MAX_CYCLES_DEFAULT)}


def mask_key(key: str) -> str:
    """掩码展示:保留前缀段与末 4 位(sk_tr_r7…hOUE 形态),短键全掩。"""
    key = (key or "").strip()
    if not key:
        return ""
    if len(key) <= 12:
        return "*" * len(key)
    return f"{key[:8]}…{key[-4:]}"


def routes_from_config(home: Path) -> list[LLMRoute]:
    """文件配置为主路由,环境变量路由追加为 fallback。

    文件配置不完整(缺 base_url/api_key/chat_model 任一)时不产生文件路由,
    与「未配置 = brain 禁用,系统退化为手动流水线」的既有语义一致。
    """
    routes: list[LLMRoute] = []
    cfg = load_llm_config(home)
    if cfg.get("base_url") and cfg.get("api_key") and cfg.get("chat_model"):
        routes.append(LLMRoute(cfg["base_url"].rstrip("/"), cfg["api_key"],
                               cfg["chat_model"]))
    routes.extend(routes_from_env())
    return routes


def vision_route_from_config(home: Path) -> LLMRoute | None:
    """识图路由:vision_model 独立于对话模型(识图必须流式,provider 侧保证)。"""
    cfg = load_llm_config(home)
    if cfg.get("base_url") and cfg.get("api_key") and cfg.get("vision_model"):
        return LLMRoute(cfg["base_url"].rstrip("/"), cfg["api_key"],
                        cfg["vision_model"])
    # 环境变量形态的识图模型(可选):INSAR_LLM_VISION_MODEL 复用主路由的地址与密钥
    if (os.environ.get("INSAR_LLM_BASE_URL")
            and os.environ.get("INSAR_LLM_VISION_MODEL")):
        return LLMRoute(os.environ["INSAR_LLM_BASE_URL"].rstrip("/"),
                        os.environ.get("INSAR_LLM_API_KEY", ""),
                        os.environ["INSAR_LLM_VISION_MODEL"])
    return None
