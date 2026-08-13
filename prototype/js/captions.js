/* ============================================================
   captions.js —— 影像面板「论文图注」区(自初始化,零改 gallery.js/figures.js)

   职责:为每张真实图件卡提供「生成图注」按钮 → 中英双语期刊风格图注卡
   (中英切换 + 各自一键复制)+「LLM 润色 / 模板骨架」徽章;已有落盘图注
   (<name>.caption.json)开面板即直接显示,无需点生成。

   所有权与注入策略(figcompare.js 同族,与 visionqa 等并行注入代理同区
   不同容器,互不认识):
   - 注入:MutationObserver 观察 .glx(影像画廊容器)出现/重渲染 → 网格
     之后幂等追加自带容器 .capx;gallery 重渲染清掉容器后观察器自动补挂,
     生成结果存模块级状态,重挂不丢失;
   - 事件委托:document 冒泡阶段统一接管 [data-capx-*] 按钮(生成/切换/
     复制),按钮全部长在自带容器里,不碰卡片自身点击(灯箱行为不变);
   - 数据:图件名读卡片 .glx-name 的 title;session/run_id 从缩略图
     /api/artifact-file URL 反解(figcompare 同策,零 state.js 依赖);
   - 已有图注:GET /api/report/caption 每图询问一次(404=尚无,静默);
   - 生成:POST /api/report/caption {session, figure, run_id?};后端骨架
     保底(LLM 未配置照常可用),润色稿经双向数值校验,不过即回退骨架;
   - 降级:file:// / 后端不可达 / 演示图件 → 区内如实提示,绝不打断画廊。
   ============================================================ */
import { $$, h, toast } from './dom.js';

/* ============================================================
   纯函数(node 校验脚本直测,不碰 DOM)
   ============================================================ */

/** 缩略图 /api/artifact-file URL → {session, runId};非产物 URL 返回 null。 */
export function parseArtifactQuery(src) {
  const s = String(src || '');
  const q = s.indexOf('?');
  if (!s.includes('/api/artifact-file') || q < 0) return null;
  const params = new URLSearchParams(s.slice(q + 1));
  const session = params.get('session');
  if (!session) return null;
  return { session, runId: params.get('run_id') || '' };
}

/** 模块状态键:同名图件跨会话/跨 run 互不串。 */
export function captionKey(info) {
  return `${info.session}|${info.runId}|${info.name}`;
}

/** llm_polish → 徽章文案/配色(蓝 = LLM 润色过;绿 = 确定性骨架,非降级失败态)。 */
export function badgeSpec(llmPolish) {
  return llmPolish
    ? { label: 'LLM 润色', cls: 'is-run',
        title: 'LLM 已润色措辞;中英数值经双向校验与骨架一致(缺失/多出未知数字都会被拒绝)' }
    : { label: '模板骨架', cls: 'is-ok',
        title: 'LLM 未配置/失败/校验未过 —— 确定性骨架,事实与数字直接来自图件元数据与账本' };
}

/** HTTP 状态 → 人话错误文案(404 = 图件不在账本;0 = 后端不可达)。 */
export function errorText(status) {
  if (status === 404) return '图件或 run 不在账本中,无法生成图注(演示图件/已清理的 run 亦然)。';
  if (status === 0) return '后端不可达(或 file:// 演示模式):无法生成图注。';
  return `生成失败(HTTP ${status}),请稍后重试。`;
}

/** 响应 → 指定语种文本(zh/en 之外或缺字段回空串,渲染层不炸)。 */
export function pickText(data, lang) {
  if (!data || typeof data !== 'object') return '';
  return typeof data[lang] === 'string' ? data[lang] : '';
}

/** 图注落盘文件名:velocity.png → velocity.caption.json(后端 CAPTION_SUFFIX 同约定)。 */
export function captionFileName(name) {
  const s = String(name);
  const i = s.lastIndexOf('.');
  return (i > 0 ? s.slice(0, i) : s) + '.caption.json';
}

/** 图件卡 → {name, session, runId, demo}(名取 .glx-name title,身份从缩略图 URL 反解)。 */
export function cardInfo(card) {
  const nameEl = card.querySelector('.glx-name');
  const name = (nameEl && (nameEl.getAttribute('title') || nameEl.textContent)) || '';
  const img = card.querySelector('img');
  const q = parseArtifactQuery(img ? img.getAttribute('src') : '');
  return q ? { name, session: q.session, runId: q.runId, demo: false }
    : { name, session: '', runId: '', demo: true };
}

/* ============================================================
   模块状态(DOM 是它的投影:gallery 重渲染/重挂不丢结果)
   ============================================================ */

const stById = new Map();    // key → {phase:none|busy|ready, data, error, lang, source}
const infoByKey = new Map(); // key → 最近一次渲染的 {name, session, runId}
const probed = new Set();    // 已询问过落盘图注的 key(每 key 只 GET 一次)
let rev = 0;                 // 状态版本:变更 → +1 → 重绘(容器 sig 含 rev,幂等收敛)

function stateOf(k) {
  if (!stById.has(k)) stById.set(k, { phase: 'none', data: null, error: null, lang: 'zh' });
  return stById.get(k);
}

function bump() {
  rev += 1;
  ensureUI();
}

/* ============================================================
   数据层
   ============================================================ */

async function postCaption(info) {
  const body = { session: info.session, figure: info.name };
  if (info.runId) body.run_id = info.runId;
  const resp = await fetch('/api/report/caption', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!resp.ok) throw Object.assign(new Error(`HTTP ${resp.status}`), { status: resp.status });
  return resp.json();
}

async function generate(k) {
  const info = infoByKey.get(k);
  const st = stateOf(k);
  if (!info || st.phase === 'busy') return;
  st.phase = 'busy';
  st.error = null;
  bump();
  try {
    st.data = await postCaption(info);
    st.source = 'generated';
  } catch (err) {
    // 失败如实提示,绝不清掉已有结果(旧图注仍可读可复制)
    st.error = errorText(err && typeof err.status === 'number' ? err.status : 0);
  }
  st.phase = st.data ? 'ready' : 'none';
  bump();
}

/** 已落盘图注询问(每 key 一次;404/不可达一律静默 —— 生成按钮点击时再报)。 */
function probe(info) {
  const k = captionKey(info);
  if (probed.has(k) || location.protocol === 'file:') return;
  probed.add(k);
  (async () => {
    try {
      const qs = new URLSearchParams({ session: info.session, figure: info.name });
      if (info.runId) qs.set('run_id', info.runId);
      const resp = await fetch(`/api/report/caption?${qs}`);
      if (!resp.ok) return;
      const data = await resp.json();
      const st = stateOf(k);
      if (!st.data) {
        st.data = data;
        st.source = 'saved';
        st.phase = 'ready';
        bump();
      }
    } catch { /* 静默降级 */ }
  })();
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
   渲染(全部长在自带容器 .capx 里)
   ============================================================ */

function badgeNode(llmPolish) {
  const b = badgeSpec(llmPolish);
  return h('span', { class: `tag ${b.cls}`, title: b.title }, b.label);
}

function noteText(st) {
  const file = captionFileName((st.data && st.data.figure) || '');
  if (st.source === 'saved') return `读取自已落盘的 ${file}(点「重新生成」可覆盖)。`;
  if (st.data && st.data.saved === false) return '图注落盘失败(工作目录不可写),以上文本仍可复制。';
  return `已落盘图件旁 ${file}(文件面板可见),重新生成会覆盖。`;
}

function captionCard(info, st, k) {
  const cur = st.lang === 'en' ? 'en' : 'zh';
  const tab = (lang, label) => h('button', {
    class: 'capx-tab', type: 'button',
    'data-capx-lang': lang, 'data-capx-key': k,
    'aria-pressed': String(cur === lang),
    title: lang === 'zh' ? '显示中文图注' : 'Show English caption',
  }, label);
  const copyBtn = (lang, label) => h('button', {
    class: 'btn btn-gho btn-sm', type: 'button',
    'data-capx-copy': lang, 'data-capx-key': k,
    'aria-label': `复制${lang === 'zh' ? '中文' : '英文'}图注:${info.name}`,
  }, label);
  return h('div', { class: 'capx-card' },
    h('div', { class: 'capx-tools', role: 'group', 'aria-label': '图注语言与复制' },
      tab('zh', '中文'), tab('en', 'English'),
      h('span', { class: 'capx-grow' }),
      copyBtn('zh', '复制中文'), copyBtn('en', '复制英文')),
    h('p', { class: 'capx-text', lang: cur === 'zh' ? 'zh-CN' : 'en' }, pickText(st.data, cur)),
    h('p', { class: 'capx-note' }, noteText(st)));
}

function rowNode(info) {
  const k = captionKey(info);
  infoByKey.set(k, info);
  const st = stateOf(k);
  const busy = st.phase === 'busy';
  return h('div', { class: 'capx-item', 'data-capx-item': info.name },
    h('div', { class: 'capx-head' },
      h('span', { class: 'capx-fig', title: info.name }, info.name),
      st.data ? badgeNode(!!st.data.llm_polish) : null,
      h('span', { class: 'capx-grow' }),
      h('button', {
        class: 'btn btn-gho btn-sm capx-gen', type: 'button',
        'data-capx-gen': info.name, 'data-capx-key': k,
        disabled: busy || undefined,
        title: '从图件元数据 + provenance 账本生成双语图注;数字与方法名只来自账本,缺项如实标「未记录」',
        'aria-label': `为 ${info.name} 生成双语图注`,
      }, busy ? '生成中…' : (st.data ? '重新生成' : '生成图注'))),
    st.error ? h('p', { class: 'capx-err', role: 'status' }, st.error) : null,
    st.data ? captionCard(info, st, k) : null);
}

function renderBox(box, infos) {
  const real = infos.filter((i) => !i.demo);
  const kids = [h('h4', { class: 'capx-title' }, '论文图注',
    h('span', { class: 'capx-hint' }, '元数据 + 账本生成 · 中英双语 · 事实闭集'))];
  if (!real.length) {
    kids.push(h('p', { class: 'capx-empty' }, location.protocol === 'file:'
      ? '演示模式(file:// 无后端):图注需真实运行产物,后端接入后自动可用。'
      : '当前为演示图件(无真实账本);运行流水线出图后,这里为每张图提供「生成图注」。'));
  } else {
    kids.push(h('div', { class: 'capx-list' }, ...real.map(rowNode)));
  }
  box.replaceChildren(...kids);
  real.forEach(probe);
}

/* ============================================================
   自初始化注入(幂等:容器 sig = 图件清单 + 状态版本,零变更不写 DOM)
   ============================================================ */

function ensureUI() {
  for (const glx of $$('.glx')) {
    const cards = glx.querySelectorAll('.glx-card');
    const infos = [...cards].map(cardInfo).filter((i) => i.name);
    if (!infos.length) continue;   // 骨架/空态阶段不注入
    const sig = `${rev}#` + infos.map((i) => (i.demo ? 'd:' : 'r:') + captionKey(i)).join(';');
    let box = [...glx.children].find((c) => c.classList && c.classList.contains('capx')) || null;
    if (box && box.getAttribute('data-capx-sig') === sig) continue;   // 已是最新:零变更
    if (!box) {
      box = h('section', { class: 'capx', 'aria-label': '论文图注生成' });
      glx.appendChild(box);
    }
    box.setAttribute('data-capx-sig', sig);
    renderBox(box, infos);
  }
}

/* 冒泡阶段委托:只认自带容器里的 [data-capx-*] 按钮,不碰图件卡(灯箱不变)。 */
function onDocClick(e) {
  const t = e.target && e.target.closest
    ? e.target.closest('[data-capx-gen],[data-capx-lang],[data-capx-copy]') : null;
  if (!t) return;
  const k = t.getAttribute('data-capx-key') || '';
  if (t.getAttribute('data-capx-gen') !== null) {
    generate(k);
    return;
  }
  const st = stById.get(k);
  if (!st || !st.data) return;
  const lang = t.getAttribute('data-capx-lang');
  if (lang !== null) {
    const next = lang === 'en' ? 'en' : 'zh';
    if (st.lang !== next) {
      st.lang = next;
      bump();
    }
    return;
  }
  const which = t.getAttribute('data-capx-copy') === 'en' ? 'en' : 'zh';
  copyText(pickText(st.data, which), which === 'zh' ? '已复制中文图注' : '已复制英文图注');
}

let booted = false;

/** 自初始化(幂等,可重复调用);无 document(bare node import)安全空转。 */
export function initCaptions() {
  if (booted || typeof document === 'undefined') return;
  booted = true;
  document.addEventListener('click', onDocClick);
  const start = () => {
    if (typeof MutationObserver === 'function' && document.body) {
      new MutationObserver(ensureUI).observe(document.body, { childList: true, subtree: true });
    }
    ensureUI();
  };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
}

initCaptions();
