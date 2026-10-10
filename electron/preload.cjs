'use strict'

const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('walkmanBridge', {
  product: process.argv.find(value => value.startsWith('--nightops-product='))?.split('=')[1] || 'bridge',
  minimize: () => ipcRenderer.invoke('win:minimize'),
  maximize: () => ipcRenderer.invoke('win:maximize'),
  close: () => ipcRenderer.invoke('win:close'),
  onStopPlayback: (callback) => {
    const handler = async (_event, nonce) => {
      try { await callback(); ipcRenderer.send('playback:stopped', nonce) }
      catch { ipcRenderer.send('playback:stop-failed', nonce) }
    }
    ipcRenderer.on('playback:stop', handler)
    return () => ipcRenderer.removeListener('playback:stop', handler)
  },
  onDraining: (callback) => {
    const handler = (_event, state) => callback(state)
    ipcRenderer.on('app:draining', handler)
    return () => ipcRenderer.removeListener('app:draining', handler)
  },
})
