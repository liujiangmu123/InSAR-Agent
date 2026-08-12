/* ============================================================
   通知与会话状态归组（零 npm 依赖 ES Module）
   —— RESEARCH-agent-ux-2026-08-12 机制 #4（桌面通知 + 标签页角标）
      与机制 #8（会话侧栏状态分组）的数据/逻辑层。

   通知触发约定（app.js 接线 ≤6 行）：
     · run() 结束（done / 失败暂停 / 异常中断）→ runEnded(phase)
     · 审批卡出现 → approvalNeeded()
   只有页面不可见（document.hidden）才发通知；页面可见绝不打扰。

   分层与降级链：
     桌面层（window.__TAURI__ 存在）→ 先探测 __TAURI__.notification
     插件桥是否可用（desktop.js 负责），不可用/异常回退 Web 层的原生
     Notification API。权限申请只发生在首次执行流水线时（惰性），
     拒绝后本会话不再骚扰（denied 记忆）。

   可测试性：createNotifier 的 Notification / document / __TAURI__ /
   聚焦函数全部可注入；分组归类是纯函数 —— tests/js/notify.test.mjs
   在 Node 里直接跑，无需浏览器。
   ============================================================ */
import {
  notificationBridge, desktopNotifyPermission, desktopSendNotification,
} from './desktop.js';

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

  return { attach, ensurePermission, notifyIfHidden, runEnded, approvalNeeded };
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

export const ensurePermission = () => inst().ensurePermission();
export const runEnded = (phase) => inst().runEnded(phase);
export const approvalNeeded = (text) => inst().approvalNeeded(text);

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
