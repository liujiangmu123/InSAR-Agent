---
name: 05-phase-filtering
description: "当需要为流水线第 5 步滤波选择方法(goldstein / boxcar / none / isce2_stripmap_filter)、调 alpha 与 filter_strength 强度、权衡相位保真与残差点抑制、或诊断解缠仍失败、条纹被滤移位、表观相干虚高、分块伪影等滤波类问题时使用。"
capability: 5
version: "1.0.0"
applies_to: all
---

# 干涉图滤波(Interferogram Filtering)

能力速览(以 `registry/capabilities.py` id=5 为准):方法 `goldstein`(默认、推荐,低相干区)/ `boxcar` / `none` / `isce2_stripmap_filter`(仅 stripmap_coseismic,stripmapApp filter 单步重跑,分钟级);science 参数 `alpha`(默认 0.4,0–1)、`filter_strength`(默认 0.5,0–1);输入 `ifg`;产物 `ifg_filt`(候选 `data/ifg_filt`、`isce2/interferogram/filt_topophase.flat`);io=medium;total 超时 4 h;磁盘 `pairs * 0.17` GB。

## 适用判据

- **`goldstein`(默认、推荐)**:Goldstein–Werner 频域自适应谱滤波——把干涉图分块 FFT,按谱强度的幂次(指数即 `alpha`)增强主导条纹分量、压制宽带噪声。低相干区首选:显著减少残差点(residues),直接改善第 6 步解缠成功率。
- **`boxcar`**:空域滑动平均。简单、快、可预期,但对条纹边缘(断层迹线、形变峰)一视同仁地模糊——同震近场不合适;仅用于快速质检或极平缓信号。
- **`none`**:不滤波,保留全部细节。三种正当理由:相干性本来就高且条纹率高(过滤有害无益);诊断基线——先看未滤波的残差点密度再决定滤波策略;为 PS(Persistent Scatterer)对照链准备输入——滤波会破坏点目标相位,PS 链纪律是不滤(landslide 场景包)。
- **`isce2_stripmap_filter`(scenario_only=stripmap_coseismic)**:stripmapApp filter 单步重跑(前驱 sub_band_interferogram 的 pickle 已在第 4 步段生成)。注意显式契约:该方法沿 stripmapApp 默认滤波形态,XML 不渲染 filter_strength 属性(docs/VALIDATION-isce2-wsl.md 实测)——在此方法下调 `alpha`/`filter_strength` 无效,这是声明行为不是 bug。
- 场景差异:permafrost 低相干区滤波是刚需(不滤基本解不了缠);quake(HyP3)本步云端已完成;同震近场以保真优先、强度从低;landslide 的 SBAS 对照链参照低强度,PS 主链不滤。

## 参数启发式

- **`alpha`(Goldstein 强度,0=不滤,1=最强,默认 0.4)**:
  - 自适应基准:Baran et al. (2003) 的修正原则是 α = 1 − γ̄(γ̄ 为局部平均相干性)——相干越低滤越狠。手动定值时以此为锚:相干普遍 0.6+ 的场景 α 取 0.2–0.4;相干 0.3–0.5 的低相干区(冻土融化季、植被坡面)α 取 0.5–0.8。
  - 往上调的触发:第 6 步解缠大面积失败、残差点密度居高不下,且第 4 步视数已到位。
  - 往下调的触发:条纹保真受损的任何证据(见 QA 节剖面检查)。过滤的三宗罪:条纹移位/合并 = 形变系统性低估;窄形变带被抹平;表观相干被人为抬高,污染下游掩膜判断。
  - 场景锚点:同震近场高梯度 α 0.2–0.4(HyP3 官方经验 α>0.2 才有可感效果,上限守住保真);冻土大尺度平缓信号 α 0.5–0.8 换解缠成功率,信号尺度远大于滤波窗,保真风险低。
  - 顺序纪律(与第 4 步联动):多视无偏、滤波有偏——信噪比不足先回第 4 步加 looks,再动 alpha。
- **`filter_strength`(默认 0.5,0–1)**:通用强度旋钮(boxcar 类核强度/引擎侧强度参数)。方向与 alpha 同:残差点多 → 升;细节丢失 → 降。与 alpha 不叠加使用心智:goldstein 方法看 alpha,boxcar 方法看 filter_strength,别两个一起拧。
- 对 `isce2_stripmap_filter` 两个参数均不生效(见适用判据);确需自定义滤波策略时,改走通用布局链的 `goldstein`/`boxcar`,不动 stripmap 段。

## 常见失败与处置

1. **症状**:滤波后第 6 步解缠仍大面积失败、残差点仍密。**根因**:失相干是本质问题而非噪声级问题——相干趋近 0 的区域,滤波是无中生有。**处置**:回第 4 步加 looks 或剔除坏对(治本);α 升到 0.6–0.8 只对中等相干区有效;真正的水体/融化区接受掩膜,不要用滤波硬造相位。
2. **症状**:滤波前后对比,条纹位置可见移动、相邻条纹合并、峰值形变低估。**根因**:过滤(alpha 过高)。**处置**:α 降到 0.2–0.4;以未滤波条纹的过零点位置为真值基准;同震场景把跨破裂带剖面的前后对比设为固定检查项。
3. **症状**:滤波后相干图(如 stripmap 链的 `phsig.cor`)整体右移、低相干区"看起来变好了"。**根因**:Goldstein 滤波抬高表观相干——滤后相干不再反映原始观测质量。**处置**:第 6 步 `min_coherence` 掩膜基于滤前相干,或对滤后相干的阈值语义心里有账;报告里必须注明掩膜用的是滤前还是滤后相干。
4. **症状**:滤波产物出现分块状伪影、块间接缝。**根因**:FFT 分块边界效应,强 alpha 放大之(分块与重叠是引擎内部默认,不外露参数)。**处置**:降 alpha;换 `boxcar` 交叉验证是伪影还是信号;接缝穿过形变区时必须处理,穿过噪声区可容忍。
5. **症状**:`isce2_stripmap_filter` 调参重跑后产物指纹不变/形态无变化。**根因**:声明行为——stripmapApp 默认滤波,XML 不渲染强度属性。**处置**:接受默认(实测该形态全链通过);把调滤波的需求转到通用链;不要反复改参数空烧重跑。
6. **症状**:stripmap 链单步重跑 filter 即崩(NoneType)。**根因**:前驱 sub_band_interferogram 的 pickle 缺失——第 4 步段(split_range_spectrum→filter)没有完整跑完。**处置**:先补第 4 步整段,再单步重跑本步;分段区间守 `_STRIPMAP_RANGES`,勿手改。

## QA 依据

- run_ok:`exit_code == 0` 且 `ifg_filt` 产物命中(`data/ifg_filt` 或 `isce2/interferogram/filt_topophase.flat`)。
- 残差点计数(可量化的核心指标):滤波前后残差点数量对比,健康滤波应有数量级下降;把前后计数写进 run 记录,为将来滤波强度的本地标定积累样本(对齐 §4.13 台账纪律:无据数字不进硬门)。
- 保真剖面检查(定性但最灵敏):抽 2–3 条跨形变中心/破裂带的剖面,滤波前后条纹相位过零点位置一致;移位即过滤证据,回调 alpha。
- 相干直方图形态:滤后相干分布右移属预期而非改善证据;报告注明掩膜相干的来源版本。
- 目视:无分块接缝伪影;断层迹线、形变边缘未被"抹圆";低相干区噪声被压、条纹可辨。
- 本步无 quality_gate 硬门;合格线是"解缠可行性提升且保真无损",两头都要查,只查一头必偏。

## 参考文献

- Goldstein, R. M., & Werner, C. L. (1998). Radar interferogram filtering for geophysical applications. *Geophysical Research Letters*, 25(21), 4035–4038. doi:10.1029/1998GL900033 —— Goldstein 滤波原始文献(α 幂次谱增强)。
- Baran, I., Stewart, M. P., Kampes, B. M., Perski, Z., & Lilly, P. (2003). A modification to the Goldstein radar interferogram filter. *IEEE TGRS*, 41(9), 2114–2118. doi:10.1109/TGRS.2003.817212 —— α = 1 − γ̄ 自适应原则。
- Hanssen, R. F. (2001). *Radar Interferometry: Data Interpretation and Error Analysis*. Kluwer. doi:10.1007/0-306-47633-9 —— 相位统计与滤波对估计量的影响。
- Just, D., & Bamler, R. (1994). Phase statistics of interferograms with applications to synthetic aperture radar. *Applied Optics*, 33(20), 4361–4368. doi:10.1364/AO.33.004361 —— 相位偏差机理(过滤为何造成系统性低估)。
- ASF HyP3 InSAR Product Guide(hyp3-docs.asf.alaska.edu)—— α>0.2 的实践指导;云端产品滤波形态参照。
- Ferretti, A., Prati, C., & Rocca, F. (2001). Permanent scatterers in SAR interferometry. *IEEE TGRS*, 39(1), 8–20. doi:10.1109/36.898661 —— PS 链不滤波纪律的机理依据(点目标相位保持)。
- 仓库内:`docs/VALIDATION-isce2-wsl.md`(stripmap filter 段实测与 XML 形态)、`src/insar_agent/registry/scenario_packs/landslide/SKILL.md`(α 低值防过滤、PS 不滤纪律)。
