import type { Page } from '@playwright/test'
import { errors, expect, launch, test, command, more } from './helpers'

test('creates a physical nut from an existing shaft thread without choosing the thread again', async () => {
  const { page } = await launch()
  const status = page.getByTestId('engine-status')
  const toolbar = page.getByRole('toolbar', { name: 'Modeling tools' })
  await expect(status).toContainText('24000 mm³')
  await page.getByRole('button', { name: 'Hide Box', exact: true }).click()
  await toolbar.getByRole('button', { name: 'Shape', exact: true }).click()
  const shape = page.getByRole('form', { name: 'Create shape' })
  await shape.getByLabel('Shape type').selectOption('cylinder')
  await shape.getByLabel('Radius').fill('15 mm')
  await shape.getByRole('button', { name: 'Create' }).click()
  await expect(status).toContainText('21205.75 mm³')
  await page.getByLabel('Planar face').selectOption({ label: 'Curved · 2827.43 mm²' })
  await command(page, 'Thread')
  const thread = page.getByRole('form', { name: 'Thread' })
  await thread.getByLabel('Thread size').selectOption('M30')
  await thread.getByLabel('Threaded length').fill('10 mm')
  await thread.getByRole('button', { name: 'Apply' }).click()
  await expect(thread).toBeHidden()
  const shaft = await status.textContent()

  await command(page, 'Matching Part')
  const match = page.getByRole('form', { name: 'Matching part' })
  await expect(match.getByText('M30 nut', { exact: true })).toBeVisible()
  await match.getByLabel('Height').fill('5 mm * 2')
  await more(match)
  await match.getByLabel('X offset').fill('60 mm')
  await expect(page.getByTestId('engine-status')).toHaveAttribute('data-preview', 'shown')
  await match.getByRole('button', { name: 'Create' }).click()
  await expect(match).toBeHidden()
  await expect(page.getByRole('button', { name: 'Hide Nut', exact: true })).toBeVisible()
  await expect(page.getByRole('list', { name: 'Model history' }).locator('li')).toHaveText(['Box', 'Cylinder', 'Thread', 'Nut'])
  const combined = await status.textContent()
  expect(Number(combined?.match(/([\d.]+) mm³/)?.[1])).toBeGreaterThan(Number(shaft?.match(/([\d.]+) mm³/)?.[1]) + 1000)

  await toolbar.getByRole('button', { name: 'Undo', exact: true }).click()
  await expect(status).toHaveText(shaft!)
  await toolbar.getByRole('button', { name: 'Redo', exact: true }).click()
  await expect(status).toHaveText(combined!)
})

test('cuts the matching thread into the bore of an existing tube, as one undoable step', async () => {
  const { page } = await launch()
  const status = page.getByTestId('engine-status')
  const toolbar = page.getByRole('toolbar', { name: 'Modeling tools' })
  const steps = page.getByRole('list', { name: 'Model history' }).locator('li')
  await expect(status).toContainText('24000 mm³')
  await page.getByRole('button', { name: 'Hide Box', exact: true }).click()
  const create = async (kind: string, fields: Record<string, string>) => {
    await toolbar.getByRole('button', { name: 'Shape', exact: true }).click()
    const shape = page.getByRole('form', { name: 'Create shape' })
    await shape.getByLabel('Shape type').selectOption(kind)
    for (const [label, value] of Object.entries(fields)) await shape.getByLabel(label).fill(value)
    await shape.getByRole('button', { name: 'Create' }).click()
    await expect(shape).toBeHidden()
  }
  await create('cylinder', { Radius: '15 mm' })
  await page.getByLabel('Planar face').selectOption({ label: 'Curved · 2827.43 mm²' })
  await command(page, 'Thread')
  const thread = page.getByRole('form', { name: 'Thread' })
  await thread.getByLabel('Thread size').selectOption('M30')
  await thread.getByLabel('Threaded length').fill('10 mm')
  await thread.getByRole('button', { name: 'Apply' }).click()
  await expect(thread).toBeHidden()

  await create('tube', { 'Outer radius': '25 mm', 'Inner radius': '15 mm' })
  await page.getByLabel('Planar face').selectOption({ label: 'Tube · Curved · 2827.43 mm²' })
  const before = await status.textContent()
  await command(page, 'Matching Part')
  const match = page.getByRole('form', { name: 'Matching part' })
  // Opened on a round face, it threads that face, and the thread previews before it is cut.
  await expect(match.getByRole('radio', { name: 'Thread a round face' })).toBeChecked()
  await expect(match.getByText(/Cuts a matching M30 thread into the selected round face/)).toBeVisible()
  await expect(status).toHaveAttribute('data-preview', 'shown')
  await match.getByRole('button', { name: 'Cut thread' }).click()
  await expect(steps).toHaveText(['Box', 'Cylinder', 'Thread', 'Tube', 'Matching Thread'])
  await expect(errors(page)).toHaveCount(0)
  await expect(status).not.toHaveText(before!)
  await toolbar.getByRole('button', { name: 'Undo', exact: true }).click()
  await expect(status).toHaveText(before!)
})

/** Moves the pointer about `around` until face `label` (its name in the face list) is what a click would take there. */
async function pointOn(page: Page, label: string, around: [number, number], reach = 90): Promise<[number, number]> {
  const canvas = page.locator('canvas')
  const option = page.getByLabel('Planar face').locator('option').filter({ hasText: new RegExp(`^${label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}$`) })
  const face = await option.getAttribute('value')
  for (let r = 0; r <= reach; r += 6) {
    for (let dx = -r; dx <= r; dx += 6) {
      for (const dy of r === 0 ? [0] : dx === -r || dx === r ? Array.from({ length: r / 3 + 1 }, (_, k) => -r + k * 6) : [-r, r]) {
        const point: [number, number] = [around[0] + dx, around[1] + dy]
        await page.mouse.move(...point)
        await page.waitForTimeout(20)
        if (await canvas.getAttribute('data-hover-face') === face) return point
      }
    }
  }
  throw new Error(`${label} is not under the pointer anywhere near ${around}`)
}

// 0.2.0's threaded pair: click the shaft, Shift-click the hole on the other part, and Thread together leads the bar.
test('a shaft and a hole clicked on two parts lead the bar with Thread together, which threads both at once', { tag: '@viewport' }, async () => {
  const { page } = await launch()
  const toolbar = page.getByRole('toolbar', { name: 'Modeling tools' })
  const steps = page.getByRole('list', { name: 'Model history' }).locator('li')
  const canvas = page.locator('canvas')
  const bar = page.getByRole('toolbar', { name: 'Selection tools' })
  await expect(page.getByTestId('engine-status')).toContainText('24000 mm³')
  await page.getByRole('button', { name: 'Hide Box', exact: true }).click()
  const create = async (kind: string, fields: Record<string, string>) => {
    await toolbar.getByRole('button', { name: 'Shape', exact: true }).click()
    const shape = page.getByRole('form', { name: 'Create shape' })
    await shape.getByLabel('Shape type').selectOption(kind)
    for (const [label, value] of Object.entries(fields)) await shape.getByLabel(label, { exact: true }).fill(value)
    await shape.getByRole('button', { name: 'Create' }).click()
    await expect(shape).toBeHidden()
  }
  // A ⌀10 shaft, and beside it a tube with a ⌀10 bore.
  await create('cylinder', { Radius: '5', Height: '12' })
  await create('tube', { 'Outer radius': '10', 'Inner radius': '5', Height: '10' })
  await command(page, 'Move')
  const move = page.getByRole('form', { name: 'Move', exact: true })
  await move.getByLabel('Body').selectOption({ label: 'Tube' })
  await move.getByLabel('X (mm)').fill('40')
  await move.getByRole('button', { name: 'Apply' }).click()
  await page.keyboard.press('Escape')
  await expect(steps).toHaveText(['Box', 'Cylinder', 'Tube', 'Move'])
  await page.getByRole('button', { name: 'Fit view' }).click()
  await expect(canvas).toHaveAttribute('data-body-anchors', /"2"/)
  await page.waitForTimeout(300)
  const anchors = JSON.parse((await canvas.getAttribute('data-body-anchors'))!) as Record<string, [number, number]>

  await page.mouse.click(...await pointOn(page, 'Cylinder · Curved · 376.99 mm²', anchors[1]))
  const bore = await pointOn(page, 'Tube · Curved · 314.16 mm²', anchors[2])
  await page.keyboard.down('Shift')
  await page.mouse.click(...bore)
  await page.keyboard.up('Shift')
  await expect(bar.getByRole('button').first()).toHaveAccessibleName('Thread together')
  await bar.getByRole('button', { name: 'Thread together' }).click()
  const pair = page.getByRole('form', { name: 'Threaded Connection' })
  await expect(pair.getByRole('status', { name: 'Threaded faces' })).toContainText('both threads are made together')
  await expect(page.getByTestId('engine-status')).toHaveAttribute('data-preview', 'shown')
  await page.keyboard.press('Enter')
  await expect(steps).toHaveText(['Box', 'Cylinder', 'Tube', 'Move', 'Threaded Connection'])
  await expect(errors(page)).toHaveCount(0)
})

// 0.2.0's Create Matching Part on a flat face: a threaded hole that fits the thread, opposite inside and out.
test('Matching Part on a flat face opens Hole set to a threaded hole that fits the thread', async () => {
  const { page } = await launch()
  const toolbar = page.getByRole('toolbar', { name: 'Modeling tools' })
  const steps = page.getByRole('list', { name: 'Model history' }).locator('li')
  await expect(page.getByTestId('engine-status')).toContainText('24000 mm³')
  await toolbar.getByRole('button', { name: 'Shape', exact: true }).click()
  const shape = page.getByRole('form', { name: 'Create shape' })
  await shape.getByLabel('Shape type').selectOption('cylinder')
  await shape.getByLabel('Radius').fill('10 mm')
  await shape.getByRole('button', { name: 'Create' }).click()
  const curved = await page.getByLabel('Planar face').locator('option', { hasText: 'Cylinder · Curved' }).first().textContent()
  await page.getByLabel('Planar face').selectOption({ label: curved!.trim() })
  await command(page, 'Thread')
  const thread = page.getByRole('form', { name: 'Thread' })
  await thread.getByLabel('Thread size').selectOption('M20')
  await thread.getByLabel('Threaded length').fill('10 mm')
  await thread.getByRole('button', { name: 'Apply' }).click()
  await expect(errors(page)).toHaveCount(0)
  await expect(steps).toHaveText(['Box', 'Cylinder', 'Thread'])

  // The box's top: Matching Part is on the bar, and opens Hole as an M20 threaded hole.
  await page.getByLabel('Planar face').selectOption({ label: 'Box · Top · 1200 mm²' })
  const bar = page.getByRole('toolbar', { name: 'Selection tools' })
  await bar.getByRole('button', { name: 'Matching Part', exact: true }).click()
  const hole = page.getByRole('form', { name: 'Hole' })
  await more(hole)
  await expect(hole.getByLabel('Style')).toHaveValue('threaded')
  await expect(hole.getByLabel('Thread size')).toHaveValue('M20')
  await hole.getByRole('button', { name: 'Apply' }).click()
  await expect(steps).toHaveText(['Box', 'Cylinder', 'Thread', 'Hole'])
  await expect(errors(page)).toHaveCount(0)
})
