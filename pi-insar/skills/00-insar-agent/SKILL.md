---
name: 00-insar-agent
description: "当需要用 insar_* 工具层驱动 InSAR 流水线时使用:建会话/规划 run、预演与落实方法或参数改动、执行与监控、读日志诊断失败、导出 provenance 与证据级,以及判断何时该用 insar_* 工具、何时可以自由用 bash。也用于解读五阶段(PREPARED/LAUNCHED/RUNNING/COLLECTED/VERIFIED)、stale 标脏与级联失效、六级证据阶梯,以及 free/strict 两种自由度模式。"
version: "1.0.0"
applies_to: all
---

# InSAR 工具层操作总纲(operator skill)

这一层不讲某一步的科学参数(那在 11 个分步技能里),只讲**怎么把台账驱动起来**:
一次合格的作业 = `plan → preview → apply → execute → status → provenance`,
全程每个科学动作都留在 provenance 台账里。

后端是本仓的 FastAPI 服务(默认 `http://127.0.0.1:8873`,由 `INSAR_API_BASE` 指定),
所有 `insar_*` 工具都是它的薄封装;工具返回的 `details` 里是完整 JSON,正文只是摘要。

## 1. 标准回合

```
insar_health                 # 后端活着吗(任何 insar_* 报错时先跑这个)
insar_env_probe              # 有哪些引擎/凭据?缺引擎 = 后续 run 是 simulated
insar_list_datasets          # 盘上有什么数据(规划真实 run 前必看)
insar_create_session         # 会话:一组 run + 工作区 + 自由度模式
insar_list_sessions          # 已有哪些会话(接着别人/自己上次的活干)
insar_plan_run               # 自然语言 → 11 步计划(只规划,不执行)
insar_run_status             # 计划长什么样 / 现在跑到哪 / 证据级 / 标脏数
insar_preview_change         # (可选)某处方法或参数改动的影响面与重跑代价
insar_apply_change           # (可选)落实改动:SET_METHOD / SET_PARAMS
insar_execute_run            # 执行(可只跑 step_ids 子集),返回时执行流已结束
insar_resume                 # 后端重启后接回 running;已结算步骤绝不重跑
insar_view_figure            # 列出/内联查看 run 的真实图件产物(省略 name 列出)
insar_run_trace              # 执行轨迹:各步阶段与耗时(排障/写报告的时间线)
insar_read_log               # 失败步骤的日志尾巴
insar_export_provenance      # kind=ledger(证据文档)/ kind=run_sh(等价裸命令脚本)
```

要点:

- **`insar_plan_run` 不执行任何东西**,它把请求解析成 11 步计划;执行永远是显式的
  `insar_execute_run`。计划里哪些步会被 skipped(如 quake 场景 HyP3 云端已完成 2–6 步)
  在这一步就能看出来。
- **`insar_execute_run` 是长调用**:它消费后端执行流直到结束才返回。要中途干预用
  `insar_intervene`(PAUSE / PLAY / KILL / RESET / SKIP;RESET 与 SKIP 必须带 `target` 步号)。
- **断点续跑分工**:`insar_resume` 接回后端重启后仍为 `running` 的 run(已结算步骤绝不重跑);
  推进计划里还 `pending` 的步骤用 `insar_execute_run`,不要拿 resume 当"再跑一遍"。
- **`run_id` 可省**:省略即"该会话最近一次 run"。跨越多个 run(比较、fork)时一定写全,
  否则你会把结论安到错的 run 上。
- **参数是 JSON 对象字符串**:`params_json='{"looks_range": 4}'`、
  `changes_json='{"5": {"method": "goldstein", "params": {"alpha": 0.6}}}'`。

## 2. 什么时候用 insar_*,什么时候可以自由用 bash

| 动作 | 走哪条路 |
| --- | --- |
| 跑/续跑/重跑任何一步科学处理 | `insar_execute_run`(禁止手拼 ISCE2/MintPy/snaphu 命令) |
| 后端重启后接回仍 `running` 的 run | `insar_resume`(已结算步骤绝不重跑;pending 用 execute) |
| 改方法、改科学参数 | `insar_preview_change` → `insar_apply_change` |
| 中止、跳过、复位、暂停 | `insar_intervene` |
| 比较两种处理选择 | `insar_fork_run`(复用未受影响的步骤)后各自 `insar_execute_run` |
| 看状态、看日志、看产物清单 | `insar_run_status` / `insar_read_log` / `insar_export_provenance` |
| 看真实图件(列出或内联) | `insar_view_figure`(省略 `name` 列出;给 `name` 内联) |
| 看各步阶段与耗时 | `insar_run_trace` |
| 读代码、读文档、查目录、算一个临时统计、写一次性脚本 | 自由用 pi 的 `read`/`bash`/`grep`(free 模式下) |

判据一句话:**会影响科学结论或产物的动作走工具层;只为"我自己看懂"的动作随便跑。**
bash 产出的东西不进台账 —— 一旦它要出现在结论里,就必须重新用流水线产出,
或在回答里明确标注"台账外产物,不计入证据级"。

## 3. 五阶段(five stages)与 state

执行器把每一步推过五个阶段(`stage` 是单调高水位,幂等守卫据此恢复):

```
PREPARED ──> LAUNCHED ──> RUNNING ──> COLLECTED ──> VERIFIED
   P            L            R            C            V
   │            │            │            │            └ run_ok 双判定通过(exit_code + 产物 + 日志)
   │            │            │            └ 产物发现完成、指纹已算
   │            │            └ 作业结束、exit_code 已知
   │            └ 作业已启动、job_dir 与命令意图已落盘
   └ 配置已渲染、输入清单已校验
```

`insar_run_status` 的每步同时给 `state`(面向人的当前态)与 `stage_letter`:

- `state`:`pending | running | done | failed | interrupted | orphaned | skipped`(外加 `stale` 旗标)。
- **`failed` ≠ `orphaned`**:`orphaned` 是 wrapper 失踪一类的环境事件,不是计算失败;
  `interrupted` 是取消(保留日志偏移,可复位重跑)。三者的处置不同,别一律当"报错重跑"。
- 侧边栏字形:`✔` done · `–` skipped · `○` pending · `✖` failed · `!` stale · `P/L/R/C/V` 运行中阶段。

失败诊断顺序:`insar_run_status` 看 `failure_class` 与 `exit_code` → `insar_read_log`(必要时加
`tail_kb`)→ 读对应分步技能的"常见失败与处置" → 用 `insar_preview_change` 评估修复改动 →
`insar_apply_change` → `insar_execute_run`(只跑受影响的 `step_ids`)。

## 4. stale(标脏)与级联失效(dirty cascade)

改动会沿依赖链传播,`insar_run_status` 的 `taints` 是当前标脏总数,每步有 `stale` / `stale_reason`。
五类原因:

| reason | 含义 |
| --- | --- |
| `method_changed` | 换了方法 |
| `param_changed` | 改了科学/呈现参数 |
| `upstream_changed` | 自身没变,上游重跑了 |
| `tool_upgraded` | 引擎版本变了 |
| `artifact_missing` | 产物被删或被改(指纹对不上) |

参数三分类决定传播范围(这是本系统的关键差异点):

- **science**:标脏自身 + **全部下游** —— 例如 `min_coherence`、`alpha`、`looks`。
- **presentation**:只标脏自身,不传播 —— 例如 `dpi`、`cmap`。
- **resource**:不标脏,只记 provenance —— 例如 `threads`、内存预算。

所以:**改参数前先 `insar_preview_change`**,它返回 `affected`(会被污染的步骤)与
`rerunMinutes` / `rerunBasis`(历史样本 <3 时为 unknown,不要把 unknown 说成"很快")。
带着 stale 步骤的 run 不能拿来下结论 —— 要么重跑受影响步骤,要么在报告里声明。

## 5. 六级证据阶梯(evidence ladder)

```
runnable → checked → audited → calibrated → validated → publishable
```

- **runnable**:全部步骤 VERIFIED(或显式 skipped)。
- **checked**:全部 run_ok 通过。
- **audited**:provenance 完整 —— 本 run 产物有指纹、指标可重解析。
- **calibrated**:有 GNSS/水准等外部比对。
- **validated**:双链交叉验证达标(且相关阈值不是 PENDING)。
- **publishable**:证据边界表 + 跨环境复现记录齐备。

取最差原则:有一步降级则整体降级。三条封顶规则必须主动说出来:

1. `simulated=true`(引擎缺失)→ **封顶 runnable**,演示不冒充证据。
2. 存在 PENDING 阈值 → 封顶 `audited`(还没标定完就不能声称 validated)。
3. 云端(HyP3)跳过的步骤:有可核对的 `hyp3_manifest` 才算审计完整且整 run 封顶 `audited`;
   无 manifest 封顶 `checked`。fork 复用步骤的外部验证指标**不自动继承**。

`insar_run_status` 给 `evidence.level` / `evidence.ceiling` / `evidence.ceiling_reason`;
`insar_export_provenance kind=ledger` 给完整依据。报结论时**证据级和封顶原因一起报**。

## 6. 绝不编造数值

速率、相干、`unwrap_coverage`、闭合环 RMS、阈值、耗时、run_id、产物路径 —— 一切数字只有三个合法来源:
`insar_run_status`、`insar_read_log`、`insar_export_provenance`、`insar_run_trace`。

- 查不到 → 说"未记录/需要先跑第 N 步",不要估、不要引用别的 run 的数、不要给"大约"。
- 质量门 PENDING 只 warning 不硬停 → 如实说 warning,不要说"通过"。
- 引用时带上 run_id 与 step 号,别人才能复核。

## 7. 分支与干预语义

- `insar_fork_run`:从 `parent_run_id` 派生带 per-step 改动的新 run,未受影响的步骤直接复用
  (返回里 `reused` 是复用数)。比较处理选择时用它,不要整链重跑。
- `insar_apply_change` 的 `deliver_as` 三语义:
  `steer` = 下一步生效(跑动中改)· `follow_up` = 本 run 结束后 · `next_run` = 下一次规划时。
- `insar_intervene` 的排队动作在**步边界**生效;`KILL` 走 abort 通道立即请求取消。

## 8. free / strict 两种模式

- **free(默认)**:pi 全部工具可用;`insar_*` 是推荐路径而非强制。
- **strict**:只有 `read` 和 `insar_*` 能跑,其余工具在调用时被拦截并返回原因。
  用 `insar_set_mode`(或启动加 `--insar-strict`、会话内 `/insar-mode strict`)切换,
  `insar_get_mode` 查询。被拦截时**不要绕道**(别去找等价的间接手段),换用对应的 `insar_*` 工具。
  模式存在后端、按会话持久化;策略是逐调用拦截,切回 free 立即恢复全部工具。

## 9. 分步技能索引(读了再动手)

| 技能 | 覆盖 |
| --- | --- |
| `01-data-acquisition` | 第 1 步 数据获取:`asf_search_slc` / `hyp3_submit` / `local_import`、检索为空与凭据失败 |
| `02-dem-preparation` | 第 2 步 辅助数据:DEM 来源与轨道档次、覆盖空洞、长波长斜坡 |
| `03-coregistration` | 第 3 步 配准:TOPS/ESD 与 stripmap 互相关、burst 边界跳变 |
| `04-interferogram` | 第 4 步 干涉:多视比、干涉网络规模、轨道/地形条纹 |
| `05-phase-filtering` | 第 5 步 滤波:`goldstein` / `boxcar` / `none`、alpha 与表观相干虚高 |
| `06-unwrap` | 第 6 步 解缠:`snaphu_mcf` 等、`min_coherence` 与 `cost_mode`、2π 跳变与覆盖率 |
| `07-sbas-inversion` | 第 7 步 时序反演:SBAS 与 PS 选链、基线阈值、参考点、NaN 区块 |
| `08-tropo-correction` | 第 8 步 误差校正:对流层延迟、去 ramp、DEM 误差、固体潮 |
| `09-deformation-model` | 第 9 步 形变模型:linear / step / poly_periodic / exponential |
| `10-figure-export` | 第 10 步 出图导出:色标与参考点规范、地理编码与 GeoTIFF |
| `11-crossval-qa` | 第 11 步 质检:PS/SBAS 交叉验证、闭合环残差、qa.json 与质量门 |

场景包技能(规划前先看清场景差异):`quake`(同震,Ridgecrest,HyP3 已完成 2–6 步)、
`permafrost`(冻土季节冻融,低相干 + 周期项)、`landslide`(滑坡点状目标,PS 链)、
`stripmap_coseismic`(ALOS 条带同震,ISCE2 stripmapApp 全链)。

## 10. 常见错误(别犯)

1. 用 bash 手拼 ISCE2/MintPy 命令"更快地"跑一步 —— 结果不进台账,等于没跑。
2. 直接改工作区里的配置文件当作"改参数" —— 指纹与标脏都不会更新,台账与现实脱节。
3. 不看 `insar_preview_change` 就 `insar_apply_change` —— 你不知道污染了多少下游。
4. 拿 `simulated=true` 的 run 谈形变量级。
5. 忽略 `taints > 0` 就下结论。
6. 报数不带 run_id/step,或把 `rerunMinutes: null` 说成"很快"。
