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
  AdviseAction,
  AdviseResponse,
  ArtifactsResponse,
  BackendClient,
  CapabilityInfo,
  CaptionResponse,
  DeliverAs,
  DoctorResponse,
  ExportFormat,
  ExportOptionsResponse,
  ExportProduct,
  MonitorResponse,
  RecommendResponse,
  ReportResponse,
  ReportSection,
  SkillResponse,
  StreamResult,
  TimeseriesPointResponse,
  TraceEvent,
} from "./backendClient.ts";
import {
  ACTIONS,
  BackendError,
  DELIVER_AS,
  EXPORT_FORMATS,
  EXPORT_PRODUCTS,
  FREEDOM_MODES,
  REPORT_SECTIONS,
  resolveExportDir,
} from "./backendClient.ts";
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

function formatStepDuration(duration: number | null, attempts: number): string | null {
  if (duration == null) return null;
  const label = formatDuration(duration);
  return attempts > 1 ? `${label} ×${attempts}` : label;
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
      const duration = formatStepDuration(step.duration, step.attempts);
      const tail = duration ? ` · ${duration}` : "";
      return `  ${String(step.step).padStart(2, "0")} ${step.name} · ${step.method ?? "-"} · ${flags}${tail}`;
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

function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "?";
  if (seconds < 0.05) return "<0.1 s";
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds - minutes * 60;
  return `${minutes} min ${rest.toFixed(0)} s`;
}

function eventKeyBits(row: TraceEvent): string {
  const bits = [row.observation, row.revision_trigger, row.error_type, row.error_message]
    .map((value) => (typeof value === "string" ? value.trim() : ""))
    .filter((value) => value.length > 0);
  return bits[0] ?? "";
}

/** One line per step: `step → phase → duration → key event`. */
export function summarizeTrace(
  events: TraceEvent[],
  stepMeta?: Map<string, { duration: number | null; attempts: number }>,
): string {
  const groups = new Map<string, TraceEvent[]>();
  for (const row of events) {
    const key = row.step_no == null ? "—" : String(row.step_no);
    const list = groups.get(key) ?? [];
    list.push(row);
    groups.set(key, list);
  }
  const lines: string[] = [];
  for (const [step, rows] of groups) {
    const meta = stepMeta?.get(step);
    const duration = meta ? formatStepDuration(meta.duration, meta.attempts) : null;
    const phase = rows.map((row) => row.phase).find((value) => typeof value === "string" && value.length > 0) ?? "-";
    const keyEvent =
      [...rows].reverse().map(eventKeyBits).find((value) => value.length > 0) ?? "(no event text)";
    const label = step === "—" ? "—" : step.padStart(2, "0");
    lines.push(`  ${label} · ${phase} · ${duration ?? "-"} · ${keyEvent}`);
  }
  return lines.join("\n");
}

function formatBytes(size: number | null): string {
  if (size === null || !Number.isFinite(size)) return "?";
  if (size < 1024) return `${size} B`;
  return `${Math.round(size / 1024)} KiB`;
}

function formatCapabilities(steps: CapabilityInfo[]): string {
  return steps
    .map((step) => {
      const methods = step.methods.map((method) => {
        const flags = [
          method.recommend ? "recommended" : undefined,
          method.ok ? undefined : "blocked",
          method.simulated ? "simulated" : undefined,
        ].filter(Boolean);
        return `    ${method.id}${flags.length > 0 ? ` (${flags.join(", ")})` : ""}`;
      });
      return [
        `${String(step.id).padStart(2, "0")} ${step.name} · default ${step.method}`,
        "  methods (closed set):",
        ...methods,
      ].join("\n");
    })
    .join("\n");
}

function formatArtifacts(listing: ArtifactsResponse): string {
  if (!listing.run) return "Session has no run yet.";
  const total = listing.steps.reduce((sum, step) => sum + step.artifacts.length, 0);
  if (total === 0) return `Run ${listing.run} has no artifacts yet.`;
  const blocks = listing.steps.map((step) => {
    const rows = step.artifacts.map((art) => {
      const missing = art.exists ? "" : " MISSING";
      return `    ${art.artId} · ${art.kind} · ${art.path} · ${formatBytes(art.size)}${missing}`;
    });
    return [
      `  step ${String(step.stepId).padStart(2, "0")} ${step.name} · ${step.method || "-"} · ${step.artifacts.length} file(s)`,
      ...rows,
    ].join("\n");
  });
  return `Run ${listing.run}: ${total} artifact(s) across ${listing.steps.length} step(s)\n${blocks.join("\n")}`;
}

function formatDoctor(report: DoctorResponse): string {
  const fails = report.results.filter((item) => item.status === "fail");
  const warns = report.results.filter((item) => item.status === "warn");
  const head =
    fails.length > 0
      ? `FAIL: ${fails.length} check(s) failed (${report.counts.fail} fail, ${report.counts.warn} warn, ${report.counts.ok} ok)`
      : report.status === "warn" || warns.length > 0
        ? `WARN: ${warns.length} check(s) with warnings (${report.counts.ok} ok)`
        : `OK: ${report.counts.ok} checks passed`;
  const lines = [...fails, ...warns].map(
    (item) => `  ${item.status.toUpperCase()} · ${item.name} · ${item.detail}${item.fix_hint ? ` · ${item.fix_hint}` : ""}`,
  );
  return [head, `took ${report.took_ms} ms`, ...lines].join("\n");
}

function formatRecommend(data: RecommendResponse): string {
  const datasetId = String(data.dataset["id"] ?? data.dataset["kind"] ?? "dataset");
  if (data.routes.length === 0) return `No routes for ${datasetId}.`;
  const blocks = data.routes.map((route) => {
    const flag = route.ready ? "ready" : `not ready${route.missing.length > 0 ? ` (missing ${route.missing.join(", ")})` : ""}`;
    return [
      `  ${route.route_id} · ${route.name} · ${flag}`,
      ...route.pros.map((line) => `    + ${line}`),
      ...route.cons.map((line) => `    - ${line}`),
      route.est_note ? `    note: ${route.est_note}` : "",
    ]
      .filter(Boolean)
      .join("\n");
  });
  return `Routes for ${datasetId}:\n${blocks.join("\n")}`;
}

function skillToMarkdown(skill: SkillResponse): string {
  const sections = Object.entries(skill.sections).flatMap(([name, text]) => [`## ${name}`, "", text, ""]);
  return [`# ${skill.name}`, "", skill.description, "", ...sections].join("\n");
}

function renderExportMatrix(opts: ExportOptionsResponse): string {
  const head = `run ${opts.run}${opts.simulated ? " (simulated — data-product export refused)" : ""}`;
  const lines = opts.products.flatMap((row) =>
    Object.entries(row.formats).map(([fmt, cell]) => {
      const status = cell.available ? "ok" : (cell.reason ?? "unavailable");
      return `  ${row.product}/${fmt}  ${status}`;
    }),
  );
  return `${head}\n${lines.join("\n")}`;
}

function adviseToolHint(action: AdviseAction): string {
  if (action.kind === "chat_prefill") {
    return `insar_plan_run / reply with: ${action.text ?? ""}`;
  }
  if (action.kind === "api_action") {
    const endpoint = action.endpoint ?? "";
    if (endpoint.includes("/resume")) return "insar_resume";
    if (endpoint.includes("/pipeline")) return "insar_execute_run";
    if (endpoint.includes("/report/draft")) return "insar_report section=methods";
    if (endpoint.includes("/report/results")) return "insar_report section=results";
    if (endpoint.includes("/report/full")) return "insar_report section=full";
    if (endpoint.includes("/report/caption")) return "insar_figure_caption";
    if (endpoint.includes("/export")) return "insar_export_product";
    if (endpoint.includes("/repro-bundle")) return "insar_repro_bundle";
    if (endpoint.includes("/skills")) return "insar_read_skill";
    if (endpoint.includes("/vision-qa")) return "insar_vision_qa";
    return `${action.method ?? "GET"} ${endpoint}`;
  }
  if (action.kind === "open_tab") {
    if (action.tab === "env") return "insar_env_probe / insar_doctor";
    if (action.tab === "audit") return "insar_export_provenance";
    if (action.tab === "figures" || action.tab === "images") return "insar_view_figure";
    return `inspect via insar_run_status (tab ${action.tab ?? "?"})`;
  }
  return action.kind;
}

function renderAdvise(doc: AdviseResponse): string {
  if (doc.suggestions.length === 0) {
    return `run ${doc.run_id} · ${doc.status}: no suggestions (${doc.note ?? doc.context ?? "none"}).`;
  }
  const head = [
    `run ${doc.run_id} · ${doc.status}`,
    doc.evidence_level ? `evidence ${doc.evidence_level}` : undefined,
    doc.context,
  ]
    .filter(Boolean)
    .join("\n");
  const cards = doc.suggestions.map((item) => {
    const tool = adviseToolHint(item.action);
    return `  [${item.id}] ${item.title}\n    why: ${item.why}\n    do: ${tool}`;
  });
  return `${head}\n${cards.join("\n")}`;
}

function renderReport(section: ReportSection, doc: ReportResponse): string {
  const body = doc.markdown ?? doc.draft ?? "";
  const head = [
    `run ${doc.run_id} · section ${section}`,
    doc.llm_polish === false ? "polish rejected → deterministic skeleton (numbers verified)" : undefined,
    doc.saved ? `saved ${doc.path ?? "workspace"}` : undefined,
  ]
    .filter(Boolean)
    .join("\n");
  return `${head}\n\n${body}`;
}

function renderCaption(doc: CaptionResponse): string {
  const polish = doc.llm_polish ? "llm polish kept" : "deterministic skeleton";
  return `run ${doc.run_id} · ${doc.figure} (${polish})\n\nZH: ${doc.zh}\n\nEN: ${doc.en}`;
}

function formatTimeseries(data: TimeseriesPointResponse): string {
  const n = data.values_mm.length;
  const first = data.values_mm[0] ?? 0;
  const last = data.values_mm[n - 1] ?? 0;
  const coord =
    data.point.lat !== null && data.point.lon !== null
      ? ` (${data.point.lat.toFixed(4)}, ${data.point.lon.toFixed(4)})`
      : "";
  const head = [
    `point row=${data.point.row} col=${data.point.col}${coord}`,
    `source ${data.source}`,
    `${n} epochs ${data.dates[0] ?? "?"} … ${data.dates[n - 1] ?? "?"}`,
    `cumulative ${(last - first).toFixed(1)} mm`,
  ].join("\n");
  const table = data.dates.map((date, i) => `${date}  ${data.values_mm[i]?.toFixed(2)}`).join("\n");
  return `${head}\n\n${table}`;
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
      "Plan an InSAR run from a natural-language request (region, time window, target). " +
      "The backend parses the intent, probes the environment and writes a plan; nothing is executed yet. " +
      "pipeline=core is the 11-step processing chain (default); pipeline=analysis is a post-processing " +
      "run over existing products (steps 20-28). For analysis, give source paths via params_json.",
    parameters: Type.Object({
      session: SessionParam,
      text: Type.String({
        description:
          'Natural-language request, e.g. "Ridgecrest coseismic deformation, 2019-06-10 to 2019-08-15".',
      }),
      pipeline: Type.Optional(
        StringEnum(
          ["core", "analysis"],
          "core = the 11-step processing chain (default). " +
            "analysis = post-processing over products of existing runs.",
        ),
      ),
      params_json: Type.Optional(
        Type.String({
          description:
            'For analysis: JSON object of source paths, e.g. {"primary":"mintpy/velocity.h5"}. Applied to step 20.',
        }),
      ),
    }),
    async execute(_toolCallId, params, signal) {
      const args = params as {
        session: string;
        text: string;
        pipeline?: "core" | "analysis";
        params_json?: string;
      };
      const session = bind(args.session);
      const extra: { pipeline?: "core" | "analysis"; params?: Record<string, unknown> } = {};
      if (args.pipeline !== undefined) extra.pipeline = args.pipeline;
      if (args.params_json !== undefined) {
        extra.params = parseJsonObject(args.params_json, "params_json");
      }
      const stream = await client.turn(session, args.text, signal, extra);
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

  const resume = typed({
    name: "insar_resume",
    label: "InSAR Resume",
    description:
      "Reattach runs left `running` after a backend restart (step-level resume). " +
      "Settled steps are never re-executed — work continues from the ledger. " +
      "For pending steps of a planned run use insar_execute_run instead.",
    parameters: Type.Object({ session: SessionParam }),
    async execute(_toolCallId, params, signal) {
      const args = params as { session: string };
      const session = bind(args.session);
      const stream = await client.resume(session, signal);
      const monitor = await client.monitor(session, undefined, signal);
      controller.observeRemote(monitor.mode);
      const text = [
        `Resume finished (${streamNote(stream)}).`,
        monitor.run ? summarizeMonitor(monitor) : `Session ${session} has no run yet.`,
      ].join("\n");
      return result(text, { monitor, events: tailEvents(stream) });
    },
  });

  const viewFigure = typed({
    name: "insar_view_figure",
    label: "InSAR View Figure",
    description:
      "List a run's real figure artifacts, or inline one figure as an image " +
      "(interferograms, velocity maps, time series …). Omit `name` to list; give " +
      "`name` to view. Figures come from the run's artifact ledger only.",
    parameters: Type.Object({
      session: SessionParam,
      run_id: RunIdParam,
      name: Type.Optional(
        Type.String({ description: "Figure file name exactly as returned by the list call" }),
      ),
    }),
    async execute(_toolCallId, params, signal) {
      const args = params as { session: string; run_id?: string; name?: string };
      const session = bind(args.session);
      const listing = await client.figures(session, args.run_id, signal);
      if (!args.name) {
        if (listing.figures.length === 0) {
          return result(`Run ${listing.run ?? "?"} has no figure artifacts yet.`, listing);
        }
        const lines = listing.figures.map(
          (f) => `  step ${String(f.step).padStart(2, "0")} · ${f.name} · ${Math.round(f.size / 1024)} KiB`,
        );
        return result(`Run ${listing.run}: ${listing.figures.length} figures\n${lines.join("\n")}`, listing);
      }
      const figure = listing.figures.find((f) => f.name === args.name);
      if (!figure) {
        const available = listing.figures.map((f) => `  ${f.name}`).join("\n");
        return result(`No figure named ${args.name}.\nAvailable:\n${available}`, listing);
      }
      const image = await client.artifactImage(figure.fullUrl, signal);
      return {
        content: [
          {
            type: "image" as const,
            data: Buffer.from(image.bytes).toString("base64"),
            mimeType: image.mediaType,
          },
          {
            type: "text" as const,
            text: `step ${figure.step} · ${figure.name} (${image.mediaType}, ${Math.round(figure.size / 1024)} KiB)`,
          },
        ],
        details: figure,
      };
    },
  });

  const runTrace = typed({
    name: "insar_run_trace",
    label: "InSAR Run Trace",
    description:
      "Execution timeline of a run: one line per step with phase, duration and key events. " +
      "Use this to see what actually happened and where a run stalled — do not guess from logs.",
    parameters: Type.Object({ session: SessionParam, run_id: RunIdParam }),
    async execute(_toolCallId, params, signal) {
      const args = params as { session: string; run_id?: string };
      const session = bind(args.session);
      // Duration comes from the commands ledger (/api/monitor), fetched in parallel
      // with the trace. A monitor miss (no run) must not fail this tool.
      const monitorP = client.monitor(session, args.run_id, signal).then(
        (monitor) =>
          new Map(
            monitor.steps.map((step) => [
              String(step.step),
              { duration: step.duration, attempts: step.attempts },
            ]),
          ),
        () => undefined,
      );
      let events: TraceEvent[];
      try {
        events = await client.trace(session, args.run_id, signal);
      } catch (error) {
        if (error instanceof BackendError && error.status === 404) {
          return result(
            `no run (HTTP 404): ${error.message}`,
            { status: 404, body: error.body },
          );
        }
        throw error;
      }
      if (!Array.isArray(events) || events.length === 0) {
        return result(
          `No run (empty trace for session ${session}${args.run_id ? `, run_id ${args.run_id}` : ""}).`,
          events ?? [],
        );
      }
      const stepMeta = await monitorP;
      const text = [
        `Trace: ${events.length} event(s) for session ${session}${args.run_id ? ` run ${args.run_id}` : ""}.`,
        "step → phase → duration → key event",
        summarizeTrace(events, stepMeta),
      ].join("\n");
      return result(text, events);
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

  const capabilities = typed({
    name: "insar_capabilities",
    label: "InSAR Capabilities",
    description:
      "Return the closed set of pipeline methods and parameters per step. " +
      "Call this before insar_apply_change — method ids outside this list are rejected.",
    parameters: Type.Object({}),
    async execute(_toolCallId, _params, signal) {
      const { steps } = await client.capabilities(signal);
      return result(formatCapabilities(steps), { steps });
    },
  });

  const timeseriesPoint = typed({
    name: "insar_timeseries_point",
    label: "InSAR Time Series at Point",
    description:
      "Read the real deformation time series of one pixel from the run's timeseries HDF5 " +
      "(values in mm, dates from the ledger). Locate by lat/lon (preferred) or row/col. " +
      "Use this to answer 'how much has this point moved' — never estimate it from a figure.",
    parameters: Type.Object({
      session: SessionParam,
      run_id: RunIdParam,
      lat: Type.Optional(Type.Number({ description: "Latitude in degrees" })),
      lon: Type.Optional(Type.Number({ description: "Longitude in degrees" })),
      row: Type.Optional(Type.Integer({ minimum: 0, description: "Pixel row (used only when lat/lon absent)" })),
      col: Type.Optional(Type.Integer({ minimum: 0, description: "Pixel column" })),
    }),
    async execute(_toolCallId, params, signal) {
      const args = params as {
        session: string;
        run_id?: string;
        lat?: number;
        lon?: number;
        row?: number;
        col?: number;
      };
      const hasLatLon = args.lat !== undefined && args.lon !== undefined;
      const hasRowCol = args.row !== undefined && args.col !== undefined;
      if (!hasLatLon && !hasRowCol) {
        throw new Error("Give either lat and lon, or row and col.");
      }
      const data = await client.timeseriesPoint(
        bind(args.session),
        { runId: args.run_id, lat: args.lat, lon: args.lon, row: args.row, col: args.col },
        signal,
      );
      return result(formatTimeseries(data), data);
    },
  });

  const listArtifacts = typed({
    name: "insar_list_artifacts",
    label: "InSAR List Artifacts",
    description:
      "List every artifact the run's ledger recorded (path, kind, size, whether the file " +
      "still exists). Use this before delivery to see what was produced and where.",
    parameters: Type.Object({ session: SessionParam, run_id: RunIdParam }),
    async execute(_toolCallId, params, signal) {
      const args = params as { session: string; run_id?: string };
      const listing = await client.artifacts(bind(args.session), args.run_id, signal);
      return result(formatArtifacts(listing), listing);
    },
  });

  const doctor = typed({
    name: "insar_doctor",
    label: "InSAR Doctor",
    description:
      "Second-scale read-only environment check (engines, disk, database, ports, WSL). " +
      "Use this when something looks wrong instead of probing with bash.",
    parameters: Type.Object({}),
    async execute(_toolCallId, _params, signal) {
      const report = await client.doctor(signal);
      return result(formatDoctor(report), report);
    },
  });

  const recommendRoute = typed({
    name: "insar_recommend_route",
    label: "InSAR Recommend Route",
    description:
      "Given a dataset id from insar_list_datasets, compare processing routes " +
      "(HyP3 cloud product vs local full chain) and which engines are missing.",
    parameters: Type.Object({
      dataset_id: Type.String({ description: "Dataset id as returned by insar_list_datasets" }),
    }),
    async execute(_toolCallId, params, signal) {
      const args = params as { dataset_id: string };
      const data = await client.recommend(args.dataset_id, signal);
      return result(formatRecommend(data), data);
    },
  });

  const readSkill = typed({
    name: "insar_read_skill",
    label: "InSAR Read Skill",
    description:
      "Fetch the scientific skill document for one pipeline step (1-11). " +
      "Use this for method/parameter meaning instead of recalling it from memory.",
    parameters: Type.Object({
      step_id: Type.Integer({ minimum: 1, maximum: 11, description: "Pipeline step number (1-11)" }),
    }),
    async execute(_toolCallId, params, signal) {
      const args = params as { step_id: number };
      const skill = await client.skill(args.step_id, signal);
      return result(skillToMarkdown(skill), skill);
    },
  });

  const exportProduct = typed({
    name: "insar_export_product",
    label: "InSAR Export Product",
    description:
      "Export a real data product for downstream use. Omit `format` to list what is " +
      "available for this run (product × format matrix with honest reasons when a " +
      "format is unavailable). Give `format` to write the file and get its path. " +
      "GeoTIFF/Shapefile for GIS, KMZ for Google Earth, CSV for spreadsheets, HDF5 for MintPy. " +
      "Simulated runs are refused on purpose: placeholder bytes must never be delivered " +
      "as data products.",
    parameters: Type.Object({
      session: SessionParam,
      run_id: RunIdParam,
      product: Type.Optional(StringEnum(EXPORT_PRODUCTS, "Which product to export")),
      format: Type.Optional(StringEnum(EXPORT_FORMATS, "Output format; omit to list availability")),
    }),
    async execute(_toolCallId, params, signal) {
      const args = params as {
        session: string;
        run_id?: string;
        product?: ExportProduct;
        format?: ExportFormat;
      };
      const session = bind(args.session);
      if (!args.format || !args.product) {
        const opts = await client.exportOptions(session, args.run_id, signal);
        return result(renderExportMatrix(opts), opts);
      }
      const out = await client.exportProduct(
        session,
        args.product,
        args.format,
        resolveExportDir(),
        args.run_id,
        signal,
      );
      return result(
        `exported ${args.product} as ${args.format}\n` +
          `path ${out.savedTo}\nsize ${out.bytes} bytes${out.reused ? " (reused cached conversion)" : ""}`,
        out,
      );
    },
  });

  const visionQa = typed({
    name: "insar_vision_qa",
    label: "InSAR Vision QA",
    description:
      "Have the model actually look at a run's figure and judge quality (unwrapping " +
      "jumps, residual atmospheric fringes, coverage holes, colour-scale sanity). " +
      "Returns a structured verdict recorded against the run — not a free-form opinion. " +
      "Omit `figure` to list existing reviews and available figure names.",
    parameters: Type.Object({
      session: SessionParam,
      run_id: RunIdParam,
      figure: Type.Optional(Type.String({ description: "Figure file name; omit to list reviews / available names" })),
    }),
    async execute(_toolCallId, params, signal) {
      const args = params as { session: string; run_id?: string; figure?: string };
      const session = bind(args.session);
      if (!args.figure) {
        const [reviews, listing] = await Promise.all([
          client.visionQaList(session, args.run_id, signal),
          client.figures(session, args.run_id, signal),
        ]);
        const names =
          listing.figures.length === 0
            ? "no figures yet"
            : listing.figures.map((figure) => `  ${figure.name}`).join("\n");
        const n = reviews.items.length;
        return result(
          `Vision QA reviews: ${n}. Available figures:\n${names}\nCall again with figure=<name> to review.`,
          { reviews, figures: listing },
        );
      }
      const review = await client.visionQa(
        { session, ...(args.run_id === undefined ? {} : { run_id: args.run_id }), figure: args.figure },
        signal,
      );
      const ok = review["ok"];
      const error = review["error"];
      const text =
        ok === false
          ? `Vision QA not applied: ${String(error ?? "unknown")}`
          : `Vision QA recorded for ${args.figure} (run ${String(review["run"] ?? args.run_id ?? "latest")}).`;
      return result(text, review);
    },
  });

  const report = typed({
    name: "insar_report",
    label: "InSAR Report Section",
    description:
      "Generate a provenance-backed report section for a run. " +
      "`methods` = paper methods section, `results` = results section (QA metrics + " +
      "velocity statistics), `full` = the assembled report. " +
      "Every number comes from the run's ledger and is cross-checked; when the model " +
      "polish fails validation the deterministic skeleton is returned instead " +
      "(`llm_polish: false`) — that is a correct outcome, not an error. " +
      "ALWAYS use this instead of writing a methods or results section yourself.",
    parameters: Type.Object({
      session: SessionParam,
      run_id: RunIdParam,
      section: StringEnum(REPORT_SECTIONS, "Which section to produce"),
    }),
    async execute(_toolCallId, params, signal) {
      const args = params as { session: string; run_id?: string; section: ReportSection };
      const doc = await client.report(args.section, bind(args.session), args.run_id, signal);
      return result(renderReport(args.section, doc), doc);
    },
  });

  const figureCaption = typed({
    name: "insar_figure_caption",
    label: "InSAR Figure Caption",
    description:
      "Bilingual (zh/en) paper caption for one figure. Numbers come from the sidecar and ledger. " +
      "Use this instead of writing a caption from memory.",
    parameters: Type.Object({
      session: SessionParam,
      run_id: RunIdParam,
      figure: Type.String({ description: "Figure file name as listed by insar_view_figure" }),
    }),
    async execute(_toolCallId, params, signal) {
      const args = params as { session: string; run_id?: string; figure: string };
      const doc = await client.caption(bind(args.session), args.figure, args.run_id, signal);
      return result(renderCaption(doc), doc);
    },
  });

  const reproBundle = typed({
    name: "insar_repro_bundle",
    label: "InSAR Repro Bundle",
    description:
      "Download the reproduction zip for a finished run (ledger, run.sh, methods.md, qa.json, " +
      "figures, MANIFEST with per-file sha256). Only done runs; in-progress runs are refused.",
    parameters: Type.Object({ session: SessionParam, run_id: RunIdParam }),
    async execute(_toolCallId, params, signal) {
      const args = params as { session: string; run_id?: string };
      const out = await client.reproBundle(bind(args.session), resolveExportDir(), args.run_id, signal);
      return result(`repro bundle\npath ${out.savedTo}\nsize ${out.bytes} bytes`, out);
    },
  });

  const adviseNext = typed({
    name: "insar_advise_next",
    label: "InSAR Advise Next",
    description:
      "Deterministic next-step suggestion cards for a finished/failed/interrupted run " +
      "(why + the matching insar_* tool). Do not improvise the next scientific action.",
    parameters: Type.Object({ session: SessionParam, run_id: RunIdParam }),
    async execute(_toolCallId, params, signal) {
      const args = params as { session: string; run_id?: string };
      const doc = await client.advise(bind(args.session), args.run_id, signal);
      return result(renderAdvise(doc), doc);
    },
  });

  return [
    health,
    createSession,
    listSessions,
    planRun,
    runStatus,
    executeRun,
    resume,
    viewFigure,
    runTrace,
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
    capabilities,
    timeseriesPoint,
    listArtifacts,
    doctor,
    recommendRoute,
    readSkill,
    exportProduct,
    visionQa,
    report,
    figureCaption,
    reproBundle,
    adviseNext,
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
