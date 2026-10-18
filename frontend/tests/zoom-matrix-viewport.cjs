'use strict'

const delay = ms => new Promise(resolve => setTimeout(resolve, ms))

async function waitForViewport(win, width, height, scale, {
  timeoutMs = 3000,
  pollMs = 25,
  now = () => performance.now(),
  sleep = delay,
} = {}) {
  const started = now()
  const expectedViewport = [width / scale, height / scale]
  let appliedZoom = null
  let viewport = null
  let stopped = false
  let timer
  const snapshot = converged => ({
    converged, appliedZoom, viewport, expectedViewport,
    elapsedMs: now() - started,
    timeoutMs,
  })
  // A stalled renderer must not turn the per-cell deadline into an unbounded
  // executeJavaScript wait. The matrix's existing overall timeout also remains.
  const deadline = new Promise(resolve => {
    timer = setTimeout(() => {
      stopped = true
      resolve({ ...snapshot(false), elapsedMs: Math.max(now() - started, timeoutMs) })
    }, timeoutMs)
  })
  const poll = async () => {
    while (!stopped) {
      // Sample directly. An animation-frame barrier can keep this poll stuck
      // on an old measurement even after resize/zoom has changed the viewport.
      appliedZoom = win.webContents.getZoomFactor()
      const measured = await win.webContents.executeJavaScript('[innerWidth, innerHeight]', true)
      if (stopped) break
      viewport = measured
      if (Math.abs(appliedZoom - scale) <= 0.02 && Array.isArray(viewport) &&
          Math.abs(viewport[0] - expectedViewport[0]) <= 1 &&
          Math.abs(viewport[1] - expectedViewport[1]) <= 1) return snapshot(true)
      const remaining = timeoutMs - (now() - started)
      if (remaining <= 0) break
      await sleep(Math.min(pollMs, remaining))
    }
    return snapshot(false)
  }
  try {
    return await Promise.race([poll(), deadline])
  } finally {
    stopped = true
    clearTimeout(timer)
  }
}

module.exports = { waitForViewport }
