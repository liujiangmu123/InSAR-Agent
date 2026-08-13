/* ============================================================
   斜杠命令面板(slash.js):把「精确操作」留在键盘上,
   弥合自然语言(/api/turn)与结构化控制(/api/actions)之间的缝隙。

   输入框以 / 开头 → 输入框上方弹出候选面板:
     /run [步骤号|全部] · /pause · /resume · /kill · /reset <步骤号>
     /skip <步骤号> · /method <步骤号> <方法id>
     /param <步骤号> <参数名>=<值> · /status · /help

   - 参数级补全:方法/参数候选取 /api/registry(按会话缓存;离线或
     后端不可达时回退 state.js 的 STEP_DEFS + PARAM_SCHEMA 镜像);
     非法步骤号/方法 id/参数名在面板内即时红字提示,不发请求。
   - 提交:干预类命令映射 POST /api/actions(deliver_as=steer;
     不带 run_id —— 服务端 resolve_run 取该会话最近 run,与
     backend.sse.js syncConfig 同一约定);/run 借用 app.js 既有执行
     链路(window.__slashRun,含审批语义:显式敲命令即显式授权);
     /status、/help 纯本地渲染卡片,不发任何请求。
   - 接线:app.js 仅三行钩子(beforeKey / beforeSubmit / __slashRun,
     均注明「slash 接线」);本模块自初始化且幂等,加载失败不影响主流程。
   - 无障碍:面板 role=listbox/option,输入框挂 aria-activedescendant
     (焦点始终留在输入框的 activedescendant 模式);颜色全部取
     tokens.css 语义令牌,明暗主题 AA 由既有对比度审计覆盖。
   - 纯逻辑(normalizeSlash / parseSlash / suggest / toPayload)不碰
     DOM,node 可直接 import 自查(prototype/slash.check.mjs)。
   ============================================================ */

import { h } from './dom.js';
import * as St from './state.js';
import { PARAM_SCHEMA, S, STEP_DEFS } from './state.js';
import * as Stream from './stream.js';

/* ============================================================
   ① 命令注册表(纯数据)
   action 字段即 POST /api/actions 的动作闭集映射(core/actions.ACTIONS);
   无 action 的命令走本地渲染(/status /help)或执行链路(/run)。
   ============================================================ */
export const COMMANDS = [
  { name: 'run',    usage: '/run [步骤号|全部]',            args: ['steps'],
    summary: '执行流水线(缺省 = 全部待办步骤)' },
  { name: 'pause',  usage: '/pause',                        args: [],
    summary: '暂停:当前步骤完成后不再启动新步骤', action: 'PAUSE' },
  { name: 'resume', usage: '/resume',                       args: [],
    summary: '恢复被暂停的运行', action: 'PLAY' },
  { name: 'kill',   usage: '/kill',                         args: [],
    summary: '取消当前运行(断点保留,可续跑)', action: 'KILL' },
  { name: 'reset',  usage: '/reset <步骤号>',               args: ['step'],
    summary: '复位该步为待运行,下游沿依赖图标脏', action: 'RESET' },
  { name: 'skip',   usage: '/skip <步骤号>',                args: ['step'],
    summary: '跳过该步(产物沿用现状,责任在操作者)', action: 'SKIP' },
  { name: 'method', usage: '/method <步骤号> <方法id>',     args: ['step', 'method'],
    summary: '改该步方法,影响范围沿依赖图级联 STALE', action: 'SET_METHOD' },
  { name: 'param',  usage: '/param <步骤号> <参数名>=<值>', args: ['step', 'kv'],
    summary: '改该步参数(资源参数不触发重跑)', action: 'SET_PARAMS' },
  { name: 'status', usage: '/status',                       args: [],
    summary: '本地渲染当前步骤矩阵摘要卡(不发请求)' },
  { name: 'help',   usage: '/help',                         args: [],
    summary: '本地渲染命令清单卡(不发请求)' },
];

/* ============================================================
   ② 纯逻辑:归一化 / 解析 / 补全 / payload 映射(node 可测)
   ctx 形状(buildCtx 产出,测试可注入):
     { steps: [{ id, name,
        methods: [{ id, label, why, ok, blocked, recommend, extra, simulated }],
        params:  { 名: { default, type, min, max, hint, kind } },
        current: { method, params, state, stale, fingerprint } }] }
   ============================================================ */

/* 全角容错:数字/等号/斜杠/逗号(含顿号)/句点/负号/全角空格 → 半角 */
const FW_PUNCT = {
  '\u3000': ' ', '\uFF1D': '=', '\uFF0F': '/', '\uFF0C': ',',
  '\u3001': ',', '\uFF0E': '.', '\uFF0D': '-',
};

export function normalizeSlash(text) {
  return String(text ?? '')
    .replace(/[\uFF10-\uFF19]/g, (c) => String.fromCharCode(c.charCodeAt(0) - 0xFF10 + 0x30))
    .replace(/[\u3000\uFF1D\uFF0F\uFF0C\u3001\uFF0E\uFF0D]/g, (c) => FW_PUNCT[c])
    .replace(/^\s+/, '');
}

export function isSlashText(text) {
  return normalizeSlash(text).startsWith('/');
}

/* 步骤号解析:半角数字(全角已归一化)+ 常用中文数字(一…十一) */
const CN_NUM = {
  一: 1, 二: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9, 十: 10, 十一: 11,
};

export function stepNum(tok) {
  if (/^\d{1,2}$/.test(tok)) return Number(tok);
  return CN_NUM[tok] ?? null;
}

const stepOf = (ctx, id) => ctx.steps.find((s) => s.id === id) || null;
const stepRange = (ctx) => `1–${Math.max(...ctx.steps.map((s) => s.id))}`;
const curVal = (step, key) => step.current?.params?.[key] ?? step.params[key]?.default;

export function fmtVal(v) {
  if (Array.isArray(v)) return `[${v.join(',')}]`;
  return v === undefined || v === null ? '—' : String(v);
}

/** 值按参数 schema 定型:{ value } 或 { error }。无 schema 时做直觉推断。 */
export function coerceValue(raw, spec = {}) {
  const t = spec.type;
  if (t === 'int' || t === 'number') {
    const n = Number(raw);
    if (raw.trim() === '' || !Number.isFinite(n)) return { error: spec.hint || '必须是数字' };
    if (t === 'int' && !Number.isInteger(n)) return { error: spec.hint || '必须是整数' };
    if (spec.min !== null && spec.min !== undefined && n < spec.min) {
      return { error: spec.hint || `不能小于 ${spec.min}` };
    }
    if (spec.max !== null && spec.max !== undefined && n > spec.max) {
      return { error: spec.hint || `不能大于 ${spec.max}` };
    }
    return { value: n };
  }
  if (t === 'bool') {
    if (raw === 'true' || raw === 'false') return { value: raw === 'true' };
    return { error: spec.hint || '必须是 true / false' };
  }
  if (t === 'list') {
    const items = raw.split(',').filter((s) => s !== '')
      .map((s) => (/^-?\d+(\.\d+)?$/.test(s) ? Number(s) : s));
    if (!items.length) return { error: spec.hint || '列表不能为空(逗号分隔)' };
    return { value: items };
  }
  if (t === 'str') return { value: raw };
  // 无 schema(离线且 PARAM_SCHEMA 未覆盖):数字样转数字,true/false 转布尔,其余原样
  if (/^-?\d+(\.\d+)?$/.test(raw)) return { value: Number(raw) };
  if (raw === 'true' || raw === 'false') return { value: raw === 'true' };
  return { value: raw };
}

/**
 * 解析完整命令文本。返回:
 *   { ok:false, error }
 *   { ok:true, name, step?, method?, key?, value?, ids? }
 * ids: null=待办步骤 | 'all'=全部 | number[](显式清单)。
 */
export function parseSlash(text, ctx) {
  const norm = normalizeSlash(text);
  if (!norm.startsWith('/')) return { ok: false, error: '不是斜杠命令' };
  const toks = norm.slice(1).trim().split(/\s+/).filter(Boolean);
  if (!toks.length) return { ok: false, error: '输入命令名,如 /run、/status(/help 看全部)' };
  const cmd = COMMANDS.find((c) => c.name === toks[0].toLowerCase());
  if (!cmd) return { ok: false, error: `未知命令 /${toks[0]}(/help 查看全部命令)` };
  const rest = toks.slice(1);

  if (!cmd.args.length) {
    if (rest.length) return { ok: false, error: `/${cmd.name} 不带参数` };
    return { ok: true, name: cmd.name };
  }

  if (cmd.name === 'run') {
    if (!rest.length) return { ok: true, name: 'run', ids: null };
    if (rest.length === 1 && (rest[0] === '全部' || rest[0].toLowerCase() === 'all')) {
      return { ok: true, name: 'run', ids: 'all' };
    }
    const ids = [];
    for (const seg of rest.flatMap((t) => t.split(',')).filter(Boolean)) {
      const n = stepNum(seg);
      if (n === null || !stepOf(ctx, n)) {
        return { ok: false, error: `无效步骤号「${seg}」(可用 ${stepRange(ctx)} 或「全部」)` };
      }
      if (!ids.includes(n)) ids.push(n);
    }
    return { ok: true, name: 'run', ids: ids.sort((a, b) => a - b) };
  }

  // 其余命令第一个参数都是步骤号
  if (!rest.length) return { ok: false, error: `${cmd.usage}:缺少步骤号` };
  const n = stepNum(rest[0]);
  const step = n === null ? null : stepOf(ctx, n);
  if (!step) return { ok: false, error: `无效步骤号「${rest[0]}」(可用 ${stepRange(ctx)})` };

  if (cmd.name === 'reset' || cmd.name === 'skip') {
    if (rest.length > 1) return { ok: false, error: `/${cmd.name} 只要一个步骤号` };
    return { ok: true, name: cmd.name, step: n };
  }

  if (cmd.name === 'method') {
    if (rest.length < 2) return { ok: false, error: `${cmd.usage}:缺少方法 id` };
    if (rest.length > 2) return { ok: false, error: '/method 只要「步骤号 + 方法id」两个参数' };
    const m = step.methods.find((x) => x.id === rest[1]);
    if (!m) {
      return { ok: false, error: `第 ${n} 步没有方法「${rest[1]}」` +
        `(候选:${step.methods.map((x) => x.id).join(' / ') || '无'})` };
    }
    return { ok: true, name: 'method', step: n, method: m.id };
  }

  // /param <步骤号> <参数名>=<值>
  if (rest.length < 2) return { ok: false, error: `${cmd.usage}:缺少「参数名=值」` };
  if (rest.length > 2) return { ok: false, error: '/param 的值不能含空格(一次改一个参数)' };
  const eq = rest[1].indexOf('=');
  if (eq <= 0) return { ok: false, error: '参数写法:<参数名>=<值>,如 min_coherence=0.3' };
  const key = rest[1].slice(0, eq);
  const raw = rest[1].slice(eq + 1);
  const spec = step.params[key];
  if (!spec) {
    return { ok: false, error: `第 ${n} 步没有参数「${key}」` +
      `(可用:${Object.keys(step.params).join(' / ') || '无'})` };
  }
  if (!raw) return { ok: false, error: `${key} 缺少值(当前 ${fmtVal(curVal(step, key))})` };
  const v = coerceValue(raw, spec);
  if (v.error) return { ok: false, error: `${key}:${v.error}` };
  return { ok: true, name: 'param', step: n, key, value: v.value };
}

/** 步骤当前状态的中文短语(STALE 语义优先于 done)。 */
const STATE_ZH = {
  done: '有效', running: '运行中', stale: 'STALE', failed: '失败', pending: '待运行',
  interrupted: '已中断', orphaned: '环境中断', skipped: '已跳过',
};

function stateZh(s) {
  const st = s.current || {};
  if (st.stale && st.state === 'done') return 'STALE';
  return STATE_ZH[st.state] || '待运行';
}

/** 命令的一句话描述(就绪提示与提交确认共用)。 */
export function describe(p) {
  switch (p.name) {
    case 'run':
      if (p.ids === 'all') return '执行全部步骤(按拓扑序,指纹一致的自动跳过)';
      if (p.ids === null) return '执行全部待办步骤(失效 / 待运行 / 断点)';
      return `执行第 ${p.ids.join('、')} 步`;
    case 'pause': return '暂停运行(当前步骤完成后生效)';
    case 'resume': return '恢复运行';
    case 'kill': return '取消当前运行(断点保留,可续跑)';
    case 'reset': return `复位第 ${p.step} 步(下游标脏待重跑)`;
    case 'skip': return `跳过第 ${p.step} 步`;
    case 'method': return `第 ${p.step} 步方法 → ${p.method}`;
    case 'param': return `第 ${p.step} 步 ${p.key} = ${fmtVal(p.value)}`;
    case 'status': return '本地渲染步骤矩阵摘要(不发请求)';
    case 'help': return '本地渲染命令清单(不发请求)';
    default: return '';
  }
}

/**
 * 按当前输入给出补全候选(面板数据源;纯函数)。
 * 返回 { open, stage, items:[{id,label,detail,badge,insert}], error, ready, meta }
 *   - error 非空 → 面板红字,不发请求;
 *   - ready 非空 → 当前文本已是完整合法命令,Enter 直接发送;
 *   - insert 为补全后的完整输入文本(替换整个输入框)。
 */
export function suggest(text, ctx) {
  const norm = normalizeSlash(text);
  const closed = { open: false, stage: 'closed', items: [], error: null, ready: null, meta: null };
  if (!norm.startsWith('/')) return closed;

  const parsed = parseSlash(norm, ctx);
  const ready = parsed.ok ? { summary: describe(parsed) } : null;
  const body = norm.slice(1);
  const endsSpace = /\s$/.test(body);
  const toks = body.trim() ? body.trim().split(/\s+/) : [];
  const out = (stage, items, extra = {}) => ({
    open: true, stage, items, error: null, ready, meta: null, ...extra,
  });

  // ---- 命令名阶段 ----
  if (toks.length === 0 || (toks.length === 1 && !endsSpace)) {
    const q = (toks[0] || '').toLowerCase();
    const items = COMMANDS
      .filter((c) => c.name.startsWith(q) || (q && c.summary.includes(q)))
      .map((c) => ({
        id: `cmd-${c.name}`, label: `/${c.name}`, detail: c.summary,
        insert: `/${c.name}${c.args.length ? ' ' : ''}`,
      }));
    if (!items.length) return out('cmd', [], { error: `未知命令「/${toks[0]}」(/help 查看全部)` });
    return out('cmd', items);
  }

  const cmd = COMMANDS.find((c) => c.name === toks[0].toLowerCase());
  if (!cmd) return out('cmd', [], { error: `未知命令「/${toks[0]}」(/help 查看全部)` });

  // ---- 无参命令:就绪或报多余参数 ----
  if (!cmd.args.length) {
    if (toks.length > 1) return out('ready', [], { error: `/${cmd.name} 不带参数`, ready: null });
    return out('ready', []);
  }

  // ---- /run:支持逗号/空格多选,已敲定的步骤不再出现在候选里 ----
  if (cmd.name === 'run') {
    const boundary = /[\s,]$/.test(body);
    const segs = toks.slice(1).flatMap((t) => t.split(',')).filter(Boolean);
    const partial = boundary ? '' : (segs.pop() ?? '');
    const committed = [];
    for (const sg of segs) {
      const sn = stepNum(sg);
      if (sn !== null && !committed.includes(sn)) committed.push(sn);
    }
    const items = [];
    if (!committed.length && (!partial || '全部'.startsWith(partial)
        || 'all'.startsWith(partial.toLowerCase()))) {
      items.push({
        id: 'run-all', label: '全部',
        detail: '按拓扑序执行全部步骤(指纹一致的自动跳过)', insert: '/run 全部',
      });
    }
    for (const s of ctx.steps) {
      if (committed.includes(s.id)) continue;
      if (partial && !String(s.id).startsWith(partial) && !s.name.includes(partial)
          && stepNum(partial) !== s.id) continue;
      items.push({
        id: `run-${s.id}`, label: String(s.id), detail: `${s.name} · ${stateZh(s)}`,
        insert: `/run ${[...committed, s.id].join(',')}`,
      });
    }
    if (!items.length) {
      return out('step', [], { error: `无效步骤号「${partial}」(可用 ${stepRange(ctx)} 或「全部」)` });
    }
    return out('step', items);
  }

  // ---- 第一参数为步骤号的命令(reset/skip/method/param) ----
  const arg1 = toks[1] ?? '';
  const typing1 = toks.length === 2 && !endsSpace;
  if (toks.length === 1 || typing1) {
    const partial = typing1 ? arg1 : '';
    const items = ctx.steps
      .filter((s) => !partial || String(s.id).startsWith(partial) || s.name.includes(partial)
        || stepNum(partial) === s.id)
      .map((s) => ({
        id: `step-${s.id}`, label: String(s.id),
        detail: `${s.name} · ${stateZh(s)}`
          + (cmd.name === 'method' ? ` · 当前 ${s.current?.method ?? '—'}` : ''),
        insert: `/${cmd.name} ${s.id}${cmd.args.length > 1 ? ' ' : ''}`,
      }));
    if (!items.length) return out('step', [], { error: `无效步骤号「${partial}」(可用 ${stepRange(ctx)})` });
    return out('step', items);
  }
  const n = stepNum(arg1);
  const step = n === null ? null : stepOf(ctx, n);
  if (!step) return out('step', [], { error: `无效步骤号「${arg1}」(可用 ${stepRange(ctx)})` });

  if (cmd.name === 'reset' || cmd.name === 'skip') {
    return out('ready', [], parsed.ok ? {} : { error: parsed.error, ready: null });
  }

  // ---- /method <步骤号> <方法id>:候选来自该步注册表 ----
  if (cmd.name === 'method') {
    const arg2 = toks[2] ?? '';
    const typing2 = toks.length === 3 && !endsSpace;
    if (toks.length === 2 || typing2) {
      const partial = typing2 ? arg2 : '';
      const items = step.methods
        .filter((m) => !partial || m.id.toLowerCase().includes(partial.toLowerCase())
          || (m.label || '').includes(partial))
        .map((m) => ({
          id: `m-${m.id}`, label: m.id,
          detail: [m.recommend ? '推荐' : '', m.why || '', m.extra || '']
            .filter(Boolean).join(' · '),
          badge: m.ok === false ? `不可用${m.blocked ? `:${m.blocked}` : ''}`
            : m.id === step.current?.method ? '当前'
              : m.simulated ? '模拟' : '',
          insert: `/method ${n} ${m.id}`,
        }));
      if (!items.length) {
        return out('method', [], { error: `第 ${n} 步没有方法「${partial}」` +
          `(候选:${step.methods.map((m) => m.id).join(' / ') || '无'})` });
      }
      return out('method', items);
    }
    return out('ready', [], parsed.ok ? {} : { error: parsed.error, ready: null });
  }

  // ---- /param <步骤号> <参数名>=<值> ----
  const arg2 = toks.length >= 3 ? toks.slice(2).join(' ') : '';
  const typing2 = toks.length >= 3 && !endsSpace;
  const eq = arg2.indexOf('=');
  const names = Object.keys(step.params);
  if (toks.length === 2 || (typing2 && eq < 0)) {
    const partial = typing2 ? arg2 : '';
    if (!names.length) return out('param', [], { error: `第 ${n} 步没有可改参数` });
    const items = names
      .filter((k) => !partial || k.toLowerCase().includes(partial.toLowerCase()))
      .map((k) => ({
        id: `p-${k}`, label: k,
        detail: `当前 ${fmtVal(curVal(step, k))}`
          + (step.params[k]?.hint ? ` · ${step.params[k].hint}` : ''),
        insert: `/param ${n} ${k}=`,
      }));
    if (!items.length) {
      return out('param', [], { error: `第 ${n} 步没有参数「${partial}」(可用:${names.join(' / ')})` });
    }
    return out('param', items);
  }
  // 值阶段:meta 行给当前值/范围;bool 给 true/false 候选;非法值即时红字
  const key = arg2.slice(0, Math.max(eq, 0));
  const raw = eq < 0 ? '' : arg2.slice(eq + 1);
  const spec = step.params[key];
  if (!spec) {
    return out('value', [], { error: `第 ${n} 步没有参数「${key}」(可用:${names.join(' / ') || '无'})` });
  }
  const metaParts = [`${key} 当前 ${fmtVal(curVal(step, key))}`];
  if (spec.min !== null && spec.min !== undefined) metaParts.push(`范围 ${spec.min}–${spec.max ?? '∞'}`);
  if (spec.type) metaParts.push(spec.type);
  const meta = metaParts.join(' · ');
  const items = spec.type === 'bool'
    ? ['true', 'false'].filter((b) => b.startsWith(raw))
      .map((b) => ({ id: `v-${b}`, label: b, detail: '', insert: `/param ${n} ${key}=${b}` }))
    : [];
  if (raw) {
    const v = coerceValue(raw, spec);
    if (v.error) return out('value', items, { error: `${key}:${v.error}`, meta, ready: null });
  }
  return out('value', items, { meta });
}

/**
 * 命令 → 提交载荷。干预类命令统一 POST /api/actions:
 *   { session, scope, target, action, payload, deliver_as:'steer' }
 * 刻意不带 run_id:服务端 resolve_run 取该会话最近 run 并在入队时绑定
 * (与 backend.sse.js syncConfig 同约定);run 级动作 target 只是信息位。
 */
export function toPayload(p, session) {
  const act = (scope, target, action, payload = {}) => ({
    kind: 'actions', url: '/api/actions',
    body: { session, scope, target, action, payload, deliver_as: 'steer' },
  });
  switch (p.name) {
    case 'pause': return act('run', 'run', 'PAUSE');
    case 'resume': return act('run', 'run', 'PLAY');
    case 'kill': return act('run', 'run', 'KILL');
    case 'reset': return act('step', String(p.step), 'RESET');
    case 'skip': return act('step', String(p.step), 'SKIP');
    case 'method': return act('step', String(p.step), 'SET_METHOD', { method: p.method });
    case 'param': return act('step', String(p.step), 'SET_PARAMS', { params: { [p.key]: p.value } });
    case 'run': return { kind: 'run', ids: p.ids };
    case 'status': return { kind: 'local', card: 'status' };
    case 'help': return { kind: 'local', card: 'help' };
    default: return null;
  }
}

/* ============================================================
   ③ 补全数据源:/api/registry 按会话缓存 + STEP_DEFS 回退
   ============================================================ */
let registry = { session: null, data: null, pending: false };

function ensureRegistry() {
  if (typeof fetch !== 'function') return;
  if (typeof location !== 'undefined' && location.protocol === 'file:') return;
  if (registry.pending || (registry.data && registry.session === S.sessionId)) return;
  registry.pending = true;
  const sid = S.sessionId;
  fetch(`/api/registry?session=${encodeURIComponent(sid)}`)
    .then((r) => (r.ok ? r.json() : null))
    .then((data) => {
      registry = {
        session: sid, pending: false,
        data: Array.isArray(data) && data.length ? data : null,
      };
      if (registry.data) refresh();   // 缓存到位后刷新候选(方法可用性/参数 schema)
    })
    .catch(() => { registry.pending = false; });   // 离线:保持 STEP_DEFS 回退
}

/** 合成补全上下文:注册表 schema(或本地回退)+ S.steps 当前值。 */
export function buildCtx() {
  const base = registry.data && registry.session === S.sessionId ? registry.data : null;
  const steps = (base || STEP_DEFS).map((d) => {
    const cur = St.st_(d.id);
    const params = {};
    for (const [k, v] of Object.entries(d.params || {})) {
      // 注册表给 schema 对象;STEP_DEFS 给默认值,叠加 PARAM_SCHEMA 的 UI 侧镜像
      params[k] = base ? { ...v } : { default: v, ...(PARAM_SCHEMA[k] || {}) };
    }
    return {
      id: d.id, name: d.name,
      methods: (d.methods || []).map((m) => ({
        id: m.id, label: m.label, why: m.why || '', ok: m.ok !== false,
        blocked: m.blocked || '', recommend: !!m.recommend,
        extra: m.extra || '', simulated: !!m.simulated,
      })),
      params,
      current: cur ? {
        method: cur.method, params: cur.params, state: cur.state,
        stale: cur.stale, fingerprint: cur.fingerprint,
      } : { method: d.method, params: null, state: 'pending', stale: false, fingerprint: '' },
    };
  });
  return { steps };
}

/* ============================================================
   ④ 浮层 UI:锚定输入框上方的 listbox 面板 + 「/ 命令」徽章
      + 输入框下方的一次性确认条
   ============================================================ */
let el = null;                        // { input, box, host, pop, list, status, badge, confirm }
let items = [];                       // 当前候选(与 DOM option 一一对应)
let active = -1;
let opened = false;
let dismissed = false;                // Esc 主动关闭:同一文本不再弹面板/拦截提交
let confirmTimer = 0;

function isOpen() { return opened; }

function paint() {
  const nodes = [...el.list.children];
  nodes.forEach((node, i) => {
    node.classList.toggle('active', i === active);
    node.setAttribute('aria-selected', String(i === active));
  });
  const cur = nodes[active];
  if (cur) {
    el.input.setAttribute('aria-activedescendant', cur.getAttribute('id'));
    cur.scrollIntoView?.({ block: 'nearest' });
  } else {
    el.input.removeAttribute('aria-activedescendant');
  }
}

function move(dir) {
  if (!items.length) return;
  active = active < 0
    ? (dir > 0 ? 0 : items.length - 1)
    : (active + dir + items.length) % items.length;
  paint();
}

function render(sug) {
  if (!sug.open) { close(); return; }
  const prevId = items[active]?.id;
  items = sug.items;
  const nodes = sug.items.map((it, i) => {
    const li = h('li', {
      class: 'slash-item', id: `slash-opt-${it.id}`, role: 'option', 'aria-selected': 'false',
    },
      h('span', { class: 'val' }, it.label),
      it.badge ? h('span', { class: 'bdg' }, it.badge) : null,
      it.detail ? h('span', { class: 'why' }, it.detail) : null);
    // pointerdown 先于 blur:阻止默认行为,焦点始终留在输入框
    li.addEventListener('pointerdown', (e) => e.preventDefault());
    li.addEventListener('mousedown', (e) => e.preventDefault());
    li.addEventListener('click', () => { active = i; complete(); });
    li.addEventListener('mouseenter', () => { active = i; paint(); });
    return li;
  });
  el.list.replaceChildren(...nodes);
  el.list.hidden = !nodes.length;
  const keep = sug.items.findIndex((it) => it.id === prevId);
  active = keep >= 0 ? keep : (sug.items.length ? 0 : -1);

  const lines = [
    sug.error ? h('div', { class: 'line err', role: 'alert' }, sug.error) : null,
    !sug.error && sug.ready ? h('div', { class: 'line ok' }, `Enter 发送:${sug.ready.summary}`) : null,
    sug.meta ? h('div', { class: 'line meta' }, sug.meta) : null,
  ].filter(Boolean);
  el.status.replaceChildren(...lines);
  el.status.hidden = !lines.length;

  opened = true;
  el.pop.hidden = false;
  el.input.setAttribute('aria-expanded', 'true');
  paint();
}

function close() {
  if (!el) return;
  opened = false;
  items = [];
  active = -1;
  el.pop.hidden = true;
  el.input.setAttribute('aria-expanded', 'false');
  el.input.removeAttribute('aria-activedescendant');
}

/** 面板开合与内容全部由输入框当前文本驱动(单一事实源)。 */
function refresh() {
  if (!el) return;
  const text = el.input.value;
  if (dismissed || !isSlashText(text) || text.includes('\n')) { close(); return; }
  ensureRegistry();
  render(suggest(text, buildCtx()));
}

function onInput() {
  dismissed = false;   // 任何编辑都解除 Esc 抑制
  refresh();
}

/** 应用当前高亮候选。返回是否真的改变了输入(未变 → Enter 应转为提交)。 */
function complete() {
  const it = items[active];
  if (!it || !it.insert) return false;
  if (normalizeSlash(el.input.value) === it.insert) return false;
  el.input.value = it.insert;
  if (el.input.setSelectionRange) {
    el.input.setSelectionRange(it.insert.length, it.insert.length);
  }
  // 走标准 input 事件:app.js 的高度/发送键联动与本模块 refresh 都挂在它上面
  el.input.dispatchEvent(new Event('input', { bubbles: true }));
  el.input.focus();
  return true;
}

/* ---------------- app.js 钩子(window.__slash) ---------------- */

/** 输入框 keydown 拦截:返回 true 表示已消费,app.js 不再处理。 */
function beforeKey(e) {
  if (!el || !opened || e.isComposing) return false;
  switch (e.key) {
    case 'ArrowDown': move(1); e.preventDefault(); return true;
    case 'ArrowUp': move(-1); e.preventDefault(); return true;
    case 'Tab': complete(); e.preventDefault(); return true;
    case 'Escape':
      dismissed = true;
      close();
      e.preventDefault();
      e.stopPropagation();   // 不触达全局 Esc(运行中语义是「停止」)
      return true;
    case 'Enter':
      if (e.shiftKey) return false;                       // 换行维持原语义
      if (complete()) { e.preventDefault(); return true; } // 补全优先
      return false;   // 已是完整输入 → 放行给 submit(),由 beforeSubmit 接管
    default: return false;
  }
}

/** submit 拦截:斜杠命令不进对话流。返回 true 表示已接管。 */
function beforeSubmit(text) {
  if (!el || !isSlashText(text)) return false;
  if (dismissed) { dismissed = false; return false; }   // Esc 后按原文发送(一次性)
  execute(text);
  return true;
}

/* ---------------- 提交:POST /api/actions / 执行链路 / 本地卡片 ---------------- */

function showConfirm(text, tone = 'info') {
  if (!el) return;
  const c = el.confirm;
  c.className = `slash-confirm is-${tone}`;
  c.textContent = text;
  c.hidden = false;
  clearTimeout(confirmTimer);
  confirmTimer = setTimeout(() => { c.hidden = true; }, 5600);
}

async function postAction(payload) {
  if (typeof location !== 'undefined' && location.protocol === 'file:') return { demo: true };
  try {
    const resp = await fetch(payload.url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload.body),
    });
    // 静态文件服务器(404/405/501)= 无后端,与 backend.sse.js 同口径 → 本地演示
    if ([404, 405, 501].includes(resp.status)) return { demo: true };
    if (!resp.ok) {
      let detail = `HTTP ${resp.status}`;
      try {
        const d = await resp.json();
        if (d?.detail) detail = typeof d.detail === 'string' ? d.detail : JSON.stringify(d.detail);
      } catch { /* 无 JSON 详情则保留状态码 */ }
      return { error: detail };
    }
    return { ok: true };
  } catch {
    return { demo: true };   // 网络不可达 → 本地演示语义
  }
}

/** 服务端受理后的本地镜像:与 dock 面板改方法/参数走同一套 state.js 级联。 */
function mirrorLocally(p) {
  try {
    if (p.name === 'method') St.setMethod(p.step, p.method);
    else if (p.name === 'param') St.setParams(p.step, { [p.key]: p.value });
    else if (p.name === 'reset') {
      St.invalidate(p.step);                                  // 下游 done → stale
      St.setStepState(p.step, 'pending', { stale: false });   // 自身复位待运行
    } else if (p.name === 'skip') {
      St.setStepState(p.step, 'done', { stale: false });      // 服务端 skipped 镜像为 done
    } else if (p.name === 'kill' && S.busy) {
      // 本地也走停止按钮:取消在途 fetch 流 + 标记 interrupted(与 KILL 语义一致)
      document.getElementById('btnStop')?.click();
    }
  } catch { /* 本地镜像失败不影响已入队动作,下次 /api/state 同步会对齐 */ }
}

function doRun(p, ctx) {
  if (S.busy) {
    showConfirm('Agent 正在运行:/run 需等当前回合结束(可先 /pause 或 /kill)', 'warn');
    return;
  }
  const ids = p.ids === 'all' ? ctx.steps.map((s) => s.id)
    : p.ids === null ? St.workSummary().all
      : p.ids;
  if (!ids.length) { showConfirm('全部步骤指纹一致,无需运行', 'ok'); return; }
  if (typeof window.__slashRun !== 'function') {
    showConfirm('/run 执行入口未接线(app.js)', 'bad');
    return;
  }
  window.__slashRun(ids);
  showConfirm(`已开始执行:第 ${ids.join('、')} 步`, 'ok');
}

async function execute(text) {
  const ctx = buildCtx();
  const parsed = parseSlash(text, ctx);
  if (!parsed.ok) {
    // 非法命令:面板红字提示,不发请求、不清空输入、不进对话流
    render({ ...suggest(text, ctx), open: true, error: parsed.error, ready: null });
    return;
  }
  const payload = toPayload(parsed, S.sessionId);
  el.input.value = '';
  el.input.style.height = 'auto';
  el.input.dispatchEvent(new Event('input', { bubbles: true }));
  close();

  if (payload.kind === 'local') { renderLocalCard(payload.card); return; }
  if (payload.kind === 'run') { doRun(parsed, ctx); return; }

  showConfirm(`正在提交:${describe(parsed)}…`);
  const res = await postAction(payload);
  if (res.error) { showConfirm(`后端拒绝:${res.error}`, 'bad'); return; }
  mirrorLocally(parsed);
  const how = res.demo ? '(演示模式:后端不可达,仅本地生效)'
    : parsed.name === 'kill' ? '(KILL 即时消费)'
      : '(steer:当前步骤结束后生效)';
  showConfirm(`已入队:${describe(parsed)}${how}`, 'ok');
}

/* ---------------- /status、/help 本地卡片 ---------------- */

function statusCard(ctx) {
  const sum = St.workSummary();
  return h('div', { class: 'slash-card' },
    h('div', { class: 'slash-card-hd' },
      h('b', null, '步骤矩阵'),
      h('span', { class: 'sub' },
        `会话 ${S.sessionId} · 失效 ${sum.stale.length} · 待运行 ${sum.pending.length}`
        + ` · 断点 ${sum.resume.length}`)),
    h('div', { class: 'slash-matrix', role: 'table', 'aria-label': '步骤矩阵摘要' },
      ...ctx.steps.map((s) => {
        const cls = s.current?.stale && s.current?.state === 'done'
          ? 'stale' : (s.current?.state || 'pending');
        return h('div', { class: `row st-${cls}`, role: 'row' },
          h('span', { class: 'n', role: 'cell' }, String(s.id).padStart(2, '0')),
          h('span', { class: 'nm', role: 'cell' }, s.name),
          h('code', { class: 'mth', role: 'cell' }, s.current?.method || '—'),
          h('span', { class: 'st', role: 'cell' }, stateZh(s)),
          h('code', { class: 'fp', role: 'cell' }, s.current?.fingerprint || '········'));
      })));
}

function helpCard() {
  return h('div', { class: 'slash-card' },
    h('div', { class: 'slash-card-hd' },
      h('b', null, '斜杠命令'),
      h('span', { class: 'sub' }, '↑↓ 选择 · Tab/Enter 补全 · Enter 发送 · Esc 关闭')),
    h('div', { class: 'slash-cmds' }, ...COMMANDS.map((c) => h('div', { class: 'row' },
      h('code', { class: 'us' }, c.usage),
      h('span', { class: 'ds' }, c.summary)))),
    h('div', { class: 'slash-card-ft' },
      '干预类命令经 POST /api/actions 入队(deliver_as=steer,当前步骤结束后生效);',
      '/run 走既有执行链路;/status、/help 纯本地。'));
}

function renderLocalCard(card) {
  try {
    Stream.agentMsg(card === 'status' ? statusCard(buildCtx()) : helpCard());
  } catch {
    showConfirm('轨迹流尚未就绪,无法渲染卡片', 'bad');
  }
}

/* ============================================================
   ⑤ 自初始化(幂等):找输入框、装面板/徽章/确认条、挂 window.__slash
   ============================================================ */
export function initSlash() {
  if (typeof document === 'undefined' || typeof window === 'undefined') return false;
  if (window.__slash) return true;   // 幂等:重复加载/重复调用不重复装配
  const input = document.getElementById('prompt');
  if (!input) return false;
  const box = input.parentNode;                     // index.html:.composer-in > .box > textarea
  const host = box?.parentNode || box;
  if (!box || !host) return false;

  const list = h('ul', {
    class: 'slash-list', id: 'slashList', role: 'listbox', 'aria-label': '斜杠命令候选',
  });
  const status = h('div', { class: 'slash-status' });
  const pop = h('div', { class: 'slash-pop', id: 'slashPop' },
    list, status,
    h('div', { class: 'slash-ft' }, '↑↓ 选择 · Tab 补全 · Enter 补全/发送 · Esc 关闭'));
  pop.hidden = true;
  host.appendChild(pop);

  // 一次性确认条:紧贴输入框正下方(.box 之后);常驻 live region,SR 可播报
  const confirm = h('div', { class: 'slash-confirm', role: 'status', 'aria-live': 'polite' });
  confirm.hidden = true;
  host.insertBefore(confirm, box.nextSibling);

  // 「/ 命令」徽章:输入框右下角,点击等效输入 /
  const badge = h('button', {
    class: 'slash-hint', id: 'slashHint', type: 'button',
    'aria-label': '斜杠命令:输入 / 打开命令面板', title: '斜杠命令(/)',
  }, '/ 命令');
  badge.addEventListener('click', () => {
    if (!isSlashText(input.value)) input.value = `/${input.value}`;
    dismissed = false;
    input.focus();
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
  const attach = document.getElementById('btnAttach');
  if (attach && attach.parentNode === box) box.insertBefore(badge, attach);
  else box.appendChild(badge);

  input.setAttribute('aria-controls', 'slashList');
  input.setAttribute('aria-haspopup', 'listbox');
  input.setAttribute('aria-expanded', 'false');

  el = { input, box, host, pop, list, status, badge, confirm };
  input.addEventListener('input', onInput);
  input.addEventListener('blur', () => {
    // 候选点击用 pointerdown 阻止了焦点转移;真离开输入框才收面板
    setTimeout(() => { if (document.activeElement !== input) close(); }, 120);
  });

  window.__slash = { beforeKey, beforeSubmit };
  return true;
}

// 浏览器环境自初始化;脚本在 body 尾部加载时 #prompt 已存在,否则等 DOMContentLoaded
if (typeof document !== 'undefined' && typeof window !== 'undefined') {
  if (!initSlash() && document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => initSlash(), { once: true });
  }
}
