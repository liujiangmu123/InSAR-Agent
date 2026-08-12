/* ============================================================
   titlebar.js — 运行状态 → 窗口/标签页标题同步(零依赖 ES Module,新增文件)。

   为什么轮询:不能改 app.js,订阅不了现有事件总线;改为每 10s 轮询一次
   GET /api/state?session=<id>(单条 SQL 的轻量端点),足够了。

   标题效果(baseTitle 是页面原始 <title>):
     浏览器    「运行中 · 第 6 步 · InSAR-Agent · 工作区」
     桌面壳    「● 运行中 · 第 6 步 · InSAR-Agent · 工作区」(● 运行中/○ 非运行)
   后端不可达 / 无会话 / 尚无 run:静默还原原始标题,绝不报错。

   桌面判定:withGlobalTauri 开启后,远程 URL(http://127.0.0.1)也会注入
   __TAURI__(2026-08-12 实机验证,见 desktop/INTEGRATION-commands.md §三);
   Windows WebView2 另有恒在的 chrome.webview —— 两者都认,双保险。

   集成(index.html 加一行,见 desktop/INTEGRATION-window.md):
   <script type="module">import { initTitleSync } from './js/titlebar.js'; initTitleSync();</script>
   ============================================================ */

const POLL_MS = 10_000;
const FETCH_TIMEOUT_MS = 5_000;

let timer = null;       // setInterval 句柄;非 null 表示已启动(幂等保护)
let baseTitle = null;   // 页面原始标题,还原用
let stateModule = null; // ./state.js(可选):动态 import 拿 S.sessionId,失败不要紧

function isDesktop() {
  try {
    return Boolean(
      window.__TAURI__ ||
      window.__TAURI_INTERNALS__ ||
      (window.chrome && window.chrome.webview),
    );
  } catch {
    return false;
  }
}

/** 会话 id 解析:显式参数 > URL ?session= > 前端状态镜像 S.sessionId。 */
function resolveSession(explicit) {
  if (explicit) return explicit;
  try {
    const q = new URLSearchParams(location.search).get('session');
    if (q) return q;
  } catch { /* file:// 等异常环境 */ }
  return (stateModule && stateModule.S && stateModule.S.sessionId) || null;
}

/**
 * /api/state 响应 → 标题文案;不需要加料的状态返回 null。
 * run.status 取值见 loop/driver.py:running / done / failed 之外
 * (ready/planning/paused/interrupted)不进标题。
 */
function statusText(payload) {
  const run = payload && payload.run;
  if (!run) return null;
  if (run.status === 'running') {
    const steps = Array.isArray(payload.steps) ? payload.steps : [];
    const active = steps.find((s) => s.state === 'running');
    if (active) return `运行中 · 第 ${active.id} 步`;
    // 步骤间隙(上一步刚结束):用「已完成数 + 1」近似当前步
    const done = steps.filter((s) => s.state === 'done' || s.state === 'skipped').length;
    return steps.length ? `运行中 · 第 ${Math.min(done + 1, steps.length)} 步` : '运行中';
  }
  if (run.status === 'done') return '已完成';
  if (run.status === 'failed') return '失败';
  return null;
}

function applyTitle(text, running) {
  if (baseTitle === null) baseTitle = document.title;
  if (!text) {
    if (document.title !== baseTitle) document.title = baseTitle;
    return;
  }
  const dot = isDesktop() ? (running ? '● ' : '○ ') : '';
  document.title = `${dot}${text} · ${baseTitle}`;
}

async function pollOnce(explicitSession) {
  const sid = resolveSession(explicitSession);
  if (!sid) {
    applyTitle(null, false);
    return;
  }
  const ctl = new AbortController();
  const tid = setTimeout(() => ctl.abort(), FETCH_TIMEOUT_MS);
  try {
    const resp = await fetch(`/api/state?session=${encodeURIComponent(sid)}`, {
      signal: ctl.signal,
      cache: 'no-store',
    });
    if (!resp.ok) {
      applyTitle(null, false); // 404/422/5xx:一律视为「暂无状态」
      return;
    }
    const payload = await resp.json();
    applyTitle(statusText(payload), Boolean(payload && payload.run && payload.run.status === 'running'));
  } catch {
    applyTitle(null, false); // 后端不可达 / file:// / 超时 / 解析失败:静默
  } finally {
    clearTimeout(tid);
  }
}

/**
 * 启动标题同步。幂等:重复调用只生效一次。
 * @param {{session?: string, intervalMs?: number}} [opts]
 *   session    固定会话 id(默认自动解析:URL ?session= → S.sessionId)
 *   intervalMs 轮询间隔,默认 10s
 * @returns {() => void} 停止函数(还原原始标题)
 */
export function initTitleSync(opts = {}) {
  if (timer !== null) return stopTitleSync;
  baseTitle = document.title;
  // state.js 是既有模块,app.js 已加载过 → 这里 import 拿到同一实例,零副作用;
  // titlebar.js 单独使用时该 import 失败也无妨(退回 URL 参数解析)。
  import('./state.js').then((m) => { stateModule = m; }).catch(() => {});

  const interval = opts.intervalMs || POLL_MS;
  pollOnce(opts.session);
  timer = setInterval(() => pollOnce(opts.session), interval);
  return stopTitleSync;
}

function stopTitleSync() {
  if (timer !== null) {
    clearInterval(timer);
    timer = null;
  }
  if (baseTitle !== null) document.title = baseTitle;
}
