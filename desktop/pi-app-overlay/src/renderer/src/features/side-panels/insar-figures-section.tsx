import { useEffect, useState } from 'react'
import type { FigureRow, FiguresPayload } from './insar-panel-model'

function joinUrl(base: string, rel: string): string {
  if (!rel) return ''
  if (/^https?:\/\//i.test(rel)) return rel
  const b = base.replace(/\/+$/, '')
  return rel.startsWith('/') ? `${b}${rel}` : `${b}/${rel}`
}

function FigureTile({
  base,
  fig,
  onOpen,
}: {
  base: string
  fig: FigureRow
  onOpen: () => void
}) {
  const [failed, setFailed] = useState(false)
  const src = joinUrl(base, fig.thumbUrl)
  const title = (typeof fig.meta?.title === 'string' && fig.meta.title) || fig.name
  return (
    <button type="button" onClick={onOpen} className="text-left">
      <div className="aspect-[4/3] overflow-hidden rounded-md border border-border/40 bg-muted/40">
        {failed || !src ? (
          <div className="flex h-full items-center justify-center px-1 text-center font-mono text-[9px] text-muted-foreground/60">
            {fig.name}
          </div>
        ) : (
          <img
            src={src}
            alt=""
            loading="lazy"
            className="h-full w-full object-cover"
            onError={() => setFailed(true)}
          />
        )}
      </div>
      <div className="mt-0.5 truncate text-[10px] text-foreground/80">{title}</div>
      <div className="font-mono text-[9px] text-muted-foreground">S{String(fig.step).padStart(2, '0')}</div>
    </button>
  )
}

export function InsarFiguresSection({
  base,
  data,
  simulated,
}: {
  base: string
  data: FiguresPayload | null
  simulated?: boolean
}) {
  const [open, setOpen] = useState<FigureRow | null>(null)

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(null)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  if (!data) {
    return <div className="p-3 text-[11px] text-muted-foreground/70">供数待接线（适配器尚未切到 insar-read）</div>
  }
  if (data.error) {
    return <div className="p-3 text-[11px] text-destructive/80">图件不可用 · {data.error}</div>
  }
  if (data.figures.length === 0) {
    return <div className="p-3 text-[11px] text-muted-foreground/70">该 run 尚无图件。</div>
  }

  return (
    <div className="relative min-h-0 flex-1 overflow-y-auto">
      {simulated && <div className="insar-banner-sim">模拟结果 · 图件仅供流程演示</div>}
      <div className="grid grid-cols-[repeat(auto-fill,minmax(120px,1fr))] gap-2 p-3">
        {data.figures.map((fig) => (
          <FigureTile key={`${fig.step}-${fig.name}`} base={base} fig={fig} onOpen={() => setOpen(fig)} />
        ))}
      </div>
      {open && (
        <div
          className="absolute inset-0 z-10 flex flex-col bg-background/95 p-3"
          role="dialog"
          aria-label={open.name}
          onClick={() => setOpen(null)}
        >
          <div className="mb-2 text-[11px] text-muted-foreground" onClick={(e) => e.stopPropagation()}>
            S{String(open.step).padStart(2, '0')} · {open.name}
            <button type="button" className="ml-2 text-primary" onClick={() => setOpen(null)}>
              关闭
            </button>
          </div>
          <img
            src={joinUrl(base, open.url || open.thumbUrl)}
            alt={open.name}
            className="max-h-full max-w-full object-contain"
            onClick={(e) => e.stopPropagation()}
          />
        </div>
      )}
    </div>
  )
}
