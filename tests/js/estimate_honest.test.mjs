/* ============================================================
   estimateRerunHonest(诚实时长预估,§7.5)两分支锁定
   —— 历史样本 ≥3 → known:true 给区间;<3 → 如实说「时长未知」。
   历史键 = `${stepId}:${fingerprint}`,同配置才算同历史。
   演示抖动样本已删除:recordRunSample 只记真实秒数(显式传入,
   或由镜像 startedAt 推算);没有依据就不记 —— 绝不用假样本。
   注意:RUN_HISTORY 是模块级不可清空,本文件用例按声明顺序执行,
   各用例用不同 stepId / 参数配置错开历史键,避免相互污染。
   ============================================================ */
import './_env.mjs';
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import * as St from '../../prototype/js/state.js';
import {
  setRegistry, setParams, setStepState, recordRunSample, estimateRerunHonest, st_,
} from '../../prototype/js/state.js';
import { REGISTRY, seedSteps } from './_registry.mjs';

setRegistry(REGISTRY);
beforeEach(() => { seedSteps(St, 5); });

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
  recordRunSample(10, 130);
  recordRunSample(10, 118);
  const r = estimateRerunHonest([10]);
  assert.equal(r.known, false);
  assert.equal(r.samples, 2);
  assert.equal(r.label, '时长未知（首次运行此配置）');
});

test('历史样本 ≥3(真实秒数)→ known,给出分钟区间文案', () => {
  for (let i = 0; i < 3; i++) recordRunSample(7, 2394);
  const r = estimateRerunHonest([7]);
  assert.equal(r.known, true);
  assert.equal(r.samples, 3);
  assert.equal(r.loSec, 2394);
  assert.equal(r.hiSec, 2394);
  assert.equal(r.label, '约 40–40 分钟（基于本机历史 3 次运行）');
});

test('样本波动 → 区间取各步骤历史的 min/max 之和', () => {
  recordRunSample(2, 60);
  recordRunSample(2, 120);
  recordRunSample(2, 90);
  const r = estimateRerunHonest([2]);
  assert.equal(r.known, true);
  assert.equal(r.loSec, 60);
  assert.equal(r.hiSec, 120);
  assert.equal(r.label, '约 1–2 分钟（基于本机历史 3 次运行）');
});

test('同配置有历史 → known;改参数(新配置)→ 回到未知分支;改回 → 历史重新命中', () => {
  setParams(7, { max_temporal_baseline: 240 });
  for (let i = 0; i < 3; i++) recordRunSample(7, 2400);
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
    recordRunSample(4, 3276);
    recordRunSample(7, 2394);
  }
  const r = estimateRerunHonest([4, 7]);
  assert.equal(r.known, true);
  assert.equal(r.loSec, 5670); // 3276 + 2394
  assert.equal(r.hiSec, 5670);
  assert.equal(r.label, '约 1.6–1.6 小时（基于本机历史 3 次运行）');
});

test('多步骤里任一无历史 → 整体未知(样本数取各步最小值)', () => {
  for (let i = 0; i < 3; i++) recordRunSample(7, 2394);
  const r = estimateRerunHonest([7, 8]); // 8 从未记录
  assert.equal(r.known, false);
  assert.equal(r.samples, 0);
});

test('没有依据不记样本:无 startedAt 且未显式传秒 → 跳过;非法秒数(0/负/NaN)→ 跳过', () => {
  recordRunSample(9);          // 镜像无 startedAt,也没显式秒数
  recordRunSample(9, 0);
  recordRunSample(9, -5);
  recordRunSample(9, NaN);
  assert.equal(estimateRerunHonest([9]).samples, 0);
});

test('由 startedAt 推算真实耗时(step.start 落起点,step.end 记样本)', () => {
  setStepState(9, 'running');                 // setStepState 落 startedAt
  st_(9).startedAt = Date.now() - 5000;       // 模拟已运行 5 秒
  recordRunSample(9);
  const r = estimateRerunHonest([9]);
  assert.equal(r.samples, 1);                 // 记了一笔真实样本(仍不足 3 → 未知)
  assert.equal(r.known, false);
});
