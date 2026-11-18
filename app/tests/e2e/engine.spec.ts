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
  await expect(page.getByRole('alert')).toContainText('through this wall')
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

test('saves and reopens a native project with editable history', async () => {
  const { app, page } = await launch()
  await app.evaluate(({ app: electronApp, dialog }) => {
    const filePath = `${electronApp.getPath('userData')}/saved.scad3`
    dialog.showSaveDialog = async () => ({ filePath, canceled: false })
    dialog.showOpenDialog = async () => ({ filePaths: [filePath], canceled: false })
  })
  await page.getByLabel('Planar face').selectOption({ label: 'Top · 600 mm²' })
  const distance = page.getByLabel('Pull distance')
  await distance.fill('-3')
  await distance.press('Enter')
  const status = page.getByTestId('engine-status')
  await expect(status).toHaveText(/4200 mm³/)

  await page.getByRole('button', { name: 'File', exact: true }).click()
  await page.getByRole('menuitem', { name: 'Save As' }).click()
  await expect(page.getByText('saved.scad3', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'File', exact: true }).click()
  await page.getByRole('menuitem', { name: 'New box' }).click()
  await expect(status).toHaveText(/6000 mm³/)
  await page.getByRole('button', { name: 'File', exact: true }).click()
  await page.getByRole('menuitem', { name: 'Open project' }).click()
  await expect(status).toHaveText(/4200 mm³/)
  await page.getByRole('button', { name: 'Undo', exact: true }).click()
  await expect(status).toHaveText(/6000 mm³/)
})

test('the viewport picks a face and the drag handle commits a pull', async () => {
  const { page } = await launch()
  const status = page.getByTestId('engine-status')
  await expect(status).toHaveText(/6000 mm³/)
  const viewport = page.locator('canvas')
  await expect(viewport).toHaveAttribute('aria-busy', 'false')
  const canvas = await viewport.boundingBox()
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
  const { page } = await launch()
  await expect(page.getByTestId('engine-status')).toHaveText(/6000 mm³/)
  await page.getByRole('button', { name: 'Command search' }).click()
  const search = page.getByLabel('Search commands')
  await search.fill('recessed wall')
  await search.press('Enter')
  await expect(page.getByRole('dialog', { name: 'Command search' })).toHaveCount(0)
  await expect(page.getByRole('complementary', { name: 'Model inspector' })).toContainText('Recessed wall')
})

test('the recessed enclosure supports the same two wall offsets as Atlas', async () => {
  const { page } = await launch()
  const status = page.getByTestId('engine-status')
  await expect(status).toHaveText(/6000 mm³/)
  await page.getByRole('button', { name: 'File', exact: true }).click()
  await page.getByRole('menuitem', { name: 'Open recessed wall example' }).click()
  await expect(status).toHaveText(/114328 mm³/)
  const plane = page.getByLabel('Planar face')
  const distance = page.getByLabel('Pull distance')
  await plane.selectOption({ label: 'Back · 6568 mm²' })
  await distance.fill('50.75')
  await distance.press('Enter')
  await expect(status).toHaveText(/447654 mm³/)
  await plane.selectOption({ label: 'Front · 4540 mm²' })
  await distance.fill('-49.75')
  await distance.press('Enter')
  await expect(status).toHaveText(/165074 mm³/)
})
