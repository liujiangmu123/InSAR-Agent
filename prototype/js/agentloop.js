/* ============================================================
   agentloop.js —— 多轮自主循环「工作过程可视化」(自初始化模块)

   动机:代理循环核心(并行分支)上线后,一个回合内 Agent 会自主跑
   多个周期(搜索数据/制定计划/执行步骤…)。没有这层可视化,用户只能
   盯着打字指示器干等 —— 不知道 Agent 在想什么、做到第几步、能不能停。

   事件契约(与循环核心互锁,先按契约开发、mock 验证):
     {"t":"agent.cycle","n":i,"max":N,"action":"search_data"|…}  每周期一条
     工具执行复用既有 tool.start / tool.end(name=动作名);
     say = 正常终止;预算耗尽/中断收尾走既有 note。

   事件通路(不改 app.js / backend.sse.js):app.js 的 consume() 对
   未知事件静默丢弃,而 driver 会把回合事件同时发布到 SSE 总线
   (app.js 注释:busy 时总线渲染由 consume 负责、onGlobalEvent 跳过),
   故本模块经 backend.sse.js 公开的 connectEvents 自建订阅,与
   app.js / bridge.js 的连接互不干扰(事件总线支持多订阅者)。
   订阅惰性建立:首次观察到回合开始(#btnStop 露出)才连,空闲页面
   零连接;会话切换后的下一个回合自动换连到新会话。

   生命周期(与 agentloop.check.mjs 的状态机断言一一对应):
     idle --首个 agent.cycle--> active(聊天流当前回合位置插「自主工作中」进度条)
     active --agent.cycle--> active(「第 n/N 步 · 当前:动作」原位更新)
     active --回合结束--> done(进度条原位转摺叠「工作记录」卡)
   回合结束判定:
     · 本地回合(观察到 #btnStop 露出过):以 busy 归零为准 ——
       say 终止/note 收尾/取消/异常最终都走 app.js 的 finally setBusy(false),
       比解析事件语义可靠;say/note 只用于给卡片定「已完成/已收尾」结语;
     · 非本地回合(纯 SSE 旁观,如另一标签页驱动)与 mock:say 立即收卡,
       note 后静默一个窗口再收卡(收尾 note 之后不会再有事件);
       注意中途 note 存在(越界忽略/干预回执等),静默窗依赖后续事件
       (agent.cycle / tool.*)在 8s(quietMs)内到达 disarm;
     · 用户停止:进度条「停止」按钮 → #btnStop.click()(即 app.js 的
       abort → token.cancel 取消链路),收卡如实标「已被用户停止于第 n 步」。

   无该类事件时零渲染;常驻监听只有 #btnStop 的 hidden 观察器(订阅
   时机来源,单个、不增长)——回合内的临时监听与定时器随收卡拆除。
   ============================================================ */
import { h, icon, mmss, sleep } from './dom.js';
import { S } from './state.js';
import { connectEvents, isMockActive } from './backend.sse.js';

/* ============================================================
   纯逻辑层(无 DOM 依赖,check.mjs 可单测)
   ============================================================ */

/** 契约动作闭集 → 中文名 + 图标(emoji 一律 aria-hidden,名称始终有文字)。 */
export const ACTION_META = {
  search_data:  { zh: '搜索数据', ic: '🔎' },
  inspect_file: { zh: '查看文件', ic: '📄' },
  check_env:    { zh: '检查环境', ic: '🩺' },
  list_data:    { zh: '列出数据', ic: '🗂' },
  status:       { zh: '查询状态', ic: '📊' },
  plan:         { zh: '制定计划', ic: '🧭' },
  execute:      { zh: '执行步骤', ic: '▶' },
  set_params:   { zh: '调整参数', ic: '🎚' },
  set_method:   { zh: '切换方法', ic: '🔀' },
  thinking:     { zh: '思考中',   ic: '💭' },
  install_engine: { zh: '安装引擎', ic: '📦' },
  list_files:   { zh: '列举文件', ic: '📂' },
  learn_tool:   { zh: '学习工具', ic: '📘' },
  search_docs:  { zh: '检索文档', ic: '📚' },
  probe_scratch:{ zh: '受控探针', ic: '🧪' },
};

/** 契约外动作兜底:保留原始动作名、不抛错(循环核心可能先于本模块加动作)。 */
export function actionMeta(action) {
  return ACTION_META[action] || { zh: String(action || '未知动作'), ic: '⚙' };
}

/** 毫秒 → 人话时长:秒以下如实标 <1s,其余 mm:ss(与工具卡计时同格式)。 */
export function fmtDur(ms) {
  if (!Number.isFinite(ms) || ms < 0) return '';
  return ms < 1000 ? '<1s' : mmss(ms / 1000);
}

/** ?agentloopmock=1 才启用内置事件剧本(开发/截图通道)。
 *  界面诚实化(0814B W6):查询串开关之外还须叠加演示态判据 demoActive ——
 *  仅 file:// 直开或 API 已回退演示模式(backend.sse.js isMockActive)时为真,
 *  真实后端存活时带 ?agentloopmock=1 也绝不播放无标识的假循环卡。
 *  保持纯函数:探测结果由调用方注入(缺省拒绝),check.mjs 可直测。 */
export function mockEnabled(search, demoActive = false) {
  try {
    return new URLSearchParams(String(search ?? '')).get('agentloopmock') === '1'
      && demoActive === true;
  } catch { return false; }
}

/** 进度条文案(emoji 图标在外层单独渲染,这里保持可播报的纯文字)。 */
export function barText(loop) {
  if (loop.stopRequested) return `停止中… · 第 ${loop.n}/${loop.max} 步`;
  return `自主工作中 · 第 ${loop.n}/${loop.max} 步 · 当前：${actionMeta(loop.action).zh}`;
}

/** 工作记录卡标题行:停止 / 完成 / 收尾三种结语如实区分。 */
export function cardText(loop) {
  const dur = fmtDur((loop.endedAt ?? loop.startedAt) - loop.startedAt);
  if (loop.finalReason === 'stopped') return `工作记录 · 已被用户停止于第 ${loop.n} 步 · 用时 ${dur}`;
  const tail = loop.sawSay ? '已完成' : loop.sawNote ? '已收尾' : '已结束';
  return `工作记录 · 共 ${loop.cycles.length} 步 · 用时 ${dur} · ${tail}`;
}

/** 周期内工具的聚合状态:none(无工具)/ok/bad(取首个非零 exit)/cut(未收尾)。 */
export function cycleExit(cy) {
  if (!cy.tools.length) return { cls: 'none', label: '' };
  const bad = cy.tools.find((t) => t.t1 != null && Number(t.exit) !== 0);
  if (bad) return { cls: 'bad', label: `exit ${bad.exit}` };
  if (cy.tools.some((t) => t.t1 == null || t.cut)) return { cls: 'cut', label: '中断' };
  return { cls: 'ok', label: 'exit 0' };
}

/* ============================================================
   实例:状态机 + 渲染
   ============================================================ */

export function createAgentLoop({ doc = globalThis.document, quietMs = 8000 } = {}) {
  const inst = {
    doc, quietMs,
    loop: null,        // 当前活跃循环(同一时刻至多一个)
    lastLoop: null,    // 最近收卡的循环(测试/调试可查)
    sawReal: false,    // 收到过真实 agent.cycle → mock 不再启用
    mockActive: false,
    disposed: false,
    _drop: null,       // SSE 退订函数
    _subSession: null, // 已订阅的会话 id(切会话后换连)
    _mo: null,         // #btnStop hidden 观察器
  };

  const btnStop = () => inst.doc?.getElementById?.('btnStop') || null;
  const btnStopVisible = () => { const b = btnStop(); return !!b && !b.hidden; };

  /** 条目宿主:轨迹流内层容器;不存在(非应用页/极早期)则零渲染。 */
  function streamInner() {
    return inst.doc?.getElementById?.('stream')?.querySelector?.('.stream-inner') || null;
  }

  /** 贴底跟随:仅当用户本就在底部附近(不打断向上翻阅),与 bridge.js 同策略。 */
  function follow() {
    const host = inst.doc?.getElementById?.('stream');
    if (host && host.scrollHeight - host.scrollTop - host.clientHeight < 120) {
      host.scrollTop = host.scrollHeight;
    }
  }

  /* ---------------- 订阅(自建 SSE,复用 backend.sse.js 公开口) ---------------- */

  function ensureSubscribed() {
    if (inst.disposed) return;
    const sid = S.sessionId;
    if (!sid) return;                                  // 无会话不连:端点会为未知会话建目录
    if (inst._drop && inst._subSession === sid) return;
    inst._drop?.();
    inst._drop = connectEvents((ev) => inst.handleEvent(ev, 'real'));
    inst._subSession = sid;
  }
  inst.ensureSubscribed = ensureSubscribed;

  /** #btnStop 的 hidden 翻转 = 回合开始/结束(app.js setBusy 的唯一 DOM 侧影)。 */
  inst._busyFlip = () => {
    if (inst.disposed) return;
    if (btnStopVisible()) {
      ensureSubscribed();
      if (inst.loop && !inst.loop.finalized) inst.loop.localBusy = true;
    } else if (inst.loop && !inst.loop.finalized && inst.loop.localBusy) {
      finalize(inst.loop.stopRequested ? 'stopped' : inst.loop.sawSay ? 'say'
        : inst.loop.sawNote ? 'note' : 'end');
    }
  };

  /* ---------------- 静默收卡窗(非本地回合的 note 收尾) ---------------- */

  function disarmQuiet(loop) {
    if (loop.quietTimer != null) { clearTimeout(loop.quietTimer); loop.quietTimer = null; }
  }

  function armQuiet(loop) {
    disarmQuiet(loop);
    loop.quietTimer = setTimeout(() => {
      if (inst.loop === loop && !loop.finalized) {
        finalize(loop.stopRequested ? 'stopped' : 'note');
      }
    }, inst.quietMs);
  }

  /* ---------------- 事件入口(SSE 与 mock 共用同一状态机) ---------------- */

  inst.handleEvent = (ev, src = 'real') => {
    if (!ev || inst.disposed) return;
    if (src === 'real' && ev.t === 'agent.cycle') inst.sawReal = true;
    const loop = inst.loop;
    switch (ev.t) {
      case 'agent.cycle':
        onCycle(ev);
        break;
      case 'tool.start':
        if (loop && !loop.finalized) { disarmQuiet(loop); onToolStart(loop, ev); }
        break;
      case 'tool.end':
        if (loop && !loop.finalized) { disarmQuiet(loop); onToolEnd(loop, ev); }
        break;
      case 'say':
        // 本地回合等 busy 归零统一收卡(say 与 setBusy(false) 仅差毫秒),
        // 旁观/mock 没有 busy 侧影,say 即终止信号,立即收卡。
        if (loop && !loop.finalized) {
          loop.sawSay = true;
          if (!loop.localBusy) finalize('say');
        }
        break;
      case 'note':
        if (loop && !loop.finalized) {
          loop.sawNote = true;
          if (!loop.localBusy) armQuiet(loop);
        }
        break;
    }
  };

  /* ---------------- 周期与工具记录 ---------------- */

  function closeCycle(loop, at) {
    const cur = loop.cycles[loop.cycles.length - 1];
    if (cur && cur.endAt == null) cur.endAt = at;
  }

  function onCycle(ev) {
    if (!inst.loop || inst.loop.finalized) startLoop();
    const loop = inst.loop;
    if (!loop) return;                                  // 无宿主:保持零渲染零状态
    disarmQuiet(loop);
    const now = Date.now();
    closeCycle(loop, now);
    loop.n = Number(ev.n) || loop.cycles.length + 1;
    loop.max = Number(ev.max) || loop.max;
    loop.action = String(ev.action || '');
    loop.cycles.push({ n: loop.n, action: loop.action, at: now, endAt: null, tools: [] });
    if (btnStopVisible()) loop.localBusy = true;        // 首个周期先于观察器翻转到达时补记
    paintBar(loop);
    paintLive(loop);
    follow();
  }

  function onToolStart(loop, ev) {
    const cy = loop.cycles[loop.cycles.length - 1];
    if (!cy) return;                                    // 循环外的普通工具事件与本条无关
    const t = {
      id: ev.id ?? null,
      name: String(ev.name || ev.label || ev.verb || ev.cmd || '工具'),
      t0: Date.now(), t1: null, exit: null, summary: '', cut: false,
    };
    cy.tools.push(t);
    if (t.id != null) loop.toolIndex.set(t.id, t);
    paintLive(loop);
  }

  function onToolEnd(loop, ev) {
    const t = ev.id != null ? loop.toolIndex.get(ev.id) : null;
    if (!t) return;
    t.t1 = Date.now();
    t.exit = Number.isFinite(Number(ev.exit)) ? Number(ev.exit) : 0;
    if (ev.summary) t.summary = String(ev.summary);
    paintLive(loop);
  }

  /* ---------------- 进度条(运行中形态) ---------------- */

  function startLoop() {
    const host = streamInner();
    if (!host) return;
    const loop = {
      el: null, txtEl: null, stopBtn: null,
      n: 0, max: 0, action: '',
      cycles: [], toolIndex: new Map(),
      startedAt: Date.now(), endedAt: null,
      localBusy: false, stopRequested: false,
      sawSay: false, sawNote: false,
      finalized: false, finalReason: '',
      quietTimer: null, offStopCapture: null,
    };
    loop.stopBtn = h('button', {
      class: 'alp-stop', type: 'button',
      'aria-label': '停止自主工作(保留断点,可续跑)',
      onclick: () => onStripStop(),
    }, '停止');
    // role=status + aria-live:每个周期播报一次「第 n/N 步 · 当前动作」
    loop.txtEl = h('span', { class: 'alp-txt', role: 'status', 'aria-live': 'polite' });
    loop.rowsEl = h('ol', { class: 'alp-rows alp-live' });
    loop.el = h('div', { class: 'alp turn rise', dataset: { state: 'run' } },
      h('div', { class: 'alp-bar' },
        h('span', { class: 'alp-bot', 'aria-hidden': 'true' }, '🤖'),
        h('span', { class: 'alp-spin', 'aria-hidden': 'true' }, h('i'), h('i'), h('i')),
        loop.txtEl,
        h('span', { class: 'alp-grow' }),
        loop.stopBtn),
      loop.rowsEl);
    host.appendChild(loop.el);
    // 用户绕过本条、直接点 composer 停止钮:同样如实标「已被用户停止」
    const b = btnStop();
    if (b) {
      const onExt = () => {
        if (!loop.finalized && !loop.stopRequested) { loop.stopRequested = true; paintBar(loop); }
      };
      b.addEventListener('click', onExt);
      loop.offStopCapture = () => b.removeEventListener('click', onExt);
    }
    inst.loop = loop;
  }

  function paintBar(loop) {
    if (!loop.el || loop.finalized) return;
    loop.txtEl.textContent = barText(loop);
    if (loop.stopRequested) {
      loop.el.dataset.state = 'stopping';
      loop.stopBtn.disabled = true;
    }
  }

  /** 进度条「停止」:触发既有停止入口(#btnStop → app.js abort → token.cancel)。 */
  function onStripStop() {
    const loop = inst.loop;
    if (!loop || loop.finalized) return;
    loop.stopRequested = true;
    paintBar(loop);
    if (inst.mockActive) { inst.mockActive = false; finalize('stopped'); return; }
    btnStop()?.click();
    // 旁观回合没有本地 busy 侧影可等,静默窗兜底收卡,进度条不悬挂
    if (!loop.localBusy) armQuiet(loop);
  }

  /* ---------------- 工作记录卡(收尾形态) ---------------- */

  function rowNode(loop, cy, live = false) {
    const meta = actionMeta(cy.action);
    const ex = cycleExit(cy);
    const sums = cy.tools.map((t) => t.summary).filter(Boolean);
    const end = cy.endAt ?? loop.endedAt;
    const dur = end != null ? fmtDur(end - cy.at) : '进行中';
    return h('li', { class: live ? 'alp-row is-live' : 'alp-row' },
      h('span', { class: 'alp-ic', 'aria-hidden': 'true' }, meta.ic),
      h('span', { class: 'alp-nm' }, `第 ${cy.n} 步 · ${meta.zh}`),
      h('span', { class: 'alp-du' }, dur),
      ex.cls !== 'none' ? h('span', { class: `alp-st is-${ex.cls}` }, ex.label) : null,
      sums.length ? h('div', { class: 'alp-summ' }, sums.join(' · ')) : null);
  }

  function paintLive(loop) {
    if (!loop.rowsEl || loop.finalized) return;
    const last = loop.cycles.length - 1;
    loop.rowsEl.replaceChildren(...loop.cycles.map((cy, i) =>
      rowNode(loop, cy, i === last && cy.endAt == null)));
  }

  function renderCard(loop) {
    if (!loop.el) return;
    const card = h('details', { class: 'alp-card' },
      h('summary', null,
        h('span', { class: 'cv' }, icon('chevron')),
        h('span', { class: 'alp-bot', 'aria-hidden': 'true' }, '🤖'),
        h('span', { class: 'alp-ttl' }, cardText(loop))),
      h('ol', { class: 'alp-rows' }, ...loop.cycles.map((cy) => rowNode(loop, cy))));
    loop.el.dataset.state = loop.finalReason === 'stopped' ? 'stopped' : 'done';
    loop.el.replaceChildren(card);
    follow();
  }

  function finalize(reason) {
    const loop = inst.loop;
    if (!loop || loop.finalized) return;
    disarmQuiet(loop);
    loop.finalized = true;
    loop.finalReason = reason;
    loop.endedAt = Date.now();
    closeCycle(loop, loop.endedAt);
    for (const t of loop.toolIndex.values()) {
      if (t.t1 == null) { t.cut = true; t.t1 = loop.endedAt; }   // 被停止的工具如实标中断
    }
    loop.offStopCapture?.();
    loop.offStopCapture = null;
    renderCard(loop);
    inst.lastLoop = loop;
    inst.loop = null;
  }

  /* ---------------- 常驻观察器与销毁 ---------------- */

  const b0 = btnStop();
  if (b0 && typeof MutationObserver !== 'undefined') {
    inst._mo = new MutationObserver(() => inst._busyFlip());
    inst._mo.observe(b0, { attributes: true, attributeFilter: ['hidden'] });
    if (!b0.hidden) inst._busyFlip();   // 模块热加载时已在回合中:立即补连
  }

  inst.dispose = () => {
    if (inst.loop && !inst.loop.finalized) finalize('end');
    inst.disposed = true;
    inst._mo?.disconnect();
    inst._mo = null;
    inst._drop?.();
    inst._drop = null;
    inst._subSession = null;
    if (globalThis.__insarAgentLoop === inst) globalThis.__insarAgentLoop = null;
  };

  return inst;
}

/* ============================================================
   mock 开发通道:?agentloopmock=1 且处于演示态(file:// 或 API 已回退
   mock,见 mockEnabled)时的内置事件剧本(6 周期,含 search_data 与
   plan;真实事件到达则永不启用/中途让位)
   ============================================================ */

export const MOCK_SCRIPT = [
  { wait: 0,    ev: { t: 'agent.cycle', n: 1, max: 6, action: 'search_data' } },
  { wait: 250,  ev: { t: 'tool.start', id: 'alm1', name: 'search_data', label: '检索 ASF 归档 · Sentinel-1 Ridgecrest 2019' } },
  { wait: 900,  ev: { t: 'tool.end', id: 'alm1', exit: 0, summary: '命中 12 景 SLC(升轨 T64 · 2019-06-10 ~ 08-15)' } },
  { wait: 350,  ev: { t: 'agent.cycle', n: 2, max: 6, action: 'list_data' } },
  { wait: 200,  ev: { t: 'tool.start', id: 'alm2', name: 'list_data', label: '盘点本地数据目录' } },
  { wait: 550,  ev: { t: 'tool.end', id: 'alm2', exit: 0, summary: 'data/slc 已有 12 景,无需重复下载' } },
  { wait: 350,  ev: { t: 'agent.cycle', n: 3, max: 6, action: 'plan' } },
  { wait: 900,  ev: { t: 'agent.cycle', n: 4, max: 6, action: 'set_params' } },
  { wait: 200,  ev: { t: 'tool.start', id: 'alm4', name: 'set_params', label: '第 5 步滤波 alpha=0.5' } },
  { wait: 400,  ev: { t: 'tool.end', id: 'alm4', exit: 0, summary: 'alpha 0.4 → 0.5 · 指纹重算 · 2 步标 STALE' } },
  { wait: 350,  ev: { t: 'agent.cycle', n: 5, max: 6, action: 'execute' } },
  { wait: 200,  ev: { t: 'tool.start', id: 'alm5', name: 'execute', label: '[05/11] Goldstein 滤波' } },
  { wait: 1500, ev: { t: 'tool.end', id: 'alm5', exit: 0, summary: '滤波完成 · 11 个干涉对' } },
  { wait: 350,  ev: { t: 'agent.cycle', n: 6, max: 6, action: 'status' } },
  { wait: 500,  ev: { t: 'tool.start', id: 'alm6', name: 'status', label: '汇总运行状态' } },
  { wait: 450,  ev: { t: 'tool.end', id: 'alm6', exit: 0, summary: '5/11 步完成 · 无失败' } },
  { wait: 700,  ev: { t: 'say', parts: ['(mock)自主循环演示结束'] } },
];

/**
 * 播放剧本:走与真实事件完全相同的 handleEvent 状态机。
 * stepMs=0 可瞬时走完(check.mjs);真实事件到达或用户停止时中途让位。
 */
export async function playMock(inst, { stepMs = null, wait = sleep } = {}) {
  if (!inst || inst.disposed || inst.sawReal || inst.mockActive) return false;
  inst.mockActive = true;
  for (const step of MOCK_SCRIPT) {
    const ms = stepMs == null ? step.wait : stepMs;
    if (ms > 0) await wait(ms);
    if (!inst.mockActive || inst.sawReal || inst.disposed) return false;
    inst.handleEvent(step.ev, 'mock');
  }
  inst.mockActive = false;
  return true;
}

/* ============================================================
   幂等挂载 + 自初始化
   ============================================================ */

/** 幂等挂载:重复调用(重复 script 标签/热重载)返回同一实例,不叠观察器。 */
export function mountAgentLoop() {
  const g = globalThis;
  if (g.__insarAgentLoop) return g.__insarAgentLoop;
  const inst = createAgentLoop({});
  g.__insarAgentLoop = inst;
  return inst;
}

/* 自初始化:仅浏览器环境(node 校验环境无 window,由测试显式创建实例)。 */
if (typeof window !== 'undefined' && typeof document !== 'undefined') {
  const start = () => {
    try {
      const inst = mountAgentLoop();
      // 稍等一拍:真实事件若已在路上(重连回放),让位不启动。演示态探测放进
      // 定时器内取值:启动探活若在这 600ms 里判定后端不可达(isMockActive
      // 置真),剧本仍可如实播放;真实后端存活则永不启用(mockEnabled 拒绝)。
      setTimeout(() => {
        if (mockEnabled(location.search,
                        location.protocol === 'file:' || isMockActive())) {
          playMock(inst).catch(() => {});
        }
      }, 600);
    } catch { /* 可视化层挂载失败不影响主应用 */ }
  };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
}
