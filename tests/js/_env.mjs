/* ============================================================
   Node 测试环境的最小浏览器桩
   —— state.js 在模块顶层读 localStorage(主题 / dock 宽度),
   Node 里没有这个全局对象,导入 state.js 之前必须先备好。
   约定:每个 *.test.mjs 必须把本文件放在第一个 import,
   ESM 按声明顺序求值,可保证桩先于 state.js 生效。
   ============================================================ */

const store = new Map();

// 极简 localStorage 假实现:仅覆盖 state.js 用到的接口,内存态、每进程独立
globalThis.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => { store.set(k, String(v)); },
  removeItem: (k) => { store.delete(k); },
  clear: () => { store.clear(); },
};

// setTheme / setDockWidth 会碰 document.documentElement,给哑对象兜底
globalThis.document = {
  documentElement: { dataset: {}, style: { setProperty() {} } },
};
