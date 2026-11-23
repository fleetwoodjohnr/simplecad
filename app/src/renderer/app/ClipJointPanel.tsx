import { useEffect, useState } from 'react'
import { SLIDE_STYLES, type ClipJointEdit, type SlideSettings } from '@/shared/engine-protocol'
import { Btn, inputCls } from '../ui'

type Inputs = ClipJointEdit['clipJoint']['inputs']

/** SimpleCAD 0.4's Clip Joint tool defaults: medium PETG snap tabs on the first part at a snug fit, as many as 80 mm
 * spacing gives. */
export const defaultClipJoint: Inputs = {
  style: 'snap_tab', male: 'a', size: 'medium', retention: 'standard', material: 'petg', clearance: '0.15', clearance_preset: 'snug', depth_clearance: '0.3',
  min_wall: '0.8', lead_in: '0.4', count: 0, spacing: '80', edge_offset: '1', seat: true
}
/** Its rails' defaults (`RAIL_DEFAULTS`): two, the way the shared face is longer, in a channel cut if the part is
 * thick enough, with a stop and a detent. */
export const defaultSlide: SlideSettings = {
  rails: 2, insertion: '', female_mode: 'auto', stop: true, detent: true,
  rail_width: '8', rail_height: '3', rail_angle: '15', rail_length: '0'
}
const options = {
  style: [['snap_tab', 'Snap tab'], ['connector', 'Printed connector'], ['tab_slot', 'Tab & slot'],
    ['dovetail', 'Dovetail rail'], ['t_rail', 'T-rail'], ['tongue_groove', 'Tongue & groove']],
  size: [['small', 'Small'], ['medium', 'Medium'], ['large', 'Large']],
  retention: [['easy', 'Easy to separate'], ['standard', 'Standard hold'], ['firm', 'Firm hold']],
  material: [['petg', 'PETG'], ['pla', 'PLA']],
  clearance_preset: [['snug', 'Snug fit'], ['sliding', 'Sliding fit'], ['press', 'Press fit'], ['loose', 'Loose fit']]
} as const
const lengths: [keyof Inputs, string][] = [['clearance', 'Clearance (mm, total)'], ['spacing', 'Spacing (mm)'], ['edge_offset', 'Edge offset (mm)'],
  ['depth_clearance', 'Depth clearance (mm)'], ['min_wall', 'Minimum wall (mm)'], ['lead_in', 'Lead-in (mm)']]
const railLengths: [keyof SlideSettings, string][] = [['rail_width', 'Rail width (mm)'], ['rail_height', 'Rail height (mm)'],
  ['rail_angle', 'Dovetail angle (°)'], ['rail_length', 'Rail length (mm, 0 = full)']]
/** Settings a sliding joint has no use for, as 0.4's tool hides them. */
const pushOnly = new Set(['size', 'retention', 'material', 'spacing'])

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

/** Two parts held together where they meet: printed connectors in paired sockets, snap tabs or keys on one part in
 * pockets cut into the other, or rails on one part sliding in channels on the other. */
export function ClipJointPanel({ busy, picked, fits, onClear, onChange, onPreview, onApply, onCancel }: Props) {
  const [inputs, setInputs] = useState(defaultClipJoint)
  const [slide, setSlide] = useState(defaultSlide)
  const [apart, setApart] = useState(false)
  const sliding = (SLIDE_STYLES as readonly string[]).includes(inputs.style)
  const set = (change: Partial<Inputs>) => { setInputs((current) => ({ ...current, ...change })); onChange() }
  const setRails = (change: Partial<SlideSettings>) => { setSlide((current) => ({ ...current, ...change })); onChange() }
  // A fit sets the clearance to what it means on this printer, as the tool does; the field can still be typed.
  useEffect(() => {
    const value = fits?.[inputs.clearance_preset]
    if (value !== undefined) setInputs((current) => ({ ...current, clearance: String(value) }))
  }, [fits, inputs.clearance_preset])
  const shownLengths = lengths.filter(([key]) => !(sliding && pushOnly.has(key)))
  const shownRails = railLengths.filter(([key]) => key !== 'rail_angle' || inputs.style === 'dovetail')
  const ready = picked.length === 2 && shownLengths.every(([key]) => String(inputs[key]).trim()) &&
    (!sliding || shownRails.every(([key]) => String(slide[key]).trim()))
  const request = (preview: boolean): Inputs => !sliding ? inputs
    : { ...inputs, slide, ...(preview && apart ? { slid_apart: true } : {}) }
  return (
    <form aria-label="Clip Joint" className="grid shrink-0 gap-2 text-xs" onSubmit={(event) => {
      event.preventDefault()
      if (ready && !busy) onApply(request(false))
    }}>
      <div className="font-semibold">Clip Joint</div>
      <div role="status" aria-label="Joined faces" className="text-text-2">
        {picked.length === 0 ? 'Click the face on the first part where the parts meet.'
          : picked.length === 1 ? `${picked[0]}, then click the face it meets on the other part.` : picked.join(' meets ')}
      </div>
      {picked.length > 0 && <Btn type="button" size="sm" className="justify-self-start" disabled={busy} onClick={onClear}>Pick again</Btn>}
      {(Object.keys(options) as (keyof typeof options)[]).filter((key) => !(sliding && pushOnly.has(key))).map((key) => <label key={key} className="grid gap-1">
        {{ style: 'Style', size: 'Size', retention: 'Retention', material: 'Material', clearance_preset: 'Fit' }[key]}
        <select className={inputCls} value={inputs[key]} disabled={busy} onChange={(event) => {
          const value = event.target.value
          // Rails need room to slide: a snug or press fit becomes a sliding one, as 0.4's tool does.
          const slides = key === 'style' && (SLIDE_STYLES as readonly string[]).includes(value)
          set({ [key]: value, ...(slides && ['snug', 'press'].includes(inputs.clearance_preset) ? { clearance_preset: 'sliding' as const } : {}) })
        }}>
          {options[key].map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </label>)}
      {inputs.style !== 'connector' && <label className="grid gap-1">{sliding ? 'Rails on' : 'Tabs on'}
        <select className={inputCls} value={inputs.male} disabled={busy} onChange={(event) => set({ male: event.target.value as Inputs['male'] })}>
          <option value="a">The first part</option>
          <option value="b">The second part</option>
        </select>
      </label>}
      {sliding ? <>
        <label className="grid gap-1">Rails
          <input className={inputCls} type="number" min={1} max={4} value={slide.rails} disabled={busy}
            onChange={(event) => setRails({ rails: Math.max(1, Math.min(4, Math.round(Number(event.target.value) || 1))) })} />
        </label>
        <label className="grid gap-1">Slides in
          <select className={inputCls} value={slide.insertion} disabled={busy} onChange={(event) => setRails({ insertion: event.target.value as SlideSettings['insertion'] })}>
            <option value="">Automatic</option>
            <option value="+u">Along the face, +U</option>
            <option value="-u">Along the face, −U</option>
            <option value="+v">Along the face, +V</option>
            <option value="-v">Along the face, −V</option>
          </select>
        </label>
        <label className="grid gap-1">Channel
          <select className={inputCls} value={slide.female_mode} disabled={busy} onChange={(event) => setRails({ female_mode: event.target.value as SlideSettings['female_mode'] })}>
            <option value="auto">Automatic</option>
            <option value="cut">Cut into the part</option>
            <option value="raised">Raised on the face</option>
          </select>
        </label>
        {shownRails.map(([key, label]) => <label key={key} className="grid gap-1">{label}
          <input className={inputCls} value={String(slide[key])} maxLength={255} disabled={busy} onChange={(event) => setRails({ [key]: event.target.value })} />
        </label>)}
        <label className="flex items-center gap-1.5">
          <input type="checkbox" checked={slide.stop} disabled={busy} onChange={(event) => setRails({ stop: event.target.checked })} />
          Stop at the end
        </label>
        <label className="flex items-center gap-1.5">
          <input type="checkbox" checked={slide.detent} disabled={busy} onChange={(event) => setRails({ detent: event.target.checked })} />
          Detent: clicks home at the stop
        </label>
      </> : <label className="grid gap-1">Number of joints (0 = as many as the spacing gives)
        <input className={inputCls} type="number" min={0} max={64} value={inputs.count} disabled={busy}
          onChange={(event) => set({ count: Math.max(0, Math.min(64, Math.round(Number(event.target.value) || 0))) })} />
      </label>}
      {shownLengths.map(([key, label]) => <label key={key} className="grid gap-1">{label}
        <input className={inputCls} value={String(inputs[key])} maxLength={255} disabled={busy} onChange={(event) => set({ [key]: event.target.value })} />
      </label>)}
      <label className="flex items-center gap-1.5">
        <input type="checkbox" checked={inputs.seat} disabled={busy} onChange={(event) => set({ seat: event.target.checked })} />
        Bring the parts together
      </label>
      {sliding && <label className="flex items-center gap-1.5">
        <input type="checkbox" checked={apart} disabled={busy} onChange={(event) => { setApart(event.target.checked); onChange() }} />
        Preview the first part slid out
      </label>}
      <div className="flex gap-1">
        <Btn type="button" size="sm" disabled={!ready || busy} onClick={() => onPreview(request(true))}>Preview</Btn>
        <Btn type="submit" size="sm" variant="primary" disabled={!ready || busy}>Apply</Btn>
        <Btn type="button" size="sm" disabled={busy} onClick={onCancel}>Cancel</Btn>
      </div>
    </form>
  )
}
