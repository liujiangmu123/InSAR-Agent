/* ============================================================
   流式回复(0814B 契约 §1.4)单测 —— 被测:prototype/js/stream.js
   的 createLiveSay(liveSay 状态机,纯逻辑)与 agentMsgStream(气泡)。

   A. 状态机(注入假气泡工厂,零 DOM):
      ① delta→say:首个 delta 开流,后续追加同一手柄,终帧整体替换并关闭;
      ② delta→abort:半截标废(label 透传)并关闭,abort 幂等;
      ③ note 穿行:其他事件不经状态机,前后 delta 仍写同一气泡;
      ④ 无 delta 的 say:finalize 返回 false 且不开流(调用方原样 agentMsg);
      ⑤ 回合结束清理:dangling 流被兜底 abort 标废;已定稿后兜底是空操作。
   B. 气泡 DOM(最小 DOM stub,与 agent-ux.check.mjs / fail-demo.check.mjs
      同源裁剪):append 纯文本累积、finalize 节点替换、abort 保留半截 +
      「(回复中断,内容不完整)」标注、is-streaming/is-aborted 类切换、
      近底部才跟随滚动(上滚阅读时 append 不抢 scrollTop)。

   app.js 侧的接线(case 'say.delta'/'say.abort'、submit finally 兜底)
   由 e2e 契约测试与 demo-mode.check.mjs 的 case 闭集断言看护。
   ============================================================ */
import { test } from 'node:test';
import assert from 'node:assert/strict';

/* ---------------- 最小 DOM stub(agent-ux.check.mjs 同源裁剪版) ---------------- */
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
  }
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
  dispatchEvent(evt) { (this.listeners[evt.type] || []).forEach((fn) => fn.call(this, evt)); return true; }
  set textContent(v) { this.replaceChildren(new TextNode(v)); }
  get textContent() { return super.textContent; }
  get children() { return this.childNodes.filter((c) => c.nodeType === 1); }
  matches(sel) {
    const m = /^([a-z0-9-]*)((?:\.[\w-]+)*)$/.exec(sel);
    if (!m) return false;
    if (m[1] && this.tagName !== m[1]) return false;
    return (m[2].match(/\.[\w-]+/g) || []).every((c) => this._cls.has(c.slice(1)));
  }
  querySelectorAll(sel) {
    const out = [];
    for (const d of walk(this)) if (d.matches(sel)) out.push(d);
    return out;
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}

function* walk(n) { for (const c of n.children) { yield c; yield* walk(c); } }

globalThis.document = {
  readyState: 'loading',            // dom.js 顶层按此走 no-op 的 DOMContentLoaded 分支
  addEventListener() {},
  body: new Element('body'),
  documentElement: { dataset: {}, style: { setProperty() {} } },
  createElement: (t) => new Element(t),
  createTextNode: (s) => new TextNode(s),
  createDocumentFragment: () => new Fragment(),
  getElementById: () => null,
};
globalThis.Node = NodeBase;
globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
globalThis.requestAnimationFrame = (fn) => { fn(); return 0; };   // follow() 同步落地

// stub 必须先于模块求值,故动态 import(静态 import 会被提升到 stub 之前)
const Stream = await import('../../prototype/js/stream.js');
const { h, txt } = await import('../../prototype/js/dom.js');

const host = new Element('div');
Stream.mount(host);
const inner = () => host.querySelector('.stream-inner');

/* ============================================================
   A. liveSay 状态机(纯逻辑:注入假气泡工厂,记录调用序列)
   ============================================================ */

/** 假气泡手柄:记录 append/finalize/abort 的调用,行为与真手柄同构。 */
function fakeOpen() {
  const opened = [];
  const open = () => {
    const hnd = { chunks: [], final: null, aborted: null, closed: false };
    hnd.append = (c) => { if (!hnd.closed) hnd.chunks.push(c); };
    hnd.finalize = (nodes) => { if (!hnd.closed) { hnd.closed = true; hnd.final = nodes; } };
    hnd.abort = (label) => { if (!hnd.closed) { hnd.closed = true; hnd.aborted = { label }; } };
    opened.push(hnd);
    return hnd;
  };
  return { open, opened };
}

test('① delta→say:首个 delta 开流,后续追加同一气泡,终帧整体替换并关闭', () => {
  const { open, opened } = fakeOpen();
  const live = Stream.createLiveSay(open);

  assert.equal(live.active(), false);
  live.delta('分析');
  live.delta('中…');
  assert.equal(opened.length, 1);                       // 只开一个气泡
  assert.deepEqual(opened[0].chunks, ['分析', '中…']);  // 增量按序追加
  assert.equal(live.active(), true);

  const nodes = ['定稿节点'];
  assert.equal(live.finalize(nodes), true);             // 有流:接管终帧
  assert.equal(opened[0].final, nodes);                 // 整体替换(引用透传)
  assert.equal(live.active(), false);                   // say 关闭状态机

  live.delta('新回合');                                  // 关闭后再 delta → 新气泡
  assert.equal(opened.length, 2);
});

test('② delta→abort:半截标废(label 透传)并关闭;abort 幂等不重复标废', () => {
  const { open, opened } = fakeOpen();
  const live = Stream.createLiveSay(open);

  live.delta('查询 ASF 数据');
  live.abort('(回复被截断,内容不完整)');                 // say.abort 终帧
  assert.deepEqual(opened[0].aborted, { label: '(回复被截断,内容不完整)' });
  assert.equal(live.active(), false);                   // say.abort 关闭状态机

  live.abort('(已停止,回复未完成)');                     // submit finally 兜底再关一次
  assert.equal(opened.length, 1);                       // 不开新气泡
  assert.deepEqual(opened[0].aborted,
    { label: '(回复被截断,内容不完整)' });               // 首次标废不被覆盖(幂等)
});

test('③ note 穿行不打断:其他事件不经状态机,前后 delta 仍写同一气泡', () => {
  const { open, opened } = fakeOpen();
  const live = Stream.createLiveSay(open);

  live.delta('第一段');
  // consume() 里 note/tool.start 等分支不触碰 liveSay —— 状态机零调用,
  // 等价于此处什么都不做;随后的 delta 必须仍落在同一气泡
  live.delta('第二段');
  assert.equal(opened.length, 1);
  assert.deepEqual(opened[0].chunks, ['第一段', '第二段']);
  assert.equal(live.active(), true);                    // 穿行事件不关闭状态机
});

test('④ 无 delta 的 say:finalize 返回 false 且不开流(调用方走原样 agentMsg)', () => {
  const { open, opened } = fakeOpen();
  const live = Stream.createLiveSay(open);

  assert.equal(live.finalize(['非流式回复']), false);   // 无流:不接管
  assert.equal(opened.length, 0);                       // 绝不凭空开气泡
  assert.equal(live.active(), false);
});

test('⑤ 回合结束清理:dangling 流兜底标废;已定稿后兜底是空操作', () => {
  const { open, opened } = fakeOpen();
  const live = Stream.createLiveSay(open);

  // 流没等到终帧就断了(协议异常/服务端崩溃)→ consume 尾部 abort() 兜底
  live.delta('半截回');
  live.abort();                                         // 无 label → 气泡侧用缺省标注
  assert.deepEqual(opened[0].aborted, { label: undefined });
  assert.equal(live.active(), false);

  // 正常定稿的回合:兜底 abort 不得把定稿消息二次标废
  live.delta('完整回复');
  live.finalize(['完整回复']);
  live.abort();
  assert.equal(opened[1].aborted, null);
  assert.ok(opened[1].final);
});

/* ============================================================
   B. agentMsgStream 气泡 DOM(最小 stub)
   ============================================================ */

test('append:纯文本累积(不解析 HTML),气泡带 is-streaming', () => {
  const s = Stream.agentMsgStream();
  assert.ok(s.el.classList.contains('is-streaming'));
  assert.ok(s.el.classList.contains('a-msg'));
  assert.equal(s.el.parentNode, inner());               // push 进流

  s.append('识别到 <b>Ridgecrest</b>');
  s.append(' 2019 地震');
  const body = s.el.querySelector('.body');
  // 单文本节点承载:HTML 原样呈现为字符,天然免注入
  assert.equal(body.textContent, '识别到 <b>Ridgecrest</b> 2019 地震');
  assert.equal(body.childNodes.filter((n) => n.nodeType === 3).length, 1);
});

test('finalize:renderPart 节点整体替换,is-streaming 移除且不标废', () => {
  const s = Stream.agentMsgStream();
  s.append('增量文本(展示旁路)');
  s.finalize([txt('定稿文本,'), h('code', null, 'velocity.h5')]);

  const body = s.el.querySelector('.body');
  assert.equal(body.textContent, '定稿文本,velocity.h5');   // 增量文本被整体替换
  assert.ok(!s.el.classList.contains('is-streaming'));
  assert.ok(!s.el.classList.contains('is-aborted'));

  s.append('迟到的增量');                                // 手柄已关闭:静默丢弃
  assert.equal(body.textContent, '定稿文本,velocity.h5');
});

test('abort:半截保留 + 缺省标注「(回复中断,内容不完整)」+ is-aborted', () => {
  const s = Stream.agentMsgStream();
  s.append('数据检索到一半');
  s.abort();

  const body = s.el.querySelector('.body');
  assert.ok(!s.el.classList.contains('is-streaming'));
  assert.ok(s.el.classList.contains('is-aborted'));
  assert.match(body.textContent, /^数据检索到一半/);     // 半截内容如实保留
  assert.equal(body.querySelector('.say-abort').textContent, '(回复中断,内容不完整)');

  s.append('迟到增量');                                  // 标废后不再追加
  assert.match(body.textContent, /^数据检索到一半\(回复中断,内容不完整\)$/);
});

test('abort:自定义 label(停止按钮/reason 映射文案)透传到标注', () => {
  const s = Stream.agentMsgStream();
  s.append('循环推进中');
  s.abort('(已停止,回复未完成)');
  assert.equal(s.el.querySelector('.say-abort').textContent, '(已停止,回复未完成)');
});

test('近底部才跟随:上滚阅读时 append 不抢 scrollTop,贴底时跟随到底', () => {
  // 用户上滚(距底 ≥60px)→ mount 的 scroll 监听关掉自动跟随
  host.scrollHeight = 1000; host.clientHeight = 400; host.scrollTop = 100;
  host.dispatchEvent({ type: 'scroll' });

  const s = Stream.agentMsgStream();                    // push 内的 follow 也不得抢
  s.append('新增内容');
  assert.equal(host.scrollTop, 100);                    // 阅读位置保持

  // 回到贴底(距底 <60px)→ 恢复跟随,append 落底
  host.scrollTop = 950;
  host.dispatchEvent({ type: 'scroll' });
  s.append('继续输出');
  assert.equal(host.scrollTop, host.scrollHeight);      // rAF 桩同步执行
});
