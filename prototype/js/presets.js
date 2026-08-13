/* ============================================================
   参数预设与输入校验层(presets.js)
   —— 非专家用户「把数据处理对」的前端防线。

   设计纪律:
   - 自初始化 + 幂等:模块加载后自动挂载;.pdetail 每次重渲染由
     MutationObserver 捕获并重新增强,单节点用 data 标记防重复。
   - 零侵入:不改 app.js / dock.js。校验拦截走 document 捕获阶段
     stopPropagation —— dock 的 input/blur/Enter 提交监听器挂在
     输入框自身(dom.js h() 用 addEventListener),捕获阶段在祖先
     节点截停事件,目标节点的监听器就不会执行,SET_PARAMS 出口
     由此被拦住;合法值原样放行,dock 原有提交路径不受影响。
   - 未知参数零干扰:知识表没有的参数不加校验、不加图标、不拦截。
   - 知识表为纯前端数据,按 capability id / 参数名 组织,键位与
     后端 /api/registry(src/insar_agent/registry/capabilities.py)
     一一对应 —— 将来技能系统落地后可整表切到 API 下发。
   ============================================================ */

/* ============================================================
   一、参数知识表
   结构:KNOWLEDGE[capabilityId] = {
     name:    步骤名(与 registry 对齐,仅用于提示文案)
     params:  { 参数名: 规格 }
     presets: { fast|standard|fine: { label, desc, values } } 或 null
     presetsNote: presets 为 null 时的原因说明(必填)
   }
   规格字段全部显式声明,不适用时写 null(知识表完整性测试依赖):
     type     'number'|'int'|'str'|'bool'|'list'
     min/max  数值范围(闭区间;null = 该侧无界/不适用)
     step     建议步进(仅提示,不做硬校验;null = 不适用)
     unit     单位(null = 无量纲)
     enum     合法枚举(null = 非枚举)
     pattern  字符串格式正则(null = 不限);patternHint 为其错误文案
     required 空值是否拒绝(false = 允许留空)
     default  默认值 —— 来源 registry/capabilities.py 的 Param.default
     help     一句话帮助(单位/范围/调参方向,气泡与错误文案复用)
   范围与档位依据写在行内注释(MintPy / ISCE2 / SNAPHU 文档常识),
   与 state.js PARAM_SCHEMA 的交集经核对完全一致(本表更全)。
   ============================================================ */

const TIER_IDS = ['fast', 'standard', 'fine'];
const TIER_LABELS = { fast: '快速预览', standard: '标准', fine: '精细' };

// 简写工厂:未给出的字段自动补 null,required 默认 true
function P(spec) {
  return {
    type: 'number', min: null, max: null, step: null, unit: null,
    enum: null, pattern: null, patternHint: null, required: true,
    default: null, help: '',
    ...spec,
  };
}

export const KNOWLEDGE = {
  1: {
    name: '数据获取',
    params: {
      // registry: min=2 max=200;SBAS 组网一般至少 5 景才谈得上时序
      scenes: P({
        type: 'int', min: 2, max: 200, step: 1, unit: '景', default: 7,
        help: '参与处理的影像景数;越多时序越稳,但下载与处理时间线性增长。',
      }),
      platform: P({
        type: 'str', default: 'sentinel-1',
        help: '卫星平台标识(如 sentinel-1);更换平台需同步调整轨道与 DEM 配置。',
      }),
      // 演示数据集的窗口格式:起止日期用 .. 连接
      dates: P({
        type: 'str', default: '2019-06-10..2019-08-15',
        pattern: /^\d{4}-\d{2}-\d{2}\.\.\d{4}-\d{2}-\d{2}$/,
        patternHint: '格式须为 YYYY-MM-DD..YYYY-MM-DD(起止日期)',
        help: '检索时间窗,格式 YYYY-MM-DD..YYYY-MM-DD;窗口过短难以分离形变与噪声。',
      }),
      // registry 独有(local_import 数据源目录);留空 = 默认工作区路径
      source: P({
        type: 'str', required: false, default: '',
        help: 'local_import 的数据源目录(HyP3 产品目录或 SLC 目录);留空用默认工作区。',
      }),
    },
    // 档位只动 scenes:平台/时间窗是研究对象的一部分,预设不应替用户改
    presets: {
      fast: { label: TIER_LABELS.fast, desc: '最小可用组网,先跑通再加密', values: { scenes: 5 } },
      standard: { label: TIER_LABELS.standard, desc: '演示数据集的既定景数', values: { scenes: 7 } },
      fine: { label: TIER_LABELS.fine, desc: '加密时序采样,下载量增大', values: { scenes: 12 } },
    },
    presetsNote: null,
  },
  2: {
    name: '辅助数据',
    params: {
      dem: P({
        type: 'str', default: 'copernicus-30m',
        help: 'DEM 来源;copernicus-30m 全球覆盖质量稳,srtm-30m 在高纬度有缺口。',
      }),
      // registry enum:poeorb 精密轨道(发布延迟约 20 天)/ resorb 快速轨道
      orbit: P({
        type: 'str', enum: ['poeorb', 'resorb'], default: 'poeorb',
        help: '轨道产品;poeorb 精密轨道最准(延迟约 20 天),resorb 快速轨道可即时获取。',
      }),
    },
    // 辅助数据没有比「精密轨道 + Copernicus DEM」更精的档 —— 精细与标准同值,
    // 两档同时高亮属于诚实呈现,不硬造差异
    presets: {
      fast: { label: TIER_LABELS.fast, desc: '快速轨道即时可得,精度略降', values: { dem: 'srtm-30m', orbit: 'resorb' } },
      standard: { label: TIER_LABELS.standard, desc: '精密轨道 + Copernicus DEM', values: { dem: 'copernicus-30m', orbit: 'poeorb' } },
      fine: { label: TIER_LABELS.fine, desc: '与标准一致(已是最优辅助数据)', values: { dem: 'copernicus-30m', orbit: 'poeorb' } },
    },
    presetsNote: null,
  },
  3: {
    name: '配准',
    params: {
      // ISCE2 topsApp 默认 0.85;过低引入噪声估计,过高样本不足难收敛
      esd_coherence_threshold: P({
        min: 0, max: 1, step: 0.05, default: 0.85,
        help: 'ESD 方位精配准的相干阈值;过低引入噪声估计,过高样本不足难收敛。',
      }),
      // registry resource 参数(本机 24 核,留 4 核给系统)
      threads: P({
        type: 'int', min: 1, max: 32, step: 1, unit: '线程', default: 8,
        help: '并行线程数;过大挤占系统其余任务,本机建议 ≤16。',
      }),
      // 以下为 stripmap(ALOS raw)场景专用路径参数:只提供帮助,不限格式
      reference_image: P({
        type: 'str', required: false, default: 'data/raw/reference/IMG-HH',
        help: 'stripmap:参考 raw 影像 IMG 相对路径(仅条带场景使用)。',
      }),
      reference_leader: P({
        type: 'str', required: false, default: 'data/raw/reference/LED',
        help: 'stripmap:参考影像 LED 头文件相对路径。',
      }),
      secondary_image: P({
        type: 'str', required: false, default: 'data/raw/secondary/IMG-HH',
        help: 'stripmap:从(secondary)raw 影像 IMG 相对路径。',
      }),
      secondary_leader: P({
        type: 'str', required: false, default: 'data/raw/secondary/LED',
        help: 'stripmap:从影像 LED 头文件相对路径。',
      }),
      resample_flag: P({
        type: 'str', enum: ['', 'dual2single'], required: false, default: '',
        help: 'FBD 从影像配 FBS 主影像时填 dual2single;留空 = 不重采样。',
      }),
      dem_path: P({
        type: 'str', required: false, default: 'data/dem/dem.wgs84',
        help: 'stripmap:ISCE 格式 DEM 相对路径。',
      }),
    },
    // 档位依据:topsApp 默认 0.85;放宽到 0.75 收敛快,收紧到 0.90 更准但需高相干
    presets: {
      fast: { label: TIER_LABELS.fast, desc: '放宽阈值,快速收敛', values: { esd_coherence_threshold: 0.75 } },
      standard: { label: TIER_LABELS.standard, desc: 'ISCE2 topsApp 默认', values: { esd_coherence_threshold: 0.85 } },
      fine: { label: TIER_LABELS.fine, desc: '收紧阈值,配准更准(需高相干)', values: { esd_coherence_threshold: 0.9 } },
    },
    presetsNote: null,
  },
  4: {
    name: '干涉',
    params: {
      // S1 IW 常用多视组合(距离×方位):20×4≈90m / 10×2≈40m / 5×1≈20m
      range_looks: P({
        type: 'int', min: 1, max: 40, step: 1, default: 10,
        help: '距离向多视数;越大信噪比越高、分辨率越低。S1 IW 常配 20×4/10×2/5×1。',
      }),
      azimuth_looks: P({
        type: 'int', min: 1, max: 40, step: 1, default: 2,
        help: '方位向多视数;与距离向按约 5:1 配比可得近方形像元(S1 IW)。',
      }),
      pairs: P({
        type: 'int', min: 1, max: 5000, step: 1, unit: '对', default: 11,
        help: '干涉对数量;由时空基线阈值剪枝而来,并非越多越好(受数据集约束)。',
      }),
      threads: P({
        type: 'int', min: 1, max: 32, step: 1, unit: '线程', default: 8,
        help: '并行线程数;干涉生成属 IO 密集,过大收益有限。',
      }),
    },
    // 档位只动多视比(速度/分辨率主轴);pairs 由组网剪枝决定,预设不碰
    presets: {
      fast: { label: TIER_LABELS.fast, desc: '粗分辨率(约 90 m),速度最快', values: { range_looks: 20, azimuth_looks: 4 } },
      standard: { label: TIER_LABELS.standard, desc: '约 40 m 分辨率,常规选择', values: { range_looks: 10, azimuth_looks: 2 } },
      fine: { label: TIER_LABELS.fine, desc: '约 20 m 分辨率,耗时数倍', values: { range_looks: 5, azimuth_looks: 1 } },
    },
    presetsNote: null,
  },
  5: {
    name: '滤波',
    params: {
      // Goldstein & Werner (1998):α 越大平滑越强;低相干区常用 0.4-0.6
      alpha: P({
        min: 0, max: 1, step: 0.05, default: 0.4,
        help: 'Goldstein 滤波强度 α;偏大压噪声但抹细节,偏小保细节但残差点多。',
      }),
      filter_strength: P({
        min: 0, max: 1, step: 0.05, default: 0.5,
        help: '滤波器强度权重;与 α 配合控制整体平滑程度。',
      }),
    },
    presets: {
      fast: { label: TIER_LABELS.fast, desc: '强滤波快速出干净图,细节有损', values: { alpha: 0.6, filter_strength: 0.7 } },
      standard: { label: TIER_LABELS.standard, desc: '低相干区的常规平衡点', values: { alpha: 0.4, filter_strength: 0.5 } },
      fine: { label: TIER_LABELS.fine, desc: '弱滤波保形变细节,噪声偏多', values: { alpha: 0.2, filter_strength: 0.3 } },
    },
    presetsNote: null,
  },
  6: {
    name: '解缠',
    params: {
      // MintPy 掩膜常用 0.2-0.4;SNAPHU 本身无硬默认(state.js 台账标 PENDING 待标定)
      min_coherence: P({
        min: 0, max: 1, step: 0.05, default: 0.25,
        help: '参与解缠的最小相干性;过低引入噪声点,过高丢失形变区覆盖。',
      }),
      // SNAPHU 代价函数三选一(snaphu 手册):DEFO 形变 / TOPO 地形 / SMOOTH 通用
      cost_mode: P({
        type: 'str', enum: ['SMOOTH', 'DEFO', 'TOPO'], default: 'SMOOTH',
        help: 'SNAPHU 代价函数;形变场景选 DEFO,地形建模选 TOPO,SMOOTH 通用平滑。',
      }),
      threads: P({
        type: 'int', min: 1, max: 32, step: 1, unit: '线程', default: 8,
        help: 'SNAPHU 并行分块线程数;本机 24 核建议 ≤16,留核给系统。',
      }),
    },
    // 档位依据:阈值升高 → 掩膜更多像元 → 问题规模小、速度快、覆盖少;反之覆盖全但慢
    presets: {
      fast: { label: TIER_LABELS.fast, desc: '高阈值少像元,速度优先', values: { min_coherence: 0.4, threads: 16 } },
      standard: { label: TIER_LABELS.standard, desc: '覆盖与噪声的常规平衡', values: { min_coherence: 0.25, threads: 8 } },
      fine: { label: TIER_LABELS.fine, desc: '低阈值保形变区覆盖,耗时最长', values: { min_coherence: 0.15, threads: 8 } },
    },
    presetsNote: null,
  },
  7: {
    name: '时序反演',
    params: {
      // MintPy 组网方式:small_baseline 冗余最多最稳;star 最快但误差集中于参考影像
      network: P({
        type: 'str', enum: ['small_baseline', 'star', 'sequential'], default: 'small_baseline',
        help: '组网方式;small_baseline 冗余最多最稳,sequential 只连相邻,star 最快但最脆。',
      }),
      // S1 12 天重访(A+B 6 天);基线过长失相干,过短网络断链
      max_temporal_baseline: P({
        type: 'int', min: 6, max: 730, step: 6, unit: '天', default: 120,
        help: '干涉对最大时间基线;偏大连接多但失相干对也多,S1 常用 48-180 天。',
      }),
      parallel_workers: P({
        type: 'int', min: 1, max: 16, step: 1, default: 4,
        help: 'MintPy 并行 worker 数;受内存限制,大数据集不宜过大。',
      }),
    },
    presets: {
      fast: { label: TIER_LABELS.fast, desc: '顺序连接 + 短基线,反演最快', values: { network: 'sequential', max_temporal_baseline: 60 } },
      standard: { label: TIER_LABELS.standard, desc: '小基线网络常规配置', values: { network: 'small_baseline', max_temporal_baseline: 120 } },
      fine: { label: TIER_LABELS.fine, desc: '更长基线加密网络冗余', values: { network: 'small_baseline', max_temporal_baseline: 180 } },
    },
    presetsNote: null,
  },
  8: {
    name: '误差校正',
    params: {
      // MintPy deramp 选项:linear 去轨道残差;quadratic 更强但可能吸收长波形变
      ramp: P({
        type: 'str', enum: ['no', 'linear', 'quadratic'], default: 'linear',
        help: '去趋势坡面;linear 去线性轨道残差,quadratic 更强但可能吸收真实长波形变。',
      }),
      // Fattahi & Amelung (2013) DEM 误差校正,MintPy 默认链路常开
      dem_error: P({
        type: 'bool', default: true,
        help: '是否估计并去除 DEM 误差(Fattahi & Amelung 方法);常规开启。',
      }),
      // registry 默认 False:对齐实测基准配置;pysolid 的 Windows DLL 未验证
      solid_earth_tides: P({
        type: 'bool', default: false,
        help: '固体潮校正(依赖 pysolid);长时序大范围收益明显,短窗口影响小。',
      }),
    },
    presets: {
      fast: { label: TIER_LABELS.fast, desc: '跳过全部校正,只看初步形变', values: { ramp: 'no', dem_error: false, solid_earth_tides: false } },
      standard: { label: TIER_LABELS.standard, desc: '线性去坡 + DEM 误差校正', values: { ramp: 'linear', dem_error: true, solid_earth_tides: false } },
      fine: { label: TIER_LABELS.fine, desc: '全量校正(含固体潮)', values: { ramp: 'quadratic', dem_error: true, solid_earth_tides: true } },
    },
    presetsNote: null,
  },
  9: {
    name: '形变模型',
    params: {
      // MintPy timeseries2velocity 周期项(年);只读列表,不做输入校验
      periods: P({
        type: 'list', required: false, unit: '年', default: [1, 0.5],
        help: '周期项(单位:年);[1, 0.5] = 年 + 半年周期,仅季节机理场景使用。',
      }),
      poly_order: P({
        type: 'int', min: 0, max: 3, step: 1, default: 1,
        help: '多项式阶数;1 = 线性速率(常规),≥2 谨慎使用(易过拟合短时序)。',
      }),
      // 同震阶跃日期:研究事件本身的属性,预设不应替用户决定
      step_date: P({
        type: 'str', required: false, default: '',
        pattern: /^(\d{8})?$/, patternHint: '格式须为 YYYYMMDD(8 位数字)或留空',
        help: '同震阶跃日期 YYYYMMDD(如 20190706);仅同震场景填写,留空 = 无阶跃项。',
      }),
    },
    // 形变模型由研究场景决定(同震日期/季节机理),硬造快慢档会误导 —— 不提供档位
    presets: null,
    presetsNote: '形变模型与研究场景强绑定(同震日期、季节机理),无通用快慢档;请按场景手工设置。',
  },
  10: {
    name: '出图导出',
    params: {
      // 期刊排版常识:线上预览 150 dpi 足够,印刷稿 ≥300,600 为安全值
      dpi: P({
        type: 'int', min: 72, max: 1200, step: 50, unit: 'dpi', default: 600,
        help: '输出分辨率;预览 150 足够,期刊常要求 ≥300,600 为安全值。',
      }),
      // Crameri 科学色带(roma/vik 等)色盲安全;形变图避免 jet
      cmap: P({
        type: 'str', default: 'roma',
        help: '色带;roma/vik 等 Crameri 科学色带色盲安全,形变图避免 jet。',
      }),
      format: P({
        type: 'str', default: 'png+pdf',
        help: '导出格式组合,如 png、pdf、png+pdf、geotiff(供 GIS)。',
      }),
    },
    presets: {
      fast: { label: TIER_LABELS.fast, desc: '低分辨率 png,快速预览', values: { dpi: 150, format: 'png' } },
      standard: { label: TIER_LABELS.standard, desc: '期刊安全值 600 dpi', values: { dpi: 600, format: 'png+pdf' } },
      fine: { label: TIER_LABELS.fine, desc: '超高分辨率,文件显著变大', values: { dpi: 1200, format: 'png+pdf' } },
    },
    presetsNote: null,
  },
  11: {
    name: '质检',
    params: {
      // PS/SBAS 交叉验证阈值:台账标 PENDING 待标定;0.85 为演示默认
      corr_threshold: P({
        min: 0, max: 1, step: 0.05, default: 0.85,
        help: 'PS/SBAS 交叉验证最低相关系数;过低放行坏结果,过高误杀正常方法差异。',
      }),
    },
    presets: {
      fast: { label: TIER_LABELS.fast, desc: '宽松阈值,先看链路通不通', values: { corr_threshold: 0.7 } },
      standard: { label: TIER_LABELS.standard, desc: '演示默认(待标定)', values: { corr_threshold: 0.85 } },
      fine: { label: TIER_LABELS.fine, desc: '严格阈值,宁可误杀', values: { corr_threshold: 0.9 } },
    },
    presetsNote: null,
  },
};

/* ============================================================
   二、数字文本归一化(全角 / 中文数字容错)
   目标:「0.25」「零点五」「十六」这类输入不至于被硬拒 ——
   归一化成 ASCII 数字文本后再走 Number() 解析。
   ============================================================ */

/**
 * 全角 → 半角(一律用 Unicode 转义,避免字面全角字符被编辑器/格式化误伤):
 *   \uFF10-\uFF19 全角数字 → 0-9(偏移 0xFEE0)
 *   \uFF0E 全角句点 / \u3002 中文句号 → .
 *   \uFF0D 全角负号 / \u2212 数学负号 → -
 *   \uFF0B 全角加号 → + ;千分位逗号(, 与 \uFF0C)剥离
 */
function toHalfWidth(s) {
  return s
    .replace(/[\uFF10-\uFF19]/g, (c) => String.fromCharCode(c.charCodeAt(0) - 0xFEE0))
    .replace(/[\uFF0E\u3002]/g, '.')
    .replace(/[\uFF0D\u2212]/g, '-')
    .replace(/\uFF0B/g, '+')
    .replace(/[,\uFF0C]/g, '');
}

const CN_DIGIT = {
  '零': 0, '〇': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4,
  '五': 5, '六': 6, '七': 7, '八': 8, '九': 9,
};
const CN_CHARS = /[零〇一二三四五六七八九十百千两点]/;

/** 中文整数 → 数值(支持 0-9999:千/百/十进位与「十六」「二十」形态);解析失败返回 null。 */
function cnIntToNumber(text) {
  if (!text) return null;
  let rest = text, total = 0, seen = false;
  for (const [ch, mul] of [['千', 1000], ['百', 100], ['十', 10]]) {
    const i = rest.indexOf(ch);
    if (i < 0) continue;
    const head = rest.slice(0, i);
    const d = head === '' ? 1 : CN_DIGIT[head];   // 「十六」的「十」= 1×10
    if (d === undefined || head.length > 1) return null;
    total += d * mul;
    seen = true;
    rest = rest.slice(i + 1);
    if (rest.startsWith('零')) rest = rest.slice(1);   // 「一百零五」
  }
  if (rest) {
    if (rest.length > 1) return null;   // 个位只允许单字
    const d = CN_DIGIT[rest];
    if (d === undefined) return null;
    total += d;
    seen = true;
  }
  return seen ? total : null;
}

/** 数字文本归一化:去空白、全角转半角、中文数字转阿拉伯;返回 ASCII 文本(不保证可解析)。 */
export function normalizeNumberText(raw) {
  let s = toHalfWidth(String(raw ?? '').replace(/[\s\u3000]+/g, ''));
  if (!CN_CHARS.test(s)) return s;
  // 含中文数字:按「点」拆整数/小数段,各段独立转换;混杂形态转不动就原样返回
  const neg = s.startsWith('负') ? (s = s.slice(1), true) : false;
  const [intPart, decPart, extra] = s.split('点');
  if (extra !== undefined) return s;
  const intNum = intPart === '' ? 0 : cnIntToNumber(intPart);
  if (intNum === null) return s;
  let out = String(intNum);
  if (decPart !== undefined) {
    let dec = '';
    for (const ch of decPart) {
      if (/\d/.test(ch)) dec += ch;
      else if (CN_DIGIT[ch] !== undefined) dec += String(CN_DIGIT[ch]);
      else return s;
    }
    if (dec === '') return s;
    out += `.${dec}`;
  }
  return neg ? `-${out}` : out;
}

/* ============================================================
   三、校验器(纯函数,check 脚本直接测)
   ============================================================ */

/** 取某步某参数的规格;未知返回 null(零干扰的判断依据)。 */
export function specOf(stepId, key) {
  return KNOWLEDGE[Number(stepId)]?.params?.[key] || null;
}

const BOOL_TEXT = { 'true': 'true', '1': 'true', '是': 'true', 'false': 'false', '0': 'false', '否': 'false' };

/**
 * 校验一个原始输入文本。
 * 返回 { ok, msg, value, text }:
 *   ok    是否通过
 *   msg   错误文案(通过时 null)
 *   value 解析后的值(数字/布尔文本/字符串;空值为 '')
 *   text  规范化回写文本(失焦时写回输入框,如全角「0.25」→「0.25」)
 */
export function validateValue(spec, raw) {
  const fail = (msg) => ({ ok: false, msg, value: null, text: String(raw ?? '') });
  if (!spec) return { ok: true, msg: null, value: raw, text: String(raw ?? '') };
  const s = String(raw ?? '').trim();

  if (s === '') {
    if (spec.required) return fail('不能为空');
    return { ok: true, msg: null, value: '', text: '' };
  }

  if (spec.type === 'list') return { ok: true, msg: null, value: s, text: s };   // 只读展示,不校验

  if (spec.type === 'bool') {
    const t = BOOL_TEXT[s.toLowerCase()];
    if (t === undefined) return fail('须为 true 或 false');
    return { ok: true, msg: null, value: t, text: t };
  }

  if (spec.type === 'str') {
    if (spec.enum && !spec.enum.includes(s)) return fail(`须为 ${spec.enum.filter(Boolean).join(' / ')} 之一`);
    if (spec.pattern && !spec.pattern.test(s)) return fail(spec.patternHint || '格式不正确');
    return { ok: true, msg: null, value: s, text: s };
  }

  // number / int
  const norm = normalizeNumberText(s);
  const n = Number(norm);
  if (!Number.isFinite(n)) return fail('必须是数字(支持全角与中文数字,如 0.25 / 零点五)');
  if (spec.type === 'int' && !Number.isInteger(n)) return fail('必须是整数');
  if (spec.enum && !spec.enum.includes(n)) return fail(`须为 ${spec.enum.join(' / ')} 之一`);
  const u = spec.unit ? ` ${spec.unit}` : '';
  if (spec.min !== null && n < spec.min) return fail(`不能小于 ${spec.min}${u}`);
  if (spec.max !== null && n > spec.max) return fail(`不能大于 ${spec.max}${u}`);
  return { ok: true, msg: null, value: n, text: String(n) };
}

/** 预设/默认值 → 输入框文本(布尔转 'true'/'false',数字转十进制文本)。 */
function canonicalText(spec, v) {
  if (spec.type === 'bool') return v === true || v === 'true' ? 'true' : 'false';
  if (Array.isArray(v)) return v.join(', ');
  return String(v);
}

/* ============================================================
   四、预设匹配与填充计划(纯函数)
   ============================================================ */

/** 两个值在规格语义下是否相等(数字按数值比,布尔按规范文本比,其余按字符串比)。 */
function equalsBySpec(spec, rawText, presetVal) {
  const res = validateValue(spec, rawText);
  if (!res.ok) return false;
  if (spec.type === 'number' || spec.type === 'int') return res.value === Number(presetVal);
  if (spec.type === 'bool') return res.value === canonicalText(spec, presetVal);
  return String(res.value) === String(presetVal);
}

/**
 * 当前表单值命中的档位列表(高亮逻辑)。
 * values = { 参数名: 输入框文本 }。档位命中要求:其全部参数都在表单里且逐一相等
 * (只比对档位声明的参数 —— pairs 这类数据决定的参数不参与档位判定)。
 * 多档同值时全部命中(如第 2 步「精细 = 标准」),诚实呈现不裁剪。
 */
export function matchedTiers(stepId, values) {
  const entry = KNOWLEDGE[Number(stepId)];
  if (!entry?.presets) return [];
  const out = [];
  for (const tier of TIER_IDS) {
    const preset = entry.presets[tier];
    if (!preset) continue;
    const pairs = Object.entries(preset.values);
    const hit = pairs.length > 0 && pairs.every(([k, v]) => {
      if (!Object.prototype.hasOwnProperty.call(values, k)) return false;
      return equalsBySpec(entry.params[k], values[k], v);
    });
    if (hit) out.push(tier);
  }
  return out;
}

/** 档位对当前表单是否可用:其声明的全部参数键都渲染在表单里。 */
export function tierApplicable(stepId, tier, presentKeys) {
  const preset = KNOWLEDGE[Number(stepId)]?.presets?.[tier];
  if (!preset) return false;
  const keys = Object.keys(preset.values);
  return keys.length > 0 && keys.every((k) => presentKeys.includes(k));
}

/** 档位填充计划:[{ key, text }],只覆盖表单里实际存在的输入框。 */
export function fillPlan(stepId, tier, presentKeys) {
  const entry = KNOWLEDGE[Number(stepId)];
  const preset = entry?.presets?.[tier];
  if (!entry || !preset) return [];
  return Object.entries(preset.values)
    .filter(([k]) => presentKeys.includes(k))
    .map(([k, v]) => ({ key: k, text: canonicalText(entry.params[k], v) }));
}

/** 恢复默认计划:表单里每个已知参数回填知识表默认值(list 只读跳过)。 */
export function defaultsPlan(stepId, presentKeys) {
  const entry = KNOWLEDGE[Number(stepId)];
  if (!entry) return [];
  return Object.entries(entry.params)
    .filter(([k, spec]) => presentKeys.includes(k) && spec.type !== 'list')
    .map(([k, spec]) => ({ key: k, text: canonicalText(spec, spec.default) }));
}

/* ============================================================
   五、知识表完整性自检(check 脚本断言其为空数组)
   ============================================================ */
const SPEC_KEYS = ['type', 'min', 'max', 'step', 'unit', 'enum', 'pattern',
                   'patternHint', 'required', 'default', 'help'];
const TYPES = ['number', 'int', 'str', 'bool', 'list'];

export function knowledgeProblems() {
  const bad = [];
  for (let id = 1; id <= 11; id++) {
    const entry = KNOWLEDGE[id];
    if (!entry) { bad.push(`步骤 ${id} 缺失`); continue; }
    if (!entry.name) bad.push(`步骤 ${id} 缺 name`);
    for (const [k, spec] of Object.entries(entry.params || {})) {
      const at = `步骤 ${id} 参数 ${k}`;
      for (const f of SPEC_KEYS) {
        if (!(f in spec) || spec[f] === undefined) bad.push(`${at} 字段 ${f} 未显式声明`);
      }
      if (!TYPES.includes(spec.type)) bad.push(`${at} type 非法:${spec.type}`);
      if (typeof spec.help !== 'string' || !spec.help) bad.push(`${at} 缺 help 帮助文案`);
      if (spec.unit !== null && typeof spec.unit !== 'string') bad.push(`${at} unit 须为字符串或 null`);
      if (spec.min !== null && typeof spec.min !== 'number') bad.push(`${at} min 须为数字或 null`);
      if (spec.max !== null && typeof spec.max !== 'number') bad.push(`${at} max 须为数字或 null`);
      if (spec.min !== null && spec.max !== null && spec.min > spec.max) bad.push(`${at} min > max`);
      if (spec.step !== null && !(typeof spec.step === 'number' && spec.step > 0)) bad.push(`${at} step 须为正数或 null`);
      if (spec.enum !== null && (!Array.isArray(spec.enum) || !spec.enum.length)) bad.push(`${at} enum 须为非空数组或 null`);
      if (typeof spec.required !== 'boolean') bad.push(`${at} required 须为布尔`);
      if (!('default' in spec)) bad.push(`${at} 缺 default`);
      // 默认值必须能通过自身校验(list 除外;非必填的空默认值合法)
      if (spec.type !== 'list') {
        const res = validateValue(spec, canonicalText(spec, spec.default));
        if (!res.ok) bad.push(`${at} 默认值 ${JSON.stringify(spec.default)} 不过自身校验:${res.msg}`);
      }
    }
    // 档位:三档齐全且值全部通过校验;或显式 null + 说明
    if (entry.presets === null) {
      if (!entry.presetsNote) bad.push(`步骤 ${id} presets 为 null 但缺 presetsNote 说明`);
    } else {
      for (const tier of TIER_IDS) {
        const preset = entry.presets?.[tier];
        if (!preset) { bad.push(`步骤 ${id} 缺 ${tier} 档`); continue; }
        if (preset.label !== TIER_LABELS[tier]) bad.push(`步骤 ${id} ${tier} 档 label 不符`);
        if (!preset.desc) bad.push(`步骤 ${id} ${tier} 档缺 desc`);
        if (!Object.keys(preset.values).length) bad.push(`步骤 ${id} ${tier} 档 values 为空`);
        for (const [k, v] of Object.entries(preset.values)) {
          const spec = entry.params[k];
          if (!spec) { bad.push(`步骤 ${id} ${tier} 档引用未声明参数 ${k}`); continue; }
          const res = validateValue(spec, canonicalText(spec, v));
          if (!res.ok) bad.push(`步骤 ${id} ${tier} 档 ${k}=${JSON.stringify(v)} 不过校验:${res.msg}`);
        }
      }
    }
  }
  return bad;
}

/* ============================================================
   六、DOM 增强层
   —— 以下代码只在浏览器(或提供 DOM stub 的测试)里生效。
   ============================================================ */

const TIP_ID = 'pz-tip';
let tipEl = null;        // 帮助气泡单例
let tipAnchor = null;    // 当前锚点(ⓘ 按钮)
const pendingMap = new WeakMap();   // .pdetail → Map(input → 填充前文本)

function el(tag, attrs, text) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === 'class') node.className = v;
    else node.setAttribute(k, String(v));
  }
  if (text != null) node.textContent = text;
  return node;
}

/** 输入框 → { detail, stepId, key, spec };未知参数或不在参数区 → null(零干扰)。 */
function paramCtx(target) {
  if (!target || !target.dataset || !target.dataset.k) return null;
  let n = target;
  while (n && !(n.dataset && n.dataset.pzStep)) n = n.parentNode;
  if (!n) return null;
  const stepId = Number(n.dataset.pzStep);
  const spec = specOf(stepId, target.dataset.k);
  if (!spec) return null;
  return { detail: n, stepId, key: target.dataset.k, spec };
}

/** 在字段旁写入/清除内联错误文案(复用 dock 渲染的 .ferr 槽位,没有则补建)。 */
function paintError(input, msg) {
  input.setAttribute('aria-invalid', String(!!msg));
  const field = input.parentNode;                    // .field
  const wrap = field && field.parentNode;            // dock 里包着 field + p.ferr 的 div
  if (!wrap) return;
  let err = wrap.querySelector('.ferr');
  if (!err) {
    err = el('p', { class: 'ferr hidden' });
    wrap.appendChild(err);
  }
  err.textContent = msg || '';
  err.classList.toggle('hidden', !msg);
}

/** 读取参数区当前值:{ 参数名: 输入框文本 }(只收已知 data-k 输入框)。 */
export function readParamValues(detail) {
  const values = {};
  for (const inp of detail.querySelectorAll('input')) {
    if (inp.dataset && inp.dataset.k) values[inp.dataset.k] = inp.value;
  }
  return values;
}

function inputsOf(detail) {
  return detail.querySelectorAll('input').filter
    ? detail.querySelectorAll('input').filter((i) => i.dataset && i.dataset.k)          // 测试 stub 返回数组
    : [...detail.querySelectorAll('input')].filter((i) => i.dataset && i.dataset.k);   // 浏览器 NodeList
}

/** 档位高亮 + 确认按钮禁用态刷新。 */
function refreshBar(detail) {
  const bar = detail.querySelector('.pz-bar');
  if (!bar) return;
  const stepId = Number(detail.dataset.pzStep);
  const matched = matchedTiers(stepId, readParamValues(detail));
  for (const btn of bar.querySelectorAll('.pz-tier')) {
    const on = matched.includes(btn.dataset.tier);
    btn.setAttribute('aria-pressed', String(on));
  }
  const confirm = bar.querySelector('.pz-apply');
  if (confirm) {
    const invalid = inputsOf(detail).some((i) => i.getAttribute('aria-invalid') === 'true');
    confirm.disabled = invalid;
    confirm.setAttribute('aria-disabled', String(invalid));
  }
}

/** 填充(不提交):写入输入框 + 记录快照 + 展示确认区。 */
function fillInputs(detail, plan, describe) {
  if (!plan.length) return;
  let snap = pendingMap.get(detail);
  if (!snap) { snap = new Map(); pendingMap.set(detail, snap); }
  const byKey = {};
  for (const inp of inputsOf(detail)) byKey[inp.dataset.k] = inp;
  let filled = 0;
  for (const { key, text } of plan) {
    const inp = byKey[key];
    if (!inp || inp.value === text) continue;
    if (!snap.has(inp)) snap.set(inp, inp.value);
    inp.value = text;
    inp.classList.add('pz-pending');
    inp.dispatchEvent(new Event('input', { bubbles: true }));
    filled++;
  }
  const zone = detail.querySelector('.pz-confirm');
  const note = detail.querySelector('.pz-note');
  if (zone && note) {
    if (filled || snap.size) {
      note.textContent = `已填充${describe} ${filled} 项,未提交`;
      zone.hidden = false;
    } else {
      note.textContent = `${describe}与当前值一致,无需变更`;
      zone.hidden = false;
      setTimeout(() => { if (!snap.size) zone.hidden = true; }, 2600);
    }
  }
  refreshBar(detail);
}

/** 清掉待确认状态(恢复或提交后)。 */
function clearPending(detail, restore) {
  const snap = pendingMap.get(detail);
  if (snap) {
    for (const [inp, old] of snap) {
      if (restore) {
        inp.value = old;
        inp.dispatchEvent(new Event('input', { bubbles: true }));
      }
      inp.classList.remove('pz-pending');
    }
    snap.clear();
  }
  const zone = detail.querySelector('.pz-confirm');
  if (zone) zone.hidden = true;
  refreshBar(detail);
}

/**
 * 提交:对每个待确认输入框派发 blur —— 走 dock 自己的 commit 路径
 * (validateParam + changeParams → SET_PARAMS),不绕过任何既有校验。
 * 首次提交会触发面板重渲染,后续输入框虽已脱离文档,监听器与闭包仍有效。
 */
function applyPending(detail) {
  const snap = pendingMap.get(detail);
  const targets = snap && snap.size ? [...snap.keys()] : inputsOf(detail);
  for (const inp of targets) {
    if (inp.classList) inp.classList.remove('pz-pending');
    inp.dispatchEvent(new Event('blur'));
  }
  // 若全部值与提交前一致(dock 短路,不重渲染),手工收掉确认区
  if (detail.isConnected) clearPending(detail, false);
}

/* ---------------- 帮助气泡 ---------------- */

function ensureTip() {
  if (tipEl) return tipEl;
  tipEl = el('div', { id: TIP_ID, class: 'pz-tip', role: 'tooltip' });
  tipEl.hidden = true;
  document.body.appendChild(tipEl);
  return tipEl;
}

function tipContent(key, spec) {
  const tip = ensureTip();
  tip.textContent = '';
  tip.appendChild(el('div', { class: 'pz-tip-title' }, spec.unit ? `${key} · ${spec.unit}` : key));
  const parts = [];
  if (spec.min !== null || spec.max !== null) {
    parts.push(`范围 ${spec.min ?? '−∞'} – ${spec.max ?? '+∞'}`);
  }
  if (spec.enum) parts.push(`可选 ${spec.enum.filter(Boolean).join(' / ')}`);
  if (spec.step !== null) parts.push(`步进 ${spec.step}`);
  parts.push(`默认 ${canonicalText(spec, spec.default) === '' ? '(空)' : canonicalText(spec, spec.default)}`);
  if (!spec.required) parts.push('可留空');
  tip.appendChild(el('div', { class: 'pz-tip-range' }, parts.join(' · ')));
  tip.appendChild(el('p', { class: 'pz-tip-help' }, spec.help));
  return tip;
}

function showTip(anchor, key, spec) {
  const tip = tipContent(key, spec);
  tip.hidden = false;
  tipAnchor = anchor;
  anchor.setAttribute('aria-describedby', TIP_ID);
  // fixed 定位:锚点下方,视口右缘内收
  if (typeof anchor.getBoundingClientRect === 'function') {
    const r = anchor.getBoundingClientRect();
    const vw = (typeof window !== 'undefined' && window.innerWidth) || 1200;
    const w = tip.offsetWidth || 280;
    tip.style.left = `${Math.max(8, Math.min(r.left - 8, vw - w - 12))}px`;
    tip.style.top = `${r.bottom + 6}px`;
  }
}

function hideTip() {
  if (tipEl) tipEl.hidden = true;
  if (tipAnchor) tipAnchor.removeAttribute('aria-describedby');
  tipAnchor = null;
}

/* ---------------- 参数区增强(幂等) ---------------- */

/**
 * 增强一个 .pdetail:插入预设条、给已知参数补 ⓘ 图标。
 * 步骤号从标题「第 N 步」解析 —— 不 import app 模块,保持零耦合。
 */
export function enhance(detail) {
  if (!detail || (detail.dataset && detail.dataset.pzDone)) return false;
  const hd = detail.querySelector('.hd');
  const m = /第\s*(\d+)\s*步/.exec(hd ? hd.textContent : '');
  if (!m) return false;
  const stepId = Number(m[1]);
  detail.dataset.pzDone = '1';
  const entry = KNOWLEDGE[stepId];
  if (!entry) return false;              // 未知步骤:整块零干扰
  detail.dataset.pzStep = String(m[1]);

  const pform = detail.querySelector('.pform');
  if (!pform) return false;
  const presentKeys = inputsOf(detail).map((i) => i.dataset.k);

  // ---- 预设条 ----
  const bar = el('div', { class: 'pz-bar', role: 'group', 'aria-label': '参数预设' });
  bar.appendChild(el('span', { class: 'pz-cap' }, '预设'));
  if (entry.presets) {
    for (const tier of TIER_IDS) {
      const preset = entry.presets[tier];
      const btn = el('button', {
        type: 'button', class: 'pz-btn pz-tier', 'aria-pressed': 'false',
        title: preset.desc,
      }, preset.label);
      btn.dataset.tier = tier;
      if (!tierApplicable(stepId, tier, presentKeys)) {
        btn.disabled = true;
        btn.setAttribute('title', `${preset.desc}(当前表单没有该档可调参数)`);
      } else {
        btn.addEventListener('click', () => {
          fillInputs(detail, fillPlan(stepId, tier, presentKeys), `「${preset.label}」预设`);
        });
      }
      bar.appendChild(btn);
    }
  } else {
    bar.appendChild(el('span', { class: 'pz-none' }, entry.presetsNote));
  }
  const resetBtn = el('button', {
    type: 'button', class: 'pz-btn pz-reset', title: '回填 registry 声明的默认参数(不自动提交)',
  }, '恢复默认');
  resetBtn.addEventListener('click', () => {
    fillInputs(detail, defaultsPlan(stepId, presentKeys), '默认值');
  });
  bar.appendChild(resetBtn);

  // 确认区:填充后出现;提交仍走 dock 的 blur-commit 路径
  const zone = el('div', { class: 'pz-confirm', role: 'status' });
  zone.hidden = true;
  zone.appendChild(el('span', { class: 'pz-note' }, ''));
  const applyBtn = el('button', { type: 'button', class: 'btn btn-pri btn-sm pz-apply' }, '确认应用');
  applyBtn.addEventListener('click', () => applyPending(detail));
  const cancelBtn = el('button', { type: 'button', class: 'btn btn-gho btn-sm pz-cancel' }, '还原');
  cancelBtn.addEventListener('click', () => clearPending(detail, true));
  zone.appendChild(applyBtn);
  zone.appendChild(cancelBtn);
  bar.appendChild(zone);

  pform.parentNode.insertBefore(bar, pform);

  // ---- 每个已知参数补 ⓘ 帮助图标(未知参数不加,零干扰) ----
  for (const inp of inputsOf(detail)) {
    const spec = specOf(stepId, inp.dataset.k);
    if (!spec) continue;
    const label = inp.parentNode && inp.parentNode.querySelector('label');
    if (!label) continue;
    const info = el('button', {
      type: 'button', class: 'pz-info',
      'aria-label': `${inp.dataset.k} 参数说明`,
    }, 'ⓘ');
    info.addEventListener('mouseenter', () => showTip(info, inp.dataset.k, spec));
    info.addEventListener('mouseleave', () => hideTip());
    info.addEventListener('focus', () => showTip(info, inp.dataset.k, spec));
    info.addEventListener('blur', () => hideTip());
    label.appendChild(info);
  }

  refreshBar(detail);
  return true;
}

function enhanceAll() {
  if (tipAnchor && !tipAnchor.isConnected) hideTip();   // 面板重渲染后收掉孤儿气泡
  for (const d of document.querySelectorAll('.pdetail')) enhance(d);
}

/* ---------------- 事件委托(document 捕获阶段) ---------------- */

/** input:接管已知参数的即时校验(截停 dock 的重复校验,单一错误出口)。 */
function onInputCapture(e) {
  const ctx = paramCtx(e.target);
  if (!ctx) return;                       // 未知参数:原样放行
  e.stopPropagation();
  const res = validateValue(ctx.spec, e.target.value);
  paintError(e.target, res.ok ? null : res.msg);
  refreshBar(ctx.detail);
}

/** blur:非法值截停提交(SET_PARAMS 出口拦截);合法值规范化后放行给 dock commit。 */
function onBlurCapture(e) {
  const ctx = paramCtx(e.target);
  if (!ctx) return;
  const res = validateValue(ctx.spec, e.target.value);
  if (!res.ok) {
    e.stopPropagation();                  // dock 的 blur-commit 不会执行
    paintError(e.target, res.msg);
    refreshBar(ctx.detail);               // 非法态同步禁用确认按钮
    return;
  }
  if (e.target.value !== res.text) e.target.value = res.text;   // 全角/中文数字 → 规范文本再提交
  paintError(e.target, null);
  refreshBar(ctx.detail);                 // 错误清除后恢复确认按钮与档位高亮
}

/** keydown:Enter 同 blur 语义;Esc 关气泡;Esc 在输入框由 dock 复位后补清错误态。 */
function onKeydownCapture(e) {
  if (e.key === 'Escape' && tipAnchor) {
    const onAnchor = e.target === tipAnchor;
    hideTip();
    if (onAnchor) { e.stopPropagation(); return; }   // 焦点在 ⓘ 上:Esc 只关气泡
  }
  const ctx = paramCtx(e.target);
  if (!ctx) return;
  if (e.key === 'Enter') {
    const res = validateValue(ctx.spec, e.target.value);
    if (!res.ok) {
      e.preventDefault();
      e.stopPropagation();                // dock 的 Enter-commit 不会执行
      paintError(e.target, res.msg);
      refreshBar(ctx.detail);
      return;
    }
    if (e.target.value !== res.text) e.target.value = res.text;
    paintError(e.target, null);
    refreshBar(ctx.detail);
  } else if (e.key === 'Escape') {
    // dock 会把值复位为渲染时原值并 blur;下一拍清掉错误态与高亮
    const { detail } = ctx;
    setTimeout(() => { paintError(e.target, null); refreshBar(detail); }, 0);
  }
}

let bound = false;
export function bindDelegation(doc) {
  if (bound) return;
  bound = true;
  doc.addEventListener('input', onInputCapture, true);
  doc.addEventListener('blur', onBlurCapture, true);
  doc.addEventListener('keydown', onKeydownCapture, true);
}

/* ---------------- 自初始化 ---------------- */

function init() {
  bindDelegation(document);
  enhanceAll();
  // 面板每次重渲染(replaceChildren)都会换新节点,观察后台重新增强;
  // 微任务级去抖,一帧多次变更只增强一次
  if (typeof MutationObserver !== 'undefined') {
    let queued = false;
    const mo = new MutationObserver(() => {
      if (queued) return;
      queued = true;
      queueMicrotask(() => { queued = false; enhanceAll(); });
    });
    mo.observe(document.body, { childList: true, subtree: true });
  }
}

if (typeof document !== 'undefined') {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init, { once: true });
  } else {
    init();
  }
}
