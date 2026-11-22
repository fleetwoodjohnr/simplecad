// SimpleCAD 0.4's printer calibration file (kernel/calibration.py): measured fits saved as a user printer
// profile that overrides the shipped one. 0.4 and this version read and write the same file.
import { mkdir, readFile, rename, writeFile } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import fits from '../../../simplecad/data/fits.json'
import printer from '../../../simplecad/data/printers/elegoo_centauri_carbon_2.json'
import type { Calibration } from '../shared/ipc-types'

/** 0.4's data_dir()/printers/<id>.json; SIMPLECAD_PRINTER_PROFILE names another file (tests). */
export function calibrationPath(env = process.env, platform = process.platform, home = os.homedir()) {
  if (env.SIMPLECAD_PRINTER_PROFILE) return env.SIMPLECAD_PRINTER_PROFILE
  const data = platform === 'win32' ? path.win32.join(env.APPDATA || home, 'SimpleCAD')
    : platform === 'darwin' ? path.join(home, 'Library', 'Application Support', 'SimpleCAD')
    : path.join(env.XDG_DATA_HOME || path.join(home, '.local', 'share'), 'simplecad')
  return path.join(data, 'printers', `${printer.id}.json`)
}

const shippedFits = () => Object.fromEntries(Object.entries(fits.fits).map(([key, fit]) => [key, fit.clearance]))

/** effective_fits and the Normal thread clearance: measured where saved, shipped otherwise. */
export async function readCalibration(file = calibrationPath()): Promise<Calibration> {
  const values = { calibrated: false, fits: shippedFits(), thread: fits.thread_clearance.normal.clearance }
  try {
    const saved = JSON.parse(await readFile(file, 'utf8'))
    for (const [key, value] of Object.entries(saved?.measured_fits ?? {})) {
      if (key in values.fits && typeof value === 'number' && Number.isFinite(value)) values.fits[key] = value
    }
    if (typeof saved?.measured_thread_clearance === 'number' && Number.isFinite(saved.measured_thread_clearance)) {
      values.thread = saved.measured_thread_clearance
    }
    values.calibrated = saved?.calibrated === true
  } catch {
    // No file, or a damaged one, is an uncalibrated printer, as in 0.4.
  }
  return values
}

const clearance = (value: unknown, low: number): value is number => typeof value === 'number' && Number.isFinite(value) && value >= low && value <= 5

/** save_measurements: the shipped profile plus what the test print showed, written atomically. */
export async function saveCalibration(data: unknown, file = calibrationPath()) {
  const request = data as Partial<Pick<Calibration, 'fits' | 'thread'>> | null
  const measured = request?.fits
  if (!measured || typeof measured !== 'object' || !clearance(request.thread, 0) ||
    Object.keys(measured).some((key) => !(key in fits.fits)) || !Object.values(measured).every((value) => clearance(value, -1))) {
    throw new Error('Enter clearances between -1 and 5 mm.')
  }
  const profile = { ...printer, calibrated: true, measured_fits: measured, measured_thread_clearance: request.thread }
  await mkdir(path.dirname(file), { recursive: true })
  await writeFile(`${file}.part`, `${JSON.stringify(profile, null, 2)}\n`)
  await rename(`${file}.part`, file)
}
