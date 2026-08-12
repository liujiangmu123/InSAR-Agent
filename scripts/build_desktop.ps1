<#
.SYNOPSIS
    InSAR-Agent 桌面版一键构建:后端冻结(PyInstaller)+ cargo release 编译
    +(可选)tauri build 出 NSIS 安装包。

.DESCRIPTION
    步骤(2026-08-12 首次全量打包实测校准):
      0. 后端冻结:运行 desktop\backend-bundle\build_backend.ps1(-SkipBackend 可跳过)。
         注意:tauri.conf.json 的 bundle.resources 已映射 backend-bundle\dist\insar-backend\,
         该目录缺失时 tauri-build 会在 cargo 编译阶段直接报错 —— 冻结必须先行。
      1. 前置检查:
         - cargo 必须存在,否则打印获取指引后退出(退出码 1);
         - tauri-cli 可选:按 .tools\tauri-cli\ → PATH 顺序查找,找不到则只编译不打包;
         - desktop\icons\icon.ico 缺失时自动执行 icons\make_icons.py 生成占位图标
           (tauri-build 在 Windows 上必须有 .ico 才能编译通过)。
      2. 在 desktop\ 下执行 cargo build --release,stdout/stderr 全部重定向到日志文件。
      3. 若找到 tauri-cli 且未指定 -CargoOnly,继续 tauri build(NSIS 安装包)。
      4. 汇总打印产物路径与大小。

    构建目录:尊重环境变量 CARGO_TARGET_DIR(不设则默认 desktop\target)。
    本机 C 盘紧张时建议:$env:CARGO_TARGET_DIR = 'E:\cargo-target-desktop-bundle'。
    日志目录:{target}\build-logs\(target 目录不入库)。
    全程在当前进程内执行(调用运算符 &),不使用 Start-Process,不弹任何新窗口。

.PARAMETER CargoOnly
    只执行 cargo release 编译;即使 tauri-cli 存在也跳过安装包打包。

.PARAMETER SkipBackend
    跳过后端冻结步骤(要求 desktop\backend-bundle\dist\insar-backend\ 已存在,
    否则 cargo 编译会因 bundle.resources 源缺失而失败)。

.PARAMETER SkipDeps
    透传给 build_backend.ps1:跳过 pip 依赖安装,直接 PyInstaller(依赖已装好时提速)。

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build_desktop.ps1
.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build_desktop.ps1 -SkipBackend -CargoOnly

.NOTES
    tauri-cli 获取(不要 cargo install,本地编译要 20-60 分钟):
      powershell -NoProfile -ExecutionPolicy Bypass -File scripts\fetch_tauri_cli.ps1
    国内网络实测 Invoke-WebRequest 直连/反代均易涓流卡死,curl.exe 断点续传兜底见
    docs\RELEASE-CHECKLIST.md §5;NSIS 工具链首次下载设
    $env:TAURI_BUNDLER_TOOLS_GITHUB_MIRROR = 'https://ghfast.top/'。
    完整打包方案见 desktop\bundle\BUNDLING.md。
    退出码:0 成功;1 前置缺失;2 cargo 编译失败;3 tauri build 失败(cargo 部分已成功);
            4 后端冻结失败。
#>
[CmdletBinding()]
param(
    [switch]$CargoOnly,
    [switch]$SkipBackend,
    [switch]$SkipDeps
)

$ErrorActionPreference = 'Stop'

$RepoRoot   = Split-Path -Parent $PSScriptRoot
$DesktopDir = Join-Path $RepoRoot 'desktop'
# 尊重 CARGO_TARGET_DIR(cargo 与 tauri-cli 都认它),产物汇总与日志跟着走
$TargetDir  = if ($env:CARGO_TARGET_DIR) { $env:CARGO_TARGET_DIR } else { Join-Path $DesktopDir 'target' }
$LogDir     = Join-Path $TargetDir 'build-logs'
$Stamp      = Get-Date -Format 'yyyyMMdd-HHmmss'

if (-not (Test-Path (Join-Path $DesktopDir 'Cargo.toml'))) {
    Write-Host "[错误] 未找到 desktop\Cargo.toml(RepoRoot=$RepoRoot)。" -ForegroundColor Red
    exit 1
}
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

# 在当前进程内执行原生命令,stdout/stderr 全部写入日志文件(不弹新窗口)。
# 执行期把 ErrorActionPreference 降为 Continue:PowerShell 5.1 重定向原生 stderr 时
# 会把 stderr 行包装成 ErrorRecord,Stop 模式下会被误判为终止错误。
function Invoke-LoggedCommand {
    param(
        [Parameter(Mandatory)][string]$Exe,
        [string[]]$CmdArgs = @(),
        [Parameter(Mandatory)][string]$LogFile,
        [Parameter(Mandatory)][string]$WorkDir
    )
    Push-Location $WorkDir
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Exe @CmdArgs 2>&1 | ForEach-Object { "$_" } |
            Out-File -FilePath $LogFile -Encoding utf8 -Append
        return $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $prevEap
        Pop-Location
    }
}

function Show-LogTail {
    param([string]$LogFile, [int]$Lines = 30)
    if (Test-Path $LogFile) {
        Write-Host "---- 日志末尾 $Lines 行($LogFile)----" -ForegroundColor DarkGray
        Get-Content $LogFile -Tail $Lines | ForEach-Object { Write-Host "  $_" }
        Write-Host '----' -ForegroundColor DarkGray
    }
}

# ---------- 0. 后端冻结(PyInstaller onedir) ----------
$BackendDist = Join-Path $DesktopDir 'backend-bundle\dist\insar-backend'
if ($SkipBackend) {
    if (-not (Test-Path (Join-Path $BackendDist 'insar-backend.exe'))) {
        Write-Host "[错误] 指定了 -SkipBackend 但冻结产物不存在:$BackendDist" -ForegroundColor Red
        Write-Host '  tauri.conf.json 的 bundle.resources 引用该目录,缺失会让 cargo 编译直接失败。'
        Write-Host '  先运行:powershell -NoProfile -ExecutionPolicy Bypass -File desktop\backend-bundle\build_backend.ps1'
        exit 4
    }
    Write-Host "[后端] 已指定 -SkipBackend,复用现有冻结产物:$BackendDist"
} else {
    Write-Host '[后端] 冻结 Python 后端(build_backend.ps1,日志 desktop\backend-bundle\build.log)…'
    $backendArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass',
        '-File', (Join-Path $DesktopDir 'backend-bundle\build_backend.ps1'))
    if ($SkipDeps) { $backendArgs += '-SkipDeps' }
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & powershell @backendArgs 2>&1 | ForEach-Object { Write-Host "  $_" }
    $backendExit = $LASTEXITCODE
    $ErrorActionPreference = $prevEap
    if ($backendExit -ne 0 -or -not (Test-Path (Join-Path $BackendDist 'insar-backend.exe'))) {
        Write-Host "[错误] 后端冻结失败(退出码 $backendExit),详见 desktop\backend-bundle\build.log" -ForegroundColor Red
        exit 4
    }
}

# ---------- 1. 前置检查 ----------
$cargoCmd = Get-Command cargo -ErrorAction SilentlyContinue
if (-not $cargoCmd) {
    Write-Host '[错误] 未找到 cargo。请先安装 Rust 工具链(MSVC):' -ForegroundColor Red
    Write-Host '  国内推荐:https://rsproxy.cn/(按说明设置 RUSTUP_DIST_SERVER / RUSTUP_UPDATE_ROOT 后运行 rustup-init.exe)'
    Write-Host '  官方:https://rustup.rs/'
    Write-Host '  安装完成后重开终端使 PATH 生效,再运行本脚本。'
    exit 1
}
Write-Host "[前置] cargo:$($cargoCmd.Source)"
Write-Host "[前置] 构建目录:$TargetDir$(if ($env:CARGO_TARGET_DIR) { '(来自 CARGO_TARGET_DIR)' })"

# tauri-cli 查找:仓库 .tools\tauri-cli\ 优先(scripts\fetch_tauri_cli.ps1 的落点),其次 PATH
$tauriExe = $null
foreach ($c in @(
    (Join-Path $RepoRoot '.tools\tauri-cli\cargo-tauri.exe'),
    (Join-Path $RepoRoot '.tools\tauri-cli\tauri.exe')
)) {
    if (Test-Path $c) { $tauriExe = $c; break }
}
if (-not $tauriExe) {
    foreach ($name in @('cargo-tauri', 'tauri')) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) { $tauriExe = $cmd.Source; break }
    }
}
if ($tauriExe) {
    Write-Host "[前置] tauri-cli:$tauriExe"
} else {
    Write-Host '[前置] 未找到 tauri-cli:本次只做 cargo 编译,不出安装包。获取方式(勿用 cargo install,编译太久):' -ForegroundColor Yellow
    Write-Host '    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\fetch_tauri_cli.ps1'
    Write-Host '    详见 desktop\bundle\BUNDLING.md §2。'
}

# 图标:tauri-build 在 Windows 上必须有 icons\icon.ico(嵌入 exe 资源),缺失则用占位脚本生成
$iconIco = Join-Path $DesktopDir 'icons\icon.ico'
if (-not (Test-Path $iconIco)) {
    Write-Host '[前置] icons\icon.ico 缺失,尝试生成占位图标(icons\make_icons.py,纯标准库)...'
    $pythonExe = $null
    $venvPy = Join-Path $RepoRoot '.venv\Scripts\python.exe'
    if (Test-Path $venvPy) { $pythonExe = $venvPy }
    if (-not $pythonExe) {
        foreach ($name in @('python', 'py')) {
            $cmd = Get-Command $name -ErrorAction SilentlyContinue
            if ($cmd) { $pythonExe = $cmd.Source; break }
        }
    }
    if ($pythonExe) {
        & $pythonExe (Join-Path $DesktopDir 'icons\make_icons.py') | Out-Null
    }
    if (-not (Test-Path $iconIco)) {
        Write-Host '[错误] 无法生成 icons\icon.ico(未找到可用 Python?)。请手动执行:' -ForegroundColor Red
        Write-Host '    .venv\Scripts\python.exe desktop\icons\make_icons.py'
        exit 1
    }
    Write-Host '[前置] 占位图标已生成(正式发布前请替换为设计稿,见 desktop\README.md §图标)。'
}

# ---------- 2. cargo release 编译 ----------
$cargoLog = Join-Path $LogDir "cargo-build-$Stamp.log"
Write-Host "[编译] cargo build --release(工作目录 desktop\,日志:$cargoLog)"
$sw = [System.Diagnostics.Stopwatch]::StartNew()
$cargoExit = Invoke-LoggedCommand -Exe $cargoCmd.Source -CmdArgs @('build', '--release') -LogFile $cargoLog -WorkDir $DesktopDir
$sw.Stop()
if ($cargoExit -ne 0) {
    Write-Host ("[错误] cargo build 失败(退出码 {0},耗时 {1}s)" -f $cargoExit, [int]$sw.Elapsed.TotalSeconds) -ForegroundColor Red
    Show-LogTail $cargoLog
    exit 2
}
Write-Host ("[编译] cargo build 成功,耗时 {0}s" -f [int]$sw.Elapsed.TotalSeconds)

# ---------- 3. tauri build(可选)----------
$tauriExit = $null
if ($CargoOnly) {
    Write-Host '[打包] 已指定 -CargoOnly,跳过 tauri build。'
} elseif (-not $tauriExe) {
    Write-Host '[打包] 无 tauri-cli,跳过安装包(获取方式见上方前置提示)。'
} else {
    $tauriLog = Join-Path $LogDir "tauri-build-$Stamp.log"
    Write-Host "[打包] tauri build(日志:$tauriLog)"
    $sw.Restart()
    $tauriExit = Invoke-LoggedCommand -Exe $tauriExe -CmdArgs @('build') -LogFile $tauriLog -WorkDir $DesktopDir
    $sw.Stop()
    if ($tauriExit -ne 0) {
        Write-Host ("[警告] tauri build 失败(退出码 {0})。常见原因:NSIS 工具链首次自动下载失败(国内网络,设 TAURI_BUNDLER_TOOLS_GITHUB_MIRROR 镜像变量重试)、backend-bundle\dist 缺失、icons 缺失。排查见 desktop\bundle\BUNDLING.md §3/§7。" -f $tauriExit) -ForegroundColor Yellow
        Show-LogTail $tauriLog
    } else {
        Write-Host ("[打包] tauri build 成功,耗时 {0}s" -f [int]$sw.Elapsed.TotalSeconds)
    }
}

# ---------- 4. 产物汇总 ----------
Write-Host ''
Write-Host '===== 产物汇总 ====='
$releaseDir = Join-Path $TargetDir 'release'
$bareExes = @(Get-ChildItem -Path $releaseDir -Filter '*.exe' -File -ErrorAction SilentlyContinue)
if ($bareExes.Count -gt 0) {
    foreach ($exe in $bareExes) {
        Write-Host ("  {0,8:N1} MB  {1}" -f ($exe.Length / 1MB), $exe.FullName)
    }
} else {
    Write-Host "  (无裸 exe?检查 $releaseDir)"
}
$backendExe = Join-Path $BackendDist 'insar-backend.exe'
if (Test-Path $backendExe) {
    $backendBytes = (Get-ChildItem -Recurse -File $BackendDist | Measure-Object -Sum Length).Sum
    Write-Host ("  {0,8:N1} MB  {1}  <- 冻结后端(onedir 整目录)" -f ($backendBytes / 1MB), $BackendDist)
}
$nsisDir = Join-Path $releaseDir 'bundle\nsis'
$setupExes = @(Get-ChildItem -Path $nsisDir -Filter '*.exe' -File -ErrorAction SilentlyContinue)
if ($setupExes.Count -gt 0) {
    foreach ($exe in $setupExes) {
        Write-Host ("  {0,8:N1} MB  {1}  <- NSIS 安装包" -f ($exe.Length / 1MB), $exe.FullName)
    }
} else {
    Write-Host "  (无 NSIS 安装包:tauri-cli 缺失 / -CargoOnly / tauri build 未成功;预期落点 $nsisDir)"
}
Write-Host "  日志目录:$LogDir"

if ($null -ne $tauriExit -and $tauriExit -ne 0) { exit 3 }
exit 0
