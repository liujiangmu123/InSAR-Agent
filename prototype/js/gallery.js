/* ============================================================
   影像网格 + 灯箱(零依赖,面板 2「影像」的产物画廊)
   数据源优先级:
     1. GET /api/figures —— 真实运行产物,缩略图/大图 src 指向
        GET /api/artifact-file(服务端已做工作区路径校验);
     2. 后端不可达 / file:// / 列表为空 —— 回落 figures.js 的
        演示 SVG,并在卡片与灯箱上明确标注「演示图件」。
   灯箱:遮罩 + 大图 + 左右键/Esc 键盘导航 + 焦点陷阱,
   与 stream.js 的 #lightbox(单图演示灯箱)互不依赖。
   ============================================================ */
import { h } from './dom.js';
import { S } from './state.js';
import { figureNode, IMAGES } from './figures.js';

/* ---------------- 数据源 ---------------- */

/* Dock.refresh 在运行期间高频触发:同一会话 3 秒内复用同一个
   in-flight promise,避免打爆后端(与 dock.js 的 cachedFetch 同策略)。 */
const cache = { key: '', at: 0, promise: null };

function fetchFigures() {
  const key = S.sessionId;
  if (cache.promise && cache.key === key && Date.now() - cache.at < 3000) {
    return cache.promise;
  }
  cache.key = key;
  cache.at = Date.now();
  cache.promise = (async () => {
    if (location.protocol === 'file:') return null;   // 静态打开必无后端
    try {
      const qs = new URLSearchParams({ session: key });
      const resp = await fetch(`/api/figures?${qs}`);
      if (!resp.ok) return null;
      return await resp.json();
    } catch {
      return null;   // 离线/跨域/解析失败:一律回落演示,不打断 UI
    }
  })();
  return cache.promise;
}

/** 手动失效缓存(「刷新」按钮用)。 */
function invalidate() { cache.at = 0; }

/* ---------------- 条目归一化 ----------------
   真实产物与演示图件共用一个条目形状,网格与灯箱不再分辨来源:
   { name, step, date, demo, node() → 缩略图/大图节点 } */

function realItem(fig) {
  const d = new Date(fig.mtime * 1000);
  const pad = (n) => String(n).padStart(2, '0');
  return {
    name: fig.name,
    step: fig.step,
    date: `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`,
    size: fig.size,
    demo: false,
    node: () => h('img', {
      src: fig.url, alt: fig.name, loading: 'lazy', draggable: 'false',
    }),
  };
}

function demoItem(g) {
  return {
    name: g.name,
    step: g.step,
    date: '',
    title: g.title,
    demo: true,
    node: () => figureNode(g.id),
  };
}

/* ---------------- 网格视图 ---------------- */

/**
 * 影像面板入口:立即返回容器,异步填充网格。
 * dock.js 的 imagesView 只需调用本函数,不关心数据来源。
 */
export function galleryView() {
  const box = h('div', { class: 'glx' },
    h('h3', { class: 'sect' }, '产物图件 · 点击查看大图'),
    h('p', { class: 'blurb' }, '正在读取产物列表(GET /api/figures)…'));
  load(box);
  return box;
}

async function load(box) {
  const data = await fetchFigures();
  if (!box.isConnected) return;   // 面板已切走/重渲染,丢弃过期结果

  const real = Array.isArray(data?.figures) && data.figures.length > 0;
  const items = real ? data.figures.map(realItem) : IMAGES.map(demoItem);
  const note = real
    ? `真实运行产物 · ${items.length} 张(GET /api/figures)。缩略图与大图直读 run 工作区文件。`
    : data === null
      ? '后端不可达 —— 以下为演示图件(手绘 SVG,非真实产物)。'
      : '该会话暂无图像产物 —— 运行出图步骤后此处显示真实 PNG;以下为演示图件。';

  box.replaceChildren(
    h('h3', { class: 'sect' }, real ? '产物图件 · 点击查看大图' : '产物图件(演示) · 点击查看大图',
      real ? h('button', {
        class: 'glx-refresh', type: 'button', title: '重新读取产物列表',
        'aria-label': '刷新产物列表',
        onclick: () => { invalidate(); load(box); },
      }, '刷新') : null),
    grid(items),
    h('p', { class: 'blurb' }, note));
}

function grid(items) {
  const g = h('div', { class: 'glx-grid', role: 'list' });
  items.forEach((it, i) => {
    g.appendChild(h('button', {
      class: 'glx-card', type: 'button', role: 'listitem',
      'aria-label': `查看大图:${it.name}(第 ${it.step} 步)`,
      onclick: (e) => openLightbox(items, i, e.currentTarget),
    },
      h('div', { class: 'glx-thumb' },
        it.node(),
        it.demo ? h('span', { class: 'glx-demo' }, '演示图件') : null),
      h('div', { class: 'glx-meta' },
        h('span', { class: 'glx-name', title: it.title || it.name }, it.name),
        h('span', { class: 'glx-sub' },
          `第 ${it.step} 步${it.date ? ` · ${it.date}` : ''}${it.size ? ` · ${fmtSize(it.size)}` : ''}`))));
  });
  return g;
}

function fmtSize(bytes) {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${bytes} B`;
}

/* ---------------- 灯箱 ---------------- */

let lb = null;   // { root, items, idx, returnFocus } —— 全局至多一个灯箱

function openLightbox(items, idx, returnFocus) {
  closeLightbox();

  const stage = h('div', { class: 'glx-lb-stage' });
  const nameEl = h('span', { class: 'glx-lb-name' });
  const stepEl = h('span', { class: 'glx-lb-step' });
  const countEl = h('span', { class: 'glx-lb-count' });

  const prev = h('button', {
    class: 'glx-lb-nav glx-lb-prev', type: 'button', 'aria-label': '上一张(←)',
    onclick: () => show(lb.idx - 1),
  }, '‹');
  const next = h('button', {
    class: 'glx-lb-nav glx-lb-next', type: 'button', 'aria-label': '下一张(→)',
    onclick: () => show(lb.idx + 1),
  }, '›');
  const close = h('button', {
    class: 'glx-lb-close', type: 'button', 'aria-label': '关闭(Esc)',
    onclick: () => closeLightbox(),
  }, '×');

  const root = h('div', {
    class: 'glx-lb', role: 'dialog', 'aria-modal': 'true', 'aria-label': '图件大图查看',
    tabindex: '-1',
    // 点遮罩(而非内容)关闭
    onclick: (e) => { if (e.target === root || e.target === stage) closeLightbox(); },
    onkeydown: onKey,
  },
    close,
    items.length > 1 ? prev : null,
    stage,
    items.length > 1 ? next : null,
    h('div', { class: 'glx-lb-bar' }, nameEl, stepEl, countEl));

  lb = { root, items, idx, returnFocus, stage, nameEl, stepEl, countEl };
  document.body.appendChild(root);
  show(idx);
  close.focus();

  function show(i) {
    const n = lb.items.length;
    lb.idx = ((i % n) + n) % n;   // 环形导航
    const it = lb.items[lb.idx];
    const big = it.node();
    if (big.tagName === 'IMG') big.removeAttribute('loading');   // 大图立即加载
    lb.stage.replaceChildren(big, it.demo ? h('span', { class: 'glx-demo' }, '演示图件') : '');
    lb.nameEl.textContent = it.name;
    lb.stepEl.textContent = `第 ${it.step} 步${it.date ? ` · ${it.date}` : ''}`;
    lb.countEl.textContent = n > 1 ? `${lb.idx + 1} / ${n}` : '';
  }

  function onKey(e) {
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closeLightbox(); return; }
    if (e.key === 'ArrowLeft') { e.preventDefault(); show(lb.idx - 1); return; }
    if (e.key === 'ArrowRight') { e.preventDefault(); show(lb.idx + 1); return; }
    if (e.key === 'Tab') {
      // 焦点陷阱:Tab 只在灯箱内的按钮间循环
      const focusables = [...root.querySelectorAll('button')];
      if (!focusables.length) return;
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      else if (!root.contains(document.activeElement)) { e.preventDefault(); first.focus(); }
    }
  }
}

export function closeLightbox() {
  if (!lb) return;
  lb.root.remove();
  if (lb.returnFocus?.isConnected) lb.returnFocus.focus?.();
  lb = null;
}
