import { checkRendererRequest, isCheckpoint, type EngineResponse } from '../shared/engine-protocol'
import { EngineDocument, type Shape } from './document'

interface PortLike {
  on(ev: 'message', cb: (e: { data: unknown; ports: PortLike[] }) => void): void
  postMessage(message: EngineResponse): void
  start(): void
  close(): void
}

interface Addon {
  readProject(data: Buffer): string
  writeProject(model: string): Buffer
  Shape: { makeBox(dx: number, dy: number, dz: number): Shape; recessedWallExample(): Shape }
}

const parent = (process as unknown as { parentPort: PortLike }).parentPort
const addon = require(process.env.SIMPLECAD_ENGINE_ADDON!) as Addon
const document = new EngineDocument((starter) => starter === 'box'
  ? addon.Shape.makeBox(30, 20, 10)
  : addon.Shape.recessedWallExample())
let port: PortLike | null = null

function onRenderer(channel: PortLike, data: unknown) {
  let id = -1
  try {
    const req = checkRendererRequest(data)
    id = req.id
    let result
    switch (req.op) {
      case 'model': result = document.model(); break
      case 'reset': result = document.reset(req.starter, req.revision); break
      case 'restore': result = document.restore(req.checkpoint); break
      case 'open': {
        const data = JSON.parse(addon.readProject(Buffer.from(req.data.buffer, req.data.byteOffset, req.data.byteLength)))
        if (!data || data.format !== 5 || data.kernel !== 'rectilinear-v1' || !isCheckpoint(data.checkpoint)) {
          throw new Error('This build can open only projects saved by the native Rust preview.')
        }
        result = document.open(data.checkpoint, req.revision)
        break
      }
      case 'save':
        result = addon.writeProject(JSON.stringify({
          format: 5, kernel: 'rectilinear-v1', checkpoint: document.save(req.revision)
        }))
        break
      case 'preview': result = document.preview(req, req.revision); break
      case 'apply': result = document.apply(req, req.revision); break
      case 'undo': result = document.step(-1, req.revision); break
      case 'redo': result = document.step(1, req.revision); break
      case 'crash':
        if (process.env.SIMPLECAD_ENGINE_DRILL !== '1') throw new Error('Not available')
        process.exit(1)
    }
    channel.postMessage({ id, ok: true, result })
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
