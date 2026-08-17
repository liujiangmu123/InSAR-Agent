/** Shared types + coerce for the InSAR workbench right panel (monitor v1 or insar-read v2). */

export interface MonitorRun {
  run_id: string
  scenario: string | null
  status: string
  simulated: boolean
}

export interface MonitorStep {
  step: number
  name: string
  method: string | null
  state: string
  stage: string
  stage_letter: string
  stale: boolean
  stale_reason: string | null
  failure_class: string | null
  duration: number | null
  attempts: number
}

export interface MonitorCurrent {
  step: number
  name: string
  method: string | null
  stage: string
}

export interface MonitorEvidence {
  level: string
  ceiling: string | null
}

export interface MonitorResponse {
  session: string
  run: MonitorRun | null
  steps: MonitorStep[]
  current: MonitorCurrent | null
  progress: { total: number; done: number; pct: number }
  evidence: MonitorEvidence | null
  mode: string
  taints: number
}

export interface ProviderError {
  error: string
  base?: string
  session?: string
}

export type DatasetKind = 'hyp3' | 'alos_raw' | 'slc_stack' | 'dem' | 'unknown'

export interface DatasetRow {
  id: string
  path: string
  name: string
  kind: DatasetKind
  size_bytes: number
  file_count: number
  date_range: { start: string; end: string } | null
  detail: Record<string, unknown>
}

export interface DatasetsPayload {
  roots: string[]
  datasets: DatasetRow[]
  scanned_at?: number
  error?: string
}

export interface FigureRow {
  step: number
  name: string
  thumbUrl: string
  url: string
  fullUrl: string
  meta?: Record<string, unknown>
}

export interface FiguresPayload {
  run: string | null
  figures: FigureRow[]
  error?: string
}

export interface InsarReadState {
  v: 2
  base: string
  monitor: MonitorResponse
  datasets: DatasetsPayload
  figures: FiguresPayload
}

export function asRecord(v: unknown): Record<string, unknown> | null {
  return v !== null && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null
}

export function asString(v: unknown): string | undefined {
  return typeof v === 'string' ? v : undefined
}

export function asFiniteNumber(v: unknown): number | undefined {
  return typeof v === 'number' && Number.isFinite(v) ? v : undefined
}

export function asBoolean(v: unknown): boolean | undefined {
  return typeof v === 'boolean' ? v : undefined
}

export function pad2(step: number): string {
  return String(step).padStart(2, '0')
}

export function shortRunId(runId: string): string {
  const match = /^\d{8}T\d{6}-(.+)$/.exec(runId)
  return match?.[1] ?? runId
}

export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '?'
  if (seconds < 0.05) return '<0.1 s'
  if (seconds < 60) return `${seconds.toFixed(1)} s`
  const minutes = Math.floor(seconds / 60)
  const rest = seconds - minutes * 60
  return `${minutes} min ${rest.toFixed(0)} s`
}

export function formatBytes(n: number): string {
  if (!Number.isFinite(n) || n < 0) return '?'
  if (n < 1024) return `${n} B`
  const kb = n / 1024
  if (kb < 1024) return `${kb < 10 ? kb.toFixed(1) : Math.round(kb)} KB`
  const mb = kb / 1024
  if (mb < 1024) return `${mb < 10 ? mb.toFixed(1) : Math.round(mb)} MB`
  const gb = mb / 1024
  return `${gb < 10 ? gb.toFixed(1) : Math.round(gb)} GB`
}

export function readProviderError(state: unknown): ProviderError | null {
  const rec = asRecord(state)
  if (!rec || rec.ok !== false) return null
  const error = asString(rec.error)
  if (!error) return { error: 'backend_unreachable' }
  return {
    error,
    base: asString(rec.base),
    session: asString(rec.session),
  }
}

function coerceRun(v: unknown): MonitorRun | null {
  const rec = asRecord(v)
  if (!rec) return null
  const runId = asString(rec.run_id)
  const status = asString(rec.status)
  if (!runId || !status) return null
  return {
    run_id: runId,
    status,
    scenario: asString(rec.scenario) ?? null,
    simulated: asBoolean(rec.simulated) ?? false,
  }
}

function coerceStep(v: unknown): MonitorStep | null {
  const rec = asRecord(v)
  if (!rec) return null
  const step = asFiniteNumber(rec.step)
  const name = asString(rec.name)
  if (step === undefined || name === undefined) return null
  return {
    step,
    name,
    method: asString(rec.method) ?? null,
    state: asString(rec.state) ?? 'pending',
    stage: asString(rec.stage) ?? '',
    stage_letter: asString(rec.stage_letter) ?? '',
    stale: asBoolean(rec.stale) ?? false,
    stale_reason: asString(rec.stale_reason) ?? null,
    failure_class: asString(rec.failure_class) ?? null,
    duration: asFiniteNumber(rec.duration) ?? null,
    attempts: asFiniteNumber(rec.attempts) ?? 1,
  }
}

function coerceCurrent(v: unknown): MonitorCurrent | null {
  const rec = asRecord(v)
  if (!rec) return null
  const step = asFiniteNumber(rec.step)
  const name = asString(rec.name)
  if (step === undefined || name === undefined) return null
  return {
    step,
    name,
    method: asString(rec.method) ?? null,
    stage: asString(rec.stage) ?? '',
  }
}

function coerceEvidence(v: unknown): MonitorEvidence | null {
  const rec = asRecord(v)
  if (!rec) return null
  const level = asString(rec.level)
  if (!level) return null
  return { level, ceiling: asString(rec.ceiling) ?? null }
}

export function coerceMonitor(state: unknown): MonitorResponse | null {
  const rec = asRecord(state)
  if (!rec || rec.ok === false) return null
  const stepsRaw = rec.steps
  const steps = Array.isArray(stepsRaw)
    ? stepsRaw.map(coerceStep).filter((row): row is MonitorStep => row !== null)
    : []
  const progressRec = asRecord(rec.progress)
  const total = asFiniteNumber(progressRec?.total) ?? steps.length
  const done = asFiniteNumber(progressRec?.done) ?? 0
  const pct = asFiniteNumber(progressRec?.pct) ?? 0
  return {
    session: asString(rec.session) ?? '',
    run: rec.run == null ? null : coerceRun(rec.run),
    steps,
    current: rec.current == null ? null : coerceCurrent(rec.current),
    progress: { total, done, pct },
    evidence: rec.evidence == null ? null : coerceEvidence(rec.evidence),
    mode: asString(rec.mode) ?? 'free',
    taints: asFiniteNumber(rec.taints) ?? 0,
  }
}

const KINDS: DatasetKind[] = ['hyp3', 'alos_raw', 'slc_stack', 'dem', 'unknown']

function coerceKind(v: unknown): DatasetKind {
  return KINDS.includes(v as DatasetKind) ? (v as DatasetKind) : 'unknown'
}

function coerceDataset(v: unknown): DatasetRow | null {
  const rec = asRecord(v)
  if (!rec) return null
  const id = asString(rec.id)
  const name = asString(rec.name)
  if (!id || !name) return null
  const rangeRec = asRecord(rec.date_range)
  const start = asString(rangeRec?.start)
  const end = asString(rangeRec?.end)
  return {
    id,
    name,
    path: asString(rec.path) ?? '',
    kind: coerceKind(rec.kind),
    size_bytes: asFiniteNumber(rec.size_bytes) ?? 0,
    file_count: asFiniteNumber(rec.file_count) ?? 0,
    date_range: start && end ? { start, end } : null,
    detail: asRecord(rec.detail) ?? {},
  }
}

export function coerceDatasets(v: unknown): DatasetsPayload {
  const rec = asRecord(v)
  if (!rec) return { roots: [], datasets: [], error: 'invalid_datasets' }
  if (asString(rec.error)) {
    return { roots: [], datasets: [], error: asString(rec.error) }
  }
  const list = Array.isArray(rec.datasets) ? rec.datasets.map(coerceDataset).filter((d): d is DatasetRow => d !== null) : []
  const roots = Array.isArray(rec.roots) ? rec.roots.filter((x): x is string => typeof x === 'string') : []
  return { roots, datasets: list, scanned_at: asFiniteNumber(rec.scanned_at) }
}

function coerceFigure(v: unknown): FigureRow | null {
  const rec = asRecord(v)
  if (!rec) return null
  const name = asString(rec.name)
  if (!name) return null
  const thumb = asString(rec.thumbUrl) || asString(rec.url) || asString(rec.fullUrl) || ''
  const url = asString(rec.url) || asString(rec.fullUrl) || thumb
  const full = asString(rec.fullUrl) || url
  return {
    step: asFiniteNumber(rec.step) ?? 0,
    name,
    thumbUrl: thumb,
    url,
    fullUrl: full,
    meta: asRecord(rec.meta) ?? undefined,
  }
}

export function coerceFigures(v: unknown): FiguresPayload {
  const rec = asRecord(v)
  if (!rec) return { run: null, figures: [], error: 'invalid_figures' }
  if (asString(rec.error)) return { run: null, figures: [], error: asString(rec.error) }
  const figures = Array.isArray(rec.figures)
    ? rec.figures.map(coerceFigure).filter((f): f is FigureRow => f !== null)
    : []
  const run = rec.run == null ? null : typeof rec.run === 'string' ? rec.run : asString(asRecord(rec.run)?.run_id) ?? null
  return { run, figures }
}

export function coerceInsarRead(state: unknown): InsarReadState | null {
  const rec = asRecord(state)
  if (!rec || rec.v !== 2) return null
  const monitor = coerceMonitor(rec.monitor)
  if (!monitor) return null
  return {
    v: 2,
    base: asString(rec.base) ?? '',
    monitor,
    datasets: coerceDatasets(rec.datasets),
    figures: coerceFigures(rec.figures),
  }
}

export function currentLine(monitor: MonitorResponse): string {
  const current = monitor.current
  if (current) {
    const method = current.method ? ` · ${current.method}` : ''
    return `▶ ${pad2(current.step)} ${current.name}${method} · ${current.stage}`
  }
  const status = monitor.run?.status ?? 'unknown'
  if (status === 'done') return '▶ finished · no step running'
  const next = monitor.steps.find((step) => step.state === 'pending')
  if (next) {
    const method = next.method ? ` · ${next.method}` : ''
    return `▶ idle · next ${pad2(next.step)} ${next.name}${method}`
  }
  return `▶ idle · run ${status}`
}

export function stepCounts(steps: MonitorStep[]): {
  done: number
  running: number
  failed: number
  stale: number
  skipped: number
  pending: number
} {
  const out = { done: 0, running: 0, failed: 0, stale: 0, skipped: 0, pending: 0 }
  for (const s of steps) {
    if (s.state === 'failed') out.failed += 1
    else if (s.stale) out.stale += 1
    else if (s.state === 'skipped') out.skipped += 1
    else if (s.state === 'done') out.done += 1
    else if (s.state === 'running') out.running += 1
    else out.pending += 1
  }
  return out
}

export type RailGlyph = 'done' | 'running' | 'pending' | 'failed' | 'skipped' | 'stale'

export function railKind(step: MonitorStep): RailGlyph {
  if (step.state === 'failed') return 'failed'
  if (step.stale) return 'stale'
  if (step.state === 'skipped') return 'skipped'
  if (step.state === 'done') return 'done'
  if (step.state === 'running') return 'running'
  return 'pending'
}
