// node --test wrapper for the Electron zoom matrix.
// Linux runs the harness under xvfb. Windows launches Electron directly.
'use strict'
const assert = require('node:assert/strict')
const { spawn, spawnSync } = require('node:child_process')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const root = path.resolve(__dirname, '..')
const profile = path.join(root, 'frontend', 'test-output', 'zoom-matrix', 'profile')

function removeProfile() {
  for (let attempt = 0; attempt < 10; attempt++) {
    try {
      fs.rmSync(profile, { recursive: true, force: true })
      return
    } catch { /* The process may still be releasing the profile directory. */ }
    Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 50)
  }
}

function killChild(child) {
  if (!child || child.pid == null || child.exitCode != null || child.signalCode != null) return
  if (process.platform === 'win32') {
    const result = spawnSync('taskkill', ['/T', '/F', '/PID', String(child.pid)], { stdio: 'ignore' })
    if (result.error || result.status !== 0) {
      try { child.kill() } catch { /* already gone */ }
    }
    return
  }
  try { process.kill(-child.pid, 'SIGKILL') }
  catch {
    try { child.kill('SIGKILL') } catch { /* already gone */ }
  }
}

// killChild returns once the leader has exited. A normal exit can still leave
// Electron helpers in the detached group, so the finally block sweeps them too.
function sweepChildGroup(child) {
  if (!child || child.pid == null) return
  if (process.platform === 'win32') {
    try {
      process.kill(child.pid, 0)
    } catch (error) {
      if (error && error.code === 'ESRCH') return
    }
    spawnSync('taskkill', ['/T', '/F', '/PID', String(child.pid)], { stdio: 'ignore' })
    return
  }
  try {
    process.kill(-child.pid, 'SIGKILL')
  } catch (error) {
    if (!error || error.code !== 'ESRCH') throw error
  }
}

function electronBinary() {
  const saved = process.env.ELECTRON_RUN_AS_NODE
  delete process.env.ELECTRON_RUN_AS_NODE
  const electronPath = require('electron')
  if (saved === undefined) delete process.env.ELECTRON_RUN_AS_NODE
  else process.env.ELECTRON_RUN_AS_NODE = saved
  assert.equal(typeof electronPath, 'string', 'require("electron") did not return the binary path')
  assert.ok(fs.existsSync(electronPath), `Electron binary is missing at ${electronPath}. Run npm ci at the repository root.`)
  return electronPath
}

test('Electron zoom matrix keeps zoomed layouts inside the viewport', { timeout: 180000 }, async (t) => {
  const dist = path.join(root, 'frontend', 'dist', 'index.html')
  assert.ok(fs.existsSync(dist), 'frontend/dist is missing. npm run test:zoom-matrix builds it first.')
  const electronPath = electronBinary()
  const harness = path.join(root, 'frontend', 'tests', 'zoom-matrix.cjs')
  const env = { ...process.env }
  delete env.ELECTRON_RUN_AS_NODE
  const electronArgs = ['--disable-gpu', '--disable-dev-shm-usage', harness]
  let command = electronPath
  let args = electronArgs
  if (process.platform === 'linux') {
    command = 'xvfb-run'
    args = ['-a', '-s', '-screen 0 1920x1400x24', electronPath, ...electronArgs]
  }
  const posix = process.platform !== 'win32'
  const child = spawn(command, args, {
    cwd: root,
    env,
    stdio: ['ignore', 'inherit', 'inherit'],
    detached: posix,
  })
  const onAbort = () => killChild(child)
  const onSignal = (signal) => {
    killChild(child)
    removeProfile()
    process.exit(signal === 'SIGINT' ? 130 : 143)
  }
  t.signal.addEventListener('abort', onAbort)
  process.prependListener('SIGINT', onSignal)
  process.prependListener('SIGTERM', onSignal)
  try {
    const code = await new Promise((resolve, reject) => {
      let settled = false
      child.on('error', error => {
        if (settled) return
        settled = true
        reject(error)
      })
      child.on('exit', status => {
        if (settled) return
        settled = true
        resolve(status)
      })
    })
    const reportPath = path.join(root, 'frontend', 'test-output', 'zoom-matrix', 'report.json')
    assert.equal(code, 0, `zoom matrix exited ${code}. Report: ${reportPath}`)
    const report = JSON.parse(fs.readFileSync(reportPath, 'utf8'))
    assert.equal(report.failed, 0)
    assert.equal(report.errors.length, 0)
    assert.equal(report.passed, report.cases)
    assert.equal(report.cases, 2 * 7 * 4)
  } finally {
    t.signal.removeEventListener('abort', onAbort)
    process.removeListener('SIGINT', onSignal)
    process.removeListener('SIGTERM', onSignal)
    killChild(child)
    sweepChildGroup(child)
    removeProfile()
  }
})
