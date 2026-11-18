import { dialog, type BrowserWindow } from 'electron'
import { mkdtemp, open, rename, rm } from 'node:fs/promises'
import path from 'node:path'
import { MAX_PROJECT_BYTES } from '../shared/engine-protocol'

const filters = [{ name: 'SimpleCAD project', extensions: ['scad3'] }]

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
    return { name: path.basename(choice.filePaths[0]), data: new Uint8Array(data) }
  } finally {
    await file.close()
  }
}

export async function saveProjectFile(window: BrowserWindow, data: unknown, suggested: unknown) {
  if (!(data instanceof Uint8Array) || !data.byteLength || data.byteLength > MAX_PROJECT_BYTES) {
    throw new Error('Invalid SimpleCAD project data.')
  }
  const name = typeof suggested === 'string' && suggested.length < 256
    ? path.basename(suggested)
    : 'Untitled.scad3'
  const choice = await dialog.showSaveDialog(window, { defaultPath: name, filters, properties: ['showOverwriteConfirmation'] })
  if (choice.canceled || !choice.filePath) return
  const temporary = await mkdtemp(path.join(path.dirname(choice.filePath), '.simplecad-save-'))
  const staged = path.join(temporary, 'model.scad3')
  try {
    const file = await open(staged, 'wx', 0o600)
    try {
      await file.writeFile(data)
      await file.sync()
    } finally {
      await file.close()
    }
    await rename(staged, choice.filePath)
    return path.basename(choice.filePath)
  } finally {
    await rm(temporary, { recursive: true, force: true })
  }
}
