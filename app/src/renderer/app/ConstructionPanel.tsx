import { useState } from 'react'
import type { ConstructionEdit } from '@/shared/engine-protocol'
import { Btn, Segmented, inputCls } from '../ui'

export type Construction = ConstructionEdit['construction']
export type AxisFrom = 'edge' | 'face'

export const constructionLabels: Record<Construction, string> = {
  midplane: 'Midplane', plane_at_angle: 'Plane at Angle', plane_through_points: 'Plane through Points',
  tangent_plane: 'Tangent Plane', construction_axis: 'Axis', construction_point: 'Point'
}

/** What a tool picks, in this order: faces, then edges, then corners. */
export function needs(kind: Construction, axisFrom: AxisFrom) {
  return {
    midplane: { faces: 2, edges: 0, points: 0, hint: 'Pick the two faces it sits between.' },
    plane_at_angle: { faces: 1, edges: 1, points: 0, hint: 'Pick a flat face, then the edge to turn it about.' },
    plane_through_points: { faces: 0, edges: 0, points: 3, hint: 'Click near three corners.' },
    tangent_plane: { faces: 1, edges: 0, points: 0, hint: 'Pick the round face it touches.' },
    construction_axis: axisFrom === 'edge'
      ? { faces: 0, edges: 1, points: 0, hint: 'Pick the edge it runs along.' }
      : { faces: 1, edges: 0, points: 0, hint: 'Pick the round face it runs through.' },
    construction_point: { faces: 1, edges: 0, points: 0, hint: 'Pick a face: the point starts at its centre.' }
  }[kind]
}

/** Each tool's settings, as SimpleCAD 0.4 defaults them; History edits these. */
const settings: Record<Construction, Record<string, string>> = {
  midplane: {}, plane_through_points: {}, construction_axis: {},
  plane_at_angle: { angle: '45' }, tangent_plane: { around: '0' }, construction_point: { x: '0', y: '0', z: '0' }
}
const labels: Record<string, string> = { angle: 'Angle (°)', around: 'Around the axis (°)', x: 'X offset', y: 'Y offset', z: 'Z offset' }

interface Props {
  kind: Construction
  busy: boolean
  picked: { faces: number; edges: number; points: number }
  axisFrom: AxisFrom
  /** A saved step's settings: History edits them; its picks stay. */
  preset?: Record<string, string>
  onAxisFrom: (from: AxisFrom) => void
  onClear: () => void
  onApply: (inputs: Record<string, string>) => void
  onEdit: (changes: Record<string, string>) => void
  onCancel: () => void
}

export function ConstructionPanel({ kind, busy, picked, axisFrom, preset, onAxisFrom, onClear, onApply, onEdit, onCancel }: Props) {
  const [values, setValues] = useState<Record<string, string>>({ ...settings[kind], ...preset })
  const need = needs(kind, axisFrom)
  const complete = picked.faces >= need.faces && picked.edges >= need.edges && picked.points >= need.points
  const valid = Object.values(values).every((value) => value.trim()) && (!!preset || complete)
  const progress = (['faces', 'edges', 'points'] as const).filter((key) => need[key])
    .map((key) => `${Math.min(picked[key], need[key])} of ${need[key]} ${key === 'points' ? 'corners' : key}`).join(' · ')
  return (
    <form aria-label={constructionLabels[kind]} className="grid shrink-0 gap-2 text-xs" onSubmit={(event) => {
      event.preventDefault()
      if (!valid || busy) return
      if (!preset) onApply(values)
      else {
        const changes = Object.fromEntries(Object.entries(values).filter(([key, value]) => preset[key] !== value))
        if (Object.keys(changes).length) onEdit(changes)
      }
    }}>
      {!preset && kind === 'construction_axis' && <div className="grid gap-1">From
        <Segmented label="Axis from" value={axisFrom} onChange={(next) => { onAxisFrom(next); onClear() }}
          options={[{ value: 'edge', label: 'Edge' }, { value: 'face', label: 'Round face' }]} />
      </div>}
      {!preset && <>
        <p>{need.hint}</p>
        {progress && <p role="status" aria-label="Picks">{progress}</p>}
        <Btn type="button" size="sm" className="justify-self-start" disabled={busy || !(picked.faces + picked.edges + picked.points)}
          onClick={onClear}>Clear picks</Btn>
      </>}
      {Object.keys(values).map((key) => <label key={key} className="grid gap-1">{labels[key]}
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
