import { useEffect, useMemo, useRef, useState } from 'react'
import { Box, Ellipsis, Eye, EyeOff, Focus, Folder, Pencil, Redo2, Search, Undo2, Ungroup } from 'lucide-react'
import type { BodyInfo, DisplayShape, ExportFormat, FaceInfo, FaceOperation, MeasurePick, Measurement, ModelResult, StackEdit, StackSettings, Transform, Vec3, ViewFrame } from '@/shared/engine-protocol'
import { displayLength, type DisplayUnit, type Theme } from '@/shared/preferences'
import { basePlanes, type SketchPlane, type SketchRecipe, type SketchSolidEdit } from '@/shared/sketch'
import type { OfferedProject } from '@/shared/ipc-types'
import { applySectionReplace, previewSectionReplace, applyArrange, previewArrange, applyConstruction, groupBodies, ungroup, applyBodyOperation, previewBodyOperation, applyEdgeFinish, previewEdgeFinish, applySketchDraft, applySketchSolid, previewSketchSolid, previewShape, readSketch, applyFace, applyPull, applyStack, applyTransform, cancelQueuedStackPreview, createShape, editSavedStep, exportModel, importModel, markSaved, measure, onEngineConnected, openProject, openSession, previewFace, previewPull, previewSavedStep, previewStack, previewTransform, repairInput, resetModel, saveModel, showBody, stepHistory } from '../state/engine'
import { Btn, IconBtn, Menu, MenuContent, MenuItem, MenuTrigger, TooltipProvider, inputCls } from '../ui'
import { Ribbon } from './Ribbon'
import { ContextBar } from './ContextBar'
import { BodyOperationPanel, bodyOperationLabels, type BodyOperation } from './BodyOperationPanel'
import { EdgeFinishPanel, type EdgeFinish } from './EdgeFinishPanel'
import { ArrangePanel } from './ArrangePanel'
import { SectionReplacePanel } from './SectionReplacePanel'
import { StepSettingsPanel } from './StepSettingsPanel'
import { ConstructionPanel, constructionLabels, needs, type AxisFrom, type Construction } from './ConstructionPanel'
import { Viewport } from '../viewport/Viewport'
import { TitleBar } from './TitleBar'
import { CommandSearch } from './CommandSearch'
import { ShapePanel } from './ShapePanel'
import { SketchEditor } from './SketchEditor'
import { profileOperations, SketchSolidPanel } from './SketchSolidPanel'
import { defaultHoleSettings, defaultThreadSettings, HolePanel, ThreadPanel } from './HolePanel'
import { MeasurePanel } from './MeasurePanel'
import { MatchingPartPanel } from './MatchingPartPanel'
import { PreferencesDialog } from './PreferencesDialog'
import { usePreferences } from './preferences'
import { TransformPanel } from './TransformPanel'
import { defaultTextSettings, TextPanel } from './TextPanel'
import { defaultOffsetPlane, defaultResize, defaultShell, defaultVent, OffsetPlanePanel, ResizePanel, ShellPanel, VentPanel } from './OffsetPlanePanel'
import { defaultTextureSettings, TexturePanel } from './TexturePanel'
import { commands, matchesShortcut, type CommandActions, type CommandId } from './commands'

function useTheme(chosen: Theme) {
  const [query] = useState(() => window.matchMedia('(prefers-color-scheme: dark)'))
  const [system, setSystem] = useState(query.matches)
  useEffect(() => {
    const update = () => setSystem(query.matches)
    query.addEventListener('change', update)
    return () => query.removeEventListener('change', update)
  }, [query])
  const dark = chosen === 'system' ? system : chosen === 'dark'
  useEffect(() => { document.documentElement.classList.toggle('dark', dark) }, [dark])
  return dark
}

const labels = [['Left', 'Right'], ['Front', 'Back'], ['Bottom', 'Top']]
const format = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2, useGrouping: false })
const STATES: Record<string, string> = { suppressed: 'Suppressed', failed: 'Failed', needs_attention: 'Needs attention' }
const defaultStack: StackSettings = {
  operation: 'stack', offset: 0, rotation: 0, flip: false, horizontal: 'center', vertical: 'center',
  horizontalGap: 0, verticalGap: 0
}

function sketchPlane(face: FaceInfo | undefined, point?: Vec3): SketchPlane | undefined {
  if (!face?.planar) return
  const normal = face.normal
  const candidate: Vec3 = Math.abs(normal[0]) < 0.9 ? [1, 0, 0] : [0, 1, 0]
  const alignment = candidate.reduce((sum, component, i) => sum + component * normal[i], 0)
  const projected = candidate.map((component, i) => component - alignment * normal[i])
  const length = Math.hypot(...projected)
  return { origin: point ?? face.center, normal, x_axis: projected.map((component) => component / length) as Vec3 }
}

type ProfileTool = SketchSolidEdit['sketchSolid']['kind']

function faceLabel(face: FaceInfo, bodies: BodyInfo[], unit: DisplayUnit) {
  const axis = face.normal.findIndex((n) => Math.abs(n) > 0.9999)
  const side = !face.planar ? 'Curved' : axis < 0 ? 'Slanted' : labels[axis][Number(face.normal[axis] > 0)]
  const several = bodies.filter((b) => b.visible && !b.empty).length > 1
  return `${several ? `${bodies[face.body].name} · ` : ''}${side} · ${format.format(displayLength(face.area, unit, 2))} ${unit}²`
}

function pickKey(pick: MeasurePick) {
  return pick.kind === 'body' ? `body:${pick.body}` : pick.kind === 'face' ? `face:${pick.face}` :
    pick.kind === 'edge' ? `edge:${pick.edge}` : `vertex:${pick.body}:${pick.point.map((v) => v.toFixed(4))}`
}

function StackOptions({ settings, onChange }: { settings: StackSettings; onChange: (next: StackSettings) => void }) {
  const number = (key: 'offset' | 'rotation' | 'horizontalGap' | 'verticalGap', label: string) => (
    <label className="grid min-w-0 gap-1">{label}
      <input type="number" step="any" className={`${inputCls} w-full`} value={settings[key]}
        onChange={(e) => onChange({ ...settings, [key]: Number(e.target.value) })} />
    </label>
  )
  return <>
    <label className="grid gap-1">Placement
      <select className={inputCls} value={settings.operation}
        onChange={(e) => onChange({ ...settings, operation: e.target.value as StackSettings['operation'] })}>
        <option value="stack">Stack</option><option value="center">Center</option><option value="concentric">Concentric</option>
      </select>
    </label>
    {settings.operation !== 'concentric' && <div className="grid grid-cols-2 gap-2">
      <label className="grid gap-1">Horizontal
        <select className={inputCls} value={settings.horizontal}
          onChange={(e) => onChange({ ...settings, horizontal: e.target.value as StackSettings['horizontal'] })}>
          <option value="left">Left</option><option value="center">Center</option><option value="right">Right</option>
        </select>
      </label>
      <label className="grid gap-1">Vertical
        <select className={inputCls} value={settings.vertical}
          onChange={(e) => onChange({ ...settings, vertical: e.target.value as StackSettings['vertical'] })}>
          <option value="bottom">Bottom</option><option value="center">Center</option><option value="top">Top</option>
        </select>
      </label>
      {number('horizontalGap', 'Horizontal gap')}
      {number('verticalGap', 'Vertical gap')}
    </div>}
    <div className="grid grid-cols-2 gap-2">{number('offset', 'Offset (mm)')}{number('rotation', 'Rotation (°)')}</div>
    <label className="flex items-center gap-2"><input type="checkbox" checked={settings.flip}
      onChange={(e) => onChange({ ...settings, flip: e.target.checked })} />Flip</label>
  </>
}

export function App() {
  const { preferences, apply: applyPreferences, loaded: preferencesLoaded } = usePreferences()
  const unit = preferences.displayUnit
  const dark = useTheme(preferences.theme)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const toggle = () => applyPreferences({ ...preferences, theme: dark ? 'light' : 'dark' })
  const [model, setModel] = useState<ModelResult>()
  const [fileName, setFileName] = useState('Untitled')
  const [preview, setPreview] = useState<DisplayShape>()
  const [selected, setSelected] = useState<number>()
  const [holePosition, setHolePosition] = useState<Vec3>()
  const [stack, setStack] = useState(false)
  const [measureKind, setMeasureKind] = useState<MeasurePick['kind']>()
  const [measurePicks, setMeasurePicks] = useState<MeasurePick[]>([])
  const [measurements, setMeasurements] = useState<Measurement[]>([])
  const [measuring, setMeasuring] = useState(false)
  const [sketchEditor, setSketchEditor] = useState<{ id: string; creating: boolean; recipe: SketchRecipe; facePlane?: SketchPlane }>()
  const [sketchTool, setSketchTool] = useState<ProfileTool>()
  const [matchingPart, setMatchingPart] = useState(false)
  const [shapePanel, setShapePanel] = useState(false)
  const [editingShape, setEditingShape] = useState<string>()
  const [transformKind, setTransformKind] = useState<Transform['kind']>()
  const [bodyTool, setBodyTool] = useState<{ kind: BodyOperation; editing?: string }>()
  const [edgeTool, setEdgeTool] = useState<{ kind: EdgeFinish; editing?: string }>()
  const [edgePicks, setEdgePicks] = useState<number[]>([])
  /** Arrange's bodies, taken as it opens; or the saved step History is editing. */
  const [arrange, setArrange] = useState<{ bodies: number[]; editing?: string }>()
  /** Section Replace: its picks (the replacement's face, then the target's), or the saved step History is editing. */
  const [section, setSection] = useState<{ editing?: string }>()
  /** A saved Move, Rotate, Pull or placement whose settings History is editing. */
  const [editingMove, setEditingMove] = useState<string>()
  const [sectionPicks, setSectionPicks] = useState<number[]>([])
  const [construction, setConstruction] = useState<{ kind: Construction; editing?: string; axisFrom: AxisFrom }>()
  const [constructPicks, setConstructPicks] = useState<{ faces: number[]; edges: number[]; points: Vec3[] }>({ faces: [], edges: [], points: [] })
  /** Bodies chosen in the Bodies list (Ctrl- or Shift-click adds), e.g. to group them. */
  const [chosenBodies, setChosenBodies] = useState<number[]>([])
  const [transformBody, setTransformBody] = useState<number>()
  const [transformSnap, setTransformSnap] = useState(0)
  const [transformPivot, setTransformPivot] = useState<Vec3>()
  const [faceTool, setFaceTool] = useState<FaceOperation['faceOperation']>()
  const [editingFace, setEditingFace] = useState<string>()
  const [repairing, setRepairing] = useState<string>()
  const [repairBody, setRepairBody] = useState<number>()
  /** Replacements for a Combine's other lost tools, by the lost object's ID. */
  const [moreRepair, setMoreRepair] = useState<Record<string, number>>({})
  const [target, setTarget] = useState<number>()
  const [targetView, setTargetView] = useState<ViewFrame>()
  const readViewFrame = useRef<() => ViewFrame>(undefined)
  const [stackSettings, setStackSettings] = useState(defaultStack)
  const [stackPrepared, setStackPrepared] = useState<{ edit: StackEdit; revision: number }>()
  const [distance, setDistance] = useState('')
  const [showEdges, setShowEdges] = useState(true)
  const [frame, setFrame] = useState(0)
  const [connections, setConnections] = useState(0)
  const [error, setError] = useState('')
  const [viewportError, setViewportError] = useState('')
  const [busy, setBusy] = useState(false)
  const [searchOpen, setSearchOpen] = useState(false)
  const [projectRequested, setProjectRequested] = useState(true)
  /** How long ago a crashed session left unsaved work, while that is on offer. */
  const [recovery, setRecovery] = useState<string>()
  /** Set once the close is allowed, so the unsaved-work guard lets it through. */
  const closing = useRef(false)
  const busyRef = useRef(false)
  const historyList = useRef<HTMLOListElement>(null)
  const distanceField = useRef<HTMLInputElement>(null)
  const connection = useRef(0)
  const repairStep = model?.steps.find((step) => step.id === repairing && !step.undone)
  const missingInput = repairStep?.missingInput
  const moreMissing = repairStep?.moreMissing ?? []
  const faceStep = model?.steps.find((step) => step.id === editingFace && !step.undone)
  const shapeStep = model?.steps.find((step) => step.id === editingShape && !step.undone)
  const savedShape = shapeStep?.shapeSettings ? { kind: shapeStep.kind, name: shapeStep.name, values: shapeStep.shapeSettings } : undefined

  const updateBusy = (active: boolean) => {
    busyRef.current = active
    setBusy(active)
  }
  const closeMeasure = () => {
    setMeasureKind(undefined)
    setMeasurePicks([])
    setMeasurements([])
    setMeasuring(false)
  }
  const pickMeasure = (pick: MeasurePick) => {
    const key = pickKey(pick)
    setMeasurePicks((items) => items.some((entry) => pickKey(entry) === key)
      ? items.filter((entry) => pickKey(entry) !== key) : [...items, pick])
    setError('')
  }
  const select = (face?: number, position?: Vec3) => {
    setSelected(face)
    setHolePosition(position)
    setPreview(undefined)
    setDistance('')
    setError('')
    if (face === undefined) {
      setChosenBodies([])
      cancelPlacement()
      closeMeasure()
      setTransformKind(undefined)
      setBodyTool(undefined)
      setEdgeTool(undefined)
      setConstruction(undefined)
      setArrange(undefined)
      setSection(undefined)
      setEditingMove(undefined)
      setShapePanel(false)
      setSketchEditor(undefined)
      setSketchTool(undefined)
      setMatchingPart(false)
      setEditingShape(undefined)
      setFaceTool(undefined)
      setEditingFace(undefined)
      setRepairing(undefined)
    }
  }
  const cancelPlacement = () => {
    setStack(false)
    setTarget(undefined)
    setTargetView(undefined)
    setStackPrepared(undefined)
    setPreview(undefined)
    cancelQueuedStackPreview()
  }
  const pickTarget = (face?: number) => {
    setPreview(undefined)
    setTarget(face)
    setTargetView(face === undefined ? undefined : readViewFrame.current?.())
  }

  useEffect(() => onEngineConnected(() => {
    const generation = ++connection.current
    setConnections(generation)
    updateBusy(true)
    setPreview(undefined)
    setSelected(undefined)
    setHolePosition(undefined)
    setDistance('')
    cancelPlacement()
    closeMeasure()
    setTransformKind(undefined)
    setBodyTool(undefined)
    setEdgeTool(undefined)
    setConstruction(undefined)
    setArrange(undefined)
    setSection(undefined)
    setShapePanel(false)
    setEditingShape(undefined)
    setFaceTool(undefined)
    setEditingFace(undefined)
    setRepairing(undefined)
    setError('')
    void openSession(generation > 1).then(
      (next) => {
        if (connection.current !== generation) return
        setModel(next)
        updateBusy(false)
      },
      (failure: Error) => {
        if (connection.current !== generation) return
        setError(failure.message)
        updateBusy(false)
      }
    )
  }), [])

  const value = distance === '' ? NaN : Number(distance)
  const revision = model?.revision
  const placement = useMemo<StackEdit | undefined>(() =>
    stack && selected !== undefined && target !== undefined
      ? { moving: selected, target, settings: { ...stackSettings, view: targetView } }
      : undefined,
  [stack, selected, target, stackSettings, targetView])
  useEffect(() => {
    if (stack || shapePanel || faceTool || transformKind || bodyTool || edgeTool || construction || arrange || section || measureKind || sketchTool || matchingPart) return
    setPreview(undefined)
    if (busy || selected === undefined || revision === undefined || !Number.isFinite(value) || value === 0) return
    let active = true
    const timer = window.setTimeout(() => {
      // A round face is pushed along its radius; a flat one along its normal.
      const round = !model?.shape.faces[selected]?.planar
      void (round
        ? previewFace(revision, { face: selected, faceOperation: 'resize_round', settings: { delta: String(value) } })
        : previewPull(revision, { face: selected, distance: value })).then(
        (next) => { if (active && !busyRef.current) setPreview(next) },
        () => { if (active) setPreview(undefined) }
      )
    }, 48)
    return () => { active = false; window.clearTimeout(timer) }
  }, [busy, selected, revision, value, stack, shapePanel, faceTool, transformKind, bodyTool, edgeTool, construction, arrange, section, measureKind, sketchTool, matchingPart])

  useEffect(() => {
    if (!stack) return
    setStackPrepared(undefined)
    if (!placement || busy || revision === undefined) return
    setError('')
    let active = true
    const timer = window.setTimeout(() => {
      void previewStack(revision, placement).then((next) => {
        if (!active || !next) return
        setPreview(next)
        setStackPrepared({ edit: placement, revision })
      }, (failure: Error) => {
        if (!active) return
        setPreview(undefined)
        setError(failure.message)
      })
    }, 48)
    return () => { active = false; window.clearTimeout(timer); cancelQueuedStackPreview() }
  }, [stack, placement, busy, revision])

  useEffect(() => {
    if (!measureKind || revision === undefined || !measurePicks.length) {
      setMeasurements([])
      setMeasuring(false)
      return
    }
    let active = true
    setMeasuring(true)
    void measure(revision, measurePicks).then((values) => {
      if (active) setMeasurements(values)
    }, (failure: Error) => {
      if (active) {
        setMeasurements([])
        setError(failure.message)
      }
    }).finally(() => { if (active) setMeasuring(false) })
    return () => { active = false }
  }, [measureKind, measurePicks, revision])

  const mutate = (run: () => Promise<ModelResult | undefined>, reframe = false) => {
    if (busyRef.current) return
    const generation = connection.current
    updateBusy(true)
    setError('')
    void run().then(
      (next) => {
        if (!next || connection.current !== generation) return
        setModel(next)
        select()
        if (reframe) setFrame((n) => n + 1)
      },
      (failure: Error) => { if (connection.current === generation) setError(failure.message) }
    ).finally(() => { if (connection.current === generation) updateBusy(false) })
  }

  const savedFaceRequest = (operation: FaceOperation) => {
    if (!faceStep?.faceSettings) return
    const defaults = {
      text: defaultTextSettings, face_texture: defaultTextureSettings, hole: defaultHoleSettings, thread: defaultThreadSettings,
      offset_plane: defaultOffsetPlane, shell: defaultShell, resize_round: defaultResize, vent_cut: defaultVent,
      matching_thread: { designation: '', clearance: 'normal', form: 'printed', left_hand: 'false', resize: 'false' }
    }[operation.faceOperation]
    const original: Record<string, string> = { ...defaults, ...faceStep.faceSettings }
    const changes = Object.fromEntries(Object.entries(operation.settings).filter(([key, value]) => original[key] !== value))
    return { editStepId: faceStep.id, changes }
  }

  const clearPreview = () => { setPreview(undefined); setError('') }
  const requestPreview = async (run: () => Promise<DisplayShape>) => {
    if (busyRef.current) return
    const generation = connection.current
    clearPreview()
    updateBusy(true)
    try {
      const next = await run()
      if (connection.current === generation) setPreview(next)
    } catch (failure) {
      if (connection.current === generation) setError((failure as Error).message)
    } finally {
      if (connection.current === generation) updateBusy(false)
    }
  }

  const showFacePreview = (operation: FaceOperation) => {
    if (revision === undefined) return
    const saved = savedFaceRequest(operation)
    if (saved) {
      if (Object.keys(saved.changes).length) void requestPreview(() => previewSavedStep(revision, saved))
    } else if (selected !== undefined) {
      void requestPreview(() => previewFace(revision, { face: selected, ...operation }))
    }
  }

  const commitFace = (operation: FaceOperation) => {
    if (revision === undefined) return
    const saved = savedFaceRequest(operation)
    if (saved) {
      mutate(() => editSavedStep(revision, saved))
    } else if (selected !== undefined) {
      mutate(() => applyFace(revision, { face: selected, ...operation }))
    }
  }

  const cancelFaceTool = () => { setFaceTool(undefined); setEditingFace(undefined); clearPreview() }
  const cancelShapePanel = () => { setShapePanel(false); setEditingShape(undefined); clearPreview() }
  const cancelTransform = () => { setTransformKind(undefined); clearPreview() }
  const closeBodyTool = () => { setBodyTool(undefined); clearPreview() }
  const measuredEdges = useMemo(() => measurePicks.flatMap((pick) => pick.kind === 'edge' ? [pick.edge] : []), [measurePicks])
  const closeEdgeTool = () => { setEdgeTool(undefined); setEdgePicks([]); clearPreview() }
  /** The body a display edge belongs to. */
  const edgeBody = (edge: number) => model?.shape.ranges.find((range) => edge >= range.edges[0] && edge < range.edges[1])?.body
  /** Toggle an edge; an edge of another body starts a new pick, since one finish rounds one body. */
  const pickEdge = (edge: number) => {
    clearPreview()
    setEdgePicks((picks) => picks.includes(edge) ? picks.filter((pick) => pick !== edge)
      : picks.length && edgeBody(picks[0]) !== edgeBody(edge) ? [edge] : [...picks, edge])
  }
  /** In Measure, an edge itself, or the corner at the end of it nearest the click. */
  /** The end of a display edge nearest a picked point: how a corner is picked. */
  const nearestEnd = (edge: number, point: Vec3) => {
    const shape = model!.shape
    const ends = [shape.edgeStart[edge], shape.edgeStart[edge + 1] - 1].map((p) =>
      [0, 1, 2].map((axis) => shape.edgePoints[p * 3 + axis]) as Vec3)
    const distance = (end: Vec3) => end.reduce((sum, value, axis) => sum + (value - point[axis]) ** 2, 0)
    return distance(ends[0]) <= distance(ends[1]) ? ends[0] : ends[1]
  }
  const pickMeasureEdge = (edge: number, point: Vec3) => {
    const body = edgeBody(edge)
    if (!model || body === undefined) return
    if (measureKind === 'edge') return pickMeasure({ kind: 'edge', edge })
    pickMeasure({ kind: 'vertex', body, point: nearestEnd(edge, point) })
  }
  const need = construction && !construction.editing ? needs(construction.kind, construction.axisFrom) : undefined
  /** A construction tool picks its faces first, then its edges or corners. */
  const constructionPhase = need && (constructPicks.faces.length < need.faces ? 'faces'
    : constructPicks.edges.length < need.edges ? 'edges' : constructPicks.points.length < need.points ? 'points' : undefined)
  const openConstruction = (kind: Construction) => {
    select()
    setConstructPicks({ faces: [], edges: [], points: [] })
    setConstruction({ kind, axisFrom: 'edge' })
  }
  const openArrange = () => {
    const shown = model?.bodies.flatMap((body, index) => body.visible && !body.empty ? [index] : []) ?? []
    const bodies = chosenBodies.length >= 2 ? chosenBodies : shown
    select()
    setArrange({ bodies })
  }
  const closeArrange = () => { setArrange(undefined); clearPreview() }
  const closeSection = () => { setSection(undefined); setSectionPicks([]); clearPreview() }
  /** Section Replace takes two faces in order: the replacement's, then the target's. */
  const pickSectionFace = (face: number) => {
    if (sectionPicks.length >= 2 || sectionPicks.includes(face)) return
    setSelected(face)
    clearPreview()
    setSectionPicks((picks) => [...picks, face])
  }
  const closeConstruction = () => { setConstruction(undefined); setConstructPicks({ faces: [], edges: [], points: [] }); clearPreview() }
  const pickConstructionEdge = (edge: number, point: Vec3) => setConstructPicks((picks) => constructionPhase === 'edges'
    ? { ...picks, edges: [...picks.edges, edge] } : { ...picks, points: [...picks.points, nearestEnd(edge, point)] })
  /** Every real edge of the picked body, or of the only visible one. */
  const pickAllEdges = () => {
    const visible = model?.bodies.flatMap((body, index) => body.visible && !body.empty ? [index] : []) ?? []
    const body = edgePicks.length ? edgeBody(edgePicks[0]) : visible.length === 1 ? visible[0] : undefined
    const range = model?.shape.ranges.find((entry) => entry.body === body)
    if (!range || !model) return
    const { edgeStart } = model.shape
    const all: number[] = []
    for (let edge = range.edges[0]; edge < range.edges[1]; edge++) if (edgeStart[edge + 1] - edgeStart[edge] > 1) all.push(edge)
    setEdgePicks(all)
  }

  const pull = (amount: number) => {
    if (stack || shapePanel || faceTool || transformKind || measureKind || missingInput || revision === undefined || selected === undefined || !Number.isFinite(amount) || amount === 0) return
    if (!model?.shape.faces[selected]?.planar) {
      mutate(() => applyFace(revision, { face: selected, faceOperation: 'resize_round', settings: { delta: String(amount) } }))
      return
    }
    mutate(() => applyPull(revision, { face: selected, distance: amount }))
  }
  const dirty = !!model && model.unsaved
  // The newest steps are the ones being worked on: keep them in view.
  const stepCount = model?.steps.length
  useEffect(() => {
    const list = historyList.current
    if (list) list.scrollTop = list.scrollHeight
  }, [stepCount])
  useEffect(() => {
    if (!dirty) return
    const guard = (event: BeforeUnloadEvent) => {
      if (closing.current) return
      event.preventDefault()
      event.returnValue = false
    }
    window.addEventListener('beforeunload', guard)
    return () => window.removeEventListener('beforeunload', guard)
  }, [dirty])
  const start = () => {
    if (revision === undefined) return
    mutate(async () => {
      if (dirty && !await window.simplecad.confirmDiscard()) return
      // Save must not write the new model over the file open until now.
      await window.simplecad.newFile()
      const next = await resetModel(revision)
      setFileName('Untitled')
      return next
    }, true)
  }

  const open = () => {
    if (revision === undefined) return
    mutate(async () => {
      if (dirty && !await window.simplecad.confirmDiscard()) return
      const file = await window.simplecad.openProject()
      return file ? finishOpen(revision, file) : undefined
    }, true)
  }
  const finishOpen = async (revision: number, file: OfferedProject) => {
    const next = await openProject(revision, file.data, file.name)
    await window.simplecad.acceptProject(file.token)
    setFileName(file.name)
    return next
  }
  useEffect(() => window.simplecad.onProjectRequested(() => setProjectRequested(true)), [])
  useEffect(() => {
    if (busy || revision === undefined || !projectRequested) return
    setProjectRequested(false)
    mutate(async () => {
      const file = await window.simplecad.pendingProject()
      if (!file || (dirty && !await window.simplecad.confirmDiscard())) return
      return finishOpen(revision, file)
    }, true)
  }, [busy, dirty, projectRequested, revision])

  // Marked saved only once the file is written: a cancelled dialog or a failed write leaves the work unsaved.
  const save = (saveAs: boolean, close = false) => {
    if (revision === undefined) return
    mutate(async () => {
      const name = await window.simplecad.saveProject(await saveModel(revision), saveAs)
      if (!name) return
      setFileName(name)
      const next = await markSaved(revision)
      if (close) {
        closing.current = true
        void window.simplecad.windowClose()
      }
      return next
    })
  }
  const saveRef = useRef(save)
  const exportProject = (format: ExportFormat) => {
    if (revision === undefined) return
    mutate(async () => {
      await window.simplecad.saveExport(await exportModel(revision, format), format)
      return undefined
    })
  }
  const importSolid = () => {
    if (revision === undefined) return
    mutate(async () => {
      const file = await window.simplecad.openModel()
      return file ? importModel(revision, file) : undefined
    }, true)
  }
  saveRef.current = save
  useEffect(() => window.simplecad.onSaveAndClose(() => saveRef.current(false, true)), [])

  // Offered once, beside the work rather than in front of it (SimpleCAD 0.4's recovery_bar.py says why).
  useEffect(() => { void window.simplecad.pendingRecovery().then(setRecovery) }, [])
  const recover = () => {
    if (revision === undefined) return
    setRecovery(undefined)
    mutate(async () => {
      if (dirty && !await window.simplecad.confirmDiscard()) return
      const work = await window.simplecad.recoverWork()
      if (!work) return
      const next = await openProject(revision, work.data, work.name, true)
      setFileName('Recovered work')
      return next
    }, true)
  }
  const discardRecovery = () => {
    setRecovery(undefined)
    void window.simplecad.discardRecovery()
  }
  const toggleBody = (body: number, visible: boolean) => {
    if (revision !== undefined) mutate(() => showBody(revision, body, visible))
  }
  const history = (direction: 'undo' | 'redo') => {
    if (revision !== undefined) mutate(() => stepHistory(revision, direction))
  }
  const canUndo = !!model?.canUndo && !busy
  const canRedo = !!model?.canRedo && !busy
  const ready = !!model && !busy
  const movingBody = selected === undefined ? undefined : model?.shape.faces[selected]?.body
  const canStack = ready && !shapePanel && !faceTool && !transformKind && !measureKind && movingBody !== undefined && model.shape.faces.some((face) => face.body !== movingBody)
  const canUseFaceTool = ready && !stack && !shapePanel && !faceTool && !transformKind && !bodyTool && !edgeTool && !construction && !arrange && !section && !measureKind && !sketchTool && !sketchEditor && !matchingPart
  const patternBodies = model?.bodies.filter((body) => body.visible && !body.empty).map((body) => body.name) ?? []
  const sketches = model?.steps.filter((step) => !step.undone && step.kind === 'sketch' && step.state === 'ok') ?? []
  const closeSketchTool = () => { setSketchTool(undefined); clearPreview() }
  const closeSketchEditor = () => { setSketchEditor(undefined); setError('') }
  const openSketch = async (id?: string) => {
    if (revision === undefined || busyRef.current) return
    const picked = selected === undefined ? undefined : sketchPlane(model?.shape.faces[selected], holePosition)
    select()
    if (!id) {
      setSketchEditor({ id: crypto.randomUUID().replaceAll('-', ''), creating: true, facePlane: picked,
        recipe: { plane: picked ?? basePlanes.XY, points: [], entities: [], constraints: [] } })
      return
    }
    const generation = connection.current
    updateBusy(true)
    try {
      const recipe = JSON.parse(await readSketch(revision, id)) as SketchRecipe
      if (connection.current === generation) setSketchEditor({ id, creating: false, recipe, facePlane: picked })
    } catch (failure) {
      if (connection.current === generation) setError((failure as Error).message)
    } finally {
      if (connection.current === generation) updateBusy(false)
    }
  }
  const openSketchTool = (kind: ProfileTool) => {
    select()
    setSketchTool(kind)
  }
  const matchable = model?.steps.filter((step) => !step.undone && step.matchingThread) ?? []
  const closeMatching = () => { setMatchingPart(false); clearPreview() }
  const canTransform = canUseFaceTool && model.bodies.some((body) => body.visible && !body.empty)
  const openTransform = (kind: Transform['kind']) => {
    const visible = model?.bodies.flatMap((body, index) => body.visible && !body.empty ? [index] : [])
    const body = movingBody ?? (visible?.length === 1 ? visible[0] : undefined)
    select()
    setTransformBody(body)
    setTransformSnap(preferences.snap ? kind === 'move' ? preferences.moveStep : preferences.rotationSnap : 0)
    setTransformPivot(undefined)
    setTransformKind(kind)
  }
  const openBodyTool = (kind: BodyOperation) => {
    const visible = model?.bodies.flatMap((body, index) => body.visible && !body.empty ? [index] : [])
    const body = movingBody ?? (visible?.length === 1 ? visible[0] : undefined)
    select()
    setTransformBody(body)
    setBodyTool({ kind })
  }
  const openFaceTool = (tool: FaceOperation['faceOperation']) => {
    setDistance('')
    clearPreview()
    setEditingFace(undefined)
    setFaceTool(tool)
  }
  const idle = !stack && !shapePanel && !faceTool && !transformKind && !bodyTool && !edgeTool && !construction && !arrange && !section && !measureKind && !sketchTool && !matchingPart && !missingInput
  const selectedFace = selected === undefined ? undefined : model?.shape.faces[selected]
  // Flat faces move along their normal; round ones along their radius (typed, as they have no single normal).
  const canPullFace = idle && ready && !!selectedFace
  const startStack = () => {
    setStackSettings(defaultStack)
    setDistance('')
    setPreview(undefined)
    setStack(true)
  }
  /** The Bodies list: each group with the bodies not already shown under an earlier group, then the rest. */
  const bodyTree = useMemo(() => {
    const shown = new Set<number>()
    const tree: { group?: string; body: number; members: number[] }[] = []
    for (const { name, members } of model?.groups ?? []) {
      const own = members.filter((body) => !shown.has(body))
      own.forEach((body) => shown.add(body))
      tree.push({ group: name, body: -1, members: own })
    }
    model?.bodies.forEach((_, body) => { if (!shown.has(body)) tree.push({ body, members: [] }) })
    return tree
  }, [model])
  /** A plain click chooses a body, or its whole group; Ctrl- or Shift-click adds or removes one. */
  const chooseBody = (body: number, adding: boolean) => {
    const group = model?.groups.find(({ members }) => members.includes(body))?.members
    setChosenBodies((chosen) => adding ? chosen.includes(body) ? chosen.filter((b) => b !== body) : [...chosen, body]
      : group ?? (chosen.length === 1 && chosen[0] === body ? [] : [body]))
  }
  const bodyRow = (index: number) => {
    const body = model!.bodies[index]
    return (
      <li key={index} className={`flex items-center gap-2 rounded-md px-2 py-1 ${chosenBodies.includes(index) ? 'bg-accent-soft' : body.visible ? 'bg-hover' : 'text-text-3'}`}>
        <Box className="size-4 shrink-0 text-text-2" />
        {measureKind === 'body' && !body.empty
          ? <button type="button" className="min-w-0 flex-1 truncate text-left" aria-pressed={measurePicks.some((pick) => pick.kind === 'body' && pick.body === index)}
              disabled={!ready} onClick={() => pickMeasure({ kind: 'body', body: index })}>{body.name}</button>
          : <button type="button" className="min-w-0 flex-1 truncate text-left" aria-pressed={chosenBodies.includes(index)}
              title={body.empty ? 'No saved geometry' : undefined}
              onClick={(event) => chooseBody(index, event.ctrlKey || event.metaKey || event.shiftKey)}>{body.name}</button>}
        <IconBtn label={`${body.visible ? 'Hide' : 'Show'} ${body.name}`} tip={false} aria-pressed={body.visible}
          disabled={!ready || body.empty} onClick={() => toggleBody(index, !body.visible)}>
          {body.visible ? <Eye className="size-3.5" /> : <EyeOff className="size-3.5" />}
        </IconBtn>
      </li>
    )
  }
  /** The middle of the chosen body's display bounds. */
  const bodyCentre = useMemo(() => {
    const range = model?.shape.ranges.find(({ body }) => body === transformBody)
    if (!model || !range) return undefined
    const low = [Infinity, Infinity, Infinity]
    const high = [-Infinity, -Infinity, -Infinity]
    for (let i = range.indices[0]; i < range.indices[1]; i++) {
      const vertex = model.shape.indices[i] * 3
      for (let axis = 0; axis < 3; axis++) {
        low[axis] = Math.min(low[axis], model.shape.positions[vertex + axis])
        high[axis] = Math.max(high[axis], model.shape.positions[vertex + axis])
      }
    }
    return low.map((value, axis) => (value + high[axis]) / 2) as Vec3
  }, [model, transformBody])
  const canPlace = !!placement && revision !== undefined && stackPrepared?.edit === placement && stackPrepared.revision === revision && ready
  const actions: CommandActions = {
    newBox: { enabled: ready, run: start },
    save: { enabled: ready, run: () => save(false) },
    saveAs: { enabled: ready, run: () => save(true) },
    open: { enabled: ready, run: open },
    undo: { enabled: canUndo, run: () => history('undo') },
    redo: { enabled: canRedo, run: () => history('redo') },
    fit: { enabled: !!model, run: () => setFrame((n) => n + 1) },
    clear: { enabled: !busy && selected !== undefined, run: () => select() },
    shape: { enabled: canUseFaceTool, run: () => { select(); setShapePanel(true) } },
    pushPull: { enabled: canPullFace, hint: 'Select a face', run: () => distanceField.current?.focus() },
    arrange: { enabled: canTransform && model.bodies.filter((body) => body.visible && !body.empty).length > 1,
      hint: 'Show two or more bodies first', run: openArrange },
    stack: { enabled: canStack && !stack, hint: 'Select a face on the part to move', run: startStack },
    sketch: { enabled: canUseFaceTool, run: () => void openSketch() },
    offsetPlane: { enabled: canUseFaceTool, run: () => openFaceTool('offset_plane') },
    shell: { enabled: canUseFaceTool, run: () => openFaceTool('shell') },
    vent: { enabled: canUseFaceTool, run: () => openFaceTool('vent_cut') },
    extrude: { enabled: canUseFaceTool && sketches.length >= 1, hint: 'Draw a sketch first', run: () => openSketchTool('extrude') },
    revolve: { enabled: canUseFaceTool && sketches.length >= 1, hint: 'Draw a sketch first', run: () => openSketchTool('revolve') },
    sweep: { enabled: canUseFaceTool && sketches.length >= 2, hint: 'Draw a profile and a path', run: () => openSketchTool('sweep') },
    loft: { enabled: canUseFaceTool && sketches.length >= 2, hint: 'Draw two profiles', run: () => openSketchTool('loft') },
    path_pattern: { enabled: canUseFaceTool && sketches.length > 0 && patternBodies.length > 0, run: () => openSketchTool('path_pattern') },
    hole: { enabled: canUseFaceTool, run: () => openFaceTool('hole') },
    thread: { enabled: canUseFaceTool, run: () => openFaceTool('thread') },
    // Keeps the selection: a round face picked first is the one a matching thread is cut into.
    matchingPart: { enabled: canUseFaceTool && matchable.length > 0, hint: 'Model a thread first', run: () => {
      setPreview(undefined)
      setMatchingPart(true)
    } },
    measure: { enabled: canUseFaceTool, run: () => {
      setMeasureKind('face')
      setPreview(undefined)
      setDistance('')
      setError('')
      setMeasurePicks(selected === undefined ? [] : [{ kind: 'face', face: selected }])
    } },
    move: { enabled: canTransform, run: () => openTransform('move') },
    rotate: { enabled: canTransform, run: () => openTransform('rotate') },
    mirror: { enabled: canTransform, hint: 'Show a body first', run: () => openBodyTool('mirror') },
    rectangularPattern: { enabled: canTransform, hint: 'Show a body first', run: () => openBodyTool('rectangular_pattern') },
    circularPattern: { enabled: canTransform, hint: 'Show a body first', run: () => openBodyTool('circular_pattern') },
    scale: { enabled: canTransform, hint: 'Show a body first', run: () => openBodyTool('scale') },
    midplane: { enabled: canTransform, hint: 'Show a body first', run: () => openConstruction('midplane') },
    planeAtAngle: { enabled: canTransform, hint: 'Show a body first', run: () => openConstruction('plane_at_angle') },
    planeThroughPoints: { enabled: canTransform, hint: 'Show a body first', run: () => openConstruction('plane_through_points') },
    tangentPlane: { enabled: canTransform, hint: 'Show a body first', run: () => openConstruction('tangent_plane') },
    axis: { enabled: canTransform, hint: 'Show a body first', run: () => openConstruction('construction_axis') },
    point: { enabled: canTransform, hint: 'Show a body first', run: () => openConstruction('construction_point') },
    fillet: { enabled: canTransform, hint: 'Show a body first', run: () => { select(); setEdgePicks([]); setEdgeTool({ kind: 'fillet' }) } },
    chamfer: { enabled: canTransform, hint: 'Show a body first', run: () => { select(); setEdgePicks([]); setEdgeTool({ kind: 'chamfer' }) } },
    sectionReplace: { enabled: canTransform && model.bodies.filter((body) => body.visible && !body.empty).length > 1,
      hint: 'Show two bodies first', run: () => { select(); setSectionPicks([]); setSection({}) } },
    split: { enabled: canTransform, hint: 'Show a body first', run: () => openBodyTool('split') },
    combine: { enabled: canTransform && model.bodies.filter((body) => body.visible && !body.empty).length > 1,
      hint: 'Show two bodies first', run: () => openBodyTool('boolean') },
    text: { enabled: canUseFaceTool, run: () => openFaceTool('text') },
    faceTexture: { enabled: canUseFaceTool, run: () => openFaceTool('face_texture') },
    settings: { enabled: preferencesLoaded, run: () => setSettingsOpen(true) },
    updates: { enabled: true, run: () => void window.simplecad.checkForUpdates().catch((err: Error) => setError(err.message)) }
  }

  useEffect(() => {
    const handle = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.repeat || event.isComposing || sketchEditor) return
      if (matchesShortcut(event, 'Mod-K')) {
        event.preventDefault()
        setSearchOpen((shown) => !shown)
        return
      }
      if (searchOpen || settingsOpen || document.querySelector('[role="menu"]')) return
      if (!busy && (stack || shapePanel || faceTool || transformKind || bodyTool || edgeTool || construction || arrange || section || measureKind || sketchTool || matchingPart) && event.key === 'Escape') {
        event.preventDefault()
        cancelPlacement()
        cancelShapePanel()
        closeSketchTool()
        closeMatching()
        cancelFaceTool()
        cancelTransform()
        closeBodyTool()
        closeEdgeTool()
        closeConstruction()
        closeArrange()
        closeSection()
        closeMeasure()
        setError('')
        return
      }
      const typing = event.target instanceof HTMLElement && event.target.matches('input, textarea, select, [contenteditable=true]')
      for (const id of ['newBox', 'open', 'save', 'saveAs', 'undo', 'redo', 'fit'] as const) {
        if (!actions[id].enabled || (typing && ['undo', 'redo', 'fit'].includes(id))) continue
        if (!matchesShortcut(event, commands[id].shortcut)) continue
        event.preventDefault()
        actions[id].run()
        return
      }
    }
    window.addEventListener('keydown', handle)
    return () => window.removeEventListener('keydown', handle)
  })

  return (
    <TooltipProvider>
      <main className="flex h-screen flex-col bg-bg text-text">
        <TitleBar dark={dark} onToggleTheme={toggle} project={fileName} dirty={dirty} />
        <div role="toolbar" aria-label="Modeling tools" className="@container flex shrink-0 items-stretch gap-1 overflow-x-auto border-b border-border bg-panel px-2 py-1">
          <Menu>
            <MenuTrigger asChild><Btn variant="primary" size="sm" className="self-center">File</Btn></MenuTrigger>
            <MenuContent>
              {(['newBox', 'open', 'save', 'saveAs', 'settings', 'updates'] as CommandId[]).map((id) => {
                const command = commands[id]
                return <MenuItem key={id} disabled={!actions[id].enabled} onSelect={actions[id].run}
                  shortcut={'shortcut' in command ? command.shortcut : undefined}>{command.label}</MenuItem>
              })}
              <MenuItem disabled={!ready} onSelect={importSolid}>Import model</MenuItem>
              {(['step', 'stl', 'obj', '3mf'] as const).map((format) =>
                <MenuItem key={format} disabled={!ready || (format === 'step'
                  ? !model?.bodies.some((body) => body.visible && !body.empty)
                  : !model?.shape.indices.length)}
                  onSelect={() => exportProject(format)}>Export {format.toUpperCase()}</MenuItem>)}
            </MenuContent>
          </Menu>
          <Ribbon actions={actions} active={{
            shape: shapePanel, move: transformKind === 'move', rotate: transformKind === 'rotate', stack, arrange: !!arrange, sectionReplace: !!section, measure: !!measureKind,
            hole: faceTool === 'hole', thread: faceTool === 'thread', text: faceTool === 'text', faceTexture: faceTool === 'face_texture',
            offsetPlane: faceTool === 'offset_plane', shell: faceTool === 'shell', matchingPart, mirror: bodyTool?.kind === 'mirror',
            rectangularPattern: bodyTool?.kind === 'rectangular_pattern', circularPattern: bodyTool?.kind === 'circular_pattern',
            scale: bodyTool?.kind === 'scale', combine: bodyTool?.kind === 'boolean', split: bodyTool?.kind === 'split',
            fillet: edgeTool?.kind === 'fillet', chamfer: edgeTool?.kind === 'chamfer'
          }} />
          <IconBtn label={commands.undo.label} shortcut={commands.undo.shortcut} disabled={!actions.undo.enabled} onClick={actions.undo.run}><Undo2 className="size-4" /></IconBtn>
          <IconBtn label={commands.redo.label} shortcut={commands.redo.shortcut} disabled={!actions.redo.enabled} onClick={actions.redo.run}><Redo2 className="size-4" /></IconBtn>
          <div className="ml-auto flex items-center">
            <IconBtn label={commands.fit.label} shortcut={commands.fit.shortcut} disabled={!actions.fit.enabled} onClick={actions.fit.run}><Focus className="size-4" /></IconBtn>
            <IconBtn label="Command search" shortcut="Mod-K" onClick={() => setSearchOpen(true)}><Search className="size-4" /></IconBtn>
          </div>
        </div>
        {recovery && (
          <section aria-label="Unsaved work from last time" className="flex shrink-0 items-center gap-2 border-b border-border bg-panel px-3 py-1.5 text-xs">
            <span className="min-w-0 flex-1">SimpleCAD closed unexpectedly. There is unsaved work from {recovery}.</span>
            <Btn variant="primary" size="sm" disabled={!ready} onClick={recover}>Recover</Btn>
            <Btn size="sm" onClick={discardRecovery}>Discard</Btn>
          </section>
        )}
        <div className="flex min-h-0 flex-1">
          <Viewport
            shape={model?.shape} shown={preview} selected={selected} showEdges={showEdges} frame={frame}
            readViewFrame={readViewFrame}
            placingBody={stack ? movingBody : undefined}
            transform={transformKind && transformBody !== undefined && revision !== undefined ? {
              kind: transformKind, body: transformBody, enabled: ready, snap: transformSnap, pivot: transformPivot,
              formatLength: (mm) => `${format.format(displayLength(mm, unit))} ${unit}`,
              onCommit: (transform) => mutate(() => applyTransform(revision, { body: transformBody, transform }))
            } : undefined}
            canPull={canPullFace}
            onPickEdge={edgeTool && !edgeTool.editing && ready ? pickEdge
              : (measureKind === 'edge' || measureKind === 'vertex') && ready ? pickMeasureEdge
              : (constructionPhase === 'edges' || constructionPhase === 'points') && ready ? pickConstructionEdge : undefined}
            pickedEdges={edgeTool ? edgePicks : measuredEdges}
            overlay={idle && selectedFace && model ? <ContextBar actions={actions} planar={selectedFace.planar} distance={distance}
              canPull={canPullFace} distanceRef={distanceField} onPull={() => pull(value)}
              onDistance={(next) => { setDistance(next); setError('') }} /> : undefined}
            onSelect={(face, point) => {
              if (!ready || shapePanel || sketchTool) return
              if (matchingPart) {
                if (face !== undefined) { clearPreview(); setSelected(face) }
                return
              }
              if (section) {
                if (face !== undefined && !section.editing) pickSectionFace(face)
                return
              }
              if (construction) {
                if (face !== undefined && constructionPhase === 'faces') {
                  setSelected(face)
                  setConstructPicks((picks) => ({ ...picks, faces: [...picks.faces, face] }))
                }
                return
              }
              if (measureKind) {
                if (face !== undefined) {
                  if (measureKind === 'body') pickMeasure({ kind: 'body', body: model.shape.faces[face].body })
                  else {
                    setSelected(face)
                    pickMeasure({ kind: 'face', face })
                  }
                }
              } else if (transformKind || bodyTool) {
                if (face !== undefined) {
                  clearPreview()
                  setTransformBody(model.shape.faces[face]?.body)
                }
              } else if (!stack) select(face, point)
              else if (face !== undefined && face !== target && model.shape.faces[face]?.body !== movingBody) {
                pickTarget(face)
              }
            }}
            onDrag={(next, commit) => {
              setDistance(String(next))
              if (commit) pull(next)
            }}
            onDragCancel={() => setDistance('')}
            onError={setViewportError}
          />
          <aside aria-label="Model inspector" className="flex min-h-0 w-56 shrink-0 flex-col gap-3 overflow-y-auto border-l border-border bg-panel p-3">
            <div className="flex items-center justify-between border-b border-border pb-2 text-xs font-semibold">Bodies
              {chosenBodies.length > 1 && <Btn size="sm" disabled={!ready} onClick={() => {
                if (revision !== undefined) mutate(() => groupBodies(revision, chosenBodies))
              }}>Group</Btn>}
            </div>
            <ul aria-label="Bodies" className="grid max-h-[40%] min-h-16 shrink-0 auto-rows-min gap-1 overflow-auto">
              {bodyTree.map((entry) => entry.group === undefined ? bodyRow(entry.body) : (
                <li key={`group:${entry.group}`} className="grid gap-1">
                  <div className="flex items-center gap-2 px-2 py-1 font-medium">
                    <Folder className="size-4 shrink-0 text-text-2" />
                    <span className="min-w-0 flex-1 truncate">{entry.group}</span>
                    <IconBtn label={`Ungroup ${entry.group}`} disabled={!ready} onClick={() => {
                      if (revision !== undefined) mutate(() => ungroup(revision, entry.group!))
                    }}><Ungroup className="size-3.5" /></IconBtn>
                  </div>
                  <ul aria-label={entry.group} className="ml-4 grid gap-1">{entry.members.map(bodyRow)}</ul>
                </li>
              ))}
            </ul>
            <label className="grid gap-1 text-xs text-text-2">
              Planar face
              <select value={selected ?? ''} disabled={!ready || stack || !!transformKind} className={`${inputCls} w-full`}
                onChange={(e) => {
                  const face = e.target.value === '' ? undefined : Number(e.target.value)
                  if (section) {
                    if (face !== undefined && !section.editing) pickSectionFace(face)
                  } else if (construction) {
                    if (face !== undefined && constructionPhase === 'faces') {
                      setSelected(face)
                      setConstructPicks((picks) => ({ ...picks, faces: [...picks.faces, face] }))
                    }
                  } else if (measureKind === 'face') {
                    setSelected(face)
                    if (face !== undefined) pickMeasure({ kind: 'face', face })
                  } else select(face)
                }}>
                <option value="">Select a face</option>
                {model?.shape.faces.map((face, index) => <option key={index} value={index}>{faceLabel(face, model.bodies, unit)}</option>)}
              </select>
            </label>
            {measureKind && model && <MeasurePanel kind={measureKind} readings={measurements} loading={measuring} displayUnit={unit}
              selections={measurePicks.map((pick) => ({
                key: pickKey(pick), label: pick.kind === 'body' ? model.bodies[pick.body]?.name ?? 'Body' :
                  pick.kind === 'edge' ? `${model.bodies[edgeBody(pick.edge) ?? -1]?.name ?? 'Body'} · Edge ${pick.edge + 1}` :
                  pick.kind === 'vertex' ? `${model.bodies[pick.body]?.name ?? 'Body'} · Corner ${pick.point.map((v) => format.format(displayLength(v, unit))).join(', ')}` :
                  model.shape.faces[pick.face] ? faceLabel(model.shape.faces[pick.face], model.bodies, unit) : 'Face'
              }))}
              onKind={setMeasureKind} onRemove={(key) => setMeasurePicks((picks) => picks.filter((pick) => pickKey(pick) !== key))}
              onClose={closeMeasure} />}
            {stack && model && (
              <section aria-label="Stack & Place" className="grid max-h-[50%] shrink-0 gap-2 overflow-auto text-xs">
                <label className="grid gap-1">Target face
                  <select value={target ?? ''} className={inputCls} disabled={busy}
                    onChange={(e) => pickTarget(e.target.value === '' ? undefined : Number(e.target.value))}>
                    <option value="">Select a target face</option>
                    {model.shape.faces.map((face, i) => face.body === movingBody ? null : <option key={i} value={i}>{faceLabel(face, model.bodies, unit)}</option>)}
                  </select>
                </label>
                <StackOptions settings={stackSettings} onChange={setStackSettings} />
                <div className="flex gap-2">
                  <Btn size="sm" variant="primary" disabled={!canPlace} onClick={() => {
                    if (canPlace) mutate(() => applyStack(revision, placement))
                  }}>Place</Btn>
                  <Btn size="sm" disabled={busy} onClick={() => { cancelPlacement(); setError('') }}>Cancel</Btn>
                </div>
              </section>
            )}
            {model && transformKind && revision !== undefined && <TransformPanel key={transformKind}
              kind={transformKind} body={transformBody} bodies={model.bodies} busy={busy}
              snap={transformSnap} onSnap={setTransformSnap}
              preferredSnap={transformKind === 'move' ? preferences.moveStep : preferences.rotationSnap} onPivot={setTransformPivot}
              onBody={(body) => { clearPreview(); setTransformBody(body) }}
              onChange={clearPreview} onPreview={(body, transform) => void requestPreview(() => previewTransform(revision, { body, transform }))}
              onApply={(body, transform) => mutate(() => applyTransform(revision, { body, transform }))}
              onCancel={cancelTransform} />}
            {model && editingMove && revision !== undefined && (() => {
              const step = model.steps.find((entry) => entry.id === editingMove && !entry.undone)
              return step?.faceSettings && <StepSettingsPanel key={step.id} name={step.name} busy={busy} preset={step.faceSettings}
                onCancel={() => setEditingMove(undefined)}
                onEdit={(changes) => mutate(() => editSavedStep(revision, { editStepId: step.id, changes }))} />
            })()}
            {model && section && revision !== undefined && <SectionReplacePanel key={section.editing ?? 'new'} busy={busy}
              picked={sectionPicks.map((face) => model.shape.faces[face] ? faceLabel(model.shape.faces[face], model.bodies, unit) : 'Face')}
              preset={model.steps.find((step) => step.id === section.editing && !step.undone)?.faceSettings}
              onClear={() => { setSectionPicks([]); setSelected(undefined); clearPreview() }} onChange={clearPreview} onCancel={closeSection}
              onPreview={(inputs) => void requestPreview(() => previewSectionReplace(revision,
                { sectionReplacement: sectionPicks[0], sectionTarget: sectionPicks[1], inputs }))}
              onApply={(inputs) => mutate(() => applySectionReplace(revision,
                { sectionReplacement: sectionPicks[0], sectionTarget: sectionPicks[1], inputs }))}
              onEdit={(changes) => mutate(() => editSavedStep(revision, { editStepId: section.editing!, changes }))} />}
            {model && arrange && revision !== undefined && <ArrangePanel key={arrange.editing ?? 'new'} busy={busy}
              names={arrange.bodies.map((body) => model.bodies[body]?.name ?? '')}
              preset={model.steps.find((step) => step.id === arrange.editing && !step.undone)?.faceSettings}
              onChange={clearPreview} onCancel={closeArrange}
              onPreview={(inputs) => void requestPreview(() => previewArrange(revision, { arrange: arrange.bodies, inputs }))}
              onApply={(inputs) => mutate(() => applyArrange(revision, { arrange: arrange.bodies, inputs }))}
              onEdit={(changes) => mutate(() => editSavedStep(revision, { editStepId: arrange.editing!, changes }))} />}
            {model && construction && revision !== undefined && <ConstructionPanel key={`${construction.kind}:${construction.editing ?? 'new'}`}
              kind={construction.kind} busy={busy} axisFrom={construction.axisFrom}
              picked={{ faces: constructPicks.faces.length, edges: constructPicks.edges.length, points: constructPicks.points.length }}
              preset={model.steps.find((step) => step.id === construction.editing && !step.undone)?.faceSettings}
              onAxisFrom={(axisFrom) => setConstruction({ ...construction, axisFrom })}
              onClear={() => { setSelected(undefined); setConstructPicks({ faces: [], edges: [], points: [] }) }} onCancel={closeConstruction}
              onApply={(values) => mutate(() => applyConstruction(revision, { construction: construction.kind,
                faces: constructPicks.faces, edges: constructPicks.edges,
                inputs: construction.kind === 'plane_through_points' ? { ...values, points: constructPicks.points } : values }))}
              onEdit={(changes) => mutate(() => editSavedStep(revision, { editStepId: construction.editing!, changes }))} />}
            {model && edgeTool && revision !== undefined && <EdgeFinishPanel key={`${edgeTool.kind}:${edgeTool.editing ?? 'new'}`}
              kind={edgeTool.kind} busy={busy} picked={edgePicks.length}
              body={edgePicks.length ? model.bodies[edgeBody(edgePicks[0]) ?? -1]?.name : undefined}
              preset={model.steps.find((step) => step.id === edgeTool.editing && !step.undone)?.faceSettings}
              onAll={pickAllEdges} onClear={() => setEdgePicks([])} onChange={clearPreview} onCancel={closeEdgeTool}
              onPreview={(value) => void requestPreview(() => previewEdgeFinish(revision, { edgeFinish: edgeTool.kind, edges: edgePicks, value }))}
              onApply={(value) => mutate(() => applyEdgeFinish(revision, { edgeFinish: edgeTool.kind, edges: edgePicks, value }))}
              onEdit={(changes) => mutate(() => editSavedStep(revision, { editStepId: edgeTool.editing!, changes }))} />}
            {model && bodyTool && revision !== undefined && <BodyOperationPanel key={`${bodyTool.kind}:${bodyTool.editing ?? 'new'}`}
              kind={bodyTool.kind} bodies={model.bodies} body={transformBody} busy={busy} centre={bodyCentre}
              preset={model.steps.find((step) => step.id === bodyTool.editing && !step.undone)?.faceSettings}
              onBody={(body) => { clearPreview(); setTransformBody(body) }} onChange={clearPreview} onCancel={closeBodyTool}
              onPreview={(edit) => void requestPreview(() => previewBodyOperation(revision, edit))}
              onApply={(edit) => mutate(() => applyBodyOperation(revision, edit))}
              onEdit={(changes) => mutate(() => editSavedStep(revision, { editStepId: bodyTool.editing!, changes }))} />}
            {shapePanel && revision !== undefined && <ShapePanel key={editingShape ?? 'new'} busy={busy} saved={savedShape}
              onApply={(edit) => shapeStep
                ? mutate(() => editSavedStep(revision, { editStepId: shapeStep.id, changes: edit.values }))
                : mutate(() => createShape(revision, edit), true)}
              onPreview={(edit) => {
                if (shapeStep) void requestPreview(() => previewSavedStep(revision, { editStepId: shapeStep.id, changes: edit.values }))
              }}
              onChange={clearPreview} onCancel={cancelShapePanel} />}
            {sketchTool && revision !== undefined && <SketchSolidPanel key={sketchTool} kind={sketchTool} sketches={sketches} bodies={patternBodies}
              busy={busy} onChange={clearPreview} onClose={closeSketchTool}
              onPreview={(edit) => void requestPreview(() => previewSketchSolid(revision, edit))}
              onApply={(edit) => mutate(() => applySketchSolid(revision, edit), true)} />}
            {matchingPart && revision !== undefined && <MatchingPartPanel threads={matchable} busy={busy}
              roundFace={selected !== undefined && !model?.shape.faces[selected]?.planar}
              onThread={(settings) => selected !== undefined && mutate(() => applyFace(revision, { face: selected, faceOperation: 'matching_thread', settings }))}
              onChange={clearPreview} onClose={closeMatching}
              onPreview={(edit) => void requestPreview(() => previewShape(revision, edit))}
              onApply={(edit) => mutate(() => createShape(revision, edit), true)} />}
            {faceTool === 'text' && <TextPanel key={editingFace ?? 'new'} busy={busy} preset={faceStep?.faceSettings}
              planar={!!faceStep || (selected !== undefined && !!model?.shape.faces[selected]?.planar)}
              onChange={clearPreview} onCancel={cancelFaceTool}
              onPreview={(settings) => void showFacePreview({ faceOperation: 'text', settings })}
              onApply={(settings) => commitFace({ faceOperation: 'text', settings })} />}
            {faceTool === 'vent_cut' && <VentPanel key={editingFace ?? 'new'} busy={busy}
              planar={!!faceStep || (selected !== undefined && !!model?.shape.faces[selected]?.planar)}
              preset={faceStep?.faceSettings} onChange={clearPreview} onCancel={cancelFaceTool}
              onApply={(settings) => commitFace({ faceOperation: 'vent_cut', settings })} />}
            {faceTool === 'resize_round' && <ResizePanel key={editingFace ?? 'new'} busy={busy}
              planar={!!faceStep || selected !== undefined}
              preset={faceStep?.faceSettings} onChange={clearPreview} onCancel={cancelFaceTool}
              onApply={(settings) => commitFace({ faceOperation: 'resize_round', settings })} />}
            {faceTool === 'shell' && <ShellPanel key={editingFace ?? 'new'} busy={busy}
              planar={!!faceStep || selected !== undefined}
              preset={faceStep?.faceSettings} onChange={clearPreview} onCancel={cancelFaceTool}
              onApply={(settings) => commitFace({ faceOperation: 'shell', settings })} />}
            {faceTool === 'offset_plane' && <OffsetPlanePanel key={editingFace ?? 'new'} busy={busy}
              planar={!!faceStep || (selected !== undefined && !!model?.shape.faces[selected]?.planar)}
              preset={faceStep?.faceSettings} onChange={clearPreview} onCancel={cancelFaceTool}
              onApply={(settings) => commitFace({ faceOperation: 'offset_plane', settings })} />}
            {faceTool === 'face_texture' && <TexturePanel key={editingFace ?? 'new'} busy={busy} preset={faceStep?.faceSettings}
              selected={!!faceStep || selected !== undefined}
              onChange={clearPreview} onCancel={cancelFaceTool}
              onPreview={(settings) => void showFacePreview({ faceOperation: 'face_texture', settings })}
              onApply={(settings) => commitFace({ faceOperation: 'face_texture', settings })} />}
            {model && faceTool === 'hole' && <HolePanel key={editingFace ?? 'new'} busy={busy}
              bodies={model.bodies} body={movingBody} preset={faceStep?.faceSettings}
              selected={!!faceStep || (selected !== undefined && !!model.shape.faces[selected]?.planar)}
              onChange={clearPreview} onCancel={cancelFaceTool}
              onPreview={(settings, untilBody) => showFacePreview({ faceOperation: 'hole', settings, untilBody, position: holePosition })}
              onApply={(settings, untilBody) => commitFace({ faceOperation: 'hole', settings, untilBody, position: holePosition })} />}
            {model && faceTool === 'thread' && <ThreadPanel key={editingFace ?? 'new'} busy={busy}
              preset={faceStep?.faceSettings}
              selected={!!faceStep || (selected !== undefined && !model.shape.faces[selected]?.planar)}
              onChange={clearPreview} onCancel={cancelFaceTool}
              onPreview={(settings) => showFacePreview({ faceOperation: 'thread', settings })}
              onApply={(settings) => commitFace({ faceOperation: 'thread', settings })} />}
            {model && repairStep && missingInput && <form aria-label={`Repair ${repairStep.name}`} className="grid gap-2 text-xs"
              onSubmit={(event) => {
                event.preventDefault()
                const body = missingInput.face && selected !== undefined ? model.shape.faces[selected]?.body : repairBody
                if (body === undefined || revision === undefined || moreMissing.some(({ objectId }) => moreRepair[objectId] === undefined)) return
                const more = moreMissing.map(({ objectId }) => ({ missingInputId: objectId, replacementBody: moreRepair[objectId] }))
                mutate(() => repairInput(revision, { repairStepId: repairStep.id, missingInputId: missingInput.objectId,
                  replacementBody: body, replacementFace: missingInput.face ? selected : undefined, ...(more.length ? { more } : {}) }))
              }}>
              <div className="font-semibold">Repair {repairStep.name}</div>
              {missingInput.face ? <p>Pick a target face to replace {missingInput.name}.</p> :
                [missingInput, ...moreMissing].map((input, i) => {
                  const value = i === 0 ? repairBody : moreRepair[input.objectId]
                  return <label key={input.objectId} className="grid gap-1">
                    {moreMissing.length ? `Replacement for ${input.name}` : 'Replacement tool'}
                    <select value={value ?? ''} className={inputCls} disabled={busy} onChange={(event) => {
                      const chosen = event.target.value === '' ? undefined : Number(event.target.value)
                      if (i === 0) setRepairBody(chosen)
                      else setMoreRepair(({ [input.objectId]: _, ...rest }) => chosen === undefined ? rest : { ...rest, [input.objectId]: chosen })
                    }}>
                      <option value="">Select a body</option>
                      {model.bodies.map((body, b) => body.empty ? null : <option key={b} value={b}>{body.name}</option>)}
                    </select>
                  </label>
                })}
              <div className="flex gap-2">
                <Btn size="sm" variant="primary" type="submit" disabled={busy || (missingInput.face ? selected === undefined
                  : repairBody === undefined || moreMissing.some(({ objectId }) => moreRepair[objectId] === undefined))}>Apply repair</Btn>
                <Btn size="sm" type="button" disabled={busy} onClick={() => { setRepairing(undefined); setError('') }}>Cancel</Btn>
              </div>
            </form>}
            <div className="border-b border-border pb-2 pt-2 text-xs font-semibold">History</div>
            <ol ref={historyList} aria-label="Model history" className="min-h-16 flex-1 overflow-auto text-xs">
              {model?.steps.map((step) => (
                <li key={step.id} className={`px-2 py-1.5 ${step.undone ? 'text-text-3' : ''}`}>
                  <div className="flex justify-between gap-2">
                    <span className="truncate">{step.name}</span>
                    {step.state !== 'ok' && <span className="shrink-0 text-danger">{STATES[step.state] ?? step.state}</span>}
                    {!step.undone && (step.faceSettings || step.shapeSettings || step.kind === 'sketch') && <IconBtn label={`Edit ${step.name}`}
                      disabled={busy || editingFace === step.id || editingShape === step.id || bodyTool?.editing === step.id || edgeTool?.editing === step.id || construction?.editing === step.id || arrange?.editing === step.id || section?.editing === step.id || editingMove === step.id} onClick={() => {
                        if (step.kind === 'sketch') {
                          void openSketch(step.id)
                          return
                        }
                        select()
                        if (step.shapeSettings) {
                          setEditingShape(step.id)
                          setShapePanel(true)
                        } else {
                          if (step.kind === 'move' || step.kind === 'push_pull' || step.kind === 'align') {
                            setEditingMove(step.id)
                            return
                          }
                          if (step.kind === 'section_replace') {
                            setSection({ editing: step.id })
                            return
                          }
                          if (step.kind === 'arrange') {
                            setArrange({ bodies: [], editing: step.id })
                            return
                          }
                          if (step.kind in constructionLabels) {
                            setConstruction({ kind: step.kind as Construction, editing: step.id, axisFrom: 'edge' })
                            return
                          }
                          if (step.kind === 'fillet' || step.kind === 'chamfer') {
                            setEdgeTool({ kind: step.kind, editing: step.id })
                            return
                          }
                          if (step.kind in bodyOperationLabels) {
                            setBodyTool({ kind: step.kind as BodyOperation, editing: step.id })
                            return
                          }
                          setEditingFace(step.id)
                          if (step.kind === 'text' || step.kind === 'face_texture' || step.kind === 'hole' || step.kind === 'thread' || step.kind === 'offset_plane' || step.kind === 'shell' || step.kind === 'resize_round' || step.kind === 'vent_cut') {
                            setFaceTool(step.kind)
                          }
                        }
                      }}>
                      <Pencil className="size-3.5" />
                    </IconBtn>}
                  </div>
                  {!step.undone && (step.state === 'failed' || step.state === 'needs_attention' || step.kind === 'embedded_import' || step.constructionPlane) && step.message &&
                    <p className="mt-1 break-words text-text-2">{step.message}</p>}
                  {!step.undone && step.missingInput && <Btn size="sm" disabled={busy || repairing === step.id}
                    aria-label={`Repair ${step.name}`} onClick={() => { select(); setRepairing(step.id); setRepairBody(undefined); setMoreRepair({}) }}>Repair input</Btn>}
                </li>
              ))}
            </ol>
          </aside>
        </div>
        <footer className="grid shrink-0 grid-cols-[1fr_auto] items-center gap-2 border-t border-border bg-panel px-2 py-1">
          <div className="min-w-0 text-xs text-text-2">
            <span data-testid="engine-status" role="status">
              {model ? `Engine ready · ${format.format(displayLength(model.shape.volume, unit, 3))} ${unit}³` : 'Starting the geometry engine…'}
            </span>
            {model?.notes.map((note) => <div key={note}>{note}</div>)}
            {viewportError && <div className="text-danger" role="alert">{viewportError}</div>}
            {error && <div className="text-danger" role="alert">{error}</div>}
            <span data-testid="engine-connections" className="sr-only">{connections}</span>
          </div>
          <div className="flex items-center gap-1.5">
            <Menu>
              <MenuTrigger asChild><IconBtn label="More actions" tip={false}><Ellipsis className="size-4" /></IconBtn></MenuTrigger>
              <MenuContent side="top" align="end">
                <MenuItem onSelect={() => setShowEdges((v) => !v)}>{showEdges ? 'Hide edges' : 'Show edges'}</MenuItem>
                <MenuItem disabled={!actions.fit.enabled} onSelect={actions.fit.run}>{commands.fit.label}</MenuItem>
                <MenuItem disabled={!actions.clear.enabled} onSelect={actions.clear.run}>{commands.clear.label}</MenuItem>
              </MenuContent>
            </Menu>
          </div>
        </footer>
        {sketchEditor && revision !== undefined && <SketchEditor key={sketchEditor.id} revision={revision}
          initial={sketchEditor.recipe} facePlane={sketchEditor.facePlane} error={error} busy={busy} onClose={closeSketchEditor}
          constructionPlanes={model?.steps.flatMap((step) => !step.undone && step.constructionPlane
            ? [{ id: `construction:${step.id}`, name: step.name, plane: step.constructionPlane }] : []) ?? []}
          onApply={(recipe) => mutate(() => applySketchDraft(revision, { sketchDraft: {
            id: sketchEditor.id, creating: sketchEditor.creating, recipe: JSON.stringify(recipe)
          } }))} />}
        {settingsOpen && <PreferencesDialog current={preferences} onClose={() => setSettingsOpen(false)}
          onApply={(next) => { applyPreferences(next); setSettingsOpen(false) }} />}
        {searchOpen && <CommandSearch actions={actions} close={() => setSearchOpen(false)} />}
      </main>
    </TooltipProvider>
  )
}
