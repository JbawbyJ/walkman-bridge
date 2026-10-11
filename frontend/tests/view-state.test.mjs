import { register } from 'node:module'
import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { JSDOM } from 'jsdom'

await register(new URL('./jsx-loader.mjs', import.meta.url))
const { ViewState, VIEW_STATE_COPY } = await import('../src/components/ViewState.jsx')

function render(props) {
  const html = renderToStaticMarkup(createElement(ViewState, props))
  return new JSDOM(`<!doctype html><body>${html}</body>`).window.document.body.firstElementChild
}

test('shared state component renders empty, loading, and error with one pattern', () => {
  const samples = {
    empty: {
      variant: 'empty',
      icon: 'plus',
      title: 'Nothing staged yet',
      message: 'Add music to your local queue, then select cleared files for your Walkman.',
    },
    loading: {
      variant: 'loading',
      icon: 'disc',
      title: 'Loading your queue',
      message: 'Your listening queue will appear here.',
    },
    error: {
      variant: 'error',
      icon: 'usb',
      title: 'Device writes are blocked',
      message: 'A playlist on this Walkman references a track that is missing.',
      onDismiss() {},
    },
  }
  const nodes = Object.fromEntries(Object.entries(samples).map(([key, props]) => [key, render(props)]))

  for (const [variant, node] of Object.entries(nodes)) {
    assert.ok(node.classList.contains('view-state'), variant)
    assert.ok(node.classList.contains(`view-state-${variant}`), variant)
    assert.equal(node.querySelectorAll('.view-state-copy').length, 1, variant)
    assert.equal(node.querySelector('.view-state-copy > strong')?.textContent, samples[variant].title, variant)
    assert.equal(node.querySelector('.view-state-copy > p')?.textContent, samples[variant].message, variant)
    assert.equal(node.querySelector('svg')?.getAttribute('aria-hidden'), 'true', variant)
  }

  assert.equal(nodes.empty.getAttribute('role'), 'status')
  assert.equal(nodes.empty.getAttribute('aria-live'), null)
  assert.ok(nodes.empty.classList.contains('empty-state'))
  assert.equal(nodes.empty.classList.contains('playlist-failure'), false)
  assert.equal(nodes.empty.querySelector('button'), null)

  assert.equal(nodes.loading.getAttribute('role'), 'status')
  assert.equal(nodes.loading.getAttribute('aria-live'), null)
  assert.ok(nodes.loading.classList.contains('empty-state'))
  assert.equal(nodes.loading.classList.contains('playlist-failure'), false)

  assert.equal(nodes.error.getAttribute('role'), 'alert')
  assert.equal(nodes.error.getAttribute('aria-live'), 'assertive')
  assert.ok(nodes.error.classList.contains('playlist-failure'))
  assert.equal(nodes.error.classList.contains('empty-state'), false)
  assert.equal(nodes.error.querySelector('button.view-state-dismiss')?.textContent, 'Dismiss')

  const playback = render({
    variant: 'error',
    role: 'status',
    icon: 'disc',
    title: 'Playback needs attention',
    message: 'The file could not be read.',
  })
  assert.ok(playback.classList.contains('view-state'))
  assert.ok(playback.classList.contains('view-state-error'))
  assert.ok(playback.classList.contains('playback-error'))
  assert.equal(playback.classList.contains('playlist-failure'), false)
  assert.equal(playback.getAttribute('role'), 'status')
  assert.equal(playback.getAttribute('aria-live'), null)
  assert.equal(playback.querySelector('.view-state-copy > strong')?.textContent, 'Playback needs attention')
  assert.equal(playback.querySelector('.view-state-copy > p')?.textContent, 'The file could not be read.')
})

test('listening, walkman, and transfer state copy shares one tone', () => {
  const entries = []
  for (const [view, groups] of Object.entries(VIEW_STATE_COPY)) {
    for (const [name, copy] of Object.entries(groups)) entries.push({ view, name, ...copy })
  }
  assert.deepEqual(Object.keys(VIEW_STATE_COPY).sort(), ['listening', 'transfer', 'walkman'])
  assert.ok(entries.length >= 9)
  for (const copy of entries) {
    assert.match(copy.title, /^[A-Z][^!]*[^.!?…]$/, `${copy.view}.${copy.name} title`)
    if (copy.message) assert.match(copy.message, /^[A-Z].*\.$/, `${copy.view}.${copy.name} message`)
  }
  const loading = entries.filter(copy => copy.name === 'loading')
  assert.equal(loading.length, 3)
  for (const copy of loading) assert.match(copy.title, /^Loading /)
})

test('night ops state styles use the shared spacing, type, and radius tokens', () => {
  const css = fs.readFileSync(new URL('../src/nightops.css', import.meta.url), 'utf8')
  const tokens = [
    '--night-space-2xs',
    '--night-space-xs',
    '--night-space-sm',
    '--night-space-md',
    '--night-space-lg',
    '--night-space-xl',
    '--night-space-2xl',
    '--night-font-micro',
    '--night-font-caption',
    '--night-font-body',
    '--night-font-title',
    '--night-font-display',
    '--night-font-hero',
    '--night-weight-regular',
    '--night-weight-medium',
    '--night-weight-strong',
    '--night-leading-tight',
    '--night-leading-body',
    '--night-leading-relaxed',
    '--night-radius-control',
    '--night-radius-panel',
  ]
  for (const token of tokens) assert.match(css, new RegExp(`${token}:`))
  const state = css.slice(css.indexOf('.view-state{'))
  assert.notEqual(state, '')
  assert.match(state, /\.view-state\{[^}]*var\(--night-space-/)
  assert.match(state, /\.view-state\{[^}]*var\(--night-radius-panel\)/)
  assert.match(state, /\.view-state strong\{[^}]*var\(--night-font-title\)/)
  assert.match(state, /\.view-state strong\{[^}]*var\(--night-weight-medium\)/)
  assert.match(state, /\.view-state strong\{[^}]*var\(--night-leading-tight\)/)
  assert.match(state, /\.view-state p\{[^}]*var\(--night-font-caption\)/)
  assert.match(state, /\.view-state p\{[^}]*var\(--night-leading-relaxed\)/)
  assert.match(state, /\.view-state-error\{[^}]*var\(--night-space-/)
  assert.match(state, /\.view-state-dismiss\{[^}]*var\(--night-radius-control\)/)
})
