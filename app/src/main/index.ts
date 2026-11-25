// SimpleCAD's main process: one window, the geometry engine in a utility process, and nothing reachable from the
// page beyond the few calls preload exposes. Security settings and patterns follow the rest of the Simple suite.

import { app, BrowserWindow, dialog, ipcMain, nativeTheme, session, type IpcMainInvokeEvent } from 'electron'
import { existsSync } from 'node:fs'
import path from 'node:path'
import { EngineHost } from './engine-host'
import { cancelRestart, checkForUpdates } from './update-dialog'
import { readLegacyPreferences } from './preferences'
import { acceptProjectFile, forgetProjectFile, offerProjectFile, openProjectFile, openModelFile, projectName, saveExportFile, saveProjectFile, timing } from './project-files'
import { sendToSlicer } from './slicer'
import { calibrationPath, readCalibration, saveCalibration } from './calibration'
import { describeAge, leftovers, readRecovered, recoveryDir, remove, sessionFile } from './recovery'

const isMac = process.platform === 'darwin'
let mainWindow: BrowserWindow | null = null
let pendingProject: string | undefined

function requestProject(file: string) {
  if (path.extname(file).toLowerCase() !== '.scad3') return
  pendingProject = file
  mainWindow?.webContents.send('project:requested')
}

function projectArgument(args: string[], workingDirectory: string) {
  const file = args.slice(app.isPackaged ? 1 : 2)
    .find((value) => !value.startsWith('-') && path.extname(value).toLowerCase() === '.scad3')
  if (file) requestProject(path.resolve(workingDirectory, file))
}

// Packaged, the native addon is unpacked from the asar archive; in development it is built into
// app/native by `npm run engine`.
const addon = app.isPackaged
  ? path.join(process.resourcesPath, 'app.asar.unpacked', 'native', 'scad_engine.node')
  : path.join(app.getAppPath(), 'native', 'scad_engine.node')
const recovery = recoveryDir()
const autosaveFile = sessionFile(recovery)
/** Recovery files whose work this session now holds: they go when it ends cleanly. */
let recovered: string[] = []
let sessionCleanup: Promise<void> | undefined
let quitState: 'running' | 'cleaning' | 'ready' = 'running'
const engine = new EngineHost(addon, () => mainWindow?.webContents ?? null, {
  SIMPLECAD_AUTOSAVE_FILE: autosaveFile,
  SIMPLECAD_AUTOSAVE_SECONDS: process.env.SIMPLECAD_AUTOSAVE_SECONDS ?? '90'
})

function createWindow() {
  if (sessionCleanup) {
    const closing = sessionCleanup
    void closing.then(() => {
      if (sessionCleanup === closing) sessionCleanup = undefined
      if (!mainWindow && quitState === 'running') createWindow()
    })
    return
  }
  const dark = nativeTheme.shouldUseDarkColors
  // SIMPLECAD_WINDOW=1024x700 opens it that size, as a small screen does (the e2e specs check the view so).
  const [width, height] = process.env.SIMPLECAD_WINDOW?.match(/^(\d+)x(\d+)$/)?.slice(1).map(Number) ?? [1440, 920]
  mainWindow = new BrowserWindow({
    width,
    height,
    minWidth: 960,
    minHeight: 600,
    show: false,
    title: 'SimpleCAD',
    backgroundColor: dark ? '#121214' : '#f7f7f8',
    titleBarStyle: isMac ? 'hiddenInset' : 'hidden',
    trafficLightPosition: { x: 14, y: 14 },
    titleBarOverlay: isMac ? undefined : { color: '#00000000', symbolColor: dark ? '#d4d4d8' : '#3f3f46', height: 44 },
    webPreferences: {
      preload: path.join(__dirname, '../preload/index.js'),
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
      webviewTag: false,
      spellcheck: false
    }
  })
  // The page holds the close back while there is unsaved work, as SimpleCAD 0.4 does (main_window.closeEvent).
  mainWindow.webContents.on('will-prevent-unload', (event) => {
    if (!mainWindow) return
    const choice = dialog.showMessageBoxSync(mainWindow, {
      type: 'warning', message: 'Close SimpleCAD? The model has unsaved changes.',
      buttons: ['Save', "Don't Save", 'Cancel'], defaultId: 0, cancelId: 2
    })
    if (choice === 1) event.preventDefault()
    // Cancel keeps SimpleCAD open, so an update's Restart now is off too.
    if (choice === 2) cancelRestart()
    // The page saves, and closes once the file is written; a cancelled or failed save keeps the window.
    if (choice === 0) mainWindow.webContents.send('app:save-and-close')
  })
  mainWindow.once('ready-to-show', () => mainWindow?.show())
  mainWindow.on('closed', () => {
    mainWindow = null
    forgetProjectFile()
  })
  if (process.env.ELECTRON_RENDERER_URL) void mainWindow.loadURL(process.env.ELECTRON_RENDERER_URL)
  else void mainWindow.loadFile(path.join(__dirname, '../renderer/index.html'))
  // Every (re)load of the page gets a fresh port to the engine.
  mainWindow.webContents.on('did-finish-load', () => void engine.start().then(() => engine.connectRenderer()))
}

function hardenSessions() {
  const allowed = new Set(['clipboard-read', 'clipboard-sanitized-write', 'fullscreen'])
  session.defaultSession.setPermissionRequestHandler((_wc, permission, cb) => cb(allowed.has(permission)))
  session.defaultSession.setPermissionCheckHandler((_wc, permission) => allowed.has(permission))
  app.on('web-contents-created', (_e, contents) => {
    contents.on('will-navigate', (ev, url) => {
      if (process.env.ELECTRON_RENDERER_URL && url.startsWith(process.env.ELECTRON_RENDERER_URL)) return
      ev.preventDefault()
    })
    contents.setWindowOpenHandler(() => ({ action: 'deny' }))
  })
}

function handle<A extends unknown[], R>(channel: string, fn: (e: IpcMainInvokeEvent, ...args: A) => Promise<R> | R) {
  ipcMain.handle(channel, (e, ...args) => {
    if (e.sender !== mainWindow?.webContents) throw new Error('Unknown sender')
    return fn(e, ...(args as A))
  })
}

function registerIpc() {
  handle('project:open', () => mainWindow ? openProjectFile(mainWindow) : undefined)
  handle('project:pending', async () => {
    const target = pendingProject
    pendingProject = undefined
    if (target) return offerProjectFile(target)
  })
  handle('project:accept', (_event, token: unknown) => acceptProjectFile(token))
  handle('model:import', () => mainWindow ? openModelFile(mainWindow) : undefined)
  handle('project:save', (_event, data: unknown, saveAs: unknown) => mainWindow ? saveProjectFile(mainWindow, data, saveAs) : undefined)
  handle('export:save', (_event, data: unknown, format: unknown) => mainWindow ? saveExportFile(mainWindow, data, format) : undefined)
  handle('slicer:send', (_event, data: unknown) => sendToSlicer(data, projectName()))
  handle('calibration:read', () => readCalibration())
  handle('calibration:save', (_event, data: unknown) => saveCalibration(data))
  handle('project:new', () => forgetProjectFile())
  handle('recovery:pending', async () => {
    const [newest] = await leftovers(recovery, autosaveFile)
    return newest ? describeAge(newest.modified) : undefined
  })
  // The newest leftover, opened as unsaved work with no file: Save asks where.
  handle('recovery:open', async () => {
    const found = await leftovers(recovery, autosaveFile)
    if (!found.length) return
    const data = await readRecovered(found[0].file)
    forgetProjectFile()
    recovered = found.map((f) => f.file)
    return { name: 'Recovered.scad3', data }
  })
  handle('recovery:discard', async () => remove((await leftovers(recovery, autosaveFile)).map((f) => f.file)))
  handle('project:discard', async () => {
    if (!mainWindow) return false
    const { response } = await dialog.showMessageBox(mainWindow, {
      type: 'warning', message: 'Discard unsaved changes?', detail: 'Use Save to keep them.',
      buttons: ['Keep editing', 'Discard changes'], defaultId: 0, cancelId: 0
    })
    return response === 1
  })
  handle('preferences:legacy', () => readLegacyPreferences())
  // `quiet`: the automatic check (once a day, silent unless there is an update).
  handle('updates:check', (_event, quiet?: unknown) => mainWindow ? checkForUpdates(mainWindow, quiet === true) : undefined)
  handle('window:minimize', () => mainWindow?.minimize())
  handle('window:toggle-maximize', () => (mainWindow?.isMaximized() ? mainWindow.unmaximize() : mainWindow?.maximize()))
  handle('window:close', () => mainWindow?.close())
}

if (process.env.SIMPLECAD_USER_DATA) app.setPath('userData', process.env.SIMPLECAD_USER_DATA)
if (process.platform === 'linux' && existsSync('/.flatpak-info')) app.setDesktopName('io.github.fleetwoodjohnr.SimpleCAD.desktop')
// Without a usable GPU (a VM, remote desktop, a CI runner) Chromium now refuses WebGL on its software
// rasterizer unless told otherwise, and the 3D view stays blank. The "unsafe" is about untrusted web pages
// reaching that rasterizer; this window shows only the app's own sandboxed pages.
app.commandLine.appendSwitch('enable-unsafe-swiftshader')

// The packaging and release checks call these on every build, headless, as they do the Python app.
if (process.argv.includes('--version')) {
  console.log(app.getVersion())
  app.exit(0)
} else if (process.argv.includes('--self-test')) {
  try {
    // A real OCCT round trip through the shipped addon and its libraries: a box, its top pulled 5 mm.
    type Display = { volume: number; faces: { planar: boolean; normal: number[] }[] }
    type Shape = { display(): Display; pushPull(face: number, distance: number): Shape }
    const { Shape } = require(addon) as { Shape: { makeBox(x: number, y: number, z: number): Shape } }
    const box = Shape.makeBox(10, 10, 10)
    const top = box.display().faces.findIndex((f) => f.planar && f.normal[2] > 0.99)
    const volume = box.pushPull(top, 5).display().volume
    if (Math.abs(volume - 1500) > 1e-6) throw new Error(`a 10 mm cube pulled 5 mm has volume ${volume}`)
    console.log(`self-test: SimpleCAD ${app.getVersion()} OK`)
    app.exit(0)
  } catch (error) {
    console.error(`self-test failed: ${String(error)}`)
    app.exit(1)
  }
} else if (!app.requestSingleInstanceLock()) {
  app.quit()
} else {
  projectArgument(process.argv, process.cwd())
  app.on('open-file', (event, file) => {
    event.preventDefault()
    requestProject(file)
    if (app.isReady() && !mainWindow) createWindow()
  })
  app.on('second-instance', (_event, args, workingDirectory) => {
    projectArgument(args, workingDirectory)
    if (!mainWindow && app.isReady()) createWindow()
    if (mainWindow?.isMinimized()) mainWindow.restore()
    mainWindow?.focus()
  })
  void app.whenReady().then(() => {
    app.setName('SimpleCAD')
    hardenSessions()
    registerIpc()
    void engine.start()
    createWindow()
    app.on('activate', () => {
      if (!mainWindow) createWindow()
    })
  })
  // Stop the worker before deleting autosaves. On macOS the app can stay open
  // without a window, and an explicit Quit uses Electron's will-quit path.
  const cleanSession = () => {
    if (sessionCleanup) return sessionCleanup
    const started = performance.now()
    timing('quit: engine stop asked', started)
    return sessionCleanup = engine.stop().then(async () => {
      timing('quit: engine stopped', started)
      await remove([autosaveFile, ...recovered])
      recovered = []
      timing('quit: autosaves removed', started)
    })
  }
  app.on('window-all-closed', () => {
    if (isMac) void cleanSession()
    else app.quit()
  })
  app.on('will-quit', (event) => {
    if (quitState === 'ready') return
    event.preventDefault()
    if (quitState === 'cleaning') return
    quitState = 'cleaning'
    const started = performance.now()
    void cleanSession().finally(() => {
      timing('quit: quitting', started)
      quitState = 'ready'
      app.quit()
    })
  })
}
