import { ipcClient } from '@renderer/lib/ipc-client'
import { useUIStore } from '@renderer/stores/ui-store'
import { refreshComposerRunDisplay } from '@renderer/lib/composer-run-display'
import { activateWorkspace } from '@renderer/lib/activate-workspace'
import {
  isSandboxWorkspacePath,
  pickPersistedWorkspace,
  resolveBootWorkspaceState,
} from '@renderer/lib/boot-workspace-state'

let bootstrapping: Promise<void> | null = null

export function resetEnsureWorkspaceWorkerOnBootForTests(): void {
  bootstrapping = null
}

/**
 * Application first-paint boot (called once on Renderer mount):
 * 1. Read UI currentWorkspace (usually null; not persisted) plus settings.currentProject
 * 2. Disk project → activate workspace and start Worker so .pi extensions / insar-llm load
 * 3. No project/sandbox → ephemeral draft home (do not wipe a disk currentProject)
 */
export function ensureWorkspaceWorkerOnBoot(): Promise<void> {
  if (bootstrapping) return bootstrapping
  bootstrapping = (async () => {
    let settingsProject: string | null = null
    try {
      const res = await ipcClient.invoke('settings.get', { key: 'currentProject' })
      const raw = res?.settings?.currentProject
      settingsProject = typeof raw === 'string' && raw.trim() ? raw.trim() : null
    } catch {
      settingsProject = null
    }
    const persisted = pickPersistedWorkspace(useUIStore.getState().currentWorkspace, settingsProject)
    const boot = resolveBootWorkspaceState(persisted)
    if (boot.ephemeralDraft) {
      const settingsIsDisk = !!settingsProject && !isSandboxWorkspacePath(settingsProject)
      if (!settingsIsDisk) {
        void ipcClient.invoke('settings.set', { key: 'currentProject', value: null }).catch(() => {})
      }
      useUIStore.getState().enterEphemeralSandboxDraft()
      queueMicrotask(() => void refreshComposerRunDisplay())
      return
    }
    const path = boot.workspace
    if (!path) return
    await activateWorkspace(path)
    if (boot.shouldStartWorker) {
      try {
        await ipcClient.invoke('workspace.ensureWorker', { path })
      } catch (error) {
        console.error('[ensureWorkspaceWorkerOnBoot] ensureWorker', error)
      }
    }
    try {
      await refreshComposerRunDisplay()
    } catch (error) {
      console.error('[ensureWorkspaceWorkerOnBoot]', error)
    }
  })()
  return bootstrapping
}
