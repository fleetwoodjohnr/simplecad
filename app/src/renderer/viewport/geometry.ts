// Turns an engine mesh into three.js geometry: one indexed surface for the faces, and line segments for the edges.

import { Box3, BufferAttribute, BufferGeometry, Sphere, Vector3 } from 'three'
import type { MeshData, Vec3 } from '@/shared/engine-protocol'

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
export function edgeGeometry(mesh: MeshData, ranges = [[0, mesh.edgeStart.length - 1]]): BufferGeometry {
  let segments = 0
  for (const [start, end] of ranges) {
    for (let edge = start; edge < end; edge++) segments += Math.max(0, mesh.edgeStart[edge + 1] - mesh.edgeStart[edge] - 1)
  }
  const out = new Float32Array(segments * 6)
  let o = 0
  for (const [start, end] of ranges) {
    for (let edge = start; edge < end; edge++) {
      for (let p = mesh.edgeStart[edge]; p + 1 < mesh.edgeStart[edge + 1]; p++) {
        out.set(mesh.edgePoints.subarray(p * 3, p * 3 + 6), o)
        o += 6
      }
    }
  }
  const geometry = new BufferGeometry()
  geometry.setAttribute('position', new BufferAttribute(out, 3))
  return geometry
}

/** Share the model's vertex buffers, supplying a separate index list per part. */
export function indexedGeometry(model: BufferGeometry, indices: Uint32Array) {
  const geometry = new BufferGeometry()
  const position = model.getAttribute('position')
  geometry.setAttribute('position', position)
  geometry.setAttribute('normal', model.getAttribute('normal'))
  geometry.setIndex(new BufferAttribute(indices, 1))
  const bounds = new Box3()
  const point = new Vector3()
  for (const index of indices) bounds.expandByPoint(point.fromBufferAttribute(position, index))
  geometry.boundingBox = bounds
  geometry.boundingSphere = bounds.getBoundingSphere(new Sphere())
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

/** What a planar face sweeps as it is pushed or pulled: its triangles as the moving cap, and a wall along each
 * boundary edge (an edge of one triangle only, once corners shared by several triangles are welded by position). */
export function facePrism(mesh: MeshData, face: number, normal: Vec3) {
  const first = mesh.faceTriStart[face] * 3
  const last = mesh.faceTriStart[face + 1] * 3
  const welded = new Map<string, number>()
  const weld = (vertex: number) => {
    const p = mesh.positions.subarray(vertex * 3, vertex * 3 + 3)
    const key = `${Math.round(p[0] * 1e5)},${Math.round(p[1] * 1e5)},${Math.round(p[2] * 1e5)}`
    if (!welded.has(key)) welded.set(key, vertex)
    return welded.get(key)!
  }
  // Directed edges keep the triangle's winding, so the walls face outward.
  const edges = new Map<string, [number, number] | null>()
  for (let i = first; i < last; i += 3) {
    const corners = [weld(mesh.indices[i]), weld(mesh.indices[i + 1]), weld(mesh.indices[i + 2])]
    for (let k = 0; k < 3; k++) {
      const a = corners[k]
      const b = corners[(k + 1) % 3]
      const key = a < b ? `${a},${b}` : `${b},${a}`
      edges.set(key, edges.has(key) ? null : [a, b])
    }
  }
  const boundary = [...edges.values()].filter((edge): edge is [number, number] => edge !== null)
  const triangles = (last - first) / 3
  const base = new Float32Array((triangles * 3 + boundary.length * 6) * 3)
  const moves = new Uint8Array(base.length / 3)
  let v = 0
  const put = (vertex: number, moving: boolean) => {
    base.set(mesh.positions.subarray(vertex * 3, vertex * 3 + 3), v * 3)
    moves[v++] = Number(moving)
  }
  for (let i = first; i < last; i++) put(mesh.indices[i], true)
  for (const [a, b] of boundary) {
    put(a, false); put(b, false); put(b, true)
    put(a, false); put(b, true); put(a, true)
  }
  const geometry = new BufferGeometry()
  const position = new BufferAttribute(base.slice(), 3)
  geometry.setAttribute('position', position)
  /** Sweep the cap `distance` along the normal. */
  const update = (distance: number) => {
    const array = position.array as Float32Array
    for (let i = 0; i < moves.length; i++) {
      for (let axis = 0; axis < 3; axis++) array[i * 3 + axis] = base[i * 3 + axis] + (moves[i] ? normal[axis] * distance : 0)
    }
    position.needsUpdate = true
    geometry.computeBoundingSphere()
  }
  return { geometry, update, boundaryEdges: boundary.length }
}
