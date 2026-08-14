//! 「打开本地数据」原生通路:目录 → 后端注册 → 前端刷新(桌面版专属价值点)。
//!
//! Web 端受浏览器沙箱限制拿不到绝对路径;桌面壳在这里补齐三条入口:
//!   1. 托盘菜单「打开数据文件夹…」(tray.rs 接线)→ 原生目录对话框;
//!   2. 前端「浏览…」按钮(prototype/js/desktopdata.js)→ 复用既有
//!      pick_directory command(commands.rs),路径在页面侧走 datasets.js
//!      的 addRoot 注册,不经本模块;
//!   3. 主窗口拖拽本地目录(WindowEvent::DragDrop,install() 挂钩子)。
//!
//! 1/3 两条壳侧入口共用同一注册通路 register_and_notify:
//!   POST http://127.0.0.1:<后端端口>/api/datasets/roots {path}
//!   (绝对路径 / 存在性 / 拒穿越均由后端把关,壳侧不重复校验)
//!   → eval 向页面广播 CustomEvent:
//!     - datasets:root-registered {path, ok, error}:回执 toast(desktopdata.js 承接);
//!     - datasets:refresh(仅成功时):数据集区刷新(datasets.js 监听)。
//!
//! 后端端口由 main.rs 启动编排在健康检查通过后写入(set_backend_port);
//! HTTP 客户端与 main.rs 同款手写 std 实现,不为一次 POST 引 reqwest。
//! 权限面:不引 fs / dialog 插件 —— 目录选择走 rfd(仅 pick_folder,
//! 无文件读写能力),拖拽/托盘全程 Rust 侧完成,webview ACL 零新增。

use std::io::{Read, Write};
use std::net::TcpStream;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU16, Ordering};
use std::time::Duration;

use tauri::{AppHandle, DragDropEvent, Manager, Runtime, WindowEvent};

use crate::tray::MAIN_WINDOW_LABEL;

/// 页面回执事件名(desktopdata.js 监听后 toast;两端契约,改名须同步)。
const EVENT_REGISTERED: &str = "datasets:root-registered";
/// 数据集区刷新事件名(datasets.js 监听;两端契约,改名须同步)。
const EVENT_REFRESH: &str = "datasets:refresh";

/// 注册请求的读写超时:本机回环 + 后端同步端点,秒级足够。
const HTTP_TIMEOUT: Duration = Duration::from_secs(5);

// ---------------- 后端端口(main.rs 健康检查通过后写入) ----------------

/// 0 = 后端尚未就绪(端口从不为 0,见 main.rs parse_port)。
static BACKEND_PORT: AtomicU16 = AtomicU16::new(0);

/// 记录已通过健康检查的后端端口(main.rs 的 Reuse / Spawn 成功路径各调一次)。
pub fn set_backend_port(port: u16) {
    BACKEND_PORT.store(port, Ordering::Relaxed);
}

fn backend_port() -> Option<u16> {
    match BACKEND_PORT.load(Ordering::Relaxed) {
        0 => None,
        p => Some(p),
    }
}

// ---------------- 入口 1:托盘「打开数据文件夹…」 ----------------

/// 弹原生目录对话框 → 注册为数据扫描根 → 通知页面刷新。
///
/// rfd 异步对话框在独立线程弹出,不阻塞托盘菜单所在的主事件循环;
/// 用户取消时什么都不做(与 commands::pick_directory 的取消语义一致)。
pub fn open_data_folder_flow<R: Runtime>(app: &AppHandle<R>) {
    let app = app.clone();
    tauri::async_runtime::spawn(async move {
        let picked = tauri::async_runtime::spawn_blocking(|| {
            crate::commands::pick_folder_sync("选择数据文件夹(注册到数据集)")
        })
        .await
        .ok()
        .flatten();
        let Some(dir) = picked else { return };
        register_and_notify(&app, &dir);
    });
}

// ---------------- 入口 3:主窗口拖拽本地目录 ----------------

/// 给之后创建的 "main" 窗口挂拖拽钩子(模式同 tray.rs 关闭到托盘:主窗由
/// main.rs 健康检查后才创建,须经 window_ready 回调而非 setup 时直挂)。
///
/// Tauri 2 默认 dragDropEnabled:WebView2 把落下的本地路径转成
/// WindowEvent::DragDrop(Rust 侧),页面拿不到也不需要拿。只认目录,
/// 拖入文件时忽略(数据集注册的最小单位是目录)。
pub fn install<R: Runtime>(app: &AppHandle<R>) -> tauri::Result<()> {
    app.plugin(
        tauri::plugin::Builder::<R, ()>::new("insar-open-data")
            .on_window_ready(|window| {
                if window.label() != MAIN_WINDOW_LABEL {
                    return;
                }
                let app = window.app_handle().clone();
                window.on_window_event(move |event| {
                    if let WindowEvent::DragDrop(DragDropEvent::Drop { paths, .. }) = event {
                        let dirs = dirs_only(paths, |p| p.is_dir());
                        if dirs.is_empty() {
                            return; // 全是文件:不打扰(注册单位是目录)
                        }
                        let app = app.clone();
                        // 注册走网络 IO,挪出窗口事件回调线程
                        std::thread::spawn(move || {
                            for dir in dirs {
                                register_and_notify(&app, &dir.to_string_lossy());
                            }
                        });
                    }
                });
            })
            .build(),
    )
}

/// 拖拽负载里只留目录(存在性判据注入便于单测)。
fn dirs_only(paths: &[PathBuf], is_dir: impl Fn(&Path) -> bool) -> Vec<PathBuf> {
    paths.iter().filter(|p| is_dir(p)).cloned().collect()
}

// ---------------- 共用注册通路 ----------------

/// 注册目录为数据扫描根,并把结果送进页面(toast 回执 + 成功时刷新)。
///
/// 失败不弹系统对话框:主窗口在场就走页面 toast(链路与成功一致),
/// 不在场(后端启动中)只记日志 —— 壳的桥语义是尽力而为、绝不打断。
fn register_and_notify<R: Runtime>(app: &AppHandle<R>, dir: &str) {
    let result = match backend_port() {
        Some(port) => register_root(port, dir),
        None => Err("后端尚未就绪,请稍后重试".into()),
    };
    match &result {
        Ok(()) => println!("[opendata] 已注册数据根:{dir}"),
        Err(e) => eprintln!("[opendata] 注册数据根失败 {dir}:{e}"),
    }
    let Some(window) = app.get_webview_window(MAIN_WINDOW_LABEL) else {
        return;
    };
    if result.is_ok() {
        // 托盘/拖拽可能发生在窗口隐藏时:成功后唤起窗口让用户看到新数据集
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
    }
    let script = notify_script(dir, result.is_ok(), result.as_ref().err().map(String::as_str));
    if let Err(e) = window.eval(&script) {
        eprintln!("[opendata] 页面通知失败:{e}");
    }
}

/// POST /api/datasets/roots {path}。200 → Ok;其余状态码取后端 detail 作错误文案。
fn register_root(port: u16, dir: &str) -> Result<(), String> {
    let body = serde_json::json!({ "path": dir }).to_string();
    let (status, response_body) = http_post_json("127.0.0.1", port, "/api/datasets/roots", &body)?;
    if status == 200 {
        return Ok(());
    }
    Err(extract_detail(&response_body).unwrap_or_else(|| format!("后端返回 HTTP {status}")))
}

/// 生成注入页面的通知脚本:回执事件必发,刷新事件与「切到文件面板」仅成功时。
///
/// 路径/错误经 serde_json 转义成合法 JS 字符串字面量(Windows 反斜杠、
/// 引号、中文均安全);切面板复刻 tray.rs OPEN_ENV_TAB_JS 的锚点约定
/// (#railDock 展开收起的 dock,#tab-files 是文件面板标签,见 dock.js)。
fn notify_script(path: &str, ok: bool, error: Option<&str>) -> String {
    let detail = serde_json::json!({ "path": path, "ok": ok, "error": error });
    let on_success = if ok {
        format!(
            "window.dispatchEvent(new CustomEvent('{EVENT_REFRESH}'));\
             var dock = document.getElementById('dock');\
             if (dock && dock.hidden) {{\
               var rail = document.getElementById('railDock');\
               if (rail) rail.click();\
             }}\
             var tab = document.getElementById('tab-files');\
             if (tab) tab.click();"
        )
    } else {
        String::new()
    };
    format!(
        "(() => {{ try {{\
           window.dispatchEvent(new CustomEvent('{EVENT_REGISTERED}', {{ detail: {detail} }}));\
           {on_success}\
         }} catch (e) {{}} }})();"
    )
}

// ---------------- 极简 HTTP POST(与 main.rs 健康检查同款手写 std) ----------------

/// 组装最小 HTTP/1.1 POST 请求头(纯字符串拼装,拆出便于单测)。
fn http_post_head(host: &str, port: u16, path: &str, body_len: usize) -> String {
    format!(
        "POST {path} HTTP/1.1\r\nHost: {host}:{port}\r\n\
         Content-Type: application/json\r\nContent-Length: {body_len}\r\n\
         Accept: application/json\r\nConnection: close\r\n\r\n"
    )
}

/// 发一个最小 JSON POST,返回 (状态码, 响应 body)。
///
/// Connection: close + 读到 EOF:不解析分块编码 —— 本地 FastAPI 对
/// close 语义的响应带 Content-Length,读尽即完整。
fn http_post_json(
    host: &str,
    port: u16,
    path: &str,
    body: &str,
) -> Result<(u16, String), String> {
    let addr: std::net::SocketAddr = format!("{host}:{port}")
        .parse()
        .map_err(|e| format!("地址解析失败:{e}"))?;
    let mut stream = TcpStream::connect_timeout(&addr, HTTP_TIMEOUT)
        .map_err(|e| format!("无法连接后端 {host}:{port}:{e}"))?;
    stream
        .set_read_timeout(Some(HTTP_TIMEOUT))
        .map_err(|e| e.to_string())?;
    stream
        .set_write_timeout(Some(HTTP_TIMEOUT))
        .map_err(|e| e.to_string())?;
    let head = http_post_head(host, port, path, body.len());
    stream
        .write_all(head.as_bytes())
        .and_then(|_| stream.write_all(body.as_bytes()))
        .map_err(|e| format!("请求发送失败:{e}"))?;

    let mut raw = Vec::new();
    let mut buf = [0u8; 4096];
    while raw.len() < 256 * 1024 {
        match stream.read(&mut buf) {
            Ok(0) => break,
            Ok(n) => raw.extend_from_slice(&buf[..n]),
            Err(_) => break, // 超时/断连:按已收内容解析,状态行不全自然报错
        }
    }
    split_response(&String::from_utf8_lossy(&raw))
}

/// 拆 HTTP 响应:状态行 → 状态码,空行之后 → body。
fn split_response(raw: &str) -> Result<(u16, String), String> {
    let status_line = raw.lines().next().unwrap_or_default();
    let mut parts = status_line.split_whitespace();
    let proto_ok = parts.next().is_some_and(|p| p.starts_with("HTTP/"));
    let status: Option<u16> = parts.next().and_then(|s| s.parse().ok());
    let (Some(status), true) = (status, proto_ok) else {
        return Err("后端响应不完整(未收到状态行)".into());
    };
    let body = raw
        .split_once("\r\n\r\n")
        .map(|(_, b)| b.to_string())
        .unwrap_or_default();
    Ok((status, body))
}

/// 从后端错误 body 提取 FastAPI 的 {"detail": "..."};解析不出返回 None。
fn extract_detail(body: &str) -> Option<String> {
    let value: serde_json::Value = serde_json::from_str(body).ok()?;
    value.get("detail")?.as_str().map(str::to_string)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::TcpListener;

    // ---------------- 端口记录 ----------------

    /// 单进程内唯一触碰 BACKEND_PORT 的测试(static 共享,拆多个会互相踩)。
    #[test]
    fn backend_port_roundtrip() {
        assert_eq!(backend_port(), None, "初始未就绪应为 None");
        set_backend_port(8873);
        assert_eq!(backend_port(), Some(8873));
        set_backend_port(0);
        assert_eq!(backend_port(), None, "写回 0 等价未就绪");
    }

    // ---------------- 拖拽负载过滤 ----------------

    #[test]
    fn dirs_only_keeps_directories_and_preserves_order() {
        let paths = vec![
            PathBuf::from("E:/data/hyp3"),
            PathBuf::from("E:/data/readme.txt"),
            PathBuf::from("E:/data/slc"),
        ];
        let dirs = dirs_only(&paths, |p| !p.extension().is_some());
        assert_eq!(
            dirs,
            vec![PathBuf::from("E:/data/hyp3"), PathBuf::from("E:/data/slc")]
        );
    }

    #[test]
    fn dirs_only_empty_when_all_files() {
        let paths = vec![PathBuf::from("a.tif"), PathBuf::from("b.zip")];
        assert!(dirs_only(&paths, |_| false).is_empty());
    }

    // ---------------- HTTP 请求/响应纯逻辑 ----------------

    #[test]
    fn http_post_head_is_well_formed() {
        let head = http_post_head("127.0.0.1", 8873, "/api/datasets/roots", 27);
        assert!(head.starts_with("POST /api/datasets/roots HTTP/1.1\r\n"));
        assert!(head.contains("Host: 127.0.0.1:8873\r\n"));
        assert!(head.contains("Content-Type: application/json\r\n"));
        assert!(head.contains("Content-Length: 27\r\n"));
        assert!(head.contains("Connection: close\r\n"));
        assert!(head.ends_with("\r\n\r\n"), "请求头必须以空行结束");
    }

    #[test]
    fn split_response_extracts_status_and_body() {
        let raw = "HTTP/1.1 400 Bad Request\r\nContent-Type: application/json\r\n\r\n{\"detail\":\"x\"}";
        assert_eq!(
            split_response(raw),
            Ok((400, "{\"detail\":\"x\"}".to_string()))
        );
    }

    #[test]
    fn split_response_rejects_garbage() {
        assert!(split_response("").is_err());
        assert!(split_response("not http at all").is_err());
    }

    #[test]
    fn extract_detail_reads_fastapi_error_shape() {
        assert_eq!(
            extract_detail("{\"detail\":\"必须是绝对路径\"}"),
            Some("必须是绝对路径".to_string())
        );
        assert_eq!(extract_detail("{\"other\":1}"), None);
        assert_eq!(extract_detail("<html>500</html>"), None);
        assert_eq!(extract_detail("{\"detail\":{\"k\":1}}"), None, "非字符串 detail 不硬转");
    }

    // ---------------- 注册通路(本地假后端) ----------------

    /// 起一个只答一次的本地 TCP 假后端:收全请求(头 + Content-Length body)
    /// 再回写 canned 响应,顺便把收到的请求文本传出来供断言。
    fn one_shot_backend(response: &'static str) -> (u16, std::sync::mpsc::Receiver<String>) {
        let listener = TcpListener::bind(("127.0.0.1", 0)).unwrap();
        let port = listener.local_addr().unwrap().port();
        let (tx, rx) = std::sync::mpsc::channel();
        std::thread::spawn(move || {
            if let Ok((mut s, _)) = listener.accept() {
                let mut got = Vec::new();
                let mut buf = [0u8; 1024];
                loop {
                    let text = String::from_utf8_lossy(&got).into_owned();
                    if let Some((head, body)) = text.split_once("\r\n\r\n") {
                        let want: usize = head
                            .lines()
                            .find_map(|l| l.strip_prefix("Content-Length: "))
                            .and_then(|v| v.trim().parse().ok())
                            .unwrap_or(0);
                        if body.len() >= want {
                            break;
                        }
                    }
                    match s.read(&mut buf) {
                        Ok(0) | Err(_) => break,
                        Ok(n) => got.extend_from_slice(&buf[..n]),
                    }
                }
                let _ = tx.send(String::from_utf8_lossy(&got).into_owned());
                let _ = s.write_all(response.as_bytes());
            }
        });
        (port, rx)
    }

    #[test]
    fn register_root_succeeds_on_200() {
        let (port, rx) = one_shot_backend(
            "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n{\"ok\":true}",
        );
        assert_eq!(register_root(port, "E:\\data\\insar"), Ok(()));
        let request = rx.recv_timeout(Duration::from_secs(5)).unwrap();
        assert!(request.starts_with("POST /api/datasets/roots HTTP/1.1\r\n"));
        // 路径按 JSON 语义转义(反斜杠翻倍),后端 pydantic 侧解回原文
        assert!(request.ends_with("{\"path\":\"E:\\\\data\\\\insar\"}"), "请求体:{request}");
    }

    #[test]
    fn register_root_surfaces_backend_detail_on_400() {
        let (port, _rx) = one_shot_backend(
            "HTTP/1.1 400 Bad Request\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n\
             {\"detail\":\"目录不存在或不是目录:Z:\\\\nope\"}",
        );
        let err = register_root(port, "Z:\\nope").unwrap_err();
        assert!(err.contains("目录不存在"), "应透传后端 detail:{err}");
    }

    #[test]
    fn register_root_fails_gracefully_when_backend_down() {
        // 绑定后立刻释放:大概率无人监听的端口
        let port = {
            let l = TcpListener::bind(("127.0.0.1", 0)).unwrap();
            l.local_addr().unwrap().port()
        };
        let err = register_root(port, "E:\\data").unwrap_err();
        assert!(err.contains("无法连接后端"), "{err}");
    }

    // ---------------- 页面通知脚本(与前端的事件名契约) ----------------

    #[test]
    fn notify_script_success_dispatches_receipt_and_refresh() {
        let js = notify_script("E:\\新 数据\\hyp3", true, None);
        assert!(js.contains(EVENT_REGISTERED));
        assert!(js.contains(EVENT_REFRESH), "成功必须触发数据集区刷新");
        assert!(js.contains("tab-files"), "成功应切到文件面板(锚点约定见 dock.js)");
        assert!(js.contains("railDock"), "dock 收起时应先展开(同 tray.rs 约定)");
        // serde_json 转义:反斜杠翻倍、中文与空格原样,是合法 JS 字符串字面量
        assert!(js.contains("E:\\\\新 数据\\\\hyp3"), "{js}");
        assert!(js.contains("\"ok\":true"));
    }

    #[test]
    fn notify_script_failure_sends_receipt_only() {
        let js = notify_script("E:\\data", false, Some("后端返回 HTTP 500"));
        assert!(js.contains(EVENT_REGISTERED));
        assert!(!js.contains(EVENT_REFRESH), "失败不得触发刷新");
        assert!(!js.contains("tab-files"), "失败不切面板");
        assert!(js.contains("后端返回 HTTP 500"));
        assert!(js.contains("\"ok\":false"));
    }

    #[test]
    fn notify_script_escapes_quotes_in_error() {
        // 错误文案可能回显用户路径,含引号也不能把脚本撑破
        let js = notify_script("E:\\d", false, Some("路径不可访问:\"E:\\d\""));
        assert!(js.contains("\\\"E:\\\\d\\\""), "{js}");
    }

    /// 事件名契约锚点:前端 datasets.js / desktopdata.js 按这两个字面量监听,
    /// 任何一端改名都会撞上本断言或 prototype/desktopdata.check.mjs 的镜像断言。
    #[test]
    fn event_names_match_frontend_contract() {
        assert_eq!(EVENT_REFRESH, "datasets:refresh");
        assert_eq!(EVENT_REGISTERED, "datasets:root-registered");
    }
}
