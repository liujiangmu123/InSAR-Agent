# InSAR-Agent 设计定稿

> 本文档是十二轮需求讨论 + 四轮代码审计的收敛结果。所有判断附证据来源，避免重复讨论。
> 最后更新：2026-08-12

---

## 1. 产品定义

自然语言驱动的 InSAR 数据处理 Agent，编排 ISCE2 / MintPy / PyStamps 等开源工具，
产出形变结果 + 期刊级图表 + 可复现记录 + 论文方法章节草稿。

**交付目标**（用户明确）：数据处理能力 → 论文图表与报告 → SCI 级论文。

**应用场景**：冻土形变（青藏高原为重点），架构可扩展到滑坡、沉降、地震。

**目标用户**：双模式 —— 专家模式（手动指定每步方法）+ 向导模式（Agent 自动决策）。

### 1.1 一句话 novelty

把科学工作流引擎的可复现性契约（参数级失效判定、完整 provenance、步骤级断点续跑）
下沉到 LLM Agent 的工具层，使自然语言驱动的 InSAR 处理不牺牲确定性与可审计性；
并通过打通 ISCE2→PyStamps 缺失桥梁，首次实现同源数据的 PS/SBAS 双链交叉验证作为质量门。

### 1.2 贡献点优先级

| 优先级 | 贡献 | 可验证性 | 差异化 | 状态 |
|---|---|---|---|---|
| 主 | 参数级 stale detection | 高 | 高 | 竞品零实现，agentic-swmm 明确写为 next-milestone |
| 主 | ISCE2→PyStamps 桥 + 双链交叉验证 | 高 | 高 | 无人做过 |
| 辅 | 完整 provenance | 高 | 中 | 竞品零实现 |
| 辅 | 步骤级断点续跑 | 中 | 中 | 竞品粒度粗到不可用 |
| 案例 | 冻土领域知识内化 | 低 | 中 | 数据待获取，作为应用案例而非创新点 |

**已降级**：「纯本地 LLM / 数据不出域」不再作为 novelty —— agentic-swmm 已支持 10 条
provider 路由（含 Ollama）。若要保留，必须配「本地模型 vs 云端模型决策成功率对比」量化数据。

---

## 2. 核心机制：kind 与 layout 分离

这是整个可插拔设计的支点。之前所有讨论把「格式不通用」当成一个整体问题，
实际上它是两个正交问题。

```
kind   （语义类型）= 这是什么数据    IFG_UNWRAPPED / COHERENCE / TIMESERIES / VELOCITY
layout （承载布局）= 谁家的格式      isce2 / snap / hyp3 / mintpy_h5 / pystamps

Capability（科学步骤）：改变 kind，不改 layout
    解缠：IFG_WRAPPED@isce2 → IFG_UNWRAPPED@isce2

Bridge（格式桥）：改变 layout，不改 kind
    prep_isce：IFG_UNWRAPPED@isce2 → IFG_UNWRAPPED@mintpy_h5
```

分开之后，「打通所有桥」从模糊的架构焦虑变成**填满一张 layout × layout 转换表**
——有限、可枚举、可验证。

---

## 3. 步骤 × 方法矩阵（可插拔核心）

每步列出候选方法。手动模式由用户选，自动模式由规则引擎收窄候选后 LLM 选。

| # | 步骤 | 候选方法 | 引擎 | 适用条件 |
|---|---|---|---|---|
| 1 | 数据获取 | `asf_search_slc` | ASF API | 要原始 SLC |
| | | `hyp3_submit` | 云端 | 跳过 2-6 步 |
| | | `local_import` | — | 已有数据 |
| 2 | 辅助数据 | `s1_orbit_poeorb` | ESA/ASF | S1 必需 |
| | | `dem_copernicus` / `dem_srtm` / `dem_local` | AWS/NASA | — |
| 3 | 配准 | `isce2_tops_geom_esd` | ISCE2 | S1 IW（几何+ESD） |
| | | `isce2_stripmap_xcorr` | ISCE2 | ALOS-2/TSX 条带 |
| | | `snap_backgeocoding` | SNAP | 走 SNAP 链 |
| 4 | 干涉 | `isce2_ifg_multilook` | ISCE2 | 可调多视比 |
| | | `snap_interferogram` | SNAP | — |
| 5 | 滤波 | `goldstein` / `boxcar` / `none` | ISCE2 | 低相干区建议开 |
| 6 | 解缠 | `snaphu_mcf` / `snaphu_smooth` / `icu` | SNAPHU | MCF 最常用 |
| 7 | 时序反演 | `mintpy_sbas` | MintPy | 低相干面状区（冻土） |
| | | `pystamps_ps` | PyStamps | 高相干点状区（公路） |
| 8 | 误差校正 | `tropo_era5_pyaps` | PyAPS | ERA5 已备 |
| | | `tropo_gacos` / `tropo_height_corr` | GACOS/经验 | 无气象数据时降级 |
| | | `solid_earth_tides` | PySolid | 大范围必需 |
| | | `dem_error` / `ramp_removal` | MintPy | — |
| 9 | 形变模型 | `linear` | — | 稳定形变 |
| | | `poly_periodic(1, [1,0.5])` | — | **冻土季节冻融** |
| | | `step(date)` | — | 地震同震（Ridgecrest） |
| | | `exponential` | — | 震后/矿区衰减 |
| 10 | 出图导出 | `mintpy_geocode` / `gdal_warp` / `figure_journal` | — | — |
| 11 | 质检 | `crossval_ps_sbas` | 自建 | **双链交叉验证** |
| | | `loop_closure` / `coherence_mask` | — | — |

---

## 4. 桥梁地图

```
                MintPy(SBAS)                    PyStamps(PS)
                     ↑                               ↑
      ┌──────────────┼──────────────┐      ┌─────────┴─────────┐
  prep_isce      prep_hyp3      prep_snap   ??? 缺失         SNAP导出
      ↑              ↑              ↑          ↑                ↑
    ISCE2          HyP3           SNAP      ISCE2             SNAP
   官方现成        官方现成        官方现成   要建(论文贡献)      官方现成
```

### 4.1 ISCE2 → PyStamps 映射表

已从 PyStamps 源码核实（`src/mtprep.py`、`src/prep/*.py`）。

| PyStamps 期望 | ISCE2 来源 | 转换动作 | 难度 |
|---|---|---|---|
| `rslc/YYYYMMDD.rslc` | `merged/SLC/*/*.slc.full`（cfloat32 LE） | 字节序 LE→BE | 低 |
| `geo/{master}.lon/.lat` | `geom_reference/lon.rdr.full`（float64 LE） | float64→32 + LE→BE | 低 |
| `geo/elevation_dem.rdc` | `geom_reference/hgt.rdr.full` | 同上，文件名硬编码 | 低 |
| `diff0/M_S.diff` | `merged/interferograms/*/filt_fine.int` | LE→BE，符号约定需核对 | 低-中 |
| `rslc/*.par` | `master.xml` + `*.vrt` + orbit | 合成 GAMMA 风格文本 | 中 |
| `diff0/M_S.base` | ISCE2 baseline | 反算 TCN 基线 C/N 分量 | 中-高 |
| `dem/*` | — | **完全可省**（src/ 内零引用） | 无 |

### 4.2 桥的三个风险点

1. **par 几何字段必须自洽**（最大风险）
   `near_range_slc` / `sar_to_earth_center` / `earth_radius_below_sensor` 直接进
   `look = arccos((se²+rg²-re²)/(2·se·rg))`，错了 bperp 和高程误差估计全崩。
   不能直接喂 ISCE2 的 `los.rdr`。

2. **TCN 基线反算**
   ISCE2 给 perp/parallel baseline，PyStamps 的 `.base` 要 TCN 的 C、N 分量。

3. **全程 big-endian 硬编码**
   `calamp.py:46-51`、`pscpatch.py:96-100` 无条件 `.byteswap()`，桥必须输出 BE。

### 4.3 PyStamps 自身的脆弱点（桥要绕开）

| 问题 | 位置 | 影响 |
|---|---|---|
| 绝对字符偏移切日期 `[nb-22:nb-14]` | `step_1_ps_loadgm.py:37-45` | 路径必须严格以 `YYYYMMDD_YYYYMMDD.diff` 结尾 |
| 反选式文件发现 | `mtprep.py:62` | 目录里有 `.vrt/.hdr/.aux` 残留会被误当数据 |
| `int(range_pixel_spacing)` | `step_1_ps_loadgm.py:67` | 像元间距被取整，2.33m → 2 |
| 缺 `.base` 静默丢干涉图 | `mtprep.py:159` | 无输入校验 |
| `insar_processor` 恒为 snap | `parminit.py:35/93` | 第 93 行是死代码，无 ISCE 分支 |

**算法参数**：60+ 个硬编码在 `prep/parminit.py:28 ps_parms_init()`，用户无法从
`input.json` 覆盖。关键项：`max_topo_err=20`、`clap_win=32`、`select_method='DENSITY'`、
`weed_time_win=730`、`unwrap_method='3D_FULL'`、`scla_method='L2'`。

### 4.4 输出契约

`ground_displacement.csv`（`stamps.py:64`）：
```
lng, lat, point_id, coherence, avg_deformation_velocity, YYYY-MM-DD, ...
```
- `avg_deformation_velocity` 单位 mm/yr
- 日期列为 mm 位移，已减 SCLA + APS + deramp + 参考点均值
- 日期头用 `datetime.fromtimestamp()`，**本地时区，跨时区可能偏一天**

---

## 5. 分层架构

```
┌───────────────────────────────────────────────────────────┐
│ UI  步骤表工作台 + 双 SSE（对话/长任务分离）                 │
├───────────────────────────────────────────────────────────┤
│ API FastAPI，人和 LLM 共用同一套接口                        │
│     task_id 唯一句柄，禁止 LLM 碰路径                       │
├───────────────────────────────────────────────────────────┤
│ Brain 云端 API（provider 表可切本地，留对比实验位）          │
│       只做候选集内选择题，绝不驱动副作用                     │
│       缓存锚定：只传 index 不传名字                         │
├───────────────────────────────────────────────────────────┤
│ Core  SQLite 单一真相源                                    │
│       参数指纹 → stale 传播                                │
│       MintPy 拆步（--dostep/--start）                      │
│       长任务在独立进程，不在 HTTP 线程                       │
├───────────────────────────────────────────────────────────┤
│ Audit 指标来源契约 YAML + 守护测试                          │
│       审计包 try/finally，失败也触发                        │
│       六级证据阶梯                                         │
├───────────────────────────────────────────────────────────┤
│ Registry  kinds/capabilities/bridges/scenarios（纯数据）    │
│ Runtime   WSL执行器 + Jinja2渲染 + provenance账本           │
├───────────────────────────────────────────────────────────┤
│ Engines   ISCE2 / MintPy / PyStamps / PyAPS + 桥           │
└───────────────────────────────────────────────────────────┘
```

### 5.1 目录结构

```
insar_agent/
├── registry/        纯数据·零逻辑
│   ├── kinds.py          DataKind（kind × layout × crs）
│   ├── capabilities.py   步骤候选方法声明
│   ├── bridges.py        桥声明
│   └── scenarios.py      场景规则：冻土→周期模型，地震→阶跃模型
├── planner/         纯函数·可单测·不碰磁盘网络
│   ├── graph.py          由 registry 构建能力图
│   ├── feasibility.py    此刻哪些候选可选
│   ├── score.py          auto 模式打分
│   └── plan.py           输出执行 DAG
├── core/            唯一真相源
│   ├── fingerprint.py    参数指纹计算
│   ├── stale.py          失效传播
│   ├── store.py          SQLite 状态机
│   └── ledger.py         provenance 账本
├── runtime/         唯一有副作用的层
│   ├── probe.py          环境探测
│   ├── render.py         Jinja2 → topsApp.xml / smallbaselineApp.cfg
│   └── executor.py       WSL 子进程 + 日志流 + 线程限制
├── engines/         薄封装·不含决策
│   ├── isce2.py  mintpy.py  pystamps.py  hyp3.py
│   └── bridges/
│       ├── prep_isce.py            调官方
│       └── isce2_to_pystamps.py    我们建的桥
├── audit/           契约与质量门
│   ├── contract.yaml     指标来源契约
│   ├── verify.py         重解析比对
│   └── ladder.py         六级证据阶梯
├── brain/           可完全拔掉
│   ├── provider.py       provider 路由表 + fallback
│   ├── intent.py         自然语言 → 目标 + 约束
│   ├── select.py         候选集内选择（结构化输出）
│   ├── repair.py         日志 → 受限修复建议（上限3次）
│   └── narrate.py        provenance → 方法章节草稿
├── report/          figures.py（期刊级出图）  methods.py
└── api/             app.py（FastAPI）+ static/（工作台）
```

### 5.2 关键架构约束

**把 `brain/` 整层删掉，系统退化成手动可插拔流水线，仍完全可用、结果完全正确。**

这条约束的价值：LLM 不承载正确性。审稿人问「大模型幻觉怎么办」，
答案是它只做建议和排序，所有科学计算走确定性代码，参数经 Schema 校验，配置由模板渲染。

---

## 6. 参数指纹与失效传播（主 novelty）

移植 Snakemake 的五触发器概念，不引入引擎。

```
指纹 = sha256(method + params + upstream_fingerprint + tool_version + input_manifest)

判定逻辑：
  记录存在 且 status==done 且 指纹未变 且 产物文件都存在  → 跳过
  记录存在 且 指纹已变                                    → 标记本步及所有下游为 stale
  否则                                                    → 执行
```

**五个触发器**（Snakemake 9.25.1 的设计）：
1. 输入文件修改时间
2. 输入集合本身变化
3. 规则代码变化
4. **参数变化** ← 对 InSAR 关键
5. **软件环境变化** ← 对 InSAR 关键

第 4、5 条是竞品全都缺失的：改了解缠阈值或换了 DEM，产物时间戳是「新」的，
但语义上已失效，只有参数触发器能抓到。

**为什么不用现成引擎**（实测依据见 §9）：
- Snakemake 要求先有静态 Snakefile 再解 DAG，我们的 DAG 是动态生成的
- 它的瓶颈在文件系统存在性查询，InSAR 中间文件动辄几万个
- Prefect/Dagster 重心是调度与可观测性，面向服务常驻，单机桌面场景配置成本 > 收益

---

## 7. 审计层设计

### 7.1 指标来源契约

借鉴 agentic-swmm，但改进其缺陷（它只有文档 + 硬编码，无机器可读 schema，无守护测试）。

```yaml
# audit/contract.yaml
ps_count:
  preferred: {artifact: ps_plot.h5, field: n_ps}
  fallback:  {artifact: ground_displacement.csv, field: row_count}
  forbidden:
    - {artifact: log, reason: "日志是叙述不是数据"}
  tolerance: 0

mean_velocity:
  preferred: {artifact: ground_displacement.csv, field: avg_deformation_velocity}
  forbidden:
    - {artifact: ps_plot.png, reason: "图像不能作为数值证据"}
  tolerance: 1e-9
```

校验流程：从权威来源重新解析 → 与记录值比对 → 不一致则报警并写入
`source_validation.matches`。禁用来源直接硬 gate。

**改进点**：agentic-swmm 只告警不 gate，我们对禁用来源做硬 gate。

### 7.2 六级证据阶梯

移植 agentic-swmm 的 `agent/memory/soul.md:35-48`，改造成 InSAR 版：

```
runnable    = 处理链跑完了
checked     = QA 指标被解析并复核（相干性/解缠残差/参考点）
audited     = artifact 与 provenance 已记录
calibrated  = 用 GNSS 等观测数据标定
validated   = 独立证据（双链交叉验证/工具间互比）支持结果
publishable = 证据边界清晰到可用于科研传播
```

报告结果时必须声明当前处在哪一级。

### 7.3 证据边界声明（论文写法）

照抄 agentic-swmm 的 "X, not Y" 句式，三列表：路径 / 证明了什么 / Evidence boundary。

原文范例（可直接仿写）：
```
"SWMM execution-layer reproducibility, not agentic workflow reproducibility"
"Agent-side plumbing for data-scarce baseline modeling; not a calibrated or
 validated network. Calibration is next-milestone scope."
"Prior uncertainty smoke, not calibration"
```

三个手法：
1. 把「管道通了」与「结果已标定」在同一张表里强制分开
2. 未做到的标注为 "next-milestone scope" 而非含糊略过
3. 主动说明哪些产物**合理地**不可字节复现（含时间戳、git HEAD）

### 7.4 审计触发时机

`agentic-swmm` 的文档写 "should happen after success, failure, or early stop"，
但代码里被 `if result.return_code == 0` 门控 —— 这是它的缺陷。

**我们必须用 try/finally 真正做到**。原文格言值得保留：
"Partial evidence is still useful evidence."

---

## 8. Brain 层设计（LLM 使用规范）

### 8.1 LLM 的四项职责（严格限定）

1. 自然语言 → 结构化意图（区域、时间范围、分析目标）
2. **在候选集内做选择题**（不是开放规划）
3. 读日志尾部判断失败原因，从预定义修复动作里选
4. provenance → 论文方法章节草稿

**明确不做**：生成命令行、写代码、自由规划多步、碰数据矩阵。

### 8.2 为什么限定为选择题（量化依据）

```
BFCL v4（端到端多轮 agentic 复合评测）：
  Qwen3-14B (FC)  41.03%    ← 端到端多步规划，本地模型不行
  Qwen3-32B       46.78%
  GPT-4.1 (FC)    53.96%

工具选择单步 F1（Docker 2025-06 评测，数据来自二手引用）：
  Qwen3-14B  0.971          ← 单步选择，已贴平前沿
  GPT-4      0.974
```

我们的架构落在 0.97 那一侧，因为规则引擎先收窄候选，LLM 只做最后一跳。

### 8.3 九条工程防护（来自真实踩坑记录）

1. 结构化输出走 token 级硬约束（GBNF/JSON Schema），不用 JSON mode
   依据：JSONSchemaBench（EPFL+Microsoft，10K schema）—— 约束解码快 50%，
   下游准确率提升最多 4%
2. 枚举全收成 Literal，**但要在代码层再做值域校验**
   原因：约束保证结构不保证语义，`{"temperature": 999}` 是合法 JSON
3. **绝不让模型串多步** —— 每次只问一个决策
   依据：社区 issue 记录 Ollama 7b/14b/32b 一律「第二个工具永不执行」
4. 工具名/参数白名单校验 + 结构化错误回灌；工具数控制在 5-10 个
5. 硬性 max_steps + 空响应/自我重复即熔断
6. KV cache 不低于 Q8（若用本地模型）
   实测：4090 上 32B-Q4 + F16 KV 只能到 ~12K context，Q8 KV 才过 24K
7. **模板层是最大隐性故障源，不是模型**
   证据：xLAM-2 8B（BFCL 曾第一）通过 LM Studio 只拿 15%，
   它能正确判断「不该调工具」(8/8)，但完全无法产出调用格式
   → 必须用自己的服务栈私测，不能信榜单
8. Qwen3.6 陷阱：`{"enable_thinking": false}`（冒号后有空格）会被静默忽略并回落
   思考模式，tool call 跑进 reasoning 通道。必须写 `{"enable_thinking":false}`
9. **日志处理不要交给 LLM 检索**
   依据：RULER / context rot 研究 —— 宣称 context 远大于真实可用长度
   → 先用确定性代码切出候选片段再喂模型

### 8.4 provider 路由

借鉴 agentic-swmm 的 `providers/routes.py:75 ROUTES`（10 条）+ 单跳 fallback。

```
RouteSpec{name, label, wire, base_url, key_env, default_model, model_menu,
          keyless, detect_url, hint}
三种 wire 收敛 10 条 route
fallback 只在 MissingCredentials/Connection/401/403/429/5xx 触发，4xx 直接抛
本地 Ollama = OpenAI 兼容：base_url="http://localhost:11434/v1", keyless=True
```

这样保留「云端 vs 本地模型决策成功率对比」实验的可能。

---

## 9. 技术选型决策与依据

### 9.1 不用 LLM Agent 框架当骨架

实测数据（2026-08-10 采集）：

| 框架 | Stars | 30天提交 | 直接依赖 | 致命问题 |
|---|---|---|---|---|
| LangGraph | 39.3k | 42 | 6 | **resume 时整个节点从头重跑** → 3 小时 ISCE2 白跑 |
| Prefect | 23.6k | 138 | 83 | 「断点续跑」实为「重跑+缓存命中」，需 server+Postgres |
| Dagster | 16.0k | 77 | 100 | 唯一原生 stale 传播，但 OSS 无人工审批，太重 |
| AutoGen | 60.3k | **0** | 1 | 官方已标 Maintenance Mode |
| smolagents | 28.7k | **0** | 53 | 事实停更，无持久化 |
| Temporal | 22.2k | — | 23 | 最强持久化，但 payload 2MB 上限 + 需自建集群 |
| Pydantic AI | 19.2k | 288 | 22 | 无致命问题，适合结构化选择 |

**LangGraph 那条是决定性的**：官方明确 resume 时节点从函数开头重跑。

**额外风险**：Prefect 83 / Dagster 100 个直接依赖，塞进本就脆弱的 ISCE2/MintPy
conda 环境是真实的依赖冲突隐患。

**结论**：自研轻量状态机（SQLite + 参数哈希，约 400-600 行）+ Pydantic AI 做选择题。

### 9.2 运行环境

| 软件 | 原生 Windows | 依据 |
|---|---|---|
| ISCE2 / ISCE3 | **不能** | README 依赖 gcc/gfortran/scons，无 Windows 路径 |
| MintPy | 勉强 | 官方原话：Windows 安装「experimental，可能有 bug」 |
| StaMPS | 半能 | MATLAB 可装，但 `mt_prep_isce` 是 Linux shell + MEX |
| PyStamps | **能** | 纯 Python + snaphu |
| SNAP | 能 | ESA 官方 Windows 安装包 |

**方案**：WSL2 + Ubuntu 24.04 + Miniforge。约 8 GB，装在 E: 盘 SSD。

关键发现：ISCE2 官方安装是 `conda install -c conda-forge isce2` **一条命令**，
不需要编译。真正难装的是 StaMPS（绑死 MATLAB + gcc-7），而 PyStamps 替换掉了它。

不装：MATLAB、原版 StaMPS、EZ-InSAR 本体。

### 9.3 硬件与性能

```
CPU   i9-13900K  24核32线程    ISCE2 可吃满并行
RAM   63.7 GB                  MintPy/PyStamps 大堆栈够用
GPU   RTX 4090   24 GB VRAM    ISCE2 有 CUDA 模块（GPUtopozero/GPUgeo2rdr）
SSD   Samsung 980 1TB          唯一 SSD，是 I/O 咽喉
HDD   13 TB + 16.7 TB          SLC 归档
```

20 景 Sentinel-1 的 ISCE2 全流程预估 2-5 小时（24 核并行）。

**存储分层是性能关键**：ISCE2 配准是 I/O 密集型，工作区必须在 SSD，
否则 24 核 CPU 空等磁盘。

必做的三个配置：
```ini
# %USERPROFILE%\.wslconfig
[wsl2]
memory=40GB          # 留 24GB 给 Windows
processors=20        # 留 4 核给系统，符合重型计算管控规则
swap=16GB
```
```yaml
# Dask 临时目录指向 SSD（MintPy 官方推荐，避免 workspace lock）
temporary-directory: /mnt/e/insar_tmp
```
```bash
export VRT_SHARED_SOURCE=0
export HDF5_USE_FILE_LOCKING=FALSE
```

**CUDA 决策**：先装 conda 版跑通，CUDA 留作后续优化。conda-forge 的 isce2 是否带
CUDA 需装后实测；若不带需源码编译（占满 CPU 20-40 分钟，属于要报备的重型任务）。
conda 版 vs CUDA 版的加速比对比数据可写进论文。

---

## 10. 竞品分析

### 10.1 KevinTyn/InSAR_Agent（直接竞品，无论文）

技术栈：DeepSeek 云端 + MintPy + HyP3 + ASF Search + ERA5；FastAPI + SSE；Docker；MIT。

**逐行审计结论：三块 novelty 它一块都没做。**

| 我们的 novelty | 它的真实状态 | 证据 |
|---|---|---|
| provenance | **零** | 全仓 grep `hash\|sha256\|provenance`，src/ 下零命中。`StepState` 只有 5 字段 |
| stale detection | **零，且有反向证据** | `workflow.py:105-107` 判定只有 `if status=='done' and not force: skipped`；`set_params()` 改参数不碰 status |
| 断点续跑 | 粗到不可用 | `run_all(start_from: int)` 整数下标，靠 LLM 猜；MintPy 内部是黑盒 |
| 崩溃恢复 | 只是承认丢了 | `app.py:110-116` 重启时把 running 改 failed 加后缀「(服务重启)」 |

**荒诞发现**：存在两套并行流水线 —— `workflow.py` 的 WorkflowRunner（有 state.json）
只挂在 LLM 工具上，前端不用；系统提示词强制走的 `auto_pipeline` 完全不写 state.json。
即：唯一有持久化的那套，实际路径上不会被调用。

其 "Workflow Engine (pause/resume)" 是营销话术兼半死代码。

#### 值得抄的四个设计

| 设计 | 出处 | 价值 |
|---|---|---|
| 缓存锚定反幻觉 | `agent.py:481-560` | LLM 只能传 `scene_indices`（0-based），系统从磁盘缓存读真值，越界返回中文错误让 LLM 自纠 |
| task_id 唯一句柄 | 全局 | "task_id NEVER contains slashes"，路径服务端解析，消掉整类路径幻觉 |
| 事件工厂分离 | `events.py`（110行） | 「给 LLM 的 data」与「给前端的 ui_update」在同一 tool_result 里分离 |
| 双 SSE + 协作式取消 | `app.py` + `mintpy.py:213-227` | 对话 120s 超时 / 长任务 30s 心跳；`threading.Event` 透传到 subprocess，terminate→wait(5)→kill |

#### 它踩过的坑（我们会踩得更狠）

**最大的疤：LLM 不肯调工具，只输出文本假装调了。** 留下三处补丁：
1. 提示词整节 "ABSOLUTE ANTI-FABRICATION"（`agent.py:31-41`）
2. 中文关键词表硬性强制 `tool_choice='required'`（`agent.py:1430-1442`），
   `_prog_kw` 列了「托管/确认/好的/嗯嗯/看看…」二十多个词，
   还有「上一句含'是否'且用户输入≤6字」的启发式
3. 要求「必须先调 `save_mintpy_overrides(task_id, {})` 再调 preview」这种凑数调用

它用云端 DeepSeek 都这样。**解法不是抄关键词表**，而是规划器出计划、执行器校验。

其他雷区：
- **ERA5/CDS 不可靠**：`agent.py:1049-1067` 被迫自动降级 —— 失败后改
  `troposphericDelay.method='no'`、unlink 所有 ERA5 中间产物、整个 MintPy 重跑
- **MintPy 输出文件名不稳定**：`mintpy.py:340-343` 写候选列表逐个试
  `['timeseries_ramp_demErr.h5', 'timeseries_ERA5_ramp_demErr.h5', 'timeseries.h5', ...]`
- HyP3 轮询死板固定窗口：`max_checks=60`，超 30 分钟返回 Timeout，已提交作业无从接管
- 后处理用「文件存在即跳过」当幂等（7 处 `if dst.is_file(): SKIP`），参数改了输出名
  不变时旧产物被当新的

#### 它的 MintPy 封装（我们要做同样的事）

- 用**正则替换模板文件**，不是 Jinja2。模板里写死占位符 `/workspace/ASF/gamma_clipped`，
  用 `_PATH_KEYS` 6 个 key 逐个替换；替换失败只 append warning
- 正则 `(key\s*=\s*)\S+` 只吃一个非空白 token，会把行尾注释留在原地（脆）
- **不管 MintPy 多步骤**，就是 `smallbaselineApp.py` 一把梭。MintPy 原生支持
  `--dostep`/`--start`，它没用 → **这是我们做步骤级断点续跑的最大切入点**
- 后处理链值得抄：h5→tif → mask_velocity（maskTempCoh + waterMask 求或置 nodata）
  → standardize_names（从 h5 attrs 读 START_DATE/END_DATE 生成
  `vel_{path}_{frame}_{YYYYMM}_{YYYYMM}.tif`）

#### 安全问题（我们绝不能重复）

```
submit_insar.py:254  明文密码落盘 f.write(user + '\n' + password + '\n')
                     accounts.enc 加密体系被完全绕过
                     crypto.py 密钥是机器码 sha256 派生，等于没加密
app.py               /api/browse 能列目录、/api/file 能读文件，无鉴权
                     cookie 没有就现场发一个，任何人访问即得会话
session.py           写了完整多用户体系，但是死代码；密码裸 sha256 无 salt
```

我们主打「数据不出域」，若系统自己有未鉴权文件读接口，卖点一击致命。

其他：零测试（无 tests/ 目录）；8 个直接依赖很轻；MintPy 本身不在依赖里；
硬编码兜底坐标 `lon=117.2, lat=35.6`；README 与代码不符（模型名三处不一致）。

### 10.2 DefoEye（arXiv 2608.04915，2026-08-05）

Streamlit 表单式 GUI 包装 GMTSAR，**零 LLM 成分**。作者 Lund University。
未查到期刊正式发表。

增量功能：并行作业执行、干涉网剪枝、多种解缠锚定。

实验设计（我们的 baseline 标杆）：2020-2024，四区域 —— Bologna / Gotland / Houston
对比 **10 个 GNSS 站**，RMSE 4.3-11.9 mm，Pearson r 0.63-0.95；Karaj 无 GNSS
改与其他工具对比，RMSE 4.8 mm/yr，r 0.98。

**可复现性机制：摘要与仓库文档中未出现任何 provenance / hash / manifest /
断点续跑 / 失效判定表述。**

差异化论述（可直接进 related work）：
> DefoEye 与我们处在不同抽象层。它把 GMTSAR 的 C-shell 手工步骤替换为 Streamlit
> 表单，解决「参数怎么填」的界面可用性问题；用户仍须自己知道该跑哪些步骤、
> 以什么顺序跑、改了某个参数后哪些下游产品已失效。我们解决「工作流该怎么编排、
> 改了参数后哪些结果不再可信」的决策与契约问题。
>
> 三条硬性区分：(a) 它锁定 GMTSAR 单栈，我们跨 ISCE2/MintPy/PyStamps 三栈并打通
> 缺失桥梁，从而能做同源数据 PS/SBAS 双链交叉验证 —— 单栈系统架构上无法提供这种
> 质量门；(b) 它的方法选择完全由人在表单里定，我们支持逐步在「人指定」与
> 「Agent 自选」间切换；(c) 它无 provenance 与 stale detection。

若审稿人问「DefoEye 已能一键跑通，你多做了什么」：
> 一键跑通与可复现是两个正交问题。DefoEye 证明端到端管道能通且精度可靠（GNSS
> RMSE 4.3-11.9 mm），这属于**处理正确性**；我们主张的是**结论可追溯性**：任何一个
> 位移产品都能回答「它由哪些输入、哪个参数集、哪条命令生成，以及某参数改动后
> 它是否已失效」。

### 10.3 agentic-swmm-workflow（已发 SCI，最重要参照物）

Zhang, Z. & Valeo, C. (2026). *Agentic SWMM: Auditable and reproducible stormwater
modelling workflow with Agent Skills and Model Context Protocol.*
**AI for Engineering (MDPI), 1(1), 5**, doi 10.3390/aieng1010005。University of Victoria。

领域数值软件 + Agent 层 + 本地 Ollama 支持 + 目标发论文 —— 与我们同构度极高。

**它公开承认的空白（我们的机会）**，证据边界表原文：
> "Memory layer fires correctly and shapes planner decisions;
> **staleness weighting and negative-precedent handling are next-milestone scope**."

三层架构：执行层 / 建模记忆层 / 受控技能演化层。
执行侧：LLM planner → Skill 目录 → MCP 传输 → 确定性 swmm5 引擎。

顶层入口 skill 职责原文（值得借鉴）：
> "decides which workflow path to take, which QA gates must pass, and
> **when to stop rather than invent missing inputs**"

审计层职责原文：
> "The audit layer does not run SWMM. It consolidates the evidence produced by the
> workflow into machine-readable provenance, run-to-run comparison records, and
> Obsidian-compatible notes."

#### provenance schema（实测 1.3，可直接改造成 InSAR 版）

生成者 `skills/swmm-experiment-audit/scripts/audit_run.py`。

```
schema_version / generated_by / generated_at_utc
run_id                    ← 无 UUID，纯靠目录名（我们要改成 UUID+单调时间戳）
case_name / case_id / objective / workflow_mode / status
run_dir  { relative_path, absolute_path }
repo     { root, git_head, git_branch, git_status_porcelain }
tools    { python_executable, python_version, swmm5_version }
environment { python, platform, aiswmm_version, git_commit,
              container_image, container_image_digest, captured_by }
commands [ { id, return_code, duration_seconds, stdout_file, stderr_file } ]
inputs   {}
artifacts { <id>: { id, role, relative_path, absolute_path, exists,
                    sha256, produced_by, used_for[], metadata? } }
metrics  { <name>: { name, value, unit, source_artifact, source_section,
                     source_field, source_validation{parsed_from_report, matches} } }
qa       { status, pass_count, fail_count, checks[{id, ok, detail}] }
warnings [] / memories_applied [] / human_decisions []
raw_sources { ... }
```

**InSAR 版改造要点**：
- `run_id` 换 UUID + 单调时间戳（原项目纯靠目录名，跨机合并会碰撞）
- `environment` 加 ISCE2/SNAP/snaphu/GDAL 版本 + conda env hash
- `artifacts` 的固定 id 换成 slc/ifg/geom/psi 的 id 集
- `metrics` 换成 PS 数、平均相干、速度分布分位数

另有 agent 快照 hash（`build_agent_snapshot():103`）值得抄：
`tools_schema_sha256` / `skills{name:sha}` / `intent_map_sha256` / `system_prompt_sha256`
—— **prompt 也进 provenance**。

#### 跨环境字节级复现实验设计（分层隔离，精巧）

同一输入走三条独立栈：
```
① macOS 全链，自然语言 prompt 驱动（aiswmm 0.7.0a1 + Homebrew 二进制）
② Docker 内 runner（0.6.4 + 源码编译的 SWMM）
③ 同容器裸 swmm5 直调

三者 model.out SHA256 全等
②vs③ 隔离出 skill 层透明性
①vs②③ 隔离出 MCP 层透明性
```
文本报告先 `strip_analysis_timestamps()` 剔时间戳再比。

**关键在分层隔离** —— 不是笼统说「我们可复现」，而是分别证明每一层透明直通。

#### 它的三个缺陷（我们要改进；审计日期 2026-08-10，上游更新快、批评有保质期）

1. schema 版本号裂成三处（`collect_run` 字面量 1.1 / `provenance_v1_2` 常量 /
   `main()` 硬编码 1.3），`comparison.json` 停在 1.1 与 provenance 脱钩 —— **2026-08-12 注：上游已修复，schema 已统一为 1.1（`COMPARISON_LEARNING.md:147`），论文 related work 不得再引用此条批评**
2. 「失败也审计」只有文档意图 —— `docs/experiment-audit-framework.md:110` 写着
   "should happen after success, failure, or early stop"，但代码里 threshold_hits/
   moc/memory hook 全被 `if result.return_code == 0` 门控
3. 指标来源契约无守护测试 —— 文档、`parse_node_inflow_peak()` 硬编码、note 表格
   三处靠人工同步 —— **2026-08-12 注：上游已补 60+ 个 `test_audit_*` 守护测试（`COMPARISON_LEARNING.md:148`），此条批评同样失效**

#### LLM provider

支持 10 条 provider 路由（OpenAI/Anthropic/OpenRouter/DeepSeek/Groq/Gemini/
本地 Ollama/本地 LM Studio/OpenAI 兼容网关/自定义端点）+ 本地回退链。
**但论文级实验用的是云端 `gpt-5.5`。**

→ 这意味着「我们能接本地模型」不是差异点。

### 10.4 MAD-SAR（非同类竞品）

Arai, K. *MAD-SAR: A Multi-Agent Agentic Engineering Framework for Landslide
Detection Using Sentinel-1 SAR Imagery.* **Information 2026, 17(6), 597**,
doi 10.3390/info17060597。

**规则驱动**（非 LLM）的 agentic 框架，协调异常检测/超分辨率/目标检测/语义分割
四个 CV 模块。不涉及干涉处理链、不涉及可复现性契约。

可作为「agentic 思想已进入 SAR 领域」的引用，以及
"physics-aware validation engine" 这一质量门思路的参照。不构成 novelty 威胁。

### 10.5 EZ-InSAR（前辈，论文对比基线）

Hrysiewicz, A., Wang, X. & Holohan, E.P. *EZ-InSAR: An easy-to-use open-source
toolbox for mapping ground surface deformation using satellite interferometric
synthetic aperture radar.* Earth Sci Inform (2023). doi 10.1007/s12145-023-00973-1

MATLAB GUI 封装 ISCE + StaMPS + MintPy。README 原话与我们的目标几乎一字不差：
> "EZ-InSAR minimizes the work of user in downloading, parametrizing, and processing
> the SAR data, so enabling these who are not familiar with InSAR but can also
> produce and analyze ground surface displacements by themselves."

对比表（可直接当论文 Table 1）：

| 维度 | EZ-InSAR (2023) | 我们 |
|---|---|---|
| 交互 | MATLAB GUI 点按钮 | 自然语言 + 步骤表 |
| 方法选择 | 用户自己决定 | 数据诊断 + 规则引擎自动路由 |
| 参数设置 | 用户填表单 | 场景知识库自动注入 |
| 出错处理 | 报错停住，看日志 | 日志解析 + 受限自愈 |
| 中断恢复 | 无，重跑 | 步骤级断点续跑 |
| 结果可信度 | 人工判断 | 双链交叉验证 |
| 许可成本 | MATLAB + 8 个 Toolbox | 全开源 |
| 论文产出 | 无 | provenance → 方法章节 + 期刊级出图 |

`EZ-InSAR_For_Windows`（西南交大王晓文团队）的做法：把预装好三套引擎的
Ubuntu 22.04 镜像打包，让用户在 Windows 上用 WSL 导入。

---

## 11. 数据资产

```
已有（InSAR-Pro/backend/data/real_data/RidgecrestSenDT71/）：
├── hyp3/            11 个干涉对（7 个获取日期）ASF HyP3 解缠干涉图（2019 Ridgecrest 地震，加州）
│                    每对含 unw_phase / corr / dem / 入射角 / water_mask
├── mintpy/inputs/   ERA5.h5（45.7 MB，大气校正数据已下载）
└── results/         timeseries_ridgecrest.h5
                     ← 实测只有 7 个日期、无 MintPy 元数据属性
                     → 演示用合成数据，不是真实处理结果

辅助（10-sci-papers/00-research-briefs/auxdata/）：
└── 青藏高原 DEM 瓦片（N37-38, E100-101）+ WorldCover 土地覆盖

缺：原始 SLC（.SAFE）、ISCE2 产物、StaMPS 产物、青藏高原 SAR 影像
```

**两个场景的论文叙事**：
- 场景 A：Ridgecrest 地震（数据已有）→ 端到端跑通 + 同震阶跃模型自动识别
- 场景 B：青藏高原冻土（数据需获取）→ 自主获取数据 + 周期冻融模型自动注入

场景 B 的数据获取本身就是 Agent 的第一个任务，是最好的演示。

---

## 12. Baseline 要求清单

竞品做到了，我们不做会被审稿人挑掉。

| # | 要求 | 标杆 |
|---|---|---|
| 1 | GNSS 定量精度验证，多区域多形变机理 | DefoEye：4 区域、10 GNSS 站、RMSE 4.3-11.9mm、r 0.63-0.95 |
| 2 | 无 GNSS 区域改用工具间互比 | DefoEye 的 Karaj 做法，可复用为双链验证补充论证 |
| 3 | **裸命令行 vs Agent 驱动的等价性证明** | 竞品论文的核心可信度锚点 |
| 4 | 跨环境字节级/数值级复现，且分层隔离 | agentic-swmm 的三栈同 SHA256 设计 |
| 5 | 完整 provenance + artifact 级 SHA256 + 命令轨迹 + Git 状态 + 工具版本 | 对齐 `experiment_provenance.json` |
| 6 | 指标来源契约 + 禁用来源清单 | 为 InSAR 指标钉死合法/禁用来源 |
| 7 | 证据边界表，"X, not Y" 句式 | 强烈建议照抄结构 |
| 8 | 审计在失败/提前停止时同样触发 | "Partial evidence is still useful evidence." |
| 9 | 可复现运行入口（Docker pin 版本 + 一行命令 + 期望哈希） | 审稿人会要求同等待遇 |
| 10 | 开源 + DOI + 版本化发布 | GitHub + Zenodo DOI + CHANGELOG + CI |

**第 3 条是现在就要记住的设计约束**：系统必须能导出「等价的裸命令行脚本」，
所以所有命令必须可序列化、可复现。这直接影响 runtime 层设计。

---

## 13. 需要对齐的领域术语

2026 年出现了一批遥感 Agent 综述与 benchmark，领域正在形成共识框架。
必须主动对齐其术语（planning / tool orchestration / trajectory-aware evaluation），
否则审稿人会认为在自说自话。

```
Agentic AI in Remote Sensing: Foundations, Taxonomy, and Emerging Systems  (2601.01891)
Agentic AI for Remote Sensing: Technical Challenges                        (2604.24919)
GISAgentBench                                                              (2608.01645)
TerraBench                                                                 (2606.13148)
EO-Gym                                                                     (2605.01250)
GeoNatureAgent                                                             (2606.12821)
```

这批 benchmark 的存在意味着：**论文不能只有 case study，需要构造自己的
InSAR 任务评测集。**

---

## 14. 上游维护风险

```
StaMPS    278 star，最后推送 2023-02-03  ← 三年半未更新，实质停更
PyStamps  2026 年发布，KorrAI 公司维护，ESA-PL 许可
ISCE2     v2.6.5，657 star
MintPy    v1.6.4，822 star
```

StaMPS 停更对我们有利（说明 PyStamps 是必要替代），但论文里要说明上游维护风险。

PyStamps 官方验证数据（README）：休斯顿 61 景，PS 点数 2,233,428 vs StaMPS 2,233,039
（+0.02%），平均速度均 0.00 mm/yr，CPU 占用低 64.6%。

---

## 15. 待补的信息缺口

| 缺口 | 原因 | 影响 |
|---|---|---|
| DefoEye 全文 | arXiv PDF 内容类型被拒 | 缺 limitations 原文、章节结构 |
| agentic-swmm 论文正文 | MDPI 三次 403 | 缺章节安排、实验个数、作者自承局限（仓库文档已覆盖架构与证据边界原文） |
| Qwen3.6-27B/35B BFCL 分数 | 官方未公布 | 本地模型选型只有社区口碑 |
| Docker 2025-06 工具选择 F1 评测 | 原页已下线 | 0.971 这个数只有二手引用，论文引用需谨慎，应自行私测 |

---

## 16. Prior art 核查与 novelty 修正（2026-08-10 补充）

针对「参数级失效判定是否已被做完」的风险核查，结论：**机制本身是成熟 prior art，
我们的贡献定位必须修正为"集成与场景创新"，不能声称发明了该机制。**

### 16.1 DVC：已实现参数级失效判定（证据来自 dvc.org 官方文档）

- `dvc.yaml` stage 的 `params` 字段可追踪到具体参数键（如 `threshold`、`nn.batch_size`）
- `dvc.lock` 记录：cmd 字符串、每个 dep 的 md5、**每个被追踪参数的键+实际值**、
  每个 out 的 md5+size
- 改被追踪参数值 → stage 判 changed → `dvc repro` 只重跑该 stage 及下游
- `--force-downstream`（强制级联）/ `--downstream`（从某步跑下游）/
  `--single-item`（单步）/ `frozen` / `always_changed` / foreach / matrix 模板

**DVC 缺三样（我们的差异化空间）**：
1. DAG 静态（必须预先写 dvc.yaml）；我们的 DAG 由 LLM 按对话动态生成，
   方法选择是一等公民（DVC 换方法 = 手改 cmd 字符串；我们 = registry 操作 + 可行性校验）
2. 无运行中人机共控（无"人改方法 → 自动标脏下游"语义）
3. 无质量门（不会因相干性过低停链）

### 16.2 redun：最接近自研方案的现成工具，不引入

insitro 开源，`pip install redun`：动态 DAG（lazy expression）、文件哈希+函数级代码哈希
双重失效判定、调用图存 SQLite 可查 provenance（`redun log <file>` 输出完整上游推导链）。

不引入的原因：无运行中人工干预；面向 Python 函数而非外部 CLI 长任务；
维护活跃度存疑。**借鉴它的 call graph schema。**

### 16.3 Apache Burr：决策循环层候选

Apache 孵化项目，零依赖核心，action/transition/State 显式状态机，
halt_after + 可插拔 persister + telemetry UI。

局限：小时级子进程放 action 里，崩溃后 resume 仍重跑该 action（与 LangGraph 同根问题）。
**只可能用于 LLM 决策循环层，执行层仍自研。** 写代码时对比"自研 200 行 vs Burr"。

**2026-08-12 源码核实关闭：不引入，自研事件循环。** 崩溃后 action 整步重跑已被
`burr/core/application.py:2683-2686` 证实；详见 `reference/AGENT_PRODUCTS_LEARNING.md` §4.1。

### 16.4 论文 novelty 表述修正

```
旧表述（危险）：我们发明了参数级失效判定
新表述（站得住）：基于哈希的失效判定是成熟机制（Make → Snakemake → DVC → redun），
                 但都服务于静态定义的流水线。我们首次将其与
                 LLM 动态规划 + 人工中途改方法 + InSAR 质量门结合，
                 使方法选择成为失效判定的第一维度。
```

related work 必须引用：Snakemake、DVC、redun、Nextflow。

### 16.5 架构调整

- 指纹 schema 对齐 dvc.lock 字段（cmd + deps md5 + params 值 + outs md5），便于对比论述
- audit 层新增 RO-Crate 导出（内部 JSON + 标准格式双输出，细节待核：wfrun profile）
- provenance 参考 redun call graph 的"上游数据流"记录结构