'use strict'
// Run after building both products. Keep test-only scanner entry points out of releases.
const assert = require('node:assert/strict')
const crypto = require('node:crypto')
const fs = require('node:fs')
const path = require('node:path')
const asar = require('@electron/asar')

const root = path.resolve(__dirname, '..')
const modules = ['boundary.cjs', 'main.cjs', 'preload.cjs', 'scanner-broker.cjs']
const expected = [...modules.map(name => `electron/${name}`), 'package.json'].sort()
const digest = bytes => crypto.createHash('sha256').update(bytes).digest('hex')
const products = []
for (const product of ['bridge', 'player']) {
  const archive = path.join(root, 'dist_electron', product, 'win-unpacked/resources/app.asar')
  const names = asar.listPackage(archive).map(name => name.replaceAll('\\', '/').replace(/^\//, ''))
    .filter(name => !asar.statFile(archive, name).files).sort()
  assert.deepEqual(names, expected, `${product}: unexpected code, test harness or data in application archive`)
  const sourceHashes = {}
  for (const name of modules) {
    const relative = `electron/${name}`
    const packaged = asar.extractFile(archive, relative)
    const source = fs.readFileSync(path.join(root, relative))
    assert.equal(digest(packaged), digest(source), `${product}: packaged ${relative} differs from verified source`)
    sourceHashes[relative] = digest(packaged)
  }
  products.push({ product, archive, files: names, sha256: sourceHashes, passed: true })
}
const output = path.join(root, 'packaging/build/asar-proof.json')
fs.mkdirSync(path.dirname(output), { recursive: true })
fs.writeFileSync(output, JSON.stringify({ checked_at: new Date().toISOString(), products }, null, 2) + '\n')
console.log('Both application archives contain only the verified production entry points')
