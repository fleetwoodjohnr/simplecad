import type { Measurement, MeasurePick } from '@/shared/engine-protocol'
import { Btn, inputCls } from '../ui'

const number = new Intl.NumberFormat(undefined, { maximumFractionDigits: 3, useGrouping: false })

interface Props {
  kind: MeasurePick['kind']
  selections: { key: string; label: string }[]
  readings: Measurement[]
  loading: boolean
  onKind: (kind: MeasurePick['kind']) => void
  onRemove: (key: string) => void
  onClose: () => void
}

export function MeasurePanel({ kind, selections, readings, loading, onKind, onRemove, onClose }: Props) {
  return <section aria-label="Measure" className="grid max-h-[45%] shrink-0 gap-2 overflow-auto text-xs">
    <label className="grid gap-1">Measure selection
      <select className={inputCls} value={kind} onChange={(event) => onKind(event.target.value as MeasurePick['kind'])}>
        <option value="face">Faces</option>
        <option value="body">Bodies</option>
      </select>
    </label>
    {selections.length === 0 ? <p>{kind === 'face' ? 'Select faces in the view or the face list.' : 'Select bodies in the view or the body list.'}</p> :
      <ul aria-label="Measured items" className="grid gap-1">
        {selections.map(({ key, label }) => <li key={key} className="flex min-w-0 items-center justify-between gap-2">
          <span className="min-w-0 truncate" title={label}>{label}</span>
          <Btn size="sm" type="button" variant="ghost" aria-label={`Remove ${label}`} onClick={() => onRemove(key)}>Remove</Btn>
        </li>)}
      </ul>}
    <dl aria-label="Measurements" aria-busy={loading} className="grid gap-1" aria-live="polite">
      {readings.map(({ label, values, unit }) => <div key={label} className="flex justify-between gap-2">
        <dt className="font-medium">{label}</dt>
        <dd className="text-right tabular-nums">
          {values.map((value) => number.format(value)).join(['Normal', 'Axis', 'Centre', 'Centre of mass'].includes(label) ? ', ' : ' × ')}
          {unit && ` ${unit}`}
        </dd>
      </div>)}
    </dl>
    <Btn size="sm" type="button" className="justify-self-start" onClick={onClose}>Close</Btn>
  </section>
}
