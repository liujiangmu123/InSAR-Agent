# insar-pi-desktop.ps1 — 从源码启动 pi Desktop(justhil/pi-app),工作区=本仓库。
# 不做现成安装包下载:壳在本地源码树构建/开发;InSAR 扩展与 adapter 走项目 .pi/。
#
# 前置:
#   1) 已克隆: git clone --branch v0.5.7 https://github.com/justhil/pi-app.git <PiAppRoot>
#   2) 在 PiAppRoot 里 npm install 过一次
#   3) 后端已起: pwsh scripts/insar-backend-real.ps1
#
#   pwsh -NoProfile -File scripts/insar-pi-desktop.ps1
#   pwsh -NoProfile -File scripts/insar-pi-desktop.ps1 -PiAppRoot E:\SoftApp\pi-app
#   $env:INSAR_PI_DESKTOP_ROOT = "E:\SoftApp\pi-app"; pwsh -File scripts/insar-pi-desktop.ps1
#
# Env: INSAR_API_BASE (default http://127.0.0.1:8873), INSAR_PI_SKIP_HEALTH=1,
#      INSAR_PI_DESKTOP_ROOT / -PiAppRoot
param(
  [string]$PiAppRoot = $(if ($env:INSAR_PI_DESKTOP_ROOT) { $env:INSAR_PI_DESKTOP_ROOT } else { "E:\SoftApp\pi-app" })
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot

if (-not (Test-Path (Join-Path $PiAppRoot "package.json"))) {
  Write-Error @"
insar-pi-desktop: 找不到 pi Desktop 源码: $PiAppRoot
请先克隆并安装依赖(源码开发,不是下安装包):
  git clone --depth 1 --branch v0.5.7 https://github.com/justhil/pi-app.git $PiAppRoot
  cd $PiAppRoot; npm install
"@
  exit 1
}

# .pi junction 自愈(与 CLI 启动器同一套规则,不启动 pi)
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
    $link = Join-Path $PiSkills $d.Name
    if (-not (Test-Path $link)) {
      New-Item -ItemType Junction -Path $link -Target $d.FullName | Out-Null
    }
  }
}
$PiThemes = Join-Path $RepoRoot ".pi\themes"
if (-not (Test-Path $PiThemes)) {
  New-Item -ItemType Junction -Path $PiThemes -Target (Join-Path $RepoRoot "pi-insar\themes") | Out-Null
}
$PiAppend = Join-Path $RepoRoot ".pi\APPEND_SYSTEM.md"
if (-not (Test-Path $PiAppend)) {
  New-Item -ItemType HardLink -Path $PiAppend -Target (Join-Path $RepoRoot "pi-insar\APPEND_SYSTEM.md") | Out-Null
}

if (-not $env:INSAR_API_BASE) { $env:INSAR_API_BASE = "http://127.0.0.1:8873" }
if ($env:INSAR_PI_SKIP_HEALTH -ne "1") {
  try {
    Invoke-WebRequest -Uri "$($env:INSAR_API_BASE)/api/health" -UseBasicParsing -TimeoutSec 5 | Out-Null
  } catch {
    Write-Host "insar-pi-desktop: backend not answering at $($env:INSAR_API_BASE)/api/health"
    Write-Host "Start: pwsh scripts/insar-backend-real.ps1   (or set INSAR_PI_SKIP_HEALTH=1)"
    exit 1
  }
}

# 开发态:npm run dev。打开后请在 Desktop 里「打开文件夹」选本仓库根,
# 以便加载 .pi/extensions 与 .pi/desktop/adapters/insar.adapter.json。
Write-Host "insar-pi-desktop: PiAppRoot=$PiAppRoot"
Write-Host "insar-pi-desktop: open workspace in UI: $RepoRoot"
Write-Host "insar-pi-desktop: INSAR_API_BASE=$($env:INSAR_API_BASE)"
Set-Location $PiAppRoot
npm run dev
exit $LASTEXITCODE
