# 桌面版功能完整性矩阵(DESKTOP-PARITY)

> 目标:给「只用桌面版就能完成全部功能」提供可重复执行的证明 —— 冻结包
> (desktop/backend-bundle/dist 的 insar-backend.exe)与源码运行零差距。
>
> - 自动化断言:`tests/test_desktop_matrix.py`(marker `desktop`+`slow`,CI 无
>   dist 产物自动跳过)
> - 一键入口:`.venv\Scripts\python scripts\check_desktop.py`(dist 缺失先构建;
>   `--rebuild` 强制重建)
> - 手工核验清单:见文末(桌面壳 Tauri 的托盘/单实例/窗口记忆/原生对话框等,
>   自动化覆盖不到)

## 本轮实测(2026-08-13)

| 项 | 值 |
|---|---|
| 源码基线 | commit `07b3e14`(含 index.html 编码修复) |
| 冻结产物 | 本轮由 `build_backend.ps1` 现场构建(onedir,71.3 MB,PyInstaller 6.22.0) |
| 执行方式 | 冻结 exe 起在随机高位端口 + 一次性临时 INSAR_HOME,结束杀净;不触碰 8873 |
| 结果汇总 | **61 项:59 通过 / 2 已知差距(strict xfail)/ 0 失败 / 0 跳过** |

### 密封口径(为什么绝不触发真实计算)

矩阵给冻结 exe 的环境有三道闸,规划必然收敛到 simulated 方法(与 journey
测试的空探测打桩同语义):`INSAR_ENGINE_PREFIX` 指向空目录(压住对
`E:\miniforge3` 等已知安装位的隐式扫描)、最小 PATH(压住 `shutil.which`
探测)、家目录环境变量全部重定向到临时目录(压住 `~/.netrc` 等凭据探测,
HyP3 云端路线不可行)。`INSAR_ALLOW_SIMULATED=1` 显式声明。

## 功能矩阵(自动化部分)

「源码」列 = 同功能在源码运行下的既有守护(TestClient 级测试);「冻结包」列 =
本矩阵对冻结 exe 的真 HTTP 实测。

| 功能面 | 源码运行 | 冻结包 | 断言(tests/test_desktop_matrix.py) | 本轮结果 |
|---|:---:|:---:|---|---|
| API 面完整性:路径×方法与源码 OpenAPI 逐项一致 | ✅ | ✅ | `test_openapi_surface_parity_with_source` | 通过 |
| 11 个 router + 全部内联端点逐点探活(49 探针:GET 200 / 合理 4xx,POST 只测可达与校验错误形状) | ✅ | ✅ | `test_api_probe[*]`(49 参数化用例) | 通过 |
| 探活完整性守卫:新挂 router 不进矩阵立即红 | — | ✅ | `test_probe_table_covers_every_mounted_api_segment` | 通过 |
| 静态 UI:index 200 + 引用的 39 项资源(js 17 + css 22)逐个 200,且与源码逐字节一致(兼作 dist 过旧探测器) | ✅ | ✅ | `test_static_ui_index_and_assets_serve_source_bytes`(从 index.html 动态解析,不硬编码) | 通过 |
| 安全响应头:/api/* 的 nosniff / no-store / no-referrer / X-Frame-Options DENY | ✅ | ✅ | `test_health_and_api_security_headers` | 通过 |
| 安全响应头:静态 UI 按页 CSP(default-src 'self' / frame-ancestors 'none' / script-src / style-src) | ✅ | ✅ | `test_static_ui_csp_headers` | 通过 |
| LLM 配置面:临时 HOME 初始未配置 → POST 假配置 → 掩码回显(前8…后4)、完整密钥不回显、llm.json 落在冻结进程 INSAR_HOME 下 | ✅ | ✅ | `test_llm_config_roundtrip_persists_under_frozen_home` | 通过 |
| 模拟 run · 规划:quake 关键词 → 意图 rules 命中、云端 2-6 skipped、run ready 且 simulated、显式「模拟」横幅 | ✅ | ✅ | `test_turn_plans_quake_simulated` | 通过 |
| 模拟 run · 执行:[1,7,8,9,10,11] 逐步 exit 0 → run done | ✅ | ❌ | `test_pipeline_executes_simulated_run_to_done` | **差距 GAP-1**(strict xfail) |
| 模拟 run · 收尾:run 收敛到终态 + provenance 可导出(simulated 如实入账,11 步齐全) | ✅ | ✅ | `test_run_reaches_terminal_state_and_provenance_exports` | 通过 |
| 全局 SSE(/api/events)可建流 | ✅ | ✅ | `test_sse_events_endpoint_reachable` | 通过 |
| 版本零差距:/api/version 与源码 `__version__` 一致,如实自报 frozen | ✅ | ✅ | `test_version_parity_with_source` | 通过 |
| 技能零差距:/api/skills 数量 = 11(与仓库根 skills/ 一致) | ✅ | ❌ | `test_skills_count_matches_source_eleven` | **差距 GAP-2**(strict xfail) |

探针明细(49 项)按 router 分组:setup 3 / llm 3 / version 2 / skills 2 /
admin 2 / artifacts 2 / doctor 1 / timeseries-point 1 / diagnostics 2 /
datasets 3 / queue 3 / 会话与状态面 12 / 回合与干预面 7 / 导出面 6,
全部通过 —— 名单见 `tests/test_desktop_matrix.py` 的 `_PROBES` 表。

## 差距清单(缺陷不在矩阵内修,另行分派)

### ✅ GAP-1(已修复 2026-08-13)冻结包无法执行本地作业 —— 模拟 run 全链断裂

> **修复**:`runtime/jobs.wrapper_python()` 冻结态解析真实解释器
> (INSAR_PYTHON > 引擎前缀(显式/隐式)> PATH,全落空显式抛错),
> jobs/simulate/localdata 三处调用点切换;矩阵 `_hermetic_env` 提供
> INSAR_PYTHON(解释器不是引擎,不破密封语义)。重建 dist 后
> `test_pipeline_executes_simulated_run_to_done` 常规断言通过。原始记录留档:

- **现象**:`POST /api/pipeline` 后第 1 步永远等不到 wrapper 心跳,
  `startup_grace`(30 s)耗尽按 orphaned 判失败,run 终态 `failed`。
- **机理**:`runtime/jobs.py::LocalJobBackend.launch` 用 `sys.executable` 启动
  `local_wrapper.py`,`engines/simulate.py::build` 的 argv 同样以
  `sys.executable` 当 Python 解释器 —— 冻结态 `sys.executable` 是
  `insar-backend.exe` 本身,PyInstaller 引导器不解释脚本参数,结果是
  **误派生第二个后端实例**而不是执行 wrapper/模拟脚本。
- **现场证据**(本轮实测,作业目录 `wrapper.err` 原文摘录):

  ```text
  INFO:     Started server process [28256]
  ERROR:    [Errno 10048] error while attempting to bind on address ('127.0.0.1', 49185)
  INFO:     Application shutdown complete.
  ```

  误派生实例继承了 `INSAR_PORT`(已被父进程占用)→ 绑定失败秒退,未造成
  更大破坏;**但若调用环境没有设 `INSAR_PORT`,误派生实例会落到默认端口
  8873 常驻** —— 与生产实例撞端口或顶替生产实例,风险面不止「模拟 run 跑不通」。
- **影响**:桌面版(纯冻结包,无 Python 环境的机器)不能独立完成任何本地
  作业执行 —— 模拟演示链、以及真实链中由 LocalJobBackend 承担的步骤
  (mintpy/qa/figures/localdata)全部不可用;WSL 后端(isce2/snaphu)不经
  此路径,不受影响。
- **修复方向**(供分派参考,二选一或组合):冻结态解析真实 Python 解释器
  (引擎前缀 `INSAR_ENGINE_PREFIX` 的 python.exe / PATH 兜底 / 显式
  `INSAR_PYTHON` 环境变量,桌面壳 sidecar.rs 已有同名探测序);解析不到时
  显式失败并给出可读提示(§1.4 显式失败),绝不静默派生第二个后端。
- **验收**:修复后 `test_pipeline_executes_simulated_run_to_done` 会 XPASS
  (strict xfail)提醒改回普通断言;`wrapper.err` 不再出现 uvicorn 日志。

### ✅ GAP-2(已修复 2026-08-13)步骤技能文档未进冻结包 —— /api/skills 恒为空

> **修复**:`insar_backend.spec` datas 增仓库根 `skills/`;entry.py 注入
> INSAR_SKILLS_DIR(exe 旁优先、_internal 兜底)。重建 dist 后
> `test_skills_count_matches_source_eleven` 常规断言通过(11/11)。原始记录留档:

- **现象**:冻结包 `GET /api/skills` 返回 `{"skills": []}`(源码 11 份);
  `GET /api/skills/{step_id}` 恒 404。
- **机理**:`insar_backend.spec` 的 `datas` 清单缺仓库根 `skills/` 目录;
  `desktop/backend-bundle/entry.py` 也未注入 `INSAR_SKILLS_DIR`
  (`skills/loader.py` 头注声称「冻结打包由 desktop 侧 entry.py 在 import 前
  注入」,实际没有);loader 冻结态回退到 `<dist>/insar-backend/skills`
  (不存在)→ 空表。
- **影响**:桌面版的规划/分诊失去步骤技能知识源(参数启发式、失败处置
  章节不再注入 LLM 上下文),前端技能面板(skillpanel)静默空转;不影响
  流水线可执行性。
- **修复方向**:spec `datas` 补 `(REPO_ROOT/"skills", "skills")` 并在 entry.py
  仿 `INSAR_UI_DIR` 的手法注入 `INSAR_SKILLS_DIR`(exe 旁优先、_internal
  兜底);或直接让 loader 冻结态多探测 `_MEIPASS/skills`。
- **验收**:修复后 `test_skills_count_matches_source_eleven` XPASS;
  探针 `skills-step` 恒 200。

### 观察项(非本轮新差距,不计入差距数)

- probe.py 模块级 `Path.home()` 在家目录环境变量全缺时炸整个后端 ——
  已由 `tests/test_frozen_probe.py::test_frozen_survives_without_home_env`
  以 strict xfail 在案,本矩阵不重复记账。
- 冻结包 `/api/version` 的 `git_head` 为 null(dist 内无 git 仓库)——
  设计内降级,版本对齐断言不受影响。

## 手工核验清单(桌面壳 Tauri,自动化覆盖不到)

以下能力属桌面壳(desktop/,Tauri 2)而非后端冻结包,无法用本矩阵的 HTTP
断言覆盖,需要人工在打包安装形态下核验。本轮矩阵只构建了后端冻结包,
**壳侧手工项本轮未执行**,留待发布验收时按步骤逐项打勾。

| # | 项 | 步骤 | 预期 | 本轮结果 |
|---|---|---|---|---|
| M1 | 系统托盘 | 启动应用 → 点关闭按钮 → 看托盘;右键托盘逐项点菜单 | 关闭仅最小化到托盘不退出(默认开);菜单含 显示主窗口/隐藏/打开数据目录/检查更新/退出,逐项生效;左键单击图标唤起窗口 | ☐ 未执行 |
| M2 | 单实例 | 应用运行中再双击启动第二份 | 第二实例立即退出,第一实例窗口被带到前台还原(含从托盘还原) | ☐ 未执行 |
| M3 | 窗口记忆 | 调整窗口位置/尺寸/最大化 → 退出 → 重启 | 恢复上次位置尺寸与最大化状态;`%APPDATA%\insar-agent\window.json` 原子更新 | ☐ 未执行 |
| M4 | 原生对话框与系统打开 | 前端触发 `pick_directory`(如数据集添加扫描根)与 `open_path`(打开数据目录) | 弹原生目录选择框、取消返回空不报错;`open_path` 用资源管理器打开且只接受存在路径 | ☐ 未执行 |
| M5 | 全局快捷键 | 任意前台应用下按 Ctrl+Shift+I | 主窗口显示/隐藏切换;注册失败(热键被占)只记日志不崩 | ☐ 未执行 |
| M6 | sidecar 看护 | 任务管理器强杀 insar-backend.exe | 壳按 1s/2s/4s 退避自动重启(最多 3 次),标题栏提示;预算耗尽弹诊断页 | ☐ 未执行 |
| M7 | 端口裁决 | 先占住 8873(健康后端→复用;非健康占用→向上扫描) | 已有健康后端时直接复用不重复 spawn;端口被无关进程占用时自动换空闲端口开窗 | ☐ 未执行 |
| M8 | 壳冒烟自检 | `insar-agent.exe --smoke`(或 INSAR_DESKTOP_SMOKE=1) | spawn sidecar → 健康检查 → kill,退出码 0 | ☐ 未执行 |
| M9 | 更新检查 | 配置 INSAR_UPDATE_ENDPOINT 后启动;另在 UI 触发 /api/version/check | 只提示不自动安装;未配置时零网络行为(矩阵已自动验后端侧:`version-check` 探针) | ☐ 未执行 |

> 后端冻结包自身的冒烟(起动→健康→杀净)已由自动化矩阵覆盖,无需手工重复;
> `desktop/backend-bundle/smoke_test.ps1` 保留为无 pytest 环境的应急通道。

## 复跑与更新本报告

```powershell
.venv\Scripts\python scripts\check_desktop.py            # dist 缺失自动先构建
.venv\Scripts\python scripts\check_desktop.py --rebuild  # UI/源码改动后强制重建
```

- 矩阵内置「dist 过旧」探测:冻结包内 UI 资源与源码逐字节比对,不一致即红,
  按提示 `--rebuild`。
- 差距修复后对应 strict xfail 会 XPASS 报错 —— 把该测试改回普通断言,并把
  本报告对应 GAP 条目移入「已闭环」;新增 router/端点若忘记进矩阵,
  完整性守卫会直接红。
