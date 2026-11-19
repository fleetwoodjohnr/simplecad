import { mkdtempSync, utimesSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { describe, expect, it } from 'vitest'
import { describeAge, leftovers, recoveryDir, sessionFile } from '@/main/recovery'

describe('recovery', () => {
  it('uses SimpleCAD 0.4 recovery folder on each system', () => {
    expect(recoveryDir({}, 'linux', '/home/a')).toBe('/home/a/.local/share/simplecad/recovery')
    expect(recoveryDir({ XDG_DATA_HOME: '/data' }, 'linux', '/home/a')).toBe('/data/simplecad/recovery')
    expect(recoveryDir({ APPDATA: 'C:\\Users\\a\\AppData\\Roaming' }, 'win32', 'C:\\Users\\a')).toBe(
      path.join('C:\\Users\\a\\AppData\\Roaming', 'SimpleCAD', 'recovery'))
    expect(recoveryDir({}, 'darwin', '/Users/a')).toBe('/Users/a/Library/Application Support/SimpleCAD/recovery')
  })

  it('offers other sessions\' work, newest first, never its own', async () => {
    const dir = mkdtempSync(path.join(tmpdir(), 'scad-recovery-'))
    const own = sessionFile(dir, 1)
    for (const [name, age] of [['session-2.scad3', 300], ['session-3.scad3', 10], ['session-1.scad3', 0], ['session-4.scad3.part', 0]] as const) {
      const file = path.join(dir, name)
      writeFileSync(file, '')
      const when = (Date.now() - age * 1000) / 1000
      utimesSync(file, when, when)
    }
    expect((await leftovers(dir, own)).map((f) => path.basename(f.file))).toEqual(['session-3.scad3', 'session-2.scad3'])
    expect(await leftovers(path.join(dir, 'missing'), own)).toEqual([])
  })

  it('says how old the work is as 0.4 does', () => {
    const now = 1_000_000_000
    expect(describeAge(now - 30_000, now)).toBe('less than a minute ago')
    expect(describeAge(now - 600_000, now)).toBe('10 minutes ago')
    expect(describeAge(now - 7_200_000, now)).toBe('2 hours ago')
    expect(describeAge(now - 3 * 86_400_000, now)).toBe('3 days ago')
  })
})
