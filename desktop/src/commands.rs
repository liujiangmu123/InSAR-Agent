//! 桌面壳 JS 桥:Tauri 2 command 模块(本分支全部为新增文件,不触碰 main.rs)。
//!
//! - 编译自检:通过 `src/lib.rs` 的库目标参与 `cargo build`,与 main.rs 的
//!   bin 目标互不影响;
//! - 挂进 main.rs 的一行式集成写法见 `desktop/INTEGRATION-commands.md`;
//! - 前端调用侧(浏览器环境安全降级)见 `prototype/js/desktop.js`。

use std::path::{Path, PathBuf};

#[cfg(windows)]
use std::os::windows::process::CommandExt;

/// Windows CreateProcess 标志:不为子进程弹出控制台窗口(与 main.rs 的
/// spawn_sidecar 同款,避免黑色 cmd 窗一闪而过)。
#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

/// 弹出原生「选择目录」对话框。
///
/// - 用户选中目录 → `Some(绝对路径)`(前端拿到字符串);
/// - 用户取消 → `None`(前端拿到 `null`,由调用方回退为手输框);
/// - `title` 为空白时使用默认标题;
/// - 使用 rfd 的异步对话框 + async command:对话框在独立线程弹出,
///   不阻塞 Tauri 主事件循环。
#[tauri::command]
pub async fn pick_directory(title: String) -> Option<String> {
    let trimmed = title.trim();
    let dialog_title = if trimmed.is_empty() {
        "选择目录"
    } else {
        trimmed
    };
    rfd::AsyncFileDialog::new()
        .set_title(dialog_title)
        .pick_folder()
        .await
        .map(|dir| dir.path().to_string_lossy().into_owned())
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
