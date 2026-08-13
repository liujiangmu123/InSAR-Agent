---
name: 06-unwrap
description: "当需要为流水线第 6 步相位解缠选择方法(snaphu_mcf / snaphu_smooth / icu / 3D_FULL / isce2_stripmap_unwrap_snaphu)、调 min_coherence 掩膜阈值与 cost_mode 代价模式、或诊断 2π 跳变、解缠覆盖率不足、连通分量碎裂、snaphu 超时、stripmap 续跑崩溃等解缠类失败时使用。"
capability: 6
version: "1.0.0"
applies_to: all
---

# 相位解缠(Phase Unwrapping)

能力速览(以 `registry/capabilities.py` id=6 为准):方法 `snaphu_mcf`(默认、推荐)/ `snaphu_smooth` / `icu` / `3D_FULL` / `isce2_stripmap_unwrap_snaphu`(仅 stripmap_coseismic);science 参数 `min_coherence`(默认 0.25,0–1)、`cost_mode`(枚举 `SMOOTH` / `DEFO` / `TOPO`,默认 `SMOOTH`);resource 参数 `threads`(默认 8);输入 `ifg_filt`;产物 `unw`(IFG_UNWRAPPED,候选 `data/unw`、`data/unw/geo`、`isce2/interferogram/filt_topophase.unw`、`isce2/interferogram/filt_topophase.unw.geo`)与可选 `unwrap_cfg`(`params/unwrap.yaml`);run_ok 含 `log_absent(ERROR|Segmentation fault)`;quality_gate:`metric_min(unwrap_coverage)` 引用台账键 `unwrap_coverage`;total 超时 7200 s;mem 8 GB。

## 适用判据

- **`snaphu_mcf`(默认、推荐)**:SNAPHU 统计代价网络流解缠,MCF(Minimum Cost Flow,最小费用流)初始化。ISCE2 `runUnwrapSnaphu` 的实际形态 = SMOOTH 代价 + MCF 初始化 + initOnly(源码核对,quake 场景包调研)。低相干区稳健,输出与 MintPy 原生兼容——SBAS 主链的标准选择。
- **`snaphu_smooth`**:精度上限更高但需人工调代价函数(交互配置),定位是"默认跑通后的精调复跑",不是首选。
- **`icu`**:区域增长(region growing)法,快;大范围低相干区会产生互相独立参考的解缠孤岛(islands)——只适合快视质检,不做成品。
- **`3D_FULL`**:时空三维解缠工具链,输出格式与下游(MintPy)不兼容——仅实验用途,选它意味着自担下游桥接。
- **`isce2_stripmap_unwrap_snaphu`(scenario_only=stripmap_coseismic)**:stripmapApp 内置 snaphu 驱动(XML 里 do unwrap=True / unwrapper name=snaphu,不需要独立 snaphu 可执行,只依赖 isce2 引擎),段尾含地理编码(geocode)。硬规则:分段必须从 filter_low_band 续起补齐 pickle 链(实测教训 2:直接从 unwrap 起必崩);实测 filter_low_band→geocode 约 13 min(snaphu 约 10 min)。
- 场景差异:quake(HyP3)解缠已在云端完成(HyP3 用 MCF,相干 <0.1 的像元不参与——HyP3 Product Guide),本步 skipped,知识仅供复核;permafrost 的主战场是"掩膜阈值 vs 覆盖率"的拉锯;同震(stripmap_coseismic / 本地重做的 quake)看点是大梯度下的代价模式选择。

## 参数启发式

- **`min_coherence`(默认 0.25;台账 OK 级,literature:Berardino et al. 2002 §V 明文固定 0.25,GIAnT 同值惯例;HyP3 用 0.1 作解缠参与下限)**:相干性掩膜阈值,决定哪些像元参与解缠。
  - 双向代价:升(0.3–0.4)→ 参与像元更可靠、错误传播风险低,但覆盖率下降、连通分量碎裂;降(0.1–0.2)→ 覆盖优先,冒解缠错误跨低质区蔓延的险。
  - 与覆盖率门联动:`unwrap_coverage` 台账值 0.70(PENDING)——阈值每升一档,覆盖必降一截,两个数要一起看,单调一个必顾此失彼。
  - 场景差异:同震荒漠(Ridgecrest 型)相干普遍高,可升到 0.3–0.4 收紧质量;冻土低相干区守 0.2–0.25,覆盖缺口靠第 5 步滤波与第 4 步多视去补,一味降阈值是用解缠错误换覆盖假象;水体/融化区的真实缺口应接受掩膜并在报告声明。
  - 注意相干来源:若第 5 步用了 Goldstein 滤波,滤后表观相干系统性偏高——同一个 0.25 在滤前/滤后相干上语义不同(见 05 技能 QA 节)。
- **`cost_mode`(默认 `SMOOTH`)**:SNAPHU 统计代价模型的先验假设。
  - `SMOOTH`:相位场平滑先验——时序干涉对、震间形变、冻土季节信号(空间尺度大、梯度缓)。
  - `DEFO`:形变模式,允许陡峭梯度(snaphu 的 DEFOMAX_CYCLE 默认 1.2 周/弧,官方手册)——同震近场首选;跨断层出现解缠跳变时的正确动作是 SMOOTH→DEFO,而不是换初始化算法(quake 场景包调研结论)。
  - `TOPO`:地形测绘模式(相位主导项是地形)——本流水线的差分干涉场景基本不用,列于枚举仅为完备。
- **`threads`(resource,默认 8)**:snaphu 主体单核,threads 对解缠本身收益有限(tile 并行未在本能力外露);维持默认,内存吃紧(预算 8 GB)时降之。

## 常见失败与处置

1. **症状**:解缠相位在相干良好区域出现 2π 整数跳变线(非破裂带处)。**根因**:`cost_mode` 先验与信号形态不符(SMOOTH 硬拟陡梯度),或滤波不足残差点仍密。**处置**:`cost_mode` 改 `DEFO`;不解决则回第 5 步 `alpha` 升一档、或第 4 步加 looks;跳变若恰沿破裂迹线则属物理不连续,不是错误(见 QA)。
2. **症状**:quality_gate 对 `unwrap_coverage` 出 warning(低于台账 0.70)。**根因**:`min_coherence` 偏高造成掩膜过度,或该区本质低相干(水体、融化区、浓密植被)。**处置**:`min_coherence` 0.25→0.2 并复查错误蔓延;或增强第 5 步滤波;确属物理性低相干则接受 warning 并在报告声明覆盖边界——该门 status=PENDING 只警告不硬停(§4.13 纪律),诚实呈现优于凑数。
3. **症状**:连通分量(connected component,conncomp)碎裂,主分量占比小、分量数多。**根因**:掩膜把场景切成互不连通的岛(阈值高/失相干带分割),各岛解缠参考独立、岛间偏移不可比。**处置**:适度降 `min_coherence` 打通桥接走廊;`icu` 尤其易碎,换回 `snaphu_mcf`;下游 MintPy 只采信主分量——碎片区的形变结论不可外推,报告须声明。
4. **症状**:snaphu 逼近/超过 total 超时(7200 s)或内存超 8 GB 预算。**根因**:低多视大幅面(节点数爆炸)+ 残差点稠密。**处置**:回第 4 步加 looks(节点数随视数积近似反比下降,治本);或缩小处理范围/减对分批;不要盲目提超时——超时是问题规模的信号,不是配额问题。
5. **症状**:stripmap 链从 unwrap 直接续跑,启动即 NoneType 崩溃。**根因**:pickle 链断——前驱 filter_high_band 等占位步的状态缺失(实测教训 2)。**处置**:必须从 filter_low_band 续起让空转占位步补齐 pickle(区间固化在 `engines/isce2.py` `_STRIPMAP_RANGES`,有测试断言连续性,勿手改分段)。
6. **症状**:解缠量级离谱(如无地质依据的数十米 LOS)或整幅符号/斜坡怪异。**根因**:解缠错误大面积蔓延(掩膜过松)、参考落在错误分量、或上游轨道斜坡未除。**处置**:先做 rewrap 检验定位错误区(见 QA);查 conncomp 主分量;收紧 `min_coherence` 重解;量级参照系:Baja 同震实测 LOS 约 −4.8 ~ +7.1 m 属大地震合理范围,时序单对通常远小于此。

## QA 依据

- run_ok 三重判定:`exit_code == 0`、`unw` 产物命中(含 `.conncomp` 伴随文件与 `.geo` 地理编码版本)、日志无 `ERROR|Segmentation fault`。
- quality_gate:`metric_min(unwrap_coverage, threshold_key="unwrap_coverage")`——台账值 0.70,source=local_calibration,status=PENDING → 只出 warning 不硬停;且该指标当前由第 11 步 qa.json 产出,本步 gate 评估时指标缺失属已知形态(contract.yaml 头注)。文献参照系:LiCSBAS `unw_cov_thre = 0.3` 是社区下限惯例(Morishita et al. 2020 §2.4.1),0.70 属主动加严待标定。
- rewrap 一致性(解缠正确性的机器可查判据):解缠相位 mod 2π 应与输入缠绕相位逐像元一致——差异应仅出现在掩膜边界;成片差异区即解缠错误区,定位后按处置节收敛。
- 剖面连续性:跨形变中心剖面连续无台阶;唯一允许的阶跃位置是同震破裂迹线(物理不连续)——阶跃出现在别处即错误。
- 残差点/闭合环(网络场景):解缠后三元组闭合差应为 2π 整数倍且异常占比低;图级闭合环相位 RMS > 1.5 rad 判问题干涉图(LiCSBAS p12 默认;Morishita et al. 2020 §2.4.2);MintPy phase closure 的 T_int 指示图可定位残余解缠误差(Yunjun et al. 2019 §3.2)。
- conncomp 统计进 run 记录:主分量像元占比、分量总数——为 unwrap_coverage 门的本地标定积累证据(标定完成前该门维持 PENDING,对齐 §4.13 来源纪律)。

## 参考文献

- Chen, C. W., & Zebker, H. A. (2000). Network approaches to two-dimensional phase unwrapping: intractability and two new algorithms. *JOSA A*, 17(3), 401–414. doi:10.1364/JOSAA.17.000401 —— MCF 网络法与可解性。
- Chen, C. W., & Zebker, H. A. (2001). Two-dimensional phase unwrapping with use of statistical models for cost functions in nonlinear optimization. *JOSA A*, 18(2), 338–351. doi:10.1364/JOSAA.18.000338 —— SNAPHU 统计代价框架(SMOOTH/DEFO/TOPO 三模式)。
- Chen, C. W., & Zebker, H. A. (2002). Phase unwrapping for large SAR interferograms: statistical segmentation and generalized network models. *IEEE TGRS*, 40(8), 1709–1719. doi:10.1109/TGRS.2002.802453 —— 大幅面分块解缠。
- Goldstein, R. M., Zebker, H. A., & Werner, C. L. (1988). Satellite radar interferometry: two-dimensional phase unwrapping. *Radio Science*, 23(4), 713–720. doi:10.1029/RS023i004p00713 —— 残差点(residue)概念本源。
- SNAPHU 用户手册与 man page(Stanford Radar Interferometry Group,web.stanford.edu/group/radar/softwareandlinks/sw/snaphu/)—— DEFOMAX_CYCLE 默认 1.2 周等代价参数语义。
- Berardino, P., et al. (2002). *IEEE TGRS*, 40(11), 2375–2383. doi:10.1109/TGRS.2002.803792 —— 台账 min_coherence=0.25 的文献出处。
- Yunjun, Z., Fattahi, H., & Amelung, F. (2019). *Computers & Geosciences*, 133, 104331. doi:10.1016/j.cageo.2019.104331 —— 闭合环解缠误差检测与修正。
- Morishita, Y., et al. (2020). LiCSBAS: an open-source InSAR time series analysis package integrated with the LiCSAR automated Sentinel-1 InSAR processor. *Remote Sensing*, 12(3), 424. doi:10.3390/rs12030424 —— 覆盖率门与闭合环 RMS 的社区惯例。
- ASF HyP3 InSAR Product Guide(hyp3-docs.asf.alaska.edu)—— 云端 MCF 解缠与相干 0.1 参与下限。
- 仓库内:`src/insar_agent/audit/contract.yaml`(min_coherence / unwrap_coverage 条目及 PENDING 语义)、`docs/VALIDATION-isce2-wsl.md`(stripmap 解缠段实测:耗时、产物路径、pickle 链教训)。
