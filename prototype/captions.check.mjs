/* ============================================================
   captions 的无浏览器自查脚本(node prototype/captions.check.mjs)
   用最小 DOM stub 直接 import js/captions.js,断言:
   ① 纯函数:缩略图 URL 反解 / 状态键 / 徽章文案 / 错误文案 / 语种取文 /
     图注文件名 / 图件卡信息读取(真实 vs 演示);
   ② 降级:file:// + 全演示图件 → 容器只给提示,零按钮零请求;
   ③ 注入 + 已有图注:真实图件卡出现 → 自带容器 .capx 逐图成行;
     GET /api/report/caption 命中的图直接显示图注(无需点生成),
     404 的图保持「生成图注」按钮;幂等重挂不重复请求;
   ④ 中英切换:English/中文页签互换正文与 aria-pressed;
   ⑤ 一键复制各自:复制英文/复制中文 → 剪贴板收到对应语种全文 + toast;
   ⑥ 生成:点「生成图注」→ POST body 带 session/figure/run_id →
     图注卡渲染 + 「LLM 润色」徽章 + 落盘文件名注明;
   ⑦ 失败语义:404 / 网络异常 → 如实提示,绝不清掉已有图注;
   ⑧ gallery 重渲染 → 观察器自动重挂,结果不丢、不重复请求;
   ⑨ 图件卡自身点击不受干扰(灯箱行为归 gallery.js)。
   只依赖 node 内建能力,零 npm 依赖(check() 约定与其余 check.mjs 一致)。
   ============================================================ */

/* ---------------- 最小 DOM stub(reportdraft.check.mjs 同款 + closest/属性选择器) ---------------- */
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

class Fragment extends NodeBase { constructor() { super(); this.nodeType = 11; } }

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
    this.value = ''; this.checked = false; this.disabled = false;
  }
  get className() { return [...this._cls].join(' '); }
  set className(v) { this._cls = new Set(String(v).split(/\s+/).filter(Boolean)); }
  get classList() {
    const s = this._cls;
    return {
      add: (...cs) => cs.forEach((c) => s.add(c)),
      remove: (...cs) => cs.forEach((c) => s.delete(c)),
      contains: (c) => s.has(c),
      toggle: (c, on) => { const v = on === undefined ? !s.has(c) : !!on; v ? s.add(c) : s.delete(c); return v; },
    };
  }
  setAttribute(k, v) { if (k === 'class') { this.className = v; return; } this.attrs[k] = String(v); }
  getAttribute(k) { return k === 'class' ? this.className : (this.attrs[k] ?? null); }
  hasAttribute(k) { return this.getAttribute(k) !== null; }
  appendChild(n) {
    if (n instanceof Fragment) { [...n.childNodes].forEach((c) => this.appendChild(c)); return n; }
    detach(n); n.parentNode = this; this.childNodes.push(n); return n;
  }
  append(...ns) { ns.forEach((n) => this.appendChild(n instanceof NodeBase ? n : new TextNode(n))); }
  replaceChildren(...ns) {
    [...this.childNodes].forEach(detach);
    this.append(...ns.filter((n) => n !== null && n !== undefined));
  }
  remove() { detach(this); }
  contains(n) { while (n) { if (n === this) return true; n = n.parentNode; } return false; }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  removeEventListener(type, fn) { this.listeners[type] = (this.listeners[type] || []).filter((f) => f !== fn); }
  dispatchEvent(evt) {
    evt.target ||= this; evt.currentTarget = this;
    (this.listeners[evt.type] || []).forEach((fn) => fn.call(this, evt));
    return true;
  }
  click() { this.dispatchEvent({ type: 'click', preventDefault() {}, target: this }); }
  focus() {} blur() {}
  select() {}
  set textContent(v) { this.replaceChildren(new TextNode(v)); }
  get textContent() { return super.textContent; }
  get children() { return this.childNodes.filter((c) => c.nodeType === 1); }
  matches(sel) {
    // 支持 tag.class1.class2[attr1][attr2] 的最小组合(captions 用到属性存在选择器)
    const m = /^([a-z0-9-]*)((?:\.[\w-]+)*)((?:\[[\w-]+\])*)$/.exec(sel);
    if (!m) return false;
    if (m[1] && this.tagName !== m[1]) return false;
    if (!(m[2].match(/\.[\w-]+/g) || []).every((c) => this._cls.has(c.slice(1)))) return false;
    return (m[3].match(/\[[\w-]+\]/g) || []).every((a) => this.getAttribute(a.slice(1, -1)) !== null);
  }
  closest(sel) {
    const parts = String(sel).split(',').map((p) => p.trim()).filter(Boolean);
    let n = this;
    while (n && n.nodeType === 1) {
      if (parts.some((p) => n.matches(p))) return n;
      n = n.parentNode;
    }
    return null;
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
  documentElement: new Element('html'),
  listeners: {},
  createElement: (t) => new Element(t),
  createElementNS: (_ns, t) => new Element(t),
  createTextNode: (s) => new TextNode(s),
  createDocumentFragment: () => new Fragment(),
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); },
  removeEventListener() {},
  execCommand: () => true,
  getElementById(id) {
    for (const d of walk(this.body)) if (d.attrs.id === id) return d;
    return null;
  },
  querySelectorAll(sel) { return this.body.querySelectorAll(sel); },
  querySelector(sel) { return this.body.querySelector(sel); },
};

globalThis.document = DOC;
globalThis.Node = NodeBase;
globalThis.window = { innerWidth: 1200, innerHeight: 800 };
globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
globalThis.requestAnimationFrame = (fn) => { fn(); return 0; };
globalThis.matchMedia = () => ({ matches: true });
globalThis.location = { protocol: 'file:' };   // 开局先演 file:// 降级,随后切回 http:

/* 剪贴板 stub:捕获写入文本(⑤ 一键复制的断言素材) */
const clipboardWrites = [];
try {
  Object.defineProperty(globalThis, 'navigator', {
    value: { clipboard: { writeText: async (t) => { clipboardWrites.push(t); } } },
    configurable: true,
  });
} catch { /* 覆盖失败可接受:复制断言会如实红 */ }

/* MutationObserver stub:记录实例,测试手动触发回调(模拟 gallery 重渲染) */
const MO_INSTANCES = [];
globalThis.MutationObserver = class {
  constructor(cb) { this.cb = cb; MO_INSTANCES.push(this); }
  observe() {}
  disconnect() {}
};
const fireMO = () => MO_INSTANCES.forEach((mo) => mo.cb());

/* fetch stub:按脚本队列回放响应;记录每次调用的 url 与请求体 */
const fetchCalls = [];
let fetchScript = [];   // 每项 {status, payload} 或 Error
globalThis.fetch = async (url, opts = {}) => {
  fetchCalls.push({ url: String(url), opts });
  const item = fetchScript.shift();
  if (!item) throw new Error('fetch 脚本队列已空');
  if (item instanceof Error) throw item;
  return {
    ok: item.status >= 200 && item.status < 300,
    status: item.status,
    json: async () => item.payload,
  };
};

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* ---------------- 断言工具(与其余 check.mjs 同一行约定) ---------------- */
let failed = 0;
let count = 0;
function check(name, cond) {
  count += 1;
  if (cond) { console.log(`  ok  ${name}`); }
  else { failed += 1; console.error(`  FAIL ${name}`); }
}

/* ---------------- 被测模块与 DOM 素材 ---------------- */
const C = await import('./js/captions.js');
const { h } = await import('./js/dom.js');

/** gallery.js grid() 同形的最小图件卡(button.glx-card > .glx-thumb img + .glx-meta)。 */
function makeCard(name, src) {
  return h('button', { class: 'glx-card', type: 'button' },
    h('div', { class: 'glx-thumb' },
      src ? h('img', { src, alt: name }) : h('div', { class: 'demo-svg' })),
    h('div', { class: 'glx-meta' },
      h('span', { class: 'glx-name', title: name }, name),
      h('span', { class: 'glx-sub' }, '第 10 步')));
}

const SRC = (file) => `/api/artifact-file?session=sess-check&run_id=r-7&step=10&art_id=figs&file=${file}`;
const glx = h('div', { class: 'glx' },
  h('h3', { class: 'sect' }, '产物图件'),
  h('div', { class: 'glx-grid' }, makeCard('demo_scene.svg', null)));
DOC.body.appendChild(glx);

const capx = () => glx.querySelectorAll('.capx');
const rows = () => glx.querySelectorAll('.capx-item');
const rowOf = (name) => rows().find((r) => r.getAttribute('data-capx-item') === name) || null;
const btnIn = (root, text) => root.querySelectorAll('button')
  .find((b) => b.textContent.includes(text)) || null;

/* ---------------- ① 纯函数 ---------------- */
console.log('== ① 纯函数 ==');
const q = C.parseArtifactQuery(SRC('velocity_thumb.png'));
check('缩略图 URL 反解出 session/run_id',
  q && q.session === 'sess-check' && q.runId === 'r-7');
check('缺 run_id 时 runId 为空串(最新 run 语义)',
  C.parseArtifactQuery('/api/artifact-file?session=s1&art_id=x').runId === '');
check('非产物 URL / 缺 session / 空串 → null',
  C.parseArtifactQuery('/api/figures?session=s1') === null
  && C.parseArtifactQuery('/api/artifact-file?art_id=x') === null
  && C.parseArtifactQuery('') === null);
check('状态键 = session|run|图名(跨会话跨 run 不串)',
  C.captionKey({ session: 's', runId: 'r', name: 'v.png' }) === 's|r|v.png');
check('徽章:润色=蓝 is-run,骨架=绿 is-ok(非降级失败态)',
  C.badgeSpec(true).label === 'LLM 润色' && C.badgeSpec(true).cls === 'is-run'
  && C.badgeSpec(false).label === '模板骨架' && C.badgeSpec(false).cls === 'is-ok');
check('错误文案:404 图件不在账本 / 0 后端不可达 / 其余带 HTTP 码',
  C.errorText(404).includes('不在账本') && C.errorText(0).includes('后端不可达')
  && C.errorText(503).includes('HTTP 503'));
check('pickText:按语种取文,缺字段/非对象回空串',
  C.pickText({ zh: '甲', en: 'A' }, 'zh') === '甲'
  && C.pickText({ zh: '甲', en: 'A' }, 'en') === 'A'
  && C.pickText({ zh: '甲' }, 'en') === '' && C.pickText(null, 'zh') === '');
check('图注文件名:velocity.png → velocity.caption.json(多点名只换末段)',
  C.captionFileName('velocity.png') === 'velocity.caption.json'
  && C.captionFileName('a.b.png') === 'a.b.caption.json'
  && C.captionFileName('noext') === 'noext.caption.json');
const realCard = makeCard('velocity.png', SRC('velocity_thumb.png'));
const demoCard = makeCard('demo.svg', null);
check('cardInfo:真实卡反解出身份,演示卡标 demo',
  JSON.stringify(C.cardInfo(realCard)) === JSON.stringify(
    { name: 'velocity.png', session: 'sess-check', runId: 'r-7', demo: false })
  && C.cardInfo(demoCard).demo === true && C.cardInfo(demoCard).name === 'demo.svg');

/* ---------------- ② file:// + 全演示图件降级 ---------------- */
console.log('\n== ② 降级(file:// / 演示图件) ==');
C.initCaptions();
fireMO();   // 真浏览器由 MutationObserver 自动触发;stub 手动模拟 .glx 出现
await sleep(10);
check('自带容器 .capx 已注入影像画廊(不改 gallery DOM 结构)', capx().length === 1);
check('全演示图件:只给提示,零「生成图注」按钮',
  capx()[0].textContent.includes('演示模式')
  && !btnIn(capx()[0], '生成图注'));
check('降级阶段零网络请求', fetchCalls.length === 0);
C.initCaptions();
check('重复初始化幂等:仍只有一个容器', capx().length === 1);

/* ---------------- ③ 注入 + 已有 .caption.json 直接显示 ---------------- */
console.log('\n== ③ 注入与已有图注 ==');
globalThis.location.protocol = 'http:';
const SAVED = {
  run_id: 'r-7', figure: 'velocity.png',
  zh: '图 X:InSAR LOS velocity(单位:mm/yr)。goldstein 滤波(alpha=0.6)。',
  en: 'Figure X: InSAR LOS velocity (units: mm/yr). goldstein (alpha=0.6).',
  llm_polish: false,
};
fetchScript = [{ status: 200, payload: SAVED }, { status: 404, payload: { detail: '尚未生成图注' } }];
glx.querySelector('.glx-grid').replaceChildren(
  makeCard('velocity.png', SRC('velocity_thumb.png')),
  makeCard('hist.png', SRC('hist_thumb.png')));
fireMO();
await sleep(20);
check('真实图件逐图成行(2 行)', rows().length === 2);
check('已落盘图注的图直接显示(GET 命中,无需点生成)',
  fetchCalls.length === 2
  && fetchCalls[0].url.includes('/api/report/caption?')
  && fetchCalls[0].url.includes('figure=velocity.png')
  && fetchCalls[0].url.includes('run_id=r-7')
  && rowOf('velocity.png').textContent.includes('goldstein 滤波(alpha=0.6)'));
check('已有图注行注明读取自 velocity.caption.json + 按钮变「重新生成」',
  rowOf('velocity.png').textContent.includes('读取自已落盘的 velocity.caption.json')
  && !!btnIn(rowOf('velocity.png'), '重新生成'));
check('骨架徽章「模板骨架」在场',
  rowOf('velocity.png').textContent.includes('模板骨架'));
check('404 的图保持「生成图注」按钮、无图注卡',
  !!btnIn(rowOf('hist.png'), '生成图注')
  && rowOf('hist.png').querySelectorAll('.capx-card').length === 0);
fireMO();
await sleep(10);
check('观察器再触发幂等:行数不变、不再发 GET',
  rows().length === 2 && fetchCalls.length === 2);

/* ---------------- ④ 中英切换 ---------------- */
console.log('\n== ④ 中英切换 ==');
const docClick = (target) => (DOC.listeners.click || [])
  .forEach((fn) => fn({ type: 'click', target, preventDefault() {} }));
const velRow = () => rowOf('velocity.png');
const tabOf = (label) => velRow().querySelectorAll('.capx-tab')
  .find((b) => b.textContent === label);
check('默认中文页签按下,正文为中文',
  tabOf('中文').getAttribute('aria-pressed') === 'true'
  && velRow().querySelector('.capx-text').textContent === SAVED.zh);
docClick(tabOf('English'));
await sleep(10);
check('切 English:正文换英文,aria-pressed 互换',
  velRow().querySelector('.capx-text').textContent === SAVED.en
  && tabOf('English').getAttribute('aria-pressed') === 'true'
  && tabOf('中文').getAttribute('aria-pressed') === 'false');
check('正文 lang 属性随语种(en)',
  velRow().querySelector('.capx-text').getAttribute('lang') === 'en');
docClick(tabOf('中文'));
await sleep(10);
check('切回中文:正文恢复中文',
  velRow().querySelector('.capx-text').textContent === SAVED.zh);

/* ---------------- ⑤ 一键复制各自 ---------------- */
console.log('\n== ⑤ 一键复制 ==');
docClick(btnIn(velRow(), '复制英文'));
await sleep(10);
check('复制英文 → 剪贴板收到英文全文',
  clipboardWrites.length === 1 && clipboardWrites[0] === SAVED.en);
docClick(btnIn(velRow(), '复制中文'));
await sleep(10);
check('复制中文 → 剪贴板收到中文全文',
  clipboardWrites.length === 2 && clipboardWrites[1] === SAVED.zh);
check('toast 复制反馈在场(已复制英文/中文图注)',
  DOC.body.querySelectorAll('.toast').some((t) => t.textContent.includes('已复制英文图注'))
  && DOC.body.querySelectorAll('.toast').some((t) => t.textContent.includes('已复制中文图注')));

/* ---------------- ⑥ 生成流程 ---------------- */
console.log('\n== ⑥ 生成 ==');
const GEN = {
  run_id: 'r-7', figure: 'hist.png',
  zh: '图 X:LOS velocity histogram(单位:mm/yr)。色标:未记录。',
  en: 'Figure X: LOS velocity histogram (units: mm/yr). Colour scale: not recorded.',
  llm_polish: true, saved: true,
};
fetchScript = [{ status: 200, payload: GEN }];
docClick(btnIn(rowOf('hist.png'), '生成图注'));
await sleep(20);
const postCall = fetchCalls[fetchCalls.length - 1];
check('POST /api/report/caption 且 body 带 session/figure/run_id',
  postCall.url === '/api/report/caption' && postCall.opts.method === 'POST'
  && JSON.parse(postCall.opts.body).session === 'sess-check'
  && JSON.parse(postCall.opts.body).figure === 'hist.png'
  && JSON.parse(postCall.opts.body).run_id === 'r-7');
check('生成后图注卡渲染,中文正文在场',
  rowOf('hist.png').querySelector('.capx-text').textContent === GEN.zh);
check('「LLM 润色」徽章在场', rowOf('hist.png').textContent.includes('LLM 润色'));
check('落盘注明 hist.caption.json',
  rowOf('hist.png').textContent.includes('已落盘图件旁 hist.caption.json'));

/* ---------------- ⑦ 失败语义(不清掉已有结果) ---------------- */
console.log('\n== ⑦ 失败语义 ==');
fetchScript = [{ status: 404, payload: { detail: 'figure 不存在' } }];
docClick(btnIn(velRow(), '重新生成'));
await sleep(20);
check('404 → 「不在账本」提示', velRow().textContent.includes('不在账本'));
check('失败不清掉已有图注(旧中文全文仍在)',
  velRow().querySelector('.capx-text').textContent === SAVED.zh);
fetchScript = [new Error('网络断了')];
docClick(btnIn(velRow(), '重新生成'));
await sleep(20);
check('网络异常 → 「后端不可达」提示且结果保留',
  velRow().textContent.includes('后端不可达')
  && velRow().querySelector('.capx-text').textContent === SAVED.zh);

/* ---------------- ⑧ gallery 重渲染重挂 ---------------- */
console.log('\n== ⑧ 重挂不丢结果 ==');
const before = fetchCalls.length;
glx.replaceChildren(
  h('h3', { class: 'sect' }, '产物图件'),
  h('div', { class: 'glx-grid' },
    makeCard('velocity.png', SRC('velocity_thumb.png')),
    makeCard('hist.png', SRC('hist_thumb.png'))));
fireMO();
await sleep(20);
check('重渲染后容器自动重挂且仍只有一个', capx().length === 1 && rows().length === 2);
check('重挂后图注不丢、不重复请求(状态存模块级)',
  rowOf('velocity.png').querySelector('.capx-text').textContent === SAVED.zh
  && rowOf('hist.png').querySelector('.capx-text').textContent === GEN.zh
  && fetchCalls.length === before);

/* ---------------- ⑨ 卡片点击不受干扰 ---------------- */
console.log('\n== ⑨ 边界 ==');
docClick(glx.querySelectorAll('.glx-card')[0]);
await sleep(10);
check('点图件卡本体:图注区零反应零请求(灯箱行为归 gallery.js)',
  fetchCalls.length === before);
check('演示卡与真实卡混排时只有真实卡成行', rows().length === 2);

console.log(failed ? `\n${count} 项断言,${failed} 项失败` : `\n全部 ${count} 项断言通过`);
process.exit(failed ? 1 : 0);
