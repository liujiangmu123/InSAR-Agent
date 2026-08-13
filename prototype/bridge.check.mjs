/* ============================================================
   干预回执桥（bridge.js）的无浏览器自查脚本
   （node prototype/bridge.check.mjs）
   用最小 DOM stub 直接 import js/bridge.js，断言三块：
     A. 回执文案生成：describeAction / fmtIds / fmtParams
        （改方法、改参数、复位、跳过、暂停/恢复/取消、步骤区间折叠）；
     B. 回执状态机：local → pending → queued → applied / rejected，
        非法迁移不动、SSE intervention 对账（步骤号 × 动作关键词）、
        同一变更重复提交复用原卡、本地连续编辑合并；
     C. 挂载与出口：mountBridge 幂等（重复挂载同实例、fetch 不二次包装）、
        fetch 出口生成/推进回执卡、SSE 事件推到已生效、点击卡片定位
        流水线步骤并闪烁、拒绝路径带拒因、dispose 还原 fetch。
   只依赖 node 内建能力，零 npm 依赖（与 agent-ux.check.mjs 同模式）。
   ============================================================ */

/* ---------------- 最小 DOM stub（较 agent-ux 版多支持 #id 与 [attr="v"] 选择器） ---------------- */
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
    this.scrollTop = 0; this.scrollHeight = 0; this.clientHeight = 0;
    this.value = ''; this.hidden = false;
  }
  get className() { return [...this._cls].join(' '); }
  set className(v) { this._cls = new Set(String(v).split(/\s+/).filter(Boolean)); }
  get classList() {
    const s = this._cls;
    return {
      add: (...cs) => cs.forEach((c) => s.add(c)),
      remove: (...cs) => cs.forEach((c) => s.delete(c)),
      contains: (c) => s.has(c),
    };
  }
  setAttribute(k, v) { if (k === 'class') { this.className = v; return; } this.attrs[k] = String(v); }
  getAttribute(k) { return k === 'class' ? this.className : (this.attrs[k] ?? null); }
  hasAttribute(k) { return k in this.attrs; }
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
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  dispatchEvent(evt) { (this.listeners[evt.type] || []).forEach((fn) => fn.call(this, evt)); return true; }
  click() { this.dispatchEvent({ type: 'click', preventDefault() {}, target: this }); }
  focus() {} blur() {} scrollIntoView() {}
  set textContent(v) { this.replaceChildren(new TextNode(v)); }
  get textContent() { return super.textContent; }
  get children() { return this.childNodes.filter((c) => c.nodeType === 1); }
  /** 支持 tag / .cls / #id / [attr="v"]（含 data-*）的复合简单选择器。 */
  matches(sel) {
    let s = String(sel).trim();
    const attrs = [];
    s = s.replace(/\[([\w-]+)="([^"]*)"\]/g, (_, k, v) => { attrs.push([k, v]); return ''; });
    let id = null;
    s = s.replace(/#([\w-]+)/g, (_, i) => { id = i; return ''; });
    const clss = [];
    s = s.replace(/\.([\w-]+)/g, (_, c) => { clss.push(c); return ''; });
    const tag = s.trim();
    if (tag && this.tagName !== tag) return false;
    if (id && this.attrs.id !== id) return false;
    for (const c of clss) if (!this._cls.has(c)) return false;
    for (const [k, v] of attrs) {
      const got = k.startsWith('data-') ? this.dataset[k.slice(5)] : this.attrs[k];
      if (String(got) !== v) return false;
    }
    return true;
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
  createElement: (t) => new Element(t),
  createElementNS: (_ns, t) => new Element(t),
  createTextNode: (s) => new TextNode(s),
  createDocumentFragment: () => new Fragment(),
  getElementById(id) {
    for (const d of walk(this.body)) if (d.attrs.id === id) return d;
    return null;
  },
  querySelector(sel) { return this.body.querySelector(sel); },
  addEventListener() {},
};

globalThis.document = DOC;
globalThis.Node = NodeBase;
globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
globalThis.requestAnimationFrame = (fn) => { fn(); return 0; };
globalThis.matchMedia = () => ({ matches: true });

/* ---------------- 断言工具 ---------------- */
let failed = 0;
function check(name, cond) {
  if (cond) { console.log(`  ok  ${name}`); }
  else { failed += 1; console.error(`  FAIL ${name}`); }
}
const tick = () => new Promise((r) => setTimeout(r, 0));

/* ---------------- 挂载前的网络 stub ---------------- */
const fetchLog = [];
let nextResp = null;   // (url, init) => resp；null = 默认 202
const mkResp = (status, jsonBody = {}) => ({
  ok: status >= 200 && status < 300, status,
  clone() { return this; },
  async json() { return jsonBody; },
});
const baseFetch = (url, init) => {
  fetchLog.push({ url, init });
  return Promise.resolve(nextResp ? nextResp(url, init) : mkResp(202, { accepted: true, id: 1 }));
};
globalThis.fetch = baseFetch;

const esInstances = [];
class FakeES {
  constructor(url) { this.url = url; this.readyState = 1; esInstances.push(this); }
  close() { this.readyState = 2; }
}
FakeES.CLOSED = 2;
globalThis.EventSource = FakeES;

/* ---------------- 导入被测模块（node 无 window：不会自初始化） ---------------- */
const B = await import('./js/bridge.js');
// 步骤目录不再内置演示数据:回执文案的真实步骤名(def_)需注册表快照水合
const St = await import('./js/state.js');
const { REGISTRY } = await import('../tests/js/_registry.mjs');
St.setRegistry(REGISTRY);

/* ============================================================
   A. 回执文案生成
   ============================================================ */
console.log('== A. 回执文案生成 ==');

check('A1 改方法：步骤号 + 真实步骤名 + 目标方法',
  B.describeAction({ action: 'SET_METHOD', target: '6', payload: { method: 'icu' } })
    === '第 6 步（解缠）方法改为 icu');

check('A2 改参数：嵌套 params 多键顿号连接',
  B.describeAction({ action: 'SET_PARAMS', target: '5',
                     payload: { params: { alpha: 0.6, win: 32 } } })
    === '第 5 步（滤波）参数改为 alpha=0.6、win=32');

check('A3 数组参数值折叠为逗号串',
  B.fmtParams({ subswaths: [1, 2] }) === 'subswaths=1,2');

check('A4 无步骤号动作：暂停 / 恢复 / 取消',
  B.describeAction({ action: 'PAUSE', target: 'run' }).includes('暂停运行')
  && B.describeAction({ action: 'PLAY', target: 'run' }) === '恢复运行'
  && B.describeAction({ action: 'KILL', target: 'run' }) === '取消当前运行');

check('A5 复位/跳过带步骤名',
  B.describeAction({ action: 'RESET', target: '7' }) === '第 7 步（时序反演）复位（下游将标脏重跑）'
  && B.describeAction({ action: 'SKIP', target: '3' }) === '第 3 步（配准）标记跳过');

check('A6 步骤区间折叠：连续段合并、断点分列',
  B.fmtIds([5, 6, 7, 8, 10]) === '第 5–8、10 步'
  && B.fmtIds([6]) === '第 6 步'
  && B.fmtIds([]) === '');

check('A7 未知动作兜底：不抛错、保留动作名',
  B.describeAction({ action: 'FROB', target: '3' }) === '第 3 步（配准）执行 FROB');

check('A8 nameOf 可注入（单测隔离步骤表）',
  B.describeAction({ action: 'SET_METHOD', target: '2', payload: { method: 'x' } },
                    () => '假名') === '第 2 步（假名）方法改为 x');

/* ============================================================
   B. 回执状态机
   ============================================================ */
console.log('\n== B. 回执状态机 ==');

check('B1 合法迁移链：local → pending → queued → applied',
  B.advance('local', 'pending') === 'pending'
  && B.advance('pending', 'queued') === 'queued'
  && B.advance('queued', 'applied') === 'applied');

check('B2 非法迁移原地不动：applied 不可退回，rejected 只能重试',
  B.advance('applied', 'queued') === 'applied'
  && B.advance('applied', 'rejected') === 'applied'
  && B.advance('rejected', 'queued') === 'rejected'
  && B.advance('rejected', 'pending') === 'pending');

const nameOf = (id) => ({ 6: '解缠' }[id] || '');
const led = new B.ReceiptLedger({ nameOf });
const body6 = { action: 'SET_METHOD', target: '6',
                payload: { method: 'icu' }, deliver_as: 'steer' };

const noted = led.note({ kind: 'method', stepId: 6, value: 'icu' });
check('B3 本地记录：一张 local 卡 + 人话文案',
  led.items.length === 1 && noted.state === 'local'
  && noted.text === '第 6 步（解缠）方法改为 icu'
  && B.stateLabel(noted) === '已记录 · 本地');

const noted2 = led.note({ kind: 'method', stepId: 6, value: 'snaphu_smooth' });
check('B4 本地连续编辑合并：同步骤改两次方法仍是一张卡，文案取最新',
  led.items.length === 1 && noted2 === noted
  && noted.text === '第 6 步（解缠）方法改为 snaphu_smooth');

led.note({ kind: 'method', stepId: 6, value: 'icu' });   // 改回目标值再提交
const sub = led.beginSubmit(body6);
check('B5 提交复用本地卡：不建新卡，状态 → pending（提交中）',
  sub === noted && led.items.length === 1 && sub.state === 'pending'
  && B.stateLabel(sub) === '提交中…' && sub.deliver === 'steer');

led.settleSubmit(sub, { ok: true });
check('B6 202 受理 → queued（已入队）', sub.state === 'queued' && B.stateLabel(sub) === '已入队');

const evOther = { t: 'intervention', text: '第 9 步方法改为 gacos,影响 2 步', affected: [9, 10] };
check('B7 对账不误伤：别的步骤的 intervention 匹配不到本卡',
  B.matchIntervention(led.items, evOther) === null && led.applyEvent(evOther) === null);

const ev6 = { t: 'intervention', text: '第 6 步方法改为 icu,影响 3 步(method_changed)',
              affected: [6, 7, 8], mode: 'steer' };
check('B8 intervention 对账（affected 含步骤号 + 动作关键词）→ applied（已生效）',
  led.applyEvent(ev6) === sub && sub.state === 'applied' && B.stateLabel(sub) === '已生效');

check('B9 applied 是终态：同一事件二次到达不再匹配',
  led.applyEvent(ev6) === null && sub.state === 'applied');

const led2 = new B.ReceiptLedger({ nameOf });
const r2 = led2.beginSubmit(body6);
check('B10 无本地卡的直接提交（页面重载后 syncConfig）：自动建卡',
  led2.items.length === 1 && r2.state === 'pending');
led2.settleSubmit(r2, { ok: false, detail: '未知投递语义 later' });
check('B11 4xx/网络错误 → rejected（被拒绝）+ 拒因入卡',
  r2.state === 'rejected' && B.stateLabel(r2) === '被拒绝'
  && r2.reason === '未知投递语义 later');
const r2b = led2.beginSubmit(body6);
check('B12 拒绝后重试同一变更：复用原卡重走一轮（rejected → pending）',
  r2b === r2 && led2.items.length === 1 && r2.state === 'pending' && r2.reason === '');
led2.settleSubmit(r2, { ok: true });
const r2c = led2.beginSubmit(body6);
check('B13 已入队后的重复提交（每次运行重发差异）：仍复用原卡不刷屏',
  r2c === r2 && led2.items.length === 1 && r2.state === 'pending');

const led3 = new B.ReceiptLedger({ nameOf });
const pauseCard = led3.beginSubmit({ action: 'PAUSE', target: 'run', payload: {} });
led3.settleSubmit(pauseCard, { ok: true });
check('B14 无步骤号动作按关键词对账：「已暂停」命中 PAUSE 卡',
  led3.applyEvent({ t: 'intervention', text: '已暂停', affected: [] }) === pauseCard
  && pauseCard.state === 'applied');

/* ============================================================
   C. 挂载与出口（幂等挂载 / fetch 出口 / SSE / 点击定位）
   ============================================================ */
console.log('\n== C. 挂载与出口 ==');

// 轨迹流 + dock 流水线行的最小骨架
const stream = new Element('div'); stream.setAttribute('id', 'stream');
const inner = new Element('div'); inner.className = 'stream-inner';
stream.appendChild(inner); DOC.body.appendChild(stream);
const dock = new Element('aside'); dock.setAttribute('id', 'dock');
const row = new Element('div'); row.className = 'pstep'; row.dataset.step = '6';
dock.appendChild(row); DOC.body.appendChild(dock);
const tabBtn = new Element('button'); tabBtn.setAttribute('id', 'tab-pipeline');
let tabClicks = 0; tabBtn.addEventListener('click', () => { tabClicks += 1; });
DOC.body.appendChild(tabBtn);

const b1 = B.mountBridge();
const wrappedFetch = globalThis.fetch;
const b2 = B.mountBridge();

check('C1 幂等挂载：重复 mountBridge 返回同一实例，全局出口就位',
  b1 === b2 && globalThis.Bridge === b1 && globalThis.__insarBridge === b1);

check('C2 fetch 只包装一次：二次挂载不叠包，原函数可还原',
  wrappedFetch.__insarBridge === true && globalThis.fetch === wrappedFetch
  && wrappedFetch.__insarOrig !== wrappedFetch);

check('C3 SSE 通道已开（/api/events 订阅 intervention）',
  esInstances.length === 1 && esInstances[0].url.includes('/api/events'));

b1.noteIntervention({ kind: 'method', stepId: 6, value: 'icu' });
const card = inner.children[0];
check('C4 noteIntervention → 本地回执卡入流（状态色带 local）',
  inner.children.length === 1 && card?.matches('.brg-receipt')
  && card.dataset.state === 'local'
  && card.textContent.includes('第 6 步（解缠）方法改为 icu')
  && card.textContent.includes('已记录 · 本地'));

// 模拟 backend.sse.js syncConfig 的既有提交点（零改造经过包装后的 fetch）
await globalThis.fetch('/api/actions', {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ session: 's', scope: 'step', target: '6',
                         action: 'SET_METHOD', payload: { method: 'icu' },
                         deliver_as: 'steer' }),
});
await tick(); await tick();
check('C5 既有 fetch 提交点零改造接入：原卡推进到已入队 + 投递语义上卡',
  inner.children.length === 1 && card.dataset.state === 'queued'
  && card.textContent.includes('已入队') && card.textContent.includes('立即生效'));

esInstances[0].onmessage?.({ data: JSON.stringify({
  t: 'intervention', text: '第 6 步方法改为 icu,影响 3 步(method_changed)',
  affected: [6, 7, 8], mode: 'steer' }) });
check('C6 SSE intervention 到达 → 已生效', card.dataset.state === 'applied'
  && card.textContent.includes('已生效'));

card.querySelector('button.brg-body').click();
check('C7 点击回执卡 → 切流水线页签 + 对应步骤行闪烁高亮',
  tabClicks === 1 && row.classList.contains('flash'));

nextResp = () => mkResp(400, { detail: 'SKIP 的 target 必须是步骤号,收到 \'x\'' });
await globalThis.fetch('/api/actions', {
  method: 'POST', body: JSON.stringify({ session: 's', scope: 'step', target: '7',
                                         action: 'SKIP', payload: {}, deliver_as: 'steer' }),
});
await tick(); await tick();
const card2 = inner.children[1];
check('C8 被拒绝路径：新卡标 rejected + 拒因上卡',
  inner.children.length === 2 && card2.dataset.state === 'rejected'
  && card2.textContent.includes('被拒绝') && card2.textContent.includes('拒因：SKIP 的 target'));

nextResp = null;
await globalThis.fetch('/api/abort', { method: 'POST', body: JSON.stringify({ session: 's' }) });
await tick(); await tick();
const card3 = inner.children[2];
check('C9 停止请求（/api/abort）：建卡且 202 即「已受理」',
  card3?.textContent.includes('停止当前运行') && card3.dataset.state === 'applied'
  && card3.textContent.includes('已受理'));

b1.noteIntervention({ kind: 'run', ids: [6, 7, 8] });
const card4 = inner.children[3];
await globalThis.fetch('/api/pipeline', { method: 'POST', body: JSON.stringify({ session: 's' }) });
await tick(); await tick();
check('C10 执行回执：run 卡随 /api/pipeline 流开始推进到「已开跑」',
  card4?.textContent.includes('执行流水线：第 6–8 步（共 3 步）')
  && card4.dataset.state === 'applied' && card4.textContent.includes('已开跑'));

const before = fetchLog.length;
await b1.submitAction({ target: 6, action: 'RESET' });
await tick(); await tick();
const card5 = inner.children[4];
check('C11 统一出口 submitAction：POST /api/actions + 回执卡自动生成',
  fetchLog.length === before + 1 && fetchLog[fetchLog.length - 1].url === '/api/actions'
  && fetchLog[fetchLog.length - 1].init.body.includes('"action":"RESET"')
  && card5?.textContent.includes('复位') && card5.dataset.state === 'queued');

b1.dispose();
const b3 = B.mountBridge();
check('C12 dispose 还原 fetch 后可重新挂载（新实例、重新包装）',
  b3 !== b1 && globalThis.Bridge === b3
  && globalThis.fetch !== wrappedFetch && globalThis.fetch.__insarBridge === true);
b3.dispose();
check('C13 dispose 彻底还原：fetch 回到原函数、全局出口清空',
  globalThis.fetch === baseFetch && !globalThis.Bridge && !globalThis.__insarBridge);

/* ---------------- 输出关键 DOM 结构 ---------------- */
function serialize(n, indent = '') {
  if (n.nodeType === 3) {
    const s = n.data.trim();
    return s ? `${indent}${s}\n` : '';
  }
  if (n.tagName === 'svg') return `${indent}<svg …/>\n`;
  const attrs = [];
  if (n._cls.size) attrs.push(`class="${n.className}"`);
  for (const [k, v] of Object.entries(n.attrs)) attrs.push(v === '' ? k : `${k}="${v}"`);
  for (const [k, v] of Object.entries(n.dataset)) attrs.push(`data-${k}="${v}"`);
  const open = `${indent}<${n.tagName}${attrs.length ? ' ' + attrs.join(' ') : ''}>`;
  if (!n.childNodes.length) return `${open}</${n.tagName}>\n`;
  const innerTxt = n.childNodes.map((c) => serialize(c, indent + '  ')).join('');
  return `${open}\n${innerTxt}${indent}</${n.tagName}>\n`;
}

console.log('\n== 关键 DOM 结构 ==\n');
console.log('--- 已生效的干预回执卡 ---');
process.stdout.write(serialize(card));
console.log('--- 被拒绝的干预回执卡 ---');
process.stdout.write(serialize(card2));

console.log(failed ? `\n${failed} 项断言失败` : '\n全部断言通过');
process.exit(failed ? 1 : 0);
