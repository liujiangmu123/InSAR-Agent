# insar-agent

可复现 InSAR 科学工作流 Agent:**参数级失效传播 + 步级断点续跑 + 完整 provenance**。

设计文档:`docs/DESIGN.md`(产品与 novelty)· `docs/AGENT-DESIGN.md`(架构定稿)·
`reference/PI_FRAMEWORK_ANALYSIS.md`(pi 框架对标与吸收决议)。

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
pip install -e ".[dev]"
pytest                                  # 91 项测试
python -m insar_agent.api.app           # http://127.0.0.1:8873(UI + API)
```

浏览器打开 http://127.0.0.1:8873 ,输入「Ridgecrest 地震同震形变」→ 确认执行。
后端不可达或 file:// 打开时,前端自动回退到 mock 演示模式。

环境变量:

| 变量 | 含义 | 默认 |
|---|---|---|
| `INSAR_HOME` | 数据库与会话工作区根目录 | `./workspace` |
| `INSAR_PORT` | 服务端口 | `8873` |
| `INSAR_ALLOW_SIMULATED` | 引擎缺失时允许模拟执行 | `1` |
| `INSAR_LLM_BASE_URL` / `INSAR_LLM_API_KEY` / `INSAR_LLM_MODEL` | LLM(OpenAI 兼容);不配 = brain 禁用,手动流水线 | 无 |
| `INSAR_LLM_FALLBACK_*` | 单跳备用路由 | 无 |

## 目录结构

```
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
tests/           91 项:哈希坑/幂等重放/reattach/孤儿/取消/超时/级联标脏/契约纪律/拔除守护/API
```

## 真实数据验收(2026-08-12,Windows 原生,无 WSL)

Phase 1 验收「11 对 HyP3 真实数据跑通 MintPy 链」**已通过**:

- 数据:Ridgecrest 2019 同震,11 对 HyP3 干涉对(云端已完成配准/干涉/滤波/解缠,即 2-6 步)
- 环境:`E:\miniforge3\envs\insar`(conda-forge,MintPy 1.6.4 + GDAL 3.13.2,**BLAS=OpenBLAS**)
- 链路:1 导入(目录联接+ERA5 缓存)→ 7 SBAS 反演 → 8 误差校正(ERA5 免凭据)→
  9 阶跃形变模型 → 10 真实出图 → 11 真实质检,全链 38 秒,`status=done`
- 证据:**audited**(PENDING 阈值如实封顶);6 项指标全部重解析通过
  (mean_coherence 0.949 / residual_rms 21.7 mm / vel p2-p98 −309~+828 mm/yr)
- 顺带验证了 orphaned 续跑:环境事件中断后不带 `--fresh` 续跑到完成

复跑:`python scripts/real_ridgecrest.py [--fresh]`(环境变量 `INSAR_ENGINE_PREFIX`、
`INSAR_HYP3_SOURCE` 可覆盖默认值)。

### 环境坑位记录(排障日志)

| 坑 | 症状 | 处置 |
|---|---|---|
| MintPy `read_template` 不指定编码 | 中文 Windows 按 GBK 读 UTF-8 cfg 崩 | cfg 纯 ASCII + 子进程 `PYTHONUTF8=1` |
| conda-forge Windows 默认 BLAS=MKL,MKL 2024 在 i9-13900K 上崩(0xC06D007F,多线程延迟加载) | deramp 第 2 景硬崩无栈 | `conda install "libblas=*=*openblas"` 切 OpenBLAS |
| conda-forge pysolid 的 Fortran DLL 加载失败 | 固体潮校正崩 | 默认 SET=off(与实测基准配置一致) |
| wrapper 以 `-m` 启动依赖宿主包环境 | PATH 污染时静默死 | 改按文件路径启动(wrapper 纯 stdlib)+ wrapper.err 落盘 |

## 当前边界(诚实声明)

- **ISCE2 全链(2-6 步)与 SNAPHU 未验证**:无 Windows 包,需 WSL(装法见对话记录)
  或云端 Linux;当前 HyP3 路线把这些步骤交给 ASF 云端。
- **ISCE2→PyStamps 桥(主 novelty)是接口边界**:三个难点(par 字段自洽/TCN 基线/
  big-endian)不允许在无真值环境下猜测实现,见 `engines/bridges/isce2_to_pystamps.py`。
- 阈值台账 5 项 PENDING 待标定 → 证据阶梯封顶 audited(§4.13 的自我约束);
  crossval_r 在 PS 链建成前诚实缺席(质量门仅警告)。
- h5 栅格的 not_all_nan 检查当前对不可解析格式降级为警告(待接 h5py 到宿主校验器)。
