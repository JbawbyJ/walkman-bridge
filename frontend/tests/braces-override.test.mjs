import { createRequire } from 'node:module'
import { test } from 'node:test'
import assert from 'node:assert/strict'

const require = createRequire(import.meta.url)
const braces = require('braces')
const micromatch = require('micromatch')

const nested = (open, close, count, body = 'a') => open.repeat(count) + body + close.repeat(count)

test('installed braces override is the depth-guarded 3.0.4 patch', () => {
  assert.equal(require('braces/package.json').version, '3.0.4')
})

test('ordinary brace expansion used by content globs still works', () => {
  assert.deepEqual(braces('a/{b,c}/d', { expand: true }), ['a/b/d', 'a/c/d'])
  assert.deepEqual(braces('{a,b,c}', { compile: true }), ['(a|b|c)'])
  assert.equal(micromatch.isMatch('src/App.jsx', 'src/**/*.{js,jsx}'), true)
  assert.equal(micromatch.isMatch('src/App.css', 'src/**/*.{js,jsx}'), false)
})

test('deeply nested patterns throw instead of exhausting the stack', () => {
  assert.throws(() => braces(nested('{', '}', 3000)), /maximum depth/)
  assert.throws(() => braces(nested('(', ')', 3000)), /maximum depth/)
  assert.throws(() => braces(nested('{', '}', 101, 'a')), /maximum depth/)
  assert.doesNotThrow(() => braces(nested('{', '}', 100, 'a')))
})

test('callers cannot raise the nesting cap, and direct ASTs are guarded', () => {
  assert.throws(() => braces(nested('{', '}', 101), { maxDepth: 100000 }), /maximum depth/)
  let node = { type: 'root', nodes: [] }
  let cursor = node
  for (let i = 0; i < 300; i++) {
    const child = { type: 'brace', nodes: [], ranges: 0, commas: 0, invalid: false }
    cursor.nodes.push(child)
    child.parent = cursor
    cursor = child
  }
  cursor.nodes.push({ type: 'text', value: 'a' })
  assert.throws(() => braces.compile(node), /maximum depth/)
  assert.throws(() => braces.expand(node), /maximum depth/)
  assert.throws(() => braces.stringify(node), /maximum depth/)
})
