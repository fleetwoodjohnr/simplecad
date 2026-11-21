import { expect, it, vi } from 'vitest'
import type { BrowserWindow } from 'electron'
import { checkForUpdates } from '../../../src/main/update-dialog'

const calls = vi.hoisted(() => ({ checkRelease: vi.fn(), showMessageBox: vi.fn() }))

vi.mock('node:fs', () => ({ existsSync: (path: string) => path === '/.flatpak-info' }))
vi.mock('electron', () => ({
  app: { getVersion: () => '0.5.0' },
  dialog: { showMessageBox: calls.showMessageBox },
  shell: {}
}))
vi.mock('../../../src/main/updates', () => ({
  checkRelease: calls.checkRelease, downloadUpdate: vi.fn(),
  RELEASES: 'https://example.test/releases', replaceAppImage: vi.fn(), updateAsset: vi.fn()
}))

it('hands responsibility to Flatpak without checking GitHub or downloading an installer', async () => {
  const window = { isDestroyed: () => false } as unknown as BrowserWindow
  await checkForUpdates(window)
  expect(calls.checkRelease).not.toHaveBeenCalled()
  expect(calls.showMessageBox).toHaveBeenCalledWith(window, expect.objectContaining({
    message: 'Flatpak manages SimpleCAD updates',
    detail: expect.stringContaining('flatpak update io.github.fleetwoodjohnr.SimpleCAD')
  }))
})
