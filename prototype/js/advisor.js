/* ============================================================
   下一步建议卡(advisor.js)· 自初始化,幂等
   run 到达终态(done / failed / interrupted)→ GET /api/advise →
   在聊天流末尾插入「下一步建议」卡:标题 + why + 可点按钮。

   数据契约(api/advisor_router.py):
     GET /api/advise?session=..&run=..
       → { run_id, status, context, suggestions:[{id,title,why,action}] }
     action.kind ∈ chat_prefill(预填输入框,不发送)
                 | api_action(现成端点:显式列表续跑 / resume / 下载包)
                 | open_tab(切 dock 面板,可带步骤号)

   所有权边界(同 skillpanel/notify 先例):不改 app.js / dock.js /
   stream.js —— 直接向 #stream 的 .stream-inner 追加节点;SSE 复用
   backend.sse.js 的公开 connectEvents(带断线退避);open_tab 走 dock.js
   公开 setTab;每 run 只插一次(内存 Set + DOM data-advisor-run 双保险)。
   后端不可达 / file:// / mock 演示:SSE 不建连,零建议零报错。
   ============================================================ */
import { h, icon, toast } from './dom.js';
import { S, on as onState } from './state.js';

/** run 终态闭集(与 report/advisor.TERMINAL_STATUSES 同源)。 */
export const TERMINAL_STATUSES = ['done', 'failed', 'interrupted'];

const STATUS_LABEL = { done: '运行完成', failed: '运行失败', interrupted: '已中断' };

/* ---------------- 终态嗅探:哪些 SSE 事件预示 run 已到终态 ----------------
   driver 的收尾语义:done → result;failed → note[bad]「…失败…」/ gate_stop;
   interrupted → note[warn]「…取消/中断/环境停止…」。嗅探只是触发器,
   权威判定始终是 /api/state 的 run.status(嗅探误报 → state 非终态 → 不插卡)。 */
export function isTerminalHint(ev) {
  if (!ev || typeof ev !== 'object') return false;
  if (ev.t === 'result' || ev.t === 'gate_stop') return true;
  if (ev.t !== 'note') return false;
  const text = String(ev.text || '');
  if (ev.tone === 'bad' && /失败/.test(text)) return true;
  if (ev.tone === 'warn' && /取消|中断|环境停止/.test(text)) return true;
  return false;
}

/* ---------------- 建议卡 DOM ---------------- */

/** action.kind → 按钮文案(open_tab 细分到具体面板名)。 */
export function actionLabel(action) {
  if (!action) return '';
  if (action.kind === 'chat_prefill') return '填入输入框';
  if (action.kind === 'api_action') return action.download ? '下载' : '执行';
  if (action.kind === 'open_tab') {
    const name = { pipeline: '流水线', images: '影像', files: '文件', audit: '审计',
                   report: '报告', trace: '轨迹', term: '终端', env: '环境' }[action.tab];
    return name ? `打开${name}面板` : '打开面板';
  }
  return '';
}

/** 预填话术进输入框:只填不发(用户看清可改写后自己回车)。 */
function fillPrompt(text, doc = document) {
  const inp = doc.getElementById('prompt');
  if (!inp) return false;
  inp.value = text;
  inp.dispatchEvent(new Event('input', { bubbles: true }));
  inp.focus();
  return true;
}

/** open_tab 缺省实现:dock.js 公开 setTab;带步骤号先选中该步(gotoStep 同语义)。 */
function openTab(action) {
  if (!document.getElementById('dockTabs')) return;   // 静态演示页无 dock
  import('./dock.js').then((Dock) => {
    try {
      if (action.step !== null && action.step !== undefined) S.selectedStep = action.step;
      Dock.setTab(action.tab);
    } catch { /* dock 未挂载:静默降级 */ }
  }).catch(() => {});
}

/** api_action 缺省实现:GET 下载开新页;POST 即发即离(run 归服务端所有,
    NDJSON 响应体立即取消 —— 断开只关响应不取消回合,进度经 SSE/面板可见)。 */
async function callApi(action) {
  if (action.method === 'GET') {
    const qs = new URLSearchParams(action.params || {});
    const url = `${action.endpoint}?${qs}`;
    if (action.download) { window.open(url, '_blank'); return true; }
    const resp = await fetch(url);
    return resp.ok;
  }
  const resp = await fetch(action.endpoint, {
    method: action.method || 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(action.body || {}),
  });
  resp.body?.cancel?.().catch?.(() => {});
  return resp.ok;
}

function actionButton(sg, hooks) {
  const action = sg.action || {};
  const label = actionLabel(action);
  if (!label) return null;
  const primary = action.kind === 'chat_prefill' || action.kind === 'api_action';
  const btn = h('button', {
    class: `btn ${primary ? 'btn-pri' : 'btn-gho'} btn-sm`, type: 'button',
    'aria-label': `${sg.title}:${label}`,
    onclick: async () => {
      if (action.kind === 'chat_prefill') {
        (hooks.fillPrompt || fillPrompt)(action.text || '');
        return;   // 只填不发,可反复点击
      }
      if (action.kind === 'open_tab') {
        (hooks.openTab || openTab)(action);
        return;
      }
      // api_action:防重复触发(在途中再点直接忽略),结果就地标注
      if (btn.disabled) return;
      btn.disabled = true;
      btn.textContent = '…';
      let ok = false;
      try { ok = await (hooks.callApi || callApi)(action); } catch { ok = false; }
      if (ok) {
        btn.textContent = action.download ? '已开始下载' : '已发起';
        (hooks.toast || toast)(action.download
          ? '下载已在新页开始'
          : '已发起 · 进度见流水线面板与事件流');
      } else {
        btn.disabled = false;
        btn.textContent = label;
        (hooks.toast || toast)('操作失败:后端不可达或已拒绝,可重试');
      }
    },
  }, label);
  return btn;
}

/** 建议卡节点(纯构造,便于 check.mjs 直接断言 DOM)。 */
export function renderAdvisorCard(data, hooks = {}) {
  const items = (data.suggestions || []).map((sg) => h('div',
    { class: 'item', dataset: { sid: sg.id || '' }, role: 'listitem' },
    h('div', { class: 'main' },
      h('div', { class: 't' }, sg.title || ''),
      sg.why ? h('div', { class: 'why' }, sg.why) : null),
    h('div', { class: 'act' }, actionButton(sg, hooks))));
  return h('div', {
    class: 'advisor turn rise', role: 'group', 'aria-label': '下一步建议',
    dataset: { advisorRun: data.run_id || '' },
  },
    h('div', { class: 'hd' }, icon('bolt'), h('b', null, '下一步建议'),
      h('span', { class: 'st' }, STATUS_LABEL[data.status] || data.status || ''),
      h('span', { class: 'grow' }),
      h('span', { class: 'src' },
        data.polish_source === 'llm' ? '规则生成 · LLM 润色' : '规则生成')),
    data.context ? h('p', { class: 'ctx' }, data.context) : null,
    h('div', { class: 'items', role: 'list' }, ...items));
}

/* ---------------- 终态监听与去重 ---------------- */

/**
 * 建议卡看门人(纯逻辑,依赖全部注入,check.mjs 直接测):
 *   feedEvent(ev)  SSE 事件进来,终态嗅探命中 → 防抖后 evaluate;
 *   evaluate()     问权威状态(/api/state)→ 终态且未展示过 → 拉建议 → 插卡。
 * 去重:每 run 只插一次(shown Set;insert 侧另有 DOM 查重兜底)。
 */
export function createAdvisorWatcher({ fetchState, fetchAdvise, insert,
                                       debounceMs = 800 } = {}) {
  const shown = new Set();
  let timer = null;

  async function evaluate() {
    const state = await fetchState();
    const run = state?.run;
    if (!run || !TERMINAL_STATUSES.includes(run.status)) return false;
    if (shown.has(run.run_id)) return false;
    const data = await fetchAdvise(run.run_id);
    if (!data || !(data.suggestions || []).length) return false;
    if (shown.has(run.run_id)) return false;   // 拉取窗口内的并发 evaluate 竞态
    shown.add(run.run_id);
    insert(data);
    return true;
  }

  function feedEvent(ev) {
    if (!isTerminalHint(ev)) return;
    clearTimeout(timer);
    // 防抖:终态附近事件连发(result → note → report),等 driver 收尾落库后只查一次
    timer = setTimeout(() => { evaluate().catch(() => {}); }, debounceMs);
  }

  return { feedEvent, evaluate, shown };
}

/** 聊天流插入:找 #stream 的 .stream-inner 追加;贴底时跟随滚动。 */
export function insertAdvisorCard(data, hooks = {}, doc = document) {
  const host = doc.getElementById('stream');
  if (!host) return null;
  let inner = host.querySelector('.stream-inner');
  if (!inner) {
    inner = h('div', { class: 'stream-inner' });
    host.appendChild(inner);
  }
  // DOM 级查重(跨实例/会话重挂的兜底):同 run 的卡已在流里就不再插
  const dup = [...inner.querySelectorAll('.advisor')]
    .some((el) => (el.dataset || {}).advisorRun === String(data.run_id || ''));
  if (dup) return null;
  const near = host.scrollHeight - host.scrollTop - host.clientHeight < 60;
  const card = renderAdvisorCard(data, hooks);
  inner.appendChild(card);
  if (near) host.scrollTop = host.scrollHeight;
  return card;
}

/* ---------------- 单例接线(index.html 一行 <script> 即生效) ---------------- */

let advisorInst = null;

/**
 * 自初始化入口(幂等):真实应用页(#stream 存在)才接线。
 * SSE 复用 backend.sse.js 公开 connectEvents;会话切换(无专用广播,
 * 与 notify.js 同法:steps 域变更时比对 sessionId)重挂订阅。
 */
export function initAdvisor() {
  if (advisorInst) return advisorInst;
  if (!document.getElementById('stream')) return null;

  const watcher = createAdvisorWatcher({
    fetchState: async () => {
      const api = await import('./backend.sse.js');
      return api.fetchState();
    },
    fetchAdvise: async (runId) => {
      try {
        const qs = new URLSearchParams({ session: S.sessionId, run: runId });
        const resp = await fetch(`/api/advise?${qs}`);
        if (!resp.ok) return null;
        return await resp.json();
      } catch {
        return null;   // 后端不可达:静默无建议,绝不打断 UI
      }
    },
    insert: (data) => insertAdvisorCard(data),
  });

  if (typeof EventSource !== 'undefined' && typeof location !== 'undefined'
      && location.protocol !== 'file:') {
    import('./backend.sse.js').then((api) => {
      let drop = api.connectEvents((ev) => watcher.feedEvent(ev));
      let lastSession = S.sessionId;
      // 会话切换没有专用广播(与 notify.js 同法):steps 域变更时比对 sessionId
      onState('steps', () => {
        if (S.sessionId === lastSession) return;
        lastSession = S.sessionId;
        drop?.();
        drop = api.connectEvents((ev) => watcher.feedEvent(ev));
      });
    }).catch(() => { /* SSE 模块加载失败:建议卡功能整体静默降级 */ });
  }

  advisorInst = { watcher };
  return advisorInst;
}

/* 自初始化:真实页面随模块加载接线;Node 校验环境(#stream 不在)自动跳过。 */
if (typeof document !== 'undefined' && typeof document.createElement === 'function') {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => initAdvisor(), { once: true });
  } else {
    initAdvisor();
  }
}
