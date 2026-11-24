import { EventEmitter } from 'node:events'
import os from 'node:os'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

/** A utility process that spawns at once, and exits on kill() unless told to ignore it. */
class FakeChild extends EventEmitter {
  pid: number | undefined = 4242
  ignoresKill = false
  kill() {
    if (!this.ignoresKill) this.exit(0)
    return true
  }
  exit(code: number) {
    this.pid = undefined
    this.emit('exit', code)
  }
  postMessage() {}
}
const forked: FakeChild[] = []
let nextIgnoresKill = false

vi.mock('electron', () => ({
  app: { getPath: () => os.tmpdir() },
  MessageChannelMain: class {},
  utilityProcess: {
    fork: () => {
      const child = new FakeChild()
      child.ignoresKill = nextIgnoresKill
      forked.push(child)
      queueMicrotask(() => child.emit('spawn'))
      return child
    }
  }
}))
vi.mock('../../../src/main/calibration', () => ({ calibrationPath: () => '/nonexistent/printer.json' }))

const { EngineHost } = await import('../../../src/main/engine-host')
const host = () => new EngineHost('addon.node', () => null, {}, () => {})

describe('EngineHost.stop', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    forked.length = 0
    nextIgnoresKill = false
  })
  afterEach(() => vi.useRealTimers())

  it('drops the restart a crash scheduled, so a stopped engine stays stopped', async () => {
    const engine = host()
    await engine.start()
    forked[0].exit(139)
    await engine.stop()
    await vi.advanceTimersByTimeAsync(1000)
    expect(forked).toHaveLength(1)
  })

  it('waits for an engine that exits when asked, without killing it outright', async () => {
    const kill = vi.spyOn(process, 'kill').mockImplementation(() => true)
    const engine = host()
    await engine.start()
    await engine.stop()
    expect(kill).not.toHaveBeenCalled()
    kill.mockRestore()
  })

  it('kills an engine that ignores the request after 5 s, and resolves', async () => {
    const kill = vi.spyOn(process, 'kill').mockImplementation(() => true)
    nextIgnoresKill = true
    const engine = host()
    await engine.start()
    let stopped = false
    void engine.stop().then(() => { stopped = true })
    await vi.advanceTimersByTimeAsync(4900)
    expect(stopped).toBe(false)
    await vi.advanceTimersByTimeAsync(200)
    expect(stopped).toBe(true)
    expect(kill).toHaveBeenCalledWith(4242, 'SIGKILL')
    kill.mockRestore()
  })

  it('starts again after a stop, as reopening a window on macOS does', async () => {
    const engine = host()
    await engine.start()
    await engine.stop()
    await engine.start()
    expect(forked).toHaveLength(2)
  })
})
