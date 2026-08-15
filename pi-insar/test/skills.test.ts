/**
 * The domain-knowledge layer: the operator skill, the appended system prompt,
 * the skill roots the launcher hands to `pi --skill`, and the launcher itself.
 *
 * No LLM key and no backend are needed — everything here is static content plus
 * one subprocess call to `scripts/sync-skills.mjs`, which is also the single
 * source of truth for the skill roots.
 */

import { execFileSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, readdirSync, rmSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { BackendClient } from "../src/backendClient.ts";
import { ModeController } from "../src/mode.ts";
import { createInsarTools } from "../src/tools.ts";

const PACKAGE_DIR = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const REPO_ROOT = resolve(PACKAGE_DIR, "..");
const SYNC_SCRIPT = join(PACKAGE_DIR, "scripts", "sync-skills.mjs");
const APPEND_SYSTEM = join(PACKAGE_DIR, "APPEND_SYSTEM.md");
const OPERATOR_SKILL = join(PACKAGE_DIR, "skills", "00-insar-agent", "SKILL.md");
const LAUNCHER = join(REPO_ROOT, "scripts", "insar-pi");
const LAUNCHER_PS = join(REPO_ROOT, "scripts", "insar-pi.ps1");   // 常量区,紧邻既有 LAUNCHER

interface SyncSkill {
  name: string;
  description: string;
  file: string;
  dir: string;
  root: string;
}

interface SyncReport {
  repoRoot: string;
  roots: Array<{ path: string; required: boolean; copy: boolean }>;
  skills: SyncSkill[];
  errors: string[];
  warnings: string[];
}

function runSync(args: string[] = []): SyncReport {
  const stdout = execFileSync(process.execPath, [SYNC_SCRIPT, "--json", ...args], {
    encoding: "utf8",
  });
  return JSON.parse(stdout) as SyncReport;
}

/** Every SKILL.md under a directory, using pi's rule: a dir with SKILL.md is a leaf. */
function skillFiles(dir: string, found: string[] = []): string[] {
  if (!existsSync(dir)) return found;
  if (existsSync(join(dir, "SKILL.md"))) {
    found.push(join(dir, "SKILL.md"));
    return found;
  }
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (!entry.isDirectory() || entry.name.startsWith(".") || entry.name === "node_modules") continue;
    skillFiles(join(dir, entry.name), found);
  }
  return found;
}

function frontmatter(file: string): Record<string, string> {
  const match = /^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/.exec(readFileSync(file, "utf8"));
  expect(match, `${file} has no frontmatter block`).not.toBeNull();
  const fields: Record<string, string> = {};
  for (const line of (match?.[1] ?? "").split(/\r?\n/)) {
    const field = /^([A-Za-z0-9_-]+):[ \t]*(.*)$/.exec(line);
    if (!field?.[1]) continue;
    fields[field[1]] = (field[2] ?? "").trim().replace(/^["'](.*)["']$/s, "$1");
  }
  return fields;
}

const TOOL_NAMES = createInsarTools(new BackendClient({}), new ModeController(new BackendClient({})))
  .map((tool) => tool.name)
  .sort();

describe("skill roots the launcher loads", () => {
  const report = runSync();

  it("validates every skill without errors", () => {
    expect(report.errors).toEqual([]);
    // 1 operator skill + 11 per-step skills + 4 scenario packs.
    expect(report.skills.length).toBeGreaterThanOrEqual(12);
  });

  it("exposes the 11 per-step domain skills plus the operator skill", () => {
    const names = report.skills.map((skill) => skill.name);
    for (const expected of [
      "00-insar-agent",
      "01-data-acquisition",
      "02-dem-preparation",
      "03-coregistration",
      "04-interferogram",
      "05-phase-filtering",
      "06-unwrap",
      "07-sbas-inversion",
      "08-tropo-correction",
      "09-deformation-model",
      "10-figure-export",
      "11-crossval-qa",
    ]) {
      expect(names).toContain(expected);
    }
    expect(new Set(names).size).toBe(names.length);
  });

  it("gives every skill a non-empty name and description within pi's limits", () => {
    for (const skill of report.skills) {
      expect(skill.name.length, `${skill.file} name`).toBeGreaterThan(0);
      expect(skill.name.length).toBeLessThanOrEqual(64);
      expect(skill.description.length, `${skill.file} description`).toBeGreaterThan(0);
      expect(skill.description.length).toBeLessThanOrEqual(1024);
    }
  });
});

describe("pi-insar/skills", () => {
  const files = skillFiles(join(PACKAGE_DIR, "skills"));

  it("has valid frontmatter in every SKILL.md, generated copies included", () => {
    expect(files.length).toBeGreaterThan(0);
    for (const file of files) {
      const fields = frontmatter(file);
      expect(fields.name ?? "", `${file} name`).not.toBe("");
      expect(fields.description ?? "", `${file} description`).not.toBe("");
    }
  });

  it("ships the hand-written operator skill", () => {
    expect(files).toContain(OPERATOR_SKILL);
    const fields = frontmatter(OPERATOR_SKILL);
    expect(fields.name).toBe("00-insar-agent");
    // The name must satisfy the Agent Skills rules for skills we author.
    expect(fields.name).toMatch(/^[a-z0-9]+(-[a-z0-9]+)*$/);
  });
});

describe("00-insar-agent operator skill", () => {
  const text = readFileSync(OPERATOR_SKILL, "utf8");

  it("documents every registered insar_ tool", () => {
    expect(TOOL_NAMES).toHaveLength(25);
    for (const name of TOOL_NAMES) expect(text, `operator skill misses ${name}`).toContain(name);
  });

  it("teaches the five stages, the dirty cascade and the evidence ladder", () => {
    for (const stage of ["PREPARED", "LAUNCHED", "RUNNING", "COLLECTED", "VERIFIED"]) {
      expect(text).toContain(stage);
    }
    for (const reason of [
      "method_changed",
      "param_changed",
      "upstream_changed",
      "tool_upgraded",
      "artifact_missing",
    ]) {
      expect(text).toContain(reason);
    }
    for (const level of ["runnable", "checked", "audited", "calibrated", "validated", "publishable"]) {
      expect(text).toContain(level);
    }
  });

  it("names the other domain skills so the model knows they exist", () => {
    for (const skill of [
      "01-data-acquisition",
      "06-unwrap",
      "11-crossval-qa",
      "quake",
      "permafrost",
      "landslide",
      "stripmap_coseismic",
    ]) {
      expect(text).toContain(skill);
    }
  });
});

describe("APPEND_SYSTEM.md", () => {
  const text = readFileSync(APPEND_SYSTEM, "utf8");

  it("exists and names every insar_ tool", () => {
    expect(text.length).toBeGreaterThan(0);
    for (const name of TOOL_NAMES) expect(text, `APPEND_SYSTEM.md misses ${name}`).toContain(name);
  });

  it("states the execution red line and both freedom modes", () => {
    expect(text).toContain("insar_execute_run");
    expect(text).toContain("insar_preview_change");
    expect(text).toContain("insar_apply_change");
    expect(text).toContain("free");
    expect(text).toContain("strict");
    expect(text).toContain("simulated");
    expect(text).toContain("provenance");
  });

  it("stays inside a sane context budget", () => {
    expect(Buffer.byteLength(text, "utf8")).toBeLessThan(8_000);
  });
});

describe("scripts/insar-pi launcher", () => {
  const text = readFileSync(LAUNCHER, "utf8");
  // The header comment documents the flags the launcher refuses to pass, so the
  // "never passes X" assertions run against the code only.
  const code = text
    .split("\n")
    .filter((line) => !/^\s*#/.test(line))
    .join("\n");

  it("is executable", () => {
    // Windows 无 Unix 权限位;可执行性由 shebang 与 git 跟踪保证,跳过位检查。
    if (process.platform === "win32") return;
    expect(statSync(LAUNCHER).mode & 0o111).toBeGreaterThan(0);
  });

  it("passes only additive pi flags", () => {
    for (const flag of ["-e ", "--skill", "--append-system-prompt"]) expect(code).toContain(flag);
    // Anything that would take pi capabilities away must never appear.
    for (const flag of [
      "--system-prompt",
      "--no-extensions",
      "--no-skills",
      "--no-builtin-tools",
      "--no-tools",
      "--tools",
      "--exclude-tools",
    ]) {
      expect(code, `launcher must not pass ${flag}`).not.toContain(flag);
    }
  });

  it("loads the same skill roots the sync script validates", () => {
    for (const root of runSync().roots) {
      if (!existsSync(join(REPO_ROOT, root.path))) continue;
      expect(text, `launcher misses skill root ${root.path}`).toContain(`/${root.path}"`);
    }
  });

  it("appends the InSAR prompt instead of replacing pi's own", () => {
    expect(code).toContain("pi-insar/APPEND_SYSTEM.md");
    expect(code).toMatch(/--append-system-prompt.+APPEND_SYSTEM/s);
  });

  it("explains how to start the backend when it is down", () => {
    expect(text).toContain("insar_agent.api.app");
    expect(text).toContain("INSAR_API_BASE");
  });

  it("wires the insar-dark theme additively", () => {
    expect(text).toContain("--theme");
    expect(text).toContain("insar-dark.json");
  });
});

describe("scripts/insar-pi.ps1 launcher (Windows)", () => {
  const text = readFileSync(LAUNCHER_PS, "utf8");

  it("loads the same extension, skills and system prompt as the bash launcher", () => {
    expect(text).toContain("pi-insar\\src\\index.ts");
    expect(text).toContain("pi-insar\\APPEND_SYSTEM.md");
    expect(text).toContain("pi-insar\\skills\\00-insar-agent");
    expect(text).toContain("src\\insar_agent\\registry\\scenario_packs");
    expect(text).toContain("--append-system-prompt");
  });

  it("never passes capability-removing flags", () => {
    for (const flag of ["--system-prompt ", "--no-extensions", "--no-skills", "--no-builtin-tools", "--tools "]) {
      expect(text).not.toContain(flag);
    }
  });

  it("wires the insar-dark theme additively", () => {
    expect(text).toContain("--theme");
    expect(text).toContain("insar-dark.json");
  });
});

describe("sync-skills --copy", () => {
  it("materialises the copyable skills and is idempotent", () => {
    const out = mkdtempSync(join(tmpdir(), "pi-insar-skills-"));
    try {
      const first = runSync(["--copy", "--out", out]);
      expect(first.errors).toEqual([]);
      const copied = skillFiles(out);
      const copyableRoots = first.roots.filter((root) => root.copy).map((root) => root.path);
      const expectedCount = first.skills.filter((skill) => copyableRoots.includes(skill.root)).length;
      expect(copied).toHaveLength(expectedCount);
      expect(existsSync(join(out, "06-unwrap", "SKILL.md"))).toBe(true);

      const mtimes = copied.map((file) => statSync(file).mtimeMs);
      runSync(["--copy", "--out", out]);
      // Idempotent: a second pass rewrites nothing.
      expect(copied.map((file) => statSync(file).mtimeMs)).toEqual(mtimes);
      expect(skillFiles(out)).toHaveLength(expectedCount);
    } finally {
      rmSync(out, { recursive: true, force: true });
    }
  });
});
