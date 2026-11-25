// The ten everyday tasks, done the shortest way 0.6.0's window allows, each held to its target.
// Counting, as for 0.5.1's 47 (build/rust-verification/v060-ux/clicks-today-afacf65.json in the 0.4.4 checkout): a click or a
// drag is 1, a drop-down list is 2, a typed value is 1, and Enter, Delete or Escape is 1. 0.6.0's target is 29 in all.
import { writeFileSync } from 'node:fs'
import type { Locator, Page } from '@playwright/test'
import { addBox, edgePoints, expect, launch, partAt, test } from './helpers'

type Point = [number, number]
const results: Record<string, { actions: number; target: number; path: string[] }> = {}

function counter(page: Page, task: string, target: number) {
  const r = (results[task] = { actions: 0, target, path: [] as string[] })
  const step = (what: string) => { r.actions++; r.path.push(what) }
  return {
    result: r,
    at: async (point: Point, what: string, modifiers: ('Shift')[] = []) => {
      step(`click ${what}`)
      for (const key of modifiers) await page.keyboard.down(key)
      await page.mouse.click(...point)
      for (const key of modifiers) await page.keyboard.up(key)
    },
    double: async (point: Point, what: string) => { step(`double-click ${what}`); await page.mouse.dblclick(...point) },
    click: async (l: Locator, what: string) => { step(`click ${what}`); await l.click() },
    type: async (text: string, what: string) => { step(`type ${text} in ${what}`); await page.keyboard.type(text) },
    key: async (key: string, what: string) => { step(`press ${what}`); await page.keyboard.press(key) },
    /** One drag from `from`, moved along `direction` until `reading` shows `wanted` (the drags snap, so it lands). */
    drag: async (from: Point, direction: Point, reading: Locator, wanted: RegExp, what: string) => {
      step(`drag ${what}`)
      const length = Math.hypot(...direction)
      const [dx, dy] = [direction[0] / length, direction[1] / length]
      await page.mouse.move(...from)
      await page.mouse.down()
      let found = false
      for (let i = 1; i <= 400 && !found; i++) {
        await page.mouse.move(from[0] + dx * i, from[1] + dy * i)
        found = wanted.test((await reading.textContent()) ?? '')
      }
      await page.mouse.up()
      expect(found, `${what} reached ${wanted}`).toBe(true)
    }
  }
}

async function start() {
  const launched = await launch()
  await expect(launched.page.getByTestId('engine-status')).toHaveText(/24000 mm³/)
  await expect(launched.page.locator('canvas')).toHaveAttribute('aria-busy', 'false')
  const steps = launched.page.getByRole('list', { name: 'Model history' }).locator('li')
  const bar = launched.page.getByRole('toolbar', { name: 'Selection tools' })
  return { ...launched, steps, bar }
}
const handles = async (page: Page, set: 'handles' | 'rings') => {
  const canvas = page.locator('canvas')
  await expect(canvas).toHaveAttribute(`data-${set}`, /"z"/)
  await page.waitForTimeout(200)
  return JSON.parse((await canvas.getAttribute(`data-${set}`))!) as Record<'x' | 'y' | 'z', Point>
}
const middle = async (l: Locator): Promise<Point> => {
  const box = (await l.boundingBox())!
  return [box.x + box.width / 2, box.y + box.height / 2]
}
const empty = async (page: Page): Promise<Point> => {
  const box = (await page.locator('canvas').boundingBox())!
  return [box.x + 8, box.y + 8]
}

test.describe.configure({ mode: 'serial' })
test.afterAll(() => {
  const total = Object.values(results).reduce((sum, r) => sum + r.actions, 0)
  if (process.env.CLICKS_OUT) writeFileSync(process.env.CLICKS_OUT, JSON.stringify({ total, results }, null, 1))
})

test('1 move a part 10 mm', { tag: '@viewport' }, async () => {
  const { page, steps } = await start()
  const c = counter(page, 'Move a part 10 mm', 2)
  await c.at(await partAt(page, 0), 'the part')
  const { x } = await handles(page, 'handles')
  const anchor = await partAt(page, 0)
  await c.drag(x, [x[0] - anchor[0], x[1] - anchor[1]], page.getByRole('status', { name: 'Transform reading' }), /^ΔX 10 mm$/, 'the X arrow')
  await expect(steps).toHaveText(['Box', 'Move'])
  expect(c.result.actions).toBeLessThanOrEqual(c.result.target)
})

test('2 rotate a part 90°', { tag: '@viewport' }, async () => {
  const { page, steps } = await start()
  const c = counter(page, 'Rotate a part 90°', 2)
  await c.at(await partAt(page, 0), 'the part')
  const { z } = await handles(page, 'rings')
  await c.drag(z, [1, 0], page.getByRole('status', { name: 'Transform reading' }), /^-?90° · snapped$/, 'the Z ring')
  await expect(steps).toHaveText(['Box', 'Rotate'])
  expect(c.result.actions).toBeLessThanOrEqual(c.result.target)
})

test('3 pull a face 5 mm', { tag: '@viewport' }, async () => {
  const { page, steps } = await start()
  const c = counter(page, 'Pull a face 5 mm', 2)
  await c.at(await partAt(page, 0), 'the top face')
  const handle = page.getByRole('button', { name: 'Drag face along its normal' })
  await c.drag(await middle(handle), [0, -1], handle.locator('span'), /^\+5 mm$/, 'its handle')
  await expect(steps).toHaveText(['Box', 'Pull'])
  expect(c.result.actions).toBeLessThanOrEqual(c.result.target)
})

test('4 round an edge 2 mm', { tag: '@viewport' }, async () => {
  const { page, steps, bar } = await start()
  const c = counter(page, 'Round an edge 2 mm', 2)
  for (const point of await edgePoints(page)) {
    await page.mouse.click(...point)
    if (await bar.getByRole('form', { name: 'Edge finish' }).count() && !(await bar.getByLabel('Pull distance').count())) {
      c.result.actions++
      c.result.path.push('click the edge')
      break
    }
    await page.mouse.click(...await empty(page))
  }
  const handle = page.getByRole('button', { name: 'Drag to round the edge' })
  await c.drag(await middle(handle), [1, 1], page.getByRole('status', { name: 'Handle reading' }), /^R 2 mm$/, 'the round handle')
  await expect(steps).toHaveText(['Box', 'Fillet'])
  expect(c.result.actions).toBeLessThanOrEqual(c.result.target)
})

test('5 delete a part', { tag: '@viewport' }, async () => {
  const { page } = await start()
  const c = counter(page, 'Delete a part', 2)
  await c.at(await partAt(page, 0), 'the part')
  await c.key('Delete', 'Delete')
  await expect(page.getByRole('list', { name: 'Bodies', exact: true }).getByRole('button', { name: 'Box', exact: true })).toHaveCount(0)
  expect(c.result.actions).toBeLessThanOrEqual(c.result.target)
})

test('6 drill a 6 mm hole', { tag: '@viewport' }, async () => {
  const { page, steps, bar } = await start()
  const c = counter(page, 'Drill a 6 mm hole', 4)
  await c.at(await partAt(page, 0), 'where it goes')
  await c.click(bar.getByRole('button', { name: 'Hole', exact: true }), 'Hole')
  await c.type('6', 'Diameter')
  await c.key('Enter', 'Enter')
  await expect(steps).toHaveText(['Box', 'Hole'])
  expect(c.result.actions).toBeLessThanOrEqual(c.result.target)
})

test('7 cut one part with another', { tag: '@viewport' }, async () => {
  const { page, steps, bar } = await start()
  await addBox(page, '30')
  await page.getByRole('button', { name: 'Fit view' }).click()
  await page.mouse.click(...await empty(page))
  const c = counter(page, 'Cut one part with another', 4)
  await c.at(await partAt(page, 0), 'the part')
  await c.at(await partAt(page, 1), 'the cutter', ['Shift'])
  await c.click(bar.getByRole('button', { name: 'Subtract', exact: true }), 'Subtract')
  await c.key('Enter', 'Enter')
  await expect(steps.last()).toHaveText('Combine')
  expect(c.result.actions).toBeLessThanOrEqual(c.result.target)
})

test('8 arrange three parts on the plate', { tag: '@viewport' }, async () => {
  const { page, steps, bar } = await start()
  await addBox(page, '50')
  await addBox(page, '80')
  await page.getByRole('button', { name: 'Fit view' }).click()
  await page.mouse.click(...await empty(page))
  const c = counter(page, 'Arrange three parts on the plate', 4)
  const box = (await page.locator('canvas').boundingBox())!
  c.result.actions++
  c.result.path.push('right-drag a box around them')
  await page.mouse.move(box.x + 8, box.y + 8)
  await page.mouse.down({ button: 'right' })
  await page.mouse.move(box.x + box.width - 8, box.y + box.height - 8, { steps: 6 })
  await page.mouse.up({ button: 'right' })
  await c.click(bar.getByRole('button', { name: 'Arrange', exact: true }), 'Arrange')
  await c.at([box.x + 30, box.y + box.height - 30], 'the plate')
  await expect(page.getByRole('form', { name: 'Arrange' }).getByRole('status', { name: 'Arrangement fit' })).toHaveText(/^3 objects/)
  await c.key('Enter', 'Enter')
  await expect(steps.last()).toHaveText('Arrange')
  expect(c.result.actions).toBeLessThanOrEqual(c.result.target)
})

test('9 join two parts with a clip', { tag: '@viewport' }, async () => {
  const { page, steps, bar } = await start()
  await addBox(page, '40')
  await page.getByRole('button', { name: 'Fit view' }).click()
  await page.mouse.click(...await empty(page))
  const c = counter(page, 'Join two parts with a clip', 4)
  await c.at(await partAt(page, 0), 'a face')
  await c.at(await partAt(page, 1), 'the other', ['Shift'])
  await c.click(bar.getByRole('button', { name: 'Clip Joint', exact: true }), 'Clip')
  await expect(page.getByRole('form', { name: 'Clip Joint' }).getByRole('status', { name: 'Joined faces' })).toContainText('meets')
  await c.key('Enter', 'Enter')
  await expect(steps.last()).toHaveText('Clip Joint')
  expect(c.result.actions).toBeLessThanOrEqual(c.result.target)
})

test("10 change a part's width", { tag: '@viewport' }, async () => {
  const { page } = await start()
  const c = counter(page, "Change a part's width", 3)
  await c.double(await partAt(page, 0), 'the part')
  await c.type('50', 'Width')
  await c.key('Enter', 'Enter')
  await expect(page.getByTestId('engine-status')).toHaveText(/30000 mm³/)
  expect(c.result.actions).toBeLessThanOrEqual(c.result.target)
})

test('all ten within 29', { tag: '@viewport' }, async () => {
  const total = Object.values(results).reduce((sum, r) => sum + r.actions, 0)
  expect(Object.keys(results)).toHaveLength(10)
  expect(total).toBeLessThanOrEqual(29)
})
