import { useState } from 'react'
import type { StepInfo } from '@/shared/engine-protocol'
import type { SketchSolidEdit } from '@/shared/sketch'
import { Btn, inputCls, useLivePreview, More, firstField } from '../ui'

export const profileOperations = {
  extrude: 'Extrude', revolve: 'Revolve', sweep: 'Sweep', loft: 'Loft', path_pattern: 'Pattern Along Path'
} as const

type Solid = SketchSolidEdit['sketchSolid']

interface Props {
  kind: Solid['kind']
  sketches: StepInfo[]
  bodies: string[]
  busy: boolean
  onChange: () => void
  onPreview: (edit: SketchSolidEdit) => void
  onApply: (edit: SketchSolidEdit) => void
  onClose: () => void
}

export function SketchSolidPanel({ kind, sketches, bodies, busy, onChange, onPreview, onApply, onClose }: Props) {
  const minimum = kind === 'sweep' || kind === 'loft' ? 2 : 1
  const [profiles, setProfiles] = useState(() => minimum === 1 ? [sketches.at(-1)?.id ?? ''] : ['', ''])
  const [amount, setAmount] = useState(kind === 'extrude' ? '10 mm' : kind === 'path_pattern' ? '4' : '360 deg')
  const [body, setBody] = useState(bodies.length === 1 ? bodies[0] : '')
  const [symmetric, setSymmetric] = useState(false)
  const [ruled, setRuled] = useState(false)
  const [contact, setContact] = useState(true)
  const [keepOrientation, setOrientation] = useState(false)
  const [axis, setAxis] = useState<'profile' | 'x' | 'y' | 'z'>('profile')
  const valid = profiles.every((id) => sketches.some((sketch) => sketch.id === id)) &&
    new Set(profiles).size === profiles.length &&
    (kind === 'path_pattern' ? bodies.includes(body) && !!amount.trim()
      : (kind !== 'extrude' && kind !== 'revolve') || !!amount.trim())

  useLivePreview(valid ? JSON.stringify(edit()) : '', valid && !busy, () => onPreview(edit()))
  function edit(): SketchSolidEdit {
    const inputs = kind === 'extrude' ? { distance: amount, symmetric } :
      kind === 'revolve' ? { angle: amount, axis_direction: {
        profile: undefined, x: [1, 0, 0], y: [0, 1, 0], z: [0, 0, 1]
      }[axis] } :
      kind === 'sweep' ? { contact, keep_orientation: keepOrientation } :
      kind === 'path_pattern' ? { body, count: amount } : { ruled }
    return { sketchSolid: { kind, profiles, inputs: JSON.stringify(inputs) } }
  }

  return <form aria-label={profileOperations[kind]} className="grid max-h-[50%] shrink-0 gap-2 overflow-auto text-xs"
    onSubmit={(event) => { event.preventDefault(); if (valid) onApply(edit()) }}>
    <strong>{profileOperations[kind]}</strong>
    {profiles.map((id, index) => {
      const label = kind === 'loft' ? `Section ${index + 1}` : index === 1 || kind === 'path_pattern' ? 'Path sketch' : 'Profile sketch'
      return <div key={index} className="flex items-end gap-1">
        <label className="grid min-w-0 flex-1 gap-1">{label}
          <select aria-label={label} className={inputCls} value={id} disabled={busy} onChange={(event) => {
            setProfiles(profiles.map((previous, i) => i === index ? event.target.value : previous))
            onChange()
          }}>
            <option value="">Select a sketch</option>
            {sketches.map((sketch) => <option key={sketch.id} value={sketch.id} disabled={sketch.id !== id && profiles.includes(sketch.id)}>{sketch.name}</option>)}
          </select>
        </label>
        {kind === 'loft' && profiles.length > minimum && <Btn type="button" size="sm" disabled={busy}
          aria-label={`Remove section ${index + 1}`} onClick={() => {
            setProfiles(profiles.filter((_, i) => i !== index)); onChange()
          }}>Remove</Btn>}
      </div>
    })}
    {kind === 'loft' && profiles.length < sketches.length && <Btn type="button" size="sm" disabled={busy}
      className="justify-self-start" onClick={() => { setProfiles([...profiles, '']); onChange() }}>Add section</Btn>}
    {(kind === 'extrude' || kind === 'revolve') && <label className="grid gap-1">
      {kind === 'extrude' ? 'Distance' : 'Angle'}
      <input className={inputCls} disabled={busy} value={amount} {...firstField} onChange={(event) => { setAmount(event.target.value); onChange() }} />
    </label>}
    {kind === 'path_pattern' && <>
      <label className="grid gap-1">Body
        <select aria-label="Body" className={inputCls} disabled={busy} value={body}
          onChange={(event) => { setBody(event.target.value); onChange() }}>
          <option value="">Select a body</option>
          {bodies.map((name) => <option key={name}>{name}</option>)}
        </select>
      </label>
      <label className="grid gap-1">Instances
        <input className={inputCls} disabled={busy} value={amount}
          onChange={(event) => { setAmount(event.target.value); onChange() }} />
      </label>
    </>}
    {kind !== 'path_pattern' && <More>
    {kind === 'revolve' && <label className="grid gap-1">Axis
      <select aria-label="Axis" className={inputCls} disabled={busy} value={axis} onChange={(event) => { setAxis(event.target.value as typeof axis); onChange() }}>
        <option value="profile">Sketch X</option>
        <option value="x">World X</option><option value="y">World Y</option><option value="z">World Z</option>
      </select>
    </label>}
    {kind === 'extrude' && <label className="flex items-center gap-1.5">
      <input type="checkbox" disabled={busy} checked={symmetric} onChange={(event) => { setSymmetric(event.target.checked); onChange() }} /> Symmetric
    </label>}
    {kind === 'loft' && <label className="flex items-center gap-1.5">
      <input type="checkbox" disabled={busy} checked={ruled} onChange={(event) => { setRuled(event.target.checked); onChange() }} /> Ruled
    </label>}
    {kind === 'sweep' && <>
      <label className="flex items-center gap-1.5">
        <input type="checkbox" disabled={busy} checked={contact} onChange={(event) => { setContact(event.target.checked); onChange() }} /> Move profile to path
      </label>
      <label className="flex items-center gap-1.5">
        <input type="checkbox" disabled={busy} checked={keepOrientation} onChange={(event) => { setOrientation(event.target.checked); onChange() }} /> Keep orientation
      </label>
    </>}
    </More>}
    <div className="flex flex-wrap gap-1.5">
      <Btn type="submit" size="sm" variant="primary" disabled={busy || !valid}>Apply</Btn>
      <Btn type="button" size="sm" disabled={busy} onClick={onClose}>Cancel</Btn>
    </div>
  </form>
}
