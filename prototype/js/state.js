/* ============================================================
   单一状态源 + 依赖图失效传播
   —— 原型里唯一的「真相源」，对应 DESIGN §5 的 core 层。
   关键点：STALE 由依赖图推导，不是硬编码 if(i>=5)。
   ============================================================ */

const listeners = new Set();
let batching = 0;
const pending = new Set();

/** 订阅状态变更。topic 为 '*' 或具体域名（steps/files/stream/dock/...）。 */
export function on(topic, fn) {
  const entry = { topic, fn };
  listeners.add(entry);
  return () => listeners.delete(entry);
}

export function emit(...topics) {
  topics.forEach((t) => pending.add(t));
  if (batching > 0) return;
  flush();
}

/** 合并一批变更，只广播一次，避免连锁重渲染。 */
export function batch(fn) {
  batching++;
  try { fn(); } finally {
    batching--;
    if (batching === 0) flush();
  }
}

function flush() {
  if (!pending.size) return;
  const topics = [...pending];
  pending.clear();
  for (const { topic, fn } of listeners) {
    if (topic === '*' || topics.includes(topic)) fn(topics);
  }
}

/* ============================================================
   步骤目录：来自 GET /api/registry 的服务端能力声明
   —— 不再内置任何演示流水线。目录只描述「有哪些步骤/方法/参数」，
   不代表任何会话的执行状态；数组保持引用不变（就地填充），
   全部消费方（dock/slash/plandiff/pipelinerail/envdata…）无需换接口。
   deps 是真正的依赖边，STALE 沿它传播。
   ============================================================ */
export const STEP_DEFS = [];

/** /api/registry 的 params 是 schema 对象（{default,kind,type,…}）→ 默认值表。 */
function paramDefaults(params) {
  const out = {};
  for (const [k, p] of Object.entries(params || {})) {
    out[k] = p && typeof p === 'object' && 'default' in p ? p.default : p;
  }
  return out;
}

/**
 * 用注册表载荷就地填充步骤目录，并重建反向依赖图。
 * dur 恒为 0：注册表不声明演示工期，时长只来自本机运行历史（§7.5）。
 */
export function setRegistry(caps) {
  STEP_DEFS.length = 0;
  for (const c of caps || []) {
    if (!c || typeof c.id !== 'number') continue;
    STEP_DEFS.push({
      id: c.id,
      name: c.name || `步骤 ${c.id}`,
      deps: [...(c.deps || [])],
      dur: 0,
      methods: (c.methods || []).map((m) => ({ ...m })),
      method: c.method || '',
      params: paramDefaults(c.params),
      outputs: (c.outputs || []).map((o) => ({ ...o })),
    });
  }
  STEP_DEFS.sort((a, b) => a.id - b.id);
  rebuildRevDeps();
  emit('registry', 'steps', 'files');
}

/* ============================================================
   运行时状态
   ============================================================ */
export const S = {
  theme: localStorage.getItem('ia-theme') || 'light',
  mode: 'expert',                 // expert | guide
  phase: 'idle',                  // idle | planning | running | paused | done
  sessionId: null,                // 当前会话 id；null=尚无会话（由 /api/sessions 水合或新建）
  siderOpen: true,
  dockOpen: true,
  dockTab: 'pipeline',
  dockWidth: Number(localStorage.getItem('ia-dock-w')) || 400,
  selectedStep: null,             // 流水线面板选中步；null=未选（无计划时面板显示空态）
  selectedFile: null,
  selectedPoint: 'A',
  busy: false,                    // agent 是否正在产出
  steps: new Map(),               // id → { state, method, params, stale, fingerprint, startedAt, elapsed }
                                  //   state: pending|running|done|failed|stale|interrupted|orphaned（§7.8 四态）
                                  //   启动为空：只有服务端 /api/state 下发计划后才有条目
  plan: [],                       // agent 生成的待办
  evidenceLevel: 2,               // 六级证据阶梯当前级别（0-5），受 THRESHOLDS 与降级记录封顶
  degraded: [],                   // 本会话降级记录 { stepId, from, to, failClass, reason }（§4.12）
  autoApprove: new Set(),         // 「本会话内同类操作不再询问」的审批指纹族（absorb-F）
  diskFreeGB: null,               // 磁盘预算；null=未知（只信 budget 事件，不再预置演示值）
};

/* 磁盘预算三级闸门（§7.5 / §4.10）：UI 与后端用同一组常量，不各写一套 */
export const DISK_TIERS = { hide: 20, warn: 8 };

export function setDiskFree(gb) {
  S.diskFreeGB = gb;
  emit('budget');
}

export const LADDER = ['runnable', 'checked', 'audited', 'calibrated', 'validated', 'publishable'];

/**
 * 质量门阈值台账 —— 本地常量仅作离线回落(离线示意)。
 * 界面诚实化(0814B W6):权威台账是服务端 /api/env 的 thresholds
 * (audit/contract.py 的合同数据,经 setServerThresholds 落进来);
 * 本常量只在后端不可达时兜底,消费方(dock.js ceilingNote / env 面板
 * 演示回落)须标注「离线示意」。source 取值 upstream_default |
 * literature | local_calibration;status=PENDING 只产出 warning,
 * 不作硬 gate —— 见 AGENT-DESIGN §4.13。
 */
export const THRESHOLDS = [
  { key: 'esd_coherence_threshold', value: 0.85, source: 'upstream_default', status: 'OK',
    ref: 'ISCE2 topsApp 默认值' },
  { key: 'stepFuncDate', value: '20190706T0320', source: 'upstream_default', status: 'OK',
    ref: '实测配置 RidgecrestSenDT71.txt' },
  { key: 'corr_threshold', value: 0.85, source: 'local_calibration', status: 'PENDING',
    ref: '待标定：PS/SBAS 一致性无先例可循' },
  { key: 'min_coherence', value: 0.25, source: 'local_calibration', status: 'PENDING',
    ref: '待查 SNAPHU 文档' },
  { key: 'unwrap_coverage', value: 0.70, source: 'literature', status: 'PENDING',
    ref: '待查文献' },
];

/** 服务端阈值台账镜像(/api/env thresholds);null = 尚未取到,回落本地常量。 */
let serverThresholds = null;

/** 环境面板取到 /api/env 后落库(dock.js envView 接线);空/坏载荷不覆盖。 */
export function setServerThresholds(rows) {
  if (!Array.isArray(rows) || !rows.length) return;
  serverThresholds = rows.map((r) => ({ ...r }));
  emit('thresholds');
}

/** 当前生效的阈值台账:服务端优先,离线回落本地常量(source 供 UI 标注)。 */
export function activeThresholds() {
  return serverThresholds
    ? { rows: serverThresholds, source: 'server' }
    : { rows: THRESHOLDS, source: 'local' };
}

/**
 * 证据阶梯上限：只要还有 PENDING 阈值，就不能声称 validated；
 * 发生过降级（§4.12 降级矩阵）则进一步封顶到 checked。
 * 这条自我约束让证据边界自动生成而非手写（AGENT-DESIGN §4.13）。
 * 阈值取 activeThresholds():服务端台账优先,离线才用本地常量推导,
 * 返回值随附 source 供消费方标注「离线示意」。
 */
export function evidenceCeiling() {
  const { rows, source } = activeThresholds();
  const pending = rows.filter((t) => String(t.status).toUpperCase() === 'PENDING');
  let level = pending.length ? 2 : 4;                  // 2=audited, 4=validated
  if (S.degraded.length) level = Math.min(level, 1);   // 1=checked（降级代价，§4.12）
  return { level, pending, degraded: S.degraded, source };
}

/* ============================================================
   会话列表镜像 —— 唯一数据源是 GET /api/sessions，不再内置演示会话。
   数组保持引用不变（就地填充）：sessionops.js 的乐观更新（改名/归档/还原）
   与 app.js 的渲染都拿同一个数组。
   ============================================================ */
export const SESSIONS = [];

function sessionSub(row) {
  const mode = row.mode === 'guide' ? '向导模式' : '专家模式';
  const t = Number(row.created_at);
  if (!Number.isFinite(t) || t <= 0) return mode;
  const d = new Date(t * 1000);
  const pad = (n) => String(n).padStart(2, '0');
  return `${mode} · 建于 ${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** 会话显示名兜底（P2-16，浏览器实测）：name 缺失/空白时退回 id，两者都无信息
 *  时给「未命名会话」。字面 "null"/"undefined" 只可能来自字符串插值事故（实测
 *  曾有以 "null" 为 id 物化的会话，侧栏与副标题原样渲染 "null"），按空名处置。 */
export function sessionDisplayName(rawName, id) {
  const pick = (v) => (typeof v === 'string' && v.trim()
    && v !== 'null' && v !== 'undefined' ? v : null);
  return pick(rawName) || pick(id) || '未命名会话';
}

/** /api/sessions 行（{session_id,name,mode,created_at,archived,…}）→ 侧栏条目。 */
export function setSessions(rows) {
  SESSIONS.length = 0;
  for (const r of rows || []) {
    const id = r && (r.session_id || r.id);
    if (!id) continue;
    const entry = { id, name: sessionDisplayName(r.name, id), sub: sessionSub(r), tone: 'idle' };
    if (r.archived) entry.archived = r.archived;   // 软删除时间戳（splitArchived 依据）
    SESSIONS.push(entry);
  }
  emit('sessions');
}

/* ---------------- 指纹与初始化 ---------------- */

/** 简易稳定哈希（原型用；真实实现走 sha256，键序需先排序，见 DESIGN §6）。 */
export function fingerprint(stepId) {
  const st = S.steps.get(stepId);
  const def = def_(stepId);
  if (!st || !def) return '········';
  const upstream = def.deps.map((d) => S.steps.get(d)?.fingerprint || '').join('|');
  const payload = JSON.stringify([st.method, sortKeys(st.params), upstream, 'v1']);
  let a = 0x811c9dc5;
  for (let i = 0; i < payload.length; i++) {
    a ^= payload.charCodeAt(i);
    a = (a * 0x01000193) >>> 0;
  }
  const hex = a.toString(16).padStart(8, '0');
  return `${hex.slice(0, 4)}…${hex.slice(4)}`;
}

function sortKeys(o) {
  if (Array.isArray(o)) return o;
  return Object.keys(o).sort().reduce((acc, k) => (acc[k] = o[k], acc), {});
}

export const def_ = (id) => STEP_DEFS.find((d) => d.id === id);
export const st_ = (id) => S.steps.get(id);

/** 反向依赖：谁依赖我。用于失效传播；随 setRegistry 重建。 */
let revDeps = new Map();

function rebuildRevDeps() {
  revDeps = new Map();
  for (const d of STEP_DEFS) {
    for (const dep of d.deps) {
      if (!revDeps.has(dep)) revDeps.set(dep, []);
      revDeps.get(dep).push(d.id);
    }
  }
}

/** 沿依赖图 BFS 收集所有下游步骤（不含自身）。 */
export function downstreamOf(id) {
  const out = new Set();
  const queue = [...(revDeps.get(id) || [])];
  while (queue.length) {
    const cur = queue.shift();
    if (out.has(cur)) continue;
    out.add(cur);
    queue.push(...(revDeps.get(cur) || []));
  }
  return [...out].sort((a, b) => a - b);
}

/** 步骤镜像的拓扑序 id（步骤 id 约定单调即拓扑序，指纹重算按此序）。 */
function mirrorIds() {
  return [...S.steps.keys()].sort((a, b) => a - b);
}

/** 按拓扑序重算全部镜像指纹（下游指纹含上游指纹，必须全量）。 */
function refingerprintAll() {
  for (const id of mirrorIds()) S.steps.get(id).fingerprint = fingerprint(id);
}

/**
 * 清空步骤镜像。不再预置任何「前 N 步已完成」的演示状态 ——
 * 镜像条目只能来自服务端 /api/state（syncServerSteps 创建）。
 */
export function initSteps() {
  S.steps.clear();
  S.plan = [];
  S.phase = 'idle';
  emit('steps', 'plan');
}

/* ---------------- 变更动作 ---------------- */

/**
 * 改方法 → 重算指纹 → 沿依赖图把自身与全部下游标 STALE。
 * 返回受影响的步骤 id 数组，供 UI 生成解释文案。
 */
export function setMethod(stepId, methodId) {
  const st = st_(stepId);
  if (!st || st.method === methodId) return [];
  st.method = methodId;
  return invalidate(stepId);
}

export function setParams(stepId, patch) {
  const st = st_(stepId);
  if (!st) return [];
  const before = JSON.stringify(sortKeys(st.params));
  Object.assign(st.params, patch);
  if (JSON.stringify(sortKeys(st.params)) === before) return [];
  return invalidate(stepId);
}

/**
 * 参数指纹变更引发的失效传播。这是主 novelty 的 UI 侧体现。
 * 设计决策：running 步骤不在此标 stale——正在运行的步骤是否作废由服务端
 * 裁决（执行器接回或复位），前端镜像不抢跑，避免与服务端状态机打架。
 */
export function invalidate(stepId) {
  const affected = [stepId, ...downstreamOf(stepId)];
  batch(() => {
    for (const id of affected) {
      const st = st_(id);
      if (!st) continue;
      // 只有「曾经完成」的步骤才谈得上失效；pending 的保持 pending
      if (st.state === 'done') { st.stale = true; st.state = 'stale'; }
      else if (st.state === 'stale') st.stale = true;
    }
    // 下游指纹依赖上游指纹，需按拓扑序全量重算
    refingerprintAll();
    emit('steps', 'files');
  });
  return affected;
}

/**
 * 步骤状态机（§7.8 四态 + pending/stale）：
 *   running     蓝色脉冲，可取消
 *   interrupted 用户取消 →「已取消 · 可续跑」，从断点继续
 *   orphaned    WSL 已停止、计算未完成 → 重启环境后续跑（不是重跑）
 *   failed      计算失败 → 失败分类 + 处置按钮
 */
export function setStepState(stepId, state, extra = {}) {
  const st = st_(stepId);
  if (!st) return;
  st.state = state;
  if (state === 'done') { st.stale = false; }
  if (state === 'running') st.startedAt = Date.now();
  Object.assign(st, extra);
  emit('steps');
}

/**
 * 待办工作分三类，语义不同不能混：
 *   resume  —— failed / interrupted / orphaned，需从断点处置后续跑（§7.8）
 *   stale   —— 曾经产出过、因指纹变更而失效（需覆写旧产物）
 *   pending —— 从未运行过（首次产出）
 * 三者都需要执行，但 UI 文案与风险提示不同。
 * 归桶优先级：终态类（resume）> stale > pending。失败/中断步骤即使叠加了
 * 脏标记也归 resume——对用户的第一动作是断点处置而非覆写重跑；
 * 纯 stale（done+脏）才提示覆写旧产物的风险（§7.8 文案按桶区分）。
 */
export function workSummary() {
  const stale = [], pending = [], resume = [];
  for (const id of mirrorIds()) {
    const st = S.steps.get(id);
    if (st.state === 'failed' || st.state === 'interrupted' || st.state === 'orphaned') resume.push(id);
    else if (st.stale || st.state === 'stale') stale.push(id);
    else if (st.state === 'pending') pending.push(id);
  }
  return { stale, pending, resume, all: [...stale, ...pending, ...resume].sort((a, b) => a - b) };
}

/** 需要重跑的步骤 id（stale + pending + resume，按拓扑序）。 */
export function staleSteps() {
  return workSummary().all;
}

/** 示意工期已随演示目录删除（dur 恒 0）：本函数恒返回 0，仅为消费方接口兼容保留。
 *  时长预估的唯一合法来源是 estimateRerunHonest 的本机运行历史（§7.5）。 */
export function estimateRerun(ids) {
  return ids.reduce((sum, id) => sum + (def_(id)?.dur || 0), 0);
}

/* ---------------- 诚实时长预估（§7.5：没有依据就不给数） ---------------- */

/**
 * 本机运行历史。键 = `${stepId}:${fingerprint}`（同配置才算同历史），
 * 值 = 该配置历次运行秒数。启动为空 —— 没跑过就是「时长未知」，不编数。
 * 样本随会话内完成的运行累积。
 */
const RUN_HISTORY = new Map();

const histKey = (id) => `${id}:${st_(id)?.fingerprint || ''}`;

/**
 * 步骤成功完成后记一笔历史。秒数取真实耗时：显式传入 secs，
 * 或由镜像的 startedAt 推算；两者都没有（无起点）则不记 —— 绝不用假样本。
 */
export function recordRunSample(stepId, secs = null) {
  const st = st_(stepId);
  if (!st) return;
  let v = secs;
  if (v == null) {
    if (!st.startedAt) return;
    v = Math.round((Date.now() - st.startedAt) / 1000);
  }
  if (!Number.isFinite(v) || v <= 0) return;
  const key = histKey(stepId);
  if (!RUN_HISTORY.has(key)) RUN_HISTORY.set(key, []);
  RUN_HISTORY.get(key).push(v);
}

/**
 * 两种形态（AGENT-DESIGN §7.5）：
 *   历史样本 ≥3 → { known:true,  label:'约 X–Y 分钟（基于本机历史 N 次运行）' }
 *   历史样本 <3 → { known:false, label:'时长未知（首次运行此配置）' }
 */
export function estimateRerunHonest(ids) {
  if (!ids?.length) return { known: false, samples: 0, label: '无待运行步骤' };
  const hist = ids.map((id) => RUN_HISTORY.get(histKey(id)) || []);
  const samples = Math.min(...hist.map((a) => a.length));
  if (samples < 3) return { known: false, samples, label: '时长未知（首次运行此配置）' };
  const lo = hist.reduce((s, a) => s + Math.min(...a), 0);
  const hi = hist.reduce((s, a) => s + Math.max(...a), 0);
  const label = hi >= 5400
    ? `约 ${fmtH(lo)}–${fmtH(hi)} 小时（基于本机历史 ${samples} 次运行）`
    : `约 ${Math.max(1, Math.round(lo / 60))}–${Math.max(1, Math.round(hi / 60))} 分钟（基于本机历史 ${samples} 次运行）`;
  return { known: true, samples, loSec: lo, hiSec: hi, label };
}

function fmtH(sec) {
  const hours = sec / 3600;
  return String(hours >= 10 ? Math.round(hours) : Math.round(hours * 10) / 10);
}

/* ---------------- 撤销窗口的状态快照（§7.6） ---------------- */

/** 全量快照：方法 / 参数 / 状态 / STALE 标记。撤销时整体回滚。 */
export function snapshotSteps() {
  return [...S.steps.values()].map((st) => ({
    id: st.id, state: st.state, method: st.method,
    params: { ...st.params }, stale: st.stale,
  }));
}

export function restoreSteps(snap) {
  batch(() => {
    for (const s of snap) {
      const st = st_(s.id);
      if (!st) continue;
      st.state = s.state;
      st.method = s.method;
      st.params = { ...s.params };
      st.stale = s.stale;
    }
    refingerprintAll();
    emit('steps', 'files');
  });
}

/* ---------------- 服务端状态镜像（GET /api/state） ---------------- */

/**
 * 把服务端 run 的步骤状态同步进本地镜像。服务端是镜像条目的唯一创建者：
 *   - 本地没有的步骤 id 就地创建（S.steps 启动为空，计划来自 /api/state）；
 *   - 服务端没给的字段不动——method / state / params / stale 一律按字段存在与否
 *     判断，防部分载荷（如只回 id/state/method 的端点）把本地脏标记静默抹掉；
 *   - params 若下发则整体替换（/api/state 每步都带权威 params，
 *     镜像后指纹与服务端配置对齐，防前后端指纹静默分叉）；
 *   - 'skipped'（云端 HyP3 完成）本地视作 done 展示。
 * 镜像后收敛 state 与 stale 布尔，不留分裂态（与服务端 store.set_stale 的
 * 联动方向一致：'stale' 态是「done+脏」的派生展示）：
 *   - stale=true 且 state='done'      → state='stale'（曾产出但已失效）；
 *   - state='stale' 而 stale=false    → 服务端明确给了 stale:false 则布尔权威，
 *     回 'done'；只给了 state:'stale' 则布尔联动为 true。
 * 同步后按拓扑序重算全部指纹并广播。
 */
export function syncServerSteps(serverSteps) {
  if (!Array.isArray(serverSteps) || !serverSteps.length) return;
  const MAP = { skipped: 'done' };
  batch(() => {
    for (const s of serverSteps) {
      if (!s || s.id == null) continue;
      let st = st_(s.id);
      if (!st) {
        st = {
          id: s.id,
          state: 'pending',
          method: def_(s.id)?.method || '',
          params: {},
          stale: false,
          elapsed: 0,
          startedAt: null,
          fingerprint: '',
          logs: [],
        };
        S.steps.set(s.id, st);
      }
      if (s.method) st.method = s.method;
      if (s.params && typeof s.params === 'object' && !Array.isArray(s.params)) {
        st.params = { ...s.params };
      }
      if (s.state) st.state = MAP[s.state] || s.state;
      const hasStale = Object.prototype.hasOwnProperty.call(s, 'stale');
      if (hasStale) st.stale = !!s.stale;
      if (st.stale && st.state === 'done') st.state = 'stale';
      else if (!st.stale && st.state === 'stale') {
        if (hasStale) st.state = 'done';
        else st.stale = true;
      }
    }
    refingerprintAll();
    emit('steps', 'files');
  });
}

/* ---------------- 产物视图（从步骤输出派生） ---------------- */

/** 文件列表不再是独立常量，而是从 STEP_DEFS.outputs 派生 —— 状态自动一致。 */
export function fileTree() {
  const out = [];
  for (const d of STEP_DEFS) {
    const st = st_(d.id);
    for (const o of d.outputs || []) {
      out.push({
        path: o.path,
        kind: o.kind,
        isDir: !o.path.includes('.'),
        step: d.id,
        stale: st?.stale || st?.state === 'stale',
        exists: st?.state === 'done' || st?.stale || st?.state === 'stale',
        hash: st?.fingerprint || '',
        method: st?.method,
      });
    }
  }
  return out;
}

export function setTheme(t) {
  S.theme = t;
  localStorage.setItem('ia-theme', t);
  document.documentElement.dataset.theme = t;
  emit('theme');
}

export function setDockWidth(w) {
  S.dockWidth = Math.max(300, Math.min(620, w));
  localStorage.setItem('ia-dock-w', String(S.dockWidth));
  document.documentElement.style.setProperty('--dock', `${S.dockWidth}px`);
}

/* ---------------- 领域参数校验（Schema 层的 UI 侧镜像） ---------------- */
export const PARAM_SCHEMA = {
  min_coherence: { type: 'number', min: 0, max: 1, hint: '相干性阈值须在 0–1 之间' },
  threads: { type: 'int', min: 1, max: 32, hint: '线程数 1–32（本机 24 核，留 4 核给系统）' },
  alpha: { type: 'number', min: 0, max: 1, hint: 'Goldstein alpha 须在 0–1 之间' },
  corr_threshold: { type: 'number', min: 0, max: 1, hint: '交叉验证相关阈值须在 0–1 之间' },
  max_temporal_baseline: { type: 'int', min: 6, max: 730, hint: '时间基线 6–730 天' },
  range_looks: { type: 'int', min: 1, max: 40, hint: '距离向多视 1–40' },
  azimuth_looks: { type: 'int', min: 1, max: 40, hint: '方位向多视 1–40' },
  dpi: { type: 'int', min: 72, max: 1200, hint: '出图 DPI 72–1200' },
  esd_coherence_threshold: { type: 'number', min: 0, max: 1, hint: 'ESD 相干阈值 0–1' },
};

/**
 * 返回 null 表示通过，否则返回错误提示。
 * 空值（''/纯空白/null/undefined）显式拒绝：Number('') 与 Number(null) 都
 * 静默变 0，会让空输入冒充合法值 0 通过（对 min=0 的参数尤其危险）。
 * 设计决策：schema 之外的键放行——PARAM_SCHEMA 只是数值参数的 UI 侧镜像，
 * 完整校验在服务端 cap.validate_params（未声明参数会被 400 拒绝），
 * UI 不重复维护全量清单，避免误拦服务端合法的非数值参数。
 */
export function validateParam(key, raw) {
  const sc = PARAM_SCHEMA[key];
  if (!sc) return null;
  if (raw == null || String(raw).trim() === '') return `${key} 不能为空`;
  const n = Number(raw);
  if (!Number.isFinite(n)) return `${key} 必须是数字`;
  if (sc.type === 'int' && !Number.isInteger(n)) return `${key} 必须是整数`;
  if (n < sc.min || n > sc.max) return sc.hint;
  return null;
}
