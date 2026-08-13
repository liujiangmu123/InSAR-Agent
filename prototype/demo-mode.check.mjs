/* ============================================================
   演示回落模式的无浏览器自查脚本（node prototype/demo-mode.check.mjs）
   —— 三波大改(gallery/envlive/fileslive/auditlive/reportlive/tspoint/
   pipelinerail/queue/notify/a11y + dock/app/stream 大改)之后,验证
   「file:// 打开或后端不可达时回落 mock 演示模式」承诺仍然成立:

   A. file:// 短路:backend.sse 与各 *live 模块不发任何 API 请求,直接
      resolve null / 走 mock;
   B. 后端不可达(fetch 全部拒绝):每个取数入口 resolve null 而非抛错,
      失败不占缓存位(可立即重试);
   C. useMock 切换链路:http 下 runTurn 首次失败 → note 警示 + 无缝转
      mock 事件流;此后同实例的只读查询不再发请求;
   D. 演示 UI 兜底:gallery 演示图件网格 + 标注、tspoint 演示曲线卡 +
      回落原因横幅、demoBanner/skeleton 组件、queue 排队语义;
   E. mock 种子完整性:STEP_DEFS 11 步 / SESSIONS / fileTree / THRESHOLDS /
      figures(IMAGES/POINTS/DATES) / envdata(TRACE/TERM_LOGS/ENGINES…)
      仍在且形状满足 dock 演示视图的消费;
   F. 源码级对齐:mock 事件类型 ⊆ app.js consume 分支;各 dock 演示
      回落带「演示数据」标注;各取数模块保留 file:// 判据。

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

/* ---------------- 网络桩:fetch 全部拒绝 + 计数 ---------------- */
let fetchCalls = [];                      // 每次尝试的 url
let fetchMode = 'reject';                 // reject | forbid(file:// 阶段不允许被调)
globalThis.fetch = (url) => {
  fetchCalls.push(String(url));
  if (fetchMode === 'forbid') {
    forbidViolations.push(String(url));
  }
  return Promise.reject(new TypeError('Failed to fetch'));
};
let forbidViolations = [];

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
const { S, STEP_DEFS, SESSIONS, THRESHOLDS, LADDER, fileTree, workSummary,
        evidenceCeiling } = St;
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

St.initSteps(5);   // 演示种子:前 5 步 done,6–11 pending

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
check('B4 fileslive:拒绝 → null', (await Fileslive.fetchFilesLive()) === null);
Auditlive.invalidate();
check('B5 auditlive:拒绝 → null', (await Auditlive.fetchAuditLive()) === null);
Reportlive.invalidate();
check('B6 reportlive:拒绝 → null', (await Reportlive.fetchReportLive()) === null);

// notify 运维视图轮询:不可达 → 回调 null(UI 退回本地状态归组)
const adminSeen = [];
const stopPoll = Notify.startAdminPoll((m) => adminSeen.push(m), {
  intervalMs: 60_000, fetchFn: () => Promise.reject(new Error('down')),
});
await sleep(30);
stopPoll();
check('B7 notify.startAdminPoll:不可达 → onRuns(null)', adminSeen.length >= 1 && adminSeen[0] === null);
check('B8 notify.groupSessions(adminMap=null) 按本地 tone 归组',
  JSON.stringify(Notify.groupSessions(SESSIONS, {}, null).map((g) => g.key))
  === JSON.stringify(['active', 'idle']));

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
   D. 演示 UI 兜底:gallery / tspoint / demoBanner / queue
   ============================================================ */
console.log('\n== D. 演示 UI 兜底组件 ==');

// D1–D3 gallery:后端不可达 → 演示图件网格 + 「演示图件」标注 + 原因说明
const galBox = Gallery.galleryView();
DOC.body.appendChild(galBox);
await sleep(30);
check('D1 gallery:回落为演示图件网格(4 张 IMAGES 卡片)',
  galBox.querySelectorAll('.glx-card').length === Figures.IMAGES.length);
check('D2 gallery:每张缩略图带「演示图件」角标',
  galBox.querySelectorAll('.glx-demo').length === Figures.IMAGES.length);
check('D3 gallery:说明文案指明「后端不可达 + 演示图件」',
  /后端不可达/.test(galBox.textContent) && /演示图件/.test(galBox.textContent));

// D4–D7 tspoint:探测失败 → 演示曲线卡 + 回落原因横幅 + 演示点位按钮
const tsHost = new Element('div');
DOC.body.appendChild(tsHost);
Tspoint.mountSpatial(tsHost);
await sleep(30);
const tsCard = tsHost.querySelector('.tscard');
check('D4 tspoint:回落演示曲线卡(data-mode=demo)', tsCard?.dataset.mode === 'demo');
check('D5 tspoint:横幅注明回落原因「后端不可达 —— 展示演示曲线」',
  /后端不可达 —— 展示演示曲线/.test(tsCard?.textContent || ''));
check('D6 tspoint:标题标注「演示曲线」且底注声明数据来源 figures.js',
  /演示曲线/.test(tsCard?.textContent || '') && /figures\.js 手绘/.test(tsCard?.textContent || ''));
check('D7 tspoint:演示点位按钮(3 个 POINTS)+「重新探测」入口保留',
  tsHost.querySelectorAll('.pins .pin').length >= Figures.POINTS.length + 2
  && /重新探测/.test(tsHost.textContent));

// D8–D10 demoBanner / skeleton:三个面板共用同一款「演示数据」横幅
const banner = Envlive.demoBanner('测试文案', { onRetry: () => {} });
check('D8 demoBanner:role=status + 「演示数据(后端未连接)」加粗标注 + 重试按钮',
  banner.getAttribute('role') === 'status'
  && /演示数据（后端未连接）/.test(banner.textContent)
  && banner.querySelectorAll('button').length === 1);
check('D9 demoBanner 三面板同源(files/audit 复用 envlive 同一函数)',
  Fileslive.demoBanner === Envlive.demoBanner && Auditlive.demoBanner === Envlive.demoBanner);
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
   E. mock 种子完整性(state / figures / envdata)
   ============================================================ */
console.log('\n== E. mock 种子完整性 ==');
check('E1 STEP_DEFS = 11 步且 id 连续,每步含 methods/params/outputs',
  STEP_DEFS.length === 11
  && STEP_DEFS.every((d, i) => d.id === i + 1 && d.methods.length > 0 && d.params && Array.isArray(d.outputs)));
check('E2 演示会话 SESSIONS ≥3 且含 ridgecrest 主会话',
  SESSIONS.length >= 3 && SESSIONS.some((s) => s.id === 'ridgecrest-2019' && s.tone && s.sub));
check('E3 initSteps(5) 后 workSummary:待跑恰为第 6–11 步',
  JSON.stringify(workSummary().all) === JSON.stringify([6, 7, 8, 9, 10, 11]));

const tree = fileTree();
check('E4 fileTree 演示产物树非空且每项含 path/kind/step/hash',
  tree.length >= 10 && tree.every((f) => f.path && f.kind && f.step && 'hash' in f));
check('E5 fileTree 覆盖 dock 演示预览的三个文本文件(FILE_TEXT 键对齐)',
  ['params/unwrap.yaml', 'provenance.json', 'products/report/methods_draft.md']
    .every((p) => tree.some((f) => f.path === p)));
check('E6 THRESHOLDS 5 项(3 项 PENDING)→ evidenceCeiling 封顶 audited(2)',
  THRESHOLDS.length === 5
  && THRESHOLDS.filter((t) => t.status === 'PENDING').length === 3
  && evidenceCeiling().level === 2);
check('E7 LADDER 六级证据阶梯完整', LADDER.length === 6 && LADDER[5] === 'publishable');

check('E8 figures:IMAGES 4 张(vel/ts/ifg/coh)供画廊演示回落',
  Figures.IMAGES.length === 4
  && JSON.stringify(Figures.IMAGES.map((i) => i.id)) === JSON.stringify(['vel', 'ts', 'ifg', 'coh'])
  && Figures.IMAGES.every((i) => i.name && i.title && i.step));
check('E9 figures:POINTS 3 点 × DATES 7 历元,时序长度对齐',
  Figures.POINTS.length === 3 && Figures.DATES.length === 7
  && Figures.POINTS.every((p) => p.ts.length === Figures.DATES.length));
check('E10 figures:figureSvg/mapSvg/timeSeriesSvg 产出可嵌入 SVG',
  /^<svg/.test(Figures.figureSvg('vel'))
  && /^<svg/.test(Figures.mapSvg(null, { markers: [{ x: 10, y: 10, color: '#f00', label: 'P1' }], note: 'n' }))
  && /^<svg/.test(Figures.timeSeriesSvg({ dates: Figures.DATES, series: [{ name: 'A', ts: Figures.POINTS[0].ts, color: '#f00' }] })));

check('E11 envdata:TERM_LOGS 覆盖全部 11 步(终端面板演示日志)',
  STEP_DEFS.every((d) => Array.isArray(Envdata.TERM_LOGS[d.id]) && Envdata.TERM_LOGS[d.id].length > 3));
check('E12 envdata:TRACE ≥10 条且形状满足轨迹卡(confidence/wall_time/action.tool)',
  Envdata.TRACE.length >= 10
  && Envdata.TRACE.every((e) => typeof e.confidence === 'number'
       && typeof e.wall_time === 'number' && e.action?.tool && e.phase && e.thought));
check('E13 envdata:TRACE 含「失败并恢复」样本(error → recovery.successful)',
  Envdata.TRACE.some((e) => e.error?.occurred)
  && Envdata.TRACE.some((e) => e.recovery?.attempted && e.recovery?.successful));
check('E14 envdata:ENGINES/DISKS/WSL/WORKSPACE/ENV_NOTE 环境演示数据齐备',
  Envdata.ENGINES.length >= 4 && Envdata.DISKS.length >= 1
  && !!Envdata.WSL.text && !!Envdata.WORKSPACE.hint && /静态示意/.test(Envdata.ENV_NOTE));
check('E15 envdata:cmdSh 等价裸命令含 job.rc 落盘与步骤工作目录',
  /job\.rc/.test(Envdata.cmdSh(6, 'snaphu.py --x')) && /06_unwrap/.test(Envdata.cmdSh(6, 'snaphu.py --x')));

/* ============================================================
   F. 源码级回落对齐(dock.js 各 view 的演示标注 + file:// 判据)
   ============================================================ */
console.log('\n== F. 源码级回落对齐 ==');
const dockSrc = readFileSync(new URL('./js/dock.js', import.meta.url), 'utf-8');
check('F1 dock.js:files/audit/env 三面板演示回落都挂 demoBanner',
  /FILES\.demoBanner\(/.test(dockSrc) && /AUD\.demoBanner\(/.test(dockSrc) && /LIVE\.demoBanner\(/.test(dockSrc));
check('F2 dock.js:report 演示回落有「演示数据(未接入真实运行)」标注',
  dockSrc.includes('演示数据（未接入真实运行）'));
check('F3 dock.js:终端/轨迹演示回落有醒目标注',
  dockSrc.includes('以下为离线示意日志') && dockSrc.includes('演示数据 · 后端未接入'));
const fileGuard = (p) => readFileSync(new URL(`./js/${p}`, import.meta.url), 'utf-8')
  .includes("location.protocol === 'file:'");
check('F4 六个取数模块都保留 file:// 判据(envlive/fileslive/auditlive/reportlive/gallery/tspoint)',
  ['envlive.js', 'fileslive.js', 'auditlive.js', 'reportlive.js', 'gallery.js', 'tspoint.js'].every(fileGuard));
check('F5 notify/backend.sse 亦保留 file:// 判据',
  fileGuard('notify.js') && fileGuard('backend.sse.js'));

/* ---------------- 收尾 ---------------- */
console.log(`\n${passed} 项通过${failed ? ` · ${failed} 项失败` : ' · 全部断言通过'}`);
process.exit(failed ? 1 : 0);
