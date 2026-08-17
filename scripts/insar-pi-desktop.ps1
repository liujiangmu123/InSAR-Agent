# insar-pi-desktop.ps1 — 从源码启动 pi Desktop(justhil/pi-app),工作区=本仓库。
# 不做现成安装包下载:壳在本地源码树构建/开发;InSAR 扩展与 adapter 走项目 .pi/。
#
# 前置:
#   1) 已克隆: git clone --branch v0.5.7 https://github.com/justhil/pi-app.git <PiAppRoot>
#   2) 在 PiAppRoot 里 npm install 过一次(本脚本不跑完整 npm install)
#   3) 后端已起: pwsh scripts/insar-backend-real.ps1
#
#   pwsh -NoProfile -File scripts/insar-pi-desktop.ps1
#   pwsh -NoProfile -File scripts/insar-pi-desktop.ps1 -PiAppRoot E:\SoftApp\pi-app
#   $env:INSAR_PI_DESKTOP_ROOT = "E:\SoftApp\pi-app"; pwsh -File scripts/insar-pi-desktop.ps1
#   pwsh -File scripts/insar-pi-desktop.ps1 -- --inspect   # 额外参数原样转给 npm run dev
#
# Env: INSAR_API_BASE (default http://127.0.0.1:8873), INSAR_PI_SKIP_HEALTH=1,
#      INSAR_PI_DESKTOP_ROOT / -PiAppRoot, ELECTRON_MIRROR (缺 electron.exe 时)
#
# 保持 param,不吞掉用户多余参数($args → npm run dev)。非交互,无 Read-Host。
# 不要 exec insar-pi.ps1 --list-models;不要下载 justhil 安装包 exe;不要 npm run package。
param(
  [string]$PiAppRoot = $(if ($env:INSAR_PI_DESKTOP_ROOT) { $env:INSAR_PI_DESKTOP_ROOT } else { "E:\SoftApp\pi-app" })
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).Path
$FixSqlite = Join-Path $PSScriptRoot "fix-pi-desktop-sqlite.ps1"

if (-not (Test-Path (Join-Path $PiAppRoot "package.json"))) {
  Write-Error @"
insar-pi-desktop: 找不到 pi Desktop 源码: $PiAppRoot
请先克隆并安装依赖(源码开发,不是下安装包):
  git clone --depth 1 --branch v0.5.7 https://github.com/justhil/pi-app.git $PiAppRoot
  cd $PiAppRoot; npm install
"@
  exit 1
}

$PiAppRoot = (Resolve-Path -LiteralPath $PiAppRoot).Path
$nodeModules = Join-Path $PiAppRoot "node_modules"
if (-not (Test-Path -LiteralPath $nodeModules -PathType Container)) {
  Write-Error @"
insar-pi-desktop: 缺少 node_modules: $nodeModules
请先在 PiAppRoot 安装依赖(本启动器不跑完整 npm install,以免过重并踩 VS18 C1001):
  cd $PiAppRoot
  npm install --registry=https://registry.npmjs.org/
"@
  exit 1
}

# .pi junction 自愈(与 CLI 启动器同一套规则,不启动 pi)
function Ensure-Junction([string]$Link, [string]$Target) {
  if (-not (Test-Path -LiteralPath $Target)) { return }
  $targetFull = (Resolve-Path -LiteralPath $Target).Path
  $need = $true
  if (Test-Path -LiteralPath $Link) {
    try {
      $item = Get-Item -LiteralPath $Link -Force
      $isReparse = [bool]($item.Attributes -band [IO.FileAttributes]::ReparsePoint)
      if (-not $isReparse) { return }
      $current = $item.Target
      if ($current -is [array]) { $current = $current[0] }
      if ($current) {
        $curFull = (Resolve-Path -LiteralPath $current).Path
        if ($curFull -eq $targetFull) { $need = $false }
      }
    } catch { $need = $true }
    if ($need) {
      [System.IO.Directory]::Delete($Link)
    }
  }
  if ($need) {
    New-Item -ItemType Junction -Path $Link -Target $targetFull | Out-Null
  }
}

$SkillRoots = @(
  (Join-Path $RepoRoot "pi-insar\skills"),
  (Join-Path $RepoRoot "skills"),
  (Join-Path $RepoRoot "src\insar_agent\registry\scenario_packs")
)
$PiSkills = Join-Path $RepoRoot ".pi\skills"
New-Item -ItemType Directory -Force -Path $PiSkills | Out-Null
foreach ($root in $SkillRoots) {
  if (-not (Test-Path $root)) { continue }
  foreach ($d in Get-ChildItem $root -Directory) {
    Ensure-Junction (Join-Path $PiSkills $d.Name) $d.FullName
  }
}
$PiThemes = Join-Path $RepoRoot ".pi\themes"
Ensure-Junction $PiThemes (Join-Path $RepoRoot "pi-insar\themes")
$PiAppend = Join-Path $RepoRoot ".pi\APPEND_SYSTEM.md"
$AppendSrc = Join-Path $RepoRoot "pi-insar\APPEND_SYSTEM.md"
if (-not (Test-Path $PiAppend)) {
  New-Item -ItemType HardLink -Path $PiAppend -Target $AppendSrc | Out-Null
}

# 写入进程环境,随后 npm/Electron 才能继承(不只是当前脚本作用域)
if (-not $env:INSAR_API_BASE) { $env:INSAR_API_BASE = "http://127.0.0.1:8873" }
[Environment]::SetEnvironmentVariable("INSAR_API_BASE", $env:INSAR_API_BASE, "Process")
[Environment]::SetEnvironmentVariable("INSAR_DESKTOP_PROJECT", $RepoRoot, "Process")

if ($env:INSAR_PI_SKIP_HEALTH -ne "1") {
  try {
    Invoke-WebRequest -Uri "$($env:INSAR_API_BASE)/api/health" -UseBasicParsing -TimeoutSec 5 | Out-Null
  } catch {
    Write-Host "insar-pi-desktop: backend not answering at $($env:INSAR_API_BASE)/api/health"
    Write-Host "Start: pwsh scripts/insar-backend-real.ps1   (or set INSAR_PI_SKIP_HEALTH=1)"
    exit 1
  }
}

$electronExe = Join-Path $PiAppRoot "node_modules\electron\dist\electron.exe"
$electronInstall = Join-Path $PiAppRoot "node_modules\electron\install.js"
if (-not (Test-Path $electronExe)) {
  if (-not (Test-Path $electronInstall)) {
    Write-Error @"
insar-pi-desktop: 缺少 Electron 包: $electronInstall
请先在 PiAppRoot 安装依赖(本启动器不跑完整 npm install):
  cd $PiAppRoot
  npm install --registry=https://registry.npmjs.org/
"@
    exit 1
  }
  if (-not $env:ELECTRON_MIRROR) {
    $env:ELECTRON_MIRROR = "https://npmmirror.com/mirrors/electron/"
  }
  [Environment]::SetEnvironmentVariable("ELECTRON_MIRROR", $env:ELECTRON_MIRROR, "Process")
  Write-Host "insar-pi-desktop: 缺少 electron.exe,运行 node node_modules\electron\install.js (ELECTRON_MIRROR=$($env:ELECTRON_MIRROR))"
  Push-Location $PiAppRoot
  try {
    & node "node_modules\electron\install.js"
    if ($LASTEXITCODE -ne 0) {
      Write-Error "insar-pi-desktop: electron install.js 失败 (exit $LASTEXITCODE)"
      exit $LASTEXITCODE
    }
  } finally {
    Pop-Location
  }
  if (-not (Test-Path $electronExe)) {
    Write-Error "insar-pi-desktop: install.js 结束后仍无 $electronExe"
    exit 1
  }
}

$sqliteNode = Join-Path $PiAppRoot "node_modules\better-sqlite3\build\Release\better_sqlite3.node"
if (-not (Test-Path $sqliteNode)) {
  Write-Host "insar-pi-desktop: 缺少 better_sqlite3.node,调用 $FixSqlite"
  & $FixSqlite -PiAppRoot $PiAppRoot
  if ($LASTEXITCODE -ne 0) {
    Write-Error "insar-pi-desktop: fix-pi-desktop-sqlite 失败 (exit $LASTEXITCODE)"
    exit $LASTEXITCODE
  }
  if (-not (Test-Path $sqliteNode)) {
    Write-Error "insar-pi-desktop: 修复后仍无 $sqliteNode"
    exit 1
  }
}

# 开发态:npm run dev。启动器写入 INSAR_DESKTOP_PROJECT=本仓库根,Desktop 冷启动直接打开该工作区
# (加载 .pi/extensions、.pi/desktop/adapters、workspace/llm.json 的 insar-llm)。
Write-Host "insar-pi-desktop: PiAppRoot=$PiAppRoot"
Write-Host "insar-pi-desktop: INSAR_DESKTOP_PROJECT=$RepoRoot"
Write-Host "insar-pi-desktop: INSAR_API_BASE=$($env:INSAR_API_BASE)"
Set-Location $PiAppRoot
# 再写一次进程环境,确保随后的 npm.cmd → electron 子进程能继承
[Environment]::SetEnvironmentVariable("INSAR_API_BASE", $env:INSAR_API_BASE, "Process")
[Environment]::SetEnvironmentVariable("INSAR_DESKTOP_PROJECT", $RepoRoot, "Process")
if ($args.Count -gt 0) {
  & npm run dev -- @args
} else {
  & npm run dev
}
exit $LASTEXITCODE
