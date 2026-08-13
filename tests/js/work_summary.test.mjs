/* ============================================================
   workSummary / staleSteps / estimateRerun 分类边界锁定
   —— 三类待办语义不同不能混:
     resume  failed/interrupted/orphaned(断点处置续跑)
     stale   曾产出过、指纹变更失效(覆写旧产物)
     pending 从未运行过(首次产出)
   归桶优先级:resume(终态)> stale > pending——失败/中断步骤即使
   叠加脏标记也归 resume,§7.8 的第一动作是断点处置而非覆写重跑。
   all 是三者的升序合并,即审批卡执行列表 rerunSummary().ids 的来源。
   数据源:_registry.mjs 快照 + seedSteps 服务端计划种子(演示种子已删除)。
   ============================================================ */
import './_env.mjs';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as St from '../../prototype/js/state.js';
import {
  S, setRegistry, initSteps, setParams, setStepState, workSummary, staleSteps,
  estimateRerun, st_,
} from '../../prototype/js/state.js';
import { REGISTRY, seedSteps } from './_registry.mjs';

setRegistry(REGISTRY);
const seed5 = () => seedSteps(St, 5);

test('服务端计划「前 5 步 done」基线:6-11 待首跑,无 stale 无 resume(锁返回对象形状)', () => {
  seed5();
  assert.deepEqual(workSummary(), {
    stale: [],
    pending: [6, 7, 8, 9, 10, 11],
    resume: [],
    all: [6, 7, 8, 9, 10, 11],
  });
});

test('边界:全部完成 → 各桶全空;全部未跑 → 全在 pending;无计划 → 四桶皆空', () => {
  seedSteps(St, 11);
  assert.deepEqual(workSummary().all, []);
  seedSteps(St, 0);
  const sum = workSummary();
  assert.deepEqual(sum.pending, [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]);
  assert.deepEqual(sum.all, sum.pending);
  initSteps();   // 无计划(启动态):不再虚构任何待办
  assert.equal(S.steps.size, 0);
  assert.deepEqual(workSummary(), { stale: [], pending: [], resume: [], all: [] });
});

test('改已完成步骤参数:done 链标 stale,pending 保持 pending,分界清晰', () => {
  seed5();
  setParams(3, { esd_coherence_threshold: 0.9 });
  const sum = workSummary();
  assert.deepEqual(sum.stale, [3, 4, 5]);            // 覆写语义
  assert.deepEqual(sum.pending, [6, 7, 8, 9, 10, 11]); // 首次产出语义
  assert.deepEqual(sum.all, [3, 4, 5, 6, 7, 8, 9, 10, 11]);
});

test('failed / interrupted / orphaned 都归 resume(断点处置续跑)', () => {
  for (const bad of ['failed', 'interrupted', 'orphaned']) {
    seed5();
    setStepState(3, bad);
    const sum = workSummary();
    assert.deepEqual(sum.resume, [3], `${bad} 应归 resume`);
    assert.ok(!sum.stale.includes(3) && !sum.pending.includes(3));
  }
});

test('all 跨桶合并且升序;staleSteps() 与 workSummary().all 一致', () => {
  seed5();
  setStepState(2, 'failed');
  setParams(5, { alpha: 0.9 });
  const sum = workSummary();
  assert.deepEqual(sum.resume, [2]);
  assert.deepEqual(sum.stale, [5]);
  assert.deepEqual(sum.all, [2, 5, 6, 7, 8, 9, 10, 11]);
  assert.deepEqual(staleSteps(), sum.all);
});

test('setStepState 直接置 stale 状态 → 归 stale 桶(即使 stale 标记未置)', () => {
  seed5();
  setStepState(6, 'stale');
  assert.equal(st_(6).stale, false);
  assert.ok(workSummary().stale.includes(6));
});

test('setStepState done 清除 stale 标记,步骤离开待办', () => {
  seed5();
  setParams(5, { alpha: 0.9 });
  assert.deepEqual(workSummary().stale, [5]);
  setStepState(5, 'done');
  assert.equal(st_(5).stale, false);
  assert.ok(!workSummary().all.includes(5));
});

test('estimateRerun:注册表不声明示意工期(dur 恒 0)→ 恒返回 0,时长只来自本机历史(§7.5)', () => {
  seed5();
  assert.equal(estimateRerun([1]), 0);
  assert.equal(estimateRerun([6, 7]), 0);
  assert.equal(estimateRerun([]), 0);
  assert.equal(estimateRerun([99]), 0);
});

/* ---------- 2026-08-12 缺陷修复回归:终态优先于 stale 的归桶 ---------- */

test('failed 叠加本地脏标记 → 仍归 resume(终态优先,不被 stale 桶抢走)', () => {
  seed5();
  setParams(3, { esd_coherence_threshold: 0.9 }); // 3/4/5 标脏
  setStepState(3, 'failed');                      // 3 又失败:failed + stale 叠加
  assert.equal(st_(3).stale, true);               // 脏标记确实还在
  const sum = workSummary();
  assert.deepEqual(sum.resume, [3]);   // §7.8:第一动作是断点处置
  assert.deepEqual(sum.stale, [4, 5]); // 纯 stale(done+脏)才提示覆写
  assert.deepEqual(sum.all, [3, 4, 5, 6, 7, 8, 9, 10, 11]);
});

test('interrupted / orphaned 叠加脏标记 → 同样归 resume 不归 stale', () => {
  for (const bad of ['interrupted', 'orphaned']) {
    seed5();
    setParams(3, { esd_coherence_threshold: 0.9 });
    setStepState(3, bad);
    const sum = workSummary();
    assert.deepEqual(sum.resume, [3], `${bad}+stale 应归 resume`);
    assert.ok(!sum.stale.includes(3), `${bad}+stale 不应归 stale 桶`);
  }
});

test('三桶两两不相交,all 无重复且为三桶的升序并集', () => {
  seed5();
  setParams(3, { esd_coherence_threshold: 0.9 }); // 3/4/5 标脏
  setStepState(4, 'failed');                      // 4 → failed+stale 叠加态
  const sum = workSummary();
  const inter = (a, b) => a.filter((x) => b.includes(x));
  assert.deepEqual(inter(sum.stale, sum.resume), []);
  assert.deepEqual(inter(sum.stale, sum.pending), []);
  assert.deepEqual(inter(sum.pending, sum.resume), []);
  assert.equal(new Set(sum.all).size, sum.all.length);
  assert.deepEqual(sum.all,
    [...sum.stale, ...sum.pending, ...sum.resume].sort((a, b) => a - b));
});
