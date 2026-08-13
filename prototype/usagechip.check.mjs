/* ============================================================
   usagechip 的无浏览器自查脚本(node prototype/usagechip.check.mjs)
   用最小 DOM stub 直测 js/usagechip.js:
   ① 文案纯函数(fmtCNY / fmtTokens / localDayString / chipText);
   ② hidden 语义(shouldShow / paintChip:无用量保持隐藏、清零收回);
   ③ #budget 归属仲裁(磁盘预算显示期间让位,不覆写);
   ④ 面板内容构建(按日/按模型表、未知成本显「—」、XSS 安全);
   ⑤ 静态接线(index.html 一行引入 + #budget 元素在场)。
   stub 裁剪自 emptystate.check.mjs;零 npm 依赖。
   ============================================================ */

/* ---------------- 最小 DOM stub ---------------- */
class NodeBase {
  constructor() { this.childNodes = []; this.parentNode = null; }
  get textContent() { return this.childNodes.map((c) => c.textContent).join(''); }
}

class TextNode extends NodeBase {
  constructor(s) { super(); this.nodeType = 3; this.data = String(s); }
  get textContent() { return this.data; }
  set textContent(v) { this.data = String(v); }
  remove() { detach(this); }
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
    this.listeners = {}; this.hidden = false; this.title = '';
  }
  get className() { return [...this._cls].join(' '); }
  set className(v) { this._cls = new Set(String(v).split(/\s+/).filter(Boolean)); }
  get classList() {
    const s = this._cls;
    return { add: (...cs) => cs.forEach((c) => s.add(c)), contains: (c) => s.has(c) };
  }
  setAttribute(k, v) { if (k === 'class') { this.className = v; return; } this.attrs[k] = String(v); }
  getAttribute(k) { return k === 'class' ? this.className : (this.attrs[k] ?? null); }
  appendChild(n) { detach(n); n.parentNode = this; this.childNodes.push(n); return n; }
  append(...ns) { ns.forEach((n) => this.appendChild(n instanceof NodeBase ? n : new TextNode(n))); }
  replaceChildren(...ns) {
    [...this.childNodes].forEach(detach);
    this.append(...ns.filter((n) => n !== null && n !== undefined));
  }
  remove() { detach(this); }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  dispatchEvent(evt) {
    evt.target ||= this; evt.currentTarget = this;
    (this.listeners[evt.type] || []).forEach((fn) => fn.call(this, evt));
    return true;
  }
  click() { this.dispatchEvent({ type: 'click', preventDefault() {}, target: this }); }
  focus() {}
  contains(n) { for (const d of walk(this)) if (d === n) return true; return this === n; }
  getBoundingClientRect() { return { top: 8, bottom: 40, left: 700, right: 860 }; }
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
    const out = [];
    for (const d of walk(this)) if (d.matches(sel)) out.push(d);
    return out;
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}

function* walk(n) { for (const c of n.children) { yield c; yield* walk(c); } }

const DOC = {
  body: new Element('body'),
  listeners: {},
  readyState: 'complete',
  createElement: (t) => new Element(t),
  createElementNS: (_ns, t) => new Element(t),
  createTextNode: (s) => new TextNode(s),
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); },
  removeEventListener() {},
  dispatchEvent(evt) { (this.listeners[evt.type] || []).forEach((fn) => fn(evt)); return true; },
  getElementById(id) {
    for (const d of walk(this.body)) if (d.attrs.id === id) return d;
    return null;
  },
};

globalThis.document = DOC;   // dom.js 的 h()/txt() 在调用时取全局 document
globalThis.Node = NodeBase;
/* 刻意不定义 globalThis.window:usagechip.js 的自初始化守卫依赖 window,
   本脚本必须拿到「未启动」的模块实例(不 fetch、不挂定时器)。 */

/* ---------------- 断言工具(check_frontend 解析约定:两空格 ok/FAIL) ---------------- */
let failed = 0;
let count = 0;
function check(name, cond) {
  count += 1;
  if (cond) { console.log(`  ok  ${name}`); }
  else { failed += 1; console.error(`  FAIL ${name}`); }
}

/* ---------------- 被测模块 ---------------- */
const U = await import('./js/usagechip.js');
check('node 环境未自初始化(无 window 守卫生效,body 无芯片残留)',
  DOC.getElementById('llmUsageChip') === null);

/* ---------------- ① 文案纯函数 ---------------- */
console.log('== ① 文案纯函数 ==');

check('localDayString:补零形态 YYYY-MM-DD',
  U.localDayString(new Date(2026, 0, 5)) === '2026-01-05');
check('localDayString:双位月日原样',
  U.localDayString(new Date(2026, 11, 31)) === '2026-12-31');

check('fmtCNY:null/undefined/NaN → null(未知不编数)',
  U.fmtCNY(null) === null && U.fmtCNY(undefined) === null && U.fmtCNY(NaN) === null);
check('fmtCNY:0.034 → ¥0.03(两位小数)', U.fmtCNY(0.034) === '¥0.03');
check('fmtCNY:12.5 → ¥12.50', U.fmtCNY(12.5) === '¥12.50');
check('fmtCNY:0 → ¥0.00', U.fmtCNY(0) === '¥0.00');
check('fmtCNY:正但不足一分 → <¥0.01(不显示假零)', U.fmtCNY(0.004) === '<¥0.01');

check('fmtTokens:null → —(未知)', U.fmtTokens(null) === '—');
check('fmtTokens:999 原样 / 1500 → 1.5k / 34000 → 34k / 2.5e6 → 2.5M',
  U.fmtTokens(999) === '999' && U.fmtTokens(1500) === '1.5k'
  && U.fmtTokens(34000) === '34k' && U.fmtTokens(2500000) === '2.5M');

const TODAY = U.localDayString();
const SUM = {
  days: 7,
  total: { calls: 12, prompt_tokens: 34000, completion_tokens: 5600, cost_est_cny: 0.034 },
  by_model: [
    { model: 'deepseek-v4-flash', calls: 10, prompt_tokens: 30000,
      completion_tokens: 5000, cost_est_cny: 0.03 },
    { model: 'kimi-k2.5', calls: 2, prompt_tokens: 4000,
      completion_tokens: 600, cost_est_cny: null },
  ],
  by_day: [{ day: TODAY, calls: 12, prompt_tokens: 34000,
             completion_tokens: 5600, cost_est_cny: 0.034 }],
  by_session: [], recent: [],
};

check('chipText:今日有量 → 「今日 ¥0.03 · 12 次调用」',
  U.chipText(SUM, TODAY) === '今日 ¥0.03 · 12 次调用');

const SUM_YESTERDAY = { ...SUM, by_day: [{ ...SUM.by_day[0], day: '2020-01-01' }] };
check('chipText:今日零调用 → 回退窗口口径「7 天 …」',
  U.chipText(SUM_YESTERDAY, TODAY) === '7 天 ¥0.03 · 12 次调用');

const SUM_NO_COST = { ...SUM, by_day: [{ ...SUM.by_day[0], cost_est_cny: null }] };
check('chipText:成本未知 → 只报次数,不显示假 ¥',
  U.chipText(SUM_NO_COST, TODAY) === '今日 12 次调用');

const EMPTY = { days: 7, total: { calls: 0, prompt_tokens: null,
  completion_tokens: null, cost_est_cny: null }, by_model: [], by_day: [], recent: [] };
check('chipText:零用量 → 空串', U.chipText(EMPTY, TODAY) === '');
check('shouldShow:有量 true / 零量 false / null false',
  U.shouldShow(SUM) === true && U.shouldShow(EMPTY) === false && U.shouldShow(null) === false);

/* ---------------- ② hidden 语义(paintChip) ---------------- */
console.log('\n== ② hidden 语义 ==');

const box = new Element('span');
box.setAttribute('id', 'budget');
box.className = 'budget';
box.hidden = true;
DOC.body.appendChild(box);

check('无用量 + 隐藏中 → 不画、保持 hidden',
  U.paintChip(box, EMPTY, TODAY) === false && box.hidden === true
  && box.children.length === 0);

let clicked = 0;
check('有用量 → 画出芯片按钮并取消 hidden',
  U.paintChip(box, SUM, TODAY, () => { clicked += 1; }) === true
  && box.hidden === false && box.classList.contains('is-usage'));
const chip = box.querySelector('button');
check('芯片是可聚焦的 button,文案正确',
  !!chip && chip.getAttribute('id') === 'llmUsageChip'
  && chip.textContent === '今日 ¥0.03 · 12 次调用');
check('a11y:aria-haspopup=dialog + aria-expanded=false',
  chip.getAttribute('aria-haspopup') === 'dialog'
  && chip.getAttribute('aria-expanded') === 'false');
chip.click();
check('点击回调接通(面板开关由回调侧处理)', clicked === 1);

check('用量清零 → 收回芯片:内容清空、恢复 hidden、摘掉 is-usage 标记',
  U.paintChip(box, EMPTY, TODAY) === false && box.hidden === true
  && box.children.length === 0 && !box.classList.contains('is-usage'));

/* ---------------- ③ #budget 归属仲裁 ---------------- */
console.log('\n== ③ 归属仲裁(磁盘预算优先) ==');

box.className = 'budget is-warn';           // 模拟 app.js paintBudget 的磁盘告警态
box.hidden = false;
box.replaceChildren(new TextNode('⚠ 磁盘 6.8G'));
check('canPaint:磁盘显示中(可见且非 is-usage)→ 不可写',
  U.canPaint(box) === false);
check('paintChip:让位 —— 不覆写磁盘告警内容',
  U.paintChip(box, SUM, TODAY) === false
  && box.textContent === '⚠ 磁盘 6.8G' && box.classList.contains('is-warn'));

box.hidden = true;                           // 磁盘条自行隐藏(>20G)后可接管
check('canPaint:磁盘条隐藏后可接管', U.canPaint(box) === true);
check('paintChip:接管并重画为用量芯片',
  U.paintChip(box, SUM, TODAY) === true && box.classList.contains('is-usage'));
check('canPaint:已是本模块的(is-usage)可原位刷新', U.canPaint(box) === true);

/* ---------------- ④ 面板内容构建 ---------------- */
console.log('\n== ④ 面板内容 ==');

const panel = U.buildPanelContent(SUM, TODAY);
const text = panel.textContent;
check('合计行:次数 + tokens + 成本',
  text.includes('12 次调用') && text.includes('34k 入 / 5.6k 出') && text.includes('¥0.03'));
check('按日表:今日行带「(今日)」标注', text.includes(`${TODAY}(今日)`));
check('按模型表:两个模型都在', text.includes('deepseek-v4-flash') && text.includes('kimi-k2.5'));
check('成本未知显「—」(kimi 行,绝不编数)', (() => {
  const kimi = panel.querySelectorAll('tr')   // stub 的选择器只支持单段,直接筛 tr
    .find((r) => r.textContent.includes('kimi-k2.5'));
  return !!kimi && kimi.textContent.endsWith('—');
})());
check('脚注声明估算口径', text.includes('不编数'));

const XSS = { ...SUM, by_model: [{ model: '<img src=x onerror=alert(1)>', calls: 1,
  prompt_tokens: 1, completion_tokens: 1, cost_est_cny: null }] };
const dirty = U.buildPanelContent(XSS, TODAY);
check('XSS:恶意模型名走 textContent,不产生元素',
  dirty.querySelectorAll('img').length === 0
  && dirty.textContent.includes('<img src=x onerror=alert(1)>'));

/* ---------------- ⑤ 静态接线 ---------------- */
console.log('\n== ⑤ 静态接线(index.html) ==');

const fs = await import('node:fs');
const html = fs.readFileSync(new URL('./index.html', import.meta.url), 'utf-8');
check('index.html 恰好一行引入 js/usagechip.js',
  (html.match(/src="js\/usagechip\.js"/g) || []).length === 1);
check('#budget 元素在场且初始 hidden(芯片的宿主)',
  /<span class="budget" id="budget"[^>]*hidden>/.test(html));
check('usagechip 不需要内联 <style>(CSP style-src \'self\' 约束)',
  !fs.readFileSync(new URL('./js/usagechip.js', import.meta.url), 'utf-8')
    .includes('createElement(\'style\''));

/* ---------------- 汇总 ---------------- */
console.log(`\n共 ${count} 项,失败 ${failed} 项`);
process.exit(failed ? 1 : 0);
