import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import { app, dialog, type BrowserWindow } from 'electron'
import { randomUUID } from 'node:crypto'
import { constants } from 'node:fs'
import { link, mkdtemp, open, realpath, rename, rm } from 'node:fs/promises'
import path from 'node:path'
import { isExportFormat, MAX_PROJECT_BYTES, type ImportFormat, type ModelImport } from '../shared/engine-protocol'

const runProgram = promisify(execFile)

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
  const formats = new Map<string, ImportFormat | 'scad'>([
    ['step', 'step'], ['stp', 'step'], ['brep', 'brep'], ['scad3', 'scad3'],
    ['igs', 'iges'], ['iges', 'iges'], ['scad', 'scad'], ['gltf', 'gltf'], ['glb', 'glb'], ['stl', 'stl'], ['obj', 'obj'], ['3mf', '3mf']
  ])
  const choice = await dialog.showOpenDialog(window, {
    properties: ['openFile'],
    filters: [
      { name: 'Supported models', extensions: [...formats.keys()] },
      { name: 'SimpleCAD project', extensions: ['scad3'] },
      { name: 'STEP', extensions: ['step', 'stp'] },
      { name: 'B-Rep', extensions: ['brep'] },
      { name: 'IGES', extensions: ['igs', 'iges'] },
      { name: 'STL', extensions: ['stl'] },
      { name: 'OBJ', extensions: ['obj'] },
      { name: '3MF', extensions: ['3mf'] },
      { name: 'glTF', extensions: ['gltf', 'glb'] },
      { name: 'OpenSCAD', extensions: ['scad'] }
    ]
  })
  if (choice.canceled || !choice.filePaths.length) return
  const target = choice.filePaths[0]
  const ext = path.extname(target)
  const importFormat = formats.get(ext.slice(1).toLowerCase())
  if (!importFormat) throw new Error('Choose a supported model file.')
  const importName = path.basename(target, ext)
  if (importFormat === 'scad') return { importName, data: await renderScad(window, target), importFormat: '3mf' }
  const data = await readInputFile(target)
  return { importName, data: importFormat === 'gltf' || importFormat === 'glb' ? await embedGltfBuffers(target, data) : data,
    importFormat }
}

async function embedGltfBuffers(target: string, input: Uint8Array): Promise<Uint8Array> {
  const bytes = Buffer.from(input.buffer, input.byteOffset, input.byteLength)
  const binary = bytes.length >= 20 && bytes.toString('ascii', 0, 4) === 'glTF'
  const jsonEnd = binary ? 20 + bytes.readUInt32LE(12) : bytes.length
  if (binary && (bytes.readUInt32LE(4) !== 2 || bytes.readUInt32LE(8) !== bytes.length ||
      bytes.readUInt32LE(16) !== 0x4e4f534a || jsonEnd > bytes.length)) {
    throw new Error('This GLB has an invalid header.')
  }
  let document: { buffers?: { uri?: string }[] }
  try {
    document = JSON.parse(bytes.toString('utf8', binary ? 20 : 0, jsonEnd).replace(/^\uFEFF/, ''))
    if (!document || typeof document !== 'object' || (document.buffers !== undefined && !Array.isArray(document.buffers))) throw new Error()
  } catch {
    throw new Error('This glTF has an unreadable model.')
  }
  let size = bytes.byteLength
  let changed = false
  const base = await realpath(path.dirname(target))
  const inside = (candidate: string) => {
    const relative = path.relative(base, candidate)
    return !!relative && !path.isAbsolute(relative) && relative !== '..' && !relative.startsWith('..' + path.sep)
  }
  for (const buffer of document.buffers ?? []) {
    if (!buffer || typeof buffer !== 'object' || (buffer.uri !== undefined && typeof buffer.uri !== 'string')) {
      throw new Error('This glTF has an unreadable buffer reference.')
    }
    if (!buffer.uri || buffer.uri.startsWith('data:')) continue
    if (/[:?#\\]/.test(buffer.uri)) throw new Error('A glTF buffer must be a local relative file.')
    let reference: string
    try {
      reference = decodeURIComponent(buffer.uri)
      if (reference.includes('\\') || reference.includes('\0')) throw new Error()
    } catch {
      throw new Error('A glTF buffer has an invalid path.')
    }
    const candidate = path.resolve(base, reference)
    if (!inside(candidate)) throw new Error('Keep the glTF and its buffers in the same model folder.')
    const actual = await realpath(candidate).catch(() => { throw new Error(`The glTF buffer "${path.basename(candidate)}" is missing.`) })
    if (!inside(actual)) throw new Error('Keep the glTF and its buffers in the same model folder.')
    const data = await readInputFile(actual)
    size += Math.ceil(data.byteLength / 3) * 4 + 48
    if (size > MAX_PROJECT_BYTES) throw new Error('This glTF and its buffers are too large to open.')
    buffer.uri = 'data:application/octet-stream;base64,' + Buffer.from(data.buffer, data.byteOffset, data.byteLength).toString('base64')
    changed = true
  }
  if (!changed) return input
  const json = Buffer.from(JSON.stringify(document))
  if (!binary) {
    if (json.length > MAX_PROJECT_BYTES) throw new Error('This glTF and its buffers are too large to open.')
    return json
  }
  const jsonLength = Math.ceil(json.length / 4) * 4
  const length = 20 + jsonLength + bytes.length - jsonEnd
  if (length > MAX_PROJECT_BYTES) throw new Error('This glTF and its buffers are too large to open.')
  const result = Buffer.alloc(length, 0x20)
  bytes.copy(result, 0, 0, 20)
  result.writeUInt32LE(length, 8)
  result.writeUInt32LE(jsonLength, 12)
  json.copy(result, 20)
  bytes.copy(result, 20 + jsonLength, jsonEnd)
  return result
}

async function renderScad(window: BrowserWindow, source: string): Promise<Uint8Array> {
  const scratch = await mkdtemp(path.join(app.getPath('userData'), 'openscad-'))
  const rendered = path.join(scratch, 'rendered.3mf')
  const abort = new AbortController()
  const stop = () => abort.abort()
  window.once('closed', stop)
  try {
    try {
      await runProgram('openscad', ['-o', rendered, source], {
        signal: abort.signal, timeout: 300_000, maxBuffer: 1024 * 1024, windowsHide: true
      })
    } catch (failure) {
      const error = failure as NodeJS.ErrnoException & { killed?: boolean; stderr?: string }
      if (error.code === 'ENOENT') throw new Error('Install OpenSCAD and add it to PATH to import .scad files.')
      if (error.code === 'ABORT_ERR') throw new Error('OpenSCAD import canceled.')
      if (error.killed) throw new Error('OpenSCAD took too long to render this model.')
      const detail = error.stderr?.trim().slice(-1000)
      throw new Error(detail ? `OpenSCAD could not render this model. ${detail}` : 'OpenSCAD could not render this model.')
    }
    return await readInputFile(rendered)
  } finally {
    window.removeListener('closed', stop)
    await rm(scratch, { recursive: true, force: true })
  }
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

/** The open project's name, as exports are named after it. */
export const projectName = () => current ? path.basename(current, path.extname(current)) : 'Untitled'

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
