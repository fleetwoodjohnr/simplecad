import { _electron as electron, expect, test as base, type ElectronApplication, type Page } from '@playwright/test'
import { once } from 'node:events'
import { copyFileSync, mkdtempSync, readdirSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { setTimeout as delay } from 'node:timers/promises'

const open = new Set<ElectronApplication>()
/** The profile and recovery folders launch made, removed after the test: each launch's Chromium profile is several
 * MB, and a few hundred runs fill a tmpfs /tmp. */
const scratch = new Set<string>()
export const scratchDir = (prefix: string) => {
  const dir = mkdtempSync(path.join(tmpdir(), prefix))
  scratch.add(dir)
  return dir
}

/** After every test, an app it left running is closed (killed if that hangs), as in the rest of the suite. */
export const test = base.extend<{ cleanup: void }>({
  cleanup: [
    async ({}, use) => {
      await use()
      for (const a of open) {
        let child: ReturnType<ElectronApplication['process']> | undefined
        try {
          child = a.process()
        } catch {
          /* a test that quit the app itself */
        }
        const exited = !child || child.exitCode !== null || child.signalCode !== null ? Promise.resolve() : once(child, 'exit').then(() => {})
        await Promise.race([a.evaluate(({ app }) => app.exit(0)).catch(() => {}), new Promise((r) => setTimeout(r, 5000))])
        try {
          if (child?.exitCode === null) child.kill('SIGKILL')
        } catch {
          /* already gone */
        }
        // A closing Chromium still writes to its profile: remove the folders only once it is gone.
        await Promise.race([exited, delay(5000)])
      }
      open.clear()
      for (const dir of scratch) {
        try {
          // SCAD_KEEP_SAVES=folder keeps the projects a test saved, to open them in another version.
          const keep = process.env.SCAD_KEEP_SAVES
          if (keep) for (const name of readdirSync(dir).filter((file) => file.endsWith('.scad3'))) copyFileSync(path.join(dir, name), path.join(keep, name))
          rmSync(dir, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 })
        } catch {
          /* a stray writer still holds it: tmp's own cleanup gets it */
        }
      }
      scratch.clear()
    },
    { auto: true }
  ]
})
export { expect } from '@playwright/test'

export interface Launched {
  app: ElectronApplication
  page: Page
}

/** Quit through the app's lifecycle and wait for the OS process, which can exit
 * before Electron's debug connection reports that the browser context closed. */
export async function quitCleanly(application: ElectronApplication) {
  const child = application.process()
  const timeout = new AbortController()
  try {
    const [code] = await Promise.race([
      Promise.all([
        once(child, 'exit', { signal: timeout.signal }),
        application.evaluate(({ app }) => { setImmediate(() => app.quit()) }).catch((error: Error) => {
          if (!error.message.includes('has been closed')) throw error
        })
      ]).then(([exit]) => exit),
      delay(12000, undefined, { signal: timeout.signal }).then(() => {
        throw new Error('SimpleCAD did not exit after Quit.')
      })
    ])
    if (code !== 0) throw new Error(`SimpleCAD exited with code ${code}.`)
    open.delete(application)
  } finally {
    timeout.abort()
  }
}

export async function launch(opts: { env?: Record<string, string>; args?: string[]; userData?: string } = {}): Promise<Launched> {
  const userData = opts.userData ?? scratchDir('scad-profile-')
  // SCAD_PACKAGED=path/to/binary runs the specs against an electron-builder output instead of the dev build.
  const packaged = process.env.SCAD_PACKAGED
  const args = opts.args ?? []
  const app = await electron.launch({
    ...(packaged ? { executablePath: packaged, args } : { args: ['.', ...args] }),
    // Never the user's own recovery folder, which SimpleCAD 0.4 shares.
    env: { ...process.env, SIMPLECAD_TIMING: '1', SIMPLECAD_RECOVERY_DIR: scratchDir('scad-recovery-'), SIMPLECAD_LEGACY_SETTINGS: path.join(userData, 'legacy-settings.json'), SIMPLECAD_PRINTER_PROFILE: path.join(userData, 'printer-profile.json'), ...opts.env, SIMPLECAD_USER_DATA: userData }
  })
  if (process.env.SCAD_DEBUG) {
    app.process().stdout?.on('data', (d) => process.stdout.write(`main: ${d}`))
    app.process().stderr?.on('data', (d) => process.stdout.write(`main! ${d}`))
  } else {
    // Start-up and save stages, so a slow runner shows where its time went.
    const launched = Date.now()
    app.process().stderr?.on('data', (d) => {
      for (const line of String(d).split('\n')) if (line.startsWith('[timing]')) console.log(`${line} (test +${Date.now() - launched} ms)`)
    })
  }
  open.add(app)
  const page = await app.firstWindow()
  // SCAD_TEST_SIMULATE_NO_WEBGL=1 runs as on CI's Intel macOS VM, where no WebGL context can be created; there the
  // workflow leaves out the @viewport tests. (Its reload costs the launcher's one-time project hand-off.)
  if (process.env.SCAD_TEST_SIMULATE_NO_WEBGL === '1') {
    await page.addInitScript(() => {
      const getContext = HTMLCanvasElement.prototype.getContext
      HTMLCanvasElement.prototype.getContext = function (this: HTMLCanvasElement, type: string, options?: unknown) {
        return /^webgl/.test(type) ? null : Reflect.apply(getContext, this, [type, options])
      } as typeof getContext
    })
    await page.reload()
  }
  // A CSP violation or an uncaught error in the page fails the test, as in the rest of the suite.
  page.on('pageerror', (e) => {
    throw e
  })
  page.on('console', (m) => {
    if (m.type() === 'error' && /Content Security Policy/.test(m.text())) throw new Error(m.text())
  })
  return { app, page }
}

/** The window points the viewport publishes on edges while picking them, those well inside the canvas, once they
 * and the canvas have settled: a panel opening resizes the canvas and moves every edge. */
export async function edgePoints(page: Page): Promise<[number, number][]> {
  const canvas = page.locator('canvas')
  const read = async () => {
    const box = (await canvas.boundingBox())!
    const anchors = JSON.parse((await canvas.getAttribute('data-edge-anchors')) ?? '[]') as ([number, number] | null)[]
    return anchors.filter((p): p is [number, number] => !!p && p[0] > box.x + 20 && p[0] < box.x + box.width - 20 &&
      p[1] > box.y + 20 && p[1] < box.y + box.height - 20)
  }
  let last = ''
  await expect.poll(async () => {
    const points = JSON.stringify(await read())
    const settled = points !== '[]' && points === last
    last = points
    return settled
  }, { intervals: [150] }).toBe(true)
  return JSON.parse(last)
}

/** Alerts but the 3D view's own startup error: what a step reports going wrong, also where no WebGL can start. */
export const errors = (page: Page) => page.getByRole('alert').filter({ hasNotText: 'Unable to initialize the 3D view' })
