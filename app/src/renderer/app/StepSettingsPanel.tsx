import { useState } from 'react'
import { Btn, inputCls } from '../ui'

const labels: Record<string, string> = {
  dx: 'Move X', dy: 'Move Y', dz: 'Move Z', rx: 'Turn about X (°)', ry: 'Turn about Y (°)', rz: 'Turn about Z (°)', angle: 'Turn (°)'
}

interface Props {
  /** The step's name, which names the form. */
  name: string
  busy: boolean
  /** The saved settings History can edit, as expressions. */
  preset: Record<string, string>
  onEdit: (changes: Record<string, string>) => void
  onCancel: () => void
}

/** Edits a saved step's settings in place as one undoable step; what else it was made with stays. */
export function StepSettingsPanel({ name, busy, preset, onEdit, onCancel }: Props) {
  const [values, setValues] = useState(preset)
  const changes = Object.fromEntries(Object.entries(values).filter(([key, value]) => preset[key] !== value))
  const valid = Object.values(values).every((value) => value.trim()) && Object.keys(changes).length > 0
  return (
    <form aria-label={name} className="grid shrink-0 gap-2 text-xs" onSubmit={(event) => {
      event.preventDefault()
      if (valid && !busy) onEdit(changes)
    }}>
      <div className="font-semibold">Edit {name}</div>
      {Object.keys(preset).map((key) => <label key={key} className="grid gap-1">{labels[key] ?? key}
        <input className={inputCls} value={values[key]} maxLength={255} disabled={busy}
          onChange={(event) => setValues((current) => ({ ...current, [key]: event.target.value }))} />
      </label>)}
      <div className="flex gap-1">
        <Btn type="submit" size="sm" variant="primary" disabled={!valid || busy}>Apply</Btn>
        <Btn type="button" size="sm" disabled={busy} onClick={onCancel}>Cancel</Btn>
      </div>
    </form>
  )
}
