# package-pi-desktop.ps1 — 从源码自打包 pi Desktop(NSIS Setup + portable)。
# 默认只打印步骤与壳根,不启动 electron-builder(避免 VS18 C1001 / 长时间占 CPU)。
# 真正打包须显式 -Execute,且一次只跑这一件事。
#
#   pwsh -File scripts/package-pi-desktop.ps1
#   pwsh -File scripts/package-pi-desktop.ps1 -Execute
#   pwsh -File scripts/package-pi-desktop.ps1 -Execute -PiAppRoot E:\SoftApp\pi-app
#
# 不要杀已在跑的 8873 / Electron;打包用另一份 node_modules 编译,建议先关掉 npm run dev。
# 不下载 justhil 现成安装包。不改 productName(避免 userData 漂移)。
param(
  [string]$PiAppRoot = $(if ($env:INSAR_PI_DESKTOP_ROOT) { $env:INSAR_PI_DESKTOP_ROOT } else { "E:\SoftApp\pi-app" }),
  [switch]$Execute,
  [switch]$SkipFixSqlite
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).Path
$FixSqlite = Join-Path $PSScriptRoot "fix-pi-desktop-sqlite.ps1"
$RecordDir = Join-Path $RepoRoot "pi-insar\docs"
$Stamp = Get-Date -Format "yyyyMMddTHHmmss"

if (-not (Test-Path (Join-Path $PiAppRoot "package.json"))) {
  Write-Error "package-pi-desktop: 找不到 pi Desktop 源码: $PiAppRoot"
  exit 1
}
$PiAppRoot = (Resolve-Path -LiteralPath $PiAppRoot).Path
$Dist = Join-Path $PiAppRoot "dist"

Write-Host "package-pi-desktop: PiAppRoot=$PiAppRoot"
Write-Host "package-pi-desktop: dist=$Dist"
Write-Host "步骤: fix-pi-desktop-sqlite.ps1 → npm run package:win → 记录 dist 体积与 SHA256"

if (-not $Execute) {
  Write-Host "dry-run: 未传 -Execute,不调用 electron-builder。"
  Write-Host "确认已关闭 npm run dev 后执行:"
  Write-Host "  pwsh -NoProfile -File scripts\package-pi-desktop.ps1 -Execute"
  exit 0
}

if (-not $SkipFixSqlite) {
  Write-Host "package-pi-desktop: fix sqlite (VS18 C1001 规避)"
  & $FixSqlite -PiAppRoot $PiAppRoot
  if ($LASTEXITCODE -ne 0) {
    Write-Error "package-pi-desktop: fix-pi-desktop-sqlite 失败 (exit $LASTEXITCODE)"
    exit $LASTEXITCODE
  }
}

Push-Location $PiAppRoot
try {
  Write-Host "package-pi-desktop: npm run package:win"
  & npm run package:win
  if ($LASTEXITCODE -ne 0) {
    Write-Error "package-pi-desktop: package:win 失败 (exit $LASTEXITCODE)"
    exit $LASTEXITCODE
  }
} finally {
  Pop-Location
}

if (-not (Test-Path -LiteralPath $Dist -PathType Container)) {
  Write-Error "package-pi-desktop: 打包结束但没有 dist/: $Dist"
  exit 1
}

$lines = @("# Desktop package record $Stamp", "", "PiAppRoot: $PiAppRoot", "")
Get-ChildItem -LiteralPath $Dist -File | ForEach-Object {
  $hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
  $row = ("{0}`t{1} bytes`tSHA256 {2}" -f $_.Name, $_.Length, $hash)
  Write-Host $row
  $lines += $row
}
$record = Join-Path $RecordDir "desktop-package-$Stamp.txt"
$lines | Set-Content -LiteralPath $record -Encoding utf8
Write-Host "package-pi-desktop: 记录 $record"
exit 0
