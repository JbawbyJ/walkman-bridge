'use strict'

const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const crypto = require('node:crypto')
const vm = require('node:vm')
const { EventEmitter } = require('node:events')

const ORIGIN = 'http://127.0.0.1:45679'
const source = fs.readFileSync(path.join(__dirname, 'main.cjs'), 'utf8')
const deferred = () => { let resolve; const promise = new Promise(yes => { resolve = yes }); return { promise, resolve } }
const flush = async () => { for (let i = 0; i < 10; i++) await new Promise(setImmediate) }

// Execute the production entry point with only OS/Electron boundaries replaced.
// No backend, socket, helper, device or user-data file is created by these tests.
function harness(options = {}) {
  const backend = new EventEmitter()
  backend.stdout = new EventEmitter(); backend.stderr = new EventEmitter()
  backend.exitCode = null; backend.killed = false
  const state = { windows: [], dialogs: [], requests: [], brokerStops: 0, brokerStarts: 0, quits: 0, kills: 0, cookiesRemoved: 0, cookie: null }
  backend.kill = () => { state.kills++; backend.killed = true; backend.exitCode = 0; backend.emit('exit', 0) }
  const ipcMain = new EventEmitter()
  ipcMain.handle = () => {}
  const isolated = {
    cookies: {
      async set(cookie) { if (options.cookieGate) await options.cookieGate.promise; state.cookie = cookie.value },
      async remove() { state.cookiesRemoved++; state.cookie = null },
    },
    webRequest: { onBeforeRequest(handler) { state.beforeRequest = handler } },
    setPermissionRequestHandler() {}, setPermissionCheckHandler() {},
    async closeAllConnections() { state.connectionsClosed = true },
  }
  class Window extends EventEmitter {
    constructor(windowOptions) {
      super(); this.destroyed = false; this.options = windowOptions; state.windows.push(this)
      this.webContents = new EventEmitter()
      Object.assign(this.webContents, {
        mainFrame: { url: ORIGIN }, setWindowOpenHandler() {},
        async executeJavaScript() {}, stop() { state.playbackStopped = true },
        send: (channel, nonce) => {
          if (channel === 'playback:stop' && !options.noStopAck) queueMicrotask(() => ipcMain.emit(options.stopFailed ? 'playback:stop-failed' : 'playback:stopped', { sender: this.webContents, senderFrame: this.webContents.mainFrame }, nonce))
        },
      })
    }
    isDestroyed() { return this.destroyed }
    async loadURL() {}
    show() { state.shown = true }
    destroy() { if (this.destroyed) return; this.destroyed = true; this.webContents.emit('destroyed'); app.emit('window-all-closed') }
  }
  const app = new Proxy(new EventEmitter(), { get(target, key) {
    if (key === 'isPackaged') return false
    if (key === 'requestSingleInstanceLock') return () => true
    if (key === 'whenReady') return () => Promise.resolve()
    if (key === 'quit' || key === 'exit') return () => { state.quits++ }
    return key in target ? target[key] : () => {}
  } })
  const fakeFs = { mkdirSync() {}, appendFileSync() {}, existsSync: () => true, readdirSync: () => [] }
  const context = vm.createContext({
    __dirname, process: { argv: [], env: { LOCALAPPDATA: 'C:\\review-no-files' } },
    console: { log() {}, error() {} }, Buffer, URL, AbortSignal, AbortController, setTimeout, clearTimeout,
    fetch: async (url, init) => {
      state.requests.push(new URL(url).pathname)
      return options.fetch ? options.fetch(url, init) : ({ ok: true, json: async () => url.endsWith('/api/health') ? { ok: true, product: 'bridge' } : { draining: true, busy: false } })
    },
    require(name) {
      if (name === 'electron') return { app, BrowserWindow: Window, ipcMain,
        screen: { getCursorScreenPoint: () => ({ x: 0, y: 0 }), getDisplayNearestPoint: () => ({ workArea: options.workArea || { x: 0, y: 0, width: 1920, height: 1040 } }) },
        session: { fromPartition: () => isolated }, dialog: {
        showErrorBox(title, message) { state.dialogs.push({ title, message }) },
        async showMessageBox(...args) { state.dialogs.push(args.at(-1)); if (options.dialogGate) await options.dialogGate.promise; return { response: 0 } },
      } }
      if (name === 'node:fs') return fakeFs
      if (name === 'node:child_process') return { spawn(_exe, _args, opts) {
        queueMicrotask(() => backend.stdout.emit('data', 'NIGHTOPS_READY ' + JSON.stringify({ port: 45679, proof: crypto.createHmac('sha256', opts.env.NIGHTOPS_TOKEN).update('45679').digest('hex') }) + '\n'))
        return backend
      } }
      if (name === './scanner-broker.cjs') return { createScannerBroker(opts) {
        state.brokerOptions = opts
        return { async start() { state.brokerStarts++ }, stop() { state.brokerStops++ } }
      } }
      if (name === './boundary.cjs') return require('./boundary.cjs')
      return require(name)
    },
  })
  vm.runInContext(source, context, { filename: 'electron/main.cjs' })
  return { state, backend, invoke: code => vm.runInContext(code, context),
    die() { backend.exitCode = 17; backend.emit('exit', 17) },
    allowed() { let result; state.beforeRequest({ url: ORIGIN + '/api/media/id/stream' }, value => { result = value }); return !result.cancel },
  }
}

test('unexpected backend death immediately blocks renderer/native traffic and destroys playback', async () => {
  const pending = deferred(), dialogGate = deferred(); let requestSignal
  const h = harness({ dialogGate, fetch: async (url, init) => {
    if (url.endsWith('/api/inflight')) { requestSignal = init.signal; return pending.promise }
    return { ok: true, json: async () => ({ ok: true, product: 'bridge' }) }
  } })
  await flush()
  assert.equal(h.state.shown, true)
  const inflight = h.invoke("request('GET', '/api/inflight')")
  const rejected = assert.rejects(inflight, /backend|connection/i)
  h.die()
  assert.equal(h.allowed(), false)
  assert.equal(h.state.windows[0].isDestroyed(), true)
  assert.ok(h.state.brokerStops > 0)
  assert.equal(requestSignal.aborted, true)
  pending.resolve({ ok: true, json: async () => ({ ok: true }) })
  await rejected
  await flush()
  assert.equal(h.state.cookie, null)
  assert.ok(h.state.cookiesRemoved > 0)
  assert.equal(h.state.dialogs.length, 1)
  assert.match(h.state.dialogs[0].message, /backend|connection/i)
  assert.match(h.state.dialogs[0].detail, /transfer|deletion/i)
  assert.match(h.state.dialogs[0].detail, /verify|check/i)
  assert.equal(h.state.kills, 0)
  assert.equal(h.state.quits, 0, 'window-all-closed must not skip cookie revocation or the unknown-write dialog')
  dialogGate.resolve()
  await flush()
  assert.ok(h.state.quits > 0)
})

test('backend death during health check cannot resume startup with a stale response', async () => {
  const health = deferred()
  const h = harness({ fetch: () => health.promise })
  await flush()
  h.die()
  health.resolve({ ok: true, json: async () => ({ ok: true, product: 'bridge' }) })
  await flush()
  assert.equal(h.state.windows.length, 0)
  assert.equal(h.state.brokerStarts, 0)
  assert.equal(h.state.dialogs.length, 1)
})

test('a cookie write finishing after backend death is revoked before any renderer starts', async () => {
  const cookieGate = deferred()
  const h = harness({ cookieGate })
  await flush()
  h.die()
  cookieGate.resolve()
  await flush()
  assert.equal(h.state.windows.length, 0)
  assert.equal(h.state.cookie, null)
})

test('a dead child fails closed even before its exit callback is delivered', async () => {
  const h = harness()
  await flush()
  h.backend.exitCode = 17
  assert.equal(h.allowed(), false)
  await assert.rejects(h.invoke("request('GET', '/api/health')"), /backend|connection/i)
  assert.equal(h.state.brokerOptions.isBackendAlive(), false)
  h.die()
  await flush()
})

test('confirmed idle shutdown terminates the backend without an unexpected-loss dialog', async () => {
  const h = harness()
  await flush()
  await h.invoke('closeSafely()')
  assert.equal(h.state.kills, 1)
  assert.equal(h.state.dialogs.length, 0)
  assert.equal(h.state.windows[0].isDestroyed(), true)
  assert.deepEqual(h.state.requests.slice(1), ['/api/internal/playback/release', '/api/shutdown/drain', '/api/engine-busy'])
})

test('failed playback stop acknowledgment retains its lease and does not kill the backend', async () => {
  const h = harness({ stopFailed: true })
  await flush()
  await h.invoke('closeSafely()')
  assert.equal(h.state.kills, 0)
  assert.deepEqual(h.state.requests, ['/api/health'])
  assert.equal(h.state.windows[0].isDestroyed(), false)
})

test('renderer crash releases playback only after destruction and still drains background work', async () => {
  let h
  const busy = deferred()
  h = harness({ fetch: async url => {
    if (url.endsWith('/api/health')) return { ok: true, json: async () => ({ ok: true, product: 'bridge' }) }
    assert.equal(h.state.windows[0].isDestroyed(), true)
    if (url.endsWith('/api/engine-busy')) return busy.promise
    return { ok: true, json: async () => ({ ok: true }) }
  } })
  await flush()
  h.state.windows[0].webContents.emit('render-process-gone', {}, { reason: 'crashed' })
  await flush()
  assert.deepEqual(h.state.requests.slice(1), ['/api/internal/playback/release', '/api/shutdown/drain', '/api/engine-busy'])
  assert.equal(h.state.kills, 0)
  busy.resolve({ ok: true, json: async () => ({ busy: false, draining: true }) })
  await flush()
  assert.equal(h.state.kills, 1)
  assert.equal(h.state.dialogs.length, 0)
})

test('renderer crash while stop acknowledgment is pending releases the lease without waiting for timeout', async () => {
  const h = harness({ noStopAck: true })
  await flush()
  const shutdown = h.invoke('closeSafely()')
  await flush()
  assert.deepEqual(h.state.requests, ['/api/health'])
  h.state.windows[0].webContents.emit('render-process-gone', {}, { reason: 'crashed' })
  await shutdown
  assert.equal(h.state.kills, 1)
  assert.equal(h.state.dialogs.length, 0)
})
