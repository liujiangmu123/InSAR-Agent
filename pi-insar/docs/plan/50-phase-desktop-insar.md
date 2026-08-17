# 50 · Phase D — 桌面工业化:pi Desktop(源码壳)× InSAR 内核

> 目标:把 **pi Desktop 源码**(E:\SoftApp\pi-app,justhil/pi-app v0.5.7)做成工业级 InSAR 数据处理桌面。科学内核(Python 后端 + 31 个 insar_* 工具 + `.pi/` 项目适配)**已完成、不重做**;本 Phase 只做"壳接线 + 桌面体验 + 自打包"。
> 分工:本文件是规划(Fable);代码由 Grok 按 §7 切片执行。执行纪律沿用 `01-execution-rules.md` 三条红线;**真实 MintPy 全链等重型计算必须先获用户明确批准**,本计划所有验收默认走轻型读回面(既有 realtest audited run)。

## 0. 一页结论(勘察后的地基事实)

以下事实来自对壳源码与本仓库的实读(2026-08-15),是排期依据,执行前用 §2 各阶段的核对命令复核:

| # | 事实 | 出处 | 对计划的含义 |
|---|---|---|---|
| F1 | 壳 worker 经 `sdk.createAgentSessionServices({cwd, agentDir})` 创建会话,SDK 资源加载器自动吃项目 `.pi/`(extensions/skills/themes/settings.json) | `pi-app/src/worker/worker-runtime.ts` | InSAR 扩展、31 工具、insar-llm 供应商、默认模型**零壳改动**即进 Desktop,前提是在 Desktop 里打开本仓库为工作区并信任 |
| F2 | Desktop 的扩展 UI 桥里 `setWidget`/`setStatus`/`setFooter`/`setTitle` 全是 **no-op**(`notify` 有桥接) | `pi-app/src/worker/desktop-ui-bridge.ts` | CLI 的 TUI 侧栏(`pi-insar/src/sidebar.ts`)在 Desktop **不可见但无害**;流水线右栏必须走 adapter sidePanel 机制 → 这是唯一"必须改壳"的功能性缺口 |
| F3 | sidePanel 状态提供者注册表 `PROVIDERS` **只有 `workspace-trellis`**;IPC handler 已 `await resolveSidePanelState(...)`,支持异步 provider | `pi-app/src/main/side-panel-registry.ts`、`src/main/ipc/handlers/adapter-panels.ts` | 加一个通用 `http-json` provider 即可把 `GET /api/monitor` 拉进右栏,改动面一个文件 + schema 一个可选字段 |
| F4 | 渲染器闭集 `BY_COMPONENT = { workspace-tasks, generic-json }`;`generic-json`(GenericAdapterSidePanel)只在挂载时拉一次 + 手动"刷新"按钮,**无轮询** | `pi-app/src/renderer/src/features/side-panels/side-panel-registry.tsx`、`generic-adapter-side-panel.tsx` | 流水线监控需要 2-5s 级自动刷新 → 渲染侧要么给 generic-json 加 pollMs,要么新增专用面板组件(二选一,见 D2) |
| F5 | 适配器加载优先级:**项目 `.pi/desktop/adapters` > `~/.pi/desktop/adapters` > builtin**,按 match.names 整份覆盖 | `pi-app/src/extension-compat/adapter-loader.ts` | 本仓库 `.pi/desktop/adapters/insar.adapter.json`(31 工具已登记,尚无 sidePanel 字段)是正确挂点;加 sidePanel 字段属**只改本仓库** |
| F6 | 壳有完整 SDK 三态机制:builtin(随包 0.83.0)/ global(npm -g,现 0.84.2)/ user(userData 内独立安装),IPC `sdk.status/install/switch` + 设置页 UI,持久化在 userData/sdk/current.json | `pi-app/src/main/sdk-loader.ts`、`global-sdk-resolve.ts`、`ipc/handlers/pi-sdk.ts` | 0.83→0.84.2 对齐**不需要 fork、不需要先动壳的 package.json**:切 global 即可;详见 §5 |
| F7 | `GET /api/monitor` 需要 `session` 查询参数(缺省取该会话最新 run);会话列表在 `GET /api/sessions` | `src/insar_agent/api/bridge_router.py` | 右栏 provider 需要知道监控哪个 session → 后端加"最新会话"语义(改本仓库),壳 provider 保持通用 GET-JSON,不写死 InSAR 逻辑 |
| F8 | worker 是 `utilityProcess.fork(..., {stdio:'pipe'})`,继承 main 进程环境变量 | `pi-app/src/main/worker-manager-pool.ts` | dev 态经 `scripts/insar-pi-desktop.ps1` 设的 `INSAR_API_BASE` 能到达扩展;打包版无启动器 env 时扩展回退默认 `http://127.0.0.1:8873`,与后端默认端口一致 |
| F9 | `package:win` = icon:export + electron-vite build + electron-builder --win(NSIS + portable, x64);better-sqlite3 原生依赖,VS18 有 C1001 已知坑,本仓库已有 `scripts/fix-pi-desktop-sqlite.ps1` | `pi-app/package.json`、`electron-builder.yml` | 自打包路径现成,风险集中在原生模块重编与 asar 内 TS 扩展直载(D5 验证) |
| F10 | 壳自检命令:`npm run typecheck`(双 tsconfig)、`npm run test:unit`(vitest)、`npm run test:scripts`(契约)、`npm run test:e2e`(playwright) | `pi-app/package.json` | 每个改壳切片以 typecheck + test:unit 为最低门槛 |
| F11 | toolCard 是 **adapter 级**(不是 per-tool),模板闭集 default/list/media/tree/kv/hashline;insar 适配器现用 list + `$.output.text` | `pi-app/src/extension-compat/adapter-schema.ts`、`.pi/desktop/adapters/insar.adapter.json` | 不追求给 31 个工具各配卡片;图件内联依赖 pi 工具结果的 image 内容块在 Desktop 时间线的原生渲染(D1 实测,缺则登记为条件性壳改动 D2-c) |

## 1. 目标验收(什么叫"做完")

### 1.1 用户故事

用户双击启动 pi Desktop(dev 态经启动器;D5 后是自打包安装版),打开本仓库为工作区,**全程中文对话**完成:

```
数据接入 → 11 步核心流水线 → 分析步 20-28 → 出图 → AI 识图质检 → 报告/复现包
```

期间:右栏实时显示 11 步 × 五阶段流水线、证据级、free/strict 模式;每个报告数字可对回 provenance;strict 模式下自由 bash 被拦截并给出引导;重型计算(新的真实全链执行)在触发前要求用户明确批准。

### 1.2 验收剧本(最终一次性走完,轻型;基于 realtest 既有 audited run)

前置:窗口 A `pwsh scripts/insar-backend-real.ps1`;Desktop 打开本仓库工作区,模型为 insar-llm/deepseek-v4-flash-0731。

| # | 中文指令(示例) | 期望 |
|---|---|---|
| 1 | "后端还活着吗?列出现有会话" | `insar_health` ok;`insar_list_sessions` 见 real 会话 |
| 2 | "看看 real 会话最新 run 的状态和证据级" | 11 步 done、evidence=audited、simulated=false;右栏同步点亮 |
| 3 | "查震中那个点的时序" | `insar_timeseries_point` 数值与 `curl /api/timeseries-point` 逐位一致 |
| 4 | "把速度场图给我看看,并让 AI 评一下图的质量" | `insar_view_figure` 图在会话内可见;`insar_vision_qa` 返回结构化质检 |
| 5 | "基于这个 run 做掩膜后算区域平均速度,出一张图" | 分析 run(20→21→24→25)全 done;右栏显示分析 run 进度;数字可溯 |
| 6 | "预测这个点未来 6 个月形变,给不确定度" | 26/27 步;±1σ/±2σ + validity + 免责声明 |
| 7 | "切严格模式,然后帮我 ls 一下目录" | `/insar-mode strict` 生效;bash 被 guard 拦截并解释 |
| 8 | "出中文报告和复现包" | `insar_report` 数字对回 provenance;`insar_repro_bundle` zip + MANIFEST 哈希可验 |
| 9 | (抽查)报告任取 3 个数字 | 与 `curl /api/provenance?run_id=...` 一致 |

### 1.3 硬性判据

- 壳:`npm run typecheck` + `npm run test:unit` 全绿(改壳后);本仓库:`npx tsc --noEmit` + `npx vitest run`(pi-insar)+ `.venv\Scripts\python.exe -m pytest -q` 全绿。
- 右栏流水线 Tab 在无 run、跑 run、失败 run 三态下都不崩(空壳/进行中/failed 字形)。
- 壳的全部改动在 `desktop/pi-app-overlay/` 有逐字节一致的镜像(§4 校验脚本)。
- `41-appendix-final-checklist.md` 既有 8 段仍全绿(桌面工作不得回退 CLI 链路)。

## 2. 阶段划分(D0 → D5:先保 pi 原生,再 InSAR 化,最后自打包)

> 每阶段自包含:前置核对 → 改动路径 → 验收命令 → 风险。跨阶段顺序不可跳跃。所有 Desktop 人工验收都在 dev 态(`npm run dev`)做,直到 D5。

### D0 · 基线固化:壳原生功能可用 + InSAR 扩展进 Desktop(零壳改动)

**目标**:证明"pi Desktop 原生功能完好"与"F1 链路真实成立"——这是后续一切改动的回归基线。

**改动路径**(全部本仓库,壳零改动):
- `desktop/pi-app-overlay/`(新建):overlay 目录骨架 + `README.md`(声明镜像规则:壳内相对路径 1:1 映射,如 `src/main/side-panel-registry.ts` → `desktop/pi-app-overlay/src/main/side-panel-registry.ts`)。
- `scripts/sync-pi-app-overlay.ps1`(新建):双向工具——`-Check` 逐字节比对 overlay 与壳、`-Pull` 从壳抓回 overlay、`-Push` 从 overlay 铺到壳(新机器复原用)。文件清单以 overlay 目录实际内容为准,不硬编码。
- `pi-insar/docs/desktop-walkthrough.md`(新建):Desktop 人工走查手册第一版(D0 部分:启动、打开工作区、信任、模型确认、insar_health、/insar-mode)。

**验收命令**:

```powershell
# 1) 壳原生自检(不带任何本仓库改动)
cd E:\SoftApp\pi-app
npm run typecheck; npm run test:unit          # 全绿(基线快照,留存输出)
# 2) 本仓库自检
cd E:\01所有项目\06定职讲师\00insaragent\pi-insar
npx tsc --noEmit; npx vitest run
# 3) 后端(窗口 A)+ Desktop(窗口 B)
pwsh scripts/insar-backend-real.ps1
pwsh -NoProfile -File scripts/insar-pi-desktop.ps1
# 4) 人工(按 desktop-walkthrough.md):打开本仓库→信任→新会话
#    a. 模型选择器出现 insar-llm/deepseek-v4-flash-0731 且为默认(.pi/settings.json 生效)
#    b. 中文说"调用 insar_health" → 工具卡返回 ok(31 工具已注册)
#    c. /insar-mode strict → 让它跑任意 bash → 被 guard 拦截
#    d. 左栏/设置等 pi 原生功能正常;插件页可见 InSAR Agent 适配器(alwaysVisible)
# 5) overlay 校验脚手架可用
pwsh scripts/sync-pi-app-overlay.ps1 -Check   # 此时 overlay 为空清单,应报"无受管文件,一致"
```

**风险**:
- R-D0-1 **builtin SDK 0.83.0 加载扩展失败**(扩展按 0.84.2 typecheck):若 `registerProvider`/`registerTool`/事件签名在 0.83 缺失或不同形,会话启动报扩展错误。处置:**不改扩展去将就 0.83**;把 D4 的"切 global 0.84.2"提前为 D0 的一部分,并在 walkthrough 记录"builtin 不可用,常驻 global"。
- R-D0-2 项目未信任导致 `.pi/` 不加载:预期行为,walkthrough 写明"选择信任"。
- R-D0-3 `better-sqlite3` 未编译(新环境 npm install 后):跑 `pwsh scripts/fix-pi-desktop-sqlite.ps1`。

### D1 · InSAR 面完整性走查(只改本仓库;产出 = 壳缺口清单定稿)

**目标**:31 个工具在 Desktop 会话里逐面走查,把"桌面缺的壳"从推测变成实测清单,锁定 D2 范围。**本阶段不改壳、不改扩展逻辑**。

**改动路径**(全部本仓库):
- `pi-insar/docs/desktop-walkthrough.md`(扩写):按 `41-appendix-final-checklist.md` §5/§6 的读回面逐条改写成 Desktop 中文指令版,含勾选框;新增"实测结论表":逐项记录 ①工具卡显示是否可读(list 模板 + `$.output.text`)②`insar_view_figure` 的 image 内容块**是否在 Desktop 时间线渲染成图**(F11 的关键未知)③长任务(execute/resume)期间工具卡与右栏的观感 ④notify 通道(backend 不可达警告)是否可见。
- `.pi/desktop/adapters/insar.adapter.json`(仅微调,若走查发现 statusField/JSONPath 不理想;不加 sidePanel——那是 D2)。

**验收命令**:

```powershell
# 走查手册 D1 章全部勾选;实测结论表四项均有"是/否+截图路径"记录
# 回归:pi-insar 测试与后端 pytest 不回退
cd pi-insar; npx tsc --noEmit; npx vitest run
cd ..; .venv\Scripts\python.exe -m pytest -q
```

**风险**:
- R-D1-1 图件不渲染(image 内容块被时间线丢弃):登记为 D2-c 条件性壳改动(定位到时间线消息渲染组件后另开切片,见 §7-G7),**不在本阶段抢修**。
- R-D1-2 长任务工具卡静默(既有 G7 缺口,P2):右栏(D2)已缓解;onUpdate 流式仍不排期。

### D2 · 改壳最小集:流水线右栏(+条件性图件渲染)

**目标**:Desktop 右栏出现"InSAR 流水线"Tab:11 步字形轨道 + 当前步 + 进度 + evidence + mode + taints,自动刷新;所有壳改动镜像进 overlay。

**改动路径 — 必须改壳(E:\SoftApp\pi-app,同步 overlay)**:
1. `src/main/side-panel-registry.ts`:`PROVIDERS` 加通用 **`http-json`** provider(async):从 adapter 的 `sidePanel.stateUrl`(模板,可引用 `${config.*}` 与环境变量白名单如 `${env.INSAR_API_BASE}`)GET JSON 原样透传;超时(默认 3s)与非 200 返回 `{ ok:false, error }` 形状的降级状态,不抛异常。`SidePanelStateProviderId` 联合类型扩为 `'workspace-trellis' | 'http-json'`,provider 签名允许返回 `Promise<unknown>`(IPC handler 已 await,F3)。
2. `src/extension-compat/adapter-schema.ts`:`AdapterSidePanel` 增加可选字段 `stateUrl?: string`、`pollMs?: number`(纯类型 + 注释,向后兼容,builtin 适配器零影响)。
3. `src/renderer/src/features/side-panels/generic-adapter-side-panel.tsx`:支持 `pollMs`(经 sidePanel 目录元数据传入;>0 时 setInterval 自动 load,卸载清理;保留手动刷新)。**不新增专用面板组件**——generic-json 的 JSON 树够 D2 用,美化(字形轨道可视化)若要做,放 D3 评估且必须是独立新组件文件,不动核心注册表。
4. `src/extension-compat/side-panel-catalog.ts` / `packages/shared/right-panels`:若 pollMs 需要进目录元数据,按最小面补字段。
5. 壳内单测:`src/main/__tests__/`(或就近 .test.ts,循壳惯例)覆盖 http-json provider 的成功/超时/非200/坏 JSON 四分支(用本地临时 HTTP 服务,**不 mock 网络层语义**)。

**改动路径 — 只改本仓库**:
6. `src/insar_agent/api/bridge_router.py`:`GET /api/monitor` 的 `session` 支持省略或 `@latest`:取 store 里最近活动会话的最新 run;无任何会话时返回既有"空壳"形状。加 pytest(真实临时后端,沿用既有测试形态)。
7. `.pi/desktop/adapters/insar.adapter.json`:加 `sidePanel` 字段:`stateProvider: "http-json"`、`panelComponent: "generic-json"`、`stateUrl: "${config.apiBaseHint}/api/monitor?session=@latest"`、`pollMs: 3000`、label "InSAR 流水线"、icon、defaultEnabled: true、i18n zh/en。
8. `scripts/sync-pi-app-overlay.ps1` 无需改;执行 `-Pull` 把 1-5 的壳文件镜像进 `desktop/pi-app-overlay/`。

**验收命令**:

```powershell
cd E:\SoftApp\pi-app
npm run typecheck; npm run test:unit           # 含新 provider 单测
cd E:\01所有项目\06定职讲师\00insaragent
.venv\Scripts\python.exe -m pytest -q          # 含 monitor @latest 新测试
pwsh scripts/sync-pi-app-overlay.ps1 -Check    # overlay 与壳逐字节一致
# 人工:后端起(realtest)→ Desktop 起 → 右栏出现"InSAR 流水线"Tab:
#   无 run:空壳 + mode;发起 D1 剧本第 5 条分析 run:字形轨道随执行推进(≤pollMs 延迟)
#   停后端:Tab 显示降级错误而非白屏;重启后端:自动恢复
```

**风险**:
- R-D2-1 上游漂移:壳是本地克隆 v0.5.7,改动都进 overlay,升级壳版本时以 overlay 为补丁源按 `-Push` 重铺 + 手工核对(overlay README 写明该流程)。
- R-D2-2 http-json 的 SSRF 面:provider 只在本机桌面进程内、URL 来自用户自己仓库的 adapter 文件,风险可控;仍限定协议 http/https + 默认仅允许 127.0.0.1/localhost 主机,越界记 error(单测覆盖)。
- R-D2-3 `@latest` 语义与多会话并存:文档写明右栏监控"最近活动会话";会话级精确监控留给 CLI 侧栏与 `insar_run_status`,不做右栏会话选择器(D3 若有真实需求再评估)。

### D2-c ·(条件性,依 D1 结论)时间线图件渲染

仅当 D1 实测"image 内容块不渲染"时执行:定位壳时间线消息/工具卡渲染组件(renderer features 下,D1 勘察时记录具体文件),补 image 内容块的内联渲染(尺寸约束 + 点击放大遵循壳既有图片预览设施)。改动文件与 D2 不重叠,同样进 overlay + 壳单测。验收:剧本第 4 条速度场图在会话内可见。

### D3 · 桌面工业化体验(只改本仓库)

**目标**:中文全流程顺滑:场景剧本技能在 Desktop 可发现、产物一键打开、strict/free 心智清晰。

**改动路径**(全部本仓库):
- `.pi/desktop/adapters/insar.adapter.json`:config.actions 加 `openPath`(打开 INSAR_HOME、最新 run 目录、exports 目录);note/i18n 补 strict/free 一句话说明;(若 D2 的 JSON 树观感不达标)评估专用面板组件——若确需,则是**壳新增独立文件** `src/renderer/src/features/side-panels/insar-pipeline-panel.tsx` + 注册表两行接线,进 overlay,与 G3/G4 文件不重叠。
- `pi-insar/skills/`、`.pi/skills` junction:确认场景包技能(subsidence/quake/volcano/...)在 Desktop 技能页可见可读;缺则补 junction 自愈(改 `scripts/insar-pi-desktop.ps1`)。
- `pi-insar/docs/desktop-walkthrough.md`:定稿 §1.2 九条剧本为正式验收章;加"重型计算批准话术"(用户明确说"批准执行"才触发新全链,引用规则文件)。
- `pi-insar/APPEND_SYSTEM.md`:若走查发现模型在 Desktop 语境下对右栏/工具卡认知不足,补一段 ≤300 字的桌面语境说明(保持 <8KB 测试红线)。

**验收**:§1.2 剧本 1-8 条全过(轻型);`openPath` 三个动作在 Windows 资源管理器正确打开;技能页可见 InSAR 技能;pi-insar/后端测试全绿。

**风险**:R-D3-1 体验项范围蔓延——凡"新面板美化/流式进度/会话选择器"一律记入"暂缓清单",不进本阶段。

### D4 · SDK 对齐:0.83.0(builtin)→ 0.84.2(global)

见 §5 策略。**改动路径**:壳零代码改动(用 F6 的设置页/IPC 切换);本仓库改 `pi-insar/docs/plan/43-appendix-pi-upgrade.md` 执行记录 + `desktop-walkthrough.md` 附"当前 Desktop 生效 SDK = global 0.84.2"。若切换暴露破坏,登记差异并按 §5 回退矩阵处置。

**验收命令**:

```powershell
npm ls -g @earendil-works/pi-coding-agent      # 0.84.2(全局在位)
# Desktop 设置 → SDK → 切到 global → 重启会话 worker
# 复跑 D0 步骤 4 + D2 人工验收 + 剧本 3/4/7 条抽查
# 壳与本仓库全部测试命令不回退
```

**风险**:R-D4-1 global 与 builtin 行为差(worker 内 API 面):差异逐条记进 43 附录;任一条阻断则回 builtin 并冻结在"builtin 常驻 + 差异清单"状态等上游,**不 patch 壳的 SDK 加载器**。

### D5 · 自打包:package:win(NSIS + portable)

**目标**:产出可分发的 Windows 安装包/便携包,安装版打开本仓库工作区通过冒烟。

**改动路径**:
- 本仓库:`scripts/package-pi-desktop.ps1`(新建):串 `fix-pi-desktop-sqlite.ps1`(存在 C1001 时)→ `npm run package:win` → 产物哈希与体积记录;`desktop-walkthrough.md` 加"安装版冒烟"章。
- 壳(可选品牌,均进 overlay):`electron-builder.yml` 的 icon/productName、`resources/` 图标。**默认不改 productName**(避免升级与用户数据目录漂移),只换 icon;改名需求单独评估。

**验收命令**:

```powershell
pwsh scripts/package-pi-desktop.ps1            # dist/ 出 Setup 与 Portable 两产物
# 安装到干净目录 → 启动 → 打开本仓库 → D0 步骤 4 冒烟 + 剧本 1/2/4/7 抽查
# 特别验证:asar 环境下项目 .pi/extensions/insar.ts(TS 直载)仍可加载;
#           无启动器 env 时扩展回退 http://127.0.0.1:8873 可用(F8)
```

**风险**:
- R-D5-1 asar 内 SDK 对项目 TS 扩展的直载依赖(jiti 等)在打包版缺文件:若失败,首选壳打包配置补 unpack 规则(`electron-builder.yml` asarUnpack,进 overlay),**不给扩展加构建步**;仍不行则记录为"打包版需 global SDK"并验证该路径。
- R-D5-2 原生模块在 electron-builder 重编阶段再次 C1001:`fix-pi-desktop-sqlite.ps1` 已处理 gyp;打包前先跑它。
- R-D5-3 打包产物体积/杀软误报:仅记录,不做签名(不在范围)。

## 3. 科学流程对照(对照 `04-science-capability-map.md` 与 00-README 已完成 Phase)

结论:**科学面已全部在后端/工具层完成(Phase 01-16 全勾),桌面缺的只是壳。** 逐环节:

| 科学环节 | 已有(不动) | 桌面缺口 | 补齐位置 |
|---|---|---|---|
| 数据接入(ASF/HyP3/本地/8 处理器) | 注册表方法 + 场景包(Phase 15) | 无——对话即用 | — |
| 11 步核心流水线 | 五阶段执行器 + provenance + 质量门(P0 既有) | 执行中**可见性**(TUI 侧栏在 Desktop no-op,F2) | D2 右栏 |
| 分析步 20-28(掩膜/校正/分解/统计/出图/预测/反演桥) | 注册表驱动 + 分析 run 机制(Phase 10-14) | 同上,右栏对分析 run 同样生效(/api/monitor 不分家) | D2 |
| 出图 + AI 识图 | figures 引擎 + `insar_view_figure` + `insar_vision_qa`(Phase 07/12) | 图在 Desktop 时间线是否内联(F11 未知) | D1 实测 → D2-c |
| 报告/复现包/数字可溯 | report 体系 + repro-bundle + provenance(Phase 08) | 产物**打开路径**(桌面语境下用户要一键到文件) | D3 openPath |
| strict/free 纪律 | guard + /insar-mode + pi-journal(Phase 09) | 无——guard 在扩展层,Desktop 同样生效(D0 验证) | — |
| LLM 供应商 | insar-llm ← workspace/llm.json(Phase 02) | 无——F1 链路直达;唯一真源不变,**不进壳任何配置文件** | — |
| 环境变量(INSAR_API_BASE 等) | CLI 启动器已设 | dev 态经启动器继承(F8);打包版靠默认端口 | D5 验证 |
| 工作区/技能/主题 | `.pi/` junction + settings.json(Phase 01/04) | Desktop 主题体系与 TUI 主题独立,insar-dark 不适用于壳 UI——**不做壳换肤**,品牌走 D5 icon(可选) | D5(可选) |

## 4. 必须改壳 vs 只改本仓库(总清单)

### 4.1 必须改 E:\SoftApp\pi-app(每一处都镜像进 `desktop/pi-app-overlay/`)

| # | 文件(壳内相对路径) | 改动 | 阶段 |
|---|---|---|---|
| 壳1 | `src/main/side-panel-registry.ts` | 加 `http-json` 异步 provider + 类型放宽 | D2 |
| 壳2 | `src/extension-compat/adapter-schema.ts` | `AdapterSidePanel` 加 `stateUrl?`/`pollMs?` | D2 |
| 壳3 | `src/renderer/src/features/side-panels/generic-adapter-side-panel.tsx` | pollMs 自动刷新 | D2 |
| 壳4 | `src/extension-compat/side-panel-catalog.ts`(+shared 类型,若元数据需带 pollMs) | 元数据透传 | D2 |
| 壳5 | provider/面板就近 `.test.ts` | 新行为单测 | D2 |
| 壳6 | (条件性)时间线 image 内容块渲染组件(D1 定位) | 图件内联 | D2-c |
| 壳7 | (可选)`src/renderer/src/features/side-panels/insar-pipeline-panel.tsx` + 注册表两行 | 专用美化面板 | D3 评估 |
| 壳8 | (可选)`electron-builder.yml` + `resources/` 图标 | 品牌 icon | D5 |

**overlay 纪律**:壳仓库(E:\SoftApp\pi-app)是本地克隆、不受本仓库 git 管;凡改壳,同一切片内必须 `sync-pi-app-overlay.ps1 -Pull` 回本仓库并随切片提交(执行 git 提交遵循 00-README 执行循环,由用户/Grok 按既有纪律做);`-Check` 进 D2 之后每阶段验收。

### 4.2 只改本仓库

| 文件 | 改动 | 阶段 |
|---|---|---|
| `desktop/pi-app-overlay/**` + `scripts/sync-pi-app-overlay.ps1` | overlay 机制(新建) | D0 |
| `pi-insar/docs/desktop-walkthrough.md` | Desktop 走查手册(新建→扩写→定稿) | D0-D5 |
| `src/insar_agent/api/bridge_router.py` + 新 pytest | monitor `@latest` 语义 | D2 |
| `.pi/desktop/adapters/insar.adapter.json` | sidePanel 字段、openPath actions、i18n | D2/D3 |
| `scripts/insar-pi-desktop.ps1` | junction 自愈补漏、提示完善 | D3 |
| `pi-insar/APPEND_SYSTEM.md` | (若需)桌面语境 ≤300 字 | D3 |
| `scripts/package-pi-desktop.ps1` | 打包一键脚本(新建) | D5 |
| `pi-insar/docs/plan/43-appendix-pi-upgrade.md`、`00-README.md` | SDK 对齐执行记录、索引行 | D4 |

## 5. SDK 0.83.0 vs 0.84.2 策略(先原生,后对齐)

现状:壳 dep 锚 **0.83.0**(builtin);全局 CLI 与 pi-insar devDependency 均 **0.84.2**。扩展按 0.84.2 的 API 写成并 typecheck。

1. **D0-D3 全程跑 builtin 0.83.0**(壳默认):理由——先证明壳原生功能与打包链路在其锚定版本上成立,版本变量后置;扩展用到的 API 面(registerProvider/registerTool/on/registerCommand)在 0.83 类型里均存在(已核实 d.ts),预期兼容。
2. **兼容性哨兵**(D0 步骤 4):若 0.83 运行时暴露任何扩展断裂 → **不改扩展迁就旧版**(单真源:扩展只对齐 0.84.2),立即启用壳内 SDK 切换(设置 → SDK → global 0.84.2,F6),后续阶段在 global 上跑,并把该事实记入 walkthrough 与 43 附录。
3. **D4 正式对齐**:切 global 0.84.2 → 全量回归(壳 typecheck/test:unit + 本仓库双测试 + 剧本抽查)→ 绿则**常驻 global**。回退矩阵:任一阻断 → `sdk.switch` 回 builtin(秒级、无文件改动)→ 差异记录进 43 附录等待上游。
4. **不做**:改壳 `package.json` 依赖升 0.84.2(那是上游 pi-app 的事,升壳版本时自然带来;本地 bump 会造成与上游 v0.5.7 基线的漂移,违背 overlay 最小化原则);fork SDK;在壳里第二处配置 LLM 密钥(llm.json 唯一真源经扩展进程内注册,与 SDK 版本无关)。
5. **打包版注意**(D5):安装包内嵌的是 builtin;若 D4 结论是"必须 global",则安装版首次运行需引导用户装全局包或用壳的 `sdk.install`(user 态)装 0.84.2——该引导写进 walkthrough 安装章。

## 6. 不做清单(与 `03-design-analysis.md` §1.4/§1.8 一脉相承)

- **不做 Web**:pi-web/pi-chat/远程会话/RPC 多客户端一律暂缓。旧网页 `prototype/` 已删除。
- **不复活 Tauri**:`desktop/` 旧壳(Cargo/tauri.conf.json)冻结不动、不删除;新桌面 = pi-app 源码壳唯一路线。
- **不引入任何假数据**:Desktop 验收全部跑在真实后端(realtest audited run)上;右栏/工具卡不接受 mock 状态源;测试用"临时目录真实拉起的后端"既有形态。
- **不 fork pi 官方 TUI / 不改 pi-coding-agent 源码**:TUI 侧栏在 Desktop 的 no-op 不去"修"(那是壳的设计边界),以 adapter sidePanel 正门替代。
- **不做壳级第二套 LLM 配置**、不把 key 写进 adapter/settings/electron store。
- **不做右栏会话选择器、onUpdate 流式进度、壳全面换肤**(暂缓清单,有真实使用反馈再议)。
- **不在计划内擅自触发重型 MintPy 全链**:一切新的真实执行须用户明确批准后、低优先级、单任务串行。

## 7. 给 Grok 的任务切片(每片文件闭集互不重叠;按序执行,G3 起需前片完成)

| 片 | 内容 | 文件闭集 | 验收 |
|---|---|---|---|
| G1 | overlay 机制 + 走查手册骨架(D0) | `desktop/pi-app-overlay/README.md`、`scripts/sync-pi-app-overlay.ps1`、`pi-insar/docs/desktop-walkthrough.md`(新) | `-Check` 空集通过;手册含 D0 章 |
| G2 | monitor `@latest`(D2 后端面) | `src/insar_agent/api/bridge_router.py`、`tests/api/test_monitor_latest.py`(新) | pytest 全绿;`curl "/api/monitor?session=@latest"` 对 realtest 返回 11 步 |
| G3 | 壳 http-json provider + schema 字段(D2 壳1/2/5) | 壳 `src/main/side-panel-registry.ts`、`src/extension-compat/adapter-schema.ts`、就近新 `.test.ts`;镜像 `desktop/pi-app-overlay/src/...` 同名 | 壳 typecheck+test:unit 绿;`-Check` 一致 |
| G4 | 面板轮询 pollMs(D2 壳3/4) | 壳 `generic-adapter-side-panel.tsx`、`side-panel-catalog.ts`(+shared 类型文件,若必需);镜像 overlay | 同上;手动刷新仍可用 |
| G5 | insar 适配器 sidePanel 接线(D2 仓库面) | `.pi/desktop/adapters/insar.adapter.json` | Desktop 右栏 Tab 三态人工验收(D2 验收段) |
| G6 | 走查手册 D1/D3 扩写 + 启动器完善 | `pi-insar/docs/desktop-walkthrough.md`、`scripts/insar-pi-desktop.ps1` | 手册剧本可执行;junction 自愈幂等 |
| G7 | (条件性,D1 结论为"图不渲染"时)时间线 image 渲染 | 壳时间线渲染组件(D1 勘察定位,开片前在片描述里写死文件名,不得触碰 G3/G4 文件)+ overlay 镜像 | 剧本第 4 条图内联可见 |
| G8 | openPath actions + APPEND_SYSTEM 桌面段(D3) | `.pi/desktop/adapters/insar.adapter.json`(G5 完成后)、`pi-insar/APPEND_SYSTEM.md` | 三个 openPath 动作正确;APPEND <8KB 测试绿 |
| G9 | SDK 对齐执行(D4,操作+记录,无代码) | `pi-insar/docs/plan/43-appendix-pi-upgrade.md`、`pi-insar/docs/desktop-walkthrough.md`(SDK 章) | D4 验收段全过 |
| G10 | 打包一键脚本 + 安装冒烟(D5) | `scripts/package-pi-desktop.ps1`(新)、`pi-insar/docs/desktop-walkthrough.md`(安装章);(可选品牌)壳 `electron-builder.yml`+`resources/` + overlay 镜像 | D5 验收段全过 |

> 切片纪律:G5 与 G8 同文件(insar.adapter.json)故**必须串行**;G3/G4 虽同属 D2 但文件不重叠,可并行;每个改壳切片的"完成定义"包含 overlay 镜像与 `-Check` 通过。任何切片不得新增依赖、不得动 `pi-insar/src/**` 科学工具逻辑(那是已交付面,回归靠既有测试)。

## 8. 关键文件坐标速查(执行时免二次勘察)

```
壳(E:\SoftApp\pi-app,v0.5.7,dep pi-coding-agent 0.83.0)
  src/main/side-panel-registry.ts        # PROVIDERS(现仅 workspace-trellis)
  src/main/ipc/handlers/adapter-panels.ts# ipc:adapter.sidePanel.getState(已 await)
  src/extension-compat/adapter-schema.ts # AdapterSidePanel 类型(加 stateUrl/pollMs 处)
  src/extension-compat/adapter-loader.ts # 项目 .pi/desktop/adapters 整份覆盖加载
  src/renderer/src/features/side-panels/ # generic-adapter-side-panel.tsx、side-panel-registry.tsx、side-panel-host.tsx
  src/worker/desktop-ui-bridge.ts        # setWidget/setStatus = no-op 的证据
  src/main/sdk-loader.ts、ipc/handlers/pi-sdk.ts  # SDK 三态与 sdk.switch
  package.json                           # dev/typecheck/test:unit/package:win/rebuild:native
本仓库
  .pi/desktop/adapters/insar.adapter.json# 31 工具 + /insar-mode(待加 sidePanel)
  src/insar_agent/api/bridge_router.py   # /api/monitor(session 必填 → 加 @latest)
  pi-insar/src/{index,tools,sidebar,provider,guard,mode,journal}.ts  # 已交付,不动
  scripts/{insar-pi-desktop,insar-backend-real,fix-pi-desktop-sqlite}.ps1
  pi-insar/docs/plan/{00,01,03,04,41,43}*.md  # 纪律与验收基准
```
