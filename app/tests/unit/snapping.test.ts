import { describe, expect, it } from 'vitest'
import { axisTargets, bodyBox, circleCentre, moveCapture, nearestSnap, snapAngle, snapAxis, snapPoints } from '@/renderer/viewport/snapping'
import type { DisplayShape } from '@/shared/engine-protocol'

// One part: a 10 × 10 square face at z = 0 (two triangles), its four straight edges, and a circle of radius 2 round
// (5, 5, 0) drawn as a closed 9-point polyline.
const circle = Array.from({ length: 9 }, (_, k) => [5 + 2 * Math.cos((k * Math.PI) / 4), 5 + 2 * Math.sin((k * Math.PI) / 4), 0]).flat()
const shape = {
  positions: new Float32Array([0, 0, 0, 10, 0, 0, 10, 10, 0, 0, 10, 0]),
  normals: new Float32Array(12),
  indices: new Uint32Array([0, 1, 2, 0, 2, 3]),
  faceTriStart: new Uint32Array([0, 2]),
  edgePoints: new Float32Array([0, 0, 0, 10, 0, 0, 10, 0, 0, 10, 10, 0, 10, 10, 0, 0, 10, 0, 0, 10, 0, 0, 0, 0, ...circle]),
  edgeStart: new Uint32Array([0, 2, 4, 6, 8, 17]),
  volume: 0,
  faces: [{ planar: true, center: [5, 5, 0], anchor: [5, 5, 0], normal: [0, 0, 1], area: 100, body: 0 }],
  ranges: [{ body: 0, indices: [0, 6], edges: [0, 5] }]
} as DisplayShape

describe('snap points', () => {
  it('finds corners, midpoints, the circle centre and the face centre', () => {
    const points = snapPoints(shape, 0)
    const of = (kind: string) => points.filter((p) => p.kind === kind).map((p) => p.point.map((v) => Math.round(v * 1e4) / 1e4))
    expect(of('vertex')).toEqual([[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0]])
    expect(of('midpoint')).toContainEqual([5, 0, 0])
    expect(of('center')).toEqual([[5, 5, 0]])
    expect(of('face')).toEqual([[5, 5, 0]])
  })
  it('gives a part over 80 faces none, as 0.2.0 kept big parts to their box', () => {
    const big = { ...shape, faces: Array.from({ length: 81 }, () => shape.faces[0]) } as DisplayShape
    expect(snapPoints(big, 0)).toEqual([])
  })
  it('measures the box and the circle', () => {
    expect(bodyBox(shape, 0)).toEqual({ low: [0, 0, 0], high: [10, 10, 0] })
    expect(circleCentre([1, 0, 0], [0, 1, 0], [-1, 0, 0])!.map((v) => Math.round(v * 1e9) / 1e9)).toEqual([0, 0, 0])
    expect(circleCentre([0, 0, 0], [1, 0, 0], [2, 0, 0])).toBeUndefined()
  })
})

describe('move magnets', () => {
  // A neighbour whose box runs 20 to 30 along X; the moving part runs 0 to 5.
  const targets = axisTargets([{ body: 1, name: 'Block', box: { low: [20, 0, 0], high: [30, 10, 10] }, points: [] }])
  const from: [number, number, number] = [0, 2.5, 5]
  it('catches within the capture and says what met what', () => {
    // 14.7 mm dragged: the far edge (5) meets Block's near edge (20) at 15.
    const hit = snapAxis(14.7, from, targets[0], 0.5)
    expect(hit?.value).toBe(15)
    expect(hit?.label).toBe('Block: far edge to near edge')
    expect(snapAxis(14.3, from, targets[0], 0.5)).toBeUndefined()
  })
  it('holds a lock until the drag is 1.6 times the capture away', () => {
    const lock = snapAxis(14.7, from, targets[0], 0.5)!.lock
    expect(snapAxis(15.75, from, targets[0], 0.5, lock)?.value).toBe(15)
    expect(snapAxis(15.85, from, targets[0], 0.5, lock)?.value).not.toBe(15)
  })
  it('keeps the capture between 0.05 and 1 mm', () => {
    expect(moveCapture(0.001)).toBe(0.05)
    expect(moveCapture(0.03)).toBeCloseTo(0.3)
    expect(moveCapture(1)).toBe(1)
  })
})

describe('ring detents', () => {
  it('holds near a step, turns freely between, and 0° is not one', () => {
    expect(snapAngle(88, 90, undefined, false, false)).toEqual({ angle: 90, snapped: true, locked: 90 })
    expect(snapAngle(80, 90, undefined, false, false)).toEqual({ angle: 80, snapped: false })
    expect(snapAngle(2, 90, undefined, false, false)).toEqual({ angle: 2, snapped: false })
    // 15° steps capture 3°, and release past 4.8°.
    expect(snapAngle(92.9, 15, undefined, false, false).angle).toBe(90)
    expect(snapAngle(94.7, 15, 90, false, false).angle).toBe(90)
    expect(snapAngle(94.9, 15, 90, false, false).angle).toBe(94.9)
  })
  it('turns freely with Shift and by whole degrees with Ctrl', () => {
    expect(snapAngle(88.4, 90, undefined, true, false)).toEqual({ angle: 88.4, snapped: false })
    expect(snapAngle(88.4, 90, undefined, false, true)).toEqual({ angle: 88, snapped: true })
  })
})

describe('measure snaps', () => {
  it('prefers a corner unless a midpoint is more than 20 px nearer', () => {
    const corner = { kind: 'vertex' as const, at: [10, 0] as [number, number] }
    const middle = { kind: 'midpoint' as const, at: [2, 0] as [number, number] }
    expect(nearestSnap([corner, middle], [0, 0])).toBe(corner)
    const near = { ...middle, at: [1, 0] as [number, number] }
    expect(nearestSnap([{ ...corner, at: [21.5, 0] as [number, number] }, middle], [0, 0])?.kind).toBe('vertex')
    expect(nearestSnap([{ ...corner, at: [22, 0] as [number, number] }, near], [0, 0])).toBe(near)
    expect(nearestSnap([{ ...corner, at: [30, 0] as [number, number] }], [0, 0])).toBeUndefined()
  })
})
