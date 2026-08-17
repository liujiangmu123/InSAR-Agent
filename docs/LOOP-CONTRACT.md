# 自主循环(converse loop)开发契约 — 2026-08-14 波次

> 状态:开发期互锁契约。11 个并行开发单元(B1~B11)按本文件对齐接口,先按契约开发、
> 用 monkeypatch/假对象验证,集成验证在验证波次统一做。
> 上线后的正式设计文档是 docs/AGENT-LOOP.md(B7 产出),本文件保留为波次台账。
> 依据:reference/PI_FRAMEWORK_ANALYSIS.md(absorb-E 系列)+ 2026-08-14 十份勘察报告。

## 0. 红线(不可破,依据 docs/AGENT-DESIGN.md / docs/DESIGN.md)

1. **单周期单决策,回合可多周期**:LLM 每周期只输出一个白名单动作(闭集)或 say 收束;
   周期间由确定性循环驱动器衔接。「绝不串多步」修订为本条(文档修订由 B7 落实)。
2. **brain 可拔除**:`Brain(None)`/无路由时回合退化为既有单步规则路径,
   既有守护测试(tests/test_converse.py 降级组等)必须原样通过。
3. **LLM 零命令零路径**;**审批不被循环绕过**:execute 动作只产生确认卡(ask)并收束回合,
   绝不自启流水线;「计划不会自动开始」的用户承诺不变。
4. 每周期入事件账本;预算耗尽/熔断/取消走 note 收尾;say = 正常终止。
5. 日志绝不进 LLM 上下文(只给摘要);联网只出查询词,不出数据;凭据不进日志不进 LLM。
6. 执行类动作永远走五阶段执行器与运行锁,循环不得未经用户批准启动重型计算。

## 1. 事件契约(动作闭集语义锁死,不可改)

- 每周期一条:`{"t":"agent.cycle","n":i,"max":N,"action":"<action>"}`,n 从 1 递增;
  必须经 driver 的 `_emit`(NDJSON 回合流 + SSE 总线双通道)。
- 周期内工具执行复用既有 `tool.start`/`tool.end`(id 配对、exit、summary)。
- `say` = 正常终止;`note` = 预算耗尽/中断/降级收尾。
- 动作闭集(与 `CYCLE_ACTION_TYPES` 对齐):
  `search_data inspect_file check_env list_data status plan execute set_params set_method thinking`
- `loop/events.py` 新增工厂 `agent_cycle(n: int, max_cycles: int, action: str) -> dict`(B1),
  并在 tests/test_e2e_contract.py 的事件类型注册表登记 "agent.cycle"(B1,参照 step.stage 先例)。

## 2. Provider 新原语(B2 — 只改 src/insar_agent/brain/provider.py + 新测试)

```python
@dataclass
class ChatOutcome:
    content: str              # assistant 文本(可能为空)
    tool_calls: list[dict]    # OpenAI 形状 [{"id","type","function":{"name","arguments"}}];无则 []
    finish_reason: str
    route_index: int          # 实际使用的路由下标(供调用方钉死)

def chat(self, messages: list[dict], *, tools: list[dict] | None = None,
         max_tokens: int = 2048, json_only: bool = True,
         route_pin: int | None = None) -> ChatOutcome
```

- `complete_json` 等既有原语一律不动,既有测试不动。
- messages 为 OpenAI 格式透传(role: system/user/assistant/tool)。
- `json_only=True` → payload 带 `response_format={"type":"json_object"}` + `temperature=0`。
- `finish_reason == "length"` → raise `BrainTruncated`(含 tool_calls 的截断同样整体拒绝,absorb-E9)。
- `route_pin=None`:沿用单跳 fallback 语义选路;`route_pin=i`:钉死路由 i,失败不切换直接抛。
- 用量上报 `kind="agent"`,沿用 `set_usage_sink` 通道;超时沿用 60s 默认。
- 把识图函数里的 SSE 聚合逻辑(provider.py:203-230)抽成模块级 `_iter_sse`,识图函数改为复用
  (行为不变,测试守住);本波 chat 非流式,流式留待后续。

## 3. Brain 第五职责 cycle(B3 — 只改 src/insar_agent/brain/facade.py + 新测试)

```python
@dataclass
class CycleResult:
    action: dict | None   # {"type": <闭集>, ...其余字段同 converse 动作} 或 None
    say: str | None       # 面向用户的话(收束或过渡语)
    done: bool            # True = LLM 选择收束(有 say 无 action)
    source: str           # "llm" | "degraded"

def cycle(self, *, goal: str, cycles_summary: list[str], state_summary: str,
          registry, route_pin: int | None = None) -> CycleResult
```

- 内部组 messages:system = 静态循环提示词(动作闭集+纪律,声明式,<2KB);
  user = 回合目标 + 系统状态摘要 + 逐周期结构化摘要(每条 ≤300 字)。调 `provider.chat(json_only=True)`。
- 期望 LLM 输出 JSON:`{"say": "..."}`(收束)或 `{"action": {"type": "...", ...}, "say": "..."}`。
- 动作校验复用 `_validate_converse_action` 同款纪律:越界动作丢动作留 say;
  缺 say 的坏形状 → `BrainUnavailable`;`enabled` 闸门与 converse 相同。
- 不带对话历史,只带本回合周期摘要(决策请求不带历史的修订版,红线 §0.1)。

## 4. 循环核心(B1 — 只改 src/insar_agent/loop/driver.py、loop/events.py、loop/budget.py,
   新测试 tests/test_agent_loop.py;登记 tests/test_e2e_contract.py 事件注册表)

```python
async def converse_loop(self, session_id: str, text: str, *, max_cycles: int = 6) -> AsyncIterator[dict]
# 2026-08-14 收口注记:首参与 turn 同形(session_id);早先版本的 converse_loop(text)
# 是省写,曾导致 B5/B9 打桩签名错位(B11 实测 TypeError),以本行为准。
```

- `brain.enabled` 为假,或 brain 缺 `cycle` 属性 → 直接委托既有 `turn(text)`(逐事件转发,零回归)。
- 每周期顺序:
  1. 取消检查(既有 request_cancel 控制位)→ 置位则 note 收尾;
  2. 消费 steering(store.pending_actions 中 deliver_as='steer' 的 USER_MESSAGE → 并入 goal);
  3. `brain.cycle(...)` 经 `asyncio.to_thread` 调用(不阻塞事件循环;store 操作留在主线程);
  4. `yield _emit(agent_cycle(n, max_cycles, action_type))`;
  5. 动作映射执行:复用 turn/_apply_converse 既有处理器语义,tool.start/tool.end 包裹,
     结果压缩为 ≤300 字摘要追加 cycles_summary(日志不整段进 LLM);
  6. say 收束 → yield say,break。
- 终止条件闭集:say | max_cycles 耗尽(note)| 取消(note)| BrainUnavailable(note+当场降级收束)|
  同动作签名连续 3 次熔断(note)| 动作失败且 LLM 连续 2 周期未换策略(note,unresolved_failure 如实声明)。
- 动作特例:
  - `execute` → 产生既有确认卡事件(ask)+ say 收束,绝不自启流水线;
  - `search_data` → 惰性 import `insar_agent.net`(B4)与 `insar_agent.loop.subtasks`(B8),
    经 `gather_limited` 并行扇出(本地目录盘点 + ASF 检索 + 可选 web),模块缺失时优雅降级为
    仅本地盘点(防御性 import 参照 driver._memory_snippets 先例);
  - `set_params`/`set_method`/`plan`/`status`/`check_env`/`list_data`/`inspect_file` 沿用既有语义。
- 路由钉死:首周期 cycle 返回后取 route_index,后续周期传 route_pin。
- usage_context(brain/usage.py 既有线头)在回合外围绑定 session_id。
- 既有 `turn()` 的行为与签名一个字节都不许变(守护测试锁死)。

## 5. net 层(B4 — 新建 src/insar_agent/net/__init__.py、asf_search.py、websearch.py,
   新测试 tests/test_net_search.py;纯 stdlib urllib,不新增第三方依赖)

```python
def asf_search(*, platform: str = "SENTINEL-1", intersects_wkt: str | None = None,
               start: str | None = None, end: str | None = None,
               beam_mode: str | None = None, processing_level: str = "SLC",
               max_results: int = 50, timeout: float = 20.0) -> list[dict]
# GET https://api.daac.asf.alaska.edu/services/search/param  output=jsonlite,匿名可用

def web_search(query: str, *, k: int = 5, timeout: float = 15.0) -> list[dict]
# 返回 [{"title","url","snippet"}];默认 DuckDuckGo html 端点(无 key);
# 环境变量 INSAR_TAVILY_KEY 存在则优先 Tavily API(POST JSON)

def search_fanout(*, region_wkt=None, start=None, end=None, query=None,
                  include_web=False, timeout=20.0) -> dict
# {"asf": [...] | {"error": ...}, "web": [...] | {"error": ...}} 部分失败不整体失败
```

- 错误闭集 `NetError(kind="timeout"|"http"|"parse"|"offline", detail=...)`,绝不裸抛 urllib 异常;
- base URL 常量可用环境变量覆盖(INSAR_ASF_SEARCH_BASE / INSAR_WEBSEARCH_BASE)——测试指到本地
  http.server mock(参照 tests/test_provider_http.py 的 MockServer 模式),测试全程离线;
- 联网纪律:只发查询词;UA 标识 insar-agent;单次响应体上限 2MB,超限截断并标注。

## 6. 子任务池(B8 — 新建 src/insar_agent/loop/subtasks.py + 新测试 tests/test_subtasks.py)

```python
@dataclass
class SubResult:
    name: str
    ok: bool
    value: object | None
    error: str | None
    elapsed_ms: int

async def gather_limited(named_tasks: dict[str, Coroutine], *, limit: int = 3,
                         timeout: float | None = None) -> dict[str, SubResult]
```

- 并发上限信号量;单个异常/超时不炸整批,如实进 SubResult.error;
- 同步阻塞函数由调用方自行 `asyncio.to_thread` 包成协程再入池;
- 执行类(io=heavy)任务不进此池(运行锁语义不变),池只服务只读检索/探测类任务。

## 7. API(B5 — 只改 src/insar_agent/api/app.py + 新测试 tests/test_api_loop.py)

- `POST /api/converse` body `{"session": str, "text": str, "max_cycles": int = 6}` → NDJSON。
  实现:`return ndjson(driver_of(body.session).converse_loop(text, max_cycles=...))`,
  放 app.py「回合(流式)」区(/api/turn 旁)。
- 校验全部挡在开流前:session 合法性同 /api/turn;`1 <= max_cycles <= 12` 越界 400(带候选说明);
  max_cycles 缺省取 llm_config 的 agent_max_cycles(惰性 import,B10;缺失时用 6)。
- 停止:复用既有 `POST /api/abort`(控制位,循环在周期边界响应),不新增端点。
- 顺手修两个已知坑(勘察确认):
  a. app.py 挂载 skills_router(FEATURES 缺口 1:前端 skillpanel 消费 GET /api/skills 实测 404);
  b. driver_of 检测 llm.json 路由指纹变化 → **原地替换 driver.brain**(不重建 Driver,
     不动 EventBus,SSE 订阅者无感)。
- 测试策略:driver.converse_loop 可能与本单元并行开发中——测试用 monkeypatch 换成契约形状的
  假异步生成器,验证端点协议(开流前 400 / NDJSON 事件透传 / abort 控制位 / 会话隔离);
  与 tests/test_api.py 的密封 probe + _stream_events 模式一致。

## 8. 配置面(B10 — 只改 src/insar_agent/brain/llm_config.py、src/insar_agent/api/llm_router.py + 对应测试)

```python
def agent_loop_settings(home: Path) -> dict
# {"enabled": bool(默认 True), "max_cycles": int(1..12,默认 6)}
# llm.json 新可选键:agent_loop / agent_max_cycles;损坏/越界回默认,绝不抛
```

- llm_router GET /config 回显(掩码规则不变),POST /config 校验合并写入;
- 设置面板加「自主循环」开关与周期上限输入(零 npm,风格随既有面板);
- 前端 js 若与 B6 所有权冲突(index.html 归 B6),只改自己的面板 js 文件,不碰 index.html。

## 9. 前端(B6 — 已作废)

旧网页 UI(`prototype/`)已删除。产品界面是 pi Desktop(右栏 InSAR 工作台只读,
操作只走对话里的 `insar_*` 工具)。事件契约仍由 `tests/test_e2e_contract.py` 锁定。

## 10. MCP(B9 — 只改 src/insar_agent/mcp/server.py、mcp/backend.py + tests/test_mcp_server.py 增补)

- 新工具 `insar_converse(session_id, text, max_cycles=6)`:POST /api/converse 消费 NDJSON 到回合
  结束(deadline 环境变量 INSAR_MCP_CONVERSE_TIMEOUT,默认 180s),返回 say/note 文本 + 周期账
  (n/action 列表);后端无该端点(旧版本)→ 归一化 BackendError 带升级指引。
- 测试:httpx.ASGITransport 直连 create_app,converse_loop 打桩(同 B5 策略)。

## 11. 文档(B7)与鲁棒性测试(B11)

- B7 只改:docs/AGENT-LOOP.md、docs/DESIGN.md、docs/AGENT-DESIGN.md、README.md;不碰本契约文件。
- B11 只新建:tests/test_api_loop_robustness.py(+可选 hypothesis):
  预算越界/非法 JSON/并发回合/会话隔离/取消风暴/事件形状不变量,converse_loop 打桩 + 真实端点两层。

## 12. 文件所有权总表(禁止越界修改;冲突=波次事故)

| 单元 | 独占文件 |
|---|---|
| B1 | loop/driver.py, loop/events.py, loop/budget.py, tests/test_agent_loop.py, tests/test_e2e_contract.py(仅事件注册处) |
| B2 | brain/provider.py, tests/test_provider_chat.py |
| B3 | brain/facade.py, tests/test_brain_cycle.py |
| B4 | net/(新建), tests/test_net_search.py |
| B5 | api/app.py, tests/test_api_loop.py |
| B6 | (已作废)旧网页 UI 已删除 |
| B7 | docs/AGENT-LOOP.md, docs/DESIGN.md, docs/AGENT-DESIGN.md, README.md |
| B8 | loop/subtasks.py(新建), tests/test_subtasks.py |
| B9 | mcp/server.py, mcp/backend.py, mcp/README.md, tests/test_mcp_server.py |
| B10 | brain/llm_config.py, api/llm_router.py, 对应测试文件 |
| B11 | tests/test_api_loop_robustness.py(新建) |
| (前置契约) | CYCLE_ACTION_TYPES / LOOP-CONTRACT §1 —— 动作闭集与事件语义 |
| (波次协调) | docs/LOOP-CONTRACT.md 本体(各单元不得改;台账类增补由验证波次统一落笔) |

(src/insar_agent/ 前缀省略。)

## 13. 公共纪律(全体)

- 注释/docstring 一律中文,UTF-8;Python 语法 ≤3.11(CI 钉 3.11,本地 .venv 是 3.14);
- ruff E/F/W/I、行宽 100;测试自动发现零登记;时序敏感断言必打 @pytest.mark.timing 并乘
  conftest.TIME_FACTOR;测试必须密封(monkeypatch INSAR_HOME / 空 probe / 断网);
- 禁新增 npm 依赖;禁新增 Python 第三方依赖(本波全部 stdlib 可达);
- 只跑自己的测试文件(.venv\Scripts\python.exe -m pytest tests/test_xxx.py -q,
  $env:INSAR_TEST_TIME_FACTOR="3");全量套件由验证波次统一跑;
- 不 git commit(波次统一处理);不启动重型计算;不动 8873 生产实例。

## 14. 集成期适配台账(验证波次落笔)

集成中既有测试因本波语义变更而调整过的地方,如实留档(成因 B5 契约 + 验证波 C1 收口):

- **tests/test_api_robustness.py**:「无 run 会话 abort」断言由 404 调整为 202。
  B5/C1 把 `/api/abort` 语义扩展为「会话无任何 run 时置会话级一次性取消 token,仍回 202」
  ——converse 回合不产生 run,循环必须可停(AGENT-LOOP.md §8);
  显式 `run_id` 不存在时的 404 口径不变。
- **tests/test_desktop_matrix.py**:同一语义变更的桌面矩阵 abort 探针同步适配
  (预期 404 → 202)。当前桌面 dist 为旧构建,探针按宽口径放行;
  dist 重建后可收紧(见 docs/GAP-ANALYSIS-2026-08-14.md ③)。
