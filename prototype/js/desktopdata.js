/* ============================================================
   桌面壳「打开本地数据」前端桥(自初始化,Web 环境零影响)

   桌面版专属价值点:浏览器沙箱拿不到绝对路径,Tauri 壳可以。
   本模块只在 Tauri 环境(window.__TAURI__ 注入)激活,做两件事:

   1. 「浏览…」按钮:注入到数据集区(datasets.js)头部「添加目录」旁,
      点击走原生目录对话框(desktop.js pickDirectory → 壳 command
      pick_directory)拿绝对路径,再走 datasets.js 的 addRoot 注册
      (与手输框同一后端通路 POST /api/datasets/roots),成功后广播
      datasets:refresh 刷新数据集区并 toast 回执。
   2. 壳侧回执承接:托盘「打开数据文件夹…」与拖拽目录(desktop/src/
      opendata.rs)注册后 eval 广播 datasets:root-registered,本模块
      监听并 toast(成功/失败文案统一在此,页面内单一出口)。

   数据集区头部随 render() 整体重建(datasets.js box.replaceChildren),
   注入用 MutationObserver 盯 document.body 子树 + id 查重回插,模式
   同 datasets.js 的 ensureSection(回调命中查重即止,不自激)。

   浏览器环境:isDesktop() 为 false,init 首行短路 —— 不注入按钮、
   不挂观察器、不挂事件监听(回执事件只有壳会发),对 Web 端零影响。
   node 测试环境(desktopdata.check.mjs)无 document:只导出纯函数。
   ============================================================ */
import { isDesktop, pickDirectory } from './desktop.js';
import { addRoot } from './datasets.js';
import { toast } from './dom.js';

/* ---------------- 纯函数(desktopdata.check.mjs 直接断言) ---------------- */

/** 数据集区刷新事件名(datasets.js 监听;与 desktop/src/opendata.rs 契约)。 */
export const REFRESH_EVENT = 'datasets:refresh';
/** 壳侧注册回执事件名(opendata.rs eval 广播;detail = {path, ok, error})。 */
export const REGISTERED_EVENT = 'datasets:root-registered';
/** 注入按钮的 id(查重锚点,数据集区每次重建后凭它判断是否需要回插)。 */
export const BROWSE_BTN_ID = 'dsBrowseBtn';

/** 回执 detail → toast 文案(壳侧与本模块自身的注册结果共用)。 */
export function receiptMessage(detail) {
  const d = detail || {};
  if (d.ok) return `已添加数据目录:${d.path || ''}`;
  return `添加数据目录失败:${d.error || '未知原因'}`;
}

/**
 * 「浏览…」核心流程(依赖注入便于单测,DOM/环境无关):
 * 原生对话框选目录 → 取消则安静返回 → addRoot 注册 → 回执 + 成功时广播刷新。
 * @param {{pick: () => Promise<string|null>,
 *          add: (path: string) => Promise<{ok: boolean, error?: string}>,
 *          notify: (msg: string) => void,
 *          dispatch: (eventName: string) => void}} io
 * @returns {Promise<'picked'|'cancelled'|'failed'>}
 */
export async function browseAndRegister(io) {
  const path = await io.pick();
  if (!path) return 'cancelled';           // 用户取消:不打扰,无 toast
  const res = await io.add(path);
  io.notify(receiptMessage({ path, ok: res.ok, error: res.error }));
  if (!res.ok) return 'failed';
  io.dispatch(REFRESH_EVENT);              // datasets.js 监听后重拉清单
  return 'picked';
}

/* ---------------- 注入与事件承接(仅浏览器 + Tauri 环境) ---------------- */

/** 真实 IO 装配:原生对话框 + datasets.addRoot + toast + window 事件。 */
function realIO() {
  return {
    pick: () => pickDirectory('选择数据文件夹(注册到数据集)'),
    add: (path) => addRoot(path),
    notify: (msg) => toast(msg),
    dispatch: (name) => window.dispatchEvent(new CustomEvent(name)),
  };
}

/** 造「浏览…」按钮(样式对齐数据集区头部既有 btn-gho 按钮)。 */
function browseButton() {
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.id = BROWSE_BTN_ID;
  btn.className = 'btn btn-gho btn-sm';
  btn.setAttribute('aria-label', '用系统对话框选择要添加的数据目录');
  btn.textContent = '浏览…';
  btn.addEventListener('click', async () => {
    if (btn.disabled) return;
    btn.disabled = true;                   // 对话框未回来前防连点
    try { await browseAndRegister(realIO()); }
    catch (err) {
      toast(`选目录失败:${String(err.message || err).slice(0, 160)}`, 3600);
    } finally { btn.disabled = false; }
  });
  return btn;
}

/** 幂等注入:数据集区头部在位且尚无本按钮时,插到「添加目录」后面。 */
function injectIfNeeded() {
  if (document.getElementById(BROWSE_BTN_ID)) return;
  const head = document.querySelector('#dsSection .ds-hd');
  if (!head) return;
  const addBtn = head.querySelector('button[aria-label="添加数据目录"]');
  if (!addBtn) return;
  addBtn.after(browseButton());
}

function init() {
  if (!isDesktop()) return;                // Web 环境:零注入零观察,直接退场
  // 壳侧(托盘/拖拽)注册回执 → toast;刷新事件由壳直接广播,datasets.js 承接
  window.addEventListener(REGISTERED_EVENT, (e) => toast(receiptMessage(e.detail)));
  injectIfNeeded();
  // 数据集区头部随 render()/tab 切换整体重建:盯全局子树,查重后回插
  new MutationObserver(injectIfNeeded)
    .observe(document.body, { childList: true, subtree: true });
}

// node 测试环境无 document:只导出纯函数,不自初始化(同 datasets.js 约定)
if (typeof document !== 'undefined' && typeof window !== 'undefined') {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init, { once: true });
  } else {
    init();
  }
}
