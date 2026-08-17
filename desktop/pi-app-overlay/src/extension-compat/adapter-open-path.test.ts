import { describe, expect, it } from 'vitest'
import { resolveAdapterOpenPath } from './adapter-open-path'

describe('resolveAdapterOpenPath', () => {
  it('joins relative paths to the workspace cwd', () => {
    const r = resolveAdapterOpenPath('workspace/sessions', 'E:/repo')
    expect(r.ok).toBe(true)
    if (r.ok) expect(r.abs.replace(/\\/g, '/')).toMatch(/E:\/repo\/workspace\/sessions$/i)
  })

  it('rejects http(s) URLs', () => {
    expect(resolveAdapterOpenPath('https://example.test/x', 'E:/repo')).toEqual({
      ok: false,
      error: 'not a filesystem path',
    })
  })

  it('rejects relative paths without a workspace', () => {
    expect(resolveAdapterOpenPath('workspace', '')).toEqual({ ok: false, error: 'no workspace' })
  })

  it('keeps Windows drive-letter paths', () => {
    const r = resolveAdapterOpenPath('E:\\data\\insar-home', 'E:/repo')
    expect(r.ok).toBe(true)
    if (r.ok) expect(r.abs.replace(/\\/g, '/').toLowerCase()).toContain('data/insar-home')
  })
})
