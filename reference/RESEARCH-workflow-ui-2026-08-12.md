# 调研：科学/数据工作流系统的 DAG 与缓存可视化

- 日期：2026-08-12
- 调研对象：Nextflow（Seqera Platform）、Snakemake、Dagster、Prefect、Airflow、Temporal、Metaflow、GitHub Actions、Buildkite，另附 GitLab CI 与 Argo Workflows
- 服务目标：insar-agent 原型 `prototype/js/dock.js` 的流水线面板（11 步固定 DAG、五阶段状态机、三段指纹、失效传播、断点续跑、fork 参数试探；当前为纵向列表）
- 方法：网络公开文档 / 官方博客 / 源码仓库 PR 与 issue 讨论；未运行任何系统

---

## 0. 要点速览（TL;DR）

1. **近线性 DAG 不值得上整幅图**。11 步、仅 2 条跨步边（3←[1,2]、11←[10,7]）的拓扑，业界同构场景（Seqera、Buildkite 侧栏、GitHub jobs 侧栏）全部用**列表为主、图为辅**。最优解是「纵向列表 + 左侧依赖轨道（git-graph 式细 SVG rail）」，不是 dagre/ELK 整图。
2. **缓存命中是第一等状态，不是没有状态**。Nextflow/Seqera 把 `CACHED` 与 Succeeded/Failed 并列成状态卡计数；Snakemake 在 DAG 里给「无需重跑」的节点画虚线框。我们的「指纹未变直接跳过」应显式渲染为第三态（空心节点 + ↷ 图标 + 计数条），而不是沿用 done 样式。
3. **失效要给原因链，不只给颜色**。Dagster 的 stale 标签带 `staleStatusCauses`（“has a new code version / has a new dependency on X / upstream data changed”），hover 弹出可点击跳到上游资产。这与我们的三段指纹一一对应（method+params / 上游指纹 / 工具版本），是本次调研**性价比最高的移植项**。
4. **部分重跑入口的黄金模式：先预览影响集，再确认**。Airflow Clear 弹窗列出将被清除的全部任务实例（Downstream/Upstream/Only Failed 可选）；GitHub Actions 部分重跑确认框列出「包括下游依赖在内」将重跑的 job。我们做「从第 N 步重跑」时必须先列出级联 STALE 的下游步骤和预计总耗时。
5. **Resume 与 Relaunch 是两个语义，别合并成一个按钮**。Seqera：Resume=同参数、吃缓存、只跑失败与未跑；Relaunch=可改参数、从头跑。对应我们的「断点续跑」与「改参数后重算受影响段」。
6. **run 对比的成熟设计 = 参数 diff 表（默认只显示差异项）+ 每步耗时对比**。MLflow 的 “Show diff only” 开关、Airflow 的 Task Duration（跨 N 次 run 的每任务耗时曲线）是两块拼图；fork 参数试探可用「列=fork、行=步骤」的 Airflow Grid 式矩阵呈现。
7. **零依赖 SVG 完全可行**。Dagster 用打了补丁的 dagre + IndexedDB 缓存布局才撑住 1 万节点，而我们节点数固定为 11、拓扑编译期已知——布局可以手写常量，比引入任何库都简单。`figures.js` 已证明手写 SVG 的工程可行性。

---

## 1. 各系统一节

### 1.1 Nextflow / Seqera Platform（原 Tower）

**定位**：科学工作流（生信为主）的执行引擎 + 商业监控平台。与我们最同构：批处理、长任务、强缓存（`-resume`）。

**DAG 图**
- Nextflow 引擎侧：`-with-dag` 输出静态 DOT / Mermaid / HTML，仅描述管线结构，不承载运行状态（[Tracing docs](https://dokk.org/documentation/nextflow/v23.04.4/tracing/)）。
- Seqera Platform 的运行详情页**没有内置实时 DAG 图**：要看图需把 `-with-dag` 产物配置成 Reports 才能在 Reports 标签打开（[Run details docs](https://docs.seqera.io/platform-enterprise/monitoring/run-details)）。一个商业化多年的工作流监控平台选择不画实时图，本身就是「列表 + 进度条足够」的市场验证。
- 取而代之的是 **Processes 面板**：每个 process 一行，行内一条按任务状态着色的分段进度条（created/submitted/completed/failed），一眼看出每步完成了几个任务。

**缓存可视化（本项目最应参考）**
- Tasks 标签顶部一排**状态计数卡**：Pending / Submitted / Running / **Cached** / Succeeded / Failed / Aborted，实时刷新。Cached 的定义：“A previous (and valid) execution of the task was found and used instead of executing the task again”（[Run details](https://docs.seqera.io/platform-enterprise/monitoring/run-details)）。
- 任务表带 `hash` 列（Nextflow task hash，即缓存键）与 `attempt` 列。用户可按 status 子串过滤。
- HTML 执行报告（`-with-report`）把 CACHED 作为任务状态之一聚合进 Summary（[ReportObserver.groovy](https://github.com/nextflow-io/nextflow/blob/master/modules/nextflow/src/main/groovy/nextflow/trace/ReportObserver.groovy) 有专门的 `onTaskCached` 钩子）；社区插件 nf-crg-report 按 COMPLETED/FAILED/**CACHED**/RETRIED/ABORTED 分组出报表。
- 缓存机制：任务 hash = 输入 + 脚本 + 容器等的组合；resume 时命中 hash 且工作目录产物完好才算 CACHED，否则重跑（[Caching and resuming](https://docs.seqera.io/nextflow/cache-and-resume)）——与我们「三段指纹 + 产物存在性校验」同构。

**运行详情页信息架构**（2025 年新版，[Seqera 博客](https://seqera.io/blog/new-seqera-products-boston-summit-2025/)）
- 顶部：run 元数据 + **整体进度条**；下方标签页：Tasks / Logs / Metrics / Configuration（解析后的最终配置）/ Inputs（含 lineage 记录）/ Outputs（每个发布文件带 lineage 记录）/ Containers（含构建溯源与安全扫描）/ Run Info。
- 点任务行开**任务详情对话框**，四个子标签：About / Execution log（实时日志）/ Data Explorer / Container。
- 时间轴报告（`-with-timeline`）：每任务一根横条，**左侧灰色段=调度等待，彩色段=真实执行**，标签显示耗时 + 峰值内存——「一根条内剖开阶段」的画法可直接映射我们的五阶段状态机。

**重跑入口**
- run 的 options 菜单区分 **Resume**（同参数，吃缓存，只跑失败与 pending 的任务；不允许换管线与工作目录）与 **Relaunch**（可改参数、从头跑）（[cache-resume docs](https://docs.seqera.io/platform-enterprise/launch/cache-resume)）。

**可借鉴**：状态计数卡（含 Cached）；hash/attempt 列；Resume/Relaunch 语义分离；进度条分段着色；任务详情四分区；灰段+彩段的耗时条。

### 1.2 Snakemake

**定位**：文件驱动的科学工作流（make 血统），无常驻 UI，可视化走 CLI 产物。

**DAG 图**
- 三档粒度：`--dag`（作业级）、`--rulegraph`（规则级，一个规则一个节点）、`--filegraph`（规则+输入输出文件），输出 DOT 交给 graphviz 渲染（[CLI docs](https://snakemake.readthedocs.io/en/stable/executing/cli.html)）。**作业数爆炸时主动降粒度**是它的核心经验——我们 11 步固定粒度天然处在最优档。
- `--dag` 中**输出已是最新、无需重跑的作业画成虚线框**，需要执行的画实线——用「线型」而非颜色表达缓存命中，打印/色盲友好。

**缓存/重跑原因（与三段指纹强对应）**
- 7.8 起默认 `--rerun-triggers` 含五类：`mtime, params, input, software-env, code`（[CLI docs](https://snakemake.readthedocs.io/en/stable/executing/cli.html)；[公告 issue #1694](https://github.com/snakemake/snakemake/issues/1694)）。作者原话：宁可因“美化空格”多跑一次，也不让磁盘结果与代码库状态悄悄不一致——与我们「指纹重算→级联 STALE」哲学同源。
- dry-run 时每个要重跑的作业打印 **reason**（如 “params changed / code has changed since last execution”），并在结尾提示如何豁免（`--rerun-triggers mtime`、`--cleanup-metadata`、`--consider-ancient`）。**给用户豁免通道**值得注意：我们目前失效即必须重跑，未来可考虑「确认此变更不影响结果，保留产物」的显式豁免操作（记入审计）。
- `--report` 生成自包含 HTML：运行统计 + 溯源（每个结果可展开生成它的规则代码、参数、软件版本）+ 拓扑（[Reports docs](https://snakemake.readthedocs.io/en/latest/snakefiles/reporting.html)）。定位是「论文补充材料级」交付物，与我们报告面板的目标一致。

**可借鉴**：虚线=可跳过的线型语义；重跑原因文案（“params changed”）；rerun-trigger 分类与三段指纹的对齐；豁免通道；自包含 HTML 报告作为交付物。

### 1.3 Dagster（资产血缘 UI 的标杆）

**定位**：以「资产（asset）」为中心的数据编排。它的资产=我们的产物，物化（materialization）=我们的一次步骤执行。

**DAG 图与布局**
- 资产图用 **dagre（打补丁维护）**，Sugiyama 分层布局；默认 ranker 从 network-simplex（最好看，最坏 O(N³)）换成 **tight-tree**，2000 资产的布局从分钟级降到 ~5 秒；布局结果缓存进 **IndexedDB**（localStorage 10MiB 不够）（[Scaling DAG Visuals 博客](https://dagster.io/blog/scaling-dag-visualization)、[PR #17723](https://github.com/dagster-io/dagster/pull/17723)）。
- 支持横/纵双向切换（Alt-O，[PR #19954](https://github.com/dagster-io/dagster/pull/19954)）；边渲染在 SVG 里、上限约 50 条可见边、用 Web Worker 算可见性（[AssetEdges.tsx](https://github.com/dagster-io/dagster/blob/4fb00173/js_modules/ui-core/src/asset-graph/AssetEdges.tsx)）。
- 结论反过来读：**这些工程量全是为了「节点数不可预知」付出的代价**。节点数固定的我们一分钱都不用付。

**失效（stale）可视化 —— 全场最佳**
- 资产带 **“Unsynced”标签**：代码版本变了、依赖增删了、或上游数据版本变了（[Asset versioning and caching](https://docs.dagster.io/guides/build/assets/asset-versioning-and-caching)）。
- 标签 hover 弹出 **staleStatusCauses 原因列表**，由服务端计算（[commit a1b4eea](https://github.com/dagster-io/dagster/commit/a1b4eead27e0bd611cfef47275090b77a9cec349)）；后从 Tooltip 升级为 **Popover**（鼠标能移进去、可滚动、**上游资产名可点击跳转**，[PR #14840](https://github.com/dagster-io/dagster/pull/14840)）。
- 原因文案精细打磨过（[PR #13529](https://github.com/dagster-io/dagster/pull/13529)）：“has a new code version” / “has a new dependency on foo/bar” / “has a new data version” / 未定义 code version 时降级说 “has a new materialization”——**按用户能理解的粒度分类失效原因**。
- 官方还专门开过 ontology 讨论（[Discussion #13102](https://github.com/dagster-io/dagster/discussions/13102)）：黄色 stale 太像告警，且用户常常“可以接受不新鲜”；提案包括拆成具体状态（“Upstream data changed / Code changed”）、改绿色+小图标。**对我们的启示**：insar 场景里 STALE=科学结论不再可信，黄色警示是对的，但「上游失效」与「自身参数变更」应在文案上分开（类比 Airflow 的 failed vs upstream_failed）。
- 数据版本有智能截断：上游重算但**数据版本没变**时，下游不标 stale——对应我们「指纹未变的步骤直接跳过」，Dagster 证明这套语义用户能接受。

**物化与血缘**
- 资产详情页标签：Overview（元数据、列 schema、**元数据数值自动画成跨物化时间序列图**）/ Partitions / **Events（物化历史，每条含 run 链接、code_version、data_version）** / Checks / Lineage / Automation（[webserver docs](https://github.com/dagster-io/dagster/blob/master/docs/docs/guides/operate/webserver.md)）。
- 侧栏对比「上次物化时的 code version v1 vs 当前 v2」——**明示新旧版本对照**而不是只说“变了”。
- 列级血缘（Dagster+）：点某列的分支图标，展开该列的上游列依赖图（[column-level lineage](https://docs.dagster.io/guides/build/assets/metadata-and-tags/column-level-lineage)）。
- 物化按钮带下拉：**“Materialize unsynced”只重算失效子集**——「只跑级联受影响段」做成了一键操作。

**可借鉴**：stale 原因 popover（可点击上游）；新旧版本对照；Events 物化历史含版本三元组；materialize-unsynced 一键补算；元数据时序小图。

### 1.4 Prefect

**定位**：Python 通用编排。运行图技术激进，值得看它放弃了什么。

**运行图**
- 早期 Prefect 2 有「Radar」同心圆图（径向布局），后被彻底放弃——**新奇布局输给了朴素布局**。
- 现行 flow run graph 用 **Pixi.js WebGL 画布**重写（[graphs AGENTS.md](https://github.com/PrefectHQ/prefect/blob/81d5e547/ui-v2/src/graphs/AGENTS.md)），提供多种布局：**temporal（节点按时间轴定位）**、dependency grid（纯依赖网格）、comparative duration（时长对比）；x 轴可用 +/- 缩放；布局在 Web Worker 里异步算（[PR #11112](https://github.com/PrefectHQ/prefect/pull/11112)）。
- **Cached 任务显式出现在图上**（同 PR 的 release notes 专门列了 “Cached tasks appear on the graph”——曾经不显示，被用户要求加回来：跳过的东西也要可见）。
- 依赖边来自输入推断或显式 `wait_for`；图数据走专用 `graph-v2` 接口。

**详情页信息架构**
- flow run 详情标签：Details / Logs / **Task Runs** / Subflow Runs / **Artifacts** / **Parameters** / Job Variables（[flow-run-details-page 源码](https://github.com/PrefectHQ/prefect/blob/81d5e547/ui-v2/src/components/flow-runs/flow-run-details-page/index.tsx)）。参数独立成签，和日志平级。
- **Artifacts 系统**：markdown/表格/链接/进度/图片五类，任务代码里主动上报；带 `key` 的 artifact 形成**跨 run 版本序列**，可点进看历史版本与产生它的 run/task 链接（[artifacts docs](https://docs.prefect.io/v3/concepts/artifacts)）——「产物有自己的页面和历史」，而不只是 run 的附属。

**可借鉴**：temporal/依赖两种布局按需切换的思想（我们可在一个 rail 上同时编码顺序与依赖）；cached 上图；参数独立分区；产物带版本历史与反向链接。

### 1.5 Airflow

**定位**：任务编排的事实标准，UI 模式被反复验证。

**Grid vs Graph —— 列表与图取舍的教科书**
- **Grid 视图（主界面）**：行=任务、列=DAG run 的矩阵，格子按状态着色，顶栏是每次 run 的时长柱状图；点格子右侧滑出详情面板（日志/rendered template/XCom/操作按钮）（[UI Overview](https://airflow.apache.org/docs/apache-airflow/stable/ui.html)、[Astronomer 指南](https://astronomer.io/docs/learn/airflow-ui)）。
- **Graph 视图（辅助）**：看结构与本次 run 的状态叠加，节点内含时长指示；顶部下拉切换不同 run。新版基于 React Flow + ELK 分层布局。
- 官方使用建议原话级明确：**“Use Graph view when you need to understand the shape of dependencies. Use Grid view when you need to see patterns over time.”**（[datavidhya masterclass](https://datavidhya.com/learn/airflow/dags-tasks-operators/airflow-ui-masterclass/)）——图管结构、网格管时间维；我们结构近线性，所以图的价值进一步缩水，时间维（run 对比）反而值得投入。
- 状态色约定：success 深绿 / running 亮绿或青 / failed 红 / **upstream_failed 橙** / skipped 粉 / up_for_retry 黄；可用 `STATE_COLORS` 自定义（[customize docs](https://airflow.apache.org/docs/apache-airflow/2.1.3/howto/customize-state-colors-ui.html)）。有两个现实教训：running 从 lime 改 cyan 是为了「不暗示运行中的必然成功」+ 深色模式对比度（[PR #55644](https://github.com/apache/airflow/pull/55644)）；failed 与 upstream_failed 的 ΔE=6.2 被 issue 指出色盲不可分——**“自身失败”与“被牵连”必须可区分，且不能只靠颜色**。

**部分重跑（Clear）—— 入口设计标杆**
- 点任务 → Clear task → 弹窗提供范围选项：**Downstream / Upstream / Past / Future / Only Failed / Recursive**，并**列出当前设置下将被清除的所有任务实例**，确认后重跑（[Astronomer rerun 指南](https://astronomer.io/docs/learn/rerunning-dags)、[DAG Runs docs](https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/dag-run.html)）。
- 血的教训两则：Downstream 默认勾选导致用户误清一大片（[issue #53500](https://github.com/apache/airflow/issues/53500) 抱怨“选项长得像标签页不像开关”）；后续 PR 把 mark-as 的 downstream 默认改掉并讨论该由用户偏好还是管理员配置决定（[PR #67763](https://github.com/apache/airflow/pull/67763)）。**默认范围要保守，选项控件要长得像开关**。
- 配套机制：清除/重试后 **try_number 递增、历史保留**，Grid 里可看每次尝试；支持给任务实例/运行**写备注**（记录为什么手动重跑）——审计友好，贴合我们 Lab Notebook 的思路。
- “Mark state as success/failed”：修好外部问题后不重跑直接标状态——我们的「豁免」原型。

**跨 run 耗时对比**
- **Task Duration 视图**：每个任务过去 N 次 run 的耗时折线，找离群点；**Landing Times**：计划时间到完成的延迟；**Gantt**：单次 run 内的并行与瓶颈（[UI/Screenshots 2.9](https://airflow.apache.org/docs/apache-airflow/2.9.3/ui.html)）。新版 Grid 每行还有 mini-Gantt。

**可借鉴**：Clear 弹窗的影响集预览与范围选项；upstream_failed 独立状态；try_number 历史；重跑备注；Task Duration 跨 run 折线；「图管结构、网格管时间」的分工原则。

### 1.6 Temporal

**定位**：持久执行引擎，无静态 DAG——历史是唯一事实。视角独特但交互设计极精。

**时间轴与事件历史**
- 工作流详情页顶部：Start/Close/Duration、Run Id、Task Queue、状态转移计数等元数据 + **Input and Results 区**（函数入参与返回值直读）（[web-ui docs](https://docs.temporal.io/web-ui)）。
- History 拆成 **Timeline（紧凑时间轴）与 Event History（全量 JSON）两个标签**，两者**通过 URL query 参数共享选中/展开状态**，切标签不丢上下文（[ui PR #3109](https://github.com/temporalio/ui/pull/3109)）。
- Timeline 把相关事件聚合成 **Event Group**（Scheduled/Started/Completed 归一行），竖向堆叠、线长=时钟时长、实时更新；可同时展开多个 group 对比（[重设计博客](https://temporal.io/blog/the-dark-magic-of-workflow-exploration)）。
- 过滤器直接内置 **“Pending & Failed only”**；pending activity 行显示**当前第几次尝试 + 下次重试时间**（[changelog](https://temporal.io/change-log/updated-event-history-timeline-view-is-now-available)）。
- 设计原则演进（[PR #2269](https://github.com/temporalio/ui/pull/2269)）：主题就叫 **“Show Everything”**——所有过滤器常显、时间戳+相对时长同显、展开一行给全部关联事件，不把信息藏在二级交互后面。

**Reset（从某点重跑）**
- Actions 菜单 → Reset → 模态里选重置点（第一个/最后一个 workflow task 或任意 Event ID）+ **必填 reason（记入新历史）**；确认后终止旧执行、复制历史到重置点、开新执行；**旧执行页顶出 Alert 横幅链接到新执行**（[ui PR #1046](https://github.com/temporalio/ui/pull/1046)、[cancellation docs](https://docs.temporal.io/develop/typescript/workflows/cancellation)）。
- 另有 **“Start Workflow Like This One”**：预填当前入参开新工作流——fork 参数试探的直接原型。

**可借鉴**：URL 保持面板状态；紧凑/全量双视图 + 状态互通；重置必填 reason + 新旧执行互链；pending 行显示重试计划；Show Everything 原则。

### 1.7 Metaflow

**定位**：Netflix 的 ML 科学工作流，UI 明确为「监控 + 实验追踪」（[开源博客](https://netflixtechblog.com/open-sourcing-a-monitoring-gui-for-metaflow-75ff465f0d60)）。

- **Timeline 视图是主力**：找性能瓶颈、任务时长分布、失败任务；顶部横排 run 全局属性（状态/开始时间/参数）。
- **DAG 视图为辅**：步骤节点按状态着色，split 分支用浅色底框分组、foreach 用双层底框分组；点节点跳任务视图。社区在做「Expanded DAG」：foreach 内的任务卡片可展开、按状态着色（[issue #89](https://github.com/Netflix/metaflow-ui/issues/89)）。
- **任务视图 = 日志 + 结果 + Cards**：`@card` 装饰器让每步产出 HTML 报告（图、表、文本），2.11 起可在运行中**实时刷新**（[Visualizing Results](https://docs.metaflow.org/metaflow/visualizing-results)）——「每步自带一张结果卡」与我们影像面板按步挂图完全同构。
- 插件系统：向 `task-details`、`run-header` 等插槽注入自定义 HTML/JS。

**可借鉴**：每步结果卡（我们已有影像画廊，可在步骤详情内嵌该步图件缩略）；分组底框表达子结构；timeline 优先于 DAG 的排序。

### 1.8 GitHub Actions

- **run summary 页自带实时图**：节点=job，名字左侧状态图标，连线=needs 依赖；点 job 看日志；matrix 腿聚合进父节点（[官方文档](https://docs.github.com/actions/managing-workflow-runs/using-the-visualization-graph)）。图是只读的、布局简单（分层左→右），没有缩放平移的复杂交互——**小图就该这么省**。
- **部分重跑**（[官方博客](https://github.blog/news-insights/product-news/save-time-partial-re-runs-github-actions/)）：Re-run jobs 下拉分 “Re-run all jobs / Re-run failed jobs”；jobs 侧栏每个 job hover 出现单独重跑图标；日志页里也能发起。**确认框列出将重跑的全部 job（含下游依赖）**。
- **attempt 导航**：多次重跑后出现 attempt 下拉，可回看每次尝试；新旧 job 并排显示在同一 run 里，账单只算重跑部分。
- 侧栏（列表）+ 图（辅助）双呈现，同一份数据。

**可借鉴**：失败重跑/单步重跑/全部重跑三级入口；确认框含下游影响；attempt 切换器。

### 1.9 Buildkite

新版 build 页是 2025-2026 迭代重点（[changelog 266](https://buildkite.com/resources/changelog/266-introducing-the-new-build-page-engineered-for-scale-and-flexibility/)、[271](https://buildkite.com/resources/changelog/271-responding-to-feedback-for-the-new-build-page/)、[build-page docs](https://buildkite.com/docs/pipelines/build-page)）：

- **侧栏 = 按状态分组的步骤列表**：blocked/failed 置顶（adaptive layout），Passed 组可折叠且**折叠偏好跨 build 持久化**；失败步骤加淡红底。这是「纵向列表如何应对规模」的最好样本。
- **内容区三视图合一**（Steps 标签内切换，per-user 记忆）：**Canvas**（结构图，官方自己承认大规模缩小后没用）/ **Table**（全 job 可排序表）/ **Waterfall**（时序性能）。
- **Waterfall 的三段条**：灰=等 agent、黄=派发、绿或红=执行（hover 出每段时长）；group/matrix/parallel 嵌套成父子行，父行条色=子行聚合（[waterfall docs](https://buildkite.com/docs/pipelines/insights/waterfall)）。
- **重试呈现**：侧栏有 retry 指示，步骤详情有 **retry selector 在 attempt 间切换**；build 列表里也能展开看历史 attempt，可全局开关 “Show past retry attempts”（[changelog 372](https://buildkite.com/resources/changelog/372-view-previous-job-attempts-in-the-build-list/)）。
- **job 详情抽屉可停靠**：底部/右侧/居中三种停靠位置，记忆用户选择。
- 性能：canvas/table 全虚拟化渲染。

**可借鉴**：状态分组置顶 + 折叠持久化；waterfall 三段条（映射五阶段）；retry attempt 切换器；详情抽屉停靠。

### 1.10 GitLab CI 与 Argo Workflows（简）

- **GitLab**：流水线图按 **stage 分列**（列即拓扑层，不需要布局算法）；`needs` 关系另开 **Needs 标签**只画有依赖关系的 job；**点击节点高亮它依赖的全部路径**（[DAG docs](https://doc-zip-test.staging.gitlab.io/ee/ci/directed_acyclic_graph/index.html)）。设计讨论（[issue #31877](https://gitlab.com/gitlab-org/gitlab/-/work_items/31877)）明确：DAG 视图只显示状态和名字，别的都不画。
- **Argo Workflows**：为速度把 dagre 换成 Coffman-Graham 表格布局，代价是 zigzag 视觉伪影（[issue #3884](https://github.com/argoproj/argo/issues/3884)），重试的虚拟节点会占层导致错位，最终靠「节点过滤器隐藏 Retry 节点」缓解——**反面教材：图布局的工程坑远多于预期**。Kubeflow 也专门把 Retry 虚拟节点从 DAG 过滤掉（[PR #4474](https://github.com/kubeflow/pipelines/pull/4474)）。
- **MLflow（run 对比参照系）**：多选 run → Compare 页：**参数对比表默认隐藏全同项（“Show diff only”）**、表格可折叠限高、平行坐标图看参数-指标关联（[对比指南](https://apxml.com/courses/data-versioning-experiment-tracking/chapter-3-tracking-experiments-mlflow/comparing-mlflow-runs)、[PR #5306](https://github.com/mlflow/mlflow/pull/5306)）；用户反馈强烈要求转置表（run 多时横向滚动痛苦）（[issue #7980](https://github.com/mlflow/mlflow/issues/7980)）——我们 fork 数少（2-4 个），一列一 fork 正合适。

---

## 2. 六个问题的横向结论

### 2.1 DAG 怎么画：布局、着色、视觉语言；列表 vs 图

**布局算法光谱**（按工程成本升序）：
1. **无布局**——列表/网格（Seqera Processes、Airflow Grid、Buildkite 侧栏、Temporal Timeline）。
2. **拓扑分列**——GitLab stage 列、GitHub Actions 小图；层=列，层内顺排，不解交叉最小化。
3. **分层布局库**——dagre（Dagster，打补丁 + tight-tree ranker + IndexedDB 缓存）、ELK（React Flow 生态、新版 Airflow Graph）；节点数百上千才需要。
4. **快而丑**——Argo 的 Coffman-Graham，为性能牺牲对齐，产生 zigzag 伪影且回不了头。
5. **自定义时序布局**——Prefect 的 temporal 布局（x=时间，Web Worker 计算，WebGL 渲染）。

**状态着色公约数**：绿=成功、红=失败、蓝/青或亮绿=运行中、黄=待重试/警示、灰=未跑/跳过、橙=**上游失败（区别于自身失败）**、粉=skipped（Airflow）。三个成熟教训：running 别用「浅一点的成功绿」（暗示必然成功 + 深色模式难分）；failed 与 upstream_failed 必须拉开（色盲 ΔE 投诉）；**不能只靠颜色**——GitHub/Buildkite 全部同时用形状图标（✓ ✗ ● 旋转圈），Snakemake 用线型（虚线）。

**列表 vs 图的取舍**（几乎所有系统都是两者共存、列表为主）：
- 列表优点：信息密度高、可滚动、状态分组置顶、易做行内操作、天然可访问（role=list）；缺点：表达不了分叉汇合。
- 图优点：结构一目了然、失效传播路径可视；缺点：布局工程大、大图缩小即失效（Buildkite 官方承认）、交互（缩放/平移/选中）成本高。
- 分工口诀（Airflow）：**图管结构、网格管时间**。
- **对 11 步近线性 DAG**：整幅图的信息增益只有 2 条跨步边，代价却是一整套画布交互。业界同构物（Seqera：进度列表 + 无实时图）指向同一结论：**列表保留为主体，把「图」压缩成列表左侧的一条依赖轨道（rail）**，跨步边画成绕行圆弧——git log --graph 与 GitLab needs 高亮的杂交。

### 2.2 缓存命中 / 跳过 / 重跑的可视化

| 系统 | 机制 | 视觉语言 |
|---|---|---|
| Nextflow/Seqera | task hash 命中 + 产物校验 → CACHED | 状态卡计数（与 Succeeded 并列）+ 任务表 hash 列 + 报告聚合 |
| Snakemake | 五类 rerun-trigger 判定「无需重跑」 | DAG 中虚线框节点；dry-run 每作业打印 reason 文本 |
| Dagster | 数据版本未变 → 下游不 stale；变了 → Unsynced 标签 | 黄色标签 + 原因 popover（可点上游）+ 新旧 code_version 对照 |
| Prefect | Cached 状态 | 图上显式渲染 cached 节点（曾隐藏，被用户要求加回） |
| CI（GH/BK） | skipped/broken | 灰色图标、占位但弱化 |

共识提炼：
1. **跳过≠隐身**。缓存命中的步骤必须以第三态显式出现（Prefect 的回归、Snakemake 的虚线）。
2. **计数先行**。顶部一条「N 重算 · M 缓存 · K 失败」的汇总（Seqera 状态卡）让用户一眼判断这次 resume 值不值。
3. **命中的证据要可见**：hash/指纹列（Seqera task hash、我们的三段指纹）让「为什么跳过」可查证。
4. **不命中的原因要分类**：Snakemake 的 params/input/code/software-env 与 Dagster 的 cause 列表，粒度都与我们三段指纹对齐——**这是行业趋同，不是巧合**。

### 2.3 产物血缘视图

- **Dagster 模式（最完整）**：产物（资产）自己有详情页；Events 标签 = 物化历史，每条物化记录携带（run 链接、code_version、data_version、自定义元数据）；点 stale 原因跳上游资产；列级血缘再往下钻。
- **Seqera 模式**：run 详情的 Inputs/Outputs 标签给每个文件挂 lineage 记录（Nextflow 25.04 的 lid:// 数据血缘）；Containers 标签管环境溯源。
- **Prefect 模式**：artifact 以 key 聚成版本序列，点进看历史版本 + 反向链接到产生它的 run/task。
- **交互公约数**：产物 → 点击 → （产生它的步骤/运行、当时的参数与代码版本、上游输入的版本）→ 再点上游继续追。即**每一跳都是链接，不是静态文本**。
- 我们现状：audit 面板的 provenance 树已实现「节点→点击跳流水线该步」（`gotoStep`），方向正确；差距在于 files 面板的产物预览还没有把「上游指纹」渲染成可点链接，且没有「该产物的历史版本」概念（fork 之后会自然产生）。

### 2.4 步骤详情页的信息架构

各系统分区惊人一致，归纳为标准模板：

1. **头部**（不滚动）：状态徽章 + 名称 + 本次耗时 / attempt 计数（Seqera 任务对话框、Temporal 事件组头、Buildkite 抽屉头）。
2. **参数区**：Airflow 的 Rendered Template（渲染后的实际值！不是模板）、Prefect 的 Parameters 独立签、Seqera 的 Configuration（解析后最终配置）。**共识：展示生效值而非声明值**——我们 `buildCmd` 的等价命令行就是这个思想。
3. **日志区**：默认尾部 + 过滤（Seqera 实时 log 子签、Airflow Logs 签）。
4. **产物区**：本步输出列表 + 每个可点（Metaflow cards、Prefect artifacts、Seqera Outputs）。
5. **耗时/资源区**：本步历史耗时对比（Airflow Task Duration）、阶段剖分条（Nextflow timeline 灰+彩、Buildkite waterfall 三段）。
6. **重试历史区**：attempt 选择器切换每次尝试的日志与状态（Buildkite retry selector、Airflow try_number、GitHub attempt 下拉、Temporal pending 行的 attempt+下次重试时间）。
7. **操作区**：重跑/标记/备注（Airflow notes——重跑理由入审计）。

我们 `stepDetail` 已有 1、2、部分 4（产物路径文本）与等价命令；缺 5（实际耗时 vs 预估）、6（attempt 历史）、7 的影响预览，产物未链接到影像/文件面板。

### 2.5 运行历史对比

- **MLflow**（参数 diff 的标准答案）：多选 run → 对比页；**参数表默认只显示有差异的行**（“Show diff only”）；全同参数折叠隐藏；表格限高滚动。用户强需求：run 作列、参数作行（转置），少量 run 时最readable。
- **Airflow**（时间维标准答案）：Grid 顶部 run 时长柱状图（一列一 run）；Task Duration 折线（一线一任务，x=历次 run）——**「每步耗时随 run 变化」比「两个 run 总耗时差」信息量大**。
- **Temporal**：不做 diff，但 “Start Workflow Like This One” 预填参数（fork 的产生端）+ reset 后新旧执行互链（fork 的关系端）。
- **GitHub**：attempt 下拉在同一页内切换历次尝试——轻量对比的下限方案。
- 综合出 fork 试探对比的设计：**列=fork、行=11 步的矩阵**（Airflow Grid 语义）；单元格 = 状态色 + 缓存标记；**首个分歧步**（指纹开始不同的行）画分界线；辅以参数 diff 表（只看差异）与每步耗时成对条形图。

### 2.6 失败重试 / 部分重跑的操作入口

分层归纳（从轻到重）：
1. **原地单步重试**：GitHub job hover 重跑图标 / Buildkite 步骤 retry 按钮。前提：该步产物自足。
2. **只跑失败**：GitHub “Re-run failed jobs” / Seqera Resume / Dagster “Materialize unsynced”——**系统算范围，用户不选**。这是最常用入口，应最显眼。
3. **从某步起级联重跑**：Airflow Clear+Downstream / Temporal Reset。共同仪式：**弹出影响集预览（列出所有将重跑的下游）→ 用户确认**；Temporal 加一条：**必填 reason 记入历史**。
4. **改参重跑**：Seqera Relaunch（可改参数，从头）/ Temporal Start-like-this-one（预填开新）。对应我们的 fork。
5. **豁免不跑**：Airflow Mark-as-success + note / Snakemake cleanup-metadata / consider-ancient。承认「用户比系统知道得多」的出口，须留审计痕迹。

教训：默认范围保守（Airflow downstream 默认勾选被骂到改）；范围选项控件必须长得像开关不像标签页；重跑后 attempt 历史必须保留可回看。

---

## 3. 可借鉴机制排行（按 价值/成本 排序）

| # | 机制 | 出处 | 为什么适合我们 | 预估成本 |
|---|---|---|---|---|
| 1 | **STALE 原因链 popover**：失效标签 hover 展开「第 6 步方法变更 → 沿 #7 #8… 级联」，上游步骤名可点击跳转 | Dagster staleStatusCauses | 三段指纹已能算出原因（method+params / 上游 / 环境），只差呈现；直接把「失效传播」从一段说明文字变成每步的可查证据 | 低（数据已有，新增 popover 渲染） |
| 2 | **重跑影响集预览**：「从第 5 步重跑」→ 先列出将失效重算的 #6-#11、逐步预估耗时合计 → 确认 | Airflow Clear 弹窗 + GitHub 部分重跑确认框 | 防误触大计算（契合本机重型计算管控规则）；deps 闭包一行代码可算 | 低-中 |
| 3 | **跳过/缓存第三态视觉 + 计数条**：resume 时命中步骤画空心节点 + ↷「缓存命中」徽章；顶部「3 重算 · 7 缓存 · 1 失败」 | Seqera 状态卡 / Snakemake 虚线 / Prefect cached 上图 | 「断点续跑只重跑受影响段」目前只是承诺文案，此机制让它每次运行都自证 | 低 |
| 4 | **纵列 + 依赖轨道（rail）**：列表左缘一条 SVG：主干 1→11 直线，2 条跨步边（1→3、7→11）画绕行弧；点选步骤高亮其上游链 | git-graph / GitLab needs 高亮 / GitHub 小图 | 用 ~24px 宽度表达全部拓扑与传播路径，保留列表全部优点；失效传播动画（flashSteps）可沿 rail 波及 | 中（约 100-150 行 SVG 生成） |
| 5 | **attempt 历史 + 续跑区分**：步骤详情加「尝试 1 失败 12:03 · 尝试 2 续跑成功 12:41」切换器 | Buildkite retry selector / Airflow try_number | 五阶段状态机 + interrupted/orphaned 已有断点语义，缺每次尝试的留痕展示 | 中（需状态层记录 attempts） |
| 6 | **fork 对比矩阵**：列=fork、行=11 步；格=状态+缓存；标注首个分歧步；配「只看差异」参数表 | Airflow Grid + MLflow show-diff-only | fork 参数试探的结果目前无处可看；矩阵天然回答「两个 fork 从哪步开始不同、各自复用了多少缓存」 | 中-高（新视图） |
| 7 | **每步耗时条（阶段剖分 + 跨 run 对比）**：本步条形分五段=五阶段状态机；叠加上次 run 的虚线基准 | Nextflow timeline 灰+彩 / Buildkite waterfall 三段 / Airflow Task Duration | 五阶段目前只有状态字，剖分条让「卡在哪个阶段」可见；对比基准回答「这次为什么慢」 | 中 |
| 8 | **Resume / Relaunch 双按钮语义**：「续跑（吃缓存）」与「改参数重算（fork）」入口分开命名 | Seqera | 已有对应后端语义，防止用户混淆两种成本完全不同的操作 | 低（文案与按钮组织） |
| 9 | **重跑理由备注**：手动重跑/豁免时可写一句话，落入 trace | Airflow notes / Temporal reset reason | 与 OpenDiscoveryTrace 的 revision_trigger 字段天然对接，论文素材 | 低 |
| 10 | **产物版本历史**：同一产物路径在多次 run/fork 下的版本序列，点开看各版本的指纹与生成参数 | Prefect artifact key 版本 / Dagster Events | fork 落地后自然需要；files 面板已有 hash 列可扩展 | 高（需产物存储分版本） |

**明确不建议引入**：dagre / ELK / React Flow 整图方案（节点数固定、拓扑编译期已知，库的全部价值——未知规模的交叉最小化——对我们为零，反而引入依赖、画布交互与可访问性负担；Dagster 为它付出 patch+worker+IndexedDB 三重工程，Argo 为性能弃 dagre 又产生 zigzag 悬案）；Prefect 式 WebGL 画布（杀鸡用牛刀）；径向/新奇布局（Prefect Radar 已死）。

---

## 4. 落地建议（映射 `prototype/js/dock.js`）

### 4.1 流水线面板主体：保留纵向列表，加依赖轨道

现状：`pipelineView()` 渲染 `.pipe` 列表，每行 `.pstep` 按钮（序号圈 + 名称 + 状态文案），`STATE_CLS` 七态着色，选中行内嵌 `stepDetail`。

建议改造（不换骨架）：
- 在 `.pipe` 容器左缘加一条**绝对定位的 SVG rail**（宽约 24-28px，高随列表）：
  - 11 个节点圆点对齐每行垂直中心（行高固定或渲染后读 `offsetTop`）；
  - 主干直线贯穿 1→11；两条跨步边 `1→3`、`7→11` 用二次贝塞尔弧从左侧绕行（lane 1），恰好一条备用车道就够——布局是**手写常量**，不需要任何算法；
  - 节点视觉复用现有语义：done=实心绿、running=描边动画、stale=黄 + `!`、failed=红 ✗、pending=灰空心、**缓存跳过=空心绿 + 内部 ↷**（新增第八种 `skipped` 样式，见 4.3）；
  - rail 设 `aria-hidden="true"`，可访问性仍由 DOM 列表承担（现有 `role="list"` 不动）——图形与语义分离，规避 SVG 无障碍难题。
- **上游链高亮**（GitLab needs 交互）：选中第 k 步时，rail 上把其依赖闭包的节点与边加粗高亮，其余降透明度；`gotoStep`/`flashSteps` 现有机制直接复用，STALE 级联动画改为「沿 rail 依次点亮」。

### 4.2 失效传播展示：从说明文字到原因链

现状：面板底部一段静态 blurb（“改动任一步的方法或参数 → 重算 sha256 指纹 → 级联标记 STALE”）+ `flashSteps` 闪烁。

建议：
- 每个 stale 步骤的行尾状态文案旁加小 `(i)`，点击/hover 弹 **原因 popover**（Dagster 模式），内容由三段指纹 diff 生成，三类文案对齐 Snakemake rerun-triggers：
  - `方法/参数变更`：「第 6 步 method snaphu→icu · min_coherence 0.30→0.35」；
  - `上游失效`：「上游 #5 指纹已变（a3f2…→b871…）」——**#5 渲染为链接**，点击 `gotoStep(5)`；
  - `环境变更`：「SNAPHU 2.0.7→2.0.8」。
- `stepDetail` 的指纹 kv 行升级为**三段展开**：`method+params 段 / upstream 段 / env 段` 各自显示短 hash 与「变了/没变」徽章——用户能看出「这次失效是哪一段引起的」（Dagster 新旧 code_version 对照的等价物）。

### 4.3 缓存命中/跳过的行内语言

- `STATE_CLS`/`STATE_TXT` 增加 `skipped`（或复用 done+修饰）：文案「↷ 缓存命中 · 未重跑」，行内附指纹短 hash 作为证据；rail 节点空心。
- 列表顶部（`h3.sect` 下方）加一条**运行摘要条**：`本次运行：3 重算 · 7 缓存命中 · 1 失败`（Seqera 状态卡的单行版）；徽章逻辑并入现有 `paintBadges()`。
- 失败步下游未跑的步骤，状态文案区分「待运行」与「**上游失败 · 被阻塞**」（Airflow upstream_failed 教训：被牵连≠自身失败，且不能只靠颜色区分——加文字）。

### 4.4 步骤详情分区补齐

现状 `stepDetail`：状态注记 + 方法下拉 + 参数表单 + 指纹/依赖/预估/产物 kv + 等价命令。按 §2.4 模板补三块：

- **耗时区**：`预估 24 min · 实际 31 min（上次 22 min）`；一根五段迷你条剖分五阶段耗时（数据来自状态机时间戳）——Nextflow/Buildkite 分段条的移植，SVG 直接用 `figures.js` 风格手写。
- **尝试历史区**：`尝试 2（续跑）✓ 08-12 12:41 · 尝试 1 ✗ OOM 12:03`，点击切换该次日志（联动 term 面板的 `termLive.step` + attempt 参数）。
- **操作区**：三个明确按钮——`续跑`（interrupted/orphaned 时；吃缓存）/ `从此步重跑`（触发 4.5 预览）/ `另存为 fork`（预填当前参数，Temporal start-like-this-one 语义）。豁免操作（「确认此变更不影响结果」）可后置，但设计时留位。

### 4.5 重跑影响预览（新增小组件）

- 点「从此步重跑」→ 行下方展开（或小模态）：**受影响步骤列表**（deps 闭包内所有下游 + 自身），每行「步骤名 · 当前状态 → 将重算 · 预估时长」，底部合计 `预计 92 min · 磁盘增量 ~40 GB`，`确认重跑` 为红色/主色按钮 + 可选**一句话理由**（落 trace 的 revision_trigger）。
- 默认范围 = 自身 + 已 STALE 的下游（Dagster materialize-unsynced 语义）；「连同未失效下游一起强制重跑」做成显式开关，默认关（Airflow 默认勾选 downstream 的教训）。

### 4.6 fork 参数试探的对比视图（后续里程碑）

- 新增（或并入流水线面板的切换器）**对比矩阵**：行=11 步、列=主线+各 fork；格子=状态色块 + 缓存 ↷ 角标；**首个指纹分歧行**画一条水平分界线并标注「fork 自此分岔：alpha 0.5→0.6」。
- 配套**参数 diff 表**：默认只显示有差异的参数（MLflow show-diff-only），行=参数、列=fork。
- 配套**每步耗时成对条**：横向双条并列（主线 vs fork），SVG 手写，复用 `timeSeriesSvg` 的坐标轴习惯。

### 4.7 零依赖 SVG 可行性评估

**结论：完全可行，且是当前约束下的最优选。**

- **规模**：11 节点、12 条边（10 主干 + 2 跨步）、拓扑静态。分层布局库解决的问题（未知规模的层分配、交叉最小化、边路由）在这里退化为常量表；Dagster/Argo 的全部布局工程（dagre 补丁、tight-tree、IndexedDB 缓存、Coffman-Graham 取舍）都是「规模不可知」税，我们免税。
- **先例**：`figures.js` 已手写时序折线、地图、干涉条纹等更复杂的 SVG（含渐变、事件交互——`mapSvg` 的 `.pt` 点击/键盘模式即成熟范式），rail 的复杂度低于其中任何一张图。
- **工程量预估**：rail 生成器约 100-150 行（节点/边/高亮态）；原因 popover 约 40-60 行（可复用现有 `.note`/`tag` 样式）；影响预览约 60-80 行；五段耗时迷你条约 30 行；fork 矩阵约 150-200 行。全部合计与 `figures.js` 单文件体量相当。
- **交互模式**：沿用「DOM 承担语义与焦点、SVG 承担图形」的分工——rail `aria-hidden`，点击目标仍是 `.pstep` 按钮；popover 用现有 hidden 切换即可，无需浮层库。
- **动画**：STALE 级联沿 rail 传播用 CSS class + `void offsetWidth` 重排触发（`flashSteps` 现成套路）；running 节点用 SVG `stroke-dasharray` 动画，零 JS 帧循环。
- **风险与边界**：a) 行高变化（步骤详情展开在选中行下方）会使节点错位——rail 需在 `refresh()` 后按各行实际 `offsetTop` 重算 y 坐标（一次 DOM 读取循环，代价可忽略）；b) 若未来步骤数变成可配置/子步骤展开，跨步边超过 3-4 条时手排 lane 会开始吃力——届时再考虑最小实现一个「区间图贪心 lane 分配」（~30 行），仍无需引库；c) 打印/导出场景 rail 随 HTML 走，无额外处理。

### 4.8 与现有面板的联动清单

- audit 面板 provenance 树的 `gotoStep` 跳转保留；反向补一条：流水线步骤详情的「产物」行渲染为链接 → `openFile(path)`（函数已导出，未接线）。
- 影像面板 `gcard` 已显示 stale/指纹；缓存命中态（4.3）落地后同步该面板的 `tag` 文案（「有效 · 本次未重算」vs「有效 · 本次重算」）。
- term 面板日志按 attempt 分文件后，4.4 的尝试历史直接以 `logs:{run}:{step}:{attempt}` 为缓存键读取。

---

## 5. 主要参考链接

- Seqera run details：https://docs.seqera.io/platform-enterprise/monitoring/run-details ；缓存与恢复：https://docs.seqera.io/nextflow/cache-and-resume ；新 run 页博客：https://seqera.io/blog/new-seqera-products-boston-summit-2025/
- Nextflow tracing/报告/时间轴/DAG：https://dokk.org/documentation/nextflow/v23.04.4/tracing/ ；ReportObserver（onTaskCached）：https://github.com/nextflow-io/nextflow/blob/master/modules/nextflow/src/main/groovy/nextflow/trace/ReportObserver.groovy
- Snakemake CLI（--dag/--rulegraph/--filegraph/--rerun-triggers）：https://snakemake.readthedocs.io/en/stable/executing/cli.html ；报告：https://snakemake.readthedocs.io/en/latest/snakefiles/reporting.html ；重跑行为公告：https://github.com/snakemake/snakemake/issues/1694
- Dagster 资产版本与缓存：https://docs.dagster.io/guides/build/assets/asset-versioning-and-caching ；stale 原因 UI：https://github.com/dagster-io/dagster/commit/a1b4eead27e0bd611cfef47275090b77a9cec349 、https://github.com/dagster-io/dagster/pull/14840 、https://github.com/dagster-io/dagster/pull/13529 ；stale 语义讨论：https://github.com/dagster-io/dagster/discussions/13102 ；DAG 规模化：https://dagster.io/blog/scaling-dag-visualization ；webserver/资产详情页：https://github.com/dagster-io/dagster/blob/master/docs/docs/guides/operate/webserver.md
- Prefect 运行图重写：https://github.com/PrefectHQ/prefect/pull/11112 ；图渲染架构：https://github.com/PrefectHQ/prefect/blob/81d5e547/ui-v2/src/graphs/AGENTS.md ；artifacts：https://docs.prefect.io/v3/concepts/artifacts
- Airflow UI 总览：https://airflow.apache.org/docs/apache-airflow/stable/ui.html ；旧版视图截图（Task Duration/Landing/Gantt）：https://airflow.apache.org/docs/apache-airflow/2.9.3/ui.html ；重跑指南：https://astronomer.io/docs/learn/rerunning-dags ；状态色定制：https://airflow.apache.org/docs/apache-airflow/2.1.3/howto/customize-state-colors-ui.html ；downstream 默认值争议：https://github.com/apache/airflow/issues/53500 、https://github.com/apache/airflow/pull/67763
- Temporal Web UI：https://docs.temporal.io/web-ui ；UI 重设计：https://temporal.io/blog/the-dark-magic-of-workflow-exploration ；Timeline/History 双签：https://github.com/temporalio/ui/pull/3109 ；Reset：https://github.com/temporalio/ui/pull/1046 、https://docs.temporal.io/develop/typescript/workflows/cancellation
- Metaflow UI 开源博客：https://netflixtechblog.com/open-sourcing-a-monitoring-gui-for-metaflow-75ff465f0d60 ；cards：https://docs.metaflow.org/metaflow/visualizing-results
- GitHub Actions 可视化图：https://docs.github.com/actions/managing-workflow-runs/using-the-visualization-graph ；部分重跑：https://github.blog/news-insights/product-news/save-time-partial-re-runs-github-actions/
- Buildkite build 页：https://buildkite.com/docs/pipelines/build-page ；waterfall：https://buildkite.com/docs/pipelines/insights/waterfall ；新 build 页 changelog：https://buildkite.com/resources/changelog/266-introducing-the-new-build-page-engineered-for-scale-and-flexibility/ 、https://buildkite.com/resources/changelog/271-responding-to-feedback-for-the-new-build-page/ 、https://buildkite.com/resources/changelog/372-view-previous-job-attempts-in-the-build-list/
- GitLab DAG/needs 视图：https://doc-zip-test.staging.gitlab.io/ee/ci/directed_acyclic_graph/index.html ；设计讨论：https://gitlab.com/gitlab-org/gitlab/-/work_items/31877
- Argo 布局取舍：https://github.com/argoproj/argo/issues/3884 、https://github.com/argoproj/argo-workflows/issues/3595
- MLflow run 对比：https://apxml.com/courses/data-versioning-experiment-tracking/chapter-3-tracking-experiments-mlflow/comparing-mlflow-runs ；show-diff-only：https://github.com/mlflow/mlflow/pull/5306 ；对比页反馈：https://github.com/mlflow/mlflow/issues/7980
- 布局引擎（对照组，不建议引入）：elkjs https://github.com/kieler/elkjs ；React Flow + ELK 范式 https://reactflow.dev/examples/layout/elkjs
