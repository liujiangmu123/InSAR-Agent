/* ============================================================
   数据集区纯函数与接线自查(node prototype/datasets.check.mjs)
   —— datasets.js 的可测内核:大小人性化 / 文件名日期解析 / 徽章映射 /
   数量文案 / file:// 短路,外加源码级接线断言(index.html 两行引入、
   五色徽章 CSS 类齐备、自初始化盯 #pane-files)。

   datasets.js 顶层以 typeof document 守卫自初始化:node 导入只暴露
   纯函数,无需 DOM stub(与 demo-mode.check.mjs 的全 stub 路线互补)。
   ============================================================ */
import { readFileSync } from 'node:fs';

let failed = 0;
let passed = 0;
function check(name, cond) {
  if (cond) { passed += 1; console.log(`  ok  ${name}`); }
  else { failed += 1; console.error(`  FAIL ${name}`); }
}

const DS = await import('./js/datasets.js');
const {
  KIND_BADGE, badgeOf, humanSize, parseDates, fmtDateRange, countLabel,
} = DS;

/* ---------------- A. 大小人性化 ---------------- */
console.log('== A. humanSize ==');
check('A1 0 字节 → "0 B"', humanSize(0) === '0 B');
check('A2 1023 → "1023 B"(不足 1KB 不换单位)', humanSize(1023) === '1023 B');
check('A3 1024 → "1.0 KB"', humanSize(1024) === '1.0 KB');
check('A4 1536 → "1.5 KB"(一位小数)', humanSize(1536) === '1.5 KB');
check('A5 100 MB 整 → "100 MB"(≥100 取整)', humanSize(100 * 1024 * 1024) === '100 MB');
check('A6 1 GB → "1.0 GB"', humanSize(1024 ** 3) === '1.0 GB');
check('A7 3.5 TB → "3.5 TB"(顶格单位)', humanSize(3.5 * 1024 ** 4) === '3.5 TB');
check('A8 null/undefined → "—"', humanSize(null) === '—' && humanSize(undefined) === '—');

/* ---------------- B. 文件名日期解析(镜像后端 catalog._DATE_RE) ---------------- */
console.log('\n== B. parseDates ==');
const GRANULE = 'S1AA_20190704T135158_20190716T135159_VVP012_INT80_G_ueF_355F_unw_phase.tif';
check('B1 HyP3 granule → 参考/次级两个日期',
  JSON.stringify(parseDates(GRANULE)) === JSON.stringify(['2019-07-04', '2019-07-16']));
const SAFE = 'S1A_IW_SLC__1SDV_20200604T022252_20200604T022319_032861_03CE65_7C85.SAFE';
check('B2 Sentinel SAFE 名 → 去重后单个日期',
  JSON.stringify(parseDates(SAFE)) === JSON.stringify(['2020-06-04']));
check('B3 ALOS 轨道号不误判为日期(IMG-HH-ALPSRP207600640-H1.0__A)',
  parseDates('IMG-HH-ALPSRP207600640-H1.0__A').length === 0);
check('B4 月/日非法的 8 位数字不算日期(20191341)',
  parseDates('x_20191341T000000_y').length === 0);
check('B5 分隔符形态(下划线/点/结尾)也能命中',
  JSON.stringify(parseDates('ts_20200101.slc')) === JSON.stringify(['2020-01-01']));
check('B6 空串/undefined 容错为空表',
  parseDates('').length === 0 && parseDates(undefined).length === 0);

/* ---------------- C. 日期范围展示 ---------------- */
console.log('\n== C. fmtDateRange ==');
check('C1 null → "—"', fmtDateRange(null) === '—');
check('C2 起止相同 → 单日期', fmtDateRange({ start: '2020-06-04', end: '2020-06-04' }) === '2020-06-04');
check('C3 起止不同 → "start ~ end"',
  fmtDateRange({ start: '2019-07-04', end: '2019-07-28' }) === '2019-07-04 ~ 2019-07-28');

/* ---------------- D. 徽章映射(五色闭集) ---------------- */
console.log('\n== D. 徽章映射 ==');
const kinds = ['hyp3', 'alos_raw', 'slc_stack', 'dem', 'unknown'];
check('D1 类型闭集与后端 catalog.KINDS 对齐(五种)',
  JSON.stringify(Object.keys(KIND_BADGE)) === JSON.stringify(kinds));
check('D2 五种类型的徽章 CSS 类互不相同(五色)',
  new Set(kinds.map((k) => badgeOf(k).cls)).size === 5);
check('D3 每个徽章都有非空中文标签',
  kinds.every((k) => badgeOf(k).label && badgeOf(k).label.length >= 2));
check('D4 未知类型回落 unknown 徽章', badgeOf('weird_future_kind') === KIND_BADGE.unknown);

/* ---------------- E. 数量文案(按类型语义) ---------------- */
console.log('\n== E. countLabel ==');
check('E1 hyp3 → 对数', countLabel({ kind: 'hyp3', detail: { pairs: 11 } }) === '11 对干涉');
check('E2 alos_raw → 场景组数', countLabel({ kind: 'alos_raw', detail: { scenes: 2 } }) === '2 组场景');
check('E3 slc_stack → slc+safe 合计', countLabel({ kind: 'slc_stack', detail: { slc: 2, safe: 1 } }) === '3 景 SLC');
check('E4 dem → DEM 文件数', countLabel({ kind: 'dem', detail: { dem_files: ['a.grd', 'b.dem'] } }) === '2 个 DEM');
check('E5 unknown → 文件数兜底', countLabel({ kind: 'unknown', detail: {}, file_count: 7 }) === '7 个文件');
check('E6 detail 缺失容错不抛错', countLabel({ kind: 'hyp3' }) === '0 对干涉');

/* ---------------- F. file:// 短路(取数入口不发请求) ---------------- */
console.log('\n== F. file:// 短路 ==');
globalThis.location = { protocol: 'file:' };
globalThis.fetch = () => { throw new Error('file:// 下不允许发请求'); };
check('F1 fetchDatasets → null', (await DS.fetchDatasets()) === null);
check('F2 fetchDatasetDetail → null', (await DS.fetchDatasetDetail('abc')) === null);
const added = await DS.addRoot('E:\\data');
check('F3 addRoot → {ok:false} 且带原因', added.ok === false && !!added.error);

/* ---------------- G. 源码级接线 ---------------- */
console.log('\n== G. 源码级接线 ==');
const src = readFileSync(new URL('./js/datasets.js', import.meta.url), 'utf-8');
check('G1 自初始化盯 #pane-files 且用 MutationObserver 在重渲染后回插',
  src.includes("getElementById('pane-files')") && src.includes('MutationObserver'));
check('G2 空态文案在位(未发现数据集 —— 添加包含 InSAR 数据的目录)',
  src.includes('未发现数据集 —— 添加包含 InSAR 数据的目录'));
check('G3 保留 file:// 判据(与 fileslive 等取数模块同约定)',
  src.includes("location.protocol === 'file:'"));

const html = readFileSync(new URL('./index.html', import.meta.url), 'utf-8');
check('G4 index.html 引入 css/datasets.css 与 js/datasets.js',
  html.includes('css/datasets.css') && html.includes('js/datasets.js'));

const css = readFileSync(new URL('./css/datasets.css', import.meta.url), 'utf-8');
check('G5 五色徽章 CSS 类齐备',
  ['ds-b-hyp3', 'ds-b-alos', 'ds-b-slc', 'ds-b-dem', 'ds-b-unknown']
    .every((c) => css.includes(`.${c}`)));
check('G6 抽屉与卡片样式在位(.ds-drawer / .ds-card)',
  css.includes('.ds-drawer') && css.includes('.ds-card'));

/* ---------------- 收尾 ---------------- */
console.log(`\n${passed} 项通过${failed ? ` · ${failed} 项失败` : ' · 全部断言通过'}`);
process.exit(failed ? 1 : 0);
