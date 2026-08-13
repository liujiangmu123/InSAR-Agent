/* ============================================================
   顶栏 LLM 用量芯片(实装 index.html 既有的 #budget 元素)
   ------------------------------------------------------------
   - 显示「今日 ¥0.03 · 12 次调用」;当日无调用回退「7 天 ¥… · n 次」;
     完全无用量保持 hidden(数据源 GET /api/llm/usage?days=7)。
   - 点击弹出小面板:7 天按日汇总表 + 按模型分解;Esc/点外关闭。
   - 60s 轮询 + document 上的 llm:config-changed 事件即时刷新。
   - 与 app.js paintBudget(demo 模式的磁盘预算)共用 #budget:磁盘
     告警更要命,它显示期间本模块让位(is-usage 类标记仲裁 ——
     paintBudget 整写 className,接管后标记自然消失)。
   - 样式纪律:index.html 的 CSP 是 style-src 'self'(禁内联 <style>),
     动态节点一律走 CSSOM 属性赋值 + 主题 token(var(--surface) 等),
     深浅主题对比度随既有 token 体系(仓库 a11y 门禁校过 AA)。
   - node(check 脚本)无 window 不自初始化;纯函数导出供直测。
   ============================================================ */

import { h } from './dom.js';

const API = '';
const POLL_MS = 60000;
export const DAYS = 7;

/* ---------------- 纯函数(usagechip.check.mjs 直测) ---------------- */

/** 本地时区的 YYYY-MM-DD(与服务端 by_day 的 localtime 口径一致)。 */
export function localDayString(d = new Date()) {
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

/** 成本 → 文案。null/非数值 → null(未知不编数);>0 但不足一分 → <¥0.01。 */
export function fmtCNY(v) {
  if (typeof v !== 'number' || !Number.isFinite(v)) return null;
  if (v > 0 && v < 0.005) return '<¥0.01';
  return `¥${v.toFixed(2)}`;
}

/** token 数 → 紧凑文案;null(未知)→ '—'。 */
export function fmtTokens(v) {
  if (typeof v !== 'number' || !Number.isFinite(v)) return '—';
  if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
  if (v >= 1e4) return `${Math.round(v / 1e3)}k`;
  if (v >= 1e3) return `${(v / 1e3).toFixed(1)}k`;
  return String(v);
}

/** 7 天窗口内有没有任何用量(无则芯片保持 hidden)。 */
export function shouldShow(summary) {
  return !!(summary && summary.total && summary.total.calls > 0);
}

/** 芯片文案:优先今日,当日零调用回退整个窗口;无用量返回 ''。
    成本未知(null)时只报次数 —— 绝不显示编造的 ¥0.00。 */
export function chipText(summary, today = localDayString()) {
  if (!shouldShow(summary)) return '';
  const t = (summary.by_day || []).find((r) => r.day === today);
  const row = t && t.calls > 0 ? t : summary.total;
  const label = t && t.calls > 0 ? '今日' : `${summary.days || DAYS} 天`;
  const cost = fmtCNY(row.cost_est_cny);
  return cost ? `${label} ${cost} · ${row.calls} 次调用`
              : `${label} ${row.calls} 次调用`;
}

/** #budget 归属仲裁:元素隐藏中(无人使用)或已是本模块的(is-usage)才可写;
    可见且不带 is-usage = 磁盘预算(app.js paintBudget)在用 → 让位。 */
export function canPaint(box) {
  return !!box && (box.hidden === true || box.classList.contains('is-usage'));
}

/* ---------------- 芯片渲染 ---------------- */

/** 把用量画进 #budget。返回是否真的画了(让位/无用量时 false)。 */
export function paintChip(box, summary, today = localDayString(), onClick = null) {
  if (!canPaint(box)) return false;
  const text = chipText(summary, today);
  if (!text) {
    if (box.classList.contains('is-usage')) {   // 用量清零(如换库):收回芯片
      box.className = 'budget';
      box.replaceChildren();
      box.hidden = true;
    }
    return false;
  }
  const btn = h('button', {
    type: 'button', id: 'llmUsageChip', class: 'btn',
    title: 'LLM 用量(计费中转站实报 token,点击看 7 天明细)',
    'aria-haspopup': 'dialog', 'aria-expanded': String(panelOpen()),
    style: { fontVariantNumeric: 'tabular-nums' },
    onclick: onClick || undefined,
  }, text);
  box.className = 'budget is-usage';
  box.title = '';
  box.replaceChildren(btn);
  box.hidden = false;
  return true;
}

/* ---------------- 明细面板 ---------------- */

let panelEl = null;
const panelOpen = () => !!panelEl;

function cell(tag, content, opts = {}) {
  return h(tag, {
    style: {
      padding: '3px 8px', textAlign: opts.num ? 'right' : 'left',
      fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap',
      color: opts.dim ? 'var(--text-3)' : 'inherit',
      fontWeight: opts.head ? '600' : '400',
    },
  }, content);
}

function table(headers, rows) {
  return h('table', { style: { borderCollapse: 'collapse', width: '100%' } },
    h('thead', null, h('tr', null,
      headers.map((t, i) => cell('th', t, { num: i > 0, head: true, dim: true })))),
    h('tbody', null, rows.map((r) => h('tr', null,
      r.map((v, i) => cell('td', v, { num: i > 0 }))))));
}

function sectionTitle(text) {
  return h('div', {
    style: { margin: '10px 0 3px', fontWeight: '600', color: 'var(--text-3)' },
  }, text);
}

/** 面板内容(纯构建,check 可在假 DOM 下调用)。 */
export function buildPanelContent(summary, today = localDayString()) {
  const cny = (v) => fmtCNY(v) || '—';
  const toks = (r) => `${fmtTokens(r.prompt_tokens)}/${fmtTokens(r.completion_tokens)}`;
  const total = summary.total || {};
  const wrap = h('div', null);
  wrap.appendChild(h('div', { style: { color: 'var(--text-3)' } },
    `近 ${summary.days || DAYS} 天合计:${total.calls || 0} 次调用 · `
    + `tokens ${fmtTokens(total.prompt_tokens)} 入 / ${fmtTokens(total.completion_tokens)} 出 · `
    + `成本 ${cny(total.cost_est_cny)}`));
  wrap.appendChild(sectionTitle('按日'));
  wrap.appendChild(table(
    ['日期', '调用', 'tokens 入/出', '成本'],
    (summary.by_day || []).map((r) => [
      r.day === today ? `${r.day}(今日)` : r.day,
      String(r.calls), toks(r), cny(r.cost_est_cny)])));
  wrap.appendChild(sectionTitle('按模型'));
  wrap.appendChild(table(
    ['模型', '调用', 'tokens 入/出', '成本'],
    (summary.by_model || []).map((r) => [
      r.model || '(未知)', String(r.calls), toks(r), cny(r.cost_est_cny)])));
  wrap.appendChild(h('div', {
    style: { marginTop: '10px', color: 'var(--text-3)', maxWidth: '340px' },
  }, '成本按中转站价格表(¥/百万 token)估算;拿不到价格或 token 计数的调用计为「—」,不编数。'));
  return wrap;
}

function closePanel(returnFocus = true) {
  if (!panelEl) return;
  panelEl.remove();
  panelEl = null;
  document.removeEventListener('keydown', onPanelKey, true);
  document.removeEventListener('pointerdown', onPanelOutside, true);
  const chip = document.getElementById('llmUsageChip');
  if (chip) {
    chip.setAttribute('aria-expanded', 'false');
    if (returnFocus) chip.focus();
  }
}

function onPanelKey(e) {
  if (e.key === 'Escape') { e.stopPropagation(); closePanel(); }
}

function onPanelOutside(e) {
  if (!panelEl) return;
  if (panelEl.contains(e.target)) return;
  const chip = document.getElementById('llmUsageChip');
  closePanel(!(chip && chip.contains(e.target)));  // 点芯片本身由 toggle 处理焦点
}

function openPanel(summary) {
  closePanel(false);
  const chip = document.getElementById('llmUsageChip');
  const rect = chip ? chip.getBoundingClientRect()
                    : { bottom: 48, right: window.innerWidth - 12 };
  panelEl = h('div', {
    id: 'llmUsagePanel', role: 'dialog', 'aria-label': 'LLM 用量明细',
    style: {
      position: 'fixed', zIndex: '460',
      top: `${Math.round(rect.bottom + 6)}px`,
      right: `${Math.max(8, Math.round(window.innerWidth - rect.right))}px`,
      minWidth: '300px', maxWidth: 'min(420px, calc(100vw - 24px))',
      maxHeight: 'min(60vh, 480px)', overflowY: 'auto',
      background: 'var(--surface)', color: 'var(--text)',
      border: '1px solid var(--border-strong)', borderRadius: 'var(--r-lg)',
      boxShadow: 'var(--sh-3)', padding: '12px 14px', fontSize: 'var(--fs-xs)',
    },
  },
    h('div', { style: { display: 'flex', alignItems: 'center', marginBottom: '4px' } },
      h('strong', { style: { flex: '1', fontSize: 'var(--fs-sm)' } }, 'LLM 用量'),
      h('button', {
        type: 'button', class: 'btn', 'aria-label': '关闭用量面板',
        onclick: () => closePanel(),
      }, '×')),
    buildPanelContent(summary));
  document.body.appendChild(panelEl);
  document.addEventListener('keydown', onPanelKey, true);
  document.addEventListener('pointerdown', onPanelOutside, true);
  if (chip) chip.setAttribute('aria-expanded', 'true');
}

/* ---------------- 数据与生命周期 ---------------- */

let lastSummary = null;

async function fetchUsage() {
  const r = await fetch(`${API}/api/llm/usage?days=${DAYS}`);
  if (!r.ok) throw new Error(`usage ${r.status}`);
  return r.json();
}

function onChipClick() {
  if (panelOpen()) { closePanel(); return; }
  if (lastSummary) openPanel(lastSummary);
}

function paint() {
  const box = document.getElementById('budget');
  if (box) paintChip(box, lastSummary, localDayString(), onChipClick);
}

async function refresh() {
  try {
    lastSummary = await fetchUsage();
  } catch {
    return; // 后端不可达(demo 模式等):保持现状,绝不打扰界面
  }
  paint();
  if (panelOpen()) openPanel(lastSummary); // 打开着就原位重建(位置随芯片)
}

function install() {
  refresh();
  setInterval(refresh, POLL_MS);
  // 模型设置保存后即时刷新(约定事件名;llmsettings 未派发时轮询兜底)
  document.addEventListener('llm:config-changed', refresh);
}

if (typeof window !== 'undefined' && typeof document !== 'undefined') {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', install, { once: true });
  } else {
    install();
  }
}
