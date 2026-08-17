# insar-agent

[![ci](https://github.com/liujiangmu123/InSAR-Agent/actions/workflows/ci.yml/badge.svg)](https://github.com/liujiangmu123/InSAR-Agent/actions/workflows/ci.yml)

可复现 InSAR 科学工作流 Agent:**参数级失效传播 + 步级断点续跑 + 完整 provenance**。

产品界面是 **pi Desktop**(左会话 / 中对话 / 右 InSAR 工作台只读)。中栏对话是唯一操作面,31 个 `insar_*` 工具接到 Python FastAPI 内核;右栏只展示流水线状态、数据集和图件,不提供执行按钮。旧网页 `prototype/` 与 Tauri 壳已删除。

## 文档索引

| 文档 | 内容 |
|---|---|
| `docs/DESIGN.md` | 产品定稿:novelty、11 步 × 方法矩阵、桥梁地图 |
| `docs/AGENT-DESIGN.md` | 架构定稿:硬约束、分层、五阶段执行器、指纹、SQLite |
| `docs/AGENT-LOOP.md` | 自主循环:事件契约、状态机、终止闭集、红线 |
| `docs/LOOP-CONTRACT.md` | 自主循环开发契约(动作闭集) |
| `docs/VALIDATION-isce2-wsl.md` | ISCE2 WSL 全链实测(ALOS Baja 同震对) |
| `docs/WSL-SETUP.md` | WSL 引擎环境 |
| `docs/PI-REAL-SESSION.md` | 真实数据走查(Ridgecrest audited 读回;重型新 run 须批准) |
| `pi-insar/README.md` | 对话外壳:Windows 入口、工具面、与内核接线 |
| `pi-insar/docs/plan/00-README.md` | 全流程交付计划索引 |
| `desktop/README.md` | pi Desktop overlay 同步 |

## 怎么跑

终端 A 起内核,终端 B 起桌面:

```powershell
pwsh scripts/insar-backend-real.ps1
pwsh scripts/insar-pi-desktop.ps1
```

仅 TUI(不要桌面壳)时,终端 B 改用 `pwsh scripts/insar-pi.ps1`。真实走查见 [`docs/PI-REAL-SESSION.md`](docs/PI-REAL-SESSION.md)。分析 run(掩膜 / 升降轨分解 / 预测 / 反演桥,步 20–28)与场景包见 [`pi-insar/README.md`](pi-insar/README.md)。不要未经批准跑 `scripts/real_ridgecrest.py`。

内核只提供 API(`http://127.0.0.1:8873`)。浏览器打开根路径得到 JSON 指引,不是产品界面。LLM 密钥只存本机 `workspace/llm.json`(pi Desktop 经 `insar-llm` 供应商读取);不配则规划走规则路径。

## 特性(全部有测试守护)

- **三段式指纹 + 参数三分类**:改 `threads` 不标脏;改 `min_coherence` 级联标脏全下游并解释原因
  (method_changed / param_changed / upstream_changed / tool_upgraded / artifact_missing);
  改 `dpi` 只标脏出图步。
- **五阶段执行器**(PREPARED→LAUNCHED→RUNNING→COLLECTED→VERIFIED):
  幂等守卫 + 意图/结算两段提交;服务重启后 **reattach 存活作业不重跑**,
  崩溃在不确定窗口时**认领既有作业不双启动**;孤儿(环境死亡)与计算失败严格区分。
- **作业目录文件契约**:进程控制退化为文件操作(job.pid/job.log/job.rc/job.cancel),
  本地(Windows)与 WSL 双后端同一接口;`cmd.sh` 即等价裸命令,`run.sh` 一键复现。
- **run_ok 双判定 + 质量门**:退出码 0 ≠ 成功;阈值台账带来源纪律
  (`upstream_default|literature|local_calibration`),**PENDING 阈值只警告不拦停**,
  证据阶梯自动封顶(runnable→…→publishable,取最差)。
- **干预队列三语义**:steer(当前步后生效)/ follow_up(run 结束后)/ next_run(下次规划);
  KILL 即时响应;运行中投递消息必须显式声明语义(HTTP 400 兜底)。
- **run fork(参数试探分支)**:改第 6 步方法 → 1-5 步零重算复用父 run 产物,6-11 重跑。
- **Brain 可整层拔除**:intent/select/triage/narrate/cycle 全部有无-LLM 降级路径;
  LLM 只做候选集内选择题(枚举索引),越界拒绝,截断整体拒绝,润色不许动数字。
- **受约束的自主循环**:一个回合内 Agent 连续多周期工作——
  单周期单决策,动作白名单闭集,周期上限(默认 6,可调 1-12)+同签名熔断+审批门内置;
  execute 永远只产生确认卡绝不自启流水线;brain 拔除/无密钥时整回合退回单步路径
  (设计 `docs/AGENT-LOOP.md`;守护 `tests/test_api_loop*.py`、`tests/test_brain_cycle.py`、
  `tests/test_net_search.py`、`tests/test_subtasks.py`,单步零回归由 `tests/test_converse.py` 锁死)。
- **诚实模拟模式**:引擎缺失时可走合成执行演示全流程,
  日志/产物/账本全程显式标注 simulated,证据封顶 runnable。

## 开发与测试

```powershell
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"        # 跑测试足够;真实 qa/出图另加 raster:".[dev,raster]"
.venv\Scripts\python -m pytest tests/ -q

# 机器高负载时,时序敏感用例(@pytest.mark.timing)可能被拖慢误判 —— 设系数放宽判定窗:
$env:INSAR_TEST_TIME_FACTOR = "3"
.venv\Scripts\python -m pytest tests/ -q -m timing
.venv\Scripts\python -m pytest tests/ -q -m "not timing"
.venv\Scripts\python scripts\stress_test_timing.py

# 可选:pre-commit(ruff / 尾空格 / EOF / YAML)
.venv\Scripts\python.exe -m pip install pre-commit
.venv\Scripts\pre-commit.exe install
```

环境变量:

| 变量 | 含义 | 默认 |
|---|---|---|
| `INSAR_HOME` | 数据库与会话工作区根目录 | `./workspace` |
| `INSAR_PORT` | 服务端口 | `8873` |
| `INSAR_ALLOW_SIMULATED` | 引擎缺失时允许模拟执行 | `1` |
| `INSAR_LLM_BASE_URL` / `INSAR_LLM_API_KEY` / `INSAR_LLM_MODEL` | LLM(OpenAI 兼容);不配 = brain 禁用,手动流水线 | 无 |
| `INSAR_LLM_FALLBACK_*` | 单跳备用路由 | 无 |
| `INSAR_TAVILY_KEY` | 自主循环联网检索:配置后网页检索优先走 Tavily | 无(用免密钥端点) |
| `INSAR_ASF_SEARCH_BASE` / `INSAR_WEBSEARCH_BASE` / `INSAR_TAVILY_BASE` | 覆盖检索基址(测试指向本地 mock) | 官方端点 |
| `INSAR_TEST_TIME_FACTOR` | 仅测试:时序判定窗放宽系数 | `1` |

## MCP server(可选)

内核也可暴露为 MCP 工具集。产品主路径仍是 pi Desktop 的 `insar_*` 闭集,不把 MCP 当作闸门旁的洞。

```powershell
.venv\Scripts\pip install -e ".[mcp]"
.venv\Scripts\python -m insar_agent.api.app
.venv\Scripts\python -m insar_agent.mcp
```

工具清单与宿主配置见 [src/insar_agent/mcp/README.md](src/insar_agent/mcp/README.md);基址 `INSAR_API_BASE`(默认 `http://127.0.0.1:8873`),验收 `tests/test_mcp_server.py`。

## 目录结构

```
.pi/             Desktop 适配器(工作台默认打开)
docs/            设计与实测:DESIGN / AGENT-DESIGN / AGENT-LOOP / VALIDATION
src/insar_agent/
├── registry/    纯数据:11 步能力声明(方法/参数分类/产物候选/run_ok/超时/replay)
├── planner/     可行性收窄(带理由)→ 打分 → 计划/fork
├── core/        SQLite 真相源:规范化哈希/三段指纹/三档文件指纹/状态机/失效传播/干预队列/账本
├── runtime/     作业目录契约(local/wsl)/双超时日志流/五阶段执行器/产物发现/环境探测
├── audit/       contract.yaml 阈值台账/run_ok 双判定/指标重解析/六级证据阶梯
├── brain/       LLM 门面(可拔除):intent/select/triage/narrate
├── loop/        自主循环:事件驱动 driver / 事件总线 / 上下文预算 / 子任务池
├── net/         联网检索薄层:ASF / 网页 / 并行扇出;只出查询词
├── engines/     薄封装零决策:mintpy / isce2 / snaphu / pystamps / hyp3 / simulate
│   └── bridges/ prep_isce + isce2_to_pystamps(诚实接口边界)
├── report/      run.sh 等价命令 / 方法章节模板
└── api/         FastAPI:NDJSON 回合流 + SSE + 干预/预览/fork/导出
pi-insar/        pi 扩展:31 个 insar_* 工具、守卫、技能、计划文档
desktop/         仅 pi-app-overlay(壳补丁镜像);同步 scripts/sync-pi-app-overlay.ps1
scripts/         启动器:insar-backend-real / insar-pi-desktop / insar-pi
workspace/       INSAR_HOME:账本、会话、llm.json、真实验收数据
tests/           契约/执行器/失效传播/Brain/API(不含旧网页 UI)
```

## Phase 状态

| Phase | 内容 | 状态 |
|---|---|---|
| 0 地基 | schema / normalize / fingerprint / filehash / store / capabilities | **完成**,验收达标:哈希三坑有测试;`should_skip` 幂等重放(同一步执行两次,第二次全跳过);`record_version` 门控(absorb-M);fp 三段编码 `policy:algo:digest`;干预队列 `deliver_as` 双投递 + 未消费可编辑/撤回 |
| 1 执行层 | 五阶段执行器 / 双超时 / reattach / run_ok | 完成(11 对 HyP3 真实数据验收,见下节;ISCE2 全链见 `docs/VALIDATION-isce2-wsl.md`) |
| 2 失效传播 | 级联标脏 + 原因分类 + 干预队列 | 完成 |
| 3 审计 | contract.yaml / 证据阶梯 / provenance / run.sh | 完成 |
| 4 Brain | intent/select/triage/narrate(可拔除有守护测试) | 完成 |
| 5 API | FastAPI + NDJSON/SSE | 完成 |
| 6 桥 | isce2_to_pystamps(主 novelty) | **接口边界,未实现**(需真值环境) |

## 真实数据验收(2026-08-12,Windows 原生,无 WSL)

Phase 1 验收「11 对 HyP3 真实数据跑通 MintPy 链」**已通过**:

- 数据:Ridgecrest 2019 同震,11 对 HyP3 干涉对(云端已完成配准/干涉/滤波/解缠,即 2-6 步)
- 环境:`E:\miniforge3\envs\insar`(conda-forge,MintPy 1.6.4 + GDAL 3.13.2,**BLAS=OpenBLAS**)
- 链路:1 导入(目录联接+ERA5 缓存)→ 7 SBAS 反演 → 8 误差校正(ERA5 免凭据)→
  9 阶跃形变模型 → 10 真实出图 → 11 真实质检,全链 38 秒,`status=done`
- 证据:**audited**(PENDING 阈值如实封顶);6 项指标全部重解析通过
  (mean_coherence 0.949 / residual_rms 21.7 mm / vel p2-p98 −309~+828 mm/yr)
- 顺带验证了 orphaned 续跑:环境事件中断后不带 `--fresh` 续跑到完成

复跑:`.venv\Scripts\python.exe scripts/real_ridgecrest.py [--fresh]`
(环境变量 `INSAR_ENGINE_PREFIX`、`INSAR_HYP3_SOURCE` 可覆盖默认值)。

### 环境坑位记录(排障日志)

| 坑 | 症状 | 处置 |
|---|---|---|
| MintPy `read_template` 不指定编码 | 中文 Windows 按 GBK 读 UTF-8 cfg 崩 | cfg 纯 ASCII + 子进程 `PYTHONUTF8=1` |
| conda-forge Windows 默认 BLAS=MKL,MKL 2024 在 i9-13900K 上崩(0xC06D007F,多线程延迟加载) | deramp 第 2 景硬崩无栈 | `conda install "libblas=*=*openblas"` 切 OpenBLAS |
| conda-forge pysolid 的 Fortran DLL 加载失败 | 固体潮校正崩 | 默认 SET=off(与实测基准配置一致) |
| wrapper 以 `-m` 启动依赖宿主包环境 | PATH 污染时静默死 | 改按文件路径启动(wrapper 纯 stdlib)+ wrapper.err 落盘 |

## 当前边界(诚实声明)

- **ISCE2 全链(2-6 步)与 SNAPHU 已在 WSL 实测跑通**(ALOS Baja 同震对,2026-08-12,
  见 `docs/VALIDATION-isce2-wsl.md`,验证经 §4.7 作业目录文件契约 + `wsl_wrapper.sh` 手工完成)。
  WSL 后端此后已接进执行链:`runtime/backend_select.py` 按引擎路由(isce2/snaphu 且
  `INSAR_WSL_DISTRO` 可达 → `WslJobBackend`,可用 `INSAR_JOB_BACKEND` 强制),含真实 WSL
  echo 冒烟测试;**真实 ISCE2 重型链尚未经代理编排回放**(只回放过手工链)。
  日常 HyP3 路线仍把 2-6 步交给 ASF 云端。
- **ISCE2→PyStamps 桥(主 novelty)是接口边界**:三个难点(par 字段自洽/TCN 基线/
  big-endian)不允许在无真值环境下猜测实现,见 `engines/bridges/isce2_to_pystamps.py`。
- 阈值台账 5 项 PENDING 待标定 → 证据阶梯封顶 audited(§4.13 的自我约束);
  crossval_r 在 PS 链建成前诚实缺席(质量门仅警告)。
- h5 栅格的 not_all_nan 检查当前对不可解析格式降级为警告(待接 h5py 到宿主校验器)。
