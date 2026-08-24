import { useEffect, useState, type MouseEvent } from 'react'

export type InsarPreviewDialogProps = {
  base: string
  session?: string
  runId?: string
  path: string
  name: string
  onClose: () => void
}

function joinUrl(base: string, rel: string): string {
  if (!rel) return ''
  if (/^https?:\/\//i.test(rel)) return rel
  const b = base.replace(/\/+$/, '')
  return rel.startsWith('/') ? `${b}${rel}` : `${b}/${rel}`
}

function asRecord(v: unknown): Record<string, unknown> | null {
  return v !== null && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null
}

function asString(v: unknown): string | null {
  return typeof v === 'string' ? v : null
}

function asBool(v: unknown): boolean {
  return v === true
}

function asNum(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null
}

function mapHw(v: unknown): { height: number; width: number } | null {
  const rec = asRecord(v)
  if (!rec) return null
  const height = asNum(rec.height)
  const width = asNum(rec.width)
  if (height == null || width == null) return null
  const h = Math.trunc(height)
  const w = Math.trunc(width)
  if (h <= 0 || w <= 0) return null
  return { height: h, width: w }
}

function statsChipLabels(stats: Record<string, unknown>): string[] {
  const chips: string[] = []
  const p2 = asNum(stats.p2)
  const p98 = asNum(stats.p98)
  const vmin = asNum(stats.vmin)
  const vmax = asNum(stats.vmax)
  const nanFrac = asNum(stats.nan_fraction)
  if (p2 != null && p98 != null) chips.push(`p2/p98 ${p2} / ${p98}`)
  else if (p2 != null) chips.push(`p2 ${p2}`)
  else if (p98 != null) chips.push(`p98 ${p98}`)
  else if (vmin != null && vmax != null) chips.push(`vmin/vmax ${vmin} / ${vmax}`)
  else if (vmin != null) chips.push(`vmin ${vmin}`)
  else if (vmax != null) chips.push(`vmax ${vmax}`)
  if (nanFrac != null) chips.push(`nan_fraction ${nanFrac}`)
  const nFinite = asNum(stats.n_finite)
  if (nFinite != null) chips.push(`n_finite ${nFinite}`)
  return chips
}

function bboxLine(rec: Record<string, unknown>): string | null {
  const bbox = asRecord(rec.bbox)
  if (!bbox) return null
  const xmin = asNum(bbox.xmin)
  const ymin = asNum(bbox.ymin)
  const xmax = asNum(bbox.xmax)
  const ymax = asNum(bbox.ymax)
  if (xmin == null || ymin == null || xmax == null || ymax == null) return null
  return `bbox ${xmin}, ${ymin} – ${xmax}, ${ymax}`
}

/** Map a click on the displayed <img> to data row/col using payload.map, or null. */
function clickToRowCol(
  e: MouseEvent<HTMLImageElement>,
  map: { height: number; width: number },
): { row: number; col: number } | null {
  const img = e.currentTarget
  const nw = img.naturalWidth
  const nh = img.naturalHeight
  if (nw <= 0 || nh <= 0) return null
  const boxW = img.clientWidth
  const boxH = img.clientHeight
  if (boxW <= 0 || boxH <= 0) return null
  const scale = Math.min(boxW / nw, boxH / nh)
  const contentW = nw * scale
  const contentH = nh * scale
  if (contentW <= 0 || contentH <= 0) return null
  const x = e.nativeEvent.offsetX - (boxW - contentW) / 2
  const y = e.nativeEvent.offsetY - (boxH - contentH) / 2
  if (x < 0 || y < 0 || x > contentW || y > contentH) return null
  const fx = x / contentW
  const fy = y / contentH
  if (!Number.isFinite(fx) || !Number.isFinite(fy)) return null
  const row = Math.min(map.height - 1, Math.max(0, Math.floor(fy * map.height)))
  const col = Math.min(map.width - 1, Math.max(0, Math.floor(fx * map.width)))
  return { row, col }
}

function cellText(v: unknown): string {
  if (v == null) return ''
  if (typeof v === 'string') return v
  if (typeof v === 'number' || typeof v === 'boolean') return String(v)
  try {
    return JSON.stringify(v)
  } catch {
    return String(v)
  }
}

function shapeText(v: unknown): string {
  if (Array.isArray(v)) return v.map(cellText).join('×')
  if (v == null) return ''
  return cellText(v)
}

function errorFromBody(status: number, body: unknown): string {
  const rec = asRecord(body)
  const detail = rec?.detail
  if (typeof detail === 'string' && detail.trim()) return detail
  if (Array.isArray(detail)) {
    const parts = detail
      .map((item) => {
        if (typeof item === 'string') return item
        const row = asRecord(item)
        return row && typeof row.msg === 'string' ? row.msg : ''
      })
      .filter((s) => s.trim())
    if (parts.length) return parts.join('; ')
  }
  return `预览失败 (${status})`
}

function isByteOffsetPath(path: string, member?: string): boolean {
  const name = (member || path).split(/[/\\]/).pop() || ''
  return /\.(ya?ml|md|txt|log|par|rsc|xml|kml|json)$/i.test(name)
}

function stringList(v: unknown): string[] {
  if (!Array.isArray(v)) return []
  return v.map(cellText)
}

function PreviewNote({ text }: { text: string | null }) {
  if (!text) return null
  return <p className="insar-preview-note">{text}</p>
}

function TruncBanner({ truncated, note }: { truncated: boolean; note: string | null }) {
  if (!truncated) return <PreviewNote text={note} />
  return (
    <div className="insar-preview-trunc">
      本页窗口预览，可用下方按钮看其余部分
      {note ? ` · ${note}` : ''}
    </div>
  )
}

function PreviewImage({
  base,
  url,
  map,
  pickable,
  onPick,
}: {
  base: string
  url: string | null
  map: { height: number; width: number } | null
  pickable: boolean
  onPick: (row: number, col: number) => void
}) {
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    setFailed(false)
  }, [url])
  if (!url || failed) return null
  const src = joinUrl(base, url)
  if (!src) return null
  const canPick = pickable && map != null
  return (
    <img
      src={src}
      alt=""
      draggable={false}
      className={canPick ? 'insar-preview-img is-pick' : 'insar-preview-img'}
      title={canPick ? '点击选取像元' : undefined}
      onError={() => setFailed(true)}
      onClick={
        canPick
          ? (e) => {
              const hit = clickToRowCol(e, map)
              if (hit) onPick(hit.row, hit.col)
            }
          : undefined
      }
    />
  )
}

function StatChips({ stats }: { stats: Record<string, unknown> }) {
  const items = statsChipLabels(stats)
  if (items.length === 0) return null
  return (
    <div className="insar-preview-chips">
      {items.map((item) => (
        <span key={item} className="insar-preview-stat">
          {item}
        </span>
      ))}
    </div>
  )
}

function AttrsBlock({ attrs }: { attrs: Record<string, unknown> }) {
  const lines = Object.entries(attrs).slice(0, 20)
  if (lines.length === 0) return null
  return (
    <ul className="insar-preview-attrs">
      {lines.map(([k, v]) => (
        <li key={k}>
          {k}={cellText(v)}
        </li>
      ))}
    </ul>
  )
}

function SampleLine({ row, col }: { row: number | null; col: number | null }) {
  if (row == null && col == null) return null
  const parts = [
    row != null ? `sample_row ${row}` : null,
    col != null ? `sample_col ${col}` : null,
  ].filter((p): p is string => Boolean(p))
  return <p className="insar-preview-meta">{parts.join(' · ')}</p>
}

function ChipBar({
  items,
  current,
  onPick,
}: {
  items: string[]
  current: string | null
  onPick: (v: string) => void
}) {
  if (items.length === 0) return null
  return (
    <div className="insar-preview-chips">
      {items.map((item) => (
        <button
          key={item}
          type="button"
          className={item === current ? 'insar-preview-chip is-on' : 'insar-preview-chip'}
          onClick={() => onPick(item)}
        >
          {item}
        </button>
      ))}
    </div>
  )
}

function Pager({
  label,
  disablePrev,
  disableNext,
  onPrev,
  onNext,
}: {
  label: string
  disablePrev: boolean
  disableNext: boolean
  onPrev: () => void
  onNext: () => void
}) {
  return (
    <div className="insar-preview-pager">
      <button type="button" disabled={disablePrev} onClick={onPrev}>
        上一{label}
      </button>
      <button type="button" disabled={disableNext} onClick={onNext}>
        下一{label}
      </button>
    </div>
  )
}

function TableBody({ rec }: { rec: Record<string, unknown> }) {
  const columns = Array.isArray(rec.columns) ? rec.columns.map(cellText) : []
  const rows = Array.isArray(rec.rows) ? rec.rows : []
  const sheet = asString(rec.sheet)
  const nRows = asNum(rec.n_rows_total)
  const nCols = asNum(rec.n_cols_total)
  const truncated = asBool(rec.truncated)
  const note = asString(rec.note)
  const capParts = [
    sheet ? `表 ${sheet}` : null,
    nRows != null && nCols != null ? `共 ${nRows} 行 × ${nCols} 列` : null,
    truncated ? '本页窗口' : '本页完整',
    bboxLine(rec),
    note,
  ].filter((p): p is string => Boolean(p))
  return (
    <div className="insar-preview-scroll">
      <table className="insar-preview-table">
        {capParts.length > 0 && <caption>{capParts.join(' · ')}</caption>}
        {columns.length > 0 && (
          <thead>
            <tr>
              {columns.map((col, i) => (
                <th key={`c${i}`} title={col}>
                  {col}
                </th>
              ))}
            </tr>
          </thead>
        )}
        <tbody>
          {rows.map((row, ri) => {
            const cells = Array.isArray(row) ? row : [row]
            return (
              <tr key={`r${ri}`}>
                {(columns.length ? columns : cells).map((_, ci) => {
                  const text = cellText(cells[ci])
                  return (
                    <td key={`r${ri}c${ci}`} title={text}>
                      {text}
                    </td>
                  )
                })}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function JsonBody({ rec }: { rec: Record<string, unknown> }) {
  const text = asString(rec.text)
  let body = text
  if (!body) {
    try {
      body = JSON.stringify(rec.data ?? rec, null, 2)
    } catch {
      body = String(rec.data ?? '')
    }
  }
  return <pre className="insar-preview-pre">{body || '（空 JSON）'}</pre>
}

function TextBody({ rec }: { rec: Record<string, unknown> }) {
  const text = asString(rec.text) ?? asString(rec.member_text) ?? ''
  const encoding = asString(rec.encoding)
  const nBytes = asNum(rec.n_bytes)
  const total = asNum(rec.n_bytes_total)
  const meta = [encoding, nBytes != null ? `${nBytes} 字节` : null, total != null ? `/ ${total}` : null]
    .filter(Boolean)
    .join(' · ')
  return (
    <>
      {meta ? <p className="insar-preview-meta">{meta}</p> : null}
      <pre className="insar-preview-pre">{text || '（空文件）'}</pre>
    </>
  )
}

function Hdf5Body({
  rec,
  onDataset,
}: {
  rec: Record<string, unknown>
  onDataset: (name: string) => void
}) {
  const datasets = Array.isArray(rec.datasets) ? rec.datasets : []
  const current = asString(rec.dataset)
  return (
    <>
      {datasets.length === 0 ? (
        <p className="insar-preview-note">未列出数据集</p>
      ) : (
        <ul className="insar-preview-list">
          {datasets.map((item, i) => {
            const row = asRecord(item)
            const path = row ? asString(row.path) ?? cellText(item) : cellText(item)
            const dtype = row ? asString(row.dtype) : null
            const shape = row ? shapeText(row.shape) : ''
            const label = [path, shape && `[${shape}]`, dtype].filter(Boolean).join(' ')
            const active = path === current
            return (
              <li key={`${path}-${i}`}>
                <button type="button" className={active ? 'insar-preview-link is-on' : 'insar-preview-link'} onClick={() => onDataset(path)}>
                  {label}
                </button>
              </li>
            )
          })}
        </ul>
      )}
    </>
  )
}

function Sparkline({ values }: { values: Array<number | null> }) {
  const nums = values.filter((v): v is number => v != null && Number.isFinite(v))
  if (nums.length < 2) return null
  const min = Math.min(...nums)
  const max = Math.max(...nums)
  const span = max - min || 1
  const w = 280
  const h = 72
  const pts = values
    .map((v, i) => {
      const x = (i / Math.max(values.length - 1, 1)) * w
      const y =
        v == null || !Number.isFinite(v) ? h / 2 : h - ((v - min) / span) * (h - 6) - 3
      return `${x},${y}`
    })
    .join(' ')
  return (
    <svg className="insar-preview-spark" viewBox={`0 0 ${w} ${h}`} aria-hidden>
      <polyline fill="none" stroke="currentColor" strokeWidth="1.5" points={pts} />
    </svg>
  )
}

function TimeseriesBody({ rec }: { rec: Record<string, unknown> }) {
  const dates = stringList(rec.dates)
  const nDates = asNum(rec.n_dates)
  const shape = shapeText(rec.shape)
  const values = Array.isArray(rec.values)
    ? rec.values.map((v) => (typeof v === 'number' && Number.isFinite(v) ? v : null))
    : []
  const sr = asNum(rec.sample_row)
  const sc = asNum(rec.sample_col)
  return (
    <>
      <p className="insar-preview-meta">
        {[
          nDates != null ? `${nDates} 个日期` : dates.length ? `${dates.length} 个日期` : null,
          shape && `shape ${shape}`,
          sr != null && sc != null ? `像元 (${sr}, ${sc})` : null,
        ]
          .filter(Boolean)
          .join(' · ') || '无日期列表'}
      </p>
      {values.length > 0 && <Sparkline values={values} />}
      {dates.length > 0 && (
        <ul className="insar-preview-list">
          {dates.map((d, i) => {
            const v = values[i]
            return (
              <li key={`${d}-${i}`}>
                {d}
                {v != null ? ` · ${v}` : ''}
              </li>
            )
          })}
        </ul>
      )}
    </>
  )
}

function ZipBody({
  rec,
  onMember,
}: {
  rec: Record<string, unknown>
  onMember: (name: string) => void
}) {
  const members = Array.isArray(rec.members) ? rec.members : []
  const total = asNum(rec.n_members_total)
  const current = asString(rec.member)
  return (
    <>
      <p className="insar-preview-meta">{total != null ? `共 ${total} 个条目` : `${members.length} 个条目`}</p>
      <ul className="insar-preview-list">
        {members.map((item, i) => {
          const row = asRecord(item)
          const name = row ? asString(row.name) ?? cellText(item) : cellText(item)
          const size = row ? asNum(row.size) : null
          const active = name === current
          return (
            <li key={`${name}-${i}`}>
              <button type="button" className={active ? 'insar-preview-link is-on' : 'insar-preview-link'} onClick={() => onMember(name)}>
                {size != null ? `${name} · ${size}` : name}
              </button>
            </li>
          )
        })}
      </ul>
    </>
  )
}

function PdfBody({ rec }: { rec: Record<string, unknown> }) {
  const pages = asNum(rec.pages)
  const page = asNum(rec.page)
  const title = asString(rec.title)
  return (
    <p className="insar-preview-note">
      {pages != null ? `${page ?? 1} / ${pages} 页` : '页数未知'}
      {title ? ` · ${title}` : ''}
    </p>
  )
}

function BinaryBody({ rec }: { rec: Record<string, unknown> }) {
  const suffix = asString(rec.suffix)
  const size = asNum(rec.size_bytes) ?? asNum(rec.size)
  const width = asNum(rec.width)
  const length = asNum(rec.length)
  const geom = asString(rec.geometry_source)
  const reason = asString(rec.reason)
  const parts = [
    suffix,
    width != null && length != null ? `${width}×${length}` : width != null ? `宽 ${width}` : null,
    geom,
    size != null ? `${size} 字节` : null,
    reason,
  ].filter(Boolean)
  return <p className="insar-preview-note">{parts.join(' · ') || '二进制干涉图，无结构预览'}</p>
}

function RasterMeta({ rec }: { rec: Record<string, unknown> }) {
  const parts = [
    asNum(rec.width) != null && asNum(rec.height) != null ? `${asNum(rec.width)}×${asNum(rec.height)}` : null,
    asString(rec.dtype),
    asNum(rec.band_count) != null ? `${asNum(rec.band_count)} 波段` : null,
    rec.crs == null ? null : `crs ${cellText(rec.crs)}`,
  ].filter(Boolean)
  if (!parts.length) return null
  return <p className="insar-preview-meta">{parts.join(' · ')}</p>
}

function KindBody({
  rec,
  onDataset,
  onMember,
}: {
  rec: Record<string, unknown>
  onDataset: (name: string) => void
  onMember: (name: string) => void
}) {
  const kind = asString(rec.kind) ?? 'unsupported'
  if (kind === 'table') return <TableBody rec={rec} />
  if (kind === 'json') return <JsonBody rec={rec} />
  if (kind === 'text') return <TextBody rec={rec} />
  if (kind === 'hdf5') return <Hdf5Body rec={rec} onDataset={onDataset} />
  if (kind === 'timeseries') return <TimeseriesBody rec={rec} />
  if (kind === 'zip') {
    return (
      <>
        {(asString(rec.member_text) || asString(rec.kml_text)) && (
          <pre className="insar-preview-pre">{asString(rec.member_text) || asString(rec.kml_text)}</pre>
        )}
        {Array.isArray(rec.columns) && <TableBody rec={rec} />}
        <ZipBody rec={rec} onMember={onMember} />
      </>
    )
  }
  if (kind === 'pdf') return <PdfBody rec={rec} />
  if (kind === 'binary_sar') return <BinaryBody rec={rec} />
  if (kind === 'raster') return <RasterMeta rec={rec} />
  if (kind === 'shp') {
    const parts = [asString(rec.note) || 'Shapefile', bboxLine(rec)].filter(Boolean)
    return <p className="insar-preview-note">{parts.join(' · ')}</p>
  }
  if (kind === 'unsupported') return null
  return (
    <pre className="insar-preview-pre">
      {(() => {
        try {
          return JSON.stringify(rec, null, 2)
        } catch {
          return String(kind)
        }
      })()}
    </pre>
  )
}

export function InsarPreviewDialog({
  base,
  session,
  runId,
  path,
  name,
  onClose,
}: InsarPreviewDialogProps) {
  const [status, setStatus] = useState<'loading' | 'ok' | 'error'>('loading')
  const [error, setError] = useState<string | null>(null)
  const [payload, setPayload] = useState<Record<string, unknown> | null>(null)
  const [sheet, setSheet] = useState<string | undefined>()
  const [offset, setOffset] = useState(0)
  const [byteOffset, setByteOffset] = useState(0)
  const [colOffset, setColOffset] = useState(0)
  const [dataset, setDataset] = useState<string | undefined>()
  const [slice, setSlice] = useState(0)
  const [member, setMember] = useState<string | undefined>()
  const [page, setPage] = useState(1)
  const [row, setRow] = useState<number | undefined>()
  const [col, setCol] = useState<number | undefined>()

  useEffect(() => {
    setSheet(undefined)
    setOffset(0)
    setByteOffset(0)
    setColOffset(0)
    setDataset(undefined)
    setSlice(0)
    setMember(undefined)
    setPage(1)
    setRow(undefined)
    setCol(undefined)
  }, [path])

  const blocked = !base.trim()
    ? '缺少 API 地址，无法预览'
    : !session?.trim()
      ? '缺少 session，无法预览'
      : !path.trim()
        ? '该文件没有工作区相对路径，无法预览'
        : null

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  useEffect(() => {
    if (blocked) {
      setStatus('error')
      setError(blocked)
      setPayload(null)
      return
    }
    const sessionId = session?.trim() ?? ''
    const ac = new AbortController()
    setError(null)
    setStatus((s) => (s === 'ok' ? 'ok' : 'loading'))
    const qs = new URLSearchParams()
    qs.set('session', sessionId)
    if (runId?.trim()) qs.set('run_id', runId.trim())
    qs.set('path', path)
    if (sheet) qs.set('sheet', sheet)
    const useBytes = isByteOffsetPath(path, member)
    const windowOff = useBytes ? byteOffset : offset
    if (windowOff) qs.set('offset', String(windowOff))
    if (colOffset) qs.set('col_offset', String(colOffset))
    if (dataset) qs.set('dataset', dataset)
    if (slice) qs.set('slice', String(slice))
    if (member) qs.set('member', member)
    if (page !== 1) qs.set('page', String(page))
    if (row != null && col != null) {
      qs.set('row', String(row))
      qs.set('col', String(col))
    }
    const url = joinUrl(base, `/api/preview?${qs.toString()}`)
    void (async () => {
      try {
        const res = await fetch(url, { signal: ac.signal })
        const body: unknown = await res.json().catch(() => null)
        if (ac.signal.aborted) return
        if (!res.ok) {
          setError(errorFromBody(res.status, body))
          setStatus('error')
          return
        }
        const rec = asRecord(body)
        if (!rec) {
          setError('响应不是 JSON')
          setStatus('error')
          return
        }
        setPayload(rec)
        setStatus('ok')
      } catch (e) {
        if (ac.signal.aborted) return
        const msg = e instanceof Error ? e.message : String(e)
        setError(msg || '无法连接预览接口')
        setStatus('error')
      }
    })()
    return () => ac.abort()
  }, [base, session, runId, path, blocked, sheet, offset, byteOffset, colOffset, dataset, slice, member, page, row, col])

  const kind = payload ? asString(payload.kind) : null
  const truncated = payload ? asBool(payload.truncated) : false
  const note = payload ? asString(payload.note) : null
  const hasImage = payload ? asBool(payload.has_image) : false
  const imageUrl = payload ? asString(payload.image_url) : null
  const title = payload ? asString(payload.name) || name : name
  const sheets = payload && Array.isArray(payload.sheets) ? payload.sheets.map(cellText) : []
  const nRows = payload ? asNum(payload.n_rows_total) : null
  const nCols = payload ? asNum(payload.n_cols_total) : null
  const limit = payload ? asNum(payload.limit) ?? 200 : 200
  const colLimit = payload ? asNum(payload.col_limit) ?? 40 : 40
  const pages = payload ? asNum(payload.pages) : null
  const sliceCount = (() => {
    if (!payload) return null
    if (kind === 'timeseries' && Array.isArray(payload.shape)) return asNum(payload.shape[0])
    if (kind !== 'hdf5' || !Array.isArray(payload.datasets)) return null
    const want = dataset || asString(payload.dataset)
    const hit = payload.datasets.map(asRecord).find((row) => row && asString(row.path) === want)
    const sh = hit && Array.isArray(hit.shape) ? hit.shape : []
    return sh.length === 3 ? asNum(sh[0]) : null
  })()
  const stats = payload ? asRecord(payload.stats) : null
  const attrs = payload ? asRecord(payload.attrs) : null
  const map = payload ? mapHw(payload.map) : null
  const jsonKeys = payload && Array.isArray(payload.keys) ? payload.keys.map(cellText).filter(Boolean) : []
  const tableLike = kind === 'table' || (kind === 'zip' && Array.isArray(payload?.columns))
  const textLike =
    kind === 'text' ||
    (kind === 'zip' && (asString(payload?.member_text) != null || asString(payload?.kml_text) != null) && asNum(payload?.n_bytes_total) != null)
  const bustedImage =
    imageUrl && payload
      ? `${imageUrl}${imageUrl.includes('?') ? '&' : '?'}_s=${asNum(payload.size) ?? 0}`
      : imageUrl

  return (
    <div
      className="insar-preview-dialog bg-background/95"
      role="dialog"
      aria-modal="true"
      aria-label={title}
      aria-busy={status === 'loading'}
      onClick={onClose}
    >
      <div className="insar-preview-panel bg-background" onClick={(e) => e.stopPropagation()}>
        <div className="insar-preview-head">
          <div className="insar-preview-title">
            <span className="truncate">{title}</span>
            {kind ? <span className="insar-preview-kind">{kind}</span> : null}
          </div>
          <button type="button" className="insar-preview-close" onClick={onClose}>
            关闭
          </button>
        </div>
        <div className="insar-preview-body">
          {status === 'loading' && <p className="insar-preview-note">加载预览…</p>}
          {status === 'error' && <p className="insar-preview-error">{error}</p>}
          {status === 'ok' && payload && (
            <>
              {kind !== 'table' && <TruncBanner truncated={truncated} note={note} />}
              {kind === 'unsupported' && !note && (
                <p className="insar-preview-note">该格式暂无预览器</p>
              )}
              <ChipBar
                items={sheets}
                current={asString(payload.sheet)}
                onPick={(v) => {
                  setSheet(v)
                  setOffset(0)
                  setColOffset(0)
                }}
              />
              {kind === 'json' && jsonKeys.length > 0 && (
                <p className="insar-preview-meta">键 {jsonKeys.join(' · ')}</p>
              )}
              {tableLike && nRows != null && (
                <Pager
                  label="页行"
                  disablePrev={offset <= 0}
                  disableNext={offset + limit >= nRows}
                  onPrev={() => setOffset(Math.max(0, offset - limit))}
                  onNext={() => setOffset(offset + limit)}
                />
              )}
              {tableLike && nCols != null && nCols > colLimit && (
                <Pager
                  label="页列"
                  disablePrev={colOffset <= 0}
                  disableNext={colOffset + colLimit >= nCols}
                  onPrev={() => setColOffset(Math.max(0, colOffset - colLimit))}
                  onNext={() => setColOffset(colOffset + colLimit)}
                />
              )}
              {textLike && asNum(payload.n_bytes_total) != null && (
                <Pager
                  label="段"
                  disablePrev={byteOffset <= 0}
                  disableNext={
                    (asNum(payload.offset) ?? byteOffset) + (asNum(payload.n_bytes) ?? 0) >=
                    (asNum(payload.n_bytes_total) ?? 0)
                  }
                  onPrev={() => setByteOffset(Math.max(0, byteOffset - (asNum(payload.n_bytes) || 256 * 1024)))}
                  onNext={() => setByteOffset(byteOffset + (asNum(payload.n_bytes) || 256 * 1024))}
                />
              )}
              {kind === 'pdf' && pages != null && pages > 1 && (
                <Pager
                  label="页"
                  disablePrev={page <= 1}
                  disableNext={page >= pages}
                  onPrev={() => setPage(Math.max(1, page - 1))}
                  onNext={() => setPage(page + 1)}
                />
              )}
              {(kind === 'hdf5' || kind === 'timeseries') && sliceCount != null && sliceCount > 1 && (
                <Pager
                  label="切片"
                  disablePrev={slice <= 0}
                  disableNext={slice + 1 >= sliceCount}
                  onPrev={() => setSlice(Math.max(0, slice - 1))}
                  onNext={() => setSlice(slice + 1)}
                />
              )}
              {stats ? <StatChips stats={stats} /> : null}
              {hasImage && bustedImage ? (
                <PreviewImage
                  base={base}
                  url={bustedImage}
                  map={map}
                  pickable={kind === 'timeseries'}
                  onPick={(r, c) => {
                    setRow(r)
                    setCol(c)
                  }}
                />
              ) : null}
              <SampleLine row={sampleRow} col={sampleCol} />
              {attrs ? <AttrsBlock attrs={attrs} /> : null}
              <KindBody
                rec={payload}
                onDataset={(name) => {
                  setDataset(name)
                  setSlice(0)
                }}
                onMember={(name) => {
                  setMember(name)
                  setOffset(0)
                  setByteOffset(0)
                  setColOffset(0)
                  setPage(1)
                  setSlice(0)
                  setRow(undefined)
                  setCol(undefined)
                }}
              />
            </>
          )}
        </div>
      </div>
    </div>
  )
}
