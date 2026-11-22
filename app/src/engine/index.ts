import { mkdirSync, renameSync, rmSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import { checkRendererRequest, type EngineResponse, type Measurement, type MeshFormat } from '../shared/engine-protocol'
import { EngineDocument, type Project, type Shape } from './document'

interface PortLike {
  on(ev: 'message', cb: (e: { data: unknown; ports: PortLike[] }) => void): void
  postMessage(message: EngineResponse): void
  start(): void
  close(): void
}

type AddonProject = { bodies: { id?: string; name: string; visible: boolean; shape: Shape | null }[]; notes: string[]; project: Project }
interface Addon {
  newProject(): AddonProject
  openProject(data: Buffer): AddonProject
  exportMesh(format: MeshFormat, parts: ReturnType<EngineDocument['meshParts']>): Buffer
  exportStep(parts: Shape[]): Buffer
  measureShapes(parts: ReturnType<EngineDocument['measurementPicks']>): Measurement[]
  crash(): void
}

const parent = (process as unknown as { parentPort: PortLike }).parentPort
const addon = require(process.env.SIMPLECAD_ENGINE_ADDON!) as Addon
const buffer = (data: Uint8Array) => Buffer.from(data.buffer, data.byteOffset, data.byteLength)
const opened = ({ bodies, notes, project }: AddonProject) => ({
  bodies: bodies.map(({ id, name, shape }) => ({ id, name, shape: shape ?? null })),
  visible: bodies.map((b) => b.visible), notes, project
})
const document = new EngineDocument(() => opened(addon.newProject()), (data) => opened(addon.openProject(buffer(data))))
let port: PortLike | null = null

function onRenderer(channel: PortLike, data: unknown) {
  const rawId = data && typeof data === 'object' && 'id' in data ? data.id : undefined
  const id = typeof rawId === 'number' && Number.isSafeInteger(rawId) && rawId >= 0 ? rawId : -1
  try {
    const req = checkRendererRequest(data)
    let result
    switch (req.op) {
      case 'model': result = document.model(); break
      case 'reset': result = document.reset(req.revision); break
      case 'restore': result = document.restore(req.checkpoint, req.data, req.imports); break
      case 'open': result = document.open(req.data, req.name, req.revision, req.recovered); break
      case 'preview':
      case 'previewFace':
      case 'previewShape':
      case 'previewSavedStep':
      case 'previewTransform':
      case 'previewBodyOperation':
      case 'previewSketchSolid':
      case 'previewStack': result = document.preview(req, req.revision); break
      case 'apply':
      case 'applyFace':
      case 'applyStack':
      case 'applyTransform':
      case 'applyBodyOperation':
      case 'importModel':
      case 'repairInput':
      case 'editSavedStep':
      case 'applySketchDraft':
      case 'applySketchSolid':
      case 'createShape': result = document.apply(req, req.revision); break
      case 'readSketch': result = document.readSketch(req.revision, req.sketchId); break
      case 'solveSketch': result = document.solveSketch(req.revision, req.recipe); break
      case 'show': result = document.setVisible(req.body, req.visible, req.revision); break
      case 'save': result = new Uint8Array(document.save(req.revision)); break
      case 'measure': result = addon.measureShapes(document.measurementPicks(req.revision, req.picks)); break
      case 'export': result = new Uint8Array(req.format === 'step'
        ? addon.exportStep(document.exportShapes(req.revision).map(({ shape }) => shape))
        : addon.exportMesh(req.format, document.meshParts(req.revision))); break
      case 'saved': result = document.markSaved(req.revision); break
      case 'undo': result = document.step(-1, req.revision); break
      case 'redo': result = document.step(1, req.revision); break
      case 'crash':
        if (process.env.SIMPLECAD_ENGINE_DRILL !== '1') throw new Error('Not available')
        // A real segfault inside the OCCT shim, not an orderly exit.
        addon.crash()
    }
    channel.postMessage({ id, ok: true, result: result! })
  } catch (err) {
    channel.postMessage({ id, ok: false, error: (err as Error).message })
  }
}

// Unsaved work goes to this session's recovery file every so often, beside the requests: written whole and
// moved into place, so a crash mid-write leaves the last one. Saved work needs none.
const autosaveFile = process.env.SIMPLECAD_AUTOSAVE_FILE
function autosave(file: string) {
  try {
    if (!document.unsaved) return rmSync(file, { force: true })
    mkdirSync(path.dirname(file), { recursive: true })
    writeFileSync(`${file}.part`, document.snapshot())
    renameSync(`${file}.part`, file)
  } catch {
    // Autosave must never interrupt the work it protects.
  }
}
if (autosaveFile) setInterval(() => autosave(autosaveFile), Number(process.env.SIMPLECAD_AUTOSAVE_SECONDS) * 1000 || 90_000)

parent.on('message', (e) => {
  const m = e.data as { type?: string }
  if (m?.type !== 'port') return
  port?.close()
  const channel = e.ports[0]
  port = channel
  channel.on('message', (ev) => onRenderer(channel, ev.data))
  channel.start()
})
