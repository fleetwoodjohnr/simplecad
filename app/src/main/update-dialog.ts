import { app, dialog, shell, type BrowserWindow } from 'electron'
import { existsSync } from 'node:fs'
import { readFile, rm } from 'node:fs/promises'
import path from 'node:path'
import keyText from '../../../simplecad/update_key.pub?raw'
import { checkRelease, downloadUpdate, RELEASES, replaceAppImage, updateAsset, type InstallKind } from './updates'

async function installKind(): Promise<InstallKind> {
  if (existsSync('/.flatpak-info')) return 'flatpak'
  if (process.env.APPIMAGE) return 'appimage'
  if (!app.isPackaged) return 'source'
  if (process.platform === 'win32') return 'windows'
  if (process.platform === 'darwin') return 'macos'
  const marker = await readFile(path.join(process.resourcesPath, 'install-kind'), 'utf8').catch(() => '')
  const kind = marker.trim()
  return kind === 'deb' || kind === 'rpm' ? kind : 'source'
}

let checking = false

export async function checkForUpdates(window: BrowserWindow) {
  if (checking) return
  checking = true
  const version = app.getVersion()
  try {
    const release = await checkRelease(version)
    if (!release) {
      await dialog.showMessageBox(window, { type: 'info', message: 'SimpleCAD is up to date', detail: `Installed version ${version}` })
      return
    }
    const kind = await installKind()
    const asset = updateAsset(release, kind, process.arch)
    const go = asset ? 'Download and install' : 'View release'
    const { response } = await dialog.showMessageBox(window, {
      type: 'info', message: `SimpleCAD ${release.version} is available`,
      detail: release.notes.slice(0, 1800), buttons: ['Cancel', go], cancelId: 0, defaultId: 0
    })
    if (response !== 1) return
    if (!asset) {
      await shell.openExternal(`${RELEASES}/tag/v${release.version}`)
      return
    }

    const update = await downloadUpdate(release, asset, path.join(app.getPath('userData'), 'updates'), Buffer.from(keyText.trim(), 'base64'))
    if (kind === 'appimage') {
      try {
        await replaceAppImage(update.installer, process.env.APPIMAGE!, update.expected)
      } finally {
        await rm(path.dirname(update.installer), { recursive: true, force: true })
      }
      await dialog.showMessageBox(window, {
        type: 'info', message: 'The update is ready', detail: 'Close SimpleCAD when ready and reopen it to start the new version.'
      })
    } else {
      const err = await shell.openPath(update.installer)
      if (err) throw new Error(`Could not open the verified installer. It is available at ${update.installer}.`)
    }
  } catch (err) {
    if (!window.isDestroyed()) await dialog.showMessageBox(window, {
      type: 'warning', message: 'SimpleCAD could not complete the update', detail: (err as Error).message
    })
  } finally {
    checking = false
  }
}
