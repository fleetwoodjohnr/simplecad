import { useState } from 'react'
import type { BodyInfo, BodyOperationEdit, Vec3 } from '@/shared/engine-protocol'
import { Btn, Segmented, inputCls } from '../ui'

export type BodyOperation = BodyOperationEdit['bodyOperation']

export const bodyOperationLabels: Record<BodyOperation, string> = {
  mirror: 'Mirror', rectangular_pattern: 'Rectangular Pattern', circular_pattern: 'Circular Pattern', scale: 'Scale',
  boolean: 'Combine'
}
const combinations = [{ value: 'join', label: 'Join' }, { value: 'cut', label: 'Cut' }, { value: 'intersect', label: 'Intersect' }]

/** SimpleCAD 0.4's defaults; the settings History can edit later are the expressions. */
const defaults: Record<BodyOperation, Record<string, string>> = {
  mirror: { keep: 'true' },
  rectangular_pattern: { count_x: '3', count_y: '1', spacing_x: '20 mm', spacing_y: '20 mm' },
  circular_pattern: { count: '4', total_angle: '360' },
  scale: { factor: '2' },
  boolean: { operation: 'join', keep_tool: 'false' }
}
const flags = new Set(['keep', 'keep_tool', 'operation'])
const fieldLabels: Record<string, string> = {
  count_x: 'Count along X', count_y: 'Count along Y', spacing_x: 'Spacing X', spacing_y: 'Spacing Y',
  count: 'Count', total_angle: 'Total angle (°)', factor: 'Factor'
}
const axes = ['x', 'y', 'z'] as const
type Axis = typeof axes[number]
const unit = (axis: Axis): Vec3 => [Number(axis === 'x'), Number(axis === 'y'), Number(axis === 'z')]

interface Props {
  kind: BodyOperation
  bodies: BodyInfo[]
  body?: number
  busy: boolean
  /** A saved step's settings: only these are edited, and its directions stay as made. */
  preset?: Record<string, string>
  onBody: (body?: number) => void
  onChange: () => void
  onPreview: (edit: BodyOperationEdit) => void
  onApply: (edit: BodyOperationEdit) => void
  onEdit: (changes: Record<string, string>) => void
  onCancel: () => void
}

export function BodyOperationPanel({ kind, bodies, body, busy, preset, onBody, onChange, onPreview, onApply, onEdit, onCancel }: Props) {
  const [values, setValues] = useState<Record<string, string>>({ ...defaults[kind], ...preset })
  const [axis, setAxis] = useState<Axis>(kind === 'mirror' ? 'x' : 'z')
  const [center, setCenter] = useState<[string, string, string]>(['0', '0', '0'])
  const [tools, setTools] = useState<number[]>([])
  const name = bodyOperationLabels[kind]
  const set = (key: string, value: string) => { setValues((current) => ({ ...current, [key]: value })); onChange() }
  const point = center.map(Number) as Vec3
  const valid = Object.entries(values).every(([key, value]) => flags.has(key) || value.trim()) &&
    (!!preset || (body !== undefined && point.every(Number.isFinite) && (kind !== 'boolean' || tools.some((tool) => tool !== body))))
  const edit = (): BodyOperationEdit => {
    const { keep, keep_tool: keepTool, ...numbers } = values
    const inputs: BodyOperationEdit['inputs'] = { ...numbers }
    if (kind === 'boolean') Object.assign(inputs, { tools: tools.filter((tool) => tool !== body), keep_tool: keepTool === 'true' })
    if (kind === 'mirror') Object.assign(inputs, { normal: unit(axis), origin: point, keep: keep === 'true' })
    if (kind === 'circular_pattern') Object.assign(inputs, { axis: unit(axis), center: point })
    return { bodyOperation: kind, body: body!, inputs }
  }
  const submit = (commit: boolean) => {
    if (!valid || busy) return
    if (preset) {
      const changes = Object.fromEntries(Object.entries(values).filter(([key, value]) => preset[key] !== value))
      if (Object.keys(changes).length) onEdit(changes)
      return
    }
    ;(commit ? onApply : onPreview)(edit())
  }

  return (
    <form aria-label={name} className="grid max-h-[50%] shrink-0 gap-2 overflow-auto text-xs"
      onSubmit={(event) => { event.preventDefault(); submit(true) }}>
      {!preset && <label className="grid gap-1">Body
        <select className={inputCls} disabled={busy} value={body ?? ''}
          onChange={(event) => { onBody(event.target.value === '' ? undefined : Number(event.target.value)); onChange() }}>
          <option value="">Select a body</option>
          {bodies.map((part, index) => part.visible && !part.empty ? <option key={index} value={index}>{part.name}</option> : null)}
        </select>
      </label>}
      {!preset && (kind === 'mirror' || kind === 'circular_pattern') && <div className="grid gap-1">
        {kind === 'mirror' ? 'Mirror across' : 'Turn about'}
        <Segmented label={kind === 'mirror' ? 'Mirror plane' : 'Axis'} value={axis} onChange={(next) => { setAxis(next); onChange() }}
          options={axes.map((value) => ({ value, label: kind === 'mirror' ? { x: 'YZ', y: 'XZ', z: 'XY' }[value] : value.toUpperCase() }))} />
      </div>}
      {!preset && (kind === 'mirror' || kind === 'circular_pattern') && <fieldset className="grid grid-cols-3 gap-2" disabled={busy}>
        <legend className="pb-1">{kind === 'mirror' ? 'Through point (mm)' : 'Centre (mm)'}</legend>
        {axes.map((key, i) => <label key={key} className="grid gap-1">{key.toUpperCase()}
          <input className={inputCls} inputMode="decimal" value={center[i]}
            onChange={(event) => { setCenter((c) => c.map((v, k) => k === i ? event.target.value : v) as [string, string, string]); onChange() }} />
        </label>)}
      </fieldset>}
      {kind === 'boolean' && <div className="grid gap-1">Operation
        <Segmented label="Operation" value={values.operation} options={combinations} onChange={(next) => set('operation', next)} />
      </div>}
      {kind === 'boolean' && !preset && <fieldset className="grid gap-1" disabled={busy}>
        <legend className="pb-1">With</legend>
        {bodies.map((part, index) => part.visible && !part.empty && index !== body ? <label key={index} className="flex items-center gap-2">
          <input type="checkbox" checked={tools.includes(index)}
            onChange={(event) => { setTools((current) => event.target.checked ? [...current, index] : current.filter((tool) => tool !== index)); onChange() }} />
          {part.name}</label> : null)}
      </fieldset>}
      {kind === 'boolean' && <label className="flex items-center gap-2">
        <input type="checkbox" checked={values.keep_tool === 'true'} disabled={busy}
          onChange={(event) => set('keep_tool', String(event.target.checked))} />Keep the tool bodies</label>}
      {Object.keys(values).filter((key) => !flags.has(key)).map((key) => <label key={key} className="grid gap-1">{fieldLabels[key]}
        <input className={inputCls} value={values[key]} maxLength={255} disabled={busy} onChange={(event) => set(key, event.target.value)} />
      </label>)}
      {kind === 'mirror' && <label className="flex items-center gap-2">
        <input type="checkbox" checked={values.keep === 'true'} disabled={busy}
          onChange={(event) => set('keep', String(event.target.checked))} />Keep the original</label>}
      <div className="flex gap-1">
        <Btn type="submit" size="sm" variant="primary" disabled={!valid || busy}>Apply</Btn>
        {!preset && <Btn type="button" size="sm" disabled={!valid || busy} onClick={() => submit(false)}>Preview</Btn>}
        <Btn type="button" size="sm" disabled={busy} onClick={onCancel}>Cancel</Btn>
      </div>
    </form>
  )
}
