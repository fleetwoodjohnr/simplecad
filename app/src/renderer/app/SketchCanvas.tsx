import { useEffect, useRef, useState, type PointerEvent } from 'react'
import type { SketchEntity, SketchPoint, SketchRecipe } from '@/shared/sketch'

type Position = [number, number]
export type DrawTool = 'select' | 'line' | 'rectangle' | 'circle' | 'arc' | 'ellipse' | 'spline'

const width = 600
const height = 420
const baseScale = 6
const tau = 2 * Math.PI
const needed: Record<DrawTool, number> = { select: 0, line: 2, rectangle: 2, circle: 2, arc: 3, ellipse: 3, spline: Infinity }
const distance = ([x, y]: Position, [u, v]: Position) => Math.hypot(x - u, y - v)
const coords = ({ x, y }: SketchPoint): Position => [x, y]
const identifier = () => crypto.randomUUID().replaceAll('-', '')

interface Props {
  recipe: SketchRecipe
  tool: DrawTool
  construction: boolean
  activePoint: string
  activeEntity: string
  fit: number
  finish: number
  onChange: (recipe: SketchRecipe) => void
  onPoint: (id: string) => void
  onEntity: (id: string) => void
}

export function SketchCanvas({ recipe, tool, construction, activePoint, activeEntity,
  fit, finish, onChange, onPoint, onEntity }: Props) {
  const canvas = useRef<HTMLCanvasElement>(null)
  const [picks, setPicks] = useState<Position[]>([])
  const [cursor, setCursor] = useState<Position>()
  const [view, setView] = useState(() => fitted(recipe))
  const drag = useRef<string>(undefined)
  const initialFit = useRef(fit)
  const finishBefore = useRef(finish)

  function add(profile: Position[]) {
    const points = [...recipe.points]
    const point = (position: Position) => {
      const previous = points.find((candidate) => distance(coords(candidate), position) < 1e-7)
      if (previous) return previous.id
      const id = identifier()
      points.push({ id, x: position[0], y: position[1] })
      return id
    }
    const common = { id: identifier(), construction }
    const [a, b, c] = profile
    const length = distance(a, b)
    const angle = Math.atan2(b[1] - a[1], b[0] - a[0])
    let entities: SketchEntity[]
    if (tool === 'line') {
      entities = [{ ...common, kind: 'line', start: point(a), end: point(b) }]
    } else if (tool === 'rectangle') {
      const corners = [a, [b[0], a[1]], b, [a[0], b[1]]] as Position[]
      const ids = corners.map(point)
      if (new Set(ids).size < 4) return
      entities = ids.map((start, index) => ({ ...common, id: identifier(), kind: 'line', start, end: ids[(index + 1) % 4] }))
    } else if (tool === 'circle') {
      entities = [{ ...common, kind: 'circle', center: point(a), radius: length }]
    } else if (tool === 'arc') {
      let end = Math.atan2(c[1] - a[1], c[0] - a[0])
      if (end <= angle) end += tau
      entities = [{ ...common, kind: 'arc', center: point(a), radius: length, start_angle: angle, end_angle: end }]
    } else if (tool === 'ellipse') {
      const minor = Math.abs(-(c[0] - a[0]) * Math.sin(angle) + (c[1] - a[1]) * Math.cos(angle))
      if (minor < 1e-6) return
      entities = [{ ...common, kind: 'ellipse', center: point(a), major: Math.max(length, minor),
        minor: Math.min(length, minor), rotation: angle + (minor > length ? Math.PI / 2 : 0) }]
    } else {
      entities = [{ ...common, kind: 'spline', points: profile.map(point) }]
    }
    const constraints = tool === 'rectangle' ? [...recipe.constraints, ...entities.map((entity, i) => ({
      id: identifier(), kind: i % 2 === 0 ? 'horizontal' : 'vertical', refs: [entity.id]
    }))] : recipe.constraints
    onChange({ ...recipe, points, entities: [...recipe.entities, ...entities], constraints })
    if (entities.length === 1) onEntity(entities[0].id)
  }

  useEffect(() => {
    if (initialFit.current === fit) return
    initialFit.current = fit
    setView(fitted(recipe))
  }, [fit, recipe.points])
  useEffect(() => {
    if (finishBefore.current === finish) return
    finishBefore.current = finish
    if (tool === 'spline' && picks.length > 1) add(picks)
    setPicks([])
  })

  function locate(event: PointerEvent<HTMLCanvasElement>): Position {
    const rect = event.currentTarget.getBoundingClientRect()
    const x = (event.clientX - rect.left) / rect.width * width
    const y = (event.clientY - rect.top) / rect.height * height
    return [view.center[0] + (x - width / 2) / view.scale, view.center[1] - (y - height / 2) / view.scale]
  }
  const rounded = ([x, y]: Position): Position => [Math.round(x), Math.round(y)]
  const nearest = (position: Position) => recipe.points.find((point) => distance(position, coords(point)) * view.scale <= 8)

  function down(event: PointerEvent<HTMLCanvasElement>) {
    if (event.button !== 0) return
    const position = locate(event)
    const point = nearest(position)
    if (tool === 'select') {
      onPoint(point?.id ?? '')
      if (point && !point.fixed) {
        drag.current = point.id
        event.currentTarget.setPointerCapture(event.pointerId)
      }
      return
    }
    const snapped = point ? coords(point) : rounded(position)
    if (picks.length && distance(picks[picks.length - 1], snapped) < 1e-7) return
    const next = [...picks, snapped]
    if (next.length === needed[tool]) {
      add(next)
      setPicks([])
    } else setPicks(next)
  }

  useEffect(() => {
    const context = canvas.current?.getContext('2d')
    if (!context) return
    const position = ([x, y]: Position): Position => [
      width / 2 + (x - view.center[0]) * view.scale, height / 2 - (y - view.center[1]) * view.scale
    ]
    const byId = new Map(recipe.points.map((point) => [point.id, position(coords(point))]))
    const dark = document.documentElement.classList.contains('dark')
    context.clearRect(0, 0, width, height)
    context.lineWidth = 1
    const major = view.scale * 10 ** Math.floor(Math.log10(42 / view.scale))
    const stride = major < 18 ? major * 5 : major
    const origin = position([0, 0])
    for (const axis of [0, 1] as const) {
      for (let px = ((origin[axis] % stride) + stride) % stride; px < [width, height][axis]; px += stride) {
        context.strokeStyle = dark ? '#394453' : '#d9e0ea'
        axis === 0 ? line([px, 0], [px, height]) : line([0, px], [width, px])
      }
    }
    context.strokeStyle = dark ? '#66778a' : '#9ba9b8'
    line([origin[0], 0], [origin[0], height])
    line([0, origin[1]], [width, origin[1]])
    const selected = dark ? '#72c5ff' : '#005ec5'
    for (const entity of recipe.entities) {
      context.strokeStyle = entity.id === activeEntity ? selected : dark ? '#e1ecf9' : '#202d3e'
      context.lineWidth = entity.id === activeEntity ? 2.5 : 1.8
      context.setLineDash(entity.construction ? [6, 3] : [])
      if (entity.kind === 'line') {
        const a = byId.get(entity.start), b = byId.get(entity.end)
        if (a && b) line(a, b)
      } else if (entity.kind === 'spline') {
        const points = entity.points.flatMap((id) => byId.get(id) ? [byId.get(id)!] : [])
        for (let i = 1; i < points.length; i++) line(points[i - 1], points[i])
      } else {
        const center = byId.get(entity.center)
        if (!center) continue
        context.beginPath()
        if (entity.kind === 'ellipse') {
          context.ellipse(...center, Math.abs(entity.major) * view.scale, Math.abs(entity.minor) * view.scale, -entity.rotation, 0, tau)
        } else {
          const start = entity.kind === 'arc' ? -entity.start_angle : 0
          const end = entity.kind === 'arc' ? -entity.end_angle : tau
          context.arc(...center, Math.abs(entity.radius) * view.scale, start, end, entity.kind === 'arc')
        }
        context.stroke()
      }
    }
    context.setLineDash([])
    for (const point of recipe.points) {
      context.fillStyle = point.id === activePoint ? selected : point.fixed ? '#db8e15' : dark ? '#aabece' : '#556576'
      context.beginPath()
      context.arc(...position(coords(point)), point.id === activePoint ? 4.5 : 3, 0, tau)
      context.fill()
    }
    if (picks.length && cursor) {
      context.strokeStyle = selected
      context.lineWidth = 1
      const segments = [...picks, cursor].map(position)
      for (let i = 1; i < segments.length; i++) line(segments[i - 1], segments[i])
    }

    function line(a: Position, b: Position) {
      if (!context) return
      context.beginPath()
      context.moveTo(...a)
      context.lineTo(...b)
      context.stroke()
    }
  }, [recipe, activePoint, activeEntity, view, cursor, picks])

  return <canvas ref={canvas} width={width} height={height} aria-label="Sketch drawing"
    className={'block aspect-[10/7] w-full touch-none rounded border border-border bg-bg ' + (tool === 'select' ? 'cursor-default' : 'cursor-crosshair')}
    onPointerDown={down} onPointerMove={(event) => {
      const position = locate(event)
      if (drag.current) {
        const [x, y] = rounded(position)
        const points = recipe.points.map((point) => point.id === drag.current ? { ...point, x, y } : point)
        onChange({ ...recipe, points })
      } else if (tool !== 'select') setCursor(rounded(position))
    }}
    onPointerUp={() => { drag.current = undefined }}
    onLostPointerCapture={() => { drag.current = undefined }}
    onPointerLeave={() => setCursor(undefined)}
    onWheel={(event) => setView((previous) => ({ ...previous,
      scale: Math.max(0.05, Math.min(100, previous.scale * (event.deltaY > 0 ? 0.85 : 1.17))) }))} />
}

function fitted({ points, entities }: SketchRecipe): { center: Position; scale: number } {
  if (points.length === 0) return { center: [0, 0], scale: baseScale }
  let left = 0, right = 0, top = 0, bottom = 0
  for (const { x, y } of points) {
    left = Math.min(left, x); right = Math.max(right, x)
    bottom = Math.min(bottom, y); top = Math.max(top, y)
  }
  const byId = new Map(points.map((point) => [point.id, point]))
  for (const entity of entities) {
    if (entity.kind === 'line' || entity.kind === 'spline') continue
    const center = byId.get(entity.center)
    if (!center) continue
    const radius = Math.abs(entity.kind === 'ellipse' ? Math.max(entity.major, entity.minor) : entity.radius)
    left = Math.min(left, center.x - radius)
    right = Math.max(right, center.x + radius)
    bottom = Math.min(bottom, center.y - radius)
    top = Math.max(top, center.y + radius)
  }
  const scale = Math.min(baseScale, (width - 60) / Math.max(1, right - left), (height - 60) / Math.max(1, top - bottom))
  return { center: [(left + right) / 2, (bottom + top) / 2], scale }
}
