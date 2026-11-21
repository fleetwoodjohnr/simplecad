import { describe, expect, it } from 'vitest'
import { edgeGeometry, surfaceGeometry } from '@/renderer/viewport/geometry'
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
