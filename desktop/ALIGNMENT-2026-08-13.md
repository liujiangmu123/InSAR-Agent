# 桌面壳 × 后端安全响应头对齐核查(2026-08-13)

后端于 AUDIT-security-r2-2026-08-13 轮次给全部响应加了 `X-Frame-Options: DENY`,并给
静态 UI 的 HTML 按页下发 CSP(`src/insar_agent/api/app.py` 的 SecurityHeadersMiddleware)。
本文逐条核对桌面壳(Tauri 2.11.5 / wry,Windows WebView2)是否受影响。结论先行:

| 核查项 | 结论 |
|---|---|
| X-Frame-Options: DENY / CSP `frame-ancestors 'none'` | 无影响:壳以顶层导航加载,壳内零 iframe |
| CSP connect-src(回落 `default-src 'self'`)× Tauri IPC | 受影响但自动降级,JS 桥功能不破,壳侧无需改配置 |
| sidecar 就绪探测端点 `/api/health` | 后端仍存在,两侧一致,无需修 |

## 1. 加载方式:顶层导航实证(非 iframe)

- 壳内 webview 创建点全仓只有一处:`desktop/src/main.rs` 的 `open_window`(L269-292),
  其中 L282-283 `WebviewWindowBuilder::new(&handle_, label, WebviewUrl::External(parsed))`
  —— `WebviewUrl::External` 把后端 URL 作为窗口**顶层文档**加载。
- 主窗口 URL 由 `backend_url`(L294-297)拼出 `http://127.0.0.1:{port}/`,经
  `open_main_window`(L299-307)传入;诊断窗(`show_diagnostics` L319-347,开窗调用
  L335-341)加载的是壳自托管的独立静态页(`serve_html` L243-264,随机回环端口),
  该页不内嵌后端任何内容。
- 全 `desktop/` 源码 grep `iframe|<frame|frameElement` 零命中;`tauri.conf.json` 未配置
  `app.security.pattern`,走缺省 brownfield 模式(tauri 2.11.5 `scripts/ipc.js` L144-148
  的 pattern 分支实证),不存在 isolation iframe。
- 结论:`X-Frame-Options: DENY` 与 `frame-ancestors 'none'` 只约束「被嵌入」,对顶层
  导航不生效 —— app.py L168-171 中间件注释的声明成立,壳侧无需修复。

## 2. CSP connect-src 'self' × Tauri IPC(核心项)

### 事实链(静态分析)

1. 后端 CSP 骨架 `_CSP_BASE`(app.py L102-103)为 `default-src 'self'; object-src 'none';
   base-uri 'none'; form-action 'self'; frame-ancestors 'none'`,按页追加 script-src /
   style-src(`_page_csp`,L119-138)——**未显式列 connect-src,按 CSP 规范回落
   default-src,即 connect-src 实际为 'self'**;CSP 只随 text/html 文档下发(L196-198)。
2. 壳确实向后端页面注入了会走 IPC 的桥:`tauri.conf.json` `"withGlobalTauri": true`
   (L9)把 `window.__TAURI__` 注入每次页面加载(含远程 URL);UI 侧
   `prototype/js/desktop.js` 经 `window.__TAURI__.core.invoke` 调壳命令
   pick_directory / open_path / app_info;远程 origin 的调用授权由
   `desktop/capabilities/remote-ui.json` 的 `"remote": {"urls": ["http://127.0.0.1:*", …]}`
   提供(这是 Tauri 2 对远程 origin 授权 IPC 的正规机制)。
3. Tauri 2.11.5 的 invoke 传输通道(注入脚本 `scripts/ipc-protocol.js`,本机 cargo
   registry 源码实证):
   - 首选**自定义协议 fetch**:`fetch(convertFileSrc(cmd, 'ipc'))`(L37),Windows /
     WebView2 上即 `http://ipc.localhost/<命令名>` —— 这是页面上下文发起的普通 fetch,
     **受页面 CSP connect-src 管辖**;对 `http://127.0.0.1:<port>` 的文档而言
     `http://ipc.localhost` 是跨源目标,connect-src 'self' 会拦截它。
   - fetch 被拒后**内建自动回退**(L59-68):源码注释明确点名 "(either the webview
     blocked a custom protocol or **it was a CSP error**) so we need to fallback to the
     postMessage interface" —— 置 `customProtocolIpcFailed = true` 后改走
     `window.ipc.postMessage`(L84,wry 注入的 WebView2 原生
     `chrome.webview.postMessage` 通道)。postMessage 不是网络请求,**不受 CSP 任何
     指令约束**;同一页面的后续 invoke 直接走 postMessage,不再重试 fetch。

### 文档依据(上游)

- 官方 JS API 参考(convertFileSrc 一节)给出的示例 CSP 明确要求放行 IPC 端点:
  `"csp": "default-src 'self' ipc: http://ipc.localhost; …"`
  (https://v2.tauri.app/reference/javascript/api/namespacecore/)。
- [tauri#7842](https://github.com/tauri-apps/tauri/issues/7842):与本仓同构的报错样本
  ——「Refused to connect to 'http://ipc.localhost/…' … Note that **'connect-src' was not
  explicitly set, so 'default-src' is used as a fallback**」。
- [tauri#8476](https://github.com/tauri-apps/tauri/issues/8476):v2 外部 URL
  (WebviewUrl::External)场景下页面 CSP 拦截 `http://ipc.localhost` 的原始 issue;
  2.11.5 已带上述 postMessage 自动回退。

### 结论与修复评估

- **结论:受影响但自动降级,功能不破。** 后端 CSP 会拦掉每次页面加载后首个 invoke 的
  fetch 尝试(DevTools 一条 CSP violation + 一条 console.warn),随后该次及后续 invoke
  全部经 postMessage 正常完成;pick_directory / open_path / app_info 语义不变。
- **壳侧无需(也无法)配置修复**:fetch 由页面上下文发出,必受页面 CSP 管;
  `initialization_scripts` 改不了 ipc-protocol.js 闭包内的通道选择开关;
  `dangerousRemoteDomainIpcAccess` 是 Tauri **v1** 的配置项,v2 的等价物就是 capability
  的 `remote.urls`(本仓 remote-ui.json 已正确配置)—— 它管「远程 origin 是否被授权
  调用命令」,与 CSP 无关,两者不能互相替代。
- 若要消除首调告警与失败往返,唯一位置在**后端** CSP:显式加
  `connect-src 'self' ipc: http://ipc.localhost`(本次任务约定不改后端头,仅记录备选)。
- 附:`withGlobalTauri` 的注入与壳侧 `WebviewWindow::eval`(托盘「导出诊断包」用)都经
  WebView2 宿主通道(AddScriptToExecuteOnDocumentCreated / ExecuteScript)执行,不受
  页面 CSP script-src 限制;`desktop/tools/verify_js_bridge.py` 经 CDP Runtime.evaluate
  取证走的是同类通道。

## 3. sidecar 就绪探测端点

- 壳侧:`HEALTH_PATH = "/api/health"`(main.rs L36-37),启动轮询(`wait_health`
  L153-170)与端口复用判定(`health_ok` L148-150)都用它。
- 后端:`@app.get("/api/health")` 仍在(app.py L444-446,返回 `{"ok": True, "version": …}`)。
- 结论:一致,无需修。SecurityHeadersMiddleware 对 `/api/*` 只补 no-store 等头,
  不改状态码,不影响探测。

## 4. 本轮壳侧改动(托盘增强 + 数据目录口径修正)

- 托盘菜单新增「**导出诊断包**」:显示并聚焦主窗口后,经 `WebviewWindow::eval` 复刻
  Web UI 命令面板 gotoPane('env') 的既有入口(dock 收起先点 `#railDock` 展开,再点
  `#tab-env`,id 契约见 prototype/js/dock.js L40 / cmdk.js L53-57),并置
  `location.hash = '#env'` 留导航痕迹。诊断信息的收集与打包由后端在「环境」面板承接
  (并行开发中),壳侧只做入口跳转,不实现打包逻辑。
- 「**打开数据目录**」改经 tauri-plugin-opener(2.5.4)打开,目标从「数据根的上一级」
  修正为后端 INSAR_HOME 的实际路径(insar.db / settings.json 真实所在地):
  env `INSAR_HOME`(相对路径按 sidecar cwd=仓库根解析)> dev `{仓库根}\workspace`
  (app.py L343:缺省 home="workspace" 按 cwd 解析)> 冻结
  `%LOCALAPPDATA%\insar-agent-data\workspace`(backend-bundle/entry.py L52-54)。
  解析逻辑拆为纯函数 `resolve_workspace_dir`,五种组合入单测。
- 验证:`cargo check` 通过;`cargo test` 45 通过 / 0 失败(含新增 6 项)。
  注:fresh checkout 下 `cargo check` 需先保证 `backend-bundle/dist/insar-backend/`
  目录存在(空目录即可)—— tauri-build 在 build script 阶段复制 `bundle.resources`,
  源目录缺失会直接失败(dist 为 PyInstaller 产物,不入库)。
