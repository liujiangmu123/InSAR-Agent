/** Shared JSONPath-lite + tool-card status extraction (Worker + Renderer). */

export function extractJsonPath(obj: unknown, path: string): unknown {
  if (!obj || typeof path !== 'string') return undefined
  const parts = path.replace(/^\$\.?/, '').split('.').filter(Boolean)
  let cur: unknown = obj
  for (const p of parts) {
    if (cur == null || typeof cur !== 'object') return undefined
    cur = (cur as Record<string, unknown>)[p]
  }
  return cur
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return v !== null && typeof v === 'object' && !Array.isArray(v)
}

function contentBlocks(output: unknown): Array<Record<string, unknown>> {
  if (Array.isArray(output)) return output.filter(isRecord)
  if (!isRecord(output)) return []
  return Array.isArray(output.content) ? output.content.filter(isRecord) : []
}

/** Convert a Pi tool result/update envelope into readable text. */
export function extractTextFromToolOutput(output: unknown): string {
  if (!output) return ''
  if (typeof output === 'string') return output
  const blocks = contentBlocks(output)
  if (blocks.length > 0) {
    return blocks.map((c) => (typeof c.text === 'string' ? c.text : '')).join('')
  }
  if (typeof (output as { text?: string }).text === 'string') return (output as { text: string }).text
  return ''
}

/** Image content blocks from a Pi tool result. Caps decoded size at 8 MiB per image. */
export const TOOL_IMAGE_MAX_BYTES = 8 * 1024 * 1024

export type ToolImageAsset = { dataUrl: string; mimeType: string; name?: string }

export function extractImagesFromToolOutput(output: unknown): ToolImageAsset[] {
  const out: ToolImageAsset[] = []
  for (const block of contentBlocks(output)) {
    if (block.type !== 'image') continue
    const mime =
      (typeof block.mimeType === 'string' && block.mimeType) ||
      (typeof block.mediaType === 'string' && block.mediaType) ||
      'image/png'
    const data = typeof block.data === 'string' ? block.data.replace(/\s+/g, '') : ''
    if (!data || data.startsWith('http:') || data.startsWith('https:')) continue
    const decoded = Math.floor((data.length * 3) / 4)
    if (decoded > TOOL_IMAGE_MAX_BYTES) continue
    const name = typeof block.name === 'string' ? block.name : undefined
    out.push({ dataUrl: `data:${mime};base64,${data}`, mimeType: mime, ...(name ? { name } : {}) })
  }
  return out
}

/** Copy details and attach extracted image assets under `images` (for media tool cards). */
export function mergeImagesIntoDetails(details: unknown, output: unknown): unknown {
  const images = extractImagesFromToolOutput(output)
  if (images.length === 0) return details
  const base: Record<string, unknown> = isRecord(details) ? { ...details } : {}
  const prev = Array.isArray(base.images) ? [...(base.images as unknown[])] : []
  base.images = [...prev, ...images]
  return base
}

/** Resolve status line for tool updates using adapter.toolCard.statusField when set. */
export function extractStatusFromOutput(output: unknown, statusField?: string): string | null {
  let text = ''
  if (statusField) {
    let root: unknown = output
    if (typeof output === 'string' && statusField.startsWith('$.')) {
      try {
        root = JSON.parse(output)
      } catch (e) {
        root = { text: output }
      }
    }
    const v = extractJsonPath(root, statusField)
    if (v != null && String(v).trim()) text = String(v).trim()
  }
  if (!text) text = extractTextFromToolOutput(output).trim()
  if (!text) return null
  if (text.length > 120) return `${text.slice(0, 120)}…`
  return text
}

/** Apply toolCard.fields mappings from tool args / details / output. */
export function applyToolCardFields(
  sources: { args?: unknown; details?: unknown; output?: unknown },
  fields?: Record<string, string>,
): Record<string, unknown> {
  if (!fields) return {}
  const out: Record<string, unknown> = {}
  for (const [key, path] of Object.entries(fields)) {
    if (path.startsWith('$.args.')) {
      out[key] = extractJsonPath(sources.args, `$.${path.slice('$.args.'.length)}`)
    } else if (path.startsWith('$.details.')) {
      out[key] = extractJsonPath(sources.details, `$.${path.slice('$.details.'.length)}`)
    } else if (path.startsWith('$.output.')) {
      let o = sources.output
      if (typeof o === 'string') {
        try {
          o = JSON.parse(o)
        } catch (e) {
          o = { text: o }
        }
      }
      out[key] = extractJsonPath(o, `$.${path.slice('$.output.'.length)}`)
    } else {
      out[key] =
        extractJsonPath(sources.args, path)
        ?? extractJsonPath(sources.details, path)
        ?? extractJsonPath(sources.output, path)
    }
  }
  return out
}
