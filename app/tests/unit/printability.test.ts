import { describe, expect, it } from 'vitest'
import { printChecks } from '@/renderer/app/printability'

/** A box from (0,0,z0) to (w,d,z0+h), as 12 outward triangles. */
function box(w: number, d: number, h: number, z0 = 0) {
  const p = [[0, 0, 0], [w, 0, 0], [w, d, 0], [0, d, 0], [0, 0, h], [w, 0, h], [w, d, h], [0, d, h]].map(([x, y, z]) => [x, y, z + z0])
  const faces = [[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5], [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]]
  return { positions: Float32Array.from(p.flat()), indices: Uint32Array.from(faces.flat()) }
}
const severities = (findings: ReturnType<typeof printChecks>) => Object.fromEntries(findings.map((f) => [f.check, f.severity]))

describe('print checks', () => {
  it('passes a box sitting on the plate: its base rests on the plate rather than overhanging', () => {
    expect(severities(printChecks(box(40, 30, 20)))).toEqual({ build_volume: 'ok', on_plate: 'ok', overhangs: 'ok' })
  })

  it('warns about a part too big for the printer, or sunk below the plate', () => {
    const findings = printChecks(box(300, 30, 20, -2))
    expect(severities(findings)).toMatchObject({ build_volume: 'warning', on_plate: 'warning' })
    expect(findings[0].hint).toBe('X is 300.0 mm, the plate allows 256 mm')
  })

  it('counts the underside of a slab on a post as overhang, but not the post resting on the plate', () => {
    const post = box(10, 10, 20)
    const slab = box(40, 40, 5, 20)
    const mesh = {
      positions: Float32Array.from([...post.positions, ...slab.positions]),
      indices: Uint32Array.from([...post.indices, ...slab.indices].map((index, i) => i < post.indices.length ? index : index + 8))
    }
    // The slab's 1600 mm² underside, 20 mm up, is 32% of the 5000 mm² of surface.
    const findings = printChecks(mesh)
    expect(severities(findings)).toMatchObject({ on_plate: 'ok', overhangs: 'warning' })
    expect(findings[2].message).toBe('32% of the surface overhangs by more than 50°.')
  })
})

describe('the engine’s half of the print check', () => {
  it('says the all-clears once however many bodies, and names the body with a problem', async () => {
    const { healthChecks } = await import('@/renderer/app/printability')
    const findings = healthChecks([
      { name: 'Box', valid: true, openEdges: 0, solids: 1 },
      { name: 'Lid', valid: true, openEdges: 0, solids: 1 },
      { name: 'Shell', valid: true, openEdges: 3, solids: 2 }
    ])
    expect(findings.map((f) => [f.severity, f.message])).toEqual([
      ['ok', 'Solid and watertight.'],
      ['ok', 'One connected solid.'],
      ['warning', "Shell's surface is not closed — 3 open edge(s)."],
      ['note', 'Shell is 2 separate pieces.']
    ])
  })
})
