export interface MeshData {
  positions: Float32Array
  normals: Float32Array
  indices: Uint32Array
  faceTriStart: Uint32Array
  edgePoints: Float32Array
  edgeStart: Uint32Array
}

export interface FaceInfo {
  /** Only a planar face can be pushed or pulled; a curved one has a zero normal. */
  planar: boolean
  center: [number, number, number]
  anchor: [number, number, number]
  normal: [number, number, number]
  area: number
  /** Index into `ModelResult.bodies`. */
  body: number
}

/** Every shown body as one mesh; faces are numbered across them, body by body. */
export interface DisplayShape extends MeshData {
  volume: number
  faces: FaceInfo[]
}

export interface BodyInfo {
  name: string
  visible: boolean
  /** The file has no readable geometry for it. */
  empty: boolean
}

/** Where the session's model came from: a new box, or a `.scad3` file (the window keeps its bytes). */
export type Source = { kind: 'box' } | { kind: 'file'; name: string }
/** A push/pull as the window asks for it: a face as the viewport numbers it. */
export interface PullEdit { face: number; distance: number }
/** A push/pull as the history keeps it: the body, and the face as that body numbers it, so showing or hiding bodies doesn't change it. */
export interface FacePick { body: number; face: number }
export interface ViewFrame {
  direction: [number, number, number]
  up: [number, number, number]
}
export interface StackSettings {
  operation: 'stack' | 'center' | 'concentric'
  offset: number
  rotation: number
  flip: boolean
  horizontal: 'left' | 'center' | 'right'
  vertical: 'bottom' | 'center' | 'top'
  horizontalGap: number
  verticalGap: number
  view?: ViewFrame
}
export interface StackEdit { moving: number; target: number; settings: StackSettings }
export interface ShapeEdit { shapeKind: string; values: Record<string, string> }
export interface StepImport { importName: string; data: Uint8Array }
export interface ImportSource { importId: string; data: Uint8Array }
export type ImportRequest = StepImport & Pick<ImportSource, 'importId'>
export type ImportRef = Omit<ImportRequest, 'data'> & { against: number[] }
export type MeshFormat = 'stl' | 'obj' | '3mf'
export type ExportFormat = MeshFormat | 'step'
export const isMeshFormat = (format: unknown): format is MeshFormat =>
  format === 'stl' || format === 'obj' || format === '3mf'
export const isExportFormat = (format: unknown): format is ExportFormat =>
  format === 'step' || isMeshFormat(format)
export type StackStep = { moving: FacePick; target: FacePick; settings: StackSettings }
export type Step = (FacePick & PullEdit) | StackStep | ShapeEdit | ImportRef

export interface Checkpoint {
  source: Source
  edits: Step[]
  cursor: number
  /** Bodies the user has hidden or shown since opening: index and shown. */
  shown: [number, boolean][]
  /** The history point last saved; -1 when it is no longer in the history. */
  saved: number
}

export interface StepInfo {
  name: string
  /** The step's type, as the file names it: `push_pull`, `box`… */
  kind: string
  /** As last built: `ok`, `suppressed`, `failed` or `needs_attention`. */
  state: string
  /** Undone in this session; redo brings it back. */
  undone: boolean
}

export interface ModelResult {
  shape: DisplayShape
  bodies: BodyInfo[]
  /** The shown point of the history is not the one last saved. */
  unsaved: boolean
  revision: number
  checkpoint: Checkpoint
  /** The model's steps, oldest first, and after them this session's undone ones. */
  steps: StepInfo[]
  /** What opening the file could not read. */
  notes: string[]
}

export type RendererCommand =
  | { op: 'model' | 'crash' }
  | { op: 'reset'; revision: number }
  | { op: 'restore'; checkpoint: Checkpoint; data?: Uint8Array; imports?: ImportSource[] }
  /** `recovered`: work from a session that crashed, opened as unsaved. */
  | { op: 'open'; data: Uint8Array; name: string; revision: number; recovered?: boolean }
  | ({ op: 'preview' | 'apply'; revision: number } & PullEdit)
  | ({ op: 'previewStack' | 'applyStack'; revision: number } & StackEdit)
  | ({ op: 'createShape'; revision: number } & ShapeEdit)
  | ({ op: 'importStep'; revision: number } & ImportRequest)
  | { op: 'show'; body: number; visible: boolean; revision: number }
  | { op: 'save' | 'saved'; revision: number }
  | { op: 'export'; revision: number; format: ExportFormat }
  | { op: 'undo' | 'redo'; revision: number }

export type RendererRequest = RendererCommand & { id: number }
export type EngineResponse =
  | { id: number; ok: true; result: ModelResult | DisplayShape | Uint8Array }
  | { id: number; ok: false; error: string }

export const MAX_EDITS = 100
export const MAX_BODIES = 10_000
export const MAX_PROJECT_BYTES = 256 * 1024 * 1024

function isIndex(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0
}

function isName(value: unknown): value is string {
  return typeof value === 'string' && value.length > 0 && value.length < 256
}

function isSource(value: unknown): value is Source {
  const source = value as Partial<{ kind: string; name: unknown }> | null
  return !!source && typeof source === 'object' &&
    (source.kind === 'box' || (source.kind === 'file' && isName(source.name)))
}

function isData(value: unknown): value is Uint8Array {
  return value instanceof Uint8Array && value.byteLength <= MAX_PROJECT_BYTES
}

function isEdit(value: unknown): value is PullEdit {
  if (!value || typeof value !== 'object') return false
  const edit = value as Partial<PullEdit>
  return isIndex(edit.face) && edit.face <= 0xffffffff &&
    typeof edit.distance === 'number' && Number.isFinite(edit.distance) && Math.abs(edit.distance) <= 1e9
}

function isStep(value: unknown): value is Step {
  if (isShapeEdit(value) || isImportRef(value)) return true
  if (isEdit(value)) return isPick(value)
  if (!value || typeof value !== 'object') return false
  const step = value as Partial<StackStep>
  return isPick(step.moving) && isPick(step.target) && step.moving.body !== step.target.body && isStackSettings(step.settings)
}

function isImportRef(value: unknown): value is ImportRef {
  if (!value || typeof value !== 'object') return false
  const step = value as Partial<ImportRef>
  return isName(step.importName) && isName(step.importId) &&
    Array.isArray(step.against) && step.against.length <= MAX_BODIES &&
    step.against.every((index) => isIndex(index) && index < MAX_BODIES)
}

function isImportSource(value: unknown): value is ImportSource {
  if (!value || typeof value !== 'object') return false
  const source = value as Partial<ImportSource>
  return isName(source.importId) && isData(source.data) && !!source.data?.byteLength
}

function isImportList(value: unknown): value is ImportSource[] {
  return Array.isArray(value) && value.length <= MAX_EDITS && value.every(isImportSource) &&
    value.reduce((size, source) => size + source.data.byteLength, 0) <= MAX_PROJECT_BYTES
}
function isShapeEdit(value: unknown): value is ShapeEdit {
  if (!value || typeof value !== 'object') return false
  const { shapeKind, values } = value as Partial<ShapeEdit>
  if (typeof shapeKind !== 'string' || !/^[a-z_]{1,20}$/.test(shapeKind) || !values || typeof values !== 'object' || Array.isArray(values)) return false
  const entries = Object.entries(values)
  return entries.length <= 12 && entries.every(([key, expression]) => /^[a-z][a-z_0-9]{0,30}$/.test(key) &&
    typeof expression === 'string' && expression.length > 0 && expression.length < 256)
}

function isPick(value: unknown): value is FacePick {
  if (!value || typeof value !== 'object') return false
  const pick = value as Partial<FacePick>
  return isIndex(pick.body) && pick.body! < MAX_BODIES && isIndex(pick.face) && pick.face! <= 0xffffffff
}

function isStackSettings(value: unknown): value is StackSettings {
  if (!value || typeof value !== 'object') return false
  const s = value as Partial<StackSettings>
  return ['stack', 'center', 'concentric'].includes(s.operation ?? '') && typeof s.flip === 'boolean' &&
    ['left', 'center', 'right'].includes(s.horizontal ?? '') && ['bottom', 'center', 'top'].includes(s.vertical ?? '') &&
    [s.offset, s.rotation, s.horizontalGap, s.verticalGap].every((n) => typeof n === 'number' && Number.isFinite(n) && Math.abs(n) <= 1e9) &&
    (s.view === undefined || isViewFrame(s.view))
}

function isViewFrame(value: unknown): value is ViewFrame {
  if (!value || typeof value !== 'object') return false
  const view = value as Partial<ViewFrame>
  const isDirection = (vector: unknown): boolean => Array.isArray(vector) && vector.length === 3 &&
    vector.every((n) => typeof n === 'number' && Number.isFinite(n) && Math.abs(n) <= 1.000001) &&
    Math.abs(Math.hypot(...vector) - 1) < 1e-6
  return isDirection(view.direction) && isDirection(view.up)
}
export function isCheckpoint(value: unknown): value is Checkpoint {
  if (!value || typeof value !== 'object') return false
  const model = value as Partial<Checkpoint>
  return isSource(model.source) && Array.isArray(model.edits) && model.edits.length <= MAX_EDITS &&
    model.edits.every(isStep) && isIndex(model.cursor) && model.cursor <= model.edits.length &&
    Array.isArray(model.shown) && model.shown.length <= MAX_BODIES &&
    model.shown.every((s) => Array.isArray(s) && s.length === 2 && isIndex(s[0]) && s[0] < MAX_BODIES && typeof s[1] === 'boolean') &&
    Number.isSafeInteger(model.saved) && model.saved! >= -1 && model.saved! <= model.edits.length
}

export function checkRendererRequest(data: unknown): RendererRequest {
  const m = data as Record<string, unknown> | null
  if (!m || typeof m !== 'object' || !isIndex(m.id)) throw new Error('Malformed engine request.')
  if (m.op === 'model' || m.op === 'crash' ||
      (m.op === 'restore' && isCheckpoint(m.checkpoint) && (m.data === undefined || isData(m.data)) &&
        (m.imports === undefined || isImportList(m.imports))) ||
      (isIndex(m.revision) && (
        m.op === 'undo' || m.op === 'redo' || m.op === 'reset' || m.op === 'save' || m.op === 'saved' ||
        (m.op === 'export' && isExportFormat(m.format)) ||
        (m.op === 'open' && isData(m.data) && isName(m.name) && (m.recovered === undefined || typeof m.recovered === 'boolean')) ||
        (m.op === 'show' && isIndex(m.body) && (m.body as number) < MAX_BODIES && typeof m.visible === 'boolean') ||
        ((m.op === 'preview' || m.op === 'apply') && isEdit(m)) ||
        ((m.op === 'previewStack' || m.op === 'applyStack') && isIndex(m.moving) && isIndex(m.target) &&
          m.moving !== m.target && isStackSettings(m.settings)) ||
        (m.op === 'createShape' && isShapeEdit(m)) ||
        (m.op === 'importStep' && isName(m.importName) && isImportSource(m))
      ))) return m as RendererRequest
  throw new Error('Invalid geometry command or arguments.')
}
