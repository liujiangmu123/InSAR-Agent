import http from 'node:http'
import { AddressInfo } from 'node:net'
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('../extension-compat/adapter-loader', () => ({ findAdapterById: () => null }))
vi.mock('../extension-compat/adapter-backend', () => ({ readAdapterConfig: () => ({}) }))
vi.mock('./workspace-task-panel-reader', () => ({ readWorkspaceTaskPanelState: () => ({}) }))

import { fetchInsarReadState } from './side-panel-registry'

type RouteFn = (url: URL) => { status: number; body: unknown }

function startServer(route: RouteFn): Promise<{ base: string; close: () => Promise<void> }> {
  const server = http.createServer((req, res) => {
    const host = req.headers.host || '127.0.0.1'
    const url = new URL(req.url || '/', `http://${host}`)
    const out = route(url)
    const payload = JSON.stringify(out.body)
    res.writeHead(out.status, { 'Content-Type': 'application/json' })
    res.end(payload)
  })
  return new Promise((resolve, reject) => {
    server.listen(0, '127.0.0.1', () => {
      const addr = server.address() as AddressInfo
      resolve({
        base: `http://127.0.0.1:${addr.port}`,
        close: () =>
          new Promise((done, fail) => {
            server.close((err) => (err ? fail(err) : done()))
          }),
      })
    })
    server.on('error', reject)
  })
}

describe('fetchInsarReadState', () => {
  let close: (() => Promise<void>) | null = null
  afterEach(async () => {
    if (close) {
      await close()
      close = null
    }
  })

  it('merges monitor + datasets + figures on success', async () => {
    const svc = await startServer((url) => {
      if (url.pathname === '/api/monitor') {
        return {
          status: 200,
          body: {
            session: 'real',
            run: { run_id: 'run-1', status: 'done', simulated: false, scenario: 'quake' },
            steps: [],
          },
        }
      }
      if (url.pathname === '/api/datasets') {
        return { status: 200, body: { roots: ['/data'], datasets: [{ id: 'd1', kind: 'hyp3' }] } }
      }
      if (url.pathname === '/api/figures') {
        expect(url.searchParams.get('session')).toBe('real')
        expect(url.searchParams.get('run_id')).toBe('run-1')
        return { status: 200, body: { run: 'run-1', figures: [{ name: 'velocity.png' }] } }
      }
      return { status: 404, body: { error: 'nope' } }
    })
    close = svc.close
    const state = (await fetchInsarReadState(svc.base, '@latest')) as Record<string, unknown>
    expect(state.v).toBe(2)
    expect(state.base).toBe(svc.base)
    expect((state.monitor as { session: string }).session).toBe('real')
    expect((state.datasets as { datasets: unknown[] }).datasets).toHaveLength(1)
    expect((state.figures as { figures: unknown[] }).figures).toHaveLength(1)
  })

  it('returns ok:false when monitor is down', async () => {
    const svc = await startServer((url) => {
      if (url.pathname === '/api/monitor') return { status: 503, body: { error: 'down' } }
      if (url.pathname === '/api/datasets') return { status: 200, body: { datasets: [] } }
      return { status: 404, body: {} }
    })
    close = svc.close
    const state = (await fetchInsarReadState(svc.base, '@latest')) as Record<string, unknown>
    expect(state.ok).toBe(false)
    expect(String(state.error)).toContain('HTTP 503')
    expect(state.v).toBeUndefined()
  })

  it('keeps monitor when datasets fail', async () => {
    const svc = await startServer((url) => {
      if (url.pathname === '/api/monitor') {
        return { status: 200, body: { session: 's1', run: null, steps: [] } }
      }
      if (url.pathname === '/api/datasets') return { status: 500, body: { error: 'scan' } }
      return { status: 404, body: {} }
    })
    close = svc.close
    const state = (await fetchInsarReadState(svc.base, 's1')) as Record<string, unknown>
    expect(state.v).toBe(2)
    expect((state.monitor as { session: string }).session).toBe('s1')
    expect((state.datasets as { error: string }).error).toBe('HTTP 500')
    expect(state.figures).toEqual({ run: null, figures: [] })
  })

  it('skips figures when there is no run', async () => {
    let figuresHits = 0
    const svc = await startServer((url) => {
      if (url.pathname === '/api/monitor') {
        return { status: 200, body: { session: 'empty', run: null, steps: [] } }
      }
      if (url.pathname === '/api/datasets') return { status: 200, body: { datasets: [] } }
      if (url.pathname === '/api/figures') {
        figuresHits += 1
        return { status: 200, body: { figures: ['should-not'] } }
      }
      return { status: 404, body: {} }
    })
    close = svc.close
    const state = (await fetchInsarReadState(svc.base, '@latest')) as Record<string, unknown>
    expect(state.v).toBe(2)
    expect(state.figures).toEqual({ run: null, figures: [] })
    expect(figuresHits).toBe(0)
  })
})
