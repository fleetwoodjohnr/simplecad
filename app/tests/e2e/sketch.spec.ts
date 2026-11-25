import { mkdtemp, readFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { edgePoints, errors, expect, launch, test, command, more } from './helpers'

test('draws and dimensions a sketch, then edits its extrusion through history and reopen', async () => {
  const scratch = await mkdtemp(path.join(tmpdir(), 'simplecad-sketch-'))
  const filePath = path.join(scratch, 'Dimensioned Sketch.scad3')
  const { app, page } = await launch({ env: { SIMPLECAD_ENGINE_DRILL: '1' } })
  try {
    const toolbar = page.getByRole('toolbar', { name: 'Modeling tools' })
    const status = page.getByTestId('engine-status')
    await expect(status).toContainText('24000 mm³')
    await page.getByRole('button', { name: 'Hide Box', exact: true }).click()
    await toolbar.getByRole('button', { name: 'Sketch', exact: true }).click()
    const editor = page.getByRole('dialog', { name: 'Sketch editor' })
    await editor.getByRole('button', { name: 'Circle', exact: true }).click()
    const drawing = editor.getByLabel('Sketch drawing', { exact: true })
    const canvas = (await drawing.boundingBox())!
    await drawing.click({ position: { x: canvas.width / 2, y: canvas.height / 2 } })
    await drawing.click({ position: { x: canvas.width * 0.55, y: canvas.height / 2 } })
    await editor.getByLabel('Constraint', { exact: true }).selectOption('radius')
    await editor.getByLabel('Round curve 1').selectOption({ label: 'Circle 1' })
    await editor.getByLabel('Dimension', { exact: true }).fill('2.5 mm * 2')
    await editor.getByRole('button', { name: 'Add constraint' }).click()
    await expect(editor.getByLabel('Constraint status')).toHaveText('2 degrees of freedom remaining.')
    await editor.getByRole('button', { name: 'Save sketch' }).click()
    await expect(editor).toBeHidden()
    await command(page, 'Extrude')
    const tool = page.getByRole('form', { name: 'Extrude' })
    await tool.getByLabel('Distance').fill('5 mm * 2')
    await expect(page.getByTestId('engine-status')).toHaveAttribute('data-preview', 'shown')
    await tool.getByRole('button', { name: 'Apply' }).click()
    await expect(status).toContainText('785.4 mm³')

    await page.getByRole('button', { name: 'Edit Sketch', exact: true }).click()
    const editing = page.getByRole('dialog', { name: 'Edit Sketch' })
    await expect(editing.getByLabel('Dimension 1')).toHaveValue('2.5 mm * 2')
    await editing.getByLabel('Dimension 1').fill('3 mm * 2')
    await editing.getByRole('button', { name: 'Save sketch' }).click()
    await expect(status).toContainText('1130.97 mm³')
    await toolbar.getByRole('button', { name: 'Undo', exact: true }).click()
    await expect(status).toContainText('785.4 mm³')
    await toolbar.getByRole('button', { name: 'Redo', exact: true }).click()
    await expect(status).toContainText('1130.97 mm³')
    await page.evaluate(() => (window as unknown as { simplecadDrill: { crashEngine(): Promise<unknown> } })
      .simplecadDrill.crashEngine().catch(() => {}))
    await expect(page.getByTestId('engine-connections')).toHaveText('2')
    await expect(status).toContainText('1130.97 mm³')

    await app.evaluate(({ dialog }, filePath) => { dialog.showSaveDialog = async () => ({ filePath, canceled: false }) }, filePath)
    await toolbar.getByRole('button', { name: 'File', exact: true }).click()
    await page.getByRole('menuitem', { name: /^Save As/ }).click()
    await expect(page.getByText('Dimensioned Sketch.scad3', { exact: true })).toBeVisible()
    const reopened = await launch({ args: [filePath] })
    await expect(reopened.page.getByTestId('engine-status')).toContainText('25130.97 mm³')
    await reopened.page.getByRole('button', { name: 'Edit Sketch', exact: true }).click()
    await expect(reopened.page.getByLabel('Dimension 1')).toHaveValue('3 mm * 2')
  } finally {
    await rm(scratch, { force: true, recursive: true })
  }
})

test('creates Loft, Sweep, Revolve and a path pattern from drawn sketches', async () => {
  const { page } = await launch()
  const toolbar = page.getByRole('toolbar', { name: 'Modeling tools' })
  const status = page.getByTestId('engine-status')
  await expect(status).toContainText('24000 mm³')
  await page.getByRole('button', { name: 'Hide Box', exact: true }).click()

  async function sketch(tool: 'Circle' | 'Line', points: [number, number][], plane = 'XY', offset = '0') {
    await toolbar.getByRole('button', { name: 'Sketch', exact: true }).click()
    const editor = page.getByRole('dialog', { name: 'Sketch editor' })
    await editor.getByLabel('Sketch plane', { exact: true }).selectOption(plane)
    await editor.getByLabel('Plane offset (mm)').fill(offset)
    await editor.getByRole('button', { name: tool, exact: true }).click()
    const drawing = editor.getByLabel('Sketch drawing')
    const { width, height } = (await drawing.boundingBox())!
    for (const [x, y] of points) {
      await drawing.click({ position: { x: width * (0.5 + x / 100), y: height * (0.5 - y / 70) } })
    }
    await editor.getByRole('button', { name: 'Save sketch' }).click()
    await expect(editor).toBeHidden()
  }

  async function openTool(name: string) {
    await command(page, name)
    return page.getByRole('form', { name, exact: true })
  }

  await sketch('Circle', [[0, 0], [5, 0]])
  await sketch('Circle', [[0, 0], [4, 0]], 'XY', '20')
  const loft = await openTool('Loft')
  await loft.getByLabel('Section 1').selectOption({ label: 'Sketch' })
  await loft.getByLabel('Section 2').selectOption({ label: 'Sketch2' })
  await more(loft)
  await loft.getByLabel('Ruled').check()
  await loft.getByRole('button', { name: 'Apply' }).click()
  await expect(status).toContainText('1277.58 mm³')

  await sketch('Line', [[0, 0], [0, 20]], 'XZ')
  const sweep = await openTool('Sweep')
  await sweep.getByLabel('Profile sketch').selectOption({ label: 'Sketch' })
  await sweep.getByLabel('Path sketch').selectOption({ label: 'Sketch3' })
  await sweep.getByRole('button', { name: 'Apply' }).click()
  await expect(status).toContainText('2848.38 mm³')

  await sketch('Circle', [[0, 15], [4, 15]])
  const revolve = await openTool('Revolve')
  await revolve.getByLabel('Profile sketch').selectOption({ label: 'Sketch4' })
  await revolve.getByRole('button', { name: 'Apply' }).click()
  const expected = Math.PI * 20 / 3 * 61 + Math.PI * 25 * 20 + 2 * Math.PI ** 2 * 15 * 16
  await expect(status).toContainText(`${Number(expected.toFixed(2))} mm³`)

  const pattern = await openTool('Pattern Along Path')
  await pattern.getByLabel('Path sketch').selectOption({ label: 'Sketch3' })
  await pattern.getByLabel('Body', { exact: true }).selectOption('Sweep')
  await pattern.getByLabel('Instances').fill('2')
  await pattern.getByRole('button', { name: 'Apply' }).click()
  await expect(status).toContainText(`${Number((expected + Math.PI * 25 * 20).toFixed(2))} mm³`)
  await toolbar.getByRole('button', { name: 'Undo', exact: true }).click()
  await expect(status).toContainText(`${Number(expected.toFixed(2))} mm³`)
})

test('sketches and extrudes on a construction plane saved by v0.4.4', async () => {
  const fixture = path.resolve('tests/e2e/fixtures/construction-plane.scad3')
  const scratch = await mkdtemp(path.join(tmpdir(), 'simplecad-construction-plane-'))
  const target = path.join(scratch, 'Extrusion.stl')
  try {
    const { app, page } = await launch({ args: [fixture] })
    const toolbar = page.getByRole('toolbar', { name: 'Modeling tools' })
    await expect(page.getByTestId('engine-status')).toContainText('8000 mm³')
    await page.getByRole('button', { name: 'Hide Plate', exact: true }).click()
    await toolbar.getByRole('button', { name: 'Sketch', exact: true }).click()
    const editor = page.getByRole('dialog', { name: 'Sketch editor' })
    await editor.getByLabel('Sketch plane', { exact: true }).selectOption({ label: 'Raised workplane' })
    await editor.getByLabel('Plane offset (mm)').fill('2')
    await editor.getByRole('button', { name: 'Rectangle', exact: true }).click()
    const drawing = editor.getByLabel('Sketch drawing')
    const { width, height } = (await drawing.boundingBox())!
    await drawing.click({ position: { x: width * 0.4, y: height * 0.4 } })
    await drawing.click({ position: { x: width * 0.6, y: height * 0.6 } })
    await editor.getByRole('button', { name: 'Save sketch' }).click()
    await expect(editor).toBeHidden()

    await command(page, 'Extrude')
    const tool = page.getByRole('form', { name: 'Extrude' })
    await tool.getByRole('button', { name: 'Apply' }).click()
    await expect(page.getByRole('list', { name: 'Model history' })).toContainText('Extrude')

    await app.evaluate(({ dialog }, filePath) => {
      dialog.showSaveDialog = async () => ({ filePath, canceled: false })
    }, target)
    await toolbar.getByRole('button', { name: 'File', exact: true }).click()
    await page.getByRole('menuitem', { name: 'Export STL' }).click()
    await expect.poll(async () => (await readFile(target).catch(() => Buffer.alloc(0))).length).toBeGreaterThan(84)
    const bytes = await readFile(target)
    const elevations = []
    for (let offset = 84; offset + 50 <= bytes.length; offset += 50) {
      for (let vertex = offset + 12; vertex < offset + 48; vertex += 12) {
        elevations.push(bytes.readFloatLE(vertex + 8))
      }
    }
    expect(Math.min(...elevations)).toBeCloseTo(37, 5)
    expect(Math.max(...elevations)).toBeCloseTo(47, 5)
  } finally {
    await rm(scratch, { recursive: true, force: true })
  }
})

test('creates and edits an offset plane through history and a worker restart', async () => {
  const { page } = await launch({ env: { SIMPLECAD_ENGINE_DRILL: '1' } })
  const toolbar = page.getByRole('toolbar', { name: 'Modeling tools' })
  const status = page.getByTestId('engine-status')
  const history = page.getByRole('list', { name: 'Model history' })
  await expect(status).toContainText('24000 mm³')
  await page.getByLabel('Planar face').selectOption({ label: 'Top · 1200 mm²' })
  await command(page, 'Offset Plane')
  const plane = page.getByRole('form', { name: 'Offset Plane' })
  await plane.getByLabel('Distance', { exact: true }).fill('1 cm + 5 mm')
  await plane.getByRole('button', { name: 'Apply' }).click()
  await expect(plane).toBeHidden()
  await expect(history).toContainText('15 mm from the face')
  await expect(status).toContainText('24000 mm³')

  const edit = history.getByRole('button', { name: 'Edit Offset Plane', exact: true })
  await edit.click()
  await expect(plane.getByLabel('Distance', { exact: true })).toHaveValue('1 cm + 5 mm')
  await plane.getByLabel('Distance', { exact: true }).fill('17 mm')
  await plane.getByRole('button', { name: 'Apply' }).click()
  await expect(history).toContainText('17 mm from the face')
  await expect(history.locator('li')).toHaveCount(2)
  await toolbar.getByRole('button', { name: 'Undo', exact: true }).click()
  await expect(history).toContainText('15 mm from the face')
  await toolbar.getByRole('button', { name: 'Redo', exact: true }).click()
  await expect(history).toContainText('17 mm from the face')

  await page.evaluate(() => (window as unknown as { simplecadDrill: { crashEngine(): Promise<unknown> } })
    .simplecadDrill.crashEngine().catch(() => {}))
  await expect(page.getByTestId('engine-connections')).toHaveText('2')
  await expect(history).toContainText('17 mm from the face')
  await expect(status).toContainText('24000 mm³')
  await toolbar.getByRole('button', { name: 'Sketch', exact: true }).click()
  const editor = page.getByRole('dialog', { name: 'Sketch editor' })
  await editor.getByLabel('Sketch plane', { exact: true }).selectOption({ label: 'Offset Plane' })
})

test('creates a midplane, a plane at an angle, an axis and a point from picks, and edits the angle from History', { tag: '@viewport' }, async () => {
  const { page } = await launch()
  const toolbar = page.getByRole('toolbar', { name: 'Modeling tools' })
  const steps = page.getByRole('list', { name: 'Model history' }).locator('li')
  const canvas = page.locator('canvas')
  const faces = page.getByLabel('Planar face')
  await expect(page.getByTestId('engine-status')).toHaveText(/24000 mm³/)
  const datum = async (name: string) => {
    await command(page, name)
    return page.getByRole('form', { name, exact: true })
  }
  const clickEdge = async () => {
    const [[x, y]] = await edgePoints(page)
    await page.mouse.click(x, y)
  }

  let form = await datum('Midplane')
  await faces.selectOption({ label: 'Top · 1200 mm²' })
  await faces.selectOption({ label: 'Bottom · 1200 mm²' })
  await expect(form.getByRole('status', { name: 'Picks' })).toHaveText('2 of 2 faces')
  await form.getByRole('button', { name: 'Apply' }).click()
  await expect(steps).toHaveText([/^Box/, /^Midplane/])

  form = await datum('Plane at Angle')
  await faces.selectOption({ label: 'Top · 1200 mm²' })
  await clickEdge()
  await expect(form.getByRole('status', { name: 'Picks' })).toHaveText('1 of 1 faces · 1 of 1 edges')
  await form.getByLabel('Angle (°)').fill('30')
  await form.getByRole('button', { name: 'Apply' }).click()
  await expect(steps).toHaveText([/^Box/, /^Midplane/, /^Plane at Angle/])

  form = await datum('Axis')
  await clickEdge()
  await form.getByRole('button', { name: 'Apply' }).click()
  form = await datum('Point')
  await faces.selectOption({ label: 'Top · 1200 mm²' })
  await form.getByRole('button', { name: 'Apply' }).click()
  await expect(steps).toHaveText([/^Box/, /^Midplane/, /^Plane at Angle/, /^Axis/, /^Point/])
  await expect(errors(page)).toHaveCount(0)

  await page.getByRole('button', { name: 'Edit Plane at Angle' }).click()
  const saved = page.getByRole('form', { name: 'Plane at Angle' })
  await expect(saved.getByLabel('Angle (°)')).toHaveValue('30')
  await saved.getByLabel('Angle (°)').fill('60')
  await saved.getByRole('button', { name: 'Apply' }).click()
  await expect(errors(page)).toHaveCount(0)
  await expect(steps).toHaveText([/^Box/, /^Midplane/, /^Plane at Angle/, /^Axis/, /^Point/])
  await toolbar.getByRole('button', { name: 'Undo', exact: true }).click()
  await page.getByRole('button', { name: 'Edit Plane at Angle' }).click()
  await expect(page.getByRole('form', { name: 'Plane at Angle' }).getByLabel('Angle (°)')).toHaveValue('30')
})
