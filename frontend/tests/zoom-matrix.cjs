// Electron zoom matrix for both products. Launch with the Electron binary
// (scripts/zoom-matrix.test.cjs). This fixture serves the built renderer and a
// loopback API only. It does not start the backend, scanner, installer, audio
// decoder, or any device/backup/sqlite path.
'use strict'
const { app, BrowserWindow, screen } = require('electron')
const http = require('node:http')
const fs = require('node:fs')
const path = require('node:path')

// Same percentages and window sizes as frontend/tests/resize-smoke.cjs.
// 640×560 at 200% is the 320 CSS-pixel width named in docs/DESIGN.md.
const ZOOM_FACTORS = [1, 1.25, 1.5, 2]
const WINDOW_SIZES = [[640, 560], [720, 560], [900, 700], [1140, 860], [1380, 860], [1920, 640], [800, 1200]]
const PRODUCTS = ['player', 'bridge']

app.commandLine.appendSwitch('disable-gpu')
app.commandLine.appendSwitch('disable-dev-shm-usage')
app.disableHardwareAcceleration()
app.on('window-all-closed', () => {})

const output = path.resolve(__dirname, '../test-output/zoom-matrix')
const profile = path.join(output, 'profile')
fs.mkdirSync(output, { recursive: true })
app.setPath('userData', profile)
function removeProfile() {
  try { fs.rmSync(profile, { recursive: true, force: true }) }
  catch { /* The wrapper removes the profile again after this process exits. */ }
}

const results = []
const failures = []
const errors = []
let product = 'player'
const timeout = setTimeout(() => {
  console.error('zoom-matrix TIMEOUT')
  removeProfile()
  app.exit(1)
}, 150000)

const items = Array.from({ length: 8 }, (_, index) => ({
  id: `zoom-${index}`,
  name: `Track ${index + 1} — A deliberately long filename to exercise zoomed queue and deck layout.flac`,
  title: `Track ${index + 1} — A deliberately long title to exercise the zoomed layout`,
  artist: 'An artist with a long display name',
  album: 'Zoom matrix fixture',
  status: index === 2 ? 'blocked' : 'ready',
  duration_seconds: 240,
  mime: 'audio/flac',
  size_bytes: 12345678,
  scan: { ok: index !== 2, state: index === 2 ? 'blocked' : 'clean' },
}))

const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://127.0.0.1')
  const json = value => {
    res.setHeader('Content-Type', 'application/json')
    res.end(JSON.stringify(value))
  }
  if (url.pathname.startsWith('/api/')) {
    if (url.pathname === '/api/health') return json({ ok: true, product, version: '0.4.1' })
    if (url.pathname === '/api/playback-state') return json({ media_id: null, position_seconds: 0, volume: 0.4, repeat: 'off', shuffle: false })
    if (url.pathname === '/api/queue') return json({ items, quota: { used_bytes: 12345678 * items.length, limit_bytes: 10 * 1024 ** 3 } })
    if (url.pathname === '/api/engine-busy') return json({ busy: false, draining: false, active: [] })
    if (url.pathname === '/api/jobs/latest') return json(null)
    if (url.pathname === '/api/device') return json({ connected: false })
    if (url.pathname === '/api/playlists') return json({ items: [] })
    if (url.pathname === '/api/device/playlists') {
      res.setHeader('ETag', '"zoom-playlists"')
      return json({ items: [] })
    }
    res.statusCode = 404
    errors.push(`Unexpected fixture request: ${req.method} ${url.pathname}`)
    return json({ detail: 'Unexpected zoom-matrix fixture request' })
  }
  const dist = path.resolve(__dirname, '../dist')
  const file = path.resolve(dist, `.${url.pathname === '/' ? '/index.html' : url.pathname}`)
  if (!file.startsWith(dist + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
    res.statusCode = 404
    return res.end()
  }
  const types = { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.woff2': 'font/woff2', '.png': 'image/png', '.svg': 'image/svg+xml' }
  res.setHeader('Content-Type', types[path.extname(file)] || 'application/octet-stream')
  fs.createReadStream(file).pipe(res)
})

const delay = ms => new Promise(resolve => setTimeout(resolve, ms))
const evaluate = (win, script) => win.webContents.executeJavaScript(script, true)

async function until(win, expression) {
  for (let attempt = 0; attempt < 100; attempt++) {
    if (await evaluate(win, expression)) return
    await delay(50)
  }
  throw new Error(`Renderer condition timed out: ${expression}`)
}

// Scroll each visible control into view and intersect every clipping ancestor.
// Matches the pass criteria used by the resize harness and docs/DESIGN.md.
const geometry = `(() => {
  const main = document.querySelector('main'), root = document.documentElement;
  const bounds = element => { const r = element.getBoundingClientRect(); return { x: r.x, y: r.y, width: r.width, height: r.height, right: r.right, bottom: r.bottom }; };
  const visible = element => element.checkVisibility({ visibilityProperty: true });
  const fit = element => { const r = element.getBoundingClientRect(); return r.left >= -1 && r.right <= innerWidth + 1 && r.top >= -1 && r.bottom <= innerHeight + 1; };
  const footer = document.querySelector('footer');
  const summary = {
    viewport: [innerWidth, innerHeight],
    root: [root.scrollWidth, root.scrollHeight],
    shell: bounds(document.querySelector('.nightops-app')),
    main: bounds(main),
    footer: bounds(footer),
    footerVisible: fit(footer),
    title: document.title,
    legacyBranding: /night\\s+ops/i.test(document.body.innerText),
    retroControls: !document.querySelector('.now-playing') || !!document.querySelector('.retro-deck .volume-dial'),
    horizontal: [],
    unreachable: [],
  };
  for (const element of [root, document.body, document.querySelector('.nightops-app'), main, ...document.querySelectorAll('.panel,.retro-deck,.app-titlebar,footer')]) {
    if (element && element.scrollWidth > element.clientWidth + 2) summary.horizontal.push({ element: element.className || element.tagName, client: element.clientWidth, scroll: element.scrollWidth });
  }
  for (const element of document.querySelectorAll('main button,main input,main select,main summary,footer button,footer input,.window-controls button,.player-wing-controls button,.workspace-tabs button')) {
    if (!visible(element)) continue;
    element.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'instant' });
    const r = element.getBoundingClientRect();
    let left = Math.max(0, r.left), right = Math.min(innerWidth, r.right), top = Math.max(0, r.top), bottom = Math.min(innerHeight, r.bottom);
    for (let parent = element.parentElement; parent; parent = parent.parentElement) {
      const style = getComputedStyle(parent), box = parent.getBoundingClientRect();
      if (/auto|scroll|hidden|clip/.test(style.overflowX)) { left = Math.max(left, box.left); right = Math.min(right, box.right); }
      if (/auto|scroll|hidden|clip/.test(style.overflowY)) { top = Math.max(top, box.top); bottom = Math.min(bottom, box.bottom); }
    }
    const hit = right > left && bottom > top ? document.elementFromPoint((left + right) / 2, (top + bottom) / 2) : null;
    if (right - left < r.width - 2 || bottom - top < Math.min(r.height, 24) - 2 || !hit || !(hit === element || element.contains(hit))) {
      summary.unreachable.push(element.getAttribute('aria-label') || element.textContent.trim().slice(0, 50));
    }
  }
  for (const scroll of document.querySelectorAll('main,main *')) if (scroll.scrollHeight > scroll.clientHeight) scroll.scrollTop = 0;
  window.scrollTo(0, 0);
  return summary;
})()`

async function dialogCheck(win) {
  await evaluate(win, `document.querySelector('.manage-music').click()`)
  await until(win, `!!document.querySelector('dialog[open]')`)
  await until(win, `!document.querySelector('.music-manager .manager-content') || !document.body.innerText.includes('Loading playlists')`)
  const result = await evaluate(win, `(() => {
    const dialog = document.querySelector('dialog[open]'), rect = dialog.getBoundingClientRect();
    const result = {
      width: rect.width,
      height: rect.height,
      viewport: [innerWidth, innerHeight],
      bounded: rect.left >= -1 && rect.right <= innerWidth + 1 && rect.top >= -1 && rect.bottom <= innerHeight + 1,
      legacyBranding: /night\\s+ops/i.test(dialog.innerText),
      horizontal: dialog.scrollWidth > dialog.clientWidth + 2,
      unreachable: [],
    };
    for (const control of dialog.querySelectorAll('button,input,select,summary')) {
      if (!control.checkVisibility({ visibilityProperty: true })) continue;
      control.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'instant' });
      const box = control.getBoundingClientRect();
      let left = Math.max(0, box.left), right = Math.min(innerWidth, box.right), top = Math.max(0, box.top), bottom = Math.min(innerHeight, box.bottom);
      for (let parent = control.parentElement; parent; parent = parent.parentElement) {
        const style = getComputedStyle(parent), area = parent.getBoundingClientRect();
        if (/auto|scroll|hidden|clip/.test(style.overflowX)) { left = Math.max(left, area.left); right = Math.min(right, area.right); }
        if (/auto|scroll|hidden|clip/.test(style.overflowY)) { top = Math.max(top, area.top); bottom = Math.min(bottom, area.bottom); }
      }
      const hit = right > left && bottom > top ? document.elementFromPoint((left + right) / 2, (top + bottom) / 2) : null;
      if (right - left < box.width - 2 || bottom - top < Math.min(box.height, 24) - 2 || !hit || !(hit === control || control.contains(hit))) {
        result.unreachable.push(control.getAttribute('aria-label') || control.textContent.trim().slice(0, 50));
      }
    }
    return result;
  })()`)
  await evaluate(win, `document.querySelector('dialog[open]').dispatchEvent(new Event('cancel', { cancelable: true }))`)
  await until(win, `!document.querySelector('dialog[open]')`)
  return result
}

function cellOk(mode, summary, dialog) {
  const reasons = []
  if (summary.legacyBranding) reasons.push('legacy Night Ops branding is visible')
  if (summary.title !== (mode === 'player' ? 'Red Lotus Player' : 'Walkman Bridge')) reasons.push(`title ${summary.title}`)
  if (!summary.retroControls) reasons.push('retro deck controls missing')
  if (summary.horizontal.length) reasons.push(`horizontal overflow ${JSON.stringify(summary.horizontal)}`)
  if (summary.unreachable.length) reasons.push(`unreachable controls ${JSON.stringify(summary.unreachable)}`)
  if (!summary.footerVisible) reasons.push('footer clipped')
  if (!(summary.main && summary.main.height >= 30)) reasons.push('workspace missing')
  if (summary.root[1] > summary.viewport[1] + 2) reasons.push('shell taller than the viewport')
  if (!dialog.bounded) reasons.push('Manage music dialog leaves the viewport')
  if (dialog.legacyBranding) reasons.push('legacy branding in the dialog')
  if (dialog.horizontal) reasons.push('dialog horizontal overflow')
  if (dialog.unreachable.length) reasons.push(`dialog controls unreachable ${JSON.stringify(dialog.unreachable)}`)
  return reasons
}

async function main() {
  await app.whenReady()
  const display = screen.getPrimaryDisplay()
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  const port = server.address().port
  for (const mode of PRODUCTS) {
    product = mode
    const win = new BrowserWindow({
      width: 1140,
      height: 860,
      minWidth: 640,
      minHeight: 560,
      frame: false,
      show: false,
      webPreferences: {
        preload: path.resolve(__dirname, '../../electron/preload.cjs'),
        additionalArguments: [`--nightops-product=${mode}`],
        backgroundThrottling: false,
        contextIsolation: true,
        sandbox: true,
        nodeIntegration: false,
      },
    })
    win.webContents.setAudioMuted(true)
    win.webContents.on('console-message', details => {
      if (details.level === 'error') errors.push(details.message)
    })
    await win.loadURL(`http://127.0.0.1:${port}/`)
    await until(win, `document.querySelector('.nightops-app') && document.querySelectorAll('.queue-row').length === ${items.length}`)
    await evaluate(win, 'document.fonts.ready')
    for (const [width, height] of WINDOW_SIZES) {
      for (const scale of ZOOM_FACTORS) {
        win.setContentSize(width, height)
        win.webContents.setZoomFactor(scale)
        await delay(80)
        await evaluate(win, 'new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
        const applied = await win.webContents.getZoomFactor()
        const summary = await evaluate(win, geometry)
        const dialog = await dialogCheck(win)
        const cssWidth = summary.viewport[0]
        const expectedCssWidth = width / scale
        const reasons = cellOk(mode, summary, dialog)
        if (Math.abs(applied - scale) > 0.02) reasons.push(`zoom factor ${applied} instead of ${scale}`)
        if (Math.abs(cssWidth - expectedCssWidth) > 24) reasons.push(`CSS width ${cssWidth} is outside ${expectedCssWidth}`)
        if (width === 640 && scale === 2 && (cssWidth < 280 || cssWidth > 360)) reasons.push(`320 CSS-pixel case measured ${cssWidth}`)
        const cell = {
          product: mode,
          size: [width, height],
          zoom: scale,
          cssViewport: summary.viewport,
          ok: reasons.length === 0,
        }
        if (reasons.length) cell.reasons = reasons
        results.push(cell)
        const label = `${mode} ${width}x${height} @${Math.round(scale * 100)}%`
        console.log(`${cell.ok ? 'PASS' : 'FAIL'} ${label}${cell.ok ? '' : ` — ${reasons.join('; ')}`}`)
        if (!cell.ok) failures.push(label)
      }
    }
    win.destroy()
  }
  const report = {
    ok: failures.length === 0 && errors.length === 0,
    electron: process.versions.electron,
    chromium: process.versions.chrome,
    displayScaleFactor: display.scaleFactor,
    scaleMethod: 'Electron webContents.setZoomFactor at 100/125/150/200 percent. Host OS and X server DPI are not changed.',
    matrix: { products: PRODUCTS, zoomFactors: ZOOM_FACTORS, windowSizes: WINDOW_SIZES },
    cases: results.length,
    passed: results.filter(cell => cell.ok).length,
    failed: failures.length,
    errors,
    notAutomated: [{
      cell: 'OS display DPI / per-monitor scale (100/125/150/175/200 percent and others)',
      reason: 'The documented matrix is webContents.setZoomFactor, which changes the CSS viewport. It does not change Windows display scaling or the X server DPI. Doing so needs a host display reconfiguration this harness must not perform. screen.getPrimaryDisplay().scaleFactor is recorded and left as the session already set it.',
    }],
    results,
  }
  fs.writeFileSync(path.join(output, 'report.json'), JSON.stringify(report, null, 2))
  console.log(`zoom-matrix ${report.ok ? 'PASS' : 'FAIL'}: ${report.passed} passed, ${report.failed} failed, ${results.length} cases, ${errors.length} renderer/API errors`)
  process.exitCode = report.ok ? 0 : 1
}

main().then(() => {
  clearTimeout(timeout)
  server.close()
  removeProfile()
  app.exit(process.exitCode || 0)
}).catch(error => {
  console.error(error)
  clearTimeout(timeout)
  server.close()
  removeProfile()
  app.exit(1)
})
