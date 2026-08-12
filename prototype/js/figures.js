/* ============================================================
   SVG 图件生成（占位演示图，将来换后端 matplotlib 出图）
   全部由本模块构造，属可信来源，可用 innerHTML 注入。
   ============================================================ */
import { h } from './dom.js';

/* Ridgecrest 2019 的 7 个获取日期（12 天重访）；Mw 7.1 主震落在 07-04 与 07-16 之间 */
export const DATES = ['06-10', '06-22', '07-04', '07-16', '07-28', '08-09', '08-15'];

export const POINTS = [
  { id: 'A', name: '断层西侧', x: 105, y: 118, rate: -182.0, std: 12.4, n: 421030,
    ts: [0, -2.1, -4.0, -168.2, -172.4, -178.1, -182.0] },
  { id: 'city', name: '断层东侧', x: 155, y: 88, rate: 96.5, std: 8.7, n: 388914,
    ts: [0, 1.2, 2.4, 88.6, 90.9, 94.1, 96.5] },
  { id: 'gnss', name: 'GNSS P580', x: 215, y: 55, rate: -178.4, std: null, n: 1, ref: true,
    ts: [0, -1.8, -3.6, -164.9, -169.2, -174.8, -178.4] },
];

export const IMAGES = [
  { id: 'vel', name: 'vel_ridgecrest_2019.png', title: '同震位移 · LOS (mm)', meta: 'figure_journal · 2.4 MB', step: 10 },
  { id: 'ts', name: 'ts_ridgecrest.png', title: '累积位移时序 · 7 日期', meta: 'figure_journal · 1.8 MB', step: 10 },
  { id: 'ifg', name: 'ifg_20190704_20190716.png', title: '干涉图 · 同震对 07-04→07-16', meta: 'isce2 interferogram · 640 KB', step: 4 },
  { id: 'coh', name: 'coh_avg.png', title: '相干性均值 · 11 对', meta: 'isce2 filter · 210 KB', step: 5 },
];

/** 把 SVG 源码包进一个 div（可信内容，仅本模块产出）。 */
function wrap(svg) {
  return h('div', { html: svg, style: { display: 'contents' } });
}

/* ---------------- 时序折线图 ---------------- */
export function timeSeriesSvg(o) {
  const w = o.w || 640, hh = o.h || 340;
  const m = { l: 54, r: 18, t: 26, b: 38 };
  const all = o.series.flatMap((s) => s.ts);
  const mn = Math.min(...all), mx = Math.max(...all);
  const pad = (mx - mn) * 0.14 || 1;
  const v0 = mn - pad, v1 = mx + pad;
  const X = (i) => m.l + (i * (w - m.l - m.r)) / (o.dates.length - 1);
  const Y = (v) => hh - m.b - ((v - v0) / (v1 - v0)) * (hh - m.t - m.b);

  let grid = '';
  for (let g = 0; g <= 4; g++) {
    const v = v0 + ((v1 - v0) * g) / 4;
    grid += `<line x1="${m.l}" y1="${Y(v).toFixed(1)}" x2="${w - m.r}" y2="${Y(v).toFixed(1)}" stroke="#e8eaee" stroke-width="1"/>`
         +  `<text x="${m.l - 7}" y="${(Y(v) + 3.5).toFixed(1)}" font-size="10" fill="#9199a5" text-anchor="end">${v.toFixed(0)}</text>`;
  }

  const lines = o.series.map((s) => {
    const pts = s.ts.map((v, i) => `${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join(' ');
    const area = o.series.length === 1
      ? `<polygon points="${m.l},${hh - m.b} ${pts} ${X(s.ts.length - 1).toFixed(1)},${hh - m.b}" fill="${s.color}" opacity=".09"/>`
      : '';
    const dash = s.dashed ? ' stroke-dasharray="5 3"' : '';
    const dots = s.ts.map((v, i) => `<circle cx="${X(i).toFixed(1)}" cy="${Y(v).toFixed(1)}" r="3" fill="${s.color}"/>`).join('');
    return `${area}<polyline points="${pts}" fill="none" stroke="${s.color}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"${dash}/>${dots}`;
  }).join('');

  const xlab = o.dates.map((d, i) =>
    `<text x="${X(i).toFixed(1)}" y="${hh - m.b + 15}" font-size="9" fill="#9199a5" text-anchor="middle">${d}</text>`).join('');

  const legend = o.series.map((s, i) => {
    const lx = w - m.r - 74 * (o.series.length - i);
    return `<rect x="${lx}" y="${m.t - 15}" width="9" height="9" rx="2" fill="${s.color}"/>`
         + `<text x="${lx + 13}" y="${m.t - 7}" font-size="10" fill="#4b5563">${esc(s.name)}</text>`;
  }).join('');

  return `<svg viewBox="0 0 ${w} ${hh}" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="${esc(o.title || '时序曲线')}">
    <rect width="${w}" height="${hh}" fill="#fff"/>
    <rect x="${m.l}" y="${m.t}" width="${w - m.l - m.r}" height="${hh - m.t - m.b}" fill="#fafbfc" stroke="#e5e7eb"/>
    ${grid}${lines}${xlab}${legend}
    <text x="${m.l}" y="15" font-size="11.5" fill="#111827" font-weight="600">${esc(o.title || '')}</text>
    <text x="${w - m.r}" y="${hh - 6}" font-size="9.5" fill="#9199a5" text-anchor="end">${esc(o.unit || '')}</text>
  </svg>`;
}

/* ---------------- 形变速率图 ---------------- */
function velSvg(uid) {
  const g = `vg-${uid}`;
  return `<svg viewBox="0 0 640 340" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Ridgecrest 同震形变场">
    <defs><linearGradient id="${g}" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#7c2d12"/><stop offset=".25" stop-color="#ea580c"/>
      <stop offset=".45" stop-color="#fde047"/><stop offset=".6" stop-color="#d9f99d"/>
      <stop offset=".75" stop-color="#a5f3fc"/><stop offset="1" stop-color="#1e3a8a"/>
    </linearGradient></defs>
    <rect width="640" height="340" fill="#fff"/>
    <rect x="28" y="18" width="524" height="252" fill="url(#${g})" opacity=".88"/>
    <g stroke="#fff" stroke-opacity=".32" fill="none" stroke-width="1.2">
      <path d="M80 268 C130 208 172 198 212 218 S312 188 372 208 S490 128 550 108"/>
      <path d="M50 208 C100 168 142 158 182 178 S272 148 322 163 S432 108 482 93"/>
      <path d="M110 128 C160 98 202 108 242 128 S332 98 382 113 S462 78 512 58"/>
    </g>
    <g fill="none" stroke="#0c1e3a" stroke-opacity=".35" stroke-width="1">
      <path d="M50 188 C110 158 190 173 250 198 S390 163 460 138 S550 98 590 78"/>
      <path d="M70 118 C130 93 210 108 270 128 S390 103 440 83"/>
    </g>
    <g stroke="#0c1e3a" stroke-width="2.5" font-family="sans-serif">
      <circle cx="322" cy="163" r="4.5" fill="#fff"/><text x="331" y="167" font-size="11" fill="#0c1e3a" font-weight="700" stroke="none">断层东侧</text>
      <circle cx="204" cy="226" r="4.5" fill="#fff"/><text x="213" y="235" font-size="11" fill="#0c1e3a" font-weight="700" stroke="none">断层西侧</text>
      <circle cx="446" cy="90" r="4.5" fill="#fff"/><text x="455" y="86" font-size="11" fill="#0c1e3a" font-weight="700" stroke="none">GNSS P580</text>
    </g>
    <rect x="562" y="18" width="13" height="252" fill="#fff" stroke="#d3d7dd"/>
    <rect x="564" y="20" width="9" height="248" fill="url(#${g})" transform="scale(1,-1) translate(0,-288)"/>
    <g font-size="9.5" fill="#4b5563" font-family="sans-serif">
      <text x="580" y="24">+40</text><text x="580" y="148">0</text><text x="580" y="272">-40</text>
      <text x="562" y="292" font-size="9">mm/yr</text>
    </g>
    <text x="30" y="292" font-size="10" fill="#4b5563" font-family="sans-serif">Sentinel-1 · 2019-06-10 — 2019-08-15 · Ridgecrest · LOS 向 · 示意图</text>
    <g font-family="sans-serif"><rect x="30" y="304" width="70" height="3" fill="#0c1e3a"/><text x="30" y="322" font-size="9" fill="#4b5563">20 km</text></g>
  </svg>`;
}

/* ---------------- 干涉条纹图 ---------------- */
function ifgSvg(uid) {
  const g = `ig-${uid}`;
  const rings = [20, 27, 35, 44, 54, 65, 77, 90, 104, 119, 135, 152, 170, 189, 209, 230, 252, 275, 299, 324, 350, 377, 405, 434, 464, 495]
    .map((r, i) => `<circle cx="316" cy="168" r="${r}" stroke-opacity="${i % 2 ? '.34' : '.2'}"/>`).join('');
  return `<svg viewBox="0 0 640 340" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="干涉相位条纹图">
    <defs><radialGradient id="${g}" cx=".5" cy=".5" r=".62">
      <stop offset="0" stop-color="#fef3c7"/><stop offset=".55" stop-color="#fbbf24"/><stop offset="1" stop-color="#d97706"/>
    </radialGradient></defs>
    <rect width="640" height="340" fill="url(#${g})"/>
    <g fill="none" stroke="#92400e" stroke-width="1.2">${rings}</g>
    <g stroke="#78350f" stroke-opacity=".48" stroke-width="1.3" fill="none">
      ${[180, 150, 120, 90, 60, 30].map((rx) =>
        `<ellipse cx="296" cy="158" rx="${rx}" ry="${(rx * 0.66).toFixed(0)}" transform="rotate(-24 296 158)"/>`).join('')}
    </g>
    <circle cx="296" cy="158" r="4.5" fill="#fff" stroke="#78350f" stroke-width="2"/>
    <rect x="14" y="14" width="152" height="26" rx="5" fill="rgba(255,255,255,.82)"/>
    <text x="24" y="31" font-size="11" fill="#78350f" font-weight="700" font-family="sans-serif">wrapped phase (−π … π)</text>
    <text x="30" y="326" font-size="10" fill="#78350f" opacity=".8" font-family="sans-serif">干涉条纹 · 同震对 0704–0716 · Δt=12 d · 示意图</text>
  </svg>`;
}

/* ---------------- 相干性图 ---------------- */
function cohSvg(uid) {
  const g = `cg-${uid}`;
  return `<svg viewBox="0 0 640 340" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="相干性均值分布图">
    <defs><linearGradient id="${g}" x1="0" y1="1" x2="0" y2="0">
      <stop offset="0" stop-color="#052e16"/><stop offset=".5" stop-color="#16a34a"/><stop offset="1" stop-color="#86efac"/>
    </linearGradient></defs>
    <rect width="640" height="340" fill="#052e16"/>
    <rect x="24" y="18" width="528" height="252" fill="url(#${g})" opacity=".55"/>
    <g fill="none" stroke="#bbf7d0" stroke-opacity=".2" stroke-width="1.2">
      <path d="M24 88 C140 58 236 98 350 68 S520 40 552 78"/>
      <path d="M24 158 C150 128 266 168 402 138 S528 110 552 148"/>
      <path d="M24 238 C110 213 244 248 380 218 S528 198 552 233"/>
    </g>
    <g fill="#86efac" opacity=".7">
      <ellipse cx="204" cy="118" rx="50" ry="36" transform="rotate(-12 204 118)"/>
      <ellipse cx="400" cy="198" rx="42" ry="30" transform="rotate(8 400 198)"/>
      <ellipse cx="126" cy="236" rx="34" ry="24" transform="rotate(-20 126 236)"/>
      <ellipse cx="504" cy="118" rx="28" ry="20" transform="rotate(15 504 118)"/>
    </g>
    <rect x="14" y="14" width="176" height="26" rx="5" fill="rgba(0,0,0,.55)"/>
    <text x="24" y="31" font-size="11" fill="#dcfce7" font-weight="700" font-family="sans-serif">相干性 γ · 11 对均值 0.62</text>
    <rect x="562" y="18" width="13" height="252" fill="#fff" stroke="#d3d7dd"/>
    <rect x="564" y="20" width="9" height="248" fill="url(#${g})"/>
    <g font-size="9.5" fill="#dcfce7" font-family="sans-serif">
      <text x="580" y="24">1.0</text><text x="580" y="148">0.5</text><text x="580" y="272">0.0</text>
    </g>
    <text x="26" y="292" font-size="10" fill="#dcfce7" opacity=".85" font-family="sans-serif">亮=高相干（城区/裸岩）· 暗=低相干（冲积扇/植被）</text>
  </svg>`;
}

let uidSeq = 0;

/** 取图件 SVG 源码。每次调用生成唯一的 gradient id，避免多处渲染时 defs 冲突。 */
export function figureSvg(id) {
  const uid = ++uidSeq;
  switch (id) {
    case 'vel': return velSvg(uid);
    case 'ifg': return ifgSvg(uid);
    case 'coh': return cohSvg(uid);
    case 'ts': return timeSeriesSvg({
      title: 'Ridgecrest 2019 · 累积位移 (LOS mm)', unit: 'mm', dates: DATES,
      series: [
        { name: '断层西侧', ts: POINTS[0].ts, color: '#dc2626' },
        { name: 'GNSS P580', ts: POINTS[2].ts, color: '#0c1e3a', dashed: true },
      ],
    });
    default: return '';
  }
}

export function figureNode(id) {
  return wrap(figureSvg(id));
}

/** 右侧小地图：形变底图 + 可点点位。 */
export function mapSvg(selectedId) {
  const uid = ++uidSeq;
  const pts = POINTS.map((p) => {
    const on = p.id === selectedId ? ' data-on="1"' : '';
    return `<g class="pt" data-id="${esc(p.id)}"${on} tabindex="0" role="button" aria-label="${esc(p.name)}">`
      + `<circle cx="${p.x}" cy="${p.y}" r="4.2"/>`
      + `<text x="${p.x + 8}" y="${p.y + 4}" font-size="10" fill="#0c1e3a" font-weight="600">${esc(p.name)}</text></g>`;
  }).join('');
  return `<svg viewBox="0 0 300 170" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="形变速率小地图">
    <defs><linearGradient id="mg-${uid}" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#7c2d12"/><stop offset=".3" stop-color="#ea580c"/>
      <stop offset=".5" stop-color="#fde047"/><stop offset=".7" stop-color="#a5f3fc"/><stop offset="1" stop-color="#1e3a8a"/>
    </linearGradient></defs>
    <rect width="300" height="170" fill="url(#mg-${uid})" opacity=".85"/>
    <g stroke="#fff" stroke-opacity=".28" fill="none">
      <path d="M18 150 C58 120 108 115 148 130 S228 105 285 80"/>
      <path d="M8 105 C48 85 98 85 138 100 S218 75 268 60"/>
    </g>
    ${pts}
  </svg>`;
}

function esc(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
