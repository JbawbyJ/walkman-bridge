'use strict'

// Manual Windows-only smoke: launches the actual helper through UAC and scans
// one harmless managed fixture. It never runs from the ordinary test suite.
const crypto = require('node:crypto')
const fs = require('node:fs')
const path = require('node:path')
const { createScannerBroker } = require('./scanner-broker.cjs')

async function main() {
  const root = path.resolve(__dirname, '..')
  const cacheRoot = path.join(root, 'backend', '.test-tmp')
  const fixture = path.join(cacheRoot, 'real-defender.flac')
  const helperPath = path.join(root, 'scanner-helper', 'artifacts', 'win-x64', 'RedLotus.ScanHelper.exe')
  fs.mkdirSync(cacheRoot, { recursive: true })
  fs.writeFileSync(fixture, Buffer.from('fLaC-HARMLESS-RED-LOTUS-DEFENDER-INTEGRATION-CHECK\n'))
  process.once('exit', () => {
    try { fs.unlinkSync(fixture) } catch {}
    try { fs.rmdirSync(cacheRoot) } catch {}
  })
  const bytes = fs.readFileSync(fixture)
  const pending = [{
    request_id: crypto.randomUUID(), nonce: crypto.randomBytes(32).toString('base64url'),
    media_id: '0123456789abcdef0123456789abcdef', path: fixture,
    sha256: crypto.createHash('sha256').update(bytes).digest('hex'), size_bytes: bytes.length,
    created_at: Date.now() / 1000,
  }]
  let terminal = null
  const fetchImpl = async (url, init = {}) => {
    const route = new URL(url).pathname
    let body
    if (route.endsWith('/pending')) body = pending.splice(0)
    else if (route.endsWith('/claim')) body = { accepted: true }
    else if (route.endsWith('/result')) { terminal = JSON.parse(init.body); body = { accepted: true } }
    else if (route.endsWith('/cancel')) { terminal = { ...JSON.parse(init.body), ok: false }; body = { accepted: true } }
    else return response({}, 404)
    return response(body)
  }
  const broker = createScannerBroker({
    baseUrl: 'http://127.0.0.1:1', token: 'uac-smoke-token', helperPath, cacheRoot,
    helperTimeoutMs: 45000, fetchImpl, log: (line) => process.stderr.write(`${line}\n`),
  })
  await broker.pollOnce()
  process.stdout.write(`${JSON.stringify(terminal)}\n`)
  if (!terminal || !terminal.ok || terminal.defender_status !== 'clean') process.exitCode = 1
}

function response(body, status = 200) {
  return { ok: status >= 200 && status < 300, status, async json() { return body }, async text() { return JSON.stringify(body) } }
}

main().catch((error) => { process.stderr.write(`${error.stack || error}\n`); process.exitCode = 1 })
