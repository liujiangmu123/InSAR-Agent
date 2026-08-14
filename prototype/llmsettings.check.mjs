/* ============================================================
   llmsettings 的无浏览器自查脚本(node prototype/llmsettings.check.mjs)
   用最小 DOM stub + fetch 桩直接 import llmsettings.js(零真实网络),断言:
   ① 自初始化:顶栏齿轮 + composer 提示行常驻 chip(button 语义 / aria / 位置);
   ② chipView 三态文案纯函数(双模型 / 仅对话 / 未配置);
   ③ CustomEvent('llm:config-changed') → chip 即时刷新;
   ④ fillSelect:种子回显(已保存)/ 完整清单并入 / 选中保持 / 识图过滤 / 缺席补项;
   ⑤ 面板状态机 · 未存配置:下拉禁用占位 → 获取后启用;
   ⑥ 面板状态机 · 已存配置:打开即回显 → 获取无缝并入 → 改选保持 → 保存联动 chip;
   ⑦ 重开面板以服务端为准回显最新保存值;已存模型不在清单时补「(已保存)」项;
   ⑧ 自主循环配置(契约 §8):normCycles 纯函数、开关/周期上限回显与保存载荷、
     留空 = null(保留旧值)、越界输入客户端夹取、服务端回执回写控件。
   放在 prototype/ 根目录:check_frontend.py 只发现该层的 *.check.mjs。
   只依赖 node 内建能力,零 npm 依赖。
   ============================================================ */

/* ---------------- 最小 DOM stub(emptystate.check.mjs 同款 + select/innerHTML 扩展) ---------------- */
class NodeBase {
  constructor() { this.childNodes = []; this.parentNode = null; }
  get textContent() { return this.childNodes.map((c) => c.textContent).join(''); }
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
    this.disabled = false;
    /* select 的 value 走原型 getter/setter;其余控件用普通数据属性 */
    if (this.tagName === 'input' || this.tagName === 'textarea' || this.tagName === 'option') {
      this.value = ''; this.placeholder = '';
    }
    if (this.tagName === 'option') this.selected = false;
  }
  get id() { return this.attrs.id || ''; }
  set id(v) { this.attrs.id = String(v); }
  get className() { return [...this._cls].join(' '); }
  set className(v) { this._cls = new Set(String(v).split(/\s+/).filter(Boolean)); }
  get classList() {
    const s = this._cls;
    return { add: (...cs) => cs.forEach((c) => s.add(c)), contains: (c) => s.has(c) };
  }
  setAttribute(k, v) { if (k === 'class') { this.className = v; return; } this.attrs[k] = String(v); }
  getAttribute(k) { return k === 'class' ? this.className : (this.attrs[k] ?? null); }
  appendChild(n) { detach(n); n.parentNode = this; this.childNodes.push(n); return n; }
  append(...ns) { ns.forEach((n) => this.appendChild(n instanceof NodeBase ? n : new TextNode(n))); }
  insertBefore(n, ref) {
    detach(n); n.parentNode = this;
    const i = ref ? this.childNodes.indexOf(ref) : -1;
    if (i < 0) this.childNodes.push(n); else this.childNodes.splice(i, 0, n);
    return n;
  }
  prepend(...ns) {
    ns.reverse().forEach((n) => this.insertBefore(
      n instanceof NodeBase ? n : new TextNode(n), this.childNodes[0] || null));
  }
  replaceChildren(...ns) {
    [...this.childNodes].forEach(detach);
    this.append(...ns.filter((n) => n !== null && n !== undefined));
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
  set innerHTML(v) {
    [...this.childNodes].forEach(detach);
    if (String(v).trim()) parseHTML(String(v), this);
  }
  matches(sel) {
    // 支持「tag#id.cls[attr] / [attr="值"]」复合选择器(本脚本与 llmsettings.js 的用量足够)
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

class SelectElement extends Element {
  get options() { return this.children.filter((c) => c.tagName === 'option'); }
  /* 单选语义:有 selected 标记的选项胜出,否则回落第一项(浏览器默认) */
  get value() {
    const opts = this.options;
    const on = opts.filter((o) => o.selected);
    return on.length ? on[on.length - 1].value : (opts.length ? opts[0].value : '');
  }
  set value(v) { for (const o of this.options) o.selected = (o.value === String(v)); }
}

function* walk(n) { for (const c of n.children) { yield c; yield* walk(c); } }

/* 迷你 HTML 解析器:够解析 buildDialog 模板与齿轮按钮的 svg(双引号属性、
   自闭合、void 元素);布尔属性(disabled/selected)与 value/placeholder 映射成属性。 */
const VOID = new Set(['input', 'br', 'hr', 'img', 'path', 'circle', 'rect',
  'line', 'polyline', 'polygon', 'ellipse']);
function parseHTML(html, parent) {
  const re = /<(\/)?([a-zA-Z][\w-]*)((?:"[^"]*"|[^>"])*?)\s*(\/)?>/g;
  const stack = [parent];
  let last = 0, m;
  while ((m = re.exec(html))) {
    const text = html.slice(last, m.index);
    if (text.trim()) {
      stack[stack.length - 1].appendChild(new TextNode(text.replace(/\s+/g, ' ').trim()));
    }
    last = re.lastIndex;
    if (m[1]) { if (stack.length > 1) stack.pop(); continue; }
    const el = DOC.createElement(m[2]);
    const attrRe = /([\w:-]+)(?:\s*=\s*"([^"]*)")?/g;
    let a;
    while ((a = attrRe.exec(m[3] || ''))) {
      const k = a[1], v = a[2] === undefined ? '' : a[2];
      el.setAttribute(k, v);
      if (k === 'disabled' || k === 'hidden' || k === 'selected' || k === 'checked') el[k] = true;
      else if (k === 'placeholder') el.placeholder = v;
      else if (k === 'value' && el.tagName !== 'select') el.value = v;
      else if (k === 'type' && (el.tagName === 'button' || el.tagName === 'input')) el.type = v;
    }
    stack[stack.length - 1].appendChild(el);
    if (!m[4] && !VOID.has(el.tagName)) stack.push(el);
  }
  const tail = html.slice(last);
  if (tail.trim()) stack[stack.length - 1].appendChild(new TextNode(tail.trim()));
}

const DOC = {
  readyState: 'complete',
  activeElement: null,
  body: new Element('body'),
  listeners: {},
  createElement: (t) => (String(t).toLowerCase() === 'select'
    ? new SelectElement(t) : new Element(t)),
  createTextNode: (s) => new TextNode(s),
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); },
  dispatchEvent(evt) {
    (this.listeners[evt.type] || []).forEach((fn) => fn(evt));
    return true;
  },
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
globalThis.CustomEvent = class {
  constructor(type, opts = {}) { this.type = type; this.detail = opts.detail ?? null; }
};

/* ---------------- fetch 桩:全部 /api/llm/* 走内存路由,零真实网络 ---------------- */
const routes = {
  config: null,     // GET /api/llm/config 的返回体
  models: null,     // POST /api/llm/models 的返回体
  saveView: null,   // POST /api/llm/config 的返回体(服务端保存后的视图)
};
const calls = [];   // {url, method, body} 供断言请求载荷
globalThis.fetch = async (url, opts = {}) => {
  const method = opts.method || 'GET';
  calls.push({ url: String(url), method, body: opts.body ? JSON.parse(opts.body) : null });
  const json = (data) => ({ ok: true, status: 200, json: async () => data });
  if (String(url).endsWith('/api/llm/config') && method === 'GET') return json(routes.config);
  if (String(url).endsWith('/api/llm/config') && method === 'POST') return json(routes.saveView);
  if (String(url).endsWith('/api/llm/models')) return json(routes.models);
  throw new Error(`意外的网络请求:${method} ${url}`);
};

/* ---------------- 断言工具(check_frontend 解析约定:两空格 ok/FAIL) ---------------- */
let failed = 0;
let count = 0;
function check(name, cond) {
  count += 1;
  if (cond) { console.log(`  ok  ${name}`); }
  else { failed += 1; console.error(`  FAIL ${name}`); }
}
const tick = () => new Promise((r) => setTimeout(r, 0));

/* ---------------- 页面骨架(index.html 的最小等价物)+ 被测模块导入 ---------------- */
const topbar = new Element('header'); topbar.className = 'topbar';
const status = new Element('span'); status.setAttribute('id', 'status');
topbar.appendChild(status);
DOC.body.appendChild(topbar);

const composer = new Element('div'); composer.className = 'composer';
const composerIn = new Element('div'); composerIn.className = 'composer-in';
const cmeta = new Element('div'); cmeta.className = 'cmeta';
const hint = new Element('span'); hint.textContent = 'Enter 发送';
cmeta.appendChild(hint);
composerIn.appendChild(cmeta); composer.appendChild(composerIn);
DOC.body.appendChild(composer);

// 导入时后端返回「未配置」:chip 初始与服务端一致
routes.config = { configured: false, source: 'none',
  base_url: 'https://tokenrhythm.studio/v1',
  chat_model: '', vision_model: '', api_key_masked: '' };

const L = await import('./js/llmsettings.js');
await tick(); await tick();

/* ---------------- ① 自初始化:齿轮 + 常驻 chip ---------------- */
console.log('== ① 自初始化:齿轮 + 常驻 chip ==');
const gear = DOC.getElementById('btnLlmSettings');
const chip = DOC.getElementById('llmModelChip');
check('顶栏齿轮按钮注入(#status 之前)', !!gear
  && topbar.children.indexOf(gear) === topbar.children.indexOf(status) - 1);
check('chip 注入 composer 提示行(.cmeta 首位)', !!chip
  && chip.parentNode === cmeta && cmeta.firstChild === chip);
check('chip 是 button 语义(type=button)', chip?.tagName === 'button' && chip?.type === 'button');
check('未配置 → chip 文案「⚙ 未配置模型」+ warn 色调',
  chip?.textContent === '⚙ 未配置模型' && chip?.dataset.tone === 'warn');
check('未配置 → aria-label「模型设置:尚未配置模型」',
  chip?.getAttribute('aria-label') === '模型设置:尚未配置模型');

/* ---------------- ② chipView 三态文案(纯函数) ---------------- */
console.log('\n== ② chipView 三态文案 ==');
const dual = L.chipView({ chat_model: 'deepseek-v4-flash', vision_model: 'kimi-k2.5' });
check('双模型:文案「⚙ 对话 · 识图 xx」', dual.text === '⚙ deepseek-v4-flash · 识图 kimi-k2.5');
check('双模型:aria 完整(对话/识图各自点名)',
  dual.aria === '模型设置:当前对话模型 deepseek-v4-flash,识图模型 kimi-k2.5' && dual.tone === '');
const solo = L.chipView({ chat_model: 'deepseek-v4-flash', vision_model: '' });
check('仅对话:文案「⚙ 对话模型」', solo.text === '⚙ deepseek-v4-flash');
check('仅对话:aria 注明未启用识图',
  solo.aria === '模型设置:当前对话模型 deepseek-v4-flash,未启用识图' && solo.tone === '');
const none = L.chipView({});
check('未配置:文案 + warn 色调', none.text === '⚙ 未配置模型' && none.tone === 'warn');
check('空入参(null)按未配置处理', L.chipView(null).tone === 'warn');
check('只存识图缺对话 → 仍视为未配置(后端 configured 同判)',
  L.chipView({ chat_model: '', vision_model: 'kimi-k2.5' }).tone === 'warn');

/* ---------------- ③ CustomEvent('llm:config-changed') → chip 即时刷新 ---------------- */
console.log('\n== ③ CustomEvent 刷新 ==');
DOC.dispatchEvent(new CustomEvent('llm:config-changed',
  { detail: { chat_model: 'deepseek-v4-flash', vision_model: 'kimi-k2.5' } }));
check('事件(双模型)→ chip 文案与 aria 同步更新',
  chip.textContent === '⚙ deepseek-v4-flash · 识图 kimi-k2.5'
  && chip.getAttribute('aria-label') === '模型设置:当前对话模型 deepseek-v4-flash,识图模型 kimi-k2.5');
check('事件(双模型)→ warn 色调撤除', chip.dataset.tone === undefined);
DOC.dispatchEvent(new CustomEvent('llm:config-changed', { detail: { chat_model: 'glm-5' } }));
check('事件(仅对话)→ 文案「⚙ glm-5」', chip.textContent === '⚙ glm-5');
DOC.dispatchEvent(new CustomEvent('llm:config-changed', { detail: {} }));
check('事件(清空)→ 回到未配置 warn 态',
  chip.textContent === '⚙ 未配置模型' && chip.dataset.tone === 'warn');

/* ---------------- ④ fillSelect:回显 / 并入 / 保持 ---------------- */
console.log('\n== ④ fillSelect 渲染与选中保持 ==');
const MODELS = [
  { id: 'deepseek-v4-flash', vision: false, price_in: 1.5, price_out: 6, currency: 'CNY' },
  { id: 'kimi-k2.5', vision: true, price_in: 4, price_out: 16, currency: 'CNY' },
  { id: 'glm-5v', vision: true },
  { id: 'qwen4-max', vision: false },
];
const s1 = DOC.createElement('select');
L.fillSelect(s1, [], 'deepseek-v4-flash', false);
check('种子回显:空清单 →「(未选择)+ 已存(已保存)」两项',
  s1.options.length === 2 && s1.options[0].textContent === '(未选择)'
  && s1.options[1].textContent === 'deepseek-v4-flash(已保存)');
check('种子回显:已存模型处于选中态', s1.value === 'deepseek-v4-flash' && s1.options[1].selected);
const s2 = DOC.createElement('select');
L.fillSelect(s2, [], '', true);
check('种子回显(识图未存):仅「(不启用识图)」一项、值为空',
  s2.options.length === 1 && s2.options[0].textContent === '(不启用识图)' && s2.value === '');
L.fillSelect(s1, MODELS, s1.value, false);
check('完整清单并入:选项 = 空 + 全部模型,选中保持',
  s1.options.length === 5 && s1.value === 'deepseek-v4-flash');
check('并入后不再带「(已保存)」标注,价格如常渲染',
  s1.options[1].textContent === 'deepseek-v4-flash · ¥1.5/6 每百万');
const s3 = DOC.createElement('select');
L.fillSelect(s3, MODELS, 'kimi-k2.5', true);
check('识图下拉只留 vision 模型(2/4)+ 标注「· 识图」',
  s3.options.length === 3 && s3.value === 'kimi-k2.5'
  && s3.options.slice(1).every((o) => o.textContent.includes('· 识图')));
const s4 = DOC.createElement('select');
L.fillSelect(s4, MODELS, '已下线的旧模型', false);
check('已存模型不在清单 → 尾部补「xxx(已保存)」且选中',
  s4.options.length === 6 && s4.value === '已下线的旧模型'
  && s4.options[5].textContent === '已下线的旧模型(已保存)');
const s5 = DOC.createElement('select');
L.fillSelect(s5, MODELS, '', false);
check('current 为空 → 空选项兜底选中', s5.value === '');

/* ---------------- ⑤ 面板状态机 · 未存配置 ---------------- */
console.log('\n== ⑤ 面板状态机 · 未存配置 ==');
gear.click();
await tick(); await tick();
let dlg = DOC.querySelector('.llmset');
const F = (name) => dlg.querySelector(`[name="${name}"]`);
check('齿轮点击 → 面板打开', !!dlg);
check('未存配置:两下拉保持禁用 + 占位「先获取模型列表」',
  F('chat_model').disabled && F('vision_model').disabled
  && F('chat_model').options.length === 1
  && F('chat_model').options[0].textContent === '先获取模型列表');
check('未存配置:提示行不出现「已加载保存的配置」', F('fetchNote').textContent === '');
routes.models = { ok: true, models: MODELS };
F('fetch').click();
await tick(); await tick();
check('获取列表 → 下拉启用、空选项兜底选中',
  !F('chat_model').disabled && F('chat_model').options.length === 5
  && F('chat_model').value === '' && F('vision_model').options.length === 3);
check('获取列表 → 提示行给出数量统计', F('fetchNote').textContent === '4 个模型(2 个支持识图)');
dlg.querySelector('.llmset-x').click();
check('关闭 → 面板移除', DOC.querySelector('.llmset-overlay') === null);

/* ---------------- ⑥ 面板状态机 · 已存配置(打开即回显) ---------------- */
console.log('\n== ⑥ 面板状态机 · 已存配置 ==');
routes.config = { configured: true, source: 'file', base_url: 'https://x/v1',
  chat_model: 'deepseek-v4-flash', vision_model: 'kimi-k2.5',
  api_key_masked: 'sk_tr_r7…hOUE' };
chip.click();
await tick(); await tick();
dlg = DOC.querySelector('.llmset');
check('chip 点击 → 打开设置面板', !!dlg);
check('打开即回显:对话下拉启用,「(未选择)+ 已存(已保存)」且选中',
  !F('chat_model').disabled && F('chat_model').options.length === 2
  && F('chat_model').value === 'deepseek-v4-flash'
  && F('chat_model').options[1].textContent === 'deepseek-v4-flash(已保存)');
check('打开即回显:识图下拉同理',
  !F('vision_model').disabled && F('vision_model').value === 'kimi-k2.5'
  && F('vision_model').options[1].textContent === 'kimi-k2.5(已保存)');
check('提示行改为「已加载保存的配置;点『获取模型列表』…」',
  F('fetchNote').textContent === '已加载保存的配置;点『获取模型列表』可查看全部可选模型与价格');
check('底部注记「当前配置可用」', F('saveNote').textContent === '当前配置可用');
check('接入地址回填 + 密钥只见掩码占位',
  F('base_url').value === 'https://x/v1'
  && F('api_key').placeholder === 'sk_tr_r7…hOUE(留空 = 沿用)' && F('api_key').value === '');

F('fetch').click();
await tick(); await tick();
check('获取列表无缝并入:全部模型可选,选中态保持',
  F('chat_model').options.length === 5 && F('chat_model').value === 'deepseek-v4-flash'
  && F('vision_model').options.length === 3 && F('vision_model').value === 'kimi-k2.5');
check('并入后已存项不再带「(已保存)」(清单里有它)',
  F('chat_model').options.every((o) => !o.textContent.includes('(已保存)')));
F('chat_model').value = 'qwen4-max';
F('fetch').click();
await tick(); await tick();
check('用户改选后再获取 → 改选结果保持(不回跳已存值)',
  F('chat_model').value === 'qwen4-max');

routes.saveView = { configured: true, source: 'file', base_url: 'https://x/v1',
  chat_model: 'qwen4-max', vision_model: 'kimi-k2.5', api_key_masked: 'sk_tr_r7…hOUE' };
F('save').click();
await tick(); await tick();
const saved = calls.filter((c) => c.method === 'POST' && c.url.endsWith('/api/llm/config'));
check('保存载荷:改选的模型上行,密钥留空 = null(沿用已存)',
  saved.length === 1 && saved[0].body.chat_model === 'qwen4-max'
  && saved[0].body.vision_model === 'kimi-k2.5' && saved[0].body.api_key === null);
check('保存成功文案 + ok 色调',
  F('saveNote').textContent === '已保存,配置可用(新会话生效)'
  && F('saveNote').dataset.tone === 'ok');
check('保存成功 → chip 即时刷新(llm:config-changed 联动)',
  chip.textContent === '⚙ qwen4-max · 识图 kimi-k2.5'
  && chip.getAttribute('aria-label') === '模型设置:当前对话模型 qwen4-max,识图模型 kimi-k2.5'
  && chip.dataset.tone === undefined);
check('会话内缓存(dlg.dataset)随保存视图更新',
  dlg.dataset.chatModel === 'qwen4-max' && dlg.dataset.visionModel === 'kimi-k2.5');
dlg.querySelector('.llmset-x').click();

/* ---------------- ⑦ 重开面板:以服务端为准回显最新保存值 ---------------- */
console.log('\n== ⑦ 重开面板回显最新保存值 ==');
routes.config = { ...routes.saveView };
chip.click();
await tick(); await tick();
dlg = DOC.querySelector('.llmset');
check('重开即回显最新保存的模型(不再要求先获取列表)',
  F('chat_model').value === 'qwen4-max'
  && F('chat_model').options[1].textContent === 'qwen4-max(已保存)'
  && F('vision_model').value === 'kimi-k2.5');
routes.models = { ok: true, models: MODELS.filter((m) => m.id !== 'qwen4-max') };
F('fetch').click();
await tick(); await tick();
check('已存模型已从清单下线 → 补「(已保存)」项并保持选中',
  F('chat_model').value === 'qwen4-max'
  && F('chat_model').options.some((o) => o.textContent === 'qwen4-max(已保存)'));
dlg.querySelector('.llmset-x').click();
check('全程无 /api/llm/test 真实调用(验证只走 UI 状态逻辑)',
  calls.every((c) => !c.url.includes('/api/llm/test')));

/* ---------------- ⑧ 自主循环配置(agent_loop / agent_max_cycles,契约 §8) ---------------- */
console.log('\n== ⑧ 自主循环配置 ==');
check('normCycles:合法值整数化原样', L.normCycles('6') === 6 && L.normCycles(3) === 3
  && L.normCycles('7.9') === 7);
check('normCycles:越界夹进 1..48 闭区间', L.normCycles('0') === 1
  && L.normCycles('99') === 48 && L.normCycles(-3) === 1);
check('normCycles:空/空白/非数 → null(= 服务端保留旧值)',
  L.normCycles('') === null && L.normCycles('  ') === null
  && L.normCycles('abc') === null && L.normCycles(null) === null);

// 旧后端形态:config 不带循环两键 → 控件维持 HTML 缺省(开 + 6)
chip.click();
await tick(); await tick();
dlg = DOC.querySelector('.llmset');
check('config 缺两键 → 开关默认勾选、周期上限默认 24',
  F('agent_loop').checked === true && F('agent_max_cycles').value === '24');
check('控件语义:checkbox + number[min=1][max=48]',
  F('agent_loop').getAttribute('type') === 'checkbox'
  && F('agent_max_cycles').getAttribute('type') === 'number'
  && F('agent_max_cycles').getAttribute('min') === '1'
  && F('agent_max_cycles').getAttribute('max') === '48');
dlg.querySelector('.llmset-x').click();

// 服务端带两键 → 打开即回显
routes.config = { ...routes.config, agent_loop: false, agent_max_cycles: 9 };
chip.click();
await tick(); await tick();
dlg = DOC.querySelector('.llmset');
check('回显:agent_loop=false → 开关不勾选', F('agent_loop').checked === false);
check('回显:agent_max_cycles=9 → 数字框为 9', F('agent_max_cycles').value === '9');

// 保存:开关布尔上行;越界输入先夹到 48;服务端回执回写控件
F('agent_loop').checked = true;
F('agent_max_cycles').value = '99';
routes.saveView = { ...routes.saveView, agent_loop: true, agent_max_cycles: 48 };
F('save').click();
await tick(); await tick();
let loopPosts = calls.filter((c) => c.method === 'POST' && c.url.endsWith('/api/llm/config'));
let loopBody = loopPosts[loopPosts.length - 1].body;
check('保存载荷:agent_loop 上行布尔 true', loopBody.agent_loop === true);
check('保存载荷:越界 99 已夹到 48 再上行', loopBody.agent_max_cycles === 48);
check('保存回执回写:数字框 = 服务端回执 48', F('agent_max_cycles').value === '48');

// 数字框留空 → 上行 null(保留旧值);回执现值回写控件
F('agent_max_cycles').value = '';
F('save').click();
await tick(); await tick();
loopPosts = calls.filter((c) => c.method === 'POST' && c.url.endsWith('/api/llm/config'));
loopBody = loopPosts[loopPosts.length - 1].body;
check('数字框留空 → 上行 null(服务端保留旧值)', loopBody.agent_max_cycles === null);
check('留空保存后数字框回到服务端现值', F('agent_max_cycles').value === '48');
dlg.querySelector('.llmset-x').click();

/* ---------------- 三态文案表(评审用) ---------------- */
console.log('\n== 常驻 chip 三态文案表 ==');
for (const [name, cfg] of [
  ['双模型', { chat_model: 'deepseek-v4-flash', vision_model: 'kimi-k2.5' }],
  ['仅对话', { chat_model: 'deepseek-v4-flash' }],
  ['未配置', {}],
]) {
  const v = L.chipView(cfg);
  console.log(`  ${name}:「${v.text}」 aria「${v.aria}」 tone=${v.tone || '(中性)'}`);
}

console.log(failed ? `\n${count} 项断言,${failed} 项失败` : `\n全部 ${count} 项断言通过`);
process.exit(failed ? 1 : 0);
