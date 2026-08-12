# 开源对标：对比学习分析表

> 生成日期：2026-08-10
> 用途：为 insar-agent（可复现 InSAR 科学工作流 Agent）做架构参考。
> 全部结论基于本地克隆源码核验（`reference/repos/`，浅克隆自 2026-08-10 main 分支），
> 每条可学习点均标注文件路径+行号。

---

## 0. 我们自己的架构（对照基准）

```
UI(步骤工作台) → API(FastAPI) → Brain(LLM只做候选集内选择题，绝不生成命令/代码)
    → Core(SQLite状态机 + 参数指纹sha256失效传播 + 步骤级断点续跑)
    → Audit(指标来源契约 + 六级证据阶梯 + try/finally触发)
    → Registry(纯数据) / Runtime(WSL执行器) / Engines(ISCE2/MintPy/PyStamps + 桥)
```

- 主 novelty：参数级 stale detection（改方法/参数/工具版本 → 下游自动标脏）
- 辅助：完整 provenance（artifact SHA256 + 命令轨迹 + 工具版本 + git 状态）
- 决策约束：LLM 只做选择题；删掉 brain/ 层系统退化为手动流水线仍完全可用

---

## 1. 总览对比表

| 项目 | 定位 | 许可 | 状态恢复粒度 | provenance | 失效判定 | LLM 使用 | 与我们的关系 |
|---|---|---|---|---|---|---|---|
| **aiida-core** | 计算科学全栈 provenance 平台 + 工作流引擎 | MIT | 进程级（plumpy Bundle 整体恢复） | 节点+边图，DB 持久化，zip 归档 | 有（节点 hash 缓存，但**永不失效**） | 无 | 可借鉴哈希与存储模式，不可照搬 |
| **aiida-workgraph** | aiida 之上的图式工作流 DSL | MIT | WorkGraph 进程重启 + 任务状态跳过（在途任务不调和） | 复用 aiida-core | 复用 aiida | 无 | 借鉴任务状态外置与幂等扫描，不可照搬 |
| **agentic-data-scientist** | 通用多 Agent 数据科学框架 | MIT | **无**（全内存 session，崩溃即丢） | **无** | **无** | LLM 规划+写代码（bypassPermissions） | 借鉴阶段化成功标准追踪；方向相反 |
| **scientific-agent-skills** | 161 个科学 Agent 技能包 + 规范 | MIT | —（静态技能库） | 无 | 有（扫描结果内容哈希缓存） | 技能即 prompt 扩展 | 借鉴 SKILL.md 闭集 schema 与校验器 |
| **OpenDiscoveryTrace** | AI 科学家过程轨迹评测数据集 | CC-BY | 轨迹文件级（每步立即写盘+按名幂等跳过） | 轨迹 JSON（含错误/修订字段） | 无 | 被测对象（GPT/Claude/Gemini/开源） | 借鉴 step 数据契约，用作评测叙事 |
| **redun** | 惰性表达式工作流引擎 | Apache-2.0 | 无显式 resume，靠缓存快进重放 | call graph 存 SQLite（Merkle 式 call_hash） | 有（eval_hash：函数源码+参数+文件哈希） | 无 | 借鉴哈希设计与缓存分层；不引入 |
| **InSAR_Agent** | InSAR 处理 Agent（直接竞品） | MIT | 整数下标粗粒度 | **零** | **零**（只查 status=='done'） | DeepSeek 云端 + 关键词表强制 tool_choice | 只抄 4 个设计点，其余为反例 |
| **agentic-swmm-workflow** | 已发 SCI 的 SWMM Agent（最重要参照） | 开源 | 阶段化（stage 级 manifest） | 完整（artifact SHA256+命令轨迹+git+工具版本） | 部分（工具/代码版本对比） | LLM planner + Skill 目录 + MCP | 同构度最高；可直接改造 provenance schema |

---

## 2. 逐项目学习分析

### 2.1 aiida-core（MIT，v2.9.0）

**架构**：ORM → 存储后端(Postgres/SQLite-zip) → 引擎(plumpy 状态机 + circus daemon) → CLI。
**核心机制**：
- 每个 Process 是 plumpy 状态机，每次状态转换后整体序列化为 Bundle 存 `checkpoints` 属性（`src/aiida/engine/persistence.py:72-93`、`src/aiida/orm/nodes/process/process.py:158/257`）
- 节点 hash = `make_hash({class, attributes, repository_hash, computer_uuid})`，算法为 **blake2b 无限扇出、dict/list 排序后顺序无关**（`src/aiida/common/hashing.py:76-107`）
- 指纹存 extras（`_aiida_hash`/`_aiida_valid_cache`/`_aiida_cached_from`），命中用 QueryBuilder 精确等值查找并克隆跳过（`src/aiida/orm/nodes/caching.py:74-150`）
- 文件仓库按 **SHA256 内容寻址**；归档为单 zip 内嵌 sqlite+repo（`src/aiida/tools/archive/implementations/sqlite_zip/main.py:23-31`）

**可学习点**：
1. 指纹边界显式化：定义“参与哈希的对象集合”而非隐式全量（`caching.py:74-86`）→ 我们的参数指纹应显式列出 method/params/upstream/tool_version/input_manifest
2. 顺序无关哈希：dict/list 递归排序后再哈希（`hashing.py:76-107`）→ 参数 dict 键序变化不应使指纹失效
3. attributes（不可变参数）与 extras（可变元数据）分离（`psql_dos/models/node.py:42-99`）→ 参数指纹 vs 审计元数据分层存储
4. checkpoint 生命周期：状态转换后保存、结束时删除、保存前先持久化未存子节点防反序列化失败（`process.py:257`、`workchains/workchain.py:362-366`）
5. 归档格式：zip 内嵌 sqlite + sha256 仓库，迁移审计简单

**不照搬**：需要 circus daemon + ZMQ broker + 完整 profile 体系；恢复是进程级非步骤级；缓存“永不失效”无传播语义；provenance 是数据节点图而非命令轨迹；执行模型绑定远程调度器而非 WSL 子进程。

### 2.2 aiida-workgraph（MIT，v0.8.1）

**核心机制**：`WorkGraphEngine(Process)` 用 `@auto_persist('_awaitables')`（`engine/workgraph.py:32-33`），`save_instance_state`/`load_instance_state`（`:100-144`）把图 JSON + context 一起持久化，恢复时重建图对象；任务状态外置到节点属性（`utils/__init__.py:324-347`）；`continue_workgraph` 幂等扫描就绪任务（`engine/task_manager.py:104-122`）；`reset_task` 支持部分重跑（`task_state.py:226-247`）。

**可学习点**：
1. 任务状态外置存储、引擎内外统一读写 → 审计层读状态不依赖引擎（`task_state.py:64-78`）
2. “图 JSON + 上下文都进 checkpoint，恢复时重建” → 我们的 step graph 序列化恢复方案
3. 幂等“扫描就绪任务”循环（`task_manager.py:104-122`）→ 与“从候选集挑可运行步骤”同构
4. `should_run_task + max_number_jobs` 限流并发子进程（`task_manager.py:139-160`）→ 符合重型计算管控

**不照搬**：深度耦合 aiida-core + plumpy；恢复仍是“整进程重启 + 任务状态跳过”，崩溃时在途子任务既不重跑也不等待（`task_manager.py:108-116` 无调和）；**tests/test_checkpoint.py 是空文件**，恢复行为依赖隐式框架。

### 2.3 agentic-data-scientist（MIT，682★）

**核心机制**：规划/执行分离，9 个角色（Plan Maker → Reviewer → Parser → Stage Orchestrator → Coding → Review → Criteria Checker → Stage Reflector → Summary）。plan_parser 把自然语言转成带 `{index,title,description,completed,implementation_result}` 的 stage 列表（`agents/adk/agent.py:127-209`）；criteria checker 强制“用工具读文件验证，不要假设”，每 criterion 输出 `{index, met, evidence}`（`prompts/base/criteria_checker.md:60-79`）；终态三元判定 `completed / completed_with_warnings / incomplete`（`stage_orchestrator.py:111-160`）；事件压缩阈值 40 条、保留最近 20 条、>10KB 截到 1KB（`event_compression.py:26-29`）。

**可学习点**：
1. 终态三元判定 + `unmet_criteria_summary`，不粉饰未批准/未达标 → 映射到我们 run_status 审计语义（`_apply_terminal_status`）
2. before callback 清 stale decision + 每实例唯一 state key 防跨阶段串扰（`review_confirmation.py:39-58,173-185`）
3. 硬限制常量显式化：TOOL_LOOP_LIMIT=5 / CODING_EVENT_LIMIT=100 / MAX_EVENTS_TO_KEEP=20（`implementation_loop.py:17-20`）
4. 关键检查点手动触发压缩，不依赖回调时机（`stage_orchestrator.py:488`）
5. 只读工具沙箱：路径 resolve 后强制 `relative_to(working_dir)`，越界拒绝（`tools/file_ops.py:63-90`）→ 我们 executor 的路径白名单思路

**不照搬（方向相反，全为反例）**：
- coding agent `permission_mode="bypassPermissions"` 全权限绕过，LLM 自由写码并执行 → 与我们“LLM 只做选择题”根本冲突
- 无 SQLite 持久化、无断点续跑、崩溃即丢
- 无 artifact SHA256、无参数指纹、无工具版本；criteria evidence 是 LLM 自由文本，不可机读校验
- event compression 用 LLM 摘要**直接替换原事件**，原始工具调用/响应不可回溯；Claude 工具响应还被主动丢弃不进上下文 → 与“证据阶梯”背道而驰
- 启动时 `git clone` 第三方 skills 仓库（`claude_code/agent.py:46-85`）→ 供应链风险；我们应内置锁定 registry

### 2.4 scientific-agent-skills（MIT，33.1k★）

**核心机制**：SKILL.md frontmatter 只允许 6 个顶层字段：`name`(1-64,小写,=目录名)/`description`(≤1024)/`license`/`compatibility`(≤500)/`allowed-tools`(空格分隔)/`metadata`（**必须含 metadata.version**）；必须 block-style YAML，strictyaml 拒绝 JSON flow（`AGENTS.md:141-214`、`tests/_contract/structure.py:132-178`）。`scan_skills.py` 是**安全扫描器**（cisco-ai-skill-scanner，检测 prompt injection/数据外泄），用内容 sha256（路径+字节）做增量缓存，扫描器版本/模型变更即失效全缓存（`scan_skills.py:58-67,300-332`）。

**可学习点**：
1. frontmatter 闭集字段 + metadata.version 语义化 bump → 我们的 registry schema 蓝本（机器可校验的版本化能力声明）
2. 内容哈希增量扫描缓存 + 扫描器版本变更失效传播 → 与参数指纹思想同构的“工具版本进指纹”
3. 一个技能一个隔离测试环境（`AGENTS.md:293-318`）→ registry 引擎测试的依赖隔离
4. 每个脚本技能必须有测试套件 + 全局契约测试强制（`tests/_meta/test_repo_contract.py:52-70`）

**不照搬**：技能靠 prompt 驱动决策，我们的决策在规则层 + LLM 选择题；skills 目录与我们的 registry/bridges 定位不同。

### 2.5 OpenDiscoveryTrace（CC-BY，ICML 2026 数据集）

**核心机制**：522 条轨迹 × 7 模型 × 124 任务；每 step 实为 **10 字段**（比 README 说的 9 个多 `raw_response`）：`step_id, timestamp, phase, thought, action{type,tool,input,output}, observation, error{occurred,type,message}, revision_trigger, confidence, raw_response`（`data/samples/frontier/dd_e01_gpt-5.4.json` L12-48）。评测任务：outcome prediction / error localization / claim verification / autonomy classification / process quality scoring。核心结论：“所有前沿模型成功率相同(~69%)，但 Claude Opus 4.6 的错误数是 GPT-5.4 的 30 倍——工具误用 66.7% vs 推理错误 83.6%，只看最终输出完全漏掉”。

**可学习点**：
1. 把 `error{occurred,type,message}` 与 `revision_trigger` 显式建成 step 字段而非混在 observation 里（`src/harness/agent_harness.py:365-455`）→ 审计层数据契约可对齐
2. “每轨迹立即落盘 + 按文件名幂等跳过” = 写盘即 checkpoint 的最小恢复方案（`agent_harness.py:506,571-581`）
3. “output-only benchmarks miss this” 直接支撑我们的证据阶梯叙事；其 4 个评测任务可迁移为“我们自己的 InSAR Agent 轨迹评测集”设计（§13 对齐）

**不照搬**：是离线评测数据集而非引擎；无 artifact 校验、无指纹、无缓存；success 判定是词重叠>0.3 的粗糙启发式（`agent_harness.py:468`），不能作为我们审计层的证据标准。

### 2.6 redun（Apache-2.0，v0.46.0）

**核心机制**：lazy expression → 动态 DAG 图归约；`eval_hash = hash_struct(["Eval", task_hash, args_hash])`（`redun/hashing.py:107`）；task hash = 函数源码或显式 version（`redun/task.py:433-466`）；外部文件用 `(path, size, mtime)` O(1) 伪哈希 + `File.is_valid` 复查（`redun/file.py:463-475,1353`）；call_hash 含排序后的 child_call_hashes 构成 Merkle 树（`hashing.py:124-130`）；三级缓存：进程内 CSE / 单步 Evaluation 缓存（默认）/ CallNode 终极缓存（opt-in shallow）（`backends/base.py:158`、`graph_reduction_caching.md`）。

**可学习点**：
1. 所有 pre-image 加类型前缀 tag（`hash_tag_bytes`）防跨类型哈希碰撞 → Merkle 式指纹
2. 外部文件 (path,size,mtime) 伪哈希 + is_valid 复查 → 我们 stale detection 的外部 artifact 校验
3. task hash=函数源码，代码改动自动失效 → 我们参数级失效外的“方法/代码级失效”补充
4. evaluation 表把 eval_hash/task_hash/args_hash/value_hash 分离存列，缓存命中可审计（`backends/db/__init__.py:612-620`）
5. 三级缓存分层：默认单步保正确性，shallow 可跳中间校验提速

**不照搬**：假设 task 纯函数、参数可 pickle 哈希；InSAR 的 numpy 中间产物/文件副作用会让哈希不稳定；运行时人工改方法=改源码→整链失效，粒度是 task 而非参数；崩溃恢复靠全量重放+缓存快进而非崩溃点续跑；3.2k 行 scheduler 体量远超我们 400-600 行自研目标。

### 2.7 InSAR_Agent（MIT，直接竞品）

**验证结论（DESIGN.md §10.1 全部属实，源码逐行核对）**：
- StepState 仅 5 字段：status/started/finished/outputs/error（`src/insar_agent/workflow.py:40-45`）
- 跳过判定只看 `if step.status == 'done' and not force: return skipped`（`workflow.py:104-107`）；`set_params()` 改参数不碰 status → 参数改了旧产物照样被当完成
- `run_all(start_from: int)` 整数下标断点续跑（`workflow.py:151-160`），MintPy 内部是黑盒
- 两套并行流水线确认存在：WorkflowRunner（写 state.json）与 LLM 直调工具链分离
- 事件工厂分离确认（`src/insar_agent/events.py:23-31`）：tool_result 同时含 LLM 用 data 与前端用 ui_update

**值得抄的 4 个设计（已验证）**：
1. 缓存锚定反幻觉：LLM 只传 scene_indices，系统从磁盘缓存读真值
2. task_id 唯一句柄，路径服务端解析（task_id 永不含 slash）
3. 事件工厂分离（`events.py` 110 行）：给 LLM 的 data 与给前端的 ui_update 在同一个 tool_result 里分离
4. 双 SSE + 协作式取消：对话 120s 超时 / 长任务 30s 心跳，threading.Event 透传 subprocess

**反例清单（绝不能重复）**：明文密码落盘（`tools/submit_insar.py:254`）、`/api/browse` 无鉴权列目录、cookie 现场发、密码裸 sha256 无 salt、零测试、README 与代码不符。

### 2.8 agentic-swmm-workflow（最重要参照物，已发 SCI）

**架构**：LLM planner → Skill 目录 → MCP 传输 → 确定性 swmm5 引擎；执行层/建模记忆层/受控技能演化层三层。
**本次核验的新发现（与 DESIGN.md §10.3 的差异）**：
- ⚠️ **schema 版本已统一为 1.1**：`collect_run`/`build_comparison`/`build_model_diagnostics` 全部 `"schema_version": "1.1"`（`skills/swmm-experiment-audit/scripts/audit_run.py`）→ DESIGN.md“三处分裂”论断在当前 main 已不成立
- ⚠️ **已有守护测试**：tests/ 下 60+ 个 `test_audit_*`（`test_audit_run_schema_v1_1.py`、`test_audit_provenance_v1_2.py`、`test_audit_threshold_hits_integration.py` 等）→ DESIGN.md“指标契约无守护测试”论断已被上游修复
- **新增 `model_diagnostics`**：对 INP 做确定性筛查（缺 rain gage / 出水口缺失 / 负面积 / conduit 坡度异常 / continuity error>5% 等，error/warning/info 分级，error 才 gate）（`audit_run.py build_model_diagnostics`）→ 与我们的“指标来源契约 + 禁用来源硬 gate”同思路
- **新增 `memories_applied`**：审计记录本次运行应用了哪些记忆条目（P0-3），缺失即 `[]` → 我们的审计应记录“应用了哪些场景规则”
- **`run_ok` 修正**：swmm5 退出码 0 但 rpt 里有 solver error 的情况，manifest 写 `run_ok`（rc==0 且无 "ERROR n:" 行），audit 优先读 run_ok 而非 return_code（`audit_run.py derive_status`）→ 关键洞察：**不能只信进程退出码**
- skill 间复用：audit 脚本用 importlib 动态加载其他 skill 的脚本（`parse_wq_loads_from_rpt`）→ 能力目录化后跨能力复用
- provider 路由确认 10 条：openai/anthropic/codex/openrouter/deepseek/groq/gemini/ollama/lmstudio/custom（`agentic_swmm/providers/routes.py:74-180`）

**它的证据阶梯**（`agent/memory/soul.md`）：“runnable smoke test 与 validated modelling claim 严格分开”、“mark incomplete paths as incomplete”。

**我们的改造要点（原 DESIGN.md 已列，本次确认仍有效）**：
- run_id 换 UUID + 单调时间戳（它纯靠目录名，跨机合并会碰撞）
- environment 加 ISCE2/SNAP/snaphu/GDAL 版本 + conda env hash
- artifacts 固定 id 换 slc/ifg/geom/psi 的 id 集
- metrics 换 PS 数、平均相干、速度分布分位数
- 抄它的 agent 快照 hash（tools_schema_sha256 / skills{name:sha} / intent_map_sha256 / system_prompt_sha256 —— **prompt 也进 provenance**）

---

## 3. 跨项目机制对照（直接回答“谁解决哪一层”）

| 机制 | 谁有 | 谁最好 | 对我们的启示 |
|---|---|---|---|
| 参数级失效传播 | DVC / redun(代码级) / Snakemake(五触发器) | 概念上 Snakemake，实现上自研 | **仍是主 novelty 缺口**，无人做到“LLM 动态选方法 + 参数级传播 + 人中途改方法” |
| 崩溃后断点续跑 | aiida(进程级) / workgraph(任务级跳过) / InSAR_Agent(整数下标) / redun(缓存快进) | 均不满足“步骤级 + 在途任务调和” | 自研 SQLite 状态机仍是正确选择 |
| 完整 provenance | aiida(节点图) / redun(call graph) / agentic-swmm(命令轨迹) | agentic-swmm 的 schema 可直接改造 | 以它的 schema 为蓝本改造 |
| 审计守护测试 | agentic-swmm(60+ test_audit_*) | agentic-swmm | 我们必须从第一天就带 contract 测试 |
| 指标来源契约 | agentic-swmm(partial) | 我们要做硬 gate 版 | contract.yaml + 守护测试是差异化点 |
| LLM 约束为选择题 | 无人做到 | — | 保持；BFCL 数据支撑 |
| 轨迹级评测数据 | OpenDiscoveryTrace | — | 对齐其 step schema 造 InSAR 评测集 |
| 技能/能力 schema 校验 | scientific-agent-skills | — | registry schema 蓝本 |

---

## 4. 结论与行动项

**架构方向无需改动**。八项目对照后，没有出现“已有人完成我们主 novelty 组合”的证据。

1. **absorb-A（audit schema）**：以 agentic-swmm `audit_run.py` 的 provenance schema 为蓝本改造成 InSAR 版；**必须包含 `memories_applied` 与 `run_ok` 语义**（进程退出码不可全信）
2. **absorb-B（哈希与存储）**：指纹哈希用 aiida/redun 的顺序无关 + 类型前缀方案；外部产物用 (path,size,mtime)+is_valid 复查
3. **absorb-C（轨迹审计）**：审计日志字段对齐 OpenDiscoveryTrace 10 字段 step schema（含 error/revision_trigger/confidence），支撑论文评测集叙事
4. **absorb-D（registry schema）**：registry/bridges 声明采用 scientific-agent-skills 的闭集字段 + version 语义化 + 契约测试模式
5. **update-DESIGN**：修正 §10.3 两条已过时论断（schema 分裂已修复、已有守护测试），并给竞品分析表加“审计日期”字段（本项目 2026-08-10 更新速度很快，分析有保质期）

**待用户拍板**：
- 是否把 absorb-A~D 正式写进 DESIGN.md（新增一节 “17. 开源对标吸收决议”）
- 是否在论文里加 agentic-data-scientist 对比行（建议：related work 一句带过，不进主表）