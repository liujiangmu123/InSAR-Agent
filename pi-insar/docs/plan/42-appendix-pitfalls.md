# 42 · 附录 B — 常见坑速查表(跨 Phase 通用)

| 坑 | 症状 | 处置 |
|---|---|---|
| Windows 路径分隔 | TS 断言/脚本里 `\` 与 `/` 混用失配 | TS 源码断言写 `\\`;Node API 一律 `join/resolve`;ps1 用 `Join-Path` |
| 8899 端口占用 | vitest globalSetup 起后端超时 | `$env:INSAR_TEST_PORT` 换端口;查 `netstat -ano | findstr 8899` |
| 8873 端口占用 | 真实后端起不来 | 结束旧进程或 `-Port 8874` + `INSAR_API_BASE` 同步改 |
| 临时 INSAR_HOME 隔离 | 测试污染真实 workspace | 只信 globalSetup 的 mkdtemp;绝不把测试 INSAR_HOME 指向 workspace/ |
| 模拟/真实引擎切换 | run 里 simulated 标志不符预期 | 模拟=测试专用(`INSAR_ALLOW_SIMULATED=1`);真实后端脚本固定 `=0` + `INSAR_ENGINE_PREFIX` |
| SQLite -shm/-wal 锁 | teardown EBUSY / 双后端锁冲突 | rmWithRetry 已兜底;同一 HOME 永远只跑一个后端 |
| pi 升级 × 自定义主题 | 升级后主题加载失败 | 按 `43-appendix-pi-upgrade.md` 流程;`REQUIRED_TOKENS` 与 themes.md 同步 |
| MSYS 路径转换 | Git Bash 下路径参数错乱 | 优先 ps1 启动器;必要时 `MSYS_NO_PATHCONV=1` |
| 项目信任 | `.pi/settings.json` 不生效 | 首次交互式启动 pi 时选择信任 |
| llm.json 漂移 | defaultModel 与 chat_model 不一致 | 以 llm.json 为准更新 `.pi/settings.json`(一处) |
| npm 全局装不动 | `npm i -g` 卡住/403 | `npm config get registry` 查代理与镜像源 |
| `pwsh` 不存在 | 启动器跑不了 | `powershell -File scripts/insar-pi.ps1`(脚本兼容 5.1) |
| 残留 python 进程 | 端口占用/文件锁持续 | `Get-Process python | Stop-Process`;确认后重跑 |
| APPEND_SYSTEM 超 8KB | skills.test 预算断言失败 | 精简措辞/空行,不得删测试要求的关键词 |
| 大图撑爆上下文 | view_figure 后会话变慢 | 用 figures 条目的 `url`(浏览档)替代 `fullUrl` 原图 |
| 工具计数断言过期 | skills/tools 测试红:toHaveLength 失配 | 每个加工具的 Phase 都同步改两处计数(skills.test.ts + tools.integration.test.ts),提交前 `npx vitest run` |
| MintPy CLI 弹窗阻塞 | plot/view 命令挂起不退出 | 一律加 `--nodisplay --save`;引擎构建器统一注入,不靠人记 |
| 中文/空格路径 × GDAL/HDF5 | 导出/读 h5 报打开失败 | 传给 CLI 的路径先 `str(Path(...))` 正规化;新建产物一律落 run 工作区(ASCII 相对路径) |
| 硬链接跨卷失败 | passthrough 物化报 EXDEV/权限错 | `os.link` 失败即回退 `shutil.copy2`(构建器内建,勿删) |
| 场景包关键词抢占 | classify_text 命中错误场景 | 新包 match 关键词禁止与既有包重叠;`scenario_of()` 全包回归 |
| 分析 run 缺源 run | register_sources 报输入不存在 | 先 `insar_list_artifacts` 拿源 run 的**绝对**产物路径再规划分析 run |
| h5 数据集名不匹配 | 分解/统计步报 dataset not found | 校正组合决定文件名与数据集名;引擎按注册表候选列表探测,不硬编码单一名 |
| conda run 启动慢 | mintpy 命令看似卡死 | `conda run -p E:\miniforge3\envs\insar` 首次要秒级预热;超时阈值按注册表 timeouts,不手动 kill |
| 预测越界 | 外推期 > 观测跨度的一半被拒 | 这是纪律不是 bug(Phase 13);缩短 horizon 或如实告知用户 |
