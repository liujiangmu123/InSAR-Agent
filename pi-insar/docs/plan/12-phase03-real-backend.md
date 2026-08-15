# Phase 03 · 真实数据后端与 pi 会话贯通(P0)

> **前置**:Phase 01(ps1 启动器)与 Phase 02(insar-llm 供应商)已完成。
> **目标**:一键拉起"真实引擎 + 真实数据 + realtest HOME"的后端;pi 里完成对既有 audited run 的全链读回;(经用户批准)可选真实新 run。**本 Phase 不改任何 TS/Python 源码**,只加启动脚本与走查手册——真实读回本身就是对 16 个工具的端到端真数据验收。
> **涉及文件**:新建 `scripts/insar-backend-real.ps1`、`docs/PI-REAL-SESSION.md`。

## 步骤 2.1 新建 `E:\01所有项目\06定职讲师\00insaragent\scripts\insar-backend-real.ps1`

用途:以真实模式启动后端。环境变量默认值与 `scripts/real_ridgecrest.py` 第 24-27 行**同源**(不造新路径);`INSAR_ALLOW_SIMULATED=0` 与该脚本 `allow_simulated=False` 同语义(不可行就失败,绝不静默模拟)。可直接落盘:

```powershell
# insar-backend-real.ps1 — InSAR backend in REAL mode (MintPy conda env + real HyP3 data).
# Defaults mirror scripts/real_ridgecrest.py; realtest HOME carries the audited
# Ridgecrest run so pi sessions can read it back immediately.
#
#   pwsh scripts/insar-backend-real.ps1                       # HOME=workspace\realtest, port 8873
#   pwsh scripts/insar-backend-real.ps1 -InsarHome workspace  # main home instead
[CmdletBinding()]
param(
  [string]$InsarHome = "workspace\realtest",
  [int]$Port = 8873
)
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$env:INSAR_HOME = Join-Path $RepoRoot $InsarHome
$env:INSAR_PORT = "$Port"
$env:INSAR_ALLOW_SIMULATED = "0"    # real mode: fail honestly, never simulate silently
if (-not $env:INSAR_ENGINE_PREFIX) { $env:INSAR_ENGINE_PREFIX = "E:\miniforge3\envs\insar" }
if (-not $env:INSAR_HYP3_SOURCE) {
  $env:INSAR_HYP3_SOURCE = "E:\01所有项目\06定职讲师\InSAR-Pro\insar-pro\backend\data\real_data\RidgecrestSenDT71"
}

Write-Host "INSAR_HOME          = $($env:INSAR_HOME)"
Write-Host "INSAR_ENGINE_PREFIX = $($env:INSAR_ENGINE_PREFIX)"
Write-Host "INSAR_HYP3_SOURCE   = $($env:INSAR_HYP3_SOURCE)"
& (Join-Path $RepoRoot ".venv\Scripts\python.exe") -m insar_agent.api.app
```

## 步骤 2.2 新建 `E:\01所有项目\06定职讲师\00insaragent\docs\PI-REAL-SESSION.md`

用途:真实走查手册(人工验收剧本,验收产物是真实输出与真实截图)。内容按以下序号写全:

1. 终端 A:`pwsh scripts/insar-backend-real.ps1`;确认启动日志与 `Invoke-WebRequest http://127.0.0.1:8873/api/health` 返回 `{"ok": true, ...}`。
2. 终端 B:`pwsh scripts/insar-pi.ps1 --insar-session real`(绑定 realtest 里既有的 `real` 会话)。
3. 会话内依次让模型执行并核对(全部是真实数据读回):
   - `insar_list_sessions` → 列表含 `real`;
   - `insar_run_status`(session=real)→ 11 步全 done、五阶段 V、progress 100%、`simulated=false`、evidence 为 **audited**;侧栏轨道同步显示;
   - `insar_export_provenance` → 真实台账(哈希、metrics 及 `reparsed_ok`);
   - `insar_read_log`(任选一步)→ 真实执行日志;
   - `/insar-mode strict` → 让模型试跑 `bash`,确认被 guard 拦截且给出 insar_* 引导话术;`/insar-mode free` 恢复。
4. ⚠️重型(**须用户明确批准后才执行**):新会话真实执行——`insar_create_session`(如 `ridgecrest-pi`)→ `insar_plan_run`("Ridgecrest 地震同震形变分析")→ `insar_execute_run`。预计分钟级、MintPy 满 CPU;等价基准命令为 `.venv\Scripts\python.exe scripts\real_ridgecrest.py`(同为重型,同需批准)。
5. 截图归档指引:pi TUI 全貌(侧栏+对话)、strict 拦截时刻、audited 状态行。截图是人工验收证据,不入库亦可,存 `workspace/` 下由用户处置。

## 验收

- 手册第 1-3 步全部走通(纯读回,无重型计算,不需批准)。
- `pytest` 与 `vitest` 不受影响(本 Phase 未改源码,跑一遍确认基线未破):

```powershell
cd E:\01所有项目\06定职讲师\00insaragent\pi-insar; npx vitest run
cd ..; .venv\Scripts\python.exe -m pytest -q
```

## git 提交

```powershell
git add scripts/insar-backend-real.ps1 docs/PI-REAL-SESSION.md
git commit -m "feat(scripts): 真实引擎后端启动器 + pi 真实数据走查手册(Ridgecrest audited 读回)"
```

## 常见坑与处置

- **8873 被占**(旧后端还在跑):`netstat -ano | findstr 8873` → 结束旧进程,或 `-Port 8874` 并 `$env:INSAR_API_BASE="http://127.0.0.1:8874"` 再起 pi。
- **`INSAR_HOME` 指错**:pi 里 `insar_list_sessions` 看不到 `real` 即为此症;确认脚本打印的 INSAR_HOME 是 `...\workspace\realtest`。
- **realtest 的 `-shm/-wal` 文件**:属 SQLite WAL 正常伴生,勿删;后端独占期间不要并行再起第二个后端指向同一 HOME(锁冲突)。
- **引擎探测失败**:`insar_env_probe` 工具可在 pi 内直接诊断(它读 `/api/env` 真实探测结果);检查 `INSAR_ENGINE_PREFIX` 与 conda env 是否完好。

## 完成标志

- [ ] `/api/health` 返回 ok(真实模式后端)
- [ ] pi 会话完成手册第 3 步全部读回项(audited、provenance、日志、strict 拦截)
- [ ] vitest/pytest 基线未破
- [ ] 本 Phase 已提交,`git status` 干净

→ 下一个文件:`13-phase04-insar-theme.md`
