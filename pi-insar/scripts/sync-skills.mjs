#!/usr/bin/env node
/**
 * Skill wiring for pi-insar.
 *
 * The single source of truth for InSAR domain knowledge stays where it already
 * lives: the repo's `skills/01-*` … `skills/11-*` and the scenario packs under
 * `src/insar_agent/registry/scenario_packs/`. `scripts/insar-pi` points pi's
 * `--skill` at those directories directly (pi recurses into any directory and
 * treats every folder holding a SKILL.md as a skill), so **no copy is needed to
 * run** and nothing can go stale.
 *
 * This script therefore defaults to *validating* that wiring:
 *
 *   node pi-insar/scripts/sync-skills.mjs            # validate every skill root
 *   node pi-insar/scripts/sync-skills.mjs --json     # same, machine readable
 *   node pi-insar/scripts/sync-skills.mjs --copy     # also materialise copies
 *   node pi-insar/scripts/sync-skills.mjs --copy --out <dir>
 *
 * `--copy` exists for packaging only (shipping pi-insar as a standalone pi
 * package, where pi auto-discovers a `skills/` directory next to the
 * extension). Copies land in `pi-insar/skills/` and are gitignored; the
 * hand-written `skills/00-insar-agent/` is never overwritten and never pruned.
 * The copy is idempotent: identical files are left untouched and previously
 * generated directories that no longer have a source are removed.
 *
 * Exit code is 1 when any skill fails validation (missing/oversized frontmatter,
 * duplicate name, missing required root).
 */

import { existsSync, mkdirSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { basename, dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const PACKAGE_DIR = resolve(SCRIPT_DIR, "..");
const REPO_ROOT = resolve(PACKAGE_DIR, "..");

/** Marker written into every generated copy so pruning never touches a hand-written skill. */
const GENERATED_MARKER = ".synced-from";

/** Agent Skills limits pi enforces (docs/skills.md § Frontmatter). */
const MAX_NAME_LENGTH = 64;
const MAX_DESCRIPTION_LENGTH = 1024;
const NAME_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

/**
 * Skill roots, in the order `scripts/insar-pi` passes them to `pi --skill`.
 * `generated: false` marks the hand-written operator skill: it is a source, not
 * a copy target.
 */
export const SKILL_ROOTS = [
  { path: "pi-insar/skills/00-insar-agent", required: true, copy: false, label: "operator skill (hand-written)" },
  { path: "skills", required: true, copy: true, label: "11 per-step domain skills" },
  { path: "src/insar_agent/registry/scenario_packs", required: false, copy: true, label: "scenario packs" },
];

/** Absolute paths of the roots that exist on disk. */
export function skillRootPaths() {
  return SKILL_ROOTS.map((root) => ({ ...root, absolute: join(REPO_ROOT, root.path) }));
}

/**
 * Find skill directories under `dir`, mirroring pi's discovery rule: a
 * directory holding a SKILL.md is a skill root and is not recursed into.
 */
function findSkillDirs(dir, found = []) {
  if (!existsSync(dir) || !statSync(dir).isDirectory()) return found;
  if (existsSync(join(dir, "SKILL.md"))) {
    found.push(dir);
    return found;
  }
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (!entry.isDirectory() || entry.name.startsWith(".") || entry.name === "node_modules") continue;
    findSkillDirs(join(dir, entry.name), found);
  }
  return found;
}

/**
 * Read the top-level scalars of a SKILL.md frontmatter block.
 *
 * Deliberately not a YAML parser: skills only need `name` and `description`,
 * both single-line scalars at indent 0. Nested blocks (`metadata:` in the
 * scenario packs) are skipped rather than misparsed.
 */
export function parseFrontmatter(text) {
  const match = /^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/.exec(text);
  if (!match) return null;
  const fields = {};
  for (const line of match[1].split(/\r?\n/)) {
    const field = /^([A-Za-z0-9_-]+):[ \t]*(.*)$/.exec(line);
    if (!field) continue;
    let value = field[2].trim();
    if ((value.startsWith('"') && value.endsWith('"') && value.length > 1) ||
        (value.startsWith("'") && value.endsWith("'") && value.length > 1)) {
      value = value.slice(1, -1);
    }
    fields[field[1]] = value;
  }
  return fields;
}

/** Load and validate one skill directory. */
function loadSkill(dir, rootLabel) {
  const file = join(dir, "SKILL.md");
  const errors = [];
  const warnings = [];
  const fields = parseFrontmatter(readFileSync(file, "utf8"));

  if (fields === null) {
    errors.push("no YAML frontmatter block");
  } else {
    if (!fields.name) errors.push("frontmatter has no name");
    else if (fields.name.length > MAX_NAME_LENGTH) errors.push(`name exceeds ${MAX_NAME_LENGTH} chars`);
    else if (!NAME_PATTERN.test(fields.name)) warnings.push(`name "${fields.name}" is not lowercase-a-z0-9-hyphens (pi warns but loads)`);
    if (!fields.description) errors.push("frontmatter has no description");
    else if (fields.description.length > MAX_DESCRIPTION_LENGTH) errors.push(`description exceeds ${MAX_DESCRIPTION_LENGTH} chars`);
  }

  return {
    dir: relative(REPO_ROOT, dir),
    file: relative(REPO_ROOT, file),
    root: rootLabel,
    name: fields?.name ?? "",
    description: fields?.description ?? "",
    errors,
    warnings,
  };
}

/** Discover + validate every skill the launcher loads. */
export function collectSkills() {
  const skills = [];
  const errors = [];
  const warnings = [];

  for (const root of skillRootPaths()) {
    if (!existsSync(root.absolute)) {
      const message = `skill root missing: ${root.path}`;
      if (root.required) errors.push(message);
      else warnings.push(`${message} (optional)`);
      continue;
    }
    const dirs = findSkillDirs(root.absolute);
    if (dirs.length === 0) warnings.push(`skill root has no SKILL.md: ${root.path}`);
    for (const dir of dirs) skills.push({ ...loadSkill(dir, root.path), copyable: root.copy });
  }

  const seen = new Map();
  for (const skill of skills) {
    if (!skill.name) continue;
    const previous = seen.get(skill.name);
    if (previous) skill.errors.push(`duplicate name "${skill.name}" (also ${previous})`);
    else seen.set(skill.name, skill.file);
  }

  for (const skill of skills) {
    for (const error of skill.errors) errors.push(`${skill.file}: ${error}`);
    for (const warning of skill.warnings) warnings.push(`${skill.file}: ${warning}`);
  }

  return { skills, errors, warnings };
}

function copyTree(from, to) {
  let written = 0;
  mkdirSync(to, { recursive: true });
  for (const entry of readdirSync(from, { withFileTypes: true })) {
    if (entry.name.startsWith(".")) continue;
    const source = join(from, entry.name);
    const target = join(to, entry.name);
    if (entry.isDirectory()) {
      written += copyTree(source, target);
      continue;
    }
    if (!entry.isFile()) continue;
    const content = readFileSync(source);
    // Idempotence: an unchanged file keeps its mtime so watchers stay quiet.
    if (existsSync(target) && readFileSync(target).equals(content)) continue;
    writeFileSync(target, content);
    written += 1;
  }
  return written;
}

/** Materialise copies of the copyable skills into `outDir`. */
function copySkills(skills, outDir) {
  mkdirSync(outDir, { recursive: true });
  const sources = skills.filter((skill) => skill.copyable && skill.errors.length === 0);
  const expected = new Set(sources.map((skill) => basename(skill.dir)));
  let written = 0;

  for (const skill of sources) {
    const from = join(REPO_ROOT, skill.dir);
    const to = join(outDir, basename(skill.dir));
    written += copyTree(from, to);
    const marker = `${skill.dir}\n`;
    const markerPath = join(to, GENERATED_MARKER);
    if (!existsSync(markerPath) || readFileSync(markerPath, "utf8") !== marker) {
      writeFileSync(markerPath, marker);
    }
  }

  const pruned = [];
  for (const entry of readdirSync(outDir, { withFileTypes: true })) {
    if (!entry.isDirectory() || expected.has(entry.name)) continue;
    // Only ever prune directories this script generated.
    if (!existsSync(join(outDir, entry.name, GENERATED_MARKER))) continue;
    rmSync(join(outDir, entry.name), { recursive: true, force: true });
    pruned.push(entry.name);
  }

  return { copied: sources.length, written, pruned };
}

function parseArgs(argv) {
  const options = { copy: false, json: false, quiet: false, out: join(PACKAGE_DIR, "skills") };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--copy") options.copy = true;
    else if (arg === "--json") options.json = true;
    else if (arg === "--quiet") options.quiet = true;
    else if (arg === "--out") options.out = resolve(argv[++i] ?? "");
    else if (arg === "--help" || arg === "-h") options.help = true;
    else throw new Error(`unknown argument ${arg} (see --help)`);
  }
  return options;
}

const HELP = `sync-skills — validate (and optionally copy) the skills pi-insar loads

Usage: node pi-insar/scripts/sync-skills.mjs [--copy] [--out <dir>] [--json] [--quiet]

  (default)     validate every skill root the launcher passes to pi --skill
  --copy        also copy the repo skills into pi-insar/skills/ (packaging only)
  --out <dir>   destination for --copy (default pi-insar/skills)
  --json        machine-readable report on stdout
  --quiet       only print problems
`;

function main(argv) {
  let options;
  try {
    options = parseArgs(argv);
  } catch (error) {
    process.stderr.write(`${error.message}\n`);
    return 2;
  }
  if (options.help) {
    process.stdout.write(HELP);
    return 0;
  }

  const report = collectSkills();
  const copy = options.copy ? copySkills(report.skills, options.out) : null;

  if (options.json) {
    process.stdout.write(`${JSON.stringify({
      repoRoot: REPO_ROOT,
      roots: SKILL_ROOTS,
      skills: report.skills.map(({ name, description, file, dir, root }) => ({ name, description, file, dir, root })),
      errors: report.errors,
      warnings: report.warnings,
      copy,
    }, null, 2)}\n`);
    return report.errors.length > 0 ? 1 : 0;
  }

  if (!options.quiet) {
    process.stdout.write(`skill roots (${REPO_ROOT}):\n`);
    for (const root of skillRootPaths()) {
      const count = report.skills.filter((skill) => skill.root === root.path).length;
      const state = existsSync(root.absolute) ? `${count} skill(s)` : root.required ? "MISSING" : "absent (optional)";
      process.stdout.write(`  ${root.path.padEnd(42)} ${state} · ${root.label}\n`);
    }
    process.stdout.write(`\n${report.skills.length} skill(s):\n`);
    for (const skill of report.skills) {
      process.stdout.write(`  ${(skill.name || "<no name>").padEnd(22)} ${skill.file}\n`);
    }
    if (copy) {
      process.stdout.write(
        `\ncopied ${copy.copied} skill(s) into ${relative(REPO_ROOT, options.out)} ` +
          `(${copy.written} file(s) written${copy.pruned.length > 0 ? `, pruned ${copy.pruned.join(", ")}` : ""})\n`,
      );
    }
  }

  for (const warning of report.warnings) process.stderr.write(`warning: ${warning}\n`);
  for (const error of report.errors) process.stderr.write(`error: ${error}\n`);
  if (report.errors.length > 0) return 1;
  if (!options.quiet) process.stdout.write("\nOK — every skill has valid frontmatter.\n");
  return 0;
}

if (process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url))) {
  process.exit(main(process.argv.slice(2)));
}
