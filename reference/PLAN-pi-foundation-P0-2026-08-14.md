# P0 地基探活结果 + 精化架构(方案 B,已按你的确认定稿)

> 2026-08-14 · 分支 `cursor/insar-agent-on-pi-research-c983` · 承接 `PLAN-pi-foundation-2026-08-14.md`
> 你已确认:Q1=方案 B(依赖 pi 不 fork)、Q2=Python 后端、Q3=pi 聊天 UI + 侧边栏监控流程(放弃旧 Web UI)、
> Q4=渐进式自由(我推荐)。本文件记录 P0 实测结论与据此精化的架构。

## 1. P0 实测(本 VM 已跑通)

- Node:默认镜像 v22.14.0 < pi 要求的 22.19.0 → 用 nvm 装了 **v22.23.2**(npm 10.9.8)。
- pi:`npm i -g @earendil-works/pi-coding-agent` → **0.84.2 安装并运行成功**(`pi --version` / `pi --help` 正常)。
- pi 本地自带**全套文档 + 80+ 示例扩展**(`$(npm root -g)/@earendil-works/pi-coding-agent/{docs,examples}`),
  直接作为开发参照。
- pi **默认 provider = google**(Gemini);支持 `--provider/--model/--api-key` 与各家环境变量。
- **可行性确认**(读示例扩展源码):
  - `pi.registerTool` / `pi.setActiveTools` / `pi.getAllTools` + CLI `--tools/--exclude-tools/--no-builtin-tools`
    → InSAR 高层工具层 + 严格模式的工具收窄都能做(`examples/extensions/tools.ts`)。
  - `ctx.ui.setWidget("widget-above"|"belowEditor", [...])` + `ctx.ui.setStatus(...)`
    → **侧边栏"被监控的流程"面板 + 页脚证据级/当前步**能直接在 pi 原生 TUI 里渲染
    (`examples/extensions/widget-placement.ts`、`status-line.ts`)。
  - 钩子 `before_tool`(fail-closed 可拦截/终止)+ 事件总线 + `file-trigger`
    → 渐进式自由的严格模式闸门 + 顺带 provenance 记账都有现成范式
    (`permission-gate.ts`、`event-bus.ts`、`file-trigger.ts`、`git-checkpoint.ts`)。
  - `--skill`(SKILL.md 渐进披露)、`--append-system-prompt`、`--theme` → InSAR 技能/提示词/换肤齐备。

**结论:你要的"pi 聊天 + 侧边栏监控流程 + InSAR 技能/工具 + 可选严格可复现"在 pi 0.84.2 上全部可落地,无需 fork。**

## 2. 精化架构(关键:Q2 与 Q3 的调和)

你 Q2 选了"pi 作 RPC 子进程 + Python 后端",Q3 又要"pi 原生聊天 UI"。这两者其实指向**相反的进程拓扑**:
- 若要 **pi 原生 TUI 作界面**,pi 必须是**顶层进程**(用户直接跑 pi);
- "pi 作 RPC 子进程"只在"我们自己写 UI、Python 当宿主"时才成立 —— 而你已放弃自写 UI。

**我据此(你授权我思考最合适的)选定拓扑 = pi 在顶层,InSAR 能力作 pi 扩展调用 Python 后端:**

```
用户  ─▶  pi(TUI 聊天,顶层进程)
             │  加载:InSAR 扩展(TS) + 11×SKILL.md + APPEND_SYSTEM.md + 主题
             │  扩展在 TUI 里渲染:侧边栏"被监控的流程"面板 + 页脚证据级
             ▼
        InSAR pi 扩展(TS,pi.registerTool)
             │  工具调用 → HTTP →
             ▼
        现有 Python FastAPI 后端(src/insar_agent/api,**原样保留,零改**)
             │  /api/turn /api/pipeline /api/runs /api/state /api/provenance /api/events(SSE)
             ▼
        可复现科学内核(core/runtime/audit/engines/… + 1543 测试,原样保留)
```

要点:
- **"Python 后端"就是你现有的 FastAPI 应用** —— 它本来就是 InSAR 引擎 + API + SSE 事件源。
  pi 扩展的工具与侧边栏直接调它的现成端点。可复现内核**一行不用改**,只是把"脸"换成 pi 聊天。
- 放弃的只有 `prototype/` 那套 Web UI(正是你说"做错了"的部分),后端引擎全部留用。
- 侧边栏面板订阅 `/api/events`(SSE)+ 轮询 `/api/runs`,把 11 步×五阶段、当前步、证据级实时画进 pi TUI。

## 3. 渐进式自由(Q4)的落地方式

- **默认(自由)**:pi 有真实 agency —— 可写/跑 Python(bash),由 InSAR 技能 + 系统提示词引导;
  科学步骤"建议"走高层工具(`insar_plan_run`/`insar_execute_step`/…),但不强制。
- **顺带记账**:一个 provenance-capture 扩展用事件钩子把 pi 的工具调用 + bash 命令写进 SQLite 账本,
  这样即便自由跑,也尽量留痕。
- **可选严格模式**:开关打开时,`before_tool` 钩子对科学步骤 fail-closed 拦截裸 bash,强制走高层闭集工具
  + `--exclude-tools bash` 收窄;用于出版级可复现跑批。

## 4. 当前阻塞与下一步

- **阻塞(已请求密钥)**:pi 跑真实 agent 回合需要 LLM API Key(默认 google;我按 Anthropic 请求了
  `ANTHROPIC_API_KEY`,你也可给 Google/OpenAI 等,告诉我即可)。**无密钥我仍能建并测**:Python 侧工具后端、
  扩展加载与 UI 渲染、provenance 记账(用假 pi + 模拟引擎),只有"pi 真跑一轮"要等密钥。
- **下一步 P1**:落地 InSAR 高层工具层(TS 扩展 → HTTP → FastAPI)+ 侧边栏面板骨架 + 无密钥可跑的测试。
  fable5 max 深度设计子代理产出后并入其 file-level 细化。
