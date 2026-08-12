# InSAR-Agent 桌面自动更新方案(Tauri 2 updater)

> 状态:方案文档。桌面壳当前 `bundle.active = false`(只出裸 exe,开发用),
> 自动更新在启用打包(README「打包路线」)后接入。`tauri.conf.json` 归另一分支
> 管理,本文只给出需要合入的配置片段,不直接改动该文件。

## 0. 总览:两条通道,一个版本号

| 通道 | 机制 | 职责 |
|---|---|---|
| 壳更新(唯一安装通道) | tauri-plugin-updater | 签名校验、下载、安装、重启 |
| 新版提示(只读探针) | 后端 `GET /api/version/check` | 告诉 UI「有没有新版」,不下载不安装 |

- **后端与前端资源随壳版本走**:打包路线 v2 用 PyInstaller 把后端(`insar_agent`)
  冻结进安装包,`prototype/` 静态 UI 同样作为资源打包 —— 壳更新 = 后端 + UI
  一起更新,不做独立热更新。因此产品只有一个版本号,四处必须同步:
  `pyproject.toml`、`src/insar_agent/__init__.py`(`__version__`)、
  `desktop/tauri.conf.json`、`desktop/Cargo.toml`。
- 引擎 conda 环境(ISCE2 / MintPy,2GB+)不进安装包,不参与本机制
  (`docs/OPTIMIZATION.md` §4)。

## 1. 依赖与代码接入(desktop/)

`desktop/Cargo.toml` 新增依赖:

```toml
[dependencies]
tauri-plugin-updater = "2"
```

`desktop/src/main.rs` 注册插件并在启动后台线程里检查更新。本壳没有 JS 前端
(WebView 加载的是 `http://127.0.0.1:{port}/` 外部页面,不走 Tauri IPC),
所以用 **Rust 侧 API** 编排,无需在 capabilities 里开放 `updater:default`,
也避免了给远程 URL 开 IPC 权限的安全面:

```rust
use tauri_plugin_updater::UpdaterExt;

let app = tauri::Builder::default()
    .plugin(tauri_plugin_updater::Builder::new().build())
    .setup(move |app| {
        let handle = app.handle().clone();
        tauri::async_runtime::spawn(async move {
            // 静默检查;找到更新后先提示用户(对话框/托盘),确认再装
            if let Ok(updater) = handle.updater() {
                if let Ok(Some(update)) = updater.check().await {
                    // update.version / update.body(notes)
                    // update.download_and_install(|_, _| {}, || {}).await
                    // 安装完成后由用户选择重启(NSIS passive 模式会自动接管)
                }
            }
        });
        Ok(())
    })
    // ... 现有 boot/sidecar 逻辑不变
```

> dev 模式(`cargo run`)下 updater 不工作也不需要工作;只在打包分发版里生效。

## 2. 密钥生成与保管(tauri signer)

本工程是纯 cargo 工程(无 node),用 cargo 版 tauri-cli:

```powershell
cargo install tauri-cli --version "^2"        # 一次性
cargo tauri signer generate -w $env:USERPROFILE\.tauri\insar-agent.key
```

输出两样东西:

- **私钥** `insar-agent.key`(可设密码):绝不入库(根 `.gitignore` 已忽略
  `*.key`),离线备份至少两份 —— **丢私钥 = 已发布用户永远收不到后续更新**;
- **公钥**(base64 一行):放进 `tauri.conf.json` 的 `plugins.updater.pubkey`,
  可公开。

构建时通过环境变量注入私钥(签名发生在 `cargo tauri build` 打包阶段,
产出 `.sig` 文件):

```powershell
$env:TAURI_SIGNING_PRIVATE_KEY = Get-Content $env:USERPROFILE\.tauri\insar-agent.key -Raw
$env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = "<私钥密码,未设则空串>"
cargo tauri build
```

CI 场景把这两个值放 secret,不落盘。

## 3. tauri.conf.json 配置片段(由持有该文件的分支合入)

```json
{
  "bundle": {
    "active": true,
    "createUpdaterArtifacts": true
  },
  "plugins": {
    "updater": {
      "endpoints": [
        "https://updates.example.com/insar-agent/{{target}}/{{arch}}/{{current_version}}"
      ],
      "pubkey": "<cargo tauri signer generate 输出的公钥>",
      "windows": { "installMode": "passive" }
    }
  }
}
```

说明:

- `createUpdaterArtifacts: true`:`cargo tauri build` 在产出安装器
  (NSIS `*-setup.exe`)的同时生成同名 `*.sig` 签名文件;
- `endpoints` 支持模板变量 `{{target}}`(windows/linux/darwin)、`{{arch}}`、
  `{{current_version}}`;起步阶段直接填一个**静态 latest.json 的固定地址**即可
  (见 §4.1),不需要动态服务;
- `installMode: passive`:Windows 下静默安装但显示进度条。

## 4. 更新清单格式

### 4.1 Tauri 静态清单(latest.json,推荐起步)

托管在任意 HTTPS 静态存储(GitHub Releases / OSS),`endpoints` 指向它:

```json
{
  "version": "0.2.0",
  "notes": "修复 X;新增 Y",
  "pub_date": "2026-08-12T03:00:00Z",
  "platforms": {
    "windows-x86_64": {
      "signature": "<InSAR-Agent_0.2.0_x64-setup.exe.sig 的文件内容>",
      "url": "https://updates.example.com/insar-agent/InSAR-Agent_0.2.0_x64-setup.exe"
    }
  }
}
```

规则:`version` 必须比已装版本大(SemVer 比较);`signature` 是 `.sig` 文件的
**内容**而非路径;`platforms` 键为 `{os}-{arch}`(如 `windows-x86_64`)。

### 4.2 动态服务端(可选进阶)

`GET {endpoint}` 根据 `{{target}}/{{arch}}/{{current_version}}` 决定响应:
有更新 → `200` + `{"version", "pub_date", "url", "signature", "notes"}`;
无更新 → `204 No Content`。静态清单不够用(灰度、按版本定向)时再上。

### 4.3 后端简化清单(INSAR_UPDATE_MANIFEST → /api/version/check)

后端探针端点读的是一个**更简单的**清单,发布脚本从 latest.json 顺手生成:

```json
{ "latest": "0.2.0", "notes": "修复 X;新增 Y", "url": "https://.../下载页或安装器" }
```

## 5. 与 /api/version、/api/version/check 的关系(职责边界)

| 端点 / 机制 | 做什么 | 不做什么 |
|---|---|---|
| `GET /api/version` | 运行时指纹:`version` / `git_head` / `python` / `platform` / `build.frozen` | 不联网 |
| `GET /api/version/check` | 读 `INSAR_UPDATE_MANIFEST` 清单,语义化比较 → `update_available` + `notes`/`url` | 不下载、不安装 |
| tauri-plugin-updater | 签名校验 + 下载 + 安装 + 重启,**唯一安装通道** | 不负责 UI 提示语义 |

典型流程:Web UI(`prototype/`,由后端服务)调 `/api/version/check` 显示
「发现新版本」横幅 → 用户确认 → 壳(Rust 侧)`updater.check()` +
`download_and_install()` → 重启进新版。没有壳的场景(纯浏览器开发模式)下,
`/api/version/check` 返回的 `url` 字段就是人工下载入口。

版本一致性诊断:壳更新后后端随包更新,理论上不会漂移;万一漂移(如用户用
`INSAR_PYTHON` 指了旧环境),壳可对比自身版本与 `GET /api/version` 的
`version` 字段并在诊断页提示。`build.frozen` 用于区分「冻结打包的后端」
(随壳走)与「源码/开发环境后端」(git_head 非空,更新靠 git)。

## 6. 发布流程(checklist)

1. **升版本**(四处同步):`pyproject.toml`、`src/insar_agent/__init__.py`、
   `desktop/tauri.conf.json`、`desktop/Cargo.toml`;
2. **构建 + 签名**:设 `TAURI_SIGNING_*` 环境变量后 `cargo tauri build`,
   取 `desktop/target/release/bundle/nsis/` 下的 `*-setup.exe` 与 `*.sig`;
3. **上传工件**到发布存储(GitHub Releases / OSS),工件永不删除(为回滚留路);
4. **最后切清单**(原子发布):更新 latest.json(§4.1)与简化清单(§4.3),
   清单一切换,更新即对全量用户生效;
5. **验收**:装上一版 → 启动应弹更新;配好 `INSAR_UPDATE_MANIFEST` 后
   `GET /api/version/check` 应返回 `update_available: true`;更新完成后
   `GET /api/version` 应报新版本号;
6. **回滚**:清单指回上一版工件即可(这也是工件永不删除的原因)。

## 7. 边界与注意

- dev 模式(`cargo run` / `bundle.active=false`)不走 updater;
- WebView2 Runtime 由系统 Evergreen 机制维护,不随包分发;
- 更新基础设施只需要「HTTPS 静态文件托管」,无需自建服务;
- 签名校验由插件强制执行:清单被篡改 / 工件不匹配公钥时拒绝安装。
