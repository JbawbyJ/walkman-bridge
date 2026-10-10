import { test } from 'node:test'
import assert from 'node:assert/strict'
import { backupFailureMessage, backupSavedMessage, backupSuccessPath } from './backup.js'

test('backupSavedMessage includes the dest path', () => {
  const dest = 'C:\\Users\\user\\AppData\\Local\\Walkman Bridge\\backups\\2026-08-31-013942'
  assert.equal(backupSavedMessage(dest), `BACKUP SAVED · ${dest}`)
})

test('backupSuccessPath reads path from POST /api/backup body', () => {
  assert.equal(
    backupSuccessPath({ ok: true, path: '/tmp/backups/2026-08-31-013942' }),
    '/tmp/backups/2026-08-31-013942',
  )
  assert.equal(backupSuccessPath({ ok: true }), null)
  assert.equal(backupSuccessPath(null), null)
})

test('404 and 405 without a live device error are the hand-copy fallback', () => {
  const fallback = backupFailureMessage({
    status: 404,
    message: '404 Not Found — {"detail":"Not Found"}',
  })
  assert.match(fallback, /copy the whole Walkman drive/i)
  assert.equal(
    backupFailureMessage({ status: 405, message: '405 Method Not Allowed' }),
    fallback,
  )
})

test('404 No Walkman detected is a live-API failure, not the hand-copy fallback', () => {
  const msg = backupFailureMessage({
    status: 404,
    message: '404 Not Found — {"detail":"No Walkman detected"}',
  })
  assert.match(msg, /^Backup failed:/)
  assert.doesNotMatch(msg, /copy the whole Walkman drive/i)
})

test('other backup errors surface the API message', () => {
  assert.equal(
    backupFailureMessage({ status: 400, message: '400 Bad Request — dest on device' }),
    'Backup failed: 400 Bad Request — dest on device',
  )
})
