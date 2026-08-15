# 05 · 后端能力总账 × 工具映射矩阵

> 目的:把 Python 后端**已经实现**的每一个 HTTP 端点列全,逐条标注"LLM 现在够不够得着",从而把"要不要写新代码"和"只是没接线"彻底分开。
> 清点方式:遍历 `src/insar_agent/api/app.py` 与 22 个 `*_router.py` 的路由装饰器(2026-08-15 实测)。

## 0. 数字摘要

| 项 | 数量 |
|---|---|
| 后端路由文件 | `app.py` + 22 个 `*_router.py` |
| 已实现 HTTP 端点 | **77** |
| 扩展当前暴露的工具 | **16** |
| 已被工具覆盖的端点 | 约 **17**(22%) |
| 计划补齐后覆盖的关键端点 | 约 **45**(其余为 Web UI 专用/运维专用,有意不暴露) |

**结论:后端不是能力不足,是能力对 LLM 不可见。**

## 1. 主应用 `app.py`(27 端点)

| 方法 | 路径 | 能力 | 现有工具 | 计划 |
|---|---|---|---|---|
| GET | `/api/health` | 健康 + 引擎快照 | `insar_health` | ✅ |
| GET/POST/DELETE | `/api/sessions*` | 会话生命周期 | `insar_create_session` / `insar_list_sessions` | ✅ |
| GET | `/api/chat` | 会话消息历史 | — | 不暴露(pi 自己管对话) |
| GET | `/api/registry` | 能力注册表快照 | — | **Phase 06**(`insar_capabilities`) |
| GET | `/api/env` | 环境/引擎探测 | `insar_env_probe` | ✅ |
| GET | `/api/runs` | run 列表 | 部分(`insar_run_status`) | 不单设工具(状态查询已覆盖) |
| GET | `/api/state` | 会话完整状态 | `insar_run_status` | ✅ |
| POST | `/api/turn` | 单轮规划 | `insar_plan_run` | ✅ |
| POST | `/api/converse` | 自主对话循环 | — | 不暴露(pi 就是对话层) |
| POST | `/api/pipeline` | 执行流水线(NDJSON 流) | `insar_execute_run` | ✅ |
| POST | `/api/resume` | 后端重启后续跑 | — | **Phase 05**(`insar_resume`) |
| POST | `/api/abort` | 中止 | `insar_intervene` | ✅ |
| POST | `/api/message` | 注入用户消息 | `insar_intervene` | ✅ |
| POST | `/api/actions` | 结构化动作 | `insar_apply_change` | ✅ |
| GET | `/api/impact` | 变更影响预览 | `insar_preview_change` | ✅ |
| POST | `/api/fork` | 分叉 run | `insar_fork_run` | ✅ |
| GET | `/api/provenance` | provenance 账本 | `insar_export_provenance` | ✅ |
| GET | `/api/run.sh` | 等价裸命令脚本 | — | **Phase 08**(随复现包交付) |
| GET | `/api/methods.md` | 方法章节(确定性模板) | — | **Phase 08** |
| GET | `/api/repro-bundle` | 复现包 zip | — | **Phase 08** |
| GET | `/api/trace` | 执行轨迹 | — | **Phase 05**(`insar_run_trace`) |
| GET | `/api/logs` | 日志 | `insar_read_log` | ✅ |
| GET | `/api/figures` | 图件清单 | — | **Phase 05**(`insar_view_figure`) |
| GET | `/api/artifact-file` | 产物文件流 | — | **Phase 05**(图片内联) |
| GET | `/api/events` | 事件流(SSE) | — | 不暴露(侧栏轮询已够) |

## 2. 分析与查询类 router

| 方法 | 路径 | 能力 | 计划工具 |
|---|---|---|---|
| GET | `/api/timeseries-point` | **点位时间序列查询** —— 定量分析的核心入口 | **Phase 06** `insar_timeseries_point` |
| GET | `/api/artifacts` | 全产物清单(文件面板数据源) | **Phase 06** `insar_list_artifacts` |
| GET | `/api/doctor` | 一键体检(秒级只读深检) | **Phase 06** `insar_doctor` |
| GET | `/api/recommend` | 数据集 → 处理路线优劣对比 | **Phase 06** `insar_recommend_route` |
| GET | `/api/datasets` / `/api/datasets/{id}` | 数据集清单/详情 | 列表已有;详情 **Phase 06** |
| POST | `/api/datasets/roots` | 登记数据根目录 | Phase 06(可选) |
| GET | `/api/skills/{step_id}` | 步骤技能文档(规划/分诊知识源) | **Phase 06** `insar_read_skill` |
| POST/GET | `/api/diagnostics*` | 诊断包导出/下载 | 不单设工具(`insar_doctor` 的 details 给出诊断包指引) |

## 3. 出图与导出类 router

| 方法 | 路径 | 能力 | 计划工具 |
|---|---|---|---|
| GET | `/api/export/options` | 可导出格式与产物枚举 | **Phase 07** `insar_export_product`(list 模式) |
| GET | `/api/export` | **导出 h5 / csv / gtiff / kmz / shp** | **Phase 07** `insar_export_product` |
| POST | `/api/vision-qa` | **AI 识图质检**(让模型看图判断质量) | **Phase 07** `insar_vision_qa` |

## 4. 报告与交付类 router

| 方法 | 路径 | 能力 | 计划工具 |
|---|---|---|---|
| POST | `/api/report/draft` | 方法章节草稿(事实闭集 + 双向数值校验) | **Phase 08** `insar_report`(section=methods) |
| POST | `/api/report/results` | 结果章节草稿 | **Phase 08** `insar_report`(section=results) |
| POST/GET | `/api/report/caption` | 双语论文图注 | **Phase 08** `insar_figure_caption` |
| POST | `/api/report/full` | **完整报告 report_full.md** | **Phase 08** `insar_report`(section=full) |
| GET | `/api/advise` | 下一步建议卡(确定性规则生成) | **Phase 08** `insar_advise_next` |
| GET/POST/DELETE | `/api/memory*` | 跨会话记忆 | **Phase 08** `insar_memory` |

## 5. 运维与配置类 router(**有意不暴露给 LLM**)

| 路径 | 能力 | 不暴露的理由 |
|---|---|---|
| `/api/llm/config`、`/api/llm/models`、`/api/llm/test`、`/api/llm/usage` | LLM 密钥与模型配置 | **红线:LLM 不得读写自己的密钥配置** |
| `/api/credentials/verify` | Earthdata 凭据校验 | 涉密,人工操作 |
| `/api/setup/*` | 环境向导(写 settings) | 改环境属人类决策 |
| `/api/install/guide`、`/api/install/mark-done` | 安装助手 | 装引擎属人类决策 |
| `/api/admin/terminate`、`/api/admin/runs` | 外部终结与运维视图 | 越权风险 |
| `/api/project/*` | 项目文件夹读写 | 与 pi 自身文件工具重叠,避免双写 |
| `/api/queue*` | 队列管理 | 不暴露(运维面,写操作留人工) |
| `/api/version/check` | 版本更新检查 | 触网,无科学价值 |

## 6. 工具面终态规划(16 → 31)

保持**闭集小而正交**的纪律:能用一个工具 + 枚举参数表达的,绝不拆成多个工具。

| Phase | 新增工具 | 累计 |
|---|---|---|
| 现状 | health / create_session / list_sessions / plan_run / run_status / execute_run / preview_change / apply_change / intervene / fork_run / export_provenance / list_datasets / env_probe / read_log / set_mode / get_mode | 16 |
| 05 | `insar_resume`、`insar_view_figure`、`insar_run_trace` | 19 |
| 06 | `insar_timeseries_point`、`insar_list_artifacts`、`insar_doctor`、`insar_recommend_route`、`insar_read_skill`、`insar_capabilities` | 25 |
| 07 | `insar_export_product`、`insar_vision_qa` | 27 |
| 08 | `insar_report`、`insar_figure_caption`、`insar_repro_bundle`、`insar_advise_next`(可选 `insar_memory` → 32) | 31 |
| 10-15 | **0 个新工具** —— 科学能力扩展通过注册表声明,由 `insar_plan_run` / `insar_apply_change` / `insar_execute_run` 直接驱动 | 31 |

> Phase 10-15 不加工具,是本架构最重要的性质:**规划器 `for sid in sorted(registry)`、执行器 `ctx.registry[step_id]`、`Driver(registry=...)` 均为注册表驱动**,新增科学能力自动获得规划、五阶段执行、指纹与失效传播、provenance、质量门与侧栏显示。

## 7. 每加一个工具的固定动作(检查清单)

1. `pi-insar/src/backendClient.ts`:加类型 + 方法(超时、AbortSignal、错误语义与既有一致);
2. `pi-insar/src/tools.ts`:`typed({...})` 声明 + 加入导出数组;
3. `pi-insar/src/guard.ts`:确认严格模式白名单覆盖(`insar_*` 前缀已统一放行则无需改);
4. `pi-insar/APPEND_SYSTEM.md`:补一行工具说明(LLM 靠它知道何时用);
5. `pi-insar/skills/00-insar-agent/SKILL.md`:补用法与何时用;
6. `pi-insar/test/tools.integration.test.ts`:**更新工具总数断言** + 新增该工具的真实往返测试;
7. `pi-insar/test/skills.test.ts`:若技能文本里写了工具数,同步更新。

> 坑:工具总数是**硬编码断言**,每个 Phase 必须同步改,否则测试红。见 `42-appendix-pitfalls.md`。
