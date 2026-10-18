'use strict'

const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const { EventEmitter } = require('node:events')

const source = fs.readFileSync(path.join(__dirname, 'preload.cjs'), 'utf8')
const flush = async () => { for (let i = 0; i < 10; i++) await new Promise(setImmediate) }

function loadPreload() {
  const sent = []
  const ipcRenderer = new EventEmitter()
  ipcRenderer.send = (...args) => { sent.push(args) }
  const exposed = {}
  vm.runInNewContext(source, {
    require(name) {
      if (name !== 'electron') throw new Error(`Unexpected preload require: ${name}`)
      return {
        contextBridge: { exposeInMainWorld(key, value) { exposed[key] = value } },
        ipcRenderer,
      }
    },
    process: { argv: ['--nightops-product=player'] },
  }, { filename: 'electron/preload.cjs' })
  return { sent, ipcRenderer, api: exposed.walkmanBridge }
}

test('a stop that arrives before the renderer subscribes still stops playback and acknowledges', async () => {
  const { sent, ipcRenderer, api } = loadPreload()
  let playing = true
  ipcRenderer.emit('playback:stop', {}, 'nonce-early')
  await flush()
  assert.equal(sent.length, 0, 'the stop stays queued until the player hook can run')
  api.onStopPlayback(async () => { playing = false })
  await flush()
  assert.equal(playing, false)
  assert.deepEqual(sent, [['playback:ready'], ['playback:stopped', 'nonce-early']])
})

test('a subscribed stop callback pauses playback before the acknowledgment', async () => {
  const { sent, ipcRenderer, api } = loadPreload()
  let playing = true
  api.onStopPlayback(async () => { playing = false })
  ipcRenderer.emit('playback:stop', {}, 'nonce-live')
  await flush()
  assert.equal(playing, false)
  assert.deepEqual(sent, [['playback:ready'], ['playback:stopped', 'nonce-live']])
})

test('a stop callback that fails to save acknowledges stop-failed', async () => {
  const { sent, ipcRenderer, api } = loadPreload()
  api.onStopPlayback(async () => { throw new Error('session save failed') })
  ipcRenderer.emit('playback:stop', {}, 'nonce-fail')
  await flush()
  assert.deepEqual(sent, [['playback:ready'], ['playback:stop-failed', 'nonce-fail']])
})

test('a stop that arrives while the hook is unsubscribed runs the replacement callback', async () => {
  const { sent, ipcRenderer, api } = loadPreload()
  const remove = api.onStopPlayback(async () => { throw new Error('stale hook') })
  remove()
  ipcRenderer.emit('playback:stop', {}, 'nonce-replace')
  await flush()
  assert.deepEqual(sent, [['playback:ready']])
  let playing = true
  api.onStopPlayback(async () => { playing = false })
  await flush()
  assert.equal(playing, false)
  assert.deepEqual(sent, [['playback:ready'], ['playback:ready'], ['playback:stopped', 'nonce-replace']])
})
