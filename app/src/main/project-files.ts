import { dialog, type BrowserWindow } from 'electron'
import { randomUUID } from 'node:crypto'
import { constants } from 'node:fs'
import { link, mkdtemp, open, rename, rm } from 'node:fs/promises'
import path from 'node:path'
import { isExportFormat, MAX_PROJECT_BYTES, type ModelImport } from '../shared/engine-protocol'

const filters = [{ name: 'SimpleCAD project', extensions: ['scad3'] }]
/** The file this window's session came from or was last saved to: where Save writes. */
let current: string | undefined
let offered: { token: string; target: string } | undefined

export async function openProjectFile(window: BrowserWindow) {
  const choice = await dialog.showOpenDialog(window, { properties: ['openFile'], filters })
  if (choice.canceled || !choice.filePaths.length) return
  return offerProjectFile(choice.filePaths[0])
}

export async function offerProjectFile(target: string) {
  if (path.extname(target).toLowerCase() !== '.scad3') throw new Error('Choose a SimpleCAD project.')
  offered = undefined
  const data = await readInputFile(target)
  const token = randomUUID()
  offered = { token, target }
  return { name: path.basename(target), data, token }
}

export function acceptProjectFile(token: unknown) {
  if (!offered || offered.token !== token) throw new Error('The project open request has expired.')
  current = offered.target
  offered = undefined
}

export async function openModelFile(window: BrowserWindow): Promise<ModelImport | undefined> {
  const choice = await dialog.showOpenDialog(window, {
    properties: ['openFile'],
    filters: [
      { name: 'Supported models', extensions: ['step', 'stp', 'brep'] },
      { name: 'STEP', extensions: ['step', 'stp'] },
      { name: 'B-Rep', extensions: ['brep'] }
    ]
  })
  if (choice.canceled || !choice.filePaths.length) return
  const target = choice.filePaths[0]
  const ext = path.extname(target)
  const extension = ext.toLowerCase()
  if (!['.step', '.stp', '.brep'].includes(extension)) throw new Error('Choose a STEP or B-Rep file.')
  return { importName: path.basename(target, ext), data: await readInputFile(target),
    importFormat: extension === '.brep' ? 'brep' : 'step' }
}

async function readInputFile(target: string): Promise<Uint8Array> {
  const flags = process.platform === 'win32' ? constants.O_RDONLY : constants.O_RDONLY | constants.O_NONBLOCK
  const file = await open(target, flags)
  try {
    const stat = await file.stat()
    if (!stat.isFile()) throw new Error('Choose a regular file.')
    if (!stat.size) throw new Error('This file is empty.')
    if (stat.size > MAX_PROJECT_BYTES) throw new Error('This file is too large for this build to open.')
    const data = Buffer.alloc(stat.size)
    let offset = 0
    while (offset < data.length) {
      const { bytesRead } = await file.read(data, offset)
      if (!bytesRead) throw new Error('The file changed while it was being opened.')
      offset += bytesRead
    }
    return new Uint8Array(data)
  } finally {
    await file.close()
  }
}

export function forgetProjectFile() {
  current = undefined
  offered = undefined
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
    target = await writeChosen(window, choice.filePath, 'scad3', data)
    if (!target) return
  } else {
    await writeAtomic(target, data, true)
  }
  current = target
  return path.basename(target)
}

export async function saveExportFile(window: BrowserWindow, data: unknown, format: unknown) {
  if (!isExportFormat(format) || !(data instanceof Uint8Array) ||
    !data.byteLength || data.byteLength > MAX_PROJECT_BYTES) throw new Error('Invalid export data.')
  const name = current ? path.basename(current, path.extname(current)) : 'Untitled'
  const choice = await dialog.showSaveDialog(window, {
    defaultPath: `${name}.${format}`,
    filters: [{ name: format.toUpperCase(), extensions: [format] }],
    properties: ['showOverwriteConfirmation']
  })
  if (choice.canceled || !choice.filePath) return
  const target = await writeChosen(window, choice.filePath, format, data)
  return target ? path.basename(target) : undefined
}

async function writeChosen(window: BrowserWindow, chosen: string, extension: string, data: Uint8Array) {
  if (path.extname(chosen).toLowerCase() === `.${extension}`) {
    await writeAtomic(chosen, data, true)
    return chosen
  }
  const target = `${chosen}.${extension}`
  try {
    await writeAtomic(target, data, false)
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== 'EEXIST') throw error
    const { response } = await dialog.showMessageBox(window, {
      type: 'warning', message: `Replace “${path.basename(target)}”?`,
      buttons: ['Cancel', 'Replace'], cancelId: 0, defaultId: 0
    })
    if (response !== 1) return
    await writeAtomic(target, data, true)
  }
  return target
}

// Written beside the target and moved into place so an interrupted write keeps the previous file.
async function writeAtomic(target: string, data: Uint8Array, overwrite: boolean) {
  const temporary = await mkdtemp(path.join(path.dirname(target), '.simplecad-save-'))
  const staged = path.join(temporary, 'output')
  try {
    const file = await open(staged, 'wx', 0o600)
    try {
      await file.writeFile(data)
      await file.sync()
    } finally {
      await file.close()
    }
    if (overwrite) await rename(staged, target)
    else await link(staged, target)
  } finally {
    await rm(temporary, { recursive: true, force: true })
  }
}
