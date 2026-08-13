/* ============================================================
   bridge.js —— 流水线操作 → 聊天流「干预回执」闭环（自初始化模块）

   动机：右侧流水线面板的干预（改方法/参数、重跑、停止）与聊天轨迹流
   看起来各自为政 —— 面板里操作后，聊天流没有对应回执，用户无法确认
   「我的操作到底进没进系统、生效了没有」。本模块补上这条回路，
   不重写任何既有逻辑。

   出站（操作 → 回执卡）：
     - app.js 三处「bridge 接线」调 window.Bridge.noteIntervention
       （方法变更 / 参数变更 / 执行流水线）→ 先落一张本地回执卡；
     - 包装 window.fetch，观察 POST /api/actions、/api/pipeline、
       /api/abort（backend.sse.js 的既有提交点零改造），把提交结果
       推进回执状态机：
         local（已记录·本地）→ pending（提交中）→ queued（已入队 202）
                                        ↘ rejected（被拒绝 4xx/网络错误）
     - window.Bridge.submitAction 是统一提交出口：新调用点一律走它，
       回执卡由同一个 fetch 出口自动生成，无需重复接线。

   入站（服务端 → 回执状态）：
     - 自持一条 SSE（/api/events）只消费 intervention 事件，按
       「步骤号 + 动作关键词」与未决回执对账 → applied（已生效）。
       与 app.js 的全局 SSE 互不干扰（服务端事件总线支持多订阅者）。

   视觉：回执卡右对齐 + 主色色带（用户操作平面），与既有左侧虚线
   .ivt 条目（系统事件平面）形成左右对齐 + 色带双重区分（bridge.css）。
   点击卡片 → 打开右侧面板流水线页签，滚动定位到该步骤并闪烁高亮。

   纯逻辑（回执文案 / 状态机 / 事件对账）全部具名导出，由
   prototype/bridge.check.mjs 做无浏览器断言。
   ============================================================ */
import { h, icon, hhmm } from './dom.js';
import { S, def_ } from './state.js';

/* ============================================================
   纯逻辑层（无 DOM 依赖，可单测）
   ============================================================ */

/** 状态机合法迁移表。queued/applied → pending 对应「同一变更重复提交」
    （syncConfig 每次运行都会重发未落库的差异）：原卡刷新重走一轮，不建新卡。 */
export const FLOW = {
  local: ['pending', 'queued', 'applied', 'rejected'],
  pending: ['queued', 'applied', 'rejected'],
  queued: ['pending', 'applied', 'rejected'],
  applied: ['pending'],
  rejected: ['pending'],
};

/** 状态推进：非法迁移原地不动（如 applied 不可退回 queued）。 */
export function advance(cur, next) {
  return (FLOW[cur] || []).includes(next) ? next : cur;
}

/** 状态 → 人话。run/abort 的 applied 语义不同（开跑 / 受理），单独措辞。 */
export function stateLabel(r) {
  if (r.kind === 'run' && r.state === 'applied') return '已开跑';
  if (r.kind === 'abort' && r.state === 'applied') return '已受理';
  return { local: '已记录 · 本地', pending: '提交中…', queued: '已入队',
           applied: '已生效', rejected: '被拒绝' }[r.state] || r.state;
}

export const DELIVER_ZH = {
  steer: '立即生效', follow_up: '本步结束后生效', next_run: '下次运行生效',
};

/** 步骤号解析：'6' → 6；run 级动作（target 非步骤号）→ null。 */
export function toStepId(target) {
  const n = Number(target);
  return Number.isInteger(n) && String(target).trim() !== '' && n > 0 ? n : null;
}

const defaultStepName = (id) => def_(id)?.name || '';

const fmtVal = (v) => (Array.isArray(v) ? v.join(',') : String(v));

export function fmtParams(params = {}) {
  return Object.entries(params).map(([k, v]) => `${k}=${fmtVal(v)}`).join('、');
}

/** 步骤号列表 → 「第 5–8、10 步」（连续段折叠）。 */
export function fmtIds(ids = []) {
  const a = [...new Set(ids)].map(Number).filter(Number.isFinite).sort((x, y) => x - y);
  if (!a.length) return '';
  const parts = [];
  let s = a[0], e = a[0];
  for (const n of a.slice(1)) {
    if (n === e + 1) { e = n; continue; }
    parts.push(s === e ? `${s}` : `${s}–${e}`);
    s = e = n;
  }
  parts.push(s === e ? `${s}` : `${s}–${e}`);
  return `第 ${parts.join('、')} 步`;
}

/** /api/actions 请求体 → 人话描述（回执卡正文）。nameOf 可注入（单测用）。 */
export function describeAction(body, nameOf = defaultStepName) {
  const sid = toStepId(body?.target);
  const name = sid != null ? nameOf(sid) : '';
  const at = sid != null ? `第 ${sid} 步${name ? `（${name}）` : ''}` : '';
  const p = body?.payload || {};
  switch (body?.action) {
    case 'SET_METHOD': return `${at}方法改为 ${p.method}`;
    case 'SET_PARAMS': return `${at}参数改为 ${fmtParams(p.params || p)}`;
    case 'RESET': return `${at}复位（下游将标脏重跑）`;
    case 'SKIP': return `${at}标记跳过`;
    case 'PAUSE': return '暂停运行（当前步完成后不再启动新步骤）';
    case 'PLAY': return '恢复运行';
    case 'KILL': return '取消当前运行';
    case 'USER_MESSAGE': return `捎话给 Agent：${String(p.text || '').slice(0, 60)}`;
    default: return at ? `${at}执行 ${body?.action || '干预'}` : String(body?.action || '干预动作');
  }
}

/* SSE intervention 事件不带动作 id，只能按「动作关键词 × 步骤号」对账。
   关键词对齐 core/actions.py 的留痕文案（方法改为/参数/复位/跳过/暂停/恢复/取消）。 */
const KIND_HINT = {
  SET_METHOD: /方法改为/,
  SET_PARAMS: /参数/,
  RESET: /复位/,
  SKIP: /跳过/,
  PAUSE: /暂停/,
  PLAY: /恢复/,
  KILL: /取消/,
};
const STEPLESS = new Set(['PAUSE', 'PLAY', 'KILL']);

/** intervention 事件 → 匹配的未决回执（新卡优先）；对不上号返回 null。 */
export function matchIntervention(items, ev) {
  if (ev?.t !== 'intervention') return null;
  const text = String(ev.text || '');
  const affected = Array.isArray(ev.affected) ? ev.affected.map(Number) : [];
  for (const r of [...items].reverse()) {
    if (!['local', 'pending', 'queued'].includes(r.state)) continue;
    const hint = KIND_HINT[r.action];
    if (!hint || !hint.test(text)) continue;
    if (r.stepId != null) {
      if (affected.includes(r.stepId) || text.includes(`第 ${r.stepId} 步`)) return r;
    } else if (STEPLESS.has(r.action)) {
      return r;
    }
  }
  return null;
}

/** 回执台账：数据与状态推进，全部纯内存；DOM 层通过 onNew/onUpdate 挂钩。 */
export class ReceiptLedger {
  constructor({ nameOf } = {}) {
    this.nameOf = nameOf || defaultStepName;
    this.items = [];
    this.seq = 0;
    this.onNew = null;
    this.onUpdate = null;
  }

  _mk(fields) {
    const r = {
      id: `r${++this.seq}`, ts: Date.now(), state: 'local', kind: 'action',
      action: '', stepId: null, ids: null, payloadKey: '', deliver: null,
      reason: '', finalOnAccept: false, text: '', ...fields,
    };
    this.items.push(r);
    this.onNew?.(r);
    return r;
  }

  _to(r, next, patch = {}) {
    r.state = advance(r.state, next);
    Object.assign(r, patch);
    this.onUpdate?.(r);
    return r;
  }

  /** 尚未提交的同步骤同动作旧卡（本地连续编辑合并到一张卡，与 syncConfig
      「一次运行只提交一份差异」的行为对齐，避免提交时账卡对不上）。 */
  _findLocal(action, stepId) {
    return [...this.items].reverse().find(
      (x) => x.state === 'local' && x.action === action && x.stepId === stepId);
  }

  /** 本地干预记录（app.js「bridge 接线」入口）：kind = method | params | run。 */
  note(spec = {}) {
    if (spec.kind === 'method') {
      const payload = { method: spec.value };
      const patch = {
        payloadKey: JSON.stringify(payload), ts: Date.now(),
        text: describeAction({ action: 'SET_METHOD', target: spec.stepId, payload }, this.nameOf),
      };
      const old = this._findLocal('SET_METHOD', spec.stepId);
      if (old) return this._to(old, 'local', patch);   // 连续改同一步：原卡改写
      return this._mk({ kind: 'method', action: 'SET_METHOD', stepId: spec.stepId, ...patch });
    }
    if (spec.kind === 'params') {
      const old = this._findLocal('SET_PARAMS', spec.stepId);
      const merged = { ...(old?.raw || {}), ...(spec.patch || {}) };
      const patch = {
        raw: merged, payloadKey: JSON.stringify({ params: merged }), ts: Date.now(),
        text: describeAction({ action: 'SET_PARAMS', target: spec.stepId,
                               payload: { params: merged } }, this.nameOf),
      };
      if (old) return this._to(old, 'local', patch);
      return this._mk({ kind: 'params', action: 'SET_PARAMS', stepId: spec.stepId, ...patch });
    }
    if (spec.kind === 'run') {
      const ids = spec.ids || [];
      return this._mk({
        kind: 'run', action: 'RUN', ids, stepId: ids[0] ?? null,
        text: `执行流水线：${fmtIds(ids)}（共 ${ids.length} 步）`,
      });
    }
    return null;
  }

  /** 停止请求（/api/abort 出站时建卡）：202 即视为受理，无后续对账。 */
  noteAbort() {
    return this._mk({ kind: 'abort', action: 'ABORT', finalOnAccept: true,
                      text: '停止当前运行（保留断点，可续跑）' });
  }

  /** fetch 出站（/api/actions）：优先复用本地回执 / 同一变更的旧卡，否则建新卡。 */
  beginSubmit(body) {
    const sid = toStepId(body.target);
    const pk = JSON.stringify(body.payload || {});
    const rev = [...this.items].reverse();
    const r = rev.find((x) => x.state === 'local' && x.action === body.action
                              && x.stepId === sid && x.payloadKey === pk)
      || rev.find((x) => x.state === 'local' && x.action === body.action && x.stepId === sid)
      || rev.find((x) => x.action === body.action && x.stepId === sid && x.payloadKey === pk)
      || this._mk({ kind: 'action', action: body.action, stepId: sid, payloadKey: pk,
                    text: describeAction(body, this.nameOf) });
    // 文案以实际提交为准（本地卡与提交体有出入时，如参数改回默认后被 diff 略去）
    return this._to(r, 'pending', {
      payloadKey: pk, deliver: body.deliver_as || null, ts: Date.now(), reason: '',
      text: describeAction(body, this.nameOf),
    });
  }

  /** 提交落定：202 → queued（abort 类直接 applied）；失败 → rejected + 拒因。 */
  settleSubmit(r, { ok, detail = '' } = {}) {
    if (!r) return null;
    if (ok) return this._to(r, r.finalOnAccept ? 'applied' : 'queued');
    return this._to(r, 'rejected', { reason: detail });
  }

  /** 执行请求（/api/pipeline）出站：把最近一张 run 回执推进到 pending。 */
  beginRun() {
    const r = [...this.items].reverse().find(
      (x) => x.kind === 'run' && ['local', 'pending'].includes(x.state));
    return r ? this._to(r, 'pending') : null;
  }

  /** run 回执落定：响应头到达（流开始）即「已开跑」；HTTP 错误 → 被拒绝。 */
  settleRun(r, { ok, detail = '' } = {}) {
    if (!r) return null;
    if (ok) return this._to(r, 'applied');
    return this._to(r, 'rejected', { reason: detail });
  }

  /** SSE intervention 事件对账 → applied。对不上号（非本处发起）返回 null。 */
  applyEvent(ev) {
    const r = matchIntervention(this.items, ev);
    return r ? this._to(r, 'applied', { serverText: String(ev?.text || '') }) : null;
  }
}

/* ============================================================
   DOM 层：回执卡渲染 + 点击定位
   ============================================================ */

/** 点击回执卡 → 打开右侧面板、切流水线页签、滚动定位该步骤并闪烁。
    只走公共 DOM（rail 按钮 / 页签按钮 / data-step 行），不 import dock.js。 */
export function locateStep(stepId, doc = globalThis.document) {
  if (stepId == null || !doc) return;
  const dock = doc.getElementById('dock');
  if (dock?.hidden) doc.getElementById('railDock')?.click();
  doc.getElementById('tab-pipeline')?.click();
  const jump = () => {
    const row = doc.querySelector(`#dock [data-step="${stepId}"]`);
    if (!row) return;
    const still = typeof globalThis.matchMedia === 'function'
      && globalThis.matchMedia('(prefers-reduced-motion: reduce)').matches;
    row.scrollIntoView?.({ block: 'center', behavior: still ? 'auto' : 'smooth' });
    row.classList.remove('flash'); void row.offsetWidth; row.classList.add('flash');
  };
  // 等一帧：页签切换后 dock 才完成该 pane 的渲染
  (globalThis.requestAnimationFrame || ((f) => f()))(jump);
}

function streamInner(doc) {
  return doc?.getElementById('stream')?.querySelector('.stream-inner') || null;
}

function renderCard(doc, r) {
  const inner = streamInner(doc);
  if (!inner) return;   // 轨迹流未挂载（headless / 极早期）：只记账不渲染
  const meta = h('span', { class: 'brg-meta' },
    h('span', { class: 'brg-time' }, hhmm(new Date(r.ts))),
    h('span', { class: 'brg-deliver' }),
    r.stepId != null ? h('span', { class: 'brg-hint' }, '· 点击定位到流水线') : null);
  const main = h('span', { class: 'brg-main' },
    h('span', { class: 'brg-text' }, r.text), meta,
    h('span', { class: 'brg-reason', hidden: true }));
  const body = r.stepId != null
    ? h('button', {
        class: 'brg-body', type: 'button',
        'aria-label': `${r.text}，点击定位到流水线第 ${r.stepId} 步`,
        onclick: () => locateStep(r.stepId, doc),
      }, h('span', { class: 'brg-ico' }, icon('bolt')), main)
    : h('span', { class: 'brg-body' },
        h('span', { class: 'brg-ico' }, icon('bolt')), main);
  const card = h('div', {
    class: 'brg-receipt turn rise', dataset: { state: r.state, rid: r.id },
    role: 'group', 'aria-label': '干预回执',
  }, body, h('span', { class: 'brg-state', role: 'status' }, stateLabel(r)));
  r.el = card;
  inner.appendChild(card);
  // 贴底跟随：仅当用户本就在底部附近（不打断向上翻阅）
  const host = doc.getElementById('stream');
  if (host && host.scrollHeight - host.scrollTop - host.clientHeight < 120) {
    host.scrollTop = host.scrollHeight;
  }
}

function paintCard(r) {
  const el = r.el;
  if (!el) return;
  el.dataset.state = r.state;
  const stateEl = el.querySelector('.brg-state');
  if (stateEl) stateEl.textContent = stateLabel(r);
  const textEl = el.querySelector('.brg-text');
  if (textEl) textEl.textContent = r.text;
  const t = el.querySelector('.brg-time');
  if (t) t.textContent = hhmm(new Date(r.ts));
  const d = el.querySelector('.brg-deliver');
  if (d) d.textContent = r.deliver && DELIVER_ZH[r.deliver] ? `· ${DELIVER_ZH[r.deliver]}` : '';
  const reason = el.querySelector('.brg-reason');
  if (reason) {
    reason.hidden = !r.reason;
    reason.textContent = r.reason ? `拒因：${r.reason}` : '';
  }
}

/* ============================================================
   fetch 出口包装：既有提交点（backend.sse.js）零改造接入回执
   ============================================================ */

function safeParseBody(body) {
  if (typeof body !== 'string') return null;
  try { return JSON.parse(body); } catch { return null; }
}

async function rejectDetail(resp) {
  let detail = `HTTP ${resp.status}`;
  try {
    const j = await resp.clone().json();
    if (j?.detail) detail = String(j.detail).slice(0, 120);
  } catch { /* 响应体非 JSON 或已被消费：保留状态码 */ }
  return detail;
}

function observeFetch(ledger, url, init, promise) {
  const method = String(init?.method || 'GET').toUpperCase();
  if (method !== 'POST') return;
  const path = String(url).replace(/^[a-z]+:\/\/[^/]+/i, '').split('?')[0];
  if (path === '/api/actions') {
    const body = safeParseBody(init?.body);
    if (!body?.action) return;
    const r = ledger.beginSubmit(body);
    promise.then(
      async (resp) => ledger.settleSubmit(r, resp.ok
        ? { ok: true } : { ok: false, detail: await rejectDetail(resp) }),
      (err) => ledger.settleSubmit(r, { ok: false, detail: err?.message || '网络错误' }));
  } else if (path === '/api/pipeline') {
    const r = ledger.beginRun();
    if (!r) return;
    promise.then(
      async (resp) => ledger.settleRun(r, resp.ok
        ? { ok: true } : { ok: false, detail: await rejectDetail(resp) }),
      (err) => ledger.settleRun(r, { ok: false, detail: err?.message || '网络错误' }));
  } else if (path === '/api/abort') {
    const r = ledger.noteAbort();
    ledger._to(r, 'pending');
    promise.then(
      (resp) => ledger.settleSubmit(r, resp.ok
        ? { ok: true } : { ok: false, detail: `HTTP ${resp.status}` }),
      (err) => ledger.settleSubmit(r, { ok: false, detail: err?.message || '网络错误' }));
  }
}

function wrapFetch(bridge) {
  const g = globalThis;
  if (typeof g.fetch !== 'function' || g.fetch.__insarBridge) return;
  const origRef = g.fetch;            // 原始引用：dispose 时原样还原
  const orig = origRef.bind(g);       // 调用副本：原生 fetch 需要 this=window
  const wrapped = function bridgedFetch(input, init) {
    const p = orig(input, init);
    try {
      const url = typeof input === 'string' ? input : (input?.url || '');
      observeFetch(bridge.ledger, url, init, p);
    } catch { /* 观察失败绝不影响原请求 */ }
    return p;
  };
  wrapped.__insarBridge = true;
  wrapped.__insarOrig = origRef;
  g.fetch = wrapped;
  bridge._unwrapFetch = () => { if (g.fetch === wrapped) g.fetch = origRef; };
}

/* ============================================================
   SSE 入站：只消费 intervention 事件做回执对账
   ============================================================ */

function connectSSE(bridge) {
  const ES = globalThis.EventSource;
  if (typeof ES !== 'function') return;
  let fails = 0;
  let timer = null;
  const open = () => {
    let es;
    try {
      es = new ES(`/api/events?session=${encodeURIComponent(S.sessionId)}`);
    } catch { return; }   // file:// 下相对地址无效 → 静默放弃
    bridge._es = es;
    es.onopen = () => { fails = 0; };
    es.onmessage = (e) => {
      if (!e.data) return;
      try {
        const ev = JSON.parse(e.data);
        if (ev?.t === 'intervention') bridge.ledger.applyEvent(ev);
      } catch { /* keepalive 等非 JSON 行忽略 */ }
    };
    es.onerror = () => {
      if (es.readyState !== ES.CLOSED) return;   // 浏览器还在自动重试
      es.close();
      fails += 1;
      // 后端不可达（mock 演示模式）：试 5 次后彻底放弃，不做无谓轮询
      if (fails <= 5) timer = setTimeout(open, Math.min(1000 * 2 ** fails, 15000));
    };
  };
  open();
  bridge._dropSSE = () => { clearTimeout(timer); bridge._es?.close?.(); };
}

/* ============================================================
   Bridge 单例：幂等挂载 + 统一出口
   ============================================================ */

export function createBridge({ doc = globalThis.document } = {}) {
  const ledger = new ReceiptLedger({});
  const bridge = {
    ledger,
    /** app.js「bridge 接线」入口：本地干预 → 回执卡（提交由既有链路完成）。 */
    noteIntervention(spec) {
      try { return ledger.note(spec); } catch { return null; }
    },
    /** 统一提交出口：新调用点走这里；回执卡由 fetch 出口包装自动生成。 */
    submitAction({ scope = 'step', target, action, payload = {}, deliver_as = 'steer' } = {}) {
      return globalThis.fetch('/api/actions', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session: S.sessionId, scope, target: String(target ?? ''),
                               action, payload, deliver_as }),
      });
    },
    dispose() {
      bridge._unwrapFetch?.();
      bridge._dropSSE?.();
      if (globalThis.__insarBridge === bridge) globalThis.__insarBridge = null;
      if (globalThis.Bridge === bridge) globalThis.Bridge = null;
    },
  };
  ledger.onNew = (r) => { try { renderCard(doc, r); } catch { /* 渲染失败只丢卡不丢账 */ } };
  ledger.onUpdate = (r) => { try { paintCard(r); } catch { /* 同上 */ } };
  wrapFetch(bridge);
  connectSSE(bridge);
  return bridge;
}

/** 幂等挂载：重复调用（重复 script 标签 / 热重载）返回同一实例，fetch 不二次包装。 */
export function mountBridge() {
  const g = globalThis;
  if (g.__insarBridge) return g.__insarBridge;
  const bridge = createBridge({});
  g.__insarBridge = bridge;
  g.Bridge = bridge;
  return bridge;
}

/* 自初始化：仅浏览器环境（node 单测环境无 window，跳过，由测试显式挂载） */
if (typeof window !== 'undefined' && typeof document !== 'undefined') {
  const start = () => { try { mountBridge(); } catch { /* 桥挂载失败不影响主应用 */ } };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
}
