# 主流 AI Agent 产品的执行过程呈现与人机协作 UX 调研

> 日期:2026-08-12 · 方法:网络公开资料调研(官方文档 / 官方博客 / 系统卡 / 社区论坛 / 第三方评测)
> 对象:Cursor、Claude(Artifacts + Claude Code)、GitHub Copilot(Workspace → coding agent → Agent HQ)、Devin、OpenAI(Canvas / Operator → ChatGPT Agent / Codex)、Manus、Replit Agent,附跨产品 UX 模式研究。
> 用途:为 insar-agent 原型(`prototype/js/stream.js` 事件卡片流、`app.js` 审批卡/装配层、`dock.js` 右侧面板)提供机制借鉴与落地建议。
> 可信度标注:凡仅见于第三方博客、未获官方文档佐证的说法,均标〔低可信〕;定价类信息一律不采。

---

## 1. Cursor(Agent / Composer / 2.0 多代理界面)

### 1.1 长任务执行流呈现

- **工作组折叠(work-group collapse)**:只读类工具调用(文件读取、grep、glob、web 搜索、MCP 工具列表查询)在任何密度设置下都**自动聚合折叠**为一行摘要条目 "Explored N tools";回合完成后整段工作再折叠进 "Worked for Ns" 分组。点击标题展开。官方明确这是设计而非 bug:默认呈现的是"近乎完整的逐步上下文"而非"全部展开"。
- **密度分层**:设置项 Conversation Density(Detailed 档)只把**文件编辑与终端命令**提升到顶层内联显示,探查类调用仍保持聚合——即"改变世界的操作"与"只读探查"在信息层级上被区别对待。
- **Agent To-dos**:agent 自己维护带依赖关系的结构化待办清单,在聊天中可见、随执行实时更新(甚至可流式同步到 Slack);官方修过"全部完成后待办卡消失"的 bug——说明设计意图是**完成后仍保留在流里作为记录**。
- **思考块**:thinking 默认折叠、流式过程中也可展开/收起。
- **2.0 Agent 侧栏**:界面从"文件树中心"转为"agent 中心"。右侧栏列出每个 agent:名称、状态、进度指示、输出日志,实时显示当前动作("searching codebase" / "editing files"),并用 context pills 显示其正在使用的文件。

### 1.2 审批 / 确认

- 常规编辑默认直接落盘(通过 diff 视图事后 review/reject),终端命令按白名单/沙箱策略弹审批;2.0 引入沙箱终端(GA)降低审批频率。
- **检查点(Checkpoints)**:每次 prompt 触发文件修改前自动快照;悬停任意历史消息出现 Restore Checkpoint,一键把**所有文件**回滚到该时刻,聊天历史保留。官方强调:仅用于撤销 agent 修改,不替代 git。

### 1.3 失败呈现

- 失败的终端命令内联显示退出码与输出;修复方向依赖用户追问或 agent 自行重试。公开资料中 Cursor 没有独立的"错误卡片"组件——失败信息就落在对应工具条目里,靠checkpoint 回滚 + 重新提示恢复。

### 1.4 干预 / 打断(排队语义,做得最细的产品)

- 三种发送语义,默认可配(Settings → Chat → Queue messages):
  - **Send**(默认):在 agent 的"下一个合适时机"插入(不打断进行中的工具调用——官方解释:中途打断会导致模型拿不到预期结果、输出劣化);
  - **Alt+Enter Queue**:排到本回合结束后;队列可**重排序、单独取消**;
  - **Cmd/Ctrl+Enter Stop & send**:立即停止并发送。
- **Ctrl+C 为"急停"热键**,停止后自动聚焦输入框并带回上一条消息便于修改重发。
- 队列与 Multitask 的组合:排队后点 "Start Multitasking" 可把运行中任务转入后台子代理,腾出前台跑下一条。

### 1.5 多任务并行

- 2.0 支持一个 prompt 并行跑最多 8 个 agent,用 **git worktree 或远程机隔离**(`.cursor/worktrees.json` 定义环境初始化),侧栏统一管理各 agent 的计划、进度、diff;支持同题多解对比后择优合并。〔注:某第三方评测称其机制为 "Shadow Virtual File System",与官方 worktree 口径不符,视为低可信,不采。〕

### 1.6 事件流与工作区联动

- 文件以 pills 内联出现在对话中,点击打开;多文件 diff 合并成一个连续 review 流(2.0 "improved review"),不用逐文件跳转。

**来源**:cursor.com/changelog(2.0、3.0、Agent To-dos、Queue messages)、cursor.com/docs/agent/overview(Checkpoints)、forum.cursor.com #165292(工作组折叠的官方答复)、#55698 与 #126214(排队语义演进)、#163311(队列×Multitask)、howaiworks.ai/blog/cursor-2-0、codecademy.com/article/cursor-2-0。

---

## 2. Claude(Artifacts / Claude Code / Claude Code Web)

### 2.1 Artifacts:对话流与工作区的"过程/交付物"分离

- 达到一定体量/复杂度的产出自动进入**右侧独立窗口**,与聊天并排;聊天=过程,artifact=持续迭代的交付物。
- 面板内:preview/源码切换、复制、下载、**版本选择器**(revision 历史可回退);Markdown 支持划选文本 → "Edit with Claude" **原位定点修改**,不必在聊天里描述位置。
- 多 artifact 并存,通过 chat controls 切换,并可**指定 Claude 后续更新哪一个**。
- Claude Code 也能把会话发布为持续更新的网页 artifact(带注释 diff 的 PR 讲解页、随会话推进的调查时间线等)——"执行过程可以物化为一份活文档"。

### 2.2 Claude Code:审批体系(当前公开产品里最完整的风险分级)

- **权限三层规则**:allow / ask / deny 规则表(按工具、按命令前缀匹配),settings.json 分 user/project 层级;审批提示中可选 **"Yes, and don't ask again for `<前缀>` commands"**(记忆按命令前缀,作用域分会话/项目)。
- **权限模式闭集**:`default`(每工具首次询问)、`acceptEdits`、`plan`(只读)、`auto`(分类器后台审查、自动放行对齐用户意图的调用)、`dontAsk`、`bypassPermissions`;**Shift+Tab 单键循环切换**,当前模式常驻状态栏。
- **Plan mode 是一个显式状态机**:只读探索 → 写计划文件 → 提交审批。审批提示给三个出口:"批准并进 auto 模式 / 批准但逐个审编辑 / 继续规划";**Ctrl+G 可直接在编辑器里改计划原文**再放行。实现上只是提示词强化 + 进出状态的工具,但 UX 上形成了"想清楚再动手"的硬检查点。
- 内置只读命令集(ls/grep 等)在沙箱内不弹审批——**风险分级落到命令粒度**。

### 2.3 Claude Code Web:云端并行会话

- 每任务独立云会话并行跑,侧栏列出全部会话;每个会话带 **`+42 -18` 式 diff 徽标**,点开 diff 视图,可**在具体行上留内联评论**,随下一条消息发给 Claude。
- 执行中可随时"steer"(插话、答疑、给反馈);完成后一键建 PR,或 `--teleport` 把会话拉回本地终端继续。CLI 里 `/tasks` 监控全部云会话。
- 定位是"监督-引导"而非终端:云会话没有交互 shell,你看进度、留评论,不敲命令。

**来源**:code.claude.com/docs(permission-modes、permissions、artifacts、claude-code-on-the-web)、support.claude.com artifacts 帮助页、claude.com/blog/artifacts、anthropic.com/news/claude-code-on-the-web、lucumr.pocoo.org/2025/12/17/what-is-plan-mode(Plan mode 逆向分析)。

---

## 3. GitHub Copilot(Workspace 遗产 → coding agent + Agent HQ)

### 3.1 Copilot Workspace(2024-04 技术预览,2025-05-30 落幕)——阶段化任务流的教科书

虽已下线,其 **topic → spec → plan → implement** 四段式仍是"每一阶段都可编辑、可回退"的最完整范本:

- **Topic**:把任务改写成一个可对代码库提问的问题;**Spec**:生成"当前状态 / 目标状态"两列 bullet(成功判据,不谈实现);**Plan**:文件级变更清单,每个文件附具体步骤,**可增删改排**——官方手册称"计划就是你的指令集";**Implement**:右侧出现排队的文件更新,逐个生成,计划项实时标 in-progress → done;之后可勾选部分文件"仅重生成所选",带集成终端验证,最后开 PR。
- 落幕后经验被重建为 coding agent:从"交互式网页编辑器"转向"**异步云工人**"(分配 issue → 隔离环境跑 → 产出 draft PR)。

### 3.2 coding agent + Agent HQ / Mission Control(2025-10 起,现行形态)

- **会话列表**:仓库新增 Agents 标签页(2026-01),会话与代码/PR/issue 同层;任意页面可呼出 agents 面板;会话可归档、分页;云会话默认对仓库可见成员共享。
- **会话日志(session log)是核心呈现单元**:展示 agent 的**内部推理 + 工具调用 + 文件 diff + 测试输出**,官方定位是"审计 agent 行为的依据"、"reasoning artifacts——挖掘它来改进你下一条 prompt"。
- **2026-01 重设计的日志呈现**(与本项目最相关):
  - 相似工具调用**自动分组**降噪;
  - 工具输出**内联预览**;文件变更用熟悉的 diff 视图,一键展开;
  - 每种工具有**专属图标**;bash 命令原文完整可见(透明性);
  - 显示 token 用量与会话时长。
- **执行中转向(steering)**:日志下方常驻输入框,提交的反馈在**当前工具调用结束后**生效(与 Cursor 的"下一个合适时机"同一语义);Stop session 保留已推送的 commit。
- **跨端接续**:VS Code Agent Sessions 视图统一本地+云会话;"Continue in Copilot CLI" 复制一条命令在终端接续;完成后可在 Copilot Chat 里**用自然语言查询会话历史**("改了什么、验证了什么、为什么")。
- 治理:agent PR 必须过状态检查 + 非发起人的人工审批,commit 标注 co-authored。

**来源**:github.blog "How to orchestrate agents using mission control"、github.blog changelog 2026-01-26 "Agents tab"、docs.github.com manage-and-track-agents、githubnext.com Copilot Workspace 用户手册(overview.md / vscode.md)、GitHub Next 落幕公告(gist)、GitHub Checkout 视频(Mission Control 演示)。

---

## 4. Devin(Cognition)

### 4.1 工作区结构:聊天 + 多标签实况面板

- 会话界面 = 左聊天流 + 右侧工具标签:**Progress(统一进度视图)/ Editor(IDE,diff 审阅与直接改)/ Desktop(原 Browser,可视浏览器+桌面)/ Shell / Planner**。Devin 可并发执行(边看浏览器边跑 shell 边读文件),Progress tab 把三者汇成一条时间线。
- **接管语义**:Desktop 标签支持用户直接操作浏览器——CAPTCHA、多因素认证、复杂导航等 Devin 搞不定的环节由人接手,完成后归还控制权。

### 4.2 计划与置信度(该产品最值得抄的两个机制)

- **Interactive Planning**:开会话后数秒内先返回"相关文件 + 调研发现 + 初步计划",计划**可编辑**,确认后才转入自主执行;执行开始后仍可继续改计划。
- **Confidence Scores(Devin 2.1)**:在会话开始、计划生成后、回答代码问题时,用 🟢🟡🔴 三级表达把握度;**非绿色时自动停下等用户批准才继续**,绿色则直接执行。官方称置信度与任务成功率高度相关;还可批量给 Jira/Linear 工单打置信分,把高置信的先派给 Devin。——本质是**用不确定度驱动审批频率**:有把握不打扰,没把握必须问。

### 4.3 受阻与失败

- 遇到环境缺失、设计决策拿不准等阻塞时**主动升级给人**:web 界面 + Slack 私信通知(通知触发器可配:任务开始/完成/PR 开出/**被阻塞需人工输入**/构建测试失败);阻塞太久会丢上下文需要重启——所以产品文档明确要求"尽快回答"。
- 全程活动日志可回看,可**回滚到会话历史任意点**。

### 4.4 多任务管理:Kanban + Spaces

- **Agent Command Center**(Devin Desktop / Windsurf 2.0):所有本地+云 agent 以 **Kanban 按状态分列**(进行中/被阻塞/待审阅/完成),一屏看清"哪个在跑、哪个要我管、哪个能收";运行中的会话**锁定置灰(只读)**,跑完才能进去改——避免人机同时写。
- **Spaces**:把一个任务/项目的会话、PR、文件、上下文聚成一个视图;新会话自动继承 Space 已知上下文;离开再回来时视图原样恢复。

**来源**:cognition.ai/blog/devin-2、docs.devin.ai(devin-session-tools、agent-command-center、spaces、slack、release-notes/2025 与 2026)、devin.ai/blog/windsurf-2-0。

---

## 5. OpenAI(Canvas / Operator → ChatGPT Agent / Codex)

### 5.1 Canvas:并排画布的定点编辑

- 文档/代码锚定在右侧持久面板,聊天在左;**划选局部 → 定向修改**,改动处绿色高亮;快捷菜单(改长度/阅读级别/修 bug/加日志/加注释/移植语言);**back 按钮回退版本**。2026 年演化为聊天流内嵌的可编辑 writing/code blocks(画布从"侧面板"内化为"流内可编辑块")。

### 5.2 Operator → ChatGPT Agent:高风险自动化的安全 UX 标杆

- **实时叙述(on-screen narration)**:执行中持续用一句话说明"我正在做什么",配虚拟浏览器实况画面——过程透明是默认,不是可选项。
- **后果性动作强制确认**:模型被专门训练为在"影响真实世界的动作"(购买、发邮件)前停下询问;Operator 系统卡给出量化指标——607 个高风险任务上确认询问召回率 92%。**风险分级不是 UI 标签,是训练目标 + 评测集**。
- **Watch Mode**:在敏感站点(邮箱、银行)自动进入"用户必须在场"模式——**用户切走页面或不活跃时执行自动暂停**,回来才能继续。
- **Takeover mode**:用户接管浏览器输入凭据时**停止截屏、不采集输入**(模型"不需要也最好永远看不见"密码);归还控制后从先前工作流状态续跑。
- 长任务结束推送手机通知;随时可打断、停止。

### 5.3 Codex(网页任务队列 → 2026-02 Codex App)

- 每任务独立线程 + **git worktree 隔离**(detached HEAD,不污染分支命名空间,满意后才 "Create branch here"),按项目组织;左栏实时显示各 agent 状态(running / paused / completed);需要装包等越权操作时弹授权提示。
- **Review pane**:任务完成后一屏呈现"改了哪些文件 + 跑了什么命令 + 测试结果";内联 diff 审阅器上可**逐行评论、按块暂存/回退**,然后批准 / 要求返工 / 直接推 PR。
- **Automations**:定时后台任务,结果统一落入 **review queue(收件箱)**等人审——"自动执行,人批结果"的异步分工。

**来源**:openai.com/index/introducing-chatgpt-agent、help.openai.com ChatGPT agent 帮助页、Operator System Card 与 ChatGPT Agent System Card(cdn.openai.com)、openai.com/index/introducing-the-codex-app、theverge.com Canvas 报道、verdent.ai Codex app 解析〔低可信,仅取与官方一致部分〕。

---

## 6. Manus

### 6.1 "Manus's Computer":实况工作区面板

- 三栏:左侧会话历史,中间对话,右侧 **"Manus 的电脑"**——在 Browser / Terminal / VS Code 视图间自动切换,实时展示开网页、滚动、点击、跑代码的过程,"像看人共享屏幕"。透明性本身作为核心卖点。
- 云端沙箱执行:关掉浏览器任务照跑,支持多任务并行;Wide Research 模式可扇出上百个并行子 agent 做大规模信息收集。

### 6.2 计划呈现:todo.md 即进度条

- 任务开始先生成 Markdown 待办清单(`todo.md`),用 `[ ]`/`[x]` 标记完成态,**执行过程中实时回写更新**——计划文件同时是 agent 的工作记忆和用户的进度视图,一物两用。

### 6.3 回放(replay):事后审计的独有机制

- **整个会话可完整回放**:逐步重演 agent 当时的操作(每个浏览器步骤都有截图留痕),可分享回放链接(沙箱保持私有);官方把 Replay 列为排查"结果不对劲"的第一手段。
- 干预:执行中可随时插话重定向。失败处理主要靠编排器自动换策略重试;第三方评测同时指出其失败模式——**分支多的长任务可能悄悄跑偏,直到结束才发现**〔对照:这正是"过程可见 + 中途检查点"要防的〕。

**来源**:spectrumailab.com Manus 上手指南、aisharenet.com Manus 交互设计分析(todo.md 机制)、toolchase.com Manus 指南、sidsaladi.substack.com Manus 101〔以上均为第三方,todo.md 与 Computer 面板、Replay 为多源交叉印证;revolutioninai.com 单源细节不采〕。

---

## 7. Replit Agent(Agent 3 → Agent 4)

### 7.1 执行流与进度

- Agent pane 实时进度;**App Testing 时在 agent 面板内嵌浏览器预览,能看到 agent 的光标在自己点应用**(点按钮、填表单),测试完回一份摘要并自动修发现的问题——"自证工作有效"作为呈现的一部分。
- Max Autonomy 连跑 200 分钟+,期间手机端/主页可远程实时监控。

### 7.2 计划审批与任务板

- **Plan mode / Build mode 二分**:Plan 只聊不动代码;agent 给出任务清单后,**"Start building" 按钮 = 审批动作**,点了才切 Build 开工;不满意就继续聊着改计划。
- Agent 4 演化为 **plan-while-building**:主线程在跑,可另开线程规划;新任务进 **Kanban 板(Drafts / Active / Ready / Done)**,每张任务卡带标题、描述、可展开的 "View plan";批量 **Accept tasks / Revise plan**;每个任务在**项目的隔离副本**里跑,完成后展示"工作日志 + 测试结果 + 变更实时预览",由人 **Apply changes to main version / Dismiss**——主版本在显式批准前一字不动,多任务合并冲突自动解决。

### 7.3 检查点 / 回滚(全环境快照,业界最重)

- 每个逻辑里程碑自动建 **checkpoint,捕获代码 + 数据库状态 + AI 对话上下文**;一键 Rollback(带确认警告),可先**非破坏性预览**该检查点状态再决定;Agent 聊天里有 history 时间轴列出全部检查点;同时以 git commit 形式暴露在 Git pane。

**来源**:replit.com/blog(introducing-agent-3、automated-self-testing、whats-changed-agent3-to-agent4)、docs.replit.com(plan-mode、task-system、checkpoints-and-rollbacks、canvas)。

---

## 8. 六问横向速览

| 问题 | 业界收敛做法(2025–2026) | 代表 |
|---|---|---|
| 1. 长任务执行流 | 只读探查聚合折叠成一行,写操作/终端命令顶层内联;回合完成后整体再折一层;活待办清单(带依赖)实时更新且完成后保留;当前动作一句话实况 | Cursor 工作组、Copilot 日志分组、Manus todo.md、ChatGPT Agent narration |
| 1'. 工具卡展开策略 | **默认折叠、失败保持展开**已是共识;每类工具专属图标;命令原文必须可见(透明);输出内联预览 | Copilot 重设计日志、Cursor |
| 2. 审批/确认卡 | 风险分级到命令/动作粒度(只读免审、写需审、后果性动作强制确认);"不再询问"按**前缀/指纹族**记忆且有作用域;计划本身是最大的审批对象且**可编辑后放行**;低置信度自动升级为必须审批 | Claude Code 权限规则、Operator 92% 召回、Workspace 可编辑计划、Devin 置信度 |
| 3. 失败呈现 | 错误三段式 what/why/next;失败卡带处置按钮组(重试/换法/回滚/看日志);日志入口分层(摘要 → 会话日志 → 原始输出);被阻塞时主动通知(推送/Slack) | 跨产品模式研究、Devin 阻塞通知、Replit 自动修 |
| 4. 干预/打断 | 三档发送语义(合适时机插入/排队/急停),排队可视化可重排;steering 输入框常驻,反馈在当前工具调用后生效;接管-归还(浏览器/终端)有明确的控制权交接仪式 | Cursor Queue、Copilot steering、Devin/Operator takeover |
| 5. 多任务并行 | Kanban 按状态分列(跑/阻塞/待审/完);每任务隔离(worktree/项目副本/独立 VM);运行中会话锁定只读;Spaces 聚合任务上下文;结果进统一 review 收件箱 | Devin ACC、Replit 任务板、Codex App、Cursor 2.0 |
| 6. 流↔工作区联动 | 产物徽标点击跳右侧面板;diff 徽标(+42 −18)点开审阅并可行级评论回传;过程物化为活文档(artifact);会话可回放审计 | Claude Code Web、Artifacts、Manus Replay、Codex review pane |

---

## 9. 可借鉴机制排行(实现成本 × 价值,面向本项目)

评分:价值 = 对 InSAR 长任务场景(小时级流水线、参数敏感、需审计)的贡献;成本 = 在零 npm 依赖、原生 DOM 的 prototype 里的实现量。**先看第一梯队,全部是"半天以内改动、体验跃升"。**

### 第一梯队:低成本 × 高价值(建议立刻做)

| # | 机制 | 来源 | 成本 | 价值 | 说明 |
|---|---|---|---|---|---|
| 1 | **只读工具聚合折叠**("探查了 N 项"工作组) | Cursor / Copilot | 低 | 高 | InSAR 回合里大量 fetch/检查类调用,逐条排列淹没关键卡片;连续只读 tool_call 合并为一条可展开摘要 |
| 2 | **排队消息可视化**(busy 时输入不再只能"停止",提供排队 chip,回合结束自动发送) | Cursor 三档语义 | 低 | 高 | 现有 `interventionEntry` 只覆盖 dock 改参;聊天输入的排队是最高频干预 |
| 3 | **失败三段式 what/why/next** | 跨产品模式研究(Zylos 等) | 极低 | 高 | `failureCard` 已有骨架,补齐"原因"与"建议下一步"字段模板即可 |
| 4 | **完成/需审批时的桌面通知 + 标签页角标** | ChatGPT Agent 推送 / Devin Slack | 低 | 高 | 长任务用户必然切走;`document.hidden` 时用原生 Notification API + `document.title` 前缀,零依赖 |
| 5 | **"不再询问"记忆的可见与可撤销** | Claude Code 前缀记忆 | 极低 | 中高 | 已有 `S.autoApprove` 指纹族;自动通过的 note 上补"撤销该记忆"链接,防止一次勾选永久失控 |
| 6 | **审批卡 before→after 对照行** | Workspace 计划 / Codex diff | 低 | 中高 | 参数/方法变更审批卡加两列对照(现 `rows` 结构已支持),diff 语义比纯文字强 |

### 第二梯队:中成本 × 高价值(下个迭代)

| # | 机制 | 来源 | 成本 | 价值 | 说明 |
|---|---|---|---|---|---|
| 7 | **计划面板"当前步"锚点 + 阶段分组** | Cursor To-dos / Manus todo.md | 中 | 高 | `planPanel` 补 running 高亮、点击滚动到对应工具卡、按"准备/处理/成图/质检"分组标签 |
| 8 | **会话侧栏状态分组**(运行中 / 需要你 / 已完成 + 计数徽标) | Devin Kanban / Copilot agents 面板 | 中 | 高 | `SESSIONS` 已有 tone;按状态分组排序,"需要你"(待审批/失败/阻塞)置顶——多会话并行的最小可用形态 |
| 9 | **产物反向联动**(dock 文件条目 → 跳回生成它的工具卡) | Codex review pane / Copilot 日志 | 中 | 中高 | 正向(工具卡 artifacts → `Dock.openFile`)已有;`tools` Map 已存工具卡引用,补 artifact→toolId 索引即可双向 |
| 10 | **会话回放** | Manus Replay | 中 | 高(讲师场景加成) | 事件流本就是 NDJSON,落盘存档 + "回放模式"按时间轴重演 `consume()`;教学演示价值极大 |
| 11 | **steering 常驻输入语义**(执行中输入框 placeholder 改为"给运行中的 Agent 提意见…",提交走 intervention 而非新回合) | Copilot 日志下输入框 | 中 | 中高 | 把"干预"从隐藏能力变成显式邀请 |

### 第三梯队:高成本或需谨慎(远期/看数据)

| # | 机制 | 来源 | 成本 | 价值 | 说明 |
|---|---|---|---|---|---|
| 12 | **置信度驱动审批**(低置信自动停下必须问) | Devin 🟢🟡🔴 | 高 | 高 | 与我们"证据级别阶梯"天然同构,但置信来源必须可信(历史成功率/数据完备度),**没有依据就不显示**——符合 §0.5.5 诚实性;先做规则版:数据未就绪/阈值 PENDING/首次配置 → 强制审批 |
| 13 | **多任务并行执行**(多 AOI/多参数对比跑) | Cursor worktree / Codex 线程 | 高 | 中高 | 后端要做任务隔离与资源配额(受本机重型计算管控约束),UI 侧先由 #8 会话分组铺路 |
| 14 | **dock 自动跟随**(当前步骤自动切到最相关面板,可关) | Manus Computer 视图自动切换 | 中 | 中 | 有打扰风险,默认关,提供"跟随执行"开关 |
| 15 | **全环境检查点回滚** | Replit checkpoints | 高 | 中 | 我们的指纹 + STALE 级联 + 断点续跑在语义上更精细(步骤级而非时间级);只需补"回滚到第 N 步之前"的 UI 入口,不值得做全量快照 |
| 16 | **过程物化为活文档**(会话 → 可分享的报告页) | Claude Code artifacts | 高 | 中 | 已有 `reportCard` + 导出 md/json/sh,方向一致;远期可做静态 HTML 导出 |

**不建议抄的**:Operator Watch Mode(敏感站点强制在场)——本项目是本机科学计算,无第三方站点风险,反而应该反向学习(长任务鼓励离开,回来 `reattach`,这条我们已有);Manus 全屏实况视频流——成本高且对确定性流水线信息增量小,dock 的终端 tab 文本流已覆盖。

---

## 10. 落地建议(映射到 prototype,零 npm 依赖)

以下按文件给出改动点;全部可用原生 DOM + 现有 `h()` 工具实现,不引入任何依赖,不需要新增后端事件类型(除注明外)。

### 10.1 `prototype/js/stream.js`(事件卡片)

**A. 工具聚合组(机制 #1)** — 新增 `toolGroup()`:

- `consume()` 遇到连续的只读类 `tool.start`(可按 `ev.verb` 或新增 `ev.kind: 'read'` 判别)时,不逐条 `toolCall()`,而是塞进一个 `<details class="toolgroup">`,summary 显示"探查了 N 项 · 共 M s",内部仍是完整的 toolCall 条目(展开可见全部)。
- 规则:**任何一条失败(exit≠0)→ 整组自动展开且组头标红**——沿用现有"成功折叠/失败展开"哲学(`finish()` 里 `exit === 0 ? el.open = false : el.open = true` 的组级版本)。
- 写操作、长任务步骤(`s1`–`s11`)永不进组,保持顶层内联——对齐 Cursor 密度分层:改变世界的操作永远可见。

**B. 失败卡三段式(#3)** — `failureCard()` 签名扩展 `{ what, why, next }`:

- 卡体固定三行:**发生了什么**(现 `detail`)/ **为什么**(失败分类 `failClass` 的中文解释 + 证据行,如"ERA5 服务 503,连续 3 次")/ **建议下一步**(现 `options` 按钮组,首个按钮即推荐动作,已有 `cost` 代价标注保留)。
- 日志入口分层保持现状(卡上"查看完整日志"→ 展开工具卡并滚动定位),这已与 Copilot 的"摘要 → 会话日志 → 原始输出"层级一致。

**C. 审批卡(#5、#6)** — `askApproval()` 两处小改:

- 同族自动通过的 `note()` 尾部加内联"撤销记忆"链接:`S.autoApprove.delete(family)` + toast 确认;
- `rows` 约定新形态 `['参数', {before: 'α=0.4', after: 'α=0.6'}]`,渲染为 `旧值 → 新值`(新值着 `--stale` 色),供参数变更类审批用——`refineApprovalCard()` 服务端校准逻辑不变。

**D. 计划面板(#7)** — `planPanel()`:

- `set(n, 'r')` 时给该 `<li>` 加 `is-current` 类(左侧竖条高亮),并在条目上挂"跳转"点击 → `tools.get('s'+n)` 的卡片 `scrollIntoView`(app.js 传入回调);
- `items` 支持 `{group: '预处理'}` 分隔项,渲染成小节标题——11 步流水线按阶段分组后扫读成本大幅下降;
- 完成后**不移除面板**(Cursor 修过的 bug 反向印证:待办卡应留档)。

### 10.2 `prototype/js/app.js`(装配层 / 审批 / 排队)

**E. 排队消息(#2)** — `submit()` 的 busy 分支重写:

- 新增 `const queued = []`;`S.busy` 时提交 → 消息进 `queued`,输入框上方渲染排队 chip 行(复用 `attach-chip` 样式:序号 + 文本截断 + 删除钮,可点击编辑);
- 回合 `finally` 里 `setBusy(false)` 后检查队列,依次弹出自动 `submit()`(一次一条,与 Cursor 当前行为一致);
- Esc/停止按钮语义不变(急停),队列保留——同时渲染一条 `interventionEntry({mode:'queue', text:'1 条消息已排队,当前回合结束后发送'})` 留痕,与现有干预留痕体系统一。

**F. 通知(#4)** — 新增 ~30 行 `notify()`:

- 触发点三个:`consume()` 收到 `ask`/审批卡渲染时、`failureCard` 时、回合正常结束时;
- 仅当 `document.hidden` 才发:`new Notification('InSAR-Agent', {body: '第 8 步需要你的确认'})` + `document.title = '● ' + 原标题`;`visibilitychange` 回来时复原;
- 权限申请时机:第一次点击"确认执行"(长任务审批通过)时顺带 `Notification.requestPermission()`——用户意图明确的时刻,不在启动时骚扰。

**G. steering 提示(#11)** — `setBusy(true)` 时把 `el.input.placeholder` 换成"Agent 运行中——输入意见将排队,在步骤间生效(Esc 停止)",busy 结束复原。一行改动,把排队能力显性化。

### 10.3 `prototype/js/dock.js`(右侧面板联动)

**H. 反向联动(#9)**:

- `app.js` 维护 `artifactIndex: Map<path, toolId>`(在 `tool.end` 处理 `ev.artifacts` 时顺手登记);
- dock 文件面板与影像面板的条目加来源徽标("由第 6 步生成"),点击 → 关 dock 浮层(窄屏)→ `tools.get(id).el.scrollIntoView({block:'center'})` + 卡片短暂高亮(复用 `flashSteps` 的动画思路)。

**I. 会话分组(#8)**:

- `renderSessions()` 按 `tone` 分三组渲染:**需要你**(待审批/失败/阻塞,橙点)> **运行中**(蓝点)> 其余;组头小标题 + 计数;
- 需后端 `SESSIONS` 数据补一个状态字段,mock 先写死演示。

### 10.4 回放(#10,涉及少量后端)

- 后端把每回合 yield 的 NDJSON 事件原样追加写 `runs/<session>/events.ndjsonl`(driver 已双通道发布,加一个 file sink 即可);
- 前端"回放模式":读档后把事件数组交给现有 `consume()`,用 `sleep(dt)` 按录制时间差(或 4× 加速)重演——`consume()` 与真流同构的设计(app.js 头注释所言)在这里直接兑现,几乎零改动;
- 讲师场景:课堂演示不再依赖现场跑真任务,回放即教学素材。

### 10.5 明确不做

- 不引入 WebSocket/虚拟列表/diff 库:diff 对照用双列文本即可;事件卡片总量在单会话数百条级,原生 DOM 足够。
- 不做全环境快照回滚(见机制 #15 分析),"从断点继续 + 指纹级联"已是更适合科学计算的模型。
- 置信度分数暂缓(#12):先用规则位(首次配置/阈值 PENDING/数据缺失 → 审批必弹),等有真实运行历史再谈数字化置信。

---

## 附:来源索引(按产品)

- **Cursor**:cursor.com/changelog(2.0 / 3.0 / Agent To-dos / Collapsible tool calls)、cursor.com/docs/agent/overview.md、cursor.com/help/ai-features/agent、forum.cursor.com(#165292 工作组折叠官方答复、#55698 / #126214 排队语义、#163311 队列×Multitask、#157319 检查点)
- **Claude**:code.claude.com/docs/en/permission-modes、…/permissions、…/artifacts、…/claude-code-on-the-web;support.claude.com artifacts;claude.com/blog/artifacts、/build-artifacts;anthropic.com/news/claude-code-on-the-web;lucumr.pocoo.org/2025/12/17/what-is-plan-mode
- **GitHub Copilot**:github.blog/ai-and-ml/github-copilot/how-to-orchestrate-agents-using-mission-control、github.blog/changelog/2026-01-26-introducing-the-agents-tab、docs.github.com …/manage-and-track-agents、github.com/githubnext/copilot-workspace-user-manual(overview.md / vscode.md)、GitHub Next 落幕公告(gist.github.com/idan)、GitHub Checkout Mission Control 视频
- **Devin**:cognition.ai/blog/devin-2、docs.devin.ai(devin-session-tools / agent-command-center / spaces / integrations/slack / release-notes 2025・2026)、devin.ai/blog/windsurf-2-0
- **OpenAI**:openai.com/index/introducing-chatgpt-agent、help.openai.com/en/articles/11752874、cdn.openai.com Operator System Card & ChatGPT Agent System Card、openai.com/index/introducing-the-codex-app、theverge.com 2024-10-04 Canvas
- **Manus**(第三方交叉印证):spectrumailab.com 上手指南、aisharenet.com 交互设计分析、toolchase.com 指南、sidsaladi.substack.com
- **Replit**:replit.com/blog(introducing-agent-3 / automated-self-testing / whats-changed-agent3-to-agent4)、docs.replit.com(features/agent/plan-mode / core-concepts/agent/task-system / features/version-control/checkpoints-and-rollbacks / learn/design/canvas)
- **跨产品 UX 模式**:library.thiagopatriota.com "UX Patterns for Agentic AI: 16 Essentials for 2026"、zylos.ai "Agentic UX: Frontend Design Patterns 2026"、hatchworks.com "Agent UX Patterns"、onething.design、agentic-design.ai
