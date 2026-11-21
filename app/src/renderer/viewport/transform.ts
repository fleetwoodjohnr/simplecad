import {
  BufferGeometry, Group, LineSegments, Mesh, Object3D, Quaternion, Vector3,
  type Camera, type LineBasicMaterial, type Material, type Scene
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
  onCommit: (transform: Transform) => void
}

export function viewportTransform(canvas: HTMLCanvasElement, camera: Camera, scene: Scene,
  surface: Material, wire: LineBasicMaterial, draw: () => void, orbit: (enabled: boolean) => void) {
  const gizmo = new TransformControls(camera, canvas)
  const handle = gizmo.getHelper()
  const hub = new Object3D()
  const moving = new Group()
  const mesh = new Mesh(new BufferGeometry(), surface)
  const edges = new LineSegments(new BufferGeometry(), wire)
  const rest = new Mesh(new BufferGeometry(), surface)
  const restEdges = new LineSegments(new BufferGeometry(), wire)
  moving.add(mesh, edges)
  scene.add(hub, moving, rest, restEdges, handle)
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

  const preview = () => {
    if (!active || !config) return
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
    moving.quaternion.copy(rotation)
    moving.position.copy(pivot).add(delta).sub(rotatedPivot.copy(pivot).applyQuaternion(rotation))
    draw()
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
  })
  gizmo.addEventListener('mouseUp', () => {
    if (!active || !config) return
    active = false
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
  })
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
  const dispose = () => {
    cancel()
    gizmo.dispose()
    canvas.removeEventListener('pointerdown', press, true)
    canvas.removeEventListener('pointercancel', cancel)
    canvas.removeEventListener('lostpointercapture', cancel)
    window.removeEventListener('keydown', key, true)
    window.removeEventListener('keyup', key, true)
    for (const part of [mesh, edges, rest, restEdges]) part.geometry.dispose()
    scene.remove(hub, handle, moving, rest, restEdges)
  }
  return { set, claimed: () => claimed, dispose }
}
