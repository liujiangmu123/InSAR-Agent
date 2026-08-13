/* ============================================================
   审计面板实时数据(「审计」面板真实化的唯一新增模块)。

   职责:
   - 拉取 GET /api/provenance(权威证据链:evidence.level 六级判定 /
     每步证据来源 step_sources / 父链验证清单 parent_validations)与
     GET /api/env 的 thresholds 阈值台账(经 envlive.fetchEnvLive 复用
     其 30 秒缓存,同一探测端点不做双份轰炸);本模块对整体结果自身
     也做 30 秒 TTL 缓存 + invalidate() 手动刷新(与 envlive 同模式);
   - 归一化为 dock.js auditView 直接消费的渲染数据结构(字段清单
     与 tests/test_audit_panel_contract.py 的断言一一对应,防契约漂移);
   - renderLive() 产出实时渲染节点:级别词汇严格使用后端六级
     (evidence.ladder 原样透传),不再出现本地演示逻辑自算的级别。

   失败语义:无 run(/api/provenance 404)/后端不可达/file:// 打开
   → resolve null,绝不抛错;调用方拿到 null 回落本地演示渲染并在
   顶部挂「演示数据」横幅(demoBanner 复用 envlive,两面板样式一致)。
   ============================================================ */
import { h, icon } from './dom.js';
import { S } from './state.js';
import { demoBanner, fetchEnvLive, thresholdSourceLabel } from './envlive.js';
import { activeRunId } from './runswitch.js';   // run 历史切换器:选中历史 run 时透传 run_id

export { demoBanner };   // 演示回落横幅:与 env 面板同款样式,由 auditView 复用

/** 缓存 TTL:与 envlive 一致,30s 内复用同一结果;手动刷新走 invalidate()。 */
const TTL_MS = 30_000;

/* 证据链随 run 推进而变且按会话隔离,缓存必须绑定 session:
   切换会话后旧缓存立即失效,不把 A 会话的证据级渲染进 B 会话。
   run 历史切换器接线后同理绑定 runId:切 run 立即失效,不串证据链。 */
let cache = { at: 0, session: null, runId: null, promise: null };

/** 手动刷新入口:清缓存,下一次 fetchAuditLive() 必然重新拉取。 */
export function invalidate() {
  cache = { at: 0, session: null, runId: null, promise: null };
}

async function getJson(url) {
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

/** 拉取证据链实况(带 30s TTL 缓存;force=true 跳过缓存)。
    /api/provenance 是主数据源,404(无 run)/不可达 → 整体返回 null;
    /api/env 是辅数据源(阈值台账),失败只回落 provenance 内嵌的同款契约。
    runId 可选(run 历史切换器接线,runswitch.js):缺省取 activeRunId(),
    非空 → /api/provenance 带 run_id 查看历史 run 的证据链;
    null(最新)行为与接线前完全一致。阈值台账取自 /api/env,是会话级
    环境实测,不随 run 切换。 */
export function fetchAuditLive({ force = false, runId = activeRunId() } = {}) {
  if (location.protocol === 'file:') return Promise.resolve(null);  // 与 envlive 同判据
  if (!force && cache.promise && cache.session === S.sessionId
      && cache.runId === (runId || null)
      && Date.now() - cache.at < TTL_MS) return cache.promise;

  const session = S.sessionId;
  const promise = (async () => {
    try {
      const qs = new URLSearchParams({ session });
      if (runId) qs.set('run_id', runId);
      const [prov, env] = await Promise.all([
        getJson(`/api/provenance?${qs}`),
        fetchEnvLive().catch(() => null),   // fetchEnvLive 自身不抛错,兜底一层
      ]);
      return normalize(prov, env);
    } catch {
      cache = { at: 0, session: null, runId: null, promise: null };  // 失败不占缓存位:下次立即重试
      return null;
    }
  })();
  cache = { at: Date.now(), session, runId: runId || null, promise };
  return promise;
}

/* ============================================================
   归一化:后端响应 → 渲染数据结构
   这里读取的每一个后端字段都在 tests/test_audit_panel_contract.py
   里有对应断言(与 test_env_panel_contract.py 同一防漂移机制)。
   ============================================================ */

/** 后端六级词汇兜底(audit/ladder.py 的 LADDER);正常路径 evidence.ladder
    原样透传 —— 词汇表以后端为准,前端不自算、不翻译。 */
const LADDER_FALLBACK = ['runnable', 'checked', 'audited', 'calibrated', 'validated', 'publishable'];

const shortId = (v) => (v ? String(v).slice(0, 8) : '');

/** step_sources 单条 → 徽标结构。origin 闭集 local|inherited|cloud|missing;
    徽标文本按任务口径:inherited 带父 run 短 id,cloud 带 manifest 短哈希。 */
function parseStepSource(sid, src, stepMeta) {
  const origin = src.origin || 'missing';
  const badge = origin === 'inherited' ? `inherited(${shortId(src.parent_run_id) || 'parent'})`
    : origin === 'cloud' ? `cloud(${String(src.manifest_sha256 || '').slice(0, 12) || 'manifest'})`
    : origin;   // local / missing 直接用词
  return {
    id: sid,
    name: (stepMeta && stepMeta.name) || `步骤 ${sid}`,
    state: (stepMeta && stepMeta.state) || '',
    origin,
    badge,
    source: src.source || origin,          // 后端已格式化的完整来源描述
    detail: src.detail || '',              // missing 的缺口说明(悬停可见)
    parentRunId: src.parent_run_id || null,
  };
}

/** /api/provenance + /api/env → auditView 消费的结构。 */
function normalize(prov, env) {
  const ev = prov.evidence || {};
  const stepsMeta = prov.steps || {};
  const ladder = (Array.isArray(ev.ladder) && ev.ladder.length) ? ev.ladder : LADDER_FALLBACK;

  const stepSources = Object.entries(ev.step_sources || {})
    .map(([sid, src]) => parseStepSource(sid, src || {}, stepsMeta[sid]))
    .sort((a, b) => Number(a.id) - Number(b.id));

  const parentValidations = (ev.parent_validations || []).map((v) => ({
    name: v.name,
    value: v.value,
    unit: v.unit || '',
    runId: v.run_id || '',
    argsHash: v.args_hash || '',
    note: v.note || '外部验证不随 fork 继承,需以当前参数重新验证',
  }));

  // 阈值台账:/api/env 的 thresholds 列表为主源(与 env 面板同一数据);
  // env 不可用时回落 provenance 内嵌的同一份契约(dict → list,键序稳定)
  const thresholds = (env && Array.isArray(env.thresholds) && env.thresholds.length)
    ? env.thresholds
    : Object.entries(prov.thresholds || {}).map(([key, t]) => ({ key, ...t }));

  return {
    runId: prov.run_id || '',
    parentRunId: prov.parent_run_id || null,
    simulated: !!prov.simulated,
    qaStatus: (prov.qa || {}).status || '',
    level: ev.level || ladder[0],
    levelIndex: Number.isInteger(ev.level_index) ? ev.level_index : 0,
    ladder,
    reasons: Array.isArray(ev.reasons) ? ev.reasons : [],
    ceiling: ev.ceiling || null,
    ceilingReason: ev.ceiling_reason || '',
    stepSources,
    parentValidations,
    thresholds,
    fetchedAt: Date.now(),
  };
}

/* ============================================================
   实时渲染:六级阶梯 / 每步来源徽标 / 父链验证清单 / 阈值台账
   ============================================================ */

/** origin → 既有 .tag 四色变体(色义:绿=本地实证,蓝=父链继承,
    橙=云端佐证(过程不可本地复核),红=审计缺口)。 */
const ORIGIN_TAG = {
  local: 'is-ok',
  inherited: 'is-run',
  cloud: 'is-stale',
  missing: 'is-bad',
};

/** 实时渲染节点数组。onRefresh:清缓存 + 面板重渲染(调用方负责);
    gotoStep:面板联动(§7.3 第 4 条)—— 点步骤行跳流水线并选中该步。 */
export function renderLive(data, { onRefresh, gotoStep } = {}) {
  const time = new Date(data.fetchedAt).toLocaleTimeString('zh-CN', { hour12: false });
  const capIdx = data.ceiling ? data.ladder.indexOf(data.ceiling) : -1;

  // ---- 元信息行:run 短 id / 模拟标记 / QA / 手动刷新 ----
  const meta = h('div', { class: 'audit-meta' },
    h('span', { class: 'mono' }, `run ${shortId(data.runId)}`),
    data.simulated ? h('span', { class: 'tag is-stale' }, icon('warn'), '模拟执行') : null,
    h('span', { class: `tag is-${data.qaStatus === 'pass' ? 'ok' : 'bad'}` },
      `QA ${data.qaStatus || '—'}`),
    h('span', { class: 'sp' }),
    h('button', {
      class: 'btn btn-gho btn-sm', type: 'button',
      'aria-label': '重新读取证据链(跳过 30 秒缓存)',
      onclick: onRefresh,
    }, icon('refresh'), '刷新'));

  // ---- 六级阶梯条:达成(on)/当前(cur)/封顶不可达(cap) ----
  const ladder = h('div', { class: 'ladder', role: 'list' },
    ...data.ladder.map((lv, i) => {
      const capped = capIdx >= 0 && i > capIdx;
      return h('div', {
        class: `lv${i <= data.levelIndex ? ' on' : ''}${i === data.levelIndex ? ' cur' : ''}${capped ? ' cap' : ''}`,
        role: 'listitem',
        title: i <= data.levelIndex ? '已达成'
          : capped ? `封顶 ${data.ceiling}:${data.ceilingReason}` : '未达成',
      }, lv);
    }));

  const ceilingNote = data.ceiling ? h('div', { class: 'note is-stale', style: { marginTop: '9px' } },
    icon('warn'),
    h('span', { class: 'grow' },
      h('b', null, `证据级别封顶 ${data.ceiling}`), `:${data.ceilingReason}`)) : null;

  const reasonsNode = data.reasons.length ? h('div', { class: 'audit-reasons' },
    h('div', { class: 'k' }, '为何停在这一级'),
    ...data.reasons.map((r) => h('div', { class: 'r' }, r))) : null;

  // ---- 每步证据来源徽标(§FOLLOWUPS #11/#12 的可视化) ----
  const srcCard = h('div', { class: 'audit-src' },
    ...data.stepSources.map((s) => h('button', {
      class: 'srow', type: 'button',
      title: `${s.detail || s.source} —— 点击跳到流水线第 ${s.id} 步`,
      'aria-label': `第 ${s.id} 步 ${s.name} 证据来源 ${s.badge},点击跳到流水线`,
      onclick: gotoStep ? () => gotoStep(Number(s.id)) : null,
    },
      h('span', { class: 'no mono' }, `#${s.id}`),
      h('span', { class: 'nm' }, s.name),
      h('span', { class: `tag ${ORIGIN_TAG[s.origin] || 'is-bad'}` }, s.badge),
      h('span', { class: 'src mono' }, s.origin === 'missing' ? (s.detail || s.source) : s.source))));

  // ---- 父链验证清单(fork 场景才有;#11:只列出、不继承) ----
  const pvSection = data.parentValidations.length ? [
    h('h3', { class: 'sect' }, '父链验证清单 · 不随 fork 继承'),
    h('div', { class: 'contract' }, ...data.parentValidations.map((v) => h('div', { class: 'm' },
      h('span', { class: 'nm' }, v.name),
      h('span', { class: 'mono', style: { color: 'var(--text)' } },
        `${v.value ?? '—'}${v.unit ? ` ${v.unit}` : ''}`),
      h('span', { class: 'tag is-stale', title: v.note }, icon('warn'), 'fork 后需重新验证'),
      h('span', { class: 'src' },
        `来源 run ${shortId(v.runId)}${v.argsHash ? ` · 参数指纹 ${shortId(v.argsHash)}` : ''}`)))),
    h('p', { class: 'blurb' },
      '外部验证(GNSS/交叉验证)绑定验证当时的参数,fork 改参后不自动继承 —— ' +
      '上表仅列出父链曾验证过什么,供判断是否需要以当前参数重新验证。'),
  ] : [];

  // ---- 质量门阈值台账(/api/env thresholds;PENDING 标橙) ----
  const thr = h('div', { class: 'contract' }, ...data.thresholds.map((t) => {
    const pending = String(t.status || '').toUpperCase() === 'PENDING';
    return h('div', { class: 'm' },
      h('span', { class: 'nm' }, t.key),
      h('span', { class: 'mono', style: { color: 'var(--text)' } }, String(t.value)),
      h('span', { class: `tag is-${pending ? 'stale' : 'ok'}` }, thresholdSourceLabel(t)),
      h('span', { class: 'src' }, t.ref || ''));
  }));

  return [
    h('h3', { class: 'sect' }, `六级证据阶梯 · 当前 ${data.level}`),
    meta,
    ladder,
    ceilingNote,
    reasonsNode,
    h('h3', { class: 'sect' }, '每步证据来源'),
    srcCard,
    h('p', { class: 'blurb' },
      '来源闭集:local(本地执行)/ inherited(fork 复用,沿父链核对过指纹)/ ' +
      'cloud(云端完成,manifest 佐证)/ missing(审计缺口,封顶生效)。'),
    ...pvSection,
    h('h3', { class: 'sect' }, '质量门阈值台账'),
    thr,
    h('p', { class: 'audit-sub' },
      `实测数据 · GET /api/provenance + /api/env · ${time} 更新(缓存 30 s,「刷新」强制重拉)`),
  ];
}

/** 骨架屏:证据链到达前的占位(与 envlive.skeleton 同风格,区段贴审计面板)。 */
export function skeleton() {
  const bar = (w) => h('div', {
    'aria-hidden': 'true',
    style: {
      height: '11px', width: w, borderRadius: '4px',
      background: 'var(--border-strong)', opacity: '.35',
    },
  });
  const card = (...bars) => h('div', {
    class: 'envcard',
    style: { display: 'grid', gap: '11px', padding: '12px' },
  }, ...bars);

  return h('div', { 'aria-busy': 'true', 'aria-label': '正在读取证据链' },
    h('h3', { class: 'sect' }, '六级证据阶梯'),
    card(bar('92%'), bar('58%')),
    h('h3', { class: 'sect' }, '每步证据来源'),
    card(bar('84%'), bar('76%'), bar('88%'), bar('63%')),
    h('h3', { class: 'sect' }, '质量门阈值台账'),
    card(bar('88%'), bar('71%'), bar('80%')),
    h('p', { class: 'blurb' }, '正在读取证据链(GET /api/provenance + GET /api/env)…'));
}
