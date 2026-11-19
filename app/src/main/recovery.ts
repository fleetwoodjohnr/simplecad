// Unsaved work kept for after a crash, in the folder and form SimpleCAD 0.4 uses (simplecad/core/autosave.py):
// each session autosaves to session-<pid>.scad3, an ordinary project, and removes it on a clean exit. One left
// behind is a session that did not end cleanly, and either app offers it back.

import { readdir, readFile, rm, stat } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { MAX_PROJECT_BYTES } from '../shared/engine-protocol'

/** `paths.data_dir()/recovery`; `SIMPLECAD_RECOVERY_DIR` moves it, for the tests. */
export function recoveryDir(env = process.env, platform = process.platform, home = os.homedir()) {
  if (env.SIMPLECAD_RECOVERY_DIR) return env.SIMPLECAD_RECOVERY_DIR
  if (platform === 'win32') return path.join(env.APPDATA || home, 'SimpleCAD', 'recovery')
  if (platform === 'darwin') return path.join(home, 'Library', 'Application Support', 'SimpleCAD', 'recovery')
  return path.join(env.XDG_DATA_HOME || path.join(home, '.local', 'share'), 'simplecad', 'recovery')
}

export const sessionFile = (dir: string, pid = process.pid) => path.join(dir, `session-${pid}.scad3`)

/** Recovery files other sessions left, newest first (`autosave.pending`). */
export async function leftovers(dir: string, own: string) {
  let names: string[]
  try {
    names = await readdir(dir)
  } catch {
    return []
  }
  const found: { file: string; modified: number }[] = []
  for (const name of names) {
    const file = path.join(dir, name)
    if (!name.endsWith('.scad3') || file === own) continue
    try {
      found.push({ file, modified: (await stat(file)).mtimeMs })
    } catch {
      /* gone meanwhile */
    }
  }
  return found.sort((a, b) => b.modified - a.modified)
}

/** `autosave.describe_age`. */
export function describeAge(modified: number, now = Date.now()) {
  const seconds = Math.max(0, (now - modified) / 1000)
  if (seconds < 90) return 'less than a minute ago'
  if (seconds < 3600) return `${Math.floor(seconds / 60)} minutes ago`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} hours ago`
  return `${Math.floor(seconds / 86400)} days ago`
}

export async function readRecovered(file: string) {
  const { size } = await stat(file)
  if (size > MAX_PROJECT_BYTES) throw new Error('The recovered work is too large for this build to open.')
  return new Uint8Array(await readFile(file))
}

/** Each file and the `.part` an interrupted autosave leaves beside it. */
export async function remove(files: string[]) {
  await Promise.all(files.flatMap((f) => [f, `${f}.part`]).map((f) => rm(f, { force: true }).catch(() => {})))
}
