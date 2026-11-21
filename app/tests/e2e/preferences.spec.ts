import os from 'node:os'
import path from 'node:path'
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { expect, launch, test } from './helpers'

test('migrates existing settings and persists changes without writing the rollback file', async () => {
  const scratch = await mkdtemp(path.join(os.tmpdir(), 'simplecad-preferences-'))
  const userData = path.join(scratch, 'Electron')
  const legacy = path.join(scratch, 'settings.json')
  const original = JSON.stringify({
    theme: 'dark', display_unit: 'cm', view: { snap: true },
    modelling: { move_step: 2.5, rotation_snap: 30 },
    recent: ['/path/to/model.scad3'], extra: { preserve: true }
  })
  const options = { userData, env: { SIMPLECAD_LEGACY_SETTINGS: legacy } }
  try {
    await mkdir(userData)
    await writeFile(legacy, original)
    const { app, page } = await launch(options)
    await expect(page.getByTestId('engine-status')).toContainText('24 cm³')
    await expect(page.locator('html')).toHaveClass(/dark/)
    const toolbar = page.getByRole('toolbar', { name: 'Modeling tools' })
    await toolbar.getByRole('button', { name: 'Move', exact: true }).click()
    await expect(page.getByLabel('Drag snapping')).toHaveValue('2.5')
    await page.getByRole('button', { name: 'Cancel', exact: true }).click()

    const file = toolbar.getByRole('button', { name: 'File', exact: true })
    await file.click()
    await page.getByRole('menuitem', { name: 'Settings', exact: true }).click()
    const dialog = page.getByRole('dialog', { name: 'Settings' })
    await expect(dialog.getByLabel('Snap when dragging')).toBeChecked()
    await expect(dialog.getByLabel('Rotation increment')).toHaveValue('30')
    await dialog.getByLabel('Theme', { exact: true }).selectOption('light')
    await dialog.getByLabel('Display units').selectOption('mm')
    await dialog.getByLabel('Move increment').fill('7.25')
    await dialog.getByRole('button', { name: 'Apply' }).click()
    await expect(page.getByTestId('engine-status')).toContainText('24000 mm³')
    await expect(page.locator('html')).not.toHaveClass(/dark/)
    await app.close()
    expect(await readFile(legacy, 'utf8')).toBe(original)

    const restarted = await launch(options)
    await expect(restarted.page.getByTestId('engine-status')).toContainText('24000 mm³')
    await expect(restarted.page.locator('html')).not.toHaveClass(/dark/)
    await restarted.page.getByRole('button', { name: 'Move', exact: true }).click()
    await expect(restarted.page.getByLabel('Drag snapping')).toHaveValue('7.25')
    await restarted.app.close()
    expect(await readFile(legacy, 'utf8')).toBe(original)
  } finally {
    await rm(scratch, { recursive: true, force: true })
  }
})
