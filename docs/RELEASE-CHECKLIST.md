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

## 5. 安装包与签名

- [ ] 发布分支:`tauri.conf.json` 置 `bundle.active = true`,
      核对 `productName` / `identifier`
- [ ] 后端冻结产物(PyInstaller)单独可启动,`/api/health` 返回 200
      (数据文件清单 = pyproject 的 package-data + `prototype/`,见 DESKTOP.md §3.2)
- [ ] `cargo tauri build` 出 NSIS/MSI 安装包
- [ ] 代码签名:exe 与安装器都过 `signtool sign /fd SHA256`;
      无证书发布 → 在发布说明里声明 SmartScreen 影响
- [ ] 干净 Win11 虚拟机装/卸各一遍:装上能起(WebView2 自带),
      卸载后无残留进程与开机项

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
