/* ============================================================
   visionqa.js —— 影像面板图件卡的「AI 识图质检」
   识图模型(vision_model)审图件:解缠跳变/失相干/参考点/色标问题初筛。

   所有权边界(不改 gallery.js / figures.js,自初始化且幂等,
   与 figcompare.js 同一注入范式):
   - 注入:MutationObserver 观察 .glx-card 出现,把质检徽章(chip)幂等
     挂进卡片缩略图角落;已有 .aiqa.json 的图自动显示 pass/warn/fail 徽章
     (GET /api/vision-qa,按 session+run 只拉一次);
   - 事件委托:document 捕获阶段接管 chip 的点击/回车(stopPropagation
     抢在 gallery 卡片 onclick 开灯箱之前);
   - 数据:图名读卡片 .glx-name 的 title(与网格同源),session/run_id 从
     缩略图 /api/artifact-file URL 反解 —— 不依赖 state.js,不碰全局状态;
   - 执行:POST /api/vision-qa {session, run_id, figure} → 进行中态 →
     结果徽章 + 结果面板(findings 列表);未配置识图模型/后端不可达/
     图件超限,面板内展示错误文案,徽章降级为「质检失败」,绝不打断画廊;
   - 演示图件(.glx-demo)无真实产物:不注入 chip。
   纯函数(徽章映射/闭集标签/清单索引/URL 反解)全部导出,
   由 prototype/visionqa.check.mjs 无浏览器直测。
   ============================================================ */
import { h } from './dom.js';

/* ================= 纯函数(导出供 check.mjs 直测) ================= */

export const VERDICTS = ['pass', 'warn', 'fail'];

const BADGE = {
  pass: { cls: 'is-pass', label: 'AI 通过' },
  warn: { cls: 'is-warn', label: 'AI 警告' },
  fail: { cls: 'is-fail', label: 'AI 不通过' },
};

/** verdict → 徽章渲染模型 {cls,label};越界值返回 null(降级:不渲染徽章)。 */
export function badgeFor(verdict) {
  return BADGE[verdict] || null;
}

/** 检查项闭集 → 中文标签(与 audit/vision_qa.CHECK_ITEMS 同一张表)。 */
export const ISSUE_LABEL = {
  unwrap_jump: '解缠跳变/条纹不连续',
  decorrelation: '失相干大区块/噪声',
  reference_point: '参考点异常',
  colorbar_scale: '色标与量纲问题',
  processing_artifact: '异常纹理/处理伪影',
};

/** issue id → 中文;未知 id 原样返回(后端已闭集校验,这里只是显示防御)。 */
export function issueLabel(id) {
  return ISSUE_LABEL[id] || String(id || '');
}

export const SEV_LABEL = { critical: '严重', warn: '警告', info: '提示' };
const SEV_RANK = { critical: 0, warn: 1, info: 2 };

export function sevLabel(sev) {
  return SEV_LABEL[sev] || String(sev || '');
}

/** findings 排序(critical 在前),剔除坏形状项;非数组返回 []。 */
export function sortFindings(findings) {
  if (!Array.isArray(findings)) return [];
  return findings
    .filter((f) => f && typeof f === 'object' && f.issue)
    .slice()
    .sort((a, b) => (SEV_RANK[a.severity] ?? 9) - (SEV_RANK[b.severity] ?? 9));
}

/** 缩略图 /api/artifact-file URL → {session, runId};非产物 URL → null。 */
export function parseArtifactQuery(src, base = 'http://localhost/') {
  try {
    const u = new URL(src, base);
    if (!u.pathname.endsWith('/api/artifact-file')) return null;
    const session = u.searchParams.get('session');
    if (!session) return null;
    return { session, runId: u.searchParams.get('run_id') || '' };
  } catch {
    return null;
  }
}

/** 质检结果缓存键(图名是网格与清单共同的稳定键)。 */
export function keyOf(ctx, name) {
  return `${ctx.session}|${ctx.runId}|${name}`;
}

/** GET /api/vision-qa 响应 → Map(图名 → 记录);坏形状/越界 verdict 剔除。 */
export function indexReviews(data) {
  const map = new Map();
  if (!data || !Array.isArray(data.items)) return map;
  for (const it of data.items) {
    if (it && typeof it === 'object' && typeof it.figure === 'string'
        && badgeFor(it.verdict)) {
      map.set(it.figure, it);
    }
  }
  return map;
}

/** ISO 时间 → 本地「YYYY-MM-DD HH:MM」;解析失败原样返回(不出 NaN)。 */
export function fmtTime(iso) {
  const d = new Date(iso || '');
  if (Number.isNaN(d.getTime())) return String(iso || '');
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
    + ` ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/* ============================================================
   以下为 DOM 侧(浏览器专用;bare node import 因 document 守卫不触达)
   ============================================================ */

const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

let booted = false;
const results = new Map();     // key → 结果记录 或 {error}
const busy = new Set();        // 质检请求进行中的 key
const loadedRuns = new Set();  // 已拉过 .aiqa.json 清单的 `${session}|${runId}`
let panel = null;              // 全局至多一个结果面板 { root, ctx, body, returnFocus }

/** 卡片 → {session, runId, name};演示图件/无产物 URL 返回 null(不注入)。 */
function cardCtx(card) {
  if (card.querySelector('.glx-demo')) return null;
  const img = card.querySelector('.glx-thumb img');
  const src = (img && img.getAttribute('src')) || '';
  const q = parseArtifactQuery(src, location.href);
  if (!q) return null;
  const nameEl = card.querySelector('.glx-name');
  const name = (nameEl && (nameEl.getAttribute('title') || nameEl.textContent)) || '';
  return name ? { session: q.session, runId: q.runId, name } : null;
}

function chipState(key) {
  if (busy.has(key)) return { cls: 'is-busy', label: '质检中…' };
  const rec = results.get(key);
  if (rec && !rec.error) {
    const b = badgeFor(rec.verdict);
    if (b) return b;
  }
  if (rec && rec.error) return { cls: 'is-err', label: '质检失败' };
  return { cls: 'is-idle', label: 'AI 质检' };
}

const CHIP_TITLE = {
  'is-idle': 'AI 识图质检:让识图模型审这张图(解缠跳变/失相干/参考点/色标)',
  'is-busy': '识图模型审阅中…',
  'is-err': 'AI 质检失败(点击查看原因并重试)',
};

/* 注意:MutationObserver 只观察 childList,本函数的 DOM 写入全部
   「先比对再写」,第二遍扫描必然零变更,不会自激振荡。 */
function ensureChips() {
  for (const card of $$('.glx .glx-card')) {
    const ctx = cardCtx(card);
    let chip = card.querySelector('.vqa-chip');
    if (!ctx) {
      if (chip) chip.remove();
      continue;
    }
    const key = keyOf(ctx, ctx.name);
    if (!chip) {
      chip = h('span', { class: 'vqa-chip is-idle', role: 'button', tabindex: '0' });
      (card.querySelector('.glx-thumb') || card).appendChild(chip);
    }
    const st = chipState(key);
    const cls = `vqa-chip ${st.cls}`;
    if (chip.className !== cls) chip.className = cls;
    if (chip.textContent !== st.label) chip.textContent = st.label;
    const title = CHIP_TITLE[st.cls] || 'AI 识图质检结果(点击展开发现列表)';
    if (chip.getAttribute('title') !== title) chip.setAttribute('title', title);
    const aria = `AI 识图质检:${ctx.name},${st.label}`;
    if (chip.getAttribute('aria-label') !== aria) chip.setAttribute('aria-label', aria);
    chip.setAttribute('aria-busy', String(st.cls === 'is-busy'));
    preloadRun(ctx);
  }
}

/** 已落盘结果预取:同一 session+run 只拉一次,失败静默(点击时再暴露错误)。 */
async function preloadRun(ctx) {
  const rk = `${ctx.session}|${ctx.runId}`;
  if (loadedRuns.has(rk)) return;
  loadedRuns.add(rk);
  if (location.protocol === 'file:') return;
  try {
    const qs = new URLSearchParams({ session: ctx.session });
    if (ctx.runId) qs.set('run', ctx.runId);
    const resp = await fetch(`/api/vision-qa?${qs}`);
    if (!resp.ok) return;
    const idx = indexReviews(await resp.json());
    for (const [name, rec] of idx) {
      const k = keyOf(ctx, name);
      if (!busy.has(k)) results.set(k, rec);
    }
    if (idx.size) ensureChips();
  } catch { /* 后端不可达:保持「AI 质检」按钮态,不打断画廊 */ }
}

/** 发起质检:进行中态 → POST → 结果/错误入缓存 → 徽章与面板同步刷新。 */
async function runReview(ctx) {
  const key = keyOf(ctx, ctx.name);
  if (busy.has(key)) return;
  busy.add(key);
  results.delete(key);
  ensureChips();
  renderPanel();
  let rec;
  try {
    const resp = await fetch('/api/vision-qa', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session: ctx.session, run_id: ctx.runId || null, figure: ctx.name,
      }),
    });
    if (resp.ok) {
      const data = await resp.json();
      rec = (data && data.ok && data.review) ? data.review
        : { error: (data && data.error) || '质检返回了无法识别的结果' };
    } else {
      rec = { error: `质检请求失败(HTTP ${resp.status})` };
    }
  } catch {
    rec = { error: '后端不可达:无法执行 AI 质检' };
  }
  busy.delete(key);
  results.set(key, rec);
  ensureChips();
  renderPanel();
}

/* ---------------- 结果面板(全局至多一个) ---------------- */

function closePanel() {
  if (!panel) return;
  panel.root.remove();
  const rf = panel.returnFocus;
  panel = null;
  if (rf && rf.isConnected) rf.focus?.();
}

function trapTab(e, root) {
  const items = $$('button:not([disabled]), [href], [tabindex="0"]', root);
  if (!items.length) { e.preventDefault(); return; }
  const first = items[0];
  const last = items[items.length - 1];
  if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  else if (!root.contains(document.activeElement)) { e.preventDefault(); first.focus(); }
}

function openPanel(ctx, returnFocus) {
  closePanel();
  const body = h('div', { class: 'vqa-body' });
  const root = h('div', {
    class: 'vqa-ov', role: 'dialog', 'aria-modal': 'true', tabindex: '-1',
    'aria-label': `AI 识图质检:${ctx.name}`,
    onclick: (e) => { if (e.target === root) closePanel(); },
    onkeydown: (e) => {
      if (e.key === 'Escape') {
        // 吞掉冒泡:app.js 的 document 级 Esc(停止 agent)不受面板影响
        e.preventDefault();
        e.stopPropagation();
        closePanel();
        return;
      }
      if (e.key === 'Tab') trapTab(e, root);
    },
  },
    h('div', { class: 'vqa-card' },
      h('div', { class: 'vqa-head' },
        h('span', { class: 'vqa-title' }, 'AI 识图质检'),
        h('span', { class: 'vqa-fig', title: ctx.name }, ctx.name),
        h('span', { class: 'vqa-grow' }),
        h('button', {
          class: 'vqa-btn vqa-close', type: 'button',
          'aria-label': '关闭(Esc)', onclick: () => closePanel(),
        }, '关闭 Esc')),
      body));
  panel = { root, ctx, body, returnFocus: returnFocus || document.activeElement };
  document.body.appendChild(root);
  renderPanel();
  root.querySelector('.vqa-close')?.focus();
  const key = keyOf(ctx, ctx.name);
  if (!results.has(key) && !busy.has(key)) runReview(ctx);   // 首次打开即发起
}

function findingsList(rec) {
  const items = sortFindings(rec.findings);
  if (!items.length) {
    return h('p', { class: 'vqa-empty' }, '未发现问题(检查项:解缠跳变 · 失相干 · 参考点 · 色标 · 伪影)。');
  }
  return h('ul', { class: 'vqa-list' }, ...items.map((f) => h('li', {
    class: `vqa-find is-${f.severity}`,
  },
    h('span', { class: 'vqa-sev' }, sevLabel(f.severity)),
    h('span', { class: 'vqa-issue' }, issueLabel(f.issue)),
    f.detail ? h('span', { class: 'vqa-detail' }, f.detail) : null)));
}

function renderPanel() {
  if (!panel) return;
  const { ctx, body } = panel;
  const key = keyOf(ctx, ctx.name);
  const again = (label) => h('button', {
    class: 'vqa-btn vqa-again', type: 'button',
    title: '重新调用识图模型审这张图(覆盖已落盘的 .aiqa.json)',
    onclick: () => runReview(ctx),
  }, label);

  if (busy.has(key)) {
    body.replaceChildren(h('p', { class: 'vqa-busy', role: 'status' },
      h('span', { class: 'vqa-spin', 'aria-hidden': 'true' }),
      '识图模型审阅中…(流式返回,通常十几秒)'));
    return;
  }
  const rec = results.get(key);
  if (!rec) {
    body.replaceChildren(h('p', { class: 'vqa-empty' }, '尚未质检。'), again('开始质检'));
    return;
  }
  if (rec.error) {
    body.replaceChildren(
      h('p', { class: 'vqa-error', role: 'alert' }, rec.error),
      again('重试'));
    return;
  }
  const b = badgeFor(rec.verdict) || { cls: 'is-err', label: String(rec.verdict || '?') };
  body.replaceChildren(
    h('div', { class: 'vqa-verdict' },
      h('span', { class: `vqa-badge ${b.cls}` }, b.label),
      rec.kind ? h('span', { class: 'vqa-kind' }, `图件类型:${rec.kind}`) : null),
    rec.summary ? h('p', { class: 'vqa-summary' }, rec.summary) : null,
    findingsList(rec),
    h('div', { class: 'vqa-foot' },
      h('span', { class: 'vqa-meta' },
        `模型 ${rec.model || '?'}${rec.created_at ? ` · ${fmtTime(rec.created_at)}` : ''}`
        + ' · 结论仅供参考,以人工复核为准'),
      again('重新质检')));
}

/* ---------------- 捕获阶段事件委托 ---------------- */

function chipTarget(e) {
  return (e.target && e.target.closest) ? e.target.closest('.vqa-chip') : null;
}

function activate(chip) {
  const card = chip.closest('.glx-card');
  const ctx = card && cardCtx(card);
  if (ctx) openPanel(ctx, chip);
}

function onDocClick(e) {
  const chip = chipTarget(e);
  if (!chip) return;
  e.preventDefault();
  e.stopPropagation();   // 抢在 gallery 卡片 onclick(开灯箱)之前
  activate(chip);
}

function onDocKey(e) {
  if (e.key !== 'Enter' && e.key !== ' ') return;
  const chip = chipTarget(e);
  if (!chip || e.target !== chip) return;
  e.preventDefault();
  e.stopPropagation();
  activate(chip);
}

/* ---------------- 自初始化(幂等) ---------------- */

export function initVisionQA() {
  if (booted || typeof document === 'undefined') return;
  booted = true;
  document.addEventListener('click', onDocClick, true);
  document.addEventListener('keydown', onDocKey, true);
  const boot = () => {
    if (typeof MutationObserver === 'function' && document.body) {
      new MutationObserver(ensureChips).observe(document.body, {
        childList: true, subtree: true,
      });
    }
    ensureChips();
  };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot, { once: true });
  } else {
    boot();
  }
}

if (typeof document !== 'undefined') initVisionQA();
