import { formatBytes, type DatasetsPayload, type DatasetKind, type DatasetRow } from './insar-panel-model'

const KIND_LABEL: Record<DatasetKind, string> = {
  hyp3: 'HyP3',
  alos_raw: 'ALOS raw',
  slc_stack: 'SLC',
  dem: 'DEM',
  nisar: 'NISAR',
  gamma: 'GAMMA',
  displacement: '位移产品',
  unknown: '未知',
}

function detailPart(detail: Record<string, unknown>, key: string, label: string): string {
  if (!(key in detail) || detail[key] == null) return ''
  return `${label} ${detail[key]}`
}

function detailLine(kind: DatasetKind, detail: Record<string, unknown>): string {
  if (kind === 'hyp3') return `${detail.pairs ?? '?'} 对 · unw ${detail.unw ?? '—'} · corr ${detail.corr ?? '—'}`
  if (kind === 'alos_raw') return `${detail.scenes ?? '?'} 景 · IMG ${detail.img ?? '—'} · LED ${detail.led ?? '—'}`
  if (kind === 'slc_stack') return `SLC ${detail.slc ?? '—'} · SAFE ${detail.safe ?? '—'}`
  if (kind === 'dem') return '高程'
  if (kind === 'nisar') return detailPart(detail, 'gunw', 'GUNW')
  if (kind === 'gamma') return detailPart(detail, 'par', 'par')
  if (kind === 'displacement') {
    return [detailPart(detail, 'tif', 'tif'), detailPart(detail, 'csv', 'csv')].filter(Boolean).join(' · ')
  }
  return ''
}

function looksUnidentifiedSource(d: DatasetRow): boolean {
  if (d.kind !== 'unknown') return false
  if (d.file_count > 0) return true
  const hay = `${d.name} ${d.path}`.toLowerCase()
  return hay.includes('.tif') || hay.includes('.csv')
}

export function InsarDatasetsSection({ data }: { data: DatasetsPayload | null }) {
  if (!data) {
    return <div className="p-3 text-[11px] text-muted-foreground/70">供数待接线（适配器尚未切到 insar-read）</div>
  }
  if (data.error) {
    return (
      <div className="p-3 text-[11px] text-destructive/80">
        数据集不可用 · {data.error}
      </div>
    )
  }
  if (data.datasets.length === 0) {
    return (
      <div className="p-3 text-[11px] leading-relaxed text-muted-foreground/70">
        未发现数据集（检查 INSAR_DATA_DIR / home/datasets）
      </div>
    )
  }
  const scanned =
    data.scanned_at && Number.isFinite(data.scanned_at)
      ? new Date(data.scanned_at < 1e12 ? data.scanned_at * 1000 : data.scanned_at).toLocaleString()
      : null
  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-3 py-2 space-y-2">
      {data.datasets.map((d) => {
        const detail = detailLine(d.kind, d.detail)
        const unidentified = looksUnidentifiedSource(d)
        return (
          <div key={d.id} className="rounded-md border border-border/40 px-2 py-1.5">
            <div className="flex items-center justify-between gap-2">
              <span className="truncate text-[12px] text-foreground/90">{d.name}</span>
              <span className={cnKind(d.kind)}>{KIND_LABEL[d.kind]}</span>
            </div>
            <div className="mt-0.5 font-mono text-[10px] text-muted-foreground">
              {formatBytes(d.size_bytes)} · {d.file_count} 文件
              {d.date_range ? ` · ${d.date_range.start}…${d.date_range.end}` : ''}
            </div>
            {detail ? <div className="mt-0.5 text-[10px] text-muted-foreground/80">{detail}</div> : null}
            {unidentified ? (
              <div className="mt-0.5 text-[10px] leading-relaxed text-muted-foreground/70">
                未识别。对话中可用 register_sources 作为模式 C 源;不支持的格式不会静默当空。
              </div>
            ) : null}
          </div>
        )
      })}
      <div className="pt-1 font-mono text-[10px] text-muted-foreground/50">
        扫描根 {data.roots.length}
        {scanned ? ` · ${scanned}` : ''}
      </div>
    </div>
  )
}

function cnKind(kind: DatasetKind): string {
  return `insar-kind ${kind}`
}
