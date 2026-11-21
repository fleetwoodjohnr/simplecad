import { useState } from 'react'
import type { BodyInfo, HoleSettings, ThreadSettings } from '@/shared/engine-protocol'
import fits from '../../../../simplecad/data/fits.json'
import metric from '../../../../simplecad/data/threads/iso_metric.json'
import printed from '../../../../simplecad/data/threads/printed.json'
import unc from '../../../../simplecad/data/threads/unc.json'
import unf from '../../../../simplecad/data/threads/unf.json'
import bsp from '../../../../simplecad/data/threads/bsp.json'
import npt from '../../../../simplecad/data/threads/npt.json'
import { Btn, inputCls } from '../ui'

const threadDefaults: ThreadOptions = {
  designation: '', clearance: 'normal', form: 'printed', from_end: 'top', left_hand: 'false'
}
export const defaultThreadSettings: ThreadSettings = { ...threadDefaults, length: '0 mm', resize: 'false' }
export const defaultHoleSettings: HoleSettings = {
  ...threadDefaults, style: 'simple', depth_mode: 'through', diameter: '6 mm', depth: '10 mm',
  counterbore_diameter: '', counterbore_depth: '', countersink_diameter: '', countersink_angle: '90 °',
  thread_length: '0 mm'
}
const threadTables = [
  ['Metric', metric.sizes], ['Printed', printed.sizes], ['UNC', unc.sizes],
  ['UNF', unf.sizes], ['BSP', bsp.sizes], ['NPT', npt.sizes]
] as const

type ThreadOptions = Pick<ThreadSettings, 'designation' | 'clearance' | 'form' | 'from_end' | 'left_hand'>

function ThreadOptionsFields({ options, onChange }: { options: ThreadOptions; onChange: (settings: Partial<ThreadOptions>) => void }) {
  return <>
    <label className="grid gap-1">Thread size
      <select className={inputCls} value={options.designation} onChange={(event) => onChange({ designation: event.target.value })}>
        <option value="">Closest standard size</option>
        {threadTables.map(([name, sizes]) => <optgroup key={name} label={name}>
          {sizes.map(({ designation }) => <option key={designation} value={designation}>{designation}</option>)}
        </optgroup>)}
      </select>
    </label>
    <label className="grid gap-1">Thread clearance
      <select className={inputCls} value={options.clearance}
        onChange={(event) => onChange({ clearance: event.target.value as ThreadSettings['clearance'] })}>
        {Object.entries(fits.thread_clearance).map(([key, fit]) => <option key={key} value={key}>{fit.label} · {fit.clearance} mm</option>)}
      </select>
    </label>
    <div className="grid grid-cols-2 gap-2">
      <label className="grid min-w-0 gap-1">Profile
        <select className={inputCls} value={options.form} onChange={(event) => onChange({ form: event.target.value as ThreadSettings['form'] })}>
          <option value="printed">Printed</option><option value="iso">ISO</option>
        </select>
      </label>
      <label className="grid min-w-0 gap-1">Start at
        <select className={inputCls} value={options.from_end} onChange={(event) => onChange({ from_end: event.target.value as ThreadSettings['from_end'] })}>
          <option value="top">Top</option><option value="bottom">Bottom</option>
        </select>
      </label>
    </div>
    <label className="flex items-center gap-2">
      <input type="checkbox" checked={options.left_hand === 'true'} onChange={(event) => onChange({ left_hand: event.target.checked ? 'true' : 'false' })} />
      Left-hand thread
    </label>
  </>
}

function ExpressionField({ label, value, onChange, placeholder }: {
  label: string; value: string; onChange: (value: string) => void; placeholder?: string
}) {
  return <label className="grid gap-1">{label}
    <input className={inputCls} maxLength={255} value={value} placeholder={placeholder}
      onChange={(event) => onChange(event.target.value)} />
  </label>
}

function FaceActions({ enabled, onPreview, onCancel }: { enabled: boolean; onPreview: () => void; onCancel: () => void }) {
  return <div className="flex gap-2">
    <Btn type="submit" size="sm" variant="primary" disabled={!enabled}>Apply</Btn>
    <Btn type="button" size="sm" disabled={!enabled} onClick={onPreview}>Preview</Btn>
    <Btn type="button" size="sm" onClick={onCancel}>Cancel</Btn>
  </div>
}

interface FacePanelProps {
  preset?: Record<string, string>
  selected: boolean
  busy: boolean
  onChange: () => void
  onCancel: () => void
}

export function HolePanel({ bodies, body, preset, selected, busy, onChange, onCancel, onPreview, onApply }: FacePanelProps & {
  bodies: BodyInfo[]; body?: number
  onPreview: (settings: HoleSettings, untilBody?: number) => void
  onApply: (settings: HoleSettings, untilBody?: number) => void
}) {
  const [settings, setSettings] = useState<HoleSettings>(() => ({ ...defaultHoleSettings, ...preset }))
  const [target, setTarget] = useState<number>()
  const values = (patch: Partial<HoleSettings>) => { setSettings((current) => ({ ...current, ...patch })); onChange() }
  const field = (key: keyof HoleSettings, label: string, placeholder?: string) =>
    <ExpressionField label={label} value={settings[key]} onChange={(value) => values({ [key]: value })} placeholder={placeholder} />
  const until = settings.depth_mode === 'to_object' ? target : undefined
  const enabled = selected && !!settings.diameter.trim() &&
    (settings.depth_mode !== 'to_object' || !!preset || (until !== undefined && until !== body))
  const submit = (run: typeof onApply) => { if (enabled && !busy) run(settings, until) }
  return <form aria-label="Hole" className="max-h-[50%] shrink-0 overflow-auto text-xs"
    onSubmit={(event) => { event.preventDefault(); submit(onApply) }}>
    <fieldset disabled={busy} className="grid gap-2">
      {!selected && <p>Select a flat face.</p>}
      <label className="grid gap-1">Style
        <select className={inputCls} value={settings.style}
          onChange={(event) => values({ style: event.target.value as HoleSettings['style'] })}>
          <option value="simple">Simple</option><option value="counterbore">Counterbore</option>
          <option value="countersink">Countersink</option><option value="threaded">Threaded</option>
        </select>
      </label>
      {field('diameter', 'Diameter')}
      <label className="grid gap-1">Depth mode
        <select className={inputCls} value={settings.depth_mode}
          onChange={(event) => values({ depth_mode: event.target.value as HoleSettings['depth_mode'] })}>
          <option value="through">Through all</option><option value="blind">To a depth</option>
          {(!preset || preset.depth_mode === 'to_object') && <option value="to_object">To another body</option>}
        </select>
      </label>
      {settings.depth_mode === 'blind' && field('depth', 'Depth')}
      {settings.depth_mode === 'to_object' && !preset && <label className="grid gap-1">Stop at body
        <select className={inputCls} value={target ?? ''} onChange={(event) => {
          setTarget(event.target.value === '' ? undefined : Number(event.target.value)); onChange()
        }}>
          <option value="">Select a body</option>
          {bodies.map((part, index) => index !== body && !part.empty ? <option key={index} value={index}>{part.name}</option> : null)}
        </select>
      </label>}
      {settings.style === 'counterbore' && <>
        {field('counterbore_diameter', 'Recess diameter', '1.8 × diameter')}
        {field('counterbore_depth', 'Recess depth', '0.6 × diameter')}
      </>}
      {settings.style === 'countersink' && <>
        {field('countersink_diameter', 'Recess diameter', '2 × diameter')}
        {field('countersink_angle', 'Recess angle')}
      </>}
      {settings.style === 'threaded' && <>
        <ThreadOptionsFields options={settings} onChange={values} />
        {field('thread_length', 'Threaded length')}
      </>}
      <FaceActions enabled={enabled} onCancel={onCancel} onPreview={() => submit(onPreview)} />
    </fieldset>
  </form>
}

export function ThreadPanel({ preset, selected, busy, onChange, onCancel, onPreview, onApply }: FacePanelProps & {
  onPreview: (settings: ThreadSettings) => void; onApply: (settings: ThreadSettings) => void
}) {
  const [settings, setSettings] = useState<ThreadSettings>(() => ({ ...defaultThreadSettings, ...preset }))
  const values = (patch: Partial<ThreadSettings>) => { setSettings((current) => ({ ...current, ...patch })); onChange() }
  return <form aria-label="Thread" className="max-h-[50%] shrink-0 overflow-auto text-xs"
    onSubmit={(event) => { event.preventDefault(); if (selected && !busy) onApply(settings) }}>
    <fieldset disabled={busy} className="grid gap-2">
      {!selected && <p>Select a cylindrical face.</p>}
      <ThreadOptionsFields options={settings} onChange={values} />
      <ExpressionField label="Threaded length" value={settings.length} onChange={(length) => values({ length })} />
      <label className="flex items-center gap-2">
        <input type="checkbox" checked={settings.resize === 'true'} onChange={(event) => values({ resize: event.target.checked ? 'true' : 'false' })} />
        Resize to fit
      </label>
      <FaceActions enabled={selected} onCancel={onCancel} onPreview={() => { if (!busy) onPreview(settings) }} />
    </fieldset>
  </form>
}
