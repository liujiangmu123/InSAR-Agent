/* ============================================================
   skillpanel 的无浏览器自查脚本(node prototype/skillpanel.check.mjs)
   用最小 DOM stub 直接 import js/skillpanel.js,断言三块核心:
   ① Markdown 轻渲染器(结构 + XSS 消毒用例清单);
   ② 技能 store 降级逻辑(列表/详情的 404、网络错、缓存、mock);
   ③ 手风琴状态机与 DOM(互斥展开、aria-expanded/controls、失败高亮)。
   只依赖 node 内建能力,零 npm 依赖(pipelinerail.check.mjs 同款约定)。
   ============================================================ */

/* ---------------- 最小 DOM stub(pipelinerail.check.mjs 同款裁剪) ---------------- */
class NodeBase {
  constructor() { this.childNodes = []; this.parentNode = null; }
  get isConnected() {
    let n = this;
    while (n.parentNode) n = n.parentNode;
    return n === DOC.body;
  }
  get textContent() { return this.childNodes.map((c) => c.textContent).join(''); }
}

class TextNode extends NodeBase {
  constructor(s) { super(); this.nodeType = 3; this.data = String(s); }
  get textContent() { return this.data; }
  set textContent(v) { this.data = String(v); }
  remove() { detach(this); }
}

class Fragment extends NodeBase { constructor() { super(); this.nodeType = 11; } }

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
    this.scrollTop = 0; this.scrollHeight = 0; this.clientHeight = 0;
    this.hidden = false;
  }
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
  setAttribute(k, v) { if (k === 'class') { this.className = v; return; } this.attrs[k] = String(v); }
  getAttribute(k) { return k === 'class' ? this.className : (this.attrs[k] ?? null); }
  appendChild(n) {
    if (n instanceof Fragment) { [...n.childNodes].forEach((c) => this.appendChild(c)); return n; }
    detach(n); n.parentNode = this; this.childNodes.push(n); return n;
  }
  append(...ns) { ns.forEach((n) => this.appendChild(n instanceof NodeBase ? n : new TextNode(n))); }
  replaceChildren(...ns) {
    [...this.childNodes].forEach(detach);
    this.append(...ns.filter((n) => n !== null && n !== undefined));
  }
  remove() { detach(this); }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  removeEventListener(type, fn) { this.listeners[type] = (this.listeners[type] || []).filter((f) => f !== fn); }
  dispatchEvent(evt) {
    evt.target ||= this; evt.currentTarget = this;
    (this.listeners[evt.type] || []).forEach((fn) => fn.call(this, evt));
    return true;
  }
  click() { this.dispatchEvent({ type: 'click', preventDefault() {}, target: this }); }
  get children() { return this.childNodes.filter((c) => c.nodeType === 1); }
  matches(sel) {
    const m = /^([a-z0-9-]*)((?:\.[\w-]+)*)$/.exec(sel);
    if (!m) return false;
    if (m[1] && this.tagName !== m[1]) return false;
    return (m[2].match(/\.[\w-]+/g) || []).every((c) => this._cls.has(c.slice(1)));
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
  body: new Element('body'),
  documentElement: new Element('html'),
  listeners: {},
  createElement: (t) => new Element(t),
  createElementNS: (_ns, t) => new Element(t),
  createTextNode: (s) => new TextNode(s),
  createDocumentFragment: () => new Fragment(),
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); },
  getElementById() { return null; },
  querySelectorAll: () => [],
  querySelector: () => null,
};

globalThis.document = DOC;
globalThis.Node = NodeBase;
globalThis.window = { innerWidth: 1280, innerHeight: 800 };   // 无 MutationObserver → 模块不自启动

/* ---------------- 断言工具(check_frontend.py 解析的既有格式) ---------------- */
let failed = 0;
let count = 0;
function check(name, cond) {
  count += 1;
  if (cond) { console.log(`  ok  ${name}`); }
  else { failed += 1; console.error(`  FAIL ${name}`); }
}

/* ---------------- 序列化(报告样例用) ---------------- */
function serialize(n, indent = '') {
  if (n.nodeType === 3) {
    const s = n.data.trim();
    return s ? `${indent}${s}\n` : '';
  }
  const attrs = [];
  if (n._cls.size) attrs.push(`class="${n.className}"`);
  for (const [k, v] of Object.entries(n.attrs)) attrs.push(v === '' ? k : `${k}="${v}"`);
  if (n.hidden) attrs.push('hidden(prop)');
  const open = `${indent}<${n.tagName}${attrs.length ? ' ' + attrs.join(' ') : ''}>`;
  if (!n.childNodes.length) return `${open}</${n.tagName}>\n`;
  return `${open}\n${n.childNodes.map((c) => serialize(c, indent + '  ')).join('')}${indent}</${n.tagName}>\n`;
}

/* ---------------- 被测模块 ---------------- */
const SK = await import('./js/skillpanel.js');
const {
  SECTION_NAMES, DEFAULT_SECTION, FAIL_SECTION, MOCK_SKILL,
  renderMarkdown, sanitizeHref, hash8, createAccordion, buildAccordion, createSkillStore,
} = SK;

const els = (root) => [...walk(root)];
const tagsOf = (root) => els(root).map((e) => e.tagName);
const anyOnAttr = (root) => els(root).some((e) => Object.keys(e.attrs).some((k) => /^on/i.test(k)));
const WHITELIST = new Set(['div', 'p', 'ul', 'ol', 'li', 'b', 'code', 'a', 'h4', 'h5', 'h6']);

check('node 环境不自启动(无 MutationObserver,只导出纯函数)', SK.initSkillPanel() === false);

/* ---------------- ① Markdown 轻渲染器:结构 ---------------- */
console.log('== ① Markdown 轻渲染器 ==');
const md1 = renderMarkdown([
  '# 标题一', '', '段落一 **粗体** 与 `min_coherence` 行内代码。', '跨行接续。', '',
  '- 甲', '- 乙', '', '1. 一', '2) 二', '', '## 子标题', '### 三级', '正文段。',
].join('\n'));
check('# → h4 / ## → h5 / ### → h6', md1.querySelector('h4')?.textContent === '标题一'
  && md1.querySelector('h5')?.textContent === '子标题' && md1.querySelector('h6')?.textContent === '三级');
check('无序列表 ul>li ×2', md1.querySelectorAll('ul li').length === 2
  && md1.querySelector('ul li').textContent === '甲');
check('有序列表 ol>li ×2(支持 1. 与 2) 两种写法)', md1.querySelectorAll('ol li').length === 2);
check('粗体 b 与行内代码 code', md1.querySelector('b')?.textContent === '粗体'
  && md1.querySelector('code')?.textContent === 'min_coherence');
check('空行分段、相邻行并入同段', md1.querySelectorAll('p').length === 2
  && md1.querySelectorAll('p')[0].textContent.includes('跨行接续'));
check('合法 https 链接:保留 href + rel=noopener noreferrer + target=_blank', (() => {
  const a = renderMarkdown('[文档](https://example.com/a?b=1)').querySelector('a');
  return a && a.getAttribute('href') === 'https://example.com/a?b=1'
    && a.getAttribute('rel') === 'noopener noreferrer' && a.getAttribute('target') === '_blank';
})());
check('hash8:剥 sha256: 前缀取前 8 位;空值给 —',
  hash8('sha256:8c1e4f52a9d0…') === '8c1e4f52' && hash8('abcdef0123') === 'abcdef01' && hash8('') === '—');

/* ---------------- ② XSS 消毒用例清单 ---------------- */
console.log('\n== ② XSS 消毒用例 ==');
const x1 = renderMarkdown('前缀 <script>alert(1)</script> 后缀');
check('XSS-1 <script> 注入:无 script 元素,文本原样保留(不执行)',
  !tagsOf(x1).includes('script') && x1.textContent.includes('<script>alert(1)</script>'));
const x2 = renderMarkdown('<img src=x onerror=alert(1)>');
check('XSS-2 <img onerror> 注入:无 img 元素、树上无任何 on* 属性',
  !tagsOf(x2).includes('img') && !anyOnAttr(x2) && x2.textContent.includes('onerror=alert(1)'));
const x3 = renderMarkdown('[点我](javascript:alert(1))');
check('XSS-3 javascript: 链接:消毒为纯文字,无 <a> 无 href',
  !x3.querySelector('a') && x3.textContent.includes('点我') && !x3.textContent.includes('javascript:'));
const x4 = renderMarkdown('[点我](JaVaScRiPt:alert(1))');
check('XSS-4 javascript: 大小写混淆:同样消毒', !x4.querySelector('a'));
const x5 = renderMarkdown('[点我](data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==)');
check('XSS-5 data: 链接:消毒(协议白名单 http/https/mailto/#)', !x5.querySelector('a'));
const x6 = renderMarkdown('[点我](vbscript:msgbox(1))');
check('XSS-6 vbscript: 链接:消毒', !x6.querySelector('a'));
const x7 = renderMarkdown('代码里的 HTML:`<b>x</b><script>y</script>`');
check('XSS-7 行内代码内的 HTML 字面量不解析(code 内纯文本)',
  x7.querySelector('code')?.textContent === '<b>x</b><script>y</script>'
  && !x7.querySelector('code b') && !tagsOf(x7).includes('script'));
const x8 = renderMarkdown('**加粗**<svg onload=alert(1)>');
check('XSS-8 <svg onload> 注入:无 svg 元素,粗体正常',
  !tagsOf(x8).includes('svg') && x8.querySelector('b')?.textContent === '加粗' && !anyOnAttr(x8));
const x9 = renderMarkdown([
  '# 混合<iframe src=x>', '- 列表项 <object data=x>', '**粗** `c` [ok](https://e.com) [bad](javascript:1)',
].join('\n'));
check('XSS-9 白名单校验:产出标签 ⊆ {div,p,ul,ol,li,b,code,a,h4,h5,h6}',
  tagsOf(x9).every((t) => WHITELIST.has(t)));
check('XSS-10 sanitizeHref 白名单:http/https/mailto/# 通过,其余拒绝',
  sanitizeHref('https://a.b') === 'https://a.b' && sanitizeHref('mailto:x@y.z') === 'mailto:x@y.z'
  && sanitizeHref('#sec') === '#sec' && sanitizeHref(' javascript:alert(1)') === null
  && sanitizeHref('data:text/html,x') === null && sanitizeHref(null) === null);

/* ---------------- ③ 技能 store:降级逻辑与缓存 ---------------- */
console.log('\n== ③ 降级逻辑(404 / 网络错 / 缓存 / mock)==');
function makeFetch(routes) {
  const calls = [];
  const fn = async (url) => {
    calls.push(url);
    const r = routes[url];
    if (!r || r.throw) throw new TypeError('Failed to fetch');
    return { ok: r.status >= 200 && r.status < 300, status: r.status, json: async () => r.body };
  };
  fn.calls = calls;
  return fn;
}

const s404 = createSkillStore({ fetchFn: makeFetch({ '/api/skills': { status: 404 } }) });
check('列表 404 → null(后端未含技能系统 → 不渲染任何技能 UI)', (await s404.getList()) === null);
const sNet = createSkillStore({ fetchFn: makeFetch({}) });
check('列表网络错 → null(静默降级,零报错)', (await sNet.getList()) === null);

const LIST_BODY = { skills: [{ capability: 6, name: 'SNAPHU 相位解缠', version: '1.0.0',
  content_hash: MOCK_SKILL.content_hash, sections: { 适用判据: '摘要' } }] };
const fOk = makeFetch({
  '/api/skills': { status: 200, body: LIST_BODY },
  '/api/skills/6': { status: 200, body: MOCK_SKILL },
  '/api/skills/3': { status: 404 },
  '/api/skills/9': { throw: true },
});
const sOk = createSkillStore({ fetchFn: fOk });
const list = await sOk.getList();
await sOk.getList();
check('列表成功 → Map 按 capability 索引', list instanceof Map && list.get(6)?.name === 'SNAPHU 相位解缠');
check('列表 per-session 只请求一次', fOk.calls.filter((u) => u === '/api/skills').length === 1);
const d6a = await sOk.getDetail(6);
const d6b = await sOk.getDetail('6');
check('详情成功且含五章节', d6a === MOCK_SKILL && SECTION_NAMES.every((n) => n in d6a.sections));
check('详情按步缓存(数字/字符串 id 同键,只请求一次)',
  d6b === d6a && fOk.calls.filter((u) => u === '/api/skills/6').length === 1);
check('详情 404 → null(该步无技能)', (await sOk.getDetail(3)) === null);
await sOk.getDetail(3);
check('详情 404 结果亦缓存(不反复打后端)', fOk.calls.filter((u) => u === '/api/skills/3').length === 1);
check('详情网络错 → null(静默降级)', (await sOk.getDetail(9)) === null);

const fMock = makeFetch({});
const sMock = createSkillStore({ fetchFn: fMock, mockData: MOCK_SKILL });
const mockList = await sMock.getList();
check('mock 模式:列表含第 6 步、零真实请求', mockList?.get(6)?.name === MOCK_SKILL.name && fMock.calls.length === 0);
check('mock 模式:详情第 6 步返回样例,其它步 null',
  (await sMock.getDetail(6)) === MOCK_SKILL && (await sMock.getDetail(3)) === null && fMock.calls.length === 0);
check('mock 样例契约:恰为五个固定章节名', JSON.stringify(Object.keys(MOCK_SKILL.sections)) === JSON.stringify(SECTION_NAMES));
check('mock 样例契约:content_hash 为 sha256:64hex', /^sha256:[0-9a-f]{64}$/.test(MOCK_SKILL.content_hash));

/* ---------------- ④ 手风琴:状态机 + DOM ---------------- */
console.log('\n== ④ 手风琴状态机与 DOM ==');
const acc = createAccordion(SECTION_NAMES, { open: DEFAULT_SECTION });
check('初始:默认展开「参数启发式」', acc.open === DEFAULT_SECTION && acc.isOpen(DEFAULT_SECTION));
acc.toggle(FAIL_SECTION);
check('切换到另一章节:互斥展开(旧章节收起)', acc.isOpen(FAIL_SECTION) && !acc.isOpen(DEFAULT_SECTION));
acc.toggle(FAIL_SECTION);
check('再点已开章节:收起(open=null)', acc.open === null);
acc.toggle('不存在的章节');
check('未知章节:no-op', acc.open === null);
acc.openSection('QA 依据');
acc.openSection('不存在的章节');
check('openSection 只认已注册章节', acc.open === 'QA 依据');
check('open 初值不在章节集时回落为 null', createAccordion(['a', 'b'], { open: 'c' }).open === null);

const secDefs = SECTION_NAMES.map((n) => ({ name: n, md: MOCK_SKILL.sections[n] }));
const toggles = [];
const dom = buildAccordion(secDefs, {
  open: DEFAULT_SECTION, alert: FAIL_SECTION, idBase: 'skl-6',
  onToggle: (name, on) => toggles.push([name, on]),
});
DOC.body.appendChild(dom);
const hds = dom.querySelectorAll('.skl-sec-hd');
const bds = dom.querySelectorAll('.skl-sec-bd');
const hdOf = (name) => hds.find((b) => b.textContent === name);
const secOf = (name) => hdOf(name).parentNode;
check('五个章节按契约顺序渲染', hds.length === 5 && hds.map((b) => b.textContent).join('|') === SECTION_NAMES.join('|'));
check('默认展开「参数启发式」:aria-expanded=true 且正文可见',
  hdOf(DEFAULT_SECTION).getAttribute('aria-expanded') === 'true'
  && !secOf(DEFAULT_SECTION).querySelector('.skl-sec-bd').hidden);
check('其余章节收起(aria-expanded=false + hidden)',
  SECTION_NAMES.filter((n) => n !== DEFAULT_SECTION).every((n) =>
    hdOf(n).getAttribute('aria-expanded') === 'false' && secOf(n).querySelector('.skl-sec-bd').hidden));
check('aria-controls ↔ 正文 id/role/aria-labelledby 接线',
  hds.every((b, i) => b.getAttribute('aria-controls') === bds[i].getAttribute('id')
    && bds[i].getAttribute('role') === 'region'
    && bds[i].getAttribute('aria-labelledby') === b.getAttribute('id')));
check('失败章节带 is-alert 高亮类', secOf(FAIL_SECTION).matches('section.is-alert'));
check('正文为渲染后的 Markdown(参数章节含 code 与 b)',
  !!secOf(DEFAULT_SECTION).querySelector('.skl-md code') && !!secOf(DEFAULT_SECTION).querySelector('.skl-md b'));

hdOf(FAIL_SECTION).click();
check('点击「常见失败与处置」:该章节展开、原章节收起(DOM 与状态联动)',
  hdOf(FAIL_SECTION).getAttribute('aria-expanded') === 'true'
  && !secOf(FAIL_SECTION).querySelector('.skl-sec-bd').hidden
  && hdOf(DEFAULT_SECTION).getAttribute('aria-expanded') === 'false'
  && secOf(DEFAULT_SECTION).querySelector('.skl-sec-bd').hidden);
hdOf(FAIL_SECTION).click();
check('再点同章节:全部收起', hds.every((b) => b.getAttribute('aria-expanded') === 'false'));
check('onToggle 回调序列正确(开/开/关)', JSON.stringify(toggles)
  === JSON.stringify([[FAIL_SECTION, true], [FAIL_SECTION, false]]));
check('章节头为原生 button(键盘可达,Enter/Space 由浏览器语义保证)',
  hds.every((b) => b.tagName === 'button' && b.getAttribute('type') === 'button'));

/* ---------------- 输出关键结构样例 ---------------- */
console.log('\n== 关键结构样例 ==\n');
console.log('--- Markdown 渲染(XSS 混合样例 x9)---');
process.stdout.write(serialize(x9));
console.log('--- 手风琴(前 2 个章节,节选)---');
const demo = buildAccordion(secDefs.slice(0, 2), { open: DEFAULT_SECTION, idBase: 'demo' });
process.stdout.write(serialize(demo).split('\n').slice(0, 30).join('\n') + '\n  …\n');

console.log(failed ? `\n${count} 项断言,${failed} 项失败` : `\n全部 ${count} 项断言通过`);
process.exit(failed ? 1 : 0);
