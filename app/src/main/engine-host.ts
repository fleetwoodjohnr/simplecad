// Runs the geometry engine (src/engine) in an Electron utility process, restarts it if it crashes, and hands the
// window a MessagePort to it. The same pattern as Simple Viz's engine host.

import { app, MessageChannelMain, utilityProcess, type UtilityProcess, type WebContents } from 'electron'
import { rmSync } from 'node:fs'
import { calibrationPath } from './calibration'
import path from 'node:path'

export class EngineHost {
  private child: UtilityProcess | null = null
  private stopping = false
  private crashes: number[] = []
  private ready: Promise<void> | null = null
  private restarting: ReturnType<typeof setTimeout> | undefined

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
        env: { ...process.env, ...this.env, SIMPLECAD_ENGINE_ADDON: this.addon, SIMPLECAD_PRINTER_PROFILE: calibrationPath(), SIMPLECAD_IGES_SCRATCH_FILE: igesFile }
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
        this.restarting = setTimeout(() => void this.restart(), 300)
      })
      child.once('error', (e) => reject(new Error(`The engine failed to start: ${String((e as { type?: string }).type ?? e)}`)))
    })
    return this.ready
  }

  private async restart() {
    this.restarting = undefined
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

  /** Resolves once the engine has exited, so nothing it was writing lands afterwards. A crash's pending restart is
   * dropped. An engine still running 5 s after being asked is killed outright: quitting must not wait on it. */
  stop(): Promise<void> {
    this.stopping = true
    clearTimeout(this.restarting)
    this.restarting = undefined
    const child = this.child
    if (!child) return Promise.resolve()
    return new Promise((resolve) => {
      const force = setTimeout(() => {
        this.log('error', 'The engine did not stop when asked; killing it.')
        try { if (child.pid) process.kill(child.pid, 'SIGKILL') } catch { /* already gone */ }
        resolve()
      }, 5000)
      child.once('exit', () => {
        clearTimeout(force)
        resolve()
      })
      child.kill()
    })
  }
}
