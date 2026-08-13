/* ============================================================
   plandiff 的无浏览器自查脚本(node prototype/plandiff.check.mjs)
   最小 DOM stub 下直接 import js/plandiff.js,覆盖:
     ① diff 算法(新增/方法变更/参数变更/重跑/跳过/移除/无变化)
     ② 总结文案(canonical 句式/连续段压缩/首个计划分组)
     ③ 首个计划概览卡(11 步分组 + 诚实时长 + why 说明一次性)
     ④ 重规划 diff 卡(变化行高亮/徽章/方法新旧值)+ 无变化不刷屏
     ⑤ sessionStorage 按会话隔离(+ 内存丢失后从存储恢复)
     ⑥ 防重复挂载(install 幂等)
     ⑦ 行点击/键盘 → 流水线步骤定位(flash 高亮)
   说明:check 脚本按仓库约定放 prototype/ 顶层(check_frontend.py
   只在该层 glob *.check.mjs,五个既有脚本同款)。零 npm 依赖。
   ============================================================ */

/* ---------------- 最小 DOM stub(pipelinerail.check.mjs 同款 + 存储扩展) ---------------- */
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
    this.hidden = false;
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

/** Map 承载的 Web Storage 替身(隔离与恢复断言需要真实读写,_m 暴露给断言)。 */
const mkStorage = () => {
  const m = new Map();
  return {
    getItem: (k) => (m.has(String(k)) ? m.get(String(k)) : null),
    setItem: (k, v) => { m.set(String(k), String(v)); },
    removeItem: (k) => { m.delete(String(k)); },
    _m: m,
  };
};

globalThis.document = DOC;
globalThis.Node = NodeBase;
globalThis.window = { innerWidth: 1200, innerHeight: 800 };
globalThis.localStorage = mkStorage();
globalThis.sessionStorage = mkStorage();
globalThis.requestAnimationFrame = (fn) => { fn(); return 0; };
globalThis.matchMedia = () => ({ matches: true });

/* ---------------- 断言工具(check_frontend.py 解析 "  ok  " / "  FAIL " 行) ---------------- */
let failed = 0;
let count = 0;
function check(name, cond) {
  count += 1;
  if (cond) { console.log(`  ok  ${name}`); }
  else { failed += 1; console.error(`  FAIL ${name}`); }
}

/* ---------------- 序列化(报告用) ---------------- */
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
  const inner = n.childNodes.map((c) => serialize(c, indent + '  ')).join('');
  return `${open}\n${inner}${indent}</${n.tagName}>\n`;
}

/* ---------------- 被测模块与骨架 ---------------- */
const St = await import('./js/state.js');
const PD = await import('./js/plandiff.js');
const { S } = St;

// 聊天流骨架:#stream > .stream-inner(计划卡插入点)
const streamEl = new Element('div');
streamEl.setAttribute('id', 'stream');
const streamInner = new Element('div');
streamInner.setAttribute('class', 'stream-inner');
streamEl.appendChild(streamInner);
DOC.body.appendChild(streamEl);

const cardCount = () => streamInner.children.filter((c) => c.matches('.plandiff')).length;

/* ---------------- ① diff 算法(纯函数) ---------------- */
console.log('== ① diff 算法 ==');

const baseSnap = () => ([
  { id: 1, name: '甲', method: 'm1', params: { x: 1 }, state: 'done', stale: false },
  { id: 2, name: '乙', method: 'm2', params: { y: 2 }, state: 'done', stale: false },
  { id: 3, name: '丙', method: 'm3', params: {}, state: 'pending', stale: false },
]);

check('无变化 → 空 diff', PD.diffPlans(baseSnap(), baseSnap()).length === 0);

{
  const cur = baseSnap(); cur[0].method = 'm9';
  const d = PD.diffPlans(baseSnap(), cur);
  check('方法变更:kinds=[method] 且带 from/to',
    d.length === 1 && d[0].id === 1 && d[0].kinds.join() === 'method'
    && d[0].method.from === 'm1' && d[0].method.to === 'm9');
}
{
  const cur = baseSnap(); cur[1].params = { y: 3, z: 8 };
  const d = PD.diffPlans(baseSnap(), cur);
  check('参数变更:逐键明细(y 2→3,新增 z)',
    d.length === 1 && d[0].kinds.join() === 'params' && d[0].params.length === 2
    && d[0].params[0].key === 'y' && d[0].params[0].from === '2' && d[0].params[0].to === '3'
    && d[0].params[1].key === 'z' && d[0].params[1].from === '(无)');
}
{
  const cur = baseSnap(); cur[0].state = 'stale'; cur[0].stale = true;
  const d = PD.diffPlans(baseSnap(), cur);
  check('曾完成 → 失效:kinds=[rerun]', d.length === 1 && d[0].kinds.join() === 'rerun');
}
{
  const cur = baseSnap(); cur[0].method = 'm9'; cur[0].state = 'stale'; cur[0].stale = true;
  const d = PD.diffPlans(baseSnap(), cur);
  check('方法变更叠加重跑:kinds=[method,rerun]', d[0].kinds.join() === 'method,rerun');
}
{
  const cur = baseSnap(); cur[1].state = 'skipped';
  const d = PD.diffPlans(baseSnap(), cur);
  check('转为跳过:kinds=[skipped](不按重跑记)', d.length === 1 && d[0].kinds.join() === 'skipped');
}
{
  const cur = [...baseSnap(), { id: 4, name: '丁', method: 'm4', params: {}, state: 'pending', stale: false }];
  const d = PD.diffPlans(baseSnap(), cur);
  check('新增步骤:kinds=[added]', d.length === 1 && d[0].id === 4 && d[0].kinds.join() === 'added');
}
{
  const d = PD.diffPlans(baseSnap(), baseSnap().slice(0, 2));
  check('移除步骤:kinds=[removed] 且保留步名', d.length === 1 && d[0].id === 3
    && d[0].kinds.join() === 'removed' && d[0].name === '丙');
}
check('pending → done(正常推进)不算变化', (() => {
  const cur = baseSnap(); cur[2].state = 'done';
  return PD.diffPlans(baseSnap(), cur).length === 0;
})());

/* ---------------- ② 总结文案 ---------------- */
console.log('\n== ② 总结文案 ==');

check('连续段压缩:7..11 → 7–11 / [3] → 3 / [3,5,6] → 3、5–6',
  PD.formatRange([7, 8, 9, 10, 11]) === '7–11'
  && PD.formatRange([3]) === '3'
  && PD.formatRange([3, 5, 6]) === '3、5–6');

{
  // canonical 场景:11 步全 done,第 6 步 snaphu_mcf → icu,6–11 失效
  const prev = Array.from({ length: 11 }, (_, i) => ({
    id: i + 1, name: `步${i + 1}`, method: i === 5 ? 'snaphu_mcf' : `m${i + 1}`,
    params: {}, state: 'done', stale: false,
  }));
  const cur = prev.map((s) => (s.id < 6 ? { ...s }
    : { ...s, method: s.id === 6 ? 'icu' : s.method, state: 'stale', stale: true }));
  const d = PD.diffPlans(prev, cur);
  const text = PD.summarize(d, cur);
  check('canonical 句式:第 6 步方法 snaphu_mcf→icu', text.includes('第 6 步方法 snaphu_mcf→icu'));
  check('canonical 句式:第 7–11 步因此重跑(因果只在上游确有变更时才说)',
    text.includes('第 7–11 步因此重跑'));
  check('总结以「相比上次:」开头', text.startsWith('相比上次:'));
}
{
  // 纯失效(无上游编辑)→ 不编因果,如实说「失效重跑」
  const prev = baseSnap();
  const cur = baseSnap(); cur[0].state = 'stale'; cur[0].stale = true;
  const text = PD.summarize(PD.diffPlans(prev, cur), cur);
  check('无上游变更的重跑段 → 「失效重跑」', text.includes('第 1 步失效重跑'));
}
check('无变化文案', PD.summarize([], baseSnap()) === '相比上次:计划无变化');
{
  const cur = Array.from({ length: 11 }, (_, i) => ({
    id: i + 1, name: `步${i + 1}`, method: 'm', params: {},
    state: i < 5 ? 'done' : 'pending', stale: false,
  }));
  check('首个计划分组文案:6 步将执行,5 步复用缓存',
    PD.summarize([], cur, { first: true }) === '共 11 步:6 步将执行,5 步复用缓存');
  cur[0].state = 'skipped';
  check('有跳过步时补第三桶', PD.summarize([], cur, { first: true }).includes('1 步跳过'));
}

/* ---------------- ③ 首个计划概览卡(DOM) ---------------- */
console.log('\n== ③ 首个计划概览卡 ==');

S.sessionId = 'sessA';
St.initSteps(5);   // 1–5 done,6–11 pending(演示初始态)
const card1 = PD.onPlan({ t: 'plan', items: [] });

check('首个计划 → 概览卡插入聊天流', !!card1 && card1.isConnected && card1.matches('.plandiff'));
check('标题「执行计划 · 11 步」', card1.querySelector('.hd').textContent.includes('执行计划 · 11 步'));
check('分组徽章:6 将执行 + 5 复用缓存',
  card1.querySelectorAll('.pd-tag.k-run').length === 6
  && card1.querySelectorAll('.pd-tag.k-cache').length === 5);
check('表格 11 行(步骤号/能力/方法/变化)', card1.querySelectorAll('.pd-row').length === 11);
check('诚实时长:无本机历史 → 如实「时长未知」(§7.5 不编数)',
  card1.querySelector('.hd .sub').textContent.includes('时长未知'));
check('总结句含分组计数', card1.querySelector('.pd-sum').textContent.includes('6 步将执行,5 步复用缓存'));

const why = card1.querySelector('.pd-why');
check('「为什么有流水线」说明首次出现(可展开 + 三句话)',
  !!why && why.querySelectorAll('.why-bd p').length === 3
  && why.querySelector('summary').textContent.includes('聊天负责表达意图'));
why.querySelector('button').click();
check('点「不再显示」→ 写 localStorage 并移除说明',
  globalThis.localStorage.getItem('ia-plandiff-why-dismissed') === '1'
  && card1.querySelector('.pd-why') === null);

/* ---------------- ④ 重规划 diff 卡 + 不刷屏 ---------------- */
console.log('\n== ④ 重规划 diff 卡 ==');

{
  const n0 = cardCount();
  const r = PD.onPlan({ t: 'plan', items: [] });   // 同一会话、计划未变(turn 复用 run 的重发)
  check('计划无变化 → 不再插卡(防刷屏)', r === null && cardCount() === n0);
}

S.sessionId = 'sessC';
St.initSteps(11);                                  // 全部 done
S.steps.get(6).method = 'snaphu_mcf';              // 造 canonical 前置:第 6 步当前方法
PD.onPlan({ t: 'plan', items: [] });               // sessC 首个计划(建立基线)
St.setMethod(6, 'icu');                            // 换方法 → 6–11 级联失效
const card3 = PD.onPlan({ t: 'plan', items: [] });

check('重规划 → diff 卡(标题含变化数)', !!card3
  && card3.querySelector('.hd').textContent.includes('计划更新 · 6 处变化'));
check('人话总结 = canonical 句式',
  card3.querySelector('.pd-sum').textContent
    === '相比上次:第 6 步方法 snaphu_mcf→icu,第 7–11 步因此重跑');
check('变化行高亮 6 行(is-changed)', card3.querySelectorAll('.pd-row.is-changed').length === 6);
{
  const row6 = card3.querySelectorAll('.pd-row')[5];
  check('第 6 步行:方法旧值删除线 + 新值加重',
    row6.querySelector('.pd-old')?.textContent === 'snaphu_mcf'
    && row6.querySelector('.pd-new')?.textContent === 'icu');
  check('第 6 步行徽章:方法变更 + 重跑双徽章',
    row6.querySelectorAll('.pd-tag.k-method').length === 1
    && row6.querySelectorAll('.pd-tag.k-rerun').length === 1);
}
check('重跑徽章共 6 枚(第 6–11 步)', card3.querySelectorAll('.pd-tag.k-rerun').length === 6);
check('why 说明已读 → diff 卡不再渲染', card3.querySelector('.pd-why') === null);

/* ---------------- ⑤ sessionStorage 按会话隔离 ---------------- */
console.log('\n== ⑤ sessionStorage 会话隔离 ==');

check('两个会话各有独立存档键',
  globalThis.sessionStorage._m.has('ia-plandiff:sessA')
  && globalThis.sessionStorage._m.has('ia-plandiff:sessC'));
{
  const a = JSON.parse(globalThis.sessionStorage.getItem('ia-plandiff:sessA'));
  const c = JSON.parse(globalThis.sessionStorage.getItem('ia-plandiff:sessC'));
  check('存档内容互不污染(sessA 第 6 步仍是 3D_FULL,sessC 已是 icu)',
    a.steps.find((s) => s.id === 6).method === '3D_FULL'
    && c.steps.find((s) => s.id === 6).method === 'icu');
}
{
  S.sessionId = 'sessA';   // 回切:diff 基线必须来自 sessA 自己的存档
  const card4 = PD.onPlan({ t: 'plan', items: [] });
  check('回切会话后 diff 对比自己的基线(3D_FULL→icu,而非 sessC 的 icu→icu)',
    !!card4 && card4.querySelector('.pd-sum').textContent.includes('第 6 步方法 3D_FULL→icu'));
}
{
  // 模拟页面刷新(内存层丢失):loadPrev 从 sessionStorage 恢复
  globalThis.sessionStorage.setItem('ia-plandiff:sessZ', JSON.stringify({
    v: 1, steps: [{ id: 1, name: '恢复', method: 'mz', params: {}, state: 'done', stale: false }],
  }));
  const z = PD.loadPrev('sessZ');
  check('内存无档时从 sessionStorage 恢复基线', z?.length === 1 && z[0].method === 'mz');
  globalThis.sessionStorage.setItem('ia-plandiff:sessBad', '{损坏的 json');
  check('损坏存档按「无基线」处理不抛错', PD.loadPrev('sessBad') === null);
}

/* ---------------- ⑥ 防重复挂载 ---------------- */
console.log('\n== ⑥ 防重复挂载 ==');

{
  const ref = globalThis.window.PlanDiff;
  const again = PD.install();
  check('install 幂等:重复挂载返回同一实例', again === ref && globalThis.window.PlanDiff === ref);
  check('全局入口形状:onPlan / gotoPipelineStep',
    typeof ref.onPlan === 'function' && typeof ref.gotoPipelineStep === 'function'
    && ref.__installed === true);
}

/* ---------------- ⑦ 行点击 → 流水线定位 ---------------- */
console.log('\n== ⑦ 行点击定位流水线 ==');

// 假 dock 步骤行(真实页由 dock.js pipelineView 渲染,.pstep[data-step])
const dockBody = new Element('div');
dockBody.setAttribute('id', 'dockBody');
for (let i = 1; i <= 11; i++) {
  const row = new Element('button');
  row.setAttribute('class', 'pstep');
  row.dataset.step = String(i);
  dockBody.appendChild(row);
}
DOC.body.appendChild(dockBody);

{
  S.sessionId = 'sessC';
  const rows = card3.querySelectorAll('.pd-row');
  rows[6].click();   // 第 7 步行
  const p7 = dockBody.querySelectorAll('.pstep').find((r) => r.dataset.step === '7');
  check('点击第 7 行 → 流水线第 7 步 flash 高亮', p7.classList.contains('flash'));
  check('并同步选中步(S.selectedStep=7)', S.selectedStep === 7);
  rows[7].dispatchEvent({ type: 'keydown', key: 'Enter', preventDefault() {} });
  const p8 = dockBody.querySelectorAll('.pstep').find((r) => r.dataset.step === '8');
  check('键盘 Enter 同样可定位(第 8 步)', p8.classList.contains('flash') && S.selectedStep === 8);
}

/* ---------------- ⑧ 事件形状兼容 + 移除行渲染 ---------------- */
console.log('\n== ⑧ 事件形状兼容 ==');

{
  S.sessionId = 'sessE';
  const card6 = PD.onPlan({
    t: 'plan',
    steps: [
      { step_id: 1, capability: '数据获取', method: 'ma', params: {}, state: 'done' },
      { step_id: 6, method: 'mb', params: {}, state: 'pending' },
    ],
  });
  check('ev.steps 优先于本地快照(step_id/capability 命名兼容)',
    !!card6 && card6.querySelector('.hd').textContent.includes('执行计划 · 2 步')
    && card6.querySelectorAll('.pd-row').length === 2);
  check('name 缺失回落 STEP_DEFS(第 6 步 → 解缠)',
    card6.querySelectorAll('.pd-row')[1].textContent.includes('解缠'));
}
{
  S.sessionId = 'sessD';
  St.initSteps(11);
  PD.savePrev('sessD', [...PD.snapshotFromState(),
    { id: 12, name: '额外校验', method: 'mx', params: {}, state: 'done', stale: false }]);
  const card7 = PD.onPlan({ t: 'plan', items: [] });
  const rm = card7?.querySelector('.pd-row.is-removed');
  check('被移除的步骤渲染专行(移除徽章 + 步名,不可点击)',
    !!rm && rm.textContent.includes('额外校验')
    && rm.querySelectorAll('.pd-tag.k-removed').length === 1);
}

/* ---------------- 输出关键结构样例 ---------------- */
console.log('\n== 关键结构样例(diff 卡,节选)==\n');
const lines = serialize(card3).split('\n');
process.stdout.write(lines.slice(0, 40).join('\n') + '\n  …\n');

console.log(failed ? `\n${count} 项断言,${failed} 项失败` : `\n全部 ${count} 项断言通过`);
process.exit(failed ? 1 : 0);
