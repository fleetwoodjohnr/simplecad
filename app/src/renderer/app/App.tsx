import { useEffect, useMemo, useRef, useState } from 'react'
import { Box, Ellipsis, Eye, EyeOff, Focus, Magnet, Pencil, Redo2, Search, Undo2 } from 'lucide-react'
import type { BodyInfo, DisplayShape, ExportFormat, FaceInfo, FaceOperation, ModelResult, StackEdit, StackSettings, Transform, ViewFrame } from '@/shared/engine-protocol'
import type { OfferedProject } from '@/shared/ipc-types'
import { applyFace, applyPull, applyStack, applyTransform, cancelQueuedStackPreview, createShape, editSavedFace, exportModel, importModel, markSaved, onEngineConnected, openProject, openSession, previewFace, previewPull, previewSavedFace, previewStack, previewTransform, repairInput, resetModel, saveModel, showBody, stepHistory } from '../state/engine'
import { Btn, IconBtn, Menu, MenuContent, MenuItem, MenuTrigger, TooltipProvider, inputCls } from '../ui'
import { Viewport } from '../viewport/Viewport'
import { TitleBar } from './TitleBar'
import { CommandSearch } from './CommandSearch'
import { ShapePanel } from './ShapePanel'
import { TransformPanel } from './TransformPanel'
import { defaultTextSettings, TextPanel } from './TextPanel'
import { defaultTextureSettings, TexturePanel } from './TexturePanel'
import { commands, matchesShortcut, type CommandActions, type CommandId } from './commands'

function useTheme(): [boolean, () => void] {
  const [query] = useState(() => window.matchMedia('(prefers-color-scheme: dark)'))
  const [chosen, setChosen] = useState<boolean | null>(() => {
    const value = localStorage.getItem('simplecad.theme')
    return value === null ? null : value === 'dark'
  })
  const [system, setSystem] = useState(query.matches)
  useEffect(() => {
    const update = () => setSystem(query.matches)
    query.addEventListener('change', update)
    return () => query.removeEventListener('change', update)
  }, [query])
  const dark = chosen ?? system
  useEffect(() => {
    document.documentElement.classList.toggle('dark', dark)
  }, [dark])
  return [dark, () => {
    localStorage.setItem('simplecad.theme', dark ? 'light' : 'dark')
    setChosen(!dark)
  }]
}

const labels = [['Left', 'Right'], ['Front', 'Back'], ['Bottom', 'Top']]
const format = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2, useGrouping: false })
const STATES: Record<string, string> = { suppressed: 'Suppressed', failed: 'Failed', needs_attention: 'Needs attention' }
const defaultStack: StackSettings = {
  operation: 'stack', offset: 0, rotation: 0, flip: false, horizontal: 'center', vertical: 'center',
  horizontalGap: 0, verticalGap: 0
}

function faceLabel(face: FaceInfo, bodies: BodyInfo[]) {
  const axis = face.normal.findIndex((n) => Math.abs(n) > 0.9999)
  const side = !face.planar ? 'Curved' : axis < 0 ? 'Slanted' : labels[axis][Number(face.normal[axis] > 0)]
  const several = bodies.filter((b) => b.visible && !b.empty).length > 1
  return `${several ? `${bodies[face.body].name} · ` : ''}${side} · ${format.format(face.area)} mm²`
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
  const [dark, toggle] = useTheme()
  const [model, setModel] = useState<ModelResult>()
  const [fileName, setFileName] = useState('Untitled')
  const [preview, setPreview] = useState<DisplayShape>()
  const [selected, setSelected] = useState<number>()
  const [stack, setStack] = useState(false)
  const [shapePanel, setShapePanel] = useState(false)
  const [transformKind, setTransformKind] = useState<Transform['kind']>()
  const [transformBody, setTransformBody] = useState<number>()
  const [faceTool, setFaceTool] = useState<FaceOperation['faceOperation']>()
  const [editingFace, setEditingFace] = useState<string>()
  const [repairing, setRepairing] = useState<string>()
  const [repairBody, setRepairBody] = useState<number>()
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
  const [busy, setBusy] = useState(false)
  const [searchOpen, setSearchOpen] = useState(false)
  const [projectRequested, setProjectRequested] = useState(true)
  /** How long ago a crashed session left unsaved work, while that is on offer. */
  const [recovery, setRecovery] = useState<string>()
  /** Set once the close is allowed, so the unsaved-work guard lets it through. */
  const closing = useRef(false)
  const busyRef = useRef(false)
  const historyList = useRef<HTMLOListElement>(null)
  const connection = useRef(0)
  const repairStep = model?.steps.find((step) => step.id === repairing && !step.undone)
  const missingInput = repairStep?.missingInput
  const faceStep = model?.steps.find((step) => step.id === editingFace && !step.undone)

  const updateBusy = (active: boolean) => {
    busyRef.current = active
    setBusy(active)
  }
  const select = (face?: number) => {
    setSelected(face)
    setPreview(undefined)
    setDistance('')
    setError('')
    if (face === undefined) {
      cancelPlacement()
      setTransformKind(undefined)
      setShapePanel(false)
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
    setDistance('')
    cancelPlacement()
    setTransformKind(undefined)
    setShapePanel(false)
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
    if (stack || faceTool || transformKind) return
    setPreview(undefined)
    if (busy || selected === undefined || revision === undefined || !Number.isFinite(value) || value === 0) return
    let active = true
    const timer = window.setTimeout(() => {
      void previewPull(revision, { face: selected, distance: value }).then(
        (next) => { if (active && !busyRef.current) setPreview(next) },
        () => { if (active) setPreview(undefined) }
      )
    }, 48)
    return () => { active = false; window.clearTimeout(timer) }
  }, [busy, selected, revision, value, stack, faceTool, transformKind])

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
    const defaults = operation.faceOperation === 'text' ? defaultTextSettings : defaultTextureSettings
    const original: Record<string, string> = { ...defaults, ...faceStep.faceSettings }
    const changes = Object.fromEntries(Object.entries(operation.settings).filter(([key, value]) => original[key] !== value))
    return { editFaceId: faceStep.id, changes }
  }

  const showFacePreview = async (operation: FaceOperation) => {
    if (busyRef.current || revision === undefined) return
    const saved = savedFaceRequest(operation)
    if (saved ? !Object.keys(saved.changes).length : selected === undefined) return
    const generation = connection.current
    setError('')
    setPreview(undefined)
    updateBusy(true)
    try {
      const next = saved ? await previewSavedFace(revision, saved) : await previewFace(revision, { face: selected!, ...operation })
      if (connection.current === generation) setPreview(next)
    } catch (failure) {
      if (connection.current === generation) setError((failure as Error).message)
    } finally {
      if (connection.current === generation) updateBusy(false)
    }
  }

  const commitFace = (operation: FaceOperation) => {
    if (revision === undefined) return
    const saved = savedFaceRequest(operation)
    if (saved) {
      mutate(() => editSavedFace(revision, saved))
    } else if (selected !== undefined) {
      mutate(() => applyFace(revision, { face: selected, ...operation }))
    }
  }

  const clearFacePreview = () => { setPreview(undefined); setError('') }
  const cancelFaceTool = () => { setFaceTool(undefined); setEditingFace(undefined); clearFacePreview() }

  const cancelTransform = () => { setTransformKind(undefined); clearFacePreview() }
  const showTransformPreview = async (body: number, transform: Transform) => {
    if (busyRef.current || revision === undefined) return
    const generation = connection.current
    clearFacePreview()
    updateBusy(true)
    try {
      const next = await previewTransform(revision, { body, transform })
      if (connection.current === generation) setPreview(next)
    } catch (failure) {
      if (connection.current === generation) setError((failure as Error).message)
    } finally {
      if (connection.current === generation) updateBusy(false)
    }
  }

  const pull = (amount: number) => {
    if (stack || faceTool || transformKind || missingInput || revision === undefined || selected === undefined || !Number.isFinite(amount) || amount === 0) return
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
  const canStack = ready && !shapePanel && !faceTool && !transformKind && movingBody !== undefined && model.shape.faces.some((face) => face.body !== movingBody)
  const canUseFaceTool = ready && !stack && !shapePanel && !faceTool && !transformKind
  const canTransform = canUseFaceTool && model.bodies.some((body) => body.visible && !body.empty)
  const openTransform = (kind: Transform['kind']) => {
    const visible = model?.bodies.flatMap((body, index) => body.visible && !body.empty ? [index] : [])
    const body = movingBody ?? (visible?.length === 1 ? visible[0] : undefined)
    select()
    setTransformBody(body)
    setTransformKind(kind)
  }
  const openFaceTool = (tool: FaceOperation['faceOperation']) => {
    setDistance('')
    clearFacePreview()
    setEditingFace(undefined)
    setFaceTool(tool)
  }
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
    move: { enabled: canTransform, run: () => openTransform('move') },
    rotate: { enabled: canTransform, run: () => openTransform('rotate') },
    text: { enabled: canUseFaceTool, run: () => openFaceTool('text') },
    faceTexture: { enabled: canUseFaceTool, run: () => openFaceTool('face_texture') },
    updates: { enabled: true, run: () => void window.simplecad.checkForUpdates().catch((err: Error) => setError(err.message)) }
  }

  useEffect(() => {
    const handle = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.repeat || event.isComposing) return
      if (matchesShortcut(event, 'Mod-K')) {
        event.preventDefault()
        setSearchOpen((shown) => !shown)
        return
      }
      if (searchOpen || document.querySelector('[role="menu"]')) return
      if ((stack || shapePanel || ((faceTool || transformKind) && !busy)) && event.key === 'Escape') {
        event.preventDefault()
        cancelPlacement()
        setShapePanel(false)
        cancelFaceTool()
        cancelTransform()
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
        <div role="toolbar" aria-label="Modeling tools" className="flex h-11 shrink-0 items-center gap-1 border-b border-border bg-panel px-2">
          <Menu>
            <MenuTrigger asChild><Btn variant="primary" size="sm">File</Btn></MenuTrigger>
            <MenuContent>
              {(['newBox', 'open', 'save', 'saveAs', 'updates'] as CommandId[]).map((id) => {
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
          <span className="mx-2 border-b-2 border-accent py-2 text-[12.5px] font-medium">Model</span>
          <Btn size="sm" disabled={!canUseFaceTool} onClick={() => {
            select()
            setShapePanel(true)
          }}>Shape</Btn>
          <Btn size="sm" disabled={!actions.move.enabled} onClick={actions.move.run}>{commands.move.label}</Btn>
          <Btn size="sm" disabled={!actions.rotate.enabled} onClick={actions.rotate.run}>{commands.rotate.label}</Btn>
          <Btn size="sm" disabled={!actions.text.enabled} onClick={actions.text.run}>{commands.text.label}</Btn>
          <Btn size="sm" disabled={!actions.faceTexture.enabled} onClick={actions.faceTexture.run}>{commands.faceTexture.label}</Btn>
          <IconBtn label={commands.undo.label} shortcut={commands.undo.shortcut} disabled={!actions.undo.enabled} onClick={actions.undo.run}><Undo2 className="size-4" /></IconBtn>
          <IconBtn label={commands.redo.label} shortcut={commands.redo.shortcut} disabled={!actions.redo.enabled} onClick={actions.redo.run}><Redo2 className="size-4" /></IconBtn>
          <Btn size="sm" disabled={!canStack || stack} onClick={() => {
            setStackSettings(defaultStack)
            setDistance('')
            setPreview(undefined)
            setStack(true)
          }}>Stack &amp; Place</Btn>
          <div className="ml-auto">
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
            canPull={!stack && !faceTool && !transformKind && ready && selected !== undefined && !!model?.shape.faces[selected]?.planar} onSelect={(face) => {
              if (!ready) return
              if (transformKind) {
                if (face !== undefined) {
                  clearFacePreview()
                  setTransformBody(model.shape.faces[face]?.body)
                }
              } else if (!stack) select(face)
              else if (face !== undefined && face !== target && model.shape.faces[face]?.body !== movingBody) {
                pickTarget(face)
              }
            }}
            onDrag={(next, commit) => {
              setDistance(String(next))
              if (commit) pull(next)
            }}
            onDragCancel={() => setDistance('')}
            onError={setError}
          />
          <aside aria-label="Model inspector" className="flex min-h-0 w-56 shrink-0 flex-col gap-3 overflow-y-auto border-l border-border bg-panel p-3">
            <div className="border-b border-border pb-2 text-xs font-semibold">Bodies</div>
            <ul aria-label="Bodies" className="grid max-h-[40%] min-h-16 shrink-0 auto-rows-min gap-1 overflow-auto">
              {model?.bodies.map((body, index) => (
                <li key={index} className={`flex items-center gap-2 rounded-md px-2 py-1 ${body.visible ? 'bg-hover' : 'text-text-3'}`}>
                  <Box className="size-4 shrink-0 text-text-2" />
                  <span className="min-w-0 flex-1 truncate" title={body.empty ? 'No saved geometry' : undefined}>{body.name}</span>
                  <IconBtn label={`${body.visible ? 'Hide' : 'Show'} ${body.name}`} tip={false} aria-pressed={body.visible}
                    disabled={!ready || body.empty} onClick={() => toggleBody(index, !body.visible)}>
                    {body.visible ? <Eye className="size-3.5" /> : <EyeOff className="size-3.5" />}
                  </IconBtn>
                </li>
              ))}
            </ul>
            <label className="grid gap-1 text-xs text-text-2">
              Planar face
              <select value={selected ?? ''} disabled={!ready || stack || !!transformKind} className={`${inputCls} w-full`}
                onChange={(e) => select(e.target.value === '' ? undefined : Number(e.target.value))}>
                <option value="">Select a face</option>
                {model?.shape.faces.map((face, index) => <option key={index} value={index}>{faceLabel(face, model.bodies)}</option>)}
              </select>
            </label>
            {stack && model && (
              <section aria-label="Stack & Place" className="grid max-h-[50%] shrink-0 gap-2 overflow-auto text-xs">
                <label className="grid gap-1">Target face
                  <select value={target ?? ''} className={inputCls} disabled={busy}
                    onChange={(e) => pickTarget(e.target.value === '' ? undefined : Number(e.target.value))}>
                    <option value="">Select a target face</option>
                    {model.shape.faces.map((face, i) => face.body === movingBody ? null : <option key={i} value={i}>{faceLabel(face, model.bodies)}</option>)}
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
              onBody={(body) => { clearFacePreview(); setTransformBody(body) }}
              onChange={clearFacePreview} onPreview={(body, transform) => void showTransformPreview(body, transform)}
              onApply={(body, transform) => mutate(() => applyTransform(revision, { body, transform }))}
              onCancel={cancelTransform} />}
            {shapePanel && revision !== undefined && <ShapePanel busy={busy}
              onCreate={(edit) => mutate(() => createShape(revision, edit), true)}
              onCancel={() => { setShapePanel(false); setError('') }} />}
            {faceTool === 'text' && <TextPanel key={editingFace ?? 'new'} busy={busy} preset={faceStep?.faceSettings}
              planar={!!faceStep || (selected !== undefined && !!model?.shape.faces[selected]?.planar)}
              onChange={clearFacePreview} onCancel={cancelFaceTool}
              onPreview={(settings) => void showFacePreview({ faceOperation: 'text', settings })}
              onApply={(settings) => commitFace({ faceOperation: 'text', settings })} />}
            {faceTool === 'face_texture' && <TexturePanel key={editingFace ?? 'new'} busy={busy} preset={faceStep?.faceSettings}
              selected={!!faceStep || selected !== undefined}
              onChange={clearFacePreview} onCancel={cancelFaceTool}
              onPreview={(settings) => void showFacePreview({ faceOperation: 'face_texture', settings })}
              onApply={(settings) => commitFace({ faceOperation: 'face_texture', settings })} />}
            {model && repairStep && missingInput && <form aria-label={`Repair ${repairStep.name}`} className="grid gap-2 text-xs"
              onSubmit={(event) => {
                event.preventDefault()
                const body = missingInput.face && selected !== undefined ? model.shape.faces[selected]?.body : repairBody
                if (body === undefined || revision === undefined) return
                mutate(() => repairInput(revision, { repairStepId: repairStep.id, missingInputId: missingInput.objectId,
                  replacementBody: body, replacementFace: missingInput.face ? selected : undefined }))
              }}>
              <div className="font-semibold">Repair {repairStep.name}</div>
              {missingInput.face ? <p>Pick a target face to replace {missingInput.name}.</p> :
                <label className="grid gap-1">Replacement tool
                  <select value={repairBody ?? ''} className={inputCls} disabled={busy}
                    onChange={(event) => setRepairBody(event.target.value === '' ? undefined : Number(event.target.value))}>
                    <option value="">Select a body</option>
                    {model.bodies.map((body, i) => body.empty ? null : <option key={i} value={i}>{body.name}</option>)}
                  </select>
                </label>}
              <div className="flex gap-2">
                <Btn size="sm" variant="primary" type="submit" disabled={busy || (missingInput.face ? selected === undefined : repairBody === undefined)}>Apply repair</Btn>
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
                    {!step.undone && step.faceSettings && <IconBtn label={`Edit ${step.name}`} disabled={busy || editingFace === step.id}
                      onClick={() => { select(); setEditingFace(step.id); setFaceTool(step.kind === 'text' ? 'text' : 'face_texture') }}>
                      <Pencil className="size-3.5" />
                    </IconBtn>}
                  </div>
                  {!step.undone && (step.state === 'failed' || step.state === 'needs_attention' || step.kind === 'embedded_import') && step.message &&
                    <p className="mt-1 break-words text-text-2">{step.message}</p>}
                  {!step.undone && step.missingInput && <Btn size="sm" disabled={busy || repairing === step.id}
                    aria-label={`Repair ${step.name}`} onClick={() => { select(); setRepairing(step.id); setRepairBody(undefined) }}>Repair input</Btn>}
                </li>
              ))}
            </ol>
          </aside>
        </div>
        <footer className="grid shrink-0 grid-cols-[1fr_auto] items-center gap-2 border-t border-border bg-panel px-2 py-1">
          <div className="min-w-0 text-xs text-text-2">
            <span data-testid="engine-status" role="status">
              {model ? `Engine ready · ${format.format(model.shape.volume)} mm³` : 'Starting the geometry engine…'}
            </span>
            {model?.notes.map((note) => <div key={note}>{note}</div>)}
            {error && <div className="text-danger" role="alert">{error}</div>}
            <span data-testid="engine-connections" className="sr-only">{connections}</span>
          </div>
          <form className="flex items-center gap-1.5" onSubmit={(e) => { e.preventDefault(); pull(value) }}>
            <input aria-label="Pull distance" type="number" step="any" placeholder="Distance" value={distance}
              disabled={stack || !!faceTool || !!transformKind || !!missingInput || selected === undefined || busy} className={`${inputCls} w-24`}
              onChange={(e) => { setDistance(e.target.value); setError('') }} />
            <span className="text-xs text-text-2">mm</span>
            <Btn type="submit" variant="primary" size="sm" disabled={stack || !!faceTool || !!transformKind || !!missingInput || !ready || selected === undefined || !model?.shape.faces[selected]?.planar || !Number.isFinite(value) || value === 0}>
              <Magnet className="size-3.5" /> Pull
            </Btn>
            <Menu>
              <MenuTrigger asChild><IconBtn label="More actions" tip={false}><Ellipsis className="size-4" /></IconBtn></MenuTrigger>
              <MenuContent side="top" align="end">
                <MenuItem onSelect={() => setShowEdges((v) => !v)}>{showEdges ? 'Hide edges' : 'Show edges'}</MenuItem>
                <MenuItem disabled={!actions.fit.enabled} onSelect={actions.fit.run}>{commands.fit.label}</MenuItem>
                <MenuItem disabled={!actions.clear.enabled} onSelect={actions.clear.run}>{commands.clear.label}</MenuItem>
              </MenuContent>
            </Menu>
          </form>
        </footer>
        {searchOpen && <CommandSearch actions={actions} close={() => setSearchOpen(false)} />}
      </main>
    </TooltipProvider>
  )
}
