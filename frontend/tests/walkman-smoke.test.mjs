// Headless Walkman Bridge renderer and resize checks.
// Replaces the hand-run viewport pass for Listening, Walkman and Transfer.
// Run from the repository root:
//   node --test frontend/tests/walkman-smoke.test.mjs
// The same file is included in `npm run test:frontend`.
import test, { before, after } from 'node:test'
import assert from 'node:assert/strict'
import { fileURLToPath } from 'node:url'
import { dialogReport, layoutReport, openBridge, startHarness } from './smoke-harness.mjs'

const VIEWS = ['listening', 'device', 'transfer', 'settings']
const VIEWPORTS = [
  [1280, 800],
  [1280, 632],
  [1360, 720],
  [1140, 860],
  [1380, 860],
  [900, 700],
  [720, 560],
  [640, 560],
  [800, 1200],
  [1920, 640],
  [512, 448],
  [320, 280],
]
const DIALOG_SIZES = new Set(['1140x860', '900x700', '640x560', '320x280'])
const EQUALIZER_SIZES = new Set(['1140x860', '640x560', '320x280'])

let harness

before(async () => { harness = await startHarness() })
after(async () => { await harness?.close() })

function assertQuiet(page, label) {
  assert.deepEqual(page.errors, [], `${label} console/page errors: ${page.errors.join(' | ')}`)
}

function assertLayout(report, label) {
  const detail = `${label}: ${JSON.stringify({ horizontal: report.horizontal, unreachable: report.unreachable, viewport: report.viewport, root: report.root, main: report.main, footerVisible: report.footerVisible, appError: report.appError })}`
  assert.equal(report.legacyBranding, false, detail)
  assert.equal(report.title, 'Red Lotus - Sony Walkman Dashboard', detail)
  assert.equal(report.retroControls, true, detail)
  assert.equal(report.appError, '', detail)
  assert.deepEqual(report.horizontal, [], detail)
  assert.deepEqual(report.unreachable, [], detail)
  assert.equal(report.footerVisible, true, detail)
  assert.ok(report.main?.height >= 30, detail)
  assert.ok(report.root[1] <= report.viewport[1] + 2, detail)
  assert.equal(harness.unexpected().length, 0, `${label} unexpected API: ${harness.unexpected().join(', ')}`)
}

test('Walkman Bridge boots and renders without console errors', { timeout: 180000 }, async () => {
  const page = await harness.newPage()
  await openBridge(page, harness.origin)
  await page.waitForSelector('.listening-column .retro-deck .volume-dial')
  await page.waitForSelector('footer .manage-music')
  const boot = await page.evaluate(() => ({
    title: document.title,
    legacy: /night\s+ops/i.test(document.body.innerText),
    product: document.querySelector('.brand-mark span')?.textContent || '',
    tabs: [...document.querySelectorAll('.workspace-tabs button')].map(button => button.textContent.trim()),
    connected: document.querySelector('.workspace-tabs .connection')?.textContent || '',
    transport: !!document.querySelector('footer [aria-label="Play"]'),
    manage: document.querySelector('footer .manage-music')?.textContent || '',
    stopped: !!document.querySelector('.listening-column .stop-button'),
    volume: !!document.querySelector('.listening-column .retro-deck .volume-dial'),
    error: document.querySelector('.notice-strip.error')?.innerText || '',
    shell: !!document.querySelector('.nightops-app'),
  }))
  assert.equal(boot.shell, true)
  assert.equal(boot.title, 'Red Lotus - Sony Walkman Dashboard')
  assert.equal(boot.legacy, false)
  assert.equal(boot.product, 'Sony Walkman Dashboard')
  assert.deepEqual(boot.tabs, ['Listening', 'Walkman', 'Transfer', 'Settings'])
  assert.match(boot.connected, /NW-S705F/)
  assert.equal(boot.transport, true)
  assert.equal(boot.manage, 'Manage music')
  assert.equal(boot.stopped, true)
  assert.equal(boot.volume, true)
  assert.equal(boot.error, '')
  assertQuiet(page, 'renderer boot')
  await page.close()
})

test('Listening, Walkman and Transfer views fit representative viewports', { timeout: 180000 }, async () => {
  const page = await harness.newPage()
  await openBridge(page, harness.origin)
  let cases = 0
  for (const [width, height] of VIEWPORTS) {
    const size = `${width}x${height}`
    await page.setViewportSize({ width, height })
    for (const view of VIEWS) {
      const label = `${view} ${size}`
      await showView(page, view, false)
      const landmarks = await page.evaluate(expected => {
        const element = document.querySelector(expected)
        if (!element || !element.checkVisibility({ visibilityProperty: true })) return false
        element.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'instant' })
        const rect = element.getBoundingClientRect()
        const visibleWidth = Math.min(rect.right, innerWidth) - Math.max(rect.left, 0)
        const visibleHeight = Math.min(rect.bottom, innerHeight) - Math.max(rect.top, 0)
        return visibleWidth > 20 && visibleHeight > 20
      }, { listening: '.listening-column .retro-deck', device: '.device-column', transfer: '.staging-panel', settings: '.appearance-settings' }[view])
      assert.equal(landmarks, true, `${label} landmark is not visible`)
      if (view === 'listening') {
        await page.evaluate(() => {
          document.querySelectorAll('.queue-row-menu').forEach((menu, index) => { menu.open = index === 2 })
        })
      }
      assertLayout(await page.evaluate(layoutReport), label)
      cases += 1
      if (DIALOG_SIZES.has(size)) await checkDialogs(page, view, label)
      if (view === 'listening' && EQUALIZER_SIZES.has(size)) {
        await showView(page, view, true)
        assert.equal(await page.locator('.equalizer').count(), 1, `${label} equalizer`)
        assertLayout(await page.evaluate(layoutReport), `${label} equalizer`)
        cases += 1
        await showView(page, view, false)
      }
    }
  }
  assert.equal(cases, VIEWS.length * VIEWPORTS.length + EQUALIZER_SIZES.size)
  console.log(`Bridge viewport cases: ${cases}`)
  assertQuiet(page, 'viewport sweep')
  await page.close()
})

async function completedOperation(page) {
  await page.route('**/api/jobs/latest', route => route.fulfill({ json: {
    job_id: 'layout-completed', kind: 'rescan', status: 'done', phase: 'finished',
    progress: 1, message: 'Synthetic scan completed', files: [],
  } }))
}

test('appearance defaults light, persists, and changes without interrupting playback', { timeout: 180000 }, async () => {
  const page = await harness.newPage()
  try {
    // Observe the real browser audio element without replacing its playback behavior.
    await page.addInitScript(() => {
      window.Audio = new Proxy(window.Audio, { construct(target, args) {
        const audio = Reflect.construct(target, args)
        window.testAudio = audio
        return audio
      } })
    })
    const wav = Buffer.alloc(44 + 8000 * 2 * 60)
    wav.write('RIFF'); wav.writeUInt32LE(wav.length - 8, 4); wav.write('WAVEfmt ', 8)
    wav.writeUInt32LE(16, 16); wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22)
    wav.writeUInt32LE(8000, 24); wav.writeUInt32LE(16000, 28); wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34)
    wav.write('data', 36); wav.writeUInt32LE(wav.length - 44, 40)
    await page.route('**/api/media/*/stream*', route => route.fulfill({ contentType: 'audio/wav', body: wav }))
    await openBridge(page, harness.origin)
    assert.equal(await page.evaluate(() => document.documentElement.dataset.theme), 'light')
    await page.locator('.track-select').first().click()
    await page.waitForFunction(() => window.testAudio && !window.testAudio.paused && window.testAudio.currentTime > 0)
    await page.evaluate(() => { window.originalAudio = window.testAudio })
    const previousTime = await page.evaluate(() => window.testAudio.currentTime)
    const source = await page.evaluate(() => window.testAudio.src)
    const volume = await page.evaluate(() => window.testAudio.volume)
    for (const theme of ['dark', 'light', 'dark']) {
      await page.getByRole('button', { name: 'Settings', exact: true }).click()
      await page.getByRole('radio', { name: theme === 'dark' ? /Dark Bloom/ : /Paper & Ember/ }).click()
      await page.waitForFunction(theme => document.documentElement.dataset.theme === theme, theme)
      await page.getByRole('button', { name: 'Listening', exact: true }).click()
      assert.equal(await page.evaluate(() => window.testAudio === window.originalAudio && !window.testAudio.paused), true)
      assert.equal(await page.evaluate(() => window.testAudio.src), source)
      assert.equal(await page.evaluate(() => window.testAudio.volume), volume)
    }
    assert.ok(await page.evaluate(() => window.testAudio.currentTime) >= previousTime)
    await page.reload({ waitUntil: 'domcontentloaded' })
    await page.waitForSelector('.workspace-tabs')
    await page.waitForFunction(() => document.documentElement.dataset.theme === 'dark')
    assertQuiet(page, 'appearance and playback')
  } finally { await page.close() }
})

test('appearance save failures keep the saved theme and allow an explicit retry', async () => {
  const page = await harness.newPage()
  try {
    await openBridge(page, harness.origin)
    await page.getByRole('button', { name: 'Settings', exact: true }).click()
    const fail = route => route.fulfill({ status: 503, json: { detail: 'Preference storage unavailable' } })
    await page.route('**/api/preferences', fail)
    await page.getByRole('radio', { name: /Dark Bloom/ }).click()
    await page.getByRole('alert').waitFor()
    assert.match(await page.getByRole('alert').innerText(), /could not be saved/)
    assert.equal(await page.evaluate(() => document.documentElement.dataset.theme), 'light')
    await page.unroute('**/api/preferences', fail)
    await page.getByRole('radio', { name: /Dark Bloom/ }).click()
    await page.waitForFunction(() => document.documentElement.dataset.theme === 'dark')
    assert.equal(await page.getByRole('alert').count(), 0)
  } finally { await page.close() }
})

test('unavailable saved appearance keeps the music workspace usable with a light fallback', async () => {
  const page = await harness.newPage()
  try {
    const fail = route => route.fulfill({ status: 503, json: { detail: 'Preference storage unavailable' } })
    await page.route('**/api/preferences', fail)
    await openBridge(page, harness.origin)
    await page.waitForSelector('.queue-row')
    assert.equal(await page.locator('.queue-row').count(), 12)
    assert.equal(await page.evaluate(() => document.documentElement.dataset.theme), 'light')
    await page.getByRole('button', { name: 'Settings', exact: true }).click()
    assert.match(await page.getByRole('alert').innerText(), /could not be loaded/)
    await page.unroute('**/api/preferences', fail)
    await page.getByRole('button', { name: 'Save current appearance', exact: true }).click()
    await page.waitForFunction(() => !document.querySelector('.appearance-settings [role=alert]'))
  } finally { await page.close() }
})

test('Listening uses the workspace width with the queue beside the player on desktop', { timeout: 180000 }, async () => {
  const page = await harness.newPage()
  try {
    await completedOperation(page)
    await openBridge(page, harness.origin)
    await page.waitForSelector('.workspace-operation')
    for (const [width, height] of [[1280, 632], [1360, 720], [900, 700], [640, 560], [320, 280]]) {
      await page.setViewportSize({ width, height })
      await showView(page, 'listening', false)
      const boxes = await page.evaluate(() => {
        const rect = selector => {
          const r = document.querySelector(selector).getBoundingClientRect()
          return { left: r.left, right: r.right, top: r.top, bottom: r.bottom, width: r.width }
        }
        const main = document.querySelector('main'), style = getComputedStyle(main)
        return { main: rect('main'), navigation: rect('.workspace-tabs'), listening: rect('.listening-column'), deck: rect('.retro-deck'), queue: rect('.queue-panel'),
          inset: parseFloat(style.paddingLeft) + parseFloat(style.paddingRight), scrollbar: main.offsetWidth - main.clientWidth }
      })
      const label = `${width}x${height}: ${JSON.stringify(boxes)}`
      assert.ok(boxes.listening.width >= boxes.main.width - boxes.inset - boxes.scrollbar - 2, label)
      if (width > 900) {
        assert.ok(boxes.navigation.bottom <= boxes.main.top + 1, 'navigation stays above the workspace')
        assert.ok(boxes.queue.left >= boxes.deck.right, label)
        assert.ok(Math.abs(boxes.queue.top - boxes.deck.top) < 10, label)
      } else {
        assert.ok(boxes.navigation.bottom <= boxes.main.top + 1, 'narrow navigation stays above the workspace')
        assert.ok(boxes.queue.top >= boxes.deck.bottom, label)
      }
      assertLayout(await page.evaluate(layoutReport), label)
    }
    assertQuiet(page, 'Listening column regression')
  } finally { await page.close() }
})

test('Bridge view changes start at the top without resetting scroll on refresh', { timeout: 180000 }, async () => {
  const page = await harness.newPage()
  try {
    await completedOperation(page)
    await page.setViewportSize({ width: 640, height: 560 })
    await openBridge(page, harness.origin)
    await page.waitForSelector('.workspace-operation')
    for (const target of ['transfer', 'device', 'listening', 'transfer']) {
      const scrolled = await page.evaluate(() => {
        const main = document.querySelector('main')
        main.scrollTop = main.scrollHeight
        return main.scrollTop
      })
      assert.ok(scrolled > 30, 'fixture must really scroll before switching views')
      await showView(page, target, false)
      assert.equal(await page.locator('main').evaluate(main => main.scrollTop), 0, `${target} inherited another view\'s scroll`)
      if (target === 'transfer') {
        await page.getByRole('checkbox', { name: /^Stage Track 1 / }).check({ timeout: 5000 })
      }
    }
    await page.evaluate(() => { document.querySelector('main').scrollTop = 80 })
    const before = await page.locator('main').evaluate(main => main.scrollTop)
    assert.ok(before > 0)
    await page.waitForResponse(response => response.url().endsWith('/api/queue'))
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
    assert.equal(await page.locator('main').evaluate(main => main.scrollTop), before, 'ordinary refresh must preserve scroll')
    assert.match(await page.locator('.workspace-tabs button.active').innerText(), /1/)
    // Staging a saved playlist also changes views without clicking a tab.
    await page.route('**/api/playlists', route => route.fulfill({ json: { items: [
      { id: 'layout-list', name: 'Layout playlist', media_ids: ['layout-0'], etag: '"layout-list"' },
    ] } }))
    await showView(page, 'listening', false)
    await page.evaluate(() => { document.querySelector('main').scrollTop = 100 })
    await page.locator('footer .manage-music').click()
    await page.getByRole('button', { name: 'Playlists', exact: true }).click()
    await page.getByRole('button', { name: 'Layout playlist 1 tracks' }).click()
    await page.getByRole('button', { name: 'Stage for transfer', exact: true }).click()
    await page.waitForSelector('.staging-panel')
    assert.equal(await page.locator('main').evaluate(main => main.scrollTop), 0, 'playlist staging inherited Listening scroll')
    assert.equal(await page.getByRole('checkbox', { name: /^Stage Track 1 / }).isChecked(), true)
    assert.equal(harness.unexpected().length, 0)
    assertQuiet(page, 'view navigation regression')
  } finally { await page.close() }
})

async function showView(page, view, equalizer) {
  const name = { listening: 'Listening', device: 'Walkman', transfer: 'Transfer', settings: 'Settings' }[view]
  await page.evaluate(target => {
    const tab = [...document.querySelectorAll('.workspace-tabs button')].find(button => button.textContent.startsWith(target))
    if (tab.getAttribute('aria-pressed') !== 'true') tab.click()
  }, name)
  await page.waitForSelector({ listening: '.listening-column', device: '.device-column', transfer: '.staging-panel', settings: '.appearance-settings' }[view])
  if (view === 'listening') {
    await page.evaluate(open => {
      const toggle = document.querySelector('.bridge-eq-toggle button')
      if ((toggle.getAttribute('aria-expanded') === 'true') !== open) toggle.click()
    }, equalizer)
    await page.waitForFunction(open => {
      const expanded = document.querySelector('.bridge-eq-toggle button')?.getAttribute('aria-expanded') === 'true'
      return expanded === open
    }, equalizer)
  }
  await page.evaluate(() => document.fonts.ready)
}

async function checkDialogs(page, view, label) {
  if (view === 'listening' || view === 'transfer') await openAndMeasure(page, label, 'link', () => {
    const button = [...document.querySelectorAll('button')].find(item => item.textContent.trim() === 'Import link')
    button.click()
  }, 'Close link importer')
  if (view === 'device') await openAndMeasure(page, label, 'delete', () => {
    document.querySelector('.device-track .icon-button').click()
  }, 'Keep track')
  await openAndMeasure(page, label, 'manager', () => {
    document.querySelector('.manage-music').click()
  }, 'Done')
}

async function openAndMeasure(page, label, type, open, closeLabel) {
  await page.evaluate(open)
  await page.waitForSelector('dialog[open]')
  if (type === 'manager') await page.waitForSelector('.music-manager .manager-track-list')
  const report = await page.evaluate(dialogReport, type)
  const detail = `${label} ${type} dialog: ${JSON.stringify(report)}`
  assert.equal(report.bounded, true, detail)
  assert.equal(report.legacyBranding, false, detail)
  assert.equal(report.horizontal, false, detail)
  assert.deepEqual(report.unreachable, [], detail)
  await page.evaluate(name => {
    const dialog = document.querySelector('dialog[open]')
    const button = [...dialog.querySelectorAll('button')].find(item => (item.getAttribute('aria-label') || item.textContent.trim()) === name)
    button.click()
  }, closeLabel)
  await page.waitForFunction(() => !document.querySelector('dialog[open]'))
}

test('rendered polished views and dialogs pass automated WCAG AA audits with actual CSS contrast', { timeout: 180000 }, async () => {
  const page = await harness.newPage()
  try {
    await openBridge(page, harness.origin)
    await page.addScriptTag({ path: fileURLToPath(new URL('../node_modules/axe-core/axe.min.js', import.meta.url)) })
    const audit = async label => {
      const violations = await page.evaluate(async () => (await window.axe.run(document, {
        runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa'] },
      })).violations.map(({ id, nodes }) => ({ id, nodes: nodes.map(({ target, failureSummary }) => ({ target, failureSummary })) })))
      assert.deepEqual(violations, [], `${label}: ${JSON.stringify(violations)}`)
    }
    for (const theme of ['light', 'dark']) {
    await page.getByRole('button', { name: 'Settings', exact: true }).click()
    await page.getByRole('radio', { name: theme === 'dark' ? /Dark Bloom/ : /Paper & Ember/ }).click()
    await page.waitForFunction(theme => document.documentElement.dataset.theme === theme, theme)
    for (const width of [1280, 640, 320]) {
      await page.setViewportSize({ width, height: width === 320 ? 280 : 800 })
      for (const view of ['Listening', 'Walkman', 'Transfer', 'Settings']) {
        await page.getByRole('button', { name: view === 'Transfer' ? /^Transfer(?:,|$)/ : view, exact: true }).click()
        await audit(`${view} ${width}px`)
      }
    }
    await page.setViewportSize({ width: 1280, height: 800 })
    await page.getByRole('button', { name: /^Transfer(?:,|$)/ }).click()
    await page.getByRole('checkbox', { name: 'Select cleared files', exact: true }).check()
    // Enter keyboard modality: programmatic focus after a mouse click does not
    // request :focus-visible in Chromium, as expected for pointer interaction.
    await page.keyboard.press('Tab')
    await page.getByRole('button', { name: /^Transfer \d+ files$/, exact: false }).focus()
    const focus = await page.getByRole('button', { name: /^Transfer \d+ files$/ }).evaluate(element => ({
      focused: document.activeElement === element,
      style: getComputedStyle(element).outlineStyle,
      width: parseFloat(getComputedStyle(element).outlineWidth),
    }))
    assert.equal(focus.focused, true)
    assert.notEqual(focus.style, 'none')
    assert.ok(focus.width >= 2)
    await audit('selected files and focused transfer action')
    await page.getByRole('button', { name: 'Manage music', exact: true }).click()
    await audit('music manager')
    await page.getByRole('button', { name: 'Done', exact: true }).click()
    await page.getByRole('button', { name: 'Listening', exact: true }).click()
    await page.getByRole('button', { name: '+ Show equalizer', exact: true }).click()
    await audit('expanded equalizer')
    await page.getByRole('button', { name: 'Import link', exact: true }).click()
    await audit('link import')
    await page.getByRole('button', { name: 'Close link importer', exact: true }).click()
    if (await page.getByRole('button', { name: /Hide equalizer/ }).count()) await page.getByRole('button', { name: /Hide equalizer/ }).click()
    }
    assert.deepEqual(page.errors, [])
    assert.deepEqual(harness.unexpected(), [])
  } finally { await page.close() }
})

test('Player with queue and equalizer stays reachable at large and narrow preview sizes', { timeout: 180000 }, async () => {
  const page = await harness.newPage({ product: 'player' })
  try {
    await page.goto(harness.origin, { waitUntil: 'domcontentloaded' })
    await page.waitForSelector('.player-composition .queue-row')
    await page.getByRole('button', { name: 'Equalizer', exact: true }).click()
    await page.addScriptTag({ path: fileURLToPath(new URL('../node_modules/axe-core/axe.min.js', import.meta.url)) })
    for (const [width, height] of [[1140, 860], [1380, 860], [1920, 640], [800, 1200], [640, 560], [320, 280]]) {
      await page.setViewportSize({ width, height })
      const report = await page.evaluate(layoutReport)
      const detail = `${width}x${height}: ${JSON.stringify(report)}`
      assert.equal(report.title, 'Red Lotus Player', detail)
      assert.deepEqual(report.horizontal, [], detail)
      assert.deepEqual(report.unreachable, [], detail)
      assert.equal(report.footerVisible, true, detail)
      assert.ok(report.main.height >= 30, detail)
      const violations = await page.evaluate(async () => (await window.axe.run(document, {
        runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa'] },
      })).violations.map(({ id, nodes }) => ({ id, targets: nodes.map(node => node.target) })))
      assert.deepEqual(violations, [], detail)
    }
    assertQuiet(page, 'Player preview')
    assert.deepEqual(harness.unexpected(), [])
  } finally { await page.close() }
})
