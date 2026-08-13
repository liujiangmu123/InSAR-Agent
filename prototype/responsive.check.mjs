/* ============================================================
   响应式状态机的无浏览器自查脚本(node prototype/responsive.check.mjs)
   与 cmdk.check.mjs 同一套纪律:最小 DOM/window 桩直接 import
   js/responsive.js,覆盖 —— 宽度→档位映射 / 挤压档折叠默认值与
   手动偏好优先级 / localStorage 键规范与异常容错 / 宽屏零痕迹 /
   单栏档三段导航(代点 rail 开关 + MutationObserver 高亮回推)/
   responsive.css「全部规则包在 @media 内」与 index.html 接线的
   静态检查。只依赖 node 内建能力,零 npm 依赖。

   注:按 check_frontend.py 的套件发现约定(prototype/*.check.mjs),
   本文件放 prototype/ 根目录,与其余 check 脚本同列。
   ============================================================ */

/* ---------------- 最小 DOM / window 桩(可造多个独立世界) ---------------- */
function* walk(el) {
  for (const c of el.childNodes) {
    if (c.nodeType === 1) { yield c; yield* walk(c); }
  }
}

class El {
  constructor(tag) {
    this.nodeType = 1;
    this.tagName = String(tag).toLowerCase();
    this.childNodes = []; this.parentNode = null;
    this.attrs = {}; this._cls = new Set(); this.dataset = {};
    this.listeners = {}; this._mo = [];
    this._hidden = false; this._text = ''; this.innerHTML = '';
  }
  get children() { return this.childNodes.filter((c) => c.nodeType === 1); }
  get className() { return [...this._cls].join(' '); }
  set className(v) { this._cls = new Set(String(v).split(/\s+/).filter(Boolean)); }
  get classList() {
    const s = this._cls;
    return {
      add: (...cs) => cs.forEach((c) => s.add(c)),
      remove: (...cs) => cs.forEach((c) => s.delete(c)),
      contains: (c) => s.has(c),
      toggle: (c, on) => { const v = on === undefined ? !s.has(c) : !!on; v ? s.add(c) : s.delete(c); return v; },
    };
  }
  /* hidden 属性变化同步通知 MutationObserver 桩(浏览器里 hidden
     属性反映为 attribute,这里直接在 setter 触发,时序等价) */
  get hidden() { return this._hidden; }
  set hidden(v) {
    const changed = this._hidden !== !!v;
    this._hidden = !!v;
    if (changed) this._mo.forEach((cb) => cb([{ type: 'attributes', attributeName: 'hidden' }]));
  }
  get textContent() { return this._text || this.childNodes.map((c) => c.textContent ?? '').join(''); }
  set textContent(v) { this._text = String(v); this.childNodes = []; }
  setAttribute(k, v) { if (k === 'class') { this.className = v; return; } this.attrs[k] = String(v); }
  getAttribute(k) { return k === 'class' ? this.className : (this.attrs[k] ?? null); }
  appendChild(n) { n.parentNode = this; this.childNodes.push(n); return n; }
  addEventListener(t, fn) { (this.listeners[t] ||= []).push(fn); }
  click() { (this.listeners.click || []).forEach((fn) => fn.call(this, { type: 'click' })); }
}

/** 一个独立「世界」:文档骨架 + matchMedia + 存储 + rail 开关行为桩 */
function mkWorld({ width = 1920, storageThrows = false } = {}) {
  const body = new El('body');
  const doc = {
    body,
    documentElement: new El('html'),
    createElement: (t) => new El(t),
    getElementById(id) {
      for (const el of walk(body)) if (el.attrs.id === id) return el;
      return null;
    },
    querySelector(sel) {   // responsive.js 只用到类选择器('.sider-hd')
      const cls = String(sel).slice(1);
      for (const el of walk(body)) if (el._cls.has(cls)) return el;
      return null;
    },
  };

  /* index.html 的既有壳元素:#sider(含 .sider-hd)/#dock/两个 rail 开关 */
  const sider = new El('aside'); sider.setAttribute('id', 'sider');
  const hd = new El('div'); hd.className = 'sider-hd'; sider.appendChild(hd);
  const dock = new El('aside'); dock.setAttribute('id', 'dock');
  const railSessions = new El('button'); railSessions.setAttribute('id', 'railSessions');
  const railDock = new El('button'); railDock.setAttribute('id', 'railDock');
  body.appendChild(sider); body.appendChild(dock);
  body.appendChild(railSessions); body.appendChild(railDock);

  /* rail 开关行为 = app.js toggleSider/toggleDock 的窄屏语义缩影:
     开一个则关另一个(互斥),再点则收起 */
  const clicks = [];
  railSessions.addEventListener('click', () => {
    clicks.push('railSessions');
    const opening = sider.hidden;
    sider.hidden = !sider.hidden;
    if (opening) dock.hidden = true;
  });
  railDock.addEventListener('click', () => {
    clicks.push('railDock');
    const opening = dock.hidden;
    dock.hidden = !dock.hidden;
    if (opening) sider.hidden = true;
  });

  /* matchMedia 桩:解析 (max-width: NNNpx),宽度变化时广播 change */
  const state = { width };
  const mqls = [];
  const win = {
    document: doc,
    matchMedia(q) {
      const px = Number((/max-width:\s*(\d+)px/.exec(q) || [])[1] || 0);
      const mq = {
        media: q, px, listeners: [],
        get matches() { return state.width <= px; },
        addEventListener(t, fn) { if (t === 'change') mq.listeners.push(fn); },
      };
      mqls.push(mq);
      return mq;
    },
    getComputedStyle: () => ({
      getPropertyValue: (n) => ({
        '--bp-sider-squeeze': '1280px',
        '--bp-dock-drawer': '1240px',
        '--bp-single-col': '768px',
      }[n] || ''),
    }),
    MutationObserver: class {
      constructor(cb) { this.cb = cb; }
      observe(el) { el._mo.push(this.cb); }
      disconnect() {}
    },
    localStorage: null,
  };
  const stored = new Map();
  win.localStorage = storageThrows
    ? { getItem() { throw new Error('denied'); }, setItem() { throw new Error('denied'); } }
    : { getItem: (k) => (stored.has(k) ? stored.get(k) : null), setItem: (k, v) => stored.set(k, String(v)) };

  const setWidth = (w) => { state.width = w; mqls.forEach((mq) => mq.listeners.forEach((fn) => fn({ matches: mq.matches }))); };
  const find = (cls) => { for (const el of walk(body)) if (el._cls.has(cls)) return el; return null; };
  return { win, doc, body, sider, dock, railSessions, railDock, clicks, stored, mqls, setWidth, find };
}

/* ---------------- 断言工具 ---------------- */
let failed = 0;
function check(name, cond) {
  if (cond) { console.log(`  ok  ${name}`); }
  else { failed += 1; console.error(`  FAIL ${name}`); }
}

/* ---------------- 世界一:挂全局 → import(走浏览器同款自初始化) ---------------- */
const W = mkWorld({ width: 1920 });
globalThis.window = W.win;
globalThis.document = W.doc;

const Rsp = await import('./js/responsive.js');

console.log('== ① 宽度 → 档位映射(纯函数,含边界) ==');
check('1920 → wide', Rsp.tierOf(1920) === 'wide');
check('1281 → wide(≥1281 新规则零生效的边界)', Rsp.tierOf(1281) === 'wide');
check('1280 → squeeze(挤压档上界)', Rsp.tierOf(1280) === 'squeeze');
check('1241 → squeeze(挤压档下界 = 抽屉档 +1)', Rsp.tierOf(1241) === 'squeeze');
check('1240 → drawer(既有抽屉档上界)', Rsp.tierOf(1240) === 'drawer');
check('769 → drawer(抽屉档下界)', Rsp.tierOf(769) === 'drawer');
check('768 → phone(单栏档上界)', Rsp.tierOf(768) === 'phone');
check('375 → phone', Rsp.tierOf(375) === 'phone');
check('断点表可注入(自定义 bps 仍按同一状态机走)',
  Rsp.tierOf(880, { squeeze: 1100, drawer: 1050, phone: 900 }) === 'phone'
  && Rsp.tierOf(1000, { squeeze: 1100, drawer: 1050, phone: 900 }) === 'drawer'
  && Rsp.tierOf(1060, { squeeze: 1100, drawer: 1050, phone: 900 }) === 'squeeze');

console.log('\n== ② 挤压档侧栏形态:默认折叠,手动偏好优先 ==');
check('无偏好 → 进档默认折叠', Rsp.siderMode('squeeze', null) === 'collapsed');
check("偏好 'expanded' → 保持展开(手动选择优先)", Rsp.siderMode('squeeze', 'expanded') === 'expanded');
check("偏好 'collapsed' → 折叠", Rsp.siderMode('squeeze', 'collapsed') === 'collapsed');
check('非法偏好值按默认折叠处理', Rsp.siderMode('squeeze', 'banana') === 'collapsed');
check('非挤压档一律不适用(null)',
  Rsp.siderMode('wide', 'expanded') === null
  && Rsp.siderMode('drawer', 'expanded') === null
  && Rsp.siderMode('phone', 'expanded') === null);

console.log('\n== ③ localStorage 键规范 ==');
check("键名 = 'ia-rsp-sider'(项目 ia- 前缀 + rsp 命名空间)",
  Rsp.STORE_KEY_SIDER === 'ia-rsp-sider' && /^ia-rsp-/.test(Rsp.STORE_KEY_SIDER));
check("取值枚举 = ['collapsed','expanded']",
  JSON.stringify(Rsp.SIDER_PREFS) === '["collapsed","expanded"]');
check('JS 回退断点与 base.css 清单镜像一致(1280/1240/768)',
  JSON.stringify(Rsp.FALLBACK_BPS) === '{"squeeze":1280,"drawer":1240,"phone":768}');
check('readBps 优先读 CSS 变量(桩里给的即所得)',
  JSON.stringify(Rsp.readBps(W.win)) === '{"squeeze":1280,"drawer":1240,"phone":768}');

console.log('\n== ④ 宽屏零痕迹(1920 自初始化后) ==');
check('body 无任何 rsp-* 状态类', W.body.className === '');
check('未注入折叠钮与导航(惰性创建,宽屏不见)', !W.find('rsp-pin') && !W.find('rsp-nav'));
check('常驻痕迹仅 3 个 matchMedia 监听(squeeze/drawer/phone 各一)',
  W.mqls.length === 3 && W.mqls.every((m) => m.listeners.length === 1));

console.log('\n== ⑤ 进入挤压档(1280):默认折叠 + 注入折叠钮 ==');
W.setWidth(1280);
const pin = W.find('rsp-pin');
check('body.rsp-squeeze 标记', W.body.classList.contains('rsp-squeeze'));
check('无偏好 → body.rsp-sider-collapsed(默认折叠)', W.body.classList.contains('rsp-sider-collapsed'));
check('折叠钮注入 .sider-hd 且可见',
  !!pin && pin.parentNode?._cls.has('sider-hd') && pin.hidden === false);
check('折叠钮语义:aria-pressed=true(当前折叠)+ 有可读名',
  pin?.getAttribute('aria-pressed') === 'true' && !!pin?.getAttribute('aria-label'));

console.log('\n== ⑥ 手动展开 → 偏好落盘 ==');
pin.click();
check('点击后取消折叠类', !W.body.classList.contains('rsp-sider-collapsed'));
check("localStorage 写入 'expanded'", W.stored.get('ia-rsp-sider') === 'expanded');
check('折叠钮 aria-pressed 翻转为 false', pin.getAttribute('aria-pressed') === 'false');

console.log('\n== ⑦ 跨断点往返:自动切换但尊重手动选择 ==');
W.setWidth(1920);
check('回宽屏:rsp-* 状态类全部清除', W.body.className === '');
check('回宽屏:折叠钮隐藏(不残留视觉)', pin.hidden === true);
W.setWidth(1275);
check('重回挤压档:保持用户手动展开(不再默认折叠)',
  W.body.classList.contains('rsp-squeeze') && !W.body.classList.contains('rsp-sider-collapsed'));
pin.click();
check("再点折叠:偏好更新为 'collapsed' 且类恢复",
  W.stored.get('ia-rsp-sider') === 'collapsed' && W.body.classList.contains('rsp-sider-collapsed'));

console.log('\n== ⑧ 单栏档(700):三段导航注入与默认高亮 ==');
/* 模拟 app.js applyResponsive 跨入窄屏的默认收起(该逻辑属 app.js,桩里手工执行) */
W.sider.hidden = true; W.dock.hidden = true;
W.setWidth(700);
const nav = W.find('rsp-nav');
check('body.rsp-phone 标记,且挤压档类不残留',
  W.body.classList.contains('rsp-phone') && !W.body.classList.contains('rsp-squeeze')
  && !W.body.classList.contains('rsp-sider-collapsed'));
check('导航注入 body 且可见,折叠钮转为隐藏', !!nav && nav.hidden === false && pin.hidden === true);
check('三段按钮 = 会话 / 聊天 / 面板',
  nav?.children.length === 3
  && nav.children.map((b) => b.textContent).join('/') === '会话/聊天/面板');
check('侧栏与 dock 均收起时默认高亮「聊天」',
  nav.children.find((b) => b.dataset.view === 'chat')?.getAttribute('aria-pressed') === 'true');

console.log('\n== ⑨ 三段导航只代点既有 rail 开关(状态机仍归 app.js) ==');
const btn = (v) => nav.children.find((b) => b.dataset.view === v);
btn('sessions').click();
check('「会话」→ 代点 #railSessions,侧栏可见',
  W.clicks.at(-1) === 'railSessions' && W.sider.hidden === false);
check('MutationObserver 回推高亮:会话 on,聊天 off',
  btn('sessions').getAttribute('aria-pressed') === 'true'
  && btn('chat').getAttribute('aria-pressed') === 'false');
const n0 = W.clicks.length;
btn('sessions').click();
check('已在会话页再点「会话」不重复代点(无多余 click)', W.clicks.length === n0);
btn('dock').click();
check('「面板」→ 代点 #railDock,互斥收起侧栏,高亮跟随',
  W.clicks.at(-1) === 'railDock' && W.dock.hidden === false && W.sider.hidden === true
  && btn('dock').getAttribute('aria-pressed') === 'true');
btn('chat').click();
check('「聊天」→ 收起打开着的面板,回到单栏聊天',
  W.dock.hidden === true && W.sider.hidden === true
  && btn('chat').getAttribute('aria-pressed') === 'true');

console.log('\n== ⑩ 中间抽屉档(1100)完全交还既有实现 ==');
W.setWidth(1100);
check('无任何 rsp-* 状态类(769–1240 归 base.css/dock.css/app.js 管)', W.body.className === '');
check('导航与折叠钮均隐藏', nav.hidden === true && pin.hidden === true);

console.log('\n== ⑪ localStorage 异常容错(隐私模式等,世界二) ==');
const W2 = mkWorld({ width: 1275, storageThrows: true });
let threw = false;
try { Rsp.init(W2.win); } catch { threw = true; }
const pin2 = W2.find('rsp-pin');
check('存储抛异常时 init 不炸,默认折叠照常',
  !threw && W2.body.classList.contains('rsp-sider-collapsed'));
pin2.click();
check('偏好退化为会话内记忆:点击展开仍生效', !W2.body.classList.contains('rsp-sider-collapsed'));

console.log('\n== ⑫ 静态检查(css / index.html / base.css 接线) ==');
const fs = await import('node:fs');
const css = fs.readFileSync(new URL('./css/responsive.css', import.meta.url), 'utf-8');
/* 顶层块清单:去注释后逐字符扫,depth 0 → { 之前的文本即块前奏 */
const stripped = css.replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '));
const preludes = [];
{
  let depth = 0, buf = '';
  for (const ch of stripped) {
    if (ch === '{') { if (depth === 0) { preludes.push(buf.trim()); buf = ''; } depth += 1; }
    else if (ch === '}') { depth -= 1; buf = ''; }
    else if (depth === 0) buf += ch;
  }
  check('responsive.css 顶层只有 @media 块(≥1281px 宽屏零生效)',
    preludes.length >= 2 && preludes.every((p) => p.startsWith('@media')));
}
check('媒体查询宽度只用清单值(max 1280/768,min 1241 = 1240+1)',
  preludes.every((p) => {
    const maxs = [...p.matchAll(/max-width:\s*(\d+)px/g)].map((m) => Number(m[1]));
    const mins = [...p.matchAll(/min-width:\s*(\d+)px/g)].map((m) => Number(m[1]));
    return maxs.every((n) => n === 1280 || n === 768) && mins.every((n) => n === 1241);
  }));
const html = fs.readFileSync(new URL('./index.html', import.meta.url), 'utf-8');
check('index.html 恰好一行引入 responsive.css(且在既有样式表之后)',
  (html.match(/responsive\.css/g) || []).length === 1
  && html.includes('<link rel="stylesheet" href="css/responsive.css">')
  && html.indexOf('responsive.css') > html.indexOf('setup.css'));
check('index.html 恰好一行引入 responsive.js',
  (html.match(/responsive\.js/g) || []).length === 1
  && html.includes('<script type="module" src="js/responsive.js"></script>'));
check('viewport 声明 viewport-fit=cover(安全区 env() 生效前提)',
  html.includes('viewport-fit=cover'));
const base = fs.readFileSync(new URL('./css/base.css', import.meta.url), 'utf-8');
check('base.css 断点清单登记了两个新档位(check_css_syntax 的唯一事实源)',
  /--bp-sider-squeeze:\s*1280px/.test(base) && /--bp-single-col:\s*768px/.test(base));

console.log(failed ? `\n${failed} 项断言失败` : '\n全部断言通过');
process.exit(failed ? 1 : 0);
