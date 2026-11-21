import { useState } from 'react'
import type { ShapeEdit, StepInfo } from '@/shared/engine-protocol'
import { Btn, inputCls } from '../ui'

interface Props {
  threads: StepInfo[]
  busy: boolean
  onPreview: (edit: ShapeEdit) => void
  onApply: (edit: ShapeEdit) => void
  onChange: () => void
  onClose: () => void
}

export function MatchingPartPanel({ threads, busy, onPreview, onApply, onChange, onClose }: Props) {
  const [source, setSource] = useState(threads.at(-1)?.id ?? '')
  const [length, setLength] = useState('')
  const [threaded, setThreaded] = useState('')
  const [head, setHead] = useState('hex')
  const [position, setPosition] = useState({ x: '0 mm', y: '0 mm', z: '0 mm' })
  const spec = threads.find((step) => step.id === source)?.matchingThread
  const nut = spec?.internal === 'false'

  function edit() {
    if (!spec) return
    const { internal: _, ...thread } = spec
    const shapeKind = nut ? 'matching_nut' : 'matching_bolt'
    const sizes = nut ? { height: length } : { length, thread_length: threaded, head }
    const values = Object.fromEntries(Object.entries({ ...thread, ...sizes, ...position }).filter(([, value]) => value.trim()))
    return { shapeKind, values }
  }
  function send(action: (edit: ShapeEdit) => void) {
    const request = edit()
    if (request) action(request)
  }

  return <form aria-label="Matching part" className="grid max-h-[50%] shrink-0 gap-2 overflow-auto text-xs"
    onSubmit={(event) => { event.preventDefault(); send(onApply) }}>
    <label className="grid gap-1">Thread to match
      <select aria-label="Thread to match" className={inputCls} value={source} disabled={busy}
        onChange={(event) => { setSource(event.target.value); setLength(''); onChange() }}>
        {threads.map((step) => <option key={step.id} value={step.id}>{step.name} · {step.matchingThread?.designation}</option>)}
      </select>
    </label>
    {spec && <p className="text-text-2">{spec.designation} {nut ? 'nut' : 'bolt'}</p>}
    <label className="grid gap-1">{nut ? 'Height' : 'Shank length'}
      <input className={inputCls} value={length} placeholder="Use standard length" disabled={busy}
        onChange={(event) => { setLength(event.target.value); onChange() }} />
    </label>
    {!nut && <>
      <label className="grid gap-1">Threaded length
        <input className={inputCls} value={threaded} placeholder="Entire shank" disabled={busy}
          onChange={(event) => { setThreaded(event.target.value); onChange() }} />
      </label>
      <label className="grid gap-1">Head
        <select aria-label="Head" className={inputCls} value={head} disabled={busy}
          onChange={(event) => { setHead(event.target.value); onChange() }}>
          <option value="hex">Hex</option><option value="socket">Socket</option>
        </select>
      </label>
    </>}
    <div className="grid grid-cols-3 gap-1.5">
      {(['x', 'y', 'z'] as const).map((axis) => <label key={axis} className="grid gap-1"> {axis.toUpperCase()} offset
        <input className={inputCls} value={position[axis]} disabled={busy}
          onChange={(event) => { setPosition({ ...position, [axis]: event.target.value }); onChange() }} />
      </label>)}
    </div>
    <div className="flex flex-wrap gap-1.5">
      <Btn size="sm" type="button" disabled={!spec || busy} onClick={() => send(onPreview)}>Preview</Btn>
      <Btn size="sm" type="submit" variant="primary" disabled={!spec || busy}>Create</Btn>
      <Btn size="sm" type="button" onClick={onClose} disabled={busy}>Cancel</Btn>
    </div>
  </form>
}
