import path from 'node:path'
import type { Page } from '@playwright/test'
import { edgePoints, errors, expect, launch, scratchDir, test } from './helpers'

/** Two small cylinders beside the 40 × 30 × 20 box, chosen in the Bodies list, and Arrange opened on them (as a row, which
 * needs three). */
async function twoCylinders(page: Page) {
  const toolbar = page.getByRole('toolbar', { name: 'Modeling tools' })
  await expect(page.getByTestId('engine-status')).toHaveText(/24000 mm³/)
  for (let count = 0; count < 2; count++) {
    await toolbar.getByRole('button', { name: 'Shape', exact: true }).click()
    const shape = page.getByRole('form', { name: 'Create shape' })
    await shape.getByLabel('Shape type').selectOption('cylinder')
    await shape.getByLabel('Radius').fill('4')
    await shape.getByLabel('Height').fill('6')
    await shape.getByRole('button', { name: 'Create', exact: true }).click()
  }
  const bodies = page.getByRole('list', { name: 'Bodies', exact: true })
  await bodies.getByRole('button', { name: 'Cylinder', exact: true }).click()
  await bodies.getByRole('button', { name: 'Cylinder2', exact: true }).click({ modifiers: ['Control'] })
  await toolbar.getByRole('button', { name: 'Arrange', exact: true }).click()
  return page.getByRole('form', { name: 'Arrange' })
}

test('lays two cylinders out in a grid on the box top, takes a suggestion, edits it from History, and keeps it through reopen', async () => {
  const { app, page } = await launch()
  const toolbar = page.getByRole('toolbar', { name: 'Modeling tools' })
  const steps = page.getByRole('list', { name: 'Model history' }).locator('li')
  const arrange = await twoCylinders(page)
  await arrange.getByRole('combobox', { name: 'Pattern', exact: true }).selectOption('grid')
  await expect(arrange.getByRole('status', { name: 'Arranged bodies' })).toHaveText('Cylinder, Cylinder2')
  await expect(arrange.getByRole('status', { name: 'Arrange target' })).toHaveText('Click the face to arrange the objects on, or use the build plate.')
  await page.getByLabel('Planar face').selectOption({ label: 'Box · Top · 1200 mm²' })
  await expect(arrange.getByRole('status', { name: 'Arrange target' })).toHaveText('On Box · Top · 1200 mm².')

  // A fixed 40 mm gap cannot fit on a 40 × 30 face: the preview says so, and offers what would.
  await arrange.getByRole('radio', { name: 'Fixed', exact: true }).click()
  await arrange.getByLabel('Spacing', { exact: true }).fill('40')
  await arrange.getByRole('button', { name: 'Preview' }).click()
  const fit = arrange.getByRole('status', { name: 'Arrangement fit' })
  await expect(fit).toHaveText(/outside the boundary|collide/)
  const offer = arrange.getByRole('button', { name: /^Try: / }).first()
  await expect(offer).toBeVisible()
  await offer.click()
  await expect(fit).toHaveText(/^2 objects · /)
  await expect(errors(page)).toHaveCount(0)
  await arrange.getByRole('button', { name: 'Apply' }).click()
  await expect(steps).toHaveText(['Box', 'Cylinder', 'Cylinder2', 'Arrange'])
  await expect(errors(page)).toHaveCount(0)
  const placed = (await page.getByTestId('engine-status').textContent())!

  // History edits the pattern whole; its target face stays as picked.
  await page.getByRole('button', { name: 'Edit Arrange' }).click()
  const saved = page.getByRole('form', { name: 'Arrange' })
  await expect(saved.getByRole('combobox', { name: 'Pattern', exact: true })).toHaveValue('grid')
  await expect(saved.getByRole('status', { name: 'Arrange target' })).toHaveText('On a face of Box.')
  await expect(saved.getByLabel('Edge margin')).toHaveValue('2')
  await saved.getByLabel('Edge margin').fill('1')
  await saved.getByRole('button', { name: 'Apply' }).click()
  await expect(errors(page)).toHaveCount(0)
  await expect(steps).toHaveText(['Box', 'Cylinder', 'Cylinder2', 'Arrange'])
  await toolbar.getByRole('button', { name: 'Undo', exact: true }).click()
  await page.getByRole('button', { name: 'Edit Arrange' }).click()
  await expect(page.getByRole('form', { name: 'Arrange' }).getByLabel('Edge margin')).toHaveValue('2')
  await page.getByRole('form', { name: 'Arrange' }).getByRole('button', { name: 'Cancel' }).click()

  const target = path.join(scratchDir('simplecad-arrange-'), 'Grid.scad3')
  await app.evaluate(({ dialog }, filePath) => {
    dialog.showSaveDialog = async () => ({ filePath, canceled: false })
  }, target)
  await page.getByRole('button', { name: 'File', exact: true }).click()
  await page.getByRole('menuitem', { name: /^Save As/ }).click()
  await expect(page.getByText('Grid.scad3', { exact: true })).toBeVisible()
  await app.close()
  const reopened = await launch({ args: [target] })
  await expect(reopened.page.getByRole('list', { name: 'Model history' }).locator('li')).toHaveText(['Box', 'Cylinder', 'Cylinder2', 'Arrange'])
  await expect(reopened.page.getByTestId('engine-status')).toHaveText(placed)
  await reopened.page.getByRole('button', { name: 'Edit Arrange' }).click()
  await expect(reopened.page.getByRole('form', { name: 'Arrange' }).getByRole('combobox', { name: 'Pattern', exact: true })).toHaveValue('grid')
})

test('stands a body on a picked face, drags the pattern, keeps a margin from a picked edge, and moves it to the build plate', async () => {
  const { page } = await launch()
  const steps = page.getByRole('list', { name: 'Model history' }).locator('li')
  const canvas = page.locator('canvas')
  const arrange = await twoCylinders(page)
  await arrange.getByRole('combobox', { name: 'Pattern', exact: true }).selectOption('packed')
  await page.getByLabel('Planar face').selectOption({ label: 'Box · Top · 1200 mm²' })
  await arrange.getByRole('button', { name: 'Pick contact faces (0 chosen)' }).click()
  await page.getByLabel('Planar face').selectOption({ label: 'Cylinder · Top · 50.27 mm²' })
  // The pick stays open for the other bodies, one face each.
  await arrange.getByRole('button', { name: 'Click a face on an object…' }).click()
  await expect(arrange.getByRole('button', { name: 'Pick contact faces (1 chosen)' })).toBeVisible()
  await arrange.getByRole('button', { name: 'Preview' }).click()
  await expect(arrange.getByRole('status', { name: 'Arrangement fit' })).toHaveText(/^2 objects · packed · /)

  // The preview drags as one; the drag becomes the pattern's offset, and the preview follows.
  await expect(canvas).toHaveAttribute('data-handles', /"x"/)
  // Let the view settle: the handles are published after each frame.
  await page.waitForTimeout(200)
  const [hx, hy] = JSON.parse((await canvas.getAttribute('data-handles'))!).x as [number, number]
  await page.mouse.move(hx, hy)
  await page.mouse.down()
  await page.mouse.move(hx + 30, hy + 8, { steps: 4 })
  await expect(canvas).toHaveAttribute('data-ghost', 'move')
  await page.keyboard.type('2')
  await page.mouse.up()
  // The top face's U is world X.
  await expect(arrange.getByRole('status', { name: 'Pattern moved' })).toHaveText('Moved 2, 0, 0 mm along U, V and off the face.')
  await expect(arrange.getByRole('status', { name: 'Arrangement fit' })).toHaveText(/^2 objects · packed · /)
  await expect(errors(page)).toHaveCount(0)
  await arrange.getByRole('button', { name: 'Apply' }).click()
  await expect(steps).toHaveText(['Box', 'Cylinder', 'Cylinder2', 'Arrange'])
  await expect(errors(page)).toHaveCount(0)

  // History keeps the picks and the drag; a manual boundary keeps its margin from an edge picked on the target face.
  await page.getByRole('button', { name: 'Edit Arrange' }).click()
  let saved = page.getByRole('form', { name: 'Arrange' })
  await expect(saved.getByRole('button', { name: 'Pick contact faces (1 chosen)' })).toBeVisible()
  await expect(saved.getByRole('status', { name: 'Pattern moved' })).toHaveText('Moved 2, 0, 0 mm along U, V and off the face.')
  await saved.getByRole('button', { name: 'Pick another face' }).click()
  await page.getByLabel('Planar face').selectOption({ label: 'Box · Top · 1200 mm²' })
  await saved.getByRole('radio', { name: 'Manual' }).click()
  await saved.getByRole('button', { name: 'Pick edges to keep the margin from (0)' }).click()
  await edgePoints(page)
  const anchors: (number[] | null)[] = JSON.parse((await canvas.getAttribute('data-edge-anchors'))!)
  const top = anchors.find((p) => p && Math.abs(p[4] - 20) < 1e-3)!
  await page.mouse.click(top[0], top[1])
  await saved.getByRole('button', { name: 'Click edges of the target face…' }).click()
  await expect(saved.getByRole('button', { name: 'Pick edges to keep the margin from (1)' })).toBeVisible()
  await saved.getByRole('button', { name: 'Apply' }).click()
  await expect(errors(page)).toHaveCount(0)
  await expect(steps).toHaveText(['Box', 'Cylinder', 'Cylinder2', 'Arrange'])

  // On the build plate, the edge picked on the box goes.
  await page.getByRole('button', { name: 'Edit Arrange' }).click()
  saved = page.getByRole('form', { name: 'Arrange' })
  await expect(saved.getByRole('button', { name: 'Pick edges to keep the margin from (1)' })).toBeVisible()
  await saved.getByRole('button', { name: 'Use the build plate' }).click()
  await saved.getByRole('button', { name: 'Apply' }).click()
  await expect(errors(page)).toHaveCount(0)
  await page.getByRole('button', { name: 'Edit Arrange' }).click()
  saved = page.getByRole('form', { name: 'Arrange' })
  await expect(saved.getByRole('status', { name: 'Arrange target' })).toHaveText('On the build plate.')
})
