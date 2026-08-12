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
   流水线定义：11 步 × 依赖关系 × 候选方法
   deps 是真正的依赖边，STALE 沿它传播。
   ============================================================ */
export const STEP_DEFS = [
  {
    id: 1, name: '数据获取', deps: [], dur: 360,
    methods: [
      { id: 'asf_search_slc', label: 'asf_search_slc', engine: 'ASF API', why: '下载原始 SLC，可控性最强', ok: true },
      { id: 'hyp3_submit', label: 'hyp3_submit', engine: '云端', why: '跳过 2–6 步，但失去中间产物控制权', ok: true, cost: '需 ASF 配额' },
      { id: 'local_import', label: 'local_import', engine: '—', why: '已有本地数据', ok: true },
    ],
    method: 'asf_search_slc',
    params: { scenes: 7, platform: 'sentinel-1', dates: '2019-06-10..2019-08-15' },
    outputs: [{ path: 'data/slc', kind: 'SLC', layout: 'isce2' }],
  },
  {
    id: 2, name: '辅助数据', deps: [1], dur: 120,
    methods: [
      { id: 'dem_copernicus', label: 'dem_copernicus', engine: 'AWS', why: 'Copernicus 30 m，覆盖全球且质量稳定', ok: true },
      { id: 'dem_srtm', label: 'dem_srtm', engine: 'NASA', why: 'SRTM 30 m，高纬度覆盖缺口', ok: true },
      { id: 'dem_local', label: 'dem_local', engine: '—', why: '使用本地 DEM 瓦片', ok: true },
    ],
    method: 'dem_copernicus',
    params: { dem: 'copernicus-30m', orbit: 'poeorb' },
    outputs: [{ path: 'data/dem', kind: 'DEM', layout: 'isce2' }],
  },
  {
    id: 3, name: '配准', deps: [1, 2], dur: 1080,
    methods: [
      { id: 'isce2_tops_geom_esd', label: 'isce2_tops_geom_esd', engine: 'ISCE2', why: 'S1 IW 标准路径：几何配准 + ESD 精化', ok: true },
      { id: 'isce2_stripmap_xcorr', label: 'isce2_stripmap_xcorr', engine: 'ISCE2', why: '条带模式（ALOS-2/TSX），当前数据非条带', ok: false, blocked: '数据为 S1 IW' },
      { id: 'snap_backgeocoding', label: 'snap_backgeocoding', engine: 'SNAP', why: '走 SNAP 链，需换 layout', ok: true },
    ],
    method: 'isce2_tops_geom_esd',
    params: { esd_coherence_threshold: 0.85 },
    outputs: [{ path: 'data/coreg', kind: 'RSLC', layout: 'isce2' }],
  },
  {
    id: 4, name: '干涉', deps: [3], dur: 3120,
    methods: [
      { id: 'isce2_ifg_multilook', label: 'isce2_ifg_multilook', engine: 'ISCE2', why: '可调多视比。小基线网络按时空基线剪枝，非全组合', ok: true },
      { id: 'snap_interferogram', label: 'snap_interferogram', engine: 'SNAP', why: 'SNAP 链对应步骤', ok: true },
    ],
    method: 'isce2_ifg_multilook',
    params: { range_looks: 10, azimuth_looks: 2, pairs: 11 },
    outputs: [{ path: 'data/ifg', kind: 'IFG_WRAPPED', layout: 'isce2' }],
  },
  {
    id: 5, name: '滤波', deps: [4], dur: 1860,
    methods: [
      { id: 'goldstein', label: 'goldstein', engine: 'ISCE2', why: '低相干区推荐，本组干涉对 γ 均值仅 0.62', ok: true },
      { id: 'boxcar', label: 'boxcar', engine: 'ISCE2', why: '简单快速，但边缘模糊', ok: true },
      { id: 'none', label: 'none', engine: '—', why: '不滤波，保留全部细节', ok: true },
    ],
    method: 'goldstein',
    params: { alpha: 0.4, filter_strength: 0.5 },
    outputs: [{ path: 'data/ifg_filt', kind: 'IFG_WRAPPED', layout: 'isce2' }],
  },
  {
    id: 6, name: '解缠', deps: [5], dur: 1440, decision: true,
    methods: [
      { id: 'snaphu_mcf', label: 'snaphu_mcf', engine: 'SNAPHU', why: 'Minimum Cost Flow。大梯度形变区稳健，MintPy 原生兼容，输出可直接进入 SBAS 反演。', ok: true, recommend: true, extra: '~24 min · 8 GB' },
      { id: 'snaphu_smooth', label: 'snaphu_smooth', engine: 'SNAPHU', why: '精度更高但需人工调 cost function，同震大梯度区难以全自动。', ok: true, extra: '~40 min · 需交互配置' },
      { id: 'icu', label: 'icu', engine: 'ISCE2', why: '区域增长法，大范围低相干区容易产生解缠孤岛。', ok: true, extra: '~18 min' },
      { id: '3D_FULL', label: '3D_FULL', engine: 'MintPy', why: '本机无 3D 相位解缠工具链，且输出格式与下游时序反演不兼容。', ok: false, blocked: '工具链缺失' },
    ],
    method: '3D_FULL',
    params: { min_coherence: 0.25, threads: 8 },
    outputs: [{ path: 'data/unw', kind: 'IFG_UNWRAPPED', layout: 'isce2' }, { path: 'params/unwrap.yaml', kind: 'CONFIG' }],
  },
  {
    id: 7, name: '时序反演', deps: [6], dur: 2280,
    methods: [
      { id: 'mintpy_sbas', label: 'mintpy_sbas', engine: 'MintPy', why: '小基线集，面状形变像元覆盖广，HyP3 干涉对可直接入网', ok: true, recommend: true },
      { id: 'pystamps_ps', label: 'pystamps_ps', engine: 'PyStamps', why: '永久散射体，适合高相干点状目标（建筑/公路）', ok: true },
    ],
    method: 'mintpy_sbas',
    params: { network: 'small_baseline', max_temporal_baseline: 120 },
    outputs: [{ path: 'products/timeseries.h5', kind: 'TIMESERIES', layout: 'mintpy_h5' }],
  },
  {
    id: 8, name: '误差校正', deps: [7], dur: 540,
    methods: [
      { id: 'tropo_era5_pyaps', label: 'tropo_era5_pyaps', engine: 'PyAPS', why: 'ERA5 已下载到本地，std 可降 32%', ok: true, recommend: true },
      { id: 'tropo_gacos', label: 'tropo_gacos', engine: 'GACOS', why: '需在线申请，当前无凭据', ok: false, blocked: '缺 GACOS 凭据' },
      { id: 'tropo_height_corr', label: 'tropo_height_corr', engine: '经验', why: '无气象数据时的降级方案', ok: true },
    ],
    method: 'tropo_era5_pyaps',
    params: { ramp: 'linear', dem_error: true, solid_earth_tides: true },
    outputs: [{ path: 'products/timeseries_corrected.h5', kind: 'TIMESERIES', layout: 'mintpy_h5' }],
  },
  {
    id: 9, name: '形变模型', deps: [8], dur: 420,
    methods: [
      { id: 'step', label: 'step(20190706)', engine: 'MintPy', why: '同震阶跃模型，参考日期取自实测配置（2019-07-06 Mw 7.1 主震）', ok: true, recommend: true },
      { id: 'linear', label: 'linear', engine: 'MintPy', why: '仅线性趋势，会把同震阶跃当趋势吸收', ok: true },
      { id: 'poly_periodic', label: 'poly_periodic(1,[1,0.5])', engine: 'MintPy', why: '线性 + 年周期 + 半年周期，适合冻融等季节形变；同震场景无季节机理', ok: true },
      { id: 'exponential', label: 'exponential', engine: 'MintPy', why: '震后弛豫衰减，观测窗口仅 2 个月暂不适用', ok: true },
    ],
    method: 'step',
    params: { step_date: '20190706' },
    outputs: [{ path: 'products/velocity.h5', kind: 'VELOCITY', layout: 'mintpy_h5' }],
  },
  {
    id: 10, name: '出图导出', deps: [9], dur: 240,
    methods: [
      { id: 'figure_journal', label: 'figure_journal', engine: '自建', why: '期刊级排版：600 dpi、色盲安全色带、比例尺', ok: true, recommend: true },
      { id: 'mintpy_geocode', label: 'mintpy_geocode', engine: 'MintPy', why: '仅地理编码，不做排版', ok: true },
      { id: 'gdal_warp', label: 'gdal_warp', engine: 'GDAL', why: '导出 GeoTIFF 供 GIS 使用', ok: true },
    ],
    method: 'figure_journal',
    params: { dpi: 600, cmap: 'roma', format: 'png+pdf' },
    outputs: [
      { path: 'products/velocities/vel_ridgecrest_2019.png', kind: 'FIGURE' },
      { path: 'products/timeseries/ts_ridgecrest.png', kind: 'FIGURE' },
    ],
  },
  {
    id: 11, name: '质检', deps: [10, 7], dur: 660,
    methods: [
      { id: 'crossval_ps_sbas', label: 'crossval_ps_sbas', engine: '自建', why: 'PS/SBAS 双链交叉验证——本项目独有质量门', ok: true, recommend: true },
      { id: 'loop_closure', label: 'loop_closure', engine: 'MintPy', why: '闭合回路残差检查，只验解缠不验反演', ok: true },
      { id: 'coherence_mask', label: 'coherence_mask', engine: 'MintPy', why: '相干性掩膜，最弱的质检', ok: true },
    ],
    method: 'crossval_ps_sbas',
    params: { corr_threshold: 0.85 },
    outputs: [
      { path: 'products/report/methods_draft.md', kind: 'REPORT' },
      { path: 'provenance.json', kind: 'PROVENANCE' },
    ],
  },
];

/* ============================================================
   运行时状态
   ============================================================ */
export const S = {
  theme: localStorage.getItem('ia-theme') || 'light',
  mode: 'expert',                 // expert | guide
  phase: 'idle',                  // idle | planning | running | paused | done
  sessionId: 'ridgecrest-2019',
  siderOpen: true,
  dockOpen: true,
  dockTab: 'pipeline',
  dockWidth: Number(localStorage.getItem('ia-dock-w')) || 400,
  selectedStep: 6,
  selectedFile: null,
  selectedPoint: 'A',
  webSite: 'asf',
  busy: false,                    // agent 是否正在产出
  steps: new Map(),               // id → { state, method, params, stale, fingerprint, startedAt, elapsed }
                                  //   state: pending|running|done|failed|stale|interrupted|orphaned（§7.8 四态）
  plan: [],                       // agent 生成的待办
  evidenceLevel: 2,               // 六级证据阶梯当前级别（0-5），受 THRESHOLDS 与降级记录封顶
  degraded: [],                   // 本会话降级记录 { stepId, from, to, failClass, reason }（§4.12）
  autoApprove: new Set(),         // 「本会话内同类操作不再询问」的审批指纹族（absorb-F）
  diskFreeGB: 16,                 // 磁盘预算（演示初值 16G：落在 8–20G 灰字提示区，§7.5）
};

/* 磁盘预算三级闸门（§7.5 / §4.10）：UI 与后端用同一组常量，不各写一套 */
export const DISK_TIERS = { hide: 20, warn: 8 };

export function setDiskFree(gb) {
  S.diskFreeGB = gb;
  emit('budget');
}

export const LADDER = ['runnable', 'checked', 'audited', 'calibrated', 'validated', 'publishable'];

/**
 * 质量门阈值台账。source 取值 upstream_default | literature | local_calibration。
 * status=PENDING 的阈值只产出 warning，不作硬 gate —— 见 AGENT-DESIGN §4.13。
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

/**
 * 证据阶梯上限：只要还有 PENDING 阈值，就不能声称 validated；
 * 发生过降级（§4.12 降级矩阵）则进一步封顶到 checked。
 * 这条自我约束让证据边界自动生成而非手写（AGENT-DESIGN §4.13）。
 */
export function evidenceCeiling() {
  const pending = THRESHOLDS.filter((t) => t.status === 'PENDING');
  let level = pending.length ? 2 : 4;                  // 2=audited, 4=validated
  if (S.degraded.length) level = Math.min(level, 1);   // 1=checked（降级代价，§4.12）
  return { level, pending, degraded: S.degraded };
}

export const SESSIONS = [
  { id: 'ridgecrest-2019', name: 'Ridgecrest 同震形变', sub: 'HyP3 + MintPy · 11 对 · 真实数据', tone: 'run', real: true },
  { id: 'yushu-permafrost', name: '玉树冻土 · SBAS 时序', sub: '数据待获取 · 场景 B', tone: 'idle' },
  { id: 'yarlung-ps', name: '雅鲁藏布江滑坡', sub: 'PyStamps PS 链 · 数据待获取', tone: 'idle' },
];

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

/** 反向依赖：谁依赖我。用于失效传播。 */
const revDeps = new Map();
for (const d of STEP_DEFS) {
  for (const dep of d.deps) {
    if (!revDeps.has(dep)) revDeps.set(dep, []);
    revDeps.get(dep).push(d.id);
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

/** 初始化：前 n 步标记完成，第 n+1 步待决策。 */
export function initSteps(doneThrough = 5) {
  S.steps.clear();
  for (const d of STEP_DEFS) {
    S.steps.set(d.id, {
      id: d.id,
      state: d.id <= doneThrough ? 'done' : 'pending',
      method: d.method,
      params: { ...d.params },
      stale: false,
      elapsed: d.id <= doneThrough ? d.dur : 0,
      startedAt: null,
      fingerprint: '',
      logs: [],
    });
  }
  for (const d of STEP_DEFS) S.steps.get(d.id).fingerprint = fingerprint(d.id);
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
      st.fingerprint = fingerprint(id);
    }
    // 下游指纹依赖上游指纹，需按拓扑序全量重算
    for (const d of STEP_DEFS) S.steps.get(d.id).fingerprint = fingerprint(d.id);
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
  for (const d of STEP_DEFS) {
    const st = st_(d.id);
    if (!st) continue;
    if (st.state === 'failed' || st.state === 'interrupted' || st.state === 'orphaned') resume.push(d.id);
    else if (st.stale || st.state === 'stale') stale.push(d.id);
    else if (st.state === 'pending') pending.push(d.id);
  }
  return { stale, pending, resume, all: [...stale, ...pending, ...resume].sort((a, b) => a - b) };
}

/** 需要重跑的步骤 id（stale + pending + resume，按拓扑序）。 */
export function staleSteps() {
  return workSummary().all;
}

export function estimateRerun(ids) {
  return ids.reduce((sum, id) => sum + (def_(id)?.dur || 0), 0);
}

/* ---------------- 诚实时长预估（§7.5：没有依据就不给数） ---------------- */

/**
 * 本机运行历史。键 = `${stepId}:${fingerprint}`（同配置才算同历史），
 * 值 = 该配置历次运行秒数。演示预置为空 —— Ridgecrest 本机从未真实跑过，
 * 诚实答案是「时长未知」而非编一个 93 分钟。样本随会话内完成的运行累积。
 */
const RUN_HISTORY = new Map();

const histKey = (id) => `${id}:${st_(id)?.fingerprint || ''}`;

/** 步骤成功完成后记一笔历史（mock：以示意工期 ±20% 抖动模拟真实波动）。 */
export function recordRunSample(stepId) {
  const def = def_(stepId);
  if (!def) return;
  const key = histKey(stepId);
  if (!RUN_HISTORY.has(key)) RUN_HISTORY.set(key, []);
  RUN_HISTORY.get(key).push(Math.round(def.dur * (0.85 + Math.random() * 0.4)));
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
    for (const d of STEP_DEFS) S.steps.get(d.id).fingerprint = fingerprint(d.id);
    emit('steps', 'files');
  });
}

/* ---------------- 服务端状态镜像（GET /api/state） ---------------- */

/**
 * 把服务端 run 的步骤状态同步进本地镜像。镜像纪律：
 *   - 本地没有的步骤 id 跳过；
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
      const st = st_(s.id);
      if (!st) continue;
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
    for (const d of STEP_DEFS) S.steps.get(d.id).fingerprint = fingerprint(d.id);
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
