/* ============================================================
   一键操作中心的无浏览器自查脚本（node prototype/quickactions.check.mjs）
   与 cmdk.check.mjs 同一套纪律：最小 DOM/window stub 直接
   import js/quickactions.js，覆盖 ——
   ① 注册表完整性（每动作 id/label/desc/ic/run/enabled 齐备且 id 唯一，
      window.QuickActions 对 cmdk 开放）
   ② 注入幂等（rail 按钮恰在 #railDock 之前、重复初始化不翻倍、CSS 单例）
   ③ 可用性动态判断（隐藏/禁用/缺失 → 原因字符串；恢复 → true）
   ④ 面板渲染（10 张卡、禁用卡带 aria-disabled + 原因 tooltip + 徽标）
   ⑤ 网格方向键导航（gridMove 纯函数 + DOM 焦点移动 + Esc 关闭归还焦点）
   ⑥ 执行与最近使用（DOM 触达、localStorage FIFO 容量 4、最近行渲染）
   ⑦ 静态接线（css 关键选择器、index.html 恰好一行引入）
   只依赖 node 内建能力，零 npm 依赖。
   ============================================================ */

/* ---------------- 最小 DOM stub（cmdk 版 + 带值属性选择器 / insertBefore） ---------------- */
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
    n.parentNode = this;
    this.childNodes.splice(i < 0 ? this.childNodes.length : i, 0, n);
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
  set textContent(v) { this.replaceChildren(new TextNode(v)); }
  get textContent() { return super.textContent; }
  get children() { return this.childNodes.filter((c) => c.nodeType === 1); }
  contains(n) { for (let x = n; x; x = x.parentNode) if (x === this) return true; return false; }
  matches(sel) {
    // 「tag#id.cls[attr]」+ 带值属性（= / ^= / *=，双引号）——本脚本与被测模块的用量足够
    const m = /^([a-zA-Z0-9-]*)(?:#([\w-]+))?((?:\.[\w-]+)*)((?:\[[^\]]*\])*)$/.exec(String(sel).trim());
    if (!m) return false;
    if (m[1] && this.tagName !== m[1].toLowerCase()) return false;
    if (m[2] && this.attrs.id !== m[2]) return false;
    if (!(m[3].match(/\.[\w-]+/g) || []).every((c) => this._cls.has(c.slice(1)))) return false;
    for (const raw of (m[4].match(/\[[^\]]*\]/g) || [])) {
      const am = /^\[([\w-]+)(?:(\^|\*)?=(?:"([^"]*)"|'([^']*)'))?\]$/.exec(raw);
      if (!am) return false;
      const val = this.attrs[am[1]];
      if (am[3] === undefined && am[4] === undefined) { // 仅存在性 [attr]
        if (val === undefined) return false;
        continue;
      }
      const want = am[3] ?? am[4];
      if (val === undefined) return false;
      if (am[2] === '^' && !val.startsWith(want)) return false;
      if (am[2] === '*' && !val.includes(want)) return false;
      if (!am[2] && val !== want) return false;
    }
    return true;
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
globalThis.MutationObserver = class { observe() {} disconnect() {} };
globalThis.requestAnimationFrame = (fn) => { fn(); return 0; };

/* localStorage stub：真实存取（最近使用的 FIFO 断言需要它） */
const store = new Map();
globalThis.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k),
};

globalThis.window = {
  addEventListener() {},
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

let failed = 0;
function check(name, cond) {
  if (cond) { console.log(`  ok  ${name}`); }
  else { failed += 1; console.error(`  FAIL ${name}`); }
}

const tick = () => new Promise((r) => setTimeout(r, 0));   // 等 queueMicrotask 落定

/* ---------------- 页面骨架（index.html 的既有入口元素） ---------------- */
const clicks = [];
function addBtn(id, parent = DOC.body, cls = '') {
  const b = new Element('button');
  b.setAttribute('id', id);
  if (cls) b.setAttribute('class', cls);
  b.addEventListener('click', () => clicks.push(id));
  parent.appendChild(b);
  return b;
}

/* rail：与 index.html 同序（…btnTheme、btnTour、railDock），⚡ 应插在 railDock 前 */
const rail = new Element('nav');
rail.setAttribute('class', 'rail');
DOC.body.appendChild(rail);
addBtn('railChat', rail, 'rail-btn');
addBtn('railSessions', rail, 'rail-btn');
const btnTheme = addBtn('btnTheme', rail, 'rail-btn');
addBtn('btnTour', rail, 'rail-btn');
const railDock = addBtn('railDock', rail, 'rail-btn');

const btnNew = addBtn('btnNew');
const btnRun = addBtn('btnRun');
btnRun.hidden = true;                          // 空闲态被 app.js 藏起 → 动作应禁用

const dock = new Element('aside');
dock.setAttribute('id', 'dock');
dock.hidden = true;                            // 初始收起：pane 动作应先开 dock
DOC.body.appendChild(dock);
railDock.addEventListener('click', () => { dock.hidden = !dock.hidden; });

const dockTabs = new Element('div');
dockTabs.setAttribute('id', 'dockTabs');
DOC.body.appendChild(dockTabs);
for (const id of ['env', 'report', 'files']) addBtn(`tab-${id}`, dockTabs);

/* pane-env：一键体检按钮 + 诊断包区块（diagexport.js 的私有容器） */
const paneEnv = new Element('section');
paneEnv.setAttribute('id', 'pane-env');
dock.appendChild(paneEnv);
const doctorBtn = new Element('button');
doctorBtn.setAttribute('class', 'btn doctor-run');
doctorBtn.addEventListener('click', () => clicks.push('doctorRun'));
paneEnv.appendChild(doctorBtn);
const diagBox = new Element('div');
diagBox.setAttribute('id', 'diagExport');
paneEnv.appendChild(diagBox);
const diagBtn = new Element('button');
diagBtn.addEventListener('click', () => clicks.push('diagExport'));
diagBox.appendChild(diagBtn);

/* pane-report：方法草稿区块（reportdraft.js）；复现包锚点初始不在场 */
const paneReport = new Element('section');
paneReport.setAttribute('id', 'pane-report');
dock.appendChild(paneReport);
const draftSec = new Element('section');
draftSec.setAttribute('class', 'reportdraft');
paneReport.appendChild(draftSec);
const draftBtn = new Element('button');
draftBtn.setAttribute('class', 'btn btn-pri btn-sm');
draftBtn.addEventListener('click', () => clicks.push('reportDraft'));
draftSec.appendChild(draftBtn);

/* pane-files：数据集区（datasets.js），重扫/添加两键并存考验 ^= 过滤 */
const paneFiles = new Element('section');
paneFiles.setAttribute('id', 'pane-files');
dock.appendChild(paneFiles);
const dsSection = new Element('div');
dsSection.setAttribute('id', 'dsSection');
paneFiles.appendChild(dsSection);
const rescanBtn = new Element('button');
rescanBtn.setAttribute('class', 'btn btn-gho btn-sm');
rescanBtn.setAttribute('aria-label', '重新扫描全部数据根目录(穿透 60 秒缓存)');
rescanBtn.addEventListener('click', () => clicks.push('dsRescan'));
dsSection.appendChild(rescanBtn);
const addDirBtn = new Element('button');
addDirBtn.setAttribute('class', 'btn btn-gho btn-sm');
addDirBtn.setAttribute('aria-label', '添加数据目录');
addDirBtn.addEventListener('click', () => clicks.push('dsAddDir'));
dsSection.appendChild(addDirBtn);

addBtn('btnLlmSettings');                      // llmsettings.js 注入的顶栏入口

/* ---------------- 被测模块 ---------------- */
const QA = await import('./js/quickactions.js');
const act = (id) => QA.ACTIONS.find((a) => a.id === id);

console.log('== ① 注册表完整性 ==');
check('注册表共 10 个动作', QA.ACTIONS.length === 10);
check('每个动作 id/label/desc/ic 齐备且非空',
  QA.ACTIONS.every((a) => [a.id, a.label, a.desc, a.ic].every((s) => typeof s === 'string' && s.length > 0)));
check('每个动作 run/enabled 均为函数',
  QA.ACTIONS.every((a) => typeof a.run === 'function' && typeof a.enabled === 'function'));
check('动作 id 全局唯一', new Set(QA.ACTIONS.map((a) => a.id)).size === QA.ACTIONS.length);
check('window.QuickActions.registry 指向同一注册表（cmdk 可消费）',
  globalThis.window.QuickActions && globalThis.window.QuickActions.registry === QA.ACTIONS);
check('window.QuickActions 暴露 open/close/toggle/execute',
  ['open', 'close', 'toggle', 'execute'].every((k) => typeof globalThis.window.QuickActions[k] === 'function'));

console.log('\n== ② 注入幂等 ==');
const qaBtn = DOC.getElementById('btnQuickActions');
check('rail 入口按钮已注入（.rail-btn + dialog 语义）',
  !!qaBtn && qaBtn.classList.contains('rail-btn')
  && qaBtn.getAttribute('aria-haspopup') === 'dialog' && qaBtn.getAttribute('aria-expanded') === 'false');
check('按钮恰在 #railDock 之前',
  rail.children.indexOf(qaBtn) === rail.children.indexOf(railDock) - 1);
QA.initQuickActions();
QA.installEntry();
check('重复初始化不产生第二个按钮',
  DOC.body.querySelectorAll('#btnQuickActions').length === 1);
check('样式 link 只注入一次（id 防护）',
  DOC.head.children.filter((n) => n.attrs.id === 'quickactionsCss').length === 1);

console.log('\n== ③ 可用性动态判断 ==');
check('#btnRun 隐藏 → 运行流水线给原因字符串', typeof act('pipeline-run').enabled() === 'string');
btnRun.hidden = false;
check('#btnRun 露出 → 运行流水线可用', act('pipeline-run').enabled() === true);
btnRun.hidden = true;
check('复现包锚点不在场 → 给原因（需先完成 run）',
  String(act('repro-bundle').enabled()).includes('run'));
const reproA = new Element('a');
reproA.setAttribute('href', '/api/repro-bundle?session=s-demo');
reproA.setAttribute('download', '');
reproA.addEventListener('click', () => clicks.push('reproAnchor'));
paneReport.appendChild(reproA);
check('锚点出现（服务端草稿渲染）→ 复现包可用', act('repro-bundle').enabled() === true);
check('诊断包按钮在场 → 可用', act('diag-export').enabled() === true);
diagBtn.disabled = true;
check('诊断包按钮打包中（disabled）→ 给原因', typeof act('diag-export').enabled() === 'string');
diagBtn.disabled = false;
doctorBtn.disabled = true;
check('体检进行中 → 给原因', typeof act('doctor-run').enabled() === 'string');
doctorBtn.disabled = false;
rescanBtn.remove();
check('重扫按钮缺席 →「添加目录」不被误认（aria-label 前缀过滤）',
  typeof act('datasets-rescan').enabled() === 'string');
dsSection.insertBefore(rescanBtn, addDirBtn);
check('重扫按钮在场 → 可用', act('datasets-rescan').enabled() === true);
check('会话/体检/草稿/模型/引导/主题全部可用',
  ['session-new', 'doctor-run', 'report-draft', 'llm-settings', 'tour-open', 'theme-toggle']
    .every((id) => act(id).enabled() === true));

console.log('\n== ④ 面板渲染 ==');
qaBtn.focus();
qaBtn.click();                                 // 鼠标流入口：点击 rail ⚡
const overlay = DOC.getElementById('quickActions');
const panel = overlay?.querySelector('.qa-panel');
check('点击 rail 按钮打开面板（aria-expanded 同步）',
  QA.isOpen() && overlay.hidden === false && qaBtn.getAttribute('aria-expanded') === 'true');
check('面板 dialog 语义', panel?.getAttribute('role') === 'dialog' && panel?.getAttribute('aria-modal') === 'true');
check('渲染全部 10 张动作卡', overlay.querySelectorAll('.qa-card').length === 10);
check('尚无最近使用 → 不渲染最近行', overlay.querySelectorAll('.qa-chip').length === 0);
const runCard = overlay.querySelector('.qa-card[data-id="pipeline-run"]');
check('禁用卡带 aria-disabled + 原因 tooltip + 「不可用」徽标',
  runCard.getAttribute('aria-disabled') === 'true'
  && String(runCard.getAttribute('title')).startsWith('不可用：')
  && runCard.querySelector('.why')?.textContent === '不可用');
const newCard = overlay.querySelector('.qa-card[data-id="session-new"]');
check('可用卡 tooltip = 一句话说明', newCard.getAttribute('title') === act('session-new').desc);
check('打开时焦点落在首个可用卡（roving tabindex）',
  DOC.activeElement === newCard && newCard.getAttribute('tabindex') === '0');

console.log('\n== ⑤ 网格方向键导航 ==');
const mv = (rows, pos, key) => QA.gridMove(rows, pos, key);
check('gridMove:→ 行内推进', JSON.stringify(mv([2, 2], { row: 0, col: 0 }, 'ArrowRight')) === '{"row":0,"col":1}');
check('gridMove:→ 行尾流入下一行', JSON.stringify(mv([3, 2], { row: 0, col: 2 }, 'ArrowRight')) === '{"row":1,"col":0}');
check('gridMove:← 首格环绕到末行末尾', JSON.stringify(mv([3, 2], { row: 0, col: 0 }, 'ArrowLeft')) === '{"row":1,"col":1}');
check('gridMove:↓ 跨行保列并钳到行尾', JSON.stringify(mv([3, 2], { row: 0, col: 2 }, 'ArrowDown')) === '{"row":1,"col":1}');
check('gridMove:↑ 从首行环绕到末行', JSON.stringify(mv([2, 2, 2], { row: 0, col: 1 }, 'ArrowUp')) === '{"row":2,"col":1}');
check('gridMove:Home/End 跳全局首尾',
  JSON.stringify(mv([3, 2], { row: 1, col: 1 }, 'Home')) === '{"row":0,"col":0}'
  && JSON.stringify(mv([3, 2], { row: 0, col: 0 }, 'End')) === '{"row":1,"col":1}');
overlay.dispatchEvent(keyEvt('ArrowRight'));
check('DOM:→ 移到第二张卡', DOC.activeElement?.getAttribute('data-id') === 'pipeline-run');
overlay.dispatchEvent(keyEvt('ArrowDown'));
check('DOM:↓ 移到下一行同列', DOC.activeElement?.getAttribute('data-id') === 'diag-export');
overlay.dispatchEvent(keyEvt('End'));
check('DOM:End 跳到最后一张卡', DOC.activeElement?.getAttribute('data-id') === 'theme-toggle');
overlay.dispatchEvent(keyEvt('ArrowRight'));
check('DOM:末卡 → 环绕回第一张', DOC.activeElement?.getAttribute('data-id') === 'session-new');
const tabEvt = keyEvt('Tab');
overlay.dispatchEvent(tabEvt);
check('Tab 被圈定在面板内（焦点移向下一项）',
  tabEvt.defaultPrevented && DOC.activeElement?.getAttribute('data-id') === 'pipeline-run');
const escEvt = keyEvt('Escape');
overlay.dispatchEvent(escEvt);
check('Esc 关闭且阻断冒泡（不触达全局 Esc=停止）',
  !QA.isOpen() && escEvt.defaultPrevented && escEvt.stopped);
check('Esc 后焦点归还 rail ⚡ 按钮、aria-expanded 复位',
  DOC.activeElement === qaBtn && qaBtn.getAttribute('aria-expanded') === 'false');

console.log('\n== ⑥ 执行与最近使用 ==');
check('pushRecent:置顶去重', JSON.stringify(QA.pushRecent(['a', 'b'], 'b')) === '["b","a"]');
check('pushRecent:FIFO 容量 4（最旧出队）',
  JSON.stringify(QA.pushRecent(['a', 'b', 'c', 'd'], 'e')) === '["e","a","b","c"]');
check('readRecent:坏数据回落空数组',
  (store.set('ia-qa-recent', '{oops'), QA.readRecent().length === 0));
store.delete('ia-qa-recent');
QA.open();
overlay.querySelector('.qa-card[data-id="theme-toggle"]').click();
check('点卡片 → 触发 #btnTheme 既有入口且面板关闭',
  clicks.includes('btnTheme') && !QA.isOpen());
check('最近使用落 localStorage（ia-qa-recent）',
  store.get('ia-qa-recent') === '["theme-toggle"]');
QA.open();
overlay.querySelector('.qa-card[data-id="pipeline-run"]').click();
check('点禁用卡：不执行、面板保持打开、最近不记录',
  QA.isOpen() && !clicks.includes('btnRun') && store.get('ia-qa-recent') === '["theme-toggle"]');
overlay.querySelector('.qa-card[data-id="doctor-run"]').click();
await tick();                                  // 等 paneAction 的 queueMicrotask 落定
check('体检动作：先开 dock（#railDock）再点 #tab-env 最后点体检按钮',
  clicks.indexOf('railDock') >= 0
  && clicks.indexOf('railDock') < clicks.indexOf('tab-env')
  && clicks.indexOf('tab-env') < clicks.indexOf('doctorRun'));
check('dock 已由收起变为展开', dock.hidden === false);
check('最近使用 FIFO：新动作置顶', store.get('ia-qa-recent') === '["doctor-run","theme-toggle"]');
QA.execute(act('repro-bundle'));
QA.execute(act('datasets-rescan'));
await tick();
QA.execute(act('session-new'));
check('复现包直点下载锚点（不跳面板）', clicks.includes('reproAnchor'));
check('重扫走文件面板既有按钮', clicks.includes('tab-files') && clicks.includes('dsRescan'));
check('新建会话触发 #btnNew', clicks.includes('btnNew'));
check('最近使用只留最近 4 个（doctor-run 之前的 theme-toggle 出队）',
  store.get('ia-qa-recent') === '["session-new","datasets-rescan","repro-bundle","doctor-run"]');
QA.open();
const chips = overlay.querySelectorAll('.qa-chip');
check('重开面板 →「最近」行按最近序渲染 4 枚 chip',
  chips.length === 4 && chips.map((c) => c.getAttribute('data-id')).join()
    === 'session-new,datasets-rescan,repro-bundle,doctor-run');
check('chip 行占第 0 行、网格从第 1 行起（方向键可跨区导航）',
  chips.every((c) => c.getAttribute('data-row') === '0')
  && overlay.querySelector('.qa-card[data-id="session-new"]').getAttribute('data-row') === '1');
overlay.dispatchEvent(keyEvt('ArrowDown'));
check('DOM:从 chip 行 ↓ 进入网格首行', DOC.activeElement?.getAttribute('data-id') === 'session-new'
  && DOC.activeElement?.classList.contains('qa-card'));
QA.close();

console.log('\n== ⑦ 静态检查（css / index.html 接线） ==');
const fs = await import('node:fs');
const css = fs.readFileSync(new URL('./css/quickactions.css', import.meta.url), 'utf-8');
check('quickactions.css 含面板与禁用态样式',
  css.includes('.qa-panel {') && css.includes(".qa-card[aria-disabled='true']"));
const html = fs.readFileSync(new URL('./index.html', import.meta.url), 'utf-8');
check('index.html 恰好一行引入 quickactions.js',
  (html.match(/quickactions\.js/g) || []).length === 1
  && html.includes('<script type="module" src="js/quickactions.js"></script>'));

console.log(failed ? `\n${failed} 项断言失败` : '\n全部断言通过');
process.exit(failed ? 1 : 0);
