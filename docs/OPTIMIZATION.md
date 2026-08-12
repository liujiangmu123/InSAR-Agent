# 优化分析与桌面版路线(2026-08-12)

> 依据:91→99 项测试全绿 + Ridgecrest 真实数据端到端验收(status=done, evidence=audited)
> 过程中的一手观察。每一条都注明证据来源与落点。
> 分级:P0 = 正确性缺陷(必须修);P1 = 设计已定未接线(AGENT-DESIGN 有明确设计);
> P2 = 产品化增强。

---

## 1. P0 · 正确性缺陷(立即修)

| # | 问题 | 证据 | 落点 |
|---|---|---|---|
| **P0-1** | `pending_actions` 队列**不按 run 隔离**:`store.due_actions(mode)` 全局取,两个并行 run 会互相消费对方的干预动作(KILL 尤其危险) | `core/store.py due_actions`;`loop/driver.py` 执行中轮询 | schema 加 `run_id` 列;`due_actions(run_id, mode)`;driver/API 全链传 run 归属 |
| **P0-2** | KILL 动作**无 target 校验**:任何 KILL 都取消"当前 run" | `loop/driver.py` 轮询分支只认 action 类型 | 校验 `target == run_id` 才消费 |
| **P0-3** | h5 栅格的 `not_all_nan/nan_fraction` 检查**实际未生效**(只支持 .npy,.h5 降级为警告) | 真实运行 WARNINGS:「step 7/9: artifact 无法解析,跳过 NaN 检查」 | `audit/runok.py` 接 h5py 读取器(宿主已装 h5py) |
| **P0-4** | 云端跳过步骤(2-6)的 **provenance 空洞**:只有 `state=skipped`,没有「由 HyP3 完成」的证据记录 | 真实 run 的 provenance:steps 2-6 无 artifacts 无注记 | 导入步登记 HyP3 产品元数据(README/参数 txt 的内容哈希)作为 2-6 步的替代证据 |
| **P0-5** | 时长预估的**样本归一化缺失**:duration_history 按 capability+method 全局中位,不同数据规模(11 对 vs 1000 对)混在一起 | `core/stale.py estimate_rerun` | 按 `pairs/scenes` 归一化存储与预估;样本 <3 仍显示「未知」(§7.5 纪律保留) |

## 2. P1 · 设计已定、尚未接线

| # | 问题 | 设计出处 | 现状 |
|---|---|---|---|
| P1-1 | **磁盘预算三级闸门**:计划时全链预算、步前检查、运行中水位 | AGENT-DESIGN §4.10 | `DiskEstimate` 已声明,executor/driver 未消费 |
| P1-2 | **资源租约仲裁**:`io=heavy` 最多 1 并发、CPU/内存池 | §4.11 | `leases` 表已建,未接(当前严格串行,做 DAG 并行时必须接) |
| P1-3 | **DAG 并行执行**:无依赖步骤并行(受资源池限制) | §4.11 | 串行;driver 单循环 |
| P1-4 | **WSL 后端零实测** + 批量指纹协议(指纹在 WSL 内算,宿主只收哈希) | §4.7/§4.9 | 代码完成,等 WSL 环境;`fingerprint_dir` 对万级文件目录会慢(9p 红线) |
| P1-5 | **§7.8 断线恢复**:关浏览器重开,从 SQLite 恢复轨迹流 + reattach 横幅 | §7.8 | 后端数据齐(trace/chat/SSE),前端未消费 |
| P1-6 | **面板 7/8/9**(轨迹 Lab Notebook / 终端全量日志 / 环境)前端未实现;四种新条目(reattach/intervention/degrade/gate_stop)后端已发、前端无专门渲染 | §7.2/§7.4 | `/api/env`、`/api/trace` 已就绪 |
| P1-7 | **审批卡影响预估仍用前端 mock 计算**(rerunSummary 本地估算),未接 `/api/impact` 的指纹级权威结果 | §7.6 | API 已就绪,一线之隔 |
| P1-8 | **brain 的 LLM 路径未经真实供应商验证**(无 key;规则/降级路径已全测) | §3.5 | 配 `INSAR_LLM_*` 后需一轮真实回归 |
| P1-9 | 质检硬门空转:crossval_r 诚实缺席(PS 链未建),SBAS-only 路线缺一个**有来源依据**的硬门(如 mean_coherence,须走 contract.yaml 纪律标定) | §4.13 | 当前只有 PENDING 警告 |

## 3. P2 · 产品化

- **桌面版**(见 §4)
- 环境向导:检测/一键创建 conda 引擎环境(复用本次安装流水线:清华镜像 + OpenBLAS 切换)
- 多会话并发压测(SSE 扇出、SQLite WAL 竞争)
- API 鉴权(当前 127.0.0.1 单机;跨机部署前必须)
- 观测面板:UNKNOWN 失败率(§4.12:它升高说明有新失败模式)、每步耗时统计
- 安装器与自更新(Tauri updater)

## 4. 桌面版技术选型(结论:Tauri 2 壳 + Python sidecar)

先说清一个前提:**业务层不可能用 Rust 重写**——MintPy/ISCE2/GDAL 生态是 Python,
指纹/执行器/审计 6000 行 Python 已验收。所以「桌面软件用 Rust」的正确姿势是:
Rust 只做**壳**(窗口/托盘/生命周期/更新),业务进程原样保留。

| 方案 | 壳语言 | 安装包体积 | 复用现有 Web UI | 复用 Python 后端 | 结论 |
|---|---|---|---|---|---|
| **Tauri 2** | Rust + WebView2 | ~5-10 MB | ✓ 原样 | sidecar 子进程 | **推荐**:本机已有 rustc 1.96 MSVC ✓,Win11 自带 WebView2 ✓ |
| Electron | Node | 100 MB+ | ✓ | 子进程 | 重;本机无系统 node |
| pywebview | 纯 Python | 最小(pip 一个包) | ✓ | 同进程 | 最快出活的**过渡版**,无托盘/更新生态 |
| egui/iced 纯 Rust | Rust | 小 | ✗ 全部重写 | ✗ | 不可行 |

**架构**:

```
┌ Tauri 壳(Rust,desktop/)───────────────────────────┐
│  窗口(WebView2 加载 http://127.0.0.1:{port})        │
│  sidecar 管理:探测 Python → spawn uvicorn → 健康检查 │
│  → 退出时优雅终止(复用作业目录契约的取消语义)         │
│  托盘/单实例锁/开机自启(后续)                        │
└──────────────────────────────────────────────────────┘
        │ spawn + watch
┌ Python sidecar ──────────────────────────────────────┐
│  insar_agent.api.app(FastAPI,现有代码零改动)        │
└──────────────────────────────────────────────────────┘
```

sidecar 打包策略:v1 检测系统 Python/conda 环境并引导(环境向导);
v2 PyInstaller 把后端冻结进安装包(引擎 conda 环境仍独立,2GB+ 不进包)。

## 5. Git 工作流(决议)

现状:父目录 `E:\01所有项目\06定职讲师` 是大杂烩仓库,`00insaragent/` 从未被跟踪(untracked)。

决议:
1. **`00insaragent` 独立 init 仓库**(嵌套仓库,父仓库自然不跟踪其内容,互不干扰);
2. `main` 立即提交基线(今天全部成果:源码+测试+文档;数据/参考仓库已被 .gitignore 排除);
3. 并行开发用 **git worktree 分支**(每个子代理一个隔离分支),完成后跑全量 pytest 再合并回 main;
4. 提交纪律:每完成一个可验证单元(测试绿)即提交;合并前全量回归。

## 6. 并行开发分工(本轮启动)

| 分支 | 范围(互不冲突) | 验收 |
|---|---|---|
| desktop-tauri | 全新 `desktop/` 目录:Tauri 2 壳 + sidecar 管理 | `cargo build` 通过;`cargo run` 打开窗口并连上后端 |
| backend-hardening | P0-1~P0-5:`core/store.py`、`loop/driver.py`、`audit/runok.py`、`engines/localdata.py`、`api/app.py` | 全量 pytest 绿 + 新增回归用例 |
| ui-realwire | P1-5/6/7:仅 `prototype/js`(审批卡接 /api/impact、SSE 断线恢复、环境面板、四种新条目渲染) | 起服务手测 + 现有测试绿 |
