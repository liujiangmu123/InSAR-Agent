import { describe, expect, it } from 'vitest'
import { pickPersistedWorkspace, resolveBootWorkspaceState } from './boot-workspace-state'

describe('pickPersistedWorkspace', () => {
  it('prefers a disk settings project when the UI store is empty', () => {
    expect(pickPersistedWorkspace(null, 'E:/repo')).toBe('E:/repo')
  })

  it('prefers a disk settings project over a leftover sandbox UI path', () => {
    expect(
      pickPersistedWorkspace(
        'C:/Users/T/AppData/Roaming/pi-desktop/sandbox-workspaces/abc',
        'E:/repo',
      ),
    ).toBe('E:/repo')
  })

  it('keeps a disk UI workspace when settings is null', () => {
    expect(pickPersistedWorkspace('E:/repo', null)).toBe('E:/repo')
  })
})

describe('resolveBootWorkspaceState', () => {
  it('opens a disk project and asks the worker to start', () => {
    expect(resolveBootWorkspaceState('E:/repo')).toEqual({
      workspace: 'E:/repo',
      ephemeralDraft: false,
      shouldStartWorker: true,
    })
  })

  it('treats a missing or sandbox path as an ephemeral draft', () => {
    expect(resolveBootWorkspaceState(null).ephemeralDraft).toBe(true)
    expect(
      resolveBootWorkspaceState('C:/Users/T/AppData/Roaming/pi-desktop/sandbox-workspaces/abc')
        .ephemeralDraft,
    ).toBe(true)
  })
})
