#!/usr/bin/env bash
# WSL 内作业 wrapper(AGENT-DESIGN §4.7 原文实现)。
# 由宿主渲染后放进作业目录;是唯一知道 Linux PID 的地方。
set -uo pipefail
JOB="$1"

set -m                                          # 独立进程组,便于整组信号
bash "$JOB/cmd.sh" >> "$JOB/job.log" 2>&1 &
CHILD=$!
echo "$CHILD"                        > "$JOB/job.pid"
ps -o pgid= -p "$CHILD" | tr -d ' '  > "$JOB/job.pgid"
awk '{print $22}' "/proc/$CHILD/stat" > "$JOB/job.start"

while kill -0 "$CHILD" 2>/dev/null; do          # 取消轮询:不依赖宿主信号能力
  if [ -f "$JOB/job.cancel" ]; then
    PGID=$(cat "$JOB/job.pgid")
    kill -TERM -"$PGID" 2>/dev/null             # 整组 SIGTERM
    for _ in $(seq 1 100); do                   # 宽限 10 s
      kill -0 "$CHILD" 2>/dev/null || break
      sleep 0.1
    done
    kill -KILL -"$PGID" 2>/dev/null             # 兜底 SIGKILL
    echo 143 > "$JOB/job.rc"                    # 128+15
    exit 143
  fi
  sleep 0.5
done

wait "$CHILD"; echo $? > "$JOB/job.rc"          # 唯一的完成标志
