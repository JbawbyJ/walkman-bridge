// Opt-in verification only: real UAC, real Defender, two generated harmless
// WAV files, and the production native exchange. The backend is a test fixture.
const { createScannerBroker } = require('../../electron/scanner-broker.cjs')
const fs = require('node:fs')
const path = require('node:path')
const crypto = require('node:crypto')
const assert = require('node:assert/strict')

const label = process.argv.find(value => value.startsWith('--label='))?.slice(8) || 'current'
assert.match(label, /^[a-z0-9-]+$/)
const root = path.resolve(__dirname, `../artifacts/real-uac-proof-${label}`)
fs.mkdirSync(root, { recursive: true })
const bytes = Buffer.alloc(44 + 16000)
bytes.write('RIFF'); bytes.writeUInt32LE(bytes.length - 8, 4); bytes.write('WAVEfmt ', 8); bytes.writeUInt32LE(16, 16); bytes.writeUInt16LE(1, 20); bytes.writeUInt16LE(1, 22); bytes.writeUInt32LE(8000, 24); bytes.writeUInt32LE(16000, 28); bytes.writeUInt16LE(2, 32); bytes.writeUInt16LE(16, 34); bytes.write('data', 36); bytes.writeUInt32LE(16000, 40)
const requests = ['one', 'two'].map(name => {
  const file = path.join(root, `${name}.wav`)
  fs.writeFileSync(file, bytes)
  return { request_id: crypto.randomUUID(), nonce: crypto.randomBytes(32).toString('hex'), media_id: crypto.randomBytes(16).toString('hex'), path: file, sha256: crypto.createHash('sha256').update(bytes).digest('hex'), size_bytes: bytes.length }
})
const results = [], claims = [], cancelled = [], logs = []
const broker = createScannerBroker({
  baseUrl: 'http://127.0.0.1:8123', token: 'test-only-native-token',
  helperPath: path.resolve(__dirname, '../artifacts/win-x64/RedLotus.ScanHelper.exe'), cacheRoot: root,
  helperTimeoutMs: 180000, log: message => { logs.push(message); console.log(message) },
  fetchImpl: async (url, init) => {
    const body = init.body ? JSON.parse(init.body) : null
    if (url.endsWith('/claim')) claims.push(body)
    if (url.endsWith('/result')) results.push(body)
    if (url.endsWith('/cancel')) cancelled.push(body)
    return { ok: true, status: 200, json: async () => url.endsWith('/pending') ? requests : { ok: true } }
  },
})
broker.pollOnce().then(() => {
  const summary = {
    uses_real_defender: true, actual_defender_results: results.length, requested_files: requests.length, claimed_files: claims.length,
    helper_pids: [...new Set(claims.map(claim => claim.helper_pid))],
    results: results.map(({ ok, state, reason_code, defender_status, defender_engine_version, defender_signature_version, defender_platform_version, exit_code }) => ({ ok, state, reason_code, defender_status, defender_engine_version, defender_signature_version, defender_platform_version, exit_code })),
    cancellations: cancelled.map(({ reason_code, detail }) => ({ reason_code, detail })), logs,
  }
  fs.writeFileSync(path.join(root, 'real-uac-smoke.json'), JSON.stringify(summary, null, 2))
  assert.equal(claims.length, 2)
  assert.equal(summary.helper_pids.length, 1)
  assert.equal(results.length, 2)
  assert.ok(results.every(result => result.ok === true && result.defender_status === 'clean'))
  assert.equal(cancelled.length, 0)
  console.log('Real UAC smoke PASS: both harmless WAVs cleared by one authenticated elevated helper.')
}).catch(error => { console.error(error); process.exitCode = 1 })
