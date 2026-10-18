import test from 'node:test'
import assert from 'node:assert/strict'
import { api } from './api.js'
import { playlistFailureMessage, resolvePlaylistCode, surfaceError, PLAYLIST_FAILURES } from './playlistFailure.js'

test('PLAYLIST_REF_MISSING tells the user device writes are blocked by a playlist that references a missing track', () => {
  const copy = playlistFailureMessage('PLAYLIST_REF_MISSING')
  assert.equal(copy.title, 'Device writes are blocked')
  assert.match(copy.message, /playlist/i)
  assert.match(copy.message, /missing track/i)
  assert.match(copy.message, /writes stay blocked/i)
})

test('PLAYLIST_JOURNAL_PENDING tells the user an interrupted playlist transaction blocks reads and writes', () => {
  const copy = playlistFailureMessage('PLAYLIST_JOURNAL_PENDING')
  assert.match(copy.title, /pending|recovery/i)
  assert.match(copy.message, /interrupted playlist transaction/i)
  assert.match(copy.message, /pending/i)
  assert.match(copy.message, /reads and writes stay blocked/i)
  assert.match(copy.message, /recovered/i)
})

test('PLAYLIST_SLOTS_EXHAUSTED tells the user the Walkman has no playlist slots left', () => {
  const copy = playlistFailureMessage('PLAYLIST_SLOTS_EXHAUSTED')
  assert.match(copy.title, /no playlist slots left/i)
  assert.match(copy.message, /no playlist slots left/i)
  assert.match(copy.message, /walkman|device/i)
})

test('absent and unknown codes do not invent a playlist failure', () => {
  for (const code of [undefined, null, '', 'playlist_failed', 'verify_device_state', 'PLAYLIST_REF_MISSING ']) {
    assert.equal(playlistFailureMessage(code), null)
  }
  assert.deepEqual(Object.keys(PLAYLIST_FAILURES).sort(), [
    'PLAYLIST_JOURNAL_PENDING',
    'PLAYLIST_REF_MISSING',
    'PLAYLIST_SLOTS_EXHAUSTED',
  ])
})

test('resolvePlaylistCode reads fatal_code and ignores application codes', () => {
  assert.equal(resolvePlaylistCode({ fatal_code: 'PLAYLIST_REF_MISSING' }), 'PLAYLIST_REF_MISSING')
  assert.equal(resolvePlaylistCode({ code: 'PLAYLIST_REF_MISSING' }), null)
  assert.equal(resolvePlaylistCode({ code: 'playlist_failed', fatal_code: null }), null)
  assert.equal(resolvePlaylistCode({ code: 'playlist_failed', fatal_code: 'PLAYLIST_JOURNAL_PENDING' }), 'PLAYLIST_JOURNAL_PENDING')
  assert.equal(resolvePlaylistCode({ detail: { message: 'journal open', fatal_code: 'PLAYLIST_JOURNAL_PENDING' } }), 'PLAYLIST_JOURNAL_PENDING')
  assert.equal(resolvePlaylistCode({ detail: 'Connection lost' }), null)
  assert.equal(resolvePlaylistCode({ files: [{ reason_code: 'PLAYLIST_SLOTS_EXHAUSTED', code: 'PLAYLIST_SLOTS_EXHAUSTED', fatal_code: null }] }), null)
  assert.equal(resolvePlaylistCode({ files: [{ fatal_code: 'PLAYLIST_SLOTS_EXHAUSTED', reason_code: 'playlist_failed' }] }), 'PLAYLIST_SLOTS_EXHAUSTED')
  assert.equal(resolvePlaylistCode({ fatal_code: 'nope' }, { files: [{ fatal_code: 'PLAYLIST_REF_MISSING' }] }), 'PLAYLIST_REF_MISSING')
  assert.equal(resolvePlaylistCode(null, undefined, { message: 'failed' }), null)
  assert.equal(resolvePlaylistCode('nope'), null)
})

test('Walkman and Transfer views surface the playlist alert and hide the generic strip', () => {
  for (const view of ['device', 'transfer']) {
    const surface = surfaceError({
      pollError: 'Connection interrupted: journal open',
      fatalCode: 'PLAYLIST_JOURNAL_PENDING',
      view,
    })
    assert.equal(surface.strip, null)
    assert.match(surface.alert.message, /reads and writes stay blocked/i)
  }
})

test('Listening keeps a single alert in the existing notice strip', () => {
  const surface = surfaceError({
    pollError: 'Connection interrupted: journal open',
    fatalCode: 'PLAYLIST_REF_MISSING',
    view: 'listening',
  })
  assert.equal(surface.alert, null)
  assert.equal(surface.role, 'alert')
  assert.equal(surface.errorStyle, true)
  assert.match(surface.strip, /Device writes are blocked/)
  assert.match(surface.strip, /missing track/)
})

test('unknown or absent codes keep the existing generic notice text and role', () => {
  assert.deepEqual(surfaceError({ error: 'Playlist edit rejected', fatalCode: 'playlist_failed', view: 'device' }), {
    alert: null,
    strip: 'Playlist edit rejected',
    role: 'alert',
    errorStyle: true,
  })
  assert.deepEqual(surfaceError({ pollError: 'Connection interrupted: nope', view: 'transfer' }), {
    alert: null,
    strip: 'Connection interrupted: nope',
    role: 'status',
    errorStyle: true,
  })
  assert.deepEqual(surfaceError({ notice: 'Backup saved.', view: 'device' }), {
    alert: null,
    strip: 'Backup saved.',
    role: 'status',
    errorStyle: false,
  })
  assert.deepEqual(surfaceError({ view: 'listening' }), {
    alert: null,
    strip: null,
    role: 'status',
    errorStyle: false,
  })
  assert.equal(surfaceError({ error: 'Playlist edit rejected', fatalCode: null, view: 'transfer' }).alert, null)
})

test('api errors expose fatal_code from detail and leave it null when the payload is generic', async () => {
  const original = globalThis.fetch
  const respond = detail => {
    globalThis.fetch = async () => ({ ok: false, status: 409, json: async () => ({ detail }) })
  }
  try {
    respond({ code: 'playlist_failed', message: 'Playlist edit rejected', fatal_code: 'PLAYLIST_REF_MISSING' })
    await assert.rejects(api.device(), error => {
      assert.equal(error.message, 'Playlist edit rejected')
      assert.equal(error.code, 'playlist_failed')
      assert.equal(error.fatal_code, 'PLAYLIST_REF_MISSING')
      return true
    })
    respond({ message: 'journal open', fatal_code: 'PLAYLIST_JOURNAL_PENDING' })
    await assert.rejects(api.tracks(), error => {
      assert.equal(error.fatal_code, 'PLAYLIST_JOURNAL_PENDING')
      assert.equal(error.code, undefined)
      return true
    })
    respond({ code: 'playlist_failed', message: 'Playlist edit rejected', fatal_code: null })
    await assert.rejects(api.device(), error => {
      assert.equal(error.fatal_code, null)
      assert.equal(error.code, 'playlist_failed')
      return true
    })
    respond('Connection lost')
    await assert.rejects(api.device(), error => {
      assert.equal(error.message, 'Connection lost')
      assert.equal(error.fatal_code, null)
      return true
    })
  } finally {
    globalThis.fetch = original
  }
})
