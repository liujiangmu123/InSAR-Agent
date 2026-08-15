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
