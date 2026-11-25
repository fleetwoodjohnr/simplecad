// SimpleCAD 0.2.0's magnets, as pure functions: the move arrows' (ui/tools/modeling.py `_snap_translation`), the
// rotate rings' detents (ui/viewport/gizmo.py `RotationSnapState`) and Measure's point snaps (kernel/snapping.py).
import type { DisplayShape } from '@/shared/engine-protocol'

export type Vec3 = [number, number, number]
export type SnapKind = 'vertex' | 'center' | 'midpoint' | 'face' | 'edge' | 'surface'
/** A point a drag or a measure can snap to, on part `body`. */
export interface SnapPoint { point: Vec3; kind: SnapKind; body: number }

export const kindLabel: Record<SnapKind, string> = {
  vertex: 'corner', center: 'centre', midpoint: 'midpoint', face: 'face centre', edge: 'edge', surface: 'surface'
}
/** Measure's snap priorities: a corner beats a midpoint unless the midpoint is more than 20 px nearer. */
export const priority: Record<SnapKind, number> = { vertex: 40, center: 30, midpoint: 20, face: 10, edge: 0, surface: -10 }
/** Parts larger than this get bounding-box magnets only (0.2.0's "full" snap tier), so a big import stays quick. */
const MAX_FACES = 80
const MAX_EDGES = 240

/** The low and high corners of part `body`'s display mesh. */
export function bodyBox(shape: DisplayShape, body: number): { low: Vec3; high: Vec3 } | undefined {
  const range = shape.ranges.find((entry) => entry.body === body)
  if (!range) return
  const low: Vec3 = [Infinity, Infinity, Infinity]
  const high: Vec3 = [-Infinity, -Infinity, -Infinity]
  for (let i = range.indices[0]; i < range.indices[1]; i++) {
    const v = shape.indices[i] * 3
    for (let a = 0; a < 3; a++) {
      const value = shape.positions[v + a]
      if (value < low[a]) low[a] = value
      if (value > high[a]) high[a] = value
    }
  }
  return Number.isFinite(low[0]) ? { low, high } : undefined
}

const point = (shape: DisplayShape, p: number): Vec3 => [shape.edgePoints[p * 3], shape.edgePoints[p * 3 + 1], shape.edgePoints[p * 3 + 2]]
const distance = (a: Vec3, b: Vec3) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2])

/** The centre of the circle through three points, or undefined when they are in a line. */
export function circleCentre(a: Vec3, b: Vec3, c: Vec3): Vec3 | undefined {
  const ab = [b[0] - a[0], b[1] - a[1], b[2] - a[2]]
  const ac = [c[0] - a[0], c[1] - a[1], c[2] - a[2]]
  const n = [ab[1] * ac[2] - ab[2] * ac[1], ab[2] * ac[0] - ab[0] * ac[2], ab[0] * ac[1] - ab[1] * ac[0]]
  const nn = n[0] ** 2 + n[1] ** 2 + n[2] ** 2
  if (nn < 1e-12 * (ab[0] ** 2 + ab[1] ** 2 + ab[2] ** 2) * (ac[0] ** 2 + ac[1] ** 2 + ac[2] ** 2)) return
  const ab2 = ab[0] ** 2 + ab[1] ** 2 + ab[2] ** 2
  const ac2 = ac[0] ** 2 + ac[1] ** 2 + ac[2] ** 2
  // a + ((|ac|² n × ab) + (|ab|² ac × n)) / (2 |n|²)
  const nxab = [n[1] * ab[2] - n[2] * ab[1], n[2] * ab[0] - n[0] * ab[2], n[0] * ab[1] - n[1] * ab[0]]
  const acxn = [ac[1] * n[2] - ac[2] * n[1], ac[2] * n[0] - ac[0] * n[2], ac[0] * n[1] - ac[1] * n[0]]
  return [0, 1, 2].map((i) => a[i] + (ac2 * nxab[i] + ab2 * acxn[i]) / (2 * nn)) as Vec3
}

/** Part `body`'s named snaps: corners, edge midpoints, circle and arc centres, and flat faces' centres. None for a
 * part over 80 faces or 240 edges. */
export function snapPoints(shape: DisplayShape, body: number): SnapPoint[] {
  const range = shape.ranges.find((entry) => entry.body === body)
  if (!range) return []
  const faces = shape.faces.filter((face) => face.body === body)
  if (faces.length > MAX_FACES || range.edges[1] - range.edges[0] > MAX_EDGES) return []
  const out: SnapPoint[] = []
  const add = (p: Vec3, kind: SnapKind) => {
    if (!out.some((s) => s.kind === kind && distance(s.point, p) < 1e-6)) out.push({ point: p, kind, body })
  }
  for (let edge = range.edges[0]; edge < range.edges[1]; edge++) {
    const [first, end] = [shape.edgeStart[edge], shape.edgeStart[edge + 1]]
    if (end - first < 2) continue
    const points = Array.from({ length: end - first }, (_, k) => point(shape, first + k))
    const closed = distance(points[0], points.at(-1)!) < 1e-6
    if (!closed) {
      add(points[0], 'vertex')
      add(points.at(-1)!, 'vertex')
    }
    // Halfway along the edge, by length.
    const lengths = points.slice(1).map((p, k) => distance(points[k], p))
    const total = lengths.reduce((sum, l) => sum + l, 0)
    let walked = 0
    for (let k = 0; k < lengths.length; k++) {
      if (walked + lengths[k] >= total / 2) {
        const t = lengths[k] ? (total / 2 - walked) / lengths[k] : 0
        add(points[k].map((v, i) => v + (points[k + 1][i] - v) * t) as Vec3, 'midpoint')
        break
      }
      walked += lengths[k]
    }
    // A curved edge's circle: through three of its points spread along it.
    if (points.length > 2) {
      const n = closed ? points.length - 1 : points.length
      const centre = circleCentre(points[0], points[Math.floor(n / 3)], points[Math.floor((2 * n) / 3)])
      if (centre && points.every((p) => Math.abs(distance(p, centre) - distance(points[0], centre)) < 1e-3 * (1 + distance(points[0], centre)))) {
        add(centre, 'center')
      }
    }
  }
  for (const face of faces) if (face.planar) add(face.center, 'face')
  return out
}

/** One axis's magnet target: a world coordinate, what it is, and whose. */
export interface AxisTarget { value: number; label: string; body: number }
/** A moving selection's references on one axis: its box's low, middle and high. */
const SIDES = ['near edge', 'centre', 'far edge'] as const

/** The values the moving parts' box can line up with, axis by axis: every other part's box low, middle and high,
 * then the named snaps of the parts given points for (0.2.0's eight nearest). */
export function axisTargets(others: { body: number; name: string; box: { low: Vec3; high: Vec3 }; points: SnapPoint[] }[]): AxisTarget[][] {
  return [0, 1, 2].map((axis) => [
    ...others.flatMap(({ body, name, box }) => [box.low[axis], (box.low[axis] + box.high[axis]) / 2, box.high[axis]]
      .map((value, i) => ({ value, label: `${name}: ${SIDES[i]}`, body }))),
    ...others.flatMap(({ body, name, points }) => points.map((p) => ({ value: p.point[axis], label: `${name}: ${kindLabel[p.kind]}`, body })))
  ])
}

export interface MoveLock { value: number; label: string; body: number }

/** 0.2.0's move magnets on one axis. `raw` is the dragged distance, `from` the moving box's low, middle and high.
 * A lock holds until the drag leaves it by more than 1.6 × `capture`; otherwise the nearest target within `capture`
 * (in mm) catches. Undefined: no magnet, so the drag's own step applies. */
export function snapAxis(raw: number, from: [number, number, number], targets: AxisTarget[], capture: number, lock?: MoveLock):
  { value: number; lock: MoveLock; label: string } | undefined {
  if (lock && Math.abs(raw - lock.value) <= capture * 1.6) return { value: lock.value, lock, label: lock.label }
  let best: { gap: number; lock: MoveLock; label: string } | undefined
  for (const target of targets) {
    for (let i = 0; i < 3; i++) {
      const delta = target.value - from[i]
      // A part never snaps to where it already is.
      if (Math.abs(delta) < 1e-7) continue
      const gap = Math.abs(raw - delta)
      if (gap <= capture && (!best || gap < best.gap)) {
        const label = `${target.label.split(': ')[0]}: ${SIDES[i]} to ${target.label.split(': ')[1]}`
        best = { gap, lock: { value: delta, label, body: target.body }, label }
      }
    }
  }
  return best && { value: best.lock.value, lock: best.lock, label: best.label }
}

/** How far 10 screen pixels reach, as 0.2.0's capture: never under 0.05 mm or over 1 mm. */
export const moveCapture = (mmPerPixel: number) => Math.min(1, Math.max(0.05, mmPerPixel * 10))

/** 0.2.0's ring detents: within min(5°, step / 5) of a step the turn holds there (and stays until it is 1.6 times that
 * away); between them it turns freely. 0° is not a detent. Shift turns freely; Ctrl takes whole degrees. */
export function snapAngle(raw: number, step: number, locked: number | undefined, free: boolean, fine: boolean):
  { angle: number; snapped: boolean; locked?: number } {
  if (free || step <= 0) return { angle: raw, snapped: false }
  if (fine) return { angle: Math.round(raw), snapped: true }
  const capture = Math.min(5, 0.2 * step)
  if (locked !== undefined && Math.abs(raw - locked) <= capture * 1.6) return { angle: locked, snapped: true, locked }
  const nearest = Math.round(raw / step) * step
  if (nearest !== 0 && Math.abs(raw - nearest) <= capture) return { angle: nearest, snapped: true, locked: nearest }
  return { angle: raw, snapped: false }
}

/** Measure's pick: within `radius` window pixels of the pointer, the snap with the best priority less distance. */
export function nearestSnap<T extends { kind: SnapKind; at: [number, number] }>(candidates: T[], pointer: [number, number], radius = 22): T | undefined {
  let best: { score: number; candidate: T } | undefined
  for (const candidate of candidates) {
    const d = Math.hypot(candidate.at[0] - pointer[0], candidate.at[1] - pointer[1])
    if (d > radius) continue
    const score = priority[candidate.kind] - d
    if (!best || score > best.score) best = { score, candidate }
  }
  return best?.candidate
}
