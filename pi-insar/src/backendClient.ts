/**
 * Typed HTTP client for the InSAR agent FastAPI backend
 * (`src/insar_agent/api/app.py` + `api/bridge_router.py`).
 *
 * The backend speaks three response flavours and this client normalises all of
 * them: plain JSON, NDJSON turn/pipeline streams (the POST only means
 * "accepted" — the outcome arrives as a stream of events), and plain text
 * (`/api/run.sh`). Streams are consumed to completion so a tool call returns
 * only once the backend has finished the work.
 */

import { existsSync, readFileSync } from "node:fs";
import { mkdir, writeFile } from "node:fs/promises";
import { basename, join } from "node:path";

export const DEFAULT_BASE_URL = "http://127.0.0.1:8873";

/** Delivery semantics for queued interventions (`store.DELIVER_AS`). */
export const DELIVER_AS = ["steer", "follow_up", "next_run"] as const;
export type DeliverAs = (typeof DELIVER_AS)[number];

/** Closed action set of `POST /api/actions` (`core.actions.ACTIONS`). */
export const ACTIONS = [
  "RESET",
  "PAUSE",
  "PLAY",
  "SKIP",
  "KILL",
  "SET_METHOD",
  "SET_PARAMS",
] as const;
export type ActionName = (typeof ACTIONS)[number];

export const FREEDOM_MODES = ["free", "strict"] as const;
export type FreedomMode = (typeof FREEDOM_MODES)[number];

export const EXPORT_PRODUCTS = ["velocity", "velocity_std", "timeseries"] as const;
export type ExportProduct = (typeof EXPORT_PRODUCTS)[number];

export const EXPORT_FORMATS = ["h5", "csv", "gtiff", "kmz", "shp"] as const;
export type ExportFormat = (typeof EXPORT_FORMATS)[number];

export const REPORT_SECTIONS = ["methods", "results", "full"] as const;
export type ReportSection = (typeof REPORT_SECTIONS)[number];

export interface HealthResponse {
  ok: boolean;
  version: string;
}

export interface SessionRow {
  session_id: string;
  name: string;
  created_at: number;
  mode: string;
  scenario: string | null;
  archived: number | null;
  project_id: string | null;
  project_name: string | null;
  project_root: string | null;
}

export interface RunSummary {
  run_id: string;
  parent_run_id: string | null;
  created_at: number;
  status: string;
  scenario: string | null;
  simulated: boolean;
  steps: { total: number; done: number; skipped: number; failed: number };
}

export interface RunsResponse {
  session: string;
  runs: RunSummary[];
}

export interface StateResponse {
  run: Record<string, unknown> | null;
  steps: Array<Record<string, unknown>>;
}

export interface MonitorRun {
  run_id: string;
  scenario: string | null;
  status: string;
  simulated: boolean;
  parent_run_id: string | null;
}

export interface MonitorStep {
  step: number;
  name: string;
  capability: string;
  method: string | null;
  state: string;
  stage: string;
  stage_letter: string;
  stale: boolean;
  stale_reason: string | null;
  failure_class: string | null;
  exit_code: number | null;
  run_ok: number | null;
  duration: number | null;
  attempts: number;
}

export interface MonitorCurrent {
  step: number;
  name: string;
  method: string | null;
  stage: string;
}

export interface MonitorEvidence {
  level: string;
  level_index: number;
  ladder: string[];
  reasons: string[];
  ceiling: string | null;
  ceiling_reason: string | null;
  step_sources: Record<string, unknown>;
  parent_validations: unknown[];
}

/** `GET /api/monitor` — the sidebar data contract. */
export interface MonitorResponse {
  session: string;
  run: MonitorRun | null;
  steps: MonitorStep[];
  current: MonitorCurrent | null;
  progress: { total: number; done: number; pct: number };
  evidence: MonitorEvidence | null;
  mode: string;
  taints: number;
}

export interface ImpactResponse {
  changedStep: number;
  reason: string;
  affected: unknown[];
  rerunMinutes: number | null;
  rerunBasis: string | null;
}

export interface ForkResponse {
  runId: string;
  steps: Array<{ id: number; state: string; method: string | null }>;
}

export interface AcceptedResponse {
  accepted: boolean;
  id?: number;
  deliver_as?: string | null;
}

export interface ModeResponse {
  session: string;
  mode: string;
}

/** One entry of `GET /api/figures` — a real on-disk figure artifact. */
export interface FigureEntry {
  step: number;
  artId: string;
  name: string;
  path: string;
  kind: string;
  size: number;
  mtime: number;
  url: string;
  fullUrl: string;
  thumbUrl: string;
  meta?: Record<string, unknown>;
}

export interface FiguresResponse {
  run: string | null;
  figures: FigureEntry[];
}

/**
 * One row of unpaged `GET /api/trace` (SQLite `trace` table; schema.sql).
 * `action` is a JSON string `{type,tool,input,output}`; `error_occurred` is 0/1.
 * Duration is not a column: wall-clock time comes from `GET /api/monitor`
 * (commands ledger, last settled row). Event `ts` is not used to compute duration.
 */
export interface TraceEvent {
  id: number;
  run_id: string | null;
  session_id: string | null;
  step_no: number | null;
  ts: number;
  phase: string | null;
  thought: string | null;
  action: string | null;
  observation: string | null;
  error_occurred: number;
  error_type: string | null;
  error_message: string | null;
  revision_trigger: string | null;
  confidence: number | null;
  raw_response: string | null;
}

/** `GET /api/timeseries-point` — one pixel from a real MintPy timeseries HDF5.
 *
 * lat/lon are always true WGS84 degrees; for projected (UTM) grids the native
 * metre coordinates travel alongside as x/y and extent_native, with `crs`
 * describing the grid. Never feed UTM metres into the lat/lon parameters. */
export interface TimeseriesPointResponse {
  dates: string[];
  values_mm: number[];
  ref_point: { lat: number; lon: number; x?: number; y?: number } | null;
  source: string;
  point: { lat: number | null; lon: number | null; row: number; col: number; x?: number; y?: number };
  shape: { rows: number; cols: number };
  extent: { lon_min: number; lon_max: number; lat_min: number; lat_max: number } | null;
  extent_native?: { x_min: number; x_max: number; y_min: number; y_max: number } | null;
  crs?: { epsg: number | null; projected: boolean; unit: string; latlon_convertible: boolean } | null;
  n_dropped: number;
}

export interface ArtifactFileRow {
  artId: string;
  path: string;
  kind: string;
  policy: string;
  fp: unknown;
  size: number | null;
  mtime: number | null;
  exists: boolean;
}

export interface ArtifactStepGroup {
  stepId: number;
  name: string;
  method: string;
  artifacts: ArtifactFileRow[];
}

/** `GET /api/artifacts` — ledger rows grouped by step (missing files stay listed). */
export interface ArtifactsResponse {
  run: string | null;
  steps: ArtifactStepGroup[];
}

export interface CapabilityMethodInfo {
  id: string;
  label: string;
  engine: string;
  why: string;
  recommend: boolean;
  extra: string;
  ok: boolean;
  simulated: boolean;
  blocked: string;
}

export interface CapabilityParamInfo {
  default: unknown;
  kind: string;
  type: string;
  min: number | null;
  max: number | null;
  hint: string;
}

/** One pipeline step from `GET /api/registry` (the endpoint returns a bare array). */
export interface CapabilityInfo {
  id: number;
  name: string;
  deps: number[];
  method: string;
  methods: CapabilityMethodInfo[];
  params: Record<string, CapabilityParamInfo>;
  outputs: Array<{ path: string; kind: string; layout: string }>;
  replay: string;
  timeouts: { idle: number; total: number };
}

export interface DoctorCheck {
  name: string;
  category: string;
  status: string;
  detail: string;
  fix_hint: string;
}

/** `GET /api/doctor` — `summarize(check_all)` plus `took_ms`. */
export interface DoctorResponse {
  status: string;
  exit_code: number;
  counts: { ok: number; warn: number; fail: number };
  results: DoctorCheck[];
  took_ms: number;
}

export interface RecommendRoute {
  route_id: string;
  name: string;
  suitable_scenarios: string[];
  pros: string[];
  cons: string[];
  requirements: Array<Record<string, unknown>>;
  steps_involved: number[];
  est_note: string;
  ready: boolean;
  missing: string[];
}

export interface RecommendResponse {
  dataset: Record<string, unknown>;
  routes: RecommendRoute[];
}

/** `GET /api/skills/{step_id}` — structured step knowledge, not a single markdown blob. */
export interface SkillResponse {
  capability: number;
  name: string;
  version: string;
  content_hash: string;
  description: string;
  applies_to: string[];
  sections: Record<string, string>;
}

export interface ExportFormatCell {
  available: boolean;
  reason: string | null;
}

export interface ExportProductRow {
  product: string;
  source: string | null;
  formats: Record<string, ExportFormatCell>;
}

export interface ExportOptionsResponse {
  run: string;
  simulated: boolean;
  engine: { available: boolean; reason: string | null };
  products: ExportProductRow[];
}

export interface SavedFile {
  savedTo: string;
  bytes: number;
  reused: boolean;
  mediaType: string;
}

export interface ReportResponse {
  run_id: string;
  draft?: string;
  markdown?: string;
  llm_polish?: boolean;
  facts_used?: unknown;
  saved?: boolean;
  path?: string | null;
  ok?: boolean;
}

export interface CaptionResponse {
  run_id: string;
  figure: string;
  zh: string;
  en: string;
  llm_polish: boolean;
  saved?: boolean;
}

export interface AdviseAction {
  kind: string;
  text?: string;
  method?: string;
  endpoint?: string;
  body?: unknown;
  params?: unknown;
  tab?: string;
  step?: number;
  download?: boolean;
}

export interface AdviseSuggestion {
  id: string;
  title: string;
  why: string;
  action: AdviseAction;
}

export interface AdviseResponse {
  run_id: string;
  status: string;
  context?: string;
  suggestions: AdviseSuggestion[];
  note?: string;
  evidence_level?: string | null;
  polish_source?: string;
}

export function resolveExportDir(env: NodeJS.ProcessEnv = process.env): string {
  const raw = env.INSAR_EXPORT_DIR?.trim();
  return raw && raw.length > 0 ? raw : join(process.cwd(), "exports");
}

export interface DatasetsResponse {
  roots: unknown[];
  datasets: unknown[];
  scanned_at: number;
  cached: boolean;
}

export interface EnvResponse {
  probe: Record<string, unknown>;
  thresholds: unknown[];
}

export interface LogsResponse {
  text: string;
  size: number | null;
  truncated: boolean;
}

/** One decoded line of an NDJSON turn/pipeline stream. */
export type StreamEvent = Record<string, unknown>;

export interface StreamResult {
  /** Decoded events, capped at `maxEvents` (oldest kept, newest dropped). */
  events: StreamEvent[];
  /** Total events observed on the wire, including any dropped by the cap. */
  total: number;
  /** True when `events` is shorter than `total`. */
  truncated: boolean;
  /** Lines that were not valid JSON (should stay empty against a healthy backend). */
  malformed: number;
}

export class BackendError extends Error {
  readonly status: number;
  readonly url: string;
  readonly body: string;

  constructor(message: string, status: number, url: string, body: string) {
    super(message);
    this.name = "BackendError";
    this.status = status;
    this.url = url;
    this.body = body;
  }
}

export interface BackendClientOptions {
  baseUrl?: string;
  /** Injected for tests; defaults to the global `fetch` (Node >= 18). */
  fetchImpl?: typeof fetch;
  /** Per-request timeout for non-streaming calls. Streams are not timed out. */
  timeoutMs?: number;
  /** Cap on events retained from one NDJSON stream. */
  maxStreamEvents?: number;
}

type QueryValue = string | number | boolean | undefined | null;

const DESKTOP_CONFIG_REL = join(".pi", "insar-desktop.json");

function stripTrailingSlash(url: string): string {
  return url.replace(/\/+$/, "");
}

/** Read apiBase from the workspace Desktop hint; ignore missing/invalid files. */
function readDesktopApiBase(cwd: string): string | undefined {
  const path = join(cwd, DESKTOP_CONFIG_REL);
  if (!existsSync(path)) return undefined;
  try {
    const parsed = JSON.parse(readFileSync(path, "utf8")) as { apiBase?: unknown };
    if (typeof parsed.apiBase !== "string") return undefined;
    const apiBase = parsed.apiBase.trim();
    return apiBase.length > 0 ? apiBase : undefined;
  } catch {
    return undefined; // 坏 JSON 不能阻断扩展加载,回落到默认 8873
  }
}

/**
 * Trailing-slash-insensitive base URL: INSAR_API_BASE, then workspace
 * `.pi/insar-desktop.json` `apiBase`, then {@link DEFAULT_BASE_URL}.
 */
export function resolveBaseUrl(
  env: NodeJS.ProcessEnv = process.env,
  cwd: string = process.cwd(),
): string {
  const raw = env.INSAR_API_BASE?.trim();
  if (raw) return stripTrailingSlash(raw);
  // 未走 insar-pi-desktop.ps1 时 Desktop 主进程/worker 往往没有 INSAR_API_BASE
  const fromDesktop = readDesktopApiBase(cwd);
  if (fromDesktop) return stripTrailingSlash(fromDesktop);
  return stripTrailingSlash(DEFAULT_BASE_URL);
}

/**
 * Merge an external `AbortSignal` with a timeout. Returns the signal to use and
 * a disposer that must run in a `finally` so the timer never leaks.
 */
function withTimeout(
  signal: AbortSignal | undefined,
  timeoutMs: number | undefined,
): { signal: AbortSignal | undefined; dispose: () => void } {
  if (!timeoutMs || timeoutMs <= 0) return { signal, dispose: () => {} };
  const timeout = AbortSignal.timeout(timeoutMs);
  if (!signal) return { signal: timeout, dispose: () => {} };
  return { signal: AbortSignal.any([signal, timeout]), dispose: () => {} };
}

export class BackendClient {
  readonly baseUrl: string;
  private readonly fetchImpl: typeof fetch;
  private readonly timeoutMs: number;
  private readonly maxStreamEvents: number;

  constructor(options: BackendClientOptions = {}) {
    this.baseUrl = (options.baseUrl ?? resolveBaseUrl()).replace(/\/+$/, "");
    this.fetchImpl = options.fetchImpl ?? globalThis.fetch;
    this.timeoutMs = options.timeoutMs ?? 30_000;
    this.maxStreamEvents = options.maxStreamEvents ?? 2_000;
  }

  url(path: string, query: Record<string, QueryValue> = {}): string {
    const url = new URL(path.startsWith("/") ? path : `/${path}`, `${this.baseUrl}/`);
    for (const [key, value] of Object.entries(query)) {
      if (value === undefined || value === null) continue;
      url.searchParams.set(key, String(value));
    }
    return url.toString();
  }

  private async send(
    method: "GET" | "POST",
    path: string,
    opts: {
      query?: Record<string, QueryValue>;
      body?: unknown;
      signal?: AbortSignal | undefined;
      accept?: string;
      stream?: boolean;
    } = {},
  ): Promise<Response> {
    const url = this.url(path, opts.query ?? {});
    // Streaming endpoints stay open for the whole run: a wall-clock timeout
    // there would abort healthy long pipelines, so only the caller's signal
    // (Esc in pi) can cancel them.
    const { signal, dispose } = withTimeout(
      opts.signal,
      opts.stream ? undefined : this.timeoutMs,
    );
    const headers: Record<string, string> = { accept: opts.accept ?? "application/json" };
    if (opts.body !== undefined) headers["content-type"] = "application/json";

    let response: Response;
    try {
      response = await this.fetchImpl(url, {
        method,
        headers,
        ...(opts.body === undefined ? {} : { body: JSON.stringify(opts.body) }),
        ...(signal ? { signal } : {}),
      });
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      throw new BackendError(
        `${method} ${url} failed: ${detail} (is the InSAR backend running? ` +
          `set INSAR_API_BASE if it is not on ${this.baseUrl})`,
        0,
        url,
        "",
      );
    } finally {
      dispose();
    }

    if (!response.ok) {
      const body = await response.text().catch(() => "");
      throw new BackendError(
        `${method} ${url} -> HTTP ${response.status}: ${describeError(body)}`,
        response.status,
        url,
        body,
      );
    }
    return response;
  }

  private async json<T>(
    method: "GET" | "POST",
    path: string,
    opts: {
      query?: Record<string, QueryValue>;
      body?: unknown;
      signal?: AbortSignal | undefined;
    } = {},
  ): Promise<T> {
    const response = await this.send(method, path, opts);
    return (await response.json()) as T;
  }

  /** Consume an NDJSON stream to completion, collecting decoded events. */
  private async ndjson(
    path: string,
    body: unknown,
    signal: AbortSignal | undefined,
  ): Promise<StreamResult> {
    const response = await this.send("POST", path, {
      body,
      ...(signal ? { signal } : {}),
      accept: "application/x-ndjson",
      stream: true,
    });

    const events: StreamEvent[] = [];
    let total = 0;
    let malformed = 0;

    const push = (line: string): void => {
      const trimmed = line.trim();
      if (trimmed.length === 0) return;
      total += 1;
      let parsed: unknown;
      try {
        parsed = JSON.parse(trimmed);
      } catch {
        malformed += 1;
        return;
      }
      if (events.length < this.maxStreamEvents) {
        events.push(parsed as StreamEvent);
      }
    };

    if (!response.body) {
      for (const line of (await response.text()).split("\n")) push(line);
      return { events, total, truncated: total > events.length, malformed };
    }

    const decoder = new TextDecoder();
    let buffer = "";
    for await (const chunk of streamChunks(response.body)) {
      buffer += decoder.decode(chunk, { stream: true });
      let newline = buffer.indexOf("\n");
      while (newline >= 0) {
        push(buffer.slice(0, newline));
        buffer = buffer.slice(newline + 1);
        newline = buffer.indexOf("\n");
      }
    }
    buffer += decoder.decode();
    push(buffer);

    return { events, total, truncated: total > events.length, malformed };
  }

  // ---------------- endpoints ----------------

  health(signal?: AbortSignal): Promise<HealthResponse> {
    return this.json<HealthResponse>("GET", "/api/health", { signal });
  }

  createSession(id: string, name?: string, signal?: AbortSignal): Promise<SessionRow> {
    return this.json<SessionRow>("POST", "/api/sessions", {
      body: { id, ...(name === undefined ? {} : { name }) },
      signal,
    });
  }

  listSessions(includeArchived = false, signal?: AbortSignal): Promise<SessionRow[]> {
    return this.json<SessionRow[]>("GET", "/api/sessions", {
      query: { include_archived: includeArchived ? 1 : undefined },
      signal,
    });
  }

  /** `POST /api/turn` — rules-path planning; no LLM key required. */
  turn(
    session: string,
    text: string,
    signal?: AbortSignal,
    extra?: { pipeline?: "core" | "analysis"; params?: Record<string, unknown> },
  ): Promise<StreamResult> {
    return this.ndjson(
      "/api/turn",
      {
        session,
        text,
        ...(extra?.pipeline ? { pipeline: extra.pipeline } : {}),
        ...(extra?.params ? { params: extra.params } : {}),
      },
      signal,
    );
  }

  /** `POST /api/pipeline` — executes the run (or `stepIds`) to completion. */
  pipeline(
    session: string,
    runId?: string,
    stepIds?: number[],
    signal?: AbortSignal,
  ): Promise<StreamResult> {
    return this.ndjson(
      "/api/pipeline",
      {
        session,
        ...(runId === undefined ? {} : { run_id: runId }),
        ...(stepIds === undefined ? {} : { step_ids: stepIds }),
      },
      signal,
    );
  }

  runs(session: string, signal?: AbortSignal): Promise<RunsResponse> {
    return this.json<RunsResponse>("GET", "/api/runs", { query: { session }, signal });
  }

  /** Most recent run of the session, or null when the session has none. */
  async latestRun(session: string, signal?: AbortSignal): Promise<RunSummary | null> {
    const { runs } = await this.runs(session, signal);
    return runs[0] ?? null;
  }

  runState(session: string, runId?: string, signal?: AbortSignal): Promise<StateResponse> {
    return this.json<StateResponse>("GET", "/api/state", {
      query: { session, run_id: runId },
      signal,
    });
  }

  monitor(session: string, runId?: string, signal?: AbortSignal): Promise<MonitorResponse> {
    return this.json<MonitorResponse>("GET", "/api/monitor", {
      query: { session, run_id: runId },
      signal,
    });
  }

  impact(
    session: string,
    step: number,
    opts: {
      runId?: string | undefined;
      method?: string | undefined;
      params?: Record<string, unknown> | undefined;
      signal?: AbortSignal | undefined;
    } = {},
  ): Promise<ImpactResponse> {
    return this.json<ImpactResponse>("GET", "/api/impact", {
      query: {
        session,
        step,
        run_id: opts.runId,
        method: opts.method,
        // The backend parses `params` as a JSON object string (app.py).
        params: opts.params === undefined ? undefined : JSON.stringify(opts.params),
      },
      signal: opts.signal,
    });
  }

  action(
    session: string,
    action: ActionName,
    opts: {
      runId?: string | undefined;
      scope?: "step" | "run";
      target?: string | number | undefined;
      payload?: Record<string, unknown> | undefined;
      deliverAs?: DeliverAs;
      signal?: AbortSignal | undefined;
    } = {},
  ): Promise<AcceptedResponse> {
    return this.json<AcceptedResponse>("POST", "/api/actions", {
      body: {
        session,
        ...(opts.runId === undefined ? {} : { run_id: opts.runId }),
        scope: opts.scope ?? "step",
        // `target` is required by ActionBody; run-scoped actions ignore its value.
        target: String(opts.target ?? "run"),
        action,
        payload: opts.payload ?? {},
        deliver_as: opts.deliverAs ?? "steer",
      },
      signal: opts.signal,
    });
  }

  abort(session: string, runId?: string, signal?: AbortSignal): Promise<AcceptedResponse> {
    return this.json<AcceptedResponse>("POST", "/api/abort", {
      body: { session, ...(runId === undefined ? {} : { run_id: runId }) },
      signal,
    });
  }

  fork(
    session: string,
    runId: string,
    changes: Record<string, { method?: string; params?: Record<string, unknown> }>,
    signal?: AbortSignal,
  ): Promise<ForkResponse> {
    return this.json<ForkResponse>("POST", "/api/fork", {
      body: { session, run_id: runId, changes },
      signal,
    });
  }

  provenance(
    session: string,
    runId?: string,
    signal?: AbortSignal,
  ): Promise<Record<string, unknown>> {
    return this.json<Record<string, unknown>>("GET", "/api/provenance", {
      query: { session, run_id: runId },
      signal,
    });
  }

  /** `GET /api/run.sh` — the equivalent bare-command script, as text. */
  async runScript(session: string, runId?: string, signal?: AbortSignal): Promise<string> {
    const response = await this.send("GET", "/api/run.sh", {
      query: { session, run_id: runId },
      accept: "text/plain",
      ...(signal ? { signal } : {}),
    });
    return await response.text();
  }

  datasets(signal?: AbortSignal): Promise<DatasetsResponse> {
    return this.json<DatasetsResponse>("GET", "/api/datasets", { signal });
  }

  /** `GET /api/env` — `session` is required by the backend (it probes per session). */
  env(session: string, signal?: AbortSignal): Promise<EnvResponse> {
    return this.json<EnvResponse>("GET", "/api/env", { query: { session }, signal });
  }

  async logs(
    session: string,
    step: number,
    opts: { runId?: string | undefined; tailKb?: number | undefined; signal?: AbortSignal | undefined } = {},
  ): Promise<LogsResponse> {
    const response = await this.send("GET", "/api/logs", {
      query: { session, step, run_id: opts.runId, tail_kb: opts.tailKb },
      accept: "text/plain",
      ...(opts.signal ? { signal: opts.signal } : {}),
    });
    const size = Number(response.headers.get("x-log-size"));
    return {
      text: await response.text(),
      size: Number.isFinite(size) ? size : null,
      truncated: response.headers.get("x-log-truncated") === "1",
    };
  }

  getMode(session: string, signal?: AbortSignal): Promise<ModeResponse> {
    return this.json<ModeResponse>("GET", "/api/mode", { query: { session }, signal });
  }

  setMode(session: string, mode: FreedomMode, signal?: AbortSignal): Promise<ModeResponse> {
    return this.json<ModeResponse>("POST", "/api/mode", { body: { session, mode }, signal });
  }

  /** `POST /api/resume` — reattach runs left `running` after a backend restart. */
  resume(session: string, signal?: AbortSignal): Promise<StreamResult> {
    return this.ndjson("/api/resume", { session }, signal);
  }

  figures(session: string, runId?: string, signal?: AbortSignal): Promise<FiguresResponse> {
    return this.json<FiguresResponse>("GET", "/api/figures", {
      query: { session, run_id: runId },
      signal,
    });
  }

  /**
   * Fetch one artifact image by the relative `fullUrl` a `figures()` entry
   * carries (e.g. `/api/artifact-file?session=...&art_id=...`). Bytes + media
   * type straight from the backend's closed image-extension set.
   */
  async artifactImage(
    relativeUrl: string,
    signal?: AbortSignal,
  ): Promise<{ bytes: Uint8Array; mediaType: string }> {
    const response = await this.send("GET", relativeUrl, {
      accept: "image/*",
      ...(signal ? { signal } : {}),
    });
    const mediaType = (response.headers.get("content-type") ?? "image/png").split(";")[0]?.trim() || "image/png";
    return { bytes: new Uint8Array(await response.arrayBuffer()), mediaType };
  }

  /**
   * Unpaged `GET /api/trace` — execution-audit rows (phase / observation / errors).
   * No run → `[]` (HTTP 200). A run owned by another session → HTTP 404.
   */
  trace(session: string, runId?: string, signal?: AbortSignal): Promise<TraceEvent[]> {
    return this.json<TraceEvent[]>("GET", "/api/trace", {
      query: { session, run_id: runId },
      signal,
    });
  }

  /**
   * `GET /api/registry` returns a bare array. Wrap it so callers always get
   * `{ steps }` without inventing fields the backend does not send.
   */
  async capabilities(signal?: AbortSignal): Promise<{ steps: CapabilityInfo[] }> {
    const steps = await this.json<CapabilityInfo[]>("GET", "/api/registry", { signal });
    return { steps };
  }

  timeseriesPoint(
    session: string,
    opts: {
      runId?: string | undefined;
      lat?: number | undefined;
      lon?: number | undefined;
      row?: number | undefined;
      col?: number | undefined;
    },
    signal?: AbortSignal,
  ): Promise<TimeseriesPointResponse> {
    return this.json<TimeseriesPointResponse>("GET", "/api/timeseries-point", {
      query: {
        session,
        run_id: opts.runId,
        lat: opts.lat,
        lon: opts.lon,
        row: opts.row,
        col: opts.col,
      },
      signal,
    });
  }

  artifacts(session: string, runId?: string, signal?: AbortSignal): Promise<ArtifactsResponse> {
    return this.json<ArtifactsResponse>("GET", "/api/artifacts", {
      query: { session, run_id: runId },
      signal,
    });
  }

  doctor(signal?: AbortSignal): Promise<DoctorResponse> {
    return this.json<DoctorResponse>("GET", "/api/doctor", { signal });
  }

  recommend(datasetId: string, signal?: AbortSignal): Promise<RecommendResponse> {
    return this.json<RecommendResponse>("GET", "/api/recommend", {
      query: { dataset_id: datasetId },
      signal,
    });
  }

  skill(stepId: number, signal?: AbortSignal): Promise<SkillResponse> {
    return this.json<SkillResponse>("GET", `/api/skills/${stepId}`, { signal });
  }

  exportOptions(session: string, runId?: string, signal?: AbortSignal): Promise<ExportOptionsResponse> {
    return this.json<ExportOptionsResponse>("GET", "/api/export/options", {
      query: { session, run_id: runId },
      signal,
    });
  }

  /**
   * Download an exported data product into `destDir`. Simulated runs are
   * refused by the backend with HTTP 409 — that error is rethrown as-is.
   */
  exportProduct(
    session: string,
    product: ExportProduct,
    fmt: ExportFormat,
    destDir: string,
    runId?: string,
    signal?: AbortSignal,
  ): Promise<SavedFile> {
    return this.saveDownload("/api/export", destDir, `${product}.${fmt}`, {
      query: { session, product, fmt, run_id: runId },
      signal,
    });
  }

  visionQa(
    body: { session: string; run_id?: string; figure: string },
    signal?: AbortSignal,
  ): Promise<Record<string, unknown>> {
    return this.json<Record<string, unknown>>("POST", "/api/vision-qa", { body, signal });
  }

  visionQaList(
    session: string,
    runId?: string,
    signal?: AbortSignal,
  ): Promise<{ run: string | null; items: unknown[] }> {
    return this.json<{ run: string | null; items: unknown[] }>("GET", "/api/vision-qa", {
      query: { session, run: runId },
      signal,
    });
  }

  report(
    section: ReportSection,
    session: string,
    runId?: string,
    signal?: AbortSignal,
  ): Promise<ReportResponse> {
    const path =
      section === "methods"
        ? "/api/report/draft"
        : section === "results"
          ? "/api/report/results"
          : "/api/report/full";
    return this.json<ReportResponse>("POST", path, {
      body: { session, ...(runId === undefined ? {} : { run_id: runId }) },
      signal,
    });
  }

  caption(
    session: string,
    figure: string,
    runId?: string,
    signal?: AbortSignal,
  ): Promise<CaptionResponse> {
    return this.json<CaptionResponse>("POST", "/api/report/caption", {
      body: { session, figure, ...(runId === undefined ? {} : { run_id: runId }) },
      signal,
    });
  }

  /** Reproduction zip: ledger + run.sh + methods.md + qa.json + figures + MANIFEST. */
  reproBundle(
    session: string,
    destDir: string,
    runId?: string,
    signal?: AbortSignal,
  ): Promise<SavedFile> {
    const fallback = runId ? `insar-repro-${runId.slice(0, 24)}.zip` : "insar-repro.zip";
    return this.saveDownload("/api/repro-bundle", destDir, fallback, {
      query: { session, run_id: runId },
      accept: "application/zip",
      signal,
    });
  }

  advise(session: string, runId?: string, signal?: AbortSignal): Promise<AdviseResponse> {
    return this.json<AdviseResponse>("GET", "/api/advise", {
      query: { session, run_id: runId },
      signal,
    });
  }

  /** `POST /api/pi-journal` — out-of-ledger observation log (never provenance). */
  journal(
    entry: {
      session: string;
      tool: string;
      mode?: string;
      is_error?: boolean;
      input_digest?: string;
      ts?: number;
    },
    signal?: AbortSignal,
  ): Promise<AcceptedResponse> {
    return this.json<AcceptedResponse>("POST", "/api/pi-journal", { body: entry, signal });
  }

  private async saveDownload(
    path: string,
    destDir: string,
    fallbackName: string,
    opts: {
      query?: Record<string, QueryValue> | undefined;
      signal?: AbortSignal | undefined;
      accept?: string | undefined;
    } = {},
  ): Promise<SavedFile> {
    const response = await this.send("GET", path, {
      query: opts.query ?? {},
      accept: opts.accept ?? "application/octet-stream",
      ...(opts.signal ? { signal: opts.signal } : {}),
    });
    const bytes = new Uint8Array(await response.arrayBuffer());
    const name = safeFileName(
      filenameFromDisposition(response.headers.get("content-disposition"), fallbackName),
    );
    await mkdir(destDir, { recursive: true });
    const savedTo = join(destDir, name);
    await writeFile(savedTo, bytes);
    return {
      savedTo,
      bytes: bytes.byteLength,
      reused: response.headers.get("x-export-reused") === "1",
      mediaType: response.headers.get("content-type") ?? "application/octet-stream",
    };
  }
}

/** FastAPI errors are `{"detail": ...}`; fall back to the raw body. */
function describeError(body: string): string {
  try {
    const parsed = JSON.parse(body) as { detail?: unknown };
    if (parsed && parsed.detail !== undefined) {
      return typeof parsed.detail === "string" ? parsed.detail : JSON.stringify(parsed.detail);
    }
  } catch {
    // not JSON
  }
  return body.slice(0, 500);
}

function filenameFromDisposition(header: string | null, fallback: string): string {
  if (!header) return fallback;
  const star = /filename\*=(?:UTF-8'')?([^;]+)/i.exec(header);
  if (star?.[1]) {
    try {
      return decodeURIComponent(star[1].trim().replace(/^"+|"+$/g, ""));
    } catch {
      // keep looking
    }
  }
  const plain = /filename="([^"]+)"/i.exec(header) ?? /filename=([^;]+)/i.exec(header);
  if (plain?.[1]) return plain[1].trim();
  return fallback;
}

/** Keep only a path-safe basename so a Content-Disposition cannot escape destDir. */
function safeFileName(name: string): string {
  const base = basename(name.replace(/\\/g, "/"));
  return base.length > 0 ? base : "download.bin";
}

/** Iterate a web `ReadableStream` without relying on async-iterator support. */
async function* streamChunks(body: ReadableStream<Uint8Array>): AsyncGenerator<Uint8Array> {
  const reader = body.getReader();
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) return;
      if (value) yield value;
    }
  } finally {
    reader.releaseLock();
  }
}
