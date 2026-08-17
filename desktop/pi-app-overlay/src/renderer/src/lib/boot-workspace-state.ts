export function isSandboxWorkspacePath(path: string | null | undefined): boolean {
  return !!path && path.replace(/\\/g, '/').includes('sandbox-workspaces/')
}

function cleanPath(value: string | null | undefined): string {
  return typeof value === 'string' ? value.trim() : ''
}

/**
 * Renderer persist does not keep currentWorkspace. Prefer a disk project from
 * settings.currentProject over a sandbox leftover in the UI store.
 */
export function pickPersistedWorkspace(
  uiWorkspace: string | null | undefined,
  settingsProject: string | null | undefined,
): string | null {
  const ui = cleanPath(uiWorkspace)
  const settings = cleanPath(settingsProject)
  if (ui && !isSandboxWorkspacePath(ui)) return ui
  if (settings && !isSandboxWorkspacePath(settings)) return settings
  return ui || settings || null
}

/**
 * Cold-start UI shape from persisted currentWorkspace.
 * - No project / last path was a sandbox → ephemeral "new chat" draft
 * - Disk project → restore project metadata; Worker starts so project extensions load
 */
export function resolveBootWorkspaceState(path: string | null | undefined): {
  workspace: string | null
  ephemeralDraft: boolean
  shouldStartWorker: boolean
} {
  if (!path) return { workspace: null, ephemeralDraft: true, shouldStartWorker: false }
  if (isSandboxWorkspacePath(path)) return { workspace: null, ephemeralDraft: true, shouldStartWorker: false }
  return { workspace: path, ephemeralDraft: false, shouldStartWorker: true }
}
