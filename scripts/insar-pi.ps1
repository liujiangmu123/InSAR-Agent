# insar-pi.ps1 — start pi with the InSAR agent loaded (Windows-native launcher).
# Resources (extension/skills/theme/APPEND_SYSTEM.md) are NOT passed as flags:
# they live under .pi/ (project auto-discovery), so terminal pi, pi Desktop and
# pi-web all load the same InSAR setup. This script only 1) self-heals the .pi
# junctions on a fresh clone, 2) health-checks the backend, 3) execs pi.
# Non-advanced on purpose: no [CmdletBinding()] / param(), so every token
# (including --insar-strict and -p) lands in $args and is forwarded to pi.
#
#   pwsh -NoProfile -File scripts/insar-pi.ps1
#   pwsh -NoProfile -File scripts/insar-pi.ps1 --insar-strict
#   pwsh -NoProfile -File scripts/insar-pi.ps1 --insar-session real
#   pwsh -NoProfile -File scripts/insar-pi.ps1 -p "plan a run"
#   pwsh -NoProfile -File scripts/insar-pi.ps1 --list-models
#
# Env: INSAR_API_BASE (default http://127.0.0.1:8873), INSAR_PI_SKIP_HEALTH=1
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot

# --- .pi 项目资源自愈(junction 不入库,新克隆第一次跑本脚本时补齐) ---
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

foreach ($required in @((Join-Path $RepoRoot ".pi\extensions\insar.ts"),
                        (Join-Path $RepoRoot "pi-insar\src\index.ts"))) {
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

# 资源全部走 .pi 自动发现;这里若再传 -e/--skill/--theme 会造成重复加载
Set-Location $RepoRoot
& pi @args
exit $LASTEXITCODE
