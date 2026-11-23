import { useEffect, useState } from 'react'
import type { ClipJointEdit } from '@/shared/engine-protocol'
import { Btn, inputCls } from '../ui'

type Inputs = ClipJointEdit['clipJoint']['inputs']

/** SimpleCAD 0.4's Clip Joint tool defaults: a medium PETG connector at a snug fit, as many as 80 mm spacing gives. */
export const defaultClipJoint: Inputs = {
  size: 'medium', retention: 'standard', material: 'petg', clearance: '0.15', clearance_preset: 'snug', depth_clearance: '0.3',
  min_wall: '0.8', lead_in: '0.4', count: 0, spacing: '80', edge_offset: '1', seat: true
}
const options = {
  size: [['small', 'Small'], ['medium', 'Medium'], ['large', 'Large']],
  retention: [['easy', 'Easy to separate'], ['standard', 'Standard hold'], ['firm', 'Firm hold']],
  material: [['petg', 'PETG'], ['pla', 'PLA']],
  clearance_preset: [['snug', 'Snug fit'], ['sliding', 'Sliding fit'], ['press', 'Press fit'], ['loose', 'Loose fit']]
} as const
const lengths: [keyof Inputs, string][] = [['clearance', 'Clearance (mm, total)'], ['spacing', 'Spacing (mm)'], ['edge_offset', 'Edge offset (mm)'],
  ['depth_clearance', 'Depth clearance (mm)'], ['min_wall', 'Minimum wall (mm)'], ['lead_in', 'Lead-in (mm)']]

interface Props {
  busy: boolean
  /** The picked faces, as words: the first part's, then the one it meets. */
  picked: string[]
  /** The printer's clearance for each fit, as calibrated. */
  fits?: Record<string, number>
  onClear: () => void
  onChange: () => void
  onPreview: (inputs: Inputs) => void
  onApply: (inputs: Inputs) => void
  onCancel: () => void
}

/** Two parts held together by printed connectors in paired sockets, cut into the faces where they meet. */
export function ClipJointPanel({ busy, picked, fits, onClear, onChange, onPreview, onApply, onCancel }: Props) {
  const [inputs, setInputs] = useState(defaultClipJoint)
  const set = (change: Partial<Inputs>) => { setInputs((current) => ({ ...current, ...change })); onChange() }
  // A fit sets the clearance to what it means on this printer, as the tool does; the field can still be typed.
  useEffect(() => {
    const value = fits?.[inputs.clearance_preset]
    if (value !== undefined) setInputs((current) => ({ ...current, clearance: String(value) }))
  }, [fits, inputs.clearance_preset])
  const ready = picked.length === 2 && lengths.every(([key]) => String(inputs[key]).trim())
  return (
    <form aria-label="Clip Joint" className="grid shrink-0 gap-2 text-xs" onSubmit={(event) => {
      event.preventDefault()
      if (ready && !busy) onApply(inputs)
    }}>
      <div className="font-semibold">Clip Joint</div>
      <div role="status" aria-label="Joined faces" className="text-text-2">
        {picked.length === 0 ? 'Click the face on the first part where the parts meet.'
          : picked.length === 1 ? `${picked[0]}, then click the face it meets on the other part.` : picked.join(' meets ')}
      </div>
      {picked.length > 0 && <Btn type="button" size="sm" className="justify-self-start" disabled={busy} onClick={onClear}>Pick again</Btn>}
      {(Object.keys(options) as (keyof typeof options)[]).map((key) => <label key={key} className="grid gap-1">
        {{ size: 'Size', retention: 'Retention', material: 'Material', clearance_preset: 'Fit' }[key]}
        <select className={inputCls} value={inputs[key]} disabled={busy} onChange={(event) => set({ [key]: event.target.value })}>
          {options[key].map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </label>)}
      <label className="grid gap-1">Connectors (0 = as many as the spacing gives)
        <input className={inputCls} type="number" min={0} max={64} value={inputs.count} disabled={busy}
          onChange={(event) => set({ count: Math.max(0, Math.min(64, Math.round(Number(event.target.value) || 0))) })} />
      </label>
      {lengths.map(([key, label]) => <label key={key} className="grid gap-1">{label}
        <input className={inputCls} value={String(inputs[key])} maxLength={255} disabled={busy} onChange={(event) => set({ [key]: event.target.value })} />
      </label>)}
      <label className="flex items-center gap-1.5">
        <input type="checkbox" checked={inputs.seat} disabled={busy} onChange={(event) => set({ seat: event.target.checked })} />
        Bring the parts together
      </label>
      <div className="flex gap-1">
        <Btn type="button" size="sm" disabled={!ready || busy} onClick={() => onPreview(inputs)}>Preview</Btn>
        <Btn type="submit" size="sm" variant="primary" disabled={!ready || busy}>Apply</Btn>
        <Btn type="button" size="sm" disabled={busy} onClick={onCancel}>Cancel</Btn>
      </div>
    </form>
  )
}
