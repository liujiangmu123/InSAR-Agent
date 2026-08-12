//! 窗口状态持久化:记忆主窗口位置/尺寸/最大化状态,重启后原样恢复。
//!
//! - 存储:`%APPDATA%\insar-agent\window.json`,原子写(先写 `.tmp` 再 rename,
//!   Windows 上 `std::fs::rename` 带 MOVEFILE_REPLACE_EXISTING 语义);
//!   非 Windows 退化到 `$XDG_CONFIG_HOME`/`~/.config`,便于开发调试。
//! - 纯 tauri API + serde,不依赖任何第三方 plugin。
//! - 屏幕分辨率/显示器布局变化后自动校正越界位置(见 [`clamp_to_monitors`])。
//!
//! 公开接口:
//! - [`restore`]:窗口创建后调用一次,应用上次保存的几何状态;
//! - [`track`]:注册 WindowEvent 监听,持续把几何变化写入状态文件;
//! - [`attach_when_ready`]:便捷入口——main 窗口由 boot() 异步创建,setup 时
//!   还不存在,这里轮询等它出现后自动执行 restore + track。
//!
//! 集成方式见 `desktop/INTEGRATION-window.md`。

use std::fs;
use std::path::PathBuf;
use std::sync::Mutex;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Manager, PhysicalPosition, PhysicalSize, WebviewWindow, WindowEvent};

/// 主窗口 label,与 main.rs `open_main_window` 保持一致。
const MAIN_WINDOW_LABEL: &str = "main";

/// 移动/缩放的落盘节流间隔:拖动过程中 Moved/Resized 事件非常密集,
/// 逐事件写盘毫无必要;关闭/失焦时会强制落盘,不丢最终状态。
const SAVE_THROTTLE: Duration = Duration::from_millis(800);

/// 越界判定阈值:窗口顶部条带(标题栏)至少要有 64×32 物理像素落在某个
/// 显示器内,否则认为用户已经抓不到窗口,需要校正。
const MIN_VISIBLE_X: i32 = 64;
const MIN_VISIBLE_Y: i32 = 32;

/// 恢复时的尺寸下限,防止损坏/手改的状态文件把窗口缩没。
const MIN_W: u32 = 400;
const MIN_H: u32 = 300;

/// 持久化的窗口状态(全部为物理像素)。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct WindowState {
    pub x: i32,
    pub y: i32,
    pub width: u32,
    pub height: u32,
    pub maximized: bool,
}

/// 状态文件路径:`%APPDATA%\insar-agent\window.json`。
/// 两级环境变量都取不到(极少见)返回 None → 本次会话静默不持久化。
fn state_file_path() -> Option<PathBuf> {
    let base = std::env::var_os("APPDATA")
        .map(PathBuf::from)
        .or_else(|| std::env::var_os("XDG_CONFIG_HOME").map(PathBuf::from))
        .or_else(|| std::env::var_os("HOME").map(|h| PathBuf::from(h).join(".config")))?;
    Some(base.join("insar-agent").join("window.json"))
}

fn load() -> Option<WindowState> {
    let text = fs::read_to_string(state_file_path()?).ok()?;
    serde_json::from_str(&text).ok()
}

/// 原子写:先写同目录 `.tmp` 再 rename 覆盖,避免关机/崩溃时留下半个 JSON。
/// 所有失败都静默——窗口状态属于「锦上添花」,绝不为它打扰用户。
fn save(state: &WindowState) {
    let Some(path) = state_file_path() else { return };
    let Some(dir) = path.parent() else { return };
    if fs::create_dir_all(dir).is_err() {
        return;
    }
    let Ok(json) = serde_json::to_string_pretty(state) else { return };
    let tmp = path.with_extension("json.tmp");
    if fs::write(&tmp, json.as_bytes()).is_ok() {
        let _ = fs::rename(&tmp, &path);
    }
}

// ---------------- 越界校正 ----------------

#[derive(Debug, Clone, Copy)]
struct Rect {
    x: i32,
    y: i32,
    w: i32,
    h: i32,
}

fn monitor_rects(window: &WebviewWindow) -> Vec<Rect> {
    window
        .available_monitors()
        .map(|monitors| {
            monitors
                .iter()
                .map(|m| {
                    let p = m.position();
                    let s = m.size();
                    Rect { x: p.x, y: p.y, w: s.width as i32, h: s.height as i32 }
                })
                .filter(|r| r.w > 0 && r.h > 0)
                .collect()
        })
        .unwrap_or_default()
}

/// 分辨率/显示器布局变化后的越界校正。
///
/// 可达判定:顶部条带在任一显示器内可见至少 MIN_VISIBLE_X × MIN_VISIBLE_Y。
/// 不可达时:选与窗口重叠面积最大的显示器(全无重叠则取中心距离最近的),
/// 把窗口整体夹取进去,尺寸超屏时同步收缩。
fn clamp_to_monitors(state: WindowState, monitors: &[Rect]) -> WindowState {
    if monitors.is_empty() {
        return state; // 查询不到显示器信息:宁可不动,也不瞎猜
    }
    let reachable = monitors.iter().any(|m| {
        let ox = (state.x + state.width as i32).min(m.x + m.w) - state.x.max(m.x);
        let oy = (state.y + MIN_VISIBLE_Y).min(m.y + m.h) - state.y.max(m.y);
        ox >= MIN_VISIBLE_X && oy >= MIN_VISIBLE_Y
    });
    if reachable {
        return state;
    }

    let target = monitors
        .iter()
        .max_by_key(|m| {
            let ox = ((state.x + state.width as i32).min(m.x + m.w) - state.x.max(m.x)).max(0);
            let oy = ((state.y + state.height as i32).min(m.y + m.h) - state.y.max(m.y)).max(0);
            let overlap = ox as i64 * oy as i64;
            if overlap > 0 {
                (1, overlap)
            } else {
                // 无重叠:比较中心距离,取负值使「更近」=「更大」,与重叠面积统一排序
                let cx = state.x as i64 + state.width as i64 / 2 - (m.x as i64 + m.w as i64 / 2);
                let cy = state.y as i64 + state.height as i64 / 2 - (m.y as i64 + m.h as i64 / 2);
                (0, -(cx * cx + cy * cy))
            }
        })
        .copied()
        .unwrap_or(monitors[0]);

    let width = state.width.min(target.w as u32);
    let height = state.height.min(target.h as u32);
    let x = state.x.clamp(target.x, target.x + target.w - width as i32);
    let y = state.y.clamp(target.y, target.y + target.h - height as i32);
    WindowState { x, y, width, height, ..state }
}

// ---------------- 公开接口 ----------------

/// 恢复上次窗口状态(位置/尺寸/最大化)。
///
/// 窗口创建后调用一次。状态文件不存在或不可解析时不做任何事,
/// 保持 main.rs 的默认几何(1440×900)。
pub fn restore(window: &WebviewWindow) {
    let Some(saved) = load() else { return };
    let state = WindowState {
        width: saved.width.max(MIN_W),
        height: saved.height.max(MIN_H),
        ..saved
    };
    let fixed = clamp_to_monitors(state, &monitor_rects(window));

    // 先按普通几何摆好,再最大化:这样用户「还原」时回到的是保存的普通尺寸
    let _ = window.set_size(PhysicalSize::new(fixed.width, fixed.height));
    let _ = window.set_position(PhysicalPosition::new(fixed.x, fixed.y));
    if fixed.maximized {
        let _ = window.maximize();
    }
}

struct Tracker {
    state: WindowState,
    dirty: bool,
    last_write: Option<Instant>,
}

fn flush(t: &mut Tracker, force: bool) {
    if !t.dirty {
        return;
    }
    let throttled = t.last_write.is_some_and(|at| at.elapsed() < SAVE_THROTTLE);
    if throttled && !force {
        return; // 保持 dirty,等下一个事件或强制落盘时机
    }
    save(&t.state);
    t.dirty = false;
    t.last_write = Some(Instant::now());
}

/// 读取窗口当前几何,合并进 prev:
/// - 最小化:返回 None(Windows 下坐标是 -32000 假值,必须忽略);
/// - 最大化:保留 prev 的普通几何,只翻转 maximized 标志(还原时要用普通几何);
/// - 普通:全量刷新。
fn snapshot_merged(window: &WebviewWindow, prev: WindowState) -> Option<WindowState> {
    if window.is_minimized().unwrap_or(false) {
        return None;
    }
    let maximized = window.is_maximized().ok()?;
    if maximized {
        return Some(WindowState { maximized: true, ..prev });
    }
    let pos = window.outer_position().ok()?;
    let size = window.inner_size().ok()?;
    if size.width == 0 || size.height == 0 {
        return None; // 创建/销毁瞬间的空几何
    }
    Some(WindowState {
        x: pos.x,
        y: pos.y,
        width: size.width,
        height: size.height,
        maximized: false,
    })
}

/// 开始追踪窗口几何变化并持久化(注册 Tauri 2 WindowEvent 监听,
/// 监听随窗口销毁自动解除)。窗口创建后调用一次,通常紧跟 [`restore`]。
pub fn track(window: &WebviewWindow) {
    let fallback = WindowState { x: 100, y: 100, width: 1440, height: 900, maximized: false };
    let base = load().unwrap_or(fallback);
    let init = snapshot_merged(window, base).unwrap_or(base);

    let win = window.clone();
    let tracker = Mutex::new(Tracker { state: init, dirty: false, last_write: None });
    window.on_window_event(move |event| {
        let Ok(mut t) = tracker.lock() else { return };
        match event {
            WindowEvent::Moved(_) | WindowEvent::Resized(_) => {
                if let Some(now) = snapshot_merged(&win, t.state) {
                    if now != t.state {
                        t.state = now;
                        t.dirty = true;
                    }
                }
                flush(&mut t, false);
            }
            // 失焦/关闭/销毁:强制落盘——除崩溃外任何退出路径都不丢状态
            WindowEvent::Focused(false) => flush(&mut t, true),
            WindowEvent::CloseRequested { .. } | WindowEvent::Destroyed => flush(&mut t, true),
            _ => {}
        }
    });
}

/// 便捷入口:在 setup 钩子里调用一次即可。
///
/// main 窗口由 boot() 在后台线程异步创建(要先等 sidecar 健康检查),
/// setup 时 `app.get_webview_window("main")` 必然是 None;这里起一个轻量
/// 线程轮询等它出现(100ms × 600 次 = 最多 60s,覆盖健康检查 30s 上限),
/// 出现后执行 restore + track。窗口始终没出现(启动失败走诊断窗)则静默退出。
pub fn attach_when_ready(app: &AppHandle) {
    let app = app.clone();
    std::thread::spawn(move || {
        for _ in 0..600 {
            if let Some(window) = app.get_webview_window(MAIN_WINDOW_LABEL) {
                restore(&window);
                track(&window);
                return;
            }
            std::thread::sleep(Duration::from_millis(100));
        }
    });
}
