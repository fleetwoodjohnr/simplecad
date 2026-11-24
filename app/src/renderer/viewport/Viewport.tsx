import { useEffect, useRef, type PointerEvent as ReactPointerEvent, type ReactNode, type RefObject } from 'react'
import {
  AmbientLight, Box3, BufferGeometry, DirectionalLight, DoubleSide, Float32BufferAttribute, GridHelper, LineBasicMaterial, LineSegments,
  MOUSE, Matrix4, Mesh, MeshBasicMaterial, MeshStandardMaterial, PerspectiveCamera, PlaneGeometry, Quaternion, Raycaster, Scene,
  Vector2, Vector3, WebGLRenderer
} from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import { ArrowUpDown } from 'lucide-react'
import type { DisplayShape, Vec3, ViewFrame } from '@/shared/engine-protocol'
import { bodiesInRect, edgeGeometry, faceAtTriangle, facePrism, indexedGeometry, surfaceGeometry } from './geometry'
import { viewportTransform, type TransformView } from './transform'

/** A handle dragged on the model, shown at each of its points: along `axis` it reads millimetres along it;
 * without one, millimetres dragged on screen at its depth (a round's radius). Drags snap to 0.25 mm, Shift for free,
 * Ctrl for 0.05 mm, as 0.2.0's did. */
export interface ModelHandle {
  key: string
  label: string
  at: Vec3[]
  axis?: Vec3
  /** What the drag reads beside the cursor. */
  reading: (value: number) => string
  onDrag: (value: number, commit: boolean) => void
  onCancel: () => void
}

/** The step a drag snaps to: 0.25 mm, free with Shift, 0.05 mm with Ctrl. */
export const snapStep = (event: { shiftKey: boolean; ctrlKey: boolean }) => event.shiftKey ? 0 : event.ctrlKey ? 0.05 : 0.25
export const snapped = (value: number, step: number) => Math.round((step ? Math.round(value / step) * step : value) * 100) / 100

interface Props {
  shape?: DisplayShape
  shown?: DisplayShape
  selected?: number
  placingBody?: number
  transform?: TransformView
  showEdges: boolean
  canPull: boolean
  frame: number
  readViewFrame: RefObject<(() => ViewFrame) | undefined>
  /** A click: the face under it (none on empty space), and whether Shift, Ctrl or Cmd was held to add. */
  onSelect: (face?: number, point?: Vec3, adding?: boolean) => void
  /** A box dragged from empty space: the bodies it took (right to left: any it touches), and whether to add them. */
  onBoxSelect?: (bodies: number[], adding: boolean) => void
  /** A double-click on a part. */
  onOpenBody?: (body: number) => void
  /** The chosen parts, tinted (a gizmo on them tints them itself). */
  chosen?: number[]
  onDrag: (distance: number, commit: boolean) => void
  onDragCancel: () => void
  onError: (error: string) => void
  /** A right click that did not drag (a drag turns the view): the face under it, or none, and where it was. */
  onContextMenu?: (face: number | undefined, x: number, y: number) => void
  /** Tools for the selection, shown beside the selected face (docked at the bottom when it is off screen). */
  overlay?: ReactNode
  /** The overlay is an open tool's panel: placed beside the face once, as it opens, and kept there while the tool
   * picks; with no face, it docks in the top right corner. */
  toolOverlay?: boolean
  /** While set, a click picks the model edge under it (display edge index) instead of a face. */
  onPickEdge?: (edge: number, point: Vec3) => void
  pickedEdges?: number[]
  /** Closed rings drawn over the model: a face pattern's usable region. */
  outline?: Vec3[][]
  /** Bodies of the displayed shape tinted red: those a face pattern could not fit. */
  marked?: number[]
  /** Handles dragged on the model: an edge's round, a split plane's. */
  handles?: ModelHandle[]
  /** A plane drawn through the model (Split's): its centre, normal and half-size. */
  plane?: { centre: Vec3; normal: Vec3; size: number }
  /** A click within a few pixels of an edge, while nothing is open: that edge, and whether to add. */
  onSelectEdge?: (edge: number, point: Vec3, adding: boolean) => void
}

export function Viewport(props: Props) {
  const host = useRef<HTMLDivElement>(null)
  const handle = useRef<HTMLButtonElement>(null)
  const bar = useRef<HTMLDivElement>(null)
  const reading = useRef<HTMLSpanElement>(null)
  /** The swept volume shown while the handle is dragged: blue adds material, red removes it. */
  const sweep = useRef<{ start(): void; update(distance: number): void; stop(): void }>(undefined)
  const current = useRef(props)
  current.current = props
  const sync = useRef<() => void>(() => {})
  const axis = useRef(new Vector2())
  const drag = useRef<{ pointer: number; start: Vector2; projection: Vector2; escape: (e: KeyboardEvent) => void } | null>(null)
  const frameRef = useRef<HTMLDivElement>(null)
  const handleReading = useRef<HTMLSpanElement>(null)
  /** From model points to the view: where a point shows, and how many millimetres a pixel is at its depth. */
  const view = useRef<{ screen(point: Vec3): Vector2; mmPerPx(point: Vec3): number }>(undefined)
  const handleDrag = useRef<{ key: string; pointer: number; start: Vector2; perMm?: Vector2; mmPerPx: number; escape: (e: KeyboardEvent) => void } | null>(null)

  useEffect(() => {
    const el = host.current!
    let renderer: WebGLRenderer
    try {
      renderer = new WebGLRenderer({ antialias: true, alpha: true })
    } catch {
      current.current.onError('Unable to initialize the 3D view. Check that WebGL is available.')
      return
    }
    current.current.onError('')
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
    // As in SimpleCAD 0.4: middle-drag pans and Shift+middle-drag orbits, right-drag orbits; left-drag orbits too, as
    // in 0.5.0 (Shift+left pans).
    controls.mouseButtons = { LEFT: MOUSE.ROTATE, MIDDLE: MOUSE.PAN, RIGHT: MOUSE.ROTATE }
    // A middle press would otherwise start the browser's autoscroll.
    const noAutoscroll = (event: MouseEvent) => { if (event.button === 1) event.preventDefault() }
    canvas.addEventListener('mousedown', noAutoscroll)
    const viewFrame = () => {
      camera.updateMatrixWorld()
      return {
        direction: camera.getWorldDirection(new Vector3()).toArray(),
        up: new Vector3(0, 1, 0).applyQuaternion(camera.quaternion).normalize().toArray()
      }
    }
    const readViewFrame = current.current.readViewFrame
    readViewFrame.current = viewFrame
    const grid = new GridHelper(200, 40, 0x888895, 0x474751)
    grid.rotation.x = Math.PI / 2
    grid.position.z = -0.02
    grid.material.transparent = true
    grid.material.opacity = 0.45
    scene.add(grid)

    const body = new Mesh(new BufferGeometry(), new MeshStandardMaterial({
      color: 0x9aa1ab, roughness: 0.7, metalness: 0.05, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1
    }))
    const committed = new Mesh(new BufferGeometry(), body.material)
    let committedShape: DisplayShape | undefined
    const highlight = new Mesh(new BufferGeometry(), new MeshBasicMaterial({ color: 0xe8a33a, polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -1 }))
    const hover = new Mesh(new BufferGeometry(), new MeshBasicMaterial({
      color: 0xe8a33a, transparent: true, opacity: 0.35, depthWrite: false, polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -1
    }))
    hover.frustumCulled = false
    // Where a previewed body starts (a typed Move or Rotate, Stack & Place), faint behind the preview.
    const ghost = new Mesh(new BufferGeometry(), new MeshBasicMaterial({ color: 0x8a93a3, transparent: true, opacity: 0.22, depthWrite: false }))
    ghost.renderOrder = 1
    let ghostKey = ''
    const prism = new Mesh(new BufferGeometry(), new MeshBasicMaterial({
      color: 0x3b82f6, transparent: true, opacity: 0.35, depthTest: false, depthWrite: false, side: DoubleSide
    }))
    prism.renderOrder = 2
    prism.visible = false
    let swept: ReturnType<typeof facePrism> | undefined
    sweep.current = {
      start() {
        const { shape, selected } = current.current
        if (!shape || selected === undefined) return
        swept = facePrism(shape, selected, shape.faces[selected].normal)
        prism.geometry.dispose()
        prism.geometry = swept.geometry
      },
      update(distance) {
        if (!swept) return
        swept.update(distance)
        prism.material.color.set(distance < 0 ? 0xe5484d : 0x3b82f6)
        prism.visible = distance !== 0
        canvas.dataset.prism = distance > 0 ? 'pull' : distance < 0 ? 'push' : ''
        draw()
      },
      stop() {
        swept = undefined
        prism.visible = false
        canvas.dataset.prism = ''
        draw()
      }
    }
    const edges = new LineSegments(new BufferGeometry(), new LineBasicMaterial({ color: 0x292b30 }))
    const chosen = new LineSegments(new BufferGeometry(), new LineBasicMaterial({ color: 0xe8a33a, depthTest: false }))
    chosen.renderOrder = 3
    let chosenKey = ''
    /** Which edge each line segment of the displayed shape belongs to, made on the first edge pick. */
    let segmentEdge: { shape: DisplayShape; owner: Uint32Array } | undefined
    const edgeOf = (shape: DisplayShape, segment: number) => {
      if (segmentEdge?.shape !== shape) {
        const owner: number[] = []
        for (let edge = 0; edge + 1 < shape.edgeStart.length; edge++) {
          for (let p = shape.edgeStart[edge]; p + 1 < shape.edgeStart[edge + 1]; p++) owner.push(edge)
        }
        segmentEdge = { shape, owner: Uint32Array.from(owner) }
      }
      return segmentEdge.owner[segment]
    }
    const outline = new LineSegments(new BufferGeometry(), new LineBasicMaterial({ color: 0x3b82f6, depthTest: false }))
    outline.renderOrder = 3
    let outlineRings: Vec3[][] | undefined
    const marked = new Mesh(new BufferGeometry(), new MeshBasicMaterial({
      color: 0xe5484d, transparent: true, opacity: 0.6, polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -1
    }))
    let markedKey = ''
    const parts = new Mesh(new BufferGeometry(), new MeshBasicMaterial({
      color: 0xe8a33a, transparent: true, opacity: 0.22, depthWrite: false, polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -1
    }))
    let partsKey = ''
    const plane = new Mesh(new PlaneGeometry(2, 2), new MeshBasicMaterial({
      color: 0x3b82f6, transparent: true, opacity: 0.2, side: DoubleSide, depthWrite: false
    }))
    plane.renderOrder = 2
    plane.visible = false
    scene.add(body, highlight, hover, edges, ghost, prism, chosen, outline, marked, parts, plane)
    // The selection box, drawn over the view while dragged from empty space.
    const area = document.createElement('div')
    area.className = 'pointer-events-none absolute z-10 hidden border border-accent bg-accent/10'
    area.dataset.testid = 'selection-box'
    el.append(area)
    let hovered: number | undefined
    let displayed: DisplayShape | undefined
    let frame = -1
    let animation = 0

    // The view's size and place in the window, measured when it resizes: reading them while drawing laid out the
    // whole window again on every frame, which cost UPS Atlas's steps 50 ms.
    let width = el.clientWidth
    let height = el.clientHeight
    let bounds = canvas.getBoundingClientRect()
    const toScreen = (point: Vector3) => {
      point.project(camera)
      return new Vector2((point.x + 1) * width / 2, (1 - point.y) * height / 2)
    }
    view.current = {
      screen: (point) => toScreen(new Vector3(...point)),
      mmPerPx: (point) => 2 * camera.position.distanceTo(new Vector3(...point)) * Math.tan(camera.fov * Math.PI / 360) / Math.max(1, height)
    }
    /** Each model handle's buttons at their points; hidden off screen. */
    const placeHandles = () => {
      for (const button of frameRef.current?.querySelectorAll<HTMLButtonElement>('button[data-model-handle]') ?? []) {
        const point = current.current.handles?.find((h) => h.key === button.dataset.modelHandle)?.at[Number(button.dataset.index)]
        if (!point) continue
        const at = toScreen(new Vector3(...point))
        button.hidden = at.x < 0 || at.x > width || at.y < 0 || at.y > height
        button.style.left = `${at.x}px`
        button.style.top = `${at.y}px`
      }
    }
    /** Beside the face, clear of the drag handle and inside the view; docked at the bottom without a point (a tool's
     * panel in the top right corner, and kept where it opened). */
    let pinned: Vector2 | 'dock' | undefined
    const placeBar = (at?: Vector2) => {
      const panel = bar.current
      if (!panel) return
      if (current.current.toolOverlay) {
        pinned ??= at?.clone() ?? 'dock'
        at = pinned === 'dock' ? undefined : pinned
      } else pinned = undefined
      const tool = current.current.toolOverlay
      // A tall panel scrolls inside the view rather than running off its bottom.
      const content = panel.firstElementChild as HTMLElement | null
      const tallest = tool ? `${Math.max(120, height - 24)}px` : ''
      if (content && content.style.maxHeight !== tallest) content.style.maxHeight = tallest
      // Its size as last observed (read once for a panel not yet observed).
      if (panelSize?.panel !== panel) panelSize = { panel, w: panel.offsetWidth, h: panel.offsetHeight }
      const { w, h } = panelSize
      const clamp = (v: number, max: number) => Math.max(8, Math.min(v, max - 8))
      const left = `${at ? clamp(at.x + 24, width - w) : tool ? Math.max(8, width - w - 12) : Math.max(8, (width - w) / 2)}px`
      const top = `${at ? clamp(at.y + 24, height - h) : tool ? 12 : height - h - 12}px`
      if (panel.style.left !== left) panel.style.left = left
      if (panel.style.top !== top) panel.style.top = top
    }
    let panelSize: { panel: HTMLElement; w: number; h: number } | undefined
    // The panel's own size changes (More… opened, a preview's note): keep it inside the view.
    const sized = new ResizeObserver((entries) => {
      const box = entries.at(-1)?.borderBoxSize?.[0]
      if (box && bar.current) panelSize = { panel: bar.current, w: box.inlineSize, h: box.blockSize }
      draw()
    })
    let watched: Element | null = null
    /** While edges can be picked or clicked, a window point on each of the first edges, for scripted picks. */
    const publishEdges = () => {
      const shape = current.current.shape
      if ((!current.current.onPickEdge && !current.current.onSelectEdge) || !shape || displayed !== shape) {
        delete canvas.dataset.edgeAnchors
        return
      }
      const rect = bounds
      const anchors: (number[] | null)[] = []
      for (let edge = 0; edge + 1 < shape.edgeStart.length && edge < 64; edge++) {
        const [first, end] = [shape.edgeStart[edge], shape.edgeStart[edge + 1]]
        if (end - first < 2) { anchors.push(null); continue }
        const middle = first + Math.floor((end - first - 1) / 2)
        const world = new Vector3().fromArray(shape.edgePoints, middle * 3).lerp(new Vector3().fromArray(shape.edgePoints, middle * 3 + 3), 0.5)
        const point = world.clone().project(camera)
        // The window point, then the model point it shows, so a script can find an edge by where it is.
        anchors.push([Math.round(rect.left + (point.x + 1) / 2 * rect.width), Math.round(rect.top + (1 - point.y) / 2 * rect.height),
          ...world.toArray().map((v) => Math.round(v * 1000) / 1000)])
      }
      const text = JSON.stringify(anchors)
      if (canvas.dataset.edgeAnchors !== text) canvas.dataset.edgeAnchors = text
    }
    /** On small models, a point on each shown part's top face in window points, for scripted clicks (as edge
     * anchors are): the middle of a face, clear of its edges. */
    let tops: { shape: DisplayShape; points: [number, Vector3][] } | undefined
    const publishBodies = () => {
      const shape = displayed
      if (!shape || shape.ranges.length > 64) {
        delete canvas.dataset.bodyAnchors
        return
      }
      if (tops?.shape !== shape) {
        const best = new Map<number, (typeof shape.faces)[number]>()
        for (const face of shape.faces) {
          const held = best.get(face.body)
          const score = (f: typeof face) => (f.planar ? f.normal[2] : -2) * 1e6 + f.area
          if (!held || score(face) > score(held)) best.set(face.body, face)
        }
        tops = { shape, points: [...best].map(([index, face]) => [index, new Vector3(...face.anchor)]) }
      }
      const rect = bounds
      const anchors = Object.fromEntries(tops.points.map(([index, point]) => {
        const at = point.clone().project(camera)
        return [index, [Math.round(rect.left + (at.x + 1) / 2 * rect.width), Math.round(rect.top + (1 - at.y) / 2 * rect.height)]]
      }))
      const text = JSON.stringify(anchors)
      if (canvas.dataset.bodyAnchors !== text) canvas.dataset.bodyAnchors = text
    }
    const draw = () => {
      if (animation) return
      animation = requestAnimationFrame(() => {
        animation = 0
        light.position.copy(camera.position)
        renderer.render(scene, camera)
        transform.publish()
        publishEdges()
        publishBodies()
        // Where the camera is and what it turns about, for scripted checks of the mouse.
        const rounded = (v: Vector3) => v.toArray().map((n) => Math.round(n * 1000) / 1000)
        const view = JSON.stringify({ position: rounded(camera.position), target: rounded(controls.target) })
        if (canvas.dataset.camera !== view) canvas.dataset.camera = view
        const busy = String(!displayed)
        if (canvas.getAttribute('aria-busy') !== busy) canvas.setAttribute('aria-busy', busy)
        placeHandles()
        if (bar.current !== watched) {
          if (watched) sized.unobserve(watched)
          watched = bar.current
          if (watched) sized.observe(watched)
        }
        if (!current.current.overlay) pinned = undefined
        const button = handle.current
        const { selected, shape, canPull } = current.current
        if (!button) return
        const face = selected === undefined ? undefined : shape?.faces[selected]
        button.hidden = !face || !canPull
        if (!face) return placeBar()
        const start = new Vector3(...face.anchor)
        const normal = new Vector3(...face.normal)
        const position = toScreen(start.clone())
        const direction = toScreen(start.add(normal)).sub(position)
        axis.current.copy(direction)
        const length = direction.length()
        const offscreen = position.x < 0 || position.x > width || position.y < 0 || position.y > height
        button.hidden ||= length < 0.1 || offscreen
        button.style.left = `${position.x}px`
        button.style.top = `${position.y}px`
        placeBar(offscreen ? undefined : position)
      })
    }
    controls.addEventListener('change', draw)
    const transform = viewportTransform(canvas, camera, scene, body.material, edges.material, draw,
      (enabled) => { controls.enabled = enabled }, () => bounds)

    /** Light up face `face` of the displayed shape under the pointer; undefined clears it. */
    const showHover = (face?: number) => {
      const { shape, selected } = current.current
      if (face === selected || !shape || displayed !== shape) face = undefined
      if (face === hovered) return
      hovered = face
      hover.visible = face !== undefined
      if (face !== undefined) {
        hover.geometry.setAttribute('position', body.geometry.getAttribute('position'))
        hover.geometry.setIndex(body.geometry.index)
        hover.geometry.setDrawRange(shape!.faceTriStart[face] * 3, (shape!.faceTriStart[face + 1] - shape!.faceTriStart[face]) * 3)
      }
      draw()
    }

    sync.current = () => {
      const { shape, shown, selected, showEdges, placingBody, transform: tool } = current.current
      const next = shown ?? shape
      if (displayed !== next) {
        hovered = undefined
        hover.visible = false
        hover.geometry.dispose()
        hover.geometry = new BufferGeometry()
        body.geometry.dispose()
        edges.geometry.dispose()
        body.geometry = next ? surfaceGeometry(next) : new BufferGeometry()
        edges.geometry = next ? edgeGeometry(next) : new BufferGeometry()
        displayed = next
      }
      const ghostBody = shown ? placingBody ?? tool?.body : undefined
      const pickShape = ghostBody !== undefined ? shape : undefined
      if (pickShape !== committedShape) {
        committed.geometry.dispose()
        committed.geometry = pickShape ? surfaceGeometry(pickShape) : new BufferGeometry()
        committedShape = pickShape
      }
      const range = pickShape?.ranges.find(({ body: index }) => index === ghostBody)
      const key = range ? `${range.indices}:${committed.geometry.id}` : ''
      if (key !== ghostKey) {
        ghost.geometry.dispose()
        ghost.geometry = range ? indexedGeometry(committed.geometry, pickShape!.indices.subarray(...range.indices)) : new BufferGeometry()
        ghostKey = key
      }
      ghost.visible = !!range
      // A live drag (transform.ts) owns the flag while it lasts.
      if (!canvas.dataset.ghost || canvas.dataset.ghost === 'preview') {
        canvas.dataset.ghost = range ? 'preview' : ''
      }
      if (current.current.outline !== outlineRings) {
        outlineRings = current.current.outline
        const points: number[] = []
        for (const ring of outlineRings ?? []) {
          for (let i = 0; i + 1 < ring.length; i++) points.push(...ring[i], ...ring[i + 1])
        }
        outline.geometry.dispose()
        outline.geometry = new BufferGeometry()
        outline.geometry.setAttribute('position', new Float32BufferAttribute(points, 3))
      }
      /** Tint `bodies` of the displayed shape with `mesh`; returns the key it now shows. */
      const tint = (mesh: Mesh, bodies: number[], key: string) => {
        const nextKey = bodies.length ? `${bodies.join(',')}:${body.geometry.id}` : ''
        if (nextKey === key) return key
        mesh.geometry.dispose()
        const runs = next!.ranges.filter((range) => bodies.includes(range.body)).map((range) => next!.indices.subarray(...range.indices))
        const indices = new Uint32Array(runs.reduce((total, run) => total + run.length, 0))
        runs.reduce((at, run) => { indices.set(run, at); return at + run.length }, 0)
        mesh.geometry = nextKey ? indexedGeometry(body.geometry, indices) : new BufferGeometry()
        return nextKey
      }
      markedKey = tint(marked, next ? current.current.marked ?? [] : [], markedKey)
      partsKey = tint(parts, next && displayed === shape ? current.current.chosen ?? [] : [], partsKey)
      // A group drag (Arrange) moves bodies of the preview; any other moves the model's.
      const transforming = transform.set(tool?.bodies ? next : shown ? undefined : shape, body.geometry, tool, showEdges)
      marked.visible = !!markedKey && !transforming
      parts.visible = !!partsKey && !transforming
      const cut = current.current.plane
      plane.visible = !!cut
      if (cut) {
        plane.position.fromArray(cut.centre)
        plane.quaternion.copy(new Quaternion().setFromUnitVectors(new Vector3(0, 0, 1), new Vector3(...cut.normal).normalize()))
        plane.scale.setScalar(cut.size)
      }
      const picking = !!current.current.onPickEdge
      body.visible = !transforming
      edges.visible = (showEdges || picking) && !transforming
      // Picked edges show while a tool picks them, and a clicked edge while it is the selection.
      const picked = displayed === shape ? current.current.pickedEdges ?? [] : []
      const pickedKey = picked.length && shape ? `${picked.join(',')}:${shape.edgeStart.length}` : ''
      if (pickedKey !== chosenKey) {
        chosen.geometry.dispose()
        chosen.geometry = pickedKey && shape ? edgeGeometry(shape, picked.map((edge) => [edge, edge + 1])) : new BufferGeometry()
        chosenKey = pickedKey
      }
      canvas.dataset.pickedEdges = String(picked.length)
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
      width = el.clientWidth
      height = el.clientHeight
      renderer.setSize(width, height)
      bounds = canvas.getBoundingClientRect()
      camera.aspect = width / Math.max(1, height)
      camera.updateProjectionMatrix()
      draw()
    }
    resize()
    const observer = new ResizeObserver(resize)
    observer.observe(el)
    const ray = new Raycaster()
    const down = new Vector2()
    /** A selection box being dragged from empty space: where it started, and whether it adds. */
    let box: { x: number; y: number; adding: boolean } | undefined
    const endBox = () => {
      box = undefined
      area.classList.add('hidden')
      controls.mouseButtons.LEFT = MOUSE.ROTATE
    }
    const press = (e: PointerEvent) => {
      down.set(e.clientX, e.clientY)
      const { onBoxSelect, onPickEdge, shape } = current.current
      // On empty space a left drag draws a box (Alt+left still turns the view); on the model it turns the view.
      if (e.button !== 0 || e.altKey || !onBoxSelect || onPickEdge || !shape || displayed !== shape ||
        transform.hits(e) || faceAt(e.clientX, e.clientY)) return
      box = { x: e.clientX, y: e.clientY, adding: e.shiftKey || e.ctrlKey || e.metaKey }
      controls.mouseButtons.LEFT = -1 as MOUSE
    }
    const drawBox = (e: PointerEvent) => {
      if (!box) return
      const rect = canvas.getBoundingClientRect()
      const [left, right] = [Math.min(box.x, e.clientX), Math.max(box.x, e.clientX)]
      const [top, bottom] = [Math.min(box.y, e.clientY), Math.max(box.y, e.clientY)]
      Object.assign(area.style, { left: `${left - rect.left}px`, top: `${top - rect.top}px`, width: `${right - left}px`, height: `${bottom - top}px` })
      // Right to left takes what it touches: dashed, as 0.2.0 drew it.
      area.style.borderStyle = e.clientX < box.x ? 'dashed' : 'solid'
      area.classList.toggle('hidden', right - left < 4 && bottom - top < 4)
    }
    /** The model face under a window point, or undefined. */
    const faceAt = (x: number, y: number) => {
      const { shape, placingBody } = current.current
      if (!shape || (displayed !== shape && committedShape !== shape)) return
      const rect = canvas.getBoundingClientRect()
      camera.updateMatrixWorld()
      ray.setFromCamera(new Vector2((x - rect.left) / rect.width * 2 - 1, 1 - (y - rect.top) / rect.height * 2), camera)
      for (const hit of ray.intersectObject(displayed === shape ? body : committed)) {
        if (hit.faceIndex == null) continue
        const face = faceAtTriangle(shape.faceTriStart, hit.faceIndex)
        if (face !== undefined && shape.faces[face]?.body !== placingBody) return { face, point: hit.point }
      }
    }
    /** The model edge nearest a window point, within a few pixels, or undefined. */
    const edgeAt = (x: number, y: number) => {
      const shape = current.current.shape
      if (!shape || displayed !== shape) return
      const rect = canvas.getBoundingClientRect()
      camera.updateMatrixWorld()
      ray.setFromCamera(new Vector2((x - rect.left) / rect.width * 2 - 1, 1 - (y - rect.top) / rect.height * 2), camera)
      // Six pixels, in model units at the orbit target's depth.
      const depth = camera.position.distanceTo(controls.target)
      ray.params.Line.threshold = depth * Math.tan((camera.fov * Math.PI) / 360) * 2 * 6 / Math.max(1, rect.height)
      const hit = ray.intersectObject(edges)[0]
      return hit?.index === undefined ? undefined : { edge: edgeOf(shape, hit.index / 2), point: hit.point.toArray() as Vec3 }
    }
    const pick = (e: PointerEvent) => {
      const shape = current.current.shape
      if (box && e.button === 0) {
        const started = box
        endBox()
        if (down.distanceTo(new Vector2(e.clientX, e.clientY)) > 4 && shape && displayed === shape) {
          const rect = canvas.getBoundingClientRect()
          camera.updateMatrixWorld()
          const viewProjection = new Matrix4().multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse)
          const within = {
            left: Math.min(started.x, e.clientX) - rect.left, right: Math.max(started.x, e.clientX) - rect.left,
            top: Math.min(started.y, e.clientY) - rect.top, bottom: Math.max(started.y, e.clientY) - rect.top
          }
          current.current.onBoxSelect?.(bodiesInRect(shape, viewProjection.elements, rect.width, rect.height, within, e.clientX < started.x), started.adding)
          return
        }
      }
      if (e.button === 2 && current.current.onContextMenu && !transform.claimed() &&
        down.distanceTo(new Vector2(e.clientX, e.clientY)) <= 4) {
        current.current.onContextMenu(faceAt(e.clientX, e.clientY)?.face, e.clientX, e.clientY)
        return
      }
      if (e.button === 0 && current.current.onPickEdge && down.distanceTo(new Vector2(e.clientX, e.clientY)) <= 4) {
        const hit = edgeAt(e.clientX, e.clientY)
        if (hit) current.current.onPickEdge(hit.edge, hit.point)
        return
      }
      if (e.button !== 0 || transform.claimed() || down.distanceTo(new Vector2(e.clientX, e.clientY)) > 4 ||
        (shape !== displayed && shape !== committedShape)) return
      const picked = faceAt(e.clientX, e.clientY)
      // As 0.2.0: an edge has a pick zone wider than its line, and wins over the face it bounds, not one in front.
      const edge = current.current.onSelectEdge && shape === displayed ? edgeAt(e.clientX, e.clientY) : undefined
      const depth = camera.position.distanceTo(controls.target)
      if (edge && (!picked || camera.position.distanceTo(new Vector3(...edge.point)) <= camera.position.distanceTo(picked.point) + depth * 0.01)) {
        current.current.onSelectEdge!(edge.edge, edge.point, e.shiftKey || e.ctrlKey || e.metaKey)
        return
      }
      current.current.onSelect(picked?.face, picked?.point.toArray(), e.shiftKey || e.ctrlKey || e.metaKey)
    }
    const open = (e: MouseEvent) => {
      const shape = current.current.shape
      // Two quick Shift-clicks are two toggles, not a double-click. The gizmo sits on the part's middle; a
      // double-click there moves nothing, so it still opens the part.
      const plain = e.button === 0 && !e.shiftKey && !e.ctrlKey && !e.metaKey
      const picked = plain ? faceAt(e.clientX, e.clientY) : undefined
      if (picked && shape) current.current.onOpenBody?.(shape.faces[picked.face].body)
    }
    // Hover: at most one pick per frame, and none while a button is held (orbiting).
    let pointer: { x: number; y: number } | null = null
    let hoverFrame = 0
    const move = (e: PointerEvent) => {
      drawBox(e)
      pointer = e.buttons ? null : { x: e.clientX, y: e.clientY }
      if (!pointer) return showHover(undefined)
      hoverFrame ||= requestAnimationFrame(() => {
        hoverFrame = 0
        if (!pointer) return
        const started = performance.now()
        showHover(faceAt(pointer.x, pointer.y)?.face)
        // What one hover costs, for the performance check.
        canvas.dataset.hoverMs = (performance.now() - started).toFixed(2)
      })
    }
    const leave = () => {
      pointer = null
      showHover(undefined)
    }
    // Capturing, so a box can be started before the orbit controls see the press.
    canvas.addEventListener('pointerdown', press, true)
    canvas.addEventListener('pointerup', pick)
    canvas.addEventListener('pointercancel', endBox)
    canvas.addEventListener('dblclick', open)
    canvas.addEventListener('pointermove', move)
    canvas.addEventListener('pointerleave', leave)
    sync.current()
    return () => {
      observer.disconnect()
      sized.disconnect()
      cancelAnimationFrame(animation)
      transform.dispose()
      controls.dispose()
      canvas.removeEventListener('pointerdown', press, true)
      canvas.removeEventListener('mousedown', noAutoscroll)
      canvas.removeEventListener('pointerup', pick)
      canvas.removeEventListener('pointercancel', endBox)
      canvas.removeEventListener('dblclick', open)
      area.remove()
      canvas.removeEventListener('pointermove', move)
      canvas.removeEventListener('pointerleave', leave)
      cancelAnimationFrame(hoverFrame)
      sweep.current = undefined
      for (const object of [body, edges, highlight, hover, grid, ghost, prism, chosen, outline, marked, parts, plane]) {
        object.geometry.dispose()
        object.material.dispose()
      }
      committed.geometry.dispose()
      renderer.dispose()
      canvas.remove()
      sync.current = () => {}
      view.current = undefined
      readViewFrame.current = undefined
    }
  }, [])

  useEffect(() => sync.current(), [props.shape, props.shown, props.selected, props.showEdges, props.frame, props.canPull, props.placingBody, props.transform, !props.overlay, !props.onPickEdge, !props.onSelectEdge, props.pickedEdges, props.outline, props.marked, props.chosen, props.plane, props.handles, props.toolOverlay])

  /** The handle rides along the face normal with the drag, carrying the distance beside it. */
  const follow = (distance?: number) => {
    const button = handle.current
    const label = reading.current
    if (!button || !label) return
    const offset = distance === undefined ? undefined : drag.current?.projection.clone().multiplyScalar(distance)
    button.style.marginLeft = offset ? `${offset.x}px` : ''
    button.style.marginTop = offset ? `${offset.y}px` : ''
    label.hidden = distance === undefined
    label.textContent = distance === undefined ? '' : `${distance > 0 ? '+' : ''}${distance} mm`
    if (distance === undefined) sweep.current?.stop()
    else sweep.current?.update(distance)
  }
  const end = () => {
    if (drag.current) window.removeEventListener('keydown', drag.current.escape, true)
    drag.current = null
    follow()
    bar.current?.classList.remove('invisible')
  }
  const move = (e: ReactPointerEvent<HTMLButtonElement>, commit: boolean) => {
    const active = drag.current
    if (!active || active.pointer !== e.pointerId) return
    const delta = new Vector2(e.clientX, e.clientY).sub(active.start)
    const distance = snapped(delta.dot(active.projection) / active.projection.lengthSq(), snapStep(e))
    if (commit) end()
    else follow(distance)
    props.onDrag(distance, commit)
  }

  const endHandle = () => {
    if (handleDrag.current) window.removeEventListener('keydown', handleDrag.current.escape, true)
    handleDrag.current = null
    if (handleReading.current) handleReading.current.hidden = true
  }
  const startHandle = (e: ReactPointerEvent<HTMLButtonElement>, key: string, index: number) => {
    const handle = props.handles?.find((h) => h.key === key)
    const at = handle?.at[index]
    if (e.button !== 0 || !handle || !at || !view.current) return
    const perMm = handle.axis ? view.current.screen(at.map((v, i) => v + handle.axis![i]) as Vec3).sub(view.current.screen(at)) : undefined
    if (perMm && perMm.lengthSq() < 0.01) return
    const button = e.currentTarget
    const pointer = e.pointerId
    const escape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      event.preventDefault()
      event.stopImmediatePropagation()
      endHandle()
      current.current.handles?.find((h) => h.key === key)?.onCancel()
      if (button.hasPointerCapture(pointer)) button.releasePointerCapture(pointer)
    }
    window.addEventListener('keydown', escape, true)
    handleDrag.current = { key, pointer, start: new Vector2(e.clientX, e.clientY), perMm, mmPerPx: view.current.mmPerPx(at), escape }
    button.setPointerCapture(pointer)
  }
  const moveHandle = (e: ReactPointerEvent<HTMLButtonElement>, commit: boolean) => {
    const active = handleDrag.current
    if (!active || active.pointer !== e.pointerId) return
    const delta = new Vector2(e.clientX, e.clientY).sub(active.start)
    const value = snapped(active.perMm ? delta.dot(active.perMm) / active.perMm.lengthSq() : delta.length() * active.mmPerPx, snapStep(e))
    const handle = props.handles?.find((h) => h.key === active.key)
    const label = handleReading.current
    const box = frameRef.current?.getBoundingClientRect()
    if (label && box && handle) {
      label.hidden = commit
      label.textContent = handle.reading(value)
      label.style.left = `${e.clientX - box.left + 16}px`
      label.style.top = `${e.clientY - box.top + 16}px`
    }
    if (commit) endHandle()
    handle?.onDrag(value, commit)
  }

  return (
    <div ref={frameRef} className="relative min-h-0 flex-1 overflow-hidden bg-workspace">
      <div ref={host} className="absolute inset-0" />
      {props.handles?.flatMap((h) => h.at.map((_, index) => (
        <button key={`${h.key}:${index}`} type="button" data-model-handle={h.key} data-index={index}
          aria-label={index ? undefined : h.label} aria-hidden={index ? true : undefined} tabIndex={index ? -1 : undefined} title={h.label}
          className="absolute z-10 grid size-6 -translate-x-1/2 -translate-y-1/2 touch-none place-items-center rounded-full border-2 border-white bg-accent shadow-md [cursor:grab] active:[cursor:grabbing] [&[hidden]]:hidden"
          onPointerDown={(e) => startHandle(e, h.key, index)}
          onPointerMove={(e) => moveHandle(e, false)}
          onPointerUp={(e) => moveHandle(e, true)}
          onLostPointerCapture={() => {
            if (handleDrag.current?.key !== h.key) return
            endHandle()
            h.onCancel()
          }} />
      )))}
      <span ref={handleReading} hidden role="status" aria-label="Handle reading"
        className="pointer-events-none absolute z-20 whitespace-nowrap rounded-md bg-[#1c1c22]/90 px-2 py-1 text-[11.5px] font-medium tabular-nums text-white shadow-lg" />
      <button
        hidden ref={handle} type="button" aria-label="Drag face along its normal" title="Drag face along its normal"
        className="absolute z-10 grid size-7 -translate-x-1/2 -translate-y-1/2 touch-none place-items-center rounded-full bg-accent text-accent-fg shadow-md [cursor:grab] active:[cursor:grabbing] [&[hidden]]:hidden"
        onPointerDown={(e) => {
          if (e.button !== 0 || !props.canPull || axis.current.lengthSq() < 0.01) return
          const button = e.currentTarget
          const pointer = e.pointerId
          // Escape drops the drag here and now; the capture it lets go of then finds no drag to cancel.
          const escape = (key: KeyboardEvent) => {
            if (key.key !== 'Escape') return
            key.preventDefault()
            key.stopImmediatePropagation()
            end()
            current.current.onDragCancel()
            if (button.hasPointerCapture(pointer)) button.releasePointerCapture(pointer)
          }
          window.addEventListener('keydown', escape, true)
          drag.current = { pointer, start: new Vector2(e.clientX, e.clientY), projection: axis.current.clone(), escape }
          button.setPointerCapture(pointer)
          bar.current?.classList.add('invisible')
          sweep.current?.start()
        }}
        onPointerMove={(e) => move(e, false)}
        onPointerUp={(e) => move(e, true)}
        onLostPointerCapture={() => {
          if (!drag.current) return
          end()
          props.onDragCancel()
        }}
      >
        <ArrowUpDown className="size-4" />
        <span ref={reading} hidden aria-hidden className="pointer-events-none absolute left-9 whitespace-nowrap rounded-md bg-[#1c1c22]/90 px-2 py-1 text-[11.5px] font-medium tabular-nums text-white shadow-lg" />
      </button>
      {props.overlay && <div ref={bar} className="absolute z-20" style={{ left: 8, top: 8 }}>{props.overlay}</div>}
    </div>
  )
}
