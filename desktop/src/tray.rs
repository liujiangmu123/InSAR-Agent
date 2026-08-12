// 系统托盘模块(tauri "tray-icon" feature)。
//
// 职责:
//   1. 托盘图标 + 菜单:显示主窗口 / 隐藏 / 打开数据目录 / 退出;
//   2. 左键单击托盘图标 → 显示并聚焦主窗口;
//   3. 「关闭窗口 → 最小化到托盘而非退出」(可配置,默认开启,仅对 label
//      为 "main" 的主窗口生效;诊断窗口 "diagnostics" 关闭仍走正常退出)。
//
// 主窗口由 main.rs 在后台健康检查通过后才动态创建(tauri.conf.json 中
// windows 为空),因此“关闭到托盘”不能在 setup 阶段直接挂到窗口上,而是
// 通过一个运行时注册的迷你插件在 window_created 回调里给新窗口挂钩子。
//
// 集成(见 desktop/INTEGRATION-tray.md):main.rs 的 Builder::setup 中调用
//   tray::setup_tray(app.handle())?;

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};

use tauri::image::Image;
use tauri::menu::{Menu, MenuEvent, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIcon, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Manager, Runtime, WebviewWindow, WindowEvent};

/// 托盘图标 ID(如需后续查找:`app.tray_by_id(TRAY_ID)`)。
pub const TRAY_ID: &str = "insar-agent-tray";

/// 主窗口 label,与 main.rs `open_main_window` 中创建的窗口一致。
pub const MAIN_WINDOW_LABEL: &str = "main";

/// 环境变量开关:设为 `0` / `false` / `off` / `no` 时,点关闭按钮直接退出
/// (恢复无托盘时代的行为);其余取值或未设置 = 关闭到托盘。
pub const CLOSE_TO_TRAY_ENV: &str = "INSAR_DESKTOP_CLOSE_TO_TRAY";

const MENU_SHOW: &str = "tray-show";
const MENU_HIDE: &str = "tray-hide";
const MENU_OPEN_DATA: &str = "tray-open-data";
const MENU_QUIT: &str = "tray-quit";

/// 「关闭窗口时最小化到托盘」运行时开关(默认开启)。
static CLOSE_TO_TRAY: AtomicBool = AtomicBool::new(true);

/// 运行时改写「关闭到托盘」行为(如后续做设置页时调用)。
#[allow(dead_code)] // 对外配置接口,集成方不一定立刻用到
pub fn set_close_to_tray(enabled: bool) {
    CLOSE_TO_TRAY.store(enabled, Ordering::Relaxed);
}

/// 当前是否启用「关闭到托盘」。
pub fn close_to_tray_enabled() -> bool {
    CLOSE_TO_TRAY.load(Ordering::Relaxed)
}

/// 创建托盘图标与菜单,并注册「关闭主窗口 → 隐藏到托盘」钩子。
///
/// 在 `Builder::setup` 中调用一次;此时主窗口尚未创建也没关系 ——
/// 钩子经由 window_created 回调挂载,对之后创建的 "main" 窗口自动生效。
pub fn setup_tray<R: Runtime>(app: &AppHandle<R>) -> tauri::Result<()> {
    apply_env_config();

    let show = MenuItem::with_id(app, MENU_SHOW, "显示主窗口", true, None::<&str>)?;
    let hide = MenuItem::with_id(app, MENU_HIDE, "隐藏", true, None::<&str>)?;
    let open_data = MenuItem::with_id(app, MENU_OPEN_DATA, "打开数据目录", true, None::<&str>)?;
    let separator = PredefinedMenuItem::separator(app)?;
    let quit = MenuItem::with_id(app, MENU_QUIT, "退出", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&show, &hide, &open_data, &separator, &quit])?;

    let tooltip = app
        .config()
        .product_name
        .clone()
        .unwrap_or_else(|| "InSAR-Agent".into());

    TrayIconBuilder::with_id(TRAY_ID)
        .icon(tray_icon(app))
        .menu(&menu)
        .show_menu_on_left_click(false) // 左键留给「显示主窗口」,右键出菜单
        .tooltip(&tooltip)
        .on_menu_event(on_menu_event)
        .on_tray_icon_event(on_tray_icon_event)
        .build(app)?;

    install_close_to_tray_hook(app)?;
    Ok(())
}

fn apply_env_config() {
    if let Ok(value) = std::env::var(CLOSE_TO_TRAY_ENV) {
        let v = value.trim().to_ascii_lowercase();
        let disabled = matches!(v.as_str(), "0" | "false" | "off" | "no");
        CLOSE_TO_TRAY.store(!disabled, Ordering::Relaxed);
    }
}

// ---------------- 菜单与托盘事件 ----------------

fn on_menu_event<R: Runtime>(app: &AppHandle<R>, event: MenuEvent) {
    match event.id().as_ref() {
        MENU_SHOW => show_main_window(app),
        MENU_HIDE => hide_main_window(app),
        MENU_OPEN_DATA => open_data_dir(app),
        MENU_QUIT => app.exit(0), // 触发 RunEvent::Exit → main.rs 里统一 kill sidecar
        _ => {}
    }
}

fn on_tray_icon_event<R: Runtime>(tray: &TrayIcon<R>, event: TrayIconEvent) {
    // 左键单击 → 显示主窗口(Linux 的 appindicator 不派发点击事件,只有菜单)
    if let TrayIconEvent::Click {
        button: MouseButton::Left,
        button_state: MouseButtonState::Up,
        ..
    } = event
    {
        show_main_window(tray.app_handle());
    }
}

// ---------------- 窗口操作 ----------------

/// 优先取 "main" 窗口;尚未创建时退化为任意已有窗口(如诊断窗口),
/// 一个都没有(后端还在启动)则为 None,操作静默跳过。
fn target_window<R: Runtime>(app: &AppHandle<R>) -> Option<WebviewWindow<R>> {
    app.get_webview_window(MAIN_WINDOW_LABEL)
        .or_else(|| app.webview_windows().into_values().next())
}

/// 显示、还原并聚焦主窗口(从托盘恢复)。
pub fn show_main_window<R: Runtime>(app: &AppHandle<R>) {
    if let Some(window) = target_window(app) {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
    }
}

fn hide_main_window<R: Runtime>(app: &AppHandle<R>) {
    if let Some(window) = target_window(app) {
        let _ = window.hide();
    }
}

/// 「关闭到托盘」:主窗口由 main.rs 在健康检查后才创建,故通过运行时插件的
/// window_created 回调,在窗口诞生那一刻挂上 CloseRequested 钩子。
fn install_close_to_tray_hook<R: Runtime>(app: &AppHandle<R>) -> tauri::Result<()> {
    app.plugin(
        tauri::plugin::Builder::<R, ()>::new("insar-close-to-tray")
            .on_window_ready(|window| {
                if window.label() != MAIN_WINDOW_LABEL {
                    return; // 诊断窗口等保持“关了就退出”的默认语义
                }
                let app = window.app_handle().clone();
                window.on_window_event(move |event| {
                    if let WindowEvent::CloseRequested { api, .. } = event {
                        if close_to_tray_enabled() {
                            api.prevent_close();
                            if let Some(w) = app.get_webview_window(MAIN_WINDOW_LABEL) {
                                let _ = w.hide();
                            }
                        }
                    }
                });
            })
            .build(),
    )
}

// ---------------- 数据目录 ----------------

/// 数据目录:与 main.rs `repo_root()` 同一语义 ——
/// dev 构建下 desktop/ 的父目录即仓库根(runs/、workspace/、data/ 所在地);
/// 打包分发后该路径不存在 → 应用数据目录;最后兜底当前工作目录。
fn data_dir<R: Runtime>(app: &AppHandle<R>) -> PathBuf {
    if let Some(root) = Path::new(env!("CARGO_MANIFEST_DIR")).parent() {
        if root.join("src").join("insar_agent").is_dir() {
            return root.to_path_buf();
        }
    }
    if let Ok(dir) = app.path().app_data_dir() {
        return dir;
    }
    std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."))
}

fn open_data_dir<R: Runtime>(app: &AppHandle<R>) {
    let dir = data_dir(app);
    if let Err(e) = std::fs::create_dir_all(&dir) {
        eprintln!("[tray] 创建数据目录失败 {}:{e}", dir.display());
        return;
    }
    reveal_in_file_manager(&dir);
}

/// 用系统文件管理器打开目录(不引 opener 插件,保持零额外依赖)。
fn reveal_in_file_manager(path: &Path) {
    #[cfg(target_os = "windows")]
    let result = std::process::Command::new("explorer").arg(path).spawn();
    #[cfg(target_os = "macos")]
    let result = std::process::Command::new("open").arg(path).spawn();
    #[cfg(all(unix, not(target_os = "macos")))]
    let result = std::process::Command::new("xdg-open").arg(path).spawn();

    if let Err(e) = result {
        eprintln!("[tray] 打开数据目录失败 {}:{e}", path.display());
    }
}

// ---------------- 托盘图标 ----------------

/// 托盘图标:优先用窗口默认图标(tauri.conf.json bundle.icon 生成的上下文);
/// 图标文件未生成时(icons/ 下的占位图不入库,见 icons/make_icons.py),
/// 用代码内联的同款占位图案兜底,保证托盘永远可见。
fn tray_icon<R: Runtime>(app: &AppHandle<R>) -> Image<'static> {
    if let Some(icon) = app.default_window_icon() {
        // default_window_icon 借用自 app,拷贝像素拿到 'static 所有权
        return Image::new_owned(icon.rgba().to_vec(), icon.width(), icon.height());
    }
    fallback_icon()
}

/// 与 icons/make_icons.py `pixels()` 同款:深蓝底 + 两道对角亮蓝条纹 + 1px 亮边框。
fn fallback_icon() -> Image<'static> {
    const SIZE: usize = 32;
    const BG: [u8; 4] = [15, 43, 76, 255];
    const STRIPE: [u8; 4] = [47, 129, 247, 255];
    const BORDER: [u8; 4] = [232, 240, 254, 255];
    let stripe_w = (SIZE / 8).max(2) as i32;

    let mut rgba = Vec::with_capacity(SIZE * SIZE * 4);
    for y in 0..SIZE {
        for x in 0..SIZE {
            let color = if x == 0 || x == SIZE - 1 || y == 0 || y == SIZE - 1 {
                BORDER
            } else {
                let s = (x + y) as i32;
                if (s - SIZE as i32).abs() < stripe_w
                    || (s - (SIZE / 2) as i32).abs() < (stripe_w / 2).max(1)
                {
                    STRIPE
                } else {
                    BG
                }
            };
            rgba.extend_from_slice(&color);
        }
    }
    Image::new_owned(rgba, SIZE as u32, SIZE as u32)
}
