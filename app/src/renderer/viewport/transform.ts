import {
  BufferGeometry, Color, Group, LineSegments, Mesh, MeshBasicMaterial, Object3D, Quaternion, Raycaster, Vector2, Vector3,
  type LineBasicMaterial, type MeshStandardMaterial, type PerspectiveCamera, type Scene
} from 'three'
import { TransformControls } from 'three/addons/controls/TransformControls.js'
import type { DisplayShape, Transform, Vec3 } from '@/shared/engine-protocol'
import { edgeGeometry, indexedGeometry } from './geometry'
import { axisTargets, bodyBox, moveCapture, snapAngle, snapAxis, snapPoints, type AxisTarget, type MoveLock, type SnapPoint } from './snapping'

export interface TransformView {
  /** Arrows only (move) or rings only (rotate); unset shows both, and the handle dragged decides. */
  kind?: Transform['kind']
  body: number
  /** Several bodies moved together (Arrange's pattern), in place of `body`. */
  bodies?: number[]
  enabled: boolean
  /** A step of zero leaves snapping off: the move step, or the angle step for a rotate-only view. */
  snap: number
  /** The angle step when both show. */
  angleSnap?: number
  /** Tint the bodies while they are not being dragged: they are the selection. */
  highlight?: boolean
  pivot?: Vec3
  /** A length in the display unit, with the unit: the live label shows it. */
  formatLength?: (mm: number) => string
  /** 0.2.0's magnets: a move lines the parts' box up with the other parts' edges, middles, corners and centres;
   * the parts' names say what it met. Off for Arrange's pattern drag. */
  magnets?: string[]
  onCommit: (transform: Transform) => void
}

const AXES = 'XYZ'

export function viewportTransform(canvas: HTMLCanvasElement, camera: PerspectiveCamera, scene: Scene,
  surface: MeshStandardMaterial, wire: LineBasicMaterial, draw: () => void, orbit: (enabled: boolean) => void,
  /** The view's place in the window, as measured when it last resized (not read while drawing). */
  measure: () => DOMRect = () => canvas.getBoundingClientRect()) {
  // Arrows and rings at once: the rings sit outside the arrows, and an arrow under the pointer wins.
  const arrows = new TransformControls(camera, canvas)
  const rings = new TransformControls(camera, canvas)
  rings.setMode('rotate')
  rings.setSize(1.35)
  // One arrow per axis, pointing the positive way: the backward ones covered the part they were on.
  const sets = (arrows as unknown as { _gizmo: { gizmo: Record<string, Object3D>; picker: Record<string, Object3D> } })._gizmo
  for (const set of [sets.gizmo.translate, sets.picker.translate]) {
    for (const child of [...set.children] as Mesh[]) {
      // Each handle's place is baked into its geometry.
      const axis = AXES.indexOf(child.name)
      child.geometry.computeBoundingBox()
      if (axis >= 0 && child.geometry.boundingBox!.getCenter(new Vector3()).getComponent(axis) < -1e-6) child.removeFromParent()
    }
  }
  const handle = new Group()
  handle.add(arrows.getHelper(), rings.getHelper())
  const hub = new Object3D()
  const moving = new Group()
  const mesh = new Mesh(new BufferGeometry(), surface)
  const edges = new LineSegments(new BufferGeometry(), wire)
  const rest = new Mesh(new BufferGeometry(), surface)
  const restEdges = new LineSegments(new BufferGeometry(), wire)
  // While dragging, the body is tinted and a faint copy stays where it started; both share the body's geometry.
  const tinted = surface.clone()
  tinted.color.lerp(new Color(0xe8a33a), 0.45)
  const chosen = surface.clone()
  chosen.color.lerp(new Color(0xe8a33a), 0.22)
  const ghost = new Mesh(mesh.geometry, new MeshBasicMaterial({ color: 0x8a93a3, transparent: true, opacity: 0.22, depthWrite: false }))
  ghost.visible = false
  ghost.renderOrder = 1
  moving.add(mesh, edges)
  scene.add(hub, moving, rest, restEdges, ghost, handle)
  // The live reading beside the handle; digits typed during a drag set it exactly.
  const label = document.createElement('div')
  label.className = 'pointer-events-none absolute left-0 top-0 z-20 hidden -translate-x-1/2 rounded-md bg-[#1c1c22]/90 px-2 py-1 text-[11.5px] font-medium tabular-nums text-white shadow-lg'
  label.setAttribute('role', 'status')
  label.setAttribute('aria-label', 'Transform reading')
  canvas.parentElement?.append(label)
  let typed = ''
  for (const each of [arrows, rings]) {
    each.detach()
    each.enabled = false
    each.showXYZE = false
    each.showE = false
  }
  /** Which handle the current drag is on. */
  let kind: Transform['kind'] = 'move'
  const gizmo = () => kind === 'move' ? arrows : rings
  let live = false
  const pivot = new Vector3()
  const delta = new Vector3()
  const rotation = new Quaternion()
  const rotatedPivot = new Vector3()
  const axisVector = new Vector3()
  let config: TransformView | undefined
  let drawn: DisplayShape | undefined
  let active = false
  let claimed = false
  let lock: { axis: number; plane: boolean } | undefined
  let shift = false
  let fine = false
  const degrees = 180 / Math.PI
  /** This drag's magnets (made when it starts), what each axis is held to, and what the turn is held at. */
  let magnets: { from: [number, number, number][]; targets: AxisTarget[][] } | undefined
  let locks: (MoveLock | undefined)[] = []
  let snapText = ''
  let ringLock: number | undefined
  let ringSnapped = false
  // The part a move snapped to, lit as a hover lights a face.
  const met = new Mesh(new BufferGeometry(), new MeshBasicMaterial({ color: 0xe8a33a, transparent: true, opacity: 0.35, depthWrite: false, polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -1 }))
  met.visible = false
  met.renderOrder = 2
  scene.add(met)
  let metBody: number | undefined
  /** The whole model's mesh, which the snapped-to part's tint is drawn from. */
  let modelGeometry: BufferGeometry | undefined

  const length = (mm: number) => config?.formatLength?.(mm) ?? `${+mm.toFixed(2)} mm`
  /** The axis the handle drags along or turns about, if it is a single one. */
  const gizmoAxis = () => AXES.indexOf(String(gizmo().axis ?? ''))
  const showLabel = () => {
    if (!active || !config) {
      label.classList.add('hidden')
      canvas.dataset.ghost = ''
      return
    }
    if (canvas.dataset.ghost !== kind) canvas.dataset.ghost = kind
    let text: string
    if (kind === 'move') {
      const parts = [0, 1, 2].filter((i) => Math.abs(delta.getComponent(i)) > 1e-9)
      text = parts.length ? parts.map((i) => `Δ${AXES[i]} ${length(delta.getComponent(i))}`).join('  ') : 'Δ 0'
      if (snapText) text += ` · snapped to ${snapText}`
    } else {
      const angle = Math.atan2(axisVector.set(rotation.x, rotation.y, rotation.z).length(), rotation.w) * 2 * degrees
      text = ringSnapped ? `${Math.round(angle)}° · snapped` : `${+angle.toFixed(2)}°`
    }
    if (canvas.dataset.snap !== snapText) canvas.dataset.snap = snapText
    // Moved by a transform, and only rewritten when it changes: moving it by left/top laid out the whole window
    // on every frame of a Move drag, which cost frames on large projects.
    const reading = typed ? `${typed}${kind === 'move' ? '' : '°'} ⏎` : text
    if (label.textContent !== reading) label.textContent = reading
    const at = pivot.clone().add(delta).project(camera)
    const { width, height } = measure()
    label.style.transform = `translate(${(at.x + 1) / 2 * width}px, ${(1 - at.y) / 2 * height + 28}px)`
    if (label.classList.contains('hidden')) label.classList.remove('hidden')
  }
  /** A typed amount replaces the drag's: along the handle's axis (or the drag's direction), or about its axis. */
  const applyTyped = () => {
    const amount = Number(typed)
    if (!typed || !Number.isFinite(amount) || !config) return false
    if (kind === 'move') {
      const axis = gizmoAxis()
      if (axis >= 0) delta.set(0, 0, 0).setComponent(axis, amount)
      else if (delta.lengthSq() > 1e-12) delta.setLength(amount)
      else return false
    } else {
      const axis = gizmoAxis()
      const turn = axisVector.set(rotation.x, rotation.y, rotation.z)
      if (axis >= 0) turn.set(0, 0, 0).setComponent(axis, 1)
      else if (turn.lengthSq() <= 1e-12) return false
      rotation.setFromAxisAngle(turn.normalize(), amount / degrees)
    }
    return true
  }

  const preview = () => {
    if (!active || !config) return
    if (typed && applyTyped()) return place()
    delta.subVectors(hub.position, pivot)
    rotation.copy(hub.quaternion)
    if (lock && kind === 'move') {
      for (let i = 0; i < 3; i++) {
        if ((i === lock.axis) === lock.plane) delta.setComponent(i, 0)
      }
    }
    const defaultStep = kind === 'move' ? 1 : 15
    const snap = kind === 'rotate' && !config.kind ? config.angleSnap ?? 0 : config.snap
    const base = shift ? (snap ? 0 : defaultStep) : snap
    const step = base * (fine ? 0.1 : 1)
    if (kind === 'move') {
      // 0.2.0's magnets first (not with Shift, free, or Ctrl, fine), each dragged axis on its own; the step otherwise.
      snapText = ''
      let snappedTo: number | undefined
      for (let i = 0; i < 3; i++) {
        const raw = delta.getComponent(i)
        const hit = magnets && !shift && !fine && Math.abs(raw) > 1e-9
          ? snapAxis(raw, magnets.from[i], magnets.targets[i], moveCapture(mmPerPixel()), locks[i]) : undefined
        locks[i] = hit?.lock
        if (hit) {
          delta.setComponent(i, hit.value)
          snapText = hit.label
          snappedTo = hit.lock.body
        } else if (step) delta.setComponent(i, Math.round(raw / step) * step)
      }
      showMet(snappedTo)
    } else if (base) {
      // 0.2.0's ring detents: held near each step, free between; Ctrl takes whole degrees.
      const axis = axisVector.set(rotation.x, rotation.y, rotation.z)
      const angle = Math.atan2(axis.length(), rotation.w) * 2 * degrees
      const held = snapAngle(angle, base, ringLock, false, fine)
      ringLock = held.locked
      ringSnapped = held.snapped
      rotation.setFromAxisAngle(axis.normalize(), held.angle / degrees)
    }
    place()
  }
  /** Millimetres per window pixel at the pivot's depth: 0.2.0's magnets catch within 10 pixels. */
  function mmPerPixel() {
    const { height } = measure()
    return 2 * camera.position.distanceTo(pivot) * Math.tan((camera.fov * Math.PI) / 360) / Math.max(1, height)
  }
  /** Light part `body` (what a move snapped to), or none. */
  function showMet(body?: number) {
    if (body === metBody) return
    metBody = body
    met.geometry.dispose()
    const range = body === undefined ? undefined : drawn?.ranges.find((entry) => entry.body === body)
    met.geometry = range && modelGeometry && drawn ? indexedGeometry(modelGeometry, drawn.indices.subarray(...range.indices)) : new BufferGeometry()
    met.visible = !!range
  }
  /** The magnets for a drag of the parts on `picked`: their box against every other part's, and the named snaps of
   * the eight nearest (0.2.0's limit). */
  function makeMagnets(shape: DisplayShape, picked: number[], names: string[]) {
    const box = mesh.geometry.boundingBox
    if (!box) return
    const from = [0, 1, 2].map((i) => [box.min.getComponent(i), (box.min.getComponent(i) + box.max.getComponent(i)) / 2, box.max.getComponent(i)]) as [number, number, number][]
    const others = shape.ranges.filter(({ body }) => !picked.includes(body)).flatMap(({ body }) => {
      const other = bodyBox(shape, body)
      return other ? [{ body, name: names[body] ?? `Part ${body + 1}`, box: other, points: [] as SnapPoint[] }] : []
    })
    const gap = (other: { box: { low: Vec3; high: Vec3 } }) => [0, 1, 2].reduce((sum, i) =>
      sum + Math.max(0, other.box.low[i] - box.max.getComponent(i), box.min.getComponent(i) - other.box.high[i]) ** 2, 0)
    for (const other of [...others].sort((a, b) => gap(a) - gap(b)).slice(0, 8)) other.points = snapPoints(shape, other.body)
    magnets = { from, targets: axisTargets(others) }
  }
  function place() {
    moving.quaternion.copy(rotation)
    moving.position.copy(pivot).add(delta).sub(rotatedPivot.copy(pivot).applyQuaternion(rotation))
    showLabel()
    draw()
  }
  const dragging = (on: boolean) => {
    ghost.visible = on
    mesh.material = on ? tinted : config?.highlight ? chosen : surface
    if (!on) typed = ''
  }

  const restorePose = () => {
    hub.position.copy(pivot)
    hub.quaternion.identity()
    moving.position.set(0, 0, 0)
    moving.quaternion.identity()
  }
  /** A newer result that came while a drag lasted, shown once it ends. */
  let later: Parameters<typeof set> | undefined
  const showLater = () => {
    const waiting = later
    later = undefined
    if (waiting) set(...waiting)
  }
  /** Ends a drag without a step; `why` goes into data-drag, which a failed scripted drag reports. */
  const cancel = (why?: string | Event) => {
    if (!active) return
    canvas.dataset.drag = `cancelled: ${typeof why === 'string' ? why : why?.type ?? ''}`
    active = false
    snapText = ''
    showMet(undefined)
    dragging(false)
    showLabel()
    for (const each of [arrows, rings]) {
      each.reset()
      each.pointerUp(null)
    }
    restorePose()
    orbit(true)
    draw()
    showLater()
  }

  const press = (event: PointerEvent) => {
    if (active && event.button === 2) {
      event.preventDefault()
      event.stopImmediatePropagation()
      cancel('right button')
      return
    }
    // A drag's press is claimed until the next press of any button, so the release that ends it opens no menu.
    claimed = false
    if (event.button === 0) {
      shift = event.shiftKey
      fine = event.ctrlKey
      // Of an arrow and a ring under the pointer, the one nearer the eye takes the press. Shift, Ctrl or Cmd held as
      // the button goes down is a click that adds to the selection, so no handle takes it (held later, they still
      // free or fine the drag).
      const adding = event.shiftKey || event.ctrlKey || event.metaKey
      // The renderer may have skipped a frame (including while its WebGL context is lost).
      // Refresh the controls' world positions and pick meshes before hit-testing the press.
      if (live && !adding) handle.updateMatrixWorld(true)
      const winner = adding ? undefined : nearest(event)
      arrows.enabled = live && !!arrows.object && !adding && winner !== rings
      rings.enabled = live && !!rings.object && !adding && winner !== arrows
      canvas.dataset.drag = `pressed: ${winner === arrows ? 'arrow' : winner === rings ? 'ring' : 'no handle'}${live ? '' : ' (handles off)'}`
    }
  }
  const pointerAt = (event: { clientX: number; clientY: number; button?: number }) => {
    const rect = canvas.getBoundingClientRect()
    return { x: (event.clientX - rect.left) / rect.width * 2 - 1, y: 1 - (event.clientY - rect.top) / rect.height * 2, button: event.button ?? 0 }
  }
  // TransformControls keeps its pick shapes on its gizmo; three.js is pinned, so reading them is safe.
  const picker = (each: TransformControls, mode: string) =>
    (each as unknown as { _gizmo: { picker: Record<string, Object3D> } })._gizmo.picker[mode]
  const ray = new Raycaster()
  /** The gizmo whose handle under the pointer is nearest the eye, if any. */
  function nearest(event: { clientX: number; clientY: number }) {
    if (!live) return undefined
    const at = pointerAt(event)
    camera.updateMatrixWorld()
    ray.setFromCamera(new Vector2(at.x, at.y), camera)
    let best: TransformControls | undefined
    let distance = Infinity
    for (const [each, mode] of [[arrows, 'translate'], [rings, 'rotate']] as const) {
      if (!each.object) continue
      const shape = picker(each, mode)
      shape.updateMatrixWorld(true)
      const hit = ray.intersectObject(shape, true).find((found) => found.object.visible)
      if (hit && hit.distance < distance) [best, distance] = [each, hit.distance]
    }
    return best
  }
  /** Only the nearer handle lights up under the pointer. */
  const hover = (event: PointerEvent) => {
    if (active || !live) return
    const winner = nearest(event)
    if (winner !== arrows) arrows.axis = null
    if (winner !== rings) rings.axis = null
  }
  const key = (event: KeyboardEvent) => {
    if (!active || !config) return
    if (event.key === 'Escape') {
      event.preventDefault()
      event.stopImmediatePropagation()
      cancel('Escape')
      return
    }
    if (event.type === 'keydown' && (/^[0-9.]$/.test(event.key) || (event.key === '-' && !typed) || (event.key === 'Backspace' && typed))) {
      event.preventDefault()
      event.stopImmediatePropagation()
      typed = event.key === 'Backspace' ? typed.slice(0, -1) : typed + event.key
      if (!typed) preview()
      else if (applyTyped()) place()
      else showLabel()
      return
    }
    if (event.type === 'keydown' && event.key === 'Enter' && typed) {
      event.preventDefault()
      event.stopImmediatePropagation()
      const was = gizmo()
      finish()
      was.pointerUp(null)
      return
    }
    shift = event.shiftKey
    fine = event.ctrlKey
    const axis = ['x', 'y', 'z'].indexOf(event.key.toLowerCase())
    if (event.type === 'keydown' && !event.repeat && kind === 'move' && axis >= 0) {
      event.preventDefault()
      lock = lock?.axis === axis && lock.plane === shift ? undefined : { axis, plane: shift }
    }
    preview()
  }

  const start = (which: Transform['kind']) => {
    kind = which
    claimed = active = true
    locks = []
    ringLock = undefined
    ringSnapped = false
    snapText = ''
    magnets = undefined
    const names = config?.magnets
    if (which === 'move' && names && drawn) makeMagnets(drawn, config?.bodies ?? [config!.body], names)
    canvas.dataset.drag = 'dragging'
    lock = undefined
    delta.set(0, 0, 0)
    rotation.identity()
    orbit(false)
    dragging(true)
    showLabel()
  }
  /** One drag, typed or not, is one history step. */
  function finish() {
    if (!active || !config) return
    canvas.dataset.drag = 'done'
    active = false
    snapText = ''
    showMet(undefined)
    dragging(false)
    showLabel()
    orbit(true)
    const { onCommit } = config
    if (kind === 'move' && delta.lengthSq() > 1e-10) {
      onCommit({ kind, values: [String(delta.x), String(delta.y), String(delta.z)] })
    } else if (kind === 'rotate') {
      const axis = new Vector3(rotation.x, rotation.y, rotation.z)
      if (axis.lengthSq() > 1e-10) {
        const angle = Math.atan2(axis.length(), rotation.w) * 2 * degrees
        onCommit({ kind, values: [String(angle)], axis: axis.normalize().toArray(), pivot: pivot.toArray() })
      }
    }
    showLater()
  }
  for (const [each, which] of [[arrows, 'move'], [rings, 'rotate']] as const) {
    each.addEventListener('change', draw)
    each.addEventListener('objectChange', preview)
    each.addEventListener('mouseDown', () => start(which))
    each.addEventListener('mouseUp', finish)
  }
  canvas.addEventListener('pointermove', hover)
  canvas.addEventListener('pointerdown', press, true)
  canvas.addEventListener('pointercancel', cancel)
  canvas.addEventListener('lostpointercapture', cancel)
  window.addEventListener('keydown', key, true)
  window.addEventListener('keyup', key, true)

  const replace = (part: Mesh | LineSegments, geometry: BufferGeometry) => {
    part.geometry.dispose()
    part.geometry = geometry
  }
  const set = (shape: DisplayShape | undefined, geometry: BufferGeometry, next: TransformView | undefined,
    showEdges: boolean) => {
    const model = geometry
    // A newer result arriving mid-drag (a live preview landing) waits for the drag to end instead of cancelling it.
    if (active && shape && drawn !== shape && next?.enabled) {
      later = [shape, model, next, showEdges]
      return true
    }
    later = undefined
    // Kept with the shape it belongs to: a result waiting for the drag to end doesn't replace it.
    modelGeometry = model
    if (drawn !== shape || config?.body !== next?.body || String(config?.bodies) !== String(next?.bodies) ||
      config?.kind !== next?.kind || config?.pivot !== next?.pivot) {
      cancel('the view changed')
      const picked = next?.bodies ?? (next ? [next.body] : [])
      const ranges = shape?.ranges.filter(({ body }) => picked.includes(body)) ?? []
      const hadGeometry = arrows.object !== undefined || rings.object !== undefined
      arrows.detach()
      rings.detach()
      if (shape && next && ranges.some((range) => range.indices[0] < range.indices[1])) {
        const pick = (keep: boolean) => {
          const parts = shape.ranges.filter((range) => ranges.includes(range) === keep)
          const out = new Uint32Array(parts.reduce((total, range) => total + range.indices[1] - range.indices[0], 0))
          parts.reduce((at, range) => { out.set(shape.indices.subarray(...range.indices), at); return at + range.indices[1] - range.indices[0] }, 0)
          return { indices: out, edges: parts.map((range) => range.edges) }
        }
        const moved = pick(true)
        const still = pick(false)
        const selected = indexedGeometry(model, moved.indices)
        replace(mesh, selected)
        ghost.geometry = selected
        replace(edges, edgeGeometry(shape, moved.edges))
        replace(rest, indexedGeometry(model, still.indices))
        replace(restEdges, edgeGeometry(shape, still.edges))
        if (next.pivot) pivot.fromArray(next.pivot)
        else selected.boundingBox!.getCenter(pivot)
        restorePose()
        if (next.kind !== 'rotate') arrows.attach(hub)
        if (next.kind !== 'move') rings.attach(hub)
        kind = next.kind ?? 'move'
      } else if (hadGeometry) {
        for (const part of [mesh, edges, rest, restEdges]) replace(part, new BufferGeometry())
        ghost.geometry = mesh.geometry
      }
    } else if (config && !config.enabled && next?.enabled) {
      // A rejected operation leaves the same model. Put its mesh back.
      restorePose()
    }
    drawn = shape
    config = next
    if (!active) mesh.material = next?.highlight ? chosen : surface
    const shown = arrows.object !== undefined || rings.object !== undefined
    if (!next?.enabled) cancel('handles turned off')
    live = shown && !!next?.enabled
    handle.visible = live
    arrows.enabled = live && !!arrows.object
    rings.enabled = live && !!rings.object
    moving.visible = rest.visible = shown
    restEdges.visible = shown && showEdges
    edges.visible = showEdges
    return shown
  }
  const at = new Vector3()
  /** A screen point on each axis handle, for scripted drags (the e2e specs and the frame-time check). */
  const publish = () => {
    if (!live || !config) {
      delete canvas.dataset.handles
      delete canvas.dataset.rings
      return
    }
    // The arrows' points as `handles` and the rings' as `rings`; a view with only rings publishes them as `handles`.
    const anchors = (which: TransformControls) => JSON.stringify(Object.fromEntries(points(which).map((p, i) => ['xyz'[i], p])))
    const put = (key: 'handles' | 'rings', text: string) => { if (canvas.dataset[key] !== text) canvas.dataset[key] = text }
    if (arrows.object) put('handles', anchors(arrows))
    if (rings.object) put(arrows.object ? 'rings' : 'handles', anchors(rings))
    if (!arrows.object || !rings.object) delete canvas.dataset.rings
  }
  // TransformControls' own scale: the handles keep one size on screen.
  const scaleOf = (which: TransformControls, from = hub.position) => from.distanceTo(camera.position) *
    Math.min(1.9 * Math.tan(Math.PI * camera.fov / 360) / camera.zoom, 7) * which.size / 4
  /** Where the handles are in the view, in its pixels: their middle, and how far their rings (or the arrows' tips)
   * reach, so the selection bar can stay off them. From where a drag started, so the bar holds still during one. */
  const reach = () => {
    if (!live || !config) return undefined
    const { width, height } = measure()
    // The rings' pick shape and the arrows' tips both end 0.6 of a handle's size out.
    const out = 0.6 * scaleOf(rings.object ? rings : arrows, pivot)
    const middle = pivot.clone().project(camera)
    const edge = at.setFromMatrixColumn(camera.matrixWorld, 1).multiplyScalar(out).add(pivot).project(camera)
    const [x, y] = [(middle.x + 1) / 2 * width, (1 - middle.y) / 2 * height]
    return { x, y, r: Math.hypot((edge.x + 1) / 2 * width - x, (1 - edge.y) / 2 * height - y) }
  }
  const points = (which: TransformControls) => {
    const scale = scaleOf(which)
    const rect = measure()
    return [0, 1, 2].map((axis) => {
      if (which === arrows) {
        at.set(0, 0, 0).setComponent(axis, 0.45)
      } else {
        // The point of the ring nearest the camera.
        at.subVectors(camera.position, hub.position).setComponent(axis, 0)
        if (at.lengthSq() < 1e-12) at.set(0, 0, 0).setComponent((axis + 1) % 3, 1)
        at.setLength(0.5)
      }
      at.multiplyScalar(scale).add(hub.position).project(camera)
      return [Math.round(rect.left + (at.x + 1) / 2 * rect.width), Math.round(rect.top + (1 - at.y) / 2 * rect.height)]
    })
  }
  const dispose = () => {
    later = undefined
    cancel()
    arrows.dispose()
    rings.dispose()
    canvas.removeEventListener('pointermove', hover)
    canvas.removeEventListener('pointerdown', press, true)
    canvas.removeEventListener('pointercancel', cancel)
    canvas.removeEventListener('lostpointercapture', cancel)
    window.removeEventListener('keydown', key, true)
    window.removeEventListener('keyup', key, true)
    for (const part of [mesh, edges, rest, restEdges, met]) part.geometry.dispose()
    met.material.dispose()
    tinted.dispose()
    chosen.dispose()
    ghost.material.dispose()
    label.remove()
    scene.remove(hub, handle, moving, rest, restEdges, ghost)
  }
  return { set, publish, reach, claimed: () => claimed, dispose }
}
