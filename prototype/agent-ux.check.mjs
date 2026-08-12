/* ============================================================
   Agent UX 第一梯队三件套的无浏览器自查脚本
   （node prototype/agent-ux.check.mjs）
   用最小 DOM stub 直接 import stream.js / queue.js，断言：
     A. 只读工具聚合组：连续探查卡建组、首张迁入、执行卡不入组、
        执行卡打断连续段、失败组默认展开并标红、孤立探查卡不建组；
     B. 排队消息 chip：入队清空输入框、chip 渲染、撤销、flush FIFO
        逐条发出、停止（phase=paused）时队列保留；
     C. 审批「不再询问」可撤销：自动通过 note 带撤销链接，点击清除
        指纹族记忆，下次同族审批重新弹卡；
     D. 产物 chip 跳转：图像/文件面板路由 + onArtifact 回退保持。
   只依赖 node 内建能力，零 npm 依赖（与 fail-demo.check.mjs 同模式）。
   ============================================================ */

/* ---------------- 最小 DOM stub（与 fail-demo.check.mjs 同源） ---------------- */
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
    this.value = ''; this.open = false; this.hidden = false;
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
  hasAttribute(k) { return k in this.attrs; }
  appendChild(n) {
    if (n instanceof Fragment) { [...n.childNodes].forEach((c) => this.appendChild(c)); return n; }
    detach(n); n.parentNode = this; this.childNodes.push(n); return n;
  }
  insertBefore(n, ref) {
    detach(n); n.parentNode = this;
    const i = this.childNodes.indexOf(ref);
    if (i < 0) this.childNodes.push(n); else this.childNodes.splice(i, 0, n);
    return n;
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
  set innerHTML(v) { this.replaceChildren(new TextNode(v)); }
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
  createElement: (t) => new Element(t),
  createElementNS: (_ns, t) => new Element(t),
  createTextNode: (s) => new TextNode(s),
  createDocumentFragment: () => new Fragment(),
  getElementById(id) {
    for (const d of walk(this.body)) if (d.attrs.id === id) return d;
    return null;
  },
};

globalThis.document = DOC;
globalThis.Node = NodeBase;
globalThis.Event = class { constructor(type, opts = {}) { this.type = type; Object.assign(this, opts); } };
globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
globalThis.requestAnimationFrame = (fn) => { fn(); return 0; };
globalThis.matchMedia = () => ({ matches: true });

/* ---------------- 序列化（报告用，svg 折叠为占位） ---------------- */
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
  const inner = n.childNodes.map((c) => serialize(c, indent + '  ')).join('');
  return `${open}\n${inner}${indent}</${n.tagName}>\n`;
}

/* ---------------- 断言工具 ---------------- */
let failed = 0;
function check(name, cond) {
  if (cond) { console.log(`  ok  ${name}`); }
  else { failed += 1; console.error(`  FAIL ${name}`); }
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* ---------------- 挂载 ---------------- */
const Stream = await import('./js/stream.js');
const Queue = await import('./js/queue.js');
const { S } = await import('./js/state.js');

const host = new Element('div');
host.setAttribute('id', 'stream');
DOC.body.appendChild(host);
const prompt = new Element('textarea');
prompt.setAttribute('id', 'prompt');
DOC.body.appendChild(prompt);
Stream.mount(host);

const inner = () => host.querySelector('.stream-inner');
const topGroups = () => inner().children.filter((c) => c.matches('details.toolgroup'));
const topTools = () => inner().children.filter((c) => c.matches('details.tool'));

/* ============================================================
   A. 只读工具聚合组
   ============================================================ */
console.log('== A. 只读工具聚合组 ==');

// A1：单张孤立探查卡（随后被消息打断）不建组，保持普通卡
const a1 = Stream.toolCall({ cmd: 'runtime/probe.py --wsl', verb: 'probe', label: '孤立探查' });
a1.finish({ exit: 0, summary: '完成' });
Stream.agentMsg('打断连续段');
check('A1 单张孤立探查卡保持顶层普通卡（不包组）',
  a1.el.parentNode === inner() && topGroups().length === 0);

// A2–A5：三张连续探查卡 → 一张组卡，首张被迁入
const b1 = Stream.toolCall({ cmd: 'runtime/probe.py --engines', verb: 'probe', label: '探测可用引擎' });
b1.log('isce2 2.6.5 ✓', 'ok');
const b2 = Stream.toolCall({ cmd: 'core/store.py --scan', verb: 'inspect', label: '扫描已有产物' });
const b3 = Stream.toolCall({ cmd: 'core/thresholds.py --audit', verb: 'inspect', label: '审计阈值' });
const group1 = topGroups()[0];
check('A2 连续探查卡聚合为一张 .toolgroup 组卡', topGroups().length === 1 && !!group1);
check('A3 首张探查卡被迁入组内（组内成员 = 3）',
  group1.querySelectorAll('.grp-body details.tool').length === 3
  && b1.el.parentNode === group1.querySelector('.grp-body'));
check('A4 组卡标题计数「探查 · 3 个工具」',
  group1.querySelector('.ttl')?.textContent === '探查 · 3 个工具');
b1.finish({ exit: 0, summary: '5 个引擎可用' });
b2.finish({ exit: 0, summary: '1–5 步可跳过' });
b3.finish({ exit: 0, summary: '3 个阈值 PENDING' });
check('A5 全成功组默认折叠且状态「完成」',
  group1.open === false && group1.querySelector('.stat')?.textContent === '完成'
  && group1.classList.contains('is-ok') && !group1.classList.contains('is-bad'));

// A6：执行步骤卡（verb [06/11]）保持顶层内联，不入组
const s6 = Stream.toolCall({ cmd: 'snaphu.py --method mcf', verb: '[06/11]', label: '解缠', open: true });
s6.log('$ snaphu.py --method mcf', 'cmd');
check('A6 执行步骤卡保持顶层内联（不进组）',
  s6.el.parentNode === inner()
  && group1.querySelectorAll('.grp-body details.tool').length === 3);

// A7–A9：执行卡打断连续段 → 新组；组内失败 → 整组默认展开并标红
const artHits = [];
s6.finish({ exit: 0, summary: '完成 · 94.2% 像元已解缠', artifacts: [
  { path: 'products/velocities/vel_ridgecrest_2019.png', hash: '08b3…77aa' },
  { path: 'provenance.json', hash: '4f21…9ac3' },
], onArtifact: (p) => artHits.push(p) });
const d1 = Stream.toolCall({ cmd: 'runtime/probe.py --credentials', verb: 'probe', label: '检查凭据' });
d1.finish({ exit: 0, summary: '1/3 凭据可用' });
const d2 = Stream.toolCall({ cmd: 'net/cds_ping.py --timeout 8', verb: 'probe', label: '探测 CDS' });
d2.log('HTTP 503 Service Unavailable', 'err');
const group2 = topGroups()[1];
check('A7 执行卡打断连续段：随后的探查卡进新组（共 2 组）',
  topGroups().length === 2 && d1.el.parentNode === group2?.querySelector('.grp-body'));
d2.finish({ exit: 7, summary: '失败 · service_down' });
check('A8 组内任一 exit≠0 → 整组默认展开', group2.open === true);
check('A9 失败组标红且状态「1 个失败」',
  group2.classList.contains('is-bad')
  && group2.querySelector('.stat')?.textContent === '1 个失败'
  && d2.el.classList.contains('is-bad') && !d1.el.classList.contains('is-bad'));

/* ============================================================
   D. 产物 chip 跳转（最小版：面板路由 + onArtifact 回退）
   ============================================================ */
console.log('\n== D. 产物 chip 跳转 ==');
check('D1 面板路由：.png/.jpg → images，其余 → files',
  Stream.artifactTab('a/vel.png') === 'images'
  && Stream.artifactTab('B/PREVIEW.JPG') === 'images'
  && Stream.artifactTab('c/ts.jpeg') === 'images'
  && Stream.artifactTab('provenance.json') === 'files'
  && Stream.artifactTab('products/timeseries.h5') === 'files');
const arts = s6.el.querySelectorAll('.arts .art');
check('D2 产物徽章渲染为可点按钮', arts.length === 2 && arts.every((b) => b.tagName === 'button'));
arts[0].click();
arts[1].click();
check('D3 点击产物徽章 → onArtifact 回退收到路径（既有 dock 打开逻辑保持）',
  JSON.stringify(artHits) === JSON.stringify([
    'products/velocities/vel_ridgecrest_2019.png', 'provenance.json']));

/* ============================================================
   B. 排队消息 chip
   ============================================================ */
console.log('\n== B. 排队消息 chip ==');
S.phase = 'idle';
prompt.value = '把第 8 步换成 GACOS';
Queue.enqueue(prompt.value);
check('B1 入队后清空输入框', prompt.value === '' && Queue.count() === 1);
Queue.enqueue('顺便导出 GeoTIFF');
const row = DOC.getElementById('queueRow');
check('B2 chip 行渲染在输入框上方（同父且先于 #prompt）',
  !!row && row.parentNode === prompt.parentNode
  && row.parentNode.childNodes.indexOf(row) < row.parentNode.childNodes.indexOf(prompt));
const chips = () => row.querySelectorAll('.queue-chip');
check('B3 每条消息一个 chip，文案「已排队：xxx」',
  chips().length === 2 && !row.hidden
  && chips()[0].querySelector('.nm')?.textContent === '已排队：把第 8 步换成 GACOS'
  && chips()[1].querySelector('.nm')?.textContent === '已排队：顺便导出 GeoTIFF');
chips()[0].querySelector('.rm').click();
check('B4 chip 上的 × 撤销对应消息，剩余顺序保持',
  Queue.count() === 1 && chips().length === 1
  && chips()[0].querySelector('.nm')?.textContent === '已排队：顺便导出 GeoTIFF');

const sent = [];
Queue.enqueue('第三条消息');
Queue.flush((t) => sent.push(t));
await sleep(15);
check('B5 flush 逐条发出：一次只发最旧一条（FIFO）',
  JSON.stringify(sent) === JSON.stringify(['顺便导出 GeoTIFF']) && Queue.count() === 1);
Queue.flush((t) => sent.push(t));
await sleep(15);
check('B6 再次 flush 发出下一条，队列清空后 chip 行隐藏',
  JSON.stringify(sent) === JSON.stringify(['顺便导出 GeoTIFF', '第三条消息'])
  && Queue.count() === 0 && row.hidden === true);

Queue.enqueue('停止后不该窜出的消息');
S.phase = 'paused';   // 停止（abort）语义：取消当前回合
Queue.flush((t) => sent.push(t));
await sleep(15);
check('B7 停止（phase=paused）时 flush 不发送，队列保留', sent.length === 2 && Queue.count() === 1);
S.phase = 'idle';
Queue.flush((t) => sent.push(t));
await sleep(15);
check('B8 恢复后 flush 继续逐条发出', sent[2] === '停止后不该窜出的消息' && Queue.count() === 0);

/* ============================================================
   C. 审批「不再询问」可撤销
   ============================================================ */
console.log('\n== C. 审批「不再询问」可撤销 ==');
S.autoApprove.add('pipeline-run:create');
let ran = 0;
const mkAsk = () => Stream.askApproval({
  title: '需确认 · 执行流水线',
  family: 'pipeline-run:create',
  rows: [['步骤', '第 6–11 步（共 6 步）']],
  actions: [
    { label: '确认执行', done: '已确认', run: () => { ran += 1; } },
    { label: '取消', done: '已取消', run: () => {} },
  ],
});
const auto = mkAsk();
const autoNote = inner().children[inner().children.length - 1];
const revokeBtn = autoNote.querySelector('.undo-link');
check('C1 同族自动通过：不渲染卡片，note 说明 + 主操作已执行',
  auto === null && ran === 1 && autoNote.matches('div.note.is-info')
  && autoNote.textContent.includes('已自动通过'));
check('C2 note 带「撤销该记忆」链接', revokeBtn?.textContent === '撤销该记忆');
revokeBtn.click();
check('C3 点击撤销 → 清除该指纹族记忆，链接置为已撤销态',
  !S.autoApprove.has('pipeline-run:create')
  && revokeBtn.textContent === '已撤销' && revokeBtn.hasAttribute('disabled'));
const again = mkAsk();
check('C4 撤销后同族审批重新弹卡（不再自动通过）',
  again !== null && again.matches('div.ask') && ran === 1);

/* ---------------- 输出关键 DOM 结构 ---------------- */
console.log('\n== 关键 DOM 结构 ==\n');
console.log('--- A. .toolgroup 聚合组（全成功 · 折叠） ---');
process.stdout.write(serialize(group1));
console.log('--- A. .toolgroup 聚合组（含失败 · 展开标红） ---');
process.stdout.write(serialize(group2));
console.log('--- B. 排队 chip 行 ---');
Queue.enqueue('示例排队消息');
process.stdout.write(serialize(row));
console.log('--- C. 自动通过 note（带撤销链接） ---');
process.stdout.write(serialize(autoNote));

console.log(failed ? `\n${failed} 项断言失败` : '\n全部断言通过');
process.exit(failed ? 1 : 0);
