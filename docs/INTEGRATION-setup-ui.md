# 环境向导 UI · 集成说明（setup.js / setup.css）

> 本交付**只新增文件，不动任何既有文件**。把向导接入主界面需要集成者在
> `prototype/index.html` 与 `prototype/js/app.js` 各加一行，见下文第 2 节。

## 1. 新增文件

| 文件 | 作用 |
|---|---|
| `prototype/js/setup.js` | 自包含 ES Module（零依赖，不 import 其它模块），导出 `maybeShowSetupWizard()` |
| `prototype/css/setup.css` | 向导样式；只消费 `tokens.css` 的设计令牌，明暗主题随 `[data-theme]` 自适应 |
| `prototype/setup-demo.html` | 独立演示页（mock fetch，无需后端），人工检查布局与交互 |
| `docs/INTEGRATION-setup-ui.md` | 本文档 |

## 2. 集成步骤（两处，各一行）

### 2.1 `index.html`：引入样式

在 `<link rel="stylesheet" href="css/dock.css">`（当前第 11 行）之后加：

```html
<link rel="stylesheet" href="css/setup.css">
```

### 2.2 `app.js`：初始化时调用

推荐在 `boot()` 函数体末尾（`paintBudget();` 之后）加一行：

```js
import('./setup.js').then((m) => m.maybeShowSetupWizard());
```

动态 import 的好处：setup.js 加载失败也不会拖垮主应用启动，且不需要在文件头部加 import。
若偏好静态导入，等价写法是两行：

```js
import { maybeShowSetupWizard } from './setup.js';   // 文件头部
maybeShowSetupWizard();                              // boot() 末尾
```

放在 `boot()` 末尾而不是开头，是为了保证向导浮层挂载时主界面骨架已就绪；
向导是全屏浮层（z-index 400，压过 toast 300 / 灯箱 200），主应用继续正常初始化即可，无需 await。

## 3. 行为契约

`maybeShowSetupWizard(): Promise<boolean>`（返回值 = 是否渲染了向导，不等待用户关闭）：

- `GET /api/setup/status` 网络错误 / 非 2xx / `file://` 打开 → **静默返回 false**，不渲染任何 DOM（浏览器纯前端演示不受影响）；
- `status.ready === true` → 静默返回 false；
- 其余情况 → 渲染全屏三步向导并返回 true。
- 重复调用幂等：向导已在页面上时直接返回 true，不会重复弹层。
- 向导**不可**被 Esc / 点击遮罩关闭（首启配置是强制流程）；唯一出口是全部检测通过后的「开始使用」。Esc 与 Tab 事件不透传到底层应用（Tab 圈定在向导内部）。
- 关闭时把焦点交还给弹出前的焦点元素。

### 向导内部流程

1. **步骤 1 · 环境检测**：`checks[]` 逐项渲染 ✓/✗ + `message` + `fix_hint`（仅失败项显示修复提示）；
   下方「环境概览」汇总 `engines` / `data.pairs` / `disk_free_gb`；「重新检测」重新拉取 status。
2. **步骤 2 · 引擎环境**：进入该步时 `POST /api/setup/engine-env`（惰性、结果缓存，失败可重试）；
   展示 `detected_conda` 与命令清单，每条命令带「复制」按钮（clipboard API，失败降级 `execCommand`）。
3. **步骤 3 · 路径配置**：两个输入框（`engine_prefix`、`hyp3_source`，至少填一项）；
   「保存并重新检测」= `POST /api/setup/save` → 重新 `GET status` → 跳回步骤 1 展示最新结果。
4. 任何时刻 `ready === true`（或 checks 全部 ok）→ 底栏「开始使用」点亮，点击关闭向导。

## 4. 依赖的后端契约

```text
GET  /api/setup/status
     → { ready: bool,
         checks: [{key, ok, message, fix_hint}],
         engines: {...}, data: {pairs: int}, disk_free_gb: float }

POST /api/setup/engine-env        （请求体为空 JSON {}）
     → { detected_conda: str|null, commands: [{title, command, note}] }

POST /api/setup/save              {engine_prefix: str, hyp3_source: str}
     → { ok: bool }               （ok=false 时可附 message/error 字段，前端会展示）
```

`engines` 的形状在契约里未定型，前端做了宽松渲染：值为布尔、
`{ok|found|available, version|path|note|message}` 对象或字符串都能显示；后端定型后可再收紧。

## 5. 自测方式（setup-demo.html）

- **HTTP 打开（完整交互）**：后端 FastAPI 静态托管 prototype/ 后访问 `/setup-demo.html`，
  或 `cd prototype && python -m http.server 8080` 后访问 `http://localhost:8080/setup-demo.html`。
  页面用 mock fetch 拦截三个 `/api/setup/*` 端点，提供三个场景：
  - *全新机器*：引擎/数据未配置（含未检测到 Conda 分支），在第 3 步保存后全部转绿；
  - *部分通过*：引擎已装（检测到 Conda 分支）、数据与磁盘未过；
  - *全部就绪*：`ready=true`，验证「静默不弹」契约。
  顶栏可切换明暗主题、重开向导。
- **file:// 双击打开（仅布局）**：浏览器会以 CORS 拦截 ES Module 加载（属预期），
  页面自动降级为**静态布局快照**——与 setup.js 产出的 DOM 结构/类名一致、三步同屏铺开，
  主题切换仍可用，供快速核对样式。

## 6. 已知限制

- `navigator.clipboard` 需要安全上下文（`localhost` / `https` / 桌面 WebView 均满足）；
  不满足时自动降级 `document.execCommand('copy')`，再失败按钮会显示「复制失败」（不会抛错）。
- 遮罩底色与「开始使用」的呼吸光环用 `color-mix()` 基于令牌生成（不硬编码颜色），
  需要 Chrome 111+ / Firefox 113+ / Safari 16.2+——与 `dock.css` 既有的 `color-mix` 用法同一基线。
- 向导没有「跳过」出口：按需求全部通过才能进入工作区；若要允许「稍后配置」需另行扩展。
- 第 2 步命令清单完全来自后端 `engine-env` 响应，前端不做任何命令拼装；
  后端返回空 `commands` 时会显示「后端未返回命令清单」的提示条。
- 静态快照（demo 页 file:// 兜底）是手写的 DOM 副本，若后续改动 setup.js 的类名/结构，
  需同步更新 `setup-demo.html` 内 `#staticSnapshot` 一段（仅影响演示页，不影响正式集成）。
