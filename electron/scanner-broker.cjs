'use strict'

const { spawn } = require('node:child_process')
const crypto = require('node:crypto')
const path = require('node:path')

const PATHS = Object.freeze({ pending: '/api/internal/scanner/pending', claim: '/api/internal/scanner/claim', result: '/api/internal/scanner/result', cancel: '/api/internal/scanner/cancel' })
const MAX_BATCH = 200
const MAX_MESSAGE = 2 * 1024 * 1024

function createScannerBroker(options) {
  if (!options || typeof options !== 'object') throw new TypeError('scanner broker options are required')
  const backend = new URL(options.baseUrl)
  if (backend.protocol !== 'http:' || !['127.0.0.1', 'localhost', '::1', '[::1]'].includes(backend.hostname)) throw new Error('scanner broker backend must be an HTTP loopback URL')
  if (!options.token || typeof options.token !== 'string') throw new Error('scanner broker native token is required')
  if (!options.helperPath || !options.cacheRoot) throw new Error('scanner helperPath and cacheRoot are required')
  const fetchImpl = options.fetchImpl || globalThis.fetch
  const launchHelper = options.launchHelper || defaultLaunchHelper
  const exchangePipe = options.exchangePipe || defaultExchangePipe
  const setIntervalFn = options.setIntervalFn || setInterval
  const clearIntervalFn = options.clearIntervalFn || clearInterval
  const log = typeof options.log === 'function' ? options.log : () => {}
  const isBackendAlive = options.isBackendAlive || (() => true)
  const lifetime = new AbortController()
  const active = new Set()
  let timer = null, polling = false, stopped = true

  function requireActive() {
    if (lifetime.signal.aborted || !isBackendAlive()) throw new Error('scanner broker backend connection is closed')
  }

  async function api(method, route, body) {
    requireActive()
    const init = { method, headers: { 'X-NightOps-Token': options.token, Accept: 'application/json' }, signal: AbortSignal.any([lifetime.signal, AbortSignal.timeout(5000)]) }
    if (body !== undefined) { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(body) }
    const response = await fetchImpl(new URL(route, backend).toString(), init)
    requireActive()
    if (!response.ok) throw new Error(`${route} returned ${response.status}`)
    const result = await response.json()
    requireActive()
    return result
  }
  async function cancel(request, reasonCode, detail) {
    try { await api('POST', PATHS.cancel, { request_id: request.request_id, nonce: request.nonce, reason_code: reasonCode, detail: String(detail || '').slice(0, 500) }) }
    catch (error) { log(`scanner cancel failed for ${request.request_id}: ${error.message}`) }
  }
  async function handleBatch(requests) {
    const admitted = []
    for (const request of requests) {
      if (!validRequest(request)) { log('scanner broker rejected malformed pending request'); continue }
      if (!isManagedPath(request.path, options.cacheRoot)) { await cancel(request, 'unmanaged_path', 'pending path is outside managed cache'); continue }
      admitted.push(request)
    }
    if (!admitted.length) return
    const pipeName = `\\\\.\\pipe\\NightOps.Scanner.${crypto.randomUUID()}`
    let helper
    try {
      requireActive()
      helper = await launchHelper({ helperPath: path.resolve(options.helperPath), cacheRoot: path.resolve(options.cacheRoot), pipeName, signal: lifetime.signal, timeoutMs: options.helperTimeoutMs || 30 * 60 * 1000 })
      requireActive()
      if (!helper || !Number.isSafeInteger(helper.pid) || helper.pid <= 0) throw new Error('native exchange did not return an authenticated helper process id')
      for (const request of admitted) await api('POST', PATHS.claim, { request_id: request.request_id, nonce: request.nonce, helper_pid: helper.pid })
      requireActive()
      const reply = await exchangePipe({ helper, helperPath: path.resolve(options.helperPath), pipeName, requests: admitted, request: admitted[0], expectedHelperPid: helper.pid, timeoutMs: options.helperTimeoutMs || 30 * 60 * 1000 })
      requireActive()
      const results = Array.isArray(reply?.results) ? reply.results : admitted.length === 1 ? [reply] : []
      if (results.length !== admitted.length) throw new Error('helper batch result count mismatch')
      const byId = new Map(results.map(result => [result?.request_id, result]))
      if (byId.size !== admitted.length) throw new Error('helper batch repeats a request identity')
      // Validate the entire batch before admitting any claimed clean result.
      for (const request of admitted) validateResult(request, byId.get(request.request_id), helper.pid)
      for (const request of admitted) await api('POST', PATHS.result, byId.get(request.request_id))
    } catch (error) {
      const cancelled = error && (error.code === 1223 || /elevation_cancelled|cancel(?:led|ed) by the user/i.test(error.message || ''))
      for (const request of admitted) await cancel(request, cancelled ? 'elevation_cancelled' : 'elevation_failed', error?.message)
      log(`scanner helper batch failed: ${error?.message}`)
    } finally { helper?.close?.() }
  }
  async function pollOnce() {
    if (polling || lifetime.signal.aborted || !isBackendAlive()) return
    polling = true
    try {
      const payload = await api('GET', PATHS.pending)
      const pending = Array.isArray(payload) ? payload : Array.isArray(payload?.requests) ? payload.requests : []
      const requests = pending.filter(request => request && !active.has(request.request_id)).slice(0, MAX_BATCH)
      for (const request of requests) active.add(request.request_id)
      try { await handleBatch(requests) } finally { for (const request of requests) active.delete(request.request_id) }
    } catch (error) { log(`scanner broker poll failed: ${error.message}`) }
    finally { polling = false }
  }
  async function start() {
    requireActive()
    if (timer !== null) return
    stopped = false
    await pollOnce()
    if (!stopped && isBackendAlive()) timer = setIntervalFn(() => { void pollOnce() }, options.pollIntervalMs || 1000)
  }
  function stop() { stopped = true; lifetime.abort(new Error('scanner broker stopped')); if (timer !== null) clearIntervalFn(timer); timer = null }
  return Object.freeze({ start, stop, pollOnce })
}

function validRequest(request) {
  return !!(request && typeof request.request_id === 'string' && request.request_id.length >= 1 && request.request_id.length <= 128 && typeof request.nonce === 'string' && request.nonce.length >= 32 && request.nonce.length <= 256 &&
    /^[a-f0-9]{32}$/i.test(request.media_id || '') && typeof request.path === 'string' && request.path.length <= 4096 &&
    /^[a-f0-9]{64}$/i.test(request.sha256 || '') && Number.isSafeInteger(request.size_bytes) && request.size_bytes >= 0)
}
function isManagedPath(candidate, root) {
  // Cache and device paths are Windows paths. path.win32 keeps drive-letter
  // classification identical on every host.
  const windows = path.win32
  const relative = windows.relative(windows.resolve(root), windows.resolve(candidate))
  return relative !== '' && !relative.startsWith(`..${windows.sep}`) && relative !== '..' && !windows.isAbsolute(relative)
}
function same(left, right) {
  const a = Buffer.from(String(left)), b = Buffer.from(String(right))
  return a.length === b.length && crypto.timingSafeEqual(a, b)
}
function validateResult(request, result, helperPid) {
  if (!result || typeof result !== 'object') throw new Error('helper returned no result')
  for (const key of ['request_id', 'nonce', 'media_id', 'path', 'sha256']) if (!same(request[key], result[key])) throw new Error(`helper result ${key} mismatch`)
  if (request.size_bytes !== result.size_bytes) throw new Error('helper result size mismatch')
  if (result.helper_pid !== helperPid) throw new Error('helper process mismatch')
  if (result.ok === true) {
    if (result.state !== 'CLEAN' || result.reason_code !== 'clean' || result.defender_status !== 'clean' || result.exit_code !== 0) throw new Error('helper did not return explicit Defender clearance')
    if (!result.defender_engine_version || !result.defender_signature_version || !result.defender_platform_version || !result.scanned_at) throw new Error('helper clearance is missing Defender signature status')
  } else if (result.state === 'CLEAN') throw new Error('failed helper result cannot be clean')
}
function sanitizedEnvironment(source) {
  const env = {}
  for (const [name, value] of Object.entries(source)) {
    if (!/^(DOTNET_|COMPlus_|CORECLR_|COR_)/i.test(name) && !/^PSModulePath$/i.test(name)) env[name] = value
  }
  env.DOTNET_EnableDiagnostics = '0'
  env.DOTNET_EnableDiagnostics_IPC = '0'
  env.DOTNET_EnableDiagnostics_Debugger = '0'
  env.DOTNET_EnableDiagnostics_Profiler = '0'
  env.CORECLR_ENABLE_PROFILING = '0'
  env.COR_ENABLE_PROFILING = '0'
  return env
}

// The native --exchange process owns elevation, retains the actual helper
// process handle, and verifies GetNamedPipeServerProcessId before it requests
// any nonce from stdin. No Node named-pipe socket can bypass that check.
function defaultLaunchHelper({ helperPath, cacheRoot, pipeName, timeoutMs, signal }) {
  return new Promise((resolve, reject) => {
    const child = spawn(helperPath, ['--exchange', '--pipe', pipeName, '--cache-root', cacheRoot], {
      cwd: path.dirname(helperPath), windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'], env: sanitizedEnvironment(process.env), signal,
    })
    let buffer = '', stderr = '', launched = false, settled = false
    let responseResolve, responseReject
    const resultPromise = new Promise((yes, no) => { responseResolve = yes; responseReject = no })
    // A launcher failure may happen before the caller asks for the result.
    resultPromise.catch(() => {})
    const timer = setTimeout(() => { fail(new Error('native scanner exchange timed out')); child.kill() }, timeoutMs)
    const fail = error => { if (settled) return; settled = true; clearTimeout(timer); reject(error); responseReject(error) }
    child.stdout.setEncoding('utf8')
    child.stdout.on('data', chunk => {
      buffer += chunk
      if (buffer.length > MAX_MESSAGE) { fail(new Error('native scanner response exceeded limit')); child.kill(); return }
      let newline
      while ((newline = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, newline).trim(); buffer = buffer.slice(newline + 1)
        if (!line) continue
        let value
        try { value = JSON.parse(line) } catch { fail(new Error('native scanner returned malformed JSON')); return }
        if (!launched) {
          if (!Number.isSafeInteger(value.helper_pid) || value.helper_pid <= 0) { fail(new Error('native scanner returned invalid helper identity')); return }
          launched = true
          resolve({ pid: value.helper_pid, pipeName, exchange: requests => {
            const payload = JSON.stringify({ requests: requests.map(request => ({ ...request, expected_helper_pid: value.helper_pid })) })
            if (Buffer.byteLength(payload) > MAX_MESSAGE) throw new Error('scanner request batch exceeded limit')
            child.stdin.end(`${payload}\n`)
            return resultPromise
          }, close: () => child.stdin.end() })
        } else {
          if (!Array.isArray(value.results)) { fail(new Error('native scanner returned invalid result batch')); return }
          settled = true; clearTimeout(timer); responseResolve(value)
        }
      }
    })
    child.stderr.on('data', chunk => { stderr = `${stderr}${chunk}`.slice(-4096) })
    child.stdin.on('error', error => fail(error))
    child.on('error', error => fail(error))
    child.on('exit', code => {
      if (settled) return
      const error = new Error(stderr.trim() || `native scanner exchange exited ${code}`)
      if (code === 4 || /elevation_cancelled/.test(error.message)) error.code = 1223
      fail(error)
    })
  })
}
async function defaultExchangePipe({ helper, requests }) {
  if (typeof helper?.exchange !== 'function') throw new Error('authenticated native scanner exchange is unavailable')
  return helper.exchange(requests)
}
module.exports = { createScannerBroker, PATHS, sanitizedEnvironment }
