import { useState } from 'react'
import type { TextSettings } from '@/shared/engine-protocol'
import { Btn, inputCls, useLivePreview, More, firstField } from '../ui'

export const defaultTextSettings: TextSettings = {
  text: '', font_family: 'sans-serif', mode: 'raised',
  text_height: '8 mm', depth: '1 mm', offset_x: '0 mm', offset_y: '0 mm', rotation: '0°'
}

interface Props {
  busy: boolean
  planar: boolean
  preset?: Record<string, string>
  onChange: () => void
  onPreview: (settings: TextSettings) => void
  /** Where it goes (the picked face and point): a new place previews again. */
  at?: string
  onApply: (settings: TextSettings) => void
  onCancel: () => void
}

export function TextPanel({ busy, planar, preset, at, onChange, onPreview, onApply, onCancel }: Props) {
  const [settings, setSettings] = useState<TextSettings>(() => ({ ...defaultTextSettings, ...preset }))
  const enabled = !busy && planar && settings.text.trim().length > 0
  const field = <K extends keyof TextSettings>(key: K, value: TextSettings[K]) => {
    setSettings((previous) => ({ ...previous, [key]: value }))
    onChange()
  }
  useLivePreview(JSON.stringify([settings, at]), enabled, () => onPreview(settings))
  return <form aria-label="Text settings" className="grid max-h-[50%] shrink-0 gap-2 overflow-auto text-xs"
    onSubmit={(event) => { event.preventDefault(); if (enabled) onApply(settings) }}>
    <label className="grid gap-1">Text to place
      <input className={inputCls} maxLength={256} value={settings.text} disabled={busy} {...firstField}
        onChange={(event) => field('text', event.target.value)} />
    </label>
    <label className="grid gap-1">Text mode
      <select className={inputCls} value={settings.mode} disabled={busy}
        onChange={(event) => field('mode', event.target.value as TextSettings['mode'])}>
        <option value="raised">Raised</option>
        <option value="engraved">Engraved</option>
      </select>
    </label>
    <label className="grid gap-1">Height
      <input className={inputCls} value={settings.text_height} maxLength={256} disabled={busy}
        onChange={(event) => field('text_height', event.target.value)} />
    </label>
    <More>
    <label className="grid gap-1">Font family
      <select className={inputCls} value={settings.font_family} disabled={busy}
        onChange={(event) => field('font_family', event.target.value as TextSettings['font_family'])}>
        <option value="sans-serif">Sans serif</option>
        <option value="serif">Serif</option>
        <option value="monospace">Monospace</option>
      </select>
    </label>
    <div className="grid grid-cols-2 gap-2">
      {([
        ['depth', 'Depth'], ['offset_x', 'X offset'], ['offset_y', 'Y offset'], ['rotation', 'Rotation']
      ] as const).map(([key, label]) => <label key={key} className="grid min-w-0 gap-1">{label}
        <input className={`${inputCls} w-full`} value={settings[key]} maxLength={256} disabled={busy}
          onChange={(event) => field(key, event.target.value)} />
      </label>)}
    </div>
    </More>
    <div className="flex gap-1">
      <Btn type="submit" size="sm" variant="primary" disabled={!enabled}>Apply</Btn>
      <Btn type="button" size="sm" disabled={busy} onClick={onCancel}>Cancel</Btn>
    </div>
  </form>
}
