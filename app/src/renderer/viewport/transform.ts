import {
  BufferGeometry, Color, Group, LineSegments, Mesh, MeshBasicMaterial, Object3D, Quaternion, Vector3,
  type LineBasicMaterial, type MeshStandardMaterial, type PerspectiveCamera, type Scene
} from 'three'
import { TransformControls } from 'three/addons/controls/TransformControls.js'
import type { DisplayShape, Transform, Vec3 } from '@/shared/engine-protocol'
import { edgeGeometry, indexedGeometry } from './geometry'

export interface TransformView {
  kind: Transform['kind']
  body: number
  enabled: boolean
  /** A step of zero leaves snapping off. */
  snap: number
  pivot?: Vec3
  /** A length in the display unit, with the unit: the live label shows it. */
  formatLength?: (mm: number) => string
  onCommit: (transform: Transform) => void
}

const AXES = 'XYZ'

export function viewportTransform(canvas: HTMLCanvasElement, camera: PerspectiveCamera, scene: Scene,
  surface: MeshStandardMaterial, wire: LineBasicMaterial, draw: () => void, orbit: (enabled: boolean) => void) {
  const gizmo = new TransformControls(camera, canvas)
  const handle = gizmo.getHelper()
  const hub = new Object3D()
  const moving = new Group()
  const mesh = new Mesh(new BufferGeometry(), surface)
  const edges = new LineSegments(new BufferGeometry(), wire)
  const rest = new Mesh(new BufferGeometry(), surface)
  const restEdges = new LineSegments(new BufferGeometry(), wire)
  // While dragging, the body is tinted and a faint copy stays where it started; both share the body's geometry.
  const tinted = surface.clone()
  tinted.color.lerp(new Color(0xe8a33a), 0.45)
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
  gizmo.detach()
  gizmo.enabled = false
  gizmo.showXYZE = false
  gizmo.showE = false
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

  const length = (mm: number) => config?.formatLength?.(mm) ?? `${+mm.toFixed(2)} mm`
  /** The axis the handle drags along or turns about, if it is a single one. */
  const gizmoAxis = () => AXES.indexOf(String(gizmo.axis ?? ''))
  const showLabel = () => {
    if (!active || !config) {
      label.classList.add('hidden')
      canvas.dataset.ghost = ''
      return
    }
    if (canvas.dataset.ghost !== config.kind) canvas.dataset.ghost = config.kind
    let text: string
    if (config.kind === 'move') {
      const parts = [0, 1, 2].filter((i) => Math.abs(delta.getComponent(i)) > 1e-9)
      text = parts.length ? parts.map((i) => `Δ${AXES[i]} ${length(delta.getComponent(i))}`).join('  ') : 'Δ 0'
    } else {
      const angle = Math.atan2(axisVector.set(rotation.x, rotation.y, rotation.z).length(), rotation.w) * 2 * degrees
      text = `${+angle.toFixed(2)}°`
    }
    // Moved by a transform, and only rewritten when it changes: moving it by left/top laid out the whole window
    // on every frame of a Move drag, which cost frames on large projects.
    const reading = typed ? `${typed}${config.kind === 'move' ? '' : '°'} ⏎` : text
    if (label.textContent !== reading) label.textContent = reading
    const at = pivot.clone().add(delta).project(camera)
    label.style.transform = `translate(${(at.x + 1) / 2 * canvas.clientWidth}px, ${(1 - at.y) / 2 * canvas.clientHeight + 28}px)`
    if (label.classList.contains('hidden')) label.classList.remove('hidden')
  }
  /** A typed amount replaces the drag's: along the handle's axis (or the drag's direction), or about its axis. */
  const applyTyped = () => {
    const amount = Number(typed)
    if (!typed || !Number.isFinite(amount) || !config) return false
    if (config.kind === 'move') {
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
    if (lock && config.kind === 'move') {
      for (let i = 0; i < 3; i++) {
        if ((i === lock.axis) === lock.plane) delta.setComponent(i, 0)
      }
    }
    const defaultStep = config.kind === 'move' ? 1 : 15
    const step = (shift ? (config.snap ? 0 : defaultStep) : config.snap) * (fine ? 0.1 : 1)
    if (step && config.kind === 'move') {
      for (let i = 0; i < 3; i++) delta.setComponent(i, Math.round(delta.getComponent(i) / step) * step)
    } else if (step && config.kind === 'rotate') {
      const axis = axisVector.set(rotation.x, rotation.y, rotation.z)
      const angle = Math.atan2(axis.length(), rotation.w) * 2
      const snapped = Math.round(angle * degrees / step) * step / degrees
      rotation.setFromAxisAngle(axis.normalize(), snapped)
    }
    place()
  }
  function place() {
    moving.quaternion.copy(rotation)
    moving.position.copy(pivot).add(delta).sub(rotatedPivot.copy(pivot).applyQuaternion(rotation))
    showLabel()
    draw()
  }
  const dragging = (on: boolean) => {
    ghost.visible = on
    mesh.material = on ? tinted : surface
    if (!on) typed = ''
  }

  const restorePose = () => {
    hub.position.copy(pivot)
    hub.quaternion.identity()
    moving.position.set(0, 0, 0)
    moving.quaternion.identity()
  }
  const cancel = () => {
    if (!active) return
    active = false
    dragging(false)
    showLabel()
    gizmo.reset()
    gizmo.pointerUp(null)
    restorePose()
    orbit(true)
    draw()
  }

  const press = (event: PointerEvent) => {
    if (active && event.button === 2) {
      event.preventDefault()
      event.stopImmediatePropagation()
      cancel()
      return
    }
    if (event.button === 0) {
      claimed = false
      shift = event.shiftKey
      fine = event.ctrlKey
    }
  }
  const key = (event: KeyboardEvent) => {
    if (!active || !config) return
    if (event.key === 'Escape') {
      event.preventDefault()
      event.stopImmediatePropagation()
      cancel()
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
      finish()
      gizmo.pointerUp(null)
      return
    }
    shift = event.shiftKey
    fine = event.ctrlKey
    const axis = ['x', 'y', 'z'].indexOf(event.key.toLowerCase())
    if (event.type === 'keydown' && !event.repeat && config.kind === 'move' && axis >= 0) {
      event.preventDefault()
      lock = lock?.axis === axis && lock.plane === shift ? undefined : { axis, plane: shift }
    }
    preview()
  }

  gizmo.addEventListener('change', draw)
  gizmo.addEventListener('objectChange', preview)
  gizmo.addEventListener('mouseDown', () => {
    claimed = active = true
    lock = undefined
    delta.set(0, 0, 0)
    rotation.identity()
    orbit(false)
    dragging(true)
    showLabel()
  })
  /** One drag, typed or not, is one history step. */
  function finish() {
    if (!active || !config) return
    active = false
    dragging(false)
    showLabel()
    orbit(true)
    const { kind, onCommit } = config
    if (kind === 'move' && delta.lengthSq() > 1e-10) {
      onCommit({ kind, values: [String(delta.x), String(delta.y), String(delta.z)] })
    } else if (kind === 'rotate') {
      const axis = new Vector3(rotation.x, rotation.y, rotation.z)
      if (axis.lengthSq() <= 1e-10) return
      const angle = Math.atan2(axis.length(), rotation.w) * 2 * degrees
      onCommit({ kind, values: [String(angle)], axis: axis.normalize().toArray(), pivot: pivot.toArray() })
    }
  }
  gizmo.addEventListener('mouseUp', finish)
  canvas.addEventListener('pointerdown', press, true)
  canvas.addEventListener('pointercancel', cancel)
  canvas.addEventListener('lostpointercapture', cancel)
  window.addEventListener('keydown', key, true)
  window.addEventListener('keyup', key, true)

  const replace = (part: Mesh | LineSegments, geometry: BufferGeometry) => {
    part.geometry.dispose()
    part.geometry = geometry
  }
  const set = (shape: DisplayShape | undefined, model: BufferGeometry, next: TransformView | undefined,
    showEdges: boolean) => {
    if (drawn !== shape || config?.body !== next?.body || config?.kind !== next?.kind || config?.pivot !== next?.pivot) {
      cancel()
      const range = shape?.ranges.find(({ body }) => body === next?.body)
      const hadGeometry = gizmo.object !== undefined
      gizmo.detach()
      if (shape && next && range && range.indices[0] < range.indices[1]) {
        const [first, last] = range.indices
        const selected = indexedGeometry(model, shape.indices.subarray(first, last))
        replace(mesh, selected)
        ghost.geometry = selected
        replace(edges, edgeGeometry(shape, [range.edges]))
        const stationary = new Uint32Array(shape.indices.length - last + first)
        stationary.set(shape.indices.subarray(0, first))
        stationary.set(shape.indices.subarray(last), first)
        replace(rest, indexedGeometry(model, stationary))
        replace(restEdges, edgeGeometry(shape, [[0, range.edges[0]], [range.edges[1], shape.edgeStart.length - 1]]))
        if (next.pivot) pivot.fromArray(next.pivot)
        else selected.boundingBox!.getCenter(pivot)
        restorePose()
        gizmo.setMode(next.kind === 'move' ? 'translate' : 'rotate')
        gizmo.attach(hub)
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
    const shown = gizmo.object !== undefined
    if (!next?.enabled) cancel()
    handle.visible = gizmo.enabled = shown && !!next?.enabled
    moving.visible = rest.visible = shown
    restEdges.visible = shown && showEdges
    edges.visible = showEdges
    return shown
  }
  const at = new Vector3()
  /** A screen point on each axis handle, for scripted drags (the e2e specs and the frame-time check). */
  const publish = () => {
    if (!gizmo.enabled || !config) {
      delete canvas.dataset.handles
      return
    }
    // TransformControls' own scale: the handles keep one size on screen.
    const scale = hub.position.distanceTo(camera.position) *
      Math.min(1.9 * Math.tan(Math.PI * camera.fov / 360) / camera.zoom, 7) * gizmo.size / 4
    const rect = canvas.getBoundingClientRect()
    const points = [0, 1, 2].map((axis) => {
      if (config!.kind === 'move') {
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
    canvas.dataset.handles = JSON.stringify({ x: points[0], y: points[1], z: points[2] })
  }
  const dispose = () => {
    cancel()
    gizmo.dispose()
    canvas.removeEventListener('pointerdown', press, true)
    canvas.removeEventListener('pointercancel', cancel)
    canvas.removeEventListener('lostpointercapture', cancel)
    window.removeEventListener('keydown', key, true)
    window.removeEventListener('keyup', key, true)
    for (const part of [mesh, edges, rest, restEdges]) part.geometry.dispose()
    tinted.dispose()
    ghost.material.dispose()
    label.remove()
    scene.remove(hub, handle, moving, rest, restEdges, ghost)
  }
  return { set, publish, claimed: () => claimed, dispose }
}
