import { useState } from 'react'
import type { TextureSettings } from '@/shared/engine-protocol'
import { Btn, inputCls } from '../ui'

const patterns = [
  ['horizontal_lines', 'Horizontal lines'], ['vertical_lines', 'Vertical lines'],
  ['diagonal_lines', 'Diagonal lines'], ['crosshatch', 'Crosshatch'], ['grid', 'Grid'],
  ['hexagon', 'Hexagon / honeycomb'], ['diamond', 'Diamond pattern'], ['knurling', 'Knurling'],
  ['ribbed', 'Ribbed texture'], ['wave', 'Wave pattern'], ['dots', 'Dots'],
  ['raised_bumps', 'Raised bumps'], ['recessed_grooves', 'Recessed grooves'],
  ['geometric', 'Geometric pattern'], ['vent', 'Vent-style pattern'], ['grip', 'Grip texture']
] as const

const initial: TextureSettings = {
  pattern: 'horizontal_lines', mode: 'raised', size: '3 mm', spacing: '2 mm', depth: '0.6 mm',
  offset_u: '0 mm', offset_v: '0 mm', rotation: '0°', edge_clearance: '3 mm',
  hole_clearance: '2 mm', text_clearance: '1.5 mm', other_clearance: '2 mm'
}

interface Props {
  busy: boolean
  selected: boolean
  onChange: () => void
  onPreview: (settings: TextureSettings) => void
  onApply: (settings: TextureSettings) => void
  onCancel: () => void
}

export function TexturePanel({ busy, selected, onChange, onPreview, onApply, onCancel }: Props) {
  const [settings, setSettings] = useState(initial)
  const enabled = !busy && selected
  const field = <K extends keyof TextureSettings>(key: K, value: TextureSettings[K]) => {
    setSettings((previous) => ({ ...previous, [key]: value }))
    onChange()
  }
  const changePattern = (pattern: string) => {
    const mode = ['dots', 'recessed_grooves', 'vent'].includes(pattern) ? 'recessed' : 'raised'
    setSettings((previous) => ({ ...previous, pattern, mode }))
    onChange()
  }
  return <form aria-label="Face Texture settings" className="grid max-h-[50%] shrink-0 gap-2 overflow-auto text-xs"
    onSubmit={(event) => { event.preventDefault(); if (enabled) onApply(settings) }}>
    <label className="grid gap-1">Pattern
      <select className={inputCls} value={settings.pattern} disabled={busy}
        onChange={(event) => changePattern(event.target.value)}>
        {patterns.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
      </select>
    </label>
    <label className="grid gap-1">Texture mode
      <select className={inputCls} value={settings.mode} disabled={busy}
        onChange={(event) => field('mode', event.target.value as TextureSettings['mode'])}>
        <option value="raised">Raised</option>
        <option value="recessed">Recessed</option>
        {settings.pattern === 'vent' && <option value="through">Through wall</option>}
      </select>
    </label>
    <div className="grid grid-cols-2 gap-2">
      {([
        ['size', 'Size'], ['spacing', 'Spacing'], ['depth', 'Depth'], ['rotation', 'Rotation'],
        ['offset_u', 'U offset'], ['offset_v', 'V offset'], ['edge_clearance', 'Edge clearance'],
        ['hole_clearance', 'Hole clearance'], ['text_clearance', 'Text clearance'], ['other_clearance', 'Other clearance']
      ] as const).map(([key, label]) => <label key={key} className="grid min-w-0 gap-1">{label}
        <input className={`${inputCls} w-full`} value={settings[key]} maxLength={256}
          disabled={busy || (settings.mode === 'through' && key === 'depth')}
          onChange={(event) => field(key, event.target.value)} />
      </label>)}
    </div>
    <div className="flex gap-1">
      <Btn type="button" size="sm" disabled={!enabled} onClick={() => onPreview(settings)}>Preview</Btn>
      <Btn type="submit" size="sm" variant="primary" disabled={!enabled}>Apply</Btn>
      <Btn type="button" size="sm" disabled={busy} onClick={onCancel}>Cancel</Btn>
    </div>
  </form>
}
