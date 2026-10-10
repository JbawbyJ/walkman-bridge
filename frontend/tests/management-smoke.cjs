// Actual Electron management workflows; HTTP/audio fixtures only, no Sony writes.
const { app, BrowserWindow, session } = require('electron')
const http = require('node:http'), fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict')
const label = process.argv.find(value => value.startsWith('--label='))?.slice(8)
if (label && !/^[a-z0-9-]+$/.test(label)) throw new Error('Invalid output label')
const output = path.resolve(__dirname, '../test-output', label ? `management-${label}` : 'management')
fs.mkdirSync(output, { recursive: true }); app.setPath('userData', path.join(output, 'profile'))
app.on('window-all-closed', () => {})
const checks = [], calls = []; let product, items, lists, sony, saved, revision
const wave = Buffer.alloc(44 + 44100 * 2)
wave.write('RIFF'); wave.writeUInt32LE(wave.length - 8, 4); wave.write('WAVEfmt ', 8); wave.writeUInt32LE(16, 16); wave.writeUInt16LE(1, 20); wave.writeUInt16LE(1, 22); wave.writeUInt32LE(44100, 24); wave.writeUInt32LE(88200, 28); wave.writeUInt16LE(2, 32); wave.writeUInt16LE(16, 34); wave.write('data', 36); wave.writeUInt32LE(wave.length - 44, 40)
const fixture = id => ({ id, name: `${id}.wav`, title: id === 'a' ? 'Ember' : 'Lotus', artist: 'Test Artist', album: 'Test Album', status: 'ready', scan: { ok: true }, duration_seconds: 1, mime: 'audio/wav', size_bytes: wave.length })
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://127.0.0.1'), p = url.pathname, method = req.method
  const json = (data, status = 200) => { res.statusCode = status; res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(data)) }
  if (p.startsWith('/api/')) {
    calls.push({ p, method, auth: req.headers.cookie === 'fixture=management' })
    if (req.headers.cookie !== 'fixture=management') return json({ detail: 'unauthorized' }, 403)
    let text = ''; for await (const chunk of req) text += chunk
    const body = text ? JSON.parse(text) : {}
    if (p === '/api/health') return json({ ok: true, product, version: '0.4.1' })
    if (p === '/api/queue') return json({ items, playlists: lists })
    if (p === '/api/playback-state') { if (method === 'PATCH') saved = { ...saved, ...body }; return json({ ...saved, playlist: lists.find(l => l.id === saved.playlist_id) || null }) }
    if (p === '/api/playback/lease') return json({ ok: true })
    if (p === '/api/engine-busy') return json({ busy: false, active: [] })
    if (p === '/api/device') return json({ connected: true, model: 'SONY fixture', free_bytes: 200000000, total_bytes: 2000000000 })
    if (p === '/api/tracks') { res.setHeader('ETag', '"tracks"'); return json([{ id: '1', title: 'Sony One', artist: 'Artist', album: 'Album' }, { id: '2', title: 'Sony Two', artist: 'Artist', album: 'Album' }]) }
    if (p === '/api/jobs/latest') return json(null)
    if (p === '/api/playlists') {
      if (method === 'POST') { const list = { id: String(lists.length + 1), ...body, revision: ++revision, etag: `"${revision}"` }; lists.push(list); return json(list, 201) }
      return json({ items: lists })
    }
    if (p.startsWith('/api/playlists/')) {
      const id = p.split('/').at(-1), row = lists.find(l => l.id === id)
      if (req.headers['if-match'] !== row?.etag) return json({ detail: 'Playlist changed. Refresh.' }, 409)
      if (method === 'DELETE') { lists = lists.filter(l => l.id !== id); return json({ ok: true }) }
      Object.assign(row, body, { revision: ++revision, etag: `"${revision}"` }); return json(row)
    }
    if (p === '/api/device/playlists' || p.startsWith('/api/device/playlists/')) {
      if (method === 'GET') { res.setHeader('ETag', `"sony-${revision}"`); return json({ items: sony }) }
      if (req.headers['if-match'] !== `"sony-${revision}"`) return json({ detail: 'Sony list changed. Refresh.' }, 409)
      revision++
      if (method === 'POST') { const row = { id: '3', ...body }; sony.push(row); return json({ ok: true, job_id: 'native', playlist: row }) }
      const id = p.split('/').at(-1), row = sony.find(l => l.id === id)
      if (method === 'DELETE') sony = sony.filter(l => l.id !== id)
      else Object.assign(row, body)
      return json({ ok: true, job_id: 'native', playlist: row })
    }
    if (/\/api\/media\/[^/]+\/metadata$/.test(p)) { const row = items.find(i => i.id === p.split('/')[3]); Object.assign(row, body); return json(row) }
    if (p.startsWith('/api/queue/items/')) { const id = p.split('/').at(-1); items = items.filter(i => i.id !== id); lists.forEach(l => l.media_ids = l.media_ids.filter(value => value !== id)); return json({ ok: true }) }
    if (p.endsWith('/stream')) { res.setHeader('Content-Type', 'audio/wav'); return res.end(wave) }
    return json({ detail: `Unexpected fixture ${method} ${p}` }, 404)
  }
  const root = path.resolve(__dirname, '../dist'), file = path.resolve(root, `.${p === '/' ? '/index.html' : p}`)
  if (!file.startsWith(root + path.sep) || !fs.existsSync(file)) { res.statusCode = 404; return res.end() }
  res.setHeader('Content-Type', { '.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css', '.png': 'image/png', '.woff2': 'font/woff2' }[path.extname(file)] || 'application/octet-stream')
  fs.createReadStream(file).pipe(res)
})
const delay = ms => new Promise(resolve => setTimeout(resolve, ms))
const run = (win, script) => win.webContents.executeJavaScript(script, true)
async function until(win, expr) { for (let n = 0; n < 100; n++) { if (await run(win, expr)) return; await delay(60) }; throw new Error(`Timed out: ${expr}`) }
const click = (win, label, scope = '.music-manager') => run(win, `Array.from(document.querySelectorAll(${JSON.stringify(scope + ' button')})).find(b=>b.textContent.trim()===${JSON.stringify(label)}&&!b.disabled)?.click()`)
const enter = (win, selector, value) => run(win, `{ const e=document.querySelector(${JSON.stringify(selector)}); Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(e,${JSON.stringify(value)}); e.dispatchEvent(new Event('input',{bubbles:true})); }`)
const ready = win => until(win, `!document.querySelector('.music-manager [role=status]')?.textContent.startsWith('Saving')`)
async function open(win) { await click(win, 'Manage music', 'body'); await until(win, `!!document.querySelector('.music-manager[open]')`); await delay(150) }
async function verify(mode) {
  product = mode; items = [fixture('a'), fixture('b')]; lists = []; sony = []; saved = { media_id: 'a', volume: 0, repeat: 'off' }; revision = 0
  const origin = `http://127.0.0.1:${server.address().port}`, partition = session.fromPartition(`management-${mode}`)
  await partition.cookies.set({ url: origin, name: 'fixture', value: 'management', path: '/api', httpOnly: true, sameSite: 'strict' })
  const win = new BrowserWindow({ show: false, width: 1280, height: 850, webPreferences: { session: partition, sandbox: true, contextIsolation: true, nodeIntegration: false, backgroundThrottling: false } })
  await win.loadURL(origin); await until(win, `!!document.querySelector('.manage-music')`); await open(win)
  assert.equal(await run(win, `document.querySelectorAll('.manager-file-row').length`), 2)
  await run(win, `document.querySelector('.manager-file-row button').click()`)
  await enter(win, '.manager-editor label input', '<b>Edited Ember</b>'); await click(win, 'Save details'); await ready(win)
  await until(win, `document.querySelector('.manager-track-list').textContent.includes('<b>Edited Ember</b>')`)
  assert.equal(await run(win, `document.querySelectorAll('.manager-track-list b').length`), 0)
  await run(win, `document.querySelector('.manager-selection input').click()`); await click(win, 'New playlist from selection')
  await enter(win, '[aria-label="New playlist name"]', 'Evening'); await click(win, 'Create playlist'); await ready(win)
  await until(win, `document.querySelectorAll('.manager-members li').length===2`)
  assert.deepEqual(lists[0].media_ids, ['a', 'b'])
  await run(win, `document.querySelectorAll('.manager-members li')[1].querySelector('button').click()`); await ready(win)
  assert.deepEqual(lists[0].media_ids, ['b', 'a'])
  await enter(win, '[aria-label="Playlist name"]', 'Evening Set'); await click(win, 'Rename'); await ready(win)
  assert.equal(lists[0].name, 'Evening Set')
  await click(win, 'Play playlist'); await until(win, `!document.querySelector('.music-manager')`)
  await until(win, `document.querySelector('.playlist-context')?.textContent.includes('Evening Set')`)
  assert.equal(saved.playlist_id, lists[0].id)
  await open(win); await click(win, 'Playlists'); await click(win, 'Evening Set2 tracks')
  await until(win, `document.querySelectorAll('.manager-members li').length===2`)
  await run(win, `document.querySelector('.manager-members li button:last-child').click()`); await ready(win)
  assert.equal(lists[0].media_ids.length, 1); assert.equal(items.length, 2)
  if (mode === 'bridge') {
    await click(win, 'Sony playlists'); await enter(win, '[aria-label="New Sony playlist name"]', 'Sony Evening'); await click(win, 'Create playlist'); await ready(win)
    await until(win, `!!document.querySelector('.manager-add-tracks')`)
    await run(win, `document.querySelector('.manager-add-tracks').open=true;document.querySelectorAll('.manager-add-tracks input[type=checkbox]').forEach(e=>e.click())`)
    await click(win, 'Add selected tracks'); await ready(win)
    assert.deepEqual(sony[0].track_ids, ['1', '2'])
    await enter(win, '[aria-label="Playlist name"]', 'Sony Night'); await click(win, 'Rename'); await ready(win)
    assert.equal(sony[0].name, 'Sony Night')
    await click(win, 'Delete playlist'); await click(win, 'Confirm removal'); await ready(win)
    assert.equal(sony.length, 0)
    checks.push('bridge: native playlist create, membership, rename and deletion send current ETag; music retained')
  }
  await click(win, 'Files'); await run(win, `document.querySelector('.manager-selection input').click()`)
  // Explicit confirmation is required before deleting managed library copies.
  await click(win, 'Remove selected'); await until(win, `!!document.querySelector('.manager-confirm')`)
  assert.equal(items.length, 2); await click(win, 'Cancel')
  win.setSize(640, 560); await delay(150)
  const bounds = await run(win, `({horizontal:document.documentElement.scrollWidth>innerWidth+1,bottom:document.querySelector('.music-manager').getBoundingClientRect().bottom,height:innerHeight})`)
  assert.equal(bounds.horizontal, false); assert.ok(bounds.bottom <= bounds.height)
  fs.writeFileSync(path.join(output, `${mode}-manager.png`), (await win.webContents.capturePage()).toPNG())
  await click(win, 'Done'); await until(win, `!document.querySelector('.music-manager')`)
  await run(win, `window.dispatchEvent(new Event('walkman:stop-playback'))`); win.destroy()
  checks.push(`${mode}: metadata escaping, create/rename/reorder/play local playlist, membership-only removal, deletion confirmation, bounded manager`)
}
const watchdog = setTimeout(() => { fs.writeFileSync(path.join(output, 'failure.txt'), 'Timeout'); app.exit(1) }, 90000)
async function main() { await app.whenReady(); await new Promise(resolve => server.listen(0, '127.0.0.1', resolve)); await verify('player'); await verify('bridge'); assert.ok(calls.every(call => call.auth)); fs.writeFileSync(path.join(output, 'result.json'), JSON.stringify({ passed: true, checks, calls }, null, 2)) }
main().then(() => { clearTimeout(watchdog); server.close(); app.exit(0) }).catch(error => { fs.writeFileSync(path.join(output, 'failure.txt'), error.stack); console.error(error); clearTimeout(watchdog); server.close(); app.exit(1) })
