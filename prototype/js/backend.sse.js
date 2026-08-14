/* ============================================================
   真实后端(FastAPI + NDJSON 流)—— backend.mock.js 的对等替身。
   对外接口与 mock 完全一致:Cancel / runTurn / runConverse / runPipeline /
   rerunSummary。app.js 的 consume() 一行不用改(Phase 5 承诺)。

   协议(api/app.py):
     POST /api/turn      {session, text}            → NDJSON 事件流
     POST /api/converse  {session, text}            → NDJSON 事件流(自主循环,含 agent.cycle)
     POST /api/pipeline  {session, run_id?, step_ids?} → NDJSON 事件流
     POST /api/abort     {session}                  → 202(配合 Cancel;converse 循环同样在周期边界响应)
     POST /api/actions   {...}                      → 202(参数/方法变更走干预队列)

   兜底:file:// 打开或后端不可达时,动态退回 backend.mock.js(纯演示)。
   两层互不冲突的回退语义(runConversePreferred):
     · 404/405 = 旧后端缺 /api/converse → 当次回退 /api/turn,S.noConverse 记忆;
     · 离线/501 = 无后端(静态文件服务器)→ useMock,与 runTurn 同一条回退路。
   ============================================================ */
import { S, workSummary, estimateRerun, def_ } from './state.js';

const API_BASE = '';
let mockModule = null;
let useMock = location.protocol === 'file:';

async function mock() {
  if (!mockModule) mockModule = await import('./backend.mock.js');
  return mockModule;
}

/* ---- mock(演示)模式的对外信号:app.js 顶栏徽章靠它保持诚实语义 ---- */

/** 演示模式是否已激活:file:// 启动即真;后端不可达回退后置真(粘性,不自动复原)。 */
export function isMockActive() { return useMock; }

const mockWatchers = new Set();

/** 订阅「回退到演示模式」的瞬间(file:// 启动态不通知,由启动探活自行发现)。 */
export function onMockActivated(fn) {
  mockWatchers.add(fn);
  return () => mockWatchers.delete(fn);
}

/** 统一的回退落点:置位 + 广播,观察者异常不打断事件流。 */
function activateMock() {
  if (useMock) return;
  useMock = true;
  mockWatchers.forEach((fn) => { try { fn(); } catch { /* 观察者自身的错误不外溢 */ } });
}

/** 协作式取消令牌 —— 接口与 mock 的 Cancel 相同,内部映射 AbortController。 */
export class Cancel {
  constructor() {
    this.flag = false;
    this.controller = new AbortController();
    this.waiters = new Set();
  }
  cancel() {
    this.flag = true;
    this.controller.abort();
    this.waiters.forEach((w) => w());
    // 服务端协作式取消:通知 driver 撤销当前 run(fire-and-forget)
    fetch(`${API_BASE}/api/abort`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session: S.sessionId }),
    }).catch(() => {});
  }
  get cancelled() { return this.flag; }
  throwIfCancelled() { if (this.flag) throw new CancelledError(); }
}
export class CancelledError extends Error {
  constructor() { super('cancelled'); this.name = 'CancelledError'; }
}

/** POST + NDJSON 行流 → 逐事件 yield。半行留存(与后端逐行写出配合)。 */
async function* ndjson(url, body, token) {
  const resp = await fetch(API_BASE + url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal: token?.controller.signal,
  });
  if (!resp.ok || !resp.body) throw new Error(`HTTP ${resp.status}`);
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  try {
    while (true) {
      token?.throwIfCancelled();
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';
      for (const line of lines) {
        if (line.trim()) yield JSON.parse(line);
      }
    }
    if (buffer.trim()) yield JSON.parse(buffer);
  } finally {
    reader.cancel().catch(() => {});
  }
}

function isOffline(err) {
  return err instanceof TypeError || /Failed to fetch|NetworkError/.test(String(err));
}

/* 静态文件服务器（python -m http.server 等）对 POST 返回 404/405/501:
   响应可达但端点不存在 —— 同样视为「无后端」,回退 mock。
   真实后端的 500/502/503 不在此列,仍作为真错误抛出。 */
function isNoBackend(err) {
  return /^HTTP (404|405|501)$/.test(err?.message || '');
}

/** 当次响应是否「旧后端缺 /api/converse」:404(无路由)/405(方法不符)。
    501 不算 —— 那是静态文件服务器(无后端),归 useMock 回退;
    真实后端的 5xx 也不算,仍作为真错误抛出。纯逻辑,单测锁定。 */
export function isNoConverse(err) {
  return /^HTTP (404|405)$/.test(err?.message || '');
}

/** 规划回合:意图 → 探测 → 计划 → 候选决策点。 */
export async function* runTurn(text, token) {
  if (useMock) { const m = await mock(); yield* m.runTurn(text, token); return; }
  try {
    yield* ndjson('/api/turn', { session: S.sessionId, text }, token);
  } catch (err) {
    if (err?.name === 'AbortError') throw new CancelledError();
    if (isOffline(err) || isNoBackend(err)) {
      activateMock();
      const m = await mock();
      yield { t: 'note', tone: 'warn', text: '后端不可达,已切换到本地演示模式(mock)' };
      yield* m.runTurn(text, token);
      return;
    }
    throw err;
  }
}

/** 自主循环回合:POST /api/converse,NDJSON 消费与 runTurn 完全同构(复用 ndjson)。
    404/405(旧后端缺该端点)原样上抛 —— 是否回退 /api/turn 由调用方决策
    (runConversePreferred 记忆 S.noConverse);离线/501(无后端)仍走 useMock 回退。 */
export async function* runConverse(text, token) {
  if (useMock) { const m = await mock(); yield* m.runConverse(text, token); return; }
  try {
    yield* ndjson('/api/converse', { session: S.sessionId, text }, token);
  } catch (err) {
    if (err?.name === 'AbortError') throw new CancelledError();
    if (isNoConverse(err)) throw err;   // 旧后端:交给调用方回退 runTurn,不切演示模式
    if (isOffline(err) || isNoBackend(err)) {
      activateMock();
      const m = await mock();
      yield { t: 'note', tone: 'warn', text: '后端不可达,已切换到本地演示模式(mock)' };
      yield* m.runConverse(text, token);
      return;
    }
    throw err;
  }
}

/**
 * 回合首选入口(app.js submit 消费):优先 /api/converse 自主循环;
 * 旧后端缺端点(404/405)→ 当次回退 /api/turn 并在 S.noConverse 记忆,
 * 后续回合直接走 /api/turn 不再探测。与 useMock 回退互不干扰:
 * 离线/静态服务器在 runConverse/runTurn 内部处理,不会误记 noConverse。
 */
export async function* runConversePreferred(text, token) {
  if (!S.noConverse) {
    try {
      yield* runConverse(text, token);
      return;
    } catch (err) {
      if (!isNoConverse(err)) throw err;
      S.noConverse = true;   // 旧后端无自主循环端点:记住,别每回合白探一次
    }
  }
  yield* runTurn(text, token);
}

/** 执行回合:先把 UI 侧的方法/参数改动同步给服务端(干预队列),再流式执行。 */
export async function* runPipeline(stepIds, token) {
  if (useMock) { const m = await mock(); yield* m.runPipeline(stepIds, token); return; }
  try {
    await syncConfig(stepIds);
    yield* ndjson('/api/pipeline', { session: S.sessionId, step_ids: stepIds }, token);
  } catch (err) {
    if (err?.name === 'AbortError') throw new CancelledError();
    if (isOffline(err) || isNoBackend(err)) {
      activateMock();
      const m = await mock();
      // 与 runTurn/runConverse 同款警示:回退演示后的事件全是假的,必须先声明
      yield { t: 'note', tone: 'warn', text: '后端不可达,已切换到本地演示模式(mock)' };
      yield* m.runPipeline(stepIds, token);
      return;
    }
    throw err;
  }
}

/** UI 本地改过的方法/参数 → 服务端干预队列(steer:执行前生效)。 */
async function syncConfig(stepIds) {
  for (const id of stepIds || []) {
    const st = S.steps.get(id);
    const def = def_(id);
    if (!st || !def) continue;
    if (st.method !== def.method) {
      await fetch(`${API_BASE}/api/actions`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session: S.sessionId, scope: 'step', target: String(id),
                               action: 'SET_METHOD', payload: { method: st.method },
                               deliver_as: 'steer' }),
      });
    }
    const changed = {};
    for (const [k, v] of Object.entries(st.params || {})) {
      if (JSON.stringify(def.params[k]) !== JSON.stringify(v)) changed[k] = v;
    }
    if (Object.keys(changed).length) {
      await fetch(`${API_BASE}/api/actions`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session: S.sessionId, scope: 'step', target: String(id),
                               action: 'SET_PARAMS', payload: { params: changed },
                               deliver_as: 'steer' }),
      });
    }
  }
}

/* ============================================================
   只读查询(GET):失败 / file:// / 后端不可达一律返回 null,
   调用方拿 null 就保持本地估算或演示数据 —— 绝不抛错打断 UI。
   ============================================================ */

async function getJson(path, params) {
  if (useMock) return null;
  try {
    const qs = new URLSearchParams({ session: S.sessionId, ...params });
    const resp = await fetch(`${API_BASE}${path}?${qs}`);
    if (!resp.ok) return null;
    return await resp.json();
  } catch {
    return null;
  }
}

/** 权威影响预估(审批卡数据源):服务端指纹系统对「改这一步」做纯预览。
    返回 { changedStep, reason, affected:[{step_id,reason,state_before}],
           rerunMinutes(null=历史样本不足), rerunBasis } 或 null。 */
export async function fetchImpact(stepId, { method, params } = {}) {
  const q = { step: String(stepId) };
  if (method) q.method = method;
  if (params && Object.keys(params).length) q.params = JSON.stringify(params);
  return getJson('/api/impact', q);
}

/** 服务端状态镜像:{ run, steps:[{id,method,state,stale,...}] } 或 null。
    runId 可选(run 历史切换器接线,runswitch.js):dock 流水线视图显式传入
    以查看历史 run;app.js 的本地镜像同步不传 —— 永远跟随最新 run,
    不受历史切换影响(镜像驱动执行 UI,跟错 run 会引发 contract_broken)。 */
export function fetchState({ runId } = {}) {
  return getJson('/api/state', runId ? { run_id: runId } : {});
}

/** 历史对话:[{role:'user'|'agent', content, created_at, ...}] 或 null。 */
export function fetchChat() {
  return getJson('/api/chat', {});
}

/** 轨迹(OpenDiscoveryTrace 表):[{step_no,phase,action,error_occurred,...}] 或 null。 */
export function fetchTrace() {
  return getJson('/api/trace', {});
}

/** 步骤日志尾部。404(该步无日志)返回 { missing:true };离线/其它失败返回 null。 */
export async function fetchLogs(stepId, { runId, tailKb } = {}) {
  if (useMock) return null;
  try {
    const qs = new URLSearchParams({ session: S.sessionId, step: String(stepId) });
    if (runId) qs.set('run_id', runId);
    if (tailKb) qs.set('tail_kb', String(tailKb));
    const resp = await fetch(`${API_BASE}/api/logs?${qs}`);
    if (resp.status === 404) return { missing: true, text: '' };
    if (!resp.ok) return null;
    return {
      missing: false,
      text: await resp.text(),
      truncated: resp.headers.get('X-Log-Truncated') === '1',
      size: Number(resp.headers.get('X-Log-Size')) || null,
    };
  } catch {
    return null;
  }
}

/* ============================================================
   全局事件通道(SSE /api/events):回合之外的带外事件
   (reattach / intervention / degrade / gate_stop / note)。
   EventSource 自带同连接重试;连接被判死(服务重启、网络断开后部分
   浏览器置 CLOSED 放弃)时手动指数退避重开。
   file:// 或后端不可达:静默返回 no-op 的断开函数。
   ============================================================ */
export function connectEvents(onEvent) {
  if (useMock || typeof EventSource === 'undefined') return () => {};
  let es = null;
  let timer = null;
  let closed = false;
  let retryMs = 1000;

  const open = () => {
    if (closed || useMock) return;   // 期间退回 mock 则不再重连
    try {
      es = new EventSource(`${API_BASE}/api/events?session=${encodeURIComponent(S.sessionId)}`);
    } catch {
      return;   // 环境不支持(如 file:// 下相对地址无效)→ 静默 no-op
    }
    es.onopen = () => { retryMs = 1000; };
    es.onmessage = (e) => {
      if (!e.data) return;
      try { onEvent(JSON.parse(e.data)); } catch { /* 非 JSON 行(keepalive 等)忽略 */ }
    };
    es.onerror = () => {
      if (es.readyState !== EventSource.CLOSED) return;   // 浏览器还在自动重试
      es.close();
      timer = setTimeout(open, retryMs);
      retryMs = Math.min(retryMs * 2, 15000);
    };
  };
  open();
  return () => { closed = true; clearTimeout(timer); es?.close(); };
}

/** 重跑摘要(审批卡)。与 mock 相同:由前端状态镜像即时计算;
    服务端在执行时会用指纹系统重新推导权威影响范围。 */
export function rerunSummary() {
  const { stale, pending, all } = workSummary();
  const overwrite = stale.flatMap((id) => (def_(id).outputs || []).map((o) => o.path));
  return {
    ids: all,
    stale, pending,
    minutes: Math.round(estimateRerun(all) / 60),
    cmds: all.length,
    files: all.flatMap((id) => (def_(id).outputs || []).map((o) => o.path)),
    overwrite,
  };
}
