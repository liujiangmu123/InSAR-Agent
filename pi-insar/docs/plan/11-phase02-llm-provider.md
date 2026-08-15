# Phase 02 · LLM 供应商接线:workspace/llm.json → pi(P0)

> **前置**:`10-phase01-windows-baseline.md` 已完成(pi 已安装、ps1 启动器在位、测试全绿)。
> **目标**:pi 启动即拥有 `insar-llm` 供应商与真实模型,默认选中,零新密钥,key 永不落新文件。
> **涉及文件**:新建 `pi-insar/src/provider.ts`、`.pi/settings.json`、`pi-insar/test/provider.test.ts`;修改 `pi-insar/src/index.ts`、`pi-insar/README.md`。
> **文件内顺序**:1.1 provider.ts → 1.2 index.ts → 1.3 settings.json → 1.4 测试 → 1.5 README。

## 步骤 1.1 新建 `E:\01所有项目\06定职讲师\00insaragent\pi-insar\src\provider.ts`

用途:定位/解析 `workspace/llm.json` 并向 pi 注册 openai-completions 供应商。导出面:`PROVIDER_ID`、`LlmFileConfig`、`locateLlmConfig`、`loadLlmConfig`、`registerInsarProvider`、`installProvider`。可直接落盘骨架:

```ts
/**
 * insar-llm — register the workspace LLM endpoint as a pi provider.
 *
 * Single source of truth: workspace/llm.json (git-ignored). The key lives in
 * memory only; it must never be logged, snapshotted or written elsewhere.
 */

import { existsSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

export const PROVIDER_ID = "insar-llm";

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");

/** Shape of workspace/llm.json (real fields, no invention). */
export interface LlmFileConfig {
  base_url: string;
  chat_model: string;
  vision_model?: string;
  api_key: string;
}

/** INSAR_LLM_CONFIG override first, then <repo>/workspace/llm.json. */
export function locateLlmConfig(env: NodeJS.ProcessEnv = process.env): string | undefined {
  const explicit = env.INSAR_LLM_CONFIG;
  if (explicit && existsSync(explicit)) return explicit;
  const fallback = join(REPO_ROOT, "workspace", "llm.json");
  return existsSync(fallback) ? fallback : undefined;
}

/** Parse + validate. Returns undefined on any problem — a missing LLM config
 *  must never break pi startup (the insar_* tools work without it). */
export function loadLlmConfig(path: string): LlmFileConfig | undefined {
  try {
    const raw = JSON.parse(readFileSync(path, "utf8")) as Partial<LlmFileConfig>;
    if (!raw.base_url || !raw.chat_model || !raw.api_key) return undefined;
    return raw as LlmFileConfig;
  } catch {
    return undefined; // 错误信息不携带文件内容:key 不允许进任何日志
  }
}

export function registerInsarProvider(pi: ExtensionAPI, config: LlmFileConfig): void {
  const model = (id: string, vision: boolean) => ({
    id,
    name: id,
    reasoning: false,
    input: (vision ? ["text", "image"] : ["text"]) as ("text" | "image")[],
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    contextWindow: 131_072,
    maxTokens: 8_192,
  });
  const models = [model(config.chat_model, false)];
  if (config.vision_model && config.vision_model !== config.chat_model) {
    models.push(model(config.vision_model, true));
  }
  pi.registerProvider(PROVIDER_ID, {
    name: "InSAR LLM (workspace/llm.json)",
    baseUrl: config.base_url.replace(/\/+$/, ""),
    apiKey: config.api_key, // literal, memory-only
    api: "openai-completions",
    models,
  });
}

/** Wire-up used by the extension factory. Returns whether a provider was registered. */
export function installProvider(pi: ExtensionAPI): boolean {
  const path = locateLlmConfig();
  if (!path) return false;
  const config = loadLlmConfig(path);
  if (!config) return false;
  registerInsarProvider(pi, config);
  return true;
}
```

说明:`contextWindow/maxTokens` 取保守静态值(上游是 OpenAI 兼容代理,131072/8192 对当前 chat_model 安全);若之后要精确化,可把工厂改 async 并 GET `{base_url}/models`,不属本 Phase。`registerProvider` 的旧式 config 形态与字段名以 `pi-insar/reference/pi/packages/coding-agent/docs/custom-provider.md` 的 "Register New Provider" 为准(已核实 0.84.2)。

## 步骤 1.2 修改 `E:\01所有项目\06定职讲师\00insaragent\pi-insar\src\index.ts`

三处插入:

- import 区(`registerInsarTools` import 之后):`import { installProvider } from "./provider.ts";`
- 工厂体内、`registerInsarTools(pi, client, controller);` 之前加一行:`installProvider(pi);`(供应商必须在启动阶段注册,`pi --list-models` 才可见)。
- 文件尾 re-export 区追加:`export { installProvider, loadLlmConfig, locateLlmConfig, PROVIDER_ID, registerInsarProvider } from "./provider.ts";`

## 步骤 1.3 新建 `E:\01所有项目\06定职讲师\00insaragent\.pi\settings.json`

用途:项目级默认(pi 官方 settings.md 字段)。`defaultModel` 的值**执行时读 `workspace/llm.json` 的 `chat_model` 字段填入**(当前为 `deepseek-v4-flash-0731`;若届时不一致以 llm.json 为准):

```json
{
  "defaultProvider": "insar-llm",
  "defaultModel": "deepseek-v4-flash-0731"
}
```

此文件入库(不含任何密钥)。首次交互式启动 pi 会弹项目信任确认 → 选择信任。

## 步骤 1.4 新建 `E:\01所有项目\06定职讲师\00insaragent\pi-insar\test\provider.test.ts`

用途:两分支契约——真实 llm.json 在位时注册成功(**读真实文件,断言结构,绝不打印 key**);缺失时优雅跳过。可直接落盘:

```ts
import { describe, expect, it } from "vitest";
import {
  installProvider,
  loadLlmConfig,
  locateLlmConfig,
  PROVIDER_ID,
} from "../src/provider.ts";

type Registered = { id: string; config: Record<string, unknown> };

function fakePi(): { pi: never; calls: Registered[] } {
  const calls: Registered[] = [];
  const pi = {
    registerProvider: (id: string, config: Record<string, unknown>) => {
      calls.push({ id, config });
    },
  };
  return { pi: pi as never, calls };
}

describe("insar-llm provider registration", () => {
  const configPath = locateLlmConfig();

  it.runIf(configPath !== undefined)(
    "registers the real workspace/llm.json endpoint (key stays opaque)",
    () => {
      const { pi, calls } = fakePi();
      expect(installProvider(pi)).toBe(true);
      expect(calls).toHaveLength(1);
      expect(calls[0]!.id).toBe(PROVIDER_ID);
      const config = calls[0]!.config as {
        baseUrl: string;
        apiKey: string;
        api: string;
        models: Array<{ id: string; contextWindow: number }>;
      };
      expect(config.api).toBe("openai-completions");
      expect(config.baseUrl.startsWith("http")).toBe(true);
      expect(config.models.length).toBeGreaterThanOrEqual(1);
      // 密钥纪律:只断言存在性,值不进任何断言消息/快照。
      expect(typeof config.apiKey).toBe("string");
      expect(config.apiKey.length).toBeGreaterThan(0);
    },
  );

  it.runIf(configPath === undefined)("skips silently when llm.json is absent", () => {
    const { pi, calls } = fakePi();
    expect(installProvider(pi)).toBe(false);
    expect(calls).toHaveLength(0);
  });

  it("returns undefined for unreadable config paths", () => {
    expect(loadLlmConfig("Z:\\definitely\\missing\\llm.json")).toBeUndefined();
  });
});
```

## 步骤 1.5 修改 `E:\01所有项目\06定职讲师\00insaragent\pi-insar\README.md`

新增 "LLM 供应商" 小节:说明 insar-llm 来自 `workspace/llm.json`、`INSAR_LLM_CONFIG` 可覆盖路径、`.pi/settings.json` 提供默认选中、无 llm.json 时 pi 照常启动(仅无此供应商)。

## 验收(真实 key 真实调用,无任何 mock)

```powershell
cd E:\01所有项目\06定职讲师\00insaragent\pi-insar
npx tsc --noEmit; npx vitest run                          # 全绿(provider.test 走真实 llm.json 分支)
cd ..
pi -e pi-insar/src/index.ts --list-models | Select-String "insar-llm"   # 无成本验证注册可见
$env:INSAR_PI_SKIP_HEALTH = "1"
pwsh scripts/insar-pi.ps1 -p "只回复两个字:确认"           # 真实 LLM 一回合,应输出"确认"
Remove-Item Env:INSAR_PI_SKIP_HEALTH
```

检查 pi 输出全程无 key 明文(供应商注册路径不打印 apiKey)。

## git 提交

```powershell
git add pi-insar/src/provider.ts pi-insar/src/index.ts pi-insar/test/provider.test.ts pi-insar/README.md .pi/settings.json
git commit -m "feat(pi-insar): insar-llm 供应商 —— workspace/llm.json 直连 pi(零新密钥)"
```

## 常见坑与处置

- **`--list-models` 看不到 insar-llm**:多半是 llm.json 路径没找到 → `installProvider` 返回 false 是静默的;先 `node -e "console.log(require('fs').existsSync('workspace/llm.json'))"` 确认;或设 `INSAR_LLM_CONFIG` 绝对路径。
- **一回合调用 401/404**:`base_url` 末尾多斜杠或缺 `/v1` → provider.ts 已去尾斜杠,检查 llm.json 的 `base_url` 是否为完整 OpenAI 兼容根(形如 `https://…/v1`)。
- **信任弹窗打断 `-p` 非交互**:先交互式启动一次 pi 并信任项目,或临时加 pi 官方信任旗标(以 `pi --help` 为准)。

## 完成标志

- [ ] `tsc`/`vitest` 全绿(provider.test 真实分支通过)
- [ ] `pi --list-models` 可见 insar-llm
- [ ] 真实一回合 `-p` 输出正常且无 key 泄露
- [ ] 本 Phase 已提交,`git status` 干净

→ 下一个文件:`12-phase03-real-backend.md`
