import { useState } from 'react'
import { ANCHORS, type SectionReplaceEdit } from '@/shared/engine-protocol'
import { Btn, inputCls } from '../ui'

type Settings = SectionReplaceEdit['inputs']
type Anchor = typeof ANCHORS[number]

const anchorLabels: Record<Anchor, string> = {
  top_left: 'Top left', top: 'Top', top_right: 'Top right', left: 'Left', center: 'Centre', right: 'Right',
  bottom_left: 'Bottom left', bottom: 'Bottom', bottom_right: 'Bottom right'
}
const fields = [['u', 'Offset U'], ['v', 'Offset V'], ['boundary', 'Boundary'], ['setback', 'Setback']] as const

interface Props {
  busy: boolean
  /** The picked faces, as labels: the replacement's first, then the target's. */
  picked: string[]
  /** A saved step's settings, as History edits them; its faces stay as picked. */
  preset?: Record<string, string>
  onClear: () => void
  onChange: () => void
  onPreview: (settings: Settings) => void
  onApply: (settings: Settings) => void
  onEdit: (changes: Record<string, string>) => void
  onCancel: () => void
}

export function SectionReplacePanel({ busy, picked, preset, onClear, onChange, onPreview, onApply, onEdit, onCancel }: Props) {
  const [settings, setSettings] = useState<Settings>({
    moving_anchor: 'center', target_anchor: 'center', u: preset?.u ?? '0', v: preset?.v ?? '0',
    boundary: preset?.boundary ?? '0.5 mm', setback: preset?.setback ?? '0'
  })
  const set = (next: Partial<Settings>) => { setSettings((current) => ({ ...current, ...next })); onChange() }
  const valid = fields.every(([key]) => settings[key].trim()) && (!!preset || picked.length === 2)
  const anchor = (key: 'moving_anchor' | 'target_anchor', label: string) => <label className="grid gap-1">{label}
    <select className={inputCls} value={settings[key]} disabled={busy} onChange={(event) => set({ [key]: event.target.value as Anchor })}>
      {ANCHORS.map((value) => <option key={value} value={value}>{anchorLabels[value]}</option>)}
    </select>
  </label>
  return (
    <form aria-label="Section Replace" className="grid shrink-0 gap-2 text-xs" onSubmit={(event) => {
      event.preventDefault()
      if (!valid || busy) return
      if (!preset) onApply(settings)
      else {
        const changes = Object.fromEntries(fields.flatMap(([key]) => preset[key] !== settings[key] ? [[key, settings[key]]] : []))
        if (Object.keys(changes).length) onEdit(changes)
      }
    }}>
      {!preset && <>
        <p role="status" aria-label="Section picks">
          {picked.length === 0 ? 'Pick the face of the replacement that meets the target.'
            : picked.length === 1 ? `Replacement: ${picked[0]}. Now pick the target face.`
            : `Replacement: ${picked[0]} · Target: ${picked[1]}`}
        </p>
        <Btn type="button" size="sm" className="justify-self-start" disabled={busy || !picked.length} onClick={onClear}>Clear picks</Btn>
        <div className="grid grid-cols-2 gap-2">{anchor('moving_anchor', 'Replacement anchor')}{anchor('target_anchor', 'Target anchor')}</div>
      </>}
      <div className="grid grid-cols-2 gap-2">
        {fields.map(([key, label]) => <label key={key} className="grid gap-1">{label}
          <input className={inputCls} value={settings[key]} maxLength={255} disabled={busy} onChange={(event) => set({ [key]: event.target.value })} />
        </label>)}
      </div>
      <div className="flex gap-1">
        <Btn type="submit" size="sm" variant="primary" disabled={!valid || busy}>Apply</Btn>
        {!preset && <Btn type="button" size="sm" disabled={!valid || busy} onClick={() => onPreview(settings)}>Preview</Btn>}
        <Btn type="button" size="sm" disabled={busy} onClick={onCancel}>Cancel</Btn>
      </div>
    </form>
  )
}
