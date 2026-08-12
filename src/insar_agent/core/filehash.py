"""三档文件指纹策略(AGENT-DESIGN §1.3 / §5.5)。

| 数据类别                        | 策略      | 依据                          |
|---------------------------------|-----------|-------------------------------|
| 原始 SLC / DEM(只增不改)      | path      | redun file.py:1710-1716(IFile)|
| 中间产物(.int/.unw/.cor)      | stat      | redun file.py:463-475          |
| 最终成果(velocity.h5/图件)    | content   | redun file.py:1784-1788        |

要点:
  - 不存在编码为 size=-1, mtime=-1 ——「产物被删」是正常哈希差异,不是异常分支
    (借鉴 redun file.py:463-475 的 -1 编码)。
  - content 档流式 4 MB 块(redun 默认 1024 B 对 GB 级文件太小,§1.3)。
  - 目录级产物 = 文件数 + 排序后 (relpath,size,mtime) 序列哈希 —— 应对 §1.3 的
    「5 个栅格字节数完全相同」碰撞风险。
  - 对外指纹统一三段编码 "<policy>:<algo>:<digest>"(§6.1 artifacts.fp,absorb-M:
    算法名显式入档,dvc serialize.py:160-161 的做法)—— 指纹策略/算法升级后,
    旧档一眼可辨,不会被误当同域比较。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from insar_agent.core.normalize import hash_struct

POLICIES = ("path", "stat", "content")
# digest 家族:content 的 pre-image 含流式 sha256;path/stat 是结构哈希,标格式版本
_ALGO = {"path": "v1", "stat": "v1", "content": "sha256"}
_BLOCK = 4 << 20  # 4 MB


def _stream_sha(path: Path, block: int = _BLOCK) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(block), b""):
            h.update(chunk)
    return h.hexdigest()


def fingerprint_path(path: Path | str, policy: str) -> str:
    """单文件指纹。policy: path | stat | content。"""
    if policy not in POLICIES:
        raise ValueError(f"unknown fingerprint policy: {policy}")
    p = Path(path)
    key = str(p).replace("\\", "/")

    if policy == "path":
        return hash_struct(["File", "path", key])
    if not p.exists():
        return hash_struct(["File", policy, key, -1, -1])  # -1 编码:不存在
    st = p.stat()
    if policy == "stat":
        return hash_struct(["File", "stat", key, st.st_size, st.st_mtime_ns])
    return hash_struct(["File", "content", key, st.st_size, _stream_sha(p)])


def fingerprint_dir(path: Path | str, policy: str = "stat") -> str:
    """目录级产物指纹:文件数 + 各文件 (relpath,size,mtime) 排序后哈希。

    policy='content' 时逐文件全量哈希(仅用于小目录,如最终成果目录)。
    """
    p = Path(path)
    key = str(p).replace("\\", "/")
    if not p.exists():
        return hash_struct(["Dir", policy, key, -1])
    entries: list[list] = []
    for f in sorted(p.rglob("*")):
        if not f.is_file():
            continue
        rel = str(f.relative_to(p)).replace("\\", "/")
        st = f.stat()
        if policy == "content":
            entries.append([rel, st.st_size, _stream_sha(f)])
        else:
            entries.append([rel, st.st_size, st.st_mtime_ns])
    return hash_struct(["Dir", policy, key, len(entries), entries])


def fingerprint(path: Path | str, policy: str) -> str:
    """产物指纹入口,返回三段编码 "<policy>:<algo>:<digest>"。

    如 content:sha256:ab12…、stat:v1:…、path:v1:…。目录走 fingerprint_dir
    聚合,文件走 fingerprint_path;不存在的目标同样得到合法三段编码
    (digest 是 -1 编码的结构哈希),差异在比较时自然显现。
    """
    if policy not in POLICIES:
        raise ValueError(f"unknown fingerprint policy: {policy}")
    p = Path(path)
    if p.is_dir():
        eff = "stat" if policy == "path" else policy  # 目录没有「仅路径」档(§5.5)
        return f"{eff}:{_ALGO[eff]}:{fingerprint_dir(p, eff)}"
    return f"{policy}:{_ALGO[policy]}:{fingerprint_path(p, policy)}"


# 旧入口名,executor/stale 沿用;语义与 fingerprint 相同
fingerprint_target = fingerprint
