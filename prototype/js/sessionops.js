/* ============================================================
   会话操作(重命名/归档/还原)的纯逻辑层 —— app.js 会话侧栏的数据操作。
   零 DOM、零网络:名称校验 + 列表变换 + 撤销窗口,Node 里可直接单测
   (tests/js/sessionops.test.mjs)。
   服务端对应 api/app.py 的 PATCH/DELETE /api/sessions/{id};
   validateSessionName 与其 check_session_name 保持同一口径
   (非空、≤80 字、无控制字符),本地先挡一道,后端裁决为准。
   ============================================================ */

export const NAME_MAX = 80;

/**
 * 会话显示名校验(check_session_name 的前端镜像)。
 * @param {*} raw 用户输入(任意类型,内部转字符串)
 * @returns {{ok:true, name:string} | {ok:false, error:string}} name 为去首尾空白后的值
 */
export function validateSessionName(raw) {
  const name = String(raw ?? '').trim();
  if (!name) return { ok: false, error: '名称不能为空' };
  if (name.length > NAME_MAX) {
    return { ok: false, error: `名称过长(去首尾空白后 ≤${NAME_MAX} 字)` };
  }
  for (const ch of name) {
    const cp = ch.codePointAt(0);
    if (cp < 0x20 || cp === 0x7f) return { ok: false, error: '名称不允许控制字符' };
  }
  return { ok: true, name };
}

/**
 * 按归档态切分会话列表(不改原数组;条目引用共享)。
 * @param {Array<{archived?:number}>} sessions
 * @returns {{active:Array, archived:Array}}
 */
export function splitArchived(sessions) {
  const active = [];
  const archived = [];
  for (const s of sessions || []) (s && s.archived ? archived : active).push(s);
  return { active, archived };
}

function find(sessions, id) {
  return (sessions || []).find((s) => s && s.id === id) || null;
}

/** 就地改名。找不到该 id 返回 false。 */
export function applyRename(sessions, id, name) {
  const s = find(sessions, id);
  if (!s) return false;
  s.name = name;
  return true;
}

/** 就地归档(落时间戳,与服务端 archived 列同语义)。找不到返回 false。 */
export function applyArchive(sessions, id, at = Date.now()) {
  const s = find(sessions, id);
  if (!s) return false;
  s.archived = at;
  return true;
}

/** 就地还原(清归档标记)。找不到或本就未归档返回 false。 */
export function applyRestore(sessions, id) {
  const s = find(sessions, id);
  if (!s || !s.archived) return false;
  delete s.archived;
  return true;
}

/**
 * 撤销窗口:归档后 timeoutMs 内可撤销,过期自动关窗。
 * 计时器可注入(单测传假实现);同 id 重复开窗会先关旧窗(不叠加计时器)。
 * @param {{timeoutMs?:number, setTimeoutFn?:Function, clearTimeoutFn?:Function}} [opts]
 * @returns {{start(id:string, onExpire?:Function):void, cancel(id:string):boolean, has(id:string):boolean}}
 *   cancel:窗口内撤销返回 true 并关窗;已过期/未开窗返回 false。
 */
export function createUndoWindow(opts = {}) {
  const timeoutMs = opts.timeoutMs ?? 5000;
  const setTimeoutFn = opts.setTimeoutFn || ((fn, ms) => setTimeout(fn, ms));
  const clearTimeoutFn = opts.clearTimeoutFn || ((t) => clearTimeout(t));
  const pending = new Map();
  return {
    start(id, onExpire) {
      if (pending.has(id)) clearTimeoutFn(pending.get(id));
      pending.set(id, setTimeoutFn(() => {
        pending.delete(id);
        if (onExpire) onExpire(id);
      }, timeoutMs));
    },
    cancel(id) {
      if (!pending.has(id)) return false;
      clearTimeoutFn(pending.get(id));
      pending.delete(id);
      return true;
    },
    has(id) {
      return pending.has(id);
    },
  };
}
