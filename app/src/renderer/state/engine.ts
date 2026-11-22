// The window's side of the engine port: requests with ids, answers matched back. The port arrives from preload as a
// window message, and again after every engine restart; and requests
// in flight when the engine dies are rejected rather than left waiting forever.

import type { BodyOperationEdit, Checkpoint, DisplayShape, EngineResponse, ExportFormat, FaceEdit, MeasurePick, Measurement, ModelResult, PullEdit, ReferenceRepair, TransformEdit, RendererCommand, SavedStepEdit, ShapeEdit, StackEdit, ModelImport } from '@/shared/engine-protocol'

import type { SketchDraftEdit, SketchSolidEdit, SketchSolution } from '@/shared/sketch'

type Pending = { resolve: (v: unknown) => void; reject: (e: Error) => void }

let port: MessagePort | null = null
let seq = 0
const pending = new Map<number, Pending>()
let checkpoint: Checkpoint | undefined
// The open file's bytes, so a restarted engine can reopen it and replay the edits.
let source: Uint8Array | undefined
const imports = new Map<string, Uint8Array>()
const listeners = new Set<() => void>()

window.addEventListener('message', (e) => {
  if (e.source !== window || e.data !== 'simplecad:engine-port' || !e.ports?.[0]) return
  port?.close()
  for (const p of pending.values()) p.reject(new Error('The geometry engine restarted.'))
  pending.clear()
  const connection = e.ports[0]
  port = connection
  connection.addEventListener('close', () => {
    if (port !== connection) return
    port = null
    for (const p of pending.values()) p.reject(new Error('The geometry engine restarted.'))
    pending.clear()
  })
  port.onmessage = (ev: MessageEvent<EngineResponse>) => {
    const m = ev.data
    const p = pending.get(m.id)
    if (!p) return
    pending.delete(m.id)
    if (m.ok) p.resolve(m.result)
    else p.reject(new Error(m.error ?? 'The geometry engine failed'))
  }
  listeners.forEach((f) => f())
})

/** Called each time a new engine port arrives: the first start and every restart. */
export function onEngineConnected(f: () => void) {
  listeners.add(f)
  if (port) f()
  return () => { listeners.delete(f) }
}

function send<T>(req: RendererCommand): Promise<T> {
  const id = ++seq
  return new Promise<T>((resolve, reject) => {
    if (!port) {
      reject(new Error('The geometry engine is reconnecting.'))
      return
    }
    pending.set(id, { resolve: resolve as (v: unknown) => void, reject })
    try {
      port.postMessage({ ...req, id })
    } catch (error) {
      pending.delete(id)
      reject(error)
    }
  })
}

function accept(result: ModelResult): ModelResult {
  checkpoint = result.checkpoint
  const retained = new Set(checkpoint.edits.flatMap((step) => 'importId' in step ? [step.importId] : []))
  for (const id of imports.keys()) if (!retained.has(id)) imports.delete(id)
  return result
}

export const openSession = (restore: boolean) => send<ModelResult>(restore && checkpoint
  ? { op: 'restore', checkpoint, data: checkpoint.source.kind === 'file' ? source : undefined,
    imports: [...imports].map(([importId, data]) => ({ importId, data })) }
  : { op: 'model' }).then(accept)
export const openProject = (revision: number, data: Uint8Array, name: string, recovered = false) =>
  send<ModelResult>({ op: 'open', revision, data, name, recovered }).then((result) => {
    source = data
    return accept(result)
  })
export const previewPull = (revision: number, edit: PullEdit) => send<DisplayShape>({ op: 'preview', revision, ...edit })
export const applyPull = (revision: number, edit: PullEdit) => send<ModelResult>({ op: 'apply', revision, ...edit }).then(accept)
export const previewFace = (revision: number, edit: FaceEdit) => send<DisplayShape>({ op: 'previewFace', revision, ...edit })
export const applyFace = (revision: number, edit: FaceEdit) => send<ModelResult>({ op: 'applyFace', revision, ...edit }).then(accept)
export const previewShape = (revision: number, edit: ShapeEdit) => send<DisplayShape>({ op: 'previewShape', revision, ...edit })
export const previewSavedStep = (revision: number, edit: SavedStepEdit) => send<DisplayShape>({ op: 'previewSavedStep', revision, ...edit })
export const editSavedStep = (revision: number, edit: SavedStepEdit) => send<ModelResult>({ op: 'editSavedStep', revision, ...edit }).then(accept)
export const previewTransform = (revision: number, edit: TransformEdit) =>
  send<DisplayShape>({ op: 'previewTransform', revision, ...edit })
export const applyTransform = (revision: number, edit: TransformEdit) =>
  send<ModelResult>({ op: 'applyTransform', revision, ...edit }).then(accept)
export const previewBodyOperation = (revision: number, edit: BodyOperationEdit) =>
  send<DisplayShape>({ op: 'previewBodyOperation', revision, ...edit })
export const applyBodyOperation = (revision: number, edit: BodyOperationEdit) =>
  send<ModelResult>({ op: 'applyBodyOperation', revision, ...edit }).then(accept)
export const readSketch = (revision: number, sketchId: string) =>
  send<string>({ op: 'readSketch', revision, sketchId })
export const solveSketch = (revision: number, recipe: string) =>
  send<SketchSolution>({ op: 'solveSketch', revision, recipe })
export const applySketchDraft = (revision: number, edit: SketchDraftEdit) =>
  send<ModelResult>({ op: 'applySketchDraft', revision, ...edit }).then(accept)
export const previewSketchSolid = (revision: number, edit: SketchSolidEdit) =>
  send<DisplayShape>({ op: 'previewSketchSolid', revision, ...edit })
export const applySketchSolid = (revision: number, edit: SketchSolidEdit) =>
  send<ModelResult>({ op: 'applySketchSolid', revision, ...edit }).then(accept)
let stackPreviewRunning = false
let nextStackPreview: { run: () => void; cancel: () => void } | undefined

export function cancelQueuedStackPreview() {
  nextStackPreview?.cancel()
  nextStackPreview = undefined
}

export function previewStack(revision: number, edit: StackEdit): Promise<DisplayShape | undefined> {
  return new Promise((resolve, reject) => {
    const run = () => {
      stackPreviewRunning = true
      void send<DisplayShape>({ op: 'previewStack', revision, ...edit }).then(resolve, reject).finally(() => {
        stackPreviewRunning = false
        const next = nextStackPreview
        nextStackPreview = undefined
        next?.run()
      })
    }
    if (stackPreviewRunning) {
      cancelQueuedStackPreview()
      nextStackPreview = { run, cancel: () => resolve(undefined) }
    } else {
      run()
    }
  })
}

export const applyStack = (revision: number, edit: StackEdit) => send<ModelResult>({ op: 'applyStack', revision, ...edit }).then(accept)
export const createShape = (revision: number, edit: ShapeEdit) => send<ModelResult>({ op: 'createShape', revision, ...edit }).then(accept)
export const repairInput = (revision: number, edit: ReferenceRepair) => send<ModelResult>({ op: 'repairInput', revision, ...edit }).then(accept)
export const importModel = (revision: number, edit: ModelImport) => {
  const importId = crypto.randomUUID()
  return send<ModelResult>({ op: 'importModel', revision, importId, ...edit }).then((result) => {
    imports.set(importId, edit.data)
    return accept(result)
  })
}
export const saveModel = (revision: number) => send<Uint8Array>({ op: 'save', revision })
export const measure = (revision: number, picks: MeasurePick[]) =>
  send<Measurement[]>({ op: 'measure', revision, picks })
export const exportModel = (revision: number, format: ExportFormat) =>
  send<Uint8Array>({ op: 'export', revision, format })
export const markSaved = (revision: number) => send<ModelResult>({ op: 'saved', revision }).then(accept)
export const showBody = (revision: number, body: number, visible: boolean) =>
  send<ModelResult>({ op: 'show', revision, body, visible }).then(accept)
export const resetModel = (revision: number) => send<ModelResult>({ op: 'reset', revision }).then((result) => {
  source = undefined
  return accept(result)
})
export const stepHistory = (revision: number, op: 'undo' | 'redo') => send<ModelResult>({ op, revision }).then(accept)
/** The isolation drill: the engine only obeys it when started for the drill. */
export const crashEngine = () => send<never>({ op: 'crash' })
