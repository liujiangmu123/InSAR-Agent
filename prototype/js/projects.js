/* 项目文件夹:新建项目 = 选定/创建目录,会话与数据都钉在该目录。 */
import { h, $, toast } from './dom.js';
import { S, setSessions, setProjects } from './state.js';
import { pickDirectory, isDesktop } from './desktop.js';

function dlg() {
  let el = document.getElementById('projDlg');
  if (el) return el;
  el = h('div', { id: 'projDlg', class: 'proj-dlg', hidden: true, role: 'dialog',
    'aria-labelledby': 'projDlgTitle' },
    h('div', { class: 'proj-card', id: 'projCard' },
      h('h2', { id: 'projDlgTitle' }, '新建项目'),
      h('p', null, '选一个文件夹作为工作区。之后对话、读取、写出都在这个目录里。把数据放进去即可。也可把文件夹拖到这个对话框上。'),
      h('label', { for: 'projName' }, '项目名称'),
      h('input', { id: 'projName', type: 'text', maxlength: 80, placeholder: '例如 玉树滑坡 2024' }),
      h('label', { for: 'projRoot' }, '文件夹路径'),
      h('div', { class: 'proj-path' },
        h('input', { id: 'projRoot', type: 'text', placeholder: '点「浏览」选文件夹,或粘贴绝对路径' }),
        h('button', { id: 'projPick', type: 'button' }, '浏览…')),
      h('p', { id: 'projHint', class: 'proj-hint' }, ''),
      h('div', { class: 'proj-actions' },
        h('button', { id: 'projCancel', type: 'button' }, '取消'),
        h('button', { id: 'projOk', type: 'button', class: 'is-primary' }, '创建并打开'))));
  document.body.appendChild(el);
  return el;
}

function closeDlg() {
  const el = document.getElementById('projDlg');
  if (!el) return;
  el.hidden = true;
  el.classList.remove('is-open');
  el.setAttribute('hidden', '');
}

function hideScrimIfIdle() {
  const scrim = document.getElementById('scrim');
  if (scrim && !scrim.hidden) {
    // 窄屏抽屉遮罩会压住对话框;打开新建项目时先收起
    scrim.hidden = true;
  }
}

function setHint(text) {
  const n = document.getElementById('projHint');
  if (n) n.textContent = text || '';
}

function fillPath(path) {
  const input = document.getElementById('projRoot');
  if (input) {
    input.value = path;
    input.dispatchEvent(new Event('input', { bubbles: true }));
  }
  const name = document.getElementById('projName');
  if (name && !name.value.trim()) {
    const base = String(path).replace(/[\\/]+$/, '').split(/[\\/]/).pop();
    if (base) name.value = base;
  }
  setHint(`已选:${path}`);
}

async function pickViaBackend() {
  const resp = await fetch('/api/projects/pick-folder', { method: 'POST' });
  if (!resp.ok) throw new Error(await resp.text());
  return resp.json();
}

async function pickProjectFolder() {
  if (isDesktop()) {
    try {
      const picked = await pickDirectory('选择项目文件夹');
      if (picked) return picked;
      return null; // 用户取消
    } catch (err) {
      console.warn('[projects] Tauri 选目录失败,改用备用选择器', err);
      toast('系统对话框调用失败,改用备用选择器', 2800);
    }
  }
  const body = await pickViaBackend();
  if (body && body.ok && body.path) return body.path;
  return null;
}

export function openProjectDialog() {
  hideScrimIfIdle();
  const el = dlg();
  el.hidden = false;
  el.classList.add('is-open');
  el.removeAttribute('hidden');
  el.style.zIndex = '480';
  setHint('');
  const name = $('#projName');
  if (name) { name.value = ''; name.focus(); }
  const root = $('#projRoot');
  if (root) root.value = '';
  bindDrop();
  return el;
}

async function openDlg() {
  openProjectDialog();
}

if (typeof window !== 'undefined') {
  window.__openProjectDialog = openProjectDialog;
}

async function createAndOpen() {
  const name = ($('#projName')?.value || '').trim();
  const root = ($('#projRoot')?.value || '').trim();
  if (!name) { toast('请填写项目名称', 2800); return; }
  if (!root) { toast('请先点「浏览」选择文件夹,或粘贴绝对路径', 3200); return; }
  const okBtn = document.getElementById('projOk');
  if (okBtn) okBtn.disabled = true;
  let proj;
  try {
    const resp = await fetch('/api/projects', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, root }),
    });
    if (!resp.ok) {
      const t = await resp.text();
      toast(`创建项目失败:${t.slice(0, 160)}`, 4200);
      return;
    }
    proj = await resp.json();
  } catch {
    toast('后端不可达,无法新建项目', 4200);
    return;
  } finally {
    if (okBtn) okBtn.disabled = false;
  }
  const now = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  const stamp = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}`
    + `-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
  const id = `s-${stamp}-${Math.random().toString(36).slice(2, 6)}`;
  let row;
  try {
    const resp = await fetch('/api/sessions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        id, name: proj.name || name, mode: S.mode,
        project_id: proj.project_id,
      }),
    });
    if (!resp.ok) {
      toast('项目已建,但会话创建失败', 4200);
      return;
    }
    row = await resp.json();
  } catch {
    toast('项目已建,但会话创建失败', 4200);
    return;
  }
  closeDlg();
  try {
    const list = await fetch('/api/sessions?include_archived=1');
    if (list.ok) setSessions(await list.json());
    else setSessions([row]);
  } catch {
    setSessions([row]);
  }
  S.sessionId = row.session_id || row.id;
  S.projectId = proj.project_id;
  try {
    const plist = await fetch('/api/projects');
    if (plist.ok) setProjects(await plist.json());
    else setProjects([proj]);
  } catch {
    setProjects([proj]);
  }
  document.dispatchEvent(new CustomEvent('insar:project-opened', { detail: { ...row, project_id: proj.project_id } }));
  const n = Number(proj.file_count);
  const extra = Number.isFinite(n) ? ` · 已看到 ${n} 个文件` : '';
  toast(`已打开项目:${proj.name} (${proj.root})${extra}`, 4200);
}

function bindDrop(el) {
  const card = document.getElementById('projCard');
  if (!card || card.dataset.dropBound) return;
  card.dataset.dropBound = '1';
  const on = (e) => { e.preventDefault(); e.stopPropagation(); };
  card.addEventListener('dragover', (e) => { on(e); card.classList.add('is-drop'); });
  card.addEventListener('dragleave', () => card.classList.remove('is-drop'));
  card.addEventListener('drop', (e) => {
    on(e);
    card.classList.remove('is-drop');
    const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    const path = f && (f.path || f.webkitRelativePath);
    if (f && f.path) {
      fillPath(f.path);
      return;
    }
    toast('请点「浏览」选择文件夹(拖入需桌面壳提供绝对路径)', 3200);
  });
}

function bind() {
  const btn = document.getElementById('btnNewProject');
  if (!btn) return;
  dlg();
  bindDrop();
  btn.addEventListener('click', openDlg);
  document.getElementById('projCancel')?.addEventListener('click', closeDlg);
  document.getElementById('projOk')?.addEventListener('click', createAndOpen);
  document.getElementById('projPick')?.addEventListener('click', async () => {
    const pickBtn = document.getElementById('projPick');
    if (pickBtn) pickBtn.disabled = true;
    setHint('正在打开文件夹对话框…若看不见请按 Alt+Tab');
    const nudge = setTimeout(() => {
      toast('系统文件夹窗口已弹出。若看不见，请按 Alt+Tab 切到「选择项目文件夹」', 5200);
    }, 800);
    try {
      const picked = await pickProjectFolder();
      if (picked) fillPath(picked);
      else setHint('未选中文件夹。也可把绝对路径粘贴到输入框。');
    } catch (err) {
      setHint('');
      toast(`无法打开文件夹对话框:${String(err.message || err).slice(0, 160)}`, 4200);
    } finally {
      clearTimeout(nudge);
      if (pickBtn) pickBtn.disabled = false;
    }
  });
  const overlay = document.getElementById('projDlg');
  if (overlay && !overlay.dataset.chromeBound) {
    overlay.dataset.chromeBound = '1';
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) closeDlg();
    });
    document.addEventListener('keydown', (e) => {
      if (e.key !== 'Escape') return;
      const dlgEl = document.getElementById('projDlg');
      if (dlgEl && dlgEl.classList.contains('is-open')) {
        e.preventDefault();
        closeDlg();
      }
    });
  }
}

if (typeof document !== 'undefined') {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bind);
  } else {
    bind();
  }
}
