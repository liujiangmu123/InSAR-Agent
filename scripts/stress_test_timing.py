"""时序测试抗负载验证:模拟「机器高负载(多代理并行)」下 timing 组的稳定性。

用法(默认 3 轮 × 系数 3,总预算控制在 5 分钟内):
    .venv\\Scripts\\python.exe scripts/stress_test_timing.py
    .venv\\Scripts\\python.exe scripts/stress_test_timing.py --rounds 3 --factor 3

做法(负载模拟,非科学计算;全程受控自灭):
  - 起 2 个忙循环进程,合计占约 50% 逻辑核:每进程 cores//4 条线程对 1 MiB
    缓冲区反复 sha256 —— hashlib 对大块数据释放 GIL,单进程即可真占多核;
  - 每个忙循环进程内置看门狗(默认 60 秒)自灭,即使父进程被杀也不残留;
    父进程每轮结束后也主动 terminate 兜底,绝不留孤儿负载;
  - 负载在场时串行跑 `pytest -m timing`(INSAR_TEST_TIME_FACTOR=<factor>)× N 轮,
    全部退出码 0 才算通过(退出码即本脚本退出码)。
"""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _burn(seconds: float, threads: int) -> None:
    """忙循环子进程体:threads 条线程做大块 sha256(释放 GIL,真占多核),
    到点自灭。"""
    deadline = time.monotonic() + seconds
    buf = os.urandom(1024 * 1024)

    def spin() -> None:
        h = hashlib.sha256()
        while time.monotonic() < deadline:
            h.update(buf)

    workers = [threading.Thread(target=spin, daemon=True) for _ in range(threads)]
    for w in workers:
        w.start()
    for w in workers:
        w.join()


def _start_load(procs: int, threads_per_proc: int, seconds: float) -> list[subprocess.Popen]:
    argv = [sys.executable, str(Path(__file__).resolve()),
            "--burn", str(seconds), "--threads", str(threads_per_proc)]
    return [subprocess.Popen(argv) for _ in range(procs)]


def _stop_load(burners: list[subprocess.Popen]) -> None:
    for p in burners:
        if p.poll() is None:
            p.terminate()
    for p in burners:
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait(timeout=10)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # 中文输出不随控制台代码页乱码
    parser = argparse.ArgumentParser(
        description="timing 组抗负载验证(2 忙循环进程 ≈50% 核,受控自灭)")
    parser.add_argument("--rounds", type=int, default=3, help="timing 组跑几轮(默认 3)")
    parser.add_argument("--factor", default="3",
                        help="INSAR_TEST_TIME_FACTOR(默认 3)")
    parser.add_argument("--burn-seconds", type=float, default=60.0,
                        help="每轮负载进程的自灭上限秒数(默认 60)")
    # 子进程模式(内部使用):--burn <秒> --threads <N>
    parser.add_argument("--burn", type=float, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--threads", type=int, default=1, help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.burn is not None:  # 忙循环子进程:纯自灭负载,不碰任何项目状态
        _burn(args.burn, args.threads)
        return 0

    cores = os.cpu_count() or 8
    threads_per_proc = max(1, cores // 4)  # 2 进程 × cores//4 线程 ≈ 50% 核
    env = {**os.environ, "INSAR_TEST_TIME_FACTOR": str(args.factor)}
    pytest_argv = [sys.executable, "-m", "pytest", "-m", "timing", "-q",
                   "--tb=short", "-p", "no:warnings"]

    print(f"[stress] 逻辑核 {cores};负载 = 2 进程 × {threads_per_proc} 线程"
          f"(≈50% 核,每轮 {args.burn_seconds:g}s 自灭);"
          f"INSAR_TEST_TIME_FACTOR={args.factor};timing 组 × {args.rounds} 轮",
          flush=True)
    t0 = time.monotonic()
    failures = 0
    for i in range(1, args.rounds + 1):
        burners = _start_load(2, threads_per_proc, args.burn_seconds)
        started = time.monotonic()
        try:
            cp = subprocess.run(pytest_argv, cwd=str(ROOT), env=env)
        finally:
            _stop_load(burners)
        dt = time.monotonic() - started
        status = "PASS" if cp.returncode == 0 else f"FAIL(rc={cp.returncode})"
        print(f"[stress] 第 {i}/{args.rounds} 轮:{status},{dt:.1f}s", flush=True)
        if cp.returncode != 0:
            failures += 1
    total = time.monotonic() - t0
    verdict = "全绿" if failures == 0 else f"{failures} 轮失败"
    print(f"[stress] 结论:{args.rounds} 轮 {verdict};总耗时 {total:.1f}s", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
