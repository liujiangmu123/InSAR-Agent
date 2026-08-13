/* ============================================================
   结果章节草稿生成(报告 tab 的独立自初始化区块,位于方法草稿模块之后)。

   职责:
   - 「生成结果章节」按钮 → POST /api/report/results {session, run_id?}
     → 渲染中文结果章节草稿(等宽预格式 + 一键复制)+ 「LLM 润色:是/否
     (回退骨架)」徽章;
   - 后端两段式防幻觉:骨架由账本 QA 指标 + 速度场统计纯代码拼接,LLM 只
     润色措辞且经双向数值校验,不过即回退骨架 —— LLM 未配置时按钮照常可用
     (骨架模式),区块内注明该语义;
   - 草稿同时落盘 run 工作目录 report_results.md(响应 saved 字段如实展示)。

   挂载策略(所有权约束:不改 dock.js / reportlive.js / reportdraft.js):
   - reportdraft.js 同款自初始化:MutationObserver 观察 #dockBody,
     #pane-report 出现且不含本区块时追加到 pane 尾部。区块位于方法草稿模块
     之后的保证:index.html 中本脚本排在 reportdraft.js 之后 → 首挂顺序在后;
     dock 重渲染时 MutationObserver 回调按创建序触发 → 重挂顺序同样在后。
   - 非工作区页面(无 #dockBody)零打扰。

   失败语义:无 run(404)/ 后端不可达 / file:// → 区块内如实提示,
   绝不抛错、绝不清掉已有结果。
   ============================================================ */
import { h, icon, toast } from './dom.js';
import { S } from './state.js';
import { activeRunId } from './runswitch.js';

/* ============================================================
   纯函数(node 校验脚本直测,不碰 DOM)
   ============================================================ */

/** 请求体:run_id 仅在 run 历史切换器选中历史 run 时携带。 */
export function requestPayload(session, runId) {
  const body = { session };
  if (runId) body.run_id = runId;
  return body;
}

/** llm_polish → 徽章文案/配色(reportdraft 同语义:骨架是确定性保底,用 is-ok)。 */
export function badgeSpec(llmPolish) {
  return llmPolish
    ? { label: 'LLM 润色:是', cls: 'is-run',
        title: 'LLM 已润色措辞;数值经双向校验与骨架一致(缺失/多出未知数字都会被拒绝)' }
    : { label: 'LLM 润色:否(回退骨架)', cls: 'is-ok',
        title: 'LLM 未配置/失败/校验未过 —— 展示确定性骨架,指标与统计直接来自账本与产物' };
}

/** HTTP 状态 → 人话错误文案(404 = 本会话还没有 run)。 */
export function errorText(status) {
  if (status === 404) return '本会话还没有可生成结果章节的 run:先完成一次执行再来。';
  if (status === 0) return '后端不可达(或 file:// 演示模式):无法生成结果章节。';
  return `生成失败(HTTP ${status}),请稍后重试。`;
}

/* ============================================================
   数据层
   ============================================================ */

async function postResults() {
  const resp = await fetch('/api/report/results', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(requestPayload(S.sessionId, activeRunId())),
  });
  if (!resp.ok) throw Object.assign(new Error(`HTTP ${resp.status}`), { status: resp.status });
  return resp.json();
}

/** 剪贴板写入:clipboard API 优先,失败退回隐藏 textarea(reportdraft 同款)。 */
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

/* ============================================================
   渲染(状态存模块级:dock 重渲染后重挂不丢结果)
   ============================================================ */

const ui = {
  busy: false,
  data: null,      // 最近一次成功响应 {run_id, draft, llm_polish, facts_used, saved}
  error: null,     // 最近一次失败文案(与 data 互斥展示,成功后清空)
  session: null,   // 结果所属会话:切会话自动复位
};

function resetUiIfSessionChanged() {
  if (ui.session !== S.sessionId) {
    ui.busy = false;
    ui.data = null;
    ui.error = null;
    ui.session = S.sessionId;
  }
}

function resultView() {
  const d = ui.data;
  if (!d) return null;
  const badge = badgeSpec(d.llm_polish);
  return h('div', { class: 'resultsdraft-result' },
    h('div', {
      style: { display: 'flex', gap: '7px', alignItems: 'center',
               flexWrap: 'wrap', margin: '8px 0' },
    },
      h('span', { class: `tag ${badge.cls}`, title: badge.title },
        icon(d.llm_polish ? 'brain' : 'shield'), badge.label),
      h('span', { class: 'tag', title: `run ${d.run_id}` },
        `run ${String(d.run_id || '').slice(0, 15)}`),
      h('span', { style: { flex: '1' } }),
      h('button', {
        class: 'btn btn-gho btn-sm', type: 'button',
        'aria-label': '复制结果章节草稿全文',
        onclick: () => copyText(d.draft, '已复制结果章节草稿(可直接改写进论文)'),
      }, icon('clip'), '复制全文')),
    h('div', { class: 'shell', role: 'region', 'aria-label': '结果章节草稿全文' }, d.draft),
    h('p', { class: 'blurb' },
      d.saved
        ? '草稿已落盘 run 工作目录 report_results.md(文件面板可见);'
        : '草稿落盘失败(工作目录不可写),以上文本仍可复制使用;',
      d.llm_polish
        ? '润色稿已通过数值双向校验:骨架数字一个不缺、未混入任何未知数字。'
        : 'LLM 未配置或润色未过校验时展示骨架 —— 指标与统计直接来自账本与产物。'));
}

function render(root) {
  resetUiIfSessionChanged();
  root.replaceChildren(
    h('h3', { class: 'sect' }, '结果章节草稿(QA 指标 + 速度场统计)'),
    h('p', { class: 'blurb' },
      '把账本 QA 指标与速度场统计变成可直接改写进论文的中文结果段落:每个数字' +
      '只来自账本与产物重解析,缺失如实写「未记录」。未配置 LLM 也可用(确定性骨架模式)。'),
    h('button', {
      class: 'btn btn-pri btn-sm', type: 'button',
      disabled: ui.busy || undefined,
      'aria-label': '生成结果章节(POST /api/report/results)',
      onclick: async () => {
        if (ui.busy) return;
        ui.busy = true;
        render(root);
        try {
          ui.data = await postResults();
          ui.error = null;
        } catch (e) {
          ui.error = errorText(e && typeof e.status === 'number' ? e.status : 0);
        } finally {
          ui.busy = false;
          if (root.isConnected) render(root);
        }
      },
    }, icon('doc'), ui.busy ? '生成中…' : '生成结果章节'),
    ui.error ? h('div', { class: 'note is-stale', role: 'status', style: { marginTop: '8px' } },
      icon('warn'), h('span', null, ui.error)) : null,
    resultView());
}

/* ============================================================
   自初始化挂载(幂等):#pane-report 出现/重渲染 → 追加本区块
   ============================================================ */

let rootEl = null;
let observer = null;

function ensureMounted() {
  const pane = document.getElementById('pane-report');
  if (!pane) return;
  if (rootEl && pane.contains(rootEl)) return;   // 已在场:幂等直返
  if (!rootEl) {
    rootEl = h('section', { class: 'resultsdraft', 'aria-label': '结果章节草稿生成' });
  }
  pane.appendChild(rootEl);                      // pane 尾部(方法草稿模块之后)
  render(rootEl);
}

/** 自初始化(幂等,可重复调用)。非工作区页面(无 #dockBody)零打扰。 */
export function initResultsDraft() {
  if (typeof document === 'undefined' || observer) return;
  const start = () => {
    const dockBody = document.getElementById('dockBody');
    if (!dockBody || observer) return;
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

initResultsDraft();
