---
name: 11-crossval-qa
description: 当规划或诊断第 11 步质检(PS/SBAS 交叉验证、闭合环残差、相干性统计质检)时使用:在 crossval_ps_sbas / loop_closure / coherence_mask 之间选方法、设计交叉验证样本划分、解读 qa.json 指标与 corr_threshold 质量门(PENDING 只警告)的行为。
capability: 11
version: "1.0.0"
applies_to: all
---

# 质检(交叉验证 / 闭合环 / 统计指标)

第 11 步对成品做质量评估并产出机器可读报告。方法 id:`crossval_ps_sbas`
(默认、推荐,引擎标识 `-`)、`loop_closure`(引擎 mintpy)、`coherence_mask`
(引擎 mintpy);产物 artifact id 为 `qa_report`(候选 `products/report/qa.json`、
`qa.json`,policy=content)。依赖第 10、7 两步(`deps=(10, 7)`),输入
artifact 为 `velocity` 与 `timeseries`。质量门(quality_gate)声明:
`metric_min(crossval_r, threshold_key="corr_threshold", on_fail="stop")` ——
但 contract.yaml 台账中 `corr_threshold` 是 **PENDING(local_calibration)**,
按 §4.13 纪律只产出 warning 不硬拦,证据阶梯如实封顶 audited。本步
`replay = safe`。

## 适用判据

- **`crossval_ps_sbas`(PS/SBAS 双链交叉验证,本项目独有质量门)**:两条独立
  处理链(PS 与 SBAS)对同一数据给出可比速度场时才有意义。**当前工程边界**:
  ISCE2→PyStamps 桥未实现,PS 链未建成 → 交叉验证不可行;`quake` 与
  `stripmap_coseismic` 场景包都把本步 overrides 为 `coherence_mask`(单干涉对
  同样无双链)—— 诚实降级为真实统计质检,**不假装有双链验证**。
- **`loop_closure`(闭合环残差检查)**:验证解缠一致性 —— 只验解缠不验反演
  (registry `why` 原文)。适合怀疑解缠误差残留时的针对性复核(HyP3 产品自带
  连通分量,可配合 MintPy phase_closure 的 T_int 指示图定位)。
- **`coherence_mask`(相干性统计质检,最弱)**:从产物重解析真实指标,绝不
  编造(engines/qa.py 实现):`velocity_coverage`、`nan_fraction`、
  `mean_coherence`(读 `avgSpatialCoh.h5`)、`vel_p2`/`vel_p98`、
  `residual_rms_mm`(读 `rms_timeseriesResidual*.txt`);`crossval_r` 在 PS 链
  建成前**不写入**,缺指标只产生 warning —— 指标可由 verify.py 重解析复核。
- 选择次序:双链可用 → `crossval_ps_sbas`;单链但怀疑解缠 → `loop_closure`;
  其余(以及当前所有实测场景)→ `coherence_mask`。

## 参数启发式

本步骤在 registry(capabilities.py id=11)声明的参数只有一个:

- **`corr_threshold`**(science,默认 `0.85`,范围 0–1):PS/SBAS 速度场相关
  系数合格线。**文献里没有"相关系数 ≥ 0.85"这类标准阈值** —— 互检惯例用
  "一对一速度差的标准差"表述(Terrafirma 多处理器互检 0.5–0.7 mm/yr;Glasgow
  S1 四方法互检平均 1.1 mm/yr;SBAS 精度 ~1 mm/yr,Casu et al. 2006),故台账
  维持 local_calibration/PENDING 是诚实做法;待 experiments/
  PENDING-crossval-calibration.md 标定,**标定时必须同时报告相关系数与速度差
  std** 两个口径。
- **交叉验证的样本划分**(为 crossval_ps_sbas 实装与标定实验准备的设计原则):
  - **空间共位**:PS 点(点目标)对 SBAS 像元(多视分布式)按同像元/最近邻
    配对,注意两链分辨率与掩膜不同,配对前各自剔除无效像元并统一到公共覆盖;
  - **分层统计**:稳定区与形变区分开算 —— 稳定区的一对一速度差 std 评估噪声底
    (文献参照 ≤ 1–2 mm/yr),形变区评估信号一致性(相关系数对动态范围敏感,
    只在形变区显著时才有区分度);
  - **先去系统差再算相关**:两链参考点/基准不同,先对齐参考(去均值或共同
    参考点),否则常数偏移会稀释相关;**系统性速度带差异是常态而非异常**
    (Stigliano 滑坡案例:PS 5–25 vs SBAS 5–15 mm/yr,成因含散射体类型/滤波/
    密度差异),不要一见系统差就判失败;
  - **报告样本量 N**:配对点过少(如 < 100)时相关系数不稳定,结论降级。
- `loop_closure` 路线阈值:图级闭合环相位 RMS > 1.5 rad 判问题干涉图
  (LiCSBAS `p12_loop_thre` 默认;Morishita et al. 2020 §2.4.2);注意闭合相位
  非严格为零有多视/滤波/土壤水分等无害成因(同文告诫),非零 ≠ 必然解缠错误。

## 常见失败与处置

1. **qa.json 无 `crossval_r`,质量门 warning「指标缺失」** → PS 链未建成
   (桥是接口边界)或单干涉对场景 → 预期行为:降级 `coherence_mask`
   (quake/stripmap 包已固化),报告声明单链证据;不要伪造相关系数,阈值
   PENDING 本来就只警告。
2. **担心 `corr_threshold` 触发 stop 硬拦** → 台账 status=PENDING 时
   `metric_min` 只出 warning(§4.13;tests/test_audit_deep.py 守护该行为)→
   只有标定转 OK 后 on_fail="stop" 才生效;转 OK 的前提是完成标定实验并同时
   报告两个口径。
3. **`mean_coherence` 缺失** → `mintpy/avgSpatialCoh.h5` 不存在(第 7 步
   invert_network 的副产物;非 MintPy 布局或跳步时无此文件)→ qa.py 现行为是
   跳过该指标并打印说明,不算失败;需要它则确认第 7 步走的是 mintpy_sbas。
4. **`residual_rms_mm` 异常大** → 区分场景:同震大形变的模型残差大属预期
   (Ridgecrest 实测 21.7 mm);震间/缓变场景显著超过 LiCSBAS 参照
   (像元级 resid_rms ≤ 2 mm)→ 回查第 8 步大气校正是否起效、第 9 步模型形态
   是否匹配机理。
5. **`velocity_coverage` 低 / `nan_fraction` 高** → 低相干掩膜大或解缠覆盖差 →
   对照台账 `nan_fraction_below = 0.50`(PENDING,只警告);病根在第 6/7 步
   (解缠覆盖率、时间相干掩膜),本步只如实呈现,不要在本步放宽统计口径。
6. **期望第 6 步就拦住低解缠覆盖,但 gate 没触发** → `unwrap_coverage` 指标
   当前仅由第 11 步 qa.json 产出,第 6 步 gate 评估时必然缺失 → 台账维持
   PENDING 是有意为之(转 OK 会把"指标缺失"变成硬拦停,runok.py metric_min
   语义);在指标产出点前移之前不得解除(contract.yaml 头注)。

## QA 依据

- **run_ok 双判定**(registry 声明):`exit_code == 0` + `artifact_exists
  (qa_report)`;qa_report policy=content(进内容指纹,报告可复核)。
- **crossval 合格线**:`crossval_r ≥ 0.85`(corr_threshold,PENDING 待标定);
  文献可比的参照系是**平稳区一对一速度差 std ≤ 1–2 mm/yr**(Terrafirma
  0.5–0.7 mm/yr;Glasgow 四方法 1.1 mm/yr;RSE 256:112306, 2021)。
- **速度场 RMSE / 与 GNSS 对比量级预期**:强形变场景 InSAR vs GNSS 时序 RMSE
  0.5–1.8 cm(Yunjun et al. 2019 §5.1 Fig. 8,失效站由时间相干 < 0.7 预先
  识别);平稳区速度差 STD ≈ 1.9–2 mm/yr(GACOS 后,Morishita et al. 2020);
  EGMS 产品规格速度 STD 0.7 mm/yr、GNSS 验证绝大多数差 < 2 mm/yr —— 本项目
  无常备 GNSS,用这些区间作"结果可信"的参照系写进报告。
- **时序闭合环残差**:图级环 RMS > 1.5 rad 判问题环(LiCSBAS);像元级 T_int
  指示图定位解缠误差(Yunjun et al. 2019 §3.2 式(8)-(9));序贯 3/5/10 连接
  网络可完全修正的错误干涉图占比上限 5/20/35%(同文结论 2)—— 冗余不足时
  闭合检查发现的问题只能剔除不能修。
- **台账纪律(总原则)**:每个阈值必须带 source(upstream_default /
  literature / local_calibration),PENDING 不参与硬 gate 只出 warning;
  Ridgecrest 全链实测基线:6 项指标全部重解析通过,mean_coherence 0.949、
  residual_rms 21.7 mm,证据级 audited(README 真实数据验收)。

## 参考文献

- Yunjun Z., Fattahi H., Amelung F. (2019). Small baseline InSAR time series
  analysis: Unwrapping error correction and noise reduction. *Computers &
  Geosciences* 133:104331. doi:10.1016/j.cageo.2019.104331(闭合相位 T_int;
  GNSS 对比 RMSE 0.5–1.8 cm;时间相干预筛失效站)
- Morishita Y., Lazecky M., Wright T.J., Weiss J.R., Elliott J.R., Hooper A.
  (2020). LiCSBAS: An open-source InSAR time series analysis package.
  *Remote Sensing* 12(3):424. doi:10.3390/rs12030424(loop 1.5 rad;噪声指标
  体系;InSAR−GNSS 速度差 STD)
- 《Benchmarking and inter-comparison of Sentinel-1 InSAR velocities and time
  series》(2021). *RSE* 256:112306. doi:10.1016/j.rse.2021.112306(Terrafirma
  速度差 std 0.5–0.7 mm/yr;Glasgow 四方法互检 1.1 mm/yr —— crossval 参照系)
- Casu F., Manzo M., Lanari R. (2006). A quantitative assessment of the SBAS
  algorithm performance for surface deformation retrieval from DInSAR data.
  *RSE* 102:195-210. doi:10.1016/j.rse.2006.01.023(SBAS 精度 ~1 mm/yr,
  vs 水准/GPS 的单历元 std)
- Ferretti A., Prati C., Rocca F. (2001). Permanent scatterers in SAR
  interferometry. *IEEE TGRS* 39(1):8-20. doi:10.1109/36.898661(PS 链精度
  上限,交叉验证另一端的口径)
- 《A comparison of multi temporal interferometry techniques for landslide
  susceptibility assessment in urban area: an example on Stigliano》(2019).
  *Geomatics, Natural Hazards and Risk*. doi:10.1080/19475705.2018.1549113
  (PS 5–25 vs SBAS 5–15 mm/yr:双链系统差是常态)
- Pepe A., Lanari R. (2006). *IEEE TGRS* 44(9):2374-2383.
  doi:10.1109/TGRS.2006.873207(时间相干 —— coherence_mask 路线掩膜口径的
  理论出处)
- EGMS Product User Manual (2022):速度 STD 0.7 mm/yr 产品规格;配套验证
  IGARSS 2024, doi:10.1109/IGARSS53475.2024.10641305(GNSS 差 < 2 mm/yr)
- LiCSBAS 批处理默认(p12 loop 阈值、p15 掩膜族):
  https://github.com/yumorishita/LiCSBAS/blob/master/batch_LiCSBAS.sh
- 本仓库台账:src/insar_agent/audit/contract.yaml(corr_threshold 条目;其 ref
  指定标定实验落位 experiments/PENDING-crossval-calibration.md,标定时须同时
  报告相关系数与速度差 std)
