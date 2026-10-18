'use strict'

const { contextBridge, ipcRenderer } = require('electron')

// Listen for the whole page lifetime. usePlayer subscribes after the health gate
// and then signals playback:ready; main waits for that before sending stop.
let playbackStopCallback = null
let pendingPlaybackNonce = null
let playbackStopRunning = false

async function acknowledgePlaybackStop(nonce) {
  const callback = playbackStopCallback
  try {
    if (typeof callback === 'function') await callback()
    ipcRenderer.send('playback:stopped', nonce)
  } catch {
    ipcRenderer.send('playback:stop-failed', nonce)
  }
}

function pumpPlaybackStop() {
  if (playbackStopRunning || typeof playbackStopCallback !== 'function' || pendingPlaybackNonce === null) return
  playbackStopRunning = true
  const nonce = pendingPlaybackNonce
  pendingPlaybackNonce = null
  void acknowledgePlaybackStop(nonce).finally(() => {
    playbackStopRunning = false
    if (pendingPlaybackNonce !== null) pumpPlaybackStop()
  })
}

ipcRenderer.on('playback:stop', (_event, nonce) => {
  pendingPlaybackNonce = nonce
  pumpPlaybackStop()
})

contextBridge.exposeInMainWorld('walkmanBridge', {
  product: process.argv.find(value => value.startsWith('--nightops-product='))?.split('=')[1] || 'bridge',
  minimize: () => ipcRenderer.invoke('win:minimize'),
  maximize: () => ipcRenderer.invoke('win:maximize'),
  close: () => ipcRenderer.invoke('win:close'),
  onStopPlayback: (callback) => {
    playbackStopCallback = callback
    ipcRenderer.send('playback:ready')
    pumpPlaybackStop()
    return () => { if (playbackStopCallback === callback) playbackStopCallback = null }
  },
  onDraining: (callback) => {
    const handler = (_event, state) => callback(state)
    ipcRenderer.on('app:draining', handler)
    return () => ipcRenderer.removeListener('app:draining', handler)
  },
})
