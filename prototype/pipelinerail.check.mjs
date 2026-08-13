/* ============================================================
   pipelinerail 的无浏览器自查脚本(node prototype/pipelinerail.check.mjs)
   用最小 DOM stub 直接 import pipelinerail.js,喂假 steps 数据,
   断言四个组件的关键结构:依赖轨道 rail(节点/边/状态类名/重算 y)、
   失效原因 popover(开合/因果链)、重跑影响确认卡(勾选/确认回调)、
   摘要条(计数/跳转)。只依赖 node 内建能力,零 npm 依赖。
   ============================================================ */

/* ---------------- 最小 DOM stub(fail-demo.check.mjs 同款 + 浮层所需扩展) ---------------- */
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
    this.offsetTop = 0; this.offsetHeight = 0; this.offsetWidth = 0;
    this.value = ''; this.open = false; this.checked = false; this.disabled = false;
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
  contains(n) { while (n) { if (n === this) return true; n = n.parentNode; } return false; }
  getBoundingClientRect() {
    return { top: this.offsetTop, left: 12, bottom: this.offsetTop + this.offsetHeight,
             right: 40, width: 28, height: this.offsetHeight };
  }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  removeEventListener(type, fn) { this.listeners[type] = (this.listeners[type] || []).filter((f) => f !== fn); }
  dispatchEvent(evt) {
    evt.target ||= this; evt.currentTarget = this;
    (this.listeners[evt.type] || []).forEach((fn) => fn.call(this, evt));
    return true;
  }
  click() { this.dispatchEvent({ type: 'click', preventDefault() {}, target: this }); }
  focus() {} blur() {} scrollIntoView() {}
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
  removeEventListener(type, fn) { this.listeners[type] = (this.listeners[type] || []).filter((f) => f !== fn); },
  getElementById(id) {
    for (const d of walk(this.body)) if (d.attrs.id === id) return d;
    return null;
  },
};

/** 触发 document 级监听(浮层的 Esc / 点外关闭走这里)。 */
function docFire(type, evt = {}) {
  evt.type = type;
  [...(DOC.listeners[type] || [])].forEach((fn) => fn(evt));
}

globalThis.document = DOC;
globalThis.Node = NodeBase;
globalThis.window = { innerWidth: 1200, innerHeight: 800 };
globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
globalThis.requestAnimationFrame = (fn) => { fn(); return 0; };
globalThis.matchMedia = () => ({ matches: true });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* ---------------- 序列化(报告用;本脚本要看 SVG,不折叠) ---------------- */
function serialize(n, indent = '') {
  if (n.nodeType === 3) {
    const s = n.data.trim();
    return s ? `${indent}${s}\n` : '';
  }
  const attrs = [];
  if (n._cls.size) attrs.push(`class="${n.className}"`);
  for (const [k, v] of Object.entries(n.attrs)) attrs.push(v === '' ? k : `${k}="${v}"`);
  for (const [k, v] of Object.entries(n.dataset)) attrs.push(`data-${k}="${v}"`);
  if (n.tagName === 'input') attrs.push(`checked=${n.checked}${n.disabled ? ' disabled' : ''}`);
  const open = `${indent}<${n.tagName}${attrs.length ? ' ' + attrs.join(' ') : ''}>`;
  if (!n.childNodes.length) return `${open}</${n.tagName}>\n`;
  const inner = n.childNodes.map((c) => serialize(c, indent + '  ')).join('');
  return `${open}\n${inner}${indent}</${n.tagName}>\n`;
}

/* ---------------- 断言工具 ---------------- */
let failed = 0;
let count = 0;
function check(name, cond) {
  count += 1;
  if (cond) { console.log(`  ok  ${name}`); }
  else { failed += 1; console.error(`  FAIL ${name}`); }
}

/* ---------------- 被测模块与假数据 ---------------- */
const R = await import('./js/pipelinerail.js');
// 步骤目录不再内置演示数据:用真实 /api/registry 快照水合(拓扑与服务端一致),
// railEdges()/upstreamChain() 的缺省 defs 才有边可推
const St = await import('./js/state.js');
const { REGISTRY } = await import('../tests/js/_registry.mjs');
St.setRegistry(REGISTRY);

/* 11 步假状态,覆盖全部展示态:
   1 done · 2 skipped(云端) · 3 done · 4 running · 5 stale(参数变更,变更源)
   6/7 stale(上游级联) · 8 pending · 9 failed · 10/11 pending */
const NAMES = ['数据获取', '辅助数据', '配准', '干涉', '滤波', '解缠',
               '时序反演', '误差校正', '形变模型', '出图导出', '质检'];
const FAKE = [
  { state: 'done' }, { state: 'skipped' }, { state: 'done' }, { state: 'running' },
  { state: 'stale', stale: true, staleReason: 'param_changed' },
  { state: 'stale', stale: true, staleReason: 'upstream_changed' },
  { state: 'stale', stale: true, staleReason: 'upstream_changed' },
  { state: 'pending' }, { state: 'failed' }, { state: 'pending' }, { state: 'pending' },
].map((s, i) => ({ id: i + 1, name: NAMES[i], stale: false, staleReason: null,
                   fingerprint: `f${i + 1}a2…9c`, ...s }));

/* 假步骤行:.pstep[data-step],行高 32、行距 40(offsetTop 实测的替身) */
const wrap = new Element('div');
wrap.setAttribute('class', 'plr-wrap');
for (const s of FAKE) {
  const row = new Element('button');
  row.setAttribute('class', 'pstep');
  row.dataset.step = String(s.id);
  row.offsetTop = (s.id - 1) * 40;
  row.offsetHeight = 32;
  wrap.appendChild(row);
}
DOC.body.appendChild(wrap);

/* ---------------- ① 依赖轨道 rail ---------------- */
console.log('== ① 依赖轨道 rail ==');
check('拓扑边 = 10 主干 + 2 跨步(与 registry deps 一致)',
  R.railEdges().length === 12);
check('跨步边恰为 1→3 与 7→11',
  JSON.stringify(R.railEdges().filter(([a, b]) => b - a !== 1)) === JSON.stringify([[1, 3], [7, 11]]));
check('upstreamChain(11) 追到全部 10 个上游',
  JSON.stringify(R.upstreamChain(11)) === JSON.stringify([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]));
check('upstreamChain(3) = [1,2](跨步边 1→3 生效)',
  JSON.stringify(R.upstreamChain(3)) === JSON.stringify([1, 2]));

let picked = null;
const svg = R.mountRail(wrap, FAKE, { flashMs: 25, onPick: (id, chain) => { picked = [id, chain]; } });
check('rail 挂载为 wrap 首个子节点(列表左缘)', wrap.childNodes[0] === svg);
check('SVG aria-hidden=true(语义仍由 DOM 列表承担)', svg.getAttribute('aria-hidden') === 'true');
const nodes = svg.querySelectorAll('.rn');
check('节点数 = 11', nodes.length === 11);
check('边元素数 = 12', svg.querySelectorAll('.re').length === 12);
check('跨步边画为绕行弧(path.rc × 2)', svg.querySelectorAll('.rc').length === 2);

const nodeOf = (id) => nodes.find((g) => Number(g.dataset.node) === id);
check('状态类名:done 实心 / skipped 第三态 / running 脉冲 / stale / failed / pending',
  nodeOf(1).matches('.is-done') && nodeOf(2).matches('.is-skip') && nodeOf(4).matches('.is-run')
  && nodeOf(5).matches('.is-stale') && nodeOf(9).matches('.is-fail') && nodeOf(10).matches('.is-pend'));
check('skipped 节点带 ↷ 字形(缓存/云端,不只靠颜色)',
  nodeOf(2).querySelector('.rg')?.textContent === '↷');
check('stale/failed 节点字形 ! / ✗',
  nodeOf(5).querySelector('.rg')?.textContent === '!' && nodeOf(9).querySelector('.rg')?.textContent === '✗');
check('节点 y = 行 offsetTop + 行高/2(第 6 行 → 216)',
  nodeOf(6).querySelector('circle').getAttribute('cy') === '216');

// refresh 后行位变化 → 重画时按实际 offsetTop 重算 y(调研 §4.7 的坑)
wrap.querySelectorAll('.pstep').find((r) => r.dataset.step === '11').offsetTop = 800;
const svg2 = R.renderRail(wrap, FAKE, {});
check('行位变化后重画,节点 y 按新 offsetTop 重算(第 11 行 → 816)',
  svg2.querySelectorAll('.rn').find((g) => g.dataset.node === '11')
    .querySelector('circle').getAttribute('cy') === '816');

// 点击节点 → 上游链高亮 1.5s(测试注入 25ms)
nodeOf(7).click();
check('点击 #7:上游链 {1..6} + 自身高亮,其余降透明',
  nodeOf(7).matches('.hl') && nodeOf(3).matches('.hl') && nodeOf(1).matches('.hl')
  && nodeOf(8).matches('.dim') && nodeOf(11).matches('.dim'));
check('跨步边 1→3 在链内同步高亮',
  svg.querySelectorAll('.rc').find((e) => e.dataset.to === '3').matches('.hl'));
check('onPick 回调携带 (id, 上游链)',
  picked && picked[0] === 7 && JSON.stringify(picked[1]) === JSON.stringify([1, 2, 3, 4, 5, 6]));
await sleep(70);
check('高亮到时自动清除(flashMs 注入 25ms)',
  !nodeOf(7).matches('.hl') && !nodeOf(8).matches('.dim'));

/* ---------------- ② 失效原因解析 + popover ---------------- */
console.log('\n== ② 失效原因 popover(Dagster 式)==');
check('五类闭集 → 三段指纹映射(task/args/eval + artifact)',
  R.parseStaleReason('method_changed').seg === 'task'
  && R.parseStaleReason('tool_upgraded').seg === 'task'
  && R.parseStaleReason('param_changed').seg === 'args'
  && R.parseStaleReason('upstream_changed').seg === 'eval'
  && R.parseStaleReason('artifact_missing').seg === 'artifact');
check('未知原因回落保守解释', R.parseStaleReason('???').label === '原因待定');
const cc = R.staleCauseChain(7, FAKE);
check('因果链:#7 上游失效 = [6,5](近→远),变更源 root = #5(param_changed)',
  JSON.stringify(cc.chain) === JSON.stringify([6, 5]) && cc.root === 5);

const gotoCalls = [], impactCalls = [];
const anchor = wrap.querySelector('.pstep');
const pop = R.openStalePopover({
  anchor, step: FAKE[6], steps: FAKE,
  onGoto: (id) => gotoCalls.push(id), onImpact: (id) => impactCalls.push(id),
});
check('popover 挂到 body 且含步骤标题', pop.isConnected && pop.querySelector('.hd').textContent.includes('第 7 步 · 时序反演'));
check('eval 段徽标 + 上游级联标签', pop.querySelector('.chip')?.matches('.seg-eval')
  && pop.querySelector('.seg b')?.textContent === '上游级联'
  && pop.querySelector('.note').textContent.includes('eval 段指纹变化'));
check('因果链渲染变更源链接「#5 滤波」', pop.querySelector('.chain .lnk')?.textContent === '#5 滤波');
pop.querySelector('.chain .lnk').click();
check('点击变更源 → onGoto(5) 且 popover 关闭', gotoCalls[0] === 5 && !pop.isConnected);

const pop2 = R.openStalePopover({ anchor, step: FAKE[4], steps: FAKE, onImpact: (id) => impactCalls.push(id) });
check('param_changed → args 段「科学参数变更」', pop2.querySelector('.chip')?.matches('.seg-args'));
pop2.querySelectorAll('.ft button')[0].click();
check('「查看影响」→ onImpact(5) 且关闭', impactCalls[0] === 5 && !pop2.isConnected);

const pop3 = R.openStalePopover({ anchor, step: FAKE[4], steps: FAKE });
docFire('keydown', { key: 'Escape' });
check('Esc 关闭 popover', !pop3.isConnected);
const pop4 = R.openStalePopover({ anchor, step: FAKE[4], steps: FAKE });
docFire('pointerdown', { target: DOC.body });
check('点外关闭 popover', !pop4.isConnected);

/* ---------------- ③ 重跑影响集确认卡 ---------------- */
console.log('\n== ③ 重跑影响确认卡(Airflow 式)==');
const li = R.localImpact(5, FAKE);
check('本地影响估算 = 自身 + deps 闭包全部下游 [5..11]',
  JSON.stringify(li.affected.map((a) => a.step_id)) === JSON.stringify([5, 6, 7, 8, 9, 10, 11]));
check('本地无历史样本 → 时长 null(不编数,§7.5)', li.rerunMinutes === null);

let confirmed = null;
const imp = {
  changedStep: 5, reason: 'param_changed',
  affected: [
    { step_id: 5, reason: 'param_changed', state_before: 'stale' },
    { step_id: 6, reason: 'upstream_changed', state_before: 'done' },
    { step_id: 7, reason: 'upstream_changed', state_before: 'pending' },
  ],
  rerunMinutes: 41.6, rerunBasis: '基于本机历史运行中位数(2 步)',
};
const card = R.openImpactCard({ step: FAKE[4], impact: imp, onConfirm: (ids) => { confirmed = ids; } });
check('确认卡 + 遮罩挂载,aria-modal 对话框', card.isConnected
  && card.getAttribute('aria-modal') === 'true' && !!DOC.body.querySelector('.plr-scrim'));
const rows = card.querySelectorAll('.imp-row');
check('影响集 3 行全部列出(步名 + 状态 + 原因)', rows.length === 3
  && rows[1].textContent.includes('解缠') && rows[1].textContent.includes('已完成 · 将重算')
  && rows[1].textContent.includes('上游级联'));
check('默认全部勾选(必需集合),触发步不可摘除',
  rows.every((r) => r.querySelector('input').checked) && rows[0].querySelector('input').disabled);
check('预估时长渲染「约 42 分钟」+ 依据', card.querySelector('.eta').textContent.includes('约 42 分钟')
  && card.querySelector('.eta').textContent.includes('中位数'));
check('数据源徽标 = 服务端指纹推导', card.querySelector('.chip')?.matches('.srv'));

const box7 = rows[2].querySelector('input');
box7.checked = false;
box7.dispatchEvent({ type: 'change' });
const cbtn = card.querySelectorAll('.ft button')[0];
check('取消勾选 #7 → 确认按钮联动为「确认重跑 2 步」', cbtn.textContent === '确认重跑 2 步');
cbtn.click();
check('确认 → onConfirm([5,6])(升序)且卡片关闭',
  JSON.stringify(confirmed) === JSON.stringify([5, 6]) && !card.isConnected
  && !DOC.body.querySelector('.plr-scrim'));

const card2 = R.openImpactCard({ step: FAKE[4], impact: { ...imp, rerunMinutes: null, rerunBasis: '步骤 6 历史运行不足 3 次,时长未知' }, onConfirm: () => {} });
check('rerunMinutes=null → 如实显示「时长未知」', card2.querySelector('.eta').textContent.includes('时长未知'));
check('本地估算徽标(local 标注)', R.openImpactCard({ step: FAKE[4], impact: li, local: true, onConfirm: () => {} })
  .querySelector('.chip')?.matches('.local'));
R.closeOverlay();

/* ---------------- ④ 摘要条 ---------------- */
console.log('\n== ④ 摘要条(Seqera 状态计数)==');
const c = R.summarize(FAKE);
check('计数口径:2 完成 · 1 缓存/跳过 · 3 失效 · 3 待跑 · 1 运行中 · 1 失败',
  c.done === 2 && c.skipped === 1 && c.stale === 3 && c.pending === 3
  && c.running === 1 && c.failed === 1 && c.resume === 0);
const jumps = [];
const bar = R.summaryBar(FAKE, { onJump: (id) => jumps.push(id) });
const chips = bar.querySelectorAll('.plr-chip');
check('四个常驻计数 + 非零附加(运行中/失败)= 6 chips', chips.length === 6);
check('chip 文案「3 失效」', chips.find((x) => x.matches('.is-stale')).textContent === '3 失效');
chips.find((x) => x.matches('.is-stale')).click();
chips.find((x) => x.matches('.is-skipped')).click();
check('点击计数 → 跳到首个对应步骤(失效→#5,缓存→#2)',
  JSON.stringify(jumps) === JSON.stringify([5, 2]));
const zeroBar = R.summaryBar(FAKE.map((s) => ({ ...s, state: 'done', stale: false })), {});
check('零计数 chip 置灰禁用(zero)', zeroBar.querySelectorAll('.plr-chip')
  .filter((x) => x.matches('.zero')).length === 3);

/* ---------------- 输出关键 DOM / SVG 结构 ---------------- */
console.log('\n== 关键结构样例 ==\n');
console.log('--- ① rail SVG(节选:前 3 节点 + 跨步弧)---');
const svgLines = serialize(svg).split('\n');
process.stdout.write(svgLines.slice(0, 24).join('\n') + '\n  …\n');
console.log('--- ② 失效原因 popover ---');
const popDemo = R.openStalePopover({ anchor, step: FAKE[6], steps: FAKE });
process.stdout.write(serialize(popDemo));
R.closeOverlay();
console.log('--- ③ 重跑影响确认卡 ---');
const cardDemo = R.openImpactCard({ step: FAKE[4], impact: imp, onConfirm: () => {} });
process.stdout.write(serialize(cardDemo));
R.closeOverlay();
console.log('--- ④ 摘要条 ---');
process.stdout.write(serialize(bar));

console.log(failed ? `\n${count} 项断言,${failed} 项失败` : `\n全部 ${count} 项断言通过`);
process.exit(failed ? 1 : 0);
