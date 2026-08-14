/**
 * Spawn a throwaway InSAR backend for the integration suite.
 *
 * A dev server usually occupies the default port 8873; this one binds
 * INSAR_TEST_PORT (8899 by default) with a temporary INSAR_HOME so the suite
 * never touches a real workspace or database.
 */

import { spawn, type ChildProcess } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

const PYTHON = process.env.INSAR_TEST_PYTHON ?? "/workspace/.venv/bin/python";
const PORT = Number(process.env.INSAR_TEST_PORT ?? 8899);
const HOST = "127.0.0.1";
const READY_TIMEOUT_MS = 30_000;

let child: ChildProcess | undefined;
let home: string | undefined;
let logTail: string[] = [];

async function waitForHealth(baseUrl: string, deadline: number): Promise<void> {
  let lastError = "not started";
  while (Date.now() < deadline) {
    if (child?.exitCode !== null && child?.exitCode !== undefined) {
      throw new Error(
        `backend exited early with code ${child.exitCode}\n${logTail.join("")}`,
      );
    }
    try {
      const response = await fetch(`${baseUrl}/api/health`, {
        signal: AbortSignal.timeout(1_000),
      });
      if (response.ok) return;
      lastError = `HTTP ${response.status}`;
    } catch (error) {
      lastError = error instanceof Error ? error.message : String(error);
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(
    `backend did not become healthy on ${baseUrl} within ${READY_TIMEOUT_MS}ms ` +
      `(last error: ${lastError})\n${logTail.join("")}`,
  );
}

export async function setup(): Promise<void> {
  const baseUrl = `http://${HOST}:${PORT}`;
  home = await mkdtemp(join(tmpdir(), "pi-insar-test-"));

  child = spawn(PYTHON, ["-m", "insar_agent.api.app"], {
    cwd: "/workspace",
    env: {
      ...process.env,
      INSAR_HOME: home,
      INSAR_PORT: String(PORT),
      INSAR_HOST: HOST,
      // Engines are absent in CI: the simulated executor is what makes a
      // key-free end-to-end run possible.
      INSAR_ALLOW_SIMULATED: "1",
    },
    stdio: ["ignore", "pipe", "pipe"],
  });

  const record = (chunk: Buffer): void => {
    logTail.push(chunk.toString());
    if (logTail.length > 50) logTail = logTail.slice(-50);
  };
  child.stdout?.on("data", record);
  child.stderr?.on("data", record);

  await waitForHealth(baseUrl, Date.now() + READY_TIMEOUT_MS);

  process.env.INSAR_API_BASE = baseUrl;
  process.env.INSAR_TEST_HOME = home;
}

export async function teardown(): Promise<void> {
  if (child && child.exitCode === null) {
    const exited = new Promise<void>((resolve) => child?.once("exit", () => resolve()));
    child.kill("SIGTERM");
    const timer = setTimeout(() => child?.kill("SIGKILL"), 5_000);
    await exited;
    clearTimeout(timer);
  }
  child = undefined;
  if (home) {
    await rm(home, { recursive: true, force: true });
    home = undefined;
  }
}
