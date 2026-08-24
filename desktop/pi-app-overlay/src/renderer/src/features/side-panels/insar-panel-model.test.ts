import { describe, expect, it } from 'vitest'
import {
  coerceFile,
  coerceFigures,
  coerceKind,
  inferAccessMode,
  presentQaNumber,
  type MonitorStep,
} from './insar-panel-model'

function step(n: number, state = 'done'): MonitorStep {
  return {
    step: n,
    name: `s${n}`,
    method: null,
    state,
    stage: '',
    stage_letter: '',
    stale: false,
    stale_reason: null,
    failure_class: null,
    duration: null,
    attempts: 1,
  }
}

describe('coerceKind', () => {
  it('keeps existing kinds', () => {
    expect(coerceKind('hyp3')).toBe('hyp3')
    expect(coerceKind('alos_raw')).toBe('alos_raw')
    expect(coerceKind('slc_stack')).toBe('slc_stack')
    expect(coerceKind('dem')).toBe('dem')
    expect(coerceKind('unknown')).toBe('unknown')
  })

  it('accepts nisar, gamma, and displacement', () => {
    expect(coerceKind('nisar')).toBe('nisar')
    expect(coerceKind('gamma')).toBe('gamma')
    expect(coerceKind('displacement')).toBe('displacement')
  })

  it('falls back to unknown for missing or unsupported values', () => {
    expect(coerceKind('NISAR')).toBe('unknown')
    expect(coerceKind('foo')).toBe('unknown')
    expect(coerceKind(null)).toBe('unknown')
    expect(coerceKind(undefined)).toBe('unknown')
    expect(coerceKind(1)).toBe('unknown')
  })
})

describe('coerceFigures', () => {
  it('defaults files to [] and truncated to false when omitted', () => {
    const out = coerceFigures({ run: 'r1', figures: [{ name: 'velocity.png', step: 10 }] })
    expect(out.error).toBeUndefined()
    expect(out.run).toBe('r1')
    expect(out.figures).toHaveLength(1)
    expect(out.figures[0]?.name).toBe('velocity.png')
    expect(out.files).toEqual([])
    expect(out.truncated).toBe(false)
  })

  it('coerces files and truncated', () => {
    const out = coerceFigures({
      run: 'r1',
      figures: [{ name: 'velocity.png' }],
      truncated: true,
      files: [
        { step: 23, name: 'report.pdf', size: 2048, kind: 'pdf', path: 'products/report.pdf' },
        { step: 24, name: 'scene.kmz', size: 4096, kind: 'kmz' },
      ],
    })
    expect(out.truncated).toBe(true)
    expect(out.files).toEqual([
      {
        step: 23,
        name: 'report.pdf',
        size: 2048,
        kind: 'pdf',
        path: 'products/report.pdf',
        viewers: [],
      },
      { step: 24, name: 'scene.kmz', size: 4096, kind: 'kmz', path: undefined, viewers: [] },
    ])
  })

  it('skips bad figure and file rows', () => {
    const out = coerceFigures({
      figures: [null, {}, { name: 'ok.png' }, { step: 1 }],
      files: [null, 'x', {}, { name: 'keep.h5', step: 20 }, { size: 12 }],
      truncated: false,
    })
    expect(out.figures.map((f) => f.name)).toEqual(['ok.png'])
    expect(out.files.map((f) => f.name)).toEqual(['keep.h5'])
    expect(out.files[0]?.step).toBe(20)
    expect(out.files[0]?.viewers).toEqual([])
  })

  it('returns empty files when the payload is invalid', () => {
    expect(coerceFigures(null)).toEqual({
      run: null,
      figures: [],
      files: [],
      truncated: false,
      error: 'invalid_figures',
    })
  })
})

describe('coerceFile', () => {
  it('keeps a reserved viewer on offset-class files', () => {
    const row = coerceFile({
      step: 20,
      name: 'azimuthOffset.h5',
      kind: 'h5',
      viewers: [
        {
          id: 'offset-timeseries',
          status: 'reserved',
          title: '偏移时间序列',
          render: 'none',
        },
      ],
    })
    expect(row).not.toBeNull()
    expect(row?.viewers).toEqual([
      {
        id: 'offset-timeseries',
        status: 'reserved',
        title: '偏移时间序列',
        render: 'none',
      },
    ])
  })

  it('defaults missing viewers to [] without throwing', () => {
    expect(() => coerceFile({ name: 'keep.h5', step: 20 })).not.toThrow()
    expect(coerceFile({ name: 'keep.h5', step: 20 })?.viewers).toEqual([])
    expect(coerceFile({ name: 'keep.h5', viewers: undefined })?.viewers).toEqual([])
  })

  it('skips bad viewer elements and unknown status', () => {
    const row = coerceFile({
      name: 'timeseries.h5',
      viewers: [
        null,
        'x',
        {},
        { id: 'bad', status: 'pending', title: '坏状态', render: 'none' },
        { status: 'ready', title: '缺 id', render: 'external' },
        {
          id: 'point-timeseries',
          status: 'ready',
          title: '点位时序',
          render: 'timeseries_point',
        },
      ],
    })
    expect(row?.viewers).toEqual([
      {
        id: 'point-timeseries',
        status: 'ready',
        title: '点位时序',
        render: 'timeseries_point',
      },
    ])
  })
})

describe('inferAccessMode', () => {
  it('returns null when there are no steps', () => {
    expect(inferAccessMode([])).toBeNull()
  })

  it('returns C when analysis steps exist and 1–11 do not', () => {
    expect(inferAccessMode([step(20), step(23), step(28)])).toBe('C')
  })

  it('returns B when steps 2–6 all exist and are skipped', () => {
    const steps = [
      step(1, 'done'),
      step(2, 'skipped'),
      step(3, 'skipped'),
      step(4, 'skipped'),
      step(5, 'skipped'),
      step(6, 'skipped'),
      step(7, 'done'),
      step(20, 'pending'),
    ]
    expect(inferAccessMode(steps)).toBe('B')
  })

  it('returns A when 1–11 are present and 2–6 are not all skipped', () => {
    const steps = [step(1), step(2), step(7), step(11)]
    expect(inferAccessMode(steps)).toBe('A')
  })

  it('does not treat incomplete 2–6 skipped as B', () => {
    expect(
      inferAccessMode([
        step(2, 'skipped'),
        step(3, 'skipped'),
        step(4, 'skipped'),
        step(5, 'skipped'),
        step(7, 'done'),
      ]),
    ).toBe('A')
  })

  it('returns null when steps are neither core nor analysis', () => {
    expect(inferAccessMode([step(15, 'pending')])).toBeNull()
  })
})

describe('presentQaNumber', () => {
  it('keeps zero and drops only null/non-finite', () => {
    expect(presentQaNumber(0)).toBe(0)
    expect(presentQaNumber(0.92)).toBe(0.92)
    expect(presentQaNumber(null)).toBeNull()
    expect(presentQaNumber(Number.NaN)).toBeNull()
  })
})
