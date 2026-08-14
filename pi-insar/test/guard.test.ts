import type { ExtensionAPI, ExtensionContext, ToolCallEvent, ToolCallEventResult } from "@earendil-works/pi-coding-agent";
import { describe, expect, it } from "vitest";
import type { BackendClient } from "../src/backendClient.ts";
import { decideBlock, installGuard, STRICT_ALLOWED_TOOLS } from "../src/guard.ts";
import { ModeController } from "../src/mode.ts";

const BUILTINS = ["bash", "write", "edit", "grep", "find", "ls", "read"];

describe("decideBlock", () => {
  it("never blocks in free mode", () => {
    for (const name of [...BUILTINS, "insar_execute_run", "some_other_extension_tool"]) {
      expect(decideBlock(name, "free")).toEqual({ block: false });
    }
  });

  it("allows read and every insar_ tool in strict mode", () => {
    for (const name of [...STRICT_ALLOWED_TOOLS, "insar_execute_run", "insar_read_log", "insar_"]) {
      expect(decideBlock(name, "strict").block).toBe(false);
    }
  });

  it("blocks mutating built-ins in strict mode with a teaching reason", () => {
    for (const name of ["bash", "write", "edit", "grep", "find", "ls", "third_party_tool"]) {
      const decision = decideBlock(name, "strict");
      expect(decision.block).toBe(true);
      expect(decision.reason).toContain(name);
      expect(decision.reason).toContain("insar_execute_run");
      expect(decision.reason).toContain("/insar-mode free");
    }
  });

  it("treats an unknown mode as free rather than blocking", () => {
    expect(decideBlock("bash", "banana").block).toBe(false);
    expect(decideBlock("bash", undefined as unknown as string).block).toBe(false);
  });

  it("tolerates a missing tool name", () => {
    expect(decideBlock("", "strict").block).toBe(false);
    expect(decideBlock(undefined as unknown as string, "strict").block).toBe(false);
  });
});

type ToolCallHandler = (
  event: ToolCallEvent,
  ctx: ExtensionContext,
) => Promise<ToolCallEventResult | void> | ToolCallEventResult | void;

function fakePi(): { pi: ExtensionAPI; handler: () => ToolCallHandler } {
  let captured: ToolCallHandler | undefined;
  const pi = {
    on: (event: string, handler: ToolCallHandler) => {
      if (event === "tool_call") captured = handler;
    },
  } as unknown as ExtensionAPI;
  return {
    pi,
    handler: () => {
      if (!captured) throw new Error("tool_call handler was not registered");
      return captured;
    },
  };
}

function toolCall(toolName: string): ToolCallEvent {
  return { type: "tool_call", toolCallId: "call-1", toolName, input: {} } as unknown as ToolCallEvent;
}

const NO_CTX = undefined as unknown as ExtensionContext;

describe("installGuard", () => {
  it("blocks only in strict mode and only for non-insar, non-read tools", async () => {
    const { pi, handler } = fakePi();
    const controller = new ModeController({} as BackendClient, "free");
    installGuard(pi, controller);

    expect(await handler()(toolCall("bash"), NO_CTX)).toBeUndefined();

    await controller.set("strict");
    const blocked = await handler()(toolCall("bash"), NO_CTX);
    expect(blocked).toMatchObject({ block: true });
    expect((blocked as ToolCallEventResult).reason).toContain("strict reproducible mode");

    expect(await handler()(toolCall("read"), NO_CTX)).toBeUndefined();
    expect(await handler()(toolCall("insar_execute_run"), NO_CTX)).toBeUndefined();

    await controller.set("free");
    expect(await handler()(toolCall("bash"), NO_CTX)).toBeUndefined();
  });

  it("fails open when the mode lookup throws", async () => {
    const { pi, handler } = fakePi();
    const exploding = {
      current: () => {
        throw new Error("mode cache exploded");
      },
    } as unknown as ModeController;
    installGuard(pi, exploding);

    // pi's tool_call hook fails closed on a thrown handler, so a bug here would
    // block built-ins for a user who never asked for strict mode.
    expect(await handler()(toolCall("bash"), NO_CTX)).toBeUndefined();
  });

  it("survives a malformed tool_call event", async () => {
    const { pi, handler } = fakePi();
    installGuard(pi, new ModeController({} as BackendClient, "strict"));
    expect(await handler()({} as ToolCallEvent, NO_CTX)).toBeUndefined();
  });
});

describe("ModeController", () => {
  it("keeps the in-memory mode when the backend is unreachable", async () => {
    const failing = {
      setMode: () => Promise.reject(new Error("connection refused")),
      getMode: () => Promise.reject(new Error("connection refused")),
    } as unknown as BackendClient;
    const controller = new ModeController(failing, "free");
    controller.noteSession("s1");

    const outcome = await controller.set("strict");
    expect(outcome).toMatchObject({ mode: "strict", persisted: false, session: "s1" });
    expect(controller.current()).toBe("strict");
    expect(decideBlock("bash", controller.current()).block).toBe(true);
  });

  it("adopts a mode observed in a backend payload and ignores garbage", () => {
    const controller = new ModeController({} as BackendClient, "free");
    controller.observeRemote("strict");
    expect(controller.current()).toBe("strict");
    controller.observeRemote("nonsense");
    expect(controller.current()).toBe("strict");
  });
});
