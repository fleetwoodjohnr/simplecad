import { readFile } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { sanitisePreferences, type Preferences } from '../shared/preferences'

function settingsPath(env: NodeJS.ProcessEnv, platform: NodeJS.Platform, home: string) {
  if (env.SIMPLECAD_LEGACY_SETTINGS) return env.SIMPLECAD_LEGACY_SETTINGS
  if (platform === 'win32') return path.win32.join(env.APPDATA || home, 'SimpleCAD', 'settings.json')
  if (platform === 'darwin') return path.join(home, 'Library', 'Application Support', 'SimpleCAD', 'settings.json')
  return path.join(env.XDG_CONFIG_HOME || path.join(home, '.config'), 'simplecad', 'settings.json')
}

export async function readLegacyPreferences(env = process.env, platform = process.platform, home = os.homedir()): Promise<Preferences | undefined> {
  try {
    const file = settingsPath(env, platform, home)
    const legacy = JSON.parse(await readFile(file, 'utf8'))
    if (!legacy || typeof legacy !== 'object' || Array.isArray(legacy)) return
    return sanitisePreferences({
      theme: legacy.theme, displayUnit: legacy.display_unit,
      snap: legacy.view?.snap ?? true, moveStep: legacy.modelling?.move_step,
      rotationSnap: legacy.modelling?.rotation_snap ?? 90
    })
  } catch {
    // A missing or damaged legacy file never prevents startup.
  }
}
