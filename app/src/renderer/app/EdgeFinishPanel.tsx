import { useState } from 'react'
import { Btn, inputCls } from '../ui'

export type EdgeFinish = 'fillet' | 'chamfer'

const fields = {
  fillet: { name: 'Fillet', key: 'radius', label: 'Radius', initial: '1 mm' },
  chamfer: { name: 'Chamfer', key: 'distance', label: 'Distance', initial: '1 mm' }
} as const

interface Props {
  kind: EdgeFinish
  busy: boolean
  /** How many edges are picked, and on which body. */
  picked: number
  body?: string
  /** A saved step's settings: History edits its size; its edges stay as picked. */
  preset?: Record<string, string>
  onAll: () => void
  onClear: () => void
  onChange: () => void
  onPreview: (value: string) => void
  onApply: (value: string) => void
  onEdit: (changes: Record<string, string>) => void
  onCancel: () => void
}

export function EdgeFinishPanel({ kind, busy, picked, body, preset, onAll, onClear, onChange, onPreview, onApply, onEdit, onCancel }: Props) {
  const { name, key, label, initial } = fields[kind]
  const [value, setValue] = useState(preset?.[key] ?? initial)
  const valid = !!value.trim() && (!!preset || picked > 0)
  return (
    <form aria-label={name} className="grid shrink-0 gap-2 text-xs" onSubmit={(event) => {
      event.preventDefault()
      if (!valid || busy) return
      if (!preset) onApply(value)
      else if (value !== preset[key]) onEdit({ [key]: value })
    }}>
      {!preset && <>
        <p role="status" aria-label="Picked edges">
          {picked ? `${picked} edge${picked === 1 ? '' : 's'} of ${body}` : 'Click the edges to finish.'}
        </p>
        <div className="flex gap-1">
          <Btn type="button" size="sm" disabled={busy} onClick={() => { onAll(); onChange() }}>All edges</Btn>
          <Btn type="button" size="sm" disabled={busy || !picked} onClick={() => { onClear(); onChange() }}>Clear</Btn>
        </div>
      </>}
      <label className="grid gap-1">{label}
        <input className={inputCls} value={value} maxLength={255} disabled={busy}
          onChange={(event) => { setValue(event.target.value); onChange() }} />
      </label>
      <div className="flex gap-1">
        <Btn type="submit" size="sm" variant="primary" disabled={!valid || busy}>Apply</Btn>
        {!preset && <Btn type="button" size="sm" disabled={!valid || busy} onClick={() => onPreview(value)}>Preview</Btn>}
        <Btn type="button" size="sm" disabled={busy} onClick={onCancel}>Cancel</Btn>
      </div>
    </form>
  )
}
