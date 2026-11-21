import { describe, expect, it } from 'vitest'
import { edgeGeometry, facePrism, surfaceGeometry } from '@/renderer/viewport/geometry'
import type { MeshData } from '@/shared/engine-protocol'

// Two faces of one triangle each, and two edges: one straight (2 points), one bent (3 points).
const mesh: MeshData = {
  positions: new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 1, 0, 1, 0, 1, 1]),
  normals: new Float32Array([0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1]),
  indices: new Uint32Array([0, 1, 2, 3, 4, 5]),
  faceTriStart: new Uint32Array([0, 1, 2]),
  edgePoints: new Float32Array([0, 0, 0, 1, 0, 0, 0, 0, 1, 0.5, 0, 1, 1, 0, 1]),
  edgeStart: new Uint32Array([0, 2, 5])
}

describe('engine mesh to three.js', () => {
  it('gives each face its own draw group over its own triangles', () => {
    const g = surfaceGeometry(mesh)
    expect(g.groups).toEqual([
      { start: 0, count: 3, materialIndex: 0 },
      { start: 3, count: 3, materialIndex: 0 }
    ])
    expect(g.getAttribute('position').count).toBe(6)
  })

  it('turns each polyline into consecutive segments and never joins two edges', () => {
    const g = edgeGeometry(mesh)
    // 1 segment for the straight edge, 2 for the bent one: 3 segments, 6 points.
    expect(Array.from(g.getAttribute('position').array)).toEqual([
      0, 0, 0, 1, 0, 0, 0, 0, 1, 0.5, 0, 1, 0.5, 0, 1, 1, 0, 1
    ])
  })
})

/** A face on z = 0 from a grid of points (duplicated per triangle, as the engine sends them) and triangles over it. */
function planarFace(points: [number, number][], triangles: [number, number, number][]): MeshData {
  const positions = new Float32Array(triangles.flatMap((t) => t.flatMap((i) => [...points[i], 0])))
  return {
    positions, normals: new Float32Array(positions.length), indices: Uint32Array.from(positions.subarray(0, positions.length / 3), (_, i) => i),
    faceTriStart: new Uint32Array([0, triangles.length]), edgePoints: new Float32Array(), edgeStart: new Uint32Array([0])
  }
}

describe('push/pull prism', () => {
  it('walls a square face along its four sides only, not along the welded diagonal', () => {
    const prism = facePrism(planarFace([[0, 0], [1, 0], [1, 1], [0, 1]], [[0, 1, 2], [0, 2, 3]]), 0, [0, 0, 1])
    expect(prism.boundaryEdges).toBe(4)
    prism.update(2)
    const z = Array.from(prism.geometry.getAttribute('position').array).filter((_, i) => i % 3 === 2)
    // The cap's six corners all moved; each wall has three corners up and three down.
    expect(z.slice(0, 6)).toEqual([2, 2, 2, 2, 2, 2])
    expect(z.slice(6).filter((value) => value === 2).length).toBe(12)
  })

  it('walls both loops of a face with a hole', () => {
    // An outer square 0..3 and an inner square 1..2, as eight triangles.
    const points: [number, number][] = [[0, 0], [3, 0], [3, 3], [0, 3], [1, 1], [2, 1], [2, 2], [1, 2]]
    const ring: [number, number, number][] = [
      [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5], [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]
    ]
    expect(facePrism(planarFace(points, ring), 0, [0, 0, 1]).boundaryEdges).toBe(8)
  })
})
