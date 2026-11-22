import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import type { ElectronApplication, Page } from '@playwright/test'
import { expect, launch, test } from './helpers'

type Point = [number, number]

/** The new box's bounds, read back from an STL export (as the transform spec in engine.spec.ts does). */
async function bounds(app: ElectronApplication, page: Page, stl: string) {
  await rm(stl, { force: true })
  await app.evaluate(({ dialog }, filePath) => {
    dialog.showSaveDialog = async () => ({ filePath, canceled: false })
  }, stl)
  await page.getByRole('button', { name: 'File', exact: true }).click()
  await page.getByRole('menuitem', { name: 'Export STL' }).click()
  await expect.poll(async () => (await readFile(stl).catch(() => Buffer.alloc(0))).length).toBe(684)
  const mesh = await readFile(stl)
  const low = [Infinity, Infinity, Infinity]
  const high = [-Infinity, -Infinity, -Infinity]
  for (let triangle = 84; triangle < mesh.length; triangle += 50) {
    for (let point = triangle + 12; point < triangle + 48; point += 12) {
      for (let axis = 0; axis < 3; axis++) {
        const v = mesh.readFloatLE(point + axis * 4)
        low[axis] = Math.min(low[axis], v)
        high[axis] = Math.max(high[axis], v)
      }
    }
  }
  return [...low, ...high]
}

async function handle(page: Page, axis: 'x' | 'y' | 'z'): Promise<Point> {
  const canvas = page.locator('canvas')
  await expect(canvas).toHaveAttribute('data-handles', /"z"/)
  // Let the view settle: the handles are published after each frame.
  await page.waitForTimeout(200)
  return JSON.parse((await canvas.getAttribute('data-handles'))!)[axis]
}

test('the ribbon groups the tools, and the selection bar offers what fits the face', async () => {
  const { page } = await launch()
  await expect(page.getByTestId('engine-status')).toHaveText(/24000 mm³/)
  const toolbar = page.getByRole('toolbar', { name: 'Modeling tools' })
  for (const group of ['Create', 'Modify', 'Combine', 'Place', 'Construct', 'Inspect']) {
    await expect(toolbar.getByRole('group', { name: group, exact: true })).toBeVisible()
  }
  const pushPull = toolbar.getByRole('button', { name: 'Push/Pull', exact: true })
  await expect(pushPull).toBeDisabled()
  await expect(page.getByRole('toolbar', { name: 'Selection tools' })).toHaveCount(0)

  await page.getByLabel('Planar face').selectOption({ label: 'Top · 1200 mm²' })
  const selection = page.getByRole('toolbar', { name: 'Selection tools' })
  await expect(selection).toBeVisible()
  await expect(selection.getByRole('button', { name: 'Hole', exact: true })).toBeEnabled()
  await pushPull.click()
  await expect(selection.getByLabel('Pull distance')).toBeFocused()

  await selection.getByRole('button', { name: 'More tools' }).click()
  await page.getByRole('menuitem', { name: 'Clear selection' }).click()
  await expect(selection).toHaveCount(0)
})

test('a Move drag leaves a ghost where it started, Escape puts it back, and a typed drag is one exact step', async () => {
  const scratch = await mkdtemp(path.join(os.tmpdir(), 'simplecad-ux-'))
  try {
    const { app, page } = await launch()
    const steps = page.getByRole('list', { name: 'Model history' }).locator('li')
    const canvas = page.locator('canvas')
    const reading = page.getByRole('status', { name: 'Transform reading' })
    await expect(steps).toHaveText(['Box'])
    const start = await bounds(app, page, path.join(scratch, 'start.stl'))
    const toolbar = page.getByRole('toolbar', { name: 'Modeling tools' })

    await toolbar.getByRole('button', { name: 'Move', exact: true }).click()
    let [x, y] = await handle(page, 'x')
    await page.mouse.move(x, y)
    await page.mouse.down()
    await page.mouse.move(x + 40, y + 10, { steps: 5 })
    await expect(canvas).toHaveAttribute('data-ghost', 'move')
    await expect(reading).toContainText('Δ')
    await page.keyboard.press('Escape')
    await expect(canvas).toHaveAttribute('data-ghost', '')
    await expect(reading).toBeHidden()
    await page.mouse.up()
    await expect(steps).toHaveText(['Box'])

    ;[x, y] = await handle(page, 'x')
    await page.mouse.move(x, y)
    await page.mouse.down()
    await page.mouse.move(x + 30, y + 8, { steps: 4 })
    await page.keyboard.type('12')
    await expect(reading).toHaveText('12 ⏎')
    await page.mouse.up()
    await expect(steps).toHaveText(['Box', 'Move'])
    const moved = await bounds(app, page, path.join(scratch, 'moved.stl'))
    moved.forEach((value, axis) => expect(value).toBeCloseTo(start[axis] + (axis % 3 === 0 ? 12 : 0), 4))

    await toolbar.getByRole('button', { name: 'Rotate', exact: true }).click()
    ;[x, y] = await handle(page, 'z')
    await page.mouse.move(x, y)
    await page.mouse.down()
    await page.mouse.move(x + 20, y + 6, { steps: 4 })
    await expect(canvas).toHaveAttribute('data-ghost', 'rotate')
    await page.keyboard.type('90')
    await page.keyboard.press('Enter')
    await expect(steps).toHaveText(['Box', 'Move', 'Rotate'])
    await page.mouse.up()
    await expect(steps).toHaveText(['Box', 'Move', 'Rotate'])
    // A quarter turn about the box's own centre swaps its X and Y extents in place.
    const turned = await bounds(app, page, path.join(scratch, 'turned.stl'))
    const centre = (b: number[], axis: number) => (b[axis] + b[axis + 3]) / 2
    const size = (b: number[], axis: number) => b[axis + 3] - b[axis]
    expect(size(turned, 0)).toBeCloseTo(size(moved, 1), 4)
    expect(size(turned, 1)).toBeCloseTo(size(moved, 0), 4)
    for (const axis of [0, 1, 2]) expect(centre(turned, axis)).toBeCloseTo(centre(moved, axis), 4)
  } finally {
    await rm(scratch, { recursive: true, force: true })
  }
})

test('dragging the face handle sweeps a preview with its distance, and Escape cancels it', async () => {
  const { page } = await launch()
  const status = page.getByTestId('engine-status')
  const canvas = page.locator('canvas')
  const steps = page.getByRole('list', { name: 'Model history' }).locator('li')
  await expect(status).toHaveText(/24000 mm³/)
  await expect(canvas).toHaveAttribute('aria-busy', 'false')
  await page.getByLabel('Planar face').selectOption({ label: 'Top · 1200 mm²' })
  const grip = page.getByRole('button', { name: 'Drag face along its normal' })
  await expect(grip).toBeVisible()
  const box = (await grip.boundingBox())!
  const [x, y] = [box.x + box.width / 2, box.y + box.height / 2]

  await page.mouse.move(x, y)
  await page.mouse.down()
  await page.mouse.move(x, y - 30, { steps: 5 })
  await expect(canvas).toHaveAttribute('data-prism', 'pull')
  await expect(grip.locator('span')).toHaveText(/^\+[\d.]+ mm$/)
  await page.keyboard.press('Escape')
  await expect(canvas).toHaveAttribute('data-prism', '')
  await page.mouse.up()
  await expect(steps).toHaveText(['Box'])
  await expect(status).toHaveText(/24000 mm³/)

  const again = (await grip.boundingBox())!
  await page.mouse.move(again.x + again.width / 2, again.y + again.height / 2)
  await page.mouse.down()
  await page.mouse.move(again.x + again.width / 2, again.y + again.height / 2 + 20, { steps: 5 })
  await expect(canvas).toHaveAttribute('data-prism', 'push')
  await page.mouse.up()
  await expect(steps).toHaveText(['Box', 'Pull'])
  await expect(status).not.toHaveText(/24000 mm³/)
})

test('measures an edge it is clicked on, and the distance between two corners', async () => {
  const { page } = await launch()
  const toolbar = page.getByRole('toolbar', { name: 'Modeling tools' })
  const canvas = page.locator('canvas')
  await expect(page.getByTestId('engine-status')).toHaveText(/24000 mm³/)
  await toolbar.getByRole('button', { name: 'Measure', exact: true }).click()
  const panel = page.getByRole('region', { name: 'Measure' })
  const reading = (label: string) => panel.getByText(label, { exact: true }).locator('..').locator('dd')
  const anchors = async () => {
    await expect(canvas).toHaveAttribute('data-edge-anchors', /\[/)
    await page.waitForTimeout(200)
    const box = (await canvas.boundingBox())!
    return (JSON.parse((await canvas.getAttribute('data-edge-anchors'))!) as ([number, number] | null)[])
      .filter((point): point is [number, number] => !!point && point[0] > box.x + 20 && point[0] < box.x + box.width - 20 &&
        point[1] > box.y + 20 && point[1] < box.y + box.height - 20)
  }

  await panel.getByLabel('Measure selection').selectOption('edge')
  const [first] = await anchors()
  await page.mouse.click(...first)
  await expect(reading('Length')).toHaveText(/^(20|30|40) mm$/)
  await expect(canvas).toHaveAttribute('data-picked-edges', '1')

  await panel.getByLabel('Measure selection').selectOption('vertex')
  await panel.getByRole('button', { name: /^Remove / }).click()
  const [a, b] = await anchors()
  await page.mouse.click(a[0], a[1])
  await expect(reading('Position')).toHaveText(/^-?[\d.]+, -?[\d.]+, -?[\d.]+ mm$/)
  await page.mouse.click(b[0], b[1])
  await expect(panel.getByRole('list', { name: 'Measured items' }).locator('li')).toHaveCount(2)
  // Two corners of the 40 × 30 × 20 box are apart by an edge, a face diagonal or the body diagonal.
  const apart = Number((await reading('Minimum distance').innerText()).split(' ')[0])
  const possible = [20, 30, 40, Math.hypot(20, 30), Math.hypot(20, 40), Math.hypot(30, 40), Math.hypot(20, 30, 40)]
  expect(possible.some((distance) => Math.abs(distance - apart) < 0.001)).toBe(true)
})

test('groups bodies from the Bodies list, through undo, a worker restart and reopen, then ungroups them', async () => {
  const scratch = await mkdtemp(path.join(os.tmpdir(), 'simplecad-group-'))
  const target = path.join(scratch, 'Grouped.scad3')
  try {
    const { app, page } = await launch({ env: { SIMPLECAD_ENGINE_DRILL: '1' } })
    const toolbar = page.getByRole('toolbar', { name: 'Modeling tools' })
    const bodies = page.getByRole('list', { name: 'Bodies', exact: true })
    await expect(page.getByTestId('engine-status')).toHaveText(/24000 mm³/)
    await toolbar.getByRole('button', { name: 'Shape', exact: true }).click()
    const shape = page.getByRole('form', { name: 'Create shape' })
    await shape.getByLabel('Shape type').selectOption('cylinder')
    await shape.getByRole('button', { name: 'Create', exact: true }).click()
    await expect(bodies).toContainText('Cylinder')

    await bodies.getByRole('button', { name: 'Box', exact: true }).click()
    await expect(page.getByRole('button', { name: 'Group', exact: true })).toHaveCount(0)
    await bodies.getByRole('button', { name: 'Cylinder', exact: true }).click({ modifiers: ['ControlOrMeta'] })
    await page.getByRole('button', { name: 'Group', exact: true }).click()
    const group = bodies.getByRole('list', { name: 'Group' })
    await expect(group.getByRole('button', { name: /^(Box|Cylinder)$/ })).toHaveCount(2)
    // A plain click on a member chooses the whole group.
    await group.getByRole('button', { name: 'Box', exact: true }).click()
    await expect(group.getByRole('button', { name: 'Cylinder', exact: true })).toHaveAttribute('aria-pressed', 'true')

    await toolbar.getByRole('button', { name: 'Undo', exact: true }).click()
    await expect(group).toHaveCount(0)
    await toolbar.getByRole('button', { name: 'Redo', exact: true }).click()
    await expect(group).toHaveCount(1)
    await page.evaluate(() =>
      (window as unknown as { simplecadDrill: { crashEngine(): Promise<unknown> } }).simplecadDrill.crashEngine().catch(() => {}))
    await expect(page.getByTestId('engine-connections')).toHaveText('2')
    await expect(group).toHaveCount(1)

    await app.evaluate(({ dialog }, filePath) => {
      dialog.showSaveDialog = async () => ({ filePath, canceled: false })
    }, target)
    await page.getByRole('button', { name: 'File', exact: true }).click()
    await page.getByRole('menuitem', { name: /^Save As/ }).click()
    await expect(page.getByText('Grouped.scad3', { exact: true })).toBeVisible()
    await app.close()
    const reopened = await launch({ args: [target] })
    const list = reopened.page.getByRole('list', { name: 'Bodies', exact: true })
    await expect(list.getByRole('list', { name: 'Group' }).getByRole('button', { name: /^(Box|Cylinder)$/ })).toHaveCount(2)
    await list.getByRole('button', { name: 'Ungroup Group' }).click()
    await expect(list.getByRole('list', { name: 'Group' })).toHaveCount(0)
    await expect(list).toContainText('Cylinder')
  } finally {
    await rm(scratch, { recursive: true, force: true })
  }
})

test('sends the model to the slicer it finds as a 3MF', async () => {
  test.skip(process.platform !== 'linux', 'Elsewhere the system’s 3MF handler opens it, which the test cannot stand in for.')
  const scratch = await mkdtemp(path.join(os.tmpdir(), 'simplecad-slicer-'))
  try {
    // A stand-in slicer that records the file it was handed.
    const log = path.join(scratch, 'handed.txt')
    await writeFile(path.join(scratch, 'orca-slicer'), `#!/bin/sh\nprintf '%s' "$1" > '${log}'\n`, { mode: 0o755 })
    const { page } = await launch({ env: { PATH: `${scratch}${path.delimiter}${process.env.PATH}` } })
    await expect(page.getByTestId('engine-status')).toHaveText(/24000 mm³/)
    await page.getByRole('button', { name: 'File', exact: true }).click()
    await page.getByRole('menuitem', { name: 'Send to slicer' }).click()
    await expect(page.getByRole('status', { name: 'Notice' })).toHaveText('Sent to OrcaSlicer.')
    await expect.poll(async () => (await readFile(log, 'utf8').catch(() => '')).endsWith('Untitled.3mf')).toBe(true)
    const model = await readFile(await readFile(log, 'utf8'))
    expect(model.subarray(0, 2).toString()).toBe('PK')
  } finally {
    await rm(scratch, { recursive: true, force: true })
  }
})
