import { describe, expect, it } from "vitest";
import { BackendClient } from "../src/backendClient.ts";
import { installJournal } from "../src/journal.ts";
import { ModeController } from "../src/mode.ts";

type ToolResultHandler = (event: {
  toolName: string;
  input: unknown;
  isError: boolean;
}) => unknown;

function fakePi(): { pi: never; handlers: Map<string, ToolResultHandler> } {
  const handlers = new Map<string, ToolResultHandler>();
  const pi = { on: (name: string, handler: ToolResultHandler) => handlers.set(name, handler) };
  return { pi: pi as never, handlers };
}

describe("pi-journal wiring (live backend)", () => {
  it("mirrors a tool_result into INSAR_HOME/pi_journal.ndjson", async () => {
    const client = new BackendClient({});             // INSAR_API_BASE 来自 globalSetup
    await client.createSession("sess-journal");
    const controller = new ModeController(client);
    controller.noteSession("sess-journal");

    const { pi, handlers } = fakePi();
    installJournal(pi, client, controller);
    const handler = handlers.get("tool_result");
    expect(handler).toBeDefined();

    handler!({ toolName: "bash", input: { command: "git status" }, isError: false });

    // fire-and-forget 落盘:轮询直至可见(上限 ~5s)。
    let total = 0;
    for (let i = 0; i < 20 && total === 0; i += 1) {
      await new Promise((wake) => setTimeout(wake, 250));
      const response = await fetch(`${client.baseUrl}/api/pi-journal`);
      total = ((await response.json()) as { total: number }).total;
    }
    expect(total).toBeGreaterThanOrEqual(1);
  });
});
