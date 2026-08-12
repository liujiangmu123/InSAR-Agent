/* ============================================================
   Agent 轨迹流渲染器 —— Codex 范式核心
   每种事件是一个可折叠条目，全部内联在同一条时间线上：
     user / agent / thinking / tool_call / plan / ask / result / note
   长任务不再抢占全屏，日志流在 tool_call 条目内滚动。
   ============================================================ */
import { h, txt, icon, frag, mmss, replay, toast } from './dom.js';
import { S, LADDER, def_, st_, STEP_DEFS } from './state.js';
import { figureNode, figureSvg, IMAGES, POINTS, DATES, timeSeriesSvg } from './figures.js';

let host = null;
let autoScroll = true;

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
}

/* ============================================================
   空态
   ============================================================ */
export function renderHero(onPick, onDemoEvents = null) {
  // 第一条是唯一有真实数据的场景，标 ready；另两条明确标注数据待获取，
  // 避免演示时让人误以为所有场景都能跑（诚实性要求，见 AGENT-DESIGN §0.5.5）
  const prompts = [
    { text: 'Ridgecrest 2019 同震形变时序分析', ready: true },
    { text: '分析青海玉树冻土 2020–2023 的 SBAS 时序形变', ready: false },
    { text: '雅鲁藏布江滑坡区做 PS 点监测并与 GNSS 对比', ready: false },
  ];
  host.replaceChildren(h('div', { class: 'stream-inner' },
    h('div', { class: 'hero rise' },
      h('div', { class: 'brand' }, 'IA'),
      h('h1', null, 'InSAR-Agent'),
      h('p', null, '自然语言驱动 InSAR 处理流水线。编排 ISCE2 / MintPy / PyStamps，' +
        '带参数级失效检测、完整证据链、步骤级断点续跑，直接产出论文级图表与方法章节。'),
      h('div', { class: 'chips' },
        ...prompts.map((p) => h('button', {
          class: 'chip', type: 'button', onclick: () => onPick(p.text),
          title: p.ready ? '有真实数据（11 个 HyP3 干涉对）' : '数据待获取，仅演示决策流程',
        }, p.text,
           h('span', { class: `dot-tag ${p.ready ? 'ok' : 'wait'}` },
             p.ready ? '真实数据' : '数据待获取')))),
      h('p', { class: 'hint' }, '选一个示例，或直接描述你的研究任务'),
      // 诚实性：本原型未接后端，耗时/体积/像元数为示意值（见 docs/AGENT-DESIGN.md §0.5.5）
      h('div', { class: 'demo-note' }, icon('warn'),
        h('span', null, h('b', null, '演示模式'),
          '：后端未接入。真实数据为 Ridgecrest 11 个 HyP3 干涉对；',
          '耗时（含每步完成后的「· NN min」标注）、体积、像元数等为',
          h('b', null, '示意值'), '，不可引用。时长预估仅在有本机运行历史时给出区间，否则如实显示「时长未知」。')),
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
  push(el);

  const t0 = Date.now();
  const tick = setInterval(() => {
    clock.textContent = mmss((Date.now() - t0) / 1000);
  }, 500);

  const api = {
    el,
    /** 追加一行输出。tone: dim|cmd|ok|warn|err */
    log(line, tone = '') {
      out.insertBefore(h('div', { class: `ln ${tone}` }, line), caret);
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
    /** 收尾：exit=0 → ok；其它 → bad。 */
    finish({ exit = 0, summary = '', artifacts = [], onArtifact = null } = {}) {
      clearInterval(tick);
      caret.remove();
      clock.textContent = mmss((Date.now() - t0) / 1000);
      el.classList.remove('is-run');
      el.classList.add(exit === 0 ? 'is-ok' : 'is-bad');
      codeTag.classList.remove('hidden');
      codeTag.textContent = exit === 0 ? 'exit 0' : `exit ${exit}`;
      prog.classList.remove('indet');
      bar.style.width = '100%';
      if (summary) peek.textContent = summary;
      if (artifacts.length) {
        el.appendChild(h('div', { class: 'arts' },
          ...artifacts.map((a) => h('button', {
            class: `art${a.stale ? ' is-stale' : ''}`, type: 'button',
            onclick: () => onArtifact && onArtifact(a.path),
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
      codeTag.classList.remove('hidden');
      codeTag.textContent = 'SIGTERM';
      peek.textContent = reason;
      return api;
    },
  };
  return api;
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
  // 同指纹族自动通过：不渲染卡片，留一条 note 说明 + 直接执行主操作
  if (family && S.autoApprove.has(family)) {
    note('info', h('span', null,
      h('b', null, '已自动通过：'), `${title} —— 你在本会话勾选过「同类操作不再询问」（指纹族 `,
      h('code', null, family), '），此类审批自动通过。'));
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

  const resolve = (label, tone) => {
    el.dataset.resolved = '1';
    verdict.replaceChildren(icon(tone === 'ok' ? 'check' : 'x'), txt(label));
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
  const st = st_(stepId);
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
  const el = h('div', { class: `note turn rise is-${tone}`, role: tone === 'bad' ? 'alert' : 'status' },
    icon(tone === 'ok' ? 'check' : tone === 'info' ? 'bolt' : 'warn'),
    h('span', { class: 'grow' }, content),
    ...actions.map((a) => h('button', {
      class: `btn ${a.kind || 'btn-wrn'} btn-sm`, type: 'button', onclick: a.run,
    }, a.label)));
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

/** gate_stop（红）：质量门拦停 —— 不是错误，附建议动作按钮。
    服务端事件是文本形态 {text, suggestions:[string]}（建议是文字说明，
    非可执行动作）；mock 剧情才有 metric/value/threshold 与按钮回调。 */
export function gateStopEntry({ stepId, metric, value, threshold, msg, suggestions = [], text = '' }) {
  if (text) {
    const def = stepId ? def_(stepId) : null;
    const tips = suggestions.map((sg) => (typeof sg === 'string' ? sg : sg?.label)).filter(Boolean);
    return push(h('div', { class: 'gate turn rise', role: 'status', 'aria-label': '质量门拦停' },
      h('div', { class: 'hd' }, icon('stop'),
        stepId ? `质量门拦停 · 第 ${stepId} 步${def ? ` ${def.name}` : ''}` : '质量门拦停',
        metric ? h('span', { class: 'cls mono' }, metric) : null),
      h('div', { class: 'bd' },
        h('p', null, text),
        h('p', { class: 'em' }, '这不是错误，是质量门拦截 —— 上游质量不足以支撑下游继续。'),
        tips.length ? h('p', { class: 'em' }, '建议：', tips.join('；')) : null)));
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
    suggestions.length ? h('div', { class: 'acts' },
      ...suggestions.map((sg) => h('button', {
        class: 'btn btn-gho btn-sm', type: 'button', onclick: sg.run,
        'aria-label': `建议动作：${sg.label}`,
      }, sg.label))) : null));
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
  const verdict = h('div', { class: 'verdict' });
  const acts = h('div', { class: 'acts' });
  const el = h('div', { class: 'fail turn rise', role: 'alert', 'aria-label': title },
    h('div', { class: 'hd' }, icon('x'), title, h('span', { class: 'cls mono' }, failClass)),
    detail ? h('div', { class: 'bd' }, detail) : null,
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
   结果卡
   ============================================================ */
export function resultCard({ onFile, onFigure }) {
  const rows = [
    ['products/velocities/vel_ridgecrest_2019.png', 'FIGURE'],
    ['products/timeseries/ts_ridgecrest.png', 'FIGURE'],
    ['provenance.json', 'PROVENANCE'],
  ];
  const tbody = h('tbody', null, ...rows.map(([p, kind]) => h('tr', {
    tabindex: '0', role: 'button',
    onclick: () => onFile(p),
    onkeydown: (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onFile(p); } },
  },
    h('td', { class: 'mono' }, p.split('/').pop()),
    h('td', { class: 'mono' }, st_(kind === 'PROVENANCE' ? 11 : 10)?.fingerprint || ''),
    h('td', null, h('span', { class: 'tag is-ok' }, icon('check'), '有效')))));

  return push(h('div', { class: 'result turn rise' },
    h('div', { class: 'hd' }, icon('chart'), '结果 · 同震位移场（LOS）',
      h('span', { class: 'grow' }), h('span', { class: 'sub' }, 'mintpy_sbas → step(20190706) → velocity.h5')),
    h('div', { class: 'bd' },
      h('button', { class: 'figure', type: 'button', onclick: () => onFigure('vel') },
        h('div', { class: 'cap' }, icon('image'), 'vel_ridgecrest_2019.png',
          h('span', { class: 'mono' }, '600 dpi · 2.4 MB'),
          h('span', { style: { display: 'inline-flex', alignItems: 'center', gap: '4px', color: 'var(--accent)', fontWeight: '600' } },
            '查看大图', icon('expand'))),
        figureNode('vel')),
      h('div', { class: 'kpis' },
        h('div', { class: 'kpi' }, h('div', { class: 'v', style: { color: 'var(--bad)' } }, '−182'),
          h('div', { class: 'k' }, '断层西侧同震位移 (mm)')),
        h('div', { class: 'kpi' }, h('div', { class: 'v', style: { color: 'var(--accent)' } }, '+96.5'),
          h('div', { class: 'k' }, '断层东侧同震位移 (mm)')),
        h('div', { class: 'kpi' }, h('div', { class: 'v', style: { color: 'var(--ok)' } }, '0.86'),
          h('div', { class: 'k' }, 'GNSS 相关系数 r')),
        h('div', { class: 'kpi' }, h('div', { class: 'v' }, '1.69M'),
          h('div', { class: 'k' }, '有效像元数')),
      ),
      h('table', { class: 'grid' },
        h('thead', null, h('tr', null,
          h('th', null, '产物'), h('th', null, '指纹'), h('th', null, '状态'))),
        tbody),
      h('p', { style: { fontSize: '11px', color: 'var(--text-3)', marginTop: '6px' } },
        '点击产物行 → 右侧文件面板预览 · 点击图件 → 全屏查看 · 位移量、耗时等数值为演示示意值，不可引用'),
      // 降级发生过 → 结果卡显式下调证据级别并说明原因（§4.12：降级不是静默的）
      ...S.degraded.map((g) => h('div', { class: 'downgrade' }, icon('warn'),
        h('span', null,
          `本次运行第 ${g.stepId} 步发生降级：`,
          h('code', null, g.from), ' → ', h('code', null, g.to),
          `（${g.failClass || 'service_down'}${g.reason ? ` · ${g.reason}` : ''}），证据级别下调至 `,
          h('b', null, LADDER[S.evidenceLevel]), '。'))),
      h('div', { class: 'xcheck' }, icon('check'),
        h('span', null, '交叉验证 ', h('b', null, 'PS/SBAS 一致性 0.92'),
          ' — 阈值 0.85 尚未标定（PENDING），当前仅作 warning 不硬 gate')),
    )));
}

/* ============================================================
   报告卡
   ============================================================ */
export function reportCard({ onExport, onFigures }) {
  return push(h('div', { class: 'result turn rise' },
    h('div', { class: 'hd' }, icon('doc'), '报告 · 论文方法草稿（2.3 节）',
      h('span', { class: 'grow' }), h('span', { class: 'sub' }, 'provenance → narrate · 可编辑')),
    h('div', { class: 'bd' },
      h('div', { class: 'draft' },
        h('h4', null, '2.3 InSAR 时序形变分析'),
        h('p', null, '本研究使用 Sentinel-1 降轨影像（path 71，2019-06-10 — 2019-08-15，' +
          '7 个获取日期），经 ASF HyP3 生成 11 个小基线干涉对，Goldstein 滤波（α=0.4）后' +
          '采用 SNAPHU MCF 方法解缠',
          h('span', { class: 'cite' }, '〔prov-6〕'),
          '。时序反演使用 MintPy 小基线集方法，误差校正依次移除 ERA5 对流层延迟、' +
          '固体潮与 DEM 误差。形变模型采用阶跃函数 step(20190706)，以刻画 Mw 7.1 主震同震位移。'),
        h('p', null, '断层西侧同震 LOS 位移达 −182 ± 12 mm，东侧 +97 ± 9 mm（',
          h('span', { class: 'mono' }, 'vel_ridgecrest_2019.png'),
          h('span', { class: 'cite' }, '〔prov-10〕'),
          '），与 GNSS 站 P580 的相关系数为 0.86',
          h('span', { class: 'cite' }, '〔prov-11〕'), '。'),
        h('div', { class: 'boundary' },
          h('b', null, '证据边界（X, not Y）：'),
          '本文报告的是', h('b', null, '处理链贯通性与量级一致性'), '，',
          h('b', null, '非'), '经标定的形变产品；12 天重访采样', h('b', null, '不足以'),
          '分离同震与震后早期形变；PS/SBAS 一致性阈值 0.85 ',
          h('b', null, '尚未标定'), '（status: PENDING），故交叉验证当前只产出 warning、不作硬 gate。',
          ...(S.degraded.length ? [
            '第 8 步大气校正因 ERA5 服务不可用', h('b', null, '降级'),
            '为 tropo_height_corr，证据级别上限从 validated 降至 checked（§4.12 降级矩阵，已写入 provenance）。',
          ] : []),
          '当前证据级别：', h('b', null, LADDER[S.evidenceLevel]),
          '，validated 与 calibrated 均属 next-milestone scope。')),
      h('div', { style: { display: 'flex', gap: '8px', flexWrap: 'wrap', marginTop: '10px' } },
        h('button', { class: 'btn btn-pri', type: 'button', onclick: () => onExport('md') },
          icon('doc'), '导出方法草稿 .md'),
        h('button', { class: 'btn btn-gho', type: 'button', onclick: () => onExport('json') }, '导出 provenance.json'),
        h('button', { class: 'btn btn-gho', type: 'button', onclick: () => onExport('sh') }, '导出 run.sh'),
        h('button', { class: 'btn btn-gho', type: 'button', onclick: onFigures }, icon('image'), '预览图表')),
    )));
}

/* ============================================================
   证据链卡（provenance 摘要）
   ============================================================ */
export function provenanceCard(stepId, fromMethod, toMethod) {
  const def = def_(stepId);
  const st = st_(stepId);
  const rows = [
    ['变更', frag(h('span', { style: { color: 'var(--accent)' } }, def.name), ' · method: ',
      h('span', { class: 'mono' }, fromMethod), ' → ',
      h('span', { class: 'mono', style: { color: 'var(--stale)' } }, toMethod))],
    ['指纹', frag(h('span', { class: 'mono' }, st.fingerprint), ' ',
      h('span', { style: { color: 'var(--stale)' } }, '（已重算）'))],
    ['参数', h('span', { class: 'mono' }, JSON.stringify(st.params))],
    ['工具', h('span', { class: 'mono' }, 'SNAPHU 2.0.7 · MintPy 1.6.4 · ISCE2 2.6.5 · Python 3.11')],
    ['环境', h('span', { class: 'mono' }, 'WSL2 Ubuntu 24.04 · conda env sha 4f21…9ac3')],
    ['git', h('span', { class: 'mono' }, 'HEAD 08471c2 · clean')],
    ['时间', h('span', { class: 'mono' }, new Date().toISOString())],
  ];
  return push(h('details', { class: 'plan turn rise', open: true },
    h('summary', null, h('span', { class: 'cv' }, icon('chevron')),
      h('span', { class: 'ttl' }, 'Provenance · 变更证据链'),
      h('span', { class: 'grow' }),
      h('span', { class: 'frac' }, '谁 · 何时 · 用什么 · 产出什么')),
    h('div', { class: 'tree', style: { margin: '9px 12px', borderRadius: 'var(--r-sm)' } },
      ...rows.map(([k, v]) => h('div', { class: 'ln' },
        h('span', { class: 'k' }, k), h('span', { class: 'v' }, v))))));
}

/* ============================================================
   灯箱
   ============================================================ */
let lbReturnFocus = null;   // 打开灯箱前的焦点元素，关闭时归还

export function openLightbox(figId, { onDock }) {
  const meta = IMAGES.find((i) => i.id === figId);
  const lb = document.getElementById('lightbox');
  lb.querySelector('.t').textContent = meta?.name || figId;
  lb.querySelector('.s').textContent = `${meta?.title || ''} · ${meta?.meta || ''}`;
  lb.querySelector('.canvas').replaceChildren(figureNode(figId));
  lb.hidden = false;
  lbReturnFocus = document.activeElement;
  const dockBtn = lb.querySelector('[data-act="dock"]');
  dockBtn.onclick = () => { lb.hidden = true; onDock(figId); };
  lb.querySelector('[data-act="close"]').focus();
}

export function closeLightbox() {
  const lb = document.getElementById('lightbox');
  if (lb) lb.hidden = true;
  if (lbReturnFocus?.isConnected) lbReturnFocus.focus?.();
  lbReturnFocus = null;
}
