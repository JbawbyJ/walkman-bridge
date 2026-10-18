'use strict'

const assert = require('node:assert/strict')
const test = require('node:test')
const { waitForViewport } = require('./zoom-matrix-viewport.cjs')

function renderer(viewportAt, zoomAt = () => 1) {
  let elapsed = 0
  const observed = []
  return {
    win: { webContents: {
      getZoomFactor: () => zoomAt(elapsed),
      executeJavaScript: async script => {
        if (script !== '[innerWidth, innerHeight]') return
        const viewport = viewportAt(elapsed)
        observed.push(viewport)
        return viewport
      },
    } },
    options: { now: () => elapsed, sleep: async ms => { elapsed += ms } },
    observed,
  }
}

test('waits for delayed CSS viewport even when getZoomFactor already reports the target', async () => {
  const fixture = renderer(ms => ms < 200 ? [320, 280] : [640, 560])
  const result = await waitForViewport(fixture.win, 640, 560, 1, fixture.options)
  assert.equal(result.converged, true)
  assert.deepEqual(result.viewport, [640, 560])
  assert.ok(result.elapsedMs >= 200, 'must wait beyond the former fixed 80ms delay')
  assert.deepEqual(fixture.observed[0], [320, 280])
})

for (const availableFrames of [0, 1]) {
  test(`hidden renderer can converge when animation frames stall after ${availableFrames} callbacks`, async () => {
    const fixture = renderer(ms => ms < 50 ? [450, 350] : [900, 700])
    const directEvaluation = fixture.win.webContents.executeJavaScript
    let frames = 0
    fixture.win.webContents.executeJavaScript = async script => {
      // A hidden window may continue evaluating JavaScript and updating its
      // viewport while animation-frame promises remain pending indefinitely.
      if (script.includes('requestAnimationFrame')) {
        if (++frames > availableFrames) return new Promise(() => {})
        return
      }
      return directEvaluation(script)
    }
    const result = await waitForViewport(fixture.win, 900, 700, 1,
      { ...fixture.options, timeoutMs: 125 })
    assert.equal(result.converged, true)
    assert.deepEqual(result.viewport, [900, 700])
    assert.ok(result.elapsedMs >= 50 && result.elapsedMs < 125)
    assert.deepEqual(fixture.observed[0], [450, 350])
  })
}

test('a viewport that never converges times out with the last measured dimensions', async () => {
  const fixture = renderer(() => [320, 280])
  const result = await waitForViewport(fixture.win, 640, 560, 1, { ...fixture.options, timeoutMs: 125 })
  assert.equal(result.converged, false)
  assert.equal(result.elapsedMs, 125)
  assert.deepEqual(result.viewport, [320, 280])
  assert.deepEqual(result.expectedViewport, [640, 560])
  assert.equal(result.appliedZoom, 1)
})

test('viewport dimensions alone do not pass while the zoom factor is still stale', async () => {
  const fixture = renderer(() => [640, 560], ms => ms < 150 ? 2 : 1)
  const result = await waitForViewport(fixture.win, 640, 560, 1, fixture.options)
  assert.equal(result.converged, true)
  assert.ok(result.elapsedMs >= 150)
  assert.equal(result.appliedZoom, 1)
})

test('fractional zoom permits Chromium CSS-pixel rounding', async () => {
  const fixture = renderer(() => [853, 573], () => 1.5)
  const result = await waitForViewport(fixture.win, 1280, 860, 1.5, fixture.options)
  assert.equal(result.converged, true)
  assert.equal(result.elapsedMs, 0)
})

test('renderer evaluation that never resolves still respects the cell deadline', { timeout: 1000 }, async () => {
  const fixture = renderer(() => new Promise(() => {}))
  const result = await waitForViewport(fixture.win, 640, 560, 1, { timeoutMs: 25 })
  assert.equal(result.converged, false)
  assert.equal(result.viewport, null)
  assert.deepEqual(result.expectedViewport, [640, 560])
  assert.ok(result.elapsedMs >= 25)
})
