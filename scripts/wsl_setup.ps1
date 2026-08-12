<#
.SYNOPSIS
    insar-agent WSL 引擎环境的宿主侧编排(幂等可重跑,全程无窗)。

.DESCRIPTION
    步骤:
      1/4 前置检查:wsl.exe 可用性与版本;发行版(默认 insar)是否已导入,
          未导入则打印主线导入命令并退出(本脚本不做导入)。
      2/4 写 %USERPROFILE%\.wslconfig:[wsl2] 段 memory=40GB / processors=20 /
          swap=16GB / swapFile=E:\wsl\swap.vhdx。已存在则先备份(时间戳后缀),
          再按键合并 —— 只增改这四个键,用户其它段/键原样保留;内容无变化不写不备份。
          注意:.wslconfig 变更需 `wsl --shutdown` 后才生效,本脚本绝不自动 shutdown
          (避免打断其它正在运行的 WSL 任务),只打印提示。
      3/4 在 WSL 内以 root 执行 scripts/wsl_setup.sh(该脚本自身幂等,哨兵文件跳步),
          输出实时写入 logs\wsl_setup_<时间戳>.log。
      4/4 验证:调 src/insar_agent/runtime/wsl_probe.py 打印引擎清单
          (ISCE2/MintPy/SNAPHU 存在性与版本、conda env 路径)。

    无窗约束:所有子进程(wsl.exe / python)都在当前控制台内执行,
    不使用 Start-Process,不会弹出新窗口。

    退出码:
      0  成功(或按参数跳过了验证)
      2  wsl.exe 不存在/不可用
      3  发行版未导入(提示主线先导入)
      4  wsl_setup.sh 失败(其分级退出码见该脚本头部注释与日志)
      5  验证失败(WSL 不可达或引擎不全)

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\wsl_setup.ps1

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\wsl_setup.ps1 -Distro insar -SkipWslConfig
#>
[CmdletBinding()]
param(
    [string]$Distro = 'insar',
    [switch]$SkipWslConfig,   # 不碰 .wslconfig
    [switch]$SkipSetup,       # 跳过 WSL 内安装段(只做前置检查与验证)
    [switch]$SkipProbe,       # 跳过验证段
    [string]$LogDir = ''      # 默认 <仓库根>\logs(已被 .gitignore)
)

$ErrorActionPreference = 'Stop'
$env:WSL_UTF8 = '1'   # wsl.exe 自身消息用 UTF-8 输出,避免 UTF-16 花屏/NUL 残留
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$RepoRoot = Split-Path -Parent $PSScriptRoot
if (-not $LogDir) { $LogDir = Join-Path $RepoRoot 'logs' }
$SetupSh = Join-Path $PSScriptRoot 'wsl_setup.sh'

function Write-Info([string]$Msg) { Write-Host "[wsl_setup.ps1] $Msg" }
function Write-Warn([string]$Msg) { Write-Host "[wsl_setup.ps1] 警告:$Msg" -ForegroundColor Yellow }

function Invoke-Native {
    <# 原生命令统一入口:临时放宽 ErrorActionPreference,避免 PS5.1 下
       stderr 经 2>&1 变成 ErrorRecord 触发 NativeCommandError 直接中断。
       在当前控制台执行(无新窗),返回 ExitCode/Lines/Text。 #>
    param(
        [Parameter(Mandatory)][string]$Exe,
        [string[]]$Arguments = @()
    )
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $lines = @(& $Exe @Arguments 2>&1 | ForEach-Object { "$_" })
        $code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $prev }
    [pscustomobject]@{ ExitCode = $code; Lines = $lines; Text = ($lines -join "`n") }
}

function Invoke-NativeLogged {
    <# 长任务版:输出逐行实时写日志文件 + 回显控制台;返回退出码。 #>
    param(
        [Parameter(Mandatory)][string]$Exe,
        [string[]]$Arguments = @(),
        [Parameter(Mandatory)][string]$LogPath
    )
    $sw = New-Object System.IO.StreamWriter($LogPath, $true, (New-Object System.Text.UTF8Encoding $false))
    $sw.AutoFlush = $true
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Exe @Arguments 2>&1 | ForEach-Object {
            $s = "$_"
            $sw.WriteLine($s)
            Write-Host $s
        }
        return $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prev
        $sw.Close()
    }
}

function Update-WslConfig {
    <# 合并 .wslconfig:只增改 [wsl2] 段的 memory/processors/swap/swapFile 四键,
       其它段与键原样保留;同键重复行去重为一行。有实际改动才备份并写回。
       返回 $true 表示写回了新内容。 #>
    param([Parameter(Mandatory)][string]$Path)

    # swapFile 在 .wslconfig 中反斜杠需转义为 \\
    $want = [ordered]@{
        memory     = '40GB'
        processors = '20'
        swap       = '16GB'
        swapFile   = 'E:\\wsl\\swap.vhdx'
    }
    $orig = @()
    if (Test-Path -LiteralPath $Path) { $orig = @(Get-Content -LiteralPath $Path) }

    $out = New-Object System.Collections.Generic.List[string]
    $section = ''
    $wsl2Seen = $false
    $done = @{}
    foreach ($k in $want.Keys) { $done[$k] = $false }

    function Add-MissingKeys {
        foreach ($k in $want.Keys) {
            if (-not $done[$k]) { $out.Add("$k=$($want[$k])"); $done[$k] = $true }
        }
    }

    foreach ($line in $orig) {
        $m = [regex]::Match($line, '^\s*\[(?<name>[^\]]+)\]\s*$')
        if ($m.Success) {
            if ($section -eq 'wsl2') { Add-MissingKeys }   # 离开 [wsl2] 前补齐缺失键
            $section = $m.Groups['name'].Value.Trim().ToLowerInvariant()
            if ($section -eq 'wsl2') { $wsl2Seen = $true }
            $out.Add($line)
            continue
        }
        if ($section -eq 'wsl2') {
            $kv = [regex]::Match($line, '^\s*(?<key>[A-Za-z][A-Za-z0-9]*)\s*=')
            if ($kv.Success) {
                $key = $kv.Groups['key'].Value
                $hit = @($want.Keys | Where-Object { $_ -ieq $key })
                if ($hit.Count -gt 0) {
                    $name = $hit[0]
                    if (-not $done[$name]) { $out.Add("$name=$($want[$name])"); $done[$name] = $true }
                    # 同键重复行:丢弃
                    continue
                }
            }
        }
        $out.Add($line)
    }
    if ($section -eq 'wsl2') { Add-MissingKeys }
    if (-not $wsl2Seen) {
        if ($out.Count -gt 0 -and $out[$out.Count - 1].Trim() -ne '') { $out.Add('') }
        $out.Add('[wsl2]')
        Add-MissingKeys
    }

    $newText = ($out -join "`r`n") + "`r`n"
    $oldText = if ($orig.Count -gt 0) { ($orig -join "`r`n") + "`r`n" } else { '' }
    if ($newText -eq $oldText) { return $false }

    if (Test-Path -LiteralPath $Path) {
        $bak = "$Path.bak.$(Get-Date -Format 'yyyyMMdd_HHmmss')"
        Copy-Item -LiteralPath $Path -Destination $bak -Force
        Write-Info "已备份原 .wslconfig -> $bak"
    }
    # ASCII 内容,UTF-8(无 BOM)写入即可被 WSL 正确读取
    [System.IO.File]::WriteAllText($Path, $newText, (New-Object System.Text.UTF8Encoding $false))
    return $true
}

# =============================== 1/4 前置检查 ===============================
Write-Info "步骤 1/4:前置检查(wsl.exe 与发行版 '$Distro')"
if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    Write-Warn 'wsl.exe 不存在:本机未启用 WSL。请主线先完成 WSL 安装与发行版导入(见 docs/WSL-SETUP.md)。'
    exit 2
}
$ver = Invoke-Native 'wsl.exe' @('--version')
if ($ver.ExitCode -eq 0 -and $ver.Lines.Count -gt 0) {
    Write-Info ("WSL 版本:" + $ver.Lines[0])
} else {
    # 旧 inbox 版 wsl.exe 无 --version,退化到 --status 判断可用性
    $st = Invoke-Native 'wsl.exe' @('--status')
    if ($st.ExitCode -ne 0) {
        Write-Warn 'wsl.exe 存在但不可用(--version/--status 均失败);请先执行 wsl --update 或检查虚拟化设置。'
        exit 2
    }
    Write-Warn '未识别 wsl --version(旧版 inbox WSL);建议 wsl --update 升级到 Store 版。'
}
$distros = @((Invoke-Native 'wsl.exe' @('-l', '-q')).Lines |
    ForEach-Object { ($_ -replace "`0", '').Trim() } | Where-Object { $_ })
Write-Info ("已导入发行版:" + $(if ($distros.Count -gt 0) { $distros -join ', ' } else { '<无>' }))
if ($distros -notcontains $Distro) {
    Write-Warn "发行版 '$Distro' 未导入。请主线先执行导入(rootfs 路径按实际调整;必须导入到 E: 盘,见 AGENT-DESIGN §4.8):"
    Write-Host "    wsl --import $Distro E:\wsl\$Distro <ubuntu-noble-wsl-amd64.rootfs.tar.gz> --version 2"
    exit 3
}

# ============================ 2/4 .wslconfig 合并 ============================
Write-Info '步骤 2/4:写入/合并 %USERPROFILE%\.wslconfig(memory=40GB processors=20 swap=16GB swapFile=E:\wsl\swap.vhdx)'
if ($SkipWslConfig) {
    Write-Info '按参数 -SkipWslConfig 跳过'
} else {
    if (Test-Path 'E:\') {
        New-Item -ItemType Directory -Force -Path 'E:\wsl' | Out-Null   # swap.vhdx 的目录须先存在
    } else {
        Write-Warn 'E: 盘不存在,无法预建 E:\wsl;swapFile 配置仍会写入,请按实际盘符调整 .wslconfig'
    }
    $cfgPath = Join-Path $env:USERPROFILE '.wslconfig'
    if (Update-WslConfig -Path $cfgPath) {
        Write-Info ".wslconfig 已更新:$cfgPath"
        Write-Warn '配置需 `wsl --shutdown` 后才生效;本脚本不自动执行(会打断正在运行的 WSL 任务),请在空闲时手动执行。'
    } else {
        Write-Info '.wslconfig 已符合目标配置,无改动'
    }
}

# ========================== 3/4 WSL 内执行 wsl_setup.sh ==========================
Write-Info "步骤 3/4:在 WSL('$Distro',root)内执行 wsl_setup.sh"
if ($SkipSetup) {
    Write-Info '按参数 -SkipSetup 跳过'
} else {
    if (-not (Test-Path -LiteralPath $SetupSh)) { Write-Warn "找不到 $SetupSh"; exit 4 }
    $conv = Invoke-Native 'wsl.exe' @('-d', $Distro, '-u', 'root', '--exec', 'wslpath', '-u', $SetupSh)
    if ($conv.ExitCode -ne 0 -or -not $conv.Text.Trim()) {
        Write-Warn ("wslpath 路径转换失败(automount 被禁用?):" + $conv.Text)
        exit 4
    }
    $shWsl = $conv.Text.Trim()
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
    $log = Join-Path $LogDir ("wsl_setup_{0}.log" -f (Get-Date -Format 'yyyyMMdd_HHmmss'))
    Write-Info "安装日志:$log"
    # 经 /tmp 中转执行:仓库在 Windows 检出可能带 CRLF,先 tr 剥掉 \r 再跑;
    # 对 9p(/mnt/*)只做一次小文件读取,符合 §4.9 性能红线
    $bash = "set -u; tmp=`$(mktemp /tmp/insar_wsl_setup.XXXXXX.sh) && tr -d '\r' < '$shWsl' > `"`$tmp`" && bash `"`$tmp`"; rc=`$?; rm -f `"`$tmp`"; exit `$rc"
    $rc = Invoke-NativeLogged 'wsl.exe' @('-d', $Distro, '-u', 'root', '--exec', 'bash', '-c', $bash) -LogPath $log
    if ($rc -ne 0) {
        Write-Warn "wsl_setup.sh 失败,退出码 $rc(分级含义见 scripts/wsl_setup.sh 头部注释与 docs/WSL-SETUP.md);日志:$log"
        exit 4
    }
    Write-Info 'WSL 内安装完成(重跑本脚本可续装/校验,哨兵文件自动跳过已完成步骤)'
}

# ================================ 4/4 验证段 ================================
Write-Info '步骤 4/4:验证 —— 探测 WSL 内引擎清单(wsl_probe)'
if ($SkipProbe) {
    Write-Info '按参数 -SkipProbe 跳过'
    exit 0
}
$py = Join-Path $RepoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $py)) {
    $pyCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($pyCmd) {
        $py = $pyCmd.Source
        Write-Warn "未找到项目 .venv,退化使用 PATH 中的 python:$py"
    } else {
        Write-Warn '找不到可用 python,跳过验证;稍后可手动运行:.venv\Scripts\python.exe -m insar_agent.runtime.wsl_probe --distro insar'
        exit 0
    }
}
$env:PYTHONPATH = (Join-Path $RepoRoot 'src') + ';' + "$env:PYTHONPATH"   # 未安装为包时也能 -m 运行
$probe = Invoke-Native $py @('-m', 'insar_agent.runtime.wsl_probe', '--distro', $Distro)
$probe.Lines | ForEach-Object { Write-Host $_ }
switch ($probe.ExitCode) {
    0 { Write-Info '验证通过:引擎全部可见'; exit 0 }
    2 { Write-Warn '验证未通过:WSL 可达但引擎不全(见上方清单);可重跑本脚本续装'; exit 5 }
    default { Write-Warn "验证失败:WSL/发行版不可达(probe 退出码 $($probe.ExitCode))"; exit 5 }
}
