import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useUIStore } from '@renderer/stores/ui-store'

const invokeMock = vi.hoisted(() => vi.fn(async () => ({})))
const activateMock = vi.hoisted(() => vi.fn(async () => {}))
const refreshMock = vi.hoisted(() => vi.fn(async () => {}))

vi.mock('@renderer/lib/ipc-client', () => ({
  ipcClient: { invoke: (...args: unknown[]) => invokeMock(...args) },
}))
vi.mock('@renderer/lib/activate-workspace', () => ({
  activateWorkspace: (...args: unknown[]) => activateMock(...args),
}))
vi.mock('@renderer/lib/composer-run-display', () => ({
  refreshComposerRunDisplay: (...args: unknown[]) => refreshMock(...args),
}))

import {
  ensureWorkspaceWorkerOnBoot,
  resetEnsureWorkspaceWorkerOnBootForTests,
} from './ensure-workspace-worker'

describe('ensureWorkspaceWorkerOnBoot', () => {
  beforeEach(() => {
    resetEnsureWorkspaceWorkerOnBootForTests()
    invokeMock.mockReset()
    activateMock.mockReset()
    refreshMock.mockReset()
    useUIStore.setState({
      currentWorkspace: null,
      ephemeralSandboxDraft: false,
    })
  })

  it('opens the persisted disk project and starts a worker instead of wiping currentProject', async () => {
    invokeMock.mockImplementation(async (method: string) => {
      if (method === 'settings.get') return { settings: { currentProject: 'E:/repo' } }
      return {}
    })

    await ensureWorkspaceWorkerOnBoot()

    expect(activateMock).toHaveBeenCalledWith('E:/repo')
    expect(invokeMock).toHaveBeenCalledWith('workspace.ensureWorker', { path: 'E:/repo' })
    expect(invokeMock).not.toHaveBeenCalledWith(
      'settings.set',
      expect.objectContaining({ key: 'currentProject', value: null }),
    )
    expect(useUIStore.getState().ephemeralSandboxDraft).toBe(false)
  })

  it('enters an ephemeral draft when settings has no disk project', async () => {
    invokeMock.mockImplementation(async (method: string) => {
      if (method === 'settings.get') return { settings: { currentProject: null } }
      return {}
    })

    await ensureWorkspaceWorkerOnBoot()

    expect(activateMock).not.toHaveBeenCalled()
    expect(useUIStore.getState().ephemeralSandboxDraft).toBe(true)
    expect(invokeMock).toHaveBeenCalledWith('settings.set', { key: 'currentProject', value: null })
  })

  it('does not wipe a disk currentProject when the UI store still points at a sandbox', async () => {
    useUIStore.setState({
      currentWorkspace: 'C:/Users/T/AppData/Roaming/pi-desktop/sandbox-workspaces/abc',
    })
    invokeMock.mockImplementation(async (method: string) => {
      if (method === 'settings.get') return { settings: { currentProject: 'E:/repo' } }
      return {}
    })

    await ensureWorkspaceWorkerOnBoot()

    expect(activateMock).toHaveBeenCalledWith('E:/repo')
    expect(invokeMock).not.toHaveBeenCalledWith(
      'settings.set',
      expect.objectContaining({ key: 'currentProject', value: null }),
    )
  })
})
