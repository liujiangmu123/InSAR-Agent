//! 桌面壳 JS 桥:Tauri 2 command 模块(本分支全部为新增文件,不触碰 main.rs)。
//!
//! - 编译自检:通过 `src/lib.rs` 的库目标参与 `cargo build`,与 main.rs 的
//!   bin 目标互不影响;
//! - 挂进 main.rs 的一行式集成写法见 `desktop/INTEGRATION-commands.md`;
//! - 前端调用侧(浏览器环境安全降级)见 `prototype/js/desktop.js`。

use std::path::{Path, PathBuf};
use std::sync::Mutex;

use tauri::WebviewWindow;

#[cfg(windows)]
use std::os::windows::process::CommandExt;

static PICKER_WINDOW: Mutex<Option<WebviewWindow>> = Mutex::new(None);

/// 主窗口创建后登记,供选目录对话框挂父窗口(否则 Windows 上会弹在 WebView 后面)。
pub fn set_picker_window(win: WebviewWindow) {
    if let Ok(mut slot) = PICKER_WINDOW.lock() {
        *slot = Some(win);
    }
}

/// Windows CreateProcess 标志:不为子进程弹出控制台窗口(与 main.rs 的
/// spawn_sidecar 同款,避免黑色 cmd 窗一闪而过)。
#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

/// Windows:把顶层 HWND 拉到前台,避免 IFileDialog 落到 WebView2 后面。
/// 句柄取自 WebView 控件时先 GetAncestor(GA_ROOT) 升到外壳窗口。
#[cfg(windows)]
fn raise_hwnd(hwnd: isize) {
    if hwnd == 0 {
        return;
    }
    const SW_RESTORE: i32 = 9;
    const HWND_TOP: isize = 0;
    const SWP_NOSIZE: u32 = 0x0001;
    const SWP_NOMOVE: u32 = 0x0002;
    const SWP_SHOWWINDOW: u32 = 0x0040;
    const GA_ROOT: u32 = 2;
    const ASFW_ANY: u32 = 0xFFFF_FFFF;
    extern "system" {
        fn AllowSetForegroundWindow(dw_process_id: u32) -> i32;
        fn SetForegroundWindow(h: isize) -> i32;
        fn BringWindowToTop(h: isize) -> i32;
        fn ShowWindow(h: isize, cmd: i32) -> i32;
        fn SetWindowPos(
            h: isize,
            insert_after: isize,
            x: i32,
            y: i32,
            cx: i32,
            cy: i32,
            flags: u32,
        ) -> i32;
        fn GetAncestor(h: isize, flags: u32) -> isize;
    }
    unsafe {
        let top = {
            let ancestor = GetAncestor(hwnd, GA_ROOT);
            if ancestor != 0 {
                ancestor
            } else {
                hwnd
            }
        };
        AllowSetForegroundWindow(ASFW_ANY);
        ShowWindow(top, SW_RESTORE);
        BringWindowToTop(top);
        SetWindowPos(
            top,
            HWND_TOP,
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW,
        );
        SetForegroundWindow(top);
    }
}

#[cfg(windows)]
fn raise_window(win: &WebviewWindow) {
    let Ok(hwnd) = win.hwnd() else {
        return;
    };
    raise_hwnd(hwnd.0 as isize);
}

/// 同步弹出原生选目录对话框。必须在非 UI 线程调用(内部再派到主线程
/// 显示),以免 `run_on_main_thread` + `recv` 卡死主循环。
pub(crate) fn pick_folder_sync(title: &str) -> Option<String> {
    let window = PICKER_WINDOW.lock().ok().and_then(|g| g.clone());
    pick_folder_blocking(title, window.as_ref())
}

fn pick_folder_blocking(title: &str, window: Option<&WebviewWindow>) -> Option<String> {
    if let Some(win) = window {
        let _ = win.unminimize();
        let _ = win.set_focus();
        #[cfg(windows)]
        raise_window(win);

        let (tx, rx) = std::sync::mpsc::sync_channel(1);
        let parent = win.clone();
        let title_owned = title.to_string();
        if win
            .run_on_main_thread(move || {
                #[cfg(windows)]
                raise_window(&parent);
                let picked = rfd::FileDialog::new()
                    .set_title(&title_owned)
                    .set_parent(&parent)
                    .pick_folder()
                    .map(|p| p.to_string_lossy().into_owned());
                let _ = tx.send(picked);
            })
            .is_ok()
        {
            return rx.recv().ok().flatten();
        }
    }
    rfd::FileDialog::new()
        .set_title(title)
        .pick_folder()
        .map(|p| p.to_string_lossy().into_owned())
}

/// 弹出原生「选择目录」对话框。
///
/// - 用户选中目录 → `Some(绝对路径)`(前端拿到字符串);
/// - 用户取消 → `None`(前端拿到 `null`,由调用方回退为手输框);
/// - `title` 为空白时使用默认标题;
/// - 阻塞等待放在 `spawn_blocking`,对话框本身在 UI 线程弹出并挂父窗口。
#[tauri::command]
pub async fn pick_directory(title: String) -> Option<String> {
    let trimmed = title.trim();
    let dialog_title = if trimmed.is_empty() {
        "选择项目文件夹".to_string()
    } else {
        trimmed.to_string()
    };
    tauri::async_runtime::spawn_blocking(move || pick_folder_sync(&dialog_title))
        .await
        .ok()
        .flatten()
}

/// 在系统文件管理器(Windows 资源管理器)中打开目录。
///
/// - 传入文件路径时打开其所在目录;空路径直接忽略;
/// - `spawn` + `CREATE_NO_WINDOW`:不阻塞调用方、不弹控制台窗;
/// - 打开失败只记 stderr,不向前端抛错(桥的语义是尽力而为、绝不抛错)。
#[tauri::command]
pub fn open_path(path: String) {
    let trimmed = path.trim();
    if trimmed.is_empty() {
        return;
    }

    #[cfg(windows)]
    {
        // 资源管理器只认反斜杠;传文件时定位到所在目录
        let mut target = PathBuf::from(trimmed.replace('/', "\\"));
        if target.is_file() {
            target.pop();
        }
        let result = std::process::Command::new("explorer")
            .arg(&target)
            .creation_flags(CREATE_NO_WINDOW)
            .spawn();
        if let Err(e) = result {
            eprintln!("[desktop] 资源管理器打开失败 {}:{e}", target.display());
        }
    }

    #[cfg(target_os = "macos")]
    {
        let _ = std::process::Command::new("open").arg(trimmed).spawn();
    }

    #[cfg(all(unix, not(target_os = "macos")))]
    {
        let _ = std::process::Command::new("xdg-open").arg(trimmed).spawn();
    }
}

/// 壳信息:名称 / 版本 / Tauri 版本 / 平台 / 仓库根路径。
///
/// 键名用 camelCase,前端直接解构;`repoRoot` 找不到时为 `null`。
#[tauri::command]
pub fn app_info() -> serde_json::Value {
    serde_json::json!({
        "name": env!("CARGO_PKG_NAME"),
        "version": env!("CARGO_PKG_VERSION"),
        "tauriVersion": tauri::VERSION,
        "platform": std::env::consts::OS,
        "arch": std::env::consts::ARCH,
        "debug": cfg!(debug_assertions),
        "repoRoot": detect_repo_root().map(|p| p.to_string_lossy().into_owned()),
    })
}

/// 向上搜索仓库根:目录内含 `src/insar_agent`、`prototype/` 或 `.git` 即视为根。
///
/// 起点依次为「当前工作目录 → exe 所在目录 → 编译期 CARGO_MANIFEST_DIR」,
/// 覆盖 dev(cwd = 仓库根或 desktop/)与打包后 exe 直接运行两种形态;
/// 与 main.rs 的 `repo_root()` 判据(`src/insar_agent`)保持兼容。
fn detect_repo_root() -> Option<PathBuf> {
    let mut starts: Vec<PathBuf> = Vec::new();
    if let Ok(cwd) = std::env::current_dir() {
        starts.push(cwd);
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            starts.push(dir.to_path_buf());
        }
    }
    starts.push(PathBuf::from(env!("CARGO_MANIFEST_DIR")));

    for start in starts {
        let mut cursor: Option<&Path> = Some(start.as_path());
        while let Some(dir) = cursor {
            if dir.join("src").join("insar_agent").is_dir()
                || dir.join("prototype").is_dir()
                || dir.join(".git").exists()
            {
                return Some(dir.to_path_buf());
            }
            cursor = dir.parent();
        }
    }
    None
}

/// 把本模块全部 command 打包为 `Builder::invoke_handler` 可直接接受的 handler。
///
/// main.rs 一行接入(完整说明见 desktop/INTEGRATION-commands.md):
///
/// ```text
/// .invoke_handler(commands::handlers())
/// ```
///
/// 注意 `invoke_handler` 只能调用一次(后一次覆盖前一次)。未来若 main.rs
/// 还有自己的 command,改用宏一行列全:
///
/// ```text
/// .invoke_handler(tauri::generate_handler![
///     commands::pick_directory, commands::open_path, commands::app_info,
///     // 其他 command …
/// ])
/// ```
pub fn handlers<R: tauri::Runtime>(
) -> impl Fn(tauri::ipc::Invoke<R>) -> bool + Send + Sync + 'static {
    tauri::generate_handler![pick_directory, open_path, app_info]
}
