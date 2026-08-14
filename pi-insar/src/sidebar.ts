/**
 * The "monitored pipeline" rail rendered into pi's native TUI.
 *
 * `renderPipelineRail` is a pure function over the `GET /api/monitor` payload
 * (no pi imports) so the layout is unit-testable with golden fixtures. The
 * install side is a thin poller that pushes those lines into
 * `ctx.ui.setWidget` and a one-line footer into `ctx.ui.setStatus`, gated on
 * `ctx.hasUI` so the extension is a silent no-op in rpc/json/print modes.
 *
 * SSE (`GET /api/events`) is deliberately not wired yet: polling `/api/monitor`
 * is the whole sidebar contract in one request and cannot desynchronise from
 * the ledger. Event-driven refresh is a later optimisation.
 */

import type { ExtensionAPI, ExtensionContext, WidgetPlacement } from "@earendil-works/pi-coding-agent";
import type { BackendClient, MonitorResponse, MonitorStep } from "./backendClient.ts";
import type { ModeController } from "./mode.ts";

/** Closed glyph set for the step row (design §D.2). */
export const GLYPHS = {
  done: "✔",
  running: "R",
  pending: "○",
  failed: "✖",
  skipped: "–",
  stale: "!",
} as const;

const STAGE_LETTERS = new Set(["P", "L", "R", "C", "V"]);

/** One step -> one glyph. Precedence: failed > stale > skipped > done > stage. */
export function stepGlyph(step: Partial<MonitorStep> | undefined | null): string {
  if (!step) return GLYPHS.pending;
  if (step.state === "failed") return GLYPHS.failed;
  if (step.stale) return GLYPHS.stale;
  if (step.state === "skipped") return GLYPHS.skipped;
  if (step.state === "done") return GLYPHS.done;
  if (step.state === "running") {
    const letter = step.stage_letter ?? "";
    return STAGE_LETTERS.has(letter) ? letter : GLYPHS.running;
  }
  return GLYPHS.pending;
}

/** `20260814T171155-800a728d` -> `800a728d` (keeps fork suffixes intact). */
export function shortRunId(runId: string): string {
  const match = /^\d{8}T\d{6}-(.+)$/.exec(runId);
  return match?.[1] ?? runId;
}

function pad2(step: number): string {
  return String(step).padStart(2, "0");
}

function currentLine(monitor: MonitorResponse): string {
  const current = monitor.current;
  if (current) {
    const method = current.method ? ` · ${current.method}` : "";
    return `▶ ${pad2(current.step)} ${current.name}${method} · ${current.stage}`;
  }
  const status = monitor.run?.status ?? "unknown";
  if (status === "done") return "▶ finished · no step running";
  const next = monitor.steps.find((step) => step.state === "pending");
  if (next) {
    const method = next.method ? ` · ${next.method}` : "";
    return `▶ idle · next ${pad2(next.step)} ${next.name}${method}`;
  }
  return `▶ idle · run ${status}`;
}

function evidenceLine(monitor: MonitorResponse): string {
  const evidence = monitor.evidence;
  const level = evidence?.level ?? "—";
  const capped =
    evidence?.ceiling && evidence.ceiling !== evidence.level
      ? ` (ceiling ${evidence.ceiling})`
      : "";
  const taints = monitor.taints ?? 0;
  const taintText = taints > 0 ? `${GLYPHS.stale} ${taints} stale` : "0 stale";
  return `evidence ${level}${capped} · mode ${monitor.mode ?? "free"} · ${taintText}`;
}

/**
 * Render the sidebar rail: 2 lines when there is nothing to monitor, 4 lines
 * for a live run. Tolerates a missing/garbled payload rather than throwing —
 * the rail must never take down a refresh tick.
 */
export function renderPipelineRail(monitor: MonitorResponse | null | undefined): string[] {
  if (!monitor || typeof monitor !== "object") {
    return ["insar · backend unreachable", "check the backend and INSAR_API_BASE"];
  }

  const run = monitor.run;
  if (!run) {
    return [
      `insar · no run · mode ${monitor.mode ?? "free"}`,
      "plan one with insar_plan_run",
    ];
  }

  const steps = Array.isArray(monitor.steps) ? monitor.steps : [];
  const progress = monitor.progress ?? { total: steps.length, done: 0, pct: 0 };
  const header = [
    `insar · ${shortRunId(run.run_id)}`,
    run.scenario ?? "unscoped",
    run.status,
    run.simulated ? "simulated" : undefined,
  ]
    .filter((part): part is string => Boolean(part))
    .join(" · ");

  const rail = steps.map(stepGlyph).join("");
  const counts = `${progress.done}/${progress.total} · ${progress.pct}%`;

  return [header, `${rail}  ${counts}`, currentLine(monitor), evidenceLine(monitor)];
}

/** Compact footer line: the rail boiled down to one row. */
export function renderStatusLine(monitor: MonitorResponse | null | undefined): string {
  if (!monitor || !monitor.run) return "insar: idle";
  const evidence = monitor.evidence?.level ?? "—";
  return (
    `insar ${shortRunId(monitor.run.run_id)} ${monitor.progress?.pct ?? 0}% · ` +
    `${monitor.run.status} · ev ${evidence} · ${monitor.mode ?? "free"}`
  );
}

export interface SidebarOptions {
  intervalMs?: number;
  widgetKey?: string;
  statusKey?: string;
  placement?: WidgetPlacement;
}

/**
 * Poll `/api/monitor` for the active InSAR session and mirror it into the TUI.
 * Nothing is rendered until a tool binds a session, so a pi run that never
 * touches InSAR stays visually untouched.
 */
export function installSidebar(
  pi: ExtensionAPI,
  client: BackendClient,
  controller: ModeController,
  options: SidebarOptions = {},
): void {
  const intervalMs = options.intervalMs ?? 2_000;
  const widgetKey = options.widgetKey ?? "insar-pipeline";
  const statusKey = options.statusKey ?? "insar";
  const placement: WidgetPlacement = options.placement ?? "aboveEditor";

  let timer: ReturnType<typeof setInterval> | undefined;
  let inFlight = false;
  let lastRendered = "";

  function clear(ctx: ExtensionContext): void {
    if (timer) {
      clearInterval(timer);
      timer = undefined;
    }
    if (!ctx.hasUI) return;
    ctx.ui.setWidget(widgetKey, undefined, { placement });
    ctx.ui.setStatus(statusKey, undefined);
  }

  function paint(ctx: ExtensionContext, lines: string[], status: string): void {
    const key = `${status}\u0000${lines.join("\n")}`;
    if (key === lastRendered) return;
    lastRendered = key;
    ctx.ui.setWidget(widgetKey, lines, { placement });
    ctx.ui.setStatus(statusKey, status);
  }

  async function tick(ctx: ExtensionContext): Promise<void> {
    if (inFlight) return;
    const session = controller.activeSession();
    if (!session) return;
    inFlight = true;
    try {
      const monitor = await client.monitor(session);
      // The backend is the source of truth for the mode; adopt what it reports
      // so an external change (other client, /api/mode) reaches the gate.
      controller.observeRemote(monitor.mode);
      paint(ctx, renderPipelineRail(monitor), renderStatusLine(monitor));
    } catch {
      paint(ctx, renderPipelineRail(null), "insar: backend unreachable");
    } finally {
      inFlight = false;
    }
  }

  pi.on("session_start", (_event, ctx) => {
    if (!ctx.hasUI) return;
    if (timer) clearInterval(timer);
    void tick(ctx);
    timer = setInterval(() => void tick(ctx), intervalMs);
    // Never hold the process open just to refresh a widget.
    timer.unref?.();
  });

  pi.on("session_shutdown", (_event, ctx) => {
    clear(ctx);
  });
}
