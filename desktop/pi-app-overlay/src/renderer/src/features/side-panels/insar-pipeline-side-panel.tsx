import { useEffect, useState } from 'react'
import { ipcClient } from '@renderer/lib/ipc-client'
import { useUIStore } from '@renderer/stores/ui-store'
import { cn } from '@renderer/lib/utils'
import type { SidePanelComponentProps } from './side-panel-registry'
import './insar-panel.css'
import { InsarRail } from './insar-rail'
import { InsarDatasetsSection } from './insar-datasets-section'
import { InsarFiguresSection } from './insar-figures-section'
import {
  type DatasetsPayload,
  type FiguresPayload,
  type MonitorResponse,
  type ProviderError,
  asRecord,
  asString,
  coerceInsarRead,
  coerceMonitor,
  currentLine,
  readProviderError,
  shortRunId,
} from './insar-panel-model'

const POLL_MS = 3_000

type SectionId = 'pipeline' | 'datasets' | 'figures'

function readIpcEnvelope(res: unknown): { ok: boolean; error: string | null; state: unknown } {
  const rec = asRecord(res)
  if (!rec) return { ok: false, error: 'empty_ipc_response', state: null }
  if (rec.ok === false) {
    return { ok: false, error: asString(rec.error) || 'load_failed', state: rec.state ?? null }
  }
  if (rec.ok === true) return { ok: true, error: null, state: rec.state }
  return { ok: false, error: 'invalid_ipc_response', state: null }
}

function evidenceChipClass(level: string): string {
  const n = level.toLowerCase()
  if (n.includes('simul')) return 'is-sim'
  if (n === 'audited' || n.includes('audit')) return 'is-evidence'
  return ''
}

function EvidenceBadge({ level, ceiling }: { level: string; ceiling: string | null }) {
  const extra = ceiling && ceiling !== level ? ` (ceiling ${ceiling})` : ''
  return (
    <span className={cn('insar-chip', evidenceChipClass(level))}>
      证据 {level}{extra}
    </span>
  )
}

export function InsarPipelineSidePanel({ panelId, adapterId, title }: SidePanelComponentProps) {
  const workspace = useUIStore((s) => s.currentWorkspace)
  const [monitor, setMonitor] = useState<MonitorResponse | null>(null)
  const [datasets, setDatasets] = useState<DatasetsPayload | null>(null)
  const [figures, setFigures] = useState<FiguresPayload | null>(null)
  const [apiBase, setApiBase] = useState('')
  const [error, setError] = useState<ProviderError | null>(null)
  const [loading, setLoading] = useState(false)
  const [section, setSection] = useState<SectionId>('pipeline')

  useEffect(() => {
    if (!workspace || !adapterId) {
      setMonitor(null)
      setDatasets(null)
      setFigures(null)
      setApiBase('')
      setError(null)
      setLoading(false)
      return
    }

    let cancelled = false
    let inFlight = false
    let first = true
    setLoading(true)

    const tick = async () => {
      if (cancelled || inFlight) return
      inFlight = true
      try {
        const res: unknown = await ipcClient.invoke('adapter.sidePanel.getState', {
          adapterId,
          workspaceId: workspace,
        })
        if (cancelled) return
        const envelope = readIpcEnvelope(res)
        if (!envelope.ok) {
          setError({ error: envelope.error || 'load_failed' })
          return
        }
        const providerError = readProviderError(envelope.state)
        if (providerError) {
          setError(providerError)
          return
        }
        const v2 = coerceInsarRead(envelope.state)
        if (v2) {
          setError(null)
          setMonitor(v2.monitor)
          setDatasets(v2.datasets)
          setFigures(v2.figures)
          setApiBase(v2.base)
          return
        }
        const legacy = coerceMonitor(envelope.state)
        if (!legacy) {
          setError({ error: 'invalid_monitor_payload' })
          return
        }
        setError(null)
        setMonitor(legacy)
        setDatasets(null)
        setFigures(null)
        setApiBase('')
      } catch (e) {
        if (cancelled) return
        setError({ error: e instanceof Error ? e.message : String(e) })
      } finally {
        inFlight = false
        if (first) {
          first = false
          if (!cancelled) setLoading(false)
        }
      }
    }

    void tick()
    const timer = window.setInterval(() => void tick(), POLL_MS)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [workspace, adapterId])

  if (!adapterId) {
    return <div className="p-4 text-[12px] text-muted-foreground">未绑定 adapterId（{panelId}）</div>
  }

  if (!workspace) {
    return <div className="p-4 text-[12px] text-muted-foreground">请先打开项目</div>
  }

  const headerTitle = title || 'InSAR'
  const showFullError = Boolean(error) && !monitor
  const dsCount = datasets && !datasets.error ? datasets.datasets.length : 0
  const figCount = figures && !figures.error ? figures.figures.length : 0
  const simulated = Boolean(monitor?.run?.simulated)

  return (
    <div className="insar-panel scrollbar-overlay flex h-full flex-col overflow-hidden">
      <div className="shrink-0 border-b border-border/40 px-3 py-2">
        <div className="flex items-baseline justify-between gap-2">
          <span className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground/60">
            {headerTitle}
          </span>
          {monitor?.run ? (
            <span className="font-mono text-[11px] tabular-nums text-foreground">
              {shortRunId(monitor.run.run_id)}
              <span className="ml-2 text-muted-foreground">{monitor.progress.pct}%</span>
            </span>
          ) : (
            <span className="font-mono text-[10px] text-muted-foreground/50">
              {loading ? '…' : '尚无 run'}
            </span>
          )}
        </div>
        {monitor?.run ? (
          <div className="mt-1.5 flex flex-wrap items-center gap-1">
            {monitor.run.scenario && (
              <span className="insar-chip">{monitor.run.scenario}</span>
            )}
            <span className="insar-chip">{monitor.run.status}</span>
            {simulated && <span className="insar-chip is-sim">模拟</span>}
            {monitor.evidence && (
              <EvidenceBadge level={monitor.evidence.level} ceiling={monitor.evidence.ceiling} />
            )}
            <span className="insar-chip">mode {monitor.mode}</span>
            <span className={cn('insar-chip', monitor.taints > 0 && 'is-stale')}>
              {monitor.taints > 0 ? `${monitor.taints} 失效` : '0 失效'}
            </span>
          </div>
        ) : (
          !showFullError && (
            <div className="mt-1 text-[10px] text-muted-foreground/60">
              {monitor
                ? `尚无 run · mode ${monitor.mode}。对话中让 agent 用 insar_plan_run 规划。`
                : loading
                  ? '加载监控…'
                  : '等待监控'}
            </div>
          )
        )}
        {monitor && (
          <div className="mt-2 h-0.5 overflow-hidden rounded-full bg-muted">
            <div
              className="h-full transition-[width] duration-300"
              style={{
                width: `${Math.max(0, Math.min(100, monitor.progress.pct))}%`,
                background: 'var(--insar-run)',
              }}
            />
          </div>
        )}
        <div className="mt-2 flex gap-1">
          {(
            [
              { id: 'pipeline' as const, label: '流水线' },
              { id: 'datasets' as const, label: `数据${datasets ? ` ${dsCount}` : ''}` },
              { id: 'figures' as const, label: `图件${figures ? ` ${figCount}` : ''}` },
            ]
          ).map((tab) => (
            <button
              key={tab.id}
              type="button"
              onClick={() => setSection(tab.id)}
              className={cn('insar-tab', section === tab.id && 'is-on')}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      {simulated && monitor?.run && (
        <div className="insar-banner-sim shrink-0">模拟结果 · 非真实数据</div>
      )}

      {error && (
        <div className="shrink-0 border-b border-destructive/30 bg-destructive/10 px-3 py-2">
          <div className="text-[11px] text-destructive">
            {error.base ? 'backend unreachable' : 'monitor error'}
          </div>
          <div className="mt-0.5 break-all font-mono text-[10px] text-destructive/80">{error.error}</div>
          {(error.base || error.session) && (
            <div className="mt-0.5 font-mono text-[10px] text-muted-foreground">
              {[error.base, error.session ? `session ${error.session}` : null]
                .filter((part): part is string => Boolean(part))
                .join(' · ')}
            </div>
          )}
        </div>
      )}

      {showFullError ? (
        <div className="flex flex-1 items-center justify-center p-4 text-center">
          <div className="max-w-[240px] space-y-1">
            <div className="text-[12px] text-muted-foreground">无法读取流水线状态</div>
            <div className="font-mono text-[10px] text-muted-foreground/50">check INSAR_API_BASE / apiBaseHint</div>
          </div>
        </div>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col">
          {section === 'pipeline' && (
            <>
              {!monitor && loading && (
                <div className="p-3 text-[12px] text-muted-foreground">加载中…</div>
              )}
              {monitor && <InsarRail monitor={monitor} />}
              {monitor?.run && (
                <div className="shrink-0 truncate border-t border-border/40 px-3 py-1.5 font-mono text-[10px] text-muted-foreground">
                  {currentLine(monitor)}
                </div>
              )}
            </>
          )}
          {section === 'datasets' && <InsarDatasetsSection data={datasets} />}
          {section === 'figures' && (
            <InsarFiguresSection base={apiBase} data={figures} simulated={simulated} />
          )}
        </div>
      )}
    </div>
  )
}
