import { checkRendererRequest, type EngineResponse } from '../shared/engine-protocol'
import { EngineDocument, type Shape } from './document'
import { readProject } from './project'

interface PortLike {
  on(ev: 'message', cb: (e: { data: unknown; ports: PortLike[] }) => void): void
  postMessage(message: EngineResponse): void
  start(): void
  close(): void
}

interface Addon {
  readArchive(data: Buffer, names: string[]): (Buffer | null)[]
  crash(): void
  Shape: { makeBox(dx: number, dy: number, dz: number): Shape; fromBrep(data: Buffer): Shape }
}

const parent = (process as unknown as { parentPort: PortLike }).parentPort
const addon = require(process.env.SIMPLECAD_ENGINE_ADDON!) as Addon
const buffer = (data: Uint8Array) => Buffer.from(data.buffer, data.byteOffset, data.byteLength)
const document = new EngineDocument(
  () => [{ name: 'Box', shape: addon.Shape.makeBox(30, 20, 10) }],
  (data) => {
    const { bodies, notes } = readProject(addon, buffer(data))
    return { bodies: bodies.map(({ name, brep }) => ({ name, shape: addon.Shape.fromBrep(brep) })), notes }
  }
)
let port: PortLike | null = null

function onRenderer(channel: PortLike, data: unknown) {
  let id = -1
  try {
    const req = checkRendererRequest(data)
    id = req.id
    let result
    switch (req.op) {
      case 'model': result = document.model(); break
      case 'reset': result = document.reset(req.revision); break
      case 'restore': result = document.restore(req.checkpoint, req.data); break
      case 'open': result = document.open(req.data, req.name, req.revision); break
      case 'preview': result = document.preview(req, req.revision); break
      case 'apply': result = document.apply(req, req.revision); break
      case 'undo': result = document.step(-1, req.revision); break
      case 'redo': result = document.step(1, req.revision); break
      case 'crash':
        if (process.env.SIMPLECAD_ENGINE_DRILL !== '1') throw new Error('Not available')
        // A real segfault inside the OCCT shim, not an orderly exit.
        addon.crash()
    }
    channel.postMessage({ id, ok: true, result: result! })
  } catch (err) {
    channel.postMessage({ id, ok: false, error: (err as Error).message })
  }
}

parent.on('message', (e) => {
  const m = e.data as { type?: string }
  if (m?.type !== 'port') return
  port?.close()
  const channel = e.ports[0]
  port = channel
  channel.on('message', (ev) => onRenderer(channel, ev.data))
  channel.start()
})
