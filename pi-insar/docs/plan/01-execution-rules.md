# 01 · 执行须知(全局约定,所有 Phase 共用)

> 每个 Phase 文件默认引用本文件,不再重复。执行任何 Phase 前先确认本页"环境事实"逐条成立;任何一条不满足,先停下向用户报告。

## 环境事实

| 项 | 值 |
|---|---|
| 仓库根 | `E:\01所有项目\06定职讲师\00insaragent` |
| 终端 | PowerShell(命令均按 PowerShell 形态给出;分隔用 `;`) |
| Python | `E:\01所有项目\06定职讲师\00insaragent\.venv\Scripts\python.exe`(已存在) |
| Node | ≥ 22.19(`node --version` 确认) |
| MintPy 引擎 | `E:\miniforge3\envs\insar`(已存在) |
| 真实 HyP3 数据 | `E:\01所有项目\06定职讲师\InSAR-Pro\insar-pro\backend\data\real_data\RidgecrestSenDT71`(`scripts/real_ridgecrest.py` 的既有默认值,同源引用) |
| 真实验收 HOME | `E:\01所有项目\06定职讲师\00insaragent\workspace\realtest`(含 `insar.db` + `ws/`,audited run 在库) |
| LLM 配置 | `E:\01所有项目\06定职讲师\00insaragent\workspace\llm.json`(字段 `base_url`/`chat_model`/`vision_model`/`api_key`;**其值一律运行时读取,任何文件/输出不得出现 key 明文**) |
| 后端默认端口 | 8873(开发)/ 8899(vitest 集成专用) |
| pi 版本 | 0.84.2(devDependency 与全局安装永远同版本) |
| pi 参考源码 | `pi-insar/reference/pi`(只读参考,禁止修改、禁止 fork 进构建) |

## 纪律(违反即失败)

1. Python 可复现内核(`engines/`、`core/`、`runtime/`、`audit/`、`planner/`、`report/`、`registry/`)保留为 Python;本计划内除 `api/bridge_router.py` 的明确追加外不触碰后端内核。
2. **禁止一切假数据/模拟数据**:不新建任何假会话、假 run 状态样例、假影像元数据、假 provenance、伪造图件等静态资产。新测试的运行态数据一律来自"临时目录里真实拉起的后端"(既有 globalSetup 机制)或既有真实资产。引擎缺失场景只允许既有诚实模拟路径(`engines/simulate.py`,显式标注 simulated)。API 契约测试的最小请求体、确定性种子属测试基建惯例,不在禁区;禁区是"以假数据充当科学样本或验收依据"。
3. 科学步骤受约束:LLM 只经 `insar_*` 闭集触达;strict 模式禁自由 bash(guard 职责,不得削弱)。
4. provenance/证据阶梯/质量门/run_ok 双判定不得绕过或弱化;pi-journal 是台账外观察日志,与 provenance 物理分离。
5. 每个改动有对应测试;既有 vitest + pytest 保持全绿。
6. 不引入新密钥;`workspace/llm.json` 是唯一密钥真源。
7. 每步都能在 Windows 上执行与验证(路径分隔、`.venv\Scripts`、无 Unix 权限位)。
8. ⚠️重型计算(真实 MintPy 全链、`real_ridgecrest.py` 复跑)必须先向用户说明脚本、预计耗时、资源占用,**得到明确同意后**再以低优先级启动;一次只跑一个。

## 测试运行形态(每个 Phase 的验收都用它)

```powershell
cd E:\01所有项目\06定职讲师\00insaragent\pi-insar
npx tsc --noEmit
npx vitest run
```

vitest 会经 `test/backend.globalSetup.ts` 自动拉起一个绑定 8899、临时 `INSAR_HOME` 的真实后端(诚实模拟引擎路径,`INSAR_ALLOW_SIMULATED=1`,产物真实落盘、显式标注 simulated)——这是既有机制,所有集成断言都跑在这个活后端上,不使用任何静态假数据。

后端(涉及 Python 改动的 Phase):

```powershell
cd E:\01所有项目\06定职讲师\00insaragent
.venv\Scripts\python.exe -m pytest -q
```

## git 提交风格(与仓库既有一致)

`feat(pi-insar): …`、`feat(pi-bridge): …`、`fix(pi-insar): …`、`docs(pi-insar): …`、`feat(scripts): …`;中文描述,一句话说清"为什么"。每个 Phase 文件末尾给出确切的 `git add` 路径与 commit message,照抄执行。
