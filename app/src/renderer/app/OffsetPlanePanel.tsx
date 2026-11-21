import { useState } from 'react'
import { Btn, inputCls } from '../ui'

export const defaultOffsetPlane = { distance: '10 mm' }

interface Props {
  busy: boolean
  planar: boolean
  preset?: Record<string, string>
  onApply: (settings: { distance: string }) => void
  onChange: () => void
  onCancel: () => void
}

export function OffsetPlanePanel({ busy, planar, preset, onApply, onChange, onCancel }: Props) {
  const [distance, setDistance] = useState(preset?.distance ?? defaultOffsetPlane.distance)
  const enabled = !busy && planar && !!distance.trim()
  return <form aria-label="Offset Plane" className="grid gap-2 text-xs" onSubmit={(event) => {
    event.preventDefault()
    if (enabled) onApply({ distance })
  }}>
    {!planar && <p>Select a planar face.</p>}
    <label className="grid gap-1">Distance
      <input className={inputCls} value={distance} maxLength={256} disabled={busy}
        onChange={(event) => { setDistance(event.target.value); onChange() }} />
    </label>
    <div className="flex gap-1">
      <Btn type="submit" size="sm" variant="primary" disabled={!enabled}>Apply</Btn>
      <Btn type="button" size="sm" disabled={busy} onClick={onCancel}>Cancel</Btn>
    </div>
  </form>
}
