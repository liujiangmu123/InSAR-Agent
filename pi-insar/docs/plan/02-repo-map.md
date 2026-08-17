# 02 · 仓库地图(开发前必读:什么在哪、什么能改)

> 目的:让执行 AI 一眼知道"这个仓库由哪几块组成、我要改的东西在哪一块、哪一块碰不得"。

## 0. 一句话拓扑

**pi(通用 Agent 外壳,TypeScript)** ← `pi-insar` 扩展(TS,本目录)── HTTP ──→ **InSAR 可复现内核(Python FastAPI)** ── 命令 ──→ **真实引擎(MintPy / ISCE2 / SNAPHU / GDAL)** ── 产物 ──→ **provenance 账本 + 证据阶梯 + 质量门**

## 1. 目录职责表

| 路径 | 是什么 | 本计划能否改 |
|---|---|---|
| `pi-insar/src/*.ts` | pi 扩展源码:工具层、守卫、模式、侧栏、后端客户端 | ✅ 主战场 |
| `pi-insar/test/*.ts` | 扩展测试(测试即契约) | ✅ 每改必配 |
| `pi-insar/skills/00-insar-agent/SKILL.md` | 操作技能(手写,唯一入库的技能副本) | ✅ |
| `pi-insar/themes/` | pi 主题(Phase 04 新建) | ✅ |
| `pi-insar/docs/plan/` | **本套计划文档** | ✅ 执行时打勾 |
| `pi-insar/reference/pi/` | **pi 上游源码(只读参考)** | ❌ 只读,禁改、禁进构建 |
| `pi-insar/APPEND_SYSTEM.md` | 注入 pi 的系统提示(使命 + 红线 + 自由度) | ✅ |
| `pi-insar/scripts/sync-skills.mjs` | 技能校验/同步(技能根的唯一真相源) | ⚠️ 谨慎,测试依赖它 |
| `scripts/insar-pi`、`scripts/insar-pi.ps1` | 启动器(bash / PowerShell) | ✅ |
| `scripts/insar-backend-real.ps1` | 真实引擎后端启动器(Phase 03 新建) | ✅ |
| `scripts/real_ridgecrest.py` | 真实数据基准复跑入口 | ⚠️ 只读参照,勿改语义 |
| `src/insar_agent/registry/` | **能力注册表**:11 步 × 方法 × 参数 × 产物 × 质量门 | ✅ 科学扩展主战场 |
| `src/insar_agent/engines/` | 引擎薄封装:`build() → CommandPlan`,零决策 | ✅ 新增引擎构建器 |
| `src/insar_agent/planner/` | 规划:可行性收窄 + 选方法 + 落库 | ⚠️ 仅 Phase 10 的 group 过滤一处 |
| `src/insar_agent/runtime/` | 五阶段执行器、作业、探测 | ❌ 红线,不改 |
| `src/insar_agent/core/` | 指纹、失效传播、账本、store | ❌ 红线,不改 |
| `src/insar_agent/audit/` | 证据阶梯、run_ok、阈值台账 | ⚠️ 只增阈值条目,不改判定逻辑 |
| `src/insar_agent/report/` | 方法/结果/图注/全文/复现包/建议 | ⚠️ 已完备,原则上只接线 |
| `src/insar_agent/api/*_router.py` | 22 个 HTTP router(见 `05-backend-capability-inventory.md`) | ✅ 少量新增端点 |
| `desktop/pi-app-overlay/` | pi Desktop 工作台补丁镜像 | ✅ Desktop 工业化 |
| `scripts/insar-pi-desktop.ps1` | 桌面产品启动器 | ✅ |
| `workspace/realtest/` | **真实验收资产**(Ridgecrest audited run) | ❌ 只读,禁改禁删 |
| `workspace/llm.json` | LLM 配置(git 忽略,唯一密钥真源) | ❌ 只读 |
| `docs/` | 主项目设计文档(AGENT-DESIGN / AGENT-LOOP 等) | ⚠️ 只在 Phase 16 收口时补 |
| `reference/repos/*` | 其他调研仓(pi 快照已迁至 `pi-insar/reference/pi`) | ❌ 与本任务无关,不读不改 |

## 2. 工作目录纪律(重要)

`pi-insar/` 是**文档与扩展之家**,但**命令的工作目录仍是仓库根** `E:\01所有项目\06定职讲师\00insaragent`:

- `npx tsc --noEmit` / `npx vitest run` → 在 `pi-insar/` 下跑;
- `pytest`、启动后端、启动 pi → 在**仓库根**跑(计划会修改 `src/insar_agent/`、`scripts/`、`.pi/`、`tests/`);
- pi 启动时的 cwd 必须是仓库根 —— **绝不要在 `pi-insar/reference/pi/` 里启动 pi**,那里有 pi 自己的 `AGENTS.md` 与 `.pi/` 开发配置,会污染 InSAR 语境并加载 pi 自己的扩展。

## 3. 参考 pi 的用法

`pi-insar/reference/pi/` 是 pi 上游 0.84.2 的源码快照,**只用于查证**,常用位置:

| 想查什么 | 看哪里 |
|---|---|
| 扩展 API(注册工具/命令/旗标/事件/UI) | `reference/pi/packages/coding-agent/docs/extensions.md` |
| 自定义供应商(接 llm.json) | `reference/pi/packages/coding-agent/docs/custom-provider.md` |
| 主题 token 清单与 schema | `reference/pi/packages/coding-agent/docs/themes.md` |
| 设置项(defaultProvider/Model/theme) | `reference/pi/packages/coding-agent/docs/settings.md` |
| 技能加载规则 | `reference/pi/packages/coding-agent/docs/skills.md` |
| Windows 注意事项 | `reference/pi/packages/coding-agent/docs/windows.md` |
| CLI 旗标语义 | `reference/pi/packages/coding-agent/src/**/args.ts` |

纪律:**不 fork、不改、不把它接进任何构建**。`pi-insar/tsconfig.json` 只含 `src/**` 与 `test/**`,`vitest.config.ts` 只收 `test/**/*.test.ts` —— 已验证 reference 目录不会被编译或收集测试。升级 pi 时按 `43-appendix-pi-upgrade.md` 走。

## 4. 五个不可动摇的红线(贯穿全部 Phase)

1. **Python 可复现内核保留为 Python**(registry/engines/core/runtime/audit/planner/report)。
2. **禁止假数据**:一切开发与验收基于真实数据、真实引擎、真实执行,或临时目录里真实拉起的后端;不新建任何伪造的科学样本/账本/图件。
3. **科学步骤受约束**:LLM 只经 `insar_*` 闭集触达;严格模式禁自由 bash。
4. **provenance / 证据阶梯 / 质量门 / run_ok 双判定不得绕过或弱化**;新增能力必须同样进账本、进指纹、受质量门约束。
5. **不引入新密钥**;`workspace/llm.json` 是唯一真源,任何输出不得出现 key 明文。
