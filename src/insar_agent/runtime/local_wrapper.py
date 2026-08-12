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


def main(job_dir_arg: str) -> int:
    job = Path(job_dir_arg)
    spec = json.loads((job / "cmd.json").read_text(encoding="utf-8"))
    argv: list[str] = spec["argv"]
    cwd: str = spec.get("cwd") or str(job)
    env = dict(os.environ)
    env.update(spec.get("env") or {})

    # 心跳先于 pid 落盘:判活逻辑(jobs.py state)见 pid 无 hb 即判 orphaned,
    # 若先写 pid,「pid 已写、hb 未建」的间隙(杀软实时扫描下 token 写入可达
    # 秒级)会被宿主的连续两次轮询同时命中而误判孤儿 —— 全量测试下
    # test_run_script_export 等用例的偶发失败即源于此。hb 先行则该窗口不存在:
    # hb 在而 pid 未写时 state=unknown,由 startup_grace 兜底,语义不变。
    # 心跳走独立守护线程,且先于子进程 spawn:hb 表达的是 wrapper 存活,
    # 不是子进程存活,也不能被 Popen 阻塞。wrapper 被 kill -9 时线程随进程死,
    # hb 停更,孤儿判定语义不变
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

    (job / "job.pid").write_text(str(os.getpid()), encoding="utf-8")
    (job / "job.token").write_text(uuid.uuid4().hex, encoding="utf-8")

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
            (job / "job.rc").write_text("127", encoding="utf-8")
            return 127

        cancel_marker = job / "job.cancel"
        while True:
            rc = proc.poll()
            if rc is not None:
                (job / "job.rc").write_text(str(rc), encoding="utf-8")
                return rc
            if cancel_marker.exists():
                proc.terminate()
                deadline = time.time() + GRACE
                while proc.poll() is None and time.time() < deadline:
                    time.sleep(0.1)
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=GRACE)
                (job / "job.rc").write_text("143", encoding="utf-8")  # 128+15
                return 143
            time.sleep(POLL)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
