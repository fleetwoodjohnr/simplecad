import { _electron as electron, expect, test as base, type ElectronApplication, type Page } from '@playwright/test'
import { once } from 'node:events'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { setTimeout as delay } from 'node:timers/promises'

const open = new Set<ElectronApplication>()

/** After every test, an app it left running is closed (killed if that hangs), as in the rest of the suite. */
export const test = base.extend<{ cleanup: void }>({
  cleanup: [
    async ({}, use) => {
      await use()
      for (const a of open) {
        await Promise.race([a.evaluate(({ app }) => app.exit(0)).catch(() => {}), new Promise((r) => setTimeout(r, 5000))])
        try {
          if (a.process().exitCode === null) a.process().kill('SIGKILL')
        } catch {
          /* already gone */
        }
      }
      open.clear()
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
  const userData = opts.userData ?? mkdtempSync(path.join(tmpdir(), 'scad-profile-'))
  // SCAD_PACKAGED=path/to/binary runs the specs against an electron-builder output instead of the dev build.
  const packaged = process.env.SCAD_PACKAGED
  const software = process.env.SCAD_TEST_SOFTWARE_WEBGL === '1' ? ['--use-gl=angle', '--use-angle=swiftshader'] : []
  const args = [...software, ...(opts.args ?? [])]
  const app = await electron.launch({
    ...(packaged ? { executablePath: packaged, args } : { args: ['.', ...args] }),
    // Never the user's own recovery folder, which SimpleCAD 0.4 shares.
    env: { ...process.env, SIMPLECAD_RECOVERY_DIR: mkdtempSync(path.join(tmpdir(), 'scad-recovery-')), SIMPLECAD_LEGACY_SETTINGS: path.join(userData, 'legacy-settings.json'), SIMPLECAD_PRINTER_PROFILE: path.join(userData, 'printer-profile.json'), ...opts.env, SIMPLECAD_USER_DATA: userData }
  })
  if (process.env.SCAD_DEBUG) {
    app.process().stdout?.on('data', (d) => process.stdout.write(`main: ${d}`))
    app.process().stderr?.on('data', (d) => process.stdout.write(`main! ${d}`))
  }
  open.add(app)
  const page = await app.firstWindow()
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
