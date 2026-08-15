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
