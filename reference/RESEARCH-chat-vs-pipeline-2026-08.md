# RESEARCH:聊天框 vs 流水线面板 —— 双平面交互调研与改进方案(2026-08)

- **日期**:2026-08-13
- **动机**:用户(地质研究者,非程序员)的根本疑问 ——「流水线是什么,聊天框是什么,两个冲突不?不应该通过自然语言进行吗,为什么还有一个流水线?」
- **方法**:现状代码走读(`prototype/index.html`、`prototype/js/app.js`、`prototype/js/dock.js`、`prototype/js/stream.js`、`prototype/js/pipelinerail.js`、`src/insar_agent/loop/driver.py`、`docs/AGENT-DESIGN.md` §7)+ 12 个产品的双平面实践调研 + HCI 文献。
- **相关既有报告**:`reference/RESEARCH-agent-ux-2026-08-12.md`(agent 交互机制)、`reference/RESEARCH-workflow-ui-2026-08-12.md`(流水线面板信息密度,已落地为 `pipelinerail.js`)。本报告聚焦**两个平面之间的关系与用户心智模型**,不重复上述内容。

---

## TL;DR

1. **用户的疑问不是功能问题,是说明问题。** 代码里两平面联动机制已相当完备(面板改参→聊天留痕、审批卡、干预回执、影响预览),但**没有任何一处向用户解释这套分工**——首访 hero 只说「自然语言驱动」,右侧面板从头到尾没有自我介绍。
2. 调研的 12 个产品(Devin、Manus、Replit、Cursor、Copilot Workspace、LangGraph Agent Inbox、OpenHands、ComfyUI、KNIME、Seqera/Nextflow、Prefect/Dagster、Galaxy)**无一例外都是双平面**:对话负责「意图、解释、审批」,结构化视图负责「状态、参数、证据」。纯聊天的 agent 产品在市场上已不存在。
3. 业界共识的联动模式有 8 个:计划预览卡、审批门、干预回执、进度回流、深链跳转、影响预览、来源留痕、会话回放。本项目已有 6 个,缺「首访引导」与「计划变更 diff」。
4. 科学计算场景下参数必须结构化的理由是硬的:**精确性**(数值/量纲/枚举无法用散文可靠传达)、**审计**(论文方法章节要精确值)、**复现**(指纹/provenance 要求键值对)、**代价不对称**(改错一个参数 = 小时级重跑)。这不是审美选择。
5. 自然语言的正确角色:承载**意图**(要研究什么)、**解释**(为什么失效/降级/拦停)、**分诊**(失败后怎么办)、**查询**(到哪了)、**澄清**(补充信息)。HCI 文献称纯 prompt 的缺陷为「表述障碍 articulation barrier」(NN/g),混合界面是共识解法。
6. 最关键的一个产品级缺口:**聊天↔流水线的双向等价性没有闭环**。面板操作会回流到聊天(已实现),但反方向——在聊天里说「把第 6 步换成 snaphu_smooth」——后端意图识别只认「场景」,参数级指令无法从自然语言到达 `SET_METHOD/SET_PARAMS` 动作队列。用户被迫学会「这类话要去面板点」,这正是困惑的来源之一。
7. 反面教材同样重要:GitHub Copilot Workspace 把结构化平面做到极致(Task→Spec→Plan→Code 全部可编辑)却**低估了聊天**,其负责人复盘承认「不够拥抱 chat 是技术性败因之一」。两平面是互补,不是谁取代谁。
8. 给本项目 13 条建议(§4):P0 四条全部是「说明层」而非新功能——首访双平面引导卡、聊天能力边界提示、NL 参数指令闭环、流水线面板自我介绍;P1 五条补联动细节;P2 四条锦上添花。
9. §5 是一页可直接放进产品帮助的中文用户解释稿(通俗类比:聊天 = 和研究助理说话,流水线 = 助理桌上那本实验记录本)。

---

## 1. 问题与现状

### 1.1 把用户的疑问拆开

用户一句话里其实是四个问题,每个都有确定答案:

| 疑问 | 答案(产品应当传达的心智模型) |
|---|---|
| 流水线是什么? | InSAR 处理这件事**客观上就是 11 个有依赖关系的步骤**(数据获取→配准→干涉→解缠→时序反演→…)。面板不是软件发明的概念,是学科事实的忠实呈现;它同时是「将要执行什么」的**合同**和「执行到哪了」的**仪表盘**。 |
| 聊天框是什么? | 你和 Agent 的**唯一对话入口**:说研究意图、看它的解释、批准或否决它的动作、问进度、问原因。 |
| 两个冲突吗? | 不冲突。它们是**同一份执行状态的两个视角**:聊天按时间轴回答「发生了什么」,面板按空间快照回答「现在是什么状态」(`docs/AGENT-DESIGN.md` §7.1 的原话)。在任何一边做的操作,另一边都会同步反映。 |
| 为什么不全用自然语言? | 自然语言到「跑一次三小时的科学计算」之间必须有一个**可核对、可精确修改、可留证据**的中间层。说「滤波强一点」无法写进论文;`filter_strength: 0.5→0.6` 可以。详见 §3。 |

### 1.2 现状盘点:两平面联动机制已有哪些(代码证据)

走读结论:**机制层完成度远高于用户感知**。已实现的联动:

| # | 机制 | 方向 | 位置 |
|---|---|---|---|
| 1 | 计划面板(`执行计划` N/11,随执行打勾) | 聊天内嵌结构化 | `prototype/js/stream.js:412` `planPanel` |
| 2 | 审批卡(步骤/命令/耗时区间/覆写清单/花费/可逆性 + 确认/换方法/带理由取消) | 聊天内嵌结构化 | `stream.js:459` `askApproval`、`app.js:1265` `askRerun` |
| 3 | 审批卡影响集权威化(本地估算先渲染,服务端指纹推导到达后原位更新) | 后端→聊天 | `app.js:1243` `refineApprovalCard`、`/api/impact` |
| 4 | 面板改方法/参数 → 聊天完整回执(旧值→新值、指纹重算、STALE 级联清单、10 秒撤销链接) | **面板→聊天** | `app.js:1109` `applyMethod`、`app.js:1190` `explainInvalidation`、`app.js:1150` `makeUndoLink` |
| 5 | 运行中干预回执(「你在第 7 步运行中将第 6 步方法改为 X → 已排入队列」) | 面板→聊天 | `app.js:1131` `interventionDuringRun`、`stream.js:757` `interventionEntry` |
| 6 | 重跑影响确认卡(Airflow Clear 式:列全部将重跑步骤+预估,默认勾必需集) | 面板内确认 | `dock.js:203` `openRerun`、`pipelinerail.js` `openImpactCard` |
| 7 | 失效原因 popover(五类闭集)+ 状态摘要条 + 依赖轨道 | 面板自解释 | `pipelinerail.js`(源头 Dagster `staleStatusCauses`) |
| 8 | 结果卡产物行 → 文件/影像面板深链;审批后 `Dock.flashSteps` 高亮受影响步骤 | 聊天→面板 | `app.js:885`、`dock.js:1577` `flashSteps`、`gotoStep` |
| 9 | 后端干预动作队列(SET_METHOD/SET_PARAMS/RESET/SKIP/KILL/PAUSE,步间检查点消费、必留痕) | 引擎层 | `src/insar_agent/loop/driver.py:240,728`、`POST /api/actions` |
| 10 | degrade/gate_stop/reattach 事件卡(降级代价、质量门拦停原因写进聊天) | 引擎→聊天 | `app.js:842-869`、`stream.js:661,687,740` |

### 1.3 困惑根因:五个缝隙

机制齐全但用户仍问「为什么有两个」,说明缝隙在**说明层与闭环完整性**:

- **缝隙 1|首访无双平面引导。** hero(`stream.js:126`)只有一句「自然语言驱动 InSAR 处理流水线」+ 三个示例 chip。没有一个字解释右侧 Dock 是什么、两边什么关系。empty-state 研究的结论是:空白容器不是中性的,它直接损害信心与可发现性(NN/g、Setproduct);AI 产品首访应「展示一个已完成的例子 + 给动词 + 暴露边界」(DesignersForest)。
- **缝隙 2|聊天能力边界不可见。** 输入框 placeholder 只有一个「描述研究任务」例子(`index.html:101`)。用户不知道聊天还能:问进度(「到哪了」§7.8 已支持输入框常开)、问原因、下参数指令、否决方案。「能做什么」靠猜,「不能做什么」(不能跳过质量门、不能凭空造数据)更没人说。
- **缝隙 3|11 步模板在任何对话之前就完整存在。** 面板启动即渲染全部 11 步(含演示参数),看起来像一个**与聊天竞争的第二操作入口**,而不是「聊天产出的结果」。用户自然会问「我该用哪个」。对照:Devin/Manus/Replit 的结构化视图都是**任务开始后**由计划生成的,空态时明确写「等待任务」。
- **缝隙 4|NL→参数级操作不闭环。** `brain.intent`(`driver.py:194`)只识别「场景」(区域/时间/链型),识别失败走 need_form 补充表单。「把第 6 步解缠方法换成 snaphu_smooth」这类话**无法从聊天到达** `SET_METHOD` 动作队列——尽管队列本身(`driver.py:240`)和面板入口都已存在。于是「不应该通过自然语言进行吗?」这个问题在当前实现里的诚实答案是「这类操作还真不行」,与产品自我介绍(自然语言驱动)矛盾。
- **缝隙 5|计划变更没有 diff。** `consume()` 收到新 `plan` 事件时直接新建一个 `planPanel`(`app.js:819`),旧计划留在滚动历史里。重规划/降级后,用户看不到「计划哪里变了」——只能自己对比两张卡。对照:Copilot Workspace 的 plan 可局部再生成并标记变化;Replit 的任务卡有明确的 Revise plan 流程。

---

## 2. 业界调研:12 个产品的双平面实践

体例:**定位 | 对话平面职责 | 结构化平面职责 | 联动机制 | 对本项目的启示**。

### 2.1 Devin(Cognition)—— 计划审批与「按计划步骤分组」的进度回流

- **定位**:自主软件工程师,chat 左 + workspace 右(Planner/Shell/Editor/Browser/Desktop 多 tab,宽度可拖)。
- **对话平面**:接任务、澄清、给反馈、随时打断纠偏。
- **结构化平面**:Interactive Planner 秒级给出初始计划(带代码引用,可点击**深链**进 IDE 核对);Progress tab 把 shell 命令、文件编辑、浏览器动作**按其所服务的计划步骤分组**;Detailed View 可用方向键逐步回放。
- **联动机制**:① 计划默认等 30 秒反馈后自动执行,复杂任务可点「Wait for my approval」变成硬审批门;② 会话页四个锚点:Task、Plan、PR、Summary;③ 计划里的引用深链到证据(文件/代码行)。
- **启示**:审批门要**可调节松紧**(默认软门+可选硬门)——本项目审批卡已有「同指纹族不再询问」,方向一致;「动作按计划步骤分组」正是轨迹流 tool_call 卡与 planPanel 的关系,可考虑在 tool_call 卡上标注所属步骤并可点击跳到计划行。

### 2.2 Manus —— todo.md 与会话回放

- **定位**:通用自主 agent。左侧对话,右上「Manus 的电脑」(实时看它开网页/跑终端),右下任务进度面板。
- **对话平面**:接任务、汇报、要求改向。
- **结构化平面**:把任务分解为 `todo.md`(Markdown 复选框),执行中实时打勾——**计划本身是一份用户可读的文件**;全会话可回放(replay),任何一步决策可回看。
- **联动机制**:进度面板与对话同屏常驻;回放让「它为什么这么做」可事后审计。
- **启示**:本项目「轨迹」面板(Lab Notebook,`AGENT-DESIGN` §7.2 面板 7)+ SQLite 事件流天然具备回放素材,P2 可做「按事件 id 回放本次 run」;`todo.md` 的启发是**计划要像文件一样可导出**——本项目已有 methods.md/run.sh 导出,可补「计划快照」。

### 2.3 Replit Agent —— Plan/Build 双模式与任务看板

- **定位**:面向非程序员的应用生成 agent。
- **对话平面**:Plan 模式只聊不改代码(brainstorm、把需求拆成任务清单);Build 模式执行。
- **结构化平面**:任务看板四列 Drafts/Active/Ready/Done;每个任务卡可「View plan」看细节;每完成一个任务自动打 checkpoint(可整体回滚,含数据库状态)。
- **联动机制**:① 计划审批是**显式按钮**「Start building」,或继续聊天改计划(Revise plan);② 任务完成后展示工作日志+测试结果+预览,用户决定 Apply/Dismiss——**先隔离执行,审阅后合入**;③ 看板卡片可配置 Auto-approve/Auto-apply(松紧可调)。
- **启示**:「计划审批 = 从聊天到执行的唯一闸门」这个叙事非常清晰,用户从不困惑两个平面谁说了算;本项目审批卡已是这个闸门,但**没有向用户宣告这一地位**(建议 R2)。

### 2.4 Cursor —— Plan Mode:聊天产出「可编辑的计划文件」

- **定位**:IDE agent。
- **对话平面**:澄清问题、研究代码库、讨论方案。
- **结构化平面**:计划以 Markdown 文件呈现(含文件路径、代码引用、todo 清单),**可直接编辑、可存入仓库**,点 Build 才执行;复杂任务时系统**主动建议**进入 Plan Mode。
- **启示**:计划是「聊天和执行之间的中间产物」,而且**用户拥有它**(可改、可存档)。本项目 planPanel 是只读的;P1 可让专家模式下计划条目可否决/重排(等价于面板 SKIP/方法切换的另一入口)。

### 2.5 GitHub Copilot Workspace —— 结构化到极致的反面教训

- **定位**(已并入 Copilot Coding Agent):Issue → Spec(现状/目标两列 bullet)→ Plan(文件级步骤清单)→ Code(diff),**每一层都可编辑、可局部再生成**。
- **教训**:其核心成员 Don Syme 复盘承认两个败因:没做实证/修复闭环,以及「**我们太不愿意拥抱 chat**——现代 vibe coding 用简单聊天流反而更省用户注意力」。结构化平面做得再精致,若把它当成**主要输入方式**,交互成本会压垮用户。
- **启示**:本项目方向(聊天为主入口、面板为核对/精修层)是对的;要警惕的是反向滑坡——**不要**为了「结构化正确性」把用户在聊天里能一句话说完的事,变成必须去面板点五下。

### 2.6 LangChain Agent Inbox / LangGraph —— 干预的类型系统

- **定位**:LangGraph 的 human-in-the-loop 基础设施。agent 执行到需要人的地方调 `interrupt()` 暂停,持久化状态,等人回复后恢复。
- **结构化平面**:Gmail 式收件箱列出所有待处理 interrupt;每个 interrupt 声明允许的回应类型:`accept / edit / respond / ignore`(`HumanInterruptConfig` 按动作类型收窄,如「付款可 accept 不可 edit,内容生成可 edit 不可 ignore」)。
- **对话平面**:`respond` 类型就是自由文本回聊天。
- **启示**:干预是**带类型契约的结构化事件**,不是散文。本项目审批卡的三分型(参数卡/作业卡/覆写卡,`AGENT-DESIGN` §7.6)与此同构;可补的是把「取消理由回传 Brain」(已有 UI)接到后端形成 `respond` 类型闭环。

### 2.7 OpenHands(原 OpenDevin)—— 事件流是两平面共同的地基

- **定位**:开源软件工程 agent。左聊天 + 右 workspace(终端/编辑器/浏览器)。
- **架构**:一切交互是 `EventStream` 上的 Action/Observation 事件,**append-only、可回放、可断线重连**;成本/预算随 stats 事件常态推流到 UI。
- **启示**:本项目 SSE + SQLite 事件自增 id + 重连去重(`AGENT-DESIGN` §7.8 reattach 三道校验)已对齐该模式。值得强调:**两平面不冲突的工程根基,就是它们消费同一条事件流**——这句话应该原样讲给用户听(见 §5)。

### 2.8 ComfyUI —— 没有聊天的极端:结构化平面自身的价值上限

- **定位**:Stable Diffusion 节点图工作流工具,**没有对话平面**也活得很好。
- **要点**:① 整个工作流(节点、参数、种子)以 JSON 嵌入生成的 PNG 元数据,拖回画布即完整复原——**产物即配方**;② 只重算图中发生变化的子图(与本项目「指纹未变直接跳过」完全同构);③ 参数散落在节点里,找「the prompt」需要图遍历——结构化不等于易读,**面向人的摘要仍然必要**。
- **启示**:证明了结构化平面对「可复现的生成任务」是**自足的**;但它的用户是愿意学节点的技术人群。本项目用户是地质研究者——聊天平面承担的正是 ComfyUI 不管的「不想学节点的人」。两平面各自的存在理由在这个对照里最清楚。

### 2.9 KNIME(K-AI)—— 聊天作为画布的编辑器 + 显式授权

- **定位**:可视化数据分析工作流平台,K-AI 是其聊天助手。
- **对话平面**:Q&A 模式答疑;Build 模式**直接在画布上放置并连接节点**——聊天的输出物是结构化工作流,不是文本。
- **授权模型**:K-AI 默认只见列名/类型;要读真实数据、要执行节点,**逐项弹权限**,选择可按 workflow 记忆、可重置。
- **启示**:「聊天改画布」方向与本项目缝隙 4 的补法一致:NL 指令 → 结构化动作(SET_METHOD)→ 面板可见变化 + 聊天留痕。授权按对象记忆(而非全局开关)对应本项目「审批缓存按参数指纹族」。

### 2.10 Nextflow / Seqera Platform —— 科学流水线的参数表单正统

- **定位**:生信流水线的发起与监控平台(前身 Nextflow Tower)。
- **结构化平面**:`nextflow_schema.json`(参数名/类型/说明/校验)→ 平台**自动渲染参数表单**;Runs 页给出命令行、**精确参数集**(官方文档明说「这是为了复现上次运行」)、完整配置、逐 process 进度、每 task 日志;失败可 resume(缓存续跑,只跑失败与未跑的);任意 run 可「Save as pipeline」变成可再发起的模板。
- **对话平面**:Seqera AI 辅助写流水线/排障,是**辅助角色**。
- **启示**:这是与 InSAR 最同构的场景(长时、批量、要复现)。它的参数呈现三形态(表单 / 原始 JSON/YAML / 上传文件)值得抄:本项目面板参数表单之外,P2 可加「以 JSON 查看/导出本步参数」。「Save run as pipeline」对应本项目的 fork/repro-bundle,概念上可向用户表述为「把这次跑通的配置存成模板」。

### 2.11 Prefect / Dagster —— 资产血缘与「新鲜度」语言

- **定位**:数据编排平台,无对话平面(近年才加 AI 辅助)。
- **要点**:Dagster 以「软件定义资产」为中心,UI 直接回答「这个数据从哪来、还新鲜吗、改了上游影响谁」(`staleStatusCauses` 已被本项目 `pipelinerail.js` 借鉴);支持 retry from failure。Prefect 用 `@materialize` 从实际运行反推资产图。
- **启示**:失效/血缘的**用户语言**(「上游重跑了」「工具版本变了」)本项目五类闭集已对齐;Dagster 把「资产视角」和「运行视角」做成两个页签——本项目「流水线(运行视角)」与「文件/审计(资产视角)」的分工同构,可在面板标题里点破这层关系帮助理解。

### 2.12 Galaxy —— 30 年科学复现 UI 的答案:历史面板 + 一键原样重跑

- **定位**:生信领域最老牌的「非程序员科学计算平台」,论文引用数万。
- **结构化平面**:左工具栏(每个工具一张参数表单)+ 右**历史面板**——每一步执行自动成为历史条目,记录工具版本、全部参数、输入输出;条目上的 ⟳「Run this job again」**原样重填当年的表单**(官方称之为「研究的 wayback machine」);从历史可一键提取成 workflow 复跑到新数据。
- **对话平面**:无(近年在加)。它证明:**对科学用户,参数表单+自动实验记录本身就是产品**。
- **启示**:本项目「审计面板 + provenance.json + 等价命令」正是 Galaxy 路线;给用户讲流水线面板时,「自动实验记录本」是地质研究者最容易共鸣的类比(§5 采用)。

### 2.13 共性模式总结

| 联动模式 | 代表产品 | 本项目现状 |
|---|---|---|
| ① 计划预览卡 + 审批门(聊天里出结构化计划,批准才执行) | Devin、Replit、Cursor、Copilot Workspace | ✅ planPanel + askApproval;缺「宣告闸门地位」的文案 |
| ② 干预带类型(accept/edit/respond/ignore,按动作收窄) | Agent Inbox、KNIME | ✅ 审批卡三分型;「理由回传」后端闭环待接 |
| ③ 面板操作 → 聊天回执(留痕+可撤销) | Devin(动作日志)、本项目独有撤销窗口 | ✅ explainInvalidation + makeUndoLink |
| ④ 进度回流(结构化状态变化推回对话轴) | Devin Progress、Manus todo.md、Replit 看板 | ✅ planPanel.set / step.start/end |
| ⑤ 深链跳转(聊天引用 ↔ 面板对象互跳) | Devin citations、本项目 flashSteps | ◐ 聊天→面板已有;provenance 树→流水线步骤未接(§7.3 待补项) |
| ⑥ 影响预览(执行前列受影响集与代价) | Airflow Clear、Dagster、Seqera resume | ✅ openImpactCard + /api/impact |
| ⑦ 首访引导 / 空态自我介绍 | Devin(预置任务卡)、Replit(模式说明) | ❌ **缺失——困惑主因** |
| ⑧ 计划变更 diff / 回放 | Copilot Workspace(局部再生成)、Manus(replay) | ❌ plan 事件整卡重建,无 diff;回放未做 |

---

## 3. 科学计算场景的特殊性:为什么参数必须结构化,语言承载什么

### 3.1 参数级操作必须结构化的四个硬理由

1. **精确性与量纲。** `corr_threshold 0.85→0.80`、`ref_point (35.61, -117.55)`、枚举 `snaphu_mcf|snaphu_smooth` ——这些值差 0.01 结果就不同。自然语言表达数值天然有损(「稍微放宽一点」是多少?),HCI 称此为**表述障碍 articulation barrier**(NN/g):用户必须把意图序列化成散文、再赌系统能无损解压回参数。表单/下拉框以「认得出」代替「想得起」,把猜词变成选择。
2. **审计。** 论文方法章节、审稿人质询、教学复盘都要求「当时到底用了什么值」。Galaxy 把每次执行的完整表单永久留档并可原样重开;Seqera 的 run 页直接列 Parameters 页签并注明「用于复现上次运行」。本项目的 provenance.json / methods.md 导出走的同一条路——其源头数据只可能来自结构化存储,不可能来自聊天记录里的散文。
3. **复现与失效判定。** 「同输入+同参数+同版本 ⇒ 同输出」是指纹系统(三段哈希、STALE 级联)的前提,而指纹只能算在**规范化的键值对**上。ComfyUI 把整个参数图嵌进 PNG、只重算变更子图,与本项目「指纹未变直接跳过」是同一个思想在两个领域的实现。
4. **代价不对称。** InSAR 单步小时级(`AGENT-DESIGN` §1.1 五个数量级),改错参数的代价是几小时白跑。所以任何参数变更必须先回答「会波及谁、重跑多久」——这就是影响预览卡存在的理由,而影响集只能从依赖图+指纹推导,自然语言给不出。

### 3.2 自然语言适合承载什么

| 角色 | 例子 | 本项目对应 |
|---|---|---|
| **意图** | 「分析玉树冻土 2020–2023 的 SBAS 时序形变」→ 场景/区域/时间/链型 | `brain.intent` → scenario → make_plan |
| **解释** | 为什么第 7 步失效了;降级 ERA5→高程相关的证据代价是什么 | explainInvalidation、degradeEntry、gateStopEntry |
| **分诊** | 失败后「换账号/降级/改参数/看日志」四选一的建议与理由 | failureCard 处置按钮 |
| **查询** | 「到哪一步了」「还要多久」 | §7.8 输入框运行中保持可用 |
| **澄清** | 意图缺区域/时间时的补充表单 | need_form → ask 事件 |
| **否决与理由** | 「取消,因为我想先只跑前 5 步」——理由回传给 Brain 调整方案 | askApproval 的 reason 动作(UI 已有,后端闭环待接) |
| **参数指令的入口**(而非存储) | 「把第 6 步换成 snaphu_smooth」→ 解析成 SET_METHOD,**落到结构化队列,回执照发** | 缝隙 4,建议 R3 |

### 3.3 一条设计公理(建议写进设计文档)

> **聊天说「要什么、为什么」;面板说「具体是什么、现在怎样」。两个平面操作的是同一台状态机、消费同一条事件流;任何一边的操作都在另一边留下痕迹。**

推论:① 任何面板操作必须在聊天留回执(已做到);② 任何聊天指令的结构化后果必须在面板可见(flashSteps 已有,NL 参数指令待补);③ 永远不要求用户「必须去另一边才能完成当前的事」——两边都能做,只是各有擅长。

---

## 4. 改进建议清单

体例:**问题 → 建议 → 预期改动位置 → 优先级**。P0 = 直接回应用户困惑,均为轻量改动;P1 = 补齐联动;P2 = 增强。

### P0(说明层,先做这四条,用户的疑问即消解大半)

- **R1|首访双平面引导卡**
  - 问题:缝隙 1,首访没人解释两个平面是什么关系(困惑的直接来源)。
  - 建议:hero 增加一张三格「工作方式」卡(纯静态,不新增交互):`① 你在下方说出研究意图 → ② 我把它排成右侧的 11 步流水线并给出计划,经你确认才执行 → ③ 执行中两边同步:聊天记录每一步,面板随时可核对与修改参数`。卡尾一行:「聊天和面板改的是同一份配置,任何一边的操作都会在另一边留下记录。」同时把 §5 的解释稿挂到 `?` 快捷键帮助浮层。
  - 位置:`prototype/js/stream.js` `renderHero`(hero 卡);`prototype/js/a11y.js`(帮助浮层加「聊天与流水线」一节);文案取自本报告 §5。
  - 优先级:**P0**。
- **R2|聊天能力边界提示(starter verbs 分组)**
  - 问题:缝隙 2,用户不知道聊天能做什么、不能做什么。
  - 建议:hero 示例 chip 从「三个研究任务」扩为三组各 2 条:**发起**(现有示例)、**干预**(「把第 6 步解缠换成 snaphu_smooth」「第 8 步对流层校正改用 ERA5」)、**查询**(「现在到哪一步了」「为什么第 9 步失效了」)。chip 点击只预填输入框不自动发送(业界共识)。placeholder 轮换展示三类例句。同时在引导卡写明边界:「我不会跳过质量门,不会在没有你确认时执行长任务或覆写产物。」
  - 位置:`stream.js` `renderHero` prompts 数组;`index.html:101` placeholder;轮换逻辑 `app.js` `watchComposer` 附近。
  - 优先级:**P0**(注意:「干预」组 chip 依赖 R3 落地,未落地前该组标「即将支持」或后置)。
- **R3|NL 参数指令闭环(chat→pipeline 补完)**
  - 问题:缝隙 4,「把第 6 步换成 X」在聊天里说了没用,与「自然语言驱动」的自我介绍矛盾。
  - 建议:`brain.intent` 之外增加一类**参数指令意图**:轻量规则/LLM 解析「第 N 步 + 方法名/参数键值」→ 直接入 `SET_METHOD/SET_PARAMS` 动作队列(复用 `POST /api/actions` 语义与步间检查点消费纪律),前端走**与面板完全相同的回执路径**(explainInvalidation + Dock.flashSteps 高亮该步)。解析不确定时降级为澄清问句(「你是想把第 6 步解缠方法改为 snaphu_smooth 吗?」+ 确认按钮),不许静默猜。方法名支持别名表(用户说「平滑解缠」也能命中)。
  - 位置:`src/insar_agent/loop/driver.py`(意图分支)、Brain 层意图解析、`prototype/js/app.js` `consume`(新事件类型或复用现有 note+flash)。
  - 优先级:**P0**(工程量最大的一条,但它让「两平面等价」从口号变成事实)。
- **R4|流水线面板自我介绍(空态 + 常驻一行)**
  - 问题:缝隙 3,11 步模板先于任何任务存在,像竞争入口。
  - 建议:面板顶部(`处理流水线 · 11 步` 标题下)常驻一行浅色说明:「InSAR 处理的固定 11 步。由你的对话生成配置,在这里核对状态、修改方法与参数——所有修改会记入左侧对话。」未发起任何任务时,步骤列表整体降透明度并盖一条空态提示:「尚未开始——在左侧描述研究任务后,这里会亮起来。」演示参数行已有「示意值」脚注,保持。
  - 位置:`prototype/js/dock.js` `pipelineView`(标题区 blurb + 空态类);`css/dock.css`。
  - 优先级:**P0**。

### P1(联动补齐)

- **R5|计划变更 diff 卡**
  - 问题:缝隙 5,重规划/降级后用户看不到计划哪里变了。
  - 建议:`plan` 事件携带 `revision` 时,planPanel 渲染 diff 模式:新增步 `+`绿、移除步 `−`红删除线、方法/参数变化的步标 `~` 并显示 `旧→新`;卡标题「执行计划(第 2 版,因 ERA5 降级调整)」。旧卡折叠为一行。
  - 位置:`stream.js` `planPanel`(diff 渲染);`loop/driver.py`(plan 事件带版本与变更原因);`app.js` `consume` case 'plan'。
  - 优先级:**P1**。
- **R6|回执带来源徽标**
  - 问题:面板操作与聊天指令的回执长得一样,用户学不到「两边等价」这件事。
  - 建议:explainInvalidation / interventionEntry 的聊天回执加小徽标「来自面板」/「来自对话」;面板 stepDetail 顶部反向显示最近一次变更来源(「2 分钟前经对话修改」)。这是把等价性**日常可见化**的最便宜手段。
  - 位置:`app.js` `applyMethod/applyParams`(传 source);`stream.js` 回执渲染;`dock.js` `stepDetail`。
  - 优先级:**P1**。
- **R7|审批卡「受影响步骤」→ 面板高亮联动**
  - 问题:审批卡列了「第 6–11 步受影响」,但用户要自己去面板找。
  - 建议:审批卡的「受影响/覆写」行可点击 → `Dock.flashSteps(ids)` + 依赖轨道高亮上游链;卡内加「在流水线面板中查看」链接(窄屏自动展开 Dock)。
  - 位置:`app.js` `askRerun` rows / `refineApprovalCard`;`dock.js` `flashSteps` 已有,补 `ensureDock()`。
  - 优先级:**P1**(改动极小)。
- **R8|provenance 树节点 → 流水线步骤跳转**
  - 问题:`AGENT-DESIGN` §7.3 第 4 条明确「点 provenance 树节点 → 跳流水线面板并选中该步」尚未实现,两面板间断链。
  - 建议:照设计文档补全,复用 `gotoStep`。
  - 位置:`dock.js` `auditView`。
  - 优先级:**P1**。
- **R9|取消理由回传 Brain 的后端闭环**
  - 问题:审批卡「取消(附理由)」目前只在前端留言(`app.js:1298` 注释自认「真实实现中理由会随审批结果回传」),Agent Inbox 的 `respond` 类型对应缺失。
  - 建议:审批结果(approve/reject+reason)作为结构化事件入库并进入下一回合 Brain 上下文,拒绝理由影响后续方案生成。
  - 位置:`src/insar_agent/api/app.py`(审批回传端点或复用 /api/message)、`loop/driver.py`、`core/store.py`。
  - 优先级:**P1**。

### P2(增强)

- **R10|把 §5 解释稿正式放进产品**:帮助浮层「聊天与流水线」一节 + 首次点击流水线面板时的一次性 coach mark(可关闭,记 localStorage)。位置:`a11y.js`、`dock.js`。
- **R11|计划快照导出**:与 methods.md/run.sh 并列,导出「本次计划(含版本历史)」——Manus todo.md 的可携带性。位置:`app.js` `doExport`、`/api` 导出族。
- **R12|运行回放**:基于事件自增 id 的「按时间轴回放本次 run」(轨迹面板加播放条)。教学场景价值高(讲师演示)。位置:`dock.js` traceView、`/api/trace`。
- **R13|向导/专家模式的双平面差异化**:向导模式下面板参数表单默认折叠(只读摘要 + 「修改需切换专家模式」提示),聊天承担更多;专家模式维持现状。降低新手误触面板的机会,也把「两种用户两种重心」讲清楚。位置:`app.js` modeSwitch、`dock.js` `stepDetail`。

---

## 5. 一页用户解释稿(放进产品帮助/首访引导)

> # 为什么这个软件既有「聊天」,又有「流水线」?
>
> **一句话:聊天是你和研究助理说话的地方,流水线是助理桌上那本实验记录本。**
>
> 想象你有一位很能干的研究助理。你对它说:「帮我分析 Ridgecrest 2019 年地震的地表形变。」——这就是**聊天框**的用途:你用平常说话的方式,告诉它你想研究什么、问它做到哪了、让它解释为什么这么做。你不需要记住任何命令和术语。
>
> 但是 InSAR 数据处理本身,天生就是**一串固定的工序**——找数据、配准、做干涉图、解缠、算时间序列……一共 11 步,一步接一步,就像洗照片有固定的冲印流程。右边的**流水线面板**,就是把这 11 步工序摊开给你看:每一步做没做完、用的什么方法、参数是多少、出了什么文件。
>
> **为什么不能只用聊天?** 因为科学计算讲究精确和留底:
> - 「滤波强一点」写不进论文,`滤波强度 0.5 → 0.6` 才写得进去;
> - 一步计算往往要跑几个小时,跑之前你得白纸黑字看清楚「将要执行什么、要多久、会覆盖什么」,确认了才开始;
> - 半年后审稿人问「你当时用的什么参数」,记录本翻开就有,聊天记录里的口语可靠不了。
>
> **为什么不能只用流水线?** 因为那样你就得自己学会每一步的术语和几十个参数——那是软件该干的活,不是你该干的。你说人话,它来翻译成工序。
>
> **两边会不会打架?** 不会。它们是**同一件事的两个窗口**:
> - 你在聊天里说「把第 6 步换个解缠方法」,面板上第 6 步会亮起来,标出哪些后续步骤需要重算;
> - 你在面板里改了一个参数,聊天里会自动记一笔「改了什么、影响哪几步、10 秒内可撤销」;
> - 所有正式执行,都要先经过聊天里的**确认卡**——列清楚步骤、耗时、会覆盖什么,你点「确认」它才动手。
>
> **平时怎么用?** 大多数时候你只需要聊天:说任务、看进度、回答它的提问。想核对细节、微调某个参数的时候,再看右边的流水线。你随时可以在聊天里问:
> - 「现在到哪一步了?」
> - 「为什么第 9 步标了失效?」
> - 「把对流层校正改用 ERA5。」
>
> **它不会做的事:** 不会不经你确认就开始几小时的计算或覆盖已有结果;不会跳过质量检查;结果不达标时它会停下来告诉你原因和选项,而不是硬着头皮往下跑。

---

## 6. 主要来源

**产品文档/一手资料**
- Devin:Interactive Planning(docs.devin.ai/work-with-devin/interactive-planning)、Session Tools(…/devin-session-tools)、2025 Release Notes(docs.devinenterprise.com/release-notes/2025)
- Manus:WorkOS《Introducing Manus》(workos.com/blog/introducing-manus-the-general-ai-agent)、besthub.dev 架构解析、kdjingpai.com 交互设计评述
- Replit Agent:Plan Mode(docs.replit.com/references/agent/plan-mode)、Task System(…/core-concepts/agent/task-system)、Task Board(…/features/agent/task-board)、Checkpoints(…/features/version-control/checkpoints-and-rollbacks)
- Cursor:Plan Mode 文档(cursor.com/docs/agent/plan-mode)、官方博客《Introducing Plan Mode》
- GitHub Copilot Workspace:用户手册 overview(github.com/githubnext/copilot-workspace-user-manual)、Don Syme 复盘《Copilot Workspace and the birth of Task-Oriented Programming》(dsyme.net, 2025-01)
- LangChain/LangGraph:Agent Inbox README(github.com/langchain-ai/agent-inbox)、Human-in-the-loop 中间件文档(docs.langchain.com/oss/python/langchain/human-in-the-loop)
- OpenHands:论文《OpenHands: An Open Platform for AI Software Developers as Generalist Agents》(arXiv:2407.16741)、DeepWiki Event-Driven Architecture
- ComfyUI:官方 README(workflow-in-PNG、变更子图重算)、numonic.ai PNG 元数据结构解析、eastondev workflow 复用指南
- KNIME:K-AI 扩展页(hub.knime.com)、Analytics Platform User Guide(docs.knime.com,授权模型)、官方博客
- Seqera/Nextflow:Launchpad 文档(docs.seqera.io/platform-cloud/launch/launchpad)、Run 监控文档、《Best Practices for Deploying Pipelines with Seqera Platform》(nextflow_schema.json)
- Prefect/Dagster:dagster.io 编排工具对比、prefect.io《Introducing Assets》、assets 文档
- Galaxy:《A user's guide to the virtual, automated, computing Lab Notebook in Galaxy》(galaxyproject.org, 2022)、Galaxy and Reusability(FAIR)、GTN 复现教程、Goecks et al. 2010(PMC2945788)

**HCI/设计**
- NN/g《Overcoming the Articulation Barrier in Generative AI Using Hybrid Interfaces》(nngroup.com/articles/ai-articulation-barrier)
- NN/g《GenUI In Real Life: Buttons and Checkboxes》(nngroup.com/articles/genui-buttons-and-checkboxes)
- IUI 2026 companion《Beyond the Conversational Paradigm: … Spectrum of Human-LLM Interaction》(dl.acm.org/doi/10.1145/3742414.3789234)
- designative.info《Beyond the Conversation Trap: Designing for Hybrid Human-Agent Interaction Modes》(2026-03)
- uxdesign.cc《The chat box isn't a UI paradigm — it's what shipped》(Norman 双鸿沟视角)
- DesignersForest《The death of the empty state in AI products》;Koder Design AI welcome/first-run screen pattern;zylos.ai《AI Agent Onboarding UX: First 5 Minutes》;setproduct.com empty state 指南

**内部**
- `docs/AGENT-DESIGN.md` §7(三区分工、审批三分型、干预留痕、四态);`prototype/js/{app,stream,dock,pipelinerail}.js`;`src/insar_agent/loop/driver.py`(意图/动作队列);`reference/RESEARCH-agent-ux-2026-08-12.md`、`reference/RESEARCH-workflow-ui-2026-08-12.md`
