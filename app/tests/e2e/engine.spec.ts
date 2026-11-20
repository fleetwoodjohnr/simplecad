import type { ElectronApplication } from '@playwright/test'
import path from 'node:path'
import os from 'node:os'
import { copyFile, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { expect, launch, test } from './helpers'

test('pushes a face in the native worker, rejects an invalid push, and undoes and redoes', async () => {
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
  await expect(page.getByRole('alert')).toContainText('straight through this body')
  await expect(status).toHaveText(/24000 mm³/)

  await distance.fill('-3')
  await distance.press('Enter')
  await expect(status).toHaveText(/20400 mm³/)
  await page.getByRole('button', { name: 'Undo', exact: true }).click()
  await expect(status).toHaveText(/24000 mm³/)
  await page.getByRole('button', { name: 'Redo', exact: true }).click()
  await expect(status).toHaveText(/20400 mm³/)
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

test('imports a B-Rep compound as independently editable bodies in one undo step', async () => {
  const { app, page } = await launch()
  const status = page.getByTestId('engine-status')
  await expect(status).toHaveText(/24000 mm³/)
  const scratch = await mkdtemp(path.join(os.tmpdir(), 'simplecad-brep-'))
  const source = path.join(scratch, 'two-cubes.brep')
  const target = path.join(scratch, 'BRep.scad3')
  try {
    await copyFile(path.join(__dirname, 'fixtures', 'two-cubes.brep'), source)
    await app.evaluate(({ dialog }, filePath) => {
      dialog.showOpenDialog = async () => ({ filePaths: [filePath], canceled: false })
    }, source)
    await page.getByRole('button', { name: 'File', exact: true }).click()
    await page.getByRole('menuitem', { name: /Import/ }).click()
    await expect(status).toHaveText(/24250 mm³/)
    const bodies = page.getByRole('list', { name: 'Bodies' })
    await expect(bodies.getByText('two-cubes 1', { exact: true })).toBeVisible()
    await expect(bodies.getByText('two-cubes 2', { exact: true })).toBeVisible()
    await page.getByRole('button', { name: 'Undo', exact: true }).click()
    await expect(status).toHaveText(/24000 mm³/)
    await page.getByRole('button', { name: 'Redo', exact: true }).click()
    await expect(status).toHaveText(/24250 mm³/)
    await app.evaluate(({ dialog }, filePath) => {
      dialog.showSaveDialog = async () => ({ filePath, canceled: false })
      dialog.showOpenDialog = async () => ({ filePaths: [filePath], canceled: false })
    }, target)
    await page.getByRole('button', { name: 'File', exact: true }).click()
    await page.getByRole('menuitem', { name: 'Save As' }).click()
    await expect(page.getByText('BRep.scad3', { exact: true })).toBeVisible()
    await rm(source)
    await page.getByRole('button', { name: 'File', exact: true }).click()
    await page.getByRole('menuitem', { name: 'New box' }).click()
    await expect(status).toHaveText(/24000 mm³/)
    await page.getByRole('button', { name: 'File', exact: true }).click()
    await page.getByRole('menuitem', { name: 'Open project' }).click()
    await expect(status).toHaveText(/24250 mm³/)
  } finally {
    await rm(scratch, { recursive: true, force: true })
  }
})

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
  await page.getByRole('button', { name: 'Stack & Place', exact: true }).click()
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

test('the viewport picks a face and the drag handle commits a pull', async () => {
  const { page } = await launch()
  const status = page.getByTestId('engine-status')
  await expect(status).toHaveText(/24000 mm³/)
  const viewport = page.locator('canvas')
  await expect(viewport).toHaveAttribute('aria-busy', 'false')
  const canvas = await viewport.boundingBox()
  // Hovering picks the face under the pointer, once a frame.
  await page.mouse.move(canvas!.x + canvas!.width / 2, canvas!.y + canvas!.height / 2)
  await expect(viewport).toHaveAttribute('data-hover-ms', /\d/)
  await page.mouse.click(canvas!.x + canvas!.width / 2, canvas!.y + canvas!.height / 2)
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
