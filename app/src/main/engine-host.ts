// Runs the geometry engine (src/engine) in an Electron utility process, restarts it if it crashes, and hands the
// window a MessagePort to it. The same pattern as Simple Viz's engine host.

import { app, MessageChannelMain, utilityProcess, type UtilityProcess, type WebContents } from 'electron'
import { rmSync } from 'node:fs'
import path from 'node:path'

export class EngineHost {
  private child: UtilityProcess | null = null
  private stopping = false
  private crashes: number[] = []
  private ready: Promise<void> | null = null

  constructor(
    private addon: string,
    private web: () => WebContents | null,
    /** More environment for the engine: where it autosaves, and how often. */
    private env: Record<string, string> = {},
    private log: (level: string, message: string) => void = (l, m) => console[l === 'error' ? 'error' : 'log'](`[engine] ${m}`)
  ) {}

  start(): Promise<void> {
    if (this.ready) return this.ready
    this.stopping = false
    this.ready = new Promise((resolve, reject) => {
      const igesFile = path.join(app.getPath('userData'), 'engine', 'import.iges')
      const discardImport = () => {
        try { rmSync(igesFile, { force: true }) } catch { this.log('error', 'Could not remove the temporary IGES file.') }
      }
      discardImport()
      const child = utilityProcess.fork(path.join(__dirname, 'engine.js'), [], {
        serviceName: 'SimpleCAD geometry engine',
        stdio: 'inherit',
        env: { ...process.env, ...this.env, SIMPLECAD_ENGINE_ADDON: this.addon, SIMPLECAD_IGES_SCRATCH_FILE: igesFile }
      })
      this.child = child
      child.once('spawn', () => resolve())
      child.on('exit', (code) => {
        discardImport()
        this.child = null
        this.ready = null
        if (this.stopping) return
        this.log('error', `Engine exited with code ${code}`)
        this.crashes.push(Date.now())
        this.crashes = this.crashes.filter((t) => Date.now() - t < 60_000)
        if (this.crashes.length > 3) return this.log('error', 'Engine keeps crashing; not restarting.')
        setTimeout(() => void this.restart(), 300)
      })
      child.once('error', (e) => reject(new Error(`The engine failed to start: ${String((e as { type?: string }).type ?? e)}`)))
    })
    return this.ready
  }

  private async restart() {
    await this.start()
    this.web()?.send('engine:restart')
    this.connectRenderer()
  }

  /** A fresh port for the window; the old one (if any) stops working. */
  connectRenderer() {
    const wc = this.web()
    if (!wc || !this.child) return
    const { port1, port2 } = new MessageChannelMain()
    this.child.postMessage({ type: 'port' }, [port1])
    wc.postMessage('engine:port', null, [port2])
  }

  /** Resolves once the engine has exited, so nothing it was writing lands afterwards. */
  stop(): Promise<void> {
    this.stopping = true
    const child = this.child
    if (!child) return Promise.resolve()
    return new Promise((resolve) => {
      child.once('exit', () => resolve())
      child.kill()
    })
  }
}
