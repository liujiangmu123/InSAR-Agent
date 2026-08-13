/* ============================================================
   一键体检面板(自初始化,挂在「环境」tab 底部)。

   不改 dock.js:env pane 每次切换都被 dock.js 整体重建
   (pane.replaceChildren),本模块用 MutationObserver 在重建后把
   体检区(单例节点,保留上次结果)重新挂回 pane 底部。

   数据源:GET /api/doctor(insar_agent/api/doctor_router.py;
   后端做单飞防抖 + 20s 超时,前端连点无害)。
   离线(file:// 或后端不可达)→ 醒目提示,绝不抛错。
   ============================================================ */
import { h, icon } from './dom.js';

const PANE_ID = 'pane-env';

/* 状态 → 文案/图标(语义三重表达:颜色 + 图标 + 文字,对齐 tokens.css 原则) */
const STATUS_META = {
  ok:   { label: '正常', ic: 'check' },
  warn: { label: '注意', ic: 'warn' },
  fail: { label: '异常', ic: 'x' },
};

let section = null;   // 单例节点:env 面板重建后原样挂回,上次结果不丢
let running = false;

function statusTag(status) {
  const m = STATUS_META[status] || STATUS_META.fail;
  return h('span', { class: `doctor-tag is-${status}` }, icon(m.ic, 11), m.label);
}

/* 运行中骨架:灰条占位,不闪不跳(与 envlive.skeleton 同风格,不引它避免耦合) */
function skeleton() {
  const bar = (w) => h('div', { class: 'doctor-skel-bar', style: { width: w } });
  return h('div', { class: 'doctor-skel', 'aria-busy': 'true', 'aria-label': '体检进行中' },
    bar('46%'), bar('88%'), bar('72%'), bar('81%'), bar('58%'),
    h('p', { class: 'doctor-note' }, '正在体检(GET /api/doctor,最长 20 秒)…'));
}

function renderError(box, message) {
  box.replaceChildren(h('div', { class: 'doctor-error', role: 'status' },
    icon('warn'),
    h('span', null, h('b', null, '体检未完成:'), ` ${message}`)));
}

/* 结果按类别分组渲染:每组一张卡,行内 ok/warn/fail 三色 + fix_hint 展示 */
function renderResults(box, data) {
  const groups = new Map();
  for (const r of data.results || []) {
    if (!groups.has(r.category)) groups.set(r.category, []);
    groups.get(r.category).push(r);
  }
  const worstOf = (rows) => rows.some((r) => r.status === 'fail') ? 'fail'
    : rows.some((r) => r.status === 'warn') ? 'warn' : 'ok';
  const c = data.counts || {};
  const time = new Date().toLocaleTimeString('zh-CN', { hour12: false });

  box.replaceChildren(
    h('div', { class: `doctor-summary is-${data.status}`, role: 'status' },
      statusTag(data.status),
      h('span', { class: 'doctor-summary-txt' },
        `正常 ${c.ok ?? 0} · 注意 ${c.warn ?? 0} · 异常 ${c.fail ?? 0}`),
      h('span', { class: 'doctor-summary-meta' },
        `用时 ${data.took_ms ?? '—'} ms · ${time}`)),
    ...[...groups.entries()].map(([category, rows]) =>
      h('div', { class: 'doctor-group' },
        h('div', { class: 'doctor-group-hd' },
          h('span', { class: 'doctor-group-nm' }, category),
          statusTag(worstOf(rows))),
        ...rows.map((r) => h('div', { class: `doctor-row is-${r.status}` },
          h('span', { class: `doctor-dot is-${r.status}`, 'aria-hidden': 'true' }),
          h('div', { class: 'doctor-row-body' },
            h('div', { class: 'doctor-row-top' },
              h('span', { class: 'doctor-name' }, r.name),
              h('span', { class: `doctor-st is-${r.status}` },
                (STATUS_META[r.status] || STATUS_META.fail).label)),
            r.detail ? h('div', { class: 'doctor-detail' }, r.detail) : null,
            r.status !== 'ok' && r.fix_hint
              ? h('div', { class: 'doctor-fix' }, `处置:${r.fix_hint}`) : null))))));
}

async function runDoctor(btn, box) {
  if (running) return;                       // 后端也有单飞防抖,这里省一次请求
  if (location.protocol === 'file:') {       // 与 backend.sse.js 同判据:演示模式无后端
    renderError(box, '当前以 file:// 打开(演示模式),一键体检需要后端在运行。');
    return;
  }
  running = true;
  btn.disabled = true;
  btn.setAttribute('aria-busy', 'true');
  box.replaceChildren(skeleton());
  try {
    const resp = await fetch('/api/doctor');
    if (!resp.ok) {
      const detail = await resp.json().then((j) => j.detail).catch(() => null);
      throw new Error(detail || `HTTP ${resp.status}`);
    }
    renderResults(box, await resp.json());
  } catch (err) {
    renderError(box, err && err.message ? err.message : '后端不可达。');
  } finally {
    running = false;
    btn.disabled = false;
    btn.removeAttribute('aria-busy');
  }
}

function buildSection() {
  const box = h('div', { class: 'doctor-results' },
    h('p', { class: 'doctor-note' },
      '深检数据库完整性/磁盘/端口/WSL 状态/依赖版本漂移 —— 秒级只读,只报告不修改。'));
  const btn = h('button', {
    class: 'btn doctor-run', type: 'button',
    'aria-label': '运行一键体检(只读,约数秒)',
    onclick: () => runDoctor(btn, box),
  }, icon('shield'), '一键体检');
  return h('div', { class: 'doctor-sec' },
    h('h3', { class: 'sect doctor-hd' }, '一键体检', btn),
    box);
}

function ensureMounted(pane) {
  if (!section) section = buildSection();
  if (!pane.contains(section)) pane.appendChild(section);
}

function init() {
  const pane = document.getElementById(PANE_ID);
  if (!pane) return false;
  ensureMounted(pane);
  // env pane 重渲染(replaceChildren)会移除本区,childList 变更后挂回;
  // 自己的 appendChild 也触发回调,由 ensureMounted 的 contains 判断幂等
  new MutationObserver(() => ensureMounted(pane)).observe(pane, { childList: true });
  return true;
}

/* 自初始化:本脚本在 app.js 之后加载,dock 通常已挂载;
   若 pane 尚不存在(加载次序变化),观察 body 直到出现,失败静默降级。 */
if (typeof document !== 'undefined' && !init()) {
  const mo = new MutationObserver(() => { if (init()) mo.disconnect(); });
  mo.observe(document.body, { childList: true, subtree: true });
}
