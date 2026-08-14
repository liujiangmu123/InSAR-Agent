/* ============================================================
   数据不可用语义的无浏览器自查脚本(node prototype/demo-mode.check.mjs)
   —— 「核心层演示脚手架删除」(state.js 不再内置会话/步骤种子)与
   「面板演示回落清除」(dock 各面板不可达/无数据渲染诚实错误态/空态)
   两波之后的合并语义;聊天层 mock 演示模式(backend.mock.js)保留仍验证:

   0. 启动空态:SESSIONS/STEP_DEFS/S.steps 启动全空,演示种子已删除
      (界面数据只来自 /api/sessions、/api/registry、/api/state);
   A. file:// 短路:backend.sse 与各 *live 模块不发任何 API 请求,直接
      resolve null / 走 mock;
   B. 后端不可达(fetch 全部拒绝):每个取数入口 resolve null 而非抛错,
      失败不占缓存位(可立即重试);后端可达但会话无 run:fileslive/
      auditlive/reportlive resolve { noRun: true }(空态语义,非错误);
   C. useMock 切换链路(聊天层,保留):http 下 runTurn 首次失败 →
      note 警示 + 无缝转 mock 事件流;此后同实例的只读查询不再发请求;
   D. 诚实状态 UI:gallery 错误态(带重试)/空态(带运行引导),
      tspoint 错误态/空态,面板绝不渲染演示图件网格与演示曲线;
      demoBanner 仅剩 env 面板一个消费方,fileslive/auditlive 不再导出;
   E. 注册表水合与镜像种子:setRegistry(真实 /api/registry 快照)→
      STEP_DEFS 11 步;setSessions 映射服务端行;seedSteps 模拟
      服务端计划;fileTree 从注册表 outputs 派生;envdata.js 的
      TERM_LOGS/TRACE/cmdSh 演示常量已删除;
   F. 源码级对齐:mock 事件类型 ⊆ app.js consume 分支;dock.js 不再
      含 files/audit/report/term/trace 的演示回落;file:// 判据保留。

   只依赖 node 内建能力,零 npm 依赖(与 fail-demo.check.mjs 同模式)。
   ============================================================ */
import { readFileSync } from 'node:fs';

/* ---------------- 最小 DOM stub（与 fail-demo.check.mjs 同源） ---------------- */
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
  set innerHTML(v) { this.replaceChildren(new TextNode(v)); }   // 仅可信模板注入用
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
};

globalThis.document = DOC;
globalThis.Node = NodeBase;
globalThis.Event = class { constructor(type, opts = {}) { this.type = type; Object.assign(this, opts); } };
globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
globalThis.requestAnimationFrame = (fn) => { fn(); return 0; };
globalThis.matchMedia = () => ({ matches: true });

/* mock 的剧情等待都走 setTimeout:统一钳到 ≤10ms,让完整回合秒级跑完 */
const realSetTimeout = globalThis.setTimeout;
globalThis.setTimeout = (fn, ms, ...a) => realSetTimeout(fn, Math.min(ms || 0, 10), ...a);

/* ---------------- 网络桩:fetch 计数 + 三种模式 ----------------
   reject  —— 后端不可达(全部拒绝);
   forbid  —— file:// 阶段不允许被调(违例记录);
   norun   —— 后端可达但会话没有任何 run(空 workspace 语义,按路由
              返回真实后端同款响应:artifacts/figures/state 200 空结构,
              provenance/methods 404,timeseries-point 404+结构化 detail)。 */
let fetchCalls = [];                      // 每次尝试的 url
let fetchMode = 'reject';                 // reject | forbid | norun
let forbidViolations = [];

const respond = (status, body, text = '') => Promise.resolve({
  ok: status >= 200 && status < 300,
  status,
  headers: { get: () => null },
  json: async () => body,
  text: async () => text,
});

globalThis.fetch = (url) => {
  const u = String(url);
  fetchCalls.push(u);
  if (fetchMode === 'forbid') {
    forbidViolations.push(u);
  }
  if (fetchMode === 'norun') {
    if (u.includes('/api/artifacts')) return respond(200, { run: null, steps: [] });
    if (u.includes('/api/figures')) return respond(200, { run: null, figures: [] });
    if (u.includes('/api/state')) return respond(200, { run: null, steps: [] });
    if (u.includes('/api/trace')) return respond(200, []);
    if (u.includes('/api/timeseries-point')) {
      return respond(404, { detail: { error: 'no_timeseries' } });
    }
    return respond(404, { detail: 'no run' });   // provenance / methods.md / env …
  }
  return Promise.reject(new TypeError('Failed to fetch'));
};

/* location 桩:先 file: 再翻 http:(各 *live 模块调用期判据) */
globalThis.location = { protocol: 'file:', search: '' };

/* ---------------- 断言工具 ---------------- */
let failed = 0;
let passed = 0;
function check(name, cond) {
  if (cond) { passed += 1; console.log(`  ok  ${name}`); }
  else { failed += 1; console.error(`  FAIL ${name}`); }
}
const sleep = (ms) => new Promise((r) => realSetTimeout(r, ms));
async function collect(iter) {
  const evs = [];
  for await (const ev of iter) evs.push(ev);
  return evs;
}

/* ============================================================
   模块导入(protocol=file: 时导入 backend.sse → useMock 初始为 true)
   ============================================================ */
const API_FILE = await import('./js/backend.sse.js');            // useMock=true 实例
const St = await import('./js/state.js');
const { S, STEP_DEFS, SESSIONS, THRESHOLDS, LADDER, workSummary,
        evidenceCeiling, fileTree } = St;
const Envlive = await import('./js/envlive.js');
const Fileslive = await import('./js/fileslive.js');
const Auditlive = await import('./js/auditlive.js');
const Reportlive = await import('./js/reportlive.js');
const Gallery = await import('./js/gallery.js');
const Tspoint = await import('./js/tspoint.js');
const Notify = await import('./js/notify.js');
const Queue = await import('./js/queue.js');
const Figures = await import('./js/figures.js');
const Envdata = await import('./js/envdata.js');
const Mock = await import('./js/backend.mock.js');
const { REGISTRY, seedSteps } = await import('../tests/js/_registry.mjs');

/* ---------------- 启动空态:演示种子已彻底删除 ---------------- */
console.log('== 0. 启动空态(演示种子已删除) ==');
check('01 启动时 SESSIONS 为空(不再内置 Ridgecrest/玉树/雅鲁藏布江演示会话)',
  SESSIONS.length === 0);
check('02 启动时 STEP_DEFS 为空(步骤目录只来自 /api/registry)',
  STEP_DEFS.length === 0);
check('03 启动时步骤镜像为空 + 无会话 id(计划只来自 /api/state)',
  S.steps.size === 0 && S.sessionId === null);

// 后续 mock 链路用真实注册表快照水合 + 模拟服务端计划(前 5 步 done)
St.setRegistry(REGISTRY);
seedSteps(St, 5);
S.sessionId = 'ridgecrest-2019';   // mock 剧情引用的会话 id(仅测试进程内)

/* ============================================================
   A. file:// 短路 —— 不发任何 API 请求
   ============================================================ */
console.log('== A. file:// 短路(0 次网络请求) ==');
fetchMode = 'forbid';
fetchCalls = [];
forbidViolations = [];

check('A1 backend.sse fetchState() → null', (await API_FILE.fetchState()) === null);
check('A2 backend.sse fetchChat() → null', (await API_FILE.fetchChat()) === null);
check('A3 backend.sse fetchTrace() → null', (await API_FILE.fetchTrace()) === null);
check('A4 backend.sse fetchImpact(6) → null', (await API_FILE.fetchImpact(6, { method: 'snaphu_mcf' })) === null);
check('A5 backend.sse fetchLogs(6) → null', (await API_FILE.fetchLogs(6)) === null);
check('A6 envlive.fetchEnvLive() → null', (await Envlive.fetchEnvLive()) === null);
check('A7 fileslive.fetchFilesLive() → null', (await Fileslive.fetchFilesLive()) === null);
check('A8 auditlive.fetchAuditLive() → null', (await Auditlive.fetchAuditLive()) === null);
check('A9 reportlive.fetchReportLive() → null', (await Reportlive.fetchReportLive()) === null);
check('A10 connectEvents → no-op 断开函数', typeof API_FILE.connectEvents(() => {}) === 'function');

const turnFile = await collect(API_FILE.runTurn('分析 Ridgecrest 同震形变', new API_FILE.Cancel()));
check('A11 file:// 下 runTurn 直接产出 mock 事件流(首事件 thinking)',
  turnFile.length > 3 && turnFile[0].t === 'thinking');
check('A12 file:// 下 runTurn 不插「已切换 mock」横幅(本来就是 mock)',
  !turnFile.some((e) => e.t === 'note' && /切换到本地演示/.test(e.text || '')));
check('A13 runTurn 收尾于第 6 步候选集(决策点)',
  turnFile[turnFile.length - 1].t === 'candidates' && turnFile[turnFile.length - 1].stepId === 6);
check('A14 全阶段 0 次 API 请求(file:// 判据在每个模块生效)', forbidViolations.length === 0);

/* ============================================================
   B. 后端不可达(http + fetch 全拒) —— resolve null,绝不抛错
   ============================================================ */
console.log('\n== B. 后端不可达:每个取数入口 resolve null ==');
fetchMode = 'reject';
globalThis.location.protocol = 'http:';

// 各 live 模块调用期判据:此时应真的发起请求、拒绝后回落 null
fetchCalls = [];
Envlive.invalidate();
check('B1 envlive:拒绝 → null', (await Envlive.fetchEnvLive()) === null);
const envTries = fetchCalls.filter((u) => u.includes('/api/env')).length;
check('B2 envlive:确实尝试了 /api/env(非静默跳过)', envTries >= 1);
check('B3 envlive:失败不占缓存位,可立即重试',
  (await Envlive.fetchEnvLive()) === null
  && fetchCalls.filter((u) => u.includes('/api/env')).length > envTries);

Fileslive.invalidate();
check('B4 fileslive:拒绝 → null(错误态语义)', (await Fileslive.fetchFilesLive()) === null);
Auditlive.invalidate();
check('B5 auditlive:拒绝 → null(错误态语义)', (await Auditlive.fetchAuditLive()) === null);
Reportlive.invalidate();
check('B6 reportlive:拒绝 → null(错误态语义)', (await Reportlive.fetchReportLive()) === null);

// 后端可达但会话没有 run(空 workspace):三个取数模块 resolve { noRun: true }
// —— 与 null(不可达)区分,调用方据此渲染空态(带运行引导)而非错误态
fetchMode = 'norun';
Fileslive.invalidate();
check('B7 fileslive:可达但无 run(artifacts run=null)→ { noRun: true }',
  (await Fileslive.fetchFilesLive())?.noRun === true);
Auditlive.invalidate();
check('B8 auditlive:可达但无 run(provenance 404)→ { noRun: true }',
  (await Auditlive.fetchAuditLive())?.noRun === true);
Reportlive.invalidate();
check('B9 reportlive:可达但无 run(methods.md 404)→ { noRun: true }',
  (await Reportlive.fetchReportLive())?.noRun === true);
fetchMode = 'reject';
Fileslive.invalidate();
Auditlive.invalidate();
Reportlive.invalidate();

// notify 运维视图轮询:不可达 → 回调 null(UI 退回本地状态归组)
const adminSeen = [];
const stopPoll = Notify.startAdminPoll((m) => adminSeen.push(m), {
  intervalMs: 60_000, fetchFn: () => Promise.reject(new Error('down')),
});
await sleep(30);
stopPoll();
check('B10 notify.startAdminPoll:不可达 → onRuns(null)', adminSeen.length >= 1 && adminSeen[0] === null);
// 会话镜像按服务端 /api/sessions 行播种(tone 统一 idle;真实状态由运维视图/实时态覆盖)
St.setSessions([
  { session_id: 'ridgecrest-2019', name: 'Ridgecrest 同震形变', mode: 'expert', created_at: 1755000000 },
  { session_id: 's-b', name: '会话乙', mode: 'guide', created_at: 1755000100 },
]);
check('B11 notify.groupSessions(adminMap=null):服务端播种的会话统一按 idle 归组',
  JSON.stringify(Notify.groupSessions(SESSIONS, {}, null).map((g) => g.key))
  === JSON.stringify(['idle'])
  && Notify.groupSessions(SESSIONS, {}, null)[0].items.length === 2);

/* ============================================================
   C. useMock 切换链路(http 实例):失败 → note 警示 + mock 接管
   ============================================================ */
console.log('\n== C. useMock 切换链路(后端中途不可达) ==');
// 带查询串重新导入 → 独立模块实例,此刻 protocol=http: → useMock=false
const API_HTTP = await import('./js/backend.sse.js?instance=http');

fetchCalls = [];
check('C1 http 实例 fetchState:尝试请求后回落 null',
  (await API_HTTP.fetchState()) === null && fetchCalls.some((u) => u.includes('/api/state')));

const turnHttp = await collect(API_HTTP.runTurn('分析 Ridgecrest 同震形变', new API_HTTP.Cancel()));
check('C2 runTurn 失败切换:首事件为「后端不可达,已切换 mock」警示',
  turnHttp[0]?.t === 'note' && turnHttp[0]?.tone === 'warn' && /切换到本地演示模式/.test(turnHttp[0]?.text || ''));
check('C3 警示之后是完整 mock 规划流(thinking → … → candidates)',
  turnHttp[1]?.t === 'thinking' && turnHttp[turnHttp.length - 1]?.t === 'candidates');

fetchCalls = [];
check('C4 切换后同实例只读查询不再发请求(useMock 被尊重)',
  (await API_HTTP.fetchState()) === null && fetchCalls.length === 0);

const pipeEvs = await collect(API_HTTP.runPipeline([9], new API_HTTP.Cancel()));
check('C5 runPipeline 走 mock:step.start/step.end(exit 0)齐全',
  pipeEvs.some((e) => e.t === 'step.start' && e.stepId === 9)
  && pipeEvs.some((e) => e.t === 'step.end' && e.stepId === 9 && e.exit === 0));
check('C6 runPipeline 收尾 result + report(结果卡/报告卡有数据可画)',
  pipeEvs.some((e) => e.t === 'result') && pipeEvs.some((e) => e.t === 'report'));

// C6b 界面诚实化(0814B W6):runPipeline 首次失败切换(独立实例走 catch 路径)
// 也要与 runTurn/runConverse 同款 warn note 先行 —— 回退后的事件全是假的,必须先声明
const API_PIPE = await import('./js/backend.sse.js?instance=pipe');
const pipeFirst = await collect(API_PIPE.runPipeline([9], new API_PIPE.Cancel()));
check('C6b runPipeline 首次失败切换:「已切换到本地演示模式」warn note 先行',
  pipeFirst[0]?.t === 'note' && pipeFirst[0]?.tone === 'warn'
  && /切换到本地演示模式/.test(pipeFirst[0]?.text || '')
  && pipeFirst.some((e) => e.t === 'step.start' && e.stepId === 9));

// 第 8 步 ERA5 degrade 剧情:专家模式停链,degrade 事件收尾
const pipe8 = await collect(API_HTTP.runPipeline([8], new API_HTTP.Cancel()));
check('C7 第 8 步降级剧情:degrade 事件到达且专家模式停链(最后一个事件)',
  pipe8[pipe8.length - 1]?.t === 'degrade' && pipe8[pipe8.length - 1]?.auto === false
  && pipe8.some((e) => e.t === 'tool.end' && e.exit === 1));

// mock 事件类型 ⊆ app.js consume/onGlobalEvent 处理分支(源码级对齐)
const appSrc = readFileSync(new URL('./js/app.js', import.meta.url), 'utf-8');
const handled = new Set([...appSrc.matchAll(/case '([\w.]+)':/g)].map((m) => m[1]));
const demoEvs = await collect(Mock.demoLongTaskEvents(new API_HTTP.Cancel()));
const emitted = new Set([...turnHttp, ...pipeEvs, ...pipe8, ...demoEvs].map((e) => e.t));
const unhandled = [...emitted].filter((t) => !handled.has(t));
check(`C8 mock 事件类型全部被 app.js consume 消费(${[...emitted].length} 类,漏 ${unhandled.length})`,
  unhandled.length === 0);
check('C9 §7.4 演示条目(reattach/intervention/gate_stop)仍可产出',
  ['reattach', 'intervention', 'gate_stop'].every((t) => demoEvs.some((e) => e.t === t)));

/* ============================================================
   D. 诚实状态 UI:gallery / tspoint 的错误态与空态(无演示回落)
   ============================================================ */
console.log('\n== D. 诚实状态 UI(错误态/空态,无演示回落) ==');

// D1–D3 gallery:后端不可达 → 错误态带重试,绝不渲染演示图件网格
const galBox = Gallery.galleryView();
DOC.body.appendChild(galBox);
await sleep(30);
check('D1 gallery:后端不可达 → 错误态(.es-error)且带「重试」按钮',
  galBox.querySelectorAll('.es-error').length === 1
  && galBox.querySelectorAll('.es-retry').length === 1);
check('D2 gallery:不再渲染任何图件卡片与「演示图件」角标',
  galBox.querySelectorAll('.glx-card').length === 0
  && galBox.querySelectorAll('.glx-demo').length === 0);
check('D3 gallery:错误文案指明「后端不可达或响应异常」,不含「演示」',
  /后端不可达或响应异常/.test(galBox.textContent) && !/演示/.test(galBox.textContent));

// D4–D5 tspoint:探测失败(不可达)→ 错误态卡带重试,无演示曲线
const tsHost = new Element('div');
DOC.body.appendChild(tsHost);
Tspoint.mountSpatial(tsHost);
await sleep(30);
const tsCardErr = tsHost.querySelector('.tscard');
check('D4 tspoint:不可达 → 错误态卡(data-mode=error,.es-error 带重试)',
  tsCardErr?.dataset.mode === 'error'
  && tsCardErr.querySelectorAll('.es-error').length === 1
  && tsCardErr.querySelectorAll('.es-retry').length === 1);
check('D5 tspoint:不再渲染演示曲线与演示点位',
  !/演示曲线/.test(tsHost.textContent) && !/figures\.js 手绘/.test(tsHost.textContent)
  && tsHost.querySelectorAll('.mapcard').length === 0);

// D6 tspoint:可达但无真实时序(404 no_timeseries)→ 空态卡 + 运行引导 + 重新探测
fetchMode = 'norun';
S.sessionId = 'demo-check-norun';          // 换会话键触发 tspoint 状态重置
const tsHost2 = new Element('div');
DOC.body.appendChild(tsHost2);
Tspoint.mountSpatial(tsHost2);
await sleep(30);
const tsCardEmpty = tsHost2.querySelector('.tscard');
check('D6 tspoint:可达无数据 → 空态卡(data-mode=empty)+ 运行引导 + 「重新探测」',
  tsCardEmpty?.dataset.mode === 'empty'
  && tsCardEmpty.querySelectorAll('.es-empty').length === 1
  && /运行流水线/.test(tsCardEmpty.textContent)
  && /重新探测/.test(tsCardEmpty.textContent));

// D7 gallery:可达但无产物(figures 空列表)→ 空态卡 + 运行引导
const galBox2 = Gallery.galleryView();
DOC.body.appendChild(galBox2);
await sleep(30);
check('D7 gallery:可达无产物 → 空态卡(.es-empty)+「运行流水线以生成图件」引导',
  galBox2.querySelectorAll('.es-empty').length === 1
  && /还没有产物图件/.test(galBox2.textContent)
  && /运行流水线以生成图件/.test(galBox2.textContent));
fetchMode = 'reject';
S.sessionId = SESSIONS[0]?.id || 'ridgecrest-2019';

// D8–D9 demoBanner 收敛 / skeleton:横幅只剩 env 面板一个消费方
const banner = Envlive.demoBanner('测试文案', { onRetry: () => {} });
check('D8 demoBanner 仍供 env 面板使用:role=status + 加粗标注 + 重试按钮',
  banner.getAttribute('role') === 'status'
  && /演示数据（后端未连接）/.test(banner.textContent)
  && banner.querySelectorAll('button').length === 1);
check('D9 fileslive/auditlive 不再导出 demoBanner(演示回落已清除)',
  !('demoBanner' in Fileslive) && !('demoBanner' in Auditlive));
check('D10 skeleton 骨架屏可用(aria-busy,env/files/audit 三款)',
  Envlive.skeleton().getAttribute('aria-busy') === 'true'
  && Fileslive.skeleton().getAttribute('aria-busy') === 'true'
  && Auditlive.skeleton().getAttribute('aria-busy') === 'true');

// D11–D12 queue:排队 → 回合结束 FIFO 发出;停止(paused)时保留
const prompt = new Element('textarea');
prompt.setAttribute('id', 'prompt');
DOC.body.appendChild(prompt);
Queue.clear();
Queue.enqueue('排队消息一');
S.phase = 'paused';
Queue.flush(() => { throw new Error('paused 时不应发送'); });
check('D11 queue:phase=paused 时 flush 保留队列(停止语义)', Queue.count() === 1);
S.phase = 'idle';
const sent = [];
Queue.flush((t) => sent.push(t));
await sleep(30);
check('D12 queue:恢复后 flush FIFO 发出并清空', sent[0] === '排队消息一' && Queue.count() === 0);

/* ============================================================
   E. 种子与渲染器边界(state / figures / envdata)
   ============================================================ */
console.log('\n== E. 注册表水合与镜像种子(演示种子已删除) ==');
check('E1 setRegistry(注册表快照)→ STEP_DEFS = 11 步且 id 连续,每步含 methods/params/outputs',
  STEP_DEFS.length === 11
  && STEP_DEFS.every((d, i) => d.id === i + 1 && d.methods.length > 0 && d.params && Array.isArray(d.outputs)));
check('E2 setSessions 映射 /api/sessions 行:session_id→id,name/sub 齐备,不再有内置演示会话',
  SESSIONS.length === 2 && SESSIONS.every((s) => s.id && s.name && s.sub && s.tone === 'idle')
  && SESSIONS.some((s) => s.id === 'ridgecrest-2019'));
check('E3 seedSteps(服务端计划,前 5 步 done)后 workSummary:待跑恰为第 6–11 步',
  JSON.stringify(workSummary().all) === JSON.stringify([6, 7, 8, 9, 10, 11]));

const tree = fileTree();
check('E4 fileTree 从注册表 outputs 派生:非空且每项含 path/kind/step/hash',
  tree.length >= 10 && tree.every((f) => f.path && f.kind && f.step && 'hash' in f));
check('E5 fileTree 覆盖注册表声明的配置/质检产物(unwrap.yaml + qa.json)',
  ['params/unwrap.yaml', 'products/report/qa.json']
    .every((p) => tree.some((f) => f.path === p)));
check('E6 THRESHOLDS 5 项(3 项 PENDING)→ evidenceCeiling 封顶 audited(2)',
  THRESHOLDS.length === 5
  && THRESHOLDS.filter((t) => t.status === 'PENDING').length === 3
  && evidenceCeiling().level === 2);
check('E7 LADDER 六级证据阶梯完整', LADDER.length === 6 && LADDER[5] === 'publishable');

// figures.js 界面诚实化(0814B W6):演示图资产(IMAGES/POINTS/DATES/
// velSvg/ifgSvg/cohSvg)已删除,仅剩零数据空壳保持 stream.js 死代码
// (W3 并行删除中)的 import 绑定可解析;tspoint 真实渲染仍消费
// timeSeriesSvg(画真实数据)/ mapSvg(点击画布,点位由调用方传入)
check('E8 figures:演示图资产已清除 —— IMAGES/POINTS/DATES 空壳零数据,figureSvg 恒空串',
  Figures.IMAGES.length === 0 && Figures.POINTS.length === 0 && Figures.DATES.length === 0
  && Figures.figureSvg('vel') === '' && Figures.figureSvg('ts') === '');
check('E9 figures:tspoint 在用的 mapSvg/timeSeriesSvg 渲染器产出可嵌入 SVG',
  /^<svg/.test(Figures.mapSvg(null, { markers: [{ x: 10, y: 10, color: '#f00', label: 'P1' }], note: 'n' }))
  && /^<svg/.test(Figures.timeSeriesSvg({ dates: ['06-10', '06-22'], series: [{ name: 'A', ts: [0, 1], color: '#f00' }] })));

check('E10 envdata:TERM_LOGS/TRACE/cmdSh 演示常量已删除(终端/轨迹无假数据可用)',
  !('TERM_LOGS' in Envdata) && !('TRACE' in Envdata) && !('cmdSh' in Envdata));
check('E11 envdata:ENGINES/DISKS/WSL/WORKSPACE/ENV_NOTE 环境演示数据齐备(env 面板范围外保留)',
  Envdata.ENGINES.length >= 4 && Envdata.DISKS.length >= 1
  && !!Envdata.WSL.text && !!Envdata.WORKSPACE.hint && /静态示意/.test(Envdata.ENV_NOTE));

/* ============================================================
   F. 源码级对齐(dock.js 演示回落已清除 + file:// 判据保留)
   ============================================================ */
console.log('\n== F. 源码级对齐 ==');
const dockSrc = readFileSync(new URL('./js/dock.js', import.meta.url), 'utf-8');
check('F1 dock.js:files/audit 面板不再挂 demoBanner;env 面板(范围外)保留',
  !/FILES\.demoBanner\(/.test(dockSrc) && !/AUD\.demoBanner\(/.test(dockSrc)
  && /LIVE\.demoBanner\(/.test(dockSrc));
check('F2 dock.js:report 静态演示草稿已删(无「演示数据(未接入真实运行)」标注)',
  !dockSrc.includes('演示数据（未接入真实运行）'));
check('F3 dock.js:终端/轨迹演示视图已删(termDemoView/traceDemoView 与其标注不复存在)',
  !dockSrc.includes('termDemoView') && !dockSrc.includes('traceDemoView')
  && !dockSrc.includes('以下为离线示意日志') && !dockSrc.includes('演示数据 · 后端未接入'));
check('F4 dock.js:五面板接入统一构造器(renderError ≥4 处 · renderEmpty ≥4 处)',
  (dockSrc.match(/ES\.renderError\(/g) || []).length >= 4
  && (dockSrc.match(/ES\.renderEmpty\(/g) || []).length >= 4);
check('F5 dock.js:不再 import figures.js/fileTree(演示数据源断开)',
  !dockSrc.includes("from './figures.js'") && !/[^\w]fileTree[^\w]/.test(dockSrc));
const fileGuard = (p) => readFileSync(new URL(`./js/${p}`, import.meta.url), 'utf-8')
  .includes("location.protocol === 'file:'");
check('F6 六个取数模块都保留 file:// 判据(envlive/fileslive/auditlive/reportlive/gallery/tspoint)',
  ['envlive.js', 'fileslive.js', 'auditlive.js', 'reportlive.js', 'gallery.js', 'tspoint.js'].every(fileGuard));
check('F7 notify/backend.sse 亦保留 file:// 判据',
  fileGuard('notify.js') && fileGuard('backend.sse.js'));

/* ---------------- 收尾 ---------------- */
console.log(`\n${passed} 项通过${failed ? ` · ${failed} 项失败` : ' · 全部断言通过'}`);
process.exit(failed ? 1 : 0);
