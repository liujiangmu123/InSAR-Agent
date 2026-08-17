import { describe, expect, it } from 'vitest'
import {
  extractImagesFromToolOutput,
  extractTextFromToolOutput,
  mergeImagesIntoDetails,
  TOOL_IMAGE_MAX_BYTES,
} from './json-path'

describe('extractImagesFromToolOutput', () => {
  it('keeps text and extracts image content blocks', () => {
    const output = {
      content: [
        { type: 'image', data: 'AQID', mimeType: 'image/png', name: 'velocity.png' },
        { type: 'text', text: 'step 10 · velocity.png' },
      ],
      details: { name: 'velocity.png' },
    }
    expect(extractTextFromToolOutput(output)).toBe('step 10 · velocity.png')
    expect(extractImagesFromToolOutput(output)).toEqual([
      {
        dataUrl: 'data:image/png;base64,AQID',
        mimeType: 'image/png',
        name: 'velocity.png',
      },
    ])
  })

  it('skips oversized and remote payloads', () => {
    const huge = 'A'.repeat(Math.floor((TOOL_IMAGE_MAX_BYTES * 4) / 3) + 8)
    expect(extractImagesFromToolOutput({ content: [{ type: 'image', data: huge, mimeType: 'image/png' }] })).toEqual([])
    expect(
      extractImagesFromToolOutput({
        content: [{ type: 'image', data: 'https://example.test/a.png', mimeType: 'image/png' }],
      }),
    ).toEqual([])
  })

  it('merges images onto details without mutating the original', () => {
    const details = { name: 'velocity.png', step: 10 }
    const merged = mergeImagesIntoDetails(details, {
      content: [{ type: 'image', data: 'AQID', mimeType: 'image/png' }],
    }) as { name: string; images: unknown[] }
    expect(details).toEqual({ name: 'velocity.png', step: 10 })
    expect(merged.name).toBe('velocity.png')
    expect(merged.images).toHaveLength(1)
  })
})
