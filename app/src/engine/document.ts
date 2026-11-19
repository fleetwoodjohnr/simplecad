import { MAX_EDITS, type Checkpoint, type DisplayShape, type FaceInfo, type ModelResult, type PullEdit, type Source } from '../shared/engine-protocol'

/** One body's display, as the addon returns it. */
export type PartDisplay = Omit<DisplayShape, 'bodies' | 'faces'> & { faces: Omit<FaceInfo, 'body'>[] }

export interface Shape {
  display(): PartDisplay
  pushPull(face: number, distance: number): Shape
}

export interface Body { name: string; shape: Shape }

/** Every body's mesh as one, faces numbered body by body, so the viewport and the edits see one model. */
export function combine(parts: { name: string; display: PartDisplay }[]): DisplayShape {
  const sum = (count: (d: PartDisplay) => number) => parts.reduce((n, p) => n + count(p.display), 0)
  const faceCount = sum((d) => d.faceTriStart.length - 1)
  const edgeCount = sum((d) => d.edgeStart.length - 1)
  const out: DisplayShape = {
    volume: 0, faces: [], bodies: parts.map((p) => p.name),
    positions: new Float32Array(sum((d) => d.positions.length)),
    normals: new Float32Array(sum((d) => d.normals.length)),
    indices: new Uint32Array(sum((d) => d.indices.length)),
    faceTriStart: new Uint32Array(faceCount + 1),
    edgePoints: new Float32Array(sum((d) => d.edgePoints.length)),
    edgeStart: new Uint32Array(edgeCount + 1)
  }
  let vertex = 0, index = 0, triangle = 0, face = 0, point = 0, edge = 0
  parts.forEach(({ display: d }, body) => {
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
  })
  out.faceTriStart[face] = triangle
  out.edgeStart[edge] = point
  return out
}

export class EngineDocument {
  private source: Source = { kind: 'box' }
  /** The bodies after each step: `states[0]` as opened, `states[cursor]` is shown. */
  private states: Body[][]
  private edits: PullEdit[] = []
  private cursor = 0
  private revision = 0
  private notes: string[] = []
  private previewResult?: { edit: PullEdit; bodies: Body[] }
  // A shape never changes, so its mesh is made once: undo, redo and previews re-mesh only what changed.
  private displays = new WeakMap<Shape, PartDisplay>()

  constructor(
    private newBox: () => Body[],
    private read: (data: Uint8Array) => { bodies: Body[]; notes: string[] }
  ) {
    this.states = [newBox()]
  }

  model(): ModelResult {
    return {
      shape: this.show(this.states[this.cursor]),
      revision: this.revision,
      checkpoint: this.checkpoint(),
      notes: this.notes
    }
  }

  reset(revision: number): ModelResult {
    this.checkRevision(revision)
    return this.install({ kind: 'box' }, this.newBox(), [], 0, [])
  }

  /** After an engine restart: the window's last checkpoint, with the file's bytes when it came from one. */
  restore(checkpoint: Checkpoint, data?: Uint8Array): ModelResult {
    if (this.revision !== 0) throw new Error('This session is already open.')
    if (checkpoint.source.kind === 'box') return this.replay(checkpoint, { bodies: this.newBox(), notes: [] })
    if (!data) throw new Error('Open the project again to keep editing it.')
    return this.replay(checkpoint, this.read(data))
  }

  open(data: Uint8Array, name: string, revision: number): ModelResult {
    this.checkRevision(revision)
    const { bodies, notes } = this.read(data)
    return this.install({ kind: 'file', name }, bodies, [], 0, notes)
  }

  preview(edit: PullEdit, revision: number): DisplayShape {
    this.checkRevision(revision)
    return this.show(this.prepare(edit))
  }

  apply(edit: PullEdit, revision: number): ModelResult {
    this.checkRevision(revision)
    if (edit.distance === 0) return this.model()
    if (this.cursor >= MAX_EDITS) throw new Error('This modeling session has reached its history limit.')
    const bodies = this.prepare(edit)
    this.edits = [...this.edits.slice(0, this.cursor), { face: edit.face, distance: edit.distance }]
    this.states = [...this.states.slice(0, this.cursor + 1), bodies]
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
    return { source: this.source, edits: this.edits, cursor: this.cursor }
  }

  private replay(checkpoint: Checkpoint, opened: { bodies: Body[]; notes: string[] }): ModelResult {
    const states = [opened.bodies]
    for (const edit of checkpoint.edits) states.push(this.pushPull(states[states.length - 1], edit))
    return this.install(checkpoint.source, opened.bodies, checkpoint.edits, checkpoint.cursor, opened.notes, states)
  }

  private install(source: Source, bodies: Body[], edits: PullEdit[], cursor: number, notes: string[], states = [bodies]): ModelResult {
    this.source = source
    this.states = states
    this.edits = edits
    this.cursor = cursor
    this.notes = notes
    this.advance()
    return this.model()
  }

  private display(shape: Shape): PartDisplay {
    let display = this.displays.get(shape)
    if (!display) this.displays.set(shape, display = shape.display())
    return display
  }

  private show(bodies: Body[]): DisplayShape {
    return combine(bodies.map((b) => ({ name: b.name, display: this.display(b.shape) })))
  }

  /** The bodies after pushing or pulling `edit.face`, numbered as `show` numbers faces. */
  private pushPull(bodies: Body[], { face, distance }: PullEdit): Body[] {
    let local = face
    for (const [i, body] of bodies.entries()) {
      const count = this.display(body.shape).faces.length
      if (local < count) {
        return bodies.map((b, j) => j === i ? { name: b.name, shape: b.shape.pushPull(local, distance) } : b)
      }
      local -= count
    }
    throw new Error('Select a face of the model.')
  }

  private advance() {
    this.revision++
    this.previewResult = undefined
  }

  private checkRevision(revision: number) {
    if (revision !== this.revision) throw new Error('The model changed. Select the face again.')
  }

  private prepare(edit: PullEdit): Body[] {
    const cached = this.previewResult
    if (cached?.edit.face === edit.face && cached.edit.distance === edit.distance) return cached.bodies
    const bodies = this.pushPull(this.states[this.cursor], edit)
    this.previewResult = { edit, bodies }
    return bodies
  }
}
