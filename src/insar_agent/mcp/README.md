# insar_mcp —— InSAR agent 的 MCP server

把 InSAR agent 暴露为 MCP(Model Context Protocol)工具集,让 Claude Desktop、
Cursor 等宿主里的 LLM 直接驱动 InSAR 处理:建会话 → 自然语言规划 → 触发执行 →
轮询状态 → 干预 → 导出溯源与图件。

## 架构:HTTP 薄包装

```
MCP 宿主(Claude Desktop / Cursor)
   │ stdio(JSON-RPC)
   ▼
python -m insar_agent.mcp        ← 本包:官方 mcp SDK(>=2.0,MCPServer/FastMCP 后继)
   │ HTTP(INSAR_API_BASE,默认 http://127.0.0.1:8873)
   ▼
python -m insar_agent.api.app    ← FastAPI 后端:run 生命周期的唯一属主
```

纪律:MCP server **不 import driver/store**,一切经 HTTP API —— 与桌面 UI 完全同一
接口面、同一进程边界。回合是 NDJSON 流,但 MCP 工具快进快出:`insar_plan_run`
消费到规划结束(秒级,无重计算);`insar_execute_run` 只观察一个受理窗口即断开
事件流(后端契约:断开不取消 run),随后由宿主用 `insar_run_status` 轮询。

## 安装与启动

```powershell
pip install -e ".[mcp]"              # mcp>=2.0 + httpx
python -m insar_agent.api.app        # 1) 先起后端(默认 127.0.0.1:8873)
python -m insar_agent.mcp            # 2) MCP server(stdio;宿主通常自动拉起)
```

后端没起时工具会报错并给出上面这条启动指引,不会静默失败。

## 宿主配置示例

### Claude Desktop(`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "insar": {
      "command": "E:\\path\\to\\repo\\.venv\\Scripts\\python.exe",
      "args": ["-m", "insar_agent.mcp"],
      "env": { "INSAR_API_BASE": "http://127.0.0.1:8873" }
    }
  }
}
```

### Cursor(项目内 `.cursor/mcp.json` 或全局 `~/.cursor/mcp.json`)

```json
{
  "mcpServers": {
    "insar": {
      "command": "E:\\path\\to\\repo\\.venv\\Scripts\\python.exe",
      "args": ["-m", "insar_agent.mcp"],
      "env": { "INSAR_API_BASE": "http://127.0.0.1:8873" }
    }
  }
}
```

要点:`command` 指向装了 `[mcp]` 依赖组的解释器(venv 内);后端在别的端口/机器时
改 `INSAR_API_BASE`。也可用安装后生成的 `insar-agent-mcp` 命令替代 `python -m`。

## 工具清单

| 工具 | 类型 | 作用 |
|---|---|---|
| `insar_list_sessions` | 只读 | 列出会话(可含已归档) |
| `insar_create_session` | 写 | 新建会话;可带 `scenario` 提示(quake/permafrost/landslide/stripmap_coseismic) |
| `insar_plan_run` | 写 | 自然语言发起规划回合,返回计划摘要(步骤/方法/问题/待补问题) |
| `insar_converse` | 写 | 自主循环回合:代理逐周期自主选动作(搜数据/查环境/定计划…)直到收束,返回结论 + 周期账 + 工具摘要;execute 只产生确认卡,绝不自动执行(需后端 ≥ 2026-08-14) |
| `insar_execute_run` | 写 | 触发执行,受理即返回(不阻塞等完成),附 `events_hint` |
| `insar_run_status` | 只读 | 步骤状态矩阵 + 当前阶段 + 失败摘要;`terminal=true` 即终态 |
| `insar_intervene` | 写(破坏性) | PAUSE / PLAY(RESUME)/ KILL / RESET / SKIP / SET_METHOD / SET_PARAMS;闭集校验错误由后端透传 |
| `insar_get_provenance` | 只读 | 溯源文档(超长字段截断并注明) |
| `insar_list_figures` | 只读 | 图件清单(名称/尺寸/元数据/可直接 GET 的 URL) |

## 典型对话流程

> 用户:「帮我跑一个 Ridgecrest 地震的同震形变分析」

1. `insar_create_session(name="Ridgecrest 同震")` → 得 `session_id`;
2. `insar_plan_run(session_id, "Ridgecrest 地震同震形变分析")` → 计划摘要:
   11 步清单、决策点、`simulated` 标记;`questions` 非空则先向用户要场景/区域,
   `problems` 非空则不要执行;
3. `insar_execute_run(session_id)` → `accepted=true` + `run_id`;
4. 循环 `insar_run_status(run_id)` 直到 `terminal=true`(向用户播报 `current` 进展);
   中途用户改主意 → `insar_intervene(run_id, "SET_PARAMS", target=7,
   payload={"params": {"max_temporal_baseline": 90}})`;
5. 终态 `done` → `insar_get_provenance(run_id)` 汇报方法与参数,
   `insar_list_figures(run_id)` 给出结果图链接。

### 自主循环形态(后端 ≥ 2026-08-14)

> 用户:「检查一下 Ridgecrest 的数据情况,把处理计划准备好」

不想逐工具编排时,把整个侦察/筹备目标交给 `insar_converse(session_id, text,
max_cycles=6)`:代理在一个回合内自主跑多个周期(每周期一个白名单动作,如
搜数据 → 查环境 → 定计划),`say` 正常收束、预算耗尽走 `note` 收尾。返回
`reply`(结论)、`cycles`(逐周期账目 `{n, action}`)、`tool_summaries`
(周期内工具结果摘要)与 `questions`(确认卡/提问)。纪律与边界:

- **执行不会被循环自动触发**:循环里的 execute 动作只产生确认卡(进
  `questions`,转述给用户),真正执行仍走 `insar_execute_run`;
- 消费上限 `INSAR_MCP_CONVERSE_TIMEOUT`(默认 180s):超限断开并置
  `truncated=true`(断开不取消,回合在后端继续),按 `next` 指引轮询;
- 旧后端没有 `POST /api/converse` 端点:报错自带升级指引,可先用
  `insar_plan_run` + `insar_execute_run` 分步替代。

## 环境变量

| 变量 | 含义 | 默认 |
|---|---|---|
| `INSAR_API_BASE` | 后端基址 | `http://127.0.0.1:8873` |
| `INSAR_MCP_HTTP_TIMEOUT` | 单次 HTTP 请求超时(秒) | `30` |
| `INSAR_MCP_PLAN_TIMEOUT` | 规划回合流的消费上限(秒) | `120` |
| `INSAR_MCP_ACCEPT_WINDOW` | 执行回合的受理观察窗(秒) | `3` |
| `INSAR_MCP_CONVERSE_TIMEOUT` | 自主循环回合流的消费上限(秒) | `180` |

## 错误面与边界

- 后端不可达 → 报错自带启动命令与 `INSAR_API_BASE` 指引;
- HTTP 4xx → 后端 `detail` 原样透传(未知动作/未知方法/参数越界都带候选清单);
- 引擎缺失环境下执行是**模拟模式**(`simulated=true`,产物带 SIMULATED 标记,
  证据级别封顶 runnable)—— 演示/测试用,不产生真实科学结论;
- 本 server 与后端同为本地单用户信任边界(无鉴权),不要把后端绑到非回环地址。

## 测试

```powershell
pip install -e ".[dev,mcp]"
python -m pytest tests/test_mcp_server.py -v
```

组网:后端经 `httpx.ASGITransport` 进程内直连(受理窗断流语义另起 in-loop uvicorn
高位端口验证);MCP 侧用 SDK 内存流做真实 initialize/list_tools/call_tool;
probe 打空桩 → 全链模拟执行,秒级完成,不做真实 InSAR 计算。
