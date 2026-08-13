/* ============================================================
   visionqa 的无浏览器自查脚本(node prototype/visionqa.check.mjs)
   直测 js/visionqa.js 导出的纯函数:
     ① 徽章映射与降级(pass 绿/warn 黄/fail 红;越界 verdict → null 不渲染);
     ② 检查项闭集标签与 severity 分级(排序:critical 在前);
     ③ GET /api/vision-qa 清单索引(坏形状/越界 verdict 剔除);
     ④ 缩略图 URL 反解与缓存键;
     ⑤ 时间格式化与 bare node 导入安全。
   模块 DOM 自初始化有 document 守卫,bare node import 安全,零 npm 依赖。
   ============================================================ */

const V = await import('./js/visionqa.js');

/* ---------------- 断言工具(与其余 check.mjs 同一输出约定) ---------------- */
let failed = 0;
let count = 0;
function check(name, cond) {
  count += 1;
  if (cond) { console.log(`  ok  ${name}`); }
  else { failed += 1; console.error(`  FAIL ${name}`); }
}
const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);

/* ---------------- ① 徽章映射与降级 ---------------- */
console.log('== ① 徽章映射与降级 ==');

check('verdict 闭集 pass/warn/fail', eq(V.VERDICTS, ['pass', 'warn', 'fail']));
check('pass → 绿徽章「AI 通过」', eq(V.badgeFor('pass'), { cls: 'is-pass', label: 'AI 通过' }));
check('warn → 黄徽章「AI 警告」', eq(V.badgeFor('warn'), { cls: 'is-warn', label: 'AI 警告' }));
check('fail → 红徽章「AI 不通过」', eq(V.badgeFor('fail'), { cls: 'is-fail', label: 'AI 不通过' }));
check('越界 verdict → null(降级:不渲染徽章)',
  V.badgeFor('ok') === null && V.badgeFor('') === null
  && V.badgeFor(undefined) === null && V.badgeFor('PASS') === null);

/* ---------------- ② 闭集标签与 severity 分级 ---------------- */
console.log('\n== ② 闭集标签与 severity 分级 ==');

check('检查项闭集五项与后端一致', eq(Object.keys(V.ISSUE_LABEL), [
  'unwrap_jump', 'decorrelation', 'reference_point', 'colorbar_scale',
  'processing_artifact']));
check('issueLabel:解缠跳变', V.issueLabel('unwrap_jump') === '解缠跳变/条纹不连续');
check('issueLabel:失相干', V.issueLabel('decorrelation') === '失相干大区块/噪声');
check('issueLabel:参考点', V.issueLabel('reference_point') === '参考点异常');
check('issueLabel:色标', V.issueLabel('colorbar_scale') === '色标与量纲问题');
check('issueLabel:伪影', V.issueLabel('processing_artifact') === '异常纹理/处理伪影');
check('issueLabel:未知 id 原样返回(显示防御)', V.issueLabel('mystery') === 'mystery');
check('issueLabel:空值 → 空串', V.issueLabel(undefined) === '' && V.issueLabel('') === '');

check('sevLabel:critical/warn/info → 严重/警告/提示',
  V.sevLabel('critical') === '严重' && V.sevLabel('warn') === '警告'
  && V.sevLabel('info') === '提示');

const mixed = [
  { issue: 'colorbar_scale', severity: 'info', detail: 'a' },
  { issue: 'unwrap_jump', severity: 'critical', detail: 'b' },
  { issue: 'decorrelation', severity: 'warn', detail: 'c' },
];
check('sortFindings:critical → warn → info',
  eq(V.sortFindings(mixed).map((f) => f.severity), ['critical', 'warn', 'info']));
check('sortFindings:原数组不被改写', mixed[0].severity === 'info');
check('sortFindings:坏形状项剔除(null/无 issue)',
  eq(V.sortFindings([null, { severity: 'warn' }, { issue: 'unwrap_jump', severity: 'info' }])
    .map((f) => f.issue), ['unwrap_jump']));
check('sortFindings:非数组 → []', eq(V.sortFindings(null), []) && eq(V.sortFindings('x'), []));

/* ---------------- ③ 清单索引(GET /api/vision-qa → Map) ---------------- */
console.log('\n== ③ 清单索引 ==');

const listing = {
  run: 'r1',
  items: [
    { figure: 'velocity.png', verdict: 'pass', findings: [], summary: 'ok' },
    { figure: 'unw.png', verdict: 'fail', findings: [], summary: 'bad' },
    { figure: 'ghost.png', verdict: 'maybe' },          // 越界 verdict:剔除
    { verdict: 'pass' },                                 // 缺 figure:剔除
    null,                                                // 坏条目:剔除
  ],
};
const idx = V.indexReviews(listing);
check('有效条目按图名入索引', idx.size === 2
  && idx.get('velocity.png').verdict === 'pass' && idx.get('unw.png').verdict === 'fail');
check('越界 verdict/坏形状条目剔除', !idx.has('ghost.png'));
check('items 非数组 → 空 Map', V.indexReviews({ items: 'x' }).size === 0);
check('响应为空/null → 空 Map', V.indexReviews(null).size === 0 && V.indexReviews({}).size === 0);

/* ---------------- ④ URL 反解与缓存键 ---------------- */
console.log('\n== ④ URL 反解与缓存键 ==');

const q1 = V.parseArtifactQuery(
  '/api/artifact-file?session=s1&run_id=r9&step=10&art_id=fig&file=vel_thumb.png');
check('缩略图 URL 反解出 session/run_id', eq(q1, { session: 's1', runId: 'r9' }));
check('缺 run_id 时 runId 为空串(最新 run 语义)',
  eq(V.parseArtifactQuery('/api/artifact-file?session=s1&art_id=x'),
    { session: 's1', runId: '' }));
check('非 artifact-file 路径 → null',
  V.parseArtifactQuery('/api/figures?session=s1') === null);
check('缺 session → null', V.parseArtifactQuery('/api/artifact-file?art_id=x') === null);
check('演示 SVG 无 src(空串)→ null', V.parseArtifactQuery('') === null);

check('keyOf:session|runId|图名 三段拼接',
  V.keyOf({ session: 's1', runId: 'r9' }, 'velocity.png') === 's1|r9|velocity.png');

/* ---------------- ⑤ 时间格式化与 bare 导入安全 ---------------- */
console.log('\n== ⑤ 时间格式化与 bare 导入安全 ==');

const t = V.fmtTime('2026-08-13T04:05:06Z');
check('fmtTime:ISO → YYYY-MM-DD HH:MM(本地时区)', /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/.test(t));
check('fmtTime:坏输入原样返回(不出 NaN)',
  V.fmtTime('不是时间') === '不是时间' && V.fmtTime('') === '');

let initSafe = true;
try { V.initVisionQA(); } catch { initSafe = false; }
check('无 document 环境下 initVisionQA 安全空转(自初始化守卫)', initSafe);

console.log(failed ? `\n${count} 项断言,${failed} 项失败` : `\n全部 ${count} 项断言通过`);
process.exit(failed ? 1 : 0);
