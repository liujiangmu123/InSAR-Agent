/* ============================================================
   环境 / 终端 / 轨迹 三个 Dock 面板的 mock 数据（DEMO 模式）
   数据口径对齐 AGENT-DESIGN：
   - §0.5 环境事实基线（WSL 未装发行版、磁盘实测、Ridgecrest 11 对）
   - §4.13 阈值来源纪律（PENDING 只记 warning，不作硬 gate）
   - §7.7 三个新面板细化（cmd.sh 现场可见、失败并恢复的轨迹）
   后端接入后：环境由 runtime/probe.py 实测、日志从 log_path 读取、
   轨迹从 SQLite 的 OpenDiscoveryTrace 表导出 —— 本文件整体废弃。
   ============================================================ */
import { STEP_DEFS } from './state.js';

/* ============================================================
   环境面板（§0.5 实测基线 + 引擎示意值）
   ============================================================ */
export const ENV_NOTE = '引擎探测结果为示意值 · 后端未接入（probe.py 就绪前，真实状态为「全部未探测」）';

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

/* ============================================================
   终端面板：按步骤的全量 mock 日志（Ridgecrest 场景）
   行格式 [text, tone]，tone ∈ cmd|dim|ok|warn|err|''（与 tokens.css
   的 --term-* 配色对应）。每步 20–40 行，含 1–2 行 WARNING。
   ============================================================ */
const L = (text, tone = '') => [text, tone];

/** 11 个干涉对：[参考日期, 副日期, 时间基线 d, 垂直基线 m, 平均相干 γ̄] */
const PAIRS = [
  ['20190610', '20190622', 12,  -42, 0.71],
  ['20190610', '20190704', 24,   88, 0.66],
  ['20190622', '20190704', 12,  -35, 0.72],
  ['20190622', '20190710', 18,  104, 0.48],
  ['20190704', '20190710',  6,  -51, 0.46],
  ['20190704', '20190716', 12,   67, 0.52],
  ['20190710', '20190716',  6,  -23, 0.74],
  ['20190710', '20190728', 18,   95, 0.63],
  ['20190716', '20190728', 12,  -78, 0.69],
  ['20190716', '20190815', 30,  112, 0.55],
  ['20190728', '20190815', 18,  -19, 0.61],
];
/** 解缠结果（与 PAIRS 对齐）：[覆盖率 %, 残差点, 孤岛数]，孤岛合计 3。 */
const UNW = [
  [97.1, 84, 0], [95.8, 132, 0], [97.3, 77, 0], [89.6, 505, 1], [88.9, 612, 2],
  [91.2, 388, 0], [97.6, 61, 0], [94.8, 148, 0], [96.9, 96, 0], [92.4, 290, 0], [95.7, 171, 0],
];
const DATES7 = ['20190610', '20190622', '20190704', '20190710', '20190716', '20190728', '20190815'];

const pid = (i) => `#${String(i + 1).padStart(2, '0')}`;
const pnm = (p) => `${p[0]}_${p[1]}`;
const ss = (n) => String(n).padStart(2, '0');

export const TERM_LOGS = {
  1: [
    L('$ asf_search.py --platform sentinel-1 --beam IW --pol VV --intersects "POINT(-117.599 35.770)" --start 2019-06-10 --end 2019-08-15', 'cmd'),
    L('08:12:01 INFO  PRE  工作目录 /mnt/e/insar/ridgecrest-2019/work/01_data · conda env insar（python 3.11.9）', 'dim'),
    L('08:12:01 INFO  PRE  幂等守卫：未发现同指纹产物 → 进入 RUN', 'dim'),
    L('08:12:02 INFO  asf_search 7.0.4 · 查询 CMR granule 目录', 'dim'),
    L('08:12:03 INFO  命中 14 景 SLC · path 71 降轨 · frame 479/484', 'dim'),
    L('08:12:03 INFO  按 frame 479 去重 → 7 个获取日期', 'dim'),
    L(`08:12:03 INFO  ${DATES7.join(' ')}`, 'ok'),
    L('08:12:04 INFO  小基线选网：时间基线 ≤ 36 d · 垂直基线 ≤ 150 m', 'dim'),
    ...PAIRS.map((p, i) => L(`08:12:04 INFO    ${pid(i)} ${pnm(p)}  Bt=${p[2]}d  B⊥=${p[3]}m`, 'dim')),
    L('08:12:05 INFO  成网 11 对（全组合 C(7,2)=21 → 按基线剪枝 10 对）', 'ok'),
    L('08:12:05 WARNING  20190815 精密轨道 POEORB 未发布 · 暂用 RESORB 预报轨道（3 天后可替换）', 'warn'),
    L('08:12:06 INFO  本地缓存命中：hyp3/ 已有 11/11 对 · 110 文件 · 402 MB', 'ok'),
    L('08:12:08 INFO  sha256 校验 110/110 一致 · 0 字节下载', 'ok'),
    L('08:12:08 INFO  POST 产物注册 data/slc → SQLite（run_id 01J8X…）', 'dim'),
    L('08:12:08 INFO  ✓ exit 0 · 耗时 6.8 s', 'ok'),
  ],
  2: [
    L('$ dem.py --source copernicus-30m --bbox "35.3 36.2 -118.2 -117.0" --orbit poeorb', 'cmd'),
    L('08:13:01 INFO  PRE  工作目录 /mnt/e/insar/ridgecrest-2019/work/02_aux · conda env insar', 'dim'),
    L('08:13:01 INFO  PRE  幂等守卫：未发现同指纹产物 → 进入 RUN', 'dim'),
    L('08:13:02 INFO  Copernicus GLO-30 DEM · 覆盖需 4 瓦片', 'dim'),
    L('08:13:05 INFO    Copernicus_DSM_COG_10_N35_00_W118_00_DEM ✓ 25.1 MB', 'dim'),
    L('08:13:08 INFO    Copernicus_DSM_COG_10_N35_00_W117_00_DEM ✓ 24.7 MB', 'dim'),
    L('08:13:11 INFO    Copernicus_DSM_COG_10_N36_00_W118_00_DEM ✓ 25.3 MB', 'dim'),
    L('08:13:14 INFO    Copernicus_DSM_COG_10_N36_00_W117_00_DEM ✓ 24.9 MB', 'dim'),
    L('08:13:16 INFO  拼接重采样 → data/dem/dem_30m.wgs84 · 98.6 MB', 'ok'),
    L('08:13:17 WARNING  DEM 空洞填充 0.31% 像元（China Lake 干湖盆）· 双线性内插', 'warn'),
    L('08:13:18 INFO  下载精密轨道 POEORB · 7 个日期', 'dim'),
    ...DATES7.slice(0, 6).map((d, i) => L(`08:13:${ss(19 + i)} INFO    ${d} POEORB ✓`, 'dim')),
    L('08:13:25 WARNING  20190815 POEORB 未发布 · 记录 RESORB 回退（与第 1 步一致）', 'warn'),
    L('08:13:26 INFO  ERA5.h5 本地缓存命中 · 45.7 MB · 跳过 CDS 请求（CDS API 不稳定，竞品踩坑记录）', 'ok'),
    L('08:13:27 INFO  轨道与 DEM 元数据写入 provenance', 'dim'),
    L('08:13:27 INFO  POST 产物注册 data/dem → SQLite', 'dim'),
    L('08:13:27 INFO  ✓ exit 0 · 耗时 28.4 s', 'ok'),
  ],
  3: [
    L('$ topsApp.py --dostep coregister --check-only --esd-coherence-threshold 0.85', 'cmd'),
    L('08:14:01 INFO  PRE  工作目录 /mnt/e/insar/ridgecrest-2019/work/03_coreg · conda env insar', 'dim'),
    L('08:14:01 INFO  PRE  幂等守卫：HyP3 产品已含配准结果 → 转一致性校验模式（不重算）', 'dim'),
    L('08:14:02 INFO  子条带 IW1–IW3 · 每日期 27 burst · 共 189 burst 校验', 'dim'),
    L('08:14:02 INFO  几何基线复核 · 7 日期 × (lv_theta, lv_phi)', 'dim'),
    ...DATES7.map((d, i) => L(`08:14:${ss(3 + i)} INFO    ${d}  入射角 33.9°–43.2° · 方位偏移 0.00${(i % 3) + 4} px ✓`, 'dim')),
    L('08:14:12 INFO  ESD 精化残差复核（burst overlap 双差）', 'dim'),
    L('08:14:14 INFO  ESD 相干性 0.91 ≥ 0.85（A·上游默认阈值）→ 通过', 'ok'),
    L('08:14:14 WARNING  20190815 burst #7 重叠区相干 0.79 · 已降权处理', 'warn'),
    L('08:14:15 INFO  方位向配准精度 0.008 px（要求 < 0.01 px）', 'ok'),
    L('08:14:15 INFO  距离向配准精度 0.011 px', 'dim'),
    L('08:14:16 INFO  ENVI 头文件与 xml 元数据一致性 ✓', 'dim'),
    L('08:14:16 INFO  POST 校验结论写入 SQLite（无产物覆写）', 'dim'),
    L('08:14:16 INFO  ✓ exit 0 · 耗时 14.9 s · 配准校验通过，无需重跑', 'ok'),
  ],
  4: [
    L('$ topsApp.py --dostep interferogram --range-looks 10 --azimuth-looks 2 --check-only', 'cmd'),
    L('08:15:01 INFO  PRE  工作目录 /mnt/e/insar/ridgecrest-2019/work/04_ifg · conda env insar', 'dim'),
    L('08:15:01 INFO  PRE  幂等守卫：HyP3 已提供干涉对 → 校验 11 对', 'dim'),
    L('08:15:02 INFO  多视 10×2 · 像元 ≈ 46 m × 28 m', 'dim'),
    ...PAIRS.map((p, i) => L(
      `08:15:${ss(3 + i)} INFO    ${pid(i)} ${pnm(p)}  Bt=${p[2]}d  B⊥=${p[3]}m  γ̄=${p[4].toFixed(2)}${p[4] < 0.5 ? ' · 近断层去相干' : ' ✓'}`, 'dim')),
    L('08:15:15 INFO  全网平均相干 γ̄ = 0.62 · 直方图写入 QA', 'ok'),
    L('08:15:15 WARNING  3 对同震干涉（跨 2019-07-06 主震）γ̄ < 0.5 —— 解缠需掩膜低相干区', 'warn'),
    L('08:15:16 INFO  水汽条纹目测检查：20190704_20190710 存在长波条纹（待第 8 步 ERA5 校正）', 'dim'),
    L('08:15:16 INFO  POST 校验结论写入 SQLite（无产物覆写）', 'dim'),
    L('08:15:17 INFO  ✓ exit 0 · 耗时 16.2 s', 'ok'),
  ],
  5: [
    L('$ filter.py --method goldstein --alpha 0.4 --filter-strength 0.5', 'cmd'),
    L('08:16:01 INFO  PRE  工作目录 /mnt/e/insar/ridgecrest-2019/work/05_filt · conda env insar', 'dim'),
    L('08:16:01 INFO  PRE  幂等守卫：未发现同指纹产物 → 进入 RUN', 'dim'),
    L('08:16:01 INFO  Goldstein 谱滤波 · α=0.4 · FFT 窗 32×32 · 步进 16', 'dim'),
    ...PAIRS.map((p, i) => L(
      `08:16:${ss(5 + i * 2)} INFO    ${pid(i)} ${pnm(p)}  条纹 SNR ${(p[4] * 6).toFixed(1)} → ${(p[4] * 11).toFixed(1)} · 残差点 ↓${52 + ((i * 7) % 20)}%`, 'dim')),
    L('08:16:29 WARNING  近断层高梯度区条纹密度接近奈奎斯特 —— α 继续增大将平滑真实形变', 'warn'),
    L('08:16:30 INFO  平均残差点密度 0.8% → 0.3%', 'ok'),
    L('08:16:30 INFO  ✓ data/ifg_filt 写出 11 对 · 268 MB', 'ok'),
    L('08:16:31 INFO  POST 产物注册 data/ifg_filt → SQLite · artifact 指纹 11 项', 'dim'),
    L('08:16:31 INFO  ✓ exit 0 · 耗时 92.7 s', 'ok'),
  ],
  6: [
    L('$ snaphu.py --method snaphu_mcf --min-coherence 0.25 --threads 16', 'cmd'),
    L('09:02:11 INFO  PRE  工作目录 /mnt/e/insar/ridgecrest-2019/work/06_unwrap · conda env insar', 'dim'),
    L('09:02:11 INFO  PRE  幂等守卫：未发现同指纹产物 → 进入 RUN', 'dim'),
    L('09:02:11 INFO  SNAPHU 2.0.7 · 代价模式 DEFO · MCF 求解器初始化', 'dim'),
    L('09:02:12 INFO  掩膜 γ < 0.25 · 输入 11 对', 'dim'),
    L('09:02:19 ERROR  snaphu: cost array allocation failed —— 申请 12.4 GB > WSL 可用 7.8 GB', 'err'),
    L('09:02:19 ERROR  pair 20190704_20190710（同震对 · 全幅无分块）中止 · rc=1', 'err'),
    L('09:02:20 INFO  失败分类 resource_exhausted → 处置：2×2 分块 + threads 16→8（资源参数，不改科学指纹）', 'dim'),
    L('$ snaphu.py --method snaphu_mcf --min-coherence 0.25 --tiles 2 2 --row-overlap 200 --threads 8   # revision #1', 'cmd'),
    ...PAIRS.map((p, i) => L(
      `09:${ss(4 + i * 2)}:31 INFO    ${pid(i)} ${pnm(p)}  覆盖 ${UNW[i][0].toFixed(1)}% · 残差点 ${UNW[i][1]}${UNW[i][2] ? ` · 孤岛 ${UNW[i][2]}` : ''}`, 'dim')),
    L('09:25:58 WARNING  共 3 处解缠孤岛（近断层低相干区）· 已掩膜并写入 QA 报告', 'warn'),
    L('09:26:00 INFO  全网解缠覆盖率 94.2% · 平均残差点 156/对', 'ok'),
    L('09:26:00 WARNING  unwrap_coverage 阈值 0.70 未标定（status=PENDING）—— 0.942 通过但仅记 warning 级，不作硬 gate（§4.13）', 'warn'),
    L('09:26:01 INFO  ✓ data/unw 写出 11 对 · 74 MB', 'ok'),
    L('09:26:01 INFO  POST 产物注册 data/unw + params/unwrap.yaml（决策产物）· artifact 指纹 12 项', 'dim'),
    L('09:26:01 INFO  ✓ exit 0 · 耗时 23 min 50 s（含 1 次失败重试 · 断点续跑未重跑已完成对）', 'ok'),
  ],
  7: [
    L('$ smallbaselineApp.py RidgecrestSenDT71.txt --dostep invert_network', 'cmd'),
    L('09:31:01 INFO  PRE  工作目录 /mnt/e/insar/ridgecrest-2019/work/07_inversion · conda env insar', 'dim'),
    L('09:31:01 INFO  PRE  幂等守卫：未发现同指纹产物 → 进入 RUN', 'dim'),
    L('09:31:02 INFO  MintPy 1.6.4 · 载入 11 对解缠相位 → ifgramStack.h5（1.9 GB）', 'dim'),
    L('09:31:20 INFO  相位 → 位移换算（λ=55.5 mm，C 波段）', 'dim'),
    L('09:31:40 INFO  闭合回路检查：12 个三角环 · 闭合残差中位数 0.11 rad ✓', 'ok'),
    L('09:31:41 INFO  参考点 (35.740°N, 117.585°W) γ̄=0.93 · 距断层 14.2 km', 'dim'),
    L('09:31:42 INFO  weightFunc=no（实测配置有意覆盖 MintPy 默认 var —— A·实测配置）', 'dim'),
    L('09:31:45 INFO  SBAS 网络反演 1,689,305 像元 · 加权最小二乘', 'dim'),
    L('09:33:05 INFO    迭代 1/4 · 残差 RMS 8.1 mm', 'dim'),
    L('09:33:52 INFO    迭代 2/4 · 残差 RMS 5.9 mm', 'dim'),
    L('09:34:41 INFO    迭代 3/4 · 残差 RMS 4.9 mm', 'dim'),
    L('09:35:30 INFO    迭代 4/4 · 残差 RMS 4.7 mm · 收敛', 'dim'),
    L('09:35:58 INFO  时序重建 7 个日期 · 参考日期 20190610', 'ok'),
    L('09:35:59 WARNING  时间相干 < 0.7 像元占 8.3% · 并入 maskTempCoh.h5（不参与后续统计）', 'warn'),
    L('09:36:00 INFO  残差直方图写入 QA', 'dim'),
    L('09:36:00 INFO  内存峰值 6.2 GB / cgroup 上限 8 GB', 'dim'),
    L('09:36:00 INFO  ✓ products/timeseries.h5 · 72 MB', 'ok'),
    L('09:36:00 INFO  POST 产物注册 timeseries.h5 → SQLite', 'dim'),
    L('09:36:01 INFO  ✓ exit 0 · 耗时 5 min 40 s', 'ok'),
  ],
  8: [
    L('$ smallbaselineApp.py RidgecrestSenDT71.txt --dostep correct_troposphere', 'cmd'),
    L('09:38:01 INFO  PRE  工作目录 /mnt/e/insar/ridgecrest-2019/work/08_correction · conda env insar', 'dim'),
    L('09:38:01 INFO  PRE  幂等守卫：未发现同指纹产物 → 进入 RUN', 'dim'),
    L('09:38:01 INFO  PyAPS3 0.3.6 · ERA5 气压层 37 层 · 本地缓存命中（45.7 MB）', 'dim'),
    ...DATES7.map((d, i) => L(`09:38:${ss(5 + i * 5)} INFO    ${d}  天顶延迟 2.${31 + i} m → LOS 投影 ✓`, 'dim')),
    L('09:38:41 WARNING  20190728 位于 ERA5 6 h 网格中点 · 时间内插不确定度偏大（已记 QA）', 'warn'),
    L('09:38:52 INFO  固体潮校正 PySolid · 7 日期 ✓', 'dim'),
    L('09:38:53 INFO  固体潮最大改正 4.1 mm', 'dim'),
    L('09:39:10 INFO  DEM 误差估计（Fattahi & Amelung 2013）· 垂直基线跨度 216 m', 'dim'),
    L('09:39:30 INFO  linear ramp 移除 · 参考点保持不动', 'dim'),
    L('09:39:55 INFO  时序标准差 6.8 mm → 4.6 mm（−32%）', 'ok'),
    L('09:39:56 INFO  ✓ products/timeseries_corrected.h5', 'ok'),
    L('09:39:56 INFO  POST 产物注册 timeseries_corrected.h5 → SQLite', 'dim'),
    L('09:39:56 INFO  ✓ exit 0 · 耗时 2 min 18 s', 'ok'),
  ],
  9: [
    L('$ timeseries2velocity.py timeseries_corrected.h5 --model step --step-date 20190706', 'cmd'),
    L('09:42:00 INFO  PRE  工作目录 /mnt/e/insar/ridgecrest-2019/work/09_model · conda env insar', 'dim'),
    L('09:42:00 INFO  PRE  幂等守卫：未发现同指纹产物 → 进入 RUN', 'dim'),
    L('09:42:01 INFO  载入 timeseries_corrected.h5 · 72 MB', 'dim'),
    L('09:42:01 INFO  模型 step(20190706T0320) · stepFuncDate 来源 A·实测配置（RidgecrestSenDT71.txt）', 'dim'),
    L('09:42:02 INFO  设计矩阵 [常数, 速度, 阶跃] · 每像元 7 观测', 'dim'),
    L('09:42:02 INFO  参考日期 20190610 · 参考点保持', 'dim'),
    L('09:42:05 INFO  最小二乘拟合 1,689,305 像元', 'dim'),
    L('09:42:29 INFO  拟合优度 R² 中位数 0.96', 'ok'),
    L('09:42:30 INFO  同震阶跃场：西盘 −182.0 ± 12.4 mm · 东盘 +96.5 ± 8.7 mm（LOS）', 'ok'),
    L('09:42:30 WARNING  观测窗口仅 40 d —— 震后弛豫与阶跃不可分，不输出 postseismic 参数（X, not Y）', 'warn'),
    L('09:42:31 INFO  背景速度项不显著（|v| < 2 mm/yr · 1σ 内）· 符合震间期预期', 'dim'),
    L('09:42:31 INFO  残差时序 RMS 3.4 mm · 无系统性季节残留', 'dim'),
    L('09:42:31 INFO  不确定度：协方差传播与残差 bootstrap（200 次）一致', 'dim'),
    L('09:42:32 INFO  应用掩膜 maskTempCoh.h5（8.3% 像元置 NaN）', 'dim'),
    L('09:42:32 INFO  HDF5 属性：REF_DATE=20190610 · UNIT=m/yr', 'dim'),
    L('09:42:32 INFO  快视图 velocity_preview.png 写出', 'dim'),
    L('09:42:32 INFO  ✓ products/velocity.h5（velocity / velocityStd / step20190706）', 'ok'),
    L('09:42:33 INFO  POST 产物注册 velocity.h5 → SQLite', 'dim'),
    L('09:42:33 INFO  ✓ exit 0 · 耗时 31.8 s', 'ok'),
  ],
  10: [
    L('$ figure_journal.py velocity.h5 --dpi 600 --cmap roma --format png+pdf', 'cmd'),
    L('09:44:01 INFO  PRE  工作目录 /mnt/e/insar/ridgecrest-2019/work/10_figures · conda env insar', 'dim'),
    L('09:44:01 INFO  PRE  幂等守卫：未发现同指纹产物 → 进入 RUN', 'dim'),
    L('09:44:02 INFO  地理编码 → WGS84 · 30 m 网格 · 双线性重采样', 'dim'),
    L('09:44:15 INFO  多边形裁剪至 AOI（35.3–36.2°N, 118.2–117.0°W）', 'dim'),
    L('09:44:20 INFO  色带 roma（Crameri 科学色带 · 色盲安全）· 对称范围 ±200 mm', 'dim'),
    L('09:44:21 INFO  叠加：断层迹线（USGS Qfaults）· 震中（Mw 7.1 / Mw 6.4）· 比例尺 · 指北针', 'dim'),
    L('09:44:23 INFO  GNSS 站 P580 站位标注', 'dim'),
    L('09:44:24 INFO  剖面线 A–A′（跨断层）叠加', 'dim'),
    L('09:44:35 WARNING  中文注记回退 Noto Sans CJK SC —— 期刊模板要求嵌入字体，pdf 已矢量嵌入', 'warn'),
    L('09:44:48 INFO  vel_ridgecrest_2019.png · 600 dpi · 4.8 MB ✓', 'ok'),
    L('09:44:59 INFO  ts_ridgecrest.png · 600 dpi · 2.1 MB ✓', 'ok'),
    L('09:45:07 INFO  pdf 版本 2 幅（矢量文本可编辑）✓', 'ok'),
    L('09:45:08 INFO  图题与色带单位复核：mm（LOS）· 正值朝向卫星', 'dim'),
    L('09:45:08 INFO  图幅宽 190 mm（双栏）· dpi 元数据校验 600 ✓', 'dim'),
    L('09:45:08 INFO  出图参数写入 figure_meta.json（可复现）', 'dim'),
    L('09:45:08 INFO  POST 产物注册 2 幅图件 → SQLite', 'dim'),
    L('09:45:08 INFO  ✓ exit 0 · 耗时 66.4 s', 'ok'),
  ],
  11: [
    L('$ crossval_ps_sbas.py --corr-threshold 0.85', 'cmd'),
    L('09:47:00 INFO  PRE  工作目录 /mnt/e/insar/ridgecrest-2019/work/11_qa · conda env insar', 'dim'),
    L('09:47:00 INFO  PRE  幂等守卫：未发现同指纹产物 → 进入 RUN', 'dim'),
    L('09:47:01 INFO  PS 链（pystamps 0.3.4）：214,880 点 · SBAS 链（MintPy）：1,689,305 像元', 'dim'),
    L('09:47:02 INFO  应用 maskTempCoh 后有效像元 1,548,972', 'dim'),
    L('09:47:05 INFO  重叠高相干区抽样 48,212 点（最近邻 ≤ 30 m · 建筑/裸岩）', 'dim'),
    L('09:47:08 INFO  PS/SBAS 参考点一致性校验 ✓', 'dim'),
    L('09:47:22 INFO  Pearson r = 0.92 · RMSE 3.1 mm/yr · 系统偏差 0.2 mm/yr', 'ok'),
    L('09:47:22 WARNING  corr_threshold=0.85 status=PENDING（本地标定未完成）—— 判定降格为 warning，不作硬 gate（§4.13）', 'warn'),
    L('09:47:23 INFO  空间分布：相关性最低分位出现在断层 2 km 缓冲带（预期内）', 'dim'),
    L('09:47:30 INFO  GNSS 对照 P580：r = 0.86 · 速率差 0.4 mm/yr', 'ok'),
    L('09:47:31 INFO  闭合回路复检：通过（与第 7 步一致）', 'dim'),
    L('09:47:32 INFO  相关性直方图写出 crossval_hist.png', 'dim'),
    L('09:47:32 INFO  速率场差分图写出 diff_ps_sbas.png', 'dim'),
    L('09:47:33 INFO  证据级别评定：audited —— 3 项阈值 PENDING，validated 不可声称', ''),
    L('09:47:34 INFO  ✓ provenance.json 写出（schema 1.0 · 含第 6 步失败重试记录）', 'ok'),
    L('09:47:34 INFO  ✓ methods_draft.md 写出（证据边界：X, not Y）', 'ok'),
    L('09:47:34 INFO  QA 汇总 qa_summary.json 写出', 'dim'),
    L('09:47:34 INFO  POST 产物注册 → SQLite · 审计触发（try/finally 守护）', 'dim'),
    L('09:47:34 INFO  ✓ exit 0 · 耗时 88.2 s', 'ok'),
  ],
};

/* ============================================================
   cmd.sh：每步等价裸命令脚本（§4.7 副产品 · §7.7 面板 8）
   动态命令行由 dock.js 的 buildCmd() 传入，保证与当前 method/params 一致。
   ============================================================ */
const STEP_DIR = {
  1: '01_data', 2: '02_aux', 3: '03_coreg', 4: '04_ifg', 5: '05_filt', 6: '06_unwrap',
  7: '07_inversion', 8: '08_correction', 9: '09_model', 10: '10_figures', 11: '11_qa',
};

export function cmdSh(stepId, cmdline) {
  const def = STEP_DEFS.find((d) => d.id === stepId);
  return [
    '#!/usr/bin/env bash',
    `# cmd.sh · 第 ${stepId} 步 ${def ? def.name : ''} · run_id 01J8X…-ridgecrest-2019`,
    '# 由 runtime/render.py 从模板渲染（演示 mock）· 与 Agent 实际执行完全等价',
    '# 不用 set -e：需捕获 rc 写入 job.rc（五阶段执行器以此判定 RUN 结束）',
    'set -uo pipefail',
    'source "$HOME/miniforge3/etc/profile.d/conda.sh"',
    'conda activate insar',
    `cd "/mnt/e/insar/ridgecrest-2019/work/${STEP_DIR[stepId] || 'misc'}"`,
    '',
    cmdline,
    'rc=$?',
    'echo "$rc" > job.rc',
    'exit "$rc"',
  ].join('\n');
}

/* ============================================================
   轨迹面板：Lab Notebook（§7.2 面板 7）
   schema 对齐 OpenDiscoveryTrace（agent_harness.py:367-378）：
   step / phase / thought / action{tool,input} / observation /
   error{occurred,type,message} / revision_trigger / confidence / wall_time
   #05 → #06 是一对「失败并恢复」记录（§7.7：这才是论文素材）。
   ============================================================ */
export const TRACE = [
  {
    id: 'T-01', ts: '2026-08-12T08:12:01+08:00', step: 1, phase: '数据获取',
    thought: '意图为 Ridgecrest 同震形变。数据大概率已在本地（教学复用），先查缓存指纹再决定是否下载。',
    action: { tool: 'asf_search.py', input: '--platform sentinel-1 --beam IW --start 2019-06-10 --end 2019-08-15' },
    observation: '7 个获取日期成网 11 对；hyp3/ 缓存 110 文件 sha256 全部一致，0 字节下载。',
    error: { occurred: false, type: null, message: null },
    revision_trigger: null, confidence: 0.95, wall_time: 6.8,
  },
  {
    id: 'T-02', ts: '2026-08-12T08:12:10+08:00', step: 1, phase: '数据获取',
    thought: 'HyP3 产品已含配准/干涉结果，按指纹判定第 1–5 步可跳过范围，避免无谓重算。',
    action: { tool: 'core/store.py', input: '--scan --session ridgecrest-2019' },
    observation: '第 1–5 步指纹匹配 → 跳过；第 6 步配置 3D_FULL 不可执行，成为决策点。',
    error: { occurred: false, type: null, message: null },
    revision_trigger: null, confidence: 0.92, wall_time: 3.1,
  },
  {
    id: 'T-03', ts: '2026-08-12T08:13:24+08:00', step: 2, phase: '辅助数据',
    thought: '20190815 精密轨道未发布：等 3 天 vs 用 RESORB。同震阶跃对轨道误差不敏感（< 1 mm），不值得阻塞。',
    action: { tool: 'orbit.py', input: '--type poeorb --fallback resorb --date 20190815' },
    observation: '6/7 日期 POEORB；20190815 记录 RESORB 回退并写入 provenance，后续可无痛替换。',
    error: { occurred: false, type: null, message: null },
    revision_trigger: null, confidence: 0.88, wall_time: 28.4,
  },
  {
    id: 'T-04', ts: '2026-08-12T09:01:12+08:00', step: 6, phase: '解缠',
    thought: '3D_FULL 工具链缺失不可行；同震大梯度下 MCF 比 smooth 稳健且免交互；icu 低相干区易产生孤岛。',
    action: { tool: 'planner/feasibility.py', input: '--step 6 --candidates snaphu_mcf,snaphu_smooth,icu,3D_FULL' },
    observation: '候选 4 → 3（3D_FULL 移除，理由留档）。推荐 snaphu_mcf，专家模式下交用户确认。',
    error: { occurred: false, type: null, message: null },
    revision_trigger: null, confidence: 0.87, wall_time: 4.2,
  },
  {
    id: 'T-05', ts: '2026-08-12T09:02:11+08:00', step: 6, phase: '解缠',
    thought: '按 20 核满配 threads=16 提速；同震对条纹密，先尝试全幅解缠保持全局一致性。',
    action: { tool: 'snaphu.py', input: '--method snaphu_mcf --min-coherence 0.25 --threads 16' },
    observation: '同震对 20190704_20190710 全幅 MCF 需 12.4 GB，超过 WSL 可用 7.8 GB，进程中止。',
    error: { occurred: true, type: 'resource_exhausted', message: 'snaphu: cost array allocation failed（12.4 GB > 7.8 GB available）' },
    revision_trigger: null, confidence: 0.42, wall_time: 8,
  },
  {
    id: 'T-06', ts: '2026-08-12T09:02:21+08:00', step: 6, phase: '解缠',
    thought: 'OOM 属资源失败而非科学失败：2×2 分块可把峰值内存压到约 1/4；threads 属资源参数，改动不触发 STALE 级联。',
    action: { tool: 'snaphu.py', input: '--method snaphu_mcf --min-coherence 0.25 --tiles 2 2 --row-overlap 200 --threads 8' },
    observation: '11/11 对解缠成功，覆盖率 94.2%，3 处孤岛已掩膜。断点续跑生效：失败前已完成的对未重跑。',
    error: { occurred: false, type: null, message: null },
    revision_trigger: 'error_recovery：resource_exhausted → 2×2 分块 + threads 16→8（资源参数，科学指纹不变，无需标脏）',
    confidence: 0.90, wall_time: 1430,
    recovery: { attempted: true, successful: true },
  },
  {
    id: 'T-07', ts: '2026-08-12T09:31:02+08:00', step: 7, phase: '时序反演',
    thought: '闭合回路残差先行检查 —— 解缠误差会伪装成形变信号。weightFunc=no 沿用实测配置的有意覆盖。',
    action: { tool: 'smallbaselineApp.py', input: 'RidgecrestSenDT71.txt --dostep invert_network' },
    observation: '12 个三角环残差中位数 0.11 rad；1,689,305 像元反演 4 次迭代收敛，RMS 4.7 mm。',
    error: { occurred: false, type: null, message: null },
    revision_trigger: null, confidence: 0.92, wall_time: 340,
  },
  {
    id: 'T-08', ts: '2026-08-12T09:38:01+08:00', step: 8, phase: '误差校正',
    thought: 'ERA5 已缓存，预期 std 降 30% 左右；GACOS 缺凭据已被规则引擎移除，不进入决策。',
    action: { tool: 'smallbaselineApp.py', input: '--dostep correct_troposphere（tropo_era5_pyaps）' },
    observation: '时序 std 6.8 → 4.6 mm（−32%）；20190728 的 6 h 内插不确定度已写入 QA。',
    error: { occurred: false, type: null, message: null },
    revision_trigger: null, confidence: 0.90, wall_time: 138,
  },
  {
    id: 'T-09', ts: '2026-08-12T09:42:01+08:00', step: 9, phase: '形变模型',
    thought: '同震阶跃是主信号；观测窗口 40 天，弛豫分量与阶跃不可分 —— 诚实起见不输出 postseismic 参数。',
    action: { tool: 'timeseries2velocity.py', input: '--model step --step-date 20190706' },
    observation: 'R² 中位数 0.96；西盘 −182.0±12.4 mm，东盘 +96.5±8.7 mm（LOS）。',
    error: { occurred: false, type: null, message: null },
    revision_trigger: null, confidence: 0.93, wall_time: 32,
  },
  {
    id: 'T-10', ts: '2026-08-12T09:44:02+08:00', step: 10, phase: '成图',
    thought: '期刊要求 600 dpi 与色盲安全色带；roma 对发散形变场语义合适。',
    action: { tool: 'figure_journal.py', input: '--dpi 600 --cmap roma --format png+pdf' },
    observation: '2 幅图 + pdf 矢量版；比例尺/断层迹线/震中标注齐全。',
    error: { occurred: false, type: null, message: null },
    revision_trigger: null, confidence: 0.97, wall_time: 66,
  },
  {
    id: 'T-11', ts: '2026-08-12T09:47:01+08:00', step: 11, phase: '质检',
    thought: 'corr_threshold 状态 PENDING（无先例可循，须本地标定）—— 按 §4.13 只能记 warning，不能宣称硬性通过。',
    action: { tool: 'crossval_ps_sbas.py', input: '--corr-threshold 0.85' },
    observation: 'PS/SBAS 重叠区 r=0.92；GNSS P580 r=0.86。证据级别评定 audited（3 项阈值待标定，validated 不可声称）。',
    error: { occurred: false, type: null, message: null },
    revision_trigger: null, confidence: 0.90, wall_time: 88,
  },
];
