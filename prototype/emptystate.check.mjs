/* ============================================================
   emptystate 的无浏览器自查脚本(node prototype/emptystate.check.mjs)
   用最小 DOM stub 直接 import emptystate.js,断言:
   ① renderEmpty 输出结构(图标/标题/提示/动作按钮/容器挂载/事件解耦);
   ② XSS:title/hint/message 一律 textContent,注入串不产生元素;
   ③ renderSkeleton 四种 kind 的结构 + rows 边界(0/负数/NaN/超大 clamp);
   ④ renderError 结构与「重试」回调;
   ⑤ 事件桥(states:* → 既有入口按钮)与会话列表空态守望。
   放在 prototype/ 根目录:check_frontend.py 只发现该层的 *.check.mjs。
   只依赖 node 内建能力,零 npm 依赖。
   ============================================================ */

/* ---------------- 最小 DOM stub(pipelinerail.check.mjs 同款裁剪) ---------------- */
class NodeBase {
  constructor() { this.childNodes = []; this.parentNode = null; }
  get isConnected() {
    let n = this;
    while (n.parentNode) n = n.parentNode;
    return n === DOC.body;
  }
  get textContent() { return this.childNodes.map((c) => c.textContent).join(''); }
}

class TextNode extends NodeBase {
  constructor(s) { super(); this.nodeType = 3; this.data = String(s); }
  get textContent() { return this.data; }
  set textContent(v) { this.data = String(v); }
  remove() { detach(this); }
}

function detach(n) {
  if (!n.parentNode) return;
  const i = n.parentNode.childNodes.indexOf(n);
  if (i >= 0) n.parentNode.childNodes.splice(i, 1);
  n.parentNode = null;
}

class Element extends NodeBase {
  constructor(tag) {
    super();
    this.nodeType = 1;
    this.tagName = String(tag).toLowerCase();
    this.attrs = {}; this._cls = new Set(); this.dataset = {}; this.style = {};
    this.listeners = {};
    this.value = ''; this.disabled = false;
  }
  get className() { return [...this._cls].join(' '); }
  set className(v) { this._cls = new Set(String(v).split(/\s+/).filter(Boolean)); }
  get classList() {
    const s = this._cls;
    return { add: (...cs) => cs.forEach((c) => s.add(c)), contains: (c) => s.has(c) };
  }
  setAttribute(k, v) { if (k === 'class') { this.className = v; return; } this.attrs[k] = String(v); }
  getAttribute(k) { return k === 'class' ? this.className : (this.attrs[k] ?? null); }
  appendChild(n) { detach(n); n.parentNode = this; this.childNodes.push(n); return n; }
  append(...ns) { ns.forEach((n) => this.appendChild(n instanceof NodeBase ? n : new TextNode(n))); }
  replaceChildren(...ns) {
    [...this.childNodes].forEach(detach);
    this.append(...ns.filter((n) => n !== null && n !== undefined));
  }
  remove() { detach(this); }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  dispatchEvent(evt) {
    evt.target ||= this; evt.currentTarget = this;
    (this.listeners[evt.type] || []).forEach((fn) => fn.call(this, evt));
    return true;
  }
  click() { this.dispatchEvent({ type: 'click', preventDefault() {}, target: this }); }
  focus() {}
  set textContent(v) { this.replaceChildren(new TextNode(v)); }
  get textContent() { return super.textContent; }
  get children() { return this.childNodes.filter((c) => c.nodeType === 1); }
  matches(sel) {
    const m = /^([a-z0-9-]*)((?:\.[\w-]+)*)$/.exec(sel);
    if (!m) return false;
    if (m[1] && this.tagName !== m[1]) return false;
    return (m[2].match(/\.[\w-]+/g) || []).every((c) => this._cls.has(c.slice(1)));
  }
  querySelectorAll(sel) {
    const out = new Set();
    for (const part of String(sel).split(',')) {
      const segs = part.trim().split(/\s+/).filter(Boolean);
      let bases = [this];
      for (const seg of segs) {
        const next = new Set();
        for (const b of bases) for (const d of walk(b)) if (d.matches(seg)) next.add(d);
        bases = [...next];
      }
      bases.forEach((b) => out.add(b));
    }
    return [...out];
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}

function* walk(n) { for (const c of n.children) { yield c; yield* walk(c); } }

const DOC = {
  body: new Element('body'),
  listeners: {},
  createElement: (t) => new Element(t),
  createElementNS: (_ns, t) => new Element(t),
  createTextNode: (s) => new TextNode(s),
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); },
  dispatchEvent(evt) {
    (this.listeners[evt.type] || []).forEach((fn) => fn(evt));
    return true;
  },
  getElementById(id) {
    for (const d of walk(this.body)) if (d.attrs.id === id) return d;
    return null;
  },
};

globalThis.document = DOC;
globalThis.Node = NodeBase;
globalThis.CustomEvent = class {
  constructor(type, opts = {}) { this.type = type; this.detail = opts.detail ?? null; }
};
/* 守望器 stub:emptystate.watchSessionsEmpty 只要求 observe 存在;
   初次 paint 是同步调用,不依赖真实变更回调。 */
globalThis.MutationObserver = class {
  constructor(cb) { this.cb = cb; }
  observe() {}
  disconnect() {}
};

/* ---------------- 断言工具(check_frontend 解析约定:两空格 ok/FAIL) ---------------- */
let failed = 0;
let count = 0;
function check(name, cond) {
  count += 1;
  if (cond) { console.log(`  ok  ${name}`); }
  else { failed += 1; console.error(`  FAIL ${name}`); }
}

/* ---------------- 被测模块(stub 就绪后再 import:模块级事件桥依赖 document) ---------------- */
const ES = await import('./js/emptystate.js');

/* ---------------- ① renderEmpty 输出结构 ---------------- */
console.log('== ① renderEmpty 结构 ==');
const e1 = ES.renderEmpty(null, { icon: 'image', title: '还没有产物图件', hint: '运行流水线的出图步骤即可生成。' });
check('根节点 .es-empty + role=status', e1.matches('.es-empty') && e1.getAttribute('role') === 'status');
check('图标区含内联 svg(不引外部资源)', !!e1.querySelector('.es-empty-ic svg'));
check('标题/提示文本落位', e1.querySelector('.es-empty-title')?.textContent === '还没有产物图件'
  && e1.querySelector('.es-empty-hint')?.textContent === '运行流水线的出图步骤即可生成。');
check('无 action → 不渲染按钮', e1.querySelector('button') === null);
check('未知图标名回落默认(仍有 svg)', !!ES.renderEmpty(null, { icon: '不存在', title: 'x' }).querySelector('svg'));
check('title/hint 缺省 → 对应节点不渲染', ES.renderEmpty(null, {}).querySelector('.es-empty-title') === null);

// 容器挂载:传入容器则 replaceChildren,重复渲染不堆叠
const host1 = new Element('div');
const e2 = ES.renderEmpty(host1, { title: 'A' });
ES.renderEmpty(host1, { title: 'B' });
check('容器挂载:replaceChildren 且返回根节点', host1.children.length === 1
  && host1.children[0].matches('.es-empty') && e2.matches('.es-empty'));

// action 事件解耦:按钮只 dispatch 自定义事件,不直接调用任何模块函数
const got = [];
DOC.addEventListener('states:test-action', (ev) => got.push(ev.detail));
const e3 = ES.renderEmpty(null, {
  title: 'x', action: { label: '运行流水线', event: 'states:test-action', detail: { from: 'check' } },
});
const actBtn = e3.querySelector('.es-empty-act');
check('action 按钮渲染(type=button + 文案)', actBtn?.getAttribute('type') === 'button'
  && actBtn?.textContent === '运行流水线');
actBtn.click();
check('点击 → document 收到自定义事件与 detail', got.length === 1 && got[0]?.from === 'check');

// action onClick 直接回调形态
let clicked = 0;
ES.renderEmpty(null, { title: 'x', action: { label: 'y', onClick: () => { clicked += 1; } } })
  .querySelector('button').click();
check('action.onClick 回调形态可用', clicked === 1);

/* ---------------- ② XSS:不可信文本一律 textContent ---------------- */
console.log('\n== ② XSS 转义 ==');
const payload = '<img src=x onerror=alert(1)>';
const xe = ES.renderEmpty(null, { title: payload, hint: '<script>alert(2)</script>' });
check('title 注入串不产生元素(无 img)', xe.querySelector('img') === null
  && xe.querySelector('.es-empty-title').children.length === 0);
check('title 原文按纯文本保留', xe.querySelector('.es-empty-title').textContent === payload);
check('hint 注入串不产生元素(无 script)', xe.querySelector('script') === null
  && xe.querySelector('.es-empty-hint').textContent === '<script>alert(2)</script>');
const xr = ES.renderError(null, { message: payload, retry: () => {} });
check('error message 注入串不产生元素', xr.querySelector('img') === null
  && xr.querySelector('.es-msg').textContent === payload);
const xa = ES.renderEmpty(null, { title: 'x', action: { label: payload, event: 'e' } });
check('action label 注入串不产生元素', xa.querySelector('img') === null
  && xa.querySelector('.es-empty-act').textContent === payload);

/* ---------------- ③ renderSkeleton 结构 + rows 边界 ---------------- */
console.log('\n== ③ renderSkeleton 结构与 rows 边界 ==');
const s1 = ES.renderSkeleton(null, { kind: 'list', rows: 4 });
check('list:根 .es-skel.es-skel-list + aria-busy', s1.matches('.es-skel.es-skel-list')
  && s1.getAttribute('aria-busy') === 'true');
check('list:灰条数 = rows(4),每条 aria-hidden', s1.querySelectorAll('.es-bar').length === 4
  && s1.querySelectorAll('.es-bar').every((b) => b.getAttribute('aria-hidden') === 'true'));
check('aria-label 缺省「内容加载中」/可传入', s1.getAttribute('aria-label') === '内容加载中'
  && ES.renderSkeleton(null, { label: '正在读取产物清单' }).getAttribute('aria-label') === '正在读取产物清单');

const s2 = ES.renderSkeleton(null, { kind: 'text', rows: 3 });
check('text:末行收短(46%)', s2.querySelectorAll('.es-bar')[2].style.width === '46%');
const s3 = ES.renderSkeleton(null, { kind: 'tree', rows: 6 });
check('tree:缩进循环 0/14/14/28/14/28', s3.querySelectorAll('.es-bar')
  .map((b) => b.style.marginLeft || '0').join(',') === '0,14px,14px,28px,14px,28px');
const s4 = ES.renderSkeleton(null, { kind: 'grid', rows: 2 });
check('grid:cell 数 = rows,每格 1 缩略块 + 2 灰条', s4.querySelectorAll('.es-cell').length === 2
  && s4.querySelectorAll('.es-thumb').length === 2 && s4.querySelectorAll('.es-bar').length === 4);
check('未知 kind 回落 list', ES.renderSkeleton(null, { kind: '???' }).matches('.es-skel-list'));

check('rows=0 → 回落默认 3', ES.renderSkeleton(null, { rows: 0 }).querySelectorAll('.es-bar').length === 3);
check('rows=-5 → 回落默认 3', ES.renderSkeleton(null, { rows: -5 }).querySelectorAll('.es-bar').length === 3);
check('rows=NaN/非数值 → 回落默认 3', ES.renderSkeleton(null, { rows: 'abc' }).querySelectorAll('.es-bar').length === 3);
check('rows=1e9 → clamp 上限 12', ES.renderSkeleton(null, { rows: 1e9 }).querySelectorAll('.es-bar').length === 12);
check('rows=5.9 → 向下取整 5', ES.renderSkeleton(null, { rows: 5.9 }).querySelectorAll('.es-bar').length === 5);
check('clampRows 边界直测', ES.clampRows(0) === 3 && ES.clampRows(-1) === 3
  && ES.clampRows(Infinity) === 3 && ES.clampRows(12) === 12 && ES.clampRows(13) === 12 && ES.clampRows(1) === 1);

/* ---------------- ④ renderError 结构与重试 ---------------- */
console.log('\n== ④ renderError 结构与重试 ==');
let retried = 0;
const r1 = ES.renderError(null, { message: '日志读取失败——后端不可达或响应异常。', retry: () => { retried += 1; } });
check('根节点 .es-error + role=alert + 告警图标', r1.matches('.es-error')
  && r1.getAttribute('role') === 'alert' && r1.children[0].tagName === 'svg');
check('消息文本落位', r1.querySelector('.es-msg')?.textContent === '日志读取失败——后端不可达或响应异常。');
const retryBtn = r1.querySelector('.es-retry');
check('「重试」按钮渲染(统一文案要求)', retryBtn?.textContent === '重试'
  && retryBtn?.getAttribute('aria-label') === '重试');
retryBtn.click();
retryBtn.click();
check('点击重试 → 回调逐次触发', retried === 2);
check('retry 缺省 → 不渲染按钮(健壮性)', ES.renderError(null, { message: 'x' }).querySelector('button') === null);
check('message 缺省 → 默认兜底文案', ES.renderError(null, {}).querySelector('.es-msg').textContent.includes('重试') === false
  && ES.renderError(null, {}).querySelector('.es-msg').textContent.length > 0);

/* ---------------- ⑤ 事件桥 + 会话列表空态守望 ---------------- */
console.log('\n== ⑤ 事件桥与会话空态守望 ==');
let runClicks = 0;
const btnRun = new Element('button');
btnRun.setAttribute('id', 'btnRun');
btnRun.addEventListener('click', () => { runClicks += 1; });
DOC.body.appendChild(btnRun);
DOC.dispatchEvent(new CustomEvent('states:run-pipeline'));
check('states:run-pipeline → 桥接点击既有入口 #btnRun', runClicks === 1);

const prompt = new Element('textarea');
prompt.setAttribute('id', 'prompt');
let focused = false;
prompt.focus = () => { focused = true; };
DOC.body.appendChild(prompt);
document.dispatchEvent(new CustomEvent('states:focus-prompt'));
check('states:focus-prompt → 聚焦 #prompt', focused === true);

let newClicks = 0;
const btnNew = new Element('button');
btnNew.setAttribute('id', 'btnNewProject');
btnNew.addEventListener('click', () => { newClicks += 1; });
DOC.body.appendChild(btnNew);

const sessions = new Element('div');
sessions.setAttribute('id', 'sessions');
DOC.body.appendChild(sessions);
ES.watchSessionsEmpty();
check('会话列表真空 → 注入空态卡', !!sessions.querySelector('.es-empty')
  && sessions.textContent.includes('还没有项目'));
sessions.querySelector('.es-empty-act').click();
check('空态「新建项目」→ 桥接点击 #btnNewProject', newClicks === 1);

// 有真实会话行时不得干预
const sessions2 = new Element('div');
sessions2.setAttribute('id', 'sessions');
sessions.remove();
const row = new Element('div');
row.setAttribute('class', 'sess');
sessions2.appendChild(row);
DOC.body.appendChild(sessions2);
ES.watchSessionsEmpty();
check('列表有 .sess 行 → 不注入空态', sessions2.querySelector('.es-empty') === null);

/* ---------------- 结构样例输出(评审用) ---------------- */
function serialize(n, indent = '') {
  if (n.nodeType === 3) {
    const s = n.data.trim();
    return s ? `${indent}${s}\n` : '';
  }
  const attrs = [];
  if (n._cls.size) attrs.push(`class="${n.className}"`);
  for (const [k, v] of Object.entries(n.attrs)) attrs.push(v === '' ? k : `${k}="${v}"`);
  const open = `${indent}<${n.tagName}${attrs.length ? ' ' + attrs.join(' ') : ''}>`;
  if (!n.childNodes.length) return `${open}</${n.tagName}>\n`;
  return `${open}\n${n.childNodes.map((c) => serialize(c, indent + '  ')).join('')}${indent}</${n.tagName}>\n`;
}
console.log('\n== 关键结构样例 ==\n--- 空态(带动作) ---');
process.stdout.write(serialize(e3));
console.log('--- 错误态 ---');
process.stdout.write(serialize(r1));

console.log(failed ? `\n${count} 项断言,${failed} 项失败` : `\n全部 ${count} 项断言通过`);
process.exit(failed ? 1 : 0);
