/* ============================================================
   空态 / 加载骨架 / 错误态 —— 全部面板共用的三个纯函数式构造器
   (样式在 css/states.css,只消费 tokens.css 设计令牌,明暗主题自适应)

   设计约束:
   - 零模块依赖:不 import dom.js/state.js,node 环境用最小 DOM stub
     即可直测(prototype/emptystate.check.mjs);
   - 不可信文本(title/hint/message)一律走 textContent,天然免 XSS;
   - 图标为内嵌极简 SVG(12 个,path 常量),不引任何外部资源;
   - 空态 action 按钮不直接调 app.js 函数:只 dispatch 自定义事件,
     由本模块的事件桥转发到既有入口按钮(#btnRun / #btnNew)——
     面板与装配层解耦,app.js 零改动。

   统一文案句式(各面板调用方遵守):
   - 空态:「还没有 X——做 Y 即可生成」;
   - 错误态必带「重试」。
   ============================================================ */

const SVG_NS = 'http://www.w3.org/2000/svg';

/* ---------------- 内联图标(≤12 个,极简线性风格,与 dom.js ICON 同美学) ---------------- */
const ICONS = {
  inbox:    ['M22 12h-6l-2 3h-4l-2-3H2', 'M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z'],
  image:    ['M3 3h18v18H3z', 'CIRCLE:8.5,8.5,1.5', 'M21 15l-5-5L5 21'],
  folder:   ['M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z'],
  terminal: ['M4 17l6-6-6-6', 'M12 19h8'],
  trace:    ['M22 12h-4l-3 9L9 3l-3 9H2'],
  shield:   ['M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z', 'M9 12l2 2 4-4'],
  doc:      ['M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z', 'M14 2v6h6', 'M16 13H8', 'M16 17H8'],
  target:   ['CIRCLE:12,12,10', 'CIRCLE:12,12,6', 'CIRCLE:12,12,2'],
  alert:    ['CIRCLE:12,12,10', 'M12 8v4', 'M12 16h.01'],
  refresh:  ['M23 4v6h-6', 'M1 20v-6h6', 'M3.51 9a9 9 0 0 1 14.85-3.36L23 10', 'M1 14l4.64 4.36A9 9 0 0 0 20.49 15'],
  play:     ['M6 3l14 9-14 9z'],
  chat:     ['M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z'],
};

/** 图标构造:path 常量 → <svg>(createElementNS,不走 innerHTML)。未知名回落 inbox。 */
export function esIcon(name) {
  const spec = ICONS[name] || ICONS.inbox;
  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('fill', 'none');
  svg.setAttribute('stroke', 'currentColor');
  svg.setAttribute('stroke-width', '2');
  svg.setAttribute('stroke-linecap', 'round');
  svg.setAttribute('stroke-linejoin', 'round');
  svg.setAttribute('aria-hidden', 'true');
  for (const d of spec) {
    const isC = d.startsWith('CIRCLE:');
    const p = document.createElementNS(SVG_NS, isC ? 'circle' : 'path');
    if (isC) {
      const [cx, cy, r] = d.slice(7).split(',');
      p.setAttribute('cx', cx); p.setAttribute('cy', cy); p.setAttribute('r', r);
    } else {
      p.setAttribute('d', d);
    }
    svg.appendChild(p);
  }
  return svg;
}

/* ---------------- 内部小工具(零依赖,不引 dom.js) ---------------- */

/** 创建元素并以 textContent 填入文本 —— title/hint/message 均不可信,禁止 innerHTML。 */
function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null && text !== '') n.textContent = String(text);
  return n;
}

/** container 为元素时替换其内容(空/载/错三态互斥,整块换);返回根节点供调用方内嵌。 */
function mount(container, node) {
  if (container && typeof container.replaceChildren === 'function') container.replaceChildren(node);
  return node;
}

/* ---------------- ① 空态 ---------------- */

/**
 * 空态卡:图标 + 标题 + 提示 + 可选动作按钮。
 * @param {Element|null} container 传入则 replaceChildren 挂载;null 只返回节点
 * @param {object} o { icon, title, hint, action }
 *   action = { label, event, detail? } → 点击 dispatch 自定义事件(经下方事件桥
 *            触达既有入口,不 import app.js);或 { label, onClick } 直接回调。
 */
export function renderEmpty(container, { icon = 'inbox', title = '', hint = '', action = null } = {}) {
  const root = el('div', 'es-empty');
  root.setAttribute('role', 'status');

  const ic = el('span', 'es-empty-ic');
  ic.appendChild(esIcon(icon));
  root.appendChild(ic);

  if (title) root.appendChild(el('h4', 'es-empty-title', title));
  if (hint) root.appendChild(el('p', 'es-empty-hint', hint));

  if (action && action.label) {
    const btn = el('button', 'es-btn es-empty-act');
    btn.setAttribute('type', 'button');
    btn.appendChild(esIcon(action.icon || 'play'));
    btn.appendChild(el('span', '', action.label));
    btn.addEventListener('click', () => {
      if (typeof action.onClick === 'function') { action.onClick(); return; }
      if (action.event) {
        document.dispatchEvent(new CustomEvent(action.event, { detail: action.detail ?? null }));
      }
    });
    root.appendChild(btn);
  }
  return mount(container, root);
}

/* ---------------- ② 加载骨架 ---------------- */

/** rows 边界:非数值/≤0 回落默认 3;上限 12(防调用方误传超大值把面板撑爆)。 */
export function clampRows(rows, fallback = 3) {
  const n = Math.floor(Number(rows));
  if (!Number.isFinite(n) || n <= 0) return fallback;
  return Math.min(12, n);
}

// 灰条宽度循环表:确定性(无随机),同参数输出结构永远一致(可测)
const BAR_W = ['92%', '64%', '83%', '75%', '88%', '58%'];
// tree 变体的缩进循环:模拟「组标题 → 子行」的层级轮廓
const TREE_IND = [0, 14, 14, 28, 14, 28];

/**
 * 骨架屏:kind ∈ list | grid | tree | text(未知回落 list)。
 * 微光动画在 states.css;prefers-reduced-motion 时降级为静态灰条。
 */
export function renderSkeleton(container, { kind = 'list', rows = 3, label = '' } = {}) {
  const k = ['list', 'grid', 'tree', 'text'].includes(kind) ? kind : 'list';
  const n = clampRows(rows);
  const root = el('div', `es-skel es-skel-${k}`);
  root.setAttribute('aria-busy', 'true');
  root.setAttribute('aria-label', label || '内容加载中');

  const bar = (w, indent = 0) => {
    const b = el('span', 'es-bar');
    b.setAttribute('aria-hidden', 'true');
    b.style.width = w;
    if (indent) b.style.marginLeft = `${indent}px`;
    return b;
  };

  if (k === 'grid') {
    for (let i = 0; i < n; i++) {
      const cell = el('div', 'es-cell');
      const thumb = el('span', 'es-thumb');
      thumb.setAttribute('aria-hidden', 'true');
      cell.appendChild(thumb);
      cell.appendChild(bar(BAR_W[i % BAR_W.length]));
      cell.appendChild(bar(BAR_W[(i + 3) % BAR_W.length]));
      root.appendChild(cell);
    }
  } else {
    for (let i = 0; i < n; i++) {
      const indent = k === 'tree' ? TREE_IND[i % TREE_IND.length] : 0;
      // text 变体末行收短,更像自然段落轮廓
      const w = (k === 'text' && i === n - 1) ? '46%' : BAR_W[i % BAR_W.length];
      root.appendChild(bar(w, indent));
    }
  }
  return mount(container, root);
}

/* ---------------- ③ 错误态 ---------------- */

/**
 * 错误条:告警图标 + 消息 + 「重试」按钮(统一文案要求:错误态必带重试;
 * retry 缺省时按钮不渲染,仅供构造器健壮性,面板调用方必须传)。
 */
export function renderError(container, { message = '读取失败——后端不可达或响应异常。', retry = null } = {}) {
  const root = el('div', 'es-error');
  root.setAttribute('role', 'alert');
  root.appendChild(esIcon('alert'));
  root.appendChild(el('span', 'es-msg', message));

  if (typeof retry === 'function') {
    const btn = el('button', 'es-btn es-retry');
    btn.setAttribute('type', 'button');
    btn.setAttribute('aria-label', '重试');
    btn.appendChild(esIcon('refresh'));
    btn.appendChild(el('span', '', '重试'));
    btn.addEventListener('click', () => retry());
    root.appendChild(btn);
  }
  return mount(container, root);
}

/* ============================================================
   事件桥:空态 action 的自定义事件 → 既有入口按钮
   面板模块只 dispatch 事件名,唯一的 DOM 耦合点收敛在这一张表里;
   入口按钮不存在/隐藏时点击是无害空操作(app.js 的可见性逻辑不变)。
   ============================================================ */
const ACTION_BRIDGE = {
  'states:run-pipeline': 'btnRun',   // 顶栏「运行流水线/重跑失效步骤」
  'states:new-session': 'btnNew',    // 侧栏「新建会话」
};

/* node 测试的最小 DOM stub 未必实现 document.addEventListener:
   桥接不可用时静默跳过(构造器本身不受影响,按钮点击成为无害空操作)。 */
if (typeof document !== 'undefined' && typeof document.addEventListener === 'function') {
  for (const [ev, id] of Object.entries(ACTION_BRIDGE)) {
    document.addEventListener(ev, () => {
      const btn = document.getElementById(id);
      if (btn && typeof btn.click === 'function') btn.click();
    });
  }
}

/* ============================================================
   会话列表空态守望(index.html 一行引导接入,app.js 零改动):
   #sessions 被 renderSessions 全量重绘,列表真空(无会话行也无分组头)
   时补一张空态卡;真实行出现后的下一次重绘自然把卡片冲掉。
   ============================================================ */
export function watchSessionsEmpty() {
  const host = document.getElementById('sessions');
  if (!host || typeof MutationObserver === 'undefined') return;

  const paint = () => {
    if (host.querySelector('.sess, .sess-group')) return;   // 有真实内容:不干预
    if (host.querySelector('.es-empty')) return;            // 已画过:防观察器自触发循环
    renderEmpty(host, {
      icon: 'chat',
      title: '还没有会话',
      hint: '点击「新建会话」即可开始——也可以直接在下方输入框描述研究任务。',
      action: { label: '新建会话', event: 'states:new-session', icon: 'chat' },
    });
  };
  new MutationObserver(paint).observe(host, { childList: true });
  paint();
}
