/* ============================================================
   数据集区(「文件」tab 顶部,自初始化模块 —— 数据集管理第一步:
   让用户与系统都知道本机有哪些 InSAR 数据、状态如何)

   职责:
   - 拉取 GET /api/datasets(后端 60s TTL 缓存)渲染数据集卡片:
     类型徽章(五色)/ 名称 / 人性化大小 / 对数(按类型语义)/ 日期范围;
   - 「重新扫描」→ GET /api/datasets?rescan=1 穿透后端缓存;
   - 「添加目录」→ POST /api/datasets/roots {path},校验失败的 detail
     原样提示(绝对路径/存在性/拒穿越由后端把关);
   - 点击卡片 → 右侧抽屉展示 GET /api/datasets/{id} 的文件清单(前 200 项);
   - 空态:「未发现数据集 —— 添加包含 InSAR 数据的目录」。

   挂载方式(所有权边界:不改 dock.js):index.html 引入本模块后自初始化,
   MutationObserver 盯住 #pane-files —— dock.js 每次切到「文件」tab 都会
   replaceChildren 重建面板内容,本区在每次重建后把自己重新前置回去
   (回调先查重再插入,插入触发的下一轮回调命中查重即止,不会自激)。

   失败语义(对齐 fileslive.js):file:// 打开或后端不可达 → resolve null,
   区块显示「后端未连接」说明,绝不抛错、绝不遮挡文件面板其余内容。
   ============================================================ */
import { h, icon } from './dom.js';

/* ---------------- 纯函数(prototype/datasets.check.mjs 直接断言) ---------------- */

/** 类型 → 徽章(五色闭集,与后端 catalog.KINDS 对齐;未知类型回落 unknown)。 */
export const KIND_BADGE = {
  hyp3:      { label: 'HyP3 产品', cls: 'ds-b-hyp3' },
  alos_raw:  { label: 'ALOS 原始', cls: 'ds-b-alos' },
  slc_stack: { label: 'SLC 栈',    cls: 'ds-b-slc' },
  dem:       { label: 'DEM',       cls: 'ds-b-dem' },
  unknown:   { label: '未知',      cls: 'ds-b-unknown' },
};

export function badgeOf(kind) {
  return KIND_BADGE[kind] || KIND_BADGE.unknown;
}

/** 字节数 → 人性化大小(口径同 fileslive.humanSize;0/undefined 容错)。 */
export function humanSize(n) {
  if (n === null || n === undefined) return '—';
  if (n < 1024) return `${n} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let v = n, i = -1;
  do { v /= 1024; i += 1; } while (v >= 1024 && i < units.length - 1);
  return `${v >= 100 ? Math.round(v) : v.toFixed(1)} ${units[i]}`;
}

/** 文件名内嵌日期解析(镜像后端 catalog._DATE_RE:YYYYMMDD 且后随
    T+6 位时刻或分隔符/结尾,月日合法才算),返回升序 ISO 日期数组。 */
export function parseDates(name) {
  const out = new Set();
  for (const m of String(name || '').matchAll(/(?<!\d)((?:19|20)\d{6})(?=T\d{6}|[_\-.]|$)/g)) {
    const s = m[1];
    const mo = +s.slice(4, 6), d = +s.slice(6, 8);
    if (mo >= 1 && mo <= 12 && d >= 1 && d <= 31) {
      out.add(`${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6, 8)}`);
    }
  }
  return [...out].sort();
}

/** 日期范围 {start,end} → 行内展示;无范围 → '—'。 */
export function fmtDateRange(range) {
  if (!range || !range.start) return '—';
  return range.start === range.end ? range.start : `${range.start} ~ ${range.end}`;
}

/** 数据集条目 → 数量徽标文案(按类型取 detail 里的语义计数)。 */
export function countLabel(ds) {
  const d = (ds && ds.detail) || {};
  switch (ds && ds.kind) {
    case 'hyp3': return `${d.pairs ?? 0} 对干涉`;
    case 'alos_raw': return `${d.scenes ?? 0} 组场景`;
    case 'slc_stack': return `${(d.slc ?? 0) + (d.safe ?? 0)} 景 SLC`;
    case 'dem': return `${(d.dem_files || []).length} 个 DEM`;
    default: return `${(ds && ds.file_count) ?? 0} 个文件`;
  }
}

/* ---------------- 取数(失败一律 resolve null,不抛错) ---------------- */

export function fetchDatasets({ rescan = false } = {}) {
  if (location.protocol === 'file:') return Promise.resolve(null);  // 与 fileslive 同判据
  return fetch(`/api/datasets${rescan ? '?rescan=1' : ''}`)
    .then((r) => (r.ok ? r.json() : null))
    .catch(() => null);
}

export function fetchDatasetDetail(id) {
  if (location.protocol === 'file:') return Promise.resolve(null);
  return fetch(`/api/datasets/${encodeURIComponent(id)}`)
    .then((r) => (r.ok ? r.json() : null))
    .catch(() => null);
}

/** 添加扫描根;返回 {ok, error?}(后端 400 的 detail 透传给输入行提示)。 */
export async function addRoot(path) {
  if (location.protocol === 'file:') return { ok: false, error: '后端未连接' };
  try {
    const r = await fetch('/api/datasets/roots', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path }),
    });
    if (r.ok) return { ok: true };
    const body = await r.json().catch(() => null);
    const detail = body && body.detail;
    return { ok: false, error: typeof detail === 'string' ? detail : `HTTP ${r.status}` };
  } catch {
    return { ok: false, error: '后端不可达' };
  }
}

/* ---------------- 渲染 ---------------- */

// 模块级 UI 状态:tab 切换会整体重建 DOM,展示态(上次清单/输入行开合)须存活
const ui = { data: null, adding: false, msg: '' };
// 桌面壳注册数据根后广播 datasets:refresh(desktopdata.js / desktop/src/opendata.rs)→ 重拉清单
let refreshNow = null; // 当前区块的 refresh 句柄(区块随 tab 切换整体重建,句柄随之更替)
if (typeof window !== 'undefined') window.addEventListener('datasets:refresh',
  () => refreshNow && refreshNow({ reload: true }));

function badgeEl(kind) {
  const b = badgeOf(kind);
  return h('span', { class: `ds-badge ${b.cls}` }, b.label);
}

/** 数据集卡片(button:回车/空格可开抽屉)。 */
function card(ds) {
  return h('button', {
    class: 'ds-card', type: 'button',
    title: ds.path,
    'aria-label': `查看数据集 ${ds.name} 的文件清单`,
    onclick: () => openDrawer(ds),
  },
    h('div', { class: 'ds-card-hd' }, badgeEl(ds.kind),
      h('span', { class: 'ds-name' }, ds.name)),
    h('div', { class: 'ds-meta' },
      h('span', null, humanSize(ds.size_bytes)),
      h('span', null, countLabel(ds)),
      h('span', { class: 'ds-dates' }, fmtDateRange(ds.date_range))));
}

/** 「添加目录」输入行(展开态)。 */
function addRow(refresh) {
  const input = h('input', {
    class: 'ds-input', type: 'text', id: 'dsRootInput',
    placeholder: '绝对路径,如 E:\\data\\insar',
    'aria-label': '要添加的数据目录绝对路径',
  });
  const submit = async () => {
    const path = input.value.trim();
    if (!path) return;
    ui.msg = '正在添加…';
    refresh();
    const res = await addRoot(path);
    ui.msg = res.ok ? '' : (res.error || '添加失败');
    if (res.ok) ui.adding = false;
    await refresh({ reload: true });
  };
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter') submit(); });
  return h('div', { class: 'ds-addrow' },
    input,
    h('button', {
      class: 'btn btn-gho btn-sm', type: 'button',
      'aria-label': '确认添加该目录为扫描根', onclick: submit,
    }, '添加'),
    ui.msg ? h('span', { class: 'ds-err', role: 'status' }, ui.msg) : null);
}

/** 区块主体:清单 / 空态 / 后端未连接三态。 */
function body() {
  if (ui.data === null) {
    return h('div', { class: 'ds-empty' },
      '后端未连接 —— 数据集清单需要本地服务(演示模式下不可用)。');
  }
  const list = ui.data.datasets || [];
  if (!list.length) {
    return h('div', { class: 'ds-empty' },
      '未发现数据集 —— 添加包含 InSAR 数据的目录',
      h('div', { class: 'ds-empty-sub' },
        '扫描根:INSAR_DATA_DIR 环境变量、workspace/datasets、以及此处手动添加的目录。'));
  }
  return h('div', { class: 'ds-grid' }, ...list.map(card));
}

function section(pane) {
  const box = h('div', { class: 'ds-section', id: 'dsSection' });

  // refresh:重拉数据并原地重渲染(reload=true 时才发请求,纯 UI 态变化不重拉)
  const refresh = async ({ reload = false, rescan = false } = {}) => {
    if (reload) ui.data = await fetchDatasets({ rescan });
    render();
  };
  refreshNow = refresh; // datasets:refresh 事件的当前刷新出口(见模块顶部监听)

  const render = () => {
    const n = ui.data && ui.data.datasets ? ui.data.datasets.length : 0;
    box.replaceChildren(
      h('div', { class: 'ds-hd' },
        h('h3', { class: 'sect' }, '数据集'),
        n ? h('span', { class: 'ds-count' }, String(n)) : null,
        h('span', { class: 'grow' }),
        h('button', {
          class: 'btn btn-gho btn-sm', type: 'button',
          'aria-label': '重新扫描全部数据根目录(穿透 60 秒缓存)',
          onclick: () => refresh({ reload: true, rescan: true }),
        }, icon('refresh'), '重新扫描'),
        h('button', {
          class: 'btn btn-gho btn-sm', type: 'button',
          'aria-label': '添加数据目录', 'aria-expanded': String(ui.adding),
          onclick: () => { ui.adding = !ui.adding; ui.msg = ''; render(); },
        }, icon('plus'), '添加目录')),
      ui.adding ? addRow(refresh) : null,
      body());
  };

  render();
  refresh({ reload: true });
  pane.prepend(box);
}

/* ---------------- 抽屉(文件清单) ---------------- */

let drawer = null;

export function closeDrawer() {
  if (drawer) { drawer.remove(); drawer = null; }
}

async function openDrawer(ds) {
  closeDrawer();
  const listHost = h('div', { class: 'ds-files', 'aria-busy': 'true' },
    h('div', { class: 'ds-empty' }, '正在读取文件清单…'));
  drawer = h('div', {
    class: 'ds-drawer', role: 'dialog', 'aria-modal': 'false',
    'aria-label': `数据集 ${ds.name} 文件清单`,
  },
    h('div', { class: 'ds-drawer-hd' }, badgeEl(ds.kind),
      h('span', { class: 'ds-name' }, ds.name),
      h('span', { class: 'grow' }),
      h('button', {
        class: 'btn btn-gho btn-sm', type: 'button',
        'aria-label': '关闭文件清单', onclick: closeDrawer,
      }, icon('x'), '关闭')),
    h('div', { class: 'ds-drawer-meta mono' }, ds.path),
    h('div', { class: 'ds-drawer-meta' },
      `${humanSize(ds.size_bytes)} · ${countLabel(ds)} · ${fmtDateRange(ds.date_range)}`),
    listHost);
  // Esc 关闭(监听挂在抽屉上,移除节点即随之释放)
  drawer.tabIndex = -1;
  drawer.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeDrawer(); });
  document.body.appendChild(drawer);
  drawer.focus();

  const detail = await fetchDatasetDetail(ds.id);
  if (!drawer || !drawer.isConnected) return;   // 等待期间已被关闭
  listHost.removeAttribute('aria-busy');
  if (!detail) {
    listHost.replaceChildren(h('div', { class: 'ds-empty' }, '文件清单读取失败(后端不可达或数据集已移除)。'));
    return;
  }
  const rows = (detail.files || []).map((f) => h('div', { class: 'ds-frow' },
    h('span', { class: 'ds-fpath mono' }, f.path),
    h('span', { class: 'ds-fsize' }, humanSize(f.size))));
  listHost.replaceChildren(
    rows.length ? h('div', null, ...rows)
      : h('div', { class: 'ds-empty' }, '该目录下没有文件(空目录或仅含子目录)。'),
    detail.files_truncated
      ? h('div', { class: 'ds-drawer-meta' }, '仅显示前 200 项(清单已截断)。') : null);
}

/* ---------------- 自初始化 ---------------- */

function ensureSection(pane) {
  if (!pane.querySelector('#dsSection')) section(pane);
}

function init() {
  const tryMount = () => {
    const pane = document.getElementById('pane-files');
    if (!pane) return false;
    ensureSection(pane);
    // dock.js 切 tab 会 replaceChildren 重建面板:每次重建后把本区前置回去
    new MutationObserver(() => ensureSection(pane))
      .observe(pane, { childList: true });
    return true;
  };
  if (tryMount()) return;
  // 防御:dock 尚未挂载(脚本顺序漂移)时,等 #pane-files 出现再接管
  const mo = new MutationObserver(() => { if (tryMount()) mo.disconnect(); });
  mo.observe(document.body, { childList: true, subtree: true });
}

// node 测试环境(datasets.check.mjs)无 document:只导出纯函数,不自初始化
if (typeof document !== 'undefined' && typeof location !== 'undefined') {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init, { once: true });
  } else {
    init();
  }
}
