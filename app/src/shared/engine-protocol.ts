import type { SketchDraftEdit, SketchSolidEdit, SketchSolution } from './sketch'

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

/** Half-open ranges in the combined display buffers. */
export interface BodyRange {
  body: number
  indices: [number, number]
  edges: [number, number]
}

/** Every shown body as one mesh; faces are numbered across them, body by body. */
export interface DisplayShape extends MeshData {
  volume: number
  faces: FaceInfo[]
  ranges: BodyRange[]
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
export interface ReferenceRepair {
  repairStepId: string
  missingInputId: string
  replacementBody: number
  replacementFace?: number
}
export interface SavedStepEdit { editStepId: string; changes: Record<string, string> }
export type SavedStep = SavedStepEdit & { replay?: { index: number; name: string; kind: string } }
export type Vec3 = [number, number, number]
type Expressions = [string, string, string]
export type Transform = { kind: 'move'; values: Expressions } |
  { kind: 'rotate'; values: Expressions; pivot?: Vec3 } |
  { kind: 'rotate'; values: [string]; axis: Vec3; pivot?: Vec3 }
export interface TransformEdit { body: number; transform: Transform }
export type MeasurePick = { kind: 'body'; body: number } | { kind: 'face'; face: number }
export interface Measurement { label: string; values: number[]; unit: string }
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
export interface TextSettings {
  text: string
  font_family: 'sans-serif' | 'serif' | 'monospace'
  mode: 'raised' | 'engraved'
  text_height: string
  depth: string
  offset_x: string
  offset_y: string
  rotation: string
}
export interface TextureSettings {
  pattern: string
  mode: 'raised' | 'recessed' | 'through'
  size: string
  spacing: string
  depth: string
  offset_u: string
  offset_v: string
  rotation: string
  edge_clearance: string
  hole_clearance: string
  text_clearance: string
  other_clearance: string
}
export interface ThreadSettings {
  designation: string
  clearance: 'tight' | 'normal' | 'loose' | 'very_loose'
  form: 'printed' | 'iso'
  from_end: 'top' | 'bottom'
  left_hand: 'true' | 'false'
  length: string
  resize: 'true' | 'false'
}
export interface HoleSettings extends Omit<ThreadSettings, 'length' | 'resize'> {
  style: 'simple' | 'counterbore' | 'countersink' | 'threaded'
  depth_mode: 'through' | 'blind' | 'to_object'
  diameter: string
  depth: string
  counterbore_diameter: string
  counterbore_depth: string
  countersink_diameter: string
  countersink_angle: string
  thread_length: string
}
export type FaceOperation =
  | { faceOperation: 'text'; settings: TextSettings }
  | { faceOperation: 'face_texture'; settings: TextureSettings }
  | { faceOperation: 'hole'; settings: HoleSettings; position?: Vec3; untilBody?: number }
  | { faceOperation: 'thread'; settings: ThreadSettings }
export type FaceEdit = { face: number } & FaceOperation
export interface ShapeEdit { shapeKind: string; values: Record<string, string> }
export type ImportFormat = 'step' | 'brep' | 'iges' | 'scad3' | 'gltf' | 'glb' | MeshFormat
export interface ModelImport { importName: string; data: Uint8Array; importFormat?: ImportFormat }
function isImportFormat(format: unknown): format is ImportFormat | undefined {
  return format === undefined || format === 'step' || format === 'brep' || format === 'iges' || format === 'scad3' ||
    format === 'gltf' || format === 'glb' || isMeshFormat(format)
}
export interface ImportSource { importId: string; data: Uint8Array }
export type ImportRequest = ModelImport & Pick<ImportSource, 'importId'>
export type ImportRef = Omit<ImportRequest, 'data'> & { against: number[] }
export type MeshFormat = 'stl' | 'obj' | '3mf'
export type ExportFormat = MeshFormat | 'step'
export const isMeshFormat = (format: unknown): format is MeshFormat =>
  format === 'stl' || format === 'obj' || format === '3mf'
export const isExportFormat = (format: unknown): format is ExportFormat =>
  format === 'step' || isMeshFormat(format)
export type StackStep = { moving: FacePick; target: FacePick; settings: StackSettings }
export type Step = (FacePick & (PullEdit | FaceEdit)) | StackStep | ShapeEdit | ImportRef | ReferenceRepair | SavedStep | TransformEdit | SketchDraftEdit | SketchSolidEdit

export interface Checkpoint {
  source: Source
  edits: Step[]
  /** The saved history position where this session's first edit was made, relative to open. */
  historyOffset: number
  /** Relative to historyOffset. Positions outside edits refer to the file's history. */
  cursor: number
  /** Bodies the user has hidden or shown since opening: identity and shown. */
  shown: [string, boolean][]
  /** The history point last saved, relative to historyOffset. */
  saved: number | null
}

export interface StepInfo {
  id: string
  name: string
  /** The step's type, as the file names it: `push_pull`, `box`… */
  kind: string
  /** As last built: `ok`, `suppressed`, `failed` or `needs_attention`. */
  state: string
  message?: string
  missingInput?: { name: string; objectId: string; face: boolean }
  faceSettings?: Record<string, string>
  shapeSettings?: Record<string, string>
  matchingThread?: Record<string, string>
  /** Undone in this session; redo brings it back. */
  undone: boolean
}

export interface ModelResult {
  shape: DisplayShape
  bodies: BodyInfo[]
  /** The shown point of the history is not the one last saved. */
  unsaved: boolean
  canUndo: boolean
  canRedo: boolean
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
  | ({ op: 'previewFace' | 'applyFace'; revision: number } & FaceEdit)
  | { op: 'measure'; revision: number; picks: MeasurePick[] }
  | ({ op: 'applyTransform' | 'previewTransform'; revision: number } & TransformEdit)
  | ({ op: 'createShape' | 'previewShape'; revision: number } & ShapeEdit)
  | ({ op: 'applySketchDraft'; revision: number } & SketchDraftEdit)
  | ({ op: 'previewSketchSolid' | 'applySketchSolid'; revision: number } & SketchSolidEdit)
  | { op: 'readSketch'; revision: number; sketchId: string }
  | { op: 'solveSketch'; revision: number; recipe: string }
  | ({ op: 'importModel'; revision: number } & ImportRequest)
  | ({ op: 'repairInput'; revision: number } & ReferenceRepair)
  | ({ op: 'previewSavedStep' | 'editSavedStep'; revision: number } & SavedStepEdit)
  | { op: 'show'; body: number; visible: boolean; revision: number }
  | { op: 'save' | 'saved'; revision: number }
  | { op: 'export'; revision: number; format: ExportFormat }
  | { op: 'undo' | 'redo'; revision: number }

export type RendererRequest = RendererCommand & { id: number }
export type EngineResponse =
  | { id: number; ok: true; result: ModelResult | DisplayShape | Uint8Array | Measurement[] | string | SketchSolution }
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

function isReferenceRepair(value: unknown): value is ReferenceRepair {
  if (!value || typeof value !== 'object') return false
  const edit = value as Partial<ReferenceRepair>
  return isName(edit.repairStepId) && isName(edit.missingInputId) && isIndex(edit.replacementBody) && edit.replacementBody < MAX_BODIES &&
    (edit.replacementFace === undefined || (isIndex(edit.replacementFace) && edit.replacementFace <= 0xffffffff))
}

function isSavedStep(value: unknown): value is SavedStep {
  if (!value || typeof value !== 'object') return false
  const { editStepId, changes, replay } = value as Partial<SavedStep>
  if (!isName(editStepId) || !changes || typeof changes !== 'object' || Array.isArray(changes)) return false
  const entries = Object.entries(changes)
  return entries.length <= 14 && entries.every(([key, value]) => /^[a-z_]{1,32}$/.test(key) && typeof value === 'string' && value.length <= 256) &&
    (replay === undefined || (!!replay && typeof replay === 'object' && isIndex(replay.index) && isName(replay.name) && isName(replay.kind)))
}

function isTransformEdit(value: unknown): value is TransformEdit {
  if (!value || typeof value !== 'object') return false
  const { body, transform } = value as Partial<TransformEdit>
  if (!isIndex(body) || body >= MAX_BODIES || !transform || typeof transform !== 'object') return false
  const vec3 = (value: unknown): value is Vec3 => Array.isArray(value) && value.length === 3 &&
    value.every((n) => typeof n === 'number' && Number.isFinite(n) && Math.abs(n) <= 1e9)
  const { kind, values, axis, pivot } = transform as Record<string, unknown>
  if (!Array.isArray(values) || !values.every((value) =>
    typeof value === 'string' && value.trim().length > 0 && value.length < 256)) return false
  if (kind === 'move') return values.length === 3 && axis === undefined && pivot === undefined
  return kind === 'rotate' && (pivot === undefined || vec3(pivot)) &&
    (axis === undefined ? values.length === 3 : values.length === 1 && vec3(axis) && Math.hypot(...axis) > 1e-9)
}

const isRecipe = (value: unknown): value is string =>
  typeof value === 'string' && value.length > 0 && value.length <= MAX_PROJECT_BYTES

function isSketchDraft(value: unknown): value is SketchDraftEdit {
  const draft = (value as Partial<SketchDraftEdit> | null)?.sketchDraft
  return !!draft && isName(draft.id) && typeof draft.creating === 'boolean' && isRecipe(draft.recipe)
}

function isSketchSolid(value: unknown): value is SketchSolidEdit {
  const solid = (value as Partial<SketchSolidEdit> | null)?.sketchSolid
  return !!solid && ['extrude', 'revolve', 'sweep', 'loft', 'path_pattern'].includes(solid.kind) &&
    Array.isArray(solid.profiles) && solid.profiles.length <= MAX_BODIES && solid.profiles.every(isName) &&
    isRecipe(solid.inputs)
}

function isStep(value: unknown): value is Step {
  if (isShapeEdit(value) || isImportRef(value) || isReferenceRepair(value) || isSavedStep(value) || isTransformEdit(value) || isSketchDraft(value) || isSketchSolid(value)) return true
  if (isEdit(value) || isFaceEdit(value)) return isPick(value)
  if (!value || typeof value !== 'object') return false
  const step = value as Partial<StackStep>
  return isPick(step.moving) && isPick(step.target) && step.moving.body !== step.target.body && isStackSettings(step.settings)
}

function isFaceEdit(value: unknown): value is FaceEdit {
  if (!value || typeof value !== 'object') return false
  const edit = value as Partial<FaceEdit>
  if (typeof edit.face !== 'number' || !isIndex(edit.face) || edit.face > 0xffffffff ||
      !edit.settings || typeof edit.settings !== 'object') return false
  const short = (entry: unknown) => typeof entry === 'string' && entry.length <= 256
  if (edit.faceOperation === 'text') {
    const { font_family: family, mode, text, text_height: height, depth, offset_x: x, offset_y: y, rotation } = edit.settings
    return (family === 'sans-serif' || family === 'serif' || family === 'monospace') &&
      (mode === 'raised' || mode === 'engraved') && [text, height, depth, x, y, rotation].every(short)
  }
  if (edit.faceOperation === 'face_texture') {
    const s = edit.settings
    return isName(s.pattern) && (s.mode === 'raised' || s.mode === 'recessed' || s.mode === 'through') &&
      [s.size, s.spacing, s.depth, s.offset_u, s.offset_v, s.rotation,
        s.edge_clearance, s.hole_clearance, s.text_clearance, s.other_clearance].every(short)
  }
  const hasThreadOptions = (s: Pick<ThreadSettings, 'designation' | 'clearance' | 'form' | 'from_end' | 'left_hand'>) =>
    short(s.designation) && ['tight', 'normal', 'loose', 'very_loose'].includes(s.clearance) &&
    ['printed', 'iso'].includes(s.form) && ['top', 'bottom'].includes(s.from_end) &&
    (s.left_hand === 'true' || s.left_hand === 'false')
  if (edit.faceOperation === 'thread') {
    return hasThreadOptions(edit.settings) && short(edit.settings.length) && ['true', 'false'].includes(edit.settings.resize)
  }
  if (edit.faceOperation === 'hole') {
    const h = edit.settings
    return hasThreadOptions(h) && ['simple', 'counterbore', 'countersink', 'threaded'].includes(h.style) &&
      ['through', 'blind', 'to_object'].includes(h.depth_mode) &&
      [h.diameter, h.depth, h.counterbore_diameter, h.counterbore_depth,
        h.countersink_diameter, h.countersink_angle, h.thread_length].every(short) &&
      (h.depth_mode === 'to_object' ? isIndex(edit.untilBody) && edit.untilBody < MAX_BODIES : edit.untilBody === undefined) &&
      (edit.position === undefined || (Array.isArray(edit.position) && edit.position.length === 3 &&
        edit.position.every((value) => typeof value === 'number' && Number.isFinite(value))))
  }
  return false
}

function isImportRef(value: unknown): value is ImportRef {
  if (!value || typeof value !== 'object') return false
  const step = value as Partial<ImportRef>
  return isName(step.importName) && isName(step.importId) &&
    isImportFormat(step.importFormat) &&
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
  return entries.length <= 14 && entries.every(([key, expression]) => /^[a-z][a-z_0-9]{0,30}$/.test(key) &&
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
  const offset = (n: unknown) => typeof n === 'number' && Number.isInteger(n) && Math.abs(n) <= 0x7fffffff
  return isSource(model.source) && Array.isArray(model.edits) && model.edits.length <= MAX_EDITS &&
    model.edits.every(isStep) && offset(model.historyOffset) && offset(model.cursor) &&
    (model.source.kind === 'file' || (model.historyOffset === 0 && isIndex(model.cursor) && model.cursor <= model.edits.length)) &&
    (!model.edits.length || model.cursor! <= model.edits.length) &&
    Array.isArray(model.shown) && model.shown.length <= MAX_BODIES &&
    model.shown.every((s) => Array.isArray(s) && s.length === 2 && isName(s[0]) && typeof s[1] === 'boolean') &&
    (model.saved === null || offset(model.saved))
}

function isMeasurePick(value: unknown): value is MeasurePick {
  if (!value || typeof value !== 'object') return false
  const pick = value as Partial<MeasurePick>
  return pick.kind === 'body' ? isIndex(pick.body) && pick.body < MAX_BODIES :
    pick.kind === 'face' && isIndex(pick.face) && pick.face <= 0xffffffff
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
        (m.op === 'measure' && Array.isArray(m.picks) && m.picks.length <= MAX_BODIES && m.picks.every(isMeasurePick)) ||
        (m.op === 'open' && isData(m.data) && isName(m.name) && (m.recovered === undefined || typeof m.recovered === 'boolean')) ||
        (m.op === 'show' && isIndex(m.body) && (m.body as number) < MAX_BODIES && typeof m.visible === 'boolean') ||
        ((m.op === 'preview' || m.op === 'apply') && isEdit(m)) ||
        ((m.op === 'previewFace' || m.op === 'applyFace') && isFaceEdit(m)) ||
        ((m.op === 'previewStack' || m.op === 'applyStack') && isIndex(m.moving) && isIndex(m.target) &&
          m.moving !== m.target && isStackSettings(m.settings)) ||
        ((m.op === 'applyTransform' || m.op === 'previewTransform') && isTransformEdit(m)) ||
        ((m.op === 'createShape' || m.op === 'previewShape') && isShapeEdit(m)) ||
        (m.op === 'readSketch' && isName(m.sketchId)) ||
        (m.op === 'solveSketch' && isRecipe(m.recipe)) ||
        (m.op === 'applySketchDraft' && isSketchDraft(m)) ||
        ((m.op === 'previewSketchSolid' || m.op === 'applySketchSolid') && isSketchSolid(m)) ||
        (m.op === 'repairInput' && isReferenceRepair(m)) ||
        ((m.op === 'previewSavedStep' || m.op === 'editSavedStep') && isSavedStep(m) && m.replay === undefined) ||
        (m.op === 'importModel' && isName(m.importName) && isImportFormat(m.importFormat) && isImportSource(m))
      ))) return m as RendererRequest
  throw new Error('Invalid geometry command or arguments.')
}
