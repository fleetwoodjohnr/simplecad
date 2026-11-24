import { describe, expect, it } from 'vitest'
import type { PartDisplay } from '../../../src/engine/document'
import { movedDisplay, relativePlacement } from '../../../src/engine/placement'

// A quarter turn about Z, then 10 along X.
const turned = [0, -1, 0, 10, 1, 0, 0, 0, 0, 0, 1, 0]
const still = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0]

describe('a moved copy shows its partner moved', () => {
  it('takes the move from one placement to the next', () => {
    expect(relativePlacement(still, turned)).toEqual(turned)
    // From the turned placement back to where it started: the inverse.
    const back = relativePlacement(turned, still)!
    const point = [10, 0, 0]
    const apply = (m: number[], p: number[]) => [0, 1, 2].map((r) => m[r * 4] * p[0] + m[r * 4 + 1] * p[1] + m[r * 4 + 2] * p[2] + m[r * 4 + 3])
    expect(apply(back, apply(turned, [1, 2, 3])).map((v) => +v.toFixed(12))).toEqual([1, 2, 3])
    expect(apply(back, point).map((v) => +v.toFixed(12))).toEqual([0, 0, 0])
  })

  it('leaves scaled or mirrored placements to be displayed again', () => {
    expect(relativePlacement(still, [2, 0, 0, 0, 0, 2, 0, 0, 0, 0, 2, 0])).toBeUndefined()
    expect(relativePlacement(still, [-1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0])).toBeUndefined()
  })

  it('moves points, turns normals, and keeps areas and volume', () => {
    const display: PartDisplay = {
      volume: 6, positions: new Float32Array([1, 0, 0, 0, 1, 0]), normals: new Float32Array([1, 0, 0, 0, 0, 1]),
      indices: new Uint32Array([0, 1, 0]), faceTriStart: new Uint32Array([0, 1]), edgePoints: new Float32Array([1, 0, 0]),
      edgeStart: new Uint32Array([0, 1]), faces: [{ planar: true, center: [1, 0, 0], anchor: [1, 0, 0], normal: [1, 0, 0], area: 2 }]
    }
    const moved = movedDisplay(display, turned)
    expect([...moved.positions]).toEqual([10, 1, 0, 9, 0, 0])
    expect([...moved.normals]).toEqual([0, 1, 0, 0, 0, 1])
    expect([...moved.edgePoints]).toEqual([10, 1, 0])
    expect(moved.faces[0]).toEqual({ planar: true, center: [10, 1, 0], anchor: [10, 1, 0], normal: [0, 1, 0], area: 2 })
    expect(moved.volume).toBe(6)
    expect(moved.indices).toBe(display.indices)
  })
})
