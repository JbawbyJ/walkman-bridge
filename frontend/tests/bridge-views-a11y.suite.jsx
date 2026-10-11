import test from 'node:test'
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { JSDOM } from 'jsdom'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createRoot } from 'react-dom/client'
import { act } from 'react'
import { playlistFailureMessage } from '../src/playlistFailure.js'
import { ListeningView, Queue } from '../src/components/PlayerPanels.jsx'
import { DevicePanel, JobPanel, StagingPanel } from '../src/components/BridgePanels.jsx'
import App from '../src/App.jsx'

const require = createRequire(import.meta.url)
const axe = require('axe-core')

let reduceMotion = false

function installDom() {
  const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://127.0.0.1/', pretendToBeVisual: true })
  const { window } = dom
  window.matchMedia = query => ({
    matches: reduceMotion && String(query).includes('prefers-reduced-motion'),
    media: query,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
    dispatchEvent() { return false },
  })
  class FakeAudio {
    constructor() {
      this.listeners = new Map()
      this.volume = 1
      this.paused = true
      this.currentTime = 0
      this.duration = 0
      this.dataset = {}
      this.error = null
      this.preload = 'metadata'
    }
    addEventListener(name, fn) { this.listeners.set(name, fn) }
    removeEventListener(name) { this.listeners.delete(name) }
    pause() { this.paused = true }
    load() {}
    play() { this.paused = false; return Promise.resolve() }
    getAttribute() { return null }
    removeAttribute() {}
    set src(value) { this._src = value }
    get src() { return this._src || '' }
  }
  window.Audio = FakeAudio
  window.HTMLCanvasElement.prototype.getContext = () => ({
    clearRect() {}, beginPath() {}, moveTo() {}, lineTo() {}, stroke() {}, fillRect() {},
  })
  window.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} }
  window.requestAnimationFrame = callback => window.setTimeout(() => callback(Date.now()), 16)
  window.cancelAnimationFrame = id => window.clearTimeout(id)
  const globals = { window, document: window.document, navigator: window.navigator, HTMLElement: window.HTMLElement, SVGElement: window.SVGElement, Element: window.Element, Node: window.Node, DocumentFragment: window.DocumentFragment, MutationObserver: window.MutationObserver, ResizeObserver: window.ResizeObserver, requestAnimationFrame: window.requestAnimationFrame, cancelAnimationFrame: window.cancelAnimationFrame, localStorage: window.localStorage, Audio: FakeAudio, IS_REACT_ACT_ENVIRONMENT: true }
  for (const [key, value] of Object.entries(globals)) {
    const descriptor = Object.getOwnPropertyDescriptor(globalThis, key)
    if (descriptor && !descriptor.writable && !descriptor.configurable) continue
    Object.defineProperty(globalThis, key, { value, configurable: true, writable: true })
  }
  return dom
}

installDom()

function markup(element) {
  const html = renderToStaticMarkup(element)
  return new JSDOM(`<!doctype html><html lang="en"><head><title>Walkman Bridge</title></head><body><main>${html}</main></body></html>`).window.document
}

function accessibleName(element) {
  const doc = element.ownerDocument
  const labelledby = element.getAttribute('aria-labelledby')
  if (labelledby) {
    const text = labelledby.split(/\s+/).map(id => doc.getElementById(id)?.textContent || '').join(' ').replace(/\s+/g, ' ').trim()
    if (text) return text
  }
  const aria = element.getAttribute('aria-label')
  if (aria?.trim()) return aria.trim()
  if (element.id) {
    const explicit = doc.querySelector(`label[for="${CSS.escape(element.id)}"]`)
    if (explicit) return explicit.textContent.replace(/\s+/g, ' ').trim()
  }
  const wrapping = element.closest('label')
  if (wrapping) {
    const clone = wrapping.cloneNode(true)
    clone.querySelectorAll('input, select, textarea, button').forEach(node => node.remove())
    const text = clone.textContent.replace(/\s+/g, ' ').trim()
    if (text) return text
  }
  if (element.getAttribute('title')?.trim()) return element.getAttribute('title').trim()
  return (element.textContent || '').replace(/\s+/g, ' ').trim()
}

function named(document, role, name) {
  const selectors = {
    button: 'button, [role="button"]',
    slider: 'input[type="range"], [role="slider"]',
    textbox: 'input:not([type]), input[type="text"], input[type="search"]',
    combobox: 'select',
    checkbox: 'input[type="checkbox"]',
    meter: '[role="meter"]',
    status: '[role="status"]',
    alert: '[role="alert"]',
    region: '[role="region"], section[aria-label], section[aria-labelledby]',
  }
  return [...document.querySelectorAll(selectors[role] || `[role="${role}"]`)].find(element => accessibleName(element) === name || accessibleName(element).includes(name))
}

function assertNamedControls(document) {
  const controls = [...document.querySelectorAll('button, input, select, textarea, summary, progress, [role="slider"], [role="meter"]')]
  const unnamed = controls.filter(element => !element.hidden && !element.closest('[hidden]') && !accessibleName(element))
  assert.deepEqual(unnamed.map(element => element.outerHTML.slice(0, 180)), [])
}

function assertHeadingOrder(document) {
  const levels = [...document.querySelectorAll('h1,h2,h3,h4,h5,h6')].map(heading => Number(heading.tagName[1]))
  assert.equal(levels[0], 1, `first heading should be h1, got ${levels.join(',')}`)
  let previous = 0
  for (const level of levels) {
    assert.ok(level <= previous + 1, `heading levels skip: ${levels.join(',')}`)
    previous = level
  }
}

async function assertAxe(document) {
  const results = await axe.run(document.documentElement, {
    rules: { 'color-contrast': { enabled: false }, 'color-contrast-enhanced': { enabled: false } },
    resultTypes: ['violations'],
  })
  const violations = results.violations.map(violation => `${violation.id}: ${violation.nodes.map(node => `${node.target.join(' ')} ${node.failureSummary}`).join(' | ')}`)
  assert.deepEqual(violations, [])
}

const player = {
  item: { id: 'ember', name: 'ember.flac', title: 'Ember Signal', artist: 'Red Lotus', album: 'Night', mime: 'audio/flac', duration_seconds: 125, status: 'ready', scan: { ok: true } },
  id: 'ember',
  playing: false,
  loading: false,
  position: 12,
  duration: 125,
  volume: 0.4,
  shuffle: false,
  repeat: 'off',
  error: null,
  graph: null,
  dsp: { enabled: false, gains: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0], loudness: false, limiter: true, preampDb: 0 },
  toggle() {}, advance() {}, toggleShuffle() {}, cycleRepeat() {}, volumeChange() {}, seek() {},
  stop: async () => {}, showError() {}, play() {}, setDsp() {},
}

const queueProps = {
  items: [player.item, { id: 'blocked', name: 'blocked.wav', title: 'Blocked', artist: 'Red Lotus', status: 'blocked', scan: { ok: false, reason: 'Defender blocked this file' }, size_bytes: 20 }],
  player,
  busy: false,
  quota: { used_bytes: 1000, limit_bytes: 5000 },
  onImport() {},
  onImportLink() {},
  onRemove() {},
  onMove() {},
  onRescan() {},
}

function listening(extra = {}) {
  return markup(createElement(ListeningView, { player, available: true, mode: 'spectrum', onMode() {}, showEq: true, onToggleEq() {}, queueProps, ...extra }))
}

test('Listening view exposes landmarks, names, focusable order, and axe-clean structure', async () => {
  const document = listening()
  assert.ok(named(document, 'region', 'Listening'))
  assert.ok(named(document, 'region', 'Now playing'))
  assert.ok(named(document, 'region', 'Playback queue'))
  assert.ok(named(document, 'region', 'Equalizer'))
  assertHeadingOrder(document)
  assert.equal(document.querySelector('.track-copy h1').textContent, 'Ember Signal')
  assert.ok(named(document, 'status', 'PAUSED'))
  assert.match(named(document, 'slider', 'Playback position').getAttribute('aria-valuetext'), /0 minutes 12 seconds of 2 minutes 5 seconds/)
  assert.match(named(document, 'slider', 'Volume').getAttribute('aria-valuetext'), /40 percent/)
  assert.equal(named(document, 'slider', 'Rotary volume').getAttribute('aria-orientation'), 'vertical')
  for (const name of ['Shuffle', 'Previous track', 'Play', 'Next track', 'Repeat: off', 'Stop', 'Search playback queue', '31 Hz gain in decibels']) {
    assert.ok(named(document, name === 'Search playback queue' ? 'textbox' : name.includes('Hz') ? 'slider' : 'button', name), name)
  }
  assert.ok(named(document, 'button', 'Play Ember Signal'))
  assert.equal(document.querySelector('.queue-row[aria-current="true"], .queue-row .track-select[aria-current="true"]')?.getAttribute('aria-current') || document.querySelector('[aria-current="true"]')?.getAttribute('aria-current'), 'true')
  assert.ok(document.querySelector('.queue-list ul > li.queue-row'))
  assertNamedControls(document)
  await assertAxe(document)
})

test('Listening empty and loading states keep their space and are announced', () => {
  const empty = markup(createElement(Queue, { ...queueProps, items: [], loading: false }))
  assert.match(empty.querySelector('.empty-state').textContent, /Make room for your music/)
  assert.equal(empty.querySelector('.empty-state').getAttribute('role'), 'status')
  const loading = markup(createElement(Queue, { ...queueProps, items: [], loading: true }))
  assert.match(loading.querySelector('.empty-state').textContent, /Loading your queue/)
  assert.equal(loading.querySelector('.empty-state').getAttribute('role'), 'status')
})

test('reduced motion pauses the listening visualization label', () => {
  reduceMotion = true
  try {
    const document = listening()
    assert.match(document.querySelector('.visualizer canvas').getAttribute('aria-label'), /reduced motion/i)
  } finally { reduceMotion = false }
})

const device = { connected: true, model: 'NW-S705F', free_bytes: 400, total_bytes: 1000, track_count: 1 }
const tracks = [{ id: '9', title: 'On Device', artist: 'Sony', album: 'Album', duration_seconds: 42 }]

function walkman(props = {}) {
  return markup(createElement(DevicePanel, {
    device, tracks, etag: '"e"', busy: false, backupBusy: false, onBackup() {}, onDelete() {}, loading: false, failure: null, ...props,
  }))
}

test('Walkman view names device controls and announces playlist failure codes', async () => {
  const document = walkman()
  assert.ok(named(document, 'region', 'Walkman'))
  assertHeadingOrder(document)
  assert.equal(document.querySelector('h1').textContent, 'Walkman device')
  assert.ok(named(document, 'textbox', 'Search device tracks'))
  assert.ok(named(document, 'combobox', 'Sort device tracks'))
  assert.match(named(document, 'meter', 'Device storage used').getAttribute('aria-valuetext'), /percent/)
  assert.ok(named(document, 'button', 'Back up device'))
  assert.ok(named(document, 'button', 'Delete On Device from device'))
  assert.ok(document.querySelector('ul.device-track-list > li.device-track'))
  assert.equal(document.querySelector('.playlist-failure'), null)
  assertNamedControls(document)
  await assertAxe(document)

  for (const code of ['PLAYLIST_REF_MISSING', 'PLAYLIST_JOURNAL_PENDING', 'PLAYLIST_SLOTS_EXHAUSTED']) {
    const failed = walkman({ failure: playlistFailureMessage(code) })
    const alert = failed.querySelector('.playlist-failure')
    assert.equal(alert.getAttribute('role'), 'alert')
    assert.equal(alert.getAttribute('aria-live'), 'assertive')
    assert.match(alert.textContent, new RegExp(playlistFailureMessage(code).title))
    assert.match(alert.textContent, new RegExp(playlistFailureMessage(code).message.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')))
    await assertAxe(failed)
  }

  const unknown = walkman({ failure: null })
  assert.equal(unknown.querySelector('.playlist-failure'), null)
  const loading = walkman({ loading: true, device: { connected: false }, tracks: [] })
  assert.match(loading.body.textContent, /Checking for a Walkman/)
  assert.match(loading.body.textContent, /Loading the device library/)
  const disconnected = walkman({ device: { connected: false }, tracks: [] })
  assert.match(disconnected.body.textContent, /No device connected/)
  assert.match(disconnected.querySelector('.empty-state').textContent, /Your device ledger appears here/)
})

const staged = [
  { id: 'ember', name: 'ember.flac', title: 'Ember Signal', status: 'ready', scan: { ok: true }, size_bytes: 2048 },
  { id: 'blocked', name: 'blocked.wav', title: 'Blocked', status: 'blocked', scan: { ok: false, reason: 'Defender blocked this file' }, size_bytes: 20 },
]

function transfer(props = {}) {
  return markup(createElement(StagingPanel, {
    items: staged,
    selection: new Set(['ember']),
    onSelection() {},
    onTransfer() {},
    onRescan() {},
    onImport() {},
    onImportLink() {},
    busy: false,
    connected: true,
    loading: false,
    failure: null,
    ...props,
  }))
}

test('Transfer view names staging controls, announces selection, and shows playlist codes', async () => {
  const document = transfer()
  assert.ok(named(document, 'region', 'Transfer'))
  assertHeadingOrder(document)
  assert.equal(document.querySelector('h1').textContent, 'Transfer & scan')
  assert.ok(named(document, 'textbox', 'Search staging area'))
  assert.ok(named(document, 'combobox', 'Sort staged files'))
  assert.ok(named(document, 'checkbox', 'Stage ember.flac for transfer'))
  assert.ok(named(document, 'checkbox', 'Select cleared files'))
  assert.ok(named(document, 'button', 'Rescan'))
  assert.ok(named(document, 'button', 'Transfer 1 file'))
  assert.equal(document.querySelector('.transfer-footer [aria-live="polite"]')?.textContent.replace(/\s+/g, ' ').trim().includes('1 selected'), true)
  assert.ok(document.querySelector('.staged-files ul > li.staged-file'))
  assertNamedControls(document)
  await assertAxe(document)

  for (const code of ['PLAYLIST_REF_MISSING', 'PLAYLIST_JOURNAL_PENDING', 'PLAYLIST_SLOTS_EXHAUSTED']) {
    const failed = transfer({ failure: playlistFailureMessage(code) })
    assert.equal(failed.querySelector('.playlist-failure')?.getAttribute('role'), 'alert')
    assert.match(failed.querySelector('.playlist-failure').textContent, new RegExp(playlistFailureMessage(code).title))
  }
  const empty = transfer({ items: [], selection: new Set(), loading: false })
  assert.match(empty.querySelector('.empty-state').textContent, /Nothing staged yet/)
  assert.equal(empty.querySelector('.empty-state').getAttribute('role'), 'status')
  const loading = transfer({ items: [], selection: new Set(), loading: true })
  assert.match(loading.body.textContent, /Loading staged files/)
  const unknown = transfer({ failure: null })
  assert.equal(unknown.querySelector('.playlist-failure'), null)
})

test('operation progress is exposed to assistive tech without changing the job title', () => {
  const document = markup(createElement(JobPanel, {
    job: { job_id: 'j', kind: 'transfer', status: 'running', phase: 'transferring', progress: 0.42, message: 'Transferring Ember', files: [] },
  }))
  assert.match(document.querySelector('.job-title').textContent, /transfer/)
  assert.equal(document.querySelector('progress').getAttribute('aria-label'), 'Operation progress')
  assert.match(document.querySelector('progress').getAttribute('aria-valuetext'), /42 percent/)
  assert.equal(document.querySelector('.visually-hidden[aria-live="polite"]').textContent, '40 percent complete')
  assert.equal(document.querySelector('.job-panel p').getAttribute('role'), 'status')
  assert.match(document.querySelector('.job-panel p').textContent, /Transferring Ember/)
})

test('an unknown job code keeps the generic operation message', () => {
  const document = markup(createElement(JobPanel, {
    job: { job_id: 'j', kind: 'playlist_update', status: 'failed', phase: 'finished', progress: 1, message: 'Playlist edit rejected', code: 'playlist_failed', files: [] },
  }))
  assert.equal(document.querySelector('.playlist-failure'), null)
  assert.match(document.querySelector('.job-panel p').textContent, /Playlist edit rejected/)
})

function jsonResponse(body, { status = 200, headers = {} } = {}) {
  return { ok: status >= 200 && status < 300, status, headers: { get: name => headers[name.toLowerCase()] ?? null }, json: async () => body }
}

async function settle() {
  for (let i = 0; i < 12; i += 1) await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)) })
}

async function renderBridge(handler) {
  globalThis.fetch = handler
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  await act(async () => { root.render(createElement(App)) })
  await settle()
  return {
    root,
    cleanup() {
      act(() => { root.unmount() })
      container.remove()
    },
  }
}

test('the bridge shell routes the three playlist codes into Walkman and Transfer alerts', async () => {
  const codes = ['PLAYLIST_REF_MISSING', 'PLAYLIST_JOURNAL_PENDING', 'PLAYLIST_SLOTS_EXHAUSTED']
  for (const code of codes) {
    const view = await renderBridge(async url => {
      const path = String(url).split('?')[0]
      if (path === '/api/health') return jsonResponse({ ok: true, product: 'bridge', version: '0.4.1' })
      if (path === '/api/playback-state') return jsonResponse({})
      if (path === '/api/queue') return jsonResponse({ items: [], quota: { used_bytes: 0, limit_bytes: 100 } })
      if (path === '/api/engine-busy') return jsonResponse({ busy: false, active: [] })
      if (path === '/api/device') return jsonResponse({ connected: true, model: 'NW-S705F', free_bytes: 10, total_bytes: 20, track_count: 0 })
      if (path === '/api/tracks') return jsonResponse([], { headers: { etag: '"tracks"' } })
      if (path === '/api/jobs/latest') return jsonResponse({ job_id: 'job-1', kind: 'transfer', status: 'failed', phase: 'finished', progress: 1, message: 'shim failed', code: 'playlist_failed', files: [{ file_id: 'ember', fatal_code: code }] })
      return jsonResponse({ ok: true })
    })
    try {
      assert.ok(document.querySelector('.skip-link')?.textContent.includes('Skip to content'))
      assert.equal(document.querySelector('main')?.id, 'workspace')
      assert.equal(document.querySelector('main')?.getAttribute('aria-label'), 'Listening')
      const copy = playlistFailureMessage(code)
      assert.match(document.querySelector('.notice-strip')?.textContent || '', new RegExp(copy.title))
      assert.equal(document.querySelector('.playlist-failure'), null)
      const walkmanTab = [...document.querySelectorAll('.workspace-tabs button')].find(button => button.textContent === 'Walkman')
      await act(async () => { walkmanTab.click() })
      assert.equal(document.querySelector('main').getAttribute('aria-label'), 'Walkman')
      assert.match(document.querySelector('.device-column .playlist-failure')?.textContent || '', new RegExp(copy.title))
      assert.equal(document.querySelector('.notice-strip'), null)
      const transferTab = [...document.querySelectorAll('.workspace-tabs button')].find(button => button.textContent === 'Transfer')
      await act(async () => { transferTab.click() })
      assert.equal(document.querySelector('main').getAttribute('aria-label'), 'Transfer')
      assert.match(document.querySelector('.staging-panel .playlist-failure')?.textContent || '', new RegExp(copy.message))
    } finally { view.cleanup() }
  }

  const generic = await renderBridge(async url => {
    const path = String(url).split('?')[0]
    if (path === '/api/health') return jsonResponse({ ok: true, product: 'bridge', version: '0.4.1' })
    if (path === '/api/playback-state') return jsonResponse({})
    if (path === '/api/queue') return jsonResponse({ items: [], quota: { used_bytes: 0, limit_bytes: 100 } })
    if (path === '/api/engine-busy') return jsonResponse({ busy: false, active: [] })
    if (path === '/api/device') return jsonResponse({ detail: { code: 'playlist_failed', message: 'Playlist edit rejected', fatal_code: null } }, { status: 409 })
    if (path === '/api/jobs/latest') return jsonResponse(null)
    return jsonResponse({ ok: true })
  })
  try {
    await act(async () => { [...document.querySelectorAll('.workspace-tabs button')].find(button => button.textContent === 'Walkman').click() })
    assert.equal(document.querySelector('.playlist-failure'), null)
    assert.match(document.querySelector('.notice-strip')?.textContent || '', /Connection interrupted: Playlist edit rejected/)
    assert.equal(document.querySelector('.notice-strip')?.getAttribute('role'), 'status')
  } finally { generic.cleanup() }

  const readBlocked = await renderBridge(async url => {
    const path = String(url).split('?')[0]
    if (path === '/api/health') return jsonResponse({ ok: true, product: 'bridge', version: '0.4.1' })
    if (path === '/api/playback-state') return jsonResponse({})
    if (path === '/api/queue') return jsonResponse({ items: [], quota: { used_bytes: 0, limit_bytes: 100 } })
    if (path === '/api/engine-busy') return jsonResponse({ busy: false, active: [] })
    if (path === '/api/device') return jsonResponse({ connected: true, model: 'NW-S705F', free_bytes: 10, total_bytes: 20, track_count: 0 })
    if (path === '/api/tracks') return jsonResponse({ detail: { message: 'journal open', fatal_code: 'PLAYLIST_JOURNAL_PENDING' } }, { status: 409 })
    if (path === '/api/jobs/latest') return jsonResponse(null)
    return jsonResponse({ ok: true })
  })
  try {
    await act(async () => { [...document.querySelectorAll('.workspace-tabs button')].find(button => button.textContent === 'Walkman').click() })
    const copy = playlistFailureMessage('PLAYLIST_JOURNAL_PENDING')
    assert.match(document.querySelector('.device-column .playlist-failure')?.textContent || '', new RegExp(copy.title))
    assert.match(document.querySelector('.device-column .playlist-failure')?.textContent || '', /interrupted playlist transaction/)
    assert.equal(document.querySelector('.notice-strip'), null)
  } finally { readBlocked.cleanup() }
})
