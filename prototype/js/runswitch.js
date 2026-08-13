/* ============================================================
   run 历史切换器(流水线面板顶部一行,「run 历史入口」缺口的补齐)。

   背景:一个会话可积累多个 run(fork/重规划),后端 /api/state、
   /api/figures、/api/artifacts、/api/provenance 等都支持 run_id 查询
   参数,但前端此前永远只显示最新 run —— fork 后旧 run 从 UI 消失。

   职责:
   - GET /api/runs?session= 拉取该会话 run 清单(5s TTL 缓存);
   - 渲染一行紧凑切换器(dock.js pipelineView 只负责挂载):下拉首项
     「最新 · <时间戳>(<状态>)」= 缺省行为(请求不带 run_id),其余
     条目按 fork 谱系缩进展示 parent 链(buildRunTree 纯函数,node 单测
     直测);选中历史 run 后调用方给的 onSwitch 触发整面板重渲染;
   - activeRunId():当前选中的历史 run id(null = 最新)。各数据面板的
     fetch 层以它为缺省参数把 run_id 透传给后端,切回「最新」(或切换
     会话)即恢复缺省行为。接线清单(改动均在各模块内注明):
       · backend.sse.js fetchState({runId}) —— 新增可选参数,由 dock
         流水线视图显式传入;app.js 的本地镜像同步不传,永远跟最新 run;
       · gallery.js fetchFigures / fileslive.js fetchFilesLive /
         auditlive.js fetchAuditLive / reportlive.js fetchReportLive
         —— 可选 runId 缺省取 activeRunId(),缓存键携带 run id;
       · /api/logs 本就支持 run_id(backend.sse.js fetchLogs);终端与
         轨迹面板不在本次接线范围,维持最新 run 语义。
   - 只读语义:查看历史 run 时,流水线面板内执行类控件(重跑/失效
     popover 的重跑入口/方法与参数编辑)一律禁用并提示「历史 run 只读,
     fork 或回到最新再操作」—— 执行类端点均落在最新 run 上,历史视图里
     放行会让用户以为在操作眼前这个 run(MutationObserver 跟踪异步增强
     插入的按钮,pipelineView 的重跑按钮在 fetchState 返回后才挂)。

   失败语义:后端不可达 / file:// / 会话无 run → 不渲染任何 UI(演示
   模式零打扰);清单拉不到但已锁定历史 run 时保底给「回到最新」出口。
   ============================================================ */
import { h } from './dom.js';
import { S } from './state.js';

/** 只读提示文案(任务口径;title 与横幅共用,一字不改)。 */
export const READONLY_HINT = '历史 run 只读,fork 或回到最新再操作';

/* ---------------- 选择态(模块级:面板重渲染后仍保持) ---------------- */

let selected = null;         // 选中的历史 run id;null = 「最新」(缺省行为)
let selectedSession = null;  // 选择态所属会话:切会话后选择自动失效

/** 当前生效的历史 run id;null = 最新。各面板 fetch 层以此决定是否带 run_id。 */
export function activeRunId() {
  return selectedSession === S.sessionId ? selected : null;
}

/** 写选择态并返回归一化结果:显式选中最新 run(latestId)等价于回到「最新」。 */
export function setActiveRun(runId, latestId = null) {
  selected = runId && runId !== latestId ? runId : null;
  selectedSession = S.sessionId;
  return selected;
}

/* ---------------- 清单拉取(5s TTL,按会话隔离) ---------------- */

const TTL_MS = 5_000;
let cache = { at: 0, session: null, promise: null };

/** 手动刷新入口:清缓存,下一次 fetchRuns() 必然重新拉取。 */
export function invalidate() {
  cache = { at: 0, session: null, promise: null };
}

/** 该会话 run 清单(服务端已按 created_at 倒序)。
    后端不可达 / file:// / 非 2xx → null,绝不抛错(与各 live 模块同款)。 */
export function fetchRuns({ force = false } = {}) {
  if (location.protocol === 'file:') return Promise.resolve(null);
  if (!force && cache.promise && cache.session === S.sessionId
      && Date.now() - cache.at < TTL_MS) return cache.promise;

  const session = S.sessionId;
  const promise = (async () => {
    try {
      const resp = await fetch(`/api/runs?session=${encodeURIComponent(session)}`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      return Array.isArray(data.runs) ? data.runs : null;
    } catch {
      cache = { at: 0, session: null, promise: null };  // 失败不占缓存位
      return null;
    }
  })();
  cache = { at: Date.now(), session, promise };
  return promise;
}

/* ============================================================
   纯函数(node --test 直测,不碰 DOM)
   ============================================================ */

/** run_id 的时间戳前缀:"20260813T101112-ab12cd34[-fork]" → "20260813T101112"。
    非常规 id(无 '-')原样返回,空值给 '—' —— 下拉里绝不出现空白行。 */
export function runStamp(runId) {
  const head = String(runId ?? '').split('-')[0];
  return head || '—';
}

/** 步骤终态统计 → 紧凑后缀:" · ✓done ↷skipped ✗failed"(非零项才列,全零 → '')。 */
export function stepsSuffix(steps) {
  if (!steps || typeof steps !== 'object') return '';
  const parts = [];
  if (steps.done) parts.push(`✓${steps.done}`);
  if (steps.skipped) parts.push(`↷${steps.skipped}`);
  if (steps.failed) parts.push(`✗${steps.failed}`);
  return parts.length ? ` · ${parts.join(' ')}` : '';
}

/** fork 谱系树:扁平清单 → 缩进展示序列 [{run, depth}]。
    根(无父/父不在清单,例如父已被清理)按 created_at 倒序;子链挂在
    parent 之下同样倒序,depth = 父 + 1。坏数据成环时每行至多出现一次。 */
export function buildRunTree(runs) {
  const list = Array.isArray(runs) ? runs.filter((r) => r && r.run_id) : [];
  const ids = new Set(list.map((r) => r.run_id));
  const kids = new Map();
  const roots = [];
  for (const r of list) {
    const pid = r.parent_run_id;
    if (pid && pid !== r.run_id && ids.has(pid)) {
      if (!kids.has(pid)) kids.set(pid, []);
      kids.get(pid).push(r);
    } else {
      roots.push(r);
    }
  }
  const newestFirst = (a, b) => (b.created_at || 0) - (a.created_at || 0);
  const out = [];
  const seen = new Set();
  const walk = (r, depth) => {
    if (seen.has(r.run_id)) return;   // 环兜底(a↔b 互为 parent 的坏数据)
    seen.add(r.run_id);
    out.push({ run: r, depth });
    for (const c of (kids.get(r.run_id) || []).slice().sort(newestFirst)) {
      walk(c, depth + 1);
    }
  };
  for (const r of roots.slice().sort(newestFirst)) walk(r, 0);
  for (const r of list.slice().sort(newestFirst)) {
    if (!seen.has(r.run_id)) walk(r, 0);  // 互为 parent 的环成员没有根:按根补列,不丢行
  }
  return out;
}

/** 下拉条目文案:谱系缩进(全角空格 + └)+「最新 · 」前缀 + 时间戳(状态·统计)。 */
export function optionLabel(node, latestId = null) {
  const r = node.run;
  const indent = node.depth ? '\u3000'.repeat(node.depth - 1) + '└ ' : '';
  const head = r.run_id === latestId ? '最新 · ' : '';
  return `${indent}${head}${runStamp(r.run_id)}(${r.status || '?'}${stepsSuffix(r.steps)})`;
}

/* ============================================================
   只读语义:历史 run 视图禁用执行类控件
   ============================================================ */

/* 执行类控件闭集(都会落到「最新 run」的执行/干预队列上):
   .prerun = 行内「重跑」;.pbadge = 「失效」popover(内含重跑影响入口);
   .pdetail 的 select/input = 方法与参数编辑(SET_METHOD/SET_PARAMS)。
   数组参数的 input 本就 readonly,不重复处理。 */
const EXEC_CONTROLS = '.prerun, .pbadge, .pdetail select, .pdetail input:not([readonly])';

function applyReadonly(scope) {
  if (!scope || !activeRunId()) return;
  for (const el of scope.querySelectorAll(EXEC_CONTROLS)) {
    if (el.disabled) continue;
    el.disabled = true;
    el.setAttribute('aria-disabled', 'true');
    el.title = READONLY_HINT;
  }
}

/* ============================================================
   挂载(dock.js pipelineView 调用;host 为面板顶部的空容器)
   ============================================================ */

/** 在 host 内渲染切换器;root 为流水线面板根节点(只读禁用的作用域)。
    onSwitch 在选择变化后调用(dock 传 refresh:整面板重渲染,重渲染
    自然带出新选择态下的数据与禁用状态)。 */
export function mountRunSwitch(host, { root = null, onSwitch = null } = {}) {
  if (selectedSession !== S.sessionId) {   // 会话切换:选择态回落「最新」
    selected = null;
    selectedSession = S.sessionId;
  }
  if (root && activeRunId()) {
    // 立即禁用已在场的控件;异步增强(fetchState 返回后)插入的重跑按钮
    // 由 MutationObserver 补禁。root 每次重渲染都是新节点,观察器随旧树
    // 一起被 GC,不需要显式 disconnect。
    applyReadonly(root);
    new MutationObserver(() => applyReadonly(root))
      .observe(root, { childList: true, subtree: true });
  }
  (async () => {
    const runs = await fetchRuns();
    if (!runs || !runs.length) {
      // 清单不可得但已锁定历史 run:保底给「回到最新」出口,不把用户锁死
      host.replaceChildren(...(activeRunId() ? [fallbackBar(onSwitch)] : []));
      return;
    }
    host.replaceChildren(...bar(runs, onSwitch));
  })();
  return host;
}

/** 正常形态:一行紧凑下拉 + 历史态徽标;历史态追加只读提示行。 */
function bar(runs, onSwitch) {
  const latestId = runs[0].run_id;         // 服务端按 created_at 倒序,首条即最新
  const active = activeRunId();
  const sel = h('select', {
    class: 'runswitch-sel',
    'aria-label': 'run 历史切换:选择历史 run 后各数据面板只读展示该 run',
    style: { flex: '1', minWidth: '0' },
    onchange: (e) => {
      setActiveRun(e.target.value || null, latestId);
      onSwitch?.();
    },
  },
    h('option', { value: '', selected: !active || undefined },
      `最新 · ${runStamp(latestId)}(${runs[0].status || '?'}${stepsSuffix(runs[0].steps)})`),
    ...buildRunTree(runs).map((node) => h('option', {
      value: node.run.run_id,
      selected: node.run.run_id === active || undefined,
    }, optionLabel(node, latestId))));

  const row = h('div', {
    class: 'field runswitch',
    style: { display: 'flex', alignItems: 'center', gap: '7px', margin: '2px 0 8px' },
  },
    h('label', { style: { flexShrink: '0', margin: '0' } }, 'run'),
    sel,
    active ? h('span', { class: 'tag is-stale', style: { flexShrink: '0' } }, '历史 · 只读') : null);

  const note = active ? h('p', {
    class: 'blurb runswitch-note', role: 'status', style: { margin: '0 0 8px' },
  }, `${READONLY_HINT}。影像/文件/审计/报告面板已同步展示 ${runStamp(active)} 的数据。`) : null;

  return [row, note].filter(Boolean);
}

/** 退路形态:历史锁定 + 清单不可得(后端刚好不可达)时只给一个回程按钮。 */
function fallbackBar(onSwitch) {
  return h('div', {
    class: 'field runswitch',
    style: { display: 'flex', alignItems: 'center', gap: '7px', margin: '2px 0 8px' },
  },
    h('span', { class: 'tag is-stale' }, '历史 · 只读'),
    h('span', { class: 'blurb', style: { flex: '1', margin: '0' } }, 'run 清单不可达'),
    h('button', {
      class: 'btn btn-gho btn-sm', type: 'button',
      onclick: () => { setActiveRun(null); onSwitch?.(); },
    }, '回到最新'));
}
