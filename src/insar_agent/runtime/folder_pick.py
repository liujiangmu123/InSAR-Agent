"""本机「选文件夹」对话框(桌面浏览按钮的备用通路)。

Tauri 原生对话框是首选;壳 invoke 失败或非桌面环境时,前端改调
POST /api/projects/pick-folder。本模块弹系统选择器(tkinter,标准库)。
测试通过 _override 注入,绝不在 CI 弹窗。
"""

from __future__ import annotations

from typing import Callable

Picker = Callable[[str], str | None]

#: 测试注入点
_override: Picker | None = None


def pick_folder(title: str = "选择项目文件夹") -> str | None:
    if _override is not None:
        return _override(title)
    return _pick_tk(title)


def _pick_tk(title: str) -> str | None:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError:
        return None
    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
        root.lift()
        root.focus_force()
        root.update()
    except Exception:  # noqa: BLE001 —— 置顶失败仍继续弹窗
        pass
    try:
        chosen = filedialog.askdirectory(parent=root, title=title, mustexist=True)
    finally:
        try:
            root.destroy()
        except Exception:  # noqa: BLE001
            pass
    path = (chosen or "").strip()
    return path or None
