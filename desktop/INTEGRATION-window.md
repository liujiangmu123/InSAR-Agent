# 桌面窗口体验模块 · 集成说明

本分支**只新增文件 + Cargo.toml 追加依赖**,不触碰 main.rs / tauri.conf.json /
既有 prototype 文件。下面的集成改动归属对应文件的负责分支,照抄即可。

新增文件:

| 文件 | 作用 |
| --- | --- |
| `desktop/src/window_state.rs` | 窗口位置/尺寸/最大化状态持久化(%APPDATA%\insar-agent\window.json,原子写;分辨率变化越界自动校正) |
| `desktop/src/shortcuts.rs` | 全局快捷键骨架:Ctrl+Shift+I 显示/隐藏主窗 |
| `prototype/js/titlebar.js` | 运行状态 → document.title 同步(10s 轮询 /api/state,浏览器/桌面通用) |
| ~~`desktop/src/bin/window_check.rs`~~ | 编译自检 bin,**已随集成移除**:main.rs 已声明 `mod shortcuts; mod window_state;`,该 bin 冗余且会干扰打包的主二进制判定(NSIS 曾误把它当主程序) |

Cargo.toml 追加的依赖:`serde`(derive)、`serde_json`、`tauri-plugin-global-shortcut = "2"`。

## 1. desktop/src/main.rs(mod 声明 + setup 钩子两行)

文件顶部(`use` 块附近)加 mod 声明:

```rust
mod shortcuts;
mod window_state;
```

`tauri::Builder::default().setup(...)` 闭包内(现有
`std::thread::spawn(move || boot(handle, port));` 之后)加两行:

```rust
window_state::attach_when_ready(app.handle()); // main 窗口出现后:恢复上次几何 + 持续追踪
shortcuts::setup_shortcuts(app.handle()).ok(); // Ctrl+Shift+I 显示/隐藏主窗;失败不阻断启动
```

说明:

- **为什么不用 `restore(&window)` / `track(&window)` 直接写在 setup 里**:
  main 窗口由 `boot()` 在后台线程异步创建(要先等 sidecar 健康检查),
  setup 时 `app.get_webview_window("main")` 必然是 `None`。
  `attach_when_ready` 内部轮询等窗口出现(100ms × 最多 60s,覆盖健康检查
  30s 上限),再依次调用 `window_state::restore(&window)` +
  `window_state::track(&window)`。启动失败走诊断窗时线程静默退出。
- 若希望「创建即恢复」零闪动,可把 `restore`/`track` 两行挪进
  `open_window` 的 build 成功分支(归属方自行决定,接口不变)。
- `setup_shortcuts` 内部用 `app.plugin(...)` 动态注册
  tauri-plugin-global-shortcut,**不需要**改 Builder 链。
- 状态文件:`%APPDATA%\insar-agent\window.json`(先写 `.tmp` 再 rename 的
  原子写;文件损坏/缺失时静默回退默认 1440×900)。

## 2. prototype/index.html(一行)

`<script type="module" src="js/app.js"></script>` 之后加:

```html
<script type="module">import { initTitleSync } from './js/titlebar.js'; initTitleSync();</script>
```

效果:

- 浏览器标签页:「运行中 · 第 6 步 · InSAR-Agent · 工作区」/「已完成 · …」/「失败 · …」;
- 桌面(WebView2)额外加状态点前缀:`●` 运行中 / `○` 非运行;
- 会话 id 自动解析:URL `?session=` → 前端状态镜像 `S.sessionId`(动态 import
  `./state.js`,同实例零副作用);
- 后端不可达 / `file://` 演示模式 / 尚无 run:静默还原原始标题,不打扰。

### 可选增强(归属 main.rs 分支,非必需)

Tauri 2 不会自动把 document.title 同步到原生窗口标题。如需原生标题栏
也跟着变,在 `open_window` 的 `WebviewWindowBuilder` 链上加一行:

```rust
.on_document_title_changed(|window, title| { let _ = window.set_title(&title); })
```

## 3. 公开接口一览

```rust
// desktop/src/window_state.rs
pub fn restore(window: &tauri::WebviewWindow);          // 应用上次保存的位置/尺寸/最大化
pub fn track(window: &tauri::WebviewWindow);            // 注册 WindowEvent 监听,变化节流落盘
pub fn attach_when_ready(app: &tauri::AppHandle);       // setup 便捷入口:等 main 窗口出现后 restore+track

// desktop/src/shortcuts.rs
pub fn setup_shortcuts(app: &tauri::AppHandle) -> Result<(), Box<dyn std::error::Error>>;
```

```js
// prototype/js/titlebar.js
export function initTitleSync(opts = {});
// opts.session    固定会话 id(默认自动解析)
// opts.intervalMs 轮询间隔,默认 10000
// 返回:停止函数(clearInterval + 还原原始标题);重复调用幂等
```

## 4. 验证方式

- 编译自检:`cd desktop && cargo build`(两个模块已由 main.rs `mod` 声明
  直接编入,build 即类型检查,无需 cargo run);
- 标题同步(浏览器即可):起后端后打开
  `http://127.0.0.1:8873/?session=<会话id>`,发起一次运行,约 10s 内标签页
  标题出现「运行中 · 第 N 步」,结束后变「已完成」/「失败」。
