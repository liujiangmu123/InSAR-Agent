# Pi Agent 框架深度解析:对 insar-agent 的借鉴分析

> 生成日期:2026-08-12
> 用途:分析 Mario Zechner 的 pi 框架(earendil-works/pi,87.5k★,MIT)及其生态、同类框架,
> 评估哪些机制可以吸收进 insar-agent(可复现 InSAR 科学工作流 Agent)。
> 全部结论基于本地克隆源码核验(`reference/repos/`,浅克隆自 2026-08-12 main 分支),
> 每条可学习点标注 文件路径:行号。格式对齐 `COMPARISON_LEARNING.md`。
> 对照基准:`docs/AGENT-DESIGN.md`(2026-08-10 定稿)。

---

## 0. 本次调研范围

新增克隆(全部在 `reference/repos/`):

| 仓库 | 定位 | 语言 | 与本项目关系 |
|---|---|---|---|
| **pi** | 最小 agent 工具箱(pi-ai / pi-agent-core / pi-coding-agent / pi-tui + 新增 protocol/server/client/session-backends) | TS | 机制蓝本,重点吸收对象 |
| **openclaw** | 多渠道个人 AI 助手(WhatsApp/Telegram/Slack…),pi 之上最大的生产项目 | TS | pi 用法样板(§3) |
| **pi-chat** | Slack/chat 自动化,官方 pi 下游 | TS | pi 用法样板(§3) |
| **mini-swe-agent** | 100 行哲学的 SWE agent | Python | 同类最小化路线(§4) |
| **smolagents** | HuggingFace 代码即动作 agent 框架 | Python | 同类(§4) |
| **claude-agent-sdk-python** | Claude Code 的 SDK 化封装 | Python | 同类,hooks 机制(§4) |
| **deepagents** | LangChain 系,planning/subagent/文件系统中间件 | Python | 同类(§4) |

结论先行(细节在 §5):

1. **pi 整体不能照搬**——它的 LLM 是全权规划者(read/write/edit/bash 自由发挥),与我们「LLM 只做候选集选择题」(`DESIGN.md:348`)方向相反;它默认无权限系统(`pi/README.md:40`),靠容器化兜底。
2. **但 pi 的 AgentHarness 持久化运行时是目前见过的最严格的「agent 崩溃恢复」设计**——「效果三明治」两段提交 + 总状态寄存器 + 工具 `replay: never|safe` 声明,与我们 §4 五阶段执行器解决的是同构问题(它解「对话循环」的崩溃恢复,我们解「外部 CLI 作业」的崩溃恢复),其事务纪律和恢复验收标准可直接抄。
3. pi 证明了「**核心极小 + 一切皆扩展**」在工程上完全可行(subagent/plan-mode/沙箱全是扩展),这直接印证我们「brain 层可整体拔除」的架构约束(`AGENT-DESIGN.md §3.5`)。
4. 八个具体机制值得吸收,映射到我们 Phase 0-5 的落点见 §5.4。

---

## 1. pi 是什么:包分层与设计哲学

### 1.1 包分层(monorepo,`pi/README.md:26-34` + 实测目录)

```
pi-ai            统一多供应商 LLM API(流式、工具调用、思考、成本跟踪、跨供应商上下文交接)
pi-agent-core    agent 运行时:agent-loop.ts(718 行)+ AgentHarness(持久化运行时,规范先行)
pi-coding-agent  完整编码 agent:AgentSession(2981 行)+ 扩展/技能/模板/主题 + 四种运行模式
pi-tui           终端 UI(差分渲染)
pi-telemetry     厂商中立遥测契约
protocol/server/client/session-backends   新增:远程会话与 SQLite 会话后端
```

依赖方向严格单向:coding-agent → agent-core → ai。UI(tui)与逻辑完全分离——
这与我们 UI(prototype)/Loop/Core 的分层同构。

### 1.2 设计哲学(与我们的对照)

| pi 的选择 | 证据 | 我们的对应 |
|---|---|---|
| 默认只给 LLM 四个工具(read/write/edit/bash) | `packages/coding-agent/README.md` | 反向:LLM 零工具,只答选择题 |
| 不内置 sub-agents / plan mode,要则装扩展 | coding-agent README「skips features like sub agents and plan mode」 | 同思路:brain 可拔除 |
| 无权限系统,信任边界外置(容器/沙箱) | `pi/README.md:38-46` | 反向:审批卡/质量门在应用层内置 |
| 系统提示词极小,只注入 AGENTS.md | Zechner 博客 2025-11-30 | 同思路:目标 < 3KB(§3.4) |
| session 即真相源,一切状态可从会话重建 | `docs/extensions.md:1850-1882` | 部分同思路:SQLite 是唯一真相源 |
| 供应链纪律:精确 pin + min-release-age + lockfile 守护 | `pi/README.md:76-88` | 印证我们「内置锁定 registry」决议 |

---

## 2. 源码级机制解析(九个机制)

### 2.1 agent loop:两层循环 + 运行中插话

`packages/agent/src/agent-loop.ts`(718 行,整个内核)。形状:

```
外层 while(true)                         ← 收 follow-up 消息则继续,否则退出(:170-272)
  内层 while(hasMoreToolCalls || pending) ← 工具调用循环(:174-260)
    注入 pending steering 消息(:182-190)
    streamAssistantResponse(:193)
    执行工具(顺序或并行,:207-222)
    prepareNextTurn 钩子(可换模型/换上下文,:232-245)
    shouldStopAfterTurn 钩子(:247-257)
    再取 steering(:259)
  外层取 follow-up(:263-268)
```

**可学习点:**

1. **steering(运行中插话)是循环的一等公民**,不是补丁。检查点在「本轮工具跑完之后、下次 LLM 调用之前」
   (`agent-loop.ts:174-190,259`)。我们 §4.5 干预队列的消费点(timer.tick + 当前步结束后生效)与之对齐,
   pi 验证了这个消费时机的正确性。
2. **`stopReason == "length" 时全部工具调用判失败`**(:207-214, `failToolCallsFromTruncatedMessage` :381-406)。
   截断消息里的工具参数可能被 JSON 补救解析器"修好"但语义不完整,一个都不能执行。
   → 我们 brain/select 的结构化输出同理:**token 截断的 JSON 即使能 parse 也必须整体拒绝**,
   这是 §3.4「索引越界直接拒绝」之外要补的一条。
3. `beforeToolCall` 可拦截(返回 `{block, reason}`,:619-647)、`afterToolCall` 可改写结果(:724-751)、
   `transformContext` 在每次 LLM 调用前变换上下文(:288-292)——三个钩子撑起了全部扩展能力。
4. 工具声明 `executionMode: "sequential"` 则整批降级为顺序执行(:418-425);
   结果带 `terminate: true` 则整批终止循环(:582-584)。
   → 对应我们 capability 的 `io='heavy' 最多 1 并发`(§4.11):**并发约束声明在工具/能力上,不在调用方**。

### 2.2 AgentHarness:持久化运行时(★ 与我们最相关)

`packages/agent/docs/harness.md`(2942 行实现规范;实现进行中,
`agent-harness.ts:74-82` 仍有 `HarnessNotImplemented`,但 session 层已实现并有一致性测试套件
`session/testing/conformance.ts` 946 行)。定位:**「持久化 agent 对话运行时——中断的工作可以恢复,
且已结算的副作用绝不重复」**(`harness.md:82`)。

三存储模型(`harness.md:111-127`):

```
entries    对话树——write-once 追加,永不修改删除
registers  当前可变状态——命名空间化的类型化单元格,覆写或删除
usage      成本账本——追加只读
```

**效果三明治**(`harness.md:129-137`)——这是整个设计的核心:

```
commit:  "我即将做 X;其输出将使用预留 id R 和 U"     ← 意图落盘
         做 X                                        ← 唯一不确定的部分
commit:  输出 + 用量 + 下一个状态                      ← 结算落盘
```

**程序计数器**(`harness.md:127`):每步之后覆写唯一寄存器 `op.state/{operationId}`,
存**完整**当前状态(total state,绝不依赖前一个状态)。恢复 = 读这个寄存器 + switch,
**不重放日志、不从缺失推断位置**(`harness.md:1805-1858`:恢复只做 5 个寄存器点查 + 有界校验)。

崩溃位置与恢复策略全表(`harness.md:1894-1916`)。唯一不确定窗口「意图已落盘、结算未落盘」的三条策略:

| 恢复到的状态 | 策略(`harness.md:1910-1916`) |
|---|---|
| generation effect_pending | 按**捕获时**的重试策略决定是否开新 attempt;到上限则在预留 id 下写合成 error;若取消已落盘则写合成 aborted 且绝不重试 |
| tool effect_pending | 仅当**存储的声明和当前声明都是 safe** 才用持久化参数重放;否则在预留 id 下写合成 interrupted 错误 |
| deferred effect_pending | 等下一次 resume(),每次 poll 用新预留 id |

**与我们 §4 的关系——同构问题的两种解:**

| 维度 | pi AgentHarness | 我们(AGENT-DESIGN.md §4) |
|---|---|---|
| 保护对象 | LLM 请求 + 进程内工具调用 | WSL 内外部 CLI 作业(小时级) |
| 意图落盘 | `op.state = effect_pending` + 预留 id | LAUNCHED 阶段(pid/job_dir 落盘) |
| 结算落盘 | 响应 entry + usage + 下一状态,一个事务 | job.rc + COLLECTED/VERIFIED 推进 |
| 恢复入口 | restore() 5 个点查(`harness.md:1810-1844`) | store.load(step_id) + 阶段守卫 |
| 副作用重复判定 | 工具 `replay: never\|safe` 声明 | 隐式:五阶段守卫 + job_state 判活 |
| 中断的合成结算 | 预留 id 下写 synthetic interrupted | orphaned 状态 + 保留部分产物 |
| 外界杀死作业 | §4.9 外部终结:条件事务发现寄存器已删则停 | 尚无对应(见 §5.2-A6) |

**可直接抄的三条纪律:**

1. **恢复的验收标准写死为「有限点查 + 有界校验」**(`harness.md:1856`:
   "What restore never does: read register history, fold anything, scan tables, …, or infer state from what is absent")。
   我们 Phase 1 验收里加一条:executor 恢复路径禁止扫描 steps 表推断状态,只允许按 step_id 点查。
2. **状态是 total 的**:`op.state` 从不依赖上一个值(`harness.md:127`)。我们 steps 表的 stage 字段已是这个方向,
   但要保证恢复所需的一切(log_offset/pid/job_dir/config_hash)都在行内或可由确定性键定位,
   不需要 join 历史表——对照 `harness.md:1087`(大参数放 `op.tool_args` 寄存器,由确定性键 `{opId}:{stepId}:{sourceIndex}` 定位)。
3. **abort 是 control 位不是状态**(`harness.md:1918-1935`):取消请求落盘后,已发起的效果仍允许结算,
   禁止发起新效果;`close() = 受控崩溃`,不写任何东西(`harness.md:1937-1954`),重开走同一条恢复路径。
   → 我们的 INTERRUPTED 处理可对齐:**取消后不阻止 job.rc 写入**(WSL 侧作业结算),只阻止新步骤启动;
   服务正常退出就应该什么都不写,让下次启动走统一 reattach 路径——不需要单独的"优雅关闭"状态。

### 2.3 工具 replay 声明(把幂等性提升为类型)

- 会话层工具启动记录带 `replay: "never" | "safe"`(`packages/agent/src/harness/session/types.ts:150-160`);
- harness 工具类型 `HarnessTool = AgentTool & { replay?: "never" | "safe" }`(`agent-harness.ts:237`),缺省 never;
- 崩溃在工具执行中:never → 在预留 result id 下写合成 "interrupted" 错误,**绝不重跑**;
  safe → 用持久化参数重放(`harness.md:180-205` 的 worked example:删文件的工具崩溃后不会删两次)。

→ **吸收决议 absorb-E1**:我们 `registry/capabilities.py` 每个 capability 加显式声明:

```python
Capability(id='snaphu_mcf',      replay='never')   # 外部副作用,恢复走 job_state 判活
Capability(id='probe_env',       replay='safe')    # 纯探测,恢复可直接重跑
Capability(id='discover_artifacts', replay='safe') # 产物发现幂等
```

五阶段守卫回答"从哪继续",replay 声明回答"这一步能不能直接重来"——两者正交,
后者让 PREPARED(渲染配置,safe)和 LAUNCHED(启动进程,never)的恢复差异变成数据而非代码。

### 2.4 session:entry 树 + lanes + 持久化队列 + fork

`packages/agent/src/harness/session/types.ts`:

- **Entry 树**:每个 entry 带 `parentId`(:14-20),类型含 message / compaction / branch_summary / **custom**(:61-74)。
  分支即对话线程,支持 `/tree` 导航、fork(:359-372,scope 可选 branch 或 tree)。
- **LaneRecord = 操作日志(WAL)**:`operation_started / abort_requested / operation_finished /
  step_attempt / tool_started / queue_enqueued / queue_cancelled / write_deferred / usage`(:80-215)。
- **三个队列全部落盘**:`QueueEnqueuedRecord` 区分 `steer | followUp | nextRun`(:162-176)。
  → 我们 `pending_actions` 表(§4.5)的思路被 pi 印证;pi 还多一个 `nextRun`(闲时排队,下次运行才消费),
  对应我们可加的「排队到下个 run」语义(用户在运行结束前预约下一次处理)。
- **恢复接口自带语义**:`findOpenOperations(lane, {limit: 2})`——0 个 = 空闲,1 个 = 挂起可恢复,
  2 个 = 损坏(:311-317 注释原话)。**把"多少个未完成操作算损坏"写进接口契约**,值得抄。
- JSONL 后端:追加写 + **tmp+rename 原子发布**(`session/jsonl/storage.ts:33-46`)+
  **残尾行自动修复**(读到最后一行 JSON 语法错则原子重写有效前缀,:80-92)。
  与我们「原子写 tmp+os.replace」决议(absorb 自 agentic-swmm)一致,残尾修复是新增可抄点。

**对 InSAR 的独特启发——run fork(见 §5.2-A5)**:pi 的树分支对应到我们场景就是**参数试探分支**:
「第 6 步换 snaphu_smooth 再跑一遍,保留原链」= fork run,未失效步骤直接引用父 run 产物
(指纹未变则复用,天然由 stale 传播决定)。这是 pi 的树结构 + 我们的指纹系统的自然组合,竞品全都没有。

### 2.5 扩展系统:事件(被动)与钩子(拦截)分离

coding-agent 层(`docs/extensions.md`)。要点:

- 扩展 = 导出默认工厂函数的 TS 模块,收 `ExtensionAPI`(:56-107);jiti 加载,免编译(:179)。
- **完整事件生命周期**(:277-348):`session_start → input(可拦截改写) → before_agent_start(可注入消息/改系统提示词)
  → agent_start → turn_start → context(可改消息) → tool_call(可 block) → tool_result(可改写) → turn_end → agent_end → agent_settled`,
  外加 session_before_switch/fork/compact/tree(都可取消)。
- harness 层把这个分离形式化(`harness.md:2325-2327`):**events 是被动的——监听器不能改变执行,
  抛错只产生 handler_error 事件,绝不影响执行;只有 hooks 能拦截**。
  hooks 共 11 个,带**重放矩阵**(fresh/retry/resume 各触发几次,`harness.md:2594-2607`),
  其中 `before_tool` 特殊:**抛错时 fail closed,阻止工具执行**(`harness.md:2566`)。
- 扩展状态管理规范(:1850-1882):状态存在 tool result 的 `details` 里,
  `session_start` 时**从当前分支**重建——分支切换后状态自动正确。
- `withFileMutationQueue`(:1896-1923):自定义工具若改文件,必须加入按 realpath канonical 化的
  per-file 互斥队列,否则并行工具调用会互相覆写。
- 40+ 官方示例(:2915-2980):permission-gate(危险命令确认)、git-checkpoint(每 turn 自动 stash)、
  protected-paths、plan-mode(完整计划模式)、sandbox、**subagent**——全部作为扩展实现,核心零改动。

**可学习点(→ 我们 loop/events.py):**

1. **「UI 监听崩溃不影响主循环」要成为显式保证**。我们的事件工厂已分离 data/ui(借鉴 InSAR_Agent events.py),
   但没写"UI 消费者抛错怎么办"。抄 pi:被动监听器全部 try/except 包裹,错误变成一条 `handler_error` 轨迹条目。
2. **拦截点显式枚举且带重放语义**。我们的审批卡(§7.6)本质是 before_tool 钩子;质量门(§4.6)本质是 after_tool 钩子。
   把它们建模为「闭集钩子 + fail-closed 语义」而非散落的 if:审批钩子抛错 = 拒绝执行(fail closed),
   叙事钩子抛错 = 跳过继续(fail open)。
3. **每个钩子在崩溃恢复后触发几次必须写进契约**(pi 的重放矩阵)。对我们:恢复后质量门要不要重新判?
   (答:VERIFIED 守卫已保证不重判;但 narrate 类钩子可能重发,需幂等或带 durable marker——
   `harness.md:2607` 原话:"Handlers that must not double-fire keep their own durable marker")。

### 2.6 skills:渐进披露的领域知识包

- 标准:实现 agentskills.io 规范(`docs/skills.md:7`),SKILL.md frontmatter 闭集
  (name/description 必填,license/compatibility/metadata/allowed-tools/disable-model-invocation 可选,:141-149)。
- **机制成本极低**(:64-71):启动时只扫描 name+description;系统提示词只含描述的 XML 清单
  (`src/core/skills.ts:335-358` `formatSkillsForPrompt`,`<available_skills><skill>…` 格式);
  任务匹配时 agent 自己用 read 加载全文;`/skill:name` 强制加载
  (注入格式 `<skill name=… location=…>`,`agent-session.ts:1322`)。
- 缺 description 则不加载;重名保留先发现者(:186-188)。

→ 结合 scientific-agent-skills 的闭集 schema(COMPARISON_LEARNING.md §2.4 已分析),
落点在我们 registry/scenarios(见 §5.2-A7)。

### 2.7 compaction:切点规则与「压缩不是删除」

`docs/compaction.md` + `src/core/compaction/compaction.ts`(854 行):

- 触发:`contextTokens > contextWindow - reserveTokens(默认 16384)`(:29-35);
  从新往旧累计到 `keepRecentTokens(默认 20k)` 找切点(:41)。
- **切点规则**(:109-117):只能切在 user/assistant/bashExecution/custom 消息处,
  **绝不切在 toolResult**(必须跟它的 toolCall 在一起)。
- 单 turn 超预算 → split turn:生成「历史摘要 + turn 前缀摘要」两段合并(:81-107)。
- 迭代摘要:重复压缩时带上一次摘要作为上下文(:43);跟踪文件操作(read/written/edited)累计进摘要。
- **压缩不改存储**:`CompactionEntry` 只是新 entry,原始消息永不删除
  (`harness.md:214`:"compaction changes provider context, not storage")。

→ 对我们 loop/budget.py(§3.3 约束四):我们决策请求无历史,压缩需求弱;
但 intent/narrate 用的对话历史可直接抄切点规则。红线:**摘要只喂 LLM,绝不进 provenance**
——证据必须重解析(audit/verify.py),这一点与 pi 的「压缩不是删除」哲学一致而用途不同。

### 2.8 四种运行模式与 RPC 协议

同一个 `AgentSession` 四种驱动(deepwiki 证实 + `src/modes/` 目录):
interactive(TUI)/ print(单发)/ **RPC(stdio JSONL)** / SDK(进程内嵌入)。

RPC 协议(`docs/rpc.md`)三分:**Commands(stdin 入)/ Responses(带 id 关联)/ Events(stdout 流出)**。

**对我们 API 层(Phase 5)最有价值的一条**:`prompt` 命令在 agent 正在流式输出时,
**必须显式带 `streamingBehavior: "steer" | "followUp"`,否则直接报错**(`rpc.md:56-65`)。
运行中投递消息的语义(现在插话 vs 等跑完再说)强制调用方声明,杜绝歧义。
→ 我们 FastAPI 的 POST /message 在 run 进行中时应同样强制 `mode=steer|follow_up|next_run` 参数。

响应与事件分离的纪律(`rpc.md:76`):`success:true` 只表示「已接受/已排队」;
接受之后的失败走事件流,**绝不给同一个请求 id 发第二个 response**。
→ 对应我们双 SSE 设计:HTTP 响应只答"收到",结果走 SSE,不混用。

### 2.9 subagent:官方示例的进程隔离模式

pi 核心无子代理,官方示例用扩展实现(`examples/extensions/subagent/index.ts`,1033 行):

- spawn `pi --mode json -p --no-session` 子进程(:300),逐行消费 stdout JSONL 事件(:353-395);
- 三种模式:single / parallel(上限 8 任务、并发 4,:33-34)/ chain(`{previous}` 占位符串接,:545-597);
- 每任务输出截断 50KB,全量保留在 details(:36,:193-202);
- agent 定义按 user/project 两级目录发现,**项目级 agent 运行前需用户确认**(供应链防护,:520-543);
- 子进程被 abort:SIGTERM → 5 秒后 SIGKILL(:410-420)。

→ 我们不需要 LLM 子代理,但这个「**spawn 进程 + 消费结构化事件流 + 全量 details 落盘 + 摘要给上层**」
的形状,正是我们 WSL 作业执行器(§4.7)的形状——pi 用 stdout JSONL,我们用作业目录文件契约,
后者对小时级任务更稳(stdout 断了就没了,文件可以 reattach)。方向被双向印证。

---

## 3. 基于 pi 的项目如何用 pi(openclaw / pi-chat)

*(后台源码探查进行中,本节待补:openclaw 的 pi 会话驱动方式、多渠道网关与会话解耦、
memory/heartbeat/cron 实现、长任务与会话恢复机制;pi-chat 的定位与差异。)*

---

## 4. 同类框架横向对比

*(后台源码探查进行中,本节待补:mini-swe-agent / smolagents / claude-agent-sdk-python / deepagents
的主循环形状、步骤数据结构、中断恢复、LLM 约束方式,以及与 pi 的对照表。)*

已有结论可先记录的部分:

| 框架 | 主循环归属 | 状态持久化 | 对我们的主要价值 |
|---|---|---|---|
| pi | 自研 TS(agent-loop.ts 718 行) | JSONL/SQLite 会话 + 寄存器 | 崩溃恢复事务纪律(§2.2-2.3) |
| mini-swe-agent | 自研 Python(~100 行核心) | 待核验 | 最小主循环参照 |
| smolagents | 自研 Python | 待核验 | ActionStep 步骤记录结构 |
| claude-agent-sdk-python | **外包**(封装 Claude Code CLI 子进程) | Claude Code 会话 | hooks/权限模式 API 设计 |
| deepagents | LangGraph | checkpointer | middleware/interrupt 模式 |

---

## 5. 对 insar-agent 的应用分析(核心章节)

### 5.1 哲学对比:为什么不能整体照搬

pi 与我们在「LLM 的角色」上是光谱两端:

```
pi:      LLM 全权规划者。四个通用工具,靠模型能力 + skills 完成一切。
          适用前提:前沿大模型、失败可重试、错误代价 = 重新生成。
我们:     LLM 候选集选择器。命令由 registry+render 确定性生成。
          适用前提:本地 14B、失败代价 = 小时级重算、论文要求可复现。
```

`DESIGN.md:353-360`(BFCL v4:本地 14B 端到端仅 41%)决定了我们不能走 pi 的路线。
**照搬 pi = 把 InSAR 处理交给 LLM 写 bash,这正是 InSAR_Agent 失败的方向的加强版。**

但这不影响机制层面的吸收——pi 的价值恰恰在于:它把「agent 系统的工程骨架」
(循环/持久化/恢复/扩展/协议)做到了与「LLM 用法」正交。骨架可抄,用法不抄。

### 5.2 吸收决议(absorb-E 系列,编号接续 COMPARISON_LEARNING.md 的 A-D)

| # | 吸收点 | 来源证据 | 落点 | 优先级 |
|---|---|---|---|---|
| **E1** | capability 加 `replay: never\|safe` 声明,恢复语义数据化 | `session/types.ts:159`、`harness.md:1915` | `registry/capabilities.py` + executor 恢复分支 | **P0**(Phase 0 schema 就位) |
| **E2** | 意图/结算两段提交纪律 + 「恢复 = 点查 + 有界校验,禁止扫描推断」写进验收标准 | `harness.md:129-137,1805-1858` | `core/store.py` + Phase 1 验收清单 | **P0** |
| **E3** | 取消 = control 位:取消后允许在途作业结算,禁止新效果;进程退出不写状态,统一走 reattach | `harness.md:1918-1954` | `runtime/executor.py` + `core/store.py` | P1 |
| **E4** | 干预队列三分:steer(当前步后生效)/ follow_up(run 结束后)/ next_run(下个 run);运行中投递强制声明模式 | `rpc.md:56-65`、`session/types.ts:162-176` | `core/actions.py` + `api/app.py` | P1 |
| **E5** | run fork:参数试探分支。fork 时复制指纹,未脏步骤引用父 run 产物 | `session/types.ts:359-372` + 我们的 stale 传播 | `core/schema.sql`(runs.parent_run_id)+ planner | P2(**论文亮点候选**) |
| **E6** | 外部终结协议:管理工具可对挂起 run 写终结事务,活着的执行器用条件事务发现后自停 | `harness.md:1960-1968` | `core/store.py`(乐观并发:UPDATE … WHERE stage=?) | P2 |
| **E7** | 场景知识 skill 化:frontmatter 闭集 + 描述常驻提示词 + 全文按需加载 | `docs/skills.md:64-71`、`skills.ts:335-358` | `registry/scenarios/`(SKILL.md 格式) | P1-P2 |
| **E8** | 被动事件/拦截钩子分离;监听器抛错隔离为 handler_error;钩子重放矩阵写进契约 | `harness.md:2325-2327,2566,2594-2607` | `loop/events.py` + `audit/`(质量门=after 钩子) | P1 |
| **E9** | 截断响应整体拒绝:stopReason=length 时所有"可解析"的结构化输出一律判无效 | `agent-loop.ts:207-214,381-406` | `brain/select.py` 值域校验之前 | P0(一行逻辑) |
| **E10** | JSONL 残尾自动修复(崩溃写了半行 → 原子重写有效前缀) | `jsonl/storage.ts:80-92` | 我们日志/轨迹 JSONL 读取器 | P1 |

三条展开说明:

**E2(事务纪律)**。我们五阶段执行器已经有 aiida 式守卫,pi 补充的是两个更严的点:
(a)**预留 id**:意图提交时就把结果的 id 定死(`harness.md:151`),崩溃后合成结算写在同一个 id 下,
下游引用永远有效。对我们:LAUNCHED 意图落盘时就分配 `command_id`/`log_path`,
orphaned 合成结果写进同一条 commands 记录,不新开行。
(b)**恢复禁止推断**:`harness.md:1856` 列出的「restore 绝不做的事」直接变成我们的负面测试:
杀进程后重启,断言恢复路径的 SQL 只有主键点查。

**E5(run fork)展开**。这是 pi 的树结构与我们指纹系统的组合创新,值得作为 §13 评测叙事的一部分:

```
run A: 1─2─3─4─5─6(snaphu_mcf)─7─8      用户:「6 换 snaphu_smooth 对比一下」
run B = fork(A, at=6, params={method: snaphu_smooth})
  → B 的 1-5:args_hash 未变 → 直接引用 A 的产物(零重算)
  → B 的 6-8:标脏重跑
  → UI:两条链并排对比(PS 数、覆盖率、时序曲线)
```

实现代价小(runs.parent_run_id + artifacts 查找先查本 run 再查祖先链),
但把「参数敏感性分析」从"手动改参数再跑一次然后自己记别混了"变成一等操作——教学场景尤其有价值。

**E7(场景 skill 化)展开**。现设计 `registry/scenarios.py` 是纯数据(冻土→周期,地震→阶跃)。
改为目录式技能包:

```
registry/scenarios/permafrost/
├── SKILL.md          # frontmatter: name/description/metadata.version
│                     # 正文:选参依据、周期模型设定、质量门侧重、文献引用
├── references/       # 详细文档(narrate 引用其段落生成方法章节)
└── templates/        # stepFunc/periodic 模型配置片段
```

intent 阶段只见描述清单(≈ pi 的 `<available_skills>` XML);场景确认后全文进 narrate/select 的上下文。
版本进 provenance(`metadata.version`,对齐 scientific-agent-skills 的语义化 bump 决议 absorb-D)。

### 5.3 明确不照搬清单

| 不抄 | 原因 |
|---|---|
| LLM 持 bash/write 工具自由执行 | `DESIGN.md:348` 硬约束;BFCL 数据;InSAR_Agent 反例 |
| 无权限系统、信任外置到容器 | 我们的审批卡/质量门/预算闸门是产品核心,必须内置 |
| session 单一真相源(对话=状态) | InSAR 的状态在外部世界(文件/进程/WSL),SQLite 记录的是**处理**真相;对话历史只是其一部分 |
| compaction 摘要参与任何证据链 | 证据必须重解析(audit/verify.py);摘要只服务 LLM 上下文 |
| deferred(15m/1h/24h 延迟批处理) | 那是 LLM 供应商批量接口的封装,不是长任务方案;但其 suspended/resume 状态形状可参考 |
| TS/jiti 动态扩展加载 | 我们是 Python;且科学系统的能力集应锁定(registry),不做运行时热插拔 |
| TUI 差分渲染栈 | 我们是 Web(prototype 已实现) |

### 5.4 分 Phase 落地(对照 AGENT-DESIGN.md §9)

| Phase | 动作 | 对应吸收项 |
|---|---|---|
| Phase 0(地基) | capabilities schema 加 `replay` 字段;store 状态行补全恢复所需字段(total state 原则) | E1、E2 |
| Phase 1(执行层) | executor 意图/结算两段提交;恢复负面测试(禁扫描);取消=control 位;残尾修复 | E2、E3、E10 |
| Phase 2(失效传播) | actions 表三队列语义(steer/follow_up/next_run) | E4 |
| Phase 4(Brain/Loop) | select 的 length 整体拒绝;events 监听器错误隔离;钩子重放矩阵文档化 | E9、E8 |
| Phase 5(API+前端) | POST /message 强制投递模式;响应/事件流分离纪律 | E4、E8 |
| P2 增强 | run fork;场景 skill 化;外部终结协议 | E5、E7、E6 |

---

## 6. 结论

1. **架构方向再次确认无需改动**。pi 站在 LLM 角色光谱的另一端,但它的工程骨架
   (事务纪律/恢复模型/事件-钩子分离/协议设计)与我们自研执行层的需求高度互补。
   对照之后,我们的主 novelty(参数级 stale 传播 + LLM 候选集约束 + 步级续跑)依然无人组合实现。
2. **最大单项收获是 harness.md 这份规范本身**:它把「崩溃恢复」从代码模式提升为可验收的契约
   (崩溃位置全表、恢复策略三条、restore 负面清单、invariants + race catalog)。
   我们的 §4 应按同样的严格度补一份「崩溃位置 × 恢复动作」全表,作为 Phase 1 的测试蓝本。
3. **E1(replay 声明)和 E9(截断整体拒绝)是两个立即可做的小改动**,建议直接进 Phase 0/4 的任务清单。
4. **E5(run fork)是唯一建议新增的功能级想法**,它把 pi 的树结构嫁接到我们的指纹系统上,
   成本低、教学与论文叙事价值高,建议进 P2 并在 DESIGN.md 「17. 开源对标吸收决议」中记录。

**待用户拍板:**
- absorb-E1~E10 是否写进 DESIGN.md 新增一节「18. pi 对标吸收决议」;
- E5(run fork)是否升级为 P1(影响 schema:runs.parent_run_id 最好 Phase 0 就加,加了不用没成本);
- 是否需要我把 §2.2 的「崩溃位置 × 恢复动作」全表按我们五阶段执行器展开成附录(Phase 1 测试蓝本)。
