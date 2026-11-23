import type { SketchDraftEdit, SketchPlane, SketchSolidEdit, SketchSolution } from './sketch'

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
  /** A Combine's other lost tools, each with its replacement body: all are repaired together. */
  more?: { missingInputId: string; replacementBody: number }[]
}
export interface SavedStepEdit { editStepId: string; changes: Record<string, string> }
export type SavedStep = SavedStepEdit & { replay?: { index: number; name: string; kind: string } }
export type Vec3 = [number, number, number]
type Expressions = [string, string, string]
export type Transform = { kind: 'move'; values: Expressions } |
  { kind: 'rotate'; values: Expressions; pivot?: Vec3 } |
  { kind: 'rotate'; values: [string]; axis: Vec3; pivot?: Vec3 }
export interface TransformEdit { body: number; transform: Transform }
export const CONSTRUCTIONS = ['midplane', 'plane_at_angle', 'plane_through_points', 'tangent_plane', 'construction_axis', 'construction_point'] as const
/** A construction plane, axis or point: picked faces and edges by display index, and its settings
 * (expressions, or a Plane through Points' three `points`). */
export interface ConstructionEdit {
  construction: typeof CONSTRUCTIONS[number]
  faces: number[]
  edges: number[]
  inputs: Record<string, string | Vec3[]>
}
/** Line bodies (by index) up along a world axis, with equal gaps or a fixed one. */
export interface ArrangeEdit { arrange: number[]; inputs: { mode: 'equal_gaps' | 'fixed_gap'; axis: 'x' | 'y' | 'z'; gap: string } }
export const ANCHORS = ['top_left', 'top', 'top_right', 'left', 'center', 'right', 'bottom_left', 'bottom', 'bottom_right'] as const
/** Section Replace: the replacement body's face (display index) set into the target's face, anchored and offset. */
export interface SectionReplaceEdit {
  sectionTarget: number
  sectionReplacement: number
  inputs: { moving_anchor: typeof ANCHORS[number]; target_anchor: typeof ANCHORS[number]; u: string; v: string; boundary: string; setback: string }
}
/** Organise bodies under one group (body indices), or dissolve a group by name. Neither changes geometry. */
export interface GroupEdit { group: number[] }
export const HINGE_TYPES = ['pin', 'knuckle', 'snap', 'living'] as const
/** SimpleCAD 0.4's settings for a hinge: its type, a named fit or a clearance, and the rest as expressions. */
export const HINGE_SETTINGS = ['clearance', 'knuckles', 'knuckle_od', 'pin', 'length', 'axial_clearance', 'open_max', 'flex', 'flex_length'] as const
/** A hinge along a picked display edge of the fixed part, turning another body (display index). */
export interface HingeEdit {
  hinge: { edge: number; swinging: number; inputs: { type: typeof HINGE_TYPES[number]; stop: boolean; flip: boolean } & Partial<Record<typeof HINGE_SETTINGS[number], string>> }
}
/** Drop every shown body onto the build plate, centre them on it too, or turn each to its best flat lay and then
 * drop them: one Move per body and turn, one undo step. */
export interface PlateEdit { plate: 'drop' | 'center' | 'orient' }
/** SimpleCAD 0.4's thread calibration: a P30 plug and four gauges, laid out on the plate, as one step. */
export interface ThreadGaugeEdit { threadGauges: 'P30' }
/** Move several bodies (display indices) by one offset, mm: a keyboard nudge, one Move each, one undo step. */
export interface MoveBodiesEdit { moveBodies: number[]; offset: Vec3 }
export interface UngroupEdit { ungroup: string }
/** Round or bevel edges of one body; `edges` index the display's edges across every shown body. */
export interface EdgeFinishEdit { edgeFinish: 'fillet' | 'chamfer'; edges: number[]; value: string }
export const BODY_OPERATIONS = ['mirror', 'rectangular_pattern', 'circular_pattern', 'scale', 'boolean', 'split'] as const
/** Mirror, pattern, scale or combine one body in place: expressions as strings, directions as vectors,
 * and a Combine's `tools` as body indices. */
export interface BodyOperationEdit {
  bodyOperation: typeof BODY_OPERATIONS[number]
  body: number
  inputs: Record<string, string | boolean | Vec3 | number[]>
}
/** A body; a face or edge by its display index; or a vertex by its body and where it was picked. */
export type MeasurePick = { kind: 'body'; body: number } | { kind: 'face'; face: number } | { kind: 'edge'; edge: number } |
  { kind: 'vertex'; body: number; point: Vec3 }
export interface Measurement { label: string; values: number[]; unit: string }
/** What a printer needs of a shown body: valid, watertight (no open edges), one solid. */
export interface BodyHealth { name: string; valid: boolean; openEdges: number; solids: number; thinnestWall?: number | null; shortEdges: number[] }
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
  | { faceOperation: 'offset_plane'; settings: { distance: string } }
  | { faceOperation: 'shell'; settings: { thickness: string } }
  /** Hexagonal vent holes through a flat face. */
  | { faceOperation: 'vent_cut'; settings: { across_flats: string; wall: string; margin: string } }
  /** Push/Pull on a round face: moves it along its radius, positive adding material. */
  | { faceOperation: 'resize_round'; settings: { delta: string } }
  /** A thread matching another one, cut into a round face of an existing part. */
  | { faceOperation: 'matching_thread'; settings: Pick<ThreadSettings, 'designation' | 'clearance' | 'form' | 'left_hand' | 'resize'> }
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
export type Step = (FacePick & (PullEdit | FaceEdit)) | StackStep | ShapeEdit | ImportRef | ReferenceRepair | SavedStep | TransformEdit | SketchDraftEdit | SketchSolidEdit | BodyOperationEdit | EdgeFinishEdit | GroupEdit | UngroupEdit | ConstructionEdit | ArrangeEdit | SectionReplaceEdit | PlateEdit | ThreadGaugeEdit | MoveBodiesEdit | HingeEdit

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
  moreMissing?: { name: string; objectId: string; face: boolean }[]
  faceSettings?: Record<string, string>
  shapeSettings?: Record<string, string>
  matchingThread?: Record<string, string>
  constructionPlane?: SketchPlane
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
  /** Each group with the bodies in it (a nested group's bodies included). */
  groups: { name: string; members: number[] }[]
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
  | ({ op: 'applyBodyOperation' | 'previewBodyOperation'; revision: number } & BodyOperationEdit)
  | ({ op: 'applyEdgeFinish' | 'previewEdgeFinish'; revision: number } & EdgeFinishEdit)
  | ({ op: 'applyHinge' | 'previewHinge'; revision: number } & HingeEdit)
  | ({ op: 'group'; revision: number } & GroupEdit)
  | ({ op: 'plate'; revision: number } & PlateEdit)
  | ({ op: 'threadGauges'; revision: number } & ThreadGaugeEdit)
  | ({ op: 'moveBodies'; revision: number } & MoveBodiesEdit)
  | { op: 'health'; revision: number }
  | { op: 'calibrate' }
  | { op: 'calibrationModel' }
  | ({ op: 'applyConstruction'; revision: number } & ConstructionEdit)
  | ({ op: 'applyArrange' | 'previewArrange'; revision: number } & ArrangeEdit)
  | ({ op: 'applySectionReplace' | 'previewSectionReplace'; revision: number } & SectionReplaceEdit)
  | ({ op: 'ungroup'; revision: number } & UngroupEdit)
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
  | { id: number; ok: true; result: ModelResult | DisplayShape | Uint8Array | Measurement[] | BodyHealth[] | string | SketchSolution }
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
    (edit.replacementFace === undefined || (isIndex(edit.replacementFace) && edit.replacementFace <= 0xffffffff)) &&
    (edit.more === undefined || (Array.isArray(edit.more) && edit.more.length < MAX_BODIES && edit.more.every((more) =>
      !!more && isName(more.missingInputId) && isIndex(more.replacementBody) && more.replacementBody < MAX_BODIES)))
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

function isBodyOperation(value: unknown): value is BodyOperationEdit {
  if (!value || typeof value !== 'object') return false
  const { bodyOperation, body, inputs } = value as Partial<BodyOperationEdit>
  if (!BODY_OPERATIONS.includes(bodyOperation!) || !isIndex(body) || body >= MAX_BODIES || !inputs || typeof inputs !== 'object') return false
  const entries = Object.entries(inputs)
  return entries.length <= 16 && entries.every(([key, value]) => /^[a-z_]{1,32}$/.test(key) && key !== 'body' && (key === 'tools'
    ? bodyOperation === 'boolean' && Array.isArray(value) && value.length > 0 && value.length < MAX_BODIES &&
      value.every((tool) => isIndex(tool) && tool < MAX_BODIES && tool !== body) :
    typeof value === 'boolean' || (typeof value === 'string' && value.trim().length > 0 && value.length < 256) ||
    (Array.isArray(value) && value.length === 3 && value.every((n) => typeof n === 'number' && Number.isFinite(n) && Math.abs(n) <= 1e9))))
}

function isEdgeFinish(value: unknown): value is EdgeFinishEdit {
  if (!value || typeof value !== 'object') return false
  const { edgeFinish, edges, value: amount } = value as Partial<EdgeFinishEdit>
  return (edgeFinish === 'fillet' || edgeFinish === 'chamfer') && Array.isArray(edges) && edges.length > 0 &&
    edges.length <= 4096 && edges.every((edge) => isIndex(edge) && edge <= 0xffffffff) &&
    typeof amount === 'string' && amount.trim().length > 0 && amount.length < 256
}

function isSectionReplace(value: unknown): value is SectionReplaceEdit {
  if (!value || typeof value !== 'object') return false
  const { sectionTarget, sectionReplacement, inputs } = value as Partial<SectionReplaceEdit>
  const expression = (text: unknown) => typeof text === 'string' && text.trim().length > 0 && text.length < 256
  return isIndex(sectionTarget) && sectionTarget <= 0xffffffff && isIndex(sectionReplacement) && sectionReplacement <= 0xffffffff &&
    sectionTarget !== sectionReplacement && !!inputs && ANCHORS.includes(inputs.moving_anchor) && ANCHORS.includes(inputs.target_anchor) &&
    expression(inputs.u) && expression(inputs.v) && expression(inputs.boundary) && expression(inputs.setback) && Object.keys(inputs).length === 6
}

function isArrange(value: unknown): value is ArrangeEdit {
  if (!value || typeof value !== 'object') return false
  const { arrange, inputs } = value as Partial<ArrangeEdit>
  return Array.isArray(arrange) && arrange.length >= 2 && arrange.length <= MAX_BODIES &&
    arrange.every((body) => isIndex(body) && body < MAX_BODIES) && new Set(arrange).size === arrange.length &&
    !!inputs && (inputs.mode === 'equal_gaps' || inputs.mode === 'fixed_gap') && ['x', 'y', 'z'].includes(inputs.axis) &&
    typeof inputs.gap === 'string' && inputs.gap.trim().length > 0 && inputs.gap.length < 256 && Object.keys(inputs).length === 3
}

function isConstruction(value: unknown): value is ConstructionEdit {
  if (!value || typeof value !== 'object') return false
  const { construction, faces, edges, inputs } = value as Partial<ConstructionEdit>
  const indices = (list: unknown) => Array.isArray(list) && list.length <= 2 && list.every((n) => isIndex(n) && n <= 0xffffffff)
  const point = (p: unknown) => Array.isArray(p) && p.length === 3 && p.every((n) => typeof n === 'number' && Number.isFinite(n) && Math.abs(n) <= 1e9)
  return CONSTRUCTIONS.includes(construction!) && indices(faces) && indices(edges) && !!inputs && typeof inputs === 'object' &&
    Object.entries(inputs).every(([key, entry]) => key === 'points'
      ? Array.isArray(entry) && entry.length === 3 && entry.every(point)
      : ['angle', 'around', 'x', 'y', 'z'].includes(key) && typeof entry === 'string' && entry.trim().length > 0 && entry.length < 256)
}

const isGroup = (value: unknown): value is GroupEdit => {
  const members = (value as Partial<GroupEdit> | null)?.group
  return Array.isArray(members) && members.length >= 2 && members.length <= MAX_BODIES &&
    members.every((body) => isIndex(body) && body < MAX_BODIES) && new Set(members).size === members.length
}
const isMoveBodies = (value: unknown): value is MoveBodiesEdit => {
  const { moveBodies, offset } = (value as Partial<MoveBodiesEdit> | null) ?? {}
  return Array.isArray(moveBodies) && moveBodies.length >= 1 && moveBodies.length <= MAX_BODIES &&
    moveBodies.every((body) => isIndex(body) && body < MAX_BODIES) && new Set(moveBodies).size === moveBodies.length &&
    Array.isArray(offset) && offset.length === 3 && offset.every((v) => typeof v === 'number' && Number.isFinite(v) && Math.abs(v) <= 1e6)
}
const isHinge = (value: unknown): value is HingeEdit => {
  const hinge = (value as Partial<HingeEdit> | null)?.hinge
  if (!hinge || !isIndex(hinge.edge) || !isIndex(hinge.swinging) || hinge.swinging >= MAX_BODIES || !hinge.inputs) return false
  const { type, stop, flip, ...settings } = hinge.inputs
  return HINGE_TYPES.includes(type) && typeof stop === 'boolean' && typeof flip === 'boolean' &&
    Object.entries(settings).every(([key, entry]) => (HINGE_SETTINGS as readonly string[]).includes(key) &&
      typeof entry === 'string' && entry.trim().length > 0 && entry.length < 256)
}
const isThreadGauges = (value: unknown): value is ThreadGaugeEdit => (value as Partial<ThreadGaugeEdit> | null)?.threadGauges === 'P30'
const isPlate = (value: unknown): value is PlateEdit => ['drop', 'center', 'orient'].includes((value as Partial<PlateEdit> | null)?.plate!)
const isUngroup = (value: unknown): value is UngroupEdit => isName((value as Partial<UngroupEdit> | null)?.ungroup)

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
  if (isShapeEdit(value) || isImportRef(value) || isReferenceRepair(value) || isSavedStep(value) || isTransformEdit(value) || isSketchDraft(value) || isSketchSolid(value) || isBodyOperation(value) || isEdgeFinish(value) || isGroup(value) || isPlate(value) || isThreadGauges(value) || isMoveBodies(value) || isHinge(value) || isUngroup(value) || isConstruction(value) || isArrange(value) || isSectionReplace(value)) return true
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
  if (edit.faceOperation === 'offset_plane') {
    return short(edit.settings.distance) && !!edit.settings.distance.trim()
  }
  if (edit.faceOperation === 'vent_cut') {
    const { across_flats: size, wall, margin } = edit.settings
    return [size, wall, margin].every((value) => short(value) && !!value.trim())
  }
  if (edit.faceOperation === 'resize_round') {
    return short(edit.settings.delta) && Number.isFinite(Number(edit.settings.delta)) && Number(edit.settings.delta) !== 0
  }
  if (edit.faceOperation === 'shell') {
    return short(edit.settings.thickness) && !!edit.settings.thickness.trim()
  }
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
  if (edit.faceOperation === 'matching_thread') {
    const { designation, clearance, form, left_hand: leftHand, resize } = edit.settings
    return short(designation) && !!designation.trim() && ['tight', 'normal', 'loose', 'very_loose'].includes(clearance) &&
      ['printed', 'iso'].includes(form) && ['true', 'false'].includes(leftHand) && ['true', 'false'].includes(resize)
  }
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
  if (pick.kind === 'vertex') {
    return isIndex(pick.body) && pick.body < MAX_BODIES && Array.isArray(pick.point) && pick.point.length === 3 &&
      pick.point.every((n) => typeof n === 'number' && Number.isFinite(n) && Math.abs(n) <= 1e9)
  }
  return pick.kind === 'body' ? isIndex(pick.body) && pick.body < MAX_BODIES :
    pick.kind === 'edge' ? isIndex(pick.edge) && pick.edge <= 0xffffffff :
    pick.kind === 'face' && isIndex(pick.face) && pick.face <= 0xffffffff
}
export function checkRendererRequest(data: unknown): RendererRequest {
  const m = data as Record<string, unknown> | null
  if (!m || typeof m !== 'object' || !isIndex(m.id)) throw new Error('Malformed engine request.')
  if (m.op === 'model' || m.op === 'crash' || m.op === 'calibrate' || m.op === 'calibrationModel' ||
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
        ((m.op === 'applyBodyOperation' || m.op === 'previewBodyOperation') && isBodyOperation(m)) ||
        ((m.op === 'applyEdgeFinish' || m.op === 'previewEdgeFinish') && isEdgeFinish(m)) ||
        ((m.op === 'applyHinge' || m.op === 'previewHinge') && isHinge(m)) ||
        (m.op === 'group' && isGroup(m)) || (m.op === 'plate' && isPlate(m)) || (m.op === 'threadGauges' && isThreadGauges(m)) || (m.op === 'moveBodies' && isMoveBodies(m)) || (m.op === 'health' && isIndex(m.revision)) || (m.op === 'ungroup' && isUngroup(m)) ||
        (m.op === 'applyConstruction' && isConstruction(m)) ||
        ((m.op === 'applyArrange' || m.op === 'previewArrange') && isArrange(m)) ||
        ((m.op === 'applySectionReplace' || m.op === 'previewSectionReplace') && isSectionReplace(m)) ||
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
