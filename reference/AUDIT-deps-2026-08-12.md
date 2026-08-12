# 依赖安全审计(2026-08-12)

工程化门禁落地时的一次性全量依赖审计:Python 侧 `pip-audit`,Rust 侧因网络受限改用
OSV 等价扫描(方法与局限见 §2)。结论先行:

- **Python:三方依赖零已知漏洞**;唯一命中是 venv 内 `pip 25.3` 自身(4 项 PYSEC),
  升级 pip 至 26.2.1 后复扫清零。
- **Rust(desktop/):零漏洞级通告**;17 个 crate 命中"不再维护"信息级警告
  (全部为传递依赖),1 项 unsoundness 仅存在于 Linux 目标依赖图,Windows 构建不受影响。

## 1. Python(pip-audit 2.10.1,.venv @ Python 3.14)

审计对象:`pip install -e ".[dev,raster]" h5py pyyaml hypothesis ruff pip-audit`
装出的完整环境(fastapi 0.141.1 / uvicorn 0.52.1 / pyyaml 6.0.3 / numpy 2.5.2 /
h5py 3.16.0 / pytest 9.1.1 / httpx 0.28.1 / hypothesis 6.165.3 等 51 包)。

| 包 | 版本 | 通告 | 修复版本 | 处置 |
|---|---|---|---|---|
| pip | 25.3 | PYSEC-2026-196 | 26.1.2 | 已升级 venv 内 pip → 26.2.1,复扫通过 |
| pip | 25.3 | PYSEC-2026-1796 | 26.0 | 同上 |
| pip | 25.3 | PYSEC-2026-2875 | 26.1 | 同上 |
| pip | 25.3 | PYSEC-2026-2876 | 26.1 | 同上 |

其余全部三方依赖:**无已知漏洞**。

说明:

- `insar-agent` 本体以 editable 安装,pip-audit 按设计跳过(本地包无 PyPI 通告可查,
  不是问题)。
- pip 属安装器而非运行时依赖,漏洞面在装包动作本身;各机器的存量 venv 建议同步执行
  `.venv\Scripts\python.exe -m pip install --upgrade pip`。CI(ci.yml)每次建 venv 后
  先 `pip install --upgrade pip`,天然不受此项影响。
- 首次运行曾遇 pypi.org 读超时(本机对境外源不稳),加 `--timeout 90` 重试成功;
  复现命令见 §3。

## 2. Rust(desktop/,Cargo.lock 共 536 crate)

### 方法说明(与原计划的偏差)

`cargo install cargo-audit --locked` 安装成功(走 rsproxy 镜像),但 `cargo audit`
需要从 `github.com/RustSec/advisory-db` 拉取通告库——本机 git 默认走 127.0.0.1:7897
本地代理(未运行),禁代理直连 github 亦超时,多次重试均不可达。

**替代方案**:RustSec 通告库已官方镜像进 OSV(osv.dev),故解析 `desktop/Cargo.lock`
全部 536 个 crate,调用 `api.osv.dev/v1/querybatch` 批量查询(crates.io 生态),
覆盖面与 `cargo audit` 等价(同一数据源),仅少了 cargo-audit 的本地依赖图归因输出。
网络恢复(代理开启)后建议补跑一次原生 `cargo audit` 交叉确认。

### 结果:0 项漏洞级,17 个 crate 命中信息级通告

| 类别 | crate(版本) | 通告 | 分析 |
|---|---|---|---|
| unsound | glib 0.18.5 | RUSTSEC-2024-0429 / GHSA-wrw7-89jp-8q8g | `VariantStrIter` 迭代器实现 unsound,修复在 0.20.0。glib 是 **Linux 目标专属**传递依赖(tauri→wry/tao 的 GTK 栈),Windows 构建(desktop.yml 与 ci.yml rust job 均为 windows)不编译此 crate;版本由 tauri 2.x 上游钉定,本地不可独立升级,随 tauri 升级自然消解 |
| unmaintained | atk / atk-sys / gdk / gdk-sys / gdkwayland-sys / gdkx11 / gdkx11-sys / gtk / gtk-sys / gtk3-macros(均 0.18.2) | RUSTSEC-2024-0411~0420 | gtk-rs GTK3 绑定停止维护(上游转向 GTK4)。同为 Linux 目标专属;Tauri 官方路线待 wry 迁移,无本地动作可做 |
| unmaintained | proc-macro-error 1.0.4 | RUSTSEC-2024-0370 | 构建期 proc-macro 传递依赖,不进运行时;等上游替换为 proc-macro-error2 |
| unmaintained | unic-char-property / unic-char-range / unic-common / unic-ucd-ident / unic-ucd-version(均 0.9.0) | RUSTSEC-2025-0075/0080/0081/0098/0100 | tauri 标识符校验的传递依赖,信息级 |

直接依赖版本(cargo tree --depth 1,均为当前 2 系最新线):
tauri 2.11.5 / tauri-build 2.6.3 / tauri-plugin-single-instance 2.4.3 /
tauri-plugin-global-shortcut 2.3.2 / tauri-plugin-updater 2.10.1 /
rfd 0.15.4 / serde 1.0.229 / serde_json 1.0.151。

### 误报与限制说明

- "unmaintained" 是 RustSec 的**信息级**通告(非漏洞):原生 `cargo audit` 默认也只按
  warning 输出、不改变退出码;上表列出仅为透明,**无需为此改动依赖**。
- GTK3 家族与 glib 出现在 Cargo.lock 属正常:lockfile 是平台无关的全量解;
  它们只在 Linux 目标的依赖图里被真正编译。本仓桌面壳当前只出 Windows 产物。
- OSV 扫描按「精确版本 ∈ 受影响区间」判定,无误报机制上的已知偏差;
  但查询时点(2026-08-12)之后发布的新通告自然不在内,建议纳入例行(如每次发版前)复扫。

## 3. 复现命令

```powershell
# Python(约 1-2 分钟,境外源慢时加大超时)
.venv\Scripts\python.exe -m pip_audit --skip-editable --progress-spinner off --timeout 90

# Rust 原生(需 github.com 可达:代理开启后)
cargo install cargo-audit --locked   # 已安装则跳过
cd desktop; cargo audit

# Rust 等价(github 不可达时):解析 Cargo.lock 批量查 osv.dev
# (本次审计所用脚本为临时脚本,未入库;逻辑= querybatch{name,version,ecosystem=crates.io})
```
