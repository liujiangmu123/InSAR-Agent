/* 项目树:项目文件夹 → 其下对话。对齐 Codex / Cursor / Antigravity。
   纯函数,node 可单测。不碰 DOM。 */

export const UNGROUPED = '__none__';

/** 只接受非空字符串项目 id;点击事件对象一律丢掉。 */
export function asProjectId(v) {
  return (typeof v === 'string' && v.trim() && v !== 'null' && v !== 'undefined')
    ? v.trim() : '';
}
const LS_COLLAPSED = 'insar.project.collapsed';
const LS_CURRENT = 'insar.project.current';

/** 会话按项目归组。projects 先按 created_at 新→旧;无项目的会话进「未归组」。 */
export function buildProjectTree(sessions, projects) {
  const active = (sessions || []).filter((s) => !s.archived);
  const byPid = new Map();
  for (const s of active) {
    const pid = s.project_id || UNGROUPED;
    if (!byPid.has(pid)) byPid.set(pid, []);
    byPid.get(pid).push(s);
  }
  const blocks = [];
  for (const p of projects || []) {
    if (!p || !p.project_id || p.archived) continue;
    blocks.push({
      key: p.project_id,
      name: p.name || p.project_id,
      root: p.root || '',
      project: p,
      sessions: byPid.get(p.project_id) || [],
    });
    byPid.delete(p.project_id);
  }
  const orphans = byPid.get(UNGROUPED) || [];
  for (const [pid, items] of byPid) {
    if (pid === UNGROUPED) continue;
    orphans.push(...items);
  }
  if (orphans.length) {
    blocks.push({
      key: UNGROUPED,
      name: '未归组',
      root: '',
      project: null,
      sessions: orphans,
    });
  }
  return blocks;
}

export function inferCurrentProject(sessionId, sessions, projects) {
  const s = (sessions || []).find((x) => x.id === sessionId);
  if (s && s.project_id) return s.project_id;
  const first = (projects || []).find((p) => p && p.project_id && !p.archived);
  return first ? first.project_id : null;
}

export function loadCollapsed() {
  try {
    const raw = JSON.parse(localStorage.getItem(LS_COLLAPSED) || '{}');
    return raw && typeof raw === 'object' ? raw : {};
  } catch {
    return {};
  }
}

export function saveCollapsed(map) {
  try { localStorage.setItem(LS_COLLAPSED, JSON.stringify(map || {})); } catch { /* ignore */ }
}

export function loadCurrentProject() {
  try { return localStorage.getItem(LS_CURRENT) || null; } catch { return null; }
}

export function saveCurrentProject(id) {
  try {
    if (id) localStorage.setItem(LS_CURRENT, id);
    else localStorage.removeItem(LS_CURRENT);
  } catch { /* ignore */ }
}

export function isOpen(collapsedMap, key, { hasCurrent } = {}) {
  if (collapsedMap && Object.prototype.hasOwnProperty.call(collapsedMap, key)) {
    return !collapsedMap[key];
  }
  return hasCurrent !== false;
}
