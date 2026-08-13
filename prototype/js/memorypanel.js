/* 记忆面板:跨会话记忆的查看/搜索/手动添加/删除(数据源 /api/memory*)。
 *
 * 自初始化 + 幂等:侧栏底部(.sider-ft)注入「记忆」入口按钮,点击开浮层面板;
 * 不改 app.js,index.html 只加两行引用。后端不可达时面板给出提示,不打断页面。
 * 记忆行渲染一律 textContent(内容来自用户/萃取文本,防注入);
 * 删除是归档软删(DELETE /api/memory/{id}),后端保留数据。
 * 聊天流中 converse 使用了记忆的展示归对话二期代理,本面板只管管理面。
 */

// ---------- 纯函数(模块顶层导出,供 prototype/memorypanel.check.mjs 直测) ----------

/* 类型徽章:kind 闭集 → 中文标签 + 配色类;未知 kind 原样展示不炸 */
export const KIND_META = {
  preference: { label: "偏好", cls: "pref" },
  fact: { label: "事实", cls: "fact" },
  outcome: { label: "结论", cls: "outcome" },
};

export function kindView(kind) {
  return KIND_META[kind] || { label: String(kind || "?"), cls: "other" };
}

export function sourceLabel(source) {
  return source === "auto" ? "自动萃取" : "手动";
}

/* 秒级时间戳 → 本地「YYYY-MM-DD HH:mm」;非法值给空串(徽章行少一段,不炸) */
export function timeLabel(ts) {
  if (!Number.isFinite(ts)) return "";
  const d = new Date(ts * 1000);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
    + ` ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/* 记忆行的元信息文案:来源 · 权重(>1 才显示,去重加权的可见反馈)· 时间 */
export function metaLabel(m) {
  const parts = [sourceLabel(m.source)];
  if (Number.isFinite(m.weight) && m.weight > 1) parts.push(`权重 ${m.weight}`);
  const t = timeLabel(m.ts);
  if (t) parts.push(t);
  return parts.join(" · ");
}

(() => {
  "use strict";
  if (typeof window === "undefined") return;
  if (window.__memoryPanelInstalled) return;
  window.__memoryPanelInstalled = true;

  const API = "";
  const $ = (sel, root) => (root || document).querySelector(sel);

  // ---------- 数据 ----------

  async function fetchList(q) {
    const url = q ? `${API}/api/memory?q=${encodeURIComponent(q)}` : `${API}/api/memory`;
    const r = await fetch(url);
    if (!r.ok) throw new Error(`memory ${r.status}`);
    return (await r.json()).items || [];
  }
  async function addMemory(kind, content) {
    const r = await fetch(`${API}/api/memory`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind, content }),
    });
    if (!r.ok) throw new Error(`add ${r.status}`);
    return r.json();
  }
  async function removeMemory(id) {
    const r = await fetch(`${API}/api/memory/${id}`, { method: "DELETE" });
    if (!r.ok) throw new Error(`delete ${r.status}`);
    return r.json();
  }

  // ---------- 视图 ----------

  function note(el, text, tone) {
    el.textContent = text;
    el.dataset.tone = tone || "dim";
  }

  function buildPanel() {
    const wrap = document.createElement("div");
    wrap.className = "memp-overlay";
    wrap.innerHTML = `
<div class="memp" role="dialog" aria-modal="true" aria-labelledby="mempTitle">
  <header>
    <h3 id="mempTitle">记忆(跨会话)</h3>
    <button type="button" class="memp-x" aria-label="关闭">×</button>
  </header>
  <div class="memp-body">
    <div class="memp-add">
      <select name="kind" aria-label="记忆类型">
        <option value="preference">偏好</option>
        <option value="fact">事实</option>
        <option value="outcome">结论</option>
      </select>
      <input type="text" name="content" autocomplete="off"
             placeholder="记住:我常用玉树区域" aria-label="记忆内容">
      <button type="button" class="btn" name="add">添加</button>
    </div>
    <div class="memp-search">
      <input type="search" name="q" autocomplete="off"
             placeholder="搜索记忆内容…" aria-label="搜索记忆">
    </div>
    <p class="memp-hint">这里的记忆会按权重带进后续对话;删除是归档,不清数据。</p>
    <span class="memp-note" name="note" role="status" aria-live="polite"></span>
    <ul class="memp-list" name="list" aria-label="记忆列表"></ul>
  </div>
</div>`;
    return wrap;
  }

  /* 单条记忆 → <li>(全部 textContent,内容绝不当 HTML 解析) */
  function renderItem(m, onDelete) {
    const li = document.createElement("li");
    li.className = "memp-item";
    const kv = kindView(m.kind);
    const badge = document.createElement("span");
    badge.className = `memp-badge is-${kv.cls}`;
    badge.textContent = kv.label;
    const main = document.createElement("div");
    main.className = "memp-main";
    const content = document.createElement("div");
    content.className = "memp-content";
    content.textContent = m.content;
    const meta = document.createElement("div");
    meta.className = "memp-meta";
    meta.textContent = metaLabel(m);
    main.appendChild(content);
    main.appendChild(meta);
    const del = document.createElement("button");
    del.type = "button";
    del.className = "memp-del";
    del.setAttribute("aria-label", `删除记忆:${m.content}`);
    del.title = "删除(归档)";
    del.textContent = "×";
    del.addEventListener("click", () => onDelete(m.id));
    li.appendChild(badge);
    li.appendChild(main);
    li.appendChild(del);
    return li;
  }

  // ---------- 行为 ----------

  let lastFocus = null;

  async function openPanel() {
    if ($(".memp-overlay")) return;
    lastFocus = document.activeElement;
    const overlay = buildPanel();
    document.body.appendChild(overlay);
    const dlg = $(".memp", overlay);
    const f = (name) => $(`[name="${name}"]`, dlg);

    const close = () => {
      overlay.remove();
      if (lastFocus && lastFocus.focus) lastFocus.focus();
    };
    $(".memp-x", dlg).addEventListener("click", close);
    overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
    overlay.addEventListener("keydown", (e) => {
      if (e.key === "Escape") { e.stopPropagation(); close(); }
    });

    const onDelete = async (id) => {
      try {
        await removeMemory(id);
        await refresh();
      } catch (e) { note(f("note"), `删除失败:${e.message}`, "bad"); }
    };

    async function refresh() {
      const q = f("q").value.trim();
      let items;
      try {
        items = await fetchList(q);
      } catch (e) {
        note(f("note"), `读取失败:${e.message}(后端未启动?)`, "bad");
        return;
      }
      const list = f("list");
      list.replaceChildren(...items.map((m) => renderItem(m, onDelete)));
      if (items.length) {
        note(f("note"), `${items.length} 条记忆${q ? "(已过滤)" : ""}`, "dim");
      } else {
        note(f("note"), q ? "没有匹配的记忆" : "还没有记忆:可手动添加,或在任务完成后萃取结论", "dim");
      }
    }

    f("q").addEventListener("input", refresh);

    f("add").addEventListener("click", async () => {
      const content = f("content").value.trim();
      if (!content) { note(f("note"), "请先填写记忆内容", "warn"); return; }
      f("add").disabled = true;
      try {
        await addMemory(f("kind").value, content);
        f("content").value = "";
        note(f("note"), "已记住", "ok");
        await refresh();
      } catch (e) {
        note(f("note"), `添加失败:${e.message}`, "bad");
      } finally { f("add").disabled = false; }
    });
    f("content").addEventListener("keydown", (e) => {
      if (e.key === "Enter") f("add").click();
    });

    await refresh();
    f("content").focus();
  }

  // ---------- 侧栏入口(自初始化,幂等) ----------

  function install() {
    const ft = $(".sider-ft");
    if (!ft || document.getElementById("btnMemory")) return;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.id = "btnMemory";
    btn.className = "memp-entry";
    btn.title = "记忆:助手记住的偏好/事实/结论(跨会话生效)";
    btn.setAttribute("aria-label", "记忆管理");
    btn.setAttribute("aria-haspopup", "dialog");
    btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"'
      + ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round"'
      + ' aria-hidden="true"><path d="M12 3a5 5 0 0 0-5 5c0 1.5.6 2.8 1.5 3.8'
      + 'L8 18h8l-.5-6.2c.9-1 1.5-2.3 1.5-3.8a5 5 0 0 0-5-5z"/>'
      + '<path d="M9 21h6"/></svg>'
      + '<span class="lbl">记忆</span>';
    btn.addEventListener("click", openPanel);
    // 放在账号行之前:属于「设置类」常驻入口,不挤压会话列表
    ft.insertBefore(btn, ft.querySelector(".who"));
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", install, { once: true });
  } else {
    install();
  }
})();
