import { existsSync } from 'fs'
import { resolve } from 'path'
import { configStore } from './config-store'

export function isSandboxWorkspacePath(p: string): boolean {
  return p.replace(/\\/g, '/').includes('sandbox-workspaces/')
}

export function pickStartupWorkspace(input: {
  envProject: string | null | undefined
  currentProject: string | null | undefined
  recentProjects: string[]
  pathExists: (p: string) => boolean
  resolvePath?: (p: string) => string
}): { path: string | null; persist: boolean; addRecent: boolean } {
  const resolvePath = input.resolvePath ?? ((p: string) => p)
  const exists = (raw: string): boolean => {
    try {
      return input.pathExists(raw)
    } catch {
      return false
    }
  }
  const asDisk = (raw: string | null | undefined): string | null => {
    const trimmed = typeof raw === 'string' ? raw.trim() : ''
    if (!trimmed) return null
    const abs = resolvePath(trimmed)
    if (isSandboxWorkspacePath(abs) || !exists(abs)) return null
    return abs
  }

  const fromEnv = asDisk(input.envProject)
  if (fromEnv) return { path: fromEnv, persist: true, addRecent: true }

  const fromCurrent = asDisk(input.currentProject)
  if (fromCurrent) return { path: fromCurrent, persist: false, addRecent: false }

  for (const recent of input.recentProjects || []) {
    const disk = asDisk(recent)
    if (disk) return { path: disk, persist: true, addRecent: false }
  }
  return { path: null, persist: false, addRecent: false }
}

/**
 * Cold-start workspace:
 * 1. INSAR_DESKTOP_PROJECT (launcher) if it exists on disk
 * 2. persisted non-sandbox currentProject
 * 3. first non-sandbox recentProjects entry that still exists
 */
export function resolveStartupWorkspace(env: NodeJS.ProcessEnv = process.env): string | null {
  const picked = pickStartupWorkspace({
    envProject: env.INSAR_DESKTOP_PROJECT,
    currentProject: configStore.get('currentProject'),
    recentProjects: configStore.get('recentProjects') || [],
    pathExists: existsSync,
    resolvePath: (p) => resolve(p),
  })
  if (picked.path && picked.persist) {
    configStore.set('currentProject', picked.path)
  }
  if (picked.path && picked.addRecent) {
    configStore.addRecentProject(picked.path)
  }
  return picked.path
}
