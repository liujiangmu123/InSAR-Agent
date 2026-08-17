import { describe, expect, it, vi } from 'vitest'

vi.mock('./config-store', () => ({
  configStore: { get: () => null, set: () => {}, addRecentProject: () => {} },
}))

import { pickStartupWorkspace } from './startup-workspace'

const exists = (p: string) =>
  p === 'E:/repo' || p === 'D:/other' || p === 'C:/Users/T/AppData/Roaming/pi-desktop/sandbox-workspaces/abc'

describe('pickStartupWorkspace', () => {
  it('prefers INSAR_DESKTOP_PROJECT over a null currentProject', () => {
    expect(
      pickStartupWorkspace({
        envProject: 'E:/repo',
        currentProject: null,
        recentProjects: [],
        pathExists: exists,
      }),
    ).toEqual({ path: 'E:/repo', persist: true, addRecent: true })
  })

  it('prefers INSAR_DESKTOP_PROJECT over a leftover sandbox currentProject', () => {
    expect(
      pickStartupWorkspace({
        envProject: 'E:/repo',
        currentProject: 'C:/Users/T/AppData/Roaming/pi-desktop/sandbox-workspaces/abc',
        recentProjects: ['D:/other'],
        pathExists: exists,
      }),
    ).toEqual({ path: 'E:/repo', persist: true, addRecent: true })
  })

  it('keeps a valid disk currentProject when env is empty', () => {
    expect(
      pickStartupWorkspace({
        envProject: '',
        currentProject: 'D:/other',
        recentProjects: ['E:/repo'],
        pathExists: exists,
      }),
    ).toEqual({ path: 'D:/other', persist: false, addRecent: false })
  })

  it('falls back to the first existing disk recent when currentProject is null', () => {
    expect(
      pickStartupWorkspace({
        envProject: null,
        currentProject: null,
        recentProjects: [
          'C:/Users/T/AppData/Roaming/pi-desktop/sandbox-workspaces/abc',
          'missing',
          'E:/repo',
        ],
        pathExists: exists,
      }),
    ).toEqual({ path: 'E:/repo', persist: true, addRecent: false })
  })

  it('returns null when nothing on disk is a real project', () => {
    expect(
      pickStartupWorkspace({
        envProject: 'missing',
        currentProject: null,
        recentProjects: ['C:/Users/T/AppData/Roaming/pi-desktop/sandbox-workspaces/abc'],
        pathExists: exists,
      }),
    ).toEqual({ path: null, persist: false, addRecent: false })
  })
})
