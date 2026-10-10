const { test } = require('node:test')
const assert = require('node:assert/strict')
const crypto = require('node:crypto')
const { ownOrigin, verifyAnnouncement, drainUntilIdle } = require('./boundary.cjs')

test('origin restriction includes port, scheme and hostname', () => {
  const origin = 'http://127.0.0.1:1234'
  assert.equal(ownOrigin(origin + '/api/queue', origin), true)
  for (const url of ['http://127.0.0.1:1235', 'http://localhost:1234', 'https://127.0.0.1:1234', 'https://evil.test']) assert.equal(ownOrigin(url, origin), false)
})
test('startup port announcement is authenticated', () => {
  const proof = crypto.createHmac('sha256', 'secret').update('4567').digest('hex')
  assert.equal(verifyAnnouncement(JSON.stringify({ port: 4567, proof }), 'secret'), 4567)
  assert.throws(() => verifyAnnouncement(JSON.stringify({ port: 4568, proof }), 'secret'))
})
test('drain stops playback then refuses admission and waits without a kill timeout', async () => {
  const calls = []; let count = 0
  await drainUntilIdle({ stopPlayback: async () => calls.push('stop'), request: async (method, route) => {
    calls.push(method + route)
    return { draining: true, busy: count++ < 3 }
  }, onWaiting: () => {}, delay: async () => {} })
  assert.equal(calls[0], 'stop')
  assert.equal(calls[1], 'POST/api/shutdown/drain')
  assert.equal(calls.length, 5)
})
test('probe failure or ambiguous state never means idle', async () => {
  for (const value of [null, { busy: false }, { busy: 'false', draining: true }]) {
    await assert.rejects(drainUntilIdle({ stopPlayback: async () => {}, request: async () => value, onWaiting: () => {} }))
  }
})
