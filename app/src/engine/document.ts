import type { SketchDraftEdit, SketchSolidEdit, SketchSolution } from '../shared/sketch'
import { overhangFraction, overhangSeverity } from '../shared/printing'
import { MAX_BODIES, MAX_EDITS, MAX_PROJECT_BYTES, type BodyInfo, type BodyOperationEdit, type EdgeFinishEdit, type GroupEdit, type PlateEdit, type ThreadGaugeEdit, type MoveBodiesEdit, type HingeEdit, type ClipJointEdit, type ConstructionEdit, type ArrangeEdit, type SectionReplaceEdit, type UngroupEdit, type Checkpoint, type DisplayShape, type FaceEdit, type FaceInfo, type FaceOperation, type FacePick, type ImportRef, type ImportRequest, type ImportSource, type ModelResult, type MeasurePick, type PullEdit, type ReferenceRepair, type SavedStepEdit, type SavedStep, type ShapeEdit, type Source, type StackEdit, type StackSettings, type StackStep, type Step, type StepInfo, type Transform, type TransformEdit, type Vec3 } from '../shared/engine-protocol'

/** The build plate and overhang limit of SimpleCAD 0.4's bundled default printer. */
const PLATE = [256, 256]
const MAX_OVERHANG = 50
/** best_print_orientation's six flat lays, as turns about X, Y and Z, in its order. */
const LAYS = [[0, 0, 0], [90, 0, 0], [180, 0, 0], [270, 0, 0], [0, 90, 0], [0, 270, 0]]
const SEVERITY = { ok: 0, note: 1, warning: 2 }

/** One body's display, as the addon returns it. */
export type PartDisplay = Omit<DisplayShape, 'faces' | 'ranges'> & { faces: Omit<FaceInfo, 'body'>[] }

export interface Shape {
  display(): PartDisplay
  /** The optimal bounding box: min x, y, z then max x, y, z. */
  bounds(): number[]
}

/** A body at one point in the history. `shape` is null when the file had no readable geometry for it. */
export interface Body { name: string; shape: Shape | null; id?: string; visible?: boolean }
/** The saved project at one point in the history (the addon's `Project`). */
export interface Project {
  sketchDraft(id: string): string
  solveSketch(recipe: string): SketchSolution
  applySketchDraft(request: SketchDraftEdit['sketchDraft'], shown: { id?: string; name: string; shape: Shape }[]): State
  applySketchSolid(request: SketchSolidEdit['sketchSolid'], shown: { id?: string; name: string; shape: Shape }[]): State
  addShape(kind: string, values: Record<string, string>, name?: string): { project: Project; name: string; shape: Shape }
  addOffsetPlane(body: string, face: number, distance: string, shown: { id?: string; name: string; shape: Shape }[]): State
  addBodyOperation(kind: string, body: string, inputs: string, shown: { id?: string; name: string; shape: Shape }[]): State
  groups(): { name: string; members: string[] }[]
  addArrangement(bodies: string[], inputs: string, shown: { id?: string; name: string; shape: Shape }[]): State
  addSectionReplace(target: { body: string; index: number }, replacement: { body: string; index: number }, inputs: string,
    shown: { id?: string; name: string; shape: Shape }[]): State
  addConstruction(kind: string, faces: { body: string; index: number }[], edges: { body: string; index: number }[],
    inputs: string, shown: { id?: string; name: string; shape: Shape }[]): State
  group(members: string[]): Project
  ungroup(name: string): Project
  addEdgeFinish(kind: string, body: string, edges: number[], value: string, shown: { id?: string; name: string; shape: Shape }[]): State
  addHinge(fixed: string, edge: number, swinging: string, inputs: string, shown: { id?: string; name: string; shape: Shape }[]): State
  addClipJoint(bodyA: string, faceA: number, bodyB: string, faceB: number, inputs: string, shown: { id?: string; name: string; shape: Shape }[]): State
  importModel(data: Uint8Array, name: string, format: string, limit: number, visible: Shape[]): { project: Project; bodies: Body[] }
  pushPull(body: string, shape: Shape, face: number, distance: number): { project: Project; shape: Shape }
  addFaceFeature(shape: Shape, request: { kind: FaceOperation['faceOperation']; body: string; face: number
    inputs: FaceOperation['settings']; position?: Vec3; target?: { name: string; shape: Shape } }): { project: Project; shape: Shape }
  place(moving: Shape, target: Shape, request: Omit<StackSettings, 'view'> & {
    movingName: string; movingFace: number; targetName: string; targetFace: number
    viewDirection?: number[]; viewUp?: number[]
  }): { project: Project; shape: Shape }
  transform(body: string, shape: Shape, request: Transform): { project: Project; shape: Shape }
  /** The file, with the newest `undone` steps taken back and kept to redo. */
  save(undone: number): Uint8Array
  steps(): Omit<StepInfo, 'undone'>[]
  historyDepth(): { undo: number; redo: number }
  moveHistory(offset: number, shown: { id?: string; name: string; shape: Shape }[]): State
  repairInput(request: { stepId: string; missingId: string; replacementId?: string; replacementName: string; face?: number
    more?: { missingId: string; replacementId?: string; replacementName: string }[] },
    shown: { id?: string; name: string; shape: Shape }[]): State
  editStepSettings(stepId: string, changes: Record<string, string>, shown: { id?: string; name: string; shape: Shape }[]): State
}
/** A project as opened: its bodies, which are shown, what could not be read, and what saving edits. */
export interface Opened { bodies: Body[]; visible: boolean[]; notes: string[]; project: Project }
/** One point in the history. */
interface State { bodies: Body[]; project: Project }

function initialState({ bodies, visible, project }: Opened): State {
  return { bodies: bodies.map((body, index) => ({ ...body, visible: visible[index] })), project }
}

/** The shown bodies' meshes as one, faces numbered body by body, so the viewport and the edits see one model. */
export function combine(parts: { body: number; display: PartDisplay }[]): DisplayShape {
  const sum = (count: (d: PartDisplay) => number) => parts.reduce((n, p) => n + count(p.display), 0)
  const faceCount = sum((d) => d.faceTriStart.length - 1)
  const edgeCount = sum((d) => d.edgeStart.length - 1)
  const out: DisplayShape = {
    volume: 0, faces: [], ranges: [],
    positions: new Float32Array(sum((d) => d.positions.length)),
    normals: new Float32Array(sum((d) => d.normals.length)),
    indices: new Uint32Array(sum((d) => d.indices.length)),
    faceTriStart: new Uint32Array(faceCount + 1),
    edgePoints: new Float32Array(sum((d) => d.edgePoints.length)),
    edgeStart: new Uint32Array(edgeCount + 1)
  }
  let vertex = 0, index = 0, triangle = 0, face = 0, point = 0, edge = 0
  for (const { body, display: d } of parts) {
    out.ranges.push({ body, indices: [index, index + d.indices.length],
      edges: [edge, edge + d.edgeStart.length - 1] })
    out.volume += d.volume
    out.positions.set(d.positions, vertex * 3)
    out.normals.set(d.normals, vertex * 3)
    for (const i of d.indices) out.indices[index++] = i + vertex
    const faces = d.faceTriStart.length - 1
    for (let f = 0; f < faces; f++) out.faceTriStart[face + f] = d.faceTriStart[f] + triangle
    for (const info of d.faces) out.faces.push({ ...info, body })
    out.edgePoints.set(d.edgePoints, point * 3)
    const edges = d.edgeStart.length - 1
    for (let e = 0; e < edges; e++) out.edgeStart[edge + e] = d.edgeStart[e] + point
    vertex += d.positions.length / 3
    triangle += d.faceTriStart[faces]
    face += faces
    point += d.edgePoints.length / 3
    edge += edges
  }
  out.faceTriStart[face] = triangle
  out.edgeStart[edge] = point
  return out
}

export class EngineDocument {
  private source: Source = { kind: 'box' }
  /** The point where editing began and the points created in this session. */
  private states: State[]
  /** The displayed point when moving outside the range of session edits. */
  private inherited?: State
  /** The history point last saved, so the window knows what is unsaved. */
  private saved: number | null = 0
  private edits: Step[] = []
  private imports = new Map<string, Uint8Array>()
  private cursor = 0
  private historyOffset = 0
  private revision = 0
  private notes: string[] = []
  // View overrides follow the saved body's ID, or the session-local name for new shapes.
  private visibility = new Map<string, boolean>()
  private savedBodyIds = new Set<string>()
  private previewResult?: { step: Step; state: State }
  // A shape never changes, so its mesh is made once: undo, redo, previews and showing a body re-mesh only what changed.
  private displays = new WeakMap<Shape, PartDisplay>()
  private stepLists = new WeakMap<Project, Omit<StepInfo, 'undone'>[]>()

  constructor(
    private newBox: () => Opened,
    private read: (data: Uint8Array) => Opened
  ) {
    this.states = [initialState(newBox())]
  }

  model(): ModelResult {
    const { bodies } = this.current
    return {
      shape: this.show(bodies),
      bodies: bodies.map((b): BodyInfo => ({ name: b.name, visible: this.isVisible(b), empty: !b.shape })),
      unsaved: this.unsaved,
      canUndo: this.canStep(-1),
      canRedo: this.canStep(1),
      revision: this.revision,
      checkpoint: this.checkpoint(),
      steps: this.steps(),
      notes: this.notes,
      groups: this.groups()
    }
  }

  /** Each group's bodies by index, a nested group's included (a member may name another group). */
  private groups() {
    const groups = this.current.project.groups()
    const byName = new Map(groups.map((group) => [group.name, group.members]))
    const index = new Map(this.current.bodies.map((body, i) => [body.name, i]))
    const bodiesOf = (members: string[], seen: Set<string>): number[] => members.flatMap((member) => {
      if (index.has(member)) return [index.get(member)!]
      if (seen.has(member) || !byName.has(member)) return []
      return bodiesOf(byName.get(member)!, new Set([...seen, member]))
    })
    return groups.map(({ name, members }) => ({ name, members: bodiesOf(members, new Set([name])) }))
  }

  reset(revision: number): ModelResult {
    this.checkRevision(revision)
    this.install({ kind: 'box' }, this.newBox())
    return this.model()
  }

  /** After an engine restart: the window's last checkpoint, with the file's bytes when it came from one. */
  restore(checkpoint: Checkpoint, data?: Uint8Array, imported: ImportSource[] = []): ModelResult {
    if (this.revision !== 0) throw new Error('This session is already open.')
    if (checkpoint.source.kind === 'file' && !data) throw new Error('Open the project again to keep editing it.')
    const opened = checkpoint.source.kind === 'box' ? this.newBox() : this.read(data!)
    const { cursor, historyOffset, saved, shown } = checkpoint
    const initial = initialState(opened)
    const base = historyOffset ? this.moveInherited(initial, historyOffset) : initial
    const states = [base]
    const sources = new Map(imported.map(({ importId, data }) => [importId, data]))
    for (const step of checkpoint.edits) states.push(this.execute(states[states.length - 1], step, sources))
    const inherited = states[cursor] ? undefined : this.moveInherited(base, cursor)
    this.install(checkpoint.source, opened)
    this.states = states
    this.inherited = inherited
    this.edits = checkpoint.edits
    this.cursor = cursor
    this.historyOffset = historyOffset
    this.saved = saved
    this.visibility = new Map(shown)
    this.imports = sources
    return this.model()
  }

  /** A recovered file is work never saved: it opens unsaved. */
  open(data: Uint8Array, name: string, revision: number, recovered = false): ModelResult {
    this.checkRevision(revision)
    this.install({ kind: 'file', name }, this.read(data), recovered ? null : 0)
    return this.model()
  }

  setVisible(body: number, visible: boolean, revision: number): ModelResult {
    this.checkRevision(revision)
    const target = this.current.bodies[body]
    if (!target) throw new Error('There is no such body.')
    this.visibility.set(this.visibilityKey(target), visible)
    this.advance()
    return this.model()
  }

  preview(edit: PullEdit | StackEdit | FaceEdit | SavedStepEdit | TransformEdit | SketchSolidEdit | ShapeEdit | BodyOperationEdit | EdgeFinishEdit | HingeEdit | ClipJointEdit | ArrangeEdit | SectionReplaceEdit, revision: number): DisplayShape {
    this.checkRevision(revision)
    const step = 'transform' in edit ? { body: edit.body, transform: edit.transform } :
      'bodyOperation' in edit ? { bodyOperation: edit.bodyOperation, body: edit.body, inputs: edit.inputs } :
      'edgeFinish' in edit ? { edgeFinish: edit.edgeFinish, edges: edit.edges, value: edit.value } :
      'hinge' in edit ? { hinge: edit.hinge } :
      'clipJoint' in edit ? { clipJoint: edit.clipJoint } :
      'arrange' in edit ? { arrange: edit.arrange, inputs: edit.inputs } :
      'sectionTarget' in edit ? { sectionTarget: edit.sectionTarget, sectionReplacement: edit.sectionReplacement, inputs: edit.inputs } :
      'editStepId' in edit ? this.resolveStepSettings(edit) :
      'sketchSolid' in edit ? { sketchSolid: edit.sketchSolid } :
      'shapeKind' in edit ? { shapeKind: edit.shapeKind, values: edit.values } : this.resolve(edit)
    return this.show(this.prepare(step).bodies)
  }

  readSketch(revision: number, id: string) {
    this.checkRevision(revision)
    return this.current.project.sketchDraft(id)
  }

  solveSketch(revision: number, recipe: string) {
    this.checkRevision(revision)
    return this.current.project.solveSketch(recipe)
  }

  measurementPicks(revision: number, selection: MeasurePick[]) {
    this.checkRevision(revision)
    return selection.map((entry) => {
      const pick = entry.kind === 'face' ? this.pick(entry.face) : entry.kind === 'edge' ? this.pickEdge(entry.edge) : entry
      const shape = this.current.bodies[pick.body]?.shape
      if (!shape) throw new Error('Select a body with geometry.')
      return { shape, face: 'face' in pick ? pick.face : undefined, edge: 'edge' in pick ? pick.edge : undefined,
        point: 'point' in pick ? pick.point : undefined }
    })
  }

  exportShapes(revision: number) {
    this.checkRevision(revision)
    const { bodies } = this.current
    return this.shown(bodies).map(({ body, shape }) => ({ name: bodies[body].name, shape }))
  }

  meshParts(revision: number) {
    return this.exportShapes(revision).map(({ name, shape }) => {
      const { positions, indices } = this.display(shape)
      return { name, positions, indices }
    })
  }

  /** The `.scad3` of the shown point of the history, the steps after it kept to redo. Nothing counts as saved until `markSaved`. */
  save(revision: number): Uint8Array {
    this.checkRevision(revision)
    return this.snapshot()
  }

  /** What `save` writes, whatever the window last saw: for autosave. */
  snapshot(): Uint8Array {
    const newest = this.states.length - 1
    if (newest > 0 && (this.cursor < 0 || this.edits.slice(this.cursor).some((step) => 'repairStepId' in step || 'editStepId' in step || 'sketchDraft' in step || 'sketchSolid' in step))) {
      return this.moveInherited({ ...this.states[newest], bodies: this.current.bodies }, this.cursor - newest).project.save(0)
    }
    if (this.inherited) return this.current.project.save(0)
    return this.states[newest].project.save(newest - this.cursor)
  }

  get unsaved() {
    return this.cursor !== this.saved
  }

  /** The file from `save` was written: the shown point of the history is the saved one. */
  markSaved(revision: number): ModelResult {
    this.checkRevision(revision)
    this.saved = this.cursor
    this.advance()
    return this.model()
  }

  apply(edit: PullEdit | StackEdit | FaceEdit | ShapeEdit | ImportRequest | ReferenceRepair | SavedStepEdit | TransformEdit | SketchDraftEdit | SketchSolidEdit | BodyOperationEdit | EdgeFinishEdit | GroupEdit | PlateEdit | ThreadGaugeEdit | MoveBodiesEdit | HingeEdit | ClipJointEdit | UngroupEdit | ConstructionEdit | ArrangeEdit | SectionReplaceEdit, revision: number): ModelResult {
    this.checkRevision(revision)
    if (('distance' in edit && edit.distance === 0) || ('editStepId' in edit && !Object.keys(edit.changes).length)) return this.model()
    const fork = this.inherited !== undefined
    if (!fork && this.cursor >= MAX_EDITS) throw new Error('This modeling session has reached its history limit.')
    let step: Step
    const retained = fork ? [] : this.edits.slice(0, this.cursor)
    const current = this.current
    const sources = new Map<string, Uint8Array>()
    for (const item of retained) {
      if ('importId' in item) sources.set(item.importId, this.imports.get(item.importId)!)
    }
    if ('importName' in edit) {
      if (sources.has(edit.importId)) throw new Error('This import is already in the project history.')
      const bytes = [...sources.values()].reduce((size, data) => size + data.byteLength, edit.data.byteLength)
      if (bytes > MAX_PROJECT_BYTES) throw new Error('This session has too much imported source data. Save and reopen the project to import more.')
      sources.set(edit.importId, edit.data)
      step = { importName: edit.importName, importId: edit.importId, importFormat: edit.importFormat ?? 'step',
        against: this.shown(current.bodies).map(({ body }) => body) }
    } else if ('repairStepId' in edit) {
      const { repairStepId, missingInputId, replacementBody, replacementFace, more } = edit
      const picked = replacementFace === undefined ? undefined : this.pick(replacementFace)
      if (picked && picked.body !== replacementBody) throw new Error('Select a face of the replacement body.')
      step = { repairStepId, missingInputId, replacementBody, replacementFace: picked?.face, ...(more?.length ? { more } : {}) }
    } else if ('editStepId' in edit) {
      step = this.resolveStepSettings(edit)
    } else if ('sketchDraft' in edit) {
      step = { sketchDraft: edit.sketchDraft }
    } else if ('sketchSolid' in edit) {
      step = { sketchSolid: edit.sketchSolid }
    } else if ('transform' in edit) {
      step = { body: edit.body, transform: edit.transform }
    } else if ('bodyOperation' in edit) {
      step = { bodyOperation: edit.bodyOperation, body: edit.body, inputs: edit.inputs }
    } else if ('hinge' in edit) {
      step = { hinge: edit.hinge }
    } else if ('clipJoint' in edit) {
      step = { clipJoint: edit.clipJoint }
    } else if ('edgeFinish' in edit) {
      step = { edgeFinish: edit.edgeFinish, edges: edit.edges, value: edit.value }
    } else if ('group' in edit) {
      step = { group: edit.group }
    } else if ('plate' in edit) {
      step = { plate: edit.plate }
    } else if ('threadGauges' in edit) {
      step = { threadGauges: edit.threadGauges }
    } else if ('moveBodies' in edit) {
      step = { moveBodies: edit.moveBodies, offset: edit.offset }
    } else if ('sectionTarget' in edit) {
      step = { sectionTarget: edit.sectionTarget, sectionReplacement: edit.sectionReplacement, inputs: edit.inputs }
    } else if ('arrange' in edit) {
      step = { arrange: edit.arrange, inputs: edit.inputs }
    } else if ('construction' in edit) {
      step = { construction: edit.construction, faces: edit.faces, edges: edit.edges, inputs: edit.inputs }
    } else if ('ungroup' in edit) {
      step = { ungroup: edit.ungroup }
    } else {
      step = 'shapeKind' in edit ? { shapeKind: edit.shapeKind, values: edit.values } : this.resolve(edit)
    }
    const state = 'importId' in step ? this.execute(current, step, sources) : this.prepare(step)
    this.imports = sources
    this.edits = [...retained, step]
    // The saved point is gone from the history once a new step replaces what followed it.
    if (this.saved !== null && this.saved > this.cursor) this.saved = null
    if (fork) {
      this.states = [current]
      this.historyOffset += this.cursor
      if (this.saved !== null) this.saved -= this.cursor
      this.cursor = 0
      this.inherited = undefined
    }
    this.states = [...this.states.slice(0, this.cursor + 1), state]
    this.cursor++
    this.advance()
    return this.model()
  }

  step(direction: -1 | 1, revision: number): ModelResult {
    this.checkRevision(revision)
    if (this.canStep(direction)) {
      const next = this.cursor + direction
      this.inherited = this.states[next] ? undefined : this.moveInherited(this.current, direction)
      this.cursor = next
      this.advance()
    }
    return this.model()
  }

  private checkpoint(): Checkpoint {
    return { source: this.source, edits: this.edits, cursor: this.cursor, historyOffset: this.historyOffset,
      shown: [...this.visibility], saved: this.saved }
  }

  private install(source: Source, opened: Opened, saved: number | null = 0) {
    this.source = source
    this.states = [initialState(opened)]
    this.inherited = undefined
    this.saved = saved
    this.edits = []
    this.imports.clear()
    this.cursor = 0
    this.historyOffset = 0
    this.visibility.clear()
    this.savedBodyIds = source.kind === 'file'
      ? new Set(opened.bodies.flatMap(({ id }) => id === undefined ? [] : [id]))
      : new Set()
    this.notes = opened.notes
    this.advance()
  }

  private get current(): State {
    return this.inherited ?? this.states[this.cursor]
  }

  private canStep(direction: -1 | 1): boolean {
    if (this.states[this.cursor + direction]) return true
    if (this.source.kind === 'box' || (direction === 1 && this.edits.length > 0 && this.cursor >= 0)) return false
    const { undo, redo } = this.current.project.historyDepth()
    return (direction < 0 ? undo : redo) > 0
  }

  private moveInherited({ bodies, project }: State, offset: number): State {
    return this.rebuiltState(project.moveHistory(offset, this.shapes(bodies)))
  }

  private shapes(bodies: Body[]) {
    return bodies.flatMap(({ id, name, shape }) => shape ? [{ id, name, shape }] : [])
  }

  private rebuiltState(state: State): State {
    return { ...state, bodies: state.bodies.map((body) => ({ ...body, shape: body.shape ?? null })) }
  }

  /** The newest state's steps: each step of this session adds one, so those past the cursor are its undone ones. */
  private steps(): StepInfo[] {
    const newest = (this.cursor >= this.states.length ? this.current : this.states[this.states.length - 1]).project
    let steps = this.stepLists.get(newest)
    if (!steps) this.stepLists.set(newest, steps = newest.steps())
    const active = this.current.project
    let activeSteps = this.stepLists.get(active)
    if (!activeSteps) this.stepLists.set(active, activeSteps = active.steps())
    const shown = new Map(activeSteps.map((step) => [step.id, step]))
    return steps.map((step) => {
      const current = shown.get(step.id)
      return { ...(current ?? step), undone: current === undefined }
    })
  }

  private display(shape: Shape): PartDisplay {
    let display = this.displays.get(shape)
    if (!display) this.displays.set(shape, display = shape.display())
    return display
  }

  /** The bodies that are drawn and can be edited: shown, with geometry. */
  private shown(bodies: Body[]) {
    return bodies.flatMap((b, i) => this.isVisible(b) && b.shape ? [{ body: i, shape: b.shape }] : [])
  }

  private visibilityKey(body: Body): string {
    return body.id && this.savedBodyIds.has(body.id) ? body.id : body.name
  }

  private isVisible(body: Body): boolean {
    return this.visibility.get(this.visibilityKey(body)) ?? body.visible ?? true
  }

  private show(bodies: Body[]): DisplayShape {
    return combine(this.shown(bodies).map(({ body, shape }) => ({ body, display: this.display(shape) })))
  }

  /** Which body a face of the shown model belongs to, and its number within that body. */
  private resolve(edit: PullEdit | StackEdit | FaceEdit): (FacePick & (PullEdit | FaceEdit)) | StackStep {
    if ('distance' in edit) return { ...this.pick(edit.face), distance: edit.distance }
    if ('faceOperation' in edit) {
      const pick = this.pick(edit.face)
      switch (edit.faceOperation) {
        case 'offset_plane': return { ...pick, faceOperation: 'offset_plane', settings: edit.settings }
        case 'shell': return { ...pick, faceOperation: 'shell', settings: edit.settings }
        case 'resize_round': return { ...pick, faceOperation: 'resize_round', settings: edit.settings }
        case 'vent_cut': return { ...pick, faceOperation: 'vent_cut', settings: edit.settings }
        case 'text': return { ...pick, faceOperation: 'text', settings: edit.settings }
        case 'face_texture': return { ...pick, faceOperation: 'face_texture', settings: edit.settings }
        case 'hole': return { ...pick, faceOperation: 'hole', settings: edit.settings, position: edit.position, untilBody: edit.untilBody }
        case 'thread': return { ...pick, faceOperation: 'thread', settings: edit.settings }
        case 'matching_thread': return { ...pick, faceOperation: 'matching_thread', settings: edit.settings }
      }
    }
    const moving = this.pick(edit.moving)
    const target = this.pick(edit.target)
    if (moving.body === target.body) throw new Error('Pick a face on each of two different parts.')
    return { moving, target, settings: edit.settings }
  }

  /** A display edge, as its body and that body's own edge index (the order the engine fingerprints them). */
  private pickEdge(edge: number): { body: number; edge: number } {
    let local = edge
    for (const { body, shape } of this.shown(this.current.bodies)) {
      const count = this.display(shape).edgeStart.length - 1
      if (local < count) return { body, edge: local }
      local -= count
    }
    throw new Error('Select an edge of the model.')
  }

  private pick(face: number): FacePick {
    let local = face
    for (const { body, shape } of this.shown(this.current.bodies)) {
      const count = this.display(shape).faces.length
      if (local < count) return { body, face: local }
      local -= count
    }
    throw new Error('Select a face of the model.')
  }

  private resolveStepSettings({ editStepId, changes }: SavedStepEdit): SavedStep {
    const steps = this.current.project.steps()
    const index = steps.findIndex((step) => step.id === editStepId && (step.faceSettings || step.shapeSettings))
    const step = steps[index]
    if (!step) throw new Error('This history step has no editable settings.')
    const fromFile = this.inherited || this.states[0].project.steps().some((step) => step.id === editStepId)
    // Replayed creations receive new native IDs. Use the recorded position only
    // for steps made in this session, checking their name and type as well.
    return { editStepId, changes, replay: fromFile ? undefined : { index, name: step.name, kind: step.kind } }
  }

  /** The next point of the history: the body pushed or pulled, and the step recorded for saving. */
  private execute({ bodies, project }: State, step: Step, sources = this.imports): State {
    if ('sketchDraft' in step) {
      return this.rebuiltState(project.applySketchDraft(step.sketchDraft, this.shapes(bodies)))
    }
    if ('sketchSolid' in step) {
      if (step.sketchSolid.kind !== 'path_pattern' && bodies.length >= MAX_BODIES) throw new Error('This project has reached its body limit.')
      return this.rebuiltState(project.applySketchSolid(step.sketchSolid, this.shapes(bodies)))
    }
    if ('editStepId' in step) {
      const steps = project.steps()
      const replacement = step.replay ? steps[step.replay.index] : undefined
      const id = steps.find((feature) => feature.id === step.editStepId)?.id ??
        (replacement && replacement.name === step.replay?.name && replacement.kind === step.replay?.kind ? replacement.id : undefined)
      if (!id) throw new Error('This history step is no longer available.')
      return this.rebuiltState(project.editStepSettings(id, step.changes, this.shapes(bodies)))
    }
    if ('repairStepId' in step) {
      const replacement = bodies[step.replacementBody]
      if (!replacement?.shape) throw new Error('Select a replacement body with geometry.')
      const more = step.more?.map(({ missingInputId, replacementBody }) => {
        const body = bodies[replacementBody]
        if (!body?.shape) throw new Error('Select a replacement body with geometry for each missing input.')
        return { missingId: missingInputId, replacementId: body.id, replacementName: body.name }
      })
      return this.rebuiltState(project.repairInput({ stepId: step.repairStepId, missingId: step.missingInputId,
        replacementId: replacement.id, replacementName: replacement.name, face: step.replacementFace, more }, this.shapes(bodies)))
    }
    if ('importId' in step) {
      const data = sources.get(step.importId)
      if (!data) throw new Error('The import source for this recovery step is missing.')
      const against = step.against.map((index) => {
        const shape = bodies[index]?.shape
        if (!shape) throw new Error('An import placement reference is missing.')
        return shape
      })
      const imported = project.importModel(data, step.importName, step.importFormat ?? 'step',
        MAX_BODIES - bodies.length, against)
      for (const body of imported.bodies) if (body.shape) this.display(body.shape)
      return { bodies: [...bodies, ...imported.bodies], project: imported.project }
    }
    if ('shapeKind' in step) {
      if (bodies.length >= MAX_BODIES) throw new Error('This project has reached its body limit.')
      const created = project.addShape(step.shapeKind, step.values)
      return { bodies: [...bodies, { name: created.name, shape: created.shape }], project: created.project }
    }
    if ('sectionTarget' in step) {
      const [target, replacement] = [step.sectionTarget, step.sectionReplacement].map((face) => {
        const pick = this.pick(face)
        return { body: bodies[pick.body].name, index: pick.face }
      })
      if (target.body === replacement.body) throw new Error('Pick the replacement on a different body from the target.')
      return this.rebuiltState(project.addSectionReplace(target, replacement, JSON.stringify(step.inputs), this.shapes(bodies)))
    }
    if ('arrange' in step) {
      const names = step.arrange.map((index) => {
        if (!bodies[index]?.shape) throw new Error('Choose bodies with geometry to arrange.')
        return bodies[index].name
      })
      return this.rebuiltState(project.addArrangement(names, JSON.stringify(step.inputs), this.shapes(bodies)))
    }
    if ('construction' in step) {
      const named = (pick: { body: number }, index: number) => ({ body: bodies[pick.body].name, index })
      const faces = step.faces.map((face) => { const pick = this.pick(face); return named(pick, pick.face) })
      const edges = step.edges.map((edge) => { const pick = this.pickEdge(edge); return named(pick, pick.edge) })
      return this.rebuiltState(project.addConstruction(step.construction, faces, edges, JSON.stringify(step.inputs), this.shapes(bodies)))
    }
    if ('group' in step) {
      const names = step.group.map((index) => {
        if (!bodies[index]) throw new Error('Select bodies of the model.')
        return bodies[index].name
      })
      return { bodies, project: project.group(names) }
    }
    if ('ungroup' in step) return { bodies, project: project.ungroup(step.ungroup) }
    if ('plate' in step) return this.onPlate(project, bodies, step.plate)
    if ('moveBodies' in step) {
      let next = bodies
      for (const body of step.moveBodies) {
        const shape = next[body]?.shape
        if (!shape) throw new Error('Select bodies with geometry to move.')
        const moved = project.transform(next[body].name, shape, { kind: 'move', values: [String(step.offset[0]), String(step.offset[1]), String(step.offset[2])] })
        project = moved.project
        next = next.map((b, i) => i === body ? { ...b, shape: moved.shape } : b)
      }
      return { bodies: next, project }
    }
    if ('threadGauges' in step) {
      // fit_panel.generate_threads: the plug and gauges it adds, with its names, sizes and layout.
      const at = (index: number) => ({ x: String(30 + 55 * (1 + index % 2)), y: String(30 + 55 * Math.floor(index / 2)) })
      const parts: [string, string, Record<string, string>][] = [
        ['matching_bolt', 'P30 calibration plug', { designation: 'P30', length: '10', thread_length: '10', form: 'printed', x: '30', y: '30' }],
        ...[0.4, 0.6, 0.8, 1].map((clearance, index): [string, string, Record<string, string>] => ['matching_nut',
          `P30 gauge ${clearance.toFixed(2)} mm`, { designation: 'P30', height: '10', clearance: String(clearance), form: 'printed', ...at(index) }])
      ]
      if (bodies.length + parts.length > MAX_BODIES) throw new Error('This project has reached its body limit.')
      let next = bodies
      for (const [kind, name, values] of parts) {
        const created = project.addShape(kind, values, name)
        project = created.project
        next = [...next, { name: created.name, shape: created.shape }]
      }
      return { bodies: next, project }
    }
    if ('clipJoint' in step) {
      const [a, b] = step.clipJoint.faces.map((face) => this.pick(face))
      if (a.body === b.body) throw new Error('Joining needs faces on two different parts.')
      return this.rebuiltState(project.addClipJoint(bodies[a.body].name, a.face, bodies[b.body].name, b.face,
        JSON.stringify(step.clipJoint.inputs), this.shapes(bodies)))
    }
    if ('hinge' in step) {
      const pick = this.pickEdge(step.hinge.edge)
      const swinging = bodies[step.hinge.swinging]
      if (!swinging?.shape || step.hinge.swinging === pick.body) throw new Error('Choose a different part to swing.')
      return this.rebuiltState(project.addHinge(bodies[pick.body].name, pick.edge, swinging.name, JSON.stringify(step.hinge.inputs),
        this.shapes(bodies)))
    }
    if ('edgeFinish' in step) {
      const picks = step.edges.map((edge) => this.pickEdge(edge))
      const body = picks[0].body
      if (picks.some((pick) => pick.body !== body)) throw new Error('Select edges of one body.')
      return this.rebuiltState(project.addEdgeFinish(step.edgeFinish, bodies[body].name, picks.map((pick) => pick.edge),
        step.value, this.shapes(bodies)))
    }
    if ('bodyOperation' in step) {
      const target = bodies[step.body]
      if (!target?.shape) throw new Error('That body has no geometry to edit.')
      const { tools, ...inputs } = step.inputs
      const named = Array.isArray(tools) ? { tools: tools.map((index) => {
        if (!bodies[index]?.shape) throw new Error('Choose tool bodies with geometry.')
        return bodies[index].name
      }) } : {}
      return this.rebuiltState(project.addBodyOperation(step.bodyOperation, target.name, JSON.stringify({ ...inputs, ...named }), this.shapes(bodies)))
    }
    const body = 'target' in step ? step.moving.body : step.body
    const shape = bodies[body]?.shape
    if (!shape) throw new Error('That body has no geometry to edit.')
    let next
    if ('target' in step) next = this.place(project, bodies, shape, step)
    else if ('transform' in step) {
      next = project.transform(bodies[body].name, shape, step.transform)
    } else if ('faceOperation' in step) {
      if (step.faceOperation === 'offset_plane') {
        return this.rebuiltState(project.addOffsetPlane(bodies[body].name, step.face, step.settings.distance, this.shapes(bodies)))
      }
      const hole = step.faceOperation === 'hole' ? step : undefined
      const target = hole?.untilBody === undefined ? undefined : bodies[hole.untilBody]
      if (hole?.untilBody !== undefined && (!target?.shape || hole.untilBody === body)) {
        throw new Error('Choose a different body to stop drilling at.')
      }
      next = project.addFaceFeature(shape, { kind: step.faceOperation, body: bodies[body].name, face: step.face,
        inputs: step.settings, position: hole?.position, target: target?.shape ? { name: target.name, shape: target.shape } : undefined })
    }
    else next = project.pushPull(bodies[body].name, shape, step.face, step.distance)
    return { bodies: bodies.map((b, i) => i === body ? { ...b, shape: next.shape } : b), project: next.project }
  }

  /** SimpleCAD 0.4's place_on_plate and center_on_plate, on its default printer's plate: one offset for everything shown, keeping its layout. */
  private onPlate(project: Project, bodies: Body[], how: PlateEdit['plate']) {
    if (!this.shown(bodies).length) throw new Error('There is nothing on the plate yet.')
    let next = [...bodies]
    if (how === 'orient') {
      // printing.best_print_orientation: of the six flat lays, the one with the least overhang, then the lowest.
      let turned = 0
      for (const { body, shape } of this.shown(bodies)) {
        let best: { lay: number[]; score: number } | undefined
        for (const lay of LAYS) {
          const candidate = lay.some(Boolean) ? project.transform(bodies[body].name, shape, { kind: 'rotate', values: lay.map(String) as [string, string, string] }).shape : shape
          const { positions, indices } = candidate.display()
          const box = candidate.bounds()
          const score = SEVERITY[overhangSeverity(overhangFraction(positions, indices, MAX_OVERHANG))] + (box[5] - box[2]) / 1000
          if (!best || score < best.score) best = { lay, score }
        }
        if (best!.lay.some(Boolean)) {
          const moved = project.transform(next[body].name, next[body].shape!, { kind: 'rotate', values: best!.lay.map(String) as [string, string, string] })
          project = moved.project
          next = next.map((b, i) => i === body ? { ...b, shape: moved.shape } : b)
          turned++
        }
      }
      if (!turned) throw new Error('Every part is already in the best of its six flat lays.')
      const offset = this.plateOffset(next, 'drop')
      if (offset.some((value) => Math.abs(value) >= 1e-9)) for (const { body } of this.shown(next)) moveBy(body, offset)
      return { bodies: next, project }
    }
    const offset = this.plateOffset(next, how)
    if (offset.every((value) => Math.abs(value) < 1e-9)) {
      throw new Error(how === 'drop' ? 'Everything already sits on the plate.' : 'Everything is already centred on the plate.')
    }
    for (const { body } of this.shown(next)) moveBy(body, offset)
    return { bodies: next, project }

    function moveBy(body: number, offset: number[]) {
      const moved = project.transform(next[body].name, next[body].shape!, { kind: 'move', values: [String(offset[0]), String(offset[1]), String(offset[2])] })
      project = moved.project
      next = next.map((b, i) => i === body ? { ...b, shape: moved.shape } : b)
    }
  }

  /** place_on_plate's or center_on_plate's one offset for everything shown, keeping its layout. */
  private plateOffset(bodies: Body[], how: 'drop' | 'center') {
    const low = [Infinity, Infinity, Infinity]
    const high = [-Infinity, -Infinity, -Infinity]
    for (const { shape } of this.shown(bodies)) {
      const box = shape.bounds()
      for (let axis = 0; axis < 3; axis++) {
        low[axis] = Math.min(low[axis], box[axis])
        high[axis] = Math.max(high[axis], box[axis + 3])
      }
    }
    return how === 'drop' ? [0, 0, -low[2]] : [0, 1].map((axis) => PLATE[axis] / 2 - (low[axis] + high[axis]) / 2).concat(-low[2])
  }

  private place(project: Project, bodies: Body[], shape: Shape, step: StackStep) {
    const target = bodies[step.target.body]
    if (!target?.shape) throw new Error('The target body has no geometry to place against.')
    const { view, ...settings } = step.settings
    return project.place(shape, target.shape, {
      ...settings, movingName: bodies[step.moving.body].name, movingFace: step.moving.face,
      targetName: target.name, targetFace: step.target.face, viewDirection: view?.direction, viewUp: view?.up
    })
  }

  private advance() {
    this.revision++
    this.previewResult = undefined
  }

  private checkRevision(revision: number) {
    if (revision !== this.revision) throw new Error('The model changed. Select the face again.')
  }

  private prepare(step: Exclude<Step, ImportRef>): State {
    const cached = this.previewResult
    if (cached && JSON.stringify(cached.step) === JSON.stringify(step)) return cached.state
    const state = this.execute(this.current, step)
    this.previewResult = { step, state }
    return state
  }
}
