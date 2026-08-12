/* ============================================================
   环境面板实时数据（面板 9「环境」真实化的唯一新增模块）。

   职责：
   - 拉取 GET /api/env（引擎/凭据/WSL/磁盘/CPU/内存 实测 + 阈值台账）
     与 GET /api/setup/status（首启就绪检查项），30 秒 TTL 缓存；
   - 归一化为 dock.js envView 直接消费的渲染数据结构（字段清单
     与 tests/test_env_panel_contract.py 的断言一一对应，防契约漂移）；
   - 导出「演示数据（后端未连接）」横幅与骨架屏小组件，
     供 envView / termView 在后端不可达时复用。

   失败语义：后端不可达 / file:// 打开 → resolve null，绝不抛错；
   调用方拿到 null 就回落 envdata.js 的静态演示数据。
   ============================================================ */
import { h, icon } from './dom.js';
import { S } from './state.js';

/** 缓存 TTL：环境探测是秒级操作，30s 内复用同一结果；手动刷新走 invalidate()。 */
const TTL_MS = 30_000;

let cache = { at: 0, promise: null };

/** 手动刷新入口：清缓存，下一次 fetchEnvLive() 必然重新拉取。 */
export function invalidate() {
  cache = { at: 0, promise: null };
}

async function getJson(url) {
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

/** 拉取环境实况（带 30s TTL 缓存；force=true 跳过缓存）。
    /api/env 是主数据源，失败 → 整体返回 null（回落演示数据）；
    /api/setup/status 是辅数据源，单独失败只让 setup 字段为 null，不拖垮面板。 */
export function fetchEnvLive({ force = false } = {}) {
  if (location.protocol === 'file:') return Promise.resolve(null);  // 与 backend.sse.js 同判据
  if (!force && cache.promise && Date.now() - cache.at < TTL_MS) return cache.promise;

  const promise = (async () => {
    try {
      const [env, setup] = await Promise.all([
        getJson(`/api/env?session=${encodeURIComponent(S.sessionId)}`),
        getJson('/api/setup/status').catch(() => null),
      ]);
      return normalize(env, setup);
    } catch {
      cache = { at: 0, promise: null };  // 失败不占缓存位：下次渲染立即重试
      return null;
    }
  })();
  cache = { at: Date.now(), promise };
  return promise;
}

/* ============================================================
   归一化：后端响应 → 渲染数据结构
   ============================================================ */

/** 引擎键 "mintpy (wsl)" → 名称 + 宿主来源；值为版本串 / "present" / null（缺失）。 */
function parseEngine(key, ver) {
  const m = key.match(/^(.+?)\s*\(wsl\)$/);
  return {
    key,
    name: m ? m[1] : key,
    host: m ? 'wsl' : 'local',
    ok: ver !== null && ver !== undefined,
    ver: ver || '',
  };
}

/** 凭据 id → 面板显示文案（probe.py 的 _CREDENTIALS 键）。 */
const CRED_LABEL = {
  earthdata: 'Earthdata · ASF 数据下载',
  cds: 'CDS · ERA5 气象再分析',
  gacos: 'GACOS · 对流层校正',
};

/** 阈值来源（audit/contract.py 的 VALID_SOURCES）→ 台账标签。 */
const THR_SOURCE_LABEL = {
  upstream_default: 'A 上游默认',
  literature: 'B 文献来源',
  local_calibration: 'C 本地标定',
};

export function thresholdSourceLabel(t) {
  if ((t.status || '').toUpperCase() === 'PENDING') return '⚠ PENDING 未标定';
  return THR_SOURCE_LABEL[t.source] || t.source || 'A 上游默认';
}

/** /api/env + /api/setup/status → envView 消费的结构。
    这里读取的每一个后端字段都在 tests/test_env_panel_contract.py 里有对应断言。 */
function normalize(env, setup) {
  const probe = env.probe || {};
  const engines = Object.entries(probe.engines || {})
    .map(([key, ver]) => parseEngine(key, ver));
  const credentials = Object.entries(probe.credentials || {})
    .map(([id, ok]) => ({ id, ok: !!ok, label: CRED_LABEL[id] || id }));

  // WSL 合并探测元数据（wsl_probe.merge_wsl_probe 挂载；无该键 = 本次未探测）
  const ep = (probe.wsl || {}).engine_probe || null;

  return {
    engines,
    engineOk: engines.filter((e) => e.ok).length,
    engineMissing: engines.filter((e) => !e.ok).length,
    credentials,
    wsl: {
      probed: !!ep,
      ok: !!(ep && ep.ok),
      distro: ep ? ep.distro : null,
      error: ep ? ep.error : null,
      enginePrefix: ep ? ep.engine_prefix : null,
    },
    host: {
      python: probe.python || '',
      platform: probe.platform || '',
      cpuCount: probe.cpu_count ?? null,
      memGb: probe.mem_gb ?? null,
      diskFreeGb: probe.disk_free_gb ?? null,
      diskTotalGb: probe.disk_total_gb ?? null,
    },
    thresholds: env.thresholds || [],
    setup: setup ? {
      ready: !!setup.ready,
      checks: (setup.checks || []).map((c) => ({
        key: c.key, ok: !!c.ok, message: c.message,
        fixHint: c.fix_hint || '', required: !!c.required,
      })),
      dataSource: setup.data ? setup.data.source : null,
      pairCount: setup.data ? setup.data.pair_count : 0,
      enginePrefix: setup.engine ? setup.engine.prefix : null,
    } : null,
    fetchedAt: Date.now(),
  };
}

/* ============================================================
   共用小组件（envView / termView 的加载与回落状态）
   ============================================================ */

/** 「演示数据（后端未连接）」醒目横幅：置于回落内容顶部。
    onRetry 传入时带「重试」按钮（清缓存 + 面板重渲染由调用方负责）。 */
export function demoBanner(text, { onRetry } = {}) {
  return h('div', {
    class: 'note is-stale',
    role: 'status',
    style: {
      display: 'flex', alignItems: 'center', gap: '7px',
      marginBottom: '10px', borderStyle: 'dashed',
    },
  },
    icon('warn'),
    h('span', { style: { flex: '1' } },
      h('b', null, '演示数据（后端未连接）'),
      text ? ` ${text}` : ''),
    onRetry ? h('button', {
      class: 'btn btn-gho btn-sm', type: 'button',
      'aria-label': '重试连接后端', onclick: onRetry,
    }, icon('refresh'), '重试') : null);
}

/** 骨架屏：实测数据到达前的占位（灰条示意布局，不闪不跳）。 */
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

  return h('div', { 'aria-busy': 'true', 'aria-label': '正在探测环境' },
    h('h3', { class: 'sect' }, '运行环境'),
    card(bar('52%'), bar('84%'), bar('67%'), bar('90%'), bar('74%')),
    h('h3', { class: 'sect' }, '磁盘 / CPU / 内存'),
    card(bar('88%'), bar('46%'), bar('61%')),
    h('h3', { class: 'sect' }, '就绪检查'),
    card(bar('70%'), bar('83%'), bar('57%')),
    h('p', { class: 'blurb' }, '正在探测环境（GET /api/env + GET /api/setup/status）…'));
}
