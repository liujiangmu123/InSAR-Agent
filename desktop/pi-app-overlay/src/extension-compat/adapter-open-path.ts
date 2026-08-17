import { homedir } from 'os'
import { isAbsolute, join, resolve } from 'path'
import { fileURLToPath } from 'url'

const WINDOWS_DRIVE = /^[a-zA-Z]:[\\/]/

export function expandUserPath(p: string, home = homedir()): string {
  if (p === '~') return home
  if (p.startsWith('~/') || p.startsWith('~\\')) return join(home, p.slice(2))
  return p
}

/** Resolve an adapter openPath target against the trusted workspace cwd. */
export function resolveAdapterOpenPath(
  raw: string,
  cwd: string,
  home = homedir(),
): { ok: true; abs: string } | { ok: false; error: string } {
  const t = raw.trim()
  if (!t) return { ok: false, error: 'no path' }
  if (t.includes('\0')) return { ok: false, error: 'invalid path' }
  const looksProtocol = /^[a-z][a-z0-9+.-]*:/i.test(t) && !WINDOWS_DRIVE.test(t)
  if (looksProtocol && !t.toLowerCase().startsWith('file:')) {
    return { ok: false, error: 'not a filesystem path' }
  }
  let p = t
  if (p.toLowerCase().startsWith('file:')) {
    try {
      p = fileURLToPath(p)
    } catch {
      return { ok: false, error: 'bad file url' }
    }
  }
  p = expandUserPath(p, home)
  if (!isAbsolute(p)) {
    if (!cwd.trim()) return { ok: false, error: 'no workspace' }
    p = resolve(cwd, p)
  } else {
    p = resolve(p)
  }
  return { ok: true, abs: p }
}
