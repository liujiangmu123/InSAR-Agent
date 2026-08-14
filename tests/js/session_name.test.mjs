/* ============================================================
   会话显示名兜底（state.js sessionDisplayName / setSessions，P2-16）
   —— 浏览器实测:曾有以字符串 "null" 为 id 物化的会话,侧栏与副标题
   字面渲染 "null"。锁定:name 缺失/空白退回 id;字面 "null"/"undefined"
   按插值事故处置;两者都无信息时显示「未命名会话」。
   ============================================================ */
import './_env.mjs';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { SESSIONS, sessionDisplayName, setSessions } from '../../prototype/js/state.js';

test('sessionDisplayName:正常名原样;空名退回 id', () => {
  assert.equal(sessionDisplayName('Ridgecrest 同震', 's-1'), 'Ridgecrest 同震');
  assert.equal(sessionDisplayName(null, 's-1'), 's-1');
  assert.equal(sessionDisplayName('   ', 's-1'), 's-1');
  assert.equal(sessionDisplayName(undefined, 's-1'), 's-1');
});

test('sessionDisplayName:字面 "null"/"undefined" 按空名处置(插值事故)', () => {
  // 实测形态:session_id 与 name 都是字符串 "null" —— 侧栏不得原样渲染
  assert.equal(sessionDisplayName('null', 'null'), '未命名会话');
  assert.equal(sessionDisplayName('undefined', 's-2'), 's-2');
  assert.equal(sessionDisplayName(null, 'null'), '未命名会话');
});

test('setSessions:兜底贯通到侧栏条目(渲染点消费的 name 字段)', () => {
  setSessions([
    { session_id: 's-a', name: '青藏冻土', mode: 'expert', created_at: 0 },
    { session_id: 's-b', name: null, mode: 'expert', created_at: 0 },
    { session_id: 'null', name: 'null', mode: 'expert', created_at: 0 },
  ]);
  assert.deepEqual(SESSIONS.map((s) => s.name), ['青藏冻土', 's-b', '未命名会话']);
});
