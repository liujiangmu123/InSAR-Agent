/* ============================================================
   影像网格 + 灯箱(零依赖,面板 2「影像」的产物画廊)
   数据源优先级:
     1. GET /api/figures —— 真实运行产物,三档尺寸契约:
        thumbUrl(320px 缩略,网格/胶片条)/ url(2048px browse,灯箱)/
        fullUrl(原图,「查看原图」);sidecar 元数据并入 meta 字段;
     2. 后端不可达 / file:// / 列表为空 —— 回落 figures.js 的
        演示 SVG,并在卡片与灯箱上明确标注「演示图件」。
   灯箱(交互范式依据 RESEARCH-insar-viewer-ux / RESEARCH-raster-viewer-tech):
     - Canvas pan/zoom:滚轮缩放(锚点在光标)+ 拖拽平移 + 双击复位 +
       键盘 +/− 缩放,高 DPI 感知(devicePixelRatio);
     - 底部胶片条:当前图高亮,点击跳转(Vertex 浏览查看器范式);
     - 对比模式:卷帘(clip-path)/ 混合(透明度滑杆)/ 闪烁(定时交替,
       间隔可调)/ 并排(双画布同步 pan/zoom,仅真实产物);Esc 逐层退出。
   与 stream.js 的 #lightbox(单图演示灯箱)互不依赖。
   ============================================================ */
import { h } from './dom.js';
import { S } from './state.js';
import { figureNode, IMAGES } from './figures.js';
import { activeRunId } from './runswitch.js';   // run 历史切换器:选中历史 run 时透传 run_id
import * as ES from './emptystate.js';          // states 接入:空态/骨架/错误态统一构造器

/* ---------------- 数据源 ---------------- */

/* Dock.refresh 在运行期间高频触发:同一会话 3 秒内复用同一个
   in-flight promise,避免打爆后端(与 dock.js 的 cachedFetch 同策略)。 */
const cache = { key: '', at: 0, promise: null };

function fetchFigures() {
  // run 历史切换器接线(runswitch.js):runId 非空 → /api/figures 带 run_id
  // 查看历史 run 的图件;缓存键携带 run id,切 run 立即失效不串数据。
  const runId = activeRunId();
  const key = `${S.sessionId}:${runId || ''}`;
  if (cache.promise && cache.key === key && Date.now() - cache.at < 3000) {
    return cache.promise;
  }
  cache.key = key;
  cache.at = Date.now();
  cache.promise = (async () => {
    if (location.protocol === 'file:') return null;   // 静态打开必无后端
    try {
      const qs = new URLSearchParams({ session: S.sessionId });
      if (runId) qs.set('run_id', runId);   // 缺省(最新)不带参数,行为不变
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

/* ---------------- 元数据格式化(纯函数,导出供 node 单测) ---------------- */

/** MintPy 的 YYYYMMDD 日期 → YYYY-MM-DD;其他格式原样返回。 */
export function fmtDate(s) {
  const t = String(s);
  return /^\d{8}$/.test(t) ? `${t.slice(0, 4)}-${t.slice(4, 6)}-${t.slice(6, 8)}` : t;
}

/** [vmin, vmax] → 「±23.4」(对称)或「-10 ~ 40」;非数值返回 ''。 */
function fmtVlim(vlim) {
  const a = Number(vlim[0]), b = Number(vlim[1]);
  if (!Number.isFinite(a) || !Number.isFinite(b)) return '';
  const trim = (n) => String(Number(n.toFixed(2)));
  return (b > 0 && Math.abs(a + b) < 1e-9) ? `±${trim(b)}` : `${trim(a)} ~ ${trim(b)}`;
}

/**
 * sidecar 元数据 → 一行展示文本(单位 / 色标+值域 / 日期区间)。
 * 无可用字段返回 ''(调用方回落文件名,即无 sidecar 的现状)。
 */
export function metaLine(meta) {
  if (!meta || typeof meta !== 'object') return '';
  const parts = [];
  if (meta.units) parts.push(`单位 ${meta.units}`);
  const vl = Array.isArray(meta.vlim) && meta.vlim.length === 2 ? fmtVlim(meta.vlim) : '';
  if (meta.cmap) parts.push(`色标 ${meta.cmap}${vl ? ` ${vl}` : ''}`);
  else if (vl) parts.push(`值域 ${vl}`);
  const dr = Array.isArray(meta.date_range) ? meta.date_range.filter(Boolean) : [];
  if (dr.length) parts.push(dr.map(fmtDate).join(' → '));
  return parts.join(' · ');
}

/* ---------------- 条目归一化 ----------------
   真实产物与演示图件共用一个条目形状,网格与灯箱不再分辨来源:
   { name, label, step, date, demo, meta, url/fullUrl/thumbUrl,
     node() → 网格缩略节点 } */

export function realItem(fig) {
  const d = new Date(fig.mtime * 1000);
  const pad = (n) => String(n).padStart(2, '0');
  const meta = (fig.meta && typeof fig.meta === 'object') ? fig.meta : null;
  const thumb = fig.thumbUrl || fig.url;   // 缺缩略档回退 browse/原图
  return {
    name: fig.name,
    // sidecar 标题优先,没有 sidecar 显示文件名(现状)
    label: (meta && typeof meta.title === 'string' && meta.title) || fig.name,
    step: fig.step,
    date: `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`,
    size: fig.size,
    demo: false,
    meta,
    url: fig.url,                          // browse 档(灯箱/对比)
    fullUrl: fig.fullUrl || fig.url,       // 原图(查看原图)
    thumbUrl: thumb,                       // 缩略档(网格/胶片条)
    node: () => h('img', {
      src: thumb, alt: fig.name, loading: 'lazy', draggable: 'false',
    }),
  };
}

function demoItem(g) {
  return {
    name: g.name,
    label: g.title || g.name,
    step: g.step,
    date: '',
    title: g.title,
    demo: true,
    meta: null,
    url: null, fullUrl: null, thumbUrl: null,
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
    // states 接入:请求中分支 → 网格骨架(此前是一行裸文本)
    ES.renderSkeleton(null, { kind: 'grid', rows: 4, label: '正在读取产物列表(GET /api/figures)' }));
  load(box);
  return box;
}

async function load(box) {
  const data = await fetchFigures();
  if (!box.isConnected) return;   // 面板已切走/重渲染,丢弃过期结果

  const real = Array.isArray(data?.figures) && data.figures.length > 0;
  const items = real ? data.figures.map(realItem) : IMAGES.map(demoItem);
  // states 接入:非 real 的两个分支交给统一构造器 —— 后端不可达(catch 归一为
  // null)= 错误态带「重试」;可达但无产物 = 空态卡 + 既有运行入口(自定义事件
  // 解耦);演示网格保留在下方,卡片自身已带「演示图件」角标。
  const stateNode = real ? null : (data === null
    ? ES.renderError(null, {
        message: '产物列表读取失败——后端不可达或响应异常;以下为演示图件(手绘 SVG,非真实产物)。',
        retry: () => { invalidate(); load(box); } })
    : ES.renderEmpty(null, { icon: 'image', title: '还没有产物图件',
        hint: '运行流水线的出图步骤即可生成——以下为演示图件(手绘 SVG,非真实产物)。',
        action: { label: '运行流水线以生成图件', event: 'states:run-pipeline' } }));
  const note = real
    ? `真实运行产物 · ${items.length} 张(GET /api/figures)。网格用缩略档,灯箱用 2048px 浏览档,原图另开。`
    : '';

  box.replaceChildren(...[
    h('h3', { class: 'sect' }, real ? '产物图件 · 点击查看大图' : '产物图件(演示) · 点击查看大图',
      real ? h('button', {
        class: 'glx-refresh', type: 'button', title: '重新读取产物列表',
        'aria-label': '刷新产物列表',
        onclick: () => { invalidate(); load(box); },
      }, '刷新') : null),
    stateNode,
    grid(items),
    note ? h('p', { class: 'blurb' }, note) : null,
  ].filter(Boolean));
}

/** 网格节点(导出供 node 单测校验 DOM 结构)。 */
export function grid(items) {
  const g = h('div', { class: 'glx-grid', role: 'list' });
  items.forEach((it, i) => {
    const ml = metaLine(it.meta);
    g.appendChild(h('button', {
      class: 'glx-card', type: 'button', role: 'listitem',
      'aria-label': `查看大图:${it.name}(第 ${it.step} 步)`,
      onclick: (e) => openLightbox(items, i, e.currentTarget),
    },
      h('div', { class: 'glx-thumb' },
        it.node(),
        it.demo ? h('span', { class: 'glx-demo' }, '演示图件') : null),
      h('div', { class: 'glx-meta' },
        h('span', { class: 'glx-name', title: it.name }, it.label),
        h('span', { class: 'glx-sub' },
          `第 ${it.step} 步${it.date ? ` · ${it.date}` : ''}${it.size ? ` · ${fmtSize(it.size)}` : ''}`)),
      // 元数据行:单位/色标/日期(sidecar 存在才有,与灯箱同一格式化)
      ml ? h('div', { class: 'glx-meta2', title: ml }, ml) : null));
  });
  return g;
}

function fmtSize(bytes) {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${bytes} B`;
}

/* ---------------- Canvas pan/zoom 查看器 ----------------
   参考 RESEARCH-raster-viewer-tech 骨架 A(<80 行):
   只维护一份视图状态 {scale, ox, oy},屏幕坐标 = 图像坐标×scale+(ox,oy);
   每帧 setTransform 重设(不用 translate/scale 连乘,避免漂移);
   高 DPI:位图尺寸 = CSS 尺寸 × devicePixelRatio,变换里再乘 dpr。
   shared 传入共享视图状态时,多画布同步重绘(并排对比模式)。 */

function attachViewer(canvas, shared = null) {
  const ctx = canvas.getContext('2d');
  const img = new Image();
  const view = shared || { scale: 1, ox: 0, oy: 0, peers: [] };
  const primary = view.peers.length === 0;   // 共享状态时只有首个画布负责 fit
  const ptrs = new Map();                    // 活跃指针 pointerId → 上次位置
  let dpr = 1;

  const rect = () => canvas.getBoundingClientRect();
  const fitScale = () => Math.min(
    rect().width / img.naturalWidth, rect().height / img.naturalHeight) || 1;

  function draw() {
    ctx.setTransform(1, 0, 0, 1, 0, 0);      // 每帧重设,避免变换累积漂移
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (!img.naturalWidth) return;
    ctx.setTransform(view.scale * dpr, 0, 0, view.scale * dpr, view.ox * dpr, view.oy * dpr);
    ctx.imageSmoothingEnabled = view.scale <= 1;   // 放大保像素边界;缩小抗锯齿
    ctx.imageSmoothingQuality = 'high';
    ctx.drawImage(img, 0, 0);
  }
  view.peers.push(draw);
  const redraw = () => view.peers.forEach((fn) => fn());

  function fit() {                           // 适配画布并居中(双击/初始)
    view.scale = fitScale();
    view.ox = (rect().width - img.naturalWidth * view.scale) / 2;
    view.oy = (rect().height - img.naturalHeight * view.scale) / 2;
    redraw();
  }
  function resize() {                        // 高 DPI:位图尺寸 = CSS 尺寸 × dpr
    dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(rect().width * dpr);
    canvas.height = Math.round(rect().height * dpr);
    if (img.naturalWidth && primary) fit(); else draw();
  }
  function zoomAt(px, py, f) {               // 以屏幕点 (px,py) 为锚缩放:o' = p − (p − o)·f
    const s = Math.min(Math.max(view.scale * f, fitScale() / 2), 64);
    f = s / view.scale;
    view.scale = s;
    view.ox = px - (px - view.ox) * f;
    view.oy = py - (py - view.oy) * f;
    redraw();
  }
  function zoomStep(f) {                     // 键盘 +/−:以画布中心为锚
    const r = rect();
    zoomAt(r.width / 2, r.height / 2, f);
  }

  canvas.style.touchAction = 'none';         // 手势全部交给 Pointer Events
  canvas.addEventListener('wheel', (e) => {
    e.preventDefault();                      // 需 passive:false,阻止页面滚动/整页缩放
    const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? 100 : 1;   // 行/页归一化
    const d = Math.max(-24, Math.min(24, e.deltaY * unit));   // 夹紧:滚轮±100+ vs 触控板捏合±3
    const r = rect();
    zoomAt(e.clientX - r.left, e.clientY - r.top, Math.exp(-d * 0.012));
  }, { passive: false });
  canvas.addEventListener('pointerdown', (e) => {
    canvas.setPointerCapture(e.pointerId);   // 拖出画布不丢事件
    ptrs.set(e.pointerId, { x: e.clientX, y: e.clientY });
  });
  canvas.addEventListener('pointermove', (e) => {
    if (!ptrs.has(e.pointerId)) return;
    const prev = ptrs.get(e.pointerId);
    ptrs.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (ptrs.size === 1) {                   // 单指/鼠标拖拽平移
      view.ox += e.clientX - prev.x;
      view.oy += e.clientY - prev.y;
      redraw();
    }
  });
  const lift = (e) => ptrs.delete(e.pointerId);
  canvas.addEventListener('pointerup', lift);
  canvas.addEventListener('pointercancel', lift);
  canvas.addEventListener('dblclick', fit);  // 双击复位
  const ro = new ResizeObserver(resize);     // 容器尺寸/跨屏 dpr 变化时重建位图
  ro.observe(canvas);
  img.onload = resize;

  return {
    load: (url) => { img.src = url; },
    fit, zoomStep,
    destroy: () => {                         // 台面切换时断开观察器并退出同步组
      ro.disconnect();
      const k = view.peers.indexOf(draw);
      if (k >= 0) view.peers.splice(k, 1);
    },
  };
}

/* ---------------- 灯箱 ---------------- */

/* 全局至多一个灯箱。mode:view(普通)| pick(选对比图)| compare(对比)。
   cmpMode:swipe(卷帘)| blend(混合)| flicker(闪烁)| side(并排同步)。 */
let lb = null;

function openLightbox(items, idx, returnFocus) {
  closeLightbox();

  const stage = h('div', { class: 'glx-lb-stage' });
  const tools = h('div', { class: 'glx-lb-tools' });
  const nameEl = h('span', { class: 'glx-lb-name' });
  const stepEl = h('span', { class: 'glx-lb-step' });
  const metaEl = h('span', { class: 'glx-lb-metaline' });
  const hintEl = h('span', { class: 'glx-lb-hint' });
  const countEl = h('span', { class: 'glx-lb-count' });

  const prev = items.length > 1 ? h('button', {
    class: 'glx-lb-nav glx-lb-prev', type: 'button', 'aria-label': '上一张(←)',
    onclick: () => show(lb.idx - 1),
  }, '‹') : null;
  const next = items.length > 1 ? h('button', {
    class: 'glx-lb-nav glx-lb-next', type: 'button', 'aria-label': '下一张(→)',
    onclick: () => show(lb.idx + 1),
  }, '›') : null;
  const close = h('button', {
    class: 'glx-lb-close', type: 'button', 'aria-label': '关闭(Esc)',
    onclick: () => closeLightbox(),
  }, '×');

  // 胶片条:全部图件的缩略横排,当前图高亮,点击跳转/选对比图
  const strip = h('div', { class: 'glx-lb-strip', role: 'list', 'aria-label': '图件胶片条' });
  items.forEach((it, i) => {
    strip.appendChild(h('button', {
      class: 'glx-strip-item', type: 'button', role: 'listitem', title: it.name,
      'aria-label': `第 ${i + 1} 张:${it.name}`,
      onclick: () => onStripClick(i),
    }, it.demo ? it.node() : h('img', {
      src: it.thumbUrl, alt: '', loading: 'lazy', draggable: 'false',
    })));
  });

  const root = h('div', {
    class: 'glx-lb', role: 'dialog', 'aria-modal': 'true', 'aria-label': '图件大图查看',
    tabindex: '-1',
    // 点遮罩(而非内容):与 Esc 同语义 —— 逐层退出
    onclick: (e) => { if (e.target === root || e.target === stage) escapeStep(); },
    onkeydown: onKey,
  },
    close, prev, stage, next, tools,
    items.length > 1 ? strip : null,
    h('div', { class: 'glx-lb-bar' }, nameEl, stepEl, metaEl, hintEl, countEl));

  lb = {
    root, items, idx, returnFocus, stage, tools, strip, prev, next,
    nameEl, stepEl, metaEl, hintEl, countEl,
    mode: 'view', bIdx: -1, cmpMode: 'swipe',
    blend: 50, flickerMs: 800,
    viewers: [], flickerTimer: 0, overlayTop: null,
  };
  document.body.appendChild(root);
  show(idx);
  close.focus();
}

export function closeLightbox() {
  if (!lb) return;
  clearStage();   // 清闪烁定时器 + 断开 canvas 观察器
  lb.root.remove();
  if (lb.returnFocus?.isConnected) lb.returnFocus.focus?.();
  lb = null;
}

/* ---------------- 灯箱内部状态机 ---------------- */

function show(i) {
  const n = lb.items.length;
  lb.idx = ((i % n) + n) % n;   // 环形导航
  renderAll();
  // 预热相邻两张 browse 档,翻页零等待(经典 lightbox 预加载模式)
  for (const d of [1, -1]) {
    const it = lb.items[(((lb.idx + d) % n) + n) % n];
    if (it && !it.demo && it.url) new Image().src = it.url;
  }
}

/** Esc / 点遮罩:compare→view,pick→view,view→关闭。 */
function escapeStep() {
  if (lb.mode !== 'view') setMode('view');
  else closeLightbox();
}

function setMode(mode) {
  lb.mode = mode;
  if (mode !== 'compare') lb.bIdx = -1;
  renderAll();
}

function startCompare(bIdx) {
  lb.bIdx = bIdx;
  lb.mode = 'compare';
  const A = lb.items[lb.idx], B = lb.items[bIdx];
  if (lb.cmpMode === 'side' && (A.demo || B.demo)) lb.cmpMode = 'swipe';   // 并排仅限真实产物
  renderAll();
}

function onStripClick(i) {
  if (!lb) return;
  if (lb.mode === 'pick') {
    if (i !== lb.idx) startCompare(i);
    return;
  }
  if (lb.mode === 'compare') {
    if (i !== lb.idx && i !== lb.bIdx) {   // 换对比图 B,保持当前模式
      lb.bIdx = i;
      renderStage(); renderTools(); updateStrip(); updateBar();
    }
    return;
  }
  show(i);
}

function renderAll() {
  renderStage();
  renderTools();
  updateStrip();
  updateBar();
  updateNav();
}

/** 台面切换前的副作用清理:闪烁定时器、canvas 查看器(ResizeObserver)。 */
function clearStage() {
  if (lb.flickerTimer) { clearInterval(lb.flickerTimer); lb.flickerTimer = 0; }
  lb.overlayTop = null;
  lb.viewers.forEach((v) => v.destroy());
  lb.viewers = [];
}

function renderStage() {
  clearStage();
  const it = lb.items[lb.idx];
  if (lb.mode !== 'compare') {
    // view/pick 共用普通大图台面:真实产物走 canvas pan/zoom,演示 SVG 原样嵌入
    if (it.demo) {
      lb.stage.replaceChildren(h('div', { class: 'glx-demo-host' },
        it.node(), h('span', { class: 'glx-demo' }, '演示图件')));
    } else {
      const canvas = h('canvas', { class: 'glx-lb-canvas', 'aria-label': `大图:${it.name}` });
      lb.stage.replaceChildren(canvas);
      const v = attachViewer(canvas);
      lb.viewers.push(v);
      v.load(it.url);
    }
    return;
  }
  const A = lb.items[lb.idx], B = lb.items[lb.bIdx];
  lb.stage.replaceChildren(lb.cmpMode === 'side' ? buildSide(A, B) : buildOverlay(A, B));
}

/** 对比图层:真实产物用 browse 档 <img>,演示图件复用其 SVG 节点。 */
function cmpLayer(it) {
  return it.demo
    ? h('div', { class: 'glx-cmp-node' }, it.node())
    : h('img', { class: 'glx-cmp-img', src: it.url, alt: it.name, draggable: 'false' });
}

function chip(tag, it) {
  return h('span', { class: `glx-cmp-chip ${tag === 'A' ? 'a' : 'b'}` }, `${tag} · ${it.name}`);
}

/** 卷帘 / 混合 / 闪烁:两图同容器叠放,上层按模式裁剪或调透明度。 */
function buildOverlay(A, B) {
  const mode = lb.cmpMode;
  // 卷帘上层放 A(左侧露出当前图);混合/闪烁上层放 B(滑杆/定时器控其透明度)
  const topIt = mode === 'swipe' ? A : B;
  const botIt = mode === 'swipe' ? B : A;
  const topLayer = h('div', { class: 'glx-cmp-top' }, cmpLayer(topIt));
  const box = h('div', { class: `glx-cmp glx-cmp-${mode}` },
    h('div', { class: 'glx-cmp-layer' }, cmpLayer(botIt)),
    topLayer);
  lb.overlayTop = topLayer;

  if (mode === 'swipe') {
    // 透明 range 铺满容器承接拖拽/键盘/触屏(RESEARCH-raster-viewer-tech 骨架 C)
    box.style.setProperty('--pos', '50%');
    box.append(
      h('div', { class: 'glx-cmp-bar', 'aria-hidden': 'true' }),
      h('input', {
        class: 'glx-cmp-range', type: 'range', min: '0', max: '100', value: '50',
        'aria-label': '卷帘位置',
        oninput: (e) => box.style.setProperty('--pos', `${e.target.value}%`),
      }));
  } else if (mode === 'blend') {
    topLayer.style.opacity = String(lb.blend / 100);
  } else {
    startFlicker();
  }
  box.append(chip('A', A), chip('B', B));
  return box;
}

/** 闪烁定时器:定时交替上层透明度,间隔 lb.flickerMs 可调。 */
function startFlicker() {
  if (lb.flickerTimer) clearInterval(lb.flickerTimer);
  let on = true;
  if (lb.overlayTop) lb.overlayTop.style.opacity = '1';
  lb.flickerTimer = setInterval(() => {
    if (!lb || !lb.overlayTop) return;
    on = !on;
    lb.overlayTop.style.opacity = on ? '1' : '0';
  }, lb.flickerMs);
}

/** 并排同步:一份视图状态 {scale,ox,oy} 驱动两个画布(骨架 A 的共享改造)。 */
function buildSide(A, B) {
  const shared = { scale: 1, ox: 0, oy: 0, peers: [] };
  const cell = (tag, it) => {
    const canvas = h('canvas', { class: 'glx-lb-canvas', 'aria-label': `${tag}:${it.name}` });
    return { canvas, box: h('div', { class: 'glx-side-cell' }, canvas, chip(tag, it)) };
  };
  const a = cell('A', A), b = cell('B', B);
  const va = attachViewer(a.canvas, shared);
  const vb = attachViewer(b.canvas, shared);
  lb.viewers.push(va, vb);
  va.load(A.url);
  vb.load(B.url);
  return h('div', { class: 'glx-cmp-side' }, a.box, b.box);
}

/* ---------------- 灯箱工具条 / 状态条 / 胶片条 ---------------- */

function renderTools() {
  const kids = [];
  if (lb.mode !== 'compare') {
    const it = lb.items[lb.idx];
    kids.push(h('button', {
      class: 'glx-lb-btn', type: 'button',
      'aria-pressed': String(lb.mode === 'pick'),
      disabled: lb.items.length < 2 ? true : null,
      title: lb.items.length < 2 ? '至少需要两张图才能对比'
        : '与另一张图对比(卷帘 / 混合 / 闪烁 / 并排)',
      onclick: () => setMode(lb.mode === 'pick' ? 'view' : 'pick'),
    }, lb.mode === 'pick' ? '取消选择' : '对比'));
    if (!it.demo && it.fullUrl) {
      kids.push(h('a', {
        class: 'glx-lb-btn', href: it.fullUrl, target: '_blank', rel: 'noopener',
        title: '在新标签页打开原图(全分辨率)',
      }, '查看原图'));
    }
  } else {
    const A = lb.items[lb.idx], B = lb.items[lb.bIdx];
    const bothReal = !A.demo && !B.demo;
    const mk = (id, label, enabled = true, why = '') => h('button', {
      class: 'glx-lb-btn', type: 'button', 'aria-pressed': String(lb.cmpMode === id),
      disabled: enabled ? null : true, title: why || label,
      onclick: () => {
        if (lb.cmpMode === id) return;
        lb.cmpMode = id;
        renderStage(); renderTools(); updateBar();
      },
    }, label);
    kids.push(
      mk('swipe', '卷帘'), mk('blend', '混合'), mk('flicker', '闪烁'),
      mk('side', '并排', bothReal, bothReal ? '并排 + 同步缩放平移' : '演示图件不支持并排画布'));
    if (lb.cmpMode === 'blend') kids.push(blendSlider());
    if (lb.cmpMode === 'flicker') kids.push(flickerSlider());
    kids.push(h('button', {
      class: 'glx-lb-btn glx-lb-exit', type: 'button',
      onclick: () => setMode('view'),
    }, '退出对比(Esc)'));
  }
  lb.tools.replaceChildren(...kids);
}

function blendSlider() {
  const out = h('span', { class: 'glx-lb-sliderval' }, `${lb.blend}%`);
  return h('span', { class: 'glx-lb-slider' }, 'B 不透明度',
    h('input', {
      type: 'range', min: '0', max: '100', value: String(lb.blend),
      'aria-label': '对比图 B 不透明度(百分比)',
      oninput: (e) => {
        lb.blend = Number(e.target.value);
        out.textContent = `${lb.blend}%`;
        if (lb.overlayTop) lb.overlayTop.style.opacity = String(lb.blend / 100);
      },
    }), out);
}

function flickerSlider() {
  const out = h('span', { class: 'glx-lb-sliderval' }, `${lb.flickerMs}ms`);
  return h('span', { class: 'glx-lb-slider' }, '闪烁间隔',
    h('input', {
      type: 'range', min: '200', max: '2000', step: '100', value: String(lb.flickerMs),
      'aria-label': '闪烁间隔(毫秒)',
      oninput: (e) => {
        lb.flickerMs = Number(e.target.value);
        out.textContent = `${lb.flickerMs}ms`;
        startFlicker();   // 立即按新间隔重启
      },
    }), out);
}

function updateNav() {
  const viewing = lb.mode === 'view';
  if (lb.prev) lb.prev.style.display = viewing ? '' : 'none';
  if (lb.next) lb.next.style.display = viewing ? '' : 'none';
}

function updateStrip() {
  if (!lb.strip) return;
  lb.strip.classList.toggle('is-picking', lb.mode === 'pick');
  [...lb.strip.children].forEach((el, i) => {
    el.classList.toggle('is-cur', i === lb.idx);
    el.classList.toggle('is-b', lb.mode === 'compare' && i === lb.bIdx);
  });
  lb.strip.children[lb.idx]?.scrollIntoView?.({ block: 'nearest', inline: 'nearest' });
}

function updateBar() {
  const n = lb.items.length;
  const it = lb.items[lb.idx];
  if (lb.mode === 'compare') {
    const B = lb.items[lb.bIdx];
    lb.nameEl.textContent = `A ${it.name} ⇄ B ${B.name}`;
    lb.stepEl.textContent = {
      swipe: '卷帘:拖动分割线', blend: '混合:滑杆调 B 不透明度',
      flicker: '闪烁:定时交替,间隔可调', side: '并排:缩放平移两图同步',
    }[lb.cmpMode] || '';
    lb.metaEl.textContent = '';
    lb.hintEl.textContent = '点胶片条换对比图 B · Esc 退出对比';
  } else {
    lb.nameEl.textContent = it.name;
    lb.stepEl.textContent = `第 ${it.step} 步${it.date ? ` · ${it.date}` : ''}`;
    lb.metaEl.textContent = metaLine(it.meta);   // 无 sidecar 时为空,保持现状
    lb.hintEl.textContent = lb.mode === 'pick'
      ? '在胶片条选择第二张图(Esc 取消)'
      : (it.demo ? '' : '滚轮缩放 · 拖拽平移 · 双击复位 · +/− 缩放');
  }
  lb.countEl.textContent = n > 1 ? `${lb.idx + 1} / ${n}` : '';
}

/* ---------------- 键盘 ---------------- */

function onKey(e) {
  if (e.key === 'Escape') {
    e.preventDefault(); e.stopPropagation();
    escapeStep();
    return;
  }
  if (lb.mode === 'view') {
    if (e.key === 'ArrowLeft') { e.preventDefault(); show(lb.idx - 1); return; }
    if (e.key === 'ArrowRight') { e.preventDefault(); show(lb.idx + 1); return; }
  }
  // +/− 缩放:普通模式作用于当前画布,并排模式经共享状态同步两图
  if (e.key === '+' || e.key === '=') {
    if (lb.viewers.length) { e.preventDefault(); lb.viewers[0].zoomStep(1.25); }
    return;
  }
  if (e.key === '-' || e.key === '_') {
    if (lb.viewers.length) { e.preventDefault(); lb.viewers[0].zoomStep(0.8); }
    return;
  }
  if (e.key === 'Tab') {
    // 焦点陷阱:Tab 只在灯箱内可聚焦控件间循环
    const focusables = [...lb.root.querySelectorAll(
      'button:not([disabled]), a[href], input')];
    if (!focusables.length) return;
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    else if (!lb.root.contains(document.activeElement)) { e.preventDefault(); first.focus(); }
  }
}
