/* ============================================================
   reportlive.js 纯函数层(markdown 解析 / 行内标记 / 来源标注)锁定
   —— 渲染层(renderMarkdown)碰 DOM 不在 node 环境测,解析层与
   report/methods.py 生成物的结构子集一一对应,在此全量锁形状。
   ============================================================ */
import './_env.mjs';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  parseBlocks, tokenizeInline, sourceLabel,
} from '../../prototype/js/reportlive.js';

/* ---------------- tokenizeInline:行内标记 ---------------- */

test('行内切分:prov 引用 / ref 锚点 / 粗体 / 行内代码 / 普通文本', () => {
  const toks = tokenizeInline(
    '滤波强度 α = 0.4〔prov-5〕〔ref:ISCE2 默认 0.5〕,方法 `goldstein`,**重要**。');
  assert.deepEqual(toks.map((t) => t.t),
    ['text', 'prov', 'ref', 'text', 'code', 'text', 'bold', 'text']);
  assert.equal(toks[1].s, '〔prov-5〕');           // prov 保留完整标记(渲染为 .cite)
  assert.equal(toks[2].s, '〔ref:ISCE2 默认 0.5〕'); // ref 保留完整标记(渲染为弱化小字)
  assert.equal(toks[4].s, 'goldstein');             // 代码剥掉反引号
  assert.equal(toks[6].s, '重要');                  // 粗体剥掉星号
});

test('行内切分:指标引用形态〔prov-产物#字段〕原样保留', () => {
  const toks = tokenizeInline('覆盖率 0.942〔prov-qa.json#unwrap_coverage〕达标');
  assert.equal(toks[1].t, 'prov');
  assert.equal(toks[1].s, '〔prov-qa.json#unwrap_coverage〕');
});

test('行内切分:未闭合的标记按普通文本处理,不抛错', () => {
  const toks = tokenizeInline('残缺〔prov-6 与残缺 **加粗');
  assert.deepEqual(toks, [{ t: 'text', s: '残缺〔prov-6 与残缺 **加粗' }]);
});

test('行内切分:空串返回空数组', () => {
  assert.deepEqual(tokenizeInline(''), []);
});

/* ---------------- parseBlocks:块级结构 ---------------- */

test('块解析:标题层级与文本', () => {
  const blocks = parseBlocks('# 一级\n\n## 二级\n\n#### 四级');
  assert.deepEqual(blocks, [
    { t: 'h', level: 1, text: '一级' },
    { t: 'h', level: 2, text: '二级' },
    { t: 'h', level: 4, text: '四级' },
  ]);
});

test('块解析:连续文本行合并为一个段落,空行分段', () => {
  const blocks = parseBlocks('第一段甲\n第一段乙\n\n第二段');
  assert.deepEqual(blocks, [
    { t: 'p', text: '第一段甲 第一段乙' },
    { t: 'p', text: '第二段' },
  ]);
});

test('块解析:引用块(> 开头)连续行合并', () => {
  const blocks = parseBlocks('> ⚠ 本次运行为**模拟执行**,\n> 不构成科学结论。');
  assert.equal(blocks.length, 1);
  assert.equal(blocks[0].t, 'quote');
  assert.equal(blocks[0].text, '⚠ 本次运行为**模拟执行**, 不构成科学结论。');
});

test('块解析:无序列表,两格缩进识别为子项(证据边界的缺口明细形态)', () => {
  const blocks = parseBlocks([
    '- 步骤证据来源:local 6 步、missing 5 步',
    '  - 第 2 步:云端完成声明缺本地证据',
    '- 本次为模拟执行',
  ].join('\n'));
  assert.equal(blocks.length, 1);
  assert.equal(blocks[0].t, 'ul');
  assert.deepEqual(blocks[0].items.map((it) => it.sub), [false, true, false]);
  assert.match(blocks[0].items[1].text, /^第 2 步/);
});

test('块解析:有序列表', () => {
  const blocks = parseBlocks('1. 甲\n2. 乙');
  assert.deepEqual(blocks, [{
    t: 'ol',
    items: [{ text: '甲', sub: false }, { text: '乙', sub: false }],
  }]);
});

test('块解析:表格(质量指标表形态),分隔行被剔除', () => {
  const blocks = parseBlocks([
    '| 指标 | 值 | 来源 | 重解析 |',
    '|---|---|---|---|',
    '| crossval_r | 0.92 | `qa.json#crossval_r` | ✓ |',
    '| unwrap_coverage | 0.942 | `qa.json#unwrap_coverage` | ✓ |',
  ].join('\n'));
  assert.equal(blocks.length, 1);
  const tbl = blocks[0];
  assert.equal(tbl.t, 'table');
  assert.deepEqual(tbl.head, ['指标', '值', '来源', '重解析']);
  assert.equal(tbl.rows.length, 2);
  assert.deepEqual(tbl.rows[0], ['crossval_r', '0.92', '`qa.json#crossval_r`', '✓']);
});

test('块解析:methods.py 生成物的整体骨架顺序', () => {
  const md = [
    '# 处理方法(自动生成草稿)',
    '',
    '> ⚠ 模拟执行声明。',
    '',
    '- 运行标识:`20260812T0000-abcd`',
    '',
    '## 处理链',
    '',
    '- **时序反演**(方法 `mintpy_sbas`〔prov-7〕):最大时间基线 120 天〔prov-7〕〔ref:台账〕。',
    '',
    '| 指标 | 值 | 来源 | 重解析 |',
    '|---|---|---|---|',
    '| crossval_r | 0.92 | `qa.json#crossval_r` | ✓ |',
    '',
    '> 尾注。',
    '',
  ].join('\n');
  assert.deepEqual(parseBlocks(md).map((b) => b.t),
    ['h', 'quote', 'ul', 'h', 'ul', 'table', 'quote']);
});

test('块解析:空输入与 null 安全', () => {
  assert.deepEqual(parseBlocks(''), []);
  assert.deepEqual(parseBlocks(null), []);
  assert.deepEqual(parseBlocks('\n\n\n'), []);
});

test('块解析:CRLF 换行归一化(Windows 后端纯文本响应)', () => {
  const blocks = parseBlocks('# 标题\r\n\r\n段落');
  assert.deepEqual(blocks.map((b) => b.t), ['h', 'p']);
});

/* ---------------- sourceLabel:来源标注 ---------------- */

test('来源标注:llm → LLM 增强,template/缺省 → 规则生成', () => {
  assert.match(sourceLabel('llm'), /LLM 增强/);
  assert.match(sourceLabel('template'), /规则生成/);
  assert.match(sourceLabel(undefined), /规则生成/);
});
