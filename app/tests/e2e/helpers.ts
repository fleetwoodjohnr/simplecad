import { _electron as electron, test as base, type ElectronApplication, type Page } from '@playwright/test'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'

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

export async function launch(opts: { env?: Record<string, string>; args?: string[]; userData?: string } = {}): Promise<Launched> {
  const userData = opts.userData ?? mkdtempSync(path.join(tmpdir(), 'scad-profile-'))
  // SCAD_PACKAGED=path/to/binary runs the specs against an electron-builder output instead of the dev build.
  const packaged = process.env.SCAD_PACKAGED
  const app = await electron.launch({
    ...(packaged ? { executablePath: packaged, args: opts.args ?? [] } : { args: ['.', ...(opts.args ?? [])] }),
    // Never the user's own recovery folder, which SimpleCAD 0.4 shares.
    env: { ...process.env, SIMPLECAD_RECOVERY_DIR: mkdtempSync(path.join(tmpdir(), 'scad-recovery-')), SIMPLECAD_LEGACY_SETTINGS: path.join(userData, 'legacy-settings.json'), ...opts.env, SIMPLECAD_USER_DATA: userData }
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
