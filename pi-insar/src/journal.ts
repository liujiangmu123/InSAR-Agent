/**
 * Out-of-ledger journal: mirror every pi tool call into the backend's
 * pi_journal.ndjson. Observation only — it never blocks, never modifies
 * results, and is NOT provenance (the scientific ledger stays untouched).
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import type { BackendClient } from "./backendClient.ts";
import type { ModeController } from "./mode.ts";

const MAX_DIGEST_CHARS = 2_000;

export function digestInput(input: unknown): string {
  try {
    const text = JSON.stringify(input);
    return text.length > MAX_DIGEST_CHARS ? text.slice(0, MAX_DIGEST_CHARS) : text;
  } catch {
    return String(input).slice(0, MAX_DIGEST_CHARS);
  }
}

export function installJournal(
  pi: ExtensionAPI,
  client: BackendClient,
  controller: ModeController,
): void {
  pi.on("tool_result", (event) => {
    const session = controller.activeSession();
    if (!session) return undefined; // 未绑定 InSAR 会话前无处归档,不记
    void client
      .journal({
        session,
        tool: event.toolName,
        mode: controller.current(),
        is_error: event.isError === true,
        input_digest: digestInput(event.input),
        ts: Date.now() / 1000,
      })
      .catch(() => {
        // 观察者不是闸门:后端不可达绝不影响会话。
      });
    return undefined;
  });
}
