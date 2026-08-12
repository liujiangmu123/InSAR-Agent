# 发布检查单(桌面版)

> 逐项打勾,任何一项不满足就不打 tag。流程详解见 `docs/DESKTOP.md` §3,
> 架构与排障见同文档 §1/§5。

## 1. 版本号同步(三处一个都不能漏)

- [ ] `pyproject.toml` → `[project] version`
- [ ] `src/insar_agent/__init__.py` → `__version__`
- [ ] `desktop/tauri.conf.json` → `version`
- [ ] 顺手核对 `desktop/Cargo.toml` → `[package] version` 与上面一致
      (第四处,壳自己的 crate 版本)

不同步的后果:UI/API 报告的版本与安装包对不上,provenance 里的 agent
版本失真。快速核对(把 X.Y.Z 换成目标版本):

```powershell
rg "X\.Y\.Z" pyproject.toml src/insar_agent/__init__.py desktop/tauri.conf.json desktop/Cargo.toml
```

## 2. 测试绿

- [ ] 本机全量:`.venv\Scripts\python.exe -m pytest tests/ -v` 全绿
- [ ] CI:`.github/workflows/tests.yml` ubuntu + windows 矩阵全绿
- [ ] 壳单元测试:`cd desktop && cargo test` 全绿

## 3. 真数据冒烟

- [ ] `scripts/real_ridgecrest.py --fresh` 跑到 `status=done`、
      `evidence=audited`(需配 `INSAR_ENGINE_PREFIX` / `INSAR_HYP3_SOURCE`)
- [ ] 桌面壳无 GUI 自检:`cd desktop && cargo run --release -- --smoke`
      退出码 0
- [ ] 手动冒烟:桌面开会话跑一条链,**中途关窗重开**,验证断点续跑
      (作业被认领、轨迹接续、不双启动)——这是桌面版的核心承诺(DESKTOP.md §4)

## 4. 图标

- [ ] `desktop/icons/` 占位图已替换为正式设计稿
      (`cargo tauri icon 源图1024.png` 生成全套)
- [ ] `desktop/icons/icon.ico` 存在(Windows 构建硬依赖,tauri-build
      嵌进 exe 资源)

## 5. 安装包与签名(2026-08-12 首次全量打包实测校准)

> 以下步骤在 Win11(10.0.26200)+ Python 3.14.3 + Rust 1.96.0 + tauri-cli 2.11.4
> + PyInstaller 6.22.0 实测走通。全链一键约 10 分钟(依赖已装、cargo 增量);
> 首次冷启动另加:pip 装依赖 ~8 分钟、cargo 全量编译 ~5.5 分钟。

### 5.1 一键命令序列(验证过)

```powershell
# ① 一次性:venv + 依赖(已有 .venv 可跳过)
py -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev,raster]" h5py pyyaml pyinstaller

# ② 一次性:tauri-cli 预编译二进制(约 7 MB,落点 .tools\tauri-cli\)
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\fetch_tauri_cli.ps1
#    ⚠ 实测坑:Invoke-WebRequest 直连与 gh 反代均易涓流卡死(不支持断点续传,
#    连接一断从头再来)。卡住时用 curl.exe(Windows 自带)兜底,一次成功:
#    curl.exe -L -o .tools\tauri-cli\cli.zip -C - --connect-timeout 12 --max-time 150 `
#      https://ghfast.top/https://github.com/tauri-apps/tauri/releases/download/tauri-cli-v2.11.4/cargo-tauri-x86_64-pc-windows-msvc.zip
#    然后核对 SHA256(fetch_tauri_cli.ps1 内 $KnownSha256 表)并解压到 .tools\tauri-cli\

# ③ 每次发布:一键构建(后端冻结 → cargo release → NSIS 安装包)
$env:CARGO_TARGET_DIR = 'E:\cargo-target-desktop-bundle'        # 可选:C 盘紧张时把构建目录放 E 盘
$env:TAURI_BUNDLER_TOOLS_GITHUB_MIRROR = 'https://ghfast.top/'  # NSIS 工具链首次下载走镜像
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build_desktop.ps1 -SkipDeps
# 产物:{target}\release\bundle\nsis\InSAR-Agent_0.1.0_x64-setup.exe(约 29 MB)
# 实测体积:壳 14.6 MB / 后端 onedir 70.3 MB / 安装包 28.9 MB / 装后 85.2 MB
```

### 5.2 检查项

- [ ] 发布分支:`tauri.conf.json` 置 `bundle.active = true`,
      核对 `productName` / `identifier`,且 `bundle.resources` 保留
      `"backend-bundle/dist/insar-backend/": "backend/"` 映射
      (冻结后端整目录进安装包,装后落 `<安装目录>\backend\`,
      恰是 sidecar 探测链的第①优先级)
- [ ] 后端冻结产物(PyInstaller)单独可启动:`desktop\backend-bundle\smoke_test.ps1`
      过(`/api/health` 200),再手动核对 `/api/registry` 与 `/`(UI 首页)
- [ ] 冻结产物数据文件齐全(`dist\insar-backend\_internal\` 下逐个核对,
      清单 = pyproject 的 package-data + `prototype/`):
      `insar_agent/core/schema.sql`、`insar_agent/audit/contract.yaml`、
      `insar_agent/runtime/wsl_wrapper.sh`、`insar_agent/runtime/local_wrapper.py`、
      **`insar_agent/registry/scenario_packs/<4 个场景包>`**(实测踩坑:spec 曾漏掉
      scenario_packs,`load_scenarios()` 静默返回空,意图识别整体失效且无报错)
- [ ] `scripts\build_desktop.ps1` 出 NSIS 安装包(内部顺序:后端冻结必须先于
      cargo —— `bundle.resources` 源目录缺失时 tauri-build 在编译期直接 panic:
      `resource path 'backend-bundle\dist\insar-backend' doesn't exist`,实测)
- [ ] 代码签名:exe 与安装器都过 `signtool sign /fd SHA256`;
      无证书发布 → 在发布说明里声明 SmartScreen 影响(本期未做)
- [ ] 干净机器装/卸各一遍,按 §5.3 的断言矩阵逐项打勾

### 5.3 安装实测断言矩阵(2026-08-12 全绿)

| 环节 | 断言 | 实测 |
|---|---|---|
| 静默安装 `/S` | 退出码 0;装后 85.2 MB | ✓(48s) |
| 安装布局 | `%LOCALAPPDATA%\InSAR-Agent\{insar-agent-desktop.exe, backend\insar-backend.exe, backend\_internal\, uninstall.exe}` | ✓ |
| 快捷方式 | 开始菜单 + 桌面(静默装自动建桌面快捷方式,NSIS 模板行为) | ✓ |
| 注册表 | `HKCU\...\Uninstall\InSAR-Agent`(DisplayVersion 与包一致) | ✓ |
| 启动 | 8873 端口 `/api/health` 200;主窗题 InSAR-Agent | ✓(约 5s) |
| **冻结后端优先** | sidecar 日志(`%TEMP%\insar-agent-sidecar-8873.log`)首行 `spawn sidecar:冻结后端:<安装目录>\backend\insar-backend.exe` | ✓ |
| UI/数据链 | `/` 200(prototype UI);`/api/registry` 11 个能力步骤;`/api/version` 报 `frozen:true` | ✓ |
| 数据目录 | `%LOCALAPPDATA%\insar-agent-data\workspace\insar.db` 创建;安装目录内**无** workspace | ✓ |
| 托盘 | 关主窗后进程存活、后端健康(关闭到托盘);二次启动单实例前置已有窗口 | ✓ |
| 静默卸载 `/S` | 退出码 0;安装目录、两处快捷方式、注册表项全清;残留进程 0 | ✓ |
| 数据保留 | 卸载后 `insar-agent-data\` 完好(设计如此,升级/重装不丢数据) | ✓ |

### 5.4 已修的打包坑(复发即查此表)

1. **数据目录撞名安装目录**:NSIS `installMode: currentUser` 默认装到
   `%LOCALAPPDATA%\InSAR-Agent`,与冻结后端旧缺省 `%LOCALAPPDATA%\insar-agent`
   在 Windows 大小写不敏感 —— 同一物理目录,insar.db 寄生进安装目录
   (卸载留尾巴、用户手动清"残留"会误删数据)。已改 `entry.py` 缺省为
   `%LOCALAPPDATA%\insar-agent-data\workspace`,托盘"打开数据目录"同步。
2. **scenario_packs 漏包**(见 §5.2 第三项)。
3. **hypothesis 混入生产包**:经 `pydantic.v1._hypothesis_plugin` /
   `hypothesis.extra.numpy` 的可选 import 被 PyInstaller 静态分析拖入,
   spec `excludes` 已显式排除(dev-only 库)。
4. **tauri-cli 下载涓流卡死**(见 §5.1 ② 的 curl 兜底)。
5. **NSIS 工具链**:缓存在 `%LOCALAPPDATA%\tauri\NSIS`,存在即离线可用;
   首次下载设 `TAURI_BUNDLER_TOOLS_GITHUB_MIRROR`。
6. **大件清单(装后 86 MB 的去向)**:OpenBLAS 19.6 MB(numpy 依赖,不可免)
   > 壳 exe 14.6 MB > python314.dll 6.4 MB > pydantic_core 5.0 MB
   > libcrypto 5.0 MB > hdf5 3.9 MB。无重复运行时、无 pytest/pip 混入;
   如需进一步瘦身,评估砍 `[raster]`(numpy+h5py 共约 33 MB,但 run_ok
   NaN 检查与 qa 引擎会降级)。

## 6. 更新清单(Tauri updater)

- [ ] `latest.json`:`version` / `pub_date` / `notes` /
      `platforms."windows-x86_64"` 的 `url` + `signature` 齐全
- [ ] 产物用 updater 私钥签名;**私钥不入库**,公钥在 tauri 配置中
- [ ] 从上一版安装包**原地升级**到本版成功,`INSAR_HOME` 数据完好

## 7. 打 tag 与发布

- [ ] `git tag vX.Y.Z && git push --tags` → 触发
      `.github/workflows/desktop.yml`,产物(exe)构建成功并已存档
- [ ] Release 挂载:CI 裸 exe + 本地 `tauri build` 安装包 + `latest.json`
- [ ] 发布说明:变更列表 + 已知边界(与根 README「当前边界(诚实声明)」
      同步:ISCE2 全链未验证、桥为接口边界、PENDING 阈值封顶等)
