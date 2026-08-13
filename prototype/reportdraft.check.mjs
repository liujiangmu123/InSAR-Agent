/* ============================================================
   reportdraft 的无浏览器自查脚本(node prototype/reportdraft.check.mjs)
   用最小 DOM stub 直接 import js/reportdraft.js,断言:
   ① 纯函数:请求体组装 / 徽章文案与配色映射(是=蓝、否回退骨架=绿)/
     错误文案(404 无 run / 0 不可达 / 其他状态码);
   ② 自初始化挂载:#pane-report 出现 → 区块挂 pane 尾部,按钮与
     「未配置 LLM 也可用」注明在场;幂等重复初始化;
   ③ 生成流程:点击 → POST /api/report/draft(body 带 session)→
     渲染草稿(.shell 等宽预格式)+ 「LLM 润色:是」徽章 + 落盘注明;
   ④ 一键复制:点「复制全文」→ 剪贴板收到草稿全文 + toast 复制文案;
   ⑤ 骨架回退:llm_polish=false → 徽章「LLM 润色:否(回退骨架)」;
   ⑥ 失败语义:404 → 「还没有可生成草稿的 run」提示,且不清掉已有结果;
   ⑦ dock 重渲染 → 观察器自动重挂,已生成结果不丢失。
   只依赖 node 内建能力,零 npm 依赖(check() 约定与其余 check.mjs 一致)。
   ============================================================ */

/* ---------------- 最小 DOM stub(provview.check.mjs 同款) ---------------- */
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
};

globalThis.document = DOC;
globalThis.Node = NodeBase;
globalThis.window = { innerWidth: 1200, innerHeight: 800 };
globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
globalThis.requestAnimationFrame = (fn) => { fn(); return 0; };
globalThis.matchMedia = () => ({ matches: true });
globalThis.location = { protocol: 'http:' };

/* 剪贴板 stub:捕获写入文本(④ 一键复制的断言素材) */
const clipboardWrites = [];
try {
  Object.defineProperty(globalThis, 'navigator', {
    value: { clipboard: { writeText: async (t) => { clipboardWrites.push(t); } } },
    configurable: true,
  });
} catch { /* 覆盖失败可接受:复制断言会如实红 */ }

/* MutationObserver stub:记录实例,测试手动触发回调(模拟 dock 重渲染) */
const MO_INSTANCES = [];
globalThis.MutationObserver = class {
  constructor(cb) { this.cb = cb; MO_INSTANCES.push(this); }
  observe() {}
  disconnect() {}
};

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

/* ---------------- 被测模块 ---------------- */
const RD = await import('./js/reportdraft.js');
const { S } = await import('./js/state.js');

/* ---------------- ① 纯函数 ---------------- */
console.log('== ① 纯函数 ==');
check('请求体:缺省只带 session(最新 run)',
  JSON.stringify(RD.requestPayload('sess-a', null)) === '{"session":"sess-a"}');
check('请求体:选中历史 run 时带 run_id',
  JSON.stringify(RD.requestPayload('sess-a', 'r-42'))
  === '{"session":"sess-a","run_id":"r-42"}');
check('徽章:llm_polish=true → 「LLM 润色:是」蓝色',
  RD.badgeSpec(true).label === 'LLM 润色:是' && RD.badgeSpec(true).cls === 'is-run');
check('徽章:llm_polish=false → 「LLM 润色:否(回退骨架)」绿色(非告警色)',
  RD.badgeSpec(false).label === 'LLM 润色:否(回退骨架)'
  && RD.badgeSpec(false).cls === 'is-ok');
check('徽章 title 说明双向校验/骨架语义',
  RD.badgeSpec(true).title.includes('双向校验')
  && RD.badgeSpec(false).title.includes('骨架'));
check('错误文案:404 = 还没有可生成草稿的 run',
  RD.errorText(404).includes('还没有可生成草稿的 run'));
check('错误文案:0 = 后端不可达', RD.errorText(0).includes('后端不可达'));
check('错误文案:其他状态码带 HTTP 码', RD.errorText(500).includes('HTTP 500'));

/* ---------------- ② 自初始化挂载 ---------------- */
console.log('\n== ② 自初始化挂载 ==');
const dockBody = new Element('div');
dockBody.setAttribute('id', 'dockBody');
const pane = new Element('section');
pane.setAttribute('id', 'pane-report');
dockBody.appendChild(pane);
DOC.body.appendChild(dockBody);
RD.initReportDraft();
await sleep(10);
const mounted = () => pane.querySelectorAll('.reportdraft');
check('区块挂进报告 pane 尾部', mounted().length === 1);
const genBtn = () => pane.querySelectorAll('button')
  .find((b) => b.textContent.includes('生成方法章节草稿') || b.textContent.includes('生成中'));
check('「生成方法章节草稿」按钮在场', !!genBtn());
check('注明未配置 LLM 也可用(骨架模式)',
  mounted()[0].textContent.includes('未配置 LLM 也可用'));
RD.initReportDraft();
check('重复初始化幂等:仍只有一个区块', mounted().length === 1);

/* ---------------- ③ 生成流程(llm_polish=true) ---------------- */
console.log('\n== ③ 生成流程 ==');
S.sessionId = 'sess-check';
const DRAFT_TEXT = '本研究的 InSAR 数据处理由 InSAR-Agent 自动化流水线执行……'
  + '滤波强度 alpha=0.6,证据级别为 runnable。';
fetchScript = [{ status: 200, payload: {
  run_id: '20260813T090000-check01', draft: DRAFT_TEXT,
  llm_polish: true, facts_used: ['scenario=coseismic'], saved: true,
} }];
genBtn().click();
await sleep(10);
check('POST /api/report/draft 且 body 带 session',
  fetchCalls.length === 1 && fetchCalls[0].url === '/api/report/draft'
  && fetchCalls[0].opts.method === 'POST'
  && JSON.parse(fetchCalls[0].opts.body).session === 'sess-check');
const shell = () => mounted()[0].querySelector('.shell');
check('草稿渲染为 .shell 等宽预格式且全文在场',
  !!shell() && shell().textContent === DRAFT_TEXT);
check('徽章「LLM 润色:是」在场',
  mounted()[0].textContent.includes('LLM 润色:是'));
check('落盘注明 report_draft.md',
  mounted()[0].textContent.includes('report_draft.md'));
check('run 短标识在场', mounted()[0].textContent.includes('20260813T090000'));

/* ---------------- ④ 一键复制 ---------------- */
console.log('\n== ④ 一键复制 ==');
const copyBtn = pane.querySelectorAll('button').find((b) => b.textContent.includes('复制全文'));
check('「复制全文」按钮在场', !!copyBtn);
copyBtn.click();
await sleep(10);
check('剪贴板收到草稿全文', clipboardWrites.length === 1 && clipboardWrites[0] === DRAFT_TEXT);
const toasts = DOC.body.querySelectorAll('.toast');
check('toast 复制文案:已复制 + 可直接改写进论文',
  toasts.some((t) => t.textContent.includes('已复制方法章节草稿')
    && t.textContent.includes('可直接改写进论文')));

/* ---------------- ⑤ 骨架回退徽章 ---------------- */
console.log('\n== ⑤ 骨架回退 ==');
fetchScript = [{ status: 200, payload: {
  run_id: '20260813T090000-check01', draft: '骨架文本:证据级别为 runnable。',
  llm_polish: false, facts_used: [], saved: false,
} }];
genBtn().click();
await sleep(10);
check('徽章切换为「LLM 润色:否(回退骨架)」',
  mounted()[0].textContent.includes('LLM 润色:否(回退骨架)'));
check('落盘失败如实注明且文本仍可复制',
  mounted()[0].textContent.includes('落盘失败'));

/* ---------------- ⑥ 失败语义(404) ---------------- */
console.log('\n== ⑥ 失败语义 ==');
fetchScript = [{ status: 404, payload: { detail: 'no run' } }];
genBtn().click();
await sleep(10);
check('404 → 「还没有可生成草稿的 run」提示',
  mounted()[0].textContent.includes('还没有可生成草稿的 run'));
check('失败不清掉已有结果(上次骨架草稿仍在)',
  !!shell() && shell().textContent.includes('骨架文本'));

/* ---------------- ⑦ dock 重渲染后自动重挂 ---------------- */
console.log('\n== ⑦ 重挂不丢结果 ==');
pane.replaceChildren();   // 模拟 dock.js 重渲染报告面板(区块被清)
MO_INSTANCES.forEach((mo) => mo.cb());
await sleep(10);
check('观察器自动重挂且仍只有一个区块', mounted().length === 1);
check('重挂后已生成结果不丢失',
  !!shell() && shell().textContent.includes('骨架文本'));

console.log(failed ? `\n${count} 项断言,${failed} 项失败` : `\n全部 ${count} 项断言通过`);
process.exit(failed ? 1 : 0);
