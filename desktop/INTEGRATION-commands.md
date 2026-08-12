# desktop 命令模块集成说明(commands.rs → main.rs)

> 状态:`src/commands.rs` 已随本分支落库,并通过 `src/lib.rs` 库目标参与
> `cargo build` 完成编译自检 —— 零接触 main.rs / tauri.conf.json。
> 这两个文件归壳分支维护,按下文改动即可点亮 JS 桥
> (前端侧见 `prototype/js/desktop.js` 与 `docs/INTEGRATION-desktop-bridge.md`)。

## 一、main.rs 的两处一行改动

1. 模块声明(放在文件顶部 `#![cfg_attr(...)]` 之后):

```rust
mod commands;
```

2. builder 链上注册 invoke_handler(一行):

```rust
.invoke_handler(commands::handlers())
```

对应当前 main.rs 的伪 diff:

```diff
 #![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

+mod commands;
+
 use std::fs::File;
@@
     let app = tauri::Builder::default()
+        .invoke_handler(commands::handlers())
         .setup(move |app| {
             let handle = app.handle().clone();
```

### 备选:与其他 command 合并注册(宏写法)

`invoke_handler` 只能调用一次,后一次会整体覆盖前一次。main.rs 将来若有
自己的 command,不要再调一次 `invoke_handler`,改用宏一行列全:

```rust
.invoke_handler(tauri::generate_handler![
    commands::pick_directory,
    commands::open_path,
    commands::app_info,
    // 其他 command …
])
```

## 二、command 清单

| command | Rust 签名 | 前端调用 | 返回 |
|---|---|---|---|
| `pick_directory` | `async fn (title: String) -> Option<String>` | `invoke('pick_directory', { title })` | 选中目录绝对路径;取消 = `null` |
| `open_path` | `fn (path: String)` | `invoke('open_path', { path })` | 无(尽力而为;传文件路径时打开其所在目录,失败仅记 stderr) |
| `app_info` | `fn () -> serde_json::Value` | `invoke('app_info')` | `{ name, version, tauriVersion, platform, arch, debug, repoRoot }` |

实现要点:`pick_directory` 走 rfd 异步对话框(独立线程,不阻塞事件循环);
`open_path` 用 `explorer` + `spawn` + `CREATE_NO_WINDOW`(与 spawn_sidecar 同款,
不弹黑窗、不阻塞);`app_info().repoRoot` 的判据与 main.rs `repo_root()` 兼容
(`src/insar_agent` / `prototype/` / `.git` 向上搜索)。

## 三、运行时前提(壳分支的三处配置,缺一 JS 桥不通;已实机验证)

前端是零依赖 ES Module(没有 npm 的 @tauri-apps/api),且主窗口通过
`WebviewUrl::External(http://127.0.0.1:<port>)` 从后端加载 —— Tauri 2 对这种
「远程 origin」默认既不注入 `window.__TAURI__`,也不放行 IPC。因此需要:

1. `tauri.conf.json` 开启全局 API 注入:

```json
{ "app": { "withGlobalTauri": true } }
```

2. `build.rs` 用 `app_manifest` 为自定义 command 生成 ACL 权限(缺了这步,
   capability 无权限可授,远程页面 invoke 一律报
   `"<cmd> not allowed. Plugin not found"` —— 2026-08-12 实测踩坑):

```rust
tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
    tauri_build::AppManifest::new().commands(&["pick_directory", "open_path", "app_info"]),
))
.expect("tauri-build 失败");
```

   生成的权限标识符是 kebab-case(见 `gen/schemas/desktop-schema.json`):
   `allow-pick-directory` / `allow-open-path` / `allow-app-info`(另有同名 deny-*)。

3. capability 放行 127.0.0.1 origin 的 IPC,并显式授权三个 command。
   `desktop/capabilities/` 目录下的文件会被自动加载(当前 tauri.conf.json 未
   显式引用 capabilities,即「全部启用」语义),见 `capabilities/remote-ui.json`:

```json
{
  "$schema": "../gen/schemas/desktop-schema.json",
  "identifier": "remote-ui",
  "description": "允许本地后端页面(127.0.0.1)调用桌面壳 command",
  "windows": ["main"],
  "remote": { "urls": ["http://127.0.0.1:*", "http://localhost:*"] },
  "permissions": [
    "core:default",
    "allow-pick-directory",
    "allow-open-path",
    "allow-app-info"
  ]
}
```

   说明:与本文旧版「自定义 command 不在 ACL 权限清单管辖内」的推断相反,
   实测表明远程 origin 下 app command 同样受 ACL 管辖 —— capability 只写
   `core:default` 时注入正常但 invoke 全部被拒;`core:default` 负责 event 等
   核心 API。端口随 INSAR_PORT 动态,故用 `:*` 通配,确认固定 8873 也可写死。

4. 依赖已就位:Cargo.toml 已追加 `rfd`、`serde_json`(本分支完成,
   Cargo.lock 同步更新)。

### 实测结论矩阵(2026-08-12,Tauri 2.11.5 / Windows / dev 构建实机验证)

验证方法:`WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=9333`
启动 debug exe,用 `desktop/tools/verify_js_bridge.py`(CDP Runtime.evaluate,
等价 F12 控制台)对真实窗口逐项取证;`pick_directory` 的原生对话框以 Win32
WM_CLOSE 自动取消,等价用户按 Esc。全程未改动 desktop.js(零诊断代码入侵)。

| # | 验证项 | 实测结果 | 证据 |
|---|---|---|---|
| a | 主窗(remote `http://127.0.0.1:<port>`)注入 `window.__TAURI__` | ✅ 注入 | `typeof __TAURI__ === 'object'`,含 app/core/event/window 等 12 个键;`core.invoke` 是 function;`__TAURI_INTERNALS__` 同在 |
| b | `desktop.js` 桌面判定分支走通 | ✅ 走通 | 真实主窗内 `import('/js/desktop.js')` 后 `isDesktop() === true`;`appInfo()` 经 safeInvoke 返回 `{ name: "insar-agent-desktop", tauriVersion: "2.11.5", platform: "windows", debug: true, repoRoot: <仓库根>, ... }` |
| c | `commands.rs` 三命令从 JS invoke 可用 | ✅ 可用(修复后) | `app_info` 返回完整对象;`open_path('')` resolve `null`(空路径忽略语义);`pick_directory` 弹出真实对话框,自动取消后 resolve `null`(取消语义) |
| d | 诊断窗(label `diagnostics`)拿不到 IPC | ✅ 隔离生效 | `__TAURI__` 对象同样被注入(withGlobalTauri 全局注入,注入≠授权),但 invoke 被拒:`app_info not allowed on window "diagnostics" ... allowed on: [windows: "main", URL: http://127.0.0.1:*] ...` —— capability 的 `windows: ["main"]` 边界起效 |

修复前的精确失败层(仅配 `core:default`、无 app_manifest 时):a/b 已通过,
c 三命令均报 `"<cmd> not allowed. Plugin not found"` —— 即 IPC 通道通、ACL
授权缺失;按上文第 2、3 点补配后 c 转绿。复跑取证:

```powershell
$env:INSAR_PORT="18973"
$env:WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS="--remote-debugging-port=9333"
& <target>\debug\insar-agent-desktop.exe   # 另开一窗
.venv\Scripts\python.exe desktop\tools\verify_js_bridge.py verify-main --url-prefix http://127.0.0.1:18973
# 诊断窗隔离项:INSAR_PYTHON 指向 cmd.exe 再启动(sidecar 秒退→诊断窗),然后
.venv\Scripts\python.exe desktop\tools\verify_js_bridge.py verify-diag --exclude-prefix http://127.0.0.1:18973
```

## 四、为什么不改 main.rs 也能编译自检

新增的 `src/lib.rs` 声明 `pub mod commands;`。同一 package 里 lib 与 bin 是两个
独立 crate:`cargo build` 把 commands.rs 编进 lib 目标完成类型检查;main.rs 的
bin 目标不引用 lib,行为零变化。集成时 main.rs 写 `mod commands;` 是把同一份
源文件编进 bin crate,与 lib 目标互不干扰 —— lib.rs 无需删除,留作后续模块的
常驻自检入口。
