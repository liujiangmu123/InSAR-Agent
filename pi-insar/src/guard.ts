/**
 * Strict-mode gate for graduated freedom.
 *
 * Free mode (the default) never blocks anything: pi keeps full agency and the
 * InSAR tools are merely the recommended path. Strict mode narrows the agent to
 * `read` plus the `insar_*` tools so every scientific action lands in the
 * provenance ledger instead of an untracked shell command.
 *
 * pi's `tool_call` hook fails closed — a handler that throws blocks the call —
 * so `installGuard` wraps the whole decision in a catch-all that falls back to
 * "do not block". A bug in here must never cost a free-mode user their tools.
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import type { FreedomMode } from "./backendClient.ts";
import type { ModeController } from "./mode.ts";
import { MODE_COMMAND } from "./mode.ts";

/** Prefix every tool of this extension carries (never override pi built-ins). */
export const INSAR_TOOL_PREFIX = "insar_";

/** Built-ins that stay available in strict mode: reading is side-effect free. */
export const STRICT_ALLOWED_TOOLS: readonly string[] = ["read"];

export interface BlockDecision {
  block: boolean;
  reason?: string;
}

const ALLOW: BlockDecision = { block: false };

function strictReason(toolName: string): string {
  return (
    `strict reproducible mode: ${toolName} is blocked. Use the InSAR tools instead ` +
    `(insar_plan_run / insar_preview_change / insar_apply_change / insar_execute_run / ` +
    `insar_read_log) so every step is recorded in the provenance ledger. ` +
    `read is still allowed. Switch with /${MODE_COMMAND} free.`
  );
}

/**
 * Pure policy: should `toolName` be blocked under `mode`?
 *
 * Unknown modes are treated as free — the safe direction, since blocking is the
 * destructive outcome for a user who never asked for strict mode.
 */
export function decideBlock(toolName: string, mode: FreedomMode | string): BlockDecision {
  if (mode !== "strict") return ALLOW;
  if (typeof toolName !== "string" || toolName.length === 0) return ALLOW;
  if (toolName.startsWith(INSAR_TOOL_PREFIX)) return ALLOW;
  if (STRICT_ALLOWED_TOOLS.includes(toolName)) return ALLOW;
  return { block: true, reason: strictReason(toolName) };
}

/**
 * Install the gate. Per-call only: pi's built-in tool set is never disabled
 * globally, so leaving strict mode restores everything immediately.
 */
export function installGuard(pi: ExtensionAPI, controller: ModeController): void {
  pi.on("tool_call", (event) => {
    try {
      const decision = decideBlock(event.toolName, controller.current());
      if (!decision.block) return undefined;
      return { block: true, ...(decision.reason ? { reason: decision.reason } : {}) };
    } catch {
      // Fail open: `tool_call` errors block the call, and a broken gate must not
      // strand a free-mode session without bash/write/edit.
      return undefined;
    }
  });
}
