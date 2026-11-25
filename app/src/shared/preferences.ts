export type Theme = 'light' | 'dark' | 'system'
export type DisplayUnit = 'mm' | 'cm' | 'm' | 'in'

export interface Preferences {
  theme: Theme
  displayUnit: DisplayUnit
  snap: boolean
  moveStep: number
  rotationSnap: number
  /** Look for a new release by itself, as the rest of the Simple suite does. */
  updateCheck: boolean
}

export const defaults: Preferences = {
  theme: 'system', displayUnit: 'mm', snap: false, moveStep: 1, rotationSnap: 15, updateCheck: true
}

export function sanitisePreferences(data: Partial<Preferences>): Preferences {
  const numeric = (value: unknown, min: number, max: number, fallback: number) =>
    typeof value === 'number' && Number.isFinite(value) && value >= min && value <= max ? value : fallback
  return {
    theme: data.theme === 'light' || data.theme === 'dark' ? data.theme : 'system',
    displayUnit: data.displayUnit === 'cm' || data.displayUnit === 'm' || data.displayUnit === 'in' ? data.displayUnit : 'mm',
    snap: typeof data.snap === 'boolean' ? data.snap : defaults.snap,
    moveStep: numeric(data.moveStep, 0.01, 100, defaults.moveStep),
    rotationSnap: numeric(data.rotationSnap, 1, 180, defaults.rotationSnap),
    updateCheck: typeof data.updateCheck === 'boolean' ? data.updateCheck : defaults.updateCheck
  }
}

const millimetres: Record<DisplayUnit, number> = { mm: 1, cm: 10, m: 1000, in: 25.4 }

export function displayLength(value: number, unit: DisplayUnit, dimensions = 1) {
  return value / millimetres[unit] ** dimensions
}
