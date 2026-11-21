import type { Vec3 } from './engine-protocol'

export interface SketchPlane {
  origin: Vec3
  x_axis: Vec3
  normal: Vec3
}

export interface SketchPoint { id: string; x: number; y: number; fixed?: boolean }

export type SketchEntity = { id: string; construction?: boolean } & (
  { kind: 'line'; start: string; end: string } |
  { kind: 'circle'; center: string; radius: number } |
  { kind: 'arc'; center: string; radius: number; start_angle: number; end_angle: number } |
  { kind: 'ellipse'; center: string; major: number; minor: number; rotation: number } |
  { kind: 'spline'; points: string[]; periodic?: boolean }
)

export interface SketchConstraint { id: string; kind: string; refs: string[]; value?: number; expression?: string; driving?: boolean }
export interface SketchRecipe {
  name?: string
  plane: SketchPlane
  points: SketchPoint[]
  entities: SketchEntity[]
  constraints: SketchConstraint[]
}

export interface SketchDraftEdit {
  sketchDraft: { id: string; creating: boolean; recipe: string }
}

export interface SketchSolidEdit {
  sketchSolid: { kind: 'extrude' | 'revolve' | 'sweep' | 'loft'; profiles: string[]; inputs: string }
}

export interface SketchSolution {
  recipe: string
  message: string
  solved: boolean
}

export const basePlanes: Record<string, SketchPlane> = {
  XY: { origin: [0, 0, 0], x_axis: [1, 0, 0], normal: [0, 0, 1] },
  XZ: { origin: [0, 0, 0], x_axis: [1, 0, 0], normal: [0, -1, 0] },
  YZ: { origin: [0, 0, 0], x_axis: [0, 1, 0], normal: [1, 0, 0] }
}
