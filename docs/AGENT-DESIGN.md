# InSAR-Agent 系统设计

> 本文是 agent 层、执行层、界面层的完整设计定稿。
> 依据：`docs/DESIGN.md`（产品与 novelty 定稿）+ 四轮源码级竞品审计。
> 所有借鉴点标注 `仓库/文件:行号`，所有「未实现」标注为设计空白。
> 2026-08-12：并入 `reference/AGENT_PRODUCTS_LEARNING.md`（11 仓库产品层 / harness 层 / 定向工具对标）的十条修正（absorb-E~P）。
> 最后更新：2026-08-14（自主循环波次修订：§2 分层图、§3.3 约束一/约束四、§3.5 五职责、§8.4；循环设计全文另立 `docs/AGENT-LOOP.md`，上一版 2026-08-12）

---

## 0. 阅读指引

| 你想知道 | 看哪节 |
|---|---|
| InSAR 和普通 agent 任务差在哪 | §1 |
| agent 主循环怎么转 | §3 |
| 小时级任务崩了怎么续 | §4（核心） |
| 指纹怎么算、怎么标脏 | §5 |
| 界面右侧那些面板怎么组织 | §7 |
| 先做什么后做什么 | §9 |

前置结论修正（研究后推翻的三条原判断）：

1. **「人改参数 → 自动标脏下游」不是我们的创新** —— aiida-workgraph 已完整实现（`aiida-workgraph/src/aiida_workgraph/engine/task_state.py:170-203` 的 `reset_task` 递归重置 + `workgraph.py:129-136` 的 `check_modified_tasks` 图 diff）。我们的空间在**零基础设施**（它需要 PostgreSQL + RabbitMQ + daemon）与**可解释性**（它静默重置，不告诉用户为什么）。
2. **InSAR-Pro 没有 ISCE2 封装** —— 它走 ISCE3 + Dolphin，且 `app/engines/isce3.py`（933 行）连 `import isce3` 都没有，全是 numpy fallback。ISCE2 链是零起点。
3. **InSAR-Pro 的 MintPy 封装已经在用 `--dostep`** —— 5 处独立 CLI 子命令调用（`InSAR-Pro/insar-pro/backend/app/engines/mintpy.py:290,556,750,914,1047`）。这正是竞品明确没做的那块，可直接复用。

---

## 0.5 环境事实基线（2026-08-10 实测）

设计必须建立在实测事实上。本节的每个数字都来自本机验证，**不是引用文档**。

### 0.5.1 关键发现：WSL 尚未就绪

```
wsl.exe                 存在（C:\Windows\system32\wsl.exe）
已安装发行版             无 —— "适用于 Linux 的 Windows 子系统没有已安装的分发"
%USERPROFILE%\.wslconfig 不存在
```

`DESIGN.md:437` 写的「WSL2 + Ubuntu 24.04 + Miniforge，约 8 GB，装在 E: 盘」**尚未执行**。
`DESIGN.md:459-474` 的三个必做配置（`.wslconfig` / Dask 临时目录 / 环境变量）也都未落地。

**这是 Phase 1 的前置阻塞项**，不是可并行的小事：ISCE2 不能原生 Windows（`DESIGN.md:431`），
没有 WSL 就没有执行层可测。

### 0.5.2 宿主 Python 缺少 POSIX 进程 API

```python
os.killpg    False        # ← §4.4 原设计依赖它
os.getpgid   False        # ← §4.3 原设计依赖它
os.setsid    False
os.WNOHANG   False
signal.SIGTERM  True      # Windows 上仅等价于 TerminateProcess
signal.SIGKILL  False     # ← 不存在
```

宿主：`win32 / C:\Python314\python.exe`。

**原 §4.3/§4.4 里的 `os.killpg(proc.pgid, SIGKILL)` 在宿主机会直接 AttributeError。**
进程控制必须重新设计 —— 见 §4.7。

### 0.5.3 磁盘实测

| 盘 | 容量 | 已用 | 可用 | 用途 |
|---|---|---|---|---|
| C: | 199 G | 164 G | **36 G (83%)** | 系统。**WSL 默认装这里会撑爆** |
| D: | 467 G | 427 G | **40 G (92%)** | 近满 |
| E: | 931 G | 449 G | **483 G** | 项目所在，工作区首选 |
| F: | 932 G | 746 G | 187 G | 归档 |

`DESIGN.md:450` 写「Samsung 980 1TB 唯一 SSD」，实测 E: 931 G 与之吻合。
但 C: 只剩 36 G 是新发现的风险：**WSL 发行版必须显式导入到 E:**，
否则 8 G 镜像 + 中间产物会填满系统盘。

### 0.5.4 数据实测（复核 DESIGN.md §11）

```
InSAR-Pro/insar-pro/backend/data/real_data/RidgecrestSenDT71/hyp3/
  11 个干涉对（非 DESIGN.md:727 写的「12 景」）· 7 个获取日期
  每对 10 文件 / 36.5 MB · 合计 110 文件 / 402 MB
  5 个主栅格 unw/corr/dem/lv_theta/lv_phi 均为 6,757,228 字节 —— 完全相同
mintpy/inputs/ERA5.h5                47,921,952 B
results/timeseries_ridgecrest.h5     72 MB / 7 日期 —— 合成数据，非真实处理结果
mintpy/RidgecrestSenDT71.txt         真实可用的 MintPy 配置（含 stepFuncDate=20190706）
```

### 0.5.5 原型中的占位数字（必须替换）

| 占位值 | 位置 | 真相 |
|---|---|---|
| `pairs: 1035` | `prototype/js/state.js:87` | **`1035 = C(46,2)`，是全组合数**。小基线网络会剪枝到数百对，此值科学上不成立 |
| `~24 min · 8 GB` | `prototype/js/state.js:104` | 无任何实测依据 |
| `2.1 GB` / `1.4 GB` | `prototype/js/backend.mock.js:146,153` | 编造 |
| 各步 `dur` 秒数 | `prototype/js/state.js` | 编造，`estimateRerun()` 基于它算「93 分钟」 |

**结论：原型当前不能作为对外演示材料**，除非加「示意值」水印。见 §10。

---

## 1. InSAR 处理的独特性：七条硬约束

这一节是整份设计的推导起点。**通用 agent 框架在这七条上全部失效**，这是自研执行层的唯一理由。

### 1.1 单步耗时跨五个数量级

```
参数校验      < 1 秒
DEM 下载      ~ 2 分钟
解缠(单对)    ~ 30 秒 × 1035 对
配准(46 景)   ~ 18 分钟
干涉图        ~ 52 分钟
ISCE2 全链    2–5 小时（DESIGN.md:454，预估未实测）
PS 时序反演   8–24 小时（InSAR-Pro 文档目标值）
```

**推论**：不能用统一的超时策略。竞品的做法是全局 `timeout=120`（`agentic-swmm/agentic_swmm/agent/tool_handlers/_shared.py:229`）——对 InSAR 直接不可用。MintPy 那条更糟：裸 `proc.wait()` 无超时（`InSAR_Agent/src/insar_agent/tools/mintpy.py:234`），可永久挂起。

设计要求：**超时必须按 capability 声明，且区分「无输出超时」与「总时长超时」**。

### 1.2 重跑代价不对称，且不可逆的是时间不是数据

改一个解缠阈值 → 下游 6 步失效 → 93 分钟。这不是「点一下重试」，是「明天再来看」。

**推论**：三条设计要求。
- 任何触发重跑的操作**必须前置确认**，并显示预估时长与影响范围（不是弹 `confirm()`，要显示命令、耗时、覆写清单）。
- 必须能**只重跑受影响段**，粒度到步。竞品的 `run_all(start_from: int)`（`InSAR_Agent/src/insar_agent/workflow.py:141-147`）用整数下标且靠 LLM 猜，不可用。
- 必须能**回答「为什么要重跑」**。redun 的 `explain_cache_miss`（`redun/redun/backends/db/__init__.py:2507`）区分 `new_task`/`new_args`/`new_call` 三类，这个能力要接到标脏路径上。

### 1.3 中间产物数量与体积失控

实测基准（唯一可信数字，来自 `InSAR-Pro/insar-pro/backend/data/real_data/`）：

```
HyP3 clipped 产品：11 个干涉对 → 110 文件 → 402 MB（每对 10 文件 / 36.5 MB）
5 个主栅格（unw/corr/dem/lv_theta/lv_phi）均为 6,757,228 字节 —— 完全相同
```

ISCE2 radar-coord 中间产物量级远高于此（`.slc.full` 单景 GB 级，1035 对干涉图各含 `.int/.cor/.unw/.vrt/.xml`）。

**推论一**：**绝不能全量内容哈希**。redun 的解法是 `(path, size, mtime)` 伪哈希（`redun/redun/file.py:463-475`）：

```python
if self.exists(path):
    stat = os.stat(path); mtime = stat.st_mtime; size = stat.st_size
else:
    mtime = -1; size = -1
return hash_struct(["File", "local", path, size, str(mtime)])
```

**推论二（重要陷阱）**：上面那 5 个栅格字节数完全相同 —— 说明 `(path,size,mtime)` 在 InSAR 场景有**真实碰撞风险**：同尺寸产物被覆写但 mtime 未变（如从备份恢复、或 `cp -p`）时判不出。因此需要三档策略：

| 数据类别 | 策略 | 依据 |
|---|---|---|
| 原始 SLC / DEM（只增不改） | 仅路径（`IFile`） | `redun/redun/file.py:1710-1716` |
| 中间产物（`.int/.unw/.cor`） | path+size+mtime | `redun/redun/file.py:463-475` |
| 最终成果（`velocity.h5` / 图件 / provenance） | 全量内容哈希 | `redun/redun/file.py:1784-1788` |

内容哈希的 `block_size` 必须调大 —— redun 默认仅 1024 字节（`redun/redun/hashing.py:57`），对 GB 级文件要改到 4 MB。

### 1.4 外部工具输出契约不稳定

这是 InSAR 特有的、通用框架完全没考虑的问题。实证清单：

| 不稳定点 | 证据 |
|---|---|
| MintPy 输出文件名随配置变 | `InSAR_Agent/.../tools/mintpy.py:343-344` 写 4 个候选名 `['timeseries_ramp_demErr.h5','timeseries_ERA5_ramp_demErr.h5','timeseries.h5',...]` |
| 输出目录不定（根目录 or `geo/`） | `InSAR_Agent/.../tools/mintpy.py:275-283` `_find_in_dir` 两处都试 |
| 可执行文件可能不在 PATH | `InSAR_Agent/.../tools/mintpy.py:192-197` `shutil.which` 失败退化为 `python -m` |
| ERA5/CDS 服务不可靠 | `InSAR_Agent/src/insar_agent/agent.py:1052-1067` 被迫自动降级 + unlink 所有 `*ERA5*` 产物 + 整个 MintPy 重跑 |
| 退出码 0 但结果错 | `agentic-swmm` 的 `run_ok` 语义：swmm5 退出 0 但 rpt 里有 solver error |
| PyStamps 路径字符偏移硬编码 | `DESIGN.md:138`：`[nb-22:nb-14]` 切日期，文件名必须严格 `YYYYMMDD_YYYYMMDD.diff` 结尾 |
| PyStamps 反选式文件发现 | `DESIGN.md:139`：目录里有 `.vrt/.hdr/.aux` 残留会被误当数据 |
| PyStamps 全程 big-endian | `DESIGN.md:131-132`：无条件 `.byteswap()`，桥必须输出 BE |

**推论**：产物发现必须是**声明式候选列表 + 显式失败**，不能假设单一路径。且需要 `run_ok` 双判定：进程退出码 + 领域校验器（产物存在、维度合理、数值范围、无 NaN 面积超限）。

### 1.5 失败原因高度可分类，且多数可自愈

InSAR 失败集中在少数几类，且每类都有确定的处置方式：

| 失败类 | 典型触发 | 处置 |
|---|---|---|
| 网络/凭据 | ASF/CDS/ESA 401、超时 | 换账号（`InSAR_Agent/src/insar_agent/tools/submit_insar.py:401-410` 遍历账号找额度）、指数退避 |
| 服务不可用 | CDS 队列积压 | 降级到 `tropo_height_corr`，标注证据等级下降 |
| 磁盘满 | 中间产物撑爆 SSD | 清理已归档的中间产物，或停链要求人工介入 |
| 内存不足 | MintPy 大堆栈 OOM | 降并行度 / 分块处理 |
| 参数不当 | 相干性阈值过高 → 解缠孤岛 | 回到候选集重选，不是重试 |
| 数据质量 | 基线过长、相干性过低 | **停链**，不自愈 —— 这是质量门该拦的 |

**推论**：修复动作必须是**预定义闭集**，LLM 只做分类不做发明。且必须区分「可自愈」与「必须停链」——`agentic-swmm` 顶层 skill 的原话值得照抄（`DESIGN.md:607`）：

> "decides which workflow path to take, which QA gates must pass, and **when to stop rather than invent missing inputs**"

### 1.6 部分完成有价值，且必须可见

MintPy 跑了 40 分钟在第 7 步失败 —— 前面的解缠产物是**有效资产**，不能因为整体失败就丢弃或标为无效。

`agentic-swmm` 的格言（`DESIGN.md:335`）：「Partial evidence is still useful evidence.」但它的代码没做到 —— 审计被 `if result.return_code == 0` 门控（`DESIGN.md:669-671`）。

**推论**：
- 审计必须 `try/finally` 真触发。
- 取消/失败时已完成步骤的状态与指纹**必须落盘**，不能只留在内存。
- 部分输出（日志尾部、已生成的中间文件）必须保留并在 UI 可见。当前原型的 `cancel()` 已做到这点（标 SIGTERM 保留输出）。

### 1.7 人在环路是常态，不是例外

专家模式下用户会在流水线跑到一半时说「第 6 步换成 SNAPHU」。这要求：

- 运行中可接收干预指令（不只是 kill）
- 干预后自动推导影响范围
- 已完成的无关步骤不受影响

aiida-workgraph 实现了这个，但走 RabbitMQ RPC（`aiida-workgraph/src/aiida_workgraph/engine/workgraph.py:287-303` 的 `message_receive` + `_schedule_rpc`）。**我们用 SQLite 的 `pending_actions` 表 + 主循环轮询替代**，零基础设施。

### 1.8 七条约束 → 架构决策映射

| 约束 | 架构决策 |
|---|---|
| §1.1 耗时跨五个数量级 | 超时按 capability 声明；双超时（无输出/总时长） |
| §1.2 重跑代价不对称 | 前置确认卡 + 步级续跑 + 失效可解释 |
| §1.3 产物体积失控 | 三档文件指纹策略；大块流式哈希 |
| §1.4 输出契约不稳定 | 声明式产物候选 + `run_ok` 双判定 |
| §1.5 失败可分类 | 预定义修复动作闭集；停链条件显式化 |
| §1.6 部分完成有价值 | try/finally 审计；状态即时落盘 |
| §1.7 人在环路常态 | SQLite 动作队列 + 轮询消费 |

---

## 2. 分层架构

```
┌──────────────────────────────────────────────────────────────────┐
│ UI      轨迹流(Codex式) + 右侧多面板 Dock + 双 SSE                 │
│         已实现于 prototype/（12 模块 3954 行，零依赖）              │
├──────────────────────────────────────────────────────────────────┤
│ API     FastAPI。人与 LLM 共用同一套接口                           │
│         task_id 唯一句柄，LLM 永不接触文件系统路径                  │
├──────────────────────────────────────────────────────────────────┤
│ Brain   ← 可整层删除，删掉后退化为手动流水线，结果完全正确           │
│         intent / select / triage / narrate / cycle 五职责           │
│         只做候选集内选择题，绝不生成命令或代码                       │
├──────────────────────────────────────────────────────────────────┤
│ Loop    agent 主循环。事件驱动，非 while+tool_call                 │
│         消费三个来源：用户消息 / 执行器事件 / 干预队列               │
├──────────────────────────────────────────────────────────────────┤
│ Core    唯一真相源 —— SQLite                                      │
│         fingerprint / stale / store / ledger / actions             │
├──────────────────────────────────────────────────────────────────┤
│ Runtime 唯一有副作用的层                                           │
│         五阶段执行器 + 幂等守卫 + 双超时 + 协作式取消               │
├──────────────────────────────────────────────────────────────────┤
│ Audit   契约与质量门。try/finally 保证触发                          │
├──────────────────────────────────────────────────────────────────┤
│ Registry  kinds / capabilities / bridges / scenarios（纯数据）      │
├──────────────────────────────────────────────────────────────────┤
│ Engines   ISCE2 / MintPy / PyStamps / PyAPS + 桥（薄封装，零决策）  │
└──────────────────────────────────────────────────────────────────┘
```

### 2.1 目录结构（相对 §DESIGN.md 5.1 的修订）

```
insar_agent/
├── registry/              纯数据·零逻辑
│   ├── kinds.py           DataKind(kind × layout × crs)
│   ├── capabilities.py    步骤候选方法声明 + 超时/资源声明 ← 新增
│   ├── artifacts.py       产物候选名声明（应对 §1.4）      ← 新增
│   ├── bridges.py         layout 转换声明
│   └── scenarios.py       场景规则（冻土→周期，地震→阶跃）
├── planner/               纯函数·可单测·不碰磁盘网络
│   ├── graph.py           由 registry 构建能力图
│   ├── feasibility.py     此刻哪些候选可选（环境探测结果收窄）
│   ├── score.py           auto 模式打分
│   └── plan.py            输出执行 DAG
├── core/                  唯一真相源
│   ├── schema.sql         SQLite DDL                     ← 新增
│   ├── fingerprint.py     三段式哈希（task/args/eval）
│   ├── normalize.py       规范化哈希（顺序无关+浮点归一） ← 新增
│   ├── filehash.py        三档文件指纹策略               ← 新增
│   ├── stale.py           失效传播 + 原因分类
│   ├── store.py           状态机 + 幂等守卫
│   ├── actions.py         干预队列（替代 RabbitMQ RPC）  ← 新增
│   └── ledger.py          provenance 账本
├── runtime/               唯一有副作用的层
│   ├── probe.py           环境探测
│   ├── render.py          Jinja2 → topsApp.xml / *.cfg
│   ├── executor.py        五阶段执行器                   ← 重写
│   ├── stream.py          日志流 + 双超时 + 取消          ← 新增
│   └── discover.py        产物发现（候选列表匹配）        ← 新增
├── engines/               薄封装·不含决策
│   ├── isce2.py  mintpy.py  pystamps.py  hyp3.py  pyaps.py
│   └── bridges/
│       ├── prep_isce.py           调官方
│       └── isce2_to_pystamps.py   我们建的桥（主 novelty）
├── audit/
│   ├── contract.yaml      指标来源契约
│   ├── verify.py          重解析比对
│   ├── runok.py           run_ok 双判定                  ← 新增
│   └── ladder.py          六级证据阶梯
├── brain/                 可完全拔掉
│   ├── provider.py        provider 路由表 + 单跳 fallback
│   ├── intent.py          自然语言 → 结构化意图
│   ├── select.py          候选集内选择（约束解码）
│   ├── triage.py          日志 → 失败分类（闭集）        ← 重命名
│   └── narrate.py         provenance → 方法章节草稿
├── loop/                  agent 主循环                    ← 新增整层
│   ├── driver.py          事件驱动主循环
│   ├── events.py          事件工厂（LLM data / UI update 分离）
│   └── budget.py          上下文预算 + compaction
├── report/                figures.py  methods.py  script.py（裸命令导出）
└── api/                   app.py + static/（prototype 产物）
```

---

## 3. Agent 主循环设计

### 3.1 为什么不能照抄竞品的 for 循环

两个竞品都是同一形状：

```python
# InSAR_Agent/src/insar_agent/agent.py:1443
for i in range(10):                       # 硬编码 10 轮，无配置
    response = client.chat.completions.create(...)
    if msg.tool_calls: ...continue
    else: return msg.content
return 'Agent loop exceeded maximum iterations.'    # :1486

# agentic-swmm/agentic_swmm/agent/planner.py:460
for step in range(1, self.max_steps + 1):          # 默认 40
    ...
else:
    final_text = f"planner stopped after max_steps={self.max_steps}"
```

这个形状对 InSAR 有三个致命问题：

1. **无法表达「等待 3 小时」** —— 循环体内同步等子进程会阻塞整个 agent，用户连问句都发不出。InSAR_Agent 的绕法是把长任务丢 daemon 线程（`agent.py:1113`）然后**靠前端 `setTimeout` 自动发一条假消息把 LLM 唤起**（`web/static/index.html:619-620`）：

   ```js
   if(obj.task_id){setTimeout(function(){doSend('auto_pipeline 已完成 (task_id='+obj.task_id+')');}, 1000);}
   ```
   工作流状态机被放进了前端 JS。这不可接受。

2. **进程重启即失忆** —— 循环状态在内存，`app.py:113-115` 只能把 running 改成 failed 加后缀「(服务重启)」。

3. **无法接收运行中干预** —— 循环在等 LLM 或等子进程，没有消费外部指令的位置。

### 3.2 事件驱动主循环

主循环不是「问 LLM → 调工具」，而是「取下一个事件 → 分派」。三个事件源合并进一个队列：

```
                    ┌──────────────┐
   用户消息 ────────>│              │
                    │  事件队列     │──> driver.step() ──> 分派
   执行器事件 ──────>│  (asyncio)   │         │
                    │              │         ├─> LLM 决策（仅候选集内）
   干预队列 ────────>│              │         ├─> 执行器投递
   (SQLite轮询)      └──────────────┘         ├─> 状态机更新 + 落盘
                                              └─> UI 事件广播
```

```python
# loop/driver.py 骨架
class Driver:
    """
    事件驱动 agent 主循环。

    与竞品 for 循环的根本区别：LLM 调用只是事件处理的一种，
    不是循环的骨架。长任务期间循环继续转，用户可继续对话。
    """
    async def run(self):
        while not self.stopped:
            ev = await self.inbox.get()          # 三源合并
            try:
                await self.dispatch(ev)
            except CancelledError:
                await self.on_cancel(ev)         # 保留部分成果（§1.6）
            except Exception as e:
                await self.on_error(ev, e)       # 结构化回灌，不崩循环
            finally:
                self.store.flush()               # 每个事件后落盘（§1.6）

    async def dispatch(self, ev):
        match ev.kind:
            case 'user.message':   await self.on_user(ev)
            case 'tool.finished':  await self.on_tool_done(ev)
            case 'step.finished':  await self.on_step_done(ev)
            case 'action.queued':  await self.on_intervention(ev)   # §1.7
            case 'timer.tick':     await self.on_tick(ev)
```

### 3.3 LLM 调用的四个约束

**约束一：单周期单决策，回合可多周期。**（2026-08-14 波次修订，原「一次只问一个决策」）每周期 LLM 仍只输出一个候选集内选择或一个白名单动作（闭集 10 项，见 `docs/AGENT-LOOP.md §3`），周期间由确定性循环驱动器（`loop/driver.py` 的 `converse_loop`）衔接，模型从不自行串接下一步；周期上限、同签名熔断与预算按本节约束二/约束四与 `docs/AGENT-LOOP.md §5` 执行。原依据（`DESIGN.md §8.3`：社区记录 Ollama 7b/14b/32b 一律「第二个工具永不执行」）不再用于禁止回合内多周期，保留为「本地小模型默认关闭自主循环」的开关条件。竞品虽然允许一次多个 tool_calls（`agent.py:1473`、`planner.py:549`），但我们的 LLM 不调工具 —— 单周期内它只回答一个选择题。

**约束二：终止条件必须显式且分类。** 竞品都不读 `finish_reason`（已核实两处均无）。我们的终止条件：

| 条件 | 值 | 处理 |
|---|---|---|
| 单轮决策数上限 | 1 | 结构化输出即结束 |
| 同一决策点重复询问 | 2 | 转人工，不再问 LLM |
| 失败分类连续同类 | 3 | 借鉴 `planner.py:39` `SAME_TOOL_RETRY_LIMIT=3` |
| 累计失败 checkpoint | 3 | 借鉴 `planner.py:47` `PIVOT_CHECKPOINT_LIMIT=3`，触发后停链问人 |
| 空响应 / 自我重复 | 1 | 立即熔断（`DESIGN.md:375`） |

`planner.py:41-47` 的注释解释了为什么要两个计数器：同工具计数会被成功调用重置（`planner.py:621-622`），而真实事故是「6 次失败跨 4 个工具，中间夹着成功的探测不断重置计数器」。累计计数器不重置。

熔断在此基础上细化为**软/硬两级 + `stuck` 一等状态**（absorb-O，`reference/AGENT_PRODUCTS_LEARNING.md` §5）：

- **软/硬两级**：决策签名 = 动作 + 排序键 JSON；连续同签名 3 次 → 向下一轮上下文注入软警告，5 次 → 硬熔断（cline 三段式，`cline/sdk/packages/core/src/runtime/safety/loop-detection.ts:66-87,113-116,144-156`）。上表「同一决策点重复询问 2 次转人工」不变——它管同一决策点的重复提问，这里管跨决策点的同签名循环。
- **`stuck` 是一等状态**，与 `failed` 分立（OpenHands 七态之一，`core/base/common.ts:67-75`）：failed 是执行层失败，走 §4.12 处置矩阵；stuck 是决策层原地打转，UI 处置不同——给「补充信息 / 转人工 / 终止」，而非失败分类的动作按钮。硬熔断进 stuck，不混入 failed。
- **上下文溢出自愈**：`context_window_exceeded` → 强制压缩 → 重试预算 = 1（每 run 一次），仍失败则抛可读错误停链（cline `agent-runtime.ts:470-471,891-932`，重试预算=1 防压缩-溢出循环）。

**约束三：`unresolved_failure` 标志。** 借鉴 `planner.py:451,592,623`。作用是防止「最后一轮自然语言总结把未修复的失败洗成成功」。这是 InSAR_Agent 完全没有的诚实性保障，而对我们尤其重要 —— 论文系统不能谎报成功。

**约束四：上下文预算。** 两个竞品都没有 compaction（已核实）。InSAR_Agent 全量重发（`agent.py:1420-1425`），且 `query_slc` 返回值含 footprints geojson（`agent.py:842-862`），几十景就顶爆上下文。

我们的策略：
- **决策请求不带对话历史**（2026-08-14 波次修订）—— 每次选择题仍是独立的结构化请求，只含「当前步骤 + 候选集 + 环境事实 + 上游摘要」；自主循环的 `cycle` 决策额外追加本回合已完成周期的结构化摘要（动作 + 结果状态，每条 ≤300 字，非原始日志），并纳入 compaction 预算（`loop/budget.py` 的 `clip_summary`）。对话历史仍然不进任何决策请求，历史膨胀从根上消除。
- **对话历史单独维护**，仅用于 intent 与 narrate，按 `agentic-swmm/agentic_swmm/agent/prompts.py:182-200` 的字符预算裁剪。
- **日志绝不进 LLM 上下文** —— 先用确定性代码切片（`DESIGN.md:384-386`：RULER / context rot 研究）。`triage.py` 只收「错误行 ±5 行」的窗口。

### 3.4 系统提示词策略

竞品的提示词规模：InSAR_Agent `SYSTEM_PROMPT` 11,210 字符 + `TOOL_DEFINITIONS` 16,515 字符 = **每轮重发 27.7 KB**（实测）；agentic-swmm base 5,276 字符 + 动态块。

InSAR_Agent 提示词里有整节 "ABSOLUTE ANTI-FABRICATION"（`agent.py:31-38`），还有中文关键词表硬性强制 `tool_choice='required'`（`agent.py:1430-1441`：`_prog_kw` 列了 22 个词含「托管/确认/好的」，加上「上一句含'是否'且用户输入 ≤6 字」的启发式），甚至要求「必须先调 `save_mintpy_overrides(task_id, {})` 再调 preview」这种凑数调用（`agent.py:46`）。

**这些补丁的存在本身说明架构错了。** 它们都在解决同一个问题：LLM 不肯调工具，只输出文本假装调了。它用云端 DeepSeek 都这样。

我们的解法不是抄关键词表，而是**在结构上让 LLM 无法假装**：

- LLM 输出的是**枚举索引**（`{"choice": 0}`），不是命令、不是路径、不是文件名。索引越界直接拒绝并回灌结构化错误。
- 借鉴 InSAR_Agent 唯一做对的设计 —— **缓存锚定**（`agent.py:481-499` `_cache_query_result` + `:514-556` `_resolve_scene_names`）：LLM 只能传 0-based `scene_indices`，系统从磁盘缓存读真值。注释原话：「LLM cannot fabricate scene names — the system reads from this cache.」
- 结构化输出走 **token 级硬约束**（GBNF/JSON Schema），不用 JSON mode。依据 JSONSchemaBench：约束解码快 50%，准确率提升最多 4%（`DESIGN.md:367-369`）。
- 约束保证结构不保证语义，**代码层再做值域校验**（`DESIGN.md:371-372`）：`{"temperature": 999}` 是合法 JSON。

系统提示词目标 **< 3 KB**，不含任何反幻觉说教 —— 幻觉在结构上不可能发生。

### 3.5 Brain 层五职责与降级路径

| 职责 | 输入 | 输出 | 失败降级 |
|---|---|---|---|
| `intent` | 自然语言 | `{region, time_range, target, scenario}` | 转表单让用户填 |
| `select` | 候选集 + 环境事实 | `{choice: int, reason: str}` | 取 registry 声明的 `recommend: true` |
| `triage` | 错误窗口（±5 行） | `{class: Literal[...], action: Literal[...]}` | 标 `unknown` 停链问人 |
| `narrate` | provenance JSON | 方法章节 Markdown | 用模板填空 |
| `cycle` | 回合目标 + 周期摘要 + 环境事实 | `{action \| say}` 闭集（`CycleResult`） | 停在当前周期，note 收尾转人工 |

**五个都能降级到无 LLM 路径。** 这就是 `DESIGN.md:233` 那条约束的实现：把 `brain/` 整层删掉，系统退化成手动可插拔流水线，仍完全可用、结果完全正确。（`cycle` 为 2026-08-14 波次新增：自主循环的逐周期决策，动作白名单与聊天回合的 `converse` 单步动作决策共用同一套校验纪律，设计全文见 `docs/AGENT-LOOP.md`。）

---

## 4. 执行层与中断恢复（核心章节）

这一节回答你最关心的问题：**小时级任务断了之后怎么不用从头开始。**

### 4.1 先看竞品为什么做不到

| 竞品 | 恢复能力 | 证据 |
|---|---|---|
| InSAR_Agent | **无**。重启只把 running 改 failed 加「(服务重启)」后缀 | `web/app.py:113-115` |
| InSAR_Agent | `WorkflowRunner` 有步级恢复（`start_from` + done 跳过 + state.json），但**主路径完全不用它** | `workflow.py:141-147`、`:108-109`、`:87-97` vs `agent.py:603` 另写一套 |
| InSAR_Agent | `_remaining.json` 保存未提交的 pair 列表，**只写不读** | `tools/submit_insar.py:443-448` |
| InSAR_Agent | `job_queue.cancel_job` 只把状态改 `'cancelling'`，**无任何代码读它** | `web/job_queue.py:92-95` |
| agentic-swmm | `resume_from_checkpoint` **只定义未调用**，循环仍从 1 开始 | `calibration_runner.py:241-248` vs `:187` |
| agentic-swmm | 文档自承：「so the next run can resume — **or at least diagnose** — what happened」 | `run_progress.py:8-10` |
| redun | 无显式 resume，靠缓存快进重放（纯函数式，不适合外部 CLI 副作用） | `redun/scheduler.py:2181-2341` |
| LangGraph | resume 时**整个节点从函数开头重跑** → 3 小时 ISCE2 白跑 | `DESIGN.md:412`（官方明确） |

**结论：没有一个现成方案能解决「小时级外部 CLI 任务的断点续跑」。** 唯一真正解决的是 aiida，方法是把长任务切成阶段 + 每个阶段入口幂等守卫。

### 4.2 aiida 的正解：五阶段 + 幂等守卫

`aiida-core/src/aiida/engine/processes/calcjobs/tasks.py:525-548` 的 docstring 把设计意图写得很清楚：

```
UPLOAD -> SUBMIT -> UPDATE -> STASH -> RETRIEVE
|   ^     |   ^     |   ^     |   ^     |   ^
v   |     v   |     v   |     v   |     v   |

The advantage of this design, is that the sequence is interruptable,
meaning, the process can potentially come back and start from where it left off.
```

每个阶段入口有幂等守卫（六处同构实现：`tasks.py:76-78, 137-140, 182-186, 294-295, 360-361, 399-400`）：

```python
if node.get_state() == CalcJobState.WITHSCHEDULER:
    assert node.get_job_id() is not None
    logger.warning(f'CalcJob<{node.pk}> already marked as WITHSCHEDULER, skipping task_submit_job')
    return node.get_job_id()
```

三个关键设计点：

1. **状态推进总在成功之后**（`tasks.py:165` 在 `else:` 分支里）。
2. **外部作业句柄持久化到节点属性**（`calcjob.py:66` `SCHEDULER_JOB_ID_KEY = 'job_id'`），重启后凭 job_id 重连调度器轮询，**不重跑**。
3. **内存 future 不跨重启**（`tasks.py:520-523` 的 `load_instance_state` 显式把 `_task`/`_killing` 置 None）—— 只有 DB 状态跨重启。

**这就是答案：不试图 checkpoint 子进程内部，只 checkpoint 它的句柄。**

### 4.3 我们的五阶段执行器

把 aiida 的五阶段映射到 InSAR：

```
PREPARED ──> LAUNCHED ──> RUNNING ──> COLLECTED ──> VERIFIED ──> DONE
   │            │            │            │             │
   │            │            │            │             └─ run_ok 双判定通过
   │            │            │            └─ 产物发现完成，指纹已算
   │            │            └─ 子进程存活，pid/日志偏移持久化
   │            └─ 子进程已启动，pid 已落盘
   └─ 配置已渲染，输入清单已校验，指纹已算

任一阶段失败 ──> FAILED（保留已完成阶段的成果）
运行中取消   ──> INTERRUPTED（保留 pid 记录与日志偏移，可 reattach）
```

各阶段的幂等守卫与恢复动作：

| 阶段 | 守卫（重启后检查） | 恢复动作 |
|---|---|---|
| PREPARED | 配置文件存在 且 指纹匹配 | 跳过渲染 |
| LAUNCHED | `pid` 已记录 | 进 RUNNING 判活 |
| RUNNING | `os.kill(pid, 0)` 存活？ | **存活 → 重新 attach 日志（从 `log_offset` 续读）；已死 → 查产物完整性决定 COLLECTED 还是 FAILED** |
| COLLECTED | 产物清单齐全 且 指纹匹配 | 跳过发现，直接校验 |
| VERIFIED | `run_ok` 记录存在 | 跳过校验 |

**RUNNING 阶段的重新 attach 是关键。** 这解决了「服务重启但 MintPy 还在跑」这个真实场景 —— InSAR_Agent 在这里直接放弃（标 failed）。

```python
# runtime/executor.py 骨架
STAGES = ['PREPARED', 'LAUNCHED', 'RUNNING', 'COLLECTED', 'VERIFIED']

async def execute_step(step_id, token):
    """
    五阶段执行。每阶段入口幂等守卫，状态推进在成功之后。
    借鉴 aiida tasks.py:137-140 的守卫模式与 :165 的推进时机。
    """
    rec = store.load(step_id)

    if rec.stage_lt('PREPARED'):
        cfg = render_config(step_id)              # Jinja2 → topsApp.xml / *.cfg
        validate_inputs(step_id)                  # 输入清单校验（PyStamps 缺 .base 会静默丢干涉图）
        store.advance(step_id, 'PREPARED', config_hash=hash_file(cfg))

    if rec.stage_lt('LAUNCHED'):
        # 不用 os.getpgid（宿主 Windows 没有，§0.5.2）。
        # wrapper 在 WSL 内把真实 pid/pgid/starttime 写进作业目录，见 §4.7。
        job = wsl.launch(step_id)                 # 返回作业目录句柄
        store.advance(step_id, 'LAUNCHED', job_dir=job.dir)

    if rec.stage_lt('RUNNING'):
        state, code = wsl.job_state(rec.job_dir)  # alive|finished|orphaned|unknown
        if state == 'alive':
            await follow_log(rec.job_dir, rec.log_offset, token)   # 重启后续读，不重跑
            state, code = wsl.job_state(rec.job_dir)
        if state == 'orphaned':
            # WSL 关机 / OOM kill：保留日志与已生成产物，不当作计算失败（§4.7）
            return store.advance(step_id, 'FAILED', reason='wsl_orphaned')
        store.advance(step_id, 'RUNNING', exit_code=code)

    if rec.stage_lt('COLLECTED'):
        arts = discover_artifacts(step_id)        # 声明式候选匹配（§1.4）
        store.advance(step_id, 'COLLECTED', artifacts=arts)

    if rec.stage_lt('VERIFIED'):
        ok, checks = run_ok(step_id)              # 退出码 + 领域校验（§1.4）
        store.advance(step_id, 'VERIFIED' if ok else 'FAILED', qa=checks)
```

阶段守卫的一个易错点：上面用 `rec.stage_lt(...)` 而不是 `rec.stage == ...`。
原因是恢复时可能跨越多个阶段（比如 `job.rc` 已写但产物还没发现，此时 stage 是
`LAUNCHED` 而实际该从 COLLECTED 继续）。用 `<` 比较让每个阶段独立判断该不该跳，
这正是 aiida 六处守卫的写法（`tasks.py:137-140` 检查的是状态值而非阶段序号）。

### 4.4 日志流与双超时

InSAR_Agent 的取消有个致命缺陷，必须规避。它的检查嵌在阻塞式读循环里（`tools/mintpy.py:213-232`）：

```python
for line in proc.stdout:                        # :213  阻塞在这里
    ...
    if stop_event and stop_event.is_set():      # :219  子进程静默时永远到不了
        proc.terminate()
        try: proc.wait(timeout=5)               # :223
        except subprocess.TimeoutExpired: proc.kill()   # :225
```

**MintPy 长时间不输出时，取消信号完全收不到。** 而 MintPy 静默 20 分钟是常态。

我们的解法：读循环放独立线程，主循环用 `select`/队列带超时轮询。

```python
# runtime/stream.py 骨架
async def stream_until_exit(step_id, token):
    """
    双超时：
      idle_timeout  —— 无输出超时（capability 声明，默认 30 min）
      total_timeout —— 总时长超时（capability 声明，如 ISCE2 干涉 6 h）
    取消检查不依赖子进程输出（规避 InSAR_Agent mintpy.py:219 的缺陷）。
    """
    cap = registry.capability_of(step_id)
    last_output = time.monotonic()
    started = last_output

    async for item in reader_queue(proc, poll=0.5):     # 0.5 s 必定醒一次
        now = time.monotonic()

        if token.cancelled:
            return await graceful_kill(proc, reason='user')

        if item is TICK:                                 # 无输出也会收到 TICK
            if now - last_output > cap.idle_timeout:
                return await graceful_kill(proc, reason='idle_timeout')
            if now - started > cap.total_timeout:
                return await graceful_kill(proc, reason='total_timeout')
            continue

        last_output = now
        store.append_log(step_id, item.line, offset=item.offset)   # 偏移持久化，供 reattach
        emit(ui_log(step_id, item.line, tone=classify_line(item.line)))
```

`graceful_kill` 的实现见 §4.7 —— 它不能用 `os.killpg`，原因是宿主机没有那个 API（§0.5.2）。

日志落盘策略（借鉴各家上限，全部实测过）：

| 项 | 值 | 依据 |
|---|---|---|
| 全量日志 | 落盘 `job.log`，不进内存 | `agentic-swmm/.../_shared.py:242-246` |
| 给 UI 的滑窗 | 最近 200 行 | InSAR_Agent 用 30/50（`job_queue.py:159-160`、`mintpy.py:238`），偏小 |
| 给 LLM 的窗口 | 仅错误行 ±5 行 | `DESIGN.md:384-386` |
| 单行上限 | 8 KB 截断 | MCP 用 5 MB（`mcp_client.py:165`），过宽 |

**日志回传给 LLM 的唯一形态：头尾截断 + 全量落盘按 `log_offset` 寻址**（absorb-L，
`reference/AGENT_PRODUCTS_LEARNING.md` §5）。头尾各半、中间打 `omitted N bytes` 标记
（codex HeadTailBuffer，`codex/codex-rs/core/src/unified_exec/head_tail_buffer.rs:31-42,114-132`）——
头是启动参数、尾是错误现场，正是 InSAR 要保的两端；全量永远在 `job.log`，按 offset 可寻址
（对应 gemini G10「旧工具大输出截断存盘留文件指针」）。两条附加纪律：

- **超时/失败错误必须携带已捕获输出尾部**（codex C8：模型能看到死前日志），绝不只回一句「timed out」。
- **截断规则写进 capability 说明**（cline L11：截断规则写进工具描述让模型自知）——模型知道中间被省略、知道全量可按 offset 追取。

**日志读取不走管道，走文件。** 这是被 §4.7 的跨边界问题逼出来的决定，但它本身更好：

```
子进程 stdout/stderr ──> 重定向到 WSL 内文件（>>）
                              │
宿主读取 ──> 打开同一文件，按 offset 增量读
```

三个好处：管道不会因读端慢而阻塞子进程（InSAR_Agent 的 `bufsize=1` 行缓冲在 MintPy
高频输出时会拖慢计算）；宿主重启后能从 `log_offset` 无缝续读（管道做不到）；
日志天然全量落盘，满足 §1.6 的「部分成果必须保留」。

代价是要处理「文件尚未创建」与「读到半行」，两者都好解：前者轮询等待，
后者只在读到换行符时才提交一行。

### 4.5 干预队列（替代 RabbitMQ RPC）

aiida-workgraph 的运行中干预走 kiwipy/RabbitMQ（`engine/workgraph.py:294`）。我们用 SQLite 表 + 主循环轮询：

```sql
CREATE TABLE pending_actions (
  id         INTEGER PRIMARY KEY,
  created_at REAL NOT NULL,
  scope      TEXT NOT NULL,          -- 'step' | 'run'
  target     TEXT NOT NULL,          -- step_id 或 run_id
  action     TEXT NOT NULL,          -- RESET|PAUSE|PLAY|SKIP|KILL|SET_METHOD|SET_PARAMS|USER_MESSAGE
  payload    TEXT,                   -- JSON
  deliver_as TEXT NOT NULL DEFAULT 'queue',  -- 'queue' 排队等当前步结束 | 'steer' 下一次 LLM 决策前插队
  consumed_at REAL                   -- NULL = 待消费；未消费即可编辑/撤回
);
```

动作集直接对齐 aiida-workgraph（`engine/task_actions.py:36-50`）：RESET / PAUSE / PLAY / SKIP / KILL，加两个 InSAR 特有的 SET_METHOD / SET_PARAMS，以及 USER_MESSAGE——掉线期间的用户消息也入此队列不丢失（OpenHands O9 的 `queued:true` 语义，`reference/AGENT_PRODUCTS_LEARNING.md` §2.3）。

主循环每次 `timer.tick`（1 s）消费一次。RESET 的语义就是 `reset_task` 递归（`task_state.py:170-203`），但我们额外做两件它没做的事：

1. **算影响范围并展示**，不静默重置。
2. **区分「影响结果的参数」与「不影响结果的参数」** —— 见 §5.4，这是真正的差异化点。

投递与撤回语义（absorb-J，pi steering/followUp 双队列 `pi/packages/agent/src/agent-loop.ts:182-190,259,263-268` + cline `pending-prompt-service.ts:168-280`）：

- **双投递**：`deliver_as='queue'` 排队、当前步结束后生效（默认，安全）；`deliver_as='steer'` 在下一次 LLM 决策前插队（用户「现在就要改主意」时用）。
- **可编辑/撤回**：`consumed_at IS NULL` 的动作可 UPDATE/DELETE，UI 为排队中的动作提供编辑与撤回入口。

### 4.6  run_ok 双判定

退出码 0 不等于成功。这是 `agentic-swmm` 学到的教训（swmm5 退出 0 但 rpt 里有 solver error），InSAR 同理：MintPy 可以「成功」地输出全 NaN 的时序。

```yaml
# registry/capabilities.py 里每个 capability 声明
snaphu_mcf:
  timeout: {idle: 1800, total: 7200}
  artifacts:
    - id: unw
      candidates: ["data/unw/*.unw", "data/unw/geo/*.unw"]   # §1.4 多候选
      required: true
  run_ok:
    - {check: exit_code, equals: 0}
    - {check: artifact_exists, id: unw}
    - {check: not_all_nan, id: unw}
    - {check: nan_fraction_below, id: unw, value: 0.5}
    - {check: log_absent, pattern: "ERROR|Segmentation fault"}
  quality_gate:                          # 失败则停链，不自愈（§1.5）
    - {metric: unwrap_coverage, min: 0.7, on_fail: stop}
```

---

### 4.7 跨 WSL 边界的进程控制（修正原设计缺陷）

**原 §4.3/§4.4 有一个致命错误**：写了 `os.killpg(proc.pgid, signal.SIGKILL)`。
实测（§0.5.2）宿主 Python 是 `win32`，`os.killpg` / `os.getpgid` / `signal.SIGKILL`
全部不存在，这段代码会直接 `AttributeError`。

更根本的问题是**进程身份跨不过边界**：

```
宿主（Windows）              WSL2（Linux VM）
─────────────────           ─────────────────────────────
python.exe
  └─ subprocess.Popen        wsl.exe --exec bash -lc "topsApp.py ..."
       proc.pid = 12345  ←── 这是 wsl.exe 的 Windows PID
                                  │
                                  └─ init ─ bash ─ topsApp.py  (Linux PID 891)
                                                     └─ mpirun ─ 8 × 子进程
```

`proc.pid` 是 `wsl.exe` 这个**中继进程**的 PID，与 Linux 侧的 `topsApp.py` 无关。
杀掉它**不保证**杀掉计算进程 —— Linux 侧可能继续以孤儿身份运行，继续吃 CPU 和写磁盘。
ISCE2 还会 fork MPI 子进程，问题更严重。

**解法：不依赖宿主的进程语义，改用 WSL 内的「作业目录 + 文件契约」。**

```
$WORK/.jobs/{run_id}/{step_id}/
├── cmd.sh          要执行的命令（渲染产物，可 diff、可复现）
├── job.pid         Linux 侧真实 PID（由 wrapper 写入）
├── job.pgid        Linux 侧进程组 ID
├── job.start       /proc/PID/stat 第 22 字段 starttime（防 PID 复用）
├── job.log         stdout+stderr（宿主按 offset 增量读）
├── job.rc          退出码（只在真正结束时写入，是完成的唯一标志）
└── job.cancel      宿主创建此文件 = 请求取消（wrapper 轮询）
```

wrapper 脚本（在 WSL 内运行，是唯一知道 Linux PID 的地方）：

```bash
#!/usr/bin/env bash
# runtime/wsl/wrapper.sh —— 由宿主渲染后放进作业目录
set -uo pipefail
JOB="$1"

set -m                                          # 独立进程组，便于整组信号
bash "$JOB/cmd.sh" >> "$JOB/job.log" 2>&1 &
CHILD=$!
echo "$CHILD"                        > "$JOB/job.pid"
ps -o pgid= -p "$CHILD" | tr -d ' '  > "$JOB/job.pgid"
awk '{print $22}' "/proc/$CHILD/stat" > "$JOB/job.start"

while kill -0 "$CHILD" 2>/dev/null; do          # 取消轮询：不依赖宿主信号能力
  if [ -f "$JOB/job.cancel" ]; then
    PGID=$(cat "$JOB/job.pgid")
    kill -TERM -"$PGID" 2>/dev/null             # 整组 SIGTERM
    for _ in $(seq 1 100); do                   # 宽限 10 s
      kill -0 "$CHILD" 2>/dev/null || break
      sleep 0.1
    done
    kill -KILL -"$PGID" 2>/dev/null             # 兜底 SIGKILL
    echo 143 > "$JOB/job.rc"                    # 128+15
    exit 143
  fi
  sleep 0.5
done

wait "$CHILD"; echo $? > "$JOB/job.rc"          # 唯一的完成标志
```

宿主侧三个操作全部退化为文件操作：

| 操作 | 实现 | 为什么可靠 |
|---|---|---|
| **判活** | 读 `job.rc`：存在 → 已结束；否则查 `job.pid` + `job.start` 二元组 | `starttime` 排除 PID 复用误判（长任务场景真实存在） |
| **取消** | `touch job.cancel` | 不需要宿主有信号能力；wrapper 在 Linux 侧执行整组 kill |
| **续读日志** | 打开 `job.log`，`seek(log_offset)` | 宿主重启后无缝续接 |

判活的 Python 侧（完全不调 `os.kill`）：

```python
# runtime/wsl/probe.py
def job_state(job_dir) -> tuple[str, int | None]:
    """
    返回 (state, exit_code)。state 取值 alive|finished|orphaned|unknown。
    绝不使用 os.kill / os.killpg —— 宿主是 Windows（§0.5.2）。
    """
    rc = job_dir / 'job.rc'
    if rc.exists():
        return 'finished', int(rc.read_text().strip() or -1)

    pid_f, start_f = job_dir / 'job.pid', job_dir / 'job.start'
    if not pid_f.exists():
        return 'unknown', None                      # wrapper 尚未启动

    pid = pid_f.read_text().strip()
    want = start_f.read_text().strip() if start_f.exists() else None
    # 在 WSL 内核实，而不是在宿主用 os.kill
    got = wsl_run(f"awk '{{print $22}}' /proc/{pid}/stat 2>/dev/null || true")
    if not got:
        return 'orphaned', None                     # 进程没了但没写 rc
    if want and got != want:
        return 'orphaned', None                     # PID 被复用，原进程已死
    return 'alive', None
```

`orphaned` 是新增状态，对应一类真实故障：**WSL 被关机（`wsl --shutdown`）、系统休眠、
或进程被 OOM killer 杀掉**，此时 `job.rc` 永不出现。原设计只有 alive/finished 二态，
遇到这种情况会永久等待。处置：标 FAILED，保留 `job.log` 与已生成产物（§1.6），
UI 明确告知「WSL 停止导致中断，非计算失败」—— 这两者的处置完全不同。

**副产品：`cmd.sh` 就是等价裸命令脚本。** `DESIGN.md:765` 的第 3 条 baseline 要求
不再需要额外实现 —— 按序拼接各步 `cmd.sh` 即可，且它是**真实执行过的那一份**，
不是重新生成的近似品。这比 agentic-swmm 的做法更强。

### 4.8  WSL 生命周期管理

WSL 的三个行为会破坏长任务，必须显式处理。

**空闲自动关机。** WSL2 在最后一个进程退出后约 8 秒回收 VM。两步之间若无任何 WSL 进程，
VM 关机，内存态与 `/tmp` 尽失。解法：整个 run 期间维持一个保活进程
（`wsl.exe -e sleep infinity`），引用计数管理，run 结束才释放。

**外部关机不可预防。** `wsl --shutdown`、系统休眠、宿主蓝屏都会让作业消失。
只能检测（`orphaned`）并优雅降级。因此**所有中间状态必须落盘到 WSL 内的持久位置**，
作业目录放 `$WORK/.jobs/`，绝不放 `/tmp`。

**路径与性能。** 跨 9p 文件系统访问有显著开销。工作区必须在 WSL 的 Linux 文件系统上，
**不是** `/mnt/e/`。但这里有个新发现的冲突：WSL 的 ext4 虚拟磁盘默认落在 C:，
而实测 C: 只剩 36 G（§0.5.3）。

```
必做：wsl --import Ubuntu E:\wsl\Ubuntu ubuntu.tar    # 显式指定到 E:（483 G 可用）
工作区：/home/insar/work                               # Linux 原生 fs
归档读取：/mnt/f/                                      # 冷数据才走 9p
```

这修正了 `DESIGN.md:456` 的表述 —— 准确说法是**工作区必须在 WSL 的 Linux 文件系统上，
且承载该虚拟磁盘的物理盘是 SSD**，而不只是「在 SSD 上」。


### 4.9 路径映射与句柄边界

**三个坐标系必须显式区分**，混用是竞品的常见错误来源：

| 坐标系 | 形态 | 谁用 |
|---|---|---|
| 逻辑句柄 | `task_id` + `art_id`（如 `yushu-2023` / `unw`） | LLM、UI、API |
| WSL 路径 | `/home/insar/work/yushu-2023/data/unw` | `cmd.sh`、引擎 |
| 宿主路径 | `\\wsl.localhost\Ubuntu\home\insar\work\...` | 宿主读日志/产物 |

规则：

1. **LLM 只见逻辑句柄。** 借鉴 InSAR_Agent 的 `task_id NEVER contains slashes`
   全局约定，这消掉整类路径幻觉。校验：`^[a-z0-9][a-z0-9_-]{2,63}$`，
   拒绝 `.`、`/`、`\`、空格、Unicode。
2. **`cmd.sh` 里只出现 WSL 路径**，由渲染层从句柄解析，绝不接受外部传入的绝对路径。
3. **宿主访问统一走一个函数**，不散落在各处：

```python
# runtime/wsl/paths.py
DISTRO = 'Ubuntu'
WSL_ROOT = PurePosixPath('/home/insar/work')

def wsl_path(task_id: str, *parts: str) -> PurePosixPath:
    _check_handle(task_id)
    for p in parts:
        if '..' in PurePosixPath(p).parts:        # 目录穿越防护
            raise ValueError(f'path escape: {p}')
    return WSL_ROOT / task_id / PurePosixPath(*parts)

def host_path(task_id: str, *parts: str) -> Path:
    """宿主侧读取用。注意 \\wsl.localhost 走 9p，只用于小文件（日志/json），
    绝不用于遍历产物目录或读栅格 —— 那些操作必须在 WSL 内做。"""
    return Path(rf'\\wsl.localhost\{DISTRO}') / str(wsl_path(task_id, *parts)).lstrip('/')
```

**性能红线**：宿主侧只读 `job.log` / `job.rc` / 小 JSON。任何「遍历目录」
「统计文件数」「读栅格」都必须在 WSL 内执行后回传结果 —— 9p 上 `os.walk`
一个含数万文件的 ISCE2 目录会慢到不可用。这条约束直接影响 §5.5 的文件指纹实现：
**指纹计算在 WSL 内做，宿主只收哈希值**。

对应的执行契约：

```bash
# 在 WSL 内批量算指纹，一次调用返回全部结果，避免逐文件跨边界
find "$DIR" -type f -printf '%p\t%s\t%T@\n' | sort
```

4. **归档盘（`/mnt/f/`）只读不写。** 冷数据（原始 SLC）留在 Windows 盘，
   通过 9p 只读挂载；所有中间产物写 Linux fs。这既是性能要求也是安全边界。

### 4.10 磁盘预算与产物 GC

这一节解决 §1.5 列的「磁盘满」失败类。竞品无一实现前置拦截，而 InSAR 撑爆磁盘是必然事件。

**为什么必须前置：** E: 剩 483 G（§0.5.3）。46 景 S1 SLC 原始数据约 110 G，
ISCE2 配准产物（`.slc.full` + geom）约 2–3× 输入，干涉图按对数线性增长。
一次全链跑完可能需要 300–400 G —— 不预检就是跑到一半失败，损失数小时。

**预算声明进 capability：**

```python
Capability(
    id='isce2_ifg_multilook',
    disk=DiskEstimate(
        # 用输入规模的函数表达，而非常数
        formula='pairs * 0.34 + scenes * 1.2',   # GB
        peak_multiplier=1.4,                      # 计算期临时文件峰值
    ),
)
```

**三级闸门：**

| 时机 | 检查 | 动作 |
|---|---|---|
| 计划生成时 | 全链预算 vs 可用空间 | 不足 → 计划阶段就报警，给出「先归档旧 run」或「减少景数」选项 |
| 每步启动前 | 本步预算 × 1.4 vs 可用 | 不足 → 停链（不是失败），提示可 GC 的候选 |
| 运行中每 60 s | 可用空间 < 20 G | 软告警；< 8 G 主动暂停当前步（保留断点，优于被 OS 写失败打断） |

主动暂停优于等 `ENOSPC`：后者会让 ISCE2 写出**截断的产物文件**，
而截断文件的 `(size, mtime)` 指纹是「有效」的（§5.5 的伪哈希看不出内容不完整），
下游会读到坏数据。这是伪哈希策略的真实风险，必须用磁盘闸门兜住。

**产物分级与 GC：**

```
tier 0  原始输入（SLC/DEM/orbit）    永不自动删。重下代价 = 数小时网络
tier 1  最终成果 + provenance        永不自动删。体积小
tier 2  关键中间态（unw / timeseries）保留最近 N 个 run（默认 2）
tier 3  可重算中间态（.int/.cor/rslc）run 完成且下游 VERIFIED 后可删
tier 4  临时文件（.vrt/.aux/工作区）  步骤 VERIFIED 后立即删
```

tier 4 的立即清理不只是省空间 —— PyStamps 的反选式文件发现会把
`.vrt/.hdr/.aux` 残留误当数据（`DESIGN.md:139`）。**清理是正确性要求，不是优化。**

GC 必须是**显式动作**，不能自动静默执行：删了 tier 3 后若用户改下游参数，
重跑需要先重算被删的中间态。所以 GC 前要算出「删除后哪些步骤会从『跳过』变成『需重算』」
并告知用户 —— 这是 §5.1 失效原因分类的一个额外用途（`artifact_missing`）。

UI 表达见 §7.2 的环境面板与 §7.6 的预算条。

### 4.11 并发、锁与资源仲裁

InSAR 的并发不是「跑得更快」的优化，而是**正确性问题** —— 两个步骤同时写同一个 HDF5
会直接损坏文件。

**四类冲突与处置：**

| 冲突 | 触发场景 | 处置 |
|---|---|---|
| 同一 run 内 DAG 并行 | 第 5 步滤波与第 2 步 DEM 无依赖 | 允许，但受资源池限制 |
| 同一产物并发写 | 用户点两次「运行」 | **步级排他锁**，第二次直接拒绝 |
| HDF5 文件锁 | MintPy 多进程读同一 `.h5` | `HDF5_USE_FILE_LOCKING=FALSE`（`DESIGN.md:472`）+ 应用层锁 |
| 跨 run 争抢 | 两个会话同时跑 | 全局资源池 + 排队 |

**锁的实现走 SQLite，不用文件锁。** 理由：文件锁在 9p 上行为不可靠，而 SQLite
的事务在 WSL/宿主两侧都可信（同一个 `.db` 文件，但只有宿主进程访问它）。

```sql
CREATE TABLE leases (
  resource   TEXT PRIMARY KEY,        -- 'step:yushu-2023:6' | 'pool:cpu' | 'artifact:...'
  holder     TEXT NOT NULL,           -- run_id + 进程标识
  acquired   REAL NOT NULL,
  heartbeat  REAL NOT NULL,           -- 心跳，用于检测持有者已死
  ttl        REAL NOT NULL
);
```

**租约必须带心跳而非固定 TTL。** 长任务可能跑 6 小时，固定 TTL 要设到 6 小时以上，
那么进程崩溃后锁要等 6 小时才释放。心跳方案：每 30 s 更新 `heartbeat`，
超过 90 s 未更新视为持有者已死，可抢占。抢占前必须先查 §4.7 的 `job_state`
确认 WSL 侧作业真的不在了 —— 宿主崩溃不代表计算停了。

**资源池仲裁。** 实测硬件（`DESIGN.md:446-452`）：24 核 32 线程 / 63.7 G RAM，
WSL 分 20 核 40 G。资源声明进 capability：

```python
Capability(id='isce2_ifg_multilook',
           cpu=8, mem_gb=12, io='heavy')      # io: light|medium|heavy
```

调度规则（简单但够用，不引入调度器）：

- CPU 总量 ≤ 20（留 4 核给系统，符合 `DESIGN.md:463` 的重型计算管控）
- 内存总量 ≤ 32 G（留 8 G 余量给 WSL 自身与文件缓存）
- **`io='heavy'` 的步骤最多 1 个并发** —— 单 SSD 是 I/O 咽喉（`DESIGN.md:450`），
  两个 heavy 步骤并行会让 24 核一起空等磁盘，总吞吐反而下降

最后一条是 InSAR 特有的反直觉结论：**配准阶段并行度不该拉满**。

**并行度是资源参数不是科学参数**（§5.4）—— 改 `cpu: 8 → 16` 不该标脏任何产物。
但有一个例外必须警惕：某些引擎的并行实现会影响数值结果（浮点求和顺序）。
若发现某 capability 有此问题，在 registry 里显式标 `parallel_affects_result=True`，
把并行度提升为科学参数。这是原设计遗漏的一个分类边界。

### 4.12 失败分类闭集

§1.5 说了失败可分类，这里定死闭集。**LLM 只能从这个枚举里选，不能发明新类别。**

```python
class FailureClass(StrEnum):
    # 环境类 —— 可自愈
    NETWORK_TRANSIENT = 'network_transient'   # 超时/连接重置 → 指数退避重试 ≤3
    AUTH_EXPIRED      = 'auth_expired'        # 401/403 → 换账号（submit_insar.py:401-410）
    QUOTA_EXHAUSTED   = 'quota_exhausted'     # 配额不足 → 换账号或排队
    SERVICE_DOWN      = 'service_down'        # 5xx/队列积压 → 降级（见下表）
    # 资源类 —— 可自愈但需调整
    DISK_FULL         = 'disk_full'           # → 触发 GC 建议，暂停（§4.10）
    OOM               = 'oom'                 # → 降并行度重试 ≤1，仍失败则停
    # 环境损坏 —— 停链
    TOOL_MISSING      = 'tool_missing'        # 引擎不在 PATH → 停，报环境问题
    WSL_ORPHANED      = 'wsl_orphaned'        # WSL 关机（§4.7）→ 停，可续跑
    # 参数/数据类 —— 停链，回到决策点
    PARAM_INVALID     = 'param_invalid'       # Schema 或引擎拒绝 → 回候选集
    DATA_QUALITY      = 'data_quality'        # 相干性过低/基线过长 → 质量门，停
    CONTRACT_BROKEN   = 'contract_broken'     # 产物缺失/维度不符 → 停，报 bug
    # 兜底
    UNKNOWN           = 'unknown'             # → 停链问人，绝不猜
```

**分类靠确定性规则优先，LLM 兜底。** 这是对原设计的重要修正 —— 原来写「LLM 读日志分类」，
但大多数失败有确定特征，用正则更快更准：

```python
RULES = [                                     # 顺序敏感，先匹配先生效
    (r'No space left on device|ENOSPC',        FailureClass.DISK_FULL),
    (r'Killed|Out of memory|MemoryError',      FailureClass.OOM),
    (r'command not found|No such file.*\.py',  FailureClass.TOOL_MISSING),
    (r'HTTP 40[13]|Unauthorized|Forbidden',    FailureClass.AUTH_EXPIRED),
    (r'HTTP 5\d\d|Service Unavailable',        FailureClass.SERVICE_DOWN),
    (r'timed out|Connection reset',            FailureClass.NETWORK_TRANSIENT),
]
# 全部未命中才问 LLM，且只给错误行 ±5 行（DESIGN.md:384-386）
```

好处：规则命中不消耗 LLM 调用，也不受幻觉影响；`UNKNOWN` 的比例本身是可观测指标 ——
它升高说明有新失败模式需要加规则。

**降级矩阵（`SERVICE_DOWN` 的处置）必须显式声明证据级别代价：**

| 原方法 | 降级为 | 证据级别影响 |
|---|---|---|
| `tropo_era5_pyaps` | `tropo_height_corr` | validated → **checked**（大气校正精度下降） |
| `dem_copernicus` | `dem_srtm` | 无影响（同精度级别） |
| `dem_copernicus` | `dem_local` | 需人工确认覆盖范围 |
| `crossval_ps_sbas` | `loop_closure` | validated → **checked**（失去双链交叉验证） |

InSAR_Agent 的 ERA5 降级（`agent.py:1052-1067`）只打一行日志，**没有告知用户结果精度变了**。
我们必须在 UI 显式表达（§7.4 的 `degrade` 条目）并写进 provenance 与证据边界表 ——
否则论文里声称的精度是虚的。

**降级不是静默的默认行为。** 向导模式下自动降级 + 显式告知；专家模式下**必须问**。

### 4.13 质量门阈值的来源纪律

**这是论文可信度的关键，也是最容易被审稿人问倒的地方。** 每个阈值必须能回答
「这个数从哪来」，只有三种合法答案：

| 来源等级 | 含义 | 论文可引用性 |
|---|---|---|
| **A · 上游默认** | 引擎官方默认值或官方文档推荐 | 可直接引用，注明版本 |
| **B · 文献** | 同类研究报告的取值 | 可引用，需给 DOI |
| **C · 本地标定** | 用本项目数据实测确定 | 必须在论文中说明标定过程 |
| **✗ 拍脑袋** | 无来源 | **禁止进入 contract.yaml** |

**当前状态诚实盘点。** 我在 §4.6 和原型里写的阈值，绝大多数是 **✗ 拍脑袋**：

| 阈值 | 我写的值 | 真实来源 | 处置 |
|---|---|---|---|
| `min_coherence` | 0.25 | ✗ 编造 | 待定：MintPy 无此单一默认，需查 SNAPHU 文档 |
| `corr_threshold`（PS/SBAS 一致性） | 0.85 | ✗ 编造 | **必须本地标定**，无先例可循 |
| `unwrap_coverage` | 0.70 | ✗ 编造 | 待查文献 |
| `nan_fraction_below` | 0.50 | ✗ 编造 | 待定 |
| `max_temporal_baseline` | 120 天 | ✗ 编造 | S1 12 天重访，需按去相干特性定 |
| `esd_coherence_threshold` | 0.85 | **A** | ISCE2 `topsApp` 默认值即 0.85 |
| `stepFuncDate` | 20190706T0320 | **A** | 实测配置文件里就是这个值（Ridgecrest 同震日） |
| `weightFunc` | `no` | **A** | 实测配置文件；MintPy 默认是 `var`，此处是有意覆盖 |

**只有 3 个有据可查，5 个是编的。** 这些编造值现在只出现在设计文档与前端 mock 里，
危害有限；但一旦写进 `audit/contract.yaml` 并用于「质量门通过/不通过」的判定，
就变成论文里的硬伤 —— 审稿人问「为什么 0.85 就算通过」，无法回答。

**纪律：**

1. `contract.yaml` 每个阈值必须带 `source` 字段，值为 `upstream_default` / `literature` /
   `local_calibration` 之一，且后两者必须填 `ref`：

```yaml
crossval_ps_sbas:
  corr_threshold:
    value: 0.85
    source: local_calibration       # 或 upstream_default / literature
    ref: "experiments/2026-XX-crossval-calibration.md"
    status: PENDING                 # ← 未标定完成前必须是 PENDING
```

2. **`status: PENDING` 的阈值不参与硬 gate**，只产出 warning。这样系统能跑，
   但不会用未经证实的标准宣称「质量门通过」。
3. 校验测试守护：CI 检查 `contract.yaml` 里没有 `source` 缺失或 `status: PENDING`
   却被用作 `on_fail: stop` 的条目。这正是 `DESIGN.md:671` 批评 agentic-swmm 缺失的
   「指标来源契约守护测试」—— 我们不能重犯。

**证据阶梯的连带修正。** §6.3 定义 `validated` 需要「双链交叉验证相关系数 ≥ 契约阈值」。
既然该阈值现在是 PENDING，那么**当前系统最高只能达到 `audited`，不能声称 `validated`**。
原型里 `S.evidenceLevel = 4`（validated）是虚的，应改为 2（audited）。

这条自我约束看起来是给自己设障，但它恰好是论文的加分项 —— `DESIGN.md:312-322`
要求的「证据边界声明」和 "X, not Y" 句式，本质就是诚实地说清没做到什么。
把 PENDING 状态做进系统，等于让证据边界自动生成而非手写。

## 5. 指纹与失效传播

### 5.1 三段式哈希

借鉴 redun 的分层（`redun/redun/hashing.py:94-114`），**三个哈希分开算、分开存**，这样才能回答「为什么重跑」：

```python
task_hash = H(["Task", name, "version", version])          # 或含源码，见 5.3
args_hash = H(["Args", normalize(method), normalize(params)])
eval_hash = H(["Eval", task_hash, args_hash, upstream_eval_hashes])
```

对比 redun 的 `hash_eval`（`hashing.py:107-114`）：它的 eval_hash **不含上游**（上游只进用于记录的 `call_hash`，`hashing.py:124-130`）。我们把上游并进 eval_hash，走 aiida 的 Merkle 级联路线（`aiida-core/src/aiida/orm/nodes/process/process.py:88-102`），因为 InSAR 的依赖是显式 DAG，级联语义更直观。

失效原因分类（接到 UI 上，redun 的 `explain_cache_miss` 只用于日志）：

| 原因 | 判定 | UI 文案 |
|---|---|---|
| `method_changed` | args_hash 变且 method 不同 | 「换了解缠方法」 |
| `param_changed` | args_hash 变且 method 相同 | 「改了 min_coherence: 0.25 → 0.3」 |
| `upstream_changed` | 自身 args_hash 未变，上游 eval_hash 变 | 「第 5 步重跑了」 |
| `tool_upgraded` | task_hash 变（工具版本） | 「MintPy 1.6.4 → 1.6.5」 |
| `artifact_missing` | 产物指纹为 `-1` | 「产物被删除」 |
| `no_metadata` | 无历史记录（首次执行 / 记录丢失） | 「首次执行，无历史记录」 |
| `outdated_metadata` | 记录的 `record_version` 低于当前版本（§6.1） | 「指纹算法已升级，此步骤记录不可比」 |

后两类对齐 Snakemake「比较前先查 RECORD_FORMAT_VERSION，低于阈值判不可比、不触发」的门控（`snakemake/src/snakemake/persistence/__init__.py:32,660-696`，absorb-M）——没有它们，指纹算法升级后旧记录会被误判可复用。

级联判定的三个语义（absorb-N，`reference/AGENT_PRODUCTS_LEARNING.md` §4.2/§4.3）：

- **masked 剪枝**：拓扑序遍历中某步判脏，即对其下游 BFS 入 masked 集合，跳过指纹计算、只记 `upstream_changed`（snakemake `dag.py:1618-1628`）。级联标脏是一次写标记，不是对每个下游重算指纹。
- **early cutoff**：上游重跑后产物指纹未变 → 下游 eval_hash 不变 → 自动清除 stale。来源是 dvc 的隐式传播链（上游 outs 哈希不变则下游 dep 不报 modified）；Snakemake 无此优化（无条件级联）。
- **changed 判定短路序**：便宜的在前——内存哈希比对（task/args/eval_hash）先判，IO 指纹（文件 stat / 内容哈希）最后判，对齐 dvc 的 `changed_stage() or changed_deps() or changed_outs()`（`dvc/dvc/stage/__init__.py:361-373`）。

### 5.2 规范化哈希：InSAR 必须处理的三个坑

aiida 的 `make_hash`（`aiida-core/src/aiida/common/hashing.py`）解决了三个我们一定会踩的坑：

**坑一：dict 键序。** 同一份 YAML 换个 key 顺序不应导致全量重算。aiida 按**已哈希的 key** 排序（`hashing.py:159-176`），而非按 key 原值 —— 好处是 key 类型混杂（str/int 混用）时也能全序，不 `TypeError`。

**坑二：浮点表示。** 这是 InSAR 的重灾区（相干阈值、多视因子、alpha）。aiida 先按有效位格式化成字符串再哈希，并把 `-0.0` 归一为 `0.0`（`hashing.py:197-202` + `:310-320`）。**不做这个，`0.3` 和 `0.30000000000000004` 会是两个不同任务。**

**坑三：嵌套结构歧义。** aiida 给容器加配对的闭合 digest（`hashing.py:123` `_END_DIGEST`）。没有它，`[[1],[2]]` 和 `[[1,2]]` 哈希相同。

类型域分离两家都做了，选一个：aiida 用 blake2b 的 `person=` 参数做密码学域分离（`hashing.py:119-120`），redun 用 bencode tag 前缀（`hashing.py:47-54`）。我们用后者，因为不引入 blake2b 依赖，`hashlib.sha256` 够用。

### 5.3 源码是否参与哈希

redun 默认把函数源码纳入 task_hash（`redun/redun/task.py:433-467`），用 `inspect.getsource` + 正则剥掉装饰器行（`redun/redun/utils.py:320-332`）。但它留了逃逸阀：一旦手工指定 `version`，源码彻底不参与（`task.py:454`）。

**InSAR 必须用 version 逃逸阀。** 理由：改一行日志字符串不该触发 12 小时重算。所以：

- `capability` 声明里写显式 `version`，人工递增。
- 但 `tool_version`（ISCE2/MintPy/SNAPHU 的实际版本号）**必须**进 task_hash —— 这是 `DESIGN.md:258` 的第 5 触发器，竞品全都缺失。
- 另外 redun 有个细节值得抄：`_task_options_base`（模块加载时的选项）**故意不哈希**，只有运行时 override 才哈希（`task.py:441-442`，注释："not allowed to impact the results of computation"）。这就是「资源配置不该影响结果指纹」的显式建模，直接对应 §5.4。

### 5.4 参数分类：真正的差异化点

研究确认：aiida 建模了「不参与哈希的参数」（`process.py:177-179` `_hash_ignored_attributes`），redun 建模了「不影响结果的选项」（`task.py:441-442`），但**没人把这个区分和「人工改参数后自动决定是否标脏」连起来**。

这是可站得住的创新点。InSAR 参数天然分三类：

| 类别 | 例子 | 进 args_hash？ | 改动后果 |
|---|---|---|---|
| **科学参数** | `min_coherence`, `alpha`, `max_temporal_baseline`, `unwrap_method` | ✓ | 标脏自身 + 全部下游 |
| **资源参数** | `threads`, `memory_limit`, `tmp_dir`, `parallel_pairs` | ✗ | 不标脏，仅记 provenance |
| **呈现参数** | `dpi`, `cmap`, `output_format`, `figure_size` | ✗（但标脏出图步） | 只标脏第 10 步 |

registry 里显式声明：

```python
Capability(
    id='snaphu_mcf',
    params={
        'min_coherence': Param(0.25, kind='science', range=(0, 1)),
        'threads':       Param(8,    kind='resource', range=(1, 32)),
        'cost_mode':     Param('SMOOTH', kind='science', enum=[...]),
    },
)
```

**用户体验差异**：改 `threads: 8 → 20` 时，系统说「资源参数变更，不影响结果，无需重跑」，而不是把 6 个下游标红要求跑 93 分钟。这个交互 aiida-workgraph 做不到（它是全图 diff）。

### 5.5 文件指纹三档策略

见 §1.3 的表。实现要点：

```python
# core/filehash.py
def fingerprint(path: Path, policy: str) -> str:
    """
    policy: 'path' | 'stat' | 'content'
    'stat' 档借鉴 redun/redun/file.py:463-475，不存在编码为 size=-1,mtime=-1，
    使「产物被删」成为正常的哈希差异而非异常分支。
    """
    if policy == 'path':
        return H(['File', 'path', str(path)])
    if not path.exists():
        return H(['File', policy, str(path), -1, -1])       # 关键：-1 编码
    st = path.stat()
    if policy == 'stat':
        return H(['File', 'stat', str(path), st.st_size, st.st_mtime_ns])
    return H(['File', 'content', str(path), st.st_size, _stream_sha(path)])

def _stream_sha(path, block=4 << 20):     # 4 MB；redun 默认 1024 对 GB 级文件太小
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(block), b''):
            h.update(chunk)
    return h.hexdigest()
```

针对 §1.3 发现的碰撞风险（5 个栅格字节数完全相同），`stat` 档额外加一条：**目录级产物用「文件数 + 各文件 (name,size,mtime) 排序后哈希」**，而非仅目录本身的 stat。

## 6. 状态存储与 Provenance

### 6.1 SQLite 表结构

参照 redun 的三层解耦（provenance / 执行记录 / 缓存分离，`redun/redun/backends/db/__init__.py:662-1006`），但压到最小可用集：

```sql
-- 运行（一次用户发起的完整处理）
CREATE TABLE runs (
  run_id      TEXT PRIMARY KEY,        -- UUID + 单调时间戳（DESIGN.md:639：不能靠目录名）
  session_id  TEXT NOT NULL,
  created_at  REAL NOT NULL,
  status      TEXT NOT NULL,           -- planning|running|paused|done|failed
  intent      TEXT,                    -- JSON：结构化意图
  argv        TEXT,                    -- 等价裸命令（DESIGN.md:765 第 3 条要求）
  env_hash    TEXT,                    -- conda env + 工具版本集合的哈希
  git_head    TEXT, git_dirty INTEGER,
  agent_hash  TEXT                     -- prompt+tools schema 哈希（DESIGN.md:644-646）
);

-- 步骤（DAG 节点）
CREATE TABLE steps (
  run_id      TEXT NOT NULL,
  step_id     INTEGER NOT NULL,
  capability  TEXT NOT NULL,           -- 'snaphu_mcf'
  method      TEXT NOT NULL,
  params      TEXT NOT NULL,           -- JSON
  task_hash   TEXT NOT NULL,
  args_hash   TEXT NOT NULL,
  eval_hash   TEXT NOT NULL,
  record_version INTEGER NOT NULL,     -- 指纹记录格式版本：低于当前值 → stale_reason='outdated_metadata'（§5.1），
                                       -- 防指纹算法升级后旧记录被误判可复用
                                       -- （对齐 snakemake RECORD_FORMAT_VERSION 门控与 dvc schema 版本，absorb-M）
  stage       TEXT NOT NULL,           -- PREPARED..VERIFIED|FAILED|INTERRUPTED
  stale       INTEGER DEFAULT 0,
  stale_reason TEXT,                   -- method_changed|param_changed|upstream_changed|...
  pid         INTEGER, pgid INTEGER,   -- 外部作业句柄（aiida calcjob.py:66 的思路）
  ext_job_id  TEXT,                    -- HyP3 job id 等
  log_path    TEXT, log_offset INTEGER,-- 供 reattach 续读
  exit_code   INTEGER,
  run_ok      INTEGER,                 -- 双判定结果（≠ exit_code）
  started_at  REAL, ended_at REAL,
  PRIMARY KEY (run_id, step_id)
);

-- 依赖边（显式 DAG，供级联标脏）
CREATE TABLE edges (
  run_id TEXT, parent INTEGER, child INTEGER,
  PRIMARY KEY (run_id, parent, child)
);

-- 产物
CREATE TABLE artifacts (
  run_id     TEXT NOT NULL,
  step_id    INTEGER NOT NULL,
  art_id     TEXT NOT NULL,            -- 'unw' | 'velocity' | ...
  path       TEXT NOT NULL,
  kind       TEXT, layout TEXT,        -- DataKind（DESIGN.md §2）
  policy     TEXT NOT NULL,            -- path|stat|content
  fp         TEXT NOT NULL,            -- 三段编码 "<policy>:<algo>:<digest>"，如 content:sha256:ab12…
                                       -- 算法名显式入档（dvc serialize.py:160-161）；不存在仍走 -1 编码（§5.5）
  record_version INTEGER NOT NULL,     -- 同 steps.record_version：指纹算法升级门控
  size       INTEGER, mtime_ns INTEGER,
  PRIMARY KEY (run_id, step_id, art_id)
);

-- 命令轨迹（每次实际执行，一个 step 可能多次）
CREATE TABLE commands (
  id         INTEGER PRIMARY KEY,
  run_id TEXT, step_id INTEGER,
  argv       TEXT NOT NULL,            -- JSON 数组，可序列化（DESIGN.md:766）
  cwd        TEXT, env_delta TEXT,
  exit_code  INTEGER, duration REAL,
  stdout_path TEXT, stderr_path TEXT,
  attempt    INTEGER DEFAULT 1
);

-- 指标 + 来源契约校验（DESIGN.md §7.1）
CREATE TABLE metrics (
  run_id TEXT, name TEXT, value REAL, unit TEXT,
  source_artifact TEXT, source_field TEXT,
  reparsed_ok INTEGER,                 -- 重解析比对结果
  PRIMARY KEY (run_id, name)
);

-- 干预队列（§4.5）
CREATE TABLE pending_actions (...);

-- 轨迹（供论文评测集导出，schema 对齐 OpenDiscoveryTrace）
CREATE TABLE trace (
  id INTEGER PRIMARY KEY,
  run_id TEXT, step_no INTEGER, ts REAL,
  phase TEXT,                          -- 数据准备|配准|干涉|解缠|时序反演|成图|质检
  thought TEXT, action TEXT, observation TEXT,
  error TEXT, revision_trigger TEXT, confidence REAL
);
```

写入原则（借鉴 agentic-swmm 唯一做对的原子写，`run_progress.py:108-115`）：

- 每个阶段推进一个事务，`COMMIT` 后才算数。
- 需要写文件的地方走 `tmp + os.replace`（POSIX/Windows 都原子）。
- UNIQUE 索引保幂等插入（`agentic-swmm/.../memory/session_db.py:280-282` 的做法）。

### 6.2 Provenance 导出

字段对齐 `agentic-swmm` 的 `experiment_provenance.json`（`DESIGN.md:618-636`），加 InSAR 特化：

```json
{
  "schema_version": "1.0",
  "run_id": "01J8X7QD3-yushu-2023",
  "generated_at_utc": "...",
  "environment": {
    "python": "3.11.9", "platform": "WSL2 Ubuntu 24.04",
    "conda_env_sha": "4f21…9ac3",
    "tools": {"isce2": "2.6.5", "mintpy": "1.6.4", "snaphu": "2.0.7",
              "pystamps": "0.3.4", "gdal": "3.8.4", "pyaps3": "0.3.6"}
  },
  "repo": {"git_head": "08471c2", "git_dirty": false},
  "agent": {"system_prompt_sha256": "...", "tools_schema_sha256": "...",
            "capabilities_sha256": "...", "model": "..."},
  "steps": {"6": {"capability": "snaphu_mcf", "method": "...",
                  "params": {...}, "task_hash": "...", "args_hash": "...",
                  "eval_hash": "...", "upstream": ["..."],
                  "cmd": ["snaphu", "-f", "snaphu.conf"],
                  "deps": [{"art_id": "filt_ifg", "fp": "stat:sha256:9c41…"}],
                  "run_ok": true, "qa": [...]}},
  "artifacts": {"unw": {"path": "...", "fp": "...", "policy": "stat",
                        "produced_by": 6, "used_for": [7]}},
  "metrics": {"mean_velocity": {"value": -23.4, "unit": "mm/yr",
                                "source_artifact": "velocity.h5",
                                "source_field": "velocity",
                                "reparsed_ok": true}},
  "qa": {"status": "pass", "checks": [...]},
  "evidence_level": "validated",
  "warnings": [], "human_decisions": [], "interventions": []
}
```

`cmd` 与 `deps` 是导出时从 SQLite 物化的（absorb-M，`reference/AGENT_PRODUCTS_LEARNING.md` §4.3）：`cmd` 取该步**最后成功 attempt** 的 `commands.argv`；`deps` 按 edges 反查上游产物得 `[{art_id, fp}]`。库内靠 edges/commands 隐式重建是对的，导出时物化使 **step 条目字段 ⊇ dvc.lock 条目字段**（cmd + deps + params + outs，`dvc/dvc/stage/serialize.py:143-192`），审稿人可并排核对。连带纪律：`steps.params` 必须存 normalize（§5.2）后的 canonical JSON——否则事后键级 diff 与当时参与 args_hash 的内容不一致。

改进点相对 agentic-swmm：`run_id` 用 UUID+单调时间戳（它纯靠目录名，跨机合并会碰撞，`DESIGN.md:639`）；schema 版本号单一来源（它裂成三处，`DESIGN.md:666-668` —— 但注意 `COMPARISON_LEARNING.md:147` 已指出上游修好了，论文里不要再攻击这条）。

同时导出 RO-Crate（`DESIGN.md §16.5`）与**等价裸命令脚本** `run.sh`（第 3 条 baseline 要求）。

### 6.3 六级证据阶梯的可执行判定

`DESIGN.md:301-308` 只有定义没有判定规则。补上：

| 级别 | 机器可判定条件 |
|---|---|
| `runnable` | 所有步骤 `stage == VERIFIED` |
| `checked` | 上 + 所有 `run_ok == 1` 且 QA 指标已解析（相干性/解缠覆盖/参考点） |
| `audited` | 上 + provenance JSON 完整（每个 artifact 有 fp，每个 metric 有 `reparsed_ok`） |
| `calibrated` | 上 + 存在 GNSS/水准比对记录且 RMSE 在阈值内 |
| `validated` | 上 + 双链交叉验证（PS/SBAS）相关系数 ≥ 契约阈值 |
| `publishable` | 上 + 证据边界表已填 + 跨环境复现记录存在 |

**取最差原则**：借鉴 `InSAR-Pro/insar-pro/backend/app/engines/result_metadata.py:148` 的 `overall_tier` 取最差等级 —— 有一步降级则整体降级。那份代码已实现了 real/fallback/simulated 三级传播，是这套阶梯的可运行前身，可直接扩展。

---

## 7. 界面设计

界面已实现于 `prototype/`（12 模块 3954 行），本节是**下一轮要补的部分**与右侧面板体系的完整设计。

### 7.1 三区布局与信息分工

```
┌──────┬────────┬──────────────────────────┬──────────────────────┐
│ rail │ 会话   │ 轨迹流（主战场）          │ Dock（右侧多面板）    │
│ 52px │ 240px  │ flex, max-width 780px    │ 300–620px 可拖拽      │
├──────┼────────┼──────────────────────────┼──────────────────────┤
│ 对话 │ 会话1  │ 用户消息                  │ [流水线][影像][文件]  │
│ 会话 │ 会话2  │ ▸ 思考（折叠）            │ [审计][报告][轨迹]    │
│ ──── │ 会话3  │ ▸ 计划 3/11               │ [浏览器][终端][环境]  │
│ 主题 │ ────   │ ▾ ⚡tool_call exit 0      │                      │
│ 面板 │专家/向导│   └ 终端输出（内联滚动）   │  ← 当前步详情         │
│      │        │ ▸ ⚡运行中 04:12 ▓▓▓░░     │  ← 产物预览           │
│      │        │ ⚠ 审批卡（内联）           │  ← provenance 树      │
│      │        │ 结果卡 + 图件              │  ← 证据阶梯           │
└──────┴────────┴──────────────────────────┴──────────────────────┘
```

**分工原则**：轨迹流回答「发生了什么」（时间序），Dock 回答「现在是什么状态」（空间快照）。同一份数据两种视角，不重复不冲突。

### 7.2 右侧 Dock：九个面板

已实现六个，需补三个。

| # | 面板 | 状态 | 内容 | 独特价值 |
|---|---|---|---|---|
| 1 | **流水线** | ✅ | 11 步 + 状态 + 选中步详情（方法下拉/参数表单/指纹/依赖/等价命令） | 参数改动的入口 |
| 2 | **影像** | ✅ | 图件画廊 + 小地图 + 点位时序 | 结果空间浏览 |
| 3 | **文件** | ✅ | 产物树（从步骤输出派生）+ 预览 | 指纹可见 |
| 4 | **审计** | ✅ | 证据阶梯 + provenance 树 + 指标契约 | 竞品全无 |
| 5 | **报告** | ✅ | 方法草稿 + 证据边界 + 导出（ZIP/md/sh） | 论文直出 |
| 6 | **浏览器** | ✅ | ASF/CDS/文献检索过程可见 | 数据获取透明 |
| 7 | **轨迹** | ⬜ 待补 | Lab Notebook：thought/action/observation/error/revision | 论文评测集导出 |
| 8 | **终端** | ⬜ 待补 | 全量日志检索（当前只在 tool_call 内看片段） | 排查长任务 |
| 9 | **环境** | ⬜ 待补 | 引擎版本 + WSL 资源 + 磁盘水位 + 候选集收窄原因 | 可行性可解释 |

#### 面板 7：轨迹（Lab Notebook）

schema 对齐 OpenDiscoveryTrace（`src/harness/agent_harness.py:367-378`），字段完整列表：`step_id / timestamp / phase / thought / action{type,tool,input,output} / observation / error{occurred,type,message} / revision_trigger / confidence / wall_time`。

InSAR 的 phase 取值（类比它的 6 个科学阶段，`analyze_trajectories.py:282`）：
```
数据获取 → 辅助数据 → 配准 → 干涉 → 滤波 → 解缠 → 时序反演 → 误差校正 → 形变模型 → 成图 → 质检
```

**必须抄它的长字段硬截断**（`raw_response[:3000]`、`observation[:2000]`、`input[:1000]`）—— 否则 InSAR 日志会撑爆 SQLite。

`revision_trigger` 字段（`agent_harness.py:443-445`）对我们特别有用：它记录「agent 自我修正的触发点」，正好对应我们的失效重跑。配 `recovery_successful` 可量化「断点续跑是否真的救回来了」——这是论文可用的指标。

导出 JSON / Markdown，直接作为 §13 提到的 InSAR 任务评测集素材。

#### 面板 8：终端

当前 tool_call 条目内只有 300px 滚动区。长任务需要独立面板：

- 按步骤分 tab，全量日志（从 `log_path` 读，非内存）
- 正则过滤 + 高亮（ERROR/WARNING/进度行）
- 跳到错误行按钮
- 复制为 issue 模板（含环境信息）

#### 面板 9：环境（可行性可解释）

这个面板解决一个真实问题：**用户看到候选集里有项被禁用，需要知道为什么。**

```
引擎          isce2 2.6.5 ✓   mintpy 1.6.4 ✓   snaphu 2.0.7 ✓
              pystamps 0.3.4 ✓  pyaps3 0.3.6 ✓  gacos ✗ 缺凭据
资源          WSL2 20 核 / 40 GB · SSD 剩余 312 GB / 1 TB
候选收窄      3D_FULL      ✗ 工具链缺失
              tropo_gacos  ✗ 缺 GACOS 凭据
              isce2_stripmap ✗ 数据为 S1 IW 非条带
磁盘预警      预计中间产物 ~180 GB，当前可用 312 GB ✓
```

磁盘预警是 §1.5 的「磁盘满」失败类的前置拦截 —— 竞品无一实现。

### 7.3 Dock 的交互契约

三条已实现并需保持：

1. **每个面板独立保留滚动位置**（`dock.js` 的 `scrollMemo`），切换不跳动。
2. **切换只是 `hidden` 开关**，不全量重建 DOM。
3. **宽度可拖拽且持久化**（`localStorage`），键盘 ←→ 也能调。

需补的两条：

4. **面板间联动要可追溯**：点结果卡的产物行 → 跳文件面板并选中；点 provenance 树节点 → 跳流水线面板并选中该步。当前只做了前者。
5. **窄屏浮层一次只显示一个**（已实现），但需补「Dock 内容可弹出为独立窗口」以支持双屏演示（教学场景）。

### 7.4 轨迹流：需补的四种条目

已实现：user / agent / thinking / tool_call / plan / ask / candidates / result / report / note。

需补：

| 条目 | 用途 | 形状 |
|---|---|---|
| **`reattach`** | 服务重启后接回运行中任务 | 蓝色横幅「检测到 MintPy 仍在运行（pid 48213，已运行 1h23m），已接回日志流」 |
| **`intervention`** | 运行中人工干预的留痕 | 「你在第 7 步运行中将第 6 步方法改为 snaphu_smooth → 已排入队列，当前步完成后生效」 |
| **`degrade`** | 降级发生时显式告知 | 橙色「ERA5 服务不可用，已降级为 tropo_height_corr。证据级别从 validated 降为 checked」 |
| **`gate_stop`** | 质量门拦停 | 红色「解缠覆盖率 0.62 < 0.70，已停链。这不是错误，是质量门拦截 —— 建议改用 snaphu_smooth 或放宽阈值」 |

`degrade` 与 `gate_stop` 是 InSAR 特有的，两个竞品都没有对应的 UI 表达（InSAR_Agent 的 ERA5 降级只打一行 log）。

### 7.5 预算与资源的常态可见

前面几节新增的机制（磁盘预算 §4.10、资源池 §4.11、阈值 PENDING §4.13）都需要 UI 表达。
但**不能靠用户主动点开面板才看到** —— 磁盘要满了必须撞到眼前。

**顶栏预算条**（常驻，只在接近阈值时才显眼）：

```
正常   [ InSAR-Agent / 玉树冻土 ]              ● 运行中 · 第 6/11 步   [取消]
紧张   [ InSAR-Agent / 玉树冻土 ]  磁盘 48G↓  ● 运行中 · 第 6/11 步   [取消]
告警   [ InSAR-Agent / 玉树冻土 ]  ⚠ 磁盘 12G · 预计需 34G           [清理] [取消]
```

三级视觉：`> 20 G` 不显示；`8–20 G` 灰字提示；`< 8 G` 橙色 + 清理入口。
这条规则对应 §4.10 的三级闸门，UI 与后端用同一组常量，不各写一套。

**预估必须带不确定度。** 原型里写「预估 93 分钟」是假精确 —— 那个数是各步 mock 秒数
相加。真实预估应该是区间且标注依据：

```
重跑 6 步 · 约 1.5–3 小时（基于本机历史 3 次运行）
重跑 6 步 · 时长未知（首次运行此配置）        ← 没有历史时诚实说不知道
```

**「时长未知」比编一个数字好。** 这是 §4.13 纪律在 UI 层的延伸：没有依据就不给数。
历史样本 < 3 次时一律显示「未知」，并说明为什么。

**作业完成 / 失败 / 等审批时拉起外部通知命令**（absorb-P，codex notify，
`codex/codex-rs/core/src/config/mod.rs:732-746`）：可配置 argv，事件以 JSON 作为最后
一个参数传入，实现只是 spawn 一个进程。InSAR 单步小时级，没人盯着屏幕等——通知在
这里是刚需不是锦上添花；载荷含 run/step、终态（done/failed/waiting_approval）与耗时，
通知渠道（桌面弹窗 / IM 推送脚本）由用户自配。

**预算余量作为状态事件常态推流**（OpenHands O5：成本随 stats 事件流出、常态可见）：
磁盘 / 内存 / LLM token 余量不等告警才出现，而是随 SSE 状态事件持续下发，顶栏预算条
只是这条流的渲染——与 §4.10 三级闸门共用同一组常量与同一条数据流。

### 7.6 误操作防护

InSAR 的误操作代价是小时级，需要比普通应用更强的防护，但**不能靠弹窗轰炸**。

| 操作 | 防护 | 理由 |
|---|---|---|
| 重跑（有覆写） | 内联审批卡 + 列出覆写清单 | 已实现 |
| 重跑（纯新建） | 内联审批卡但不标红 | 已实现，文案区分 |
| 改科学参数 | 即时显示影响范围，**不拦** | 改参数本身无损，拦了反而妨碍探索 |
| 改资源参数 | 无提示，直接生效 | §5.4 不影响结果 |
| 删除产物（GC） | **二次确认 + 输入 run_id** | 不可逆且影响下游可跳过性 |
| 取消运行中任务 | 一次确认，说明「已完成步骤保留」 | 降低取消的心理成本 |
| 切换会话（运行中） | 提示「任务继续在后台运行」 | 避免误以为切换会中断 |
| 审批门无回调 / 非交互环境 | **fail-closed：默认拒绝** | gemini 三值 policy 的 ASK_USER 非交互自动降级 DENY + cline 无审批回调即拒（absorb-F）——小时级代价下的正确保守设定 |
| 审批等人 | 挂 `WAITING_APPROVAL` **无限期等待**；超时只用于提醒，绝不自动拒绝 | 小时级任务的审批可能等人几小时。反例：cline 文件轮询 IPC 5 分钟超时自动拒绝 |
| 同参数族重复弹卡 | 审批缓存按**参数指纹族**：同指纹族批一次，全会话生效 | codex 审批缓存 key（规范化命令+cwd+权限）的改造（C7）；一个 stack 二十个同参数干涉对逐个弹卡会杀死可用性 |
| 注定失败的动作 | **拦截先于审批**：可行性校验不过的动作直接拒绝回灌，不弹审批卡 | cline L4：「用户永不会被请求审批注定失败的命令」 |

**审批卡三分型**（gemini G3「确认卡内容按工具类型分型」的改造，absorb-F）：

| 卡型 | 内容 | 典型触发 |
|---|---|---|
| 参数卡 | 新旧参数键级 diff + 影响范围 | SET_PARAMS / SET_METHOD |
| 作业卡 | 等价命令 + 预计时长（区间+依据，§7.5） | 提交长任务 |
| 覆写卡 | 将变 STALE 的下游清单（步骤/产物/预估重跑时长） | 覆写产物或触发级联重跑 |

**输入 run_id 确认只用于 GC**，其它操作不用 —— 过度使用会让用户形成肌肉记忆盲点。

**新增：撤销窗口。** 改参数后 10 秒内显示「撤销」按钮（不是弹窗，是 note 条目内的链接）。
比二次确认更好：不打断流程，但给了后悔的机会。参数变更本来就只改 SQLite 一行，
撤销成本极低。

### 7.7 Dock 的三个新面板细化

§7.2 列了待补的面板 7/8/9，这里定内容。

**面板 9「环境」是优先级最高的**，因为它承载 §0.5 的事实基线与 §4.13 的阈值状态：

```
┌ 运行环境 ─────────────────────────────────────────┐
│ WSL2        ✗ 未安装发行版          [安装指引]     │  ← 当前真实状态
│ 工作区      —                                      │
│ 引擎        全部未探测                             │
├ 磁盘 ─────────────────────────────────────────────┤
│ E: 工作区   483 G 可用 / 931 G          ████░░░░░  │
│ C: 系统     36 G 可用 / 199 G  ⚠ 勿装 WSL 于此    │
│ F: 归档     187 G 可用 / 932 G                     │
├ 质量门阈值 ───────────────────────────────────────┤
│ esd_coherence      0.85   A 上游默认               │
│ corr_threshold     0.85   ⚠ PENDING 未标定         │  ← §4.13
│ min_coherence      0.25   ⚠ PENDING 未标定         │
│ 当前证据上限：audited（3 项阈值待标定）             │
└───────────────────────────────────────────────────┘
```

这个面板同时是**装机向导的入口** —— WSL 未就绪时它是首屏该看的东西，
而不是让用户点了「运行」才发现跑不起来。

**面板 8「终端」新增一个能力：`cmd.sh` 直接可见。** §4.7 的副产品让每步都有一份
真实执行的脚本，终端面板应该能：查看 `cmd.sh` 原文、复制、以及「在 WSL 中手动执行此步」
—— 后者对调试引擎问题极有价值，也是「等价裸命令」承诺的现场证明。

**面板 7「轨迹」的导出必须包含失败轨迹。** OpenDiscoveryTrace 的
`recovery_attempted` / `recovery_successful` 字段（`agent_harness.py:483-491`）
正是评测「断点续跑是否真的救回来了」的指标。成功轨迹人人都有，
**失败并恢复的轨迹才是我们的论文素材**。

### 7.8 长任务的可观测状态

通则（已实现）：不用全屏 Overlay；tool_call 条目保持 `is-run` 内联进度 + 计时；
顶栏徽章显示「运行中 · 第 6/11 步」；**输入框保持可用**，用户可随时问「到哪了」。
待补：关闭浏览器再打开时从 SQLite 恢复轨迹流并接回日志（§4.7 的 `log_offset`）。

原型现在只有 running/done/failed。新增 §4.7 的 `orphaned` 后需要四态区分，
因为它们的**用户动作完全不同**：

| 状态 | UI 表达 | 用户能做什么 |
|---|---|---|
| `running` | 蓝色脉冲 + 计时 + 进度 | 取消 / 继续对话 |
| `interrupted` | 橙色「已取消 · 可续跑」 | 从断点继续 |
| `orphaned` | 橙色「WSL 已停止 · 计算未完成」+ 说明 | 重启 WSL 后续跑（**不是重跑**） |
| `failed` | 红色 + 失败分类 + 建议动作 | 按分类给的动作（换账号/降级/改参数/看日志） |

`orphaned` 与 `failed` 的区分很重要：前者是环境事件，产物可能是完整的（只是没写 `job.rc`）；
后者是计算问题。UI 混为一谈会让用户白跑一遍。

失败态的 UI 必须**直接给出该分类对应的动作按钮**，而不是只报错：

```
✗ 第 8 步失败 · service_down
  ERA5 服务不可用（HTTP 503，重试 3 次）

  可选处置：
  [降级为 tropo_height_corr]  ← 证据级别将从 validated 降为 checked
  [稍后重试]                   ← 保留断点
  [查看完整日志]
```

把「降级的代价」写在按钮旁边，这是 §4.12 降级矩阵的 UI 落地 ——
InSAR_Agent 静默降级（`agent.py:1063`）的做法在教学与论文场景都不可接受。

**reattach 纪律：接管 running 任务前三道校验**（absorb-G + cline L8 的恢复校验纪律，
`reference/AGENT_PRODUCTS_LEARNING.md` §2.5）：

1. **参数指纹一致性**：作业目录里随 `cmd.sh` 一起落盘的 args_hash 必须与 SQLite steps 记录一致；失配判 `orphaned`，**绝不盲目接管**——失配说明作业目录被外部改动或记录已过期（同 cline 压缩边车「源前缀哈希失配即放弃重算」的纪律，`session-compaction.ts:161-190`）。
2. **wrapper 协议版本**：`job.*` 文件契约带协议版本号，与当前代码期望不一致时不解析、判 `orphaned`（cline hub 构建指纹校验的移植，`sdk/ARCHITECTURE.md:196-202`）。
3. **断线重连协议**（前端断开重连 / 浏览器重开，OpenHands O8 几乎照搬）：SQLite 拉历史 → 按事件自增 id（比时间戳锚点稳）增量推送 → 前端按 id 去重 → 重放事件不触发副作用；掉线期间的用户消息进 pending_actions 的 USER_MESSAGE（§4.5），不丢失。

## 8. 功能清单

按「是否影响论文 novelty」分级。

### 8.1 P0：novelty 直接依赖

| 功能 | 对应 novelty | 验收标准 |
|---|---|---|
| 三段式指纹 + 规范化哈希 | 主 novelty | 键序变化不失效；`0.3` 与 `0.30000000000000004` 同哈希 |
| 参数三分类（科学/资源/呈现） | 主 novelty 的差异化 | 改 `threads` 不标脏；改 `min_coherence` 标脏全下游 |
| 依赖图级联标脏 + 原因分类 | 主 novelty | 改第 3 步 → 3/4/5 标脏，1/2 不动，附「为什么」 |
| 五阶段执行器 + 幂等守卫 | 辅：断点续跑 | 杀进程重启 → 从中断阶段继续，不重跑已完成阶段 |
| RUNNING 阶段 reattach | 辅：断点续跑 | 服务重启但 MintPy 仍活 → 接回日志不重跑 |
| 完整 provenance + artifact 指纹 | 辅 | 任一产物可回溯到命令/参数/版本/git |
| ISCE2→PyStamps 桥 | 主 novelty | 同源数据跑通 PS 链 |
| 双链交叉验证质量门 | 主 novelty | PS/SBAS 相关系数进 QA，低于阈值停链 |
| try/finally 审计 | 辅 | 失败/取消同样产出 provenance |
| 等价裸命令导出 | baseline 第 3 条 | `run.sh` 脱离 agent 可复现 |

### 8.2 P1：可用性必需

超时按 capability 声明（双超时）· 产物候选发现 · `run_ok` 双判定 · 干预队列 · 失败分类闭集 + 降级 · 磁盘/内存前置检查 · 日志分层落盘 · 三档文件指纹 · 会话持久化与恢复 · 轨迹导出。

### 8.3 P2：教学与论文增强

轨迹面板 · 终端面板 · 环境面板 · Dock 弹窗（双屏演示）· 失效级联动画回放（课堂演示「改参数如何失效」）· 跨环境复现对比工具 · InSAR 任务评测集构造。

### 8.4 明确不做

| 不做 | 原因 |
|---|---|
| LLM 生成命令行/代码 | `DESIGN.md:348` 硬约束 |
| LLM 开放式自由规划（任意命令/任意工具序列） | 原「LLM 自由多步规划」，2026-08-14 波次精确化：受约束的自主循环（动作白名单闭集 + 周期上限 + 熔断 + 审批门内置，见 `docs/AGENT-LOOP.md`）是支持的；BFCL v4 数据（本地 14B 端到端仅 41%，`DESIGN.md:353-360`）保留为「本地小模型默认关闭自主循环」的开关条件 |
| LLM 检索日志 | context rot（`DESIGN.md:384-386`） |
| 引入 Snakemake/Prefect/Dagster/LangGraph/redun | `DESIGN.md:406-425`（LangGraph resume 从节点开头重跑是决定性的） |
| 未鉴权的文件读接口 | 竞品致命安全问题（`DESIGN.md:548-551`），我们主打数据不出域 |
| 明文密码落盘 | 竞品 `submit_insar.py:441` + `web/core.py:87-89` 的错误 |
| 全量内容哈希 | §1.3 |

---

## 9. 实施顺序

依赖关系决定顺序，不是重要性。

### Phase 0：地基（3–4 天）
```
core/schema.sql          SQLite DDL
core/normalize.py        规范化哈希（先写单测：键序/浮点/嵌套三个坑）
core/fingerprint.py      三段式
core/filehash.py         三档策略
core/store.py            状态机 + 幂等守卫
registry/capabilities.py 11 步声明（含超时/参数分类/产物候选/run_ok）
```
验收：`pytest` 覆盖三个哈希坑 + 幂等重放（同一步执行两次，第二次全跳过）。

### Phase 1：执行层（4–5 天）
```
runtime/probe.py         环境探测（产出候选收窄依据）
runtime/render.py        Jinja2 模板
runtime/stream.py        日志流 + 双超时 + 协作式取消
runtime/executor.py      五阶段
runtime/discover.py      产物候选匹配
audit/runok.py           双判定
```
验收：用 11 对 HyP3 真实数据跑通 MintPy 链（复用 `InSAR-Pro/.../engines/mintpy.py` 的 5 处 `--dostep`）；中途 `kill -9` 后重启能续；服务重启但子进程存活时能 reattach。

**这个阶段同时填掉 §6 发现的资源特征空白** —— 拿真实耗时/内存/文件数替换原型里的占位符（`1035 pairs`、`~24 min · 8 GB` 全是 mock）。

### Phase 2：失效传播（2–3 天）
```
core/stale.py            级联 + 原因分类
core/actions.py          干预队列
planner/graph.py feasibility.py plan.py
```
验收：改第 3 步参数 → 3/4/5 标脏且附原因；改 `threads` 不标脏；运行中投 SET_METHOD 动作能在当前步结束后生效。

### Phase 3：审计（2–3 天）
```
audit/contract.yaml verify.py ladder.py
core/ledger.py
report/script.py         裸命令导出
```
验收：provenance JSON 完整；`run.sh` 可脱离 agent 复现；失败时审计同样触发（try/finally 守护测试）。

### Phase 4：Brain（2–3 天）
```
brain/provider.py intent.py select.py triage.py narrate.py
loop/driver.py events.py budget.py
```
验收：拔掉 `brain/` 整层，手动模式仍跑通（这是 `DESIGN.md:233` 的架构约束，要有测试守护）。

### Phase 5：API + 前端接真（3–4 天）
```
api/app.py               FastAPI + 双 SSE
prototype/js/backend.mock.js → backend.sse.js
```
只改一个文件（事件契约已按真实 SSE 形状设计）。补面板 7/8/9 与四种新条目。

### Phase 6：桥（5–7 天，主 novelty）
```
engines/bridges/isce2_to_pystamps.py
```
风险最高，三个已知难点（`DESIGN.md:123-132`）：par 几何字段自洽（最大风险，错了 bperp 和高程误差全崩）、TCN 基线反算、全程 big-endian。

放最后不是因为不重要，而是它依赖前面的执行层与审计层来验证正确性。

---

## 10. 立即要做的三件事

1. **修文档矛盾**（半天）。`COMPARISON_LEARNING.md:189` 的 `update-DESIGN` 行动项没执行 —— `DESIGN.md:666-672` 那两条对 agentic-swmm 的批评已被上游修复，进了论文 related work 会被审稿人直接驳倒。同时把「12 景」改成「11 个干涉对 / 7 个日期」（实测），`§16/§15` 顺序调正。**（2026-08-12 已完成：DESIGN.md §10.3 加审计注、§11 改实测数、§15/§16 已对调。）**

2. **标注原型里的占位数字**（半天）。`prototype/js/state.js:87` 的 `pairs: 1035`、`:104` 的 `~24 min · 8 GB`、`backend.mock.js:146` 的 `2.1 GB` 全是 mock。要么加「示意值」标注，要么等 Phase 1 用真数据替换。演示给别人看时这些数字会被当真。

3. **建 `insar_agent/` 骨架并跑通 Phase 0 的哈希单测**（1 天）。`pyproject.toml` 声明 `where=["src"]` 但 `src/` 不存在 —— 先让 `pip install -e .` 能过。

---

## 附：借鉴来源索引

| 我们的设计 | 来源 |
|---|---|
| 三段式哈希 | `redun/redun/hashing.py:94-114` |
| version 逃逸阀 | `redun/redun/task.py:454` |
| 资源参数不哈希 | `redun/redun/task.py:441-442` |
| 文件伪哈希 + `-1` 编码 | `redun/redun/file.py:463-475` |
| 三档文件策略 | `redun/redun/file.py:463/1710-1716/1784-1788` |
| 失效原因分类 | `redun/redun/backends/db/__init__.py:2507` |
| call graph 三层解耦 | `redun/redun/backends/db/__init__.py:662-1006` |
| 参数级上游追溯 | `redun/redun/backends/db/__init__.py:550-565` |
| 规范化哈希（键序/浮点/嵌套） | `aiida-core/src/aiida/common/hashing.py:159-176, 197-202, 123` |
| Merkle 输入级联 | `aiida-core/src/aiida/orm/nodes/process/process.py:88-102` |
| 五阶段 + 幂等守卫 | `aiida-core/src/aiida/engine/processes/calcjobs/tasks.py:76-140, 525-548` |
| 外部句柄持久化 | `aiida-core/.../calculation/calcjob.py:66` |
| 内存 future 不跨重启 | `aiida-core/.../calcjobs/tasks.py:520-523` |
| attributes/extras 可变性分离 | `aiida-core/src/aiida/storage/psql_dos/models/node.py:31-49` |
| 引用式序列化（只存 UUID） | `aiida-core/src/aiida/orm/utils/serialize.py:77-87` |
| 递归标脏 | `aiida-workgraph/.../engine/task_state.py:170-203` |
| 动作集 | `aiida-workgraph/.../engine/task_actions.py:36-50` |
| 双重试计数器 | `agentic-swmm/.../agent/planner.py:39-47` |
| `unresolved_failure` 标志 | `agentic-swmm/.../agent/planner.py:451, 592, 623` |
| 字段白名单给模型 | `agentic-swmm/.../agent/tool_registry.py:193-236` |
| 原子写 tmp+replace | `agentic-swmm/.../run_progress.py:108-115` |
| 只读步骤降噪 | `agentic-swmm/.../agent/planner.py:338-339` |
| 幂等 UNIQUE 索引 | `agentic-swmm/.../memory/session_db.py:280-282` |
| 缓存锚定反幻觉 | `InSAR_Agent/src/insar_agent/agent.py:481-499, 514-556` |
| task_id 唯一句柄 | `InSAR_Agent` 全局 |
| 事件工厂 data/ui 分离 | `InSAR_Agent/src/insar_agent/events.py:19-23` |
| MintPy `--dostep` 拆步 | `InSAR-Pro/insar-pro/backend/app/engines/mintpy.py:290,556,750,914,1047` |
| 三级可信度取最差 | `InSAR-Pro/.../engines/result_metadata.py:46-47, 148` |
| Schema 参数契约 | `InSAR-Pro/.../algorithms/schemas.py:82-120` |
| 轨迹 schema | `OpenDiscoveryTrace/src/harness/agent_harness.py:367-378` |
| 长字段硬截断 | `OpenDiscoveryTrace/.../agent_harness.py:377` |
| 审批决策八值枚举（Denied 带理由 / TimedOut 分立） | `codex/codex-rs/protocol/src/protocol.rs:3849-3884` |
| 拒绝理由作为工具结果回给模型 | `codex/codex-rs/core/src/tools/approvals.rs:332-334` |
| 日志头尾截断 + `omitted N bytes` 标记 | `codex/codex-rs/core/src/unified_exec/head_tail_buffer.rs:31-42,114-132` |
| 落盘白名单写成显式穷举函数 | `codex/codex-rs/rollout/src/policy.rs:9-21,87-184` |
| 工具调用七态调度（每态独立类型） | `gemini-cli/packages/core/src/scheduler/types.ts:26-34,81-196` |
| checkpoint 三元组 + 恢复时重提决策点 | `gemini-cli/packages/core/src/utils/checkpointUtils.ts:98-104`、`core/src/commands/restore.ts:11-58` |
| 三值 policy（默认 ASK_USER，非交互降级 DENY） | `gemini-cli/packages/core/src/policy/types.ts:10-14,114-193,296-306` |
| 事件信封（ULID id/timestamp/source 封闭联合体） | `OpenHands/src/types/agent-server/core/openhands-event.ts:25-46` |
| 断线重连（拉历史+锚点增量+去重+重放免副作用） | `OpenHands/src/contexts/conversation-websocket-context.tsx:253-257,904-913,518-523` |
| 熔断三段式（同签名 3 次软警告 / 5 次硬熔断） | `cline/sdk/packages/core/src/runtime/safety/loop-detection.ts:66-87,113-116,144-156` |
| 插话双投递队列（queue/steer + 可编辑撤回） | `cline/sdk/packages/core/src/runtime/turn-queue/pending-prompt-service.ts:14,168-280` |
| 压缩边车 + 源前缀哈希失配即弃 | `cline/sdk/packages/core/src/session/models/session-compaction.ts:77-98,161-190` |
| 异常即控制流（异常携带待追加消息） | `mini-swe-agent/src/minisweagent/exceptions.py:1-26`、`agents/default.py:88-124` |
| 五钩子面 + steering/followUp 双队列 | `pi/packages/agent/src/agent-loop.ts:226-292,619-647,182-190` |
| can_use_tool 回调形状（allow+updated_input / deny+message） | `claude-agent-sdk-python/src/claude_agent_sdk/types.py:236-259,201-234,1727-1787` |
| halt_before/halt_after 双语义（before 优先） | `burr/core/application.py:1213-1221,1276` |
| sequence_id 先递增再执行（防 replay 卡死） | `burr/core/application.py:929-932` |
| masked 剪枝（判脏下游跳过指纹计算） | `snakemake/src/snakemake/dag.py:1618-1628` |
| 记录格式版本门控（RECORD_FORMAT_VERSION） | `snakemake/src/snakemake/persistence/__init__.py:32,660-696` |
| run-cache key/value 分离（≈ eval_hash / 产物指纹） | `dvc/dvc/stage/cache.py:28-33,59-63,165` |
| early cutoff（上游产物指纹未变 → 下游免重算） | dvc 隐式传播链；显式覆盖开关 `dvc/dvc/repo/reproduce.py:197-198` |

自「审批决策八值枚举」起的 21 行为 2026-08-12 追加，来自 11 仓库产品层 / harness 层 / 定向工具对标；吸收决议 absorb-E~P 与完整证据链见 `reference/AGENT_PRODUCTS_LEARNING.md` §5。

**反例（明确不抄）**：InSAR_Agent 的关键词表强制 `tool_choice`（`agent.py:1430-1441`）、阻塞读循环内检查取消（`tools/mintpy.py:219`）、明文密码落盘（`submit_insar.py:441`）、前端 `setTimeout` 回灌唤起 LLM（`web/static/index.html:619-620`）、文件存在即跳过当幂等（7 处）。
