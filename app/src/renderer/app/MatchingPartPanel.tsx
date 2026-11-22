import { useState } from 'react'
import type { FaceOperation, ShapeEdit, StepInfo } from '@/shared/engine-protocol'

type ThreadSettings = Extract<FaceOperation, { faceOperation: 'matching_thread' }>['settings']
import { Btn, Segmented, inputCls } from '../ui'

interface Props {
  threads: StepInfo[]
  busy: boolean
  onPreview: (edit: ShapeEdit) => void
  onApply: (edit: ShapeEdit) => void
  /** A round face is selected, so a matching thread can be cut into it. */
  roundFace: boolean
  onThread: (settings: ThreadSettings) => void
  onChange: () => void
  onClose: () => void
}

export function MatchingPartPanel({ threads, busy, onPreview, onApply, roundFace, onThread, onChange, onClose }: Props) {
  const [source, setSource] = useState(threads.at(-1)?.id ?? '')
  /** Make a separate bolt or nut, or cut the matching thread into a round face of a part already made. */
  const [mode, setMode] = useState<'part' | 'thread'>('part')
  const [resize, setResize] = useState(false)
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
    <div className="grid gap-1">Make
      <Segmented label="Make" value={mode} onChange={(next) => { setMode(next); onChange() }}
        options={[{ value: 'part', label: nut ? 'A nut' : 'A bolt' }, { value: 'thread', label: 'Thread a round face' }]} />
    </div>
    {mode === 'thread' ? <>
      <p>{roundFace ? `Cuts a matching ${spec?.designation ?? ''} thread into the selected round face.` : 'Select the round face to thread, in the view or the face list.'}</p>
      <label className="flex items-center gap-2"><input type="checkbox" checked={resize} disabled={busy}
        onChange={(event) => { setResize(event.target.checked); onChange() }} />Resize the face to the thread's size</label>
      <div className="flex flex-wrap gap-1.5">
        <Btn size="sm" type="button" variant="primary" disabled={!spec || !roundFace || busy} onClick={() => spec && onThread({
          designation: spec.designation, clearance: (spec.clearance ?? 'normal') as ThreadSettings['clearance'], form: (spec.form ?? 'printed') as ThreadSettings['form'],
          left_hand: spec.left_hand === 'true' ? 'true' : 'false', resize: resize ? 'true' : 'false'
        })}>Cut thread</Btn>
        <Btn size="sm" type="button" onClick={onClose} disabled={busy}>Cancel</Btn>
      </div>
    </> : <>
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
    </>}
  </form>
}
