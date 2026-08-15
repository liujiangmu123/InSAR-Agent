# Phase 04 · InSAR 品牌换皮:insar-dark 主题(P1)

> **前置**:Phase 01 已完成(ps1 启动器已内置条件 `--theme` 逻辑);建议 Phase 02/03 也已完成(单执行者按编号串行)。与后续工具 Phase 无文件耦合。
> **目标**:insar-dark 主题(干涉条纹配色)成为默认视觉;两个启动器自动加载;主题文件有完备性测试。
> **涉及文件**:新建 `pi-insar/themes/insar-dark.json`、`pi-insar/test/theme.test.ts`;修改 `.pi/settings.json`、`scripts/insar-pi`(bash 版)、`pi-insar/test/skills.test.ts`。
> **文件内顺序**:3.1 主题 JSON → 3.2 settings → 3.3 bash 启动器 → 3.4/3.5 测试。

## 步骤 3.1 新建 `E:\01所有项目\06定职讲师\00insaragent\pi-insar\themes\insar-dark.json`

用途:pi 主题(themes.md 的 51 必需 token + 可选 thinkingMax,schema 已核实 0.84.2)。配色语义:相位青(coherence cyan)为主 accent,条纹橙/品红为强调,深海军蓝背景层次,形变红/绿作 diff。可直接落盘(完整文件):

```json
{
  "$schema": "https://raw.githubusercontent.com/earendil-works/pi/main/packages/coding-agent/src/modes/interactive/theme/theme-schema.json",
  "name": "insar-dark",
  "vars": {
    "phaseCyan": "#3fd0e0",
    "phaseCyanBright": "#7fe3f0",
    "fringeOrange": "#ff9d45",
    "fringeMagenta": "#e05299",
    "losRed": "#ff5f56",
    "cohGreen": "#4cd97b",
    "maskYellow": "#ffc857",
    "railBlue": "#33415e",
    "deepNavy": "#16233a",
    "panelNavy": "#132033",
    "inkNavy": "#101826",
    "gray": 245,
    "grayDim": 240
  },
  "colors": {
    "accent": "phaseCyan",
    "border": "railBlue",
    "borderAccent": "phaseCyan",
    "borderMuted": "#22304a",
    "success": "cohGreen",
    "error": "losRed",
    "warning": "maskYellow",
    "muted": "gray",
    "dim": "grayDim",
    "text": "",
    "thinkingText": "gray",
    "selectedBg": "deepNavy",
    "scrollbarThumb": "railBlue",
    "searchMatchBg": "#2a3b5e",
    "searchMatchText": "",
    "userMessageBg": "panelNavy",
    "userMessageText": "",
    "customMessageBg": "#101b2c",
    "customMessageText": "",
    "customMessageLabel": "phaseCyan",
    "toolPendingBg": "inkNavy",
    "toolSuccessBg": "#0f2218",
    "toolErrorBg": "#2a1414",
    "toolTitle": "phaseCyan",
    "toolOutput": "",
    "mdHeading": "fringeOrange",
    "mdLink": "phaseCyan",
    "mdLinkUrl": "gray",
    "mdCode": "phaseCyanBright",
    "mdCodeBlock": "",
    "mdCodeBlockBorder": "railBlue",
    "mdQuote": "gray",
    "mdQuoteBorder": "railBlue",
    "mdHr": "railBlue",
    "mdListBullet": "phaseCyan",
    "toolDiffAdded": "cohGreen",
    "toolDiffRemoved": "losRed",
    "toolDiffContext": "gray",
    "syntaxComment": "gray",
    "syntaxKeyword": "fringeMagenta",
    "syntaxFunction": "phaseCyan",
    "syntaxVariable": "fringeOrange",
    "syntaxString": "cohGreen",
    "syntaxNumber": "#c792ea",
    "syntaxType": "phaseCyanBright",
    "syntaxOperator": "fringeMagenta",
    "syntaxPunctuation": "gray",
    "thinkingOff": "grayDim",
    "thinkingMinimal": "railBlue",
    "thinkingLow": "phaseCyan",
    "thinkingMedium": "phaseCyanBright",
    "thinkingHigh": "fringeOrange",
    "thinkingXhigh": "fringeMagenta",
    "thinkingMax": "#ff2d78",
    "bashMode": "fringeOrange"
  }
}
```

## 步骤 3.2 修改 `E:\01所有项目\06定职讲师\00insaragent\.pi\settings.json`

加一个字段(选中主题;主题文件本身由启动器 `--theme` 提供,无需 `themes` 数组):

```json
{
  "defaultProvider": "insar-llm",
  "defaultModel": "deepseek-v4-flash-0731",
  "theme": "insar-dark"
}
```

## 步骤 3.3 修改 `E:\01所有项目\06定职讲师\00insaragent\scripts\insar-pi`(bash 版)

位置一:常量区 `APPEND_SYSTEM=` 行之后追加:

```bash
THEME="${REPO_ROOT}/pi-insar/themes/insar-dark.json"
```

位置二:`PI_ARGS+=(--append-system-prompt "${APPEND_SYSTEM}")` 之后追加:

```bash
[ -f "${THEME}" ] && PI_ARGS+=(--theme "${THEME}")
```

理由:`--theme` 仍是增量参数(只增加可选主题,不剥夺任何能力),与启动器哲学一致。ps1 版在 Phase 01 已内置同款条件逻辑,无需再改。

## 步骤 3.4 新建 `E:\01所有项目\06定职讲师\00insaragent\pi-insar\test\theme.test.ts`

用途:主题完备性契约——51 必需 token 齐全、vars 引用可解析、色值格式合法(hex6/0-255 整数/vars 名/空串)。REQUIRED_TOKENS 清单照抄 themes.md(0.84.2)。可直接落盘:

```ts
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const THEME_PATH = resolve(import.meta.dirname, "..", "themes", "insar-dark.json");

/** themes.md (pi 0.84.2): every theme must define all 51 required tokens. */
const REQUIRED_TOKENS = [
  "accent", "border", "borderAccent", "borderMuted", "success", "error", "warning",
  "muted", "dim", "text", "thinkingText",
  "selectedBg", "userMessageBg", "userMessageText", "customMessageBg",
  "customMessageText", "customMessageLabel", "toolPendingBg", "toolSuccessBg",
  "toolErrorBg", "toolTitle", "toolOutput",
  "mdHeading", "mdLink", "mdLinkUrl", "mdCode", "mdCodeBlock", "mdCodeBlockBorder",
  "mdQuote", "mdQuoteBorder", "mdHr", "mdListBullet",
  "toolDiffAdded", "toolDiffRemoved", "toolDiffContext",
  "syntaxComment", "syntaxKeyword", "syntaxFunction", "syntaxVariable",
  "syntaxString", "syntaxNumber", "syntaxType", "syntaxOperator", "syntaxPunctuation",
  "thinkingOff", "thinkingMinimal", "thinkingLow", "thinkingMedium", "thinkingHigh",
  "thinkingXhigh",
  "bashMode",
] as const;

type Theme = { name: string; vars?: Record<string, unknown>; colors: Record<string, unknown> };
const theme = JSON.parse(readFileSync(THEME_PATH, "utf8")) as Theme;

function isValidColor(value: unknown, vars: Record<string, unknown>): boolean {
  if (typeof value === "number") return Number.isInteger(value) && value >= 0 && value <= 255;
  if (typeof value !== "string") return false;
  if (value === "") return true;
  if (/^#[0-9a-fA-F]{6}$/.test(value)) return true;
  return Object.prototype.hasOwnProperty.call(vars, value); // vars reference
}

describe("insar-dark theme", () => {
  it("is named uniquely and slash-free", () => {
    expect(theme.name).toBe("insar-dark");
    expect(theme.name.includes("/")).toBe(false);
  });

  it("defines all 51 required color tokens", () => {
    expect(REQUIRED_TOKENS).toHaveLength(51);
    for (const token of REQUIRED_TOKENS) {
      expect(theme.colors, `missing token ${token}`).toHaveProperty(token);
    }
  });

  it("uses only valid color values and resolvable vars references", () => {
    const vars = theme.vars ?? {};
    for (const [name, value] of Object.entries(vars)) {
      expect(isValidColor(value, {}), `var ${name} invalid`).toBe(true);
    }
    for (const [token, value] of Object.entries(theme.colors)) {
      expect(isValidColor(value, vars), `color ${token} = ${String(value)} invalid`).toBe(true);
    }
  });
});
```

(若 `import.meta.dirname` 在当前 tsconfig 下不可用,按仓库其他测试的路径解析写法对齐,例如 `fileURLToPath(import.meta.url)`。)

## 步骤 3.5 修改 `E:\01所有项目\06定职讲师\00insaragent\pi-insar\test\skills.test.ts`

在 Phase 01 新增的 ps1 describe 与既有 bash describe 中各加一条:

```ts
it("wires the insar-dark theme additively", () => {
  expect(text).toContain("--theme");
  expect(text).toContain("insar-dark.json");
});
```

## 验收

```powershell
cd E:\01所有项目\06定职讲师\00insaragent\pi-insar
npx tsc --noEmit; npx vitest run          # theme.test + launcher 断言全绿
cd ..
pwsh scripts/insar-pi.ps1                 # (后端在跑)TUI 呈 insar-dark 配色;/settings 里 theme=insar-dark
```

人工核对项:工具卡片成功/失败底色(墨绿/暗红)、侧栏轨道 accent 青色、markdown 标题条纹橙。截取一张真实会话截图归档(与 Phase 03 手册同一批)。

## git 提交

```powershell
git add pi-insar/themes/insar-dark.json pi-insar/test/theme.test.ts pi-insar/test/skills.test.ts scripts/insar-pi .pi/settings.json
git commit -m "feat(pi-insar): insar-dark 主题 —— 干涉条纹配色换皮 + 启动器接线 + 完备性测试"
```

## 常见坑与处置

- **主题没生效**:`.pi/settings.json` 只在项目受信后生效;或主题名拼写与 `"name"` 字段不一致。`--theme` 只是"提供"文件,"选中"靠 settings。
- **pi 升级后主题报缺 token**:themes.md 的必需清单变长 → 按 `43-appendix-pi-upgrade.md` 流程补 token,并同步 `REQUIRED_TOKENS`。
- **VS Code 终端色差**:`terminal.integrated.minimumContrastRatio` 设 1(themes.md 官方建议)。

## 完成标志

- [ ] `tsc`/`vitest` 全绿(theme.test 51 token 校验通过)
- [ ] 真实会话 TUI 呈 insar-dark 配色(截图归档)
- [ ] 本 Phase 已提交,`git status` 干净

→ 下一个文件:`20-phase05-tools-run-control.md`
