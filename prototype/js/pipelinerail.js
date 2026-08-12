/* ============================================================
   流水线面板信息密度组件(纯呈现层,零依赖,手写 SVG)
   —— 调研 RESEARCH-workflow-ui-2026-08-12 的四项高性价比移植:
   1. 依赖轨道 rail:列表左缘细 SVG,主干 1→11 直线 + 跨步边
      (1→3、7→11)绕行弧;节点按状态着色,点击高亮上游链。
      布局是手写常量(11 节点拓扑编译期已知,不引布局库,§4.7)。
   2. 失效原因 popover(Dagster staleStatusCauses 式):
      staleReason 五类闭集 → 三段指纹(task/args/eval)对应解释,
      上游因果链可点击跳转。
   3. 重跑影响集确认(Airflow Clear 弹窗式):先列全部将重跑步骤
      与预估时长,默认只勾选必需集合,确认后才执行。
   4. 状态计数摘要条(Seqera 状态卡式):N 完成 · M 缓存/跳过 ·
      K 失效 · J 待跑,点击滚动到首个对应行。
   语义分工:SVG 一律 aria-hidden,可访问语义仍由 DOM 列表承担。
   ============================================================ */
import { h, txt } from './dom.js';
import { STEP_DEFS, downstreamOf, estimateRerunHonest } from './state.js';

const SVG_NS = 'http://www.w3.org/2000/svg';

/* 轨道几何常量:总宽 26px 表达全部拓扑(调研 §3 第 4 项) */
export const RAIL_W = 26;
const LANE_X = 17;      // 主干车道 x
const ARC_X = 5;        // 跨步边绕行车道 x(仅需一条备用车道)
const NODE_R = 4.5;

/* ---------------- 拓扑(来自 STEP_DEFS.deps,与 registry 对齐) ---------------- */

/** 全部依赖边 [from, to]。11 步 = 10 条主干 + 2 条跨步(1→3、7→11)。 */
export function railEdges(defs = STEP_DEFS) {
  const edges = [];
  for (const d of defs) for (const dep of d.deps) edges.push([dep, d.id]);
  return edges;
}

/** 上游依赖传递闭包(不含自身),升序。点击节点高亮用。 */
export function upstreamChain(id, defs = STEP_DEFS) {
  const byId = new Map(defs.map((d) => [d.id, d]));
  const seen = new Set();
  const queue = [...(byId.get(id)?.deps || [])];
  while (queue.length) {
    const cur = queue.shift();
    if (seen.has(cur)) continue;
    seen.add(cur);
    queue.push(...(byId.get(cur)?.deps || []));
  }
  return [...seen].sort((a, b) => a - b);
}

/* ---------------- 状态归类(rail / 摘要条共用一套口径) ---------------- */

/** 视图模型 → 八类展示态。stale 优先(盖过 done/stale 本身)。 */
export function stepCategory(s) {
  if (s.stale || s.state === 'stale') return 'stale';
  if (s.state === 'skipped') return 'skipped';
  if (s.state === 'done') return 'done';
  if (s.state === 'running') return 'running';
  if (s.state === 'failed') return 'failed';
  if (s.state === 'interrupted' || s.state === 'orphaned') return 'resume';
  return 'pending';
}

const CAT_CLS = {
  done: 'is-done', skipped: 'is-skip', stale: 'is-stale', pending: 'is-pend',
  running: 'is-run', failed: 'is-fail', resume: 'is-resume',
};

/* 节点内小字形:形状信息不只靠颜色(色盲教训,调研 §2.1) */
const CAT_GLYPH = { skipped: '↷', stale: '!', failed: '✗' };

export function summarize(steps) {
  const c = { done: 0, skipped: 0, stale: 0, pending: 0, running: 0, failed: 0, resume: 0 };
  for (const s of steps) c[stepCategory(s)] += 1;
  return c;
}

/* ---------------- 摘要条(Seqera 状态计数卡的单行版) ---------------- */

/** 计数摘要条。点击某计数 → onJump(首个该类步骤 id)。 */
export function summaryBar(steps, { onJump } = {}) {
  const c = summarize(steps);
  const chip = (key, label, n, always) => {
    if (!n && !always) return null;
    return h('button', {
      class: `plr-chip is-${key}${n ? '' : ' zero'}`, type: 'button',
      dataset: { cat: key }, disabled: !n || undefined,
      'aria-label': `${label} ${n} 步${n ? ',点击跳到首个' : ''}`,
      onclick: () => {
        const hit = steps.find((s) => stepCategory(s) === key);
        if (hit) onJump?.(hit.id);
      },
    }, h('b', null, String(n)), ` ${label}`);
  };
  return h('div', { class: 'plr-sum', role: 'group', 'aria-label': '本次运行状态摘要' },
    chip('done', '完成', c.done, true),
    chip('skipped', '缓存/跳过', c.skipped, true),
    chip('stale', '失效', c.stale, true),
    chip('pending', '待跑', c.pending, true),
    chip('running', '运行中', c.running, false),
    chip('failed', '失败', c.failed, false),
    chip('resume', '断点', c.resume, false));
}

/* ---------------- 依赖轨道 rail(手写 SVG,figures.js 先例) ---------------- */

function svgEl(tag, attrs = {}) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, String(v));
  return el;
}

/**
 * 生成轨道 SVG。wrap 是包含 .pstep[data-step] 行的已挂载容器
 * (position:relative),节点 y 直接取各行实际 offsetTop —— 调研 §4.7
 * 明确的坑:行高会变(换行/详情展开),必须在 refresh 后按实测重算。
 */
export function renderRail(wrap, steps, { onPick, flashMs = 1500 } = {}) {
  const rows = new Map();
  for (const el of wrap.querySelectorAll('.pstep')) {
    const id = Number(el.dataset.step);
    if (id) rows.set(id, el);
  }
  const stById = new Map(steps.map((s) => [s.id, s]));
  const ys = new Map();
  let bottom = 0;
  for (const [id, el] of rows) {
    const y = (el.offsetTop || 0) + (el.offsetHeight || 0) / 2;
    ys.set(id, y);
    bottom = Math.max(bottom, (el.offsetTop || 0) + (el.offsetHeight || 0));
  }
  const H = Math.max(1, Math.ceil(bottom));

  const svg = svgEl('svg', {
    class: 'plr-rail', width: RAIL_W, height: H,
    viewBox: `0 0 ${RAIL_W} ${H}`, 'aria-hidden': 'true',
  });

  // 边先画(节点覆盖线头);主干 = 相邻步直线,跨步边 = 绕行贝塞尔弧
  const edgesG = svgEl('g', { class: 'res' });
  for (const [a, b] of railEdges()) {
    const ya = ys.get(a), yb = ys.get(b);
    if (ya === undefined || yb === undefined) continue;
    let e;
    if (b - a === 1) {
      e = svgEl('line', { class: 're', x1: LANE_X, y1: ya, x2: LANE_X, y2: yb });
    } else {
      const k = Math.min(20, Math.abs(yb - ya) / 4);
      e = svgEl('path', {
        class: 're rc',
        d: `M ${LANE_X} ${ya} C ${ARC_X} ${ya + k} ${ARC_X} ${yb - k} ${LANE_X} ${yb}`,
      });
    }
    e.dataset.from = String(a);
    e.dataset.to = String(b);
    edgesG.appendChild(e);
  }
  svg.appendChild(edgesG);

  // 节点:圆点按状态着色,skipped/stale/failed 附加小字形(不只靠颜色)
  for (const [id, y] of ys) {
    const s = stById.get(id) || { state: 'pending' };
    const cat = stepCategory(s);
    const g = svgEl('g', { class: `rn ${CAT_CLS[cat]}` });
    g.dataset.node = String(id);
    g.appendChild(svgEl('circle', { cx: LANE_X, cy: y, r: NODE_R }));
    const glyph = CAT_GLYPH[cat];
    if (glyph) {
      const t = svgEl('text', { class: 'rg', x: LANE_X, y: y + 2.4, 'text-anchor': 'middle' });
      t.appendChild(document.createTextNode(glyph));
      g.appendChild(t);
    }
    g.addEventListener('click', () => highlightUpstream(svg, id, { flashMs, onPick }));
    svg.appendChild(g);
  }
  return svg;
}

/** 点击节点:上游链(传递闭包)变色 flashMs 毫秒,其余降透明度。 */
export function highlightUpstream(svg, id, { flashMs = 1500, onPick } = {}) {
  const chain = upstreamChain(id);
  const keep = new Set([id, ...chain]);
  for (const g of svg.querySelectorAll('.rn')) {
    const nid = Number(g.dataset.node);
    g.classList.toggle('hl', keep.has(nid));
    g.classList.toggle('dim', !keep.has(nid));
  }
  for (const e of svg.querySelectorAll('.re')) {
    const on = keep.has(Number(e.dataset.from)) && keep.has(Number(e.dataset.to));
    e.classList.toggle('hl', on);
    e.classList.toggle('dim', !on);
  }
  onPick?.(id, chain);
  clearTimeout(svg.__hlTimer);
  svg.__hlTimer = setTimeout(() => {
    for (const el of [...svg.querySelectorAll('.rn'), ...svg.querySelectorAll('.re')]) {
      el.classList.remove('hl');
      el.classList.remove('dim');
    }
  }, flashMs);
  return chain;
}

/**
 * 挂载轨道并保持 y 坐标新鲜:立即按实测 offsetTop 画一次;
 * 容器尺寸变化(拖拽 dock 宽度导致换行)时重画。refresh() 整体重建
 * 面板时旧容器脱离文档,观察器随之自行断开。
 */
export function mountRail(wrap, steps, opts = {}) {
  let ro = null;
  const paint = () => {
    if (!wrap.isConnected) { ro?.disconnect(); return; }
    wrap.querySelector('.plr-rail')?.remove();
    wrap.insertBefore(renderRail(wrap, steps, opts), wrap.childNodes[0] || null);
  };
  if (typeof ResizeObserver !== 'undefined') {
    ro = new ResizeObserver(paint);
    ro.observe(wrap);
  }
  paint();
  return wrap.querySelector('.plr-rail');
}

/* ---------------- 失效原因解析(三段指纹 ←→ 五类闭集) ---------------- */

/* 服务端 stale_reason 闭集(core/stale.py)→ 指纹段 + 中文解释。
   段口径:task=方法/版本变更、args=科学参数变更、eval=上游级联。 */
export const STALE_EXPLAIN = {
  method_changed: { seg: 'task', label: '方法变更',
    note: 'task 段指纹变化:本步换了处理方法,旧产物按新方法不再可信。' },
  tool_upgraded: { seg: 'task', label: '工具版本变更',
    note: 'task 段指纹变化:该步所用引擎/工具版本升级,旧结果无法与新版对齐复现。' },
  param_changed: { seg: 'args', label: '科学参数变更',
    note: 'args 段指纹变化:影响科学结论的参数被修改,旧产物与当前配置不一致。' },
  upstream_changed: { seg: 'eval', label: '上游级联',
    note: 'eval 段指纹变化:上游步骤重算或变更,本步输入已不是旧产物的输入。' },
  artifact_missing: { seg: 'artifact', label: '产物缺失/被改',
    note: '产物指纹与台账记录不符:文件被删除或内容被外部修改。' },
};

export function parseStaleReason(reason) {
  return STALE_EXPLAIN[reason] || {
    seg: 'eval', label: '原因待定',
    note: `后端未提供失效原因${reason ? `(${reason})` : ''},按保守策略视为需重算。`,
  };
}

/**
 * 上游失效因果链:本步的上游闭包中所有失效步骤(近 → 远),
 * root = 首个「自身原因失效」(staleReason 非 upstream_changed)的上游,
 * 即级联的始作俑者;全是级联时取最远端。
 */
export function staleCauseChain(id, steps) {
  const stById = new Map(steps.map((s) => [s.id, s]));
  const chain = upstreamChain(id)
    .filter((uid) => {
      const s = stById.get(uid);
      return s && (s.stale || s.state === 'stale');
    })
    .sort((a, b) => b - a);
  const root = chain.find((uid) => {
    const r = stById.get(uid)?.staleReason;
    return r && r !== 'upstream_changed';
  }) ?? chain[chain.length - 1] ?? null;
  return { chain, root };
}

/* ---------------- 浮层管理(单例:定位 + Esc + 点外关闭,零依赖) ---------------- */

let overlayTeardown = null;

export function closeOverlay() {
  overlayTeardown?.();
  overlayTeardown = null;
}

/** 挂浮层:popover 锚定在 anchor 下方(fixed,视口坐标),modal 居中 + 遮罩。 */
function mountOverlay(node, { anchor = null, modal = false } = {}) {
  closeOverlay();
  let scrim = null;
  if (modal) {
    scrim = h('div', { class: 'plr-scrim', 'aria-hidden': 'true' });
    document.body.appendChild(scrim);
  }
  document.body.appendChild(node);
  if (!modal && anchor?.getBoundingClientRect) {
    const r = anchor.getBoundingClientRect();
    const vw = (typeof window !== 'undefined' && window.innerWidth) || 1200;
    const vh = (typeof window !== 'undefined' && window.innerHeight) || 800;
    const w = 292;   // 与 rail.css 的 .plr-pop 宽度一致
    node.style.left = `${Math.round(Math.max(8, Math.min(r.left ?? 8, vw - w - 8)))}px`;
    const hEst = node.offsetHeight || 180;
    const below = (r.bottom ?? 0) + 6;
    node.style.top = `${Math.round(below + hEst > vh - 8 ? Math.max(8, (r.top ?? 0) - hEst - 6) : below)}px`;
  }
  const onKey = (e) => { if (e.key === 'Escape') closeOverlay(); };
  const onDown = (e) => { if (!node.contains(e.target)) closeOverlay(); };
  const onScroll = (e) => { if (!node.contains(e.target)) closeOverlay(); };   // 锚点随滚动漂移,直接关闭
  document.addEventListener('keydown', onKey);
  document.addEventListener('pointerdown', onDown);
  if (!modal) document.addEventListener('scroll', onScroll, true);
  overlayTeardown = () => {
    document.removeEventListener('keydown', onKey);
    document.removeEventListener('pointerdown', onDown);
    if (!modal) document.removeEventListener('scroll', onScroll, true);
    node.remove();
    scrim?.remove();
  };
  return node;
}

/* ---------------- 失效原因 popover(Dagster 式) ---------------- */

/**
 * step:{id,name,state,stale,staleReason,fingerprint};steps:全部视图模型。
 * onGoto(id) 跳转上游步骤;onImpact(id) 打开重跑影响确认。
 */
export function openStalePopover({ anchor, step, steps, onGoto, onImpact }) {
  const local = !step.staleReason;   // 后端不可达时无 staleReason,按本地状态推断
  const { chain, root } = staleCauseChain(step.id, steps);
  const info = local && chain.length
    ? parseStaleReason('upstream_changed')          // 本地推断:上游有失效 → 级联
    : local
    ? { seg: 'args', label: '方法/参数变更',
        note: '本地推断:本步配置已改动(离线态无法区分 task/args 段,以服务端指纹为准)。' }
    : parseStaleReason(step.staleReason);

  const stById = new Map(steps.map((s) => [s.id, s]));
  const rootStep = root != null ? stById.get(root) : null;
  const cascade = chain.filter((x) => x !== root).sort((a, b) => a - b);

  const pop = h('div', { class: 'plr-pop', role: 'dialog', 'aria-label': `第 ${step.id} 步失效原因` },
    h('div', { class: 'hd' },
      h('span', { class: 'tag is-stale' }, '失效'),
      `第 ${step.id} 步 · ${step.name || ''}`,
      h('button', {
        class: 'x', type: 'button', 'aria-label': '关闭',
        onclick: () => closeOverlay(),
      }, '×')),
    h('div', { class: 'bd' },
      h('div', { class: 'seg' },
        h('span', { class: `chip seg-${info.seg}` }, `${info.seg} 段`),
        h('b', null, info.label),
        local ? h('span', { class: 'chip local' }, '本地推断') : null),
      h('p', { class: 'note' }, info.note),
      rootStep ? h('div', { class: 'chain' },
        h('span', { class: 'k' }, '因果链'),
        h('button', {
          class: 'lnk', type: 'button',
          'aria-label': `跳转到变更源第 ${rootStep.id} 步 ${rootStep.name || ''}`,
          onclick: () => { closeOverlay(); onGoto?.(rootStep.id); },
        }, `#${rootStep.id} ${rootStep.name || ''}`),
        txt(`(${parseStaleReason(rootStep.staleReason).label})`),
        cascade.length ? txt(` → 沿 ${cascade.map((x) => `#${x}`).join(' ')} 级联`) : null,
        txt(' → 本步')) : null,
      h('div', { class: 'fp' },
        h('span', { class: 'k' }, '当前指纹'),
        h('span', { class: 'mono' }, step.fingerprint || '—'))),
    h('div', { class: 'ft' },
      h('button', {
        class: 'btn btn-wrn btn-sm', type: 'button',
        onclick: () => { closeOverlay(); onImpact?.(step.id); },
      }, '查看影响'),
      h('button', {
        class: 'btn btn-gho btn-sm', type: 'button',
        onclick: () => closeOverlay(),
      }, '关闭')));
  return mountOverlay(pop, { anchor });
}

/* ---------------- 重跑影响集确认(Airflow Clear 式) ---------------- */

/* 影响行原因 → 中文(与 app.js 的 IMPACT_REASON_ZH 同源口径) */
export const IMPACT_REASON_ZH = {
  method_changed: '换了方法', param_changed: '改了参数', upstream_changed: '上游级联',
  tool_upgraded: '工具版本变了', artifact_missing: '产物缺失', manual_rerun: '手动重跑',
};

const STATE_BEFORE_ZH = {
  done: '已完成 · 将重算', stale: '已失效 · 将重算', failed: '失败 · 将重跑',
  pending: '未运行 · 将首次运行', skipped: '云端/缓存 · 将重算', running: '运行中',
  interrupted: '已中断 · 将续跑', orphaned: '环境中断 · 将续跑',
};

/**
 * 后端不可达 / 服务端视角无变化时的本地影响估算:
 * 自身 + deps 闭包全部下游;时长走 estimateRerunHonest(§7.5 不编数)。
 */
export function localImpact(stepId, steps) {
  const stById = new Map(steps.map((s) => [s.id, s]));
  const ids = [stepId, ...downstreamOf(stepId)];
  const affected = ids.map((id) => {
    const s = stById.get(id) || { state: 'pending' };
    return {
      step_id: id,
      reason: id === stepId ? 'manual_rerun' : 'upstream_changed',
      state_before: s.state === 'done' && s.stale ? 'stale' : s.state,
    };
  });
  const eta = estimateRerunHonest(ids);
  return {
    changedStep: stepId, reason: 'manual_rerun', affected,
    rerunMinutes: eta.known ? (eta.loSec + eta.hiSec) / 120 : null,
    rerunBasis: eta.known ? eta.label : '本机历史样本不足,不编数(§7.5)',
  };
}

/**
 * 确认卡:列出将重跑的全部步骤/预估时长/影响原因,确认后 onConfirm(ids)。
 * 默认范围保守:只勾选必需集合(服务端 affected 或本地 deps 闭包),
 * 触发步不可取消勾选;不提供「强制重跑未失效下游」(服务端按指纹跳过,承诺不越界)。
 */
export function openImpactCard({ step, impact, local = false, onConfirm }) {
  const affected = [...(impact.affected || [])].sort((a, b) => a.step_id - b.step_id);
  const defById = new Map(STEP_DEFS.map((d) => [d.id, d]));
  const boxes = new Map();

  const confirmBtn = h('button', { class: 'btn btn-wrn btn-sm', type: 'button' });
  const syncLabel = () => {
    const n = [...boxes.values()].filter((b) => b.checked).length;
    confirmBtn.textContent = `确认重跑 ${n} 步`;
    confirmBtn.disabled = n === 0;
  };

  const rows = affected.map((a) => {
    const box = h('input', {
      type: 'checkbox', 'aria-label': `重跑第 ${a.step_id} 步`,
      onchange: syncLabel,
    });
    box.checked = true;                              // 必需集合默认全选
    if (a.step_id === step.id) box.disabled = true;  // 触发步不可摘除
    boxes.set(a.step_id, box);
    return h('label', { class: 'imp-row' },
      box,
      h('span', { class: 'no mono' }, `#${a.step_id}`),
      h('span', { class: 'nm' }, defById.get(a.step_id)?.name || ''),
      h('span', { class: 'st' }, STATE_BEFORE_ZH[a.state_before] || a.state_before || ''),
      h('span', { class: 'why' }, IMPACT_REASON_ZH[a.reason] || a.reason || '—'));
  });

  const minutes = impact.rerunMinutes;
  const etaText = minutes === null || minutes === undefined
    ? '时长未知' : `约 ${Math.max(1, Math.round(minutes))} 分钟`;

  const card = h('div', {
    class: 'plr-imp', role: 'dialog', 'aria-modal': 'true',
    'aria-label': `从第 ${step.id} 步重跑的影响确认`,
  },
    h('div', { class: 'hd' },
      `重跑影响确认 · 从第 ${step.id} 步 ${step.name || ''}`,
      h('span', { class: `chip ${local ? 'local' : 'srv'}` },
        local ? '本地估算 · 后端不可达' : '服务端指纹推导')),
    h('div', { class: 'bd' },
      h('p', { class: 'note' },
        `以下 ${affected.length} 步的产物将被重算(默认只含必需的下游集合):`),
      h('div', { class: 'imp-list' }, ...rows),
      h('div', { class: 'eta' },
        h('span', { class: 'k' }, '预计'),
        h('b', null, etaText),
        impact.rerunBasis ? h('span', { class: 'basis' }, ` · ${impact.rerunBasis}`) : null)),
    h('div', { class: 'ft' },
      confirmBtn,
      h('button', {
        class: 'btn btn-gho btn-sm', type: 'button',
        onclick: () => closeOverlay(),
      }, '取消')));

  confirmBtn.addEventListener('click', () => {
    const ids = [...boxes.entries()].filter(([, b]) => b.checked).map(([id]) => id)
      .sort((a, b) => a - b);
    closeOverlay();
    onConfirm?.(ids);
  });
  syncLabel();
  return mountOverlay(card, { modal: true });
}
