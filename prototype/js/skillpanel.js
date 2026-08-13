/* ============================================================
   步骤技能面板(skillpanel.js)· 自初始化,幂等
   流水线步骤详情(.pdetail,dock.js 渲染)出现时注入「本步技能」
   折叠区:技能徽章(名称 · 版本 · hash 前 8 位)+ 五章节手风琴。

   数据契约(后端技能系统并行开发中,按契约先行;不可用时优雅降级):
     GET /api/skills        → {skills:[{capability,name,version,content_hash,sections:{章节:摘要}}]}
     GET /api/skills/{step} → {capability,name,version,content_hash,sections:{五章节:全文 Markdown}}
     整个列表 404/网络错 = 后端未含技能系统 → 不渲染任何技能 UI,零报错;
     详情 404 = 该步无技能。
   本地 mock:URL 带 ?skillmock=1 时启用内嵌的第 6 步解缠技能样例
   (开发/截图验证用,不发真实请求);另 &skillopen=1 默认展开(仅 mock 生效)。

   所有权边界:不改 app.js / dock.js —— 事件委托 + MutationObserver 挂载;
   步骤失败态直接读步骤按钮的状态类名(.pstep.f),不依赖内部状态模块。
   ============================================================ */
import { h, txt } from './dom.js';

/* ---------------- 契约常量 ---------------- */
export const SECTION_NAMES = ['适用判据', '参数启发式', '常见失败与处置', 'QA 依据', '参考文献'];
export const DEFAULT_SECTION = '参数启发式';   // 展开技能区时优先展开的章节
export const FAIL_SECTION = '常见失败与处置';  // 步骤 failed 时自动展开并高亮的章节

/** hash 前 8 位展示(徽章用);完整 hash 放 title。 */
export function hash8(hashStr) {
  const hex = String(hashStr || '').replace(/^sha256[:：]?/i, '');
  return hex.slice(0, 8) || '—';
}

/* ============================================================
   Markdown 轻渲染(极简,防 XSS)
   只解析:#..###### 标题 / - * 无序列表 / 1. 有序列表 / **粗体** /
   `行内代码` / [链接](url)。产出只经 createElement/createTextNode,
   绝不 innerHTML:源文本里的 HTML(<script>、onerror=…)天然落为纯文本;
   白名单标签 = h4/h5/h6/p/ul/ol/li/b/code/a。
   链接协议白名单 http/https/mailto/#,其余(javascript:/data:…)丢弃
   href,只留链接文字。
   ============================================================ */
const HREF_OK = /^(https?:|mailto:|#)/i;

export function sanitizeHref(url) {
  const u = String(url ?? '').trim();
  return HREF_OK.test(u) ? u : null;
}

/** 行内解析:code 优先(内部不再解析),其余未命中部分一律文本节点。 */
function inline(text) {
  const out = [];
  const re = /`([^`]+)`|\*\*([^*]+)\*\*|\[([^\]]+)\]\(([^)\s]+)\)/g;
  let last = 0;
  for (let m; (m = re.exec(text)); last = re.lastIndex) {
    if (m.index > last) out.push(txt(text.slice(last, m.index)));
    if (m[1] !== undefined) out.push(h('code', null, m[1]));
    else if (m[2] !== undefined) out.push(h('b', null, m[2]));
    else {
      const href = sanitizeHref(m[4]);
      out.push(href
        ? h('a', { href, target: '_blank', rel: 'noopener noreferrer' }, m[3])
        : txt(m[3]));   // 危险协议:链接消毒为纯文字
    }
  }
  if (last < text.length) out.push(txt(text.slice(last)));
  return out;
}

export function renderMarkdown(md) {
  const root = h('div', { class: 'skl-md' });
  let list = null;   // 当前累积的 ul/ol
  let para = [];     // 当前累积的段落行
  const flushPara = () => {
    if (para.length) { root.appendChild(h('p', null, ...inline(para.join(' ')))); para = []; }
  };
  for (const raw of String(md ?? '').split(/\r?\n/)) {
    const line = raw.trim();
    if (!line) { flushPara(); list = null; continue; }
    const hd = /^(#{1,6})\s+(.*)$/.exec(line);
    const li = /^(?:[-*]|\d+[.)])\s+(.*)$/.exec(line);
    if (hd) {
      flushPara(); list = null;
      const lv = Math.min(hd[1].length + 3, 6);   // # → h4,## → h5,其余 → h6
      root.appendChild(h(`h${lv}`, { class: 'skl-h' }, ...inline(hd[2])));
    } else if (li) {
      flushPara();
      const tag = /^\d/.test(line) ? 'ol' : 'ul';
      if (!list || list.tagName.toLowerCase() !== tag) { list = h(tag, null); root.appendChild(list); }
      list.appendChild(h('li', null, ...inline(li[1])));
    } else {
      list = null;
      para.push(line);
    }
  }
  flushPara();
  return root;
}

/* ============================================================
   手风琴状态机(纯逻辑,可单测):互斥展开,再点已开章节则收起
   ============================================================ */
export function createAccordion(names, { open = null } = {}) {
  const all = [...names];
  let cur = all.includes(open) ? open : null;
  return {
    names: all,
    get open() { return cur; },
    isOpen: (n) => cur === n,
    toggle(n) { if (all.includes(n)) cur = (cur === n ? null : n); return cur; },
    openSection(n) { if (all.includes(n)) cur = n; return cur; },
  };
}

/** 手风琴 DOM:button + aria-expanded/aria-controls,键盘可达(原生按钮)。 */
export function buildAccordion(sections, { open = null, alert = null, idBase = 'skl', onToggle = null } = {}) {
  const acc = createAccordion(sections.map((s) => s.name), { open });
  const root = h('div', { class: 'skl-acc' });
  const refs = new Map();
  const fade = (bd) => {   // 长文本滚动区:未到底部时露出底部渐隐
    bd.classList.toggle('is-scroll', bd.scrollTop + bd.clientHeight < bd.scrollHeight - 4);
  };
  const paint = () => {
    for (const [name, r] of refs) {
      const on = acc.isOpen(name);
      r.btn.setAttribute('aria-expanded', String(on));
      r.bd.hidden = !on;
      r.sec.classList.toggle('is-open', on);
      if (on) fade(r.bd);
    }
  };
  sections.forEach((s, i) => {
    const hdId = `${idBase}-h${i}`, bdId = `${idBase}-b${i}`;
    const btn = h('button', {
      class: 'skl-sec-hd', type: 'button', id: hdId,
      'aria-expanded': 'false', 'aria-controls': bdId,
      onclick: () => { acc.toggle(s.name); paint(); onToggle?.(s.name, acc.isOpen(s.name)); },
    }, h('span', { class: 'skl-caret', 'aria-hidden': 'true' }), s.name);
    const bd = h('div', {
      class: 'skl-sec-bd', id: bdId, role: 'region', 'aria-labelledby': hdId,
      onscroll: (e) => fade(e.target),
    }, renderMarkdown(s.md));
    bd.hidden = true;
    const sec = h('section', { class: `skl-sec${s.name === alert ? ' is-alert' : ''}` }, btn, bd);
    refs.set(s.name, { btn, bd, sec });
    root.appendChild(sec);
  });
  paint();
  return root;
}

/* ============================================================
   技能数据 store:per-session 内存缓存 —— 列表一次、详情按需(按步缓存)
   返回口径:getList() → Map(stepId→摘要) | null(技能系统不可用,静默降级)
             getDetail(id) → 详情对象 | null(该步无技能/读取失败)
   ============================================================ */
export function createSkillStore({ fetchFn = null, mockData = null } = {}) {
  const doFetch = fetchFn || ((url) => fetch(url));
  let listPromise = null;
  const details = new Map();

  async function loadList() {
    if (mockData) {
      return new Map([[Number(mockData.capability), {
        capability: mockData.capability, name: mockData.name, version: mockData.version,
        content_hash: mockData.content_hash,
        sections: { [SECTION_NAMES[0]]: mockData.summary || '' },
      }]]);
    }
    try {
      const resp = await doFetch('/api/skills');
      if (!resp.ok) return null;              // 404 = 后端未含技能系统 → 整体降级
      const data = await resp.json();
      const map = new Map();
      for (const s of data?.skills || []) map.set(Number(s.capability), s);
      return map;
    } catch { return null; }                  // 网络错 / file:// → 静默降级
  }

  return {
    getList() { return (listPromise ||= loadList()); },
    getDetail(stepId) {
      const id = Number(stepId);
      if (details.has(id)) return details.get(id);
      const p = (async () => {
        if (mockData) return id === Number(mockData.capability) ? mockData : null;
        try {
          const resp = await doFetch(`/api/skills/${id}`);
          if (!resp.ok) return null;          // 404 = 该步无技能
          return await resp.json();
        } catch { return null; }
      })();
      details.set(id, p);                     // 缓存 promise:并发展开同一步只发一次请求
      return p;
    },
  };
}

/* ============================================================
   本地 mock(?skillmock=1):第 6 步解缠技能样例,开发/截图验证用
   ============================================================ */
export const MOCK_SKILL = {
  capability: 6,
  name: 'SNAPHU 相位解缠',
  version: '1.0.0',
  content_hash: 'sha256:8c1e4f52a9d03b7646e8f1c25a90d3b8e7f60412c5a9d8b3e6f1704a2c5d9e8b',
  summary: '滤波后平均相干 > 0.35 首选 snaphu_mcf;同震大梯度区避免区域增长法。',
  sections: {
    '适用判据': [
      '## 何时适用',
      '- 干涉图已完成滤波(第 5 步)且平均相干性 > 0.35 时,`snaphu_mcf` 为默认首选。',
      '- 同震大梯度形变区(单干涉对 LOS 位移 > 0.5 m)优先 MCF:区域增长法(`icu`)在密集条纹处易断裂。',
      '- 低相干植被区占比超过场景 40% 时,先收紧相干掩膜再解缠,而不是急着换方法。',
      '',
      '## 何时不适用',
      '- 需要绝对相位基准(如跨轨拼接)时,单纯 2D 解缠不够,须结合 GNSS 控制点约束。',
    ].join('\n'),
    '参数启发式': [
      '## 关键参数',
      '- `min_coherence`:0.10–0.15 起步;同震近场为保留大梯度信号用 **0.10**,农田/植被区提到 0.15 抑制噪声。',
      '- `threads`:与瓦片数同数量级即可,超过物理核数收益为零(本机 20 核 → 8–16)。',
      '- 瓦片划分:单瓦片建议 ≤ 2000×2000 像素;`NTILEROW/NTILECOL` 按影像尺寸整除选取,重叠 ≥ 100 像素。',
      '',
      '## 经验法则',
      '1. 先跑一个代表性干涉对验证参数,再批量;**不要**拿全部 11 对试参数。',
      '2. 残差条纹呈放射状 → 多为解缠误差而非大气,回到本步调参。',
      '3. cost 模式保持 `DEFO`(形变);`TOPO` 仅用于 DEM 生成链路。',
    ].join('\n'),
    '常见失败与处置': [
      '## 失败类别',
      '- **解缠孤岛**(2π 整数跳变的封闭区域):相干掩膜过松 → 提高 `min_coherence` 一档;或改 `snaphu_smooth` 人工引导。',
      '- **内存耗尽(OOM)**:瓦片过大 → 增大 `NTILEROW/NTILECOL`;单进程内存与瓦片像素数近似线性。',
      '- **大梯度失稳**:同震近场条纹混叠 → 先做多视(降采样)再解缠,或 `icu` 分区后 MCF 融合。',
      '',
      '## 处置顺序',
      '1. 查 `snaphu.log` 尾部的 cost 收敛信息;',
      '2. 掩膜 → 瓦片 → 方法降级,一次只动一个变量;',
      '3. 处置后必须重跑第 11 步质检,闭合残差达标才放行下游。',
    ].join('\n'),
    'QA 依据': [
      '- 闭合回路残差:三元组闭合相位 RMS **< 0.3 rad** 判通过(阈值 PENDING 标定,见质量门台账)。',
      '- 解缠后重缠绕与滤波相位差:非掩膜区 ≥ 98% 像素一致。',
      '- 与 GNSS 站 LOS 投影对比:同震场 |ΔLOS| < 20 mm 记一致(演示阈值)。',
    ].join('\n'),
    '参考文献': [
      '- Chen & Zebker (2001) 统计代价网络流相位解缠:[DOI](https://doi.org/10.1364/JOSAA.18.000338)',
      '- SNAPHU 用户手册:[官方文档](https://web.stanford.edu/group/radar/softwareandlinks/sw/snaphu/)',
      '- MintPy 解缠误差闭合检查:[GitHub](https://github.com/insarlab/MintPy)',
    ].join('\n'),
  },
};

/* ============================================================
   注入层(仅浏览器):MutationObserver + 事件委托,不改 dock.js
   ============================================================ */
const uiState = { region: new Map(), section: new Map() };   // per-session 展开记忆

/** 当前详情卡对应的步骤号:优先读选中步骤按钮的 data-step,兜底解析标题。 */
function detectStep(pd) {
  const cur = document.querySelector('#pane-pipeline .pstep[aria-current="true"]');
  if (cur?.dataset.step) return Number(cur.dataset.step);
  const m = /第\s*(\d+)\s*步/.exec(pd.querySelector('.hd')?.textContent || '');
  return m ? Number(m[1]) : null;
}

/** 与失败卡联动的最小实现:读步骤按钮的状态类名(dock.js 的 f = failed)。 */
function isFailed(stepId) {
  const btn = document.querySelector(`#pane-pipeline .pstep[data-step="${stepId}"]`);
  return !!btn?.classList.contains('f');
}

/** 在 .pdetail 尾部填充「本步技能」折叠区(host 已挂,meta 来自列表)。 */
function fillRegion(host, stepId, meta, { store, autoOpen }) {
  const failed = isFailed(stepId);
  const bdId = `skl-bd-${stepId}`;
  const caret = h('span', { class: 'skl-caret', 'aria-hidden': 'true' });
  const badge = h('span', {
    class: 'skl-badge',
    title: `内容哈希 ${meta.content_hash || '—'} · 知识版本随运行写入 provenance`,
  }, `${meta.name || '技能'} · v${meta.version || '?'} · ${hash8(meta.content_hash)}`);
  const body = h('div', { class: 'skl-bd', id: bdId });
  body.hidden = true;

  let detailStarted = false;
  const ensureDetail = () => {
    if (detailStarted) return;
    detailStarted = true;
    const loading = h('p', { class: 'skl-empty' }, '正在读取技能内容…');
    body.appendChild(loading);
    store.getDetail(stepId).then((detail) => {
      if (!body.isConnected) return;
      loading.remove();
      const sections = SECTION_NAMES
        .filter((n) => detail?.sections?.[n] != null)
        .map((n) => ({ name: n, md: detail.sections[n] }));
      if (!sections.length) {
        body.appendChild(h('p', { class: 'skl-empty' }, '该步技能内容暂不可用。'));
        return;
      }
      // 失败态自动展开「常见失败与处置」并高亮;用户本会话的手动选择优先
      const remembered = uiState.section.has(stepId) ? uiState.section.get(stepId) : undefined;
      const open = remembered !== undefined ? remembered
        : (failed && sections.some((s) => s.name === FAIL_SECTION) ? FAIL_SECTION : DEFAULT_SECTION);
      body.appendChild(buildAccordion(sections, {
        open, alert: failed ? FAIL_SECTION : null, idBase: `skl-${stepId}`,
        onToggle: (name, on) => uiState.section.set(stepId, on ? name : null),
      }));
      body.appendChild(h('p', { class: 'skl-prov' },
        '知识版本(名称 · 版本 · 内容哈希)随本步运行写入 provenance,论文中可溯源。'));
    });
  };

  const btn = h('button', {
    class: 'skl-hd', type: 'button', 'aria-expanded': 'false', 'aria-controls': bdId,
  }, caret, txt('本步技能'), badge);
  const setOpen = (on) => {
    btn.setAttribute('aria-expanded', String(on));
    host.classList.toggle('is-open', on);
    body.hidden = !on;
    if (on) ensureDetail();
  };
  btn.addEventListener('click', () => {
    const on = btn.getAttribute('aria-expanded') !== 'true';
    uiState.region.set(stepId, on);
    setOpen(on);
  });

  host.append(btn, body);
  host.hidden = false;
  // 默认收起;失败步自动展开(用户显式收起过则尊重用户);mock 的 skillopen=1 供截图
  const remembered = uiState.region.get(stepId);
  if (remembered !== undefined ? remembered : (failed || autoOpen)) setOpen(true);
}

export function initSkillPanel() {
  if (typeof window === 'undefined' || typeof document === 'undefined') return false;
  if (window.__skillPanelInit) return false;           // 幂等:重复引入零副作用
  if (typeof MutationObserver !== 'function') return false;
  const dockBody = document.getElementById('dockBody');
  if (!dockBody) return false;                         // 非工作区页面:不挂载
  window.__skillPanelInit = true;

  let mock = false, autoOpen = false;
  try {
    const q = new URLSearchParams(window.location.search);
    mock = q.get('skillmock') === '1';
    autoOpen = mock && q.get('skillopen') === '1';
  } catch { /* 无 location(异常宿主)时按真实路径走 */ }
  const store = createSkillStore({ mockData: mock ? MOCK_SKILL : null });

  const scan = () => {
    for (const pd of document.querySelectorAll('#pane-pipeline .pdetail')) {
      if (pd.querySelector('.skl')) continue;          // 已注入(幂等)
      const stepId = detectStep(pd);
      if (!stepId) continue;
      const host = h('div', { class: 'skl' });         // 先占位,列表回来再填充
      host.hidden = true;
      pd.appendChild(host);
      store.getList().then((list) => {
        const meta = list?.get(stepId);
        if (!meta || !host.isConnected) return;        // 无技能系统/该步无技能:零 UI
        fillRegion(host, stepId, meta, { store, autoOpen });
      });
    }
  };

  let queued = false;
  const schedule = () => {
    if (queued) return;
    queued = true;
    queueMicrotask(() => { queued = false; scan(); });
  };

  // 面板任何重渲染(切步骤/刷新/切 run)都会触发 childList 变化
  new MutationObserver(schedule).observe(dockBody, { childList: true, subtree: true });
  // 事件委托:点击步骤行展开详情时立即注入(dock.js 同步重渲染后冒泡到 document)
  document.addEventListener('click', (e) => {
    if (e.target?.closest?.('.pstep')) schedule();
  });
  scan();
  return true;
}

/* 浏览器环境自启动;node 测试环境(无 MutationObserver)只导出纯函数 */
if (typeof window !== 'undefined' && typeof document !== 'undefined'
    && typeof MutationObserver === 'function') {
  initSkillPanel();
}
