import type { ElectronApplication } from '@playwright/test'
import path from 'node:path'
import { expect, launch, test } from './helpers'

test('pushes a face in the native worker, rejects an invalid push, and undoes and redoes', async () => {
  const { app, page } = await launch()
  const status = page.getByTestId('engine-status')
  await expect(status).toHaveText(/Engine ready · 6000 mm³/)
  await expect(page.locator('canvas')).toBeVisible()
  const mainModules = await app.evaluate(() => Object.keys(process.getBuiltinModule('node:module')!.createRequire(process.cwd() + '/package.json').cache).filter((m) => m.endsWith('.node')))
  expect(mainModules).toEqual([])

  await page.getByLabel('Planar face').selectOption({ label: 'Top · 600 mm²' })
  const distance = page.getByLabel('Pull distance')
  await distance.fill('-25')
  await distance.press('Enter')
  await expect(page.getByRole('alert')).toContainText('straight through this body')
  await expect(status).toHaveText(/6000 mm³/)

  await distance.fill('-3')
  await distance.press('Enter')
  await expect(status).toHaveText(/4200 mm³/)
  await page.getByRole('button', { name: 'Undo', exact: true }).click()
  await expect(status).toHaveText(/6000 mm³/)
  await page.getByRole('button', { name: 'Redo', exact: true }).click()
  await expect(status).toHaveText(/4200 mm³/)
})

test('the more actions menu fits above its trigger and Escape restores focus', async () => {
  const { app, page } = await launch()
  await app.evaluate(({ BrowserWindow }) => {
    const window = BrowserWindow.getAllWindows()[0]
    window.setSize(960, 600)
    window.webContents.setZoomFactor(2)
  })
  await page.getByLabel('Planar face').selectOption({ label: 'Top · 600 mm²' })
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
  await page.getByLabel('Planar face').selectOption({ label: 'Top · 600 mm²' })
  const distance = page.getByLabel('Pull distance')
  await distance.fill('-2')
  await distance.press('Enter')
  await expect(status).toHaveText(/4800 mm³/)

  const crashed = page.evaluate(() =>
    (window as unknown as { simplecadDrill: { crashEngine(): Promise<unknown> } }).simplecadDrill.crashEngine().then(
      () => 'answered',
      (e: Error) => e.message
    )
  )
  expect(await crashed).toMatch(/restarted/)
  await expect(page.getByTestId('engine-connections')).toHaveText('2')
  await expect(status).toHaveText(/Engine ready · 4800 mm³/)
  await page.getByRole('button', { name: 'Undo', exact: true }).click()
  await expect(status).toHaveText(/6000 mm³/)
})

const fixture = path.join(__dirname, 'fixtures', 'features.scad3')

/** Answer the next Open dialog with the fixture, a plate with a hole, a recess and a boss, and a hidden box. */
async function offerFixture(app: ElectronApplication) {
  await app.evaluate(({ dialog }, filePath) => {
    dialog.showOpenDialog = async () => ({ filePaths: [filePath], canceled: false })
  }, fixture)
}

test('opens a .scad3 project and pulls a face with its recess and boss, through a real engine crash', async () => {
  const { app, page } = await launch({ env: { SIMPLECAD_ENGINE_DRILL: '1' } })
  const status = page.getByTestId('engine-status')
  await expect(status).toHaveText(/6000 mm³/)
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

test('the viewport picks a face and the drag handle commits a pull', async () => {
  const { page } = await launch()
  const status = page.getByTestId('engine-status')
  await expect(status).toHaveText(/6000 mm³/)
  const viewport = page.locator('canvas')
  await expect(viewport).toHaveAttribute('aria-busy', 'false')
  const canvas = await viewport.boundingBox()
  // Hovering picks the face under the pointer, once a frame.
  await page.mouse.move(canvas!.x + canvas!.width / 2, canvas!.y + canvas!.height / 2)
  await expect(viewport).toHaveAttribute('data-hover-ms', /\d/)
  await page.mouse.click(canvas!.x + canvas!.width / 2, canvas!.y + canvas!.height / 2)
  await expect(page.getByLabel('Planar face')).not.toHaveValue('')
  await page.getByLabel('Planar face').selectOption({ label: 'Top · 600 mm²' })
  const handle = page.getByRole('button', { name: 'Drag face along its normal' })
  await expect(handle).toBeVisible()
  const target = (await handle.boundingBox())!
  const x = target.x + target.width / 2
  const y = target.y + target.height / 2
  await page.mouse.move(x, y)
  await page.mouse.down()
  await page.mouse.move(x, y - 24, { steps: 6 })
  await page.mouse.up()
  await expect(page.getByRole('list', { name: 'Model history' }).locator('li')).toHaveCount(1)
  await expect(status).not.toHaveText(/6000 mm³/)
})

test('command search executes the same command as the File menu', async () => {
  const { app, page } = await launch()
  await expect(page.getByTestId('engine-status')).toHaveText(/6000 mm³/)
  await offerFixture(app)
  await page.getByRole('button', { name: 'Command search' }).click()
  const search = page.getByLabel('Search commands')
  await search.fill('open project')
  await search.press('Enter')
  await expect(page.getByRole('dialog', { name: 'Command search' })).toHaveCount(0)
  await expect(page.getByRole('complementary', { name: 'Model inspector' })).toContainText('Plate')
})
