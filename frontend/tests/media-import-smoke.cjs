// Real Electron renderer, isolated authenticated HTTP fixtures, no downloader,
// scanner bypass in production, or physical device access.
const { app, BrowserWindow, session } = require('electron')
const http = require('node:http')
const fs = require('node:fs')
const path = require('node:path')
const assert = require('node:assert/strict')
const output = path.resolve(__dirname, '../test-output/media-import')
fs.mkdirSync(output, { recursive: true })
for (const filename of ['result.json', 'failure.txt', 'timeout.txt', 'console.log']) {
  const file = path.join(output, filename)
  if (fs.existsSync(file)) fs.unlinkSync(file)
}
app.setPath('userData', path.join(output, 'electron-profile'))
app.on('window-all-closed', () => {})
setTimeout(() => { fs.writeFileSync(path.join(output, 'timeout.txt'), 'Renderer timeout'); app.exit(1) }, 60000)
const cover = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aWQAAAABJRU5ErkJggg==', 'base64')
const title = '<img src=x onerror=alert(1)> Ember & Night'
const item = id => ({ id, name: `${id}.wav`, title, artist: '<script>unsafe()</script>', album: 'Album <b>one</b>', genre: 'Ambient', year: '2026', track: '2/10', status: 'ready', mime: 'audio/wav', duration_seconds: 2, scan: { ok: true }, artwork_url: `/api/media/${id}/artwork` })
const wave = Buffer.alloc(44 + 48000 * 2)
wave.write('RIFF'); wave.writeUInt32LE(wave.length - 8, 4); wave.write('WAVEfmt ', 8); wave.writeUInt32LE(16, 16); wave.writeUInt16LE(1, 20); wave.writeUInt16LE(1, 22); wave.writeUInt32LE(48000, 24); wave.writeUInt32LE(96000, 28); wave.writeUInt16LE(2, 32); wave.writeUInt16LE(16, 34); wave.write('data', 36); wave.writeUInt32LE(wave.length - 44, 40)
let product = 'player', items = [item('cover')], activeJob = null, acceptLink = false, pendingAdmission = null, invalidCover = false
const calls = [], submissions = [], checks = []
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://127.0.0.1')
  const json = (value, status = 200) => { res.statusCode = status; res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(value)) }
  if (url.pathname.startsWith('/api')) {
    calls.push({ method: req.method, path: url.pathname, authenticated: req.headers.cookie === 'fixture=private' })
    if (req.headers.cookie !== 'fixture=private') return json({ detail: 'Fixture authentication required' }, 401)
    if (url.pathname === '/api/health') return json({ ok: true, product, version: '0.2.0' })
    if (url.pathname === '/api/playback-state') return json({ media_id: 'cover', position_seconds: 0, volume: .3, shuffle: false, repeat: 'off' })
    if (url.pathname === '/api/playback/lease') return json({ ok: true })
    if (url.pathname === '/api/queue') return json({ items })
    if (url.pathname === '/api/engine-busy') return json({ busy: activeJob?.status === 'running', active: activeJob?.status === 'running' ? [{ kind: 'link_import' }] : [] })
    if (url.pathname === '/api/device') return json({ connected: false })
    if (url.pathname === '/api/jobs/latest') return json(activeJob)
    if (url.pathname === '/api/jobs/link-job') return json(activeJob)
    if (url.pathname === '/api/media/import-link') {
      let body = ''; for await (const chunk of req) body += chunk
      submissions.push(JSON.parse(body))
      if (!acceptLink) return json({ detail: { code: 'provider_unavailable', message: 'This track is unavailable. Try another public track.' } }, 422)
      pendingAdmission = () => {
        activeJob = { job_id: 'link-job', kind: 'link_import', status: 'running', phase: 'downloading', progress: null, message: 'Downloading the selected track.', files: [{ file_id: 'download-one', name: 'Selected track', state: 'downloading' }] }
        json({ job_id: 'link-job' }, 202)
      }
      return
    }
    if (url.pathname === '/api/media/cover/artwork') { res.setHeader('Content-Type', 'image/png'); return res.end(invalidCover ? Buffer.from('invalid image fixture') : cover) }
    if (/\/stream$/.test(url.pathname)) { res.setHeader('Content-Type', 'audio/wav'); res.setHeader('Content-Length', wave.length); return res.end(wave) }
    return json({ detail: 'Unexpected fixture request' }, 404)
  }
  const root = path.resolve(__dirname, '../dist'), file = path.resolve(root, `.${url.pathname === '/' ? '/index.html' : url.pathname}`)
  if (!file.startsWith(root + path.sep) || !fs.existsSync(file)) { res.statusCode = 404; return res.end() }
  res.setHeader('Content-Type', { '.js': 'application/javascript', '.css': 'text/css', '.html': 'text/html', '.png': 'image/png', '.woff2': 'font/woff2' }[path.extname(file)] || 'application/octet-stream')
  res.setHeader('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self'")
  fs.createReadStream(file).pipe(res)
})
const delay = ms => new Promise(resolve => setTimeout(resolve, ms))
async function until(win, expression, timeout = 6000) {
  const end = Date.now() + timeout
  while (Date.now() < end) { if (await win.webContents.executeJavaScript(expression)) return; await delay(60) }
  throw new Error(`Renderer condition timed out: ${expression}`)
}
const run = (win, source) => win.webContents.executeJavaScript(source, true)
const open = win => run(win, `window.__opener=Array.from(document.querySelectorAll('.queue-tools button')).find(b=>b.textContent==='Import link');window.__opener.focus();window.__opener.click()`)
const enter = (win, value) => run(win, `{const input=document.querySelector('#music-link');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,${JSON.stringify(value)});input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}));}`)
const submit = win => run(win, `document.querySelector('.link-import-dialog form').requestSubmit()`)
async function escape(win) { win.webContents.sendInputEvent({ type: 'keyDown', keyCode: 'Escape' }); win.webContents.sendInputEvent({ type: 'keyUp', keyCode: 'Escape' }); await until(win, `!document.querySelector('.link-import-dialog')`) }
async function verify(mode) {
  product = mode; activeJob = null; acceptLink = false; invalidCover = false; pendingAdmission = null; items = [item('cover')]
  const origin = `http://127.0.0.1:${server.address().port}`
  const partition = session.fromPartition(`media-import-${mode}`)
  await partition.cookies.set({ url: origin, name: 'fixture', value: 'private', httpOnly: true, sameSite: 'strict', path: '/api' })
  const win = new BrowserWindow({ show: false, width: 1440, height: 980, webPreferences: { session: partition, sandbox: true, contextIsolation: true, nodeIntegration: false, backgroundThrottling: false } })
  win.webContents.on('console-message', details => fs.appendFileSync(path.join(output, 'console.log'), `${mode} ${details.level}: ${details.message}\n`))
  await win.loadURL(origin)
  await until(win, `document.querySelector('.record-art.has-cover img')?.naturalWidth===1`)
  assert.equal(await run(win, `document.querySelector('.track-copy h1').textContent`), title)
  assert.equal(await run(win, `document.querySelectorAll('.track-copy img,.track-copy script,.track-copy b').length`), 0)
  assert.equal(await run(win, `document.querySelector('.track-details').textContent`), 'Ambient · 2026 · Track 2/10')
  checks.push(`${mode}: authenticated local artwork and escaped metadata`)

  await open(win); await until(win, `document.activeElement.id==='music-link'`)
  await escape(win); assert.equal(await run(win, `document.activeElement===window.__opener`), true)
  await open(win); await until(win, `document.activeElement.id==='music-link'`)
  win.webContents.sendInputEvent({ type: 'keyDown', keyCode: 'Tab', modifiers: ['shift'] }); win.webContents.sendInputEvent({ type: 'keyUp', keyCode: 'Tab', modifiers: ['shift'] })
  assert.equal(await run(win, `!!document.activeElement.closest('.link-import-dialog')`), true)
  await enter(win, 'https://example.com/unsupported'); await submit(win)
  await until(win, `document.querySelector('#link-import-error')?.textContent.includes('YouTube or SoundCloud')`)
  const url = mode === 'player' ? 'https://www.youtube.com/watch?v=abcdefghijk' : 'https://soundcloud.com/artist/single-track'
  await enter(win, url); await submit(win)
  await until(win, `document.querySelector('#link-import-error')?.textContent==='This track is unavailable. Try another public track.'`)
  assert.equal(await run(win, `document.activeElement.id`), 'music-link')
  checks.push(`${mode}: Escape, focus recovery, dialog tab containment, inline provider error`)

  acceptLink = true
  await submit(win)
  await until(win, `document.querySelector('.link-import-dialog button[type=submit]').disabled`)
  const submissionCount = submissions.length
  await submit(win); await delay(80); assert.equal(submissions.length, submissionCount)
  await escape(win) // Closing the form never cancels an already sent import.
  assert.equal(await run(win, `document.activeElement.tagName`), 'MAIN')
  assert.ok(pendingAdmission); pendingAdmission()
  await until(win, `document.querySelector('.job-title')?.textContent.includes('downloading')`)
  assert.equal(await run(win, `document.querySelector('.job-panel progress').hasAttribute('value')`), false)
  assert.equal(await run(win, `document.querySelector('.queue-tools button').disabled`), true)
  if (mode === 'player') {
    await run(win, `Array.from(document.querySelectorAll('.player-wing-controls button')).find(b=>b.textContent==='Equalizer').click()`)
    await until(win, `!!document.querySelector('.equalizer')`)
    await run(win, `Array.from(document.querySelectorAll('.player-wing-controls button')).find(b=>b.textContent==='Equalizer').click()`)
    await until(win, `!document.querySelector('.equalizer') && !!document.querySelector('.workspace-operation .job-panel')`)
  }
  activeJob = { ...activeJob, phase: 'scanning', progress: .45, message: 'Scanning downloaded audio.', files: [{ file_id: 'download-one', name: 'Selected track', state: 'scanning' }] }
  await until(win, `document.querySelector('.job-title')?.textContent.includes('scanning')`)
  assert.equal(await run(win, `document.querySelector('.job-panel progress').value`), .45)
  activeJob = { ...activeJob, phase: 'analyzing', progress: .7, message: 'Reading cleared audio metadata.', files: [{ file_id: 'download-one', name: 'Selected track', state: 'analyzing' }] }
  await run(win, `document.querySelector('.job-details summary').click()`)
  await until(win, `document.querySelector('.job-details').open`)
  await until(win, `document.querySelector('.job-files')?.textContent.includes('Reading metadata')`)
  items.push({ ...item('imported'), title: 'Imported track', artwork_url: null })
  activeJob = { ...activeJob, phase: 'done', status: 'done', progress: 1, message: 'Track is ready.', files: [{ file_id: 'download-one', name: 'Selected track', state: 'ready' }] }
  await until(win, `document.querySelector('.job-title')?.textContent.includes('done') && document.querySelectorAll('.queue-row').length===2 && !document.querySelector('.queue-tools button').disabled`)
  assert.equal(await run(win, `document.querySelector('.job-details').open`), false)
  checks.push(`${mode}: one admitted request, progress survives form closure and collapsed panels, ready item appears in queue`)
  await delay(120) // Let the compositor paint the confirmed terminal state.
  fs.writeFileSync(path.join(output, `${mode}-desktop.png`), (await win.webContents.capturePage()).toPNG())
  win.setSize(640, 900); await delay(100)
  assert.equal(await run(win, `document.documentElement.scrollWidth>window.innerWidth+2`), false)
  await open(win); await until(win, `!!document.querySelector('.link-import-dialog[open]')`)
  fs.writeFileSync(path.join(output, `${mode}-link-dialog.png`), (await win.webContents.capturePage()).toPNG())
  await escape(win)
  await run(win, `window.dispatchEvent(new Event('walkman:stop-playback'))`)
  win.destroy()

  invalidCover = true
  const restored = new BrowserWindow({ show: false, width: 1440, height: 980, webPreferences: { session: partition, sandbox: true, contextIsolation: true, nodeIntegration: false, backgroundThrottling: false } })
  await restored.loadURL(origin)
  await until(restored, `document.querySelector('.job-title')?.textContent.includes('done') && document.querySelector('.record-art:not(.has-cover) img')?.naturalWidth>0`)
  checks.push(`${mode}: restored durable job and invalid artwork falls back to bundled lotus`)
  await run(restored, `window.dispatchEvent(new Event('walkman:stop-playback'))`)
  restored.destroy()
}
async function main() {
  await app.whenReady(); await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  await verify('player'); await verify('bridge')
  assert.ok(calls.filter(call => call.path.endsWith('/artwork')).length >= 4)
  assert.ok(calls.every(call => call.authenticated))
  assert.deepEqual(submissions.map(row => Object.keys(row)), Array(submissions.length).fill(['url']))
  fs.writeFileSync(path.join(output, 'result.json'), JSON.stringify({ ok: true, checks, submissions, calls }, null, 2))
  console.log(`Media import renderer PASS: ${checks.length} checks in both actual Electron compositions.`)
}
main().then(() => { server.close(); app.exit(0) }).catch(error => { fs.writeFileSync(path.join(output, 'failure.txt'), error.stack || String(error)); console.error(error); server.close(); app.exit(1) })
