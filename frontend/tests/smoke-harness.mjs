// Headless Chrome/Edge harness for the Walkman Bridge renderer smoke tests.
// Serves the production Vite build and a local API fixture. No screenshots,
// audio files, databases, or device data are written.
import { build } from 'vite'
import { chromium } from 'playwright-core'
import fs from 'node:fs'
import http from 'node:http'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const dist = path.join(frontendRoot, 'dist')

const queue = Array.from({ length: 12 }, (_, index) => ({
  id: `layout-${index}`,
  name: `Track ${index + 1} — A deliberately long filename to exercise the queue and staging layout.flac`,
  title: `Track ${index + 1} — A deliberately long title to exercise the queue`,
  artist: 'An artist with a long display name',
  album: 'Resize regression fixture',
  status: index === 2 ? 'blocked' : 'ready',
  duration_seconds: 240,
  mime: 'audio/flac',
  size_bytes: 12345678,
  scan: { ok: index !== 2, state: index === 2 ? 'blocked' : 'clean', reason: index === 2 ? 'Test fixture: clearance unavailable' : undefined },
}))
const tracks = queue.slice(0, 6).map((item, index) => ({
  id: `device-${index}`,
  title: item.title,
  artist: item.artist,
  album: item.album,
  duration_seconds: item.duration_seconds,
}))
let playback = { media_id: null, position_seconds: 0, volume: 0.4, shuffle: false, repeat: 'off' }

const types = {
  '.js': 'application/javascript',
  '.css': 'text/css',
  '.html': 'text/html',
  '.woff2': 'font/woff2',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
  '.json': 'application/json',
  '.map': 'application/json',
}

function browserExecutable() {
  const found = [
    process.env.WALKMAN_CHROME,
    '/usr/local/bin/google-chrome',
    '/usr/bin/google-chrome',
    '/usr/bin/google-chrome-stable',
    '/usr/bin/chromium',
    '/usr/bin/chromium-browser',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    process.env.PROGRAMFILES && path.join(process.env.PROGRAMFILES, 'Google', 'Chrome', 'Application', 'chrome.exe'),
    process.env['PROGRAMFILES(X86)'] && path.join(process.env['PROGRAMFILES(X86)'], 'Google', 'Chrome', 'Application', 'chrome.exe'),
    process.env.PROGRAMFILES && path.join(process.env.PROGRAMFILES, 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
    process.env['PROGRAMFILES(X86)'] && path.join(process.env['PROGRAMFILES(X86)'], 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
    process.env.LOCALAPPDATA && path.join(process.env.LOCALAPPDATA, 'Google', 'Chrome', 'Application', 'chrome.exe'),
  ].filter(candidate => candidate && fs.existsSync(candidate))
  if (!found.length) throw new Error('Headless renderer smoke needs Chrome or Edge. Set WALKMAN_CHROME to the browser executable.')
  return found[0]
}

async function ensureBuild() {
  // Tailwind resolves its content globs from the process cwd. The production
  // frontend build runs in this directory; match that so preflight and utilities
  // are the same CSS the Electron harness serves.
  const previous = process.cwd()
  process.chdir(frontendRoot)
  try {
    await build({
      root: frontendRoot,
      configFile: path.join(frontendRoot, 'vite.config.js'),
      logLevel: 'error',
    })
  } finally {
    process.chdir(previous)
  }
  if (!fs.existsSync(path.join(dist, 'index.html'))) throw new Error('Frontend production build did not emit dist/index.html')
}

function listen() {
  const unexpected = []
  const server = http.createServer(async (req, res) => {
    try {
      const url = new URL(req.url, 'http://127.0.0.1')
      if (url.pathname === '/favicon.ico') { res.statusCode = 204; res.end(); return }
      if (url.pathname.startsWith('/api/')) {
        let text = ''
        for await (const chunk of req) text += chunk
        const body = text ? JSON.parse(text) : {}
        const json = value => { res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(value)) }
        if (url.pathname === '/api/health') return json({ ok: true, product: 'bridge', version: '0.4.1' })
        if (url.pathname === '/api/playback-state') {
          if (req.method === 'PATCH') playback = { ...playback, ...body }
          return json(playback)
        }
        if (url.pathname === '/api/playback/lease') return json({ ok: true })
        if (url.pathname === '/api/queue') return json({ items: queue, quota: { used_bytes: 12345678 * queue.length, limit_bytes: 10 * 1024 ** 3 } })
        if (url.pathname === '/api/engine-busy') return json({ busy: false, draining: false, active: [] })
        if (url.pathname === '/api/jobs/latest') return json(null)
        if (url.pathname === '/api/device') return json({ connected: true, model: 'NW-S705F', track_count: tracks.length, total_bytes: 2 * 1024 ** 3, free_bytes: 1024 ** 3 })
        if (url.pathname === '/api/tracks') { res.setHeader('ETag', '"layout-fixture"'); return json(tracks) }
        if (url.pathname === '/api/playlists') return json({ items: [] })
        if (url.pathname === '/api/device/playlists') { res.setHeader('ETag', '"layout-playlists"'); return json({ items: [] }) }
        unexpected.push(`${req.method} ${url.pathname}`)
        res.statusCode = 404
        return json({ detail: 'Unexpected layout-fixture request' })
      }
      const relative = decodeURIComponent(url.pathname === '/' ? '/index.html' : url.pathname)
      const file = path.resolve(dist, `.${relative}`)
      if (!file.startsWith(`${dist}${path.sep}`) || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
        res.statusCode = 404
        res.end()
        return
      }
      res.setHeader('Content-Type', types[path.extname(file)] || 'application/octet-stream')
      fs.createReadStream(file).pipe(res)
    } catch (error) {
      if (!res.headersSent) res.statusCode = 500
      res.end(String(error && error.message || error))
    }
  })
  return new Promise((resolve, reject) => {
    server.once('error', reject)
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address()
      resolve({
        origin: `http://127.0.0.1:${port}`,
        unexpected: () => [...unexpected],
        close: () => new Promise((done, fail) => server.close(error => error ? fail(error) : done())),
      })
    })
  })
}

export async function startHarness() {
  await ensureBuild()
  const server = await listen()
  const browser = await chromium.launch({
    executablePath: browserExecutable(),
    headless: true,
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
  })
  return {
    origin: server.origin,
    unexpected: server.unexpected,
    async newPage() {
      const page = await browser.newPage({ viewport: { width: 1280, height: 800 } })
      const errors = []
      page.on('pageerror', error => errors.push(`pageerror: ${error.message}`))
      page.on('console', message => { if (message.type() === 'error') errors.push(`console: ${message.text()}`) })
      await page.addInitScript(() => {
        window.walkmanBridge = {
          product: 'bridge',
          minimize() {},
          maximize() {},
          close() {},
          onStopPlayback() { return () => {} },
          onDraining() { return () => {} },
        }
      })
      page.errors = errors
      return page
    },
    async close() {
      await browser.close()
      await server.close()
    },
  }
}

export async function openBridge(page, origin) {
  await page.goto(origin, { waitUntil: 'domcontentloaded' })
  await page.waitForSelector('.nightops-app .workspace-tabs')
  await page.evaluate(() => document.fonts.ready)
}

export const layoutReport = () => {
  const main = document.querySelector('main')
  const root = document.documentElement
  const footer = document.querySelector('footer')
  const bounds = element => {
    if (!element) return null
    const rect = element.getBoundingClientRect()
    return { x: rect.x, y: rect.y, width: rect.width, height: rect.height, right: rect.right, bottom: rect.bottom }
  }
  const visible = element => element.checkVisibility({ visibilityProperty: true })
  const fit = element => {
    if (!element) return false
    const rect = element.getBoundingClientRect()
    return rect.left >= -1 && rect.right <= innerWidth + 1 && rect.top >= -1 && rect.bottom <= innerHeight + 1
  }
  const summary = {
    viewport: [innerWidth, innerHeight],
    root: [root.scrollWidth, root.scrollHeight],
    shell: bounds(document.querySelector('.nightops-app')),
    main: bounds(main),
    footer: bounds(footer),
    footerVisible: fit(footer),
    title: document.title,
    legacyBranding: /night\s+ops/i.test(document.body.innerText),
    retroControls: !document.querySelector('.now-playing') || !!document.querySelector('.retro-deck .volume-dial'),
    appError: document.querySelector('.notice-strip.error')?.innerText || '',
    horizontal: [],
    unreachable: [],
  }
  for (const element of [root, document.body, document.querySelector('.nightops-app'), main, ...document.querySelectorAll('.panel,.retro-deck,.app-titlebar,footer')]) {
    if (element && element.scrollWidth > element.clientWidth + 2) summary.horizontal.push({ element: element.className || element.tagName, client: element.clientWidth, scroll: element.scrollWidth })
  }
  for (const element of document.querySelectorAll('main button,main input,main select,main summary,footer button,footer input,.window-controls button,.workspace-tabs button,.backup-warning button')) {
    if (!visible(element)) continue
    element.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'instant' })
    const rect = element.getBoundingClientRect()
    let left = Math.max(0, rect.left), right = Math.min(innerWidth, rect.right), top = Math.max(0, rect.top), bottom = Math.min(innerHeight, rect.bottom)
    for (let parent = element.parentElement; parent; parent = parent.parentElement) {
      const style = getComputedStyle(parent)
      const box = parent.getBoundingClientRect()
      if (/auto|scroll|hidden|clip/.test(style.overflowX)) { left = Math.max(left, box.left); right = Math.min(right, box.right) }
      if (/auto|scroll|hidden|clip/.test(style.overflowY)) { top = Math.max(top, box.top); bottom = Math.min(bottom, box.bottom) }
    }
    const hit = right > left && bottom > top ? document.elementFromPoint((left + right) / 2, (top + bottom) / 2) : null
    if (right - left < rect.width - 2 || bottom - top < Math.min(rect.height, 24) - 2 || !hit || !(hit === element || element.contains(hit))) {
      summary.unreachable.push(element.getAttribute('aria-label') || element.textContent.trim().slice(0, 80))
    }
  }
  for (const scroll of document.querySelectorAll('main, main *')) if (scroll.scrollHeight > scroll.clientHeight) scroll.scrollTop = 0
  window.scrollTo(0, 0)
  return summary
}

export const dialogReport = type => {
  const dialog = document.querySelector('dialog[open]')
  const rect = dialog.getBoundingClientRect()
  const result = {
    type,
    width: rect.width,
    height: rect.height,
    viewport: [innerWidth, innerHeight],
    bounded: rect.left >= 0 && rect.right <= innerWidth + 1 && rect.top >= 0 && rect.bottom <= innerHeight + 1 && rect.width > 40 && rect.height > 40,
    legacyBranding: /night\s+ops/i.test(dialog.innerText),
    horizontal: dialog.scrollWidth > dialog.clientWidth + 2,
    unreachable: [],
  }
  for (const control of dialog.querySelectorAll('button,input,select,summary')) {
    if (!control.checkVisibility({ visibilityProperty: true })) continue
    control.scrollIntoView({ block: 'center', behavior: 'instant' })
    const box = control.getBoundingClientRect()
    let left = Math.max(0, box.left), right = Math.min(innerWidth, box.right), top = Math.max(0, box.top), bottom = Math.min(innerHeight, box.bottom)
    for (let parent = control.parentElement; parent; parent = parent.parentElement) {
      const style = getComputedStyle(parent)
      const area = parent.getBoundingClientRect()
      if (/auto|scroll|hidden|clip/.test(style.overflowX)) { left = Math.max(left, area.left); right = Math.min(right, area.right) }
      if (/auto|scroll|hidden|clip/.test(style.overflowY)) { top = Math.max(top, area.top); bottom = Math.min(bottom, area.bottom) }
    }
    const hit = right > left && bottom > top ? document.elementFromPoint((left + right) / 2, (top + bottom) / 2) : null
    if (right - left < box.width - 2 || bottom - top < Math.min(box.height, 24) - 2 || !hit || !(hit === control || control.contains(hit))) {
      result.unreachable.push(control.textContent.trim().slice(0, 80) || control.getAttribute('aria-label') || control.id)
    }
  }
  return result
}
