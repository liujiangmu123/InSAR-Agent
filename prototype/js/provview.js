/* ============================================================
   provenance 账本浏览器(审计面板下半区的人类可读渲染)。

   职责(与 auditlive.js 互补,绝不重复):
   - auditlive 管「证据阶梯实况」(六级判定/每步来源徽标/阈值台账);
     本模块管「完整账本浏览」—— 把 GET /api/provenance 的 JSON 文档
     渲染成研究者能读的证据树:run 头卡 → 11 步节点(状态/方法/阶段/
     来源)→ 展开看参数表(非默认高亮)/ 指纹三段 / 命令行 / QA 判定;
   - 干预时间线:interventions 按时间列出(谁/何时/做了什么);空清单
     渲染「无人工干预 · 全自动执行」徽章 —— 这本身是重要的可复现性信息;
   - 操作:复制引用块(可贴论文方法章节的中文文本)/ 导出 JSON /
     与同会话另一 run 并排对比(方法/参数/来源/状态差异高亮,浅实现)。

   挂载策略(所有权约束:不改 dock.js/auditlive.js/app.js):
   - dock 的 tab 按钮与 pane 全部由 dock.js 动态生成,index.html 里只有
     #dockBody 容器 → 无法静态新增子 tab,故选「审计 tab 下半区」方案;
   - 自初始化 + 幂等:MutationObserver 观察 #dockBody,#pane-audit 出现
     且不含本区块时追加到 pane 尾部;dock.js 每次重渲染审计面板都会
     replaceChildren 清掉本区块,观察器随即重挂(展开态等 UI 状态存
     模块级变量,重挂不丢失)。非工作区页面(无 #dockBody)零打扰。

   失败语义:后端不可达 / file:// / 无 run(404) → 渲染内置演示文档
   并顶部醒目标注「演示数据」,与各 live 模块同一哲学;绝不抛错。

   大文档性能:步骤详情惰性构建(首次展开才建 DOM);JSON 字段 >2KB
   截断显示,点击再展开全文。
   ============================================================ */
import { h, icon, toast } from './dom.js';
import { S } from './state.js';
import { fetchRuns, activeRunId, runStamp } from './runswitch.js';

/* ============================================================
   纯函数与常量(node 校验脚本直测,不碰 DOM)
   ============================================================ */

/** 来源闭集 → .tag 四色变体(与 auditlive 的色义一字不差:
    绿=本地实证,蓝=父链继承,橙=云端佐证,红=审计缺口)。 */
export const ORIGIN_TAG = {
  local: 'is-ok',
  inherited: 'is-run',
  cloud: 'is-stale',
  missing: 'is-bad',
};

/** origin → tag 类;闭集外的坏数据一律按审计缺口(missing)渲染。 */
export function originTag(origin) {
  return ORIGIN_TAG[origin] || ORIGIN_TAG.missing;
}

/** 短哈希:前 8 位(徽章/头卡用;title 挂全量)。 */
export const shortHash = (v, n = 8) => (v ? String(v).slice(0, n) : '—');

/** UTC 时间:接受 ISO 字符串或 epoch 秒(interventions.consumed_at 是
    time.time() 浮点),统一输出 "YYYY-MM-DD HH:MM:SS UTC";坏值给 '—'。 */
export function fmtUtc(v) {
  if (v === null || v === undefined || v === '') return '—';
  const d = typeof v === 'number' ? new Date(v * 1000) : new Date(String(v));
  if (Number.isNaN(d.getTime())) return '—';
  return d.toISOString().replace('T', ' ').slice(0, 19) + ' UTC';
}

/** 参数表里与默认值不同的键集(默认值来自 GET /api/registry 的能力声明;
    defaults 缺失/该键无默认 → 不标,不凭空声称「非默认」)。 */
export function paramDiffKeys(params, defaults) {
  if (!params || typeof params !== 'object' || !defaults) return [];
  return Object.keys(params).filter((k) =>
    k in defaults && JSON.stringify(params[k]) !== JSON.stringify(defaults[k]));
}

/** JSON 字段截断:序列化后 >limit 字节折叠(大账本里 payload/params
    可能塞下整段配置,全量展开会拖垮面板)。 */
export function truncJson(value, limit = 2048) {
  let full;
  try { full = JSON.stringify(value, null, 1) ?? String(value); }
  catch { full = String(value); }
  if (full.length <= limit) return { text: full, truncated: false, full };
  return { text: full.slice(0, limit), truncated: true, full };
}

/** argv → 可复制的单行命令(含空白/引号的参数加双引号)。 */
export function argvLine(argv) {
  return (Array.isArray(argv) ? argv : [])
    .map((a) => (/[\s"']/.test(String(a)) ? `"${String(a).replace(/"/g, '\\"')}"` : String(a)))
    .join(' ');
}

/** 步骤字典 → 按 step_id 升序的数组(账本 steps 是字符串键字典)。 */
export function sortedSteps(doc) {
  return Object.entries((doc && doc.steps) || {})
    .map(([sid, s]) => ({ sid: Number(sid), ...(s || {}) }))
    .sort((a, b) => a.sid - b.sid);
}

/** 干预条目 → 时间线行数据(谁/何时/做了什么)。pending_actions 全部
    来自用户干预入口(POST /api/actions),actor 固定「用户」。 */
export function interventionLine(iv) {
  const act = (iv && iv.action) || '?';
  const target = iv && iv.target !== null && iv.target !== undefined && iv.target !== ''
    ? `第 ${iv.target} 步` : '';
  const payload = iv && iv.payload && Object.keys(iv.payload).length
    ? JSON.stringify(iv.payload) : '';
  const DELIVER = { steer: '步间生效', follow_up: 'run 后生效', next_run: '下次规划生效' };
  return {
    when: fmtUtc(iv ? iv.consumed_at : null),
    who: '用户',
    what: [act, target].filter(Boolean).join(' · '),
    payload,
    deliver: DELIVER[iv && iv.deliver_as] || (iv && iv.deliver_as) || '',
  };
}

/** 引用块:生成可贴进论文方法章节的中文文本。
    数据/方法链/软件版本/处理日期齐备;缺字段如实给占位,不编数;
    simulated run 必须携带「不构成科学证据」警示(§4.13 没有依据就不给数)。 */
export function citationText(doc) {
  const d = doc || {};
  const steps = sortedSteps(d);
  const env = d.environment || {};
  const tools = Object.entries(env.tools || {});
  const chain = steps.length
    ? steps.map((s) => `${s.name || `步骤${s.sid}`}(${s.method || '方法未记录'})`).join(' → ')
    : '(无步骤记录)';
  const toolsLine = tools.length
    ? tools.map(([k, v]) => `${k} ${v}`).join('、')
    : '未记录';
  const intent = d.intent && typeof d.intent === 'object'
    ? (d.intent.goal || d.intent.text || d.intent.intent || '') : '';
  const lines = [
    `本研究的 InSAR 数据处理由 InSAR-Agent 自动执行并全程记账` +
    `(run ${shortHash(d.run_id)},agent ${shortHash((d.agent || {}).agent_hash)},` +
    `账本 schema ${d.schema_version || '—'})。`,
    `场景:${d.scenario || '未记录'}${intent ? `;研究意图:${intent}` : ''}。`,
    `处理链共 ${steps.length} 步:${chain}。`,
    `软件环境:Python ${env.python || '—'}(${env.platform || '—'});工具版本:${toolsLine}。`,
    `处理完成于 ${fmtUtc(d.generated_at_utc)};QA 判定 ${(d.qa || {}).status || '—'};` +
    `证据级 ${d.evidence_level || '—'}(六级证据阶梯)。`,
  ];
  if (d.simulated) {
    lines.push('注意:该 run 为模拟执行(引擎缺失),结果仅用于流程演示,不构成科学证据。');
  }
  const n = Array.isArray(d.interventions) ? d.interventions.length : 0;
  lines.push(n
    ? `执行期间共 ${n} 次人工干预,逐条记录于 provenance 账本。`
    : '执行全程无人工干预(全自动),完整参数、指纹与命令行随附 provenance.json。');
  return lines.join('\n');
}

/** run 对比(浅实现):两份账本 → 逐步差异清单。
    对比维度按任务口径:方法 / 参数 / 证据来源(origin)/ 状态;
    参数指纹(args_hash)不同但键值相同的情况也如实标出(上游传递差异)。 */
export function diffRuns(docA, docB) {
  const a = docA || {}, b = docB || {};
  const stepsA = Object.fromEntries(sortedSteps(a).map((s) => [s.sid, s]));
  const stepsB = Object.fromEntries(sortedSteps(b).map((s) => [s.sid, s]));
  const srcA = ((a.evidence || {}).step_sources) || {};
  const srcB = ((b.evidence || {}).step_sources) || {};
  const ids = [...new Set([...Object.keys(stepsA), ...Object.keys(stepsB)].map(Number))]
    .sort((x, y) => x - y);

  const steps = ids.map((id) => {
    const sa = stepsA[id], sb = stepsB[id];
    const pa = (sa && sa.params) || {}, pb = (sb && sb.params) || {};
    const paramKeys = [...new Set([...Object.keys(pa), ...Object.keys(pb)])]
      .filter((k) => JSON.stringify(pa[k]) !== JSON.stringify(pb[k])).sort();
    const originA = (srcA[String(id)] || {}).origin || (sa ? 'local' : null);
    const originB = (srcB[String(id)] || {}).origin || (sb ? 'local' : null);
    const row = {
      id,
      name: (sa && sa.name) || (sb && sb.name) || `步骤 ${id}`,
      inA: !!sa,
      inB: !!sb,
      methodA: sa ? sa.method || '' : null,
      methodB: sb ? sb.method || '' : null,
      paramKeys,
      originA,
      originB,
      stateA: sa ? sa.state || '' : null,
      stateB: sb ? sb.state || '' : null,
    };
    row.methodChanged = !!(sa && sb) && row.methodA !== row.methodB;
    row.originChanged = !!(sa && sb) && originA !== originB;
    row.stateChanged = !!(sa && sb) && row.stateA !== row.stateB;
    row.changed = !sa || !sb || row.methodChanged || row.originChanged
      || row.stateChanged || paramKeys.length > 0;
    return row;
  });

  return {
    runA: a.run_id || '', runB: b.run_id || '',
    levelA: a.evidence_level || '', levelB: b.evidence_level || '',
    levelChanged: (a.evidence_level || '') !== (b.evidence_level || ''),
    steps,
    changedCount: steps.filter((s) => s.changed).length,
  };
}

/* ============================================================
   内置演示文档(file:// / 后端不可达时的回落;也是校验脚本的固定样本)
   与 ledger.export_provenance 字段一一同名 —— 模拟 run 的真实形状。
   ============================================================ */

const DEMO_STEP_NAMES = ['数据获取', '辅助数据', '配准', '干涉', '滤波', '解缠',
  '时序反演', '误差校正', '形变模型', '出图导出', '质检'];
const DEMO_METHODS = ['local_import', 'dem_copernicus', 'isce2_tops_geom_esd',
  'isce2_ifg', 'goldstein', 'snaphu_mcf', 'mintpy_sbas', 'era5_pyaps',
  'velocity_fit', 'figure_journal', 'crossval_ps_sbas'];

function demoSteps() {
  const out = {};
  DEMO_STEP_NAMES.forEach((name, i) => {
    const sid = i + 1;
    out[String(sid)] = {
      name, capability: name, method: DEMO_METHODS[i],
      params: sid === 5 ? { alpha: 0.6, window: 32 }
        : sid === 6 ? { min_coherence: 0.3, threads: 8 }
        : { threads: 8 },
      task_hash: `t${sid}a1b2c3d4e5f6a7b8`, args_hash: `a${sid}b2c3d4e5f6a7b8c9`,
      local_hash: `l${sid}c3d4e5f6a7b8c9d0`, eval_hash: `e${sid}d4e5f6a7b8c9d0e1`,
      upstream: sid > 1 ? [String(sid - 1)] : [],
      stage: 'VERIFIED', state: sid === 2 ? 'skipped' : 'done',
      stale: false, stale_reason: null, failure_class: null,
      run_ok: 1, exit_code: 0,
      qa: [{ check: 'exit_code', ok: true, severity: 'pass', detail: 'exit_code=0,期望 0' },
           { check: 'artifact_exists', ok: true, severity: 'pass', detail: `artifact demo_${sid}` }],
      commands: sid === 2 ? [] : [{
        argv: ['python', `step_${sid}.py`, '--method', DEMO_METHODS[i], '--threads', '8'],
        exit_code: 0, duration: 4.2, attempt: 1, cmd_path: `steps/${sid}/cmd.sh`,
      }],
    };
  });
  return out;
}

/** 演示账本:模拟 run(simulated),证据级按后端规则封顶 runnable。 */
export const DEMO_DOC = {
  schema_version: '1.0',
  run_id: '20260813T090000-demo0001',
  session_id: 'ridgecrest-2019',
  parent_run_id: null,
  generated_at_utc: '2026-08-13T09:12:00Z',
  simulated: true,
  environment: { python: '3.11.9', platform: 'linux', tools: { isce2: '2.6.3', mintpy: '1.5.1', snaphu: '2.0.7' } },
  repo: { git_head: '9cbd3ea', git_dirty: 0 },
  agent: { agent_hash: 'f00dcafe12345678' },
  intent: { goal: 'Ridgecrest 2019 同震形变(演示)' },
  scenario: 'coseismic_interferogram',
  steps: demoSteps(),
  artifacts: {}, metrics: {},
  thresholds: { corr_threshold: { value: 0.9, source: 'literature', ref: 'contract.yaml', status: 'PENDING' } },
  qa: { status: 'pass' },
  evidence: {
    level: 'runnable', level_index: 0,
    ladder: ['runnable', 'checked', 'audited', 'calibrated', 'validated', 'publishable'],
    reasons: ['封顶 runnable:模拟执行(引擎缺失),演示结果不构成证据'],
    ceiling: 'runnable', ceiling_reason: '模拟执行(引擎缺失),演示结果不构成证据',
    step_sources: Object.fromEntries(DEMO_STEP_NAMES.map((_, i) => {
      const sid = String(i + 1);
      if (sid === '2') return [sid, { origin: 'cloud', source: 'cloud(manifest sha256:ab12cd34ef56)', manifest_sha256: 'ab12cd34ef56a7b8' }];
      if (sid === '3') return [sid, { origin: 'inherited', source: 'inherited(parent=20260812T080000-p0)', parent_run_id: '20260812T080000-p0' }];
      if (sid === '4') return [sid, { origin: 'missing', source: 'missing', detail: '沿祖先链未找到复用步骤的产物记录' }];
      return [sid, { origin: 'local', source: 'local' }];
    })),
    parent_validations: [],
  },
  evidence_level: 'runnable',
  warnings: [],
  interventions: [
    { action: 'SET_PARAMS', target: '6', payload: { params: { min_coherence: 0.3 } },
      deliver_as: 'steer', consumed_at: 1786957320 },
    { action: 'PAUSE', target: null, payload: {}, deliver_as: 'steer', consumed_at: 1786957440 },
  ],
};

/** 演示对比样本:同链 fork(第 6 步换方法、第 5 步改参、末步失败),
    对比视图与 diff 校验共用 —— 差异是刻意设计的。 */
export const DEMO_DOC_B = (() => {
  const b = JSON.parse(JSON.stringify(DEMO_DOC));
  b.run_id = '20260813T100000-demo0002';
  b.parent_run_id = DEMO_DOC.run_id;
  b.generated_at_utc = '2026-08-13T10:30:00Z';
  b.steps['6'].method = 'snaphu_smooth';
  b.steps['5'].params.alpha = 0.8;
  b.steps['11'].state = 'failed';
  b.steps['11'].run_ok = 0;
  b.evidence.step_sources['4'] = { origin: 'local', source: 'local' };
  b.evidence_level = 'runnable';
  b.qa = { status: 'fail' };
  b.interventions = [];
  return b;
})();

/** 演示模式的参数默认参照(与 DEMO_DOC 刻意错开:第 6 步 min_coherence
    默认 0.4,账本里 0.3 → 非默认高亮有真实素材)。 */
const DEMO_DEFAULTS = {
  5: { alpha: 0.6, window: 32 },
  6: { min_coherence: 0.4, threads: 8 },
};

/* ============================================================
   数据层:/api/provenance 原始文档 + /api/registry 参数默认值
   ============================================================ */

const TTL_MS = 30_000;
const docCache = new Map();   // `${session}|${runId}` → { at, promise }

/** 手动刷新入口:清缓存,下一次 fetch 必然重拉。 */
export function invalidate() {
  docCache.clear();
  regCache = { at: 0, promise: null };
}

async function getJson(url) {
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

/** 原始 provenance 文档(30s TTL;失败/file:// → null,绝不抛错)。
    auditlive 拉同一端点但只留归一化结果,本模块要原文(导出/引用块/
    对比都需要完整字段),各自缓存互不打扰。 */
export function fetchProvDoc({ runId = null, force = false } = {}) {
  if (typeof location !== 'undefined' && location.protocol === 'file:') {
    return Promise.resolve(null);
  }
  const key = `${S.sessionId}|${runId || ''}`;
  const hit = docCache.get(key);
  if (!force && hit && Date.now() - hit.at < TTL_MS) return hit.promise;
  const promise = (async () => {
    try {
      const qs = new URLSearchParams({ session: S.sessionId });
      if (runId) qs.set('run_id', runId);
      return await getJson(`/api/provenance?${qs}`);
    } catch {
      docCache.delete(key);   // 失败不占缓存位:下次立即重试
      return null;
    }
  })();
  docCache.set(key, { at: Date.now(), promise });
  return promise;
}

let regCache = { at: 0, promise: null };

/** 参数默认值表 {stepId: {param: default}}(/api/registry 能力声明,
    静态数据给 10 分钟长缓存;失败 → null,参数表不做非默认标注)。 */
export function fetchDefaults() {
  if (typeof location !== 'undefined' && location.protocol === 'file:') {
    return Promise.resolve(null);
  }
  if (regCache.promise && Date.now() - regCache.at < 600_000) return regCache.promise;
  const promise = (async () => {
    try {
      const caps = await getJson('/api/registry');
      const out = {};
      for (const cap of caps || []) {
        out[cap.id] = Object.fromEntries(
          Object.entries(cap.params || {}).map(([k, p]) => [k, p.default]));
      }
      return out;
    } catch {
      regCache = { at: 0, promise: null };
      return null;
    }
  })();
  regCache = { at: Date.now(), promise };
  return promise;
}

/* ============================================================
   渲染:run 头卡 / 操作行 / 干预时间线 / 证据树 / 对比双列
   ============================================================ */

/** UI 状态存模块级:dock 重渲染审计面板会清掉本区块,重挂后不丢展开态。 */
const ui = {
  open: new Set(),        // 已展开的步骤 id
  compareRunId: null,     // 对比目标 run(null = 未开启;'demo-b' = 演示样本)
  session: null,          // 状态所属会话:切会话自动复位
};

function resetUiIfSessionChanged() {
  if (ui.session !== S.sessionId) {
    ui.open.clear();
    ui.compareRunId = null;
    ui.session = S.sessionId;
  }
}

/** 剪贴板写入:clipboard API 优先,file:// 退回隐藏 textarea
    (dock.js 有同款但未导出;所有权约束不改它)。 */
async function copyText(text, okMsg) {
  try {
    await navigator.clipboard.writeText(text);
    toast(okMsg);
  } catch {
    const ta = h('textarea', { style: { position: 'fixed', top: '-100px', opacity: '0' } }, text);
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch { ok = false; }
    ta.remove();
    toast(ok ? okMsg : '复制失败:浏览器未授权剪贴板访问');
  }
}

/** Blob 下载(与 dock.js 同款,未导出故自带)。 */
function download(filename, text, mime = 'application/json') {
  const url = URL.createObjectURL(new Blob([text], { type: `${mime};charset=utf-8` }));
  const a = h('a', { href: url, download: filename });
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

const STATE_DOT = {
  done: 'd-done', running: 'd-run', failed: 'd-fail', skipped: 'd-skip',
  pending: 'd-pend', stale: 'd-stale', interrupted: 'd-stale', orphaned: 'd-stale',
};
const STATE_TXT = {
  done: '完成', running: '运行中', failed: '失败', skipped: '跳过(缓存/云端)',
  pending: '待运行', stale: '失效', interrupted: '已中断', orphaned: '环境中断',
};

/** 来源徽章(auditlive 同一文案口径:inherited 带父 run 短 id,cloud 带
    manifest 短哈希)。 */
function originBadge(src) {
  const origin = (src && src.origin) || 'missing';
  const label = origin === 'inherited' ? `inherited(${shortHash(src.parent_run_id) || 'parent'})`
    : origin === 'cloud' ? `cloud(${String((src && src.manifest_sha256) || '').slice(0, 12) || 'manifest'})`
    : origin;
  return h('span', {
    class: `tag ${originTag(origin)}`,
    title: (src && (src.detail || src.source)) || origin,
  }, label);
}

/** 指纹三段行:inputs/params/env 前 8 位,title 全量。
    字段对应(stale.parseStaleReason 的三段口径):
    inputs=eval_hash(含上游传递)/ params=args_hash(科学参数)/
    env=task_hash(方法+工具版本)。 */
function fpTriple(s) {
  const seg = (label, hash, why) => h('span', {
    class: 'prov-fp mono',
    title: `${why}\n全量:${hash || '(未记录)'}`,
  }, h('b', null, label), ' ', shortHash(hash));
  return h('div', { class: 'prov-fps' },
    seg('inputs', s.eval_hash, 'eval_hash:含上游产物传递的求值指纹'),
    seg('params', s.args_hash, 'args_hash:科学参数指纹'),
    seg('env', s.task_hash, 'task_hash:方法 + 工具版本指纹'));
}

/** JSON 值节点(>2KB 截断 + 展开按钮;幂等重挂后回到截断态,可接受)。 */
function jsonNode(value, limit = 2048) {
  const t = truncJson(value, limit);
  if (!t.truncated) return h('pre', { class: 'prov-json' }, t.text);
  const pre = h('pre', { class: 'prov-json' }, t.text,
    h('span', { class: 'prov-json-more' }, `\n… 已截断(全文 ${t.full.length} 字符)`));
  const btn = h('button', {
    class: 'btn btn-gho btn-sm', type: 'button',
    onclick: () => { pre.replaceChildren(document.createTextNode(t.full)); btn.remove(); },
  }, '展开全文');
  return h('div', null, pre, btn);
}

/** 参数表(与默认值不同的键高亮;defaults null → 不标注)。 */
function paramTable(s, defaults) {
  const params = s.params || {};
  const keys = Object.keys(params);
  if (!keys.length) return h('p', { class: 'blurb' }, '该步无参数记录。');
  const defs = defaults ? defaults[s.sid] : null;
  const diff = new Set(paramDiffKeys(params, defs));
  return h('div', { class: 'prov-params' }, ...keys.map((k) => {
    const changed = diff.has(k);
    const raw = JSON.stringify(params[k]);
    return h('div', { class: `prow${changed ? ' diff' : ''}` },
      h('span', { class: 'k mono' }, k),
      // 大参数值(>2KB)走截断渲染:整段配置塞进参数时不拖垮面板
      raw && raw.length > 2048
        ? h('span', { class: 'v' }, jsonNode(params[k]))
        : h('span', { class: 'v mono' }, raw),
      changed ? h('span', {
        class: 'tag is-stale',
        title: `默认值 ${JSON.stringify(defs[k])}(GET /api/registry 能力声明)`,
      }, '非默认') : null);
  }));
}

/** 命令行区:argv 折叠展示(aria-expanded)+ 一键复制。 */
function commandRows(s) {
  const cmds = s.commands || [];
  if (!cmds.length) return h('p', { class: 'blurb' }, '该步无命令记录(跳过或复用)。');
  return h('div', null, ...cmds.map((c, i) => {
    const line = argvLine(c.argv);
    const body = h('div', { class: 'prov-cmd-body', hidden: true },
      h('pre', { class: 'prov-json' }, line),
      h('div', { class: 'prov-cmd-meta mono' },
        `exit ${c.exit_code ?? '—'} · ${typeof c.duration === 'number' ? `${c.duration.toFixed(1)} s` : '—'}` +
        ` · 第 ${c.attempt ?? 1} 次尝试${c.cmd_path ? ` · ${c.cmd_path}` : ''}`));
    let open = false;   // 开合态自持,不回读 hidden 属性(无浏览器校验环境同语义)
    const tog = h('button', {
      class: 'prov-cmd-tog mono', type: 'button', 'aria-expanded': 'false',
      onclick: () => {
        open = !open;
        body.hidden = !open;
        tog.setAttribute('aria-expanded', String(open));
      },
    }, icon('chevron'), `命令 ${i + 1} · ${line.length > 64 ? `${line.slice(0, 64)}…` : line}`);
    return h('div', { class: 'prov-cmd' },
      h('div', { class: 'prov-cmd-hd' },
        tog,
        h('button', {
          class: 'btn btn-gho btn-sm', type: 'button',
          'aria-label': `复制第 ${i + 1} 条命令行`,
          onclick: () => copyText(line, '已复制命令行'),
        }, icon('clip'), '复制')),
      body);
  }));
}

/** QA 判定表(runok.to_qa 的原样字段:check/ok/severity/detail —— 值与
    阈值都在 detail 文本里,如实透传不再自行拆数)。 */
function qaTable(s) {
  const qa = s.qa || [];
  if (!qa.length) return h('p', { class: 'blurb' }, '该步无 QA 检查记录。');
  const TONE = { pass: 'is-ok', warn: 'is-stale', fail: 'is-bad', stop: 'is-bad' };
  return h('div', { class: 'prov-qa' }, ...qa.map((c) => h('div', { class: 'qrow' },
    h('span', { class: 'k mono' }, c.check || '—'),
    h('span', { class: `tag ${TONE[c.severity] || (c.ok ? 'is-ok' : 'is-bad')}` },
      c.severity || (c.ok ? 'pass' : 'fail')),
    h('span', { class: 'v' }, c.detail || ''))));
}

/** 单个步骤节点:头行(色点/名称/方法/阶段/来源徽章,aria-expanded)+
    惰性详情(首次展开才构建 DOM —— 大账本性能要求)。 */
function stepNode(s, src, defaults) {
  const dot = STATE_DOT[s.state] || 'd-pend';
  const detail = h('div', {
    class: 'prov-step-bd', role: 'region',
    'aria-label': `第 ${s.sid} 步 ${s.name || ''} 详情`,
    hidden: !ui.open.has(s.sid) || undefined,
  });
  let built = false;
  const build = () => {
    if (built) return;
    built = true;
    detail.replaceChildren(
      h('div', { class: 'prov-sub' }, '参数(非默认值高亮)'),
      paramTable(s, defaults),
      h('div', { class: 'prov-sub' }, '指纹三段(悬停看全量)'),
      fpTriple(s),
      h('div', { class: 'prov-sub' }, '命令行'),
      commandRows(s),
      h('div', { class: 'prov-sub' }, 'QA 判定'),
      qaTable(s),
      s.stale ? h('div', { class: 'note is-stale' }, icon('warn'),
        h('span', null, `已失效:${s.stale_reason || '原因未记录'}`)) : null);
  };
  if (ui.open.has(s.sid)) build();

  const head = h('button', {
    class: 'prov-step-hd', type: 'button',
    'aria-expanded': String(ui.open.has(s.sid)),
    onclick: () => {
      const open = !ui.open.has(s.sid);   // ui.open 是唯一真相,不回读 hidden 属性
      if (open) { build(); ui.open.add(s.sid); } else { ui.open.delete(s.sid); }
      detail.hidden = !open;
      head.setAttribute('aria-expanded', String(open));
    },
  },
    h('span', { class: `dot ${dot}`, title: STATE_TXT[s.state] || s.state }),
    h('span', { class: 'no mono' }, `#${s.sid}`),
    h('span', { class: 'nm' }, s.name || `步骤 ${s.sid}`),
    h('span', { class: 'mth mono' }, s.method || '—'),
    // 非 done 状态补文字标注:状态不只靠色点(语义三重表达,tokens.css 首则)
    s.state && s.state !== 'done'
      ? h('span', { class: 'st' }, STATE_TXT[s.state] || s.state) : null,
    h('span', { class: `tag ${s.stage === 'VERIFIED' ? 'is-ok' : ''}`,
      title: '执行阶段高水位(VERIFIED = 产物已核证,进入证据链)' }, s.stage || '—'),
    originBadge(src));
  return h('div', { class: 'prov-step' }, head, detail);
}

/** 干预时间线;空 → 「无人工干预 · 全自动执行」徽章(可复现性证词)。 */
function interventionTimeline(doc) {
  const ivs = Array.isArray(doc.interventions) ? doc.interventions : [];
  if (!ivs.length) {
    return h('div', { class: 'prov-iv-empty' },
      h('span', { class: 'tag is-ok' }, icon('check'), '无人工干预 · 全自动执行'),
      h('span', { class: 'blurb', style: { margin: '0' } },
        '整条链未经手工修正 —— 复现只需同一账本与同一环境。'));
  }
  return h('div', { class: 'prov-iv' }, ...ivs.map((iv) => {
    const line = interventionLine(iv);
    return h('div', { class: 'ivrow' },
      h('span', { class: 'when mono' }, line.when),
      h('span', { class: 'who' }, line.who),
      h('span', { class: 'what mono' }, line.what),
      line.deliver ? h('span', { class: 'tag' }, line.deliver) : null,
      line.payload ? h('span', { class: 'pl mono', title: line.payload },
        line.payload.length > 48 ? `${line.payload.slice(0, 48)}…` : line.payload) : null);
  }));
}

/** run 头卡:id/场景/意图/agent_hash 前 8 位/生成时间 UTC + 证据级/QA。 */
function headCard(doc) {
  const kv = (k, v, mono = true, title = null) => h('div', { class: 'hrow' },
    h('span', { class: 'k' }, k),
    h('span', { class: `v${mono ? ' mono' : ''}`, title: title || undefined }, v));
  const intent = doc.intent && typeof doc.intent === 'object'
    ? (doc.intent.goal || doc.intent.text || JSON.stringify(doc.intent)) : String(doc.intent || '');
  return h('div', { class: 'prov-head' },
    h('div', { class: 'prov-head-tags' },
      h('span', { class: 'tag is-run', title: '六级证据阶梯当前级(权威判定在上方实况区)' },
        icon('shield'), `证据级 ${doc.evidence_level || '—'}`),
      h('span', { class: `tag is-${(doc.qa || {}).status === 'pass' ? 'ok' : 'bad'}` },
        `QA ${(doc.qa || {}).status || '—'}`),
      doc.simulated ? h('span', { class: 'tag is-stale' }, icon('warn'), '模拟执行') : null),
    kv('run', doc.run_id || '—', true, doc.run_id),
    kv('场景', doc.scenario || '未记录', false),
    kv('意图', intent || '未记录', false),
    kv('agent', shortHash((doc.agent || {}).agent_hash), true,
      `agent_hash 全量:${(doc.agent || {}).agent_hash || '(未记录)'}`),
    kv('生成', fmtUtc(doc.generated_at_utc)),
    doc.parent_run_id ? kv('父 run', doc.parent_run_id, true, doc.parent_run_id) : null);
}

/** 对比双列(浅实现):每步一行两格,差异字段高亮。 */
function compareView(diff) {
  const cell = (row, side) => {
    if ((side === 'a' && !row.inA) || (side === 'b' && !row.inB)) {
      return h('div', { class: 'ccell miss' }, '(此 run 无该步)');
    }
    const m = side === 'a' ? row.methodA : row.methodB;
    const o = side === 'a' ? row.originA : row.originB;
    const st = side === 'a' ? row.stateA : row.stateB;
    return h('div', { class: 'ccell' },
      h('span', { class: `mono${row.methodChanged ? ' hl' : ''}` }, m || '—'),
      row.paramKeys.length ? h('span', { class: 'hl mono', title: `参数差异:${row.paramKeys.join(', ')}` },
        `Δ参数 ${row.paramKeys.join(',')}`) : null,
      h('span', { class: `tag ${originTag(o)}${row.originChanged ? ' hlring' : ''}` }, o || '—'),
      h('span', { class: `mono${row.stateChanged ? ' hl' : ''}` }, st || '—'));
  };
  return h('div', { class: 'prov-cmp' },
    h('div', { class: 'chd' },
      h('span', null, `当前 · ${runStamp(diff.runA)}`),
      h('span', null, `对比 · ${runStamp(diff.runB)}`)),
    h('div', { class: 'chd sub' },
      h('span', { class: diff.levelChanged ? 'hl' : '' }, `证据级 ${diff.levelA || '—'}`),
      h('span', { class: diff.levelChanged ? 'hl' : '' }, `证据级 ${diff.levelB || '—'}`)),
    ...diff.steps.map((row) => h('div', { class: `crow${row.changed ? ' changed' : ''}` },
      h('div', { class: 'cno mono' }, `#${row.id} ${row.name}`),
      cell(row, 'a'),
      cell(row, 'b'))),
    h('p', { class: 'blurb' },
      `${diff.changedCount} 步存在差异(方法/参数/证据来源/状态);` +
      '相同步骤不高亮。对比为只读浅视图,细节请分别切 run 查看。'));
}

/** 主渲染(纯函数:数据进节点出;fetch 由 render() 外壳负责)。
    runs 为同会话 run 清单(null → 演示对比样本);compareDoc 非空 → 追加对比区。 */
export function renderDoc(doc, { defaults = null, runs = null, compareDoc = null, demo = false, onRefresh = null, onCompare = null } = {}) {
  const steps = sortedSteps(doc);
  const sources = ((doc.evidence || {}).step_sources) || {};

  // ---- 操作行:复制引用块 / 导出 JSON / 对比下拉 ----
  const others = (runs || []).filter((r) => r.run_id !== doc.run_id);
  const cmpSel = h('select', {
    'aria-label': '选择要对比的同会话 run',
    onchange: (e) => onCompare && onCompare(e.target.value || null),
  },
    h('option', { value: '', selected: !ui.compareRunId || undefined }, '不对比'),
    ...(demo
      ? [h('option', { value: 'demo-b', selected: ui.compareRunId === 'demo-b' || undefined },
          `演示样本 · ${runStamp(DEMO_DOC_B.run_id)}(fork 改参)`)]
      : others.map((r) => h('option', {
          value: r.run_id, selected: ui.compareRunId === r.run_id || undefined,
        }, `${runStamp(r.run_id)}(${r.status || '?'})`))));

  const acts = h('div', { class: 'prov-acts' },
    h('button', {
      class: 'btn btn-pri btn-sm', type: 'button',
      'aria-label': '复制可贴进论文方法章节的引用块',
      onclick: () => copyText(citationText(doc), '已复制引用块(数据/方法链/版本/日期)'),
    }, icon('clip'), '复制引用块'),
    h('button', {
      class: 'btn btn-gho btn-sm', type: 'button',
      'aria-label': '下载原始 provenance JSON 文档',
      onclick: () => {
        download(`provenance_${shortHash(doc.run_id, 24)}.json`, JSON.stringify(doc, null, 1));
        toast('已导出 provenance.json(原始账本)');
      },
    }, icon('doc'), '导出 JSON'),
    h('label', { class: 'prov-cmp-lb' }, '对比', cmpSel),
    onRefresh ? h('button', {
      class: 'btn btn-gho btn-sm', type: 'button',
      'aria-label': '重新读取账本(跳过 30 秒缓存)',
      onclick: onRefresh,
    }, icon('refresh'), '刷新') : null);

  const out = [
    h('h3', { class: 'sect' }, '完整账本 · Provenance 浏览'),
    demo ? h('div', { class: 'note is-stale', role: 'status', style: { marginBottom: '8px' } },
      icon('warn'),
      h('span', null, h('b', null, '演示数据'),
        ':后端不可达或本会话无 run,以下为内置演示账本,非真实运行记录。')) : null,
    headCard(doc),
    acts,
    h('h3', { class: 'sect' }, '干预时间线'),
    interventionTimeline(doc),
    h('h3', { class: 'sect' }, `证据树 · ${steps.length} 步(点击展开)`),
    steps.length
      ? h('div', { class: 'prov-tree' },
          ...steps.map((s) => stepNode(s, sources[String(s.sid)], defaults)))
      : h('p', { class: 'blurb' }, '账本无步骤记录。'),
  ];
  if (compareDoc) {
    out.push(h('h3', { class: 'sect' }, 'run 对比 · 差异高亮'),
      compareView(diffRuns(doc, compareDoc)));
  }
  out.push(h('p', { class: 'audit-sub' },
    demo ? '演示账本 · 与 GET /api/provenance 字段同形'
      : 'GET /api/provenance 原始账本 · 缓存 30 s,「刷新」强制重拉'));
  return out.filter(Boolean);
}

/* ============================================================
   自初始化挂载(幂等):#pane-audit 出现/重渲染 → 追加本区块
   ============================================================ */

let rootEl = null;      // 本区块根节点(单例)
let observer = null;
let renderSeq = 0;      // 竞态防护:只有最后一次 render 的结果落地

async function render() {
  if (!rootEl) return;
  resetUiIfSessionChanged();
  const seq = ++renderSeq;
  const runId = activeRunId();
  const [doc, defaults, runs] = await Promise.all([
    fetchProvDoc({ runId }),
    fetchDefaults(),
    fetchRuns().catch(() => null),
  ]);
  if (seq !== renderSeq || !rootEl || !rootEl.isConnected) return;   // 已被更新的渲染取代

  const demo = doc === null;
  const shown = demo ? DEMO_DOC : doc;
  // 对比选择跨模式失效:演示模式只认演示样本,真实模式只认真实 run id
  if (demo && ui.compareRunId && ui.compareRunId !== 'demo-b') ui.compareRunId = null;
  if (!demo && ui.compareRunId === 'demo-b') ui.compareRunId = null;
  let compareDoc = null;
  if (demo && ui.compareRunId === 'demo-b') {
    compareDoc = DEMO_DOC_B;
  } else if (!demo && ui.compareRunId) {
    compareDoc = await fetchProvDoc({ runId: ui.compareRunId });
    if (seq !== renderSeq) return;
    if (!compareDoc) { ui.compareRunId = null; toast('对比 run 的账本读取失败'); }
  }

  rootEl.replaceChildren(...renderDoc(shown, {
    defaults: demo ? DEMO_DEFAULTS : defaults,   // 演示模式用内置默认参照,高亮有素材
    runs, compareDoc, demo,
    onRefresh: () => { invalidate(); render(); },
    onCompare: (rid) => { ui.compareRunId = rid || null; render(); },
  }));
}

function ensureMounted() {
  const pane = document.getElementById('pane-audit');
  if (!pane) return;
  if (rootEl && pane.contains(rootEl)) return;     // 已在场:幂等直返
  if (!rootEl) {
    rootEl = h('section', { class: 'provview', 'aria-label': 'Provenance 完整账本浏览' },
      h('p', { class: 'blurb' }, '正在读取完整账本(GET /api/provenance)…'));
  }
  pane.appendChild(rootEl);                        // 审计 tab 下半区(pane 尾部)
  render();
}

/** 自初始化(幂等,可重复调用)。非工作区页面(无 #dockBody)零打扰。 */
export function initProvView() {
  if (typeof document === 'undefined' || observer) return;
  const start = () => {
    const dockBody = document.getElementById('dockBody');
    if (!dockBody || observer) return;
    // dock.js 每次 render('audit') 都 replaceChildren 整个 pane,本区块
    // 随之被摘除;观察器负责重挂(回调里 contains 判断,自身挂载不再触发)
    observer = new MutationObserver(ensureMounted);
    observer.observe(dockBody, { childList: true, subtree: true });
    ensureMounted();
  };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
}

initProvView();
