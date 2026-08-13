/* ============================================================
   sessionops.js(会话重命名/归档/还原纯逻辑)行为锁定
   —— 与服务端 api/app.py 的 check_session_name 同口径:
      非空、去首尾空白、≤80 字、无控制字符。
   撤销窗口用注入的假计时器验证:5 秒语义不靠真等待。
   ============================================================ */
import './_env.mjs';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  NAME_MAX, validateSessionName, splitArchived,
  applyRename, applyArchive, applyRestore, createUndoWindow,
} from '../../prototype/js/sessionops.js';

/* ---------------- validateSessionName ---------------- */

test('validateSessionName:合法名通过并剔除首尾空白', () => {
  assert.deepEqual(validateSessionName('Ridgecrest 同震形变'),
    { ok: true, name: 'Ridgecrest 同震形变' });
  assert.deepEqual(validateSessionName('  两边留白  '), { ok: true, name: '两边留白' });
  // name 不落文件系统:路径分隔符等敏感字符合法(与 id 校验的关键差异)
  assert.equal(validateSessionName('A/B:C*D?').ok, true);
});

test('validateSessionName:空 / 纯空白 / 非字符串输入拒绝', () => {
  assert.equal(validateSessionName('').ok, false);
  assert.equal(validateSessionName('   ').ok, false);
  assert.equal(validateSessionName(null).ok, false);
  assert.equal(validateSessionName(undefined).ok, false);
});

test('validateSessionName:长度边界 80 收 81 拒(去空白后计)', () => {
  assert.equal(validateSessionName('名'.repeat(NAME_MAX)).ok, true);
  assert.equal(validateSessionName('名'.repeat(NAME_MAX + 1)).ok, false);
  // 首尾空白不计入长度:空白 + 80 字仍合法
  assert.equal(validateSessionName(`  ${'x'.repeat(NAME_MAX)}  `).ok, true);
});

test('validateSessionName:控制字符(换行/制表/DEL)拒绝', () => {
  assert.equal(validateSessionName('换\n行').ok, false);
  assert.equal(validateSessionName('制\t表').ok, false);
  assert.equal(validateSessionName('删\x7f除').ok, false);
});

/* ---------------- 列表变换 ---------------- */

const mkList = () => [
  { id: 'a', name: 'A', archived: 1723500000000 },
  { id: 'b', name: 'B' },
  { id: 'c', name: 'C' },
];

test('splitArchived:按归档标记切分,条目引用共享、原数组不动', () => {
  const list = mkList();
  const { active, archived } = splitArchived(list);
  assert.deepEqual(active.map((s) => s.id), ['b', 'c']);
  assert.deepEqual(archived.map((s) => s.id), ['a']);
  assert.equal(active[0], list[1]);   // 引用共享:改 active 条目即改原列表
  assert.equal(list.length, 3);       // 原数组不被裁剪
  assert.deepEqual(splitArchived([]), { active: [], archived: [] });
  assert.deepEqual(splitArchived(null), { active: [], archived: [] });
});

test('applyRename:就地改名;未知 id 返回 false 不动列表', () => {
  const list = mkList();
  assert.equal(applyRename(list, 'b', '新 B'), true);
  assert.equal(list[1].name, '新 B');
  assert.equal(applyRename(list, 'ghost', 'x'), false);
  assert.deepEqual(list.map((s) => s.name), ['A', '新 B', 'C']);
});

test('applyArchive / applyRestore:归档落时间戳,还原清标记', () => {
  const list = mkList();
  assert.equal(applyArchive(list, 'b', 42), true);
  assert.equal(list[1].archived, 42);
  assert.equal(applyRestore(list, 'b'), true);
  assert.equal('archived' in list[1], false);
  // 还原未归档的 / 未知 id:false
  assert.equal(applyRestore(list, 'c'), false);
  assert.equal(applyRestore(list, 'ghost'), false);
  assert.equal(applyArchive(list, 'ghost'), false);
  // 缺省时间戳:当前时刻(只验证是递增的数值,不钉具体值)
  assert.equal(applyArchive(list, 'c'), true);
  assert.equal(typeof list[2].archived, 'number');
});

/* ---------------- 撤销窗口(假计时器) ---------------- */

/** 手动推进的假计时器:setTimeoutFn 登记回调,fire() 触发到期。 */
function fakeTimers() {
  let seq = 0;
  const timers = new Map();
  return {
    setTimeoutFn: (fn, ms) => { const id = ++seq; timers.set(id, { fn, ms }); return id; },
    clearTimeoutFn: (id) => { timers.delete(id); },
    fire(id) { const t = timers.get(id); timers.delete(id); t?.fn(); },
    firstId() { return timers.keys().next().value; },
    size() { return timers.size; },
  };
}

test('撤销窗口:窗口内 cancel 返回 true 并清计时器', () => {
  const t = fakeTimers();
  const w = createUndoWindow({ timeoutMs: 5000, ...t });
  w.start('sess-1');
  assert.equal(w.has('sess-1'), true);
  assert.equal(w.cancel('sess-1'), true);   // 5 秒内撤销:成功
  assert.equal(w.has('sess-1'), false);
  assert.equal(t.size(), 0);                // 计时器已清,不残留
  assert.equal(w.cancel('sess-1'), false);  // 二次撤销:窗口已关
});

test('撤销窗口:到期后 onExpire 触发且 cancel 变 false', () => {
  const t = fakeTimers();
  const w = createUndoWindow({ timeoutMs: 5000, ...t });
  const expired = [];
  w.start('sess-1', (id) => expired.push(id));
  t.fire(t.firstId());                      // 模拟 5 秒到期
  assert.deepEqual(expired, ['sess-1']);
  assert.equal(w.cancel('sess-1'), false);  // 过期后撤销无效(按钮成空操作)
  assert.equal(w.has('sess-1'), false);
});

test('撤销窗口:同 id 重复开窗替换旧计时器,不叠加', () => {
  const t = fakeTimers();
  const w = createUndoWindow({ timeoutMs: 5000, ...t });
  w.start('sess-1');
  const first = t.firstId();
  w.start('sess-1');                        // 再归档同一会话:旧窗作废
  assert.equal(t.size(), 1);                // 旧计时器被 clear,只剩新的
  assert.notEqual(t.firstId(), first);
  assert.equal(w.cancel('sess-1'), true);
  // 未 start 过的 id:cancel/has 均为否
  assert.equal(w.cancel('never'), false);
  assert.equal(w.has('never'), false);
});
