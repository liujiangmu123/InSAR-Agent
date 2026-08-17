# sync-pi-app-overlay.ps1 — 双向同步 desktop/pi-app-overlay ↔ pi-app 壳。
# 文件清单以 overlay 目录实际内容为准(不硬编码);根目录 README.md 是说明,不受管。
# 相对路径 1:1,例如 overlay/src/main/foo.ts ↔ $PiAppRoot/src/main/foo.ts。
#
#   pwsh -File scripts/sync-pi-app-overlay.ps1 -Check
#   pwsh -File scripts/sync-pi-app-overlay.ps1 -Pull
#   pwsh -File scripts/sync-pi-app-overlay.ps1 -Push
#   pwsh -File scripts/sync-pi-app-overlay.ps1 -Pull -Path src/foo.ts
#   pwsh -File scripts/sync-pi-app-overlay.ps1 -Pull -Path src/foo.ts -IncludeAllManaged
#   pwsh -File scripts/sync-pi-app-overlay.ps1 -Check -PiAppRoot E:\SoftApp\pi-app
#
# -Check: 逐字节比对(SHA256;文本亦按 UTF-8 文件字节)。不一致列出;无受管文件则成功。
# -Pull:  把壳中「overlay 已有的相对路径」拷回 overlay(壳缺失则跳过并警告)。
#         -Path 额外从壳拷入 overlay(用于扩清单);单独 -Path 不刷新其余受管文件。
#         -IncludeAllManaged: 在已有 -Path 时,同时刷新 overlay 已有的全部受管文件。
# -Push:  overlay → 壳,覆盖(新机器/升壳版本后复原补丁)。
#
# 默认 PiAppRoot=E:\SoftApp\pi-app,可用 -PiAppRoot 或 INSAR_PI_DESKTOP_ROOT。
# 不要杀 8873 / Electron;本脚本只做文件拷贝与比对。

[CmdletBinding()]
param(
  [switch]$Check,
  [switch]$Pull,
  [switch]$Push,
  [switch]$IncludeAllManaged,
  [string[]]$Path,
  [string]$PiAppRoot = $(if ($env:INSAR_PI_DESKTOP_ROOT) { $env:INSAR_PI_DESKTOP_ROOT } else { "E:\SoftApp\pi-app" })
)

$ErrorActionPreference = "Stop"
try {
  [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
  $OutputEncoding = [System.Text.UTF8Encoding]::new($false)
} catch { }
$RepoRoot = (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).Path
$OverlayRoot = Join-Path $RepoRoot "desktop\pi-app-overlay"

function ConvertTo-RelKey([string]$Rel) {
  return (($Rel -replace '\\', '/').Trim('/'))
}

function Test-IsManagedRel([string]$RelKey) {
  if ([string]::IsNullOrWhiteSpace($RelKey)) { return $false }
  if ($RelKey -eq 'README.md') { return $false }
  if ($RelKey -match '(^|/)\.\.(/|$)') { return $false }
  return $true
}

function Get-ManagedOverlayFiles {
  if (-not (Test-Path -LiteralPath $OverlayRoot -PathType Container)) {
    return @()
  }
  $overlayFull = (Resolve-Path -LiteralPath $OverlayRoot).Path
  $prefixLen = $overlayFull.Length
  $items = @()
  Get-ChildItem -LiteralPath $overlayFull -Recurse -File | ForEach-Object {
    $rel = $_.FullName.Substring($prefixLen).TrimStart('\', '/')
    $key = ConvertTo-RelKey $rel
    if (-not (Test-IsManagedRel $key)) { return }
    $items += [pscustomobject]@{
      Rel         = $key
      OverlayPath = $_.FullName
    }
  }
  return @($items | Sort-Object Rel)
}

function Get-ShellPath([string]$RelKey) {
  $parts = $RelKey -split '/'
  $p = $script:PiAppRootResolved
  foreach ($part in $parts) { $p = Join-Path $p $part }
  return $p
}

function Get-OverlayPath([string]$RelKey) {
  $parts = $RelKey -split '/'
  $p = $OverlayRoot
  foreach ($part in $parts) { $p = Join-Path $p $part }
  return $p
}

function Test-FilesByteEqual([string]$Left, [string]$Right) {
  $ha = (Get-FileHash -LiteralPath $Left -Algorithm SHA256).Hash
  $hb = (Get-FileHash -LiteralPath $Right -Algorithm SHA256).Hash
  return $ha -eq $hb
}

function Copy-ManagedFile([string]$From, [string]$To) {
  $destDir = Split-Path -Parent $To
  if (-not (Test-Path -LiteralPath $destDir -PathType Container)) {
    New-Item -ItemType Directory -Force -Path $destDir | Out-Null
  }
  Copy-Item -LiteralPath $From -Destination $To -Force
}

$modeCount = @($Check.IsPresent, $Pull.IsPresent, $Push.IsPresent) | Where-Object { $_ } | Measure-Object | Select-Object -ExpandProperty Count
if ($modeCount -ne 1) {
  Write-Error "sync-pi-app-overlay: 必须且只能指定 -Check、-Pull、-Push 之一"
  exit 1
}
if ($IncludeAllManaged -and -not $Pull) {
  Write-Error "sync-pi-app-overlay: -IncludeAllManaged 只能与 -Pull 连用"
  exit 1
}
if ($Path -and -not $Pull) {
  Write-Error "sync-pi-app-overlay: -Path 只能与 -Pull 连用"
  exit 1
}

if (-not (Test-Path -LiteralPath $PiAppRoot)) {
  Write-Error "sync-pi-app-overlay: 找不到 pi Desktop 源码: $PiAppRoot"
  exit 1
}
$script:PiAppRootResolved = (Resolve-Path -LiteralPath $PiAppRoot).Path
if (-not (Test-Path -LiteralPath (Join-Path $script:PiAppRootResolved "package.json"))) {
  Write-Error "sync-pi-app-overlay: 不是 pi-app 根(缺 package.json): $($script:PiAppRootResolved)"
  exit 1
}

$managed = @(Get-ManagedOverlayFiles)

if ($Check) {
  if ($managed.Count -eq 0) {
    Write-Host "无受管文件,一致"
    exit 0
  }
  $bad = @()
  foreach ($item in $managed) {
    $shellPath = Get-ShellPath $item.Rel
    if (-not (Test-Path -LiteralPath $shellPath -PathType Leaf)) {
      Write-Host "不一致  $($item.Rel)  (壳缺失)"
      $bad += $item.Rel
      continue
    }
    if (Test-FilesByteEqual $item.OverlayPath $shellPath) {
      Write-Host "一致    $($item.Rel)"
    } else {
      Write-Host "不一致  $($item.Rel)  (字节不同)"
      $bad += $item.Rel
    }
  }
  if ($bad.Count -gt 0) {
    Write-Host "$($bad.Count) 个文件不一致, $($managed.Count - $bad.Count) 个文件一致"
    exit 1
  }
  Write-Host "$($managed.Count) 个文件一致"
  exit 0
}

if ($Push) {
  if ($managed.Count -eq 0) {
    Write-Host "无受管文件,未拷贝"
    exit 0
  }
  $n = 0
  foreach ($item in $managed) {
    $shellPath = Get-ShellPath $item.Rel
    Copy-ManagedFile $item.OverlayPath $shellPath
    Write-Host "Push  $($item.Rel)"
    $n++
  }
  Write-Host "已 Push $n 个文件 → $($script:PiAppRootResolved)"
  exit 0
}

# -Pull
$relSet = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
if (-not $Path -or $IncludeAllManaged) {
  foreach ($item in $managed) { [void]$relSet.Add($item.Rel) }
}
if ($Path) {
  foreach ($raw in $Path) {
    $key = ConvertTo-RelKey $raw
    if (-not (Test-IsManagedRel $key)) {
      Write-Error "sync-pi-app-overlay: 非法 -Path: $raw"
      exit 1
    }
    [void]$relSet.Add($key)
  }
}

if ($relSet.Count -eq 0) {
  Write-Host "无受管文件,未拷贝"
  exit 0
}

$copied = 0
$skipped = 0
$sorted = @($relSet) | Sort-Object
foreach ($rel in $sorted) {
  $shellPath = Get-ShellPath $rel
  $overlayPath = Get-OverlayPath $rel
  if (-not (Test-Path -LiteralPath $shellPath -PathType Leaf)) {
    Write-Host "跳过  $rel  (壳缺失)"
    $skipped++
    continue
  }
  Copy-ManagedFile $shellPath $overlayPath
  Write-Host "Pull  $rel"
  $copied++
}
Write-Host "已 Pull $copied 个文件 → $OverlayRoot"
if ($skipped -gt 0) {
  Write-Host "跳过 $skipped 个(壳中不存在)"
}
exit 0
