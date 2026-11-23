import { useState } from 'react'
import { Btn, inputCls } from '../ui'

const labels: Record<string, string> = {
  dx: 'Move X', dy: 'Move Y', dz: 'Move Z', rx: 'Turn about X (°)', ry: 'Turn about Y (°)', rz: 'Turn about Z (°)', angle: 'Turn (°)',
  distance: 'Distance', offset: 'Offset', rotation: 'Rotation (°)', flip: 'Flip', horizontal: 'Horizontal', vertical: 'Vertical',
  horizontal_gap: 'Horizontal gap', vertical_gap: 'Vertical gap', u: 'Offset U', v: 'Offset V',
  clearance: 'Clearance', knuckles: 'Knuckles', knuckle_od: 'Knuckle diameter', pin: 'Pin diameter', length: 'Length (0 = whole edge)',
  axial_clearance: 'Gap between knuckles', open_max: 'Opens to (°)', stop: 'Stop at the limit', flex: 'Strip thickness',
  flex_length: 'Bending length', size: 'Size', retention: 'Retention', material: 'Material', depth_clearance: 'Depth clearance',
  min_wall: 'Minimum wall', lead_in: 'Lead-in', spacing: 'Spacing', edge_offset: 'Edge offset', designation: 'Thread size', form: 'Profile'
}
/** Choices that differ by step: a threaded pair's clearance is a thread fit, not a part fit. */
const choicesFor: Record<string, Record<string, string[]>> = {
  threaded_connection: { clearance: ['tight', 'normal', 'loose', 'very_loose'], form: ['printed', 'iso'] }
}
/** Settings that are a checkbox. */
const toggles = ['flip', 'stop']
/** Settings that are a choice rather than an expression. */
const choices: Record<string, string[]> = { horizontal: ['left', 'center', 'right'], vertical: ['bottom', 'center', 'top'],
  clearance: ['snug', 'sliding', 'loose', 'press'], size: ['small', 'medium', 'large'], retention: ['easy', 'standard', 'firm'],
  material: ['pla', 'petg'] }

interface Props {
  /** The step's type, which decides some of its choices. */
  kind?: string
  /** The step's name, which names the form. */
  name: string
  busy: boolean
  /** The saved settings History can edit, as expressions. */
  preset: Record<string, string>
  onEdit: (changes: Record<string, string>) => void
  onCancel: () => void
}

/** Edits a saved step's settings in place as one undoable step; what else it was made with stays. */
export function StepSettingsPanel({ kind, name, busy, preset, onEdit, onCancel }: Props) {
  const options = { ...choices, ...choicesFor[kind ?? ''] }
  const [values, setValues] = useState(preset)
  const changes = Object.fromEntries(Object.entries(values).filter(([key, value]) => preset[key] !== value))
  const valid = Object.values(values).every((value) => value.trim()) && Object.keys(changes).length > 0
  return (
    <form aria-label={name} className="grid shrink-0 gap-2 text-xs" onSubmit={(event) => {
      event.preventDefault()
      if (valid && !busy) onEdit(changes)
    }}>
      <div className="font-semibold">Edit {name}</div>
      {Object.keys(preset).map((key) => {
        const set = (value: string) => setValues((current) => ({ ...current, [key]: value }))
        if (toggles.includes(key)) {
          return <label key={key} className="flex items-center gap-2">
            <input type="checkbox" checked={values[key] === 'true'} disabled={busy} onChange={(event) => set(String(event.target.checked))} />
            {labels[key]}</label>
        }
        return <label key={key} className="grid gap-1">{labels[key] ?? key}
          {options[key]?.includes(preset[key])
            ? <select className={inputCls} value={values[key]} disabled={busy} onChange={(event) => set(event.target.value)}>
                {options[key].map((option) => <option key={option} value={option}>{option[0].toUpperCase() + option.slice(1)}</option>)}
              </select>
            : <input className={inputCls} value={values[key]} maxLength={255} disabled={busy} onChange={(event) => set(event.target.value)} />}
        </label>
      })}
      <div className="flex gap-1">
        <Btn type="submit" size="sm" variant="primary" disabled={!valid || busy}>Apply</Btn>
        <Btn type="button" size="sm" disabled={busy} onClick={onCancel}>Cancel</Btn>
      </div>
    </form>
  )
}
