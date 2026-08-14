/* ============================================================
   多轮自主循环可视化(js/agentloop.js)的无浏览器自查脚本
   (node prototype/agentloop.check.mjs)
   用最小 DOM stub 直接 import js/agentloop.js,断言五块:
     A. 纯逻辑:动作字典闭集/兜底、时长格式、mock 开关、
        进度条与工作记录卡文案、周期退出态聚合;
     B. 事件状态机:首个 agent.cycle 插进度条 → 后续周期原位更新
        (不重复插条)→ tool.start/end 记录耗时与退出态 → say 收卡
        (默认摺叠、行数=周期数)→ 收卡后新周期开新条;note 走静默窗收卡;
     C. 本地回合与停止:busy 侧影(#btnStop hidden)决定收卡时机;
        进度条「停止」触发既有 #btnStop 入口;composer 直接停止同样
        被如实记为「已被用户停止于第 n 步」;未收尾工具标「中断」;
     D. 订阅与挂载:busy 开始才建 SSE(公开口 connectEvents 真实通路)、
        会话切换换连、mountAgentLoop 幂等、dispose 后事件不再处理;
     E. mock 剧本:6 周期含 search_data/plan、say 收尾、成对 tool 事件;
        瞬时播完出 6 行工作记录卡;真实事件到达拒绝启动;中途停止让位。
   只依赖 node 内建能力,零 npm 依赖(与 bridge.check.mjs 同模式)。
   ============================================================ */

/* ---------------- 最小 DOM stub(bridge.check.mjs 同源,补 removeEventListener) ---------------- */
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
      toggle: (c, on) => { (on ?? !s.has(c)) ? s.add(c) : s.delete(c); },
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
  removeEventListener(type, fn) {
    this.listeners[type] = (this.listeners[type] || []).filter((f) => f !== fn);
  }
  dispatchEvent(evt) { [...(this.listeners[evt.type] || [])].forEach((fn) => fn.call(this, evt)); return true; }
  click() { this.dispatchEvent({ type: 'click', preventDefault() {}, target: this }); }
  focus() {} blur() {} scrollIntoView() {}
  set textContent(v) { this.replaceChildren(new TextNode(v)); }
  get textContent() { return super.textContent; }
  get children() { return this.childNodes.filter((c) => c.nodeType === 1); }
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
globalThis.location = { protocol: 'http:', search: '' };   // backend.sse.js 顶层读 protocol

const esInstances = [];
class FakeES {
  constructor(url) { this.url = url; this.readyState = 1; esInstances.push(this); }
  close() { this.readyState = 2; }
}
FakeES.CLOSED = 2;
globalThis.EventSource = FakeES;

/* ---------------- 断言工具 ---------------- */
let failed = 0;
function check(name, cond) {
  if (cond) { console.log(`  ok  ${name}`); }
  else { failed += 1; console.error(`  FAIL ${name}`); }
}
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

/* ---------------- 应用骨架:轨迹流 + composer 停止钮 ---------------- */
const stream = new Element('div'); stream.setAttribute('id', 'stream');
const inner = new Element('div'); inner.className = 'stream-inner';
stream.appendChild(inner); DOC.body.appendChild(stream);
const btnStop = new Element('button'); btnStop.setAttribute('id', 'btnStop');
btnStop.hidden = true;
let btnStopClicks = 0;
btnStop.addEventListener('click', () => { btnStopClicks += 1; });   // app.js 的 abort 入口替身
DOC.body.appendChild(btnStop);

/* ---------------- 导入被测模块(node 无 window:不会自初始化) ---------------- */
const AL = await import('./js/agentloop.js');
const St = await import('./js/state.js');

/* ============================================================
   A. 纯逻辑
   ============================================================ */
console.log('== A. 纯逻辑(动作字典/文案/时长/mock 开关) ==');

const CONTRACT_ACTIONS = ['search_data', 'inspect_file', 'check_env', 'list_data',
  'status', 'plan', 'execute', 'set_params', 'set_method', 'thinking',
  'install_engine', 'list_files', 'learn_tool', 'search_docs', 'probe_scratch'];
check('A1 契约动作闭集齐全,均有中文名与图标',
  CONTRACT_ACTIONS.every((a) => AL.ACTION_META[a]?.zh && AL.ACTION_META[a]?.ic)
  && Object.keys(AL.ACTION_META).length === CONTRACT_ACTIONS.length);

check('A2 契约外动作兜底:保留原始动作名,不抛错',
  AL.actionMeta('frobnicate').zh === 'frobnicate' && AL.actionMeta('').zh === '未知动作');

check('A3 时长格式:秒以下如实标 <1s,其余 mm:ss,非法输入为空',
  AL.fmtDur(400) === '<1s' && AL.fmtDur(7000) === '00:07'
  && AL.fmtDur(65000) === '01:05' && AL.fmtDur(-5) === '' && AL.fmtDur(NaN) === '');

// A4 界面诚实化(0814B W6):mockEnabled 加第二参数注入「演示态探测」
// (file:// 或 API 已回退 mock)——查询串开关与演示态须同时为真
check('A4 mock 开关:?agentloopmock=1 且演示态为真才启用',
  AL.mockEnabled('?agentloopmock=1', true) && AL.mockEnabled('?x=2&agentloopmock=1', true)
  && !AL.mockEnabled('?agentloopmock=0', true) && !AL.mockEnabled('', true)
  && !AL.mockEnabled('?mock=1', true));
check('A4b 真实后端存活(演示态为假/缺省):带 mock 查询串也一律拒绝',
  !AL.mockEnabled('?agentloopmock=1', false) && !AL.mockEnabled('?agentloopmock=1')
  && !AL.mockEnabled('?agentloopmock=1', 'truthy'));

check('A5 进度条文案:工作中 / 停止中两态',
  AL.barText({ stopRequested: false, n: 2, max: 6, action: 'search_data' })
    === '自主工作中 · 第 2/6 步 · 当前：搜索数据'
  && AL.barText({ stopRequested: true, n: 2, max: 6, action: 'search_data' })
    === '停止中… · 第 2/6 步');

const mk = (over) => ({ startedAt: 0, endedAt: 42000, n: 3, finalReason: 'say',
  sawSay: true, sawNote: false, cycles: [{}, {}, {}], ...over });
check('A6 工作记录卡结语:完成 / 收尾 / 被用户停止三种如实区分',
  AL.cardText(mk({})) === '工作记录 · 共 3 步 · 用时 00:42 · 已完成'
  && AL.cardText(mk({ sawSay: false, sawNote: true, finalReason: 'note' }))
    === '工作记录 · 共 3 步 · 用时 00:42 · 已收尾'
  && AL.cardText(mk({ finalReason: 'stopped' }))
    === '工作记录 · 已被用户停止于第 3 步 · 用时 00:42');

check('A7 周期退出态聚合:无工具/全成功/取首个失败/未收尾即中断',
  AL.cycleExit({ tools: [] }).cls === 'none'
  && AL.cycleExit({ tools: [{ t1: 1, exit: 0 }] }).cls === 'ok'
  && AL.cycleExit({ tools: [{ t1: 1, exit: 0 }, { t1: 2, exit: 2 }] }).label === 'exit 2'
  && AL.cycleExit({ tools: [{ t1: null, exit: null }] }).cls === 'cut');

/* ============================================================
   B. 事件状态机(cycle → 原位更新 → 收卡)
   ============================================================ */
console.log('\n== B. 事件状态机 ==');

const i1 = AL.createAgentLoop({ doc: DOC, quietMs: 30 });

check('B1 无事件零渲染:流内无条目、无活跃循环、无 SSE 连接',
  inner.children.length === 0 && i1.loop === null && esInstances.length === 0);

i1.handleEvent({ t: 'agent.cycle', n: 1, max: 6, action: 'search_data' });
const strip = inner.children[0];
check('B2 首个 agent.cycle:当前回合位置插入进度条(data-state=run)',
  inner.children.length === 1 && strip?.matches('.alp') && strip.dataset.state === 'run'
  && strip.querySelector('.alp-txt')?.textContent === '自主工作中 · 第 1/6 步 · 当前：搜索数据'
  && strip.querySelector('.alp-stop') !== null);

check('B3 aria-live 进度播报:文案节点是 polite 状态区',
  strip.querySelector('.alp-txt')?.getAttribute('aria-live') === 'polite'
  && strip.querySelector('.alp-txt')?.getAttribute('role') === 'status');

i1.handleEvent({ t: 'agent.cycle', n: 2, max: 6, action: 'plan' });
check('B4 后续周期原位更新:不重复插条,文案跟随 n/动作',
  inner.children.length === 1
  && strip.querySelector('.alp-txt')?.textContent === '自主工作中 · 第 2/6 步 · 当前：制定计划');

i1.handleEvent({ t: 'tool.start', id: 't1', name: 'plan', label: '生成执行计划' });
i1.handleEvent({ t: 'tool.end', id: 't1', exit: 0, summary: '11 步计划就绪' });
check('B5 工具事件挂到当前周期:耗时与退出态入账',
  i1.loop.cycles[1].tools.length === 1 && i1.loop.cycles[1].tools[0].exit === 0
  && i1.loop.cycles[1].tools[0].t1 !== null
  && i1.loop.cycles[1].tools[0].summary === '11 步计划就绪');
check('B5b 运行中即展开步骤清单(不等收卡)',
  strip.querySelectorAll('.alp-live .alp-row').length === 2
  && strip.querySelector('.alp-row.is-live') !== null
  && [...strip.querySelectorAll('.alp-summ')].some((n) => n.textContent.includes('11 步计划就绪')));

i1.handleEvent({ t: 'agent.cycle', n: 3, max: 6, action: 'frobnicate' });
check('B6 契约外动作不致渲染中断:兜底文案保留动作名',
  strip.querySelector('.alp-txt')?.textContent.includes('frobnicate'));

i1.handleEvent({ t: 'say', parts: ['完成'] });
const card1 = strip.querySelector('.alp-card');
check('B7 say 终止(非本地回合):进度条原位转工作记录卡',
  inner.children.length === 1 && card1 !== null && strip.dataset.state === 'done'
  && strip.querySelector('.alp-bar') === null && i1.loop === null);

check('B8 工作记录卡默认摺叠,标题含步数与「已完成」',
  !card1.hasAttribute('open')
  && card1.querySelector('.alp-ttl')?.textContent.includes('工作记录 · 共 3 步')
  && card1.querySelector('.alp-ttl')?.textContent.includes('已完成'));

const rows1 = card1.querySelectorAll('.alp-row');
check('B9 每周期一行:图标+名称+耗时;工具周期带 exit 徽章与摘要',
  rows1.length === 3
  && rows1[0].querySelector('.alp-ic') !== null
  && rows1[0].querySelector('.alp-nm')?.textContent === '第 1 步 · 搜索数据'
  && rows1[0].querySelector('.alp-du')?.textContent !== ''
  && rows1[1].querySelector('.alp-st')?.textContent === 'exit 0'
  && rows1[1].querySelector('.alp-summ')?.textContent === '11 步计划就绪'
  && rows1[0].querySelector('.alp-st') === null);

i1.handleEvent({ t: 'agent.cycle', n: 1, max: 2, action: 'check_env' });
check('B10 收卡后新周期开新条:上一张卡保留',
  inner.children.length === 2 && inner.children[1].dataset.state === 'run'
  && inner.children[0].querySelector('.alp-card') !== null);

i1.handleEvent({ t: 'note', tone: 'warn', text: '轮次预算已用尽,收尾' });
check('B11 note 到达先不收卡(等静默窗,防误吞回合中提示)',
  i1.loop !== null && inner.children[1].querySelector('.alp-bar') !== null);

await wait(60);
check('B12 静默窗后收卡:note 收尾结语「已收尾」',
  i1.loop === null
  && inner.children[1].querySelector('.alp-ttl')?.textContent.includes('已收尾'));

/* ============================================================
   C. 本地回合与停止交互
   ============================================================ */
console.log('\n== C. 本地回合与停止 ==');

inner.replaceChildren();
i1.dispose();
const i2 = AL.createAgentLoop({ doc: DOC, quietMs: 30 });

btnStop.hidden = false;          // 回合开始:composer 停止钮露出
i2._busyFlip();
i2.handleEvent({ t: 'agent.cycle', n: 1, max: 4, action: 'search_data' });
i2.handleEvent({ t: 'say', parts: ['本地回合的 say'] });
check('C1 本地回合 say 不立即收卡:等 busy 归零统一收卡',
  i2.loop !== null && i2.loop.sawSay === true && i2.loop.localBusy === true
  && inner.children[0].querySelector('.alp-bar') !== null);

btnStop.hidden = true;           // app.js finally setBusy(false)
i2._busyFlip();
check('C2 busy 归零收卡:结语「已完成」',
  i2.loop === null
  && inner.children[0].querySelector('.alp-ttl')?.textContent.includes('已完成'));

inner.replaceChildren();
btnStop.hidden = false;
i2._busyFlip();
i2.handleEvent({ t: 'agent.cycle', n: 1, max: 4, action: 'search_data' });
i2.handleEvent({ t: 'agent.cycle', n: 2, max: 4, action: 'execute' });
i2.handleEvent({ t: 'tool.start', id: 'c3t', name: 'execute', label: '[02/11] 干涉图生成' });
const clicksBefore = btnStopClicks;
const strip2 = inner.children[0];
strip2.querySelector('.alp-stop').click();
check('C3 进度条「停止」触发既有停止入口(#btnStop.click)并转停止中',
  btnStopClicks === clicksBefore + 1 && strip2.dataset.state === 'stopping'
  && strip2.querySelector('.alp-txt')?.textContent === '停止中… · 第 2/4 步'
  && strip2.querySelector('.alp-stop').disabled === true);

btnStop.hidden = true;           // 取消链路落地,busy 归零
i2._busyFlip();
const card2 = strip2.querySelector('.alp-card');
check('C4 停止收卡:如实标「已被用户停止于第 2 步」(data-state=stopped)',
  strip2.dataset.state === 'stopped'
  && card2?.querySelector('.alp-ttl')?.textContent.includes('已被用户停止于第 2 步'));

check('C5 被停止的未收尾工具如实标「中断」',
  card2.querySelectorAll('.alp-row')[1]?.querySelector('.alp-st')?.textContent === '中断');

inner.replaceChildren();
btnStop.hidden = false;
i2._busyFlip();
i2.handleEvent({ t: 'agent.cycle', n: 1, max: 4, action: 'thinking' });
btnStop.click();                 // 用户绕过进度条,直接点 composer 停止钮
btnStop.hidden = true;
i2._busyFlip();
check('C6 composer 直接停止同样如实记为用户停止',
  inner.children[0].dataset.state === 'stopped'
  && inner.children[0].querySelector('.alp-ttl')?.textContent.includes('已被用户停止于第 1 步'));

/* ============================================================
   D. 订阅与挂载
   ============================================================ */
console.log('\n== D. 订阅与挂载 ==');

inner.replaceChildren();
i2.dispose();
const i3 = AL.createAgentLoop({ doc: DOC, quietMs: 30 });

St.S.sessionId = 'chk-a';
check('D1 空闲不订阅:busy 前零 SSE 连接', esInstances.length === 0);

btnStop.hidden = false;
i3._busyFlip();
check('D2 busy 开始经公开口 connectEvents 建订阅(/api/events)',
  esInstances.length === 1 && esInstances[0].url.includes('/api/events')
  && esInstances[0].url.includes('session=chk-a'));

esInstances[0].onmessage?.({ data: JSON.stringify({ t: 'agent.cycle', n: 1, max: 3, action: 'list_data' }) });
check('D3 SSE 事件驱动渲染(真实通路):进度条出现且标记 sawReal',
  inner.children[0]?.matches('.alp') && i3.sawReal === true
  && inner.children[0].querySelector('.alp-txt')?.textContent.includes('列出数据'));

btnStop.hidden = true;
i3._busyFlip();
St.S.sessionId = 'chk-b';        // 切换会话
btnStop.hidden = false;
i3._busyFlip();
check('D4 会话切换换连:旧连接关闭,新连接指向新会话',
  esInstances.length === 2 && esInstances[0].readyState === FakeES.CLOSED
  && esInstances[1].url.includes('session=chk-b'));
btnStop.hidden = true;
i3._busyFlip();

const m1 = AL.mountAgentLoop();
const m2 = AL.mountAgentLoop();
check('D5 幂等挂载:重复 mountAgentLoop 返回同一实例并登记全局出口',
  m1 === m2 && globalThis.__insarAgentLoop === m1);

m1.dispose();
const m3 = AL.mountAgentLoop();
check('D6 dispose 后可重新挂载(新实例,全局出口更新)',
  m3 !== m1 && globalThis.__insarAgentLoop === m3);
m3.dispose();

inner.replaceChildren();
i3.dispose();
i3.handleEvent({ t: 'agent.cycle', n: 1, max: 2, action: 'plan' });
check('D7 dispose 后事件不再处理:零渲染零状态',
  inner.children.length === 0 && i3.loop === null);

/* ============================================================
   E. mock 剧本
   ============================================================ */
console.log('\n== E. mock 剧本 ==');

const cycles = AL.MOCK_SCRIPT.filter((s) => s.ev.t === 'agent.cycle');
const starts = AL.MOCK_SCRIPT.filter((s) => s.ev.t === 'tool.start');
const ends = AL.MOCK_SCRIPT.filter((s) => s.ev.t === 'tool.end');
check('E1 剧本:6 周期,含 search_data 与 plan,say 收尾',
  cycles.length === 6
  && cycles.some((s) => s.ev.action === 'search_data')
  && cycles.some((s) => s.ev.action === 'plan')
  && AL.MOCK_SCRIPT[AL.MOCK_SCRIPT.length - 1].ev.t === 'say');

check('E2 剧本:tool.start/tool.end 成对且 id 对应',
  starts.length === ends.length
  && starts.every((s) => ends.some((e) => e.ev.id === s.ev.id)));

const i4 = AL.createAgentLoop({ doc: DOC, quietMs: 30 });
const played = await AL.playMock(i4, { stepMs: 0 });
const mockCard = inner.children[0]?.querySelector('.alp-card');
check('E3 瞬时播完剧本:一张 6 行工作记录卡,结语「已完成」',
  played === true && inner.children.length === 1
  && mockCard?.querySelectorAll('.alp-row').length === 6
  && mockCard?.querySelector('.alp-ttl')?.textContent.includes('工作记录 · 共 6 步')
  && mockCard?.querySelector('.alp-ttl')?.textContent.includes('已完成'));

const i5 = AL.createAgentLoop({ doc: DOC, quietMs: 30 });
i5.sawReal = true;               // 曾收到真实事件
const refused = await AL.playMock(i5, { stepMs: 0 });
check('E4 真实事件到达过:mock 拒绝启动、不渲染',
  refused === false && inner.children.length === 1);
i5.dispose();

inner.replaceChildren();
i4.dispose();
const i6 = AL.createAgentLoop({ doc: DOC, quietMs: 30 });
let waits = 0;
const stopWait = async () => {
  waits += 1;
  if (waits === 7) inner.children[0]?.querySelector('.alp-stop')?.click();   // 播到中途按停止
};
const partial = await AL.playMock(i6, { stepMs: 1, wait: stopWait });
check('E5 mock 中途停止:让位收卡,如实标「已被用户停止于第 2 步」',
  partial === false && inner.children[0]?.dataset.state === 'stopped'
  && inner.children[0]?.querySelector('.alp-ttl')?.textContent.includes('已被用户停止于第 2 步'));
i6.dispose();

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
console.log('--- 被用户中途停止的工作记录卡 ---');
if (inner.children[0]) process.stdout.write(serialize(inner.children[0]));

console.log(failed ? `\n${failed} 项断言失败` : '\n全部断言通过');
process.exit(failed ? 1 : 0);
