/* ============================================================
   可达性增强层(a11y)
   - 「?」全局快捷键帮助浮层:清单从界面现有提示(composer 提示行 +
     控件的 aria-keyshortcuts 声明)运行时提取,Esc 关闭,Tab 焦点圈定,
     关闭后焦点归还触发元素。
   - aria-modal 浮层的 Tab 焦点圈定:补齐灯箱(#lightbox)缺失的焦点
     陷阱;环境向导(.setup-scrim)自带陷阱,此处显式避让。
   约定:不修改 stream.js / dock.js / app.js 的业务逻辑,全部键盘监听
   走 window 捕获阶段,浮层打开时吞掉事件,不影响既有快捷键。
   纯解析函数(normalizeKeys 等)无 DOM 依赖,供 node 单元测试直接 import。
   ============================================================ */
import { h, icon } from './dom.js';

/* ---------------- 纯函数:快捷键清单解析 ---------------- */

/** aria-keyshortcuts 词汇 → 界面展示词汇。 */
const KEY_LABEL = { Control: 'Ctrl', Escape: 'Esc', ' ': 'Space' };

/** "Control+Enter" → ['Ctrl', 'Enter']。空串返回 []。 */
export function normalizeKeys(spec) {
  return String(spec || '')
    .split('+')
    .map((k) => k.trim())
    .filter(Boolean)
    .map((k) => KEY_LABEL[k] || k);
}

/** 匹配提示文本末尾的快捷键 token,如「发送 Enter」「切换主题 Ctrl+J」。 */
const TRAILING_COMBO = /\s+((?:(?:Ctrl|Control|Shift|Alt|Meta)\+)*(?:Enter|Esc|Escape|Tab|Space|F\d{1,2}|[A-Za-z0-9?]))$/;

/** 去掉 title 尾部的快捷键标注:「发送 Enter」→「发送」。 */
export function stripShortcutSuffix(title) {
  return String(title || '').replace(TRAILING_COMBO, '').trim();
}

/** 解析 composer 提示行:「Enter 发送 · Shift+Enter 换行」→ 条目数组。 */
export function parseHintText(text) {
  const out = [];
  for (const part of String(text || '').split('·')) {
    const m = part.trim().match(/^(\S+)\s+(.+)$/);
    if (!m) continue;
    const keys = normalizeKeys(m[1]);
    // 键位段必须全是快捷键 token(避免把普通句子误判成快捷键)
    if (!keys.length || !keys.every((k) => /^(Ctrl|Shift|Alt|Meta|Esc|Enter|Tab|Space|F\d{1,2}|.)$/.test(k))) continue;
    out.push({ keys, label: m[2].trim() });
  }
  return out;
}

/** 按键位组合去重,保留首个出现的描述。 */
export function dedupeShortcuts(list) {
  const seen = new Set();
  const out = [];
  for (const s of list) {
    const sig = s.keys.join('+').toLowerCase();
    if (!sig || seen.has(sig)) continue;
    seen.add(sig);
    out.push(s);
  }
  return out;
}

/* ---------------- DOM 侧:清单采集 ---------------- */

/** 静态补登清单:界面提示行 / aria-keyshortcuts 覆盖不到的全局快捷键统一在此登记(防散落)。 */
export const STATIC_SHORTCUTS = [{ keys: ['Ctrl', 'Shift', 'K'], label: '打开 / 关闭命令面板' }];

/** 从当前文档提取快捷键清单(静态补登 + 提示行 + aria-keyshortcuts 声明)。 */
export function collectShortcuts(root = document) {
  const list = [...STATIC_SHORTCUTS];
  for (const span of root.querySelectorAll('.cmeta span')) {
    list.push(...parseHintText(span.textContent));
  }
  for (const el of root.querySelectorAll('[aria-keyshortcuts]')) {
    const keys = normalizeKeys(el.getAttribute('aria-keyshortcuts'));
    const label = el.getAttribute('aria-label')
      || stripShortcutSuffix(el.getAttribute('title'))
      || (el.id && root.querySelector(`label[for="${el.id}"]`)?.textContent.trim())
      || el.textContent.trim();
    if (keys.length && label) list.push({ keys, label });
  }
  list.push({ keys: ['?'], label: '打开 / 关闭本帮助' });
  const out = dedupeShortcuts(list);
  const help = out.find((s) => s.keys.length === 1 && s.keys[0] === '?');
  if (help) help.label = '打开 / 关闭本帮助';   // 提示行里的短标注换成完整描述
  return out;
}

/* ---------------- 焦点工具 ---------------- */

function isEditable(t) {
  return !!t && (t.isContentEditable
    || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName || ''));
}

function focusables(container) {
  return [...container.querySelectorAll(
    'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
  )].filter((el) => !el.disabled && !el.hidden && el.getClientRects().length);
}

/** 把 Tab 圈定在容器内(aria-modal 浮层的补充陷阱)。 */
function trapTab(e, container) {
  const items = focusables(container);
  if (!items.length) { e.preventDefault(); return; }
  const first = items[0];
  const last = items[items.length - 1];
  const active = document.activeElement;
  if (!container.contains(active)) {
    e.preventDefault();
    (e.shiftKey ? last : first).focus();
  } else if (e.shiftKey && active === first) {
    e.preventDefault();
    last.focus();
  } else if (!e.shiftKey && active === last) {
    e.preventDefault();
    first.focus();
  }
}

/* ---------------- 帮助浮层 ---------------- */

let helpEl = null;
let helpList = null;
let helpClose = null;
let returnFocus = null;

function buildHelp() {
  if (helpEl) return;
  helpList = h('div', { class: 'list' });
  helpClose = h('button', {
    class: 'icon-btn', type: 'button', 'aria-label': '关闭快捷键帮助(Esc)',
    onclick: closeHelp,
  }, icon('x'));
  helpEl = h('div', {
    class: 'kbd-help', id: 'kbdHelp', role: 'dialog', 'aria-modal': 'true',
    'aria-labelledby': 'kbdHelpTitle', hidden: true,
  },
    // 遮罩仅作点击关闭的便捷通道;键盘路径走 Esc 与关闭按钮
    h('div', { class: 'mask', onclick: closeHelp }),
    h('div', { class: 'panel' },
      h('div', { class: 'hd' },
        h('h2', { id: 'kbdHelpTitle' }, '键盘快捷键'),
        helpClose),
      helpList,
      h('div', { class: 'ft' }, '清单取自界面提示与控件的 aria-keyshortcuts 声明,随界面自动更新。')));
  document.body.appendChild(helpEl);
}

function renderHelpList() {
  const rows = collectShortcuts().map((s) => {
    const keys = h('span', { class: 'keys' });
    s.keys.forEach((k, i) => {
      if (i) keys.appendChild(document.createTextNode('+'));
      keys.appendChild(h('kbd', null, k));
    });
    return h('div', { class: 'row' }, keys, h('span', { class: 'lbl' }, s.label));
  });
  helpList.replaceChildren(...rows);
}

export function openHelp() {
  buildHelp();
  renderHelpList();   // 每次打开重建:主题按钮等的 aria-label 会随状态变化
  returnFocus = document.activeElement;
  helpEl.hidden = false;
  helpClose.focus();
}

export function closeHelp() {
  if (!helpEl || helpEl.hidden) return;
  helpEl.hidden = true;
  if (returnFocus?.isConnected) returnFocus.focus?.();
  returnFocus = null;
}

/* ---------------- 全局键盘调度(捕获阶段) ---------------- */

function swallow(e) {
  e.preventDefault();
  e.stopPropagation();
}

function onKeydown(e) {
  if (e.isComposing) return;

  if (helpEl && !helpEl.hidden) {
    if (e.key === 'Escape' || e.key === '?') { swallow(e); closeHelp(); return; }
    if (e.key === 'Tab') trapTab(e, helpEl);
    return;
  }

  // 灯箱缺失的焦点陷阱(Esc 关闭已由 app.js 统一处理,不重复)
  const lb = document.getElementById('lightbox');
  if (lb && !lb.hidden) {
    if (e.key === 'Tab') trapTab(e, lb);
    return;
  }

  if (e.key === '?' && !e.ctrlKey && !e.metaKey && !e.altKey
      && !isEditable(e.target)
      && !document.querySelector('.setup-scrim')) {   // 强制向导期间不抢焦点
    swallow(e);
    openHelp();
  }
}

export function initA11y() {
  window.addEventListener('keydown', onKeydown, true);
}

// node 环境(单元测试 import 纯函数)没有 document,安全跳过初始化
if (typeof document !== 'undefined') initA11y();
