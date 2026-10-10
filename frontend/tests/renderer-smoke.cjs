// Isolated real-Electron renderer test. The HTTP fixture is deliberately owned
// by this test; it never invokes the production scanner or any device operation.
const { app, BrowserWindow } = require('electron')
const http = require('node:http')
const fs = require('node:fs')
const path = require('node:path')
const assert = require('node:assert/strict')

app.commandLine.appendSwitch('autoplay-policy', 'no-user-gesture-required')
app.on('window-all-closed', () => {}) // The test creates each product window in sequence.
const label = process.argv.find(value => value.startsWith('--label='))?.slice(8)
if (label && !/^[a-z0-9-]+$/.test(label)) throw new Error('Invalid output label')
const output = path.resolve(__dirname, '../test-output', label ? `renderer-${label}` : '.')
fs.mkdirSync(output, { recursive: true })
const progress = value => fs.appendFileSync(path.join(output, 'renderer-progress.log'), new Date().toISOString() + ' ' + value + '\n')
setTimeout(() => { progress('TIMEOUT'); app.exit(1) }, 60000)
app.setPath('userData', path.join(output, 'electron-profile'))
const wave = Buffer.alloc(44 + 48000 * 2 * 2)
wave.write('RIFF'); wave.writeUInt32LE(wave.length - 8, 4); wave.write('WAVEfmt ', 8); wave.writeUInt32LE(16, 16); wave.writeUInt16LE(1, 20); wave.writeUInt16LE(1, 22); wave.writeUInt32LE(48000, 24); wave.writeUInt32LE(96000, 28); wave.writeUInt16LE(2, 32); wave.writeUInt16LE(16, 34); wave.write('data', 36); wave.writeUInt32LE(wave.length - 44, 40)
for (let i = 0; i < (wave.length - 44) / 2; i++) wave.writeInt16LE(Math.round(Math.sin(i / 48000 * 2 * Math.PI * 440) * 4000), 44 + i * 2)
const media = (id, title) => ({ id, name: `${title}.wav`, title, artist: 'Red Lotus test signal', album: 'Local renderer verification', status: 'ready', mime: 'audio/wav', duration_seconds: 2, size_bytes: wave.length, scan: { ok: true, state: 'clean' } })
let items = [media('one', 'Ember Signal'), media('two', 'Night Drive'), { ...media('blocked', 'Blocked fixture'), status: 'blocked', scan: { ok: false, reason: 'Test fixture: clearance unavailable' } }]
let saved = { media_id: 'one', position_seconds: 0.35, volume: 0.38, shuffle: false, repeat: 'off' }
let product = 'player'
let leasedId = null
const calls = [], errors = []
const instrumentation = `window.__audio=[];window.__graphs=[];const OriginalAudio=window.Audio;window.Audio=class extends OriginalAudio{constructor(...args){super(...args);window.__audio.push(this)}};const OriginalContext=window.AudioContext;window.AudioContext=class extends OriginalContext{constructor(...args){super(...args);window.__graphs.push(this)}};window.addEventListener('error',e=>console.error(e.message));`
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://127.0.0.1')
  const json = data => { res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(data)) }
  if (url.pathname.startsWith('/api')) {
    calls.push(`${req.method} ${url.pathname}`)
    if (url.pathname === '/api/health') return json({ ok: true, version: '0.4.1', product })
    if (url.pathname === '/api/playback-state') {
      if (req.method === 'PATCH') { let data = ''; for await (const chunk of req) data += chunk; saved = JSON.parse(data) }
      return json(saved)
    }
    if (url.pathname === '/api/playback/lease') {
      let data = ''; for await (const chunk of req) data += chunk
      const id = JSON.parse(data).media_id
      if (req.method === 'POST') leasedId = id
      else if (req.method === 'DELETE' && leasedId === id) leasedId = null
      return json({ ok: true })
    }
    if (url.pathname === '/api/queue') return json({ items, quota: { used_bytes: wave.length * 2, limit_bytes: 10 * 1024 ** 3 } })
    if (url.pathname === '/api/engine-busy') return json({ busy: !!leasedId, draining: false, active: leasedId ? [{ kind: 'playback_session' }] : [] })
    if (url.pathname === '/api/jobs/latest') return json(null)
    if (url.pathname === '/api/device') return json({ connected: false })
    if (/\/api\/media\/[^/]+\/stream$/.test(url.pathname)) {
      if (url.pathname.split('/')[3] !== leasedId) { res.statusCode = 409; return json({ detail: 'Test stream requires its playback lease' }) }
      res.setHeader('Content-Type', 'audio/wav'); res.setHeader('Accept-Ranges', 'bytes')
      const range = /^bytes=(\d+)-(\d*)$/.exec(req.headers.range || '')
      const start = range ? Number(range[1]) : 0, end = range && range[2] ? Math.min(Number(range[2]), wave.length - 1) : wave.length - 1
      if (range) { res.statusCode = 206; res.setHeader('Content-Range', `bytes ${start}-${end}/${wave.length}`) }
      res.setHeader('Content-Length', end - start + 1); return res.end(wave.subarray(start, end + 1))
    }
    if (url.pathname === '/api/queue/order') { let data = ''; for await (const chunk of req) data += chunk; items = JSON.parse(data).ids.map(id => items.find(item => item.id === id)); return json({ ok: true }) }
    res.statusCode = 404; return json({ detail: 'Unexpected test request' })
  }
  const root = path.resolve(__dirname, '../dist')
  const file = path.resolve(root, `.${url.pathname === '/' ? '/index.html' : url.pathname}`)
  if (!file.startsWith(`${root}${path.sep}`) || !fs.existsSync(file)) { res.statusCode = 404; return res.end() }
  const extension = path.extname(file)
  res.setHeader('Content-Type', { '.js': 'application/javascript', '.css': 'text/css', '.html': 'text/html', '.woff2': 'font/woff2', '.png': 'image/png' }[extension] || 'application/octet-stream')
  if (extension === '.html') {
    res.setHeader('Content-Security-Policy', "default-src 'self'; script-src 'self' 'nonce-renderer-fixture'; style-src 'self' 'unsafe-inline'; img-src 'self' data:")
    return res.end(fs.readFileSync(file, 'utf8').replace('<head>', '<head><script nonce="renderer-fixture">' + instrumentation + '</script>'))
  }
  fs.createReadStream(file).pipe(res)
})
const delay = ms => new Promise(resolve => setTimeout(resolve, ms))
async function until(win, expression, timeout = 6000) {
  const deadline = Date.now() + timeout
  while (Date.now() < deadline) { if (await win.webContents.executeJavaScript(expression)) return; await delay(60) }
  throw new Error(`Renderer condition timed out: ${expression}`)
}
async function click(win, label) {
  await win.webContents.executeJavaScript(`(() => { const target=document.querySelector(${JSON.stringify(`[aria-label="${label}"]`)});const menu=target.closest('details');if(menu&&!menu.open)menu.querySelector('summary').click();target.scrollIntoView({block:'center'});target.click() })()`, true)
}
async function windowFor(mode) {
  progress('windowFor ' + mode)
  product = mode
  const win = new BrowserWindow({ width: 1440, height: 980, show: false, webPreferences: { partition: `renderer-${mode}-${Date.now()}`, backgroundThrottling: false, contextIsolation: true, sandbox: true, nodeIntegration: false } })
  progress('created ' + mode)
  win.webContents.setAudioMuted(true)
  win.webContents.on('console-message', details => { progress('console ' + details.level + ': ' + details.message); if (details.level === 'error') errors.push(details.message) })
  await win.loadURL(`http://127.0.0.1:${server.address().port}/`)
  progress('loaded ' + mode)
  progress(await win.webContents.executeJavaScript(`JSON.stringify({audio:typeof window.__audio,body:document.body.innerText.slice(0,200)})`))
  await until(win, `document.querySelector('.nightops-app') && window.__audio?.some(a=>a.readyState>=1)`)
  assert.equal(await win.webContents.executeJavaScript('document.title'), mode === 'player' ? 'Red Lotus Player' : 'Walkman Bridge')
  assert.equal(await win.webContents.executeJavaScript(`/night\\s+ops/i.test(document.body.innerText)`), false, 'Legacy branding must not appear in the rendered UI')
  progress('hydrated ' + mode)
  return win
}

async function main() {
  await app.whenReady()
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  const player = await windowFor('player')
  await delay(3200) // Refresh must not disable queue actions because paused audio holds a lease.
  assert.equal(await player.webContents.executeJavaScript(`document.querySelector('.queue-tools button').disabled`), false)
  assert.equal(await player.webContents.executeJavaScript(`!!document.querySelector('.device-column')`), false)
  assert.equal(await player.webContents.executeJavaScript(`window.__audio.at(-1).paused`), true)
  progress(await player.webContents.executeJavaScript(`JSON.stringify({position:window.__audio.at(-1).currentTime,duration:window.__audio.at(-1).duration,pending:window.__audio.at(-1).dataset.pendingPosition,seekable:window.__audio.at(-1).seekable.length})`))
  await until(player, `!window.__audio.at(-1).seeking`)
  assert.ok(Math.abs(await player.webContents.executeJavaScript(`window.__audio.at(-1).currentTime`) - 0.35) < 0.08)
  assert.equal(await player.webContents.executeJavaScript(`!!document.querySelector('.equalizer')`), false)
  await player.webContents.executeJavaScript(`Array.from(document.querySelectorAll('.player-wing-controls button')).find(button=>button.textContent==='Equalizer').click()`)
  await until(player, `!!document.querySelector('.equalizer')`)
  assert.equal(await player.webContents.executeJavaScript(`document.querySelector('[aria-label="Equalizer preset"]').value`), 'Flat')
  await click(player, 'Play')
  await until(player, `!window.__audio.at(-1).paused && window.__audio.at(-1).currentTime > 0.5`)
  await click(player, 'Pause')
  await until(player, `window.__audio.at(-1).paused`)
  const oldVolume = await player.webContents.executeJavaScript(`window.__audio.at(-1).volume`)
  await player.webContents.executeJavaScript(`document.querySelector('.volume-dial').dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowUp',bubbles:true}))`)
  assert.ok(await player.webContents.executeJavaScript(`window.__audio.at(-1).volume`) > oldVolume)
  await player.webContents.executeJavaScript(`const range=document.querySelector('[aria-label="Playback position"]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(range,'1.4');range.dispatchEvent(new Event('input',{bubbles:true}));range.dispatchEvent(new Event('change',{bubbles:true}));`)
  await until(player, `window.__audio.at(-1).currentTime > 1.3`)
  await click(player, 'Play')
  await until(player, `document.querySelector('.track-copy h1').textContent==='Night Drive' && !window.__audio.at(-1).paused && !!document.querySelector('[aria-label="Pause"]')`)
  await click(player, 'Pause')
  await click(player, 'Shuffle')
  await click(player, 'Repeat: off')
  assert.equal(await player.webContents.executeJavaScript(`document.querySelector('[aria-label="Shuffle"]').getAttribute('aria-pressed')`), 'true')
  await player.webContents.executeJavaScript(`document.querySelector('.eq-toolbar input[type=checkbox]').click()`)
  assert.equal(await player.webContents.executeJavaScript(`document.querySelector('.equalizer .micro').textContent`), 'ACTIVE')
  await player.webContents.executeJavaScript(`document.querySelector('.eq-toolbar input[type=checkbox]').click()`)
  await click(player, 'Move Night Drive up')
  await until(player, `document.querySelector('.queue-row .track-select strong').textContent==='Night Drive'`)
  fs.writeFileSync(path.join(output, 'player-desktop.png'), (await player.webContents.capturePage()).toPNG())
  player.setSize(640, 900)
  await delay(100)
  assert.equal(await player.webContents.executeJavaScript(`document.documentElement.scrollWidth > window.innerWidth + 2`), false)
  fs.writeFileSync(path.join(output, 'player-compact.png'), (await player.webContents.capturePage()).toPNG())
  await player.webContents.executeJavaScript(`window.dispatchEvent(new Event('walkman:stop-playback'))`)
  await until(player, `!window.__audio.at(-1).getAttribute('src')`)
  await delay(150)
  assert.equal(leasedId, null)
  assert.equal(saved.media_id, 'two')
  assert.equal(saved.shuffle, true)
  assert.equal(saved.repeat, 'all')
  assert.equal(calls.some(call => call.includes('/prepare')), false)
  player.destroy()
  const bridge = await windowFor('bridge')
  assert.equal(await bridge.webContents.executeJavaScript(`!!document.querySelector('.listening-column .retro-deck .volume-dial') && !!document.querySelector('.listening-column .stop-button')`), true, 'Bridge Listening reuses the retro transport and rotary volume')
  const bridgeVolume = await bridge.webContents.executeJavaScript(`window.__audio.at(-1).volume`)
  await bridge.webContents.executeJavaScript(`document.querySelector('.volume-dial').dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowUp',bubbles:true}))`)
  assert.ok(await bridge.webContents.executeJavaScript(`window.__audio.at(-1).volume`) > bridgeVolume)
  assert.equal(await bridge.webContents.executeJavaScript(`!!document.querySelector('.workspace-tabs') && !!document.querySelector('.persistent-transport') && !document.querySelector('.device-column')`), true)
  assert.equal(await bridge.webContents.executeJavaScript(`window.__audio.at(-1).paused`), true)
  await bridge.webContents.executeJavaScript(`document.querySelector('.bridge-eq-toggle button').click()`)
  await until(bridge, `!!document.querySelector('.equalizer')`)
  assert.equal(await bridge.webContents.executeJavaScript(`document.querySelector('.eq-toolbar input[type=checkbox]').checked`), false)
  await bridge.webContents.executeJavaScript(`Array.from(document.querySelectorAll('.workspace-tabs button')).find(button=>button.textContent==='Walkman').click()`)
  await until(bridge, `!!document.querySelector('.device-column') && !!document.querySelector('.persistent-transport')`)
  await bridge.webContents.executeJavaScript(`Array.from(document.querySelectorAll('.workspace-tabs button')).find(button=>button.textContent==='Listening').click()`)
  await until(bridge, `!!document.querySelector('.listening-column')`)
  fs.writeFileSync(path.join(output, 'bridge-desktop.png'), (await bridge.webContents.capturePage()).toPNG())
  bridge.setSize(640, 900)
  await delay(100)
  assert.equal(await bridge.webContents.executeJavaScript(`document.documentElement.scrollWidth > window.innerWidth + 2`), false)
  fs.writeFileSync(path.join(output, 'bridge-compact.png'), (await bridge.webContents.capturePage()).toPNG())
  await bridge.webContents.executeJavaScript(`window.dispatchEvent(new Event('walkman:stop-playback'))`)
  await delay(150)
  assert.equal(leasedId, null)
  bridge.destroy()
  assert.deepEqual(errors, [])
  fs.writeFileSync(path.join(output, 'renderer-smoke.json'), JSON.stringify({ ok: true, checks: ['product titles and no legacy branding', 'Bridge retro transport and functional rotary volume', 'two actual compositions', 'paused server session restore', 'real WAV decode and playback', 'seek', 'automatic queue advance', 'shuffle and repeat', 'live Web Audio DSP toggle and bypass', 'persistent reorder', 'source release on stop', 'server session save', 'standalone device controls absent', 'desktop and compact width'], calls, errors }, null, 2))
  fs.rmSync(path.join(output, 'renderer-smoke-failure.txt'), { force: true })
  console.log('Renderer smoke PASS: real audio, paused restoration, seek, queue advance/order, DSP bypass, both responsive compositions.')
}
main().then(() => { server.close(); app.exit(0) }).catch(error => { fs.writeFileSync(path.join(output, 'renderer-smoke-failure.txt'), error.stack || String(error)); console.error(error); server.close(); app.exit(1) })
