import type { Measurement, MeasurePick } from '@/shared/engine-protocol'
import { displayLength, type DisplayUnit } from '@/shared/preferences'
import { Btn, inputCls } from '../ui'

const number = new Intl.NumberFormat(undefined, { maximumFractionDigits: 3, useGrouping: false })
const dimensions: Record<string, number> = { mm: 1, 'mm²': 2, 'mm³': 3 }

interface Props {
  kind: MeasurePick['kind']
  selections: { key: string; label: string }[]
  readings: Measurement[]
  loading: boolean
  displayUnit: DisplayUnit
  onKind: (kind: MeasurePick['kind']) => void
  onRemove: (key: string) => void
  onClose: () => void
}

export function MeasurePanel({ kind, selections, readings, loading, displayUnit, onKind, onRemove, onClose }: Props) {
  return <section aria-label="Measure" className="grid max-h-[45%] shrink-0 gap-2 overflow-auto text-xs">
    <label className="grid gap-1">Measure selection
      <select className={inputCls} value={kind} onChange={(event) => onKind(event.target.value as MeasurePick['kind'])}>
        <option value="face">Faces</option>
        <option value="edge">Edges</option>
        <option value="vertex">Vertices</option>
        <option value="body">Bodies</option>
      </select>
    </label>
    {selections.length === 0 ? <p>{{ face: 'Select faces in the view or the face list.', edge: 'Click edges in the view.',
      vertex: 'Click near the corners to measure.', body: 'Select bodies in the view or the body list.' }[kind]}</p> :
      <ul aria-label="Measured items" className="grid gap-1">
        {selections.map(({ key, label }) => <li key={key} className="flex min-w-0 items-center justify-between gap-2">
          <span className="min-w-0 truncate" title={label}>{label}</span>
          <Btn size="sm" type="button" variant="ghost" aria-label={`Remove ${label}`} onClick={() => onRemove(key)}>Remove</Btn>
        </li>)}
      </ul>}
    <dl aria-label="Measurements" aria-busy={loading} className="grid gap-1" aria-live="polite">
      {readings.map(({ label, values, unit }) => {
        const power = dimensions[unit]
        const formatted = values.map((value) => number.format(power ? displayLength(value, displayUnit, power) : value))
        const suffix = power ? displayUnit + unit.slice(2) : unit
        return <div key={label} className="flex justify-between gap-2">
          <dt className="font-medium">{label}</dt>
          <dd className="text-right tabular-nums">
            {formatted.join(['Normal', 'Axis', 'Centre', 'Centre of mass', 'Position'].includes(label) ? ', ' : ' × ')}
            {suffix && ` ${suffix}`}
          </dd>
        </div>
      })}
    </dl>
    <Btn size="sm" type="button" className="justify-self-start" onClick={onClose}>Close</Btn>
  </section>
}
