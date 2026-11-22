import { useState } from 'react'
import { Btn, inputCls } from '../ui'

export const defaultOffsetPlane = { distance: '10 mm' }
export const defaultShell = { thickness: '2 mm' }
export const defaultResize = { delta: '1' }

interface Props {
  busy: boolean
  planar: boolean
  preset?: Record<string, string>
  onApply: (settings: { distance: string }) => void
  onChange: () => void
  onCancel: () => void
}

export function OffsetPlanePanel(props: Props) {
  return <FaceValuePanel {...props} name="Offset Plane" label="Distance" field="distance" initial={defaultOffsetPlane.distance}
    hint="Select a planar face." onApply={(value) => props.onApply({ distance: value })} />
}

export function ShellPanel(props: Omit<Props, 'onApply'> & { onApply: (settings: { thickness: string }) => void }) {
  return <FaceValuePanel {...props} name="Shell" label="Wall thickness" field="thickness" initial={defaultShell.thickness}
    hint="Select the face to open." onApply={(value) => props.onApply({ thickness: value })} />
}

/** History's edit of a round Push/Pull: how far the round face moves along its radius, in millimetres. */
export function ResizePanel(props: Omit<Props, 'onApply'> & { onApply: (settings: { delta: string }) => void }) {
  return <FaceValuePanel {...props} name="Resize" label="Radius change (mm)" field="delta" initial={defaultResize.delta}
    hint="Select a round face." onApply={(value) => props.onApply({ delta: value })} />
}

/** One expression on a picked face: an offset plane's distance, a shell's wall. */
function FaceValuePanel({ busy, planar, preset, onApply, onChange, onCancel, name, label, field, initial, hint }: Omit<Props, 'onApply'> & {
  name: string; label: string; field: string; initial: string; hint: string; onApply: (value: string) => void
}) {
  const [distance, setDistance] = useState(preset?.[field] ?? initial)
  const enabled = !busy && planar && !!distance.trim()
  return <form aria-label={name} className="grid gap-2 text-xs" onSubmit={(event) => {
    event.preventDefault()
    if (enabled) onApply(distance)
  }}>
    {!planar && <p>{hint}</p>}
    <label className="grid gap-1">{label}
      <input className={inputCls} value={distance} maxLength={256} disabled={busy}
        onChange={(event) => { setDistance(event.target.value); onChange() }} />
    </label>
    <div className="flex gap-1">
      <Btn type="submit" size="sm" variant="primary" disabled={!enabled}>Apply</Btn>
      <Btn type="button" size="sm" disabled={busy} onClick={onCancel}>Cancel</Btn>
    </div>
  </form>
}
