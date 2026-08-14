/* ============================================================
   converse 回退纯逻辑锁(被测:backend.sse.js / backend.mock.js)
   —— B6 前端接线的可测内核,fetch 全程打桩,测试密封离线:
   ① isNoConverse 判定闭集:404/405 = 旧后端缺 /api/converse;
      501(静态文件服务器)/离线/真 5xx 不算 —— 那些归 useMock 回退;
   ② runConverse:POST /api/converse {session, text},NDJSON 逐事件透传
      (消费逻辑与 runTurn 同构,复用同一 ndjson 助手);
   ③ 404 原样上抛:不切演示模式、不吞错 —— 回退决策权在调用方;
   ④ runConversePreferred(app.js submit 的入口):404/405 → 当次回退
      /api/turn 且 S.noConverse 记忆,下回合零探测(「不重复探测」契约);
   ⑤ 离线 → useMock 回退:note 提示 + mock 剧本接管 + onMockActivated
      通知(顶栏「演示」徽章数据源),粘性 —— 后续回合零网络请求;
      离线不得误记 S.noConverse(离线 ≠ 旧后端);
   ⑥ mock 剧本契约:runConverse 织入 agent.cycle 序列(n 递增、动作在
      agentloop.js 闭集内、tool.start/end 成对、say 收尾)—— file://
      离线演示里进度条(agentloop.js)的数据源;runTurn 保持既有序列
      零 agent.cycle(demo-mode.check.mjs 以「mock 事件类型 ⊆ app.js
      case 闭集」锁定,e2e 契约也不允许 app.js 出现该分支)。
   注意:useMock 是模块级粘性状态,⑤ 必须放在依赖真实 fetch 路径的
   用例之后(node:test 同文件内按声明顺序串行执行)。
   ============================================================ */
import './_env.mjs';
import { test } from 'node:test';
import assert from 'node:assert/strict';

// backend.sse.js 顶层读 location.protocol(file:// 判定),node 里先补桩
globalThis.location = { protocol: 'http:', search: '' };

const SSE = await import('../../prototype/js/backend.sse.js');
const { S } = await import('../../prototype/js/state.js');

/* ---------------- fetch 打桩:记录调用,按 url 出响应 ---------------- */

let calls = [];

/** impl(url, body) → Response 最小面;每次 stub 重置调用记录。 */
function stubFetch(impl) {
  calls = [];
  globalThis.fetch = async (url, init) => {
    const body = init?.body ? JSON.parse(init.body) : null;
    calls.push({ url: String(url), body });
    return impl(String(url), body);
  };
}

/** 事件数组 → NDJSON 单块流式响应(覆盖 ndjson 助手用到的 Response 面)。 */
function ndjsonResp(events) {
  const payload = new TextEncoder().encode(
    events.map((e) => JSON.stringify(e)).join('\n') + '\n');
  let sent = false;
  return {
    ok: true, status: 200,
    body: {
      getReader: () => ({
        read: async () => (sent ? { done: true } : (sent = true, { done: false, value: payload })),
        cancel: async () => {},
      }),
    },
  };
}

/** 非 2xx 响应:ndjson 助手据此抛 `HTTP <status>`。 */
const httpErr = (status) => ({ ok: false, status, body: null });

async function collect(iter) {
  const out = [];
  for await (const ev of iter) out.push(ev);
  return out;
}

/* ---------------- ① 判定闭集 ---------------- */

test('isNoConverse:404/405 = 旧后端缺端点;501/离线/5xx/空值不算', () => {
  assert.equal(SSE.isNoConverse(new Error('HTTP 404')), true);
  assert.equal(SSE.isNoConverse(new Error('HTTP 405')), true);
  // 501 是静态文件服务器(无后端)的形状,归 useMock 回退,两层语义不得混淆
  assert.equal(SSE.isNoConverse(new Error('HTTP 501')), false);
  assert.equal(SSE.isNoConverse(new Error('HTTP 500')), false);
  assert.equal(SSE.isNoConverse(new Error('HTTP 503')), false);
  assert.equal(SSE.isNoConverse(new TypeError('Failed to fetch')), false);
  assert.equal(SSE.isNoConverse(null), false);
  assert.equal(SSE.isNoConverse(undefined), false);
});

/* ---------------- ② 正常通路 ---------------- */

test('runConverse:POST /api/converse {session,text},NDJSON 逐事件透传', async () => {
  S.sessionId = 't-conv';
  const script = [
    { t: 'agent.cycle', n: 1, max: 6, action: 'search_data' },
    { t: 'say', parts: ['循环收束'] },
  ];
  stubFetch(() => ndjsonResp(script));
  const evs = await collect(SSE.runConverse('分析同震形变', null));
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/api/converse');
  assert.deepEqual(calls[0].body, { session: 't-conv', text: '分析同震形变' });
  assert.deepEqual(evs, script);
  assert.equal(SSE.isMockActive(), false);
});

/* ---------------- ③ 404 上抛,决策权在调用方 ---------------- */

test('runConverse:404 原样上抛,不切演示模式、自身无记忆(每次仍真发请求)', async () => {
  stubFetch(() => httpErr(404));
  await assert.rejects(collect(SSE.runConverse('x', null)), { message: 'HTTP 404' });
  await assert.rejects(collect(SSE.runConverse('x', null)), { message: 'HTTP 404' });
  assert.equal(calls.length, 2);   // 记忆开关在 runConversePreferred 层,不在这层
  assert.equal(SSE.isMockActive(), false);
});

/* ---------------- ④ 首选入口:回退 + 记忆 ---------------- */

test('runConversePreferred:405 → 当次回退 /api/turn 并记忆,下回合零探测', async () => {
  delete S.noConverse;   // 模块级状态,显式复位保证用例密封
  const turnScript = [{ t: 'say', parts: ['旧后端规划回合'] }];
  stubFetch((url) => (url === '/api/converse' ? httpErr(405) : ndjsonResp(turnScript)));

  const evs = await collect(SSE.runConversePreferred('分析冻土', null));
  assert.deepEqual(calls.map((c) => c.url), ['/api/converse', '/api/turn']);
  assert.deepEqual(evs, turnScript);          // 回退回合的事件一条不丢
  assert.equal(S.noConverse, true);           // 记忆:旧后端无该端点
  assert.equal(SSE.isMockActive(), false);    // 旧后端 ≠ 无后端,不切演示模式

  stubFetch((url) => (url === '/api/converse' ? httpErr(405) : ndjsonResp(turnScript)));
  await collect(SSE.runConversePreferred('再来一回合', null));
  assert.deepEqual(calls.map((c) => c.url), ['/api/turn']);   // 不再探测 converse
});

test('runConversePreferred:真 5xx 原样上抛,不回退、不记忆', async () => {
  delete S.noConverse;
  stubFetch(() => httpErr(503));
  await assert.rejects(collect(SSE.runConversePreferred('x', null)), { message: 'HTTP 503' });
  assert.deepEqual(calls.map((c) => c.url), ['/api/converse']);   // 没有第二跳
  assert.notEqual(S.noConverse, true);
  assert.equal(SSE.isMockActive(), false);
});

/* ---------------- ⑥ mock 剧本契约(独立 import,不碰 useMock) ---------------- */

test('mock 剧本:runConverse 含 agent.cycle 序列,runTurn 保持零周期账', async () => {
  const M = await import('../../prototype/js/backend.mock.js');
  // 剧本内建等待走全局 setTimeout:桩成立即回调,瞬时播完(不动 mock 源码)
  const realSetTimeout = globalThis.setTimeout;
  globalThis.setTimeout = (fn, _ms, ...args) => (fn(...args), 0);
  let turnEvs, convEvs;
  try {
    turnEvs = await collect(M.runTurn('分析 Ridgecrest 同震形变', null));
    convEvs = await collect(M.runConverse('分析 Ridgecrest 同震形变', null));
  } finally {
    globalThis.setTimeout = realSetTimeout;
  }

  // runTurn 序列锁:零 agent.cycle 且首事件 thinking(demo-mode.check.mjs
  // C8/A11 以此锁定 app.js case 闭集,回归会在两处同时红)
  assert.equal(turnEvs.filter((e) => e.t === 'agent.cycle').length, 0);
  assert.equal(turnEvs[0].t, 'thinking');

  const cycles = convEvs.filter((e) => e.t === 'agent.cycle');
  assert.ok(cycles.length >= 3 && cycles.length <= 4, `周期数 ${cycles.length} 应在 3~4`);
  assert.deepEqual(cycles.map((c) => c.n), cycles.map((_, i) => i + 1));   // n 从 1 递增
  assert.ok(cycles.every((c) => Number.isInteger(c.max) && c.max >= cycles.length));
  // 动作闭集与 agentloop.js ACTION_META 对齐(契约 §1)
  const ACTIONS = ['search_data', 'inspect_file', 'check_env', 'list_data',
    'status', 'plan', 'execute', 'set_params', 'set_method', 'thinking'];
  assert.ok(cycles.every((c) => ACTIONS.includes(c.action)),
    `契约外动作:${cycles.map((c) => c.action)}`);

  // 首个周期先于一切内容事件(真实 driver 语义:先记账再干活)
  assert.equal(convEvs[0].t, 'agent.cycle');

  // say 收尾在全部周期之后(正常终止语义)
  const lastCycleIdx = convEvs.findLastIndex((e) => e.t === 'agent.cycle');
  const sayIdx = convEvs.findIndex((e) => e.t === 'say');
  assert.ok(sayIdx > lastCycleIdx, 'say 必须在最后一个周期之后收尾');

  // tool.start / tool.end 成对且 id 对应(进度条的工具耗时数据源)
  const starts = convEvs.filter((e) => e.t === 'tool.start');
  const ends = convEvs.filter((e) => e.t === 'tool.end');
  assert.ok(starts.length > 0);
  assert.equal(starts.length, ends.length);
  assert.ok(starts.every((s) => ends.some((e) => e.id === s.id)));

  // 除周期账外,两端点同一剧本(mock 后端不区分 turn/converse)
  assert.deepEqual(convEvs.filter((e) => e.t !== 'agent.cycle'), turnEvs);
});

/* ---------------- ⑤ 离线 → useMock(粘性,必须最后) ---------------- */

test('离线回退:note 提示 + mock 剧本接管 + onMockActivated 通知,不误记 noConverse', async () => {
  delete S.noConverse;
  let fired = 0;
  const off = SSE.onMockActivated(() => { fired += 1; });
  stubFetch(() => { throw new TypeError('Failed to fetch'); });

  const it = SSE.runConversePreferred('地震演示', null);
  const first = await it.next();               // 切换提示,tone=warn
  assert.equal(first.value.t, 'note');
  assert.equal(first.value.tone, 'warn');
  assert.match(first.value.text, /演示模式/);
  const second = await it.next();              // mock 剧本第一条:agent.cycle n=1
  assert.deepEqual(
    { t: second.value.t, n: second.value.n },
    { t: 'agent.cycle', n: 1 });
  await it.return();                           // 提前收流:不必播完整段剧本

  assert.equal(SSE.isMockActive(), true);      // 回退已发生
  assert.equal(fired, 1);                      // 徽章信号恰好一次
  assert.notEqual(S.noConverse, true);         // 离线 ≠ 旧后端:不误记

  // 粘性:后续回合直接走 mock,零网络请求(不再重复探测后端)
  stubFetch(() => { throw new TypeError('Failed to fetch'); });
  const it2 = SSE.runConverse('再来一回合', null);
  const f2 = await it2.next();
  assert.equal(f2.value.t, 'agent.cycle');     // 直接进剧本,没有 note
  await it2.return();
  assert.equal(calls.length, 0);
  off();
});
