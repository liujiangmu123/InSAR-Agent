# @insar-agent/pi-insar

The InSAR agent as a [pi](https://github.com/earendil-works/pi-mono) extension (P1 of
`reference/PLAN-pi-foundation-P0-2026-08-14.md`).

pi is the top-level process and owns the chat UI. This extension adds:

- **31 `insar_*` tools** — high-level operations the model calls (plan, execute, query,
  figures, export, report). They talk HTTP to the existing Python FastAPI backend
  (`src/insar_agent/api`), which keeps the reproducible science kernel unchanged.
  Phases 10–15 add **zero** tools: new science is registry-declared.
- **Domain knowledge** — `APPEND_SYSTEM.md` (mission context + red lines + the two
  freedom modes) and the `00-insar-agent` operator skill on top of the repo's 20
  per-step skills (11 core + 9 analysis) and 8 scenario packs.
- **A monitored-pipeline sidebar** — the 11 core steps × five stages, current step,
  progress, evidence level and taints, rendered in pi's native TUI above the editor
  plus a compact footer line.
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

Delivery plan: [`docs/plan/00-README.md`](docs/plan/00-README.md). Real-data walkthrough:
[`../docs/PI-REAL-SESSION.md`](../docs/PI-REAL-SESSION.md). Do not run
`scripts/real_ridgecrest.py` unless the user has approved that heavy job.

## Quickstart (Windows first)

Install the pinned pi, start a real backend, then start pi. Commands assume the repo root
and PowerShell.

```powershell
# 1) pi 0.84.2 (same version as pi-insar's devDependency)
npm i -g @earendil-works/pi-coding-agent@0.84.2
pi --version   # 0.84.2
node --version # >= 22

# 2) backend — real-mode HOME is workspace\realtest (audited Ridgecrest readback)
pwsh scripts/insar-backend-real.ps1
#    http://127.0.0.1:8873

# 3) pi
pwsh scripts/insar-pi.ps1
pwsh scripts/insar-pi.ps1 --insar-strict
pwsh scripts/insar-pi.ps1 --insar-session real
```

### Desktop 源码启动

从源码跑 pi Desktop（`justhil/pi-app`），不要下安装包。后端先起，再：

```powershell
pwsh scripts/insar-pi-desktop.ps1
pwsh scripts/insar-pi-desktop.ps1 -PiAppRoot E:\SoftApp\pi-app
```

启动器顺序：找 `PiAppRoot` → 自愈 `.pi` junction → 写入进程环境 `INSAR_API_BASE` → 探活 8873 → 缺 `electron.exe` 则跑 `install.js`（可设 `ELECTRON_MIRROR`）→ 缺 `better_sqlite3.node` 则调用 `scripts/fix-pi-desktop-sqlite.ps1` → `npm run dev`。请在窗口里打开工作区 = 仓库根。

Git Bash users can still use `scripts/insar-pi`. Both launchers check
`GET $INSAR_API_BASE/api/health` first and print how to start the backend if it is down
(they never spawn it), then exec pi with **additive flags only**: `-e pi-insar/src/index.ts`,
`--skill` for each skill root, `--append-system-prompt pi-insar/APPEND_SYSTEM.md`, and
`--theme pi-insar/themes/insar-dark.json` when that file exists. They never strip pi
capabilities; taking tools away is the strict-mode gate's job, per tool call, not the
launcher's.

Wire it up by hand:

```powershell
$env:INSAR_API_BASE = "http://127.0.0.1:8873"
pi -e .\pi-insar\src\index.ts
```

Or drop the extension in an auto-discovered location (`~/.pi/agent/extensions/`,
`.pi/extensions/`) so `/reload` picks it up. The package declares `pi.extensions`, so
`pi install` works too.

JS deps (once):

```powershell
cd pi-insar
npm install   # see "npm note" below if you regenerate the lockfile
```

Unix-style kernel start (does not spawn pi):

```bash
INSAR_HOME=<dir> INSAR_PORT=8873 .venv/bin/python -m insar_agent.api.app
```

### Configuration (runtime)

| Knob | Meaning |
| --- | --- |
| `INSAR_API_BASE` | Backend base URL (default `http://127.0.0.1:8873`) |
| `INSAR_LLM_CONFIG` | Absolute path to an `llm.json` override (default `<repo>/workspace/llm.json`) |
| `INSAR_PI_SKIP_HEALTH=1` | `scripts/insar-pi` / `insar-pi.ps1` / `insar-pi-desktop.ps1`: start without the backend check |
| `--insar-session <id>` | Bind this pi session to an existing InSAR session (sidebar starts immediately) |
| `--insar-strict` | Start in strict reproducible mode |
| `/insar-mode [free\|strict\|status]` | Show or switch the freedom mode at runtime |

## LLM provider

`insar-llm` is registered at extension load from `workspace/llm.json` (gitignored; the
only key source). The file's `base_url` / `chat_model` / optional `vision_model` /
`api_key` are read at runtime; the key stays in memory and must never be logged or
written elsewhere.

- Set `INSAR_LLM_CONFIG` to an absolute path to use a different config file.
- `.pi/settings.json` selects `insar-llm` and that file's `chat_model` as the project
  default (`defaultProvider` / `defaultModel`).
- If `llm.json` is missing or unreadable, pi starts as usual — `insar_*` tools still
  work; this provider is simply absent (`pi --list-models` will not show `insar-llm`).

## Theme

`insar-dark` is an interferogram-fringe palette (phase cyan accent, fringe orange/magenta,
navy panels).

- The launcher **provides** the file with `--theme pi-insar/themes/insar-dark.json`.
- `.pi/settings.json` **selects** it (`"theme": "insar-dark"`). Providing ≠ selecting.
- Switch back to pi's built-in dark: `/settings` and set theme to `dark`.

## Tools — 31 `insar_*`

Every name is prefixed `insar_`: registering `read`/`bash`/`write` would override pi's
built-ins for the whole session. The extension is purely additive — it never disables a
built-in tool globally, and strict mode blocks per call through the `tool_call` hook only.

JSON-valued arguments (`params_json`, `changes_json`) are passed as JSON **object
strings** because `Type.Union`/free-form objects are not portable across model providers;
they are parsed and validated before any HTTP call.

`insar_memory` is an optional extra (would be tool 32) and is **not** registered.

pi-journal is **not** a tool — see the next section after science.

### 1. Session, planning, execution

| Tool | Backend | One line |
| --- | --- | --- |
| `insar_create_session` | `POST /api/sessions` | Create or re-open a backend session (runs, workspace, freedom mode). Also binds the sidebar/gate. |
| `insar_list_sessions` | `GET /api/sessions` | List non-archived sessions. |
| `insar_plan_run` | `POST /api/turn` | Natural-language plan. `pipeline=core` (default, 11 steps) or `pipeline=analysis` (steps 20–28); `params_json` carries step-20 source paths. Nothing executes yet. |
| `insar_execute_run` | `POST /api/pipeline` | Run a planned pipeline to completion (NDJSON stream). |
| `insar_resume` | `POST /api/resume` | Reattach runs left `running` after a backend restart; settled steps are not rerun. |
| `insar_run_status` | `GET /api/monitor` | Per-step stage/state, progress, evidence, mode, taints — same payload as the sidebar. |
| `insar_run_trace` | `GET /api/trace` | Execution timeline: stages, durations, events (for triage and reports). |
| `insar_preview_change` | `GET /api/impact` | Dry-run a method/param change: what goes stale and the rerun cost. |
| `insar_apply_change` | `POST /api/actions` | Queue `SET_METHOD` / `SET_PARAMS` (`steer` / `follow_up` / `next_run`). |
| `insar_intervene` | `POST /api/actions`, `POST /api/abort` | `PAUSE` / `PLAY` / `RESET` / `SKIP`; `KILL` goes through `/api/abort`. |
| `insar_fork_run` | `POST /api/fork` | Branch a run with per-step changes; reuse unaffected steps. |

### 2. Query and analysis

| Tool | Backend | One line |
| --- | --- | --- |
| `insar_timeseries_point` | `GET /api/timeseries-point` | Pixel time series (mm) at a lat/lon — quantitative analysis entry. |
| `insar_list_artifacts` | `GET /api/artifacts` | Full product inventory (path, size, hash) for delivery checks. |
| `insar_capabilities` | `GET /api/registry` | Registry closed set: methods and params the model may choose (core + analysis). |
| `insar_doctor` | `GET /api/doctor` | Second-scale read-only environment deep-check. |
| `insar_recommend_route` | `GET /api/recommend` | Dataset → processing-route comparison (HyP3 vs local chain). |
| `insar_read_skill` | `GET /api/skills/{step_id}` | Fetch one step's skill text on demand (do not stuff all 11 into context). |

### 3. Figures and QA

| Tool | Backend | One line |
| --- | --- | --- |
| `insar_view_figure` | `GET /api/figures` | List and inline-view real figure artifacts in the TUI (`{type:"image", data, mimeType}`). |
| `insar_vision_qa` | figure bytes + vision model | Model-looks-at-the-figure quality check (unwrap jumps, tropo stripes). |
| `insar_figure_caption` | `POST /api/report/caption` | Bilingual paper captions from the fact closed-set (not free prose). |

### 4. Export and delivery

| Tool | Backend | One line |
| --- | --- | --- |
| `insar_export_product` | `GET /api/export` | Export velocity / std / timeseries as h5 / csv / GeoTIFF / KMZ / shp. Simulated runs are refused (409) — placeholder bytes are not a data product. |
| `insar_report` | `POST /api/report/{draft,results,full}` | Methods / results / full report: fact closed-set + numeric round-trip; LLM polish rolls back to the skeleton on any check fail. Never invent methods or results. |
| `insar_repro_bundle` | `GET /api/repro-bundle` | Zip: ledger + `run.sh` + methods + figures + per-file sha256 MANIFEST. |
| `insar_export_provenance` | `GET /api/provenance`, `GET /api/run.sh` | `kind=ledger` provenance document or `kind=run_sh` equivalent bare commands. |

### 5. Environment and advisor

| Tool | Backend | One line |
| --- | --- | --- |
| `insar_health` | `GET /api/health` | Backend reachability and version — call first if other tools fail. |
| `insar_list_datasets` | `GET /api/datasets` | SAR datasets under configured scan roots. |
| `insar_env_probe` | `GET /api/env` | Engines, credentials, disk/CPU, audit thresholds. |
| `insar_read_log` | `GET /api/logs` | Tail of a step log (prefer this over hunting files with bash). |
| `insar_set_mode` / `insar_get_mode` | `GET`/`POST /api/mode` | Freedom mode: free (default) vs strict (`read` + `insar_*` only). |
| `insar_advise_next` | `GET /api/advise` | Deterministic next-action card from backend rules (not free-form advice). |

## Science capabilities

### Core pipeline (steps 1–11)

A core run still has **exactly 11 steps**. Ids 12–19 are reserved.

| Step | Name |
| --- | --- |
| 1 | 数据获取 |
| 2 | 辅助数据 |
| 3 | 配准 |
| 4 | 干涉 |
| 5 | 滤波 |
| 6 | 解缠 |
| 7 | 时序反演 |
| 8 | 误差校正 |
| 9 | 形变模型 |
| 10 | 出图导出 |
| 11 | 质检 |

### Analysis steps 20–28

`group=analysis`: a separate **analysis run** (`pipeline=analysis`), not extra steps
bolted onto every core run. Canonical products: 20 `source.h5` → 21 `masked.h5` →
22 `corrected.h5` → 23 `decomposed.h5` → 24 `measure` → 25 figures → 26/27 detect/predict
→ 28 inversion bridge. Source-run product paths are science parameters (fingerprinted).

| Step | Name | Role |
| --- | --- | --- |
| 20 | 分析输入 | Register/validate source products (no rewrite). |
| 21 | 掩膜子集 | Coherence mask / geographic subset / passthrough (hardlink then `copy2`; never simulate). |
| 22 | 速度场校正 | ITRF plate motion / passthrough before decomposition. |
| 23 | 几何分解 | Asc/desc → vertical + east (or raster diff / passthrough). Missing desc track fails honestly. |
| 24 | 统计剖面 | Spatial/temporal averages, transects, residual RMS. |
| 25 | 分析出图 | MintPy view / transect figure / KMZ; missing kinds skip with sidecar, no placeholder plots. |
| 26 | 变化检测 | Epoch diff / quadratic acceleration / velocity compare. |
| 27 | 外推预测 | Extrapolate the **already fitted** step-9 time function + uncertainty + horizon. No black-box ML. |
| 28 | 反演桥导出 | GBIS / Kite / GMT / QGIS / HDF-EOS5 bridges only — **no in-agent inversion**. |

### Scenario packs (8)

Priority order (smaller number tried first): `stripmap_coseismic` (5) → `quake` (10) →
`volcano` (15) → `permafrost` (20) → `subsidence` (25) → `landslide` (30) →
`lt1_gamma` (35) → `teaching` (90).

| Pack | Use |
| --- | --- |
| `stripmap_coseismic` | Stripmap coseismic (local ISCE2-style chain) |
| `quake` | Coseismic step model |
| `volcano` | Volcano deformation (`ramp: "no"` is a red line; quote it in YAML) |
| `permafrost` | Permafrost / seasonal periodic |
| `subsidence` | Urban subsidence (`poly_periodic`, `periods=[1]`) |
| `landslide` | Landslide PS monitoring |
| `lt1_gamma` | LT-1 (Chinese L-band) via GAMMA layout — step 7 `processor=gamma` |
| `teaching` | Classroom demo: full figure set, honest downgrade at step 11 |

MintPy's data plane is parameterized (`processor` + file globs) so ISCE / ARIA / GAMMA /
GMTSAR / SNAP / ROI_PAC / NISAR products can enter step 7. Default remains HyP3;
`stripmap_coseismic` overrides ISCE paths.

### Simulation discipline

Missing engines → the existing honest simulated path (`engines/simulate.py`). Logs,
products and the ledger are explicitly marked **simulated**; evidence is capped at
`runnable`. Never impersonate a real result. GIS export of simulated runs is refused
(409). Reports must use `insar_report`, never self-written methods/results.

## pi-journal

Out-of-ledger observation log: every pi tool call (free and strict) is appended as
NDJSON to `INSAR_HOME/pi_journal.ndjson` via `POST /api/pi-journal`.

It answers “what did the agent do in free mode?”. It is **not provenance**: physically
separate, not in the evidence ladder, never a scientific citation. A journal write
failure must not block the session.

## Tests

From `pi-insar/` (no LLM key required):

```powershell
cd pi-insar
npx tsc --noEmit
npx vitest run
```

`test/backend.globalSetup.ts` spawns a **real** backend on `INSAR_TEST_PORT` (default
**8899**, so it never collides with a dev server on 8873) with a temporary `INSAR_HOME`,
waits for `/api/health`, and tears it down afterwards. Default Python/CWD are
platform-aware (repo-root `.venv\Scripts\python.exe` / repo root). Integration tests
drive tools' `execute()` directly (plain objects, no pi runtime).
`backend.globalSetup.ts` pins `INSAR_ENGINE_PREFIX` to a dummy path so the suite stays
honest simulated even if a host MintPy env exists.

Python kernel (repo root):

```powershell
cd ..
$env:PYTHONPATH = "$pwd\src"
.venv\Scripts\python.exe -m pytest -q
```

`render.test.ts` and `guard.test.ts` are pure golden/unit tests. `skills.test.ts`
validates skill frontmatter via `scripts/sync-skills.mjs --json`, checks that
`APPEND_SYSTEM.md` and the operator skill name all **31** registered tools, and asserts
the launcher only ever passes additive pi flags. `APPEND_SYSTEM.md` must stay under
8000 bytes.

### npm note

`package-lock.json` is committed, so `npm install` resolves from it. Regenerating it from
scratch (no lockfile) crashes npm 10.9.8 with `Cannot read properties of null (reading
'edgesOut')` while it resolves vitest 4's optional peer dependencies; use
`npm install --legacy-peer-deps` in that case, which produced this lockfile.

## Windows environment variables

| Variable | Meaning | Default |
| --- | --- | --- |
| `INSAR_API_BASE` | Backend base URL for pi / MCP / tools | `http://127.0.0.1:8873` |
| `INSAR_TEST_PYTHON` | Interpreter vitest's globalSetup spawns | repo `.venv\Scripts\python.exe` if present |
| `INSAR_TEST_CWD` | CWD for that test backend | repo root |
| `INSAR_TEST_PORT` | Test-only backend port | `8899` |
| `INSAR_LLM_CONFIG` | Absolute path overriding `workspace/llm.json` | unset → `workspace/llm.json` if it exists |
| `INSAR_PI_SKIP_HEALTH` | Skip launcher health check (`=1`) | unset (check on) |
| `INSAR_ENGINE_PREFIX` | Real MintPy/ISCE conda prefix for real-mode backend | real launcher: `E:\miniforge3\envs\insar`; vitest: dummy path |
| `INSAR_ALLOW_SIMULATED` | Allow honest simulated execution when engines are missing | `1` in tests / demo; real launcher sets `0` |

Do not put API keys in this file. The only key source is `workspace/llm.json` (or
`INSAR_LLM_CONFIG`).

Vitest overrides, only if the venv is not at repo-root `.venv`:

```powershell
$env:INSAR_TEST_PYTHON = "<repo>\.venv\Scripts\python.exe"
$env:INSAR_TEST_CWD = "<repo>"
```

## Prompt and skills

| Resource | Role |
| --- | --- |
| `APPEND_SYSTEM.md` | Appended to pi's system prompt (never replaces it): the 11-step pipeline, the red lines, and the free/strict modes. Kept small — it is in every request. |
| `skills/00-insar-agent/SKILL.md` | Hand-written operator skill: how to drive the tool layer end to end, the five stages, stale/dirty cascade, the six-level evidence ladder, and the "never fabricate a number" rule. |
| `../skills/01-*` … `../skills/11-*` + analysis steps 20–28 | The repo's per-step domain skills, 20 in total (methods, parameter heuristics, failure playbooks). Unchanged, loaded in place. |
| `../src/insar_agent/registry/scenario_packs/*/SKILL.md` | Scenario packs (8): `stripmap_coseismic`, `quake`, `volcano`, `permafrost`, `subsidence`, `landslide`, `lt1_gamma`, `teaching`. |

**Skills are not copied.** pi's `--skill <dir>` recurses and loads every directory holding a
`SKILL.md`, so the launcher points at the three roots above and the repo `skills/` stays the
single source of truth — nothing can go stale. `scripts/sync-skills.mjs` therefore *validates*
that wiring by default (frontmatter, name/description limits, duplicate names, missing roots)
and exits non-zero on a problem:

```powershell
npm run skills                 # validate every skill root
node scripts/sync-skills.mjs --json
node scripts/sync-skills.mjs --copy   # packaging only: copy into pi-insar/skills/
```

`--copy` exists for shipping pi-insar as a standalone pi package (pi auto-discovers a
`skills/` directory inside a package). Copies are idempotent, marked with `.synced-from`,
pruned when their source disappears, and gitignored — only `skills/00-insar-agent/` is
tracked.

## Module layout

| File | Responsibility |
| --- | --- |
| `src/backendClient.ts` | Typed `fetch` client; JSON, NDJSON streams and text endpoints; `AbortSignal` support. Journal methods stay last. |
| `src/provider.ts` | `insar-llm` provider from `workspace/llm.json` (key in memory only) |
| `src/journal.ts` | Out-of-ledger pi-journal hook (`POST /api/pi-journal`) |
| `src/tools.ts` | The 31 `insar_*` tool definitions and `registerInsarTools` |
| `src/mode.ts` | `ModeController` (in-memory mirror of the backend mode) + `/insar-mode` + CLI flags |
| `src/guard.ts` | Pure `decideBlock(tool, mode)` + the exception-safe `tool_call` hook |
| `src/sidebar.ts` | Pure `renderPipelineRail(monitor)` + the `ctx.hasUI`-gated widget poller |
| `src/index.ts` | Extension factory: `installProvider` + `installJournal` + tools + guard |
| `themes/insar-dark.json` | Interferogram-fringe TUI theme |
| `APPEND_SYSTEM.md` | InSAR mission context + red lines + modes, appended to the system prompt |
| `skills/00-insar-agent/` | Operator skill for the tool layer (hand-written, tracked) |
| `scripts/sync-skills.mjs` | Validates the skill roots; `--copy` materialises them for packaging |
| `../scripts/insar-pi` | Bash launcher: health check + `pi` with additive flags only |
| `../scripts/insar-pi.ps1` | Windows launcher (theme + skill roots + health check) |
| `../scripts/insar-backend-real.ps1` | Real-mode backend (`INSAR_HOME=workspace\realtest`, `INSAR_ALLOW_SIMULATED=0`) |

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
