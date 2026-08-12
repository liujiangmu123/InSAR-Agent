/* ============================================================
   workSummary / staleSteps / estimateRerun 分类边界锁定
   —— 三类待办语义不同不能混:
     stale   曾产出过、指纹变更失效(覆写旧产物)
     pending 从未运行过(首次产出)
     resume  failed/interrupted/orphaned(断点处置续跑)
   all 是三者的升序合并,即审批卡执行列表 rerunSummary().ids 的来源。
   ============================================================ */
import './_env.mjs';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  initSteps, setParams, setStepState, workSummary, staleSteps,
  estimateRerun, st_,
} from '../../prototype/js/state.js';

test('initSteps(5) 基线:6-11 待首跑,无 stale 无 resume(锁返回对象形状)', () => {
  initSteps(5);
  assert.deepEqual(workSummary(), {
    stale: [],
    pending: [6, 7, 8, 9, 10, 11],
    resume: [],
    all: [6, 7, 8, 9, 10, 11],
  });
});

test('边界:全部完成 → 各桶全空;全部未跑 → 全在 pending', () => {
  initSteps(11);
  assert.deepEqual(workSummary().all, []);
  initSteps(0);
  const sum = workSummary();
  assert.deepEqual(sum.pending, [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]);
  assert.deepEqual(sum.all, sum.pending);
});

test('改已完成步骤参数:done 链标 stale,pending 保持 pending,分界清晰', () => {
  initSteps(5);
  setParams(3, { esd_coherence_threshold: 0.9 });
  const sum = workSummary();
  assert.deepEqual(sum.stale, [3, 4, 5]);            // 覆写语义
  assert.deepEqual(sum.pending, [6, 7, 8, 9, 10, 11]); // 首次产出语义
  assert.deepEqual(sum.all, [3, 4, 5, 6, 7, 8, 9, 10, 11]);
});

test('failed / interrupted / orphaned 都归 resume(断点处置续跑)', () => {
  for (const bad of ['failed', 'interrupted', 'orphaned']) {
    initSteps(5);
    setStepState(3, bad);
    const sum = workSummary();
    assert.deepEqual(sum.resume, [3], `${bad} 应归 resume`);
    assert.ok(!sum.stale.includes(3) && !sum.pending.includes(3));
  }
});

test('all 跨桶合并且升序;staleSteps() 与 workSummary().all 一致', () => {
  initSteps(5);
  setStepState(2, 'failed');
  setParams(5, { alpha: 0.9 });
  const sum = workSummary();
  assert.deepEqual(sum.resume, [2]);
  assert.deepEqual(sum.stale, [5]);
  assert.deepEqual(sum.all, [2, 5, 6, 7, 8, 9, 10, 11]);
  assert.deepEqual(staleSteps(), sum.all);
});

test('setStepState 直接置 stale 状态 → 归 stale 桶(即使 stale 标记未置)', () => {
  initSteps(5);
  setStepState(6, 'stale');
  assert.equal(st_(6).stale, false);
  assert.ok(workSummary().stale.includes(6));
});

test('setStepState done 清除 stale 标记,步骤离开待办', () => {
  initSteps(5);
  setParams(5, { alpha: 0.9 });
  assert.deepEqual(workSummary().stale, [5]);
  setStepState(5, 'done');
  assert.equal(st_(5).stale, false);
  assert.ok(!workSummary().all.includes(5));
});

test('estimateRerun 按步骤示意工期求和;空列表为 0;未知 id 计 0', () => {
  initSteps(5);
  assert.equal(estimateRerun([1]), 360);
  assert.equal(estimateRerun([6, 7]), 1440 + 2280);
  assert.equal(estimateRerun([]), 0);
  assert.equal(estimateRerun([99]), 0);
});
