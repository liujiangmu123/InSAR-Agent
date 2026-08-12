"""本地作业 wrapper —— 作业目录文件契约的执行方(对应 WSL 侧的 wrapper.sh)。

职责(与 AGENT-DESIGN §4.7 的 bash wrapper 逐条对应):
  1. 读 cmd.json,启动子进程,stdout/stderr 追加到 job.log
  2. 写 job.pid / job.token;守护线程每 0.2s 刷 job.hb 心跳
  3. 轮询 job.cancel:出现即终止子进程(宽限 10s 后强杀),写 rc=143
  4. 子进程退出:把退出码写入 job.rc —— 完成的唯一标志

用法:python -m insar_agent.runtime.local_wrapper <job_dir>
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

POLL = 0.2
GRACE = 10.0


def _write_rc(job: Path, rc: int) -> None:
    """rc 原子落盘。

    write_text 会先创建/截断出一个空文件再写内容;宿主侧 state() 每 poll 轮询
    job.rc,恰好撞进这个窗口时会把空文件按 -1 解析 —— 真实退出码被吞,
    上层据此误判失败原因。tmp + os.replace 保证读方看到的 rc 要么不存在、
    要么内容完整(触发场景见 tests/test_chaos_runtime.py 的竞态说明)。
    """
    tmp = job / "job.rc.tmp"
    tmp.write_text(str(rc), encoding="utf-8")
    os.replace(tmp, job / "job.rc")


def _fail_fast(job: Path, message: str) -> int:
    """启动即失败:诊断写进 job.log(随日志流可见)+ rc=127 快速终局。

    修复前 cmd.json 缺失/损坏会让 wrapper 带着未捕获异常静默死亡:无 pid、
    无 rc,宿主只能等 startup_grace 耗尽后误判 orphaned(慢),诊断埋在
    wrapper.err 里对日志流不可见(触发场景:渲染中断/外部篡改作业目录)。
    """
    with open(job / "job.log", "ab") as log:
        log.write(f"[wrapper] {message}\n".encode())
    _write_rc(job, 127)
    return 127


def main(job_dir_arg: str) -> int:
    job = Path(job_dir_arg)
    try:
        spec = json.loads((job / "cmd.json").read_text(encoding="utf-8"))
        argv: list[str] = list(spec["argv"])
        cwd: str = spec.get("cwd") or str(job)
        extra_env: dict = spec.get("env") or {}
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        return _fail_fast(job, f"bad cmd.json: {exc!r}")
    env = dict(os.environ)
    env.update(extra_env)

    (job / "job.pid").write_text(str(os.getpid()), encoding="utf-8")
    (job / "job.token").write_text(uuid.uuid4().hex, encoding="utf-8")
    # 心跳走独立守护线程,且先于子进程 spawn:hb 表达的是 wrapper 存活,
    # 不是子进程存活,也不能被 Popen 阻塞(杀软实时扫描下新进程创建可达秒级)
    # 拖停 —— 否则判活逻辑(jobs.py state:无 hb / hb 超龄 → orphaned)
    # 会把慢启动误判为孤儿。wrapper 被 kill -9 时线程随进程死,hb 停更,
    # 孤儿判定语义不变
    hb = job / "job.hb"
    hb.touch()

    def _beat() -> None:
        while True:
            try:
                hb.touch()
            except OSError:
                pass
            time.sleep(POLL)

    threading.Thread(target=_beat, daemon=True).start()

    kwargs: dict = {}
    if sys.platform == "win32":
        # CREATE_NEW_PROCESS_GROUP | BELOW_NORMAL_PRIORITY_CLASS
        # 低优先级:重型计算不干扰宿主日常使用(工作区重算管控)。
        # 不加 CREATE_NO_WINDOW:wrapper 自己已持有隐藏控制台(由 jobs.launch 创建),
        # 子进程直接继承同一个隐藏控制台即不可见;若再开新隐藏控制台,
        # venv 启动器等多级链路反而可能踩到标志互斥的坑。SW_HIDE 双保险。
        kwargs["creationflags"] = 0x00000200 | 0x00004000
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0  # SW_HIDE
        kwargs["startupinfo"] = si
    else:
        kwargs["start_new_session"] = True

    with open(job / "job.log", "ab") as log:
        try:
            proc = subprocess.Popen(argv, cwd=cwd, env=env, stdout=log, stderr=log, **kwargs)
        except OSError as exc:
            log.write(f"[wrapper] spawn failed: {exc}\n".encode())
            _write_rc(job, 127)
            return 127

        cancel_marker = job / "job.cancel"
        while True:
            rc = proc.poll()
            if rc is not None:
                _write_rc(job, rc)
                return rc
            if cancel_marker.exists():
                proc.terminate()
                deadline = time.time() + GRACE
                while proc.poll() is None and time.time() < deadline:
                    time.sleep(0.1)
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=GRACE)
                _write_rc(job, 143)  # 128+15
                return 143
            time.sleep(POLL)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
