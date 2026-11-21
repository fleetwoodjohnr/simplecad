import { useEffect, useMemo, useState } from 'react'
import { basePlanes, type SketchConstraint, type SketchEntity, type SketchPlane, type SketchRecipe, type SketchSolution } from '@/shared/sketch'
import { solveSketch } from '../state/engine'
import { Btn, Modal, inputCls } from '../ui'
import { SketchCanvas, type DrawTool } from './SketchCanvas'

type ReferenceType = 'Point' | 'Line' | 'Round curve' | 'Entity'
const rules: Record<string, { label: string; refs: ReferenceType[]; dimension?: string }> = {
  horizontal: { label: 'Horizontal', refs: ['Line'] },
  vertical: { label: 'Vertical', refs: ['Line'] },
  distance: { label: 'Distance', refs: ['Point', 'Point'], dimension: '10 mm' },
  radius: { label: 'Radius', refs: ['Round curve'], dimension: '5 mm' },
  diameter: { label: 'Diameter', refs: ['Round curve'], dimension: '10 mm' },
  angle: { label: 'Angle', refs: ['Line', 'Line'], dimension: '90 deg' },
  coincident: { label: 'Coincident', refs: ['Point', 'Point'] },
  parallel: { label: 'Parallel', refs: ['Line', 'Line'] },
  perpendicular: { label: 'Perpendicular', refs: ['Line', 'Line'] },
  equal: { label: 'Equal length or radius', refs: ['Entity', 'Entity'] },
  concentric: { label: 'Concentric', refs: ['Round curve', 'Round curve'] },
  point_on_line: { label: 'Point on line', refs: ['Point', 'Line'] },
  midpoint: { label: 'Midpoint', refs: ['Point', 'Line'] },
  tangent: { label: 'Tangent', refs: ['Line', 'Round curve'] },
  symmetric: { label: 'Symmetric', refs: ['Point', 'Point', 'Line'] },
  fix: { label: 'Fix point', refs: ['Point'] },
}
const tools: Record<DrawTool, string> = {
  select: 'Select', line: 'Line', rectangle: 'Rectangle', circle: 'Circle', arc: 'Arc',
  ellipse: 'Ellipse', spline: 'Spline'
}
const hints: Record<DrawTool, string> = {
  select: 'Drag a free point or select geometry from the lists.',
  line: 'Pick the start and end points.',
  rectangle: 'Pick opposite corners.',
  circle: 'Pick the centre and a point on the circle.',
  arc: 'Pick the centre, start, and end in counterclockwise order.',
  ellipse: 'Pick the centre, then one point on each axis.',
  spline: 'Pick at least two control points, then finish the spline.'
}
const format = (value: number) => value.toFixed(4).replace(/\.?0+$/, '')
const identifier = () => crypto.randomUUID().replaceAll('-', '')

interface Props {
  initial: SketchRecipe
  revision: number
  busy: boolean
  facePlane?: SketchPlane
  constructionPlanes: { id: string; name: string; plane: SketchPlane }[]
  error: string
  onApply: (recipe: SketchRecipe) => void
  onClose: () => void
}

export function SketchEditor({ initial, revision, busy, facePlane, constructionPlanes, error, onApply, onClose }: Props) {
  const [draft, setDraft] = useState<SketchRecipe>(() => ({
    ...initial, plane: initial.plane ?? basePlanes.XY,
    points: initial.points ?? [], entities: initial.entities ?? [], constraints: initial.constraints ?? []
  }))
  const [tool, setTool] = useState<DrawTool>('select')
  const [construction, setConstruction] = useState(false)
  const [fit, setFit] = useState(0)
  const [finish, setFinish] = useState(0)
  const [pointId, setPoint] = useState('')
  const [entityId, setEntity] = useState('')
  const [plane, setPlane] = useState('Original')
  const [offset, setOffset] = useState('0')
  const [kind, setKind] = useState('horizontal')
  const [references, setReferences] = useState<string[]>([])
  const [dimension, setDimension] = useState('')
  const [answer, setAnswer] = useState<{ tested: string; result?: SketchSolution; error?: string }>()
  const tested = useMemo(() => JSON.stringify(draft), [draft])
  const current = answer?.tested === tested ? answer : undefined
  const shown = useMemo(() => current?.result?.solved ? JSON.parse(current.result.recipe) as SketchRecipe : draft, [current, draft])
  const point = shown.points.find((p) => p.id === pointId)
  const entity = draft.entities.find((item) => item.id === entityId)
  const rule = rules[kind]

  useEffect(() => {
    let active = true
    const timer = window.setTimeout(() => {
      void solveSketch(revision, tested).then(
        (result) => { if (active) setAnswer({ tested, result }) },
        (error: Error) => { if (active) setAnswer({ tested, error: error.message }) }
      )
    }, 100)
    return () => { active = false; window.clearTimeout(timer) }
  }, [revision, tested])

  function choosePlane(selected: string, distance: string) {
    const base = selected === 'Original' ? initial.plane : selected === 'Face' ? facePlane :
      basePlanes[selected] ?? constructionPlanes.find((plane) => plane.id === selected)?.plane
    const displacement = Number(distance)
    if (!base || !Number.isFinite(displacement)) return
    setDraft((previous) => ({ ...previous, plane: { ...base,
      origin: base.origin.map((value, i) => value + base.normal[i] * displacement) as SketchPlane['origin'] } }))
  }

  function removeConstraint(id: string) {
    const removed = draft.constraints.find((item) => item.id === id)
    const constraints = draft.constraints.filter((item) => item.id !== id)
    const points = removed?.kind === 'fix' && !constraints.some((item) => item.kind === 'fix' && item.refs[0] === removed.refs[0])
      ? draft.points.map((p) => p.id === removed.refs[0] ? { ...p, fixed: false } : p) : draft.points
    setDraft({ ...draft, constraints, points })
  }
  function addConstraint() {
    const refs = rule.refs.map((_, i) => references[i])
    if (refs.some((id) => !id) || new Set(refs).size !== refs.length) return
    const entry: SketchConstraint = { id: identifier(), kind, refs }
    if (rule.dimension) entry.expression = dimension.trim() || rule.dimension
    const points = kind === 'fix' ? shown.points.map((p) => p.id === refs[0] ? { ...p, fixed: true } : p) : shown.points
    setDraft({ ...shown, points, constraints: [...draft.constraints, entry] })
    setReferences([])
  }
  function removeEntity() {
    if (!entity) return
    setDraft({ ...draft, entities: draft.entities.filter((item) => item.id !== entity.id),
      constraints: draft.constraints.filter((item) => !item.refs.includes(entity.id)) })
    setEntity('')
  }
  const entityName = (item: SketchEntity, index: number) => tools[item.kind] + ' ' + (index + 1)
  function options(type: ReferenceType) {
    if (type === 'Point') return draft.points.map((p, i) => ({ id: p.id, name: 'Point ' + (i + 1) }))
    return draft.entities.flatMap((item, i) => type === 'Entity' ||
      (type === 'Line' && item.kind === 'line') ||
      (type === 'Round curve' && (item.kind === 'circle' || item.kind === 'arc'))
      ? [{ id: item.id, name: entityName(item, i) }] : [])
  }

  return <Modal title={draft.name ? `Edit ${draft.name}` : 'Sketch editor'} width={1040} open
    onOpenChange={(open) => { if (!open && !busy) onClose() }}
    footer={<>
      {error && <span className="mr-auto text-xs text-danger" role="alert">{error}</span>}
      <Btn type="button" disabled={busy} onClick={onClose}>Cancel</Btn>
      <Btn type="button" variant="primary" disabled={busy || !current?.result?.solved || !draft.entities.length}
        onClick={() => onApply(shown)}>Save sketch</Btn>
    </>}>
      <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_15rem]">
        <div className="grid content-start gap-2">
          <div role="toolbar" aria-label="Drawing tools" className="flex flex-wrap gap-1">
            {(Object.keys(tools) as DrawTool[]).map((name) =>
              <Btn key={name} type="button" size="sm" aria-pressed={tool === name} variant={tool === name ? 'primary' : 'secondary'}
                onClick={() => setTool(name)}>{tools[name]}</Btn>)}
            {tool === 'spline' && <Btn size="sm" onClick={() => setFinish((n) => n + 1)}>Finish spline</Btn>}
            <Btn size="sm" onClick={() => setFit((n) => n + 1)}>Fit sketch</Btn>
          </div>
          <p className="text-xs text-text-2">{hints[tool]}</p>
          <SketchCanvas key={tool} recipe={shown} tool={tool} construction={construction} activePoint={pointId}
            activeEntity={entityId} fit={fit} finish={finish} onChange={setDraft} onPoint={setPoint} onEntity={setEntity} />
          <output aria-label="Constraint status" aria-live="polite" className="min-h-6 text-sm">
            {current?.error ?? current?.result?.message ?? 'Solving…'}
          </output>
        </div>
        <div className="grid content-start gap-2 text-xs">
          <label className="grid gap-1">Sketch plane
            <select aria-label="Sketch plane" className={inputCls} value={plane} onChange={(event) => {
              setPlane(event.target.value)
              choosePlane(event.target.value, offset)
            }}>
              <option value="Original">Original</option>
              {Object.keys(basePlanes).map((name) => <option key={name}>{name}</option>)}
              {facePlane && <option value="Face">Selected face</option>}
              {constructionPlanes.map(({ id, name }) => <option key={id} value={id}>{name}</option>)}
            </select>
          </label>
          <label className="grid gap-1">Plane offset (mm)
            <input className={inputCls} type="number" value={offset} onChange={(event) => {
              setOffset(event.target.value)
              choosePlane(plane, event.target.value)
            }} />
          </label>
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={construction} onChange={(event) => setConstruction(event.target.checked)} />
            Draw construction geometry
          </label>
          <label className="grid gap-1">Point
            <select aria-label="Point" className={inputCls} value={pointId} onChange={(event) => setPoint(event.target.value)}>
              <option value="">Select a point</option>
              {options('Point').map(({ id, name }) => <option key={id} value={id}>{name}</option>)}
            </select>
          </label>
          {point && <div className="grid grid-cols-2 gap-2">
            {(['x', 'y'] as const).map((axis) => <label key={axis} className="grid gap-1"> Point {axis.toUpperCase()} (mm)
              <input className={inputCls} type="number" step="any" defaultValue={format(point[axis])} key={point.id + axis + point[axis]}
                onBlur={(event) => {
                  const value = event.target.valueAsNumber
                  if (Number.isFinite(value) && value !== point[axis]) setDraft({ ...shown,
                    points: shown.points.map((item) => item.id === pointId ? { ...item, [axis]: value } : item) })
                }} />
            </label>)}
          </div>}
          <label className="grid gap-1">Entity
            <select aria-label="Entity" className={inputCls} value={entityId} onChange={(event) => setEntity(event.target.value)}>
              <option value="">Select an entity</option>
              {options('Entity').map(({ id, name }) => <option key={id} value={id}>{name}</option>)}
            </select>
          </label>
          {entity && <div className="flex items-center justify-between gap-2">
            <label className="flex items-center gap-1.5">
              <input type="checkbox" checked={!!entity.construction} onChange={(event) => setDraft({ ...draft,
                entities: draft.entities.map((item) => item.id === entity.id ? { ...item, construction: event.target.checked } : item) })} />
              Construction
            </label>
            <Btn type="button" size="sm" onClick={removeEntity}>Delete</Btn>
          </div>}
          <label className="grid gap-1">Constraint
            <select aria-label="Constraint" className={inputCls} value={kind} onChange={(event) => {
              setKind(event.target.value); setReferences([]); setDimension('')
            }}>
              {Object.entries(rules).map(([value, entry]) => <option key={value} value={value}>{entry.label}</option>)}
            </select>
          </label>
          {rule.refs.map((ref, index) => <label key={index} className="grid gap-1">{ref} {index + 1}
            <select aria-label={ref + ' ' + (index + 1)} className={inputCls} value={references[index] ?? ''} onChange={(event) => {
              const next = [...references]; next[index] = event.target.value; setReferences(next)
            }}>
              <option value="">Choose…</option>
              {options(ref).map(({ id, name }) => <option key={id} value={id}>{name}</option>)}
            </select>
          </label>)}
          {rule.dimension && <label className="grid gap-1">Dimension
            <input className={inputCls} value={dimension} placeholder={rule.dimension} onChange={(event) => setDimension(event.target.value)} />
          </label>}
          <Btn type="button" size="sm" className="justify-self-start" onClick={addConstraint}
            disabled={rule.refs.some((_, i) => !references[i])}>Add constraint</Btn>
        </div>
      </div>
      {draft.constraints.length > 0 && <ul aria-label="Sketch constraints" className="grid max-h-44 gap-1 overflow-auto text-xs">
        {draft.constraints.map((item, i) => <li key={item.id} className="flex items-center gap-2 border-t border-border py-1">
          <span className="min-w-28">{rules[item.kind]?.label ?? item.kind}</span>
          {rules[item.kind]?.dimension && <input className={inputCls} aria-label={`Dimension ${i + 1}`}
            value={item.expression ?? String(item.value ?? '')} onChange={(event) => setDraft({ ...draft,
              constraints: draft.constraints.map((constraint) => constraint.id === item.id ? { ...constraint, expression: event.target.value } : constraint) })} />}
          <Btn className="ml-auto" size="sm" aria-label={`Remove constraint ${i + 1}`} onClick={() => removeConstraint(item.id)}>Remove</Btn>
        </li>)}
      </ul>}
  </Modal>
}
