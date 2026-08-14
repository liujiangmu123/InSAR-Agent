/**
 * Graduated freedom: the session-level `free | strict` mode.
 *
 * The backend owns the durable value (`GET`/`POST /api/mode`, persisted per
 * InSAR session under the home dir). The extension keeps a tiny in-memory
 * mirror because the `tool_call` gate must decide synchronously — an HTTP round
 * trip inside the hook would stall every tool call and turn a backend outage
 * into a stalled agent.
 */

import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import type { BackendClient, FreedomMode } from "./backendClient.ts";
import { FREEDOM_MODES } from "./backendClient.ts";

export type { FreedomMode };

export const MODE_COMMAND = "insar-mode";
export const STRICT_FLAG = "insar-strict";
export const SESSION_FLAG = "insar-session";

export function isFreedomMode(value: unknown): value is FreedomMode {
  return typeof value === "string" && (FREEDOM_MODES as readonly string[]).includes(value);
}

export interface SetModeResult {
  mode: FreedomMode;
  /** True when the backend accepted and persisted the change. */
  persisted: boolean;
  session: string | undefined;
  /** Why persistence failed, when it did. The in-memory mode still changed. */
  error?: string;
}

export class ModeController {
  private mode: FreedomMode;
  private session: string | undefined;

  constructor(
    private readonly client: BackendClient,
    initialMode: FreedomMode = "free",
  ) {
    this.mode = initialMode;
  }

  /** Synchronous, never-throwing read for the strict-mode gate. */
  current(): FreedomMode {
    return this.mode;
  }

  activeSession(): string | undefined {
    return this.session;
  }

  /**
   * Record the InSAR session a tool call just touched. The gate needs to know
   * which session's mode applies, and tools are the only place the session id
   * appears. Refreshes the mirror in the background on a session switch.
   */
  noteSession(session: string | undefined): void {
    if (!session || session === this.session) return;
    this.session = session;
    void this.refresh().catch(() => {
      // Best effort: a failed refresh keeps the previous in-memory mode, which
      // is `free` by default and therefore never blocks built-ins by accident.
    });
  }

  /** Adopt a mode observed in a backend payload (e.g. a monitor snapshot). */
  observeRemote(mode: unknown): void {
    if (isFreedomMode(mode)) this.mode = mode;
  }

  /** Pull the authoritative mode for the active session. */
  async refresh(signal?: AbortSignal): Promise<FreedomMode> {
    const session = this.session;
    if (!session) return this.mode;
    const response = await this.client.getMode(session, signal);
    this.observeRemote(response.mode);
    return this.mode;
  }

  /**
   * Switch modes. The in-memory mirror changes first so the gate reacts even if
   * the backend is unreachable; the result reports whether it was persisted.
   */
  async set(
    mode: FreedomMode,
    opts: { session?: string | undefined; signal?: AbortSignal | undefined } = {},
  ): Promise<SetModeResult> {
    const session = opts.session ?? this.session;
    if (opts.session) this.session = opts.session;
    this.mode = mode;
    if (!session) {
      return {
        mode,
        persisted: false,
        session: undefined,
        error:
          "no InSAR session bound yet — the mode applies to this pi session only until " +
          "an insar_* tool runs against a session",
      };
    }
    try {
      const response = await this.client.setMode(session, mode, opts.signal);
      this.observeRemote(response.mode);
      return { mode: this.mode, persisted: true, session };
    } catch (error) {
      return {
        mode,
        persisted: false,
        session,
        error: error instanceof Error ? error.message : String(error),
      };
    }
  }
}

function describe(controller: ModeController): string {
  const session = controller.activeSession();
  const scope = session ? `session ${session}` : "no InSAR session bound yet";
  return controller.current() === "strict"
    ? `insar mode: strict (${scope}) — only read and insar_* tools run; /${MODE_COMMAND} free to relax`
    : `insar mode: free (${scope}) — all pi tools available; /${MODE_COMMAND} strict for reproducible runs`;
}

function notify(ctx: ExtensionContext, text: string, level: "info" | "warning" | "error"): void {
  if (ctx.hasUI) ctx.ui.notify(text, level);
}

/**
 * Wire the `/insar-mode` command plus the `--insar-strict` / `--insar-session`
 * CLI flags. Flags are registered eagerly (the CLI parses them at startup) and
 * read at `session_start`, per pi's flag lifecycle.
 */
export function installModeControls(pi: ExtensionAPI, controller: ModeController): void {
  pi.registerFlag(STRICT_FLAG, {
    description: "Start InSAR strict reproducible mode (only read and insar_* tools)",
    type: "boolean",
    default: false,
  });
  pi.registerFlag(SESSION_FLAG, {
    description: "Bind this pi session to an existing InSAR backend session id",
    type: "string",
  });

  pi.on("session_start", async (_event, ctx) => {
    const session = pi.getFlag(SESSION_FLAG);
    if (typeof session === "string" && session.length > 0) controller.noteSession(session);
    if (pi.getFlag(STRICT_FLAG) === true) {
      const result = await controller.set("strict");
      if (!result.persisted && result.session) {
        notify(ctx, `insar: strict mode is local only (${result.error})`, "warning");
      }
    }
  });

  pi.registerCommand(MODE_COMMAND, {
    description: "Show or switch the InSAR freedom mode (free | strict)",
    getArgumentCompletions: (prefix) =>
      [...FREEDOM_MODES, "status"]
        .filter((value) => value.startsWith(prefix))
        .map((value) => ({ value, label: value })),
    handler: async (args, ctx) => {
      const [requested, sessionOverride] = args.trim().split(/\s+/).filter(Boolean);
      if (!requested || requested === "status") {
        notify(ctx, describe(controller), "info");
        return;
      }
      if (!isFreedomMode(requested)) {
        notify(ctx, `insar: unknown mode ${requested} (use free or strict)`, "error");
        return;
      }
      const result = await controller.set(requested, { session: sessionOverride });
      if (result.persisted) {
        notify(ctx, describe(controller), "info");
      } else {
        notify(ctx, `${describe(controller)} — not persisted: ${result.error}`, "warning");
      }
    },
  });
}
