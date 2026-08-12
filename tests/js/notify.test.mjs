/* ============================================================
   notify.js（机制 #4 通知 + 机制 #8 会话状态归组）行为锁定
   —— 全部环境依赖注入假实现（Notification / document.hidden /
   __TAURI__ / 聚焦函数），在 Node 里验证：
     · 权限流：惰性申请只问一次、拒绝后不再骚扰
     · hidden 分支：页面可见绝不打扰；不可见才发 + 标签页角标
     · 降级链：桌面桥（__TAURI__.notification）→ Web Notification
     · 分组归类：实时状态 > 运维视图 > tone 兜底，四组闭集
   ============================================================ */
import './_env.mjs';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  createNotifier, classifySession, groupSessions,
  summarizeAdminRuns, startAdminPoll, GROUP_LABEL,
} from '../../prototype/js/notify.js';

/* ---------------- 假环境工厂 ---------------- */

/** 假 document：hidden / title 可控，visibilitychange 可手动触发。 */
function fakeDoc({ hidden = true, title = 'InSAR-Agent 原型' } = {}) {
  const handlers = {};
  return {
    hidden, title,
    addEventListener(type, fn) { (handlers[type] ||= []).push(fn); },
    dispatch(type) { for (const fn of handlers[type] || []) fn(); },
  };
}

/** 假 Web Notification 构造器：静态 permission + 申请计数 + 实例登记。 */
function fakeNotificationClass({ permission = 'default', answer = 'granted' } = {}) {
  const calls = { request: 0 };
  const created = [];
  class FakeNotification {
    static permission = permission;
    static async requestPermission() {
      calls.request += 1;
      FakeNotification.permission = answer;
      return answer;
    }
    constructor(title, opts = {}) {
      this.title = title;
      this.body = opts.body;
      this.closed = false;
      created.push(this);
    }
    close() { this.closed = true; }
  }
  return { FakeNotification, calls, created };
}

/** 假 __TAURI__：notification 插件桥的权限与发送均可注入故障。 */
function fakeTauri({ granted = true, answer = 'granted', sendThrows = false } = {}) {
  const calls = { isGranted: 0, request: 0, sent: [] };
  const tauri = {
    notification: {
      async isPermissionGranted() { calls.isGranted += 1; return granted; },
      async requestPermission() { calls.request += 1; return answer; },
      async sendNotification(payload) {
        if (sendThrows) throw new Error('桥故障（注入）');
        calls.sent.push(payload);
      },
    },
  };
  return { tauri, calls };
}

/* ============================================================
   一、权限流
   ============================================================ */

test('权限流：惰性申请只问一次，granted 后 Web 层可发通知', async () => {
  const { FakeNotification, calls, created } = fakeNotificationClass({ answer: 'granted' });
  const n = createNotifier({
    doc: fakeDoc(), getNotification: () => FakeNotification, getTauri: () => null,
  });
  assert.equal(await n.ensurePermission(), true);
  assert.equal(calls.request, 1);
  await n.ensurePermission();
  assert.equal(calls.request, 1, '第二次 ensurePermission 不得再弹权限框');
  assert.equal(await n.notifyIfHidden('流水线已完成'), 'web');
  assert.equal(created.length, 1);
  assert.equal(created[0].body, '流水线已完成');
});

test('权限流：用户拒绝后不再骚扰（不再申请、不再发送）', async () => {
  const { FakeNotification, calls, created } = fakeNotificationClass({ answer: 'denied' });
  const n = createNotifier({
    doc: fakeDoc(), getNotification: () => FakeNotification, getTauri: () => null,
  });
  assert.equal(await n.ensurePermission(), false);
  assert.equal(calls.request, 1);
  await n.ensurePermission();
  assert.equal(calls.request, 1, '拒绝后不得再次申请');
  assert.equal(await n.notifyIfHidden('x'), 'denied');
  assert.equal(created.length, 0, '拒绝后不得创建任何通知');
});

test('权限流：浏览器已是 denied → 直接记忆，绝不弹框', async () => {
  const { FakeNotification, calls } = fakeNotificationClass({ permission: 'denied' });
  const n = createNotifier({
    doc: fakeDoc(), getNotification: () => FakeNotification, getTauri: () => null,
  });
  assert.equal(await n.ensurePermission(), false);
  assert.equal(calls.request, 0, '已 denied 时不得调用 requestPermission');
  assert.equal(await n.notifyIfHidden('x'), 'denied');
});

/* ============================================================
   二、hidden 分支
   ============================================================ */

test('hidden 分支：页面可见时不发任何通知、不动标题', async () => {
  const { FakeNotification, created } = fakeNotificationClass({ permission: 'granted' });
  const doc = fakeDoc({ hidden: false });
  const n = createNotifier({
    doc, getNotification: () => FakeNotification, getTauri: () => null,
  });
  assert.equal(await n.notifyIfHidden('x'), 'visible');
  assert.equal(created.length, 0);
  assert.equal(doc.title, 'InSAR-Agent 原型');
  // document 不存在（Node 裸环境）同样按可见处理，不抛错
  const bare = createNotifier({ doc: null, getNotification: () => null, getTauri: () => null });
  assert.equal(await bare.notifyIfHidden('x'), 'visible');
});

test('hidden 分支：不可见 → 发通知 + 标签页角标；点击聚焦；切回复原标题', async () => {
  const { FakeNotification, created } = fakeNotificationClass({ permission: 'granted' });
  const doc = fakeDoc({ hidden: true });
  let focused = 0;
  const n = createNotifier({
    doc, getNotification: () => FakeNotification, getTauri: () => null,
    focus: () => { focused += 1; },
  });
  n.attach();
  assert.equal(await n.notifyIfHidden('第 8 步需要确认'), 'web');
  assert.equal(doc.title, '● InSAR-Agent 原型');
  await n.notifyIfHidden('再来一条');
  assert.equal(doc.title, '● InSAR-Agent 原型', '角标必须幂等，不得叠加 ●');
  created[0].onclick();
  assert.equal(focused, 1, '点击通知必须聚焦窗口');
  assert.equal(created[0].closed, true, '点击后通知应关闭');
  doc.hidden = false;
  doc.dispatch('visibilitychange');
  assert.equal(doc.title, 'InSAR-Agent 原型', '切回页面后标题复原');
});

test('runEnded：done/paused/异常中断 三种相位对应三种文案', async () => {
  const { FakeNotification, created } = fakeNotificationClass({ permission: 'granted' });
  const n = createNotifier({
    doc: fakeDoc(), getNotification: () => FakeNotification, getTauri: () => null,
  });
  await n.runEnded('done');
  await n.runEnded('paused');
  await n.runEnded('running');   // run() 异常退出时相位残留 running
  assert.equal(created.length, 3);
  assert.match(created[0].body, /完成/);
  assert.match(created[1].body, /暂停|失败/);
  assert.match(created[2].body, /中断/);
});

/* ============================================================
   三、降级链（桌面层 → Web 层）
   ============================================================ */

test('降级链：桌面桥可用且已授权 → 走 Tauri 插件，Web 层不动、不弹权限框', async () => {
  const { FakeNotification, calls: webCalls, created } = fakeNotificationClass({ permission: 'granted' });
  const { tauri, calls } = fakeTauri({ granted: true });
  const n = createNotifier({
    doc: fakeDoc(), getNotification: () => FakeNotification, getTauri: () => tauri,
  });
  assert.equal(await n.notifyIfHidden('已完成'), 'desktop');
  assert.equal(calls.sent.length, 1);
  assert.deepEqual(calls.sent[0], { title: 'InSAR-Agent', body: '已完成' });
  assert.equal(created.length, 0, '桌面层成功时不得再走 Web 层');
  assert.equal(calls.request, 0, '发送时刻不得弹桌面权限框');
  assert.equal(webCalls.request, 0, '发送时刻不得弹 Web 权限框');
});

test('降级链：__TAURI__ 存在但 notification 桥缺失/半残 → 回退 Web Notification', async () => {
  const { FakeNotification, created } = fakeNotificationClass({ permission: 'granted' });
  const mk = (tauri) => createNotifier({
    doc: fakeDoc(), getNotification: () => FakeNotification, getTauri: () => tauri,
  });
  // 桥整个缺失
  assert.equal(await mk({}).notifyIfHidden('a'), 'web');
  // 桥半残：只有 sendNotification，缺权限函数 → 探测判不可用
  const half = { notification: { sendNotification() {} } };
  assert.equal(await mk(half).notifyIfHidden('b'), 'web');
  assert.equal(created.length, 2);
});

test('降级链：桌面发送抛错 → 回退 Web Notification', async () => {
  const { FakeNotification, created } = fakeNotificationClass({ permission: 'granted' });
  const { tauri, calls } = fakeTauri({ granted: true, sendThrows: true });
  const n = createNotifier({
    doc: fakeDoc(), getNotification: () => FakeNotification, getTauri: () => tauri,
  });
  assert.equal(await n.notifyIfHidden('x'), 'web');
  assert.equal(calls.sent.length, 0);
  assert.equal(created.length, 1, '桌面层失败必须落到 Web 层');
});

test('降级链：桌面权限被拒 → denied 记忆，两层都保持沉默', async () => {
  const { FakeNotification, created } = fakeNotificationClass({ permission: 'granted' });
  const { tauri, calls } = fakeTauri({ granted: false, answer: 'denied' });
  const n = createNotifier({
    doc: fakeDoc(), getNotification: () => FakeNotification, getTauri: () => tauri,
  });
  assert.equal(await n.ensurePermission(), false);
  assert.equal(calls.request, 1);
  assert.equal(await n.notifyIfHidden('x'), 'denied');
  assert.equal(calls.sent.length, 0);
  assert.equal(created.length, 0, '桌面拒绝后不得改走 Web 层骚扰');
});

/* ============================================================
   四、会话分组归类（renderSessions 的数据层）
   ============================================================ */

test('分组归类：当前会话的实时状态优先于 tone', () => {
  assert.equal(classifySession({ id: 'a', tone: 'idle' }, { currentId: 'a', busy: true }), 'active');
  assert.equal(classifySession({ id: 'a', tone: 'run' }, { currentId: 'a', busy: false, phase: 'paused' }), 'attention');
  assert.equal(classifySession({ id: 'a', tone: 'idle' }, { currentId: 'a', phase: 'done' }), 'done');
  // 非当前会话不受 live 影响，落到 tone
  assert.equal(classifySession({ id: 'b', tone: 'run' }, { currentId: 'a', busy: true }), 'active');
  assert.equal(classifySession({ id: 'b', tone: 'idle' }, { currentId: 'a', phase: 'paused' }), 'idle');
});

test('分组归类：运维视图次之（ready=待审批 归「需要你」），tone 兜底', () => {
  const s = { id: 'x', tone: 'idle' };
  assert.equal(classifySession(s, {}, 'running'), 'active');
  assert.equal(classifySession(s, {}, 'planning'), 'active');
  assert.equal(classifySession(s, {}, 'failed'), 'attention');
  assert.equal(classifySession(s, {}, 'paused'), 'attention');
  assert.equal(classifySession(s, {}, 'interrupted'), 'attention');
  assert.equal(classifySession(s, {}, 'ready'), 'attention');
  assert.equal(classifySession(s, {}, 'done'), 'done');
  // 未知状态不硬归组，落回 tone 兜底
  assert.equal(classifySession({ id: 'x', tone: 'stale' }, {}, 'bogus'), 'attention');
  assert.equal(classifySession({ id: 'x', tone: 'run' }, {}), 'active');
  assert.equal(classifySession({ id: 'x' }, {}), 'idle');
});

test('groupSessions：固定组序 + 计数 + 空组剔除 + 运维映射生效', () => {
  const sessions = [
    { id: 's1', tone: 'idle' },   // 运维视图说它 failed → 需要你
    { id: 's2', tone: 'run' },    // 进行中
    { id: 's3', tone: 'idle' },   // 当前会话且 phase=done → 已完成
    { id: 's4', tone: 'stale' },  // 需要你
  ];
  const groups = groupSessions(sessions,
    { currentId: 's3', busy: false, phase: 'done' }, { s1: 'failed' });
  assert.deepEqual(groups.map((g) => g.key), ['active', 'attention', 'done'], '空的「空闲」组必须剔除');
  assert.deepEqual(groups.map((g) => g.label), ['进行中', '需要你', '已完成']);
  assert.deepEqual(groups.map((g) => g.items.length), [1, 2, 1]);
  assert.deepEqual(groups[1].items.map((s) => s.id), ['s1', 's4']);
  assert.equal(GROUP_LABEL.attention, '需要你');
});

test('summarizeAdminRuns：同会话取最新 run；非法载荷 → null', () => {
  const map = summarizeAdminRuns([
    { session_id: 'a', status: 'failed', created_at: 1 },
    { session_id: 'a', status: 'running', created_at: 5 },
    { session_id: 'b', status: 'done', created_at: 2 },
    { status: 'done', created_at: 9 },   // 缺 session_id：跳过
  ]);
  assert.deepEqual(map, { a: 'running', b: 'done' });
  assert.equal(summarizeAdminRuns(null), null);
  assert.equal(summarizeAdminRuns('oops'), null);
});

test('startAdminPoll：可达 → 映射回调；不可达/非 2xx → 回调 null（只按本地状态）', async () => {
  const settle = () => new Promise((r) => setTimeout(r, 20));
  // 成功
  let got = [];
  const stop1 = startAdminPoll((m) => got.push(m), {
    intervalMs: 60_000,
    fetchFn: async () => ({ ok: true, json: async () => [{ session_id: 'a', status: 'running', created_at: 1 }] }),
  });
  await settle(); stop1();
  assert.deepEqual(got, [{ a: 'running' }]);
  // 网络异常
  got = [];
  const stop2 = startAdminPoll((m) => got.push(m), {
    intervalMs: 60_000, fetchFn: async () => { throw new Error('offline'); },
  });
  await settle(); stop2();
  assert.deepEqual(got, [null]);
  // 非 2xx
  got = [];
  const stop3 = startAdminPoll((m) => got.push(m), {
    intervalMs: 60_000, fetchFn: async () => ({ ok: false }),
  });
  await settle(); stop3();
  assert.deepEqual(got, [null]);
});
