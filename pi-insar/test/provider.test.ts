import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
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

  it("prefers INSAR_LLM_CONFIG over the repo-relative path", () => {
    const dir = mkdtempSync(join(tmpdir(), "pi-insar-llm-"));
    try {
      const explicit = join(dir, "llm.json");
      writeFileSync(explicit, "{}");
      expect(locateLlmConfig({ INSAR_LLM_CONFIG: explicit }, dir)).toBe(explicit);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it("walks up from cwd for workspace/llm.json when the repo path is absent", () => {
    const root = mkdtempSync(join(tmpdir(), "pi-insar-llm-walk-"));
    try {
      const nested = join(root, "a", "b", "c");
      mkdirSync(nested, { recursive: true });
      mkdirSync(join(root, "workspace"));
      const llm = join(root, "workspace", "llm.json");
      writeFileSync(llm, "{}");
      const missingRepo = join(root, "no-such-repo");
      expect(locateLlmConfig({}, nested, missingRepo)).toBe(llm);
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });

  it("prefers the repo-relative path over a cwd walk candidate", () => {
    const root = mkdtempSync(join(tmpdir(), "pi-insar-llm-prio-"));
    try {
      const repo = join(root, "repo");
      const cwd = join(root, "here");
      mkdirSync(join(repo, "workspace"), { recursive: true });
      mkdirSync(join(cwd, "workspace"), { recursive: true });
      const repoLlm = join(repo, "workspace", "llm.json");
      writeFileSync(repoLlm, "{}");
      writeFileSync(join(cwd, "workspace", "llm.json"), "{}");
      expect(locateLlmConfig({}, cwd, repo)).toBe(repoLlm);
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });
});
