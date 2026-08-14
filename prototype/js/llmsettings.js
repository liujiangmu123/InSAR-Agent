/* LLM 模型设置面板:填密钥 → 获取模型 → 选模型(对话/识图)→ 测试 → 保存。
 * 兼管自主循环配置(LOOP-CONTRACT §8):「自主循环」开关 + 周期上限(1-48),
 * 与模型配置同走 GET/POST /api/llm/config(agent_loop / agent_max_cycles 两键)。
 *
 * 安全纪律:密钥只上行(POST /api/llm/config),永不回显全文——输入框留空
 * 表示沿用服务端已存密钥(占位符展示掩码);模型列表/测试都由服务端持钥出网。
 * 自初始化 + 幂等:顶栏注入「模型」齿轮按钮、composer 提示行注入常驻模型
 * 指示 chip,均不改 app.js / index.html。
 * 回显纪律:打开面板即拉 GET /api/llm/config,已存模型立刻渲染进下拉
 * (不必先点「获取模型列表」);保存成功后广播 CustomEvent('llm:config-changed'),
 * chip 监听事件即时刷新。dlg.dataset 只是会话内缓存,每次打开都以服务端为准。
 */

// ---------- 纯函数(模块顶层导出,供 prototype/llmsettings.check.mjs 直测) ----------

function priceLabel(m) {
  if (m.price_in == null) return "";
  const cur = m.currency === "CNY" ? "¥" : (m.currency ? m.currency + " " : "");
  return ` · ${cur}${m.price_in}/${m.price_out} 每百万`;
}

/* 周期上限输入规整:整数化并夹进 1..48 闭区间;空串/非数返回 null
 * (POST 语义与密钥留空一致:null = 服务端保留旧值)。 */
export function normCycles(raw) {
  const s = String(raw ?? "").trim();
  if (!s) return null;
  const n = Number(s);
  if (!Number.isFinite(n)) return null;
  return Math.min(48, Math.max(1, Math.trunc(n)));
}

/* 下拉渲染:空选项 + 模型清单(识图下拉只留 vision 模型),current 保持选中;
 * current 不在清单里时尾部补一项「xxx(已保存)」。打开面板的「即回显」复用
 * 同一条路径:传空清单即得到「(未选择)+ 已存模型(已保存)」;之后真正获取
 * 到完整清单再调一次,选中态无缝并入(已存模型在清单里就不再带标注)。 */
export function fillSelect(sel, models, current, visionOnly) {
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
    opt.value = current; opt.textContent = `${current}(已保存)`;
    opt.selected = true; sel.appendChild(opt);
  }
}

/* 常驻模型指示 chip 的三态文案:双模型 / 仅对话 / 未配置。
 * 返回 { text, aria, tone }:text 是可见文案,aria 是完整无障碍名,
 * tone="warn" 时 chip 走醒目告警配色(未配置对话模型即视为未配置)。 */
export function chipView(cfg) {
  const chat = (cfg && cfg.chat_model) || "";
  const vision = (cfg && cfg.vision_model) || "";
  if (!chat) {
    return { text: "⚙ 未配置模型", aria: "模型设置:尚未配置模型", tone: "warn" };
  }
  if (!vision) {
    return {
      text: `⚙ ${chat}`,
      aria: `模型设置:当前对话模型 ${chat},未启用识图`, tone: "",
    };
  }
  return {
    text: `⚙ ${chat} · 识图 ${vision}`,
    aria: `模型设置:当前对话模型 ${chat},识图模型 ${vision}`, tone: "",
  };
}

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
    <div class="llmset-row">
      <input type="checkbox" name="agent_loop" id="llmsetLoopOn" checked>
      <label for="llmsetLoopOn">自主循环(单回合内多周期自主推进)</label>
    </div>
    <label>循环周期软上限(1-48,分析任务未完成会自动再加;硬顶 48)
      <input type="number" name="agent_max_cycles" min="1" max="48" step="1" value="24">
    </label>
    <p class="llmset-hint">关闭自主循环后回合退化为单步问答;执行类操作永远只产生
      确认卡,不会被循环绕过。</p>
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

    // 当前配置回填(密钥只有掩码 → 放占位符)。已存模型打开即回显:
    // 下拉立刻启用并渲染「(未选择)+ 已存模型(已保存)」,不必先获取列表。
    try {
      const cfg = await fetchConfig();
      f("base_url").value = cfg.base_url || "";
      if (cfg.api_key_masked) {
        f("api_key").placeholder = `${cfg.api_key_masked}(留空 = 沿用)`;
      }
      // 自主循环两键回显:服务端只回合法值(缺失/损坏已回默认);
      // 旧后端不带这两键时维持 HTML 缺省(开 + 6)。
      f("agent_loop").checked = cfg.agent_loop !== false;
      if (Number.isInteger(cfg.agent_max_cycles)) {
        f("agent_max_cycles").value = String(cfg.agent_max_cycles);
      }
      dlg.dataset.chatModel = cfg.chat_model || "";
      dlg.dataset.visionModel = cfg.vision_model || "";
      if (cfg.chat_model || cfg.vision_model) {
        fillSelect(f("chat_model"), [], dlg.dataset.chatModel, false);
        fillSelect(f("vision_model"), [], dlg.dataset.visionModel, true);
        f("chat_model").disabled = f("vision_model").disabled = false;
        note(f("fetchNote"),
          "已加载保存的配置;点『获取模型列表』可查看全部可选模型与价格", "dim");
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
        // 无缝并入:回显阶段(或用户改选后)的当前选中值原样保持;
        // 下拉还没启用过(无已存配置)才回退到 dataset 缓存。
        const curChat = f("chat_model").disabled
          ? (dlg.dataset.chatModel || "") : f("chat_model").value;
        const curVision = f("vision_model").disabled
          ? (dlg.dataset.visionModel || "") : f("vision_model").value;
        fillSelect(f("chat_model"), models, curChat, false);
        fillSelect(f("vision_model"), models, curVision, true);
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
        agent_loop: !!f("agent_loop").checked,
        // 越界输入先夹进 1..12 再上行(服务端同样校验);空 = null = 保留旧值
        agent_max_cycles: normCycles(f("agent_max_cycles").value),
      };
      const view = await saveConfig(body);
      dlg.dataset.chatModel = view.chat_model || "";
      dlg.dataset.visionModel = view.vision_model || "";
      f("api_key").value = "";
      if (view.api_key_masked) {
        f("api_key").placeholder = `${view.api_key_masked}(留空 = 沿用)`;
      }
      // 以服务端回执为准回写循环控件(旧后端不回这两键则维持现状)
      if ("agent_loop" in view) f("agent_loop").checked = view.agent_loop !== false;
      if (Number.isInteger(view.agent_max_cycles)) {
        f("agent_max_cycles").value = String(view.agent_max_cycles);
      }
      // 广播最新配置视图(不含密钥全文),常驻 chip 监听后即时刷新
      document.dispatchEvent(new CustomEvent("llm:config-changed", { detail: view }));
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

  // ---------- 常驻模型指示 chip(composer 提示行左端) ----------

  function renderChip(chip, cfg) {
    const v = chipView(cfg);
    chip.textContent = v.text;             // 模型名来自服务端,textContent 天然防注入
    chip.setAttribute("aria-label", v.aria);
    chip.title = v.aria;
    if (v.tone) chip.dataset.tone = v.tone;
    else delete chip.dataset.tone;
  }

  function installChip() {
    const anchor = $(".composer .cmeta");
    if (!anchor || document.getElementById("llmModelChip")) return;
    const chip = document.createElement("button");
    chip.type = "button";
    chip.id = "llmModelChip";
    chip.className = "llm-chip";
    renderChip(chip, null);                // 先按「未配置」占位,读到配置再刷新
    chip.addEventListener("click", openDialog);
    document.addEventListener("llm:config-changed", (e) => renderChip(chip, e.detail));
    anchor.prepend(chip);
    // 初始状态取服务端;后端不可达时维持「未配置」占位,不打断页面
    fetchConfig().then((cfg) => renderChip(chip, cfg)).catch(() => {});
  }

  // ---------- 顶栏入口 ----------

  function installGear() {
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

  function install() {
    installGear();
    installChip();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", install, { once: true });
  } else {
    install();
  }
})();
