import { MAX_BODIES, MAX_EDITS, MAX_PROJECT_BYTES, type BodyInfo, type Checkpoint, type DisplayShape, type FaceEdit, type FaceInfo, type FaceOperation, type FacePick, type ImportRef, type ImportRequest, type ImportSource, type ModelResult, type PullEdit, type ShapeEdit, type Source, type StackEdit, type StackSettings, type StackStep, type Step, type StepInfo } from '../shared/engine-protocol'

/** One body's display, as the addon returns it. */
export type PartDisplay = Omit<DisplayShape, 'faces'> & { faces: Omit<FaceInfo, 'body'>[] }

export interface Shape {
  display(): PartDisplay
}

/** A body at one point in the history. `shape` is null when the file had no readable geometry for it. */
export interface Body { name: string; shape: Shape | null; id?: string; visible?: boolean }
/** The saved project at one point in the history (the addon's `Project`). */
export interface Project {
  addShape(kind: string, values: Record<string, string>): { project: Project; name: string; shape: Shape }
  importModel(data: Uint8Array, name: string, format: string, limit: number, visible: Shape[]): { project: Project; bodies: Body[] }
  pushPull(body: string, shape: Shape, face: number, distance: number): { project: Project; shape: Shape }
  addFaceFeature(kind: FaceOperation['faceOperation'], shape: Shape, body: string, face: number, settings: FaceOperation['settings']): { project: Project; shape: Shape }
  place(moving: Shape, target: Shape, request: Omit<StackSettings, 'view'> & {
    movingName: string; movingFace: number; targetName: string; targetFace: number
    viewDirection?: number[]; viewUp?: number[]
  }): { project: Project; shape: Shape }
  /** The file, with the newest `undone` steps taken back and kept to redo. */
  save(undone: number): Uint8Array
  steps(): Omit<StepInfo, 'undone'>[]
  historyDepth(): { undo: number; redo: number }
  moveHistory(offset: number, shown: { id?: string; name: string; shape: Shape }[]): State
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
    volume: 0, faces: [],
    positions: new Float32Array(sum((d) => d.positions.length)),
    normals: new Float32Array(sum((d) => d.normals.length)),
    indices: new Uint32Array(sum((d) => d.indices.length)),
    faceTriStart: new Uint32Array(faceCount + 1),
    edgePoints: new Float32Array(sum((d) => d.edgePoints.length)),
    edgeStart: new Uint32Array(edgeCount + 1)
  }
  let vertex = 0, index = 0, triangle = 0, face = 0, point = 0, edge = 0
  for (const { body, display: d } of parts) {
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
      notes: this.notes
    }
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
    this.visibility.set(target.id ?? target.name, visible)
    this.advance()
    return this.model()
  }

  preview(edit: PullEdit | StackEdit | FaceEdit, revision: number): DisplayShape {
    this.checkRevision(revision)
    return this.show(this.prepare(this.resolve(edit)).bodies)
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
    if (this.cursor < 0 && newest > 0) {
      return this.moveInherited(this.states[newest], this.cursor - newest).project.save(0)
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

  apply(edit: PullEdit | StackEdit | FaceEdit | ShapeEdit | ImportRequest, revision: number): ModelResult {
    this.checkRevision(revision)
    if ('distance' in edit && edit.distance === 0) return this.model()
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
    const shown = bodies.flatMap(({ id, name, shape }) => shape ? [{ id, name, shape }] : [])
    const moved = project.moveHistory(offset, shown)
    return { ...moved, bodies: moved.bodies.map((body) => ({ ...body, shape: body.shape ?? null })) }
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

  private isVisible(body: Body): boolean {
    return this.visibility.get(body.id ?? body.name) ?? body.visible ?? true
  }

  private show(bodies: Body[]): DisplayShape {
    return combine(this.shown(bodies).map(({ body, shape }) => ({ body, display: this.display(shape) })))
  }

  /** Which body a face of the shown model belongs to, and its number within that body. */
  private resolve(edit: PullEdit | StackEdit | FaceEdit): (FacePick & (PullEdit | FaceEdit)) | StackStep {
    if ('distance' in edit) return { ...this.pick(edit.face), distance: edit.distance }
    if ('faceOperation' in edit) {
      const pick = this.pick(edit.face)
      return edit.faceOperation === 'text'
        ? { ...pick, faceOperation: 'text', settings: edit.settings }
        : { ...pick, faceOperation: 'face_texture', settings: edit.settings }
    }
    const moving = this.pick(edit.moving)
    const target = this.pick(edit.target)
    if (moving.body === target.body) throw new Error('Pick a face on each of two different parts.')
    return { moving, target, settings: edit.settings }
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

  /** The next point of the history: the body pushed or pulled, and the step recorded for saving. */
  private execute({ bodies, project }: State, step: Step, sources = this.imports): State {
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
    const body = 'target' in step ? step.moving.body : step.body
    const shape = bodies[body]?.shape
    if (!shape) throw new Error('That body has no geometry to edit.')
    let next
    if ('target' in step) next = this.place(project, bodies, shape, step)
    else if ('faceOperation' in step) {
      next = project.addFaceFeature(step.faceOperation, shape, bodies[body].name, step.face, step.settings)
    }
    else next = project.pushPull(bodies[body].name, shape, step.face, step.distance)
    return { bodies: bodies.map((b, i) => i === body ? { ...b, shape: next.shape } : b), project: next.project }
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
