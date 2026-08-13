/* ============================================================
   通知中心（notify.js 第三节）的无浏览器自查脚本
   （node prototype/notify.check.mjs）
   用最小 DOM stub 直接 import notify.js，断言四组行为：
     A. 收集规则：哪些 SSE 事件产生通知（result / step.end exit≠0 /
        gate_stop / note[bad] / note[warn]中断词），哪些是噪音
        （step.stage / tool.* / thinking / overall / 普通 note …）；
     B. 聚合逻辑：同会话 60s 窗口内同家族合并（计数/精化/置未读），
        跨家族、跨会话、超窗不合并；原生通知按条目防抖只发一次；
     C. localStorage 持久：上限 200 条 FIFO 淘汰最旧，重建可回放；
     D. 未读计数状态机：新增/更新→未读，已读→更新回未读，
        全部已读、单条已读、清空；
   外加抽屉 DOM/aria、开关权限被拒禁用、runEnded/approvalNeeded
   改道等界面侧冒烟。只依赖 node 内建能力，零 npm 依赖
   （DOM stub 与 agent-ux.check.mjs 同源）。
   ============================================================ */

/* ---------------- 最小 DOM stub（与 agent-ux.check.mjs 同源） ---------------- */
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
    this.value = ''; this.open = false; this.hidden = false;
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

/* 铃铛按钮先于模块导入放进 DOM（自初始化要找 #btnNotify） */
const bell = new Element('button');
bell.setAttribute('id', 'btnNotify');
bell.setAttribute('aria-label', '通知中心');
DOC.body.appendChild(bell);

/* ---------------- 断言工具 ---------------- */
let failed = 0;
function check(name, cond) {
  if (cond) { console.log(`  ok  ${name}`); }
  else { failed += 1; console.error(`  FAIL ${name}`); }
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** Map 版 localStorage：验证持久化 round-trip 用。 */
function memStorage() {
  const m = new Map();
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => { m.set(k, String(v)); },
    removeItem: (k) => { m.delete(k); },
  };
}

/* ---------------- 挂载 ---------------- */
const Notify = await import('./js/notify.js');
const {
  classifyEvent, phaseVerdict, createNotifyStore, createNotifySettings,
  relativeTime, dayLabel, createNotifyCenter, initNotifyCenter,
} = Notify;

/* ============================================================
   A. 收集规则：哪些事件产生通知
   ============================================================ */
console.log('== A. 收集规则（SSE 事件 → 通知判定） ==');

check('A1 result → run_done（run 终态·完成）',
  classifyEvent({ t: 'result' })?.kind === 'run_done');
check('A2 step.end exit=0 不通知；exit≠0 → run_failed 且标题带步号',
  classifyEvent({ t: 'step.end', stepId: 6, exit: 0 }) === null
  && classifyEvent({ t: 'step.end', stepId: 6, exit: 7 })?.kind === 'run_failed'
  && classifyEvent({ t: 'step.end', stepId: 6, exit: 7 }).title === '第 6 步失败');
check('A3 gate_stop → run_failed（质量门拦停,带/不带 stepId 都收）',
  classifyEvent({ t: 'gate_stop', text: '解缠覆盖率 62% < 70%', stepId: 6 })?.kind === 'run_failed'
  && classifyEvent({ t: 'gate_stop', text: 'x' })?.kind === 'run_failed');
check('A4 note[bad] → run_failed（失败分诊上下文）',
  classifyEvent({ t: 'note', tone: 'bad', text: '第 6 步失败 · numerical' })?.kind === 'run_failed');
check('A5 note[warn] 含取消/中断/环境停止 → run_interrupted',
  classifyEvent({ t: 'note', tone: 'warn', text: '第 6 步已取消 · 可续跑(已完成阶段保留)' })?.kind === 'run_interrupted'
  && classifyEvent({ t: 'note', tone: 'warn', text: '第 6 步:执行环境停止(非计算失败)。' })?.kind === 'run_interrupted');
check('A6 普通 note 不通知（warn 非中断词 / info / ok）',
  classifyEvent({ t: 'note', tone: 'warn', text: 'run 已完成,但第 8 步因干预待重跑' }) === null
  && classifyEvent({ t: 'note', tone: 'info', text: 'x' }) === null
  && classifyEvent({ t: 'note', tone: 'ok', text: 'provenance 已导出' }) === null);
check('A7 step.stage 等执行噪音一律不通知',
  ['step.stage', 'step.start', 'tool.start', 'tool.log', 'tool.progress', 'tool.end',
    'thinking', 'say', 'plan', 'candidates', 'overall', 'budget',
    'reattach', 'intervention', 'ask', 'report', 'handler_error']
    .every((t) => classifyEvent({ t, stepId: 3, stage: 'PREPARED', exit: 1, tone: 'bad' }) === null));
check('A8 非法载荷安全返回 null', classifyEvent(null) === null && classifyEvent('x') === null);
check('A9 phaseVerdict：done/paused/其他 → 完成/失败/中断（mock 模式兜底口径）',
  phaseVerdict('done').kind === 'run_done'
  && phaseVerdict('paused').kind === 'run_failed'
  && phaseVerdict('running').kind === 'run_interrupted');

/* ============================================================
   B. 聚合逻辑：60s 窗口 / 家族 / 会话隔离
   ============================================================ */
console.log('\n== B. 聚合逻辑（60s 窗口） ==');

const T0 = Date.parse('2026-08-13T10:00:00');
const stB = createNotifyStore({ storage: memStorage() });
const vFail = (body, at) => stB.ingest({
  kind: 'run_failed', title: '第 6 步失败', body, sessionId: 'a', sessionName: 'A', at,
});

const b1 = vFail('运行已停链，等待失败处置', T0);
const b2 = vFail('第 6 步失败 · numerical(分诊来源:规则)', T0 + 30e3);
check('B1 同会话 30s 内同家族合并为一条（count=2,正文取最新）',
  b1.isNew === true && b2.isNew === false && b2.entry === b1.entry
  && b2.entry.count === 2 && stB.list().length === 1
  && b2.entry.body.includes('numerical'));
const b3 = stB.ingest({
  kind: 'run_interrupted', title: '运行已中断', body: '第 6 步已取消 · 可续跑',
  sessionId: 'a', sessionName: 'A', at: T0 + 40e3,
});
check('B2 失败家族内精化：中断事件把条目升级为 run_interrupted（标题跟随）',
  b3.isNew === false && b3.entry.kind === 'run_interrupted'
  && b3.entry.title === '运行已中断' && b3.entry.count === 3);
const b4 = stB.ingest({
  kind: 'run_done', title: '运行完成', body: 'x', sessionId: 'a', sessionName: 'A', at: T0 + 50e3,
});
check('B3 跨家族不合并：同会话 10s 后的 run_done 另起一条（防止失败与完成混为一谈）',
  b4.isNew === true && stB.list().length === 2);
const b5 = stB.ingest({
  kind: 'run_done', title: '运行完成', body: 'y', sessionId: 'a', sessionName: 'A', at: T0 + 50e3 + 61e3,
});
check('B4 超过 60s 窗口不合并：同家族也另起一条',
  b5.isNew === true && stB.list().length === 3);
const b6 = stB.ingest({
  kind: 'run_done', title: '运行完成', body: 'z', sessionId: 'b', sessionName: 'B', at: T0 + 50e3 + 62e3,
});
check('B5 跨会话不合并：b 会话独立成条',
  b6.isNew === true && stB.list().length === 4);
const b7 = stB.ingest({
  kind: 'run_done', title: '运行完成', body: 'w', sessionId: 'a', sessionName: 'A', at: T0 + 50e3 + 90e3,
});
check('B6 聚合后条目移到最新位（列表尾＝最近更新）',
  b7.isNew === false && b7.entry === b5.entry
  && stB.list()[stB.list().length - 1] === b7.entry);
check('B7 证据级达标标注：result 时刻注入 evidence 字段',
  stB.ingest({
    kind: 'run_done', title: '运行完成', body: '', evidence: 'audited',
    sessionId: 'c', sessionName: 'C', at: T0,
  }).entry.evidence === 'audited');

/* ============================================================
   C. localStorage 持久：200 条 FIFO + 重建回放
   ============================================================ */
console.log('\n== C. localStorage 上限 FIFO ==');

const storC = memStorage();
const stC = createNotifyStore({ storage: storC });
for (let i = 0; i < 205; i++) {
  stC.ingest({
    kind: 'run_done', title: `完成 ${i}`, body: '',
    sessionId: `s-${i}`, sessionName: `S${i}`, at: T0 + i * 120e3,   // 2min 间隔,绝不聚合
  });
}
check('C1 超上限 FIFO：205 条只留 200,最旧 5 条被淘汰',
  stC.list().length === 200
  && stC.list()[0].sessionId === 's-5'
  && stC.list()[199].sessionId === 's-204');
const stC2 = createNotifyStore({ storage: storC });
check('C2 重建回放：新 store 从同一 storage 读回同样 200 条(id/顺序一致)',
  stC2.list().length === 200
  && stC2.list()[0].id === stC.list()[0].id
  && stC2.list()[199].id === stC.list()[199].id);
check('C3 无 storage(隐私模式等)不炸：内存态照常工作',
  (() => {
    const st = createNotifyStore({ storage: null });
    st.ingest({ kind: 'run_done', title: 'x', body: '', sessionId: 'a', at: T0 });
    return st.list().length === 1;
  })());
check('C4 损坏的持久化数据回退空列表',
  (() => {
    const bad = memStorage();
    bad.setItem('ia-notify-v1', '{oops');
    return createNotifyStore({ storage: bad }).list().length === 0;
  })());

/* ============================================================
   D. 未读计数状态机
   ============================================================ */
console.log('\n== D. 未读计数状态机 ==');

const stD = createNotifyStore({ storage: memStorage() });
const d1 = stD.ingest({ kind: 'run_done', title: '1', body: '', sessionId: 'a', at: T0 }).entry;
stD.ingest({ kind: 'run_failed', title: '2', body: '', sessionId: 'b', at: T0 });
check('D1 新增即未读：两条 → unread=2', stD.unread() === 2);
stD.markRead(d1.id);
check('D2 单条已读：unread=1,重复标记幂等',
  (stD.markRead(d1.id), stD.unread() === 1));
stD.ingest({ kind: 'run_done', title: '1b', body: '', sessionId: 'a', at: T0 + 30e3 });
check('D3 已读条目被聚合更新 → 回到未读（unread=2）',
  stD.unread() === 2 && stD.list().find((e) => e.id === d1.id).read === false);
stD.markAllRead();
check('D4 全部已读：unread=0', stD.unread() === 0);
stD.clear();
check('D5 清空：列表空且 unread=0', stD.list().length === 0 && stD.unread() === 0);

/* ---- 设置持久 ---- */
const cfgStor = memStorage();
const cfg1 = createNotifySettings({ storage: cfgStor });
check('D6 开关默认全关(后台通知/提示音)',
  cfg1.get().browser === false && cfg1.get().sound === false);
cfg1.set({ sound: true });
check('D7 开关持久化：重建后读回', createNotifySettings({ storage: cfgStor }).get().sound === true);

/* ============================================================
   E. 时间展示纯函数
   ============================================================ */
console.log('\n== E. 相对时间与按天分组 ==');

const NOW = Date.parse('2026-08-13T15:00:00');
check('E1 相对时间：刚刚/分钟/小时/隔天 HH:MM',
  relativeTime(NOW - 10e3, NOW) === '刚刚'
  && relativeTime(NOW - 5 * 60e3, NOW) === '5 分钟前'
  && relativeTime(NOW - 3 * 3600e3, NOW) === '3 小时前'
  && /^\d{2}:\d{2}$/.test(relativeTime(NOW - 30 * 3600e3, NOW)));
check('E2 按天分组：今天/昨天/月日(跨年补年份)',
  dayLabel(NOW - 60e3, NOW) === '今天'
  && dayLabel(NOW - 24 * 3600e3, NOW) === '昨天'
  && dayLabel(Date.parse('2026-08-01T08:00:00'), NOW) === '8月1日'
  && dayLabel(Date.parse('2025-12-31T08:00:00'), NOW) === '2025年12月31日');

/* ============================================================
   F. 抽屉 DOM / aria / 交互（自初始化的单例）
   ============================================================ */
console.log('\n== F. 抽屉 DOM 与交互 ==');

const center = initNotifyCenter();
check('F1 模块自初始化：#btnNotify 存在时随导入建好抽屉；重复 init 返回同一实例',
  center !== null && initNotifyCenter() === center
  && DOC.getElementById('notifyDrawer') !== null);
const drawer = center.drawer;
check('F2 aria：抽屉 role=dialog + aria-modal + aria-label',
  drawer.getAttribute('role') === 'dialog'
  && drawer.getAttribute('aria-modal') === 'true'
  && drawer.getAttribute('aria-label') === '通知中心');

center.feedEvent({ t: 'step.end', stepId: 6, exit: 7 });
center.feedEvent({ t: 'note', tone: 'bad', text: '第 6 步失败 · numerical。处置:换方法' });
const badge = bell.querySelector('.nbadge');
check('F3 铃铛角标：两事件聚合为 1 条未读,角标显示 1,aria-label 带未读数',
  center.store.list().length === 1 && badge.textContent === '1' && badge.hidden === false
  && bell.getAttribute('aria-label') === '通知中心，1 条未读');

center.open();
check('F4 打开抽屉：hidden 解除,铃铛 aria-expanded=true',
  center.isOpen() && drawer.hidden === false
  && bell.getAttribute('aria-expanded') === 'true');
const dayLists = drawer.querySelectorAll('.nd-group');
const items = drawer.querySelectorAll('.nd-item');
check('F5 列表结构：按天分组(今天) + role=list/listitem',
  dayLists.length === 1
  && dayLists[0].querySelector('.nd-day').textContent === '今天'
  && dayLists[0].querySelector('div').getAttribute('role') === 'list'
  && items.length === 1
  && items[0].parentNode.getAttribute('role') === 'listitem');
check('F6 条目内容：标题/聚合计数/正文/元信息(相对时间·会话名)',
  items[0].querySelector('.nd-t').textContent.includes('第 6 步失败')
  && items[0].querySelector('.nd-times').textContent === '×2'
  && items[0].querySelector('.nd-b').textContent.includes('numerical')
  && items[0].querySelector('.nd-m').textContent.includes('刚刚')
  && items[0].querySelector('.nd-m').textContent.includes('Ridgecrest 同震形变'));

/* 证据级达标：默认 THRESHOLDS 含 PENDING → 天花板恰为 audited(=达标线) */
center.feedEvent({ t: 'result' });
center.open();
const doneItem = drawer.querySelectorAll('.nd-item').find((el) => el.matches('.is-done'));
check('F7 result → run_done 条目注明「证据级 audited 达标」',
  !!doneItem && doneItem.querySelector('.nd-m').textContent.includes('证据级 audited 达标'));

/* 跳转：点击条目 → 标记已读 + 关闭抽屉（同会话不切换,仅滚动定位） */
const readAllBtn = drawer.querySelectorAll('.nd-act').find((b) => b.textContent === '全部已读');
const clearBtn = drawer.querySelectorAll('.nd-act').find((b) => b.textContent === '清空');
drawer.querySelectorAll('.nd-item').find((el) => el.matches('.is-fail')).click();
check('F8 点击条目：该条已读 + 抽屉关闭(跳转链路走会话侧栏,同会话仅滚动)',
  center.isOpen() === false
  && center.store.list().find((e) => e.kind === 'run_failed').read === true);

center.open();
readAllBtn.click();
check('F9 全部已读：unread=0,角标隐藏,按钮进入禁用态',
  center.store.unread() === 0 && badge.hidden === true && readAllBtn.disabled === true);
clearBtn.click();
check('F10 清空：列表空,渲染空态文案',
  center.store.list().length === 0
  && drawer.querySelector('.nd-empty').textContent.includes('run 结束'));

/* Esc 关闭 */
center.open();
drawer.dispatchEvent({ type: 'keydown', key: 'Escape', stopPropagation() {}, preventDefault() {} });
check('F11 Esc 关闭抽屉', center.isOpen() === false);

/* 开关：Node stub 无 Notification → 后台通知开关禁用并说明;提示音可开 */
const swAll = drawer.querySelectorAll('.nd-switch');
check('F12 无系统通知能力时：后台通知开关禁用 + 说明文案,role=switch 语义完整',
  swAll.length === 2 && swAll[0].disabled === true
  && swAll[0].getAttribute('role') === 'switch'
  && drawer.querySelector('.nd-note').textContent.includes('不支持')
  && swAll[1].getAttribute('role') === 'switch'
  && swAll[1].getAttribute('aria-checked') === 'false');
swAll[1].click();
check('F13 提示音开关可开(WebAudio 缺失时静默,不抛错)',
  swAll[1].getAttribute('aria-checked') === 'true' && center.settings.get().sound === true);
swAll[1].click();

/* ============================================================
   G. 注入环境：权限流与原生通知防抖
   ============================================================ */
console.log('\n== G. 权限流与原生通知（注入假环境） ==');

/** 独立文档(第二实例互不干扰) */
function fakeDocFor() {
  const body = new Element('body');
  return {
    body,
    createElement: DOC.createElement,
    createElementNS: DOC.createElementNS,
    createTextNode: DOC.createTextNode,
    getElementById(id) {
      for (const d of walk(body)) if (d.attrs.id === id) return d;
      return null;
    },
    querySelector: (sel) => body.querySelector(sel),
    activeElement: null,
  };
}

function fakeN(permission, answer = permission) {
  const calls = { request: 0 };
  const created = [];
  class FN {
    static permission = permission;
    static async requestPermission() { calls.request += 1; FN.permission = answer; return answer; }
    constructor(title, opts = {}) { this.title = title; this.body = opts.body; created.push(this); }
    close() {}
  }
  return { FN, calls, created };
}

/* G1: 开启开关 → 申请权限,granted 后原生通知只在 hidden 时发,且防抖合一 */
{
  const { FN, calls, created } = fakeN('default', 'granted');
  const hiddenDoc = { hidden: true, title: 'x', addEventListener() {} };
  const notifier = Notify.createNotifier({
    doc: hiddenDoc, getNotification: () => FN, getTauri: () => null,
  });
  const c = createNotifyCenter({
    doc: fakeDocFor(), storage: memStorage(), notifier,
    getNotification: () => FN,
    session: () => ({ id: 'a', name: 'A' }),
    evidence: () => null,
    signalDebounceMs: 10,
  });
  const sw = c.drawer.querySelectorAll('.nd-switch')[0];
  sw.click();
  await sleep(20);
  check('G1 打开「后台通知」→ 用户手势时刻申请权限并记住开启',
    calls.request === 1 && c.settings.get().browser === true
    && sw.getAttribute('aria-checked') === 'true');
  c.feedEvent({ t: 'step.end', stepId: 6, exit: 7 });
  c.feedEvent({ t: 'note', tone: 'bad', text: '第 6 步失败 · numerical' });
  c.feedEvent({ t: 'note', tone: 'warn', text: '第 6 步已取消 · 可续跑' });
  await sleep(60);
  check('G2 三个事件突发 → 抽屉聚合 1 条,原生通知防抖后只发 1 条,正文取精化后形态',
    c.store.list().length === 1 && created.length === 1
    && created[0].body.includes('运行已中断'));
}

/* G3: 权限被拒 → 开关禁用并说明 */
{
  const { FN } = fakeN('denied');
  const notifier = Notify.createNotifier({
    doc: { hidden: true, title: 'x', addEventListener() {} },
    getNotification: () => FN, getTauri: () => null,
  });
  const c = createNotifyCenter({
    doc: fakeDocFor(), storage: memStorage(), notifier,
    getNotification: () => FN,
    session: () => ({ id: 'a', name: 'A' }),
    evidence: () => null,
  });
  const sw = c.drawer.querySelectorAll('.nd-switch')[0];
  const note = c.drawer.querySelector('.nd-note');
  check('G3 权限已被拒：开关禁用 + 面板说明如何恢复',
    sw.disabled === true && note.hidden === false
    && note.textContent.includes('拒绝'));
}

/* G4: 上次开着,期间权限被收回 → 启动时落回关闭态 */
{
  const { FN } = fakeN('denied');
  const stor = memStorage();
  createNotifySettings({ storage: stor }).set({ browser: true });
  const c = createNotifyCenter({
    doc: fakeDocFor(), storage: stor,
    notifier: Notify.createNotifier({
      doc: { hidden: true, title: 'x', addEventListener() {} },
      getNotification: () => FN, getTauri: () => null,
    }),
    getNotification: () => FN,
    session: () => ({ id: 'a', name: 'A' }),
    evidence: () => null,
  });
  check('G4 权限期间被收回：开关显式落回关闭态(不留「开着却发不出」的假状态)',
    c.settings.get().browser === false);
}

/* G5: runEnded / approvalNeeded 兜底改道进单例中心(mock 模式唯一收集源) */
{
  const before = center.store.list().length;
  Notify.runEnded('done');
  Notify.approvalNeeded('流水线执行需要你的确认（审批卡已就绪）');
  const list = center.store.list();
  check('G5 runEnded/approvalNeeded 改道通知中心：完成 + 审批各留一条',
    list.length === before + 2
    && list.some((e) => e.kind === 'run_done')
    && list.some((e) => e.kind === 'approval' && e.body.includes('审批卡')));
  const p = await Notify.ensurePermission();
  check('G6 开关关闭时 ensurePermission 不再弹权限框(返回 false)', p === false);
}

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
center.feedEvent({ t: 'step.end', stepId: 6, exit: 7 });
center.open();
console.log('--- 通知抽屉（含一条未读失败通知） ---');
process.stdout.write(serialize(drawer));

console.log(failed ? `\n${failed} 项断言失败` : '\n全部断言通过');
process.exit(failed ? 1 : 0);
