import { test } from 'node:test'
import assert from 'node:assert/strict'
import { PLAYLIST_RECOVERY_HELP, playlistRecoveryHelp } from './help/playlistRecovery.js'

const CODES = [
  'PLAYLIST_REF_MISSING',
  'PLAYLIST_JOURNAL_PENDING',
  'PLAYLIST_SLOTS_EXHAUSTED',
  'GENERIC',
]

test('playlist recovery help defines the three codes and GENERIC', () => {
  assert.deepEqual(Object.keys(PLAYLIST_RECOVERY_HELP), CODES)
  for (const code of CODES) {
    const entry = PLAYLIST_RECOVERY_HELP[code]
    assert.equal(typeof entry.title, 'string')
    assert.ok(entry.title.trim(), `${code} title`)
    assert.equal(typeof entry.explanation, 'string')
    assert.ok(entry.explanation.trim(), `${code} explanation`)
    assert.equal(typeof entry.action, 'string')
    assert.ok(entry.action.trim(), `${code} action`)
  }
  assert.equal(PLAYLIST_RECOVERY_HELP.PLAYLIST_REF_MISSING.actionId, 'repair')
  assert.equal(PLAYLIST_RECOVERY_HELP.PLAYLIST_JOURNAL_PENDING.actionId, 'inspect_recover')
  assert.equal(PLAYLIST_RECOVERY_HELP.PLAYLIST_SLOTS_EXHAUSTED.actionId, 'free_slots')
  assert.equal(PLAYLIST_RECOVERY_HELP.GENERIC.actionId, null)
})

test('unknown or missing codes use the GENERIC fallback', () => {
  assert.equal(playlistRecoveryHelp('PLAYLIST_REF_MISSING'), PLAYLIST_RECOVERY_HELP.PLAYLIST_REF_MISSING)
  assert.equal(playlistRecoveryHelp('NOT_A_CODE'), PLAYLIST_RECOVERY_HELP.GENERIC)
  assert.equal(playlistRecoveryHelp(undefined), PLAYLIST_RECOVERY_HELP.GENERIC)
  assert.equal(playlistRecoveryHelp(''), PLAYLIST_RECOVERY_HELP.GENERIC)
})
