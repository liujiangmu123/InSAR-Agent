/* ============================================================
   文件面板实时数据（files 面板真实化的唯一新增模块，
   UI-DETAILS-AUDIT-2026-08-12 第 2 条）。

   职责：
   - 拉取 GET /api/artifacts（按步骤分组的真实产物记录：art_id / 相对路径 /
     kind / policy / 三段指纹 / size / mtime / exists），30 秒 TTL 缓存，
     缓存与刷新语义对齐 envlive.js；
   - 渲染产物树（步骤分组 → 产物行：kind 图标 / 名称 / 指纹短哈希（悬停
     见完整指纹）/ 人性化大小 / 修改时间），点击行展开详情卡（三段指纹
     policy:algo:digest 逐段解释 / 所属步骤与方法 / 图像类可跳影像面板）；
   - 导出 highlightStep(stepId) 给 dock.selectStepFile 联动：切到 files
     面板后滚动到该步骤产物组并高亮一闪（chip 联动分支只调用不实现）。

   失败语义：后端不可达 / file:// 打开 / 会话无 run → resolve null，绝不
   抛错；调用方（dock.js filesView）拿到 null 回落 fileTree() 演示数据。
   ============================================================ */
import { h, icon } from './dom.js';
import { S } from './state.js';
import { activeRunId } from './runswitch.js';   // run 历史切换器:选中历史 run 时透传 run_id
import * as ES from './emptystate.js';          // states 接入:空态/骨架统一构造器

// 演示回落横幅与 envlive 共用同一款（文案由调用方给），避免两套样式漂移
export { demoBanner } from './envlive.js';

/** 缓存 TTL：产物清单读 DB + 逐文件 stat，秒级；30s 内复用，手动刷新走 invalidate()。 */
const TTL_MS = 30_000;

let cache = { at: 0, runId: null, promise: null };

/** 手动刷新入口：清缓存，下一次 fetchFilesLive() 必然重新拉取。 */
export function invalidate() {
  cache = { at: 0, runId: null, promise: null };
}

/** 拉取真实产物清单（带 30s TTL 缓存；force=true 跳过缓存）。
    返回 null = 无真实数据可展示（后端不可达 / file:// / 会话还没有 run），
    调用方以此回落演示；run 存在但产物为空是真实状态，照常返回渲染。
    runId 可选（run 历史切换器接线，runswitch.js）：缺省取 activeRunId()，
    非空 → /api/artifacts 带 run_id 查看历史 run；缓存按 run 区分，
    null（最新）行为与接线前完全一致。 */
export function fetchFilesLive({ force = false, runId = activeRunId() } = {}) {
  if (location.protocol === 'file:') return Promise.resolve(null);  // 与 backend.sse.js 同判据
  if (!force && cache.promise && cache.runId === (runId || null)
      && Date.now() - cache.at < TTL_MS) return cache.promise;

  const promise = (async () => {
    try {
      const qs = new URLSearchParams({ session: S.sessionId });
      if (runId) qs.set('run_id', runId);
      const resp = await fetch(`/api/artifacts?${qs}`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      if (!data.run) return null;               // 会话无 run：回落演示数据
      return normalize(data);
    } catch {
      cache = { at: 0, runId: null, promise: null };  // 失败不占缓存位：下次渲染立即重试
      return null;
    }
  })();
  cache = { at: Date.now(), runId: runId || null, promise };
  return promise;
}

/* ============================================================
   归一化与纯函数（渲染数据结构；后端字段清单由
   tests/test_artifacts_api.py 钉死，这里逐字段读取）
   ============================================================ */

/** 三段指纹 "<policy>:<algo>:<digest>" → 逐段拆解（坏数据容错：缺段给空串）。 */
export function splitFp(fp) {
  const [policy = '', algo = '', ...rest] = String(fp || '').split(':');
  return { policy, algo, digest: rest.join(':') };
}

/** 行内展示用短哈希：digest 前 10 位（完整指纹悬停 title / 详情卡可见）。 */
export function shortDigest(fp) {
  const { digest } = splitFp(fp);
  return digest ? `${digest.slice(0, 10)}…` : '—';
}

/** 字节数 → 人性化大小；null（目录型 / 未记录）→ '—'。 */
export function humanSize(n) {
  if (n === null || n === undefined) return '—';
  if (n < 1024) return `${n} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let v = n, i = -1;
  do { v /= 1024; i += 1; } while (v >= 1024 && i < units.length - 1);
  return `${v >= 100 ? Math.round(v) : v.toFixed(1)} ${units[i]}`;
}

/** epoch 秒 → 本地完整时间（详情卡）；null → '—'。 */
export function fmtMtime(sec) {
  if (sec === null || sec === undefined) return '—';
  return new Date(sec * 1000).toLocaleString('zh-CN', { hour12: false });
}

/** epoch 秒 → 行内短时间 MM-DD HH:mm。 */
export function fmtMtimeShort(sec) {
  if (sec === null || sec === undefined) return '—';
  const d = new Date(sec * 1000);
  const p = (x) => String(x).padStart(2, '0');
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** 图像类判定（「在影像面板查看」按钮的开关）：FIGURE 产物或图像扩展名。 */
export function isImageArtifact(a) {
  return a.kind === 'FIGURE' || /\.(png|jpe?g|gif|webp)$/i.test(a.path || '');
}

/** kind → dom.js 图标名（registry/kinds.py 的闭集；未知 kind 回落通用文件）。 */
const KIND_ICON = {
  FIGURE: 'image', REPORT: 'doc', CONFIG: 'list', PROVENANCE: 'shield',
  TIMESERIES: 'chart', VELOCITY: 'chart',
  SLC: 'grid', RSLC: 'grid', DEM: 'grid', IFG_WRAPPED: 'grid', IFG_UNWRAPPED: 'grid',
};

/** 指纹策略段说明（core/filehash.py 三档语义,§1.3/§5.5）。 */
export const POLICY_DOC = {
  content: '内容档 — 全文件字节流式哈希，任何字节变化都会改变指纹（最终成果）',
  stat: 'stat 档 — 文件大小 + 修改时间（纳秒），快速检测覆写（中间产物）',
  path: '路径档 — 仅路径身份，内容不参与（只增不改的原始数据）',
};

/** 指纹算法段说明（filehash._ALGO：content→sha256，path/stat→结构哈希 v1）。 */
export const ALGO_DOC = {
  sha256: 'SHA-256 内容摘要算法',
  v1: '结构哈希格式 v1（路径 / stat 元组规范化后哈希）',
};

/** /api/artifacts 响应 → 渲染数据结构。 */
export function normalize(data) {
  const steps = (data.steps || []).map((s) => ({
    stepId: s.stepId,
    name: s.name,
    method: s.method,
    artifacts: (s.artifacts || []).map((a) => ({
      artId: a.artId, path: a.path, kind: a.kind, policy: a.policy,
      fp: a.fp, size: a.size, mtime: a.mtime, exists: a.exists !== false,
      name: String(a.path || '').split('/').pop() || a.artId,
      fpParts: splitFp(a.fp),
      image: isImageArtifact(a),
    })),
  }));
  return {
    run: data.run,
    steps,
    total: steps.reduce((n, s) => n + s.artifacts.length, 0),
    fetchedAt: Date.now(),
  };
}

/* ============================================================
   面板联动：selectStepFile(stepId)（dock.js）→ 高亮该步骤产物组
   ============================================================ */

let lastTree = null;      // 最近一次渲染的产物树容器
let pendingStep = null;   // 树未渲染完成（拉取中）时暂存的联动目标

function applyHighlight(rootEl, stepId) {
  const grp = rootEl.querySelector(`[data-step-group="${stepId}"]`);
  if (!grp) return;       // 该步骤暂无产物组：只切面板，不高亮
  grp.scrollIntoView({ block: 'center' });
  // 复用全局 termHit 关键帧（dock.css）做落点一闪，不新增样式表
  grp.style.animation = 'none';
  void grp.offsetWidth;
  grp.style.animation = 'termHit 1.2s ease-out';
}

/** 滚动并高亮某步骤的产物组。树已在页上直接生效；
    拉取中则暂存目标，待 liveBody 渲染完成后消费。 */
export function highlightStep(stepId) {
  if (lastTree && lastTree.isConnected) {
    applyHighlight(lastTree, stepId);
    return;
  }
  pendingStep = stepId;
}

/* ============================================================
   渲染（dock.js filesView 消费；数据到达前用 skeleton() 占位）
   ============================================================ */

// 展开的详情卡（"stepId:artId"）：模块级保存，重渲染/切面板后仍保持展开态
const filesUI = { openKey: null };

/** 骨架屏：产物树到达前的占位。states 接入:委托统一构造器(微光 + reduced-motion 降级)。 */
export function skeleton() {
  return h('div', { 'aria-busy': 'true', 'aria-label': '正在读取产物清单' },
    h('h3', { class: 'sect' }, '数据与产物'),
    ES.renderSkeleton(null, { kind: 'tree', rows: 7, label: '正在读取产物清单（GET /api/artifacts）' }),
    h('p', { class: 'blurb' }, '正在读取产物清单（GET /api/artifacts）…'));
}

/** 真实产物树。data 来自 fetchFilesLive()；hooks：
    onRefresh —— 清缓存并触发面板重渲染（dock 提供）；
    openImages —— 切到影像面板（dock.openImages，图像类详情卡用）。 */
export function liveBody(data, { onRefresh, openImages } = {}) {
  const time = new Date(data.fetchedAt).toLocaleTimeString('zh-CN', { hour12: false });

  const head = h('div', { class: 'envcard' },
    h('div', { class: 'erow' },
      h('span', { class: 'k' }, 'run'),
      h('span', { class: 'v mono', style: { wordBreak: 'break-all' } }, data.run),
      onRefresh ? h('button', {
        class: 'btn btn-gho btn-sm', type: 'button',
        'aria-label': '重新拉取产物清单（跳过 30 秒缓存）',
        onclick: onRefresh,
      }, icon('refresh'), '刷新') : null),
    h('div', { class: 'sub' },
      `实测数据 · GET /api/artifacts · ${time} 更新（缓存 30 s，「刷新」强制重拉）` +
      ` · 共 ${data.total} 个产物`));

  if (!data.total) {
    return [
      h('h3', { class: 'sect' }, '数据与产物 · 真实 run 记录'),
      head,
      // states 接入:无数据分支 → 空态卡(run 存在但还没有产物记录)
      ES.renderEmpty(null, { icon: 'folder', title: '还没有产物记录',
        hint: '步骤完成产物发现（COLLECTED）即可生成——路径、大小与三段指纹在此可查。' }),
    ];
  }

  const tree = h('div', null);
  lastTree = tree;

  const key = (s, a) => `${s.stepId}:${a.artId}`;

  const detailCard = (s, a) => {
    const seg = a.fpParts;
    const kv = (k, v, mono = false) => h('div', { class: 'erow' },
      h('span', { class: 'k' }, k),
      h('span', {
        class: `v${mono ? ' mono' : ''}`,
        style: mono ? { wordBreak: 'break-all', fontSize: '10.5px' } : null,
      }, v));
    return h('div', { class: 'envcard', style: { margin: '2px 0 7px' } },
      kv('路径', a.path, true),
      kv('所属', `第 ${s.stepId} 步 ${s.name} · 方法 ${s.method || '—'}`),
      kv('大小', a.size === null || a.size === undefined
        ? '—（目录型产物或未记录）' : `${humanSize(a.size)}（${a.size} 字节）`),
      kv('修改时间', fmtMtime(a.mtime)),
      h('div', { class: 'sub' }, '三段指纹 policy:algo:digest —— 逐段含义：'),
      kv('策略', `${seg.policy || '—'} · ${POLICY_DOC[seg.policy] || '未知策略（按原样展示）'}`),
      // 目录型产物没有「仅路径」档：声明 path 会被升为 stat（filehash §5.5），如实标注
      a.policy && a.policy !== seg.policy
        ? kv('声明策略', `${a.policy}（目录型产物无「仅路径」档，实际按 ${seg.policy} 档计算）`) : null,
      kv('算法', `${seg.algo || '—'} · ${ALGO_DOC[seg.algo] || '未知算法（按原样展示）'}`),
      kv('摘要', seg.digest || '—', true),
      !a.exists ? h('div', { class: 'sub' },
        '⚠ 文件已缺失（记录保留）——「产物被删」按正常指纹差异处理，重跑该步可再生。') : null,
      a.image && openImages ? h('div', { class: 'sub' },
        h('button', {
          class: 'btn btn-gho btn-sm', type: 'button',
          'aria-label': '在影像面板查看该产物',
          onclick: openImages,
        }, icon('image'), '在影像面板查看')) : null);
  };

  const artifactRow = (s, a) => {
    const open = filesUI.openKey === key(s, a);
    return h('button', {
      class: 'row', type: 'button',
      'aria-expanded': String(open),
      title: a.fp,                                    // 完整三段指纹：悬停可见
      onclick: () => { filesUI.openKey = open ? null : key(s, a); renderTree(); },
    },
      h('span', { class: 'ic' }, icon(KIND_ICON[a.kind] || 'file')),
      h('span', { class: 'nm' }, a.name),
      h('span', { class: 'hs' }, shortDigest(a.fp)),
      h('span', { class: 'mt' }, humanSize(a.size)),
      h('span', { class: 'mt' }, fmtMtimeShort(a.mtime)),
      a.exists
        ? h('span', { class: 'led', style: { background: 'var(--ok)' }, title: '文件在盘' })
        : h('span', { class: 'led', style: { background: 'var(--border-strong)' }, title: '文件已缺失' }));
  };

  const groupNode = (s) => {
    const rows = [];
    for (const a of s.artifacts) {
      rows.push(artifactRow(s, a));
      if (filesUI.openKey === key(s, a)) rows.push(detailCard(s, a));
    }
    return h('div', { dataset: { stepGroup: s.stepId }, style: { borderRadius: '6px' } },
      h('div', {
        style: {
          display: 'flex', alignItems: 'baseline', gap: '6px',
          padding: '8px 4px 3px', fontSize: '11.5px', fontWeight: '600',
        },
      },
        h('span', { class: 'mono', style: { color: 'var(--text-3)' } }, `#${s.stepId}`),
        h('span', null, s.name),
        h('span', { class: 'mono', style: { color: 'var(--text-3)', fontWeight: '400' } },
          `${s.method || '—'} · ${s.artifacts.length} 个产物`)),
      h('div', { class: 'rows' }, ...rows));
  };

  const renderTree = () => tree.replaceChildren(...data.steps.map(groupNode));
  renderTree();

  // 联动目标在拉取期间到达：渲染完成、树挂载后消费（scrollIntoView 需在页上）
  if (pendingStep !== null) {
    const sid = pendingStep;
    pendingStep = null;
    setTimeout(() => { if (tree.isConnected) applyHighlight(tree, sid); }, 0);
  }

  return [
    h('h3', { class: 'sect' }, '数据与产物 · 真实 run 记录'),
    head,
    tree,
    h('h3', { class: 'sect' }, '指纹'),
    h('p', { class: 'blurb' },
      '每个产物携带三段指纹 policy:algo:digest（artifacts.fp，§6.1）：策略与算法显式入档，' +
      '策略/算法升级后旧档一眼可辨，不会被误当同域比较。点击产物行可逐段查看解释。'),
  ];
}
