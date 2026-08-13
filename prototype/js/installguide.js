/* ============================================================
   环境安装助手(自初始化,与 doctorpanel/diagexport 同区共存)。

   环境面板每一行缺失引擎(.eng 里 .st.bad)旁注入「如何安装」按钮 →
   右侧抽屉:途径选项卡(WSL / conda / 手动)、逐步命令(每条一键复制)、
   预计耗时/磁盘、坑位提示、前置条件满足态、「我装好了,重新检测」。

   数据源:GET /api/install/guide(30s TTL 缓存);
   确认重探:POST /api/install/mark-done {engine}(服务端穿透 WSL 探测缓存)。
   安全边界:界面只展示可复制命令,绝不代跑安装(后端也没有代跑端点)。

   dock.js 每次切换都重建 env pane(replaceChildren),按钮由
   MutationObserver 在重建后补注(幂等,同 diagexport 做法,不改 dock.js)。
   离线(file://)不注入 —— 指引数据在后端。
   ============================================================ */
import { h, icon } from './dom.js';

const API_GUIDE = '/api/install/guide';
const API_MARK_DONE = '/api/install/mark-done';

/** 七引擎闭集(与 runtime/install_guide.py 的 ENGINE_ORDER 一致):
    只给认识的引擎行注按钮,演示数据里的非引擎行(如 gacos 凭据)不打扰。 */
export const KNOWN_ENGINES = ['mintpy', 'gdal', 'snaphu', 'isce2', 'pyaps', 'pystamps', 'snap'];

export const ROUTE_LABEL = { wsl: 'WSL', conda: 'conda', manual: '手动' };
export const routeLabel = (id) => ROUTE_LABEL[id] || id;

/** 途径元信息行:预计耗时 + 磁盘占用(0 → 增量可忽略)。 */
export function metaText(route) {
  const disk = route.disk_gb > 0 ? `磁盘约 ${route.disk_gb} GB` : '磁盘增量可忽略';
  return `预计 ${route.est_minutes} 分钟 · ${disk}`;
}

/** 「复制全部」的文本:逐条命令按行拼接(粘进终端可逐行执行)。 */
export const copyAllText = (route) => (route.steps || []).join('\n');

/* ---------------- 指引数据(30s TTL 缓存;mark-done 后失效) ---------------- */

let guideCache = { at: 0, promise: null };

export function invalidateGuide() {
  guideCache = { at: 0, promise: null };
}

export function fetchGuide({ force = false } = {}) {
  if (!force && guideCache.promise && Date.now() - guideCache.at < 30_000) {
    return guideCache.promise;
  }
  const promise = (async () => {
    const resp = await fetch(API_GUIDE);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return resp.json();
  })().catch((err) => {
    guideCache = { at: 0, promise: null };  // 失败不占缓存位:下次立即重试
    throw err;
  });
  guideCache = { at: Date.now(), promise };
  return promise;
}

/* ---------------- 一键复制(状态机:复制 → 已复制 ✓ / 复制失败) ---------------- */

export async function copyText(text, btn) {
  const orig = '复制';
  try {
    await navigator.clipboard.writeText(text);
    btn.textContent = '已复制 ✓';
    btn.classList.add('is-ok');
  } catch {
    btn.textContent = '复制失败';
    btn.classList.add('is-bad');
  }
  btn.disabled = true;
  setTimeout(() => {
    btn.textContent = orig;
    btn.className = 'instg-copy';
    btn.disabled = false;
  }, 1600);
}

/* ---------------- 装好确认(mark-done → 服务端缓存失效重探) ---------------- */

export async function markDone(engine) {
  const resp = await fetch(API_MARK_DONE, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ engine }),
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const out = await resp.json();
  invalidateGuide();  // 本模块的指引缓存作废
  // 环境面板的 30s 缓存一并作废:下次渲染即拉到重探结果(加载失败静默,不耦合)
  import('./envlive.js').then((m) => m.invalidate()).catch(() => {});
  document.dispatchEvent(new CustomEvent('install:probe-refreshed', { detail: out }));
  return out;
}

/* ---------------- 抽屉(单例;途径选项卡状态机) ---------------- */

let overlay = null;     // 抽屉 DOM 单例(打开时挂 body)
let lastFocus = null;   // 关闭后焦点归还

export function closeDrawer() {
  if (overlay) overlay.remove();
  overlay = null;
  if (lastFocus && typeof lastFocus.focus === 'function') lastFocus.focus();
  lastFocus = null;
}

function onDrawerKeydown(e) {
  if (e.key === 'Escape') closeDrawer();
}

/** 前置条件行:met=true ✓ / false ✗ / null 「请自行确认」。 */
function requireRow(d) {
  const state = d.met === true ? 'met' : d.met === false ? 'unmet' : 'unknown';
  const mark = d.met === true ? icon('check', 11) : d.met === false ? icon('x', 11) : icon('warn', 11);
  const suffix = d.met === true ? '' : d.met === false ? '(未满足)' : '(请自行确认)';
  return h('div', { class: `instg-req is-${state}` }, mark,
    h('span', null, `前置:${d.label}${suffix}`));
}

/** 单条途径的详情体(meta / 前置 / 逐步命令 / 坑位)。 */
export function renderRoute(route) {
  const steps = (route.steps || []).map((step, i) =>
    h('div', { class: 'instg-step' },
      h('span', { class: 'instg-idx mono', 'aria-hidden': 'true' }, String(i + 1)),
      h('code', { class: 'instg-cmd' }, step),
      h('button', {
        class: 'instg-copy', type: 'button', 'aria-label': `复制第 ${i + 1} 步`,
        onclick: (e) => copyText(step, e.currentTarget || e.target),
      }, '复制')));
  return h('div', { class: 'instg-route' },
    h('div', { class: 'instg-meta' },
      h('span', { class: 'instg-badge' }, icon('clock', 11), metaText(route)),
      h('span', { class: 'grow' }),
      h('button', {
        class: 'instg-copy instg-copyall', type: 'button',
        'aria-label': '复制全部命令',
        onclick: (e) => copyText(copyAllText(route), e.currentTarget || e.target),
      }, '复制')),
    ...(route.requires_detail || []).map(requireRow),
    h('div', { class: 'instg-steps' }, ...steps),
    (route.notes || []).length
      ? h('div', { class: 'instg-notes' },
          ...(route.notes || []).map((n) =>
            h('div', { class: 'instg-note' }, icon('warn', 11), h('span', null, n))))
      : null);
}

/** 途径选项卡 + 当前途径详情(选项卡状态机:aria-selected 单选)。 */
export function renderPlanBody(host, plan, activeId) {
  const active = plan.routes.find((r) => r.route === activeId) || plan.routes[0];
  host.replaceChildren(
    h('p', { class: 'instg-why' },
      h('b', null, `推荐途径 ${routeLabel(plan.recommend)}:`), plan.why),
    h('div', { class: 'instg-tabs', role: 'tablist', 'aria-label': '安装途径' },
      ...plan.routes.map((r) => h('button', {
        class: 'instg-tab', type: 'button', role: 'tab',
        'aria-selected': r.route === active.route ? 'true' : 'false',
        onclick: () => renderPlanBody(host, plan, r.route),
      }, routeLabel(r.route),
        r.route === plan.recommend ? h('span', { class: 'instg-rec' }, '荐') : null))),
    h('div', { class: 'instg-title' }, active.title),
    renderRoute(active));
}

/** 抽屉主体渲染:加载中 → 方案 / 已就绪 / 加载失败。 */
async function renderDrawer(body, footer, engine) {
  body.replaceChildren(h('p', { class: 'instg-loading', 'aria-busy': 'true' },
    '正在读取安装方案(GET /api/install/guide;WSL 冷探测最长约 30 秒)…'));
  let guide;
  try {
    guide = await fetchGuide();
  } catch (err) {
    body.replaceChildren(h('div', { class: 'instg-error', role: 'status' },
      icon('warn'), h('span', null, `安装方案加载失败:${err.message || err}。`),
      h('button', {
        class: 'btn btn-gho btn-sm', type: 'button',
        onclick: () => { invalidateGuide(); renderDrawer(body, footer, engine); },
      }, '重试')));
    return;
  }
  if (!overlay || !body.isConnected) return;   // 抽屉已关,丢弃过期结果
  const plan = (guide.plans || []).find((p) => p.engine === engine);
  if (!plan) {
    const ver = (guide.engines || {})[engine];
    body.replaceChildren(h('div', { class: 'instg-ready', role: 'status' },
      icon('check'),
      h('span', null, `已探测到 ${engine}`, ver ? `(${ver})` : '', ',无需安装。')));
    footer.hidden = true;
    return;
  }
  footer.hidden = false;
  renderPlanBody(body, plan, plan.recommend);
}

export function openDrawer(engine) {
  closeDrawer();
  lastFocus = document.activeElement;

  const body = h('div', { class: 'instg-body' });
  const status = h('p', { class: 'instg-status', role: 'status', 'aria-live': 'polite' },
    '装完后点击重新检测:服务端会穿透探测缓存强制重探。');
  const doneBtn = h('button', { class: 'btn instg-done', type: 'button' },
    icon('refresh'), '我装好了,重新检测');
  doneBtn.addEventListener('click', async () => {
    doneBtn.disabled = true;
    doneBtn.textContent = '正在重新探测…';
    status.className = 'instg-status';
    status.textContent = '正在重新探测(WSL 冷启动最长约 30 秒)…';
    try {
      const out = await markDone(engine);
      if (out.present) {
        status.className = 'instg-status is-ok';
        status.textContent = `已探测到 ${engine}(${out.version}),环境就绪;环境面板将在下次刷新更新。`;
        renderDrawer(body, footer, engine);   // 方案区切到「已就绪」态
      } else {
        status.className = 'instg-status is-warn';
        status.textContent = `仍未探测到 ${engine}:请确认步骤已执行完(改过 PATH 需新开终端/重启服务),或切换其他途径。`;
      }
    } catch (err) {
      status.className = 'instg-status is-bad';
      status.textContent = `重新探测失败:${err.message || err}。请稍后重试。`;
    } finally {
      doneBtn.disabled = false;
      doneBtn.replaceChildren(icon('refresh'), '我装好了,重新检测');
    }
  });
  const footer = h('div', { class: 'instg-ft' }, doneBtn, status);

  const drawer = h('aside', {
    class: 'instg-drawer', role: 'dialog', 'aria-modal': 'true',
    'aria-label': `${engine} 安装指引`,
  },
    h('div', { class: 'instg-hd' },
      h('b', null, '如何安装 '), h('span', { class: 'mono instg-eng' }, engine),
      h('span', { class: 'grow' }),
      h('button', {
        class: 'instg-x', type: 'button', 'aria-label': '关闭安装指引',
        onclick: closeDrawer,
      }, icon('x'))),
    body, footer);

  overlay = h('div', { class: 'instg-overlay' },
    h('button', { class: 'instg-scrim', type: 'button', 'aria-label': '关闭安装指引', onclick: closeDrawer }),
    drawer);
  overlay.addEventListener('keydown', onDrawerKeydown);
  document.body.appendChild(overlay);
  renderDrawer(body, footer, engine);
  return overlay;
}

/* ---------------- 环境面板按钮注入(幂等;不改 dock.js) ---------------- */

/** 引擎行 .nm 的首个文本节点 = 引擎名(后面可能跟宿主徽标 span)。 */
function engineNameOf(row) {
  const nm = row.querySelector('.nm');
  const first = nm && nm.childNodes && nm.childNodes[0];
  return first ? String(first.textContent || '').trim().toLowerCase() : '';
}

export function ensureRowButtons() {
  const pane = document.getElementById('pane-env');
  if (!pane) return;
  for (const row of pane.querySelectorAll('.eng')) {
    if (row.querySelector('.instg-howto')) continue;       // 已注过:幂等
    if (!row.querySelector('.st.bad')) continue;           // 引擎在位:不打扰
    const name = engineNameOf(row);
    if (!KNOWN_ENGINES.includes(name)) continue;           // 闭集之外不注
    row.appendChild(h('button', {
      class: 'instg-howto', type: 'button',
      'aria-label': `如何安装 ${name}`, 'aria-haspopup': 'dialog',
      onclick: () => openDrawer(name),
    }, '如何安装'));
  }
}

/* 自初始化:env pane 由 dock.js 随时重建,body 级观察器兜底补注。 */
if (typeof document !== 'undefined'
    && typeof location !== 'undefined' && location.protocol !== 'file:') {
  ensureRowButtons();
  new MutationObserver(ensureRowButtons)
    .observe(document.body, { childList: true, subtree: true });
}
