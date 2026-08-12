/* ============================================================
   validateParam(领域参数校验,Schema 层的 UI 侧镜像)锁定
   —— 约定:返回 null 表示通过,否则返回错误提示文案。
   越界文案直接取 PARAM_SCHEMA[key].hint,避免抄写错位。
   ============================================================ */
import './_env.mjs';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { validateParam, PARAM_SCHEMA } from '../../prototype/js/state.js';

test('数值边界:min/max 为闭区间,端点合法', () => {
  assert.equal(validateParam('min_coherence', 0), null);
  assert.equal(validateParam('min_coherence', 1), null);
  assert.equal(validateParam('dpi', 72), null);
  assert.equal(validateParam('dpi', 1200), null);
});

test('越界返回 schema 的 hint 文案', () => {
  assert.equal(validateParam('min_coherence', -0.01), PARAM_SCHEMA.min_coherence.hint);
  assert.equal(validateParam('min_coherence', 1.01), PARAM_SCHEMA.min_coherence.hint);
  assert.equal(validateParam('dpi', 71), PARAM_SCHEMA.dpi.hint);
  assert.equal(validateParam('dpi', 1201), PARAM_SCHEMA.dpi.hint);
});

test('int 类型拒绝小数,接受整数', () => {
  assert.equal(validateParam('threads', 8), null);
  assert.equal(validateParam('threads', 8.5), 'threads 必须是整数');
  assert.equal(validateParam('range_looks', 10.2), 'range_looks 必须是整数');
});

test('非数字输入 → 必须是数字(NaN / Infinity 同样拒绝)', () => {
  assert.equal(validateParam('threads', 'abc'), 'threads 必须是数字');
  assert.equal(validateParam('alpha', NaN), 'alpha 必须是数字');
  assert.equal(validateParam('alpha', Infinity), 'alpha 必须是数字');
});

test('数字字符串走 Number 强转后通过(UI 输入框场景)', () => {
  assert.equal(validateParam('alpha', '0.5'), null);
  assert.equal(validateParam('threads', '8'), null);
});

test('设计决策:schema 之外的参数键不校验,直接放行', () => {
  // PARAM_SCHEMA 只是数值参数的 UI 侧镜像,完整校验在服务端 cap.validate_params
  // (未声明参数会被 400 拒绝);UI 不重复维护全量清单,避免误拦合法参数。
  assert.equal(validateParam('no_such_key', 'anything'), null);
});

/* ---------- 2026-08-12 缺陷修复回归:空值显式拒绝 ---------- */

test('空字符串判无效:不再被 Number 强转为 0 静默通过(对 min=0 的参数尤其危险)', () => {
  assert.equal(validateParam('min_coherence', ''), 'min_coherence 不能为空'); // 修复前 '' → 0 静默通过
  assert.equal(validateParam('threads', ''), 'threads 不能为空');            // 修复前靠范围误拦,文案错位
});

test('纯空白字符串同样判无效(trim 后为空)', () => {
  assert.equal(validateParam('min_coherence', '   '), 'min_coherence 不能为空');
  assert.equal(validateParam('alpha', '\t'), 'alpha 不能为空');
});

test('null / undefined 判无效:Number(null) 会静默变 0,与空串同类', () => {
  assert.equal(validateParam('min_coherence', null), 'min_coherence 不能为空');
  assert.equal(validateParam('min_coherence', undefined), 'min_coherence 不能为空');
});

test('真实 0 值不受空值拒绝误伤:数字 0 与字符串 "0" 照常走范围校验', () => {
  assert.equal(validateParam('min_coherence', 0), null);
  assert.equal(validateParam('min_coherence', '0'), null);
  assert.equal(validateParam('threads', 0), PARAM_SCHEMA.threads.hint); // 0 < min=1:范围拦截而非空值拦截
});

test('max_temporal_baseline 时间基线边界 6-730', () => {
  assert.equal(validateParam('max_temporal_baseline', 6), null);
  assert.equal(validateParam('max_temporal_baseline', 730), null);
  assert.equal(validateParam('max_temporal_baseline', 5), PARAM_SCHEMA.max_temporal_baseline.hint);
  assert.equal(validateParam('max_temporal_baseline', 731), PARAM_SCHEMA.max_temporal_baseline.hint);
});
