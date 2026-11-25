import type { ElectronApplication } from '@playwright/test'
import path from 'node:path'
import os from 'node:os'
import { copyFile, mkdtemp, readFile, rename, rm, writeFile } from 'node:fs/promises'
import { errors, expect, launch, quitCleanly, test, command, more, partAt } from './helpers'

test('pushes a face in the native worker, rejects an invalid push, and undoes and redoes', { tag: '@viewport' }, async () => {
  const { app, page } = await launch()
  const status = page.getByTestId('engine-status')
  await expect(status).toHaveText(/Engine ready · 24000 mm³/)
  await expect(page.locator('canvas')).toBeVisible()
  const mainModules = await app.evaluate(() => Object.keys(process.getBuiltinModule('node:module')!.createRequire(process.cwd() + '/package.json').cache).filter((m) => m.endsWith('.node')))
  expect(mainModules).toEqual([])

  await page.getByLabel('Planar face').selectOption({ label: 'Top · 1200 mm²' })
  const distance = page.getByLabel('Pull distance')
  await distance.fill('-25')
  await distance.press('Enter')
  await expect(errors(page)).toContainText('straight through this body')
  await expect(status).toHaveText(/24000 mm³/)

  await distance.fill('-3')
  await distance.press('Enter')
  await expect(status).toHaveText(/20400 mm³/)
  await page.getByRole('button', { name: 'Undo', exact: true }).click()
  await expect(status).toHaveText(/24000 mm³/)
  await page.getByRole('button', { name: 'Redo', exact: true }).click()
  await expect(status).toHaveText(/20400 mm³/)
})

test('reports an unavailable 3D view after the geometry worker becomes ready', async () => {
  const { page } = await launch()
  await page.addInitScript(() => {
    const getContext = HTMLCanvasElement.prototype.getContext
    HTMLCanvasElement.prototype.getContext = function (this: HTMLCanvasElement, type: string, options?: unknown) {
      return /^webgl/.test(type) ? null : Reflect.apply(getContext, this, [type, options])
    } as typeof getContext
  })
  await page.reload()
  await expect(page.locator('canvas')).toHaveCount(0)
  await expect(page.getByTestId('engine-status')).toContainText('24000 mm³')
  await expect(page.getByRole('alert')).toContainText('Unable to initialize the 3D view')
})

test('raises and engraves text through the geometry worker, then saves and reopens', async () => {
  const scratch = await mkdtemp(path.join(os.tmpdir(), 'simplecad-text-'))
  const target = path.join(scratch, 'Text.scad3')
  try {
    const { app, page } = await launch()
    const status = page.getByTestId('engine-status')
    await expect(status).toHaveText(/24000 mm³/)
    await page.getByLabel('Planar face').selectOption({ label: 'Top · 1200 mm²' })
    await page.getByRole('toolbar', { name: 'Selection tools' }).getByRole('button', { name: 'Text', exact: true }).click()
    const text = page.getByRole('form', { name: 'Text settings' })
    await text.getByLabel('Text to place').fill('A')
    await expect(page.getByTestId('engine-status')).toHaveAttribute('data-preview', 'shown')
    await expect(text.getByRole('button', { name: 'Apply' })).toBeEnabled()
    await expect(errors(page)).toHaveCount(0)
    await expect(status).toHaveText(/24000 mm³/)
    await text.getByRole('button', { name: 'Apply' }).click()
    await expect(text).toBeHidden()
    const raised = await status.textContent()
    expect(Number(raised?.match(/([\d.]+) mm³/)?.[1])).toBeGreaterThan(24000)
    await page.getByRole('button', { name: 'Undo', exact: true }).click()
    await expect(status).toHaveText(/24000 mm³/)
    await page.getByRole('button', { name: 'Redo', exact: true }).click()
    await expect(status).toHaveText(raised!)
    await page.getByLabel('Planar face').selectOption({ label: 'Bottom · 1200 mm²' })
    await page.getByRole('toolbar', { name: 'Selection tools' }).getByRole('button', { name: 'Text', exact: true }).click()
    await text.getByLabel('Text to place').fill('H')
    await text.getByLabel('Text mode').selectOption('engraved')
    await more(text)
    await text.getByLabel('Depth').fill('25 mm')
    await text.getByRole('button', { name: 'Apply' }).click()
    await expect(errors(page)).toContainText('deeper than the material')
    await text.getByLabel('Depth').fill('2 mm')
    await text.getByRole('button', { name: 'Apply' }).click()
    await expect(page.getByRole('list', { name: 'Model history' }).locator('li')).toHaveText(['Box', 'Text', 'Text2'])
    const volume = await status.textContent()
    expect(Number(volume?.match(/([\d.]+) mm³/)?.[1])).toBeLessThan(24000)
    await app.evaluate(({ dialog }, filePath) => {
      dialog.showSaveDialog = async () => ({ filePath, canceled: false })
    }, target)
    await page.getByRole('button', { name: 'File', exact: true }).click()
    await page.getByRole('menuitem', { name: /^Save As/ }).click()
    await expect(page.getByText('Text.scad3', { exact: true })).toBeVisible()
    await app.close()
    const reopened = await launch({ args: [target] })
    const reopenedStatus = reopened.page.getByTestId('engine-status')
    await expect(reopenedStatus).toHaveText(volume!)
    const savedHistory = reopened.page.getByRole('list', { name: 'Model history' })
    await expect(savedHistory.locator('li')).toHaveText(['Box', 'Text', 'Text2'])
    const editSaved = savedHistory.getByRole('button', { name: 'Edit Text', exact: true })
    await editSaved.click()
    const editor = reopened.page.getByRole('form', { name: 'Text settings' })
    await expect(editor.getByLabel('Text to place')).toHaveValue('A')
    await more(editor)
    await expect(editor.getByLabel('Depth')).toHaveValue('1 mm')
    await editor.getByLabel('Depth').fill('3 mm / 2')
    await expect(reopenedStatus).toHaveAttribute('data-preview', 'shown')
    await expect(editor.getByRole('button', { name: 'Apply' })).toBeEnabled()
    await expect(reopenedStatus).toHaveText(volume!)
    await editor.getByRole('button', { name: 'Apply' }).click()
    await expect(editor).toBeHidden()
    await expect(savedHistory.locator('li')).toHaveText(['Box', 'Text', 'Text2'])
    const editedVolume = await reopenedStatus.textContent()
    expect(Number(editedVolume?.match(/([\d.]+) mm³/)?.[1])).toBeGreaterThan(Number(volume?.match(/([\d.]+) mm³/)?.[1]))
    await reopened.page.getByRole('button', { name: 'Undo', exact: true }).click()
    await expect(reopenedStatus).toHaveText(volume!)
    await reopened.page.getByRole('button', { name: 'Redo', exact: true }).click()
    await expect(reopenedStatus).toHaveText(editedVolume!)
    await editSaved.click()
    await expect(editor.getByLabel('Depth')).toHaveValue('3 mm / 2')
    await editor.getByRole('button', { name: 'Cancel' }).click()
    await reopened.page.getByRole('button', { name: 'File', exact: true }).click()
    await reopened.page.getByRole('menuitem', { name: /^Save(?! As)/ }).click()
    await expect(reopened.page.getByText('Text.scad3', { exact: true })).toBeVisible()
    await reopened.app.close()
  } finally {
    await rm(scratch, { recursive: true, force: true })
  }
})

test('previews, applies and edits a planar face texture through a worker restart', async () => {
  const { page } = await launch({ env: { SIMPLECAD_ENGINE_DRILL: '1' } })
  const status = page.getByTestId('engine-status')
  await expect(status).toHaveText(/24000 mm³/)
  await page.getByLabel('Planar face').selectOption({ label: 'Top · 1200 mm²' })
  await command(page, 'Face Texture')
  const texture = page.getByRole('form', { name: 'Face Texture settings' })
  await texture.getByRole('combobox', { name: /^Pattern/ }).selectOption('crosshatch')
  await texture.getByLabel('Size', { exact: true }).fill('5 mm')
  await more(texture)
  await texture.getByLabel('Spacing', { exact: true }).fill('5 mm')
  await expect(page.getByTestId('engine-status')).toHaveAttribute('data-preview', 'shown')
  await expect(texture.getByRole('button', { name: 'Apply' })).toBeEnabled()
  await expect(errors(page)).toHaveCount(0)
  await expect(status).toHaveText(/24000 mm³/)
  await texture.getByRole('button', { name: 'Apply' }).click()
  await expect(texture).toBeHidden()
  await expect(page.getByRole('list', { name: 'Model history' }).locator('li')).toHaveText(['Box', 'Face Texture'])
  const applied = await status.textContent()
  expect(Number(applied?.match(/([\d.]+) mm³/)?.[1])).toBeGreaterThan(24000)
  await page.getByRole('button', { name: 'Undo', exact: true }).click()
  await expect(status).toHaveText(/24000 mm³/)
  await page.getByRole('button', { name: 'Redo', exact: true }).click()
  await expect(status).toHaveText(applied!)
  await page.getByRole('button', { name: 'Edit Face Texture', exact: true }).click()
  await expect(texture.getByLabel('Size', { exact: true })).toHaveValue('5 mm')
  await more(texture)
  await texture.getByLabel('Depth').fill('3 mm / 2')
  await texture.getByRole('button', { name: 'Apply' }).click()
  await expect(texture).toBeHidden()
  const edited = await status.textContent()
  expect(Number(edited?.match(/([\d.]+) mm³/)?.[1])).toBeGreaterThan(Number(applied?.match(/([\d.]+) mm³/)?.[1]))
  await page.evaluate(() =>
    (window as unknown as { simplecadDrill: { crashEngine(): Promise<unknown> } })
      .simplecadDrill.crashEngine().catch(() => {}))
  await expect(page.getByTestId('engine-connections')).toHaveText('2')
  await expect(status).toHaveText(edited!)
  await page.getByRole('button', { name: 'Undo', exact: true }).click()
  await expect(status).toHaveText(applied!)
})

test('drills and edits a blind hole, preserving its parametric history', async () => {
  const { page } = await launch({ env: { SIMPLECAD_ENGINE_DRILL: '1' } })
  const status = page.getByTestId('engine-status')
  const history = page.getByRole('list', { name: 'Model history' })
  const toolbar = page.getByRole('toolbar', { name: 'Modeling tools' })
  await expect(status).toContainText('24000 mm³')
  await page.getByLabel('Planar face').selectOption({ label: 'Top · 1200 mm²' })
  await command(page, 'Hole')
  const hole = page.getByRole('form', { name: 'Hole' })
  await hole.getByLabel('Depth mode').selectOption('blind')
  await hole.getByLabel('Diameter', { exact: true }).fill('3 mm * 2')
  await hole.getByLabel('Depth', { exact: true }).fill('8 mm')
  await expect(page.getByTestId('engine-status')).toHaveAttribute('data-preview', 'shown')
  await expect(hole.getByRole('button', { name: 'Apply' })).toBeEnabled()
  await expect(status).toContainText('24000 mm³')
  await hole.getByRole('button', { name: 'Apply' }).click()
  await expect(status).toContainText('23773.81 mm³')
  await history.getByRole('button', { name: 'Edit Hole', exact: true }).click()
  await expect(hole.getByLabel('Diameter', { exact: true })).toHaveValue('3 mm * 2')
  await hole.getByLabel('Depth', { exact: true }).fill('12 mm')
  await hole.getByRole('button', { name: 'Apply' }).click()
  await expect(status).toContainText('23660.71 mm³')
  await toolbar.getByRole('button', { name: 'Undo', exact: true }).click()
  await expect(status).toContainText('23773.81 mm³')
  await toolbar.getByRole('button', { name: 'Redo', exact: true }).click()
  await expect(status).toContainText('23660.71 mm³')
  await page.evaluate(() =>
    (window as unknown as { simplecadDrill: { crashEngine(): Promise<unknown> } }).simplecadDrill.crashEngine().catch(() => {}))
  await expect(page.getByTestId('engine-connections')).toHaveText('2')
  await expect(status).toContainText('23660.71 mm³')
})

test('models a physical thread on a shaft and undoes it in one step', async () => {
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
  const faces = page.getByLabel('Planar face')
  await faces.selectOption({ label: 'Curved · 2827.43 mm²' })
  await command(page, 'Thread')
  const thread = page.getByRole('form', { name: 'Thread' })
  await thread.getByLabel('Thread size').selectOption('M30')
  await thread.getByLabel('Threaded length').fill('10 mm')
  await thread.getByRole('button', { name: 'Apply' }).click()
  await expect(toolbar.getByRole('button', { name: 'Undo', exact: true })).toBeEnabled({ timeout: 60_000 })
  // The native thread test uses the same 1 mm³ bound for compiler-dependent
  // B-spline integration outside Linux. Keep the Fedora result to display precision.
  await expect.poll(async () => {
    const volume = Number((await status.textContent())?.match(/([\d.]+) mm³/)?.[1])
    return Math.abs(volume - 20578.55)
  }).toBeLessThan(process.platform === 'linux' ? 0.005 : 1)
  await toolbar.getByRole('button', { name: 'Undo', exact: true }).click()
  await expect(status).toContainText('21205.75 mm³')
})

test('creates a threaded through hole from the standard size picker', async () => {
  const { page } = await launch()
  const status = page.getByTestId('engine-status')
  const toolbar = page.getByRole('toolbar', { name: 'Modeling tools' })
  await expect(status).toContainText('24000 mm³')
  await page.getByRole('button', { name: 'Hide Box', exact: true }).click()
  await toolbar.getByRole('button', { name: 'Shape', exact: true }).click()
  const shape = page.getByRole('form', { name: 'Create shape' })
  await shape.getByLabel('Depth', { exact: true }).fill('40 mm')
  await shape.getByLabel('Height', { exact: true }).fill('12 mm')
  await shape.getByRole('button', { name: 'Create' }).click()
  await expect(status).toContainText('19200 mm³')
  await page.getByLabel('Planar face').selectOption({ label: 'Top · 1600 mm²' })
  await command(page, 'Hole')
  const hole = page.getByRole('form', { name: 'Hole' })
  await more(hole)
  await hole.getByLabel('Style').selectOption('threaded')
  await hole.getByLabel('Diameter', { exact: true }).fill('30 mm')
  await hole.getByLabel('Thread size').selectOption('M30')
  await hole.getByRole('button', { name: 'Apply' }).click()
  await expect(status).toContainText('11534.33 mm³')
  await toolbar.getByRole('button', { name: 'Undo', exact: true }).click()
  await expect(status).toContainText('19200 mm³')
  await toolbar.getByRole('button', { name: 'Redo', exact: true }).click()
  await expect(status).toContainText('11534.33 mm³')
})

test('measures a body and the spacing between two faces without changing history', async () => {
  const { page } = await launch()
  const status = page.getByTestId('engine-status')
  const toolbar = page.getByRole('toolbar', { name: 'Modeling tools' })
  const history = page.getByRole('list', { name: 'Model history' }).locator('li')
  await expect(status).toContainText('24000 mm³')
  await toolbar.getByRole('button', { name: 'Measure', exact: true }).click()
  const panel = page.getByRole('region', { name: 'Measure' })
  const reading = (label: string) => panel.getByText(label, { exact: true }).locator('..').locator('dd')
  await panel.getByLabel('Measure selection').selectOption('body')
  await page.getByRole('list', { name: 'Bodies', exact: true }).getByRole('button', { name: 'Box', exact: true }).click()
  await expect(reading('Volume')).toHaveText('24000 mm³')
  await expect(reading('Surface area')).toHaveText('5200 mm²')
  await expect(reading('Bounding box')).toHaveText('40 × 30 × 20 mm')
  await expect(reading('Centre of mass')).toHaveText('20, 15, 10 mm')
  await panel.getByRole('button', { name: 'Remove Box' }).click()
  await panel.getByLabel('Measure selection').selectOption('face')
  await page.getByLabel('Planar face').selectOption({ label: 'Top · 1200 mm²' })
  await expect(reading('Area')).toHaveText('1200 mm²')
  await page.getByLabel('Planar face').selectOption({ label: 'Bottom · 1200 mm²' })
  await expect(reading('Minimum distance')).toHaveText('20 mm')
  await expect(reading('Angle')).toHaveText('0 °')
  await expect(history).toHaveText(['Box'])
  await expect(toolbar.getByRole('button', { name: 'Undo', exact: true })).toBeDisabled()
})

test('the more actions menu fits above its trigger and Escape restores focus', async () => {
  const { app, page } = await launch()
  await app.evaluate(({ BrowserWindow }) => {
    const window = BrowserWindow.getAllWindows()[0]
    window.setSize(960, 600)
    window.webContents.setZoomFactor(2)
  })
  await page.getByLabel('Planar face').selectOption({ label: 'Top · 1200 mm²' })
  const trigger = page.getByRole('button', { name: 'More actions' })
  await trigger.press('Enter')
  const menu = page.getByRole('menu')
  await expect(menu.getByRole('menuitem', { name: 'Clear selection' })).toBeVisible()
  await expect.poll(async () => {
    const a = (await trigger.boundingBox())!
    const b = (await menu.boundingBox())!
    const width = await page.evaluate(() => window.innerWidth)
    return b.y >= 0 && b.y + b.height <= a.y && b.x >= 0 && b.x + b.width <= width
  }).toBe(true)
  await page.keyboard.press('Escape')
  await expect(trigger).toBeFocused()
})

test('the window restores accepted edits after the geometry worker exits', async () => {
  const { page } = await launch({ env: { SIMPLECAD_ENGINE_DRILL: '1' } })
  const status = page.getByTestId('engine-status')
  await expect(status).toHaveText(/Engine ready/)
  await page.getByLabel('Planar face').selectOption({ label: 'Top · 1200 mm²' })
  const distance = page.getByLabel('Pull distance')
  await distance.fill('-2')
  await distance.press('Enter')
  await expect(status).toHaveText(/21600 mm³/)

  const crashed = page.evaluate(() =>
    (window as unknown as { simplecadDrill: { crashEngine(): Promise<unknown> } }).simplecadDrill.crashEngine().then(
      () => 'answered',
      (e: Error) => e.message
    )
  )
  expect(await crashed).toMatch(/restarted/)
  await expect(page.getByTestId('engine-connections')).toHaveText('2')
  await expect(status).toHaveText(/Engine ready · 21600 mm³/)
  await page.getByRole('button', { name: 'Undo', exact: true }).click()
  await expect(status).toHaveText(/24000 mm³/)
})

const fixture = path.join(__dirname, 'fixtures', 'features.scad3')

test('opens a project handed to the desktop launcher', async () => {
  const { page } = await launch({ args: [fixture] })
  await expect(page.getByTestId('engine-status')).toHaveText(/71016.32 mm³/)
  await expect(page.getByText('features.scad3', { exact: true })).toBeVisible()
})

test('keeps an existing file when an export adds its extension and replacement is declined', async () => {
  const { app, page } = await launch()
  await expect(page.getByTestId('engine-status')).toHaveText(/24000 mm³/)
  const scratch = await mkdtemp(path.join(os.tmpdir(), 'simplecad-export-overwrite-'))
  try {
    const selected = path.join(scratch, 'Model')
    const target = `${selected}.stl`
    await writeFile(target, 'previous file')
    await app.evaluate(({ dialog }, filePath) => {
      dialog.showSaveDialog = async () => ({ filePath, canceled: false })
      dialog.showMessageBox = async () => ({ response: 0, checkboxChecked: false })
    }, selected)
    const file = page.getByRole('button', { name: 'File', exact: true })
    await file.click()
    await page.getByRole('menuitem', { name: 'Export STL' }).click()
    await expect(page.getByRole('button', { name: 'Shape', exact: true })).toBeEnabled()
    expect(await readFile(target, 'utf8')).toBe('previous file')
  } finally {
    await rm(scratch, { recursive: true, force: true })
  }
})

test('moves and rotates by expressions and preserves both steps through restart and reopen', async () => {
  const scratch = await mkdtemp(path.join(os.tmpdir(), 'simplecad-transform-'))
  const target = path.join(scratch, 'Transformed.scad3')
  const stl = path.join(scratch, 'Bounds.stl')
  try {
    const { app, page } = await launch({ env: { SIMPLECAD_ENGINE_DRILL: '1' } })
    const toolbar = page.getByRole('toolbar', { name: 'Modeling tools' })
    const steps = page.getByRole('list', { name: 'Model history' }).locator('li')
    const bounds = async () => {
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
    const matchBounds = (actual: number[], expected: number[]) =>
      actual.forEach((value, index) => expect(value).toBeCloseTo(expected[index], 5))
    await expect(steps).toHaveText(['Box'])
    const start = await bounds()
    await command(page, 'Move')
    const move = page.getByRole('form', { name: 'Move', exact: true })
    await move.getByLabel('X (mm)').fill('1 cm + 2 mm')
    await expect(page.getByTestId('engine-status')).toHaveAttribute('data-preview', 'shown')
    await expect(move.getByRole('button', { name: 'Apply' })).toBeEnabled()
    await expect(errors(page)).toHaveCount(0)
    await expect(steps).toHaveText(['Box'])
    await move.getByRole('button', { name: 'Apply' }).click()
    await expect(steps).toHaveText(['Box', 'Move'])
    const moved = start.map((value, axis) => axis % 3 === 0 ? value + 12 : value)
    matchBounds(await bounds(), moved)

    await command(page, 'Rotate')
    const rotate = page.getByRole('form', { name: 'Rotate', exact: true })
    await rotate.getByLabel('Z (°)').fill('45 + 45')
    await more(rotate)
    await rotate.getByLabel('Turn about').selectOption('origin')
    await rotate.getByRole('button', { name: 'Apply' }).click()
    await expect(steps).toHaveText(['Box', 'Move', 'Rotate'])
    const turned = [-moved[4], moved[0], moved[2], -moved[1], moved[3], moved[5]]
    matchBounds(await bounds(), turned)
    await toolbar.getByRole('button', { name: 'Undo', exact: true }).click()
    matchBounds(await bounds(), moved)
    await toolbar.getByRole('button', { name: 'Redo', exact: true }).click()
    matchBounds(await bounds(), turned)

    await page.evaluate(() =>
      (window as unknown as { simplecadDrill: { crashEngine(): Promise<unknown> } }).simplecadDrill.crashEngine().catch(() => {}))
    await expect(page.getByTestId('engine-connections')).toHaveText('2')
    await expect(steps).toHaveText(['Box', 'Move', 'Rotate'])
    matchBounds(await bounds(), turned)
    await app.evaluate(({ dialog }, filePath) => {
      dialog.showSaveDialog = async () => ({ filePath, canceled: false })
    }, target)
    await page.getByRole('button', { name: 'File', exact: true }).click()
    await page.getByRole('menuitem', { name: /^Save As/ }).click()
    await expect(page.getByText('Transformed.scad3', { exact: true })).toBeVisible()
    await app.close()
    const reopened = await launch({ args: [target] })
    await expect(reopened.page.getByRole('list', { name: 'Model history' }).locator('li')).toHaveText(['Box', 'Move', 'Rotate'])
    await reopened.page.getByRole('button', { name: 'Undo', exact: true }).click()
    await expect(reopened.page.getByRole('button', { name: 'Redo', exact: true })).toBeEnabled()
  } finally {
    await rm(scratch, { recursive: true, force: true })
  }
})

test('edits a saved primitive from History and rebuilds the dependent Pull', async () => {
  const scratch = await mkdtemp(path.join(os.tmpdir(), 'simplecad-edit-shape-'))
  const target = path.join(scratch, 'Edited Box.scad3')
  try {
    const { app, page } = await launch()
    const status = page.getByTestId('engine-status')
    const file = page.getByRole('button', { name: 'File', exact: true })
    await expect(status).toContainText('24000 mm³')
    await page.getByLabel('Planar face').selectOption({ label: 'Top · 1200 mm²' })
    await page.getByLabel('Pull distance').fill('2')
    await page.getByLabel('Pull distance').press('Enter')
    await expect(status).toContainText('26400 mm³')
    await app.evaluate(({ dialog }, filePath) => {
      dialog.showSaveDialog = async () => ({ filePath, canceled: false })
    }, target)
    await file.click()
    await page.getByRole('menuitem', { name: /^Save As/ }).click()
    await expect(page.getByText('Edited Box.scad3', { exact: true })).toBeVisible()
    await app.close()

    const reopened = await launch({ args: [target], env: { SIMPLECAD_ENGINE_DRILL: '1' } })
    const history = reopened.page.getByRole('list', { name: 'Model history' })
    const reopenedStatus = reopened.page.getByTestId('engine-status')
    await expect(reopenedStatus).toContainText('26400 mm³')
    await history.getByRole('button', { name: 'Edit Box', exact: true }).click()
    const editor = reopened.page.getByRole('form', { name: 'Edit Box' })
    await expect(editor.getByLabel('Width', { exact: true })).toHaveValue('40 mm')
    await editor.getByLabel('Width', { exact: true }).fill('40 mm + 2 mm')
    await expect(reopenedStatus).toHaveAttribute('data-preview', 'shown')
    await expect(editor.getByRole('button', { name: 'Apply' })).toBeEnabled()
    await expect(reopenedStatus).toContainText('26400 mm³')
    await editor.getByRole('button', { name: 'Apply' }).click()
    await expect(reopenedStatus).toContainText('27720 mm³')

    const undo = reopened.page.getByRole('button', { name: 'Undo', exact: true })
    const redo = reopened.page.getByRole('button', { name: 'Redo', exact: true })
    await undo.click()
    await expect(reopenedStatus).toContainText('26400 mm³')
    await redo.click()
    await expect(reopenedStatus).toContainText('27720 mm³')
    await reopened.page.evaluate(() =>
      (window as unknown as { simplecadDrill: { crashEngine(): Promise<unknown> } }).simplecadDrill.crashEngine().catch(() => {}))
    await expect(reopened.page.getByTestId('engine-connections')).toHaveText('2')
    await expect(reopenedStatus).toContainText('27720 mm³')
    await history.getByRole('button', { name: 'Edit Box', exact: true }).click()
    await expect(editor.getByLabel('Width', { exact: true })).toHaveValue('40 mm + 2 mm')
    await expect(editor.getByLabel('Depth', { exact: true })).toHaveValue('30 mm')
  } finally {
    await rm(scratch, { recursive: true, force: true })
  }
})

test('creates a cylinder from expressions and undoes and redoes creation', async () => {
  const { page } = await launch()
  const status = page.getByTestId('engine-status')
  await expect(status).toHaveText(/24000 mm³/)
  await page.getByRole('button', { name: 'Shape', exact: true }).click()
  const panel = page.getByRole('form', { name: 'Create shape' })
  await panel.getByLabel('Shape type').selectOption('cylinder')
  await panel.getByLabel('Radius').fill('10 mm / 2')
  await panel.getByLabel('Height').fill('12 mm')
  await panel.getByRole('button', { name: 'Create', exact: true }).click()
  await expect(status).toHaveText(/24942.48 mm³/)
  await expect(page.getByRole('list', { name: 'Bodies' })).toContainText('Cylinder')
  await page.getByRole('button', { name: 'Undo', exact: true }).click()
  await expect(status).toHaveText(/24000 mm³/)
  await page.getByRole('button', { name: 'Redo', exact: true }).click()
  await expect(status).toHaveText(/24942.48 mm³/)
})

for (const { format, filename, volume } of [
  { format: 'B-Rep', filename: 'two-cubes.brep', volume: 24250 },
  { format: 'IGES in inches', filename: 'two-boxes-inches.igs', volume: 25680 }
]) {
  test(`imports ${format} as independently editable bodies in one undo step`, async () => {
    const stem = path.parse(filename).name
    const { app, page } = await launch()
    const status = page.getByTestId('engine-status')
    await expect(status).toHaveText(/24000 mm³/)
    const scratch = await mkdtemp(path.join(os.tmpdir(), 'simplecad-solid-import-'))
    const source = path.join(scratch, filename)
    const target = path.join(scratch, 'Embedded.scad3')
    try {
      await copyFile(path.join(__dirname, 'fixtures', filename), source)
      await app.evaluate(({ dialog }, filePath) => {
        dialog.showOpenDialog = async () => ({ filePaths: [filePath], canceled: false })
      }, source)
      await page.getByRole('button', { name: 'File', exact: true }).click()
      await page.getByRole('menuitem', { name: /Import/ }).click()
      await expect(status).toHaveText(`Engine ready · ${volume} mm³`)
      if (filename.endsWith('.igs')) {
        const staged = await app.evaluate(({ app }) => `${app.getPath('userData')}/engine/import.iges`)
        await expect(readFile(staged)).rejects.toMatchObject({ code: 'ENOENT' })
      }
      const bodies = page.getByRole('list', { name: 'Bodies' })
      await expect(bodies.getByText(`${stem} 1`, { exact: true })).toBeVisible()
      await expect(bodies.getByText(`${stem} 2`, { exact: true })).toBeVisible()
      await page.getByRole('button', { name: 'Undo', exact: true }).click()
      await expect(status).toHaveText(/24000 mm³/)
      await page.getByRole('button', { name: 'Redo', exact: true }).click()
      await expect(status).toHaveText(`Engine ready · ${volume} mm³`)
      await app.evaluate(({ dialog }, filePath) => {
        dialog.showSaveDialog = async () => ({ filePath, canceled: false })
        dialog.showOpenDialog = async () => ({ filePaths: [filePath], canceled: false })
      }, target)
      await page.getByRole('button', { name: 'File', exact: true }).click()
      await page.getByRole('menuitem', { name: 'Save As' }).click()
      await expect(page.getByText('Embedded.scad3', { exact: true })).toBeVisible()
      await rename(source, path.join(scratch, `moved-${filename}`))
      await page.getByRole('button', { name: 'File', exact: true }).click()
      await page.getByRole('menuitem', { name: 'New box' }).click()
      await expect(status).toHaveText(/24000 mm³/)
      await page.getByRole('button', { name: 'File', exact: true }).click()
      await page.getByRole('menuitem', { name: 'Open project' }).click()
      await expect(status).toHaveText(`Engine ready · ${volume} mm³`)
    } finally {
      await rm(scratch, { recursive: true, force: true })
    }
  })
}

test('exports four formats and embeds an imported STEP solid through save and reopen', async () => {
  const { app, page } = await launch({ env: { SIMPLECAD_ENGINE_DRILL: '1' } })
  await expect(page.getByTestId('engine-status')).toHaveText(/24000 mm³/)
  const scratch = await mkdtemp(path.join(os.tmpdir(), 'simplecad-mesh-export-'))
  try {
    for (const format of ['step', 'stl', 'obj', '3mf']) {
      const target = path.join(scratch, `Export.${format}`)
      await app.evaluate(({ dialog }, filePath) => {
        dialog.showSaveDialog = async () => ({ filePath, canceled: false })
      }, target)
      await page.getByRole('button', { name: 'File', exact: true }).click()
      await page.getByRole('menuitem', { name: `Export ${format.toUpperCase()}` }).click()
      await expect.poll(async () => (await readFile(target).catch(() => Buffer.alloc(0))).length).toBeGreaterThan(0)
      const data = await readFile(target)
      if (format === 'step') expect(data.toString()).toMatch(/^ISO-10303-21;/)
      if (format === 'stl') expect(data.readUInt32LE(80)).toBe(12)
      if (format === 'obj') expect(data.toString()).toMatch(/^o Box$/m)
      if (format === '3mf') expect(data.includes(Buffer.from('3D/3dmodel.model'))).toBe(true)
    }
    const status = page.getByTestId('engine-status')
    await expect(status).toHaveText(/24000 mm³/)
    const source = path.join(scratch, 'Export.step')
    const project = path.join(scratch, 'Imported.scad3')
    await app.evaluate(({ dialog }, filePath) => {
      dialog.showOpenDialog = async () => ({ filePaths: [filePath], canceled: false })
    }, source)
    await page.getByRole('button', { name: 'File', exact: true }).click()
    await page.getByRole('menuitem', { name: 'Import model' }).click()
    await expect(status).toHaveText(/48000 mm³/)
    await page.getByRole('button', { name: 'Undo', exact: true }).click()
    await expect(status).toHaveText(/24000 mm³/)
    await page.getByRole('button', { name: 'Redo', exact: true }).click()
    await expect(status).toHaveText(/48000 mm³/)
    await page.evaluate(() =>
      (window as unknown as { simplecadDrill: { crashEngine(): Promise<unknown> } })
        .simplecadDrill.crashEngine().catch(() => {}))
    await expect(page.getByTestId('engine-connections')).toHaveText('2')
    await expect(status).toHaveText(/48000 mm³/)
    await app.evaluate(({ dialog }, filePath) => {
      dialog.showSaveDialog = async () => ({ filePath, canceled: false })
      dialog.showOpenDialog = async () => ({ filePaths: [filePath], canceled: false })
    }, project)
    await page.getByRole('button', { name: 'File', exact: true }).click()
    await page.getByRole('menuitem', { name: 'Save As' }).click()
    await expect(page.getByText('Imported.scad3', { exact: true })).toBeVisible()
    expect((await readFile(project)).includes(Buffer.from('assets/'))).toBe(true)
    await rm(source)
    await page.getByRole('button', { name: 'File', exact: true }).click()
    await page.getByRole('menuitem', { name: 'New box' }).click()
    await expect(status).toHaveText(/24000 mm³/)
    await page.getByRole('button', { name: 'File', exact: true }).click()
    await page.getByRole('menuitem', { name: 'Open project' }).click()
    await expect(status).toHaveText(/48000 mm³/)
  } finally {
    await rm(scratch, { recursive: true, force: true })
  }
})

/** Answer the next Open dialog with the fixture, a plate with a hole, a recess and a boss, and a hidden box. */
async function offerFixture(app: ElectronApplication) {
  await app.evaluate(({ dialog }, filePath) => {
    dialog.showOpenDialog = async () => ({ filePaths: [filePath], canceled: false })
  }, fixture)
}

for (const [format, imported] of [['stl', 'Model'], ['obj', 'Box2'], ['3mf', 'Box2']] as const) {
  test(`round-trips ${format.toUpperCase()} as editable geometry and embeds the source`, async () => {
    const scratch = await mkdtemp(path.join(os.tmpdir(), 'simplecad-mesh-import-'))
    const source = path.join(scratch, `Model.${format}`)
    const target = path.join(scratch, 'Mesh.scad3')
    try {
      const { app, page } = await launch()
      const status = page.getByTestId('engine-status')
      const file = page.getByRole('button', { name: 'File', exact: true })
      await expect(status).toHaveText(/24000 mm³/)
      await app.evaluate(({ dialog }, filePath) => {
        dialog.showSaveDialog = async () => ({ filePath, canceled: false })
        dialog.showOpenDialog = async () => ({ filePaths: [filePath], canceled: false })
      }, source)
      await file.click()
      await page.getByRole('menuitem', { name: `Export ${format.toUpperCase()}` }).click()
      await expect.poll(async () => (await readFile(source).catch(() => Buffer.alloc(0))).length).toBeGreaterThan(0)
      await file.click()
      await page.getByRole('menuitem', { name: /Import/ }).click()
      await expect(status).toHaveText(/48000 mm³/)
      await page.getByLabel('Planar face').selectOption({ label: `${imported} · Top · 1200 mm²` })
      const distance = page.getByLabel('Pull distance')
      await distance.fill('1')
      await distance.press('Enter')
      await expect(status).toHaveText(/49200 mm³/)
      const undo = page.getByRole('button', { name: 'Undo', exact: true })
      await undo.click()
      await expect(status).toHaveText(/48000 mm³/)
      await undo.click()
      await expect(status).toHaveText(/24000 mm³/)
      const redo = page.getByRole('button', { name: 'Redo', exact: true })
      await redo.click()
      await expect(status).toHaveText(/48000 mm³/)
      await app.evaluate(({ dialog }, filePath) => {
        dialog.showSaveDialog = async () => ({ filePath, canceled: false })
        dialog.showOpenDialog = async () => ({ filePaths: [filePath], canceled: false })
      }, target)
      await file.click()
      await page.getByRole('menuitem', { name: /^Save As/ }).click()
      await expect(page.getByText('Mesh.scad3', { exact: true })).toBeVisible()
      await rename(source, path.join(scratch, `moved.${format}`))
      await file.click()
      await page.getByRole('menuitem', { name: 'New box' }).click()
      await expect(status).toHaveText(/24000 mm³/)
      await file.click()
      await page.getByRole('menuitem', { name: 'Open project' }).click()
      await expect(status).toHaveText(/48000 mm³/)
    } finally {
      await rm(scratch, { recursive: true, force: true })
    }
  })
}

for (const format of ['gltf', 'glb'] as const) {
  test(`imports ${format.toUpperCase()} with millimetre geometry and embeds its resources`, async () => {
    const scratch = await mkdtemp(path.join(os.tmpdir(), 'simplecad-gltf-'))
    const source = path.join(scratch, `Cube.${format}`)
    const bufferFile = path.join(scratch, 'box data.bin')
    const target = path.join(scratch, 'Imported.scad3')
    try {
      const vertices = [
        0, 0, 0, 0.01, 0, 0, 0.01, 0.02, 0, 0, 0.02, 0,
        0, 0, 0.03, 0.01, 0, 0.03, 0.01, 0.02, 0.03, 0, 0.02, 0.03
      ]
      const indices = [0, 2, 1, 0, 3, 2, 4, 5, 6, 4, 6, 7, 0, 1, 5, 0, 5, 4,
        1, 2, 6, 1, 6, 5, 2, 3, 7, 2, 7, 6, 3, 0, 4, 3, 4, 7]
      const mesh = Buffer.alloc(vertices.length * 4 + indices.length * 2)
      vertices.forEach((value, i) => mesh.writeFloatLE(value, i * 4))
      indices.forEach((value, i) => mesh.writeUInt16LE(value, vertices.length * 4 + i * 2))
      const model = Buffer.from(JSON.stringify({
        asset: { version: '2.0' },
        buffers: [{ byteLength: mesh.length, uri: format === 'gltf' ? 'box%20data.bin' : undefined }],
        bufferViews: [{ buffer: 0, byteOffset: 0, byteLength: 96, target: 34962 },
          { buffer: 0, byteOffset: 96, byteLength: 72, target: 34963 }],
        accessors: [{ bufferView: 0, componentType: 5126, count: 8, type: 'VEC3', min: [0, 0, 0], max: [0.01, 0.02, 0.03] },
          { bufferView: 1, componentType: 5123, count: 36, type: 'SCALAR' }],
        meshes: [{ primitives: [{ attributes: { POSITION: 0 }, indices: 1 }] }],
        nodes: [{ mesh: 0, name: 'Imported cube' }], scenes: [{ nodes: [0] }], scene: 0
      }))
      if (format === 'gltf') {
        await writeFile(bufferFile, mesh)
        await writeFile(source, model)
      } else {
        const jsonLength = Math.ceil(model.length / 4) * 4
        const container = Buffer.alloc(28 + jsonLength + mesh.length, 0x20)
        container.write('glTF', 0, 'ascii')
        container.writeUInt32LE(2, 4)
        container.writeUInt32LE(container.length, 8)
        container.writeUInt32LE(jsonLength, 12)
        container.write('JSON', 16, 'ascii')
        model.copy(container, 20)
        container.writeUInt32LE(mesh.length, 20 + jsonLength)
        container.writeUInt32LE(0x004e4942, 24 + jsonLength)
        mesh.copy(container, 28 + jsonLength)
        await writeFile(source, container)
      }

      const { app, page } = await launch()
      const status = page.getByTestId('engine-status')
      const file = page.getByRole('button', { name: 'File', exact: true })
      await expect(status).toContainText('24000 mm³')
      await app.evaluate(({ dialog }, source) => {
        dialog.showOpenDialog = async () => ({ filePaths: [source], canceled: false })
      }, source)
      await file.click()
      await page.getByRole('menuitem', { name: 'Import model' }).click()
      await expect(status).toContainText('30000 mm³')
      await expect(page.getByRole('list', { name: 'Bodies', exact: true })).toContainText('Imported cube')
      await page.getByLabel('Planar face').selectOption({ label: 'Imported cube · Top · 200 mm²' })
      await page.getByLabel('Pull distance').fill('2')
      await page.getByLabel('Pull distance').press('Enter')
      await expect(status).toContainText('30400 mm³')
      await page.getByRole('button', { name: 'Undo', exact: true }).click()
      await expect(status).toContainText('30000 mm³')
      await page.getByRole('button', { name: 'Redo', exact: true }).click()
      await expect(status).toContainText('30400 mm³')
      await app.evaluate(({ dialog }, target) => {
        dialog.showSaveDialog = async () => ({ filePath: target, canceled: false })
      }, target)
      await file.click()
      await page.getByRole('menuitem', { name: /^Save As/ }).click()
      await expect(page.getByText('Imported.scad3', { exact: true })).toBeVisible()
      await rename(source, path.join(scratch, 'Moved.' + format))
      await rm(bufferFile, {force: true})
      await app.close()
      const reopened = await launch({ args: [target] })
      await expect(reopened.page.getByTestId('engine-status')).toContainText('30400 mm³')
    } finally {
      await rm(scratch, { recursive: true, force: true })
    }
  })
}

test('imports a SimpleCAD project as editable history with embedded assets', async () => {
  const scratch = await mkdtemp(path.join(os.tmpdir(), 'simplecad-native-import-'))
  const source = path.join(scratch, 'Parts.scad3')
  const target = path.join(scratch, 'Combined.scad3')
  try {
    await copyFile(fixture, source)
    const { app, page } = await launch({ env: { SIMPLECAD_ENGINE_DRILL: '1' } })
    const status = page.getByTestId('engine-status')
    const steps = page.getByRole('list', { name: 'Model history' }).locator('li')
    const file = page.getByRole('button', { name: 'File', exact: true })
    await expect(status).toContainText('24000 mm³')
    await app.evaluate(({ dialog }, source) => {
      dialog.showOpenDialog = async () => ({ filePaths: [source], canceled: false })
    }, source)
    await file.click()
    await page.getByRole('menuitem', { name: 'Import model' }).click()
    await expect(status).toContainText('95016.32 mm³')
    await expect(page.getByRole('button', { name: 'Show Spare' })).toBeEnabled()
    await expect(steps).toHaveText(['Box', 'Imported solid', 'Imported solid2', 'Move', 'Move2'])
    await page.getByRole('button', { name: 'Undo', exact: true }).click()
    await expect(status).toContainText('24000 mm³')
    await page.getByRole('button', { name: 'Redo', exact: true }).click()
    await expect(status).toContainText('95016.32 mm³')
    await rename(source, path.join(scratch, 'Moved.scad3'))
    await page.evaluate(() =>
      (window as unknown as { simplecadDrill: { crashEngine(): Promise<unknown> } }).simplecadDrill.crashEngine().catch(() => {}))
    await expect(page.getByTestId('engine-connections')).toHaveText('2')
    await expect(status).toContainText('95016.32 mm³')
    await page.getByRole('button', { name: 'Show Spare' }).click()
    await expect(status).toContainText('96016.32 mm³')
    const face = page.getByLabel('Planar face')
    await face.selectOption({ label: 'Spare · Top · 100 mm²' })
    await page.getByLabel('Pull distance').fill('2')
    await page.getByLabel('Pull distance').press('Enter')
    await expect(status).toContainText('96216.32 mm³')
    await app.evaluate(({ dialog }, filePath) => {
      dialog.showSaveDialog = async () => ({ filePath, canceled: false })
    }, target)
    await file.click()
    await page.getByRole('menuitem', { name: /^Save As/ }).click()
    await expect(page.getByText('Combined.scad3', { exact: true })).toBeVisible()
    await app.close()
    const reopened = await launch({ args: [target] })
    const reopenedStatus = reopened.page.getByTestId('engine-status')
    await expect(reopenedStatus).toContainText('95016.32 mm³')
    await reopened.page.getByRole('button', { name: 'Show Spare' }).click()
    await expect(reopenedStatus).toContainText('96216.32 mm³')
    await expect(reopened.page.getByRole('list', { name: 'Model history' }).locator('li'))
      .toHaveText(['Box', 'Imported solid', 'Imported solid2', 'Move', 'Move2', 'Pull'])
  } finally {
    await rm(scratch, { recursive: true, force: true })
  }
})

test('stacks two parts after undo and records the new placement as one step', async () => {
  const { app, page } = await launch()
  const status = page.getByTestId('engine-status')
  const file = page.getByRole('button', { name: 'File', exact: true })
  await expect(status).toHaveText(/24000 mm³/)
  await offerFixture(app)
  await file.click()
  await page.getByRole('menuitem', { name: 'Open project' }).click()
  await expect(status).toHaveText(/71016.32 mm³/)
  await page.getByRole('button', { name: 'Show Spare' }).click()
  await expect(status).toHaveText(/72016.32 mm³/)
  const moving = page.getByLabel('Planar face')
  await moving.selectOption({ label: 'Spare · Top · 100 mm²' })
  const distance = page.getByLabel('Pull distance')
  await distance.fill('2')
  await distance.press('Enter')
  await expect(status).toHaveText(/72216.32 mm³/)
  const undo = page.getByRole('button', { name: 'Undo', exact: true })
  await undo.click()
  await expect(status).toHaveText(/72016.32 mm³/)

  const steps = page.getByRole('list', { name: 'Model history' }).locator('li')
  await moving.selectOption({ label: 'Spare · Bottom · 100 mm²' })
  await app.evaluate(({ BrowserWindow }) => {
    const window = BrowserWindow.getAllWindows()[0]
    window.setSize(960, 600)
    window.webContents.setZoomFactor(1.5)
  })
  await command(page, 'Stack & Place')
  await page.getByLabel('Target face').selectOption({ label: 'Plate · Top · 3251.73 mm²' })
  const place = page.getByRole('button', { name: 'Place', exact: true })
  await expect(place).toBeEnabled()
  await page.getByLabel('Offset (mm)').fill('2')
  await expect(place).toBeEnabled()
  await place.click()
  await expect(steps).toHaveText(['Imported solid', 'Imported solid2', 'Align'])
  await expect(status).toHaveText(/72016.32 mm³/)
  const redo = page.getByRole('button', { name: 'Redo', exact: true })
  await expect(redo).toBeDisabled()
  await undo.click()
  await expect(redo).toBeEnabled()
})

test('opens a .scad3 project and pulls a face with its recess and boss, through a real engine crash', async () => {
  const { app, page } = await launch({ env: { SIMPLECAD_ENGINE_DRILL: '1' } })
  const status = page.getByTestId('engine-status')
  await expect(status).toHaveText(/24000 mm³/)
  await offerFixture(app)
  await page.getByRole('button', { name: 'File', exact: true }).click()
  await page.getByRole('menuitem', { name: 'Open project' }).click()
  await expect(status).toHaveText(/Engine ready · 71016.32 mm³/)
  const bodies = page.getByRole('list', { name: 'Bodies' })
  await expect(bodies).toContainText('Plate')
  await expect(bodies).toContainText('Spare')
  await expect(page.getByText('features.scad3', { exact: true })).toBeVisible()

  await page.getByLabel('Planar face').selectOption({ label: 'Top · 3251.73 mm²' })
  const distance = page.getByLabel('Pull distance')
  await distance.fill('6')
  await distance.press('Enter')
  // Every column under the face grows 6 mm except the two through-holes.
  await expect(status).toHaveText(/92371.27 mm³/)

  const crashed = page.evaluate(() =>
    (window as unknown as { simplecadDrill: { crashEngine(): Promise<unknown> } }).simplecadDrill.crashEngine().then(
      () => 'answered',
      (e: Error) => e.message
    )
  )
  expect(await crashed).toMatch(/restarted/)
  await expect(page.getByTestId('engine-connections')).toHaveText('2')
  await expect(status).toHaveText(/Engine ready · 92371.27 mm³/)
  await page.getByRole('button', { name: 'Undo', exact: true }).click()
  await expect(status).toHaveText(/71016.32 mm³/)

  // The hidden 10 mm box: shown, it adds its volume and its faces, named by body; the history is untouched.
  await page.getByRole('button', { name: 'Show Spare' }).click()
  await expect(status).toHaveText(/72016.32 mm³/)
  await expect(page.getByLabel('Planar face').locator('option', { hasText: 'Spare · Top · 100 mm²' })).toHaveCount(1)
  await page.getByRole('button', { name: 'Redo', exact: true }).click()
  await expect(status).toHaveText(/93371.27 mm³/)
  await page.getByRole('button', { name: 'Hide Spare' }).click()
  await expect(status).toHaveText(/92371.27 mm³/)
})

test('preserves saved and new history through worker restart and Save As', async () => {
  const scratch = await mkdtemp(path.join(os.tmpdir(), 'simplecad-history-'))
  const original = path.join(scratch, 'Original.scad3')
  const copied = path.join(scratch, 'Undone.scad3')
  const volume = (page: Awaited<ReturnType<typeof launch>>['page']) => page.getByTestId('engine-status')
  const pull = async (page: Awaited<ReturnType<typeof launch>>['page'], amount: string) => {
    await page.getByLabel('Planar face').selectOption({ label: 'Top · 1200 mm²' })
    const distance = page.getByLabel('Pull distance')
    await distance.fill(amount)
    await distance.press('Enter')
  }
  const saveAs = async (app: ElectronApplication, page: Awaited<ReturnType<typeof launch>>['page'], filePath: string) => {
    await app.evaluate(({ dialog }, filePath) => {
      dialog.showSaveDialog = async () => ({ filePath, canceled: false })
    }, filePath)
    await page.getByRole('button', { name: 'File', exact: true }).click()
    await page.getByRole('menuitem', { name: /^Save As/ }).click()
    await expect(page.getByText(path.basename(filePath), { exact: true })).toBeVisible()
  }
  const history = (page: Awaited<ReturnType<typeof launch>>['page'], name: 'Undo' | 'Redo') =>
    page.getByRole('button', { name, exact: true })
  try {
    const first = await launch()
    await expect(volume(first.page)).toHaveText(/24000 mm³/)
    await pull(first.page, '2')
    await expect(volume(first.page)).toHaveText(/26400 mm³/)
    await saveAs(first.app, first.page, original)
    await quitCleanly(first.app)

    const second = await launch({ args: [original], env: { SIMPLECAD_ENGINE_DRILL: '1' } })
    const { app, page } = second
    await expect(volume(page)).toHaveText(/26400 mm³/)
    await pull(page, '3')
    await expect(volume(page)).toHaveText(/30000 mm³/)
    await history(page, 'Undo').click()
    await expect(volume(page)).toHaveText(/26400 mm³/)
    await history(page, 'Undo').click()
    await expect(volume(page)).toHaveText(/24000 mm³/)
    await page.evaluate(() =>
      (window as unknown as { simplecadDrill: { crashEngine(): Promise<unknown> } })
        .simplecadDrill.crashEngine().catch(() => {}))
    await expect(page.getByTestId('engine-connections')).toHaveText('2')
    await expect(volume(page)).toHaveText(/24000 mm³/)
    await expect(history(page, 'Redo')).toBeEnabled()
    await saveAs(app, page, copied)
    await quitCleanly(app)

    const last = await launch({ args: [copied], env: { SIMPLECAD_ENGINE_DRILL: '1' } })
    await expect(volume(last.page)).toHaveText(/24000 mm³/)
    await history(last.page, 'Redo').click()
    await expect(volume(last.page)).toHaveText(/26400 mm³/)
    await history(last.page, 'Redo').click()
    await expect(volume(last.page)).toHaveText(/30000 mm³/)
    await history(last.page, 'Undo').click()
    await expect(volume(last.page)).toHaveText(/26400 mm³/)
    await pull(last.page, '1')
    await expect(volume(last.page)).toHaveText(/27600 mm³/)
    await expect(history(last.page, 'Redo')).toBeDisabled()
    await last.page.evaluate(() =>
      (window as unknown as { simplecadDrill: { crashEngine(): Promise<unknown> } })
        .simplecadDrill.crashEngine().catch(() => {}))
    await expect(last.page.getByTestId('engine-connections')).toHaveText('2')
    await expect(volume(last.page)).toHaveText(/27600 mm³/)
    await expect(history(last.page, 'Redo')).toBeDisabled()
  } finally {
    await rm(scratch, { recursive: true, force: true })
  }
})

test('saves a pull into the project and reopens it', async () => {
  const { app, page } = await launch()
  const status = page.getByTestId('engine-status')
  await expect(status).toHaveText(/24000 mm³/)
  // A copy in the test profile: the committed fixture is never written.
  const copy = await app.evaluate(async ({ app: electron }, source) => {
    const fs = process.getBuiltinModule('node:fs')
    const target = `${electron.getPath('userData')}/features-copy.scad3`
    fs.copyFileSync(source, target)
    return target
  }, fixture)
  await app.evaluate(({ dialog }, filePath) => {
    dialog.showOpenDialog = async () => ({ filePaths: [filePath], canceled: false })
  }, copy)
  const file = page.getByRole('button', { name: 'File', exact: true })
  await file.click()
  await page.getByRole('menuitem', { name: 'Open project' }).click()
  await expect(status).toHaveText(/71016.32 mm³/)
  await page.getByLabel('Planar face').selectOption({ label: 'Top · 3251.73 mm²' })
  const distance = page.getByLabel('Pull distance')
  await distance.fill('6')
  await distance.press('Enter')
  await expect(status).toHaveText(/92371.27 mm³/)
  await expect(page.getByText('features-copy.scad3 •')).toBeVisible()
  const steps = page.getByRole('list', { name: 'Model history' }).locator('li')
  await expect(steps).toHaveText(['Imported solid', 'Imported solid2', 'Pull'])

  await file.click()
  await page.getByRole('menuitem', { name: /^Save(?! As)/ }).click()
  await expect(page.getByText('features-copy.scad3', { exact: true })).toBeVisible()

  // Saved, so a new model starts without asking; reopening the file reads the pull back from it.
  await file.click()
  await page.getByRole('menuitem', { name: 'New box' }).click()
  await expect(status).toHaveText(/24000 mm³/)
  await file.click()
  await page.getByRole('menuitem', { name: 'Open project' }).click()
  await expect(status).toHaveText(/92371.27 mm³/)

  // A new model saves to a new file, asked for, never over the one open before it.
  await file.click()
  await page.getByRole('menuitem', { name: 'New box' }).click()
  await expect(status).toHaveText(/24000 mm³/)
  const fresh = await app.evaluate(({ app: electron, dialog }) => {
    const target = `${electron.getPath('userData')}/new-box.scad3`
    dialog.showSaveDialog = async () => ({ filePath: target, canceled: false })
    return target
  })
  await file.click()
  await page.getByRole('menuitem', { name: /^Save(?! As)/ }).click()
  await expect(page.getByText('new-box.scad3', { exact: true })).toBeVisible()
  await file.click()
  await page.getByRole('menuitem', { name: 'Open project' }).click()
  await expect(status).toHaveText(/92371.27 mm³/)
  await app.evaluate(({ dialog }, filePath) => {
    dialog.showOpenDialog = async () => ({ filePaths: [filePath], canceled: false })
  }, fresh)
  await file.click()
  await page.getByRole('menuitem', { name: 'Open project' }).click()
  await expect(status).toHaveText(/Engine ready · 24000 mm³/)
})

test('the viewport picks a face and the drag handle commits a pull', { tag: '@viewport' }, async () => {
  const { page } = await launch()
  const status = page.getByTestId('engine-status')
  await expect(status).toHaveText(/24000 mm³/)
  const viewport = page.locator('canvas')
  await expect(viewport).toHaveAttribute('aria-busy', 'false')
  // Wait for the maximized canvas to settle before sending pointer events.
  await viewport.hover()
  await expect(viewport).toHaveAttribute('data-hover-ms', /\d/)
  // The middle of the view is the box's near corner, where a click takes an edge: click its top face.
  await page.mouse.click(...await partAt(page, 0))
  await expect(page.getByLabel('Planar face')).not.toHaveValue('')
  await page.getByLabel('Planar face').selectOption({ label: 'Top · 1200 mm²' })
  const handle = page.getByRole('button', { name: 'Drag face along its normal' })
  await expect(handle).toBeVisible()
  const target = (await handle.boundingBox())!
  const x = target.x + target.width / 2
  const y = target.y + target.height / 2
  await page.mouse.move(x, y)
  await page.mouse.down()
  await page.mouse.move(x, y - 24, { steps: 6 })
  await page.mouse.up()
  await expect(page.getByRole('list', { name: 'Model history' }).locator('li')).toHaveText(['Box', 'Pull'])
  await expect(status).not.toHaveText(/24000 mm³/)
})

test('command search executes the same command as the File menu', async () => {
  const { app, page } = await launch()
  await expect(page.getByTestId('engine-status')).toHaveText(/24000 mm³/)
  await offerFixture(app)
  await page.getByRole('button', { name: 'Command search' }).click()
  const search = page.getByLabel('Search commands')
  await search.fill('open project')
  await search.press('Enter')
  await expect(page.getByRole('dialog', { name: 'Command search' })).toHaveCount(0)
  await expect(page.getByRole('complementary', { name: 'Model inspector' })).toContainText('Plate')
})
