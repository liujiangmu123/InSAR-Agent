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

## 三、运行时前提(壳分支的两处配置,缺一 JS 桥不通)

前端是零依赖 ES Module(没有 npm 的 @tauri-apps/api),且主窗口通过
`WebviewUrl::External(http://127.0.0.1:<port>)` 从后端加载 —— Tauri 2 对这种
「远程 origin」默认既不注入 `window.__TAURI__`,也不放行 IPC。因此需要:

1. `tauri.conf.json` 开启全局 API 注入:

```json
{ "app": { "withGlobalTauri": true } }
```

2. 新建 capability 放行 127.0.0.1 origin 的 IPC。`desktop/capabilities/` 目录
   下的文件会被自动加载(当前 tauri.conf.json 未显式引用 capabilities,即
   「全部启用」语义),新建 `desktop/capabilities/remote-ui.json`:

```json
{
  "$schema": "../gen/schemas/desktop-schema.json",
  "identifier": "remote-ui",
  "description": "允许本地后端页面(127.0.0.1)调用桌面壳 command",
  "windows": ["main"],
  "remote": { "urls": ["http://127.0.0.1:*", "http://localhost:*"] },
  "permissions": ["core:default"]
}
```

   说明:自定义 command(本模块三个)不在 ACL 权限清单管辖内,capability 在
   这里的作用是把 IPC 通道对该远程 origin 打开;`core:default` 顺带放行 event
   等核心 API。端口随 INSAR_PORT 动态,故用 `:*` 通配,确认固定 8873 也可写死。
   ⚠ 此条未经运行时验证(本分支不动壳、不 cargo run),集成后请以
   `window.__TAURI__` 是否注入为准做一次冒烟。

3. 依赖已就位:Cargo.toml 已追加 `rfd`、`serde_json`(本分支完成,
   Cargo.lock 同步更新)。

## 四、为什么不改 main.rs 也能编译自检

新增的 `src/lib.rs` 声明 `pub mod commands;`。同一 package 里 lib 与 bin 是两个
独立 crate:`cargo build` 把 commands.rs 编进 lib 目标完成类型检查;main.rs 的
bin 目标不引用 lib,行为零变化。集成时 main.rs 写 `mod commands;` 是把同一份
源文件编进 bin crate,与 lib 目标互不干扰 —— lib.rs 无需删除,留作后续模块的
常驻自检入口。
