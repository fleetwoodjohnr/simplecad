import { MAX_EDITS, type BodyInfo, type Checkpoint, type DisplayShape, type FaceInfo, type ModelResult, type PullEdit, type Source, type Step, type StepInfo } from '../shared/engine-protocol'

/** One body's display, as the addon returns it. */
export type PartDisplay = Omit<DisplayShape, 'faces'> & { faces: Omit<FaceInfo, 'body'>[] }

export interface Shape {
  display(): PartDisplay
}

/** A body at one point in the history. `shape` is null when the file had no readable geometry for it. */
export interface Body { name: string; shape: Shape | null }
/** The saved project at one point in the history (the addon's `Project`). */
export interface Project {
  pushPull(body: string, shape: Shape, face: number, distance: number): { project: Project; shape: Shape }
  /** The file, with the newest `undone` steps taken back and kept to redo. */
  save(undone: number): Uint8Array
  steps(): Omit<StepInfo, 'undone'>[]
}
/** A project as opened: its bodies, which are shown, what could not be read, and what saving edits. */
export interface Opened { bodies: Body[]; visible: boolean[]; notes: string[]; project: Project }
/** One point in the history. */
interface State { bodies: Body[]; project: Project }

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
  /** Each point of the history: `states[0]` as opened, `states[cursor]` is shown. */
  private states: State[]
  /** The history point last saved, so the window knows what is unsaved. */
  private saved = 0
  private edits: Step[] = []
  private cursor = 0
  private revision = 0
  private notes: string[] = []
  // Which bodies are shown: a view setting, outside the edit history. `opened` is as the file had it.
  private visible: boolean[]
  private opened: boolean[]
  private previewResult?: { step: Step; state: State }
  // A shape never changes, so its mesh is made once: undo, redo, previews and showing a body re-mesh only what changed.
  private displays = new WeakMap<Shape, PartDisplay>()
  private stepLists = new WeakMap<Project, Omit<StepInfo, 'undone'>[]>()

  constructor(
    private newBox: () => Opened,
    private read: (data: Uint8Array) => Opened
  ) {
    const box = newBox()
    this.states = [{ bodies: box.bodies, project: box.project }]
    this.visible = [...box.visible]
    this.opened = [...box.visible]
  }

  model(): ModelResult {
    const { bodies } = this.states[this.cursor]
    return {
      shape: this.show(bodies),
      bodies: bodies.map((b, i): BodyInfo => ({ name: b.name, visible: this.visible[i], empty: !b.shape })),
      unsaved: this.unsaved,
      revision: this.revision,
      checkpoint: this.checkpoint(),
      steps: this.steps(),
      notes: this.notes
    }
  }

  reset(revision: number): ModelResult {
    this.checkRevision(revision)
    return this.install({ kind: 'box' }, this.newBox(), [], 0)
  }

  /** After an engine restart: the window's last checkpoint, with the file's bytes when it came from one. */
  restore(checkpoint: Checkpoint, data?: Uint8Array): ModelResult {
    if (this.revision !== 0) throw new Error('This session is already open.')
    if (checkpoint.source.kind === 'file' && !data) throw new Error('Open the project again to keep editing it.')
    const opened = checkpoint.source.kind === 'box' ? this.newBox() : this.read(data!)
    for (const [body, shown] of checkpoint.shown) if (body < opened.visible.length) opened.visible[body] = shown
    const states: State[] = [{ bodies: opened.bodies, project: opened.project }]
    for (const step of checkpoint.edits) states.push(this.pushPull(states[states.length - 1], step))
    return this.install(checkpoint.source, opened, checkpoint.edits, checkpoint.cursor, states, checkpoint.saved)
  }

  /** A recovered file is work never saved: it opens unsaved. */
  open(data: Uint8Array, name: string, revision: number, recovered = false): ModelResult {
    this.checkRevision(revision)
    return this.install({ kind: 'file', name }, this.read(data), [], 0, undefined, recovered ? -1 : 0)
  }

  setVisible(body: number, visible: boolean, revision: number): ModelResult {
    this.checkRevision(revision)
    if (body >= this.visible.length) throw new Error('There is no such body.')
    this.visible[body] = visible
    this.advance()
    return this.model()
  }

  preview(edit: PullEdit, revision: number): DisplayShape {
    this.checkRevision(revision)
    return this.show(this.prepare(this.resolve(edit)).bodies)
  }

  /** The `.scad3` of the shown point of the history, the steps after it kept to redo. Nothing counts as saved until `markSaved`. */
  save(revision: number): Uint8Array {
    this.checkRevision(revision)
    return this.snapshot()
  }

  /** What `save` writes, whatever the window last saw: for autosave. */
  snapshot(): Uint8Array {
    const newest = this.states.length - 1
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

  apply(edit: PullEdit, revision: number): ModelResult {
    this.checkRevision(revision)
    if (edit.distance === 0) return this.model()
    if (this.cursor >= MAX_EDITS) throw new Error('This modeling session has reached its history limit.')
    const step = this.resolve(edit)
    const state = this.prepare(step)
    this.edits = [...this.edits.slice(0, this.cursor), step]
    this.states = [...this.states.slice(0, this.cursor + 1), state]
    // The saved point is gone from the history once a new step replaces what followed it.
    if (this.saved > this.cursor) this.saved = -1
    this.cursor++
    this.advance()
    return this.model()
  }

  step(direction: -1 | 1, revision: number): ModelResult {
    this.checkRevision(revision)
    const next = this.cursor + direction
    if (next >= 0 && next < this.states.length) {
      this.cursor = next
      this.advance()
    }
    return this.model()
  }

  private checkpoint(): Checkpoint {
    const shown = this.visible.flatMap((v, i): [number, boolean][] => v === this.opened[i] ? [] : [[i, v]])
    return { source: this.source, edits: this.edits, cursor: this.cursor, shown, saved: this.saved }
  }

  private install(source: Source, opened: Opened, edits: Step[], cursor: number,
    states: State[] = [{ bodies: opened.bodies, project: opened.project }], saved = 0): ModelResult {
    this.source = source
    this.states = states
    this.saved = saved
    this.edits = edits
    this.cursor = cursor
    this.visible = [...opened.visible]
    this.opened = opened.bodies.map((_, i) => opened.visible[i])
    this.notes = opened.notes
    this.advance()
    return this.model()
  }

  /** The newest state's steps: each step of this session adds one, so those past the cursor are its undone ones. */
  private steps(): StepInfo[] {
    const newest = this.states[this.states.length - 1].project
    let steps = this.stepLists.get(newest)
    if (!steps) this.stepLists.set(newest, steps = newest.steps())
    const shown = steps.length - (this.states.length - 1 - this.cursor)
    return steps.map((step, i) => ({ ...step, undone: i >= shown }))
  }

  private display(shape: Shape): PartDisplay {
    let display = this.displays.get(shape)
    if (!display) this.displays.set(shape, display = shape.display())
    return display
  }

  /** The bodies that are drawn and can be edited: shown, with geometry. */
  private shown(bodies: Body[]) {
    return bodies.flatMap((b, i) => this.visible[i] && b.shape ? [{ body: i, shape: b.shape }] : [])
  }

  private show(bodies: Body[]): DisplayShape {
    return combine(this.shown(bodies).map(({ body, shape }) => ({ body, display: this.display(shape) })))
  }

  /** Which body a face of the shown model belongs to, and its number within that body. */
  private resolve({ face, distance }: PullEdit): Step {
    let local = face
    for (const { body, shape } of this.shown(this.states[this.cursor].bodies)) {
      const count = this.display(shape).faces.length
      if (local < count) return { body, face: local, distance }
      local -= count
    }
    throw new Error('Select a face of the model.')
  }

  /** The next point of the history: the body pushed or pulled, and the step recorded for saving. */
  private pushPull({ bodies, project }: State, { body, face, distance }: Step): State {
    const shape = bodies[body]?.shape
    if (!shape) throw new Error('That body has no geometry to edit.')
    const next = project.pushPull(bodies[body].name, shape, face, distance)
    return { bodies: bodies.map((b, i) => i === body ? { name: b.name, shape: next.shape } : b), project: next.project }
  }

  private advance() {
    this.revision++
    this.previewResult = undefined
  }

  private checkRevision(revision: number) {
    if (revision !== this.revision) throw new Error('The model changed. Select the face again.')
  }

  private prepare(step: Step): State {
    const cached = this.previewResult?.step
    if (cached?.body === step.body && cached.face === step.face && cached.distance === step.distance) return this.previewResult!.state
    const state = this.pushPull(this.states[this.cursor], step)
    this.previewResult = { step, state }
    return state
  }
}
