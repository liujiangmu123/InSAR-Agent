/**
 * The `insar_*` tool layer: high-level, ledger-aware operations the model calls
 * instead of hand-rolling shell commands.
 *
 * Every name is prefixed `insar_` on purpose — registering `read`/`bash`/`write`
 * would override pi's built-ins for the whole session. This extension is purely
 * additive: it adds tools, never removes pi's own.
 *
 * Tool results follow pi's shape: `content` is the short text the model reads,
 * `details` carries the full JSON for rendering and later inspection.
 */

import type { ToolDefinition } from "@earendil-works/pi-coding-agent";
import { type Static, type TSchema, Type } from "typebox";
import type {
  ActionName,
  BackendClient,
  DeliverAs,
  MonitorResponse,
  StreamResult,
} from "./backendClient.ts";
import { ACTIONS, DELIVER_AS, FREEDOM_MODES } from "./backendClient.ts";
import { isFreedomMode, type ModeController } from "./mode.ts";
import { renderPipelineRail } from "./sidebar.ts";

/** Any tool this module produces, erased to a single storable type. */
export type InsarTool = ToolDefinition<TSchema, unknown, unknown>;

/** Text budget for the `content` a tool hands to the model (pi's cap is 50KB). */
export const MAX_CONTENT_BYTES = 24_000;

/** Events retained in `details` from an NDJSON stream. */
const MAX_DETAIL_EVENTS = 40;

/**
 * `{ type: "string", enum: [...] }` — the only enum encoding Google's API
 * accepts. pi exports `StringEnum` for this, but it lives in `@earendil-works/pi-ai`;
 * inlining keeps this package's runtime dependencies to typebox alone.
 */
function StringEnum<const T extends readonly string[]>(
  values: T,
  description: string,
): ReturnType<typeof Type.Unsafe<T[number]>> {
  return Type.Unsafe<T[number]>({ type: "string", enum: [...values], description });
}

const SessionParam = Type.String({
  description: "InSAR backend session id (create it with insar_create_session)",
});

const RunIdParam = Type.Optional(
  Type.String({ description: "Run id; omit to use the session's most recent run" }),
);

const StepParam = Type.Integer({
  minimum: 1,
  description: "Pipeline step number (1-11)",
});

const ParamsJsonParam = Type.Optional(
  Type.String({
    description:
      'Parameter patch as a JSON object string, e.g. {"looks_range": 4}. Only the named parameters change.',
  }),
);

export function truncate(text: string, maxBytes = MAX_CONTENT_BYTES): string {
  if (Buffer.byteLength(text, "utf8") <= maxBytes) return text;
  const buffer = Buffer.from(text, "utf8").subarray(0, maxBytes);
  // Never split a multi-byte character: `utf8` decoding of a cut buffer would
  // emit a replacement char, which is noise in the model's context.
  const decoded = new TextDecoder("utf-8", { fatal: false }).decode(buffer).replace(/\uFFFD$/, "");
  return `${decoded}\n[truncated to ${maxBytes} bytes]`;
}

function result(text: string, details: unknown): { content: [{ type: "text"; text: string }]; details: unknown } {
  return { content: [{ type: "text", text: truncate(text) }], details };
}

function parseJsonObject(raw: string, label: string): Record<string, unknown> {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch (error) {
    throw new Error(`${label} is not valid JSON: ${(error as Error).message}`);
  }
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error(`${label} must be a JSON object, got ${Array.isArray(parsed) ? "array" : typeof parsed}`);
  }
  return parsed as Record<string, unknown>;
}

/** One-line-per-step digest used by the planning/status tools. */
export function summarizeSteps(monitor: MonitorResponse): string {
  return monitor.steps
    .map((step) => {
      const flags = [
        step.state,
        step.stale ? "stale" : undefined,
        step.failure_class ?? undefined,
      ]
        .filter(Boolean)
        .join("/");
      return `  ${String(step.step).padStart(2, "0")} ${step.name} · ${step.method ?? "-"} · ${flags}`;
    })
    .join("\n");
}

export function summarizeMonitor(monitor: MonitorResponse): string {
  return renderPipelineRail(monitor).join("\n");
}

/** Pull the human-facing lines out of an NDJSON turn stream. */
function narrateStream(stream: StreamResult): string {
  const lines: string[] = [];
  for (const event of stream.events) {
    const kind = event["t"];
    if (kind === "say" && Array.isArray(event["parts"])) {
      lines.push(...(event["parts"] as unknown[]).map((part) => String(part)));
    } else if (kind === "note" && typeof event["text"] === "string") {
      lines.push(`note: ${event["text"] as string}`);
    }
  }
  return lines.join("\n");
}

function tailEvents(stream: StreamResult): unknown[] {
  return stream.events.slice(-MAX_DETAIL_EVENTS);
}

function streamNote(stream: StreamResult): string {
  const parts = [`${stream.total} stream events`];
  if (stream.truncated) parts.push(`${stream.events.length} kept`);
  if (stream.malformed > 0) parts.push(`${stream.malformed} malformed`);
  return parts.join(", ");
}

function typed<T extends TSchema>(definition: ToolDefinition<T, unknown, unknown>): InsarTool {
  return definition as unknown as InsarTool;
}

/**
 * Build every `insar_*` tool against a backend client and the shared mode
 * controller. Returned as plain objects so they can be driven directly in tests
 * without a pi runtime.
 */
export function createInsarTools(client: BackendClient, controller: ModeController): InsarTool[] {
  /** Every session-scoped call binds the gate/sidebar to that session. */
  const bind = (session: string): string => {
    controller.noteSession(session);
    return session;
  };

  const health = typed({
    name: "insar_health",
    label: "InSAR Health",
    description:
      "Check that the InSAR backend is reachable and report its version. Run this first if any other insar_ tool fails.",
    parameters: Type.Object({}),
    async execute(_toolCallId, _params, signal) {
      const response = await client.health(signal);
      return result(
        `InSAR backend ${response.ok ? "ok" : "degraded"} · version ${response.version} · ${client.baseUrl}`,
        { ...response, baseUrl: client.baseUrl },
      );
    },
  });

  const createSession = typed({
    name: "insar_create_session",
    label: "InSAR Create Session",
    description:
      "Create (or re-open) an InSAR backend session. A session owns its runs, workspace and freedom mode. Use a short slug id without path separators.",
    parameters: Type.Object({
      session: SessionParam,
      name: Type.Optional(Type.String({ description: "Human-readable display name" })),
    }),
    async execute(_toolCallId, params, signal) {
      const args = params as { session: string; name?: string };
      const row = await client.createSession(bind(args.session), args.name, signal);
      return result(`Session ${row.session_id} ready (name: ${row.name}, mode: ${row.mode}).`, row);
    },
  });

  const listSessions = typed({
    name: "insar_list_sessions",
    label: "InSAR List Sessions",
    description: "List the InSAR backend sessions (excluding archived ones).",
    parameters: Type.Object({}),
    async execute(_toolCallId, _params, signal) {
      const rows = await client.listSessions(false, signal);
      const text =
        rows.length === 0
          ? "No InSAR sessions yet. Create one with insar_create_session."
          : `${rows.length} session(s):\n` +
            rows.map((row) => `  ${row.session_id} · ${row.name} · mode ${row.mode}`).join("\n");
      return result(text, rows);
    },
  });

  const planRun = typed({
    name: "insar_plan_run",
    label: "InSAR Plan Run",
    description:
      "Plan an InSAR run from a natural-language request (region, time window, target). The backend parses the intent, probes the environment and writes an 11-step plan; nothing is executed yet. Returns the run id and the planned steps.",
    parameters: Type.Object({
      session: SessionParam,
      text: Type.String({
        description:
          'Natural-language request, e.g. "Ridgecrest coseismic deformation, 2019-06-10 to 2019-08-15".',
      }),
    }),
    async execute(_toolCallId, params, signal) {
      const args = params as { session: string; text: string };
      const session = bind(args.session);
      const stream = await client.turn(session, args.text, signal);
      const run = await client.latestRun(session, signal);
      if (!run) {
        throw new Error(
          `Planning produced no run for session ${session} (${streamNote(stream)}). ` +
            "Check the backend logs; the turn stream may have reported an error note.",
        );
      }
      const monitor = await client.monitor(session, run.run_id, signal);
      const narration = narrateStream(stream);
      const text = [
        `Planned run ${run.run_id} (scenario ${run.scenario ?? "unscoped"}${run.simulated ? ", simulated" : ""}).`,
        summarizeMonitor(monitor),
        "Steps:",
        summarizeSteps(monitor),
        narration ? `\n${narration}` : "",
        `\nExecute with insar_execute_run (${streamNote(stream)}).`,
      ]
        .filter(Boolean)
        .join("\n");
      return result(text, { run, monitor, events: tailEvents(stream) });
    },
  });

  const runStatus = typed({
    name: "insar_run_status",
    label: "InSAR Run Status",
    description:
      "Current state of a run: per-step stage/state, progress, evidence level, freedom mode and stale (taint) count. This is the same data the sidebar renders.",
    parameters: Type.Object({ session: SessionParam, run_id: RunIdParam }),
    async execute(_toolCallId, params, signal) {
      const args = params as { session: string; run_id?: string };
      const monitor = await client.monitor(bind(args.session), args.run_id, signal);
      controller.observeRemote(monitor.mode);
      if (!monitor.run) {
        return result(
          `Session ${monitor.session} has no run yet. Plan one with insar_plan_run.`,
          monitor,
        );
      }
      return result(`${summarizeMonitor(monitor)}\nSteps:\n${summarizeSteps(monitor)}`, monitor);
    },
  });

  const executeRun = typed({
    name: "insar_execute_run",
    label: "InSAR Execute Run",
    description:
      "Execute a planned run (or only the given step ids) to completion and return the final pipeline state. Long-running: the call returns when the backend's execution stream ends. Prefer this over running processing commands in bash.",
    parameters: Type.Object({
      session: SessionParam,
      run_id: RunIdParam,
      step_ids: Type.Optional(
        Type.Array(Type.Integer({ minimum: 1 }), {
          description: "Subset of planned step numbers to run; omit to run everything pending",
        }),
      ),
    }),
    async execute(_toolCallId, params, signal) {
      const args = params as { session: string; run_id?: string; step_ids?: number[] };
      const session = bind(args.session);
      const stream = await client.pipeline(session, args.run_id, args.step_ids, signal);
      const monitor = await client.monitor(session, args.run_id, signal);
      controller.observeRemote(monitor.mode);
      const failures = monitor.steps.filter((step) => step.state === "failed");
      const text = [
        `Execution finished: run ${monitor.run?.run_id ?? "?"} is ${monitor.run?.status ?? "unknown"} (${streamNote(stream)}).`,
        summarizeMonitor(monitor),
        failures.length > 0
          ? `Failed steps:\n${failures
              .map((step) => `  ${step.step} ${step.name} · ${step.failure_class ?? "unknown"} · exit ${step.exit_code ?? "?"}`)
              .join("\n")}\nInspect one with insar_read_log.`
          : "",
      ]
        .filter(Boolean)
        .join("\n");
      return result(text, { monitor, events: tailEvents(stream) });
    },
  });

  const previewChange = typed({
    name: "insar_preview_change",
    label: "InSAR Preview Change",
    description:
      "Dry-run a method or parameter change: reports which downstream steps it invalidates and the estimated rerun cost. Nothing is modified. Always preview before insar_apply_change.",
    parameters: Type.Object({
      session: SessionParam,
      run_id: RunIdParam,
      step: StepParam,
      method: Type.Optional(Type.String({ description: "Candidate method id for the step" })),
      params_json: ParamsJsonParam,
    }),
    async execute(_toolCallId, params, signal) {
      const args = params as {
        session: string;
        run_id?: string;
        step: number;
        method?: string;
        params_json?: string;
      };
      const patch =
        args.params_json === undefined ? undefined : parseJsonObject(args.params_json, "params_json");
      const impact = await client.impact(bind(args.session), args.step, {
        runId: args.run_id,
        method: args.method,
        params: patch,
        signal,
      });
      const rerun = impact.rerunMinutes === null ? "unknown" : `~${impact.rerunMinutes} min`;
      return result(
        `Step ${impact.changedStep}: ${impact.reason}. Affected steps: ${
          impact.affected.length === 0 ? "none" : JSON.stringify(impact.affected)
        }. Rerun cost ${rerun} (${impact.rerunBasis ?? "no basis"}).`,
        impact,
      );
    },
  });

  const applyChange = typed({
    name: "insar_apply_change",
    label: "InSAR Apply Change",
    description:
      "Queue a method and/or parameter change for a step (SET_METHOD / SET_PARAMS). deliver_as decides when it lands: steer = before the next step, follow_up = after this run, next_run = at the next planning pass.",
    parameters: Type.Object({
      session: SessionParam,
      run_id: RunIdParam,
      step: StepParam,
      method: Type.Optional(Type.String({ description: "New method id for the step" })),
      params_json: ParamsJsonParam,
      deliver_as: StringEnum(DELIVER_AS, "When the change takes effect"),
    }),
    async execute(_toolCallId, params, signal) {
      const args = params as {
        session: string;
        run_id?: string;
        step: number;
        method?: string;
        params_json?: string;
        deliver_as: DeliverAs;
      };
      if (args.method === undefined && args.params_json === undefined) {
        throw new Error("insar_apply_change needs at least one of method or params_json");
      }
      const session = bind(args.session);
      const queued: Array<{ action: ActionName; response: unknown }> = [];
      if (args.method !== undefined) {
        queued.push({
          action: "SET_METHOD",
          response: await client.action(session, "SET_METHOD", {
            runId: args.run_id,
            scope: "step",
            target: args.step,
            payload: { method: args.method },
            deliverAs: args.deliver_as,
            signal,
          }),
        });
      }
      if (args.params_json !== undefined) {
        queued.push({
          action: "SET_PARAMS",
          response: await client.action(session, "SET_PARAMS", {
            runId: args.run_id,
            scope: "step",
            target: args.step,
            payload: { params: parseJsonObject(args.params_json, "params_json") },
            deliverAs: args.deliver_as,
            signal,
          }),
        });
      }
      return result(
        `Queued ${queued.map((entry) => entry.action).join(" + ")} on step ${args.step} (deliver_as ${args.deliver_as}).`,
        { step: args.step, deliver_as: args.deliver_as, queued },
      );
    },
  });

  const intervene = typed({
    name: "insar_intervene",
    label: "InSAR Intervene",
    description:
      "Control a running pipeline: PAUSE/PLAY the run, KILL it (cancel), or RESET/SKIP a single step. RESET and SKIP need the step number in target.",
    parameters: Type.Object({
      session: SessionParam,
      run_id: RunIdParam,
      action: StringEnum(["PAUSE", "PLAY", "KILL", "RESET", "SKIP"] as const, "Intervention to apply"),
      target: Type.Optional(
        Type.Integer({ minimum: 1, description: "Step number; required for RESET and SKIP" }),
      ),
    }),
    async execute(_toolCallId, params, signal) {
      const args = params as {
        session: string;
        run_id?: string;
        action: "PAUSE" | "PLAY" | "KILL" | "RESET" | "SKIP";
        target?: number;
      };
      const session = bind(args.session);
      if (args.action === "KILL") {
        // Cancellation is a control-plane operation: /api/abort sets the cancel
        // token the driver polls, which is what actually stops the run.
        const response = await client.abort(session, args.run_id, signal);
        return result(`Cancellation requested for ${args.run_id ?? "the latest run"}.`, response);
      }
      const stepScoped = args.action === "RESET" || args.action === "SKIP";
      if (stepScoped && args.target === undefined) {
        throw new Error(`${args.action} needs target (the step number)`);
      }
      const response = await client.action(session, args.action as ActionName, {
        runId: args.run_id,
        scope: stepScoped ? "step" : "run",
        target: args.target,
        deliverAs: "steer",
        signal,
      });
      return result(
        `Queued ${args.action}${stepScoped ? ` on step ${args.target}` : ""} (takes effect at the next step boundary).`,
        response,
      );
    },
  });

  const forkRun = typed({
    name: "insar_fork_run",
    label: "InSAR Fork Run",
    description:
      "Branch a run into a new one with per-step method/parameter changes, reusing every unaffected step. Use this to compare processing choices without redoing the whole pipeline.",
    parameters: Type.Object({
      session: SessionParam,
      parent_run_id: Type.String({ description: "Run id to branch from" }),
      changes_json: Type.String({
        description:
          'Per-step changes as a JSON object keyed by step number, e.g. {"5": {"method": "goldstein", "params": {"alpha": 0.6}}}',
      }),
    }),
    async execute(_toolCallId, params, signal) {
      const args = params as { session: string; parent_run_id: string; changes_json: string };
      const changes = parseJsonObject(args.changes_json, "changes_json") as Record<
        string,
        { method?: string; params?: Record<string, unknown> }
      >;
      const fork = await client.fork(bind(args.session), args.parent_run_id, changes, signal);
      const reused = fork.steps.filter((step) => step.state === "done").length;
      return result(
        `Forked ${args.parent_run_id} -> ${fork.runId}: ${fork.steps.length} steps, ${reused} reused. Execute it with insar_execute_run.`,
        fork,
      );
    },
  });

  const exportProvenance = typed({
    name: "insar_export_provenance",
    label: "InSAR Export Provenance",
    description:
      "Export the reproducibility record of a run: kind=ledger returns the provenance document (environment, steps, metrics, thresholds, evidence level), kind=run_sh returns the equivalent bare-command bash script.",
    parameters: Type.Object({
      session: SessionParam,
      run_id: RunIdParam,
      kind: StringEnum(["ledger", "run_sh"] as const, "What to export"),
    }),
    async execute(_toolCallId, params, signal) {
      const args = params as { session: string; run_id?: string; kind: "ledger" | "run_sh" };
      const session = bind(args.session);
      if (args.kind === "run_sh") {
        const script = await client.runScript(session, args.run_id, signal);
        return result(script, { kind: "run_sh", script, bytes: Buffer.byteLength(script, "utf8") });
      }
      const doc = await client.provenance(session, args.run_id, signal);
      const steps = Array.isArray(doc["steps"]) ? (doc["steps"] as unknown[]).length : 0;
      const artifacts = Array.isArray(doc["artifacts"]) ? (doc["artifacts"] as unknown[]).length : 0;
      const evidence = doc["evidence_level"] ?? "unknown";
      return result(
        `Provenance for run ${String(doc["run_id"] ?? args.run_id ?? "latest")}: ` +
          `${steps} steps, ${artifacts} artifacts, evidence level ${String(evidence)}, ` +
          `simulated=${String(doc["simulated"] ?? false)}. Full document in details.`,
        doc,
      );
    },
  });

  const listDatasets = typed({
    name: "insar_list_datasets",
    label: "InSAR List Datasets",
    description:
      "List the SAR datasets discovered under the configured scan roots (id, kind, path, size), so a run can be planned against data that is already on disk.",
    parameters: Type.Object({}),
    async execute(_toolCallId, _params, signal) {
      const response = await client.datasets(signal);
      const count = Array.isArray(response.datasets) ? response.datasets.length : 0;
      const roots = Array.isArray(response.roots) ? response.roots.length : 0;
      return result(
        count === 0
          ? `No datasets found (${roots} scan root(s)). Add data or a scan root before planning a real run.`
          : `${count} dataset(s) across ${roots} scan root(s). Details hold the full listing.`,
        response,
      );
    },
  });

  const envProbe = typed({
    name: "insar_env_probe",
    label: "InSAR Env Probe",
    description:
      "Probe the processing environment for a session: which engines (ISCE2, MintPy, SNAP, snaphu, GDAL...) and credentials are available, plus disk/CPU and the audit thresholds. Missing engines mean runs fall back to simulated execution, which caps the evidence level.",
    parameters: Type.Object({ session: SessionParam }),
    async execute(_toolCallId, params, signal) {
      const args = params as { session: string };
      const response = await client.env(bind(args.session), signal);
      const engines = (response.probe?.["engines"] ?? {}) as Record<string, unknown>;
      const available = Object.entries(engines)
        .filter(([, value]) => value !== null && value !== false)
        .map(([name]) => name);
      const missing = Object.keys(engines).filter((name) => !available.includes(name));
      return result(
        `Engines available: ${available.length > 0 ? available.join(", ") : "none"}. ` +
          `Missing: ${missing.length > 0 ? missing.join(", ") : "none"}. ` +
          `${available.length === 0 ? "Runs will be simulated (evidence capped at runnable)." : ""}`,
        response,
      );
    },
  });

  const readLog = typed({
    name: "insar_read_log",
    label: "InSAR Read Log",
    description:
      "Read the tail of a step's execution log. Use this to diagnose a failed step instead of hunting for log files with bash.",
    parameters: Type.Object({
      session: SessionParam,
      run_id: RunIdParam,
      step: StepParam,
      tail_kb: Type.Optional(
        Type.Integer({ minimum: 1, maximum: 1024, description: "KiB from the end of the log (default 64)" }),
      ),
    }),
    async execute(_toolCallId, params, signal) {
      const args = params as { session: string; run_id?: string; step: number; tail_kb?: number };
      const log = await client.logs(bind(args.session), args.step, {
        runId: args.run_id,
        tailKb: args.tail_kb,
        signal,
      });
      const header = `Step ${args.step} log (${log.size ?? "?"} bytes${log.truncated ? ", tail only" : ""}):\n`;
      return result(header + log.text, { step: args.step, ...log });
    },
  });

  const setMode = typed({
    name: "insar_set_mode",
    label: "InSAR Set Mode",
    description:
      "Set the session's freedom mode. free = all pi tools available (default). strict = only read and insar_* tools run, so every scientific action is recorded in the provenance ledger.",
    parameters: Type.Object({
      session: SessionParam,
      mode: StringEnum(FREEDOM_MODES, "free or strict"),
    }),
    async execute(_toolCallId, params, signal) {
      const args = params as { session: string; mode: string };
      if (!isFreedomMode(args.mode)) {
        throw new Error(`unknown mode ${args.mode} (expected free or strict)`);
      }
      const session = bind(args.session);
      const outcome = await controller.set(args.mode, { session, signal });
      if (!outcome.persisted) {
        throw new Error(`Backend refused the mode change: ${outcome.error ?? "unknown error"}`);
      }
      return result(
        args.mode === "strict"
          ? `Session ${session} is now strict: only read and insar_* tools run. Switch back with insar_set_mode free.`
          : `Session ${session} is now free: all pi tools are available.`,
        { session, mode: outcome.mode },
      );
    },
  });

  const getMode = typed({
    name: "insar_get_mode",
    label: "InSAR Get Mode",
    description: "Report the session's freedom mode (free or strict).",
    parameters: Type.Object({ session: SessionParam }),
    async execute(_toolCallId, params, signal) {
      const args = params as { session: string };
      const response = await client.getMode(bind(args.session), signal);
      controller.observeRemote(response.mode);
      return result(`Session ${response.session} mode: ${response.mode}.`, response);
    },
  });

  return [
    health,
    createSession,
    listSessions,
    planRun,
    runStatus,
    executeRun,
    previewChange,
    applyChange,
    intervene,
    forkRun,
    exportProvenance,
    listDatasets,
    envProbe,
    readLog,
    setMode,
    getMode,
  ];
}

/** Name -> tool, for tests and for callers that dispatch by name. */
export function toolsByName(tools: InsarTool[]): Map<string, InsarTool> {
  return new Map(tools.map((tool) => [tool.name, tool]));
}

/** Register every `insar_*` tool with pi. Returns them for inspection. */
export function registerInsarTools(
  pi: { registerTool: (tool: InsarTool) => void },
  client: BackendClient,
  controller: ModeController,
): InsarTool[] {
  const tools = createInsarTools(client, controller);
  for (const tool of tools) pi.registerTool(tool);
  return tools;
}

/** Exposed so callers can assert the action set stays aligned with the backend. */
export const SUPPORTED_ACTIONS = ACTIONS;
export type { Static, TSchema };
