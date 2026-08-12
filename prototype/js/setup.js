/* ============================================================
   环境向导（桌面版首启）—— 自包含 ES Module，零依赖
   ------------------------------------------------------------
   用法（app.js 初始化处一行，详见 docs/INTEGRATION-setup-ui.md）：
     import('./setup.js').then((m) => m.maybeShowSetupWizard());
   或静态两行：
     import { maybeShowSetupWizard } from './setup.js';
     maybeShowSetupWizard();

   后端契约（/api/setup/*，由集成分支实现）：
     GET  /api/setup/status
          → { ready, checks: [{key, ok, message, fix_hint}],
              engines: {...}, data: {pairs}, disk_free_gb }
     POST /api/setup/engine-env
          → { detected_conda: str|null, commands: [{title, command, note}] }
     POST /api/setup/save   {engine_prefix, hyp3_source}
          → { ok }

   行为：
     - status.ready === true      → 静默返回 false，不渲染任何 DOM；
     - 后端不可达 / 非 2xx / file:// → 同样静默返回 false；
     - 其余情况                    → 渲染全屏三步向导，全部通过后
                                     「开始使用」是唯一出口。
   样式：css/setup.css（只依赖 tokens.css 设计令牌，明暗主题自适应）。
   ============================================================ */

const API_BASE = '';                 // 与 backend.sse.js 一致：同源相对路径
const OVERLAY_ID = 'setupWizard';

const STEP_META = [
  { n: 1, name: '环境检测' },
  { n: 2, name: '引擎环境' },
  { n: 3, name: '路径配置' },
];

/* ---------------- API ---------------- */
async function apiGet(path) {
  const res = await fetch(API_BASE + path);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function apiPost(path, body) {
  const res = await fetch(API_BASE + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body ?? {}),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

/* ---------------- DOM 小工具（自包含，不 import dom.js） ---------------- */
function h(tag, props, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null) continue;
    if (k === 'class') el.className = v;
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, String(v));
  }
  for (const c of children.flat(Infinity)) {
    if (c == null || c === false || c === true) continue;
    el.append(typeof c === 'object' && c.nodeType ? c : String(c));
  }
  return el;
}

/* 与主界面同风格的 24 viewBox 线性图标 */
const ICONS = {
  ok: '<path d="M20 6L9 17l-5-5"/>',
  fail: '<path d="M6 6l12 12M18 6L6 18"/>',
  info: '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/>',
  refresh: '<path d="M23 4v6h-6M1 20v-6h6M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>',
  copy: '<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
  play: '<path d="M6 3l14 9-14 9z" fill="currentColor" stroke="none"/>',
};

function icon(name) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('fill', 'none');
  svg.setAttribute('stroke', 'currentColor');
  svg.setAttribute('stroke-width', '2');
  svg.setAttribute('stroke-linecap', 'round');
  svg.setAttribute('stroke-linejoin', 'round');
  svg.setAttribute('aria-hidden', 'true');
  svg.innerHTML = ICONS[name] || '';
  return svg;
}

/* ---------------- 复制：clipboard API，失败降级 execCommand ---------------- */
async function copyText(text) {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch { /* 非安全上下文或权限被拒：走降级路径 */ }
  }
  try {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.cssText = 'position:fixed;top:-200px;left:0;opacity:0';
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand('copy');
    ta.remove();
    return ok;
  } catch {
    return false;
  }
}

/* ============================================================
   入口：需要时弹出向导
   返回 Promise<boolean> —— 是否渲染了向导（不等待用户关闭）。
   重复调用幂等：向导已在页面上时直接返回 true。
   ============================================================ */
let inflight = null;

export function maybeShowSetupWizard() {
  if (!inflight) inflight = openIfNeeded().finally(() => { inflight = null; });
  return inflight;
}

async function openIfNeeded() {
  if (document.getElementById(OVERLAY_ID)) return true;
  let status;
  try {
    status = await apiGet('/api/setup/status');
  } catch {
    return false;                    // 后端不可达（如 file:// 演示）：静默不弹
  }
  if (!status || status.ready === true) return false;
  if (!document.body) {
    await new Promise((r) => document.addEventListener('DOMContentLoaded', r, { once: true }));
  }
  createWizard(status).mount();
  return true;
}

/* ============================================================
   向导本体
   ============================================================ */
function createWizard(initialStatus) {
  let status = initialStatus;
  let engineEnv = null;            // POST engine-env 的响应缓存
  let engineLoading = false;
  let step = 1;
  let closed = false;
  const lastFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  const refs = {};

  /* ---- 状态判定 ---- */
  const checks = () => (Array.isArray(status?.checks) ? status.checks : []);

  function isReady() {
    if (!status) return false;
    if (status.ready === true) return true;
    // 只按必需项判定(与后端 ready 同口径):可选项(required===false,如
    // snaphu/pyaps/数据源)未配置不锁死「开始使用」—— 2026-08-12 用户实测反馈
    const cs = checks();
    const req = cs.filter((c) => c.required !== false);
    return req.length > 0 && req.every((c) => c.ok);
  }

  /* ---- 提示条 ---- */
  function makeNote() {
    const el = h('div', { class: 'setup-note', role: 'status', 'aria-live': 'polite' });
    el.hidden = true;
    return el;
  }

  function setNote(el, tone, text) {
    if (!tone) { el.hidden = true; el.textContent = ''; return; }
    el.className = `setup-note is-${tone}`;
    el.textContent = text;
    el.hidden = false;
  }

  /* ---- 步骤条 ---- */
  refs.stepBtns = [];
  refs.stepNos = [];
  const stepsNav = h('ol', { class: 'setup-steps', 'aria-label': '配置步骤' },
    STEP_META.map((m) => {
      const no = h('span', { class: 'no', 'aria-hidden': 'true' }, String(m.n));
      const btn = h('button', { class: 'setup-step', type: 'button', onclick: () => goto(m.n) },
        no, h('span', { class: 'nm' }, m.name));
      refs.stepBtns.push(btn);
      refs.stepNos.push(no);
      return h('li', null, btn);
    }));

  /* ---- 第 1 步：检测清单 ---- */
  refs.checks = h('ul', { class: 'setup-checks' });
  refs.facts = h('div', { class: 'setup-facts' });
  refs.factsWrap = h('div', { class: 'setup-facts-wrap' },
    h('span', { class: 'setup-facts-lb' }, '环境概览'), refs.facts);
  refs.note1 = makeNote();
  refs.redetectLbl = h('span', null, '重新检测');
  refs.redetect = h('button', { class: 'setup-btn', type: 'button', onclick: () => redetect() },
    icon('refresh'), refs.redetectLbl);
  const pane1 = h('section', { class: 'setup-pane', 'aria-label': '第 1 步 环境检测' },
    h('p', { class: 'setup-blurb' },
      '必需项全部就绪即可开始使用；可选项（本地解缠、数据源等）按所选路线配置。',
      '未通过的必需项按提示修复后，点「重新检测」。'),
    refs.checks,
    refs.factsWrap,
    refs.note1,
    h('div', { class: 'setup-acts' }, refs.redetect));

  /* ---- 第 2 步：引擎环境 ---- */
  refs.engineBox = h('div', { class: 'setup-engine' });
  const pane2 = h('section', { class: 'setup-pane', 'aria-label': '第 2 步 引擎环境' },
    h('p', { class: 'setup-blurb' },
      'InSAR 引擎（ISCE2 / MintPy / SNAPHU）运行在独立 conda 环境中。在终端逐条执行以下命令，完成后回到第 1 步重新检测。'),
    refs.engineBox);

  /* ---- 第 3 步：路径配置 ---- */
  refs.enginePrefix = h('input', {
    type: 'text', placeholder: '例：D:\\miniconda3\\envs\\insar-engine',
    spellcheck: 'false', autocomplete: 'off',
  });
  refs.hyp3Source = h('input', {
    type: 'text', placeholder: '例：E:\\insar_data\\hyp3',
    spellcheck: 'false', autocomplete: 'off',
  });
  refs.note3 = makeNote();
  refs.saveLbl = h('span', null, '保存并重新检测');
  refs.save = h('button', { class: 'setup-btn is-pri', type: 'button', onclick: () => save() },
    refs.saveLbl);
  const pane3 = h('section', { class: 'setup-pane', 'aria-label': '第 3 步 路径配置' },
    h('p', { class: 'setup-blurb' }, '告诉桌面版引擎环境与数据在哪里，保存后自动重新检测。'),
    h('label', { class: 'setup-field' },
      h('span', { class: 'lb' }, '引擎环境路径'),
      refs.enginePrefix,
      h('span', { class: 'hint' }, '包含 ISCE2 / MintPy 的 conda 环境根目录（第 2 步安装完成后的 envs/insar-engine）。')),
    h('label', { class: 'setup-field' },
      h('span', { class: 'lb' }, 'HyP3 数据目录'),
      refs.hyp3Source,
      h('span', { class: 'hint' }, '存放 ASF HyP3 干涉对产品的目录，留空表示暂不配置。')),
    refs.note3,
    h('div', { class: 'setup-acts' }, refs.save));

  for (const inp of [refs.enginePrefix, refs.hyp3Source]) {
    inp.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); save(); }
    });
  }

  refs.panes = [pane1, pane2, pane3];

  /* ---- 底栏 ---- */
  refs.sum = h('span', { class: 'setup-sum', role: 'status', 'aria-live': 'polite' });
  refs.prev = h('button', { class: 'setup-btn', type: 'button', onclick: () => goto(step - 1) }, '上一步');
  refs.next = h('button', { class: 'setup-btn', type: 'button', onclick: () => goto(step + 1) }, '下一步');
  refs.start = h('button', {
    class: 'setup-btn is-pri is-start', type: 'button',
    onclick: () => { if (isReady()) close(); },
  }, icon('play'), h('span', null, '开始使用'));

  /* ---- 骨架 ---- */
  const card = h('div', {
    class: 'setup-card', role: 'dialog', 'aria-modal': 'true',
    'aria-labelledby': 'setupTitle', tabindex: '-1',
  },
    h('header', { class: 'setup-hd' },
      h('span', { class: 'logo', 'aria-hidden': 'true' }, 'IA'),
      h('div', { class: 'tt' },
        h('div', { class: 't', id: 'setupTitle' }, '环境向导'),
        h('div', { class: 's' }, '首次使用前，先把运行环境准备好'))),
    stepsNav,
    h('div', { class: 'setup-body' }, pane1, pane2, pane3),
    h('footer', { class: 'setup-ft' },
      refs.sum,
      h('span', { class: 'setup-grow' }),
      refs.prev, refs.next, refs.start));

  const overlay = h('div', { class: 'setup-scrim', id: OVERLAY_ID }, card);

  /* 强制流程：Esc 不关闭也不透传；Tab 圈定在向导内 */
  overlay.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { e.stopPropagation(); return; }
    if (e.key !== 'Tab') return;
    const focusables = [...overlay.querySelectorAll('button:not(:disabled), input:not(:disabled)')]
      .filter((n) => n.offsetParent !== null);
    if (!focusables.length) return;
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    if (e.shiftKey && (document.activeElement === first || document.activeElement === card)) {
      last.focus();
      e.preventDefault();
    } else if (!e.shiftKey && document.activeElement === last) {
      first.focus();
      e.preventDefault();
    }
    e.stopPropagation();
  });

  /* ---- 渲染：检测清单 ---- */
  function renderChecks() {
    const cs = checks();
    if (!cs.length) {
      refs.checks.replaceChildren(h('li', { class: 'setup-check-empty' },
        '后端未返回检测项，请点击下方「重新检测」。'));
      return;
    }
    refs.checks.replaceChildren(...cs.map((c) => {
      const optional = c.required === false;
      // 可选项未配置是中性态(不算"未通过"):必需项才用红色失败视觉
      const tone = c.ok ? 'is-ok' : optional ? 'is-opt' : 'is-bad';
      const pill = c.ok ? '通过' : optional ? '可选 · 未配置' : '未通过';
      return h('li', { class: `setup-check ${tone}` },
        h('span', { class: 'st', 'aria-hidden': 'true' },
          icon(c.ok ? 'ok' : optional ? 'info' : 'fail')),
        h('div', { class: 'bd' },
          h('div', { class: 'msg' },
            c.message || c.key || '（未命名检测项）',
            c.key ? h('code', { class: 'key' }, c.key) : null),
          !c.ok && c.fix_hint
            ? h('div', { class: 'fix' }, h('b', null, optional ? '配置提示' : '修复提示'),
                c.fix_hint)
            : null),
        h('span', { class: 'pill' }, pill));
    }));
  }

  /* ---- 渲染：环境概览（engines 形状未定，宽松处理） ---- */
  function engineEntries() {
    const eng = status?.engines;
    if (!eng || typeof eng !== 'object') return [];
    return Object.entries(eng).map(([name, v]) => {
      if (typeof v === 'boolean') return { name, ok: v, detail: '' };
      if (v && typeof v === 'object') {
        const ok = typeof v.ok === 'boolean' ? v.ok
          : typeof v.found === 'boolean' ? v.found
            : typeof v.available === 'boolean' ? v.available : null;
        const detail = [v.version, v.path, v.note, v.message]
          .find((x) => typeof x === 'string' && x) || '';
        return { name, ok, detail };
      }
      return { name, ok: null, detail: v == null ? '' : String(v) };
    });
  }

  function renderFacts() {
    const facts = engineEntries().map((e) => h('span', {
      class: `setup-fact${e.ok === true ? ' is-ok' : e.ok === false ? ' is-bad' : ''}`,
      title: e.detail || null,
    },
      h('b', null, e.name), ' ',
      e.detail || (e.ok === true ? '可用' : e.ok === false ? '缺失' : '未知')));
    const pairs = status?.data?.pairs;
    if (typeof pairs === 'number') {
      facts.push(h('span', { class: 'setup-fact' }, h('b', null, String(pairs)), ' 个干涉对'));
    }
    const disk = status?.disk_free_gb;
    if (typeof disk === 'number') {
      facts.push(h('span', { class: 'setup-fact' },
        '磁盘剩余 ', h('b', null, disk >= 100 ? String(Math.round(disk)) : disk.toFixed(1)), ' GB'));
    }
    refs.facts.replaceChildren(...facts);
    refs.factsWrap.hidden = !facts.length;
  }

  /* ---- 渲染：第 2 步命令清单 ---- */
  function commandCard(c, i) {
    const cmd = c?.command || '';
    return h('div', { class: 'setup-cmd' },
      h('div', { class: 'hd' },
        h('span', { class: 'tt' }, `${i + 1}. ${c?.title || '命令'}`),
        h('span', { class: 'setup-grow' }),
        copyButton(cmd)),
      c?.note ? h('div', { class: 'nt' }, c.note) : null,
      h('pre', { class: 'code' }, cmd));
  }

  function copyButton(text) {
    const lbl = h('span', null, '复制');
    const btn = h('button', { class: 'setup-copy', type: 'button', 'aria-label': '复制命令' },
      icon('copy'), lbl);
    btn.addEventListener('click', async () => {
      if (btn.disabled) return;
      const ok = await copyText(text);
      btn.disabled = true;
      btn.classList.toggle('is-done', ok);
      lbl.textContent = ok ? '已复制' : '复制失败';
      setTimeout(() => {
        btn.disabled = false;
        btn.classList.remove('is-done');
        lbl.textContent = '复制';
      }, 1600);
    });
    return btn;
  }

  function renderEngineBox(view) {
    const box = refs.engineBox;
    if (view.loading) {
      box.replaceChildren(h('div', { class: 'setup-loading' }, '正在检测 Conda 并生成安装命令…'));
      return;
    }
    if (view.error) {
      box.replaceChildren(
        h('div', { class: 'setup-note is-bad' },
          `获取安装命令失败：${view.error.message || view.error}`),
        h('div', { class: 'setup-acts' },
          h('button', { class: 'setup-btn', type: 'button', onclick: () => ensureEngineEnv(true) }, '重试')));
      return;
    }
    const data = view.data || {};
    const conda = typeof data.detected_conda === 'string' && data.detected_conda
      ? data.detected_conda : null;
    const cmds = Array.isArray(data.commands) ? data.commands : [];
    if (conda && !refs.enginePrefix.value) {
      refs.enginePrefix.placeholder = `例：${conda}\\envs\\insar-engine`;
    }
    box.replaceChildren(
      h('div', { class: `setup-conda${conda ? ' is-ok' : ''}` },
        icon(conda ? 'ok' : 'info'),
        conda
          ? h('span', null, '检测到 Conda：', h('code', null, conda), '，以下命令可直接执行。')
          : h('span', null, '未检测到 Conda，请按命令清单先安装 Miniconda。')),
      ...cmds.map((c, i) => commandCard(c, i)),
      cmds.length
        ? h('p', { class: 'setup-hint-ln' },
          '全部执行完成后，回到第 1 步点「重新检测」；若安装到了自定义位置，再在第 3 步填写路径。')
        : h('div', { class: 'setup-note is-info' }, '后端未返回命令清单。'));
  }

  async function ensureEngineEnv(force = false) {
    if (engineLoading || (engineEnv && !force)) return;
    engineLoading = true;
    renderEngineBox({ loading: true });
    try {
      engineEnv = await apiPost('/api/setup/engine-env', {});
      renderEngineBox({ data: engineEnv });
    } catch (err) {
      engineEnv = null;
      renderEngineBox({ error: err });
    } finally {
      engineLoading = false;
    }
  }

  /* ---- 动作：重新检测 ---- */
  async function redetect() {
    if (refs.redetect.disabled) return;
    refs.redetect.disabled = true;
    refs.redetectLbl.textContent = '检测中…';
    setNote(refs.note1, null);
    try {
      status = await apiGet('/api/setup/status');
      paint();
      setNote(refs.note1, isReady() ? 'ok' : 'info', isReady()
        ? '全部检测通过！点击右下角「开始使用」进入工作区。'
        : '已重新检测，仍有未通过项，可按修复提示处理后再试。');
    } catch (err) {
      setNote(refs.note1, 'bad', `重新检测失败：后端不可达（${err.message}）。`);
    } finally {
      refs.redetect.disabled = false;
      refs.redetectLbl.textContent = '重新检测';
    }
  }

  /* ---- 动作：保存并重新检测 ---- */
  async function save() {
    const enginePrefix = refs.enginePrefix.value.trim();
    const hyp3Source = refs.hyp3Source.value.trim();
    if (!enginePrefix && !hyp3Source) {
      setNote(refs.note3, 'bad', '两项都为空：请至少填写一项再保存。');
      return;
    }
    if (refs.save.disabled) return;
    refs.save.disabled = true;
    refs.saveLbl.textContent = '保存中…';
    setNote(refs.note3, null);
    try {
      const res = await apiPost('/api/setup/save', {
        engine_prefix: enginePrefix,
        hyp3_source: hyp3Source,
      });
      if (res && res.ok === false) {
        throw new Error(res.message || res.error || '后端拒绝了本次保存');
      }
      refs.saveLbl.textContent = '重新检测中…';
      status = await apiGet('/api/setup/status');
      paint();
      goto(1);
      setNote(refs.note1, isReady() ? 'ok' : 'info', isReady()
        ? '配置已保存，全部检测通过！点击右下角「开始使用」。'
        : '配置已保存并重新检测，仍有未通过项，请查看修复提示。');
    } catch (err) {
      setNote(refs.note3, 'bad', `保存失败：${err.message}`);
    } finally {
      refs.save.disabled = false;
      refs.saveLbl.textContent = '保存并重新检测';
    }
  }

  /* ---- 步骤切换 / 全量重绘 ---- */
  function goto(n) {
    step = Math.min(3, Math.max(1, n));
    paint();
    if (step === 2) ensureEngineEnv();
  }

  function paint() {
    renderChecks();
    renderFacts();
    const ready = isReady();

    refs.stepBtns.forEach((btn, i) => {
      const n = i + 1;
      btn.classList.toggle('is-cur', n === step);
      if (n === step) btn.setAttribute('aria-current', 'step');
      else btn.removeAttribute('aria-current');
      const done = n === 1 && ready;   // 2/3 步是资料页，不标完成态
      btn.classList.toggle('is-done', done);
      refs.stepNos[i].replaceChildren(done ? icon('ok') : String(n));
    });
    refs.panes.forEach((p, i) => { p.hidden = i + 1 !== step; });

    refs.prev.disabled = step <= 1;
    refs.next.disabled = step >= 3;

    const cs = checks();
    const req = cs.filter((c) => c.required !== false);
    const reqFails = req.filter((c) => !c.ok).length;
    const optMiss = cs.filter((c) => c.required === false && !c.ok).length;
    refs.sum.className = `setup-sum${ready ? ' is-ok' : ''}`;
    refs.sum.textContent = ready
      ? (optMiss ? `必需项全部通过（另有 ${optMiss} 项可选未配置），可以开始使用`
                 : '全部检测通过，可以开始使用')
      : cs.length ? `${reqFails} / ${req.length} 项必需检测未通过` : '等待检测结果';
    refs.start.disabled = !ready;
    if (ready) refs.start.removeAttribute('title');
    else refs.start.setAttribute('title', '所有检测项通过后可用');
    refs.start.classList.toggle('is-pulse', ready);
  }

  /* ---- 关闭 ---- */
  function close() {
    if (closed) return;
    closed = true;
    overlay.classList.add('is-closing');
    const remove = () => { overlay.remove(); lastFocus?.focus?.(); };
    if (matchMedia('(prefers-reduced-motion: reduce)').matches) remove();
    else setTimeout(remove, 180);
  }

  return {
    mount() {
      paint();
      document.body.appendChild(overlay);
      requestAnimationFrame(() => card.focus());
    },
  };
}
