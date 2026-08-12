# 开源 Agent 设计学习报告：产品层 · harness 层 · 工作流定向调研

> 配套 `COMPARISON_LEARNING.md`（科学工作流对标，absorb-A~D）与 `RS_AGENTS_ANALYSIS.md`（遥感 Agent 生态）。
> 本篇覆盖 11 个新精读仓库，聚焦 `AGENT-DESIGN.md` 未对标过的三层：
> **世界级 Agent 产品层**（codex / gemini-cli / OpenHands / cline）、
> **轻量 harness 层**（pi / mini-swe-agent / deepagents / claude-agent-sdk-python）、
> **定向工具**（burr / snakemake / dvc，关闭 DESIGN.md §16.1/§16.3 与 §6 的三个待验证决策）。
> 所有论断附 `文件:行号`，行号基于 `reference/repos/` 下 2026-08-12 浅克隆快照。
> 由四个并行子代理源码精读产出，汇总日期：2026-08-12

---

## 0. 一页结论

1. **三个待验证决策全部关闭**：
   - `DESIGN.md §16.3`（Burr）→ **不引入，自研 200 行事件循环**。pull 型循环无法合流三事件源；action 粒度 resume 与小时级子进程冲突（崩溃后整步重跑，`burr/core/application.py:2683-2686` 证实）；快照式持久化与结构化 steps 表分裂真相源。抄走 6 个技巧（§4.1）。
   - `DESIGN.md §6`（Snakemake 五触发器）→ 实现已见底：按输出文件存 MetadataRecord + 全局类别开关 + masked 剪枝 + 双向 BFS。移植 3 点、避开 2 坑（params 丢键名、代码触发无逃逸阀，§4.2）。
   - `DESIGN.md §16.1/§16.5`（对齐 dvc.lock）→ 逐字段对照完成：我们已覆盖其全部语义且多出状态机/run_ok/stale_reason/外部作业句柄四个维度；**需补三处**：记录格式版本字段、fp 三段编码、provenance 导出物化 cmd+deps（§4.3）。
2. **自研主循环路线被四家独立验证**：pi / mini-swe-agent / deepagents / claude-sdk 的核心循环全部收敛到「LLM 决策 → 执行 → 追加消息」的极小形状，没有任何一家在循环里放 DAG 或调度器；策略全部外置为钩子。`loop/driver.py` 保持一屏以内是可行且正确的目标（§3）。
3. **「服务重启后收养正在跑的进程」是全行业空白**：OpenHands 只恢复客户端↔服务端层（断线重连协议可直接抄）；cline hub 做到客户端与任务解耦（daemon 架构可参考）；但两家的子进程都随服务进程死。**§4.7 的 wrapper.sh + job.rc + log_offset 方案是本项目真正的差异化工程点，可作为论文与通用 Agent 框架的对比论据**（§2.5）。
4. **秒级 vs 小时级的适用性反转**是评估一切借鉴点的总原则：短超时/每步快照/盲目重试在 InSAR 场景直接失效；拒绝带理由回传、指纹化审批缓存、完成通知、恢复时重提决策点反而从「锦上添花」升级为刚需（§2.4）。
5. 产出吸收决议 **absorb-E ~ absorb-P** 共 12 条（§5），及对 `AGENT-DESIGN.md` 的 10 条修正建议（§7）。

---

## 1. 仓库清单

### 本篇新精读（2026-08-12 浅克隆）

| 仓库 | 定位 | 本篇取材 |
|---|---|---|
| `codex` | OpenAI Codex CLI（Rust），协议层为宪法的终端 Agent | 审批/沙箱枚举、rollout 持久化、头尾截断、中途压缩 |
| `gemini-cli` | Google（TS），显式状态机驱动的工具调度器 | 七态调度、审批粒度七档、policy engine、checkpoint 三元组 |
| `OpenHands` | 事件流架构 Agent 平台（主仓已重构为 TS 桌面端，Python 核心在未检出的 agent-sdk，取证自其协议镜像） | 封闭事件联合体、断线重连协议、预算双闸门 |
| `cline` | 原 VS Code 扩展，现 sdk monorepo（agents/core/shared 分层） | 熔断三段式、hub 守护进程、插话双队列、压缩边车 |
| `pi` | earendil-works Agent harness（TS），分层最干净 | 五钩子面、steering/followUp 队列、扩展事件词表 |
| `deepagents` | LangChain 的 harness 发行版 | todo 纯工具化、subagent 上下文隔离、标准化错误字面量 |
| `mini-swe-agent` | SWE-agent 团队极简 agent（核心循环 ~40 行） | 异常即控制流、单方法环境协议、每步落盘 |
| `claude-agent-sdk-python` | Anthropic 官方 SDK（循环在捆绑 CLI 里，SDK 是控制协议客户端） | can_use_tool 回调形状、hooks 命名、query/client 双模式 |
| `burr` | Apache 孵化状态机框架（DESIGN.md §16.3 点名对比） | 决策：不引入；抄 6 技巧 |
| `snakemake` | 五触发器失效判定的概念来源（DESIGN.md §6） | 触发器真实实现、传播机制、两个坑 |
| `dvc` | 参数级失效判定 prior art（DESIGN.md §16.1） | dvc.lock 对照、键级 params diff、run-cache |

### 已在库未深读（备注）

`openclaw`（个人 AI 助理网关，桥接聊天软件）与 `pi-chat`（pi 的 Discord/Telegram 桥）与本项目架构相关性低，仅备查。`smolagents` 已在 `DESIGN.md §9.1` 否决（事实停更）。

---

## 2. 世界级产品层：codex / gemini-cli / OpenHands / cline

### 2.1 Codex CLI 核心借鉴（12 条）

行号相对 `codex/codex-rs/`。

| # | 设计点 | 证据 | 映射 | 采纳 |
|---|---|---|---|---|
| C1 | 审批策略四档枚举：`UnlessTrusted / OnRequest / Granular（按类别细粒度）/ Never`，策略与机制分离 | `protocol/src/protocol.rs:917-958` | UI 审批卡 + loop 审批门 | 改造：InSAR 按「只读查询/提交作业/覆写产物/删除」四类映射 |
| C2 | 沙箱策略独立枚举：`ReadOnly / WorkspaceWrite{writable_roots} / DangerFullAccess`，写权限精确到目录列表 | `protocol/src/protocol.rs:1004-1052` | Runtime 作业目录契约 | 抄思路：WSL 执行器可写根 = 当前作业目录 |
| C3 | 可写根内部再挖只读洞（保护 `.git`、hooks 防提权） | `protocol/src/protocol.rs:1054-1105` | §7.6 | 抄思路：作业目录内 manifest/指纹文件/SQLite 对执行器只读 |
| C4 | `ReviewDecision` 八值：`Denied{rejection:String}` 带理由、`ApprovedForSession`、`Abort`、`TimedOut` 分立 | `protocol/src/protocol.rs:3849-3884` | 审批卡返回值协议 | **直接抄**（见 absorb-E） |
| C5 | 拒绝的落地：理由字符串作为工具结果回给模型——模型收到**可行动的理由**而非布尔值 | `core/src/tools/approvals.rs:332-334` | brain/select 上下文 | **直接抄**：拒绝理由进下一道选择题 |
| C6 | 审批注入点唯一化 + 决策来源（Hook/自动/人工）随决策记录 | `core/src/tools/approvals.rs:341-376,562-575` | loop/driver + Audit | 直接抄 |
| C7 | 审批缓存按**规范化命令+cwd+权限**作 key，同参数一次批准全会话生效 | `core/src/tools/approvals.rs:133-148,474` | core/store | 改造：**审批缓存 key = 参数指纹**，同指纹族批一次 |
| C8 | 超时错误**携带完整已捕获输出**（模型能看到死前日志）+ 2s 管道排水防孙进程挂死 | `core/src/exec.rs:792-813,82-89` | Runtime 五阶段 | 数值不适用，两个语义照搬 |
| C9 | 输出截断三层：1MiB 硬上限；stdout/stderr 1/3:2/3 配额；HeadTailBuffer 头尾各半+`omitted N bytes` 标记 | `utils/pty/src/lib.rs:12`、`core/src/exec.rs:879-887`、`core/src/unified_exec/head_tail_buffer.rs:31-42,114-132` | runtime/stream §4.4 | 直接抄：头（启动参数）+尾（错误现场）正是 InSAR 要保的两端 |
| C10 | 会话持久化 = JSONL 追加写 `{timestamp, ordinal, item}`；恢复语义三分 `New/Resumed/Forked` | `history/src/lib.rs:91-100,195-216`、`rollout/src/recorder.rs:76-120` | core/store 之外的轨迹层 | 改造：SQLite 为真相源，JSONL 只作对话轨迹，ordinal 对齐 |
| C11 | **落盘白名单是显式穷举函数**：每个事件类型被迫表态存/不存，不留默认分支 | `rollout/src/policy.rs:9-21,87-184` | Audit 证据阶梯 | 直接抄：「什么算证据」写成穷举函数 |
| C12 | 中途压缩：每次采样后查 `token_limit_reached && needs_follow_up`，turn 内即触发 | `core/src/session/turn.rs:441-465` | loop/budget | 改造：摘要对象应是状态卡片重建而非自由摘要 |

低成本高价值附加项：**turn 结束通知外部程序**（`notify = ["notify-send", ...]`，JSON 参数拉起任意命令，`core/src/config/mod.rs:732-746`）——InSAR 单步小时级，作业完成/失败/等审批时拉起通知是刚需，实现只是 spawn 一个进程（absorb-P）。

**不照搬**：多平台 OS 级沙箱（Brain 只做选择题已从源头消掉任意命令威胁）；Guardian LLM 审 LLM（小时级代价下审批权必须在人）；PTY 交互式进程会话（批处理管线用文件契约）；远端压缩（依赖 OpenAI 私有服务）；execpolicy 规则语言（候选集封闭，SQLite policy 表 + Kind 分类就够）。

### 2.2 Gemini CLI 核心借鉴（11 条）

行号相对 `gemini-cli/packages/`。

| # | 设计点 | 证据 | 映射 | 采纳 |
|---|---|---|---|---|
| G1 | 工具调用七态状态机，**每个状态是独立 TS 类型**（非法状态不可表示） | `core/src/scheduler/types.ts:26-34,81-196` | loop 任务状态机 | 直接抄思路：每态一型，指纹只在特定态可变 |
| G2 | 审批粒度七档：本次/总是/总是并持久化/整个 server/该工具总是/改后再跑/取消 | `core/src/tools/tools.ts:1094-1102` | UI 审批卡按钮组 | 改造：取四档，「总是」作用域按参数指纹族 |
| G3 | 确认卡内容按工具类型分型（edit 卡带 diff、exec 卡带命令、mcp 卡带 server+args） | `core/src/tools/tools.ts:977-1089` | UI 审批卡 | 直接抄：分型为参数卡/作业卡/覆写卡 |
| G4 | 工具风险分类枚举 `Kind`：Read/Edit/Delete/Move/Execute…，policy 按注解匹配 | `core/src/tools/tools.ts:1104-1118` | Brain 候选集元数据 | 直接抄 |
| G5 | 三值 policy engine：`ALLOW/DENY/ASK_USER`；**默认 ASK_USER**；非交互环境 ASK_USER 自动降级为 DENY（fail-closed） | `core/src/policy/types.ts:10-14,114-193,296-306` | §7.6 | 直接抄：小时级代价场景的正确保守设定 |
| G6 | 审批模式全序 `PLAN < DEFAULT < AUTO_EDIT < YOLO`，规则自动适用于更宽松模式 | `core/src/policy/types.ts:48-65` | 会话模式 | 改造：规划/正常/批量放行三档 |
| G7 | 「总是允许」即时转为 policy 规则，`AndSave` 按信任状态持久化 | `core/src/scheduler/policy.ts:207-241` | core/store | 改造：规则写 SQLite policy 表随项目走 |
| G8 | shadow git：独立 GIT_DIR + 项目根 WORK_TREE + 专用 gitconfig，快照=commit | `core/src/services/gitService.ts:131-218` | §7.6 | 改造：只快照参数文件/清单/小产物，GB 级栅格靠指纹重算 |
| G9 | **checkpoint 三元组**：{对话历史, 快照 hash, 该工具调用}；`/restore` 回滚后**重新提出该调用供再审批** | `core/src/utils/checkpointUtils.ts:98-104`、`core/src/commands/restore.ts:11-58` | 断点续跑 + 人机共控 | 直接抄思路：恢复=回到分岔口重新选（absorb-F） |
| G10 | 压缩五道工序：0.5×上限触发；保留最近 30% 且切分点落在用户消息边界；**旧工具大输出截断存盘留文件指针**；摘要继承旧快照；压缩后变大即放弃 | `core/src/context/chatCompressionService.ts:37-41,60-100,124-237,353-359,462-471` | loop/budget | 前三条照搬；文件指针恰好对应作业目录契约 |
| G11 | 分层记忆 GEMINI.md：cwd 向上遍历至 git root，全局/扩展/项目三类拼接 | `core/src/utils/memoryDiscovery.ts:405-509` | Brain 提示组装 | 仅参考：两层足够（用户偏好 + 项目 INSAR.md） |

细节对比：gemini 用户取消只回传固定文案 `"Operation cancelled by user"`（`core/src/scheduler/scheduler.ts:269-286`），codex 的 `Denied{rejection}` 可自定义理由——**取 codex 方案**，取消时让用户附一句「为什么」。

**不照搬**：shadow git 全工作区快照（GB 级产物不可行）；压缩探针自校验（成本翻倍，我们主上下文是结构化状态）；ModifyWithEditor 自由改参（必须走参数卡→新指纹→重新入队的正路）；JIT 子目录记忆（单项目不需要）。

### 2.3 OpenHands 核心借鉴（12 条）

材料说明：主仓已重构为 TS 桌面端，Python 核心（AgentController/StuckDetector/Condenser）在未检出的 software-agent-sdk 仓库；以下取证自其手工镜像的 agent-server 完整事件协议、WS 恢复层与 Electron 进程管理。行号相对 `OpenHands/`。

| # | 设计点 | 证据 | 映射 | 采纳 |
|---|---|---|---|---|
| O1 | 封闭事件联合体 + 统一信封 `id(ULID)/timestamp/source`，`source ∈ {agent,user,environment,hook}` | `src/types/agent-server/core/openhands-event.ts:25-46` | loop/events.py | 直接抄：封闭集 + kind 判别；source 加 `engine` |
| O2 | Action↔Observation 双向链接；**用户拒绝也是 Observation**（带 rejection_reason） | `core/events/observation-event.ts:27-52` | events + 干预队列 | 直接抄：干预以观察事件回填轨迹流，审计免费获得 |
| O3 | 每个 ActionEvent 落库带 `security_risk` 字段 | `core/events/action-event.ts:58-61` | §7.6 | 字段照抄，取值来源改为 capability 静态声明（不用 LLM 预测） |
| O4 | `stuck` 是一等状态（七态之一）；状态变更本身作为事件广播 | `core/base/common.ts:67-75`、`core/events/conversation-state-event.ts:99-151` | §7.8 四态 | 直接抄：熔断进独立 stuck 态而非笼统 failed |
| O5 | 预算双闸门（max_iterations + max_budget_per_task），成本随 stats 事件流出常态可见 | `src/types/settings.ts:130,143` | loop/budget + §7.5 | 「预算余量作为状态事件推流」直接采纳 |
| O6 | bash 软超时协议：`exit_code=-1` 表示超时未完成，空命令续读、`C-c` 中断、喂 stdin、reset | `core/base/action.ts:27-44`、`core/base/observation.ts:63-77` | runtime/stream | 「部分完成」观察语义采纳；主循环不阻塞 |
| O7 | 命令元数据结构化落进每条观察（exit_code/pid/username/cwd/解释器路径） | `core/base/common.ts:16-49` | wrapper.sh 契约 | 补齐 pid/cwd/解释器路径 |
| O8 | **断线恢复协议**：REST 分页拉历史 → WS `resend_mode='since'+锚点`增量订阅 → 按 id 去重 → **重放事件跳过非幂等副作用** | `src/contexts/conversation-websocket-context.tsx:253-257,904-913,518-523`、`src/api/event-service/event-service.api.ts:21-24,97-131` | §7.8 待补项 | **几乎照搬**（absorb-G）：用事件自增 id 替代时间戳锚点更稳 |
| O9 | 掉线不丢消息：WS 不通走 REST 把用户消息排进服务端队列（queued:true） | `conversation-websocket-context.tsx:1032-1075` | pending_actions | 补 `USER_MESSAGE` 动作类型 |
| O10 | 压缩可审计：`Condensation` 事件记录 `forgotten_event_ids + summary`；condenser 花费单独记账 | `core/events/condensation-event.ts:5-37` | budget + Audit | 直接抄：裁剪动作本身写成事件 |
| O11 | 流式 delta 是独立事件类型，不污染持久事件流 | `core/events/streaming-delta-event.ts:3-8` | api/UI | 采纳：日志尾巴流与 step 事件分通道 |
| O12 | Windows 无 POSIX 信号的变通（`process.emit("SIGTERM")` 进程内跑 handler）+ 孤儿进程占端口的坑位注释 | `electron/main.mjs:700-746` | §4.8 WSL 生命周期 | 现成坑位清单 |

**不照搬**：event sourcing 作为唯一真相源（重放又慢又脆，STALE 传播需要关系查询）；LLM 预测 security_risk（与选择题定位冲突）；`resend_mode='all'` 全量重放路径；LLM 摘要式 condenser（伤复现性）；桌面退出即杀后端（与我们核心需求相反）。

### 2.4 cline 核心借鉴（12 条）

材料说明：cline 已整体重写为 sdk monorepo（`sdk/packages/{agents,core,shared}`），经典 shadow git/ContextManager 已不存在。行号相对 `cline/`。

| # | 设计点 | 证据 | 映射 | 采纳 |
|---|---|---|---|---|
| L1 | **重复调用熔断三段式**：签名=工具名+排序键 JSON；连续 3 次→注入软警告，5 次→硬熔断 | `sdk/packages/core/src/runtime/safety/loop-detection.ts:66-87,113-116,144-156` | loop/driver 熔断 | 直接抄 ok/软/硬三段（absorb-O） |
| L2 | 连续错误计数器 + 可插拔决策回调；停机文案「**Session state was preserved. Send a new prompt to resume**」 | `runtime/safety/mistake-tracker.ts:88-150,188-190` | §4.12 + §7.8 文案 | 计数器限 Brain 决策层；文案哲学照抄 |
| L3 | 审批粒度 `ToolPolicy{enabled, autoApprove}` + 通配符；**无审批回调时默认拒绝（fail-closed）** | `shared/src/llms/tools.ts:7-18,46-80`、`agents/src/agent-runtime.ts:1598-1637` | §7.6 | capability 声明三元组；fail-closed 必须保留 |
| L4 | plan 模式双保险：提示词契约 + `beforeTool` 硬拦截；**拦截先于审批**（用户永不会被请求审批注定失败的命令） | `shared/src/prompt/cline.ts:25-45`、`core/src/extensions/tools/command-guard-extension.ts:12-15,70-73` | 专家/向导双模式 | 直接抄：向导模式=提示词+白名单硬闸双层 |
| L5 | 模式切换权：VS Code 路线关掉模型自主切换工具、让模型引导用户手动切换 | `shared/src/prompt/cline.ts:47-59,123-129` | 双模式切换 | 采纳：切换权在用户 |
| L6 | **插话双投递语义**：`queue`（排队等本轮结束）vs `steer`（下一次 LLM 调用前插队）；排队项可编辑/删除 | `core/src/runtime/turn-queue/pending-prompt-service.ts:14,168-280`、`agents/src/agent-runtime.ts:983-994` | core/actions §4.5 | **直接抄**（absorb-J）：动作分两档投递 + 可编辑撤回 |
| L7 | checkpoint 事务纪律：破坏性恢复前先自快照、失败回滚、未捕获 untracked 的旧快照拒绝 clean | `core/src/hooks/checkpoint-hooks.ts:117-155,382-466`、`core/src/session/checkpoint-restore.ts:50-154,396-406` | §7.6 | 机制不搬（GB 级产物），**事务纪律搬** |
| L8 | **压缩边车 + 前缀哈希投影**：canonical 转录 append-only；派生态存边车文件带源前缀哈希，失配即放弃重算 | `core/src/session/models/session-compaction.ts:77-98,161-190` | budget + §5 指纹 | 「派生态+源指纹校验」与参数指纹同构，直接用 |
| L9 | 上下文预算分层 + 同文件旧读取改写 `[outdated]` + 攒 64KB 批量改写（**中途改字节打断 provider 前缀缓存**） | `core/src/session/services/message-builder.ts:28-49` | loop/budget | 采纳去重；前缀缓存经验记下 |
| L10 | 溢出自愈：`context_window_exceeded` → 强制压缩 + **单次**重试（每 run 一次）→ 仍失败抛可读错误 | `agents/src/agent-runtime.ts:470-471,891-932` | loop/driver | 采纳：重试预算=1 防循环 |
| L11 | 终端长输出中间截断（保头尾+删除量标记），**截断规则写进工具描述让模型自知** | `core/src/extensions/tools/definitions.ts:420,437-438` | runtime/stream §4.4 | 采纳 |
| L12 | 流式事件同时带增量与**累积**值（UI 更新幂等、丢一条不坏）；`content_end` 收口 | `core/src/runtime/orchestration/runtime-event-adapter.ts:200-230` | UI 轨迹流 | 直接抄：进度事件带全量状态，重连单条即可重建 |

超出表格但必须记录：**hub 守护进程**——authority runtime 在 detached daemon，客户端 `session.attach/detach` 不停任务（`core/src/hub/server/handlers/session-handlers.ts:678-755`、`sdk/ARCHITECTURE.md:145`）；每进程随机 token 写 owner-only 发现文件（`ARCHITECTURE.md:183-193`）——与我们 wrapper 作业目录协议同一思想；其构建指纹校验（`:196-202`）→ reattach 时校验 wrapper 协议版本。

**不照搬**：文件轮询审批 IPC 的 **5 分钟超时自动拒绝**（小时级任务审批可能等人几小时，必须无限期挂起为 WAITING_APPROVAL，超时只用于提醒）；每用户轮次 git 快照（GB 级产物不可行）；连续错误即 abort 整 run（执行层失败走 §4.12 处置矩阵，计数熔断只留给 Brain 层）；惰性会话持久化（run 提交必须立即落库）；消息级压缩投影（Brain 输入是 SQLite 现算投影，无消息历史膨胀）。

### 2.5 特别结论：会话恢复 vs 「服务重启后 reattach 小时级 WSL 任务」

- **OpenHands 恢复的是客户端↔服务端层**：前提是 agent-server 还活着；服务端自身重启后正在跑的 bash 子进程不会存活（软超时续读是同一进程会话内的续读，不是跨进程收养）。桌面模式甚至反向设计：退出显式杀整个后端进程组（`electron/main.mjs:702-716`）。
- **cline 更近一步**：hub daemon 独立于 IDE，IDE 重启不影响任务；但 daemon 自己重启后恢复的是消息/快照/压缩边车，**不是正在执行的子进程**；cron runner 的「claim 续租 + 崩溃 requeue」是任务级重新排队重跑（`sdk/ARCHITECTURE.md:470-475`）。
- **结论**：两家都缺同一块——「执行进程与服务进程解耦 + 通过文件系统协议收养正在跑的外部进程」。根因是它们的工具生命周期 = 服务进程生命周期（秒级命令重跑即可）。**§4.7 方案必须自研，且这恰是论文可用的差异化论据。**
- 拼装件三块现成：① UI 重连协议抄 OpenHands（O8/O9）；② 服务/执行分离方向参考 cline hub（含协议版本校验）；③ 恢复校验纪律抄 cline L8——reattach 前先验作业目录参数指纹与 SQLite 一致，失配判 orphaned，绝不盲目接管。

### 2.6 秒级 vs 小时级：适用性反转清单

**直接失效**：短超时+快速失败（codex 默认 10s；InSAR 需要心跳+阶段进度契约——跑 6 小时正常、6 分钟没心跳才异常）；每个工具调用前全量快照（快照时机改为每个**不可逆决策**前）；「失败让模型自己再试」（一次盲目重试=几小时算力，重试必须升级为审批事件）。

**升级为刚需**：拒绝带理由回传（一次高质量拒绝理由省一次小时级错误作业）；指纹化审批缓存（一个 stack 二十个同参数干涉对逐个弹卡会杀死可用性）；JSONL/事件流的 Resumed/Forked 语义；恢复时重提待决调用（G9）；完成/失败外部通知；大输出转文件指针（从第一天就是日志回传的唯一形态）。

---

## 3. 轻量 harness 层：主循环形状的收敛证据

### 3.1 横向对照

| 维度 | mini-swe-agent | pi | deepagents | claude-agent-sdk |
|---|---|---|---|---|
| 循环位置 | 自己写，~40 行同步 Python | 自己写，事件流异步 TS | LangGraph 图（外包） | CLI 子进程（外包） |
| 动作协议 | 单 bash 工具 | 多工具 toolcall | middleware 注入 | CLI 全家桶 + MCP |
| 工具前拦截 | 覆写 + 白名单正则 | `beforeToolCall`→block/terminate | HITL interrupt | 规则→`can_use_tool`→PreToolUse |
| 中途插话 | 仅 Ctrl-C | steering/followUp 双队列 | HITL interrupt | 双向流式 + interrupt() |
| 终止判据 | 最后消息 role==exit | shouldStopAfterTurn | 图自然终止 | ResultMessage |
| 状态存储 | 内存+每步轨迹 JSON | JSONL 树（可分叉） | LangGraph checkpoint | CLI transcript |

**共同点**（四家独立收敛）：核心循环极小且形状相同（LLM 决策→执行→追加）；append-only 日志为真相源；策略与机械分离且钩子位置一致（模型前/工具前/工具后/轮末）。**四家都没有而我们独有的正确决策是 SQLite 可查询真相源——不必动摇。**

### 3.2 四件可直接落地的设计

**① mini 的异常即控制流**（→ `loop/driver.py`）：所有非正常路径继承 `InterruptAgentFlow(*messages)`，异常对象携带要追加的消息；循环 except 只做 `add_messages(*e.messages)`，退出判据统一为「最后一条消息 role==exit」；`finally: save()` 保证任何路径落盘（`mini-swe-agent/src/minisweagent/exceptions.py:1-26`、`agents/default.py:88-124`）。移植后 budget/audit/api 都不需要持有 driver 引用，只要抛异常；终止原因全部进 SQLite。连「限额现场升级」都自然实现（`agents/interactive.py:80-94` 捕获 LimitsExceeded 问用户要新限额）。

**② pi 的五钩子面 + 双投递队列**（→ `loop/driver.py` + `core/actions.py`）：`transformContext / beforeToolCall / afterToolCall / shouldStopAfterTurn / prepareNextTurn` 五组钩子（`pi/packages/agent/src/agent-loop.ts:226-292,619-647,724-751`）；steering（本轮内插队）与 followUp（agent 将停时续跑）双队列（`agent-loop.ts:182-190,259,263-268`），扩展 API 显式暴露 `deliverAs: "steer"|"followUp"|"nextTurn"`（`extensions/types.ts:1302-1305`）。防御细节：`stopReason=="length"` 时整批工具调用按参数可能截断处理、拒执行让模型重发（`agent-loop.ts:208-214`）。审计梯挂 beforeToolCall 位、runok 挂 afterToolCall 位。

**③ claude-sdk 的 `can_use_tool` 回调形状**（→ `api/app.py` 权限协议）：`PermissionResultAllow{updated_input?}`（批准同时改参数）/ `PermissionResultDeny{message, interrupt}`（拒绝必须带理由，回给模型）；`ToolPermissionContext` 自带 UI 渲染素材（title/description/suggestions）；配置遮蔽显式告警（`claude-agent-sdk-python/src/claude_agent_sdk/types.py:236-259,201-234,1727-1787`）。这套形状与本机 no-heavy-compute 规则严丝合缝：前端可回 allow / allow+updated_input（降线程数）/ deny+reason，理由写回事件流供 Brain 下轮可见。

**④ deepagents 的标准化错误字面量 + todo 纯工具化**：错误码定为「LLM 可理解并可能自行修复」的 Literal 闭集（`deepagents/libs/deepagents/deepagents/backends/protocol.py:30-53`）——佐证 §4.12 失败分类闭集，且提示：**错误归一化做得越好，Brain 的选择题出得越准**（`dem_missing`→候选集出现「下载 DEM」）。todo 只是工具+状态字段+prompt，没有执行语义（引擎不解释它）——SQLite 加 todos 表 + Brain 候选集加 update_plan 动作即可，前端直接渲染成进度 UI。subagent 上下文隔离协议（父状态剔除 messages、只回传结论，`middleware/subagents.py:537-539,486-512`）适用于将来日志分析/报告草拟等高 token 子任务。

**不适用**：deepagents 本体（LangGraph 硬依赖到私有 API：`graph.py:12-31` 六处 langgraph import、`backends/state.py:7` 直接用 Pregel 私有常量——已否决 LangGraph 则只取思想不引包）；pi 的运行时自扩展（与候选集封闭动作空间矛盾）；claude-sdk 的 stdio 子进程协议（我们 driver 与 API 同进程）；mini 的哨兵字符串提交（runok 双判定是更强完成判据）与每步全新 subshell（与 WSL 长任务不兼容）。

---

## 4. 定向调研结论：三个待验证决策关闭

### 4.1 Burr：不引入，自研（关闭 DESIGN.md §16.3）

三条约束逐项检验均不兼容（行号相对 `burr/`）：
1. **三事件源合流**：Burr 循环是 pull 型，唯一驱动源是 state；外部输入只能在 halt 停下后注入再重启（`burr/core/application.py:1287-1302`）。包一层自己的事件循环后，Burr 退化成查转移表——而转移表本体只有十几行（`burr/core/graph.py:150-160`）。
2. **小时级子进程**：`_step` 同步阻塞到返回；崩溃在 action 中途时连快照都没有，恢复只能整步重跑（`application.py:2683-2686` 证实 DESIGN.md §16.3 的判断）。
3. **零基础设施**：合格，但快照式 JSON 持久化（`persistence.py:574-580`）与结构化 steps 表并存会分裂真相源。

**抄走 6 个技巧**：halt_before/halt_after 双语义且 before 优先（`application.py:1213-1221,1276`→干预暂停点区分「进入前停/完成后停」）；sequence_id 先递增再执行防 replay 卡死（`application.py:929-932`）；failed 状态也落快照（`persistence.py:214-234`→失败尝试同样是 provenance）；(app_id, sequence_id, position, status) 四元组主键（`persistence.py:405-426`）；Condition 有序求值+default 兜底（`graph.py:150-160`→LLM 决策前的确定性转移规则表）；tracking 的 begin/end+span 层级 jsonl schema（`tracking/client.py:447-509`→trace 导出时 LLM 子调用作 span 挂在 step 事件下）。

tracking UI：观赏性存在但不值得为它引入框架——只覆盖决策层轨迹，不覆盖执行器/产物流；我们 trace 表对标 OpenDiscoveryTrace 字段更全；论文演示图用自己的数据画。

### 4.2 Snakemake：五触发器实现已见底（充实 DESIGN.md §6）

真实存储模型（行号相对 `snakemake/`）：元数据**按输出文件为键**（`src/snakemake/persistence/__init__.py:450-452`），每个输出文件挂一条 MetadataRecord（rule/input/shellcmd/params/code 全文/conda_env/input_checksums/starttime/endtime/incomplete/external_jobid，`persistence/__init__.py:36-54`）；作业开始打 incomplete 标记+记 external_jobid、完成后清除（`:463-466,525-526`）——与我们 PREPARED→RUNNING + ext_job_id 同构。比较前先查 `RECORD_FORMAT_VERSION`（=6），低于阈值判「不可比、不触发」（`:32,660-696`，注释记录 v4/v6 两次踩坑）。

**移植三点**：
- **mtime + checksum 双重验证**：输入比最老输出新**且** checksum 与记录不一致才算 updated_input（`dag.py:1512-1518`）——touch 不触发重算；带缓存与文件大小上限（`dag.py:1422-1445`）。
- **masked 剪枝**：拓扑序遍历中某 job 判 needrun 即对其下游 BFS 入 masked 集合，后续跳过其五触发器计算（`dag.py:1618-1628`）——级联标脏时下游只记 upstream_changed，不逐个算指纹。
- **record_format_version 门控**：见 absorb-M。

**避开两坑**：
- **坑一 params 丢键名**：存储是排序后的 repr 值列表（`persistence/__init__.py:788-795`），对称差比较说不出哪个键变了；两参数互换取值（a=1,b=2→a=2,b=1）**根本检测不到**。我们必须键级存 canonical JSON（DVC 的做法才是正确参照）。
- **坑二 代码触发无逃逸阀**：run 块源码全文比较（`persistence/__init__.py:660-665`），改一行日志=全量重算，唯一逃生是全局关掉整类触发器。redun 的 version 阀 + 我们强制 tool_version 进 task_hash 正是修正。
- 另两个可借鉴细节：派生参数不进参数指纹（`rules.py:983-1003`，其变化由 INPUT 触发器捕捉——佐证我们参数三分类）；UNREPRESENTABLE 哨兵（`persistence/__init__.py:744-769`，但要改为显式降级+warning，不能静默丢弃）；`--consider-ancient` 用户否决通道（`cli.py:851-861`）→ 接到干预队列。

**关键差异**：Snakemake 的传播是无条件级联，没有 early cutoff；DVC 有（见下）。我们的 eval_hash 级联应做到：上游重跑后产物指纹不变 → 下游 eval_hash 不变 → 清除 stale。

### 4.3 DVC：dvc.lock 对照完成（关闭 DESIGN.md §16.1/§16.5）

（行号相对 `dvc/`）dvc.lock 每 stage 条目：`cmd` + `deps[{path, hash:"md5", md5, size, nfiles}]` + `params{file:{key:value}}`（**存实际值非摘要**）+ `outs[同 deps 结构]`，固定字段序、全排序（`dvc/stage/serialize.py:143-192,94-111`）。params 键级追踪极朴素：`hash_info = HashInfo("params", {key: value})` 就是键值字典本身，diff 即逐键 `!=` 输出 new/deleted/modified 三态（`dvc/dependency/param.py:172-183,141-159`）。changed 判定短路序（便宜在前）：`changed_stage() or changed_deps() or changed_outs()`（`dvc/stage/__init__.py:361-373`）。**下游传播是隐式的**：上游重跑→outs md5 变→下游 dep.status() 报 modified，由此自然获得 **early cutoff**（上游重跑但产物 md5 没变→下游自动跳过）；`--force-downstream` 是显式覆盖（`dvc/repo/reproduce.py:197-198`）。

**run-cache 是 eval_hash 的同构先例**：key = sha256(cmd + deps md5 + params 值 + outs 路径)（`dvc/stage/cache.py:28-33,59-63`）≈ 我们的 eval_hash（deps md5 传递上游身份 ≈ upstream_eval_hashes）；value = sha256(含 outs md5 的完整条目)（`cache.py:165`）≈ artifacts.fp。区别：DVC content-addressed 可物理恢复产物；我们 computation-addressed，「复用」=eval_hash 命中且产物指纹有效→跳过执行，不需要 checkout。**额外可抄**：key/value 分离；同 key 多 value 保留历史（`cache.py:103-109`→同参数多次运行 QA 不同的审计场景）；`_can_hash` 门槛（不确定性 stage 不缓存，`cache.py:36-56`）。

**逐字段对照结论**：steps/artifacts/edges/commands 已覆盖 dvc.lock 全部语义，且多出 stage 状态机、run_ok 双判定、stale_reason、pid/ext_job_id/log_offset 四个维度（论文对照表直接引用）。**需补三处**：
1. `steps`/`artifacts` 表加 `record_version INTEGER NOT NULL`（对齐 Snakemake RECORD_FORMAT_VERSION 与 DVC `schema:"2.0"`），算法升级后旧记录判 stale（stale_reason 补 `outdated_metadata`），防误判可复用。
2. `artifacts.fp` 改三段编码 `"<policy>:<algo>:<digest>"`（如 `content:sha256:ab12…`），对齐 DVC 算法名显式入档（`serialize.py:160-161`）。
3. §6.2 provenance JSON 导出为每 step 物化 `cmd`（最后成功 attempt 的 argv）与 `deps:[{art_id, fp}]`——SQLite 里靠 edges 隐式重建是对的，导出时物化让「step 条目字段 ⊇ dvc.lock 字段」逐字段成立，审稿人并排可核对。
4. 附带：`steps.params` 必须存 normalize 后的 canonical JSON（否则事后键级 diff 与当时参与 args_hash 的内容不一致）。

---

## 5. 吸收决议（延续 COMPARISON_LEARNING.md 的 absorb-A~D）

| ID | 决议 | 来源 | 落点 |
|---|---|---|---|
| absorb-E | 拒绝/取消必须带理由字符串并作为工具结果回给模型；理由进 Brain 下一道选择题上下文 | codex C4/C5 + claude-sdk Deny.message | brain/select + UI 审批卡返回协议 |
| absorb-F | 审批体系四件套：审批卡按类型分型（参数/作业/覆写）；粒度四档且「总是」按参数指纹族；fail-closed（无回调默认拒绝）；审批挂起 WAITING_APPROVAL 无限期等待、超时只提醒不自动拒绝 | gemini G2/G3/G5 + cline L3 + 反例 cline 5min 超时 | §7.6 + loop/driver |
| absorb-G | 断线重连协议：SQLite 拉历史 + 按事件自增 id 增量推送 + 前端去重 + 重放不触发副作用；掉线期间用户消息进 pending_actions | OpenHands O8/O9 | §7.8 + api/app.py |
| absorb-H | 事件工厂定稿：封闭联合体 + kind 判别 + source 枚举{agent,user,environment,engine,hook}；用户拒绝/干预也是 Observation 事件；落盘白名单写成显式穷举函数 | OpenHands O1/O2 + codex C11 + pi 事件词表 + claude-sdk hook 命名 | loop/events.py |
| absorb-I | 异常即控制流：InterruptRun(*events) 异常族 + finally 每步落盘 + 「最后事件 role==exit」统一终止判据 | mini-swe-agent | loop/driver.py |
| absorb-J | 干预队列补双投递语义（queue/steer/nextTurn）+ 未消费动作可编辑撤回 + USER_MESSAGE 动作类型 | pi + cline L6 + OpenHands O9 | core/actions.py §4.5 |
| absorb-K | 权限回调形状：allow+updated_input（批准同时改参数，如降线程）/ deny+message；请求自带 UI 渲染素材；配置遮蔽显式告警 | claude-sdk | api/app.py 权限协议 |
| absorb-L | 日志回传唯一形态：头尾截断（HeadTailBuffer + omitted 标记）+ 全量落盘按 log_offset 寻址 + 截断规则写进给模型的说明；超时/失败错误必须携带已捕获输出 | codex C8/C9 + gemini G10 + cline L11 | runtime/stream.py §4.4 |
| absorb-M | schema 三处补齐：record_version 门控字段；fp 三段编码 policy:algo:digest；provenance 导出物化 cmd+deps；params 存 canonical JSON | snakemake + dvc | core/schema.sql + ledger.py §6.1/§6.2 |
| absorb-N | 级联两个优化：masked 剪枝（下游只记 upstream_changed 不重算指纹）+ early cutoff（上游重跑产物指纹未变则清除下游 stale）；changed 判定短路序（内存哈希→IO 指纹） | snakemake + dvc | core/stale.py §5.1 |
| absorb-O | 熔断三段式（软警告 3 次/硬熔断 5 次，签名=动作+排序键 JSON）限 Brain 决策层；stuck 设为一等状态与 failed 区分 | cline L1/L2 + OpenHands O4 | loop/driver §3.3 |
| absorb-P | turn/作业结束拉起外部通知命令（可配置 argv，JSON 传参）；预算余量作为状态事件常态推流 | codex notify + OpenHands O5 | §7.5/§7.8 + loop/budget |

另记两条佐证（不新开 ID）：deepagents 错误字面量闭集佐证 §4.12 失败分类闭集，补充「错误归一化质量决定选择题质量」的论述；Snakemake 派生参数剔除佐证 §5.4 参数三分类。

---

## 6. 明确不照搬总清单（跨仓库汇总）

| 不照搬 | 出处 | 理由 |
|---|---|---|
| OS 级沙箱（Landlock/seatbelt）、execpolicy 规则语言、Guardian LLM 审批 | codex | 候选集封闭已消掉任意命令威胁；审批权必须在人 |
| shadow git 全工作区快照、每轮次 git 快照 | gemini/cline | GB 级栅格不可 git 对象化；产物版本由参数指纹决定、可重算 |
| event sourcing 作唯一真相源、LLM 摘要式 condenser、LLM 预测 security_risk | OpenHands | SQLite 行级状态已定为真相源；伤复现性；与选择题定位冲突 |
| 审批 5 分钟超时自动拒绝、错误计数熔断整个 run、惰性会话持久化 | cline | 小时级审批要等人；执行层失败走处置矩阵；run 提交必须立即落库 |
| 引入 Burr / deepagents（LangGraph 硬依赖到私有 API）/ pi 运行时自扩展 / claude-sdk stdio 协议 | §4.1/§3.2 | 分别与三事件源、已否决依赖、封闭动作空间、同进程架构冲突 |
| Snakemake 按输出文件为键的元数据、params repr 集合比较、代码全文比较 | snakemake | 步骤级干预需要 step 为节点；丢键名检测不到互换；无逃逸阀 |
| DVC 内容寻址缓存 + checkout 物理恢复、以 git commit 为状态锚 | dvc | 几十 GB 产物原地消费；run 目录 + SQLite 是独立锚点 |
| mini 哨兵字符串完成信号、每步全新 subshell | mini-swe-agent | runok 双判定更强；与 WSL 长任务不兼容 |

---

## 7. 对 AGENT-DESIGN.md 的修正建议（写回主文档时逐条核对）

1. §16.3 决策关闭：Burr 不引入，附 `application.py:2683-2686` 的整步重跑证据；把 6 个技巧并入 §3/§6。
2. §6.1 schema：steps/artifacts 加 `record_version`；`artifacts.fp` 三段编码；`stale_reason` 闭集补 `no_metadata`、`outdated_metadata` 两类。
3. §6.2 导出：per-step 物化 cmd+deps；`steps.params` 声明为 canonical JSON。
4. §5.1 级联：写入 masked 剪枝与 early cutoff 语义（上游重跑产物指纹未变→清除下游 stale）；changed 判定短路序。
5. §4.5 干预队列：动作补 `deliver_as`（queue/steer）语义、可编辑撤回、`USER_MESSAGE` 类型。
6. §3.3 熔断：细化为软(3)/硬(5)两级 + stuck 一等状态；溢出自愈重试预算=1。
7. §7.6 审批：补 fail-closed 原则、WAITING_APPROVAL 无限期挂起、审批缓存按参数指纹族、审批卡三分型、「拦截先于审批」。
8. §7.8 reattach：补「接管前校验作业目录指纹与 SQLite 一致，失配判 orphaned」纪律 + wrapper 协议版本校验；断线重连协议按 absorb-G 落地。
9. §7.5 新增：作业完成/失败/等审批的外部通知命令配置（notify argv）。
10. §4.4 日志：明确「头尾截断 + 文件指针」为回传给模型的唯一形态，截断规则写进能力说明。

---

## 8. 与既有结论的一致性核对

- 本篇结论与 `COMPARISON_LEARNING.md` 的「架构方向无需改动」一致：11 个仓库没有一个动摇「LLM 只做选择题 + SQLite 真相源 + 自研执行层」的主线，反而四家 harness 收敛验证了它。
- `DESIGN.md §9.1` 否决重框架的判断进一步强化：deepagents 对 LangGraph 私有 API 的依赖（`backends/state.py:7`）说明「基于重框架的薄层」也会被地基锁死。
- 主 novelty 边界更新：aiida-workgraph 已实现「改参数→自动标脏」（AGENT-DESIGN 前置修正 1），本篇再确认 DVC 有键级参数失效 + early cutoff、Snakemake 有五触发器——**我们的差异化必须持续锚定在「LLM 动态规划 + 运行中人机共控 + 小时级外部进程收养 + 质量门」的组合上**，单点机制均有 prior art，论文表述遵循 DESIGN.md §16.4 的修正句式。
