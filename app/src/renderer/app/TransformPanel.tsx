import { useState } from 'react'
import type { BodyInfo, Transform, Vec3 } from '@/shared/engine-protocol'
import { Btn, inputCls, useLivePreview, More, firstField } from '../ui'

const axes = ['X', 'Y', 'Z'] as const
type Entries = [string, string, string]
const origin: Vec3 = [0, 0, 0]
type PivotMode = 'center' | 'origin' | 'custom'
const pivotAt = (mode: PivotMode, point: Entries) => mode === 'center' ? undefined : mode === 'origin' ? origin : point.map(Number) as Vec3
const validPivot = (point: Vec3) => point.every((value) => Number.isFinite(value) && Math.abs(value) <= 1e9)

interface Props {
  kind: Transform['kind']
  bodies: BodyInfo[]
  body?: number
  busy: boolean
  snap: number
  preferredSnap: number
  onSnap: (step: number) => void
  onPivot: (pivot?: Vec3) => void
  onBody: (body?: number) => void
  onChange: () => void
  onPreview: (body: number, transform: Transform) => void
  onApply: (body: number, transform: Transform) => void
  onCancel: () => void
}

export function TransformPanel({ kind, bodies, body, busy, snap, preferredSnap, onSnap, onPivot, onBody, onChange, onPreview, onApply, onCancel }: Props) {
  const [values, setValues] = useState<Entries>(['', '', ''])
  const [pivotMode, setPivotMode] = useState<PivotMode>('center')
  const [custom, setCustom] = useState<Entries>(['0', '0', '0'])
  const rotation = kind === 'rotate'
  const name = rotation ? 'Rotate' : 'Move'
  const increments = [...new Set([preferredSnap, ...(rotation ? [5, 15, 45, 90] : [0.1, 0.5, 1, 5, 10])])].sort((a, b) => a - b)
  const pivot = pivotAt(pivotMode, custom)
  const setDragPivot = (mode: PivotMode, point: Entries) => {
    const next = pivotAt(mode, point)
    onPivot(next && validPivot(next) ? next : undefined)
  }
  const entered = values.some((value) => value.trim() !== '' && Number(value) !== 0)
  const valid = body !== undefined && entered && (!pivot || validPivot(pivot))
  const submit = (commit: boolean) => {
    if (!valid || busy) return
    const expressions = values.map((value) => value.trim() || '0') as Entries
    const transform: Transform = rotation ? { kind, values: expressions, pivot } : { kind, values: expressions }
    const run = commit ? onApply : onPreview
    run(body, transform)
  }

  useLivePreview(JSON.stringify([body, values, pivot]), valid && !busy, () => submit(false))
  // Which part: asked first when none is chosen, and kept under More… when one is.
  const bodyField = <label className="grid gap-1">Body
    <select className={inputCls} disabled={busy} value={body ?? ''}
      onChange={(event) => onBody(event.target.value === '' ? undefined : Number(event.target.value))}>
      <option value="">Select a body</option>
      {bodies.map((part, index) => part.visible && !part.empty ? <option key={index} value={index}>{part.name}</option> : null)}
    </select>
  </label>
  return (
    <form aria-label={name} className="grid max-h-[50%] shrink-0 gap-2 overflow-auto text-xs"
      onSubmit={(event) => { event.preventDefault(); submit(true) }}>
      {body === undefined && bodyField}
      <fieldset className="grid grid-cols-3 gap-2" disabled={busy}>
        {axes.map((axis, index) => <label key={axis} className="grid min-w-0 gap-1">{axis} ({rotation ? '°' : 'mm'})
          <input className={`${inputCls} w-full`} maxLength={255} placeholder="0" value={values[index]} {...(index === 0 ? firstField : {})}
            onChange={(event) => {
              const next = [...values] as Entries
              next[index] = event.target.value
              setValues(next)
              onChange()
            }} />
        </label>)}
      </fieldset>
      <More>
      {body !== undefined && bodyField}
      {rotation && <>
        <label className="grid gap-1">Turn about
          <select className={inputCls} disabled={busy} value={pivotMode} onChange={(event) => {
            const next = event.target.value as typeof pivotMode
            setPivotMode(next)
            setDragPivot(next, custom)
            onChange()
          }}>
            <option value="center">Body center</option>
            <option value="origin">World origin</option>
            <option value="custom">Custom point</option>
          </select>
        </label>
        {pivotMode === 'custom' && <fieldset className="grid grid-cols-3 gap-2" disabled={busy}>
          {axes.map((axis, index) => <label key={axis} className="grid min-w-0 gap-1">Pivot {axis}
            <input type="number" step="any" className={`${inputCls} w-full`} value={custom[index]}
              onChange={(event) => {
                const next = [...custom] as Entries
                next[index] = event.target.value
                setCustom(next)
                setDragPivot(pivotMode, next)
                onChange()
              }} />
          </label>)}
        </fieldset>}
      </>}
      <label className="grid gap-1">Drag snapping
        <select className={inputCls} disabled={busy} value={snap} onChange={(event) => onSnap(Number(event.target.value))}>
          <option value={0}>Off</option>
          {increments.map((step) =>
            <option key={step} value={step}>{step} {rotation ? '°' : 'mm'}</option>)}
        </select>
      </label>
      </More>
      <div className="flex gap-2">
        <Btn size="sm" variant="primary" type="submit" disabled={busy || !valid}>Apply</Btn>
        <Btn size="sm" type="button" disabled={busy} onClick={onCancel}>Cancel</Btn>
      </div>
    </form>
  )
}
