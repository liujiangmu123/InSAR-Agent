/* ============================================================
   点位时序卡(面板 2「影像」下半区):地图-曲线双联动
   —— RESEARCH-insar-viewer-ux 头号结论「点图出时序」的最小闭环,
   与 MintPy tsview / EGMS / InSAR Explorer 同构。

   数据源:GET /api/timeseries-point —— run 工作区 MintPy timeseries*.h5
   的单像元真实时序;挂载时用 (row=0,col=0) 探测一次,顺带学到
   extent/shape 网格元数据(打在 NaN 像元上也能从 404 结构化 detail
   里学到),之后地图点击都能换算坐标。

   失败语义(无演示回落,states 统一构造器):
     - 后端不可达 / file:// / 响应异常 → renderError(带重试=重新探测);
     - 可达但无真实时序(无 run / 模拟运行 / 未出 h5)→ renderEmpty
       (带运行引导),并保留「重新探测」;绝不渲染演示假曲线。

   交互(EGMS 式 hold-on):点击选点换曲线;Shift+点击叠加对比,
   至多 3 条(不同色);「清除」重置。真实曲线附最小二乘线性拟合虚线
   (零依赖)与「速率 X±σ mm/yr」标注,σ 由拟合残差估计。
   timeSeriesSvg / mapSvg 在此只作渲染器:折线画的是后端真实数据,
   小地图为示意底图(仅承接点击换算坐标,已在注记说明)。
   ============================================================ */
import { h, icon, toast } from './dom.js';
import { S } from './state.js';
import { timeSeriesSvg, mapSvg } from './figures.js';
import * as ES from './emptystate.js';   // states 接入:空态/骨架/错误态统一构造器

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
  state: 'error',   // 探测结论:real(有真实时序)| empty(可达但无数据)| error(不可达/异常)
  reason: '',       // empty / error 的原因文案
  grid: null,       // { extent, shape, refPoint, source }
  curves: [],       // 真实曲线 [{ dates, values, point, source }]
  busy: false,      // 点击取数中
};

function resetForSession(key) {
  T.key = key;
  T.probed = false;
  T.probing = null;
  T.state = 'error';
  T.reason = '';
  T.grid = null;
  T.curves = [];
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
      T.state = 'error';
      T.reason = '静态打开(file://)无后端,无法读取真实时序。';
      return;
    }
    try {
      const { status, body } = await fetchPoint({ row: '0', col: '0' });
      const detail = body && body.detail;
      if (status === 200 && body) {
        T.state = 'real';
        T.grid = gridOf(body);
      } else if (status === 404 && detail && detail.error === 'pixel_invalid') {
        // 探测像元恰好无效:仍是真实模式,网格元数据从结构化 detail 学
        T.state = 'real';
        T.grid = gridOf(detail);
      } else if (status === 404 && detail
                 && (detail.error === 'placeholder' || detail.error === 'no_timeseries')) {
        T.state = 'empty';
        T.reason = '本次为模拟运行,没有真实时序产物。';
      } else if (status === 404) {
        T.state = 'empty';
        T.reason = '该会话还没有运行记录。';
      } else {
        T.state = 'error';
        T.reason = `后端响应异常(HTTP ${status})。`;
      }
    } catch {
      T.state = 'error';
      T.reason = '后端不可达。';
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

/* ---------------- 视图 ---------------- */

/** 面板 2 下半区入口:dock.js 只调用这一个函数。 */
export function mountSpatial(host) {
  if (T.key !== S.sessionId) resetForSession(S.sessionId);
  render(host);
  probe().then(() => { if (host.isConnected) render(host); });
}

function render(host) {
  if (!T.probed) {
    host.replaceChildren(loadingCard());
    return;
  }
  // 无真实时序可用:整个下半区只给一张诚实的状态卡(空态/错误态),
  // 不再渲染演示地图与演示曲线
  if (T.state !== 'real') {
    host.replaceChildren(stateCard(host));
    return;
  }
  host.replaceChildren(mapCard(host), realCard(host));
}

/** 重新探测:清探测缓存后重跑 probe(真实产物落盘后无需刷新页面)。 */
function reprobe(host) {
  T.probed = false;
  T.probing = null;
  render(host);
  probe().then(() => { if (host.isConnected) render(host); });
}

/** 探测期占位卡。 */
function loadingCard() {
  return h('div', { class: 'tscard', dataset: { mode: 'loading' } },
    h('div', { class: 'hd' }, '点位时序',
      h('span', { class: 'mono' }, 'GET /api/timeseries-point')),
    // states 接入:请求中分支 → 段落骨架(探测真实时序产物期间)
    ES.renderSkeleton(null, { kind: 'text', rows: 3, label: '正在探测真实时序产物' }));
}

/** 空态/错误态卡:后端不可达 → 错误态带重试;可达但无数据 → 空态带运行引导。 */
function stateCard(host) {
  const body = T.state === 'error'
    ? ES.renderError(null, {
        message: `点位时序读取失败——${T.reason}`,
        retry: () => reprobe(host),
      })
    : ES.renderEmpty(null, {
        icon: 'target', title: '还没有真实时序产物',
        hint: `${T.reason}运行流水线产出 mintpy/timeseries*.h5 后,点击地图即可读取单像元真实时序。`,
        action: { label: '运行流水线', event: 'states:run-pipeline' },
      });
  return h('div', { class: 'tscard', dataset: { mode: T.state } },
    h('div', { class: 'hd' }, '点位时序',
      h('span', { class: 'mono' }, 'GET /api/timeseries-point')),
    body,
    // 空态保留「重新探测」:运行完成后无需刷新页面即可切到真实模式
    T.state === 'empty' ? h('div', { class: 'pins' }, h('button', {
      class: 'pin', type: 'button', 'aria-label': '重新探测真实时序产物',
      onclick: () => reprobe(host),
    }, '重新探测')) : null);
}

function fmt(x, digits = 3) {
  return x === null || x === undefined ? '—' : Number(x).toFixed(digits);
}

function mapCard(host) {
  const markers = T.curves.map((c, i) => {
    const rel = (c.point.lat !== null && T.grid && T.grid.extent)
      ? latLonToRel(c.point.lat, c.point.lon, T.grid.extent)
      : { u: (c.point.col + 0.5) / ((T.grid && T.grid.shape && T.grid.shape.cols) || 1),
          v: (c.point.row + 0.5) / ((T.grid && T.grid.shape && T.grid.shape.rows) || 1) };
    return { x: rel.u * 300, y: rel.v * 170, color: COLORS[i], label: `P${i + 1}` };
  });
  const note = (T.grid && T.grid.extent)
    ? `覆盖 ${fmt(T.grid.extent.lat_min, 2)}°—${fmt(T.grid.extent.lat_max, 2)}°N(底图为示意)`
    : `radar 坐标 · ${T.grid.shape.rows}×${T.grid.shape.cols} 网格(相对位置取 row/col)`;

  // mapSvg 仅作点击画布:底图为示意(注记已声明),点位与标记均来自真实取数
  const canvas = h('div', {
    class: 'canvas',
    html: mapSvg(null, { hidePoints: true, markers, note }),
  });
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

  const clearBtn = h('button', {
    class: 'pin', type: 'button', 'aria-label': '清除已选点位',
    onclick: () => { T.curves = []; render(host); },
  }, '清除');
  const hint = h('span', {
    class: 'mono',
    style: { fontSize: '10px', color: 'var(--text-3)', alignSelf: 'center' },
  }, `Shift+点击叠加对比(至多 ${MAX_CURVES} 条)`);

  return h('div', { class: 'mapcard' },
    h('div', { class: 'bar' }, icon('target'),
      '形变时序 · 点击地图取像元',
      h('span', { class: 'mono' }, T.grid.source || '')),
    canvas,
    h('div', { class: 'pins' }, clearBtn, hint));
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
      // states 接入:无数据分支 → 空态卡(真实模式已就绪,引导选点)
      ES.renderEmpty(null, { icon: 'target', title: '还没有选择点位',
        hint: '点击上方小地图任意位置即可生成——读取该像元的真实时序曲线(MintPy ' +
          (T.grid.source || 'timeseries*.h5') + ');Shift+点击叠加至多 3 条对比。' }));
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

