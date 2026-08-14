# 差距分析与路线图 — 2026-08-14「pi 式自主循环」波次收官

> 面向项目所有者的战略文档,由验证波次(C7)收官时撰写。
> 素材:`docs/LOOP-CONTRACT.md`(波次台账)、`docs/AGENT-LOOP.md`(正式设计)、
> `reference/PI_FRAMEWORK_ANALYSIS.md`(pi 对标与 absorb-E 系列决议)、本波 git 改动面
> (30 个既有文件修改 + 21 个新文件:9 个新 Python 测试、2 个 js 测试、net/ 四文件、
> subtasks、agentloop 三件套与两份文档)。
> 写实纪律:已实现的注明守护测试文件;未实现的如实写「没做」,不含糊其辞。

## ① 本波已交付(对照 pi 能力集逐项)

全量测试绿(验证波次统一跑,含集成套件 `tests/test_agent_loop_integration.py`)。

| 能力 | 一句话现状 | 守护测试 |
|---|---|---|
| 多轮自主循环 | `converse_loop` 确定性驱动器:单周期单决策、动作白名单闭集 10 项、终止条件闭集 7 条(say / execute 确认卡 / 周期上限 / 取消 / 降级 / 双熔断),brain 可拔除时整回合退回 `turn()` 零回归 | `tests/test_agent_loop.py`、`tests/test_agent_loop_integration.py` |
| 联网搜索(ASF+网页) | `net/` 纯 stdlib:`asf_search`(ASF 归档匿名检索)+ `web_search`(免密钥端点,设 `INSAR_TAVILY_KEY` 走 Tavily)+ `search_fanout` 部分失败不整体失败,错误闭集 `NetError` | `tests/test_net_search.py`(离线 MockServer) |
| 子任务并行池 | `gather_limited` 信号量限并发(默认 3),单任务异常/超时不炸整批、不留孤儿协程;执行类任务不进池 | `tests/test_subtasks.py` |
| 运行中插话 steering | 循环运行中用户消息以 steer 入干预队列,周期边界消费并入回合目标,发 intervention 回执 | `tests/test_agent_loop.py`(`test_steering_*` 组) |
| 会话级取消 | 复用 `POST /api/abort` 控制位;无 run 会话置会话级一次性 token(404→202 语义扩展),周期边界响应,已完成周期结果保留 | `tests/test_api_loop.py`(`test_abort_*` 组)、`tests/test_agent_loop.py`(`test_cancel_*` 组) |
| 预算与熔断 | 周期上限 1..12(默认 6);同签名提案连续 3 次熔断、连续失败 2 次未换策略熔断;周期摘要 ≤300 字硬截,日志绝不进上下文 | `tests/test_agent_loop.py`(熔断/预算组) |
| MCP 工具 | `insar_converse(session_id, text, max_cycles)` 消费 NDJSON 到回合结束,返回 say/note + 周期账;旧后端归一化 `BackendError` 带升级指引 | `tests/test_mcp_server.py` |
| 前端可视化 | `agentloop.js` 自建 SSE 订阅渲染进度条(第 n/N 步·当前动作)、停止按钮、收卡转「工作记录」;`app.js` 零改动消费(未知事件静默丢弃) | `prototype/agentloop.check.mjs`(37 项具名断言全过)、`tests/js/converse_fallback.test.mjs`(旧后端回退) |
| 配置开关 | llm.json 两键 `agent_loop`/`agent_max_cycles`,损坏回默认绝不抛;设置面板开关;关闭后 `/api/converse` 直接退单步 `turn()` | `tests/test_llm_agent_config.py`、`tests/test_api_loop.py`(`test_converse_agent_loop_disabled_delegates_to_turn`) |

支撑层同样有独立守护:brain 第五职责 `cycle`(闭集校验/坏形状/越界安全收束,
`tests/test_brain_cycle.py`)、`provider.chat`(截断整体拒绝/route_pin 钉路由/用量
kind=agent,`tests/test_provider_chat.py`)、API 鲁棒性(预算越界/非法 JSON/并发回合/
取消风暴,`tests/test_api_loop_robustness.py`)。

## ② 与 pi 的能力差距(如实清单:pi 有、我们没有)

| # | 差距 | 现状实话 | 是否值得做 / 建议 Phase |
|---|---|---|---|
| 1 | 流式 token 输出 | `provider.chat` 非流式整段返回;识图函数的 SSE 聚合已抽成 `_iter_sse` 留位,前端只按周期粒度渲染 | **值得做,P1**。用户体感最直接的差距;复用 `_iter_sse`,改动面集中在 provider + 前端打字机 |
| 2 | 真子代理进程隔离 | 我们是**回合内 asyncio 子任务池**(`gather_limited`),共享进程与上下文,不是 pi 式「spawn 独立进程 + 独立上下文 + JSONL 事件流」的子代理 | **值得做但不急,P2**。等并行 run / run fork(absorb-E5)需求成熟一起做;重型计算的进程隔离已由 WSL 执行器承担 |
| 3 | 扩展/技能热加载(pi 的 extensions/jiti) | 场景技能包已有**静态形态**(`registry/scenario_packs/` 四包:SKILL.md + overrides.yaml);运行时热加载没做 | **热加载明确不做**(`PI_FRAMEWORK_ANALYSIS.md §5.3`:科学系统能力集应锁定);**技能内容接入循环上下文值得做,P2**(absorb-E7) |
| 4 | AgentHarness 级「效果三明治」崩溃恢复 | 执行层有等价物(五阶段执行器 PREPARED→VERIFIED、意图/结算纪律);**循环本身无断点续跑**——进程中断则回合作废,周期摘要不落盘恢复 | **值得做,P1**。周期摘要与循环状态落 store,重启后回合可续,是执行器纪律的循环版 |
| 5 | 跨回合循环记忆 | 周期摘要只活在单个回合内;跨回合只有既有用户记忆(`brain/memory.py`),循环结论不自动沉淀 | **有限形态值得做,P2**。先做「回合收束时把循环结论写入会话记忆」,不抄 pi 的全量对话树(§5.3:对话≠状态真相源) |
| 6 | 300+ 模型多供应商矩阵 | 我们是 OpenAI 兼容协议 + 单跳 fallback + route_pin 钉路由 | **不值得追平,不设 Phase**。OpenAI 兼容中转已覆盖主流模型;按实际需求逐个加适配,记录为「明确不做」的决定 |

## ③ 已知未修事项(评审留档,如实交底)

1. **agentloop.js 工具事件不按来源过滤**:`tool.start`/`tool.end` 不带回合来源标记,
   旁观视角下执行回合(流水线)的工具事件会混入循环卡、并 disarm 静默窗。
   修法方向:按 `loop{n}` 的 tool_id 前缀过滤或事件补来源字段(需两端同步,属契约变更)。
2. **steering 双消费竞态**:循环周期边界与执行回合检查点可能争抢同一条 steer 行;
   消费即删除,**幂等无害**(只影响哪一方收到,不会双重生效)。留档不修。
3. **纯静态页无法触发聊天 mock 回合**:设计使然——mock 剧本织在 runConverse 通道
   (见 LOOP-CONTRACT §9),无后端的静态打开需用 `?agentloopmock=1` 演示通道。不修。
4. **requirements.txt 与 pyproject 镜像存量缺口**:pyproject 的 `[mcp]` 组(mcp>=2.0)
   与 dev 组的 hypothesis/ruff/pip-audit 未镜像进 requirements.txt。存量问题,
   本波零新增第三方依赖。低优先级补齐。
5. **存量 ruff**:评审时点记 48 条;C7 复核(src+tests,E/F/W/I 规则集)为 47 条,
   其中 E501 行宽 30 条,按 pyproject 注释既定策略「告警清单管理,不为清零改写既有代码」。
6. **桌面 dist 重建后 abort 探针可收紧**:`tests/test_desktop_matrix.py` 当前对旧构建
   的 dist 按宽口径放行,dist 重建后应收紧为 202 单口径(成因见 LOOP-CONTRACT §14)。

## ④ 路线图建议

**P1(下一波,直接兑现用户价值)**

- **真实 LLM 密钥下的端到端实测与金标回归**:本波循环行为全部由 mock/打桩守护,
  缺真活体验证;建议录制若干「金标准回合」(真实密钥跑通后固化事件序列)做回归基线。
- **流式 token 输出**(差距 #1):provider 流式化 + say 增量渲染。
- **循环断点续跑**(差距 #4):循环状态落 store,进程重启可续,补齐与执行层对等的恢复纪律。

**P2(能力纵深)**

- **子代理进程隔离与并行 run**(差距 #2):独立进程 + JSONL 事件流,配合 run fork(absorb-E5)。
- **技能系统接入循环**(差距 #3):scenario_packs 的 SKILL.md 按需注入 cycle 上下文,不做热加载。
- **web 检索结果入数据资产台账**:当前检索命中只进周期摘要(AGENT-LOOP §13);
  打通「检索命中 → 用户确认 → 登记候选数据集 → 流水线第 1 步下载」的可审计链路。

**P3(桌面与打磨)**

- Tauri 桌面壳的循环停止托盘入口、循环进行中的系统通知;
- 本地小模型按路由自动关断循环(现为手动开关 + 文档建议);
- 桌面 dist 重建与 abort 探针收紧(③-6)。

## ⑤ 风险与红线重申(不因循环而松动)

1. **重型计算审批**:`execute` 动作永远只产生确认卡,循环绝不自启流水线;
   「计划不会自动开始」的用户承诺不变;开发与验证期不启动重型计算(工作区规则)。
2. **数据主权**:联网只出查询词与检索参数,绝不上传本地数据;凭据不进日志不进 LLM;
   检索结果不进 provenance 证据链——数据获取唯一可审计路径仍是流水线第 1 步。
3. **LLM 只做闭集选择**:零命令零路径、动作白名单闭集、越界即拦(brain 侧安全收束)、
   截断整体拒绝、日志只以 ≤300 字摘要回灌;BFCL 低分依据保留为本地小模型关断开关条件,
   循环的可靠性由确定性驱动器而非模型自觉保证。
