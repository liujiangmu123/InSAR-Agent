# 绕过 VS 18 BuildTools 编译 better-sqlite3 时的 C1001(LTCG/Ox 内部错误)。
# 仅改 PiAppRoot/node_modules/better-sqlite3 的 gyp,然后 electron-rebuild。
#   pwsh -File scripts/fix-pi-desktop-sqlite.ps1
#   pwsh -File scripts/fix-pi-desktop-sqlite.ps1 -PiAppRoot E:\SoftApp\pi-app
param(
  [string]$PiAppRoot = $(if ($env:INSAR_PI_DESKTOP_ROOT) { $env:INSAR_PI_DESKTOP_ROOT } else { "E:\SoftApp\pi-app" })
)
$ErrorActionPreference = "Stop"
$mod = Join-Path $PiAppRoot "node_modules\better-sqlite3"
if (-not (Test-Path (Join-Path $mod "binding.gyp"))) {
  Write-Error "fix-pi-desktop-sqlite: 找不到 $mod (先在 PiAppRoot npm install --ignore-scripts)"
  exit 1
}

$marker = Join-Path $mod ".insar-vs18-noltcg"
if (-not (Test-Path $marker)) {
  $gypi = Join-Path $mod "deps\common.gypi"
  $raw = Get-Content $gypi -Raw
  if ($raw -notmatch "WholeProgramOptimization") {
    $inject = @"
        'msvs_settings': {
          'VCCLCompilerTool': {
            'Optimization': 0,
            'WholeProgramOptimization': 'false',
            'AdditionalOptions': ['/Od', '/GL-'],
          },
          'VCLinkerTool': { 'LinkTimeCodeGeneration': 0 },
        },
"@
    $raw = $raw -replace "('NDEBUG',\s*\],)", "`$1`n$inject"
    Set-Content -Path $gypi -Value $raw -NoNewline
  }
  $gyp = Join-Path $mod "binding.gyp"
  $b = Get-Content $gyp -Raw
  if ($b -notmatch "/GL-") {
    $b = $b -replace "'/std:c\+\+20',", "'/std:c++20',`n            '/Od',`n            '/GL-',"
    $b = $b -replace "('VCCLCompilerTool': \{)", "`$1`n          'Optimization': 0,`n          'WholeProgramOptimization': 'false',"
    if ($b -notmatch "VCLinkerTool") {
      $b = $b -replace "('msvs_settings': \{)", "`$1`n        'VCLinkerTool': { 'LinkTimeCodeGeneration': 0 },"
    }
    Set-Content -Path $gyp -Value $b -NoNewline
  }
  $sql = Join-Path $mod "deps\sqlite3.gyp"
  $s = Get-Content $sql -Raw
  if ($s -notmatch "/GL-") {
    $s = $s -replace "('RuntimeLibrary': 0 \}, # static release)", @"
'RuntimeLibrary': 0,
              'Optimization': 0,
              'WholeProgramOptimization': 'false',
              'AdditionalOptions': ['/Od', '/GL-'],
            },
            'VCLinkerTool': { 'LinkTimeCodeGeneration': 0 },
"@
    Set-Content -Path $sql -Value $s -NoNewline
  }
  Set-Content $marker "vs18-noltcg"
}

$build = Join-Path $mod "build"
if (Test-Path $build) { Remove-Item -Recurse -Force $build }
Set-Location $PiAppRoot
npx --yes @electron/rebuild -f -w better-sqlite3
exit $LASTEXITCODE
