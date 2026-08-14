/* ============================================================
   点位时序卡纯函数锁定(tspoint.js)+ figures.js 演示基建扩展
   —— 最小二乘拟合(速率与 σ)/ 日期换算 / 地图坐标换算 /
      多曲线并集日期轴 / timeSeriesSvg(idx·nodots·nolegend·标签抽稀)/
      mapSvg(markers·hidePoints·note)。全部零 DOM 依赖,Node 直跑。
   ============================================================ */
import './_env.mjs';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  linearFit, dateToYear, fitValues, rateLabel,
  relToLatLon, latLonToRel, unionAxis, shortDates,
} from '../../prototype/js/tspoint.js';
import { timeSeriesSvg, mapSvg } from '../../prototype/js/figures.js';

const close = (a, b, eps = 1e-9) =>
  assert.ok(Math.abs(a - b) < eps, `期望 ${a} ≈ ${b}`);

test('linearFit:精确直线 → 斜率/截距精确,σ=0', () => {
  const fit = linearFit([0, 1, 2, 3], [1, 3, 5, 7]);
  close(fit.slope, 2);
  close(fit.intercept, 1);
  close(fit.sigma, 0);
  assert.equal(fit.n, 4);
});

test('linearFit:带噪数据 → 已知解析解(手算核对)', () => {
  // x̄=1.5 ȳ=1.75 Sxx=5 Sxy=6.5 → slope=1.3 intercept=-0.2
  // 残差 [0.2,-0.1,-0.4,0.3] → RSS=0.3,σ=sqrt(0.3/2/5)
  const fit = linearFit([0, 1, 2, 3], [0, 1, 2, 4]);
  close(fit.slope, 1.3);
  close(fit.intercept, -0.2);
  close(fit.sigma, Math.sqrt(0.03));
});

test('linearFit:退化输入 → null 或 σ=null', () => {
  assert.equal(linearFit([1], [2]), null);                 // 单点无法拟合
  assert.equal(linearFit([2, 2, 2], [1, 2, 3]), null);     // x 全相同
  assert.equal(linearFit([0, 1], [3, 5]).sigma, null);     // n=2 自由度不足
  close(linearFit([0, 1], [3, 5]).slope, 2);
});

test('dateToYear:YYYYMMDD 与演示 MM-DD(2019)一致换算', () => {
  close(dateToYear('20190101'), 2019);
  close(dateToYear('20200101'), 2020);
  close(dateToYear('20190702'), 2019 + 182 / 365.25);
  close(dateToYear('06-10'), dateToYear('20190610'));      // 演示日期同轴
  assert.ok(Number.isNaN(dateToYear('bogus')));
});

test('fitValues + rateLabel:拟合线取值与「速率 X±σ mm/yr」文案', () => {
  const fit = { slope: -12.34, intercept: 24900, sigma: 0.87, n: 9 };
  const vals = fitValues(['20190101', '20200101'], fit);
  close(vals[1] - vals[0], -12.34);
  assert.equal(rateLabel(fit), '速率 -12.3±0.9 mm/yr');
  assert.equal(rateLabel({ slope: 5.04, sigma: null }), '速率 +5.0 mm/yr');
  assert.equal(rateLabel(null), '速率 —(历元不足)');
});

test('地图坐标换算:角点语义 + 相对坐标往返', () => {
  const extent = { lon_min: -118, lon_max: -117.2, lat_min: 35.4, lat_max: 36 };
  const tl = relToLatLon(0, 0, extent);                    // 左上 = (lat_max, lon_min)
  close(tl.lat, 36); close(tl.lon, -118);
  const br = relToLatLon(1, 1, extent);
  close(br.lat, 35.4); close(br.lon, -117.2);
  const { lat, lon } = relToLatLon(0.3, 0.7, extent);
  const back = latLonToRel(lat, lon, extent);
  close(back.u, 0.3); close(back.v, 0.7);
});

test('unionAxis:多曲线日期并集轴与各自下标映射', () => {
  const a = ['20190610', '20190704'];
  const b = ['20190622', '20190704'];
  const axis = unionAxis([a, b]);
  assert.deepEqual(axis.dates, ['20190610', '20190622', '20190704']);
  assert.deepEqual(axis.idxOf(a), [0, 2]);
  assert.deepEqual(axis.idxOf(b), [1, 2]);
});

test('shortDates:同年 MM-DD,跨年 YY-MM,演示日期原样', () => {
  assert.deepEqual(shortDates(['20190610', '20190704']), ['06-10', '07-04']);
  assert.deepEqual(shortDates(['20191210', '20200110']), ['19-12', '20-01']);
  assert.deepEqual(shortDates(['06-10']), ['06-10']);
});

test('timeSeriesSvg 扩展:nodots 不画点 / nolegend 不进图例 / 标签抽稀', () => {
  const dates = Array.from({ length: 20 }, (_, i) => `d${i}`);
  const ts = dates.map((_, i) => i);
  const svg = timeSeriesSvg({
    dates, unit: 'mm',
    series: [
      { name: 'P1', ts, color: '#dc2626' },
      { name: '', ts, color: '#dc2626', dashed: true, nodots: true, nolegend: true },
    ],
  });
  // 数据点圆只来自 P1(20 个);拟合线 nodots 不加圆
  assert.equal((svg.match(/<circle /g) || []).length, 20);
  // 图例色块(rx="2" 的小矩形)只有 P1 一个
  assert.equal((svg.match(/rx="2"/g) || []).length, 1);
  assert.ok(svg.includes('stroke-dasharray'));             // 拟合虚线
  // 20 个刻度按 lstep=3 抽稀:i%3==0 的 7 个 + 末尾 1 个 = 8 个标签
  assert.equal((svg.match(/text-anchor="middle"/g) || []).length, 8);
});

test('timeSeriesSvg 扩展:idx 并集轴定位曲线可缺历元', () => {
  const svg = timeSeriesSvg({
    dates: ['a', 'b', 'c'], unit: 'mm',
    series: [{ name: 'P1', ts: [0, 2], color: '#dc2626', idx: [0, 2] }],
  });
  // 两个数据点分别落在轴首(x=54)与轴尾;不含中间刻度位置的点
  assert.equal((svg.match(/<circle /g) || []).length, 2);
  assert.ok(svg.includes('points="54.0,'));                // idx=0 → 轴首
});

test('mapSvg:markers/note 渲染;演示点位已随假图资产删除(0814B W6)', () => {
  const svg = mapSvg(null, {
    hidePoints: true,
    markers: [{ x: 150, y: 85, color: '#2563eb', label: 'P1' }],
    note: '覆盖 35.40°—36.00°N',
  });
  assert.ok(svg.includes('class="mk"'));
  assert.ok(svg.includes('>P1</text>'));
  assert.ok(svg.includes('覆盖 35.40°—36.00°N'));
  assert.ok(!svg.includes('class="pt"'));                  // 演示点位不复存在
  // 旧签名(选中演示点)保持可调用,但绝不再渲染演示点位 —— 界面诚实化:
  // POINTS 假点位已删除,点位只能由调用方(tspoint 真实取数)经 markers 传入
  const legacy = mapSvg('A');
  assert.ok(/^<svg/.test(legacy));
  assert.ok(!legacy.includes('class="pt"') && !legacy.includes('data-on="1"'));
});
