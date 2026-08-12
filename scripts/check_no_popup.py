"""无窗验证:实证「跑一个作业不产生任何可见控制台窗口」。

原理:用 EnumWindows 数可见的控制台类窗口(经典 conhost 的 ConsoleWindowClass
与 Windows Terminal 的 CASCADIA_HOSTING_WINDOW_CLASS),跑一个真实作业
(LocalJobBackend → wrapper → 子进程,与 pytest 同一条链),前后对比。

用法:.venv\\Scripts\\python.exe scripts\\check_no_popup.py
退出码:0 = 无新窗;1 = 检测到新窗(输出窗口标题辅助定位)。
"""

from __future__ import annotations

import asyncio
import ctypes
import ctypes.wintypes as wt
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from insar_agent.runtime.jobs import CommandPlan, LocalJobBackend  # noqa: E402
from insar_agent.runtime.stream import follow_job  # noqa: E402

user32 = ctypes.windll.user32
CONSOLE_CLASSES = {"ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS"}


def visible_console_windows() -> set[tuple[int, str, str]]:
    found: set[tuple[int, str, str]] = set()

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        if cls.value in CONSOLE_CLASSES:
            title = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(hwnd, title, 512)
            found.add((hwnd, cls.value, title.value))
        return True

    user32.EnumWindows(cb, 0)
    return found


async def run_one_job(ws: Path) -> int:
    backend = LocalJobBackend()
    job = ws / "job"
    plan = CommandPlan(
        argv=[sys.executable, "-X", "utf8", "-c",
              "import time\nfor i in range(6):\n    print('tick', i, flush=True)\n    time.sleep(0.5)"],
        cwd=str(ws), env={}, files={})
    backend.prepare(job, plan)
    backend.launch(job)
    out = await follow_job(backend, job, poll=0.2, idle_timeout=30, total_timeout=60)
    return out.exit_code if out.exit_code is not None else -1


def main() -> int:
    before = visible_console_windows()
    print(f"基线可见控制台窗口:{len(before)}")

    ws = Path(tempfile.mkdtemp(prefix="popup-check-"))
    rc_holder: list[int] = []

    async def scenario():
        task = asyncio.create_task(run_one_job(ws))
        # 作业运行期间连续采样窗口(弹窗往往一闪而过,轮询抓现行)
        new_seen: set[tuple[int, str, str]] = set()
        while not task.done():
            now = visible_console_windows()
            new_seen |= (now - before)
            await asyncio.sleep(0.1)
        rc_holder.append(await task)
        return new_seen

    new_windows = asyncio.run(scenario())
    print(f"作业退出码:{rc_holder[0]}")
    if new_windows:
        print("检测到新弹出的控制台窗口:")
        for hwnd, cls, title in new_windows:
            print(f"  hwnd={hwnd} class={cls} title={title!r}")
        return 1
    print("PASS:全程零新窗")
    return 0


if __name__ == "__main__":
    sys.exit(main())
