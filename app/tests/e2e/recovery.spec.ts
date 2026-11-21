import { execFile } from 'node:child_process'
import { existsSync, mkdtempSync, readdirSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { promisify } from 'node:util'
import type { ElectronApplication, Page } from '@playwright/test'
import { expect, launch, test } from './helpers'

async function pullTop(page: Page, distance: string) {
  await page.getByLabel('Planar face').selectOption({ label: 'Top · 1200 mm²' })
  const field = page.getByLabel('Pull distance')
  await field.fill(distance)
  await field.press('Enter')
}

async function closeWindow(app: ElectronApplication, page: Page) {
  const closed = process.platform === 'darwin' ? page.waitForEvent('close') : app.waitForEvent('close')
  await page.evaluate(() => { void window.simplecad.windowClose() })
  await closed
}

test('unsaved work is autosaved, and offered back after a crash', async () => {
  const recovery = mkdtempSync(path.join(tmpdir(), 'scad-recovery-'))
  const env = { SIMPLECAD_RECOVERY_DIR: recovery, SIMPLECAD_AUTOSAVE_SECONDS: '1' }
  const first = await launch({ env })
  const status = first.page.getByTestId('engine-status')
  await expect(status).toHaveText(/24000 mm³/)
  await pullTop(first.page, '-2')
  await expect(status).toHaveText(/21600 mm³/)
  await expect.poll(() => readdirSync(recovery).filter((f) => f.endsWith('.scad3')).length, { timeout: 10_000 }).toBe(1)
  const child = first.app.process()
  if (process.platform === 'win32') {
    // Chromium's children can keep the Playwright transport open after the
    // Windows main process dies. Terminate this launch's entire process tree.
    await promisify(execFile)('taskkill.exe', ['/PID', String(child.pid), '/T', '/F'])
  } else {
    expect(child.kill('SIGKILL')).toBe(true)
  }
  await expect.poll(() => child.exitCode !== null || child.signalCode !== null).toBe(true)

  const { app, page } = await launch({ env })
  const offer = page.getByRole('region', { name: 'Unsaved work from last time' })
  await expect(offer).toContainText('less than a minute ago')
  await offer.getByRole('button', { name: 'Recover' }).click()
  await expect(page.getByTestId('engine-status')).toHaveText(/Engine ready · 21600 mm³/)
  await expect(page.getByText('Recovered work •')).toBeVisible()
  await expect(page.getByRole('list', { name: 'Model history' }).locator('li')).toHaveText(['Box', 'Pull'])
  await expect(offer).toBeHidden()

  // Closed without saving, on purpose: a clean exit, so no work is left to offer.
  await app.evaluate(({ dialog }) => { dialog.showMessageBoxSync = () => 1 })
  await closeWindow(app, page)
  await expect.poll(() => readdirSync(recovery)).toEqual([])
})

test('closing with unsaved work asks: Cancel keeps the window, Save saves and then closes', async () => {
  const { app, page } = await launch()
  await expect(page.getByTestId('engine-status')).toHaveText(/24000 mm³/)
  await pullTop(page, '5')
  await expect(page.getByText('Untitled •')).toBeVisible()

  await app.evaluate(({ dialog }) => { dialog.showMessageBoxSync = () => 2 })
  await page.evaluate(() => window.simplecad.windowClose())
  await page.waitForTimeout(500)
  expect(await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().length)).toBe(1)

  const target = await app.evaluate(({ app: electron, dialog }) => {
    const file = `${electron.getPath('userData')}/closing.scad3`
    dialog.showMessageBoxSync = () => 0
    dialog.showSaveDialog = async () => ({ filePath: file, canceled: false })
    return file
  })
  await closeWindow(app, page)
  expect(existsSync(target)).toBe(true)
})

test('explicit Quit cleans a discarded autosave before the application exits', async () => {
  const recovery = mkdtempSync(path.join(tmpdir(), 'scad-recovery-'))
  const { app, page } = await launch({ env: { SIMPLECAD_RECOVERY_DIR: recovery, SIMPLECAD_AUTOSAVE_SECONDS: '1' } })
  const status = page.getByTestId('engine-status')
  await expect(status).toHaveText(/24000 mm³/)
  await pullTop(page, '-2')
  await expect(status).toHaveText(/21600 mm³/)
  await expect.poll(() => readdirSync(recovery).filter((f) => f.endsWith('.scad3')).length).toBe(1)
  const closed = app.waitForEvent('close')
  await app.evaluate(({ app: electron, dialog }) => {
    dialog.showMessageBoxSync = () => 1
    setImmediate(() => electron.quit())
  })
  await closed
  expect(readdirSync(recovery)).toEqual([])
})
