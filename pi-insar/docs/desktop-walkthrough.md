# Desktop 走查手册

人工验收剧本。仓库根:`E:\01所有项目\06定职讲师\00insaragent`。壳:`E:\SoftApp\pi-app`。不要杀已在跑的 8873 后端和 Electron。

科学内核(11 步 + 分析 20–28 + 31 个 `insar_*`)已在后端/扩展交付。Desktop 只验证:对话能调到这些方法、右栏可见、图内联、产物能打开、strict 生效。

---

## D0 · 基线:壳原生 + InSAR 扩展进 Desktop

目标:证明 pi Desktop 原生可用,且打开本仓库后 `.pi/` 扩展进会话。

### 启动

- [ ] 窗口 A 后端已在 `http://127.0.0.1:8873`(已在跑则保持)。未起时:`pwsh scripts/insar-backend-real.ps1`
- [ ] 窗口 B Desktop 已开(已开 Electron 则不要再起)。未起时:`pwsh -NoProfile -File scripts/insar-pi-desktop.ps1`

启动器会幂等自愈 `.pi/skills` 与 `.pi/themes` 的 junction(只删重解析点,不 Recurse 进目标)。

### 打开工作区

- [ ] 启动器写入 `INSAR_DESKTOP_PROJECT` = 本仓库根,窗口应直接打开该项目(不要停在空的 pi Project Home / 临时沙箱)
- [ ] 顶栏/窗口标题为 **InSAR Agent**,右栏可见 **InSAR 工作台**
- [ ] 若提示信任项目,选择**信任**(否则 `.pi/` 扩展与 `insar-llm` 不加载)

### 模型与扩展

- [ ] 模型选择器出现 `insar-llm` / `deepseek-v4-flash-0731`,且为默认
- [ ] 中文说「调用 insar_health」→ 工具卡返回 ok
- [ ] `/insar-mode strict` → 让它跑任意 bash → 被 guard 拦截
- [ ] 左栏/设置等 pi 原生功能正常;插件页可见 InSAR Agent 适配器

### overlay 脚手架

```powershell
pwsh -File scripts/sync-pi-app-overlay.ps1 -Check
```

- [ ] 退出码 0;打印 `N 个文件一致`

规则与 `-Pull` / `-Push` 见 `desktop/pi-app-overlay/README.md`。

### 哨兵(R-D0-1)

若会话因 builtin SDK 0.83.0 加载扩展失败:**不改扩展迁就 0.83**;设置 → SDK → 切 global 0.84.2,并把该事实记在 D4。

---

## D1 · 九条轻型剧本(既有 audited run,禁止新全链)

前置:后端指向 `workspace/realtest`(或当前真实 HOME);Desktop 工作区=本仓库;模型 `insar-llm/deepseek-v4-flash-0731`。**不要**对用户说「我帮你重跑 MintPy」——未获「批准执行」不得 `insar_execute_run` 新的 01–11 全链。

剧本 5 的分析 run(掩膜/区域平均)是轻型后处理,允许在已有 audited 产物上跑;若模型要规划 01–11,必须先停下来问用户。

| # | 中文指令(示例) | 期望 |
|---|---|---|
| 1 | 「后端还活着吗?列出现有会话」 | `insar_health` ok;`insar_list_sessions` 见 real 会话 |
| 2 | 「看看最新 run 的状态和证据级」 | 11 步 done、evidence=audited、simulated=false;右栏 InSAR Tab 同步 |
| 3 | 「查震中那个点的时序」 | `insar_timeseries_point`;数值与 `curl` 同一端点逐位一致 |
| 4 | 「把速度场图给我看看,并让 AI 评一下图的质量」 | `insar_view_figure` **时间线内联可见**;`insar_vision_qa` 结构化质检 |
| 5 | 「基于这个 run 做掩膜后算区域平均速度,出一张图」 | 分析 run 20→21→24→25;右栏显示分析轨道;数字可溯 |
| 6 | 「预测这个点未来 6 个月形变,给不确定度」 | 步 26/27;±1σ/±2σ + validity + 免责声明 |
| 7 | 「切严格模式,然后帮我 ls 一下目录」 | `/insar-mode strict`;bash 被拦截 |
| 8 | 「出中文报告和复现包」 | `insar_report` 数字对 provenance;`insar_repro_bundle` zip |
| 9 | (抽查)报告任取 3 个数字 | 与 `GET /api/provenance?run_id=...` 一致 |

图不内联 → 记 G7 回归失败,先查时间线 media 卡是否展开、`showInlineFigures` 是否开。

---

## D2 · 右栏流水线

- [ ] 右栏出现 **InSAR** Tab(或 `/insar` 打开)
- [ ] 无 run:空壳 + mode,不崩
- [ ] 有 audited run:11 步字形(✔/R/○/✖)
- [ ] 插件页「探测后端」httpCheck 成功
- [ ] session 空或 `@latest` 钉最近活动会话

---

## D3 · 技能、openPath、strict 心智

- [ ] 技能页可见 `00-insar-agent`、步技能 `01-data-acquisition`…`11-crossval-qa`、场景包 quake/subsidence/volcano/landslide/permafrost/stripmap_coseismic
- [ ] 插件页 InSAR Agent:**打开 INSAR_HOME**、**打开会话/run 目录**、**打开导出目录** 均弹出资源管理器(默认 `workspace`、`workspace/sessions`、`pi-insar/exports`;真实验收可把 homeDir 改成 `workspace/realtest`)
- [ ] `/insar-mode free` 恢复 bash;`strict` 再拦

### 重型计算批准话术

Agent **不得**自行发起新的真实 01–11 全链 / `real_ridgecrest.py` / MintPy 重跑。仅当用户原话含「批准执行」或同等明确同意时,才 `insar_plan_run` + `insar_execute_run`,且一次一个、低优先级。规则见仓库 `.cursor/rules` 的重型计算管控与 `pi-insar/docs/plan/01-execution-rules.md` 第 8 条。

研究/方法选择:先 `insar_capabilities` 拿闭集,再 `insar_recommend_route` / `insar_read_skill`,禁止编方法 id。

---

## D4 · SDK:builtin 0.83.0 → global 0.84.2

壳 `package.json` 的 bundled SDK **保持 0.83.0**,不要为对齐去 bump 壳依赖。

1. `npm ls -g @earendil-works/pi-coding-agent` 应为 0.84.2
2. Desktop **设置 → SDK → 使用 global** → 重启会话 worker
3. 复跑 D0 的 insar_health + 剧本 3/4/7
4. 阻断则切回 builtin,差异记 `43-appendix-pi-upgrade.md`,**不 patch SDK 加载器**

当前记录:开发态曾用 builtin 0.83.0 加载本仓库扩展;若 D0 已通过则 builtin 可暂留,D4 正式验收以设置页切换为准(操作在用户机器的 Desktop UI,不写进仓库 userData)。

---

## D5 · 自打包(需明确批准)

```powershell
pwsh -File scripts/package-pi-desktop.ps1            # dry-run,不编译
pwsh -File scripts/package-pi-desktop.ps1 -Execute   # npm run package:win;先关 dev 窗口
```

- [ ] `E:\SoftApp\pi-app\dist\` 出现 Setup 与 Portable
- [ ] 安装到干净目录 → 打开本仓库 → D0 步骤 4 冒烟 + 剧本 1/2/4/7
- [ ] 无启动器 env 时扩展仍回退 `http://127.0.0.1:8873`
- [ ] 项目 `.pi/extensions/insar.ts` 在 asar 下仍能加载;失败则记「打包版需 global SDK」,优先 `electron-builder.yml` asarUnpack,不给扩展加构建步

---

## 上下游方法(对话即可,不新造工具)

| 环节 | 用户怎么说 | Agent 调什么 |
|---|---|---|
| 数据接入 | 导入本地 / ASF / HyP3 | `insar_list_datasets` → `insar_plan_run`(步 01,方法闭集来自 `insar_capabilities`) |
| 11 步主链 | 配准/干涉/解缠/时序… | `insar_plan_run` → preview/apply → `insar_execute_run`(须批准) |
| 分析 20–28 | 掩膜、分解、统计、预测、反演桥 | `pipeline=analysis` + `params_json` |
| 出图/识图 | 看速度场、评图 | `insar_view_figure` / `insar_vision_qa` |
| 报告/复现 | 论文方法/结果/zip | `insar_report` / `insar_figure_caption` / `insar_repro_bundle` |

不自造 Okada/滑动分布反演;GBIS/Kite/GMT 只走步 28 导出桥。

---

## Phase U · 右栏 InSAR 工作台(chrome + 可视化)

交互骨架不变:左会话 / 中对话 / 右 Tabs。右栏 InSAR 从 ASCII 日志升级为**工作台**(流水线轨道 + 数据集 + 图件),顶栏出现「InSAR 工作台」chip。面板只读,执行仍走对话里的 `insar_*`。

主进程改动后需**重启 Desktop 窗口**(不要 taskkill;不要停 8873)。适配器 JSON 有进程内缓存,以重启后为准。

### U0–U2 右栏三态

- [ ] 打开本仓库为信任工作区,右栏有 **InSAR** Tab;面板内三个分区:**流水线 / 数据 / 图件**
- [ ] **无 run**:流水线空壳 + mode,文案「尚无 run…insar_plan_run」;不崩、不造假数据
- [ ] **有 audited run**(realtest):步号分组 01–11 / 20–28;状态节点(完成实心 / 运行脉冲 / 失败× / 失效! / 跳过↷);摘要 chips;证据徽章 `audited`;底部当前行
- [ ] 点某一步展开详情(方法 / 阶段 / 耗时 / stale_reason),**没有**执行或重跑按钮
- [ ] **数据**分区:真实 `INSAR_DATA_DIR` / `home/datasets` 卡片 + kind 徽章;空则「未发现数据集…」
- [ ] **图件**分区:缩略网格;点击灯箱;Esc 关闭;无图则「该 run 尚无图件」
- [ ] 拔掉/停掉**临时**后端(不要杀 8873 主实例)→ 红色降级横幅 `backend unreachable` / monitor error,再恢复后 3s 内刷新
- [ ] `simulated===true` 的 run:橙色横幅「模拟结果 · 非真实数据」

### U3 chrome

- [ ] **重置右栏布局**后再开:设置 → 右侧面板 → 重置布局(或清空已存 `rightPanelOrder`)。老用户若已手动排过 Tab,**不会**自动把 InSAR 置顶,这是预期。
- [ ] 重置后:InSAR Tab **第一**且默认激活;其后是运行 / 文件
- [ ] 顶栏标题旁小号 chip「InSAR 工作台」(仅本工作区;设置页与非 InSAR 项目不出现)
- [ ] 打包版 CSP `img-src` 含 `http://127.0.0.1:*`(dev 态 Vite 跳过 CSP,网格直连 8873 即可)。安装包冒烟仍归 D5。

### 供数

右栏一次 IPC 吃 `insar-read`:`GET /api/monitor?session=@latest`(或配置的 session)→ 用解析后的真实 session + `run_id` 拉 `/api/figures`;`/api/datasets` 与 monitor 并行。轮询 3s。旧 `http-json` 仍在壳里,本仓库适配器已切到 `insar-read`。
