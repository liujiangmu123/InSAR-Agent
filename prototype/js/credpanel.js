/* ============================================================
   数据下载凭证区(自初始化)—— 环境面板「数据下载凭证」卡片。

   第 1 步 asf_search/HyP3 真实下载需要 NASA Earthdata 凭证;本卡片提供
   「双方式切换(EDL token 推荐 / 账号密码)→ 保存 → 验证」闭环 + 注册外链。

   安全纪律(与 llmsettings.js 同级):凭证只上行(POST /api/credentials),
   永不回显全文 —— 输入框留空表示沿用服务端已存值(占位符展示掩码);
   验证由服务端持凭出网(POST /api/credentials/verify),凭证不留浏览器。

   挂载纪律(与 diagexport.js 同款):容器是本模块私有 DOM,dock.js 每次
   replaceChildren 重渲染环境面板都会把它冲掉,MutationObserver 把同一个
   节点补挂回去(同节点复用 → 输入中的内容/验证结果跨重渲染存活)。
   ============================================================ */

/* ---------- 纯函数(模块顶层导出,供 prototype/credpanel.check.mjs 直测) ---------- */

/* 状态徽章三态:未配置(stale 醒目)/ token 已配置 / 账号密码已配置。 */
export function credBadge(cfg) {
  if (!cfg || !cfg.configured) {
    return { text: '未配置', tone: 'stale', aria: '数据下载凭证:尚未配置' };
  }
  const label = cfg.mode === 'token' ? 'EDL token' : '账号密码';
  return {
    text: `已配置 · ${label}`, tone: 'ok',
    aria: `数据下载凭证:已配置(${label} 方式)`,
  };
}

/* 保存请求体:只带当前方式的字段(另一方式输入框里的残留不误伤已存值),
   空串 → null(服务端语义:留空 = 沿用旧值)。 */
export function saveBody(mode, fields) {
  const v = (s) => ((s || '').trim() ? s.trim() : null);
  if (mode === 'password') {
    return {
      earthdata_username: v(fields.earthdata_username),
      earthdata_password: v(fields.earthdata_password),
    };
  }
  return { edl_token: v(fields.edl_token) };
}

/* 密文输入框占位符:有掩码 = 已存值可沿用;无 = 提示填写。 */
export function maskedHint(masked, emptyHint) {
  return masked ? `${masked}(留空 = 沿用已保存值)` : emptyHint;
}

/* ---------- 以下 DOM 接线只在浏览器环境执行(node 导入只取纯函数) ---------- */
if (typeof document !== 'undefined'
    && typeof location !== 'undefined' && location.protocol !== 'file:') {
  const box = document.createElement('div');
  box.id = 'credPanel';
  box.className = 'envcard credpanel';
  // 静态模板,零插值:动态值一律经 .value/.textContent/占位符写入,天然防注入
  box.innerHTML = `
<div class="cred-head">
  <b>数据下载凭证</b>
  <span class="tag is-stale" data-role="badge" role="status" aria-live="polite">未配置</span>
</div>
<div class="sub">第 1 步 asf_search / HyP3 下载 Sentinel-1 数据需要 NASA Earthdata 登录。
  凭证只保存在本机 workspace/credentials.json(不进浏览器存储、不进版本库),
  界面永远只显示掩码。没有账号?
  <a href="https://urs.earthdata.nasa.gov/users/new" target="_blank"
     rel="noopener noreferrer">去注册 Earthdata 账号 ↗</a>
  (注册后在 Generate Token 页可生成 EDL token)</div>
<div class="seg cred-mode" role="group" aria-label="凭证方式">
  <button type="button" data-mode="token" aria-pressed="true">EDL token(推荐)</button>
  <button type="button" data-mode="password" aria-pressed="false">账号密码</button>
</div>
<label data-sec="token">EDL token
  <input type="password" name="edl_token" autocomplete="off" spellcheck="false"
         placeholder="urs.earthdata.nasa.gov → Generate Token">
</label>
<label data-sec="password" hidden>Earthdata 用户名
  <input type="text" name="earthdata_username" autocomplete="off" spellcheck="false"
         placeholder="注册邮箱对应的用户名">
</label>
<label data-sec="password" hidden>Earthdata 密码
  <input type="password" name="earthdata_password" autocomplete="off" spellcheck="false"
         placeholder="账号密码">
</label>
<div class="cred-row">
  <button type="button" class="btn btn-pri btn-sm" name="save">保存</button>
  <button type="button" class="btn btn-gho btn-sm" name="verify">保存并验证</button>
  <span class="cred-note" data-role="note" role="status" aria-live="polite"></span>
</div>`;

  const $ = (sel) => box.querySelector(sel);
  const f = (name) => $(`[name="${name}"]`);
  const noteEl = $('[data-role="note"]');
  const badgeEl = $('[data-role="badge"]');
  let mode = 'token';

  const note = (text, tone) => {
    noteEl.textContent = text;
    noteEl.dataset.tone = tone || 'dim';
  };

  const renderBadge = (cfg) => {
    const v = credBadge(cfg);
    badgeEl.textContent = v.text;
    badgeEl.className = `tag is-${v.tone === 'ok' ? 'ok' : 'stale'}`;
    badgeEl.setAttribute('aria-label', v.aria);
  };

  const setMode = (next) => {
    mode = next;
    for (const btn of box.querySelectorAll('.cred-mode button')) {
      btn.setAttribute('aria-pressed', String(btn.dataset.mode === next));
    }
    for (const sec of box.querySelectorAll('[data-sec]')) {
      sec.hidden = sec.dataset.sec !== next;
    }
  };
  for (const btn of box.querySelectorAll('.cred-mode button')) {
    btn.addEventListener('click', () => setMode(btn.dataset.mode));
  }

  /* 服务端视图 → 界面回显:用户名明文回填,密文只落占位符掩码。 */
  const applyView = (cfg) => {
    renderBadge(cfg);
    f('earthdata_username').value = cfg.earthdata_username || '';
    f('edl_token').value = '';
    f('earthdata_password').value = '';
    f('edl_token').placeholder = maskedHint(
      cfg.edl_token_masked, 'urs.earthdata.nasa.gov → Generate Token');
    f('earthdata_password').placeholder = maskedHint(
      cfg.earthdata_password_masked, '账号密码');
    if (cfg.mode === 'password') setMode('password');
    document.dispatchEvent(new CustomEvent('credentials:config-changed', { detail: cfg }));
  };

  let loaded = false;
  async function loadOnce() {
    if (loaded) return;
    loaded = true;
    try {
      const r = await fetch('/api/credentials');
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      applyView(await r.json());
    } catch {
      loaded = false;                    // 后端未启动:下次补挂时重试,不打断页面
      note('读取凭证状态失败(后端未启动?)', 'bad');
    }
  }

  async function doSave() {
    const body = saveBody(mode, {
      edl_token: f('edl_token').value,
      earthdata_username: f('earthdata_username').value,
      earthdata_password: f('earthdata_password').value,
    });
    const r = await fetch('/api/credentials', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const view = await r.json();
    applyView(view);
    return view;
  }

  f('save').addEventListener('click', async () => {
    note('保存中…', 'dim');
    try {
      const view = await doSave();
      note(view.configured ? '已保存(只落本机 credentials.json)'
                           : '已保存,但配置不完整', view.configured ? 'ok' : 'warn');
    } catch (e) { note(`保存失败:${e.message || e}`, 'bad'); }
  });

  f('verify').addEventListener('click', async () => {
    note('验证中(服务端持凭出网)…', 'dim');
    f('verify').disabled = true;
    try {
      await doSave();                    // 先落盘再验:验证走服务端已存凭证
      const r = await fetch('/api/credentials/verify', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const out = await r.json();
      if (out.ok) note(`✓ ${out.detail}(${out.latency_ms} ms)`, 'ok');
      else note(`✗ ${out.error || '验证失败'}`, 'bad');
    } catch (e) {
      note(`✗ 验证失败:${e.message || e}`, 'bad');
    } finally { f('verify').disabled = false; }
  });

  /* 挂载:环境面板存在且未含本容器时补挂;replaceChildren 后由观察器补回。 */
  function ensureMounted() {
    const pane = document.getElementById('pane-env');
    if (pane && !pane.contains(box)) {
      pane.appendChild(box);
      loadOnce();
    }
  }
  ensureMounted();
  new MutationObserver(ensureMounted)
    .observe(document.body, { childList: true, subtree: true });
}
