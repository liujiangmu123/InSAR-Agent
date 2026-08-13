"""PyInstaller 冻结入口:创建 FastAPI app 并常驻 uvicorn(desktop/backend-bundle)。

为什么需要独立入口而不是 python -m insar_agent.api.app:
  1. 冻结产物没有「-m 模块启动」语义,PyInstaller 需要一个真实脚本做分析根;
  2. api/app.py 的 PROTOTYPE_DIR 按 __file__ 回溯源码树(src/../prototype),
     冻结后该路径不存在 —— 必须在 import app 之前解析出 UI 目录并写入
     INSAR_UI_DIR(api/app.py 唯一认可的环境变量覆盖点)。

环境变量:
  INSAR_PORT    监听端口,缺省 8873
  INSAR_HOST    监听地址,缺省 127.0.0.1
  INSAR_HOME    数据目录;冻结态缺省 %LOCALAPPDATA%\\insar-agent-data\\workspace
                (app.py 的相对缺省 workspace/ 会落到 cwd,安装目录可能只读)。
                目录名带 -data 后缀:NSIS 安装目录是 %LOCALAPPDATA%\\InSAR-Agent,
                Windows 路径大小写不敏感,曾用的 insar-agent 与它是同一物理目录,
                用户数据会寄生进安装目录(卸载残留/手动清理误删,首次打包实测)
  INSAR_UI_DIR  静态 UI 目录;显式设置则完全尊重,否则自动探测:
                exe 旁 prototype/ 优先(便于不重打包热改 UI),
                其次 _internal 内打包副本(spec datas 的缺省落点)
"""

from __future__ import annotations

import multiprocessing
import os
import sys
from pathlib import Path


def _frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _ui_candidates() -> list[Path]:
    if _frozen():
        exe_dir = Path(sys.executable).resolve().parent
        cands = [exe_dir / "prototype"]
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            cands.append(Path(meipass) / "prototype")
        return cands
    # 源码直跑(调试用):desktop/backend-bundle/entry.py -> 仓库根/prototype
    return [Path(__file__).resolve().parents[2] / "prototype"]


def _skills_candidates() -> list[Path]:
    """技能文档目录(skills/loader.py 的 INSAR_SKILLS_DIR):exe 旁优先
    (便于不重打包热改技能),其次 _internal 打包副本(spec datas 落点)。"""
    if _frozen():
        exe_dir = Path(sys.executable).resolve().parent
        cands = [exe_dir / "skills"]
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            cands.append(Path(meipass) / "skills")
        return cands
    return [Path(__file__).resolve().parents[2] / "skills"]


def _resolve_env() -> None:
    if not os.environ.get("INSAR_UI_DIR"):
        for cand in _ui_candidates():
            if (cand / "index.html").is_file():
                os.environ["INSAR_UI_DIR"] = str(cand)
                break
    # 技能目录注入(loader.py 头注声明的契约;漏注入 = /api/skills 恒空,
    # DESKTOP-PARITY GAP-2 实测)——显式设置则完全尊重
    if not os.environ.get("INSAR_SKILLS_DIR"):
        for cand in _skills_candidates():
            if cand.is_dir():
                os.environ["INSAR_SKILLS_DIR"] = str(cand)
                break
    if _frozen() and not os.environ.get("INSAR_HOME"):
        base = os.environ.get("LOCALAPPDATA") or str(Path.home())
        os.environ["INSAR_HOME"] = str(Path(base) / "insar-agent-data" / "workspace")


def _ensure_streams() -> None:
    """console=False 且调用方没重定向 stdout/stderr 时,冻结进程的标准流是
    None(pythonw 语义)—— uvicorn 的日志 handler 对着 None 写会直接崩溃
    (实测:带重定向启动正常,裸启动秒退)。绑定到 INSAR_HOME/logs/backend.log,
    既保活也留诊断线索;调用方已重定向(Tauri 壳/冒烟脚本)则完全尊重。"""
    if not _frozen() or (sys.stdout is not None and sys.stderr is not None):
        return
    log_dir = Path(os.environ["INSAR_HOME"]) / "logs"  # _resolve_env 已保证有值
    log_dir.mkdir(parents=True, exist_ok=True)
    stream = open(log_dir / "backend.log", "a", buffering=1,
                  encoding="utf-8", errors="replace")
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream


def _log_crash() -> None:
    """无窗进程没有可见的报错渠道,启动失败必须留下痕迹(显式失败原则)。"""
    try:
        import datetime
        import traceback

        base = os.environ.get("INSAR_HOME") or os.environ.get("LOCALAPPDATA") or "."
        log_dir = Path(base) / "logs" if os.environ.get("INSAR_HOME") \
            else Path(base) / "insar-agent-data" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        with open(log_dir / "backend-crash.log", "a", encoding="utf-8") as fh:
            fh.write(f"\n[{datetime.datetime.now().isoformat()}]\n")
            traceback.print_exc(file=fh)
    except Exception:
        pass  # 崩溃记录本身绝不能再抛


def main() -> None:
    # PROTOTYPE_DIR 在 app.py import 时读取 INSAR_UI_DIR,必须先解析环境再 import
    _resolve_env()
    _ensure_streams()

    import uvicorn

    from insar_agent.api.app import create_app

    uvicorn.run(create_app(),
                host=os.environ.get("INSAR_HOST", "127.0.0.1"),
                port=int(os.environ.get("INSAR_PORT", "8873")))


if __name__ == "__main__":
    multiprocessing.freeze_support()  # 冻结态 multiprocessing 的标准防护
    try:
        main()
    except BaseException:
        _log_crash()
        raise
