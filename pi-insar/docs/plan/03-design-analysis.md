# 03 · 设计完备性分析(交付物 1,背景阅读)

> 本文件回答"现状哪里完备、哪里缺、为什么这么排期"。不含操作步骤;执行 AI 读完理解意图即可进入 `10-phase01-windows-baseline.md`。
> 本文写于第一轮排期(8 个 Phase);此后的完备性审计(`06-completeness-verdict.md`)又追加了工具面 Phase 06-08 与科学能力 Phase 10-15,总表以 `00-README.md` 为准。

## 1.0 一页结论

- **架构选型正确且已过半**:方案 B(pi 作外壳、Python 内核保留、insar_* 闭集工具)的"受约束边界层"与"科学内核层"已完整落地并有测试护航;缺的集中在"pi 用好用满"的外围三件事——**LLM 供应商没接**(pi 现在无法用 `workspace/llm.json` 的真实 key 驱动)、**Windows 启动路径没打通**(pi 未安装、启动器只有 bash 版、测试基建默认 Unix 路径)、**品牌换皮零实现**(无主题、无 InSAR 视觉)。
- **两个高价值工具缺口**:步级断点续跑(后端 `/api/resume` 已存在但未暴露为工具)和真实图件进 TUI(pi 工具结果原生支持 base64 图像,后端 `/api/figures` + `/api/artifact-file` 已存在)——都是"后端有、前端没接"的低风险高收益项。
- **一个设计过但没做的纪律件**:P0 结论 §3 提出的"free 模式动作顺带记账"(台账外观察日志)未实现——它不是 provenance,是回答"自由模式下 agent 都干了什么"的审计日志。
- **不需要动 pi 源码**:全部定制(供应商、主题、系统提示、工具、守卫、侧栏)都有官方扩展点,升级不背包袱。

## 1.1 方案 B 四层逐层核对

| 层 | 方案 B 要求 | 现状 | 判定 |
|---|---|---|---|
| **科学内核**(Python) | engines/core/runtime/audit/planner/report/registry 原样保留,五阶段执行器、参数级失效传播、证据阶梯、质量门、run_ok 双判定 | 未动;`workspace/realtest` 真实全链 done+audited 佐证 | ✅ 已实现(红线,保持) |
| **受约束边界** | LLM 只经 insar_* 闭集触达科学步骤;strict 模式禁自由 bash;APPEND_SYSTEM 注入使命与红线;技能文档 | 16 个 `insar_*` 工具 + `guard.ts` 纯函数闸门(fail-open 保 free 模式)+ `APPEND_SYSTEM.md`(<8KB 有测试)+ 1 操作技能 + 11 步技能 + 4 场景包,`sync-skills.mjs` 校验 | ✅ 已实现;缺口:resume/figures 工具(G4/G5)、free 模式台账外日志(G6) |
| **通用 Agent 层(pi)** | pi 作顶层进程,自由使用:安装/配置/扩展/供应商 | 扩展经 `-e` 源码直载 ✔(jiti 原生吃 TS,无构建步);`package.json` 有 `pi.extensions` 冗余入口 ✔;**pi 未全局安装**;**供应商未接 llm.json**(pi 无模型可用);`.pi/` 项目设置未建 | ⚠️ 半成品 |
| **UI 层** | pi TUI 为主界面(Q3 决议放弃旧 Web UI),侧栏监控 11 步×五阶段,InSAR 品牌 | `sidebar.ts` 轨道图 + footer 状态 ✔(纯渲染函数有金测);**主题/品牌零实现**;图件不可见 | ⚠️ 半成品 |

## 1.2 "pi 用得是否最优"逐项判定

- **安装方式**:最优 = `npm i -g @earendil-works/pi-coding-agent@0.84.2`(与 devDependency 同版本锚定)。不 fork、不从源码跑(pi-insar/reference/pi 只作阅读参考)。理由:全局安装是 pi 官方分发路径,锚定版本消除上游漂移;fork 会把 5 万行 TUI 维护成本背上身,违背"不重造轮子"的战略动机。Windows 前置:Node ≥ 22.19 + **Git Bash**(pi 的 bash 工具在 Windows 依赖它,官方 windows.md 明示)。
- **扩展加载**:现状 `-e <repo>/pi-insar/src/index.ts` 已是最优——源码直载零构建、改完即生效;`pi.extensions` 字段作为"若未来以包分发"的冗余,不冲突。
- **供应商配置**:现状是**最大缺口**。最优 = 扩展内 `pi.registerProvider("insar-llm", {...})`(custom-provider.md 官方 API),运行时读 `workspace/llm.json`。备选及否决理由:① 手工 `~/.pi/agent/models.json` ——密钥要复制第二份,与 llm.json 漂移,违背"唯一真源";② `$ENV_VAR` 引用——要求每个终端先手工 set,易忘且散落;③ pi 订阅登录——引入新密钥,违背硬约束。扩展内注册的关键优势:llm.json 改动即生效、key 只驻内存、`pi --list-models` 即可无成本验证注册。
- **主题/品牌**:最优 = 独立主题 JSON(51 必需 token,themes.md schema)+ 启动器 `--theme <绝对路径>` + `.pi/settings.json` 的 `"theme"` 字段选中。**不改 pi 源码、不覆盖内置 footer**(自定义 footer 会丢 token/成本显示,损失大于收益);品牌感由 主题配色 + 侧栏 `insar` 轨道 + footer 状态项承担。注意 pi **无 setTitle API**(已核实 extensions.md),终端标题不做。
- **系统提示注入**:`--append-system-prompt <文件路径>` 合法(已核实 args.ts:259 "Append text or file contents"),启动器现状正确,保持。
- **上游升级影响**:所有定制走官方扩展点(registerProvider/registerTool/registerFlag/registerCommand/on/ui.setWidget/ui.setStatus/--theme),升级时唯二需要人工核对的是 **主题 token 清单是否新增**(themes.md)与 **扩展 API 签名 typecheck**。见 `43-appendix-pi-upgrade.md`。

## 1.3 TUI 侧栏与 prototype/ Web UI 的关系

- pi TUI 是唯一主交互面(Q3 决议)。`prototype/` 仍由后端挂载,定位降级为"后端自带的只读观测台"(调试看图用),**不再投入开发**,也不删除(删除是无收益的破坏性动作,且其静态文件不影响 pi 拓扑)。
- 两者读同一后端、同一 SQLite,无状态冲突。TUI 看图的正解不是复活 Web UI,而是 `insar_view_figure` 工具把真实 run 产物图以 base64 内联进 pi 会话(pi 工具结果支持 `{type:"image"}` 内容块,已核实)。

## 1.4 远程会话 / pi-chat / MCP:均不做

- **RPC/远程会话(protocol/server/client)、pi-chat web**:不做。单机科研工作流没有远程多客户端需求;后端已有 SSE(`/api/events`)覆盖进程外观测。做了 = 维护第二个 UI,重蹈 prototype 覆辙。
- **MCP**:不做。pi 0.84.2 无一等 MCP 客户端(docs 无此项);更本质的是 insar_* 闭集 + guard 就是本项目的"受约束工具面",引 MCP 等于在闸门旁边开洞,与硬约束相抵。

## 1.5 已知缺口(gap)

| # | 缺口 | 危害 |
|---|---|---|
| G1 | pi 未安装;启动器只有 bash 版(PowerShell 双击不可用);测试基建默认 `/workspace` Unix 路径 | Windows 上整条 pi 链路跑不起来 |
| G2 | 供应商未接 `workspace/llm.json` | pi 无模型可用,只能靠外部订阅(引新密钥,违约) |
| G3 | 主题/品牌零实现 | 交付观感与"InSAR 风格"目标不符 |
| G4 | `/api/resume`(步级断点续跑)未暴露为工具 | 核心卖点"断点续跑"在 pi 里没有入口 |
| G5 | `/api/figures` 未暴露;真实图件在 TUI 不可见 | 质检/汇报必须切浏览器,链路断裂 |
| G6 | free 模式 pi 侧动作无台账外记录(P0 §3 设计过) | 自由模式的操作不可回放审计 |
| G7 | `insar_execute_run`/`resume` 无 onUpdate 流式进度 | 长任务期间 TUI 只有侧栏轮询,工具卡片静默 |
| G8 | Windows 测试 teardown EBUSY(SQLite -wal/-shm 句柄竞态) | vitest 每轮报错噪音,掩盖真实故障 |
| G9 | README Windows 环境变量文档用 bash `export` 语法 | 执行者照抄即失败 |
| G10 | strict 模式屏蔽 grep/ls/find 等只读内建 | 严格模式可用性下降(是否放宽有争议) |
| G11 | 无 `.pi/settings.json` 项目设置 | 每次会话要手选 provider/model/theme |
| G12 | pi 升级无章程 | 未来升级时主题/API 冲突无检查表 |

## 1.6 风险点

- **R1 pi 上游漂移**:0.84.2 已锚定(devDep + 全局同版本)。风险面:主题必需 token 增多(51 → N)、扩展 API 签名变化、CLI 旗标语义变化。处置:`43-appendix-pi-upgrade.md` 升级章程。
- **R2 Windows/WSL 双引擎**:MintPy 走本机 conda(`E:\miniforge3\envs\insar`),ISCE2 走 WSL。引擎选择由后端 `INSAR_ENGINE_PREFIX` 探测,pi 扩展完全无感——保持这个分层,任何引擎逻辑都不进 TS 层。
- **R3 测试隔离**:集成套件绑 8899 端口 + 临时 `INSAR_HOME`(mkdtemp),已隔离;残余风险是 8899 被占(处置:`INSAR_TEST_PORT` 覆盖)与 EBUSY(G8,Phase 01 修)。
- **R4 Git Bash 依赖与 MSYS 路径转换**:在 Git Bash 里执行 `scripts/insar-pi` 时,MSYS 会把 `/e/01所有项目/...` 形参自动转成 `E:\01所有项目\...` 传给原生 pi.cmd,一般正确;若出现路径错乱,设 `MSYS_NO_PATHCONV=1` 并改传 Windows 形态路径。PowerShell 启动器(Phase 01)绕开整个问题,是 Windows 首选。
- **R5 密钥面**:llm.json 已被 .gitignore 忽略;provider 注册后 key 仅驻内存。纪律:任何日志、错误信息、测试快照不得携带 `api_key` 值;文档只写字段名。
- **R6 重型计算**:真实 MintPy 全链是分钟级重活。执行 AI 在触发任何真实执行(新 run 或复跑 `real_ridgecrest.py`)前**必须先征得用户明确同意**,并低优先级启动。
- **R7 项目信任**:`.pi/settings.json` 只在项目被信任后生效,首次交互式启动 pi 会弹信任确认——这是预期行为,文档写明"选择信任"即可。

## 1.7 测试盲区

- T1 `index.ts` 工厂接线无直接测试(各组件有单测,可接受,不补)。
- T2 侧栏轮询的安装/停止(timer 生命周期)未测(P2,不阻塞)。
- T3 供应商注册无测试 → Phase 02 补(用真实 llm.json 的存在/缺失两分支,不造假配置)。
- T4 PowerShell 启动器无文本断言 → Phase 01 在 `skills.test.ts` 补(与 bash 版同款)。
- T5 真实 LLM 驱动的 pi 会话无法进 CI(密钥、成本)→ 以"真实走查手册 + 真实截图"为人工验收(Phase 03/16),**不做假会话录制**。

## 1.8 明确不做的"缺口"(与硬约束冲突,防返工)

- **X1 不新建任何静态假数据资产**:新测试的运行态数据一律来自"临时目录里真实拉起的后端"(既有 globalSetup 模式)或既有仓库资产;禁止把伪造的 run 状态/影像元数据/provenance 存成 fixture 文件。API 契约测试里的最小请求体与既有确定性种子属测试基建惯例,不在禁区;禁区是"以假数据充当科学样本或验收依据"。
- **X2 不做 mock LLM 的假会话录制回放**(验收走真 key 真会话)。
- **X3 pi-journal 不混入 provenance**:台账外日志与 run 账本物理分离(独立 NDJSON 文件),不参与证据阶梯,不为科学结论背书——否则等于伪造 provenance。
- **X4 不复活/重写 prototype Web UI**(Q3 已决)。
- **X5 不在 strict 模式为科学步骤开 bash 白名单**(G10 若放宽也只放只读内建,且默认**不做**,理由:strict 的语义承诺是"能落账的动作才可做",宽松一分,承诺就打折;free 模式已覆盖探索需求)。
- **X6 不引入新密钥**;不把 key 复制进任何新配置文件(llm.json 唯一真源)。
- **X7 不 fork/不改 pi 源码**(pi-insar/reference/pi 仅供阅读)。

## 1.9 "做到完美"工作清单(P0 必做 / P1 应做 / P2 可选)

| 级 | 项 | 一句话理由 | 对应文件 |
|---|---|---|---|
| P0 | Windows 基线:globalSetup 平台感知 + EBUSY 重试 + README 语法修正 + 现有改动收口提交 | 不修则 Windows 上测试常绿都做不到 | 10 |
| P0 | 安装 pi 0.84.2 + PowerShell 启动器 | pi 链路在开发机跑起来是一切验收前提 | 10 |
| P0 | 供应商接线(llm.json → registerProvider)+ `.pi/settings.json` 默认模型 | 没有它 pi 无法真实驱动,违背分支目标 | 11 |
| P0 | 真实数据后端启动脚本 + pi 真实走查手册 | 验收必须走真数据,入口要一键化 | 12 |
| P1 | insar-dark 主题 + 启动器 --theme + 主题测试 | "界面换皮成 InSAR 风格"是明确分支目标 | 13 |
| P1 | `insar_resume`/`insar_view_figure`/`insar_run_trace` 工具 | 断点续跑与真实图件是后端已有、前端未接的核心能力 | 20 |
| P1 | free 模式台账外日志(桥端点 + 扩展钩子) | P0 设计遗留项,补齐审计闭环 | 24 |
| P0 | 工具面补全:分析查询/导出 GIS/报告交付(16 → 31 工具) | 后端 77 端点只暴露 16 工具,科学价值困在 HTTP 里 | 21-23 |
| P0 | 科学能力扩展:后处理/校正/出图/预测/反演桥/场景包 | "全流程 InSAR Agent"的科学面,注册表驱动零架构改动 | 30-35 |
| P1 | 文档收口 + 全量最终验收 | 交付完成的可复核证明 | 40 |
| P2 | execute/resume 的 onUpdate 流式进度 | 体验项,依赖 pi 工具 onUpdate 语义,收益中等 | 不排期 |
| P2 | strict 只读内建白名单(grep/ls/find) | 语义有争议,默认不做(见 X5) | 不排期 |
| P2 | 侧栏轮询 timer 生命周期测试、会话自动命名 | 锦上添花 | 不排期 |
