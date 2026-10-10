'use strict'

// Acceptance harness: actual Electron windows, preload, IPC and session, running
// production main/broker against an authenticated, test-only HTTP backend.
// No Python engine, native scanner, audio decoder or device is launched.
const electron = require('electron')
const { app, BrowserWindow } = electron
const assert = require('node:assert/strict')
const crypto = require('node:crypto')
const fs = require('node:fs')
const http = require('node:http')
const path = require('node:path')
const vm = require('node:vm')
const { createRequire } = require('node:module')
const { EventEmitter } = require('node:events')

const root = path.resolve(__dirname, '../..')
const outputDir = path.join(root, 'packaging/build/electron-lifecycle-acceptance')
fs.mkdirSync(outputDir, { recursive: true })
const profile = fs.mkdtempSync(path.join(outputDir, 'profile-'))
const report = { electron: process.versions.electron, profile, started_at: new Date().toISOString(), checks: [] }
let window, server, nativeToken, context, testFinished = false
let backendKills = 0, quitCalls = 0, stops = 0, busyCalls = 0, failProbe = true, ready = false
const dialogs = [], requests = []
const mainPath = path.join(root, 'electron/main.cjs')
const mainRequire = createRequire(mainPath)
const backend = new EventEmitter()
backend.stdout = new EventEmitter(); backend.stderr = new EventEmitter()
backend.exitCode = null; backend.killed = false
backend.kill = () => { backendKills++; backend.killed = true; backend.exitCode = 0; backend.emit('exit', 0) }

const waitFor = async (predicate, label) => {
  const deadline = Date.now() + 10000
  while (!predicate()) {
    if (Date.now() > deadline) throw new Error(`Timed out: ${label}`)
    await new Promise(resolve => setTimeout(resolve, 20))
  }
}
const check = (name, details = {}) => { report.checks.push({ name, passed: true, ...details }) }

async function finish(error) {
  if (testFinished) return
  testFinished = true
  clearTimeout(watchdog)
  report.passed = !error
  report.finished_at = new Date().toISOString()
  report.backend_kills = backendKills
  report.busy_responses = busyCalls
  if (error) report.error = error.stack || String(error)
  fs.writeFileSync(path.join(outputDir, 'proof.json'), JSON.stringify(report, null, 2))
  console.log(JSON.stringify(report))
  if (context) {
    try { vm.runInContext('broker?.stop()', context) } catch { /* Best effort test cleanup. */ }
  }
  if (window && !window.isDestroyed()) window.destroy()
  server?.closeAllConnections()
  server?.close()
  app.exit(error ? 1 : 0)
}
const watchdog = setTimeout(() => { void finish(new Error('Electron lifecycle harness exceeded 40 seconds')) }, 40000)
process.on('uncaughtException', error => { void finish(error) })
process.on('unhandledRejection', error => { void finish(error) })

void (async () => {
  server = http.createServer((request, response) => {
    const route = new URL(request.url, 'http://127.0.0.1').pathname
    const native = nativeToken && request.headers['x-nightops-token'] === nativeToken
    const cookie = nativeToken && request.headers.cookie?.split(';').some(value => value.trim() === `nightops_session=${nativeToken}`)
    if (!native && !cookie) { response.writeHead(403); response.end(); return }
    if (route.startsWith('/api/internal/') && !native) { response.writeHead(403); response.end(); return }
    const json = (value, status = 200) => { response.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' }); response.end(JSON.stringify(value)) }
    if (route.startsWith('/api/')) requests.push(route)
    if (route === '/api/health') return json({ ok: true, product: 'player' })
    if (route === '/api/internal/scanner/pending') return json({ requests: [] })
    if (route === '/api/internal/playback/release') {
      assert.ok(stops > 0, 'native lease release must follow actual preload Stop ACK')
      return json({ ok: true })
    }
    if (route === '/api/shutdown/drain') return json({ draining: true, busy: true })
    if (route === '/api/engine-busy') {
      assert.equal(backendKills, 0, 'backend killed before its final idle response')
      if (failProbe) return json({ detail: 'fixture cannot determine operation status' }, 503)
      busyCalls++
      return json({ draining: true, busy: busyCalls <= 3 })
    }
    if (route === '/fixture.js') {
      response.writeHead(200, { 'Content-Type': 'text/javascript' })
      response.end("window.fixtureStops = 0; window.walkmanBridge.onStopPlayback(async () => { window.fixtureStops++; }); document.body.dataset.ready = 'yes';")
      return
    }
    response.writeHead(200, { 'Content-Type': 'text/html', 'Content-Security-Policy': "default-src 'self'; script-src 'self'" })
    response.end('<!doctype html><html><head><title>Red Lotus lifecycle acceptance</title></head><body><script src="/fixture.js"></script></body></html>')
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  const port = server.address().port

  class HiddenWindow extends BrowserWindow {
    constructor(options) {
      super({ ...options, show: false, skipTaskbar: true, x: -32000, y: -32000 })
      window = this
    }
    // Production calls show() after load. Keep this acceptance window hidden.
    show() { ready = true }
  }
  const appFacade = new Proxy(app, { get(target, key) {
    if (key === 'quit' || key === 'exit') return () => { quitCalls++ }
    const value = target[key]
    return typeof value === 'function' ? value.bind(target) : value
  } })
  electron.ipcMain.on('playback:stopped', event => {
    if (event.sender === window?.webContents) stops++
  })
  const fakeFs = new Proxy(fs, { get(target, key) {
    if (key === 'existsSync') return candidate => String(candidate).endsWith('.exe') || fs.existsSync(candidate)
    return target[key]
  } })
  context = vm.createContext({ __dirname: path.join(root, 'electron'), console, Buffer, URL,
    AbortController, AbortSignal, fetch, setTimeout, clearTimeout,
    process: { argv: ['--product=player'], env: { ...process.env, LOCALAPPDATA: profile }, resourcesPath: root },
    require(name) {
      if (name === 'electron') return { ...electron, app: appFacade, BrowserWindow: HiddenWindow, dialog: {
        showErrorBox(title, message) { dialogs.push({ title, message }) },
        async showMessageBox(...args) { dialogs.push(args.at(-1)); return { response: 0 } },
      } }
      if (name === 'node:fs') return fakeFs
      if (name === 'node:child_process') return { spawn(_exe, _args, options) {
        nativeToken = options.env.NIGHTOPS_TOKEN
        setImmediate(() => backend.stdout.emit('data', 'NIGHTOPS_READY ' + JSON.stringify({ port, proof: crypto.createHmac('sha256', nativeToken).update(String(port)).digest('hex') }) + '\n'))
        return backend
      } }
      return mainRequire(name)
    },
  })
  vm.runInContext(fs.readFileSync(mainPath, 'utf8'), context, { filename: mainPath })
  await waitFor(() => ready, 'production main loaded its BrowserWindow')
  assert.equal(window.isVisible(), false)
  const boundary = await window.webContents.executeJavaScript("({ product: window.walkmanBridge.product, hiddenCookie: !document.cookie.includes('nightops_session'), ready: document.body.dataset.ready, node: typeof require })")
  assert.deepEqual(boundary, { product: 'player', hiddenCookie: true, ready: 'yes', node: 'undefined' })
  check('actual sandboxed preload, HTTP session and renderer loaded', boundary)

  await window.webContents.executeJavaScript('window.walkmanBridge.minimize()')
  await waitFor(() => window.isMinimized(), 'minimize IPC')
  app.emit('second-instance')
  await waitFor(() => !window.isMinimized(), 'second-instance restores minimized window')
  window.hide()
  await window.webContents.executeJavaScript('window.walkmanBridge.maximize()')
  await waitFor(() => window.isMaximized(), 'maximize IPC')
  await window.webContents.executeJavaScript('window.walkmanBridge.maximize()')
  await waitFor(() => !window.isMaximized(), 'maximize IPC toggles restore')
  window.hide()
  check('actual minimize, restore, maximize and unmaximize through production IPC')

  await window.webContents.executeJavaScript('window.walkmanBridge.close()')
  await waitFor(() => dialogs.length > 0, 'probe failure dialog')
  assert.match(dialogs[0].message, /cannot confirm/)
  assert.equal(backendKills, 0)
  assert.equal(quitCalls, 0)
  assert.equal(window.isDestroyed(), false)
  assert.equal(await window.webContents.executeJavaScript('window.fixtureStops'), 1)
  check('HTTP 503 busy probe preserves backend and window after actual Stop ACK')

  failProbe = false
  const closeStarted = Date.now()
  await window.webContents.executeJavaScript('window.walkmanBridge.close()')
  await waitFor(() => quitCalls > 0, 'three busy responses followed by idle shutdown')
  const elapsed = Date.now() - closeStarted
  assert.equal(busyCalls, 4)
  assert.ok(elapsed >= 2800, `drain returned too early: ${elapsed}ms`)
  assert.equal(backendKills, 1)
  assert.equal(window.isDestroyed(), true)
  assert.equal(dialogs.length, 1)
  const cookies = await electron.session.fromPartition('persist:nightops-player').cookies.get({ url: `http://127.0.0.1:${port}`, name: 'nightops_session' })
  assert.equal(cookies.length, 0)
  check('close waits for three busy responses then idle, revokes cookie and destroys window', { elapsed_ms: elapsed, busy_responses: 3, idle_responses: 1 })
  report.request_sequence = requests.filter(route => !route.endsWith('/pending'))
  await finish()
})().catch(error => { void finish(error) })
