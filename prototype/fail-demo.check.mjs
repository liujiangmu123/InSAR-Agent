/* ============================================================
   fail-demo 的无浏览器自查脚本（node prototype/fail-demo.check.mjs）
   用最小 DOM stub 直接 import stream.js，复现 fail-demo.html 的
   四个场景，断言关键 DOM 结构并打印卡片序列化结果。
   只依赖 node 内建能力，零 npm 依赖（与原型纪律一致）。
   ============================================================ */

/* ---------------- 最小 DOM stub ---------------- */
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
    this.value = ''; this.open = false;
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
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  dispatchEvent(evt) { (this.listeners[evt.type] || []).forEach((fn) => fn.call(this, evt)); return true; }
  click() { this.dispatchEvent({ type: 'click', preventDefault() {}, target: this }); }
  focus() {} blur() {} scrollIntoView() {}
  set textContent(v) { this.replaceChildren(new TextNode(v)); }
  get textContent() { return super.textContent; }
  get children() { return this.childNodes.filter((c) => c.nodeType === 1); }
  set innerHTML(v) { this.replaceChildren(new TextNode(v)); }   // 仅 figures.js 可信模板用，演示路径不触发
  matches(sel) {
    // 支持「tag.cls1.cls2」与「.cls」「tag」的复合选择器，够本脚本用
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

/* ---------------- 复现 fail-demo 场景 ---------------- */
const Stream = await import('./js/stream.js');
// 步骤目录不再内置演示数据:gate 卡标题的步骤名(def_)需注册表快照水合
const St = await import('./js/state.js');
const { REGISTRY } = await import('../tests/js/_registry.mjs');
St.setRegistry(REGISTRY);

const host = new Element('div');
host.setAttribute('id', 'stream');
DOC.body.appendChild(host);
const prompt = new Element('textarea');
prompt.setAttribute('id', 'prompt');
DOC.body.appendChild(prompt);

const hookCalls = [];
Stream.setFailureHooks({
  openTerminal: (stepNo) => hookCalls.push(['openTerminal', stepNo]),
  resume: () => hookCalls.push(['resume']),
});
Stream.mount(host);

// ① 工具卡失败 + note(bad) → 失败卡
const LINES = [
  ['$ smallbaselineApp.py --dostep correct_troposphere --method tropo_era5_pyaps', 'cmd'],
  ['本地缓存命中 3/7 天 · 需从 CDS 补拉 4 个日期的 ERA5 再分析数据', 'dim'],
  ['CDS 请求已入队 · queue position 12 → 4', 'dim'],
  ['GET https://cds.climate.copernicus.eu/api/v2/… → HTTP 503 Service Unavailable', 'err'],
  ['重试 1/3（指数退避 2s）… HTTP 503', 'warn'],
  ['重试 2/3（指数退避 4s）… HTTP 503', 'warn'],
  ['重试 3/3（指数退避 8s）… HTTP 503', 'err'],
  ['失败分类：service_down（规则命中 r"HTTP 5\\d\\d"，未消耗 LLM 调用）', 'dim'],
  ['✗ ERA5 不可达 · 断点已保留（已下载 0/4 天，无需清理）', 'err'],
  ['wrapper: exit_code=1 · checkpoint.json 已写入', 'dim'],
];
const t = Stream.toolCall({
  cmd: 'smallbaselineApp.py --dostep correct_troposphere --method tropo_era5_pyaps',
  verb: '[08/11]', label: '大气校正', open: true,
});
LINES.forEach(([line, tone]) => t.log(line, tone));
t.finish({ exit: 1, summary: '失败 · service_down（HTTP 503 × 3）' });
const failCard = Stream.note('bad',
  '第 8 步失败 · service_down（分诊来源：rule）。处置：换用离线大气模型降级继续，或保留断点等 ERA5 恢复。');

console.log('\n== ① 失败卡（note bad 紧随 tool 失败）==');
check('渲染为 .stepfail 卡而非普通横幅', failCard.matches('div.stepfail'));
check('role=alert', failCard.getAttribute('role') === 'alert');
check('标题含步骤号与名称', failCard.querySelector('.hd').textContent.includes('第 8 步失败 · 大气校正'));
check('失败类别徽标 service_down', failCard.querySelector('.cls')?.textContent === 'service_down');
const lns = failCard.querySelectorAll('.logs .out .ln');
check('日志预览截尾为 8 行（写入 10 行）', lns.length === 8);
check('预览首行是第 3 行日志（头两行被截掉）', lns[0]?.textContent === LINES[2][0]);
check('预览末行是最后一行日志', lns[7]?.textContent === LINES[9][0]);
check('预览标题标注 exit 1', failCard.querySelector('.logs .cap').textContent.includes('exit 1'));
const btns = failCard.querySelectorAll('.acts button').map((b) => b.textContent);
check('动作按钮 = [查看完整日志, 从断点继续]', JSON.stringify(btns) === JSON.stringify(['查看完整日志', '从断点继续']));
failCard.querySelectorAll('.acts button')[0].click();
failCard.querySelectorAll('.acts button')[1].click();
check('查看完整日志 → openTerminal hook(stepNo=8)', JSON.stringify(hookCalls[0]) === JSON.stringify(['openTerminal', 8]));
check('从断点继续 → resume hook', JSON.stringify(hookCalls[1]) === JSON.stringify(['resume']));

// ② gate_stop（文本形态，字符串建议）
const gate = Stream.gateStopEntry({
  stepId: 6,
  text: '第 6 步被质量门拦停：unwrap_coverage 0.62 < 0.70，上游解缠质量不足以支撑时序反演。',
  suggestions: [
    '换用其他方法（snaphu_smooth）重跑第 6 步',
    '放宽阈值至 0.60（需在 contract.yaml 里登记依据）',
  ],
});
console.log('\n== ② gate_stop 卡（建议升级为可点操作）==');
check('渲染为 .gate 卡', gate.matches('div.gate'));
check('标题含步骤号与名称', gate.querySelector('.hd').textContent.includes('第 6 步 解缠'));
const gbtns = gate.querySelectorAll('.acts button');
check('两条建议渲染为按钮', gbtns.length === 2);
check('首选建议加重（btn-wrn）', gbtns[0].matches('button.btn-wrn'));
gbtns[0].click();
check('点击建议 → 回填 #prompt 输入框', prompt.value === '换用其他方法（snaphu_smooth）重跑第 6 步');
check('失败上下文已被 ① 消费，gate 卡无日志预览', gate.querySelectorAll('.logs').length === 0);

// ③ 审批卡确认后的紧凑完成态
const ask = Stream.askApproval({
  title: '需确认 · 执行流水线',
  rows: [['步骤', '第 6、7、8、9、10、11 步（共 6 步）'], ['花费', '0 元 · 本机计算']],
  actions: [
    { label: '确认执行', icon: 'play', done: '已确认 · 开始执行', run: () => {} },
    { label: '取消', done: '已取消 · 流水线保持待运行', run: () => {} },
  ],
});
ask.querySelector('.acts button').click();
console.log('\n== ③ 审批卡确认后（P2-002）==');
check('卡片标记 data-resolved', ask.dataset.resolved === '1');
const verdict = ask.querySelector('.verdict');
check('完成态含小图标 + 文案 + 时间三个子节点', verdict.childNodes.length === 3);
check('图标是 svg（尺寸由 .ask .verdict svg 约束为 14px）', verdict.childNodes[0].tagName === 'svg');
check('文案为「已确认 · 开始执行」', verdict.childNodes[1].textContent === '已确认 · 开始执行');
check('时间戳 HH:MM', /^\d{2}:\d{2}$/.test(verdict.querySelector('.time')?.textContent || ''));

// ④ 对照：无失败上下文的 bad note 保持横幅
const plain = Stream.note('bad', '对照：这条 bad note 前面没有失败的工具卡。');
console.log('\n== ④ 对照横幅 ==');
check('无失败上下文时仍是 .note.is-bad 横幅', plain.matches('div.note.is-bad'));

// CSS 静态检查：P2-002 的尺寸约束与新卡样式确实落在 stream.css
const css = (await import('node:fs')).readFileSync(
  new URL('./css/stream.css', import.meta.url), 'utf-8');
console.log('\n== CSS 静态检查 ==');
check('.ask .verdict svg 尺寸约束存在', /\.ask \.verdict svg \{ width: 14px; height: 14px/.test(css));
check('.stepfail 样式块存在', css.includes('.stepfail {'));
check('.logs 预览样式存在', css.includes('.logs .out {'));

/* ---------------- 输出关键 DOM 结构 ---------------- */
console.log('\n== 关键 DOM 结构 ==\n');
console.log('--- ① .stepfail 失败卡 ---');
process.stdout.write(serialize(failCard));
console.log('--- ② .gate 质量门卡 ---');
process.stdout.write(serialize(gate));
console.log('--- ③ .ask 审批卡（已确认）---');
process.stdout.write(serialize(ask));

console.log(failed ? `\n${failed} 项断言失败` : '\n全部断言通过');
process.exit(failed ? 1 : 0);
