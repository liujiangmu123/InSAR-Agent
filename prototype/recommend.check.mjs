/* ============================================================
   处理路线推荐纯函数与接线自查(node prototype/recommend.check.mjs)
   —— recommend.js 的可测内核:适配徽章映射(环境就绪绿 / 缺失黄)/
   预填话术生成 / 步骤号折叠 / path 反查 / file:// 短路,外加源码级接线
   断言(index.html 两行引入、徽章 CSS 类齐备、捕获阶段委托 + 不改
   datasets.js 的注入方式)。

   recommend.js 顶层以 typeof document 守卫自初始化:node 导入只暴露
   纯函数(依赖链 datasets.js/dom.js 同样守卫,与 datasets.check.mjs
   同一路线)。
   ============================================================ */
import { readFileSync } from 'node:fs';

let failed = 0;
let passed = 0;
function check(name, cond) {
  if (cond) { passed += 1; console.log(`  ok  ${name}`); }
  else { failed += 1; console.error(`  FAIL ${name}`); }
}

const REC = await import('./js/recommend.js');
const { badgeState, prefillText, stepsLabel, matchByPath } = REC;

/* ---------------- A. 适配徽章映射(满足绿 / 缺失黄) ---------------- */
console.log('== A. badgeState ==');
const readyRoute = { ready: true, missing: [] };
const missRoute = { ready: false, missing: ['isce2', 'snaphu'] };
check('A1 必需项全满足 → 绿徽章 rec-b-ok', badgeState(readyRoute).cls === 'rec-b-ok');
check('A2 绿徽章文案「环境就绪」', badgeState(readyRoute).label === '环境就绪');
check('A3 有缺失 → 黄徽章 rec-b-miss', badgeState(missRoute).cls === 'rec-b-miss');
check('A4 黄徽章文案列出全部缺失项(顿号分隔)',
  badgeState(missRoute).label === '缺 isce2、snaphu');
check('A5 缺失表为空但未就绪 → 黄徽章兜底文案',
  badgeState({ ready: false, missing: [] }).label === '环境待核对');
check('A6 null 容错不抛错', badgeState(null).cls === 'rec-b-miss');

/* ---------------- B. 预填话术生成(只预填不发送) ---------------- */
console.log('\n== B. prefillText ==');
const DS = { path: 'E:\\data\\ridgecrest_hyp3', name: 'ridgecrest_hyp3' };
const t1 = prefillText({ route_id: 'hyp3_direct', name: 'HyP3 产品直通时序' }, DS);
check('B1 常规路线:「用<路线名>分析,数据在 <path>」',
  t1 === '用HyP3 产品直通时序分析,数据在 E:\\data\\ridgecrest_hyp3');
check('B2 话术包含数据路径(对话大脑可直接消费)', t1.includes(DS.path));
const t2 = prefillText({ route_id: 'identify_first', name: '先识别数据类型' },
  { path: 'E:\\data\\notes' });
check('B3 unknown 类走「先识别」引导话术',
  t2 === '帮我看看 E:\\data\\notes 里是什么数据,该怎么处理');
const t3 = prefillText({ route_id: 'dem_only', name: '仅地形,需配主数据' },
  { path: 'E:\\data\\dem_tiles' });
check('B4 DEM 类话术声明配主数据用途',
  t3.includes('E:\\data\\dem_tiles') && t3.includes('配合主数据'));
check('B5 字段缺失容错(空路线/空数据集不抛错)',
  typeof prefillText(null, null) === 'string');

/* ---------------- C. 步骤号折叠展示 ---------------- */
console.log('\n== C. stepsLabel ==');
check('C1 直通路线 [1,7,8,9,10,11] → "1、7-11"',
  stepsLabel([1, 7, 8, 9, 10, 11]) === '1、7-11');
check('C2 全链 [1..11] → "1-11"',
  stepsLabel([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]) === '1-11');
check('C3 单步 [2] → "2"', stepsLabel([2]) === '2');
check('C4 空列表 → "—"(identify_first 无步骤)', stepsLabel([]) === '—');
check('C5 乱序输入先排序再折叠', stepsLabel([9, 7, 8, 1]) === '1、7-9');

/* ---------------- D. 卡片 path → 数据集条目反查 ---------------- */
console.log('\n== D. matchByPath ==');
const LIST = [
  { id: 'aaa', path: 'E:\\data\\x' },
  { id: 'bbb', path: 'E:\\data\\y' },
];
check('D1 命中返回条目(拿到 id 供 /api/recommend)',
  matchByPath(LIST, 'E:\\data\\y').id === 'bbb');
check('D2 未命中返回 null', matchByPath(LIST, 'E:\\data\\z') === null);
check('D3 空清单/undefined 容错', matchByPath(undefined, 'p') === null);

/* ---------------- E. file:// 短路(取数入口不发请求) ---------------- */
console.log('\n== E. file:// 短路 ==');
globalThis.location = { protocol: 'file:' };
globalThis.fetch = () => { throw new Error('file:// 下不允许发请求'); };
check('E1 fetchRecommend → null', (await REC.fetchRecommend('abc')) === null);

/* ---------------- F. 源码级接线 ---------------- */
console.log('\n== F. 源码级接线 ==');
const src = readFileSync(new URL('./js/recommend.js', import.meta.url), 'utf-8');
check('F1 捕获阶段事件委托(click/keydown 第三参 true,拦在卡片 onclick 前)',
  src.includes("addEventListener('click', onActivate, true)")
  && src.includes("addEventListener('keydown', onActivate, true)"));
check('F2 stopPropagation 阻断卡片自身 onclick(不误开文件抽屉)',
  src.includes('e.stopPropagation()'));
check('F3 MutationObserver 盯 #pane-files:datasets.js 重渲染后补插入口',
  src.includes("getElementById('pane-files')") && src.includes('MutationObserver'));
check('F4 注入目标是 .ds-card(读 datasets.js 的 DOM,不改它)',
  src.includes(".querySelectorAll('.ds-card')"));
check('F5 预填走 #prompt 且 dispatch input,绝不代发送',
  src.includes("getElementById('prompt')")
  && src.includes("new Event('input'") && !src.includes('btnSend'));
check('F6 保留 file:// 判据(与 datasets/fileslive 同约定)',
  src.includes("location.protocol === 'file:'"));

const dsSrc = readFileSync(new URL('./js/datasets.js', import.meta.url), 'utf-8');
check('F7 datasets.js 未被本功能改动(不含 recommend 字样)',
  !dsSrc.toLowerCase().includes('recommend'));

const html = readFileSync(new URL('./index.html', import.meta.url), 'utf-8');
check('F8 index.html 引入 css/recommend.css 与 js/recommend.js',
  html.includes('css/recommend.css') && html.includes('js/recommend.js'));

const css = readFileSync(new URL('./css/recommend.css', import.meta.url), 'utf-8');
check('F9 徽章双色 CSS 类齐备(.rec-b-ok / .rec-b-miss)',
  css.includes('.rec-b-ok') && css.includes('.rec-b-miss'));
check('F10 抽屉/入口/双列样式在位(.rec-panel / .rec-btn / .rec-cols)',
  css.includes('.rec-panel') && css.includes('.rec-btn') && css.includes('.rec-cols'));

/* ---------------- 收尾 ---------------- */
console.log(`\n${passed} 项通过${failed ? ` · ${failed} 项失败` : ' · 全部断言通过'}`);
process.exit(failed ? 1 : 0);
