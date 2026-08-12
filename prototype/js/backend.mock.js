/* ============================================================
   模拟后端 —— 唯一需要替换成真实 SSE 的一层
   对外只暴露 AsyncIterable<Event>，事件形状与 DESIGN §5 的
   events.py 契约对齐：给 LLM 的 data 与给前端的 ui_update 分离。
   替换方式：把 runTurn / runPipeline 换成 EventSource 读取即可，
   上层 app.js 完全不用改。
   ============================================================ */
import { S, STEP_DEFS, def_, st_, workSummary, estimateRerun } from './state.js';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** 协作式取消令牌 —— 对应 DESIGN §10.1 的 threading.Event 透传。 */
export class Cancel {
  constructor() { this.flag = false; this.waiters = new Set(); }
  cancel() { this.flag = true; this.waiters.forEach((w) => w()); }
  get cancelled() { return this.flag; }
  throwIfCancelled() { if (this.flag) throw new CancelledError(); }
}
export class CancelledError extends Error {
  constructor() { super('cancelled'); this.name = 'CancelledError'; }
}

/** 可被取消打断的等待。 */
async function wait(ms, token) {
  if (!token) return sleep(ms);
  token.throwIfCancelled();
  await new Promise((resolve) => {
    const t = setTimeout(resolve, ms);
    const w = () => { clearTimeout(t); resolve(); };
    token.waiters.add(w);
    setTimeout(() => token.waiters.delete(w), ms + 50);
  });
  token.throwIfCancelled();
}

/* ============================================================
   意图识别（真实实现由 brain/intent.py 出结构化意图）
   ============================================================ */
const SCENARIOS = {
  // 只有 quake 场景有真实数据（11 个 HyP3 干涉对），另两个 dataReady=false。
  quake: {
    match: /地震|同震|quake|Ridgecrest|玛多/,
    region: 'Ridgecrest（加州）', dates: '2019-06-10 — 2019-08-15',
    scenes: '7 个获取日期 / 11 个干涉对', dataReady: true,
    model: 'step', chain: 'SBAS',
    diag: '同震形变量级大（数十 cm），HyP3 已提供解缠相位与相干性',
    pick: 'mintpy_sbas',
    reason: '阶跃形变 → step(20190706) 模型；日期取自实测配置',
  },
  permafrost: {
    match: /冻土|permafrost|玉树|青海|青藏/,
    region: '青海玉树', dates: '2020-01 — 2023-12',
    scenes: '数据待获取', dataReady: false,
    model: 'poly_periodic', chain: 'SBAS',
    diag: '冻土区植被与季节冻融导致时间去相干严重，点状 PS 目标稀疏',
    pick: 'mintpy_sbas',
    reason: '低相干面状形变 → SBAS 优于 PS；形变含季节冻融 → poly_periodic 模型',
  },
  landslide: {
    match: /滑坡|landslide|雅鲁藏布/,
    region: '雅鲁藏布江', dates: '待定',
    scenes: '数据待获取', dataReady: false,
    model: 'linear', chain: 'PS',
    diag: '陡坡地形几何畸变明显，裸岩区高相干点密集',
    pick: 'pystamps_ps',
    reason: '高相干点状目标 → PS 链；需 ISCE2→PyStamps 桥',
  },
};

export function classify(text) {
  for (const [k, v] of Object.entries(SCENARIOS)) if (v.match.test(text)) return { key: k, ...v };
  return { key: 'quake', ...SCENARIOS.quake };   // 默认走有真实数据的场景
}

/* ============================================================
   规划回合：用户输入 → 事件流
   ============================================================ */
export async function* runTurn(text, token) {
  const sc = classify(text);

  yield { t: 'thinking', title: '解析意图与约束', body:
    `区域：${sc.region}\n目标：${sc.chain} 时序形变\n时间范围：${sc.dates}\n数据：${sc.scenes}\n` +
    `模式：${S.mode === 'expert' ? '专家（每步人工确认方法）' : '向导（Agent 自动决策，关键节点征询）'}` };
  await wait(520, token);

  // ---- 环境探测 ----
  yield { t: 'tool.start', id: 'probe', verb: 'probe', cmd: 'runtime/probe.py --engines --wsl',
          label: '探测可用引擎' };
  const probeLines = [
    ['$ probe.py --engines --wsl', 'cmd'],
    ['WSL2 Ubuntu 24.04 · 20 核 / 40 GB · E: 483 GB 空闲', 'dim'],
    ['isce2      2.6.5   ✓  conda-forge（无 CUDA 模块）', 'ok'],
    ['mintpy     1.6.4   ✓', 'ok'],
    ['snaphu     2.0.7   ✓', 'ok'],
    ['pystamps   0.3.4   ✓', 'ok'],
    ['pyaps3     0.3.6   ✓  ERA5 缓存命中 3/7 天（其余需 CDS 在线补拉）', 'ok'],
    ['gacos      —       ✗  缺凭据 → tropo_gacos 从候选集移除', 'warn'],
    ['3D 解缠链  —       ✗  → 3D_FULL 从候选集移除', 'warn'],
  ];
  for (const [l, tone] of probeLines) { yield { t: 'tool.log', id: 'probe', line: l, tone }; await wait(120, token); }
  yield { t: 'tool.end', id: 'probe', exit: 0, summary: '5 个引擎可用 · 2 个候选被规则引擎排除' };
  await wait(300, token);

  // ---- 数据诊断 ----
  yield { t: 'tool.start', id: 'diag', verb: 'inspect', cmd: `core/store.py --scan --session ${S.sessionId}`,
          label: '扫描已有产物与指纹' };
  for (const [l, tone] of [
    [`$ store.py --scan --session ${S.sessionId}`, 'cmd'],
    ['hyp3/            11 对 · 110 文件 · 402 MB · 指纹匹配 ✓ 跳过', 'ok'],
    ['hyp3/*/dem       HyP3 内置 DEM · 指纹匹配 ✓ 跳过', 'ok'],
    ['（HyP3 已完成配准与干涉 → 第 3–4 步不适用）', 'dim'],
    ['ERA5.h5          45.7 MB 缓存 3/7 天 · 缺 4 天需 CDS 在线补拉', 'warn'],
    ['data/ifg_filt    goldstein α=0.4 · 指纹匹配 ✓ 跳过', 'ok'],
    ['data/unw         缺失 —— 当前配置 unwrap_method=3D_FULL 不可执行', 'err'],
    [`诊断：${sc.diag}`, 'dim'],
  ]) { yield { t: 'tool.log', id: 'diag', line: l, tone }; await wait(150, token); }
  yield { t: 'tool.end', id: 'diag', exit: 0, summary: 'HyP3 已完成 1–5 步 · 从第 6 步继续' };
  await wait(320, token);

  // ---- 生成计划 ----
  yield { t: 'plan', items: [
    { n: 1, text: '探测引擎与环境，收窄候选集', st: 'd' },
    { n: 2, text: '扫描已有产物，按指纹判定可跳过步骤', st: 'd' },
    { n: 3, text: '第 6 步解缠方法决策（当前配置不可用）', st: 'r' },
    { n: 4, text: '执行第 6–11 步流水线', st: 'p' },
    { n: 5, text: '双链交叉验证质量门', st: 'p' },
    { n: 6, text: '生成图表、provenance 与方法章节草稿', st: 'p' },
  ] };
  await wait(380, token);

  yield { t: 'say', parts: [
    `已按指纹跳过前 5 步。第 `, { b: '6 步 · 解缠' }, ` 需要决策：当前配置 `,
    { code: '3D_FULL' }, ` 在本机不可执行（无 3D 解缠工具链，且输出格式与下游 `,
    { code: 'mintpy_sbas' }, ` 不兼容）。规则引擎已把 4 个候选收窄到 3 个可行项，Agent 建议 `,
    { b: 'snaphu_mcf' }, `：${sc.reason}。`,
  ] };
  await wait(200, token);

  yield { t: 'candidates', stepId: 6 };
}

/* ============================================================
   流水线执行：逐步产出 tool_call 事件
   ============================================================ */
const SCRIPTS = {
  6: (st) => [
    [`$ snaphu.py --method ${st.method} --min-coherence ${st.params.min_coherence} --threads ${st.params.threads}`, 'cmd'],
    ['读取干涉对 11 pairs（γ > ' + st.params.min_coherence + '）', 'dim'],
    ['构建 Delaunay 网络 · 代价函数 SMOOTH', 'dim'],
    ['MCF 最小费用流求解 … 32%', 'dim'],
    ['MCF 最小费用流求解 … 78%', 'dim'],
    ['94.2% 像元已解缠 · 残差点 1,204 · 孤岛 3 处（已掩膜）', 'warn'],
    ['✓ data/unw/*.unw · 74 MB', 'ok'],
  ],
  7: (st) => [
    [`$ smallbaselineApp.py --dostep invert_network --method ${st.method}`, 'cmd'],
    ['7 个日期 × 11 对 · 闭合回路检查通过', 'dim'],
    [`网络：${st.params.network} · 最大时间基线 ${st.params.max_temporal_baseline} d`, 'dim'],
    ['SBAS 反演 1,689,305 像元 · 加权最小二乘 · 4 次迭代收敛', 'dim'],
    ['✓ products/timeseries.h5 · 72 MB', 'ok'],
  ],
  8: (st) => st.method === 'tropo_height_corr' ? [
    ['$ smallbaselineApp.py --dostep correct_troposphere --method tropo_height_corr', 'cmd'],
    ['降级路径：高程相关对流层校正，不依赖外部气象服务（见上方降级通知）', 'dim'],
    ['固体潮校正（PySolid）', 'dim'],
    [`DEM 误差估计 · ${st.params.ramp} ramp 移除`, 'dim'],
    ['时序标准差 6.8 mm → 5.4 mm（降低 21%，弱于 ERA5 路径的 32%）', 'warn'],
    ['✓ products/timeseries_corrected.h5 · 证据级别 checked（降级代价已入 provenance）', 'ok'],
  ] : [
    [`$ smallbaselineApp.py --dostep correct_troposphere --method ${st.method}`, 'cmd'],
    ['ERA5 对流层延迟（PyAPS3）', 'dim'],
    ['固体潮校正（PySolid）', 'dim'],
    [`DEM 误差估计 · ${st.params.ramp} ramp 移除`, 'dim'],
    ['时序标准差 6.8 mm → 4.6 mm（降低 32%）', 'ok'],
    ['✓ products/timeseries_corrected.h5', 'ok'],
  ],
  9: (st) => [
    [`$ timeseries2velocity.py --model ${st.method}`, 'cmd'],
    [`拟合模型 ${st.method} · 参考日期 2019-07-06（Mw 7.1 主震）`, 'dim'],
    ['R² = 0.96 · 同震阶跃分量主导，震后早期趋势微弱', 'dim'],
    ['断层西侧: −182.0 ± 12.4 mm（同震 LOS 位移）', 'ok'],
    ['断层东侧: +96.5 ± 8.7 mm（同震 LOS 位移）', 'ok'],
    ['✓ products/velocity.h5', 'ok'],
  ],
  10: (st) => [
    [`$ figure_journal.py --dpi ${st.params.dpi} --cmap ${st.params.cmap}`, 'cmd'],
    ['地理编码 → WGS84 · 30 m 网格', 'dim'],
    ['出图：速率图 / 时序图 / 时空剖面 · 色盲安全色带 roma', 'dim'],
    ['✓ vel_ridgecrest_2019.png · ts_ridgecrest.png（600 dpi, png+pdf）', 'ok'],
  ],
  11: (st) => [
    [`$ crossval_ps_sbas.py --corr-threshold ${st.params.corr_threshold}`, 'cmd'],
    ['PS 链（PyStamps）: 214,880 点', 'dim'],
    ['SBAS 链（MintPy）: 1,689,305 像元', 'dim'],
    ['重叠区 Pearson r = 0.92 · RMSE 3.1 mm/yr', 'ok'],
    [`质量门：r=0.92 ≥ ${st.params.corr_threshold} → 通过`, 'ok'],
    ['GNSS 对照（1 站）: r = 0.86 · 差异 0.4 mm/yr', 'ok'],
    ['✓ provenance.json · methods_draft.md', 'ok'],
  ],
};

/* ---- 磁盘预算剧情（§7.5）：随步骤完成递减，第 10 步降到 8G 以下触发橙色告警，
        run 结束后 tier-4 临时文件清理回升。数值为示意。 ---- */
const BUDGET_AFTER = { 6: 14.2, 7: 12.1, 8: 10.9, 9: 9.7, 10: 7.2, 11: 6.8 };

/* ---- ERA5 降级剧情（§4.12 / §7.4）：503 × 3 次退避重试后停链 ---- */
const ERA5_FAIL_LINES = [
  ['$ smallbaselineApp.py --dostep correct_troposphere --method tropo_era5_pyaps', 'cmd'],
  ['本地缓存命中 3/7 天 · 需从 CDS 补拉 4 个日期的 ERA5 再分析数据', 'dim'],
  ['GET https://cds.climate.copernicus.eu/api/v2/… → HTTP 503 Service Unavailable', 'err'],
  ['重试 1/3（指数退避 2s）… HTTP 503', 'warn'],
  ['重试 2/3（指数退避 4s）… HTTP 503', 'warn'],
  ['重试 3/3（指数退避 8s）… HTTP 503', 'err'],
  ['失败分类：service_down（规则命中 r"HTTP 5\\d\\d"，未消耗 LLM 调用）', 'dim'],
  ['✗ ERA5 不可达 · 断点已保留（已下载 0/4 天，无需清理）', 'err'],
];

export async function* runPipeline(stepIds, token) {
  const total = stepIds.length;
  let done = 0;

  for (const id of stepIds) {
    // —— 第 8 步 degrade 剧情：ERA5 服务 503 → 停链发 degrade 事件 ——
    // 专家模式：停下等用户处置（§4.12「专家模式下必须问」，处置后 app.js 重新发起续跑）；
    // 向导模式：自动降级 + 显式告知（consume 端应用降级后本迭代内以新方法继续）。
    if (id === 8 && st_(8).method === 'tropo_era5_pyaps') {
      const d8 = def_(8);
      yield { t: 'step.start', stepId: 8 };
      yield { t: 'tool.start', id: 's8', verb: '[08/11]',
              cmd: ERA5_FAIL_LINES[0][0].replace(/^\$ /, ''), label: d8.name, open: true };
      for (let i = 0; i < ERA5_FAIL_LINES.length; i++) {
        const [l, tone] = ERA5_FAIL_LINES[i];
        yield { t: 'tool.log', id: 's8', line: l, tone };
        yield { t: 'tool.progress', id: 's8', pct: Math.round(((i + 1) / ERA5_FAIL_LINES.length) * 80) };
        await wait(i === 0 ? 240 : 380, token);
      }
      yield { t: 'tool.end', id: 's8', exit: 1, summary: '失败 · service_down（HTTP 503 × 3）' };
      yield { t: 'step.end', stepId: 8, exit: 1 };
      yield { t: 'budget', diskFreeGB: 11.8 };
      const auto = S.mode === 'guide';
      yield { t: 'degrade', auto, stepId: 8, failClass: 'service_down',
              from: 'tropo_era5_pyaps', to: 'tropo_height_corr',
              evidenceFrom: 'validated', evidenceTo: 'checked',
              detail: 'ERA5 服务不可用（HTTP 503，重试 3 次）',
              reason: '大气校正精度下降' };
      if (!auto) return;           // 专家模式：停链，等待失败卡上的处置动作
      await wait(320, token);
    }

    const d = def_(id), st = st_(id);   // degrade 分支后再取，拿到降级后的新方法
    const lines = (SCRIPTS[id] || (() => [[`$ run.py --step ${id}`, 'cmd'], ['✓ 完成', 'ok']]))(st);

    yield { t: 'step.start', stepId: id };
    yield { t: 'tool.start', id: `s${id}`, verb: `[${String(id).padStart(2, '0')}/11]`,
            cmd: lines[0][0].replace(/^\$ /, ''), label: d.name, open: true };

    for (let i = 0; i < lines.length; i++) {
      const [l, tone] = lines[i];
      yield { t: 'tool.log', id: `s${id}`, line: l, tone };
      yield { t: 'tool.progress', id: `s${id}`, pct: Math.round(((i + 1) / lines.length) * 100) };
      await wait(i === 0 ? 240 : 400, token);
    }

    done++;
    yield { t: 'tool.end', id: `s${id}`, exit: 0, summary: `${d.name}完成 · ${Math.round(d.dur / 60)} min（示意）`,
            artifacts: (d.outputs || []).map((o) => ({ path: o.path, hash: st.fingerprint })) };
    yield { t: 'step.end', stepId: id, exit: 0 };
    if (BUDGET_AFTER[id] !== undefined) yield { t: 'budget', diskFreeGB: BUDGET_AFTER[id] };
    yield { t: 'overall', pct: Math.round((done / total) * 100) };
    await wait(180, token);
  }

  // run 结束：按预算策略清理 tier-4 临时产物，磁盘回升（§4.10，数值为示意）
  if (stepIds.includes(11)) {
    yield { t: 'say', parts: [
      '流水线执行完毕。按磁盘预算策略（tier-4）清理临时产物：中间解缠残差与滤波缓存约 ',
      { b: '9.0 GB' }, ' 已回收，磁盘 6.8G → 15.8G（示意值）。'] };
    yield { t: 'budget', diskFreeGB: 15.8 };
    await wait(260, token);
  }

  yield { t: 'result' };
  await wait(400, token);
  yield { t: 'report' };
}

/* ============================================================
   §7.4 新条目静态演示：reattach / intervention / gate_stop
   由 hero 下方「查看长任务事件演示」触发，不改变会话状态。
   ============================================================ */
export async function* demoLongTaskEvents(token) {
  yield { t: 'say', parts: [
    '插入三种长任务事件条目的静态演示（', { code: 'reattach' }, ' / ',
    { code: 'intervention' }, ' / ', { code: 'gate_stop' },
    '，AGENT-DESIGN §7.4）。演示条目不改变当前会话状态。'] };
  await wait(360, token);
  yield { t: 'reattach', engine: 'MintPy', pid: 48213, runFor: '1h23m', stepId: 7, logOffset: 184320 };
  await wait(360, token);
  yield { t: 'intervention', mode: 'queue',
          text: '你在第 7 步运行中将第 6 步方法改为 snaphu_smooth → 已排入队列，当前步完成后生效' };
  await wait(360, token);
  yield { t: 'gate_stop', demo: true, stepId: 6, metric: 'unwrap_coverage', value: '0.62', threshold: '0.70',
          msg: '建议改用 snaphu_smooth 重新解缠，或在阈值台账登记依据后放宽门限。',
          suggestions: [
            { label: '改用 snaphu_smooth 重跑第 6 步' },
            { label: '放宽阈值至 0.60（需登记依据）' },
          ] };
}

/** 重跑摘要，供审批卡使用。区分「覆写失效产物」与「首次产出」。 */
export function rerunSummary() {
  const { stale, pending, all } = workSummary();
  const overwrite = stale.flatMap((id) => (def_(id).outputs || []).map((o) => o.path));
  return {
    ids: all,
    stale, pending,
    minutes: Math.round(estimateRerun(all) / 60),
    cmds: all.length,
    files: all.flatMap((id) => (def_(id).outputs || []).map((o) => o.path)),
    overwrite,
  };
}
