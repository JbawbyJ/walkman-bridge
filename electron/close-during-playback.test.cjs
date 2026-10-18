'use strict'

const test = require('node:test')
const assert = require('node:assert/strict')
const { spawn } = require('node:child_process')
const fs = require('node:fs')
const path = require('node:path')

const root = path.resolve(__dirname, '..')
const harness = path.join(__dirname, 'tests/close-during-playback.electron.cjs')
const proofPath = path.join(root, 'packaging/build/player-close-during-playback/proof.json')

function electronBinary() {
  const electronPath = require('electron')
  if (typeof electronPath !== 'string' || !fs.existsSync(electronPath)) throw new Error('Electron binary is not installed')
  return electronPath
}

function assertGone(pid) {
  assert.throws(() => process.kill(pid, 0), /ESRCH|no such process/i)
}

test('closing during playback exits the player process within a few seconds', async () => {
  fs.rmSync(path.dirname(proofPath), { recursive: true, force: true })
  const electronPath = electronBinary()
  const env = { ...process.env }
  delete env.ELECTRON_RUN_AS_NODE
  const linux = process.platform === 'linux'
  const command = linux ? 'xvfb-run' : electronPath
  const args = linux
    ? ['-a', '--server-args=-screen 0 1280x720x24', electronPath, harness, '--no-sandbox', '--disable-gpu']
    : [harness]
  const child = spawn(command, args, { cwd: root, env, stdio: ['ignore', 'pipe', 'pipe'] })
  let output = ''
  child.stdout.on('data', chunk => { output += chunk })
  child.stderr.on('data', chunk => { output += chunk })
  const exit = await new Promise(resolve => {
    const timer = setTimeout(() => resolve('timeout'), 8000)
    child.once('exit', (code, signal) => { clearTimeout(timer); resolve({ code, signal }) })
  })
  if (exit === 'timeout') {
    child.kill('SIGKILL')
    await new Promise(resolve => child.once('exit', resolve))
  }
  assert.notEqual(exit, 'timeout', `player process ${child.pid} was still running\n${output}`)
  assert.equal(exit.code, 0, `player exit ${exit.code} signal ${exit.signal}\n${output}`)
  assertGone(child.pid)
  const proof = JSON.parse(fs.readFileSync(proofPath, 'utf8'))
  assert.equal(proof.passed, true)
  assert.ok(proof.elapsed_ms < 4000, `close took ${proof.elapsed_ms}ms`)
  assert.ok(proof.checks.includes('close during playback stopped audio and quit'))
})
