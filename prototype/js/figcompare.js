/* ============================================================
   figcompare.js —— 影像网格的双图对比(卷帘 swipe / 闪烁 blink / 并排)
   InSAR 判读刚需:对比解缠前后、不同参数产物、形变前后。

   所有权边界(不改 gallery.js / figures.js,自初始化且幂等):
   - 注入:MutationObserver 观察影像网格(.glx)出现,把「对比」开关
     幂等挂进网格工具条(h3.sect);网格重渲染后自动补挂、按图名回填
     已选 A/B 徽章;
   - 事件委托:document 捕获阶段统一接管 —— 开关点击、对比模式下的
     卡片点选(stopPropagation 抢在 gallery 灯箱的卡片 onclick 之前);
   - 数据:名称/步骤/元数据摘要直接读卡片 DOM(与网格同源);真实产物
     再拉一次 GET /api/figures(session/run_id 从缩略图 /api/artifact-file
     URL 反解)把缩略档升级为 browse 档;后端不可达/演示图件回落缩略图
     或克隆演示 SVG,三模式照常可用;
   - 大图性能:CSS transform 缩放(不重采样),transform-origin 0 0,
     屏幕坐标 = 图内坐标 × scale + (tx,ty);<img> decoding=async;
     缩放范围 SCALE_MIN–SCALE_MAX(0.25–8x,scale=1 即适配台面);
   - 可达性:对比视窗 role=dialog + aria-modal,Esc 关闭,Tab 焦点圈定,
     关闭后焦点归还触发卡片;prefers-reduced-motion 时 blink 自动播放
     禁用(空格键手动切换不受影响)。
   纯函数(缩放/平移数学、模式状态机、A/B 选择器)全部导出,
   由 prototype/figcompare.check.mjs 无浏览器直测。
   ============================================================ */
import { h } from './dom.js';

/* ================= 纯函数:缩放/平移数学 ================= */

export const SCALE_MIN = 0.25;
export const SCALE_MAX = 8;

export const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

/** 缩放夹紧;±Infinity 当极值自然夹紧,NaN 回落 1(绝不进 transform)。 */
export function clampScale(s) {
  return Number.isNaN(s) ? 1 : clamp(s, SCALE_MIN, SCALE_MAX);
}

/** 初始视图:scale=1 即「适配台面」,两图同底对齐的基准。 */
export function resetView() {
  return { scale: 1, tx: 0, ty: 0 };
}

/**
 * 以台面点 (px,py) 为锚缩放:锚点下的图内像素在缩放前后不动。
 * 推导:w = (p − t)/s 是锚点的图内坐标,令 w·s′ + t′ = p
 *   ⇒ t′ = p − (p − t)·(s′/s)。s′ 越界时按夹紧后的实际倍率换算,
 * 保证贴边缩放时锚点依然不漂移。
 */
export function zoomAt(view, px, py, factor) {
  const s2 = clampScale(view.scale * factor);
  const k = s2 / view.scale;
  return { scale: s2, tx: px - (px - view.tx) * k, ty: py - (py - view.ty) * k };
}

export function panBy(view, dx, dy) {
  return { scale: view.scale, tx: view.tx + dx, ty: view.ty + dy };
}

/** 视图状态 → CSS transform(translate 在前:screen = world·s + t)。 */
export function viewTransform(view) {
  return `translate(${view.tx}px, ${view.ty}px) scale(${view.scale})`;
}

/** 滚轮 delta 归一化(行/页模式换算像素)并夹紧,返回缩放倍率。 */
export function wheelFactor(deltaY, deltaMode = 0) {
  const unit = deltaMode === 1 ? 16 : deltaMode === 2 ? 100 : 1;
  const d = clamp(deltaY * unit, -24, 24);
  return Math.exp(-d * 0.012);
}

/* ================= 纯函数:A/B 选择器 ================= */

/**
 * 点选一张图的选择变换:
 *   已选 → 取消(若取消的是 A,原 B 自动补位为 A);
 *   不满两张 → 追加(首张为 A,次张为 B);
 *   已满两张 → 保留 A,新图换掉 B(快速迭代对比对象)。
 */
export function selToggle(sel, key) {
  const rest = sel.filter((k) => k !== key);
  if (rest.length !== sel.length) return rest;
  if (sel.length < 2) return [...sel, key];
  return [sel[0], key];
}

/** 交换 A/B;不足两张原样拷贝返回。 */
export function swapAB(pair) {
  return pair.length === 2 ? [pair[1], pair[0]] : [...pair];
}

/* ================= 纯函数:模式状态机 ================= */

export const MODES = ['swipe', 'blink', 'side'];
export const MODE_LABEL = { swipe: '卷帘', blink: '闪烁', side: '并排' };
export const HZ_MIN = 0.5;
export const HZ_MAX = 4;

export function createCompareState({ reduced = false } = {}) {
  return {
    mode: 'swipe',       // swipe 卷帘 | blink 闪烁 | side 并排同步
    pos: 50,             // 卷帘分割线位置(0–100,%)
    view: resetView(),   // 三模式共享缩放/平移:切模式后两图仍同底
    auto: false,         // blink 自动播放
    hz: 2,               // blink 自动频率(次/秒),判读经典 2Hz
    phase: 'A',          // blink 当前显示层
    reduced: !!reduced,  // prefers-reduced-motion:自动播放禁用
  };
}

/** 切换对比模式:非法/同模式原样返回;切换即相位归位、自动播放停。 */
export function setMode(st, mode) {
  if (!MODES.includes(mode) || mode === st.mode) return st;
  return { ...st, mode, phase: 'A', auto: false };
}

/** blink 自动播放开关;非 blink 模式与 reduced-motion 下永远保持关闭。 */
export function toggleAuto(st) {
  if (st.mode !== 'blink' || st.reduced) return st.auto ? { ...st, auto: false } : st;
  return { ...st, auto: !st.auto };
}

export function blinkTick(st) {
  return { ...st, phase: st.phase === 'A' ? 'B' : 'A' };
}

export function setHz(st, hz) {
  const v = Number(hz);
  return Number.isFinite(v) ? { ...st, hz: clamp(v, HZ_MIN, HZ_MAX) } : st;
}

/** 自动播放的层切换间隔:2Hz = 每 500ms 换一层。 */
export function blinkIntervalMs(hz) {
  const v = Number(hz);
  return Math.round(1000 / clamp(Number.isFinite(v) ? v : 2, HZ_MIN, HZ_MAX));
}

export function setPos(st, pos) {
  const v = Number(pos);
  return Number.isFinite(v) ? { ...st, pos: clamp(v, 0, 100) } : st;
}

/* ================= 纯函数:URL 反解与清单匹配 ================= */

/** 从缩略图 /api/artifact-file URL 反解 session/run_id(拉清单升级 browse 档用)。 */
export function parseArtifactQuery(src, base = 'http://localhost/') {
  try {
    const u = new URL(src, base);
    if (!u.pathname.endsWith('/api/artifact-file')) return null;
    const session = u.searchParams.get('session');
    if (!session) return null;
    return { session, runId: u.searchParams.get('run_id') || '' };
  } catch {
    return null;
  }
}

/** /api/figures 清单按文件名精确匹配(名称是网格与清单共同的稳定键)。 */
export function pickFigure(figures, name) {
  if (!Array.isArray(figures)) return null;
  return figures.find((f) => f && f.name === name) || null;
}

/* ============================================================
   以下为 DOM 侧(浏览器专用;bare node import 因 document 守卫不触达)
   ============================================================ */

const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

let booted = false;
let modeOn = false;   // 对比选择模式(全局:影像面板同一时刻只有一个网格)
let sel = [];         // 已选图名,[0]=A、[1]=B;以名字为键,网格重渲染后可回填
let overlay = null;   // 当前对比视窗,全局至多一个 { root, stop, returnFocus }

function cardName(card) {
  const el = card.querySelector('.glx-name');
  return (el && (el.getAttribute('title') || el.textContent)) || '';
}

function gridCards() {
  return $$('.glx .glx-card');
}

function findCard(name) {
  return gridCards().find((c) => cardName(c) === name) || null;
}

/* ---------------- 工具条注入(幂等)与选中态绘制 ---------------- */

/* 注意:MutationObserver 只观察 childList,本函数的 DOM 写入全部
   「先比对再写」,第二遍扫描必然零变更,不会自激振荡。 */
function ensureToolbar() {
  for (const glx of $$('.glx')) {
    const sect = glx.querySelector(':scope > .sect');
    if (!sect) continue;
    let btn = sect.querySelector('.fcp-toggle');
    if (!btn) {
      btn = h('button', { class: 'fcp-toggle', type: 'button', 'aria-pressed': 'false' }, '对比');
      btn.addEventListener('click', () => setSelecting(!modeOn));
      sect.append(btn, h('span', { class: 'fcp-status', role: 'status', 'aria-live': 'polite' }));
    }
    const enough = glx.querySelectorAll('.glx-card').length >= 2;
    btn.disabled = !enough && !modeOn;
    btn.title = btn.disabled ? '至少需要两张图才能对比'
      : modeOn ? '退出对比选择(已选徽章将清除)'
        : '双图对比:开启后点选两张图(A/B),自动进入卷帘/闪烁/并排视窗';
    const label = modeOn ? '退出对比' : '对比';
    if (btn.textContent !== label) btn.textContent = label;
    btn.setAttribute('aria-pressed', String(modeOn));
    glx.classList.toggle('fcp-selecting', modeOn);
  }
  paintSelection();
}

function paintSelection() {
  const cards = gridCards();
  if (cards.length) {
    // 网格换代(切 run/刷新)后,清单里已不存在的选择立即失效
    const names = new Set(cards.map(cardName));
    const kept = sel.filter((n) => names.has(n));
    if (kept.length !== sel.length) sel = kept;
  }
  for (const card of cards) {
    const i = sel.indexOf(cardName(card));
    card.classList.toggle('is-fcp-a', i === 0);
    card.classList.toggle('is-fcp-b', i === 1);
    let badge = card.querySelector('.fcp-badge');
    if (i >= 0) {
      if (!badge) {
        badge = h('span', { class: 'fcp-badge', 'aria-hidden': 'true' });
        (card.querySelector('.glx-thumb') || card).appendChild(badge);
      }
      const want = i === 0 ? 'A' : 'B';
      if (badge.textContent !== want) badge.textContent = want;
      badge.classList.toggle('is-b', i === 1);
    } else if (badge) {
      badge.remove();
    }
  }
  const text = !modeOn ? ''
    : sel.length === 0 ? '点选两张图(0/2)'
      : sel.length === 1 ? `已选 A:${sel[0]}(1/2)`
        : `A:${sel[0]} ⇄ B:${sel[1]}`;
  for (const s of $$('.fcp-status')) {
    if (s.textContent !== text) s.textContent = text;
  }
}

function setSelecting(on) {
  modeOn = on;
  if (!on) sel = [];
  ensureToolbar();
}

/* 捕获阶段委托:对比模式下抢在 gallery 卡片 onclick(开灯箱)之前接管点选。 */
function onDocClick(e) {
  const t = e.target && e.target.closest ? e.target : null;
  if (!t || !modeOn) return;
  const card = t.closest('.glx-card');
  if (!card || !t.closest('.glx')) return;
  e.preventDefault();
  e.stopPropagation();
  sel = selToggle(sel, cardName(card));
  paintSelection();
  if (sel.length === 2) openCompare(card);
}

/* ---------------- 卡片 → 对比条目(DOM 同源数据) ---------------- */

function itemFromCard(card) {
  const nameEl = card.querySelector('.glx-name');
  const img = card.querySelector('.glx-thumb img');
  return {
    name: cardName(card),
    label: (nameEl && nameEl.textContent) || '',
    sub: card.querySelector('.glx-sub')?.textContent || '',
    metaText: card.querySelector('.glx-meta2')?.textContent || '',
    demo: !!card.querySelector('.glx-demo'),
    src: img ? img.getAttribute('src') || '' : '',
    fullUrl: '',
    svgSrc: img ? null : card.querySelector('.glx-thumb svg'),  // 演示图件:按层克隆
  };
}

/** 真实产物经 /api/figures 把缩略档升级为 2048px browse 档;失败静默保持缩略档。 */
async function enrich(entries) {
  if (typeof location !== 'undefined' && location.protocol === 'file:') return;
  const seed = entries.find((it) => !it.demo && it.src);
  if (!seed) return;
  const q = parseArtifactQuery(seed.src, location.href);
  if (!q) return;
  try {
    const qs = new URLSearchParams({ session: q.session });
    if (q.runId) qs.set('run_id', q.runId);
    const resp = await fetch(`/api/figures?${qs}`);
    if (!resp.ok) return;
    const data = await resp.json();
    for (const it of entries) {
      const fig = pickFigure(data?.figures, it.name);
      if (fig) {
        it.src = fig.url || it.src;
        it.fullUrl = fig.fullUrl || '';
      }
    }
  } catch { /* 后端不可达:缩略档照常可对比,不打断 UI */ }
}

/* ---------------- 对比视窗 ---------------- */

function closeCompare() {
  if (!overlay) return;
  overlay.stop();
  overlay.root.remove();
  const rf = overlay.returnFocus;
  overlay = null;
  if (rf && rf.isConnected) rf.focus?.();
}

function openCompare(returnFocus) {
  closeCompare();
  const cards = sel.map(findCard);
  if (cards.some((c) => !c)) return;
  const entries = cards.map(itemFromCard);
  const reduced = typeof matchMedia === 'function'
    && matchMedia('(prefers-reduced-motion: reduce)').matches;
  let st = createCompareState({ reduced });

  /* --- 视窗内共享的可变引用 --- */
  const trs = [];        // 受 transform 驱动的图层内框
  const viewBoxes = [];  // 台面视口盒(缩放锚点坐标系)
  let topLayer = null;   // swipe 的裁剪层 / blink 的透明度层
  let timer = 0;         // blink 自动播放定时器

  const stage = h('div', { class: 'fcp-stage' });
  const bar = h('div', { class: 'fcp-bar' });
  const headInfo = h('div', { class: 'fcp-pair' });
  const zoomEl = h('span', { class: 'fcp-zoom' }, '100%');
  const hintEl = h('span', { class: 'fcp-hint' });
  const phaseEl = h('span', { class: 'fcp-phase', role: 'status', 'aria-live': 'polite' });
  const modeBtns = new Map();

  /* --- 视图应用:一份 {scale,tx,ty} 驱动全部图层(双图联动的根) --- */
  const applyView = () => {
    const t = viewTransform(st.view);
    for (const el of trs) el.style.transform = t;
    const pct = `${Math.round(st.view.scale * 100)}%`;
    if (zoomEl.textContent !== pct) zoomEl.textContent = pct;
  };

  function makeLayer(it) {
    const tr = h('div', { class: 'fcp-tr' });
    if (it.demo && it.svgSrc) {
      tr.appendChild(h('div', { class: 'fcp-svg' }, it.svgSrc.cloneNode(true)));
    } else {
      tr.appendChild(h('img', {
        class: 'fcp-img', src: it.src, alt: it.name,
        decoding: 'async', draggable: 'false',
      }));
    }
    trs.push(tr);
    return h('div', { class: 'fcp-layer' }, tr);
  }

  function chip(tag, it) {
    return h('span', {
      class: `fcp-chip is-${tag.toLowerCase()}`, dataset: { tag },
    }, `${tag} · ${it.name}`);
  }

  function applyPhase() {
    if (st.mode !== 'blink' || !topLayer) return;
    topLayer.style.opacity = st.phase === 'B' ? '1' : '0';
    const text = `当前显示:${st.phase}`;
    if (phaseEl.textContent !== text) phaseEl.textContent = text;
    for (const c of stage.querySelectorAll('.fcp-chip')) {
      c.classList.toggle('is-dim', c.dataset.tag !== st.phase);
    }
  }

  function stopAuto() {
    if (timer) { clearInterval(timer); timer = 0; }
  }
  function startAuto() {
    stopAuto();
    timer = setInterval(() => { st = blinkTick(st); applyPhase(); }, blinkIntervalMs(st.hz));
  }

  function clearStage() {
    stopAuto();
    trs.length = 0;
    viewBoxes.length = 0;
    topLayer = null;
  }

  function renderStage() {
    clearStage();
    const [A, B] = entries;
    if (st.mode === 'side') {
      const cell = (tag, it) => {
        const view = h('div', { class: 'fcp-view' }, makeLayer(it), chip(tag, it));
        viewBoxes.push(view);
        return view;
      };
      stage.replaceChildren(h('div', { class: 'fcp-box fcp-sidegrid' }, cell('A', A), cell('B', B)));
    } else {
      // 卷帘上层放 A(分割线左侧露 A);闪烁上层放 B(相位控其透明度)
      const bottom = makeLayer(st.mode === 'swipe' ? B : A);
      const top = makeLayer(st.mode === 'swipe' ? A : B);
      top.classList.add('fcp-top');
      topLayer = top;
      const view = h('div', { class: `fcp-view fcp-${st.mode}` },
        bottom, top, chip('A', A), chip('B', B));
      viewBoxes.push(view);
      if (st.mode === 'swipe') {
        view.style.setProperty('--fcp-pos', `${st.pos}%`);
        view.append(
          h('div', { class: 'fcp-cut', 'aria-hidden': 'true' }),
          // 透明 range 铺满台面:鼠标拖拽/触控滑动/键盘 ←→ 微调三合一
          h('input', {
            class: 'fcp-range', type: 'range', min: '0', max: '100', step: '1',
            value: String(st.pos), 'aria-label': '卷帘分割线位置(百分比,←/→ 微调)',
            oninput: (e) => {
              st = setPos(st, e.target.value);
              view.style.setProperty('--fcp-pos', `${st.pos}%`);
            },
          }));
      } else {
        applyPhase();
        if (st.auto) startAuto();
      }
      stage.replaceChildren(h('div', { class: 'fcp-box' }, view));
    }
    applyView();
    renderBar();
  }

  function renderBar() {
    const kids = [];
    if (st.mode === 'blink') {
      kids.push(h('button', {
        class: 'fcp-btn', type: 'button', 'aria-pressed': String(st.auto),
        disabled: st.reduced ? true : null,
        title: st.reduced
          ? '系统开启了「减少动态效果」,自动闪烁已停用(空格键仍可手动切换)'
          : '按设定频率自动交替 A/B(空格键随时手动切换)',
        onclick: () => {
          st = toggleAuto(st);
          if (st.auto) startAuto(); else stopAuto();
          renderBar();
        },
      }, st.auto ? '停止自动' : '自动播放'));
      const out = h('span', { class: 'fcp-val' }, `${st.hz} Hz`);
      kids.push(h('label', { class: 'fcp-slider' }, '频率',
        h('input', {
          type: 'range', min: String(HZ_MIN), max: String(HZ_MAX), step: '0.5',
          value: String(st.hz), disabled: st.reduced ? true : null,
          'aria-label': '自动闪烁频率(赫兹)',
          oninput: (e) => {
            st = setHz(st, e.target.value);
            out.textContent = `${st.hz} Hz`;
            if (st.auto) startAuto();   // 立即按新频率重启
          },
        }), out));
      kids.push(phaseEl);
    }
    hintEl.textContent = {
      swipe: '拖动分割线或 ←/→ 微调 · 滚轮缩放 · 双击复位',
      blink: '空格手动切换 A/B · 拖拽平移 · 滚轮缩放 · 双击复位',
      side: '滚轮缩放 · 拖拽平移 · 双图联动 · 双击复位',
    }[st.mode] || '';
    kids.push(hintEl, h('span', { class: 'fcp-grow' }),
      h('span', { class: 'fcp-zoomlab' }, '缩放 ', zoomEl));
    bar.replaceChildren(...kids);
  }

  function renderHead() {
    const side = (tag, it) => h('span', { class: 'fcp-item' },
      h('span', { class: `fcp-chip-h is-${tag.toLowerCase()}` }, tag),
      h('span', { class: 'fcp-name', title: it.name }, it.label || it.name),
      it.sub ? h('span', { class: 'fcp-sub' }, it.sub) : null,
      it.metaText ? h('span', { class: 'fcp-meta', title: it.metaText }, it.metaText) : null);
    headInfo.replaceChildren(
      side('A', entries[0]),
      h('button', {
        class: 'fcp-btn fcp-swap', type: 'button', 'aria-label': '交换 A/B',
        title: '交换 A/B(网格徽章与图层同时换位)', onclick: doSwap,
      }, '⇄ 交换'),
      side('B', entries[1]));
  }

  function doSwap() {
    entries.reverse();
    sel = swapAB(sel);
    paintSelection();          // 网格里的 A/B 徽章同步换位
    st = { ...st, phase: 'A' };
    renderHead();
    renderStage();
  }

  function selectMode(m) {
    const next = setMode(st, m);
    if (next === st) return;
    st = next;
    for (const [k, b] of modeBtns) b.setAttribute('aria-pressed', String(st.mode === k));
    renderStage();
  }

  /* --- 台面交互:滚轮缩放(锚点在光标)/ 拖拽平移 / 双击复位 --- */
  const boxUnder = (t) => (t && t.closest ? t.closest('.fcp-view') : null);

  stage.addEventListener('wheel', (e) => {
    const box = boxUnder(e.target) || viewBoxes[0];
    if (!box) return;
    e.preventDefault();   // 需 passive:false,阻止页面滚动
    const r = box.getBoundingClientRect();
    st = {
      ...st,
      view: zoomAt(st.view, e.clientX - r.left, e.clientY - r.top,
        wheelFactor(e.deltaY, e.deltaMode)),
    };
    applyView();
  }, { passive: false });

  const ptrs = new Map();
  stage.addEventListener('pointerdown', (e) => {
    if (st.mode === 'swipe' || e.button !== 0) return;   // 卷帘的拖拽属于分割线
    const box = boxUnder(e.target);
    if (!box) return;
    box.setPointerCapture(e.pointerId);   // 捕获在视口盒上:拖出不丢、click 不误伤遮罩
    ptrs.set(e.pointerId, { x: e.clientX, y: e.clientY });
  });
  stage.addEventListener('pointermove', (e) => {
    const prev = ptrs.get(e.pointerId);
    if (!prev) return;
    ptrs.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (ptrs.size === 1) {
      st = { ...st, view: panBy(st.view, e.clientX - prev.x, e.clientY - prev.y) };
      applyView();
    }
  });
  const lift = (e) => ptrs.delete(e.pointerId);
  stage.addEventListener('pointerup', lift);
  stage.addEventListener('pointercancel', lift);
  stage.addEventListener('dblclick', () => {
    st = { ...st, view: resetView() };
    applyView();
  });

  function zoomStep(f) {
    const box = viewBoxes[0];
    if (!box) return;
    const r = box.getBoundingClientRect();
    st = { ...st, view: zoomAt(st.view, r.width / 2, r.height / 2, f) };
    applyView();
  }

  function trapTab(e) {
    const items = $$('button:not([disabled]), input:not([disabled]), a[href]', root);
    if (!items.length) { e.preventDefault(); return; }
    const first = items[0];
    const last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    else if (!root.contains(document.activeElement)) { e.preventDefault(); first.focus(); }
  }

  function onKey(e) {
    if (e.key === 'Escape') {
      // 吞掉冒泡:app.js 的 document 级 Esc(停止 agent)不受对比视窗影响
      e.preventDefault();
      e.stopPropagation();
      closeCompare();
      return;
    }
    if (e.key === 'Tab') { trapTab(e); return; }
    const onCtl = /^(input|button|select|textarea|a)$/i.test(e.target.tagName || '');
    if (e.key === ' ' && st.mode === 'blink' && !onCtl) {
      e.preventDefault();
      st = blinkTick(st);
      applyPhase();
      return;
    }
    if (e.key === '+' || e.key === '=') { e.preventDefault(); zoomStep(1.25); return; }
    if (e.key === '-' || e.key === '_') { e.preventDefault(); zoomStep(0.8); return; }
    if (e.key === '0' && !onCtl) {
      st = { ...st, view: resetView() };
      applyView();
    }
  }

  const closeBtn = h('button', {
    class: 'fcp-btn fcp-close', type: 'button',
    'aria-label': '关闭对比(Esc)', onclick: () => closeCompare(),
  }, '关闭 Esc');

  const root = h('div', {
    class: 'fcp-ov', role: 'dialog', 'aria-modal': 'true', tabindex: '-1',
    'aria-label': `双图对比:A ${entries[0].name} 对 B ${entries[1].name}`,
    onkeydown: onKey,
    onclick: (e) => { if (e.target === root || e.target === stage) closeCompare(); },
  },
    h('div', { class: 'fcp-head' },
      headInfo,
      h('span', { class: 'fcp-grow' }),
      h('div', { class: 'fcp-modes', role: 'group', 'aria-label': '对比模式' },
        ...MODES.map((m) => {
          const b = h('button', {
            class: 'fcp-btn', type: 'button', 'aria-pressed': String(st.mode === m),
            onclick: () => selectMode(m),
          }, MODE_LABEL[m]);
          modeBtns.set(m, b);
          return b;
        })),
      closeBtn),
    stage, bar);

  document.body.appendChild(root);
  overlay = { root, stop: stopAuto, returnFocus: returnFocus || document.activeElement };
  renderHead();
  renderStage();   // 先用缩略档立即出图
  closeBtn.focus();
  enrich(entries).then(() => {
    // browse 档就绪后原位升级 src,不打断当前模式/缩放/相位
    if (!overlay || overlay.root !== root) return;
    for (const img of stage.querySelectorAll('img.fcp-img')) {
      const it = entries.find((x) => x.name === img.getAttribute('alt'));
      if (it && it.src && img.getAttribute('src') !== it.src) img.src = it.src;
    }
  });
}

/* ---------------- 自初始化(幂等) ---------------- */

export function initFigCompare() {
  if (booted || typeof document === 'undefined') return;
  booted = true;
  document.addEventListener('click', onDocClick, true);
  const boot = () => {
    if (typeof MutationObserver === 'function' && document.body) {
      new MutationObserver(ensureToolbar).observe(document.body, {
        childList: true, subtree: true,
      });
    }
    ensureToolbar();
  };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot, { once: true });
  } else {
    boot();
  }
}

if (typeof document !== 'undefined') initFigCompare();
