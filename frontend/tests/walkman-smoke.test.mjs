// Headless Walkman Bridge renderer and resize checks.
// Replaces the hand-run viewport pass for Listening, Walkman and Transfer.
// Run from the repository root:
//   node --test frontend/tests/walkman-smoke.test.mjs
// The same file is included in `npm run test:frontend`.
import test, { before, after } from 'node:test'
import assert from 'node:assert/strict'
import { dialogReport, layoutReport, openBridge, startHarness } from './smoke-harness.mjs'

const VIEWS = ['listening', 'device', 'transfer']
const VIEWPORTS = [
  [1280, 800],
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
  assert.equal(report.title, 'Walkman Bridge', detail)
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
  assert.equal(boot.title, 'Walkman Bridge')
  assert.equal(boot.legacy, false)
  assert.equal(boot.product, 'WALKMAN BRIDGE')
  assert.deepEqual(boot.tabs, ['Listening', 'Walkman', 'Transfer'])
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
      }, { listening: '.listening-column .retro-deck', device: '.device-column', transfer: '.staging-panel' }[view])
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

async function showView(page, view, equalizer) {
  const name = { listening: 'Listening', device: 'Walkman', transfer: 'Transfer' }[view]
  await page.evaluate(target => {
    const tab = [...document.querySelectorAll('.workspace-tabs button')].find(button => button.textContent.startsWith(target))
    if (tab.getAttribute('aria-pressed') !== 'true') tab.click()
  }, name)
  await page.waitForSelector({ listening: '.listening-column', device: '.device-column', transfer: '.staging-panel' }[view])
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
