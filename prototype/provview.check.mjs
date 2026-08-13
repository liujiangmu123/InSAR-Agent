/* ============================================================
   provview 的无浏览器自查脚本(node prototype/provview.check.mjs)
   用最小 DOM stub 直接 import js/provview.js,断言:
   ① 引用块文本生成(完整账本夹具 / 缺字段边界 / 模拟 run 警示 / 无干预口径);
   ② run 对比 diff 逻辑(方法/参数/来源/状态四维差异 + 缺步 + 计数);
   ③ 来源徽章映射闭集(local|inherited|cloud|missing 四键四色,坏值回落);
   ④ 纯函数(JSON 截断 / 参数默认值差集 / UTC 时间 / argv 拼行 / 步序);
   ⑤ DOM 渲染与挂载(11 步树 / aria-expanded 惰性展开 / 非默认参数高亮 /
     干预时间线与空态徽章 / 对比双列 / 幂等重挂 / 后端不可达错误态 /
     无 run 空态 —— 演示回落已清除,渲染夹具由本文件自带)。
   只依赖 node 内建能力,零 npm 依赖(check() 约定与其余 check.mjs 一致)。
   ============================================================ */

/* ---------------- 最小 DOM stub(pipelinerail.check.mjs 同款) ---------------- */
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
globalThis.location = { protocol: 'http:' };          // fetch 层判 file:// 用
// node 22 的全局 navigator 只有 getter,须 defineProperty 覆盖(本脚本不点
// 复制按钮,失败也无妨 —— 兜底 try 住)
try {
  Object.defineProperty(globalThis, 'navigator', {
    value: { clipboard: { writeText: async () => {} } }, configurable: true,
  });
} catch { /* 覆盖失败可接受 */ }

/* MutationObserver stub:记录实例,测试手动触发回调(模拟 dock 重渲染) */
const MO_INSTANCES = [];
globalThis.MutationObserver = class {
  constructor(cb) { this.cb = cb; MO_INSTANCES.push(this); }
  observe() {}
  disconnect() {}
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
const PV = await import('./js/provview.js');

/* ---------------- 渲染夹具(仅测试用) ----------------
   与 ledger.export_provenance 字段一一同名(模拟 run 的真实形状)。
   生产模块的演示回落已清除,夹具由本文件自带,不再从 provview.js 导入。 */
const FIX_STEP_NAMES = ['数据获取', '辅助数据', '配准', '干涉', '滤波', '解缠',
  '时序反演', '误差校正', '形变模型', '出图导出', '质检'];
const FIX_METHODS = ['local_import', 'dem_copernicus', 'isce2_tops_geom_esd',
  'isce2_ifg', 'goldstein', 'snaphu_mcf', 'mintpy_sbas', 'era5_pyaps',
  'velocity_fit', 'figure_journal', 'crossval_ps_sbas'];

function fixtureSteps() {
  const out = {};
  FIX_STEP_NAMES.forEach((name, i) => {
    const sid = i + 1;
    out[String(sid)] = {
      name, capability: name, method: FIX_METHODS[i],
      params: sid === 5 ? { alpha: 0.6, window: 32 }
        : sid === 6 ? { min_coherence: 0.3, threads: 8 }
        : { threads: 8 },
      task_hash: `t${sid}a1b2c3d4e5f6a7b8`, args_hash: `a${sid}b2c3d4e5f6a7b8c9`,
      local_hash: `l${sid}c3d4e5f6a7b8c9d0`, eval_hash: `e${sid}d4e5f6a7b8c9d0e1`,
      upstream: sid > 1 ? [String(sid - 1)] : [],
      stage: 'VERIFIED', state: sid === 2 ? 'skipped' : 'done',
      stale: false, stale_reason: null, failure_class: null,
      run_ok: 1, exit_code: 0,
      qa: [{ check: 'exit_code', ok: true, severity: 'pass', detail: 'exit_code=0,期望 0' },
           { check: 'artifact_exists', ok: true, severity: 'pass', detail: `artifact demo_${sid}` }],
      commands: sid === 2 ? [] : [{
        argv: ['python', `step_${sid}.py`, '--method', FIX_METHODS[i], '--threads', '8'],
        exit_code: 0, duration: 4.2, attempt: 1, cmd_path: `steps/${sid}/cmd.sh`,
      }],
    };
  });
  return out;
}

const DEMO_DOC = {
  schema_version: '1.0',
  run_id: '20260813T090000-demo0001',
  session_id: 'ridgecrest-2019',
  parent_run_id: null,
  generated_at_utc: '2026-08-13T09:12:00Z',
  simulated: true,
  environment: { python: '3.11.9', platform: 'linux', tools: { isce2: '2.6.3', mintpy: '1.5.1', snaphu: '2.0.7' } },
  repo: { git_head: '9cbd3ea', git_dirty: 0 },
  agent: { agent_hash: 'f00dcafe12345678' },
  intent: { goal: 'Ridgecrest 2019 同震形变(演示)' },
  scenario: 'coseismic_interferogram',
  steps: fixtureSteps(),
  artifacts: {}, metrics: {},
  thresholds: { corr_threshold: { value: 0.9, source: 'literature', ref: 'contract.yaml', status: 'PENDING' } },
  qa: { status: 'pass' },
  evidence: {
    level: 'runnable', level_index: 0,
    ladder: ['runnable', 'checked', 'audited', 'calibrated', 'validated', 'publishable'],
    reasons: ['封顶 runnable:模拟执行(引擎缺失),演示结果不构成证据'],
    ceiling: 'runnable', ceiling_reason: '模拟执行(引擎缺失),演示结果不构成证据',
    step_sources: Object.fromEntries(FIX_STEP_NAMES.map((_, i) => {
      const sid = String(i + 1);
      if (sid === '2') return [sid, { origin: 'cloud', source: 'cloud(manifest sha256:ab12cd34ef56)', manifest_sha256: 'ab12cd34ef56a7b8' }];
      if (sid === '3') return [sid, { origin: 'inherited', source: 'inherited(parent=20260812T080000-p0)', parent_run_id: '20260812T080000-p0' }];
      if (sid === '4') return [sid, { origin: 'missing', source: 'missing', detail: '沿祖先链未找到复用步骤的产物记录' }];
      return [sid, { origin: 'local', source: 'local' }];
    })),
    parent_validations: [],
  },
  evidence_level: 'runnable',
  warnings: [],
  interventions: [
    { action: 'SET_PARAMS', target: '6', payload: { params: { min_coherence: 0.3 } },
      deliver_as: 'steer', consumed_at: 1786957320 },
    { action: 'PAUSE', target: null, payload: {}, deliver_as: 'steer', consumed_at: 1786957440 },
  ],
};

// 对比样本:同链 fork(第 6 步换方法、第 5 步改参、末步失败)—— 差异是刻意设计的
const DEMO_DOC_B = (() => {
  const b = JSON.parse(JSON.stringify(DEMO_DOC));
  b.run_id = '20260813T100000-demo0002';
  b.parent_run_id = DEMO_DOC.run_id;
  b.generated_at_utc = '2026-08-13T10:30:00Z';
  b.steps['6'].method = 'snaphu_smooth';
  b.steps['5'].params.alpha = 0.8;
  b.steps['11'].state = 'failed';
  b.steps['11'].run_ok = 0;
  b.evidence.step_sources['4'] = { origin: 'local', source: 'local' };
  b.evidence_level = 'runnable';
  b.qa = { status: 'fail' };
  b.interventions = [];
  return b;
})();

/* ---------------- ① 引用块文本生成 ---------------- */
console.log('== ① 引用块(论文方法章节中文文本)==');
const cite = PV.citationText(DEMO_DOC);
check('含 run 短 id 与 agent_hash 前 8 位',
  cite.includes('run 20260813') && cite.includes('agent f00dcafe'));
check('含场景与研究意图',
  cite.includes('coseismic_interferogram') && cite.includes('Ridgecrest 2019 同震形变'));
check('含 11 步方法链(名称+方法名)',
  cite.includes('处理链共 11 步') && cite.includes('解缠(snaphu_mcf)')
  && cite.includes('数据获取(local_import)'));
check('含软件版本(python + 工具台账)',
  cite.includes('Python 3.11.9') && cite.includes('isce2 2.6.3') && cite.includes('mintpy 1.5.1'));
check('含处理日期(UTC)与证据级',
  cite.includes('2026-08-13 09:12:00 UTC') && cite.includes('证据级 runnable'));
check('模拟 run 必须携带「不构成科学证据」警示',
  cite.includes('模拟执行') && cite.includes('不构成科学证据'));
check('有干预的 run 如实声明干预次数', cite.includes('2 次人工干预'));

const citeB = PV.citationText(DEMO_DOC_B);
check('无干预 run 声明「全程无人工干预」', citeB.includes('全程无人工干预'));

const citeEmpty = PV.citationText({});
check('缺字段边界:空文档不抛错且给占位',
  citeEmpty.includes('(无步骤记录)') && citeEmpty.includes('场景:未记录')
  && citeEmpty.includes('工具版本:未记录'));
check('缺字段边界:null 文档不抛错', PV.citationText(null).length > 0);
const real = PV.citationText({ ...DEMO_DOC, simulated: false });
check('非模拟 run 不带模拟警示', !real.includes('不构成科学证据'));

/* ---------------- ② run 对比 diff 逻辑 ---------------- */
console.log('\n== ② run 对比 diff ==');
const diff = PV.diffRuns(DEMO_DOC, DEMO_DOC_B);
const row = (id) => diff.steps.find((s) => s.id === id);
check('11 步全部入列且按数值序', diff.steps.length === 11
  && JSON.stringify(diff.steps.map((s) => s.id)) === JSON.stringify([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]));
check('第 6 步方法差异(snaphu_mcf → snaphu_smooth)',
  row(6).methodChanged && row(6).methodA === 'snaphu_mcf' && row(6).methodB === 'snaphu_smooth');
check('第 5 步参数差异键 = [alpha]',
  JSON.stringify(row(5).paramKeys) === JSON.stringify(['alpha']) && row(5).changed);
check('第 4 步证据来源差异(missing → local)',
  row(4).originChanged && row(4).originA === 'missing' && row(4).originB === 'local');
check('第 11 步状态差异(done → failed)',
  row(11).stateChanged && row(11).stateA === 'done' && row(11).stateB === 'failed');
check('第 1 步无差异不标 changed', !row(1).changed);
check('差异计数 = 4(步 4/5/6/11)', diff.changedCount === 4);
check('run 头信息携带双方 id', diff.runA === DEMO_DOC.run_id && diff.runB === DEMO_DOC_B.run_id);

const bMissing = JSON.parse(JSON.stringify(DEMO_DOC_B));
delete bMissing.steps['11'];
const diff2 = PV.diffRuns(DEMO_DOC, bMissing);
check('对方缺步:inB=false 且计为差异',
  !diff2.steps.find((s) => s.id === 11).inB && diff2.steps.find((s) => s.id === 11).changed);
check('空文档对比不抛错(全部步来自另一侧)',
  PV.diffRuns(DEMO_DOC, {}).steps.length === 11
  && PV.diffRuns({}, {}).steps.length === 0);

/* ---------------- ③ 来源徽章映射闭集 ---------------- */
console.log('\n== ③ 来源徽章闭集 ==');
check('闭集恰为 local|inherited|cloud|missing 四键',
  JSON.stringify(Object.keys(PV.ORIGIN_TAG).sort())
  === JSON.stringify(['cloud', 'inherited', 'local', 'missing']));
check('四色互不相同(色义可区分)',
  new Set(Object.values(PV.ORIGIN_TAG)).size === 4);
check('映射与审计实况区一字不差(绿/蓝/橙/红)',
  PV.ORIGIN_TAG.local === 'is-ok' && PV.ORIGIN_TAG.inherited === 'is-run'
  && PV.ORIGIN_TAG.cloud === 'is-stale' && PV.ORIGIN_TAG.missing === 'is-bad');
check('闭集外坏值回落 missing 色(审计缺口)',
  PV.originTag('bogus') === 'is-bad' && PV.originTag(undefined) === 'is-bad');

/* ---------------- ④ 纯函数 ---------------- */
console.log('\n== ④ 纯函数 ==');
const small = PV.truncJson({ a: 1 });
check('小 JSON 不截断', !small.truncated && small.text === small.full);
const big = PV.truncJson({ blob: 'x'.repeat(3000) });
check('>2KB JSON 截断且保留全文', big.truncated && big.text.length === 2048
  && big.full.includes('x'.repeat(3000)));
check('参数默认值差集:只标默认表里存在且值不同的键',
  JSON.stringify(PV.paramDiffKeys({ a: 1, b: 2, z: 9 }, { a: 1, b: 3, c: 0 }))
  === JSON.stringify(['b']));
check('默认表缺失 → 不标注(不凭空声称非默认)',
  PV.paramDiffKeys({ a: 1 }, null).length === 0);
check('UTC 时间:ISO 字符串', PV.fmtUtc('2026-08-13T09:12:00Z') === '2026-08-13 09:12:00 UTC');
check('UTC 时间:epoch 秒(interventions.consumed_at)',
  /^2026-.* UTC$/.test(PV.fmtUtc(1786957320)));
check('UTC 时间:坏值给 —', PV.fmtUtc('not-a-date') === '—' && PV.fmtUtc(null) === '—');
check('argv 拼行:空白参数加引号',
  PV.argvLine(['python', 'a b.py', '--x', '1']) === 'python "a b.py" --x 1');
check('步序:字符串键按数值排(10 不在 2 前)',
  JSON.stringify(PV.sortedSteps({ steps: { 10: { name: 'x' }, 2: { name: 'y' } } }).map((s) => s.sid))
  === JSON.stringify([2, 10]));
const ivl = PV.interventionLine(DEMO_DOC.interventions[0]);
check('干预行:谁/何时/做了什么/投递语义',
  ivl.who === '用户' && ivl.what === 'SET_PARAMS · 第 6 步'
  && ivl.deliver === '步间生效' && ivl.when.endsWith('UTC'));
check('引用块短哈希助手', PV.shortHash('abcdef0123456789') === 'abcdef01' && PV.shortHash(null) === '—');

/* ---------------- ⑤ DOM 渲染与挂载 ---------------- */
console.log('\n== ⑤ DOM 渲染与挂载 ==');

// renderDoc 纯渲染:账本夹具 + 默认参照 + 对比样本
const DEFAULTS = { 5: { alpha: 0.6, window: 32 }, 6: { min_coherence: 0.4, threads: 8 } };
const host = new Element('div');
host.replaceChildren(...PV.renderDoc(DEMO_DOC, {
  defaults: DEFAULTS, runs: null, compareDoc: DEMO_DOC_B,
}));
DOC.body.appendChild(host);

check('run 头卡:id/agent 前 8 位/生成时间/场景齐备',
  host.querySelector('.prov-head').textContent.includes('20260813T090000-demo0001')
  && host.querySelector('.prov-head').textContent.includes('f00dcafe')
  && host.querySelector('.prov-head').textContent.includes('2026-08-13 09:12:00 UTC')
  && host.querySelector('.prov-head').textContent.includes('coseismic_interferogram'));
check('头卡徽章:证据级 + QA + 模拟执行',
  host.querySelector('.prov-head-tags').textContent.includes('证据级 runnable')
  && host.querySelector('.prov-head-tags').textContent.includes('QA pass')
  && host.querySelector('.prov-head-tags').textContent.includes('模拟执行'));
check('演示横幅已清除(不再有「演示数据」标注),底注声明真实数据源',
  !host.querySelectorAll('.note').some((n) => n.textContent.includes('演示数据'))
  && host.textContent.includes('GET /api/provenance 原始账本'));
check('操作行:复制引用块 / 导出 JSON / 对比下拉齐备',
  host.querySelector('.prov-acts').textContent.includes('复制引用块')
  && host.querySelector('.prov-acts').textContent.includes('导出 JSON')
  && !!host.querySelector('.prov-acts select'));

const steps = host.querySelectorAll('.prov-step');
check('证据树 11 步节点', steps.length === 11);
const heads = host.querySelectorAll('.prov-step-hd');
check('树节点键盘可达:button + aria-expanded 初始 false',
  heads.length === 11 && heads.every((b) => b.tagName === 'button')
  && heads.every((b) => b.getAttribute('aria-expanded') === 'false'));
check('状态色点 + 跳过步文字标注(不只靠颜色)',
  host.querySelectorAll('.dot').length === 11
  && steps[1].querySelector('.st')?.textContent.includes('跳过'));
check('来源徽章四色:local/cloud/inherited/missing 各有实例',
  steps[0].querySelector('.tag.is-ok') && steps[1].querySelector('.tag.is-stale')
  && steps[2].querySelector('.tag.is-run') && steps[3].querySelector('.tag.is-bad'));
check('cloud 徽章带 manifest 短哈希、inherited 带父 run 短 id',
  steps[1].textContent.includes('cloud(ab12cd34ef56')
  && steps[2].textContent.includes('inherited(20260812'));

// 惰性展开:点击前详情区为空,点击后才建 DOM
const hd6 = heads[5];
const bd6 = steps[5].querySelector('.prov-step-bd');
check('惰性:未展开的步骤详情区为空', bd6.children.length === 0);
hd6.click();
check('展开后 aria-expanded=true 且详情就位',
  hd6.getAttribute('aria-expanded') === 'true'
  && !!bd6.querySelector('.prov-params') && !!bd6.querySelector('.prov-qa'));
check('参数表:非默认值行高亮(min_coherence 0.3 ≠ 默认 0.4)',
  bd6.querySelectorAll('.prow.diff').length === 1
  && bd6.querySelector('.prow.diff').textContent.includes('min_coherence'));
check('指纹三段 inputs/params/env(前 8 位 + title 全量)',
  bd6.querySelectorAll('.prov-fp').length === 3
  && bd6.querySelector('.prov-fp').getAttribute('title').includes('全量:')
  && bd6.textContent.includes('e6d4e5f6'));
check('命令行:折叠按钮(aria-expanded)+ 复制入口',
  bd6.querySelector('.prov-cmd-tog').getAttribute('aria-expanded') === 'false'
  && bd6.querySelector('.prov-cmd-hd').textContent.includes('复制'));
hd6.click();
check('再点收起:aria-expanded=false', hd6.getAttribute('aria-expanded') === 'false');

// 干预时间线:两条记录;DEMO_DOC_B 无干预 → 空态徽章
check('干预时间线两条(SET_PARAMS 第 6 步 / PAUSE)',
  host.querySelectorAll('.prov-iv .ivrow').length === 2
  && host.querySelector('.prov-iv').textContent.includes('SET_PARAMS · 第 6 步'));
const hostB = new Element('div');
hostB.replaceChildren(...PV.renderDoc(DEMO_DOC_B, {}));
check('无干预 → 「无人工干预 · 全自动执行」徽章',
  !!hostB.querySelector('.prov-iv-empty')
  && hostB.querySelector('.prov-iv-empty').textContent.includes('无人工干预 · 全自动执行'));

// 大 JSON 参数值:>2KB 截断渲染 + 「展开全文」按钮
const bigDoc = JSON.parse(JSON.stringify(DEMO_DOC));
bigDoc.steps['1'].params = { blob: 'x'.repeat(3000) };
bigDoc.interventions = [];
const hostBig = new Element('div');
hostBig.replaceChildren(...PV.renderDoc(bigDoc, {}));
DOC.body.appendChild(hostBig);
hostBig.querySelectorAll('.prov-step-hd')[0].click();
const bigPre = hostBig.querySelector('.prov-json');
check('>2KB 参数值截断显示 + 展开按钮',
  !!bigPre && bigPre.textContent.includes('已截断')
  && hostBig.querySelectorAll('button').some((b) => b.textContent === '展开全文'));
hostBig.remove();

// 对比双列:差异行高亮 + 双列头
const cmp = host.querySelector('.prov-cmp');
check('对比双列在场且带双方 run 时间戳',
  !!cmp && cmp.querySelector('.chd').textContent.includes('20260813T090000')
  && cmp.querySelector('.chd').textContent.includes('20260813T100000'));
check('差异行高亮 4 行(与 diff 计数一致)',
  cmp.querySelectorAll('.crow.changed').length === 4);
check('参数差异标注 Δ参数 alpha', cmp.textContent.includes('Δ参数 alpha'));

// 自初始化挂载:#dockBody + #pane-audit → 追加区块;dock 重渲染后幂等重挂
const dockBody = new Element('div');
dockBody.setAttribute('id', 'dockBody');
const pane = new Element('section');
pane.setAttribute('id', 'pane-audit');
dockBody.appendChild(pane);
DOC.body.appendChild(dockBody);
PV.initProvView();
await sleep(30);   // render() 的 fetch 全部落空 → 错误态(演示回落已清除)
const mounted = () => pane.querySelectorAll('.provview');
check('自初始化:区块挂进审计 pane 尾部', mounted().length === 1);
check('后端不可达 → 错误态带重试,绝不渲染演示账本',
  pane.querySelectorAll('.es-error').length === 1
  && pane.querySelectorAll('.es-retry').length === 1
  && pane.querySelectorAll('.prov-step').length === 0);
PV.initProvView();
check('重复初始化幂等:仍只有一个区块', mounted().length === 1);
pane.replaceChildren();   // 模拟 dock.js 重渲染审计面板(区块被清)
MO_INSTANCES.forEach((mo) => mo.cb());
await sleep(30);
check('dock 重渲染后观察器自动重挂', mounted().length === 1);

// 可达但无 run(/api/provenance 404)→ 空态带运行引导:桩 fetch 后点「重试」
globalThis.fetch = async () => ({
  ok: false, status: 404,
  headers: { get: () => null },
  json: async () => ({ detail: 'no run' }),
  text: async () => 'no run',
});
pane.querySelector('.es-retry')?.click();
await sleep(30);
check('可达但无 run → 空态卡 + 运行引导(绝不渲染演示账本)',
  pane.querySelectorAll('.es-empty').length === 1
  && pane.textContent.includes('还没有账本记录')
  && pane.textContent.includes('运行流水线')
  && pane.querySelectorAll('.prov-step').length === 0);

console.log(failed ? `\n${count} 项断言,${failed} 项失败` : `\n全部 ${count} 项断言通过`);
process.exit(failed ? 1 : 0);
