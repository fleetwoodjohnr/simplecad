import { dialog, type BrowserWindow } from 'electron'
import { open } from 'node:fs/promises'
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
