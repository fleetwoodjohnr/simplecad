import { useEffect, useRef, useState } from 'react'
import type { ArrangeEdit, ArrangementNote, ArrangePattern, ArrangePatternSettings, ArrangeRow } from '@/shared/engine-protocol'
import { Btn, Segmented, inputCls, useLivePreview, More } from '../ui'

/** What the window is waiting for a click on. */
export type ArrangeRole = 'target' | 'contact' | 'edges'

const PATTERNS: [ArrangePattern | 'row', string][] = [['grid', 'Grid'], ['linear', 'Linear'], ['radial', 'Radial'],
  ['staggered', 'Staggered'], ['packed', 'Packed'], ['custom', 'Custom'], ['row', 'Row (no face)']]
const ANCHORS = [['top_left', '↖'], ['top', '↑'], ['top_right', '↗'], ['left', '←'], ['center', '●'], ['right', '→'],
  ['bottom_left', '↙'], ['bottom', '↓'], ['bottom_right', '↘']] as const

type Text = 'gap' | 'gap_y' | 'margin' | 'inset_left' | 'inset_right' | 'inset_top' | 'inset_bottom' | 'direction' | 'rotation' |
  'radius' | 'start_angle' | 'normal_offset'
type Switch = 'contain' | 'avoid_holes' | 'avoid_bodies' | 'auto_rotate' | 'face_center' | 'orient'
interface Settings extends Record<Text, string>, Record<Switch, boolean> {
  pattern: ArrangePattern | 'row'
  spacing: 'auto' | 'fixed'
  boundary: 'auto' | 'manual'
  align: string
  fill: 'rows' | 'columns'
  distribute: 'between' | 'evenly'
  rows: number
  columns: number
  mode: ArrangeRow['mode']
  axis: ArrangeRow['axis']
  row_gap: string
}

/** SimpleCAD 0.4's Arrange panel defaults, which Reset goes back to. */
const DEFAULTS: Settings = {
  pattern: 'row', spacing: 'auto', boundary: 'auto', align: 'center', fill: 'rows', distribute: 'between', rows: 0, columns: 0,
  contain: true, avoid_holes: true, avoid_bodies: true, auto_rotate: false, face_center: false, orient: true,
  gap: '3', gap_y: '3', margin: '2', inset_left: '0', inset_right: '0', inset_top: '0', inset_bottom: '0', direction: '0',
  rotation: '0', radius: '0', start_angle: '0', normal_offset: '0', mode: 'equal_gaps', axis: 'x', row_gap: '10 mm'
}

/** A saved step's settings, as History shows them, over the defaults. */
function recorded(preset: Record<string, string>): Settings {
  const out: Settings = { ...DEFAULTS, pattern: (preset.pattern as Settings['pattern']) ?? 'row' }
  const record = out as unknown as Record<string, unknown>
  for (const [key, value] of Object.entries(preset)) {
    if (!(key in DEFAULTS) || key === 'gap' || key === 'pattern') continue
    const fallback = record[key]
    record[key] = typeof fallback === 'boolean' ? value === 'true' : typeof fallback === 'number' ? Number(value) || 0 : value
  }
  if (out.pattern === 'row') {
    out.mode = preset.mode === 'fixed_gap' ? 'fixed_gap' : 'equal_gaps'
    out.axis = preset.axis === 'y' || preset.axis === 'z' ? preset.axis : 'x'
    out.row_gap = preset.gap ?? '10 mm'
  } else if (preset.gap !== undefined) {
    out.gap = preset.gap
  }
  return out
}

/** `ArrangePanel._inputs`: each pattern writes only the settings it reads. */
function patternInputs(s: Settings, move: [number, number, number]): ArrangePatternSettings {
  const pattern = s.pattern as ArrangePattern
  const inputs: ArrangePatternSettings = {
    pattern, spacing: s.spacing, boundary: s.boundary, gap: s.gap, margin: s.margin, rotation: s.rotation,
    normal_offset: s.normal_offset, contain: s.contain, avoid_holes: s.avoid_holes, avoid_bodies: s.avoid_bodies,
    auto_rotate: s.auto_rotate, orient: s.orient, move_x: move[0], move_y: move[1], move_normal: move[2]
  }
  if (pattern !== 'radial') Object.assign(inputs, { direction: s.direction, align: s.align })
  if (['grid', 'staggered', 'custom'].includes(pattern)) Object.assign(inputs, { rows: s.rows, columns: s.columns })
  if (pattern === 'custom') Object.assign(inputs, { gap_y: s.gap_y, fill: s.fill, distribute: s.distribute })
  if (pattern === 'radial') Object.assign(inputs, { radius: s.radius, start_angle: s.start_angle, face_center: s.face_center })
  if (s.boundary === 'manual') {
    Object.assign(inputs, { inset_left: s.inset_left, inset_right: s.inset_right, inset_top: s.inset_top, inset_bottom: s.inset_bottom })
  }
  return inputs
}

interface Props {
  busy: boolean
  /** The bodies arranged, by name. */
  names: string[]
  /** A saved step's settings, as History edits them. */
  preset?: Record<string, string>
  /** The target face, in words. */
  target?: string
  plate: boolean
  role?: ArrangeRole
  contacts: number
  edges: number
  /** The latest preview's fit. */
  note?: ArrangementNote
  /** How far the pattern was dragged: along the face's U and V, and off it. */
  move: [number, number, number]
  onRole: (role?: ArrangeRole) => void
  onPlate: (on: boolean) => void
  /** Settings went back to the defaults: the drag goes too. */
  onReset: () => void
  onChange: () => void
  onPreview: (inputs: ArrangeEdit['inputs']) => void
  onApply: (inputs: ArrangeEdit['inputs']) => void
  /** The pattern a new arrangement starts with (a Grid from the selection bar, waiting for its face). */
  pattern?: Settings['pattern']
  onEdit: (changes: Record<string, string>) => void
  onCancel: () => void
}

/** Several bodies laid out without fusing or renaming any: in a row where they are, or in a pattern on a face or the
 * build plate, as SimpleCAD 0.4's Arrange panel lays them. */
export function ArrangePanel({ busy, names, preset, pattern, target, plate, role, contacts, edges, note, move, onRole, onPlate, onReset,
  onChange, onPreview, onApply, onEdit, onCancel }: Props) {
  const [settings, setSettings] = useState<Settings>(() => preset ? recorded(preset) : { ...DEFAULTS, ...(pattern ? { pattern } : {}) })
  const s = settings
  const set = (next: Partial<Settings>) => { setSettings((current) => ({ ...current, ...next })); onChange() }
  const row = s.pattern === 'row'
  const editingRow = !!preset && (preset.pattern ?? 'row') === 'row'
  const enough = row ? (s.mode === 'equal_gaps' ? 3 : 2) : 1
  const onFace = !!target || plate
  const valid = row ? !!s.row_gap.trim() && (editingRow || names.length >= enough)
    : names.length >= 1 && onFace && !!s.gap.trim() && !!s.margin.trim()
  const inputs = (): ArrangeEdit['inputs'] => row ? { mode: s.mode, axis: s.axis, gap: s.row_gap } : patternInputs(s, move)
  useLivePreview(JSON.stringify([inputs(), names, target, plate, contacts, edges]), valid && !busy && !editingRow && !role, () => onPreview(inputs()))
  const choose = (pattern: Settings['pattern']) => {
    set({ pattern })
    if (pattern !== 'row' && !onFace) onRole('target')
  }
  // A drag of the preview moves the pattern; the preview follows.
  const dragged = useRef(move)
  useEffect(() => {
    if (dragged.current === move) return
    dragged.current = move
    if (!row && valid) onPreview(patternInputs(s, move))
  }, [move])
  // A suggestion changes the settings it names, and the preview follows.
  const suggest = (patch: ArrangementNote['suggestions'][number]['patch']) => {
    const next: Settings = { ...s }
    const record = next as unknown as Record<string, unknown>
    for (const [key, value] of Object.entries(patch)) {
      record[key] = typeof value === 'number' && key !== 'rows' && key !== 'columns' ? String(value) : value
    }
    setSettings(next)
    onPreview(patternInputs(next, move))
  }
  const text = (key: Text, label: string) => <label key={key} className="grid gap-1">{label}
    <input className={inputCls} value={s[key]} maxLength={255} disabled={busy} onChange={(event) => set({ [key]: event.target.value })} />
  </label>
  const check = (key: Switch, label: string) => <label key={key} className="flex items-center gap-1.5">
    <input type="checkbox" checked={s[key]} disabled={busy} onChange={(event) => set({ [key]: event.target.checked })} />{label}
  </label>
  const count = (key: 'rows' | 'columns', label: string) => <label key={key} className="grid gap-1">{label} (0 = Auto)
    <input className={inputCls} type="number" min={0} max={999} value={s[key]} disabled={busy}
      onChange={(event) => set({ [key]: Math.max(0, Math.min(999, Math.round(Number(event.target.value) || 0))) })} />
  </label>
  const pick = (which: ArrangeRole, idle: string, waiting: string) =>
    <Btn type="button" size="sm" className="justify-self-start" aria-pressed={role === which} disabled={busy}
      onClick={() => onRole(role === which ? undefined : which)}>{role === which ? waiting : idle}</Btn>

  return (
    <form aria-label="Arrange" className="grid shrink-0 gap-2 text-xs" onSubmit={(event) => {
      event.preventDefault()
      if (!valid || busy) return
      if (editingRow) {
        const saved = recorded(preset!)
        const changes = Object.fromEntries(([['mode', s.mode], ['axis', s.axis], ['gap', s.row_gap]] as const)
          .filter(([key, value]) => (key === 'gap' ? saved.row_gap : saved[key]) !== value))
        if (Object.keys(changes).length) onEdit(changes)
      } else onApply(inputs())
    }}>
      {(!preset || !row) && <p role="status" aria-label="Arranged bodies">
        {names.length >= enough ? names.join(', ') : `Choose ${enough} or more bodies in the Bodies list (Ctrl-click).`}
      </p>}
      {!editingRow && <label className="grid gap-1">Pattern
        <select className={inputCls} value={s.pattern} disabled={busy} onChange={(event) => choose(event.target.value as Settings['pattern'])}>
          {PATTERNS.filter(([value]) => !preset || value !== 'row').map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </label>}
      {row ? <>
        <div className="grid gap-1">Spacing
          <Segmented label="Spacing" value={s.mode} onChange={(mode) => set({ mode })}
            options={[{ value: 'equal_gaps', label: 'Equal gaps' }, { value: 'fixed_gap', label: 'Fixed gap' }]} />
        </div>
        <div className="grid gap-1">Along
          <Segmented label="Along" value={s.axis} onChange={(axis) => set({ axis })}
            options={(['x', 'y', 'z'] as const).map((value) => ({ value, label: value.toUpperCase() }))} />
        </div>
        {s.mode === 'fixed_gap' && <label className="grid gap-1">Gap
          <input className={inputCls} value={s.row_gap} maxLength={255} disabled={busy} onChange={(event) => set({ row_gap: event.target.value })} />
        </label>}
      </> : <>
        <div role="status" aria-label="Arrange target" className="text-text-2">
          {plate ? 'On the build plate.' : target ? `On ${target}.` : 'Click the face to arrange the objects on, or use the build plate.'}
        </div>
        <div className="flex flex-wrap gap-1">
          {pick('target', target ? 'Pick another face' : 'Pick the target face', 'Click a face…')}
          <Btn type="button" size="sm" aria-pressed={plate} disabled={busy} title="No face to arrange on? Lay the objects out on the printer's build plate."
            onClick={() => onPlate(!plate)}>Use the build plate</Btn>
        </div>
        <More>
        <div className="font-semibold">Spacing</div>
        <Segmented label="Pattern spacing" value={s.spacing} onChange={(spacing) => set({ spacing })}
          options={[{ value: 'auto', label: 'Automatic' }, { value: 'fixed', label: 'Fixed' }]} />
        {text('gap', s.spacing === 'auto' ? 'Minimum spacing' : 'Spacing')}
        {s.pattern === 'custom' && text('gap_y', 'Row spacing')}
        <div className="font-semibold">Boundary</div>
        <Segmented label="Boundary" value={s.boundary} onChange={(boundary) => set({ boundary })}
          options={[{ value: 'auto', label: 'Automatic' }, { value: 'manual', label: 'Manual' }]} />
        {text('margin', s.boundary === 'manual' ? 'Margin from picked edges' : 'Edge margin')}
        {s.boundary === 'manual' && <>
          {text('inset_left', 'Left inset')}
          {text('inset_right', 'Right inset')}
          {text('inset_top', 'Top inset')}
          {text('inset_bottom', 'Bottom inset')}
          {!plate && pick('edges', `Pick edges to keep the margin from (${edges})`, 'Click edges of the target face…')}
        </>}
        {check('contain', 'Keep objects entirely inside')}
        {check('avoid_holes', 'Avoid holes and cutouts')}
        {check('avoid_bodies', 'Avoid other objects on the face')}
        <div className="font-semibold">Layout</div>
        {s.pattern !== 'radial' && text('direction', s.pattern === 'linear' ? 'Row direction' : 'Pattern turn')}
        {['grid', 'staggered', 'custom'].includes(s.pattern) && <>{count('rows', 'Rows')}{count('columns', 'Columns')}</>}
        {s.pattern === 'custom' && <>
          <select aria-label="Fill" className={inputCls} value={s.fill} disabled={busy} onChange={(event) => set({ fill: event.target.value as Settings['fill'] })}>
            <option value="rows">Fill rows first</option>
            <option value="columns">Fill columns first</option>
          </select>
          <select aria-label="Distribute" className={inputCls} value={s.distribute} disabled={busy}
            onChange={(event) => set({ distribute: event.target.value as Settings['distribute'] })}>
            <option value="between">Spread to the margins</option>
            <option value="evenly">Even gaps, edges included</option>
          </select>
        </>}
        {s.pattern === 'radial' && <>
          {text('radius', 'Radius (0 auto)')}
          {text('start_angle', 'Start angle')}
          {check('face_center', 'Turn each to face the centre')}
        </>}
        {s.pattern !== 'radial' && <div className="grid gap-1">Alignment within the boundary
          <div role="radiogroup" aria-label="Alignment" className="grid w-24 grid-cols-3 gap-0.5">
            {ANCHORS.map(([value, arrow]) => <button key={value} type="button" role="radio" aria-checked={s.align === value}
              aria-label={value.replace('_', ' ')} disabled={busy} onClick={() => set({ align: value })}
              className={`h-7 rounded-md ${s.align === value ? 'bg-panel text-text shadow-[0_1px_2px_rgb(0_0_0/0.1)]' : 'bg-hover text-text-2'}`}>{arrow}</button>)}
          </div>
        </div>}
        <div className="font-semibold">Orientation</div>
        {check('orient', 'Stand objects on a flat face')}
        {pick('contact', `Pick contact faces (${contacts} chosen)`, 'Click a face on an object…')}
        {text('rotation', 'Rotate each')}
        {check('auto_rotate', 'Turn objects to fit better')}
        {text('normal_offset', 'Stand-off')}
        {move.some((amount) => Math.abs(amount) > 1e-6) && <div role="status" aria-label="Pattern moved" className="text-text-2">
          Moved {move.map((amount) => +amount.toFixed(2)).join(', ')} mm along U, V and off the face.</div>}
        <Btn type="button" size="sm" className="justify-self-start" disabled={busy} title="Back to the default settings, and undo any drag."
          onClick={() => { setSettings({ ...DEFAULTS, pattern: s.pattern }); onReset() }}>Reset</Btn>
        </More>
        {note && <div role="status" aria-label="Arrangement fit" className={note.fits ? 'text-text-2' : 'text-danger'}>{note.summary}</div>}
        {note && !note.fits && note.problems.map((problem) => <p key={problem.name} className="text-danger">{problem.name} {problem.reason}.</p>)}
        {note?.suggestions.map((offer) => <Btn key={offer.label} type="button" size="sm" className="justify-self-start" disabled={busy}
          onClick={() => suggest(offer.patch)}>Try: {offer.label}</Btn>)}
      </>}
      <div className="flex gap-1">
        <Btn type="submit" size="sm" variant="primary" disabled={!valid || busy}>Apply</Btn>
        <Btn type="button" size="sm" disabled={busy} onClick={onCancel}>Cancel</Btn>
      </div>
    </form>
  )
}
