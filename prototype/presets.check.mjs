/* ============================================================
   presets.js 的无浏览器自查脚本(node prototype/presets.check.mjs)
   与 cmdk.check.mjs 同一套纪律:最小 DOM stub 直接 import
   js/presets.js,覆盖 ——
     ① 校验器:类型 / 范围 / 边界值 / 必填 / 枚举 / 格式
     ② 校验器:全角数字与中文数字容错
     ③ 预设匹配高亮逻辑(含多档同值 / 缺键不判 / 数值等价)
     ④ 填充计划与恢复默认(只覆盖表单实际存在的键)
     ⑤ 知识表完整性(11 步全覆盖,字段显式,档位值过自身校验)
     ⑥ DOM 增强冒烟(预设条 / ⓘ 图标 / 幂等 / 未知参数零干扰)
     ⑦ 提交拦截(capture 截停:非法值不达 dock 的 blur/Enter commit)
     ⑧ 静态接线(index.html 恰好各一行 css/js;presets.css 关键类)
   只依赖 node 内建能力,零 npm 依赖。
   ============================================================ */

/* ---------------- 最小 DOM stub(cmdk 版裁剪 + 事件捕获模拟) ---------------- */
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
}

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
    this.hidden = false; this.disabled = false; this.value = '';
    this.offsetWidth = 0; this.offsetTop = 0; this.offsetHeight = 16;
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
  removeAttribute(k) { delete this.attrs[k]; }
  appendChild(n) { detach(n); n.parentNode = this; this.childNodes.push(n); return n; }
  insertBefore(n, ref) {
    detach(n); n.parentNode = this;
    const i = this.childNodes.indexOf(ref);
    if (i < 0) this.childNodes.push(n); else this.childNodes.splice(i, 0, n);
    return n;
  }
  remove() { detach(this); }
  set textContent(v) { [...this.childNodes].forEach(detach); if (v !== '') this.appendChild(new TextNode(v)); }
  get textContent() { return super.textContent; }
  get children() { return this.childNodes.filter((c) => c.nodeType === 1); }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  dispatchEvent(evt) {
    evt.target ||= this;
    (this.listeners[evt.type] || []).forEach((fn) => fn.call(this, evt));
    return true;
  }
  click() { this.dispatchEvent({ type: 'click', target: this, preventDefault() {}, stopPropagation() {} }); }
  getBoundingClientRect() {
    return { top: this.offsetTop, left: 30, bottom: this.offsetTop + this.offsetHeight, right: 45, width: 15, height: 15 };
  }
  matches(sel) {
    const m = /^([a-z0-9-]*)((?:\.[\w-]+)*)$/.exec(String(sel).trim());
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

/* document 级捕获监听收集:模拟「捕获 → (未截停时)目标监听」的浏览器语义 */
const docL = {};
const DOC = {
  body: new Element('body'),
  documentElement: new Element('html'),
  readyState: 'complete',
  createElement: (t) => new Element(t),
  createTextNode: (s) => new TextNode(s),
  addEventListener(type, fn) { (docL[type] ||= []).push(fn); },
  querySelectorAll(sel) { return this.body.querySelectorAll(sel); },
  querySelector(sel) { return this.body.querySelector(sel); },
};

globalThis.document = DOC;
globalThis.Event = class {
  constructor(type, opts = {}) {
    this.type = type;
    Object.assign(this, opts);
    this.stopped = false; this.defaultPrevented = false;
  }
  stopPropagation() { this.stopped = true; }
  preventDefault() { this.defaultPrevented = true; }
};

/** 浏览器事件流模拟:先跑 document 捕获监听,未被截停才轮到目标自身监听。 */
function fire(type, target, opts = {}) {
  const evt = new Event(type, { ...opts, target });
  for (const fn of docL[type] || []) fn(evt);
  if (!evt.stopped) target.dispatchEvent(evt);
  return evt;
}

/* ---------------- 被测模块(import 时自初始化:委托绑到 stub document) ---------------- */
const PZ = await import('./js/presets.js');

let failed = 0;
function check(name, cond) {
  if (cond) { console.log(`  ok  ${name}`); }
  else { failed += 1; console.error(`  FAIL ${name}`); }
}

const V = (stepId, key, raw) => PZ.validateValue(PZ.specOf(stepId, key), raw);

/* ================= ① 校验器:类型 / 范围 / 边界值 ================= */
console.log('== ① 校验器:类型 / 范围 / 边界值 ==');
check('number 合法值通过(min_coherence 0.25)', V(6, 'min_coherence', '0.25').ok);
check('number 下边界含端点(0)', V(6, 'min_coherence', '0').ok && V(6, 'min_coherence', '0').value === 0);
check('number 上边界含端点(1)', V(6, 'min_coherence', '1').ok);
check('number 越下界拒绝(-0.01)', !V(6, 'min_coherence', '-0.01').ok && /不能小于/.test(V(6, 'min_coherence', '-0.01').msg));
check('number 越上界拒绝(1.01)', !V(6, 'min_coherence', '1.01').ok && /不能大于/.test(V(6, 'min_coherence', '1.01').msg));
check('step 仅为建议不做硬校验(0.27 通过)', V(6, 'min_coherence', '0.27').ok);
check('非数字文本拒绝(abc)', !V(6, 'min_coherence', 'abc').ok && /数字/.test(V(6, 'min_coherence', 'abc').msg));
check('int 通过(threads 16)', V(6, 'threads', '16').ok && V(6, 'threads', '16').value === 16);
check('int 拒绝小数(8.5)', !V(6, 'threads', '8.5').ok && /整数/.test(V(6, 'threads', '8.5').msg));
check('int 越界拒绝(0 / 33)', !V(6, 'threads', '0').ok && !V(6, 'threads', '33').ok);
check('范围文案带单位(threads 33 → 32 线程)', /32 线程/.test(V(6, 'threads', '33').msg));
check('必填空值拒绝(min_coherence 空串)', !V(6, 'min_coherence', '').ok && /不能为空/.test(V(6, 'min_coherence', '').msg));
check('必填空白串拒绝("  ")', !V(6, 'min_coherence', '  ').ok);
check('非必填空值放行(step_date="")', V(9, 'step_date', '').ok);
check('非必填空值放行(source="")', V(1, 'source', '').ok);
check('枚举命中通过(orbit=poeorb)', V(2, 'orbit', 'poeorb').ok);
check('枚举外拒绝(orbit=xxx)', !V(2, 'orbit', 'xxx').ok && /poeorb/.test(V(2, 'orbit', 'xxx').msg));
check('枚举大小写敏感(cost_mode=defo 拒绝)', !V(6, 'cost_mode', 'defo').ok && V(6, 'cost_mode', 'DEFO').ok);
check('bool 规范化(True → true)', V(8, 'dem_error', 'True').ok && V(8, 'dem_error', 'True').value === 'true');
check('bool 容错(1/否)', V(8, 'dem_error', '1').value === 'true' && V(8, 'dem_error', '否').value === 'false');
check('bool 非法拒绝(maybe)', !V(8, 'dem_error', 'maybe').ok);
check('格式校验通过(dates 起止窗)', V(1, 'dates', '2019-06-10..2019-08-15').ok);
check('格式校验拒绝(dates 斜杠写法)', !V(1, 'dates', '2019/06/10').ok && /YYYY-MM-DD/.test(V(1, 'dates', '2019/06/10').msg));
check('格式校验拒绝(step_date 带连字符)', !V(9, 'step_date', '2019-07-06').ok);
check('格式校验通过(step_date 8 位)', V(9, 'step_date', '20190706').ok);
check('未知参数规格为 null(specOf)', PZ.specOf(6, 'mystery') === null && PZ.specOf(99, 'alpha') === null);
check('未知参数校验放行(spec=null)', PZ.validateValue(null, '!!whatever!!').ok);

/* ================= ② 校验器:全角 / 中文数字容错 ================= */
console.log('\n== ② 校验器:全角数字与中文数字容错 ==');
const FW_025 = '\uFF10\uFF0E\uFF12\uFF15';           // 「0.25」全角
const FW_600 = '\uFF16\uFF10\uFF10';                  // 「600」全角
check('全角数字通过并规范化(0.25)', V(6, 'min_coherence', FW_025).ok && V(6, 'min_coherence', FW_025).text === '0.25');
check('全角整数通过(600 dpi)', V(10, 'dpi', FW_600).ok && V(10, 'dpi', FW_600).value === 600);
check('中文句号作小数点(0。25)', V(6, 'min_coherence', '0\u30022' + '5').ok);
check('千分位剥离(1,200 → 1200)', PZ.normalizeNumberText('1,200') === '1200' && V(10, 'dpi', '1,200').ok);
check('首尾与全角空格容错(" 0.4 ")', V(5, 'alpha', ' 0.4\u3000').ok);
check('中文数字:零点五 → 0.5', PZ.normalizeNumberText('零点五') === '0.5' && V(5, 'alpha', '零点五').value === 0.5);
check('中文数字:八 → 8', V(6, 'threads', '八').value === 8);
check('中文数字:十六 → 16', V(6, 'threads', '十六').value === 16);
check('中文数字:两 → 2', V(4, 'azimuth_looks', '两').value === 2);
check('中文数字:一百二十 → 120', V(7, 'max_temporal_baseline', '一百二十').value === 120);
check('中文数字:三点一四 → 3.14', PZ.normalizeNumberText('三点一四') === '3.14');
check('中文负数:负五 → -5(再被范围拒)', PZ.normalizeNumberText('负五') === '-5' && !V(6, 'threads', '负五').ok);
check('混杂非法形态拒绝(ABC点五)', !V(5, 'alpha', 'ABC点五').ok);
check('规范化不改合法 ASCII("0.4")', PZ.normalizeNumberText('0.4') === '0.4');

/* ================= ③ 预设匹配高亮逻辑 ================= */
console.log('\n== ③ 预设匹配高亮逻辑 ==');
check('精确命中标准档(第 6 步 0.25/8)',
  JSON.stringify(PZ.matchedTiers(6, { min_coherence: '0.25', threads: '8' })) === '["standard"]');
check('命中快速档(0.4/16)',
  JSON.stringify(PZ.matchedTiers(6, { min_coherence: '0.4', threads: '16' })) === '["fast"]');
check('数值等价命中(0.40 == 0.4)',
  PZ.matchedTiers(6, { min_coherence: '0.40', threads: '16' }).includes('fast'));
check('全角文本也命中(0.40)',
  PZ.matchedTiers(6, { min_coherence: '\uFF10\uFF0E\uFF14\uFF10', threads: '16' }).includes('fast'));
check('任一参数偏离即不命中(0.3/8)', PZ.matchedTiers(6, { min_coherence: '0.3', threads: '8' }).length === 0);
check('档位参数缺失不判命中(只给 min_coherence)', PZ.matchedTiers(6, { min_coherence: '0.25' }).length === 0);
check('多档同值全部命中(第 2 步标准=精细)',
  JSON.stringify(PZ.matchedTiers(2, { dem: 'copernicus-30m', orbit: 'poeorb' })) === '["standard","fine"]');
check('无档位步骤永不命中(第 9 步)', PZ.matchedTiers(9, { poly_order: '1' }).length === 0);
check('非法值不参与命中(min_coherence=abc)', PZ.matchedTiers(6, { min_coherence: 'abc', threads: '16' }).length === 0);
check('档位外参数不影响判定(带 pairs)',
  PZ.matchedTiers(4, { range_looks: '10', azimuth_looks: '2', pairs: '999' }).includes('standard'));

/* ================= ④ 填充计划与恢复默认 ================= */
console.log('\n== ④ 填充计划与恢复默认 ==');
const plan6 = PZ.fillPlan(6, 'fast', ['min_coherence', 'threads']);
check('填充计划覆盖全部档位键', plan6.length === 2
  && plan6.find((p) => p.key === 'min_coherence').text === '0.4'
  && plan6.find((p) => p.key === 'threads').text === '16');
check('填充计划只写表单存在的键', PZ.fillPlan(6, 'fast', ['min_coherence']).length === 1);
check('档位可用性:键齐 → 可用', PZ.tierApplicable(6, 'fast', ['min_coherence', 'threads', 'extra']));
check('档位可用性:缺键 → 不可用', !PZ.tierApplicable(6, 'fast', ['min_coherence']));
check('第 9 步无档位 → 不可用', !PZ.tierApplicable(9, 'fast', ['poly_order', 'step_date']));
const dft8 = PZ.defaultsPlan(8, ['ramp', 'dem_error', 'solid_earth_tides']);
check('恢复默认按 registry 声明回填(第 8 步)',
  dft8.find((p) => p.key === 'ramp').text === 'linear'
  && dft8.find((p) => p.key === 'dem_error').text === 'true'
  && dft8.find((p) => p.key === 'solid_earth_tides').text === 'false');
check('恢复默认跳过只读 list(periods)', !PZ.defaultsPlan(9, ['periods', 'poly_order']).some((p) => p.key === 'periods'));
check('布尔档位值转规范文本(第 8 步快速档)',
  PZ.fillPlan(8, 'fast', ['ramp', 'dem_error', 'solid_earth_tides']).find((p) => p.key === 'dem_error').text === 'false');
check('cmap 不在第 10 步档位内(呈现参数不替用户改)',
  !PZ.fillPlan(10, 'fast', ['dpi', 'cmap', 'format']).some((p) => p.key === 'cmap'));

/* ================= ⑤ 知识表完整性 ================= */
console.log('\n== ⑤ 知识表完整性 ==');
const problems = PZ.knowledgeProblems();
if (problems.length) console.error('    完整性问题:\n    ' + problems.join('\n    '));
check('knowledgeProblems() 无告警', problems.length === 0);
check('11 步全部覆盖', [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11].every((id) => !!PZ.KNOWLEDGE[id]));
const nParams = Object.values(PZ.KNOWLEDGE).reduce((s, e) => s + Object.keys(e.params).length, 0);
check(`参数条目数与设计一致(36 条,实际 ${nParams})`, nParams === 36);
check('键位与 registry 对齐抽查(第 6 步三参数)',
  ['min_coherence', 'cost_mode', 'threads'].every((k) => k in PZ.KNOWLEDGE[6].params));
check('枚举与 registry 对齐抽查(network 三选)',
  JSON.stringify(PZ.KNOWLEDGE[7].params.network.enum) === '["small_baseline","star","sequential"]');
check('范围与 state.js PARAM_SCHEMA 对齐抽查(dpi 72-1200)',
  PZ.KNOWLEDGE[10].params.dpi.min === 72 && PZ.KNOWLEDGE[10].params.dpi.max === 1200);
check('每条 help 都非空', Object.values(PZ.KNOWLEDGE)
  .every((e) => Object.values(e.params).every((p) => typeof p.help === 'string' && p.help.length > 4)));
check('单位显式声明(有值或 null)', Object.values(PZ.KNOWLEDGE)
  .every((e) => Object.values(e.params).every((p) => p.unit === null || typeof p.unit === 'string')));
check('第 9 步显式无档位并给出理由', PZ.KNOWLEDGE[9].presets === null && PZ.KNOWLEDGE[9].presetsNote.length > 8);
check('其余步骤三档齐全', [1, 2, 3, 4, 5, 6, 7, 8, 10, 11]
  .every((id) => ['fast', 'standard', 'fine'].every((t) => !!PZ.KNOWLEDGE[id].presets?.[t])));

/* ================= ⑥ DOM 增强冒烟 ================= */
console.log('\n== ⑥ DOM 增强冒烟(预设条 / ⓘ / 幂等 / 零干扰) ==');

/** 按 dock.js stepDetail 的真实结构拼一个参数字段。 */
function makeField(key, value) {
  const wrap = new Element('div');
  const field = new Element('div'); field.className = 'field';
  const label = new Element('label'); label.textContent = key;
  const inp = new Element('input');
  inp.dataset.k = key; inp.value = value;
  field.appendChild(label); field.appendChild(inp);
  const err = new Element('p'); err.className = 'ferr hidden';
  wrap.appendChild(field); wrap.appendChild(err);
  return { wrap, inp, err, label };
}

function makeDetail(stepId, stepName, fields) {
  const detail = new Element('div'); detail.className = 'pdetail';
  const hd = new Element('div'); hd.className = 'hd';
  hd.textContent = `第 ${stepId} 步 · ${stepName}`;
  const bd = new Element('div'); bd.className = 'bd';
  const pform = new Element('div'); pform.className = 'pform';
  for (const f of fields) pform.appendChild(f.wrap);
  bd.appendChild(pform);
  detail.appendChild(hd); detail.appendChild(bd);
  DOC.body.appendChild(detail);
  return { detail, pform };
}

// 第 6 步:两个已知参数 + 一个未知参数(模拟服务端新增而知识表未收录)
const fMin = makeField('min_coherence', '0.25');
const fThr = makeField('threads', '8');
const fMys = makeField('mystery', '42');
const { detail } = makeDetail(6, '解缠', [fMin, fThr, fMys]);

// 给未知/已知参数各挂一个「dock 式」blur/Enter 提交监听,验证拦截语义
const commits = [];
for (const f of [fMin, fThr, fMys]) {
  f.inp.addEventListener('blur', () => commits.push(`${f.inp.dataset.k}=${f.inp.value}`));
  f.inp.addEventListener('keydown', (e) => { if (e.key === 'Enter') commits.push(`${f.inp.dataset.k}=${f.inp.value}`); });
}

check('enhance 返回 true(首次增强)', PZ.enhance(detail) === true);
const bar = detail.querySelector('.pz-bar');
check('预设条插入参数区顶部', !!bar && detail.querySelector('.bd').children[0] === bar);
const tierBtns = bar ? bar.querySelectorAll('.pz-tier') : [];
check('三档按钮齐全', tierBtns.length === 3
  && tierBtns.map((b) => b.textContent).join(',') === '快速预览,标准,精细');
check('当前值命中标准档 → aria-pressed 高亮',
  tierBtns.find((b) => b.dataset.tier === 'standard')?.getAttribute('aria-pressed') === 'true'
  && tierBtns.find((b) => b.dataset.tier === 'fast')?.getAttribute('aria-pressed') === 'false');
check('「恢复默认」按钮存在', !!bar.querySelector('.pz-reset'));
check('已知参数有 ⓘ 图标', !!fMin.label.querySelector('.pz-info') && !!fThr.label.querySelector('.pz-info'));
check('未知参数无 ⓘ 图标(零干扰)', !fMys.label.querySelector('.pz-info'));
check('ⓘ 有可及名', fMin.label.querySelector('.pz-info').getAttribute('aria-label') === 'min_coherence 参数说明');
const before = detail.querySelectorAll('.pz-bar').length;
check('重复 enhance 幂等', PZ.enhance(detail) === false && detail.querySelectorAll('.pz-bar').length === before);

// 档位填充:不自动提交,出确认区
tierBtns.find((b) => b.dataset.tier === 'fast').click();
check('点快速档填充输入框(0.4 / 16)', fMin.inp.value === '0.4' && fThr.inp.value === '16');
check('填充不触发提交(commits 为空)', commits.length === 0);
check('填充值带待确认标记(pz-pending)', fMin.inp.classList.contains('pz-pending'));
const zone = detail.querySelector('.pz-confirm');
check('确认区出现并计数', zone && zone.hidden === false && /2 项/.test(detail.querySelector('.pz-note').textContent));
check('填充后快速档立即高亮', tierBtns.find((b) => b.dataset.tier === 'fast').getAttribute('aria-pressed') === 'true');
// 还原:回到填充前
bar.querySelector('.pz-cancel').click();
check('「还原」恢复填充前值并收起确认区', fMin.inp.value === '0.25' && fThr.inp.value === '8' && zone.hidden === true);
// 再填充并确认应用:对每个待确认输入框派发 blur → dock 式监听收到提交
tierBtns.find((b) => b.dataset.tier === 'fast').click();
bar.querySelector('.pz-apply').click();
check('「确认应用」经 blur 提交给 dock 监听', commits.includes('min_coherence=0.4') && commits.includes('threads=16'));
check('确认后待确认标记清除', !fMin.inp.classList.contains('pz-pending') && zone.hidden === true);
commits.length = 0;

// 帮助气泡:focus 触发 / Esc 关闭(键盘可达)
const info = fMin.label.querySelector('.pz-info');
info.dispatchEvent(new Event('focus', { target: info }));
const tip = DOC.body.querySelector('.pz-tip');
check('focus ⓘ 弹出气泡', !!tip && tip.hidden === false);
check('气泡是 tooltip 语义且回填 aria-describedby',
  tip.getAttribute('role') === 'tooltip' && info.getAttribute('aria-describedby') === 'pz-tip');
check('气泡含范围/步进/默认与帮助文案',
  /0 – 1/.test(tip.textContent) && /步进 0.05/.test(tip.textContent)
  && /默认 0.25/.test(tip.textContent) && /过低引入噪声点/.test(tip.textContent));
const escEvt = fire('keydown', info, { key: 'Escape' });
check('Esc 关闭气泡且不外溢(截停)', tip.hidden === true && escEvt.stopped === true);
info.dispatchEvent(new Event('focus', { target: info }));
info.dispatchEvent(new Event('blur', { target: info }));
check('blur 离开 ⓘ 收起气泡', tip.hidden === true);

// 无档位步骤(第 9 步):说明文案 + 恢复默认,无档位按钮
const f9 = makeField('step_date', '20190706');
const { detail: detail9 } = makeDetail(9, '形变模型', [f9]);
PZ.enhance(detail9);
const bar9 = detail9.querySelector('.pz-bar');
check('第 9 步无档位按钮但有说明与恢复默认',
  bar9.querySelectorAll('.pz-tier').length === 0
  && /场景/.test(bar9.querySelector('.pz-none').textContent)
  && !!bar9.querySelector('.pz-reset'));

/* ================= ⑦ 提交拦截(SET_PARAMS 出口) ================= */
console.log('\n== ⑦ 提交拦截:非法值截停 dock 的 blur/Enter commit ==');
// 非法输入:即时校验红框 + 文案 + 确认按钮禁用
fMin.inp.value = '1.5';
const evIn = fire('input', fMin.inp);
check('input 即时校验:aria-invalid 红框', fMin.inp.getAttribute('aria-invalid') === 'true');
check('input 即时校验:内联错误文案', /不能大于 1/.test(fMin.err.textContent) && !fMin.err.classList.contains('hidden'));
check('已知参数 input 事件被接管(截停 dock 重复校验)', evIn.stopped === true);
check('非法时确认按钮禁用(aria-disabled)',
  bar.querySelector('.pz-apply').disabled === true
  && bar.querySelector('.pz-apply').getAttribute('aria-disabled') === 'true');
// blur:非法 → 截停,dock 收不到提交
const evBlur = fire('blur', fMin.inp);
check('非法值 blur 被截停(无提交)', evBlur.stopped === true && commits.length === 0);
// Enter:非法 → 截停 + preventDefault
const evEnter = fire('keydown', fMin.inp, { key: 'Enter' });
check('非法值 Enter 被截停(无提交)', evEnter.stopped === true && evEnter.defaultPrevented === true && commits.length === 0);
// 合法全角值 → 规范化后放行,dock 收到规范文本
fMin.inp.value = '\uFF10\uFF0E\uFF13';   // 全角「0.3」
const evOk = fire('blur', fMin.inp);
check('合法全角值放行并规范化提交(0.3)', evOk.stopped === false && commits.includes('min_coherence=0.3'));
check('放行后错误态清除', fMin.inp.getAttribute('aria-invalid') === 'false' && fMin.err.classList.contains('hidden'));
check('确认按钮恢复可用', bar.querySelector('.pz-apply').disabled === false);
commits.length = 0;
// 未知参数:输入/失焦全程零干扰
fMys.inp.value = '!!not-a-number!!';
const evMysIn = fire('input', fMys.inp);
const evMysBlur = fire('blur', fMys.inp);
check('未知参数 input 不拦截、不标记',
  evMysIn.stopped === false && fMys.inp.getAttribute('aria-invalid') === null && fMys.err.textContent === '');
check('未知参数 blur 原样放行提交', evMysBlur.stopped === false && commits.includes('mystery=!!not-a-number!!'));

/* ================= ⑧ 静态接线 ================= */
console.log('\n== ⑧ 静态接线(index.html / presets.css) ==');
const fs = await import('node:fs');
const html = fs.readFileSync(new URL('./index.html', import.meta.url), 'utf-8');
check('index.html 恰好一行引入 presets.js',
  (html.match(/presets\.js/g) || []).length === 1
  && html.includes('<script type="module" src="js/presets.js"></script>'));
check('index.html 恰好一行引入 presets.css',
  (html.match(/presets\.css/g) || []).length === 1
  && html.includes('<link rel="stylesheet" href="css/presets.css">'));
const css = fs.readFileSync(new URL('./css/presets.css', import.meta.url), 'utf-8');
check('presets.css 含预设条/气泡/待确认/高亮样式',
  css.includes('.pz-bar') && css.includes('.pz-tip') && css.includes('.pz-pending')
  && css.includes(".pz-btn[aria-pressed='true']") && css.includes('.pz-info'));
check('presets.css 只用语义色令牌(无裸 hex 颜色)', !/#[0-9a-fA-F]{3,8}\b/.test(css));

console.log(failed ? `\n${failed} 项断言失败` : '\n全部断言通过');
process.exit(failed ? 1 : 0);
