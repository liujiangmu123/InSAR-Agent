# Phase 01 · Windows 基线固化(P0)

> **前置**:已读 `01-execution-rules.md`;无其他前置(本 Phase 是第一个)。
> **目标**:pi 在本机可运行;vitest 在 Windows 干净全绿(无 EBUSY 噪音);PowerShell 一键启动;把工作区里 3 个未提交的 Windows 适配改动一并收口提交。
> **涉及文件**:修改 `pi-insar/test/backend.globalSetup.ts`、`pi-insar/test/skills.test.ts`、`pi-insar/README.md`;新建 `scripts/insar-pi.ps1`。

## 步骤 0.1 安装 pi 与前置检查

```powershell
node --version                                # 须 ≥ 22.19
npm i -g @earendil-works/pi-coding-agent@0.84.2
pi --version                                  # 应输出 0.84.2
bash --version                                # Git Bash 须在位(pi 的 bash 工具依赖)
```

常见坑:公司代理导致 npm 装不动 → `npm config get registry` 检查;`pi` 不在 PATH → 重开终端让 npm 全局 bin 生效。

## 步骤 0.2 修改 `E:\01所有项目\06定职讲师\00insaragent\pi-insar\test\backend.globalSetup.ts`

改动位置与理由:文件头常量区(现第 9-16 行)与 `teardown()`(现第 80-93 行)。现状默认 `PYTHON = "/workspace/.venv/bin/python"`、`CWD = "/workspace"` 是 devcontainer 遗产,Windows 上必须靠环境变量救;teardown 直接 `rm` 临时目录,Windows 上 python 进程退出与 SQLite `-wal/-shm` 句柄释放存在竞态窗口,报 EBUSY。

旧(第 9-16 行):

```ts
import { spawn, type ChildProcess } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

const PYTHON = process.env.INSAR_TEST_PYTHON ?? "/workspace/.venv/bin/python";
const CWD = process.env.INSAR_TEST_CWD ?? "/workspace";
const PORT = Number(process.env.INSAR_TEST_PORT ?? 8899);
```

新(替换为,补两个工具函数):

```ts
import { spawn, type ChildProcess } from "node:child_process";
import { existsSync } from "node:fs";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

/** repo 根 = 本文件(pi-insar/test/)上两级 —— 平台无关,替代硬编码 /workspace。 */
const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");

function defaultPython(cwd: string): string {
  const venv =
    process.platform === "win32"
      ? join(cwd, ".venv", "Scripts", "python.exe")
      : join(cwd, ".venv", "bin", "python");
  if (existsSync(venv)) return venv;
  return process.platform === "win32" ? "python" : "python3";
}

const CWD = process.env.INSAR_TEST_CWD ?? REPO_ROOT;
const PYTHON = process.env.INSAR_TEST_PYTHON ?? defaultPython(CWD);
const PORT = Number(process.env.INSAR_TEST_PORT ?? 8899);
```

在 `teardown()` 前新增(EBUSY/EPERM 退避重试;仅重试这三类,其他错误照抛):

```ts
/** Windows: python 退出后 SQLite -wal/-shm 句柄释放有竞态窗口,退避重试。 */
async function rmWithRetry(target: string, attempts = 10): Promise<void> {
  for (let attempt = 1; ; attempt += 1) {
    try {
      await rm(target, { recursive: true, force: true });
      return;
    } catch (error) {
      const code = (error as NodeJS.ErrnoException).code;
      if (attempt >= attempts || (code !== "EBUSY" && code !== "EPERM" && code !== "ENOTEMPTY")) {
        throw error;
      }
      await new Promise((wake) => setTimeout(wake, 200 * attempt));
    }
  }
}
```

teardown 内旧 `await rm(home, { recursive: true, force: true });` → 新 `await rmWithRetry(home);`。其余(waitForHealth、spawn env、`INSAR_ALLOW_SIMULATED: "1"`)一律不动。

## 步骤 0.3 新建 `E:\01所有项目\06定职讲师\00insaragent\scripts\insar-pi.ps1`

用途:bash 启动器的 PowerShell 等价物(Windows 首选入口)。结构:参数透传数组 → 路径常量 → 存在性检查 → 健康检查 → 组装增量参数 → `& pi`。与 bash 版同一哲学:**只传增量参数**(-e / --skill / --append-system-prompt / --theme),绝不传 `--system-prompt`、`--no-extensions`、`--no-skills`、`--no-builtin-tools`、`--tools`。注意:头注释不要写出这些禁传旗标原文(0.4 的 "never passes" 断言直接扫全文)。可直接落盘:

```powershell
# insar-pi.ps1 — start pi with the InSAR agent loaded (Windows-native launcher).
# Mirrors scripts/insar-pi (bash). Everything passed is *additive*; graduated
# freedom is enforced per tool call by the extension guard, not by crippling pi.
#
#   pwsh scripts/insar-pi.ps1                      # interactive, free mode
#   pwsh scripts/insar-pi.ps1 --insar-strict       # strict reproducible mode
#   pwsh scripts/insar-pi.ps1 -p "plan a run"      # any pi flag passes through
#
# Env: INSAR_API_BASE (default http://127.0.0.1:8873), INSAR_PI_SKIP_HEALTH=1
[CmdletBinding()]
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$PiArgs)
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$Extension = Join-Path $RepoRoot "pi-insar\src\index.ts"
$AppendSystem = Join-Path $RepoRoot "pi-insar\APPEND_SYSTEM.md"
$Theme = Join-Path $RepoRoot "pi-insar\themes\insar-dark.json"   # Phase 04 落地前不存在,存在才传
$SkillRoots = @(
  (Join-Path $RepoRoot "pi-insar\skills\00-insar-agent"),
  (Join-Path $RepoRoot "skills"),
  (Join-Path $RepoRoot "src\insar_agent\registry\scenario_packs")
)

foreach ($required in @($Extension, $AppendSystem)) {
  if (-not (Test-Path $required)) { Write-Error "insar-pi: missing $required"; exit 1 }
}
if (-not (Get-Command pi -ErrorAction SilentlyContinue)) {
  Write-Error "insar-pi: pi is not on PATH. Install: npm i -g @earendil-works/pi-coding-agent@0.84.2"
  exit 1
}

if (-not $env:INSAR_API_BASE) { $env:INSAR_API_BASE = "http://127.0.0.1:8873" }
if ($env:INSAR_PI_SKIP_HEALTH -ne "1") {
  try {
    Invoke-WebRequest -Uri "$($env:INSAR_API_BASE)/api/health" -UseBasicParsing -TimeoutSec 5 | Out-Null
  } catch {
    Write-Host "insar-pi: backend not answering at $($env:INSAR_API_BASE)/api/health"
    Write-Host "Start it from ${RepoRoot}:  pwsh scripts/insar-backend-real.ps1   (or set INSAR_PI_SKIP_HEALTH=1)"
    exit 1
  }
}

$Args = @("-e", $Extension)
foreach ($root in $SkillRoots) { if (Test-Path $root) { $Args += @("--skill", $root) } }
$Args += @("--append-system-prompt", $AppendSystem)
if (Test-Path $Theme) { $Args += @("--theme", $Theme) }

& pi @Args @PiArgs
exit $LASTEXITCODE
```

## 步骤 0.4 修改 `E:\01所有项目\06定职讲师\00insaragent\pi-insar\test\skills.test.ts`

位置:文件顶部路径常量区(第 25 行 `LAUNCHER` 定义处,既有常量为 `PACKAGE_DIR`/`REPO_ROOT`/`LAUNCHER`)与末尾 launcher describe 之后。新增 ps1 启动器的契约断言(与 bash 版同款语义):

```ts
const LAUNCHER_PS = join(REPO_ROOT, "scripts", "insar-pi.ps1");   // 常量区,紧邻既有 LAUNCHER

describe("scripts/insar-pi.ps1 launcher (Windows)", () => {
  const text = readFileSync(LAUNCHER_PS, "utf8");

  it("loads the same extension, skills and system prompt as the bash launcher", () => {
    expect(text).toContain("pi-insar\\src\\index.ts");
    expect(text).toContain("pi-insar\\APPEND_SYSTEM.md");
    expect(text).toContain("pi-insar\\skills\\00-insar-agent");
    expect(text).toContain("src\\insar_agent\\registry\\scenario_packs");
    expect(text).toContain("--append-system-prompt");
  });

  it("never passes capability-removing flags", () => {
    for (const flag of ["--system-prompt ", "--no-extensions", "--no-skills", "--no-builtin-tools", "--tools "]) {
      expect(text).not.toContain(flag);
    }
  });
});
```

注意:断言字符串用 `\\`(ps1 里是单反斜杠,TS 源码里要转义)。

## 步骤 0.5 修改 `E:\01所有项目\06定职讲师\00insaragent\pi-insar\README.md`

位置:既有"Windows 本地开发"环境变量小节。把 bash `export` 语法替换为 PowerShell 语法,并写明默认值已平台感知(0.2 改后通常不需要设):

```powershell
# 仅当 venv 不在仓库根 .venv 时才需要:
$env:INSAR_TEST_PYTHON = "E:\01所有项目\06定职讲师\00insaragent\.venv\Scripts\python.exe"
$env:INSAR_TEST_CWD = "E:\01所有项目\06定职讲师\00insaragent"
```

同节补一行:Windows 启动入口是 `pwsh scripts/insar-pi.ps1`(Git Bash 用户仍可用 `scripts/insar-pi`)。

## 验收(全走测试与真实命令)

```powershell
cd E:\01所有项目\06定职讲师\00insaragent\pi-insar
npx tsc --noEmit                    # 0 error
npx vitest run                      # 全绿,且结束后无 "EBUSY: resource busy" 输出
pi --version                        # 0.84.2
cd ..
pwsh scripts/insar-pi.ps1           # 后端未起 → 预期打印 backend not answering 并退出码 1
```

## git 提交

```powershell
git add pi-insar/test/backend.globalSetup.ts pi-insar/test/skills.test.ts pi-insar/README.md scripts/insar-pi.ps1
git commit -m "fix(pi-insar): Windows 基线 —— 测试基建平台感知 + EBUSY 退避 + PowerShell 启动器"
```

## 常见坑与处置

- **8899 被占**:`netstat -ano | findstr 8899` 找占用;临时改 `$env:INSAR_TEST_PORT="8901"` 再跑 vitest。
- **EBUSY 仍偶发**:重试上限 10 次×递增 200ms ≈ 11s,足够;若还失败说明有残留 python 进程,`Get-Process python | Stop-Process` 后重跑。
- **`pwsh` 不存在**:用 `powershell -File scripts/insar-pi.ps1`(脚本兼容 5.1)。

## 完成标志

- [ ] `tsc`/`vitest` 全绿且无 EBUSY 噪音
- [ ] `pi --version` = 0.84.2
- [ ] ps1 启动器"后端未起"报错路径正确(退出码 1)
- [ ] 本 Phase 已提交,`git status` 干净

→ 下一个文件:`11-phase02-llm-provider.md`
