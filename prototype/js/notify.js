/* ============================================================
   通知与会话状态归组（零 npm 依赖 ES Module）
   —— RESEARCH-agent-ux-2026-08-12 机制 #4（桌面通知 + 标签页角标）
      与机制 #8（会话侧栏状态分组）的数据/逻辑层，
      外加「通知中心」（铃铛 + 抽屉，见文件下半部分第三节）。

   通知触发约定（app.js 接线 ≤6 行）：
     · run() 结束（done / 失败暂停 / 异常中断）→ runEnded(phase)
     · 审批卡出现 → approvalNeeded()
   两个入口现在同时喂给通知中心（抽屉留痕），原生通知是否发送
   由抽屉里的「后台通知」开关决定（默认关）；页面可见绝不打扰。

   分层与降级链：
     桌面层（window.__TAURI__ 存在）→ 先探测 __TAURI__.notification
     插件桥是否可用（desktop.js 负责），不可用/异常回退 Web 层的原生
     Notification API。权限申请只发生在用户打开「后台通知」开关时，
     拒绝后本会话不再骚扰（denied 记忆 + 开关禁用并说明）。

   可测试性：createNotifier 的 Notification / document / __TAURI__ /
   聚焦函数全部可注入；分组归类是纯函数 —— tests/js/notify.test.mjs
   在 Node 里直接跑；通知中心的收集/聚合/存储层同样纯逻辑可注入，
   由 prototype/notify.check.mjs 在 Node DOM stub 里回归。
   ============================================================ */
import {
  notificationBridge, desktopNotifyPermission, desktopSendNotification,
} from './desktop.js';
import { h, icon, toast } from './dom.js';
import { S, SESSIONS, LADDER, evidenceCeiling, on as onState } from './state.js';

/* ============================================================
   一、通知器
   ============================================================ */

/**
 * 创建通知器。全部环境依赖可注入（单测传假实现，运行时用缺省值）。
 * @param {object} [opts]
 * @param {Document|null} [opts.doc] 文档对象（读 hidden / title、挂 visibilitychange）
 * @param {() => (typeof Notification|null)} [opts.getNotification] Web 通知构造器
 * @param {() => (object|null)} [opts.getTauri] 桌面壳全局对象（window.__TAURI__）
 * @param {() => void} [opts.focus] 通知点击时聚焦窗口的实现
 * @param {string} [opts.appTitle] 通知标题
 */
export function createNotifier(opts = {}) {
  const env = {
    doc: opts.doc !== undefined ? opts.doc
      : (typeof document !== 'undefined' ? document : null),
    getNotification: opts.getNotification
      || (() => (typeof Notification !== 'undefined' ? Notification : null)),
    getTauri: opts.getTauri
      || (() => (typeof window !== 'undefined' ? (window.__TAURI__ ?? null) : null)),
    focus: opts.focus
      || (() => { try { window.focus(); } catch { /* 无窗口环境忽略 */ } }),
    appTitle: opts.appTitle || 'InSAR-Agent',
  };
  // asked：本会话已申请过权限（只问一次）；denied：被拒后不再骚扰；
  // baseTitle：标签页角标（●）加上前的原标题，切回页面时复原
  const st = { asked: false, granted: false, denied: false, baseTitle: null, attached: false };

  function markTitle() {
    if (!env.doc || typeof env.doc.title !== 'string') return;
    if (st.baseTitle === null) st.baseTitle = env.doc.title;
    env.doc.title = `● ${st.baseTitle}`;
  }

  function restoreTitle() {
    if (st.baseTitle === null || !env.doc) return;
    env.doc.title = st.baseTitle;
    st.baseTitle = null;
  }

  /** 挂 visibilitychange：用户切回页面时复原标题角标。幂等。 */
  function attach() {
    if (st.attached || typeof env.doc?.addEventListener !== 'function') return;
    st.attached = true;
    env.doc.addEventListener('visibilitychange', () => {
      if (!env.doc.hidden) restoreTitle();
    });
  }

  /**
   * 惰性权限申请 —— 只在首次执行流水线时调用（用户意图明确的时刻，
   * 不在启动时骚扰）。桌面桥可用走插件权限；桥异常回退 Web 层。
   * 本会话只问一次；拒绝后不再申请。
   * @returns {Promise<boolean>} 是否已获授权
   */
  async function ensurePermission() {
    if (st.asked || st.denied) return st.granted;
    st.asked = true;
    const bridge = notificationBridge(env.getTauri());
    if (bridge) {
      const r = await desktopNotifyPermission(bridge, true);
      if (r === 'granted') { st.granted = true; return true; }
      if (r === 'denied') { st.denied = true; return false; }
      // default：用户没作答 —— 桌面层已经弹过框，不再回退 Web 层二次弹框
      if (r === 'default') return false;
      // unavailable：桥调用异常 → 回退 Web 层申请
    }
    const N = env.getNotification();
    if (!N) return false;   // 环境不支持通知：静默
    if (N.permission === 'granted') { st.granted = true; return true; }
    if (N.permission === 'denied') { st.denied = true; return false; }
    try {
      const r = await N.requestPermission();
      if (r === 'granted') st.granted = true;
      if (r === 'denied') st.denied = true;
    } catch { /* 环境拒绝弹框：保持未授权 */ }
    return st.granted;
  }

  /**
   * 页面不可见时发通知（可见直接返回，绝不打扰）。
   * 降级链：标签页角标（无条件）→ 桌面层（桥可用且已授权）→ Web 层。
   * 此处绝不弹权限框 —— 申请只发生在 ensurePermission。
   * @returns {Promise<'visible'|'desktop'|'web'|'denied'|'skipped'>} 走了哪条分支
   */
  async function notifyIfHidden(body, { title = env.appTitle } = {}) {
    if (!env.doc || env.doc.hidden !== true) return 'visible';
    markTitle();   // 角标不依赖通知权限，先落地
    if (st.denied) return 'denied';   // 拒绝后不再骚扰
    const bridge = notificationBridge(env.getTauri());
    if (bridge) {
      const perm = await desktopNotifyPermission(bridge, false);
      if (perm === 'granted' && await desktopSendNotification(bridge, title, body)) {
        return 'desktop';   // 桌面系统 toast 点击由壳负责激活窗口
      }
      if (perm === 'denied') { st.denied = true; return 'denied'; }
      // default（尚未申请）或桥发送失败 → 回退 Web 层
    }
    const N = env.getNotification();
    if (!N || N.permission !== 'granted') {
      if (N?.permission === 'denied') st.denied = true;
      return 'skipped';   // Web 层无权限：保持沉默
    }
    try {
      const n = new N(title, { body });
      // 点击通知：聚焦窗口并收起通知
      n.onclick = () => {
        env.focus();
        try { n.close(); } catch { /* 部分实现无 close */ }
      };
      return 'web';
    } catch {
      return 'skipped';   // 构造失败（如移动端需 ServiceWorker）：静默降级
    }
  }

  /** run() 结束通知：done=完成；paused=有失败/中断待处理；其余=异常中断。 */
  function runEnded(phase) {
    if (phase === 'done') return notifyIfHidden('流水线已完成，全部步骤通过');
    if (phase === 'paused') return notifyIfHidden('流水线已暂停：有步骤失败或被中断，需要你处理');
    return notifyIfHidden('流水线执行中断，需要你查看');
  }

  /** 审批卡出现时的通知。 */
  function approvalNeeded(text = '有一张审批卡等待你的确认') {
    return notifyIfHidden(text);
  }

  /** 只做标签页角标（页面不可见时），不发系统通知 —— 「后台通知」关闭时用。 */
  function markIfHidden() {
    if (!env.doc || env.doc.hidden !== true) return false;
    markTitle();
    return true;
  }

  return { attach, ensurePermission, notifyIfHidden, runEnded, approvalNeeded, markIfHidden };
}

/* ---- 默认单例（app.js 接线用；单测请用 createNotifier 注入假环境） ---- */
let singleton = null;
function inst() {
  if (!singleton) singleton = createNotifier();
  return singleton;
}

/**
 * 接线入口：挂标题角标复原监听；给了 onAdminRuns 则启动 30 秒一次的
 * /api/admin/runs 运维视图轮询（给会话徽标；不可达时回调 null，只按本地状态）。
 */
export function init({ onAdminRuns } = {}) {
  inst().attach();
  if (onAdminRuns) startAdminPoll(onAdminRuns);
}

/* 三个单例入口改道通知中心（app.js 调用点不变）：
   - 回合结束 / 审批卡 → 先在抽屉留痕（mock 演示模式下 SSE 不可用，这是唯一收集源；
     真实后端下与 SSE 事件在 60s 聚合窗口内合并，不产生重复条目），
     原生通知按「后台通知」开关由中心统一决定，页面不可见时角标无条件保留；
   - ensurePermission 不再无条件弹权限框 —— 权限申请的唯一入口是抽屉里的开关，
     开关关闭时首次执行流水线不该打扰用户。 */
export const ensurePermission = () => (
  centerInst?.settings.get().browser ? inst().ensurePermission() : Promise.resolve(false)
);
export const runEnded = (phase) => {
  centerInst?.feedPhase(phase);
  return inst().markIfHidden();
};
export const approvalNeeded = (text = '有一张审批卡等待你的确认') => {
  centerInst?.feedApproval(text);
  return inst().markIfHidden();
};

/* ============================================================
   二、会话状态归组（纯函数，renderSessions 的数据层）
   四组闭集：进行中（running/busy）｜需要你（失败/暂停/待审批）
            ｜已完成｜空闲 —— Devin Kanban / Copilot agents 面板式。
   ============================================================ */

export const GROUP_ORDER = ['active', 'attention', 'done', 'idle'];
export const GROUP_LABEL = { active: '进行中', attention: '需要你', done: '已完成', idle: '空闲' };

/* 服务端 run 状态（core/store.py set_run_status 调用方的闭集）→ 分组。
   ready = 计划就绪待用户批准执行 → 归「需要你」（待审批语义）。 */
const ADMIN_GROUP = {
  running: 'active', planning: 'active',
  failed: 'attention', paused: 'attention', interrupted: 'attention', ready: 'attention',
  done: 'done',
};

/**
 * 单个会话归组。优先级：当前会话的实时状态 > 运维视图 > 本地 tone 兜底。
 * @param {{id:string, tone?:string}} sess state.js SESSIONS 条目
 * @param {{currentId?:string, busy?:boolean, phase?:string}} [live] 当前会话实时状态（app.js 的 S）
 * @param {string} [adminStatus] /api/admin/runs 里该会话最新 run 的 status
 * @returns {'active'|'attention'|'done'|'idle'}
 */
export function classifySession(sess, live = {}, adminStatus = undefined) {
  if (sess && sess.id === live.currentId) {
    if (live.busy) return 'active';
    if (live.phase === 'paused') return 'attention';
    if (live.phase === 'done') return 'done';
    // 其余相位（idle/planning）：继续看运维视图与本地 tone
  }
  if (adminStatus && ADMIN_GROUP[adminStatus]) return ADMIN_GROUP[adminStatus];
  const tone = sess?.tone;
  if (tone === 'run') return 'active';
  if (tone === 'stale' || tone === 'warn') return 'attention';
  if (tone === 'done' || tone === 'ok') return 'done';
  return 'idle';
}

/**
 * 会话列表 → 有序分组（空组剔除）。
 * @param {Array} sessions state.js 的 SESSIONS
 * @param {object} [live] 同 classifySession
 * @param {Object<string,string>|null} [adminMap] summarizeAdminRuns 的产物；null=运维视图不可达
 * @returns {Array<{key:string, label:string, items:Array}>}
 */
export function groupSessions(sessions, live = {}, adminMap = null) {
  const buckets = { active: [], attention: [], done: [], idle: [] };
  for (const s of sessions || []) {
    const status = adminMap ? adminMap[s.id] : undefined;
    buckets[classifySession(s, live, status)].push(s);
  }
  return GROUP_ORDER
    .filter((k) => buckets[k].length)
    .map((k) => ({ key: k, label: GROUP_LABEL[k], items: buckets[k] }));
}

/**
 * /api/admin/runs 数组 → { session_id: 最新 run 的 status }。
 * 同会话多个 run 时按 created_at 取最新（与 api/app.py 的「最新 run」口径一致）。
 * 非数组载荷返回 null（调用方视作不可达）。
 */
export function summarizeAdminRuns(runs) {
  if (!Array.isArray(runs)) return null;
  const latest = {};
  for (const r of runs) {
    if (!r || !r.session_id) continue;
    const at = r.created_at ?? 0;
    if (!(r.session_id in latest) || at >= latest[r.session_id].at) {
      latest[r.session_id] = { at, status: r.status };
    }
  }
  return Object.fromEntries(Object.entries(latest).map(([k, v]) => [k, v.status]));
}

/**
 * 30 秒一次拉运维视图给会话徽标（可选增强）。
 * 后端不可达 / 非 2xx / 载荷非法 → onRuns(null)，UI 只按本地状态归组；
 * 恢复可达后自动回到服务端口径。file:// 场景（无后端）直接不启动。
 * @param {(map: Object<string,string>|null) => void} onRuns
 * @param {{intervalMs?:number, fetchFn?:typeof fetch}} [opts]
 * @returns {() => void} 停止轮询
 */
export function startAdminPoll(onRuns, opts = {}) {
  const intervalMs = opts.intervalMs ?? 30000;
  const doFetch = opts.fetchFn
    || (typeof fetch !== 'undefined' ? fetch.bind(globalThis) : null);
  if (!doFetch) return () => {};
  if (!opts.fetchFn && typeof location !== 'undefined' && location.protocol === 'file:') {
    return () => {};   // file:// 打开：无后端可言，不空转
  }
  let timer = null;
  let stopped = false;
  const tick = async () => {
    let map = null;
    try {
      const resp = await doFetch('/api/admin/runs');
      if (resp?.ok) map = summarizeAdminRuns(await resp.json());
    } catch { /* 不可达：map 保持 null */ }
    if (stopped) return;
    onRuns(map);
    timer = setTimeout(tick, intervalMs);
  };
  tick();
  return () => { stopped = true; clearTimeout(timer); };
}

/* ============================================================
   三、通知中心（铃铛 + 抽屉 + 可选后台通知/提示音）
   —— 真实 InSAR 处理动辄数十分钟，用户不守着页面也要能回来看到
      「哪个会话的哪次 run 出了什么事」。

   收集规则（订阅全局 SSE /api/events，事件闭集见 loop/events.py）：
     · result                       → run 完成（终态 done；此刻计算证据级，
                                       达到 audited 及以上时在通知上注明达标）
     · step.end 且 exit ≠ 0         → 步骤失败（run 将以 failed/interrupted 收尾）
     · gate_stop                    → 质量门拦停（归失败家族）
     · note[warn] 含 取消/中断/环境停止 → run 中断（driver 中断路径的收尾 note）
     · note[bad]                    → 失败上下文（分诊结论，聚合进失败条目）
     · step.stage / tool.* / thinking / say / plan / candidates / overall /
       budget / reattach / intervention / 普通 note → 噪音，不通知
   聚合：同一会话 60s 窗口内的同家族（完成 | 失败/中断 | 审批）事件
   合并为一条并重置未读 —— 这同时天然去重了「NDJSON 回合流 + SSE 总线」
   双通道与 runEnded 兜底入口对同一结局的重复上报。

   存储：localStorage（上限 200 条 FIFO），刷新后可回溯。
   原生通知：仅页面不可见（document.hidden）且「后台通知」开关打开时发，
   点击聚焦回页面；权限被拒则开关禁用并说明。提示音由 WebAudio 合成
   两个音符（A5→E6），不引入音频文件，默认关。
   ============================================================ */

/* ---------------- 3.1 收集规则（纯函数） ---------------- */

/** driver 中断路径收尾 note 的特征词（「第 N 步已取消 · 可续跑」「执行环境停止」）。 */
const INTERRUPT_RE = /取消|中断|环境停止/;

/** 通知家族：同家族才允许 60s 聚合（防止「上一个 run 失败 + 下一个 run 完成」被并成一条）。 */
const KIND_FAMILY = {
  run_done: 'done', run_failed: 'fail', run_interrupted: 'fail', approval: 'ask',
};

/** 失败家族内的精化：中断比失败更具体（取消路径先看到 step.end exit≠0 再看到中断 note）。 */
function refineKind(oldKind, newKind) {
  if (oldKind === 'run_interrupted' || newKind === 'run_interrupted') return 'run_interrupted';
  return newKind;
}

/**
 * SSE 事件 → 通知判定（收集规则的唯一实现，表驱动闭集）。
 * @param {object} ev 总线事件（{t, ...}，形状见 loop/events.py 事件工厂）
 * @returns {{kind:string, title:string, body:string, stepId?:number}|null} null=噪音不通知
 */
export function classifyEvent(ev) {
  if (!ev || typeof ev !== 'object') return null;
  switch (ev.t) {
    case 'result':
      return { kind: 'run_done', title: '运行完成', body: '全部步骤通过，产物与报告已就绪' };
    case 'step.end':
      if (ev.exit === 0) return null;
      return {
        kind: 'run_failed', stepId: ev.stepId,
        title: `第 ${ev.stepId} 步失败`, body: '运行已停链，等待失败处置',
      };
    case 'gate_stop':
      return {
        kind: 'run_failed', stepId: ev.stepId ?? undefined,
        title: ev.stepId ? `第 ${ev.stepId} 步被质量门拦停` : '质量门拦停',
        body: ev.text || '指标未达标，运行已停链',
      };
    case 'note':
      if (ev.tone === 'bad') return { kind: 'run_failed', title: '运行失败', body: ev.text || '' };
      if (ev.tone === 'warn' && INTERRUPT_RE.test(ev.text || '')) {
        return { kind: 'run_interrupted', title: '运行已中断', body: ev.text || '' };
      }
      return null;
    default:
      return null;   // step.stage 等执行噪音一律静默（规则表见节首注释）
  }
}

/** app.js 回合结束回调（runEnded）→ 通知判定：mock 演示模式下的唯一收集源。 */
export function phaseVerdict(phase) {
  if (phase === 'done') return { kind: 'run_done', title: '运行完成', body: '流水线已完成，全部步骤通过' };
  if (phase === 'paused') {
    return { kind: 'run_failed', title: '运行已暂停', body: '有步骤失败或被中断，需要你处理' };
  }
  return { kind: 'run_interrupted', title: '运行已中断', body: '流水线执行意外中断，需要你查看' };
}

/* ---------------- 3.2 存储层（localStorage · 200 条 FIFO · 60s 聚合） ---------------- */

const STORE_KEY = 'ia-notify-v1';
const CFG_KEY = 'ia-notify-cfg-v1';
let idSeq = 0;

/**
 * 通知存储：聚合、未读计数、FIFO 上限、localStorage 持久，全部可注入可单测。
 * @param {object} [opts]
 * @param {Storage|object|null} [opts.storage] localStorage 等价物；null=仅内存
 * @param {number} [opts.limit] 条数上限（默认 200，FIFO 淘汰最旧）
 * @param {number} [opts.windowMs] 聚合窗口（默认 60s）
 */
export function createNotifyStore(opts = {}) {
  const storage = opts.storage !== undefined ? opts.storage
    : (typeof localStorage !== 'undefined' ? localStorage : null);
  const limit = opts.limit ?? 200;
  const windowMs = opts.windowMs ?? 60000;
  const key = opts.key || STORE_KEY;
  const subs = new Set();

  let items = [];
  try {
    const raw = storage?.getItem(key);
    const data = raw ? JSON.parse(raw) : null;
    if (Array.isArray(data?.items)) items = data.items.filter((it) => it && it.id && it.kind);
  } catch { /* 损坏的持久化数据：从空列表重建 */ }

  function persist() {
    try { storage?.setItem(key, JSON.stringify({ v: 1, items })); }
    catch { /* 配额满/隐私模式：保持内存态，不阻断 UI */ }
  }

  function emitChange(type, payload) {
    for (const fn of subs) fn({ type, ...payload });
  }

  /**
   * 收一条通知判定。同会话 60s 窗口内的同家族条目合并（count+1、置未读、
   * 移到列表尾＝最新），否则新建；超上限从头部 FIFO 淘汰。
   * @param {{kind, title, body, sessionId, sessionName?, evidence?, at?}} v
   * @returns {{entry:object, isNew:boolean}}
   */
  function ingest(v) {
    const at = v.at ?? Date.now();
    const fam = KIND_FAMILY[v.kind];
    let entry = null;
    for (let i = items.length - 1; i >= 0; i--) {
      const it = items[i];
      if (it.sessionId !== v.sessionId) continue;
      if (at - it.lastAt <= windowMs && KIND_FAMILY[it.kind] === fam) {
        entry = it;
        items.splice(i, 1);   // 移尾：聚合更新视作最新
      }
      break;   // 只看该会话最近一条，更早的窗口必然已过期
    }
    let isNew = false;
    if (entry) {
      const changed = entry.kind !== v.kind;
      entry.kind = refineKind(entry.kind, v.kind);
      if (changed) entry.title = v.title;
      if (v.body) entry.body = v.body;
      if (v.evidence) entry.evidence = v.evidence;
      entry.count += 1;
      entry.lastAt = at;
      entry.read = false;
    } else {
      isNew = true;
      entry = {
        id: `n${at.toString(36)}-${(idSeq++).toString(36)}`,
        kind: v.kind, title: v.title, body: v.body || '',
        sessionId: v.sessionId, sessionName: v.sessionName || v.sessionId,
        runId: v.runId || null, evidence: v.evidence || null,
        count: 1, firstAt: at, lastAt: at, read: false,
      };
    }
    items.push(entry);
    while (items.length > limit) items.shift();
    persist();
    emitChange('ingest', { entry, isNew });
    return { entry, isNew };
  }

  function update(id, patch) {
    const entry = items.find((it) => it.id === id);
    if (!entry) return null;
    Object.assign(entry, patch);
    persist();
    emitChange('change', { entry });
    return entry;
  }

  function markRead(id) {
    const entry = items.find((it) => it.id === id);
    if (!entry || entry.read) return;
    entry.read = true;
    persist();
    emitChange('change', { entry });
  }

  function markAllRead() {
    let dirty = false;
    for (const it of items) { if (!it.read) { it.read = true; dirty = true; } }
    if (!dirty) return;
    persist();
    emitChange('change', {});
  }

  function clear() {
    if (!items.length) return;
    items = [];
    persist();
    emitChange('change', {});
  }

  return {
    ingest, update, markRead, markAllRead, clear,
    list: () => items.slice(),
    unread: () => items.reduce((n, it) => n + (it.read ? 0 : 1), 0),
    subscribe: (fn) => { subs.add(fn); return () => subs.delete(fn); },
  };
}

/** 开关设置（后台通知/提示音），localStorage 持久，默认全关。 */
export function createNotifySettings(opts = {}) {
  const storage = opts.storage !== undefined ? opts.storage
    : (typeof localStorage !== 'undefined' ? localStorage : null);
  const key = opts.key || CFG_KEY;
  let cfg = { browser: false, sound: false };
  try {
    const raw = storage?.getItem(key);
    if (raw) cfg = { ...cfg, ...JSON.parse(raw) };
  } catch { /* 损坏配置回默认 */ }
  return {
    get: () => ({ ...cfg }),
    set(patch) {
      cfg = { ...cfg, ...patch };
      try { storage?.setItem(key, JSON.stringify(cfg)); } catch { /* 同上 */ }
      return { ...cfg };
    },
  };
}

/* ---------------- 3.3 时间展示（纯函数） ---------------- */

/** 相对时间：刚刚 / N 分钟前 / N 小时前 / HH:MM（超过一天）。 */
export function relativeTime(ts, now = Date.now()) {
  const d = Math.max(0, now - ts);
  if (d < 45e3) return '刚刚';
  if (d < 3600e3) return `${Math.max(1, Math.floor(d / 60e3))} 分钟前`;
  if (d < 86400e3) return `${Math.floor(d / 3600e3)} 小时前`;
  const t = new Date(ts);
  return `${String(t.getHours()).padStart(2, '0')}:${String(t.getMinutes()).padStart(2, '0')}`;
}

/** 按天分组标签：今天 / 昨天 / M月D日（跨年补年份）。 */
export function dayLabel(ts, now = Date.now()) {
  const day = (x) => { const d = new Date(x); return [d.getFullYear(), d.getMonth(), d.getDate()]; };
  const [y1, m1, d1] = day(ts);
  const [y2, m2, d2] = day(now);
  if (y1 === y2 && m1 === m2 && d1 === d2) return '今天';
  if (y1 === y2 && m1 === m2 && d2 - d1 === 1) return '昨天';
  const prev = new Date(now); prev.setDate(prev.getDate() - 1);
  const [py, pm, pd] = day(prev.getTime());
  if (y1 === py && m1 === pm && d1 === pd) return '昨天';   // 跨月/跨年的昨天
  return `${y1 !== y2 ? `${y1}年` : ''}${m1 + 1}月${d1}日`;
}

/* ---------------- 3.4 提示音（WebAudio 两音符，无音频文件） ---------------- */

let audioCtx = null;

/** 极简提示音：A5→E6 上行琶音，约 0.3s，音量克制。环境不支持时静默返回 false。 */
export function chime() {
  const AC = typeof AudioContext !== 'undefined' ? AudioContext
    : (typeof webkitAudioContext !== 'undefined' ? webkitAudioContext : null);
  if (!AC) return false;
  try {
    audioCtx = audioCtx || new AC();
    if (audioCtx.state === 'suspended') audioCtx.resume().catch(() => {});
    const t0 = audioCtx.currentTime;
    for (const [freq, at] of [[880, 0], [1318.5, 0.11]]) {
      const osc = audioCtx.createOscillator();
      const gain = audioCtx.createGain();
      osc.type = 'sine';
      osc.frequency.value = freq;
      gain.gain.setValueAtTime(0.0001, t0 + at);
      gain.gain.exponentialRampToValueAtTime(0.05, t0 + at + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, t0 + at + 0.18);
      osc.connect(gain).connect(audioCtx.destination);
      osc.start(t0 + at);
      osc.stop(t0 + at + 0.2);
    }
    return true;
  } catch {
    return false;   // 自动播放策略拒绝等：静默，不影响抽屉本身
  }
}

/* ---------------- 3.5 抽屉 UI 与接线 ---------------- */

/** 通知种类 → 图标/色调/中文（图标取自 dom.js ICON 闭集）。 */
const KIND_META = {
  run_done: { icon: 'check', cls: 'is-done', zh: '运行完成' },
  run_failed: { icon: 'warn', cls: 'is-fail', zh: '运行失败' },
  run_interrupted: { icon: 'stop', cls: 'is-stop', zh: '运行中断' },
  approval: { icon: 'shield', cls: 'is-ask', zh: '等待审批' },
};

/**
 * 创建通知中心（非单例工厂，check 脚本可注入假环境多次构建）。
 * 环境注入项全部可缺省：真实页面里零参调用即可。
 * @param {object} [opts]
 * @param {Document|null} [opts.doc]
 * @param {Storage|null} [opts.storage]
 * @param {object} [opts.notifier] createNotifier 实例（原生通知与角标的执行者）
 * @param {() => (typeof Notification|null)} [opts.getNotification] 权限状态检查用
 * @param {() => {id:string, name:string}} [opts.session] 当前会话解析
 * @param {() => string|null} [opts.evidence] result 时刻的证据级标签（达标才返回）
 * @param {(entry:object) => void} [opts.jump] 点击通知的跳转实现（缺省走会话侧栏）
 * @returns {object|null} 中心句柄；无 DOM 环境返回 null
 */
export function createNotifyCenter(opts = {}) {
  const doc = opts.doc !== undefined ? opts.doc
    : (typeof document !== 'undefined' ? document : null);
  if (!doc || typeof doc.createElement !== 'function') return null;

  const storage = opts.storage !== undefined ? opts.storage
    : (typeof localStorage !== 'undefined' ? localStorage : null);
  const store = createNotifyStore({ storage });
  const settings = createNotifySettings({ storage });
  const notifier = opts.notifier || inst();
  const getN = opts.getNotification
    || (() => (typeof Notification !== 'undefined' ? Notification : null));
  const session = opts.session || (() => ({
    id: S.sessionId,
    name: SESSIONS.find((s) => s.id === S.sessionId)?.name || S.sessionId,
  }));
  // 证据级达标判定：result 时刻算证据天花板，>= audited 才通知（与 app.js 的口径一致）
  const evidence = opts.evidence || (() => {
    const lv = evidenceCeiling().level;
    return lv >= LADDER.indexOf('audited') ? LADDER[lv] : null;
  });

  /* ---- DOM 骨架（幂等：已存在同 id 抽屉则直接复用，不重复建） ---- */
  const bell = doc.getElementById('btnNotify');
  if (doc.getElementById('notifyDrawer')) return null;   // 已有实例在管这块 DOM

  // hidden 一律走属性赋值而非 h() 的属性表：浏览器里属性会反射到 attribute
  // （CSS [hidden] 生效），无反射的最小 DOM stub 里 isOpen() 判断也不失真
  const badge = h('span', { class: 'nbadge', 'aria-hidden': 'true' });
  badge.hidden = true;
  bell?.appendChild(badge);

  const scrim = h('button', {
    class: 'notify-scrim', type: 'button',
    'aria-label': '关闭通知中心', tabindex: '-1',
    onclick: () => close(),
  });
  scrim.hidden = true;
  const listHost = h('div', { class: 'nd-list' });
  const unreadLbl = h('span', { class: 'nd-unread' });
  const btnReadAll = h('button', {
    class: 'nd-act', type: 'button',
    onclick: () => { store.markAllRead(); },
  }, '全部已读');
  const btnClear = h('button', {
    class: 'nd-act', type: 'button',
    onclick: () => { store.clear(); toast('已清空通知'); },
  }, '清空');
  const btnClose = h('button', {
    class: 'icon-btn nd-close', type: 'button', 'aria-label': '关闭通知中心 Esc',
    onclick: () => close(),
  }, icon('x'));

  const permNote = h('p', { class: 'nd-note', id: 'ndPermNote' });
  const swBrowser = h('button', {
    class: 'nd-switch', type: 'button', role: 'switch',
    'aria-checked': 'false', 'aria-labelledby': 'ndLblBrowser',
    'aria-describedby': 'ndPermNote',
    onclick: () => toggleBrowser(),
  });
  const swSound = h('button', {
    class: 'nd-switch', type: 'button', role: 'switch',
    'aria-checked': 'false', 'aria-labelledby': 'ndLblSound',
    onclick: () => toggleSound(),
  });

  const drawer = h('aside', {
    class: 'notify-drawer', id: 'notifyDrawer', role: 'dialog',
    'aria-modal': 'true', 'aria-label': '通知中心',
  },
    h('header', { class: 'nd-hd' },
      h('h2', { class: 'nd-title' }, '通知'), unreadLbl,
      h('span', { class: 'nd-grow' }), btnReadAll, btnClear, btnClose),
    h('div', { class: 'nd-settings' },
      h('div', { class: 'nd-set' },
        h('span', { class: 'nd-set-main' },
          h('span', { class: 'nd-set-lbl', id: 'ndLblBrowser' }, '后台通知'),
          h('span', { class: 'nd-set-sub' }, '页面切走时发系统通知，点击可回到本页')),
        swBrowser),
      permNote,
      h('div', { class: 'nd-set' },
        h('span', { class: 'nd-set-main' },
          h('span', { class: 'nd-set-lbl', id: 'ndLblSound' }, '提示音'),
          h('span', { class: 'nd-set-sub' }, '新通知时播放两音符提示（WebAudio 合成）')),
        swSound)),
    listHost);
  drawer.hidden = true;
  doc.body.appendChild(scrim);
  doc.body.appendChild(drawer);

  /* ---- 打开 / 关闭（焦点管理 + Esc + Tab 圈定） ---- */
  let lastFocus = null;

  function isOpen() { return !drawer.hidden; }

  function open() {
    if (isOpen()) return;
    lastFocus = doc.activeElement;
    scrim.hidden = false;
    drawer.hidden = false;
    bell?.setAttribute('aria-expanded', 'true');
    renderList();   // 打开时刷新相对时间
    if (typeof requestAnimationFrame === 'function') {
      requestAnimationFrame(() => drawer.classList.add('open'));
    } else {
      drawer.classList.add('open');
    }
    btnClose.focus?.();
  }

  function close() {
    if (!isOpen()) return;
    drawer.classList.remove('open');
    scrim.hidden = true;
    drawer.hidden = true;
    bell?.setAttribute('aria-expanded', 'false');
    (lastFocus?.isConnected !== false ? lastFocus : bell)?.focus?.();
    lastFocus = null;
  }

  drawer.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { e.stopPropagation(); close(); return; }
    if (e.key !== 'Tab') return;
    // 最小 Tab 圈定：焦点保持在抽屉内（role=dialog + aria-modal 的自洽实现）
    const items = [...drawer.querySelectorAll('button')]
      .filter((b) => !b.disabled && !b.hidden);
    if (!items.length) return;
    const first = items[0];
    const last = items[items.length - 1];
    const active = doc.activeElement;
    if (e.shiftKey && active === first) { e.preventDefault(); last.focus?.(); }
    else if (!e.shiftKey && active === last) { e.preventDefault(); first.focus?.(); }
  });

  bell?.addEventListener('click', () => (isOpen() ? close() : open()));

  /* ---- 跳转：切到所属会话并滚动到轨迹流末尾（run 结局都在末尾） ---- */
  const jump = opts.jump || ((entry) => {
    close();
    let switched = false;
    if (entry.sessionId && entry.sessionId !== S.sessionId) {
      const esc = (typeof CSS !== 'undefined' && CSS.escape)
        ? CSS.escape(entry.sessionId) : entry.sessionId.replace(/"/g, '\\"');
      const btn = doc.querySelector(`.sess[data-sid="${esc}"] .sess-main`);
      if (!btn) { toast('该会话不在当前列表，无法跳转'); return; }
      btn.click();   // 复用 app.js 的 switchSession 全链路（水合 + SSE 重挂）
      switched = true;
    }
    setTimeout(() => {
      const sc = doc.getElementById('stream');
      if (!sc) return;
      const smooth = typeof matchMedia === 'function'
        && !matchMedia('(prefers-reduced-motion: reduce)').matches;
      try { sc.scrollTo({ top: sc.scrollHeight, behavior: smooth ? 'smooth' : 'auto' }); }
      catch { sc.scrollTop = sc.scrollHeight; }
    }, switched ? 600 : 50);   // 切会话后等历史水合再定位
  });

  /* ---- 渲染 ---- */
  function renderBadge() {
    const n = store.unread();
    badge.textContent = n > 99 ? '99+' : String(n);
    badge.hidden = n === 0;
    bell?.setAttribute('aria-label', n ? `通知中心，${n} 条未读` : '通知中心');
    unreadLbl.textContent = n ? `${n} 条未读` : '';
    btnReadAll.disabled = n === 0;
    btnClear.disabled = store.list().length === 0;
  }

  function itemNode(entry) {
    const meta = KIND_META[entry.kind] || KIND_META.run_done;
    const parts = [relativeTime(entry.lastAt), entry.sessionName || entry.sessionId];
    if (entry.runId) parts.push(`run ${String(entry.runId).slice(0, 8)}`);
    if (entry.evidence) parts.push(`证据级 ${entry.evidence} 达标`);
    return h('div', { role: 'listitem' },
      h('button', {
        class: `nd-item ${meta.cls}${entry.read ? '' : ' is-unread'}`, type: 'button',
        onclick: () => { store.markRead(entry.id); jump(entry); },
      },
        h('span', { class: 'nd-ico', 'aria-hidden': 'true' }, icon(meta.icon)),
        h('span', { class: 'nd-main' },
          h('span', { class: 'nd-t' },
            entry.title,
            entry.count > 1 ? h('span', { class: 'nd-times' }, `×${entry.count}`) : null),
          entry.body ? h('span', { class: 'nd-b' }, entry.body) : null,
          h('span', { class: 'nd-m' }, parts.join(' · '))),
        entry.read ? null : h('span', { class: 'nd-dot', 'aria-hidden': 'true' })));
  }

  function renderList() {
    const items = store.list().sort((a, b) => b.lastAt - a.lastAt);
    if (!items.length) {
      listHost.replaceChildren(h('p', { class: 'nd-empty' },
        'run 结束、步骤失败或证据级达标时会在这里留痕，最多保留 200 条。'));
      return;
    }
    const groups = [];
    for (const it of items) {
      const label = dayLabel(it.lastAt);
      let g = groups[groups.length - 1];
      if (!g || g.label !== label) { g = { label, items: [] }; groups.push(g); }
      g.items.push(it);
    }
    listHost.replaceChildren(...groups.map((g) => h('section', { class: 'nd-group' },
      h('h3', { class: 'nd-day' }, g.label),
      h('div', { role: 'list', 'aria-label': `${g.label}的通知` },
        ...g.items.map(itemNode)))));
  }

  /* ---- 开关 ---- */
  function renderSettings() {
    const cfg = settings.get();
    const N = getN();
    const denied = !!N && N.permission === 'denied';
    const unsupported = !N && !notificationBridge(
      typeof window !== 'undefined' ? window.__TAURI__ : null);
    swBrowser.setAttribute('aria-checked', String(cfg.browser && !denied));
    swBrowser.disabled = denied || unsupported;
    permNote.textContent = denied
      ? '浏览器已拒绝通知权限：请在地址栏的站点设置里重新允许，再回到这里打开。'
      : unsupported ? '当前环境不支持系统通知。' : '';
    permNote.hidden = !permNote.textContent;
    swSound.setAttribute('aria-checked', String(cfg.sound));
  }

  async function toggleBrowser() {
    const cfg = settings.get();
    if (cfg.browser) {
      settings.set({ browser: false });
      renderSettings();
      return;
    }
    const ok = await notifier.ensurePermission();   // 用户手势时刻申请权限（唯一入口）
    if (ok) settings.set({ browser: true });
    renderSettings();
    if (!ok && getN()?.permission !== 'denied') {
      permNote.textContent = '未获通知授权，开关保持关闭；可重试或检查浏览器设置。';
      permNote.hidden = false;
    }
  }

  function toggleSound() {
    const on = !settings.get().sound;
    settings.set({ sound: on });
    renderSettings();
    if (on) chime();   // 开启即预览，一听即知音量
  }

  /* ---- 收集入口 ---- */
  function signal(entry) {
    const cfg = settings.get();
    if (cfg.sound) chime();
    if (cfg.browser) {
      notifier.notifyIfHidden(`${entry.title} · ${entry.body}`.slice(0, 160));
    } else {
      notifier.markIfHidden();   // 开关关闭也保留标签页角标（无权限依赖，不打扰）
    }
  }

  /* 原生通知/提示音按条目防抖：一次失败会连着到 step.end + note[bad]（+中断 note），
     抽屉里聚合成一条，系统通知也只该响一次 —— 等突发平息后再发，届时条目
     已是精化后的最终形态（entry 引用被 ingest 原地更新）。 */
  const signalTimers = new Map();
  const signalDebounceMs = opts.signalDebounceMs ?? 1200;
  function scheduleSignal(entry) {
    clearTimeout(signalTimers.get(entry.id));
    signalTimers.set(entry.id, setTimeout(() => {
      signalTimers.delete(entry.id);
      signal(entry);
    }, signalDebounceMs));
  }

  /** run 归属补全：新条目异步问一次 /api/state（不可达就保持 null，UI 略去该行）。 */
  let fetchRun = null;   // 惰性注入（initNotifyCenter 接 SSE 时一并给）
  function enrich(entry) {
    if (!fetchRun) return;
    fetchRun().then((runId) => {
      if (runId) { store.update(entry.id, { runId }); }
    }).catch(() => {});
  }

  function ingestVerdict(v, extra = {}) {
    const sess = session();
    const { entry, isNew } = store.ingest({
      ...v, sessionId: sess.id, sessionName: sess.name, ...extra,
    });
    if (isNew) enrich(entry);
    scheduleSignal(entry);
    return entry;
  }

  function feedEvent(ev) {
    const v = classifyEvent(ev);
    if (!v) return null;
    // 证据级达标只在 result 时刻有意义（与 app.js 更新 S.evidenceLevel 同时机）
    return ingestVerdict(v, v.kind === 'run_done' ? { evidence: evidence() } : {});
  }

  function feedPhase(phase) {
    const v = phaseVerdict(phase);
    return ingestVerdict(v, v.kind === 'run_done' ? { evidence: evidence() } : {});
  }

  function feedApproval(text) {
    return ingestVerdict({ kind: 'approval', title: '等待审批', body: text || '' });
  }

  store.subscribe(() => { renderBadge(); if (isOpen()) renderList(); });
  renderBadge();
  renderSettings();
  if (settings.get().browser && getN()?.permission === 'denied') {
    settings.set({ browser: false });   // 上次会话之后权限被收回：显式落回关闭态
    renderSettings();
  }

  return {
    store, settings, drawer, bell, badge,
    open, close, isOpen,
    feedEvent, feedPhase, feedApproval,
    setFetchRun: (fn) => { fetchRun = fn; },
    renderSettings,
  };
}

/* ---------------- 3.6 单例接线（index.html / app.js 共用一份） ---------------- */

let centerInst = null;

/**
 * 通知中心单例初始化（幂等：重复调用返回同一实例）。
 * 接线内容：铃铛/抽屉 UI + 全局 SSE 订阅（复用 backend.sse.js 的公开
 * connectEvents，含断线退避重连）+ 会话切换时重挂 SSE + run 归属补全。
 */
export function initNotifyCenter(opts = {}) {
  if (centerInst) return centerInst;
  centerInst = createNotifyCenter(opts);
  if (!centerInst) return null;

  // SSE 订阅：file:// 或 Node 环境静默跳过（mock 模式由 runEnded 入口兜底收集）
  if (typeof EventSource !== 'undefined' && typeof location !== 'undefined') {
    import('./backend.sse.js').then((api) => {
      centerInst.setFetchRun(async () => (await api.fetchState())?.run?.run_id || null);
      let drop = api.connectEvents((ev) => centerInst.feedEvent(ev));
      let lastSession = S.sessionId;
      // 会话切换没有专用广播：steps 域每次变更时比对 sessionId，变了就重挂
      onState('steps', () => {
        if (S.sessionId === lastSession) return;
        lastSession = S.sessionId;
        drop?.();
        drop = api.connectEvents((ev) => centerInst.feedEvent(ev));
      });
    }).catch(() => { /* 后端模块加载失败：抽屉仍可用，仅无实时收集 */ });
  }
  return centerInst;
}

/* 自初始化：真实页面（含 createElement 的 DOM）里随模块加载即接线；
   Node 单测环境（_env.mjs 的哑 document）不满足条件，自动跳过。 */
if (typeof document !== 'undefined' && typeof document.createElement === 'function') {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => initNotifyCenter(), { once: true });
  } else {
    initNotifyCenter();
  }
}
