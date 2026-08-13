/* ============================================================
   首访引导（onboarding tour）—— 自包含 ES Module，零依赖
   ------------------------------------------------------------
   回答新用户的三个困惑：聊天框是什么、流水线是什么、两者怎么配合。
   五步聚光灯：四矩形遮罩挖洞高亮目标元素 + 旁侧讲解卡片。

   触发：
   - 主界面首次可见时自动播放（localStorage 记忆已看，key 带版本号；
     文案大改时递增 TOUR_VERSION，老用户会重新看到一次）；
   - 环境向导 setup.js 在场时绝不抢时机：复用其幂等入口判断，
     向导渲染了就等它从 DOM 移除后再起；
   - 左侧导航条「?」按钮（#btnTour）随时重看。

   可达性：role=dialog + aria-modal，Tab 焦点圈定在卡片内，
   ←/→ 前进后退、Esc 关闭；关闭后焦点归还触发元素。
   目标元素不存在（dock 收起、面板未渲染等）时优雅跳过该步。
   窗口 resize / 页面滚动时重新定位遮罩与卡片。

   集成（index.html 共 3 行）：
     <link rel="stylesheet" href="css/tour.css">
     <button class="rail-btn" id="btnTour" …>?</button>
     <script type="module" src="js/tour.js"></script>
   样式：css/tour.css（只依赖 tokens.css 设计令牌；动效由
   tokens.css 的 prefers-reduced-motion 全局降级）。
   约定：不修改 app.js / setup.js / dock.js —— 切换 dock 标签
   一律走真实按钮的 click()，与用户手点完全等价。
   ============================================================ */

/* ---------------- 已看记忆（key 带版本号） ---------------- */

export const TOUR_VERSION = 1;

/** 版本 v 对应的 localStorage key：升级 TOUR_VERSION 即换 key，自然重新展示。 */
export function seenKeyFor(version = TOUR_VERSION) {
  return `ia-tour-seen-v${version}`;
}

export const SEEN_KEY = seenKeyFor();

/** 当前版本未看过 → 需要自动播放。storage 异常（隐私模式等）按「已看」处理，不骚扰。 */
export function shouldAutoShow(storage, version = TOUR_VERSION) {
  try {
    return !storage.getItem(seenKeyFor(version));
  } catch {
    return false;
  }
}

/** 记住「当前版本已看」。写入失败静默：代价只是下次再播一遍。 */
export function markSeen(storage, version = TOUR_VERSION) {
  try {
    (storage || localStorage).setItem(seenKeyFor(version), new Date().toISOString());
  } catch { /* 忽略 */ }
}

/* ---------------- 五步文案与目标 ----------------
   sel 是候选选择器列表：从前往后取第一个「有可见命中」的，支持逗号
   并集（如影像+审计两个标签一起圈）。tab 声明该步依赖的 dock 标签：
   进入该步前先点对应标签按钮（等价用户手点），标签不可见则整步跳过。 */

export const STEPS = [
  {
    id: 'chat',
    tab: null,
    sel: ['.composer .box', '.composer-in', '#prompt'],
    title: '聊天框：用研究语言布置任务',
    body: '不用写命令或代码——像给同事布置研究一样，说清研究区、时间段和想要的分析即可。'
      + 'Agent 会把它翻译成一份完整的处理方案，并在执行前请你确认。',
    eg: '分析玉树冻土 2020-2023 的 SBAS 时序形变',
  },
  {
    id: 'pipeline',
    tab: 'pipeline',
    sel: ['#pane-pipeline', '#dock'],
    title: '流水线：计划与执行的可核查状态',
    body: '聊天里说的是你的研究意图；这里是意图落地后的执行清单——每一步用什么方法、'
      + '什么参数、产出什么证据，都可查、可改。科学结论要求过程可复现，所以计划不能'
      + '只留在对话里，得是一份能逐条核对的状态。两者不冲突，而是分工：你说清目标，'
      + '流水线让过程经得起检查。',
  },
  {
    id: 'intervene',
    tab: 'pipeline',
    sel: ['#pane-pipeline .pdetail', '#pane-pipeline'],
    title: '干预：随时可改，不必重来',
    body: '选中流水线里的任意一步，就能改参数、换方法，运行中还可以暂停。'
      + '你的每次改动都会同步出现在聊天回执里，受影响的后续步骤会自动标记为待重算。'
      + '不满意 Agent 的选择时直接改，不必重新组织一轮对话。',
  },
  {
    id: 'evidence',
    tab: 'images',
    sel: ['#tab-images, #tab-audit'],
    title: '影像与审计：看结果，也看证据',
    body: '「影像」面板查看形变图与时序曲线；「审计」面板追溯每个数字的来源——'
      + '方法、参数、指纹与证据级别。写进论文之前，先在这里核对证据链。',
  },
  {
    id: 'env',
    tab: 'env',
    sel: ['#tab-env'],
    title: '环境与帮助：遇到问题先来这里',
    body: '「环境」面板列出引擎、磁盘等运行条件，任务跑不起来时先看这里。'
      + '引导到此结束；想再看一遍，点左下角导航条的「?」按钮。',
  },
];

/* ---------------- DOM 小工具（自包含，不 import dom.js） ---------------- */

function h(tag, props, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null) continue;
    if (k === 'class') el.className = v;
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, String(v));
  }
  for (const c of children.flat(Infinity)) {
    if (c == null || c === false || c === true) continue;
    el.append(typeof c === 'object' && c.nodeType ? c : String(c));
  }
  return el;
}

/** 可见性：存在、未被 hidden、且有几何尺寸（display:none 祖先 → rect 全 0）。 */
function isVisible(el) {
  if (!el || el.hidden) return false;
  const r = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
  return !!r && r.width > 0 && r.height > 0;
}

function visibleMatches(sel, doc = document) {
  return [...doc.querySelectorAll(sel)].filter(isVisible);
}

/** 该步当前可用吗：声明的 dock 标签可见（点它即可露出目标），或选择器已有可见命中。 */
function stepAvailable(step, doc = document) {
  if (step.tab && isVisible(doc.getElementById(`tab-${step.tab}`))) return true;
  return step.sel.some((sel) => visibleMatches(sel, doc).length > 0);
}

/** 起播时的可用步骤清单（dock 收起等场景自动缩短为可用子集）。 */
export function availableSteps(doc = document) {
  return STEPS.filter((s) => stepAvailable(s, doc));
}

/** 目标解析：候选选择器依次尝试，返回第一组可见命中（可能多个 → 联合外接矩形）。 */
function resolveEls(step, doc = document) {
  for (const sel of step.sel) {
    const els = visibleMatches(sel, doc);
    if (els.length) return els;
  }
  return [];
}

/* ---------------- 几何 ---------------- */

function unionRect(rects) {
  return {
    left: Math.min(...rects.map((r) => r.left)),
    top: Math.min(...rects.map((r) => r.top)),
    right: Math.max(...rects.map((r) => r.right)),
    bottom: Math.max(...rects.map((r) => r.bottom)),
  };
}

function place(el, x, y, w, hgt) {
  el.style.left = `${Math.round(x)}px`;
  el.style.top = `${Math.round(y)}px`;
  el.style.width = `${Math.max(0, Math.round(w))}px`;
  el.style.height = `${Math.max(0, Math.round(hgt))}px`;
}

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

/* ---------------- 运行时（单例，幂等） ---------------- */

let ui = null;          // 打开期间的全部运行时引用；关闭后归 null
let rafPending = false;

export function isOpen() {
  return !!ui;
}

/** 当前选中的 dock 标签按钮（起播前记住，结束后归位）。 */
function selectedTabBtn() {
  const tabs = document.getElementById('dockTabs');
  if (!tabs) return null;
  return [...tabs.querySelectorAll('button')]
    .find((b) => b.getAttribute('aria-selected') === 'true') || null;
}

/** 进入某步前的准备：目标藏在别的 dock 标签后面时，替用户点开（等价手点）。 */
function prep(step) {
  if (!step.tab) return;
  const btn = document.getElementById(`tab-${step.tab}`);
  if (btn && isVisible(btn) && btn.getAttribute('aria-selected') !== 'true') {
    btn.click();
    ui.tabTouched = true;
  }
}

/** 从 from 沿 dir 找下一个此刻仍可用的步（中途 dock 被收起等变化在此兜住）。 */
function scan(from, dir) {
  for (let i = from; i >= 0 && i < ui.steps.length; i += dir) {
    if (stepAvailable(ui.steps[i])) return i;
  }
  return -1;
}

/* ---------------- 布局：四矩形遮罩 + 洞 + 卡片 ---------------- */

function layout() {
  if (!ui) return;
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const { masks, hole, card } = ui;
  const els = resolveEls(ui.steps[ui.idx]);

  let r = null;
  if (els.length) {
    const pad = 8;   // 洞比目标外扩一圈，高亮圈不贴边
    const u = unionRect(els.map((el) => el.getBoundingClientRect()));
    r = {
      left: clamp(u.left - pad, 0, vw),
      top: clamp(u.top - pad, 0, vh),
      right: clamp(u.right + pad, 0, vw),
      bottom: clamp(u.bottom + pad, 0, vh),
    };
    if (r.right - r.left <= 0 || r.bottom - r.top <= 0) r = null;
  }

  if (r) {
    place(masks[0], 0, 0, vw, r.top);                                   // 上
    place(masks[1], 0, r.top, r.left, r.bottom - r.top);                // 左
    place(masks[2], r.right, r.top, vw - r.right, r.bottom - r.top);    // 右
    place(masks[3], 0, r.bottom, vw, vh - r.bottom);                    // 下
    place(hole, r.left, r.top, r.right - r.left, r.bottom - r.top);
    hole.hidden = false;
  } else {
    // 目标此刻解析不到（极端竞态）：整屏遮罩 + 居中卡片，仍可读文案与前进
    place(masks[0], 0, 0, vw, vh);
    for (const m of [masks[1], masks[2], masks[3]]) place(m, 0, 0, 0, 0);
    hole.hidden = true;
  }

  // 卡片放置：右 → 左 → 下 → 上 → 居中，取第一个塞得下的方位
  const cw = card.offsetWidth || 340;
  const ch = card.offsetHeight || 220;
  const gap = 14;
  const edge = 12;
  let x;
  let y;
  if (r && r.right + gap + cw <= vw - edge) {
    x = r.right + gap;
    y = clamp(r.top, edge, vh - ch - edge);
  } else if (r && r.left - gap - cw >= edge) {
    x = r.left - gap - cw;
    y = clamp(r.top, edge, vh - ch - edge);
  } else if (r && r.bottom + gap + ch <= vh - edge) {
    x = clamp(r.left, edge, vw - cw - edge);
    y = r.bottom + gap;
  } else if (r && r.top - gap - ch >= edge) {
    x = clamp(r.left, edge, vw - cw - edge);
    y = r.top - gap - ch;
  } else {
    x = (vw - cw) / 2;
    y = (vh - ch) / 2;
  }
  card.style.left = `${Math.round(clamp(x, edge, Math.max(edge, vw - cw - edge)))}px`;
  card.style.top = `${Math.round(clamp(y, edge, Math.max(edge, vh - ch - edge)))}px`;
}

/** resize / 滚动的重排：rAF 合并，一帧至多一次。 */
function onReflow() {
  if (rafPending || !ui) return;
  rafPending = true;
  requestAnimationFrame(() => {
    rafPending = false;
    if (ui) layout();
  });
}

/* ---------------- 步骤渲染 ---------------- */

function show(i) {
  if (!ui) return;
  ui.idx = i;
  const step = ui.steps[i];
  prep(step);

  // 目标滚入视口后再量几何（面板内容可能在折叠区）
  const els = resolveEls(step);
  els[0]?.scrollIntoView?.({ block: 'nearest' });

  ui.titleEl.textContent = step.title;
  ui.bodyEl.textContent = step.body;
  ui.egEl.textContent = step.eg || '';
  ui.egEl.hidden = !step.eg;
  ui.stepEl.textContent = `第 ${i + 1} 步 · 共 ${ui.steps.length} 步`;
  ui.dotsEl.replaceChildren(...ui.steps.map((_, k) => h('i', { class: k === i ? 'on' : '' })));

  const noPrev = scan(i - 1, -1) === -1;
  const last = scan(i + 1, +1) === -1;
  ui.btnPrev.disabled = noPrev;
  ui.btnNext.textContent = last ? '完成' : '下一步';
  ui.card.setAttribute('aria-label', `界面引导 第 ${i + 1} 步（共 ${ui.steps.length} 步）`);

  // 焦点始终留在卡片内；上一步刚被禁用时移交给主按钮
  if (!ui.card.contains(document.activeElement) || (ui.btnPrev.disabled && document.activeElement === ui.btnPrev)) {
    ui.btnNext.focus();
  }
  requestAnimationFrame(() => layout());
  layout();
}

export function next() {
  if (!ui) return;
  const j = scan(ui.idx + 1, +1);
  if (j === -1) finish();
  else show(j);
}

export function prev() {
  if (!ui) return;
  const j = scan(ui.idx - 1, -1);
  if (j !== -1) show(j);
}

/** 完成（走完全程）与关闭（跳过/Esc）都记「已看」——重看入口是「?」按钮。 */
export function finish() {
  if (!ui) return;
  markSeen(localStorage);
  teardown();
}

export function closeTour() {
  if (!ui) return;
  markSeen(localStorage);
  teardown();
}

/* ---------------- 键盘：←/→ 前进后退，Esc 关闭，Tab 圈定 ---------------- */

function focusables() {
  return [ui.btnX, ui.btnSkip, ui.btnPrev, ui.btnNext].filter((b) => !b.disabled);
}

function trapTab(e) {
  const items = focusables();
  if (!items.length) { e.preventDefault(); return; }
  const first = items[0];
  const last = items[items.length - 1];
  const active = document.activeElement;
  if (!ui.card.contains(active)) {
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

function onKeydown(e) {
  if (!ui || e.isComposing) return;
  // 更高层浮层（快捷键帮助 / 命令面板）打开时让位，避免一个 Esc 关两层
  const kbd = document.getElementById('kbdHelp');
  if (kbd && !kbd.hidden) return;
  const cmdk = document.getElementById('cmdk');
  if (cmdk && !cmdk.hidden) return;

  if (e.key === 'Escape') {
    e.preventDefault();
    e.stopPropagation();
    closeTour();
  } else if (e.key === 'ArrowRight') {
    e.preventDefault();
    e.stopPropagation();
    next();
  } else if (e.key === 'ArrowLeft') {
    e.preventDefault();
    e.stopPropagation();
    prev();
  } else if (e.key === 'Tab') {
    trapTab(e);
  }
}

/* ---------------- 打开 / 关闭 ---------------- */

/**
 * 启动引导。幂等：已打开时直接返回现有控制器。
 * 目标全部不可用（极窄屏等）时返回 null，不渲染任何 DOM。
 */
export function startTour() {
  if (ui) return ui.ctl;
  const steps = availableSteps();
  if (!steps.length) return null;

  const masks = [0, 1, 2, 3].map(() => h('div', { class: 'tour-mask', 'aria-hidden': 'true' }));
  const hole = h('div', { class: 'tour-hole', 'aria-hidden': 'true' });

  const stepEl = h('span', { class: 'tour-step' });
  const btnX = h('button', { class: 'tour-x', type: 'button', 'aria-label': '关闭引导（Esc）', onclick: () => closeTour() }, '✕');
  const titleEl = h('h2', { class: 'tour-title', id: 'tourTitle' });
  const bodyEl = h('p', { class: 'tour-body', id: 'tourBody' });
  const egEl = h('div', { class: 'tour-eg' });
  const dotsEl = h('div', { class: 'tour-dots', 'aria-hidden': 'true' });
  const btnSkip = h('button', { class: 'tour-skip', type: 'button', onclick: () => closeTour() }, '跳过引导');
  const btnPrev = h('button', {
    class: 'tour-btn', type: 'button', 'aria-keyshortcuts': 'ArrowLeft', onclick: () => prev(),
  }, '上一步');
  const btnNext = h('button', {
    class: 'tour-btn is-pri', type: 'button', 'aria-keyshortcuts': 'ArrowRight', onclick: () => next(),
  }, '下一步');

  const card = h('div', {
    class: 'tour-card', role: 'dialog', 'aria-modal': 'true',
    'aria-labelledby': 'tourTitle', 'aria-describedby': 'tourBody', tabindex: '-1',
  },
    h('div', { class: 'tour-top' }, stepEl, btnX),
    titleEl, bodyEl, egEl, dotsEl,
    h('div', { class: 'tour-acts' }, btnSkip, h('span', { class: 'tour-grow' }), btnPrev, btnNext));

  const root = h('div', { class: 'tour', id: 'tourOverlay' }, ...masks, hole, card);

  ui = {
    steps,
    idx: 0,
    masks,
    hole,
    card,
    stepEl,
    titleEl,
    bodyEl,
    egEl,
    dotsEl,
    btnX,
    btnSkip,
    btnPrev,
    btnNext,
    tabTouched: false,
    startTabBtn: selectedTabBtn(),
    returnFocus: document.activeElement,
    ctl: { next, prev, close: closeTour, finish },
  };

  document.body.appendChild(root);
  ui.root = root;
  window.addEventListener('keydown', onKeydown, true);
  window.addEventListener('resize', onReflow);
  window.addEventListener('scroll', onReflow, true);
  show(0);
  ui.btnNext.focus();
  return ui.ctl;
}

function teardown() {
  const u = ui;
  ui = null;
  window.removeEventListener('keydown', onKeydown, true);
  window.removeEventListener('resize', onReflow);
  window.removeEventListener('scroll', onReflow, true);
  // 引导替用户切过 dock 标签的话，归还到起播前选中的那个
  if (u.tabTouched && u.startTabBtn && isVisible(u.startTabBtn)
      && u.startTabBtn.getAttribute('aria-selected') !== 'true') {
    u.startTabBtn.click();
  }
  u.root.remove();
  u.returnFocus?.focus?.();
}

/* ---------------- 触发：自动播放 + 「?」按钮 ---------------- */

/** 环境向导（#setupWizard）从 DOM 移除后 resolve。 */
function wizardGone() {
  return new Promise((resolve) => {
    const gone = () => !document.getElementById('setupWizard');
    if (gone()) { resolve(); return; }
    if (typeof MutationObserver === 'function') {
      const mo = new MutationObserver(() => {
        if (gone()) { mo.disconnect(); resolve(); }
      });
      mo.observe(document.body, { childList: true });
    } else {
      const t = setInterval(() => {
        if (gone()) { clearInterval(t); resolve(); }
      }, 250);
    }
  });
}

/**
 * 主界面首次可见时自动播放。与环境向导的协调复用 setup.js 的幂等入口
 * maybeShowSetupWizard()（返回是否渲染了向导），不自己猜后端状态：
 * 向导在场 → 等它移除；没有向导 → 立即开始。返回是否真的播了。
 */
export async function autoStart() {
  if (!shouldAutoShow(localStorage)) return false;
  if (document.readyState === 'loading') {
    await new Promise((r) => document.addEventListener('DOMContentLoaded', r, { once: true }));
  }
  let wizard = false;
  try {
    const m = await import('./setup.js');
    wizard = await m.maybeShowSetupWizard();
  } catch {
    wizard = !!document.querySelector('.setup-scrim');   // 导入失败的兜底：直接看 DOM
  }
  if (wizard) await wizardGone();
  if (!shouldAutoShow(localStorage) || isOpen()) return false;   // 等待期间已看过/已手动打开
  return !!startTour();
}

/** 「?」按钮：无视已看标记随时重看。 */
function wireButton() {
  const btn = document.getElementById('btnTour');
  if (!btn || btn.dataset.tourWired) return;
  btn.dataset.tourWired = '1';
  btn.addEventListener('click', () => {
    if (!isOpen()) startTour();
  });
}

let inited = false;

/** 自初始化入口（幂等）：接线「?」按钮 + 调度自动播放（失败安全降级，不冒错）。 */
export function initTour() {
  if (inited || typeof document === 'undefined') return;
  inited = true;
  wireButton();
  autoStart().catch(() => {});
}

// node 环境（单元测试 import 纯函数）没有 document，安全跳过初始化
if (typeof document !== 'undefined') initTour();
