/* ============================================================
   窄屏 / 分屏响应式状态机(自初始化,零依赖,极小)

   只做 CSS 做不了的三件事,其余全部交给 responsive.css 的媒体查询:
   1. 档位标记:按窗口宽度维护 body.rsp-squeeze / body.rsp-phone;
   2. 挤压档(1241–1280):侧栏「折叠为细条」的手动切换与偏好记忆
      (localStorage,跨断点/跨会话都尊重用户上次的手动选择);
   3. 单栏档(≤768):注入顶部 会话/聊天/面板 三段导航。导航不持有
      任何面板状态,只代点既有 rail 开关(#railSessions/#railDock),
      开合互斥、遮罩收起等语义全部沿用 app.js 的既有状态机;
      按钮高亮通过 MutationObserver 观察 #sider/#dock 的 hidden
      属性回推,与 Ctrl+B、把手、遮罩等其它入口天然同步。

   宽屏(>1280)约定:除 matchMedia 监听外不注入任何 DOM、不加任何
   body 类 —— 细条钮与导航都在首次进入对应档位时才惰性创建。

   断点唯一事实源:base.css :root 的 --bp-*(与 app.js 同一约定,
   CSS 变量读不到时才用镜像回退值)。

   localStorage 键规范:一律 `ia-rsp-` 前缀(沿用项目 `ia-` 惯例);
   目前仅一个键,取值枚举见 SIDER_PREFS。
   ============================================================ */

/** 挤压档侧栏偏好键:'collapsed'(折叠细条)| 'expanded'(常驻展开) */
export const STORE_KEY_SIDER = 'ia-rsp-sider';
export const SIDER_PREFS = ['collapsed', 'expanded'];

/** 断点镜像回退值(与 base.css :root 清单一致,仅 CSS 变量读不到时启用) */
export const FALLBACK_BPS = { squeeze: 1280, drawer: 1240, phone: 768 };

/** 从 base.css :root 读断点(不在 JS 里手写第二份数字) */
export function readBps(win) {
  const cs = win.getComputedStyle(win.document.documentElement);
  const px = (name, fb) => parseInt(cs.getPropertyValue(name), 10) || fb;
  return {
    squeeze: px('--bp-sider-squeeze', FALLBACK_BPS.squeeze),
    drawer: px('--bp-dock-drawer', FALLBACK_BPS.drawer),
    phone: px('--bp-single-col', FALLBACK_BPS.phone),
  };
}

/** 宽度 → 档位:phone ≤768 | drawer 769–1240 | squeeze 1241–1280 | wide >1280 */
export function tierOf(width, bps = FALLBACK_BPS) {
  if (width <= bps.phone) return 'phone';
  if (width <= bps.drawer) return 'drawer';
  if (width <= bps.squeeze) return 'squeeze';
  return 'wide';
}

/** 挤压档侧栏形态:进档默认折叠(分屏就是为了省宽度),
    用户手动展开过('expanded' 偏好)则该档位内持续尊重;其余档位不适用 */
export function siderMode(tier, pref) {
  if (tier !== 'squeeze') return null;
  return pref === 'expanded' ? 'expanded' : 'collapsed';
}

export function init(win) {
  const doc = win.document;
  const body = doc.body;
  const bps = readBps(win);

  /* 隐私模式等场景 localStorage 会抛异常:偏好静默退化为「本次会话内记忆」 */
  let memPref = null;
  const store = {
    get() { try { return win.localStorage.getItem(STORE_KEY_SIDER); } catch { return memPref; } },
    set(v) { memPref = v; try { win.localStorage.setItem(STORE_KEY_SIDER, v); } catch { /* 已有内存兜底 */ } },
  };

  /* 三个档位边界各一个 matchMedia —— 宽屏下本模块的全部常驻痕迹 */
  const mqs = {
    squeeze: win.matchMedia(`(max-width: ${bps.squeeze}px)`),
    drawer: win.matchMedia(`(max-width: ${bps.drawer}px)`),
    phone: win.matchMedia(`(max-width: ${bps.phone}px)`),
  };
  const tier = () =>
    (mqs.phone.matches ? 'phone'
      : mqs.drawer.matches ? 'drawer'
        : mqs.squeeze.matches ? 'squeeze' : 'wide');

  /* ---------------- 挤压档:侧栏折叠钮 ---------------- */
  let pin = null;

  function ensurePin() {
    if (pin) return;
    const hd = doc.querySelector('.sider-hd');
    if (!hd) return;   // 结构缺失(非 index 页)时该功能整体静默失活
    pin = doc.createElement('button');
    pin.className = 'icon-btn rsp-pin';
    pin.setAttribute('type', 'button');
    /* 视觉:朝左双箭头,折叠态由 responsive.css 旋转 180° 示意「展开」 */
    pin.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"'
      + ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
      + '<path d="M11 17l-5-5 5-5M18 17l-5-5 5-5"/></svg>';
    pin.addEventListener('click', () => {
      const next = body.classList.contains('rsp-sider-collapsed') ? 'expanded' : 'collapsed';
      store.set(next);   // 手动选择即偏好:跨断点往返与下次会话都保持
      applySider(tier());
    });
    hd.appendChild(pin);
  }

  function applySider(t) {
    const collapsed = siderMode(t, store.get()) === 'collapsed';
    body.classList.toggle('rsp-sider-collapsed', t === 'squeeze' && collapsed);
    if (!pin) return;
    pin.hidden = t !== 'squeeze';
    const label = collapsed ? '展开会话侧栏(当前折叠为细条,悬停可临时展开)' : '折叠会话侧栏为细条';
    pin.setAttribute('aria-label', label);
    pin.setAttribute('title', label);
    pin.setAttribute('aria-pressed', String(collapsed));
  }

  /* ---------------- 单栏档:顶部三段导航 ---------------- */
  let nav = null;
  const VIEWS = [
    ['sessions', '会话'],
    ['chat', '聊天'],
    ['dock', '面板'],
  ];

  const siderEl = () => doc.getElementById('sider');
  const dockEl = () => doc.getElementById('dock');

  /* 只代点既有开关:开合互斥/遮罩/焦点等语义留在 app.js,一处状态机 */
  function switchView(view) {
    const railSessions = doc.getElementById('railSessions');
    const railDock = doc.getElementById('railDock');
    if (view === 'sessions' && siderEl()?.hidden) railSessions?.click();
    if (view === 'dock' && dockEl()?.hidden) railDock?.click();
    if (view === 'chat') {
      if (siderEl() && !siderEl().hidden) railSessions?.click();
      if (dockEl() && !dockEl().hidden) railDock?.click();
    }
  }

  /** 高亮 = 实际可见性回推:会话页 > 面板页 > 聊天(与打开来源无关) */
  function paintNav() {
    if (!nav) return;
    const active = siderEl() && !siderEl().hidden ? 'sessions'
      : dockEl() && !dockEl().hidden ? 'dock' : 'chat';
    for (const b of nav.children) {
      b.setAttribute('aria-pressed', String(b.dataset.view === active));
    }
  }

  function ensureNav() {
    if (nav) return;
    if (!siderEl() || !dockEl()) return;   // 结构缺失时静默失活
    nav = doc.createElement('nav');
    nav.className = 'rsp-nav';
    nav.setAttribute('aria-label', '视图切换');
    for (const [view, label] of VIEWS) {
      const b = doc.createElement('button');
      b.setAttribute('type', 'button');
      b.dataset.view = view;
      b.textContent = label;
      b.addEventListener('click', () => switchView(view));
      nav.appendChild(b);
    }
    body.appendChild(nav);
    /* hidden 属性由 app.js 的 paintShell 统一落地:观察它即可与
       rail 按钮 / Ctrl+B / 把手 / 遮罩等所有其它入口保持同步 */
    const MO = win.MutationObserver;
    if (MO) {
      const mo = new MO(paintNav);
      mo.observe(siderEl(), { attributes: true, attributeFilter: ['hidden'] });
      mo.observe(dockEl(), { attributes: true, attributeFilter: ['hidden'] });
    }
    paintNav();
  }

  /* ---------------- 档位落地 ---------------- */
  function apply() {
    const t = tier();
    body.classList.toggle('rsp-squeeze', t === 'squeeze');
    body.classList.toggle('rsp-phone', t === 'phone');
    if (t === 'squeeze') ensurePin();
    if (t === 'phone') ensureNav();
    if (nav) {
      nav.hidden = t !== 'phone';
      if (t === 'phone') paintNav();
    }
    applySider(t);
  }

  for (const mq of Object.values(mqs)) mq.addEventListener('change', apply);
  apply();
}

/* 浏览器环境自初始化;responsive.check.mjs 先备好全局桩再 import,
   走的也是这一条路(以 #sider 存在为「应用壳就绪」的判据) */
if (typeof window !== 'undefined'
  && typeof document !== 'undefined'
  && document.getElementById?.('sider')) {
  init(window);
}
