// 0.6.0's new ways of making a step, each saved: with SCAD_KEEP_SAVES=<folder> the projects are kept, for the release
// check that 0.4.4 opens them and rebuilds what 0.6.0 shows (scripts/compare_projects.py --rebuild).
import path from 'node:path'
import type { ElectronApplication, Page } from '@playwright/test'
import { addBox, edgePoints, expect, launch, partAt, scratchDir, test } from './helpers'

async function saveAs(app: ElectronApplication, page: Page, name: string) {
  const target = path.join(scratchDir('simplecad-saves-'), name)
  await app.evaluate(({ dialog }, filePath) => {
    dialog.showSaveDialog = async () => ({ filePath, canceled: false })
  }, target)
  await page.getByRole('button', { name: 'File', exact: true }).click()
  await page.getByRole('menuitem', { name: /^Save As/ }).click()
  await expect(page.getByText(name, { exact: true })).toBeVisible()
}

async function drag(page: Page, from: [number, number], by: [number, number]) {
  await page.mouse.move(...from)
  await page.mouse.down()
  await page.mouse.move(from[0] + by[0], from[1] + by[1], { steps: 6 })
  await page.mouse.up()
}

const middle = async (page: Page, name: string): Promise<[number, number]> => {
  const box = (await page.getByRole('button', { name }).boundingBox())!
  return [box.x + box.width / 2, box.y + box.height / 2]
}

test('a part moved and turned by its arrows and rings saves as Move and Rotate steps', { tag: '@viewport' }, async () => {
  const { app, page } = await launch()
  const steps = page.getByRole('list', { name: 'Model history' }).locator('li')
  const canvas = page.locator('canvas')
  await expect(steps).toHaveText(['Box'])
  await page.mouse.click(...await partAt(page, 0))
  await expect(canvas).toHaveAttribute('data-rings', /"z"/)
  await page.waitForTimeout(200)
  const { x } = JSON.parse((await canvas.getAttribute('data-handles'))!)
  await page.mouse.move(x[0], x[1])
  await page.mouse.down()
  await page.mouse.move(x[0] + 30, x[1] + 8, { steps: 4 })
  await page.keyboard.type('12.5')
  await page.mouse.up()
  await expect(steps).toHaveText(['Box', 'Move'])
  await page.waitForTimeout(200)
  const { z } = JSON.parse((await canvas.getAttribute('data-rings'))!)
  await page.mouse.move(z[0], z[1])
  await page.mouse.down()
  await page.mouse.move(z[0] + 20, z[1] + 6, { steps: 4 })
  await page.keyboard.type('30')
  await page.mouse.up()
  await expect(steps).toHaveText(['Box', 'Move', 'Rotate'])
  await saveAs(app, page, 'Gizmo move.scad3')
})

test('an edge rounded by its handle saves as a Fillet step', { tag: '@viewport' }, async () => {
  const { app, page } = await launch()
  const steps = page.getByRole('list', { name: 'Model history' }).locator('li')
  const bar = page.getByRole('toolbar', { name: 'Selection tools' })
  await expect(steps).toHaveText(['Box'])
  for (const point of await edgePoints(page)) {
    await page.mouse.click(...point)
    if (await bar.getByRole('button', { name: 'Fillet', exact: true }).count() && !(await bar.getByLabel('Pull distance').count())) break
    const box = (await page.locator('canvas').boundingBox())!
    await page.mouse.click(box.x + 6, box.y + 6)
  }
  await drag(page, await middle(page, 'Drag to round the edge'), [25, 25])
  await expect(steps).toHaveText(['Box', 'Fillet'])
  await saveAs(app, page, 'Drag round.scad3')
})

test('a part split by dragging its plane saves as a Split step', { tag: '@viewport' }, async () => {
  const { app, page } = await launch()
  const steps = page.getByRole('list', { name: 'Model history' }).locator('li')
  await expect(steps).toHaveText(['Box'])
  await page.getByRole('list', { name: 'Bodies', exact: true }).getByRole('button', { name: 'Box', exact: true }).click()
  await page.getByRole('toolbar', { name: 'Selection tools' }).getByRole('button', { name: 'Split', exact: true }).click()
  const form = page.getByRole('form', { name: 'Split' })
  await drag(page, await middle(page, 'Drag the split plane'), [0, -30])
  await expect(form.getByLabel('Offset from the centre')).not.toHaveValue('0')
  await page.keyboard.press('Enter')
  await expect(steps).toHaveText(['Box', 'Split'])
  await saveAs(app, page, 'Split drag.scad3')
})

test('three parts box-selected and arranged on the plate save as an Arrange step', { tag: '@viewport' }, async () => {
  const { app, page } = await launch()
  const steps = page.getByRole('list', { name: 'Model history' }).locator('li')
  await addBox(page, '50')
  await addBox(page, '80')
  await page.getByRole('button', { name: 'Fit view' }).click()
  const box = (await page.locator('canvas').boundingBox())!
  await page.mouse.click(box.x + 8, box.y + 8)
  await drag(page, [box.x + 8, box.y + 8], [box.width - 16, box.height - 16])
  await page.getByRole('toolbar', { name: 'Selection tools' }).getByRole('button', { name: 'Arrange', exact: true }).click()
  await page.mouse.click(box.x + 30, box.y + box.height - 30)
  await expect(page.getByRole('form', { name: 'Arrange' }).getByRole('status', { name: 'Arrangement fit' })).toHaveText(/^3 objects/)
  await page.keyboard.press('Enter')
  await expect(steps.last()).toHaveText('Arrange')
  await saveAs(app, page, 'Box-selected arrange.scad3')
})
