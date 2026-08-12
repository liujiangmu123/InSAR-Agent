/* ============================================================
   排队消息（Cursor 排队语义的最小实现，RESEARCH §9 机制 #2）
   busy 期间用户在输入框回车 → 消息不再被丢弃，而是进入前端队列，
   在输入区上方渲染可撤销的 chip（「已排队：xxx ×」）；
   回合结束（app.js 的 setBusy(false) 时机）调 flush() 逐条发出 ——
   一次只发一条，下一条等下个回合结束，与 Cursor 当前行为一致。
   与「停止」按钮语义不冲突：abort 会先置 S.phase='paused'，
   此时 flush() 不发送，队列保留并 toast 提示。
   app.js 只保留 ≤4 行调用点：enqueue（submit busy 分支）、
   flush（setBusy(false)）、clear（reset）。
   ============================================================ */
import { h, icon, toast } from './dom.js';
import { S } from './state.js';

const queue = [];        // { id, text }，FIFO
let seq = 0;
let row = null;          // chip 行容器，惰性创建
let holdNoticed = false; // 「已停止，队列保留」提示只报一次，避免连环 toast

/** chip 行挂载点：主页面插在 #attachRow 之前；演示页退化为 #prompt 之前。 */
function ensureRow() {
  if (row && row.isConnected) return row;
  const attach = document.getElementById('attachRow');
  const prompt = document.getElementById('prompt');
  const anchor = attach || prompt;
  if (!anchor || !anchor.parentNode) return null;
  row = h('div', {
    class: 'queue-row', id: 'queueRow', hidden: true,
    'aria-label': '排队中的消息', 'aria-live': 'polite',
  });
  anchor.parentNode.insertBefore(row, anchor);
  return row;
}

/** 入队后清空输入框（同步触发 input 事件，让发送按钮态与高度自适应生效）。 */
function clearComposer() {
  const input = document.getElementById('prompt');
  if (!input) return;
  input.value = '';
  input.dispatchEvent(new Event('input', { bubbles: true }));
}

export function count() {
  return queue.length;
}

/** busy 期间的消息入队：渲染 chip，清空输入框。返回消息 id。 */
export function enqueue(text) {
  const t = String(text || '').trim();
  if (!t) return null;
  queue.push({ id: ++seq, text: t });
  holdNoticed = false;
  clearComposer();
  render();
  return seq;
}

/** 撤销一条排队消息（chip 上的 × 按钮）。 */
export function revoke(id) {
  const i = queue.findIndex((q) => q.id === id);
  if (i < 0) return false;
  queue.splice(i, 1);
  render();
  return true;
}

/** 清空队列（app.js reset：换会话/新对话时旧队列不应跨会话发出）。 */
export function clear() {
  queue.length = 0;
  holdNoticed = false;
  render();
}

/** 重绘 chip 行：每条消息一个可撤销 chip。 */
export function render() {
  const box = ensureRow();
  if (!box) return;
  box.hidden = !queue.length;
  box.replaceChildren(...queue.map((q) => h('span', { class: 'queue-chip' },
    h('span', { class: 'ic' }, icon('clock')),
    h('span', { class: 'nm', title: q.text }, `已排队：${q.text}`),
    h('button', {
      class: 'rm', type: 'button',
      'aria-label': `撤销排队消息：${q.text}`, title: '撤销',
      onclick: () => revoke(q.id),
    }, icon('x')))));
}

/**
 * 回合结束时逐条发出：每次调用只发最旧的一条（FIFO），
 * 剩余的等下一个回合结束再 flush —— 排队语义是「排到回合之间」。
 * S.phase === 'paused'（用户停止 / 降级停链）时不发送：
 * 停止的含义是「取消当前回合」，队列保留并提示，防止急停后消息自动窜出。
 * @param {(text: string) => void} send app.js 注入的发送入口
 */
export function flush(send) {
  if (!queue.length || typeof send !== 'function') return;
  if (S.phase === 'paused') {
    if (!holdNoticed) {
      holdNoticed = true;
      toast(`已停止：${queue.length} 条排队消息已保留，可点 chip 上的 × 撤销`);
    }
    return;
  }
  const item = queue.shift();
  render();
  // 让当前回合收尾（paintStatus 等）先完成再发送；发送会同步占用并清空
  // 输入框，这里保护用户回合间正在输入的草稿（发送后原样放回）
  setTimeout(() => {
    const input = document.getElementById('prompt');
    const draft = input ? input.value : '';
    send(item.text);
    if (input && input.value === '' && draft && draft !== item.text) {
      input.value = draft;
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }, 0);
}
