/* ============================================================
   下一步建议卡(advisor.js)的无浏览器自查脚本
   (node prototype/advisor.check.mjs)
   用最小 DOM stub 直接 import advisor.js,断言五组行为:
     A. 终态嗅探:result / gate_stop / note[bad]失败 / note[warn]取消
        触发,执行噪音(step.stage / tool.* / 普通 note)不触发;
     B. 建议卡 DOM:标题 + why + 按钮按 action.kind 生成,
        data-advisor-run 标记与来源标注;
     C. 动作分派:chat_prefill 只填输入框不发送(可重复点),
        open_tab 透传 tab/step,api_action 防重复触发 + 失败可重试;
     D. 看门人去重:同 run 只插一次(evaluate 竞态 / 非终态 / 空建议
        / 换 run 各分支),防抖后只查一次权威状态;
     E. DOM 级查重兜底:同 run 卡片二次插入被拒。
   只依赖 node 内建能力,零 npm 依赖(DOM stub 与 notify.check.mjs 同源)。
   ============================================================ */

/* ---------------- 最小 DOM stub(与 notify.check.mjs 同源) ---------------- */
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
    this.value = ''; this.open = false; this.hidden = false; this.disabled = false;
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
  readyState: 'complete',
  createElement: (t) => new Element(t),
  createElementNS: (_ns, t) => new Element(t),
  createTextNode: (s) => new TextNode(s),
  createDocumentFragment: () => new Fragment(),
  getElementById(id) {
    for (const d of walk(this.body)) if (d.attrs.id === id) return d;
    return null;
  },
  querySelector(sel) { return this.body.querySelector(sel); },
  querySelectorAll(sel) { return this.body.querySelectorAll(sel); },
  addEventListener() {},
};

globalThis.document = DOC;
globalThis.Node = NodeBase;
globalThis.Event = class { constructor(type, opts = {}) { this.type = type; Object.assign(this, opts); } };
globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
globalThis.requestAnimationFrame = (fn) => { fn(); return 0; };
globalThis.matchMedia = () => ({ matches: true });

/* 真实页面骨架先于模块导入放进 DOM(自初始化要找 #stream / #prompt) */
const stream = new Element('div'); stream.setAttribute('id', 'stream');
const prompt = new Element('textarea'); prompt.setAttribute('id', 'prompt');
const send = new Element('button'); send.setAttribute('id', 'btnSend');
DOC.body.appendChild(stream);
DOC.body.appendChild(prompt);
DOC.body.appendChild(send);

/* ---------------- 断言工具 ---------------- */
let failed = 0;
function check(name, cond) {
  if (cond) { console.log(`  ok  ${name}`); }
  else { failed += 1; console.error(`  FAIL ${name}`); }
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* ---------------- 挂载 ---------------- */
const Advisor = await import('./js/advisor.js');
const {
  isTerminalHint, actionLabel, renderAdvisorCard, insertAdvisorCard,
  createAdvisorWatcher, initAdvisor, TERMINAL_STATUSES,
} = Advisor;

/* ============================================================
   A. 终态嗅探
   ============================================================ */
console.log('== A. 终态嗅探(SSE 事件 → 是否触发建议评估) ==');

check('A1 result / gate_stop → 触发',
  isTerminalHint({ t: 'result' }) === true
  && isTerminalHint({ t: 'gate_stop', text: '解缠覆盖率不足' }) === true);
check('A2 note[bad]「失败」/ note[warn]取消·中断·环境停止 → 触发',
  isTerminalHint({ t: 'note', tone: 'bad', text: '第 6 步失败 · oom。处置:降并行度' }) === true
  && isTerminalHint({ t: 'note', tone: 'warn', text: '第 6 步已取消 · 可续跑' }) === true
  && isTerminalHint({ t: 'note', tone: 'warn', text: '第 3 步:执行环境停止(非计算失败)。' }) === true);
check('A3 执行噪音不触发(step.* / tool.* / thinking / 普通 note / overall)',
  ['step.stage', 'step.start', 'step.end', 'tool.start', 'tool.log', 'tool.end',
   'thinking', 'say', 'plan', 'candidates', 'overall', 'report', 'ask']
    .every((t) => isTerminalHint({ t, tone: 'bad', text: '失败' }) === (t === 'note'))
  && isTerminalHint({ t: 'note', tone: 'ok', text: 'provenance 已导出' }) === false
  && isTerminalHint({ t: 'note', tone: 'warn', text: 'run 已完成,但第 8 步待重跑' }) === false);
check('A4 非法载荷安全返回 false',
  isTerminalHint(null) === false && isTerminalHint('x') === false
  && isTerminalHint({}) === false);
check('A5 终态闭集与后端同源(done/failed/interrupted)',
  JSON.stringify(TERMINAL_STATUSES) === JSON.stringify(['done', 'failed', 'interrupted']));

/* ============================================================
   B. 建议卡 DOM
   ============================================================ */
console.log('\n== B. 建议卡 DOM 结构 ==');

const PAYLOAD = {
  run_id: 'r-done-1', status: 'done', polish_source: 'rules',
  context: 'run 已完成:10/11 步,证据级 audited(3/6)',
  suggestions: [
    { id: 'qa-crossval', title: '补跑交叉验证 / QA 复核',
      why: '第 11 步质检尚未完成:双链互检证据强度最高',
      action: { kind: 'api_action', method: 'POST', endpoint: '/api/pipeline',
                body: { session: 's1', run_id: 'r-done-1', step_ids: [11] } } },
    { id: 'sensitivity-rerun', title: '换参数敏感性重跑',
      why: '当前第 6 步 min_coherence=0.25,fork 重跑是低成本稳健性检查',
      action: { kind: 'chat_prefill', text: '基于当前 run 做参数敏感性重跑:调整第 6 步 min_coherence(当前 0.25)' } },
    { id: 'skill-failures', title: '查看第 6 步技能文档《常见失败与处置》',
      why: '技能文档收录该步常见失败模式与处置依据',
      action: { kind: 'open_tab', tab: 'pipeline', step: 6 } },
  ],
};

const card = renderAdvisorCard(PAYLOAD, {});
check('B1 卡片根节点:.advisor + data-advisor-run 去重标记 + aria-label',
  card.classList.contains('advisor')
  && card.dataset.advisorRun === 'r-done-1'
  && card.getAttribute('aria-label') === '下一步建议');
check('B2 头部:标题「下一步建议」+ 终态徽标「运行完成」+ 来源「规则生成」',
  card.querySelector('.hd').textContent.includes('下一步建议')
  && card.querySelector('.hd .st').textContent === '运行完成'
  && card.querySelector('.hd .src').textContent === '规则生成');
check('B3 上下文一句话原样呈现', card.querySelector('.ctx').textContent === PAYLOAD.context);
const items = card.querySelectorAll('.item');
check('B4 三条建议 → 三个条目,标题与 why 逐条对应',
  items.length === 3
  && items[0].querySelector('.t').textContent === '补跑交叉验证 / QA 复核'
  && items[1].querySelector('.why').textContent.includes('min_coherence=0.25')
  && items[2].querySelector('.t').textContent.includes('第 6 步'));
const btns = card.querySelectorAll('.item button');
check('B5 按钮文案按 action.kind 生成(执行 / 填入输入框 / 打开流水线面板)',
  btns.length === 3
  && btns[0].textContent === '执行'
  && btns[1].textContent === '填入输入框'
  && btns[2].textContent === '打开流水线面板');
check('B6 actionLabel:下载类 api_action 标「下载」,未知 kind 空串',
  actionLabel({ kind: 'api_action', method: 'GET', download: true }) === '下载'
  && actionLabel({ kind: 'open_tab', tab: 'audit' }) === '打开审计面板'
  && actionLabel({ kind: 'nope' }) === '');
check('B7 LLM 润色来源标注(polish_source=llm → 「规则生成 · LLM 润色」)',
  renderAdvisorCard({ ...PAYLOAD, polish_source: 'llm' }, {})
    .querySelector('.hd .src').textContent === '规则生成 · LLM 润色');

/* ============================================================
   C. 动作分派
   ============================================================ */
console.log('\n== C. 动作分派 ==');

/* chat_prefill:只填输入框,不点发送 */
let inputFired = 0;
prompt.addEventListener('input', () => { inputFired += 1; });
let sendClicked = 0;
send.addEventListener('click', () => { sendClicked += 1; });
btns[1].click();
check('C1 chat_prefill:话术回填 #prompt + input 事件同步(发送键态自适应)',
  prompt.value.includes('min_coherence') && prompt.value.includes('0.25')
  && inputFired === 1);
check('C2 chat_prefill 不发送:#btnSend 未被触碰,按钮可重复点击',
  sendClicked === 0 && (btns[1].click(), inputFired === 2) && btns[1].disabled === false);

/* open_tab:hooks 透传 tab 与步骤号 */
const tabCalls = [];
const card2 = renderAdvisorCard(PAYLOAD, { openTab: (a) => tabCalls.push([a.tab, a.step]) });
card2.querySelectorAll('.item button')[2].click();
check('C3 open_tab:透传 tab=pipeline 与 step=6(dock 公开切换口的入参)',
  tabCalls.length === 1 && tabCalls[0][0] === 'pipeline' && tabCalls[0][1] === 6);

/* api_action:防重复触发;失败恢复可重试 */
{
  let calls = 0;
  let gate;
  const pending = new Promise((r) => { gate = r; });
  const toasts = [];
  const c = renderAdvisorCard(PAYLOAD, {
    callApi: () => { calls += 1; return pending; },
    toast: (m) => toasts.push(m),
  });
  const b = c.querySelectorAll('.item button')[0];
  b.click();
  await sleep(0);
  b.click();   // 在途中二次点击:disabled,不再发
  check('C4 api_action:点击即禁用防重复(在途中二次点击不发第二次)',
    calls === 1 && b.disabled === true && b.textContent === '…');
  gate(true);
  await sleep(0);
  check('C5 api_action 成功:按钮标「已发起」+ toast 引导看进度',
    b.textContent === '已发起' && b.disabled === true
    && toasts.some((m) => m.includes('已发起')));
}
{
  const toasts = [];
  const c = renderAdvisorCard(PAYLOAD, {
    callApi: async () => false,
    toast: (m) => toasts.push(m),
  });
  const b = c.querySelectorAll('.item button')[0];
  b.click();
  await sleep(0);
  check('C6 api_action 失败:按钮复原可重试 + 失败 toast',
    b.disabled === false && b.textContent === '执行'
    && toasts.some((m) => m.includes('失败')));
}

/* ============================================================
   D. 看门人:终态评估与去重
   ============================================================ */
console.log('\n== D. 看门人去重 ==');

function makeWatcher(runs, adviseMap) {
  const inserted = [];
  let stateCalls = 0;
  const w = createAdvisorWatcher({
    debounceMs: 0,
    fetchState: async () => { stateCalls += 1; return { run: runs.current }; },
    fetchAdvise: async (id) => adviseMap[id] ?? null,
    insert: (d) => inserted.push(d.run_id),
  });
  return { w, inserted, stateCalls: () => stateCalls };
}

{
  const runs = { current: { run_id: 'r1', status: 'done' } };
  const { w, inserted } = makeWatcher(runs, {
    r1: { run_id: 'r1', suggestions: [{ id: 'x', title: 't', why: 'w', action: { kind: 'open_tab', tab: 'audit' } }] },
    r2: { run_id: 'r2', suggestions: [{ id: 'x', title: 't', why: 'w', action: { kind: 'open_tab', tab: 'audit' } }] },
  });
  await w.evaluate();
  await w.evaluate();
  check('D1 同 run 只插一次(第二次 evaluate 因 shown 去重跳过)',
    inserted.length === 1 && inserted[0] === 'r1' && w.shown.has('r1'));
  runs.current = { run_id: 'r2', status: 'failed' };
  await w.evaluate();
  check('D2 换 run(新终态)再插一次:去重按 run_id 隔离',
    inserted.length === 2 && inserted[1] === 'r2');
}
{
  const runs = { current: { run_id: 'r3', status: 'running' } };
  const { w, inserted } = makeWatcher(runs, {
    r3: { run_id: 'r3', suggestions: [{ id: 'x', title: 't', why: 'w', action: { kind: 'open_tab', tab: 'audit' } }] },
  });
  await w.evaluate();
  check('D3 权威状态非终态 → 不拉建议不插卡(嗅探误报被拦)',
    inserted.length === 0 && !w.shown.has('r3'));
}
{
  const runs = { current: { run_id: 'r4', status: 'done' } };
  const { w, inserted } = makeWatcher(runs, {
    r4: { run_id: 'r4', suggestions: [] },
  });
  await w.evaluate();
  check('D4 空建议(如后端「运行中」竞态响应)→ 不插卡且不记 shown(可重评估)',
    inserted.length === 0 && !w.shown.has('r4'));
}
{
  const runs = { current: { run_id: 'r5', status: 'done' } };
  const { w, inserted, stateCalls } = makeWatcher(runs, {
    r5: { run_id: 'r5', suggestions: [{ id: 'x', title: 't', why: 'w', action: { kind: 'open_tab', tab: 'audit' } }] },
  });
  w.feedEvent({ t: 'result' });
  w.feedEvent({ t: 'note', tone: 'ok', text: 'provenance: provenance.json' });
  w.feedEvent({ t: 'report' });
  await sleep(15);
  check('D5 事件防抖:终态附近连发多事件只评估一次(且噪音不触发)',
    inserted.length === 1 && stateCalls() === 1);
  w.feedEvent({ t: 'tool.log', line: 'x' });
  await sleep(15);
  check('D6 纯噪音事件不触发评估', stateCalls() === 1);
}

/* ============================================================
   E. DOM 级查重兜底 + 单例
   ============================================================ */
console.log('\n== E. 聊天流插入与查重兜底 ==');

{
  const first = insertAdvisorCard(PAYLOAD, {});
  const second = insertAdvisorCard(PAYLOAD, {});
  const cards = stream.querySelectorAll('.advisor');
  check('E1 插入聊天流:#stream 下自动建 .stream-inner,卡片入流',
    first !== null && stream.querySelector('.stream-inner') !== null && cards.length === 1);
  check('E2 同 run 二次插入被 DOM 查重拒绝(返回 null,不重复渲染)',
    second === null && stream.querySelectorAll('.advisor').length === 1);
  const other = insertAdvisorCard({ ...PAYLOAD, run_id: 'r-done-2' }, {});
  check('E3 不同 run 正常插入', other !== null && stream.querySelectorAll('.advisor').length === 2);
}

check('E4 模块自初始化:真实页面(#stream 在)随导入建好单例,重复 init 幂等',
  initAdvisor() !== null && initAdvisor() === Advisor.initAdvisor());

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
  const open = `${indent}<${n.tagName}${attrs.length ? ' ' + attrs.join(' ') : ''}>`;
  if (!n.childNodes.length) return `${open}</${n.tagName}>\n`;
  const inner = n.childNodes.map((c) => serialize(c, indent + '  ')).join('');
  return `${open}\n${inner}${indent}</${n.tagName}>\n`;
}

console.log('\n== 关键 DOM 结构 ==\n');
console.log('--- 下一步建议卡(done · 三种动作) ---');
process.stdout.write(serialize(card));

console.log(failed ? `\n${failed} 项断言失败` : '\n全部断言通过');
process.exit(failed ? 1 : 0);
