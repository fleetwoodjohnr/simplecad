import { copyFile, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import type { ElectronApplication, Page } from '@playwright/test'
import { edgePoints, errors, expect, launch, scratchDir, test, command, searchCommand } from './helpers'

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

// 0.6.0's ribbon, as 0.2.0's: what starts a model or looks at it; everything else is beside the selection and in search.
test('the short ribbon keeps Shape, Sketch, Import, Measure and Print, and the selection bar offers what fits the face', async () => {
  const { page } = await launch()
  await expect(page.getByTestId('engine-status')).toHaveText(/24000 mm³/)
  const toolbar = page.getByRole('toolbar', { name: 'Modeling tools' })
  for (const group of ['Create', 'Inspect']) await expect(toolbar.getByRole('group', { name: group, exact: true })).toBeVisible()
  for (const name of ['Shape', 'Sketch', 'Import', 'Measure', 'Print check']) await expect(toolbar.getByRole('button', { name, exact: true })).toBeVisible()
  await expect(toolbar.getByRole('button', { name: 'Fillet', exact: true })).toHaveCount(0)
  await expect(page.getByRole('toolbar', { name: 'Selection tools' })).toHaveCount(0)

  await page.getByLabel('Planar face').selectOption({ label: 'Top · 1200 mm²' })
  const selection = page.getByRole('toolbar', { name: 'Selection tools' })
  await expect(selection).toBeVisible()
  await expect(selection.getByRole('button', { name: 'Hole', exact: true })).toBeEnabled()
  await command(page, 'Push/Pull')
  await expect(selection.getByLabel('Pull distance')).toBeFocused()

  await selection.getByRole('button', { name: 'More tools' }).click()
  await page.getByRole('menuitem', { name: 'Clear selection' }).click()
  await expect(selection).toHaveCount(0)
})

test('a Move drag leaves a ghost where it started, Escape puts it back, and a typed drag is one exact step', { tag: '@viewport' }, async () => {
  const scratch = await mkdtemp(path.join(os.tmpdir(), 'simplecad-ux-'))
  try {
    const { app, page } = await launch()
    const steps = page.getByRole('list', { name: 'Model history' }).locator('li')
    const canvas = page.locator('canvas')
    const reading = page.getByRole('status', { name: 'Transform reading' })
    await expect(steps).toHaveText(['Box'])
    const start = await bounds(app, page, path.join(scratch, 'start.stl'))
    const toolbar = page.getByRole('toolbar', { name: 'Modeling tools' })

    await command(page, 'Move')
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

    await command(page, 'Rotate')
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

test('dragging the face handle sweeps a preview with its distance, and Escape cancels it', { tag: '@viewport' }, async () => {
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

test('measures an edge it is clicked on, and the distance between two corners', { tag: '@viewport' }, async () => {
  const { page } = await launch()
  const toolbar = page.getByRole('toolbar', { name: 'Modeling tools' })
  const canvas = page.locator('canvas')
  await expect(page.getByTestId('engine-status')).toHaveText(/24000 mm³/)
  await toolbar.getByRole('button', { name: 'Measure', exact: true }).click()
  const panel = page.getByRole('region', { name: 'Measure' })
  const reading = (label: string) => panel.getByText(label, { exact: true }).locator('..').locator('dd')
  const anchors = () => edgePoints(page)

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

test('checks the shown bodies for printing', async () => {
  const { page } = await launch()
  await expect(page.getByTestId('engine-status')).toHaveText(/24000 mm³/)
  await page.getByRole('toolbar', { name: 'Modeling tools' }).getByRole('button', { name: 'Print check', exact: true }).click()
  const findings = page.getByRole('region', { name: 'Print check' }).getByRole('list', { name: 'Findings' }).locator('li')
  await expect(findings).toHaveText([
    'Fits the Elegoo Centauri Carbon 2: 40.0 x 30.0 x 20.0 mm.',
    'Sitting on the build plate.',
    'No significant overhangs.',
    'Solid and watertight.',
    'One connected solid.',
    'Thinnest wall 20.00 mm.',
    'No features below the nozzle size.'
  ])
})

test('centres the shown bodies on the build plate as one undo step', async () => {
  const { page } = await launch()
  await expect(page.getByTestId('engine-status')).toHaveText(/24000 mm³/)
  const undo = page.getByRole('button', { name: 'Undo', exact: true })
  await expect(undo).toBeDisabled()
  await page.getByRole('toolbar', { name: 'Modeling tools' }).getByRole('button', { name: 'Print check', exact: true }).click()
  const panel = page.getByRole('region', { name: 'Print check' })
  await panel.getByRole('button', { name: 'Center all', exact: true }).click()
  await expect(undo).toBeEnabled()
  await expect(page.getByTestId('engine-status')).toHaveText(/24000 mm³/)
  await undo.click()
  await expect(undo).toBeDisabled()
})

test('orients a part stood on end to its lowest flat lay and drops it on the plate, as one undo step', async () => {
  const { page } = await launch()
  const toolbar = page.getByRole('toolbar', { name: 'Modeling tools' })
  const steps = page.getByRole('list', { name: 'Model history' }).locator('li')
  await expect(page.getByTestId('engine-status')).toHaveText(/24000 mm³/)
  await command(page, 'Rotate')
  const rotate = page.getByRole('form', { name: 'Rotate', exact: true })
  await rotate.getByLabel('X (°)').fill('90')
  await rotate.getByRole('button', { name: 'Apply' }).click()
  await expect(steps).toHaveText(['Box', 'Rotate'])

  await toolbar.getByRole('button', { name: 'Print check', exact: true }).click()
  const panel = page.getByRole('region', { name: 'Print check' })
  const findings = panel.getByRole('list', { name: 'Findings' }).locator('li')
  await expect(findings.nth(0)).toHaveText('Fits the Elegoo Centauri Carbon 2: 40.0 x 20.0 x 30.0 mm.')
  await expect(findings.nth(1)).toHaveText(/below the build plate/)
  await panel.getByRole('button', { name: 'Orient each', exact: true }).click()
  await expect(steps).toHaveText(['Box', 'Rotate', 'Rotate2', 'Move'])
  await expect(findings.nth(0)).toHaveText('Fits the Elegoo Centauri Carbon 2: 40.0 x 30.0 x 20.0 mm.')
  await expect(findings.nth(1)).toHaveText('Sitting on the build plate.')
  await expect(page.getByTestId('engine-status')).toHaveText(/24000 mm³/)

  await toolbar.getByRole('button', { name: 'Undo', exact: true }).click()
  await expect(findings.nth(0)).toHaveText('Fits the Elegoo Centauri Carbon 2: 40.0 x 20.0 x 30.0 mm.')
  // Already in its best lay: nothing to turn, and the history is left alone.
  await toolbar.getByRole('button', { name: 'Redo', exact: true }).click()
  await panel.getByRole('button', { name: 'Orient each', exact: true }).click()
  await expect(errors(page)).toHaveText('Every part is already in the best of its six flat lays.')
})

test('saves measured fits as SimpleCAD 0.4’s printer calibration, and reads them back', async () => {
  // The helper removes it once the app has closed: Windows keeps an open profile's files locked.
  const userData = scratchDir('scad-fit-')
  {
    const { page } = await launch({ userData })
    await expect(page.getByTestId('engine-status')).toHaveText(/24000 mm³/)
    await page.getByRole('toolbar', { name: 'Modeling tools' }).getByRole('button', { name: 'Print check', exact: true }).click()
    await page.getByRole('region', { name: 'Print check' }).getByRole('button', { name: 'Fit & Clearance' }).click()
    const form = page.getByRole('form', { name: 'Fit & Clearance' })
    await expect(form.getByRole('status', { name: 'Calibration' })).toHaveText(/using shipped defaults/)
    await expect(form.getByLabel('Snug Fit (mm)')).toHaveValue('0.15')
    await form.getByLabel('Snug Fit (mm)').fill('0.22')
    await form.getByLabel('Normal thread clearance (mm, diametral)').fill('0.45')
    await form.getByRole('button', { name: 'Save measurements' }).click()
    await expect(page.getByRole('status', { name: 'Notice' })).toHaveText(/Saved/)
    await expect(errors(page)).toHaveCount(0)
    const saved = JSON.parse(await readFile(path.join(userData, 'printer-profile.json'), 'utf8'))
    expect(saved).toMatchObject({ calibrated: true, measured_fits: { snug: 0.22 }, measured_thread_clearance: 0.45 })

    await form.getByRole('button', { name: 'Close' }).click()
    await page.getByRole('region', { name: 'Print check' }).getByRole('button', { name: 'Fit & Clearance' }).click()
    await expect(form.getByRole('status', { name: 'Calibration' })).toHaveText(/using your measured values/)
    await expect(form.getByLabel('Snug Fit (mm)')).toHaveValue('0.22')
  }
})

test('adds the calibration test print as named pieces in one undoable step', async () => {
  test.setTimeout(90_000)
  const { page } = await launch()
  const toolbar = page.getByRole('toolbar', { name: 'Modeling tools' })
  const bodies = page.getByRole('list', { name: 'Bodies' })
  await expect(page.getByTestId('engine-status')).toHaveText(/24000 mm³/)
  await page.getByRole('button', { name: 'Command search' }).click()
  const search = page.getByLabel('Search commands')
  await search.fill('fit & clearance')
  await search.press('Enter')
  const form = page.getByRole('form', { name: 'Fit & Clearance' })
  await form.getByRole('button', { name: 'Add calibration model' }).click()
  await expect(bodies).toContainText('Calibration pins', { timeout: 60_000 })
  await expect(bodies).toContainText('Calibration plate')
  await expect(page.getByRole('status', { name: 'Notice' })).toHaveText(/Print both pieces/)
  await expect(errors(page)).toHaveCount(0)
  await toolbar.getByRole('button', { name: 'Undo', exact: true }).click()
  await expect(bodies).not.toContainText('Calibration')
  await expect(page.getByTestId('engine-status')).toHaveText(/24000 mm³/)
})

test('adds the P30 thread gauges as named parts in one undoable step', async () => {
  test.setTimeout(180_000)
  const { page } = await launch()
  const toolbar = page.getByRole('toolbar', { name: 'Modeling tools' })
  const bodies = page.getByRole('list', { name: 'Bodies' })
  await expect(page.getByTestId('engine-status')).toHaveText(/24000 mm³/)
  await page.getByRole('button', { name: 'Command search' }).click()
  const search = page.getByLabel('Search commands')
  await search.fill('fit & clearance')
  await search.press('Enter')
  await page.getByRole('form', { name: 'Fit & Clearance' }).getByRole('button', { name: 'Add P30 thread gauges' }).click()
  await expect(page.getByRole('status', { name: 'Notice' })).toHaveText(/turns freely/, { timeout: 150_000 })
  await expect(errors(page)).toHaveCount(0)
  for (const name of ['P30 calibration plug', 'P30 gauge 0.40 mm', 'P30 gauge 0.60 mm', 'P30 gauge 0.80 mm', 'P30 gauge 1.00 mm']) {
    await expect(bodies).toContainText(name)
  }
  await toolbar.getByRole('button', { name: 'Undo', exact: true }).click()
  await expect(bodies).not.toContainText('P30')
})

test('arrow keys nudge a selected face or chosen body as one exact step, and Escape drops a nudge', { tag: '@viewport' }, async () => {
  const { page } = await launch()
  const status = page.getByTestId('engine-status')
  const steps = page.getByRole('list', { name: 'Model history' }).locator('li')
  const canvas = page.locator('canvas')
  await expect(status).toHaveText(/24000 mm³/)

  // Up four times on the top face: one 1 mm pull, committed once the arrows are let go.
  await page.getByLabel('Planar face').selectOption({ label: 'Top · 1200 mm²' })
  await canvas.focus()
  for (let press = 0; press < 4; press++) await page.keyboard.press('ArrowUp')
  await expect(page.getByRole('status', { name: 'Notice' })).toHaveText('Adding 1.00 mm — arrows continue, Esc cancels')
  await expect(steps).toHaveText(['Box', 'Pull'])
  await expect(status).toHaveText(/25200 mm³/)

  // A chosen body steps along the one world axis nearest screen right: X or Y in the default iso view, where
  // right lies between them.
  await page.getByRole('list', { name: 'Bodies', exact: true }).getByRole('button', { name: 'Box', exact: true }).click()
  for (let press = 0; press < 2; press++) await page.keyboard.press('ArrowRight')
  await expect(steps).toHaveText(['Box', 'Pull', 'Move'])
  await page.getByRole('button', { name: 'Edit Move' }).click()
  const move = page.getByRole('form', { name: 'Move' })
  const moved = await Promise.all(['Move X', 'Move Y', 'Move Z'].map(async (label) => Number(await move.getByLabel(label).inputValue())))
  expect(moved.map(Math.abs).sort()).toEqual([0, 0, 0.5])
  expect(moved[2]).toBe(0)
  await move.getByRole('button', { name: 'Cancel' }).click()

  // Escape before the burst commits leaves the history alone.
  await page.getByRole('list', { name: 'Bodies', exact: true }).getByRole('button', { name: 'Box', exact: true }).click()
  await page.keyboard.down('ArrowLeft')
  await page.keyboard.press('Escape')
  await page.keyboard.up('ArrowLeft')
  await page.waitForTimeout(600)
  await expect(steps).toHaveText(['Box', 'Pull', 'Move'])

  // Several chosen bodies move together: a Move each, one undo step.
  const toolbar = page.getByRole('toolbar', { name: 'Modeling tools' })
  await toolbar.getByRole('button', { name: 'Shape', exact: true }).click()
  const shape = page.getByRole('form', { name: 'Create shape' })
  await shape.getByLabel('Shape type').selectOption('cylinder')
  await shape.getByRole('button', { name: 'Create', exact: true }).click()
  await expect(steps).toHaveText(['Box', 'Pull', 'Move', 'Cylinder'])
  const bodies = page.getByRole('list', { name: 'Bodies', exact: true })
  await bodies.getByRole('button', { name: 'Box', exact: true }).click()
  await bodies.getByRole('button', { name: 'Cylinder', exact: true }).click({ modifiers: ['ControlOrMeta'] })
  await page.keyboard.press('ArrowUp')
  await expect(steps).toHaveText(['Box', 'Pull', 'Move', 'Cylinder', 'Move2', 'Move3'])
  await toolbar.getByRole('button', { name: 'Undo', exact: true }).click()
  await toolbar.getByRole('button', { name: 'Undo', exact: true }).click()
  await expect(bodies).not.toContainText('Cylinder')
})

test('the wheel button pans the view, and turns it with Shift, as in 0.4; the right button turns it', { tag: '@viewport' }, async () => {
  const { page } = await launch()
  await expect(page.getByTestId('engine-status')).toHaveText(/24000 mm³/)
  const canvas = page.locator('canvas')
  await expect(canvas).toHaveAttribute('data-camera', /position/)
  const camera = async () => JSON.parse((await canvas.getAttribute('data-camera'))!) as { position: number[]; target: number[] }
  // The view frames the model once it arrives (the camera starts at the origin): start from where it settles.
  let last = ''
  await expect.poll(async () => {
    const now = (await canvas.getAttribute('data-camera'))!
    const settled = now === last && (JSON.parse(now) as { position: number[] }).position.some((v) => Math.abs(v) > 1)
    last = now
    return settled
  }, { intervals: [200], timeout: 15_000 }).toBe(true)
  const offset = (view: { position: number[]; target: number[] }) => view.position.map((v, i) => v - view.target[i])
  const box = (await canvas.boundingBox())!
  const [x, y] = [box.x + box.width / 2, box.y + box.height / 2]
  const drag = async (button: 'middle' | 'right', shift = false) => {
    const before = await camera()
    if (shift) await page.keyboard.down('Shift')
    await page.mouse.move(x, y)
    await page.mouse.down({ button })
    await page.mouse.move(x + 80, y + 30, { steps: 6 })
    await page.mouse.up({ button })
    if (shift) await page.keyboard.up('Shift')
    await expect.poll(async () => JSON.stringify(await camera())).not.toBe(JSON.stringify(before))
    return [before, await camera()]
  }
  // A pan carries the camera and the point it turns about together.
  let [before, after] = await drag('middle')
  expect(after.target).not.toEqual(before.target)
  offset(after).forEach((v, i) => expect(v).toBeCloseTo(offset(before)[i], 1))
  // An orbit swings the camera round the point it turns about, which stays.
  for (const [button, shift] of [['middle', true], ['right', false]] as const) {
    ;[before, after] = await drag(button, shift)
    expect(after.target).toEqual(before.target)
    expect(after.position).not.toEqual(before.position)
  }
  await expect(errors(page)).toHaveCount(0)
})

// SimpleCAD 0.4.4 made it: A stacked on B, then B deleted; C cut by D, then D deleted. Both keep their last good shape.
const lostInputs = path.join(__dirname, '../../../engine/scad/tests/data/keep/broken.scad3')

test('a part whose step lost its input says why beside it, and Keep where it is lets it move again', async () => {
  const copy = path.join(scratchDir('simplecad-keep-'), 'Lost inputs.scad3')
  await copyFile(lostInputs, copy)
  const { page } = await launch({ args: [copy] })
  const toolbar = page.getByRole('toolbar', { name: 'Modeling tools' })
  const bodies = page.getByRole('list', { name: 'Bodies', exact: true })
  const steps = page.getByRole('list', { name: 'Model history' }).locator('li')
  const notice = page.getByRole('region', { name: "Part can't change" })
  await expect(steps).toHaveText([/^Box/, /^Box3/, /^Align/, /^Combine/])

  await bodies.getByRole('button', { name: 'A', exact: true }).click()
  await expect(notice).toContainText("A can't change until Align is kept as it is or repaired.")
  // The bar offers Keep and Repair in place of the tools that would change it, and search refuses them.
  await expect(page.getByRole('toolbar', { name: 'Selection tools' })).toHaveCount(0)
  await expect(await searchCommand(page, 'Move')).toBeDisabled()
  await page.keyboard.press('Escape')
  await notice.getByRole('button', { name: 'Keep where it is' }).click()
  await expect(steps.nth(2)).toContainText('Align (kept)')
  await expect(notice).toHaveCount(0)
  await expect(errors(page)).toHaveCount(0)

  await bodies.getByRole('button', { name: 'A', exact: true }).click()
  await expect(notice).toHaveCount(0)
  await command(page, 'Move')
  const move = page.getByRole('form', { name: 'Move', exact: true })
  await move.getByLabel('X (mm)').fill('5')
  await move.getByRole('button', { name: 'Apply' }).click()
  await expect(steps.last()).toContainText('Move')
  await expect(errors(page)).toHaveCount(0)

  // A face of the blocked part, picked while another part is chosen, still refuses the tools that would change it.
  await bodies.getByRole('button', { name: 'A', exact: true }).click()
  await page.getByLabel('Planar face').selectOption({ label: (await page.getByLabel('Planar face').locator('option', { hasText: /^C · / }).first().textContent())! })
  await expect(notice).toContainText("C can't change until Combine")
  await expect(await searchCommand(page, 'Move')).toBeDisabled()
  await page.keyboard.press('Escape')

  // The cut part offers to keep its shape, or to choose a new cutter.
  await bodies.getByRole('button', { name: 'C', exact: true }).click()
  await expect(notice.getByRole('button', { name: 'Keep this shape' })).toBeVisible()
  await notice.getByRole('button', { name: 'Repair input' }).click()
  await expect(page.getByRole('form', { name: 'Repair Combine' })).toBeVisible()
})

// SimpleCAD 0.4.4's right click: it chooses what is under the pointer and offers what can be done to it.
test('a right click picks the face under it and offers its tools, Edit and Delete; a right drag still turns the view', { tag: '@viewport' }, async () => {
  const { page } = await launch()
  const status = page.getByTestId('engine-status')
  await expect(status).toHaveText(/24000 mm³/)
  const canvas = page.locator('canvas')
  await expect(canvas).toHaveAttribute('aria-busy', 'false')
  const box = (await canvas.boundingBox())!
  const [x, y] = [box.x + box.width / 2, box.y + box.height / 2]

  await page.mouse.click(x, y, { button: 'right' })
  const menu = page.getByRole('menu', { name: 'Face of Box' })
  await expect(menu).toBeVisible()
  await expect(page.getByRole('toolbar', { name: 'Selection tools' })).toBeVisible()
  await expect(menu.getByRole('menuitem', { name: 'Move' })).toBeVisible()
  await expect(menu.getByRole('menuitem', { name: 'Edit Box' })).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(menu).toHaveCount(0)

  await page.mouse.click(x, y, { button: 'right' })
  await page.getByRole('menu', { name: 'Face of Box' }).getByRole('menuitem', { name: 'Edit Box' }).click()
  await expect(page.getByRole('form', { name: 'Edit Box' })).toBeVisible()
  await page.getByRole('form', { name: 'Edit Box' }).getByRole('button', { name: 'Cancel' }).click()

  await page.mouse.click(x, y, { button: 'right' })
  await page.getByRole('menu', { name: 'Face of Box' }).getByRole('menuitem', { name: 'Delete Box' }).click()
  await expect(page.getByRole('list', { name: 'Bodies', exact: true }).getByRole('button', { name: 'Box', exact: true })).toHaveCount(0)
  await page.getByRole('toolbar', { name: 'Modeling tools' }).getByRole('button', { name: 'Undo', exact: true }).click()
  await expect(status).toHaveText(/24000 mm³/)

  // A right drag turns the view and opens nothing.
  const before = await canvas.getAttribute('data-camera')
  await page.mouse.move(x, y)
  await page.mouse.down({ button: 'right' })
  await page.mouse.move(x + 80, y + 30, { steps: 6 })
  await page.mouse.up({ button: 'right' })
  await expect.poll(() => canvas.getAttribute('data-camera')).not.toBe(before)
  await expect(page.getByRole('menu')).toHaveCount(0)
  await expect(errors(page)).toHaveCount(0)
})

test('a right click, or the menu key, on a body or a step offers what can be done to it', async () => {
  const { page } = await launch()
  await expect(page.getByTestId('engine-status')).toHaveText(/24000 mm³/)
  const bodies = page.getByRole('list', { name: 'Bodies', exact: true })
  const row = bodies.getByRole('button', { name: 'Box', exact: true })

  await row.click({ button: 'right' })
  const menu = page.getByRole('menu', { name: 'Box' })
  await expect(menu.getByRole('menuitem', { name: 'Move' })).toBeVisible()
  await menu.getByRole('menuitem', { name: 'Hide Box' }).click()
  await expect(bodies.getByRole('button', { name: 'Show Box' })).toBeVisible()

  // From the keyboard: the menu key on the focused row, then the arrows and Enter.
  await row.focus()
  await page.keyboard.press('Shift+F10')
  await expect(page.getByRole('menu', { name: 'Box' })).toBeVisible()
  await page.getByRole('menu', { name: 'Box' }).getByRole('menuitem', { name: 'Show Box' }).focus()
  await page.keyboard.press('Enter')
  await expect(bodies.getByRole('button', { name: 'Hide Box' })).toBeVisible()

  // The Menu key on a step's focused Edit button, then the arrow key to its first item.
  await page.getByRole('button', { name: 'Edit Box' }).focus()
  await page.keyboard.press('ContextMenu')
  await expect(page.getByRole('menu', { name: 'Box' }).getByRole('menuitem', { name: 'Edit Box' })).toBeVisible()
  await page.keyboard.press('Escape')
  await page.getByRole('list', { name: 'Model history' }).locator('li').first().click({ button: 'right' })
  await page.getByRole('menu', { name: 'Box' }).getByRole('menuitem', { name: 'Edit Box' }).click()
  await expect(page.getByRole('form', { name: 'Edit Box' })).toBeVisible()
  await expect(errors(page)).toHaveCount(0)
})

/** Where part `index` shows in the window (the view publishes each part's centre on small models). */
async function partAt(page: Page, index: number): Promise<Point> {
  const canvas = page.locator('canvas')
  await expect(canvas).toHaveAttribute('data-body-anchors', new RegExp(`"${index}"`))
  await page.waitForTimeout(200)
  // Beside the face's pull handle, which sits on the published point once the face is chosen.
  const [x, y] = JSON.parse((await canvas.getAttribute('data-body-anchors'))!)[index] as [number, number]
  return [x + 20, y]
}

/** A 20 × 20 × 10 box beside the first, `x` mm along. */
async function addBox(page: Page, x: string) {
  const toolbar = page.getByRole('toolbar', { name: 'Modeling tools' })
  await toolbar.getByRole('button', { name: 'Shape', exact: true }).click()
  const shape = page.getByRole('form', { name: 'Create shape' })
  await shape.getByLabel('Shape type').selectOption('box')
  for (const [label, value] of [['Width', '20'], ['Depth', '20'], ['Height', '10']]) await shape.getByLabel(label).fill(value)
  await shape.getByRole('button', { name: 'Create', exact: true }).click()
  await command(page, 'Move')
  const move = page.getByRole('form', { name: 'Move', exact: true })
  await move.getByLabel('Body').selectOption({ index: (await move.getByLabel('Body').locator('option').count()) - 1 })
  await move.getByLabel('X (mm)').fill(x)
  await move.getByRole('button', { name: 'Apply' }).click()
  await page.keyboard.press('Escape')
}

// 0.2.0's way: a click takes the part, and the arrows and rings are already on it; letting go is one step.
test('a click takes the part with its arrows and rings; dragging one moves or turns it as one step', { tag: '@viewport' }, async () => {
  const scratch = await mkdtemp(path.join(os.tmpdir(), 'simplecad-ux-'))
  try {
    const { app, page } = await launch()
    const steps = page.getByRole('list', { name: 'Model history' }).locator('li')
    const bodies = page.getByRole('list', { name: 'Bodies', exact: true })
    const canvas = page.locator('canvas')
    await expect(steps).toHaveText(['Box'])
    const start = await bounds(app, page, path.join(scratch, 'start.stl'))

    const [cx, cy] = await partAt(page, 0)
    await page.mouse.click(cx, cy)
    await expect(bodies.getByRole('button', { name: 'Box', exact: true })).toHaveAttribute('aria-pressed', 'true')
    await expect(page.getByRole('toolbar', { name: 'Selection tools' })).toBeVisible()
    await expect(canvas).toHaveAttribute('data-rings', /"z"/)

    let [x, y] = await handle(page, 'x')
    await page.mouse.move(x, y)
    await page.mouse.down()
    await page.mouse.move(x + 30, y + 8, { steps: 4 })
    await expect(canvas).toHaveAttribute('data-ghost', 'move')
    await page.keyboard.type('10')
    await page.mouse.up()
    await expect(steps).toHaveText(['Box', 'Move'])
    const moved = await bounds(app, page, path.join(scratch, 'moved.stl'))
    moved.forEach((value, axis) => expect(value).toBeCloseTo(start[axis] + (axis % 3 === 0 ? 10 : 0), 4))

    // The part stays chosen after the step, so the next drag needs no click.
    await expect(canvas).toHaveAttribute('data-rings', /"z"/)
    await page.waitForTimeout(200)
    ;[x, y] = JSON.parse((await canvas.getAttribute('data-rings'))!).z as Point
    await page.mouse.move(x, y)
    await page.mouse.down()
    await page.mouse.move(x + 20, y + 6, { steps: 4 })
    await expect(canvas).toHaveAttribute('data-ghost', 'rotate')
    await page.keyboard.type('90')
    await page.mouse.up()
    await expect(steps).toHaveText(['Box', 'Move', 'Rotate'])
    const turned = await bounds(app, page, path.join(scratch, 'turned.stl'))
    expect(turned[3] - turned[0]).toBeCloseTo(start[4] - start[1], 4)
    expect(turned[4] - turned[1]).toBeCloseTo(start[3] - start[0], 4)

    // A click on empty space lets go of it.
    const box = (await canvas.boundingBox())!
    await page.mouse.click(box.x + 6, box.y + 6)
    await expect(bodies.getByRole('button', { name: 'Box', exact: true })).toHaveAttribute('aria-pressed', 'false')
    await expect(canvas).not.toHaveAttribute('data-handles', /./)
    await expect(errors(page)).toHaveCount(0)
  } finally {
    await rm(scratch, { recursive: true, force: true })
  }
})

// The Windows and macOS runners' small screens give an 800 × 560 view, where the bar beside the face covered the X
// arrow and the part's middle.
test('in a small window the selection bar stays off the arrows, the rings and the middle of the part', { tag: '@viewport' }, async () => {
  const { page } = await launch({ env: { SIMPLECAD_WINDOW: '1024x700' } })
  const steps = page.getByRole('list', { name: 'Model history' }).locator('li')
  const canvas = page.locator('canvas')
  await expect(steps).toHaveText(['Box'])
  expect((await canvas.boundingBox())!.height).toBeLessThan(620)
  await page.mouse.click(...await partAt(page, 0))
  const bar = page.getByRole('toolbar', { name: 'Selection tools' })
  await expect(bar).toBeVisible()
  await expect(canvas).toHaveAttribute('data-rings', /"z"/)
  await page.waitForTimeout(200)
  const area = (await bar.boundingBox())!
  const view = (await canvas.boundingBox())!
  const points: Point[] = [...Object.values(JSON.parse((await canvas.getAttribute('data-handles'))!) as Record<string, Point>),
    ...Object.values(JSON.parse((await canvas.getAttribute('data-rings'))!) as Record<string, Point>),
    [view.x + view.width / 2, view.y + view.height / 2]]
  for (const [x, y] of points) {
    expect(x >= area.x && x <= area.x + area.width && y >= area.y && y <= area.y + area.height, `${x},${y} clear of the bar`).toBe(false)
  }
  const [x, y] = await handle(page, 'x')
  await page.mouse.move(x, y)
  await page.mouse.down()
  await page.mouse.move(x + 30, y + 8, { steps: 4 })
  await expect(canvas).toHaveAttribute('data-ghost', 'move')
  await page.keyboard.press('Escape')
  await page.mouse.up()
  await expect(steps).toHaveText(['Box'])
  await expect(errors(page)).toHaveCount(0)
})

// History stays clickable while a step runs (disabling hundreds of rows on each step was slow on large projects).
test('Edit chosen in History while a step runs opens once that step is done', async () => {
  const { page } = await launch()
  const status = page.getByTestId('engine-status')
  await expect(status).toContainText('24000 mm³')
  await page.getByLabel('Planar face').selectOption({ label: 'Top · 1200 mm²' })
  await page.getByLabel('Pull distance').fill('2')
  await page.getByLabel('Pull distance').press('Enter')
  await expect(status).toContainText('26400 mm³')
  // In one go, so Edit arrives while the Undo still runs.
  await page.evaluate(() => {
    const button = (selector: string) => document.querySelector<HTMLButtonElement>(selector)!
    button('[role=toolbar][aria-label="Modeling tools"] [aria-label="Undo"]').click()
    button('[aria-label="Model history"] [aria-label="Edit Box"]').click()
  })
  await expect(status).toContainText('24000 mm³')
  await expect(page.getByRole('form', { name: 'Edit Box' })).toBeVisible()
  await expect(errors(page)).toHaveCount(0)
})

test('a box from empty space takes what it surrounds, or right to left what it touches; Shift adds; a group is one click and a double-click reaches in, then edits', { tag: '@viewport' }, async () => {
  const { page } = await launch()
  const steps = page.getByRole('list', { name: 'Model history' }).locator('li')
  const bodies = page.getByRole('list', { name: 'Bodies', exact: true })
  const chosen = (name: string) => bodies.getByRole('button', { name, exact: true })
  const canvas = page.locator('canvas')
  await expect(steps).toHaveText(['Box'])
  await addBox(page, '45')
  await expect(steps).toHaveText(['Box', 'Box2', 'Move'])
  await page.getByRole('button', { name: 'Fit view' }).click()
  await page.locator('canvas').click({ position: { x: 6, y: 6 } })
  const area = (await canvas.boundingBox())!

  // Left to right, round everything: both.
  await page.mouse.move(area.x + 6, area.y + 6)
  await page.mouse.down()
  await page.mouse.move(area.x + area.width - 6, area.y + area.height - 6, { steps: 6 })
  await expect(page.getByTestId('selection-box')).toBeVisible()
  await page.mouse.up()
  await expect(chosen('Box')).toHaveAttribute('aria-pressed', 'true')
  await expect(chosen('Box2')).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByTestId('selection-box')).toBeHidden()
  // Several parts carry the arrows only, and move together.
  await expect(canvas).toHaveAttribute('data-handles', /"x"/)
  await expect(canvas).not.toHaveAttribute('data-rings', /./)

  // Right to left, from empty space on the right to the second part's middle: only what it touches.
  const [bx, by] = await partAt(page, 1)
  await page.mouse.click(area.x + 6, area.y + 6)
  await page.mouse.move(area.x + area.width - 6, by - 4)
  await page.mouse.down()
  await page.mouse.move(bx, by + 4, { steps: 4 })
  await page.mouse.up()
  await expect(chosen('Box')).toHaveAttribute('aria-pressed', 'false')
  await expect(chosen('Box2')).toHaveAttribute('aria-pressed', 'true')

  // Shift-click adds the first part, and a second Shift-click takes it away again.
  const [ax, ay] = await partAt(page, 0)
  await page.keyboard.down('Shift')
  await page.mouse.click(ax, ay)
  await expect(chosen('Box')).toHaveAttribute('aria-pressed', 'true')
  await expect(chosen('Box2')).toHaveAttribute('aria-pressed', 'true')
  await page.mouse.click(ax, ay)
  await page.keyboard.up('Shift')
  await expect(chosen('Box')).toHaveAttribute('aria-pressed', 'false')

  // Group the two: a click on one takes both; a double-click reaches inside; another opens the step that made it.
  await page.keyboard.down('Shift')
  await page.mouse.click(ax, ay)
  await page.keyboard.up('Shift')
  await page.getByRole('button', { name: 'Group', exact: true }).click()
  // The Group step's result resets the selection, so the clicks wait for it.
  await expect(bodies.getByRole('list', { name: 'Group' })).toBeVisible()
  await page.mouse.click(area.x + 6, area.y + 6)
  await page.mouse.click(ax, ay)
  await expect(chosen('Box')).toHaveAttribute('aria-pressed', 'true')
  await expect(chosen('Box2')).toHaveAttribute('aria-pressed', 'true')
  await page.mouse.dblclick(ax, ay)
  await expect(chosen('Box')).toHaveAttribute('aria-pressed', 'true')
  await expect(chosen('Box2')).toHaveAttribute('aria-pressed', 'false')
  await page.mouse.dblclick(ax, ay)
  await expect(page.getByRole('form', { name: 'Edit Box' })).toBeVisible()
  await expect(errors(page)).toHaveCount(0)
})

test('a part that cannot change gets no arrows until it is kept', { tag: '@viewport' }, async () => {
  const copy = path.join(scratchDir('simplecad-keep-'), 'Lost inputs.scad3')
  await copyFile(lostInputs, copy)
  const { page } = await launch({ args: [copy] })
  const bodies = page.getByRole('list', { name: 'Bodies', exact: true })
  const canvas = page.locator('canvas')
  const notice = page.getByRole('region', { name: "Part can't change" })
  await bodies.getByRole('button', { name: 'A', exact: true }).click()
  await expect(notice).toBeVisible()
  await page.waitForTimeout(300)
  await expect(canvas).not.toHaveAttribute('data-handles', /./)
  await notice.getByRole('button', { name: 'Keep where it is' }).click()
  await expect(notice).toHaveCount(0)
  await bodies.getByRole('button', { name: 'A', exact: true }).click()
  await expect(canvas).toHaveAttribute('data-handles', /"x"/)
})

// 0.2.0's bar: whatever is selected, its likeliest tool leads, the rest behind ⋯, and the right-click menu agrees.
test('the bar beside the selection fits what is chosen: a face and its part, two parts, a group, three parts', async () => {
  const { page } = await launch()
  const steps = page.getByRole('list', { name: 'Model history' }).locator('li')
  const bodies = page.getByRole('list', { name: 'Bodies', exact: true })
  const bar = page.getByRole('toolbar', { name: 'Selection tools' })
  const first = () => bar.getByRole('button').first()
  await expect(steps).toHaveText(['Box'])

  // A face: its pull distance and tools, then its part's.
  await page.getByLabel('Planar face').selectOption({ label: 'Top · 1200 mm²' })
  await expect(bar.getByLabel('Pull distance')).toBeVisible()
  for (const name of ['Hole', 'Sketch', 'Text', 'Shell', 'Move', 'Rotate', 'Scale', 'Split', 'Hide', 'Delete']) {
    await expect(bar.getByRole('button', { name, exact: true })).toBeVisible()
  }
  await expect(bodies.getByRole('button', { name: 'Box', exact: true })).toHaveAttribute('aria-pressed', 'true')

  await addBox(page, '45')
  await addBox(page, '80')
  await expect(steps).toHaveText(['Box', 'Box2', 'Move', 'Box3', 'Move2'])

  // Two parts: Subtract leads, and opens Combine set to cut the first with the second.
  await bodies.getByRole('button', { name: 'Box', exact: true }).click()
  await bodies.getByRole('button', { name: 'Box2', exact: true }).click({ modifiers: ['ControlOrMeta'] })
  await expect(first()).toHaveAccessibleName('Subtract')
  for (const name of ['Join', 'Intersect', 'Group', 'Stack & Place', 'Clip Joint']) await expect(bar.getByRole('button', { name, exact: true })).toBeVisible()
  await bodies.getByRole('button', { name: 'Box2', exact: true }).click({ button: 'right' })
  await expect(page.getByRole('menu', { name: 'Box2' }).getByRole('menuitem', { name: 'Subtract' })).toBeVisible()
  await page.keyboard.press('Escape')
  await first().click()
  const combine = page.getByRole('form', { name: 'Combine' })
  await expect(combine.getByRole('radio', { name: 'Cut' })).toBeChecked()
  await expect(combine.getByRole('checkbox', { name: 'Box2' })).toBeChecked()
  await expect(combine.getByLabel('Body')).toHaveValue('0')
  await combine.getByRole('button', { name: 'Cancel' }).click()

  // Grouped, one click takes the group, and Ungroup leads.
  await bodies.getByRole('button', { name: 'Box', exact: true }).click()
  await bodies.getByRole('button', { name: 'Box2', exact: true }).click({ modifiers: ['ControlOrMeta'] })
  await bar.getByRole('button', { name: 'Group', exact: true }).click()
  const group = bodies.getByRole('list', { name: 'Group' })
  await expect(group).toHaveCount(1)
  await bodies.getByRole('button', { name: 'Box', exact: true }).click()
  await expect(first()).toHaveAccessibleName('Ungroup')
  await first().click()
  await expect(group).toHaveCount(0)

  // Three parts: Arrange leads.
  await bodies.getByRole('button', { name: 'Box', exact: true }).click()
  await bodies.getByRole('button', { name: 'Box2', exact: true }).click({ modifiers: ['ControlOrMeta'] })
  await bodies.getByRole('button', { name: 'Box3', exact: true }).click({ modifiers: ['ControlOrMeta'] })
  await expect(first()).toHaveAccessibleName('Arrange')
  await first().click()
  await expect(page.getByRole('form', { name: 'Arrange' })).toBeVisible()
  await expect(errors(page)).toHaveCount(0)
})

/** The first edge anchor the view publishes that is clicked as an edge (not the face beside it). */
async function clickEdge(page: Page) {
  const bar = page.getByRole('toolbar', { name: 'Selection tools' })
  for (const [x, y] of await edgePoints(page)) {
    await page.mouse.click(x, y)
    if (await bar.getByRole('button', { name: 'Fillet', exact: true }).count() &&
      !(await bar.getByLabel('Pull distance').count())) return [x, y] as Point
    await page.mouse.click(5 + (await page.locator('canvas').boundingBox())!.x, 5 + (await page.locator('canvas').boundingBox())!.y)
  }
  throw new Error('No edge could be clicked')
}

// 0.2.0's handles: a clicked edge carries a handle that rounds it, and letting go is one step.
test('a clicked edge offers Fillet, Chamfer and Measure, and its handle rounds it when dragged', { tag: '@viewport' }, async () => {
  const { page } = await launch()
  const steps = page.getByRole('list', { name: 'Model history' }).locator('li')
  const status = page.getByTestId('engine-status')
  await expect(status).toHaveText(/24000 mm³/)
  const bar = page.getByRole('toolbar', { name: 'Selection tools' })
  await clickEdge(page)
  await expect(bar.getByRole('button').first()).toHaveAccessibleName('Fillet')
  await expect(bar.getByRole('button', { name: 'Chamfer', exact: true })).toBeVisible()
  await expect(bar.getByRole('button', { name: 'Measure', exact: true })).toBeVisible()
  await expect(page.locator('canvas')).toHaveAttribute('data-picked-edges', '1')

  const handle = page.getByRole('button', { name: 'Drag to round the edge' })
  const box = (await handle.boundingBox())!
  const [x, y] = [box.x + box.width / 2, box.y + box.height / 2]
  await page.mouse.move(x, y)
  await page.mouse.down()
  await page.mouse.move(x + 25, y + 25, { steps: 5 })
  await expect(page.getByRole('status', { name: 'Handle reading' })).toContainText(/^R \d/)
  // Escape drops the drag; nothing changes.
  await page.keyboard.press('Escape')
  await page.mouse.up()
  await expect(steps).toHaveText(['Box'])

  await page.mouse.move(x, y)
  await page.mouse.down()
  await page.mouse.move(x + 25, y + 25, { steps: 5 })
  const reading = (await page.getByRole('status', { name: 'Handle reading' }).textContent())!
  await page.mouse.up()
  await expect(steps).toHaveText(['Box', 'Fillet'])
  // A round takes material away; the radius snapped to 0.25 mm.
  expect(Number(reading.match(/R ([\d.]+)/)![1]) * 4 % 1).toBe(0)
  await expect.poll(async () => Number((await status.textContent())!.match(/([\d.]+) mm³/)![1])).toBeLessThan(24000)
  await expect(errors(page)).toHaveCount(0)
})

test('Chamfer from a clicked edge starts with that edge picked', { tag: '@viewport' }, async () => {
  const { page } = await launch()
  const steps = page.getByRole('list', { name: 'Model history' }).locator('li')
  await expect(page.getByTestId('engine-status')).toHaveText(/24000 mm³/)
  await clickEdge(page)
  await page.getByRole('toolbar', { name: 'Selection tools' }).getByRole('button', { name: 'Chamfer', exact: true }).click()
  const chamfer = page.getByRole('form', { name: 'Chamfer' })
  await expect(chamfer.getByRole('status', { name: 'Picked edges' })).toHaveText('1 edge of Box')
  await chamfer.getByLabel('Distance').fill('2 mm')
  await chamfer.getByRole('button', { name: 'Apply' }).click()
  await expect(steps).toHaveText(['Box', 'Chamfer'])
})

test('Split shows its plane on the part; dragging a handle moves it, reading both halves', { tag: '@viewport' }, async () => {
  const { page } = await launch()
  const steps = page.getByRole('list', { name: 'Model history' }).locator('li')
  const bodies = page.getByRole('list', { name: 'Bodies', exact: true })
  await expect(page.getByTestId('engine-status')).toHaveText(/24000 mm³/)
  await bodies.getByRole('button', { name: 'Box', exact: true }).click()
  await page.getByRole('toolbar', { name: 'Selection tools' }).getByRole('button', { name: 'Split', exact: true }).click()
  const form = page.getByRole('form', { name: 'Split' })
  const offset = form.getByLabel('Offset from the centre')
  await expect(offset).toHaveValue('0')
  const handle = page.getByRole('button', { name: 'Drag the split plane' })
  const box = (await handle.boundingBox())!
  const [x, y] = [box.x + box.width / 2, box.y + box.height / 2]
  await page.mouse.move(x, y)
  await page.mouse.down()
  await page.mouse.move(x, y - 30, { steps: 5 })
  // The Box is 20 mm high: the readout is the two heights, which add up to it.
  const reading = (await page.getByRole('status', { name: 'Handle reading' }).textContent())!
  const [a, b] = reading.split('|').map((part) => Number(part.match(/[\d.]+/)![0]))
  expect(a + b).toBeCloseTo(20, 6)
  expect(a).toBeGreaterThan(10)
  await page.mouse.up()
  await expect(offset).not.toHaveValue('0')
  expect(Number(await offset.inputValue())).toBeCloseTo(a - 10, 6)
  await form.getByRole('button', { name: 'Apply' }).click()
  await expect(steps).toHaveText(['Box', 'Split'])
  await expect(bodies.getByRole('button', { name: /^Box/ })).toHaveCount(2)
})
