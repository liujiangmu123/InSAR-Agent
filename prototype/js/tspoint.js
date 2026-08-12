/* ============================================================
   点位时序卡(面板 2「影像」下半区):地图-曲线双联动
   —— RESEARCH-insar-viewer-ux 头号结论「点图出时序」的最小闭环,
   与 MintPy tsview / EGMS / InSAR Explorer 同构。

   数据源优先级(与 gallery.js 同策略):
     1. GET /api/timeseries-point —— run 工作区 MintPy timeseries*.h5 的
        单像元真实时序;挂载时用 (row=0,col=0) 探测一次,顺带学到
        extent/shape 网格元数据(打在 NaN 像元上也能从 404 结构化 detail
        里学到),之后地图点击都能换算坐标;
     2. 后端不可达 / 模拟运行无真实产物 —— 回落 figures.js 演示曲线
        (POINTS/DATES),横幅注明原因。

   交互(EGMS 式 hold-on):点击选点换曲线;Shift+点击叠加对比,
   至多 3 条(不同色);「清除」重置。真实曲线附最小二乘线性拟合虚线
   (零依赖)与「速率 X±σ mm/yr」标注,σ 由拟合残差估计。
   ============================================================ */
import { h, icon, toast } from './dom.js';
import { S } from './state.js';
import { timeSeriesSvg, mapSvg, POINTS, DATES } from './figures.js';

/* ---------------- 纯函数(Node 单测直接可跑,零 DOM 依赖) ---------------- */

/** 'YYYYMMDD'(真实)或 'MM-DD'(演示,按 2019 年)→ 十进制年。 */
export function dateToYear(s) {
  const t = String(s);
  let y, mo, d;
  if (/^\d{8}$/.test(t)) {
    y = +t.slice(0, 4); mo = +t.slice(4, 6); d = +t.slice(6, 8);
  } else {
    const mm = t.match(/^(\d{1,2})-(\d{1,2})$/);
    if (!mm) return NaN;
    y = 2019; mo = +mm[1]; d = +mm[2];
  }
  const ms = Date.UTC(y, mo - 1, d) - Date.UTC(y, 0, 1);
  return y + ms / (365.25 * 86400e3);
}

/** 最小二乘线性拟合:xs(十进制年)→ ys(mm)。
    σ_slope = sqrt(RSS / (n-2) / Sxx)(拟合残差估计);
    n<3 残差自由度不足 σ 为 null;n<2 或 x 全相同无法拟合返回 null。 */
export function linearFit(xs, ys) {
  const n = Math.min(xs.length, ys.length);
  if (n < 2) return null;
  let sx = 0, sy = 0;
  for (let i = 0; i < n; i++) { sx += xs[i]; sy += ys[i]; }
  const mx = sx / n, my = sy / n;
  let sxx = 0, sxy = 0;
  for (let i = 0; i < n; i++) {
    sxx += (xs[i] - mx) * (xs[i] - mx);
    sxy += (xs[i] - mx) * (ys[i] - my);
  }
  if (sxx === 0) return null;
  const slope = sxy / sxx;
  const intercept = my - slope * mx;
  let rss = 0;
  for (let i = 0; i < n; i++) {
    const r = ys[i] - intercept - slope * xs[i];
    rss += r * r;
  }
  const sigma = n > 2 ? Math.sqrt(rss / (n - 2) / sxx) : null;
  return { slope, intercept, sigma, n };
}

/** 拟合线在各历元上的取值(供 timeSeriesSvg 画同轴虚线)。 */
export function fitValues(dates, fit) {
  return dates.map((d) => fit.intercept + fit.slope * dateToYear(d));
}

/** 「速率 X±σ mm/yr」标注文本。 */
export function rateLabel(fit) {
  if (!fit) return '速率 —(历元不足)';
  const v = `${fit.slope >= 0 ? '+' : ''}${fit.slope.toFixed(1)}`;
  return fit.sigma === null ? `速率 ${v} mm/yr`
       : `速率 ${v}±${fit.sigma.toFixed(1)} mm/yr`;
}

/** 小地图相对坐标 (u,v ∈ 0..1,v 自上而下) → 地理坐标。 */
export function relToLatLon(u, v, extent) {
  return {
    lat: extent.lat_max - v * (extent.lat_max - extent.lat_min),
    lon: extent.lon_min + u * (extent.lon_max - extent.lon_min),
  };
}

/** 地理坐标 → 小地图相对坐标(把已选点标回地图)。 */
export function latLonToRel(lat, lon, extent) {
  return {
    u: (lon - extent.lon_min) / ((extent.lon_max - extent.lon_min) || 1),
    v: (extent.lat_max - lat) / ((extent.lat_max - extent.lat_min) || 1),
  };
}

/** 多曲线共用日期轴:并集升序;idxOf 把单曲线日期映射到并集下标。 */
export function unionAxis(seriesDates) {
  const set = new Set();
  for (const ds of seriesDates) for (const d of ds) set.add(d);
  const dates = [...set].sort();
  const pos = new Map(dates.map((d, i) => [d, i]));
  return { dates, idxOf: (ds) => ds.map((d) => pos.get(d)) };
}

/** 'YYYYMMDD' → 短标签:同年 'MM-DD',跨年 'YY-MM'。 */
export function shortDates(dates) {
  const years = new Set(dates.map((d) => String(d).slice(0, 4)));
  return dates.map((d) => {
    const t = String(d);
    if (!/^\d{8}$/.test(t)) return t;   // 演示 'MM-DD' 原样
    return years.size > 1 ? `${t.slice(2, 4)}-${t.slice(4, 6)}`
                          : `${t.slice(4, 6)}-${t.slice(6, 8)}`;
  });
}

/* ---------------- 数据源与联动状态 ---------------- */

const COLORS = ['#dc2626', '#2563eb', '#059669'];
const MAX_CURVES = 3;

/* 模块级状态:dock 高频 refresh 会整体重挂载,曲线与探测结果放这里
   才不会每次刷新都清空/重拉(与 gallery.js 的缓存同思路)。 */
const T = {
  key: '',          // 会话键:切会话时整体重置
  probed: false,    // 探测是否已完成(未完成 → 加载态)
  probing: null,    // in-flight 探测 promise(去重)
  real: false,      // 真实模式?
  reason: '',       // 演示回落原因(横幅文案)
  grid: null,       // { extent, shape, refPoint, source }
  curves: [],       // 真实曲线 [{ dates, values, point, source }]
  demoSel: [POINTS[0].id],   // 演示模式选中点 id(hold-on 至多 3)
  busy: false,      // 点击取数中
};

function resetForSession(key) {
  T.key = key;
  T.probed = false;
  T.probing = null;
  T.real = false;
  T.reason = '';
  T.grid = null;
  T.curves = [];
  T.demoSel = [POINTS[0].id];
  T.busy = false;
}

async function fetchPoint(params) {
  const qs = new URLSearchParams({ session: S.sessionId, ...params });
  const resp = await fetch(`/api/timeseries-point?${qs}`);
  const body = await resp.json().catch(() => null);
  return { status: resp.status, body };
}

function gridOf(d) {
  return {
    extent: d.extent || null,
    shape: d.shape || null,
    refPoint: d.ref_point || null,
    source: d.source || '',
  };
}

/** 挂载探测:一次请求学到「有无真实时序 + 网格元数据」。 */
function probe() {
  if (T.probing) return T.probing;
  T.probing = (async () => {
    if (typeof location !== 'undefined' && location.protocol === 'file:') {
      T.real = false;
      T.reason = '静态打开(file://)无后端 —— 展示演示曲线。';
      return;
    }
    try {
      const { status, body } = await fetchPoint({ row: '0', col: '0' });
      const detail = body && body.detail;
      if (status === 200 && body) {
        T.real = true;
        T.grid = gridOf(body);
      } else if (status === 404 && detail && detail.error === 'pixel_invalid') {
        // 探测像元恰好无效:仍是真实模式,网格元数据从结构化 detail 学
        T.real = true;
        T.grid = gridOf(detail);
      } else if (status === 404 && detail
                 && (detail.error === 'placeholder' || detail.error === 'no_timeseries')) {
        T.real = false;
        T.reason = '模拟运行无真实时序,展示演示曲线。';
      } else if (status === 404) {
        T.real = false;
        T.reason = '该会话还没有运行记录 —— 展示演示曲线。';
      } else {
        T.real = false;
        T.reason = `后端响应异常(HTTP ${status})—— 展示演示曲线。`;
      }
    } catch {
      T.real = false;
      T.reason = '后端不可达 —— 展示演示曲线。';
    } finally {
      T.probed = true;
    }
  })();
  return T.probing;
}

/** 真实模式点击:相对坐标 → lat/lon(有 extent)或 row/col(radar 兜底)。 */
async function pickReal(u, v, hold, host) {
  const g = T.grid || {};
  let params = null;
  if (g.extent) {
    const { lat, lon } = relToLatLon(u, v, g.extent);
    params = { lat: lat.toFixed(6), lon: lon.toFixed(6) };
  } else if (g.shape) {
    params = {
      row: String(Math.min(g.shape.rows - 1, Math.max(0, Math.floor(v * g.shape.rows)))),
      col: String(Math.min(g.shape.cols - 1, Math.max(0, Math.floor(u * g.shape.cols)))),
    };
  } else {
    return;   // 探测没学到网格元数据,无从换算
  }
  T.busy = true;
  render(host);
  try {
    const { status, body } = await fetchPoint(params);
    const detail = body && body.detail;
    if (status === 200 && body) {
      if (body.ref_point) T.grid.refPoint = body.ref_point;
      const pt = { dates: body.dates, values: body.values_mm,
                   point: body.point, source: body.source };
      // hold-on:保留最近的 MAX_CURVES-1 条再叠加;普通点击整体替换
      const next = hold ? T.curves.slice(-(MAX_CURVES - 1)) : [];
      const dup = next.some((c) => c.point.row === pt.point.row
                                && c.point.col === pt.point.col);
      if (!dup) next.push(pt);
      T.curves = next;
    } else if (detail && detail.error === 'pixel_invalid') {
      toast('该像元无有效数据(NaN)—— 请换一个位置');
    } else if (detail && detail.error === 'out_of_coverage') {
      toast('坐标超出数据覆盖范围');
    } else {
      toast(String((detail && detail.message) || detail || `取时序失败(HTTP ${status})`));
    }
  } catch {
    toast('后端不可达,取时序失败');
  }
  T.busy = false;
  render(host);
}

/** 演示模式点击:Shift 叠加/取消,普通点击单选(与旧 tsCard 行为兼容)。 */
function pickDemo(id, hold) {
  if (hold) {
    T.demoSel = T.demoSel.includes(id)
      ? T.demoSel.filter((x) => x !== id)
      : [...T.demoSel, id].slice(-MAX_CURVES);
    if (!T.demoSel.length) T.demoSel = [id];
  } else {
    T.demoSel = [id];
  }
  S.selectedPoint = T.demoSel[T.demoSel.length - 1];
}

/* ---------------- 视图 ---------------- */

/** 面板 2 下半区入口:dock.js 只调用这一个函数。 */
export function mountSpatial(host) {
  if (T.key !== S.sessionId) resetForSession(S.sessionId);
  render(host);
  probe().then(() => { if (host.isConnected) render(host); });
}

function render(host) {
  host.replaceChildren(mapCard(host), tsCard(host));
}

function fmt(x, digits = 3) {
  return x === null || x === undefined ? '—' : Number(x).toFixed(digits);
}

function mapCard(host) {
  const real = T.probed && T.real;
  const markers = real ? T.curves.map((c, i) => {
    const rel = (c.point.lat !== null && T.grid && T.grid.extent)
      ? latLonToRel(c.point.lat, c.point.lon, T.grid.extent)
      : { u: (c.point.col + 0.5) / ((T.grid && T.grid.shape && T.grid.shape.cols) || 1),
          v: (c.point.row + 0.5) / ((T.grid && T.grid.shape && T.grid.shape.rows) || 1) };
    return { x: rel.u * 300, y: rel.v * 170, color: COLORS[i], label: `P${i + 1}` };
  }) : [];
  const note = real
    ? (T.grid && T.grid.extent
        ? `覆盖 ${fmt(T.grid.extent.lat_min, 2)}°—${fmt(T.grid.extent.lat_max, 2)}°N(底图为示意)`
        : `radar 坐标 · ${T.grid.shape.rows}×${T.grid.shape.cols} 网格(相对位置取 row/col)`)
    : '';

  const canvas = h('div', {
    class: 'canvas',
    html: mapSvg(real ? null : T.demoSel[T.demoSel.length - 1],
                 { hidePoints: real, markers, note }),
  });
  if (real) {
    canvas.style.cursor = 'crosshair';
    canvas.setAttribute('role', 'button');
    canvas.setAttribute('aria-label', '点击小地图任意位置取该像元时序;Shift+点击叠加对比');
    canvas.addEventListener('click', (e) => {
      const svg = canvas.querySelector('svg');
      const r = svg ? svg.getBoundingClientRect() : null;
      if (!r || !r.width || !r.height) return;
      const u = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
      const v = Math.min(1, Math.max(0, (e.clientY - r.top) / r.height));
      pickReal(u, v, e.shiftKey, host);
    });
  } else {
    canvas.querySelectorAll('.pt').forEach((g) => {
      if (T.demoSel.includes(g.dataset.id)) g.setAttribute('data-on', '1');
      const pick = (e) => { pickDemo(g.dataset.id, e.shiftKey); render(host); };
      g.addEventListener('click', pick);
      g.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(e); }
      });
    });
  }

  const clearBtn = h('button', {
    class: 'pin', type: 'button', 'aria-label': '清除已选点位',
    onclick: () => { T.curves = []; T.demoSel = [POINTS[0].id]; render(host); },
  }, '清除');
  // 演示态可手动重探:运行产出真实 h5 后无需刷新页面即可切到真实模式
  const reprobeBtn = (T.probed && !T.real) ? h('button', {
    class: 'pin', type: 'button', 'aria-label': '重新探测真实时序产物',
    onclick: () => {
      T.probed = false; T.probing = null;
      render(host);
      probe().then(() => { if (host.isConnected) render(host); });
    },
  }, '重新探测') : null;
  const hint = h('span', {
    class: 'mono',
    style: { fontSize: '10px', color: 'var(--text-3)', alignSelf: 'center' },
  }, `Shift+点击叠加对比(至多 ${MAX_CURVES} 条)`);

  return h('div', { class: 'mapcard' },
    h('div', { class: 'bar' }, icon('target'),
      real ? '形变时序 · 点击地图取像元' : '形变速率 · LOS (mm/yr)',
      h('span', { class: 'mono' },
        real ? (T.grid.source || '') : 'vel_ridgecrest_2019.png')),
    canvas,
    h('div', { class: 'pins' },
      ...(real ? [] : POINTS.map((p) => h('button', {
        class: 'pin', type: 'button', 'aria-pressed': String(T.demoSel.includes(p.id)),
        onclick: (e) => { pickDemo(p.id, e.shiftKey); render(host); },
      }, p.name))),
      clearBtn, reprobeBtn, hint));
}

function tsCard(host) {
  if (!T.probed) {
    return h('div', { class: 'tscard', dataset: { mode: 'loading' } },
      h('div', { class: 'hd' }, '点位时序',
        h('span', { class: 'mono' }, 'GET /api/timeseries-point')),
      h('p', { class: 'blurb' }, '正在探测真实时序产物…'));
  }
  return T.real ? realCard(host) : demoCard();
}

/* ---- 真实模式:折线 + 数据点 + 拟合虚线 + 速率标注 ---- */
function realCard() {
  const head = (extra) => h('div', { class: 'hd' },
    `点位时序${T.curves.length > 1 ? ` · ${T.curves.length} 点对比` : ''}`,
    T.busy ? h('span', { style: { fontSize: '10.5px', color: 'var(--text-2)' } }, '加载中…') : null,
    h('span', { class: 'mono' }, extra));

  if (!T.curves.length) {
    return h('div', { class: 'tscard', dataset: { mode: 'real' } },
      head(T.grid.source || ''),
      h('p', { class: 'blurb' },
        '点击左侧小地图任意位置,读取该像元的真实时序曲线(MintPy ' +
        (T.grid.source || 'timeseries*.h5') + ');Shift+点击叠加至多 3 条对比。'));
  }

  const named = T.curves.map((c, i) => ({ ...c, name: `P${i + 1}`, color: COLORS[i] }));
  const axis = unionAxis(named.map((c) => c.dates));
  const series = [];
  const statSpans = [];
  const posParts = [];
  for (const c of named) {
    const idx = axis.idxOf(c.dates);
    series.push({ name: c.name, ts: c.values, color: c.color, idx });
    const fit = linearFit(c.dates.map(dateToYear), c.values);
    if (fit) {
      // 拟合虚线与数据曲线同轴同色:不画点、不进图例
      series.push({ name: '', ts: fitValues(c.dates, fit), color: c.color,
                    idx, dashed: true, nodots: true, nolegend: true });
    }
    statSpans.push(h('span', null, `${c.name} `,
      h('b', { style: { color: c.color } }, rateLabel(fit)),
      ` · ${c.values.length} 历元`));
    posParts.push(c.point.lat !== null
      ? `${c.name}(${fmt(c.point.lat)}, ${fmt(c.point.lon)})`
      : `${c.name}(row ${c.point.row}, col ${c.point.col})`);
  }

  const svg = timeSeriesSvg({
    title: '', unit: 'mm', w: 340, h: 190,
    dates: shortDates(axis.dates), series,
  });
  const ref = T.grid.refPoint;
  const meta = [
    posParts.join(' · '),
    ref ? `参考点 (${fmt(ref.lat)}, ${fmt(ref.lon)})` : null,
    `数据源 ${named[0].source} · LOS 位移(mm)· 虚线为最小二乘线性拟合`,
  ].filter(Boolean).join('\n');

  return h('div', { class: 'tscard', dataset: { mode: 'real' } },
    head(named[0].source),
    h('div', { html: svg }),
    h('div', { class: 'stats' }, ...statSpans),
    h('p', { class: 'blurb', style: { whiteSpace: 'pre-line' } }, meta));
}

/* ---- 演示回落:figures.js 演示曲线(POINTS/DATES),标注原因 ---- */
function demoCard() {
  const sel = T.demoSel
    .map((id) => POINTS.find((p) => p.id === id))
    .filter(Boolean);
  const series = sel.map((p, i) => ({
    name: p.name, ts: p.ts,
    color: p.ref ? '#0c1e3a' : COLORS[i % COLORS.length],
    dashed: !!p.ref,
  }));
  const svg = timeSeriesSvg({
    title: '', unit: 'mm', w: 340, h: 190, dates: DATES, series,
  });
  const stats = sel.map((p) => h('span', null, `${p.name} `,
    h('b', { style: { color: p.rate < 0 ? 'var(--bad)' : 'var(--accent)' } },
      `${p.rate > 0 ? '+' : ''}${p.rate} mm`),
    p.std !== null ? ` ±${p.std}` : ''));

  return h('div', { class: 'tscard', dataset: { mode: 'demo' } },
    h('div', { class: 'hd' },
      `${sel.length > 1 ? `${sel.length} 点对比` : sel[0] ? sel[0].name : ''} · 点位时序`,
      h('span', { class: 'mono' }, '演示曲线')),
    h('div', { class: 'note is-stale', role: 'status' }, icon('warn'),
      h('span', null, T.reason || '模拟运行无真实时序,展示演示曲线。')),
    h('div', { html: svg }),
    h('div', { class: 'stats' }, ...stats),
    h('p', { class: 'blurb' },
      '演示数据(figures.js 手绘)。真实运行产出 mintpy/timeseries*.h5 后,' +
      '此处自动切换为点图取真实时序。'));
}
