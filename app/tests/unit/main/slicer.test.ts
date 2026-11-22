import { describe, expect, it, vi } from 'vitest'

vi.mock('electron', () => ({ app: {}, shell: {} }))
const { findSlicer } = await import('../../../src/main/slicer')

const on = (...files: string[]) => (file: string) => files.includes(file)

describe('finding a slicer', () => {
  it('takes the first slicer in SimpleCAD 0.4’s order, from any PATH entry', () => {
    const found = findSlicer('/usr/bin:/opt/bin', on('/opt/bin/prusa-slicer', '/usr/bin/orca-slicer'), () => false)
    expect(found).toEqual({ name: 'OrcaSlicer', command: ['/usr/bin/orca-slicer'] })
  })

  it('falls back to a Flatpak before trying the next slicer', () => {
    const flatpak = (id: string) => id === 'com.orcaslicer.OrcaSlicer'
    expect(findSlicer('/usr/bin', on('/usr/bin/cura'), flatpak))
      .toEqual({ name: 'OrcaSlicer', command: ['flatpak', 'run', 'com.orcaslicer.OrcaSlicer'] })
  })

  it('finds nothing when no slicer is installed', () => {
    expect(findSlicer('/usr/bin', () => false, () => false)).toBeUndefined()
  })
})
