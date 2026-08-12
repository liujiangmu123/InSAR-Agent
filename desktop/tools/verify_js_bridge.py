# -*- coding: utf-8 -*-
"""JS 桥实机验证脚本(通过 WebView2 CDP 远程调试端口对真实窗口取证)。

用法(仓库根执行;依赖 websocket-client,已随 .venv 安装):
  1. 以调试端口启动桌面壳(PowerShell):
       $env:WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS="--remote-debugging-port=9333"
       $env:INSAR_PORT="18973"
       desktop\\target\\debug\\insar-agent-desktop.exe
  2. 取证:
       .venv\\Scripts\\python.exe desktop\\tools\\verify_js_bridge.py list
       .venv\\Scripts\\python.exe desktop\\tools\\verify_js_bridge.py verify-main --url-prefix http://127.0.0.1:18973
       .venv\\Scripts\\python.exe desktop\\tools\\verify_js_bridge.py verify-diag --exclude-prefix http://127.0.0.1:18973

原理:CDP Runtime.evaluate 在窗口真实 JS 上下文里执行断言表达式,
结果以 JSON 打印 —— 与 F12 控制台手工验证等价,但可无人值守留痕。
pick_directory 的原生对话框用 Win32 WM_CLOSE 自动取消(等价用户按 Esc)。
"""

from __future__ import annotations

import argparse
import ctypes
import json
import sys
import time
import urllib.request

from websocket import create_connection  # websocket-client

WM_CLOSE = 0x0010


def fetch_pages(cdp_port: int) -> list[dict]:
    """列出调试端口上的所有页面(type=page)。"""
    with urllib.request.urlopen(f"http://127.0.0.1:{cdp_port}/json/list", timeout=5) as resp:
        items = json.load(resp)
    return [it for it in items if it.get("type") == "page"]


def cdp_eval(ws_url: str, expression: str, await_promise: bool = True, timeout: float = 30.0) -> dict:
    """在页面 JS 上下文执行表达式,返回 CDP Runtime.evaluate 的 result 字段。"""
    # suppress_origin:不发 Origin 头 —— 新版 DevTools 拒绝陌生 Origin 的握手
    ws = create_connection(ws_url, timeout=timeout, suppress_origin=True)
    try:
        ws.send(json.dumps({
            "id": 1,
            "method": "Runtime.evaluate",
            "params": {
                "expression": expression,
                "awaitPromise": await_promise,
                "returnByValue": True,
            },
        }))
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = json.loads(ws.recv())
            if msg.get("id") == 1:
                if "error" in msg:
                    return {"cdpError": msg["error"]}
                result = msg.get("result", {})
                if "exceptionDetails" in result:
                    exc = result["exceptionDetails"]
                    text = exc.get("exception", {}).get("description") or exc.get("text")
                    return {"exception": text}
                return {"value": result.get("result", {}).get("value")}
        return {"cdpError": "timeout waiting evaluate reply"}
    finally:
        ws.close()


def pick_page(pages: list[dict], url_prefix: str | None, exclude_prefix: str | None) -> dict | None:
    for p in pages:
        url = p.get("url", "")
        if url_prefix and url.startswith(url_prefix):
            return p
        if exclude_prefix and not url.startswith(exclude_prefix) and url.startswith("http://127.0.0.1"):
            return p
    return None


# ---------------- 取证表达式 ----------------

INJECT_EXPR = """
(() => {
  const t = window.__TAURI__;
  return {
    href: location.href,
    title: document.title,
    tauriType: typeof t,
    tauriKeys: t ? Object.keys(t).sort() : null,
    coreInvokeType: (t && t.core) ? typeof t.core.invoke : null,
    internalsType: typeof window.__TAURI_INTERNALS__,
    chromeWebview: !!(window.chrome && window.chrome.webview),
    titlebarIsDesktopExpr: !!(window.__TAURI__ || window.__TAURI_INTERNALS__ || (window.chrome && window.chrome.webview)),
  };
})()
"""

DESKTOP_JS_EXPR = """
import('/js/desktop.js').then(async (m) => ({
  isDesktop: m.isDesktop(),
  appInfo: await m.appInfo(),
})).catch((e) => ({ importError: String(e) }))
"""

DIRECT_INVOKE_EXPR = """
(async () => {
  const t = window.__TAURI__;
  const inv = t && t.core && t.core.invoke ? (c, a) => t.core.invoke(c, a) : null;
  if (!inv) return { error: 'window.__TAURI__.core.invoke 不存在' };
  const out = {};
  try { out.app_info = { ok: true, value: await inv('app_info') }; }
  catch (e) { out.app_info = { ok: false, error: String(e) }; }
  try { const r = await inv('open_path', { path: '' }); out.open_path_empty = { ok: true, value: r === null ? 'null' : String(r) }; }
  catch (e) { out.open_path_empty = { ok: false, error: String(e) }; }
  return out;
})()
"""

# 诊断窗:__TAURI__ 缺席时,再直接探 __TAURI_INTERNALS__.invoke,拿精确失败层
DIAG_PROBE_EXPR = """
(async () => {
  const out = {};
  const t = window.__TAURI__;
  out.tauriPresent = t != null;
  const internals = window.__TAURI_INTERNALS__;
  out.internalsPresent = internals != null;
  const inv = (t && t.core && t.core.invoke) || (internals && internals.invoke) || null;
  if (!inv) { out.invokeAttempt = '无任何 invoke 入口可用'; return out; }
  try { out.invokeAttempt = { ok: true, value: await inv('app_info') }; }
  catch (e) { out.invokeAttempt = { ok: false, error: String(e) }; }
  return out;
})()
"""

PICK_START_EXPR = """
(() => {
  window.__insarPickProbe = { state: 'pending' };
  window.__TAURI__.core.invoke('pick_directory', { title: '%TITLE%' })
    .then((v) => { window.__insarPickProbe = { state: 'resolved', value: v === null ? 'null' : String(v) }; })
    .catch((e) => { window.__insarPickProbe = { state: 'rejected', error: String(e) }; });
  return 'started';
})()
"""

PICK_POLL_EXPR = "window.__insarPickProbe || { state: 'missing' }"

PICK_DIALOG_TITLE = "桥验证-自动关闭"


def close_native_dialog(title: str, timeout: float = 8.0) -> bool:
    """按标题找原生对话框窗口,发 WM_CLOSE(等价用户取消)。"""
    user32 = ctypes.windll.user32
    deadline = time.time() + timeout
    while time.time() < deadline:
        hwnd = user32.FindWindowW(None, title)
        if hwnd:
            user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
            return True
        time.sleep(0.2)
    return False


def verify_main(page: dict) -> dict:
    ws = page["webSocketDebuggerUrl"]
    report: dict = {"page": {"url": page.get("url"), "title": page.get("title")}}
    report["a_injection"] = cdp_eval(ws, INJECT_EXPR)
    report["b_desktop_js"] = cdp_eval(ws, DESKTOP_JS_EXPR)
    report["c_direct_invoke"] = cdp_eval(ws, DIRECT_INVOKE_EXPR)

    # c-2:pick_directory 弹真对话框 → WM_CLOSE 自动取消 → 断言 resolve null
    start = cdp_eval(ws, PICK_START_EXPR.replace("%TITLE%", PICK_DIALOG_TITLE), await_promise=False)
    pick: dict = {"start": start}
    if start.get("value") == "started":
        pick["dialogClosed"] = close_native_dialog(PICK_DIALOG_TITLE)
        deadline = time.time() + 10
        status: dict = {}
        while time.time() < deadline:
            status = cdp_eval(ws, PICK_POLL_EXPR, await_promise=False)
            if (status.get("value") or {}).get("state") not in (None, "pending"):
                break
            time.sleep(0.3)
        pick["result"] = status
    report["c_pick_directory"] = pick
    return report


def verify_diag(page: dict) -> dict:
    ws = page["webSocketDebuggerUrl"]
    return {
        "page": {"url": page.get("url"), "title": page.get("title")},
        "d_injection": cdp_eval(ws, INJECT_EXPR),
        "d_invoke_probe": cdp_eval(ws, DIAG_PROBE_EXPR),
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("mode", choices=["list", "verify-main", "verify-diag"])
    ap.add_argument("--cdp-port", type=int, default=9333)
    ap.add_argument("--url-prefix", default=None, help="目标页面 URL 前缀(verify-main 用)")
    ap.add_argument("--exclude-prefix", default=None, help="排除该前缀后取第一个 127.0.0.1 页面(verify-diag 用)")
    args = ap.parse_args()

    pages = fetch_pages(args.cdp_port)
    if args.mode == "list":
        print(json.dumps([{k: p.get(k) for k in ("title", "url")} for p in pages],
                         ensure_ascii=False, indent=2))
        return 0

    page = pick_page(pages, args.url_prefix, args.exclude_prefix)
    if page is None:
        print(json.dumps({"error": "未找到目标页面", "pages": [p.get("url") for p in pages]},
                         ensure_ascii=False))
        return 1
    report = verify_main(page) if args.mode == "verify-main" else verify_diag(page)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
