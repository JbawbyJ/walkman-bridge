import test from 'node:test'
import assert from 'node:assert/strict'
import { api, RECOVERY_ACTIONS } from './api.js'

test('recovery action ids map onto inspect, recover, repair, and delete', async () => {
  assert.deepEqual(RECOVERY_ACTIONS.inspect_recover.map(step => step.id), ['inspect', 'recover'])
  assert.equal(RECOVERY_ACTIONS.inspect_recover[0].method, 'GET')
  assert.equal(RECOVERY_ACTIONS.inspect_recover[0].path, '/device/playlist-recovery/inspect')
  assert.equal(RECOVERY_ACTIONS.inspect_recover[1].method, 'POST')
  assert.equal(RECOVERY_ACTIONS.inspect_recover[1].call, 'recoverPlaylistJournal')
  assert.equal(RECOVERY_ACTIONS.repair[0].id, 'repair')
  assert.equal(RECOVERY_ACTIONS.repair[0].path, '/device/playlist-recovery/repair')
  assert.deepEqual(RECOVERY_ACTIONS.free_slots, [
    { id: 'deleteDevicePlaylist', method: 'DELETE', path: '/device/playlists/{id}', call: 'deleteDevicePlaylist' },
  ])
  assert.deepEqual(RECOVERY_ACTIONS.inspect_recover.fatal_codes, [
    'PLAYLIST_JOURNAL_PENDING',
    'DEVICE_ROLLBACK_FAILED',
    'DEVICE_PROBE_RESTORE_FAILED',
  ])
  assert.equal(RECOVERY_ACTIONS.manual_help.fatal_code, 'DEVICE_PROBE_CONFLICT')
  assert.deepEqual(RECOVERY_ACTIONS.manual_help.steps, [])
  assert.equal(RECOVERY_ACTIONS.manual_help.method, undefined)
  assert.equal(RECOVERY_ACTIONS.manual_help.path, undefined)
  assert.equal(RECOVERY_ACTIONS.manual_help.call, undefined)
  assert.equal(RECOVERY_ACTIONS.reconnect_retry.fatal_code, 'PLAYLIST_LIBRARY_NOT_LOADED')
  assert.equal(RECOVERY_ACTIONS.reconnect_retry.steps[1].call, 'repairPlaylists')
  assert.equal(RECOVERY_ACTIONS.close_and_retry.fatal_code, 'DEVICE_FILE_LOCKED')
  assert.deepEqual(RECOVERY_ACTIONS.close_and_retry.steps.map(step => step.id), ['close', 'retry'])
  assert.equal(RECOVERY_ACTIONS.close_and_retry.steps.every(step => step.path === undefined && step.call === undefined), true)
  assert.equal(RECOVERY_ACTIONS.clear_read_only_retry.fatal_code, 'DEVICE_FILE_READ_ONLY')
  assert.deepEqual(RECOVERY_ACTIONS.clear_read_only_retry.steps.map(step => step.id), ['clear_read_only', 'retry'])
  assert.equal(RECOVERY_ACTIONS.clear_read_only_retry.steps.every(step => step.path === undefined && step.call === undefined && step.method === undefined), true)
  assert.equal(RECOVERY_ACTIONS.GENERIC, null)

  const previous = globalThis.fetch
  const calls = []
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url, method: options.method || 'GET' })
    return { ok: true, status: 200, headers: { get: () => null }, json: async () => ({ ok: true, outcome: null }) }
  }
  try {
    await api.inspectPlaylistJournal()
    await api.recoverPlaylistJournal()
    await api.repairPlaylists()
  } finally {
    globalThis.fetch = previous
  }
  assert.deepEqual(calls, [
    { url: '/api/device/playlist-recovery/inspect', method: 'GET' },
    { url: '/api/device/playlist-recovery/recover', method: 'POST' },
    { url: '/api/device/playlist-recovery/repair', method: 'POST' },
  ])
})
