# 51 · Phase U — 桌面 InSAR 化:壳观感重启(chrome + 右栏可视化)

> 目标:让 pi Desktop 用起来**像 InSAR 数据处理软件**,而不是通用编码 agent 聊天壳。交互骨架不动(左会话 / 中对话 / 右 Tabs,操作员仍然全程用中文对话驱动 31 个 `insar_*` 工具);要改的是**观感与读面**:右栏 InSAR 面板从"ASCII 日志"升级为工业流水线轨道 + 数据集 + 图件检查器,加最小的 InSAR chrome(默认右栏 Tab、标题副题、打包版 CSP)。
> 分工:本文件是规划(Fable);代码由 Grok 按 §5 切片执行。纪律沿用 `01-execution-rules.md` 三条红线与 Phase 50 的 overlay 纪律。**不跑 MintPy / 不碰重型计算;不杀 8873 后端与既有 Electron。**
> 前置:Phase 50 的 D0-D2 面已落地(overlay 机制、`http-json` provider、`/api/monitor?session=@latest`、`insar-pipeline` 右栏面板、G7 图件 media 卡)。D4/D5(SDK 对齐、自打包)与本 Phase 无依赖关系,先后皆可。

## 0. 一页结论

### 0.1 勘察事实(2026-08-15 实读源码,执行前按 §6 坐标复核)

| # | 事实 | 出处 | 对计划的含义 |
|---|---|---|---|
| F1 | 右栏 `insar-pipeline` 面板已存在:2s 轮询 `adapter.sidePanel.getState`、monitor 全量 coerce、三态(空/跑/错)不崩;但视觉是等宽字形日志(`✔R○` 拼串),无语义色系统、无数据/图件面 | 壳 `src/renderer/src/features/side-panels/insar-pipeline-side-panel.tsx` | 升级不是重写:保留轮询/coerce/降级骨架,重构呈现层,新增子区 |
| F2 | 主进程 provider `http-json` **写死** `/api/monitor?session=`(读 adapter config 的 `apiBaseHint`/`session`,env `INSAR_API_BASE` 兜底,5s 超时,失败落成 `{ok:false,error,base,session}` 状态而非抛错);IPC `adapter.sidePanel.getState` 只传 `adapterId+workspaceId`,读什么完全由 provider 决定 | 壳 `src/main/side-panel-registry.ts`、`src/main/ipc/handlers/adapter-panels.ts` | 数据集+图件的供数在 **provider 层聚合**(新增 `insar-read`),IPC 与渲染器通道零改动;既有 `http-json` 原样保留作兼容 |
| F3 | adapter schema 一个适配器只有**一个** `sidePanel`(无多面板字段);右栏 Tab 目录 = 5 个核心(review/run/context/tree/files,默认只开 files+run)+ 适配器面板追加在后 | 壳 `src/extension-compat/adapter-schema.ts`、`packages/shared/right-panels.ts` | 做**一个"InSAR 工作台"面板 + 面板内分区**(流水线/数据/图件),不开三个 Tab、不改 schema 的面板数量模型 |
| F4 | `/api/monitor` 支持 `session=@latest` 且响应体带**解析后的真实 session id**;`/api/figures` 与 `/api/datasets` 存在但 figures 需要具体 session(无 @latest) | `src/insar_agent/api/bridge_router.py`、`api/app.py:1152`、`api/data_catalog_router.py` | provider 内**链式取数**:monitor(@latest)→ 用其 session/run_id 取 figures;datasets 直取。**零新后端端点、零 Python 改动** |
| F5 | `/api/figures` 每条含三档 URL(`thumbUrl` 320px 网格档 / `url` 2048px 浏览档 / `fullUrl` 原图),都是 `/api/artifact-file?...` 的 HTTP GET;注释明言按"画廊 3s 轮询"设计;`/api/datasets` 服务端 60s TTL 缓存,kind 闭集 `hyp3/alos_raw/slc_stack/dem/unknown` | `api/app.py`(figures/artifact-file)、`data/catalog.py` | 缩略图直接 `<img src="{base}{thumbUrl}">`;轮询定 3s 与后端设计吻合;数据集徽章五色闭集 |
| F6 | 打包版 CSP `img-src 'self' data: blob:`(dev 跳过 CSP 以配合 Vite HMR)→ 打包后右栏 `<img>` 取 127.0.0.1:8873 会被拦 | 壳 `src/main/index.ts:75-84` | U3 里 CSP 一行加 `http://127.0.0.1:*`(仅 img-src);dev 态开发全程不受影响 |
| F7 | 默认激活 Tab = 目录序第一个启用项 = `run`;顺序/开关持久化在 userData configStore;目录合并层无"适配器面板置前/默认激活"机制 | `packages/shared/right-panels.ts`(`firstEnabledPanel`/`normalizeRightPanelOrder`) | 加可选 `defaultActive` 字段(schema→catalog→排序),insar 适配器声明后,**无用户自存布局时** InSAR Tab 排第一且默认激活;用户手动布局仍优先 |
| F8 | TopBar 标题 = `title \|\| t('common:app.name')`("pi Desktop"),组件内可访问 uiStore(含右栏目录);OS 窗口题名与 productName 不在本 Phase 触碰 | 壳 `src/renderer/src/components/app/top-bar.tsx`、`locales/*/common.json` | 副标题做成**条件 chip**:右栏目录含 `adapterId==='insar'` 时显示「InSAR 工作台」——只在 InSAR 工作区出现,通用 pi 工作区零污染 |
| F9 | 旧 prototype 视觉语言(已删除,语义已进 `.insar-panel`):四色 --ok/#159a46、--stale/#ca4c0a、--bad/#dc2626、--accent/#2563eb + weak/border/text,明暗双份 | 已移植,不 import 旧 CSS |
| F10 | overlay 纪律与工具链在位:`scripts/sync-pi-app-overlay.ps1 -Check/-Pull/-Push [-Path]`;壳测试形态 `npm run typecheck` + `npm run test:unit`(main 测试就近 `.test.ts` 或 `__tests__/`);渲染器有组件级 CSS import 先例(katex);现 `http-json` provider **无单测** | `scripts/sync-pi-app-overlay.ps1`、壳 `src/main/**.test.ts`、`markdown-view.tsx` | 每个改壳切片:改动 + 就近单测 + `-Pull -Path` 镜像;新 provider 补真实本地 HTTP 服务的单测(不 mock 网络语义) |

### 0.2 什么变、什么保持 pi

**保持不动**(这就是"保留 pi 交互方式"的边界):左栏会话列表与项目切换、中央对话时间线与 composer、工具卡体系(insar list 卡 + figures media 卡)、slash 命令、设置页、`http-json` 既有 provider、后端与 31 工具、`.pi/` 加载链、LLM 配置单真源。

**要变**(全部围绕"InSAR 处理软件"的观感):
1. 右栏 InSAR 面板 → **工作台**:工业流水线轨道(步号 01–11 / 20–28、状态节点+摘要 chips、证据级徽章、模拟横幅、点击步看方法/耗时/失效原因)+ 数据集卡片区 + 图件缩略网格与灯箱;
2. 供数 → 主进程新 `insar-read` 聚合 provider(monitor+datasets+figures 一次 IPC);
3. chrome → InSAR Tab 默认第一且激活、TopBar「InSAR 工作台」副题 chip、打包版 CSP 放行本机图件;
4. 语义色 → `.insar-panel` 作用域 tokens(prototype 四色语义,明暗双份),**不全局换肤**。

## 1. 目标 UX(操作员视角)

| 区 | 内容 | 中文标签 | 本 Phase 动不动 |
|---|---|---|---|
| 左栏 | 会话列表 / 项目 | pi 自带中文(会话/项目) | **不动** |
| 中央 | 对话时间线 + composer;操作员在这里下指令、看工具卡与图件 media 卡 | pi 自带 | **不动** |
| 右栏 | Tab 序(默认布局):**InSAR 工作台** → 运行 → 文件(review/context/tree 默认关) | Tab 名「InSAR」;面板内分区「流水线 / 数据 / 图件」 | **重点改** |
| 顶栏 | `pi Desktop / <项目名>` 后随条件副题 chip「InSAR 工作台」 | 中文 | 加一个 chip |

右栏 InSAR 面板(宽 ~288px 起)结构:

```
┌──────────────────────────────────────┐
│ INSAR 工作台          800a728d  64%  │ ← run 短 id + 进度
│ quake · running · [模拟] [证据 audited]│ ← 状态行:chips(模拟=橙横幅)
│ ▓▓▓▓▓▓▓▓░░░░░░░░░░░░░               │ ← 进度条(--insar-run)
│ [流水线] [数据 3] [图件 12]           │ ← 分区切换(计数徽标)
├──────────────────────────────────────┤
│ ✓5 ▶1 ✖0 !1 ↷2 ○4                   │ ← 摘要 chips(零值淡显)
│ 核心流水线 01–11            5/11     │
│ 01 ● 数据接入      hyp3      3.2 s  │
│ 07 ◉ 相位解缠      snaphu    …      │ ← 运行中:脉冲节点+行高亮
│ 08 ! 形变反演      mintpy    12 s   │ ← 失效:橙 + tooltip
│ 分析后处理 20–28            0/5     │
│ 20 ○ 掩膜          —                │
│ ┌ 展开详情(点击行) ──────────────┐  │
│ │ 方法 snaphu · 阶段 R 运行 · ×2 │  │
│ │ stale_reason: 上游 06 重跑     │  │
│ └───────────────────────────────┘  │
│ ▶ 07 相位解缠 · snaphu · R          │ ← 底部当前行(保留)
└──────────────────────────────────────┘
```

三条铁律:① 面板**只读**——没有任何执行/重跑按钮,一切操作回到对话里的 `insar_*` 工具(strict 纪律不被右栏绕过);② 状态 = **颜色+形状+文字**三重表达(色盲友好,prototype 同款原则);③ 空/错/模拟诚实呈现——无 run 显示"尚无 run,对话中让 agent 规划",后端不可达显示降级横幅,simulated 永远有橙色横幅,**绝不放演示假数据**。

## 2. 阶段 U0 → U3

> 顺序:U0(供数)与 U1 前半(tokens/轨道组件)可并行;U2 依赖 U0/U1;U3 收口。所有人工验收在 dev 态(`npm run dev` 或 `scripts/insar-pi-desktop.ps1`)做。

### U0 · 读面聚合:主进程 `insar-read` provider(改壳 main)

**目标**:一次 IPC 返回 `{monitor, datasets, figures}`,后端零改动。

**供数契约(v2,S 切片间的固定接口,写死在此不再协商)**:

```jsonc
// resolveSidePanelState → state(成功面)
{
  "v": 2,
  "base": "http://127.0.0.1:8873",        // 实际使用的 API base
  "monitor": { /* GET /api/monitor?session={cfg.session||@latest} 原样 */ },
  "datasets": { /* GET /api/datasets 原样 */ },   // 失败: { "error": "HTTP 500" }
  "figures":  { /* GET /api/figures?session={monitor.session}&run_id={monitor.run.run_id} 原样 */ }
              // monitor.run==null 时不发请求,固定 { "run": null, "figures": [] };失败: { "error": "..." }
}
// monitor 本身不可达/非200 → 整体 { ok:false, error, base, session }(与现 http-json 完全一致,面板降级逻辑复用)
```

要点:monitor 先行,figures 用 monitor 解析出的**真实 session**(F4 的 @latest 桥);datasets 与 monitor 并行;子面各自 5s 超时、独立降级,任何子面失败不拖垮 monitor 主面;不做主进程缓存(datasets 服务端 60s TTL、figures 按 3s 轮询设计,F5)。

**验收**:壳 typecheck + test:unit 全绿(含新 provider 单测:真实本地 HTTP 服务覆盖 成功/monitor挂/datasets挂/无run跳过figures 四分支);`-Check` 一致。

**风险**:R-U0-1 轮询放大后端负载 → 面板轮询定 3s(F5 设计值),单面板单连接,可忽略;R-U0-2 provider id 写错导致右栏报 `unknown_provider` → 适配器 JSON 的切换放 U3 末片,期间面板对旧形状向后兼容(见 U1)。

### U1 · 流水线轨道工业化(改壳 renderer)

**目标**:字形日志 → 工业轨道。`.insar-panel` 作用域语义 tokens + 轨道组件 + 面板容器重构。

- **tokens**(新 `insar-panel.css`):`--insar-ok/-run/-bad/-stale/-pend` 各配 `-weak/-border/-text` 变体;亮色取 prototype 明主题值(#159a46/#2563eb/#dc2626/#ca4c0a),`.dark .insar-panel` 取暗主题值(#4ade80/#60a5fa/#f87171/#fb923c 系);节点/chip/横幅工具类;`@media (prefers-reduced-motion: reduce)` 停脉冲。**只作用于 `.insar-panel` 子树**,不碰壳全局变量与 Tailwind 配置。
- **轨道**(新 `insar-rail.tsx`,纯呈现):摘要 chips 行(完成/运行/失败/失效/跳过/待执行,计数,零值淡显)→ 分组步列表(<20 = 「核心流水线 01–11」,≥20 = 「分析后处理 20–28」,组头带组内进度)→ 行 = 步号(2位等宽)+ 状态节点(●实心=done,◉脉冲=running,○空心=pending,✖=failed,!=stale,↷虚线=skipped;色+形+文三重)+ 名称 + 方法 + 右对齐耗时/尝试 → 点击行内联展开详情(方法/阶段/耗时/尝试/stale_reason/failure_class)。**无按钮**。
- **容器**(重构 `insar-pipeline-side-panel.tsx`):轮询 2s→3s;识别 v2 合并态(`state.v===2`)并拆发 monitor/datasets/figures;**旧形状(纯 monitor)向后兼容**——轨道照常渲染,数据/图件分区显示"供数待接线(适配器尚未切到 insar-read)";头部升级:run 短 id、scenario、状态 chip、evidence 徽章(`audited` 绿 / 含 `simul` 橙 / 其余中性,ceiling 括注)、`simulated===true` 时橙色横幅「模拟结果 · 非真实数据」、mode chip、taints;分区切换头(流水线/数据/图件,带计数);类型与 coerce 抽到共享 model 文件。

**验收**:壳双测绿;dev 态人工三态走查(无 run / realtest audited run / 停后端再启)不崩且视觉达标;reduced-motion 下无动画。

**风险**:R-U1-1 面板重构回归 → 保留既有降级/空态文案语义,IPC 面不动;R-U1-2 色彩对比度 → 直接采用 prototype 已做过 WCAG 校准的值(F9),不自造色。

### U2 · 数据集 + 图件检查器(改壳 renderer,新文件)

**目标**:右栏「数据」「图件」分区可用,操作员不离开 Desktop 就能核对数据源与成图。

- **数据集**(新 `insar-datasets-section.tsx`):卡片列表——名称、kind 徽章(五色闭集 `hyp3/alos_raw/slc_stack/dem/unknown`,tokens 映射 + 文字,unknown 灰)、size 人类可读、file_count、date_range、detail 摘要(pairs/scenes/slc 等按 kind);底部 roots 与 scanned_at;空态「未发现数据集(检查 INSAR_DATA_DIR / home/datasets)」;`{error}` 时降级行。不做 rescan 按钮(服务端 60s TTL 已够,避免右栏长出运维按钮)。
- **图件**(新 `insar-figures-section.tsx`):缩略网格(`repeat(auto-fill, minmax(120px,1fr))`,4:3 cover,`loading="lazy"`,`src={base}{thumbUrl}`,加载失败灰块+文件名)——每格下行 sidecar 标题||文件名 + `S{step}` 徽标;点击 → 面板内灯箱(浏览档 `{base}{url}`、图注 = 名称+步号+meta 摘要、Esc/点击关闭);simulated run 时网格顶端橙 chip;空态「该 run 尚无图件」。**看原图/下载不做**(时间线 media 卡 + openPath 已覆盖,避免重复)。

**验收**:壳双测绿;realtest run 下网格出图、灯箱可开合;datasets 对真实 INSAR_DATA_DIR 列出正确 kind;dev 态 `<img>` 直连 8873 正常(CSP dev 不拦,F6)。

**风险**:R-U2-1 面板窄导致网格挤 → minmax(120px) 自适应 1-2 列,右栏可拖宽;R-U2-2 大量图件 → 后端 figures 每目录上限 100 条已封顶,网格 lazy 加载。

### U3 · InSAR chrome + 收口(改壳 shared/main/顶栏 + 本仓库)

**目标**:打开工作区第一眼是 InSAR:右栏默认落在 InSAR Tab、顶栏见「InSAR 工作台」、打包版图件不瞎。

- **默认 Tab**:`packages/shared/right-panels.ts` 加可选 `defaultActive`(AdapterSidePanelMeta + RightPanelCatalogItem 透传;`normalizeRightPanelOrder` 在**无用户已存顺序**时把 defaultActive 面板排最前 → `firstEnabledPanel` 自然选中它);`adapter-schema.ts` 的 `AdapterSidePanel` 加同名可选字段;`side-panel-catalog.ts` 透传;单测进 `right-panels.test.ts`。用户已存布局永远优先(验收用重置布局或单测证明)。
- **副题 chip**:`top-bar.tsx` 在右栏目录含 `adapterId==='insar'` 时,在标题后渲染小号 chip「InSAR 工作台」(locales zh/en 加 `topbar.insarWorkbench`)。通用工作区不出现。
- **CSP**:`src/main/index.ts` 打包版 CSP 的 `img-src` 追加 `http://127.0.0.1:*`(仅 img-src;connect-src 不动——渲染器取数走 IPC,不直连)。
- **适配器接线**(仅本仓库):`.pi/desktop/adapters/insar.adapter.json` — `sidePanel.stateProvider` 切 `insar-read`、加 `defaultActive: true`、description/i18n 更新为「工作台:流水线/数据/图件」;`insar-figures.adapter.json` 不动。
- **文档**:`pi-insar/docs/desktop-walkthrough.md` 加 Phase U 走查章(三态 + 数据/图件 + 默认 Tab + 副题 + 打包版 CSP 说明);overlay 全量 `-Check`。

**验收**:壳双测绿 + 本仓库 `pi-insar` 双测与根 pytest 不回退;人工:重置右栏布局后重开 Desktop → InSAR Tab 第一且默认激活、顶栏见副题、面板三分区全通;`-Check` 逐字节一致。

**风险**:R-U3-1 已存 rightPanelOrder 的老用户看不到"默认第一" → 预期行为(用户布局优先),走查手册写明"设置→右栏→重置布局"路径;R-U3-2 CSP 通配端口兼容性 → CSP3 host-source 支持端口通配,Chromium 实现在位;打包验证归 Phase 50 D5 冒烟捎带,不阻塞本 Phase。

## 3. 必须改壳 vs 只改本仓库

### 3.1 必须改 E:\SoftApp\pi-app(每处镜像进 `desktop/pi-app-overlay/`,同切片内 `-Pull -Path`)

| # | 壳内相对路径 | 改动 | 新/改 | 阶段 |
|---|---|---|---|---|
| 壳1 | `src/main/side-panel-registry.ts` | 加 `insar-read` 聚合 provider(monitor→figures 链 + datasets 并行);`http-json` 原样保留 | 改 | U0 |
| 壳2 | `src/main/side-panel-registry.insar-read.test.ts` | provider 四分支单测(真实本地 HTTP) | 新 | U0 |
| 壳3 | `src/renderer/src/features/side-panels/insar-panel.css` | `.insar-panel` 语义 tokens(明暗)+ 节点/chip/横幅样式 + reduced-motion | 新 | U1 |
| 壳4 | `src/renderer/src/features/side-panels/insar-panel-model.ts` | v2 合并态 + monitor/datasets/figures 类型与 coerce(自现面板抽出并扩展) | 新 | U1 |
| 壳5 | `src/renderer/src/features/side-panels/insar-rail.tsx` | 轨道纯呈现组件(chips/分组/节点/展开详情) | 新 | U1 |
| 壳6 | `src/renderer/src/features/side-panels/insar-datasets-section.tsx` | 数据集卡片区 | 新 | U2 |
| 壳7 | `src/renderer/src/features/side-panels/insar-figures-section.tsx` | 图件网格 + 灯箱 | 新 | U2 |
| 壳8 | `src/renderer/src/features/side-panels/insar-pipeline-side-panel.tsx` | 容器重构:3s 轮询、v2 拆发、分区切换、头部升级、旧形状兼容 | 改 | U1/U2 装配 |
| 壳9 | `packages/shared/right-panels.ts` + `right-panels.test.ts` | `defaultActive` 排序语义 + 单测 | 改 | U3 |
| 壳10 | `src/extension-compat/adapter-schema.ts`、`src/extension-compat/side-panel-catalog.ts` | `sidePanel.defaultActive?: boolean` 透传 | 改 | U3 |
| 壳11 | `src/renderer/src/components/app/top-bar.tsx` + `locales/{zh,en}/common.json` | 条件副题 chip「InSAR 工作台」 | 改 | U3 |
| 壳12 | `src/main/index.ts` | 打包版 CSP `img-src` 加 `http://127.0.0.1:*` | 改 | U3 |

### 3.2 只改本仓库

| 文件 | 改动 | 阶段 |
|---|---|---|
| `.pi/desktop/adapters/insar.adapter.json` | `stateProvider: "insar-read"`、`defaultActive: true`、description/i18n 更新 | U3 |
| `desktop/pi-app-overlay/**` | 壳1-壳12 逐字节镜像(`-Pull`) | 各切片 |
| `pi-insar/docs/desktop-walkthrough.md` | Phase U 走查章 | U3 |
| `pi-insar/docs/plan/00-README.md` | 本 Phase 索引行勾选 + 提交号 | 完成时 |

**不改**:`src/insar_agent/**`(Python 内核零改动,无新端点)、`pi-insar/src/**`(扩展与工具面)、壳 `package.json` / `electron-builder.yml` / productName、`insar-figures.adapter.json`、`generic-adapter-side-panel.tsx`、既有 `http-json` provider 行为。

## 4. 非目标(明确不做)

- **不重写应用、不做第二套聊天 UI**:中央时间线是唯一操作面;右栏面板只读。
- **不做 GIS/WebGL 地图引擎**、不引 mapbox/deck.gl/openlayers 等任何新依赖(本 Phase **零新增 npm 依赖**)。
- **不复活 prototype/ 为产品 UI**(观察其视觉语言即可)、不复活 Tauri 旧壳、不 fork pi TUI。
- **不全局换肤**:壳全局变量、Tailwind 配置、通用组件配色一律不动;InSAR 色系锁死在 `.insar-panel` 与一个副题 chip 里。
- **不做右栏会话选择器、不做 onUpdate 流式进度**(维持 Phase 50 暂缓判定;monitor `@latest` 语义已覆盖桌面场景)。
- **不动 SDK 版本、不改 productName、不加第二处 LLM 配置、不把任何 key 写进 adapter/设置**。
- **不造假数据**:面板一切内容来自 live `/api/monitor`、`/api/datasets`、`/api/figures`;空/错就是空/错;演示走 realtest 真实 run。
- **不加新后端端点、不改既有端点**(读面聚合完全在壳主进程完成)。
- **不跑 MintPy / 重型计算**;验收全部基于既有 realtest audited run 的读回面。

## 5. 给 Grok 的任务切片(G11–G16,接续 Phase 50 的 G1–G10;每片文件闭集互不重叠)

> 通用完成定义(每片):改动落盘 → 壳 `npm run typecheck` + `npm run test:unit` 全绿 → `pwsh scripts/sync-pi-app-overlay.ps1 -Pull -Path <本片壳文件...>` 镜像 → 按 00-README 执行循环提交。任何切片**不得新增 npm/pip 依赖**、不得动 §3.2 "不改"清单、不得引入假数据或演示态。并行许可:G11 ∥ G12 ∥ G15;G13 依赖 G12;G14 依赖 G11+G12+G13;G16 依赖 G11+G14+G15。

| 片 | 一句话目标 | 文件闭集(壳路径省略前缀 E:\SoftApp\pi-app) | 验收 |
|---|---|---|---|
| G11 | 主进程 `insar-read` 聚合 provider(§2-U0 契约 v2 逐字实现) | `src/main/side-panel-registry.ts`;新 `src/main/side-panel-registry.insar-read.test.ts` | 单测四分支(成功合并 / monitor 挂→`ok:false` / datasets 挂→子面 `{error}` 其余正常 / 无 run→figures 跳过为空壳)用真实本地 HTTP 服务;typecheck+test:unit 绿;`-Pull` 镜像 |
| G12 | InSAR 语义 tokens + 轨道组件 + 共享 model(纯新文件,不接线) | 新 `src/renderer/src/features/side-panels/insar-panel.css`、`insar-rail.tsx`、`insar-panel-model.ts`(model 含 v2/旧形状两套 coerce,类型与 §2-U0 契约一致) | typecheck 绿(组件此时未被引用属正常);tokens 明暗双份、reduced-motion 停脉冲;轨道无任何执行按钮 |
| G13 | 数据集 + 图件分区组件(纯新文件,消费 G12 的 model 类型) | 新 `src/renderer/src/features/side-panels/insar-datasets-section.tsx`、`insar-figures-section.tsx` | typecheck 绿;五色 kind 徽章闭集、图件网格 lazy + 灯箱、空/错态文案照 §2-U2;不发任何网络请求(只吃 props,`<img>` 除外) |
| G14 | 面板容器重构装配(唯一改动此文件的切片) | `src/renderer/src/features/side-panels/insar-pipeline-side-panel.tsx`(import G12/G13 组件与 css;3s 轮询;v2 拆发;旧形状兼容降级文案「供数待接线」;头部 run/evidence/simulated/mode/分区切换) | typecheck+test:unit 绿;dev 态人工:无 run / realtest run / 停后端 三态不崩;旧 provider(未切 insar-read 前)下轨道照常、数据/图件区显示待接线 |
| G15 | InSAR chrome:defaultActive + 副题 chip + CSP | `packages/shared/right-panels.ts`、`packages/shared/right-panels.test.ts`、`src/extension-compat/adapter-schema.ts`、`src/extension-compat/side-panel-catalog.ts`、`src/renderer/src/components/app/top-bar.tsx`、`src/renderer/src/locales/zh/common.json`、`src/renderer/src/locales/en/common.json`、`src/main/index.ts` | 新单测:defaultActive 面板在无已存顺序时排第一、有已存顺序时用户优先;副题 chip 仅当目录含 insar 适配器(settings-chrome 既有测试不回退);CSP 仅 img-src 多 `http://127.0.0.1:*`;typecheck+test:unit 绿 |
| G16 | 适配器接线 + 文档 + 全量校验(仅本仓库) | `.pi/desktop/adapters/insar.adapter.json`、`pi-insar/docs/desktop-walkthrough.md`、`pi-insar/docs/plan/00-README.md`(勾表) | 重启 Desktop 后:右栏 InSAR Tab 默认第一且激活,面板三分区吃到真数据;`sync-pi-app-overlay.ps1 -Check` 全量逐字节一致;`pi-insar` `npx tsc --noEmit`+`npx vitest run` 与根 `.venv\Scripts\python.exe -m pytest -q` 不回退;走查章可执行 |

**切片纪律**:G12/G13 是"纯新文件"片——禁止顺手改既有文件;G14 是唯一允许改 `insar-pipeline-side-panel.tsx` 的片;G15 是唯一碰 shared/top-bar/index.ts 的片;G16 是唯一碰适配器 JSON 与文档的片。视觉细节(间距、字号、hover)Grok 在 tokens 约束内自决,**不得**为视觉去改壳全局样式或 Tailwind 配置。

## 6. 关键文件坐标速查

```
壳(E:\SoftApp\pi-app,v0.5.7)
  src/main/side-panel-registry.ts                     # PROVIDERS: workspace-trellis | http-json(现写死 monitor)→ 加 insar-read
  src/main/ipc/handlers/adapter-panels.ts             # ipc:adapter.sidePanel.getState(不改)
  src/main/index.ts:75-84                             # 打包版 CSP(img-src 行)
  src/extension-compat/adapter-schema.ts:117-129      # AdapterSidePanel(加 defaultActive?)
  src/extension-compat/side-panel-catalog.ts          # sidePanel → AdapterSidePanelMeta 透传
  packages/shared/right-panels.ts                     # CORE_RIGHT_PANEL_IDS/normalizeRightPanelOrder/firstEnabledPanel
  src/renderer/src/features/side-panels/
    insar-pipeline-side-panel.tsx                     # 现面板(2s 轮询 + monitor coerce)→ G14 重构
    side-panel-registry.tsx                           # ADAPTER_PANEL_COMPONENTS['insar-pipeline'](不改)
    (新增) insar-panel.css / insar-panel-model.ts / insar-rail.tsx / insar-datasets-section.tsx / insar-figures-section.tsx
  src/renderer/src/components/app/top-bar.tsx         # 标题行(加条件副题 chip)
  src/renderer/src/styles/globals.css                 # 壳全局变量(只读参考,勿改)
本仓库
  .pi/desktop/adapters/insar.adapter.json             # sidePanel(stateProvider/defaultActive 在此切)
  src/insar_agent/api/bridge_router.py                # /api/monitor(@latest 语义,响应带解析后 session)
  src/insar_agent/api/app.py:1152/1252                # /api/figures(三档 url)、/api/artifact-file
  src/insar_agent/api/data_catalog_router.py          # /api/datasets(60s TTL,kind 闭集)
  scripts/sync-pi-app-overlay.ps1                     # -Check / -Pull [-Path] / -Push
  desktop/pi-app-overlay/**                           # 壳改动镜像(1:1 相对路径)
```

## 7. 重启与运行注意

- **主进程/共享层改动**(G11、G15 的 `right-panels.ts`/`adapter-schema.ts`/`side-panel-catalog.ts`/`index.ts`):dev 态 electron-vite 会重启主进程,若行为未生效由**用户重启 Desktop 窗口**;计划内任何一步都**不得 taskkill Electron**。
- **纯渲染器改动**(G12/G13/G14 的 tsx/css):Vite HMR 即时生效,无需重启。
- **适配器 JSON 改动**(G16):adapter catalog 有进程内缓存,以重启 Desktop 为准(插件页刷新可 `invalidateAdapterCatalog`,但验收以重启后状态为据)。
- **8873 后端永不杀**:验收用窗口 A `pwsh scripts/insar-backend-real.ps1` 既有实例;"停后端"类降级测试用临时端口的一次性后端或拔 apiBaseHint,不动主实例。
- 打包版(NSIS/portable)验证不在本 Phase:CSP 改动的打包生效归 Phase 50 D5 冒烟一并确认。
