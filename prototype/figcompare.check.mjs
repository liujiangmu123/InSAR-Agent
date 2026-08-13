/* ============================================================
   figcompare 的无浏览器自查脚本(node prototype/figcompare.check.mjs)
   直测 js/figcompare.js 导出的纯函数:
     ① 缩放/平移数学(clamp、以光标为锚的坐标换算、滚轮归一化);
     ② 模式状态机(swipe/blink/side 切换、自动播放、reduced-motion);
     ③ A/B 选择器(点选/取消/补位/替换/交换);
     ④ 缩略图 URL 反解与 /api/figures 清单匹配。
   模块 DOM 自初始化有 document 守卫,bare node import 安全,零 npm 依赖。
   ============================================================ */

const F = await import('./js/figcompare.js');

/* ---------------- 断言工具(与其余 check.mjs 同一输出约定) ---------------- */
let failed = 0;
let count = 0;
function check(name, cond) {
  count += 1;
  if (cond) { console.log(`  ok  ${name}`); }
  else { failed += 1; console.error(`  FAIL ${name}`); }
}
const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const near = (a, b, eps = 1e-9) => Math.abs(a - b) <= eps;

/* ---------------- ① 缩放/平移数学 ---------------- */
console.log('== ① 缩放/平移数学 ==');

check('缩放范围常量 = 0.25–8x', F.SCALE_MIN === 0.25 && F.SCALE_MAX === 8);
check('clampScale:范围内原样', F.clampScale(1) === 1 && F.clampScale(3.5) === 3.5);
check('clampScale:下越界夹到 0.25', F.clampScale(0.01) === 0.25);
check('clampScale:上越界夹到 8', F.clampScale(64) === 8);
check('clampScale:NaN/Infinity 回落 1(不进 transform)',
  F.clampScale(NaN) === 1 && F.clampScale(Infinity) === 8);

check('resetView = {scale:1, tx:0, ty:0}', eq(F.resetView(), { scale: 1, tx: 0, ty: 0 }));

// 以光标为锚缩放:t′ = p − (p − t)·(s′/s)
const v0 = F.resetView();
const z1 = F.zoomAt(v0, 100, 50, 2);
check('zoomAt:2x 后 scale/tx/ty 精确(t′ = p − (p−t)·k)',
  z1.scale === 2 && z1.tx === -100 && z1.ty === -50);
// 锚点不变性:锚点下的图内像素 w = (p−t)/s,缩放后 w·s′+t′ 仍是 p
const w = { x: (100 - v0.tx) / v0.scale, y: (50 - v0.ty) / v0.scale };
check('zoomAt:锚点下像素缩放前后钉在原屏幕位置',
  near(w.x * z1.scale + z1.tx, 100) && near(w.y * z1.scale + z1.ty, 50));

const z2 = F.zoomAt(F.zoomAt({ scale: 1.7, tx: 33, ty: -12 }, 120, 80, 2), 120, 80, 0.5);
check('zoomAt:同锚放大再缩小回到原视图(可逆)',
  near(z2.scale, 1.7) && near(z2.tx, 33) && near(z2.ty, -12));

const v6 = { scale: 6, tx: 10, ty: 20 };
const z3 = F.zoomAt(v6, 200, 100, 2);
const w6 = { x: (200 - v6.tx) / v6.scale, y: (100 - v6.ty) / v6.scale };
check('zoomAt:撞上限夹到 8x,锚点换算按实际倍率仍不漂移',
  z3.scale === 8 && near(w6.x * z3.scale + z3.tx, 200) && near(w6.y * z3.scale + z3.ty, 100));
check('zoomAt:撞下限夹到 0.25x', F.zoomAt({ scale: 0.3, tx: 0, ty: 0 }, 0, 0, 0.5).scale === 0.25);

const p1 = F.panBy({ scale: 2, tx: 5, ty: -3 }, 10, -7);
check('panBy:平移只动 tx/ty,scale 不变', eq(p1, { scale: 2, tx: 15, ty: -10 }));
check('viewTransform:translate 在前(screen = world·s + t)',
  F.viewTransform(p1) === 'translate(15px, -10px) scale(2)');

check('wheelFactor:deltaY=0 → 倍率 1', F.wheelFactor(0) === 1);
check('wheelFactor:滚轮 ±100px 夹紧到 ±24 → e^∓0.288',
  near(F.wheelFactor(100), Math.exp(-0.288)) && near(F.wheelFactor(-100), Math.exp(0.288)));
check('wheelFactor:行模式 delta=3 × 16px/行 = 48 → 同样夹紧',
  near(F.wheelFactor(3, 1), Math.exp(-0.288)));
check('wheelFactor:小步进正反互逆', near(F.wheelFactor(10) * F.wheelFactor(-10), 1));

/* ---------------- ② 模式状态机 ---------------- */
console.log('\n== ② 模式状态机 ==');

check('三模式闭集 swipe/blink/side', eq(F.MODES, ['swipe', 'blink', 'side']));
let st = F.createCompareState();
check('初始态:swipe · 分割线 50% · 视图复位 · 自动关 · 2Hz · 相位 A',
  st.mode === 'swipe' && st.pos === 50 && eq(st.view, F.resetView())
  && st.auto === false && st.hz === 2 && st.phase === 'A' && st.reduced === false);

check('setMode:非法模式原样返回(同一引用)', F.setMode(st, 'blend') === st);
check('setMode:同模式原样返回(同一引用)', F.setMode(st, 'swipe') === st);

st = F.setMode(st, 'blink');
check('setMode:切入 blink,相位归 A、自动关', st.mode === 'blink' && st.phase === 'A' && !st.auto);
st = F.toggleAuto(st);
check('toggleAuto:blink 下开启自动', st.auto === true);
st = F.blinkTick(st);
check('blinkTick:相位 A → B', st.phase === 'B');
check('blinkTick:相位 B → A(交替)', F.blinkTick(st).phase === 'A');
st = F.setMode(st, 'side');
check('setMode:切走 blink 时自动停、相位归 A', st.mode === 'side' && !st.auto && st.phase === 'A');
check('toggleAuto:非 blink 模式拒绝开启', F.toggleAuto(st).auto === false);

const rst = F.setMode(F.createCompareState({ reduced: true }), 'blink');
check('reduced-motion:blink 下 toggleAuto 拒绝开启(经典 2Hz 闪烁禁用)',
  rst.reduced === true && F.toggleAuto(rst).auto === false);

st = F.createCompareState();
check('setHz:上越界夹到 4Hz', F.setHz(st, 10).hz === 4);
check('setHz:下越界夹到 0.5Hz', F.setHz(st, 0.1).hz === 0.5);
check('setHz:非数值原样返回', F.setHz(st, 'abc') === st);
check('blinkIntervalMs:2Hz=500ms · 4Hz=250ms · 0.5Hz=2000ms',
  F.blinkIntervalMs(2) === 500 && F.blinkIntervalMs(4) === 250 && F.blinkIntervalMs(0.5) === 2000);
check('blinkIntervalMs:越界频率先夹紧再换算', F.blinkIntervalMs(100) === 250);

check('setPos:120 → 100(夹紧)', F.setPos(st, 120).pos === 100);
check('setPos:-5 → 0(夹紧)', F.setPos(st, -5).pos === 0);
check('setPos:字符串 "37" 可用(range input 的 value)', F.setPos(st, '37').pos === 37);
check('setPos:非数值原样返回', F.setPos(st, 'x') === st);

/* ---------------- ③ A/B 选择器 ---------------- */
console.log('\n== ③ A/B 选择器 ==');

check('空集点选 → 成为 A', eq(F.selToggle([], 'vel.png'), ['vel.png']));
check('再选一张 → 成为 B', eq(F.selToggle(['vel.png'], 'coh.png'), ['vel.png', 'coh.png']));
check('点已选的 A → 取消,原 B 补位为 A', eq(F.selToggle(['a', 'b'], 'a'), ['b']));
check('点已选的 B → 取消,A 保留', eq(F.selToggle(['a', 'b'], 'b'), ['a']));
check('已满两张点第三张 → 保 A 换 B', eq(F.selToggle(['a', 'b'], 'c'), ['a', 'c']));
check('单张时点自身 → 清空', eq(F.selToggle(['a'], 'a'), []));

check('swapAB:交换 A/B', eq(F.swapAB(['a', 'b']), ['b', 'a']));
const one = ['a'];
const sw = F.swapAB(one);
check('swapAB:不足两张原样拷贝(不共享引用)', eq(sw, ['a']) && sw !== one);

/* ---------------- ④ URL 反解与清单匹配 ---------------- */
console.log('\n== ④ URL 反解与清单匹配 ==');

const q1 = F.parseArtifactQuery(
  '/api/artifact-file?session=s1&run_id=r9&step=10&art_id=fig&file=vel_thumb.png');
check('缩略图 URL 反解出 session/run_id', eq(q1, { session: 's1', runId: 'r9' }));
check('缺 run_id 时 runId 为空串(最新 run 语义)',
  eq(F.parseArtifactQuery('/api/artifact-file?session=s1&art_id=x'),
    { session: 's1', runId: '' }));
check('非 artifact-file 路径 → null', F.parseArtifactQuery('/api/figures?session=s1') === null);
check('缺 session → null', F.parseArtifactQuery('/api/artifact-file?art_id=x') === null);
check('演示 SVG 无 src(空串)→ null', F.parseArtifactQuery('') === null);

const figs = [{ name: 'vel.png', url: '/b', fullUrl: '/f' }, { name: 'coh.png', url: '/c' }];
check('pickFigure:按名精确匹配', F.pickFigure(figs, 'coh.png').url === '/c');
check('pickFigure:无匹配 → null', F.pickFigure(figs, 'zz.png') === null);
check('pickFigure:清单非数组 → null', F.pickFigure(null, 'vel.png') === null);

/* ---------------- ⑤ bare node 导入安全 ---------------- */
console.log('\n== ⑤ bare node 导入安全 ==');
let initSafe = true;
try { F.initFigCompare(); } catch { initSafe = false; }
check('无 document 环境下 initFigCompare 安全空转(自初始化守卫)', initSafe);

console.log(failed ? `\n${count} 项断言,${failed} 项失败` : `\n全部 ${count} 项断言通过`);
process.exit(failed ? 1 : 0);
