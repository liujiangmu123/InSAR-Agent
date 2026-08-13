"""文件系统小工具:tmp + os.replace 原子写(§6.1 写入原则)。

统一此前各自手写的 tmp+replace(core/ledger、report/script、runtime/render、
runtime/jobs;REVIEW 2026-08-12 P2-3):旧写法在「写 tmp 与 replace 之间」抛异常
(磁盘满/编码错/目标被独占)会残留 *.tmp,且固定 tmp 名在并发写同一目标时互踩。
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path


def atomic_write_text(target: Path, text: str, *, encoding: str = "utf-8",
                      newline: str | None = None) -> None:
    """写临时文件后 os.replace 原子落位;失败清理临时文件再抛。

    - 读者永远看不到半截文件(POSIX/Windows 的 replace 都原子);
    - 写入/替换失败不残留 *.tmp;
    - tmp 名带随机后缀:并发写同一目标互不相踩,后完成者胜。
    """
    tmp = target.with_name(f"{target.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp.write_text(text, encoding=encoding, newline=newline)
        os.replace(tmp, target)
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass  # 清理失败不掩盖原始异常(tmp 残留只是垃圾,不影响正确性)
        raise
