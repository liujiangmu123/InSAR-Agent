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

实现说明(2026-08 性能改造,输出与旧 rglob 实现逐字节一致,由
tests/test_hash_semantics_lock.py 的参照实现等价测试钉死):
  - 目录遍历改 os.scandir 迭代:Windows 上 DirEntry 的 is_dir/is_file/stat
    直接来自枚举缓存,免去旧实现逐文件两次 os.stat 系统调用;
  - 相对路径由部件元组拼接,免 Path.relative_to 的逐文件 Path 运算;
  - 排序契约:pathlib 按「大小写归一的部件元组」排序(Windows 逐部件 lower,
    POSIX 原样,3.11–3.14 语义相同),此处用同一键排序,顺序与旧
    sorted(p.rglob("*")) 完全一致;
  - 符号链接目录不下钻(同 Python 3.13+ rglob 默认语义;Windows junction
    正常下钻,与旧实现一致)。
"""

from __future__ import annotations

import hashlib
import os
from operator import itemgetter
from pathlib import Path

from insar_agent.core.normalize import hash_struct

POLICIES = ("path", "stat", "content")
# digest 家族:content 的 pre-image 含流式 sha256;path/stat 是结构哈希,标格式版本
_ALGO = {"path": "v1", "stat": "v1", "content": "sha256"}
_BLOCK = 4 << 20  # 4 MB
_WIN = os.name == "nt"


def _stream_sha(path: Path | str, block: int = _BLOCK) -> str:
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
    try:
        # 单次 stat 同时充当存在性判定(旧实现 exists()+stat() 两次系统调用);
        # 捕获 (OSError, ValueError) 与 Path.exists 对非法路径的吞错范围一致
        st = p.stat()
    except (OSError, ValueError):
        return hash_struct(["File", policy, key, -1, -1])  # -1 编码:不存在
    if policy == "stat":
        return hash_struct(["File", "stat", key, st.st_size, st.st_mtime_ns])
    return hash_struct(["File", "content", key, st.st_size, _stream_sha(p)])


_BY_KEY_THEN_PARTS = itemgetter(0, 1)


def _walk_files(root: str) -> list[tuple[tuple[str, ...], tuple[str, ...], os.stat_result, str]]:
    """收集 (排序键, 相对部件, stat, 绝对路径),排序键复刻 pathlib 部件序。"""
    files: list[tuple[tuple[str, ...], tuple[str, ...], os.stat_result, str]] = []
    # 小写化排序键沿栈增量构造(每条目 O(1)),POSIX 上即原始部件
    stack: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = [(root, (), ())]
    while stack:
        base, rel, low = stack.pop()
        try:
            it = os.scandir(base)
        except NotADirectoryError:
            continue  # 对文件调用:旧实现 rglob 产出空集,等价
        except OSError:
            continue  # 遍历中途目录消失/无权限:跳过,与 glob 的容错一致
        with it:
            for entry in it:
                name = entry.name
                parts = rel + (name,)
                lows = low + (name.lower(),) if _WIN else parts
                try:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append((entry.path, parts, lows))
                    elif entry.is_file():
                        files.append((lows, parts, entry.stat(), entry.path))
                except OSError:
                    continue  # 条目竞态消失:跳过
    # 同键(仅大小写异体共存的大小写敏感目录)以原始部件决胜,保证确定性;
    # stat_result 永不参与比较(前两键相等即同一路径,不可能出现)
    files.sort(key=_BY_KEY_THEN_PARTS)
    return files


def fingerprint_dir(path: Path | str, policy: str = "stat") -> str:
    """目录级产物指纹:文件数 + 各文件 (relpath,size,mtime) 排序后哈希。

    policy='content' 时逐文件全量哈希(仅用于小目录,如最终成果目录)。
    """
    p = Path(path)
    key = str(p).replace("\\", "/")
    if not p.exists():
        return hash_struct(["Dir", policy, key, -1])
    entries: list[list] = []
    if policy == "content":
        for _, parts, st, abspath in _walk_files(str(p)):
            entries.append(["/".join(parts), st.st_size, _stream_sha(abspath)])
    else:
        for _, parts, st, _abspath in _walk_files(str(p)):
            entries.append(["/".join(parts), st.st_size, st.st_mtime_ns])
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
