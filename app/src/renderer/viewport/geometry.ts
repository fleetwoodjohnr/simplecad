// Turns an engine mesh into three.js geometry: one indexed surface for the faces, and line segments for the edges.

import { BufferAttribute, BufferGeometry } from 'three'
import type { MeshData } from '@/shared/engine-protocol'

export function surfaceGeometry(mesh: MeshData): BufferGeometry {
  const geometry = new BufferGeometry()
  geometry.setAttribute('position', new BufferAttribute(mesh.positions, 3))
  geometry.setAttribute('normal', new BufferAttribute(mesh.normals, 3))
  geometry.setIndex(new BufferAttribute(mesh.indices, 1))
  // One draw group per face, so a face can be given its own material (hover, selection) without rebuilding.
  for (let face = 0; face + 1 < mesh.faceTriStart.length; face++) {
    const start = mesh.faceTriStart[face] * 3
    geometry.addGroup(start, mesh.faceTriStart[face + 1] * 3 - start, 0)
  }
  geometry.computeBoundingSphere()
  return geometry
}

/** Every edge polyline as segment pairs, for `LineSegments`. */
export function edgeGeometry(mesh: MeshData): BufferGeometry {
  let segments = 0
  for (let edge = 0; edge + 1 < mesh.edgeStart.length; edge++) {
    segments += Math.max(0, mesh.edgeStart[edge + 1] - mesh.edgeStart[edge] - 1)
  }
  const out = new Float32Array(segments * 6)
  let o = 0
  for (let edge = 0; edge + 1 < mesh.edgeStart.length; edge++) {
    for (let p = mesh.edgeStart[edge]; p + 1 < mesh.edgeStart[edge + 1]; p++) {
      out.set(mesh.edgePoints.subarray(p * 3, p * 3 + 6), o)
      o += 6
    }
  }
  const geometry = new BufferGeometry()
  geometry.setAttribute('position', new BufferAttribute(out, 3))
  return geometry
}

/** Find the model face for a ray hit on the tessellation. */
export function faceAtTriangle(ranges: Uint32Array, triangle: number): number | undefined {
  if (!Number.isInteger(triangle) || triangle < 0 || triangle >= ranges[ranges.length - 1] || ranges.length < 2) return
  let low = 0
  let high = ranges.length - 1
  while (high - low > 1) {
    const mid = (low + high) >>> 1
    if (ranges[mid] <= triangle) low = mid
    else high = mid
  }
  return low
}
