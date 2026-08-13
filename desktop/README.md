# InSAR-Agent 桌面壳(Tauri 2 + Python sidecar)

Rust 只做「壳」:WebView2 窗口 + sidecar 生命周期管理。业务 100% 留在
Python 后端(FastAPI,`src/insar_agent/api/app.py`)与静态 Web UI
(`prototype/`),后端零改动 —— 架构决策见 `docs/OPTIMIZATION.md` §4。

这是一个纯 cargo 手写工程:不依赖 tauri-cli,也不依赖 node。

## 启动流程

1. 读 `INSAR_PORT`(缺省 8873);
2. 若端口上已有健康后端 → 直接连接(适合开发时手动起 uvicorn 的场景),
   此时不 spawn、退出时也不误杀别人的进程;
3. 否则探测 Python:`INSAR_PYTHON` > `{仓库根}\.venv\Scripts\python.exe`(项目约定)> `PATH`;
4. spawn `python -m insar_agent.api.app`(工作目录 = 仓库根,显式传
   INSAR_PORT;stdout/stderr 重定向到 `%TEMP%\insar-agent-sidecar-{port}.log`);
5. 轮询 `http://127.0.0.1:{port}/api/health`(手写 std::net 最小 HTTP GET,
   不引 reqwest),30 秒超时,sidecar 提前退出则立即失败;
6. 打开 1440x900 WebView 窗口指向 `http://127.0.0.1:{port}/`;
7. 任何失败(找不到 Python / spawn 失败 / 健康检查超时)→ 弹诊断窗口,
   含 Python 路径、端口、工作目录、sidecar 日志尾部与排查建议,绝不静默退出。

## 退出语义(为什么直接 kill)

窗口全关 → 应用退出 → 直接 kill sidecar。后端自带 orphan 检测与 reattach
语义:SQLite 里的 run/step 状态与轨迹完整保留,「桌面关闭 → 重开」等价于
「断点续跑」。这正是产品语义,不需要优雅停机协商。

## 构建与运行

前置:rustc/cargo(MSVC 工具链)、WebView2(Win11 自带)、宿主 Python 已在
本仓库执行过 `pip install -e .`。

```powershell
cd desktop
cargo build          # 首次需下载并编译几百个 crate,10-25 分钟属正常
cargo run            # 起 sidecar + 开窗口
cargo test           # 单元测试:状态行/端口解析、手写 HTTP 客户端、诊断页托管
```

环境变量:

| 变量 | 作用 |
|---|---|
| `INSAR_PORT` | 后端端口(缺省 8873) |
| `INSAR_PYTHON` | 显式指定 Python 解释器(优先级最高) |
| `INSAR_DESKTOP_SMOKE=1`(或 `--smoke` 参数) | 无 GUI 自检:spawn sidecar → 等健康检查 → kill → 退出码 0/1,无人值守验证用 |

## 与后端安全头的对齐结论(2026-08-13)

- 主窗口以 `WebviewUrl::External` 顶层导航加载后端,壳内零 iframe:后端新加的
  `X-Frame-Options: DENY` 与 CSP `frame-ancestors 'none'` 对壳无影响。
- 后端按页 CSP 未列 connect-src(回落 `default-src 'self'`),会拦下 Tauri fetch 型
  IPC(`http://ipc.localhost`)的首次尝试;tauri 2.11.5 内建自动回退到 postMessage
  通道(不受 CSP 管),JS 桥功能不受影响,仅 DevTools 留一条告警。壳侧无需修复。
- sidecar 就绪探测 `/api/health` 与后端路由一致;托盘新增「导出诊断包」(跳 Web UI
  「环境」面板),「打开数据目录」经 opener 插件打开后端 INSAR_HOME(workspace)。
- 逐条证据与行号引用见 `ALIGNMENT-2026-08-13.md`。

## crates.io 镜像(可选)

直连 crates.io 拉取慢时,创建 `desktop/.cargo/config.toml`:

```toml
[source.crates-io]
replace-with = "rsproxy-sparse"

[source.rsproxy-sparse]
registry = "sparse+https://rsproxy.cn/index/"
```

## 图标

`icons/` 下是纯标准库脚本生成的占位图(`python icons/make_icons.py` 可重新
生成)。Windows 构建必须有 `icons/icon.ico`(tauri-build 把它嵌进 exe 资源)。
正式发布前替换为设计稿导出:装 tauri-cli 后 `cargo tauri icon 源图1024.png`
可一键生成全套尺寸。

## 打包路线(后续工作)

当前 `bundle.active = false`,只出裸 exe(开发用)。发布安装器时:

1. `cargo install tauri-cli` → `cargo tauri build`,走 Tauri 官方 NSIS/MSI
   打包与 updater 签名链路;
2. sidecar 打包策略(OPTIMIZATION §4):v1 检测系统 Python/conda 并引导
   (环境向导);v2 用 PyInstaller 把后端冻结进安装包(引擎 conda 环境仍
   独立,2GB+ 不进包)。打包模式下 sidecar 工作目录应改为应用数据目录,
   `main.rs` 中 `repo_root()` 的回退分支即为此预留;
3. 托盘、单实例锁、开机自启(OPTIMIZATION §4 列为后续)。
