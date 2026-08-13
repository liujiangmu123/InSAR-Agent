/* ============================================================
   斜杠命令面板的无浏览器自查脚本(node prototype/slash.check.mjs)
   与 cmdk.check.mjs 同一套纪律:最小 DOM/window stub 直接 import
   js/slash.js,覆盖 —— 命令解析器(全命令 + 边界:多空格/全角数字与
   标点/中文数字/非法值)/ 补全过滤逻辑 / payload 映射正确性 /
   面板 listbox 语义与键盘导航 / 提交拦截(合法入队、非法红字不发
   请求、Esc 一次性放行)/ 本地卡片渲染 / 静态接线检查。
   只依赖 node 内建能力,零 npm 依赖。
   ============================================================ */

/* ---------------- 最小 DOM stub(cmdk 版 + insertBefore/nextSibling) ---------------- */
class NodeBase {
  constructor() { this.childNodes = []; this.parentNode = null; }
  get isConnected() {
    let n = this;
    while (n.parentNode) n = n.parentNode;
    return n === DOC.body;
  }
  get textContent() { return this.childNodes.map((c) => c.textContent).join(''); }
  get nextSibling() {
    if (!this.parentNode) return null;
    const i = this.parentNode.childNodes.indexOf(this);
    return this.parentNode.childNodes[i + 1] || null;
  }
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
    this.hidden = false; this.disabled = false;
    this.scrollTop = 0; this.scrollHeight = 0; this.clientHeight = 0;
    this.value = '';
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
  appendChild(n) {
    if (n instanceof Fragment) { [...n.childNodes].forEach((c) => this.appendChild(c)); return n; }
    detach(n); n.parentNode = this; this.childNodes.push(n); return n;
  }
  insertBefore(n, ref) {
    if (!ref) return this.appendChild(n);
    detach(n);
    const i = this.childNodes.indexOf(ref);
    if (i < 0) return this.appendChild(n);
    n.parentNode = this;
    this.childNodes.splice(i, 0, n);
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
  focus() { DOC.activeElement = this; }
  blur() { if (DOC.activeElement === this) DOC.activeElement = DOC.body; }
  scrollIntoView() {}
  setSelectionRange() {}
  set textContent(v) { this.replaceChildren(new TextNode(v)); }
  get textContent() { return super.textContent; }
  get children() { return this.childNodes.filter((c) => c.nodeType === 1); }
  set innerHTML(v) { this.replaceChildren(new TextNode(v)); }
  contains(n) { for (let x = n; x; x = x.parentNode) if (x === this) return true; return false; }
  matches(sel) {
    const m = /^([a-z0-9-]*)(?:#([\w-]+))?((?:\.[\w-]+)*)((?:\[[\w-]+\])*)$/.exec(String(sel).trim());
    if (!m) return false;
    if (m[1] && this.tagName !== m[1]) return false;
    if (m[2] && this.attrs.id !== m[2]) return false;
    if (!(m[3].match(/\.[\w-]+/g) || []).every((c) => this._cls.has(c.slice(1)))) return false;
    return (m[4].match(/\[[\w-]+\]/g) || []).every((a) => this.attrs[a.slice(1, -1)] !== undefined);
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
  head: new Element('head'),
  documentElement: new Element('html'),
  activeElement: null,
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
DOC.activeElement = DOC.body;

globalThis.document = DOC;
globalThis.Node = NodeBase;
globalThis.Event = class { constructor(type, opts = {}) { this.type = type; Object.assign(this, opts); } };
globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
globalThis.requestAnimationFrame = (fn) => { fn(); return 0; };
globalThis.matchMedia = () => ({ matches: true });
globalThis.location = { protocol: 'http:' };
globalThis.window = {
  addEventListener() {}, removeEventListener() {}, dispatchEvent() { return true; },
};

/* fetch stub:GET /api/registry 返回不可用(走 STEP_DEFS 回退);
   POST /api/actions 记录载荷并回 202。 */
const posted = [];
globalThis.fetch = async (url, opts = {}) => {
  if ((opts.method || 'GET') === 'POST') {
    posted.push({ url, body: JSON.parse(opts.body) });
    return { ok: true, status: 202, json: async () => ({ accepted: true, id: posted.length }) };
  }
  return { ok: false, status: 503, json: async () => null };
};

/* ---------------- 断言工具 ---------------- */
let failed = 0;
function check(name, cond) {
  if (cond) { console.log(`  ok  ${name}`); }
  else { failed += 1; console.error(`  FAIL ${name}`); }
}
const tick = () => new Promise((r) => setTimeout(r, 10));
function keyEvt(key, opts = {}) {
  return {
    type: 'keydown', key, isComposing: false, shiftKey: false,
    defaultPrevented: false, stopped: false,
    preventDefault() { this.defaultPrevented = true; },
    stopPropagation() { this.stopped = true; },
    ...opts,
  };
}

/* ---------------- 页面骨架(index.html 的 composer 结构) ---------------- */
const composerIn = new Element('div'); composerIn.setAttribute('class', 'composer-in');
const box = new Element('div'); box.setAttribute('class', 'box');
const input = new Element('textarea'); input.setAttribute('id', 'prompt');
const btnAttach = new Element('button'); btnAttach.setAttribute('id', 'btnAttach');
const cmeta = new Element('div'); cmeta.setAttribute('class', 'cmeta');
box.appendChild(input); box.appendChild(btnAttach);
composerIn.appendChild(box); composerIn.appendChild(cmeta);
DOC.body.appendChild(composerIn);
const stream = new Element('div'); stream.setAttribute('id', 'stream');
DOC.body.appendChild(stream);

/* ---------------- 被测模块 ---------------- */
const Slash = await import('./js/slash.js');
const St = await import('./js/state.js');
const Stream = await import('./js/stream.js');
// 步骤目录/镜像不再内置演示种子:注册表快照水合 + 模拟服务端计划(1–5 done,6–11 pending)
const { REGISTRY, seedSteps } = await import('../tests/js/_registry.mjs');
St.setRegistry(REGISTRY);
seedSteps(St, 5);
Stream.mount(stream);     // /status /help 卡片渲染进轨迹流

const type = (text) => {
  input.value = text;
  input.dispatchEvent({ type: 'input', target: input });
};
const pop = () => DOC.getElementById('slashPop');
const options = () => pop().querySelectorAll('.slash-item');
const activeId = () => input.getAttribute('aria-activedescendant');

/* ---------------- 纯逻辑测试夹具(不依赖浏览器状态) ---------------- */
const CTX = { steps: Array.from({ length: 11 }, (_, i) => ({
  id: i + 1, name: `步骤${i + 1}`, methods: [], params: {},
  current: { method: 'm0', params: {}, state: 'pending', stale: false, fingerprint: '' },
})) };
CTX.steps[5] = {
  id: 6, name: '解缠',
  methods: [
    { id: 'snaphu_mcf', label: 'snaphu_mcf', ok: true, recommend: true, why: 'MCF', extra: '' },
    { id: 'snaphu_smooth', label: 'snaphu_smooth', ok: true, why: '', extra: '' },
    { id: 'icu', label: 'icu', ok: true, why: '区域增长', extra: '' },
    { id: '3D_FULL', label: '3D_FULL', ok: false, blocked: '工具链缺失', why: '', extra: '' },
  ],
  params: {
    min_coherence: { default: 0.25, type: 'number', min: 0, max: 1, hint: '相干性阈值须在 0–1 之间' },
    threads: { default: 8, type: 'int', min: 1, max: 32, hint: '线程数 1–32' },
  },
  current: { method: 'snaphu_mcf', params: { min_coherence: 0.25, threads: 8 },
    state: 'stale', stale: true, fingerprint: 'ab12…cd34' },
};
CTX.steps[7] = {
  id: 8, name: '误差校正',
  methods: [{ id: 'tropo_era5_pyaps', label: 'tropo_era5_pyaps', ok: true }],
  params: {
    dem_error: { default: true, type: 'bool' },
    ramp: { default: 'linear', type: 'str' },
  },
  current: { method: 'tropo_era5_pyaps', params: { dem_error: true, ramp: 'linear' },
    state: 'pending', stale: false, fingerprint: '' },
};

console.log('== ① 命令注册表 ==');
check('注册表共 10 条命令', Slash.COMMANDS.length === 10);
check('命令名闭集与任务书一致',
  Slash.COMMANDS.map((c) => c.name).join(',')
  === 'run,pause,resume,kill,reset,skip,method,param,status,help');
check('usage 均以 / 开头且带用法', Slash.COMMANDS.every((c) => c.usage.startsWith(`/${c.name}`)));
check('干预类命令映射动作闭集',
  Slash.COMMANDS.filter((c) => c.action).map((c) => c.action).join(',')
  === 'PAUSE,PLAY,KILL,RESET,SKIP,SET_METHOD,SET_PARAMS');

console.log('\n== ② 归一化(全角/中文容错) ==');
check('全角数字 → 半角', Slash.normalizeSlash('/skip ６') === '/skip 6');
check('全角等号/数字 → 半角', Slash.normalizeSlash('/param 6 threads＝８') === '/param 6 threads=8');
check('顿号/全角逗号 → 半角逗号', Slash.normalizeSlash('/run 6、7，8') === '/run 6,7,8');
check('全角空格与行首空白折叠', Slash.normalizeSlash('　 /run　6') === '/run 6');
check('全角斜杠开头也识别为命令', Slash.isSlashText('／run') === true);
check('普通文本不误判', Slash.isSlashText('分析玉树冻土') === false);
check('中文数字步骤号:十一 → 11', Slash.stepNum('十一') === 11);
check('中文数字步骤号:六 → 6', Slash.stepNum('六') === 6);

console.log('\n== ③ 解析器:全命令 ==');
let p = Slash.parseSlash('/run', CTX);
check('/run(无参)→ 待办语义 ids=null', p.ok && p.name === 'run' && p.ids === null);
p = Slash.parseSlash('/run 全部', CTX);
check('/run 全部 → ids=all', p.ok && p.ids === 'all');
check('/run all(英文)等价', Slash.parseSlash('/run all', CTX).ids === 'all');
p = Slash.parseSlash('/run 6,7 9', CTX);
check('/run 逗号+空格混合清单', p.ok && JSON.stringify(p.ids) === '[6,7,9]');
check('/run 重复步骤去重', JSON.stringify(Slash.parseSlash('/run 6 6', CTX).ids) === '[6]');
for (const name of ['pause', 'resume', 'kill', 'status', 'help']) {
  p = Slash.parseSlash(`/${name}`, CTX);
  check(`/${name} 解析通过`, p.ok && p.name === name);
}
p = Slash.parseSlash('/reset 6', CTX);
check('/reset 6', p.ok && p.name === 'reset' && p.step === 6);
p = Slash.parseSlash('/skip 十一', CTX);
check('/skip 十一(中文数字)→ 11', p.ok && p.step === 11);
p = Slash.parseSlash('/method 6 icu', CTX);
check('/method 6 icu', p.ok && p.step === 6 && p.method === 'icu');
p = Slash.parseSlash('/param 6 min_coherence=0.3', CTX);
check('/param 数值定型为 number', p.ok && p.key === 'min_coherence' && p.value === 0.3);
p = Slash.parseSlash('/param 8 dem_error=true', CTX);
check('/param bool 定型为布尔', p.ok && p.value === true);
p = Slash.parseSlash('/param 8 ramp=quadratic', CTX);
check('/param str 保持字符串', p.ok && p.value === 'quadratic');

console.log('\n== ④ 解析器:边界与非法值(红字,不发请求) ==');
check('多空格容错', Slash.parseSlash('/method   6    icu', CTX).ok);
check('全角步骤号容错', Slash.parseSlash('/method ６ icu', CTX).ok);
check('命令名大小写不敏感', Slash.parseSlash('/RUN 6', CTX).ok);
check('未知命令报错', !Slash.parseSlash('/xyz', CTX).ok
  && Slash.parseSlash('/xyz', CTX).error.includes('未知命令'));
check('/pause 带参报错', !Slash.parseSlash('/pause 1', CTX).ok);
check('/run 0 越界报错', !Slash.parseSlash('/run 0', CTX).ok);
check('/run 12 越界报错', !Slash.parseSlash('/run 12', CTX).ok);
check('/run x 非数字报错', !Slash.parseSlash('/run x', CTX).ok);
check('/reset 缺步骤号报错', !Slash.parseSlash('/reset', CTX).ok);
check('/reset 多参报错', !Slash.parseSlash('/reset 6 7', CTX).ok);
p = Slash.parseSlash('/method 6 nope', CTX);
check('非法方法 id 报错并给候选', !p.ok && p.error.includes('snaphu_mcf'));
check('非法步骤上改方法报错', !Slash.parseSlash('/method 99 icu', CTX).ok);
p = Slash.parseSlash('/param 6 nope=1', CTX);
check('非法参数名报错并给可用清单', !p.ok && p.error.includes('min_coherence'));
p = Slash.parseSlash('/param 6 min_coherence=1.5', CTX);
check('数值越界报 hint', !p.ok && p.error.includes('0–1'));
check('int 参数拒绝小数', !Slash.parseSlash('/param 6 threads=8.5', CTX).ok);
check('缺值报错', !Slash.parseSlash('/param 6 min_coherence=', CTX).ok);
check('缺等号报写法提示', !Slash.parseSlash('/param 6 min_coherence 0.3', CTX).ok);
check('bool 参数拒绝非 true/false', !Slash.parseSlash('/param 8 dem_error=maybe', CTX).ok);

console.log('\n== ⑤ 补全过滤逻辑 ==');
let s = Slash.suggest('/', CTX);
check('「/」列出全部 10 条命令', s.open && s.items.length === 10);
s = Slash.suggest('/me', CTX);
check('「/me」前缀过滤到 /method,补全带尾空格',
  s.items.length === 1 && s.items[0].insert === '/method ');
s = Slash.suggest('/pa', CTX);
check('「/pa」命中 pause 与 param', s.items.map((i) => i.label).join(',') === '/pause,/param');
s = Slash.suggest('/method ', CTX);
check('「/method␣」列出全部 11 步', s.stage === 'step' && s.items.length === 11);
check('步骤候选带名称/状态/当前方法', s.items[5].detail.includes('解缠')
  && s.items[5].detail.includes('STALE') && s.items[5].detail.includes('snaphu_mcf'));
s = Slash.suggest('/method 6 ', CTX);
check('「/method 6␣」列该步 4 个方法', s.stage === 'method' && s.items.length === 4);
check('被禁方法带「不可用」徽标及原因',
  s.items.find((i) => i.label === '3D_FULL')?.badge === '不可用:工具链缺失');
check('当前方法带「当前」徽标', s.items.find((i) => i.label === 'snaphu_mcf')?.badge === '当前');
s = Slash.suggest('/method 6 sn', CTX);
check('方法子串过滤', s.items.length === 2
  && s.items.every((i) => i.label.startsWith('snaphu')));
s = Slash.suggest('/method 6 zz', CTX);
check('无匹配方法 → 红字带候选、无候选项', s.items.length === 0 && s.error.includes('候选'));
s = Slash.suggest('/param 6 ', CTX);
check('「/param 6␣」列参数名并显示当前值',
  s.items.length === 2 && s.items[0].detail.includes('当前 0.25'));
check('参数补全带 = 号', s.items[0].insert === '/param 6 min_coherence=');
s = Slash.suggest('/param 6 min', CTX);
check('参数名子串过滤', s.items.length === 1 && s.items[0].label === 'min_coherence');
s = Slash.suggest('/param 6 min_coherence=2', CTX);
check('值越界即时红字 + meta 行(不发请求)',
  s.error?.includes('0–1') && s.meta?.includes('当前 0.25') && !s.ready);
s = Slash.suggest('/param 8 dem_error=', CTX);
check('bool 值阶段给 true/false 候选', s.items.map((i) => i.label).join(',') === 'true,false');
s = Slash.suggest('/run ', CTX);
check('「/run␣」候选含「全部」', s.items[0].label === '全部' && s.items.length === 12);
s = Slash.suggest('/run 6,', CTX);
check('已敲定步骤不再出现,补全续接清单',
  !s.items.some((i) => i.label === '6') && s.items.some((i) => i.insert === '/run 6,7'));
s = Slash.suggest('/pause', CTX);
check('完整命令给「Enter 发送」就绪提示', s.ready?.summary.includes('暂停'));
s = Slash.suggest('/pause x', CTX);
check('无参命令带参 → 红字', s.error === '/pause 不带参数' && !s.ready);
s = Slash.suggest('/method 6 icu', CTX);
check('就绪提示描述具体变更', s.ready?.summary === '第 6 步方法 → icu');

console.log('\n== ⑥ payload 映射(POST /api/actions) ==');
const pay = (text) => Slash.toPayload(Slash.parseSlash(text, CTX), 'sess-x');
let pl = pay('/pause');
check('/pause → PAUSE(run 级,steer)', pl.kind === 'actions' && pl.url === '/api/actions'
  && pl.body.session === 'sess-x' && pl.body.scope === 'run' && pl.body.target === 'run'
  && pl.body.action === 'PAUSE' && pl.body.deliver_as === 'steer');
check('/resume → PLAY', pay('/resume').body.action === 'PLAY');
check('/kill → KILL', pay('/kill').body.action === 'KILL');
pl = pay('/reset 6');
check('/reset 6 → RESET(step 级,target=字符串)', pl.body.action === 'RESET'
  && pl.body.scope === 'step' && pl.body.target === '6' && typeof pl.body.target === 'string');
check('/skip 11 → SKIP target=11', pay('/skip 11').body.target === '11');
pl = pay('/method 6 icu');
check('/method → SET_METHOD payload.method', pl.body.action === 'SET_METHOD'
  && pl.body.payload.method === 'icu');
pl = pay('/param 6 min_coherence=0.3');
check('/param → SET_PARAMS payload.params(值保持 number 类型)',
  pl.body.action === 'SET_PARAMS' && pl.body.payload.params.min_coherence === 0.3);
check('payload 不带 run_id(服务端 resolve 最近 run)',
  !Object.prototype.hasOwnProperty.call(pl.body, 'run_id'));
pl = pay('/run 6');
check('/run → 执行链路(kind=run,ids 显式)', pl.kind === 'run' && JSON.stringify(pl.ids) === '[6]');
check('/status → 本地卡片', pay('/status').kind === 'local');
check('/help → 本地卡片', pay('/help').kind === 'local');

console.log('\n== ⑦ 面板 UI:listbox 语义 + 键盘导航 + 补全 ==');
const badge = DOC.getElementById('slashHint');
check('「/ 命令」徽章挂在输入框容器内且有 aria-label',
  !!badge && box.contains(badge) && !!badge.getAttribute('aria-label'));
badge.click();
check('点击徽章等效输入 /(面板打开)', input.value === '/' && pop().hidden === false);
check('listbox/option 语义', pop().querySelector('#slashList').getAttribute('role') === 'listbox'
  && options().length === 10
  && options().every((o) => o.getAttribute('role') === 'option'));
check('aria-activedescendant 指向首个候选', activeId() === 'slash-opt-cmd-run');
check('输入框 aria-expanded=true', input.getAttribute('aria-expanded') === 'true');
Slash.initSlash();
check('重复初始化幂等(徽章不重复)', DOC.body.querySelectorAll('.slash-hint').length === 1);

const W = globalThis.window;
W.__slash = W.__slash || null;   // initSlash 已挂;直接引用
const hooks = W.__slash;
check('window.__slash 钩子已就位', typeof hooks?.beforeKey === 'function'
  && typeof hooks?.beforeSubmit === 'function');

let e = keyEvt('ArrowDown');
check('↓ 移到第二项且吞掉按键', hooks.beforeKey(e) && e.defaultPrevented
  && activeId() === 'slash-opt-cmd-pause');
e = keyEvt('ArrowUp');
hooks.beforeKey(e);
check('↑ 回到首项', activeId() === 'slash-opt-cmd-run');
e = keyEvt('ArrowUp');
hooks.beforeKey(e);
check('首项 ↑ 环绕到末项(/help)', activeId() === 'slash-opt-cmd-help');

type('/method 6 ');
check('「/method 6␣」实时渲染全部方法候选(STEP_DEFS 回退 = 注册表快照)',
  options().length === St.def_(6).methods.length && options().length >= 4);
e = keyEvt('Tab');
check('Tab 补全首个方法', hooks.beforeKey(e) && e.defaultPrevented
  && input.value === '/method 6 snaphu_mcf');
e = keyEvt('Enter');
check('输入已是完整命令时 Enter 放行给 submit()', hooks.beforeKey(e) === false);

type('/me');
e = keyEvt('Enter');
check('Enter 在补全会改变输入时优先补全', hooks.beforeKey(e) === true
  && input.value === '/method ');

console.log('\n== ⑧ 提交:合法入队 / 非法红字不发请求 / Esc 一次性放行 ==');
const St6before = St.st_(6).method;
check('前置:第 6 步当前方法为注册表默认 snaphu_mcf', St6before === 'snaphu_mcf');
const fp7before = St.st_(7).fingerprint;
type('/method 6 icu');
let handled = hooks.beforeSubmit('/method 6 icu');
await tick();
check('合法命令被接管并 POST /api/actions', handled === true && posted.length === 1
  && posted[0].url === '/api/actions');
check('载荷:SET_METHOD steer + 会话 id', posted[0].body.action === 'SET_METHOD'
  && posted[0].body.payload.method === 'icu' && posted[0].body.deliver_as === 'steer'
  && posted[0].body.session === St.S.sessionId && posted[0].body.target === '6');
check('提交后清空输入并关面板', input.value === '' && pop().hidden === true);
const chip = composerIn.querySelector('.slash-confirm');
check('输入框下方一次性确认(已入队 + 生效语义)', !!chip && chip.hidden === false
  && chip.textContent.includes('已入队') && chip.textContent.includes('icu'));
check('202 受理后本地镜像同步(方法已切换)', St.st_(6).method === 'icu');
// 种子态下 7–11 步是 pending(从未产出),按 state.js 语义不标 STALE,
// 但上游方法变更必须沿依赖图重算下游指纹 —— 以指纹变化验证级联生效
check('本地镜像沿依赖图重算下游指纹(第 7 步)', St.st_(7).fingerprint !== fp7before);

const postsBefore = posted.length;
type('/method 6 nope');
handled = hooks.beforeSubmit('/method 6 nope');
await tick();
check('非法方法 id:接管但不发请求', handled === true && posted.length === postsBefore);
check('非法值红字提示留在面板内', pop().hidden === false
  && pop().querySelector('.slash-status .err')?.textContent.includes('没有方法'));
check('非法提交不清空输入(可就地改)', input.value === '/method 6 nope');

type('/pause');
e = keyEvt('Escape');
check('Esc 关闭面板且阻断冒泡(不触达全局停止)', hooks.beforeKey(e) === true
  && e.stopped && pop().hidden === true);
check('Esc 后按原文放行一次(当普通消息发送)', hooks.beforeSubmit('/pause') === false);
handled = hooks.beforeSubmit('/pause');
await tick();
check('再次提交恢复命令语义(PAUSE 入队)', handled === true
  && posted[posted.length - 1].body.action === 'PAUSE'
  && posted[posted.length - 1].body.scope === 'run');

const runCalls = [];
W.__slashRun = (ids) => runCalls.push(ids);
type('/run 6');
hooks.beforeSubmit('/run 6');
await tick();
check('/run 6 → 借用 app.js 执行链路(未走 /api/actions)',
  JSON.stringify(runCalls) === '[[6]]'
  && posted[posted.length - 1].body.action === 'PAUSE');
St.S.busy = true;
hooks.beforeSubmit('/run');
await tick();
check('运行中 /run 被拒并提示(不重复入队)', runCalls.length === 1
  && composerIn.querySelector('.slash-confirm').textContent.includes('正在运行'));
St.S.busy = false;

console.log('\n== ⑨ /status、/help 本地卡片 ==');
hooks.beforeSubmit('/status');
await tick();
const card = stream.querySelector('.slash-card');
check('/status 渲染步骤矩阵卡(本地,不发请求)', !!card
  && card.querySelectorAll('.slash-matrix .row').length === 11);
check('矩阵含方法/状态列(第 6 步已是 icu,1–5 步有效)',
  card.textContent.includes('icu') && card.textContent.includes('有效')
  && card.textContent.includes('待运行'));
hooks.beforeSubmit('/help');
await tick();
const helpRows = stream.querySelectorAll('.slash-cmds .row');
check('/help 渲染 10 条命令清单卡', helpRows.length === 10);
check('清单含用法与说明', stream.textContent.includes('/param <步骤号> <参数名>=<值>'));

console.log('\n== ⑩ 静态接线检查(index.html / app.js / slash.css) ==');
const fs = await import('node:fs');
const html = fs.readFileSync(new URL('./index.html', import.meta.url), 'utf-8');
check('index.html 恰好两行接线(css + js 各一)',
  (html.match(/slash\.css/g) || []).length === 1
  && (html.match(/slash\.js/g) || []).length === 1
  && html.includes('<link rel="stylesheet" href="css/slash.css">')
  && html.includes('<script type="module" src="js/slash.js"></script>'));
const appJs = fs.readFileSync(new URL('./js/app.js', import.meta.url), 'utf-8');
const wired = (appJs.match(/slash 接线/g) || []).length;
check('app.js 接线 ≤8 行且注明「slash 接线」(实际 3 处)', wired === 3);
check('app.js 三处钩子齐全(beforeKey/beforeSubmit/__slashRun)',
  appJs.includes('window.__slash?.beforeKey(e)')
  && appJs.includes('window.__slash?.beforeSubmit(text)')
  && appJs.includes('window.__slashRun = (ids) => run(ids)'));
const css = fs.readFileSync(new URL('./css/slash.css', import.meta.url), 'utf-8');
check('slash.css 关键样式齐全(面板/激活态/错误行/徽章/确认条)',
  css.includes('.slash-pop {') && css.includes('.slash-item.active')
  && css.includes('.slash-status .err') && css.includes('.slash-hint')
  && css.includes('.slash-confirm'));
check('slash.css 断点命中 base.css 清单(--bp-compact 480)',
  css.includes('@media (max-width: 480px)'));

console.log(failed ? `\n${failed} 项断言失败` : '\n全部断言通过');
process.exit(failed ? 1 : 0);
