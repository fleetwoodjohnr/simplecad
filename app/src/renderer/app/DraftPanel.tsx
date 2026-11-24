import { useState } from 'react'
import type { Vec3 } from '@/shared/engine-protocol'
import { Btn, inputCls } from '../ui'

/** What a draft is made with, as SimpleCAD 0.4's `DraftFeature` reads it: the taper, the way the part leaves its mould
 * (or the plate), and a point on the plane where the faces keep their size. */
export interface DraftInputs { angle: string; pull_direction: Vec3; neutral_plane: Vec3 }

const pulls: [string, Vec3][] = [['+Z (up)', [0, 0, 1]], ['−Z (down)', [0, 0, -1]], ['+X', [1, 0, 0]], ['−X', [-1, 0, 0]],
  ['+Y', [0, 1, 0]], ['−Y', [0, -1, 0]]]

interface Props {
  busy: boolean
  /** The picked faces, as words, all of one body. */
  picked: string[]
  onClear: () => void
  onChange: () => void
  onPreview: (inputs: DraftInputs) => void
  onApply: (inputs: DraftInputs) => void
  onCancel: () => void
}

/** Tapers the picked faces of one body by an angle, so a part releases from a mould or prints cleanly; 0.4 builds the
 * same step, 3° about a pull of +Z through the origin unless told otherwise. */
export function DraftPanel({ busy, picked, onClear, onChange, onPreview, onApply, onCancel }: Props) {
  const [angle, setAngle] = useState('3')
  const [pull, setPull] = useState(0)
  const [neutral, setNeutral] = useState<[string, string, string]>(['0', '0', '0'])
  const point = neutral.map(Number) as Vec3
  const ready = picked.length > 0 && angle.trim().length > 0 && point.every(Number.isFinite)
  const inputs = (): DraftInputs => ({ angle, pull_direction: pulls[pull][1], neutral_plane: point })
  return (
    <form aria-label="Draft" className="grid shrink-0 gap-2 text-xs" onSubmit={(event) => {
      event.preventDefault()
      if (ready && !busy) onApply(inputs())
    }}>
      <div className="font-semibold">Draft</div>
      <div role="status" aria-label="Drafted faces" className="text-text-2">
        {picked.length === 0 ? 'Click the faces to taper, all on one body.' : picked.join(', ')}
      </div>
      {picked.length > 0 && <Btn type="button" size="sm" className="justify-self-start" disabled={busy} onClick={onClear}>Pick again</Btn>}
      <label className="grid gap-1">Draft angle (°)
        <input className={inputCls} value={angle} maxLength={255} disabled={busy} onChange={(event) => { setAngle(event.target.value); onChange() }} />
      </label>
      <label className="grid gap-1">Pull direction
        <select className={inputCls} value={pull} disabled={busy} onChange={(event) => { setPull(Number(event.target.value)); onChange() }}>
          {pulls.map(([label], index) => <option key={label} value={index}>{label}</option>)}
        </select>
      </label>
      <fieldset className="grid grid-cols-3 gap-1" disabled={busy}>
        <legend className="mb-1">Neutral plane through (mm)</legend>
        {(['X', 'Y', 'Z'] as const).map((axis, index) => <label key={axis} className="grid gap-1">{axis}
          <input className={inputCls} type="number" value={neutral[index]} onChange={(event) => {
            const next = [...neutral] as [string, string, string]
            next[index] = event.target.value
            setNeutral(next)
            onChange()
          }} />
        </label>)}
      </fieldset>
      <div className="flex gap-1">
        <Btn type="button" size="sm" disabled={!ready || busy} onClick={() => onPreview(inputs())}>Preview</Btn>
        <Btn type="submit" size="sm" variant="primary" disabled={!ready || busy}>Apply</Btn>
        <Btn type="button" size="sm" disabled={busy} onClick={onCancel}>Cancel</Btn>
      </div>
    </form>
  )
}
