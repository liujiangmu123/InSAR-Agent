/* ============================================================
   安全 DOM 构建
   规则：一律走 h() / txt()，不用 innerHTML 拼接不可信数据。
   SVG 图件由 figures.js 自己生成（可信来源），单独走 rawSvg()。
   ============================================================ */

/** 创建元素。props 里 class/dataset/aria/on* 都支持；children 可嵌套数组。 */
export function h(tag, props = null, ...children) {
  const node = document.createElement(tag);
  if (props) {
    for (const [k, v] of Object.entries(props)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === 'class') node.className = v;
      else if (k === 'style' && typeof v === 'object') Object.assign(node.style, v);
      else if (k === 'dataset') for (const [dk, dv] of Object.entries(v)) {
        if (dv !== null && dv !== undefined) node.dataset[dk] = dv;
      }
      else if (k.startsWith('on') && typeof v === 'function') {
        node.addEventListener(k.slice(2).toLowerCase(), v);
      }
      else if (k === 'html') node.innerHTML = v;         // 仅用于本地可信模板
      else if (v === true) node.setAttribute(k, '');
      else node.setAttribute(k, String(v));
    }
  }
  append(node, children);
  return node;
}

export function append(parent, children) {
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false || c === '') continue;
    parent.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return parent;
}

export const txt = (s) => document.createTextNode(String(s ?? ''));

export function frag(...children) {
  return append(document.createDocumentFragment(), children);
}

/** 内联 SVG 图标。d 为路径集合，来自本模块内的 ICON 常量表。 */
export function icon(name, size = null) {
  const spec = ICON[name];
  if (!spec) return txt('');
  const el = document.createElementNS(SVG_NS, 'svg');
  el.setAttribute('viewBox', '0 0 24 24');
  el.setAttribute('fill', spec.fill || 'none');
  el.setAttribute('stroke', spec.fill ? 'none' : 'currentColor');
  el.setAttribute('stroke-width', '2');
  el.setAttribute('stroke-linecap', 'round');
  el.setAttribute('stroke-linejoin', 'round');
  el.setAttribute('aria-hidden', 'true');
  if (size) { el.setAttribute('width', size); el.setAttribute('height', size); }
  for (const d of spec.d) {
    const p = document.createElementNS(SVG_NS, d.startsWith('CIRCLE:') ? 'circle' : 'path');
    if (d.startsWith('CIRCLE:')) {
      const [cx, cy, r] = d.slice(7).split(',');
      p.setAttribute('cx', cx); p.setAttribute('cy', cy); p.setAttribute('r', r);
    } else p.setAttribute('d', d);
    el.appendChild(p);
  }
  return el;
}

const SVG_NS = 'http://www.w3.org/2000/svg';

export const ICON = {
  chat:     { d: ['M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z'] },
  grid:     { d: ['M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z'] },
  play:     { d: ['M6 3l14 9-14 9z'], fill: 'currentColor' },
  panel:    { d: ['M3 3h18v18H3z', 'M9 3v18', 'M3 12h6'] },
  plus:     { d: ['M12 5v14', 'M5 12h14'] },
  chevron:  { d: ['M9 6l6 6-6 6'] },
  check:    { d: ['M20 6L9 17l-5-5'] },
  x:        { d: ['M6 6l12 12', 'M18 6L6 18'] },
  warn:     { d: ['M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z', 'M12 9v4', 'M12 17h.01'] },
  file:     { d: ['M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z', 'M14 2v6h6'] },
  folder:   { d: ['M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z'] },
  image:    { d: ['M3 3h18v18H3z', 'CIRCLE:8.5,8.5,1.5', 'M21 15l-5-5L5 21'] },
  shield:   { d: ['M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z', 'M9 12l2 2 4-4'] },
  doc:      { d: ['M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z', 'M14 2v6h6', 'M16 13H8', 'M16 17H8'] },
  globe:    { d: ['CIRCLE:12,12,10', 'M2 12h20', 'M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z'] },
  list:     { d: ['M8 6h13', 'M8 12h13', 'M8 18h13', 'M3 6h.01', 'M3 12h.01', 'M3 18h.01'] },
  send:     { d: ['M22 2L11 13', 'M22 2l-7 20-4-9-9-4 20-7z'] },
  clip:     { d: ['M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48'] },
  trash:    { d: ['M3 6h18', 'M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2', 'M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6'] },
  expand:   { d: ['M15 3h6v6', 'M9 21H3v-6', 'M21 3l-7 7', 'M3 21l7-7'] },
  refresh:  { d: ['M23 4v6h-6', 'M1 20v-6h6', 'M3.51 9a9 9 0 0 1 14.85-3.36L23 10', 'M1 14l4.64 4.36A9 9 0 0 0 20.49 15'] },
  bolt:     { d: ['M13 2L3 14h7l-1 8 10-12h-7z'] },
  sun:      { d: ['CIRCLE:12,12,4', 'M12 2v2', 'M12 20v2', 'M4.9 4.9l1.4 1.4', 'M17.7 17.7l1.4 1.4', 'M2 12h2', 'M20 12h2', 'M4.9 19.1l1.4-1.4', 'M17.7 6.3l1.4-1.4'] },
  moon:     { d: ['M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z'] },
  brain:    { d: ['M9.5 2A2.5 2.5 0 0 1 12 4.5v15a2.5 2.5 0 0 1-4.96.44 2.5 2.5 0 0 1-2.96-3.08 3 3 0 0 1-.34-5.58 2.5 2.5 0 0 1 1.32-4.24A2.5 2.5 0 0 1 9.5 2z', 'M14.5 2A2.5 2.5 0 0 0 12 4.5v15a2.5 2.5 0 0 0 4.96.44 2.5 2.5 0 0 0 2.96-3.08 3 3 0 0 0 .34-5.58 2.5 2.5 0 0 0-1.32-4.24A2.5 2.5 0 0 0 14.5 2z'] },
  link:     { d: ['M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71', 'M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71'] },
  chart:    { d: ['M18 20V10', 'M12 20V4', 'M6 20v-6'] },
  stop:     { d: ['M6 6h12v12H6z'], fill: 'currentColor' },
  target:   { d: ['CIRCLE:12,12,10', 'CIRCLE:12,12,6', 'CIRCLE:12,12,2'] },
  clock:    { d: ['CIRCLE:12,12,10', 'M12 6v6l4 2'] },
};

/* ---------------- 查询与工具 ---------------- */
export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

/** 保持滚动位置执行一次重渲染，避免面板跳动。 */
export function keepScroll(el, mutate) {
  const top = el.scrollTop;
  mutate();
  el.scrollTop = top;
}

/** 只在内容真的变了才写入，减少无谓重排。 */
export function swap(host, build) {
  const next = build();
  host.replaceChildren(next instanceof Node ? next : frag(next));
  return host;
}

export function clsToggle(el, map) {
  for (const [c, on] of Object.entries(map)) el.classList.toggle(c, !!on);
  return el;
}

/** 重放 CSS 动画：移除类 → 强制 reflow → 加回。 */
export function replay(el, cls) {
  el.classList.remove(cls);
  void el.offsetWidth;
  el.classList.add(cls);
}

let toastHost = null;
function ensureToastHost() {
  if (!toastHost) {
    toastHost = h('div', { class: 'toast-host', role: 'status', 'aria-live': 'polite' });
    document.body.appendChild(toastHost);
  }
  return toastHost;
}
// a11y:live region 必须先于首条消息存在,否则屏幕阅读器不播报第一条 toast;
// 模块脚本执行时 body 已可用,node 单测环境无 document 则跳过。
if (typeof document !== 'undefined') {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => ensureToastHost(), { once: true });
  } else {
    ensureToastHost();
  }
}

export function toast(msg, ms = 2400) {
  const t = h('div', { class: 'toast' }, msg);
  ensureToastHost().appendChild(t);
  setTimeout(() => t.remove(), ms);
}

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export function hhmm(d = new Date()) {
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

export function mmss(sec) {
  const m = Math.floor(sec / 60), s = Math.floor(sec % 60);
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}
