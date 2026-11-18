import { MAX_EDITS, type Checkpoint, type DisplayShape, type ModelResult, type PullEdit, type Starter } from '../shared/engine-protocol'

export interface Shape {
  display(): DisplayShape
  pushPull(face: number, distance: number): Shape
}

export class EngineDocument {
  private starter: Starter = 'box'
  private shapes: Shape[]
  private edits: PullEdit[] = []
  private cursor = 0
  private revision = 0
  private previewResult?: { edit: PullEdit; shape: Shape }

  constructor(private create: (starter: Starter) => Shape) {
    this.shapes = [create(this.starter)]
  }

  model(): ModelResult {
    return {
      shape: this.shapes[this.cursor].display(),
      revision: this.revision,
      checkpoint: this.checkpoint()
    }
  }

  reset(starter: Starter, revision: number): ModelResult {
    this.checkRevision(revision)
    const shape = this.create(starter)
    return this.install({ starter, edits: [], cursor: 0 }, [shape])
  }

  restore(checkpoint: Checkpoint): ModelResult {
    if (this.revision !== 0) throw new Error('This session is already open.')
    return this.load(checkpoint)
  }

  open(checkpoint: Checkpoint, revision: number): ModelResult {
    this.checkRevision(revision)
    return this.load(checkpoint)
  }

  save(revision: number): Checkpoint {
    this.checkRevision(revision)
    return this.checkpoint()
  }

  private checkpoint(): Checkpoint {
    return { starter: this.starter, edits: this.edits, cursor: this.cursor }
  }

  private load(checkpoint: Checkpoint): ModelResult {
    const shapes = [this.create(checkpoint.starter)]
    for (const { face, distance } of checkpoint.edits) {
      shapes.push(shapes[shapes.length - 1].pushPull(face, distance))
    }
    return this.install(checkpoint, shapes)
  }

  preview(edit: PullEdit, revision: number): DisplayShape {
    this.checkRevision(revision)
    return this.prepare(edit).display()
  }

  apply({ face, distance }: PullEdit, revision: number): ModelResult {
    this.checkRevision(revision)
    if (distance === 0) return this.model()
    if (this.cursor >= MAX_EDITS) throw new Error('This modeling session has reached its history limit.')
    const shape = this.prepare({ face, distance })
    const result = shape.display()
    this.edits = [...this.edits.slice(0, this.cursor), { face, distance }]
    this.shapes = [...this.shapes.slice(0, this.cursor + 1), shape]
    this.cursor++
    this.advance()
    return {
      shape: result, revision: this.revision,
      checkpoint: this.checkpoint()
    }
  }

  step(direction: -1 | 1, revision: number): ModelResult {
    this.checkRevision(revision)
    const next = this.cursor + direction
    if (next >= 0 && next < this.shapes.length) {
      this.cursor = next
      this.advance()
    }
    return this.model()
  }

  private install(checkpoint: Checkpoint, shapes: Shape[]): ModelResult {
    this.shapes = shapes
    this.starter = checkpoint.starter
    this.edits = checkpoint.edits
    this.cursor = checkpoint.cursor
    this.advance()
    return this.model()
  }

  private advance() {
    this.revision++
    this.previewResult = undefined
  }

  private checkRevision(revision: number) {
    if (revision !== this.revision) throw new Error('The model changed. Select the face again.')
  }

  private prepare(edit: PullEdit): Shape {
    const cached = this.previewResult
    if (cached?.edit.face === edit.face && cached.edit.distance === edit.distance) return cached.shape
    const shape = this.shapes[this.cursor].pushPull(edit.face, edit.distance)
    this.previewResult = { edit, shape }
    return shape
  }
}
