'use strict'
const test = require('node:test')
const assert = require('node:assert/strict')
const { spawnSync } = require('node:child_process')
const path = require('node:path')

test('installed smoke cleanup preserves unrelated reused PIDs', t => {
  const shell = 'pwsh'
  const result = spawnSync(shell, ['-NoProfile', '-File', path.join(__dirname, 'smoke-install.ownership.test.ps1')], {
    encoding: 'utf8', windowsHide: true, timeout: 10000,
  })
  if (result.error?.code === 'ENOENT' && process.platform !== 'win32') return t.skip('PowerShell is not installed')
  assert.ifError(result.error)
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`)
})
