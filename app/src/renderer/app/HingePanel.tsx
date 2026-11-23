import { useState } from 'react'
import { HINGE_TYPES, type HingeEdit } from '@/shared/engine-protocol'
import { Btn, Segmented, inputCls } from '../ui'

type Inputs = HingeEdit['hinge']['inputs']

/** SimpleCAD 0.4's Hinge tool defaults (kernel/hinge.py DEFAULTS). */
export const defaultHinge: Inputs = {
  type: 'knuckle', clearance: 'sliding', knuckles: '5', knuckle_od: '8', pin: '3', length: '0', axial_clearance: '0.4',
  open_max: '120', stop: false, flip: false, flex: '0.5', flex_length: '3'
}
const types: Record<typeof HINGE_TYPES[number], string> = { pin: 'Pin', knuckle: 'Print-in-place', snap: 'Snap-on', living: 'Living' }
const fits = [['snug', 'Snug fit'], ['sliding', 'Sliding fit'], ['loose', 'Loose fit']]
/** The settings a type uses, with the hinge tool's labels. */
const turning: [keyof Inputs, string][] = [['knuckles', 'Knuckles'], ['knuckle_od', 'Knuckle diameter (mm)'], ['pin', 'Pin diameter (mm)'],
  ['length', 'Length (mm, 0 = whole edge)'], ['axial_clearance', 'Gap between knuckles (mm)'], ['open_max', 'Opens to (°)']]
const living: [keyof Inputs, string][] = [['length', 'Length (mm, 0 = whole edge)'], ['flex', 'Strip thickness (mm)'],
  ['flex_length', 'Bending length (mm)']]

interface Props {
  busy: boolean
  /** The picked edge, as words, or undefined before one is picked. */
  edge?: string
  /** The parts that can swing: every shown body but the fixed one. */
  parts: { index: number; name: string }[]
  swinging?: number
  onSwinging: (body: number) => void
  onChange: () => void
  onPreview: (inputs: Inputs) => void
  onApply: (inputs: Inputs) => void
  onClose: () => void
}

/** Knuckles, a pin and a stop between a fixed part and the part that swings, from one edge of the fixed part. */
export function HingePanel({ busy, edge, parts, swinging, onSwinging, onChange, onPreview, onApply, onClose }: Props) {
  const [inputs, setInputs] = useState(defaultHinge)
  const set = (change: Partial<Inputs>) => { setInputs((current) => ({ ...current, ...change })); onChange() }
  const fields = inputs.type === 'living' ? living : turning
  const ready = edge !== undefined && swinging !== undefined && fields.every(([key]) => String(inputs[key] ?? '').trim())
  // Only what this type uses is sent, as the hinge tool saves it.
  const sent = (): Inputs => ({ type: inputs.type, stop: inputs.type !== 'living' && inputs.stop, flip: inputs.type !== 'living' && inputs.flip,
    ...Object.fromEntries(fields.map(([key]) => [key, inputs[key]])), ...(inputs.type === 'living' ? {} : { clearance: inputs.clearance }) })
  return (
    <form aria-label="Hinge" className="grid shrink-0 gap-2 text-xs" onSubmit={(event) => {
      event.preventDefault()
      if (ready && !busy) onApply(sent())
    }}>
      <div className="font-semibold">Hinge</div>
      <div role="status" aria-label="Hinge edge" className="text-text-2">
        {edge ?? 'Click the straight edge the hinge runs along, on the part that stays fixed.'}
      </div>
      <label className="grid gap-1">Part that swings
        <select className={inputCls} value={swinging ?? ''} disabled={busy || !parts.length} onChange={(event) => { onSwinging(Number(event.target.value)); onChange() }}>
          {swinging === undefined && <option value="">Choose a part</option>}
          {parts.map((part) => <option key={part.index} value={part.index}>{part.name}</option>)}
        </select>
      </label>
      <Segmented label="Hinge type" value={inputs.type} onChange={(type) => set({ type: type as Inputs['type'] })}
        options={HINGE_TYPES.map((type) => ({ value: type, label: types[type] }))} />
      {inputs.type !== 'living' && <label className="grid gap-1">Fit
        <select className={inputCls} value={inputs.clearance} disabled={busy} onChange={(event) => set({ clearance: event.target.value })}>
          {fits.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </label>}
      {fields.map(([key, label]) => <label key={key} className="grid gap-1">{label}
        <input className={inputCls} value={String(inputs[key] ?? '')} maxLength={255} disabled={busy} onChange={(event) => set({ [key]: event.target.value })} />
      </label>)}
      {inputs.type !== 'living' && <div className="flex gap-3">
        <label className="flex items-center gap-1.5"><input type="checkbox" checked={inputs.stop} disabled={busy} onChange={(event) => set({ stop: event.target.checked })} />Stop at the limit</label>
        <label className="flex items-center gap-1.5"><input type="checkbox" checked={inputs.flip} disabled={busy} onChange={(event) => set({ flip: event.target.checked })} />Flip</label>
      </div>}
      {inputs.type === 'living' && <p className="text-text-2">Living hinges suit PP, PETG or TPU. In PLA they crack after a few bends.</p>}
      <div className="flex gap-1">
        <Btn type="button" size="sm" disabled={!ready || busy} onClick={() => onPreview(sent())}>Preview</Btn>
        <Btn type="submit" size="sm" variant="primary" disabled={!ready || busy}>Apply</Btn>
        <Btn type="button" size="sm" disabled={busy} onClick={onClose}>Cancel</Btn>
      </div>
    </form>
  )
}
