// InSAR-Agent 桌面壳:Rust/Tauri 只做「壳」—— 窗口 + sidecar 生命周期管理,
// 业务 100% 留在 Python 后端(FastAPI + prototype/ 静态 UI),后端零改动。
// 架构决策见 docs/OPTIMIZATION.md §4;sidecar 进程/日志细节在 src/sidecar.rs。
//
// 启动流程:读 INSAR_PORT → 端口裁决(已有健康后端 → 复用;被占且非本后端 →
//           从 8873 起找空闲端口)→ 探测后端(① exe 同目录 backend\
//           insar-backend.exe 冻结产物,免 Python;② INSAR_PYTHON;③ 仓库根
//           .venv;④ PATH python)→ spawn(冻结 exe 直接运行 / Python 跑
//           `python -m insar_agent.api.app`;CREATE_NO_WINDOW,stdout/stderr
//           走 5MB 滚动日志)→ 轮询 /api/health(30 秒超时)
//           → 打开 WebView 窗口指向后端。
// 运行期:sidecar 意外退出 → 指数退避自动重启(最多 3 次,窗口标题实时提示),
//         仍未恢复 → 弹诊断页。
// 退出语义:直接 kill sidecar 子进程即可 —— 后端自带 orphan 检测与 reattach
//           语义,「桌面关闭 → 重开」等价于「断点续跑」,这正是产品语义,
//           无需优雅停机协商。

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod commands;
mod shortcuts;
mod sidecar;
mod singleton;
mod tray;
mod window_state;

use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use tauri::{AppHandle, Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};

const DEFAULT_PORT: u16 = 8873;
const HEALTH_TIMEOUT_SECS: u64 = 30;
/// 后端健康检查端点(与 FastAPI 侧路由保持一致)。
const HEALTH_PATH: &str = "/api/health";
/// INSAR_PORT 被占且不是本后端时,从这里起向上扫描空闲端口。
const PORT_SCAN_START: u16 = 8873;
const PORT_SCAN_SPAN: u16 = 100;
/// sidecar 意外退出后的最大连续自动重启次数,超过即弹诊断页。
const MAX_RESTARTS: u32 = 3;
/// 重启后稳定运行满该时长,重启预算清零(区分「崩溃循环」与「偶发崩溃」)。
const STABLE_UPTIME_SECS: u64 = 60;

// ---------------- 配置解析 ----------------

fn parse_port(raw: Option<String>) -> u16 {
    raw.and_then(|s| s.trim().parse::<u16>().ok())
        .filter(|p| *p != 0)
        .unwrap_or(DEFAULT_PORT)
}

fn insar_port() -> u16 {
    parse_port(std::env::var("INSAR_PORT").ok())
}

// ---------------- 端口裁决 ----------------

#[derive(Debug, PartialEq, Eq)]
enum PortPlan {
    /// 端口上已有健康后端:直接连接,不 spawn、不接管生命周期。
    Reuse(u16),
    /// 在该端口 spawn sidecar(可能是期望端口,也可能是冲突后的回退端口)。
    Spawn(u16),
}

fn port_is_free(port: u16) -> bool {
    TcpListener::bind(("127.0.0.1", port)).is_ok()
}

/// 端口裁决:健康后端 → 复用;空闲 → 用之;被占且 /api/health 非 200 →
/// 从 PORT_SCAN_START 起向上找第一个空闲端口。
fn resolve_port(desired: u16) -> Result<PortPlan, String> {
    resolve_port_with(desired, health_ok, port_is_free)
}

/// 端口裁决的纯逻辑核:健康探测与空闲探测以闭包注入,便于单测回退序列。
/// 扫描时跳过 desired 本身 —— 走到扫描分支说明它已被判定不可用,
/// 即使空闲探测因 TOCTOU 竞态再次放行也不能选回去。
fn resolve_port_with(
    desired: u16,
    healthy: impl Fn(u16) -> bool,
    free: impl Fn(u16) -> bool,
) -> Result<PortPlan, String> {
    if healthy(desired) {
        return Ok(PortPlan::Reuse(desired));
    }
    if free(desired) {
        return Ok(PortPlan::Spawn(desired));
    }
    let end = PORT_SCAN_START.saturating_add(PORT_SCAN_SPAN);
    for p in PORT_SCAN_START..end {
        if p != desired && free(p) {
            return Ok(PortPlan::Spawn(p));
        }
    }
    Err(format!(
        "端口 {desired} 已被其他进程占用(/api/health 非 200),且 {PORT_SCAN_START}-{} 范围内没有空闲端口",
        end - 1
    ))
}

// ---------------- 极简 HTTP(健康检查不为此引 reqwest 等重依赖) ----------------

/// 组装最小 HTTP/1.1 GET 请求头(纯字符串拼装,拆出便于单测)。
fn http_request_head(host: &str, port: u16, path: &str) -> String {
    format!(
        "GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nAccept: */*\r\nConnection: close\r\n\r\n"
    )
}

/// 对 host:port 发一个最小 HTTP/1.1 GET,返回响应状态码。
fn http_get_status(host: &str, port: u16, path: &str, timeout: Duration) -> Option<u16> {
    let addr: std::net::SocketAddr = format!("{host}:{port}").parse().ok()?;
    let mut stream = TcpStream::connect_timeout(&addr, timeout).ok()?;
    stream.set_read_timeout(Some(timeout)).ok()?;
    stream.set_write_timeout(Some(timeout)).ok()?;
    let request = http_request_head(host, port, path);
    stream.write_all(request.as_bytes()).ok()?;
    let mut response = Vec::new();
    let mut buf = [0u8; 1024];
    while response.len() < 8192 {
        match stream.read(&mut buf) {
            Ok(0) => break,
            Ok(n) => {
                response.extend_from_slice(&buf[..n]);
                if response.windows(4).any(|w| w == b"\r\n\r\n") {
                    break; // 状态行 + 头部收全即可,body 不需要
                }
            }
            Err(_) => break,
        }
    }
    parse_status_code(&String::from_utf8_lossy(&response))
}

/// 从 "HTTP/1.1 200 OK" 状态行解析状态码。
fn parse_status_code(response: &str) -> Option<u16> {
    let line = response.lines().next()?;
    let mut parts = line.split_whitespace();
    if !parts.next()?.starts_with("HTTP/") {
        return None;
    }
    parts.next()?.parse().ok()
}

fn health_ok(port: u16) -> bool {
    http_get_status("127.0.0.1", port, HEALTH_PATH, Duration::from_secs(2)) == Some(200)
}

/// 轮询健康检查;sidecar 提前退出则立即失败(不空等满超时)。
fn wait_health(port: u16, timeout: Duration) -> Result<(), String> {
    let deadline = Instant::now() + timeout;
    loop {
        if health_ok(port) {
            return Ok(());
        }
        if let Some(status) = sidecar::child_exit_status() {
            return Err(format!("sidecar 进程提前退出({status})"));
        }
        if Instant::now() >= deadline {
            return Err(format!(
                "健康检查超时:{} 秒内 /api/health 未返回 200",
                timeout.as_secs()
            ));
        }
        std::thread::sleep(Duration::from_millis(500));
    }
}

// ---------------- 诊断页(启动失败/自愈失败时展示,绝不静默退出) ----------------

fn escape_html(s: &str) -> String {
    s.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&#39;")
}

fn read_log_tail(path: &Path, max_bytes: usize) -> String {
    match std::fs::read(path) {
        Ok(bytes) => {
            let start = bytes.len().saturating_sub(max_bytes);
            String::from_utf8_lossy(&bytes[start..]).into_owned()
        }
        Err(e) => format!("(无法读取日志 {}:{e})", path.display()),
    }
}

fn diagnostics_html(
    error: &str,
    backend_desc: &str,
    launch_desc: &str,
    port: u16,
    workdir: &Path,
) -> String {
    let log_path = sidecar::log_path(port);
    format!(
        r#"<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>InSAR-Agent 启动失败</title>
<style>
body{{font-family:"Segoe UI",system-ui,sans-serif;margin:2rem;background:#0f1720;color:#e6edf3}}
h1{{font-size:1.3rem;color:#ff7b72}} h2{{font-size:1.05rem;margin-top:1.5rem}}
code,pre{{background:#161f2b;border-radius:6px}} code{{padding:.1rem .4rem}}
pre{{padding:1rem;overflow:auto;max-height:40vh;white-space:pre-wrap}}
td{{padding:.25rem .75rem .25rem 0;vertical-align:top}} .k{{color:#8b98a5;white-space:nowrap}}
</style></head><body>
<h1>InSAR-Agent 后端(sidecar)启动失败</h1>
<p><strong>{error}</strong></p>
<table>
<tr><td class="k">后端</td><td><code>{backend}</code>(探测顺序:exe 旁 <code>backend\insar-backend.exe</code> → <code>INSAR_PYTHON</code> → 仓库根 <code>.venv</code> → PATH)</td></tr>
<tr><td class="k">启动命令</td><td><code>{launch}</code></td></tr>
<tr><td class="k">端口</td><td><code>{port}</code>(环境变量 <code>INSAR_PORT</code> 可改)</td></tr>
<tr><td class="k">工作目录</td><td><code>{workdir}</code></td></tr>
<tr><td class="k">sidecar 日志</td><td><code>{log}</code>(滚动历史在同名 <code>.1</code> 文件)</td></tr>
</table>
<h2>日志尾部</h2>
<pre>{log_tail}</pre>
<h2>排查建议</h2>
<ul>
<li>打包分发形态:确认主程序旁 <code>backend\</code> 目录完整(<code>insar-backend.exe</code>
与 <code>_internal\</code> 同在),该形态无需安装 Python;若目录残缺请重新安装</li>
<li>源码开发形态:项目约定使用仓库内虚拟环境 —— 若 <code>.venv</code> 不存在,在仓库根执行
<code>py -m venv .venv</code>,再 <code>.venv\Scripts\python.exe -m pip install -r requirements.txt</code>,
并确认能手动运行 <code>python -m insar_agent.api.app</code></li>
<li>用 <code>INSAR_PYTHON</code> 显式指定解释器(仅源码形态生效;冻结后端优先级更高);
用 <code>INSAR_PORT</code> 指定端口(被占用时壳会自动从 8873 起改用空闲端口)</li>
<li>修复后关闭本窗口重开应用即可 —— 后端支持断点续跑,不会丢进度</li>
</ul>
</body></html>"#,
        error = escape_html(error),
        backend = escape_html(backend_desc),
        launch = escape_html(launch_desc),
        port = port,
        workdir = escape_html(&workdir.display().to_string()),
        log = escape_html(&log_path.display().to_string()),
        log_tail = escape_html(&read_log_tail(&log_path, 16 * 1024)),
    )
}

/// 用 127.0.0.1 上的临时监听器托管一页静态 HTML(对 WebView2 比 data: URL 更稳),
/// 返回随机端口。监听线程随进程退出一起消亡。
fn serve_html(html: String) -> std::io::Result<u16> {
    let listener = TcpListener::bind(("127.0.0.1", 0))?;
    let port = listener.local_addr()?.port();
    let body = html.into_bytes();
    std::thread::spawn(move || {
        for stream in listener.incoming() {
            let Ok(mut stream) = stream else { continue };
            let mut buf = [0u8; 4096];
            let _ = stream.read(&mut buf); // 读掉请求头(尽力而为)
            let header = format!(
                "HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                body.len()
            );
            let _ = stream.write_all(header.as_bytes());
            let _ = stream.write_all(&body);
            let _ = stream.flush();
        }
    });
    Ok(port)
}

// ---------------- 窗口 ----------------

/// 创建 WebView 窗口。可从任意线程调用(内部派发到主线程)。
fn open_window(handle: &AppHandle, label: &str, title: &str, url: String, size: (f64, f64)) {
    let handle_ = handle.clone();
    let label = label.to_string();
    let title = title.to_string();
    let _ = handle.run_on_main_thread(move || {
        let parsed = match tauri::Url::parse(&url) {
            Ok(u) => u,
            Err(e) => {
                eprintln!("[desktop] URL 解析失败 {url}:{e}");
                handle_.exit(1);
                return;
            }
        };
        let built =
            WebviewWindowBuilder::new(&handle_, label.as_str(), WebviewUrl::External(parsed))
                .title(title.as_str())
                .inner_size(size.0, size.1)
                .build();
        if let Err(e) = built {
            eprintln!("[desktop] 创建窗口失败:{e}");
            handle_.exit(1);
        }
    });
}

/// 主窗口指向本地后端的 URL(纯拼装,拆出便于单测)。
fn backend_url(port: u16) -> String {
    format!("http://127.0.0.1:{port}/")
}

fn open_main_window(handle: &AppHandle, port: u16) {
    open_window(
        handle,
        "main",
        "InSAR-Agent",
        backend_url(port),
        (1440.0, 900.0),
    );
}

/// 设置主窗口标题(任意线程可调;窗口尚不存在时静默忽略)。
fn set_main_title(handle: &AppHandle, title: String) {
    let handle_ = handle.clone();
    let _ = handle.run_on_main_thread(move || {
        if let Some(win) = handle_.get_webview_window("main") {
            let _ = win.set_title(&title);
        }
    });
}

fn show_diagnostics(
    handle: &AppHandle,
    error: &str,
    backend_desc: &str,
    launch_desc: &str,
    port: u16,
    workdir: &Path,
) {
    eprintln!("[desktop] 启动失败:{error}");
    match serve_html(diagnostics_html(
        error,
        backend_desc,
        launch_desc,
        port,
        workdir,
    )) {
        Ok(diag_port) => open_window(
            handle,
            "diagnostics",
            "InSAR-Agent — 启动失败",
            format!("http://127.0.0.1:{diag_port}/"),
            (980.0, 760.0),
        ),
        Err(e) => {
            eprintln!("[desktop] 诊断页托管失败:{e}");
            handle.exit(1);
        }
    }
}

// ---------------- 启动编排 ----------------

/// 后台启动流程:端口裁决 → 探测 → spawn → 健康检查 → 开窗 → 转入运行期看护;
/// 任何启动失败都开诊断窗。
fn boot(handle: AppHandle, desired_port: u16) {
    let workdir = sidecar::repo_root();

    let port = match resolve_port(desired_port) {
        Ok(PortPlan::Reuse(p)) => {
            println!("[desktop] 端口 {p} 已有健康后端,直接连接(不 spawn sidecar)");
            open_main_window(&handle, p);
            return;
        }
        Ok(PortPlan::Spawn(p)) => {
            if p != desired_port {
                println!(
                    "[desktop] 端口 {desired_port} 被其他进程占用(/api/health 非 200),自动改用空闲端口 {p}"
                );
            }
            p
        }
        Err(e) => {
            show_diagnostics(&handle, &e, "(未探测)", "(未探测)", desired_port, &workdir);
            return;
        }
    };

    let backend = match sidecar::find_backend(&workdir) {
        Ok(v) => v,
        Err(e) => {
            show_diagnostics(&handle, &e, "(未找到)", "(未探测)", port, &workdir);
            return;
        }
    };
    let backend_desc = backend.describe();
    let launch_desc = backend.launch_desc();
    println!("[desktop] 后端 = {backend_desc}");
    println!(
        "[desktop] spawn sidecar:cwd={},log={}",
        workdir.display(),
        sidecar::log_path(port).display()
    );

    if let Err(e) = sidecar::spawn(&backend, port, &workdir) {
        show_diagnostics(&handle, &e, &backend_desc, &launch_desc, port, &workdir);
        return;
    }
    match wait_health(port, Duration::from_secs(HEALTH_TIMEOUT_SECS)) {
        Ok(()) => {
            println!("[desktop] /api/health = 200,打开主窗口");
            open_main_window(&handle, port);
            std::thread::spawn(move || supervise(handle, backend, backend_desc, port, workdir));
        }
        Err(e) => show_diagnostics(&handle, &e, &backend_desc, &launch_desc, port, &workdir),
    }
}

/// 第 attempt 次(从 1 计)自动重启前的退避时长:1s → 2s → 4s 指数翻倍。
/// 移位量封顶(64s 上限)防溢出 —— 当前 MAX_RESTARTS=3 只用到前三档,
/// 未来调大重启预算时该上限保证不 panic、不无限退避。
fn restart_backoff(attempt: u32) -> Duration {
    let shift = attempt.saturating_sub(1).min(6);
    Duration::from_secs(1u64 << shift)
}

/// 运行期看护:sidecar 意外退出 → 指数退避(1s/2s/4s)自动重启,最多
/// MAX_RESTARTS 次;稳定运行满 STABLE_UPTIME_SECS 后预算清零。重启期间
/// 窗口标题实时提示;重启预算耗尽仍未恢复 → 弹诊断页并停止看护。
fn supervise(
    handle: AppHandle,
    backend: sidecar::Backend,
    backend_desc: String,
    port: u16,
    workdir: PathBuf,
) {
    let mut attempts: u32 = 0;
    let mut spawned_at = Instant::now();
    loop {
        let exit_desc = match sidecar::wait_exit_or_shutdown() {
            sidecar::WaitOutcome::Shutdown => return,
            sidecar::WaitOutcome::Exited(desc) => desc,
        };
        if spawned_at.elapsed() >= Duration::from_secs(STABLE_UPTIME_SECS) {
            attempts = 0;
        }
        eprintln!("[desktop] sidecar 意外退出({exit_desc}),尝试自动重启");

        let mut recovered = false;
        while attempts < MAX_RESTARTS {
            attempts += 1;
            let backoff = restart_backoff(attempts); // 1s/2s/4s
            set_main_title(
                &handle,
                format!(
                    "InSAR-Agent — 后端已退出,{}s 后自动重启({attempts}/{MAX_RESTARTS})",
                    backoff.as_secs()
                ),
            );
            if sidecar::sleep_unless_shutdown(backoff) {
                return;
            }
            set_main_title(
                &handle,
                format!("InSAR-Agent — 后端重启中({attempts}/{MAX_RESTARTS})…"),
            );
            spawned_at = Instant::now();
            if let Err(e) = sidecar::spawn(&backend, port, &workdir) {
                eprintln!("[desktop] 第 {attempts} 次重启 spawn 失败:{e}");
                continue;
            }
            match wait_health(port, Duration::from_secs(HEALTH_TIMEOUT_SECS)) {
                Ok(()) => {
                    recovered = true;
                    break;
                }
                Err(e) => {
                    eprintln!("[desktop] 第 {attempts} 次重启后健康检查失败:{e}");
                    sidecar::kill_current();
                }
            }
        }

        if recovered {
            println!("[desktop] sidecar 已自动恢复(第 {attempts} 次重启)");
            set_main_title(&handle, "InSAR-Agent".into());
            continue;
        }
        set_main_title(
            &handle,
            format!("InSAR-Agent — 后端已停止(自动重启 {MAX_RESTARTS} 次失败)"),
        );
        show_diagnostics(
            &handle,
            &format!("sidecar 意外退出({exit_desc}),自动重启 {MAX_RESTARTS} 次均未恢复"),
            &backend_desc,
            &backend.launch_desc(),
            port,
            &workdir,
        );
        return;
    }
}

// ---------------- 自动更新(可选,环境变量开关) ----------------

/// 更新清单地址:只从环境变量 INSAR_UPDATE_ENDPOINT 读取,绝不硬编码。
/// 未配置(或空白)→ 返回 None → updater 插件不挂载、不做任何更新检查。
/// 清单格式 / 密钥生成 / 发布流程见 desktop/updater/UPDATER.md。
fn update_endpoint() -> Option<String> {
    std::env::var("INSAR_UPDATE_ENDPOINT")
        .ok()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
}

/// 后台静默检查更新:只「检查 + 日志提示」,不自动下载安装(签名密钥
/// 尚未生成,download_and_install 的编排留给发布期的 UI 决策)。
/// 任何失败只记日志,绝不影响主流程。
fn spawn_update_check(handle: AppHandle, endpoint: String) {
    tauri::async_runtime::spawn(async move {
        use tauri_plugin_updater::UpdaterExt;
        let url = match tauri::Url::parse(&endpoint) {
            Ok(u) => u,
            Err(e) => {
                eprintln!("[desktop] INSAR_UPDATE_ENDPOINT 不是合法 URL({endpoint}):{e}");
                return;
            }
        };
        let updater = match handle
            .updater_builder()
            .endpoints(vec![url])
            .and_then(|b| b.build())
        {
            Ok(u) => u,
            Err(e) => {
                eprintln!("[desktop] updater 初始化失败(不影响使用):{e}");
                return;
            }
        };
        match updater.check().await {
            Ok(Some(update)) => println!(
                "[desktop] 更新检查:发现新版本 {}(当前 {}),安装编排待发布期接入",
                update.version, update.current_version
            ),
            Ok(None) => println!("[desktop] 更新检查:已是最新版本"),
            Err(e) => eprintln!("[desktop] 更新检查失败(不影响使用):{e}"),
        }
    });
}

/// 无 GUI 自检(`--smoke` 或 INSAR_DESKTOP_SMOKE=1):
/// spawn sidecar → 等健康检查 → kill → 退出码 0/1。无人值守/CI 验证用。
fn run_smoke(desired_port: u16) -> i32 {
    println!("[smoke] 期望端口 {desired_port}");
    let port = match resolve_port(desired_port) {
        Ok(PortPlan::Reuse(p)) => {
            println!("[smoke] OK:端口 {p} 已有健康后端(跳过 spawn)");
            return 0;
        }
        Ok(PortPlan::Spawn(p)) => {
            if p != desired_port {
                println!("[smoke] 端口 {desired_port} 被占用,改用空闲端口 {p}");
            }
            p
        }
        Err(e) => {
            eprintln!("[smoke] FAIL:{e}");
            return 1;
        }
    };
    let workdir = sidecar::repo_root();
    let backend = match sidecar::find_backend(&workdir) {
        Ok(v) => v,
        Err(e) => {
            eprintln!("[smoke] FAIL:{e}");
            return 1;
        }
    };
    println!(
        "[smoke] 后端={} cwd={}",
        backend.describe(),
        workdir.display()
    );
    if let Err(e) = sidecar::spawn(&backend, port, &workdir) {
        eprintln!("[smoke] FAIL:{e}");
        return 1;
    }
    let result = wait_health(port, Duration::from_secs(HEALTH_TIMEOUT_SECS));
    sidecar::shutdown();
    match result {
        Ok(()) => {
            println!("[smoke] OK:/api/health = 200,sidecar 已终止");
            0
        }
        Err(e) => {
            eprintln!(
                "[smoke] FAIL:{e}(日志:{})",
                sidecar::log_path(port).display()
            );
            1
        }
    }
}

fn main() {
    let port = insar_port();
    let smoke = std::env::args().any(|a| a == "--smoke")
        || std::env::var("INSAR_DESKTOP_SMOKE").is_ok_and(|v| v == "1");
    if smoke {
        std::process::exit(run_smoke(port));
    }

    // 更新检查开关:读 INSAR_UPDATE_ENDPOINT(未配置 = 完全不启用,绝不硬编码)
    let update_endpoint = update_endpoint();

    // 单实例包装必须在其他插件之前(见 INTEGRATION-tray.md)
    let mut builder = singleton::ensure_single_instance(tauri::Builder::default())
        // opener:托盘「打开数据目录」以系统文件管理器打开数据目录(tray.rs)
        .plugin(tauri_plugin_opener::init());
    if update_endpoint.is_some() {
        builder = builder.plugin(tauri_plugin_updater::Builder::new().build());
    }
    let app = builder
        .invoke_handler(commands::handlers())
        .setup(move |app| {
            let handle = app.handle().clone();
            tray::setup_tray(app.handle())?; // 托盘:关闭到托盘钩子经 window_created 自动挂上
            window_state::attach_when_ready(app.handle()); // 窗口几何持久化(主窗异步创建)
            if let Err(e) = shortcuts::setup_shortcuts(app.handle()) {
                eprintln!("[desktop] 全局快捷键注册失败(不影响主流程):{e}");
            }
            if let Some(endpoint) = update_endpoint.clone() {
                println!("[desktop] 更新检查已启用:INSAR_UPDATE_ENDPOINT={endpoint}");
                spawn_update_check(app.handle().clone(), endpoint);
            }
            // 健康检查最长 30 秒,放后台线程,避免卡死事件循环
            std::thread::spawn(move || boot(handle, port));
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("tauri 应用构建失败");

    app.run(|_handle, event| {
        if let RunEvent::Exit = event {
            sidecar::shutdown(); // 窗口全关/应用退出 → 停止看护并终止 sidecar(断点续跑语义)
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_status_code_valid() {
        assert_eq!(
            parse_status_code("HTTP/1.1 200 OK\r\nContent-Type: a\r\n\r\n"),
            Some(200)
        );
        assert_eq!(parse_status_code("HTTP/1.0 404 Not Found\r\n"), Some(404));
        assert_eq!(parse_status_code("HTTP/2 502 Bad Gateway"), Some(502));
    }

    #[test]
    fn parse_status_code_invalid() {
        assert_eq!(parse_status_code(""), None);
        assert_eq!(parse_status_code("nonsense response"), None);
        assert_eq!(parse_status_code("HTTP/1.1 abc OK"), None);
    }

    #[test]
    fn parse_port_fallbacks() {
        assert_eq!(parse_port(None), 8873);
        assert_eq!(parse_port(Some(String::new())), 8873);
        assert_eq!(parse_port(Some("notaport".into())), 8873);
        assert_eq!(parse_port(Some("0".into())), 8873);
        assert_eq!(parse_port(Some("18944".into())), 18944);
        assert_eq!(parse_port(Some(" 9001 ".into())), 9001);
    }

    #[test]
    fn escape_html_covers_specials() {
        assert_eq!(escape_html(r#"<b>&"'"#), "&lt;b&gt;&amp;&quot;&#39;");
    }

    /// 用本地临时 TCP 服务端验证手写 HTTP 客户端(健康检查核心路径)。
    #[test]
    fn http_get_status_reads_local_server() {
        let listener = TcpListener::bind(("127.0.0.1", 0)).unwrap();
        let port = listener.local_addr().unwrap().port();
        std::thread::spawn(move || {
            if let Ok((mut s, _)) = listener.accept() {
                let mut buf = [0u8; 1024];
                let _ = s.read(&mut buf);
                let _ = s.write_all(
                    b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok",
                );
            }
        });
        assert_eq!(
            http_get_status("127.0.0.1", port, "/api/health", Duration::from_secs(2)),
            Some(200)
        );
    }

    /// serve_html + http_get_status 组合:诊断页能被 GET 到。
    #[test]
    fn serve_html_serves_diagnostics_page() {
        let port = serve_html("<html>diag</html>".into()).unwrap();
        assert_eq!(
            http_get_status("127.0.0.1", port, "/", Duration::from_secs(2)),
            Some(200)
        );
    }

    #[test]
    fn health_check_refused_port_is_not_ok() {
        // 绑定后立刻释放,得到一个大概率无人监听的端口
        let port = {
            let l = TcpListener::bind(("127.0.0.1", 0)).unwrap();
            l.local_addr().unwrap().port()
        };
        assert!(!health_ok(port));
    }

    #[test]
    fn port_is_free_detects_bound_listener() {
        let listener = TcpListener::bind(("127.0.0.1", 0)).unwrap();
        let port = listener.local_addr().unwrap().port();
        assert!(!port_is_free(port));
    }

    // ---------------- 端口仲裁(纯逻辑,探测以闭包注入) ----------------

    #[test]
    fn resolve_port_reuses_healthy_backend() {
        let plan = resolve_port_with(9000, |_| true, |_| panic!("已健康就不该再探测空闲"));
        assert_eq!(plan, Ok(PortPlan::Reuse(9000)));
    }

    #[test]
    fn resolve_port_spawns_on_free_desired_port() {
        let plan = resolve_port_with(9000, |_| false, |p| p == 9000);
        assert_eq!(plan, Ok(PortPlan::Spawn(9000)));
    }

    #[test]
    fn resolve_port_falls_back_to_first_free_scan_port() {
        // 期望端口被占且不健康 → 从 PORT_SCAN_START 起向上,命中第一个空闲端口
        let plan = resolve_port_with(9000, |_| false, |p| p == 8875 || p == 8876);
        assert_eq!(plan, Ok(PortPlan::Spawn(8875)));
    }

    #[test]
    fn resolve_port_scan_covers_span_boundary() {
        // 扫描区间为 [PORT_SCAN_START, PORT_SCAN_START+PORT_SCAN_SPAN):最后一个端口也能被找到
        let last = PORT_SCAN_START + PORT_SCAN_SPAN - 1;
        let plan = resolve_port_with(9000, |_| false, |p| p == last);
        assert_eq!(plan, Ok(PortPlan::Spawn(last)));
    }

    #[test]
    fn resolve_port_scan_skips_desired_even_if_freed_meanwhile() {
        // desired 首查被占;扫描途中即使空闲探测因 TOCTOU 竞态对它放行,也必须跳过它
        use std::cell::Cell;
        let calls = Cell::new(0u32);
        let free = |p: u16| {
            calls.set(calls.get() + 1);
            if calls.get() == 1 {
                false // 第一次(desired 直查)被占
            } else {
                p == 8873 || p == 8890
            }
        };
        let plan = resolve_port_with(8873, |_| false, free);
        assert_eq!(plan, Ok(PortPlan::Spawn(8890)));
    }

    #[test]
    fn resolve_port_errors_when_scan_exhausted() {
        let err = resolve_port_with(8873, |_| false, |_| false).unwrap_err();
        assert!(err.contains("8873"), "错误信息应含期望端口:{err}");
        assert!(err.contains("没有空闲端口"), "错误信息应说明扫描失败:{err}");
    }

    // ---------------- 重启退避曲线 ----------------

    #[test]
    fn restart_backoff_follows_doubling_curve() {
        // MAX_RESTARTS=3 实际用到的三档:1s → 2s → 4s
        assert_eq!(restart_backoff(1), Duration::from_secs(1));
        assert_eq!(restart_backoff(2), Duration::from_secs(2));
        assert_eq!(restart_backoff(3), Duration::from_secs(4));
    }

    #[test]
    fn restart_backoff_is_capped_and_never_panics() {
        // 间隔上限 64s;次数上限由 supervise 的 while attempts < MAX_RESTARTS 保证
        assert_eq!(restart_backoff(7), Duration::from_secs(64));
        assert_eq!(restart_backoff(100), Duration::from_secs(64));
        assert_eq!(restart_backoff(u32::MAX), Duration::from_secs(64));
        // 0 不在正常调用域(attempt 从 1 计),防御性取最小档
        assert_eq!(restart_backoff(0), Duration::from_secs(1));
    }

    // ---------------- 健康检查 / 主窗口 URL 拼装 ----------------

    #[test]
    fn backend_url_points_to_loopback_root() {
        assert_eq!(backend_url(8873), "http://127.0.0.1:8873/");
        assert_eq!(backend_url(18944), "http://127.0.0.1:18944/");
    }

    #[test]
    fn http_request_head_is_well_formed() {
        let head = http_request_head("127.0.0.1", 8873, HEALTH_PATH);
        assert!(head.starts_with("GET /api/health HTTP/1.1\r\n"));
        assert!(head.contains("Host: 127.0.0.1:8873\r\n"));
        assert!(head.contains("Connection: close\r\n"));
        assert!(head.ends_with("\r\n\r\n"), "请求头必须以空行结束");
    }
}
