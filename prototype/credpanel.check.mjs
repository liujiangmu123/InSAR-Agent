/* ============================================================
   数据下载凭证区纯函数与接线自查(node prototype/credpanel.check.mjs)
   —— credpanel.js 的可测内核:徽章三态 / 保存请求体(留空→null、
   方式隔离)/ 掩码占位符,外加源码级接线断言(index.html 两行引入、
   自初始化盯 #pane-env + MutationObserver、注册外链、密文输入框类型、
   file:// 短路、无明文回显路径)。

   credpanel.js 顶层以 typeof document 守卫自初始化:node 导入只暴露
   纯函数,无需 DOM stub(与 datasets.check.mjs 同路线)。
   ============================================================ */
import { readFileSync } from 'node:fs';

let failed = 0;
let passed = 0;
function check(name, cond) {
  if (cond) { passed += 1; console.log(`  ok  ${name}`); }
  else { failed += 1; console.error(`  FAIL ${name}`); }
}

const CP = await import('./js/credpanel.js');
const { credBadge, saveBody, maskedHint } = CP;

/* ---------------- A. 徽章三态 ---------------- */
console.log('== A. credBadge ==');
check('A1 null/未配置 → stale 醒目徽章',
  credBadge(null).tone === 'stale' && credBadge(null).text === '未配置');
check('A2 configured:false 同样按未配置',
  credBadge({ configured: false, mode: 'none' }).tone === 'stale');
const tok = credBadge({ configured: true, mode: 'token' });
check('A3 token 方式 → ok 徽章且标注 EDL token',
  tok.tone === 'ok' && tok.text.includes('EDL token'));
const pwd = credBadge({ configured: true, mode: 'password' });
check('A4 账号密码方式 → ok 徽章且标注账号密码',
  pwd.tone === 'ok' && pwd.text.includes('账号密码'));
check('A5 aria 完整无障碍名带方式说明',
  tok.aria.includes('已配置') && credBadge(null).aria.includes('尚未配置'));

/* ---------------- B. 保存请求体(留空沿用 + 方式隔离) ---------------- */
console.log('\n== B. saveBody ==');
check('B1 token 方式只带 edl_token 字段',
  JSON.stringify(Object.keys(saveBody('token', { edl_token: 'x', earthdata_username: 'u' })))
    === JSON.stringify(['edl_token']));
check('B2 password 方式只带用户名/密码字段(token 输入残留不误伤已存值)',
  JSON.stringify(Object.keys(saveBody('password',
    { edl_token: 'stale', earthdata_username: 'u', earthdata_password: 'p' })))
    === JSON.stringify(['earthdata_username', 'earthdata_password']));
check('B3 空串/纯空白 → null(服务端语义:留空 = 沿用旧值)',
  saveBody('token', { edl_token: '  ' }).edl_token === null
  && saveBody('password', {}).earthdata_password === null);
check('B4 值两端空白被裁剪',
  saveBody('token', { edl_token: ' abc ' }).edl_token === 'abc'
  && saveBody('password', { earthdata_username: ' u ', earthdata_password: 'p' })
    .earthdata_username === 'u');

/* ---------------- C. 掩码占位符 ---------------- */
console.log('\n== C. maskedHint ==');
check('C1 有掩码 → 「掩码(留空 = 沿用已保存值)」',
  maskedHint('********', '填 token') === '********(留空 = 沿用已保存值)');
check('C2 无掩码 → 原始提示', maskedHint('', '填 token') === '填 token');

/* ---------------- D. 源码级接线 ---------------- */
console.log('\n== D. 源码级接线 ==');
const src = readFileSync(new URL('./js/credpanel.js', import.meta.url), 'utf-8');
check('D1 自初始化盯 #pane-env 且用 MutationObserver 在重渲染后回插',
  src.includes("getElementById('pane-env')") && src.includes('MutationObserver'));
check('D2 保留 file:// 判据(离线打开原型不发请求,与 diagexport 同约定)',
  src.includes("location.protocol !== 'file:'"));
check('D3 「去注册 Earthdata 账号」外链在位且新窗安全打开',
  src.includes('https://urs.earthdata.nasa.gov/users/new')
  && src.includes('rel="noopener noreferrer"'));
check('D4 token 与密码输入框都是 password 类型(不可肩窥)',
  /type="password"\s+name="edl_token"/.test(src)
  && /type="password"\s+name="earthdata_password"/.test(src));
check('D5 密文永不回填 value:视图应用时密文框清空、掩码只进占位符',
  src.includes("f('edl_token').value = ''")
  && src.includes("f('earthdata_password').value = ''")
  && !src.includes("f('edl_token').value = cfg"));
check('D6 三个 API 端点齐备(GET/POST /api/credentials + /verify)',
  src.includes("fetch('/api/credentials')")
  && src.includes("fetch('/api/credentials', {")
  && src.includes("fetch('/api/credentials/verify'"));
check('D7 状态注记与徽章带 aria-live(读屏可感知保存/验证结果)',
  (src.match(/aria-live="polite"/g) || []).length >= 2);
check('D8 双方式切换按 aria-pressed 表达选中态',
  src.includes("data-mode=\"token\"") && src.includes("data-mode=\"password\"")
  && src.includes('aria-pressed'));

const html = readFileSync(new URL('./index.html', import.meta.url), 'utf-8');
check('D9 index.html 引入 css/credpanel.css 与 js/credpanel.js',
  html.includes('css/credpanel.css') && html.includes('js/credpanel.js'));

const css = readFileSync(new URL('./css/credpanel.css', import.meta.url), 'utf-8');
check('D10 卡片/方式切换/注记样式在位(.credpanel / .cred-mode / .cred-note)',
  css.includes('.credpanel') && css.includes('.cred-mode') && css.includes('.cred-note'));
check('D11 注记三色 tone 与 llmset-note 同款 *-text 档',
  ['data-tone="ok"', 'data-tone="warn"', 'data-tone="bad"'].every((t) => css.includes(t)));

/* ---------------- 收尾 ---------------- */
console.log(`\n${passed} 项通过${failed ? ` · ${failed} 项失败` : ' · 全部断言通过'}`);
process.exit(failed ? 1 : 0);
