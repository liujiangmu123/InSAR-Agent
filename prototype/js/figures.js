/* ============================================================
   SVG 图件渲染器 —— 只画真实数据,不再内置任何演示图。
   在用消费方:tspoint.js(timeSeriesSvg 画后端单像元时序真实曲线,
   mapSvg 作点击画布,点位与标记全部来自真实取数)。
   产出仅由本模块构造,属可信来源,可用 innerHTML 注入。

   界面诚实化(0814B W6):原演示图资产 —— IMAGES 假图清单、POINTS 假
   点位、DATES 假日期轴、velSvg/ifgSvg/cohSvg 手绘假图与其路由
   figureSvg/figureNode —— 已全部删除,真实后端下界面不得出现任何
   写死的假数据。文件尾部的空壳导出仅为并行分支期间保持 import 绑定
   可解析(W3 正在删除 stream.js 里唯一的死代码消费方 resultCard/
   openLightbox);W3 清理 stream.js 的 import 行后应连壳一起删除。
   ============================================================ */
import { h } from './dom.js';

/* ---------------- 时序折线图 ----------------
   series 可选扩展(tspoint.js 真实曲线用):
     idx      —— 各点在日期轴上的下标(多曲线日期并集轴,曲线可缺历元);
     nodots   —— 不画数据点圆点(线性拟合虚线用);
     nolegend —— 不进图例(拟合线与其数据曲线同色,无需重复占位)。
   日期轴超过 8 个刻度时自动抽稀标签,避免真实时序(几十个历元)挤成一团。 */
export function timeSeriesSvg(o) {
  const w = o.w || 640, hh = o.h || 340;
  const m = { l: 54, r: 18, t: 26, b: 38 };
  const all = o.series.flatMap((s) => s.ts);
  const mn = Math.min(...all), mx = Math.max(...all);
  const pad = (mx - mn) * 0.14 || 1;
  const v0 = mn - pad, v1 = mx + pad;
  const nx = Math.max(1, o.dates.length - 1);   // 单历元防除零
  const X = (i) => m.l + (i * (w - m.l - m.r)) / nx;
  const Y = (v) => hh - m.b - ((v - v0) / (v1 - v0)) * (hh - m.t - m.b);

  let grid = '';
  for (let g = 0; g <= 4; g++) {
    const v = v0 + ((v1 - v0) * g) / 4;
    grid += `<line x1="${m.l}" y1="${Y(v).toFixed(1)}" x2="${w - m.r}" y2="${Y(v).toFixed(1)}" stroke="#e8eaee" stroke-width="1"/>`
         +  `<text x="${m.l - 7}" y="${(Y(v) + 3.5).toFixed(1)}" font-size="10" fill="#9199a5" text-anchor="end">${v.toFixed(0)}</text>`;
  }

  const lines = o.series.map((s) => {
    const px = s.idx && s.idx.length === s.ts.length ? s.idx : s.ts.map((_, i) => i);
    const pts = s.ts.map((v, i) => `${X(px[i]).toFixed(1)},${Y(v).toFixed(1)}`).join(' ');
    const area = o.series.length === 1
      ? `<polygon points="${X(px[0]).toFixed(1)},${hh - m.b} ${pts} ${X(px[px.length - 1]).toFixed(1)},${hh - m.b}" fill="${s.color}" opacity=".09"/>`
      : '';
    const dash = s.dashed ? ' stroke-dasharray="5 3"' : '';
    const dots = s.nodots ? ''
      : s.ts.map((v, i) => `<circle cx="${X(px[i]).toFixed(1)}" cy="${Y(v).toFixed(1)}" r="3" fill="${s.color}"/>`).join('');
    return `${area}<polyline points="${pts}" fill="none" stroke="${s.color}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"${dash}/>${dots}`;
  }).join('');

  const lstep = Math.max(1, Math.ceil(o.dates.length / 8));   // 标签抽稀步长
  const xlab = o.dates.map((d, i) =>
    (i % lstep !== 0 && i !== o.dates.length - 1) ? ''
      : `<text x="${X(i).toFixed(1)}" y="${hh - m.b + 15}" font-size="9" fill="#9199a5" text-anchor="middle">${d}</text>`).join('');

  const vis = o.series.filter((s) => !s.nolegend);
  const legend = vis.map((s, i) => {
    const lx = w - m.r - 74 * (vis.length - i);
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

let uidSeq = 0;

/** 右侧小地图(tspoint.js 的点击画布):底图为示意渐变(调用方已随图声明),
    点位与标记全部由调用方传入 —— 演示点位(旧 POINTS)已随假图资产删除。
    opts:
      markers —— 已选真实点标记 [{x, y, color, label}](viewBox 300×170 坐标);
      note    —— 左下角小注(如真实数据的覆盖范围/网格说明)。
    首参保留(旧演示选点签名)仅为调用方兼容,不再产生任何输出。 */
export function mapSvg(_selectedId, opts = {}) {
  const uid = ++uidSeq;
  const markers = (opts.markers || []).map((k) => {
    const x = (+k.x).toFixed(1), y = (+k.y).toFixed(1);
    return `<g class="mk"><circle cx="${x}" cy="${y}" r="5.2" fill="none" stroke="#fff" stroke-width="3.4"/>`
      + `<circle cx="${x}" cy="${y}" r="5.2" fill="none" stroke="${esc(k.color || '#dc2626')}" stroke-width="2"/>`
      + (k.label ? `<text x="${(+k.x + 8).toFixed(1)}" y="${(+k.y + 4).toFixed(1)}" font-size="10" fill="#0c1e3a" font-weight="700">${esc(k.label)}</text>` : '')
      + '</g>';
  }).join('');
  const note = opts.note
    ? `<rect x="4" y="151" width="${Math.min(292, 14 + String(opts.note).length * 9)}" height="15" rx="3" fill="rgba(255,255,255,.78)"/>`
      + `<text x="9" y="162" font-size="9" fill="#0c1e3a">${esc(opts.note)}</text>`
    : '';
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
    ${markers}${note}
  </svg>`;
}

function esc(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

/* ============================================================
   空壳导出(零数据,勿再填充)—— 仅为 stream.js 死代码(W3 并行删除中)
   的 import 绑定在合并窗口内保持可解析;W3 落地后应整体删除本节。
   ============================================================ */
export const DATES = [];
export const POINTS = [];
export const IMAGES = [];

/** 已删除的演示图生成器:任何 id 一律空串(无假图可画)。 */
export function figureSvg() { return ''; }

/** 已删除的演示图节点:恒为空容器。 */
export function figureNode() {
  return h('div', { style: { display: 'contents' } });
}
