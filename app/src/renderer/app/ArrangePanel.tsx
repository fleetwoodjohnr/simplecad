import { useState } from 'react'
import type { ArrangeEdit } from '@/shared/engine-protocol'
import { Btn, Segmented, inputCls } from '../ui'

type Settings = ArrangeEdit['inputs']

interface Props {
  busy: boolean
  /** The bodies the row takes (those chosen in the Bodies list, or every one shown), by name. */
  names: string[]
  /** A saved step's settings, as History edits them. */
  preset?: Record<string, string>
  onChange: () => void
  onPreview: (settings: Settings) => void
  onApply: (settings: Settings) => void
  onEdit: (changes: Record<string, string>) => void
  onCancel: () => void
}

export function ArrangePanel({ busy, names, preset, onChange, onPreview, onApply, onEdit, onCancel }: Props) {
  const [settings, setSettings] = useState<Settings>({
    mode: preset?.mode === 'fixed_gap' ? 'fixed_gap' : 'equal_gaps',
    axis: preset?.axis === 'y' || preset?.axis === 'z' ? preset.axis : 'x',
    gap: preset?.gap ?? '10 mm'
  })
  const set = (next: Partial<Settings>) => { setSettings((current) => ({ ...current, ...next })); onChange() }
  const enough = settings.mode === 'equal_gaps' ? 3 : 2
  const valid = !!settings.gap.trim() && (!!preset || names.length >= enough)
  return (
    <form aria-label="Arrange" className="grid shrink-0 gap-2 text-xs" onSubmit={(event) => {
      event.preventDefault()
      if (!valid || busy) return
      if (!preset) onApply(settings)
      else {
        const changes = Object.fromEntries(Object.entries(settings).filter(([key, value]) => preset[key] !== value))
        if (Object.keys(changes).length) onEdit(changes)
      }
    }}>
      {!preset && <p role="status" aria-label="Arranged bodies">
        {names.length >= enough ? names.join(', ') : `Choose ${enough} or more bodies in the Bodies list (Ctrl-click).`}
      </p>}
      <div className="grid gap-1">Spacing
        <Segmented label="Spacing" value={settings.mode} onChange={(mode) => set({ mode })}
          options={[{ value: 'equal_gaps', label: 'Equal gaps' }, { value: 'fixed_gap', label: 'Fixed gap' }]} />
      </div>
      <div className="grid gap-1">Along
        <Segmented label="Along" value={settings.axis} onChange={(axis) => set({ axis })}
          options={(['x', 'y', 'z'] as const).map((value) => ({ value, label: value.toUpperCase() }))} />
      </div>
      {settings.mode === 'fixed_gap' && <label className="grid gap-1">Gap
        <input className={inputCls} value={settings.gap} maxLength={255} disabled={busy} onChange={(event) => set({ gap: event.target.value })} />
      </label>}
      <div className="flex gap-1">
        <Btn type="submit" size="sm" variant="primary" disabled={!valid || busy}>Apply</Btn>
        {!preset && <Btn type="button" size="sm" disabled={!valid || busy} onClick={() => onPreview(settings)}>Preview</Btn>}
        <Btn type="button" size="sm" disabled={busy} onClick={onCancel}>Cancel</Btn>
      </div>
    </form>
  )
}
