import { useEffect, useRef, type PointerEvent as ReactPointerEvent } from 'react'
import {
  AmbientLight, Box3, BufferGeometry, DirectionalLight, GridHelper, LineBasicMaterial, LineSegments,
  Mesh, MeshBasicMaterial, MeshStandardMaterial, PerspectiveCamera, Raycaster, Scene, Vector2, Vector3, WebGLRenderer
} from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import { ArrowUpDown } from 'lucide-react'
import type { DisplayShape } from '@/shared/engine-protocol'
import { edgeGeometry, faceAtTriangle, surfaceGeometry } from './geometry'

interface Props {
  shape?: DisplayShape
  shown?: DisplayShape
  selected?: number
  showEdges: boolean
  canPull: boolean
  frame: number
  onSelect: (face?: number) => void
  onDrag: (distance: number, commit: boolean) => void
  onDragCancel: () => void
  onError: (error: string) => void
}

export function Viewport(props: Props) {
  const host = useRef<HTMLDivElement>(null)
  const handle = useRef<HTMLButtonElement>(null)
  const current = useRef(props)
  current.current = props
  const sync = useRef<() => void>(() => {})
  const axis = useRef(new Vector2())
  const drag = useRef<{ pointer: number; start: Vector2; projection: Vector2 } | null>(null)

  useEffect(() => {
    const el = host.current!
    let renderer: WebGLRenderer
    try {
      renderer = new WebGLRenderer({ antialias: true, alpha: true })
    } catch {
      current.current.onError('Unable to initialize the 3D view. Check that WebGL is available.')
      return
    }
    renderer.setPixelRatio(Math.min(2, window.devicePixelRatio))
    const canvas = renderer.domElement
    el.appendChild(canvas)
    canvas.dataset.docFocus = ''
    canvas.tabIndex = 0
    canvas.setAttribute('aria-label', '3D view')
    canvas.setAttribute('aria-busy', 'true')
    const scene = new Scene()
    scene.add(new AmbientLight(0xffffff, 1.2))
    const light = new DirectionalLight(0xffffff, 2.2)
    scene.add(light)
    const camera = new PerspectiveCamera(35, 1, 0.1, 10_000)
    camera.up.set(0, 0, 1)
    const controls = new OrbitControls(camera, canvas)
    const grid = new GridHelper(200, 40, 0x888895, 0x474751)
    grid.rotation.x = Math.PI / 2
    grid.position.z = -0.02
    scene.add(grid)

    const body = new Mesh(new BufferGeometry(), new MeshStandardMaterial({
      color: 0x9aa1ab, roughness: 0.7, metalness: 0.05, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1
    }))
    const highlight = new Mesh(new BufferGeometry(), new MeshBasicMaterial({ color: 0xe8a33a, polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -1 }))
    const edges = new LineSegments(new BufferGeometry(), new LineBasicMaterial({ color: 0x292b30 }))
    scene.add(body, highlight, edges)
    let displayed: DisplayShape | undefined
    let frame = -1
    let animation = 0

    const toScreen = (point: Vector3) => {
      point.project(camera)
      return new Vector2((point.x + 1) * el.clientWidth / 2, (1 - point.y) * el.clientHeight / 2)
    }
    const draw = () => {
      if (animation) return
      animation = requestAnimationFrame(() => {
        animation = 0
        light.position.copy(camera.position)
        renderer.render(scene, camera)
        canvas.setAttribute('aria-busy', String(!displayed))
        const button = handle.current
        const { selected, shape, canPull } = current.current
        if (!button) return
        const face = selected === undefined ? undefined : shape?.faces[selected]
        button.hidden = !face || !canPull
        if (!face) return
        const start = new Vector3(...face.center)
        const normal = new Vector3(...face.normal)
        const position = toScreen(start.clone())
        const direction = toScreen(start.add(normal)).sub(position)
        axis.current.copy(direction)
        const length = direction.length()
        button.hidden ||= length < 0.1 ||
          position.x < 0 || position.x > el.clientWidth || position.y < 0 || position.y > el.clientHeight
        button.style.left = `${position.x}px`
        button.style.top = `${position.y}px`
      })
    }
    controls.addEventListener('change', draw)

    sync.current = () => {
      const { shape, shown, selected, showEdges } = current.current
      const next = shown ?? shape
      if (displayed !== next) {
        body.geometry.dispose()
        edges.geometry.dispose()
        body.geometry = next ? surfaceGeometry(next) : new BufferGeometry()
        edges.geometry = next ? edgeGeometry(next) : new BufferGeometry()
        displayed = next
      }
      edges.visible = showEdges
      highlight.visible = !!shape && selected !== undefined && displayed === shape
      highlight.geometry.dispose()
      highlight.geometry = new BufferGeometry()
      if (highlight.visible && shape && selected !== undefined) {
        highlight.geometry.setAttribute('position', body.geometry.getAttribute('position'))
        highlight.geometry.setIndex(body.geometry.index)
        const first = shape.faceTriStart[selected]
        highlight.geometry.setDrawRange(first * 3, (shape.faceTriStart[selected + 1] - first) * 3)
        highlight.frustumCulled = false
      }
      if (next && frame !== current.current.frame) {
        frame = current.current.frame
        const box = new Box3().setFromObject(body)
        const radius = box.getSize(new Vector3()).length() / 2
        box.getCenter(controls.target)
        camera.position.copy(controls.target).addScaledVector(new Vector3(1, -1, 1).normalize(),
          radius / Math.sin((camera.fov * Math.PI) / 360) * 1.15)
        controls.update()
      }
      draw()
    }

    const resize = () => {
      renderer.setSize(el.clientWidth, el.clientHeight)
      camera.aspect = el.clientWidth / Math.max(1, el.clientHeight)
      camera.updateProjectionMatrix()
      draw()
    }
    resize()
    const observer = new ResizeObserver(resize)
    observer.observe(el)
    const ray = new Raycaster()
    const down = new Vector2()
    const press = (e: PointerEvent) => down.set(e.clientX, e.clientY)
    const pick = (e: PointerEvent) => {
      const shape = current.current.shape
      if (!shape || displayed !== shape || e.button !== 0 || down.distanceTo(new Vector2(e.clientX, e.clientY)) > 4) return
      const rect = canvas.getBoundingClientRect()
      camera.updateMatrixWorld()
      ray.setFromCamera(new Vector2(
        (e.clientX - rect.left) / rect.width * 2 - 1,
        1 - (e.clientY - rect.top) / rect.height * 2
      ), camera)
      const hit = ray.intersectObject(body)[0]?.faceIndex
      current.current.onSelect(hit == null ? undefined : faceAtTriangle(shape.faceTriStart, hit))
    }
    canvas.addEventListener('pointerdown', press)
    canvas.addEventListener('pointerup', pick)
    sync.current()
    return () => {
      observer.disconnect()
      cancelAnimationFrame(animation)
      controls.dispose()
      canvas.removeEventListener('pointerdown', press)
      canvas.removeEventListener('pointerup', pick)
      for (const object of [body, edges, highlight, grid]) {
        object.geometry.dispose()
        object.material.dispose()
      }
      renderer.dispose()
      canvas.remove()
      sync.current = () => {}
    }
  }, [])

  useEffect(() => sync.current(), [props.shape, props.shown, props.selected, props.showEdges, props.frame, props.canPull])

  const move = (e: ReactPointerEvent<HTMLButtonElement>, commit: boolean) => {
    const active = drag.current
    if (!active || active.pointer !== e.pointerId) return
    const delta = new Vector2(e.clientX, e.clientY).sub(active.start)
    const distance = Math.round(delta.dot(active.projection) / active.projection.lengthSq() * 100) / 100
    if (commit) drag.current = null
    props.onDrag(distance, commit)
  }

  return (
    <div className="relative min-h-0 flex-1 overflow-hidden bg-workspace">
      <div ref={host} className="absolute inset-0" />
      <button
        hidden ref={handle} type="button" aria-label="Drag face along its normal" title="Drag face along its normal"
        className="absolute z-10 grid size-7 -translate-x-1/2 -translate-y-1/2 touch-none place-items-center rounded-full bg-accent text-accent-fg shadow-md [cursor:grab] active:[cursor:grabbing] [&[hidden]]:hidden"
        onPointerDown={(e) => {
          if (e.button !== 0 || !props.canPull || axis.current.lengthSq() < 0.01) return
          drag.current = { pointer: e.pointerId, start: new Vector2(e.clientX, e.clientY), projection: axis.current.clone() }
          e.currentTarget.setPointerCapture(e.pointerId)
        }}
        onPointerMove={(e) => move(e, false)}
        onPointerUp={(e) => move(e, true)}
        onLostPointerCapture={() => {
          if (!drag.current) return
          drag.current = null
          props.onDragCancel()
        }}
      >
        <ArrowUpDown className="size-4" />
      </button>
    </div>
  )
}
