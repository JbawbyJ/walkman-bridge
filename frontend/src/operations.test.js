import test from 'node:test'
import assert from 'node:assert/strict'
import { processingBusy } from './playback.js'

test('playback keeps shutdown busy without blocking imports or queue actions', () => {
  assert.equal(processingBusy({ busy: true, active: [{ kind: 'playback_session' }, { kind: 'playback' }] }), false)
  assert.equal(processingBusy({ busy: true, active: [{ kind: 'playback_session' }, { kind: 'import' }] }), true)
  assert.equal(processingBusy({ busy: false, draining: true, active: [] }), true)
  assert.equal(processingBusy({ busy: true }), true)
})
