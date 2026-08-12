/* ============================================================
   gallery.js 元数据格式化 + 三档条目归一化 + 网格 DOM 结构锁定
   —— gallery.js 的 import 链(dom.js/state.js/figures.js)需要
   createElement 级别的 DOM,_env.mjs 的哑 document 不够用;
   本文件先装迷你 DOM 桩再动态 import(静态 import 会被提升,
   无法保证桩先生效)。桩只实现 h()/append()/grid() 用到的接口。
   ============================================================ */
import './_env.mjs';   // localStorage 桩(state.js 顶层需要)
import { test } from 'node:test';
import assert from 'node:assert/strict';

/* ---------------- 迷你 DOM 桩 ---------------- */

class FakeNode {}

class FakeText extends FakeNode {
  constructor(s) { super(); this.data = String(s); }
  get textContent() { return this.data; }
}

class FakeElement extends FakeNode {
  constructor(tag) {
    super();
    this.tagName = String(tag).toUpperCase();
    this.children = [];
    this.attrs = {};
    this.dataset = {};
    this.style = { setProperty() {} };
    this.className = '';
    this._html = '';
  }
  appendChild(c) { this.children.push(c); return c; }
  append(...cs) { this.children.push(...cs); }
  replaceChildren(...cs) { this.children = cs; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k] ?? null; }
  addEventListener() {}
  remove() {}
  set innerHTML(v) { this._html = String(v); }
  get innerHTML() { return this._html; }
  get textContent() {
    return this.children.map((c) => c.textContent ?? '').join('');
  }
  querySelectorAll() { return []; }
  get isConnected() { return true; }
}

globalThis.Node = FakeNode;
globalThis.document = {
  documentElement: { dataset: {}, style: { setProperty() {} } },
  readyState: 'complete',
  body: new FakeElement('body'),
  createElement: (t) => new FakeElement(t),
  createElementNS: (_ns, t) => new FakeElement(t),
  createTextNode: (s) => new FakeText(s),
  createDocumentFragment: () => new FakeElement('#fragment'),
  addEventListener() {},
};

// 桩就位后再加载被测模块(动态 import 保证顺序)
const { metaLine, fmtDate, realItem, grid } =
  await import('../../prototype/js/gallery.js');

/* ---------------- 工具:按类名收集节点 ---------------- */

function collect(node, out = []) {
  out.push(node);
  for (const c of node.children || []) collect(c, out);
  return out;
}
const byClass = (root, cls) =>
  collect(root).filter((n) => (n.className || '').split(' ').includes(cls));

/* ---------------- metaLine / fmtDate ---------------- */

test('metaLine:单位/色标+对称值域/日期区间拼接为一行', () => {
  assert.equal(metaLine({
    units: 'mm/yr', cmap: 'vik', vlim: [-23.4, 23.4],
    date_range: ['20190610', '20190815'],
  }), '单位 mm/yr · 色标 vik ±23.4 · 2019-06-10 → 2019-08-15');
});

test('metaLine:非对称值域用区间;无 cmap 时前缀「值域」', () => {
  assert.equal(metaLine({ vlim: [-10, 40] }), '值域 -10 ~ 40');
  assert.equal(metaLine({ cmap: 'roma', vlim: [-10, 40] }), '色标 roma -10 ~ 40');
});

test('metaLine:空/非对象/无可用字段返回空串(回落文件名的现状)', () => {
  assert.equal(metaLine(null), '');
  assert.equal(metaLine('x'), '');
  assert.equal(metaLine({}), '');
  assert.equal(metaLine({ vlim: ['a', 'b'] }), '');   // 非数值值域不显示
});

test('metaLine:日期区间缺一端只显示存在的一端', () => {
  assert.equal(metaLine({ date_range: ['20190610', null] }), '2019-06-10');
});

test('fmtDate:YYYYMMDD 加连字符,其他格式原样', () => {
  assert.equal(fmtDate('20190610'), '2019-06-10');
  assert.equal(fmtDate('2019-06-10'), '2019-06-10');
});

/* ---------------- realItem:三档 url 与标题回退 ---------------- */

const FIG_FULL = {
  step: 10, artId: 'figures', name: 'velocity.png', size: 2048, mtime: 1765526400,
  url: '/api/artifact-file?a=1&file=velocity_browse.png',
  fullUrl: '/api/artifact-file?a=1&file=velocity.png',
  thumbUrl: '/api/artifact-file?a=1&file=velocity_thumb.png',
  meta: { title: 'InSAR LOS velocity', units: 'mm/yr', cmap: 'vik', vlim: [-23.4, 23.4] },
};
const FIG_PLAIN = {
  step: 10, artId: 'figures', name: 'plain.png', size: 100, mtime: 1765526400,
  url: '/api/artifact-file?a=1&file=plain.png',
  fullUrl: '/api/artifact-file?a=1&file=plain.png',
  thumbUrl: '/api/artifact-file?a=1&file=plain.png',
};

test('realItem:三档各就各位,网格缩略节点用 thumbUrl', () => {
  const it = realItem(FIG_FULL);
  assert.equal(it.url, FIG_FULL.url);
  assert.equal(it.fullUrl, FIG_FULL.fullUrl);
  assert.equal(it.thumbUrl, FIG_FULL.thumbUrl);
  assert.equal(it.node().attrs.src, FIG_FULL.thumbUrl);
  assert.equal(it.label, 'InSAR LOS velocity');   // sidecar 标题优先
});

test('realItem:缺三档字段回退 url;无 meta 时 label 回落文件名', () => {
  const it = realItem({ ...FIG_PLAIN, fullUrl: undefined, thumbUrl: undefined });
  assert.equal(it.fullUrl, FIG_PLAIN.url);
  assert.equal(it.thumbUrl, FIG_PLAIN.url);
  assert.equal(it.label, 'plain.png');
  assert.equal(it.meta, null);
});

/* ---------------- 网格 DOM:元数据行有则显示,无则保持现状 ---------------- */

test('grid:有 sidecar 的卡片带元数据行,标题取 sidecar;无则只有文件名', () => {
  const g = grid([realItem(FIG_FULL), realItem(FIG_PLAIN)]);
  const [card1, card2] = g.children;

  assert.equal(byClass(card1, 'glx-name')[0].textContent, 'InSAR LOS velocity');
  assert.equal(byClass(card1, 'glx-name')[0].attrs.title, 'velocity.png');
  const meta2 = byClass(card1, 'glx-meta2');
  assert.equal(meta2.length, 1);
  assert.match(meta2[0].textContent, /单位 mm\/yr · 色标 vik ±23\.4/);
  // 缩略图用 thumb 档
  assert.equal(byClass(card1, 'glx-thumb')[0].children[0].attrs.src, FIG_FULL.thumbUrl);

  assert.equal(byClass(card2, 'glx-name')[0].textContent, 'plain.png');
  assert.equal(byClass(card2, 'glx-meta2').length, 0);   // 无 sidecar:现状
});
