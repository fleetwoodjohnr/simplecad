import { contextBridge, ipcRenderer } from 'electron'
import type { SimpleCadApi } from '../shared/ipc-types'

// The engine port is a MessagePort, which contextBridge can't pass: post it into the page's own window instead.
// The page only accepts it from its own window (event.source === window) with this exact message.
ipcRenderer.on('engine:port', (e) => window.postMessage('simplecad:engine-port', '*', e.ports))

const api: SimpleCadApi = {
  platform: process.platform,
  openProject: () => ipcRenderer.invoke('project:open'),
  pendingProject: () => ipcRenderer.invoke('project:pending'),
  acceptProject: (token) => ipcRenderer.invoke('project:accept', token),
  onProjectRequested: (run) => {
    const listener = () => run()
    ipcRenderer.on('project:requested', listener)
    return () => ipcRenderer.removeListener('project:requested', listener)
  },
  openStep: () => ipcRenderer.invoke('step:open'),
  saveProject: (data, saveAs) => ipcRenderer.invoke('project:save', data, saveAs),
  saveExport: (data, format) => ipcRenderer.invoke('export:save', data, format),
  confirmDiscard: () => ipcRenderer.invoke('project:discard'),
  newFile: () => ipcRenderer.invoke('project:new'),
  pendingRecovery: () => ipcRenderer.invoke('recovery:pending'),
  recoverWork: () => ipcRenderer.invoke('recovery:open'),
  discardRecovery: () => ipcRenderer.invoke('recovery:discard'),
  onSaveAndClose: (run) => {
    const listener = () => run()
    ipcRenderer.on('app:save-and-close', listener)
    return () => ipcRenderer.removeListener('app:save-and-close', listener)
  },
  checkForUpdates: () => ipcRenderer.invoke('updates:check'),
  windowMinimize: () => ipcRenderer.invoke('window:minimize'),
  windowToggleMaximize: () => ipcRenderer.invoke('window:toggle-maximize'),
  windowClose: () => ipcRenderer.invoke('window:close')
}

contextBridge.exposeInMainWorld('simplecad', api)
