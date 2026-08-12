// 单实例模块(tauri-plugin-single-instance)。
//
// 语义:第二个实例启动时不会真的跑起来 —— 插件在它进入事件循环前就把
// argv/cwd 转发给已运行的第一实例并退出;第一实例在回调里把已有主窗口
// 显示、还原并前置(配合托盘的「关闭到托盘」:窗口即使隐藏着也能被唤回)。
//
// Windows 下插件基于命名互斥体(CreateMutexW,名字取自 tauri.conf.json 的
// identifier)+ 本地管道转发参数,跨平台且经受过生产验证,故不手写 winapi。
//
// 集成(见 desktop/INTEGRATION-tray.md):必须在其他插件之前注册,推荐写法:
//   let app = singleton::ensure_single_instance(tauri::Builder::default())
//       .setup(...)...

use tauri::{AppHandle, Manager, Runtime};

/// 给 Builder 注册单实例守卫:第二实例启动时,前置已有窗口。
///
/// 官方建议 single-instance 是第一个注册的插件,因此接口设计为包装
/// `tauri::Builder`,在 `setup`/其他 `.plugin(...)` 之前调用。
pub fn ensure_single_instance<R: Runtime>(builder: tauri::Builder<R>) -> tauri::Builder<R> {
    builder.plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
        bring_main_window_to_front(app);
    }))
}

/// 显示、还原并聚焦已有窗口:优先 "main"(与 main.rs 创建的主窗口一致),
/// 还没创建时退化为任意已有窗口(如诊断窗口);一个都没有则静默跳过
/// (后端仍在启动中,窗口马上会出现,无需特殊处理)。
fn bring_main_window_to_front<R: Runtime>(app: &AppHandle<R>) {
    if let Some(window) = app
        .get_webview_window("main")
        .or_else(|| app.webview_windows().into_values().next())
    {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
    }
}
