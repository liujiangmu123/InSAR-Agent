# ROADMAP:教学模式(设计文档,未实现)

> 读者:产品决策者与后续实现者。本文是**方案设计**,不含任何已落地代码;
> 所有「改动面」小节都对应到现有模块的具体文件,估算按现有代码形态给出。
> 背景:用户本人是高校定职讲师,InSAR 处理是研究生/高年级本科课程内容,
> 产品的模拟引擎、事件流回放式 UI、证据阶梯与场景包天然构成教学素材。
> 调研日期:2026-08-13;外部链接均为当日检索可达版本。

---

## 0. 为什么做教学模式:现有能力 → 教学资产的映射

先盘点:本项目为「可复现科研」建的每一件东西,几乎都能在教学场景二次变现——

| 现有能力 | 所在模块 | 教学场景的二次价值 |
|---|---|---|
| NDJSON 事件流 + 与真流同构的 `consume()` | `loop/events.py` / `loop/driver.py` / `prototype/js/app.js` | 录一次课堂演示,处处可回放;断网教室零后端重演 |
| 诚实模拟模式(引擎缺失走合成执行,显式标注 simulated) | `engines/simulate.py` | 机房没有 conda/WSL 也能让全班跑通 11 步流程 |
| run fork(参数试探分支,未受影响步骤零重算) | `planner/plan.py::fork_run` / `POST /api/fork` | 学生试参数的安全沙箱:父 run 天然不可污染 |
| 六级证据阶梯(取最差、simulated 封顶 runnable) | `audit/ladder.py` | 任务卡的客观目标:「把这个场景做到 audited」 |
| 真实 qa 指标(重解析,绝不编造) | `engines/qa.py` / `audit/verify.py` | 自动评分的分数来源:指标 vs 容差带 |
| 阈值台账(每个阈值带文献 ref) | `audit/contract.yaml` | 「为什么」浮层的参数依据素材,引文即教材 |
| 场景包 SKILL.md(领域知识正文:选参依据/模型设定/质量门侧重) | `registry/scenario_packs/*/SKILL.md` | 「为什么」浮层与讲稿的正文素材,已有 4 个场景 |
| 候选收窄解释(narrowed,带理由) | `planner/plan.py`(PlanStep.narrowed)| 「为什么不选别的方法」——方法论教学的反面教材 |
| run.sh 等价命令 + 方法章节 + provenance | `report/script.py` / `report/methods.py` / `core/ledger.py` | 参考答案的「复现包」;学生作业的可验收产物 |
| 失效传播 + 影响预估(affected/rerunMinutes) | `core/stale.py` / `GET /api/impact` | 课堂演示「改一个参数,下游哪些步作废」 |
| PAUSE/PLAY 干预 + 步间生效语义 | `loop/driver.py`(paused 状态)/ `POST /api/actions` | 现场演示的逐步讲解,机制已存在,零新开发 |
| Ridgecrest 实测:HyP3 路线全链 38 秒 | `scripts/real_ridgecrest.py` | **一节课内可以现场跑真数据**,不是只能放录像 |

内部先行设想(本方案与之对齐,不另起炉灶):

- `reference/RESEARCH-agent-ux-2026-08-12.md` §6.3/§10.4:Manus Replay 调研与回放技术路径
  (「事件流本就是 NDJSON,落盘存档 + 回放模式重演 consume();讲师场景加成」),第二梯队条目 #10;
- `docs/AGENT-DESIGN.md` §7.3(Dock 弹出独立窗口支持双屏演示)与 §8.3「P2:教学与论文增强」
  (失效级联动画回放——课堂演示「改参数如何失效」、InSAR 任务评测集构造)。

---

## 1. 竞品/参照调研:GIS/遥感教学软件的课堂交互范式

### 1.1 Esri Learn ArcGIS / Classroom Activities Gallery(lesson 模式)

Esri 的课堂资源以「lesson」为单位组织,经 [Classroom Activities Gallery](https://community.esri.com/t5/education-blog/find-a-lesson-for-your-class/ba-p/1613733) 分类检索,每课配教师指南与可编辑的学生活动材料。抽样其公开 lesson PDF(如 [Measuring Fires](https://www.esri.com/content/dam/esrisites/en-us/media/pdf/teach-with-gis/measuring-fires.pdf)、[Zaatari Refugee Camp](https://www.esri.com/content/dam/esrisites/en-us/media/pdf/teach-with-gis/zaatari.pdf)、[Visualizing an E. Coli Outbreak](https://www.esri.com/content/dam/esrisites/en-us/media/pdf/teach-with-gis/visualizing-an-ecoli-outbreak.pdf)),lesson 的固定骨架是:

- **元数据头**:Lesson Overview / Build Skills in These Areas(技能点列表)/ Software Requirements / Estimated Time(30 min–1 h);
- **步骤正文**:编号步骤 + 截图,嵌入编号问题(Q1、Q2…);
- **教师版答案带容差**:如 Zaatari 测面积题的参考答案明确写「5,127,866 m² 允许 ±5% 浮动,重点是证明学生会用工具」——**容差带评分**而非精确匹配;
- **脚手架递减**:Measuring Fires 第二个案例刻意「用更笼统的措辞写步骤,迫使你回忆流程」——同一工作流从跟做到独立完成。

### 1.2 QGIS Training Manual(开源手册生态)

[QGIS Training Manual](https://docs.qgis.org/3.10/en/docs/training_manual/index.html) 是「模块 → 课 → 小节」三层结构,设计目标是支撑 5 天线下课。其[贡献规范](https://docs.qgis.org/3.10/en/docs/training_manual/appendix/contribute.html)把小节分成两类刚性类型:

- **Follow Along(跟做)**:逐步指令 + 截图;
- **Try Yourself(自试)**:短作业,带难度标记(basic/moderate/hard),每题关联文末 **answer sheet** 的锚点条目(做法说明 + 期望结果),正反向链接。

手册同时维护「Preparing Exercise Data」附录——练习数据的制备本身是课程包的一部分。

### 1.3 Jupyter nbgrader(作业发布与自动评分)

[nbgrader](https://nbgrader.readthedocs.io/en/latest/user_guide/highlights.html) 是 Jupyter 生态的作业系统,其[创建与评分工作流](https://nbgrader.readthedocs.io/en/stable/user_guide/creating_and_grading_assignments.html)与[收发机制](https://nbgrader.readthedocs.io/en/stable/user_guide/managing_assignment_files.html)提供了最完整的「师生双版本 + 自动评分」范式:

- **source/release 双版本**:教师版含 `### BEGIN SOLUTION` 区块与隐藏测试;`generate_assignment` 剥离答案生成学生版(答案区变代码桩);
- **测试即评分**:autograder tests 单元格带分值,`autograde` = 重跑整个 notebook 看哪些测试报错;隐藏测试学生不可见,防「面向测试编程」;
- **validate 自查**:学生提交前一键跑可见测试,当场知道过没过;
- **exchange 收发**:release → fetch → submit → collect → feedback 的目录协议;
- **教师侧校验纪律**:`nbgrader validate` 确认教师版全过,`validate --invert` 确认学生版(未作答时)全不过——正反两个方向都要验。

### 1.4 EO College / ESA:InSAR MOOC 用什么教

- [Echoes in Space](https://eo-college.org/courses/echoes-in-space/)(ESA × EO College 的雷达遥感 MOOC):5 周结构,每周「Topics + Quizzes」(如 History 11 Topics/5 Quizzes),强调 hands-on:用免费软件(SNAP)处理真实数据([ESA 介绍页](https://eo4society.esa.int/resources/echoes-in-space/)),完课发证书。**范式:知识单元 + 嵌入式小测 + 真数据实操三件套**;
- EO College 的 [InSAR 资源分类页](https://eo-college.org/resource-category/methods/insar/) 汇聚了 SAR-EDU 时代的单元化教学资源(InSAR Basics / InSAR Error Sources / DEM generation with Python 等)——碎片化「单元(Unit)+ 教程(Tutorial)」可自由组课;
- [DLR/ESA PolInSAR 训练课](https://eo4society.esa.int/event/8th-edition-of-the-dlr-esa-open-polinsar-training-course-2024/)(11 周,[2025 版概览 PDF](https://eo4society.esa.int/wp-content/uploads/2025/02/ThePolInSARCourse-2025-N9-Overview-v2-002.pdf)):跑在 ESA MAAP 云平台上,「参与者零硬件/软件要求」——**云端零装机是高级课程的标配**,把环境问题从课堂里消灭。

### 1.5 EarthScope(UNAVCO)ISCE+ 短训班与 ASF OpenSARLab

研究生级 InSAR 实操教学的事实标准:

- [EarthScope 2024 InSAR Processing and Analysis (ISCE+) 短训班](https://www.earthscope.org/event/2024-insar-processing-and-analysis-isce-short-course/)([2022 版](https://www.unavco.org/event/2022-short-course-insar-processing-analysis-isce/)):5 天,Zoom 讲座 + ASF OpenSARLab/OpenScienceLab 云端 JupyterHub 实操(ISCE2 → ARIA-tools/HyP3 → MintPy 全链),配 pre-course 自学模块、office hours 与 Slack;
- [ASF OpenSARLab 介绍](https://www.earthdata.nasa.gov/s3fs-public/2023-01/ASF_OpenSARLab_Webinar_1_25_23.pdf):AWS 上的 JupyterHub,自动克隆 [数据菜谱 notebook 仓库](https://github.com/ASFOpenSARlab/opensarlab-notebooks),预置 conda 环境,持久化用户卷,并提供「为班级定制部署」的服务;
- 课程主讲之一 Gareth Funning 的[教学页](https://www.garethfunning.com/training)总结了范式:「notebook 把**文档和真实命令放进同一份文件**」——这正是本项目「工具卡 + cmd.sh 即等价裸命令」已经做到的事,只是我们的载体是事件流而非 notebook。

### 1.6 GETSI(课堂模块:讲师/学生材料分离)

[GETSI「Imaging Active Tectonics with InSAR and Lidar」模块](https://serc.carleton.edu/getsi/teaching_materials/imaging_active_tectonics/index.html)是面向本科课堂(非软件训练)的成熟课程包样本:

- 5 个 Unit,可整体两周连讲、也可[单元独立使用](https://serc.carleton.edu/getsi/teaching_materials/imaging_active_tectonics/overview.html)(每单元标注 Context for Use);
- **学生材料与讲师材料严格分离**:[Unit 3](https://serc.carleton.edu/getsi/teaching_materials/imaging_active_tectonics/unit3.html) 提供学生练习 handout(.docx/PDF)与讲师专用 answer key;讨论题定位为「低风险形成性评估」;
- [Unit 4](https://serc.carleton.edu/getsi/teaching_materials/imaging_active_tectonics/unit4.html) 用交互式反演工具(Visible Earthquakes)做「全班同做一例 + 分组各做一例再拼图(jigsaw)」的课堂编排。

### 1.7 课堂投影形态(Jupyter RISE)

[RISE](https://rise.readthedocs.io/en/latest/)(Reveal.js Jupyter 扩展)是「工作界面 ↔ 演示界面一键切换」的参照:单快捷键进出放映模式,幻灯片里可以现场执行代码,支持 speaker notes 独立窗口([用法文档](https://rise.readthedocs.io/en/5.6.0/usage.html))。**启示:演示模式不是另一个应用,是同一界面的显示预设 + 交互降噪**。

### 1.8 范式归纳(对本项目的直接指导)

| # | 范式 | 出处 | 对应本方案 |
|---|---|---|---|
| 1 | 跟做/自试二元结构,脚手架递减 | QGIS、Esri | 课堂演示模式(跟做)/ 学生练习模式(自试),任务卡提示阶梯 |
| 2 | 师生双版本:答案剥离 + 隐藏测试 | nbgrader、GETSI | 课程包 `reference/` 目录默认不随学生版分发;rubric 容差带不全量公开 |
| 3 | 评分 = 容差带 + 重跑验证,非精确匹配 | Esri(±5%)、nbgrader(重跑测试) | 自动评分以 qa 指标带为主,产物指纹相等只作「完全复现」徽标 |
| 4 | validate 自查按钮:提交前学生自己能跑评分子集 | nbgrader | 练习模式「自查」按钮调评分器公开子集 |
| 5 | 云端/预置环境消灭装机问题 | OpenSARLab、ESA MAAP | 本项目等价物:simulate 引擎(零环境)+ HyP3 预处理产物(38 秒全链) |
| 6 | 文档与真实命令同载体 | OpenSARLab notebooks | 已有:工具卡 + cmd.sh/run.sh;教学模式补「为什么」层 |
| 7 | 元数据头(受众/时长/前置/目标)+ 单元可拆 | Esri、GETSI、EO College | 课程包 frontmatter 闭集字段 |
| 8 | 演示模式是显示预设,不是新应用 | RISE | 大屏模式 = CSS/交互预设,复用现有 UI |

---

## 2. 总体形态:教学模式是「一层皮 + 一种包」,不是新系统

设计原则(与 AGENT-DESIGN 七条硬约束同源):

1. **不动执行语义**:教学模式不引入任何新的执行路径;证据阶梯规则原样生效
   (simulated 封顶 runnable 在课堂上同样如实展示——诚实本身就是教学内容);
2. **回放优先于重跑**:课堂演示默认消费录好的事件档,现场跑真任务是可选增强;
3. **单一真相源**:课程包引用场景包(`scenario: quake`),不复制领域知识正文;
4. **不建账号系统**:单机部署,评分产物是本地文件(score.json/CSV),对接 LMS 是远期生态问题。

三个交付面:

- **课堂演示模式**(讲师视角,§3):回放 + 暂停点 + 大屏 + 「为什么」浮层;
- **学生练习模式**(学生视角,§4):任务卡 + fork 沙箱 + 自动评分;
- **课程包**(内容载体,§5):场景包的教学扩展,`LESSON.md + checkpoints.yaml + replay/ + reference/`。

入口设计:不新增 `sessions.mode` 枚举值(现有 `expert | guide` 表达的是**决策粒度**,与教学正交)。
教学态是会话的附加上下文:P0 阶段用前端状态(URL 参数 `?replay=` / 顶栏切换)承载;
P1 起 `sessions` 表加可空列 `lesson_key`(`core/schema.sql` 迁移),会话由课程包发起时写入。

---

## 3. 课堂演示模式(讲师视角)

### 3.1 NDJSON 回放(P0 核心)

**现状**:`Driver._emit()` 把事件发布到 EventBus 并 yield 进回合 NDJSON 流(双通道),
但**事件不落盘、不带时间戳**;`store.trace` 表记录的是 phase 级思考/动作,粒度不适合 UI 重演;
前端 `consume()` 与真流同构(`app.js` 头注释明言 mock↔SSE 可替换),这是回放的技术前提。

**设计**:

- **录**:`Driver._emit` 增加可选 file sink——事件包一层信封
  `{"ts": <epoch毫秒>, "seq": <单调序号>, "ev": {...}}`,按会话追加写
  `{workspace}/events.ndjsonl`(即 `INSAR_HOME/sessions/<id>/events.ndjsonl`)。
  信封只在档案里存在,不改在线流的事件形状——线上契约(`tests/test_e2e_contract.py`
  的事件类型注册表)零影响。写失败降级为 warning,不阻塞执行(对齐 EventBus 的隔离哲学);
- **取**:`GET /api/replay?session=…`(可选 `run_id` 过滤、`from_seq` 分片)返回事件档;
- **播**:前端新增 `replay(events)`:把数组包装成 async iterator 依 `ts` 差值 `sleep(dt)`
  逐条产出,**直接交给现有 `consume()`**——零改造复用全部渲染逻辑(工具卡、计划面板、
  审批卡、失败卡、图件灯箱都自动可回放)。控制条:播放/暂停(空格)、单步(→)、
  速度 1×/2×/4×/8×、进度轴(按 `step.start` 事件打刻度,可点击跳步);
- **离线**:回放不依赖后端——`backend.mock.js` 的 file:// 回退已证明前端可离线运行。
  「课堂回放包」= `events.ndjsonl` + 引用到的 figures 快照,拷 U 盘进断网教室即可放
  (P0 先支持「后端在、读档放」;纯离线打包放 P2 课程包生态一并做);
- **回放态防误触**:回放中 composer 禁用、干预按钮隐藏(回放是放映,不是会话),
  顶栏显式徽标「回放中 · 2026-08-12 Ridgecrest 演示」——绝不让观众误以为在真跑。

**为什么不用录屏**:NDJSON 回放可变速、可跳步、可中途切进面板看产物、字号随大屏预设缩放,
视频全都不行;且事件档是文本,进 git、进课程包、可 diff。

### 3.2 讲解暂停点

两种形态,分别覆盖「放录像讲」与「现场跑着讲」:

- **回放暂停点(P0,声明式)**:课程包 `LESSON.md` frontmatter 声明锚点列表,如

  ```yaml
  pause_at:
    - on: step.start        # 事件类型锚
      step: 6
      card: "#解缠"          # 正文标题锚:暂停时弹出该节讲解卡
    - on: gate_stop          # 质量门拦停必停:讲「为什么 agent 拒绝带病上路」
    - on: degrade            # 证据降级事件必停:讲证据阶梯
  ```

  回放器匹配到锚点事件即自动暂停,右下弹讲解卡(渲染 LESSON.md 对应小节,含公式/图);
  无课程包时退化为「每个 `step.start` 自动暂停」的通用步进开关;
- **现场暂停(P0,零开发)**:直接复用既有 PAUSE/PLAY 干预(`POST /api/actions`,
  步间生效,run 状态 `paused`,进度保留)。讲师现场跑真任务时,每步讲完按 PLAY 放行——
  机制已有测试守护,教学模式只需在大屏预设里把 PAUSE/PLAY 按钮放大置顶。

### 3.3 大屏模式(投影显示预设)

**现状**:`state.js` 有 `theme: light|dark`(localStorage 持久化,`data-theme` 属性驱动 CSS);
`a11y.js` 有全局快捷键帮助浮层与焦点管理;`dock.js` 九面板
(pipeline/images/files/audit/report/web/trace/term/env);AGENT-DESIGN §7.3 已有
「Dock 内容可弹出为独立窗口以支持双屏演示」的待办。

**设计**(纯前端,`data-display="projector"` 一个属性开关,Ctrl+Shift+P 切换):

- **字号/密度**:字号已 token 化(`prototype/css/tokens.css`:`--fs: 13.5px` 及
  xs/sm/md/lg/xl 六档)——大屏预设就是在 `[data-display="projector"]` 下整体覆写
  token 刻度(基准提到 ~20px),全 UI 自动缩放,不逐处改样式;行高与卡片间距同步放大;
  `tool.log` 默认折叠只留摘要行(投影上滚动日志不可读,点开才展开);事件流单列拉宽;
- **对比度**:投影仪灰阶压缩严重——提供 `projector-light`(白底,教室灯开)与
  `projector-dark`(黑底,教室灯关)两档,文本对比度按 WCAG AAA(≥7:1)校准,
  状态色(ok/warn/bad)加图标冗余,不只靠颜色;
- **降噪**:侧栏(会话列表)自动收起;dock 默认只留「流水线」或「影像」单面板;
  toast/通知静默(讲课时不弹「需要你的确认」的桌面通知);
- **双屏(P1)**:落实 §7.3 待办——dock 面板弹独立浏览器窗口(`window.open` +
  BroadcastChannel 同步状态):主屏投事件流,副屏(讲台显示器)开轨迹/审计面板当讲师备注,
  形态对标 RISE 的 speaker notes 窗口;
- **持久化**:`S.display` 进 localStorage,与 theme 并列;`?display=projector` URL 参数
  支持一键进入(教室机器收藏夹直达)。

### 3.4 「为什么」浮层(每步方法选择的领域知识)

**素材已经全部存在,只缺聚合与呈现**:

| 素材 | 现有位置 | 回答的问题 |
|---|---|---|
| 场景包知识正文(选参依据表/模型设定说明/质量门侧重/文献段) | `SKILL.md` 正文,`Scenario.knowledge()` 惰性读取 | 这个场景为什么这样配 |
| 候选收窄解释 | `planner/plan.py` PlanStep.narrowed(已进轨迹面板) | 为什么**不选**其他方法(如 quake 场景:linear 会把阶跃摊成假趋势) |
| 方法推荐理由与来源 | `planner/score.py` pick.reason/source(计划回合已叙述) | 为什么选中这个方法 |
| 参数阈值依据 | `audit/contract.yaml` 每阈值的 ref(文献引用 + 来源等级) | 这个数字哪来的(Berardino 2002 §V…) |
| 方法章节的引用锚点 | `report/methods.py`(〔ref:…〕社区参照注释) | 社区惯例带是什么 |

**设计**:

- **后端**:`GET /api/why?session&step=N` 聚合上述素材为一个结构化响应:
  `{ chosen: {method, reason, source}, rejected: [{method, why_not}], params: [{name, value, ref, source_level}], knowledge_md: "SKILL.md 相应小节" }`。
  SKILL.md 正文当前无 API 暴露,需在 `registry/scenarios.py` 之上加「按小节标题切片」的
  辅助函数(渐进披露原则不变:确认场景后才读正文);
- **前端**:计划面板每步与工具卡标题栏加「为什么」图标(?);点开右侧浮层三段式:
  **选了什么**(方法 + 关键参数值)→ **为什么**(reason + 台账 ref 原文,文献条目可复制)→
  **为什么不选别的**(narrowed 候选与理由)。浮层内容在大屏预设下同步放大;
- **边界纪律**:浮层只展示 provenance/registry/台账里已有的文字,不引入 LLM 现场发挥
  (narrate 的「润色不许动数字」纪律同样适用——教学场景对错误零容忍)。

### 3.5 失效级联演示(P1,课堂保留节目)

AGENT-DESIGN §8.3 已列的「失效级联动画回放」在教学模式落地为一个演示动作:
讲师在流水线面板改一个参数(如 `min_coherence` 0.25→0.4),`GET /api/impact` 返回
affected + rerunMinutes,前端把级联标脏做成 300ms 逐步点亮的动画 + 每步 stale_reason 气泡
(method_changed/param_changed/upstream_changed 的颜色区分)。这是「参数级失效传播」
这一产品 novelty 最直观的课堂表达,改动集中在 `prototype/js/pipelinerail.js`。

---

## 4. 学生练习模式

### 4.1 任务卡

任务卡是课程包 `checkpoints.yaml` 的条目,渲染成事件流里的一张常驻卡片(形态对齐现有审批卡):

```yaml
# checkpoints.yaml 条目示例(schema 详见 §5)
- id: cp-2
  title: 把 Ridgecrest 同震做到 audited
  scenario: quake                  # 引用场景包(数据/云端已完成步骤随之确定)
  given:
    from_run: reference            # 从参考 run fork(1-6 步现成:导入+云端段直接复用)
  require:
    evidence_at_least: audited     # 证据阶梯目标(audit/ladder.py 的 LADDER 序)
    metrics:                       # qa 指标容差带(engines/qa.py 产出的指标名)
      mean_coherence: {min: 0.90}
      residual_rms_mm: {max: 30}
    must_fork: true                # 必须用 fork 试参数,不许覆写(工作方式也是考点)
    export: [run.sh, methods.md]   # 要求导出的复现物
  locked_steps: [1, 2, 3, 4, 5, 6] # 不许动的步骤(导入与云端段)
  hints:                           # 提示阶梯(脚手架递减,参照 Esri/QGIS)
    - 看第 9 步的形变模型与场景机理是否匹配
    - SKILL.md「模型设定说明」一节解释了 step 与 linear 的区别
    - 阶跃日期错一天,残差空间分布会出现断层同形态残余
```

要点:

- **目标用证据级表述**,不用「做对」这种模糊词——证据阶梯是机器可判定的
  (`audit/ladder.py`),学生在审计面板实时看到自己停在哪级、为什么(reasons 字段已有);
- **环境分档**:任务卡声明 `env_mode: simulated | hyp3 | full`。模拟机房(无 conda)
  只能开 simulated 任务卡(目标封顶 runnable,考流程与决策);装了 MintPy 环境的机房
  开 hyp3 任务卡(38 秒全链,目标 audited)——参照 OpenSARLab「环境即课程一部分」的教训,
  任务卡与机房能力显式匹配,而不是让学生撞环境墙;
- **locked_steps 的执行**:干预入口校验(`POST /api/actions` 与 `POST /api/fork` 在
  会话带 lesson_key 时拒绝对锁定步的 SET_METHOD/SET_PARAMS/fork changes),400 带原因
  ——复用现有入队即校验的防线位置。

### 4.2 参数试探的安全沙箱(fork 语义直接兑现)

- 学生对任务卡「开始作答」= 从 `given.from_run` 指定的参考 run **fork**
  (`POST /api/fork` 已有):1-6 步产物沿祖先链零重算复用,学生只跑 7-11 步——
  单次试探成本从小时级降到秒~分钟级,「多试几组参数」从奢侈变成默认学习方式;
- **父 run 天然不可污染**:fork 是新 run_id 新工作区,学生怎么折腾都不影响参考 run 与
  同学的分支;证据阶梯对 fork 的「不空洞过审」规则(复用产物沿祖先链核对指纹、
  外部验证不自动继承)原样生效——学生的 audited 是自己挣的,不是继承的;
- **资源防线**:每会话 fork 数上限与磁盘配额(probe 已有 disk_free_gb;超限拒绝 fork 并
  提示清理旧分支);重型引擎(isce2/snaphu 本地重跑)在教学会话默认不可选
  (locked_steps 覆盖),从机制上杜绝「一个学生把机房跑挂」;
- **对比学习**:学生的多个 fork 分支在流水线面板可并排看指标(velocity p2/p98、
  residual_rms 随参数怎么变)——这正是 fork 设计文档里「参数试探分支」的本意,
  教学场景只是把它变成练习的标准动作。

### 4.3 自动评分设想

评分器设计为**纯函数新模块** `audit/grade.py`(输入:该 run 的 provenance dict + qa.json +
rubric;输出:score.json),不碰执行链——与 `report/methods.py` 同样的纯函数纪律。

三层分数,权重由 rubric 声明:

1. **结果分(qa 指标 vs 容差带)**:逐项对照 `require.metrics`
   (min/max/range 三种带;缺指标 = 该项 `unverifiable`,不给分也不判错,
   提示「重跑第 11 步质检」——延续「指标缺失只警告」的诚实纪律);
2. **过程分(provenance 结构检查)**:
   - 证据级达标:`evidence.level_index >= target`(直接读 provenance 的 evidence 段);
   - 工作方式:`must_fork` → 检查 `parent_run_id` 非空;`export` → run.sh/methods.md
     可导出且非空;失败处置留痕(trace 表有 revision_trigger 记录)作加分项;
3. **对照分(与参考 run 的指纹/结果对比)**:
   - **结果对照**:学生 qa 指标与参考 run 指标的偏差在 rubric 带内 → 满分;
   - **配置对照**:逐步比较 `eval_hash`(三段指纹已有)——与参考 run 全等说明照抄配置,
     rubric 可声明「必须至少一步与参考不同」(考探索)或「必须全等」(考复现),两种题型;
   - **复现徽标**:产物文件指纹(三档 filehash)与参考一致 → 「完全复现」徽标。
     **注意**:指纹含工具版本,跨机器天然漂移——对照分永远以指标带为准,
     指纹相等只做徽标/提示,绝不作为扣分依据(这是跟 nbgrader「重跑测试而非 diff 输出」
     学的同一课)。

配套机制(参照 nbgrader):

- **validate 自查**:练习卡上「自查」按钮调 `GET /api/grade?dry_run=1`,只跑 rubric 的
  **公开子集**(`hidden: true` 的指标带不参与、不显示阈值,只报「隐藏项 N 项待批改」)
  ——学生提交前有确定性反馈,又防面向阈值调参;
- **师侧双向校验**:课程包制作工具须验证「参考 run 对 rubric 满分」且
  「空白 fork(未作答)不满分」——对应 `nbgrader validate` / `validate --invert`;
- **score.json 落盘**:`{workspace}/score.json`,每项得分附依据路径
  (指向 provenance 的具体字段),成绩可审计——评分器不说无凭据的话,
  与全项目「绝不编造」一致;汇总导出 CSV(P2)。

---

## 5. 课程包格式(schema 草案)

### 5.1 设计决策

- **课程包引用场景包,不替代**:场景包(SKILL.md + overrides.yaml)是领域知识与机器配置的
  单一真相源,课程包只加教学层(讲稿/暂停点/任务卡/参考答案)。一个场景包可被多个课程包
  引用(同一 quake 场景:本科 45 分钟演示课 vs 研究生 3 小时实操课);
- **加载器复用 scenario_packs 的全部纪律**:frontmatter 闭集校验、缺必填拒载并警告、
  未知字段警告不拒载、渐进披露(启动只读 frontmatter,正文按需)——新建 `registry/lessons.py`
  与 `registry/lesson_packs/` 目录,结构性代码可直接参照 `registry/scenarios.py`;
- **参考答案是复现包,不是标准答案文本**:参考 run 的 provenance + run.sh + qa.json +
  事件档,学生的「答案」和参考的「答案」是同一种东西(可复现的 run),评分是两个 run 的
  机器对照——这是本产品相对 nbgrader(对照的是代码输出)的差异化。

### 5.2 目录与 frontmatter 草案

```
registry/lesson_packs/<key>/
├── LESSON.md            # frontmatter(闭集)+ 正文(讲稿分节;暂停点讲解卡按标题锚引用)
├── checkpoints.yaml     # 任务卡 + rubric(机器读;学生版分发时 hidden 项可剥离)
├── replay/
│   └── events.ndjsonl   # 课堂演示事件档(录制自讲师的一次真实 run)
│   └── figures/         # 回放引用的图件快照(离线课堂用,可选)
└── reference/           # 参考答案复现包(学生版分发时整目录剥离)
    ├── provenance.json  #   export_provenance 原样输出(含指纹/指标/证据级)
    ├── run.sh           #   等价裸命令(export_run_script)
    ├── qa.json          #   参考指标(评分对照源)
    └── methods.md       #   参考方法章节(范文)
```

```yaml
# LESSON.md frontmatter 草案(闭集,对齐 scenario_packs 的校验风格)
---
name: quake-101                    # 与目录名一致(拒载条件同 scenarios.py)
description: Ridgecrest 同震形变全链演示与练习(90 分钟,研究生)
metadata:
  version: 1.0.0                   # 进 provenance(课程包也是溯源对象)
  scenario: quake                  # 引用的场景包 key(必填;加载时校验存在)
  audience: graduate               # graduate | undergraduate | professional
  duration_min: 90
  env_mode: hyp3                   # simulated | hyp3 | full(机房能力要求)
  objectives:                      # 教学目标(展示用)
    - 说出 SBAS 时序反演的输入输出与关键假设
    - 解释同震场景为什么用 step 模型而不是 linear
    - 独立把一条链做到 audited 并解释证据阶梯为何封顶
  prereq: [InSAR 基本原理, 相位解缠概念]
  pause_at:                        # 回放暂停点(§3.2;on 值 = 事件类型闭集)
    - {on: step.start, step: 7, card: "#时序反演"}
    - {on: gate_stop}
    - {on: degrade}
  checkpoints: checkpoints.yaml    # 相对路径(缺省即此名)
---
# 正文:讲稿。二级标题即讲解卡锚点(如 "## 时序反演"),
# 领域知识不在此复制 —— 需要时写「见场景包 SKILL.md §模型设定说明」由 UI 联动展开。
```

```yaml
# checkpoints.yaml 顶层草案
schema_version: "1.0"
checkpoints:
  - id: cp-1
    kind: quiz                     # quiz(概念题,选项+答案)| task(任务卡,见 §4.1)
    after: {on: step.end, step: 6} # 嵌入位置:回放/演示进行到此处弹题(参照 MOOC 周内小测)
    question: HyP3 产品已含解缠相位,意味着哪些步骤云端已完成?
    options: [2-6 步, 1-6 步, 7-11 步]
    answer: 0
  - id: cp-2
    kind: task
    # …(§4.1 示例的全部字段)
    rubric:
      weights: {result: 0.5, process: 0.3, compare: 0.2}
      metrics:
        mean_coherence: {min: 0.90, hidden: false}
        residual_rms_mm: {max: 30, hidden: true}   # 隐藏带:自查时不显示阈值
      compare_to: reference        # 对照 run(reference/ 目录)
      config_policy: explore       # explore(须至少一步异于参考)| reproduce(须全等)
```

### 5.3 版本与失配

- 参考 run 的 provenance 含 `agent_hash`(registry 能力声明的哈希)与工具版本——
  agent 升级或方法矩阵变更后,课程包加载时对比当前 `agent_hash`,失配则在讲师侧
  显式警告「参考 run 产自旧版能力矩阵,建议重生成」(重生成 = 重跑一次参考 run +
  一条打包命令,P2 提供 `scripts/make_lesson_pack.py`);
- 事件档带 `schema_version`;回放器对未知事件类型静默忽略——前端对 `step.stage`
  的处理已是此先例,契约测试的事件类型注册表保证词汇表演进有对账。

---

## 6. 分期路线与改动面

### P0:回放 + 大屏(±3 人日,纯增量,不动执行链)

| # | 事项 | 改动面 | 估算 |
|---|---|---|---|
| 1 | 事件落盘 file sink(ts+seq 信封,写失败不阻塞) | `loop/driver.py`(_emit 包装,~25 行)+ 契约测试补档案形状 | 0.5 d |
| 2 | `GET /api/replay`(读档、run_id/分片过滤) | `api/app.py`(~30 行) | 0.5 d |
| 3 | 回放模式:iterator 包装 → `consume()`;控制条(空格暂停/单步/变速/进度轴);回放态禁用干预 | `prototype/js/app.js`(replay 函数 + 控制条)、`state.js`(replay 标志)、CSS | 1 d |
| 4 | 大屏显示预设(projector-light/dark、token 刻度覆写、降噪、快捷键、URL 参数) | `prototype/css/tokens.css`(覆写层)、`state.js`(S.display)、`titlebar.js`(切换钮)、`a11y.js`(快捷键登记) | 0.5 d |
| 5 | 暂停点 v0:「每步自动暂停」开关 + 手动空格;现场课复用 PAUSE/PLAY(零开发,写使用文档) | `app.js` 回放器内 ~15 行 + `docs/USAGE_GUIDE.md` 补节 | 0.5 d |

验收:断网教室(后端在、无引擎)对 Ridgecrest 实测会话完整回放并 4× 变速;投影模式下
教室后排(5 米)可读事件流标题与指标数字;回放中所有干预入口不可触发。
风险:事件档在长会话膨胀——按会话单文件 + seq 分片读取已够;不做轮转(教学会话短)。

### P1:任务卡 + 评分 + 「为什么」浮层(约 6-8 人日)

| # | 事项 | 改动面 |
|---|---|---|
| 1 | 课程包加载器(闭集校验/拒载警告/渐进披露,含 checkpoints.yaml 解析) | 新 `registry/lessons.py` + `registry/lesson_packs/quake-101/`(首个包);测试仿 `tests` 中 scenario_packs 用例 |
| 2 | 会话关联课程包 | `core/schema.sql`(sessions 加 `lesson_key` 可空列)、`core/store.py`、`api/app.py`(创建会话入参) |
| 3 | 任务卡 UI(常驻卡、开始作答=fork、自查按钮、locked_steps 灰显) | `prototype/js/stream.js`(新卡型)、`app.js`(接线) |
| 4 | locked_steps 校验 | `api/app.py`(/api/actions 与 /api/fork 入口校验,~20 行) |
| 5 | 评分器 + API(`audit/grade.py` 纯函数;`GET /api/grade?dry_run=`;score.json 落盘) | 新 `audit/grade.py`(~200 行)+ `api/app.py` 路由;测试:参考 run 满分/空白不满分双向 |
| 6 | 「为什么」浮层(`GET /api/why` 聚合 + 前端浮层) | `registry/scenarios.py`(正文切片辅助)、`api/app.py`、`prototype/js/stream.js`/`dock.js` |
| 7 | 失效级联演示动画 | `prototype/js/pipelinerail.js`(impact 结果的逐步点亮动画) |
| 8 | 回放暂停点声明式(pause_at 匹配 + 讲解卡渲染) | `app.js` 回放器 + LESSON.md 小节渲染(markdown 已有依赖?无则用最小解析,正文是受控内容) |

验收:一个学生在 simulated 机房从任务卡到 score.json 全程无讲师干预;
评分器对参考 run 满分、对空白 fork 不满分(双向测试进 CI)。

### P2:课程包生态(约 8-12 人日,按需裁剪)

- **打包/分发**:`scripts/make_lesson_pack.py`(从指定 run 生成 replay/ + reference/ +
  manifest;学生版剥离 reference/ 与 hidden rubric)——nbgrader generate_assignment 的对应物;
- **纯离线回放包**:events.ndjsonl + figures 快照 + 静态 UI 打成 zip,file:// 直开
  (`backend.mock.js` 的离线路径扩展为「读本地档」);
- **参考包失配检测**:agent_hash/工具版本对照与讲师侧重生成引导(§5.3);
- **成绩汇总**:多学生 score.json 汇 CSV(`scripts/collect_scores.py`);LMS 对接止步于
  CSV 导出,不做协议集成;
- **双屏演示**:dock 面板弹独立窗口 + BroadcastChannel 状态同步(AGENT-DESIGN §7.3 待办);
- **课程包目录页**:UI 列出已装课程包(名称/受众/时长/env_mode 与本机环境的匹配检查),
  一键开课;
- **(探索)评测集副产品**:任务卡 + rubric + 参考 run 的三元组积累到一定数量,
  即 AGENT-DESIGN §8.3 设想的「InSAR 任务评测集」——教学生态与 agent 评测共用一套资产。

### 明确不做(与全项目诚实纪律对齐)

- **不建账号/名册系统**:单机单学生,身份就是会话名;成绩以文件交付;
- **不做防作弊体系**:指纹全等只提示「与参考/同学配置一致」,不判定、不告发;
- **不做实时多人课堂同步**(教师端广播控制学生端):复杂度/收益比失衡,回放包 + 大屏已覆盖演示需求;
- **不为教学放松证据阶梯**:simulated 封顶 runnable、PENDING 封顶 audited 在课堂如实展示
  ——「系统诚实地告诉你证据到哪级」正是要教的科研素养;
- **不引入视频录制**:事件档全面优于录屏(可交互、可变速、可 diff、体积小)。

---

## 7. 调研来源索引

| 类别 | 来源 |
|---|---|
| Esri 课堂 lesson | [Classroom Activities Gallery 介绍](https://community.esri.com/t5/education-blog/find-a-lesson-for-your-class/ba-p/1613733) · [Measuring Fires lesson PDF](https://www.esri.com/content/dam/esrisites/en-us/media/pdf/teach-with-gis/measuring-fires.pdf) · [Zaatari lesson PDF](https://www.esri.com/content/dam/esrisites/en-us/media/pdf/teach-with-gis/zaatari.pdf) · [E. Coli Outbreak lesson PDF](https://www.esri.com/content/dam/esrisites/en-us/media/pdf/teach-with-gis/visualizing-an-ecoli-outbreak.pdf) |
| QGIS 手册生态 | [QGIS Training Manual](https://docs.qgis.org/3.10/en/docs/training_manual/index.html) · [贡献规范(Follow Along/Try Yourself/答案表)](https://docs.qgis.org/3.10/en/docs/training_manual/appendix/contribute.html) |
| nbgrader | [Highlights](https://nbgrader.readthedocs.io/en/latest/user_guide/highlights.html) · [创建与评分](https://nbgrader.readthedocs.io/en/stable/user_guide/creating_and_grading_assignments.html) · [作业收发](https://nbgrader.readthedocs.io/en/stable/user_guide/managing_assignment_files.html) |
| ESA / EO College | [Echoes in Space 课程页](https://eo-college.org/courses/echoes-in-space/) · [ESA 资源页](https://eo4society.esa.int/resources/echoes-in-space/) · [EO College InSAR 资源分类](https://eo-college.org/resource-category/methods/insar/) · [DLR/ESA PolInSAR 课(2024)](https://eo4society.esa.int/event/8th-edition-of-the-dlr-esa-open-polinsar-training-course-2024/) · [PolInSAR 2025 概览 PDF](https://eo4society.esa.int/wp-content/uploads/2025/02/ThePolInSARCourse-2025-N9-Overview-v2-002.pdf) |
| EarthScope / ASF | [2024 ISCE+ 短训班](https://www.earthscope.org/event/2024-insar-processing-and-analysis-isce-short-course/) · [2022 版](https://www.unavco.org/event/2022-short-course-insar-processing-analysis-isce/) · [OpenSARLab webinar PDF](https://www.earthdata.nasa.gov/s3fs-public/2023-01/ASF_OpenSARLab_Webinar_1_25_23.pdf) · [opensarlab-notebooks 仓库](https://github.com/ASFOpenSARlab/opensarlab-notebooks) · [Gareth Funning 教学页](https://www.garethfunning.com/training) |
| GETSI 课堂模块 | [Imaging Active Tectonics 模块](https://serc.carleton.edu/getsi/teaching_materials/imaging_active_tectonics/index.html) · [讲师材料总览](https://serc.carleton.edu/getsi/teaching_materials/imaging_active_tectonics/overview.html) · [Unit 3(InSAR 练习+答案钥)](https://serc.carleton.edu/getsi/teaching_materials/imaging_active_tectonics/unit3.html) · [Unit 4(交互反演 jigsaw)](https://serc.carleton.edu/getsi/teaching_materials/imaging_active_tectonics/unit4.html) |
| 演示形态 | [RISE 文档](https://rise.readthedocs.io/en/latest/) · [RISE 用法(speaker notes)](https://rise.readthedocs.io/en/5.6.0/usage.html) |
| 内部先行文档 | `reference/RESEARCH-agent-ux-2026-08-12.md` §6.3(Manus Replay)/§10.4(回放技术路径) · `docs/AGENT-DESIGN.md` §7.3(Dock 弹窗双屏)/§8.3(教学与论文增强) · `docs/VALIDATION-isce2-wsl.md` 与 README「真实数据验收」(38 秒全链的课堂可行性依据) |
