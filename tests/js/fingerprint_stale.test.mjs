/* ============================================================
   指纹镜像 + 失效级联(setMethod / setParams / invalidate)锁定
   —— STALE 由依赖图推导而非硬编码;指纹含上游指纹,
   任何一步方法/参数变更都应级联改写全部下游指纹。
   ============================================================ */
import './_env.mjs';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  STEP_DEFS, initSteps, setMethod, setParams, setStepState,
  snapshotSteps, restoreSteps, downstreamOf, fingerprint,
  st_, workSummary,
} from '../../prototype/js/state.js';

test('指纹格式 4+4 hex,同配置下确定复现', () => {
  initSteps(5);
  assert.match(st_(6).fingerprint, /^[0-9a-f]{4}…[0-9a-f]{4}$/);
  assert.equal(fingerprint(6), st_(6).fingerprint); // 现算与缓存一致
  const fp6 = st_(6).fingerprint;
  initSteps(5); // 重新初始化 → 同配置同指纹
  assert.equal(st_(6).fingerprint, fp6);
});

test('未知步骤:指纹给占位符,setMethod/setParams 返回空数组', () => {
  initSteps(5);
  assert.equal(fingerprint(99), '········');
  assert.deepEqual(setMethod(99, 'x'), []);
  assert.deepEqual(setParams(99, { a: 1 }), []);
});

test('setMethod 级联:自身与全部下游指纹变化,上游不变', () => {
  initSteps(5);
  const fps = new Map(STEP_DEFS.map((d) => [d.id, st_(d.id).fingerprint]));
  setMethod(5, 'boxcar');
  for (const id of [1, 2, 3, 4]) {
    assert.equal(st_(id).fingerprint, fps.get(id), `上游 ${id} 指纹不应变化`);
  }
  for (const id of [5, 6, 7, 8, 9, 10, 11]) {
    assert.notEqual(st_(id).fingerprint, fps.get(id), `步骤 ${id} 指纹应级联变化`);
  }
});

test('setMethod 返回受影响 id(自身+下游);重复设同方法幂等返回空', () => {
  initSteps(5);
  assert.deepEqual(setMethod(5, 'boxcar'), [5, 6, 7, 8, 9, 10, 11]);
  assert.deepEqual(setMethod(5, 'boxcar'), []); // 方法未变 → 不传播
});

test('setParams 等值 patch 幂等:不传播、不标脏', () => {
  initSteps(5);
  assert.deepEqual(setParams(5, { alpha: 0.4 }), []); // 与默认值相同
  assert.deepEqual(setParams(5, {}), []);
  assert.equal(st_(5).state, 'done');
  assert.equal(st_(5).stale, false);
});

test('级联只把「曾经完成」的标 stale,pending 保持 pending', () => {
  initSteps(5);
  setMethod(5, 'boxcar');
  assert.equal(st_(5).state, 'stale');
  assert.equal(st_(5).stale, true);
  for (const id of [6, 7, 8, 9, 10, 11]) {
    assert.equal(st_(id).state, 'pending', `步骤 ${id} 应保持 pending`);
    assert.equal(st_(id).stale, false);
  }
  assert.deepEqual(workSummary().stale, [5]);
});

test('参数改回原值:指纹复原,但状态仍是 stale(前端镜像不做指纹比对自动恢复)', () => {
  initSteps(5);
  const fp5 = st_(5).fingerprint;
  const fp11 = st_(11).fingerprint;
  setParams(5, { alpha: 0.9 });
  assert.notEqual(st_(5).fingerprint, fp5);
  setParams(5, { alpha: 0.4 });
  assert.equal(st_(5).fingerprint, fp5);   // 指纹往返复原
  assert.equal(st_(11).fingerprint, fp11); // 下游指纹同样复原
  assert.equal(st_(5).state, 'stale');     // 锁现状:恢复 done 需真正重跑或服务端镜像
});

test('downstreamOf 沿依赖图 BFS:不含自身,升序,菱形依赖去重(11 依赖 10 和 7)', () => {
  assert.deepEqual(downstreamOf(1), [2, 3, 4, 5, 6, 7, 8, 9, 10, 11]);
  assert.deepEqual(downstreamOf(7), [8, 9, 10, 11]); // 11 只出现一次
  assert.deepEqual(downstreamOf(10), [11]);
  assert.deepEqual(downstreamOf(11), []);
});

test('snapshot/restore 全量回滚:状态/方法/参数/stale/指纹', () => {
  initSteps(5);
  const fpsBefore = STEP_DEFS.map((d) => st_(d.id).fingerprint);
  const snap = snapshotSteps();
  setMethod(5, 'boxcar');
  setParams(6, { threads: 16 });
  setStepState(2, 'failed');
  restoreSteps(snap);
  assert.equal(st_(5).method, 'goldstein');
  assert.equal(st_(5).state, 'done');
  assert.equal(st_(5).stale, false);
  assert.equal(st_(6).params.threads, 8);
  assert.equal(st_(2).state, 'done');
  assert.deepEqual(STEP_DEFS.map((d) => st_(d.id).fingerprint), fpsBefore);
  assert.deepEqual(workSummary().all, [6, 7, 8, 9, 10, 11]);
});

test('设计决策:running 步骤不被级联标 stale(上游变更时保持 running,失效裁决交给服务端)', () => {
  // 正在运行的步骤是否作废由服务端裁决(执行器接回或复位),前端镜像不抢跑,
  // 避免与服务端状态机打架——见 state.js invalidate 的设计决策注释。
  initSteps(5);
  setStepState(6, 'running');
  setParams(5, { alpha: 0.9 }); // 6 在受影响范围内
  assert.equal(st_(6).state, 'running');
  assert.equal(st_(6).stale, false);
  assert.ok(!workSummary().all.includes(6)); // running 不属于任何待办桶
});
