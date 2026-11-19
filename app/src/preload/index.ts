import { contextBridge, ipcRenderer } from 'electron'
import type { SimpleCadApi } from '../shared/ipc-types'

// The engine port is a MessagePort, which contextBridge can't pass: post it into the page's own window instead.
// The page only accepts it from its own window (event.source === window) with this exact message.
ipcRenderer.on('engine:port', (e) => window.postMessage('simplecad:engine-port', '*', e.ports))

const api: SimpleCadApi = {
  platform: process.platform,
  openProject: () => ipcRenderer.invoke('project:open'),
  confirmDiscard: () => ipcRenderer.invoke('project:discard'),
  checkForUpdates: () => ipcRenderer.invoke('updates:check'),
  windowMinimize: () => ipcRenderer.invoke('window:minimize'),
  windowToggleMaximize: () => ipcRenderer.invoke('window:toggle-maximize'),
  windowClose: () => ipcRenderer.invoke('window:close')
}

contextBridge.exposeInMainWorld('simplecad', api)
