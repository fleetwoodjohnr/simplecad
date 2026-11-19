import { dialog, type BrowserWindow } from 'electron'
import { mkdtemp, open, rename, rm } from 'node:fs/promises'
import path from 'node:path'
import { MAX_PROJECT_BYTES } from '../shared/engine-protocol'

const filters = [{ name: 'SimpleCAD project', extensions: ['scad3'] }]
/** The file this window's session came from or was last saved to: where Save writes. */
let current: string | undefined

export async function openProjectFile(window: BrowserWindow) {
  const choice = await dialog.showOpenDialog(window, { properties: ['openFile'], filters })
  if (choice.canceled) return
  const file = await open(choice.filePaths[0], 'r')
  try {
    const stat = await file.stat()
    if (!stat.isFile() || stat.size > MAX_PROJECT_BYTES) throw new Error('This file is too large for this build to open.')
    const data = Buffer.alloc(stat.size)
    let offset = 0
    while (offset < data.length) {
      const { bytesRead } = await file.read(data, offset)
      if (!bytesRead) throw new Error('The file changed while it was being opened.')
      offset += bytesRead
    }
    current = choice.filePaths[0]
    return { name: path.basename(current), data: new Uint8Array(data) }
  } finally {
    await file.close()
  }
}

export function forgetProjectFile() {
  current = undefined
}

/** Save to the session's file, or ask where (`saveAs`, or no file yet). Returns the file's name, or nothing if cancelled. */
export async function saveProjectFile(window: BrowserWindow, data: unknown, saveAs: unknown) {
  if (!(data instanceof Uint8Array) || !data.byteLength || data.byteLength > MAX_PROJECT_BYTES) {
    throw new Error('Invalid SimpleCAD project data.')
  }
  let target = current
  if (saveAs === true || !target) {
    const choice = await dialog.showSaveDialog(window, {
      defaultPath: target ?? 'Untitled.scad3', filters, properties: ['showOverwriteConfirmation']
    })
    if (choice.canceled || !choice.filePath) return
    target = choice.filePath.toLowerCase().endsWith('.scad3') ? choice.filePath : `${choice.filePath}.scad3`
  }
  // Written beside the target and moved into place, so an interrupted save never destroys the previous version.
  const temporary = await mkdtemp(path.join(path.dirname(target), '.simplecad-save-'))
  const staged = path.join(temporary, 'model.scad3')
  try {
    const file = await open(staged, 'wx', 0o600)
    try {
      await file.writeFile(data)
      await file.sync()
    } finally {
      await file.close()
    }
    await rename(staged, target)
    current = target
    return path.basename(target)
  } finally {
    await rm(temporary, { recursive: true, force: true })
  }
}
