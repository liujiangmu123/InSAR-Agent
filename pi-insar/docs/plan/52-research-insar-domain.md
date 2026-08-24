# 52 · InSAR 领域研究:处理什么数据 × 怎么处理 × 输出什么 × 能做什么(后续开发依据)

> 定位:`04-science-capability-map.md` 是"本机能力盘点"(向内看),本文是"领域全景 + 差距分析 + 路线建议"(向外看)。
> 结论用于排定 50/51 之后的开发波次(R1-R6)。联网检索与整理日期 **2026-08-16**,关键论断均附来源;
> 凡引用本仓事实,以 `src/insar_agent/registry/capabilities.py` 与 `04` 号文档为准。

## 0. 结论先行

1. **数据面进入"双频免费时代"**:Sentinel-1(C 波段)之外,NISAR 的 L 波段数据已于 2026-07-20 全面免费开放(ASF 分发,含 GUNW 解缠干涉产品),年底补齐全部科学阶段存档。我们第 1 步的 `prep_nisar.py` 已在机未声明 —— 这是最低成本的高价值扩展。
2. **方法面从 PS/SBAS 分立走向 PS/DS 混合相位连接**:NASA OPERA 的 DISP-S1 业务产品用开源库 Dolphin 做 PS+DS 混合估计。我们第 7 步"SBAS+PS 双链"的底座仍正确,但 DS/相位连接应列入中期路线。
3. **产物面有了行业金标准**:欧洲 EGMS 三级产品(Basic 相对 LOS → Calibrated GNSS 绝对 → Ortho 垂直+东西向)定义了位移产品的分级形态。我们 21-23 步(掩膜→板块校正→升降轨分解)已对齐其形态,缺的是 GNSS 校准环节。
4. **接入模式应从 2 种扩到 3 种**:全链(SLC 起步)、云端干涉产品(HyP3 起步,realtest 已走通)之外,"位移产品直接分析"(OPERA DISP / EGMS / LT-1 官方形变场 → 直接进 20-28 分析链)是轻量、无重型计算的新模式,特别适合教学与快速评估。
5. **智能化是 2024-2026 的明确趋势但尚未业务化**:深度学习解缠/大气校正/质量评估论文密集,普遍卡在跨传感器泛化。对我们的正确姿势是"DL 做 warning 级质检参考,不做硬门"。

---

## 1. 处理什么数据

### 1.1 SAR 卫星与波段

| 卫星/星座 | 波段 | 重访 | 分辨率/幅宽 | 获取 | 对 InSAR 的意义 |
|---|---|---|---|---|---|
| **Sentinel-1 A/C**(ESA) | C | 6-12 天 | IW 模式 5×20 m / 250 km | 免费(ASF、CDSE) | 事实上的全球监测主力;我们默认平台 |
| **NISAR**(NASA-ISRO,2025-07 发射) | **L**(+S) | 12 天 | 3-10 m / 240 km | **免费**(ASF;2026-07-20 起开放,年底补齐存档) | L 波段穿透植被,低相干区革命性改善;自带 GUNW 解缠产品 |
| **ALOS-2 / ALOS-4**(JAXA) | L | 14 天 | 3-10 m | 科研申请/商业 | 经典 L 波段;本仓条带链(ALOS-1 raw)已实测 |
| **陆探一号 LT-1 A/B**(中国,2023-12 业务化) | L | 双星 4 天/单星 8 天 | 3 m / 400 km | 自然资源卫星遥感云服务平台 | 全球首个形变+测图双能力 L 波段编队;国产自主可控 |
| **高分三号 01/02/03**(中国) | C | ~29 天(单星) | 最高 1 m | 同上 | 国产 C 波段补充;干涉能力有限 |
| TerraSAR-X / COSMO-SkyMed / RADARSAT-2 | X/C | 11/16/24 天 | 1-3 m | 商业 | 高分辨率精细目标(大坝、建筑) |
| ICEYE / Capella / Umbra 等商业星座 | X | 小时级(星座) | 0.5-1 m | 商业 | 应急响应;干涉对配对受限 |

要点:波段决定适用场景 —— C 波段(波长 5.6 cm)对植被区失相干敏感;L 波段(24 cm)穿透性强,是冻土、滑坡、植被区监测的首选,也更耐大梯度形变(每条纹代表的形变量更大)。

### 1.2 数据级别与三种接入模式

行业数据级别:L0 原始信号 → **L1 SLC**(单视复数,干涉的标准起点)→ L2 地理编码产品(OPERA CSLC、NISAR GSLC/GUNW、HyP3 成品干涉对)→ **L3 位移产品**(OPERA DISP-S1、EGMS、LT-1 全国形变场)。

由此对应三种接入模式(前两种本仓已支持):

| 模式 | 起点 | 走本仓哪些步 | 现状 |
|---|---|---|---|
| A. 全链自主 | L1 SLC(ASF 下载/本地) | 1-11 全步 | ISCE2 条带链 WSL 实测通过;TOPS 链已声明待实测 |
| B. 云端干涉产品 | HyP3/ARIA GUNW/LiCSAR 成品干涉对 | 1(导入)→ 7-11 | **realtest 主路线**,场景包 `cloud_completed: [2,3,4,5,6]` |
| C. 位移产品直接分析 | OPERA DISP / EGMS / LT-1 形变场 | 20-28 分析链 | **缺口**:step 20 `register_sources` 只吃 h5,需加 GeoTIFF/CSV 适配(→ R2) |

模式 C 的价值:零重型计算(符合本机算力纪律)、分钟级出分析结论,适合教学演示、快速可行性评估、与官方产品交叉验证。

### 1.3 辅助数据

| 类别 | 内容 | 本仓现状 |
|---|---|---|
| DEM | Copernicus GLO-30(推荐)/ SRTM / 本地瓦片 | 第 2 步三方法齐 |
| 精密轨道 | POEORB(21 天后精轨)/ RESORB | 第 2 步 `orbit` 参数 |
| 对流层 | ERA5(CDS 凭据)、GACOS、高程相关经验模型 | 第 8 步三方法齐 + OPERA 产品选项 |
| 电离层 | split-spectrum(需 ISCE2 stack 分频谱)、IGS TEC;2026 年新趋势:GNSS-TEC 长波长改正 | 参数已声明;TEC 桥未做 |
| GNSS | 绝对参考定标、独立验证(RMSE/MAE 指标) | **完全缺失**(§6 R3) |
| 掩膜类 | 水体掩膜、相干掩膜 | HyP3 布局自带;第 21 步可生成 |

---

## 2. 怎么处理:方法体系

### 2.1 方法族谱(按发展时间线)

| 方法 | 代表文献/实现 | 原理一句话 | 适用 | 本仓现状 |
|---|---|---|---|---|
| D-InSAR 两轨差分 | 教科书方法 | 两景相位差 − 地形相位 = 形变 | 同震、单事件 | 条带链已实测(quake/stripmap 场景包) |
| Stacking 叠加 | 经典 | 多干涉对平均压噪声 | 快速普查 | 未单列(可由 SBAS 退化) |
| **PS-InSAR** | Ferretti 2001;StaMPS/PyStamps | 只用相位稳定的点目标(建筑、岩石) | 城市、基础设施 | 第 7 步 `pystamps_ps` **已声明未实测**(→ R1) |
| **SBAS** | Berardino 2002;MintPy(Yunjun 2019)、LiCSBAS | 小基线网络反演面状形变 | 自然地表、大范围 | 第 7 步默认方法,**已实测** |
| DS/SqueeSAR | Ferretti 2011 | 统计同质像元(SHP)+ 协方差矩阵全息估计 | 农田、裸地等分布式目标 | 无(→ R4) |
| **PS/DS 混合相位连接** | **OPERA DISP-S1 / Dolphin**(Staniewicz 2024, JOSS) | PS 直用单视相位,DS 经 SHP 邻域相位连接,统一解缠反演 | 业务化大规模生产(北美 30 m 全覆盖) | 无(→ R4,行业方向) |
| 偏移量追踪 / MAI | pixel offset / multi-aperture | 幅度互相关测大形变/方位向形变 | 强震近场、冰川、矿区塌陷 | 无(NISAR 自带 GOFF 产品可作模式 C 输入) |

网络设计护栏(已进注册表):star 拓扑仅 PS 试验用;纯 sequential 短基线有 fading 系统偏差,须混长基线对(Ansari 2021)。

### 2.2 关键环节的方法选择

- **配准**:S1 TOPS 几何配准 + ESD 精化(方位向精度需千分之一像元);条带互相关。均已声明。
- **解缠**:SNAPHU MCF(默认)/ ICU;OPERA 用多尺度 tophu 应对大图。解缠误差改正(bridging / phase_closure)第 7 步参数已声明。
- **大气**:ERA5 再分析(默认)→ GACOS → 高程相关(降级,证据等级下降)。湍流分量靠时序域滤波压制。
- **电离层**:L 波段必修(split-spectrum);C 波段短时序通常可忽略。2026 年 GNSS-TEC 方法进入 DISP-S1 参考文献。
- **参考框架**:参考点/参考日期(相对)→ ITRF 板块运动改正(第 22 步已声明)→ GNSS 锚定(绝对,EGMS Calibrated 形态,本仓缺失)。

### 2.3 软件生态对照(自由部分均为开源)

| 软件 | 覆盖 | 特点 | 与本仓关系 |
|---|---|---|---|
| **ISCE2** | SLC→解缠 | 成熟、NASA 系标准;stripmap/topsApp | 主引擎,条带链已实测 |
| ISCE3 | 同上 | NISAR 官方重写(早期阶段,C++/CUDA) | 观望;NISAR 产品可用模式 B/C 消化 |
| SNAP | 全链 GUI | ESA 官方免费;openEO 云端 UDP(2026-05 起 CDSE 可在线出干涉图) | 引擎接口已声明未实测 |
| GAMMA / SARscape | 全链 | 商业;SARscape 5.7+ 官方支持 LT-1 | prep_gamma 布局可接其输出 |
| GMTSAR | 全链 | 命令行一体化 | prep_gmtsar 布局 |
| **MintPy** | 时序反演→分析 | SBAS 标准实现,70+ CLI | 主引擎,已实测 |
| LiCSBAS | 时序反演 | 直接消化 LiCSAR 云产品,带闭合环自动剔除 | 思路可借鉴(质检自动化) |
| StaMPS / PyStamps | PS | PS 经典实现 | 第 7 步 PS 引擎(→ R1) |
| **Dolphin** | PS/DS 相位连接 | `dolphin config` + `dolphin run` 两条命令,conda 可装 | CLI 形态与本仓引擎模式天然匹配(→ R4) |
| GBIS / Kite / Grond | 形变源反演 | 贝叶斯 Okada/Mogi | 第 28 步导出桥已声明 |
| RAiDER / PyAPS | 大气 | 对流层延迟 | PyAPS 已用 |

第三方对比研究(2025,南非 Midvaal 沉降):ISCE-StaMPS 精度最高,HyP3-MintPy 长于捕捉大范围强形变(实测到 −233.9 mm/yr)—— 印证我们"SBAS 打底 + PS 补点"的双链设计。

### 2.4 精度验证方法(行业惯例)

1. **GNSS 对比**:LOS 投影后算 RMSE/MAE(NISAR 官方验证口径;EGMS 靠 GNSS 定标)。
2. **闭合环残差**:检验解缠一致性(第 11 步已声明)。
3. **双链交叉验证**:PS 与 SBAS 独立解互验(第 11 步默认门,**本仓独有卖点,待 R1 实测**)。
4. 官方产品交叉:与 OPERA DISP / EGMS / LT-1 全国形变场比对(模式 C 打通后零成本获得)。

精度量级参考:地面沉降工程化监测总体精度优于 ±5 mm(中国地调局,华北/长三角/汾渭);LT-1 官方在轨测试指标 —— D-InSAR 形变场优于 2.7 mm、stacking 速度场 8.6 mm/yr、MT-InSAR 时序 3.7 mm。

---

## 3. 输出什么:产物体系

### 3.1 行业金标准:EGMS 三级产品

| 级 | 名称 | 内容 | 对应本仓 |
|---|---|---|---|
| L2a | Basic | 相对参考点的 LOS 速度 + 时序(升/降轨分开,burst 组织) | 第 9 步 velocity + 第 7 步 timeseries(现状即此级) |
| L2b | **Calibrated**(主产品) | GNSS 模型锚定后的**绝对**位移 | **缺 GNSS 校准环节**(→ R3) |
| L3 | Ortho | 纯垂直 + 纯东西向两层,100 m 格网 GeoTIFF | 第 23 步 `asc_desc_horz_vert` 已声明(形态已对齐) |

启示:产品分级 = 用户分层。Basic 给研究者,Calibrated 给工程方,Ortho 给规划/管理者(方向无歧义)。报告与导出命名建议显式标注级别。

### 3.2 全量产物清单(中间 → 最终 → 交付)

| 层 | 产物 | 格式/位置 | 本仓现状 |
|---|---|---|---|
| 干涉级 | 缠绕/滤波干涉图、相干图、解缠相位+连通域 | ISCE2 布局 / HyP3 GeoTIFF | 已实测 |
| 时序级 | 位移时序、校正后时序、速度场±不确定度、DEM 残差、残差 RMS、时相相干掩膜 | MintPy HDF5 | 已实测 |
| 分解级 | 垂直/东西向速度场、剖面、区域统计、变化量、加速度、外推预测±置信区间 | analysis/ 下 h5/json | 已声明(分析链) |
| 交付格式 | h5 / CSV / GeoTIFF / KMZ(+交互时序)/ SHP;GBIS .mat / Kite / GMT grd / QGIS / HDF-EOS5 | `report/export.py` 五格式 + 第 28 步桥 | 已实测/已声明 |
| 文档级 | 中文报告(数值全部工具实测)、图注、qa.json、provenance、复现包 zip | products/report | 已实测 |
| 图件级 | 速度场主图(600 dpi 色盲安全)、网络图、相干矩阵、剖面图、点位时序 | products/figures | 已实测 |

对照缺口:NISAR GUNW/GOFF、OPERA DISP HDF5、EGMS CSV/GeoTIFF 三类外部产品的**读入**适配(模式 C 输入侧),以及产品分级标注(输出侧)。

---

## 4. 能做什么:应用与服务形态

### 4.1 应用场景(× 场景包现状)

| 应用 | 测什么 | 决策价值 | 场景包 |
|---|---|---|---|
| 城市地面沉降 | 漏斗范围/速率/季节回弹 | 地下水管控、规划;工程化精度 ±5 mm | `subsidence` ✅ |
| 地震 | 同震形变场、震后余滑 | 断层滑动反演(GBIS 桥)、发震构造 | `quake`、`stripmap_coseismic` ✅ |
| 多年冻土 | 冻融周期+退化趋势 | 活动层厚度、青藏工程走廊 | `permafrost` ✅ |
| 滑坡 | 缓慢蠕滑与加速段 | 隐患早期识别(LT-1 的设计主场景之一) | `landslide` ✅ |
| 火山 | 岩浆囊加压/泄压 | Mogi/Okada 源反演、前兆 | `volcano` ✅ |
| 矿区沉陷 | 采空区沉陷盆地 | 安全生产、土地复垦 | 无(可由 subsidence 覆写) |
| 基础设施 | 高铁/大坝/机场毫米级变形 | 运营预警(PS 主场,依赖 R1) | 无 |
| 水文参数反演 | 含水层贮水系数、弹性/非弹性响应 | 水资源管理(形变→参数是 SCI 热点) | 无 |
| 冰川/油气田 | 冰流速、注采形变 | 气候研究、油藏管理 | 无 |

### 4.2 服务形态分级(产品定位参考)

1. **单点深度研究**(现状主打):一个 AOI 全链可复现,产出论文级图件+报告 —— 对标"研究者自己跑 ISCE+MintPy",卖点是对话驱动+可审计。
2. **区域批量普查**(EGMS/LT-1 全国一张图形态):多 AOI/多轨编排,年度更新 —— 需要批量剧本与磁盘预算管理(重型计算纪律适用,须用户逐批批准)。
3. **业务化持续监测**(预警形态):新景到达→增量处理→阈值告警 —— 远期,依赖 2 的成熟。

### 4.3 教学形态(结合讲师业务)

模式 C(官方位移产品直接分析)零算力、分钟级出图,天然适合课堂演示"InSAR 能看见什么";
配合 strict/free 双模式讲"可复现科学计算";复现包 zip 可直接作为课程作业材料。建议在 R6 做一个 `teaching` 场景包(小数据、快节奏、图件全开)。

---

## 5. 前沿趋势(2024-2026 检索结论)

1. **双频/多源融合**:NISAR(L)+ Sentinel-1(C)同区互补;OPERA 已预告 DISP-NI(NISAR 版位移产品,与 DISP-S1 同构)。多源一致性分析将成常规动作(我们 23/26 步的 diff 机制可直接服务)。
2. **PS/DS 混合与云端规模化**:Dolphin 相位连接、burst 级并行(CDSE openEO UDP 在线出干涉图)、"不下载 SLC"的处理范式。
3. **深度学习渗透各环节但未业务化**:解缠(UnwrapDiff 扩散模型以 SNAPHU 为先验,NRMSE −10%;ResUCTransNet;UMSPU 支持 2048² 大图)、干涉图质量评估(ConvNeXt,LiCSAR 2 万样本集)、大气校正、时序预测。共同瓶颈:跨传感器/跨场景泛化,缺标准化训练集(测绘学报 2024 智能 InSAR 综述同此判断)。
4. **GNSS-InSAR 深度融合**:EGMS Calibrated 的 GNSS 锚定;GNSS-TEC 电离层长波长改正(2026,已入 DISP-S1 文献链)。
5. **国产自主可控**:LT-1 业务化 + LandSAR 生产系统 + 全国形变场产品;后续 01 组补网与 02 组规划。国内用户对"能吃 LT-1 数据"会有真实需求。

---

## 6. 差距分析与开发路线建议(R 波次)

> 排序原则:先补"已声明未实测"的科学卖点,再扩数据面(低成本高感知),再上新方法(高成本)。
> 所有重型计算(全链实跑)遵守本机算力纪律:先获用户批准、低优先级、一次一个。

### R1 · PS 链实测 + 双链交叉验证落地(P0,核心科学卖点)

- **内容**:`pystamps_ps`(第 7 步)真实跑通;ISCE2→PyStamps 桥(`registry/bridges.py` 已有声明骨架);第 11 步 `crossval_ps_sbas` 用真实双解出 `crossval_r`,阈值走 `contract.yaml` 台账。
- **为什么**:这是第 11 步**默认质检门**与规划中的核心科学贡献(§2.4);行业对比研究证明 PS/SBAS 互补是真需求(§2.3)。
- **验收**:同一数据集 PS 与 SBAS 双解 + qa.json 相关系数落账;strict 模式全程可复现。

### R2 · 数据接入扩展:NISAR / LT-1 / 模式 C(P0,数据面红利)

- **内容**:
  a) 第 1 步声明 `nisar_import`(GUNW 即"云端已完成 2-6"的又一实例,复用 `cloud_completed` 机制;`prep_nisar.py` 本机已有);
  b) 模式 C:step 20 `register_sources` 增加 GeoTIFF/CSV 适配,可直接登记 OPERA DISP / EGMS / LT-1 形变场进分析链;
  c) LT-1:以 prep_gamma/SARscape 导出布局接入,场景包标注国产数据路线。
- **为什么**:NISAR 免费 L 波段是 2026 年最大数据红利(§1.1);模式 C 零算力、教学友好(§1.2、§4.3)。
- **验收**:NISAR GUNW 样例走到 7-11;一份 EGMS/DISP 样例产品完成 21-25 分析并出报告。

### R3 · GNSS 校准与产品分级(P1,对标 EGMS Calibrated)

- **内容**:新增 GNSS 对比/锚定桥(读 GNSS 时序 CSV → LOS 投影 → 差值统计/常数校准;不自造平差);产物与报告显式标注 Basic/Calibrated/Ortho 级别(§3.1)。
- **为什么**:绝对参考是工程用户的第一问;验证口径(RMSE vs GNSS)是 NISAR/EGMS 官方惯例(§2.4)。
- **验收**:含 GNSS 站的数据集出"InSAR−GNSS 残差表"并进 qa.json。

### R4 · DS/相位连接引擎:Dolphin(P1,方法面升级)

- **内容**:第 7 步新增 `dolphin_ps_ds` 方法(`dolphin config`→`dolphin run` 的 CLI 形态与 engines 模式匹配);输出回 MintPy 布局继续 8-11 步。
- **为什么**:PS/DS 混合是业务化主流(OPERA,§2.1);农田/裸地类分布式目标是 SBAS 与 PS 之间的空档。
- **风险**:重型计算;Windows 支持需验证(conda-forge 有包);列为实验方法不动默认。

### R5 · 智能质检增强(P2,趋势卡位)

- **内容**:干涉图质量 DL 评估(相干+闭合环统计已有,DL 分类只作 **warning 级**参考,不做硬门,对齐 PENDING 阈值纪律);现有 `/api/vision-qa` 识图质检扩展到网络图/相干矩阵。
- **为什么**:智能 InSAR 是明确趋势但泛化未解(§5.3)—— 做"参考意见",不做"决策依据",符合诚实纪律。

### R6 · 服务化与教学(P2,产品面)

- **内容**:区域批量普查剧本(多 AOI 队列,逐批用户批准);`teaching` 场景包(小数据快节奏);年度更新剧本对齐 EGMS 节奏。
- **为什么**:§4.2 服务分级的 2 级形态;讲师业务的直接需求(§4.3)。

### 不做清单(本轮明确排除)

- 不自造 GNSS 平差/形变源反演(保持第 28 步"只做桥"纪律);
- 不上线 DL 解缠替换 SNAPHU(证据不足,§5.3);
- 不做 ISCE3 迁移(早期阶段,NISAR 产品经模式 B/C 消化即可);
- 不做偏移量追踪自研(NISAR GOFF 产品可作模式 C 输入,先观察需求)。

---

## 8. 产品外壳与操作员路径(2026-08-17 补勘)

> 本节把「领域能力」落到桌面操作员实际看见什么、说什么、右栏该显示什么。
> Chrome 事实来自实读 `E:\SoftApp\pi-app\src\renderer\src\app\app.tsx`:主对话页渲染的是 `ImmersiveChrome`,**不是** overlay 里改过的 `TopBar`。这就是用户反馈「右上角还是 pi」的根因:Phase 51 只改了设置页用的厚顶栏,主聊天顶栏硬编码 π + `"pi"`。

### 8.1 可见品牌分层(改哪一层才算「外壳改好了」)

| 层 | 用户看见 | 正确产品名来源 | 2026-08-17 状态 |
|---|---|---|---|
| OS 窗口标题 / 托盘 tooltip | 任务栏悬停 | `APP_DISPLAY_NAME`(`app-brand.ts`) | InSAR Agent |
| 主对话顶栏(`ImmersiveChrome`) | 窗口内最显眼的字 | `t('common:app.name')` | **已改为 InSAR Agent**(原硬编码 `pi`+π) |
| 设置页厚顶栏(`TopBar`) | 仅设置视图 | 同上 | 早已 InSAR Agent +「InSAR 工作台」chip |
| 系统通知 | 运行结束/等待操作 | 字面量须跟 APP_DISPLAY_NAME | 已改 |
| 设置侧栏「Pi」 | 设置分类名 | 指 pi CLI 运行时,不是产品名 | 已改为「运行时」 |
| `electron-builder` productName | 快捷方式/任务管理器 | **禁止改**(userData 路径) | 仍为 pi Desktop,属有意保留 |

验收口令:打开主对话页,顶栏应读「InSAR Agent / \<项目名\>」+「InSAR 工作台」chip,不得出现孤立小写 `pi` 或衬线 π 方标。

### 8.2 三种接入模式 × 对话开场 × 右栏该显示什么

操作员仍然只在中央对话里下指令;右栏只读。模式不同,右栏「流水线 / 数据 / 图件」的**诚实空态**也不同,不能三种模式共用一句「尚无 run」。

| 模式 | 操作员会说的典型话 | 主链走哪些步 | 右栏流水线 | 右栏数据 | 右栏图件 |
|---|---|---|---|---|---|
| A 全链 SLC | 「从 Sentinel-1 SLC 做到速度场」 | 1–11 | 01–11 全亮;2–6 本机引擎缺失则诚实 TOOL_MISSING,禁止假进度 | SLC 目录、DEM、干涉对数 | 到第 10 步才有期刊图 |
| B HyP3 云端干涉 | 「用 Ridgecrest HyP3 跑时序」 | 1 导入 → 7–11(2–6 skipped) | 02–06 显示 skipped(云端已完成),不是失败 | HyP3 产品目录(unw/corr/dem) | 速度场 + 相干 + 网络图 |
| C 位移产品直接分析 | 「把 EGMS/OPERA/LT-1 形变场做剖面和分解」 | **只走 20–28** | 01–11 整段应标「本 run 不走主链」(不是 pending) | 登记的 GeoTIFF/CSV/HDF5 源 | 分析图(剖面/KMZ/分解场) |

模式 C 是教学与快速评估的主路径(零重型计算)。当前缺口仍是 step 20 `register_sources` 只吃 h5(→ R2/B1)。未接通前,右栏若收到 GeoTIFF 必须显示「源格式不支持」,不得静默当空。

### 8.3 产物分级在 UI 上怎么露出(对标 EGMS)

报告、图件灯箱、导出文件名建议显式带级别,避免工程用户把相对 LOS 当成绝对位移:

| 级别 | 何时出现 | UI 徽章建议 | 本仓对应 |
|---|---|---|---|
| Basic | 第 9 步速度场默认 | `LOS · 相对参考点` | 现状产出 |
| Calibrated | 做过 GNSS 锚定(R3) | `LOS · GNSS 锚定` | 缺口 |
| Ortho | 第 23 步升降轨分解成功 | `垂直 / 东西向` | 已声明 |

模拟 run 的橙色横幅优先级高于级别徽章:simulated 永远先看见「模拟」,再看见 Basic。

### 8.4 场景包与「继续研究」后的操作员映射

| 用户意图 | 应命中场景 | 注意 |
|---|---|---|
| 课堂演示 / 教学流程 | `teaching`(priority 90) | 图件全开、第 11 步 coherence_mask,不假装 PS 双链 |
| LT-1 / 陆探 / GAMMA 布局 | `lt1_gamma`(35) | 「用陆探一号做沉降」会先命中 `subsidence`(25),这是正确的歧义消解 |
| 地震 / Ridgecrest | `quake` | HyP3 云端 2–6 skipped |
| 冻土 / 滑坡 / 火山 / 沉降 | 既有包 | 见 §4.1 |

### 8.5 外壳还没做、但研究后应进下一波 UI 的项

1. 任务管理器/快捷方式仍显示 pi Desktop(productName 锁定)——安装包层可用显示名/图标覆盖,不改 userData 键。
2. 主顶栏仍用 CircleDot 占位,未用 InSAR `build/icon.png`;应用图标已在窗口/托盘,顶栏可后续换成 14px 栅格图标。
3. 右栏缺「接入模式」chip(A/B/C)与产物级别徽章(§8.2–8.3)。
4. 设置「运行时」页内部仍有「与终端 pi 一致」——保留为 CLI 专名,不要改成产品名。

---

## 9. 参考资料

**本仓依据**:`src/insar_agent/registry/capabilities.py`(11+9 步声明)· `04-science-capability-map.md`(本机 70+ CLI 盘点)· `docs/VALIDATION-isce2-wsl.md`(条带链实测)· `report/export.py`(五格式导出)。

**数据源**:
- NISAR 数据指南与开放公告:[nisar-docs.asf.alaska.edu/availability-overview](https://nisar-docs.asf.alaska.edu/availability-overview/) · [ASF 公告 2026-07](https://asf.alaska.edu/notices/nisar-l-band-data-now-publicly-available/) · [产品说明(RSLC/GSLC/GUNW/GOFF)](https://nisar-docs.asf.alaska.edu/products-overview/)
- 陆探一号:[在轨测试论文(ISPRS 2023,精度指标)](https://doi.org/10.5194/isprs-archives-xlviii-1-w2-2023-1251-2023) · [投入使用公告](https://zrzy.luan.gov.cn/ztzl/ztzl/lawxzx/5281806.html) · [自然资源卫星遥感云服务平台](https://www.sasclouds.com/satellite/chinese/lsar)
- HyP3 产品指南:[hyp3-docs.asf.alaska.edu](https://hyp3-docs.asf.alaska.edu/guides/insar_product_guide/) · G-POD SBAS 服务:[terradue 文档](https://terradue.github.io/doc-tep-geohazards-v2/tutorials/gpod-sbas-insar.html)

**行业产品**:
- OPERA DISP 产品族:[JPL 产品页](https://www.jpl.nasa.gov/go/opera/products/disp-product-suite/) · [DISP-S1 数据集(Earthdata)](https://www.earthdata.nasa.gov/data/catalog/asf-opera-l3-disp-s1-v1-1) · [DISP-S1 ATBD(算法基础文档)](https://earthdata.nasa.gov/s3fs-public/2026-03/OPERA_DISP-S1_ATBD_D-108765_Rev_A_v1.0.0_Final.pdf)
- Dolphin:[github.com/isce-framework/dolphin](https://github.com/isce-framework/dolphin)(Staniewicz 2024, JOSS 6997)
- EGMS:[产品说明 v3](https://library.land.copernicus.eu/products/European_Ground_Motion_Service_Product_Description_v3.html) · [官方 FAQ(三级产品)](https://land.copernicus.eu/en/faq/products/european-ground-motion-service)
- openEO CDSE 在线干涉处理:[ESA 2026-05](https://eo4society.esa.int/2026/05/18/sentinel%e2%80%911-insar-processing-with-openeo-in-cdse/)

**方法与综述**:
- InSAR 变形监测综述(方法分类):[测绘学报 2017](https://html.rhhz.net/CHXB/html/2017-10-1717.htm) · InSAR 地学参数反演综述:[测绘学报 2022](http://xb.chinasmp.com/article/2022/1001-1595/20220728.htm)
- 智能 InSAR 综述:[测绘学报 2024(英文版)](http://xb.chinasmp.com/EN/Y2024/V53/I6/1037)
- DL 解缠:[UnwrapDiff(扩散模型)](https://arxiv.org/html/2512.04749v1) · [ResUCTransNet(RS 2026)](https://doi.org/10.3390/rs18050705) · 干涉图质量评估:[ConvNeXt-InSAR(RS 2026)](https://www.mdpi.com/2072-4292/18/5/733)
- 开源软件对比实测:[Midvaal 沉降研究 2025](https://doi.org/10.47191/etj/v10i11.06) · LiCSBAS:[RS 2020](https://www.mdpi.com/2072-4292/12/3/424) · ISCE3:[github](https://github.com/isce-framework/isce3)
- 地面沉降工程化(±5 mm):[中国地调局航遥中心](http://www.agrs.cgs.gov.cn/cgzt/kjcg/201608/t20160818_372139.html) · 冻土 InSAR 综述:[地球科学进展 2021](http://www.adearth.ac.cn/article/2021/1001-8166/1001-8166-2021-36-7-694.shtml)
