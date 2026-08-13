/* LLM 模型设置面板:填密钥 → 获取模型 → 选模型(对话/识图)→ 测试 → 保存。
 *
 * 安全纪律:密钥只上行(POST /api/llm/config),永不回显全文——输入框留空
 * 表示沿用服务端已存密钥(占位符展示掩码);模型列表/测试都由服务端持钥出网。
 * 自初始化 + 幂等:顶栏注入「模型」齿轮按钮,不改 app.js。
 */
(() => {
  "use strict";
  if (window.__llmSettingsInstalled) return;
  window.__llmSettingsInstalled = true;

  const API = "";
  const $ = (sel, root) => (root || document).querySelector(sel);

  // ---------- 数据 ----------

  async function fetchConfig() {
    const r = await fetch(`${API}/api/llm/config`);
    if (!r.ok) throw new Error(`config ${r.status}`);
    return r.json();
  }
  async function saveConfig(body) {
    const r = await fetch(`${API}/api/llm/config`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error(`save ${r.status}`);
    return r.json();
  }
  async function fetchModels(body) {
    const r = await fetch(`${API}/api/llm/models`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    if (!r.ok) throw new Error(`models ${r.status}`);
    return r.json();
  }
  async function runTest(kind, model) {
    const r = await fetch(`${API}/api/llm/test`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind, model: model || null }),
    });
    if (!r.ok) throw new Error(`test ${r.status}`);
    return r.json();
  }

  // ---------- 视图 ----------

  function priceLabel(m) {
    if (m.price_in == null) return "";
    const cur = m.currency === "CNY" ? "¥" : (m.currency ? m.currency + " " : "");
    return ` · ${cur}${m.price_in}/${m.price_out} 每百万`;
  }

  function fillSelect(sel, models, current, visionOnly) {
    const list = visionOnly ? models.filter((m) => m.vision) : models;
    sel.innerHTML = "";
    const blank = document.createElement("option");
    blank.value = ""; blank.textContent = visionOnly ? "(不启用识图)" : "(未选择)";
    sel.appendChild(blank);
    for (const m of list) {
      const opt = document.createElement("option");
      opt.value = m.id;
      opt.textContent = `${m.id}${m.vision ? " · 识图" : ""}${priceLabel(m)}`;
      if (m.id === current) opt.selected = true;
      sel.appendChild(opt);
    }
    if (current && ![...sel.options].some((o) => o.value === current)) {
      const opt = document.createElement("option");
      opt.value = current; opt.textContent = `${current}(当前已存)`;
      opt.selected = true; sel.appendChild(opt);
    }
  }

  function note(el, text, tone) {
    el.textContent = text;
    el.dataset.tone = tone || "dim";
  }

  function buildDialog() {
    const wrap = document.createElement("div");
    wrap.className = "llmset-overlay";
    wrap.innerHTML = `
<div class="llmset" role="dialog" aria-modal="true" aria-labelledby="llmsetTitle">
  <header>
    <h3 id="llmsetTitle">模型接入设置</h3>
    <button type="button" class="llmset-x" aria-label="关闭">×</button>
  </header>
  <div class="llmset-body">
    <label>接入地址(OpenAI 兼容 /v1)
      <input type="url" name="base_url" autocomplete="off" spellcheck="false"
             placeholder="https://tokenrhythm.studio/v1">
    </label>
    <label>API 密钥
      <input type="password" name="api_key" autocomplete="off" spellcheck="false"
             placeholder="sk_…(留空 = 沿用已保存密钥)">
    </label>
    <p class="llmset-hint">密钥只保存在本机 workspace/llm.json,不进浏览器存储、
      不进版本库;界面永远只显示掩码。</p>
    <div class="llmset-row">
      <button type="button" class="btn" name="fetch">获取模型列表</button>
      <span class="llmset-note" name="fetchNote" role="status" aria-live="polite"></span>
    </div>
    <label>对话模型(规划/分诊用)
      <select name="chat_model" disabled><option value="">先获取模型列表</option></select>
    </label>
    <label>识图模型(图件识别用,自动流式调用)
      <select name="vision_model" disabled><option value="">先获取模型列表</option></select>
    </label>
    <div class="llmset-row">
      <button type="button" class="btn" name="testChat">测试对话</button>
      <button type="button" class="btn" name="testVision">测试识图</button>
      <span class="llmset-note" name="testNote" role="status" aria-live="polite"></span>
    </div>
  </div>
  <footer>
    <span class="llmset-note" name="saveNote" role="status" aria-live="polite"></span>
    <button type="button" class="btn btn-pri" name="save">保存</button>
  </footer>
</div>`;
    return wrap;
  }

  // ---------- 行为 ----------

  let lastFocus = null;

  async function openDialog() {
    if ($(".llmset-overlay")) return;
    lastFocus = document.activeElement;
    const overlay = buildDialog();
    document.body.appendChild(overlay);
    const dlg = $(".llmset", overlay);
    const f = (name) => $(`[name="${name}"]`, dlg);

    const close = () => {
      overlay.remove();
      if (lastFocus && lastFocus.focus) lastFocus.focus();
    };
    $(".llmset-x", dlg).addEventListener("click", close);
    overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
    overlay.addEventListener("keydown", (e) => {
      if (e.key === "Escape") { e.stopPropagation(); close(); }
    });

    // 当前配置回填(密钥只有掩码 → 放占位符)
    try {
      const cfg = await fetchConfig();
      f("base_url").value = cfg.base_url || "";
      if (cfg.api_key_masked) {
        f("api_key").placeholder = `${cfg.api_key_masked}(留空 = 沿用)`;
      }
      dlg.dataset.chatModel = cfg.chat_model || "";
      dlg.dataset.visionModel = cfg.vision_model || "";
      if (cfg.chat_model || cfg.vision_model) {
        note(f("saveNote"), cfg.configured ? "当前配置可用" : "配置不完整", "dim");
      }
    } catch { note(f("saveNote"), "读取配置失败(后端未启动?)", "bad"); }

    f("fetch").addEventListener("click", async () => {
      note(f("fetchNote"), "获取中…", "dim");
      f("fetch").disabled = true;
      try {
        const out = await fetchModels({
          base_url: f("base_url").value.trim() || null,
          api_key: f("api_key").value.trim() || null,
        });
        if (!out.ok) { note(f("fetchNote"), out.error || "获取失败", "bad"); return; }
        const models = out.models || [];
        fillSelect(f("chat_model"), models, dlg.dataset.chatModel, false);
        fillSelect(f("vision_model"), models, dlg.dataset.visionModel, true);
        f("chat_model").disabled = f("vision_model").disabled = false;
        const nv = models.filter((m) => m.vision).length;
        note(f("fetchNote"), `${models.length} 个模型(${nv} 个支持识图)`, "ok");
      } catch (e) {
        note(f("fetchNote"), `获取失败:${e.message}`, "bad");
      } finally { f("fetch").disabled = false; }
    });

    const doSave = async () => {
      const body = {
        base_url: f("base_url").value.trim() || null,
        api_key: f("api_key").value.trim() || null, // 空 = 服务端保留旧值
        chat_model: f("chat_model").value || dlg.dataset.chatModel || null,
        vision_model: f("vision_model").value || dlg.dataset.visionModel || null,
      };
      const view = await saveConfig(body);
      dlg.dataset.chatModel = view.chat_model || "";
      dlg.dataset.visionModel = view.vision_model || "";
      f("api_key").value = "";
      if (view.api_key_masked) {
        f("api_key").placeholder = `${view.api_key_masked}(留空 = 沿用)`;
      }
      return view;
    };

    f("save").addEventListener("click", async () => {
      note(f("saveNote"), "保存中…", "dim");
      try {
        const view = await doSave();
        note(f("saveNote"), view.configured
          ? "已保存,配置可用(新会话生效)" : "已保存,但配置不完整", view.configured ? "ok" : "warn");
      } catch (e) { note(f("saveNote"), `保存失败:${e.message}`, "bad"); }
    });

    const doTest = async (kind) => {
      const noteEl = f("testNote");
      note(noteEl, kind === "vision" ? "识图测试中(流式)…" : "对话测试中…", "dim");
      try {
        await doSave(); // 先落盘再测:测试走服务端已存配置
        const sel = f(kind === "vision" ? "vision_model" : "chat_model").value || null;
        const out = await runTest(kind, sel);
        if (out.ok) {
          note(noteEl, `✓ ${out.model} · ${out.latency_ms}ms · ${out.reply || ""}`, "ok");
        } else {
          note(noteEl, `✗ ${out.error || "测试失败"}`, "bad");
        }
      } catch (e) { note(noteEl, `✗ ${e.message}`, "bad"); }
    };
    f("testChat").addEventListener("click", () => doTest("chat"));
    f("testVision").addEventListener("click", () => doTest("vision"));

    f("base_url").focus();
  }

  // ---------- 顶栏入口 ----------

  function install() {
    const anchor = document.getElementById("status");
    if (!anchor || document.getElementById("btnLlmSettings")) return;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.id = "btnLlmSettings";
    btn.className = "btn";
    btn.title = "模型接入设置(密钥/模型选择)";
    btn.setAttribute("aria-label", "模型接入设置");
    btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"'
      + ' stroke-width="2" aria-hidden="true" style="width:14px;height:14px">'
      + '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82'
      + 'l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0'
      + ' 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0'
      + '-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82'
      + ' 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65'
      + ' 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0'
      + ' 1.82.33h.01a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0'
      + ' 1 1.51h.01a1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06'
      + 'a1.65 1.65 0 0 0-.33 1.82v.01a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4'
      + 'h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>'
      + '<span class="lbl">模型</span>';
    btn.addEventListener("click", openDialog);
    anchor.parentNode.insertBefore(btn, anchor);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", install, { once: true });
  } else {
    install();
  }
})();
