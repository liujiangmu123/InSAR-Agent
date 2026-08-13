"""MCP server:把 InSAR agent 暴露给 Claude Desktop / Cursor 等 MCP 宿主。

薄包装纪律:一切经 FastAPI 后端的 HTTP API(基址 INSAR_API_BASE,默认
http://127.0.0.1:8873),不直接 import driver/store —— run 的生命周期归后端
所有,MCP server 只是另一个客户端,进程边界与桌面 UI 完全一致。

入口:python -m insar_agent.mcp(stdio transport);需安装可选依赖组 [mcp]。
"""
