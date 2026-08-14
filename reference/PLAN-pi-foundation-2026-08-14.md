# 以 pi 为基础重建 insar-agent:调研与方案(待确认)

> 生成日期:2026-08-14 · 状态:**方案待你确认,尚未开始开发**
> 分支:`cursor/insar-agent-on-pi-research-c983`
> 调研方式:两个 fable5(max/xhigh)子代理并行 —— 一个联网核验 pi 现状,一个只读盘点本仓可复现内核。
> 结论先行:**不建议"fork pi 源码、在 pi 里重写 InSAR"**;建议 **"把 pi 当依赖用(不 fork),
> 作为通用 Agent 外壳;把现有 Python 可复现内核保留并封装成 pi 的受约束 skills/tools"**。理由见 §3–§5。

---

## 1. 我对你想法的理解(先对齐,确认我没理解错)

你的两个目标:

1. **继承 pi 的全部能力**:pi 已经把"AI 持续规划 + 工具调用 + 会话 + 技能 + 扩展"这些通用 Agent 能力做完了,
   你不想从 0 再造一个 Agent。所以想直接站在 pi 上,把界面换成 insar-agent 的皮。
2. **在 pi 之上做 InSAR**:用 AI 推动 InSAR 数据处理 —— 加 InSAR 的 skill、提示词、工具,
   让"AI 干活"的能力真正用在 InSAR 这个有用的场景上。

执行要求:建分支、按 pi 源码改、界面还是 pi 换皮、其他架构不变、彻底开发;
思考/解决问题用 **fable5 max**,写代码用 **opus 5 max fast**;先给方案,你确认后再开工。

这个战略动机我完全认同:**不重复造通用 Agent 的轮子,把力气放在 InSAR 的科学价值上。**
下面的方案分歧只在"怎么站在 pi 上"这一步 —— 有三个硬约束让"直接 fork pi 源码在里面重写"这条路
代价极高且会砸掉你现在最值钱的东西。

---

## 2. 关键调研结论(2026-08-14 实时核验)

### 2.1 pi 是什么(全部已联网核验)

- 仓库 `earendil-works/pi`,**MIT**,**TypeScript** monorepo,最新 **0.84.2(今天发布)**,90,324 star,周下载 ~165 万。
- 包:`pi-ai`(多供应商 LLM)、`pi-agent-core`(agent loop + 尚未实现的 AgentHarness)、
  `pi-coding-agent`(CLI 产品:扩展/技能/模板/主题 + 四种运行模式)、`pi-tui`(**终端 UI**)、
  `pi-telemetry`,以及较新的 `protocol/server/client/session-backends`(远程会话,**实验性、无兼容保证**)。
- **pi 没有官方 Web UI**。官方 UI 只有终端 TUI;`pi-web-ui`(Lit 组件)已于 2026-05 停更并从仓库移除;
  Web 化只有两条路:第三方守护进程 [PI WEB](https://pi-web.dev/),或自己在实验性 protocol/server/client 上搭。
- **扩展 API 足以"不 fork 就加领域能力"**:`registerTool` 加工具(甚至替换内置 read/bash)、
  SKILL.md 加技能(agentskills.io,渐进披露)、`SYSTEM.md`/`APPEND_SYSTEM.md` 改系统提示词、
  主题 JSON 换肤;分发用 `pi install npm:/git:`。官方明说"适配 pi 无需 fork 内核"。
- 可作库嵌入(SDK,Node 进程内)或作子进程用(`pi --mode rpc`,stdio JSONL,语言中立)。
- **哲学(要害)**:LLM 是**全权自由规划者**(read/write/edit/bash 自由发挥)、**无内置权限系统**
  (信任边界靠容器外置)、系统提示词极小、"核心极小、一切皆扩展、无 MCP"。

### 2.2 本仓最值钱的东西(只读盘点,给出模块路径)

你现在的"世界级科学可复现底座"= 一整套**深度绑定 Python/科学栈**的机制:

- 三段式指纹 + 参数三分类 + 级联标脏:`core/fingerprint.py`、`normalize.py`、`filehash.py`、`stale.py`
  (哈希字节级规范化被 `tests/test_hash_semantics_lock.py` 锁死)。
- 五阶段执行器 + 幂等守卫 + reattach + 孤儿/失败区分:`runtime/executor.py`、`stream.py`、`jobs.py`、
  `wsl.py`+`wsl_wrapper.sh`、`backend_select.py`。
- run_ok 双判定 + 质量门 + 阈值台账(带引用)+ 六级证据阶梯:`audit/runok.py`、`contract.yaml`、
  `ladder.py`、`verify.py`。
- 干预队列三语义 / run fork / SQLite 唯一真相源 / provenance + run.sh:`core/store.py`、`planner/plan.py`、
  `core/schema.sql`、`core/ledger.py`、`report/script.py`。
- Brain 受约束 + 可整层拔除 + 自主循环:`brain/facade.py`(LLM 只做"枚举候选选择题",越界拒绝)、
  `loop/driver.py`(converse_loop)、`events.py`、`budget.py`、`queue.py`。
- Web UI:`prototype/` ~55 个纯 JS 模块(栅格双图对比 `figcompare.js`、证据账本 `provview.js`、
  计划 diff `plandiff.js`、技能面板、斜杠命令、时序点曲线 `tspoint.js`……)。
- 1543 个 Python 测试 + JS 测试,大量是"行为锁 + golden 文件",本身就是资产。

**深度 Python 绑定、极难移植到 TS 的部分**:`engines/{mintpy,isce2,snaphu,pystamps,hyp3,era5}.py`、
ISCE2→PyStamps 桥、h5py/numpy 栅格 QA 与出图、WSL 作业后端。这些是 InSAR 的科学本体,TS 里没有等价物。

---

## 3. 三个根本性冲突(为什么"fork pi 在里面重写"代价极高)

1. **语言/技术栈冲突**:pi 是 TS,你的科学价值(可复现内核 + 引擎 + 栅格 QA)是 Python。
   "在 pi 源码里重写 InSAR"意味着要么把可复现内核 + mintpy/isce2/h5py/numpy 全部用 TS 重写
   (基本不可能,且砸掉 1543 个测试与 golden 资产),要么还是把 Python 当 sidecar —— 那就不该 fork pi。

2. **UI 冲突**:pi 没有官方 Web UI,只有终端 TUI。你的核心 UX(栅格双图、证据账本、计划 diff、
   时序点曲线)在终端里放不下。"把 pi 的 UI 换皮成 InSAR"这句在今天无处落地 —— 要么丢掉你 55 个模块的
   Web UI 去搭实验性 protocol,要么其实你想保留的正是你自己的 Web UI。

3. **哲学倒置(最致命)**:pi 的立身之本是"LLM 自由跑 bash",你的"世界级"恰恰是它的反面 ——
   "LLM 只做候选集选择题 → 科学可复现"。**全盘继承 pi = 继承自由规划循环 = 亲手拆掉你的学术定位。**
   你的仓库自己的 `reference/PI_FRAMEWORK_ANALYSIS.md §0` 早就写了:"pi 整体不能照搬"。

---

## 4. 方案对比

| 方案 | 做法 | 得到"全部 pi 能力" | 保住可复现内核 | 保住 Web UI/科学 UX | 跟随上游 | 工作量/风险 | 结论 |
|---|---|---|---|---|---|---|---|
| **A. Fork pi 重写** | fork monorepo,在 TS 里重建 InSAR | 是(但要打架) | ✗ 需 TS 重写,极可能砸掉 | ✗ 无官方 Web UI 可继承 | ✗ 周更,fork 迅速漂移 | 极高 / 极高 | **不推荐** |
| **B. 依赖 pi(不 fork)** | pi 作通用 Agent 外壳(SDK/RPC);Python 内核保留,封装成 pi 的受约束 skills/tools;UI 保留并换皮 | **是**(pi 设计就是这么用) | ✓ 原样保留 | ✓ 复用现有 UI,换皮到 pi 观感 | ✓ 升级 pi 版本即可 | 中 / 中低 | **推荐** |
| **C. 选择性吸收** | 继续现架构,只借 pi 的机制(已有 absorb-E/F 台账) | ✗ 不给"全部 pi 能力" | ✓ | ✓ | — | 低 / 低 | 保底 |

**方案 B 恰好命中你的两个目标,又不砸科学价值** —— 这是 pi 官方推荐的用法("adapt without forking core")。

---

## 5. 推荐方案(B)的架构

**双层架构 —— 通用 Agent 层用 pi,科学处理层用受约束的 Python 内核。**

```
┌────────────────────────────────────────────────────────────┐
│  UI 层:保留 prototype/ 的 Web UI,换皮到 pi 观感(主题/配色/命名)  │
│  (pi 的 TUI 主题体系做参照,但载体仍是你的 Web UI,SSE 契约不变)     │
├────────────────────────────────────────────────────────────┤
│  通用 Agent 层 = pi(作依赖,不 fork)                           │
│   · 会话/持续规划/工具调用/技能渐进披露/扩展/压缩 —— 全部来自 pi     │
│   · 接入方式:pi --mode rpc 子进程(语言中立,后端 Python 友好)     │
│     或 pi SDK(若某些面转 Node)                                │
│   · 目标 1 达成:AI"能干活"的通用能力 100% 继承,零重造          │
├────────────────────────────────────────────────────────────┤
│  受约束边界(关键:pi 不能对科学步骤自由跑 bash)                  │
│   · 把 InSAR 内核暴露成一小撮**高层闭集工具**给 pi:              │
│     plan_run / preview_change / execute_step / fork_run /       │
│     export_provenance …(pi 只能调这些,拿不到裸 bash)          │
│   · 科学步骤仍走五阶段执行器 + 指纹/标脏 + 质量门 + 证据阶梯        │
│   · 目标 2 达成:AI 推动 InSAR,但可复现保证一分不丢              │
├────────────────────────────────────────────────────────────┤
│  科学内核 = 现有 Python(原样保留)                              │
│   core/ runtime/ audit/ engines/ planner/ registry/ report/     │
│   + 1543 测试 + golden 文件                                     │
└────────────────────────────────────────────────────────────┘
```

一句话:**pi 负责"聊天/探索/规划/写代码/研究"这类自由发挥有价值的部分;
一旦进入真正的 InSAR 科学处理,就交给受约束的确定性流水线。两者用一组高层工具对接,而不是裸 bash。**

InSAR 领域能力用 pi 原生机制承载:
- **Skills**:每一步(11 步)一个 SKILL.md(领域知识渐进披露),复用你已有的 `skills/` 内容。
- **Tools**:`registerTool` 把上面的高层闭集工具注册给 pi。
- **Prompts**:`APPEND_SYSTEM.md` 注入 InSAR 语境与红线(不许生成命令行、只做选择题)。
- **Theme/换皮**:pi 主题 JSON 作参照,Web UI 换配色/命名到 insar-agent 观感。

---

## 6. 分阶段开发计划(方案 B,确认后执行)

> 思考/设计/排障 = fable5 max 子代理;写代码 = opus 5 max fast 子代理;每阶段并行多子代理。

- **P0 打通 pi(spike)**:装 pi(npm,Node ≥22.19),跑通 `pi --mode rpc`,用最小 Python 适配器
  收发 JSONL 事件流,验证"Python 后端 ↔ pi Agent"往返。产出:可运行的桥 + 冒烟测试。
- **P1 受约束工具边界**:定义并实现暴露给 pi 的高层闭集工具(plan_run/preview_change/execute_step/
  fork_run/export_provenance),对接现有 `loop/driver.py` 与 `runtime/executor.py`;禁用 pi 裸 bash
  对科学步骤的触达(权限/确认扩展)。产出:pi 能安全驱动一次模拟 run 到 provenance。
- **P2 InSAR 技能与提示词**:11 步 SKILL.md + `APPEND_SYSTEM.md` 红线 + 领域 few-shot;
  把 brain 的"候选集选择题"约束在 pi 侧复刻(before_tool 钩子 fail-closed)。
- **P3 UI 换皮**:保留 `prototype/` Web UI,按 pi 观感换主题/配色/命名/首屏;SSE 事件契约不变;
  必要处把 pi 的会话事件映射到现有事件总线。
- **P4 端到端与守护**:pi 驱动的全链(规划→确认→执行→证据)在模拟模式跑通并录屏;
  现有 1543 测试保持绿;新增 pi-桥接层测试;CI 增补 Node 侧。
- **P5 收尾**:文档、迁移说明、把 absorb-E/F 里仍值得的机制补齐。

每阶段结束:提交 + 推送 + 更新 PR + 给你一段可验证证据(日志/录屏)。

---

## 7. 需要你拍板的决策点(确认后我立刻开工)

1. **走方案 B(依赖 pi、不 fork、保留 Python 内核与 Web UI)对吗?**
   如果你坚持方案 A(fork pi 在 TS 里重写),我也能做,但请确认你接受"砸掉现有可复现内核 + Web UI +
   1543 测试、并与周更上游长期打架"的代价 —— 我强烈建议不要。
2. **接入方式**:pi 作**子进程(RPC)**(推荐,Python 后端友好)还是 **SDK(转 Node)**?
3. **UI**:保留并换皮你现有 Web UI(推荐,复用 55 模块)?还是要做成 pi 那种以聊天为中心的形态?
4. **"继承全部 pi 功能"的边界**:科学处理步骤是否接受"不给 pi 裸 bash、只给高层闭集工具"
   这条红线(这是同时满足你目标 1、2 又不丢可复现的关键)?若你要 pi 在科学步骤也自由跑 bash,
   那可复现保证无法成立,需要你明确取舍。

你回复确认(或修改)以上四点,我就按你拍板的路线,启动多子代理(fable5 max 思考 / opus 5 max fast 编码)
彻底开发。
