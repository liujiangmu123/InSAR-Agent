# 桌面版打包方案(Windows · NSIS 安装包)

面向:把 `desktop/`(Tauri 2 手写 cargo 工程,无 node)打成**中文界面的 NSIS 安装包**。
本方案由四部分组成:

| 文件 | 作用 |
|---|---|
| `desktop/bundle/BUNDLING.md` | 本文档:完整打包方案与操作手册 |
| `desktop/bundle/tauri.bundle.snippet.json` | `bundle` 配置段草案,待合入 `tauri.conf.json`(合入说明见 §7) |
| `scripts/build_desktop.ps1` | 一键构建:cargo release 编译 +(可选)`tauri build` 出安装包 |
| `scripts/fetch_tauri_cli.ps1` | 下载 tauri-cli 官方预编译二进制(镜像回退 + SHA256 校验骨架) |

> `desktop/tauri.conf.json` 归另一分支管,本分支只提供草案与文档,不直接改它。

## 1. 总览(TL;DR)

```powershell
# ① 获取 tauri-cli(约 7 MB,秒级;禁止 cargo install,那是 20-60 分钟的全量编译)
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\fetch_tauri_cli.ps1

# ② 图标:build_desktop.ps1 会在缺失时自动生成占位图(正式发布前替换设计稿,见 §3-3)

# ③ 把 desktop\bundle\tauri.bundle.snippet.json 合入 desktop\tauri.conf.json(见 §7)

# ④ 一键构建(国内网络首次打包前建议先设官方镜像变量,见 §3-4)
$env:TAURI_BUNDLER_TOOLS_GITHUB_MIRROR = 'https://ghfast.top/'
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build_desktop.ps1
# 产物:desktop\target\release\bundle\nsis\InSAR-Agent_0.1.0_x64-setup.exe
```

## 2. 获取 tauri-cli(不要 cargo install)

### 2.1 官方预编译二进制(首选)

tauri 仓库按 crate 分 tag 发布,CLI 的资产 URL 模式:

```text
https://github.com/tauri-apps/tauri/releases/download/tauri-cli-v{VERSION}/cargo-tauri-{TARGET}.zip
```

- Windows x64:`TARGET = x86_64-pc-windows-msvc`,zip 约 7 MB,内含单个 `cargo-tauri.exe`;
- 版本示例(2026-08 时点最新):`VERSION = 2.11.4` →
  `https://github.com/tauri-apps/tauri/releases/download/tauri-cli-v2.11.4/cargo-tauri-x86_64-pc-windows-msvc.zip`
- 落点约定:解压到 `.tools\tauri-cli\cargo-tauri.exe`(`.tools/` 已被根 `.gitignore` 忽略),
  `build_desktop.ps1` 会自动发现该路径;
- 直接运行 `cargo-tauri.exe build` 即可(它同时兼容作为 `cargo tauri` 子命令调用)。

**校验**:官方不随包发布 `.sha256` 文件,可信校验源有两个:
1. GitHub Releases API 的 asset `digest` 字段(`sha256:…`):
   `https://api.github.com/repos/tauri-apps/tauri/releases/tags/tauri-cli-v{VERSION}`
   `fetch_tauri_cli.ps1` 会自动尝试查询并强校验;
2. TOFU(首次信任):在可信网络首次下载后 `Get-FileHash` 记录,回填脚本内 `$KnownSha256` 表,
   之后所有机器/镜像下载都强校验。

### 2.2 国内镜像备选

| 路线 | 说明 |
|---|---|
| gh 反代前缀 | `https://ghfast.top/`、`https://gh-proxy.com/`、`https://mirror.ghproxy.com/` + 完整 GitHub URL。`fetch_tauri_cli.ps1` 已内置按序回退;镜像可用性随时间漂移,失效换 `-MirrorPrefix` |
| npm 包(需 Node) | `npm --registry https://registry.npmmirror.com i -g @tauri-apps/cli`,npmmirror 全量镜像、国内最稳;但本工程无 node,仅作装机备选 |
| cargo-binstall | `cargo binstall tauri-cli`,本质也从 GitHub Releases 拉预编译包,国内同样需要反代 |

注意:清华 TUNA 不镜像 GitHub Releases 二进制;TUNA / rsproxy 只覆盖 crates.io 与 rustup
(`desktop\.cargo\config.toml` 已配 rsproxy,cargo 依赖下载不受 GitHub 影响)。

### 2.3 为什么禁止 `cargo install tauri-cli`

本地全量编译 600+ crate,普通开发机 20-60 分钟且占满 CPU;预编译包 7 MB、秒级可用,收益悬殊。

## 3. `tauri build` 出 NSIS 安装包的要求

1. **bundle 配置**:`bundle.active = true` 且 `targets` 含 `"nsis"`(当前 conf 为 `active: false`,
   只出裸 exe;合入 snippet 后生效,见 §7);
2. **identifier**:conf 已有 `dev.insar.agent`,满足要求(不能以 `.exe` 结尾、发布前不要用 `com.tauri.dev` 这类默认值);
3. **图标**:Windows 打包必须有 `.ico`。conf 的 `bundle.icon` 已引用
   `icons/icon.ico + 32x32.png + 128x128.png`,但这三个文件是**生成物**(不入库):
   - 占位图:`python desktop\icons\make_icons.py`(纯标准库;`build_desktop.ps1` 缺失时自动执行);
   - 正式设计稿:`cargo-tauri.exe icon 设计稿1024.png`(在 `desktop\` 下执行)一键生成全套尺寸;
4. **NSIS 工具链**(首次打包自动下载,国内网络的主要故障点):
   tauri-cli 会把 `nsis-3.11.zip`(来自 `tauri-apps/binary-releases`)与两个插件下载到
   `%LOCALAPPDATA%\tauri\NSIS`。国内两种解法:
   - **官方镜像变量**(推荐):`$env:TAURI_BUNDLER_TOOLS_GITHUB_MIRROR = 'https://ghfast.top/'`
     (bundler 下载工具时把 GitHub URL 重写到该前缀,一次设置全部生效);
   - **离线预置**(内网机):手动下载后摆成如下结构,bundler 检测到就不再联网:

     ```text
     %LOCALAPPDATA%\tauri\NSIS\
     ├── makensis.exe、Bin\、Plugins\ …   ← nsis-3.11.zip 解压内容
     └── Plugins\x86-unicode\
         ├── ApplicationID.dll            ← NSIS-ApplicationID.zip(binary-releases 仓库)
         └── additional\
             └── nsis_tauri_utils.dll     ← tauri-apps/nsis-tauri-utils releases
     ```
5. **WebView2**:安装器默认 `downloadBootstrapper`(装机时联网拉起,Win11 基本已自带无感);
   内网离线装机改 `webviewInstallMode.type` 为 `offlineInstaller` 或 `embedBootstrapper`(包体积增大);
6. **产物路径**:`desktop\target\release\bundle\nsis\{productName}_{version}_x64-setup.exe`,
   即 `InSAR-Agent_0.1.0_x64-setup.exe`(productName/version 取自 `tauri.conf.json`)。

## 4. 中文安装界面

`bundle.windows.nsis.languages = ["SimpChinese"]`(NSIS 语言名,注意不是 zh-CN):

- 只配一种语言时,安装/卸载界面直接中文,无需选择;
- `displayLanguageSelector = false`:不弹语言选择框(多语言时才有意义);
- Tauri 自定义文案(如 WebView2 下载提示)官方已带简中翻译,若发现漏翻可用
  `customLanguageFiles` 指定自定义 `.nsh` 覆盖;
- 安装包文件名、开始菜单项显示 `productName`(`InSAR-Agent`);安装器内的产品描述文案
  取 `shortDescription`(snippet 已给中文)。

## 5. 默认安装目录 E 盘

Tauri 的 `NsisConfig` **没有**"默认安装目录"配置项。官方模板 `.onInit` 的默认逻辑:

```text
installMode = currentUser → $LOCALAPPDATA\InSAR-Agent(C 盘用户目录)
installMode = perMachine  → $PROGRAMFILES64\InSAR-Agent(需管理员)
二次安装 → 恢复上次安装位置(注册表)
```

三种把默认目录改到 E 盘的路径,按侵入度排序:

1. **不改模板**:NSIS 目录选择页默认开启,安装者手动改成 `E:\InSAR-Agent`(演示机一次性成本);
   二次安装会记住上次位置;
2. **命令行/静默安装**:`InSAR-Agent_0.1.0_x64-setup.exe /D=E:\InSAR-Agent`
   (NSIS 内建参数;`/D=` 必须是最后一个参数、路径含空格也**不能**加引号);
3. **自定义模板(真·默认 E 盘)**:
   1. 从与 CLI 版本一致的 tag 取官方模板(模板与 bundler 强耦合,**必须对版本**):
      `https://github.com/tauri-apps/tauri/blob/tauri-cli-v{VERSION}/crates/tauri-bundler/src/bundle/windows/nsis/installer.nsi`
      存为 `desktop\bundle\installer.nsi`;
   2. 找到 `.onInit` 里 currentUser 分支的默认目录行:

      ```nsis
      !else if "${INSTALLMODE}" == "currentUser"
        StrCpy $INSTDIR "$LOCALAPPDATA\${PRODUCTNAME}"
      !endif
      ```

      改为"E 盘存在则默认 E 盘,否则回退原逻辑":

      ```nsis
      !else if "${INSTALLMODE}" == "currentUser"
        ${If} ${FileExists} "E:\*.*"
          StrCpy $INSTDIR "E:\${PRODUCTNAME}"
        ${Else}
          StrCpy $INSTDIR "$LOCALAPPDATA\${PRODUCTNAME}"
        ${EndIf}
      !endif
      ```

   3. conf 的 nsis 段加 `"template": "bundle/installer.nsi"`(路径相对 `desktop\`);
   4. 维护提醒:升级 tauri-cli 后需重新 diff 官方模板,把改动重新套上。

推荐:讲师演示/交付场景用方案 3 一次做死;赶时间先用方案 1/2 顶住。

## 6. 桌面快捷方式

官方模板已内置,无需配置:

- **交互式安装**:完成页有"创建桌面快捷方式"复选框(简中文案),勾选即建
  `$DESKTOP\InSAR-Agent.lnk`;开始菜单快捷方式总是创建;
- **静默/被动安装**(`/S`、`/P`):跳过完成页,**自动**创建桌面快捷方式;
- 安装器参数 `/NS` 可整体禁用快捷方式创建;卸载时自动清理两处快捷方式;
- 如需"交互式安装也强制建桌面快捷方式"(不给用户勾选机会),用
  `nsis.installerHooks` 指向一个 `.nsh`,在 `NSIS_HOOK_POSTINSTALL` 宏里补:

  ```nsis
  !macro NSIS_HOOK_POSTINSTALL
    CreateShortcut "$DESKTOP\${PRODUCTNAME}.lnk" "$INSTDIR\${MAINBINARYNAME}.exe"
  !macroend
  ```

## 7. `tauri.bundle.snippet.json` 合入说明(README 段落)

JSON 不支持注释,合入位置与规则统一写在这里:

- snippet 的顶层 `bundle` 键 = `desktop\tauri.conf.json` 顶层 `bundle` 键的**草案**;
- 当前 conf 已有 `bundle: { active: false, icon: [...] }`,合入 = **逐键深合并**:
  - `active`:`false` → `true`(打包总开关);
  - `targets`/`publisher`/`shortDescription`/`longDescription`/`windows`:conf 中尚无,整键拷入;
  - `icon`:snippet 与 conf 完全一致,保持不变即可;
- **不要**动 conf 的 `productName` / `identifier` / `version` / `app`(归另一分支管);
- `icon`、`installerIcon`、`template` 等相对路径均相对 `desktop\`(conf 所在目录);
- 合入后自检:`.tools\tauri-cli\cargo-tauri.exe build`(在 `desktop\` 下执行)或直接跑
  `scripts\build_desktop.ps1`,看 `desktop\target\build-logs\tauri-build-*.log`。

## 8. 一键构建脚本

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build_desktop.ps1              # 全流程
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build_desktop.ps1 -CargoOnly   # 只编译不打包
```

- 前置检查:cargo 必须存在(缺失打印 rsproxy/rustup 指引后退出);tauri-cli 可选
  (按 `.tools\tauri-cli\` → PATH 查找,找不到只编译并打印获取指引);`icons\icon.ico`
  缺失时自动跑 `make_icons.py` 生成占位图;
- `cargo build --release` 在 `desktop\` 下执行,stdout/stderr 全部重定向到
  `desktop\target\build-logs\cargo-build-*.log`;找到 tauri-cli 时继续 `tauri build`
  (日志 `tauri-build-*.log`);
- 全程进程内执行(`&` 调用),不使用 `Start-Process`,不弹任何新窗口;
- 退出码:`0` 成功 / `1` 前置缺失 / `2` cargo 编译失败 / `3` tauri build 失败(cargo 已成功)。

## 9. 现状与缺口(截至本分支)

- [x] cargo release 编译链路已验证:`insar-agent-desktop.exe`(约 8 MB)一键产出;
- [x] tauri-cli 获取脚本、bundle 配置草案、本文档;
- [ ] `bundle` 段尚未合入 `tauri.conf.json`(conf 归另一分支,按 §7 合入后 `tauri build` 才会出安装包);
- [ ] 正式图标:当前为脚本占位图,发布前用 `cargo-tauri icon` 从 1024px 设计稿生成;
- [ ] 首次 NSIS 工具链下载依赖网络(§3-4 的镜像变量或离线预置);
- [ ] "默认 E 盘"的自定义模板未落地(方案与关键改动行见 §5,需在合入 conf 的同一分支做);
- [ ] 安装器只装桌面壳:Python 后端仍需宿主环境(`pip install -e .`),v1 环境向导 / v2 PyInstaller
    冻结见 `desktop\README.md` §打包路线与 `docs\OPTIMIZATION.md` §4;
- [ ] 代码签名(signtool/证书)与自动更新(updater)未纳入本期。
