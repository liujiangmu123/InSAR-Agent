/* ============================================================
   命令面板（Ctrl+Shift+K · cmdk.js）
   零依赖浮层：静态命令注册表 —— 九个面板跳转 / 新建会话 / 切换主题 /
   运行流水线 / 四个示例任务 / 快捷键帮助 / 下载报告。全部命令经既有
   入口触达（dock 标签按钮 click、#btnTheme click、a11y 的 openHelp），
   不修改 app.js / dock.js / stream.js 的任何逻辑。
   - 子串模糊搜索：大小写不敏感，空格分词后各词分别子串匹配
   - ↑↓ 循环导航（跳过禁用项）+ Enter 执行 + Esc 关闭
   - aria-combobox 语义；目标元素不存在的命令禁用显示
   - 打开记忆焦点、关闭归还；Tab 圈定在浮层内
   - Ctrl+K（聚焦输入框）保持原行为，本面板只占用 Ctrl+Shift+K；
     该快捷键在 a11y.js 的 STATIC_SHORTCUTS 统一登记，帮助浮层可见
   纯逻辑（matches / COMMANDS）可被 node 直接 import 自查
   （prototype/cmdk.check.mjs，与 fail-demo.check 同一套 stub 纪律）。
   ============================================================ */

/* ---------------- 小工具（自包含，不 import dom.js） ---------------- */

function el(tag, attrs, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'hidden') n.hidden = !!v;
    else n.setAttribute(k, v === true ? '' : String(v));
  }
  for (const kid of kids) {
    if (kid === null || kid === undefined) continue;
    n.appendChild(typeof kid === 'string' ? document.createTextNode(kid) : kid);
  }
  return n;
}

const byId = (id) => document.getElementById(id);
const q1 = (sel) => document.querySelector(sel);
/** 元素存在但被 app.js 藏起（如 #btnRun 空闲态）→ 同样视为不可用。 */
const visible = (n) => (n && !n.hidden && !n.disabled ? n : null);
const heroChip = (i) => document.querySelectorAll('#stream .hero .chips .chip')[i] || null;

/* ---------------- 命令注册表（静态清单） ---------------- */

const PANELS = [
  ['pipeline', '流水线'], ['images', '影像'], ['files', '文件'],
  ['audit', '审计'], ['report', '报告'], ['web', '浏览器'],
  ['trace', '轨迹'], ['term', '终端'], ['env', '环境'],
];

const EXAMPLES = [
  'Ridgecrest 2019 同震形变时序分析',
  '分析青海玉树冻土 2020–2023 的 SBAS 时序形变',
  '雅鲁藏布江滑坡区做 PS 点监测并与 GNSS 对比',
];

/** 面板跳转：dock 收起时先走 #railDock 既有开关，再点该 tab 自己的按钮。 */
function gotoPane(id) {
  if (byId('dock')?.hidden) byId('railDock')?.click();
  byId(`tab-${id}`)?.click();
}

/**
 * 每条命令：title/group 展示 + alias 供英文子串搜索 + target() 可用性探测
 * （返回目标元素或 null，null → 禁用显示）+ run() 执行（纯 DOM 触达）。
 */
export const COMMANDS = [
  ...PANELS.map(([id, label]) => ({
    id: `panel-${id}`, group: '面板', title: `打开「${label}」面板`, alias: `panel ${id}`,
    target: () => byId(`tab-${id}`),
    run: () => gotoPane(id),
  })),
  {
    id: 'session-new', group: '会话', title: '新建会话', alias: 'new session reset',
    target: () => byId('btnNew'),
    run: () => byId('btnNew').click(),
  },
  {
    id: 'theme-toggle', group: '外观', title: '切换主题（浅色 / 深色）', alias: 'theme dark light',
    target: () => byId('btnTheme'),
    run: () => byId('btnTheme').click(),
  },
  {
    id: 'pipeline-run', group: '运行', title: '运行流水线（执行待跑 / 失效步骤）', alias: 'run pipeline rerun',
    target: () => visible(byId('btnRun')),
    run: () => byId('btnRun').click(),
  },
  ...EXAMPLES.map((text, i) => ({
    id: `example-${i + 1}`, group: '示例任务', title: `示例：${text}`, alias: 'example demo',
    target: () => heroChip(i),
    run: () => heroChip(i).click(),
  })),
  {
    id: 'example-events', group: '示例任务',
    title: '示例：长任务事件演示（reattach / intervention / gate_stop）', alias: 'example demo events',
    target: () => q1('#stream .hero-demo'),
    run: () => q1('#stream .hero-demo').click(),
  },
  {
    id: 'help-keys', group: '帮助', title: '键盘快捷键帮助', alias: 'help keyboard shortcuts',
    target: () => document.body,
    // a11y.js 的既有导出入口；模块加载失败时静默（帮助属增强功能）
    run: () => import('./a11y.js').then((m) => m.openHelp()).catch(() => {}),
  },
  {
    id: 'report-download', group: '报告', title: '下载报告（.md 方法草稿）', alias: 'download report md export',
    // 报告面板渲染后才有下载按钮（实测 .btn-pri：服务端草稿「下载 .md」/ 演示态「.md 草稿」）
    target: () => q1('#pane-report .btn-pri'),
    run: () => q1('#pane-report .btn-pri').click(),
  },
];

/** 子串模糊匹配（纯函数，node 可直接测）：查询按空白分词，逐词命中即通过。 */
export function matches(cmd, query) {
  const hay = `${cmd.title} ${cmd.group} ${cmd.alias || ''}`.toLowerCase();
  return String(query || '').toLowerCase().split(/\s+/).filter(Boolean)
    .every((tok) => hay.includes(tok));
}

/* ---------------- 浮层 UI ---------------- */

let overlay = null;
let panel = null;
let input = null;
let listEl = null;
let emptyEl = null;
let rows = [];          // [{ cmd, el, enabled }] 与当前过滤结果一一对应
let activeIdx = -1;     // rows 下标；-1 = 无可选项
let query = '';
let prevFocus = null;   // 打开前的焦点元素，关闭时归还

function buildUI() {
  if (overlay) return;
  input = el('input', {
    class: 'cmdk-input', type: 'text', role: 'combobox',
    'aria-expanded': 'true', 'aria-autocomplete': 'list',
    'aria-controls': 'cmdk-list', 'aria-label': '搜索命令',
    placeholder: '输入命令，如「面板」「主题」「示例」…', spellcheck: 'false',
  });
  input.addEventListener('input', () => { query = input.value; render(); });

  listEl = el('ul', { class: 'cmdk-list', id: 'cmdk-list', role: 'listbox', 'aria-label': '命令列表' });
  emptyEl = el('div', { class: 'cmdk-empty', role: 'status', hidden: true }, '没有匹配的命令');

  panel = el('div', { class: 'cmdk-panel', role: 'dialog', 'aria-modal': 'true', 'aria-label': '命令面板' },
    el('div', { class: 'cmdk-inrow' }, input),
    listEl,
    emptyEl,
    el('div', { class: 'cmdk-ft' }, '↑↓ 选择 · Enter 执行 · Esc 关闭 · 目标控件不在页面上的命令显示为「不可用」'));

  const mask = el('div', { class: 'cmdk-mask' });
  mask.addEventListener('click', close);

  overlay = el('div', { class: 'cmdk', id: 'cmdk', hidden: true }, mask, panel);
  overlay.addEventListener('keydown', onOverlayKey);
  document.body.appendChild(overlay);
}

function render() {
  const filtered = COMMANDS.filter((c) => matches(c, query));
  rows = [];
  const nodes = [];
  let lastGroup = null;
  for (const cmd of filtered) {
    if (cmd.group !== lastGroup) {
      lastGroup = cmd.group;
      nodes.push(el('li', { class: 'cmdk-group', role: 'presentation' }, cmd.group));
    }
    const enabled = !!cmd.target();
    const li = el('li', {
      class: 'cmdk-item', id: `cmdk-opt-${cmd.id}`, role: 'option', 'aria-selected': 'false',
    },
      el('span', { class: 'ttl' }, cmd.title),
      enabled ? null : el('span', { class: 'why' }, '不可用'));
    if (!enabled) li.setAttribute('aria-disabled', 'true');
    li.addEventListener('click', () => { if (enabled) execute(cmd); });
    rows.push({ cmd, el: li, enabled });
    nodes.push(li);
  }
  listEl.replaceChildren(...nodes);
  emptyEl.hidden = filtered.length > 0;
  activeIdx = rows.findIndex((r) => r.enabled);
  paintActive();
}

function paintActive() {
  rows.forEach((r, i) => {
    r.el.classList.toggle('active', i === activeIdx);
    r.el.setAttribute('aria-selected', String(i === activeIdx));
  });
  const cur = rows[activeIdx];
  if (cur) {
    input.setAttribute('aria-activedescendant', cur.el.getAttribute('id'));
    cur.el.scrollIntoView?.({ block: 'nearest' });
  } else {
    input.removeAttribute('aria-activedescendant');
  }
}

/** 在可用项之间移动，首尾相接成环；禁用项被跳过。 */
function move(dir) {
  const en = rows.map((r, i) => (r.enabled ? i : -1)).filter((i) => i >= 0);
  if (!en.length) return;
  const cur = en.indexOf(activeIdx);
  activeIdx = cur < 0
    ? en[dir > 0 ? 0 : en.length - 1]
    : en[(cur + dir + en.length) % en.length];
  paintActive();
}

function jumpEnd(last) {
  const en = rows.map((r, i) => (r.enabled ? i : -1)).filter((i) => i >= 0);
  if (!en.length) return;
  activeIdx = last ? en[en.length - 1] : en[0];
  paintActive();
}

/** 执行命令：先关面板归还焦点，命令再自行接管（如 openHelp 移焦到帮助浮层）。 */
export function execute(cmd) {
  if (!cmd || !cmd.target()) return false;
  close();
  cmd.run();
  return true;
}

/* ---------------- 焦点圈定 ---------------- */

function focusables() {
  return ['input', 'button', 'select', 'textarea', 'a']
    .flatMap((t) => [...panel.querySelectorAll(t)])
    .filter((n) => !n.disabled && !n.hidden);
}

function trapTab(e) {
  e.preventDefault();   // 焦点永不离开浮层；当前可聚焦元素只有输入框，Tab 原位轮转
  const items = focusables();
  if (!items.length) return;
  const i = items.indexOf(document.activeElement);
  const next = e.shiftKey
    ? (i <= 0 ? items[items.length - 1] : items[i - 1])
    : (i < 0 || i === items.length - 1 ? items[0] : items[i + 1]);
  next.focus();
}

/* ---------------- 键盘 ---------------- */

function onOverlayKey(e) {
  // 浮层是模态：内部按键一律不外溢到底层全局快捷键（Esc=停止、Ctrl+K=聚焦等）
  e.stopPropagation();
  switch (e.key) {
    case 'Escape': e.preventDefault(); close(); break;
    case 'Tab': trapTab(e); break;
    case 'ArrowDown': e.preventDefault(); move(1); break;
    case 'ArrowUp': e.preventDefault(); move(-1); break;
    case 'Home': e.preventDefault(); jumpEnd(false); break;
    case 'End': e.preventDefault(); jumpEnd(true); break;
    case 'Enter': e.preventDefault(); execute(rows[activeIdx]?.cmd); break;
  }
}

/** 全局开关（window 捕获阶段，与 a11y.js 同策略）：Ctrl/⌘+Shift+K。 */
function onGlobalKey(e) {
  if (e.isComposing) return;
  const mod = e.ctrlKey || e.metaKey;
  const isK = e.key === 'k' || e.key === 'K' || e.code === 'KeyK';
  if (mod && e.shiftKey && !e.altKey && isK) {
    e.preventDefault();
    e.stopPropagation();
    toggle();
  }
}

/* ---------------- 开合 ---------------- */

export function isOpen() {
  return !!overlay && !overlay.hidden;
}

export function open() {
  if (document.querySelector('.setup-scrim')) return;   // 强制环境向导期间不抢焦点（与 a11y.js 同约定）
  buildUI();
  if (!overlay.hidden) { input.focus(); return; }
  // 帮助浮层开着时先走它自己的关闭按钮：焦点先归还给原触发元素，再被本面板记忆
  const help = byId('kbdHelp');
  if (help && !help.hidden) help.querySelector('.icon-btn')?.click();
  prevFocus = document.activeElement;
  overlay.hidden = false;
  input.value = '';
  query = '';
  render();
  input.focus();
}

export function close() {
  if (!overlay || overlay.hidden) return;
  overlay.hidden = true;
  const back = prevFocus;
  prevFocus = null;
  if (back && back.isConnected !== false) back.focus?.();
}

export function toggle() {
  if (isOpen()) close();
  else open();
}

/* ---------------- 初始化 ---------------- */

/** 样式随模块自带（index.html 只引本 js 一行），重复注入有 id 防护。 */
function injectCss() {
  if (byId('cmdkCss') || document.head?.querySelector?.('#cmdkCss')) return;
  const link = document.createElement('link');
  link.id = 'cmdkCss';
  link.rel = 'stylesheet';
  link.href = new URL('../css/cmdk.css', import.meta.url).href;
  document.head?.appendChild(link);
}

export function initCmdK() {
  injectCss();
  window.addEventListener('keydown', onGlobalKey, true);
}

// node 环境（cmdk.check.mjs 提前布好 document/window stub 才会初始化）
if (typeof document !== 'undefined' && typeof window !== 'undefined') initCmdK();
