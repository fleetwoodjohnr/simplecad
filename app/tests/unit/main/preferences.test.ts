import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { expect, it } from 'vitest'
import { readLegacyPreferences } from '@/main/preferences'
import { displayLength } from '@/shared/preferences'

it('keeps valid legacy fields and leaves damaged or unsupported data untouched', async () => {
  const scratch = await mkdtemp(path.join(os.tmpdir(), 'simplecad-preferences-unit-'))
  const file = path.join(scratch, 'settings.json')
  const env = { SIMPLECAD_LEGACY_SETTINGS: file }
  try {
    expect(await readLegacyPreferences(env)).toBeUndefined()
    await writeFile(file, '[invalid JSON')
    expect(await readLegacyPreferences(env)).toBeUndefined()
    const original = '{"theme":"dark","display_unit":"in","modelling":{"move_step": 200},"extra":true}'
    await writeFile(file, original)
    expect(await readLegacyPreferences(env)).toEqual({
      theme: 'dark', displayUnit: 'in', snap: true, moveStep: 1, rotationSnap: 90, updateCheck: true
    })
    expect(await readFile(file, 'utf8')).toBe(original)
    expect(displayLength(25.4 ** 3, 'in', 3)).toBe(1)
  } finally {
    await rm(scratch, { recursive: true, force: true })
  }
})
