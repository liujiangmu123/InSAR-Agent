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
