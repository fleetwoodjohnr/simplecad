import { describe, expect, it } from 'vitest'
import { combine, EngineDocument, type PartDisplay, type Shape } from '@/engine/document'
import { bodyEntries, legacyEntry } from '@/engine/project'

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

describe('combine', () => {
  it('numbers faces, vertices and edges body by body', () => {
    const shown = combine([
      { name: 'A', display: new FakeShape(10, 2).display() },
      { name: 'B', display: new FakeShape(5, 3).display() }
    ])
    expect(shown.bodies).toEqual(['A', 'B'])
    expect(shown.volume).toBe(15)
    expect(shown.faces.map((f) => f.body)).toEqual([0, 0, 1, 1, 1])
    expect(Array.from(shown.faceTriStart)).toEqual([0, 1, 2, 3, 4, 5])
    // B's first triangle points at B's first vertex, after A's 6.
    expect(Array.from(shown.indices.subarray(6, 9))).toEqual([6, 7, 8])
    expect(Array.from(shown.edgeStart)).toEqual([0, 2, 4])
  })
})

describe('EngineDocument', () => {
  const bodies = () => [{ name: 'A', shape: new FakeShape(10, 2) }, { name: 'B', shape: new FakeShape(5, 3) }]
  const opened = () => ({ bodies: bodies(), notes: ['1 hidden body is not shown.'] })

  it('sends a face number to the body that owns it, and undoes and redoes', () => {
    const doc = new EngineDocument(bodies, opened)
    let model = doc.open(new Uint8Array(1), 'Atlas.scad3', doc.model().revision)
    expect(model.notes).toEqual(['1 hidden body is not shown.'])
    model = doc.apply({ face: 3, distance: 2 }, model.revision)
    expect(model.shape.volume).toBe(17)
    expect(model.checkpoint).toEqual({ source: { kind: 'file', name: 'Atlas.scad3' }, edits: [{ face: 3, distance: 2 }], cursor: 1 })
    model = doc.step(-1, model.revision)
    expect(model.shape.volume).toBe(15)
    model = doc.step(1, model.revision)
    expect(model.shape.volume).toBe(17)
    expect(() => doc.apply({ face: 5, distance: 1 }, model.revision)).toThrow('Select a face')
    expect(() => doc.apply({ face: 0, distance: 1 }, model.revision - 1)).toThrow('The model changed')
  })

  it('replays a checkpoint after a restart, and needs the file for one that came from a file', () => {
    const checkpoint = { source: { kind: 'file' as const, name: 'Atlas.scad3' }, edits: [{ face: 0, distance: 1 }, { face: 2, distance: 4 }], cursor: 1 }
    expect(() => new EngineDocument(bodies, opened).restore(checkpoint)).toThrow('Open the project again')
    const model = new EngineDocument(bodies, opened).restore(checkpoint, new Uint8Array(1))
    expect(model.shape.volume).toBe(16)
    expect(model.checkpoint.edits).toHaveLength(2)
  })
})

describe('.scad3 entries', () => {
  const sha = 'a'.repeat(64)

  it('reads shown bodies from geometry/<sha>.brep and says what it leaves out', () => {
    const { entries, notes } = bodyEntries({
      format: 4,
      bodies: [{ id: '1', name: 'Box', visible: true }, { id: '2', name: 'Hexagon', visible: false }, { id: '3', name: 'Lid' }],
      geometry: { 1: { sha256: sha }, 2: { sha256: sha } }
    })
    expect(entries).toEqual([{ name: 'Box', entry: `geometry/${sha}.brep`, sha }])
    expect(notes).toEqual(['1 hidden body is not shown.', '"Lid" has no saved geometry; open it in SimpleCAD 0.4 to rebuild it.'])
  })

  it('reads a format 1 file by body name, as the Python app names the entries', () => {
    expect(legacyEntry('Lid / top')).toBe('bodies/Lid___top.brep')
    expect(legacyEntry('')).toBe('bodies/body.brep')
    const { entries, notes } = bodyEntries({ bodies: [{ name: 'a b' }, { name: 'a_b' }, { name: 'c' }] })
    expect(entries).toEqual([{ name: 'c', entry: 'bodies/c.brep' }])
    expect(notes).toHaveLength(2)
  })

  it('refuses a newer format', () => {
    expect(() => bodyEntries({ format: 5, bodies: [] })).toThrow('newer SimpleCAD')
  })
})
