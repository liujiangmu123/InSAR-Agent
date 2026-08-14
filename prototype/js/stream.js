/* ============================================================
   Agent 轨迹流渲染器 —— Codex 范式核心
   每种事件是一个可折叠条目，全部内联在同一条时间线上：
     user / agent / thinking / tool_call / plan / ask / result / note
   长任务不再抢占全屏，日志流在 tool_call 条目内滚动。
   ============================================================ */
import { h, txt, icon, frag, mmss, hhmm, toast } from './dom.js';
import { S, def_, st_ } from './state.js';

let host = null;
let autoScroll = true;

/* ============================================================
   失败上下文与动作接线（P2-003）
   工具卡以 exit≠0 收尾时记下上下文（步骤号、名称、日志尾部）；
   紧随其后的 note(tone=bad) / gate_stop / failureCard 消费它，
   渲染出带日志预览与处置入口的一等公民失败卡。
   ============================================================ */

// app.js 在 boot 时注入：openTerminal → 切到 dock 终端面板；resume → 复用断点续跑入口。
// 未注入时按钮降级：日志按钮回落为展开原地工具卡；续跑按钮不渲染。
let failureHooks = {};
export function setFailureHooks(hooks) {
  failureHooks = { ...failureHooks, ...hooks };
}

let lastFailure = null;               // { stepNo, stepName, exit, logTail, el, at }
const FAILURE_FRESH_MS = 120000;      // 超过时限视为与后续条目无关，不再挂靠

/** 消费一次失败上下文（取走即清空，过期返回 null）。 */
function takeFailure() {
  const f = lastFailure;
  lastFailure = null;
  return f && Date.now() - f.at <= FAILURE_FRESH_MS ? f : null;
}

/** 日志尾部预览区：终端配色，最多 8 行（截尾）。 */
function logPreview(f) {
  if (!f?.logTail?.length) return null;
  return h('div', { class: 'logs' },
    h('div', { class: 'cap' },
      `最后 ${f.logTail.length} 行日志`,
      h('span', { class: 'mono' }, `exit ${f.exit}`)),
    h('div', { class: 'out' },
      ...f.logTail.map((l) => h('div', { class: `ln ${l.tone || ''}` }, l.line))));
}

/** 「查看完整日志」：优先走注入的 dock 终端面板入口，否则展开原地工具卡。 */
function viewLogBtn(f) {
  return h('button', {
    class: 'btn btn-gho btn-sm', type: 'button',
    'aria-label': '查看完整日志（切换到终端面板）',
    onclick: () => {
      if (failureHooks.openTerminal) { failureHooks.openTerminal(f?.stepNo); return; }
      if (f?.el?.isConnected) {
        f.el.open = true;
        const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
        f.el.scrollIntoView({ block: 'center', behavior: reduced ? 'auto' : 'smooth' });
      }
    },
  }, '查看完整日志');
}

/** 「从断点继续」：复用 app.js 的续跑入口；未接线（静态演示页）时不渲染。 */
function resumeBtn() {
  if (!failureHooks.resume) return null;
  return h('button', {
    class: 'btn btn-wrn btn-sm', type: 'button',
    'aria-label': '从断点继续（已完成步骤直接跳过）',
    onclick: () => failureHooks.resume(),
  }, '从断点继续');
}

export function mount(el) {
  host = el;
  // 用户手动上滚时暂停自动跟随，回到底部时恢复
  el.addEventListener('scroll', () => {
    const gap = el.scrollHeight - el.scrollTop - el.clientHeight;
    autoScroll = gap < 60;
  }, { passive: true });
}

function inner() {
  let box = host.querySelector('.stream-inner');
  if (!box) {
    box = h('div', { class: 'stream-inner' });
    host.replaceChildren(box);
  }
  return box;
}

/** 跟随滚动合帧：一帧内多次追加只滚一次。 */
let scrollQueued = false;
function follow() {
  if (!autoScroll || scrollQueued) return;
  scrollQueued = true;
  requestAnimationFrame(() => {
    scrollQueued = false;
    host.scrollTop = host.scrollHeight;
  });
}

function push(node) {
  inner().appendChild(node);
  probeRun = null;   // 顶层出现任何新条目都打断探查段的「连续」性（见下方聚合组）
  follow();
  return node;
}

/** composer 高度变化后重新贴底（app.js 的 ResizeObserver 调用）。 */
export function refollow() {
  follow();
}

export function clear() {
  host.replaceChildren(h('div', { class: 'stream-inner' }));
  autoScroll = true;
  lastFailure = null;
  probeRun = null;
}

/* ============================================================
   空态
   ============================================================ */
export function renderHero(onPick, onDemoEvents = null) {
  lastFailure = null;   // 回到空态即换会话/重置，旧失败上下文不再有效
  probeRun = null;
  // 纯示例文案（只作输入引导，不声称任何数据/会话真实存在——
  // 数据是否就绪由服务端探测与规划回合如实回答）
  const prompts = [
    { text: 'Ridgecrest 2019 同震形变时序分析' },
    { text: '分析青海玉树冻土 2020–2023 的 SBAS 时序形变' },
    { text: '雅鲁藏布江滑坡区做 PS 点监测并与 GNSS 对比' },
  ];
  host.replaceChildren(h('div', { class: 'stream-inner' },
    h('div', { class: 'hero rise' },
      h('div', { class: 'brand' }, 'IA'),
      h('h1', null, 'InSAR-Agent'),
      h('p', null, '自然语言驱动 InSAR 处理流水线。编排 ISCE2 / MintPy / PyStamps，' +
        '带参数级失效检测、完整证据链、步骤级断点续跑，直接产出论文级图表与方法章节。'),
      h('div', { class: 'chips' },
        ...prompts.map((p) => h('button', {
          class: 'chip', type: 'button',
          // states 接入:聊天空态引导卡 —— 示例任务点击只填充输入框、不自动发送,
          // 用户看清并可改写后再回车;不经 onPick(其回调会立即 submit)。
          onclick: () => {
            const inp = document.getElementById('prompt');
            if (!inp) { onPick(p.text); return; }   // 输入框缺失(非常规宿主)回落旧行为
            inp.value = p.text;
            inp.dispatchEvent(new Event('input', { bubbles: true }));   // 让发送键启用/高度自适应
            inp.focus();
          },
          title: '示例任务：点击填入输入框，可改写后再发送',
        }, p.text))),
      h('p', { class: 'hint' }, '选一个示例，或直接描述你的研究任务'),
      onDemoEvents ? h('button', {
        class: 'hero-demo', type: 'button', onclick: onDemoEvents,
        'aria-label': '在轨迹流中插入 reattach、intervention、gate_stop 三种长任务事件的静态演示',
      }, '查看长任务事件演示（reattach / intervention / gate_stop）→') : null,
    )));
}

/* ============================================================
   消息基元
   ============================================================ */
export function userMsg(text) {
  return push(h('div', { class: 'u-msg turn rise' }, h('div', { class: 'body' }, text)));
}

/** Agent 文本消息。parts 支持字符串与节点混排（便于内嵌 <code>）。 */
export function agentMsg(...parts) {
  return push(h('div', { class: 'a-msg turn rise' },
    h('div', { class: 'av' }, 'IA'),
    h('div', { class: 'body' }, ...parts)));
}

export function code(s) { return h('code', null, s); }

/** 思考条目：默认折叠，只露一行标题。 */
export function thinking(summary, detail) {
  const d = h('details', { class: 'think turn rise' },
    h('summary', null, h('span', { class: 'cv' }, icon('chevron')), summary),
    detail ? h('div', { class: 'inner' }, detail) : null);
  return push(d);
}

/** 流式打字指示器，返回 remove 函数。 */
export function typingIndicator() {
  const node = push(h('div', { class: 'a-msg turn' },
    h('div', { class: 'av' }, 'IA'),
    h('div', { class: 'body' }, h('div', { class: 'typing' }, h('i'), h('i'), h('i')))));
  return () => node.remove();
}

/* ============================================================
   流式回复气泡（0814B §1.4）：say.delta 增量渲染
     append   纯文本追加（单文本节点，不进任何解析器），近底部才跟随滚动
              （follow 内建判定：用户上滚阅读时不抢滚动）；
     finalize 终帧 say 到达：renderPart 节点整体替换增量文本（以定稿为准，
              替换后与非流式 agentMsg 排版完全一致）；
     abort    截断/停止/断流：半截内容如实保留 + 淡化中断标注，
              绝不把未定稿文本冒充完整回复。
   气泡类：is-streaming（打字光标 ::after 纯装饰）/ is-aborted（标废态）。
   ============================================================ */
export function agentMsgStream() {
  const text = txt('');   // 单文本节点增量追加：纯文本语义，天然免注入
  const body = h('div', { class: 'body' }, text);
  const el = push(h('div', { class: 'a-msg turn rise is-streaming' },
    h('div', { class: 'av' }, 'IA'), body));
  let closed = false;   // finalize/abort 后手柄失效，迟到的 append 静默丢弃

  const api = {
    el,
    append(chunk) {
      if (closed) return api;
      text.data += String(chunk ?? '');
      follow();
      return api;
    },
    finalize(nodes) {
      if (closed) return api;
      closed = true;
      el.classList.remove('is-streaming');
      body.replaceChildren(...nodes);
      follow();
      return api;
    },
    abort(label = '(回复中断,内容不完整)') {
      if (closed) return api;
      closed = true;
      el.classList.remove('is-streaming');
      el.classList.add('is-aborted');
      body.appendChild(h('span', { class: 'say-abort' }, label));
      follow();
      return api;
    },
  };
  return api;
}

/* ============================================================
   liveSay 状态机（0814B §1.4，纯逻辑，node 单测见 tests/js/say_stream.test.mjs）
   一次流式回复的生命周期：say.delta 开流/追加 → 终帧二选一
   （say 定稿 / say.abort 标废）→ 回合结束兜底 abort。
   只有这三个入口能关闭状态；note 等其他事件不经状态机，穿行不打断。
   open 注入气泡工厂：生产环境缺省 agentMsgStream，单测传假手柄。
   ============================================================ */
export function createLiveSay(open = agentMsgStream) {
  let cur = null;   // 当前流式气泡手柄；null = 无进行中的流式回复

  return {
    active: () => cur !== null,
    /** say.delta：首个增量开流，后续追加到同一气泡。 */
    delta(chunk) {
      if (!cur) cur = open();
      cur.append(chunk);
    },
    /** say 终帧：有流 → 整体替换并关闭，返回 true；
        无流 → 返回 false（调用方按非流式路径原样渲染）。 */
    finalize(nodes) {
      if (!cur) return false;
      cur.finalize(nodes);
      cur = null;
      return true;
    },
    /** say.abort 终帧 / 停止 / 回合结束兜底：半截标废并关闭；
        无流时是无害空操作（幂等，可放心放进 finally）。 */
    abort(label) {
      cur?.abort(label);
      cur = null;
    },
  };
}

/** 流式思考块（Cursor 式：等待时展开可见，可随时摺叠）。 */
export function thinkStream() {
  const text = txt('');
  const label = txt('思考中');
  const inner = h('div', { class: 'inner' }, text);
  const el = push(h('details', { class: 'think turn rise is-streaming', open: true },
    h('summary', null, h('span', { class: 'cv' }, icon('chevron')), label),
    inner));
  let closed = false;
  const api = {
    el,
    get closed() { return closed; },
    append(chunk) {
      if (closed) return api;
      text.data += String(chunk ?? '');
      follow();
      return api;
    },
    end() {
      if (closed) return api;
      closed = true;
      el.classList.remove('is-streaming');
      label.data = '思考';
      return api;
    },
    collapse() {
      el.open = false;
      return api;
    },
    abort(tag = '(思考中断)') {
      if (closed) return api;
      closed = true;
      el.classList.remove('is-streaming');
      el.classList.add('is-aborted');
      label.data = '思考';
      inner.appendChild(h('span', { class: 'say-abort' }, tag));
      return api;
    },
  };
  return api;
}

export function createLiveThink(open = thinkStream) {
  let cur = null;
  return {
    active: () => cur !== null && !cur.closed,
    delta(chunk) {
      if (!cur || cur.closed) cur = open();
      cur.append(chunk);
    },
    end() {
      cur?.end();
    },
    collapse() {
      cur?.collapse();
    },
    abort(tag) {
      cur?.abort(tag);
      cur = null;
    },
    close() {
      cur = null;
    },
  };
}

/* ============================================================
   只读工具聚合折叠（Cursor 工作组模式，RESEARCH §9 机制 #1）
   连续的探查类工具卡（verb 非 [NN/MM] 执行步骤，对应 id 不以
   s+数字开头的 probe/inspect 类事件）聚合为一张折叠组卡
   「探查 · N 个工具」；执行步骤卡（s1–s11）永远顶层内联。
   规则：
     · 单张孤立探查卡保持普通卡，出现第二张连续探查卡才建组并迁入首张；
     · 顶层出现任何其他条目（消息/note/执行卡…）即打断连续段（push()）；
     · 组内任一 exit≠0 → 整组默认展开并标红（成功折叠/失败展开的组级版本）。
   纯渲染层改动：consume() 分发与事件契约不变。
   ============================================================ */
let probeRun = null;   // 当前连续探查段 { first, group }

/** verb 形如 [08/11] → 步骤号 8；探查类（probe/inspect 等）→ null。 */
function stepNoOf(verb) {
  const m = /^\[(\d+)\/\d+\]$/.exec(String(verb).trim());
  return m ? parseInt(m[1], 10) : null;
}

function makeProbeGroup() {
  const dot = h('span', { class: 'dot' });
  const ttl = h('span', { class: 'ttl' }, '探查 · 0 个工具');
  const stat = h('span', { class: 'stat' }, '运行中…');
  const body = h('div', { class: 'grp-body' });
  const el = h('details', { class: 'toolgroup turn rise is-run', 'aria-label': '探查工具组' },
    h('summary', null,
      h('span', { class: 'cv' }, icon('chevron')),
      dot, ttl, h('span', { class: 'grow' }), stat),
    body);
  const g = {
    el, body, total: 0, ended: 0, failed: 0, canceled: 0,
    add(card) {
      g.total += 1;
      body.appendChild(card);
      card._probeGroup = g;
      g.paint();
    },
    memberEnd(exit) {
      g.ended += 1;
      if (exit !== 0) {
        g.failed += 1;
        el.open = true;                 // 失败组默认展开，供排查
        el.classList.add('is-bad');
      }
      g.paint();
    },
    memberCancel() {
      g.ended += 1;
      g.canceled += 1;
      g.paint();
    },
    paint() {
      ttl.textContent = `探查 · ${g.total} 个工具`;
      const running = g.ended < g.total;
      el.classList.toggle('is-run', running && !g.failed);
      el.classList.toggle('is-ok', !running && !g.failed && !g.canceled);
      el.classList.toggle('is-warn', !running && !g.failed && g.canceled > 0);
      stat.textContent = g.failed ? `${g.failed} 个失败`
        : running ? '运行中…'
        : g.canceled ? '已取消' : '完成';
      stat.classList.toggle('is-bad', g.failed > 0);
    },
  };
  return g;
}

/** 探查卡进流：维持「连续段」语义（建组、迁移首张、追加成员）。 */
function addProbeCard(el) {
  if (probeRun?.group) {                       // 段内已有组 → 直接追加
    probeRun.group.add(el);
    follow();
    return;
  }
  if (probeRun?.first?.isConnected) {          // 第二张连续探查卡 → 建组并迁入首张
    const g = makeProbeGroup();
    inner().insertBefore(g.el, probeRun.first);
    g.add(probeRun.first);
    g.add(el);
    probeRun.group = g;
    follow();
    return;
  }
  push(el);                                    // 首张：普通卡入流，登记段起点
  probeRun = { first: el, group: null };
}

/* ============================================================
   工具调用条目 —— 最核心的组件
   ============================================================ */
export function toolCall({ cmd, verb = '', label = '', open = false, determinate = true }) {
  const dot = h('span', { class: 'dot' });
  const clock = h('span', { class: 'clock' }, '00:00');
  const codeTag = h('span', { class: 'code hidden' });
  const peek = h('span', { class: 'peek' }, label);
  const out = h('div', { class: 'out', role: 'log', 'aria-live': 'off' });
  const bar = h('i');
  const prog = h('div', { class: `prog${determinate ? '' : ' indet'}` }, bar);
  const caret = h('span', { class: 'caret' });

  const cmdNode = h('span', { class: 'cmd' },
    verb ? h('span', { class: 'verb' }, verb + ' ') : null, cmd);

  const el = h('details', {
    class: 'tool turn rise is-run', open: open || undefined,
  },
    h('summary', null,
      h('span', { class: 'cv' }, icon('chevron')),
      dot, cmdNode, peek, clock, codeTag),
    prog, out);

  out.appendChild(caret);
  const stepNo = stepNoOf(verb);
  // 执行步骤卡（s1–s11）永远顶层内联；探查类进聚合组（任务 1）
  if (stepNo !== null) push(el);
  else addProbeCard(el);

  const t0 = Date.now();
  const tick = setInterval(() => {
    clock.textContent = mmss((Date.now() - t0) / 1000);
  }, 500);

  // 日志尾部环形缓冲：失败卡的预览数据源（截尾 8 行，P2-003）
  const logTail = [];
  const LOG_TAIL_MAX = 8;

  const api = {
    el,
    /** 追加一行输出。tone: dim|cmd|ok|warn|err */
    log(line, tone = '') {
      out.insertBefore(h('div', { class: `ln ${tone}` }, line), caret);
      logTail.push({ line, tone });
      if (logTail.length > LOG_TAIL_MAX) logTail.shift();
      out.scrollTop = out.scrollHeight;   // 终端框内部跟随，不影响外层流
      follow();
      return api;
    },
    progress(pct) {
      prog.classList.remove('indet');
      bar.style.width = `${Math.max(0, Math.min(100, pct))}%`;
      return api;
    },
    peek(text) { peek.textContent = text; return api; },
    /** 收尾：exit=0 → ok；其它 → bad，并留存失败上下文供后续失败卡挂靠。 */
    finish({ exit = 0, summary = '', artifacts = [], onArtifact = null } = {}) {
      clearInterval(tick);
      caret.remove();
      clock.textContent = mmss((Date.now() - t0) / 1000);
      el.classList.remove('is-run');
      el.classList.add(exit === 0 ? 'is-ok' : 'is-bad');
      el._probeGroup?.memberEnd(exit);   // 组内失败 → 整组展开标红
      if (exit !== 0) {
        lastFailure = {
          stepNo,                        // 探查类（verb 非 [NN/MM]）为 null
          stepName: label || '', exit,
          logTail: [...logTail], el, at: Date.now(),
        };
      }
      codeTag.classList.remove('hidden');
      codeTag.textContent = exit === 0 ? 'exit 0' : `exit ${exit}`;
      prog.classList.remove('indet');
      bar.style.width = '100%';
      if (summary) peek.textContent = summary;
      if (artifacts.length) {
        el.appendChild(h('div', { class: 'arts' },
          ...artifacts.map((a) => h('button', {
            class: `art${a.stale ? ' is-stale' : ''}`, type: 'button',
            onclick: () => { onArtifact && onArtifact(a.path); jumpArtifact(a.path, stepNo); },
            title: `${a.path} · ${a.hash || ''}`,
          }, icon('file'), a.path.split('/').pop(),
             a.hash ? h('span', { class: 'h' }, a.hash) : null))));
      }
      // 成功的条目自动收起，失败的保持展开供排查
      if (exit === 0) el.open = false; else el.open = true;
      return api;
    },
    /** 被取消：保留已有输出，标黄。 */
    cancel(reason = '已取消') {
      clearInterval(tick);
      caret.remove();
      el.classList.remove('is-run');
      el.classList.add('is-warn');
      el._probeGroup?.memberCancel();
      codeTag.classList.remove('hidden');
      codeTag.textContent = 'SIGTERM';
      peek.textContent = reason;
      return api;
    },
  };
  return api;
}

/* ============================================================
   产物 chip 跳转（任务 4 最小版，RESEARCH §8 问题 6）：
   tool.end 的产物徽章可点 —— 图像类切影像面板，其余切文件面板。
   ============================================================ */

/** 产物 → 目标面板：.png/.jpg 归影像，其余归文件（供校验脚本断言）。 */
export function artifactTab(path) {
  return /\.(png|jpe?g)$/i.test(String(path)) ? 'images' : 'files';
}

/** 仅真实应用页（存在 #dockTabs）动态加载 dock.js 并切面板；
    静态演示页 / node 校验环境无 dock，保持 onArtifact 回退即可。 */
function jumpArtifact(path, stepNo) {
  if (!document.getElementById('dockTabs')) return;
  import('./dock.js').then((Dock) => {
    try {
      // 并行分支若导出了 selectStepFile 则透传步骤号（feature-detect）
      if (typeof Dock.selectStepFile === 'function' && stepNo !== null) Dock.selectStepFile(stepNo);
      Dock.setTab(artifactTab(path));
    } catch { /* dock 未挂载：保持 onArtifact 的既有跳转 */ }
  }).catch(() => {});
}

/* ============================================================
   内联计划面板 —— agent 自己生成/更新的待办
   ============================================================ */
export function planPanel(items, { open = true, title = '执行计划' } = {}) {
  const frac = h('span', { class: 'frac' });
  const mini = h('div', { class: 'mini' });
  const list = h('ol');
  const el = h('details', { class: 'plan turn rise', open: open || undefined },
    h('summary', null,
      h('span', { class: 'cv' }, icon('chevron')),
      h('span', { class: 'ttl' }, title), mini,
      h('span', { class: 'grow' }), frac),
    list);

  const state = items.map((it) => ({ ...it }));

  function paint() {
    const done = state.filter((s) => s.st === 'd').length;
    frac.textContent = `${done}/${state.length}`;
    mini.replaceChildren(...state.map((s) => h('i', { class: s.st === 'p' ? '' : s.st })));
    list.replaceChildren(...state.map((s) => h('li', { class: s.st },
      h('span', { class: 'mk' },
        s.st === 'd' ? icon('check') : s.st === 'f' ? icon('x') : s.st === 's' ? txt('!') : txt(String(s.n))),
      h('span', { class: 'tx' }, s.text),
      s.note ? h('span', { class: 'note' }, s.note) : null)));
  }
  paint();
  push(el);

  return {
    el,
    /** st: p(pending) r(running) d(done) f(failed) s(stale) */
    set(n, st, note) {
      const it = state.find((x) => x.n === n);
      if (!it) return;
      it.st = st;
      if (note !== undefined) it.note = note;
      paint();
    },
    all(st) { state.forEach((s) => { s.st = st; }); paint(); },
  };
}

/* ============================================================
   审批卡 —— 内联确认，不用 window.confirm
   增强（absorb-E/F）：
     · family：审批指纹族。勾选「本会话内同类操作不再询问」后，
       同族审批自动通过并在流里留 note 说明（mock 层实现）。
     · action.reason：点击后先出可选理由输入框，理由随 run(reason) 回传。
   ============================================================ */
export function askApproval({ title, rows, danger = false, actions, family = null }) {
  // 同指纹族自动通过：不渲染卡片，留一条 note 说明 + 直接执行主操作。
  // 「不再询问」可撤销（任务 3，RESEARCH §9 机制 #5）：note 带「撤销该记忆」
  // 链接，点击清除 S.autoApprove 里的该指纹族并 toast 确认 ——
  // 一次勾选不再是永久失控，下次同类操作会重新弹审批卡。
  if (family && S.autoApprove.has(family)) {
    const revoke = h('button', {
      class: 'undo-link', type: 'button',
      'aria-label': `撤销「同类操作不再询问」记忆（指纹族 ${family}）`,
      onclick: () => {
        if (!S.autoApprove.has(family)) return;
        S.autoApprove.delete(family);
        revoke.textContent = '已撤销';
        revoke.setAttribute('disabled', '');
        toast(`已撤销记忆：指纹族 ${family} 的同类操作将重新询问`);
      },
    }, '撤销该记忆');
    note('info', h('span', null,
      h('b', null, '已自动通过：'), `${title} —— 你在本会话勾选过「同类操作不再询问」（指纹族 `,
      h('code', null, family), '），此类审批自动通过。', txt(' '), revoke));
    const primary = actions[0];
    primary?.run && primary.run();
    return null;
  }

  const el = h('div', { class: `ask turn rise${danger ? ' is-dng' : ''}`, role: 'group', 'aria-label': title });
  const verdict = h('div', { class: 'verdict' });
  const acts = h('div', { class: 'acts' });
  const rememberInput = family ? h('input', {
    type: 'checkbox', 'aria-label': '本会话内同类操作不再询问',
  }) : null;

  const dl = h('dl', null, ...rows.flatMap(([k, v]) => [h('dt', null, k), h('dd', null, v)]));
  el.append(
    h('div', { class: 'hd' }, icon(danger ? 'warn' : 'shield'), title),
    dl,
    acts, verdict);

  /** 服务端权威数据到达后原位更新/追加行（先本地即时估算，后服务端校准）。
      已作出决定的卡不再改动 —— 卡面必须如实反映用户批准时看到的内容。 */
  el.updateRows = (pairs) => {
    if (el.dataset.resolved) return;
    for (const [k, v] of pairs) {
      const dt = [...dl.querySelectorAll('dt')].find((n) => n.textContent === k);
      if (dt) dt.nextElementSibling.replaceChildren(v instanceof Node ? v : txt(v));
      else dl.append(h('dt', null, k), h('dd', null, v));
    }
  };

  // P2-002：决定后的完成态是紧凑单行 —— 小图标 + 结论 + 时间，
  // 不放大占位，不遮挡后续执行流卡片（图标尺寸由 .ask .verdict svg 约束）
  const resolve = (label, tone) => {
    el.dataset.resolved = '1';
    verdict.replaceChildren(
      icon(tone === 'ok' ? 'check' : 'x'), txt(label),
      h('span', { class: 'time mono' }, hhmm()));
    verdict.style.color = tone === 'ok' ? 'var(--ok-text)' : 'var(--text-3)';
  };

  const commit = (a, i, reason) => {
    // 「不再询问」只在批准主操作时生效 —— 拒绝时记住毫无意义
    if (i === 0 && family && rememberInput?.checked) {
      S.autoApprove.add(family);
      toast('已记住：本会话内同类操作不再询问');
    }
    resolve(a.done || a.label, a.tone || (i === 0 ? 'ok' : 'no'));
    a.run && a.run(reason);
  };

  /** 取消理由输入（absorb-E：理由回传给 Agent 调整方案）。 */
  const showReasonBox = (a, i) => {
    if (el.querySelector('.reasonbox')) return;
    const inp = h('input', {
      type: 'text', placeholder: '原因（可选，会帮助 Agent 调整方案）',
      'aria-label': '取消原因（可选）',
      onkeydown: (e) => { if (e.key === 'Enter') { e.preventDefault(); confirmCancel(); } },
    });
    const box = h('div', { class: 'reasonbox' }, inp,
      h('button', { class: 'btn btn-gho btn-sm', type: 'button', onclick: () => confirmCancel() }, '确认取消'));
    function confirmCancel() {
      const v = inp.value.trim();
      box.remove();
      commit(a, i, v);
    }
    el.insertBefore(box, verdict);
    inp.focus();
  };

  actions.forEach((a, i) => {
    acts.appendChild(h('button', {
      class: `btn ${a.kind || (i === 0 ? (danger ? 'btn-dng' : 'btn-pri') : 'btn-gho')}`,
      type: 'button',
      onclick: () => { a.reason ? showReasonBox(a, i) : commit(a, i); },
    }, a.icon ? icon(a.icon) : null, a.label));
  });
  if (rememberInput) {
    acts.appendChild(h('label', { class: 'remember' }, rememberInput, '本会话内同类操作不再询问'));
  }

  push(el);
  // 焦点落到主操作，键盘用户可直接回车
  requestAnimationFrame(() => acts.querySelector('button')?.focus());
  return el;
}

/* ============================================================
   候选集 —— LLM 受限决策的可视化
   ============================================================ */
export function candidateSet({ stepId, onPick }) {
  const def = def_(stepId);
  // 空镜像守卫（P1-6，浏览器实测）：新会话首回合 candidates 事件先于 /api/state
  // 的镜像同步到达，st_() 无条目 —— 以注册表默认方法兜底，决策卡照常渲染
  // （app.js 的 candidates 分支只守卫了 def_()）。
  const st = st_(stepId) || { method: def.method };
  const opts = h('div', { class: 'opts', role: 'radiogroup', 'aria-label': `${def.name}方法候选` });

  const buttons = def.methods.map((m) => {
    const b = h('button', {
      class: 'opt', type: 'button', role: 'radio',
      'aria-checked': String(m.id === st.method),
      disabled: !m.ok || undefined,
      dataset: { m: m.id },
      onclick: () => {
        if (!m.ok) return;
        buttons.forEach((x) => x.setAttribute('aria-checked', String(x.dataset.m === m.id)));
        onPick(m.id);
      },
    },
      h('span', { class: 'rd' }),
      h('span', { class: 'grow' },
        h('span', { class: 'nm' }, m.label,
          m.recommend ? h('span', { class: 'tag is-ok' }, icon('check'), '推荐') : null,
          m.id === st.method ? h('span', { class: 'tag is-stale' }, '当前') : null,
          !m.ok ? h('span', { class: 'tag is-bad' }, icon('x'), m.blocked || '不可用') : null,
          m.extra ? h('span', { class: 'extra' }, m.extra) : null),
        h('span', { class: 'why' }, `${m.engine} · ${m.why}`)));
    return b;
  });
  opts.append(...buttons);

  const avail = def.methods.filter((m) => m.ok).length;
  push(h('div', { class: 'cands turn rise' },
    h('div', { class: 'hd' }, icon('target'),
      `候选集 · ${def.name}方法（第 ${stepId} 步）`,
      h('span', { class: 'grow' }),
      h('span', { class: 'sub' }, `受限决策 ${avail} 选 1 · 规则引擎已排除 ${def.methods.length - avail} 项`)),
    opts,
    h('div', { class: 'ft' }, '选择会重算参数指纹，并沿依赖图级联标记下游产物为 STALE。')));
  return opts;
}

/* ============================================================
   通知横幅
   ============================================================ */
export function note(tone, content, actions = []) {
  // P2-003：失败通知（tone=bad）紧随工具卡失败出现时，升级为结构化失败卡，
  // 不再只是一条与普通提示同级的横幅。无失败上下文（如运行时错误）保持横幅。
  if (tone === 'bad') {
    const f = takeFailure();
    if (f) return stepFailureCard(f, content, actions);
  }
  const el = h('div', { class: `note turn rise is-${tone}`, role: tone === 'bad' ? 'alert' : 'status' },
    icon(tone === 'ok' ? 'check' : tone === 'info' ? 'bolt' : tone === 'bad' ? 'x' : 'warn'),
    h('span', { class: 'grow' }, content),
    ...actions.map((a) => h('button', {
      class: `btn ${a.kind || 'btn-wrn'} btn-sm`, type: 'button', onclick: a.run,
    }, a.label)));
  return push(el);
}

/* ============================================================
   一等公民失败卡（P2-003）：步骤号与名称 + 失败类别徽标 +
   日志尾部预览 + 处置入口（查看完整日志 / 从断点继续）。
   数据源：toolCall 失败留存的上下文 + note(bad) 的原文
   （服务端文本已含 failure_class 描述与处置建议）。
   ============================================================ */
function stepFailureCard(f, content, actions = []) {
  // 从 note 原文提取失败类别（driver 格式「第 N 步失败 · service_down(…)」）
  const text = typeof content === 'string' ? content : content?.textContent || '';
  const failClass = /·\s*([a-z_][a-z0-9_]*)/i.exec(text)?.[1] || null;
  const title = f.stepNo
    ? `第 ${f.stepNo} 步失败${f.stepName ? ` · ${f.stepName}` : ''}`
    : '执行失败';

  const el = h('div', { class: 'stepfail turn rise', role: 'alert', 'aria-label': title },
    h('div', { class: 'hd' }, icon('x'), title,
      failClass ? h('span', { class: 'cls mono' }, failClass) : null),
    h('div', { class: 'bd' }, content),
    logPreview(f),
    h('div', { class: 'acts' },
      viewLogBtn(f),
      resumeBtn(),
      ...actions.map((a) => h('button', {
        class: `btn ${a.kind || 'btn-gho'} btn-sm`, type: 'button', onclick: a.run,
      }, a.label))));
  return push(el);
}

/* ============================================================
   §7.4 四种新条目：degrade / gate_stop / reattach / intervention
   ============================================================ */

/** degrade（橙）：降级发生时显式告知，附证据级别代价（§4.12 降级矩阵）。
    两种事件形态：服务端是文本形态 {text, evidenceBefore, evidenceAfter}；
    mock 剧情才有 from/to/failClass 等结构化字段。 */
export function degradeEntry({ from, to, evidenceFrom = 'validated', evidenceTo = 'checked',
                               detail, reason, text = '', evidenceBefore, evidenceAfter }) {
  takeFailure();   // 降级横幅已交代该失败，上下文不再挂到后续条目
  if (text) {
    return push(h('div', { class: 'degrade turn rise', role: 'status', 'aria-label': '降级通知' },
      icon('warn'),
      h('span', { class: 'grow' },
        h('b', null, '已降级：'), text,
        '。证据级别 ', h('b', null, evidenceBefore || evidenceFrom),
        ' → ', h('b', null, evidenceAfter || evidenceTo),
        '。已写入 provenance 与证据边界表。')));
  }
  return push(h('div', { class: 'degrade turn rise', role: 'status', 'aria-label': '降级通知' },
    icon('warn'),
    h('span', { class: 'grow' },
      h('b', null, '已降级：'),
      detail ? `${detail}，` : '', '方法 ',
      h('code', null, from), ' → ', h('code', null, to),
      '。证据级别 ', h('b', null, evidenceFrom), ' → ', h('b', null, evidenceTo),
      reason ? `（${reason}）` : '', '。已写入 provenance 与证据边界表。')));
}

/** gate_stop（红）：质量门拦停 —— 不是错误，建议列表是一等公民的可点操作。
    服务端事件是文本形态 {text, suggestions:[string]}：建议按钮点击后回填输入框，
    一键交给 Agent 处理；mock 剧情才有 metric/value/threshold 与按钮回调。
    紧随工具卡失败出现时（P2-003）附日志尾部预览与完整日志入口。 */
export function gateStopEntry({ stepId, metric, value, threshold, msg, suggestions = [], text = '' }) {
  const f = takeFailure();
  // 建议按钮：首选项加重（btn-wrn），其余幽灵按钮；字符串建议回填输入框
  const suggestBtn = (sg, i) => {
    const label = typeof sg === 'string' ? sg : sg?.label;
    if (!label) return null;
    return h('button', {
      class: `btn ${i === 0 ? 'btn-wrn' : 'btn-gho'} btn-sm`, type: 'button',
      'aria-label': `建议动作：${label}`,
      onclick: sg.run || (() => fillComposer(label)),
    }, label);
  };
  if (text) {
    const def = stepId ? def_(stepId) : null;
    const btns = suggestions.map(suggestBtn).filter(Boolean);
    return push(h('div', { class: 'gate turn rise', role: 'status', 'aria-label': '质量门拦停' },
      h('div', { class: 'hd' }, icon('stop'),
        stepId ? `质量门拦停 · 第 ${stepId} 步${def ? ` ${def.name}` : ''}` : '质量门拦停',
        metric ? h('span', { class: 'cls mono' }, metric) : null),
      h('div', { class: 'bd' },
        h('p', null, text),
        h('p', { class: 'em' }, '这不是错误，是质量门拦截 —— 上游质量不足以支撑下游继续。'),
        btns.length ? h('p', { class: 'em' }, '建议（点击填入输入框，交给 Agent 处理）：') : null),
      logPreview(f),
      btns.length || f ? h('div', { class: 'acts' },
        ...btns, f ? viewLogBtn(f) : null) : null));
  }
  const def = def_(stepId);
  return push(h('div', { class: 'gate turn rise', role: 'status', 'aria-label': '质量门拦停' },
    h('div', { class: 'hd' }, icon('stop'),
      `质量门拦停 · 第 ${stepId} 步${def ? ` ${def.name}` : ''}`,
      h('span', { class: 'cls mono' }, metric)),
    h('div', { class: 'bd' },
      h('p', null, h('b', { class: 'mono' }, `${metric} ${value} < ${threshold}`), '，已停链。'),
      h('p', { class: 'em' }, '这不是错误，是质量门拦截 —— 上游质量不足以支撑下游继续。',
        msg ? ` ${msg}` : '')),
    logPreview(f),
    suggestions.length || f ? h('div', { class: 'acts' },
      ...suggestions.map(suggestBtn).filter(Boolean),
      f ? viewLogBtn(f) : null) : null));
}

/** 建议文本回填输入框：同步触发 input 事件，让发送按钮态与高度自适应生效。 */
function fillComposer(textValue) {
  const input = document.getElementById('prompt');
  if (!input) return;
  input.value = textValue;
  input.dispatchEvent(new Event('input', { bubbles: true }));
  input.focus();
}

/** reattach（蓝横幅）：服务重启后接回运行中任务。
    服务端事件是文本形态 {text}；mock 演示才有 pid/runFor 等字段。 */
export function reattachEntry({ engine = 'MintPy', pid, runFor, stepId, logOffset, text = '' }) {
  if (text) {
    return push(h('div', { class: 'reattach turn rise', role: 'status', 'aria-label': '任务接回通知' },
      icon('link'),
      h('span', { class: 'grow' }, h('b', null, '已接回运行中任务：'), text)));
  }
  return push(h('div', { class: 'reattach turn rise', role: 'status', 'aria-label': '任务接回通知' },
    icon('link'),
    h('span', { class: 'grow' },
      h('b', null, '已接回运行中任务：'),
      `检测到 ${engine} 仍在运行（pid `, h('code', null, String(pid)),
      `，已运行 ${runFor}），已接回日志流`,
      logOffset ? frag('（log_offset ', h('code', null, logOffset.toLocaleString('en-US')), '）') : null,
      stepId ? `，续挂到第 ${stepId} 步。` : '。')));
}

/** intervention：运行中人工干预的留痕（排队 / 立即生效）。 */
export function interventionEntry({ text, mode = 'queue' }) {
  return push(h('div', { class: 'ivt turn rise', role: 'status', 'aria-label': '干预留痕' },
    icon('clock'),
    h('span', { class: 'grow' }, text),
    h('span', { class: 'tag is-run' }, mode === 'steer' ? 'steer · 立即生效' : 'queued · 步间生效')));
}

/* ============================================================
   失败卡（§7.8）：失败分类徽标 + 处置按钮组（附代价说明）
   ============================================================ */
export function failureCard({ stepId, failClass, title, detail, options = [] }) {
  // 同一失败的处置卡：消费失败上下文，把日志尾部预览一并渲染（P2-003）
  const f = takeFailure();
  const preview = f && (!stepId || !f.stepNo || f.stepNo === stepId) ? logPreview(f) : null;
  const verdict = h('div', { class: 'verdict' });
  const acts = h('div', { class: 'acts' });
  const el = h('div', { class: 'fail turn rise', role: 'alert', 'aria-label': title },
    h('div', { class: 'hd' }, icon('x'), title, h('span', { class: 'cls mono' }, failClass)),
    detail ? h('div', { class: 'bd' }, detail) : null,
    preview,
    h('div', { class: 'cap' }, '可选处置：'),
    acts, verdict);

  options.forEach((o) => {
    acts.appendChild(h('div', { class: 'row' },
      h('button', {
        class: `btn ${o.kind || 'btn-gho'} btn-sm`, type: 'button',
        'aria-label': o.cost ? `${o.label}（${o.cost}）` : o.label,
        onclick: () => {
          if (!o.keepOpen) {
            el.dataset.resolved = '1';
            verdict.replaceChildren(icon('check'), txt(o.done || o.label));
          }
          o.run && o.run();
        },
      }, o.label),
      o.cost ? h('span', { class: 'cost' }, '← ', o.cost) : null));
  });

  return push(el);
}

/* ============================================================
   灯箱（仅剩关闭入口）
   演示假卡 resultCard/reportCard/provenanceCard 与假图入口 openLightbox
   已删除（0814B W3：R3 勘察确认全前端零调用方，写死的 −182mm/+96.5mm
   等示意 KPI 不允许再有代码路径能画出来）。closeLightbox 保留：
   index.html 的 #lightbox 遮罩/关闭钮/Esc 仍接线到它（app.js）。
   ============================================================ */
export function closeLightbox() {
  const lb = document.getElementById('lightbox');
  if (lb) lb.hidden = true;
}
