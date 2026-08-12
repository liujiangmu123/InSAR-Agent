// InSAR-Agent 桌面壳:Rust/Tauri 只做「壳」—— 窗口 + sidecar 生命周期管理,
// 业务 100% 留在 Python 后端(FastAPI + prototype/ 静态 UI),后端零改动。
// 架构决策见 docs/OPTIMIZATION.md §4。
//
// 启动流程:读 INSAR_PORT → 探测 Python → spawn `python -m insar_agent.api.app`
//           → 轮询 /api/health(30 秒超时)→ 打开 WebView 窗口指向后端。
// 退出语义:直接 kill sidecar 子进程即可 —— 后端自带 orphan 检测与 reattach
//           语义,「桌面关闭 → 重开」等价于「断点续跑」,这正是产品语义,
//           无需优雅停机协商。

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::fs::File;
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::{AppHandle, RunEvent, WebviewUrl, WebviewWindowBuilder};

const DEFAULT_PORT: u16 = 8873;
const HEALTH_TIMEOUT_SECS: u64 = 30;
const DEFAULT_WINDOWS_PYTHON: &str = r"C:\Python314\python.exe";

/// sidecar 子进程句柄;应用退出时统一 kill(见 `kill_sidecar`)。
static SIDECAR: Mutex<Option<Child>> = Mutex::new(None);

// ---------------- 配置解析 ----------------

fn parse_port(raw: Option<String>) -> u16 {
    raw.and_then(|s| s.trim().parse::<u16>().ok())
        .filter(|p| *p != 0)
        .unwrap_or(DEFAULT_PORT)
}

fn insar_port() -> u16 {
    parse_port(std::env::var("INSAR_PORT").ok())
}

/// 探测 Python:INSAR_PYTHON > C:\Python314\python.exe > PATH。
/// 返回(解释器路径,来源说明——用于日志与诊断页)。
fn find_python() -> Result<(PathBuf, String), String> {
    if let Some(v) = std::env::var_os("INSAR_PYTHON") {
        let p = PathBuf::from(&v);
        if p.is_file() {
            return Ok((p, "环境变量 INSAR_PYTHON".into()));
        }
        return Err(format!("INSAR_PYTHON 指向的文件不存在:{}", p.display()));
    }
    let default = PathBuf::from(DEFAULT_WINDOWS_PYTHON);
    if default.is_file() {
        return Ok((default, format!("默认宿主 {DEFAULT_WINDOWS_PYTHON}")));
    }
    if let Some(paths) = std::env::var_os("PATH") {
        for dir in std::env::split_paths(&paths) {
            if dir.as_os_str().is_empty() {
                continue;
            }
            for name in ["python.exe", "python3.exe", "python", "python3"] {
                let cand = dir.join(name);
                if cand.is_file() {
                    return Ok((cand, "PATH".into()));
                }
            }
        }
    }
    Err(format!(
        "未找到 Python 解释器(依次尝试:INSAR_PYTHON、{DEFAULT_WINDOWS_PYTHON}、PATH)"
    ))
}

/// sidecar 工作目录 = 仓库根。
/// dev 模式(cargo build/run):CARGO_MANIFEST_DIR 是 desktop/,其父目录即仓库根
/// (编译期烙入)。打包分发后该路径不存在,回退到当前工作目录 —— 打包模式的
/// 正式方案(应用数据目录)见 README「打包路线」。
fn repo_root() -> PathBuf {
    if let Some(root) = Path::new(env!("CARGO_MANIFEST_DIR")).parent() {
        if root.join("src").join("insar_agent").is_dir() {
            return root.to_path_buf();
        }
    }
    std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."))
}

// ---------------- sidecar 生命周期 ----------------

fn sidecar_log_path(port: u16) -> PathBuf {
    std::env::temp_dir().join(format!("insar-agent-sidecar-{port}.log"))
}

fn spawn_sidecar(python: &Path, port: u16, workdir: &Path) -> Result<(), String> {
    let log_path = sidecar_log_path(port);
    let log = File::create(&log_path)
        .map_err(|e| format!("无法创建 sidecar 日志 {}:{e}", log_path.display()))?;
    let log_err = log
        .try_clone()
        .map_err(|e| format!("日志句柄复制失败:{e}"))?;

    let mut cmd = Command::new(python);
    cmd.args(["-m", "insar_agent.api.app"])
        .current_dir(workdir)
        .env("INSAR_PORT", port.to_string())
        .stdin(Stdio::null())
        .stdout(Stdio::from(log))
        .stderr(Stdio::from(log_err));
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000; // 避免弹出黑色控制台窗
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    let child = cmd
        .spawn()
        .map_err(|e| format!("启动 sidecar 失败({}):{e}", python.display()))?;
    *SIDECAR.lock().unwrap() = Some(child);
    Ok(())
}

/// 直接 kill sidecar:后端有 orphan 检测与 reattach 语义,
/// 「桌面关闭 → 重开」= 断点续跑(SQLite 中 run/step 状态完整保留)。
fn kill_sidecar() {
    if let Some(mut child) = SIDECAR.lock().unwrap().take() {
        let _ = child.kill();
        let _ = child.wait();
    }
}

// ---------------- 极简 HTTP(健康检查不为此引 reqwest 等重依赖) ----------------

/// 对 host:port 发一个最小 HTTP/1.1 GET,返回响应状态码。
fn http_get_status(host: &str, port: u16, path: &str, timeout: Duration) -> Option<u16> {
    let addr: std::net::SocketAddr = format!("{host}:{port}").parse().ok()?;
    let mut stream = TcpStream::connect_timeout(&addr, timeout).ok()?;
    stream.set_read_timeout(Some(timeout)).ok()?;
    stream.set_write_timeout(Some(timeout)).ok()?;
    let request = format!(
        "GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nAccept: */*\r\nConnection: close\r\n\r\n"
    );
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
    http_get_status("127.0.0.1", port, "/api/health", Duration::from_secs(2)) == Some(200)
}

/// 轮询健康检查;sidecar 提前退出则立即失败(不空等满超时)。
fn wait_health(port: u16, timeout: Duration) -> Result<(), String> {
    let deadline = Instant::now() + timeout;
    loop {
        if health_ok(port) {
            return Ok(());
        }
        if let Some(child) = SIDECAR.lock().unwrap().as_mut() {
            if let Ok(Some(status)) = child.try_wait() {
                return Err(format!("sidecar 进程提前退出({status})"));
            }
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

// ---------------- 诊断页(启动失败时展示,绝不静默退出) ----------------

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

fn diagnostics_html(error: &str, python_desc: &str, port: u16, workdir: &Path) -> String {
    let log_path = sidecar_log_path(port);
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
<h1>InSAR-Agent 后端(Python sidecar)启动失败</h1>
<p><strong>{error}</strong></p>
<table>
<tr><td class="k">Python</td><td><code>{python}</code></td></tr>
<tr><td class="k">启动命令</td><td><code>python -m insar_agent.api.app</code></td></tr>
<tr><td class="k">端口</td><td><code>{port}</code>(环境变量 <code>INSAR_PORT</code> 可改)</td></tr>
<tr><td class="k">工作目录</td><td><code>{workdir}</code></td></tr>
<tr><td class="k">sidecar 日志</td><td><code>{log}</code></td></tr>
</table>
<h2>日志尾部</h2>
<pre>{log_tail}</pre>
<h2>排查建议</h2>
<ul>
<li>确认该解释器已安装本项目:<code>pip install -e .</code>,并能手动运行
<code>python -m insar_agent.api.app</code></li>
<li>用 <code>INSAR_PYTHON</code> 显式指定解释器;用 <code>INSAR_PORT</code> 避开端口冲突</li>
<li>修复后关闭本窗口重开应用即可 —— 后端支持断点续跑,不会丢进度</li>
</ul>
</body></html>"#,
        error = escape_html(error),
        python = escape_html(python_desc),
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
        let built = WebviewWindowBuilder::new(&handle_, label.as_str(), WebviewUrl::External(parsed))
            .title(title.as_str())
            .inner_size(size.0, size.1)
            .build();
        if let Err(e) = built {
            eprintln!("[desktop] 创建窗口失败:{e}");
            handle_.exit(1);
        }
    });
}

fn open_main_window(handle: &AppHandle, port: u16) {
    open_window(
        handle,
        "main",
        "InSAR-Agent",
        format!("http://127.0.0.1:{port}/"),
        (1440.0, 900.0),
    );
}

fn show_diagnostics(handle: &AppHandle, error: &str, python_desc: &str, port: u16, workdir: &Path) {
    eprintln!("[desktop] 启动失败:{error}");
    match serve_html(diagnostics_html(error, python_desc, port, workdir)) {
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

/// 后台启动流程:探测 → spawn → 健康检查 → 开窗;任何失败都开诊断窗。
fn boot(handle: AppHandle, port: u16) {
    let workdir = repo_root();

    // 端口上已有健康后端(如手动起的 dev server):直接复用,
    // 不 spawn、也不接管其生命周期(退出时 SIDECAR 为空,不会误杀)
    if health_ok(port) {
        println!("[desktop] 端口 {port} 已有健康后端,直接连接(不 spawn sidecar)");
        open_main_window(&handle, port);
        return;
    }

    let (python, python_src) = match find_python() {
        Ok(v) => v,
        Err(e) => {
            show_diagnostics(&handle, &e, "(未找到)", port, &workdir);
            return;
        }
    };
    let python_desc = format!("{}(来源:{python_src})", python.display());
    println!("[desktop] python = {python_desc}");
    println!(
        "[desktop] spawn sidecar:cwd={},log={}",
        workdir.display(),
        sidecar_log_path(port).display()
    );

    if let Err(e) = spawn_sidecar(&python, port, &workdir) {
        show_diagnostics(&handle, &e, &python_desc, port, &workdir);
        return;
    }
    match wait_health(port, Duration::from_secs(HEALTH_TIMEOUT_SECS)) {
        Ok(()) => {
            println!("[desktop] /api/health = 200,打开主窗口");
            open_main_window(&handle, port);
        }
        Err(e) => show_diagnostics(&handle, &e, &python_desc, port, &workdir),
    }
}

/// 无 GUI 自检(`--smoke` 或 INSAR_DESKTOP_SMOKE=1):
/// spawn sidecar → 等健康检查 → kill → 退出码 0/1。无人值守/CI 验证用。
fn run_smoke(port: u16) -> i32 {
    println!("[smoke] 端口 {port}");
    if health_ok(port) {
        println!("[smoke] OK:端口已有健康后端(跳过 spawn)");
        return 0;
    }
    let workdir = repo_root();
    let (python, src) = match find_python() {
        Ok(v) => v,
        Err(e) => {
            eprintln!("[smoke] FAIL:{e}");
            return 1;
        }
    };
    println!(
        "[smoke] python={}({src}) cwd={}",
        python.display(),
        workdir.display()
    );
    if let Err(e) = spawn_sidecar(&python, port, &workdir) {
        eprintln!("[smoke] FAIL:{e}");
        return 1;
    }
    let result = wait_health(port, Duration::from_secs(HEALTH_TIMEOUT_SECS));
    kill_sidecar();
    match result {
        Ok(()) => {
            println!("[smoke] OK:/api/health = 200,sidecar 已终止");
            0
        }
        Err(e) => {
            eprintln!(
                "[smoke] FAIL:{e}(日志:{})",
                sidecar_log_path(port).display()
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

    let app = tauri::Builder::default()
        .setup(move |app| {
            let handle = app.handle().clone();
            // 健康检查最长 30 秒,放后台线程,避免卡死事件循环
            std::thread::spawn(move || boot(handle, port));
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("tauri 应用构建失败");

    app.run(|_handle, event| {
        if let RunEvent::Exit = event {
            kill_sidecar(); // 窗口全关/应用退出 → 终止 sidecar(断点续跑语义)
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
}
