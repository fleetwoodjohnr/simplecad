import { _electron as electron } from '@playwright/test'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'

/** One launch before the suite. On a fresh CI runner the first start pays for the system scanning and caching the
 * app and its geometry engine: in the 0.5.1 dry run the first macOS start took 2.9 s to load the engine against a
 * median of 0.6 s, and its first answer came at 3.9 s, close to the first test's 5 s wait. */
export default async function warm() {
  const profile = mkdtempSync(path.join(tmpdir(), 'scad-warm-'))
  const packaged = process.env.SCAD_PACKAGED
  const app = await electron.launch({
    ...(packaged ? { executablePath: packaged, args: [] } : { args: ['.'] }),
    // Never the user's own recovery folder or settings, as in every test.
    env: { ...process.env, SIMPLECAD_USER_DATA: profile, SIMPLECAD_RECOVERY_DIR: path.join(profile, 'recovery'),
      SIMPLECAD_LEGACY_SETTINGS: path.join(profile, 'legacy-settings.json'), SIMPLECAD_PRINTER_PROFILE: path.join(profile, 'printer-profile.json') }
  })
  try {
    const page = await app.firstWindow()
    await page.getByTestId('engine-status').filter({ hasText: 'Engine ready' }).waitFor({ timeout: 60_000 })
  } finally {
    await app.close()
    rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 })
  }
}
