"""数据下载凭证(NASA Earthdata):文件通道 + 第 1 步引擎环境变量注入。

第 1 步数据获取(asf_search / hyp3_sdk)需要 Earthdata 登录凭证。本模块与
brain/llm_config 的密钥纪律同级:

安全边界:
  - 凭证只落 workspace/credentials.json(.gitignore 已排除 workspace/,不进版本库);
  - API 层只回掩码(mask_secret 全掩,不泄露长度;用户名非密钥,保留明文);
  - 诊断包侧由 report/diagbundle 做值级擦除(secret_values 提供密文清单);
  - 日志不落:注入走环境变量,引擎脚本只打印「方式」不打印值。

消费口径 = 环境变量注入(materialize_env),不写 ~/.netrc。依据:
  - runtime/probe.py 的凭据判定(_CREDENTIALS)本就认 EARTHDATA_TOKEN 环境变量;
  - engines/hyp3.py 凭据纪律「凭据只从环境变量/~/.netrc 读取,命令行里不出现」——
    两者中 ~/.netrc 要写用户主目录(侵入其他程序、进程死后仍残留明文),
    环境变量随作业进程消亡,是唯一不落额外明文的通道;
  - asf_search(ASFSession.auth_with_token / auth_with_creds)与 hyp3_sdk 均可从
    EARTHDATA_TOKEN / EARTHDATA_USERNAME / EARTHDATA_PASSWORD 显式取凭证
    (引擎 fetch 脚本由本仓生成,消费契约由我们定义,与上述惯例对齐)。

字段:edl_token(推荐,EDL 官网「Generate Token」)与 earthdata_username /
earthdata_password 双方式并存,token 优先。不设 hyp3_prompt:agent 的作业进程
无交互终端,hyp3_sdk 的 prompt 形态在此无意义。
"""

from __future__ import annotations

import json
from pathlib import Path

from insar_agent.core.fsio import atomic_write_text

_FILENAME = "credentials.json"

_ALLOWED_KEYS = ("earthdata_username", "earthdata_password", "edl_token")

#: 凭证字段 → 注入给第 1 步引擎子进程的环境变量名
_ENV_OF = {
    "edl_token": "EARTHDATA_TOKEN",
    "earthdata_username": "EARTHDATA_USERNAME",
    "earthdata_password": "EARTHDATA_PASSWORD",
}

#: 值级擦除的最短密文长度:再短的值当普通词处理,避免诊断包里误伤正常文本
_MIN_SECRET_LEN = 6


def credentials_path(home: Path) -> Path:
    return Path(home) / _FILENAME


def load_credentials(home: Path) -> dict:
    """读文件配置;缺失/损坏返回 {}(绝不抛:配置坏了 = 未配置)。

    utf-8-sig:容忍 BOM —— 本文件允许用户手工编辑,Windows 记事本/
    PowerShell 5.1 Set-Content 写 UTF-8 都会带 BOM(实测踩坑)。
    """
    try:
        raw = json.loads(credentials_path(home).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {k: raw[k] for k in _ALLOWED_KEYS
            if isinstance(raw.get(k), str) and raw[k].strip()}


def save_credentials(home: Path, updates: dict) -> dict:
    """合并写入(None/空串字段 = 保留旧值;界面密钥框留空不覆盖,同 llm_config)。"""
    cfg = load_credentials(home)
    for k in _ALLOWED_KEYS:
        v = updates.get(k)
        if isinstance(v, str) and v.strip():
            cfg[k] = v.strip()
    Path(home).mkdir(parents=True, exist_ok=True)
    atomic_write_text(credentials_path(home),
                      json.dumps(cfg, ensure_ascii=False, indent=2))
    return cfg


def mask_secret(value: str) -> str:
    """密码/token 全掩:定宽星号,连长度都不泄露;空值回空串(界面按未配置渲染)。"""
    return "********" if (value or "").strip() else ""


def configured_mode(cfg: dict) -> str:
    """已配置的凭证方式:token 优先(EDL 推荐形态)> password > none。"""
    if cfg.get("edl_token"):
        return "token"
    if cfg.get("earthdata_username") and cfg.get("earthdata_password"):
        return "password"
    return "none"


def materialize_env(home: Path) -> dict[str, str]:
    """凭证 → 第 1 步引擎子进程的环境变量增量(CommandPlan.env 消费)。

    未配置返回 {}:构建器注入后行为与从前完全一致(env 不变、命令行不变)。
    """
    cfg = load_credentials(home)
    return {env: cfg[key] for key, env in _ENV_OF.items() if cfg.get(key)}


def home_for_workspace(workspace: Path) -> Path:
    """会话工作区 <home>/sessions/<sid> → home;其余形态视 workspace 本身为 home。

    依据:api/app.py 是会话工作区的唯一构造点(ws = home / "sessions" / session_id);
    测试/脚本直接拿任意目录当工作区时,凭证文件就找该目录(自包含,无全局状态)。
    """
    ws = Path(workspace)
    if ws.parent.name == "sessions":
        return ws.parent.parent
    return ws


def env_for_workspace(workspace: Path) -> dict[str, str]:
    """engines 构建器的接线入口:按工作区定位 home 并物化凭证环境变量。"""
    return materialize_env(home_for_workspace(workspace))


def secret_values(home: Path) -> tuple[str, ...]:
    """需要值级擦除的密文清单(password/token;用户名非密钥不在列)。

    供 report/diagbundle 在打包前对全部成员做替换 —— 哪怕引擎把凭证回显进
    job.log,诊断包里也只会出现占位符。短于 _MIN_SECRET_LEN 的值不进清单
    (Earthdata 密码策略远长于此;超短值做全文替换只会误伤正常文本)。
    """
    cfg = load_credentials(home)
    return tuple(v for k in ("earthdata_password", "edl_token")
                 if len(v := cfg.get(k, "")) >= _MIN_SECRET_LEN)
