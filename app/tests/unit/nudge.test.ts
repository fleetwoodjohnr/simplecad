import { describe, expect, it } from 'vitest'
import { worldAxes } from '@/renderer/app/nudge'

describe('arrow-key nudge axes', () => {
  it('steps along X, Z and Y from the front', () => {
    expect(worldAxes({ direction: [0, 1, 0], up: [0, 0, 1] })).toEqual([[1, 0, 0], [0, 0, 1], [0, 1, 0]])
  })

  it('takes one axis each in the iso view, where right lies between X and Y', () => {
    const third = 1 / Math.sqrt(3)
    const sixth = 1 / Math.sqrt(6)
    expect(worldAxes({ direction: [-third, third, -third], up: [-sixth, sixth, 2 * sixth] })).toEqual([[1, 0, 0], [0, 0, 1], [0, 1, 0]])
  })

  it('keeps the signs of a view from behind', () => {
    expect(worldAxes({ direction: [0, -1, 0], up: [0, 0, 1] })).toEqual([[-1, 0, 0], [0, 0, 1], [0, -1, 0]])
  })
})
