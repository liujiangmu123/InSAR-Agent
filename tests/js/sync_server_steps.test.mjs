/* ============================================================
   syncServerSteps(GET /api/state 服务端状态镜像)行为锁定
   —— 含 2026-08-12 UI 事故回归:mock 种子状态里 skipped 的 2-6 步
   一度被塞进执行列表。执行列表 rerunSummary().ids 直接取自
   workSummary().all(见 backend.mock.js / backend.sse.js),
   所以在 state 层锁住 workSummary().all 即锁住事故根因。
   ============================================================ */
import './_env.mjs';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  S, STEP_DEFS, initSteps, syncServerSteps, workSummary,
  setParams, st_, on,
} from '../../prototype/js/state.js';

test('skipped 映射为 done 展示(云端 HyP3 完成的步骤)', () => {
  initSteps(0);
  syncServerSteps([{ id: 2, state: 'skipped' }]);
  assert.equal(st_(2).state, 'done');
  assert.equal(st_(2).stale, false);
});

test('回归 2026-08-12:镜像含 skipped 的 2-6 步后,执行列表不得包含它们', () => {
  initSteps(0); // 全 pending,模拟冷启动后首次拉取服务端状态
  // HyP3 路线:1 完成,2-6 云端跳过,7-11 尚未运行
  syncServerSteps([
    { id: 1, state: 'done' },
    { id: 2, state: 'skipped' }, { id: 3, state: 'skipped' },
    { id: 4, state: 'skipped' }, { id: 5, state: 'skipped' },
    { id: 6, state: 'skipped' },
    { id: 7, state: 'pending' }, { id: 8, state: 'pending' },
    { id: 9, state: 'pending' }, { id: 10, state: 'pending' },
    { id: 11, state: 'pending' },
  ]);
  const sum = workSummary();
  for (const id of [1, 2, 3, 4, 5, 6]) {
    assert.ok(!sum.all.includes(id), `步骤 ${id} 不应出现在执行列表`);
  }
  assert.deepEqual(sum.all, [7, 8, 9, 10, 11]);
  assert.deepEqual(sum.stale, []);
  assert.deepEqual(sum.resume, []);
});

test('failed 原样镜像并进入 resume 桶(断点续跑语义)', () => {
  initSteps(5);
  syncServerSteps([{ id: 3, state: 'failed' }]);
  assert.equal(st_(3).state, 'failed');
  const sum = workSummary();
  assert.deepEqual(sum.resume, [3]);
  assert.ok(!sum.stale.includes(3) && !sum.pending.includes(3));
});

test('服务端 stale:true 且 done → 本地展示为 stale', () => {
  initSteps(5);
  syncServerSteps([{ id: 2, state: 'done', stale: true }]);
  assert.equal(st_(2).state, 'stale');
  assert.equal(st_(2).stale, true);
  assert.deepEqual(workSummary().stale, [2]);
});

test('锁现状:failed + stale:true → 状态保持 failed,却归入 stale 桶(stale 判定优先于 resume)', () => {
  initSteps(5);
  syncServerSteps([{ id: 3, state: 'failed', stale: true }]);
  assert.equal(st_(3).state, 'failed');
  assert.equal(st_(3).stale, true);
  const sum = workSummary();
  assert.deepEqual(sum.stale, [3]);   // 注意:不在 resume —— workSummary 先判 stale
  assert.deepEqual(sum.resume, []);
});

test('本地不存在的步骤 id 被跳过,不抛错', () => {
  initSteps(5);
  assert.doesNotThrow(() =>
    syncServerSteps([{ id: 99, state: 'done' }, { id: 6, state: 'running' }]));
  assert.equal(st_(6).state, 'running');
  assert.equal(st_(99), undefined);
});

test('空数组与非数组载荷是 no-op', () => {
  initSteps(5);
  const before = JSON.stringify([...S.steps.values()]);
  syncServerSteps([]);
  syncServerSteps(null);
  syncServerSteps('oops');
  assert.equal(JSON.stringify([...S.steps.values()]), before);
});

test('服务端 method 镜像到本地并重算该步指纹', () => {
  initSteps(5);
  const fpBefore = st_(3).fingerprint;
  syncServerSteps([{ id: 3, state: 'done', method: 'snap_backgeocoding' }]);
  assert.equal(st_(3).method, 'snap_backgeocoding');
  assert.notEqual(st_(3).fingerprint, fpBefore);
});

test('锁现状:服务端未给 stale 字段时,本地 stale 会被复位(与注释「没给的字段不动」不符)', () => {
  initSteps(5);
  setParams(3, { esd_coherence_threshold: 0.9 }); // 本地先把 3/4/5 标脏
  assert.equal(st_(3).state, 'stale');
  syncServerSteps([{ id: 3, state: 'done' }]);    // 服务端只说 done,没提 stale
  assert.equal(st_(3).state, 'done');
  assert.equal(st_(3).stale, false);              // 本地脏标记被抹掉
  assert.ok(!workSummary().all.includes(3));
});

test('method 镜像后按拓扑序重算全部指纹:下游变、上游不变', () => {
  initSteps(5);
  const fps = new Map(STEP_DEFS.map((d) => [d.id, st_(d.id).fingerprint]));
  syncServerSteps([{ id: 3, method: 'snap_backgeocoding' }]);
  for (const id of [1, 2]) {
    assert.equal(st_(id).fingerprint, fps.get(id), `上游 ${id} 指纹不应变化`);
  }
  for (const id of [3, 4, 5, 6, 7, 8, 9, 10, 11]) {
    assert.notEqual(st_(id).fingerprint, fps.get(id), `步骤 ${id} 指纹应级联变化`);
  }
});

test('镜像不做 stale 级联:服务端只标 3,下游 4 保持 done(以服务端为准)', () => {
  initSteps(5);
  syncServerSteps([{ id: 3, state: 'done', stale: true }]);
  assert.equal(st_(3).state, 'stale');
  assert.equal(st_(4).state, 'done'); // 前端镜像不替服务端推导级联
  assert.equal(st_(4).stale, false);
  assert.deepEqual(workSummary().stale, [3]);
});

test('锁现状:服务端条目缺 state 字段 → 本地 state 不变,但 stale 标记被复位(分裂状态)', () => {
  initSteps(5);
  setParams(4, { range_looks: 12 }); // 4/5 标脏
  assert.equal(st_(4).state, 'stale');
  assert.equal(st_(4).stale, true);
  syncServerSteps([{ id: 4 }]);      // 只有 id,什么都没说
  assert.equal(st_(4).state, 'stale');  // state 保住了
  assert.equal(st_(4).stale, false);    // stale 标记却被清了
  assert.ok(workSummary().stale.includes(4)); // 仍按 state 归入 stale 桶
});

test('一次同步在同一 batch 内只广播一次 steps(避免连锁重渲染)', () => {
  initSteps(5);
  let calls = 0;
  let seen = null;
  const off = on('steps', (topics) => { calls += 1; seen = topics; });
  syncServerSteps([{ id: 2, state: 'failed' }, { id: 3, state: 'failed' }]);
  off();
  assert.equal(calls, 1);
  assert.ok(seen.includes('steps') && seen.includes('files'));
});
