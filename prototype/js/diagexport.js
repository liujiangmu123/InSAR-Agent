/* ============================================================
   诊断包导出(自初始化,极小)。

   「环境」tab 末尾挂一块自带容器的卡片(#diagExport):点「导出诊断包」
   → POST /api/diagnostics → 进度态 → 完成给下载链接与大小;失败给
   错误与重试。容器是本模块私有 DOM:dock.js 每次 replaceChildren 重渲染
   环境面板都会把它冲掉,MutationObserver 负责把同一个节点补挂回去
   (同节点复用 → 下载链接/错误态跨重渲染存活),不依赖并行模块的 DOM。
   ============================================================ */

const fmtSize = (n) => (n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);

const box = document.createElement('div');
box.id = 'diagExport';
box.className = 'envcard';
box.style.cssText = 'margin-top:10px;padding:12px;display:grid;gap:8px';

const row = document.createElement('div');
row.style.cssText = 'display:flex;align-items:center;gap:8px';
const title = document.createElement('b');
title.textContent = '诊断包';
const btn = document.createElement('button');
btn.type = 'button';
btn.className = 'btn btn-gho btn-sm';
btn.textContent = '导出诊断包';
btn.setAttribute('aria-label', '导出诊断包(日志/环境/数据库摘要)');
row.append(title, btn);

const status = document.createElement('div');
status.className = 'sub';
status.setAttribute('role', 'status');
status.setAttribute('aria-live', 'polite');
status.textContent = '打包最近失败 run 的日志、环境探测与数据库摘要(已脱敏),用于提交问题反馈。';
box.append(row, status);

async function doExport() {
  btn.disabled = true;
  btn.textContent = '正在打包…';
  status.textContent = '正在收集日志与环境信息…';
  try {
    const resp = await fetch('/api/diagnostics', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const out = await resp.json();
    const link = document.createElement('a');
    link.href = out.download_url;
    link.download = '';
    link.textContent = `下载诊断包(${fmtSize(out.size)})`;
    status.replaceChildren('打包完成:', link);
  } catch (err) {
    status.textContent = `导出失败:${err.message || err}。请重试;若反复失败,请直接把服务端日志贴进反馈。`;
  } finally {
    btn.disabled = false;
    btn.textContent = '导出诊断包';
  }
}
btn.addEventListener('click', doExport);

/* 挂载:环境面板存在且未含本容器时补挂;replaceChildren 后由观察器补挂。 */
function ensureMounted() {
  const pane = document.getElementById('pane-env');
  if (pane && !pane.contains(box)) pane.appendChild(box);
}

if (location.protocol !== 'file:') {   // 离线打开原型时后端必不可达,不挂按钮
  ensureMounted();
  new MutationObserver(ensureMounted)
    .observe(document.body, { childList: true, subtree: true });
}
