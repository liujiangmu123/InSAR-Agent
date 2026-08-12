# 托盘 + 单实例:集成说明(给 main.rs 维护者)

交付物(本分支只加文件,不碰 `main.rs` / `tauri.conf.json`):

| 文件 | 内容 |
|---|---|
| `src/tray.rs` | 系统托盘:图标、菜单(显示主窗口 / 隐藏 / 打开数据目录 / 退出)、左键单击显示主窗口、「关闭窗口 → 最小化到托盘」(可配置) |
| `src/singleton.rs` | 单实例锁(tauri-plugin-single-instance):第二实例启动时前置已有窗口 |
| `src/lib.rs` | 仅为让上述模块在未接线时也参与 `cargo build` 编译检查;接线后可留可删 |
| `Cargo.toml` | 依赖增量(见下),`Cargo.lock` 已随之更新 |

## 依赖增量(已完成,无需再动)

```toml
tauri = { version = "2", features = ["tray-icon"] }   # 原 features = []
tauri-plugin-single-instance = "2"                     # 新增
```

## main.rs 最小集成 diff(共 5 行)

```diff
 use tauri::{AppHandle, RunEvent, WebviewUrl, WebviewWindowBuilder};
+
+mod singleton;
+mod tray;
```

```diff
 fn main() {
     ...
-    let app = tauri::Builder::default()
+    let app = singleton::ensure_single_instance(tauri::Builder::default())
         .setup(move |app| {
             let handle = app.handle().clone();
+            tray::setup_tray(app.handle())?;
             // 健康检查最长 30 秒,放后台线程,避免卡死事件循环
             std::thread::spawn(move || boot(handle, port));
             Ok(())
         })
```

要点:

- `ensure_single_instance` 包装 `Builder`,**必须在其他插件之前**(官方对
  single-instance 的要求);当前 main.rs 没有别的插件,包在 `default()` 上即可。
- `setup_tray(app.handle())` 在 setup 里调用一次。主窗口那会儿还没创建
  (健康检查通过后才开窗)没有关系:「关闭到托盘」钩子经由 window_created
  回调,在 `"main"` 窗口诞生那一刻自动挂上。
- `--smoke` 自检路径在 Builder 之前就 `exit`,不经过托盘/单实例,行为不变。

## 接线后的行为语义

- **关闭主窗口(X)** → 窗口隐藏到托盘,应用与 Python sidecar 继续运行
  (对长时间跑批的产品语义友好);托盘菜单「退出」→ `app.exit(0)` →
  `RunEvent::Exit` → main.rs 里现有的 `kill_sidecar()` 照常执行,断点续跑
  语义不变。
- **诊断窗口(label = "diagnostics")不受影响**:关闭即退出,和现在一样。
- **第二实例**:进程直接不进事件循环,argv 转发给第一实例,第一实例把主
  窗口显示 / 还原 / 前置(即使已隐藏到托盘也能唤回)。
- **托盘菜单**:
  - 显示主窗口:show + unminimize + focus("main" 不在时退化为任意已有窗口);
  - 隐藏:隐藏窗口(托盘图标常驻,可随时唤回);
  - 打开数据目录:dev 构建 = 仓库根(与 main.rs `repo_root()` 同语义,
    runs/、workspace/ 所在地);打包分发后回退到应用数据目录;
  - 退出:结束应用(连带 kill sidecar)。
- **托盘图标**:优先取窗口默认图标;`icons/` 下占位图未生成时用代码内联的
  同款图案(深蓝底 + 对角条纹)兜底,托盘永远可见。

## 可配置项

| 配置 | 作用 |
|---|---|
| `INSAR_DESKTOP_CLOSE_TO_TRAY=0`(或 `false`/`off`/`no`) | 关闭「关闭到托盘」:点 X 直接退出,恢复旧行为 |
| `tray::set_close_to_tray(bool)` | 运行时改写同一开关(为将来的设置页预留) |

## 冲突提示

- `Cargo.lock` 因新增依赖而更新;若与其他分支冲突,合并后在 `desktop/`
  重新 `cargo build` 让 cargo 重解析即可。
- Windows 构建仍需先生成图标:`python icons/make_icons.py`(既有要求,
  与本模块无关;托盘本身不依赖这些文件)。
