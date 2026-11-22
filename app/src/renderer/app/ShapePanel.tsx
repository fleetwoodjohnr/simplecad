import { useState } from 'react'
import type { ShapeEdit } from '@/shared/engine-protocol'
import { Btn, inputCls } from '../ui'

type Field = [name: string, label: string, initial: number]
const height = (initial: number): Field => ['height', 'Height', initial]
const radius = (initial: number): Field => ['radius', 'Radius', initial]
const size: Field = ['size', 'Size', 30]
const dimensions = (width: number, depth: number, tall: number): Field[] => [['width', 'Width', width], ['depth', 'Depth', depth], height(tall)]
const shapes: Record<string, { label: string; fields: Field[] }> = {
  box: { label: 'Box', fields: dimensions(40, 30, 20) },
  cylinder: { label: 'Cylinder', fields: [radius(12), height(30)] },
  sphere: { label: 'Sphere', fields: [radius(15)] },
  cone: { label: 'Cone', fields: [['bottom_radius', 'Bottom radius', 15], ['top_radius', 'Top radius', 0], height(30)] },
  torus: { label: 'Torus', fields: [['major_radius', 'Ring radius', 25], ['minor_radius', 'Tube radius', 6]] },
  tube: { label: 'Tube', fields: [['outer_radius', 'Outer radius', 15], ['inner_radius', 'Inner radius', 10], height(30)] },
  wedge: { label: 'Wedge', fields: [...dimensions(40, 30, 20), ['top_width', 'Top width', 0]] },
  oval: { label: 'Oval', fields: dimensions(40, 25, 10) },
  pill: { label: 'Pill', fields: dimensions(40, 15, 10) },
  star: { label: 'Star', fields: [size, ['inner_ratio', 'Inset', 0.45], height(10)] },
  vent_plate: { label: 'Vent plate', fields: [['width', 'Width', 60], ['depth', 'Depth', 60], ['thickness', 'Thickness', 3],
    ['across_flats', 'Hole size', 5], ['wall', 'Wall', 1.2], ['margin', 'Border', 2]] },
  heart: { label: 'Heart', fields: [size, height(10)] },
  cross: { label: 'Cross', fields: [size, ['arm_width', 'Arm width', 10], height(10)] },
  crescent: { label: 'Crescent', fields: [size, ['thickness', 'Thickness', 8], height(10)] },
  lightning: { label: 'Lightning', fields: [size, height(10)] },
  triangle: { label: 'Triangle', fields: [['across_corners', 'Corner circle', 30], height(10)] },
  pentagon: { label: 'Pentagon', fields: [['across_corners', 'Corner circle', 25], height(10)] },
  hexagon: { label: 'Hexagon', fields: [['across_flats', 'Across flats', 17], height(8)] },
  octagon: { label: 'Octagon', fields: [['across_flats', 'Across flats', 20], height(10)] },
  polygon_prism: { label: 'Polygon', fields: [['sides', 'Sides', 6], ['across_flats', 'Across flats', 17], height(8)] },
}

interface Props {
  busy: boolean
  saved?: { kind: string; name: string; values: Record<string, string> }
  onApply: (edit: ShapeEdit) => void
  onPreview: (edit: ShapeEdit) => void
  onChange: () => void
  onCancel: () => void
}

export function ShapePanel({ busy, saved, onApply, onPreview, onChange, onCancel }: Props) {
  const [kind, setKind] = useState(saved?.kind ?? 'box')
  const [edits, setEdits] = useState<Record<string, string>>({})
  const fields: Field[] = kind === 'polygon_prism' && saved?.values.across_corners !== undefined && saved.values.across_flats === undefined
    ? [['sides', 'Sides', 6], ['across_corners', 'Corner circle', 30], height(8)]
    : shapes[kind].fields
  const initialText = (key: string, initial: number) => saved?.values[key] ?? String(initial)
  const hasChanges = !saved || fields.some(([key, , initial]) =>
    edits[key] !== undefined && edits[key] !== initialText(key, initial))
  const submit = (commit: boolean) => {
    if (busy || !hasChanges) return
    const values = Object.fromEntries(fields.flatMap(([key, , initial]) => {
      const original = initialText(key, initial)
      const value = edits[key] ?? original
      return saved && original === value ? [] : [[key, value]]
    }))
    if (Object.values(values).some((value) => !value.trim())) return
    const run = commit ? onApply : onPreview
    run({ shapeKind: kind, values })
  }

  return <form aria-label={saved ? `Edit ${saved.name}` : 'Create shape'} className="grid max-h-[50%] shrink-0 gap-2 overflow-auto text-xs"
    onSubmit={(event) => { event.preventDefault(); submit(true) }}>
    {!saved && <label className="grid gap-1">Shape type
      <select className={inputCls} value={kind} disabled={busy} onChange={(event) => { setKind(event.target.value); setEdits({}) }}>
        {Object.entries(shapes).map(([key, shape]) => <option key={key} value={key}>{shape.label}</option>)}
      </select>
    </label>}
    {fields.map(([key, label, initial]) => <label key={key} className="grid gap-1">{label}
      <input className={inputCls} maxLength={255} value={edits[key] ?? initialText(key, initial)} required disabled={busy}
        onChange={(event) => { setEdits({ ...edits, [key]: event.target.value }); onChange() }} />
    </label>)}
    <div className="flex gap-2">
      <Btn type="submit" size="sm" variant="primary" disabled={busy || !hasChanges}>{saved ? 'Apply' : 'Create'}</Btn>
      {saved && <Btn type="button" size="sm" disabled={busy || !hasChanges} onClick={() => submit(false)}>Preview</Btn>}
      <Btn type="button" size="sm" disabled={busy} onClick={onCancel}>Cancel</Btn>
    </div>
  </form>
}
