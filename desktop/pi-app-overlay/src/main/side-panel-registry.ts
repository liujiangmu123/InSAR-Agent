// 兼容层：adapter.json sidePanel.stateProvider → 状态函数（无插件名 IPC）

import { findAdapterById } from '../extension-compat/adapter-loader'
import { readAdapterConfig } from '../extension-compat/adapter-backend'
import { readWorkspaceTaskPanelState } from './workspace-task-panel-reader'

export type SidePanelStateProviderId = 'workspace-trellis' | 'http-json' | 'insar-read'

type SidePanelStateFn = (
  cwd: string,
  adapterId: string,
  workspaceId: string,
) => unknown | Promise<unknown>

function trimSlash(s: string): string {
  return s.replace(/\/+$/, '')
}

async function fetchJson(
  url: string,
): Promise<{ ok: true; data: unknown } | { ok: false; error: string }> {
  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(5000) })
    if (!res.ok) return { ok: false, error: `HTTP ${res.status}` }
    return { ok: true, data: await res.json() }
  } catch (e: unknown) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) }
  }
}

/** U0 v2 contract: monitor first, datasets in parallel, figures chained off resolved session. */
export async function fetchInsarReadState(base: string, session: string): Promise<unknown> {
  const monitorUrl = `${base}/api/monitor?session=${encodeURIComponent(session)}`
  const datasetsUrl = `${base}/api/datasets`
  const [monitorRes, datasetsRes] = await Promise.all([
    fetchJson(monitorUrl),
    fetchJson(datasetsUrl),
  ])
  if (!monitorRes.ok) {
    return { ok: false, error: monitorRes.error, base, session }
  }
  const monitor = monitorRes.data
  const datasets = datasetsRes.ok ? datasetsRes.data : { error: datasetsRes.error }

  const rec = monitor !== null && typeof monitor === 'object' ? (monitor as Record<string, unknown>) : null
  const run = rec?.run !== null && typeof rec?.run === 'object' ? (rec.run as Record<string, unknown>) : null
  const resolvedSession = typeof rec?.session === 'string' && rec.session.trim() ? rec.session.trim() : session
  const runId = typeof run?.run_id === 'string' && run.run_id.trim() ? run.run_id.trim() : ''

  let figures: unknown = { run: null, figures: [] }
  if (runId) {
    const figUrl = `${base}/api/figures?session=${encodeURIComponent(resolvedSession)}&run_id=${encodeURIComponent(runId)}`
    const figRes = await fetchJson(figUrl)
    figures = figRes.ok ? figRes.data : { error: figRes.error }
  }

  return { v: 2, base, monitor, datasets, figures }
}

const PROVIDERS: Record<SidePanelStateProviderId, SidePanelStateFn> = {
  'workspace-trellis': (cwd, adapterId, workspaceId) => {
    const base = readWorkspaceTaskPanelState(cwd)
    const view = readAdapterConfig(adapterId, workspaceId)
    const showRecentJournal = view.showRecentJournal !== false
    const limit = typeof view.journalLimit === 'number' ? view.journalLimit : 5
    if (!showRecentJournal) return { ...base, recentJournals: [] }
    if (base.recentJournals && base.recentJournals.length > limit) {
      return { ...base, recentJournals: base.recentJournals.slice(0, limit) }
    }
    return base
  },
  // 失败必须落成 state：IPC 已 async，抛错会打崩主进程而非显示右栏错误
  'http-json': async (_cwd, adapterId, workspaceId) => {
    const view = readAdapterConfig(adapterId, workspaceId)
    const hint = typeof view.apiBaseHint === 'string' ? view.apiBaseHint.trim() : ''
    const envBase = typeof process.env.INSAR_API_BASE === 'string' ? process.env.INSAR_API_BASE.trim() : ''
    const base = trimSlash(hint || envBase || 'http://127.0.0.1:8873')
    const sessionRaw = typeof view.session === 'string' ? view.session.trim() : ''
    const session = sessionRaw || '@latest'
    const url = `${base}/api/monitor?session=${encodeURIComponent(session)}`
    try {
      const res = await fetch(url, { signal: AbortSignal.timeout(5000) })
      if (!res.ok) {
        return { ok: false, error: `HTTP ${res.status}`, base, session }
      }
      return await res.json()
    } catch (e: unknown) {
      return {
        ok: false,
        error: e instanceof Error ? e.message : String(e),
        base,
        session,
      }
    }
  },
  'insar-read': async (_cwd, adapterId, workspaceId) => {
    const view = readAdapterConfig(adapterId, workspaceId)
    const hint = typeof view.apiBaseHint === 'string' ? view.apiBaseHint.trim() : ''
    const envBase = typeof process.env.INSAR_API_BASE === 'string' ? process.env.INSAR_API_BASE.trim() : ''
    const base = trimSlash(hint || envBase || 'http://127.0.0.1:8873')
    const sessionRaw = typeof view.session === 'string' ? view.session.trim() : ''
    const session = sessionRaw || '@latest'
    return fetchInsarReadState(base, session)
  },
}

export async function resolveSidePanelState(
  adapterId: string,
  cwd: string,
  workspaceId: string,
): Promise<{ ok: true; state: unknown } | { ok: false; error: string }> {
  const adapter = findAdapterById(adapterId, cwd)
  const sp = adapter?.sidePanel
  if (!sp?.stateProvider) return { ok: false, error: 'no_state_provider' }
  const fn = PROVIDERS[sp.stateProvider as SidePanelStateProviderId]
  if (!fn) return { ok: false, error: `unknown_provider:${sp.stateProvider}` }
  return { ok: true, state: await fn(cwd, adapterId, workspaceId) }
}
