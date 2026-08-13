// 系统托盘模块(tauri "tray-icon" feature)。
//
// 职责:
//   1. 托盘图标 + 菜单:显示主窗口 / 隐藏 / 打开数据目录 / 导出诊断包 / 退出;
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
use tauri_plugin_opener::OpenerExt;

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
const MENU_EXPORT_DIAG: &str = "tray-export-diag";
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
    let export_diag = MenuItem::with_id(app, MENU_EXPORT_DIAG, "导出诊断包", true, None::<&str>)?;
    let separator = PredefinedMenuItem::separator(app)?;
    let quit = MenuItem::with_id(app, MENU_QUIT, "退出", true, None::<&str>)?;
    let menu = Menu::with_items(
        app,
        &[&show, &hide, &open_data, &export_diag, &separator, &quit],
    )?;

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
        CLOSE_TO_TRAY.store(close_to_tray_flag(&value), Ordering::Relaxed);
    }
}

/// 环境变量取值 → 是否启用「关闭到托盘」(纯解析,拆出便于单测):
/// `0` / `false` / `off` / `no`(大小写、首尾空白不敏感)= 关闭;其余 = 开启。
fn close_to_tray_flag(raw: &str) -> bool {
    let v = raw.trim().to_ascii_lowercase();
    !matches!(v.as_str(), "0" | "false" | "off" | "no")
}

// ---------------- 菜单与托盘事件 ----------------

fn on_menu_event<R: Runtime>(app: &AppHandle<R>, event: MenuEvent) {
    match event.id().as_ref() {
        MENU_SHOW => show_main_window(app),
        MENU_HIDE => hide_main_window(app),
        MENU_OPEN_DATA => open_data_dir(app),
        MENU_EXPORT_DIAG => export_diagnostics(app),
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

// ---------------- 数据目录(后端 INSAR_HOME,即 workspace) ----------------

/// 后端数据目录(INSAR_HOME)的实际路径,与后端落点逐条对齐 —— insar.db /
/// settings.json / logs/ 都直接住在这个目录里(api/app.py create_app:
/// home = INSAR_HOME,缺省 "workspace",按后端进程 cwd 解析):
/// ① 环境变量 INSAR_HOME:后端最优先认它,壳与 sidecar 共享同一份进程环境;
///    相对路径按后端「以 cwd 解析」的语义锚定到 dev 仓库根(壳 spawn
///    sidecar 时 cwd = 仓库根,见 sidecar.rs);
/// ② dev 构建:{仓库根}\workspace —— 后端缺省 home="workspace" + cwd=仓库根;
/// ③ 打包分发:%LOCALAPPDATA%\insar-agent-data\workspace —— 冻结入口的缺省
///    (desktop/backend-bundle/entry.py _resolve_env),不能用 Tauri
///    app_data_dir(%APPDATA%\dev.insar.agent,后端从不写那里);
/// ④ 兜底:Tauri 应用数据目录 → 当前工作目录(防呆,理论不可达)。
fn workspace_dir<R: Runtime>(app: &AppHandle<R>) -> PathBuf {
    let dev_root = Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .filter(|root| root.join("src").join("insar_agent").is_dir())
        .map(Path::to_path_buf);
    if let Some(dir) = resolve_workspace_dir(
        std::env::var_os("INSAR_HOME").map(PathBuf::from),
        dev_root,
        std::env::var_os("LOCALAPPDATA").map(PathBuf::from),
    ) {
        return dir;
    }
    if let Ok(dir) = app.path().app_data_dir() {
        return dir;
    }
    std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."))
}

/// workspace_dir 的纯逻辑核(三个来源注入便于单测),优先级见其文档注释。
fn resolve_workspace_dir(
    insar_home: Option<PathBuf>,
    dev_repo_root: Option<PathBuf>,
    local_app_data: Option<PathBuf>,
) -> Option<PathBuf> {
    if let Some(home) = insar_home {
        if home.is_absolute() {
            return Some(home);
        }
        // 相对 INSAR_HOME:后端按自身 cwd 解析;dev 下 sidecar cwd = 仓库根
        return Some(match dev_repo_root {
            Some(root) => root.join(home),
            None => home,
        });
    }
    if let Some(root) = dev_repo_root {
        return Some(root.join("workspace"));
    }
    local_app_data.map(|base| base.join("insar-agent-data").join("workspace"))
}

fn open_data_dir<R: Runtime>(app: &AppHandle<R>) {
    let dir = workspace_dir(app);
    // 首启后端还没建出 workspace 时先补齐(与后端 home.mkdir(parents=True) 同语义)
    if let Err(e) = std::fs::create_dir_all(&dir) {
        eprintln!("[tray] 创建数据目录失败 {}:{e}", dir.display());
        return;
    }
    // opener 插件:按系统关联方式打开目录(Windows = 资源管理器)
    if let Err(e) = app.opener().open_path(dir.to_string_lossy(), None::<&str>) {
        eprintln!("[tray] 打开数据目录失败 {}:{e}", dir.display());
    }
}

// ---------------- 导出诊断包(入口跳转) ----------------

/// 把 Web UI 切到「环境」面板的脚本,复刻命令面板 gotoPane('env') 的既有
/// 入口(prototype/js/cmdk.js:dock 收起时先点 #railDock 展开,再点
/// #tab-env 标签按钮 —— id 约定见 prototype/js/dock.js);location.hash
/// 置为 #env 只是留导航痕迹,UI 当前不做 hash 路由。
/// 诊断信息的收集与打包由后端在环境面板内承接,壳侧只负责送到入口。
/// 经 WebviewWindow::eval(WebView2 宿主通道)执行,不受页面 CSP 约束。
const OPEN_ENV_TAB_JS: &str = "(() => {\
     try { location.hash = '#env'; } catch (e) {}\
     var dock = document.getElementById('dock');\
     if (dock && dock.hidden) {\
       var rail = document.getElementById('railDock');\
       if (rail) rail.click();\
     }\
     var tab = document.getElementById('tab-env');\
     if (tab) tab.click();\
   })();";

/// 「导出诊断包」:显示并聚焦主窗口,跳到 Web UI 的「环境」面板。
/// 主窗口尚未创建(后端启动中/启动失败)时不做事,只留日志。
fn export_diagnostics<R: Runtime>(app: &AppHandle<R>) {
    let Some(window) = app.get_webview_window(MAIN_WINDOW_LABEL) else {
        eprintln!("[tray] 主窗口尚未创建(后端未就绪),暂无法打开「环境」面板");
        return;
    };
    let _ = window.show();
    let _ = window.unminimize();
    let _ = window.set_focus();
    if let Err(e) = window.eval(OPEN_ENV_TAB_JS) {
        eprintln!("[tray] 切换「环境」面板失败:{e}");
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn close_to_tray_flag_recognizes_disable_values() {
        for v in ["0", "false", "off", "no", " FALSE ", "Off", "  NO"] {
            assert!(!close_to_tray_flag(v), "{v:?} 应关闭「关闭到托盘」");
        }
    }

    #[test]
    fn close_to_tray_flag_defaults_to_enabled_for_other_values() {
        for v in ["", "1", "true", "on", "yes", "任意值"] {
            assert!(close_to_tray_flag(v), "{v:?} 应保持「关闭到托盘」开启");
        }
    }

    // ---------------- 数据目录解析(对齐后端 INSAR_HOME 语义) ----------------

    #[test]
    fn workspace_absolute_insar_home_wins() {
        // temp_dir 在各平台都是绝对路径,借它构造跨平台的绝对 INSAR_HOME
        let home = std::env::temp_dir().join("insar-home-abs");
        let got = resolve_workspace_dir(
            Some(home.clone()),
            Some(PathBuf::from("repo")),
            Some(PathBuf::from("lad")),
        );
        assert_eq!(got, Some(home));
    }

    #[test]
    fn workspace_relative_insar_home_resolves_against_repo_root() {
        // 后端把相对 INSAR_HOME 按 cwd 解析;壳 spawn sidecar 时 cwd = 仓库根
        let got = resolve_workspace_dir(
            Some(PathBuf::from("workspace")),
            Some(PathBuf::from("repo")),
            None,
        );
        assert_eq!(got, Some(PathBuf::from("repo").join("workspace")));
    }

    #[test]
    fn workspace_dev_defaults_to_repo_workspace_subdir() {
        // dev 缺省 = {仓库根}\workspace(insar.db 真实所在地),不是仓库根本身
        let got = resolve_workspace_dir(None, Some(PathBuf::from("repo")), None);
        assert_eq!(got, Some(PathBuf::from("repo").join("workspace")));
    }

    #[test]
    fn workspace_frozen_defaults_to_localappdata_workspace() {
        // 对齐 backend-bundle/entry.py:%LOCALAPPDATA%\insar-agent-data\workspace
        let got = resolve_workspace_dir(None, None, Some(PathBuf::from("lad")));
        assert_eq!(
            got,
            Some(
                PathBuf::from("lad")
                    .join("insar-agent-data")
                    .join("workspace")
            )
        );
    }

    #[test]
    fn workspace_none_when_no_source_available() {
        assert_eq!(resolve_workspace_dir(None, None, None), None);
    }

    // ---------------- 「导出诊断包」入口脚本 ----------------

    #[test]
    fn env_tab_script_matches_ui_anchor_contract() {
        // 锚点契约:prototype/js/dock.js 的 id=tab-env、cmdk.js 的 #railDock
        // 展开开关、/#env 导航痕迹 —— UI 侧改 id 时靠本测试提醒同步壳侧
        assert!(OPEN_ENV_TAB_JS.contains("tab-env"));
        assert!(OPEN_ENV_TAB_JS.contains("railDock"));
        assert!(OPEN_ENV_TAB_JS.contains("#env"));
    }
}
