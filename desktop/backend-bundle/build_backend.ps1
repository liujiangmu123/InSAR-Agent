<#
静默构建冻结后端(insar-backend,onedir/无窗)。

用法(任意目录皆可,脚本自定位):
  powershell -ExecutionPolicy Bypass -File desktop\backend-bundle\build_backend.ps1 [-SkipDeps]

行为:
  1. 确保仓库根 .venv 存在(缺则 py -m venv .venv);
  2. 安装运行时依赖(requirements.txt + pip install -e .)与打包依赖
     (requirements-desktop.txt,PyInstaller 版本钉住);-SkipDeps 可跳过;
  3. 运行 PyInstaller(insar_backend.spec),产物在 dist\insar-backend\;
  4. 全程输出写入 build.log,控制台只出一行结果 —— 不弹任何窗口。
#>
[CmdletBinding()]
param(
    [switch]$SkipDeps
)

$ErrorActionPreference = "Stop"
$BundleDir = $PSScriptRoot
$RepoRoot  = (Resolve-Path (Join-Path $BundleDir "..\..")).Path
$VenvPy    = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$LogFile   = Join-Path $BundleDir "build.log"

Set-Content -Path $LogFile -Encoding UTF8 -Value (
    "[{0}] build_backend start (repo={1})" -f (Get-Date -Format s), $RepoRoot)

function Invoke-Logged {
    param([string]$Label, [string[]]$Argv)
    Add-Content -Path $LogFile -Encoding UTF8 -Value ("`n=== {0} ===" -f $Label)
    $prev = $ErrorActionPreference
    # PowerShell 5.1:原生命令的 stderr 在重定向时会被包装成 ErrorRecord,
    # 配合 Stop 会把 pip 的进度信息当成致命错误 —— 步骤内退回 Continue,
    # 成败只认退出码。
    $ErrorActionPreference = "Continue"
    try {
        $out = & $Argv[0] $Argv[1..($Argv.Count - 1)] 2>&1
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prev
    }
    if ($out) {
        $out | ForEach-Object { $_.ToString() } | Add-Content -Path $LogFile -Encoding UTF8
    }
    if ($code -ne 0) {
        throw ("{0} 失败(exit={1}),详见 {2}" -f $Label, $code, $LogFile)
    }
}

if (-not (Test-Path $VenvPy)) {
    Invoke-Logged "create venv" @("py", "-m", "venv", (Join-Path $RepoRoot ".venv"))
}

if (-not $SkipDeps) {
    Invoke-Logged "install runtime deps" @($VenvPy, "-m", "pip", "install",
        "-r", (Join-Path $RepoRoot "requirements.txt"), "-e", $RepoRoot)
    Invoke-Logged "install desktop build deps" @($VenvPy, "-m", "pip", "install",
        "-r", (Join-Path $RepoRoot "requirements-desktop.txt"))
}

Invoke-Logged "pyinstaller" @($VenvPy, "-m", "PyInstaller",
    "--noconfirm", "--clean", "--log-level", "INFO",
    "--distpath", (Join-Path $BundleDir "dist"),
    "--workpath", (Join-Path $BundleDir "build"),
    (Join-Path $BundleDir "insar_backend.spec"))

$ExePath = Join-Path $BundleDir "dist\insar-backend\insar-backend.exe"
if (-not (Test-Path $ExePath)) {
    throw ("构建结束但没有产物:{0},详见 {1}" -f $ExePath, $LogFile)
}
$DistBytes = (Get-ChildItem -Recurse -File (Join-Path $BundleDir "dist\insar-backend") |
    Measure-Object -Sum Length).Sum
Write-Output ("BUILD OK  {0}  ({1:N1} MB, 日志 {2})" -f $ExePath, ($DistBytes / 1MB), $LogFile)
