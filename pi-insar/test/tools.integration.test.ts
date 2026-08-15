/**
 * End-to-end drive of the insar_* tools against a real FastAPI backend spawned
 * by test/backend.globalSetup.ts. No LLM key is needed: /api/turn plans through
 * the rules path and the missing engines make the executor run simulated.
 *
 * The tools are plain objects, so `execute()` is called directly — no pi
 * runtime, no model.
 */

import type { ExtensionContext } from "@earendil-works/pi-coding-agent";
import { beforeAll, describe, expect, it } from "vitest";
import { BackendClient, type MonitorResponse } from "../src/backendClient.ts";
import { decideBlock } from "../src/guard.ts";
import { ModeController } from "../src/mode.ts";
import { createInsarTools, type InsarTool, toolsByName } from "../src/tools.ts";

const BASE_URL =
  process.env.INSAR_API_BASE ?? `http://127.0.0.1:${process.env.INSAR_TEST_PORT ?? 8899}`;

const SESSION = `pi-insar-it-${process.pid}`;
const NO_CTX = undefined as unknown as ExtensionContext;

const client = new BackendClient({ baseUrl: BASE_URL, timeoutMs: 60_000 });
const controller = new ModeController(client);
let tools: Map<string, InsarTool>;

interface ToolOutcome {
  text: string;
  details: unknown;
}

async function call(name: string, args: Record<string, unknown> = {}): Promise<ToolOutcome> {
  const tool = tools.get(name);
  if (!tool) throw new Error(`tool ${name} is not registered`);
  const outcome = await tool.execute(`test-${name}`, args, undefined, undefined, NO_CTX);
  const text = outcome.content
    .map((part) => (part.type === "text" ? part.text : `[${part.type}]`))
    .join("\n");
  return { text, details: outcome.details };
}

let runId = "";

beforeAll(() => {
  tools = toolsByName(createInsarTools(client, controller));
});

describe("insar_* tools against a live backend", () => {
  it("registers every tool under the insar_ prefix", () => {
    expect(tools.size).toBe(25);
    for (const name of tools.keys()) expect(name.startsWith("insar_")).toBe(true);
    // Overriding a built-in would replace it for the whole pi session.
    for (const builtin of ["read", "bash", "write", "edit", "grep", "find", "ls"]) {
      expect(tools.has(builtin)).toBe(false);
    }
  });

  it("insar_health reports a reachable backend", async () => {
    const { text, details } = await call("insar_health");
    expect(text).toContain("InSAR backend ok");
    expect(details).toMatchObject({ ok: true });
  });

  it("insar_create_session creates the session and binds the mode controller", async () => {
    const { text, details } = await call("insar_create_session", {
      session: SESSION,
      name: "pi-insar integration",
    });
    expect(text).toContain(`Session ${SESSION} ready`);
    expect(details).toMatchObject({ session_id: SESSION });
    expect(controller.activeSession()).toBe(SESSION);
  });

  it("insar_list_sessions includes the new session", async () => {
    const { details } = await call("insar_list_sessions");
    const sessions = details as Array<{ session_id: string }>;
    expect(sessions.map((row) => row.session_id)).toContain(SESSION);
  });

  it("insar_plan_run plans an 11-step run without an LLM key", async () => {
    const { text, details } = await call("insar_plan_run", {
      session: SESSION,
      text: "Ridgecrest 地震同震形变",
    });
    const payload = details as { run: { run_id: string; scenario: string }; monitor: MonitorResponse };
    runId = payload.run.run_id;

    expect(runId).toMatch(/^\d{8}T\d{6}-/);
    expect(payload.monitor.steps).toHaveLength(11);
    expect(payload.monitor.run?.status).toBe("ready");
    expect(text).toContain(`Planned run ${runId}`);
    expect(text).toContain("Execute with insar_execute_run");
  });

  it("insar_run_status reports the planned run before execution", async () => {
    const { text, details } = await call("insar_run_status", { session: SESSION, run_id: runId });
    const monitor = details as MonitorResponse;
    expect(monitor.run?.run_id).toBe(runId);
    expect(monitor.progress.pct).toBeLessThan(100);
    expect(text).toContain("insar · ");
    expect(text).toContain("Steps:");
  });

  it("insar_preview_change previews an impact without mutating the run", async () => {
    const { text, details } = await call("insar_preview_change", {
      session: SESSION,
      run_id: runId,
      step: 7,
      method: "mintpy_sbas",
    });
    expect(details).toMatchObject({ changedStep: 7 });
    expect(text).toContain("Step 7:");

    const monitor = await client.monitor(SESSION, runId);
    expect(monitor.run?.status).toBe("ready");
  });

  it("insar_execute_run runs the simulated pipeline to completion", async () => {
    const { text, details } = await call("insar_execute_run", { session: SESSION, run_id: runId });
    const monitor = (details as { monitor: MonitorResponse }).monitor;
    expect(monitor.run?.status).toBe("done");
    expect(monitor.progress.pct).toBe(100);
    expect(text).toContain("Execution finished");
  });

  it("insar_run_status shows a finished run with runnable evidence", async () => {
    const { details } = await call("insar_run_status", { session: SESSION, run_id: runId });
    const monitor = details as MonitorResponse;
    expect(monitor.run?.status).toBe("done");
    expect(monitor.progress).toMatchObject({ total: 11, done: 11, pct: 100 });
    // Engines are absent, so the run was simulated and the ladder is capped.
    expect(monitor.run?.simulated).toBe(true);
    expect(monitor.evidence?.level).toBe("runnable");
    expect(monitor.taints).toBe(0);
  });

  it("insar_read_log returns the tail of a step log", async () => {
    const { text, details } = await call("insar_read_log", {
      session: SESSION,
      run_id: runId,
      step: 1,
      tail_kb: 4,
    });
    expect(text).toContain("Step 1 log");
    expect((details as { text: string }).text.length).toBeGreaterThan(0);
  });

  it("insar_export_provenance returns the ledger and the run.sh script", async () => {
    const ledger = await call("insar_export_provenance", {
      session: SESSION,
      run_id: runId,
      kind: "ledger",
    });
    const doc = ledger.details as Record<string, unknown>;
    expect(doc["run_id"]).toBe(runId);
    expect(doc["evidence_level"]).toBe("runnable");
    expect(ledger.text).toContain("evidence level runnable");

    const script = await call("insar_export_provenance", {
      session: SESSION,
      run_id: runId,
      kind: "run_sh",
    });
    expect(script.text.startsWith("#!/usr/bin/env bash")).toBe(true);
    expect(script.text).toContain(runId);
  });

  it("insar_apply_change queues a method change for the next run", async () => {
    const { text, details } = await call("insar_apply_change", {
      session: SESSION,
      run_id: runId,
      step: 7,
      method: "mintpy_sbas",
      deliver_as: "next_run",
    });
    expect(text).toContain("Queued SET_METHOD on step 7");
    const queued = (details as { queued: Array<{ response: { accepted: boolean } }> }).queued;
    expect(queued[0]?.response.accepted).toBe(true);
  });

  it("insar_apply_change rejects a call with nothing to change", async () => {
    await expect(
      call("insar_apply_change", { session: SESSION, run_id: runId, step: 7, deliver_as: "steer" }),
    ).rejects.toThrow(/at least one of method or params_json/);
  });

  it("insar_apply_change rejects malformed params_json before touching the backend", async () => {
    await expect(
      call("insar_apply_change", {
        session: SESSION,
        run_id: runId,
        step: 7,
        params_json: "not json",
        deliver_as: "steer",
      }),
    ).rejects.toThrow(/params_json is not valid JSON/);
  });

  it("insar_fork_run branches the finished run and reuses its steps", async () => {
    const { text, details } = await call("insar_fork_run", {
      session: SESSION,
      parent_run_id: runId,
      changes_json: JSON.stringify({ 7: { method: "mintpy_sbas" } }),
    });
    const fork = details as { runId: string; steps: unknown[] };
    expect(fork.runId).toContain("-fork");
    expect(fork.steps).toHaveLength(11);
    expect(text).toContain(`Forked ${runId} ->`);
  });

  it("insar_env_probe reports the (engine-less) environment", async () => {
    const { text, details } = await call("insar_env_probe", { session: SESSION });
    expect(details).toHaveProperty("probe");
    expect(text).toContain("Engines available:");
  });

  it("insar_list_datasets returns the dataset catalogue", async () => {
    const { details } = await call("insar_list_datasets");
    expect(details).toHaveProperty("datasets");
  });

  it("insar_intervene queues a step-scoped action and rejects a missing target", async () => {
    const { text, details } = await call("insar_intervene", {
      session: SESSION,
      run_id: runId,
      action: "SKIP",
      target: 9,
    });
    expect(text).toContain("Queued SKIP on step 9");
    expect(details).toMatchObject({ accepted: true });

    await expect(
      call("insar_intervene", { session: SESSION, run_id: runId, action: "RESET" }),
    ).rejects.toThrow(/RESET needs target/);
  });

  it("insar_set_mode / insar_get_mode flip the freedom mode and drive the gate", async () => {
    expect(decideBlock("bash", controller.current()).block).toBe(false);

    const strict = await call("insar_set_mode", { session: SESSION, mode: "strict" });
    expect(strict.text).toContain("is now strict");
    expect(controller.current()).toBe("strict");
    expect(decideBlock("bash", controller.current()).block).toBe(true);
    expect(decideBlock("insar_execute_run", controller.current()).block).toBe(false);
    expect(decideBlock("read", controller.current()).block).toBe(false);

    const readBack = await call("insar_get_mode", { session: SESSION });
    expect(readBack.details).toMatchObject({ session: SESSION, mode: "strict" });
    // The monitor payload carries the same mode, which is what the sidebar shows.
    expect((await client.monitor(SESSION, runId)).mode).toBe("strict");

    const free = await call("insar_set_mode", { session: SESSION, mode: "free" });
    expect(free.text).toContain("is now free");
    expect(controller.current()).toBe("free");
    expect(decideBlock("bash", controller.current()).block).toBe(false);
  });

  it("insar_intervene KILL cancels through the control plane", async () => {
    const { text, details } = await call("insar_intervene", {
      session: SESSION,
      run_id: runId,
      action: "KILL",
    });
    expect(text).toContain("Cancellation requested");
    expect(details).toMatchObject({ accepted: true });
  });

  it("surfaces backend errors as thrown tool errors", async () => {
    await expect(
      call("insar_export_provenance", { session: SESSION, run_id: "no-such-run", kind: "ledger" }),
    ).rejects.toThrow(/HTTP 404/);
  });

  it("reports an unknown run as an empty monitor shell, matching /api/monitor", async () => {
    // /api/monitor only 404s for a run owned by another session; an id that does
    // not exist at all degrades to the empty shell so the sidebar keeps polling.
    const { text, details } = await call("insar_run_status", {
      session: SESSION,
      run_id: "no-such-run",
    });
    expect((details as MonitorResponse).run).toBeNull();
    expect(text).toContain("has no run yet");
  });

  it("insar_resume returns a clean stream when nothing is running", async () => {
    const { text } = await call("insar_resume", { session: SESSION });
    expect(text).toContain("Resume finished");
  });

  it("insar_view_figure lists real figure artifacts (possibly none) and rejects unknown names", async () => {
    const listing = await call("insar_view_figure", { session: SESSION });
    expect(listing.text).toMatch(/figures|no figure artifacts/i);
    const missing = await call("insar_view_figure", { session: SESSION, name: "no-such-figure.png" });
    expect(missing.text).toContain("No figure named");
  });

  it("insar_run_trace renders a per-step timeline for a finished run", async () => {
    const { text, details } = await call("insar_run_trace", { session: SESSION, run_id: runId });
    expect(Array.isArray(details)).toBe(true);
    expect((details as unknown[]).length).toBeGreaterThan(0);
    expect(text).toMatch(/step → phase → duration → key event/);
  });

  it("insar_run_trace honestly reports no run when the session has none", async () => {
    const emptySession = `${SESSION}-norun`;
    await call("insar_create_session", { session: emptySession, name: "empty-trace" });
    const outcome = await call("insar_run_trace", { session: emptySession });
    expect(outcome.text).toMatch(/404|no run/i);
  });

  it("insar_capabilities returns the closed method set", async () => {
    const out = await call("insar_capabilities", {});
    expect(out.text).toContain("mintpy_sbas");
    expect(out.text).toContain("methods (closed set)");
  });

  it("insar_read_skill returns step knowledge", async () => {
    const out = await call("insar_read_skill", { step_id: 7 });
    expect(out.text.length).toBeGreaterThan(200);
  });

  it("insar_timeseries_point fails honestly when there is no timeseries", async () => {
    await expect(call("insar_timeseries_point", { session: SESSION, lat: 35.7, lon: -117.6 })).rejects.toThrow(
      /no timeseries|placeholder|404/i,
    );
  });

  it("insar_list_artifacts round-trips the ledger listing", async () => {
    const { text, details } = await call("insar_list_artifacts", { session: SESSION, run_id: runId });
    expect(details).toHaveProperty("steps");
    expect(text).toMatch(/artifact|no artifacts/i);
  });

  it("insar_doctor returns a live environment report", async () => {
    const { text, details } = await call("insar_doctor");
    expect(details).toHaveProperty("status");
    expect(details).toHaveProperty("results");
    expect(text).toMatch(/^(FAIL:|WARN:|OK:)/);
  });

  it("insar_recommend_route fails honestly for an unknown dataset", async () => {
    await expect(call("insar_recommend_route", { dataset_id: "no-such-dataset" })).rejects.toThrow(/404/);
  });
});
