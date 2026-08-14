/* ============================================================
   右侧 Dock 面板
   六个视图各自渲染进独立的 pane，切换只是 hidden 开关 ——
   保留各自滚动位置，不做全量重建。
   ============================================================ */
import { h, txt, icon, $, keepScroll, toast } from './dom.js';
import {
  S, LADDER, STEP_DEFS, def_, st_, validateParam, workSummary,
  THRESHOLDS, evidenceCeiling, setServerThresholds,
} from './state.js';
import { galleryView } from './gallery.js';
import { ENV_NOTE, WSL, WORKSPACE, ENGINES, DISKS } from './envdata.js';
import * as API from './backend.sse.js';   // 面板 7/8 实时数据；离线时各视图回落演示数据
import * as RS from './runswitch.js';       // run 历史切换器(仅流水线面板顶部挂载)
import * as ES from './emptystate.js';      // states 接入:空态/骨架/错误态统一构造器(零依赖)

const TABS = [
  { id: 'pipeline', label: '流水线', ic: 'list' },
  { id: 'images', label: '影像', ic: 'image' },
  { id: 'files', label: '文件', ic: 'folder' },
  { id: 'audit', label: '审计', ic: 'shield' },
  { id: 'report', label: '报告', ic: 'doc' },
  { id: 'web', label: '浏览器', ic: 'globe' },
  // 面板 7/8/9（AGENT-DESIGN §7.2 / §7.7）—— 按钮在 mount 阶段动态创建
  { id: 'trace', label: '轨迹', ic: 'brain' },
  { id: 'term', label: '终端', ic: 'chevron' },
  { id: 'env', label: '环境', ic: 'chart' },
];

let refs = {};
let hooks = {};
const scrollMemo = new Map();

export function mount({ tabsEl, bodyEl, on }) {
  hooks = on;
  refs = { tabsEl, bodyEl, panes: {}, buttons: {} };

  for (const t of TABS) {
    const b = h('button', {
      type: 'button', role: 'tab', id: `tab-${t.id}`,
      'aria-selected': String(S.dockTab === t.id),
      'aria-controls': `pane-${t.id}`,
      onclick: () => setTab(t.id),
    }, icon(t.ic), t.label, t.id === 'pipeline' ? h('span', { class: 'n' }, '11') : null);
    refs.buttons[t.id] = b;
    tabsEl.appendChild(b);

    const p = h('section', {
      class: 'dock-pane', id: `pane-${t.id}`, role: 'tabpanel',
      'aria-labelledby': `tab-${t.id}`, tabindex: '0',
      hidden: S.dockTab !== t.id || undefined,
    });
    refs.panes[t.id] = p;
    bodyEl.appendChild(p);
  }

  // 左右方向键在标签间移动焦点（WAI-ARIA tabs 模式）
  tabsEl.addEventListener('keydown', (e) => {
    const idx = TABS.findIndex((t) => t.id === S.dockTab);
    if (e.key === 'ArrowRight') { setTab(TABS[(idx + 1) % TABS.length].id); focusTab(); }
    if (e.key === 'ArrowLeft') { setTab(TABS[(idx - 1 + TABS.length) % TABS.length].id); focusTab(); }
  });

  renderAll();
}

function focusTab() { refs.buttons[S.dockTab]?.focus(); }

export function setTab(id) {
  if (refs.panes[S.dockTab]) scrollMemo.set(S.dockTab, refs.bodyEl.scrollTop);
  S.dockTab = id;
  for (const t of TABS) {
    refs.panes[t.id].hidden = t.id !== id;
    refs.buttons[t.id].setAttribute('aria-selected', String(t.id === id));
  }
  render(id);
  refs.bodyEl.scrollTop = scrollMemo.get(id) || 0;
}

export function renderAll() {
  render(S.dockTab);
  paintBadges();
}

/** 只重渲染当前可见 pane；其它 pane 在切换时惰性刷新。 */
export function refresh() {
  keepScroll(refs.bodyEl, () => render(S.dockTab));
  paintBadges();
}

function paintBadges() {
  const { stale, all } = workSummary();
  const badge = refs.buttons.pipeline.querySelector('.n');
  if (!badge) return;
  badge.textContent = stale.length ? `${stale.length} 失效`
                    : all.length ? `${STEP_DEFS.length - all.length}/${STEP_DEFS.length}`
                    : '11 ✓';
  badge.classList.toggle('alert', stale.length > 0);
}

function render(tab) {
  const pane = refs.panes[tab];
  if (!pane) return;
  switch (tab) {
    case 'pipeline': pane.replaceChildren(pipelineView()); break;
    case 'images': pane.replaceChildren(imagesView()); break;
    case 'files': pane.replaceChildren(filesView()); break;
    case 'audit': pane.replaceChildren(auditView()); break;
    case 'report': pane.replaceChildren(reportView()); break;
    case 'web': pane.replaceChildren(webView()); break;
    case 'trace': pane.replaceChildren(traceView()); break;
    case 'term': pane.replaceChildren(termView()); break;
    case 'env': pane.replaceChildren(envView()); break;
  }
}

/* ============================================================
   流水线视图：11 步 + 选中步详情（方法 / 参数 / 产物 / 指纹）
   ============================================================ */
const STATE_CLS = {
  done: 'd', running: 'r', stale: 's', failed: 'f', pending: 'p',
  interrupted: 'i', orphaned: 'o',
};
// §7.8 四态：interrupted/orphaned 文案与 app.js 的 STATE_LABEL 保持一字不差
const STATE_TXT = {
  done: '✓ 有效', running: '运行中', stale: 'STALE', failed: '失败', pending: '待运行',
  interrupted: '已取消 · 可续跑', orphaned: 'WSL 已停止 · 计算未完成',
};

/** 空镜像兜底（P1-5，浏览器实测）：会话还没有 run 时 S.steps 为空，st_() 无条目
 *  —— 无守卫解引用会在面板每次渲染炸出未处理拒绝 + 错误横幅。空态按 pending
 *  形状兜底（方法取注册表默认，与 syncServerSteps 创建镜像时的缺省一致）。 */
const stOr = (id) => st_(id) || {
  state: 'pending', stale: false, method: def_(id)?.method || '',
  params: {}, fingerprint: '········',
};

function pipelineView() {
  const list = h('div', { class: 'pipe', role: 'list' });
  // 行级视图模型:本地状态先渲染;服务端字段(staleReason / skipped 三态)异步并入
  const vms = new Map();
  for (const d of STEP_DEFS) {
    const st = stOr(d.id);
    vms.set(d.id, {
      id: d.id, name: d.name, state: st.state, stale: !!st.stale,
      staleReason: null, fingerprint: st.fingerprint,
    });
    const cls = STATE_CLS[st.state] || 'p';
    const mth = d.methods.find((m) => m.id === st.method);
    const btn = h('button', {
      class: `pstep ${cls}`, type: 'button',
      dataset: { step: d.id },
      'aria-current': String(S.selectedStep === d.id),
      onclick: () => { S.selectedStep = d.id; refresh(); },
    },
      h('span', { class: 'no' },
        st.state === 'done' ? icon('check') : st.state === 'stale' ? txt('!') : txt(String(d.id))),
      h('span', { class: 'nm' }, d.name,
        mth ? h('span', { class: 'mth' }, ` ${mth.label}`) : null),
      h('span', { class: 'fl' }, STATE_TXT[st.state] || ''));
    if (S.selectedStep === d.id) btn.style.boxShadow = 'inset 0 0 0 1px var(--accent)';
    // 行 = 步骤按钮 + 行内动作区(失效徽标/重跑入口,不能嵌进 button)
    list.appendChild(h('div', { class: 'prow', role: 'listitem' },
      btn, h('span', { class: 'pacts' })));
  }

  const sumHost = h('div', { class: 'plr-sumhost' });
  const wrap = h('div', { class: 'plr-wrap' }, list);
  const runsHost = h('div');   // run 历史切换器挂载点(渲染与只读语义全在 runswitch.js)
  const root = h('div', null,
    runsHost,
    h('h3', { class: 'sect' }, '处理流水线 · 11 步'),
    sumHost,
    wrap,
    stepDetail(S.selectedStep),
    h('h3', { class: 'sect' }, '失效传播'),
    h('p', { class: 'blurb' },
      '改动任一步的方法或参数 → 重算 sha256 指纹 → 沿依赖图级联标记全部下游为 STALE。' +
      '断点续跑只重跑受影响段，指纹未变的步骤直接跳过。'));

  RS.mountRunSwitch(runsHost, { root, onSwitch: refresh });   // 选中变化 → 整面板按新 run 重渲染

  // 异步增强(envView 的动态 import 先例,不新增模块级依赖):
  // 依赖轨道 rail / 摘要条 / 失效原因 popover / 重跑影响确认。
  (async () => {
    const [R, state] = await Promise.all([
      import('./pipelinerail.js'),
      cachedFetch(`state:${S.sessionId}:${RS.activeRunId() || ''}`,   // 缓存键带所选 run
        () => API.fetchState({ runId: RS.activeRunId() })),
    ]);
    if (!root.isConnected) return;
    R.closeOverlay();   // 面板已重渲染,旧浮层的锚点失效
    for (const s of state?.steps || []) {
      const vm = vms.get(s.id);
      if (!vm) continue;
      if (s.state) vm.state = s.state;   // 保留 skipped(本地镜像映射为 done,第三态在此恢复)
      vm.stale = !!s.stale;
      vm.staleReason = s.staleReason || null;
      if (s.fingerprint) vm.fingerprint = s.fingerprint;
    }
    const steps = [...vms.values()];

    // 摘要条计数点击 → 滚动到首个对应步骤行并高亮一闪
    const jumpTo = (id) => {
      const el = list.querySelector(`[data-step="${id}"]`);
      if (!el) return;
      el.scrollIntoView({ block: 'center' });
      el.classList.remove('flash'); void el.offsetWidth; el.classList.add('flash');
    };
    // 重跑入口:先调 /api/impact 预览影响集;服务端视角无变化或不可达 → 本地估算
    const openRerun = async (id) => {
      const st = st_(id);
      const imp = await API.fetchImpact(id, { method: st.method, params: st.params });
      if (!root.isConnected) return;
      const useSrv = !!(imp && imp.affected?.length);
      R.openImpactCard({
        step: vms.get(id), local: !useSrv,
        impact: useSrv ? imp : R.localImpact(id, steps),
        onConfirm: (ids) => (hooks.runSteps ? hooks.runSteps(ids) : toast('运行入口未接线(演示)')),
      });
    };

    for (const vm of steps) {
      const btn = list.querySelector(`[data-step="${vm.id}"]`);
      const acts = btn?.parentNode?.querySelector('.pacts');
      if (!btn || !acts) continue;
      if (vm.state === 'skipped') {   // 缓存/云端跳过的行内语言(第三态,非 done)
        btn.classList.add('k');
        const fl = btn.querySelector('.fl');
        if (fl) fl.textContent = '↷ 缓存/云端';
      }
      if (vm.stale || vm.state === 'stale') {
        acts.appendChild(h('button', {
          class: 'pbadge', type: 'button', 'aria-label': `第 ${vm.id} 步失效原因`,
          onclick: (e) => R.openStalePopover({
            anchor: e.currentTarget, step: vm, steps,
            onGoto: (sid) => gotoStep(sid),
            onImpact: (sid) => openRerun(sid),
          }),
        }, '失效'));
      }
      // skipped 不给重跑入口:云端已完成步骤强行入列会触发 contract_broken(实测教训)
      if (vm.stale || ['done', 'stale', 'failed', 'interrupted', 'orphaned'].includes(vm.state)) {
        acts.appendChild(h('button', {
          class: 'prerun', type: 'button', 'aria-label': `从第 ${vm.id} 步重跑(先看影响集)`,
          onclick: () => openRerun(vm.id),
        }, '重跑'));
      }
    }
    sumHost.replaceChildren(R.summaryBar(steps, { onJump: jumpTo }));
    // rail 最后挂载:行内动作已就位,节点 y 按行实际 offsetTop 实测(调研 §4.7 的坑)
    R.mountRail(wrap, steps, { onPick: (id, chain) => flashSteps([id, ...chain]) });
  })();

  return root;
}

function stepDetail(stepId) {
  const d = def_(stepId), st = stOr(stepId);   // 空镜像同 pipelineView 兜底（P1-5）
  if (!d) return txt('');
  const mth = d.methods.find((m) => m.id === st.method);

  const methodSel = h('select', {
    'aria-label': '处理方法',
    onchange: (e) => hooks.changeMethod?.(stepId, e.target.value),
  }, ...d.methods.map((m) => h('option', {
    value: m.id, selected: m.id === st.method || undefined, disabled: !m.ok || undefined,
  }, `${m.label}${m.ok ? '' : ` — ${m.blocked}`}`)));

  const fields = Object.entries(st.params).map(([k, v]) => {
    if (Array.isArray(v)) {
      return h('div', { class: 'field' },
        h('label', null, k),
        h('input', { value: v.join(', '), readonly: true, tabindex: '-1' }));
    }
    const err = h('p', { class: 'ferr hidden' });

    const showError = (msg, target) => {
      target.setAttribute('aria-invalid', String(!!msg));
      err.textContent = msg || '';
      err.classList.toggle('hidden', !msg);
    };

    /** 提交参数。change 事件在保持焦点时不触发，所以 blur 与 Enter 都显式提交。 */
    const commit = (target) => {
      const msg = validateParam(k, target.value);
      showError(msg, target);
      if (msg) return;
      const num = Number(target.value);
      const next = Number.isFinite(num) && target.value.trim() !== '' ? num : target.value;
      if (String(next) === String(st.params[k])) return;   // 值没变不触发失效
      hooks.changeParams?.(stepId, { [k]: next });
    };

    const inp = h('input', {
      value: String(v), 'aria-label': k, dataset: { k },
      inputmode: 'decimal',
      oninput: (e) => showError(validateParam(k, e.target.value), e.target),
      onblur: (e) => commit(e.target),
      onkeydown: (e) => {
        if (e.key === 'Enter') { e.preventDefault(); commit(e.target); }
        if (e.key === 'Escape') { e.target.value = String(v); showError(null, e.target); e.target.blur(); }
      },
    });
    return h('div', null, h('div', { class: 'field' }, h('label', null, k), inp), err);
  });

  // §7.8 四态优先于 STALE 标记：interrupted/orphaned/failed 的用户动作（续跑/处置）
  // 比「失效」更紧要——取消时 app.js 会同时挂 stale，不能让 STALE 盖住断点态。
  const tone = st.state === 'failed' ? 'bad'
    : st.state === 'interrupted' || st.state === 'orphaned' ? 'stale'
    : st.stale ? 'stale'
    : st.state === 'done' ? 'ok'
    : st.state === 'running' ? 'run' : '';
  const label = st.state === 'failed' || st.state === 'interrupted' || st.state === 'orphaned'
    ? STATE_TXT[st.state]
    : st.stale ? '⚠ STALE' : st.state === 'done' ? '✓ 有效' : STATE_TXT[st.state];

  // interrupted / orphaned 用户动作完全不同（§7.8）：前者直接续跑，后者先重启环境
  const stateNote = st.state === 'interrupted'
    ? h('div', { class: 'note is-stale' }, icon('warn'),
        h('span', null, h('b', null, '已取消 · 可续跑'),
          '：已完成阶段的产物与指纹保留，从断点继续，不重跑已完成部分。'))
    : st.state === 'orphaned'
    ? h('div', { class: 'note is-stale' }, icon('warn'),
        h('span', null, h('b', null, 'WSL 已停止 · 计算未完成'),
          '：环境事件而非计算失败——产物可能是完整的（只是未写 job.rc）。重启 WSL 后从断点续跑，不是重跑。'))
    : null;

  return h('div', { class: 'pdetail' },
    h('div', { class: 'hd' },
      h('span', { class: tone ? `tag is-${tone}` : 'tag' }, label),
      `第 ${stepId} 步 · ${d.name}`),
    h('div', { class: 'bd' },
      stateNote,
      h('div', { class: 'field' }, h('label', null, '方法'), methodSel),
      mth ? h('p', { style: { fontSize: '11.5px', color: 'var(--text-2)' } }, `${mth.engine} · ${mth.why}`) : null,
      h('div', { class: 'pform' }, ...fields),
      // 界面诚实化(0814B W6):原「参数为演示占位数据」脚注是反向假文案 ——
      // 参数实为 /api/state 下发的服务端真值,已删;「预估 0 min(示意)」
      // 恒零假行同删(dur 恒 0,诚实预估走 estimateRerunHonest 的运行历史)
      h('dl', { class: 'kv' },
        h('dt', null, '指纹'), h('dd', null, st.fingerprint),
        h('dt', null, '依赖'), h('dd', null, d.deps.length ? d.deps.map((x) => `#${x}`).join(' ') : '—'),
        h('dt', null, '产物'), h('dd', null, (d.outputs || []).map((o) => o.path).join('\n') || '—')),
      h('div', { class: 'shell' }, buildCmd(stepId)),
      h('p', { class: 'fnote' },
        '上为按方法与参数拼装的示意命令;真实执行命令以复现脚本(GET /api/run.sh)为准。')));
}

/** 由 method + params 渲染等价裸命令行 —— 对应 DESIGN §12 第 3 条要求。
 *  注意:这是前端拼装的「示意命令」(程序名为约定映射),与服务端真实 argv
 *  无关;详情卡已随行标注,真实命令的唯一来源是 /api/run.sh 复现脚本。 */
export function buildCmd(stepId) {
  const d = def_(stepId), st = stOr(stepId);   // stepDetail 内联调用，同守卫（P1-5）
  const flags = Object.entries(st.params)
    .map(([k, v]) => `--${k.replace(/_/g, '-')} ${Array.isArray(v) ? v.join(',') : v}`)
    .join(' ');
  const prog = {
    1: 'asf_search.py', 2: 'dem.py', 3: 'topsApp.py --dostep coregister',
    4: 'topsApp.py --dostep interferogram', 5: 'filter.py',
    6: 'snaphu.py', 7: 'smallbaselineApp.py --dostep invert_network',
    8: 'smallbaselineApp.py --dostep correct_troposphere',
    9: 'timeseries2velocity.py', 10: 'figure_journal.py', 11: 'crossval_ps_sbas.py',
  }[stepId] || 'run.py';
  return `$ ${prog} --method ${st.method} ${flags}`;
}

/* ============================================================
   影像视图：画廊 + 小地图 + 点位时序
   ============================================================ */
function imagesView() {
  // 产物图件网格:真实产物(GET /api/figures → /api/artifact-file),
  // 不可达 → 错误态带重试 / 无产物 → 空态带运行引导 —— 全在 gallery.js。
  const gal = galleryView();

  // 下半区(空间浏览小地图 + 点位时序卡)整体归 tspoint.js:点击地图 →
  // GET /api/timeseries-point 取真实单像元时序(Shift 叠加对比);不可达/
  // 无真实时序时由 tspoint.js 渲染诚实的错误态/空态。动态 import 与
  // envlive 同策略,不新增模块级 import(与并行分支的 dock.js 改动解耦)。
  const spatial = h('div', null);
  import('./tspoint.js').then((m) => { if (spatial.isConnected) m.mountSpatial(spatial); });

  return h('div', null,
    gal,
    h('h3', { class: 'sect' }, '空间浏览 · 点击点位取时序'),
    spatial);
}

/* ============================================================
   文件视图：真实产物树（GET /api/artifacts）
   ============================================================ */
function filesView() {
  /* 真实化（GET /api/artifacts，经 fileslive.js 拉取与 30s 缓存）：
     加载中骨架屏 → 真实产物树（步骤分组 + 三段指纹详情卡）；
     后端不可达 → 错误态带重试；会话还没有 run → 空态带运行引导。
     fileslive.js 走动态 import，不新增模块级 import（同 envView 做法，
     与并行分支的 dock.js 改动解耦）。 */
  const root = h('div', null,   // states 接入:请求中分支 → 树形骨架(fileslive 动态 import 前的首帧)
    ES.renderSkeleton(null, { kind: 'tree', rows: 6, label: '正在读取产物清单（GET /api/artifacts）' }));

  (async () => {
    const FILES = await import('./fileslive.js');
    if (!root.isConnected) return;
    root.replaceChildren(FILES.skeleton());        // 骨架屏：等待清单结果
    const data = await FILES.fetchFilesLive();
    if (!root.isConnected) return;                 // 面板已切走，丢弃过期结果
    if (data === null) {
      // 后端不可达/响应异常 → 错误态带重试，绝不渲染假产物树
      root.replaceChildren(
        h('h3', { class: 'sect' }, '数据与产物'),
        ES.renderError(null, {
          message: '产物清单读取失败——后端不可达或响应异常（GET /api/artifacts）。',
          retry: () => { FILES.invalidate(); refresh(); },
        }));
      return;
    }
    if (data.noRun) {
      // 后端可达但会话还没有 run → 空态带运行引导
      root.replaceChildren(
        h('h3', { class: 'sect' }, '数据与产物'),
        ES.renderEmpty(null, {
          icon: 'folder', title: '还没有 run 记录',
          hint: '运行一次流水线即可生成——每个产物的路径、大小与三段指纹在此可查。',
          action: { label: '运行流水线', event: 'states:run-pipeline' },
        }));
      return;
    }
    root.replaceChildren(...FILES.liveBody(data, {
      onRefresh: () => { FILES.invalidate(); refresh(); },
      openImages,
    }));
  })();

  return root;
}

/* ============================================================
   审计视图：服务端证据阶梯 + 每步来源 + 阈值台账
   ============================================================ */
function auditView() {
  /* 真实化(/api/provenance 权威证据链 + /api/env 阈值台账,经 auditlive.js
     拉取与 30s 缓存):run 存在即渲染服务端证据级 —— 级别词汇严格用后端
     六级(evidence.ladder);后端不可达 → 错误态带重试;会话还没有 run
     (404)→ 空态带运行引导,绝不渲染本地自算的演示证据链。
     auditlive.js 走动态 import,不新增模块级 import(与并行分支解耦)。 */
  const root = h('div', null,   // states 接入:请求中分支 → 列表骨架(auditlive 动态 import 前的首帧)
    ES.renderSkeleton(null, { kind: 'list', rows: 6, label: '正在读取证据链(GET /api/provenance)' }));

  (async () => {
    const AUD = await import('./auditlive.js');
    if (!root.isConnected) return;
    root.replaceChildren(AUD.skeleton());          // 骨架屏:等待证据链读取
    const data = await AUD.fetchAuditLive();
    if (!root.isConnected) return;                 // 面板已切走,丢弃过期结果
    if (data === null) {
      root.replaceChildren(
        h('h3', { class: 'sect' }, '六级证据阶梯'),
        ES.renderError(null, {
          message: '证据链读取失败——后端不可达或响应异常(GET /api/provenance)。',
          retry: () => { AUD.invalidate(); refresh(); },
        }));
      return;
    }
    if (data.noRun) {
      root.replaceChildren(
        h('h3', { class: 'sect' }, '六级证据阶梯'),
        ES.renderEmpty(null, {
          icon: 'shield', title: '还没有证据链记录',
          hint: '运行一次流水线即可生成——每步证据来源、六级评定与阈值台账在此可审。',
          action: { label: '运行流水线', event: 'states:run-pipeline' },
        }));
      return;
    }
    root.replaceChildren(...AUD.renderLive(data, {
      onRefresh: () => { AUD.invalidate(); refresh(); },
      gotoStep,
    }));
  })();

  return root;
}

/* ============================================================
   报告视图
   ============================================================ */
function reportView() {
  /* 真实化（GET /api/methods.md，经 reportlive.js 拉取与 30s 缓存）：
     run done 后渲染服务端生成的真实方法草稿 —— 顶部标注生成来源
     （X-Narrate-Source：llm = LLM 增强 / template = 规则生成）＋「下载 .md」
     Blob 下载；草稿里的〔prov-N〕可溯引用渲染为特殊样式（.cite）。
     后端不可达 → 错误态带重试；会话还没有 run（404）→ 空态带运行引导，
     绝不渲染数字为示意值的静态演示草稿。
     reportlive.js 走动态 import，不新增模块级 import（与并行分支解耦）。 */
  const root = h('div', null,   // states 接入:请求中分支 → 段落骨架(报告面板此前只有一行裸文本)
    ES.renderSkeleton(null, { kind: 'text', rows: 7, label: '正在获取方法草稿（GET /api/methods.md）' }));

  // ---- 实测渲染：服务端真实草稿 + 来源标注 + 「下载 .md」 ----
  const liveBody = (RPT, data) => {
    const isLLM = data.source === 'llm';
    return [
      h('h3', { class: 'sect' }, '论文方法草稿（服务端生成）'),
      h('div', {
        style: { display: 'flex', gap: '7px', alignItems: 'center',
                 flexWrap: 'wrap', marginBottom: '8px' },
      },
        h('span', {
          class: `tag is-${isLLM ? 'run' : 'ok'}`,
          title: `X-Narrate-Source: ${data.source}`,
        }, icon(isLLM ? 'brain' : 'shield'), RPT.sourceLabel(data.source)),
        h('span', { style: { flex: '1' } }),
        h('button', {
          class: 'btn btn-pri btn-sm', type: 'button',
          'aria-label': '下载方法草稿 methods.md',
          onclick: () => {
            download(`methods_${S.sessionId}.md`, data.markdown, 'text/markdown');
            toast('已下载 methods.md（服务端生成内容）');
          },
        }, icon('doc'), '下载 .md'),
        h('button', {
          class: 'btn btn-gho btn-sm', type: 'button',
          'aria-label': '重新拉取方法草稿（跳过 30 秒缓存）',
          onclick: () => { RPT.invalidate(); refresh(); },
        }, icon('refresh'), '刷新')),
      h('div', { class: 'draft' }, RPT.renderMarkdown(data.markdown)),
      h('p', { class: 'blurb' },
        '内容由 GET /api/methods.md 从 provenance 账本确定生成；〔prov-N〕指向第 N 步执行记录，' +
        '〔ref:…〕为文献/依据锚点。LLM 只做措辞润色且有数字反幻觉护栏，失败自动回落规则模板。'),
    ];
  };

  (async () => {
    const RPT = await import('./reportlive.js');
    if (!root.isConnected) return;
    const data = await RPT.fetchReportLive();
    if (!root.isConnected) return;                 // 面板已切走，丢弃过期结果
    if (data === null) {
      root.replaceChildren(
        h('h3', { class: 'sect' }, '论文方法草稿'),
        ES.renderError(null, {
          message: '方法草稿读取失败——后端不可达或响应异常（GET /api/methods.md）。',
          retry: () => { RPT.invalidate(); refresh(); },
        }));
      return;
    }
    if (data.noRun) {
      root.replaceChildren(
        h('h3', { class: 'sect' }, '论文方法草稿'),
        ES.renderEmpty(null, {
          icon: 'doc', title: '还没有方法草稿',
          hint: '完成一次流水线运行即可生成——草稿由 provenance 账本确定派生，〔prov-N〕逐条可溯。',
          action: { label: '运行流水线', event: 'states:run-pipeline' },
        }));
      return;
    }
    root.replaceChildren(...liveBody(RPT, data));
  })();

  return root;
}

/* ============================================================
   浏览器面板 —— 诚实空态(界面诚实化,0814B W6)
   原「内置浏览器」渲染的是整面写死的假检索结果(假 ASF granule、
   假文献引用数)且无演示标识,真实后端下照常出现 —— 已整体删除。
   实时检索过程当前未接入此面板;tab 结构保留,待检索可视化真实
   接线后再点亮。
   ============================================================ */
function webView() {
  return h('div', null,
    h('h3', { class: 'sect' }, '数据检索'),
    ES.renderEmpty(null, {
      icon: 'globe', title: '实时检索过程未接入此面板',
      hint: '数据检索请在聊天中让 Agent 执行(自主循环的 search_data 动作),' +
        '检索过程与结果见聊天流的工具卡。所有下载走服务端句柄,LLM 不接触本地路径。',
    }));
}

/* ============================================================
   共用小工具（三个新面板用；不 import app.js）
   ============================================================ */

/** Blob 下载（客户端生成文件，零依赖）。 */
function download(filename, text, mime = 'text/plain') {
  const url = URL.createObjectURL(new Blob([text], { type: `${mime};charset=utf-8` }));
  const a = h('a', { href: url, download: filename });
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/* 面板 7/8 实时数据的短 TTL 缓存：Dock.refresh 在运行期间高频触发，
   同一键 3 秒内复用同一个 in-flight promise，避免打爆后端。 */
const liveCache = new Map();

function cachedFetch(key, loader, ttlMs = 3000) {
  const hit = liveCache.get(key);
  if (hit && Date.now() - hit.at < ttlMs) return hit.promise;
  const promise = loader();
  liveCache.set(key, { at: Date.now(), promise });
  return promise;
}

/* ============================================================
   环境视图（面板 9 · §7.7 优先级最高）
   运行环境 / 磁盘 / 候选收窄原因 / 质量门阈值 —— 可行性可解释
   ============================================================ */
function envView() {
  /* 真实化（/api/env + /api/setup/status，经 envlive.js 拉取与 30s 缓存）：
     加载中骨架屏 → 实测渲染；后端不可达 → 回落 envdata.js 静态数据并
     顶部醒目标注「演示数据（后端未连接）」。envlive.js 走动态 import，
     不新增模块级 import（与并行分支的 dock.js 改动解耦）。 */
  const root = h('div', null, h('p', { class: 'blurb' }, '正在探测环境（GET /api/env）…'));

  // ---- 两种模式共用：候选收窄（本地状态派生）/ 阈值台账 / 证据上限 ----
  const narrowedRows = () => {
    const narrowed = [];
    for (const d of STEP_DEFS) {
      for (const m of d.methods) if (!m.ok) narrowed.push({ d, m });
    }
    return h('div', { class: 'envcard' },
      ...narrowed.map(({ d, m }) => h('div', { class: 'erow' },
        h('span', { class: 'ban' }, icon('x')),
        h('span', { class: 'mono m2' }, m.label),
        h('span', { class: 'why' }, m.blocked),
        h('span', { class: 'at' }, `第 ${d.id} 步 · ${d.name}`))));
  };

  const thresholdRows = (rows, labelOf) => h('div', { class: 'contract' }, ...rows.map((t) => {
    const okThr = String(t.status).toUpperCase() === 'OK';
    return h('div', { class: 'm' },
      h('span', { class: 'nm' }, t.key),
      h('span', { class: 'mono', style: { color: 'var(--text)' } }, String(t.value)),
      h('span', { class: `tag is-${okThr ? 'ok' : 'stale'}` }, labelOf(t)),
      h('span', { class: 'src' }, t.ref));
  }));

  const ceilingNote = () => {
    // 证据上限优先由服务端阈值台账(/api/env)推导;只有离线回落本地常量时
    // 才如实标「离线示意」(界面诚实化,0814B W6)
    const { level, pending, source } = evidenceCeiling();
    return h('div', { class: 'note is-stale ceiling', role: 'status' },
      icon('warn'),
      h('span', null, h('b', null, `当前证据上限：${LADDER[level]}`),
        `（${pending.length} 项阈值待标定${source === 'local' ? ' · 离线示意' : ''}）`),
      h('span', { class: 'law' }, '§4.13 没有依据就不给数'));
  };

  const tailSections = (thrNode) => [
    h('h3', { class: 'sect' }, '候选收窄原因'),
    narrowedRows(),
    h('p', { class: 'blurb' },
      '规则引擎在规划前探测环境，把不可行方法从候选集移除 —— 每一条禁用都能回答「为什么」。'),
    h('h3', { class: 'sect' }, '质量门阈值'),
    thrNode,
    ceilingNote(),
    h('p', { class: 'blurb' },
      'WSL 未就绪时，这个面板是首屏该看的东西 —— 而不是点了「运行」才发现跑不起来。'),
  ];

  // ---- 演示回落：envdata.js 静态数据（仅离线演示用），顶部醒目标注 ----
  const demoBody = (LIVE) => {
    const okN = ENGINES.filter((e) => e.ok).length;
    const runCard = h('div', { class: 'envcard' },
      h('div', { class: 'erow' },
        h('span', { class: 'k' }, 'WSL2'),
        h('span', { class: 'v' }, h('span', { class: 'tag is-bad' }, icon('x'), WSL.text)),
        h('button', {
          class: 'btn btn-gho btn-sm', type: 'button', 'aria-label': 'WSL 安装指引',
          onclick: () => toast('演示模式：真实版本将引导 wsl --install，并把发行版导入 E: 盘（C: 仅剩 36 G，勿装于此）', 3400),
        }, '安装指引')),
      h('div', { class: 'sub' }, WSL.detail),
      h('div', { class: 'erow' },
        h('span', { class: 'k' }, '工作区'),
        h('span', { class: 'v' }, WORKSPACE.path || '—',
          h('span', { class: 'hint' }, `　${WORKSPACE.hint}`))),
      h('div', { class: 'erow' },
        h('span', { class: 'k' }, '引擎'),
        h('span', { class: 'v' }, `${okN} 可用 · ${ENGINES.length - okN} 缺失`)),
      ...ENGINES.map((e) => h('div', { class: 'eng' },
        h('span', { class: 'nm' }, e.name),
        h('span', { class: 'ver' }, e.ver),
        h('span', { class: `st ${e.ok ? 'ok' : 'bad'}` }, icon(e.ok ? 'check' : 'x')),
        h('span', { class: 'enote' }, e.note || ''))),
      h('div', { class: 'sub' }, ENV_NOTE));

    const diskCard = h('div', { class: 'envcard' },
      ...DISKS.map((dk) => {
        const usedPct = Math.round((1 - dk.free / dk.total) * 100);
        const tight = dk.free / dk.total < 0.3;   // 剩余 < 30% 记「紧张」
        return h('div', { class: 'drow' },
          h('div', { class: 'top' },
            h('span', { class: 'lbl' }, dk.label),
            dk.warn ? h('span', { class: 'tag is-stale' }, icon('warn'), dk.warn) : null,
            h('span', { class: 'mono val' }, `${dk.free} G 可用 / ${dk.total} G`)),
          h('div', {
            class: `diskbar${tight ? ' tight' : ''}`,
            role: 'img', 'aria-label': `${dk.label} 已用 ${usedPct}%，剩余 ${dk.free} G`,
          }, h('i', { style: { width: `${usedPct}%` } })),
          dk.note ? h('div', { class: 'sub2' }, dk.note) : null);
      }),
      h('div', { class: 'sub' }, '预计中间产物 ~180 GB（示意值）· E: 可用 483 GB ✓ 满足磁盘预算前置检查（§4.10）'));

    const demoLabel = (t) => (t.status === 'OK'
      ? (t.ref.includes('实测') ? 'A 实测配置' : 'A 上游默认') : '⚠ PENDING 未标定');

    return [
      LIVE.demoBanner('环境为静态示意数据（envdata.js），非本机实测。', {
        onRetry: () => { LIVE.invalidate(); refresh(); },
      }),
      h('h3', { class: 'sect' }, '运行环境'),
      runCard,
      h('h3', { class: 'sect' }, '磁盘'),
      diskCard,
      ...tailSections(thresholdRows(THRESHOLDS, demoLabel)),
    ];
  };

  // ---- 实测渲染：引擎（宿主徽标）/ 凭据 / 磁盘·CPU·内存 / WSL / 就绪检查 ----
  const liveBody = (LIVE, data) => {
    const hostBadge = (host) => h('span', {
      class: 'mono', 'aria-label': host === 'wsl' ? 'WSL 内探测' : '本机探测',
      style: {
        fontSize: '9px', padding: '0 5px', marginLeft: '6px', borderRadius: '999px',
        border: '1px solid var(--border-strong)',
        color: host === 'wsl' ? 'var(--accent)' : 'var(--text-2)',
      },
    }, host);

    // 现状：后端仅在 WSL 可达时透出 engine_probe（不可达原因不下发），
    // 所以 probed=false 统一按「不可达或未安装」渲染；error 分支为前向兼容保留。
    const wslTag = data.wsl.ok
      ? h('span', { class: 'tag is-ok' }, icon('check'), `发行版 ${data.wsl.distro} 可达`)
      : data.wsl.probed
      ? h('span', { class: 'tag is-bad' }, icon('x'), '发行版不可达')
      : h('span', { class: 'tag is-bad' }, icon('x'), '不可达或未安装');
    const wslDetail = data.wsl.ok
      ? (data.wsl.enginePrefix ? `WSL 内 conda 引擎环境：${data.wsl.enginePrefix}`
                               : 'WSL 发行版可达，但未发现 conda 引擎环境')
      : (data.wsl.error
          || '未检测到可达的 WSL 发行版（wsl.exe 缺失、未装发行版，或本次未探测）· 引擎清单仅含本机探测结果');

    const time = new Date(data.fetchedAt).toLocaleTimeString('zh-CN', { hour12: false });

    const runCard = h('div', { class: 'envcard' },
      h('div', { class: 'erow' },
        h('span', { class: 'k' }, 'WSL'),
        h('span', { class: 'v' }, wslTag),
        h('button', {
          class: 'btn btn-gho btn-sm', type: 'button',
          'aria-label': '重新探测环境（跳过 30 秒缓存）',
          onclick: () => { LIVE.invalidate(); refresh(); },
        }, icon('refresh'), '刷新')),
      h('div', { class: 'sub' }, wslDetail),
      h('div', { class: 'erow' },
        h('span', { class: 'k' }, '宿主'),
        h('span', { class: 'v' }, `${data.host.platform} · Python ${data.host.python}`)),
      h('div', { class: 'erow' },
        h('span', { class: 'k' }, '引擎'),
        h('span', { class: 'v' }, `${data.engineOk} 可用 · ${data.engineMissing} 缺失`,
          h('span', { class: 'hint' }, '　徽标 = 探测宿主（local / wsl）'))),
      ...data.engines.map((e) => h('div', { class: 'eng' },
        h('span', { class: 'nm' }, e.name, hostBadge(e.host)),
        h('span', { class: 'ver' }, e.ok ? (e.ver === 'present' ? '已装' : e.ver) : '—'),
        h('span', { class: `st ${e.ok ? 'ok' : 'bad'}` }, icon(e.ok ? 'check' : 'x')),
        h('span', { class: 'enote' }, e.ok ? '' : '未探测到'))),
      h('div', { class: 'sub' }, `实测数据 · GET /api/env · ${time} 更新（缓存 30 s，「刷新」强制重探）`));

    const credCard = h('div', { class: 'envcard' },
      ...data.credentials.map((c) => h('div', { class: 'erow' },
        h('span', { class: 'k mono' }, c.id),
        h('span', { class: 'v' }, c.label),
        h('span', { class: `tag is-${c.ok ? 'ok' : 'bad'}` },
          icon(c.ok ? 'check' : 'x'), c.ok ? '已配置' : '缺失'))));

    const { diskFreeGb, diskTotalGb, cpuCount, memGb } = data.host;
    const usedPct = diskTotalGb ? Math.round((1 - diskFreeGb / diskTotalGb) * 100) : 0;
    const tight = diskTotalGb ? diskFreeGb / diskTotalGb < 0.3 : false;
    const diskCard = h('div', { class: 'envcard' },
      h('div', { class: 'drow' },
        h('div', { class: 'top' },
          h('span', { class: 'lbl' }, '工作区磁盘'),
          tight ? h('span', { class: 'tag is-stale' }, icon('warn'), '余量紧张') : null,
          h('span', { class: 'mono val' }, `${diskFreeGb} G 可用 / ${diskTotalGb} G`)),
        h('div', {
          class: `diskbar${tight ? ' tight' : ''}`,
          role: 'img', 'aria-label': `工作区磁盘已用 ${usedPct}%，剩余 ${diskFreeGb} G`,
        }, h('i', { style: { width: `${usedPct}%` } }))),
      h('div', { class: 'erow' },
        h('span', { class: 'k' }, 'CPU'),
        h('span', { class: 'v' }, cpuCount != null ? `${cpuCount} 逻辑核` : '未知')),
      h('div', { class: 'erow' },
        h('span', { class: 'k' }, '内存'),
        h('span', { class: 'v' }, memGb != null ? `${memGb} GB` : '未知')));

    // 就绪检查（/api/setup/status）：该端点单独失败不拖垮面板，只降级本卡
    const checkMark = (ok) => h('span', {
      style: { display: 'flex', flexShrink: '0', color: ok ? 'var(--ok)' : 'var(--bad)' },
    }, icon(ok ? 'check' : 'x', 12));
    const setupCard = data.setup
      ? h('div', { class: 'envcard' },
          h('div', { class: 'erow' },
            h('span', { class: 'k' }, '总评'),
            h('span', { class: 'v' },
              h('span', { class: `tag is-${data.setup.ready ? 'ok' : 'stale'}` },
                icon(data.setup.ready ? 'check' : 'warn'),
                data.setup.ready ? '就绪：必选项全部通过' : '未就绪：存在未通过的必选项'))),
          ...data.setup.checks.map((c) => h('div', { class: 'erow' },
            checkMark(c.ok),
            h('span', { class: 'v' }, c.message,
              c.required ? null : h('span', { class: 'hint' }, '　可选'),
              !c.ok && c.fixHint ? h('div', { class: 'hint' }, `处置：${c.fixHint}`) : null))))
      : h('div', { class: 'envcard' },
          h('div', { class: 'sub' },
            'GET /api/setup/status 不可用 —— 就绪检查暂缺（不影响其余实测数据）。'));

    return [
      h('h3', { class: 'sect' }, '运行环境'),
      runCard,
      h('h3', { class: 'sect' }, '凭据'),
      credCard,
      h('h3', { class: 'sect' }, '磁盘 / CPU / 内存'),
      diskCard,
      h('h3', { class: 'sect' }, '就绪检查'),
      setupCard,
      ...tailSections(thresholdRows(data.thresholds, LIVE.thresholdSourceLabel)),
    ];
  };

  (async () => {
    const LIVE = await import('./envlive.js');
    if (!root.isConnected) return;
    root.replaceChildren(LIVE.skeleton());       // 骨架屏：等待探测结果
    const data = await LIVE.fetchEnvLive();
    // 服务端阈值台账落进全局状态(证据上限推导的权威来源,面板切走也成立)
    if (data) setServerThresholds(data.thresholds);
    if (!root.isConnected) return;                // 面板已切走，丢弃过期结果
    root.replaceChildren(...(data ? liveBody(LIVE, data) : demoBody(LIVE)));
  })();

  return root;
}

/* ============================================================
   终端视图（面板 8 · §7.7）
   实时：步骤下拉（/api/state）→ 该步日志尾部（/api/logs）
        + 正则过滤 + ERROR/WARNING 高亮 + 跳到末尾。
   后端不可达 → 错误态带重试；会话没跑过 → 空态带运行引导。
   ============================================================ */
const termLive = { step: null, filter: '' };

/** 终端面板的错误态：后端不可达/响应异常（/api/state 归一为 null）。 */
function termErrorBody() {
  return h('div', null,
    h('h3', { class: 'sect' }, '终端 · 步骤日志（服务端）'),
    ES.renderError(null, {
      message: '运行状态读取失败——后端不可达或响应异常（GET /api/state）。',
      retry: () => { liveCache.delete(`state:${S.sessionId}`); refresh(); },
    }));
}

function termView() {
  const body = h('div', null,   // states 接入:请求中分支 → 段落骨架(等待 /api/state)
    ES.renderSkeleton(null, { kind: 'text', rows: 5, label: '正在读取运行状态（GET /api/state）' }));
  /* /api/state 可达 → 实时路径（选步骤 + /api/logs 读日志）；
     不可达（null）→ 错误态带重试，绝不渲染演示日志。
     state 有 3 秒短缓存（cachedFetch），紧随其后的 loadTermView 同键读取直接复用。 */
  loadTermView(body);
  return body;
}

async function loadTermView(body) {
  const state = await cachedFetch(`state:${S.sessionId}`, () => API.fetchState());
  if (!body.isConnected) return;                      // 面板已切走/重渲染，丢弃过期结果
  if (state === null) { body.replaceChildren(termErrorBody()); return; }
  if (!state.steps?.length) {
    body.replaceChildren(h('div', null,   // states 接入:无数据分支 → 空态卡 + 既有运行入口(自定义事件解耦)
      h('h3', { class: 'sect' }, '终端 · 步骤日志（服务端）'),
      ES.renderEmpty(null, { icon: 'terminal', title: '还没有运行记录',
        hint: '运行一次流水线即可生成——每步日志（log_path 尾部）在此可读、可过滤。',
        action: { label: '运行流水线', event: 'states:run-pipeline' } })));
    return;
  }
  body.replaceChildren(termLiveView(state));
}

/** 日志行着色：ERROR/WARNING 高亮（经典大写日志级别 + Traceback），命令行提示符青色。 */
function lineTone(ln) {
  if (/ERROR|CRITICAL|Traceback/.test(ln)) return ' is-err';
  if (/WARN/.test(ln)) return ' is-warn';
  if (/^\$\s/.test(ln)) return ' is-cmd';
  return '';
}

function termLiveView(state) {
  const steps = state.steps;
  if (!steps.some((s) => s.id === termLive.step)) {
    // 默认选中最后一个「跑过」的步骤（pending 必然 404），一个都没跑过则选第一步
    const ran = steps.filter((s) => s.state !== 'pending');
    termLive.step = (ran.length ? ran[ran.length - 1] : steps[0]).id;
  }
  const runId = state.run?.run_id;

  const sel = h('select', {
    'aria-label': '选择要查看日志的步骤',
    onchange: (e) => { termLive.step = Number(e.target.value); refresh(); },
  }, ...steps.map((s) => h('option', {
    value: String(s.id), selected: s.id === termLive.step || undefined,
  }, `${String(s.id).padStart(2, '0')} ${s.name} · ${s.state}${s.stale ? '（STALE）' : ''}`)));

  const logBox = h('pre', {
    class: 'term-log', tabindex: '0',
    'aria-label': `第 ${termLive.step} 步日志`,
  }, h('span', { class: 'tl is-dim' }, '读取日志中…（GET /api/logs）'));
  const meta = h('div', { class: 'term-meta' }, '—');

  let lineEls = [];
  const applyFilter = () => {
    const q = termLive.filter.trim();
    let re = null;
    if (q) {
      try { re = new RegExp(q, 'i'); }
      catch { re = new RegExp(q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'i'); }   // 无效正则退回文本匹配
    }
    let shown = 0;
    for (const el of lineEls) {
      const hit = !re || re.test(el.textContent);
      el.classList.toggle('hide', !hit);
      if (hit) shown++;
    }
    meta.textContent = (re ? `${shown}/${lineEls.length} 行匹配` : `${lineEls.length} 行`) + metaSuffix;
  };
  let metaSuffix = '';

  (async () => {
    const log = await cachedFetch(
      `logs:${S.sessionId}:${runId}:${termLive.step}`,
      () => API.fetchLogs(termLive.step, { runId }));
    if (!logBox.isConnected) return;
    if (log === null) {
      // states 接入:catch/不可达分支 → 错误态(必带重试:清该步日志缓存后重渲染)
      logBox.replaceChildren(ES.renderError(null, { message: '日志读取失败——后端不可达或响应异常。',
        retry: () => { liveCache.delete(`logs:${S.sessionId}:${runId}:${termLive.step}`); refresh(); } }));
      return;
    }
    if (log.missing) {
      // states 接入:无数据分支 → 空态卡(该步未执行过,/api/logs 404)
      logBox.replaceChildren(ES.renderEmpty(null, { icon: 'terminal', title: '这一步还没有日志',
        hint: '执行过该步骤即可生成——日志文件（log_path）落盘后在此可读；也可能已被清理。' }));
      meta.textContent = '0 行';
      return;
    }
    const lines = log.text.replace(/\r/g, '').replace(/\n$/, '').split('\n');   // Windows CRLF 日志去 \r
    lineEls = lines.map((ln) => h('span', { class: `tl${lineTone(ln)}` }, ln));
    logBox.replaceChildren(...lineEls);
    metaSuffix = log.truncated ? ` · 已截断至尾部（全文 ${Math.round((log.size || 0) / 1024)} KB）` : '';
    applyFilter();
    logBox.scrollTop = logBox.scrollHeight;   // 默认贴底：最新输出优先
  })();

  const filterInp = h('input', {
    type: 'search', value: termLive.filter,
    placeholder: '过滤日志 · 支持正则（如 ERROR|WARN）',
    'aria-label': '过滤日志，支持正则表达式',
    oninput: (e) => { termLive.filter = e.target.value; applyFilter(); },
  });

  return h('div', null,
    h('h3', { class: 'sect' }, '终端 · 步骤日志（服务端）'),
    h('div', { class: 'field' }, h('label', null, '步骤'), sel),
    h('div', { class: 'term-tools' },
      filterInp,
      h('button', {
        class: 'btn btn-gho btn-sm', type: 'button', 'aria-label': '跳到日志末尾',
        onclick: () => { logBox.scrollTop = logBox.scrollHeight; },
      }, icon('chevron'), '跳到末尾'),
      h('button', {
        class: 'btn btn-gho btn-sm', type: 'button', 'aria-label': '重新读取日志',
        onclick: () => {
          liveCache.delete(`logs:${S.sessionId}:${runId}:${termLive.step}`);
          refresh();
        },
      }, icon('refresh'), '刷新')),
    logBox,
    meta,
    h('p', { class: 'blurb' },
      '日志读取 GET /api/logs（该步 log_path 的尾部 64 KB）。' +
      'ERROR / WARNING 行自动高亮；过滤支持正则，无效正则自动退回文本匹配。'));
}

/* ============================================================
   轨迹视图（面板 7 · Lab Notebook）
   实时：GET /api/trace（SQLite trace 表，OpenDiscoveryTrace 对齐），
        表格式列出 step_no / phase / action / error / revision_trigger，
        顶部「导出 JSON」（Blob 下载）。
   后端不可达 → 错误态带重试；没有轨迹记录 → 空态带运行引导。
   ============================================================ */
function traceView() {
  const body = h('div', null,   // states 接入:请求中分支 → 列表骨架(等待 /api/trace)
    ES.renderSkeleton(null, { kind: 'list', rows: 6, label: '正在读取轨迹（GET /api/trace）' }));
  loadTraceView(body);
  return body;
}

async function loadTraceView(body) {
  const rows = await cachedFetch(`trace:${S.sessionId}`, () => API.fetchTrace());
  if (!body.isConnected) return;
  if (rows === null) {
    // 后端不可达/响应异常 → 错误态带重试，绝不渲染演示轨迹
    body.replaceChildren(h('div', null,
      h('h3', { class: 'sect' }, '轨迹 · OpenDiscoveryTrace（服务端）'),
      ES.renderError(null, {
        message: '轨迹读取失败——后端不可达或响应异常（GET /api/trace）。',
        retry: () => { liveCache.delete(`trace:${S.sessionId}`); refresh(); },
      })));
    return;
  }
  body.replaceChildren(traceLiveView(rows));
}

/** action 列：trace 表里 action 存 canonical JSON 字符串，取 tool 名作类型。 */
function actionKind(action) {
  if (!action) return '—';
  try {
    const a = typeof action === 'string' ? JSON.parse(action) : action;
    return a.tool || a.kind || (Object.keys(a).length ? Object.keys(a)[0] : '—');
  } catch {
    return String(action).slice(0, 24) || '—';
  }
}

function traceLiveView(rows) {
  const errs = rows.filter((r) => r.error_occurred).length;
  const revs = rows.filter((r) => r.revision_trigger).length;

  const table = h('table', { class: 'grid' },
    h('thead', null, h('tr', null,
      h('th', null, '#'), h('th', null, 'step'), h('th', null, 'phase'),
      h('th', null, 'action'), h('th', null, 'error'), h('th', null, 'revision'))),
    h('tbody', null, ...rows.map((r, i) => h('tr', {
      // 长字段不进表格：悬停行可见 thought / observation / 错误信息
      title: [r.thought, r.observation, r.error_message].filter(Boolean).join('\n') || undefined,
    },
      h('td', { class: 'mono' }, String(i + 1)),
      h('td', { class: 'mono' }, r.step_no ?? '—'),
      h('td', null, r.phase || '—'),
      h('td', { class: 'mono' }, actionKind(r.action)),
      h('td', null, r.error_occurred
        ? h('span', { class: 'tag is-bad' }, icon('x'), r.error_type || 'error')
        : '—'),
      h('td', null, r.revision_trigger
        ? h('span', { class: 'tag is-stale' }, r.revision_trigger)
        : '—')))));

  return h('div', null,
    h('h3', { class: 'sect' }, '轨迹 · OpenDiscoveryTrace（服务端）'),
    h('div', { class: 'tstats' },
      h('span', { class: 'tag' }, `${rows.length} 条记录`),
      h('span', { class: `tag${errs ? ' is-bad' : ''}` }, `${errs} 次 error`),
      h('span', { class: `tag${revs ? ' is-stale' : ''}` }, `${revs} 次 revision`)),
    h('div', { class: 'term-acts', style: { marginTop: '0', marginBottom: '8px' } },
      h('button', {
        class: 'btn btn-pri btn-sm', type: 'button', 'aria-label': '导出轨迹为 JSON',
        onclick: () => {
          download(`trace_${S.sessionId}.json`, JSON.stringify({
            schema: 'OpenDiscoveryTrace/1.0',
            session: S.sessionId,
            generated_at: new Date().toISOString(),
            source: 'GET /api/trace',
            entries: rows,
          }, null, 2), 'application/json');
          toast('已导出轨迹 JSON（服务端数据）');
        },
      }, '导出 JSON'),
      h('button', {
        class: 'btn btn-gho btn-sm', type: 'button', 'aria-label': '刷新轨迹',
        onclick: () => { liveCache.delete(`trace:${S.sessionId}`); refresh(); },
      }, icon('refresh'), '刷新')),
    // states 接入:无数据分支 → 空态卡 + 既有运行入口(自定义事件解耦)
    rows.length ? table : ES.renderEmpty(null, { icon: 'trace', title: '还没有轨迹记录',
      hint: '发起一次规划或执行即可生成——每一步的 phase / action / error 在此可审。',
      action: { label: '运行流水线', event: 'states:run-pipeline' } }),
    h('p', { class: 'blurb' },
      'schema 对齐 OpenDiscoveryTrace：step_no / phase / action / error / revision_trigger。' +
      '悬停行可见 thought 与 observation；导出 JSON 含全部字段。'));
}

/** 面板联动（§7.3 第 4 条）：跳到流水线面板并选中/滚动到该步，高亮一闪示意落点。 */
function gotoStep(stepId) {
  S.selectedStep = stepId;
  setTab('pipeline');
  const el = refs.panes.pipeline.querySelector(`[data-step="${stepId}"]`);
  if (!el) return;
  el.scrollIntoView({ block: 'center' });
  el.classList.remove('flash'); void el.offsetWidth; el.classList.add('flash');
}

/** 供外部把某步高亮闪一下（STALE 级联动画）。 */
export function flashSteps(ids) {
  if (S.dockTab !== 'pipeline') return;
  for (const id of ids) {
    const el = refs.panes.pipeline.querySelector(`[data-step="${id}"]`);
    if (el) { el.classList.remove('flash'); void el.offsetWidth; el.classList.add('flash'); }
  }
}

export function openFile(path) {
  S.selectedFile = path;
  setTab('files');
}

export function openImages() { setTab('images'); }

/** 面板联动（产物 chip → 文件面板）：切到 files 并滚动高亮该步骤的产物组。 */
export function selectStepFile(stepId) {
  setTab('files');
  import('./fileslive.js').then((m) => m.highlightStep(stepId)).catch(() => {});
}
