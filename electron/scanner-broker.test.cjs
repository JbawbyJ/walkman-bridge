'use strict'

const assert = require('node:assert/strict')
const test = require('node:test')
const { createScannerBroker, sanitizedEnvironment } = require('./scanner-broker.cjs')

const REQUEST = {
  request_id: 'req-1', nonce: 'secret-nonce'.repeat(3), media_id: '0123456789abcdef0123456789abcdef',
  path: 'C:\\cache\\0123456789abcdef0123456789abcdef\\source.mp3',
  sha256: 'a'.repeat(64), size_bytes: 123, created_at: 100,
}

function response(body, status = 200) {
  return { ok: status >= 200 && status < 300, status, async json() { return body }, async text() { return JSON.stringify(body) } }
}

// Resolves like a backend route that answers after ms, and rejects on abort as fetch does.
function answerAfter(ms, signal, value) {
  return new Promise((resolve, reject) => {
    if (signal.aborted) { reject(signal.reason); return }
    const timer = setTimeout(resolve, ms, value)
    signal.addEventListener('abort', () => { clearTimeout(timer); reject(signal.reason) }, { once: true })
  })
}

test('start polls immediately and stop clears recurring poll', async () => {
  let fetched = 0
  let scheduled
  let cleared
  const broker = createScannerBroker({
    baseUrl: 'http://127.0.0.1:8123', token: 'native-secret', helperPath: 'C:\\app\\ScannerHelper.exe',
    cacheRoot: 'C:\\cache', fetchImpl: async () => { fetched += 1; return response([]) },
    setIntervalFn(fn, ms) { scheduled = { fn, ms }; return 77 },
    clearIntervalFn(id) { cleared = id },
  })
  await broker.start()
  assert.equal(fetched, 1)
  assert.equal(scheduled.ms, 1000)
  broker.stop()
  assert.equal(cleared, 77)
})

test('claims helper pid, exchanges bound request, and submits explicit clean result', async () => {
  const calls = []
  let exchanged
  const fetchImpl = async (url, init = {}) => {
    calls.push({ url, init })
    assert.equal(init.headers['X-NightOps-Token'], 'native-secret')
    if (url.endsWith('/pending')) return response([REQUEST])
    if (url.endsWith('/claim')) return response(REQUEST)
    if (url.endsWith('/result')) return response({ accepted: true })
    throw new Error(`unexpected ${url}`)
  }
  const broker = createScannerBroker({
    baseUrl: 'http://127.0.0.1:8123', token: 'native-secret', helperPath: 'C:\\app\\ScannerHelper.exe',
    cacheRoot: 'C:\\cache', fetchImpl, launchHelper: async (options) => ({ pid: 4321, pipeName: options.pipeName }),
    exchangePipe: async (options) => {
      exchanged = options
      return {
        ...options.request, helper_pid: 4321, ok: true, state: 'CLEAN', reason: 'clean',
        reason_code: 'clean', defender_status: 'clean', defender_engine_version: '1',
        defender_signature_version: '2', defender_platform_version: '3', exit_code: 0,
        scanned_at: 200,
      }
    },
    setIntervalFn: () => 1, clearIntervalFn: () => {},
  })
  await broker.pollOnce()
  assert.equal(exchanged.request.nonce, REQUEST.nonce)
  assert.equal(exchanged.expectedHelperPid, 4321)
  assert.deepEqual(calls.map((call) => new URL(call.url).pathname), [
    '/api/internal/scanner/pending', '/api/internal/scanner/claim', '/api/internal/scanner/result',
  ])
  assert.deepEqual(JSON.parse(calls[1].init.body), { request_id: 'req-1', nonce: REQUEST.nonce, helper_pid: 4321 })
})

test('tampered helper result is cancelled and never submitted', async () => {
  const paths = []
  const fetchImpl = async (url) => {
    paths.push(new URL(url).pathname)
    if (url.endsWith('/pending')) return response([REQUEST])
    return response({ ok: true })
  }
  const broker = createScannerBroker({
    baseUrl: 'http://127.0.0.1:8123', token: 'native-secret', helperPath: 'C:\\app\\ScannerHelper.exe', cacheRoot: 'C:\\cache',
    fetchImpl, launchHelper: async (options) => ({ pid: 4321, pipeName: options.pipeName }),
    exchangePipe: async () => ({ ...REQUEST, nonce: 'tampered', helper_pid: 4321, ok: true, state: 'CLEAN', reason_code: 'clean', defender_status: 'clean', exit_code: 0 }),
    setIntervalFn: () => 1, clearIntervalFn: () => {},
  })
  await broker.pollOnce()
  assert.equal(paths.includes('/api/internal/scanner/result'), false)
  assert.equal(paths.at(-1), '/api/internal/scanner/cancel')
})

test('operator UAC cancellation posts fail-closed cancellation', async () => {
  const requests = []
  const broker = createScannerBroker({
    baseUrl: 'http://127.0.0.1:8123', token: 'native-secret', helperPath: 'C:\\app\\ScannerHelper.exe', cacheRoot: 'C:\\cache',
    fetchImpl: async (url, init = {}) => {
      requests.push({ url, init })
      return response(url.endsWith('/pending') ? [REQUEST] : {})
    },
    launchHelper: async () => { const error = new Error('The operation was canceled by the user'); error.code = 1223; throw error },
    setIntervalFn: () => 1, clearIntervalFn: () => {},
  })
  await broker.pollOnce()
  const cancel = requests.find((request) => request.url.endsWith('/cancel'))
  assert.equal(JSON.parse(cancel.init.body).reason_code, 'elevation_cancelled')
})

test('rejects non-loopback backend URL and empty native token', () => {
  assert.throws(() => createScannerBroker({ baseUrl: 'http://example.com', token: 'x', helperPath: 'x', cacheRoot: 'x' }), /loopback/)
  assert.throws(() => createScannerBroker({ baseUrl: 'http://127.0.0.1', token: '', helperPath: 'x', cacheRoot: 'x' }), /token/)
})

test('batches up to 200 requests into one elevation and validates every result', async () => {
  let launches = 0, batchSize = 0
  const submitted = []
  const requests = Array.from({ length: 201 }, (_, index) => ({ ...REQUEST, request_id: `request-${index}` }))
  const broker = createScannerBroker({
    baseUrl: 'http://127.0.0.1:8123', token: 'native-secret', helperPath: 'C:\\app\\helper.exe', cacheRoot: 'C:\\cache',
    fetchImpl: async (url, init) => {
      if (url.endsWith('/pending')) return response(requests)
      if (url.endsWith('/result')) submitted.push(JSON.parse(init.body))
      return response({ ok: true })
    },
    launchHelper: async () => { launches++; return { pid: 4321 } },
    exchangePipe: async ({ requests: batch }) => { batchSize = batch.length; return { results: batch.map(request => ({ ...request, helper_pid: 4321, ok: false, state: 'ERROR', reason_code: 'defender_unavailable' })) } },
  })
  await broker.pollOnce()
  assert.equal(launches, 1)
  assert.equal(batchSize, 200)
  assert.equal(submitted.length, 200)
})

test('malformed batch cancels all files before forwarding any clean result', async () => {
  const paths = []
  const requests = [REQUEST, { ...REQUEST, request_id: 'second' }]
  const broker = createScannerBroker({
    baseUrl: 'http://127.0.0.1:8123', token: 'native-secret', helperPath: 'C:\\app\\helper.exe', cacheRoot: 'C:\\cache',
    fetchImpl: async url => { paths.push(url); return response(url.endsWith('/pending') ? requests : {}) },
    launchHelper: async () => ({ pid: 4321 }),
    exchangePipe: async () => ({ results: [{ ...REQUEST, helper_pid: 4321, ok: false, state: 'ERROR' }] }),
  })
  await broker.pollOnce()
  assert.equal(paths.filter(url => url.endsWith('/result')).length, 0)
  assert.equal(paths.filter(url => url.endsWith('/cancel')).length, 2)
})

// Both tests wait out a real 5.5 s backend answer, so they run side by side.
test.describe('backend routes that wait behind a whole-file re-hash', { concurrency: true }, () => {
  const SLOW_HASH_MS = 5500

  test('a result POST taking longer than 5 s does not cancel the rest of its batch', async () => {
    const large = { ...REQUEST, request_id: 'large', media_id: 'f'.repeat(32), path: `C:\\cache\\${'f'.repeat(32)}\\large.flac`, size_bytes: 500 * 1024 * 1024 }
    const requests = [large, { ...REQUEST, request_id: 'sibling-1' }, { ...REQUEST, request_id: 'sibling-2' }]
    const accepted = [], cancelled = []
    const broker = createScannerBroker({
      baseUrl: 'http://127.0.0.1:8123', token: 'native-secret', helperPath: 'C:\\app\\helper.exe', cacheRoot: 'C:\\cache',
      fetchImpl: async (url, init) => {
        if (url.endsWith('/pending')) return response(requests)
        if (url.endsWith('/claim')) return response({})
        const body = JSON.parse(init.body)
        if (url.endsWith('/cancel')) { cancelled.push(body.request_id); return response({}) }
        const answer = body.request_id === 'large' ? await answerAfter(SLOW_HASH_MS, init.signal, response(body)) : response(body)
        accepted.push(body.request_id)
        return answer
      },
      launchHelper: async () => ({ pid: 4321 }),
      exchangePipe: async ({ requests: batch }) => ({ results: batch.map(request => ({
        ...request, helper_pid: 4321, ok: true, state: 'CLEAN', reason_code: 'clean', defender_status: 'clean',
        defender_engine_version: '1', defender_signature_version: '2', defender_platform_version: '3', exit_code: 0, scanned_at: 200,
      })) }),
    })
    await broker.pollOnce()
    assert.deepEqual(accepted, ['large', 'sibling-1', 'sibling-2'])
    assert.deepEqual(cancelled, [])
  })

  test('a cancel POST taking longer than 5 s still reaches the backend', async () => {
    const cancelled = [], logs = []
    const broker = createScannerBroker({
      baseUrl: 'http://127.0.0.1:8123', token: 'native-secret', helperPath: 'C:\\app\\helper.exe', cacheRoot: 'C:\\cache', log: line => logs.push(line),
      fetchImpl: async (url, init) => {
        if (url.endsWith('/pending')) return response([REQUEST])
        const answer = await answerAfter(SLOW_HASH_MS, init.signal, response({}))
        cancelled.push(JSON.parse(init.body).request_id)
        return answer
      },
      launchHelper: async () => { const error = new Error('The operation was canceled by the user'); error.code = 1223; throw error },
    })
    await broker.pollOnce()
    assert.deepEqual(cancelled, ['req-1'])
    assert.equal(logs.some(line => line.startsWith('scanner cancel failed')), false)
  })
})

test('launch environment strips every runtime hook/profiler prefix and module injection', () => {
  const result = sanitizedEnvironment({ Path: 'normal', DOTNET_STARTUP_HOOKS: 'evil.dll', dotnet_root: 'evil', COMPlus_ReadyToRun: '0', CORECLR_PROFILER_PATH: 'evil.dll', COR_PROFILER: 'evil', PSModulePath: 'evil', SystemRoot: 'C:\\Windows' })
  assert.equal(Object.values(result).includes('evil.dll'), false)
  assert.equal(Object.values(result).includes('evil'), false)
  assert.equal(result.Path, 'normal')
  assert.equal(result.DOTNET_EnableDiagnostics, '0')
  assert.equal(result.CORECLR_ENABLE_PROFILING, '0')
})

test('default transport refuses a launcher without an authenticated native exchange', async () => {
  const paths = []
  const broker = createScannerBroker({
    baseUrl: 'http://127.0.0.1:8123', token: 'native-secret', helperPath: 'C:\\app\\helper.exe', cacheRoot: 'C:\\cache',
    fetchImpl: async url => { paths.push(url); return response(url.endsWith('/pending') ? [REQUEST] : {}) },
    launchHelper: async () => ({ pid: 4321, pipeName: '\\\\.\\pipe\\untrusted' }),
  })
  await broker.pollOnce()
  assert.equal(paths.filter(url => url.endsWith('/result')).length, 0)
  assert.equal(paths.filter(url => url.endsWith('/cancel')).length, 1)
})

for (const stage of ['pending', 'helper', 'claim', 'exchange', 'result']) {
  test(`stop invalidates an in-flight ${stage} continuation without further IPC or HTTP`, async () => {
    let release, reached
    const paused = new Promise(resolve => { reached = resolve })
    const gate = new Promise(resolve => { release = resolve })
    const paths = []; let launches = 0, exchanges = 0, closed = 0, signal
    const pause = async value => { reached(); await gate; return value }
    const broker = createScannerBroker({
      baseUrl: 'http://127.0.0.1:8123', token: 'native-secret', helperPath: 'C:\\app\\helper.exe', cacheRoot: 'C:\\cache',
      fetchImpl: async (url, init) => {
        paths.push(url); signal = init.signal
        if (url.endsWith('/pending')) return stage === 'pending' ? pause(response([REQUEST])) : response([REQUEST])
        return url.endsWith(`/${stage}`) ? pause(response({})) : response({})
      },
      launchHelper: async () => { launches++; const helper = { pid: 4321, close() { closed++ } }; return stage === 'helper' ? pause(helper) : helper },
      exchangePipe: async () => { exchanges++; const result = { ...REQUEST, helper_pid: 4321, ok: false, state: 'ERROR' }; return stage === 'exchange' ? pause(result) : result },
    })
    const poll = broker.pollOnce()
    await paused
    const countAtStop = paths.length
    broker.stop()
    assert.equal(signal.aborted, true)
    release()
    await poll
    await broker.pollOnce()
    assert.equal(paths.length, countAtStop)
    assert.equal(launches, stage === 'pending' ? 0 : 1)
    assert.equal(exchanges, ['exchange', 'result'].includes(stage) ? 1 : 0)
    assert.equal(closed, stage === 'pending' ? 0 : 1)
  })
}
