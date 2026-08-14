/* ============================================================
   runswitch.js 纯函数锁定(run 历史切换器)
   —— buildRunTree(fork 谱系 → 缩进展示序列)/ optionLabel(下拉文案)/
      runStamp / stepsSuffix / setActiveRun 归一化。
   全部不碰 DOM,_env.mjs 桩足够(runswitch 经 dom.js/state.js 导入链)。
   ============================================================ */
import './_env.mjs';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  buildRunTree, optionLabel, runStamp, stepsSuffix, simSuffix,
  setActiveRun, activeRunId, READONLY_HINT,
} from '../../prototype/js/runswitch.js';

const R = (id, { parent = null, at = 0, status = 'done', steps = null } = {}) => ({
  run_id: id, parent_run_id: parent, created_at: at, status, steps,
});

test('buildRunTree:无谱系时按 created_at 倒序平铺(depth 全 0)', () => {
  const tree = buildRunTree([R('a', { at: 1 }), R('c', { at: 3 }), R('b', { at: 2 })]);
  assert.deepEqual(tree.map((n) => n.run.run_id), ['c', 'b', 'a']);
  assert.deepEqual(tree.map((n) => n.depth), [0, 0, 0]);
});

test('buildRunTree:fork 子链缩进挂在父之下,孙代 depth=2,同层倒序', () => {
  const tree = buildRunTree([
    R('root', { at: 1 }),
    R('fork1', { parent: 'root', at: 2 }),
    R('fork2', { parent: 'root', at: 4 }),
    R('grand', { parent: 'fork1', at: 3 }),
    R('other', { at: 5 }),               // 更晚的独立根排最前
  ]);
  assert.deepEqual(
    tree.map((n) => [n.run.run_id, n.depth]),
    [['other', 0], ['root', 0], ['fork2', 1], ['fork1', 1], ['grand', 2]]);
});

test('buildRunTree:父不在清单(已被清理)的 run 按根展示,不丢行', () => {
  const tree = buildRunTree([R('orphan', { parent: 'gone', at: 2 }), R('a', { at: 1 })]);
  assert.deepEqual(tree.map((n) => [n.run.run_id, n.depth]), [['orphan', 0], ['a', 0]]);
});

test('buildRunTree:坏数据成环/自指不死循环,每行至多出现一次;非数组给空', () => {
  const cyc = buildRunTree([
    R('x', { parent: 'y', at: 2 }), R('y', { parent: 'x', at: 1 }),
    R('self', { parent: 'self', at: 3 }),
  ]);
  const ids = cyc.map((n) => n.run.run_id);
  assert.deepEqual([...ids].sort(), ['self', 'x', 'y']);   // 全都在,且仅一次
  assert.equal(new Set(ids).size, ids.length);
  assert.deepEqual(buildRunTree(null), []);
  assert.deepEqual(buildRunTree('junk'), []);
});

test('optionLabel:最新前缀 + 谱系缩进(└)+ 状态与统计后缀', () => {
  const latest = { run: R('20260813T101112-ab12cd34', { status: 'done', steps: { total: 2, done: 2, skipped: 0, failed: 0 } }), depth: 0 };
  assert.equal(optionLabel(latest, '20260813T101112-ab12cd34'),
               '最新 · 20260813T101112(done · ✓2)');
  const child = { run: R('20260812T000000-ee-fork', { status: 'failed', steps: { total: 3, done: 1, skipped: 0, failed: 2 } }), depth: 1 };
  assert.equal(optionLabel(child, 'zzz'), '└ 20260812T000000(failed · ✓1 ✗2)');
  const grand = { run: R('20260811T000000-ff', { status: 'running', steps: null }), depth: 2 };
  assert.equal(optionLabel(grand, 'zzz'), '\u3000└ 20260811T000000(running)');
});

test('simSuffix/optionLabel:simulated run 如实标「模拟」,真实 run 文案逐字节不变(0814B W6)', () => {
  assert.equal(simSuffix({ simulated: true }), ' · 模拟');
  assert.equal(simSuffix({ simulated: 1 }), ' · 模拟');       // /api/runs 送布尔,SQLite 侧曾是 0/1
  assert.equal(simSuffix({ simulated: false }), '');
  assert.equal(simSuffix({}), '');                            // 旧后端无字段:不标
  assert.equal(simSuffix(null), '');
  const sim = {
    run: { ...R('20260814T010203-aa11bb22', { status: 'done', steps: { total: 1, done: 1, skipped: 0, failed: 0 } }), simulated: true },
    depth: 0,
  };
  assert.equal(optionLabel(sim, 'zzz'), '20260814T010203(done · 模拟 · ✓1)');
  const real = { run: R('20260814T010203-aa11bb22', { status: 'done', steps: { total: 1, done: 1, skipped: 0, failed: 0 } }), depth: 0 };
  assert.equal(optionLabel(real, 'zzz'), '20260814T010203(done · ✓1)');
});

test('runStamp:取 run_id 时间戳前缀;fork 后缀不影响;空值给占位不给空白', () => {
  assert.equal(runStamp('20260813T101112-ab12cd34'), '20260813T101112');
  assert.equal(runStamp('20260813T101112-ab12cd34-fork'), '20260813T101112');
  assert.equal(runStamp('weird_id_no_dash'), 'weird_id_no_dash');
  assert.equal(runStamp(''), '—');
  assert.equal(runStamp(null), '—');
});

test('stepsSuffix:非零项才列(✓done ↷skipped ✗failed),全零/缺失为空串', () => {
  assert.equal(stepsSuffix({ total: 4, done: 1, skipped: 2, failed: 1 }), ' · ✓1 ↷2 ✗1');
  assert.equal(stepsSuffix({ total: 3, done: 3, skipped: 0, failed: 0 }), ' · ✓3');
  assert.equal(stepsSuffix({ total: 0, done: 0, skipped: 0, failed: 0 }), '');
  assert.equal(stepsSuffix(null), '');
  assert.equal(stepsSuffix('junk'), '');
});

test('setActiveRun:选历史 run 生效;显式选中最新 run 归一化回「最新」(null)', () => {
  assert.equal(setActiveRun('old-run', 'latest-run'), 'old-run');
  assert.equal(activeRunId(), 'old-run');
  assert.equal(setActiveRun('latest-run', 'latest-run'), null);   // 选最新 = 回缺省
  assert.equal(activeRunId(), null);
  assert.equal(setActiveRun('old-run', 'latest-run'), 'old-run');
  assert.equal(setActiveRun(null), null);                         // 「回到最新」出口
  assert.equal(activeRunId(), null);
});

test('只读提示文案与任务口径一字不差(disabled title 与横幅共用)', () => {
  assert.equal(READONLY_HINT, '历史 run 只读,fork 或回到最新再操作');
});
