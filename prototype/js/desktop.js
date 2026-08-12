/* ============================================================
   桌面壳(Tauri 2)前端桥 —— 零依赖 ES Module

   浏览器环境下所有函数安全降级(返回 null / false),绝不抛错,
   调用方无需区分运行环境、无需 try/catch。
   Rust 侧实现:desktop/src/commands.rs
   壳集成前提:desktop/INTEGRATION-commands.md(withGlobalTauri + capability)
   前端接线点:docs/INTEGRATION-desktop-bridge.md
   ============================================================ */

/**
 * 是否运行在 Tauri 桌面壳内。
 * 依赖壳开启 app.withGlobalTauri(注入 window.__TAURI__);
 * 未集成或纯浏览器环境返回 false。
 * @returns {boolean}
 */
export function isDesktop() {
  return typeof window !== 'undefined' && window.__TAURI__ != null;
}

/**
 * 取 Tauri invoke 函数;非桌面环境返回 null。
 * 包一层箭头函数,避免解构 invoke 丢失 this 的隐患。
 */
function getInvoke() {
  if (!isDesktop()) return null;
  const tauri = window.__TAURI__;
  if (tauri.core && typeof tauri.core.invoke === 'function') {
    return (cmd, args) => tauri.core.invoke(cmd, args); // Tauri 2 标准入口
  }
  if (typeof tauri.invoke === 'function') {
    return (cmd, args) => tauri.invoke(cmd, args); // 兼容自定义注入
  }
  return null;
}

/** 统一调用封装:任何异常都吞掉并 console.warn,返回 fallback。 */
async function safeInvoke(command, args, fallback) {
  const invoke = getInvoke();
  if (!invoke) return fallback;
  try {
    return await invoke(command, args);
  } catch (err) {
    console.warn(`[desktop] invoke('${command}') 失败,已降级:`, err);
    return fallback;
  }
}

/**
 * 弹出原生「选择目录」对话框。
 * @param {string} [title] 对话框标题
 * @returns {Promise<string|null>} 选中目录的绝对路径;
 *   用户取消、浏览器环境或调用失败均返回 null(调用方回退为手输框)
 */
export async function pickDirectory(title = '选择目录') {
  const result = await safeInvoke('pick_directory', { title }, null);
  return typeof result === 'string' && result.length > 0 ? result : null;
}

/**
 * 在系统文件管理器(资源管理器)中打开目录;传文件路径则打开其所在目录。
 * @param {string} path 要打开的路径
 * @returns {Promise<boolean>} 桌面壳内成功发起返回 true;
 *   浏览器环境、空路径或调用失败返回 false
 */
export async function openPath(path) {
  if (typeof path !== 'string' || path.trim() === '') return false;
  const invoke = getInvoke();
  if (!invoke) return false;
  try {
    await invoke('open_path', { path });
    return true;
  } catch (err) {
    console.warn(`[desktop] invoke('open_path') 失败,已降级:`, err);
    return false;
  }
}

/**
 * 读取桌面壳信息。
 * @returns {Promise<{name: string, version: string, tauriVersion: string,
 *   platform: string, arch: string, debug: boolean,
 *   repoRoot: string|null}|null>} 浏览器环境或调用失败返回 null
 */
export async function appInfo() {
  const info = await safeInvoke('app_info', undefined, null);
  return info && typeof info === 'object' ? info : null;
}

/* ============================================================
   通知的桌面分支（notify.js 的桌面层）
   —— Tauri notification 插件经 withGlobalTauri 注入 window.__TAURI__。
   三个函数都不抛错：探测失败返回 null，调用异常返回降级信号，
   由 notify.js 决定是否回退 Web Notification。
   ============================================================ */

/**
 * 探测 __TAURI__.notification 插件桥是否可用。
 * 要求完整契约（sendNotification / isPermissionGranted / requestPermission
 * 三函数齐全）才算可用 —— 半残的桥直接回退 Web 层，比在发送时才炸掉稳。
 * @param {object|null} [tauri] 可注入（单测）；缺省取 window.__TAURI__
 * @returns {object|null} 可用返回插件对象，否则 null
 */
export function notificationBridge(tauri = (typeof window !== 'undefined' ? window.__TAURI__ : null)) {
  const n = tauri?.notification;
  if (!n) return null;
  const fns = ['sendNotification', 'isPermissionGranted', 'requestPermission'];
  return fns.every((k) => typeof n[k] === 'function') ? n : null;
}

/**
 * 桌面通知权限。ask=false 只查询不弹框（发送时用）；ask=true 允许弹框
 * （仅首次执行流水线的惰性申请用，见 notify.js ensurePermission）。
 * @param {object|null} bridge notificationBridge 的返回值
 * @returns {Promise<'granted'|'denied'|'default'|'unavailable'>}
 *   default=尚未申请或用户关掉了弹框；unavailable=桥调用异常（回退 Web 层）
 */
export async function desktopNotifyPermission(bridge, ask = false) {
  if (!bridge) return 'unavailable';
  try {
    if (await bridge.isPermissionGranted()) return 'granted';
    if (!ask) return 'default';
    const r = await bridge.requestPermission();
    return r === 'granted' ? 'granted' : r === 'denied' ? 'denied' : 'default';
  } catch (err) {
    console.warn('[desktop] notification 权限查询/申请失败,已降级:', err);
    return 'unavailable';
  }
}

/**
 * 经桌面壳发送系统通知（前提：权限已 granted）。
 * Windows 系统 toast 的点击激活由壳负责（应用 AUMID 绑定），无需 JS 接线。
 * @returns {Promise<boolean>} false=发送失败（调用方回退 Web 层）
 */
export async function desktopSendNotification(bridge, title, body) {
  if (!bridge) return false;
  try {
    await bridge.sendNotification({ title, body });
    return true;
  } catch (err) {
    console.warn('[desktop] notification 发送失败,已降级:', err);
    return false;
  }
}
