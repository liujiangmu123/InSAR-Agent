/* ============================================================
   报告面板实时数据(面板 5「报告」真实化的唯一新增模块)。

   职责:
   - 拉取 GET /api/methods.md?session=(PlainText;响应头 X-Narrate-Source:
     template = 规则模板生成,llm = LLM 润色增强),30 秒 TTL 缓存,
     手动刷新走 invalidate() —— 缓存模式对齐 envlive.js;
   - 零依赖极简 markdown 渲染:标题/段落/粗体/行内代码/列表/引用块/表格,
     〔prov-…〕可溯引用与〔ref:…〕依据锚点渲染为特殊样式;
   - 解析层(parseBlocks / tokenizeInline)是纯函数,node --test 无 DOM 直测;
     渲染层(renderMarkdown)才碰 document,全部走 createElement + textContent,
     不用 innerHTML —— LLM 润色内容按不可信数据处理。

   失败语义(无演示回落):
   - 后端不可达 / file:// 打开 / 响应异常 → resolve null(调用方渲染
     错误态带重试),绝不抛错;
   - 后端可达但会话还没有 run(404)→ resolve { noRun: true }(调用方
     渲染空态带运行引导)。
   ============================================================ */
import { S } from './state.js';
import { activeRunId } from './runswitch.js';   // run 历史切换器:选中历史 run 时透传 run_id

/** 缓存 TTL:方法草稿由账本确定派生,30s 内复用;手动刷新走 invalidate()。 */
const TTL_MS = 30_000;

let cache = { at: 0, runId: null, promise: null };

/** 手动刷新入口:清缓存,下一次 fetchReportLive() 必然重新拉取。 */
export function invalidate() {
  cache = { at: 0, runId: null, promise: null };
}

/** 拉取真实方法草稿(带 30s TTL 缓存;force=true 跳过缓存)。
    404(本会话还没有 run)→ { noRun: true }(空态);网络失败/其他
    HTTP 错误 → null(错误态)。
    runId 可选(run 历史切换器接线,runswitch.js):缺省取 activeRunId(),
    非空 → /api/methods.md 带 run_id 生成历史 run 的方法草稿;缓存按 run
    区分,null(最新)行为与接线前完全一致。 */
export function fetchReportLive({ force = false, runId = activeRunId() } = {}) {
  if (location.protocol === 'file:') return Promise.resolve(null);  // 与 backend.sse.js 同判据
  if (!force && cache.promise && cache.runId === (runId || null)
      && Date.now() - cache.at < TTL_MS) return cache.promise;

  const promise = (async () => {
    try {
      const qs = new URLSearchParams({ session: S.sessionId });
      if (runId) qs.set('run_id', runId);
      const resp = await fetch(`/api/methods.md?${qs}`);
      if (resp.status === 404) return { noRun: true };   // 会话无 run:空态(非错误)
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const markdown = await resp.text();
      return {
        markdown,
        source: resp.headers.get('X-Narrate-Source') || 'template',
        fetchedAt: Date.now(),
      };
    } catch {
      cache = { at: 0, runId: null, promise: null };  // 失败不占缓存位:下次渲染立即重试
      return null;
    }
  })();
  cache = { at: Date.now(), runId: runId || null, promise };
  return promise;
}

/** X-Narrate-Source 响应头 → 面板顶部的来源标注文案。 */
export function sourceLabel(source) {
  return source === 'llm'
    ? 'LLM 增强(数字经反幻觉护栏校验,与模板一致)'
    : '规则生成(确定性模板,无 LLM 参与)';
}

/* ============================================================
   极简 markdown 解析(纯函数,零依赖)
   支持子集与 report/methods.py 生成物一一对应:
   标题 / 段落 / 无序·有序列表(含两格缩进子项)/ 引用块 / 表格 /
   粗体 / 行内代码 / 〔prov-…〕 / 〔ref:…〕。
   ============================================================ */

/** 行内标记切分:〔prov-…〕 / 〔ref:…〕 / **粗体** / `代码` / 普通文本。 */
export function tokenizeInline(text) {
  const re = /〔prov-[^〕]*〕|〔ref:[^〕]*〕|\*\*[^*]+\*\*|`[^`]+`/g;
  const out = [];
  let last = 0;
  for (let m = re.exec(text); m !== null; m = re.exec(text)) {
    if (m.index > last) out.push({ t: 'text', s: text.slice(last, m.index) });
    const raw = m[0];
    if (raw.startsWith('〔prov-')) out.push({ t: 'prov', s: raw });
    else if (raw.startsWith('〔ref:')) out.push({ t: 'ref', s: raw });
    else if (raw.startsWith('**')) out.push({ t: 'bold', s: raw.slice(2, -2) });
    else out.push({ t: 'code', s: raw.slice(1, -1) });
    last = m.index + raw.length;
  }
  if (last < text.length) out.push({ t: 'text', s: text.slice(last) });
  return out;
}

/** 块级解析:markdown 文本 → 块数组(结构见各 push 处)。 */
export function parseBlocks(md) {
  const blocks = [];
  const lines = String(md ?? '').replace(/\r\n?/g, '\n').split('\n');
  let i = 0;

  const isTableRow = (ln) => /^\s*\|.*\|\s*$/.test(ln);
  const isTableSep = (ln) => /^\s*\|?[\s:|-]+\|?\s*$/.test(ln) && ln.includes('-');
  const splitRow = (ln) => ln.trim().replace(/^\|/, '').replace(/\|$/, '')
    .split('|').map((c) => c.trim());

  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i += 1; continue; }

    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      blocks.push({ t: 'h', level: heading[1].length, text: heading[2].trim() });
      i += 1;
      continue;
    }
    if (/^\s*>/.test(line)) {
      const parts = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) {
        parts.push(lines[i].replace(/^\s*>\s?/, ''));
        i += 1;
      }
      blocks.push({ t: 'quote', text: parts.join(' ').trim() });
      continue;
    }
    if (isTableRow(line)) {
      const head = splitRow(line);
      i += 1;
      if (i < lines.length && isTableRow(lines[i]) && isTableSep(lines[i])) i += 1;  // 分隔行
      const rows = [];
      while (i < lines.length && isTableRow(lines[i])) {
        rows.push(splitRow(lines[i]));
        i += 1;
      }
      blocks.push({ t: 'table', head, rows });
      continue;
    }
    const bullet = line.match(/^(\s*)-\s+(.*)$/);
    if (bullet) {
      const items = [];
      while (i < lines.length) {
        const m = lines[i].match(/^(\s*)-\s+(.*)$/);
        if (!m) break;
        items.push({ text: m[2].trim(), sub: m[1].length >= 2 });  // 两格缩进 = 子项
        i += 1;
      }
      blocks.push({ t: 'ul', items });
      continue;
    }
    const ordered = line.match(/^\s*\d+\.\s+(.*)$/);
    if (ordered) {
      const items = [];
      while (i < lines.length) {
        const m = lines[i].match(/^\s*\d+\.\s+(.*)$/);
        if (!m) break;
        items.push({ text: m[1].trim(), sub: false });
        i += 1;
      }
      blocks.push({ t: 'ol', items });
      continue;
    }
    // 普通段落:连续非空、非结构行合并为一段
    const parts = [line.trim()];
    i += 1;
    while (i < lines.length && lines[i].trim()
           && !/^(#{1,6})\s/.test(lines[i]) && !/^\s*>/.test(lines[i])
           && !isTableRow(lines[i]) && !/^\s*-\s+/.test(lines[i])
           && !/^\s*\d+\.\s+/.test(lines[i])) {
      parts.push(lines[i].trim());
      i += 1;
    }
    blocks.push({ t: 'p', text: parts.join(' ') });
  }
  return blocks;
}

/* ============================================================
   渲染(仅此层接触 DOM;createElement + textContent,天然免 XSS)
   ============================================================ */

/** 行内 token → DOM 节点数组。 */
function renderInline(text) {
  return tokenizeInline(text).map((tok) => {
    if (tok.t === 'text') return document.createTextNode(tok.s);
    if (tok.t === 'bold') {
      const b = document.createElement('b');
      b.textContent = tok.s;
      return b;
    }
    if (tok.t === 'code') {
      const c = document.createElement('code');
      c.className = 'mono';
      c.textContent = tok.s;
      return c;
    }
    const span = document.createElement('span');
    span.textContent = tok.s;
    if (tok.t === 'prov') {
      span.className = 'cite';                       // 可溯引用:复用 .draft .cite 样式
      span.title = '可溯引用:指向 provenance 账本对应记录';
    } else {
      span.className = 'refnote';                    // 依据锚点:弱化小字
      span.style.color = 'var(--text-3)';
      span.style.fontSize = '10.5px';
      span.title = '文献 / 依据锚点(来自阈值台账 ref 或社区参照)';
    }
    return span;
  });
}

function renderBlock(b) {
  if (b.t === 'h') {
    const el = document.createElement(b.level <= 2 ? 'h4' : 'h5');
    el.append(...renderInline(b.text));
    return el;
  }
  if (b.t === 'quote') {
    const el = document.createElement('blockquote');
    el.className = 'boundary';                       // 复用报告面板既有的边界块样式
    el.style.margin = '9px 0';
    el.append(...renderInline(b.text));
    return el;
  }
  if (b.t === 'ul' || b.t === 'ol') {
    const el = document.createElement(b.t);
    el.style.paddingLeft = '18px';
    el.style.margin = '6px 0';
    for (const item of b.items) {
      const li = document.createElement('li');
      if (item.sub) li.style.marginLeft = '14px';
      li.append(...renderInline(item.text));
      el.appendChild(li);
    }
    return el;
  }
  if (b.t === 'table') {
    const table = document.createElement('table');
    table.className = 'grid';
    const thead = document.createElement('thead');
    const htr = document.createElement('tr');
    for (const cell of b.head) {
      const th = document.createElement('th');
      th.append(...renderInline(cell));
      htr.appendChild(th);
    }
    thead.appendChild(htr);
    table.appendChild(thead);
    const tbody = document.createElement('tbody');
    for (const row of b.rows) {
      const tr = document.createElement('tr');
      for (const cell of row) {
        const td = document.createElement('td');
        td.append(...renderInline(cell));
        tr.appendChild(td);
      }
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    return table;
  }
  const p = document.createElement('p');
  p.append(...renderInline(b.text));
  return p;
}

/** markdown 文本 → DOM 容器(调用方塞进 .draft 容器获得排版样式)。
    reproLink=false 时不追加「导出复现包」链接(完整报告区块自带复现附录章,
    再挂链接就重复了);缺省 true,dock.js 的既有调用零改动。 */
export function renderMarkdown(md, { reproLink = true } = {}) {
  const root = document.createElement('div');
  for (const b of parseBlocks(md)) root.appendChild(renderBlock(b));
  if (!reproLink) return root;
  // 「导出复现包」:href 直链 GET /api/repro-bundle(zip:provenance/run.sh/
  // methods/qa/图件 + MANIFEST sha256 清单);run 非 done 时后端 409 不出包
  const a = document.createElement('a');
  a.className = 'btn btn-gho btn-sm';
  a.style.marginTop = '10px';
  a.href = `/api/repro-bundle?session=${encodeURIComponent(S.sessionId)}`;
  a.setAttribute('download', '');
  a.title = '打包下载 provenance.json / run.sh / methods.md / qa.json / 图件 + MANIFEST(sha256 清单)';
  a.textContent = '导出复现包(.zip)';
  root.appendChild(a);
  return root;
}
