import { describe, expect, it } from 'vitest'
import { searchable } from '@/renderer/app/CommandSearch'
import { selectionTools, type SelectionKind } from '@/renderer/app/ContextBar'
import { commands } from '@/renderer/app/commands'

// 0.6.0's ribbon keeps only Shape, Sketch, Import, Measure and Print: every other tool has to be found in search.
describe('command search', () => {
  it('lists every command and every tool the selection bar offers', () => {
    const kinds: SelectionKind[] = ['face', 'curvedFace', 'edge', 'part', 'parts', 'manyParts', 'group']
    for (const id of [...Object.keys(commands), ...selectionTools(kinds)]) expect(searchable).toContain(id)
  })
})
