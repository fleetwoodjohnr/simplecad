// Send the model to a slicer: write it as 3MF, then open it as SimpleCAD 0.4's printing.open_in_slicer does.
import { spawn, spawnSync } from 'node:child_process'
import { accessSync, constants } from 'node:fs'
import { mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { app, shell } from 'electron'
import { MAX_PROJECT_BYTES } from '../shared/engine-protocol'

/** Slicers to look for, in preference order: name, commands on the PATH, Flatpak ID. */
export const SLICERS: [name: string, binaries: string[], flatpak: string][] = [
  ['OrcaSlicer', ['orca-slicer', 'OrcaSlicer', 'orcaslicer'], 'com.orcaslicer.OrcaSlicer'],
  ['ElegooSlicer', ['elegoo-slicer', 'ElegooSlicer', 'elegooslicer'], 'com.elegoo.ElegooSlicer'],
  ['PrusaSlicer', ['prusa-slicer', 'PrusaSlicer'], 'com.prusa3d.PrusaSlicer'],
  ['Cura', ['cura', 'UltiMaker-Cura'], 'com.ultimaker.cura']
]

/** The first slicer this machine has, as its name and the command that starts it. */
export function findSlicer(searchPath: string, executable: (file: string) => boolean, flatpak: (id: string) => boolean) {
  const dirs = searchPath.split(path.delimiter).filter(Boolean)
  for (const [name, binaries, id] of SLICERS) {
    for (const binary of binaries) {
      const found = dirs.map((dir) => path.join(dir, binary)).find(executable)
      if (found) return { name, command: [found] }
    }
    if (flatpak(id)) return { name, command: ['flatpak', 'run', id] }
  }
}

/** A system program gets the system's libraries, not the ones a bundled build puts on the path. */
function systemEnv() {
  const env = { ...process.env }
  if (env.LD_LIBRARY_PATH_ORIG !== undefined) env.LD_LIBRARY_PATH = env.LD_LIBRARY_PATH_ORIG
  else delete env.LD_LIBRARY_PATH
  delete env.LD_PRELOAD
  return env
}

const isExecutable = (file: string) => {
  try {
    accessSync(file, constants.X_OK)
    return true
  } catch {
    return false
  }
}
// A machine without Flatpak fails the spawn, which is a no.
const hasFlatpak = (id: string) => spawnSync('flatpak', ['info', id], { timeout: 8000, stdio: 'ignore', env: systemEnv() }).status === 0

/** Writes the model and opens it in a slicer; returns the slicer's name. */
export async function sendToSlicer(data: unknown, project: string) {
  if (!(data instanceof Uint8Array) || !data.byteLength || data.byteLength > MAX_PROJECT_BYTES) throw new Error('Invalid model data.')
  const folder = path.join(app.getPath('temp'), 'SimpleCAD')
  await mkdir(folder, { recursive: true })
  const file = path.join(folder, `${project.replace(/[\\/:*?"<>|]/g, '_') || 'Untitled'}.3mf`)
  await writeFile(file, data)
  if (process.platform !== 'linux') {
    // Slicers aren't on the PATH here: the one installed as the 3MF handler opens it.
    if (await shell.openPath(file)) throw new Error('No program is set to open 3MF files. Install OrcaSlicer or another slicer, then try again.')
    return 'the default slicer'
  }
  const env = systemEnv()
  const slicer = findSlicer(env.PATH ?? '', isExecutable, hasFlatpak)
  if (!slicer) throw new Error('No slicer was found on this machine. Install OrcaSlicer or ElegooSlicer, then try again.')
  await new Promise<void>((resolve, reject) => {
    const child = spawn(slicer.command[0], [...slicer.command.slice(1), file], { detached: true, stdio: 'ignore', env })
    child.once('spawn', () => { child.unref(); resolve() })
    child.once('error', () => reject(new Error(`${slicer.name} could not be started.`)))
  })
  return slicer.name
}
