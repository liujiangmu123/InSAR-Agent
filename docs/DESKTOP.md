# 桌面版完整文档(Tauri 2 壳 + Python sidecar)

> 读者:开发者与发布者。壳的实现细节与启动流程见 `desktop/README.md`;
> 技术选型论证(为什么不是 Electron / pywebview / 纯 Rust)见
> `docs/OPTIMIZATION.md` §4;发布前逐项核对 `docs/RELEASE-CHECKLIST.md`。
>
> 一句话:**Rust 只做壳(窗口 + sidecar 生命周期),业务 100% 留在 Python
> 后端(`src/insar_agent`)与零依赖 Web UI(`prototype/`),后端零改动。**

## 1. 架构:三层进程与两条边界

壳与 sidecar 之间只有两样东西:环境变量 + 本机 HTTP;sidecar 与引擎之间
只有作业目录文件契约。没有 IPC、没有共享内存、没有自定义协议。

```
┌ Tauri 壳(desktop/,Rust + WebView2)────────────────────────────────┐
│  WebView2 窗口 ─────── 加载 http://127.0.0.1:{INSAR_PORT}/           │
│  sidecar 管理 ──────── 探测 Python → spawn → 轮询健康检查(30s 超时) │
│  失败诊断窗口 ──────── Python 路径/端口/日志尾部,绝不静默退出       │
│  退出语义 ──────────── 窗口全关 → 直接 kill sidecar(见 §4)         │
└──────────────┬───────────────────────────────────────────────────────┘
               │ spawn:python -m insar_agent.api.app
               │   · 显式传 INSAR_PORT(壳与 sidecar 永不脱节)
               │   · stdout/stderr → %TEMP%\insar-agent-sidecar-{port}.log
               │ 健康检查:GET /api/health == 200(手写 std::net,每 500ms)
               ▼
┌ Python sidecar(宿主 Python,INSAR_PYTHON 可指定)──────────────────┐
│  FastAPI + uvicorn:API + prototype/ 静态 UI,监听 127.0.0.1:{PORT}  │
│  唯一真相源:{INSAR_HOME}/ 下的 SQLite 与会话工作区                  │
│  五阶段执行器 / 指纹 / 失效传播 / 干预队列 / provenance 全在这一层   │
└──────────────┬───────────────────────────────────────────────────────┘
               │ 作业目录契约(job.pid / job.log / job.rc / job.cancel)
               │ 引擎子进程以 DETACHED_PROCESS + 新进程组启动,
               │ 不随 sidecar 死亡(这是断点续跑语义的物理基础,见 §4)
               ▼
┌ 引擎环境(conda,与宿主 Python 完全独立)───────────────────────────┐
│  {INSAR_ENGINE_PREFIX}\python.exe 运行 MintPy 等科学栈               │
│  数据源:{INSAR_HYP3_SOURCE}(HyP3 产品目录,导入步消费)            │
│  2GB+ 科学栈永不打进安装包(OPTIMIZATION §4 决议)                   │
└───────────────────────────────────────────────────────────────────────┘
```

### 1.1 端口与环境变量约定

| 变量 | 消费方 | 作用 | 默认 |
|---|---|---|---|
| `INSAR_PORT` | 壳 + sidecar | 后端端口。壳读取后**显式传给 sidecar**,二者永远一致;非法值/0 回退默认 | `8873` |
| `INSAR_HOME` | sidecar | SQLite 与会话工作区根目录,相对路径按 sidecar 工作目录解析(dev 模式 = 仓库根) | `./workspace` |
| `INSAR_PYTHON` | 壳 | sidecar 解释器,优先级最高;缺省时依次探测 `C:\Python314\python.exe`、`PATH` | 无 |
| `INSAR_ENGINE_PREFIX` | 引擎封装 | conda 引擎环境前缀,MintPy 等以 `{prefix}\python.exe` 启动(细粒度覆盖:`INSAR_ENGINE_PYTHON`) | 无(缺 → 模拟模式) |
| `INSAR_HYP3_SOURCE` | 导入步 | HyP3 数据源目录;`params.source` 优先于它,两者都缺 → 导入步**显式失败** | 无 |
| `INSAR_DESKTOP_SMOKE=1`(或 `--smoke`) | 壳 | 无 GUI 自检:spawn → 健康检查 → kill → 退出码 0/1 | 关 |

两条纪律:

- 壳只认识 `INSAR_PORT` / `INSAR_PYTHON`(外加自检开关);引擎相关变量由
  sidecar 从进程环境**继承**——桌面场景下配成系统/用户级环境变量,或在同一
  终端 `$env:...` 设置后再启动壳。
- sidecar 的 stdout/stderr 全量落盘 `%TEMP%\insar-agent-sidecar-{port}.log`,
  这是排障第一现场(诊断窗口会自动展示其尾部)。

## 2. 开发工作流

三步:.venv → cargo build → 运行。

### 2.1 建立宿主虚拟环境

```powershell
py -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
# 验证后端能独立起来(Ctrl+C 关掉即可):
.venv\Scripts\python.exe -m insar_agent.api.app   # http://127.0.0.1:8873
```

sidecar 解释器必须装有本项目(`pip install -e .`),否则 spawn 后立即
`No module named insar_agent` 退出 → 弹诊断窗口。

### 2.2 构建壳

```powershell
cd desktop
cargo build        # 首次拉取并编译几百个 crate,10-25 分钟属正常
cargo test         # 壳单元测试:端口解析/手写 HTTP 客户端/诊断页托管
```

crates.io 已通过 `desktop/.cargo/config.toml` 换源 rsproxy(国内镜像,
且显式禁用系统代理);CI 上则用 `--config` 把源换回官方(见
`.github/workflows/desktop.yml`),两边互不干扰。

### 2.3 运行与调试

```powershell
# 推荐:显式把 sidecar 钉到项目虚拟环境(绝对路径)
$env:INSAR_PYTHON = "E:\...\00insaragent\.venv\Scripts\python.exe"
cd desktop
cargo run
```

三种常用姿势:

| 场景 | 做法 | 原理 |
|---|---|---|
| 前后端一起调 | 直接 `cargo run` | 壳探测 → spawn → 健康检查 → 开窗 |
| 只调前端/后端热重载 | 先手动起 `python -m insar_agent.api.app`,再 `cargo run` | 壳发现端口上**已有健康后端 → 直接连接**,不 spawn;退出时也不误杀你的进程 |
| 无人值守自检 | `cargo run -- --smoke`(或 `INSAR_DESKTOP_SMOKE=1`) | spawn → 等健康 → kill → 退出码 0/1,适合脚本与 CI |

## 3. 发布工作流

现状:`tauri.conf.json` 中 `bundle.active = false`,只出裸 exe(开发用)。
发布 = 图标 → 后端冻结 → 安装包 → 更新清单四步,全部在**发布分支**上做,
过程中动到的配置(bundle、updater)不回流 main 之前先过 RELEASE-CHECKLIST。

### 3.1 图标

- `desktop/icons/` 当前是占位图(`python icons/make_icons.py` 纯标准库生成);
- 正式发布前用设计稿替换:装 tauri-cli 后 `cargo tauri icon 源图1024.png`
  一键生成全套尺寸;
- **`icons/icon.ico` 是 Windows 构建硬依赖**(tauri-build 把它嵌进 exe 资源,
  缺失直接编译失败)。

### 3.2 后端冻结(PyInstaller,v2 策略)

OPTIMIZATION §4 的两级策略:v1 检测系统 Python/conda 并引导(环境向导);
v2 用 PyInstaller 把 FastAPI 后端冻结进安装包。冻结要点:

- **入口**:PyInstaller 不支持 `-m`,需要一个薄入口脚本,内容等价于
  `python -m insar_agent.api.app`;
- **数据文件**:`pyproject.toml` 的 package-data 清单就是 `--add-data` 清单——
  `core/schema.sql`、`audit/contract.yaml`、`runtime/wsl_wrapper.sh`、
  `registry/scenarios/**`;`prototype/` 静态 UI 同样要随包,并验证冻结环境
  下的查找路径;
- **边界不动**:引擎 conda 环境(2GB+ 科学栈)**永不冻结进包**,仍走
  `INSAR_ENGINE_PREFIX` 外部引用;
- **壳侧配套小改动**(发布分支):探测顺序优先「安装目录内的冻结后端 exe」,
  sidecar 工作目录从仓库根切到应用数据目录——`main.rs` 中 `repo_root()`
  的回退分支即为此预留;
- 验证:冻结产物直接双击/命令行启动,`GET /api/health` 返回 200,再挂壳跑
  `--smoke`。

### 3.3 tauri build 出安装包

```powershell
cargo install tauri-cli --locked
# 发布分支:tauri.conf.json 置 bundle.active = true,核对 productName/identifier/version
cd desktop
cargo tauri build      # NSIS/MSI 安装包 + updater 签名链路
```

签名:`signtool sign /fd SHA256`(exe 与安装器都签),或在 tauri 配置里
指定证书让构建自动签;无证书发布必须在发布说明里声明 SmartScreen 影响。

### 3.4 更新清单(Tauri updater)

当前壳**未接** updater(OPTIMIZATION §3 列为产品化项),发布自更新时:

1. `cargo tauri signer generate` 生成密钥对:公钥进 tauri 配置,
   **私钥不入库**;
2. 每次发布对安装包签名得到 `signature`;
3. 固定 URL 托管 `latest.json`:
   `{version, pub_date, notes, platforms: {"windows-x86_64": {url, signature}}}`;
4. 验证「上一版 → 本版」原地升级,且 `INSAR_HOME` 数据完好。

## 4. 桌面语义:关闭 = 断点中断,重开 = reattach 续跑

壳退出时**直接 kill sidecar,没有优雅停机协商**——这是刻意设计,不是偷懒。
它成立的原因是执行层的两个事实(AGENT-DESIGN §4,尤其 §4.3 五阶段执行器):

1. **状态先于进程**:五阶段(PREPARED→LAUNCHED→RUNNING→COLLECTED→VERIFIED)
   每次推进都先落 SQLite,pid 与日志偏移持久化;kill sidecar 不丢任何账。
2. **引擎作业不陪葬**:引擎子进程以 `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`
   启动(`runtime/jobs.py`),生死状态写在作业目录文件里
   (`job.pid/job.log/job.rc`),sidecar 死了它继续算。

| 桌面动作 | 进程层面 | 执行器语义(AGENT-DESIGN §4.3) |
|---|---|---|
| 关闭窗口(应用退出) | 壳 kill sidecar;引擎作业继续跑或自然结束 | **断点中断**:阶段状态/pid/log_offset 均已持久化 |
| 重新打开 | 壳重新 spawn sidecar(或复用端口上已健康的后端) | **reattach 续跑**:继续执行时逐阶段幂等守卫——pid 存活 → 从 log_offset 续读日志,不重跑;已死 → 按 `job.rc` 与产物完整性结算 COLLECTED/FAILED;环境死亡 → orphaned(严格区分于计算失败,不当作算错) |
| 干预 KILL / 写 `job.cancel` | 作业进程组终止 | INTERRUPTED:保留日志偏移,可续跑 |

对用户的意义:第 7 步 SBAS 反演跑到一半关掉桌面,MintPy 继续算;重开后作业
被**认领**(不双启动、不重跑),轨迹从断点接续。这与 Web 版「服务重启
reattach 存活作业」是同一套语义,桌面只是把「服务重启」变成了日常操作。

## 5. 故障排查

出现任何异常,先跑 `cd desktop && cargo run -- --smoke` 把问题压缩到
「壳 → sidecar → 健康检查」链路,再对表:

| 症状 | 常见原因 | 处置 |
|---|---|---|
| 诊断窗:未找到 Python 解释器 | 三级探测(`INSAR_PYTHON` → `C:\Python314\python.exe` → `PATH`)全落空 | 设 `INSAR_PYTHON` 指向 `.venv\Scripts\python.exe`(**绝对路径**) |
| 诊断窗:sidecar 进程提前退出 | 解释器没装本项目(`No module named insar_agent`);依赖缺失;`INSAR_HOME` 不可写 | 对该解释器 `pip install -e .`;读日志 `%TEMP%\insar-agent-sidecar-{port}.log`(诊断窗已展示尾部) |
| 诊断窗:健康检查超时 | 端口被**非本项目**进程占用,uvicorn bind 失败但进程未即刻退出;后端启动极慢 | `Get-NetTCPConnection -LocalPort 8873` 找占用者;换 `INSAR_PORT` 重试 |
| 窗口白屏 / 创建窗口失败 | WebView2 Runtime 缺失(精简系统 / Windows Server;Win11 自带) | 安装 Microsoft Edge WebView2 Evergreen Runtime 后重开 |
| 步骤全被标 simulated | `INSAR_ENGINE_PREFIX` 未配,或指向的 conda 环境损坏(引擎探测失败 → 默认 `INSAR_ALLOW_SIMULATED=1` 走模拟) | 配好引擎环境再跑;GBK/MKL/pysolid 等环境坑位见根 `README.md`「环境坑位记录」 |
| 打开后连上了「别人的」后端 | 端口上已有健康后端,壳按设计直接复用 | 想要独立实例 → 换 `INSAR_PORT` |
| 关闭桌面后引擎进程还在 | 见 §4:这是断点续跑语义,**不是泄漏** | 想终止 → 重开后对 run 下 KILL 干预;或按 `job.pid` 手动终止 |
