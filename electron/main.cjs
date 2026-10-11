'use strict'
const { app, BrowserWindow, dialog, ipcMain, session, screen } = require('electron')
const { spawn } = require('node:child_process')
const crypto = require('node:crypto')
const fs = require('node:fs')
const path = require('node:path')
const os = require('node:os')
const { ownOrigin, validSender, verifyAnnouncement, drainUntilIdle } = require('./boundary.cjs')
const { createScannerBroker } = require('./scanner-broker.cjs')

const root = path.resolve(__dirname, '..')
const resources = app.isPackaged ? process.resourcesPath : root
const productFile = path.join(resources, 'product.json')
const product = app.isPackaged ? JSON.parse(fs.readFileSync(productFile, 'utf8')).product :
  (process.argv.includes('--product=player') ? 'player' : 'bridge')
if (!['bridge', 'player'].includes(product)) throw new Error('Invalid installed product')
const name = product === 'player' ? 'Red Lotus Player' : 'Walkman Bridge'
const smoke = process.argv.includes('--smoke')
const dataDir = path.join(process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local'),
  product === 'player' ? 'Red Lotus Player' : 'Walkman Bridge')
app.setName(name)
// Stable identity preserves upgrades, taskbar association and separate products.
app.setAppUserModelId(`com.redlotus.nightops.${product}`)
app.setPath('userData', dataDir)
const token = crypto.randomBytes(48).toString('base64url')
let child, window, origin, broker, isolatedSession, cookieWrite
let closing = false, allowClose = false, playbackReady = false
const playbackReadyWaitMs = 3000
const smokeReadyWaitMs = 20000
let backendLost = false, expectedBackendExit = false, rendererGone = false
const backendLifetime = new AbortController()
fs.mkdirSync(dataDir, { recursive: true })
const log = line => {
  try { fs.appendFileSync(path.join(dataDir, 'redlotus.log'), `${new Date().toISOString()} ${line}\n`) }
  catch { /* Diagnostic storage must never interrupt fail-closed lifecycle handling. */ }
}

function isBackendAlive() {
  return !backendLost && !expectedBackendExit && !!child && child.exitCode === null && !child.killed
}
function requireBackend() {
  if (!isBackendAlive()) throw new Error('Backend connection has been lost')
}
async function revokeSession() {
  // A cookie write admitted just before exit must finish before its revocation.
  try { await cookieWrite } catch { /* Removal is still required after a failed write. */ }
  if (isolatedSession && origin) await isolatedSession.cookies.remove(origin, 'nightops_session')
}
function backendFailed(reason) {
  if (expectedBackendExit || backendLost) return
  // Invalidate every continuation synchronously, before cleanup or a dialog can yield.
  backendLost = true
  allowClose = true
  closing = true
  backendLifetime.abort(new Error('Backend connection has been lost'))
  broker?.stop()
  if (window && !window.isDestroyed()) {
    window.webContents.stop()
    window.destroy()
  }
  void (async () => {
    const cleanup = await Promise.allSettled([revokeSession(), isolatedSession?.closeAllConnections()])
    for (const result of cleanup) if (result.status === 'rejected') log(`Backend-loss cleanup: ${result.reason}`)
    if (!smoke) await dialog.showMessageBox({ type: 'error', buttons: ['Close app'],
      message: 'The backend connection was lost. Playback has stopped.',
      detail: 'Transfers or deletions that were in progress may have completed only partly. Their outcome is unknown. Restart the app and verify the device contents before retrying an operation.\n' + reason })
    app.quit()
  })().catch(error => { log(`Backend-loss shutdown: ${error.message}`); app.exit(1) })
}

function runtime(relative, fallback) {
  const bundled = path.join(resources, relative)
  if (fs.existsSync(bundled)) return bundled
  if (!app.isPackaged && fallback && fs.existsSync(fallback)) return fallback
  throw new Error(`Required runtime is missing: ${relative}`)
}
function startBackend() {
  const python = runtime('python/python.exe', path.join(root, 'backend', '.venv', 'Scripts', 'python.exe'))
  const backend = path.join(resources, 'backend')
  const env = { ...process.env, PYTHONUNBUFFERED: '1', PYTHONPATH: backend,
    NIGHTOPS_PRODUCT: product, NIGHTOPS_TOKEN: token, NIGHTOPS_DATA_DIR: dataDir,
    WALKMAN_JOBS_DB: path.join(dataDir, 'nightops.sqlite'),
    WALKMAN_BRIDGE_FFMPEG: runtime('ffmpeg/ffmpeg.exe', path.join(root, '..', 'Walkman Bridge', 'ffmpeg', 'ffmpeg.exe')) }
  // Packaged runtime paths are fixed. Inherited development overrides never enter a release.
  delete env.ELECTRON_RUN_AS_NODE
  delete env.PYTHONHOME
  delete env.NIGHTOPS_ORIGIN
  if (app.isPackaged) delete env.MOCK_DEVICE_PATH
  if (product === 'bridge') {
    const portableTools = path.join(root, '..', 'tools')
    const jdk = !app.isPackaged && fs.existsSync(portableTools) ? fs.readdirSync(portableTools).find(n => n.startsWith('jdk-')) : null
    env.WALKMAN_BRIDGE_JAVA = runtime('jre/bin/java.exe', jdk && path.join(portableTools, jdk, 'bin', 'java.exe'))
  } else delete env.WALKMAN_BRIDGE_JAVA
  return new Promise((resolve, reject) => {
    child = spawn(python, ['-B', '-m', 'boot'], { cwd: backend, env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] })
    let buffer = ''
    const deadline = setTimeout(() => reject(new Error('Backend startup timed out')), 60000)
    child.on('error', error => { clearTimeout(deadline); backendFailed(error.message); reject(error) })
    child.on('exit', code => { log(`Backend exited: ${code}`); clearTimeout(deadline); backendFailed(`Backend exited with code ${code}.`); reject(new Error(`Backend exited during startup: ${code}`)) })
    child.stdout.on('data', data => {
      buffer += data.toString()
      if (buffer.length > 65536) { reject(new Error('Invalid backend startup output')); return }
      let end
      while ((end = buffer.indexOf('\n')) !== -1) {
        const line = buffer.slice(0, end).trim(); buffer = buffer.slice(end + 1)
        if (line.startsWith('NIGHTOPS_READY ')) {
          try { const port = verifyAnnouncement(line.slice(15), token); clearTimeout(deadline); resolve(`http://127.0.0.1:${port}`) }
          catch (error) { clearTimeout(deadline); reject(error) }
        } else if (line) log(line)
      }
    })
    child.stderr.on('data', data => log(String(data).slice(0, 8192)))
  })
}
async function request(method, route) {
  requireBackend()
  const response = await fetch(origin + route, { method, headers: { 'X-NightOps-Token': token }, signal: AbortSignal.any([backendLifetime.signal, AbortSignal.timeout(5000)]) })
  requireBackend()
  if (!response.ok) throw new Error(`Backend ${route} returned ${response.status}`)
  const result = await response.json()
  requireBackend()
  return result
}
async function healthy() {
  const deadline = Date.now() + 60000
  while (Date.now() < deadline) {
    try {
      const health = await request('GET', '/api/health')
      if (health.ok !== true || health.product !== product) throw new Error('Wrong backend identity')
      return
    } catch (error) {
      if (!isBackendAlive()) throw error
      await new Promise(r => setTimeout(r, 250))
    }
  }
  throw new Error('Authenticated backend health check timed out')
}
function terminateIdleBackend() {
  expectedBackendExit = true
  backendLifetime.abort(new Error('Backend has shut down'))
  if (child && child.exitCode === null) child.kill()
}
function waitForPlaybackReady(timeoutMs = playbackReadyWaitMs) {
  if (playbackReady || rendererGone || !window || window.isDestroyed()) return Promise.resolve()
  return new Promise(resolve => {
    const contents = window.webContents
    const cleanup = () => { clearTimeout(timer); ipcMain.removeListener('playback:ready', done); contents.removeListener('render-process-gone', finish); contents.removeListener('destroyed', finish) }
    const finish = () => { cleanup(); resolve() }
    const done = event => { if (validSender(event, window, origin)) finish() }
    const timer = setTimeout(finish, timeoutMs)
    ipcMain.on('playback:ready', done)
    contents.once('render-process-gone', finish)
    contents.once('destroyed', finish)
  })
}
async function stopPlayback() {
  if (!window || window.isDestroyed() || rendererGone) return
  // runSmoke follows loadURL, before usePlayer's effect subscribes. Wait until
  // that subscription signals, instead of sending a stop the renderer drops.
  await waitForPlaybackReady()
  if (!window || window.isDestroyed() || rendererGone) return
  if (!playbackReady) throw new Error('Renderer did not register playback stop')
  // Execute only a constant script in the known local main frame. No user data is interpolated.
  try { await window.webContents.executeJavaScript("window.dispatchEvent(new Event('walkman:stop-playback')); for (const audio of document.querySelectorAll('audio')) { audio.pause(); audio.removeAttribute('src'); audio.load(); }") }
  catch (error) { if (!rendererGone && !window.isDestroyed()) throw error }
  if (window.isDestroyed() || rendererGone) return
  const nonce = crypto.randomBytes(16).toString('hex')
  await new Promise((resolve, reject) => {
    const contents = window.webContents
    const cleanup = () => { clearTimeout(timer); ipcMain.removeListener('playback:stopped', done); ipcMain.removeListener('playback:stop-failed', failed); contents.removeListener('render-process-gone', gone); contents.removeListener('destroyed', gone) }
    const gone = () => { cleanup(); resolve() }
    const done = (event, value) => { if (validSender(event, window, origin) && value === nonce) { cleanup(); resolve() } }
    const failed = (event, value) => { if (validSender(event, window, origin) && value === nonce) { cleanup(); reject(new Error('Playback state could not be saved')) } }
    const timer = setTimeout(() => { cleanup(); reject(new Error('Renderer did not stop playback')) }, 10000)
    ipcMain.on('playback:stopped', done)
    ipcMain.on('playback:stop-failed', failed)
    contents.once('render-process-gone', gone)
    contents.once('destroyed', gone)
    window.webContents.send('playback:stop', nonce)
  })
}
async function exitUnresponsiveRenderer(error) {
  // Confirmed renderer loss. Destroy the window before the lease is released so
  // playback is already gone, then drain admitted device work. The backend is
  // terminated only after that busy-wait, so a Walkman write is not killed.
  log(`Close forced: ${error.message}. Exiting because the renderer did not acknowledge playback stop.`)
  try { broker?.stop() } catch (stopError) { log(`Broker stop during forced close: ${stopError.message}`) }
  if (window && !window.isDestroyed()) window.destroy()
  rendererGone = true
  if (backendLost) return
  try {
    await drainUntilIdle({ stopPlayback: async () => {
      await request('POST', '/api/internal/playback/release')
    }, request, onWaiting: () => {} })
  } catch (drainError) {
    if (backendLost) return
    log(`Close deferred: ${drainError.message}`)
    closing = false
    const warning = { type: 'warning', buttons: ['Retry shutdown'],
      message: 'The app cannot confirm that all work has finished.',
      detail: 'The player has stopped, but background work may still be active. Retry shutdown to wait for that work before exiting.\n' + drainError.message }
    const result = await dialog.showMessageBox(warning)
    if (result.response === 0) void closeSafely()
    return
  }
  try { await revokeSession() } catch (revokeError) { log(`Session revoke during forced close: ${revokeError.message}`) }
  if (backendLost) return
  allowClose = true
  terminateIdleBackend()
  app.quit()
}
async function closeSafely() {
  if (closing || allowClose) return
  closing = true
  try {
    await drainUntilIdle({ stopPlayback: async () => {
      await stopPlayback()
      // Only ACK or confirmed renderer loss can release the native playback lease.
      await request('POST', '/api/internal/playback/release')
    }, request,
      onWaiting: state => { if (window && !window.isDestroyed()) window.webContents.send('app:draining', state) } })
    broker?.stop()
    await revokeSession()
    if (backendLost) return
    allowClose = true
    terminateIdleBackend()
    window?.destroy()
    app.quit()
  } catch (error) {
    if (backendLost) return
    if (smoke) {
      log(`Close deferred: ${error.message}`)
      try { broker?.stop() } catch (stopError) { log(`Broker stop during forced close: ${stopError.message}`) }
      allowClose = true
      terminateIdleBackend()
      if (window && !window.isDestroyed()) window.destroy()
      app.exit(1)
      return
    }
    if (error.message === 'Renderer did not stop playback' || error.message === 'Renderer did not register playback stop') {
      await exitUnresponsiveRenderer(error)
      return
    }
    log(`Close deferred: ${error.message}`)
    closing = false
    const warning = { type: 'warning', buttons: [rendererGone ? 'Retry shutdown' : 'Keep open'],
      message: 'The app cannot confirm that all work has finished.',
      detail: (rendererGone ? 'The player has stopped, but background work may still be active. Retry shutdown to wait for that work before exiting.\n' : 'The window will remain open. Check the operation status before closing again.\n') + error.message }
    const result = await (rendererGone ? dialog.showMessageBox(warning) : dialog.showMessageBox(window, warning))
    if (rendererGone && result.response === 0) void closeSafely()
  }
}
function wireIpc() {
  ipcMain.on('playback:ready', event => { if (validSender(event, window, origin)) playbackReady = true })
  for (const action of ['minimize', 'maximize', 'close']) {
    ipcMain.handle(`win:${action}`, event => {
      if (!isBackendAlive() || !validSender(event, window, origin)) throw new Error('Untrusted IPC sender')
      if (action === 'minimize') window.minimize()
      if (action === 'maximize') window.isMaximized() ? window.unmaximize() : window.maximize()
      if (action === 'close') window.close()
    })
  }
}
async function createWindow() {
  requireBackend()
  const isolated = isolatedSession = session.fromPartition(`persist:nightops-${product}`)
  isolated.webRequest.onBeforeRequest((details, callback) => callback({ cancel: !isBackendAlive() || !ownOrigin(details.url, origin) }))
  cookieWrite = isolated.cookies.set({ url: origin, name: 'nightops_session', value: token,
    httpOnly: true, sameSite: 'strict', secure: false, path: '/' })
  await cookieWrite
  requireBackend()
  isolated.setPermissionRequestHandler((_wc, _permission, callback) => callback(false))
  isolated.setPermissionCheckHandler(() => false)
  // Electron reports this area in display-independent pixels, including Windows
  // scaling and excluding the taskbar. Keep first launch fully on that display.
  const area = screen.getDisplayNearestPoint(screen.getCursorScreenPoint()).workArea
  const minWidth = Math.min(640, area.width), minHeight = Math.min(560, area.height)
  const width = Math.min(product === 'player' ? 1140 : 1280, Math.max(minWidth, area.width - 48))
  const height = Math.min(860, Math.max(minHeight, area.height - 48))
  window = new BrowserWindow({ width, height,
    x: Math.round(area.x + (area.width - width) / 2), y: Math.round(area.y + (area.height - height) / 2),
    minWidth, minHeight, resizable: true, frame: false, show: false,
    backgroundColor: '#160c0e', autoHideMenuBar: true,
    webPreferences: { preload: path.join(__dirname, 'preload.cjs'), session: isolated,
      // Smoke never shows the window. Throttling that hidden page delays the
      // playback-stop subscription, so the close ack never arrives. Shown
      // windows keep Chromium's default throttling.
      backgroundThrottling: !smoke,
      additionalArguments: [`--nightops-product=${product}`], contextIsolation: true, sandbox: true, nodeIntegration: false,
      webSecurity: true, allowRunningInsecureContent: false, webviewTag: false } })
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
  window.webContents.on('will-navigate', (event, url) => { if (!isBackendAlive() || !ownOrigin(url, origin)) event.preventDefault() })
  window.webContents.on('will-attach-webview', event => event.preventDefault())
  window.webContents.on('did-start-navigation', (_event, _url, isInPlace, isMainFrame) => {
    // A reload replaces the JS context. In-place and subframe navigations do not.
    if (isMainFrame && !isInPlace) playbackReady = false
  })
  window.webContents.on('render-process-gone', (_event, details) => {
    rendererGone = true
    log(`Renderer exited: ${details.reason}`)
    if (!window.isDestroyed()) window.destroy()
    if (isBackendAlive()) void closeSafely()
  })
  window.on('close', event => { if (!allowClose) { event.preventDefault(); void closeSafely() } })
  await window.loadURL(origin)
  requireBackend()
  if (rendererGone || window.isDestroyed()) throw new Error('Renderer exited during startup')
  if (!smoke) window.show()
}
async function runSmoke() {
  await waitForPlaybackReady(smokeReadyWaitMs)
  if (!playbackReady) {
    const reason = 'Smoke failed: renderer did not register playback stop'
    log(reason)
    console.error(reason)
    try { broker?.stop() } catch (stopError) { log(`Broker stop during forced close: ${stopError.message}`) }
    if (backendLost) return
    allowClose = true
    terminateIdleBackend()
    if (window && !window.isDestroyed()) window.destroy()
    app.exit(1)
    return
  }
  const result = await window.webContents.executeJavaScript(`({ product: window.walkmanBridge.product,
    hasClose: typeof window.walkmanBridge.close === 'function', origin: location.origin,
    cookieHidden: !document.cookie.includes('nightops_session'), title: document.title })`)
  if (result.product !== product || !result.hasClose || !result.cookieHidden || result.origin !== origin) throw new Error('Renderer boundary smoke failed')
  const denied = await window.webContents.executeJavaScript("fetch('/api/internal/scanner/pending').then(r => r.status)")
  if (denied !== 403) throw new Error('Renderer accessed native scanner route')
  const health = await request('GET', '/api/health')
  log(`SMOKE OK ${JSON.stringify({ ...result, health })}`)
  console.log(`SMOKE OK ${product}`)
  await closeSafely()
}

if (!app.requestSingleInstanceLock()) app.quit()
else {
  app.on('second-instance', () => { if (window && !window.isDestroyed()) { if (window.isMinimized()) window.restore(); window.focus() } })
  app.on('before-quit', event => { if (!allowClose && window) { event.preventDefault(); void closeSafely() } })
  // Unexpected loss must finish capability revocation and show its recovery dialog.
  app.on('window-all-closed', () => { if (allowClose && !backendLost) app.quit() })
  app.whenReady().then(async () => {
    try {
      origin = await startBackend()
      requireBackend()
      await healthy()
      const helper = runtime('scanner-helper/RedLotus.ScanHelper.exe',
        path.join(root, 'scanner-helper', 'artifacts', 'win-x64', 'RedLotus.ScanHelper.exe'))
      broker = createScannerBroker({ baseUrl: origin, token, helperPath: helper, cacheRoot: path.join(dataDir, 'cache'), log, isBackendAlive })
      await broker.start()
      requireBackend()
      wireIpc()
      await createWindow()
      if (smoke) await runSmoke()
    } catch (error) {
      if (backendLost) return
      log(`Startup failed: ${error.stack || error}`)
      console.error(error.message)
      if (!smoke) dialog.showErrorBox(name, error.message)
      // No renderer or admitted work exists if startup fails before window creation.
      if (!window) { broker?.stop(); terminateIdleBackend(); app.exit(1) }
      else await closeSafely()
    }
  })
}
