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
