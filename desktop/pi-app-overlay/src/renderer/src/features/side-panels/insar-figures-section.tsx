import { useEffect, useState } from 'react'
import {
  formatBytes,
  pad2,
  type FigureRow,
  type FileRow,
  type FileViewer,
  type FiguresPayload,
} from './insar-panel-model'
import { InsarPreviewDialog } from './insar-preview-dialog'

export type InsarFiguresSectionProps = {
  base: string
  data: FiguresPayload | null
  simulated?: boolean
  session?: string
  runId?: string
}

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
      <div className="font-mono text-[9px] text-muted-foreground">S{pad2(fig.step)}</div>
    </button>
  )
}

function fileMeta(file: FileRow): string {
  const parts = [`S${pad2(file.step)}`, file.size != null ? formatBytes(file.size) : null].filter(
    (p): p is string => Boolean(p),
  )
  return parts.join(' · ')
}

function hasPreviewPath(file: FileRow): boolean {
  return Boolean(file.path?.trim())
}

function viewerHint(viewer: FileViewer): string {
  if (viewer.status === 'reserved') return '预留 · 尚未实现查看器'
  if (viewer.render === 'table' || viewer.render === 'json_tree' || viewer.render === 'hdf5_meta') {
    return '点击翻页预览'
  }
  if (viewer.render === 'timeseries_point') return '对话工具可取点'
  if (viewer.render === 'external') return '用外部程序打开'
  return '已就绪'
}

function FileItem({ file, onOpen }: { file: FileRow; onOpen: () => void }) {
  const viewers = file.viewers ?? []
  const canOpen = hasPreviewPath(file)
  const kind = file.kind?.trim()
  return (
    <li className="min-w-0">
      <button
        type="button"
        className={canOpen ? 'insar-preview-file' : 'insar-preview-file cursor-not-allowed opacity-50'}
        disabled={!canOpen}
        title={canOpen ? undefined : '该文件没有工作区相对路径，无法预览'}
        onClick={canOpen ? onOpen : undefined}
      >
        <div className="flex min-w-0 items-center gap-1">
          <div className="min-w-0 truncate text-[11px] text-foreground/90">{file.name}</div>
          {kind ? <span className="insar-preview-kind">{kind}</span> : null}
        </div>
        <div className="font-mono text-[10px] text-muted-foreground">{fileMeta(file)}</div>
      </button>
      {viewers.length > 0 && (
        <ul className="mt-0.5 space-y-0.5">
          {viewers.map((viewer) => (
            <li key={viewer.id} className={`insar-viewer is-${viewer.status}`}>
              {canOpen && viewer.status !== 'reserved' ? (
                <button type="button" className="insar-preview-link" onClick={onOpen}>
                  {viewer.title}
                  {' · '}
                  {viewerHint(viewer)}
                </button>
              ) : (
                <>
                  {viewer.title}
                  {' · '}
                  {viewerHint(viewer)}
                </>
              )}
            </li>
          ))}
        </ul>
      )}
    </li>
  )
}

export function InsarFiguresSection({
  base,
  data,
  simulated,
  session,
  runId,
}: InsarFiguresSectionProps) {
  const [open, setOpen] = useState<FigureRow | null>(null)
  const [preview, setPreview] = useState<{ path: string; name: string } | null>(null)

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

  const files = data.files ?? []
  const empty = data.figures.length === 0 && files.length === 0

  return (
    <div className="relative min-h-0 flex-1">
      <div className="h-full overflow-y-auto">
        {data.truncated && <div className="insar-banner-trunc">仅显示前 100 项</div>}
        {simulated && !empty && <div className="insar-banner-sim">模拟结果 · 图件仅供流程演示</div>}
        {empty ? (
          <div className="p-3 text-[11px] text-muted-foreground/70">该 run 尚无图件。</div>
        ) : (
          <>
            {data.figures.length > 0 && (
              <div className="grid grid-cols-[repeat(auto-fill,minmax(120px,1fr))] gap-2 p-3">
                {data.figures.map((fig) => (
                  <FigureTile
                    key={`${fig.step}-${fig.name}`}
                    base={base}
                    fig={fig}
                    onOpen={() => {
                      setPreview(null)
                      setOpen(fig)
                    }}
                  />
                ))}
              </div>
            )}
            {files.length > 0 && (
              <div className={data.figures.length > 0 ? 'border-t border-border/20 px-3 py-2' : 'px-3 py-2'}>
                <div className="text-[10px] uppercase tracking-wider text-muted-foreground/70">非图像产物</div>
                <div className="mt-1 text-[10px] leading-relaxed text-muted-foreground/70">
                  有路径的条目可预览（表格/JSON 翻页；立方体仅切片与单像元曲线）。地球仪/几何请用外部程序。无路径则仅列出。
                </div>
                <ul className="mt-2 space-y-1.5">
                  {files.map((file) => (
                    <FileItem
                      key={`${file.step}-${file.name}-${file.path ?? ''}`}
                      file={file}
                      onOpen={() => {
                        const path = file.path?.trim()
                        if (!path) return
                        setOpen(null)
                        setPreview({ path, name: file.name })
                      }}
                    />
                  ))}
                </ul>
              </div>
            )}
          </>
        )}
      </div>
      {open && (
        <div
          className="absolute inset-0 z-10 flex flex-col bg-background/95 p-3"
          role="dialog"
          aria-label={open.name}
          onClick={() => setOpen(null)}
        >
          <div className="mb-2 text-[11px] text-muted-foreground" onClick={(e) => e.stopPropagation()}>
            S{pad2(open.step)} · {open.name}
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
      {preview && (
        <InsarPreviewDialog
          base={base}
          session={session}
          runId={runId}
          path={preview.path}
          name={preview.name}
          onClose={() => setPreview(null)}
        />
      )}
    </div>
  )
}
