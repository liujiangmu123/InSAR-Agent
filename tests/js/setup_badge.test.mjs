/* ============================================================
   engineSourceBadge(引擎来源徽标,setup.js 的纯函数出口)锁定
   —— 输入是后端 status.engine.engines 的探测值字符串:
      "present"          → 启动 shell 的 PATH 命中;
      "present(<env>)"   → conda 引擎环境(显式 engine_prefix 或隐式发现);
      "… (wsl)"          → WSL 兜底(setup_router 拼的空格 + "(wsl)" 后缀);
      null / "" / 非串   → 引擎缺失,不出徽标。
   徽标语义与 tests/test_setup_discovery.py 的后端三态锁互为两端。
   ============================================================ */
import './_env.mjs';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { engineSourceBadge } from '../../prototype/js/setup.js';

/** 只比 kind/label(title 是给 hover 的说明文案,单独一条锁存在性) */
function pick(badge) {
  return { kind: badge.kind, label: badge.label };
}

test('缺失值不出徽标:null / undefined / 空串 / 非字符串', () => {
  assert.equal(engineSourceBadge(null), null);
  assert.equal(engineSourceBadge(undefined), null);
  assert.equal(engineSourceBadge(''), null);
  assert.equal(engineSourceBadge(42), null);
});

test('PATH 命中:"present" 与裸版本串都归 PATH 徽标', () => {
  assert.deepEqual(pick(engineSourceBadge('present')), { kind: 'path', label: 'PATH' });
  assert.deepEqual(pick(engineSourceBadge('3.13.2')), { kind: 'path', label: 'PATH' });
});

test('conda 环境命中:"present(insar)" → 徽标显示环境名', () => {
  assert.deepEqual(pick(engineSourceBadge('present(insar)')),
    { kind: 'conda', label: 'insar' });
  assert.deepEqual(pick(engineSourceBadge('present(insar-engine)')),
    { kind: 'conda', label: 'insar-engine' });
});

test('WSL 兜底:空格 + "(wsl)" 后缀,version 与 present 两种形状都识别', () => {
  assert.deepEqual(pick(engineSourceBadge('present (wsl)')), { kind: 'wsl', label: 'WSL' });
  assert.deepEqual(pick(engineSourceBadge('2.0.6 (wsl)')), { kind: 'wsl', label: 'WSL' });
});

test('歧义钉死:conda 环境恰好叫 wsl(无空格)不得误判为 WSL 来源', () => {
  // setup_router 拼 WSL 后缀时带空格("x (wsl)");"present(wsl)" 只能是环境名
  assert.deepEqual(pick(engineSourceBadge('present(wsl)')), { kind: 'conda', label: 'wsl' });
});

test('title 提示齐备:三种来源都有中文"来源"说明(hover 可读)', () => {
  for (const v of ['present', 'present(insar)', 'present (wsl)']) {
    assert.ok(engineSourceBadge(v).title.includes('来源'), v);
  }
});
