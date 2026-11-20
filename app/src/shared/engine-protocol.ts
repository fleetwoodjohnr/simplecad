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
export interface StackSettings {
  operation: 'stack' | 'center' | 'concentric'
  offset: number
  rotation: number
  flip: boolean
  horizontal: 'left' | 'center' | 'right'
  vertical: 'bottom' | 'center' | 'top'
  horizontalGap: number
  verticalGap: number
}
export interface StackEdit { moving: number; target: number; settings: StackSettings }
export type StackStep = { moving: FacePick; target: FacePick; settings: StackSettings }
export type Step = (FacePick & PullEdit) | StackStep

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
  | { op: 'restore'; checkpoint: Checkpoint; data?: Uint8Array }
  /** `recovered`: work from a session that crashed, opened as unsaved. */
  | { op: 'open'; data: Uint8Array; name: string; revision: number; recovered?: boolean }
  | ({ op: 'preview' | 'apply'; revision: number } & PullEdit)
  | ({ op: 'previewStack' | 'applyStack'; revision: number } & StackEdit)
  | { op: 'show'; body: number; visible: boolean; revision: number }
  | { op: 'save' | 'saved'; revision: number }
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
  if (isEdit(value)) return isPick(value)
  if (!value || typeof value !== 'object') return false
  const step = value as Partial<StackStep>
  return isPick(step.moving) && isPick(step.target) && step.moving.body !== step.target.body && isStackSettings(step.settings)
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
    [s.offset, s.rotation, s.horizontalGap, s.verticalGap].every((n) => typeof n === 'number' && Number.isFinite(n) && Math.abs(n) <= 1e9)
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
      (m.op === 'restore' && isCheckpoint(m.checkpoint) && (m.data === undefined || isData(m.data))) ||
      (isIndex(m.revision) && (
        m.op === 'undo' || m.op === 'redo' || m.op === 'reset' || m.op === 'save' || m.op === 'saved' ||
        (m.op === 'open' && isData(m.data) && isName(m.name) && (m.recovered === undefined || typeof m.recovered === 'boolean')) ||
        (m.op === 'show' && isIndex(m.body) && (m.body as number) < MAX_BODIES && typeof m.visible === 'boolean') ||
        ((m.op === 'preview' || m.op === 'apply') && isEdit(m)) ||
        ((m.op === 'previewStack' || m.op === 'applyStack') && isIndex(m.moving) && isIndex(m.target) &&
          m.moving !== m.target && isStackSettings(m.settings))
      ))) return m as RendererRequest
  throw new Error('Invalid geometry command or arguments.')
}
