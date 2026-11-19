// SimpleCAD's main process: one window, the geometry engine in a utility process, and nothing reachable from the
// page beyond the few calls preload exposes. Security settings and patterns follow the rest of the Simple suite.

import { app, BrowserWindow, dialog, ipcMain, nativeTheme, session, type IpcMainInvokeEvent } from 'electron'
import path from 'node:path'
import { EngineHost } from './engine-host'
import { checkForUpdates } from './update-dialog'
import { openProjectFile, saveProjectFile } from './project-files'

const isMac = process.platform === 'darwin'
let mainWindow: BrowserWindow | null = null

// Packaged, the native addon is unpacked from the asar archive; in development it is built into
// app/native by `npm run engine`.
const addon = app.isPackaged
  ? path.join(process.resourcesPath, 'app.asar.unpacked', 'native', 'scad_engine.node')
  : path.join(app.getAppPath(), 'native', 'scad_engine.node')
const engine = new EngineHost(addon, () => mainWindow?.webContents ?? null)

function createWindow() {
  const dark = nativeTheme.shouldUseDarkColors
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 920,
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
  mainWindow.webContents.on('will-prevent-unload', (event) => {
    if (!mainWindow) return
    const choice = dialog.showMessageBoxSync(mainWindow, {
      type: 'warning', message: 'Close with unsaved changes?',
      buttons: ['Keep editing', 'Discard changes'], defaultId: 0, cancelId: 0
    })
    if (choice === 1) event.preventDefault()
  })
  mainWindow.once('ready-to-show', () => mainWindow?.show())
  mainWindow.on('closed', () => {
    mainWindow = null
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
  handle('project:save', (_event, data: unknown, saveAs: unknown) => mainWindow ? saveProjectFile(mainWindow, data, saveAs) : undefined)
  handle('project:discard', async () => {
    if (!mainWindow) return false
    const { response } = await dialog.showMessageBox(mainWindow, {
      type: 'warning', message: 'Discard unsaved changes?', detail: 'Use Save to keep them.',
      buttons: ['Keep editing', 'Discard changes'], defaultId: 0, cancelId: 0
    })
    return response === 1
  })
  handle('updates:check', () => mainWindow ? checkForUpdates(mainWindow) : undefined)
  handle('window:minimize', () => mainWindow?.minimize())
  handle('window:toggle-maximize', () => (mainWindow?.isMaximized() ? mainWindow.unmaximize() : mainWindow?.maximize()))
  handle('window:close', () => mainWindow?.close())
}

if (process.env.SIMPLECAD_USER_DATA) app.setPath('userData', process.env.SIMPLECAD_USER_DATA)
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
  app.on('second-instance', () => {
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
  app.on('window-all-closed', () => {
    engine.stop()
    if (!isMac) app.quit()
  })
}
