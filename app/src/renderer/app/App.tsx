import { useEffect, useRef, useState } from 'react'
import { Box, Ellipsis, Eye, EyeOff, Focus, Magnet, Redo2, Search, Undo2 } from 'lucide-react'
import type { BodyInfo, DisplayShape, FaceInfo, ModelResult } from '@/shared/engine-protocol'
import { applyPull, markSaved, onEngineConnected, openProject, openSession, previewPull, resetModel, saveModel, showBody, stepHistory } from '../state/engine'
import { Btn, IconBtn, Menu, MenuContent, MenuItem, MenuTrigger, TooltipProvider, inputCls } from '../ui'
import { Viewport } from '../viewport/Viewport'
import { TitleBar } from './TitleBar'
import { CommandSearch } from './CommandSearch'
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

function faceLabel(face: FaceInfo, bodies: BodyInfo[]) {
  const axis = face.normal.findIndex((n) => Math.abs(n) > 0.9999)
  const side = !face.planar ? 'Curved' : axis < 0 ? 'Slanted' : labels[axis][Number(face.normal[axis] > 0)]
  const several = bodies.filter((b) => b.visible && !b.empty).length > 1
  return `${several ? `${bodies[face.body].name} · ` : ''}${side} · ${format.format(face.area)} mm²`
}

export function App() {
  const [dark, toggle] = useTheme()
  const [model, setModel] = useState<ModelResult>()
  const [fileName, setFileName] = useState('Untitled')
  const [preview, setPreview] = useState<DisplayShape>()
  const [selected, setSelected] = useState<number>()
  const [distance, setDistance] = useState('')
  const [showEdges, setShowEdges] = useState(true)
  const [frame, setFrame] = useState(0)
  const [connections, setConnections] = useState(0)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [searchOpen, setSearchOpen] = useState(false)
  /** How long ago a crashed session left unsaved work, while that is on offer. */
  const [recovery, setRecovery] = useState<string>()
  /** Set once the close is allowed, so the unsaved-work guard lets it through. */
  const closing = useRef(false)
  const busyRef = useRef(false)
  const historyList = useRef<HTMLOListElement>(null)
  const connection = useRef(0)

  const updateBusy = (active: boolean) => {
    busyRef.current = active
    setBusy(active)
  }
  const select = (face?: number) => {
    setSelected(face)
    setPreview(undefined)
    setDistance('')
    setError('')
  }

  useEffect(() => onEngineConnected(() => {
    const generation = ++connection.current
    setConnections(generation)
    updateBusy(true)
    setPreview(undefined)
    setSelected(undefined)
    setDistance('')
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
  useEffect(() => {
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
  }, [busy, selected, revision, value])

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

  const pull = (amount: number) => {
    if (revision === undefined || selected === undefined || !Number.isFinite(amount) || amount === 0) return
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
      if (!file) return
      const next = await openProject(revision, file.data, file.name)
      setFileName(file.name)
      return next
    }, true)
  }

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
  const canUndo = !!model && model.checkpoint.cursor > 0 && !busy
  const canRedo = !!model && model.checkpoint.cursor < model.checkpoint.edits.length && !busy
  const ready = !!model && !busy
  const actions: CommandActions = {
    newBox: { enabled: ready, run: start },
    save: { enabled: ready, run: () => save(false) },
    saveAs: { enabled: ready, run: () => save(true) },
    open: { enabled: ready, run: open },
    undo: { enabled: canUndo, run: () => history('undo') },
    redo: { enabled: canRedo, run: () => history('redo') },
    fit: { enabled: !!model, run: () => setFrame((n) => n + 1) },
    clear: { enabled: !busy && selected !== undefined, run: () => select() },
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
            </MenuContent>
          </Menu>
          <span className="mx-2 border-b-2 border-accent py-2 text-[12.5px] font-medium">Model</span>
          <IconBtn label={commands.undo.label} shortcut={commands.undo.shortcut} disabled={!actions.undo.enabled} onClick={actions.undo.run}><Undo2 className="size-4" /></IconBtn>
          <IconBtn label={commands.redo.label} shortcut={commands.redo.shortcut} disabled={!actions.redo.enabled} onClick={actions.redo.run}><Redo2 className="size-4" /></IconBtn>
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
            canPull={ready && selected !== undefined && !!model?.shape.faces[selected]?.planar} onSelect={(face) => { if (ready) select(face) }}
            onDrag={(next, commit) => {
              setDistance(String(next))
              if (commit) pull(next)
            }}
            onDragCancel={() => setDistance('')}
            onError={setError}
          />
          <aside aria-label="Model inspector" className="flex w-56 shrink-0 flex-col gap-3 border-l border-border bg-panel p-3">
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
              <select value={selected ?? ''} disabled={!ready} className={`${inputCls} w-full`}
                onChange={(e) => select(e.target.value === '' ? undefined : Number(e.target.value))}>
                <option value="">Select a face</option>
                {model?.shape.faces.map((face, index) => <option key={index} value={index}>{faceLabel(face, model.bodies)}</option>)}
              </select>
            </label>
            <div className="border-b border-border pb-2 pt-2 text-xs font-semibold">History</div>
            <ol ref={historyList} aria-label="Model history" className="min-h-0 flex-1 overflow-auto text-xs">
              {model?.steps.map((step, index) => (
                <li key={index} className={`flex justify-between gap-2 px-2 py-1.5 ${step.undone ? 'text-text-3' : ''}`}>
                  <span className="truncate">{step.name}</span>
                  {step.state !== 'ok' && <span className="shrink-0 text-danger">{STATES[step.state] ?? step.state}</span>}
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
              disabled={selected === undefined || busy} className={`${inputCls} w-24`}
              onChange={(e) => { setDistance(e.target.value); setError('') }} />
            <span className="text-xs text-text-2">mm</span>
            <Btn type="submit" variant="primary" size="sm" disabled={!ready || selected === undefined || !model?.shape.faces[selected]?.planar || !Number.isFinite(value) || value === 0}>
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
