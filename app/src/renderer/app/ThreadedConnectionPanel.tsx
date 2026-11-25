import { useState } from 'react'
import fits from '../../../../simplecad/data/fits.json'
import type { ThreadedConnectionEdit } from '@/shared/engine-protocol'
import { threadTables } from './HolePanel'
import { Btn, inputCls, useLivePreview, More } from '../ui'

type Inputs = ThreadedConnectionEdit['threadedConnection']['inputs']

/** SimpleCAD 0.4's Threaded connection defaults: the closest standard size, Normal clearance, printed, full engagement. */
export const defaultThreadedConnection: Inputs = { designation: '', clearance: 'normal', form: 'printed', length: '0' }

interface Props {
  busy: boolean
  /** The picked round faces, as words. */
  picked: string[]
  onClear: () => void
  onChange: () => void
  onPreview: (inputs: Inputs, align: boolean) => void
  onApply: (inputs: Inputs, align: boolean) => void
  onCancel: () => void
}

/** A shaft on one part and its mating hole on another, threaded as a matched printable pair: clearance on the hole. */
export function ThreadedConnectionPanel({ busy, picked, onClear, onChange, onPreview, onApply, onCancel }: Props) {
  const [inputs, setInputs] = useState(defaultThreadedConnection)
  const [align, setAlign] = useState(false)
  const set = (change: Partial<Inputs>) => { setInputs((current) => ({ ...current, ...change })); onChange() }
  const ready = picked.length === 2 && inputs.length.trim().length > 0
  useLivePreview(JSON.stringify([inputs, align, picked]), ready && !busy, () => onPreview(inputs, align))
  return (
    <form aria-label="Threaded Connection" className="grid shrink-0 gap-2 text-xs" onSubmit={(event) => {
      event.preventDefault()
      if (ready && !busy) onApply(inputs, align)
    }}>
      <div className="font-semibold">Threaded Connection</div>
      <div role="status" aria-label="Threaded faces" className="text-text-2">
        {picked.length === 0 ? 'Click the shaft on one part.' : picked.length === 1 ? `${picked[0]}, then click its mating hole on the other part.`
          : `${picked.join(' and ')}: both threads are made together, with the clearance on the hole.`}
      </div>
      {picked.length > 0 && <Btn type="button" size="sm" className="justify-self-start" disabled={busy} onClick={onClear}>Pick again</Btn>}
      <label className="grid gap-1">Thread size
        <select className={inputCls} value={inputs.designation} disabled={busy} onChange={(event) => set({ designation: event.target.value })}>
          <option value="">Closest standard size</option>
          {threadTables.map(([name, sizes]) => <optgroup key={name} label={name}>
            {sizes.map(({ designation }) => <option key={designation} value={designation}>{designation}</option>)}
          </optgroup>)}
        </select>
      </label>
      <More>
      <label className="grid gap-1">Printable clearance
        <select className={inputCls} value={inputs.clearance} disabled={busy} onChange={(event) => set({ clearance: event.target.value as Inputs['clearance'] })}>
          {Object.entries(fits.thread_clearance).map(([key, fit]) => <option key={key} value={key}>{fit.label} · {fit.clearance} mm</option>)}
        </select>
      </label>
      <label className="grid gap-1">Profile
        <select className={inputCls} value={inputs.form} disabled={busy} onChange={(event) => set({ form: event.target.value as Inputs['form'] })}>
          <option value="printed">Printed</option><option value="iso">ISO</option>
        </select>
      </label>
      <label className="grid gap-1">Length (mm, 0 = full engagement)
        <input className={inputCls} value={inputs.length} maxLength={255} disabled={busy} onChange={(event) => set({ length: event.target.value })} />
      </label>
      <label className="flex items-center gap-1.5">
        <input type="checkbox" checked={align} disabled={busy} onChange={(event) => { setAlign(event.target.checked); onChange() }} />
        Set the shaft into the hole first
      </label>
      </More>
      <div className="flex gap-1">
        <Btn type="submit" size="sm" variant="primary" disabled={!ready || busy}>Apply</Btn>
        <Btn type="button" size="sm" disabled={busy} onClick={onCancel}>Cancel</Btn>
      </div>
    </form>
  )
}
