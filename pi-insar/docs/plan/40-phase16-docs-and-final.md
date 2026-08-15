# Phase 16 · 文档收口与最终验收(P0)

> **前置**:`10`~`35`(Phase 01-15)全部完成并提交。
> **目标**:README 反映最终形态;从零走一遍完整验收清单(`41-appendix-final-checklist.md`);分支干净。
> **涉及文件**:修改 `pi-insar/README.md`、仓库根 `README.md`。

## 步骤 16.1 修改 `E:\01所有项目\06定职讲师\00insaragent\pi-insar\README.md`

结构调整为(保留既有内容,补齐新增面):

- **Quickstart(Windows 优先)**:安装 pi(`npm i -g @earendil-works/pi-coding-agent@0.84.2`)→ 起真实后端(`pwsh scripts/insar-backend-real.ps1`)→ 起 pi(`pwsh scripts/insar-pi.ps1`)。
- **LLM 供应商**:insar-llm 来自 `workspace/llm.json`;`INSAR_LLM_CONFIG` 可覆盖;`.pi/settings.json` 默认选中;无 llm.json 时 pi 照常启动。
- **主题**:insar-dark 由启动器 `--theme` 提供、settings 选中;如何换回内置 dark(`/settings`)。
- **工具面**:31 个 `insar_*` 工具,按五组列表:会话与规划执行(create/list/plan/execute/resume/run_status/run_trace)、查询分析(timeseries_point/list_artifacts/capabilities/doctor/recommend_route/read_skill)、图件与质检(view_figure/vision_qa/figure_caption)、导出交付(export_product/report/repro_bundle/export_provenance)、环境与顾问(mode/advise_next/journal 等)。逐一一句话说明。
- **科学能力**:核心 11 步流水线 + 分析步 20-28(掩膜/校正/分解/统计/出图/变化检测/预测/反演桥)+ 6 个场景包;一句话重申模拟纪律(引擎缺失 → 显式 simulated,绝不冒充真实)。
- **pi-journal**:台账外定位一段(观察日志,非 provenance)。
- **测试**:vitest / pytest 命令与 8899 集成后端说明。
- **Windows 环境变量表**:INSAR_API_BASE、INSAR_TEST_PYTHON/CWD/PORT、INSAR_LLM_CONFIG、INSAR_PI_SKIP_HEALTH、INSAR_ENGINE_PREFIX、INSAR_ALLOW_SIMULATED。

## 步骤 16.2 修改 `E:\01所有项目\06定职讲师\00insaragent\README.md`(仓库根)

在既有 pi-insar 相关小节(如有)补:Windows 入口、`docs/PI-REAL-SESSION.md` 链接、一句"分析 run(升降轨分解/预测/反演桥)见 pi-insar/README";无此章节则加四行简介(pi 外壳 + Python 内核 + 分析链 + 真实走查入口)。

## 步骤 16.3 执行最终验收

打开 `41-appendix-final-checklist.md`,从第 1 条顺序执行到最后一条,全部通过即开发完成。其中标 ⚠️重型 的条目,须用户明确批准后执行。

## git 提交

```powershell
git add pi-insar/README.md README.md
git commit -m "docs(pi-insar): Windows quickstart、31 工具面、科学能力全景与真实走查文档收口"
```

## 完成标志

- [ ] 两个 README 反映最终形态(31 工具、16+9 能力步、6 场景包如实列出)
- [ ] `41-appendix-final-checklist.md` 全部通过(重型条目经用户批准)
- [ ] `git status` 干净,`git log --oneline -30` 可见 Phase 01~16 完整提交序列
- [ ] `00-README.md` 进度表全部打勾并填提交号

→ 收尾:向用户汇报完成情况与验收证据(测试输出、真实走查截图)。
