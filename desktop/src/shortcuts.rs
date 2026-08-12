//! 全局快捷键模块(骨架):目前只挂一个键 —— Ctrl+Shift+I 显示/隐藏主窗口。
//!
//! 依赖 tauri-plugin-global-shortcut(Cargo.toml 已追加)。plugin 在本函数内
//! 通过 `app.plugin(...)` 动态注册,因此 main.rs 不需要改 Builder 链,
//! 只需在 setup 钩子里调用一次 [`setup_shortcuts`]。
//!
//! 后续加键:在 [`setup_shortcuts`] 里继续 `app.global_shortcut().on_shortcut(...)`
//! 即可,plugin 只注册一次。

use tauri::{AppHandle, Manager};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};

/// 主窗口 label,与 main.rs `open_main_window` 保持一致。
const MAIN_WINDOW_LABEL: &str = "main";

/// 注册全局快捷键(setup 钩子中调用一次)。
///
/// 失败(快捷键被其它程序占用、plugin 重复注册等)通过返回值上抛,
/// 由调用方决定处置——建议 `.ok()` 忽略:快捷键属增强功能,不该阻断启动。
pub fn setup_shortcuts(app: &AppHandle) -> Result<(), Box<dyn std::error::Error>> {
    app.plugin(tauri_plugin_global_shortcut::Builder::new().build())?;

    let toggle = Shortcut::new(Some(Modifiers::CONTROL | Modifiers::SHIFT), Code::KeyI);
    app.global_shortcut().on_shortcut(toggle, |app, _shortcut, event| {
        // 只响应按下沿;Released 忽略,避免一次按键触发两次切换
        if event.state == ShortcutState::Pressed {
            toggle_main_window(app);
        }
    })?;
    Ok(())
}

/// 显示/隐藏主窗口:可见且未最小化 → 隐藏;否则还原、显示并聚焦。
/// 主窗口尚未创建(启动早期/诊断模式)时静默不动作。
fn toggle_main_window(app: &AppHandle) {
    let window = app
        .get_webview_window(MAIN_WINDOW_LABEL)
        .or_else(|| app.webview_windows().into_values().next());
    let Some(window) = window else { return };

    let minimized = window.is_minimized().unwrap_or(false);
    if window.is_visible().unwrap_or(false) && !minimized {
        let _ = window.hide();
    } else {
        if minimized {
            let _ = window.unminimize();
        }
        let _ = window.show();
        let _ = window.set_focus();
    }
}
