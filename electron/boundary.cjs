'use strict'
const crypto = require('node:crypto')

function ownOrigin(url, origin) {
  try { return new URL(url).origin === origin } catch { return false }
}
function validSender(event, window, origin) {
  return !!window && !window.isDestroyed() && event.sender === window.webContents &&
    event.senderFrame === window.webContents.mainFrame && ownOrigin(event.senderFrame.url, origin)
}
function verifyAnnouncement(line, token) {
  const body = JSON.parse(line)
  if (!Number.isInteger(body.port) || body.port < 1024 || body.port > 65535) throw new Error('Invalid backend port')
  const expected = crypto.createHmac('sha256', token).update(String(body.port)).digest('hex')
  if (typeof body.proof !== 'string' || body.proof.length !== expected.length ||
      !crypto.timingSafeEqual(Buffer.from(body.proof), Buffer.from(expected))) throw new Error('Backend identity mismatch')
  return body.port
}
async function drainUntilIdle({ stopPlayback, request, onWaiting, delay = ms => new Promise(r => setTimeout(r, ms)) }) {
  await stopPlayback()
  await request('POST', '/api/shutdown/drain')
  for (;;) {
    const state = await request('GET', '/api/engine-busy')
    if (state.draining !== true || typeof state.busy !== 'boolean') throw new Error('Unknown shutdown status')
    if (!state.busy) return
    onWaiting(state)
    await delay(1000)
  }
}
module.exports = { ownOrigin, validSender, verifyAnnouncement, drainUntilIdle }
