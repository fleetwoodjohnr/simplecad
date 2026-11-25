// 0.6.0's new ways of making a step, each saved: with SCAD_KEEP_SAVES=<folder> the projects are kept, for the release
// check that 0.4.4 opens them and rebuilds what 0.6.0 shows (scripts/compare_projects.py --rebuild).
import path from 'node:path'
import type { ElectronApplication, Page } from '@playwright/test'
import { addBox, edgePoints, expect, handleAt, launch, partAt, scratchDir, test } from './helpers'

async function saveAs(app: ElectronApplication, page: Page, name: string) {
  const target = path.join(scratchDir('simplecad-saves-'), name)
  await app.evaluate(({ dialog }, filePath) => {
    dialog.showSaveDialog = async () => ({ filePath, canceled: false })
  }, target)
  await page.getByRole('button', { name: 'File', exact: true }).click()
  await page.getByRole('menuitem', { name: /^Save As/ }).click()
  await expect(page.getByText(name, { exact: true })).toBeVisible()
}

async function drag(page: Page, from: [number, number], by: [number, number], button: 'left' | 'right' = 'left') {
  await page.mouse.move(...from)
  await page.mouse.down({ button })
  await page.mouse.move(from[0] + by[0], from[1] + by[1], { steps: 6 })
  await page.mouse.up({ button })
}

const middle = (page: Page, name: string): Promise<[number, number]> => handleAt(page.getByRole('button', { name }))

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
    if (await bar.getByRole('form', { name: 'Edge finish' }).count() && !(await bar.getByLabel('Pull distance').count())) break
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
  await drag(page, [box.x + 8, box.y + 8], [box.width - 16, box.height - 16], 'right')
  await page.getByRole('toolbar', { name: 'Selection tools' }).getByRole('button', { name: 'Arrange', exact: true }).click()
  await page.mouse.click(box.x + 30, box.y + box.height - 30)
  await expect(page.getByRole('form', { name: 'Arrange' }).getByRole('status', { name: 'Arrangement fit' })).toHaveText(/^3 objects/)
  await page.keyboard.press('Enter')
  await expect(steps.last()).toHaveText('Arrange')
  await saveAs(app, page, 'Box-selected arrange.scad3')
})

test('a part dragged until it catches flush on another saves as a Move', { tag: '@viewport' }, async () => {
  const { app, page } = await launch()
  const steps = page.getByRole('list', { name: 'Model history' }).locator('li')
  const canvas = page.locator('canvas')
  await addBox(page, '45')
  await page.getByRole('button', { name: 'Fit view' }).click()
  const [px, py] = await partAt(page, 1)
  await page.mouse.click(px, py)
  await expect(canvas).toHaveAttribute('data-handles', /"x"/)
  await page.waitForTimeout(200)
  const [hx, hy] = JSON.parse((await canvas.getAttribute('data-handles'))!).x as [number, number]
  const [ux, uy] = [hx - (px - 20), hy - py].map((v, _, all) => v / Math.hypot(...all))
  await page.mouse.move(hx, hy)
  await page.mouse.down()
  for (let step = 1; step < 120 && !(await canvas.getAttribute('data-snap'))?.startsWith('Box:'); step++) {
    await page.mouse.move(hx - ux * step * 2, hy - uy * step * 2)
  }
  await page.mouse.up()
  await expect(steps).toHaveText(['Box', 'Box2', 'Move', 'Move2'])
  await saveAs(app, page, 'Magnet move.scad3')
})

test('two parts combined with Subtract from the bar save as a Combine', { tag: '@viewport' }, async () => {
  const { app, page } = await launch()
  const steps = page.getByRole('list', { name: 'Model history' }).locator('li')
  const bodies = page.getByRole('list', { name: 'Bodies', exact: true })
  await addBox(page, '30')
  await bodies.getByRole('button', { name: 'Box', exact: true }).click()
  await bodies.getByRole('button', { name: 'Box2', exact: true }).click({ modifiers: ['ControlOrMeta'] })
  await page.getByRole('toolbar', { name: 'Selection tools' }).getByRole('button', { name: 'Subtract', exact: true }).click()
  await expect(page.getByTestId('engine-status')).toHaveAttribute('data-preview', 'shown')
  await page.keyboard.press('Enter')
  await expect(steps).toHaveText(['Box', 'Box2', 'Move', 'Combine'])
  await saveAs(app, page, 'Subtract.scad3')
})

test('an edge bevelled by its handle saves as a Chamfer step', { tag: '@viewport' }, async () => {
  const { app, page } = await launch()
  const steps = page.getByRole('list', { name: 'Model history' }).locator('li')
  const bar = page.getByRole('toolbar', { name: 'Selection tools' })
  await expect(steps).toHaveText(['Box'])
  for (const point of await edgePoints(page)) {
    await page.mouse.click(...point)
    if (await bar.getByRole('form', { name: 'Edge finish' }).count() && !(await bar.getByLabel('Pull distance').count())) break
    const box = (await page.locator('canvas').boundingBox())!
    await page.mouse.click(box.x + 6, box.y + 6)
  }
  await bar.getByRole('radio', { name: 'Bevel' }).click()
  await drag(page, await middle(page, 'Drag to bevel the edge'), [25, 25])
  await expect(steps).toHaveText(['Box', 'Chamfer'])
  await saveAs(app, page, 'Drag bevel.scad3')
})

test('a part placed on a face and dragged there saves as an Align step', { tag: '@viewport' }, async () => {
  const { app, page } = await launch()
  const steps = page.getByRole('list', { name: 'Model history' }).locator('li')
  const canvas = page.locator('canvas')
  const faces = page.getByLabel('Planar face')
  await page.getByRole('toolbar', { name: 'Modeling tools' }).getByRole('button', { name: 'Shape', exact: true }).click()
  const shape = page.getByRole('form', { name: 'Create shape' })
  await shape.getByLabel('Shape type').selectOption('cylinder')
  await shape.getByLabel('Radius').fill('5 mm')
  await shape.getByLabel('Height').fill('10 mm')
  await shape.getByRole('button', { name: 'Create', exact: true }).click()
  await faces.selectOption({ label: (await faces.locator('option', { hasText: /^Cylinder · Bottom/ }).innerText()).trim() })
  await page.getByRole('button', { name: 'Command search' }).click()
  await page.getByLabel('Search commands').fill('Stack & Place')
  await page.getByRole('dialog', { name: 'Command search' }).getByRole('button', { name: /^Stack & Place/ }).first().click()
  await page.getByLabel('Target face').selectOption({ label: (await page.getByLabel('Target face').locator('option', { hasText: /^Box · Top/ }).innerText()).trim() })
  await expect(canvas).toHaveAttribute('data-handles', /"x"/)
  await page.waitForTimeout(300)
  const [hx, hy] = (JSON.parse((await canvas.getAttribute('data-handles'))!) as Record<string, [number, number]>).x
  await drag(page, [hx, hy], [30, 8])
  await expect.poll(async () => [await page.getByLabel('Horizontal gap').inputValue(), await page.getByLabel('Vertical gap').inputValue()].some((v) => Number(v) !== 0)).toBe(true)
  await page.getByRole('button', { name: 'Place', exact: true }).click()
  await expect(steps).toHaveText(['Box', 'Cylinder', 'Align'])
  await saveAs(app, page, 'Stack drag.scad3')
})

test('Matching Part on a flat face saves a threaded Hole', async () => {
  const { app, page } = await launch()
  const steps = page.getByRole('list', { name: 'Model history' }).locator('li')
  const faces = page.getByLabel('Planar face')
  await page.getByRole('toolbar', { name: 'Modeling tools' }).getByRole('button', { name: 'Shape', exact: true }).click()
  const shape = page.getByRole('form', { name: 'Create shape' })
  await shape.getByLabel('Shape type').selectOption('cylinder')
  await shape.getByLabel('Radius').fill('10 mm')
  await shape.getByRole('button', { name: 'Create' }).click()
  await faces.selectOption({ label: (await faces.locator('option', { hasText: 'Cylinder · Curved' }).first().innerText()).trim() })
  await page.getByRole('button', { name: 'Command search' }).click()
  await page.getByLabel('Search commands').fill('Thread')
  await page.getByRole('dialog', { name: 'Command search' }).getByRole('button', { name: /^Thread$/ }).first().click()
  const thread = page.getByRole('form', { name: 'Thread' })
  await thread.getByLabel('Thread size').selectOption('M20')
  await thread.getByLabel('Threaded length').fill('10 mm')
  await thread.getByRole('button', { name: 'Apply' }).click()
  await expect(steps).toHaveText(['Box', 'Cylinder', 'Thread'])
  await faces.selectOption({ label: 'Box · Top · 1200 mm²' })
  await page.getByRole('toolbar', { name: 'Selection tools' }).getByRole('button', { name: 'Matching Part', exact: true }).click()
  await page.getByRole('form', { name: 'Hole' }).getByRole('button', { name: 'Apply' }).click()
  await expect(steps).toHaveText(['Box', 'Cylinder', 'Thread', 'Hole'])
  await saveAs(app, page, 'Threaded hole.scad3')
})
