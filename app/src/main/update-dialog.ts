import { app, dialog, shell, type BrowserWindow } from 'electron'
import { existsSync } from 'node:fs'
import { readFile, rm } from 'node:fs/promises'
import path from 'node:path'
import keyText from '../../../simplecad/update_key.pub?raw'
import { checkRelease, downloadUpdate, RELEASES, replaceAppImage, updateAsset, type InstallKind, type Release } from './updates'

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

async function downloadWithDialog(window: BrowserWindow, release: Release, asset: string) {
  const cancel = new AbortController()
  const dismiss = new AbortController()
  const onClose = () => cancel.abort()
  window.once('closed', onClose)
  const prompt = dialog.showMessageBox(window, {
    type: 'info', message: `Downloading SimpleCAD ${release.version}`,
    detail: 'The installer will be verified before it opens.',
    buttons: ['Cancel'], cancelId: 0, signal: dismiss.signal
  }).then(() => {
    if (!dismiss.signal.aborted) cancel.abort()
  }, onClose)
  try {
    return await downloadUpdate(release, asset, path.join(app.getPath('userData'), 'updates'),
      Buffer.from(keyText.trim(), 'base64'), fetch, cancel.signal)
  } catch (err) {
    if (!cancel.signal.aborted) throw err
  } finally {
    dismiss.abort()
    window.removeListener('closed', onClose)
    await prompt
  }
}

export async function checkForUpdates(window: BrowserWindow) {
  if (checking || window.isDestroyed()) return
  checking = true
  const version = app.getVersion()
  try {
    const kind = await installKind()
    if (window.isDestroyed()) return
    if (kind === 'flatpak') {
      await dialog.showMessageBox(window, {
        type: 'info',
        message: 'Flatpak manages SimpleCAD updates',
        detail: 'Use your software centre or run flatpak update io.github.fleetwoodjohnr.SimpleCAD.'
      })
      return
    }
    const release = await checkRelease(version)
    if (window.isDestroyed()) return
    if (!release) {
      await dialog.showMessageBox(window, { type: 'info', message: 'SimpleCAD is up to date', detail: `Installed version ${version}` })
      return
    }
    const asset = updateAsset(release, kind, process.arch)
    const go = asset ? 'Download and install' : 'View release'
    const { response } = await dialog.showMessageBox(window, {
      type: 'info', message: `SimpleCAD ${release.version} is available`,
      detail: release.notes.slice(0, 1800), buttons: ['Cancel', go], cancelId: 0, defaultId: 0
    })
    if (response !== 1 || window.isDestroyed()) return
    if (!asset) {
      await shell.openExternal(`${RELEASES}/tag/v${release.version}`)
      return
    }

    const update = await downloadWithDialog(window, release, asset)
    if (!update) return
    if (window.isDestroyed()) {
      await rm(path.dirname(update.installer), { recursive: true, force: true })
      return
    }
    if (kind === 'appimage') {
      try {
        await replaceAppImage(update.installer, process.env.APPIMAGE!, update.expected)
      } finally {
        await rm(path.dirname(update.installer), { recursive: true, force: true })
      }
      if (!window.isDestroyed()) await dialog.showMessageBox(window, {
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
