/* ============================================================
   真实后端(FastAPI + NDJSON 流)—— backend.mock.js 的对等替身。
   对外接口与 mock 完全一致:Cancel / runTurn / runPipeline / rerunSummary。
   app.js 的 consume() 一行不用改(Phase 5 承诺)。

   协议(api/app.py):
     POST /api/turn      {session, text}            → NDJSON 事件流
     POST /api/pipeline  {session, run_id?, steps?} → NDJSON 事件流
     POST /api/abort     {session}                  → 202(配合 Cancel)
     POST /api/actions   {...}                      → 202(参数/方法变更走干预队列)

   兜底:file:// 打开或后端不可达时,动态退回 backend.mock.js(纯演示)。
   ============================================================ */
import { S, workSummary, estimateRerun, def_ } from './state.js';

const API_BASE = '';
let mockModule = null;
let useMock = location.protocol === 'file:';

async function mock() {
  if (!mockModule) mockModule = await import('./backend.mock.js');
  return mockModule;
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

/** 规划回合:意图 → 探测 → 计划 → 候选决策点。 */
export async function* runTurn(text, token) {
  if (useMock) { const m = await mock(); yield* m.runTurn(text, token); return; }
  try {
    yield* ndjson('/api/turn', { session: S.sessionId, text }, token);
  } catch (err) {
    if (err?.name === 'AbortError') throw new CancelledError();
    if (isOffline(err) || isNoBackend(err)) {
      useMock = true;
      const m = await mock();
      yield { t: 'note', tone: 'warn', text: '后端不可达,已切换到本地演示模式(mock)' };
      yield* m.runTurn(text, token);
      return;
    }
    throw err;
  }
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
      useMock = true;
      const m = await mock();
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
