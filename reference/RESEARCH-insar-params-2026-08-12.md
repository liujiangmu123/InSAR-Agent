# RESEARCH:InSAR 处理参数的社区最佳实践与文献依据

- 日期:2026-08-12
- 目的:为 `src/insar_agent/audit/contract.yaml` 的 PENDING 阈值补充可引用的 ref,为
  `registry/scenario_packs/*/SKILL.md` 补充领域知识。**本文件只做知识沉淀,不改代码与 yaml。**
- 方法:网络检索一手来源 —— 软件源码/默认配置文件(可精确到参数名)、期刊论文(精确到章节/公式/表格)、
  官方产品文档与教程。所有数值均在本次调研中从原文核对,非二手转述。
- 来源分级(对齐 contract.yaml 的 source 纪律):
  - **[U] upstream_default** — 软件源码或官方默认配置文件里的确切默认值;
  - **[L] literature** — 期刊论文明确给出的数值/公式;
  - **[C] community practice** — 官方教程、产品指南、多篇论文汇聚成的惯例(引用时注明"惯例"而非"标准")。

## 快速导读:本项目最关心的结论

| 项目参数 | 社区锚点 | 一句话结论 |
|---|---|---|
| `min_coherence` = 0.25(解缠掩膜) | Berardino 2002 明文 "coherence threshold fixed to 0.25";GIAnT 惯例 0.25 | **可直接升级为 literature,PENDING 可解除** |
| `unwrap_coverage` = 0.70 | LiCSBAS 默认 `unw_cov_thre` = 0.3(下限惯例) | 有惯例可引,但 0.70 比社区惯例严,建议引用时注明"本项目从严" |
| `max_temporal_baseline` = 120 天 | ASF Ridgecrest 教程 24 天;GMTSAR 教程 50 天;MintPy 默认不限且官方推荐宽松阈值+冗余网络 | 120 天属宽松侧,可引 Yunjun 2019 §6.3 的"宽松阈值"主张,但冻土场景应显式收紧 |
| `corr_threshold` = 0.85(PS/SBAS 交叉验证) | 无任何文献惯例(互检惯例用"速度差 std 0.5–1.1 mm/yr"表述) | **维持 local_calibration 是诚实的**,可补"预期量级"引用 |
| `esd_coherence_threshold` = 0.85 | ISCE2 `topsApp.py` 源码 `ESD_COHERENCE_THRESHOLD default=0.85` | 已 OK,ref 可精确到源码参数 |
| `weightFunc` = "no" | MintPy 官方注释:"SBAS (Berardino et al., 2002) = minNormVelocity (yes) + weightFunc (no)" | 已 OK,ref 可补充这条官方注释 |

---

## 1. 相干性阈值

相干性阈值在流水线里出现在**四个不同层面**,社区各有不同的惯例数值,不可混用:

### 1.1 干涉图级筛选(网络构建时整幅剔除)

| 建议值 | 适用条件 | 出处 |
|---|---|---|
| 平均空间相干 < **0.7** 且不在 MST 上 → 剔除该干涉图 | SBAS 网络修剪(可选步骤,MintPy 默认关闭) | [U] MintPy `smallbaselineApp.cfg` §2 modify_network:`mintpy.network.coherenceBased = auto (no)`,`minCoherence = auto (0.7)`;[L] Yunjun et al. (2019) §4.2、§5.3.1;Chaussard et al. (2015, GRL) |
| 相干面积比 < **0.75** 且不在 MST 上 → 剔除 | 有效相干比法(阈值化面积比,对局部失相干更稳健) | [U] 同上 `minAreaRatio = auto (0.75)`;[L] Kang et al. (2021, RSE) |
| 图幅平均相干 < **0.05** 或解缠覆盖率 < **0.3** → 剔除 | LiCSBAS 图级质检(多视地理编码产品) | [U] LiCSBAS `batch_LiCSBAS.sh`:`p11_coh_thre 默认 0.05`、`p11_unw_thre 默认 0.3`;[L] Morishita et al. (2020, Remote Sensing 12(3):424) §2.4.1 |

要点:MintPy 的哲学是**保留 MST 保证网络连通**(`keepMinSpanTree = yes`),剔除只作用于冗余边;
剔除依据用"观测到的空间相干"而非"基线模型预测的相干"(Yunjun 2019 §5.3.1 对比了两者,前者在植被区更可靠)。

### 1.2 像元级空间相干掩膜(解缠前/反演前)

| 建议值 | 适用条件 | 出处 |
|---|---|---|
| **0.25** | SBAS 经典配置:强多视(4×20)后按像元筛选参与分析的像元 | [L] **Berardino et al. (2002, IEEE TGRS 40(11):2375-2383) §V**:"the coherence threshold has been fixed to 0.25"(44 景 ERS、70 对、多视后像元约 80×85 m) |
| **0.25** | GIAnT 系工具链的通用空间相干阈值 | [L] Yunjun et al. (2019) §6.5:"a spatial coherence threshold of 0.25 (as commonly done with GIAnT, Agram and Simons, 2015)" |
| **0.1** | 解缠有效性掩膜(比分析掩膜宽松,只排除完全失相干) | [U] ASF HyP3 InSAR Product Guide(GAMMA 链):"Any input pixel with a coherence value less than 0.1 is given a validity mask value of zero and not used during unwrapping" |
| **0.4** | MintPy 反演期掩膜阈值(仅当 `maskDataset = coherence` 时启用;默认**不掩膜**) | [U] MintPy `smallbaselineApp.cfg` §5 invert_network:`maskThreshold = auto (0.4)`,注释明确 "no - no masking [recommended]" |

要点:阈值高低取决于用途 —— **解缠掩膜宜低(0.1–0.3)**,过高会把解缠区域切碎成孤岛;
**统计/出图掩膜可高(0.4+)**。MintPy 推荐反演时不做空间相干掩膜,把可靠性判断留给时间相干(见 1.3)。
本项目第 6 步 `min_coherence = 0.25` 位于 Berardino/GIAnT 惯例点上,语义是解缠+分析掩膜,合理。

### 1.3 时序反演后的可靠像元:时间相干(temporal coherence)

| 建议值 | 适用条件 | 出处 |
|---|---|---|
| **≥ 0.7** | SBAS 反演后可靠像元掩膜(MintPy 默认;也是 EMCF 系文献惯例) | [L] **Pepe & Lanari (2006, IEEE TGRS 44(9):2374-2383)**(时间相干原始定义);[U] MintPy `smallbaselineApp.cfg` §5:`minTempCoh = auto (0.7)`;[L] Yunjun et al. (2019) 式(3)及 Fig. 15(默认 0.7 的优劣讨论) |
| **≥ 0.8** | 网络冗余度被削弱时(例如先做过空间相干阈值化,单像元干涉图数变少,时间相干虚高) | [L] Yunjun et al. (2019) §6.5.5:与 GIAnT 对比实验改用 0.8 并说明原因 |
| 掩膜后可靠像元数 ≥ **100** | 掩膜有效性的保底检查 | [U] MintPy `minNumPixel = auto (100)` |

### 1.4 参考点与"最小相干像元比例"

- 参考点自动选取:相干 ≥ **0.85** 的像元中随机选 —— [U] MintPy `smallbaselineApp.cfg` §3:
  `mintpy.reference.minCoherence = auto (0.85)`;人工选点准则见 [L] Yunjun et al. (2019) §4.3
  (高相干、无强湍流、贴近 AOI 且高程相近)。
- "时序反演最小相干像元比例"**没有统一的社区比例值**。可引用的替代惯例:
  - MintPy 用**绝对数**(minNumPixel = 100)而非比例;
  - LiCSBAS 用**图级覆盖率 0.3**(§1.1)+ 像元级 `n_unw ≥ 1.5 × 影像数`(`p15_n_unw_r_thre 默认 1.5`,
    反演期 `p13_n_unw_r_thre 默认 1`)—— [U] `batch_LiCSBAS.sh`;[L] Morishita et al. (2020) Table 3(噪声指标定义)。
  - 本项目若需要"velocity 有效像元比例"门,建议定位为 local_calibration,引用上述两条作类比而非直接依据。

---

## 2. Goldstein 滤波强度 alpha

原始定义:[L] **Goldstein & Werner (1998, GRL 25(21):4035-4038)** —— 频域谱平滑加权指数,
α ∈ [0,1],0 = 不滤波,1 = 最强滤波。α 过大损失分辨率/产生假条纹,过小抑噪不足。

### 2.1 各软件默认值一览(全部核对过源码/官方文档)

| 软件 | 默认 α | 备注 | 出处 |
|---|---|---|---|
| ISCE2 topsApp | **0.5** | `FILTER_STRENGTH default=0.5` | [U] isce2 `applications/topsApp.py`(参数 `filter strength`) |
| SNAP S1TBX | **1.0**(FFT 64,窗口 3) | 官方默认被文献批评为过强 | [U] SNAP GoldsteinPhaseFiltering 操作器默认参数;[L] IGARSS 2023 论文《A Discussion on the Goldstein Filtering Parameters Within the SNAP Software》(doi:10.1109/IGARSS52108.2023.10282000):默认值"excessively high",产生十字状伪影,**建议改 0.5–0.6** |
| ASF HyP3(GAMMA adf) | **0.6** | 范围 0–1;官方指导:"应 >0.2,极低相干对趋向 1" | [U] HyP3 InSAR Product Guide "Adaptive Phase Filter" 节 |
| LiCSAR(GAMMA adf) | **1.0** | 前置了 20×4 强多视(46×56 m),再强滤波 | [L] Morishita et al. (2020) §2.1 |
| MintPy Galápagos 基准案例 | **0.2** | S1 多视 15×5 后轻滤波 | [L] Yunjun et al. (2019) §5.1(配置文件随论文公开) |
| 文献通用折中 | **0.5** | "α=0.5 is normally used to ensure a balance" | [L] Sensors 16(11):1976 (2016) 综述引语(引 Goldstein 原文与后续实践) |

### 2.2 场景差异建议

| 场景 | 建议 α | 依据 |
|---|---|---|
| 城市/裸岩高相干 | **0.2–0.5**(轻滤波,防过滤) | [L] Baran et al. (2003, IEEE TGRS 41(9), doi:10.1109/TGRS.2003.817212):改进滤波器令 **α = 1 − 平均相干**,动机即"防止高相干区被过滤";HyP3 指导(>0.2) |
| 植被/低相干面状区 | **0.6–1.0**(强滤波利于解缠) | [U] HyP3 Product Guide:"interferograms with very low coherence will benefit from higher values (closer to 1)";LiCSAR 全球产品用 1.0 |
| 大形变梯度(同震近场密集条纹) | 宁可**多视换 SNR,α 保持中低**;强滤波会把密条纹平滑成假条纹 | [L] IGARSS 2023(十字伪影随 α 与 FFT 尺寸增大);[U] MintPy 载入注释警告 mean/median 多视会"smoothen the unwrapping errors, breaking the integer 2π relationship"(同理:滤波是不可逆的相位改写) |
| 自适应方案 | α = 1 − γ̄(逐 patch) | [L] Baran et al. (2003);注:相干 < 0.3 时与固定 α 的原始滤波几乎无差别(同文结论) |

要点:α 与 FFT 窗口尺寸联动(IGARSS 2023 认为两者是最敏感参数);跨软件比较 α 数值时要注意
前置多视强度不同(LiCSAR 1.0 是在 20×4 多视之后,不可直接照搬到单视干涉图)。
本项目 capabilities 第 5 步默认 `alpha = 0.4` 落在 ISCE2(0.5)与 MintPy 示例(0.2)之间,属合理折中,可引 ISCE2 默认作 upstream 锚点。

---

## 3. snaphu 解缠:cost mode、tile、误差检测

snaphu 官方文档(均已核对原文):
man page <https://web.stanford.edu/group/radar/softwareandlinks/sw/snaphu/snaphu_man1.html>;
完整配置模板 <https://web.stanford.edu/group/radar/softwareandlinks/sw/snaphu/snaphu.conf.full>。
理论依据:[L] Chen & Zebker (2000, JOSA A 17:401-414;2001, JOSA A 18:338-351;2002, IEEE TGRS 40:1709-1719)。

### 3.1 cost mode 的选择依据

| 模式 | 统计假设 | 何时用 | 出处 |
|---|---|---|---|
| TOPO(默认) | 相位 = 地形 | 仅 DEM 生成;形变应用不用 | [U] man page:"topography mode ... This is the default";`snaphu.conf.full`:`STATCOSTMODE TOPO` |
| DEFO(`-d`) | 相位 = 形变,允许有限不连续(`DEFOMAX_CYCLE` 默认 **1.2 周**) | **同震近场**:破裂带两侧允许相位跳变 | [U] man page `-d`;`snaphu.conf.full`:`DEFOMAX_CYCLE 1.2`、`DEFOTHRESHFACTOR 1.2`、`DEFOCONST 0.9` |
| SMOOTH(`-s`) | = DEFO 且 DEFOMAX=0(不允许不连续) | **缓变形变**:震间、冻土季节形变、蠕滑滑坡、时序小形变 | [U] man page:"If the surface displacement varies slowly and true discontinuities are not expected at all, DEFOMAX_CYCLE can be set to zero. This behavior is also invoked with the −s option" |

**ISCE2 的 `snaphu_mcf` 实际是什么**(易被误解,本次已核对源码):

```python
# isce2 components/isceobj/TopsProc/runUnwrapSnaphu.py
def runUnwrapMcf(self):
    runUnwrap(self, costMode='SMOOTH', initMethod='MCF', defomax=2, initOnly=True)
```

即 **SMOOTH 代价 + MCF 初始化 + initOnly=True(只做初始化解,不跑迭代优化器)**。
"MCF"指初始化算法(相对 MST 默认,[U] man page `--mst`/`--mcf`),不是一种 cost mode。
topsApp 的 `unwrapper_name` 默认其实是 `icu`,教程普遍改用 `snaphu_mcf`([C] ISCE2 官方 EarthScope 2024 topsApp 教程)。
HyP3 GAMMA 链同样用 MCF 算法 + 三角网([U] HyP3 InSAR Product Guide)。
→ 对本项目第 6 步:`snaphu_mcf`(推荐项)与 `cost_mode` 参数(SMOOTH/DEFO/TOPO)正交,与 ISCE2 语义一致;
quake 场景近场若出现跨断层解缠错误,应试 DEFO + defomax>0 的完整优化,而非换 init。

### 3.2 tile 参数惯例

| 参数 | 默认 | 建议 | 出处 |
|---|---|---|---|
| `--tile ntilerow ntilecol rowovrlp colovrlp` | 1×1(不分块)、overlap 0 | 大幅面(S1 全幅多视后仍 >5k×5k)分块并行;man page 示例 `--tile 3 4 30 30 --nproc 2`;overlap 数十像元起步 | [U] man page OPTIONS/EXAMPLES;`snaphu.conf.full`:`NTILEROW/NTILECOL 1`、`ROWOVRLP/COLOVRLP 0` |
| `NPROC` | 1 | 与 tile 数匹配的并行进程数 | [U] 同上 |
| `TILECOSTTHRESH` | **500** | 可靠区域边界代价阈值,越大越保守越慢 | [U] `snaphu.conf.full` |
| `MINREGIONSIZE` | **100** px | tile 内可靠区域最小尺寸 | [U] 同上 |
| `TILEEDGEWEIGHT` | **2.5** | tile 边界次级弧的额外权重 | [U] 同上 |
| `-S`(SINGLETILEREOPTIMIZE) | FALSE | tile 初始化 + 全幅重优化,兼顾速度与一致性(官方推荐的提速路径) | [U] man page `-S` |
| 内存 | — | 单 tile 模式约 **100 MB / 百万像素** | [U] snaphu 主页(Stanford) |

连通分量(供 MintPy 桥接/闭合修正用):`-g` 输出;`MINCONNCOMPFRAC 0.01`、`CONNCOMPTHRESH 300`、
`MAXNCOMPS 32`([U] `snaphu.conf.full`)。MintPy 端 `connCompMinArea = auto (2.5e3)` 像元
([U] `smallbaselineApp.cfg` §4)。

### 3.3 解缠误差检测惯例(闭合环)

- 原理:三元组闭合相位的**整数模糊** `C_int`(式 9)非零 ⇔ 存在解缠错误;逐像元统计非零三元组数
  **T_int** 作为解缠误差指示图 —— [L] **Yunjun et al. (2019, Computers & Geosciences 133:104331)
  §3.2 式(8)-(9)、Fig. 3d-e**;概念可上溯 [L] Biggs et al. (2007)。
  MintPy 的 quick_overview 步骤即输出该指标([U] `smallbaselineApp.cfg` "quick_overview" 注释)。
- 图级剔除阈值:闭合环相位 **RMS > 1.5 rad** 判为问题环;某干涉图的所有环都有问题 → 整幅剔除 ——
  [U] LiCSBAS `p12_loop_thre 默认 1.5 rad`;[L] Morishita et al. (2020) §2.4.2(含"环相位非严格为零
  (多视/滤波/土壤水分)"的告诫)。配套像元级指标:`n_loop_err 默认 5`、`n_ifg_noloop 默认 50`(§7 表)。
- 修正(而非剔除)的适用条件 —— [L] Yunjun et al. (2019) §3、§7 结论 2:
  - **bridging**:适合被窄失相干带(河流/水体)分隔的可靠区域(如群岛);
  - **phase_closure**:适合高冗余网络;序贯 3/5/10 连接时,可完全修正的错误干涉图占比上限分别为
    **5% / 20% / 35%**(最大 2 周误差);
  - MintPy 默认两者都关(`unwrapError.method = auto (no)`),按场景显式开启。
- 本项目第 11 步 `loop_closure` 方法的阈值,建议直接采用 LiCSBAS 的 1.5 rad 惯例作为 literature 锚点。

---

## 4. 大气校正:ERA5 / GACOS 的适用条件与残差量级

### 4.1 为什么必须校正(误差量级)

- [L] **Zebker, Rosen & Hensley (1997, JGR 102(B4):7547-7563)**:相对湿度 20% 的时空变化
  → 形变产品 **≈10 cm** 误差(地形产品最差基线可达 ~100 m);湿区(如夏威夷)是主导误差;
  多干涉图平均可压到 ~1 cm。
- 对流层延迟分两类:**分层(与高程相关)+ 湍流**。分层项在山区/高原与地形强相关,会伪装成
  与地形相关的形变;湍流项空间尺度数 km—数十 km([L] Yu et al. 2018 JGR 引言;Jolivet et al. 2014)。

### 4.2 ERA5 / 全球大气模型(GAM)路线

| 事实 | 数值 | 出处 |
|---|---|---|
| MintPy 默认开启 pyaps+ERA5 | `troposphericDelay.method = auto (pyaps)`,`weatherModel = auto (ERA5)`,注释 "recommended and turn ON by default" | [U] MintPy `smallbaselineApp.cfg` §8 |
| ERA-I 校正效果(青藏昆仑) | 短时基线干涉图 APS 平均削减 **73%** | [L] Jolivet et al. (2011, GRL 38, L17311) |
| ERA-I 校正效果(洛杉矶) | 单景方差削减 **≈70%**(湿延迟贡献 ~55%,静力项 ~15%,不可忽略) | [L] **Jolivet et al. (2014, JGR 119:2324-2341, doi:10.1002/2013JB010588)** §3 |
| 失效条件 | 湍流主导时效果差;平坦/湍流区甚至恶化(天山案例:ERA-I 校正后 STD 反升) | [L] Jolivet et al. (2014) §5(效果"primarily controlled by the level of turbulence");[L] Remote Sensing 9(8):765 (2017) 天山对比;[L] Int J Appl Earth Obs 111:102822 (2022):ERA5≈ERA-I,夏季/局地湍流最难 |

### 4.3 GACOS 路线

| 事实 | 数值 | 出处 |
|---|---|---|
| 模型 | ITD(迭代对流层分解)融合 ECMWF HRES 0.125°/137 层/6 h + GNSS ZTD(5 min) | [L] **Yu et al. (2018, JGR 123:9202-9222, doi:10.1029/2017JB015305)**;官网 <http://www.gacos.net> |
| 校正效果 | 8 幅全球分布干涉图:相位 StdDev 平均改善 GPS/ECMWF/融合 = **47%/49%/54%**;对 GPS 位移 RMS 改善 **55%/45%/63%**;校正后精度 **≈1 cm** | [L] Yu et al. (2018 JGR) §4;[L] Yu et al. (2018, RSE 204:109-121):加州/英格兰 **45–78%** 噪声削减 |
| 适用性判据 | 产品自带可行性指标:GNSS/ECMWF 交叉 RMS、相位-延迟相关、时间差、地形起伏 —— **官方立场是"先看指标再决定是否用"** | [L] Yu et al. (2018 JGR) §5;GACOS 官网 FAQ |
| 时序案例 | LiCSBAS 日本案例:GACOS 校正后 InSAR−GNSS 速度差 STD **2.4 → 1.9 mm/yr**;远离参考点的点受益最大(季节性假信号消除) | [L] Morishita et al. (2020) §3.4 |

### 4.4 什么情况必须校正 / 可降级

- **必须校正**:信号量级 ≦ 大气噪声的场景 —— mm/yr 级慢速形变(震间、冻土、滑坡蠕滑)、
  与地形相关的信号(高原/山区,分层延迟与形变混淆)、依赖长波长信号的应用。
  依据:上表误差量级(单景 cm 级大气 vs mm/yr 信号)+ Morishita 2020 的速度差改善实测。
- **相对不敏感**:同震形变数十 cm,大气 cm 级扰动不改变一阶结论(但精化滑动分布仍需校正)。
- **降级链**(证据级别递减):GACOS/ERA5 → 高程相关经验校正
  (Doin et al., 2009, J. Applied Geophysics;[U] MintPy `height_correlation`)。
  经验法**无法区分与地形相关的真实形变**(火山、冻土坡面),MintPy cfg §8 注释与 Yunjun 2019 §4.6 均明示。
- L 波段(ALOS)补充:**电离层**常大于对流层,用 split-spectrum 校正 ——
  [L] Fattahi et al. (2017, IEEE TGRS,条带);Liang et al. (2019, IEEE TGRS,TOPS);
  [U] MintPy cfg §7(默认关,"optional but recommended")。
- 固体潮:S1 大区域长时序才显著,[L] Yunjun et al. (2022, IEEE TGRS);[U] MintPy `solidEarthTides = auto (no)`。

---

## 5. SBAS 网络设计:时空基线阈值与冗余度

### 5.1 经典与卫星平台惯例

| 平台/出处 | 垂直基线 | 时间基线 | 备注 |
|---|---|---|---|
| ERS 经典 SBAS —— [L] **Berardino et al. (2002) §V** | **< 130 m** | (按子集划分) | 44 景/70 对/3 子集;配套 0.25 相干阈值、4×20 多视 |
| Sentinel-1 轨道设计 —— [U] ESA 技术说明 ESA-EOPG-EOPGMQ-TN-2024-12《Increase of Sentinel-1A Orbital Tube》 | 轨道管设计直径 **100 m (RMS)**,2025 起放宽至 200 m;地面轨迹死区 ±120 m;12 天对 bperp < ~165 m | 12/24/36 天对 bperp 均 < 300 m | S1 天然小基线,**bperp 通常不是 S1 选对的约束项** |
| S1 P-SBAS —— [L] Manunta et al. (2019, IEEE TGRS 57(9), doi:10.1109/TGRS.2019.2904912) | S1 bperp 标准差 **50 m**(对比 Envisat 360 m) | 用最大连接数约束替代 | "intrinsically small baseline system" |
| ASF HyP3+MintPy Ridgecrest 官方教程 —— [C] `hyp3-docs/tutorials/hyp3_insar_stack_for_ts_analysis.ipynb` | (HyP3 产品不限) | **max_temporal_baseline = 24 天** | **与本项目 quake 场景同数据源同工作流,最贴身的惯例** |
| GMTSAR S1 时序教程(Kilauea)—— [C] `sentinel_time_series_5.pdf` | **100 m** | **50 天** | `select_pairs.csh ... 50 100` |
| 文献综述 —— [L] RSE 249:111941 (2020) Table 1 | 数百 m 至 >1000 m | 数月至数年 | 例:Goel & Adam (2014) 150 m / 9 个月;结论:阈值本质是"最大化对数 vs 最小化失相干"的权衡 |
| MintPy 默认 —— [U] `smallbaselineApp.cfg` §2 | `perpBaseMax = auto (no)` 不限 | `tempBaseMax = auto (no)` 不限 | 官方主张**宽松阈值 + 冗余网络**,再用数据驱动修剪(§1.1) |
| ALOS(L 波段)—— [L] Yunjun et al. (2019) §5.1 | **< 1800 m** | **< 1800 天** | 另附方位向 Doppler 重叠 >15% 条件;其他 L 波段研究常用 1000 m/2000+ 天量级 |

L 波段长基线的代价([L] Wang et al. 2024, ionospheric compensation 评估,IRD 报告库):
DEM 误差随大 bperp 放大(需 [L] Fattahi & Amelung 2013 的 DEM 误差校正),电离层为主要残差。

### 5.2 冗余度与连接数

- MintPy 反演约束:每个 SAR 日期最少参与 **1** 幅干涉图(`minRedundancy = auto (1.0)`,[U] cfg §5)——这是下限而非建议值。
- [L] **Yunjun et al. (2019) §6.3 + 结论 3**:提高冗余(序贯连接数)提升反演精度与时间相干可辨识度;
  **官方建议"宽松阈值、更多连接"**;其 S1 基准案例用**序贯 5 连接**;闭合修正能力随冗余上升(5/20/35% @ 3/5/10 连接,结论 2)。
- 网络健康度量(可进质检):LiCSBAS 噪声指标 `n_gap`(网络断点数,默认掩膜阈值 10)、
  `maxTlen`(连通网络最大时长,默认 ≥1 yr)—— [U] `batch_LiCSBAS.sh`;[L] Morishita et al. (2020) Table 3。
- 冻土/植被区注意:**跨冻融季/生长季的对相干性系统性下降**,序贯短时基线对优先
  (permafrost 文献普遍做法,见 §6.3;LiCSBAS 亦支持季节性选对)。

---

## 6. 形变模型拟合:标准形式与参考文献

MintPy 的时间函数框架([U] `smallbaselineApp.cfg` §12,理论出处 [L] **Hetland et al. (2012, JGR 117, B02404)
式(2)-(9)**,MInTS 方法):`polynomial`(默认 1 阶)+ `periodic`(年/半年,`1,0.5`)+
`stepDate` + `exp`/`log`(onset + 特征时间天数,可叠加多个)。
不确定度:residue 法(默认,[L] Fattahi & Amelung 2015, JGR)/ covariance / bootstrap(默认 400 次)。

### 6.1 同震阶跃

- 标准形式:Heaviside 阶跃 `H(t − t_eq)`,MintPy `stepDate = YYYYMMDD(THHMM)`。
- Ridgecrest 实测配置 `stepFuncDate = 20190706T0320` 已是 A 级来源(主震 2019-07-06 03:19:53 UTC)。
- 只拟合线性会把阶跃摊成假趋势 —— 这是 MintPy `timeseries2velocity` 支持 step 的动机([U] cfg §12 注释)。

### 6.2 震后:对数(余滑)与指数(黏弹)

| 形式 | 机理 | 标准式 | 出处 |
|---|---|---|---|
| 对数 | 余滑(速率-状态摩擦稳态近似) | `d(t) = A·ln(1 + t/τ_log)` | [L] **Marone, Scholz & Bilham (1991, JGR 96(B5):8441-8452)**;[L] Ingleby & Wright (2017, GRL 44, doi:10.1002/2017GL072865):全球 151 例证实 Omori 型 1/t 衰减,对数拟合是社区默认 |
| 指数 | 黏弹性松弛 | `d(t) = C·(1 − e^{−t/τ_exp})` | [L] 同上二文的对照机理;教科书式对照见 Sobrero et al. (2020, J Geodesy 94:9) |
| 组合(推荐给 Mw≥7 大震) | 余滑早期主导 + 黏弹长期主导 | `d(t) = a·ln(1+t/b) + c·(1−e^{−t/τ}) + V·t` | [L] **Tobita (2016, Earth Planets Space 68:41, doi:10.1186/s40623-016-0422-4)**(Tohoku 案例,给出全局非线性最小二乘解法) |

实践告诫([L] Sobrero et al. 2020;Tobita 2016):对数与指数在观测窗内常**近似不可辨识**,
特征时间 τ 与振幅强相关;InSAR 时序上先固定 τ 网格搜索、报告拟合残差而非宣称机理归因。
MintPy 语法示例:`mintpy.timeFunc.log = 20190706,60`(onset,τ 天)。

### 6.3 冻土季节冻融

- 一阶标准式(社区默认):`d(t) = a·t + b·sin(2πt/T + φ₀) + c`,T = 1 年 ——
  [L] Heihe 案例综述式(3)(JGR-ES 128, doi:10.1029/2022JF006782,引 Zhang et al. 2019);
  [L] Li et al. (2019, Remote Sensing 11(9):1000,青藏高原 S1):正弦季节模型,峰-峰季节位移
  **40–80 mm**;[L] 赵蓉等 (2013, 地球物理学报 56(5),SBAS+周期模型)。
- 进阶物理模型:**度日模型(Stefan)** —— 形变 ∝ √(累计融化/冻结度日),冻结期平台;
  [L] **Daout et al. (2017, GRL 44, doi:10.1002/2016GL070781)**:正弦是"一阶线性拟合",
  完整季节循环数据显示冬季无形变的**非正弦不对称** —— 这正是 `poly_periodic(periods=[1,0.5])`
  中**半年项**存在的依据(傅里叶二阶项吸收不对称);[L] Liu et al. (2012, JGR)首创 Stefan 用法。
- 数据跨度:周期项可靠估计需 ≥2 个完整年循环(Daout 2017 用 8 年数据估计模型参数的教训;
  Li et al. 2019 明示观测 <14 个月时线性项不可分离)。

### 6.4 滑坡蠕滑

- 标准产品是线性速率(LOS),蠕滑期准匀速;加速(临滑)表现为线性拟合残差系统性增大,
  应报警而非换高阶模型拟合掉 —— 与 [L] Colesanti & Wasowski (2006, Engineering Geology 88:173-199)
  的"缓慢滑坡 InSAR 适用性"框架一致(极慢速 mm/yr 级滑坡是 MT-InSAR 的适用域)。

---

## 7. 质量评估指标:可接受量级(点名文献)

### 7.1 速度标准差 / 速度精度

| 指标 | 数值 | 出处 |
|---|---|---|
| SBAS 速度精度(ERS 40–60 景) | **≈1 mm/yr**;时序单历元 std ≈**5 mm**(vs 水准 4.7 mm,vs GPS 6.9 mm) | [L] **Casu, Manzo & Lanari (2006, RSE 102:195-210, doi:10.1016/j.rse.2006.01.023)** 结论节 |
| PS 速度精度(ERS 长档案) | **< 1 mm/yr**,最优 **0.1–0.5 mm/yr**(罗马 70 景案例后验 0.25 mm/yr);PS 候选 D_A ≤ **0.25**;最少 **≥20–25 景** | [L] **Ferretti, Prati & Rocca (2001, IEEE TGRS 39(1):8-20, doi:10.1109/36.898661)** §III/§V;[L] PSI 综述 (2016, doi:10.1007/s40534-016-0108-4):"minimum number of 25 SAR images" |
| S1 时序速度 std 演化 | 24 天采样需 **≈2.2 年**收敛到 2 mm/yr(6/12/70 天:1.4/1.8/3.1 年);InSAR−GNSS 速度差 STD 收敛至 **≈2 mm/yr** 而非 0 | [L] **Morishita et al. (2020, Remote Sensing 12(3):424)** §3.5 与式(4) |
| 泛欧产品规范 | EGMS 平均速度 STD **0.7 mm/yr**(产品规格);GNSS 验证:绝大多数速度差 **< 2 mm/yr** | [U] EGMS Product User Manual (2022) 质量表;[L] IGARSS 2024 EGMS 验证(doi:10.1109/IGARSS53475.2024.10641305) |
| 互检惯例(多处理器一致性) | Terrafirma:速度差 std **0.5–0.7 mm/yr**、时序差 **1.5–5.6 mm**;Glasgow S1 四方法互检:速度差 std 平均 **1.1 mm/yr** | [L] Terrafirma 验证(Adam et al. 2009,转引自下文);[L] **RSE 256:112306 (2021)《Benchmarking and inter-comparison of Sentinel-1 InSAR velocities and time series》** |
| LiCSBAS 掩膜默认 | `vstd ≤ 100 mm/yr`(宽松的粗差门,非精度门) | [U] `batch_LiCSBAS.sh` p15 |

### 7.2 与 GNSS 对比的 RMSE

- [L] **Yunjun et al. (2019) §5.1 Fig. 8**:Sierra Negra(强形变火山)S1 3.5 年,InSAR vs GPS 时序
  R² = 1.0,**RMSE 0.5–1.8 cm**;失效站 GV10(RMSE 3.9 cm)被时间相干 0.64 < 0.7 自动剔除 ——
  演示了"质量指标应能预先识别坏点"。
- [L] Morishita et al. (2020):日本平原案例 InSAR−GNSS 速度差 STD 1.9 mm/yr(GACOS 后)。
- 可接受量级参考:时序 RMSE **≈1–2 cm**(单历元)、速度差 **≈1–2 mm/yr**(数年 S1)是社区
  "结果可信"的常见报告区间(上两行 + §7.1 EGMS/Terrafirma)。

### 7.3 残差与闭合指标

| 指标 | 惯例阈值 | 出处 |
|---|---|---|
| 逐历元残差相位 RMS | 去二次趋势面后计算;**> 3×MAD(中位数绝对偏差)判为噪声历元并剔除**;参考日取最小 RMS 历元 | [L] Yunjun et al. (2019) §4.9(引 Rousseeuw & Hubert 2011);[U] MintPy `residualRMS.deramp = auto (quadratic)`、`cutoff = auto (3)` |
| SB 反演残差 RMS(像元级) | LiCSBAS 掩膜默认 `resid_rms ≤ 2 mm` | [U] `batch_LiCSBAS.sh` p15;[L] Morishita et al. (2020) Table 3 |
| 闭合环残差(图级) | 环 RMS **> 1.5 rad** 判问题环;全环皆坏 → 剔除该干涉图 | [U] LiCSBAS `p12_loop_thre = 1.5 rad`;[L] Morishita et al. (2020) §2.4.2 |
| 闭合环残差(像元级) | `n_loop_err ≤ 5`(不闭合环数)、`n_ifg_noloop ≤ 50` | [U] `batch_LiCSBAS.sh` p15 |
| 解缠误差像元指示 | `T_int`(非零整数模糊三元组数)分布图;`T_int = 0` 为无解缠误差 | [L] Yunjun et al. (2019) §3.2 式(8)-(9)、Fig. 3 |
| 时空一致性 | LiCSBAS `stc ≤ 5 mm`(相邻像元时序双差 RMS 最小值) | [U] `batch_LiCSBAS.sh` p15;[L] Morishita et al. (2020) Table 3(引 Hanssen 系 STC 概念) |

### 7.4 PS/SBAS 交叉验证(本项目独有质量门)的文献参照

- 文献里**没有"相关系数 ≥0.85"这类标准阈值**;互检惯例的表述是"一对一速度差的 std"
  (Terrafirma 0.5–0.7 mm/yr;Glasgow 四方法 1.1 mm/yr;Casu ~1 mm/yr,见 §7.1)。
- 双方法系统差是常态而非异常:PSI 与 SBAS 在同一滑坡上可给出 5–25 vs 5–15 mm/yr 的速度带
  ([L] Geomatics NHR 2019 Stigliano 案例,doi:10.1080/19475705.2018.1549113);
  成因包括散射体类型不同、滤波差异、密度/覆盖差异([L] RSE 2021 互检论文 §5 讨论)。
- → 建议 `corr_threshold` 维持 local_calibration,并在标定实验中**同时报告相关系数与速度差 std**,
  以速度差 std ≤ 1–2 mm/yr(平稳区)作为文献可比的参照系。

---

## 落地映射表 (a):contract.yaml 可补 ref 的阈值清单

> 落地由主线做;下表给出"阈值 → 建议值 → 引用(可直接粘进 ref 字段)"。
> source 分级:U=upstream_default,L=literature,C=community practice(C 建议写入 ref 备注而非独立等级)。

### PENDING 项

| threshold_key | 现值 | 调研结论 | 建议 ref(可粘贴) | 建议 source/status |
|---|---|---|---|---|
| `min_coherence` | 0.25 | **有直接文献锚点,可解除 PENDING** | `Berardino et al. (2002, IEEE TGRS 40(11), §V: "the coherence threshold has been fixed to 0.25"); 同值为 GIAnT 惯例 (Agram & Simons 2015, 转引 Yunjun et al. 2019 §6.5); 解缠有效掩膜下界参照 HyP3 InSAR Product Guide (coherence<0.1 masked)` | `literature` / OK |
| `unwrap_coverage` | 0.70 | 有惯例可引但数值取向不同:LiCSBAS 的图级剔除线是 0.3(下限门);0.70 是"良好解缠"的从严工程门 | `LiCSBAS11_check_unw.py unw_cov_thre 默认 0.3 (yumorishita/LiCSBAS batch_LiCSBAS.sh; Morishita et al. 2020, Remote Sens. 12(3):424 §2.4.1); 本项目取 0.70 从严, 属 local 加严, 若触发 warning 频繁可回退 0.3–0.5` | `literature`(0.3 下限)+ 注明加严;或改 `local_calibration` / 待 1 次实测后转 OK |
| `max_temporal_baseline` | 120 天 | S1 惯例区间 24–90 天(同数据源的 ASF Ridgecrest 教程用 24 天);MintPy 默认不限并主张宽松+冗余;120 天偏宽松但有官方主张背书 | `MintPy smallbaselineApp.cfg mintpy.network.tempBaseMax=auto(no, 不限) + Yunjun et al. (2019) §6.3 推荐宽松阈值+冗余网络; 同数据源惯例: ASF hyp3_insar_stack_for_ts_analysis.ipynb (Ridgecrest) max_temporal_baseline=24d; GMTSAR S1 教程 50d/100m; 冻土场景须收紧(跨冻融季失相干, 见 scenario pack)` | `literature` / OK(quake·HyP3 路线该门实际不触发;permafrost 场景包应覆写更小值) |
| `corr_threshold` | 0.85 | 无文献惯例(互检用速度差 std 表述);维持本地标定是诚实做法 | `无直接文献先例; 参照系: PSI 互检速度差 std 0.5–0.7 mm/yr (Terrafirma, 转引 RSE 256:112306, 2021), S1 四方法互检 1.1 mm/yr (同文), SBAS 精度 ~1 mm/yr (Casu et al. 2006, RSE 102); 待 experiments/PENDING-crossval-calibration.md 标定` | 维持 `local_calibration` / PENDING(但 ref 从"待标定"升级为"有参照系的待标定") |
| `nan_fraction_below` | 0.50 | 无直接惯例;逻辑镜像是 LiCSBAS 覆盖率门(valid ≥0.3 ⇔ nan ≤0.7),0.50 是从严工程值 | `工程门, 逻辑参照 LiCSBAS unw_cov_thre 0.3 (valid fraction 下限 ⇔ nan fraction 上限 0.7); 本项目对成品栅格取 nan≤0.5 从严` | 维持 `local_calibration`,ref 补上参照 |

### 已 OK 项的 ref 精确化(可选增强)

| threshold_key | 现 ref | 增强建议 |
|---|---|---|
| `esd_coherence_threshold` (0.85) | "ISCE2 topsApp 默认值" | 精确到源码:`isce2 applications/topsApp.py: ESD_COHERENCE_THRESHOLD = Application.Parameter(default=0.85)`(github.com/isce-framework/isce2) |
| `weightFunc` ("no") | 实测配置 | 补官方注释:`MintPy smallbaselineApp.cfg §5: "SBAS (Berardino et al., 2002) = minNormVelocity (yes) + weightFunc (no)"` —— 说明该覆盖正是"经典 SBAS 均权"配置,有明确语义而非任意覆盖 |
| `stepFuncDate` | 实测配置 | 可补:主震 2019-07-06 03:19:53 UTC(USGS),配置值 T0320 与之一致 |

### capabilities.py 声明参数的顺带锚点(不进 contract,正文引用用)

| 参数(步骤) | 默认 | 锚点 |
|---|---|---|
| `alpha` 0.4(第 5 步) | ISCE2 topsApp `FILTER_STRENGTH default=0.5`;HyP3 adf 0.6;MintPy 示例 0.2 | 0.4 处于惯例带内;若走 HyP3 路线实际生效的是产品端 0.6 |
| `cost_mode` SMOOTH(第 6 步) | ISCE2 `snaphu_mcf` 即 SMOOTH+MCF+initOnly;snaphu man page:平滑形变用 `-s` | 与推荐方法 `snaphu_mcf` 语义一致 |
| `max_temporal_baseline` 120(第 7 步) | 见上表 | — |
| `ramp` linear(第 8 步) | MintPy `deramp = auto (no)`,注释:局部形变推荐 linear,**同震/震间等长波长信号不推荐** | quake 场景建议覆写为 `no`(阶跃是长波长信号,去 ramp 有吃信号风险);注意与现默认 linear 的张力 |
| `dem_error` True(第 8 步) | MintPy `topographicResidual = auto (yes)`(Fattahi & Amelung 2013) | 一致 |

---

## 落地映射表 (b):三个场景包 SKILL.md 可补的领域知识段落草稿

> 以下为可直接粘贴(或轻改)的 markdown 草稿,引用均已在正文核对。

### (b-1) quake/SKILL.md 增补草稿

```markdown
## 参数依据补充(2026-08 调研)

- **时间基线**:ASF 官方 HyP3+MintPy Ridgecrest 教程(hyp3-docs,
  hyp3_insar_stack_for_ts_analysis.ipynb)与本场景同数据源同链路,取
  max_temporal_baseline = 24 天;本项目 contract 的 120 天门在 HyP3 路线不构成约束,
  实际选对已由 11 对产品固化。
- **解缠(云端已完成,复核用)**:HyP3 用 MCF 算法,相干 < 0.1 的像元不参与解缠
  (HyP3 InSAR Product Guide);若本地重做,ISCE2 `snaphu_mcf` = SMOOTH 代价 + MCF 初始化
  + initOnly(runUnwrapSnaphu.py 源码);近场跨断层出现解缠跳变时改 DEFO 模式
  (snaphu DEFOMAX_CYCLE 默认 1.2 周,man page)。
- **去 ramp 警告**:MintPy 官方注释明确"co-/post-/inter-seismic 等长波长形变不推荐 deramp"
  (smallbaselineApp.cfg §9)。同震阶跃属长波长信号,第 8 步 ramp 参数建议在本场景覆写为 no,
  避免把形变梯度当轨道误差扣除。
- **震后模型(fork 分支用)**:余滑 → 对数 d(t)=A·ln(1+t/τ)(Marone et al. 1991, JGR;
  Ingleby & Wright 2017, GRL 证实 Omori 型衰减);黏弹 → 指数;大震常用组合式
  d(t)=a·ln(1+t/b)+c(1−e^(−t/τ))+Vt(Tobita 2016, EPS)。MintPy 语法:
  `mintpy.timeFunc.log = 20190706,60`。对数/指数在短观测窗内近似不可辨识,
  报告残差而非断言机理(Sobrero et al. 2020, J Geodesy)。
- **质量预期**:强形变火山/同震场景 InSAR vs GNSS 时序 RMSE 0.5–1.8 cm 是
  "结果可信"的文献参照(Yunjun et al. 2019 §5.1 Fig.8);阶跃拟合残差的空间分布
  应无断层同形态残余(有则提示阶跃日期/模型形态错误)。
- **闭合环质检**:图级闭合环 RMS > 1.5 rad 判问题干涉图(LiCSBAS12 默认;
  Morishita et al. 2020 §2.4.2);HyP3 产品自带 conn comp,可跑 MintPy
  phase_closure 的 T_int 指示图(Yunjun 2019 §3.2)定位残余解缠误差。
```

### (b-2) landslide/SKILL.md 增补草稿

```markdown
## 参数依据补充(2026-08 调研)

- **PS 数据量门槛**:PS 候选按振幅离散度 D_A ≤ 0.25 初选(Ferretti et al. 2001,
  IEEE TGRS 39(1) §III);可靠估计需 ≥20–25 景(PSI 综述, doi:10.1007/s40534-016-0108-4),
  短时序结果不可信 —— 与本场景 data_ready=false 时的"≥20 景"要求一致。
- **精度预期**:PS 速度精度 <1 mm/yr(最优 0.1–0.5 mm/yr, Ferretti 2001;罗马案例
  后验 0.25 mm/yr);蠕滑滑坡 mm–cm/yr 量级在 PSI 适用域内(Colesanti & Wasowski
  2006, Eng. Geology 88:173-199 —— 滑坡 InSAR 适用性的标准引文)。
- **几何可见性**:陡坡叠掩/阴影 + LOS 对坡向敏感:LOS 对南北向运动几乎不敏感,
  坡向/坡度相对 LOS 的几何决定可测性(Colesanti & Wasowski 2006;PSI 滑坡应用普遍
  用升降轨互补)。LOS→坡向投影假设必须显式进报告。
- **滤波**:高相干裸岩点目标区 Goldstein alpha 取低值(0.2–0.5)防过滤
  (Baran et al. 2003 的 α=1−γ̄ 自适应原则;HyP3 官方指导 α>0.2);
  PS 链本身不滤波(滤波破坏点目标相位),此条仅适用于 SBAS 对照链。
- **双链交叉验证预期**:PSI 与 SBAS 在同一滑坡给出系统性不同的速度带是常态
  (Stigliano 案例:PS 5–25 vs SBAS 5–15 mm/yr, doi:10.1080/19475705.2018.1549113);
  互检惯例用一对一速度差 std 表述:多处理器互检 0.5–1.1 mm/yr(Terrafirma;
  RSE 256:112306, 2021)。crossval 门的 0.85 相关阈值属本地标定,无文献先例。
- **加速识别**:线性拟合残差系统性增大是临滑前兆信号,应报警而非改用高阶模型
  拟合掉(维持现行纪律;残差历元剔除用 3×MAD 惯例, Yunjun 2019 §4.9)。
```

### (b-3) permafrost/SKILL.md 增补草稿

```markdown
## 参数依据补充(2026-08 调研)

- **形变模型标准式**:一阶社区默认 d(t)=a·t+b·sin(2πt/T+φ₀)+c(T=1 年)
  (青藏高原 S1 案例 Li et al. 2019, Remote Sens. 11(9):1000;Heihe 综述式(3),
  doi:10.1029/2022JF006782)。Daout et al. (2017, GRL) 用 8 年数据证明冬季冻结期
  无形变、季节循环非正弦不对称 —— 半年项(periods=[1,0.5])即吸收该不对称的
  傅里叶二阶项;更物理的替代是 Stefan 度日模型(形变∝√累计融化度日,
  Liu et al. 2012, JGR)。
- **量级预期**:青藏高原冻融峰-峰季节位移常见 40–80 mm(Li et al. 2019),
  长期退化沉降 mm–cm/yr —— 远小于单景大气扰动(Zebker et al. 1997:湿度 20% 变化
  ≈10 cm 误差),故大气校正不可省。
- **大气校正依据**:高原分层延迟与地形强相关,会伪装成与地形相关的冻融信号;
  GACOS 校正后 InSAR−GNSS 速度差 STD 2.4→1.9 mm/yr(Morishita et al. 2020 §3.4);
  ERA-I 在青藏昆仑平均削减 APS 73%(Jolivet et al. 2011, GRL)。GACOS 产品自带
  可行性指标,应先查指标再决定采用(Yu et al. 2018, JGR 123:9202)。
  经验高程相关校正(tropo_height_corr)无法区分地形相关的真实冻融形变,
  降级时必须在报告声明(MintPy cfg §8 注释;Yunjun 2019 §4.6)。
- **网络设计**:跨冻结/融化季的干涉对相干性系统性下降,优先序贯短时基线对;
  S1 惯例时间基线 24–90 天,本场景取保守值并保证冗余(Yunjun 2019 §6.3:
  宽松阈值+更多连接;闭合修正能力 3/5/10 连接对应 5/20/35%)。
- **数据跨度**:周期项可靠估计需 ≥2 个完整年循环;观测 <14 个月时线性趋势
  与季节项不可分离(Li et al. 2019 §2 引述)—— 本场景 2020-01—2023-12 的
  4 年设计满足要求。
- **质检侧重**:解缠覆盖率门参照 LiCSBAS unw_cov_thre 0.3 下限(本项目 0.70 从严);
  低相干区时间相干掩膜 0.7(Pepe & Lanari 2006;MintPy minTempCoh 默认),
  掩膜后像元数 ≥100(MintPy minNumPixel)。
```

---

## 参考文献与一手来源清单

**软件默认配置 / 源码(upstream_default 可引)**

- MintPy 默认配置:<https://github.com/insarlab/MintPy/blob/main/src/mintpy/defaults/smallbaselineApp.cfg>(本文所有 `mintpy.*` 默认值出处)
- snaphu man page:<https://web.stanford.edu/group/radar/softwareandlinks/sw/snaphu/snaphu_man1.html>;snaphu.conf.full:<https://web.stanford.edu/group/radar/softwareandlinks/sw/snaphu/snaphu.conf.full>
- ISCE2 topsApp:<https://github.com/isce-framework/isce2/blob/main/applications/topsApp.py>(ESD_COHERENCE_THRESHOLD=0.85、FILTER_STRENGTH=0.5、unwrapper 默认 icu);runUnwrapSnaphu:<https://github.com/isce-framework/isce2/blob/master/components/isceobj/TopsProc/runUnwrapSnaphu.py>(snaphu_mcf = SMOOTH+MCF+initOnly)
- LiCSBAS 批处理默认:<https://github.com/yumorishita/LiCSBAS/blob/master/batch_LiCSBAS.sh>(p11/p12/p15 全套阈值)
- ASF HyP3 InSAR Product Guide:<https://hyp3-docs.asf.alaska.edu/guides/insar_product_guide/>(adf α=0.6、解缠相干掩膜 0.1、MCF、水掩膜)
- ASF HyP3+MintPy 时序教程(Ridgecrest,max_temporal_baseline=24d):<https://github.com/ASFHyP3/hyp3-docs/blob/main/docs/tutorials/hyp3_insar_stack_for_ts_analysis.ipynb>
- GACOS 官网:<http://www.gacos.net/>
- EGMS Product User Manual (2022):速度 STD 0.7 mm/yr 规格表
- ESA 技术说明《Increase of Sentinel-1A Orbital Tube: impact on interferometry》(ESA-EOPG-EOPGMQ-TN-2024-12, 2024):<https://sentiwiki.copernicus.eu/>(轨道管 100→200 m RMS,12 天对 bperp<165 m)
- GMTSAR S1 时序教程(50d/100m 选对):<https://topex.ucsd.edu/gmtsar/tar/sentinel_time_series_5.pdf>

**核心文献(literature 可引)**

- Berardino P., Fornaro G., Lanari R., Sansosti E. (2002). A new algorithm for surface deformation monitoring based on small baseline differential SAR interferograms. *IEEE TGRS* 40(11):2375-2383. doi:10.1109/TGRS.2002.803792(§V:bperp<130 m、相干阈值 0.25、4×20 多视)
- Ferretti A., Prati C., Rocca F. (2001). Permanent scatterers in SAR interferometry. *IEEE TGRS* 39(1):8-20. doi:10.1109/36.898661(D_A≤0.25;速度精度 <1 mm/yr)
- Yunjun Z., Fattahi H., Amelung F. (2019). Small baseline InSAR time series analysis: Unwrapping error correction and noise reduction. *Computers & Geosciences* 133:104331. doi:10.1016/j.cageo.2019.104331(式3 时间相干;§3.2 闭合相位 T_int;§4.9 残差 RMS 3×MAD;§5.1 GPS RMSE 0.5–1.8 cm;§6.3 冗余建议;§6.5 GIAnT 0.25 惯例)
- Pepe A., Lanari R. (2006). On the extension of the minimum cost flow algorithm for phase unwrapping of multitemporal differential SAR interferograms. *IEEE TGRS* 44(9):2374-2383. doi:10.1109/TGRS.2006.873207(时间相干定义;0.7 惯例)
- Morishita Y., Lazecky M., Wright T.J., Weiss J.R., Elliott J.R., Hooper A. (2020). LiCSBAS: An open-source InSAR time series analysis package integrated with the LiCSAR automated Sentinel-1 InSAR processor. *Remote Sensing* 12(3):424. doi:10.3390/rs12030424(loop 1.5 rad;coverage 0.3;噪声指标表;速度 std 收敛 ~2 mm/yr;GACOS 1.9 vs 2.4 mm/yr)
- Goldstein R.M., Werner C.L. (1998). Radar interferogram filtering for geophysical applications. *GRL* 25(21):4035-4038. doi:10.1029/1998GL900033
- Baran I., Stewart M.P., Kampes B.M., Perski Z., Lilly P. (2003). A modification to the Goldstein radar interferogram filter. *IEEE TGRS* 41(9):2114-2118. doi:10.1109/TGRS.2003.817212(α=1−γ̄)
- Chen C.W., Zebker H.A. (2002). Phase unwrapping for large SAR interferograms: statistical segmentation and generalized network models. *IEEE TGRS* 40:1709-1719(tile 模式理论)
- Zebker H.A., Rosen P.A., Hensley S. (1997). Atmospheric effects in interferometric synthetic aperture radar surface deformation and topographic maps. *JGR* 102(B4):7547-7563. doi:10.1029/96JB03804(湿度 20%→10 cm)
- Jolivet R., Grandin R., Lasserre C., Doin M.-P., Peltzer G. (2011). Systematic InSAR tropospheric phase delay corrections from global meteorological reanalysis data. *GRL* 38:L17311(昆仑 APS −73%)
- Jolivet R., Agram P.S., Lin N.Y., Simons M., Doin M.-P., Peltzer G., Li Z. (2014). Improving InSAR geodesy using Global Atmospheric Models. *JGR* 119:2324-2341. doi:10.1002/2013JB010588(LA 方差 −70%;湍流决定成败)
- Yu C., Li Z., Penna N.T., Crippa P. (2018). Generic atmospheric correction model for Interferometric Synthetic Aperture Radar observations. *JGR Solid Earth* 123:9202-9222. doi:10.1029/2017JB015305(GACOS;StdDev −47~54%;~1 cm)
- Yu C., Li Z., Penna N.T. (2018). Interferometric synthetic aperture radar atmospheric correction using a GPS-based iterative tropospheric decomposition model. *RSE* 204:109-121(45–78% 削减)
- Doin M.-P. et al. (2009). Corrections of stratified tropospheric delays in SAR interferometry. *J. Applied Geophysics* 69:35-50(高程相关经验校正)
- Manunta M. et al. (2019). The parallel SBAS approach for Sentinel-1 IW deformation time-series generation. *IEEE TGRS* 57(9). doi:10.1109/TGRS.2019.2904912(S1 bperp std 50 m)
- Casu F., Manzo M., Lanari R. (2006). A quantitative assessment of the SBAS algorithm performance for surface deformation retrieval from DInSAR data. *RSE* 102:195-210(速度 ~1 mm/yr;时序 ~5 mm)
- Hetland E.A. et al. (2012). Multiscale InSAR time series (MInTS) analysis of surface deformation. *JGR* 117:B02404(MintPy timeFunc 式(2)-(9) 出处)
- Fattahi H., Amelung F. (2013). DEM error correction in InSAR time series. *IEEE TGRS* 51(7);(2015) InSAR uncertainty due to orbital errors. *JGR*(残差不确定度传播)
- Marone C., Scholz C.H., Bilham R. (1991). On the mechanics of earthquake afterslip. *JGR* 96(B5):8441-8452(对数余滑)
- Ingleby T., Wright T.J. (2017). Omori-like decay of postseismic velocities following continental earthquakes. *GRL* 44. doi:10.1002/2017GL072865
- Tobita M. (2016). Combined logarithmic and exponential function model for fitting postseismic GNSS time series after 2011 Tohoku-Oki earthquake. *EPS* 68:41. doi:10.1186/s40623-016-0422-4(组合式标准形)
- Sobrero F.S., Bevis M., Gómez D.D., Wang F. (2020). Logarithmic and exponential transients in GNSS trajectory models... *J. Geodesy* 94:84(τ 不可辨识性)
- Daout S., Doin M.-P., Peltzer G., Socquet A., Lasserre C. (2017). Large-scale InSAR monitoring of permafrost freeze-thaw cycles on the Tibetan Plateau. *GRL* 44. doi:10.1002/2016GL070781(正弦一阶;度日模型;非对称)
- Li et al. (2019). Time-series InSAR monitoring of permafrost freeze-thaw seasonal displacement over Qinghai-Tibetan Plateau using Sentinel-1 data. *Remote Sensing* 11(9):1000(峰-峰 40–80 mm;<14 个月不可分离)
- Liu L., Zhang T., Wahr J. (2010/2012). InSAR measurements of surface deformation over permafrost... *JGR*(Stefan 模型)
- Colesanti C., Wasowski J. (2006). Investigating landslides with space-borne SAR interferometry. *Engineering Geology* 88:173-199(滑坡 InSAR 适用性标准引文)
- 《A comparison of multi temporal interferometry techniques...Stigliano》(2019). *Geomatics, Natural Hazards and Risk*. doi:10.1080/19475705.2018.1549113(PS 5–25 vs SBAS 5–15 mm/yr)
- 《Benchmarking and inter-comparison of Sentinel-1 InSAR velocities and time series》(2021). *RSE* 256:112306(Terrafirma 0.5–0.7 mm/yr / 1.5–5.6 mm;Glasgow 1.1 mm/yr)
- IGARSS 2023《A Discussion on the Goldstein Filtering Parameters Within the SNAP Software》. doi:10.1109/IGARSS52108.2023.10282000(SNAP 默认过强,建议 0.5–0.6)
- IGARSS 2024《Validation of the EGMS with GNSS and Corner Reflectors...》. doi:10.1109/IGARSS53475.2024.10641305(速度差 <2 mm/yr)
- Kang Y. et al. (2021). Coherence-based network modification... *RSE*(面积比 0.75,MintPy cfg 引用)
- Chaussard E. et al. (2015). *GRL*(相干选网,MintPy cfg 引用)
- Fattahi H. et al. (2017). InSAR ionospheric correction by split-spectrum. *IEEE TGRS*;Liang C. et al. (2019). *IEEE TGRS*(TOPS 电离层)

**调研纪律声明**:本文数值均在 2026-08-12 从上述一手来源核对;二手综述仅用于交叉印证。
未找到文献先例的阈值(corr_threshold、nan_fraction_below)已如实标注,不伪造引用。
