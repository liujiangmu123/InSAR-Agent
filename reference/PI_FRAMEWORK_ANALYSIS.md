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

## 3. Pi 生态周边(2025-2026 现状)

> 本节回答三件事:pi 本体这一年发生了什么;谁在生产环境用它、怎么用;生态里有什么值得抄。
> 证据混合:本地克隆(pi / openclaw / pi-chat,2026-08-12)+ 网络调研,网络来源随文给链接。

### 3.1 pi 本体:从个人项目到 Earendil(近一年时间线)

| 时间 | 事件 | 出处 |
|---|---|---|
| 2025-11-12 | `@mariozechner/pi-coding-agent` npm 首发(badlogic/pi-mono 时期) | [npm](https://www.npmjs.com/package/@mariozechner/pi-coding-agent) |
| 2025-11-24 | Peter Steinberger 以 pi 为 agent 内核发布 Warelay(即后来的 OpenClaw),两个月内冲到数十万 star,pi 随之出圈 | §3.2 |
| 2025-11-30 | 设计宣言《What I learned building an opinionated and minimal coding agent》,含 Terminal-Bench 2.0 上与 Codex/Cursor/Windsurf 的对比跑分 | [mariozechner.at](https://mariozechner.at/posts/2025-11-30-pi-coding-agent/) |
| 2026-04-08 | **Earendil Inc.(Armin Ronacher 与 Colin Hanna 2025 年创立的公益公司)收购 pi 项目**,Mario Zechner 成为主要股东与团队成员;同日 Mario 发《I've sold out》;Earendil 支持者含 Steinberger、Sentry/Slack/Revolut/n8n 创始人 | [公告](https://earendil.com/posts/announcing-pi-and-lefos/)、[博客索引](https://mariozechner.at/) |
| 2026-05-07 | npm scope 迁移:`@mariozechner/*`(止于 0.73.1)→ `@earendil-works/*`(0.74.0 起);仓库为 earendil-works/pi | [npm](https://www.npmjs.com/package/@earendil-works/pi-coding-agent) |
| 2026-08-07 | 最新 0.84.1;周下载约 165 万、483 个依赖包;官方文档站 [pi.dev](https://pi.dev/),Discord 设包分享频道 | 同上 |

**文章/演讲(近一年,按时间):**
《Prompts are code, .json/.md files are state》(2025-06-02)、《MCP vs CLI: Benchmarking Tools for
Coding Agents》(2025-08-15)、《What if you don't need MCP at all?》(2025-11-02,pi 无 MCP 决策的论据)、
pi 设计宣言(2025-11-30)、《Thoughts on slowing the fuck down》(2026-03-25,解释 pi 的慢开发哲学)、
《I've sold out》(2026-04-08,收购当日);演讲《Building pi in a World of Slop》(2026);
[Pragmatic Engineer 播客 2026-04-29](https://newsletter.pragmaticengineer.com/p/building-pi-and-what-makes-self-modifying)
(与 Armin Ronacher 对谈:pi 因 Claude Code 加功能后行为不可预测而生,「少加功能=行为稳定」;
pi 定位是「让造专用 harness 变得容易」);Armin Ronacher 2026-02-02
《Pi: The Minimal Agent Within OpenClaw》([lucumr.pocoo.org](https://lucumr.pocoo.org/))。

**仓库近一年机制演进亮点(本地克隆 CHANGELOG 核验,补 §1-2 的静态视角):**

1. **会话层大改:v4 lane-based Session**(`pi/packages/agent/CHANGELOG.md:19-42`,0.84.0,2026-08-06)
   ——durable operation records、global facts、共享序列号、树作用域 lane 视图;旧 JSONL/内存后端全部
   换成实现统一 `SessionRepo` 契约的 v4 版;**为原子 JSONL 发布把 `renameFile()`(同文件系统替换语义)
   写进执行环境接口**——我们已吸收的「tmp+rename 原子写」在 pi 里被上升为接口契约。
2. **AgentHarness 仍在实现中**:0.84.0 提供 compile-complete 脚手架,未完成路径抛
   `HarnessNotImplemented`(`agent/CHANGELOG.md:32`)——印证 §2.2 的判断:harness.md 是规范先行,
   我们抄的是规范而非等它的实现。
3. **恢复查询进接口**:带索引的 `Session.findOpenOperations()` + `RecordQuery.operationKind` 过滤
   (`agent/CHANGELOG.md:34`)——§2.4「0/1/2 个未完成操作=空闲/可恢复/损坏」的落地。
4. **RPC/JSON 事件改增量**:`message_update` 只发 `assistantMessageEvent` delta,删除累计 `message`
   字段;原设计每个 delta 都带全量消息,输出随消息长度二次方增长
   (`pi/packages/coding-agent/CHANGELOG.md:66`,#7290)。→ 直接教训:**流式事件只发增量,
   `message_end` 才是权威全量**(吸收为 F3)。
5. **实验性约束采样**:`PI_EXPERIMENTAL=1` 下对 read/bash/edit/write 启用 strict JSON-schema
   constrained sampling(`coding-agent/CHANGELOG.md:8`,Unreleased)——工具参数在采样阶段就受
   schema 约束。→ 对我们 brain/select:本地部署 14B 时用 vLLM guided_json / llama.cpp GBNF
   把「候选集选择题」的值域错误在采样层杜绝,与 E9(截断整体拒绝)互补(吸收为 F6)。
6. **新增包**:protocol / server / client / session-backends(sqlite-node)/ evals
   (本地 `pi/packages/` 目录)——官方把「远程会话 + SQLite 会话后端」提上主线,TUI 降为众多前端之一。

### 3.2 OpenClaw(原 Clawdbot / Moltbot):pi 之上最大的生产项目

**现名查证**:五个名字三个月——Warelay(2025-11-24 首发)→ CLAWDIS(2025-12-03)→ Clawdbot
(2026-01-02)→ Moltbot(2026-01-27,Anthropic 商标施压,作者自述「非我所愿」)→ **OpenClaw**
(2026-01-30 至今)([Wikipedia](https://en.wikipedia.org/wiki/OpenClaw))。GitHub
[openclaw/openclaw](https://github.com/openclaw/openclaw) 约 38 万 star(2026-08),MIT;
2026-02-14 Steinberger 宣布加入 OpenAI,项目移交 OpenClaw Foundation。主流媒体持续报道其安全争议
(暴露网关、提示注入面、一键技能安装),作者自认「需要仔细配置才安全,不面向非技术用户」
([CNBC 2026-02-02](https://www.cnbc.com/2026/02/02/openclaw-open-source-ai-agent-rise-controversy-clawdbot-moltbot-moltbook.html))。

**与 pi 的关系(本地克隆核验)**:发端时直接嵌 pi 作 agent 内核(Earendil 公告称 pi 为
「the minimal agent within OpenClaw」);至 2026-08 主干,lockfile 里 pi 家族只剩
`@earendil-works/pi-tui 0.82.1`(`openclaw/package.json:2018`),agent 运行时已内化为自己的
`src/agents|gateway|cron|memory|hooks|fleet/…`。**「以极小内核起步、长大后内化」正是 pi
『核心极小、皆可替换』哲学的成功案例,也再次印证我们「brain 层可整体拔除」的架构约束。**

**值得记录的机制**(来自 [docs.openclaw.ai](https://docs.openclaw.ai/concepts/architecture)):

| 机制 | 内容 | 对我们 |
|---|---|---|
| 网关 wire protocol | WS 类型化帧 req/res/event 三分;首帧必须 `connect` 否则硬断;**副作用方法(send/agent)强制幂等键,服务端短期去重缓存** | → F1;与 §2.8 RPC 纪律同源,幂等键是增量 |
| 事件不重放 | 「Events are not replayed; clients must refresh on gaps」,事件帧带 seq/stateVersion | → F2,双 SSE 断线语义 |
| 会话路由 | 按来源定会话:DM 共享、群/房间隔离、**cron 每次跑新会话**、webhook 隔离([session](https://docs.openclaw.ai/concepts/session.md)) | 印证「一次处理=一个 run」 |
| 会话重置 | daily/idle 双模式;**heartbeat/cron/exec 等系统事件可写元数据但不延长 freshness**;重置时丢弃旧会话排队的系统通知 | → F5「系统事件不得给会话续命」 |
| heartbeat vs automations | heartbeat=近似周期(默认 30min)、主会话上下文、**不产生任务记录**、忙时自动让位;automations=精确 cron/one-shot/webhook、独立会话、**必有任务记录**([automation](https://docs.openclaw.ai/automation)) | → F5 分层:我们的判活轮询≈heartbeat,用户可见调度≈automations |
| 记忆/技能 | 记忆=markdown 文件(账户级+渠道级);技能=`~/.openclaw/skills/<name>/SKILL.md`,同 agentskills.io 规范 | 印证 E7 场景 skill 化 |
| doctor --fix | 迁移模式:先物化系统持有的 DB 行,再导入旧文件内容,归档原件后删除;运行时只读 DB 不读旧文件([heartbeat](https://docs.openclaw.ai/gateway/heartbeat)) | schema/配置迁移的好样板 |

### 3.3 pi-chat 与「远程会话」方向(附 pi-web / pi-tui 现状)

[earendil-works/pi-chat](https://github.com/earendil-works/pi-chat)(官方下游,本地克隆):
把 Discord/Telegram 桥接到沙箱化 pi 会话(勘误 §0 表:桥接对象是 Discord/Telegram;
Slack 方向的下游示例是 `@mariozechner/pi-mom`)。机制:

- **每渠道一个 [Gondolin](https://github.com/earendil-works/gondolin) micro-VM**(Alpine/QEMU),
  工具全部在 VM 内执行;持久工作区 + 账户/渠道两级记忆文件(`pi-chat/README.md:3,32-37`);
- agent 可自造技能(SKILL.md 格式)并自动发现注入提示词(`README.md:152-170`)
  ——**运行时能力自增长**,与我们「registry 锁定能力集」相反,明确不抄;
- **worker 舰队管理**:每渠道一个 detached tmux/pi worker,**每 15 秒向
  `~/.pi/agent/chat/worker-status/` 覆写状态快照 JSON,编排者读快照而非探进程**(`README.md:81`)→ F4;
- `ConversationRuntime` 自述「日志状态机 + 作业队列 + 切片构建 + 检查点管理」(`pi-chat/AGENTS.md`)
  ——聊天驱动的循环同样在向「先落盘再行动」收敛;
- 秘密管理:agent 只见占位符环境变量,Gondolin 仅对白名单主机的出站 HTTP 替换真值,
  **agent 永远看不到真实秘密**(`README.md:176-184`)——「凭据不过 LLM」的干净实现,
  对我们 probe_env / 凭据处理是好参照。

**pi-tui 现状**:持续作为核心包演进(0.84.x:运行时切换的全屏模式、Mermaid/LaTeX 终端渲染、
差分渲染三策略 + CSI 2026 同步输出),OpenClaw 至今直接依赖它。
**pi-web 现状**:两条线——官方主线转向 protocol/server/client/session-backends 远程会话栈
(§3.1-6;badlogic 时期曾有 `pi-web-ui` Lit 组件包);社区有 [PI WEB](https://pi-web.dev/)
(服务端常驻 daemon 持有会话,浏览器/手机只是控制面,关浏览器会话继续跑)。
**「会话活在服务端、UI 是可断连的控制面」已成 pi 生态共识,我们 FastAPI + 双 SSE 架构被三方印证。**

### 3.4 扩展/技能生态:有什么值得抄

**分发机制**([packages.md](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/packages.md)):
`pi install npm:@foo/bar@1.2.3 | git:host/user/repo@ref`;npm keyword `pi-package` 可搜;
git ref pin 死后 `pi update --all` 自动跳过;安装用 `npm install --omit=dev` + `--ignore-scripts`
——供应链纪律与 §1.2 一脉相承,再次印证我们「内置锁定 registry」决议。

**官方 50+ 示例扩展**(本地 `pi/packages/coding-agent/examples/extensions/`)按主题分:

- 安全闸:permission-gate、confirm-destructive、**timed-confirm(带超时的确认)**、protected-paths、
  dirty-repo-guard、project-trust;
- 过程留痕:git-checkpoint(每 turn stash)、auto-commit-on-exit、bookmark、session-name;
- 人机交互:question、**questionnaire(多步结构化提问)**、handoff、send-user-message;
- 结构化/流程:structured-output、todo、plan-mode、custom-compaction、kimi-deferred-tools;
- 系统集成:**event-bus(扩展间事件总线)**、**file-trigger(文件变化触发注入)**、reload-runtime、
  rpc-demo、shutdown-command、ssh、interactive-shell;
- 沙箱:sandbox、gondolin(micro-VM 工具执行);另有 doom-overlay/snake/space-invaders 等玩具
  ——证明扩展 API 的表达力(pi-doom 也是官方 README 的安装示例)。

对我们信息量最大的三个(→ F7):

1. **timed-confirm**:确认对话框带倒计时,超时走默认分支。我们审批卡(§7.6)需要同款语义:
   **超时必须有显式默认,且默认=拒绝/暂停,绝不默认放行**,倒计时可见。
2. **questionnaire**:把「问用户」做成多步结构化表单而非自由聊天。对应我们 intent 澄清:
   参数缺失时发结构化问卷(候选集单选/数值域输入),不做开放式追问
   ——与「LLM 只做选择题」哲学同构,UI 形态可直接抄。
3. **file-trigger**:监听文件系统事件注入消息。对应我们「产物落盘 → 触发质量门/失效传播」,
   我们的触发源是作业目录契约文件,更可控,方向一致。

### 3.5 吸收决议(absorb-F 系列,编号接续 §5.2 的 E 系列)

| # | 吸收点 | 来源证据 | 落点 | 优先级 |
|---|---|---|---|---|
| **F1** | 副作用 API 强制幂等键 + 服务端短期去重(重试安全,与 E4 的投递模式声明正交) | OpenClaw 网关协议([architecture](https://docs.openclaw.ai/concepts/architecture)) | `api/app.py`(POST /runs、/message、审批答复) | P1 |
| **F2** | 事件流不回放:事件带单调 seq,客户端见缺口 → GET 全量快照重同步;服务端不留回放缓冲 | 同上「Events are not replayed; clients must refresh on gaps」 | 双 SSE + prototype 重连逻辑 | P1 |
| **F3** | 流式事件只发 delta,终态事件为权威全量(防二次方膨胀) | pi 0.84.0 破坏性变更(`coding-agent/CHANGELOG.md:66`,#7290) | `loop/events.py` + SSE 序列化 | P1 |
| **F4** | 作业进度快照文件:wrapper 周期覆写 status.json(pid/阶段/进度),监控与 reattach 读快照,不解析日志推断 | pi-chat worker-status 15s 快照(`pi-chat/README.md:81`) | `runtime/wrapper` + executor reattach | P1-P2 |
| **F5** | 系统 tick 与用户任务分层:判活轮询/水位检查等系统事件不进 run 历史与 provenance;用户可见的调度任务必有任务记录 | OpenClaw heartbeat/automations 对照([automation](https://docs.openclaw.ai/automation)) | `loop/timer` + `core/store`(ops 日志与 provenance 分表) | P2 |
| **F6** | 结构化输出用约束采样兜底:select/intent 的 JSON 输出启用 schema-constrained sampling,值域错误在采样层杜绝 | pi 实验特性(`coding-agent/CHANGELOG.md:8`) | `brain/select.py`(vLLM guided_json / GBNF) | **P1(与 E9 配对)** |
| **F7** | 审批卡超时显式默认(默认拒绝/暂停 + 可见倒计时);intent 澄清用结构化问卷而非自由追问 | pi 官方扩展 timed-confirm / questionnaire(`examples/extensions/`) | prototype 审批卡 + intent 澄清流程 | P2 |

**明确不抄(§3 范围):**

| 不抄 | 原因 |
|---|---|
| Gondolin per-channel micro-VM | WSL 已是我们的隔离边界;单用户科研桌面无多租户需求 |
| 多渠道网关 + heartbeat 常驻 agent | 单用户单机工具;未来若做「巡检告警」,回头抄 automations 的任务记录模型即可 |
| agent 运行时自造 skills(pi-chat) | 能力集必须锁定在 registry(可复现性红线);场景包由人审后入库(E7) |
| OpenClaw 的「信任外置」安全模型 | 其暴露面事故正是反面教材;审批/质量门内置路线不变 |

---

## 4. Python agent 框架横向对比(选型再验证,2025-2026)

> 问题:我们的自研执行层(SQLite 真相源 + 五阶段幂等执行器 + 事件总线)在 Python 生态是否已有现成替代?
> 方法:对七个主流框架逐一回答五问——持久化/断点恢复模型、工具重放语义、人机审批点、流式事件模型、
> 与我们自研件的对应物。证据:官方文档与发布记录(链接随文)+ 四个本地克隆
> (smolagents / claude-agent-sdk-python / deepagents / mini-swe-agent)。

### 4.0 2025-2026 战场地图

| 框架 | 2026-08 状态 | 关键事件 |
|---|---|---|
| pydantic-ai | **V2.0(2026-06-23)**,harness-first | V1 2025-09;V2 引入 capabilities 原语([release](https://github.com/pydantic/pydantic-ai/releases/tag/v2.0.0)) |
| LangGraph | 1.x 稳定(1.1.10) | 1.0 于 2025-10-22 与 LangChain v1 同发;`create_agent` 建于其上;deepagents 是官方 harness 样板 |
| OpenAI Agents SDK | 活跃;HITL/RunState 已内建 | 2025-03 发布(Swarm 后继);[Temporal 集成 GA 2026-03-23](https://temporal.io/blog/announcing-openai-agents-sdk-integration) |
| Claude Agent SDK | 活跃 | 2025 年秋由 Claude Code SDK 更名;内核=Claude Code CLI 子进程,SDK 是封装 |
| smolagents | 1.2x(1.24.0,2026-01-16),约 27k★ | 定位未变:代码即动作的轻量原型([对比文 2026-05](https://futureagi.com/blog/oss-agent-frameworks-2026/)) |
| CrewAI | 1.x(1.14.4,2026-04-30),约 51k★ | 1.8.0 起 `@human_feedback`;商业层 AMP;已宣称独立于 LangChain |
| AutoGen | **维护模式**(2025-09-30 后冻结) | 双后继:官方 → **Microsoft Agent Framework 1.0 GA 2026-04**([InfoQ](https://www.infoq.com/news/2026/08/agent-framework-harness-ga/));社区 → AG2(0.12.x,Apache 2.0) |

三条横向观察:

1. **「harness」成了 2026 年的行业词**:pi 自称 agent harness;pydantic-ai V2 官方措辞
   「leans into a harness-first design」;MAF 在 Build 2026 发布「Agent Harness」运行时;
   deepagents 自述「opinionated harness」。各家都在把「循环+工具+上下文+会话」骨架与业务分离
   ——我们 Loop/Core 分层与此一致;论文写相关工作时可用这个词锚定。
2. **审批点全行业收敛到同一形状**:「工具声明需审批 → run 暂停/结束并携带待批清单 →
   决策以结构化对象送回 → 恢复」。七家中五家(pydantic-ai/OpenAI/Claude/CrewAI/MAF)是这个形状,
   只剩参数命名不同。我们 §7.6 审批卡方向被全面印证,具体形状抄 pydantic-ai(G1)。
3. **崩溃级持久化仍是分水岭**:框架自带的多是「对话/状态快照」;真正崩溃恢复要么外包
   Temporal/DBOS(pydantic-ai、OpenAI),要么靠 checkpointer/超步检查点(LangGraph、MAF)。
   **没有一家把「外部长进程」当一等恢复对象**——这正是我们五阶段执行器的生态位(见 4.9 节)。

### 4.1 pydantic-ai(V2,harness-first)

- **持久化/恢复**:核心库无 checkpointer;消息史可序列化(`ModelMessagesTypeAdapter`)。崩溃级
  durability 外包给四家官方集成:Temporal(模型/工具调用下放为 Activity,编排确定性重放)、
  DBOS(步骤 checkpoint 进 Postgres,进程内)、Prefect、Restate
  ([durable execution overview](https://pydantic.dev/docs/ai/capabilities/durable_execution/overview/))。
- **工具重放**:本体无声明;Temporal 模式下工具=Activity,结果入事件历史、重放不重执行
  (≈我们「结算落盘后不重跑」,但要求编排代码确定性)。
- **审批点**([deferred tools](https://pydantic.dev/docs/ai/tools-toolsets/deferred-tools/)):
  `requires_approval=True` 或工具内 raise `ApprovalRequired`;run 以 `DeferredToolRequests`
  (待批 tool_call 清单)**正常结束**;调用方收集决策后携带 `DeferredToolResults`
  (tool_call_id → bool / `ToolApproved(override_args)` / `ToolDenied(message)`)+ 原消息史发起
  **新 run(新 run_id,用 conversation_id 关联,明文规定不得复用暂停 run 的 id)**。
  V2 另有进程内路径(`HandleDeferredToolCalls` capability 内联清算)。
- **外部工具**:`CallDeferred` 异常=「结果不在本 run 内产生」;工具先调度后台任务、带走
  `tool_call_id`,结果由外部系统日后送回——**七家中唯一把「慢任务交给外部执行器」建模为一等概念**,
  但只定义了「结果送回」,没有判活/reattach/产物校验;后半段正是我们五阶段的内容。
- **流式**:`run_stream` / `event_stream_handler`(V2 移入 `ProcessEventStream` capability);
  `AgentStreamEvent` 闭集,[版本政策](https://pydantic.dev/docs/ai/project/version-policy/)明言
  「新增事件类型属 minor,消费方必须防御性编码」。
- **对应物**:DeferredToolRequests/Results ≈ 审批卡出入口(→ G1);CallDeferred + tool_call_id ≈
  LAUNCHED 意图落盘的前半段;durable 集成 ≈ 我们 store+executor(引擎外置)。
  **术语撞车预警**:pydantic-ai 的 capability=「行为扩展包」(工具+指令+钩子+模型设置打包),
  我们 registry 的 capability=「可执行处理能力」;文档互引时必须显式区分。

### 4.2 LangGraph 1.x(+ deepagents)

- **持久化/恢复**:checkpointer(InMemory/SQLite/Postgres)以 `thread_id` 为主键,每个 super-step
  结束存全图状态 + **pending writes(超步内已成功节点的写先存,失败节点重跑时成功节点不重算)**
  ([checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers))。
  durability 三档:`exit` / `async` / `sync`(每步同步落盘)。支持时间旅行与 fork thread。
- **工具重放语义(关键坑)**:`interrupt()` 暂停后 `Command(resume=…)` 恢复,**节点从头重放,
  interrupt 之前的副作用会再执行一次**(官方文档明示,
  [interrupt reference](https://reference.langchain.com/python/langgraph/types/interrupt));
  多个 interrupt 按**索引顺序**配对 resume 值,节点内代码顺序一改即错位。functional API 略好:
  重放时 `@task` 结果从 checkpointer 恢复不重算
  ([functional-api](https://docs.langchain.com/oss/python/langgraph/functional-api))。
  **「节点=恢复边界」比我们「阶段=恢复边界」粗一档,节点内幂等要用户自己保证
  ——五阶段守卫解决的正是这个粒度问题。**
- **审批点**:interrupt 是通用暂停原语,HITL 建于其上(payload 经
  `stream.interrupts` 暴露);LangChain v1 提供 HumanInTheLoopMiddleware 包装工具审批。
- **流式**:多模式(values/updates/messages/custom)+ `stream_events(version="v3")`。
- **deepagents(本地克隆)**:「Deep Agents=中间件/后端/档案的 opinionated harness;
  LangGraph=执行运行时:state、checkpoints、streaming、interrupts」
  (`deepagents/openwiki/architecture/overview.md:14-16`)。两个可抄细节:
  `DeepAgentState` 用 **delta 式 message reducer 防长线程 checkpoint 超线性增长**(:28);
  后端能力决定工具面——不能执行 shell 的后端直接**移除** execute 工具与相应提示词,
  而不是调用时报错(:38),「不给不可用的选项」与我们候选集哲学同构。
- **对应物**:checkpointer+thread_id ≈ store+run_id;durability=sync ≈ 我们逐阶段落盘;
  interrupt/Command ≈ 审批卡+干预队列;@task 结果恢复 ≈ COLLECTED 产物复用。
- **再验证结论**:LangGraph 是七家中唯一能整体替代我们 store+executor 外壳的,但我们的核心逻辑
  (WSL 进程 reattach、产物指纹跳过、参数级 stale 传播)全部生活在「节点内部」,LangGraph 帮不上;
  引入它 = 五阶段一行不少 + checkpoint 状态与 SQLite 真相源双写。
  **维持自研决议,理由从「它不够」升级为「它帮不到刀刃上」。**

### 4.3 OpenAI Agents SDK(Python)

- **持久化/恢复**:两层——Sessions(SQLiteSession/SQLAlchemy/OpenAI Conversations/Redis)存对话史;
  **RunState 存「暂停的 run」全量快照**(model responses、生成 items、审批状态、
  `_last_processed_response`、序列化 tool_input),`to_json()/from_json()` 显式进出
  ([run_state](https://openai.github.io/openai-agents-python/ref/run_state/))。
  RunState 只在 HITL 中断时产生;工具执行中进程崩溃无恢复语义,崩溃级 durability 的官方答案是
  Temporal 集成(GA 2026-03-23)。
- **工具重放**:无声明;Temporal 模式同 4.1 节。
- **审批点**([human-in-the-loop](https://openai.github.io/openai-agents-python/human_in_the_loop/)):
  工具声明 `needs_approval` → run 结果带 interruptions → `result.to_state()` →
  `state.approve(item)/reject(item)`(**支持 `always_approve/always_reject` 粘性决策,
  随 RunState 序列化存活**)→ `Runner.run(agent, state)` 恢复。
- **流式**:三层事件——`raw_response_event`(LLM 原始 delta)/ `run_item_stream_event`(语义级:
  message_output_created、tool_called、tool_output、handoff_*、mcp_approval_requested…)/
  `agent_updated_stream_event`([streaming](https://openai.github.io/openai-agents-python/streaming/));
  文档强调**流未消费完 = run 未结束,session 持久化副作用可能仍在结算**。
- **对应物**:RunState ≈ 我们 steps 行 total state(但粒度=整 run 快照,非每步一行);
  三层事件 ≈ 我们 data/ui 事件分离;粘性审批 → G2。

### 4.4 Claude Agent SDK(Python,本地克隆佐证)

- **持久化/恢复**:真相源在 Claude Code CLI 的会话转录(JSONL),SDK 只管 `resume=session_id` /
  `fork_session=True`(`claude-agent-sdk-python/src/claude_agent_sdk/types.py:1841-1862`);
  `enable_file_checkpointing` 可回滚**文件系统状态**(workspace 快照)。resume 恢复的是对话,
  不是执行位置;工具执行中崩溃无恢复语义。
- **工具重放**:无;PreToolUse 可返回 `permissionDecision:"defer"` 把工具调用「停车」
  ——结束本次 query,恢复时经 deferred-replay pass 重新决策
  ([hooks](https://code.claude.com/docs/en/agent-sdk/hooks))。
- **审批点(四层漏斗)**:PreToolUse 钩子(`allow/deny/ask/defer` + `updatedInput` 参数改写)→
  声明式 allow/deny 规则 → permission_mode → `can_use_tool` 回调兜底;SDK 内置
  「can_use_tool 被上游规则遮蔽」的告警(`types.py:1700-1781`)——**层级多到需要遮蔽检测,
  反面提示:我们的审批层级保持两层(质量门+审批卡)以内**。
- **流式**:CLI 子进程 stdout 消息流(可含 partial);Python 钩子六种 vs TS 十二种,
  SessionStart/End 在 Python 只能走 settings 文件 shell 钩子——**跨语言钩子面不齐,
  外包内核的典型代价**。
- **对应物**:can_use_tool ≈ 审批卡;PreToolUse/PostToolUse ≈ E8 的 before/after 钩子;
  fork_session ≈ E5;file checkpointing ≈ 产物指纹(我们校验而非回滚)。
  **真相源在别人进程里,与「SQLite 唯一真相源」红线相抵——维持「抄 API 设计、不抄依赖形态」。**

### 4.5 smolagents(本地克隆佐证)

- **持久化/恢复**:内存态 `AgentMemory`(`MemoryStep` 谱系:ActionStep/PlanningStep/TaskStep/
  FinalAnswerStep,`smolagents/src/smolagents/memory.py:42-214`);`agent.to_dict()/from_dict()`
  (`agents.py:970,1011`)序列化的是**配置**而非执行位置;跨 run 持久化至今是开放 issue
  ([#1216](https://github.com/huggingface/smolagents/issues/1216)),社区靠手动搬 `memory.steps`。
  有 `interrupt()`(:754,协作式停止)与 `replay(detailed=)`(:859,**离线回放展示,非执行恢复**)。
- **工具重放**:无;每次 run 从头。
- **审批点**:无内建;step_callbacks 可自行拦截。
- **流式**:`stream_outputs=True` + `run(stream=True)` 生成器逐步产出(`agents.py:352,436,658`)。
- **对应物**:MemoryStep 谱系 ≈ 我们 steps/trace 结构;其 `replay` ≈ 我们的轨迹回放 UI
  ——名字相同语义不同,见 4.10 节术语决议。定位是原型/教学,「要 durable state 与 workflow replay
  就别用它」是 2026 年对比文的共识标注,选型无变化。

### 4.6 CrewAI Flows

- **持久化/恢复**:`@persist`(类级=每方法后存,方法级=定点存)默认 SQLiteFlowPersistence
  ([flows](https://docs.crewai.com/en/concepts/flows));**方法成功后写快照,无意图落盘**
  ——方法执行中崩溃=该方法白跑,重入语义未定义(效果三明治只有下半片)。恢复/分叉双参数:
  `kickoff(inputs={"id": uuid})`=同世系续跑(历史延长);`kickoff(restore_from_state_id=uuid)`=
  快照播种新世系(新 state.id);与另一套 Checkpointing 系统互斥,混用抛 ValueError
  ([mastering-flow-state](https://docs.crewai.com/en/guides/flows/mastering-flow-state))。
- **审批点**:`@human_feedback`(1.8.0+):暂停收集人工反馈,**自由文本反馈由 LLM 折叠成 emit
  枚举 outcome 再路由给 @listen**;异步 provider 下 kickoff 返回 `HumanFeedbackPending`,
  状态自动持久化,`resume()` 继续([human-feedback](https://docs.crewai.com/en/learn/human-feedback-in-flows))。
- **工具重放**:未定义。
- **流式**:`Flow.stream=True` → kickoff 返回可迭代的 StreamFrame。
- **对应物**:@persist ≈ store.advance(但无幂等守卫、无意图提交);resume/fork 双参数 → G5
  (E5 的 API 细化);「LLM 折叠自由反馈到枚举」有想象力但**拒绝**:审批答复必须是确定性结构
  (勾选/数值),不能再过一层 LLM。

### 4.7 AutoGen 系:AG2 与 Microsoft Agent Framework

**格局**:[microsoft/autogen](https://github.com/microsoft/autogen) 于 2025-09-30
(autogen-agentchat 0.7.5)后冻结进维护模式,官方指路
[Microsoft Agent Framework](https://learn.microsoft.com/en-us/agent-framework/overview/)
(AutoGen + Semantic Kernel 两团队合并;preview 2025-10-01 → 1.0 GA 2026-04;Build 2026 又发
Agent Harness 运行时与编排模式 GA)。社区线 AG2(ag2ai/ag2,Apache 2.0,0.12.3,2026-05)延续
0.2 风格 API:`human_input_mode=ALWAYS|TERMINATE|NEVER` 的控制台式审批、group chat 恢复=把导出的
消息列表喂回 manager——2023 年的形状,只对存量用户有意义。

**MAF 的 workflows 检查点(值得细看)**([checkpoints](https://learn.microsoft.com/en-us/agent-framework/workflows/checkpoints)):

- 执行按 **superstep** 推进,每个 superstep 结束自动存检查点:全部 executor 状态 + 下一超步
  pending messages + **pending requests/responses** + shared state;
- executor 自定义状态必须实现 `on_checkpoint_save()/on_checkpoint_restore()` 对——**把「哪些状态
  入检查点」的责任显式压给节点作者**,与我们 E2 的 total state 纪律同源(→ G6);
- 恢复:`workflow.run(checkpoint_id=…)` **与新 message 互斥**;挂起的人工请求
  (`ctx.request_info()` + `@response_handler`)可在恢复时同批投喂答复(→ G4);
- 存储:File/Cosmos 用 pickle + **受限 unpickler**(类型白名单 `allowed_checkpoint_types`,
  越界抛 `WorkflowCheckpointException`)——为安全给 pickle 打的补丁之复杂,反证 JSON-only 决议(→ G8);
- 坦诚的边界声明:**server-side session(FoundryAgent)状态不入检查点**,「要可靠检查点请自写
  executor」——外部世界状态进不了检查点、只能记指针,与我们「SQLite 记录处理真相,
  外部世界靠判活+校验」是同构问题的同款答案。

**对应物**:superstep 检查点 ≈ 阶段推进;pending requests 入检查点 ≈ pending_actions 表;
checkpoint_id 与新消息互斥 ≈ G4。

### 4.8 对照组:mini-swe-agent(本地克隆)

100 行哲学的下限样本(`mini-swe-agent/src/minisweagent/agents/default.py`):主循环 `run()` 38 行
(:88-124);**每步 `finally: self.save()` 无条件把全量轨迹(config+messages+stats)写盘**
(:120-121, :159-190)——total-state 落盘的最朴素实现,崩溃后**人**可以拿 messages 续,
程序不能续;step/cost/wall-time 三限额在 query 入口检查(:130-147)。
**信息量:极简路线的「持久化」就是全量快照,够 SWE-bench 跑分,不够科学工作流
——我们与它的差全部落在恢复语义上,这正是论文对比叙事的素材。**

### 4.9 五问对照总表(与我们自研件互查)

| 框架 | 持久化单位 | 断点恢复 | 工具重放语义 | 审批点 | 流式事件 |
|---|---|---|---|---|---|
| **我们(自研)** | SQLite:runs/steps 行(total state) | 五阶段守卫,按 step_id 点查,进程 reattach | capability `replay: never\|safe`(E1)+ job_state 判活 | 审批卡(before 钩子,fail-closed) | 事件总线 data/ui 分离 + 双 SSE |
| pydantic-ai V2 | 消息史(可序列化) | 外包 Temporal/DBOS/Prefect/Restate | Activity 重放(Temporal 模式) | DeferredToolRequests/Results ★ | AgentStreamEvent 闭集 |
| LangGraph 1.x | checkpointer 超步快照 + pending writes | thread_id + Command(resume);**节点内副作用重放** | @task 结果恢复;节点级无守卫 | interrupt() 原语 | 多模式 + stream_events v3 |
| OpenAI Agents SDK | Sessions + RunState 快照 | HITL 中断点恢复;崩溃靠 Temporal | 无(Temporal 模式=Activity) | needs_approval + 粘性 approve/reject ★ | 三层事件 ★ |
| Claude Agent SDK | CLI 会话转录(外部进程持有) | resume/fork 会话;无执行位置恢复 | defer 停车 + deferred-replay pass | 四层漏斗 hooks→规则→mode→can_use_tool ★ | CLI stdout 消息流 |
| smolagents | 内存 AgentMemory | 无(开放 issue) | 无 | 无内建 | run(stream=True) 生成器 |
| CrewAI Flows | SQLite 状态快照(方法后) | resume/fork 双参数 ★;无意图落盘 | 未定义 | @human_feedback(LLM 折叠反馈) | StreamFrame |
| MAF 1.0 | superstep 检查点(含 pending requests)★ | checkpoint_id(与新消息互斥)★ | 无声明;超步内已完成 executor 不重跑 | request_info + response_handler | workflow 事件流 |

★ = 有值得抄的具体形状(进 G 系列)。

读表结论:

1. **持久化单位没有一家是「外部长进程作业」**。最接近的两个:pydantic-ai `CallDeferred`
   (只管结果送回,不管判活/产物)、MAF 的「server-side 状态不入检查点」免责声明(承认问题,
   不解决)。我们 LAUNCHED/RUNNING 的 reattach(pid 判活、log_offset 续读、产物完整性判定)
   在七家中零对应——**自研必要性再确认**。
2. **工具重放语义只有 pi(E1 来源)与 LangGraph(部分)显式处理**,其余未定义或整段外包 Temporal。
   replay 声明 + 五阶段守卫作为内建能力,是我们相对生态的真实差异点。
3. **审批点形状全行业收敛**(4.0 节观察 2),抄形状即可,不必抄框架。
4. **流式事件的「原始/语义/控制」三层结构是事实标准**(OpenAI 三层、LangGraph 多模式、
   pi RPC 三分),我们 data/ui 分离已对齐,补上 F2/F3 的断线与增量纪律即可。

### 4.10 吸收决议(absorb-G 系列,接续 F 系列)

| # | 吸收点 | 来源证据 | 落点 | 优先级 |
|---|---|---|---|---|
| **G1** | 审批=「run 正常出口 + 批复作下次输入」:待批清单(tool_call_id→上下文)是 run 的一种正常结束态;批复对象 {id→approve(override_args)/deny(message)};续跑是**新 run**,以 run 链关联,绝不复用挂起线程 | pydantic-ai deferred tools([文档](https://pydantic.dev/docs/ai/tools-toolsets/deferred-tools/)) | `core/actions` + `api`(POST /runs/{id}/approvals)+ §7.6 审批卡数据形状 | **P1** |
| **G2** | 粘性审批:`always_approve/always_reject` 决策落库、随恢复存活;审批卡加「本 run 内同类不再询问」 | OpenAI Agents SDK RunState([HITL](https://openai.github.io/openai-agents-python/human_in_the_loop/)) | approvals 表 + prototype | P2 |
| **G3** | 审批钩子返回值闭集 `allow\|deny\|ask\|defer` + 可审计的 `updatedInput` 参数改写;defer=挂起进队列而非阻塞进程 | Claude Agent SDK PreToolUse([hooks](https://code.claude.com/docs/en/agent-sdk/hooks)) | 审批钩子契约(与 E8 配套) | P1 |
| **G4** | 恢复入口互斥:resume(run_id) 与新指令互斥;恢复第一步清算 pending 审批/干预,再进执行 | MAF `run(checkpoint_id=…)` 语义([checkpoints](https://learn.microsoft.com/en-us/agent-framework/workflows/checkpoints)) | `api` + executor 恢复入口 | P2 |
| **G5** | E5 run fork 的 API 细化:resume 与 fork 是两个互斥参数(同世系续跑 vs 快照播种新世系),混用即错 | CrewAI `inputs.id` vs `restore_from_state_id`([文档](https://docs.crewai.com/en/guides/flows/mastering-flow-state)) | `api`(并入 E5 实现) | P2 |
| **G6** | 自定义状态入检查点走显式契约(on_save/on_restore 对),不隐式序列化对象图 | MAF executor 检查点 API | `core/store`(steps 行字段白名单,拒绝 blob 塞对象) | P2 |
| **G7**(拒绝) | 不引外部 durable execution 引擎(Temporal/DBOS/Prefect/Restate):单机科研工具引编排服务不成比例;Activity 重放收益已由五阶段意图/结算覆盖;Temporal 要求编排代码确定性,与「外部 CLI reattach」模型错位 | pydantic-ai/OpenAI 官方路线的适用前提 | ——(记录为选型依据) | — |
| **G8**(拒绝) | 不用 pickle 序列化任何状态(即使带受限 unpickler);SQLite 行内字段 + JSON 白名单到底 | MAF restricted unpickler 的复杂度即反证 | `core/schema.sql` 纪律 | — |

术语小决议(随 G 系列记录):**resume/reattach 专指执行恢复,replay 只指 UI 轨迹回放**
——smolagents 的 `replay()` 是回放展示、pi 的 `replay` 是重放安全声明、Temporal 的 replay 是
确定性重放,三个 replay 三个意思,我们文档必须锁死用法。

### 4.11 结论:自研路线是否仍成立

**成立,且比 2026-08-10 定稿时更有把握。**

1. **竞争面**:2025-10 至 2026-06 的三波大版本(LangGraph 1.0、MAF 1.0、pydantic-ai V2)把力气都
   花在「对话级持久化 + 审批点 + 流式」,没有人下探「外部科学计算作业的崩溃恢复」;
   我们的差异化(五阶段 reattach + 参数级 stale + 候选集约束)反而更清晰。
2. **可抄面**:审批点(G1-G3)与恢复入口(G4-G5)已有行业收敛形状,照抄省设计;
   E 系列(pi)管执行内核,F 系列(生态运维面)管协议纪律,G 系列(Python 生态)管 API 面,三组正交。
3. **风险面**:若未来必须并入某框架,唯一候选是 LangGraph(checkpointer 换 store 外壳),
   迁移成本=把五阶段包进节点 + 双真相源对账;已评估,不值得——除非出现「多人协作/云端托管」需求
   (那是 LangGraph Platform 的主场,与论文场景无关)。

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
