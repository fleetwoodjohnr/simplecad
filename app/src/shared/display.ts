import type { DisplayShape, FaceInfo } from './engine-protocol'

/** One body's display, as the addon returns it. */
export type PartDisplay = Omit<DisplayShape, 'faces' | 'ranges'> & { faces: Omit<FaceInfo, 'body'>[] }

/** The shown bodies' meshes as one, faces numbered body by body, so the viewport and the edits see one model. */
export function combine(parts: { body: number; display: PartDisplay }[]): DisplayShape {
  const sum = (count: (d: PartDisplay) => number) => parts.reduce((n, p) => n + count(p.display), 0)
  const faceCount = sum((d) => d.faceTriStart.length - 1)
  const edgeCount = sum((d) => d.edgeStart.length - 1)
  const out: DisplayShape = {
    volume: 0, faces: [], ranges: [],
    positions: new Float32Array(sum((d) => d.positions.length)),
    normals: new Float32Array(sum((d) => d.normals.length)),
    indices: new Uint32Array(sum((d) => d.indices.length)),
    faceTriStart: new Uint32Array(faceCount + 1),
    edgePoints: new Float32Array(sum((d) => d.edgePoints.length)),
    edgeStart: new Uint32Array(edgeCount + 1)
  }
  let vertex = 0, index = 0, triangle = 0, face = 0, point = 0, edge = 0
  for (const { body, display: d } of parts) {
    out.ranges.push({ body, indices: [index, index + d.indices.length],
      edges: [edge, edge + d.edgeStart.length - 1] })
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

/** A model's parts on their way to the window: the display only of a part the window does not hold (it holds the
 * last model's). Returns them, and the keys the window will hold after. */
export function partsToSend(parts: { body: number; key: number; display: PartDisplay }[], held: ReadonlySet<number>) {
  return {
    sent: parts.map(({ body, key, display }) => held.has(key) ? { body, key } : { body, key, display }),
    held: new Set(parts.map(({ key }) => key))
  }
}

/** The window's side: the parts again, from those sent and those it holds; and what it holds after. */
export function partsReceived(sent: { body: number; key: number; display?: PartDisplay }[], held: ReadonlyMap<number, PartDisplay>) {
  const next = new Map<number, PartDisplay>()
  const parts = sent.map(({ body, key, display }) => {
    const part = display ?? held.get(key)
    if (!part) throw new Error('The window lost part of the model. Reopen the project.')
    next.set(key, part)
    return { body, display: part }
  })
  return { parts, held: next }
}
