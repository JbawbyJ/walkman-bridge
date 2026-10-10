// Independent review regressions. Run from the project root with:
// node --test frontend/tests/playback-review-regressions.test.mjs
// This executes the checked-out hook with deterministic effect scheduling and
// fake Audio/Web Audio boundaries. It is not a browser or backend lease test.
import fs from 'node:fs'
import vm from 'node:vm'
import test from 'node:test'
import assert from 'node:assert/strict'
import { nextTrackId, playable, restoreSession } from '../src/playback.js'
import { dspSettings } from '../src/dsp.js'

const source = fs.readFileSync(new URL('../src/usePlayer.js', import.meta.url), 'utf8')
  .replace(/^import .*\r?\n/gm, '')
  .replace('export function usePlayer', 'function usePlayer') + '\nthis.usePlayer = usePlayer;'
const appSource = fs.readFileSync(new URL('../src/App.jsx', import.meta.url), 'utf8')
const ready = (id, lufs = -9) => ({ id, status: 'ready', scan: { ok: true }, duration_seconds: 100, integrated_lufs: lufs })

function mount(initialItems, session = {}, save = () => Promise.resolve({}), boundaries = {}) {
  const slots = [], graphs = [], prepared = [], leaseCalls = []
  let cursor = 0, pending = [], player, items = initialItems, serverLease = null
  class Audio {
    constructor() {
      this.attrs = {}; this.dataset = {}; this.listeners = {}
      this.currentTime = 0; this.duration = 100; this.paused = true; this.error = null
    }
    set src(value) {
      assert.equal(serverLease, value.split('?')[0].split('/').at(-1), 'source assignment requires the matching acquired lease')
      this.attrs.src = value; this.currentTime = 0; this.error = null
    }
    get src() { return this.attrs.src }
    getAttribute(key) { return this.attrs[key] || null }
    removeAttribute(key) { delete this.attrs[key] }
    load() {}
    addEventListener(name, handler) { this.listeners[name] = handler }
    removeEventListener(name) { delete this.listeners[name] }
    emit(name) { this.listeners[name]?.() }
    pause() { const wasPlaying = !this.paused; this.paused = true; if (wasPlaying) this.emit('pause') }
    play() { this.paused = false; this.emit('playing'); return Promise.resolve() }
  }
  const context = {
    useState(initial) {
      const index = cursor++
      if (!slots[index]) slots[index] = { value: typeof initial === 'function' ? initial() : initial }
      return [slots[index].value, update => {
        slots[index].value = typeof update === 'function' ? update(slots[index].value) : update
      }]
    },
    useRef(initial) { const index = cursor++; if (!slots[index]) slots[index] = { current: initial }; return slots[index] },
    useEffect(effect, deps) {
      const index = cursor++, previous = slots[index]
      if (!previous || deps.some((value, i) => !Object.is(value, previous.deps[i]))) {
        pending.push(() => { previous?.cleanup?.(); slots[index] = { deps, cleanup: effect() } })
      }
    },
    api: {
      savePlaybackState: save,
      mediaUrl: (id, revision) => `/media/${id}${revision ? `?revision=${revision}` : ''}`,
      async acquirePlaybackLease(id) { leaseCalls.push(`POST ${id}`); await boundaries.acquire?.(id); serverLease = id },
      async releasePlaybackLease(id) { leaseCalls.push(`DELETE ${id}`); await boundaries.release?.(id); if (serverLease === id) serverLease = null },
      removeQueueItem: id => boundaries.remove?.(id),
    },
    nextTrackId, playable, restoreSession, dspSettings, Audio,
    createAudioGraph() {
      const graph = { updates: [], update(input) { this.updates.push(dspSettings(input)) }, resume: () => boundaries.resume?.() || Promise.resolve(), dispose() {} }
      graphs.push(graph); return graph
    },
    window: { addEventListener() {}, removeEventListener() {} },
    // Persistence timing is controlled through explicit stop/forget calls.
    setTimeout() { return 1 }, clearTimeout() {}, Date, Promise, Set, Number, Math,
  }
  vm.runInNewContext(source, context)
  const render = (nextItems = items) => {
    items = nextItems; cursor = 0; pending = []
    player = context.usePlayer(items, 'player', id => prepared.push(id), session)
    const effects = pending; pending = []; effects.forEach(effect => effect())
    return player
  }
  render()
  const settle = async () => { await new Promise(setImmediate); render() }
  return { render, settle, get player() { return player }, get serverLease() { return serverLease }, graphs, prepared, leaseCalls }
}

test('manual Next at queue end never leaves active audio behind paused UI', async () => {
  const h = mount([ready('a'), ready('b')])
  await h.player.play('b'); h.render(); h.player.advance(1); h.render()
  assert.equal(h.player.playing, !h.player.audio.current.paused)
})

test('saved playlist plays its own order and stops before unrelated library music', async () => {
  const saved = []
  const h = mount([ready('a'), ready('b'), ready('c')], {}, body => { saved.push(body); return Promise.resolve({}) })
  await h.player.setPlaylist({ id: 'night', name: 'Night', media_ids: ['c', 'a'] }); h.render()
  assert.equal(h.player.id, 'c')
  h.player.advance(1); await h.settle()
  assert.equal(h.player.id, 'a')
  h.player.advance(1); await h.settle()
  assert.equal(h.player.audio.current.paused, true)
  assert.equal(h.player.id, 'a')
  assert.equal(saved.at(-1).playlist_id, 'night')
})

test('playlist session restores paused and repeat follows only saved members', async () => {
  const playlist = { id: 'night', name: 'Night', media_ids: ['c', 'a'] }
  const h = mount([ready('a'), ready('b'), ready('c')], { media_id: 'a', position_seconds: 23, repeat: 'all', playlist })
  await h.settle()
  assert.equal(h.player.audio.current.paused, true)
  assert.equal(h.player.activePlaylist.name, 'Night')
  h.player.advance(1); await h.settle()
  assert.equal(h.player.id, 'c')
  await h.player.play('b'); h.render()
  assert.equal(h.player.activePlaylist, null)
})

test('Stop cancels a playlist start waiting on session persistence', async () => {
  let release
  const waiting = new Promise(resolve => { release = resolve })
  const h = mount([ready('a'), ready('b')], {}, () => waiting)
  const switching = h.player.setPlaylist({ id: 'night', name: 'Night', media_ids: ['b'] })
  const stopping = h.player.stop()
  release({}); await Promise.all([switching, stopping]); h.render()
  assert.equal(h.player.audio.current.paused, true)
  assert.equal(h.player.audio.current.getAttribute('src'), null)
})

test('stale restored track cannot escape the saved playlist on Play', async () => {
  const h = mount([ready('a'), ready('b')], { media_id: 'a', position_seconds: 42,
    playlist: { id: 'night', name: 'Night', media_ids: ['b'] } })
  await h.settle()
  assert.equal(h.player.id, 'b')
  assert.equal(h.player.position, 0)
  assert.equal(h.player.audio.current.paused, true)
  await h.player.play(); h.render()
  assert.equal(h.player.id, 'b')
  assert.equal(h.player.activePlaylist.id, 'night')
})

test('removing current membership stops it and deleting playlist returns to all music', async () => {
  const h = mount([ready('a'), ready('b')])
  await h.player.setPlaylist({ id: 'night', name: 'Night', media_ids: ['a', 'b'] }); h.render()
  h.player.syncPlaylists([{ id: 'night', name: 'Night two', media_ids: ['b'] }]); await h.settle()
  assert.equal(h.player.audio.current.paused, true)
  assert.equal(h.player.id, null)
  h.player.syncPlaylists([]); await h.settle()
  assert.equal(h.player.activePlaylist, null)
})

test('codec preparation during paused session restoration does not start playback', async () => {
  const h = mount([ready('a')], { media_id: 'a', position_seconds: 25 })
  await h.settle()
  h.player.audio.current.error = { code: 4 }; h.player.audio.current.emit('error'); h.render()
  assert.deepEqual(h.prepared, ['a'])
  await h.player.retryPrepared('a'); h.render()
  assert.equal(h.player.audio.current.paused, true)
})

test('Stop cancels playback intent for pending codec preparation', async () => {
  const h = mount([ready('a')], { media_id: 'a' })
  await h.settle()
  h.player.audio.current.error = { code: 4 }; h.player.audio.current.emit('error'); h.render()
  await h.player.stop(); h.render(); await h.player.retryPrepared('a'); h.render()
  assert.equal(h.player.audio.current.paused, true)
})

test('first graph applies selected-track loudness when enabled before initial Play', async () => {
  const h = mount([ready('a', -9)], { media_id: 'a' })
  h.player.setDsp({ enabled: true, loudness: true }); h.render()
  await h.player.play('a'); h.render()
  assert.equal(h.graphs[0].updates.at(-1).preampDb, -9)
})

test('old forget completion cannot clear a newly playing different track', async () => {
  let release
  const saving = new Promise(resolve => { release = resolve })
  const h = mount([ready('a'), ready('b')], {}, () => saving)
  await h.player.play('a'); h.render()
  const forgetting = h.player.forget(); h.render()
  await h.player.play('b'); h.render()
  release({}); await forgetting; h.render()
  assert.equal(h.player.id, 'b')
})

// Execute App's actual removal closure so its ordering remains part of the
// regression. This deliberately models the dangerous instant DELETE is issued,
// after an earlier stream response has finished and released its backend lease.
async function removalSnapshot(restartDuringSave) {
  let release, atDelete
  const saving = new Promise(resolve => { release = resolve })
  const h = mount([ready('a')], {}, () => saving, { remove: async id => {
    atDelete = { id, src: h.player.audio.current.src, paused: h.player.audio.current.paused }
  } })
  await h.player.play('a'); h.render()
  const declaration = appSource.match(/^\s*const remove = (.+)$/m)?.[1]
  assert.ok(declaration, 'App removal closure must be available to this bounded harness')
  const remove = vm.runInNewContext(`(${declaration})`, {
    player: h.player,
    latest: { get current() { return { player: h.player } } },
    action: async task => task(),
    api: { removeQueueItem: async id => {
      atDelete = { id, src: h.player.audio.current.src, paused: h.player.audio.current.paused }
    } },
  })
  const removing = remove('a'); h.render()
  if (restartDuringSave) { await h.player.play('a'); h.render() }
  release({}); await removing; h.render([])
  return { ...atDelete, remainingSrc: h.player.audio.current.src }
}

test('ordinary current-track removal releases audio before queue DELETE', async () => {
  const snapshot = await removalSnapshot(false)
  assert.equal(snapshot.paused, true)
  assert.equal(snapshot.src, undefined)
})

test('restarting current track during removal cannot leave playback active at queue DELETE', async () => {
  const snapshot = await removalSnapshot(true)
  assert.ok(snapshot.paused || snapshot.src !== '/media/a', JSON.stringify(snapshot))
})

test('paused restoration waits for lease acquisition before assigning a source', async () => {
  let allowAcquire
  const acquiring = new Promise(resolve => { allowAcquire = resolve })
  const h = mount([ready('a')], { media_id: 'a' }, undefined, { acquire: () => acquiring })
  await h.settle()
  assert.equal(h.player.audio.current.src, undefined)
  allowAcquire(); await h.settle()
  assert.equal(h.player.audio.current.src, '/media/a')
  assert.equal(h.player.audio.current.paused, true)
})

test('Stop unloads immediately and waits for the lease release before completion', async () => {
  let allowRelease, completed = false
  const releasing = new Promise(resolve => { allowRelease = resolve })
  const h = mount([ready('a')], {}, undefined, { release: () => releasing })
  await h.player.play('a'); h.render()
  const stopping = h.player.stop().then(() => { completed = true })
  await h.settle()
  assert.equal(h.player.audio.current.src, undefined)
  assert.equal(completed, false)
  allowRelease(); await stopping
  assert.equal(h.serverLease, null)
})

test('same-ID restart acquires after the old Stop release and keeps its lease', async () => {
  let allowRelease
  const releasing = new Promise(resolve => { allowRelease = resolve })
  const h = mount([ready('a')], {}, undefined, { release: () => releasing })
  await h.player.play('a'); h.render()
  const stopping = h.player.stop()
  const restarting = h.player.play('a')
  await h.settle()
  assert.deepEqual(h.leaseCalls, ['POST a', 'DELETE a'])
  assert.equal(h.player.audio.current.src, undefined)
  allowRelease(); await Promise.all([stopping, restarting]); h.render()
  assert.deepEqual(h.leaseCalls, ['POST a', 'DELETE a', 'POST a'])
  assert.equal(h.serverLease, 'a')
  assert.equal(h.player.audio.current.paused, false)
})

test('in-flight acquisition cannot attach its source after Stop and a newer selection', async () => {
  let allowAcquire
  const acquiring = new Promise(resolve => { allowAcquire = resolve })
  const h = mount([ready('a'), ready('b')], {}, undefined, { acquire: id => id === 'a' ? acquiring : undefined })
  const first = h.player.play('a'); await h.settle()
  const stopping = h.player.stop()
  const next = h.player.play('b')
  allowAcquire(); await Promise.all([first, stopping, next]); h.render()
  assert.deepEqual(h.leaseCalls, ['POST a', 'DELETE a', 'POST b'])
  assert.equal(h.player.audio.current.src, '/media/b')
  assert.equal(h.serverLease, 'b')
})

test('failed lease acquisition never exposes a playback source', async () => {
  const h = mount([ready('a')], {}, undefined, { acquire: async () => { throw new Error('Lease unavailable') } })
  await h.player.play('a'); h.render()
  assert.equal(h.player.audio.current.src, undefined)
  assert.equal(h.player.playing, false)
  assert.equal(h.player.error, 'Lease unavailable')
})

test('codec fallback preserves an explicit Play request through derivative scanning', async () => {
  const h = mount([ready('a')])
  await h.player.play('a'); h.render()
  h.player.audio.current.paused = true
  h.player.audio.current.error = { code: 4 }; h.player.audio.current.emit('error'); h.render()
  h.render([{ ...ready('a'), status: 'scanning' }])
  h.render([ready('a')])
  await h.player.retryPrepared('a'); h.render()
  assert.equal(h.player.audio.current.paused, false)
  assert.equal(h.player.id, 'a')
})

test('a pending graph resume cannot start playback after Stop', async () => {
  let resume
  const resuming = new Promise(resolve => { resume = resolve })
  const h = mount([ready('a')], {}, undefined, { resume: () => resuming })
  const playing = h.player.play('a'); await h.settle()
  const stopping = h.player.stop()
  resume(); await Promise.all([playing, stopping]); h.render()
  assert.equal(h.player.audio.current.paused, true)
  assert.equal(h.player.audio.current.src, undefined)
  assert.equal(h.serverLease, null)
})
