import { mkdtemp, readFile, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { describe, expect, it } from 'vitest'
import { calibrationPath, readCalibration, saveCalibration } from '../../../src/main/calibration'

describe('printer calibration', () => {
  it('lives where SimpleCAD 0.4 keeps it on each system', () => {
    expect(calibrationPath({}, 'linux', '/home/a')).toBe('/home/a/.local/share/simplecad/printers/elegoo_centauri_carbon_2.json')
    expect(calibrationPath({ XDG_DATA_HOME: '/data' }, 'linux', '/home/a')).toBe('/data/simplecad/printers/elegoo_centauri_carbon_2.json')
    expect(calibrationPath({}, 'darwin', '/Users/a')).toBe('/Users/a/Library/Application Support/SimpleCAD/printers/elegoo_centauri_carbon_2.json')
    expect(calibrationPath({ APPDATA: 'C:\\Users\\a\\AppData\\Roaming' }, 'win32', 'C:\\Users\\a'))
      .toMatch(/^C:\\Users\\a\\AppData\\Roaming\\SimpleCAD[\\/]printers[\\/]elegoo_centauri_carbon_2\.json$/)
  })

  it('reads the shipped fits until measurements are saved, in 0.4’s format, then reads those back', async () => {
    const file = path.join(await mkdtemp(path.join(tmpdir(), 'scad-calibration-')), 'printers', 'profile.json')
    expect(await readCalibration(file)).toEqual({ calibrated: false, thread: 0.6,
      fits: { sliding: 0.3, snug: 0.15, press: -0.05, loose: 0.5, thread: 0.6 } })
    await saveCalibration({ fits: { snug: 0.22 }, thread: 0.45 }, file)
    const saved = JSON.parse(await readFile(file, 'utf8'))
    expect(saved).toMatchObject({ id: 'elegoo_centauri_carbon_2', calibrated: true, measured_fits: { snug: 0.22 }, measured_thread_clearance: 0.45,
      build_volume: { x: 256, y: 256, z: 256 } })
    expect(await readCalibration(file)).toMatchObject({ calibrated: true, thread: 0.45, fits: { snug: 0.22, sliding: 0.3 } })
    await writeFile(file, '{damaged')
    expect((await readCalibration(file)).calibrated).toBe(false)
  })

  it('refuses values that are not clearances', async () => {
    const file = path.join(await mkdtemp(path.join(tmpdir(), 'scad-calibration-')), 'profile.json')
    for (const bad of [null, { fits: { snug: 9 }, thread: 0.5 }, { fits: { warp: 0.1 }, thread: 0.5 }, { fits: {}, thread: -1 }, { fits: { snug: Number.NaN }, thread: 0.5 }]) {
      await expect(saveCalibration(bad, file)).rejects.toThrow('Enter clearances between -1 and 5 mm.')
    }
  })
})
