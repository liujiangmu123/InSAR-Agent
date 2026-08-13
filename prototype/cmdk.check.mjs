/* ============================================================
   命令面板的无浏览器自查脚本（node prototype/cmdk.check.mjs）
   与 fail-demo.check.mjs 同一套纪律：最小 DOM/window stub 直接
   import js/cmdk.js，覆盖 —— 注册表完整性 / 子串搜索过滤 /
   键盘导航环（含禁用项跳过）/ 执行回调（DOM 触达）/ 禁用态 /
   焦点记忆与归还 / Tab 圈定 / aria-combobox 语义 / 全局快捷键开合 /
   a11y 静态清单登记。只依赖 node 内建能力，零 npm 依赖。
   ============================================================ */

/* ---------------- 最小 DOM stub（fail-demo 版 + 焦点/选择器扩展） ---------------- */
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
  matches(sel) {
    // 支持「tag#id.cls[attr]」的简单复合选择器（本脚本与 cmdk.js 的用量足够）
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

/* window stub：记录捕获监听器，供模拟全局按键 */
const winListeners = {};
globalThis.window = {
  addEventListener(type, fn) { (winListeners[type] ||= []).push(fn); },
  removeEventListener() {},
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
  for (const fn of winListeners.keydown || []) fn(e);
  return e;
}

let failed = 0;
function check(name, cond) {
  if (cond) { console.log(`  ok  ${name}`); }
  else { failed += 1; console.error(`  FAIL ${name}`); }
}

/* ---------------- 页面骨架（index.html 的既有入口元素） ---------------- */
const clicks = [];
function addBtn(id, parent = DOC.body) {
  const b = new Element('button');
  b.setAttribute('id', id);
  b.addEventListener('click', () => clicks.push(id));
  parent.appendChild(b);
  return b;
}

const homeBtn = addBtn('homeBtn');            // 充当「打开面板前的焦点元素」
const btnNew = addBtn('btnNew');
const btnTheme = addBtn('btnTheme');

const dock = new Element('aside');
dock.setAttribute('id', 'dock');
dock.hidden = true;                            // 初始收起：面板跳转应先打开 dock
DOC.body.appendChild(dock);
const railDock = addBtn('railDock');
railDock.addEventListener('click', () => { dock.hidden = !dock.hidden; });

const dockTabs = new Element('div');
dockTabs.setAttribute('id', 'dockTabs');
DOC.body.appendChild(dockTabs);
const PANE_IDS = ['pipeline', 'images', 'files', 'audit', 'report', 'web', 'trace', 'term', 'env'];
for (const id of PANE_IDS) addBtn(`tab-${id}`, dockTabs);

const btnRun = addBtn('btnRun');
btnRun.hidden = true;                          // 空闲态被 app.js 藏起 → 命令应显示禁用

const stream = new Element('div');
stream.setAttribute('id', 'stream');
DOC.body.appendChild(stream);
const hero = new Element('div'); hero.setAttribute('class', 'hero'); stream.appendChild(hero);
const chipBox = new Element('div'); chipBox.setAttribute('class', 'chips'); hero.appendChild(chipBox);
for (let i = 0; i < 3; i++) {
  const c = new Element('button'); c.setAttribute('class', 'chip');
  c.addEventListener('click', () => clicks.push(`chip${i}`));
  chipBox.appendChild(c);
}
const demoBtn = new Element('button'); demoBtn.setAttribute('class', 'hero-demo');
demoBtn.addEventListener('click', () => clicks.push('heroDemo'));
hero.appendChild(demoBtn);
// 注意：不创建 #pane-report .btn-pri（下载报告应显示禁用）

/* ---------------- 被测模块 ---------------- */
const CmdK = await import('./js/cmdk.js');

const type = (inputEl, text) => {
  inputEl.value = text;
  inputEl.dispatchEvent({ type: 'input', target: inputEl });
};

console.log('== ① 命令注册表 ==');
check('注册表共 18 条（9 面板 + 会话/主题/运行 + 4 示例 + 帮助 + 下载报告）', CmdK.COMMANDS.length === 18);
check('九个面板跳转命令', CmdK.COMMANDS.filter((c) => c.group === '面板').length === 9);
check('四个示例任务命令', CmdK.COMMANDS.filter((c) => c.group === '示例任务').length === 4);

console.log('\n== ② 搜索过滤（纯函数） ==');
const themeCmd = CmdK.COMMANDS.find((c) => c.id === 'theme-toggle');
check('子串命中「主题」', CmdK.matches(themeCmd, '主题'));
check('大小写不敏感「THEME」', CmdK.matches(themeCmd, 'THEME'));
check('多词各自匹配「主题 深色」', CmdK.matches(themeCmd, '主题 深色'));
check('未命中返回 false', !CmdK.matches(themeCmd, '流水线'));
check('空查询全量通过', CmdK.matches(themeCmd, ''));

console.log('\n== ③ Ctrl+Shift+K 开合 + 焦点记忆 + combobox 语义 ==');
homeBtn.focus();
const openEvt = fireGlobal('K', { ctrlKey: true, shiftKey: true });
const overlay = DOC.getElementById('cmdk');
const input = overlay?.querySelector('.cmdk-input');
const list = overlay?.querySelector('.cmdk-list');
check('Ctrl+Shift+K 打开面板', CmdK.isOpen() && overlay && overlay.hidden === false);
check('全局快捷键被吞掉（preventDefault + stopPropagation）', openEvt.defaultPrevented && openEvt.stopped);
check('打开时焦点移入搜索框', DOC.activeElement === input);
check('combobox 语义（role/aria-controls/listbox）',
  input?.getAttribute('role') === 'combobox'
  && input?.getAttribute('aria-controls') === 'cmdk-list'
  && list?.getAttribute('role') === 'listbox');
check('全量渲染 18 个 option', overlay.querySelectorAll('.cmdk-item').length === 18);
check('默认高亮第一个可用命令（aria-activedescendant）',
  input.getAttribute('aria-activedescendant') === 'cmdk-opt-panel-pipeline');
const plainK = fireGlobal('k', { ctrlKey: true });
check('Ctrl+K（无 Shift）不被面板占用', CmdK.isOpen() && !plainK.defaultPrevented);

console.log('\n== ④ 搜索过滤（DOM） ==');
type(input, '主题');
let items = overlay.querySelectorAll('.cmdk-item');
check('「主题」过滤到唯一命中', items.length === 1 && items[0].attrs.id === 'cmdk-opt-theme-toggle');
type(input, 'zzz 不存在的命令');
check('无匹配 → 空态提示可见、列表为空',
  overlay.querySelectorAll('.cmdk-item').length === 0
  && overlay.querySelector('.cmdk-empty').hidden === false);
type(input, '');
check('清空查询恢复全量', overlay.querySelectorAll('.cmdk-item').length === 18);

console.log('\n== ⑤ 键盘导航环 ==');
type(input, '面板');
check('「面板」过滤出 9 条', overlay.querySelectorAll('.cmdk-item').length === 9);
overlay.dispatchEvent(keyEvt('ArrowUp'));
check('首项 ↑ 环绕到末项', input.getAttribute('aria-activedescendant') === 'cmdk-opt-panel-env');
overlay.dispatchEvent(keyEvt('ArrowDown'));
check('末项 ↓ 环绕回首项', input.getAttribute('aria-activedescendant') === 'cmdk-opt-panel-pipeline');
overlay.dispatchEvent(keyEvt('ArrowDown'));
check('↓ 移到第二项', input.getAttribute('aria-activedescendant') === 'cmdk-opt-panel-images');
overlay.dispatchEvent(keyEvt('End'));
check('End 跳到最后可用项', input.getAttribute('aria-activedescendant') === 'cmdk-opt-panel-env');
overlay.dispatchEvent(keyEvt('Home'));
check('Home 跳回首个可用项', input.getAttribute('aria-activedescendant') === 'cmdk-opt-panel-pipeline');

console.log('\n== ⑥ 禁用态（目标元素不存在 / 被隐藏） ==');
type(input, '运行流水线');
items = overlay.querySelectorAll('.cmdk-item');
check('#btnRun 隐藏 → 命令禁用显示（aria-disabled + 徽标）',
  items.length === 1 && items[0].getAttribute('aria-disabled') === 'true'
  && items[0].querySelector('.why')?.textContent === '不可用');
check('无可用项时不设置 aria-activedescendant', input.getAttribute('aria-activedescendant') === null);
overlay.dispatchEvent(keyEvt('Enter'));
check('Enter 对禁用项不执行、面板保持打开', CmdK.isOpen() && !clicks.includes('btnRun'));
type(input, '报告');
items = overlay.querySelectorAll('.cmdk-item');
check('「报告」混合结果：面板跳转可用 + 下载报告禁用（#pane-report 未渲染）',
  items.length === 2
  && items[0].attrs.id === 'cmdk-opt-panel-report' && items[0].getAttribute('aria-disabled') === null
  && items[1].attrs.id === 'cmdk-opt-report-download' && items[1].getAttribute('aria-disabled') === 'true');
overlay.dispatchEvent(keyEvt('ArrowDown'));
check('导航跳过禁用项（环绕回唯一可用项）',
  input.getAttribute('aria-activedescendant') === 'cmdk-opt-panel-report');

console.log('\n== ⑦ 执行回调（DOM 触达）+ dock 联动 ==');
overlay.dispatchEvent(keyEvt('Enter'));
check('Enter 执行面板跳转：先开 dock（#railDock）再点 #tab-report',
  clicks.includes('railDock') && clicks.includes('tab-report')
  && clicks.indexOf('railDock') < clicks.indexOf('tab-report'));
check('dock 已由收起变为展开', dock.hidden === false);
check('执行后面板关闭', !CmdK.isOpen());
check('焦点归还到打开前元素', DOC.activeElement === homeBtn);

console.log('\n== ⑧ Esc 关闭 + 焦点归还 + 事件不外溢 ==');
btnTheme.focus();
CmdK.open();
check('open() 再次打开并聚焦搜索框', CmdK.isOpen() && DOC.activeElement === input);
const escEvt = keyEvt('Escape');
overlay.dispatchEvent(escEvt);
check('Esc 关闭面板且阻断冒泡（不触达全局 Esc=停止）',
  !CmdK.isOpen() && escEvt.defaultPrevented && escEvt.stopped);
check('Esc 后焦点归还 #btnTheme', DOC.activeElement === btnTheme);

console.log('\n== ⑨ Tab 焦点圈定 ==');
CmdK.open();
const tabEvt = keyEvt('Tab');
overlay.dispatchEvent(tabEvt);
check('Tab 被拦截且焦点仍在浮层内', tabEvt.defaultPrevented && DOC.activeElement === input);
const shiftTab = keyEvt('Tab', { shiftKey: true });
overlay.dispatchEvent(shiftTab);
check('Shift+Tab 同样圈定', shiftTab.defaultPrevented && DOC.activeElement === input);

console.log('\n== ⑩ 主题 / 示例任务的执行回调 ==');
type(input, '主题');
overlay.dispatchEvent(keyEvt('Enter'));
check('Enter 触发 #btnTheme click 且面板关闭', clicks.includes('btnTheme') && !CmdK.isOpen());
CmdK.open();
type(input, '示例');
check('「示例」过滤出 4 条且全部可用',
  overlay.querySelectorAll('.cmdk-item').length === 4
  && overlay.querySelectorAll('.cmdk-item').every((n) => n.getAttribute('aria-disabled') === null));
overlay.dispatchEvent(keyEvt('Enter'));
check('执行示例任务 → hero 第一个 chip 被点击', clicks.includes('chip0'));

console.log('\n== ⑪ 遮罩点击 + 快捷键再按关闭 ==');
fireGlobal('K', { ctrlKey: true, shiftKey: true });
check('Ctrl+Shift+K 再按 → 打开', CmdK.isOpen());
fireGlobal('K', { ctrlKey: true, shiftKey: true });
check('Ctrl+Shift+K 三按 → 关闭（toggle）', !CmdK.isOpen());
CmdK.open();
overlay.querySelector('.cmdk-mask').click();
check('点击遮罩关闭', !CmdK.isOpen());

console.log('\n== ⑫ 快捷键帮助命令 + a11y 静态清单登记 ==');
CmdK.open();
type(input, '快捷键');
overlay.dispatchEvent(keyEvt('Enter'));
await new Promise((r) => setTimeout(r, 30));   // 等动态 import('./a11y.js') 落定
const kbdHelp = DOC.getElementById('kbdHelp');
check('帮助命令经 a11y.openHelp 打开帮助浮层', !!kbdHelp && kbdHelp.hidden === false);
const A11y = await import('./js/a11y.js');
check('a11y 静态清单已登记 Ctrl+Shift+K',
  A11y.STATIC_SHORTCUTS.some((s) => s.keys.join('+') === 'Ctrl+Shift+K'));
const merged = A11y.collectShortcuts();
check('帮助浮层数据源含 Ctrl+Shift+K（静态清单并入）',
  merged.some((s) => s.keys.join('+') === 'Ctrl+Shift+K' && s.label === '打开 / 关闭命令面板'));
check('数据源仍含既有 ? 帮助项', merged.some((s) => s.keys.join('+') === '?'));

console.log('\n== ⑬ 静态检查（css / index.html 接线） ==');
const fs = await import('node:fs');
const css = fs.readFileSync(new URL('./css/cmdk.css', import.meta.url), 'utf-8');
check('cmdk.css 有浮层与禁用态样式',
  css.includes('.cmdk {') && css.includes(".cmdk-item[aria-disabled='true']"));
const html = fs.readFileSync(new URL('./index.html', import.meta.url), 'utf-8');
check('index.html 恰好一行引入 cmdk.js',
  (html.match(/cmdk\.js/g) || []).length === 1
  && html.includes('<script type="module" src="js/cmdk.js"></script>'));

console.log(failed ? `\n${failed} 项断言失败` : '\n全部断言通过');
process.exit(failed ? 1 : 0);
