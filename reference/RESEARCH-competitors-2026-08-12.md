# InSAR 处理软件生态:竞品与自动化程度全景(2025–2026)

- **调研日期**:2026-08-12(网络搜索,来源以官方文档 / GitHub / 论文为主,关键事实附链接)
- **对照基准**:insar-agent(自然语言意图 → 规则/LLM 规划 → 编排 ISCE2/MintPy/snaphu 等引擎;参数级失效传播、五阶段执行器断点续跑、六级证据阶梯、溯源账本、run fork 参数试探;差异化主张 = **可复现性与出版级证据链**)
- **每个对象按 6 维回答**:① 自动化程度(一键到哪步/人工决策点) ② 参数管理(模板/预设/推荐) ③ 可复现性(日志/参数留痕/版本锁定)——重点维 ④ 错误处理(失败后怎么办) ⑤ 界面形态 ⑥ 活跃度与定位(最近一年)

---

## 1. 逐对象分析

### 1.1 LiCSAR(COMET 自动化生产管线)

系统级"无人值守"生产线,不是用户软件:全球 Sentinel-1 按预定义 frame 自动生成干涉图/相干图,基于 GAMMA 商业软件跑在英国 JASMIN 集群上([官方文档](https://comet-licsar.github.io/)、[RS 2020 论文](https://doi.org/10.3390/rs12152430))。

1. **自动化程度**:对最终用户是"零键"——新影像入库后自动解算,新干涉图约 2 周内上线门户;内置 EIDP(Earthquake InSAR Data Provider)自动响应 Mw≥5.5 地震事件,自动选 frame、自动初始化、自动出同震干涉图并转 KMZ。**人工决策点:用户完全没有**——frame 定义、多视(5×20,~100 m)、每景配 3–4 个历史配对全部固定;GACOS 大气改正未全自动(按需生成)。
2. **参数管理**:系统级固定默认,无用户可调参数;frame/burst 定义与基线等元数据存 LiCSInfo 数据库。
3. **可复现性**:内部有 LiCSInfo 元数据库(文件路径、垂直基线、解缠像素数等质量量度),产品带元数据 txt;但处理依赖商业 GAMMA + 内部集群脚本,**外部用户无法复现生产过程**,只能复用产品。
4. **错误处理**:入库前自动质检(线检测算法识别 SD/配准伪影;归一化平均相干 + 解缠像素占比阈值剔除坏干涉图);坏 frame 由 COMET 团队重处理,用户只能等或报 issue。2025-10 存储迁移导致下游(LiCSBAS)下载断裂,靠发公告+改代码解决——典型的"生产线变更冲击下游"案例([门户公告](https://comet.nerc.ac.uk/comet-lics-portal/))。
5. **界面形态**:Web 门户(交互地图选 frame 下载)+ CEDA 归档;无处理界面。
6. **活跃度与定位**:持续运行;2025-10 完成存储系统迁移;每年办 COMET InSAR Workshop(2025 有材料)。定位 = 构造/火山监测的**公共产品生产线**,是"把处理彻底藏起来"路线的代表。

### 1.2 LiCSBAS(/LiCSBAS2)

消费 LiCSAR 产品的开源时序分析包(Python+bash),从下载 GeoTIFF 到速度场/时序全链([GitHub](https://github.com/comet-licsar/LiCSBAS)、[RS 2020 论文](https://www.mdpi.com/2072-4292/12/3/424))。

1. **自动化程度**:`batch_LiCSBAS.sh` 一个脚本从步骤 01(下载)跑到 16(时空滤波),中间自动做 loop closure 剔坏干涉图、噪声指数掩膜;**人工决策点**:GACOS 开关、掩膜阈值(11 项噪声指数)、clip 范围、loop 阈值、参考点,以及步骤 15/16 之间人工目检的惯例。
2. **参数管理**:批处理脚本顶部的 shell 变量就是参数面板(`start_step`/`end_step`/`p12_loop_thre`/`p16_filtwidth_km`…),wiki 提供样例脚本;有默认值,无场景预设、无参数推荐。
3. **可复现性**:每步输出 `tee -a` 追加到日志;**改过参数的 batch 脚本副本 = 事实上的参数记录**(靠用户自觉保存);无指纹、无版本锁定。生态碎片化是真实风险:comet-licsar 官方 v1.15.x、yumorishita 的 [LiCSBAS2](https://github.com/yumorishita/LiCSBAS2)(v2.0.0,2026-03,支持 NISAR GUNW)、社区修复 fork(如 [bcankara 版](https://github.com/bcankara/LiCSBAS) v1.15.2 修 2025-10 迁移)并存,**"你跑的是哪个 LiCSBAS"本身就是可复现性问题**。
4. **错误处理**:步骤退出码非零则批处理中止(`PIPESTATUS` 检查);续跑 = 手工改 `start_step` 再跑(有 [issue 实录](https://github.com/yumorishita/LiCSBAS/issues/351));[Known issues FAQ](https://github.com/yumorishita/LiCSBAS/issues/244) 维护常见坑(如 step16 并行在 WSL 卡死 → `n_para=1`)。无自动诊断。
5. **界面形态**:CLI + matplotlib 交互时序查看器(`LiCSBAS_plot_ts.py`)。
6. **活跃度与定位**:很活跃(2025-10 迁移适配、2026-03 LiCSBAS2 v2.0.0 支持 ARIA/NISAR GUNW);定位 = LiCSAR 产品的官方下游、大区域构造形变入门首选。

### 1.3 GAMMA(商业)

商业 SAR/InSAR/PSI 全链工具箱(MSP/ISP/DIFF&GEO/LAT/IPTA + GEO/TDBP 模块),LiCSAR 与 HyP3 全景 InSAR 的底层引擎([官网](https://gamma-rs.ch/gamma-software))。

1. **自动化程度**:刻意的"工具箱"哲学——数百个命令行程序,单跑或脚本串联,**无一键 App**;官方提供 demo 脚本作为半成品流水线。人工决策点遍布全链(专家软件);自动化程度取决于用户自己的脚本工程。
2. **参数管理**:每个产品伴随 `.par` 参数文件(处理元数据链条);demo 示例充当模板;无场景预设/参数推荐(培训课程承担了这个角色,2025 年 10 月 PSI 培训、12 月 SAR/InSAR 培训)。
3. **可复现性**:`.par` 文件保留每步处理元数据,程序回显参数;**流水线级 provenance 完全靠用户脚本纪律**;版本锁定通过商业发行版(半年一更)+维护合同实现,这一点其实比多数开源工具"硬"。
4. **错误处理**:商业支持(邮件/维护合同)+ 确定性 CLI;无自动诊断/重跑机制。
5. **界面形态**:CLI 为主 + Python/Matlab wrapper;2025 年中新增 **TS_DISP 模块**(QGIS 插件 + ArcGIS Pro 工具箱的时序查看器,IPTA 用户免费)([2025-07 release notes](https://www.gamma-rs.ch/uploads/media/upgrades_info_20250701.pdf));2023 年起有 GIS 模块。
6. **活跃度与定位**:商业健康([2025 年报](https://www.gamma-rs.ch/files/annual-report/annual_report_2025.pdf):新传感器支持包括 RCM、SAOCOM、Capella、StriX、UMBRA、LuTan-1、PALSAR-3、NISAR、BIOMASS、Sentinel-1C 等,维护合同数上升)。定位 = 专业机构的算法金标准与生产引擎。

### 1.4 StaMPS(+ 生态:SNAP2StaMPS / PyStamps)

PS/SBAS 时序经典(MATLAB),[原仓库](https://github.com/dbekaert/StaMPS) 最后一个 release 是 **2018 年的 v4.1-beta**,本体已停止维护。

1. **自动化程度**:预处理靠外部(ISCE/SNAP/GAMMA/DORIS),`mt_prep_*` 后在 MATLAB 里 `stamps(start_step, end_step)` 跑 1–8 步;选点/剔点(weed)、解缠检查、参考选择等多处需人工迭代调参。
2. **参数管理**:`setparm`/`getparm` 把参数持久化在 `parms.mat`——在它的年代算好的参数留痕;无预设/推荐。
3. **可复现性**:`parms.mat` + 处理日志部分可追溯;依赖 MATLAB 版本与 8 年未更新的代码;TRAIN 大气改正集成。
4. **错误处理**:纯手工(v4.1b 提供了手工修解缠错误的脚本);社区问答靠邮件列表/GitHub issues(51 个 open)。
5. **界面形态**:MATLAB CLI + `ps_plot` 绘图。
6. **活跃度与定位**:本体冻结,但**生态在延续**:[SNAP2StaMPS v2](https://github.com/mdelgadoblasco/snap2stamps)(SNAP 预处理桥,2025 年新增 SVA 旁瓣抑制附件)、**[PyStamps](https://github.com/korraitech/PyStamps)(Korrai 公司,2025-07 创建,Python 重写、对照 MATLAB 版基准验证、去 MATLAB 依赖)**。PyStamps 正是 insar-agent 选用的 PS 引擎,值得持续跟踪其成熟度(目前 25 star,商业公司背书)。

### 1.5 sarvey(FERN.Lab / 汉诺威 LUH)

面向**工程应用**(基础设施监测)的开源时序分析,构建在 MintPy(多视 SBAS)与 MiaplPy(单视 phase linking)之上([GitHub](https://github.com/luhipi/sarvey)、[文档](https://sarvey.readthedocs.io/main/readme.html))。

1. **自动化程度**:`sarvey -f config.json 0 4` 一条命令跑完 5 步(准备→一致性检查→解缠→滤波→致密化);预处理(MiaplPy 格式装载)在外部。**人工决策点**:相干阈值 `coherence_p1/p2`、`grid_size`、**one-step vs two-step 解缠工作流的选择**(小区域/大区域两条路线,文档明确给出选择指引——接近"场景预设"思想)。
2. **参数管理**:`-g` 生成默认 JSON 配置(按步骤分节),`sarvey -p` 自文档化列出全部参数说明;demo 教程演示"改 `coherence_p2` 0.8→0.7 后只重跑 3–4 步"。这是开源阵营里**参数管理体验最好**的之一。
3. **可复现性**:config JSON 即参数留痕;Zenodo 版本化发布(DOI);有 CI/测试/文档纪律(FERN.Lab 负责工程化)。无指纹/环境锁定。
4. **错误处理**:按步骤号手工重跑;无自动诊断。
5. **界面形态**:CLI(`sarvey`/`sarvey_plot`/`sarvey_export`)+ Python API;导出 Shapefile。
6. **活跃度与定位**:活跃——1.3.0(2026-02-23)、1.2.x(2025-07);**2026 年发了正式软件论文**(Piter et al., *Environmental Modelling & Software* 107004)+ IGARSS 2025;定位 = 工程测量师友好的 PS/DS 混合时序工具。

### 1.6 MiaplPy(insarlab)

全分辨率非线性 phase linking(PTA/EMI/EVD/sequential)时序包,MintPy 姊妹项目([GitHub](https://github.com/insarlab/MiaplPy))。

1. **自动化程度**:`miaplpyApp.py template.txt --dostep/--start` 分步执行(load_slc → phase_linking → unwrap → invert_network → 时序改正,后段交给 MintPy);决策点:patch 划分、SHP 窗口、解缠参数、网络选择。
2. **参数管理**:启动时自动生成 `miaplpyApp.cfg` + `custom_smallbaselineApp.cfg` 两个配置(MintPy 模板文化的延伸)。
3. **可复现性**:同 MintPy 风格(cfg + HDF5 属性);无正式 release 的版本管理偏弱(靠 git commit)。
4. **错误处理**:`--start` 手工续跑;GitHub issues(52 open);典型坑是 BLAS 多线程(官方建议 `OMP_NUM_THREADS=1`)——与我们在 Windows 上遇到的 MKL 崩溃同类。
5. **界面形态**:CLI。
6. **活跃度与定位**:开发活跃但无发布节奏:2025-03 合并"去 ISCE2 依赖"PR、2025-08 仍有 yunjunz 提交;定位 = 城市/基础设施高分辨率 DS 研究工具,也是 sarvey 的底层。

### 1.7 MintPy(基础设施地位,insar-agent 的核心引擎)

SBAS 时序分析事实标准,读 ISCE/ARIA/HyP3/GMTSAR/SNAP/GAMMA 等格式([GitHub](https://github.com/insarlab/MintPy))。

1. **自动化程度**:`smallbaselineApp.py` 十三步一键(参考点→loop closure→反演→各类改正→速度);`--dostep/--start/--end` 细粒度控制。**人工决策点**:参考点、网络修剪、大气改正方案选择、掩膜阈值——文档建议人工检查关键中间产物。
2. **参数管理**:`smallbaselineApp.cfg` 全参数默认模板 + custom 模板覆盖 + **`auto` 关键字机制**(把"推荐值"编码进默认);示例模板/示例数据齐全。无场景级预设。
3. **可复现性**:模板持久化在工作目录、输出 HDF5 属性携带处理元数据;**update 模式基于"产物存在性+时间戳"跳过已完成步骤**——是竞品中最接近"增量重算"的,但没有参数级失效传播:改一个参数后哪些下游要重跑,靠用户判断。无环境/版本锁定。
4. **错误处理**:异常即中止,用户改模板后 `--dostep` 重跑;社区支持极强(GitHub discussions)。
5. **界面形态**:CLI + `view.py`/`tsview.py` 交互查看器 + 丰富 Jupyter 教程。
6. **活跃度与定位**:v1.6.3(2025-11-24,支持 Sentinel-1C/SAOCOM/ALOS-4),2025-12 仍有核心开发者 PR;被 sarvey/MiaplPy/EZ-InSAR/ARIA/InSARHub 全部复用,**时序层的公共地基**。

### 1.8 ISCE2 → ISCE3 / OPERA 产品线(含 dolphin / disp-s1 / sweets)

**ISCE2**:干涉处理老将,JPL 已明确不再系统性维护(最后 JPL 主导版本 2.6.3/2023;[v2.6.4](https://github.com/isce-framework/isce2/releases/tag/v2.6.4) 2025-05 为社区 bugfix;[官方讨论区 2026-07 确认](https://github.com/isce-framework/isce2/discussions/28133) JPL 重心在 NISAR/ISCE3)。topsStack/stripmapStack 仍是学界主力:`stackSentinel.py` 生成 configs/ + run_files/,**按编号顺序手工执行 run 文件,失败重跑该 run 文件** = 它的全部"断点续跑";NumPy≥2.0 兼容仍未解决。→ 对 insar-agent 的含义:我们封装的 ISCE2 是一个**冻结中的引擎**,长期要有 ISCE3 路线图。

**ISCE3**:NISAR 任务处理框架(C++/CUDA + Python 绑定),YAML runconfig 驱动 RSLC/GSLC/GCOV/InSAR 工作流,conda-forge 分发,2025–2026 持续高频提交(无传统版本节奏,跟随 NISAR SAS 发布)([文档](https://isce-framework.github.io/isce3/))。

**OPERA DISP-S1(北美形变产品)**:2025 年发布并达 **validated** 状态(要求 ≥80% 站点满足 5 mm/yr,实测 >96% 通过;GNSS 直接对比 VA1 + 残差统计 VA2 双方法)([NASA Earthdata](https://www.earthdata.nasa.gov/data/catalog/asf-opera-l3-disp-s1-v1-1)、[arXiv 2511.12051](https://arxiv.org/abs/2511.12051));[ATBD Rev A v1.0.0(2026-03)](https://earthdata.nasa.gov/s3fs-public/2026-03/OPERA_DISP-S1_ATBD_D-108765_Rev_A_v1.0.0_Final.pdf) 公开全算法链;30 m、HDF5/Zarr、<72h 近实时 forward 模式。

**dolphin**(DISP-S1 的核心软件,[GitHub](https://github.com/isce-framework/dolphin)):
1. **自动化程度**:`dolphin config --slc-files ...` → `dolphin run config.yaml` 两条命令端到端(PS/DS phase linking → 解缠 → 时序);唯一必填输入是配准 SLC 路径。
2. **参数管理**:**YAML 全量配置文件,`config` 子命令自动生成带默认值的完整配置**——参数管理范式与我们最接近。
3. **可复现性**:论文明确主张 "sensor-agnostic, open-source implementation with **fully-reproducible data products**";Docker 镜像、conda/pip 版本化(0.41.0,2025-08)。
4. **错误处理**:工作流级重跑;**解缠器可插拔**(snaphu-py / isce3 的 PHASS/ICU / spurt 3D / tophu 多尺度)= 失败时换算法的架构预留。
5. **界面形态**:CLI + Python API。
6. **活跃度与定位**:非常活跃(JOSS 2024 论文、月度级发版);OPERA 生产级引擎,**开源阵营自动化+工程化天花板**。

**sweets**([GitHub](https://github.com/isce-framework/sweets)):AOI + 日期范围 + 轨道号一条命令 → 下载 burst/CSLC → 干涉 → 解缠 → 时序 → 速度图,配置 YAML 可往返序列化。**"一键"入口体验的当前最优参照**(但无失效传播/证据链,研究代码)。

### 1.9 ARIA-tools

NASA ARIA 标准 GUNW 产品的下载/裁剪/拼接/时序准备工具(**自身不做时序**,输出接 MintPy)([GitHub](https://github.com/aria-tools/ARIA-tools))。

1. **自动化程度**:`ariaDownload.py` → `ariaTSsetup.py` 两步把 GUNW 变成 MintPy 可加载的栈,自动判断拼接/裁剪;决策点:AOI、轨道、日期窗、质量筛选(`ariaMisclosure.py` 查相位三角闭合)。
2. **参数管理**:CLI 参数,无配置文件/预设。
3. **可复现性**:GUNW 产品本身版本化(DOI、CMR 元数据),工具侧无留痕机制;README 顶部 "RESEARCH CODE ... AS IS"。
4. **错误处理**:手工重跑;Jupyter 教程覆盖常见流程。
5. **界面形态**:CLI + [官方教程 notebook 库](https://github.com/aria-tools/ARIA-tools-docs)。
6. **活跃度与定位**:维护中(支持 NISAR GUNW 与 HyP3 路由生产的 ARIA-S1-GUNW);定位 = "标准产品 → 时序"的胶水层。

### 1.10 HyP3(ASF 云端按需处理)

insar-agent 已把 2–6 步日常托付给它,故重点看边界([文档](https://hyp3-docs.asf.alaska.edu/))。

1. **自动化程度**:提交像对(Vertex 网页/API/SDK)→ 云端自动跑 GAMMA(全景)或 ISCE2(burst)→ GeoTIFF zip;三条产线:全景 InSAR(GAMMA)、burst InSAR(ISCE2,支持 ≤15 个连续 burst 合并出图,暂只在 API/SDK)、ARIA frame GUNW(ISCE2)。**人工决策点:像对选择**(Vertex 有 SBAS 辅助配对)与少量参数;**时序分析不管**(用户自己接 MintPy,官方给 notebook)。
2. **参数管理**:参数极少而固定(looks 20x4/10x2/5x1、水体掩膜开关、相位滤波系数默认 0.6、DEM 固定 GLO-30)——**用"减参数"换稳健,和我们"参数三分类+推荐"是相反哲学**。
3. **可复现性**:**产品自描述做得最好**:zip 内含 README + 参数 txt(滤波器、解缠算法 mcf、DEM 源、参考点坐标等全记录);API 的 job 对象永久保留 `job_parameters` JSON + 日志 URL;处理插件(hyp3-gamma/hyp3-isce2)开源且版本化。**但产品 14 天(HyP3+ 30 天)过期删除,长期留存是用户责任;作业公开可见**。
4. **错误处理**:作业状态 FAILED + 日志链接;处理是幂等的——重新提交同参数作业即重跑;无诊断辅助。
5. **界面形态**:Vertex Web + REST API + Python SDK + 教程 notebook(OpenSARLab)。
6. **活跃度与定位**:非常活跃(2025–2026:multi-burst 上线并将进 Vertex、Sentinel-1D 支持先 GAMMA 后 ISCE2 补齐、8000 免费额度/月 + HyP3+ 付费);定位 = **"把 2–6 步产品化"** 的云标杆。

### 1.11 EZ-InSAR

爱尔兰 UCD/iCRAG 出品的"易用化包装层":自动下载 S1+DEM,串 ISCE2 → StaMPS/MintPy([GitHub](https://github.com/alexisInSAR/EZ-InSAR))。

1. **自动化程度**:GUI 建 job → 自动下载数据/轨道/DEM → 配准/干涉 → PSI/SBAS 时序 → 可视化,全程向导;能**自动检测 S1 最优参数**(frame/burst 选择);决策点:ROI、处理器选择(PS or SBAS)、时序参数。
2. **参数管理**:GUI 参数子应用 + 配置文件;无场景预设。
3. **可复现性**:**明确以 FAIR 原则为卖点**——论文原话:多处理器混用导致"参数即改即用、难以复现",解法是"对所有集成的开源处理器做一致且透明的参数日志"([EZ-InSAR-3 论文,ESI 2026](https://doi.org/10.1007/s12145-026-02115-9));日志文件 + live-log 面板。**是竞品中唯一把"参数留痕→可复现"写成产品主张的**,但只做到日志级,无指纹/级联/证据分级。
4. **错误处理**:日志排查 + 重跑;无自动诊断。
5. **界面形态**:GUI + CLI + Python API 三态(v3);MATLAB 版(v2.3.1 beta,2025-08)将被 Python 版取代(release 按 "Dec-2025.1" 日历命名)。
6. **活跃度与定位**:活跃(2026 年发 EZ-InSAR-3 论文);定位 = 教学/中小团队的"开源方法+商业级易用"。

### 1.12 RAiDER

对流层延迟改正专用(射线追踪),NISAR ST 资助([GitHub](https://github.com/dbekaert/RAiDER))。

1. **自动化程度**:`raider.py` YAML 配置一键算延迟立方体;`calcDelaysGUNW` 直接给 GUNW 产品叠加改正层(HyP3/ARIA 集成);决策点:天气模型选择(部分需许可)、插值方法。
2. **参数管理**:YAML 配置;天气模型凭据可全走环境变量(`setup_from_env`,为 CI 设计)。
3. **可复现性**:亮点——**把 provenance 元数据直接写进产物**:每个延迟层记录 `model_times_used`、`interpolation_method`、`scene_center_time`(v0.5.x release notes);Docker 镜像版本化。
4. **错误处理**:有明确的**降级语义**:HRRR 数据不可用时,若 GUNW 在 S3 上则"不加改正、成功退出",本地则抛 `ValueError`——区分"环境性缺数据"与"计算失败"的先例。
5. **界面形态**:CLI + notebook 教程。
6. **活跃度与定位**:活跃(v0.6.0,2025-09);定位 = 校正层供应商(第 8 步同行)。

### 1.13 云端全自动服务:P-SBAS/GEP 与 EGMS

**P-SBAS(CNR-IREA,跑在 ESA Geohazards Exploitation Platform)**([教程](https://terradue.github.io/doc-tep-geohazards-v2/tutorials/gep-sbas-s1.html)):网页上选 S1 SLC → 设 AOI/参考点/极化/阈值 → 云端全链 SBAS → CSV 时序 + GeoTIFF。**一键到最终时序**,决策点只剩输入选择与少数阈值;可复现性是黑盒(服务版本由供应方管理);失败=重新提交;定位 = 无算力用户的按需全链。

**EGMS(欧洲地面运动服务)**([Copernicus](https://land.copernicus.eu/en/products/european-ground-motion-service)):大陆尺度 PSI/DS 产品,年度更新,Basic/Calibrated/Ortho 三级;用户零处理,只消费;2025–2028 新框架合同将增加非城市区 DS 点密度并计划开放 API。与 OPERA DISP-S1 一起代表 **"InSAR 产品化、处理隐形化"** 的行业大趋势——**这类服务吃掉的是"标准需求",反衬出"非标准科研场景+可审计处理"的空间**。

### 1.14 PyGMTSAR / insar.dev

一人核心(A. Pechnikov)的纯 Python 生态([GitHub](https://github.com/AlexeyPechnikov/pygmtsar)、[insar.dev](http://insar.dev/)):

1. **自动化程度**:notebook 一键端到端(自动取 SLC/轨道/DEM → 干涉 → SBAS/PSI → 3D 可视化),在免费 Colab 甚至手机上可跑。
2. **参数管理**:Python API 默认值 + 示例 notebook 即模板。
3. **可复现性**:主打 **"可分享的可复现 notebook"**(Zarr 预处理栈放 Zenodo/GitHub,别人几分钟复算);但官网同时承认"频繁更新,请自行 pin 版本以保证复现"——版本锁定责任在用户。
4. **错误处理**:**[PyGMTSAR AI Assistant](https://insar.dev/ai)(ChatGPT 定制)负责讲理论、带示例、帮搭管线、排障**——是 InSAR 圈第一个正式挂牌的"AI 助手",但只做咨询,不执行、不留痕。
5. **界面形态**:Jupyter/Colab + Docker;新一代 insardev(Zarr v3,支持 NISAR)。
6. **活跃度与定位**:活跃(PyPI 2025.4.8);定位 = 教育/轻量科研的"到处能跑"。**对我们最有信息量的是它验证了"AI 助手 + InSAR"有真实需求,同时留下了'助手不落地执行'的空档**。

### 1.15 新兴直接同类:InSARHub

[GitHub](https://github.com/xzckay/InSARHub)(新项目,star 数≈0):自托管 Web UI 全流程——地图选 AOI 搜数据 → 交互式干涉网络编辑(按评分着色、拖拽增删边)→ 提交 HyP3(云)或本地/SLURM 跑 ISCE2 stackSentinel → **面板内监控作业、下载、重试失败作业** → MintPy 分步时序 → 导出;另有 CLI 供 HPC 批处理。**形态上与 insar-agent 的"Web + 双后端 + 分步执行"最接近**,但无 LLM 规划、无失效传播、无证据链、无 provenance;目前无社区。判断:方向撞车但深度差距大,值得每季度看一眼。

### 1.16 AI/agent + 遥感处理(2025–2026 新物种)

近一年井喷,但**全部停在"感知/分析层工具调用",没有一个编排真实 InSAR 重型处理链**:

- **Earth-Agent**(opendatalab,[ICLR 2026](https://github.com/opendatalab/Earth-Agent)):ReAct/POMDP 框架,**MCP 工具生态 104 个工具**(指数/反演/感知/分析/统计五件套),配 Earth-Bench(248 任务/13,729 图);评测协议分**端到端 + 逐步轨迹核查**(Tool-In-Order、参数正确率)。
- **OpenEarthAgent**(MBZUAI,[arXiv 2602.17665](https://arxiv.org/abs/2602.17665)):统一工具注册表(JSON schema)+ 中央编排器(校验参数、缓存中间产物、维护工作记忆);在 14,538 条**回放验证过的工具轨迹**上做 SFT。
- **OpenEarth-Agent**([arXiv 2603.22148](https://arxiv.org/abs/2603.22148)):从"调用工具"进到"**创造工具**"——五 agent 协作(数据摘要/规划/工作流/编码/检查),Coding Agent 按 DAG 逐节点生成脚本,上一节点执行成功才推进;OpenEarth-Bench 596 个全管线案例。
- **综述**:*Agentic AI in Remote Sensing*(WACV 2026 GeoCV,[arXiv 2601.01891](https://arxiv.org/abs/2601.01891)):单体 copilot(RS-Agent 等)vs 多 agent(GeoLLM-Squad,agentic correctness 60.29%);指出 grounding/安全/编排是短板;评测正从像素精度转向**轨迹正确性**。
- **InSAR 沾边的 LLM 应用**:Chat-with-point(Twente):对 EGMS 点时序做 LLM VQA(EGMS-Instruct 指令集,GPT-4 当教师蒸馏)——**解释侧**而非处理侧;PyGMTSAR AI Assistant——**咨询侧**。
- **结论**:"LLM 规划 + 真实科学计算引擎 + 可审计执行"的组合在 InSAR 领域**仍是空白**,insar-agent 目前没有正面对手;最近的威胁路径是 OpenEarth-Agent 式"编码 agent 现场造工具"往重型引擎方向演化,但它缺我们的执行器/证据链地基。

---

## 2. 对比矩阵(对象 × 6 维)

| 对象 | ① 自动化程度(一键到哪) | ② 参数管理 | ③ 可复现性(重点) | ④ 错误处理 | ⑤ 界面 | ⑥ 活跃度/定位(近一年) |
|---|---|---|---|---|---|---|
| **LiCSAR** | 全自动生产线,用户零操作;EIDP 自动响应地震 | 系统固定,用户不可调 | 内部 LiCSInfo 库;外部不可复现过程,只可复用产品 | 入库前自动质检剔坏图;坏 frame 团队重处理 | Web 门户(只下载) | 运行中;2025-10 存储迁移;公共产品线 |
| **LiCSBAS** | 一脚本 01→16(下载→滤波);目检惯例在 15/16 间 | batch 脚本变量;wiki 样例;无预设 | 追加式日志+脚本副本;**fork 碎片化伤复现** | 非零即停;改 start_step 手工续跑;FAQ 文化 | CLI + 交互查看器 | 活跃;LiCSBAS2 v2.0.0(2026-03)支持 NISAR |
| **GAMMA** | 工具箱,无一键;demo 脚本半成品 | .par 文件链;demo 即模板;培训补位 | .par 元数据链;流水线留痕靠用户;商业版本锁定较硬 | 商业支持;无自动机制 | CLI+Py/Matlab;新 TS_DISP(QGIS/ArcGIS) | 商业健康;年 2 更;新传感器全覆盖 |
| **StaMPS** | 8 步半自动,多处人工调参迭代 | parms.mat 持久化(setparm) | parms.mat+日志;代码 2018 年冻结 | 全手工;有手修解缠脚本 | MATLAB CLI | 本体停更;PyStamps(2025)Python 重生 |
| **sarvey** | 一命令 0→4 步;one/two-step 双工作流可选 | JSON 分节配置+`-p` 自文档;**最接近场景预设** | 配置即留痕;Zenodo DOI;CI/测试纪律 | 改配置按步号重跑;无诊断 | CLI + Python API | 活跃;1.3.0(2026-02)+EMS 2026 论文 |
| **MiaplPy** | miaplpyApp 分步;后段接 MintPy | 双 cfg 自动生成 | cfg+HDF5 属性;无 release 节奏 | --start 手工续跑;BLAS 坑文档化 | CLI | 提交活跃;全分辨率 DS 研究工具 |
| **MintPy** | smallbaselineApp 13 步一键 | cfg 模板+auto 关键字+示例 | 模板留痕;**update 模式=时间戳级跳步**;无参数级传播 | 异常中止;--dostep 重跑;社区强 | CLI+查看器+notebook | v1.6.3(2025-11);时序层公共地基 |
| **ISCE2** | stackSentinel 生成 run_files 手工顺序执行 | CLI 参数→configs 文件 | run 文件可留存;无元数据留痕 | 失败重跑对应 run 文件 | CLI | JPL 停维(社区 v2.6.4,2025-05);冻结引擎 |
| **ISCE3/OPERA** | dolphin 两命令端到端;DISP-S1 产品化(<72h NRT) | dolphin YAML 全量配置自动生成 | ATBD 公开+Docker+**"fully-reproducible products"主张** | 解缠器可插拔;工作流重跑 | CLI+产品门户 | 高频开发;validated 产品(2025);工程化天花板 |
| **ARIA-tools** | 下载→TSsetup 两步到 MintPy 栈 | CLI 参数,无配置 | GUNW 产品版本化;工具侧无留痕 | 手工重跑;notebook 教程 | CLI+notebook | 维护中;标准产品胶水层 |
| **HyP3** | 提交像对→云端出成品;时序自理 | 极少参数(减参哲学);job_parameters JSON | **产品自描述最佳**(README+参数 txt);但 14/30 天过期 | FAILED+日志 URL;重提交即重跑 | Vertex Web+API+SDK | 很活跃;multi-burst/S1D(2025-26);云标杆 |
| **EZ-InSAR** | GUI 向导:自动下载→干涉→时序 | GUI 参数应用+自动检测 S1 参数 | **FAIR 参数日志为主打卖点**(仅日志级) | 日志排查+重跑 | GUI+CLI+Py(v3 转 Python) | 活跃;EZ-InSAR-3 论文(2026) |
| **RAiDER** | YAML 一键延迟计算;GUNW 直接叠加 | YAML+环境变量凭据 | **provenance 写进产物属性**(model_times_used 等) | 缺数据时优雅降级(S3 成功退出/本地报错) | CLI | v0.6.0(2025-09);校正层供应商 |
| **P-SBAS/GEP** | 网页全链 SLC→时序 CSV | 网页表单少量参数 | 服务黑盒,版本由供方管理 | 重新提交 | Web | 运行中;无算力用户按需全链 |
| **EGMS** | 产品服务,零处理 | 无 | 产品版本化;过程不可见 | — | Web Explorer | 2025-28 新合同(DS 加密、API 计划) |
| **PyGMTSAR/insar.dev** | Colab notebook 一键端到端 | API 默认值+notebook 模板 | 可分享 notebook+Zarr 栈;**版本 pin 靠用户** | **AI 助手排障(咨询级)** | Notebook+Docker | 活跃(2025.4.8);教育/轻量 |
| **InSARHub** | Web 全流程:搜数→网络编辑→HyP3/ISCE2→MintPy | 表单+交互网络编辑 | 无 | **面板内重试失败作业** | 自托管 Web+CLI | 新生(0 star);形态最接近我们 |
| **EO agents(Earth-Agent 等)** | 分析层任务自动多步推理+工具调用 | 工具注册表 schema 校验 | 轨迹可回放(研究场景) | 检查 agent/迭代重试 | 研究框架 | 2025-26 井喷(ICLR/WACV);**不碰重型处理链** |
| **(对照)insar-agent** | 意图→规划→11 步编排;HyP3 或 ISCE2/WSL 双路线 | 注册表参数三分类+计划预览 | **三段指纹+SQLite 账本+run.sh 等价命令+证据阶梯** | **五阶段执行器:reattach/孤儿区分/双超时/级联标脏** | Web(NDJSON/SSE)+API | 开发中;可复现性与出版级证据链 |

---

## 3. 我们的差异化定位

### 3.1 全景对照下确认为独有的能力

1. **参数级失效传播 + 级联标脏原因分类**(method_changed/param_changed/upstream_changed/tool_upgraded/artifact_missing)。全场最接近的是 MintPy 的 update 模式,但那只是"产物存在性+时间戳";没有任何竞品能回答"改了 `min_coherence`,哪些下游失效、为什么"。
2. **六级证据阶梯 + 阈值台账来源纪律**(upstream_default/literature/local_calibration,PENDING 只警告并封顶)。竞品的"质量"要么是产品级验收(OPERA VA1/VA2,一次性、离线),要么是散落的 QC 脚本;**把"这个结果可信到什么级别、为什么"做成运行时一等公民的,只有我们**。
3. **五阶段执行器语义**:reattach 存活作业不重跑、崩溃窗口认领不双启动、孤儿(环境死亡)与计算失败严格区分。竞品的"续跑"全部是"看文件在不在/改 start_step"级别;RAiDER 的缺数据降级语义是唯一同类思想的碎片。
4. **run fork 参数试探**(改第 6 步方法,1–5 步零重算复用父产物)。竞品做参数对比 = 复制目录整链重跑(MintPy 教程明示此法);snakemake/DVC 有 DAG 缓存但没有进入任何 InSAR 工具。
5. **LLM 只做候选集内选择题 + Brain 可整层拔除**。EO agent 阵营是"LLM 全权决策"(幻觉风险直接进结果);PyGMTSAR AI 是"只咨询不执行";我们的"受限决策 + 无 LLM 降级路径"在两个极端之间,且有测试守护——这个位置目前没人占。
6. **诚实模拟模式 + 模拟标注**(引擎缺失时合成执行、全程标 simulated、证据封顶 runnable)。无对应物。

### 3.2 别人做得更好、我们要学的

1. **dolphin/OPERA 的工程化与算法可插拔**:两命令端到端、YAML 全量配置自动生成、解缠器四选一(snaphu-py/PHASS/spurt/tophu)、顺序 phase linking 增量处理(<72h NRT)、ATBD 文档纪律。我们的 registry/planner 架构对齐,但引擎深度与增量处理差距明显。
2. **HyP3 的产品自描述**:每个产物 zip 自带 README + 全参数 txt,job_parameters JSON 永久可查——"产物离开系统仍能自证来历"。我们的 provenance 在 SQLite 账本里,**应该像它一样把关键留痕冗余进产物包**(我们已有 run.sh,可再加 per-artifact 参数卡片)。
3. **sweets 的入口体验**:AOI+日期+轨道一条命令到速度图,数据搜索(burst/CSLC 自动发现)全自动。我们的 intent→plan 已覆盖"意图层",但**数据发现/自动选像对**还是弱项(现在靠 HyP3 手工选对或预置数据)。
4. **LiCSAR 的事件驱动自动化与质检阈值**:EIDP 从 USGS 目录自动触发同震处理;质检用"线检测伪影 + 归一化平均相干 + 解缠像素占比"三件套。前者是我们"自然语言意图"之外的另一种触发源(事件订阅),后者可直接充实我们第 11 步质检的指标库与阈值台账。
5. **sarvey 的参数自文档与场景双工作流**:`-p` 打印全参数说明、one-step/two-step 按场景选择并在文档中写清选择依据——这就是"场景预设"的雏形,值得抄进我们 registry 的参数描述与 planner 的收窄理由。
6. **MintPy 的 auto 关键字与模板注释文化**:把推荐值编码进默认模板,注释即文档。我们的参数推荐(planner 打分)可以显式输出"为什么这个值"到计划预览。
7. **EZ-InSAR 的 FAIR 叙事**:它已经把"参数透明日志=FAIR 复现"讲成了论文卖点——**这提醒我们:差异化主张要抢先用"证据阶梯>参数日志"的对比讲清楚**,否则外界会以为日志级留痕就是终点。
8. **EO agent 的轨迹级评测协议**(Earth-Bench 的 Tool-In-Order/参数正确率、OpenEarthAgent 的回放验证轨迹):可直接借来做我们 brain 的回归测试基准(候选集选择正确率、越界拒绝率)。
9. **PyGMTSAR 的可分享复现单元**:预处理栈(Zarr)+ notebook 上传 Zenodo,他人几分钟复算。我们的 run 导出(export)可以对齐这个体验:一个 zip = 数据指针 + 账本 + run.sh + 环境清单。
10. **OPERA 的验证方法学**:VA1(GNSS 投影对比)/VA2(随机像素对距离-残差统计)——我们 crossval_r 阈值标定可以直接采用 VA2 式统计而不必等 GNSS。

### 3.3 行业空白(没人做、且与我们主张同向)

1. **工作流级可审计复现**:全行业最好水平 = "配置文件 + 日志 + 产品元数据"(HyP3/dolphin/EZ-InSAR 各占一角);**没有人做"指纹 + 账本 + 等价命令 + 证据分级"的闭环**。出版级证据链的窗口仍然敞开。
2. **失败的语义化**:全行业的失败处理停留在"看日志、改参数、重跑";没有人区分"环境死亡 vs 计算失败 vs 质量不达标",更没有失效传播。这是科研工作流的真痛点(跑三天的栈崩在第 14 小时)。
3. **参数试探的増量复算**:改一个参数付整链代价,是所有 InSAR 时序工具的共同税。fork+复用一旦做实并配上真实引擎验证,是可展示的硬差异。
4. **LLM 编排重型科学计算且可审计**:EO agent 停在分析层;InSAR 圈的 AI 停在咨询层(PyGMTSAR)与解释层(Chat-with-point)。"自然语言 → 真实 ISCE2/MintPy 链 → 出版级证据"整条线没有第二个玩家。
5. **跨引擎桥的诚实接口**(isce2→pystamps 类):现状是 snap2stamps/prep_* 脚本各自为战、字段语义靠猜;把"桥的正确性边界"显式化没人做——但这也说明它难(我们自己也把它列为未实现)。

### 3.4 威胁与风险(诚实面)

- **产品化趋势侵蚀"处理"本身的价值**:OPERA DISP-S1 / EGMS 让"标准区域标准需求"用户直接拿 validated 产品,不再处理。我们的价值必须锚定在**非标准场景**(新事件、参数敏感研究、非覆盖区域、发表需要过程证据)——这与"出版级证据链"主张一致,但要说清楚。
- **dolphin 生态若向用户端下沉**(sweets 已是雏形),其工程质量 + JPL 品牌会形成强吸引;我们宜尽早支持 dolphin 作为可选引擎而非只对标。
- **ISCE2 冻结**:我们重型链押在停维引擎上,中期需要 ISCE3/dolphin 路线图。
- **EO agent 侧若把"编码 agent 现场造工具"(OpenEarth-Agent)接到真实 InSAR 引擎**,可能从另一个方向切入;但它们缺执行器/证据链地基,窗口期估计 1–2 年。

---

## 4. 借鉴清单(按价值排序)

| # | 借鉴项 | 来源 | 学什么/怎么落 | 对应模块 | 价值 |
|---|---|---|---|---|---|
| 1 | 产物自描述包 | HyP3 | 每个 run 导出 zip 内置 README+参数卡片+run.sh+环境清单,产物离开系统可自证 | report/ + api 导出 | **P0**(直接强化核心主张,成本低) |
| 2 | 质检指标三件套 + 阈值 | LiCSAR | 归一化平均相干、解缠像素占比、伪影线检测,进 contract.yaml 阈值台账(来源=upstream_default) | audit/ | **P0**(填 5 项 PENDING 的现成弹药) |
| 3 | VA2 式残差统计验证 | OPERA DISP-S1 | 随机像素对距离-速度差统计做无 GNSS 自验证,标定 crossval_r | audit/ 质量门 | **P0**(解开"PS 链未建成→crossval 缺席"的死结) |
| 4 | 解缠器可插拔 | dolphin | snaphu 之外预留 spurt/tophu 接口;解缠失败→planner 换方法重规划的素材 | engines/ + planner/ | **P1**(失效传播的天然演示场景) |
| 5 | 数据发现自动化 | sweets/HyP3 Vertex | AOI+日期→自动 burst/像对枚举与 SBAS 配对建议,接进 intent→plan | planner/ 前置 | **P1**(补"第 1 步之前"的断层) |
| 6 | 参数自文档 + 场景双工作流 | sarvey | registry 参数描述加"何时用/为什么";planner 输出场景化收窄理由(小区域 one-step 类比) | registry/ + planner/ | **P1** |
| 7 | 校正层 provenance 进产物属性 | RAiDER | ERA5 改正记录 model_times_used 等到 h5 属性,与账本互为冗余 | engines/mintpy + audit/ | **P1**(小改动,论文审稿人友好) |
| 8 | 轨迹级 brain 评测 | Earth-Bench/OpenEarthAgent | 固化"候选集选择正确率/越界拒绝率/参数正确率"回归集,回放验证 | tests/ + brain/ | **P1**(把"可拔除"升级为"可度量") |
| 9 | 事件驱动触发源 | LiCSAR EIDP | USGS ComCat 订阅→自动生成同震 run 草案(仅到计划预览,不自动执行,守重型计算规矩) | loop/ + planner/ | **P2**(演示价值高) |
| 10 | 可分享复现单元 | PyGMTSAR | run 导出对齐"Zenodo 可复算"体验;教学市场副产品 | report/ | **P2** |
| 11 | auto 关键字默认值 | MintPy | 参数推荐值显式标 auto 并在预览中解释推导 | registry/ | **P2** |
| 12 | dolphin 作为第 7 步可选引擎 | dolphin | 中期路线:MintPy SBAS 之外增 phase-linking 路线,对冲 ISCE2 冻结 | engines/ | **P2**(战略性,工作量大) |
| 13 | FAIR 话术对标 | EZ-InSAR | 对外材料用"日志级留痕(他们)vs 证据分级+指纹+账本(我们)"的阶梯对比图 | docs/ | **P2**(定位传播) |

---

## 附:本次调研主要来源

- LiCSBAS/LiCSAR:[comet-licsar/LiCSBAS](https://github.com/comet-licsar/LiCSBAS)、[yumorishita/LiCSBAS2](https://github.com/yumorishita/LiCSBAS2)、[COMET-LiCS 门户](https://comet.nerc.ac.uk/comet-lics-portal/)、Morishita et al. 2020、Lazecký et al. 2020
- GAMMA:[官网](https://gamma-rs.ch/gamma-software)、[2025-07 Release Notes](https://www.gamma-rs.ch/uploads/media/upgrades_info_20250701.pdf)、[2025 年报](https://www.gamma-rs.ch/files/annual-report/annual_report_2025.pdf)
- StaMPS 系:[dbekaert/StaMPS](https://github.com/dbekaert/StaMPS)、[snap2stamps](https://github.com/mdelgadoblasco/snap2stamps)、[korraitech/PyStamps](https://github.com/korraitech/PyStamps)
- sarvey:[luhipi/sarvey](https://github.com/luhipi/sarvey)、[readthedocs](https://sarvey.readthedocs.io/)、Piter et al. 2026(EMS 107004)
- MiaplPy/MintPy:[insarlab/MiaplPy](https://github.com/insarlab/MiaplPy)、[insarlab/MintPy](https://github.com/insarlab/MintPy)(v1.6.3)
- ISCE2/3 与 OPERA:[isce2 v2.6.4](https://github.com/isce-framework/isce2/releases/tag/v2.6.4)、[isce2 停维讨论](https://github.com/isce-framework/isce2/discussions/28133)、[isce3](https://github.com/isce-framework/isce3)、[dolphin](https://github.com/isce-framework/dolphin)(JOSS 2024)、[sweets](https://github.com/isce-framework/sweets)、[DISP-S1 数据集页](https://www.earthdata.nasa.gov/data/catalog/asf-opera-l3-disp-s1-v1-1)、[DISP-S1 ATBD Rev A(2026-03)](https://earthdata.nasa.gov/s3fs-public/2026-03/OPERA_DISP-S1_ATBD_D-108765_Rev_A_v1.0.0_Final.pdf)、[arXiv 2511.12051](https://arxiv.org/abs/2511.12051)
- ARIA-tools:[aria-tools/ARIA-tools](https://github.com/aria-tools/ARIA-tools)、[ARIA-tools-docs](https://github.com/aria-tools/ARIA-tools-docs)
- HyP3:[hyp3-docs](https://hyp3-docs.asf.alaska.edu/)、[Burst InSAR 产品指南](https://hyp3-docs.asf.alaska.edu/guides/burst_insar_product_guide/)、[InSAR 产品指南(参数表)](https://github.com/ASFHyP3/hyp3-docs/blob/develop/docs/guides/insar_product_guide.md)
- EZ-InSAR:[alexisInSAR/EZ-InSAR](https://github.com/alexisInSAR/EZ-InSAR)、Hrysiewicz et al. 2023、EZ-InSAR-3(ESI 2026, [doi](https://doi.org/10.1007/s12145-026-02115-9))
- RAiDER:[dbekaert/RAiDER](https://github.com/dbekaert/RAiDER)(v0.6.0)
- 云服务:[P-SBAS on GEP 教程](https://terradue.github.io/doc-tep-geohazards-v2/tutorials/gep-sbas-s1.html)、[EGMS](https://land.copernicus.eu/en/products/european-ground-motion-service)
- PyGMTSAR:[AlexeyPechnikov/pygmtsar](https://github.com/AlexeyPechnikov/pygmtsar)、[insar.dev](http://insar.dev/)
- InSARHub:[xzckay/InSARHub](https://github.com/xzckay/InSARHub)
- EO agents:[Earth-Agent(ICLR 2026)](https://github.com/opendatalab/Earth-Agent)、[OpenEarthAgent](https://github.com/mbzuai-oryx/OpenEarthAgent)、[OpenEarth-Agent(arXiv 2603.22148)](https://www.arxiv.org/abs/2603.22148)、[Agentic AI in RS 综述(arXiv 2601.01891)](https://arxiv.org/abs/2601.01891)

> 可信度说明:以上事实以 2026-08-12 可检索到的公开信息为准;版本号/日期均来自官方 release 页或论文;StaMPS 的 `parms.mat` 机制与 MintPy update 模式细节属工具常识+项目实测经验,未逐条对照源码;InSARHub 为极新项目,信息仅来自其 README。
