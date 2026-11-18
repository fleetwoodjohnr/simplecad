export interface MeshData {
  positions: Float32Array
  normals: Float32Array
  indices: Uint32Array
  faceTriStart: Uint32Array
  edgePoints: Float32Array
  edgeStart: Uint32Array
}

export interface FaceInfo {
  center: [number, number, number]
  normal: [number, number, number]
  area: number
}

export interface DisplayShape extends MeshData {
  volume: number
  faces: FaceInfo[]
}

export type Starter = 'box' | 'recessed-wall'
export interface PullEdit { face: number; distance: number }

export interface Checkpoint {
  starter: Starter
  edits: PullEdit[]
  cursor: number
}

export interface ModelResult {
  shape: DisplayShape
  revision: number
  checkpoint: Checkpoint
}

export type RendererCommand =
  | { op: 'model' | 'crash' }
  | { op: 'reset'; starter: Starter; revision: number }
  | { op: 'restore'; checkpoint: Checkpoint }
  | { op: 'open'; data: Uint8Array; revision: number }
  | { op: 'save'; revision: number }
  | ({ op: 'preview' | 'apply'; revision: number } & PullEdit)
  | { op: 'undo' | 'redo'; revision: number }

export type RendererRequest = RendererCommand & { id: number }
export type EngineResponse =
  | { id: number; ok: true; result: ModelResult | DisplayShape | Uint8Array }
  | { id: number; ok: false; error: string }

export const MAX_EDITS = 100
export const MAX_PROJECT_BYTES = 128 * 1024 * 1024

function isIndex(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0
}

function isStarter(value: unknown): value is Starter {
  return value === 'box' || value === 'recessed-wall'
}

function isEdit(value: unknown): value is PullEdit {
  if (!value || typeof value !== 'object') return false
  const edit = value as Partial<PullEdit>
  return isIndex(edit.face) && edit.face <= 0xffffffff &&
    typeof edit.distance === 'number' && Number.isFinite(edit.distance) && Math.abs(edit.distance) <= 1e9
}

export function isCheckpoint(value: unknown): value is Checkpoint {
  if (!value || typeof value !== 'object') return false
  const model = value as Partial<Checkpoint>
  return isStarter(model.starter) && Array.isArray(model.edits) && model.edits.length <= MAX_EDITS &&
    model.edits.every(isEdit) && isIndex(model.cursor) && model.cursor <= model.edits.length
}

export function checkRendererRequest(data: unknown): RendererRequest {
  const m = data as Record<string, unknown> | null
  if (!m || typeof m !== 'object' || !isIndex(m.id)) throw new Error('Malformed engine request.')
  if (m.op === 'model' || m.op === 'crash' ||
      (m.op === 'restore' && isCheckpoint(m.checkpoint)) ||
      (isIndex(m.revision) && (
        m.op === 'undo' || m.op === 'redo' || m.op === 'save' ||
        (m.op === 'open' && m.data instanceof Uint8Array && m.data.byteLength <= MAX_PROJECT_BYTES) ||
        (m.op === 'reset' && isStarter(m.starter)) ||
        ((m.op === 'preview' || m.op === 'apply') && isEdit(m))
      ))) return m as RendererRequest
  throw new Error('Invalid geometry command or arguments.')
}
