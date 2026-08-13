/* ============================================================
   installguide 的无浏览器自查脚本(node prototype/installguide.check.mjs)
   最小 DOM stub + fetch/clipboard 桩直接 import installguide.js(零真实网络),断言:
   ① 自初始化注入:缺失且认识的引擎行才有「如何安装」按钮,幂等不重复;
   ② 抽屉打开:loading → 方案渲染(推荐途径横幅 / 选项卡 / 步骤 / 耗时磁盘);
   ③ 选项卡状态机:aria-selected 单选,切换后步骤/meta/前置随之更换;
   ④ 一键复制:剪贴板收到原文,按钮「复制 → 已复制 ✓」;复制全部 = 逐行拼接;
   ⑤ 剪贴板失败 → 「复制失败」态;
   ⑥ mark-done 成功:POST 载荷 {engine}、指引缓存失效重拉、状态行「已探测到」、
      方案区切「无需安装」、install:probe-refreshed 事件派发;
   ⑦ mark-done 仍缺失:「仍未探测到」告警态;
   ⑧ 前置未满足展示「(未满足)」;⑨ 关闭(按钮 + Esc);⑩ 纯函数文案表。
   放在 prototype/ 根目录:scripts/check_frontend.py 只发现该层的 *.check.mjs。
   只依赖 node 内建能力,零 npm 依赖。
   ============================================================ */

/* ---------------- 最小 DOM stub(llmsettings.check.mjs 同款裁剪版) ---------------- */
class NodeBase {
  constructor() { this.childNodes = []; this.parentNode = null; }
  get textContent() { return this.childNodes.map((c) => c.textContent).join(''); }
  get isConnected() {
    for (let n = this; n; n = n.parentNode) if (n === DOC.body) return true;
    return false;
  }
}

class TextNode extends NodeBase {
  constructor(s) { super(); this.nodeType = 3; this.data = String(s); }
  get textContent() { return this.data; }
  set textContent(v) { this.data = String(v); }
  remove() { detach(this); }
}

function detach(n) {
  if (!n.parentNode) return;
  const i = n.parentNode.childNodes.indexOf(n);
  if (i >= 0) n.parentNode.childNodes.splice(i, 1);
  n.parentNode = null;
}

class Element extends NodeBase {
  constructor(tag) {
    super();
    this.nodeType = 1;
    this.tagName = String(tag).toLowerCase();
    this.attrs = {}; this._cls = new Set(); this.dataset = {}; this.style = {};
    this.listeners = {};
    this.disabled = false; this.hidden = false;
  }
  get id() { return this.attrs.id || ''; }
  set id(v) { this.attrs.id = String(v); }
  get className() { return [...this._cls].join(' '); }
  set className(v) { this._cls = new Set(String(v).split(/\s+/).filter(Boolean)); }
  get classList() {
    const s = this._cls;
    return {
      add: (...cs) => cs.forEach((c) => s.add(c)),
      remove: (...cs) => cs.forEach((c) => s.delete(c)),
      contains: (c) => s.has(c),
    };
  }
  setAttribute(k, v) { if (k === 'class') { this.className = v; return; } this.attrs[k] = String(v); }
  getAttribute(k) { return k === 'class' ? this.className : (this.attrs[k] ?? null); }
  appendChild(n) { detach(n); n.parentNode = this; this.childNodes.push(n); return n; }
  append(...ns) { ns.forEach((n) => this.appendChild(n instanceof NodeBase ? n : new TextNode(n))); }
  replaceChildren(...ns) {
    [...this.childNodes].forEach(detach);
    this.append(...ns.filter((n) => n !== null && n !== undefined && n !== false));
  }
  remove() { detach(this); }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  dispatchEvent(evt) {
    evt.target ||= this; evt.currentTarget = this;
    (this.listeners[evt.type] || []).forEach((fn) => fn.call(this, evt));
    return true;
  }
  click() { this.dispatchEvent({ type: 'click', preventDefault() {}, target: this }); }
  focus() { DOC.activeElement = this; }
  get firstChild() { return this.childNodes[0] || null; }
  set textContent(v) { this.replaceChildren(new TextNode(v)); }
  get textContent() { return super.textContent; }
  get children() { return this.childNodes.filter((c) => c.nodeType === 1); }
  matches(sel) {
    const m = /^([a-zA-Z0-9-]*)(?:#([\w-]+))?((?:\.[\w-]+)*)((?:\[[\w-]+(?:="[^"]*")?\])*)$/
      .exec(String(sel).trim());
    if (!m) return false;
    if (m[1] && this.tagName !== m[1].toLowerCase()) return false;
    if (m[2] && this.attrs.id !== m[2]) return false;
    if (!(m[3].match(/\.[\w-]+/g) || []).every((c) => this._cls.has(c.slice(1)))) return false;
    return (m[4].match(/\[[\w-]+(?:="[^"]*")?\]/g) || []).every((t) => {
      const am = /^\[([\w-]+)(?:="([^"]*)")?\]$/.exec(t);
      const val = this.attrs[am[1]];
      return am[2] === undefined ? val !== undefined : val === am[2];
    });
  }
  querySelectorAll(sel) {
    const out = new Set();
    for (const part of String(sel).split(',')) {
      const segs = part.trim().split(/\s+/).filter(Boolean);
      let bases = [this];
      for (const seg of segs) {
        const next = new Set();
        for (const b of bases) for (const d of walk(b)) if (d.matches(seg)) next.add(d);
        bases = [...next];
      }
      bases.forEach((b) => out.add(b));
    }
    return [...out];
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}

function* walk(n) { for (const c of n.children) { yield c; yield* walk(c); } }

const DOC = {
  readyState: 'complete',
  activeElement: null,
  body: new Element('body'),
  listeners: {},
  createElement: (t) => new Element(t),
  createElementNS: (_ns, t) => new Element(t),
  createTextNode: (s) => new TextNode(s),
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); },
  dispatchEvent(evt) { (this.listeners[evt.type] || []).forEach((fn) => fn(evt)); return true; },
  getElementById(id) {
    for (const d of walk(this.body)) if (d.attrs.id === id) return d;
    return null;
  },
  querySelector(sel) { return this.body.querySelector(sel); },
  querySelectorAll(sel) { return this.body.querySelectorAll(sel); },
};

globalThis.document = DOC;
globalThis.window = globalThis;
globalThis.Node = NodeBase;
globalThis.location = { protocol: 'http:' };
globalThis.MutationObserver = class { constructor(cb) { this.cb = cb; } observe() {} disconnect() {} };
globalThis.CustomEvent = class {
  constructor(type, opts = {}) { this.type = type; this.detail = opts.detail ?? null; }
};

/* ---------------- 剪贴板桩(可切换失败态;node 22 的 navigator 是只读 getter,须 defineProperty 覆盖) ---------------- */
const copied = [];
let clipboardFail = false;
Object.defineProperty(globalThis, 'navigator', {
  configurable: true,
  value: {
    clipboard: {
      writeText: async (t) => {
        if (clipboardFail) throw new Error('denied');
        copied.push(String(t));
      },
    },
  },
});

/* ---------------- fetch 桩:/api/install/* 走内存路由,零真实网络 ---------------- */
const WSL_ROUTE_OK = {
  route: 'wsl', title: 'WSL 一键脚本(scripts/wsl_setup.ps1,已实测)',
  est_minutes: 40, disk_gb: 12,
  steps: ['wsl --version',
          'powershell -NoProfile -ExecutionPolicy Bypass -File scripts\\wsl_setup.ps1',
          '.venv\\Scripts\\python.exe -m insar_agent.runtime.wsl_probe --distro insar'],
  notes: ['脚本幂等可重跑'],
  requires: ['wsl2'],
  requires_detail: [{ id: 'wsl2', label: 'WSL2 可用(wsl --version)', met: true }],
};
const CONDA_ROUTE = {
  route: 'conda', title: 'Windows 本机 conda(miniforge,与向导同口径)',
  est_minutes: 20, disk_gb: 6,
  steps: ['下载并安装 Miniforge:https://mirrors.tuna.tsinghua.edu.cn/…/Miniforge3-Windows-x86_64.exe',
          'conda create -n insar -c https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/conda-forge/ --override-channels python=3.11 mintpy -y',
          'conda install -n insar -c https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/conda-forge/ --override-channels "libblas=*=*openblas" -y'],
  notes: ['必做:BLAS 切 OpenBLAS(MKL 2024 硬崩 0xC06D007F 坑位)'],
  requires: [], requires_detail: [],
};
const ISCE2_ROUTE_UNMET = {
  ...WSL_ROUTE_OK,
  requires_detail: [{ id: 'wsl2', label: 'WSL2 可用(wsl --version)', met: false }],
};
const PLAN_MINTPY = {
  engine: 'mintpy', label: 'MintPy(SBAS 时序反演,7-9 步引擎)', required: true,
  recommend: 'conda', why: 'Windows 本机 conda 环境最快可验证(无需 WSL)', unmet: [],
  routes: [CONDA_ROUTE, WSL_ROUTE_OK],
};
const PLAN_ISCE2 = {
  engine: 'isce2', label: 'ISCE2(干涉处理 2-6 步引擎)', required: false,
  recommend: 'wsl',
  why: '唯一途径:isce2 无 Windows 原生包。前置未就绪:WSL2 可用(wsl --version)',
  unmet: ['wsl2'],
  routes: [ISCE2_ROUTE_UNMET],
};
const ENGINES_BASE = { mintpy: null, gdal: 'present', snaphu: 'present',
                       isce2: null, pyaps: 'present', pystamps: 'present', snap: 'present' };
const GUIDE_BEFORE = {
  missing: ['mintpy', 'isce2'], plans: [PLAN_MINTPY, PLAN_ISCE2],
  engines: ENGINES_BASE, wsl: { installed: true, distros: ['insar'], engine_probe: null },
};
const GUIDE_AFTER = {   // mark-done 后:mintpy 已在位,只剩 isce2
  missing: ['isce2'], plans: [PLAN_ISCE2],
  engines: { ...ENGINES_BASE, mintpy: '1.6.4' },
  wsl: GUIDE_BEFORE.wsl,
};

let guidePayload = GUIDE_BEFORE;
let markDoneResult = null;
const calls = [];
globalThis.fetch = async (url, opts = {}) => {
  const method = opts.method || 'GET';
  calls.push({ url: String(url), method, body: opts.body ? JSON.parse(opts.body) : null });
  const json = (data) => ({ ok: true, status: 200, json: async () => data });
  if (String(url).endsWith('/api/install/guide') && method === 'GET') return json(guidePayload);
  if (String(url).endsWith('/api/install/mark-done') && method === 'POST') return json(markDoneResult);
  throw new Error(`意外的网络请求:${method} ${url}`);
};
const guideGets = () => calls.filter((c) => c.method === 'GET').length;

/* ---------------- 断言工具(check_frontend 解析约定:两空格 ok/FAIL) ---------------- */
let failed = 0;
let count = 0;
function check(name, cond) {
  count += 1;
  if (cond) { console.log(`  ok  ${name}`); }
  else { failed += 1; console.error(`  FAIL ${name}`); }
}
const tick = () => new Promise((r) => setTimeout(r, 0));

/* ---------------- 页面骨架:环境面板 + 引擎行(dock.js envView 的最小等价物) ---------------- */
function engRow(name, ok, withBadge = false) {
  const row = new Element('div'); row.className = 'eng';
  const nm = new Element('span'); nm.className = 'nm';
  nm.appendChild(new TextNode(name));
  if (withBadge) { const b = new Element('span'); b.className = 'mono'; b.textContent = 'local'; nm.appendChild(b); }
  const ver = new Element('span'); ver.className = 'ver'; ver.textContent = ok ? '已装' : '—';
  const st = new Element('span'); st.className = `st ${ok ? 'ok' : 'bad'}`;
  row.appendChild(nm); row.appendChild(ver); row.appendChild(st);
  return row;
}
const pane = new Element('div'); pane.setAttribute('id', 'pane-env');
const rowMintpy = engRow('mintpy', false, true);
const rowGdal = engRow('gdal', true);
const rowIsce2 = engRow('isce2', false);
const rowGacos = engRow('gacos', false);   // 闭集之外:不应注按钮
pane.appendChild(rowMintpy); pane.appendChild(rowGdal);
pane.appendChild(rowIsce2); pane.appendChild(rowGacos);
DOC.body.appendChild(pane);

const IG = await import('./js/installguide.js');
await tick();

/* ---------------- ① 自初始化注入 ---------------- */
console.log('== ① 缺失引擎行「如何安装」按钮注入 ==');
check('缺失引擎 mintpy 行注入按钮(nm 带宿主徽标也能取名)',
  rowMintpy.querySelectorAll('.instg-howto').length === 1);
check('缺失引擎 isce2 行注入按钮', rowIsce2.querySelectorAll('.instg-howto').length === 1);
check('在位引擎 gdal 行不注', rowGdal.querySelectorAll('.instg-howto').length === 0);
check('闭集之外的行(gacos)不注', rowGacos.querySelectorAll('.instg-howto').length === 0);
IG.ensureRowButtons();
check('重复扫描幂等:不重复注入',
  rowMintpy.querySelectorAll('.instg-howto').length === 1
  && rowIsce2.querySelectorAll('.instg-howto').length === 1);
check('按钮语义:type=button + aria-haspopup=dialog + aria-label 点名引擎',
  rowMintpy.querySelector('.instg-howto').getAttribute('type') === 'button'
  && rowMintpy.querySelector('.instg-howto').getAttribute('aria-haspopup') === 'dialog'
  && rowMintpy.querySelector('.instg-howto').getAttribute('aria-label') === '如何安装 mintpy');

/* ---------------- ② 抽屉打开与方案渲染 ---------------- */
console.log('\n== ② 抽屉打开:loading → 方案渲染 ==');
rowMintpy.querySelector('.instg-howto').click();
let overlay = DOC.body.querySelector('.instg-overlay');
check('点击按钮 → 抽屉挂到 body,role=dialog + aria-modal',
  !!overlay && overlay.querySelector('.instg-drawer').getAttribute('role') === 'dialog'
  && overlay.querySelector('.instg-drawer').getAttribute('aria-modal') === 'true');
check('数据到达前先给 loading 态', !!overlay.querySelector('.instg-loading'));
await tick(); await tick();
check('推荐途径横幅:点名推荐途径 conda + why 原文',
  overlay.querySelector('.instg-why').textContent
    === '推荐途径 conda:Windows 本机 conda 环境最快可验证(无需 WSL)');
let tabs = overlay.querySelectorAll('.instg-tab');
check('两条途径 → 两个选项卡(conda / WSL)', tabs.length === 2
  && tabs[0].textContent.startsWith('conda') && tabs[1].textContent.startsWith('WSL'));
check('默认选中推荐途径(aria-selected 单选)',
  tabs[0].getAttribute('aria-selected') === 'true'
  && tabs[1].getAttribute('aria-selected') === 'false');
check('推荐选项卡带「荐」徽标', tabs[0].querySelectorAll('.instg-rec').length === 1
  && tabs[1].querySelectorAll('.instg-rec').length === 0);
check('途径标题 + 步骤逐条渲染(conda 3 步)',
  overlay.querySelector('.instg-title').textContent === CONDA_ROUTE.title
  && overlay.querySelectorAll('.instg-step').length === 3
  && overlay.querySelectorAll('.instg-cmd')[1].textContent === CONDA_ROUTE.steps[1]);
check('meta:预计耗时 + 磁盘占用',
  overlay.querySelector('.instg-badge').textContent.includes('预计 20 分钟')
  && overlay.querySelector('.instg-badge').textContent.includes('磁盘约 6 GB'));
check('坑位提示渲染(OpenBLAS 坑)',
  overlay.querySelector('.instg-notes').textContent.includes('OpenBLAS'));
check('每步都有复制按钮 + 复制全部按钮',
  overlay.querySelectorAll('.instg-steps .instg-copy').length === 3
  && overlay.querySelectorAll('.instg-copyall').length === 1);

/* ---------------- ③ 选项卡状态机 ---------------- */
console.log('\n== ③ 选项卡状态机 ==');
tabs[1].click();
tabs = overlay.querySelectorAll('.instg-tab');
check('切到 WSL → aria-selected 移位',
  tabs[0].getAttribute('aria-selected') === 'false'
  && tabs[1].getAttribute('aria-selected') === 'true');
check('步骤区切换为 WSL 途径(首条 wsl --version)',
  overlay.querySelector('.instg-title').textContent === WSL_ROUTE_OK.title
  && overlay.querySelectorAll('.instg-cmd')[0].textContent === 'wsl --version');
check('meta 随途径更换(预计 40 分钟 · 磁盘约 12 GB)',
  overlay.querySelector('.instg-badge').textContent === '预计 40 分钟 · 磁盘约 12 GB');
check('前置条件行:met=true → is-met,无「未满足」后缀',
  overlay.querySelector('.instg-req').classList.contains('is-met')
  && overlay.querySelector('.instg-req').textContent === '前置:WSL2 可用(wsl --version)');
overlay.querySelectorAll('.instg-tab')[1].click();
check('重复点击当前选项卡:保持选中不炸',
  overlay.querySelectorAll('.instg-tab')[1].getAttribute('aria-selected') === 'true');

/* ---------------- ④ 一键复制 ---------------- */
console.log('\n== ④ 一键复制 ==');
const copyBtn = overlay.querySelectorAll('.instg-steps .instg-copy')[0];
copyBtn.click();
await tick();
check('剪贴板收到该步原文', copied[copied.length - 1] === 'wsl --version');
check('按钮态「已复制 ✓」+ is-ok + 暂时禁用',
  copyBtn.textContent === '已复制 ✓' && copyBtn.classList.contains('is-ok') && copyBtn.disabled);
overlay.querySelector('.instg-copyall').click();
await tick();
check('复制全部 = 步骤逐行拼接', copied[copied.length - 1] === WSL_ROUTE_OK.steps.join('\n'));

/* ---------------- ⑤ 剪贴板失败态 ---------------- */
console.log('\n== ⑤ 剪贴板失败态 ==');
clipboardFail = true;
const copyBtn2 = overlay.querySelectorAll('.instg-steps .instg-copy')[1];
copyBtn2.click();
await tick();
check('写剪贴板被拒 → 「复制失败」+ is-bad',
  copyBtn2.textContent === '复制失败' && copyBtn2.classList.contains('is-bad'));
clipboardFail = false;

/* ---------------- ⑥ mark-done 成功(缓存失效重探) ---------------- */
console.log('\n== ⑥ mark-done 成功流 ==');
let refreshedDetail = null;
DOC.addEventListener('install:probe-refreshed', (e) => { refreshedDetail = e.detail; });
markDoneResult = { engine: 'mintpy', present: true, version: '1.6.4',
                   missing: ['isce2'], plans: [PLAN_ISCE2],
                   engines: GUIDE_AFTER.engines, wsl: GUIDE_AFTER.wsl };
guidePayload = GUIDE_AFTER;   // 服务端重探后的世界
const getsBefore = guideGets();
overlay.querySelector('.instg-done').click();
await tick(); await tick(); await tick();
const post = calls.filter((c) => c.method === 'POST');
check('POST /api/install/mark-done 载荷 {engine: mintpy}',
  post.length === 1 && post[0].body.engine === 'mintpy'
  && post[0].url.endsWith('/api/install/mark-done'));
check('状态行:已探测到 + 版本号(ok 色调)',
  overlay.querySelector('.instg-status').textContent.includes('已探测到 mintpy(1.6.4)')
  && overlay.querySelector('.instg-status').classList.contains('is-ok'));
check('指引缓存失效 → 重新拉取 guide', guideGets() === getsBefore + 1);
check('方案区切「无需安装」+ 底部确认区隐藏',
  overlay.querySelector('.instg-ready').textContent.includes('已探测到 mintpy')
  && overlay.querySelector('.instg-ft').hidden === true);
check('install:probe-refreshed 事件携带重探结果',
  refreshedDetail && refreshedDetail.present === true && refreshedDetail.engine === 'mintpy');

/* ---------------- ⑦ 关闭(按钮) ---------------- */
console.log('\n== ⑦ 关闭抽屉 ==');
overlay.querySelector('.instg-x').click();
check('关闭按钮 → 抽屉移除', DOC.body.querySelector('.instg-overlay') === null);

/* ---------------- ⑧ 前置未满足 + mark-done 仍缺失(isce2) ---------------- */
console.log('\n== ⑧ isce2:前置未满足 + 仍缺失流 ==');
IG.openDrawer('isce2');
overlay = DOC.body.querySelector('.instg-overlay');
await tick(); await tick();
check('唯一途径 → 单选项卡,why 提及前置未就绪',
  overlay.querySelectorAll('.instg-tab').length === 1
  && overlay.querySelector('.instg-why').textContent.includes('前置未就绪'));
check('前置行:met=false → is-unmet + 「(未满足)」',
  overlay.querySelector('.instg-req').classList.contains('is-unmet')
  && overlay.querySelector('.instg-req').textContent.includes('(未满足)'));
markDoneResult = { engine: 'isce2', present: false, version: null,
                   missing: ['isce2'], plans: [PLAN_ISCE2],
                   engines: GUIDE_AFTER.engines, wsl: GUIDE_AFTER.wsl };
overlay.querySelector('.instg-done').click();
await tick(); await tick(); await tick();
check('仍缺失 → 状态行「仍未探测到」(warn 色调),确认按钮复位可再点',
  overlay.querySelector('.instg-status').textContent.includes('仍未探测到 isce2')
  && overlay.querySelector('.instg-status').classList.contains('is-warn')
  && overlay.querySelector('.instg-done').disabled === false);

/* ---------------- ⑨ Esc 关闭 + 已在位引擎抽屉 ---------------- */
console.log('\n== ⑨ Esc 关闭 + 已在位引擎 ==');
overlay.dispatchEvent({ type: 'keydown', key: 'Escape' });
check('Esc → 抽屉移除', DOC.body.querySelector('.instg-overlay') === null);
IG.openDrawer('gdal');
overlay = DOC.body.querySelector('.instg-overlay');
await tick(); await tick();
check('已在位引擎(gdal)→「无需安装」态 + 确认区隐藏',
  overlay.querySelector('.instg-ready').textContent.includes('已探测到 gdal(present)')
  && overlay.querySelector('.instg-ft').hidden === true);
IG.closeDrawer();

/* ---------------- ⑩ 纯函数文案 ---------------- */
console.log('\n== ⑩ 纯函数文案 ==');
check('routeLabel:wsl→WSL / conda→conda / manual→手动 / 未知原样',
  IG.routeLabel('wsl') === 'WSL' && IG.routeLabel('conda') === 'conda'
  && IG.routeLabel('manual') === '手动' && IG.routeLabel('x') === 'x');
check('metaText:disk_gb=0 → 「磁盘增量可忽略」',
  IG.metaText({ est_minutes: 5, disk_gb: 0 }) === '预计 5 分钟 · 磁盘增量可忽略');
check('copyAllText:steps 逐行拼接;缺省安全',
  IG.copyAllText({ steps: ['a', 'b'] }) === 'a\nb' && IG.copyAllText({}) === '');
check('KNOWN_ENGINES 七引擎闭集(与 runtime/install_guide.py 对齐)',
  IG.KNOWN_ENGINES.length === 7
  && ['mintpy', 'gdal', 'snaphu', 'isce2', 'pyaps', 'pystamps', 'snap']
    .every((e) => IG.KNOWN_ENGINES.includes(e)));

console.log(failed ? `\n${count} 项断言,${failed} 项失败` : `\n全部 ${count} 项断言通过`);
process.exit(failed ? 1 : 0);
