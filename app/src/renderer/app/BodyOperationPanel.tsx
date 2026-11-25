import { useState } from 'react'
import type { BodyInfo, BodyOperationEdit, Vec3 } from '@/shared/engine-protocol'
import { Btn, Segmented, inputCls, useLivePreview, More, firstField } from '../ui'

export type BodyOperation = BodyOperationEdit['bodyOperation']

export const bodyOperationLabels: Record<BodyOperation, string> = {
  mirror: 'Mirror', rectangular_pattern: 'Rectangular Pattern', circular_pattern: 'Circular Pattern', scale: 'Scale',
  boolean: 'Combine', split: 'Split', draft: 'Draft'
}
const combinations = [{ value: 'cut', label: 'Subtract' }, { value: 'join', label: 'Join' }, { value: 'intersect', label: 'Intersect' }]

/** SimpleCAD 0.4's defaults; the settings History can edit later are the expressions. */
const defaults: Record<BodyOperation, Record<string, string>> = {
  mirror: { keep: 'true' },
  rectangular_pattern: { count_x: '3', count_y: '1', spacing_x: '20 mm', spacing_y: '20 mm' },
  circular_pattern: { count: '4', total_angle: '360' },
  scale: { factor: '2' },
  boolean: { operation: 'join', keep_tool: 'false' },
  split: { position: '0' },
  // Draft has its own panel (it picks faces); this is DraftFeature's default.
  draft: { angle: '3' }
}
const flags = new Set(['keep', 'keep_tool', 'operation'])
const fieldLabels: Record<string, string> = {
  count_x: 'Count along X', count_y: 'Count along Y', spacing_x: 'Spacing X', spacing_y: 'Spacing Y',
  count: 'Count', total_angle: 'Total angle (°)', factor: 'Factor', position: 'Offset from the centre'
}
const axes = ['x', 'y', 'z'] as const
type Axis = typeof axes[number]
const unit = (axis: Axis): Vec3 => [Number(axis === 'x'), Number(axis === 'y'), Number(axis === 'z')]

interface Props {
  kind: BodyOperation
  bodies: BodyInfo[]
  body?: number
  /** The body's centre, which a split plane runs through. */
  centre?: Vec3
  busy: boolean
  /** A saved step's settings: only these are edited, and its directions stay as made. */
  preset?: Record<string, string>
  /** A Combine opened on chosen parts: its tools and operation to start with. */
  tools?: number[]
  /** Given, the tools are the window's (a click in the view adds or takes one away): `tools` is what they are now. */
  onTools?: (tools: number[]) => void
  operation?: string
  /** Told when Subtract, Join or Intersect is chosen, so the view can colour the parts by what they do. */
  onOperation?: (operation: string) => void
  /** Split's plane, held by the window so it can be dragged in the view. */
  split?: { axis: Axis; position: string; onAxis: (axis: Axis) => void; onPosition: (position: string) => void }
  onBody: (body?: number) => void
  onChange: () => void
  onPreview: (edit: BodyOperationEdit) => void
  onApply: (edit: BodyOperationEdit) => void
  onEdit: (changes: Record<string, string>) => void
  onCancel: () => void
}

/** 0.2.0's line saying which part is kept and which do the cutting, joining or overlapping. */
function combineSentence(operation: string, body: number | undefined, tools: number[], bodies: BodyInfo[]) {
  const kept = body === undefined ? undefined : bodies[body]?.name
  const names = tools.filter((tool) => tool !== body).map((tool) => bodies[tool]?.name ?? '')
  const list = names.length > 1 ? `${names.slice(0, -1).join(', ')} and ${names.at(-1)}` : names[0]
  if (kept === undefined) return 'Click the part to keep.'
  if (!list) return `Keeping ${kept}. Click the part to ${operation === 'cut' ? 'cut from it' : operation === 'join' ? 'join to it' : 'overlap it'}.`
  if (operation === 'cut') return `Removing ${list} from ${kept}. The result keeps the name ${kept}.`
  if (operation === 'join') return `Joining ${list} to ${kept}.`
  return `Keeping where ${kept} and ${list} overlap.`
}

export function BodyOperationPanel({ kind, bodies, body, centre, busy, preset, tools: chosen, onTools, operation, onOperation, split, onBody, onChange, onPreview, onApply, onEdit, onCancel }: Props) {
  const [own, setValues] = useState<Record<string, string>>({ ...defaults[kind], ...(operation ? { operation } : {}), ...preset })
  const [ownAxis, setAxis] = useState<Axis>(kind === 'mirror' ? 'x' : 'z')
  const held = kind === 'split' && !preset ? split : undefined
  const values = held ? { ...own, position: held.position } : own
  const axis = held?.axis ?? ownAxis
  const [center, setCenter] = useState<[string, string, string]>(['0', '0', '0'])
  const [ownTools, setOwnTools] = useState<number[]>(chosen ?? [])
  const tools = onTools ? chosen ?? [] : ownTools
  const setTools = (next: number[]) => onTools ? onTools(next) : setOwnTools(next)
  const name = bodyOperationLabels[kind]
  const set = (key: string, value: string) => {
    if (held && key === 'position') held.onPosition(value)
    else setValues((current) => ({ ...current, [key]: value }))
    onChange()
  }
  const point = center.map(Number) as Vec3
  const valid = Object.entries(values).every(([key, value]) => flags.has(key) || value.trim()) &&
    (!!preset || (body !== undefined && point.every(Number.isFinite) && (kind !== 'boolean' || tools.some((tool) => tool !== body))))
  const edit = (): BodyOperationEdit => {
    const { keep, keep_tool: keepTool, ...numbers } = values
    const inputs: BodyOperationEdit['inputs'] = { ...numbers }
    if (kind === 'boolean') Object.assign(inputs, { tools: tools.filter((tool) => tool !== body), keep_tool: keepTool === 'true' })
    if (kind === 'mirror') Object.assign(inputs, { normal: unit(axis), origin: point, keep: keep === 'true' })
    if (kind === 'circular_pattern') Object.assign(inputs, { axis: unit(axis), center: point })
    if (kind === 'split') Object.assign(inputs, { normal: unit(axis), origin: centre ?? [0, 0, 0] })
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

  useLivePreview(valid ? JSON.stringify(edit()) : '', valid && !busy && !preset, () => submit(false))
  // Which part: asked first when none is chosen, and kept under More… when one is.
  const bodyField = <label className="grid gap-1">Body
    <select className={inputCls} disabled={busy} value={body ?? ''}
      onChange={(event) => { onBody(event.target.value === '' ? undefined : Number(event.target.value)); onChange() }}>
      <option value="">Select a body</option>
      {bodies.map((part, index) => part.visible && !part.empty ? <option key={index} value={index}>{part.name}</option> : null)}
    </select>
  </label>
  return (
    <form aria-label={name} className="grid max-h-[50%] shrink-0 gap-2 overflow-auto text-xs"
      onSubmit={(event) => { event.preventDefault(); submit(true) }}>
      {!preset && body === undefined && bodyField}
      {!preset && (kind === 'mirror' || kind === 'circular_pattern' || kind === 'split') && <div className="grid gap-1">
        {{ mirror: 'Mirror across', split: 'Cut across', circular_pattern: 'Turn about' }[kind]}
        <Segmented label={kind === 'circular_pattern' ? 'Axis' : kind === 'split' ? 'Split plane' : 'Mirror plane'} value={axis}
          onChange={(next) => { if (held) held.onAxis(next); else setAxis(next); onChange() }}
          options={axes.map((value) => ({ value, label: kind === 'circular_pattern' ? value.toUpperCase() : { x: 'YZ', y: 'XZ', z: 'XY' }[value] }))} />
      </div>}
      {Object.keys(values).filter((key) => !flags.has(key)).map((key, index) => <label key={key} className="grid gap-1">{fieldLabels[key]}
        <input className={inputCls} value={values[key]} maxLength={255} disabled={busy} {...(index === 0 ? firstField : {})}
          onChange={(event) => set(key, event.target.value)} />
      </label>)}
      {kind === 'boolean' && <div className="grid gap-1">Operation
        <Segmented label="Operation" value={values.operation} options={combinations} onChange={(next) => { set('operation', next); onOperation?.(next) }} />
      </div>}
      {kind === 'boolean' && !preset && <div className="grid gap-1">
        <p role="status" aria-label="What is combined">{combineSentence(values.operation, body, tools, bodies)}</p>
        <Btn type="button" size="sm" className="justify-self-start" disabled={busy || body === undefined || !tools.length}
          onClick={() => {
            // As 0.2.0's Swap: the first tool is kept and the part that was kept becomes a tool.
            const [next, ...rest] = tools
            onBody(next)
            setTools([...rest, body!])
            onChange()
          }}>Swap</Btn>
      </div>}
      <More>
      {kind === 'boolean' && !preset && <fieldset className="grid gap-1" disabled={busy}>
        <legend className="pb-1">With</legend>
        {bodies.map((part, index) => part.visible && !part.empty && index !== body ? <label key={index} className="flex items-center gap-2">
          <input type="checkbox" checked={tools.includes(index)}
            onChange={(event) => { setTools(event.target.checked ? [...tools, index] : tools.filter((tool) => tool !== index)); onChange() }} />
          {part.name}</label> : null)}
      </fieldset>}
      {!preset && body !== undefined && bodyField}
      {!preset && (kind === 'mirror' || kind === 'circular_pattern') && <fieldset className="grid grid-cols-3 gap-2" disabled={busy}>
        <legend className="pb-1">{kind === 'mirror' ? 'Through point (mm)' : 'Centre (mm)'}</legend>
        {axes.map((key, i) => <label key={key} className="grid gap-1">{key.toUpperCase()}
          <input className={inputCls} inputMode="decimal" value={center[i]}
            onChange={(event) => { setCenter((c) => c.map((v, k) => k === i ? event.target.value : v) as [string, string, string]); onChange() }} />
        </label>)}
      </fieldset>}
      {kind === 'mirror' && <label className="flex items-center gap-2">
        <input type="checkbox" checked={values.keep === 'true'} disabled={busy}
          onChange={(event) => set('keep', String(event.target.checked))} />Keep the original</label>}
      {kind === 'boolean' && <label className="flex items-center gap-2">
        <input type="checkbox" checked={values.keep_tool === 'true'} disabled={busy}
          onChange={(event) => set('keep_tool', String(event.target.checked))} />Keep the tool bodies</label>}
      </More>
      <div className="flex gap-1">
        <Btn type="submit" size="sm" variant="primary" disabled={!valid || busy}>Apply</Btn>
        <Btn type="button" size="sm" disabled={busy} onClick={onCancel}>Cancel</Btn>
      </div>
    </form>
  )
}
