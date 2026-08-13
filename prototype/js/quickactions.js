/* ============================================================
   一键操作中心（⚡ 快捷面板 · quickactions.js）

   目标：把散落各面板的高频操作收拢成一个鼠标随手可达的浮出面板 ——
   与 cmdk.js（键盘流 Ctrl+Shift+K）互补，动作纪律与其完全一致：

   实现纪律（与 cmdk 同款，零业务重写）：
   - 每个动作 = 触发既有 DOM 入口（button.click() / a.click()），或经
     既有公开端点间接触达；本模块不发任何新请求、不复制任何业务逻辑；
   - 不 import 任何业务模块（app.js/dock.js/state.js…零耦合），全部经
     document 查询触达 —— 目标控件不在场即禁用并给原因 tooltip；
   - 面板内四个 pane 动作（体检/诊断包/草稿/重扫）先走 gotoPane 让用户
     看到执行现场，再等一个微任务点目标按钮 —— dock.js 切 tab 会
     replaceChildren 重建 pane，各模块的 MutationObserver（微任务时序）
     会先把区块挂回/重建，此时再点击才作用在「眼前这个」控件上。

   对 cmdk 的开放接口：动作注册表挂在 window.QuickActions.registry
   （每项 {id,label,desc,ic,enabled,run}；enabled() === true 表示可用，
   返回字符串 = 禁用原因）。cmdk 后续可直接 map 本注册表并入其命令清单
   （label → title、enabled() !== true → 禁用显示），无需改本模块。

   键盘可达：面板内网格方向键导航（←→ 行内推进换行环绕、↑↓ 跨行保列、
   Home/End）+ Enter 执行 + Esc 关闭归还焦点 + Tab 圈定。
   最近使用：localStorage('ia-qa-recent') 记最近 4 个动作 id，面板顶部
   「最近」行快速重复。纯逻辑（注册表/pushRecent/gridMove）可被 node
   直接 import 自查（prototype/quickactions.check.mjs，与 cmdk.check
   同一套 stub 纪律）。
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

const SVG_NS = 'http://www.w3.org/2000/svg';

/* 与 rail/顶栏一致的细线图标（stroke=currentColor，随主题变色） */
const ICON_PATHS = {
  bolt: 'M13 2L3 14h9l-1 8 10-12h-9l1-8z',
  plus: 'M12 5v14M5 12h14',
  play: 'M6 4l14 8-14 8z',
  shield: 'M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z',
  box: 'M21 8l-9-5-9 5v8l9 5 9-5zM3 8l9 5 9-5M12 13v9',
  doc: 'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6',
  download: 'M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3',
  refresh: 'M23 4v6h-6M1 20v-6h6M3.5 9a9 9 0 0 1 14.9-3.4L23 10M1 14l4.6 4.4A9 9 0 0 0 20.5 15',
  gear: 'M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M19.1 4.9L17 7M7 17l-2.1 2.1',
  help: 'M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zM9.1 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3M12 17h.01',
  moon: 'M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8z',
};

function svgIcon(name, size = 15) {
  const s = document.createElementNS(SVG_NS, 'svg');
  s.setAttribute('viewBox', '0 0 24 24');
  s.setAttribute('fill', 'none');
  s.setAttribute('stroke', 'currentColor');
  s.setAttribute('stroke-width', '2');
  s.setAttribute('stroke-linecap', 'round');
  s.setAttribute('stroke-linejoin', 'round');
  s.setAttribute('aria-hidden', 'true');
  s.setAttribute('width', String(size));
  s.setAttribute('height', String(size));
  const p = document.createElementNS(SVG_NS, 'path');
  p.setAttribute('d', ICON_PATHS[name] || ICON_PATHS.bolt);
  s.appendChild(p);
  return s;
}

const byId = (id) => document.getElementById(id);
const q1 = (sel) => document.querySelector(sel);
/** 元素在场但被 app.js 藏起/禁用（如 #btnRun 空闲态）→ 视为不可用（与 cmdk 同判据）。 */
const visible = (n) => (n && !n.hidden && !n.disabled ? n : null);

/** 面板跳转：dock 收起时先走 #railDock 既有开关，再点该 tab 自己的按钮（cmdk 同款）。 */
function gotoPane(id) {
  if (byId('dock')?.hidden) byId('railDock')?.click();
  byId(`tab-${id}`)?.click();
}

/**
 * pane 内动作：先跳面板让用户看到执行现场，等一个微任务再点目标按钮。
 * 缘由：dock.js 切 tab 会 replaceChildren 重建 pane，doctorpanel/diagexport/
 * reportdraft/datasets 各自的 MutationObserver（微任务时序）随后才把区块
 * 挂回或重建；queueMicrotask 排在它们之后，点到的必然是重挂后的现役控件。
 */
const paneAction = (pane, sel) => () => {
  gotoPane(pane);
  queueMicrotask(() => q1(sel)?.click());
};

/* ---------------- 动作注册表（第一期 10 个，全部复用既有实现） ---------------- */

/*
 * 每个动作：
 *   id      唯一标识（最近使用按它存取）
 *   label   卡片名称       desc 一句话说明（卡片副行）
 *   ic      图标键（ICON_PATHS）
 *   enabled() === true 可用；返回字符串 = 禁用原因（tooltip 原样展示）
 *   run()   执行 —— 只触发既有 DOM 入口，绝不重写业务
 */
export const ACTIONS = [
  {
    id: 'session-new', label: '新建会话', ic: 'plus',
    desc: '开一个全新的研究会话（等效侧栏「+」）',
    enabled: () => (byId('btnNew') ? true : '会话侧栏未就绪'),
    run: () => byId('btnNew').click(),
  },
  {
    id: 'pipeline-run', label: '运行流水线', ic: 'play',
    desc: '执行待跑 / 失效步骤（等效顶栏运行按钮）',
    enabled: () => (visible(byId('btnRun'))
      ? true : '当前没有待运行或失效的步骤：先发起任务或修改参数'),
    run: () => byId('btnRun').click(),
  },
  {
    id: 'doctor-run', label: '一键体检', ic: 'shield',
    desc: '秒级只读巡检数据库 / 磁盘 / 端口 / 依赖（环境面板）',
    enabled: () => {
      const b = q1('#pane-env .doctor-run');
      if (!b) return '环境面板体检区未就绪，稍后再试';
      return b.disabled ? '体检正在进行中，请稍候' : true;
    },
    run: paneAction('env', '#pane-env .doctor-run'),
  },
  {
    id: 'diag-export', label: '导出诊断包', ic: 'box',
    desc: '打包日志 / 环境 / 数据库摘要，用于问题反馈',
    enabled: () => {
      const b = q1('#diagExport button');
      if (!b) return '需要后端在运行（file:// 演示模式不可用）';
      return b.disabled ? '正在打包，请稍候' : true;
    },
    run: paneAction('env', '#diagExport button'),
  },
  {
    id: 'report-draft', label: '生成方法草稿', ic: 'doc',
    desc: '把 provenance 账本写成论文方法段（报告面板）',
    // 「本会话还没有 run」由后端 404 判定，报告面板区块内就地给出人话提示；
    // 这里只探测入口按钮本身（生成中 = 禁用）
    enabled: () => {
      const b = q1('#pane-report .reportdraft .btn-pri');
      if (!b) return '报告面板草稿区未就绪，稍后再试';
      return b.disabled ? '正在生成草稿，请稍候' : true;
    },
    run: paneAction('report', '#pane-report .reportdraft .btn-pri'),
  },
  {
    id: 'repro-bundle', label: '导出复现包', ic: 'download',
    desc: '下载 provenance / run.sh / 图件的 .zip 包',
    // 直链锚点由报告面板服务端草稿渲染出（需已完成一次 run）；
    // 锚点带 download 属性，点击原地下载不跳页
    enabled: () => (q1('#pane-report a[href*="/api/repro-bundle"]')
      ? true : '先完成一次 run，并打开报告面板加载服务端草稿'),
    run: () => q1('#pane-report a[href*="/api/repro-bundle"]').click(),
  },
  {
    id: 'datasets-rescan', label: '重扫数据集', ic: 'refresh',
    desc: '穿透缓存重扫全部数据根目录（文件面板）',
    enabled: () => (q1('#dsSection button[aria-label^="重新扫描"]')
      ? true : '文件面板数据集区未就绪，稍后再试'),
    run: paneAction('files', '#dsSection button[aria-label^="重新扫描"]'),
  },
  {
    id: 'llm-settings', label: '打开模型设置', ic: 'gear',
    desc: '密钥 / 模型选择 / 连通性测试',
    enabled: () => (byId('btnLlmSettings') ? true : '模型设置入口未就绪'),
    run: () => byId('btnLlmSettings').click(),
  },
  {
    id: 'tour-open', label: '查看界面引导', ic: 'help',
    desc: '重看各区块功能的分步引导',
    enabled: () => (byId('btnTour') ? true : '界面引导入口未就绪'),
    run: () => byId('btnTour').click(),
  },
  {
    id: 'theme-toggle', label: '切换主题', ic: 'moon',
    desc: '浅色 / 深色一键切换（Ctrl+J）',
    enabled: () => (byId('btnTheme') ? true : '主题切换入口未就绪'),
    run: () => byId('btnTheme').click(),
  },
];

/* ---------------- 最近使用（localStorage，FIFO 容量 4） ---------------- */

export const RECENT_KEY = 'ia-qa-recent';   // 键名沿用仓库 ia- 前缀惯例
export const RECENT_MAX = 4;

/** 纯函数：把 id 置顶（去重），FIFO 截断到 max —— 最近在前。 */
export function pushRecent(list, id, max = RECENT_MAX) {
  return [id, ...(Array.isArray(list) ? list : []).filter((x) => x !== id)]
    .slice(0, max);
}

/** 读最近清单：坏数据（非 JSON / 非字符串数组）一律回落空数组。 */
export function readRecent() {
  try {
    const v = JSON.parse(localStorage.getItem(RECENT_KEY) || '[]');
    return Array.isArray(v) ? v.filter((x) => typeof x === 'string') : [];
  } catch {
    return [];
  }
}

function recordUse(id) {
  try { localStorage.setItem(RECENT_KEY, JSON.stringify(pushRecent(readRecent(), id))); }
  catch { /* 隐私模式等写失败：最近行退化为空，不影响动作执行 */ }
}

/* ---------------- 网格方向键导航（纯函数） ---------------- */

/**
 * rows = 各行长度数组（如 [2, 2, 2, 2, 2] 或带「最近」行的 [3, 2, …]），
 * pos = {row, col}。语义：←→ 行内推进，越界流入邻行并首尾环绕；
 * ↑↓ 跨行保列（列号超行长时钳到行尾）并环绕；Home/End 跳全局首尾。
 */
export function gridMove(rows, pos, key) {
  const R = rows.length;
  if (!R) return pos;
  let { row, col } = pos;
  switch (key) {
    case 'ArrowRight':
      col += 1;
      if (col >= rows[row]) { row = (row + 1) % R; col = 0; }
      break;
    case 'ArrowLeft':
      col -= 1;
      if (col < 0) { row = (row - 1 + R) % R; col = rows[row] - 1; }
      break;
    case 'ArrowDown':
      row = (row + 1) % R;
      col = Math.min(col, rows[row] - 1);
      break;
    case 'ArrowUp':
      row = (row - 1 + R) % R;
      col = Math.min(col, rows[row] - 1);
      break;
    case 'Home': row = 0; col = 0; break;
    case 'End': row = R - 1; col = rows[R - 1] - 1; break;
    default: break;
  }
  return { row, col };
}

/* ---------------- 浮层 UI ---------------- */

let overlay = null;
let panel = null;
let bodyEl = null;
let prevFocus = null;   // 打开前焦点，关闭时归还（通常是 rail 的 ⚡ 按钮）

function buildUI() {
  if (overlay) return;
  bodyEl = el('div', { class: 'qa-bd' });
  panel = el('div', { class: 'qa-panel', role: 'dialog', 'aria-modal': 'true', 'aria-label': '快捷操作面板' },
    el('div', { class: 'qa-hd' },
      el('span', { class: 'qa-title' }, '快捷操作'),
      el('span', { class: 'qa-hint' }, '方向键选择 · Enter 执行 · Esc 关闭')),
    bodyEl,
    el('div', { class: 'qa-ft' }, '全部动作复用现有入口；不可用的卡片悬停可见原因。'));

  const mask = el('button', { class: 'qa-mask', type: 'button', 'aria-label': '关闭快捷操作面板' });
  mask.addEventListener('click', close);

  overlay = el('div', { class: 'qa', id: 'quickActions', hidden: true }, mask, panel);
  overlay.addEventListener('keydown', onPanelKey);
  document.body.appendChild(overlay);
}

/** 一张动作卡（网格区）/ 一枚最近 chip（顶部行）。禁用不用 disabled 属性
    而用 aria-disabled：保持可聚焦悬停，原因 tooltip 对键盘/鼠标都可达。 */
function actionButton(action, kind, row, col) {
  const state = action.enabled();
  const ok = state === true;
  const btn = el('button', {
    class: kind, type: 'button',
    'data-id': action.id, 'data-row': String(row), 'data-col': String(col),
    tabindex: '-1',
    'aria-disabled': ok ? null : 'true',
    title: ok ? action.desc : `不可用：${state}`,
  });
  if (kind === 'qa-card') {
    btn.append(
      el('span', { class: 'ic', 'aria-hidden': 'true' }, svgIcon(action.ic)),
      el('span', { class: 'tx' },
        el('span', { class: 'nm' }, action.label,
          ok ? null : el('span', { class: 'why' }, '不可用')),
        el('span', { class: 'ds' }, ok ? action.desc : String(state))));
  } else {
    btn.append(svgIcon(action.ic, 12), el('span', null, action.label));
  }
  btn.addEventListener('click', () => execute(action));
  return btn;
}

/** 重建面板内容：每次打开重算可用性与最近行（与 cmdk 打开时 render 同策略）。 */
function render() {
  const byIdMap = new Map(ACTIONS.map((a) => [a.id, a]));
  const recent = readRecent().map((id) => byIdMap.get(id)).filter(Boolean);
  const kids = [];
  let row = 0;

  if (recent.length) {
    kids.push(el('div', { class: 'qa-sec' }, '最近'));
    const chips = el('div', { class: 'qa-recent-row', role: 'group', 'aria-label': '最近使用的动作' });
    recent.forEach((a, i) => chips.appendChild(actionButton(a, 'qa-chip', row, i)));
    kids.push(chips);
    row += 1;
  }

  kids.push(el('div', { class: 'qa-sec' }, '全部动作'));
  const grid = el('div', { class: 'qa-grid', role: 'group', 'aria-label': '全部快捷动作' });
  ACTIONS.forEach((a, i) => {
    grid.appendChild(actionButton(a, 'qa-card', row + Math.floor(i / 2), i % 2));
  });
  kids.push(grid);

  bodyEl.replaceChildren(...kids);
  // roving tabindex：首个可用项作为 Tab 进入点
  const items = allItems();
  const first = items.find((n) => n.getAttribute('aria-disabled') !== 'true') || items[0];
  if (first) first.setAttribute('tabindex', '0');
  return first || null;
}

const allItems = () => [...panel.querySelectorAll('.qa-chip, .qa-card')];

function focusItem(items, pos) {
  const target = items.find((n) =>
    n.getAttribute('data-row') === String(pos.row)
    && n.getAttribute('data-col') === String(pos.col));
  if (!target) return;
  items.forEach((n) => n.setAttribute('tabindex', n === target ? '0' : '-1'));
  target.focus();
}

/** 各行长度（data-row → 该行元素数），供 gridMove 用。 */
function rowLens(items) {
  const lens = [];
  for (const n of items) {
    const r = Number(n.getAttribute('data-row'));
    lens[r] = Math.max(lens[r] || 0, Number(n.getAttribute('data-col')) + 1);
  }
  return lens;
}

/* ---------------- 键盘 ---------------- */

const NAV_KEYS = ['ArrowRight', 'ArrowLeft', 'ArrowDown', 'ArrowUp', 'Home', 'End'];

function trapTab(e) {
  e.preventDefault();   // 焦点圈定在浮层内：Tab 在动作项之间轮转（cmdk 同策略）
  const items = allItems();
  if (!items.length) return;
  const i = items.indexOf(document.activeElement);
  const next = e.shiftKey
    ? (i <= 0 ? items[items.length - 1] : items[i - 1])
    : (i < 0 || i === items.length - 1 ? items[0] : items[i + 1]);
  items.forEach((n) => n.setAttribute('tabindex', n === next ? '0' : '-1'));
  next.focus();
}

function onPanelKey(e) {
  // 浮层是模态：按键不外溢到底层全局快捷键（Esc=停止 等）
  e.stopPropagation();
  if (e.key === 'Escape') { e.preventDefault(); close(); return; }
  if (e.key === 'Tab') { trapTab(e); return; }
  if (!NAV_KEYS.includes(e.key)) return;
  e.preventDefault();
  const items = allItems();
  if (!items.length) return;
  const cur = items.indexOf(document.activeElement);
  if (cur < 0) { focusItem(items, { row: 0, col: 0 }); return; }
  const pos = {
    row: Number(items[cur].getAttribute('data-row')),
    col: Number(items[cur].getAttribute('data-col')),
  };
  focusItem(items, gridMove(rowLens(items), pos, e.key));
}

/* ---------------- 执行 ---------------- */

/** 执行动作：不可用即拒绝（面板保持打开，原因在 tooltip）；可用则记入
    最近 → 关面板归还焦点 → 触发既有入口（动作自行接管后续 UI）。 */
export function execute(action) {
  if (!action || action.enabled() !== true) return false;
  recordUse(action.id);
  close();
  action.run();
  return true;
}

/* ---------------- 开合 ---------------- */

export function isOpen() {
  return !!overlay && !overlay.hidden;
}

export function open() {
  buildUI();
  prevFocus = document.activeElement;
  overlay.hidden = false;
  byId('btnQuickActions')?.setAttribute('aria-expanded', 'true');
  const first = render();
  if (first) first.focus();
}

export function close() {
  if (!overlay || overlay.hidden) return;
  overlay.hidden = true;
  byId('btnQuickActions')?.setAttribute('aria-expanded', 'false');
  const back = prevFocus;
  prevFocus = null;
  if (back && back.isConnected !== false) back.focus?.();
}

export function toggle() {
  if (isOpen()) close();
  else open();
}

/* ---------------- 初始化 ---------------- */

/** 样式随模块自带（index.html 只引本 js 一行），重复注入有 id 防护
    （head 查询兜底：与 cmdk.injectCss 同款，兼容极简 DOM stub）。 */
function injectCss() {
  if (document.getElementById('quickactionsCss')
      || document.head?.querySelector?.('#quickactionsCss')) return;
  const link = document.createElement('link');
  link.setAttribute('id', 'quickactionsCss');
  link.setAttribute('rel', 'stylesheet');
  link.setAttribute('href', new URL('../css/quickactions.css', import.meta.url).href);
  document.head?.appendChild(link);
}

/** rail 入口注入（#railDock 之前）：幂等，已在场即直返 false 表示无需重试。 */
export function installEntry() {
  if (byId('btnQuickActions')) return true;      // 已注入：幂等直返
  const railDock = byId('railDock');
  if (!railDock || !railDock.parentNode) return false;
  const b = el('button', {
    class: 'rail-btn', id: 'btnQuickActions', type: 'button',
    'aria-label': '快捷操作', title: '快捷操作（一键面板）',
    'aria-haspopup': 'dialog', 'aria-expanded': 'false',
  }, svgIcon('bolt', 18));
  b.addEventListener('click', toggle);
  railDock.parentNode.insertBefore(b, railDock);
  return true;
}

/** 自初始化（幂等，可重复调用）。#railDock 尚不在场（加载次序漂移 /
    非工作区页面）时观察 body 等它出现，等不到则零打扰。 */
export function initQuickActions() {
  injectCss();
  if (installEntry()) return;
  const mo = new MutationObserver(() => { if (installEntry()) mo.disconnect(); });
  mo.observe(document.body, { childList: true, subtree: true });
}

// 对 cmdk 等消费方的公开出口：registry 即动作清单（见文件头注释）
if (typeof window !== 'undefined') {
  window.QuickActions = { registry: ACTIONS, open, close, toggle, isOpen, execute };
}

// node 环境（quickactions.check.mjs 提前布好 document/window stub 才会初始化）
if (typeof document !== 'undefined' && typeof window !== 'undefined') initQuickActions();
