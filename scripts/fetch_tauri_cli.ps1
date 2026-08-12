<#
.SYNOPSIS
    下载 tauri-cli 官方预编译二进制到 .tools\tauri-cli\(不要 cargo install,本地编译要 20-60 分钟)。

.DESCRIPTION
    资产 URL 模式(官方 GitHub Releases,tag 按 crate 划分):
      https://github.com/tauri-apps/tauri/releases/download/tauri-cli-v{VERSION}/cargo-tauri-{TARGET}.zip
    Windows x64 的 TARGET 为 x86_64-pc-windows-msvc,zip 内为单个 cargo-tauri.exe(约 7 MB)。

    下载顺序:GitHub 直连 → 国内反代镜像逐个回退(清华 TUNA 不镜像 GitHub Releases 二进制,
    故用 gh 反代;crates.io/rustup 的国内镜像见 desktop\bundle\BUNDLING.md §2.3)。
    反代镜像可用性随时间漂移,失效时换 -MirrorPrefix 或修改 $MirrorPrefixes。

    SHA256 校验(骨架,优先级从高到低):
      1. -Sha256 显式传入;
      2. $KnownSha256 表(拿到可信校验值后回填);
      3. GitHub Releases API 的 asset digest 字段(尽力而为,国内可能连不上 api.github.com);
      4. 都没有 → 打印实际 SHA256 并放行(TOFU:首次可信下载后把值回填到表里)。

.PARAMETER Version
    tauri-cli 版本号(不带 v 前缀)。默认 2.11.4(2026-08 时点最新,以 releases 页为准)。
.PARAMETER Target
    目标三元组,默认 x86_64-pc-windows-msvc。
.PARAMETER Sha256
    期望的 zip SHA256(64 位十六进制)。
.PARAMETER MirrorPrefix
    优先使用的反代镜像前缀,如 https://ghfast.top/(拼接在完整 GitHub URL 之前)。
.PARAMETER Force
    已存在 cargo-tauri.exe 时仍强制重新下载。

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\fetch_tauri_cli.ps1
.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\fetch_tauri_cli.ps1 -Version 2.11.4 -Sha256 abc...(64位)
#>
[CmdletBinding()]
param(
    [string]$Version = '2.11.4',
    [string]$Target = 'x86_64-pc-windows-msvc',
    [string]$Sha256 = '',
    [string]$MirrorPrefix = '',
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
# PS5.1 默认不启用 TLS1.2,GitHub 会拒绝握手
[Net.ServicePointManager]::SecurityProtocol = `
    [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

$RepoRoot = Split-Path -Parent $PSScriptRoot
$DestDir  = Join-Path $RepoRoot '.tools\tauri-cli'   # 已被根 .gitignore 的 .tools/ 规则忽略
$ExePath  = Join-Path $DestDir 'cargo-tauri.exe'

# 已知版本 zip 的 SHA256(小写十六进制)。骨架:官方未随包发布校验文件,
# 可信来源为 GitHub API 的 asset digest,或首次可信下载后回填(TOFU)。
$KnownSha256 = @{
    # 来源:GitHub Releases API asset digest,2026-08-12 实际下载并校验通过
    '2.11.4|x86_64-pc-windows-msvc' = '0743e30a661a35d63339b24cf63828f97ba5389a1d7f13b368a542794dd0a3f3'
}

if ((Test-Path $ExePath) -and -not $Force) {
    Write-Host "[跳过] 已存在 $ExePath(-Force 可强制重下)"
    exit 0
}

$AssetName = "cargo-tauri-$Target.zip"
$GitHubUrl = "https://github.com/tauri-apps/tauri/releases/download/tauri-cli-v$Version/$AssetName"

$MirrorPrefixes = @(
    ''                             # 直连 GitHub
    'https://ghfast.top/'
    'https://gh-proxy.com/'
    'https://mirror.ghproxy.com/'
)
if ($MirrorPrefix) { $MirrorPrefixes = @($MirrorPrefix) + $MirrorPrefixes }

New-Item -ItemType Directory -Force -Path $DestDir | Out-Null
$ZipPath = Join-Path $DestDir $AssetName

# ---------- 下载(直连 + 镜像回退) ----------
$downloaded = $false
foreach ($prefix in $MirrorPrefixes) {
    $url = "$prefix$GitHubUrl"
    Write-Host "[下载] $url"
    try {
        Invoke-WebRequest -Uri $url -OutFile $ZipPath -UseBasicParsing -TimeoutSec 90
        if ((Get-Item $ZipPath).Length -gt 3MB) { $downloaded = $true; break }
        Write-Host '[警告] 文件过小,疑似镜像返回了错误页,换下一个源' -ForegroundColor Yellow
    } catch {
        Write-Host "[警告] 失败:$($_.Exception.Message)" -ForegroundColor Yellow
    }
}
if (-not $downloaded) {
    if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }
    Write-Host '[错误] 所有下载源均失败。可手动用浏览器/下载器获取后,把 zip 里的 cargo-tauri.exe 解压到 .tools\tauri-cli\:' -ForegroundColor Red
    Write-Host "    $GitHubUrl"
    Write-Host '  其他获取路线(npmmirror 的 npm 包等)见 desktop\bundle\BUNDLING.md §2。'
    exit 1
}

# ---------- SHA256 校验(骨架) ----------
$expected = $Sha256
if (-not $expected) { $expected = $KnownSha256["$Version|$Target"] }
if (-not $expected) {
    # 尽力而为:GitHub API 自 2024 起为每个 asset 提供 digest(sha256:...)
    try {
        $api = "https://api.github.com/repos/tauri-apps/tauri/releases/tags/tauri-cli-v$Version"
        $rel = Invoke-RestMethod -Uri $api -TimeoutSec 20 -UseBasicParsing `
            -Headers @{ 'User-Agent' = 'insar-agent-desktop-build'; 'Accept' = 'application/vnd.github+json' }
        $asset = $rel.assets | Where-Object { $_.name -eq $AssetName } | Select-Object -First 1
        if ($asset -and $asset.PSObject.Properties['digest'] -and $asset.digest -match '^sha256:([0-9a-fA-F]{64})$') {
            $expected = $Matches[1]
            Write-Host "[校验] 从 GitHub API 取得官方 digest:$expected"
        }
    } catch {
        Write-Host "[提示] 查询 GitHub API digest 失败(不影响下载):$($_.Exception.Message)" -ForegroundColor DarkGray
    }
}

$actual = (Get-FileHash -Algorithm SHA256 -Path $ZipPath).Hash.ToLowerInvariant()
if ($expected) {
    if ($actual -ne $expected.ToLowerInvariant()) {
        Remove-Item $ZipPath -Force
        Write-Host "[错误] SHA256 不匹配,已删除下载文件!预期 $expected,实际 $actual。" -ForegroundColor Red
        Write-Host '  若刚换过镜像,可能是镜像被投毒或缓存损坏,请改用直连/官方渠道重试。'
        exit 1
    }
    Write-Host "[校验] SHA256 通过:$actual"
} else {
    Write-Host '[警告] 无可用的期望 SHA256,跳过强校验(骨架模式)。本次实际值,确认可信后请回填脚本内 $KnownSha256 表:' -ForegroundColor Yellow
    Write-Host "    '$Version|$Target' = '$actual'"
}

# ---------- 解压与自检 ----------
Expand-Archive -Path $ZipPath -DestinationPath $DestDir -Force
Remove-Item $ZipPath -Force
if (-not (Test-Path $ExePath)) {
    # 兼容 zip 内多一层目录的情形
    $found = Get-ChildItem -Path $DestDir -Recurse -Filter 'cargo-tauri.exe' | Select-Object -First 1
    if ($found) { Move-Item $found.FullName $ExePath -Force }
}
if (-not (Test-Path $ExePath)) {
    Write-Host '[错误] 解压后未找到 cargo-tauri.exe,压缩包结构可能有变,请解压检查。' -ForegroundColor Red
    exit 1
}

$ver = & $ExePath --version
Write-Host "[完成] $ExePath($ver)"
Write-Host '  scripts\build_desktop.ps1 会自动发现并使用它执行 tauri build。'
exit 0
