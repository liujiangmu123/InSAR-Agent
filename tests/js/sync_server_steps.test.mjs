/* ============================================================
   syncServerSteps(GET /api/state 服务端状态镜像)行为锁定
   —— 演示种子删除后,服务端成为镜像条目的唯一创建者:
   S.steps 启动为空,/api/state 下发的步骤就地创建;字段按存在与否合并。
   含 2026-08-12 UI 事故回归:mock 种子状态里 skipped 的 2-6 步
   一度被塞进执行列表。执行列表 rerunSummary().ids 直接取自
   workSummary().all(见 backend.mock.js / backend.sse.js),
   所以在 state 层锁住 workSummary().all 即锁住事故根因。
   ============================================================ */
import './_env.mjs';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as St from '../../prototype/js/state.js';
import {
  S, STEP_DEFS, setRegistry, initSteps, syncServerSteps, workSummary,
  setParams, st_, on,
} from '../../prototype/js/state.js';
import { REGISTRY, seedSteps } from './_registry.mjs';

setRegistry(REGISTRY);
const seed5 = () => seedSteps(St, 5);

test('冷启动(镜像为空):服务端步骤就地创建条目,skipped 映射为 done 展示', () => {
  initSteps();   // 启动态:没有任何本地种子
  assert.equal(S.steps.size, 0);
  syncServerSteps([{ id: 2, state: 'skipped' }]);
  assert.equal(st_(2).state, 'done');
  assert.equal(st_(2).stale, false);
  assert.equal(S.steps.size, 1);   // 只创建服务端给的步骤,不虚构其余
});

test('回归 2026-08-12:镜像含 skipped 的 2-6 步后,执行列表不得包含它们', () => {
  initSteps(); // 冷启动:计划完全由服务端下发(创建 + 状态一次完成)
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
  seed5();
  syncServerSteps([{ id: 3, state: 'failed' }]);
  assert.equal(st_(3).state, 'failed');
  const sum = workSummary();
  assert.deepEqual(sum.resume, [3]);
  assert.ok(!sum.stale.includes(3) && !sum.pending.includes(3));
});

test('服务端 stale:true 且 done → 本地展示为 stale', () => {
  seed5();
  syncServerSteps([{ id: 2, state: 'done', stale: true }]);
  assert.equal(st_(2).state, 'stale');
  assert.equal(st_(2).stale, true);
  assert.deepEqual(workSummary().stale, [2]);
});

test('failed + stale:true → 状态保持 failed,归 resume 桶(终态优先于 stale,§7.8)', () => {
  seed5();
  syncServerSteps([{ id: 3, state: 'failed', stale: true }]);
  assert.equal(st_(3).state, 'failed');
  assert.equal(st_(3).stale, true);
  const sum = workSummary();
  assert.deepEqual(sum.resume, [3]);  // 第一动作是断点处置,不是覆写重跑
  assert.deepEqual(sum.stale, []);
  assert.deepEqual(sum.all.filter((id) => id === 3), [3]); // all 里只计一次
});

test('注册表之外的步骤 id 同样创建镜像:指纹给占位符,不抛错(计划以服务端为准)', () => {
  seed5();
  assert.doesNotThrow(() =>
    syncServerSteps([{ id: 99, state: 'done', method: 'custom_step' }, { id: 6, state: 'running' }]));
  assert.equal(st_(6).state, 'running');
  assert.equal(st_(99).state, 'done');           // 服务端说有就有 —— 目录只是注解
  assert.equal(st_(99).method, 'custom_step');
  assert.equal(st_(99).fingerprint, '········'); // 无目录依赖信息 → 占位指纹
});

test('空数组与非数组载荷是 no-op', () => {
  seed5();
  const before = JSON.stringify([...S.steps.values()]);
  syncServerSteps([]);
  syncServerSteps(null);
  syncServerSteps('oops');
  assert.equal(JSON.stringify([...S.steps.values()]), before);
});

test('服务端 method 镜像到本地并重算该步指纹', () => {
  seed5();
  const fpBefore = st_(3).fingerprint;
  syncServerSteps([{ id: 3, state: 'done', method: 'snap_backgeocoding' }]);
  assert.equal(st_(3).method, 'snap_backgeocoding');
  assert.notEqual(st_(3).fingerprint, fpBefore);
});

test('服务端未给 stale 字段 → 本地脏标记保留(没给的字段不动)', () => {
  seed5();
  setParams(3, { esd_coherence_threshold: 0.9 }); // 本地先把 3/4/5 标脏
  assert.equal(st_(3).state, 'stale');
  syncServerSteps([{ id: 3, state: 'done' }]);    // 服务端只说 done,没提 stale
  assert.equal(st_(3).stale, true);               // 本地脏标记不被抹掉(修复前被复位)
  assert.equal(st_(3).state, 'stale');            // done+脏 收敛为 stale 展示,无分裂态
  assert.ok(workSummary().all.includes(3));       // 仍在待办里
});

test('method 镜像后按拓扑序重算全部指纹:下游变、上游不变', () => {
  seed5();
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
  seed5();
  syncServerSteps([{ id: 3, state: 'done', stale: true }]);
  assert.equal(st_(3).state, 'stale');
  assert.equal(st_(4).state, 'done'); // 前端镜像不替服务端推导级联
  assert.equal(st_(4).stale, false);
  assert.deepEqual(workSummary().stale, [3]);
});

test('服务端条目只有 id(state/stale 都缺)→ 完全不动,无分裂态', () => {
  seed5();
  setParams(4, { range_looks: 12 }); // 4/5 标脏
  assert.equal(st_(4).state, 'stale');
  assert.equal(st_(4).stale, true);
  syncServerSteps([{ id: 4 }]);      // 只有 id,什么都没说
  assert.equal(st_(4).state, 'stale');  // state 不动
  assert.equal(st_(4).stale, true);     // stale 也不动(修复前被复位成 false,产生分裂态)
  assert.ok(workSummary().stale.includes(4));
});

test('一次同步在同一 batch 内只广播一次 steps(避免连锁重渲染)', () => {
  seed5();
  let calls = 0;
  let seen = null;
  const off = on('steps', (topics) => { calls += 1; seen = topics; });
  syncServerSteps([{ id: 2, state: 'failed' }, { id: 3, state: 'failed' }]);
  off();
  assert.equal(calls, 1);
  assert.ok(seen.includes('steps') && seen.includes('files'));
});

/* ---------- 2026-08-12 缺陷修复回归:字段存在才镜像 + 分裂态收敛 ---------- */

test('服务端明确 stale:false 且本地为 stale → 收敛回 done(布尔权威,不留分裂态)', () => {
  seed5();
  setParams(3, { esd_coherence_threshold: 0.9 });
  assert.equal(st_(3).state, 'stale');
  syncServerSteps([{ id: 3, stale: false }]);  // 没给 state,只明确说不脏
  assert.equal(st_(3).stale, false);
  assert.equal(st_(3).state, 'done');          // 'stale' 态本是 done+脏 的派生展示,去脏即回 done
  assert.ok(!workSummary().all.includes(3));
});

test('服务端只给 state:"stale"(缺 stale 字段)→ 布尔联动为 true,无分裂态', () => {
  seed5();
  syncServerSteps([{ id: 2, state: 'stale' }]);
  assert.equal(st_(2).state, 'stale');
  assert.equal(st_(2).stale, true);   // 修复前:state='stale' 而 stale=false 的分裂态
  assert.deepEqual(workSummary().stale, [2]);
});

test('设计决策:state:"stale" 与 stale:false 同时下发(矛盾载荷)→ 布尔权威,收敛为 done', () => {
  // 服务端 store.set_stale 的联动方向是「脏布尔 → 派生 state」,前端沿同一方向收敛;
  // 真实 /api/state 不会产生这种矛盾,该规则只为部分载荷/异常数据兜底。
  seed5();
  syncServerSteps([{ id: 2, state: 'stale', stale: false }]);
  assert.equal(st_(2).state, 'done');
  assert.equal(st_(2).stale, false);
  assert.ok(!workSummary().all.includes(2));
});

/* ---------- 2026-08-12 缺陷修复回归:服务端 params 镜像(/api/state 已下发 params) ---------- */

test('服务端带 params → 整体镜像进本地,并按拓扑序级联重算指纹', () => {
  seed5();
  const fps = new Map(STEP_DEFS.map((d) => [d.id, st_(d.id).fingerprint]));
  syncServerSteps([{ id: 3, params: { esd_coherence_threshold: 0.7 } }]);
  assert.deepEqual(st_(3).params, { esd_coherence_threshold: 0.7 });
  for (const id of [1, 2]) {
    assert.equal(st_(id).fingerprint, fps.get(id), `上游 ${id} 指纹不应变化`);
  }
  for (const id of [3, 4, 5, 6, 7, 8, 9, 10, 11]) {
    assert.notEqual(st_(id).fingerprint, fps.get(id), `步骤 ${id} 指纹应级联变化`);
  }
});

test('params 镜像是整体替换:服务端未包含的本地键被清掉(镜像即对齐)', () => {
  seed5();
  assert.deepEqual(st_(5).params, { alpha: 0.4, filter_strength: 0.5 });
  syncServerSteps([{ id: 5, params: { alpha: 0.4 } }]);
  assert.deepEqual(st_(5).params, { alpha: 0.4 }); // filter_strength 不再存在
});

test('params 与本地一致 → 镜像幂等,指纹不变', () => {
  seed5();
  const fp5 = st_(5).fingerprint;
  const fp11 = st_(11).fingerprint;
  syncServerSteps([{ id: 5, params: { alpha: 0.4, filter_strength: 0.5 } }]);
  assert.equal(st_(5).fingerprint, fp5);
  assert.equal(st_(11).fingerprint, fp11);
});

test('params 字段缺失或为 null → 本地参数不动(没给的字段不动)', () => {
  seed5();
  const before = JSON.stringify(st_(5).params);
  syncServerSteps([{ id: 5 }]);
  syncServerSteps([{ id: 5, params: null }]);
  assert.equal(JSON.stringify(st_(5).params), before);
});

test('本地改参后镜像服务端权威 params → 前后端指纹分叉被修复(缺陷主场景)', () => {
  seed5();
  const fp5 = st_(5).fingerprint;
  setParams(5, { alpha: 0.9 });                  // 本地先分叉
  assert.notEqual(st_(5).fingerprint, fp5);
  syncServerSteps([{ id: 5, state: 'done', stale: false,
                     params: { alpha: 0.4, filter_strength: 0.5 } }]);
  assert.equal(st_(5).fingerprint, fp5);         // 指纹与服务端配置重新对齐
  assert.equal(st_(5).state, 'done');
  assert.ok(!workSummary().all.includes(5));
});
