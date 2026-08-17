import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { DEFAULT_BASE_URL, resolveBaseUrl } from "../src/backendClient.ts";

const EMPTY_ENV: NodeJS.ProcessEnv = {};

describe("resolveBaseUrl", () => {
  const temps: string[] = [];

  afterEach(() => {
    for (const dir of temps.splice(0)) {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  function workspaceWithDesktopApi(apiBase: string): string {
    const root = mkdtempSync(join(tmpdir(), "pi-insar-desktop-"));
    temps.push(root);
    mkdirSync(join(root, ".pi"));
    writeFileSync(
      join(root, ".pi", "insar-desktop.json"),
      JSON.stringify({ apiBase, session: "real" }),
    );
    return root;
  }

  it("reads apiBase from .pi/insar-desktop.json when INSAR_API_BASE is unset", () => {
    const root = workspaceWithDesktopApi("http://127.0.0.1:17999");
    expect(resolveBaseUrl(EMPTY_ENV, root)).toBe("http://127.0.0.1:17999");
  });

  it("prefers INSAR_API_BASE over .pi/insar-desktop.json", () => {
    const root = workspaceWithDesktopApi("http://127.0.0.1:17999");
    expect(resolveBaseUrl({ INSAR_API_BASE: "http://127.0.0.1:18001" }, root)).toBe(
      "http://127.0.0.1:18001",
    );
  });

  it("falls back to the default URL when the desktop file is absent", () => {
    const root = mkdtempSync(join(tmpdir(), "pi-insar-desktop-"));
    temps.push(root);
    expect(resolveBaseUrl(EMPTY_ENV, root)).toBe(DEFAULT_BASE_URL);
  });
});
