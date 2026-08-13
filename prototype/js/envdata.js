/* ============================================================
   环境面板（Dock 面板 9）的静态回落数据 —— 仅离线演示用。

   真实数据源已接入（GET /api/env + GET /api/setup/status，经
   envlive.js 拉取缓存），本文件只在后端不可达（file:// 打开、服务
   未启动）时被 envView 使用，且 UI 必须醒目标注「演示数据（后端
   未连接）」。

   终端 / 轨迹面板的演示日志与演示轨迹（TERM_LOGS / cmdSh / TRACE）
   已随「演示回落清除」删除：这两个面板在后端不可达时渲染诚实的
   错误态（states 统一构造器），不再假装有数据。

   本文件内一切数值为静态示意（口径对齐 AGENT-DESIGN §0.5 / §4.13 /
   §7.7 撰写时的实测基线），与当前真机状态无关，勿当真实环境读。
   ============================================================ */

/* ============================================================
   环境面板（§0.5 实测基线 + 引擎示意值）
   ============================================================ */
export const ENV_NOTE = '引擎探测结果为静态示意值（仅离线演示）· 后端连接恢复后自动切换为 /api/env 实测数据';

export const WSL = {
  ok: false,
  text: '未安装发行版',
  detail: 'wsl.exe 存在，但无已安装发行版；%USERPROFILE%\\.wslconfig 不存在（2026-08-10 实测 · §0.5.1）',
};

export const WORKSPACE = {
  path: null,
  hint: 'WSL 就绪后默认 /mnt/e/insar/<session>',
};

export const ENGINES = [
  { name: 'isce2',    ver: '2.6.5', ok: true,  note: 'conda-forge · 无 CUDA 模块' },
  { name: 'mintpy',   ver: '1.6.4', ok: true,  note: '' },
  { name: 'snaphu',   ver: '2.0.7', ok: true,  note: '' },
  { name: 'pystamps', ver: '0.3.4', ok: true,  note: '' },
  { name: 'pyaps3',   ver: '0.3.6', ok: true,  note: 'ERA5.h5 已缓存 45.7 MB' },
  { name: 'gacos',    ver: '—',     ok: false, note: '缺凭据' },
];

/** 磁盘实测（§0.5.3）。free/total 单位 GB。 */
export const DISKS = [
  { id: 'E', label: 'E: 工作区', total: 931, free: 483, note: '项目所在 · WSL 发行版导入首选' },
  { id: 'C', label: 'C: 系统',   total: 199, free: 36,  warn: '勿装 WSL 于此' },
  { id: 'F', label: 'F: 归档',   total: 932, free: 187 },
];
