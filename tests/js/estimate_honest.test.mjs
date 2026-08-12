/* ============================================================
   estimateRerunHonest(诚实时长预估,§7.5)两分支锁定
   —— 历史样本 ≥3 → known:true 给区间;<3 → 如实说「时长未知」。
   历史键 = `${stepId}:${fingerprint}`,同配置才算同历史。
   注意:RUN_HISTORY 是模块级不可清空,本文件用例按声明顺序执行,
   各用例用不同 stepId / 参数配置错开历史键,避免相互污染。
   Math.random 固定为 0.5 → 抖动因子 1.05,样本值确定可断言。
   ============================================================ */
import './_env.mjs';
import { test, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import {
  initSteps, setParams, recordRunSample, estimateRerunHonest,
} from '../../prototype/js/state.js';

const realRandom = Math.random;
beforeEach(() => {
  initSteps(5);
  Math.random = () => 0.5; // dur × (0.85 + 0.5×0.4) = dur × 1.05
});
afterEach(() => { Math.random = realRandom; });

test('空列表 / 未传参 → 无待运行步骤', () => {
  assert.deepEqual(estimateRerunHonest([]),
    { known: false, samples: 0, label: '无待运行步骤' });
  assert.equal(estimateRerunHonest(undefined).label, '无待运行步骤');
});

test('无任何历史 → 时长未知(首次运行此配置)', () => {
  assert.deepEqual(estimateRerunHonest([11]),
    { known: false, samples: 0, label: '时长未知（首次运行此配置）' });
});

test('样本不足 3 次 → 仍未知,如实报告样本数', () => {
  recordRunSample(10);
  recordRunSample(10);
  const r = estimateRerunHonest([10]);
  assert.equal(r.known, false);
  assert.equal(r.samples, 2);
  assert.equal(r.label, '时长未知（首次运行此配置）');
});

test('历史样本 ≥3 → known,给出分钟区间文案', () => {
  for (let i = 0; i < 3; i++) recordRunSample(7);
  const r = estimateRerunHonest([7]);
  assert.equal(r.known, true);
  assert.equal(r.samples, 3);
  assert.equal(r.loSec, 2394); // 2280 × 1.05
  assert.equal(r.hiSec, 2394);
  assert.equal(r.label, '约 40–40 分钟（基于本机历史 3 次运行）');
});

test('同配置有历史 → known;改参数(新配置)→ 回到未知分支;改回 → 历史重新命中', () => {
  setParams(7, { max_temporal_baseline: 240 });
  for (let i = 0; i < 3; i++) recordRunSample(7);
  assert.equal(estimateRerunHonest([7]).known, true);
  setParams(7, { max_temporal_baseline: 90 }); // 指纹变 → 历史键不再命中
  const r = estimateRerunHonest([7]);
  assert.equal(r.known, false);
  assert.equal(r.samples, 0);
  assert.equal(r.label, '时长未知（首次运行此配置）');
  setParams(7, { max_temporal_baseline: 240 }); // 改回旧配置 → 同指纹 → 历史找回
  assert.equal(estimateRerunHonest([7]).known, true);
});

test('合计上界 ≥5400 秒 → 切换为小时文案', () => {
  for (let i = 0; i < 3; i++) {
    recordRunSample(4); // 3120 × 1.05 = 3276
    recordRunSample(7); // 2280 × 1.05 = 2394
  }
  const r = estimateRerunHonest([4, 7]);
  assert.equal(r.known, true);
  assert.equal(r.loSec, 5670); // 3276 + 2394
  assert.equal(r.hiSec, 5670);
  assert.equal(r.label, '约 1.6–1.6 小时（基于本机历史 3 次运行）');
});

test('多步骤里任一无历史 → 整体未知(样本数取各步最小值)', () => {
  for (let i = 0; i < 3; i++) recordRunSample(7);
  const r = estimateRerunHonest([7, 8]); // 8 从未记录
  assert.equal(r.known, false);
  assert.equal(r.samples, 0);
});
