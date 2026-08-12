<#
冻结产物冒烟验证:无窗后台启动 insar-backend.exe → 轮询 /api/health → 立刻结束进程。

用法:
  powershell -ExecutionPolicy Bypass -File desktop\backend-bundle\smoke_test.ps1 [-Port 18873]

退出码:0 = 健康检查 200;非 0 = 失败(排查 smoke-backend.err.log)。
数据目录用本地 .smoke-home(一次性,gitignore),不污染真实 workspace。
#>
[CmdletBinding()]
param(
    [int]$Port = 18873,
    [int]$TimeoutSec = 30
)

$ErrorActionPreference = "Stop"
$BundleDir = $PSScriptRoot
$Exe = Join-Path $BundleDir "dist\insar-backend\insar-backend.exe"
if (-not (Test-Path $Exe)) {
    throw ("产物不存在,先运行 build_backend.ps1:{0}" -f $Exe)
}

$SmokeHome = Join-Path $BundleDir ".smoke-home"
if (Test-Path $SmokeHome) { Remove-Item -Recurse -Force $SmokeHome }
$OutLog = Join-Path $BundleDir "smoke-backend.log"
$ErrLog = Join-Path $BundleDir "smoke-backend.err.log"

$prevPort = $env:INSAR_PORT
$prevHome = $env:INSAR_HOME
$env:INSAR_PORT = "$Port"
$env:INSAR_HOME = $SmokeHome

$proc = $null
$status = ""
try {
    # console=False 的 exe 本就无窗;-WindowStyle Hidden 双保险,输出重定向留证据
    $proc = Start-Process -FilePath $Exe -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $OutLog -RedirectStandardError $ErrLog
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if ($proc.HasExited) {
            throw ("后端提前退出(exit={0}),详见 {1}" -f $proc.ExitCode, $ErrLog)
        }
        try {
            $resp = Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 `
                -Uri ("http://127.0.0.1:{0}/api/health" -f $Port)
            if ($resp.StatusCode -eq 200) { $status = $resp.Content; break }
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
} finally {
    if ($proc -and -not $proc.HasExited) { Stop-Process -Id $proc.Id -Force }
    $env:INSAR_PORT = $prevPort
    $env:INSAR_HOME = $prevHome
}

if ($status) {
    Write-Output ("SMOKE OK  /api/health 200  {0}" -f $status)
    exit 0
}
Write-Output ("SMOKE FAILED  {0} 秒内未得到 200,详见 {1}" -f $TimeoutSec, $ErrLog)
exit 1
