import { test } from 'node:test'
import assert from 'node:assert/strict'
import { PLAYLIST_RECOVERY_HELP, playlistRecoveryHelp, formatPlaylistRecovery } from './help/playlistRecovery.js'

const CODES = [
  'PLAYLIST_REF_MISSING',
  'PLAYLIST_JOURNAL_PENDING',
  'PLAYLIST_SLOTS_EXHAUSTED',
  'PLAYLIST_LIBRARY_NOT_LOADED',
  'DEVICE_FILE_LOCKED',
  'DEVICE_ROLLBACK_FAILED',
  'DEVICE_FILE_READ_ONLY',
  'DEVICE_PROBE_RESTORE_FAILED',
  'DEVICE_PROBE_CONFLICT',
  'DEVICE_PROBE_PENDING',
  'GENERIC',
]

test('playlist recovery help defines the fatal codes and GENERIC', () => {
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
  assert.equal(PLAYLIST_RECOVERY_HELP.PLAYLIST_LIBRARY_NOT_LOADED.actionId, 'reconnect_retry')
  assert.equal(PLAYLIST_RECOVERY_HELP.DEVICE_FILE_LOCKED.actionId, 'close_and_retry')
  assert.equal(PLAYLIST_RECOVERY_HELP.DEVICE_FILE_LOCKED.explanation.includes('{path}'), false)
  assert.equal(PLAYLIST_RECOVERY_HELP.DEVICE_ROLLBACK_FAILED.actionId, 'inspect_recover')
  assert.equal(PLAYLIST_RECOVERY_HELP.DEVICE_FILE_READ_ONLY.actionId, 'clear_read_only_retry')
  assert.equal(PLAYLIST_RECOVERY_HELP.DEVICE_FILE_READ_ONLY.explanation.includes('{path}'), false)
  assert.equal(PLAYLIST_RECOVERY_HELP.DEVICE_PROBE_RESTORE_FAILED.actionId, 'inspect_recover')
  assert.equal(PLAYLIST_RECOVERY_HELP.DEVICE_PROBE_CONFLICT.actionId, 'manual_help')
  assert.equal(PLAYLIST_RECOVERY_HELP.DEVICE_PROBE_PENDING.actionId, 'inspect_recover')
  assert.match(PLAYLIST_RECOVERY_HELP.DEVICE_PROBE_PENDING.explanation, /Nothing is broken/)
  assert.equal(PLAYLIST_RECOVERY_HELP.DEVICE_PROBE_PENDING.action.includes('puts it back'), false)
  assert.equal(PLAYLIST_RECOVERY_HELP.GENERIC.actionId, null)
})

test('unknown or missing codes use the GENERIC fallback', () => {
  assert.equal(playlistRecoveryHelp('PLAYLIST_REF_MISSING'), PLAYLIST_RECOVERY_HELP.PLAYLIST_REF_MISSING)
  assert.equal(playlistRecoveryHelp('NOT_A_CODE'), PLAYLIST_RECOVERY_HELP.GENERIC)
  assert.equal(playlistRecoveryHelp(undefined), PLAYLIST_RECOVERY_HELP.GENERIC)
  assert.equal(playlistRecoveryHelp(''), PLAYLIST_RECOVERY_HELP.GENERIC)
})

test('formatPlaylistRecovery appends a plain-text path and omits it when absent', () => {
  const missing = formatPlaylistRecovery('DEVICE_FILE_LOCKED')
  assert.equal(missing.explanation, PLAYLIST_RECOVERY_HELP.DEVICE_FILE_LOCKED.explanation)
  assert.equal(missing.explanation.includes('{path}'), false)
  assert.equal(formatPlaylistRecovery('DEVICE_FILE_LOCKED', { path: '' }).explanation, missing.explanation)
  assert.equal(formatPlaylistRecovery('DEVICE_FILE_READ_ONLY', { path: null }).explanation, PLAYLIST_RECOVERY_HELP.DEVICE_FILE_READ_ONLY.explanation)

  const normal = formatPlaylistRecovery('DEVICE_FILE_LOCKED', { path: 'OMGAUDIO/10F00/10000001.OMA' })
  assert.equal(normal.explanation, `${PLAYLIST_RECOVERY_HELP.DEVICE_FILE_LOCKED.explanation} (OMGAUDIO/10F00/10000001.OMA)`)
  assert.equal(typeof normal.explanation, 'string')

  const markup = formatPlaylistRecovery('DEVICE_FILE_READ_ONLY', { path: '<b>x</b>' })
  assert.equal(markup.explanation, `${PLAYLIST_RECOVERY_HELP.DEVICE_FILE_READ_ONLY.explanation} (<b>x</b>)`)
  assert.equal(typeof markup.title, 'string')
  assert.equal(typeof markup.action, 'string')
})

test('formatPlaylistRecovery uses recover wording only for that context, and recoveryAction overrides the action id', () => {
  const locked = formatPlaylistRecovery('DEVICE_FILE_LOCKED')
  assert.equal(locked.actionId, 'close_and_retry')
  assert.match(locked.explanation, /nothing was changed/)

  const readOnly = formatPlaylistRecovery('DEVICE_FILE_READ_ONLY')
  assert.equal(readOnly.actionId, 'clear_read_only_retry')
  assert.match(readOnly.explanation, /stopped before changing anything/)

  const lockedRecover = formatPlaylistRecovery('DEVICE_FILE_LOCKED', { context: 'recover', path: 'OMGAUDIO/10F00/10000001.OMA' })
  assert.equal(lockedRecover.actionId, 'inspect_recover')
  assert.match(lockedRecover.explanation, /already committed and only needs finishing/)
  assert.equal(lockedRecover.explanation.includes('nothing was changed'), false)
  assert.match(lockedRecover.action, /run Recover again/)
  assert.equal(lockedRecover.explanation.endsWith(' (OMGAUDIO/10F00/10000001.OMA)'), true)

  const readOnlyRecover = formatPlaylistRecovery('DEVICE_FILE_READ_ONLY', { context: 'recover' })
  assert.equal(readOnlyRecover.actionId, 'inspect_recover')
  assert.match(readOnlyRecover.explanation, /already committed and only needs finishing/)
  assert.equal(readOnlyRecover.explanation.includes('stopped before changing anything'), false)
  assert.match(readOnlyRecover.action, /Read-only/)
  assert.match(readOnlyRecover.action, /run Recover again/)

  const overridden = formatPlaylistRecovery('DEVICE_FILE_LOCKED', { recoveryAction: 'custom_step' })
  assert.equal(overridden.actionId, 'custom_step')
  assert.equal(overridden.action, PLAYLIST_RECOVERY_HELP.DEVICE_FILE_LOCKED.action)
  const overriddenRecover = formatPlaylistRecovery('DEVICE_FILE_READ_ONLY', { context: 'recover', recoveryAction: 'custom_step' })
  assert.equal(overriddenRecover.actionId, 'custom_step')
  assert.equal(formatPlaylistRecovery('DEVICE_FILE_LOCKED', { recoveryAction: '' }).actionId, 'close_and_retry')

  const unknown = formatPlaylistRecovery('NOT_A_CODE', { path: 'OMGAUDIO/10F00/10000001.OMA' })
  assert.equal(unknown.title, PLAYLIST_RECOVERY_HELP.GENERIC.title)
  assert.equal(unknown.explanation, PLAYLIST_RECOVERY_HELP.GENERIC.explanation)
  assert.equal(unknown.action, PLAYLIST_RECOVERY_HELP.GENERIC.action)
  assert.equal(unknown.actionId, null)
  assert.equal(formatPlaylistRecovery(undefined).title, PLAYLIST_RECOVERY_HELP.GENERIC.title)
})

test('formatPlaylistRecovery shows probe locations as plain text and still honors recoveryAction', () => {
  const file = 'OMGAUDIO/10F00/10000001.OMA'
  const copy = 'OMGAUDIO/10F00/10000001.OMA.jsymphonic-probe'
  const both = formatPlaylistRecovery('DEVICE_PROBE_RESTORE_FAILED', { path: file, probePath: copy })
  assert.equal(both.explanation, `${PLAYLIST_RECOVERY_HELP.DEVICE_PROBE_RESTORE_FAILED.explanation} (file: ${file}; renamed copy: ${copy})`)
  assert.equal(typeof both.explanation, 'string')

  const pathOnly = formatPlaylistRecovery('DEVICE_PROBE_CONFLICT', { path: file })
  assert.equal(pathOnly.explanation.endsWith(` (file: ${file})`), true)
  assert.equal(pathOnly.explanation.includes('renamed copy'), false)

  const probeOnly = formatPlaylistRecovery('DEVICE_PROBE_PENDING', { probePath: '<b>x</b>' })
  assert.equal(probeOnly.explanation, `${PLAYLIST_RECOVERY_HELP.DEVICE_PROBE_PENDING.explanation} (renamed copy: <b>x</b>)`)
  assert.equal(formatPlaylistRecovery('DEVICE_PROBE_PENDING', { path: '', probePath: '' }).explanation, PLAYLIST_RECOVERY_HELP.DEVICE_PROBE_PENDING.explanation)

  assert.equal(formatPlaylistRecovery('DEVICE_PROBE_CONFLICT', { recoveryAction: 'custom_step' }).actionId, 'custom_step')
  assert.equal(formatPlaylistRecovery('DEVICE_PROBE_RESTORE_FAILED', { recoveryAction: 'custom_step' }).actionId, 'custom_step')
  assert.equal(formatPlaylistRecovery('DEVICE_PROBE_PENDING', { recoveryAction: 'custom_step' }).actionId, 'custom_step')
  assert.equal(formatPlaylistRecovery('DEVICE_PROBE_CONFLICT', { recoveryAction: '' }).actionId, 'manual_help')
})
