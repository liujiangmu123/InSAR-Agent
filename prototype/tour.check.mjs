/* ============================================================
   首访引导的无浏览器自查脚本（node prototype/tour.check.mjs）
   与 cmdk.check.mjs 同一套纪律：最小 DOM/window stub 直接
   import js/tour.js，覆盖 —— 已看记忆（key 带版本号 / 升级重展）/
   可用步骤计算（dock 收起自动缩短）/ 聚光灯几何（四矩形挖洞）/
   步骤状态机（前进/后退/跳过缺失目标/完成落 localStorage）/
   键盘可达（←→/Esc/Tab 圈定）/ 焦点归还 / dock 标签联动与归位 /
   「?」按钮重看 / 与环境向导的协调 / resize 重排 / 静态接线。
   只依赖 node 内建能力，零 npm 依赖。
   ============================================================ */

/* ---------------- 最小 DOM stub（cmdk 版 + 几何/隐藏链扩展） ---------------- */
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
    this.hidden = false; this.disabled = false;
    this.scrollTop = 0; this.scrollHeight = 0; this.clientHeight = 0;
    this.value = '';
    this._rect = null;                       // 由 rect() 助手注入的几何
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
  append(...ns) { ns.forEach((n) => this.appendChild(n instanceof NodeBase ? n : new TextNode(n))); }
  replaceChildren(...ns) {
    [...this.childNodes].forEach(detach);
    this.append(...ns.filter((n) => n !== null && n !== undefined));
  }
  remove() { detach(this); }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  removeEventListener(type, fn) {
    const a = this.listeners[type];
    if (a) { const i = a.indexOf(fn); if (i >= 0) a.splice(i, 1); }
  }
  dispatchEvent(evt) { (this.listeners[evt.type] || []).forEach((fn) => fn.call(this, evt)); return true; }
  click() { this.dispatchEvent({ type: 'click', preventDefault() {}, target: this }); }
  focus() { DOC.activeElement = this; }
  blur() { if (DOC.activeElement === this) DOC.activeElement = DOC.body; }
  scrollIntoView() {}
  set textContent(v) { this.replaceChildren(new TextNode(v)); }
  get textContent() { return super.textContent; }
  get children() { return this.childNodes.filter((c) => c.nodeType === 1); }
  set innerHTML(v) { this.replaceChildren(new TextNode(v)); }
  contains(n) { for (let x = n; x; x = x.parentNode) if (x === this) return true; return false; }
  /* 几何：自身或任一祖先 hidden → 零矩形（模拟 display:none 链） */
  _hiddenDeep() { for (let n = this; n; n = n.parentNode) if (n.hidden) return true; return false; }
  getBoundingClientRect() {
    if (this._hiddenDeep() || !this._rect) {
      return { left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0 };
    }
    return { ...this._rect };
  }
  get offsetWidth() { return this._hiddenDeep() || !this._rect ? 0 : this._rect.width; }
  get offsetHeight() { return this._hiddenDeep() || !this._rect ? 0 : this._rect.height; }
  matches(sel) {
    // 支持「tag#id.cls[attr]」的简单复合选择器（本脚本与 tour.js 的用量足够）
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
globalThis.requestAnimationFrame = (fn) => { fn(); return 0; };
globalThis.matchMedia = () => ({ matches: true });

/* localStorage：功能性 mock（tour 的已看记忆要真正读写） */
const store = new Map();
globalThis.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => { store.set(k, String(v)); },
  removeItem: (k) => { store.delete(k); },
};

/* window stub：捕获监听器可注册可移除，视口尺寸可改（resize 测试用） */
const winListeners = {};
globalThis.window = {
  innerWidth: 1280, innerHeight: 800,
  addEventListener(type, fn) { (winListeners[type] ||= []).push(fn); },
  removeEventListener(type, fn) {
    const a = winListeners[type];
    if (a) { const i = a.indexOf(fn); if (i >= 0) a.splice(i, 1); }
  },
  dispatchEvent() { return true; },
};

/* ---------------- 事件与断言工具 ---------------- */
function keyEvt(key, opts = {}) {
  return {
    type: 'keydown', key, code: '', ctrlKey: false, metaKey: false,
    shiftKey: false, altKey: false, isComposing: false,
    defaultPrevented: false, stopped: false,
    preventDefault() { this.defaultPrevented = true; },
    stopPropagation() { this.stopped = true; },
    ...opts,
  };
}

function fireGlobal(key, opts = {}) {
  const e = keyEvt(key, opts);
  for (const fn of [...(winListeners.keydown || [])]) fn(e);
  return e;
}

function fireResize() {
  for (const fn of [...(winListeners.resize || [])]) fn({ type: 'resize' });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

let failed = 0;
function check(name, cond) {
  if (cond) { console.log(`  ok  ${name}`); }
  else { failed += 1; console.error(`  FAIL ${name}`); }
}

/* ---------------- 页面骨架（index.html 的既有入口元素 + 几何） ---------------- */
function rect(el, left, top, w, h) {
  el._rect = { left, top, width: w, height: h, right: left + w, bottom: top + h };
  return el;
}

function make(tag, id, cls, parent) {
  const el = new Element(tag);
  if (id) el.setAttribute('id', id);
  if (cls) el.setAttribute('class', cls);
  (parent || DOC.body).appendChild(el);
  return el;
}

const clicks = [];

// 输入区：.composer > .composer-in > .box > #prompt
const composer = rect(make('div', null, 'composer'), 240, 680, 800, 110);
const composerIn = rect(make('div', null, 'composer-in', composer), 240, 690, 800, 90);
const box = rect(make('div', null, 'box', composerIn), 250, 700, 780, 70);
rect(make('textarea', 'prompt', null, box), 260, 706, 700, 40);

// 左侧导航条：主题按钮 + 「?」引导按钮
const btnTheme = rect(make('button', 'btnTheme', 'rail-btn'), 8, 700, 36, 36);
btnTheme.addEventListener('click', () => clicks.push('btnTheme'));
const btnTour = rect(make('button', 'btnTour', 'rail-btn'), 8, 744, 36, 36);

// 右侧 dock：标签条 + 各面板（几何与 dock.js 渲染结果同构）
const dock = rect(make('aside', 'dock'), 880, 0, 400, 800);
const dockTabs = rect(make('div', 'dockTabs', null, dock), 880, 0, 400, 36);
const dockBody = rect(make('div', 'dockBody', null, dock), 880, 36, 400, 764);
const PANE_IDS = ['pipeline', 'images', 'files', 'audit', 'report', 'web', 'trace', 'term', 'env'];
const tabBtn = {};
const pane = {};
PANE_IDS.forEach((id, i) => {
  tabBtn[id] = rect(make('button', `tab-${id}`, null, dockTabs), 884 + i * 44, 4, 40, 28);
  pane[id] = rect(make('section', `pane-${id}`, null, dockBody), 880, 36, 400, 764);
  tabBtn[id].addEventListener('click', () => { clicks.push(`tab-${id}`); selectTab(id); });
});
const pdetail = rect(make('div', null, 'pdetail', pane.pipeline), 890, 300, 380, 220);

function selectTab(id) {
  for (const t of PANE_IDS) {
    tabBtn[t].setAttribute('aria-selected', String(t === id));
    pane[t].hidden = t !== id;
  }
}
selectTab('images');   // 起始选中「影像」：迫使第 2 步真的去点流水线标签

/* ---------------- 被测模块 ----------------
   预置「已看」标记再 import：模块自初始化的 autoStart 会同步早退，
   保证本脚本的时序完全可控（防御：真开了就立刻关掉）。 */
store.set('ia-tour-seen-v1', 'preseed');
const Tour = await import('./js/tour.js');
if (Tour.isOpen()) Tour.closeTour();

const holeOf = () => DOC.querySelector('.tour-hole');
const cardOf = () => DOC.querySelector('.tour-card');
const masksOf = () => DOC.body.querySelector('.tour').children.filter((c) => c.classList.contains('tour-mask'));
const titleText = () => cardOf().querySelector('.tour-title').textContent;
const stepText = () => cardOf().querySelector('.tour-step').textContent;
const overlayCount = () => DOC.querySelectorAll('.tour').length;

console.log('== ① 已看记忆：key 带版本号 / 升级重展 ==');
check('SEEN_KEY 与 seenKeyFor(TOUR_VERSION) 一致且带版本号',
  Tour.SEEN_KEY === Tour.seenKeyFor(Tour.TOUR_VERSION)
  && Tour.seenKeyFor(3) === 'ia-tour-seen-v3');
const mem = { data: new Map(), getItem(k) { return this.data.get(k) ?? null; }, setItem(k, v) { this.data.set(k, v); } };
check('全新 storage → 需要自动播放', Tour.shouldAutoShow(mem) === true);
Tour.markSeen(mem);
check('markSeen 后同版本不再播放', Tour.shouldAutoShow(mem) === false);
check('版本号升级（文案大改）→ 重新展示', Tour.shouldAutoShow(mem, Tour.TOUR_VERSION + 1) === true);
const broken = { getItem() { throw new Error('denied'); }, setItem() { throw new Error('denied'); } };
check('storage 异常（隐私模式）按已看处理，不骚扰', Tour.shouldAutoShow(broken) === false);

console.log('\n== ② 可用步骤：dock 收起自动缩短 ==');
check('步骤定义恰好五步', Tour.STEPS.length === 5);
check('全部就绪 → 5 步可用', Tour.availableSteps().length === 5);
dock.hidden = true;
const shrunk = Tour.availableSteps();
check('dock 收起 → 只剩聊天框一步', shrunk.length === 1 && shrunk[0].id === 'chat');
dock.hidden = false;

console.log('\n== ③ 启动：遮罩 / 卡片 / ARIA ==');
store.delete(Tour.SEEN_KEY);
const ctl = Tour.startTour();
check('startTour 返回控制器且渲染唯一浮层', !!ctl && Tour.isOpen() && overlayCount() === 1);
check('重复 startTour 幂等（仍只有一个浮层）', Tour.startTour() === ctl && overlayCount() === 1);
const card = cardOf();
check('卡片 role=dialog + aria-modal + 标题/描述关联',
  card.getAttribute('role') === 'dialog' && card.getAttribute('aria-modal') === 'true'
  && card.getAttribute('aria-labelledby') === 'tourTitle'
  && card.getAttribute('aria-describedby') === 'tourBody');
check('四矩形遮罩 + 聚光洞就位', masksOf().length === 4 && holeOf() && holeOf().hidden === false);
check('第 1 步讲聊天框，计数「第 1 步 · 共 5 步」',
  titleText().includes('聊天框') && stepText() === '第 1 步 · 共 5 步');
check('示例任务可见（玉树冻土 SBAS）',
  cardOf().querySelector('.tour-eg').hidden === false
  && cardOf().querySelector('.tour-eg').textContent.includes('玉树冻土'));
check('进度点 5 个且第 1 个点亮',
  cardOf().querySelector('.tour-dots').children.length === 5
  && cardOf().querySelector('.tour-dots').children[0].classList.contains('on'));
const btns = cardOf().querySelectorAll('.tour-btn');
const [btnPrev, btnNext] = btns;
check('首步「上一步」禁用、「下一步」为主按钮', btnPrev.disabled === true && btnNext.textContent === '下一步');
check('初始焦点落在「下一步」', DOC.activeElement === btnNext);

console.log('\n== ④ 聚光灯几何：洞对准目标，四矩形拼满全屏 ==');
const hole = holeOf();
check('洞 = 输入框外扩 8px（250,700,780×70 → 242,692,796×86）',
  hole.style.left === '242px' && hole.style.top === '692px'
  && hole.style.width === '796px' && hole.style.height === '86px');
const [mT, mL, mR, mB] = masksOf();
check('上/左遮罩与洞无缝拼接',
  mT.style.top === '0px' && mT.style.width === '1280px' && mT.style.height === '692px'
  && mL.style.left === '0px' && mL.style.width === '242px' && mL.style.height === '86px');
check('右/下遮罩与洞无缝拼接',
  mR.style.left === '1038px' && mR.style.width === '242px'
  && mB.style.top === '778px' && mB.style.height === '22px');
check('卡片放在目标上方且不出屏', card.style.top === '458px' && card.style.left === '242px');

console.log('\n== ⑤ 前进 / 后退与 dock 标签联动 ==');
ctl.next();
check('第 2 步讲流水线（意图 vs 可核查状态）', titleText().includes('流水线') && stepText() === '第 2 步 · 共 5 步');
check('替用户点开了流水线标签', clicks.includes('tab-pipeline') && pane.pipeline.hidden === false);
check('洞对准流水线面板（视口内裁剪）',
  holeOf().style.left === '872px' && holeOf().style.top === '28px'
  && holeOf().style.width === '408px' && holeOf().style.height === '772px');
const arrowR = fireGlobal('ArrowRight');
check('→ 键前进到第 3 步「干预」且事件被吞掉',
  titleText().includes('干预') && arrowR.defaultPrevented && arrowR.stopped);
check('洞对准步骤详情卡（890,300,380×220 → 882,292,396×236）',
  holeOf().style.left === '882px' && holeOf().style.top === '292px'
  && holeOf().style.width === '396px' && holeOf().style.height === '236px');
fireGlobal('ArrowLeft');
check('← 键退回第 2 步', titleText().includes('流水线'));
ctl.next();
ctl.next();
check('第 4 步讲影像与审计，替用户点开影像标签',
  titleText().includes('影像与审计') && clicks.includes('tab-images'));
check('洞 = 影像+审计两个标签的联合外接矩形',
  holeOf().style.left === '920px' && holeOf().style.top === '0px'
  && holeOf().style.width === '144px' && holeOf().style.height === '40px');
ctl.next();
check('第 5 步讲环境与帮助，替用户点开环境标签',
  titleText().includes('环境') && clicks.includes('tab-env') && pane.env.hidden === false);
check('末步主按钮变「完成」且「上一步」可用', btnNext.textContent === '完成' && btnPrev.disabled === false);
ctl.next();
check('完成 → 浮层移除并落 localStorage（key 带版本号）',
  !Tour.isOpen() && overlayCount() === 0 && !!store.get(Tour.SEEN_KEY));
check('dock 标签归位到起播前选中的「影像」',
  clicks[clicks.length - 1] === 'tab-images' && pane.images.hidden === false);

console.log('\n== ⑥ Esc 关闭 / Tab 圈定 / 焦点归还 ==');
store.delete(Tour.SEEN_KEY);
btnTheme.focus();
Tour.startTour();
const items = cardOf().querySelectorAll('button');
const btnX = items.find((b) => b.classList.contains('tour-x'));
const nextBtn = cardOf().querySelectorAll('.tour-btn')[1];
nextBtn.focus();
const tabEvt = fireGlobal('Tab');
check('Tab 在卡片内圈定（末位环绕到 ✕）', tabEvt.defaultPrevented && DOC.activeElement === btnX);
const shiftTab = fireGlobal('Tab', { shiftKey: true });
check('Shift+Tab 反向环绕回「下一步」', shiftTab.defaultPrevented && DOC.activeElement === nextBtn);
const escEvt = fireGlobal('Escape');
check('Esc 关闭（跳过也算已看）且事件不外溢',
  !Tour.isOpen() && escEvt.defaultPrevented && escEvt.stopped && !!store.get(Tour.SEEN_KEY));
check('关闭后焦点归还触发前元素（#btnTheme）', DOC.activeElement === btnTheme);

console.log('\n== ⑦ 目标缺失：起播时整步跳过 + 候选选择器回退 ==');
store.delete(Tour.SEEN_KEY);
tabBtn.images.hidden = true;
tabBtn.audit.hidden = true;
pdetail.hidden = true;
Tour.startTour();
check('影像/审计标签不可见 → 该步被跳过（共 4 步）',
  stepText() === '第 1 步 · 共 4 步'
  && cardOf().querySelector('.tour-dots').children.length === 4);
Tour.next();
Tour.next();
check('步骤详情缺失 → 回退高亮整个流水线面板',
  titleText().includes('干预')
  && holeOf().style.left === '872px' && holeOf().style.width === '408px');
Tour.next();
check('第 4 步直接是「环境」（影像与审计已让位）', titleText().includes('环境') && stepText() === '第 4 步 · 共 4 步');
Tour.closeTour();
tabBtn.images.hidden = false;
tabBtn.audit.hidden = false;
pdetail.hidden = false;
selectTab('images');

console.log('\n== ⑧ 目标中途消失：导航时动态让位 ==');
store.delete(Tour.SEEN_KEY);
Tour.startTour();
check('全量起播仍是 5 步', stepText() === '第 1 步 · 共 5 步');
Tour.next();
Tour.next();
tabBtn.images.hidden = true;
tabBtn.audit.hidden = true;
Tour.next();
check('第 4 步中途失效 → 前进直接落到第 5 步', titleText().includes('环境') && stepText() === '第 5 步 · 共 5 步');
Tour.prev();
check('后退同样跳过失效步（回到干预）', titleText().includes('干预'));
Tour.closeTour();
tabBtn.images.hidden = false;
tabBtn.audit.hidden = false;
selectTab('images');

console.log('\n== ⑨ 「?」按钮随时重看（无视已看标记） ==');
check('已看标记在（上一节 Esc 落盘）', !!store.get(Tour.SEEN_KEY));
btnTour.click();
check('点「?」重看：引导打开', Tour.isOpen() && overlayCount() === 1);
Tour.initTour();
btnTour.click();
check('initTour 幂等：重复接线/重复点击不叠加浮层', overlayCount() === 1);
cardOf().querySelectorAll('button').find((b) => b.classList.contains('tour-skip')).click();
check('「跳过引导」关闭且已看标记保留', !Tour.isOpen() && !!store.get(Tour.SEEN_KEY));

console.log('\n== ⑩ 自动播放与环境向导协调（不抢时机） ==');
store.delete(Tour.SEEN_KEY);
const wizard = make('div', 'setupWizard', 'setup-scrim');
const auto = Tour.autoStart();
await sleep(80);
check('环境向导在场 → 引导按兵不动', !Tour.isOpen());
wizard.remove();
const started = await auto;
check('向导从 DOM 移除 → 自动开始且从第 1 步讲起',
  started === true && Tour.isOpen() && titleText().includes('聊天框'));
fireGlobal('Escape');
check('已看后 autoStart 直接返回 false', (await Tour.autoStart()) === false && !Tour.isOpen());

console.log('\n== ⑪ resize 重排：遮罩与洞跟随视口 ==');
store.delete(Tour.SEEN_KEY);
Tour.startTour();
window.innerWidth = 1000;
fireResize();
check('视口收窄 → 顶部遮罩宽度重算', masksOf()[0].style.width === '1000px');
check('洞随视口裁剪（右缘 1038 → 1000）', holeOf().style.width === '758px');
window.innerWidth = 1280;
fireResize();
check('视口恢复 → 几何还原', holeOf().style.width === '796px');
Tour.closeTour();

console.log('\n== ⑫ 静态接线（index.html / tour.css） ==');
const fs = await import('node:fs');
const html = fs.readFileSync(new URL('./index.html', import.meta.url), 'utf-8');
check('index.html 恰好一行引入 js/tour.js',
  (html.match(/js\/tour\.js/g) || []).length === 1
  && html.includes('<script type="module" src="js/tour.js"></script>'));
check('index.html 恰好一行引入 css/tour.css',
  (html.match(/css\/tour\.css/g) || []).length === 1);
check('index.html 恰好一个「?」重看按钮（挂在主题按钮旁）',
  (html.match(/id="btnTour"/g) || []).length === 1
  && html.indexOf('id="btnTheme"') < html.indexOf('id="btnTour"')
  && html.indexOf('id="btnTour"') < html.indexOf('id="railDock"'));
check('index.html 接线共 3 行（≤5 行预算）',
  html.split('\n').filter((ln) => /tour/i.test(ln)).length === 3);
const css = fs.readFileSync(new URL('./css/tour.css', import.meta.url), 'utf-8');
check('tour.css 有遮罩/洞/卡片样式',
  css.includes('.tour-mask') && css.includes('.tour-hole') && css.includes('.tour-card'));
check('tour.css 无宽度类 @media（断点清单纪律）', !/@media[^{]*width/.test(css));

console.log(failed ? `\n${failed} 项断言失败` : '\n全部断言通过');
process.exit(failed ? 1 : 0);
