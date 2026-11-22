import { mkdtemp, rm } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { expect, launch, test } from './helpers'

test('patterns a body, edits the count from History in one step, and keeps it through restart and reopen', async () => {
  const scratch = await mkdtemp(path.join(os.tmpdir(), 'simplecad-pattern-'))
  const target = path.join(scratch, 'Pattern.scad3')
  try {
    const { app, page } = await launch({ env: { SIMPLECAD_ENGINE_DRILL: '1' } })
    const toolbar = page.getByRole('toolbar', { name: 'Modeling tools' })
    const status = page.getByTestId('engine-status')
    const steps = page.getByRole('list', { name: 'Model history' }).locator('li')
    await expect(status).toHaveText(/24000 mm³/)

    await toolbar.getByRole('button', { name: 'Pattern' }).click()
    await page.getByRole('menuitem', { name: 'Rectangular Pattern' }).click()
    const form = page.getByRole('form', { name: 'Rectangular Pattern' })
    await expect(form.getByLabel('Body')).toHaveValue('0')
    await form.getByLabel('Count along X').fill('3')
    await form.getByLabel('Spacing X').fill('5 cm')
    await form.getByRole('button', { name: 'Preview' }).click()
    await expect(page.getByRole('alert')).toHaveCount(0)
    await expect(steps).toHaveText(['Box'])
    await form.getByRole('button', { name: 'Apply' }).click()
    await expect(steps).toHaveText(['Box', 'Rectangular Pattern'])
    await expect(status).toHaveText(/72000 mm³/)

    await page.getByRole('button', { name: 'Edit Rectangular Pattern' }).click()
    const saved = page.getByRole('form', { name: 'Rectangular Pattern' })
    await expect(saved.getByLabel('Spacing X')).toHaveValue('5 cm')
    await saved.getByLabel('Count along X').fill('2')
    await saved.getByRole('button', { name: 'Apply' }).click()
    await expect(status).toHaveText(/48000 mm³/)
    await expect(steps).toHaveText(['Box', 'Rectangular Pattern'])
    await toolbar.getByRole('button', { name: 'Undo', exact: true }).click()
    await expect(status).toHaveText(/72000 mm³/)
    await toolbar.getByRole('button', { name: 'Redo', exact: true }).click()
    await expect(status).toHaveText(/48000 mm³/)

    await page.evaluate(() =>
      (window as unknown as { simplecadDrill: { crashEngine(): Promise<unknown> } }).simplecadDrill.crashEngine().catch(() => {}))
    await expect(page.getByTestId('engine-connections')).toHaveText('2')
    await expect(status).toHaveText(/Engine ready · 48000 mm³/)
    await app.evaluate(({ dialog }, filePath) => {
      dialog.showSaveDialog = async () => ({ filePath, canceled: false })
    }, target)
    await page.getByRole('button', { name: 'File', exact: true }).click()
    await page.getByRole('menuitem', { name: /^Save As/ }).click()
    await expect(page.getByText('Pattern.scad3', { exact: true })).toBeVisible()
    await app.close()

    const reopened = await launch({ args: [target] })
    await expect(reopened.page.getByRole('list', { name: 'Model history' }).locator('li')).toHaveText(['Box', 'Rectangular Pattern'])
    await expect(reopened.page.getByTestId('engine-status')).toHaveText(/48000 mm³/)
    await reopened.page.getByRole('button', { name: 'Undo', exact: true }).click()
    await expect(reopened.page.getByTestId('engine-status')).toHaveText(/72000 mm³/)
  } finally {
    await rm(scratch, { recursive: true, force: true })
  }
})

test('mirrors and scales a body as single undoable steps', async () => {
  const { page } = await launch()
  const toolbar = page.getByRole('toolbar', { name: 'Modeling tools' })
  const status = page.getByTestId('engine-status')
  const steps = page.getByRole('list', { name: 'Model history' }).locator('li')
  await expect(status).toHaveText(/24000 mm³/)

  await toolbar.getByRole('button', { name: 'Mirror', exact: true }).click()
  const mirror = page.getByRole('form', { name: 'Mirror' })
  await expect(mirror.getByLabel('Keep the original')).toBeChecked()
  await mirror.getByRole('button', { name: 'Apply' }).click()
  await expect(steps).toHaveText(['Box', 'Mirror'])
  await expect(status).toHaveText(/48000 mm³/)

  await toolbar.getByRole('button', { name: 'Scale', exact: true }).click()
  const scale = page.getByRole('form', { name: 'Scale' })
  await scale.getByLabel('Factor').fill('1 / 2')
  await scale.getByRole('button', { name: 'Apply' }).click()
  await expect(steps).toHaveText(['Box', 'Mirror', 'Scale'])
  await expect(status).toHaveText(/6000 mm³/)
  await toolbar.getByRole('button', { name: 'Undo', exact: true }).click()
  await expect(status).toHaveText(/48000 mm³/)
})
