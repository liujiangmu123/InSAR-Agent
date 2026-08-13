/* ============================================================
   处理路线推荐(数据集卡片「处理建议」按钮 → 路线对比抽屉)

   职责:
   - 给 datasets.js 渲染的数据集卡片(.ds-card)注入「处理建议」入口:
     所有权边界 —— 不改 datasets.js,用 MutationObserver 盯 #pane-files
     子树,卡片(重)渲染后补插;点击/键盘激活走 document 捕获阶段委托
     (stopPropagation 拦在卡片自身 onclick 之前,不误开文件抽屉);
   - 点「处理建议」→ GET /api/recommend?dataset_id=... → 右侧抽屉展示
     路线对比卡:名称 + 适配徽章(环境就绪绿 / 缺失黄)+ pros/cons 双列
     + 需求核对 + 涉及步骤 + 规模提示;
   - 每条路线一个「按此路线开始对话」按钮:把预填话术写进聊天输入框
     #prompt(dispatch input 让联动组件感知),聚焦但绝不代发 —— 决策权
     与发送权都留给用户(衔接对话大脑的入口)。

   卡片 → 数据集 id 的映射:datasets.js 的卡片不携带 id,但 title 属性
   就是数据集 path;点击时拉 /api/datasets(后端 60s TTL 缓存,秒回)
   按 path 反查条目拿 id —— 与清单天然同源,不复制状态。

   失败语义(对齐 datasets.js):file:// 或后端不可达 → resolve null,
   抽屉显示「后端未连接」,绝不抛错。
   ============================================================ */
import { fetchDatasets } from './datasets.js';
import { h, icon } from './dom.js';

/* ---------------- 纯函数(prototype/recommend.check.mjs 直接断言) ---------------- */

/** 路线 → 适配徽章:必需项全满足 → 绿「环境就绪」;有缺失 → 黄「缺 xxx」。 */
export function badgeState(route) {
  if (route && route.ready) return { cls: 'rec-b-ok', label: '环境就绪' };
  const missing = (route && route.missing) || [];
  return {
    cls: 'rec-b-miss',
    label: missing.length ? `缺 ${missing.join('、')}` : '环境待核对',
  };
}

/** 路线 + 数据集 → 聊天预填话术(只预填不发送,衔接对话大脑)。 */
export function prefillText(route, ds) {
  const path = (ds && ds.path) || '';
  const id = route && route.route_id;
  if (id === 'identify_first') return `帮我看看 ${path} 里是什么数据,该怎么处理`;
  if (id === 'dem_only') {
    return `我有一份本地 DEM(${path}),想在第 2 步作为地形数据配合主数据使用`;
  }
  return `用${(route && route.name) || '推荐路线'}分析,数据在 ${path}`;
}

/** 步骤号列表 → 紧凑展示(连续段折叠:[1,7,8,9] → "1、7-9";空 → "—")。 */
export function stepsLabel(steps) {
  const ids = (steps || []).slice().sort((a, b) => a - b);
  if (!ids.length) return '—';
  const parts = [];
  let start = ids[0], prev = ids[0];
  for (const n of ids.slice(1)) {
    if (n === prev + 1) { prev = n; continue; }
    parts.push(start === prev ? `${start}` : `${start}-${prev}`);
    start = prev = n;
  }
  parts.push(start === prev ? `${start}` : `${start}-${prev}`);
  return parts.join('、');
}

/** 数据集清单里按 path 反查条目(卡片 title 属性 → 数据集 id 的桥)。 */
export function matchByPath(list, path) {
  return (list || []).find((d) => d && d.path === path) || null;
}

/** 拉取推荐(失败一律 resolve null,不抛错;口径同 datasets.js 取数)。 */
export function fetchRecommend(datasetId) {
  if (location.protocol === 'file:') return Promise.resolve(null);
  return fetch(`/api/recommend?dataset_id=${encodeURIComponent(datasetId)}`)
    .then((r) => (r.ok ? r.json() : null))
    .catch(() => null);
}

/* ---------------- 路线对比抽屉 ---------------- */

let panel = null;

export function closePanel() {
  if (panel) { panel.remove(); panel = null; }
}

/** 把预填话术写进聊天输入框:dispatch input 让自适应高度/状态联动感知。 */
function prefillChat(text) {
  const prompt = document.getElementById('prompt');
  if (!prompt) return;
  prompt.value = text;
  prompt.dispatchEvent(new Event('input', { bubbles: true }));
  closePanel();
  prompt.focus();
}

function reqChip(req) {
  return h('span', { class: `rec-req ${req.ok ? 'rec-req-ok' : 'rec-req-miss'}` },
    icon(req.ok ? 'check' : 'x', 12),
    `${req.label}${req.optional ? '(可选)' : ''}`);
}

function listCol(title, cls, items) {
  return h('div', { class: `rec-col ${cls}` },
    h('div', { class: 'rec-col-hd' }, title),
    h('ul', { class: 'rec-list' }, ...(items || []).map((s) => h('li', null, s))));
}

/** 单条路线的对比卡。 */
function routeCard(route, ds) {
  const badge = badgeState(route);
  return h('div', { class: 'rec-route' },
    h('div', { class: 'rec-route-hd' },
      h('span', { class: 'rec-name' }, route.name),
      h('span', { class: `rec-badge ${badge.cls}` }, badge.label)),
    h('div', { class: 'rec-cols' },
      listCol('优势', 'rec-pros', route.pros),
      listCol('代价', 'rec-cons', route.cons)),
    route.requirements && route.requirements.length
      ? h('div', { class: 'rec-reqs' }, ...route.requirements.map(reqChip)) : null,
    h('div', { class: 'rec-meta' }, `涉及步骤:${stepsLabel(route.steps_involved)}`),
    h('div', { class: 'rec-meta' }, route.est_note),
    h('button', {
      class: 'btn btn-pri btn-sm rec-start', type: 'button',
      'aria-label': `按路线「${route.name}」预填对话(不会自动发送)`,
      onclick: () => prefillChat(prefillText(route, ds)),
    }, icon('chat', 14), '按此路线开始对话'));
}

function openPanel(ds) {
  closePanel();
  const host = h('div', { class: 'rec-body', 'aria-busy': 'true' },
    h('div', { class: 'rec-empty' }, '正在生成处理建议…'));
  panel = h('div', {
    class: 'rec-panel', role: 'dialog', 'aria-modal': 'false',
    'aria-label': `数据集 ${ds.name} 的处理建议`,
  },
    h('div', { class: 'rec-hd' },
      h('span', { class: 'rec-title' }, '处理建议'),
      h('span', { class: 'rec-ds mono' }, ds.name),
      h('span', { class: 'grow' }),
      h('button', {
        class: 'btn btn-gho btn-sm', type: 'button',
        'aria-label': '关闭处理建议', onclick: closePanel,
      }, icon('x'), '关闭')),
    h('div', { class: 'rec-sub mono' }, ds.path),
    host);
  panel.tabIndex = -1;
  panel.addEventListener('keydown', (e) => { if (e.key === 'Escape') closePanel(); });
  document.body.appendChild(panel);
  panel.focus();

  fetchRecommend(ds.id).then((data) => {
    if (!panel || !panel.isConnected) return;   // 等待期间已被关闭
    host.removeAttribute('aria-busy');
    if (!data || !Array.isArray(data.routes)) {
      host.replaceChildren(h('div', { class: 'rec-empty' },
        '处理建议获取失败(后端未连接或数据集已移除)。'));
      return;
    }
    host.replaceChildren(...data.routes.map((r) => routeCard(r, data.dataset || ds)));
  });
}

/* ---------------- 「处理建议」入口注入与事件委托 ---------------- */

const BTN_CLASS = 'rec-btn';

/** 给尚未注入的 .ds-card 补「处理建议」入口(span 承载,卡片本身是 button)。 */
function augmentCards(root) {
  for (const card of root.querySelectorAll('.ds-card')) {
    if (card.querySelector(`.${BTN_CLASS}`)) continue;
    const hd = card.querySelector('.ds-card-hd') || card;
    hd.appendChild(h('span', {
      class: BTN_CLASS, role: 'button', tabindex: '0',
      'aria-label': `查看数据集 ${card.title || ''} 的处理建议`,
    }, '处理建议'));
  }
}

/** 激活入口:卡片 title(= 数据集 path)→ 清单反查条目 → 开抽屉。 */
async function activate(card) {
  const data = await fetchDatasets();          // 后端 60s TTL 缓存,与清单同源
  const ds = matchByPath(data && data.datasets, card.title);
  if (ds) openPanel(ds);
}

function onActivate(e) {
  const btn = e.target && e.target.closest && e.target.closest(`.${BTN_CLASS}`);
  if (!btn) return;
  if (e.type === 'keydown' && e.key !== 'Enter' && e.key !== ' ') return;
  // 捕获阶段拦截:阻止事件到达 .ds-card 自身的 onclick(否则误开文件抽屉)
  e.preventDefault();
  e.stopPropagation();
  const card = btn.closest('.ds-card');
  if (card) activate(card);
}

function init() {
  document.addEventListener('click', onActivate, true);
  document.addEventListener('keydown', onActivate, true);

  const tryMount = () => {
    const pane = document.getElementById('pane-files');
    if (!pane) return false;
    augmentCards(pane);
    // datasets.js 每次刷新会重建卡片:子树观察,重建后把入口补回去
    // (augmentCards 先查重再插,插入触发的下一轮回调命中查重即止,不自激)
    new MutationObserver(() => augmentCards(pane))
      .observe(pane, { childList: true, subtree: true });
    return true;
  };
  if (tryMount()) return;
  // 防御:dock 尚未挂载(脚本顺序漂移)时,等 #pane-files 出现再接管
  const mo = new MutationObserver(() => { if (tryMount()) mo.disconnect(); });
  mo.observe(document.body, { childList: true, subtree: true });
}

// node 测试环境(recommend.check.mjs)无 document:只导出纯函数,不自初始化
if (typeof document !== 'undefined' && typeof location !== 'undefined') {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init, { once: true });
  } else {
    init();
  }
}
