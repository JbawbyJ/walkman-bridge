// Shared zoom-matrix budget. The harness, the node:test wrapper, and both
// windows-products jobs (zoom-matrix and zoom-matrix-linux) must stay in this order:
//   harness limit < wrapper timeout (harness + 60s) < job timeout-minutes
'use strict'

const PRODUCTS = ['player', 'bridge']
const ZOOM_FACTORS = [1, 1.25, 1.5, 2]
const WINDOW_SIZES = [[640, 560], [720, 560], [900, 700], [1140, 860], [1380, 860], [1920, 640], [800, 1200]]

// Only this row may be marked not_run, and only when its height exceeds the
// work area. On the 1920x1080 Windows desktop that is every 800x1200 cell.
// Any other skipped cell fails the run.
const EXPECTED_NOT_RUN = [
  { product: 'player', size: [800, 1200], zoom: 1 },
  { product: 'player', size: [800, 1200], zoom: 1.25 },
  { product: 'player', size: [800, 1200], zoom: 1.5 },
  { product: 'player', size: [800, 1200], zoom: 2 },
  { product: 'bridge', size: [800, 1200], zoom: 1 },
  { product: 'bridge', size: [800, 1200], zoom: 1.25 },
  { product: 'bridge', size: [800, 1200], zoom: 1.5 },
  { product: 'bridge', size: [800, 1200], zoom: 2 },
]

const CASES = PRODUCTS.length * WINDOW_SIZES.length * ZOOM_FACTORS.length
// 10s base + 5s per cell, and never under 300s. 56 cells → 290s, so 300s.
const HARNESS_MS = Math.max(300_000, 10_000 + 5_000 * CASES)
const WRAPPER_MARGIN_MS = 60_000
const WRAPPER_MS = HARNESS_MS + WRAPPER_MARGIN_MS
const JOB_TIMEOUT_MINUTES = 20
const JOB_TIMEOUT_MS = JOB_TIMEOUT_MINUTES * 60 * 1000

if (!(HARNESS_MS < WRAPPER_MS && WRAPPER_MS < JOB_TIMEOUT_MS)) {
  throw new Error(`zoom-matrix timeouts out of order: harness ${HARNESS_MS}ms < wrapper ${WRAPPER_MS}ms < job ${JOB_TIMEOUT_MS}ms`)
}

function isExpectedNotRun(cell) {
  if (!cell || !Array.isArray(cell.size) || !cell.workArea) return false
  const listed = EXPECTED_NOT_RUN.some(item =>
    item.product === cell.product &&
    item.size[0] === cell.size[0] &&
    item.size[1] === cell.size[1] &&
    item.zoom === cell.zoom)
  return listed && cell.size[1] > cell.workArea.height
}

module.exports = {
  PRODUCTS,
  ZOOM_FACTORS,
  WINDOW_SIZES,
  EXPECTED_NOT_RUN,
  CASES,
  HARNESS_MS,
  WRAPPER_MARGIN_MS,
  WRAPPER_MS,
  JOB_TIMEOUT_MINUTES,
  JOB_TIMEOUT_MS,
  isExpectedNotRun,
}
