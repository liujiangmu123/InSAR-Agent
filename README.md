# insar-agent

<!-- CI 徽章占位:推送到 GitHub 后把 OWNER/REPO 换成真实仓库路径即点亮(工作流已就位:ci.yml / desktop.yml) -->
[![ci](https://github.com/OWNER/REPO/actions/workflows/ci.yml/badge.svg)](https://github.com/OWNER/REPO/actions/workflows/ci.yml)

可复现 InSAR 科学工作流 Agent:**参数级失效传播 + 步级断点续跑 + 完整 provenance**。

## 文档索引

| 文档 | 内容 |
|---|---|
| `docs/DESIGN.md` | 产品定稿:novelty、11 步 × 方法矩阵、桥梁地图、竞品分析 |
| `docs/AGENT-DESIGN.md` | 架构定稿:七条硬约束 → 分层/五阶段执行器/指纹/SQLite/UI |
| `docs/VALIDATION-isce2-wsl.md` | 最新实测:ISCE2 WSL 全链(ALOS Baja 同震对,2026-08-12 跑通) |
| `reference/AGENT_PRODUCTS_LEARNING.md` | codex/gemini-cli/OpenHands/cline + snakemake/dvc 定向调研(absorb-E~P) |
| `reference/COMPARISON_LEARNING.md` | redun/aiida/agentic-swmm 等对照学习(absorb-A~D) |
| `reference/PI_FRAMEWORK_ANALYSIS.md` | pi 框架对标与吸收决议(absorb-E1~E8) |

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
- **Brain 可整层拔除**:intent/select/triage/narrate 全部有无-LLM 降级路径;
  LLM 只做候选集内选择题(枚举索引),越界拒绝,截断整体拒绝,润色不许动数字。
- **诚实模拟模式**:引擎缺失(本机无 WSL)时可走合成执行演示全流程,
  日志/产物/账本全程显式标注 simulated,证据封顶 runnable。

## 快速开始

```powershell
# 推荐:虚拟环境 + 可编辑安装
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"        # 跑测试足够;真实 qa/出图另加 raster:".[dev,raster]"
.venv\Scripts\python -m pytest tests/ -q     # 215 项测试(2026-08-12 全绿)

# 机器高负载时(多代理并行开发/后台大任务):时序敏感用例(@pytest.mark.timing,
# 心跳/双超时/宽限判定窗)可能被拖慢误判 —— 设系数放宽判定窗(断言语义不变):
$env:INSAR_TEST_TIME_FACTOR = "3"
.venv\Scripts\python -m pytest tests/ -q                 # 全量(判定窗×3)
.venv\Scripts\python -m pytest tests/ -q -m timing       # 只跑时序敏感组
.venv\Scripts\python -m pytest tests/ -q -m "not timing" # 只跑常规组(CI 常规 job 同款)
# 负载模拟验证(2 个忙循环进程占 ~50% 核 60s 自灭 × timing 组 3 遍):
.venv\Scripts\python scripts\stress_test_timing.py

.venv\Scripts\python scripts\test_js.py      # 前端 state.js 单测(node --test,零 npm 依赖)
.venv\Scripts\python -m insar_agent.api.app  # http://127.0.0.1:8873(UI + API)

# 可选:装 pre-commit 提交钩子(ruff / 尾空格 / EOF / YAML / CSS 质检,配置见 .pre-commit-config.yaml)
.venv\Scripts\python.exe -m pip install pre-commit
.venv\Scripts\pre-commit.exe install

# 备选:不安装也能跑(tests/conftest.py 把 src 插入 sys.path;需全局 pytest)
python -m pytest tests/ -q
```

浏览器打开 http://127.0.0.1:8873 ,输入「Ridgecrest 地震同震形变」→ 确认执行。
后端不可达或 file:// 打开时,前端自动回退到 mock 演示模式。

只看原型(纯静态,零依赖,不起后端):

```powershell
cd prototype
python -m http.server 8000              # 浏览器开 http://127.0.0.1:8000
```

环境变量:

| 变量 | 含义 | 默认 |
|---|---|---|
| `INSAR_HOME` | 数据库与会话工作区根目录 | `./workspace` |
| `INSAR_PORT` | 服务端口 | `8873` |
| `INSAR_ALLOW_SIMULATED` | 引擎缺失时允许模拟执行 | `1` |
| `INSAR_LLM_BASE_URL` / `INSAR_LLM_API_KEY` / `INSAR_LLM_MODEL` | LLM(OpenAI 兼容);不配 = brain 禁用,手动流水线 | 无 |
| `INSAR_LLM_FALLBACK_*` | 单跳备用路由 | 无 |
| `INSAR_TEST_TIME_FACTOR` | 仅测试:时序判定窗放宽系数(高负载并行开发用 3;产品超时语义不受影响) | `1` |

## 目录结构

```
docs/            设计文档:DESIGN.md(产品)+ AGENT-DESIGN.md(架构)
reference/       竞品与开源框架学习报告(absorb 决议台账)
src/insar_agent/
├── registry/    纯数据:11 步能力声明(方法/参数分类/产物候选/run_ok/超时/replay)
├── planner/     可行性收窄(带理由)→ 打分 → 计划/fork
├── core/        SQLite 真相源:规范化哈希/三段指纹/三档文件指纹/状态机/失效传播/干预队列/账本
├── runtime/     作业目录契约(local/wsl)/双超时日志流/五阶段执行器/产物发现/环境探测
├── audit/       contract.yaml 阈值台账/run_ok 双判定/指标重解析/六级证据阶梯
├── brain/       LLM 门面(可拔除):intent/select/triage/narrate
├── loop/        事件驱动 driver/事件总线(监听隔离)/上下文预算
├── engines/     薄封装零决策:mintpy(--dostep) / isce2 / snaphu / pystamps / hyp3 / simulate
│   └── bridges/ prep_isce(官方)+ isce2_to_pystamps(Phase 6,诚实接口边界)
├── report/      run.sh 等价命令 / 方法章节模板
└── api/         FastAPI:NDJSON 回合流 + SSE + 干预/预览/fork/导出
prototype/       Web UI(零依赖);backend.sse.js 接真后端,mock 保留为离线演示
tests/           215 项:哈希坑/幂等重放/reattach/孤儿/取消/超时/级联标脏/契约纪律/拔除守护/API
```

## Phase 状态

| Phase | 内容 | 状态 |
|---|---|---|
| 0 地基 | schema / normalize / fingerprint / filehash / store / capabilities | **完成**,验收达标:哈希三坑有测试;`should_skip` 幂等重放(同一步执行两次,第二次全跳过);`record_version` 门控(absorb-M);fp 三段编码 `policy:algo:digest`;干预队列 `deliver_as` 双投递 + 未消费可编辑/撤回 |
| 1 执行层 | 五阶段执行器 / 双超时 / reattach / run_ok | 完成(11 对 HyP3 真实数据验收,见下节;ISCE2 全链见 `docs/VALIDATION-isce2-wsl.md`) |
| 2 失效传播 | 级联标脏 + 原因分类 + 干预队列 | 完成 |
| 3 审计 | contract.yaml / 证据阶梯 / provenance / run.sh | 完成 |
| 4 Brain | intent/select/triage/narrate(可拔除有守护测试) | 完成 |
| 5 API + 前端 | FastAPI + NDJSON/SSE + backend.sse.js | 完成 |
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
