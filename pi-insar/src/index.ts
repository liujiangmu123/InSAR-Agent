/**
 * pi-insar — the InSAR agent as a pi extension.
 *
 * Topology (reference/PLAN-pi-foundation-P0-2026-08-14.md §2): pi is the
 * top-level process and owns the chat UI; this extension registers `insar_*`
 * tools that call the existing Python FastAPI backend over HTTP, mirrors the
 * monitored pipeline into the TUI, and implements graduated freedom
 * (free by default, strict on request).
 *
 * The factory only registers things. No sockets, watchers or timers start here:
 * pi loads extensions in invocations that never open a session (`--list-models`,
 * flag parsing), so background work is deferred to `session_start` and the
 * health check is lazy.
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { BackendClient, type BackendClientOptions } from "./backendClient.ts";
import { installGuard } from "./guard.ts";
import { installModeControls, ModeController } from "./mode.ts";
import { installSidebar, type SidebarOptions } from "./sidebar.ts";
import { registerInsarTools } from "./tools.ts";

export interface InsarExtensionOptions {
  client?: BackendClient;
  clientOptions?: BackendClientOptions;
  sidebar?: SidebarOptions | false;
}

export function createInsarExtension(options: InsarExtensionOptions = {}) {
  return function insarExtension(pi: ExtensionAPI): void {
    const client = options.client ?? new BackendClient(options.clientOptions ?? {});
    const controller = new ModeController(client);

    registerInsarTools(pi, client, controller);
    installModeControls(pi, controller);
    installGuard(pi, controller);
    if (options.sidebar !== false) installSidebar(pi, client, controller, options.sidebar ?? {});

    pi.on("session_start", async (_event, ctx) => {
      // Lazy reachability check: a down backend is worth one warning, not a
      // failed startup — the user may be about to start it in another pane.
      try {
        await client.health();
      } catch (error) {
        if (!ctx.hasUI) return;
        const detail = error instanceof Error ? error.message : String(error);
        ctx.ui.notify(`insar: backend not reachable at ${client.baseUrl} (${detail})`, "warning");
      }
    });
  };
}

export default createInsarExtension();

export { BackendClient, BackendError } from "./backendClient.ts";
export type { MonitorResponse } from "./backendClient.ts";
export { decideBlock, installGuard, INSAR_TOOL_PREFIX, STRICT_ALLOWED_TOOLS } from "./guard.ts";
export { type FreedomMode, installModeControls, ModeController } from "./mode.ts";
export { installSidebar, renderPipelineRail, renderStatusLine, stepGlyph } from "./sidebar.ts";
export { createInsarTools, type InsarTool, registerInsarTools, toolsByName } from "./tools.ts";
