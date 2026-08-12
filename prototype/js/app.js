/* ============================================================
   装配层：事件流 → UI
   backend.mock.js 换成真 SSE 时，这里的 consume() 不用改。
   ============================================================ */
import { h, txt, icon, $, $$, toast, sleep } from './dom.js';
import * as St from './state.js';
import { S, STEP_DEFS, def_, st_, LADDER, SESSIONS, THRESHOLDS, DISK_TIERS } from './state.js';
import * as Stream from './stream.js';
import * as Dock from './dock.js';
import * as API from './backend.sse.js';   // 真实后端;file:// 或后端不可达时自动回退 mock
import { demoLongTaskEvents } from './backend.mock.js';   // §7.4 新条目静态演示（仅 mock）

/* ---------------- 元素引用 ---------------- */
const el = {};
let token = null;                 // 当前取消令牌
const tools = new Map();          // tool id → toolCall api
let currentPlan = null;
let lastChange = null;            // 最近一次方法/参数变更 {stepId, method?, params?}（审批卡向服务端要影响预估用）
let dropEvents = null;            // 全局 SSE 通道的断开函数
const attachments = [];           // 附件演示：只留 {name,size}，不读内容、不上传

/* ============================================================
   启动
   ============================================================ */

/** 未捕获错误不静默：暴露到轨迹流，避免界面停在半状态却看不出原因。 */
function installErrorSurface() {
  window.__errors = [];
  const report = (label, err) => {
    const msg = err?.stack || err?.message || String(err);
    window.__errors.push(`${label}: ${msg}`);
    try {
      Stream.note('bad', h('span', null,
        h('b', null, `${label}：`), (err?.message || String(err)).slice(0, 200)));
      setBusy(false);
    } catch { /* 渲染器本身出错时不再递归 */ }
  };
  window.addEventListener('error', (e) => report('运行时错误', e.error || e.message));
  window.addEventListener('unhandledrejection', (e) => report('未处理的 Promise 拒绝', e.reason));
}

function boot() {
  Object.assign(el, {
    stream: $('#stream'),
    input: $('#prompt'),
    send: $('#btnSend'),
    stop: $('#btnStop'),
    sider: $('#sider'),
    sessions: $('#sessions'),
    scrim: $('#scrim'),
    dock: $('#dock'),
    dockTabs: $('#dockTabs'),
    dockBody: $('#dockBody'),
    status: $('#status'),
    statusText: $('#statusText'),
    subtitle: $('#subtitle'),
    runBtn: $('#btnRun'),
    grip: $('#grip'),
  });

  document.documentElement.dataset.theme = S.theme;
  document.documentElement.style.setProperty('--dock', `${S.dockWidth}px`);

  St.initSteps(5);
  Stream.mount(el.stream);
  Dock.mount({
    tabsEl: el.dockTabs, bodyEl: el.dockBody,
    on: {
      changeMethod: (id, m) => applyMethod(id, m),
      changeParams: (id, p) => applyParams(id, p),
      lightbox: (fig) => Stream.openLightbox(fig, { onDock: () => Dock.openImages() }),
      export: doExport,
    },
  });

  renderSessions();
  renderHero();
  wireChrome();
  wireKeys();
  installErrorSurface();
  watchComposer();
  St.on('steps', () => { Dock.refresh(); paintStatus(); });
  St.on('budget', paintBudget);

  // 必须在 Dock.mount 之后：paintShell 会调 Dock.refresh
  applyResponsive();
  window.addEventListener('resize', applyResponsive);
  paintStatus();
  paintBudget();

  // 后端就绪时：恢复服务端状态与历史对话，并接上全局事件通道（SSE）；
  // file:// 或后端不可达时两者都静默跳过，mock 演示不受影响
  hydrateFromServer();
  connectGlobalEvents();
}

/* ============================================================
   后端会话恢复（§7.4）：/api/state 状态镜像 + /api/chat 历史重放
   + /api/events 全局事件通道（断线自动重连，见 backend.sse.js）
   ============================================================ */
async function hydrateFromServer() {
  const [state, chat] = await Promise.all([API.fetchState(), API.fetchChat()]);
  if (state?.steps?.length) {
    St.syncServerSteps(state.steps);   // 方法/状态/stale 镜像 → 指纹重算 → 广播
    paintStatus();
  }
  // 历史对话只在轨迹流仍是空态时重放，不打断已开始的会话
  if (chat?.length && !S.busy && !el.stream.querySelector('.turn')) {
    Stream.clear();
    for (const m of chat) {
      if (m.role === 'user') Stream.userMsg(m.content);
      else Stream.agentMsg(m.content);
    }
  }
}

function connectGlobalEvents() {
  dropEvents?.();
  dropEvents = API.connectEvents(onGlobalEvent);
}

/**
 * 全局 SSE 只渲染五类带外条目（reattach / intervention / degrade / gate_stop / note）。
 * 回合进行中（S.busy）一律跳过：driver 把每个事件同时 yield 给回合的 NDJSON 流
 * 并发布到总线，双通道都画会重复 —— busy 时由 consume() 负责渲染。
 */
function onGlobalEvent(ev) {
  if (S.busy) return;
  switch (ev.t) {
    case 'reattach': Stream.reattachEntry(ev); break;
    case 'intervention': Stream.interventionEntry(ev); break;
    case 'degrade': Stream.degradeEntry(ev); break;
    case 'gate_stop': Stream.gateStopEntry(ev); break;
    case 'note': Stream.note(ev.tone === 'warn' ? 'stale' : ev.tone || 'info', ev.text); break;
  }
}

function renderHero() {
  Stream.renderHero((p) => { el.input.value = p; submit(); }, runDemoEvents);
}

/**
 * 轨迹流底部留白随 composer 实际高度联动（附件 chip 出现、输入框长高时
 * composer 会变高），避免末尾的候选卡/审批卡被压到不可点区域（实测 bug）。
 */
function watchComposer() {
  const composer = $('.composer');
  if (!composer) return;
  const apply = () => {
    document.documentElement.style.setProperty(
      '--composer-h', `${Math.ceil(composer.getBoundingClientRect().height)}px`);
    Stream.refollow();
  };
  if (window.ResizeObserver) new ResizeObserver(apply).observe(composer);
  apply();
}

/* ============================================================
   顶栏 / 侧栏 / Dock 外壳
   ============================================================ */
function wireChrome() {
  el.send.addEventListener('click', submit);
  el.stop.addEventListener('click', abort);

  el.input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(); }
  });
  el.input.addEventListener('input', () => {
    el.send.disabled = !el.input.value.trim() || S.busy;
    el.input.style.height = 'auto';
    el.input.style.height = `${Math.min(132, el.input.scrollHeight)}px`;
  });

  $('#btnNew').addEventListener('click', reset);
  $('#railChat').addEventListener('click', () => { closeOverlays(); reset(); });
  $('#railSessions').addEventListener('click', toggleSider);
  $('#railDock').addEventListener('click', toggleDock);
  el.scrim.addEventListener('click', closeOverlays);
  $('#btnTheme').addEventListener('click', () => {
    St.setTheme(S.theme === 'light' ? 'dark' : 'light');
    paintTheme();
    Dock.refresh();
  });
  paintTheme();

  $$('#modeSwitch button').forEach((b) => b.addEventListener('click', () => {
    S.mode = b.dataset.mode;
    $$('#modeSwitch button').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
    toast(S.mode === 'guide'
      ? '向导模式：Agent 自动决策，仅在昂贵/破坏性操作前征询'
      : '专家模式：每步方法由你指定，Agent 只给建议');
  }));

  el.runBtn.addEventListener('click', () => askRerun());
  $('#btnClear').addEventListener('click', () => { el.input.value = ''; el.input.focus(); el.send.disabled = true; });
  wireAttach();

  // 灯箱
  $('#lightbox [data-act="close"]').addEventListener('click', Stream.closeLightbox);
  $('#lightbox').addEventListener('click', (e) => { if (e.target.id === 'lightbox') Stream.closeLightbox(); });

  wireGrip();
}

function paintTheme() {
  const b = $('#btnTheme');
  b.replaceChildren(icon(S.theme === 'light' ? 'moon' : 'sun'));
  b.setAttribute('aria-label', S.theme === 'light' ? '切换到深色主题' : '切换到浅色主题');
  b.title = b.getAttribute('aria-label');
}

const NARROW = 1240;
const isNarrow = () => window.innerWidth <= NARROW;
let lastNarrow = null;

/** 宽窄屏切换时调整默认可见性；同宽度区间内不覆盖用户的手动选择。 */
function applyResponsive() {
  const narrow = isNarrow();
  if (narrow !== lastNarrow) {
    lastNarrow = narrow;
    if (narrow) { S.siderOpen = false; S.dockOpen = false; }
    else { S.siderOpen = true; S.dockOpen = true; }
    paintShell();
  }
  // 宽屏下浮层遮罩始终无效
  if (!narrow) el.scrim.hidden = true;
}

/** 侧栏与 Dock 的可见性只由状态驱动，一处落地。 */
function paintShell() {
  el.sider.hidden = !S.siderOpen;
  el.dock.hidden = !S.dockOpen;
  $('#railSessions').setAttribute('aria-pressed', String(S.siderOpen));
  $('#railDock').setAttribute('aria-pressed', String(S.dockOpen));
  el.scrim.hidden = !(isNarrow() && (S.siderOpen || S.dockOpen));
  if (S.dockOpen) Dock.refresh();
}

function toggleSider() {
  S.siderOpen = !S.siderOpen;
  if (S.siderOpen && isNarrow()) S.dockOpen = false;   // 窄屏一次只显示一个浮层
  paintShell();
}

function toggleDock() {
  S.dockOpen = !S.dockOpen;
  if (S.dockOpen && isNarrow()) S.siderOpen = false;
  paintShell();
}

function closeOverlays() {
  if (!isNarrow()) return;
  S.siderOpen = false; S.dockOpen = false;
  paintShell();
}

/** 拖拽调整 Dock 宽度。 */
function wireGrip() {
  let startX = 0, startW = 0;
  const move = (e) => {
    St.setDockWidth(startW + (startX - e.clientX));
  };
  const up = () => {
    document.removeEventListener('mousemove', move);
    document.removeEventListener('mouseup', up);
    document.body.classList.remove('resizing');
    el.grip.classList.remove('on');
  };
  el.grip.addEventListener('mousedown', (e) => {
    e.preventDefault();
    startX = e.clientX; startW = S.dockWidth;
    document.addEventListener('mousemove', move);
    document.addEventListener('mouseup', up);
    document.body.classList.add('resizing');
    el.grip.classList.add('on');
  });
  // 键盘也能调宽度
  el.grip.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowLeft') { St.setDockWidth(S.dockWidth + 24); e.preventDefault(); }
    if (e.key === 'ArrowRight') { St.setDockWidth(S.dockWidth - 24); e.preventDefault(); }
  });
}

function wireKeys() {
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      if (!$('#lightbox').hidden) { Stream.closeLightbox(); return; }
      if (isNarrow() && (S.siderOpen || S.dockOpen)) { closeOverlays(); return; }
      if (S.busy) { abort(); return; }
    }
    const mod = e.ctrlKey || e.metaKey;
    if (mod && e.key === 'k') { e.preventDefault(); el.input.focus(); }
    if (mod && e.key === 'b') { e.preventDefault(); toggleDock(); }
    if (mod && e.key === 'j') { e.preventDefault(); St.setTheme(S.theme === 'light' ? 'dark' : 'light'); paintTheme(); Dock.refresh(); }
    if (mod && e.key === 'Enter' && !S.busy) { e.preventDefault(); askRerun(); }
  });
}

function renderSessions() {
  el.sessions.replaceChildren(...SESSIONS.map((s) => h('button', {
    class: 'sess', type: 'button', 'aria-current': String(s.id === S.sessionId),
    onclick: () => {
      if (s.id === S.sessionId) return;
      S.sessionId = s.id;
      renderSessions();
      el.subtitle.textContent = s.name;
      reset();
      hydrateFromServer();      // 新会话的服务端状态与历史
      connectGlobalEvents();    // SSE 重新挂到新会话
      toast(`已切换会话：${s.name}`);
    },
  },
    h('span', { class: 'nm' }, s.name),
    h('span', { class: 'mt' },
      h('span', { class: 'led', style: {
        width: '6px', height: '6px', borderRadius: '50%',
        background: s.tone === 'run' ? 'var(--accent)' : s.tone === 'stale' ? 'var(--stale)' : 'var(--border-strong)',
      } }),
      s.sub))));
}

function paintStatus() {
  const { stale, pending, all } = St.workSummary();
  const running = STEP_DEFS.find((d) => st_(d.id).state === 'running');
  // §7.8：interrupted / orphaned / failed 都属「可从断点处置续跑」
  const resumable = STEP_DEFS.some((d) =>
    ['interrupted', 'orphaned', 'failed'].includes(st_(d.id).state));

  let kind = '', text = '空闲';
  if (S.busy && running) { kind = 'run'; text = `运行中 · 第 ${running.id}/11 步`; }
  else if (S.busy) { kind = 'run'; text = '思考中'; }
  else if (S.phase === 'paused') { kind = 'stale'; text = '已暂停 · 可断点续跑'; }
  else if (stale.length) { kind = 'stale'; text = `${stale.length} 个产物失效`; }
  else if (pending.length && pending.length < STEP_DEFS.length) { kind = 'run'; text = `${pending.length} 步待运行`; }
  else if (!all.length) { kind = 'ok'; text = '全部完成 · 指纹一致'; }

  el.status.className = `tag${kind ? ` is-${kind}` : ''}`;
  el.statusText.textContent = text;

  // 有任何待办（失效或未运行）就露出运行按钮，文案区分三种语义
  el.runBtn.hidden = S.busy || !all.length;
  el.runBtn.querySelector('.lbl').textContent =
    resumable ? '从断点继续' : stale.length ? '重跑失效步骤' : '运行流水线';
  el.runBtn.classList.toggle('btn-wrn', resumable || stale.length > 0);
  el.runBtn.classList.toggle('btn-pri', !resumable && stale.length === 0);
}

/* ============================================================
   顶栏磁盘预算条（§7.5 三级规则）：
     > 20G 不显示；8–20G 灰字「磁盘 XXG↓」；< 8G 橙色 ⚠ + [清理]
   ============================================================ */
function fmtGB(gb) {
  return `${gb < 10 ? gb.toFixed(1) : Math.round(gb)}G`;
}

function paintBudget() {
  const box = $('#budget');
  if (!box) return;
  const gb = S.diskFreeGB;
  if (gb > DISK_TIERS.hide) { box.hidden = true; return; }
  box.hidden = false;
  if (gb >= DISK_TIERS.warn) {
    box.className = 'budget';
    box.title = `E: 工作区剩余 ${fmtGB(gb)}（示意值）· 低于 ${DISK_TIERS.hide}G 后常态显示`;
    box.replaceChildren(txt(`磁盘 ${fmtGB(gb)}↓`));
  } else {
    box.className = 'budget is-warn';
    box.title = `E: 工作区剩余 ${fmtGB(gb)}（示意值）· 低于 ${DISK_TIERS.warn}G 触发告警（§4.10 三级闸门）`;
    box.replaceChildren(
      txt(`⚠ 磁盘 ${fmtGB(gb)}`),
      h('button', {
        class: 'btn btn-wrn btn-sm', type: 'button', 'aria-label': '清理临时产物',
        onclick: () => toast('演示：真实实现按 §4.10 预算分级 GC —— tier-4 临时文件可立即回收约 9G'),
      }, '清理'));
  }
}

/* §7.4 新条目静态演示入口（hero 下方链接） */
async function runDemoEvents() {
  if (S.busy) { toast('Agent 正在运行，请稍后再试'); return; }
  setBusy(true);
  token = new API.Cancel();
  try {
    await consume(demoLongTaskEvents(token));
  } catch (err) {
    if (err?.name !== 'CancelledError') Stream.note('bad', `演示中断：${err.message}`);
  } finally {
    setBusy(false);
  }
}

/* ============================================================
   事件流消费 —— 与真 SSE 同构
   ============================================================ */
async function consume(iter) {
  for await (const ev of iter) {
    switch (ev.t) {
      case 'thinking':
        Stream.thinking(ev.title, h('div', { style: { whiteSpace: 'pre-line' } }, ev.body));
        break;

      case 'say':
        Stream.agentMsg(...ev.parts.map(renderPart));
        break;

      case 'tool.start':
        tools.set(ev.id, Stream.toolCall({
          cmd: ev.cmd, verb: ev.verb, label: ev.label, open: ev.open,
        }));
        break;

      case 'tool.log':
        tools.get(ev.id)?.log(ev.line, ev.tone);
        break;

      case 'tool.progress':
        tools.get(ev.id)?.progress(ev.pct);
        break;

      case 'tool.end':
        tools.get(ev.id)?.finish({
          exit: ev.exit, summary: ev.summary,
          artifacts: ev.artifacts || [],
          onArtifact: (p) => { Dock.openFile(p); ensureDock(); },
        });
        break;

      case 'plan':
        currentPlan = Stream.planPanel(ev.items);
        break;

      case 'candidates':
        Stream.candidateSet({ stepId: ev.stepId, onPick: (m) => applyMethod(ev.stepId, m) });
        break;

      case 'step.start':
        St.setStepState(ev.stepId, 'running');
        break;

      case 'step.end':
        St.setStepState(ev.stepId, ev.exit === 0 ? 'done' : 'failed');
        // 成功完成记一笔本机运行历史（§7.5：预估区间的唯一合法依据）
        if (ev.exit === 0) St.recordRunSample(ev.stepId);
        break;

      case 'overall':
        if (currentPlan && ev.pct >= 100) { currentPlan.set(4, 'd'); currentPlan.set(5, 'd'); }
        break;

      /* ---- §7.4 新条目 ---- */
      case 'degrade':
        // 服务端事件是文本形态（{text, evidenceBefore, evidenceAfter}）直接渲染；
        // mock 剧情才带 stepId/from/to，走降级处置流程
        if (ev.text && !ev.from) { Stream.degradeEntry(ev); break; }
        onDegrade(ev);
        break;

      case 'gate_stop':
        // 服务端事件是文本形态（{text, suggestions:[string]}），建议是文字说明
        if (ev.text && !ev.metric) { Stream.gateStopEntry(ev); break; }
        Stream.gateStopEntry({
          ...ev,
          suggestions: (ev.suggestions || []).map((sg) => ({
            label: sg.label,
            run: ev.demo || !sg.method
              ? () => toast('演示条目：真实场景中此按钮会执行对应处置并重跑受影响步骤')
              : () => applyMethod(ev.stepId, sg.method),
          })),
        });
        break;

      case 'reattach':
        Stream.reattachEntry(ev);
        break;

      case 'intervention':
        Stream.interventionEntry(ev);
        break;

      /* ---- 磁盘预算（§7.5） ---- */
      case 'budget':
        St.setDiskFree(ev.diskFreeGB);
        break;

      case 'note':
        Stream.note(ev.tone === 'warn' ? 'stale' : ev.tone || 'info', ev.text);
        break;

      case 'result':
        currentPlan?.set(4, 'd');
        currentPlan?.set(5, 'd');
        // 不写死 validated —— 有 PENDING 阈值时封顶 audited（§4.13）
        S.evidenceLevel = St.evidenceCeiling().level;
        Stream.resultCard({
          onFile: (p) => { Dock.openFile(p); ensureDock(); },
          onFigure: (f) => Stream.openLightbox(f, { onDock: () => { Dock.openImages(); ensureDock(); } }),
        });
        break;

      case 'report': {
        currentPlan?.set(6, 'd');
        Stream.reportCard({
          onExport: doExport,
          onFigures: () => { Dock.openImages(); ensureDock(); },
        });
        const dg = S.degraded[0];
        Stream.agentMsg(
          '流水线全部完成。断点续跑生效：仅重跑了 STALE 的 ',
          h('b', null, '6–11 步'), '，前 5 步指纹未变直接跳过。',
          ...(dg ? [
            '本次运行含一次降级：第 ', String(dg.stepId), ' 步 ',
            h('code', null, dg.from), ' → ', h('code', null, dg.to),
            `（${dg.failClass}），证据级别因此从 validated 上限下调至 checked。`,
          ] : []),
          '产物、命令、参数、指纹、工具版本与 git 状态已写入 ',
          h('code', null, 'provenance.json'), '，证据级别 ',
          h('b', null, LADDER[S.evidenceLevel]), '。可在右侧「审计」面板下钻完整证据链。');
        break;
      }
    }
  }
}

/* ============================================================
   degrade 处置（§4.12）：向导模式自动降级 + 显式告知；
   专家模式停链，渲染 §7.8 失败卡等用户选择。
   ============================================================ */
function applyDegrade(ev) {
  S.degraded.push({ stepId: ev.stepId, from: ev.from, to: ev.to,
                    failClass: ev.failClass, reason: ev.reason });
  St.setMethod(ev.stepId, ev.to);            // 静默切换（降级路径自己的告知条目随后渲染）
  S.evidenceLevel = St.evidenceCeiling().level;
  Dock.refresh();
  paintStatus();
}

function onDegrade(ev) {
  if (ev.auto) {
    // 向导模式：mock 生成器在本迭代内继续以新方法执行，这里只需应用 + 告知
    applyDegrade(ev);
    Stream.degradeEntry(ev);
    Stream.note('info', '向导模式：已自动降级并继续执行（专家模式下会停链询问，见 §4.12）。');
    return;
  }
  Stream.failureCard({
    stepId: ev.stepId,
    failClass: ev.failClass,
    title: `第 ${ev.stepId} 步失败 · ${def_(ev.stepId)?.name || ''}`,
    detail: ev.detail,
    options: [
      { label: `降级为 ${ev.to}`, kind: 'btn-wrn', done: `已降级为 ${ev.to}，继续执行`,
        cost: `证据级别 ${ev.evidenceFrom} → ${ev.evidenceTo}（${ev.reason}）`,
        run: () => {
          applyDegrade(ev);
          Stream.degradeEntry(ev);
          resumeAfterDisposal();
        } },
      { label: '稍后重试', done: '已保留断点',
        cost: '保留断点与已完成产物，ERA5 恢复后从第 8 步继续',
        run: () => Stream.note('stale',
          `已保留断点。第 ${ev.stepId} 步与下游保持待运行，服务恢复后可从顶栏「从断点继续」重跑。`) },
      { label: '查看完整日志', keepOpen: true,
        run: () => {
          const t = tools.get(`s${ev.stepId}`);
          if (!t) return;
          t.el.open = true;
          const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
          t.el.scrollIntoView({ block: 'center', behavior: reduced ? 'auto' : 'smooth' });
        } },
    ],
  });
}

/** 失败处置后的续跑：mock 直接从断点重新发起剩余步骤。 */
function resumeAfterDisposal() {
  if (S.busy) { toast('Agent 正在运行'); return; }
  const ids = St.workSummary().all;
  if (!ids.length) { toast('没有待运行步骤'); return; }
  run(ids);
}

function renderPart(p) {
  if (typeof p === 'string') return txt(p);
  if (p.code) return h('code', null, p.code);
  if (p.b) return h('b', null, p.b);
  return txt('');
}

function ensureDock() {
  if (!S.dockOpen) toggleDock();
}

/* ============================================================
   附件（演示）：本地选文件 → 输入框上方文件名 chip，可删除；
   不读取内容、不上传，发送时仅把文件名并入消息文本。
   ============================================================ */
function wireAttach() {
  const input = $('#fileInput');
  $('#btnAttach').addEventListener('click', () => {
    toast('演示模式：附件不会上传，仅以文件名 chip 附在消息上');
    input.click();
  });
  input.addEventListener('change', () => {
    for (const f of input.files) {
      if (!attachments.some((a) => a.name === f.name)) {
        attachments.push({ name: f.name, size: f.size });
      }
    }
    input.value = '';   // 允许再次选择同名文件
    renderAttachments();
  });
}

function fmtSize(n) {
  if (n >= 1048576) return `${(n / 1048576).toFixed(1)} MB`;
  if (n >= 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${n} B`;
}

function renderAttachments() {
  const row = $('#attachRow');
  row.hidden = !attachments.length;
  row.replaceChildren(...attachments.map((a, i) => h('span', { class: 'attach-chip' },
    h('span', { class: 'ic' }, icon('clip')),
    h('span', { class: 'nm', title: a.name }, a.name),
    h('span', { class: 'sz' }, fmtSize(a.size)),
    h('button', {
      class: 'rm', type: 'button', 'aria-label': `移除附件 ${a.name}`, title: '移除',
      onclick: () => { attachments.splice(i, 1); renderAttachments(); },
    }, icon('x')))));
}

/* ============================================================
   用户提交
   ============================================================ */
async function submit() {
  let text = el.input.value.trim();
  if (!text || S.busy) return;
  if (attachments.length) {
    text += `\n〔附件 · 演示未上传〕${attachments.map((a) => a.name).join('、')}`;
    attachments.length = 0;
    renderAttachments();
  }
  el.input.value = '';
  el.input.style.height = 'auto';
  el.send.disabled = true;

  if (S.phase === 'idle' && !el.stream.querySelector('.turn')) Stream.clear();
  // 长任务事件演示插入过条目时保留它们，只移除空态 hero
  else el.stream.querySelector('.hero')?.remove();
  Stream.userMsg(text);

  setBusy(true);
  token = new API.Cancel();
  const stopTyping = Stream.typingIndicator();
  try {
    await sleep(320);
    stopTyping();
    await consume(API.runTurn(text, token));
  } catch (err) {
    stopTyping();
    if (err?.name !== 'CancelledError') {
      Stream.note('bad', `执行中断：${err.message}`);
    }
  } finally {
    setBusy(false);
  }
}

function setBusy(on) {
  S.busy = on;
  el.send.hidden = on;
  el.stop.hidden = !on;
  el.send.disabled = on || !el.input.value.trim();
  paintStatus();
}

function abort() {
  if (!token) return;
  token.cancel();
  tools.forEach((t) => t.el.classList.contains('is-run') && t.cancel('已取消 · 可续跑（SIGTERM → wait 5s → SIGKILL）'));
  S.phase = 'paused';
  // §7.8：被取消的运行中步骤标 interrupted（同时挂 stale 供依赖图/面板统计沿用）
  let hit = false;
  STEP_DEFS.forEach((d) => {
    if (st_(d.id).state === 'running') { hit = true; St.setStepState(d.id, 'interrupted', { stale: true }); }
  });
  Stream.note('stale', h('span', null,
    h('b', null, '已取消 · 可续跑'),
    hit ? '。当前步已标记 interrupted；已完成步骤的产物与指纹保留，可从断点继续。'
        : '。已完成步骤的产物与指纹保留，下次运行从断点继续。'), [
    { label: '从断点继续', kind: 'btn-wrn', run: resumeRun },
  ]);
  setBusy(false);
}

/**
 * 从断点继续（§7.8）：对 interrupted/failed/orphaned 直接续跑（取消/失败时
 * 用户已经历一次决策，不再弹审批）；没有断点态时回落到常规审批流程。
 */
function resumeRun() {
  if (S.busy) return;
  const ids = St.workSummary().all;
  if (!ids.length) { toast('没有可续跑的步骤'); return; }
  const resumable = STEP_DEFS.some((d) =>
    ['interrupted', 'orphaned', 'failed'].includes(st_(d.id).state));
  if (!resumable) { askRerun(); return; }
  Stream.note('info', `从断点继续：重跑第 ${ids.join('、')} 步，指纹一致的已完成步骤直接跳过。`);
  run(ids);
}

/* ============================================================
   方法 / 参数变更 → STALE 级联（+ §7.6 撤销窗口 / §7.4 intervention 留痕）
   ============================================================ */
function applyMethod(stepId, methodId) {
  const snap = St.snapshotSteps();
  const before = st_(stepId).method;
  const affected = St.setMethod(stepId, methodId);
  if (!affected.length) return;
  lastChange = { stepId, method: methodId };
  if (interventionDuringRun(`将第 ${stepId} 步方法改为 ${methodId}`)) return;
  explainInvalidation(stepId, before, methodId, affected, 'method', snap);
}

function applyParams(stepId, patch) {
  const snap = St.snapshotSteps();
  const before = { ...st_(stepId).params };
  const affected = St.setParams(stepId, patch);
  if (!affected.length) return;
  lastChange = { stepId, params: { ...patch } };
  const k = Object.keys(patch)[0];
  if (interventionDuringRun(`将第 ${stepId} 步参数改为 ${k}=${patch[k]}`)) return;
  explainInvalidation(stepId, `${k}=${before[k]}`, `${k}=${patch[k]}`, affected, 'param', snap);
}

/** 运行中改配置 → intervention 留痕（§7.4），不打断当前步。 */
function interventionDuringRun(what) {
  if (!(S.busy && S.phase === 'running')) return false;
  const running = STEP_DEFS.find((d) => st_(d.id).state === 'running');
  Stream.interventionEntry({
    mode: 'queue',
    text: `你在第 ${running?.id ?? '?'} 步运行中${what} → 已排入队列，当前步完成后生效（mock：状态已即时更新并级联 STALE）`,
  });
  Dock.refresh();
  paintStatus();
  return true;
}

/* ---- §7.6 撤销窗口：note 内联「撤销」链接，10 秒倒计时，单层撤销 ---- */
const undoCleanups = new Set();

function expireUndos() {
  [...undoCleanups].forEach((fn) => fn(false));
}

function makeUndoLink(snap, describe) {
  expireUndos();   // 同一时刻只保留最近一次变更的撤销窗口，避免跨变更回滚歧义
  let secs = 10;
  let alive = true;
  const cnt = h('span', { class: 'cnt mono', 'aria-hidden': 'true' }, '10s');
  const btn = h('button', {
    class: 'undo-link', type: 'button',
    'aria-label': `撤销本次变更（${describe}），10 秒内有效`,
    onclick: () => {
      if (!alive) return;
      if (S.busy) { toast('Agent 正在运行，不可撤销'); return; }
      cleanup(false);
      St.restoreSteps(snap);
      Dock.refresh();
      paintStatus();
      Stream.note('ok', `已撤销：${describe}。旧值已恢复，相关 STALE 标记已回滚。`);
    },
  }, '撤销 ', cnt);
  const tick = setInterval(() => {
    secs -= 1;
    if (secs <= 0) cleanup(true);
    else cnt.textContent = `${secs}s`;
  }, 1000);
  function cleanup(fade) {
    if (!alive) return;
    alive = false;
    clearInterval(tick);
    undoCleanups.delete(cleanup);
    if (fade) {
      // 渐隐淡出；prefers-reduced-motion 时 tokens.css 全局降级为瞬时
      btn.classList.add('expired');
      setTimeout(() => btn.remove(), 500);
    } else {
      btn.remove();
    }
  }
  undoCleanups.add(cleanup);
  return btn;
}

function explainInvalidation(stepId, from, to, affected, kind, snap = null) {
  const def = def_(stepId);
  const staleIds = affected.filter((id) => st_(id).stale);
  Dock.flashSteps(affected);
  Dock.refresh();

  Stream.agentMsg(
    `已更新第 ${stepId} 步（${def.name}）的${kind === 'method' ? '方法' : '参数'}：`,
    h('code', null, `${from} → ${to}`), '。参数指纹重算为 ',
    h('code', null, st_(stepId).fingerprint),
    '，沿依赖图级联标记 ',
    h('b', { style: { color: 'var(--stale)' } }, `${staleIds.length} 个步骤`),
    ` 为 STALE（第 ${staleIds.join('、')} 步）。`);

  Stream.provenanceCard(stepId, from, to);

  const sum = API.rerunSummary();
  if (sum.ids.length) {
    const parts = [];
    if (sum.stale.length) parts.push(`${sum.stale.length} 个产物失效待覆写`);
    if (sum.pending.length) parts.push(`${sum.pending.length} 步待首次运行`);
    // §7.5 时长诚实化：有本机历史给区间，没有就如实说「时长未知」
    const eta = St.estimateRerunHonest(sum.ids);
    Stream.note(sum.stale.length ? 'stale' : 'info',
      h('span', null,
        h('b', null, parts.join(' · ')),
        `　第 ${sum.ids[0]}–${sum.ids[sum.ids.length - 1]} 步 · ${eta.label}`,
        snap ? txt('　') : null,
        snap ? makeUndoLink(snap, `第 ${stepId} 步 ${from} → ${to}`) : null),
      [{ label: sum.stale.length ? '重新运行' : '开始运行',
         kind: sum.stale.length ? 'btn-wrn' : 'btn-pri', run: () => askRerun() }]);
  }
  paintStatus();
}

/* ============================================================
   审批 → 执行
   ============================================================ */

/* 服务端失效原因 → 中文（core/stale.py 的五类闭集） */
const IMPACT_REASON_ZH = {
  method_changed: '换了方法',
  param_changed: '改了参数',
  upstream_changed: '上游重跑了',
  tool_upgraded: '工具版本变了',
  artifact_missing: '产物被删除',
};

/**
 * 审批卡权威化（§7.6）：本地即时估算先渲染，服务端影响预估（指纹系统推导）
 * 到达后原位更新卡片内容。离线 / 无 run / 服务端视角无变化 → 保持本地估算。
 * 时长诚实化（§7.5）：rerunMinutes 为 null 时如实显示「时长未知」，绝不编数。
 */
async function refineApprovalCard(card, sum) {
  if (!card?.updateRows) return;   // 同族自动通过时没有卡片
  // 预估探针：优先用最近一次变更；没有变更记录时用首个待跑步骤的当前配置
  const probe = lastChange
    || (sum.ids.length
      ? { stepId: sum.ids[0], method: st_(sum.ids[0]).method, params: st_(sum.ids[0]).params }
      : null);
  if (!probe) return;
  const imp = await API.fetchImpact(probe.stepId, { method: probe.method, params: probe.params });
  if (!imp || !card.isConnected) return;
  const ids = (imp.affected || []).map((a) => a.step_id).filter((x) => x != null);
  const reason = IMPACT_REASON_ZH[imp.reason];
  if (!ids.length && !reason) return;   // 服务端视角无失效（no_change），本地估算保持原样
  const rows = [];
  if (ids.length) rows.push(['受影响', `第 ${ids.join('、')} 步（共 ${ids.length} 步 · 服务端指纹推导）`]);
  if (reason) rows.push(['失效原因', reason]);
  rows.push(['耗时', imp.rerunMinutes === null || imp.rerunMinutes === undefined
    ? '时长未知（历史样本不足）'
    : `约 ${Math.max(1, Math.round(imp.rerunMinutes))} 分钟（${imp.rerunBasis || '服务端历史中位数'}）`]);
  card.updateRows(rows);
}

function askRerun() {
  if (S.busy) return;
  const sum = API.rerunSummary();
  if (!sum.ids.length) { toast('全部步骤指纹一致，无需运行'); return; }

  // §7.5 时长诚实化：区间估计只在本机历史 ≥3 次时给出；
  // Ridgecrest 演示本机从未真实跑过 → 如实显示「时长未知 · 首次运行此配置」
  const eta = St.estimateRerunHonest(sum.ids);
  const longTask = eta.known && eta.hiSec > 30 * 60;
  const hasOverwrite = sum.overwrite.length > 0;
  const card = Stream.askApproval({
    title: longTask
      ? '需确认 · 长时任务'
      : hasOverwrite ? '需确认 · 将覆写已有产物' : '需确认 · 执行流水线',
    danger: longTask && hasOverwrite,
    // absorb-F：同指纹族审批可勾选「本会话内不再询问」
    family: `pipeline-run:${hasOverwrite ? 'overwrite' : 'create'}`,
    rows: [
      ['步骤', `第 ${sum.ids.join('、')} 步（共 ${sum.ids.length} 步）`],
      ['命令', sum.ids.map((id) => Dock.buildCmd(id).replace(/^\$ /, '').split(' ')[0]).join(' → ')],
      ['耗时', eta.known ? `${eta.label}（20 核并行 · WSL2）` : '时长未知 · 首次运行此配置'],
      // 覆写与首次产出分开列，避免把「新建文件」说成「破坏性覆写」
      ...(hasOverwrite ? [['覆写', `${sum.overwrite.length} 个失效产物（第 ${sum.stale.join('、')} 步）`]] : []),
      ...(sum.pending.length ? [['新建', `${sum.pending.length} 步首次产出（第 ${sum.pending.join('、')} 步）`]] : []),
      ['花费', '0 元 · 本机计算，未提交云端作业'],
      ['可逆', hasOverwrite ? '旧产物按指纹归档，可回滚' : '仅新增文件，无覆写风险'],
    ],
    actions: [
      { label: '确认执行', icon: 'play', done: '已确认 · 开始执行', run: () => run(sum.ids) },
      { label: '改用其它方法', done: '已取消 · 返回候选集', run: () => {
        Stream.candidateSet({ stepId: 6, onPick: (m) => applyMethod(6, m) });
      } },
      // absorb-E：取消可附一句理由，回传给 Agent 调整方案
      { label: '取消', reason: true, done: '已取消 · 流水线保持待运行', run: (reason) => {
        if (!reason?.trim()) return;
        Stream.userMsg(`取消原因：${reason.trim()}`);
        Stream.agentMsg('收到，已记录这次取消的原因：', h('code', null, reason.trim()),
          '。后续给出执行方案时会优先考虑这一点（真实实现中理由会随审批结果回传给 Brain 层）。');
      } },
    ],
  });
  refineApprovalCard(card, sum);   // 本地估算先行，服务端权威影响预估到达后原位更新
}

async function run(ids) {
  expireUndos();   // 开始执行后旧参数不可再撤销（撤销窗口只在静止态有效）
  setBusy(true);
  S.phase = 'running';
  token = new API.Cancel();
  currentPlan?.set(3, 'd');
  currentPlan?.set(4, 'r');
  try {
    await consume(API.runPipeline(ids, token));
    // degrade 剧情会在失败处停链（§4.12 专家模式必须问）——此时是暂停不是完成
    const broken = STEP_DEFS.some((d) =>
      ['failed', 'interrupted', 'orphaned'].includes(st_(d.id).state));
    S.phase = broken ? 'paused' : 'done';
  } catch (err) {
    if (err?.name !== 'CancelledError') Stream.note('bad', `执行失败：${err.message}`);
  } finally {
    setBusy(false);
    paintStatus();
    Dock.refresh();
  }
}

/* ============================================================
   导出 —— 客户端真实生成文件（Blob 下载，零依赖、无后端）
   ============================================================ */
const STATE_LABEL = {
  done: '有效', running: '运行中', stale: 'STALE', failed: '失败', pending: '待运行',
  interrupted: '已取消 · 可续跑', orphaned: 'WSL 已停止 · 计算未完成',
};

function download(filename, text, mime = 'text/plain') {
  const url = URL.createObjectURL(new Blob([text], { type: `${mime};charset=utf-8` }));
  const a = h('a', { href: url, download: filename });
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function fmtParams(params) {
  return Object.entries(params)
    .map(([k, v]) => `${k}=${Array.isArray(v) ? `[${v.join(',')}]` : v}`)
    .join(' · ');
}

/** 方法章节草稿：方法/参数/指纹取自真实会话状态，数值结果显式标注为演示值。 */
function buildMethodsMarkdown() {
  const { pending } = St.evidenceCeiling();
  const rows = STEP_DEFS.map((d) => {
    const st = st_(d.id);
    const state = st.stale ? 'STALE' : (STATE_LABEL[st.state] || st.state);
    return `| ${d.id} | ${d.name} | \`${st.method}\` | ${fmtParams(st.params)} | \`${st.fingerprint}\` | ${state} |`;
  });
  return `# 2.3 InSAR 时序形变分析（方法章节草稿）

> 由 InSAR-Agent 原型于 ${new Date().toISOString()} 在客户端生成（后端未接入）。
> 方法、参数与指纹取自当前会话状态；**位移量、相关系数等数值结果均为演示值，不可引用**。

## 处理链快照 · 11 步

| # | 步骤 | 方法 | 参数 | 指纹 | 状态 |
|---|------|------|------|------|------|
${rows.join('\n')}

## 方法叙述

本研究使用 Sentinel-1 降轨影像（path 71，2019-06-10 — 2019-08-15，7 个获取日期），
经 ASF HyP3 生成 11 个小基线干涉对，Goldstein 滤波（α=${st_(5).params.alpha}）后采用
\`${st_(6).method}\` 解缠〔prov-6〕。时序反演使用 MintPy \`${st_(7).method}\`，
误差校正 \`${st_(8).method}\`（ERA5 对流层延迟、固体潮、DEM 误差），
形变模型 \`${st_(9).method}\`，出图 \`${st_(10).method}\`（${st_(10).params.dpi} dpi），
质检 \`${st_(11).method}\`（相关阈值 ${st_(11).params.corr_threshold}）。

## 结果（演示值 · 不可引用）

- 断层西侧同震 LOS 位移 −182.0 ± 12.4 mm〔演示值〕
- 断层东侧同震 LOS 位移 +96.5 ± 8.7 mm〔演示值〕
- GNSS 站 P580 相关系数 0.86〔演示值〕
- PS/SBAS 交叉验证一致性 0.92〔演示值〕

## 证据边界（X, not Y）

- 当前证据级别：**${LADDER[S.evidenceLevel]}**（阶梯：${LADDER.join(' → ')}）
- ${pending.length} 个质量门阈值为 PENDING，证据级别封顶 audited；validated 与 calibrated 属 next-milestone scope
${S.degraded.map((g) => `- 降级记录：第 ${g.stepId} 步 \`${g.from}\` → \`${g.to}\`（${g.failClass}），证据级别 validated → checked（§4.12 降级矩阵）`).join('\n')}${S.degraded.length ? '\n' : ''}- 12 天重访采样不足以分离同震与震后早期形变
- 报告的是处理链贯通性与量级一致性，非经标定的形变产品

## 质量门阈值台账

| 阈值 | 值 | 来源 | 状态 | 依据 |
|------|----|------|------|------|
${THRESHOLDS.map((t) => `| ${t.key} | ${t.value} | ${t.source} | ${t.status} | ${t.ref} |`).join('\n')}
`;
}

/** provenance 快照：步骤 / 方法 / 参数 / 指纹 / 状态，与审计面板同源。 */
function buildProvenance() {
  return {
    schema_version: '1.0',
    generated_at: new Date().toISOString(),
    session: S.sessionId,
    demo: true,
    note: '演示导出：后端未接入，指纹为前端 FNV-1a 摘要（非 sha256），数值结果为示意值。',
    mode: S.mode,
    phase: S.phase,
    evidence_level: { index: S.evidenceLevel, name: LADDER[S.evidenceLevel], ladder: LADDER },
    thresholds: THRESHOLDS,
    // §4.12：降级必须写进 provenance，否则论文里声称的精度是虚的
    degrades: S.degraded.map((g) => ({ ...g, evidence_cost: 'validated → checked' })),
    steps: STEP_DEFS.map((d) => {
      const st = st_(d.id);
      return {
        id: d.id, name: d.name, deps: d.deps,
        method: st.method, params: st.params,
        fingerprint: st.fingerprint, state: st.state, stale: st.stale,
        command: Dock.buildCmd(d.id).replace(/^\$ /, ''),
        outputs: (d.outputs || []).map((o) => o.path),
      };
    }),
    environment: { runtime: 'browser-demo' },
  };
}

/** 与 Agent 执行等价的裸命令脚本（右侧「报告」面板展示的同一份）。 */
function buildRunScript() {
  const body = STEP_DEFS.map((d) => {
    const st = st_(d.id);
    return `# [${String(d.id).padStart(2, '0')}/11] ${d.name} · fingerprint ${st.fingerprint}\n`
      + Dock.buildCmd(d.id).replace(/^\$ /, '');
  }).join('\n\n');
  return `#!/usr/bin/env bash
# InSAR-Agent 等价裸命令脚本 · 会话 ${S.sessionId}
# 演示导出：命令由当前 method/params 动态生成，可脱离 Agent 审阅复现路径。
set -euo pipefail

${body}
`;
}

function doExport(kind) {
  if (kind === 'md') {
    download('methods_draft.md', buildMethodsMarkdown(), 'text/markdown');
    toast('已导出 methods_draft.md · 数值结果为演示值');
  } else if (kind === 'json') {
    download('provenance.json', JSON.stringify(buildProvenance(), null, 2), 'application/json');
    toast('已导出 provenance.json · 当前会话状态快照');
  } else if (kind === 'sh') {
    download('run.sh', buildRunScript(), 'text/x-shellscript');
    toast('已导出 run.sh · 11 步等价裸命令');
  } else {
    toast('演示模式不提供 ZIP 打包（零依赖），请分别导出 .md / .json / .sh');
  }
}

/* ============================================================
   重置
   ============================================================ */
function reset() {
  expireUndos();
  St.initSteps(5);
  S.phase = 'idle';
  S.selectedStep = 6;
  S.selectedFile = null;
  S.degraded.length = 0;
  S.autoApprove.clear();
  lastChange = null;
  S.evidenceLevel = St.evidenceCeiling().level;
  St.setDiskFree(16);            // 预算演示回到初值（8–20G 灰字区）
  currentPlan = null;
  tools.clear();
  attachments.length = 0;
  renderAttachments();
  renderHero();
  Dock.setTab('pipeline');
  Dock.refresh();
  paintStatus();
}

/* 启动失败必须可见 —— 否则页面只是一片空白，无从排查。 */
try {
  boot();
} catch (err) {
  document.body.prepend(h('div', {
    style: {
      position: 'fixed', inset: 'auto 0 0 0', zIndex: '999',
      background: '#dc2626', color: '#fff', padding: '10px 16px',
      font: '12px/1.5 ui-monospace, monospace', whiteSpace: 'pre-wrap',
    },
  }, `启动失败：${err.message}\n${err.stack || ''}`));
  throw err;
}
