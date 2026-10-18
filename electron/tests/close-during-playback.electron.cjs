'use strict'

// Closes production main while the renderer is playing and the React stop hook
// has not subscribed yet. The fixture is the only HTTP server; no Python engine,
// scanner, decoder or device is started.
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
const outputDir = path.join(root, 'packaging/build/player-close-during-playback')
fs.mkdirSync(outputDir, { recursive: true })
const profile = fs.mkdtempSync(path.join(outputDir, 'profile-'))
const report = { electron: process.versions.electron, started_at: new Date().toISOString(), checks: [] }
let window, server, nativeToken, context, testFinished = false
let quitCalls = 0, ready = false, stopProof = null
const mainPath = path.join(root, 'electron/main.cjs')
const mainRequire = createRequire(mainPath)
const backend = new EventEmitter()
backend.stdout = new EventEmitter(); backend.stderr = new EventEmitter()
backend.exitCode = null; backend.killed = false
backend.kill = () => { backend.killed = true; backend.exitCode = 0; backend.emit('exit', 0) }

app.commandLine.appendSwitch('autoplay-policy', 'no-user-gesture-required')
const wave = Buffer.alloc(44 + 4800)
wave.write('RIFF'); wave.writeUInt32LE(wave.length - 8, 4); wave.write('WAVEfmt ', 8)
wave.writeUInt32LE(16, 16); wave.writeUInt16LE(1, 20); wave.writeUInt16LE(1, 22)
wave.writeUInt32LE(8000, 24); wave.writeUInt32LE(16000, 28); wave.writeUInt16LE(2, 32)
wave.writeUInt16LE(16, 34); wave.write('data', 36); wave.writeUInt32LE(wave.length - 44, 40)

const waitFor = async (predicate, label) => {
  const deadline = Date.now() + 8000
  while (Date.now() < deadline) {
    if (await predicate()) return
    await new Promise(resolve => setTimeout(resolve, 20))
  }
  throw new Error(`Timed out: ${label}`)
}
const check = name => { report.checks.push(name) }

async function finish(error) {
  if (testFinished) return
  testFinished = true
  clearTimeout(watchdog)
  report.passed = !error
  report.finished_at = new Date().toISOString()
  report.quit_calls = quitCalls
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
const watchdog = setTimeout(() => { void finish(new Error('Close-during-playback harness exceeded 12 seconds')) }, 12000)
process.on('uncaughtException', error => { void finish(error) })
process.on('unhandledRejection', error => { void finish(error) })

void (async () => {
  server = http.createServer(async (request, response) => {
    const route = new URL(request.url, 'http://127.0.0.1').pathname
    const native = nativeToken && request.headers['x-nightops-token'] === nativeToken
    const cookie = nativeToken && request.headers.cookie?.split(';').some(value => value.trim() === `nightops_session=${nativeToken}`)
    if (route === '/tone.wav') {
      response.writeHead(200, { 'Content-Type': 'audio/wav', 'Content-Length': wave.length, 'Accept-Ranges': 'bytes' })
      response.end(wave)
      return
    }
    if (!native && !cookie) { response.writeHead(403); response.end(); return }
    if (route.startsWith('/api/internal/') && !native) { response.writeHead(403); response.end(); return }
    const json = value => { response.writeHead(200, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' }); response.end(JSON.stringify(value)) }
    if (route === '/api/health') return json({ ok: true, product: 'player' })
    if (route === '/api/internal/scanner/pending') return json({ requests: [] })
    if (route === '/api/internal/playback/release') return json({ ok: true })
    if (route === '/api/shutdown/drain') return json({ draining: true, busy: false })
    if (route === '/api/engine-busy') return json({ draining: true, busy: false })
    if (route === '/playback-stop-proof' && request.method === 'POST') {
      const chunks = []
      for await (const chunk of request) chunks.push(chunk)
      stopProof = JSON.parse(Buffer.concat(chunks).toString() || '{}')
      return json({ ok: true })
    }
    if (route === '/player.js') {
      response.writeHead(200, { 'Content-Type': 'text/javascript' })
      response.end(`window.__audio = new Audio('/tone.wav'); window.__audio.loop = true; window.__stopSeen = false;
        window.addEventListener('walkman:stop-playback', () => { window.__stopSeen = true });
        window.__audio.play().then(() => { window.__playing = !window.__audio.paused }).catch(error => { window.__playing = false; window.__playError = error.message });`)
      return
    }
    response.writeHead(200, { 'Content-Type': 'text/html', 'Content-Security-Policy': "default-src 'self'; media-src 'self'; script-src 'self'" })
    response.end('<!doctype html><html><head><title>Red Lotus Player</title></head><body><script src="/player.js"></script></body></html>')
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  const port = server.address().port

  class HiddenWindow extends BrowserWindow {
    constructor(options) {
      super({ ...options, show: false, skipTaskbar: true, webPreferences: { ...options.webPreferences, backgroundThrottling: false } })
      window = this
    }
    show() { ready = true }
  }
  const appFacade = new Proxy(app, { get(target, key) {
    if (key === 'quit' || key === 'exit') return () => { quitCalls++ }
    const value = target[key]
    return typeof value === 'function' ? value.bind(target) : value
  } })
  const fakeFs = new Proxy(fs, { get(target, key) {
    if (key === 'existsSync') return candidate => String(candidate).endsWith('.exe') || fs.existsSync(candidate)
    return target[key]
  } })
  context = vm.createContext({ __dirname: path.join(root, 'electron'), console, Buffer, URL,
    AbortController, AbortSignal, fetch, setTimeout, clearTimeout,
    process: { argv: ['--product=player'], env: { ...process.env, LOCALAPPDATA: profile }, resourcesPath: root },
    require(name) {
      if (name === 'electron') return { ...electron, app: appFacade, BrowserWindow: HiddenWindow, dialog: {
        showErrorBox() {},
        async showMessageBox() { return { response: 0 } },
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
  await waitFor(() => ready, 'production window loaded')
  window.webContents.setAudioMuted(true)
  await waitFor(() => window.webContents.executeJavaScript('window.__playing === true'), 'renderer entered the playing state')
  check('renderer is playing before close')

  const started = Date.now()
  const closing = vm.runInContext('closeSafely()', context)
  await window.webContents.executeJavaScript(`(() => { window.walkmanBridge.onStopPlayback(async () => {
    const audio = window.__audio
    audio.pause(); audio.removeAttribute('src'); audio.load()
    await fetch('/playback-stop-proof', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ paused: audio.paused, src: audio.getAttribute('src') }) })
  }); return null })()`)
  const outcome = await Promise.race([
    closing.then(() => 'closed'),
    new Promise(resolve => setTimeout(() => resolve('still-running'), 4000)),
  ])
  const elapsed = Date.now() - started
  assert.equal(outcome, 'closed', `close did not finish (${outcome}) after ${elapsed}ms`)
  assert.ok(elapsed < 4000, `close took ${elapsed}ms`)
  assert.equal(stopProof?.paused, true)
  assert.equal(stopProof?.src, null)
  assert.ok(quitCalls > 0, 'main process did not quit')
  check('close during playback stopped audio and quit')
  report.elapsed_ms = elapsed
  await finish()
})().catch(error => { void finish(error) })
