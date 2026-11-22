import { describe, expect, it } from 'vitest'
import { combine, EngineDocument, type PartDisplay, type Project, type Shape } from '@/engine/document'

/** A stand-in for an OCCT shape: `faces` one-triangle faces; a pull adds `distance` to the volume of that body. */
class FakeShape implements Shape {
  pulls: [number, number][] = []
  constructor(readonly volume: number, readonly faces = 2) {}
  display(): PartDisplay {
    const n = this.faces
    return {
      volume: this.volume,
      faces: Array.from({ length: n }, () => ({ planar: true, center: [0, 0, 0], anchor: [0, 0, 0], normal: [0, 0, 1], area: 1 })),
      positions: new Float32Array(9 * n),
      normals: new Float32Array(9 * n),
      indices: Uint32Array.from({ length: 3 * n }, (_, i) => i),
      faceTriStart: Uint32Array.from({ length: n + 1 }, (_, i) => i),
      edgePoints: new Float32Array(6),
      edgeStart: new Uint32Array([0, 2])
    }
  }
  pushPull(face: number, distance: number) {
    const next = new FakeShape(this.volume + distance, this.faces)
    next.pulls = [...this.pulls, [face, distance]]
    return next
  }
}

/** Stands in for the addon's Project: remembers the steps it was given. */
class FakeProject implements Project {
  sketchDraft(): never { throw new Error('Sketches are covered in the packaged app.') }
  solveSketch(): never { throw new Error('Sketch solving is covered in the packaged app.') }
  applySketchDraft(): never { throw new Error('Sketch editing is covered in the packaged app.') }
  applySketchSolid(): never { throw new Error('Sketch modeling is covered in the packaged app.') }
  addOffsetPlane(): never { throw new Error('Construction planes are covered in the packaged app.') }
  addBodyOperation(): never { throw new Error('Body operations are covered in the packaged app.') }
  addEdgeFinish(): never { throw new Error('Edge finishes are covered in the packaged app.') }
  groups() { return [] }
  group(): never { throw new Error('Groups are covered in the packaged app.') }
  ungroup(): never { throw new Error('Groups are covered in the packaged app.') }
  addConstruction(): never { throw new Error('Construction tools are covered in the packaged app.') }
  addArrangement(): never { throw new Error('Arrangements are covered in the packaged app.') }
  addSectionReplace(): never { throw new Error('Section Replace is covered in the packaged app.') }
  addFaceFeature(): never { throw new Error('Face tools are covered in the packaged app.') }
  constructor(readonly made: string[] = []) {}
  addShape(kind: string) {
    return { project: new FakeProject([...this.made, kind]), name: kind, shape: new FakeShape(3) }
  }
  importModel(_data: Uint8Array, _name: string, _format: string, _limit: number): ReturnType<Project['importModel']> {
    throw new Error('Not a STEP test')
  }
  pushPull(body: string, shape: Shape, face: number, distance: number) {
    return { project: new FakeProject([...this.made, `${body} ${face} ${distance}`]), shape: (shape as FakeShape).pushPull(face, distance) }
  }
  transform(_body: string, shape: Shape, _request: Parameters<Project['transform']>[2]) {
    return { project: this, shape }
  }
  place(...[moving, , request]: Parameters<Project['place']>) {
    const name = `${request.movingName} ${request.movingFace} to ${request.targetName} ${request.targetFace}`
    return { project: new FakeProject([...this.made, name]), shape: moving }
  }
  steps() {
    return this.made.map((name, index) => ({ id: String(index), name, kind: 'push_pull', state: 'ok' }))
  }
  historyDepth() { return { undo: 0, redo: 0 } }
  moveHistory(): never { throw new Error('No saved history in this fixture.') }
  repairInput(): never { throw new Error('No missing inputs in this fixture.') }
  editStepSettings(): never { throw new Error('No face features in this fixture.') }
  save(undone: number) {
    const kept = this.made.length - undone
    return new TextEncoder().encode([...this.made.slice(0, kept), ...this.made.slice(kept).map((s) => `redo ${s}`)].join(';'))
  }
}

describe('combine', () => {
  it('numbers faces, vertices and edges body by body', () => {
    const shown = combine([
      { body: 0, display: new FakeShape(10, 2).display() },
      { body: 2, display: new FakeShape(5, 3).display() }
    ])
    expect(shown.volume).toBe(15)
    expect(shown.faces.map((f) => f.body)).toEqual([0, 0, 2, 2, 2])
    expect(Array.from(shown.faceTriStart)).toEqual([0, 1, 2, 3, 4, 5])
    // The second body's first triangle points at its first vertex, after the first body's 6.
    expect(Array.from(shown.indices.subarray(6, 9))).toEqual([6, 7, 8])
    expect(Array.from(shown.edgeStart)).toEqual([0, 2, 4])
    expect(shown.ranges).toEqual([
      { body: 0, indices: [0, 6], edges: [0, 1] },
      { body: 2, indices: [6, 15], edges: [1, 2] }
    ])
  })
})

describe('EngineDocument', () => {
  const box = () => ({ bodies: [{ name: 'Box', shape: new FakeShape(6000, 6) }], visible: [true], notes: [], project: new FakeProject(['new']) })
  // A, then a hidden C, then B; and one body the file had no geometry for.
  const opened = () => ({
    bodies: [{ name: 'A', shape: new FakeShape(10, 2) }, { name: 'C', shape: new FakeShape(100, 1) },
      { name: 'B', shape: new FakeShape(5, 3) }, { name: 'Empty', shape: null }],
    visible: [true, false, true, true],
    notes: ['"Empty" could not be read from this file.'],
    project: new FakeProject()
  })

  it('numbers faces across the shown bodies only, and undoes and redoes', () => {
    const doc = new EngineDocument(box, opened)
    let model = doc.open(new Uint8Array(1), 'Atlas.scad3', doc.model().revision)
    expect(model.bodies).toEqual([
      { name: 'A', visible: true, empty: false }, { name: 'C', visible: false, empty: false },
      { name: 'B', visible: true, empty: false }, { name: 'Empty', visible: true, empty: true }])
    expect(model.shape.volume).toBe(15)
    expect(model.notes).toHaveLength(1)
    // Face 3 of the shown model is B's second face.
    model = doc.apply({ face: 3, distance: 2 }, model.revision)
    expect(model.shape.volume).toBe(17)
    expect(model.checkpoint.edits).toEqual([{ body: 2, face: 1, distance: 2 }])
    model = doc.step(-1, model.revision)
    expect(model.shape.volume).toBe(15)
    model = doc.step(1, model.revision)
    expect(model.shape.volume).toBe(17)
    expect(() => doc.apply({ face: 5, distance: 1 }, model.revision)).toThrow('Select a face')
    expect(() => doc.apply({ face: 0, distance: 1 }, model.revision - 1)).toThrow('The model changed')
  })

  it('shows a hidden body without touching the history, and renumbers the faces', () => {
    const doc = new EngineDocument(box, opened)
    let model = doc.open(new Uint8Array(1), 'Atlas.scad3', doc.model().revision)
    model = doc.setVisible(1, true, model.revision)
    expect(model.shape.volume).toBe(115)
    expect(model.shape.faces.map((f) => f.body)).toEqual([0, 0, 1, 2, 2, 2])
    expect(model.checkpoint.edits).toEqual([])
    expect(model.checkpoint.shown).toEqual([['C', true]])
  })

  it('places the current shape after undo, and replays the accepted placement after restart', () => {
    const doc = new EngineDocument(box, opened)
    let model = doc.open(new Uint8Array(1), 'Atlas.scad3', doc.model().revision)
    model = doc.apply({ face: 0, distance: 3 }, model.revision)
    model = doc.step(-1, model.revision)
    const edit = {
      moving: 0, target: 2, settings: {
        operation: 'stack', horizontal: 'center', vertical: 'center',
        horizontalGap: 0, verticalGap: 0, offset: 0, rotation: 0, flip: false
      }
    } as const
    expect(doc.preview(edit, model.revision).volume).toBe(15)
    expect(() => doc.apply(edit, model.revision - 1)).toThrow('The model changed')
    model = doc.apply(edit, model.revision)
    expect(model.steps.map((s) => s.name)).toEqual(['A 0 to B 0'])
    const restored = new EngineDocument(box, opened).restore(model.checkpoint, new Uint8Array(1))
    expect(restored.steps.map((s) => s.name)).toEqual(['A 0 to B 0'])
  })

  it('replays a checkpoint after a restart, whatever has been shown or hidden since', () => {
    const doc = new EngineDocument(box, opened)
    let model = doc.open(new Uint8Array(1), 'Atlas.scad3', doc.model().revision)
    model = doc.apply({ face: 3, distance: 2 }, model.revision)
    model = doc.setVisible(0, false, model.revision)
    const checkpoint = model.checkpoint
    expect(() => new EngineDocument(box, opened).restore(checkpoint)).toThrow('Open the project again')
    const restored = new EngineDocument(box, opened).restore(checkpoint, new Uint8Array(1))
    // A hidden, B pulled 2: only B's 7 is shown.
    expect(restored.shape.volume).toBe(7)
    expect(restored.bodies[0].visible).toBe(false)
  })

  it('saves the shown point of the history, with every step on the way to it', () => {
    const doc = new EngineDocument(box, opened)
    let model = doc.apply({ face: 1, distance: 3 }, doc.model().revision)
    expect(new TextDecoder().decode(doc.save(model.revision))).toBe('new;Box 1 3')
    model = doc.open(new Uint8Array(1), 'Atlas.scad3', model.revision)
    expect(model.unsaved).toBe(false)
    model = doc.apply({ face: 3, distance: 2 }, model.revision)
    model = doc.apply({ face: 0, distance: -1 }, model.revision)
    expect(model.unsaved).toBe(true)
    model = doc.step(-1, model.revision)
    expect(model.steps.map((s) => [s.name, s.undone])).toEqual([['B 1 2', false], ['A 0 -1', true]])
    expect(new TextDecoder().decode(doc.save(model.revision))).toBe('B 1 2;redo A 0 -1')
    // Handing over the file is not saving it: the window says when it was written.
    expect(doc.model().unsaved).toBe(true)
    model = doc.markSaved(model.revision)
    expect(model.unsaved).toBe(false)
    model = doc.step(1, model.revision)
    expect(model.unsaved).toBe(true)
  })
})
