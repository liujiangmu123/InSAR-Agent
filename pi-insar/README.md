# @insar-agent/pi-insar

The InSAR agent as a [pi](https://github.com/earendil-works/pi-mono) extension (P1 of
`reference/PLAN-pi-foundation-P0-2026-08-14.md`).

pi is the top-level process and owns the chat UI. This extension adds:

- **`insar_*` tools** — high-level operations the model calls (plan, execute, preview,
  fork, provenance, logs, mode). They talk HTTP to the existing Python FastAPI backend
  (`src/insar_agent/api`), which keeps the reproducible science kernel unchanged.
- **Domain knowledge** — `APPEND_SYSTEM.md` (mission context + red lines + the two
  freedom modes) and the `00-insar-agent` operator skill on top of the repo's 11
  per-step skills and 4 scenario packs (P2).
- **A monitored-pipeline sidebar** — the 11 steps × five stages, current step, progress,
  evidence level and taints, rendered in pi's native TUI above the editor plus a compact
  footer line.
- **Graduated freedom** — free by default (pi keeps full agency); optional strict mode
  narrows the agent to `read` + `insar_*` so every scientific action lands in the
  provenance ledger.

```
user ──▶ pi (TUI)
            │  loads pi-insar
            │  widget: monitored pipeline · footer: run / evidence / mode
            ▼
      insar_* tools ──HTTP──▶ FastAPI backend ──▶ reproducible kernel
```

## Install

```bash
cd pi-insar
npm install   # see "npm note" below if you regenerate the lockfile
```

Run everything with one command (from anywhere):

```bash
scripts/insar-pi                    # interactive, free mode
scripts/insar-pi --insar-strict     # start in strict reproducible mode
```

`scripts/insar-pi` checks `GET $INSAR_API_BASE/api/health` first and prints how to start
the backend if it is down (it never spawns it), then execs pi with **additive flags only**:
`-e pi-insar/src/index.ts`, `--skill` for each skill root, and `--append-system-prompt
pi-insar/APPEND_SYSTEM.md`. It never passes `--system-prompt`, `--no-extensions`,
`--no-skills`, `--no-builtin-tools` or `--tools`: taking pi capabilities away is the
strict-mode gate's job, per tool call, not the launcher's.

Or wire it up by hand:

```bash
INSAR_API_BASE=http://127.0.0.1:8873 pi -e ./pi-insar/src/index.ts
```

Or drop it in an auto-discovered location (`~/.pi/agent/extensions/`, `.pi/extensions/`)
so `/reload` picks it up. The package declares `pi.extensions`, so `pi install` works too.

Backend (started separately):

```bash
INSAR_HOME=<dir> INSAR_PORT=8873 .venv/bin/python -m insar_agent.api.app
```

### Configuration

| Knob | Meaning |
| --- | --- |
| `INSAR_API_BASE` | Backend base URL (default `http://127.0.0.1:8873`) |
| `INSAR_PI_SKIP_HEALTH=1` | `scripts/insar-pi` only: start pi without the backend check |
| `--insar-session <id>` | Bind this pi session to an existing InSAR session (sidebar starts immediately) |
| `--insar-strict` | Start in strict reproducible mode |
| `/insar-mode [free\|strict\|status]` | Show or switch the freedom mode at runtime |

## Prompt and skills

| Resource | Role |
| --- | --- |
| `APPEND_SYSTEM.md` | Appended to pi's system prompt (never replaces it): the 11-step pipeline, the red lines, and the free/strict modes. Kept small — it is in every request. |
| `skills/00-insar-agent/SKILL.md` | Hand-written operator skill: how to drive the tool layer end to end, the five stages, stale/dirty cascade, the six-level evidence ladder, and the "never fabricate a number" rule. |
| `../skills/01-*` … `../skills/11-*` | The repo's per-step domain skills (methods, parameter heuristics, failure playbooks). Unchanged, loaded in place. |
| `../src/insar_agent/registry/scenario_packs/*/SKILL.md` | Scenario packs (`quake`, `permafrost`, `landslide`, `stripmap_coseismic`). |

**Skills are not copied.** pi's `--skill <dir>` recurses and loads every directory holding a
`SKILL.md`, so the launcher points at the three roots above and the repo `skills/` stays the
single source of truth — nothing can go stale. `scripts/sync-skills.mjs` therefore *validates*
that wiring by default (frontmatter, name/description limits, duplicate names, missing roots)
and exits non-zero on a problem:

```bash
npm run skills                 # validate every skill root
node scripts/sync-skills.mjs --json
node scripts/sync-skills.mjs --copy   # packaging only: copy into pi-insar/skills/
```

`--copy` exists for shipping pi-insar as a standalone pi package (pi auto-discovers a
`skills/` directory inside a package). Copies are idempotent, marked with `.synced-from`,
pruned when their source disappears, and gitignored — only `skills/00-insar-agent/` is
tracked.

## Tools

| Tool | Backend | Notes |
| --- | --- | --- |
| `insar_health` | `GET /api/health` | Reachability + version |
| `insar_create_session` | `POST /api/sessions` | Also binds the sidebar/gate to that session |
| `insar_list_sessions` | `GET /api/sessions` | |
| `insar_plan_run` | `POST /api/turn` → `GET /api/runs` → `GET /api/monitor` | Rules-path planning; no LLM key needed |
| `insar_run_status` | `GET /api/monitor` | Same payload the sidebar renders |
| `insar_execute_run` | `POST /api/pipeline` → `GET /api/monitor` | Consumes the NDJSON stream to completion |
| `insar_preview_change` | `GET /api/impact` | Dry run: what a change invalidates |
| `insar_apply_change` | `POST /api/actions` | `SET_METHOD` and/or `SET_PARAMS` |
| `insar_intervene` | `POST /api/actions`, `POST /api/abort` | `PAUSE`/`PLAY`/`RESET`/`SKIP`; `KILL` goes through `/api/abort` |
| `insar_fork_run` | `POST /api/fork` | Branch with per-step changes |
| `insar_export_provenance` | `GET /api/provenance`, `GET /api/run.sh` | `kind=ledger \| run_sh` |
| `insar_list_datasets` | `GET /api/datasets` | |
| `insar_env_probe` | `GET /api/env` | Engines, credentials, thresholds |
| `insar_read_log` | `GET /api/logs` | Tail of a step log |
| `insar_set_mode` / `insar_get_mode` | `GET`/`POST /api/mode` | Freedom mode |

Every name is prefixed `insar_`: registering `read`/`bash`/`write` would override pi's
built-ins for the whole session. The extension is purely additive — it never disables a
built-in tool globally, and strict mode blocks per call through the `tool_call` hook only.

JSON-valued arguments (`params_json`, `changes_json`) are passed as JSON **object
strings** because `Type.Union`/free-form objects are not portable across model providers;
they are parsed and validated before any HTTP call.

## Module layout

| File | Responsibility |
| --- | --- |
| `src/backendClient.ts` | Typed `fetch` client; JSON, NDJSON streams and text endpoints; `AbortSignal` support |
| `src/tools.ts` | The `insar_*` tool definitions and `registerInsarTools` |
| `src/mode.ts` | `ModeController` (in-memory mirror of the backend mode) + `/insar-mode` + CLI flags |
| `src/guard.ts` | Pure `decideBlock(tool, mode)` + the exception-safe `tool_call` hook |
| `src/sidebar.ts` | Pure `renderPipelineRail(monitor)` + the `ctx.hasUI`-gated widget poller |
| `src/index.ts` | Extension factory wiring everything together |
| `APPEND_SYSTEM.md` | InSAR mission context + red lines + modes, appended to the system prompt |
| `skills/00-insar-agent/` | Operator skill for the tool layer (hand-written, tracked) |
| `scripts/sync-skills.mjs` | Validates the skill roots; `--copy` materialises them for packaging |
| `../scripts/insar-pi` | Launcher: health check + `pi` with additive flags only |

Design constraints worth keeping:

- **The gate fails open.** pi's `tool_call` hook fails *closed* (a thrown handler blocks
  the call), so `installGuard` wraps the whole decision in a catch-all. A bug in the gate
  must never strand a free-mode session without `bash`.
- **All `ctx.ui.*` calls are gated on `ctx.hasUI`**, so the extension is a silent no-op in
  `rpc`/`json`/`print` modes.
- **No background work in the factory.** pi loads extensions in invocations that never
  open a session; the poller starts at `session_start` and stops at `session_shutdown`,
  and the health check is lazy.
- **The sidebar polls `/api/monitor`.** SSE (`/api/events`) is intentionally not wired
  yet: one request carries the whole sidebar contract and cannot desynchronise from the
  ledger.

## Glyphs

`✔` done · `–` skipped · `○` pending · `✖` failed · `!` stale (taint) ·
`P`/`L`/`R`/`C`/`V` the five stages of a running step.

```
insar · b0c36d18 · quake · running · simulated
✔–––––✔L○○○  7/11 · 64%
▶ 08 误差校正 · tropo_era5_pyaps · LAUNCHED
evidence runnable · mode free · 0 stale
```

## Tests

```bash
npx tsc --noEmit
npx vitest run
```

No LLM key is required. `test/backend.globalSetup.ts` spawns a real backend on
`INSAR_TEST_PORT` (default 8899, so it never collides with a dev server on 8873) with a
temporary `INSAR_HOME`, waits for `/api/health`, and tears it down afterwards. The
integration suite drives `insar_create_session → insar_plan_run → insar_execute_run →
insar_run_status → insar_export_provenance → insar_set_mode` by calling the tools'
`execute()` directly, since they are plain objects with no pi runtime dependency.
`render.test.ts` and `guard.test.ts` are pure golden/unit tests. `skills.test.ts` needs
neither: it validates the frontmatter of every skill the launcher loads (through
`scripts/sync-skills.mjs --json`), checks that `APPEND_SYSTEM.md` and the operator skill
name all 16 registered tools, and asserts the launcher only ever passes additive pi flags.

### npm note

`package-lock.json` is committed, so `npm install` resolves from it. Regenerating it from
scratch (no lockfile) crashes npm 10.9.8 with `Cannot read properties of null (reading
'edgesOut')` while it resolves vitest 4's optional peer dependencies; use
`npm install --legacy-peer-deps` in that case, which produced this lockfile.
