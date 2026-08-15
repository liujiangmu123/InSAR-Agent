# pi-insar 交付计划 · 总索引(2026-08-15)

> 这是一套给"执行 AI"的任务书,按编号**从小到大逐文件执行**。每个 Phase 文件自包含:前置条件、逐步操作(含可直接落盘的代码骨架)、验收命令、git 提交、常见坑、完成标志。执行完一个文件、验收全绿、提交完成后,才进入下一个文件。
> 终点:一个**工业级 InSAR 全流程 Agent**——数据接入 → 11 步核心流水线(真实引擎)→ 分析(掩膜/校正/升降轨分解/统计剖面)→ 出图(论文级图集/KMZ)→ 变化检测与有纪律的预测 → 反演桥(GBIS/Kite/GMT)→ 报告与复现包,全程在 pi 对话内完成,每个数字可溯源。

## 编号规约

| 号段 | 含义 |
|---|---|
| 00-06 | 背景与总账(读,不执行) |
| 10-13 | Phase 01-04:Windows 基线 / LLM 供应商 / 真实后端 / 主题 |
| 20-24 | Phase 05-09:工具面补全(16 → 31 个工具)+ pi-journal |
| 30-35 | Phase 10-15:科学能力扩展(注册表驱动,0 个新工具) |
| 40-44 | Phase 16 收口 + 附录 A-D |

## 第一部分:背景阅读(执行前通读一遍)

| 文件 | 内容 | 状态 |
|---|---|---|
| `01-execution-rules.md` | 环境事实、八条纪律、测试形态、git 风格(所有 Phase 共用) | ☐ 已读 |
| `02-repo-map.md` | 仓库地图:什么在哪、什么能改、pi 参考源码怎么用 | ☐ 已读 |
| `03-design-analysis.md` | 设计完备性分析(交付物 1):缺口/风险/不做清单 | ☐ 已读 |
| `04-science-capability-map.md` | InSAR 全流程科学能力地图(上游→处理→下游→交付) | ☐ 已读 |
| `05-backend-capability-inventory.md` | 后端 77 端点总账 × 工具映射矩阵(16 → 31) | ☐ 已读 |
| `06-completeness-verdict.md` | 完备性判定:为什么这套计划做完 = 全流程 Agent | ☐ 已读 |

## 第二部分:执行 Phase(按行序执行,不可跳跃)

| # | 文件 | 内容 | 级 | 完成 | 提交号 |
|---|---|---|---|---|---|
| 1 | `10-phase01-windows-baseline.md` | Windows 基线:测试基建 + pi 安装 + PowerShell 启动器 | P0 | ☑ | `3002245` |
| 2 | `11-phase02-llm-provider.md` | LLM 供应商:workspace/llm.json → pi(insar-llm) | P0 | ☐ | |
| 3 | `12-phase03-real-backend.md` | 真实数据后端一键脚本 + pi 真实走查手册 | P0 | ☐ | |
| 4 | `13-phase04-insar-theme.md` | InSAR 品牌换皮(insar-dark 主题) | P1 | ☐ | |
| 5 | `20-phase05-tools-run-control.md` | 工具:resume / view_figure / run_trace(16→19) | P0 | ☐ | |
| 6 | `21-phase06-tools-analysis-query.md` | 工具:点位时序/产物/体检/路线/技能/能力闭集(19→25) | P0 | ☐ | |
| 7 | `22-phase07-tools-export-gis.md` | 工具:GIS 导出 + AI 识图质检(25→27) | P0 | ☐ | |
| 8 | `23-phase08-tools-report-delivery.md` | 工具:报告/图注/复现包/顾问(27→31) | P0 | ☐ | |
| 9 | `24-phase09-pi-journal.md` | free 模式台账外日志 pi-journal | P1 | ☐ | |
| 10 | `30-phase10-postprocess-capabilities.md` | 分析 run 机制 + 掩膜/校正占位/分解/统计(步 20-24) | P0 | ☐ | |
| 11 | `31-phase11-correction-capabilities.md` | 校正链:解缠误差/电离层/板块运动/不确定度 | P0 | ☐ | |
| 12 | `32-phase12-figure-system.md` | 出图体系:图集/网络图/相干矩阵/剖面/KMZ(步 25) | P0 | ☐ | |
| 13 | `33-phase13-analysis-and-prediction.md` | 变化检测 + 有纪律的外推预测(步 26-27) | P1 | ☐ | |
| 14 | `34-phase14-inversion-bridge.md` | 反演桥:GBIS/Kite/GMT/QGIS 标准导出(步 28) | P1 | ☐ | |
| 15 | `35-phase15-scenario-packs.md` | 多源数据接入(8 种处理器)+ 沉降/火山场景包 + 剧本 | P0 | ☐ | |
| 16 | `40-phase16-docs-and-final.md` | 文档收口 + 最终全量验收 | P0 | ☐ | |

## 第三部分:附录(随用随查)

| 文件 | 内容 |
|---|---|
| `41-appendix-final-checklist.md` | 附录 A:完成验收清单(全部通过 = 交付完成) |
| `42-appendix-pitfalls.md` | 附录 B:常见坑速查表(跨 Phase 通用) |
| `43-appendix-pi-upgrade.md` | 附录 C:pi 升级章程(0.84.2 → 未来) |
| `44-appendix-glossary.md` | 附录 D:InSAR 产物与术语速查 |

## 执行循环(每个 Phase 相同)

1. 确认前置 Phase 已完成(看本页表格勾选);
2. 通读该 Phase 文件,核对"前置事实"仍成立(文件里给了核对命令);
3. 按"文件内顺序"逐文件改/建,骨架代码可直接落盘再按需精化;
4. 跑该 Phase 的验收命令,全绿才算过(TS:`pi-insar` 下 `npx tsc --noEmit; npx vitest run`;Python:仓库根 `.venv\Scripts\python.exe -m pytest -q`);
5. 按文件末尾给出的路径与 message 提交 git;
6. 回本页勾表、填提交号,进入下一行。

## 三条速查红线(全文见 `01-execution-rules.md`)

- **禁止一切假数据**:测试跑在临时目录真实拉起的后端上;引擎缺失只允许显式标注 simulated 的诚实模拟。
- **科学步骤只经 `insar_*` 闭集**;strict 模式禁自由 bash;provenance/质量门/证据阶梯不得绕过。
- **⚠️ 重型计算**(真实 MintPy 全链等)必须先获用户明确批准,低优先级启动,一次一个。
