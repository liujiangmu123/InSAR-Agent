/* ============================================================
   桌面壳「打开本地数据」桥自查(node prototype/desktopdata.check.mjs)
   —— desktopdata.js 的可测内核:环境检测 / 降级 / 事件契约 /
   browseAndRegister 流程(依赖注入,不碰 DOM),外加跨文件契约断言
   (index.html 引入、datasets.js 监听、desktop/src/opendata.rs 与
   tray.rs / main.rs 的壳侧接线与事件名镜像)。

   desktopdata.js 顶层以 typeof document/window 守卫自初始化:
   node 导入只暴露纯函数,无需 DOM stub(同 datasets.check.mjs 路线)。
   ============================================================ */
import { readFileSync } from 'node:fs';

let failed = 0;
let passed = 0;
function check(name, cond) {
  if (cond) { passed += 1; console.log(`  ok  ${name}`); }
  else { failed += 1; console.error(`  FAIL ${name}`); }
}

/* ---------------- A. 环境检测与降级 ---------------- */
console.log('== A. 环境检测与降级 ==');
const desktop = await import('./js/desktop.js');
check('A1 bare node(无 window)isDesktop() === false(Web/测试环境降级)',
  desktop.isDesktop() === false);

// 导入本身即断言:自初始化被 document/window 守卫拦下,不炸、无副作用
const DD = await import('./js/desktopdata.js');
check('A2 node 导入安全:不自初始化,导出可用',
  typeof DD.browseAndRegister === 'function' && typeof DD.receiptMessage === 'function');
check('A3 导入后未捏造全局 DOM(降级不留痕)',
  typeof globalThis.document === 'undefined' && typeof globalThis.window === 'undefined');

globalThis.window = { __TAURI__: { core: { invoke: async () => null } } };
check('A4 注入 window.__TAURI__ 后 isDesktop() === true(桌面环境检测)',
  desktop.isDesktop() === true);
delete globalThis.window;

/* ---------------- B. 事件与锚点契约 ---------------- */
console.log('\n== B. 事件与锚点契约 ==');
check('B1 刷新事件名 datasets:refresh(datasets.js / opendata.rs 同名)',
  DD.REFRESH_EVENT === 'datasets:refresh');
check('B2 回执事件名 datasets:root-registered(opendata.rs eval 广播)',
  DD.REGISTERED_EVENT === 'datasets:root-registered');
check('B3 注入按钮查重锚点 id = dsBrowseBtn', DD.BROWSE_BTN_ID === 'dsBrowseBtn');

/* ---------------- C. 回执文案 ---------------- */
console.log('\n== C. receiptMessage ==');
check('C1 成功回执含路径',
  DD.receiptMessage({ ok: true, path: 'E:\\data\\hyp3' }) === '已添加数据目录:E:\\data\\hyp3');
check('C2 失败回执含后端 detail',
  DD.receiptMessage({ ok: false, error: '必须是绝对路径' }) === '添加数据目录失败:必须是绝对路径');
check('C3 空 detail 容错(不抛错,给未知原因)',
  DD.receiptMessage(null) === '添加数据目录失败:未知原因'
  && DD.receiptMessage({ ok: false }) === '添加数据目录失败:未知原因');

/* ---------------- D. browseAndRegister 流程(依赖注入,无 DOM) ---------------- */
console.log('\n== D. browseAndRegister ==');
let calls;
function stubIO(pickResult, addResult) {
  calls = { add: 0, path: null, notify: [], dispatch: [] };
  return {
    pick: async () => pickResult,
    add: async (p) => { calls.add += 1; calls.path = p; return addResult; },
    notify: (m) => calls.notify.push(m),
    dispatch: (n) => calls.dispatch.push(n),
  };
}

const r1 = await DD.browseAndRegister(stubIO(null, { ok: true }));
check('D1 用户取消:不注册、无 toast、无广播(安静返回)',
  r1 === 'cancelled' && calls.add === 0
  && calls.notify.length === 0 && calls.dispatch.length === 0);

const r2 = await DD.browseAndRegister(stubIO('E:\\data\\hyp3', { ok: true }));
check('D2 成功:原样注册所选绝对路径(不加工)',
  r2 === 'picked' && calls.add === 1 && calls.path === 'E:\\data\\hyp3');
check('D3 成功:toast 回执含路径 + 广播 datasets:refresh 刷新数据集区',
  calls.notify.length === 1 && calls.notify[0].includes('E:\\data\\hyp3')
  && calls.dispatch.length === 1 && calls.dispatch[0] === DD.REFRESH_EVENT);

const r3 = await DD.browseAndRegister(stubIO('relative\\path', { ok: false, error: '必须是绝对路径' }));
check('D4 后端拒绝:toast 透传 detail、不广播刷新',
  r3 === 'failed' && calls.notify[0].includes('必须是绝对路径')
  && calls.dispatch.length === 0);

/* ---------------- E. 源码级接线(页面侧) ---------------- */
console.log('\n== E. 源码级接线(页面侧) ==');
const src = readFileSync(new URL('./js/desktopdata.js', import.meta.url), 'utf-8');
check('E1 Web 环境零影响:init 首行 isDesktop 短路(不注入不观察)',
  src.includes('if (!isDesktop()) return;'));
check('E2 注入锚点:数据集区「添加目录」按钮(aria-label)旁 + id 查重回插',
  src.includes('添加数据目录') && src.includes('BROWSE_BTN_ID')
  && src.includes('MutationObserver'));
check('E3 原生对话框走 desktop.js、注册走 datasets.js addRoot(与手输同一后端通路)',
  src.includes("from './desktop.js'") && src.includes("from './datasets.js'")
  && src.includes('pickDirectory') && src.includes('addRoot'));
check('E4 自初始化有 document/window 双守卫(node 导入安全的依据)',
  src.includes("typeof document !== 'undefined'") && src.includes("typeof window !== 'undefined'"));

const dsSrc = readFileSync(new URL('./js/datasets.js', import.meta.url), 'utf-8');
check('E5 datasets.js 监听 datasets:refresh 并重拉清单(reload: true)',
  dsSrc.includes("addEventListener('datasets:refresh'")
  && /datasets:refresh[\s\S]{0,120}reload: true/.test(dsSrc));

const html = readFileSync(new URL('./index.html', import.meta.url), 'utf-8');
check('E6 index.html 引入 js/desktopdata.js(datasets.js 之后)',
  html.includes('js/desktopdata.js')
  && html.indexOf('js/desktopdata.js') > html.indexOf('js/datasets.js'));

/* ---------------- F. 壳侧契约镜像(desktop/src,防两端漂移) ---------------- */
console.log('\n== F. 壳侧契约镜像 ==');
const rs = readFileSync(new URL('../desktop/src/opendata.rs', import.meta.url), 'utf-8');
check('F1 opendata.rs 广播同名事件(refresh + root-registered)',
  rs.includes('datasets:refresh') && rs.includes('datasets:root-registered'));
check('F2 opendata.rs 走同一后端端点 POST /api/datasets/roots',
  rs.includes('/api/datasets/roots'));
check('F3 拖拽入口在位(WindowEvent::DragDrop 的 Drop 分支)',
  rs.includes('DragDropEvent::Drop'));

const trayRs = readFileSync(new URL('../desktop/src/tray.rs', import.meta.url), 'utf-8');
check('F4 托盘菜单「打开数据文件夹…」接到 opendata 流程',
  trayRs.includes('打开数据文件夹…') && trayRs.includes('open_data_folder_flow'));

const mainRs = readFileSync(new URL('../desktop/src/main.rs', import.meta.url), 'utf-8');
check('F5 main.rs 健康检查后记录端口并安装拖拽钩子',
  mainRs.includes('set_backend_port') && mainRs.includes('opendata::install'));

/* ---------------- 收尾 ---------------- */
console.log(`\n${passed} 项通过${failed ? ` · ${failed} 项失败` : ' · 全部断言通过'}`);
process.exit(failed ? 1 : 0);
