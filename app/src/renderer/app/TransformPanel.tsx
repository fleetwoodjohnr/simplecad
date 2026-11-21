import { useState } from 'react'
import type { BodyInfo, Transform, Vec3 } from '@/shared/engine-protocol'
import { Btn, inputCls } from '../ui'

const axes = ['X', 'Y', 'Z'] as const
type Entries = [string, string, string]
const origin: Vec3 = [0, 0, 0]

interface Props {
  kind: Transform['kind']
  bodies: BodyInfo[]
  body?: number
  busy: boolean
  onBody: (body?: number) => void
  onChange: () => void
  onPreview: (body: number, transform: Transform) => void
  onApply: (body: number, transform: Transform) => void
  onCancel: () => void
}

export function TransformPanel({ kind, bodies, body, busy, onBody, onChange, onPreview, onApply, onCancel }: Props) {
  const [values, setValues] = useState<Entries>(['', '', ''])
  const [pivotMode, setPivotMode] = useState<'center' | 'origin' | 'custom'>('center')
  const [custom, setCustom] = useState<Entries>(['0', '0', '0'])
  const rotation = kind === 'rotate'
  const name = rotation ? 'Rotate' : 'Move'
  const pivot = pivotMode === 'center' ? undefined : pivotMode === 'origin' ? origin : custom.map(Number) as Vec3
  const entered = values.some((value) => value.trim() !== '' && Number(value) !== 0)
  const valid = body !== undefined && entered && (!pivot || pivot.every((n) => Number.isFinite(n) && Math.abs(n) <= 1e9))
  const submit = (commit: boolean) => {
    if (!valid || busy) return
    const expressions = values.map((value) => value.trim() || '0') as Entries
    const transform: Transform = rotation ? { kind, values: expressions, pivot } : { kind, values: expressions }
    const run = commit ? onApply : onPreview
    run(body, transform)
  }

  return (
    <form aria-label={name} className="grid max-h-[50%] shrink-0 gap-2 overflow-auto text-xs"
      onSubmit={(event) => { event.preventDefault(); submit(true) }}>
      <label className="grid gap-1">Body
        <select className={inputCls} disabled={busy} value={body ?? ''}
          onChange={(event) => onBody(event.target.value === '' ? undefined : Number(event.target.value))}>
          <option value="">Select a body</option>
          {bodies.map((part, index) => part.visible && !part.empty ? <option key={index} value={index}>{part.name}</option> : null)}
        </select>
      </label>
      <fieldset className="grid grid-cols-3 gap-2" disabled={busy}>
        {axes.map((axis, index) => <label key={axis} className="grid min-w-0 gap-1">{axis} ({rotation ? '°' : 'mm'})
          <input className={`${inputCls} w-full`} maxLength={255} placeholder="0" value={values[index]}
            onChange={(event) => {
              const next = [...values] as Entries
              next[index] = event.target.value
              setValues(next)
              onChange()
            }} />
        </label>)}
      </fieldset>
      {rotation && <>
        <label className="grid gap-1">Turn about
          <select className={inputCls} disabled={busy} value={pivotMode} onChange={(event) => {
            setPivotMode(event.target.value as typeof pivotMode)
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
                onChange()
              }} />
          </label>)}
        </fieldset>}
      </>}
      <div className="flex gap-2">
        <Btn size="sm" variant="primary" type="submit" disabled={busy || !valid}>Apply</Btn>
        <Btn size="sm" type="button" disabled={busy || !valid} onClick={() => submit(false)}>Preview</Btn>
        <Btn size="sm" type="button" disabled={busy} onClick={onCancel}>Cancel</Btn>
      </div>
    </form>
  )
}
