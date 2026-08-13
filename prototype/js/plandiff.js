/* ============================================================
   计划 diff 卡 —— 聊天流里的「计划概览 / 计划变更对比」
   (plan-and-execute 预览:让用户一眼看懂「我说的话如何变成/改变了计划」)

   数据事实(调研结论,勿凭字段名想当然):
     · plan 事件本体只带议程 items[{n,text,st}](loop/events.py 的 plan 工厂),
       不携带步骤级计划;步骤级真相 = 本地镜像 S.steps × STEP_DEFS(mock 模式
       即真相),真实后端的权威快照走 GET /api/state(每步带 method/params/
       state/stale,见 backend.sse.js fetchState)。
     · 因此 onPlan 先用本地快照同步渲染(卡片紧跟 planPanel 出现在流里),
       再异步向服务端校准:fetchState 拿到权威 steps 后原位重绘并重存基线
       (mock / file:// / 离线时 fetchState 返回 null,静默保持本地快照)。
     · 若未来 plan 事件扩展出 ev.steps(step_id/capability/method/params/state),
       normalizeSteps 已兼容该形状,事件数据优先于本地快照。

   接线:app.js 的 plan 分支调 window.PlanDiff.onPlan(ev)(拉式,不自听事件,
   避免与 consume() 的双通道去重逻辑打架)。本模块自初始化 + 幂等挂载。

   隔离:上一份计划按会话存 内存 Map + sessionStorage(键含 sessionId);
   「为什么有流水线」一次性说明用 localStorage(跨会话只出现一次)。
   ============================================================ */
import { h, icon } from './dom.js';
import { S, STEP_DEFS, st_, estimateRerunHonest } from './state.js';

const SS_PREFIX = 'ia-plandiff:';            // sessionStorage 键前缀(按会话隔离)
const WHY_KEY = 'ia-plandiff-why-dismissed'; // localStorage:「为什么有流水线」已读标记
const MEM = new Map();                       // sessionId → 上一份计划快照(内存层,storage 不可用时兜底)

/* 「将执行」桶的状态闭集:pending/失效/失败/中断/孤儿/运行中都算待执行段;
   done(且不脏)= 复用缓存;skipped(云端/缓存)= 跳过 —— 与 workSummary 口径一致 */
const RUN_STATES = new Set(['pending', 'stale', 'failed', 'interrupted', 'orphaned', 'running']);

const sessKey = () => S.sessionId || 'default';

/* ---------------- 存取(内存优先,sessionStorage 兜底;异常一律静默) ---------------- */

function ssGet(key) {
  try { return sessionStorage.getItem(key); } catch { return null; }
}
function ssSet(key, val) {
  try { sessionStorage.setItem(key, val); } catch { /* 隐私模式等:内存层仍在 */ }
}
function lsGet(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}
function lsSet(key, val) {
  try { localStorage.setItem(key, val); } catch { /* 同上 */ }
}

/** 读上一份计划快照;内存没有(如页面刷新后)则回落 sessionStorage。 */
export function loadPrev(sess) {
  if (MEM.has(sess)) return MEM.get(sess);
  const raw = ssGet(SS_PREFIX + sess);
  if (!raw) return null;
  try {
    const doc = JSON.parse(raw);
    if (Array.isArray(doc?.steps)) { MEM.set(sess, doc.steps); return doc.steps; }
  } catch { /* 损坏的存档当作没有 */ }
  return null;
}

export function savePrev(sess, snap) {
  MEM.set(sess, snap);
  ssSet(SS_PREFIX + sess, JSON.stringify({ v: 1, steps: snap }));
}

/* ---------------- 计划快照 ---------------- */

/** 本地镜像 → 步骤级快照。S.steps 未初始化的步骤按 STEP_DEFS 缺省(pending)。 */
export function snapshotFromState() {
  return STEP_DEFS.map((d) => {
    const st = st_(d.id);
    return {
      id: d.id, name: d.name,
      method: st?.method ?? d.method,
      params: { ...(st?.params ?? d.params) },
      state: st?.state ?? 'pending',
      stale: !!st?.stale,
    };
  });
}

/** 事件/服务端形状归一:兼容 step_id/capability 命名,name 缺失回落 STEP_DEFS。 */
export function normalizeSteps(list) {
  return list.map((s, i) => {
    const id = s.id ?? s.step_id ?? i + 1;
    return {
      id,
      name: s.name ?? s.capability ?? STEP_DEFS.find((d) => d.id === id)?.name ?? `步骤 ${id}`,
      method: s.method ?? '',
      params: (s.params && typeof s.params === 'object') ? { ...s.params } : {},
      state: s.state || 'pending',
      stale: !!s.stale,
    };
  });
}

/* ---------------- diff 算法 ---------------- */

/** 参数逐键对比(键并集;值按 JSON 序列化比较)→ [{key, from, to}]。 */
export function paramDiff(a = {}, b = {}) {
  const keys = [...new Set([...Object.keys(a), ...Object.keys(b)])].sort();
  const out = [];
  for (const k of keys) {
    if (JSON.stringify(a[k]) !== JSON.stringify(b[k])) {
      out.push({ key: k, from: fmtVal(a[k]), to: fmtVal(b[k]) });
    }
  }
  return out;
}

function fmtVal(v) {
  return v === undefined ? '(无)' : typeof v === 'string' ? v : JSON.stringify(v);
}

/**
 * 两份快照的步骤级 diff。每步可叠加多种变化(kinds):
 *   added   新增步骤       removed 步骤被移除
 *   method  方法变更(带 from/to) params 参数变更(带逐键明细)
 *   rerun   曾完成、现在要重来(prev done → cur 非 done/skipped,失效重跑段)
 *   skipped 转为跳过(云端/缓存复用,prev 非 skipped → cur skipped)
 * 无变化 → 返回 []。
 */
export function diffPlans(prev, cur) {
  const prevById = new Map((prev || []).map((s) => [s.id, s]));
  const curIds = new Set(cur.map((s) => s.id));
  const changes = [];
  for (const s of cur) {
    const p = prevById.get(s.id);
    if (!p) { changes.push({ id: s.id, name: s.name, kinds: ['added'] }); continue; }
    const kinds = [];
    const entry = { id: s.id, name: s.name, kinds };
    if ((p.method || '') !== (s.method || '')) {
      kinds.push('method');
      entry.method = { from: p.method || '(无)', to: s.method || '(无)' };
    }
    const pd = paramDiff(p.params, s.params);
    if (pd.length) { kinds.push('params'); entry.params = pd; }
    if (s.state === 'skipped' && p.state !== 'skipped') {
      kinds.push('skipped');
    } else if (p.state === 'done' && !p.stale && (RUN_STATES.has(s.state) || s.stale)) {
      kinds.push('rerun');
    }
    if (kinds.length) changes.push(entry);
  }
  for (const p of prev || []) {
    if (!curIds.has(p.id)) changes.push({ id: p.id, name: p.name, kinds: ['removed'] });
  }
  changes.sort((a, b) => a.id - b.id);
  return changes;
}

/** 连续段压缩:[7,8,9,10,11] → '7–11';[3,5,6] → '3、5–6'。 */
export function formatRange(ids) {
  const sorted = [...new Set(ids)].sort((a, b) => a - b);
  const parts = [];
  for (let i = 0; i < sorted.length;) {
    let j = i;
    while (j + 1 < sorted.length && sorted[j + 1] === sorted[j] + 1) j++;
    parts.push(j > i ? `${sorted[i]}–${sorted[j]}` : String(sorted[i]));
    i = j + 1;
  }
  return parts.join('、');
}

/** 概览分桶:将执行 / 复用缓存 / 跳过(11 步分组的口径)。 */
export function groupOverview(cur) {
  const run = [], cache = [], skip = [];
  for (const s of cur) {
    if (s.state === 'skipped') skip.push(s.id);
    else if (s.state === 'done' && !s.stale) cache.push(s.id);
    else run.push(s.id);
  }
  return { run, cache, skip };
}

/**
 * 一句人话总结。
 *   首个计划:「共 11 步:6 步将执行,5 步复用缓存」
 *   重规划: 「相比上次:第 6 步方法 snaphu_mcf→icu,第 7–11 步因此重跑」
 * 重跑段的「因此」只有在其上游确有方法/参数/新增变化时才说;
 * 否则如实写「失效重跑」(不编因果)。
 */
export function summarize(changes, cur, { first = false } = {}) {
  if (first) {
    const g = groupOverview(cur);
    const parts = [`${g.run.length} 步将执行`, `${g.cache.length} 步复用缓存`];
    if (g.skip.length) parts.push(`${g.skip.length} 步跳过`);
    return `共 ${cur.length} 步:${parts.join(',')}`;
  }
  if (!changes.length) return '相比上次:计划无变化';
  const has = (c, k) => c.kinds.includes(k);
  const methodCh = changes.filter((c) => has(c, 'method'));
  const paramCh = changes.filter((c) => has(c, 'params') && !has(c, 'method'));
  const added = changes.filter((c) => has(c, 'added')).map((c) => c.id);
  const removed = changes.filter((c) => has(c, 'removed')).map((c) => c.id);
  const skipped = changes.filter((c) => has(c, 'skipped')).map((c) => c.id);
  // 重跑段总结只列「纯重跑」的步(方法/参数变更行已各自成句,不重复点名)
  const edited = new Set([...methodCh, ...paramCh].map((c) => c.id));
  const rerun = changes.filter((c) => has(c, 'rerun') && !edited.has(c.id)).map((c) => c.id);

  const clauses = [];
  for (const c of methodCh) clauses.push(`第 ${c.id} 步方法 ${c.method.from}→${c.method.to}`);
  for (const c of paramCh) {
    clauses.push(c.params.length === 1
      ? `第 ${c.id} 步参数 ${c.params[0].key} ${c.params[0].from}→${c.params[0].to}`
      : `第 ${c.id} 步 ${c.params.length} 项参数调整`);
  }
  if (added.length) clauses.push(`新增第 ${formatRange(added)} 步`);
  if (rerun.length) {
    const causal = [...edited, ...added].some((id) => id < Math.min(...rerun));
    clauses.push(`第 ${formatRange(rerun)} 步${causal ? '因此' : '失效'}重跑`);
  }
  if (skipped.length) clauses.push(`第 ${formatRange(skipped)} 步转为跳过(复用缓存)`);
  if (removed.length) clauses.push(`移除第 ${formatRange(removed)} 步`);
  return `相比上次:${clauses.join(',')}`;
}

/* ---------------- 预计总时长 ---------------- */

/** 事件里带了就用事件的;否则给诚实估算(§7.5:有本机历史才给区间,没有就说未知)。 */
function etaLabel(ev, cur) {
  if (ev?.etaLabel) return String(ev.etaLabel);
  if (Number.isFinite(ev?.etaMinutes)) return `预计约 ${Math.round(ev.etaMinutes)} 分钟`;
  const { run } = groupOverview(cur);
  return estimateRerunHonest(run).label;
}

/* ---------------- 流水线定位(点击行 → 滚动到 dock 对应步骤) ---------------- */

/** 有 dock 的真实页走 setTab('pipeline') 再定位;静态/测试环境直接在文档里找行。 */
export function gotoPipelineStep(stepId) {
  S.selectedStep = stepId;
  const dockEl = document.getElementById('dock');
  if (dockEl?.hidden) document.getElementById('railDock')?.click();   // dock 收起时先展开
  const jump = () => {
    const scope = document.getElementById('dockBody') || document.body;
    const row = [...scope.querySelectorAll('.pstep')]
      .find((r) => String(r.dataset?.step) === String(stepId));
    if (!row) return;
    const reduced = typeof matchMedia === 'function'
      && matchMedia('(prefers-reduced-motion: reduce)').matches;
    row.scrollIntoView({ block: 'center', behavior: reduced ? 'auto' : 'smooth' });
    row.classList.remove('flash'); void row.offsetWidth; row.classList.add('flash');
  };
  if (document.getElementById('dockTabs')) {
    // feature-detect(stream.js jumpArtifact 同款):仅真实应用页动态加载 dock.js
    import('./dock.js').then((D) => { D.setTab('pipeline'); jump(); }).catch(jump);
  } else {
    jump();
  }
}

/* ---------------- 卡片渲染 ---------------- */

const TAG_TXT = {
  added: '新增', removed: '移除', method: '方法变更', params: '参数变更',
  rerun: '重跑', skipped: '转为跳过',
};
const GROUP_TAG = { run: ['k-run', '将执行'], cache: ['k-cache', '复用缓存'], skip: ['k-skip', '跳过'] };

function groupOf(s) {
  if (s.state === 'skipped') return 'skip';
  if (s.state === 'done' && !s.stale) return 'cache';
  return 'run';
}

function tag(kind) {
  return h('span', { class: `pd-tag k-${kind}` }, TAG_TXT[kind] || kind);
}

/** 「为什么有流水线」一次性说明:已读(localStorage)后所有计划卡不再渲染。 */
function whyBox() {
  if (lsGet(WHY_KEY) === '1') return null;
  const box = h('details', { class: 'pd-why' },
    h('summary', null,
      h('span', { class: 'cv' }, icon('chevron')),
      h('span', { class: 'why-line' },
        '为什么聊天下面还有一条流水线?聊天负责表达意图,流水线是可核查的执行状态——每步参数、证据、日志都可追溯。')),
    h('div', { class: 'why-bd' },
      h('p', null, '你说的话会被解析成右侧流水线上的具体步骤:每一步用什么方法、什么参数都是明摆的,不是黑盒执行。'),
      h('p', null, '每一步的运行状态、产物指纹与日志都留在流水线里,随时可以点开核查;出了问题能定位到具体某一步,而不是从头猜。'),
      h('p', null, '改一句话或一个参数,受影响的步骤会被自动标记失效并只重跑那一段——这正是本卡「变化徽章」的来源。'),
      h('button', {
        class: 'btn btn-gho btn-sm', type: 'button',
        'aria-label': '知道了,不再显示这条说明',
        onclick: () => { lsSet(WHY_KEY, '1'); box.remove(); },
      }, '知道了,不再显示')));
  return box;
}

function stepRow(s, change, first) {
  const kinds = change?.kinds || [];
  const changed = kinds.length > 0;
  const badges = first
    ? [h('span', { class: `pd-tag ${GROUP_TAG[groupOf(s)][0]}` }, GROUP_TAG[groupOf(s)][1])]
    : kinds.map(tag);
  const methodCell = kinds.includes('method')
    ? [h('s', { class: 'pd-old' }, change.method.from), ' → ', h('b', { class: 'pd-new' }, change.method.to)]
    : [s.method];
  const paramNote = kinds.includes('params')
    ? h('div', { class: 'pd-sub' },
        change.params.map((p) => `${p.key}: ${p.from} → ${p.to}`).join(' · '))
    : null;
  return h('tr', {
    class: `pd-row${changed && !first ? ' is-changed' : ''}`,
    tabindex: '0', role: 'button',
    'aria-label': `第 ${s.id} 步 ${s.name}:定位到流水线对应步骤`,
    onclick: () => gotoPipelineStep(s.id),
    onkeydown: (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); gotoPipelineStep(s.id); }
    },
  },
    h('td', { class: 'no' }, String(s.id)),
    h('td', { class: 'nm' }, s.name, paramNote),
    h('td', { class: 'mth' }, ...methodCell),
    h('td', { class: 'chg' }, ...badges));
}

/** 被移除的步骤单独一行(只在 diff 卡出现,不可点击 —— 流水线上已无此步)。 */
function removedRow(c) {
  return h('tr', { class: 'pd-row is-removed' },
    h('td', { class: 'no' }, String(c.id)),
    h('td', { class: 'nm' }, c.name || `步骤 ${c.id}`),
    h('td', { class: 'mth' }, '—'),
    h('td', { class: 'chg' }, tag('removed')));
}

function buildModel({ first, changes, cur, ev, sess }) {
  return {
    first, changes, cur, sess,
    eta: etaLabel(ev, cur),
    summary: summarize(changes, cur, { first }),
  };
}

function buildCardParts(model) {
  const { first, changes, cur, eta, summary } = model;
  const byId = new Map(changes.map((c) => [c.id, c]));
  const removed = changes.filter((c) => c.kinds.includes('removed'));
  const title = first
    ? `执行计划 · ${cur.length} 步`
    : `计划更新 · ${changes.length} 处变化`;
  return [
    h('div', { class: 'hd' }, icon('list'), title,
      h('span', { class: 'grow' }),
      eta ? h('span', { class: 'sub' }, eta) : null),
    h('p', { class: 'pd-sum' }, summary),
    h('div', { class: 'bd' },
      h('table', { class: 'pd-table' },
        h('thead', null, h('tr', null,
          h('th', { scope: 'col' }, '#'),
          h('th', { scope: 'col' }, '能力'),
          h('th', { scope: 'col' }, '方法'),
          h('th', { scope: 'col' }, '变化'))),
        h('tbody', null,
          ...cur.map((s) => stepRow(s, byId.get(s.id), first)),
          ...removed.map(removedRow))),
      h('p', { class: 'pd-hint' }, '点击行可定位到右侧流水线对应步骤')),
    whyBox(),
  ].filter(Boolean);
}

/* ---------------- 流插入 / 原位重绘 ---------------- */

// 最近一次 onPlan 的渲染上下文:服务端校准回来后按它决定原位重绘还是补插卡片
let lastPlan = null;   // { sess, prev, first, node }

function streamHost() {
  return document.getElementById('stream')?.querySelector('.stream-inner') || null;
}

function insertCard(model) {
  const host = streamHost();
  if (!host) return null;
  const node = h('div', {
    class: 'plandiff turn rise', role: 'group',
    'aria-label': `计划卡:${model.summary}`,
  }, ...buildCardParts(model));
  host.appendChild(node);
  // 跟随滚动:仅当用户本就贴底(与 stream.js 的 60px 阈值一致),不打断上翻阅读
  const sc = document.getElementById('stream');
  if (sc && sc.scrollHeight - sc.scrollTop - sc.clientHeight < 60) sc.scrollTop = sc.scrollHeight;
  return node;
}

function repaint(node, model) {
  node.setAttribute('aria-label', `计划卡:${model.summary}`);
  node.replaceChildren(...buildCardParts(model));
  return node;
}

/** 服务端权威快照异步校准:重 diff、原位重绘、重存基线。离线/mock 静默跳过。 */
function refreshFromServer(sess, ev) {
  import('./backend.sse.js')
    .then((API) => API.fetchState())
    .then((state) => {
      if (!state?.steps?.length) return;
      if (sess !== sessKey()) return;                    // 期间换了会话:丢弃过期校准
      if (!lastPlan || lastPlan.sess !== sess) return;
      const cur = normalizeSteps(state.steps);
      const changes = lastPlan.first ? [] : diffPlans(lastPlan.prev, cur);
      savePrev(sess, cur);
      const model = buildModel({ first: lastPlan.first, changes, cur, ev, sess });
      if (lastPlan.node?.isConnected) repaint(lastPlan.node, model);
      else if (!lastPlan.first && changes.length) lastPlan.node = insertCard(model);
    })
    .catch(() => { /* file:// / node 校验环境 / 后端不可达:保持本地快照 */ });
}

/* ---------------- 入口 ---------------- */

/**
 * plan 事件入口(app.js 的 plan 分支拉式调用)。
 * 首个计划 → 概览卡;重规划 → diff 卡;与上份快照完全一致 → 不渲染
 * (turn 复用既有 run 时每回合都会重发 plan 事件,不能刷屏)。
 */
export function onPlan(ev) {
  if (!ev || ev.t !== 'plan') return null;
  const sess = sessKey();
  const cur = Array.isArray(ev.steps) && ev.steps.length
    ? normalizeSteps(ev.steps)
    : snapshotFromState();
  const prev = loadPrev(sess);
  const changes = prev ? diffPlans(prev, cur) : [];
  savePrev(sess, cur);
  const first = !prev;
  let node = null;
  if (first || changes.length) {
    node = insertCard(buildModel({ first, changes, cur, ev, sess }));
  }
  lastPlan = { sess, prev, first, node };
  refreshFromServer(sess, ev);
  return node;
}

/* ---------------- 自初始化(幂等挂载) ---------------- */

/** 全局挂载:重复 import / 重复 <script> 不覆盖已有实例(防重复挂载)。 */
export function install() {
  const g = typeof window !== 'undefined' ? window : globalThis;
  if (g.PlanDiff?.__installed) return g.PlanDiff;
  g.PlanDiff = { __installed: true, onPlan, gotoPipelineStep };
  return g.PlanDiff;
}

install();
