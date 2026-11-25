import { app, dialog, shell, type BrowserWindow } from 'electron'
import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { readFile, rm, writeFile } from 'node:fs/promises'
import path from 'node:path'
import keyText from '../../../simplecad/update_key.pub?raw'
import { checkRelease, downloadUpdate, dueForCheck, installCommand, RELEASES, replaceAppImage, updateAsset, windowsUpdateFlags,
  type InstallKind, type Release } from './updates'

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

/** When the automatic check last ran, kept beside the settings: it runs at most once a day. */
const lastCheckFile = () => path.join(app.getPath('userData'), 'update-check.json')
async function claimAutomaticCheck() {
  const last = Number(JSON.parse(await readFile(lastCheckFile(), 'utf8').catch(() => '{}')).last) || 0
  const now = Date.now()
  if (!dueForCheck(last, now)) return false
  await writeFile(lastCheckFile(), JSON.stringify({ last: now })).catch(() => {})
  return true
}

/** Close the window the usual way (unsaved work is asked about), then start the new version once it has closed.
 * Cancelling that question leaves SimpleCAD running, and it doesn't restart later. */
let restarting: (() => void) | undefined
export function cancelRestart() {
  restarting = undefined
}
function restartWhenClosed(window: BrowserWindow, start: () => void) {
  restarting = start
  window.once('closed', () => {
    const run = restarting
    restarting = undefined
    if (!run) return
    run()
    app.quit()
  })
  window.close()
}

/** Runs a command, resolving with its exit code (127 when it can't start). */
const run = (command: string, args: string[]) => new Promise<number>((resolve) => {
  const child = spawn(command, args, { stdio: 'ignore' })
  child.on('error', () => resolve(127))
  child.on('exit', (code) => resolve(code ?? 1))
})

async function offerRestart(window: BrowserWindow, version: string, start: () => void) {
  if (window.isDestroyed()) return
  const { response } = await dialog.showMessageBox(window, {
    type: 'info', message: `SimpleCAD ${version} is installed`, detail: 'Restart SimpleCAD to use it. Unsaved work is asked about first.',
    buttons: ['Later', 'Restart now'], defaultId: 1, cancelId: 0
  })
  if (response === 1 && !window.isDestroyed()) restartWhenClosed(window, start)
}
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

/** Look for a newer release. `quiet`, the automatic check: at most once a day, and nothing is shown unless there is
 * an update (no "up to date", no error, nothing under Flatpak, which updates SimpleCAD itself). Nothing is installed
 * without a click, and SimpleCAD restarts only when asked to. */
export async function checkForUpdates(window: BrowserWindow, quiet = false) {
  if (checking || window.isDestroyed()) return
  if (quiet && (process.env.SIMPLECAD_UPDATE_CHECK === '0' || !app.isPackaged || !await claimAutomaticCheck())) return
  checking = true
  const version = app.getVersion()
  try {
    const kind = await installKind()
    if (window.isDestroyed()) return
    if (kind === 'flatpak') {
      if (quiet) return
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
      if (!quiet) await dialog.showMessageBox(window, { type: 'info', message: 'SimpleCAD is up to date', detail: `Installed version ${version}` })
      return
    }
    const asset = updateAsset(release, kind, process.arch)
    const go = asset ? 'Install' : 'View release'
    const { response } = await dialog.showMessageBox(window, {
      type: 'info', message: `SimpleCAD ${release.version} is available`,
      detail: release.notes.slice(0, 1800), buttons: ['Later', go], cancelId: 0, defaultId: 1
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
      const image = process.env.APPIMAGE!
      await offerRestart(window, release.version, () => app.relaunch({ execPath: image }))
    } else if (kind === 'rpm' || kind === 'deb') {
      // The system's package manager installs it (pkexec asks for the password); then SimpleCAD can restart.
      const [command, args] = installCommand(kind, update.installer, process.getuid?.() === 0)
      const code = await run(command, args)
      await rm(path.dirname(update.installer), { recursive: true, force: true }).catch(() => {})
      if (code === 0) await offerRestart(window, release.version, () => app.relaunch())
      else throw new Error(code === 126 || code === 127
        ? `The update was not installed. Install it from ${RELEASES}/tag/v${release.version}, or run your package manager on the downloaded file.`
        : `The package manager stopped with code ${code}. Nothing was changed.`)
    } else if (kind === 'windows') {
      // Inno Setup closes and updates SimpleCAD, keeping how it was installed, then starts it again (/RELAUNCH).
      const installer = update.installer
      const { response } = await dialog.showMessageBox(window, {
        type: 'info', message: `SimpleCAD ${release.version} is ready to install`,
        detail: 'SimpleCAD closes to install it, and starts again when it is done. Unsaved work is asked about first.',
        buttons: ['Later', 'Install and restart'], defaultId: 1, cancelId: 0
      })
      if (response === 1 && !window.isDestroyed()) {
        restartWhenClosed(window, () => spawn(installer, windowsUpdateFlags, { detached: true, stdio: 'ignore' }).unref())
      }
    } else {
      const err = await shell.openPath(update.installer)
      if (err) throw new Error(`Could not open the verified installer. It is available at ${update.installer}.`)
    }
  } catch (err) {
    if (quiet) console.warn(`update check: ${(err as Error).message}`)
    else if (!window.isDestroyed()) await dialog.showMessageBox(window, {
      type: 'warning', message: 'SimpleCAD could not complete the update', detail: (err as Error).message
    })
  } finally {
    checking = false
  }
}
