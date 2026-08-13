/* ============================================================
   memorypanel 的无浏览器自查脚本(node prototype/memorypanel.check.mjs)
   用最小 DOM stub + fetch 桩直接 import memorypanel.js(零真实网络),断言:
   ① 自初始化:侧栏底部「记忆」入口(button 语义 / aria / 位于账号行之前);
   ② 纯函数:kindView 三类徽章 + 未知回退、sourceLabel、timeLabel(本地时区
      无关)、metaLabel(权重 >1 才显示);
   ③ 打开面板 → GET /api/memory → 列表渲染(徽章/内容/元信息/条数提示);
   ④ 注入安全:内容含 HTML 字符串按纯文本渲染,不产生元素;
   ⑤ 搜索:输入即请求 ?q=(URL 编码),过滤结果 + 「已过滤」提示,清空回全量;
   ⑥ 添加:空内容拦截零请求;正常添加 POST {kind, content} + 清空输入 + 刷新;
      Enter 快捷提交;
   ⑦ 删除:行内 × → DELETE /api/memory/{id} → 列表刷新;
   ⑧ 空列表空态文案;⑨ 关闭面板移除浮层并还原焦点。
   放在 prototype/ 根目录:check_frontend.py 只发现该层的 *.check.mjs。
   只依赖 node 内建能力,零 npm 依赖。
   ============================================================ */

/* ---------------- 最小 DOM stub(llmsettings.check.mjs 同款) ---------------- */
class NodeBase {
  constructor() { this.childNodes = []; this.parentNode = null; }
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
    this.disabled = false;
    if (this.tagName === 'input' || this.tagName === 'textarea' || this.tagName === 'option') {
      this.value = ''; this.placeholder = '';
    }
    if (this.tagName === 'option') this.selected = false;
  }
  get id() { return this.attrs.id || ''; }
  set id(v) { this.attrs.id = String(v); }
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
  insertBefore(n, ref) {
    detach(n); n.parentNode = this;
    const i = ref ? this.childNodes.indexOf(ref) : -1;
    if (i < 0) this.childNodes.push(n); else this.childNodes.splice(i, 0, n);
    return n;
  }
  prepend(...ns) {
    ns.reverse().forEach((n) => this.insertBefore(
      n instanceof NodeBase ? n : new TextNode(n), this.childNodes[0] || null));
  }
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
  focus() { DOC.activeElement = this; }
  get firstChild() { return this.childNodes[0] || null; }
  set textContent(v) { this.replaceChildren(new TextNode(v)); }
  get textContent() { return super.textContent; }
  get children() { return this.childNodes.filter((c) => c.nodeType === 1); }
  set innerHTML(v) {
    [...this.childNodes].forEach(detach);
    if (String(v).trim()) parseHTML(String(v), this);
  }
  matches(sel) {
    const m = /^([a-zA-Z0-9-]*)(?:#([\w-]+))?((?:\.[\w-]+)*)((?:\[[\w-]+(?:="[^"]*")?\])*)$/
      .exec(String(sel).trim());
    if (!m) return false;
    if (m[1] && this.tagName !== m[1].toLowerCase()) return false;
    if (m[2] && this.attrs.id !== m[2]) return false;
    if (!(m[3].match(/\.[\w-]+/g) || []).every((c) => this._cls.has(c.slice(1)))) return false;
    return (m[4].match(/\[[\w-]+(?:="[^"]*")?\]/g) || []).every((t) => {
      const am = /^\[([\w-]+)(?:="([^"]*)")?\]$/.exec(t);
      const val = this.attrs[am[1]];
      return am[2] === undefined ? val !== undefined : val === am[2];
    });
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

class SelectElement extends Element {
  get options() { return this.children.filter((c) => c.tagName === 'option'); }
  get value() {
    const opts = this.options;
    const on = opts.filter((o) => o.selected);
    return on.length ? on[on.length - 1].value : (opts.length ? opts[0].value : '');
  }
  set value(v) { for (const o of this.options) o.selected = (o.value === String(v)); }
}

function* walk(n) { for (const c of n.children) { yield c; yield* walk(c); } }

const VOID = new Set(['input', 'br', 'hr', 'img', 'path', 'circle', 'rect',
  'line', 'polyline', 'polygon', 'ellipse']);
function parseHTML(html, parent) {
  const re = /<(\/)?([a-zA-Z][\w-]*)((?:"[^"]*"|[^>"])*?)\s*(\/)?>/g;
  const stack = [parent];
  let last = 0, m;
  while ((m = re.exec(html))) {
    const text = html.slice(last, m.index);
    if (text.trim()) {
      stack[stack.length - 1].appendChild(new TextNode(text.replace(/\s+/g, ' ').trim()));
    }
    last = re.lastIndex;
    if (m[1]) { if (stack.length > 1) stack.pop(); continue; }
    const el = DOC.createElement(m[2]);
    const attrRe = /([\w:-]+)(?:\s*=\s*"([^"]*)")?/g;
    let a;
    while ((a = attrRe.exec(m[3] || ''))) {
      const k = a[1], v = a[2] === undefined ? '' : a[2];
      el.setAttribute(k, v);
      if (k === 'disabled' || k === 'hidden' || k === 'selected' || k === 'checked') el[k] = true;
      else if (k === 'placeholder') el.placeholder = v;
      else if (k === 'value' && el.tagName !== 'select') el.value = v;
      else if (k === 'type' && (el.tagName === 'button' || el.tagName === 'input')) el.type = v;
    }
    stack[stack.length - 1].appendChild(el);
    if (!m[4] && !VOID.has(el.tagName)) stack.push(el);
  }
  const tail = html.slice(last);
  if (tail.trim()) stack[stack.length - 1].appendChild(new TextNode(tail.trim()));
}

const DOC = {
  readyState: 'complete',
  activeElement: null,
  body: new Element('body'),
  listeners: {},
  createElement: (t) => (String(t).toLowerCase() === 'select'
    ? new SelectElement(t) : new Element(t)),
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
  querySelector(sel) { return this.body.querySelector(sel); },
  querySelectorAll(sel) { return this.body.querySelectorAll(sel); },
};

globalThis.document = DOC;
globalThis.window = globalThis;
globalThis.Node = NodeBase;

/* ---------------- fetch 桩:全部 /api/memory* 走内存路由,零真实网络 ---------------- */
const T0 = Math.floor(new Date(2026, 7, 13, 9, 5).getTime() / 1000); // 本地 2026-08-13 09:05

const seed = () => ([
  { id: 1, ts: T0, kind: 'preference', content: '常用区域:玉树', source: 'user',
    session_id: null, weight: 2, archived: null },
  { id: 2, ts: T0 - 60, kind: 'fact', content: 'SAR 数据在 D:/SAR/yushu', source: 'user',
    session_id: null, weight: 1, archived: null },
  { id: 3, ts: T0 - 120, kind: 'outcome', source: 'auto', session_id: null, weight: 1,
    content: '2026-08-10 permafrost_sbas@玉树 用 mintpy_sbas 完成,证据级 runnable',
    archived: null },
]);
const routes = { list: seed() };   // GET /api/memory 的返回体(可按用例改)
const calls = [];                  // {url, method, body} 供断言请求载荷
globalThis.fetch = async (url, opts = {}) => {
  const method = opts.method || 'GET';
  calls.push({ url: String(url), method, body: opts.body ? JSON.parse(opts.body) : null });
  const json = (data) => ({ ok: true, status: 200, json: async () => data });
  const u = String(url);
  if (/\/api\/memory\/\d+$/.test(u) && method === 'DELETE') {
    const id = Number(u.split('/').pop());
    routes.list = routes.list.filter((m) => m.id !== id);
    return json({ ok: true, archived: true, id });
  }
  if (u.includes('/api/memory') && method === 'POST') {
    const body = JSON.parse(opts.body);
    const item = { id: 99, ts: T0 + 60, kind: body.kind, content: body.content,
      source: 'user', session_id: null, weight: 1, archived: null };
    routes.list = [item, ...routes.list];
    return json({ ok: true, memory: item });
  }
  if (u.includes('/api/memory') && method === 'GET') {
    const qm = /[?&]q=([^&]*)/.exec(u);
    const q = qm ? decodeURIComponent(qm[1]) : '';
    return json({ items: q ? routes.list.filter((m) => m.content.includes(q)) : routes.list });
  }
  throw new Error(`意外的网络请求:${method} ${u}`);
};

/* ---------------- 断言工具(check_frontend 解析约定:两空格 ok/FAIL) ---------------- */
let failed = 0;
let count = 0;
function check(name, cond) {
  count += 1;
  if (cond) { console.log(`  ok  ${name}`); }
  else { failed += 1; console.error(`  FAIL ${name}`); }
}
const tick = () => new Promise((r) => setTimeout(r, 0));

/* ---------------- 页面骨架(index.html 侧栏底部的最小等价物)+ 被测模块导入 ---------------- */
const sider = new Element('aside'); sider.className = 'sider';
const ft = new Element('div'); ft.className = 'sider-ft';
const seg = new Element('div'); seg.className = 'seg';
const who = new Element('div'); who.className = 'who';
ft.appendChild(seg); ft.appendChild(who); sider.appendChild(ft);
DOC.body.appendChild(sider);

const M = await import('./js/memorypanel.js');
await tick();

/* ---------------- ① 自初始化:侧栏「记忆」入口 ---------------- */
console.log('== ① 自初始化:侧栏「记忆」入口 ==');
const btn = DOC.getElementById('btnMemory');
check('入口按钮注入 .sider-ft', !!btn && btn.parentNode === ft);
check('位于账号行(.who)之前', ft.children.indexOf(btn) === ft.children.indexOf(who) - 1);
check('button 语义(type=button)+ aria-label + haspopup=dialog',
  btn?.tagName === 'button' && btn?.type === 'button'
  && btn?.getAttribute('aria-label') === '记忆管理'
  && btn?.getAttribute('aria-haspopup') === 'dialog');
check('可见文案「记忆」', btn?.textContent.includes('记忆'));
check('幂等守卫已置位', window.__memoryPanelInstalled === true);

/* ---------------- ② 纯函数:徽章 / 来源 / 时间 / 元信息 ---------------- */
console.log('\n== ② 纯函数 ==');
check('kindView 三类:偏好/事实/结论',
  M.kindView('preference').label === '偏好' && M.kindView('preference').cls === 'pref'
  && M.kindView('fact').label === '事实' && M.kindView('fact').cls === 'fact'
  && M.kindView('outcome').label === '结论' && M.kindView('outcome').cls === 'outcome');
check('kindView 未知回退原样 + other 配色',
  M.kindView('legacy').label === 'legacy' && M.kindView('legacy').cls === 'other'
  && M.kindView('').label === '?');
check('sourceLabel:auto=自动萃取,其余=手动',
  M.sourceLabel('auto') === '自动萃取' && M.sourceLabel('user') === '手动');
check('timeLabel 本地 YYYY-MM-DD HH:mm', M.timeLabel(T0) === '2026-08-13 09:05');
check('timeLabel 非法值给空串', M.timeLabel(NaN) === '' && M.timeLabel(undefined) === '');
check('metaLabel 权重 >1 才显示(去重加权的可见反馈)',
  M.metaLabel({ source: 'auto', weight: 1.5, ts: T0 }) === '自动萃取 · 权重 1.5 · 2026-08-13 09:05'
  && M.metaLabel({ source: 'user', weight: 1, ts: T0 }) === '手动 · 2026-08-13 09:05');
check('metaLabel 缺时间戳只留来源', M.metaLabel({ source: 'user', weight: 1, ts: null }) === '手动');

/* ---------------- ③ 打开面板 → 列表渲染 ---------------- */
console.log('\n== ③ 打开面板 → 列表渲染 ==');
btn.focus(); // 真实浏览器点击即聚焦;stub 的 click() 不带焦点副作用,显式补齐
btn.click();
await tick(); await tick();
let dlg = DOC.querySelector('.memp');
const F = (name) => dlg.querySelector(`[name="${name}"]`);
check('入口点击 → 面板打开(role=dialog + aria-modal)', !!dlg
  && dlg.getAttribute('role') === 'dialog' && dlg.getAttribute('aria-modal') === 'true');
check('打开即 GET /api/memory', calls.some((c) => c.method === 'GET' && c.url.endsWith('/api/memory')));
let items = dlg.querySelectorAll('.memp-item');
check('三条记忆按服务端顺序渲染', items.length === 3
  && items[0].querySelector('.memp-content').textContent === '常用区域:玉树');
check('徽章:偏好/事实/结论各就各位',
  items[0].querySelector('.memp-badge').textContent === '偏好'
  && items[1].querySelector('.memp-badge').textContent === '事实'
  && items[2].querySelector('.memp-badge').textContent === '结论'
  && items[2].querySelector('.memp-badge').className.includes('is-outcome'));
check('元信息:来源/权重/时间拼接', items[0].querySelector('.memp-meta').textContent
  === '手动 · 权重 2 · 2026-08-13 09:05');
check('自动萃取行的来源标注', items[2].querySelector('.memp-meta').textContent.startsWith('自动萃取'));
check('删除按钮带内容级 aria-label', items[0].querySelector('.memp-del')
  ?.getAttribute('aria-label') === '删除记忆:常用区域:玉树');
check('条数提示「3 条记忆」', F('note').textContent === '3 条记忆');
check('打开后焦点落在添加输入框', DOC.activeElement === F('content'));

/* ---------------- ④ 注入安全:HTML 字符串按纯文本渲染 ---------------- */
console.log('\n== ④ 注入安全 ==');
routes.list = [{ id: 7, ts: T0, kind: 'fact', source: 'user', weight: 1, archived: null,
  content: '<img src=x onerror=alert(1)> 注入试探' }];
F('q').dispatchEvent({ type: 'input' });
await tick(); await tick();
items = dlg.querySelectorAll('.memp-item');
check('内容按 textContent 渲染,不产生元素节点', items.length === 1
  && items[0].querySelectorAll('img').length === 0
  && items[0].querySelector('.memp-content').textContent === '<img src=x onerror=alert(1)> 注入试探');
routes.list = seed();

/* ---------------- ⑤ 搜索 ---------------- */
console.log('\n== ⑤ 搜索 ==');
F('q').value = '玉树';
F('q').dispatchEvent({ type: 'input' });
await tick(); await tick();
check('输入即请求 ?q=(URL 编码)', calls.some((c) => c.method === 'GET'
  && c.url.endsWith('/api/memory?q=' + encodeURIComponent('玉树'))));
items = dlg.querySelectorAll('.memp-item');
check('过滤结果渲染(2/3 条命中)+「已过滤」提示', items.length === 2
  && F('note').textContent === '2 条记忆(已过滤)');
F('q').value = '';
F('q').dispatchEvent({ type: 'input' });
await tick(); await tick();
check('清空搜索回全量', dlg.querySelectorAll('.memp-item').length === 3);

/* ---------------- ⑥ 添加 ---------------- */
console.log('\n== ⑥ 添加 ==');
const postsBefore = calls.filter((c) => c.method === 'POST').length;
F('content').value = '   ';
F('add').click();
await tick();
check('空内容拦截:零 POST + warn 提示',
  calls.filter((c) => c.method === 'POST').length === postsBefore
  && F('note').textContent === '请先填写记忆内容' && F('note').dataset.tone === 'warn');
F('kind').value = 'fact';
F('content').value = '机器只有 16GB 内存,大区域要分块';
F('add').click();
await tick(); await tick();
const post = calls.filter((c) => c.method === 'POST').pop();
check('添加 POST /api/memory {kind, content}', !!post && post.url.endsWith('/api/memory')
  && post.body.kind === 'fact' && post.body.content === '机器只有 16GB 内存,大区域要分块');
check('成功后输入清空 + 「已记住」+ 列表刷新(4 条)',
  F('content').value === '' && F('note').textContent === '4 条记忆'
  && dlg.querySelectorAll('.memp-item').length === 4);
F('content').value = 'Enter 提交的记忆';
F('content').dispatchEvent({ type: 'keydown', key: 'Enter' });
await tick(); await tick();
check('Enter 快捷提交(等价点添加)',
  calls.filter((c) => c.method === 'POST').pop().body.content === 'Enter 提交的记忆');

/* ---------------- ⑦ 删除 ---------------- */
console.log('\n== ⑦ 删除 ==');
routes.list = seed();
F('q').dispatchEvent({ type: 'input' });
await tick(); await tick();
items = dlg.querySelectorAll('.memp-item');
items[1].querySelector('.memp-del').click();
await tick(); await tick();
check('行内 × → DELETE /api/memory/2',
  calls.some((c) => c.method === 'DELETE' && c.url.endsWith('/api/memory/2')));
check('删除后列表刷新(2 条,id=2 消失)',
  dlg.querySelectorAll('.memp-item').length === 2
  && dlg.querySelectorAll('.memp-content').every((c) => !c.textContent.includes('D:/SAR')));

/* ---------------- ⑧ 空列表空态 ---------------- */
console.log('\n== ⑧ 空态 ==');
routes.list = [];
F('q').dispatchEvent({ type: 'input' });
await tick(); await tick();
check('空列表 → 引导文案', dlg.querySelectorAll('.memp-item').length === 0
  && F('note').textContent === '还没有记忆:可手动添加,或在任务完成后萃取结论');
routes.list = [];
F('q').value = '不存在的关键词';
F('q').dispatchEvent({ type: 'input' });
await tick(); await tick();
check('搜索无命中 → 「没有匹配的记忆」', F('note').textContent === '没有匹配的记忆');

/* ---------------- ⑨ 关闭 ---------------- */
console.log('\n== ⑨ 关闭 ==');
dlg.querySelector('.memp-x').click();
check('× 关闭 → 浮层移除', DOC.querySelector('.memp-overlay') === null);
check('焦点还原到入口按钮', DOC.activeElement === btn);
btn.click();
await tick(); await tick();
dlg = DOC.querySelector('.memp');
dlg.parentNode.dispatchEvent({ type: 'keydown', key: 'Escape', stopPropagation() {} });
check('Escape 关闭浮层', DOC.querySelector('.memp-overlay') === null);

console.log(failed ? `\n${count} 项断言,${failed} 项失败` : `\n全部 ${count} 项断言通过`);
process.exit(failed ? 1 : 0);
