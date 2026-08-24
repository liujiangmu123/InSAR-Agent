import { useState } from 'react'
import { cn } from '@renderer/lib/utils'
import {
  type AccessMode,
  type MonitorResponse,
  type MonitorStep,
  type RailGlyph,
  formatDuration,
  inferAccessMode,
  pad2,
  railKind,
  stepCounts,
} from './insar-panel-model'

const NODE_MARK: Record<RailGlyph, string> = {
  done: '',
  running: '',
  pending: '',
  failed: '×',
  skipped: '↷',
  stale: '!',
}

function Node({ kind }: { kind: RailGlyph }) {
  return (
    <span className={cn('insar-node', `is-${kind}`)} aria-hidden>
      {NODE_MARK[kind]}
    </span>
  )
}

function StepRow({
  step,
  current,
  open,
  onToggle,
}: {
  step: MonitorStep
  current: boolean
  open: boolean
  onToggle: () => void
}) {
  const kind = railKind(step)
  const duration = step.duration == null ? null : formatDuration(step.duration)
  const attempts = step.attempts > 1 ? `×${step.attempts}` : null
  return (
    <div>
      <button
        type="button"
        onClick={onToggle}
        title={step.stale_reason || step.failure_class || undefined}
        className={cn(
          'grid w-full grid-cols-[1.7rem_14px_minmax(0,1fr)_auto] items-center gap-x-1.5 px-3 py-1 text-left text-[11px] leading-tight',
          current ? 'bg-[color:var(--insar-run-weak)]' : 'hover:bg-accent/20',
        )}
      >
        <span className="font-mono tabular-nums text-muted-foreground">{pad2(step.step)}</span>
        <Node kind={kind} />
        <span className="min-w-0 truncate">
          <span className="text-foreground/90">{step.name}</span>
          {step.method && <span className="ml-1.5 font-mono text-[10px] text-muted-foreground">{step.method}</span>}
        </span>
        <span className="font-mono text-[10px] tabular-nums text-muted-foreground">
          {[duration, attempts].filter(Boolean).join(' ')}
        </span>
      </button>
      {open && (
        <div className="mx-3 mb-1.5 rounded-md border border-border/40 bg-muted/30 px-2 py-1.5 font-mono text-[10px] leading-relaxed text-muted-foreground">
          <div>方法 {step.method || '—'} · 阶段 {step.stage || '—'} · 状态 {step.state}</div>
          {duration && <div>耗时 {duration}{attempts ? ` ${attempts}` : ''}</div>}
          {step.failure_class && <div>failure_class {step.failure_class}</div>}
          {step.stale && <div>stale {step.stale_reason || '标脏'}</div>}
        </div>
      )}
    </div>
  )
}

function Group({
  title,
  note,
  steps,
  currentStep,
  openStep,
  onToggle,
}: {
  title: string
  note?: string
  steps: MonitorStep[]
  currentStep: number | null
  openStep: number | null
  onToggle: (step: number) => void
}) {
  if (steps.length === 0) return null
  const done = steps.filter((s) => s.state === 'done' || s.state === 'skipped').length
  return (
    <div className="border-t border-border/20">
      <div className="flex items-baseline justify-between px-3 py-1 text-[10px] uppercase tracking-wider text-muted-foreground/70">
        <span className="flex min-w-0 items-baseline gap-1.5">
          <span>{title}</span>
          {note ? (
            <span className="normal-case tracking-normal text-muted-foreground/60">{note}</span>
          ) : null}
        </span>
        <span className="font-mono tabular-nums">
          {done}/{steps.length}
        </span>
      </div>
      {steps.map((step) => (
        <StepRow
          key={step.step}
          step={step}
          current={currentStep === step.step || step.state === 'running'}
          open={openStep === step.step}
          onToggle={() => onToggle(step.step)}
        />
      ))}
    </div>
  )
}

function CoreSkippedNote({ figuresEmpty }: { figuresEmpty: boolean }) {
  return (
    <div className="border-t border-border/20">
      <div className="px-3 py-1 text-[10px] uppercase tracking-wider text-muted-foreground/70">
        核心流水线 01–11
      </div>
      <div className="px-3 py-1.5 text-[11px] leading-relaxed text-muted-foreground/80">
        本 run 不走主链(位移产品直接分析)
        {figuresEmpty ? (
          <div className="mt-1">本 run 不走主链出图，分析产物看 files 预览</div>
        ) : null}
      </div>
    </div>
  )
}

export function InsarRail({
  monitor,
  accessMode,
  figuresEmpty = false,
}: {
  monitor: MonitorResponse
  accessMode?: AccessMode | null
  figuresEmpty?: boolean
}) {
  const [openStep, setOpenStep] = useState<number | null>(null)
  const counts = stepCounts(monitor.steps)
  const core = monitor.steps.filter((s) => s.step < 20)
  const analysis = monitor.steps.filter((s) => s.step >= 20)
  const currentStep = monitor.current?.step ?? null
  const mode = accessMode ?? inferAccessMode(monitor.steps)
  const showCoreCNote = mode === 'C' && core.length === 0
  const showModeCFiguresNote = mode === 'C' && figuresEmpty
  const chips: Array<{ key: keyof typeof counts; label: string; cls: string }> = [
    { key: 'done', label: '完成', cls: 'is-done' },
    { key: 'running', label: '运行', cls: 'is-running' },
    { key: 'failed', label: '失败', cls: 'is-failed' },
    { key: 'stale', label: '失效', cls: 'is-stale' },
    { key: 'skipped', label: '跳过', cls: 'is-skipped' },
    { key: 'pending', label: '待执行', cls: '' },
  ]

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex flex-wrap gap-1 px-3 py-1.5">
        {chips.map((c) => (
          <span key={c.key} className={cn('insar-chip', c.cls, counts[c.key] === 0 && 'zero')}>
            {c.label} <b>{counts[c.key]}</b>
          </span>
        ))}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {monitor.steps.length === 0 && !showCoreCNote ? (
          monitor.run ? (
            <div className="p-3 text-[11px] text-muted-foreground/70">尚无步骤</div>
          ) : null
        ) : (
          <>
            {showCoreCNote ? (
              <CoreSkippedNote figuresEmpty={figuresEmpty} />
            ) : (
              <Group
                title="核心流水线 01–11"
                note={mode === 'B' ? '云端已完成' : undefined}
                steps={core}
                currentStep={currentStep}
                openStep={openStep}
                onToggle={(n) => setOpenStep((cur) => (cur === n ? null : n))}
              />
            )}
            <Group
              title="分析后处理 20–28"
              steps={analysis}
              currentStep={currentStep}
              openStep={openStep}
              onToggle={(n) => setOpenStep((cur) => (cur === n ? null : n))}
            />
            {showModeCFiguresNote && !showCoreCNote ? (
              <div className="border-t border-border/20 px-3 py-1.5 text-[11px] leading-relaxed text-muted-foreground/80">
                本 run 不走主链出图，分析产物看 files 预览
              </div>
            ) : null}
          </>
        )}
      </div>
    </div>
  )
}
